"""
Banking Page Orchestration
==========================

Handles post-authentication data loading for the Banking page.

Pattern:
    page.restore_state -> cache hit: instant data
    page.restore_state -> cache miss: banking.orchestrate_init -> full load

Flow (3-Level Architecture):
    1. Frontend sends page.restore_state with page="banking"
    2. If cache hit -> page.state_restored with data
    3. If cache miss -> page.state_not_found
    4. Frontend sends banking.orchestrate_init with company_id only
    5. Backend reads company context from Level 2 cache: company:{uid}:{cid}:context
    6. Backend checks business cache (Level 3): business:{uid}:{cid}:bank
    7. If cache miss -> fetch from ERP via cache handler
    8. Backend saves to page state cache
    9. Backend sends banking.full_data

ARCHITECTURE (3-Level Cache):
    - Level 2: company:{uid}:{cid}:context → Full company_data (mandate_path, client_uuid, bank_erp, etc.)
    - Level 3: business:{uid}:{cid}:bank → { to_process, in_process, pending, processed }

NOTE Banking Specificities:
- Bank transactions come from ERP (Odoo) + Task Manager (Firebase) via firebase_cache_handlers
- 4 lists: to_process (ERP unreconciled), in_process (on_process flat list with batch_id), pending, processed (completed)
- Accounts are extracted from ERP transactions (journal_id / journal_name)
- Batches are computed on-the-fly by grouping in_process items by batch_id (not stored in cache)
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.redis_client import get_redis

logger = logging.getLogger("banking.orchestration")

# Cache TTL for banking data
TTL_BANKING_DATA = 1800  # 30 minutes


def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """
    Retrieve company context from Level 2 cache.

    Cache hierarchy (tries in order):
    1. Level 2: company:{uid}:{company_id}:context (full company_data)
    2. Fallback Firebase: fetch_all_mandates_light → repopulate Level 2

    Returns:
        Dict with: mandate_path, client_uuid, bank_erp, etc.
    """
    redis_client = get_redis()

    # 1. Level 2 context key (full company_data)
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            logger.info(
                f"[BANKING] Context from Level 2: {level2_key} "
                f"mandate_path={data.get('mandate_path', '')[:30]}... "
                f"bank_erp={data.get('bank_erp', '')}"
            )
            return data
    except Exception as e:
        logger.warning(f"[BANKING] Level 2 context read error: {e}")

    # 2. Fallback Firebase: Level 2 expiré ou absent → récupérer depuis Firebase
    logger.warning(f"[BANKING] Level 2 cache MISS for uid={uid}, company={company_id} — fetching from Firebase...")
    try:
        from app.firebase_providers import get_firebase_management
        from app.wrappers.dashboard_orchestration_handlers import set_selected_company

        firebase = get_firebase_management()
        mandates = firebase.fetch_all_mandates_light(uid)
        for m in (mandates or []):
            m_ids = (m.get("contact_space_id"), m.get("id"), m.get("contact_space_name"))
            if company_id in m_ids:
                m["company_id"] = company_id
                set_selected_company(uid, company_id, m)
                logger.info(
                    f"[BANKING] Context repopulated from Firebase: "
                    f"mandate_path={m.get('mandate_path', '')[:30]}..."
                )
                return m
    except Exception as e:
        logger.error(f"[BANKING] Firebase fallback failed: {e}")

    logger.error(f"[BANKING] No company context found for uid={uid}, company={company_id}")
    return {}


def _get_transactions_from_cache(
    uid: str, company_id: str, transaction_ids: List[str]
) -> List[Dict[str, Any]]:
    """
    Extract selected transactions from the banking business cache.

    Searches the to_process list in business:{uid}:{cid}:bank for the
    given transaction_ids and returns matching items.

    Args:
        uid: Firebase user ID
        company_id: Company ID
        transaction_ids: List of transaction IDs to extract

    Returns:
        List of transaction dicts found in cache
    """
    try:
        redis_client = get_redis()
        cache_key = f"business:{uid}:{company_id}:bank"
        cached = redis_client.get(cache_key)

        if not cached:
            logger.warning(f"[BANKING] No business cache for transaction lookup: {cache_key}")
            return []

        data = json.loads(cached if isinstance(cached, str) else cached.decode())
        documents = data.get("data", data)

        # Search in to_process (primary source for process action)
        ids_set = set(transaction_ids)
        results = []

        for list_name in ["to_process", "in_process", "pending"]:
            for item in documents.get(list_name, []):
                item_id = str(item.get("id", item.get("job_id", "")))
                if item_id in ids_set:
                    results.append(item)
                    ids_set.discard(item_id)
            if not ids_set:
                break

        logger.info(
            f"[BANKING] Retrieved {len(results)}/{len(transaction_ids)} "
            f"transactions from cache"
        )
        return results

    except Exception as e:
        logger.error(f"[BANKING] Error getting transactions from cache: {e}")
        return []


async def handle_banking_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle banking.orchestrate_init WebSocket event.

    This is the main orchestration entry point when the Banking page loads
    and no cached state is available.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            "company_id": str (required - used to lookup context from SessionStateManager)
            "account_id": str (optional - filter by account on load)
        }
    """
    company_id = payload.get("company_id")
    account_id = payload.get("account_id")  # Optional initial filter
    force_refresh = payload.get("force_refresh", False)

    logger.info(f"[BANKING] Orchestration started for company={company_id}, account={account_id}, force_refresh={force_refresh}")

    # Validate company_id
    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID"
            }
        })
        return

    # Get company context from SessionStateManager
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    # Validate we have the required context
    if not mandate_path:
        logger.error(f"[BANKING] No mandate_path in session context - dashboard orchestration may not have run")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        # ════════════════════════════════════════════════════════════
        # STEP 1: Fetch Bank Transactions (using cache handler)
        # Sources: ERP (Odoo) + Task Manager (Firebase)
        # ════════════════════════════════════════════════════════════
        to_process = []
        in_process = []
        pending = []
        processed = []

        try:
            logger.info(f"[BANKING] Fetching transactions via cache handler...")

            # Get client_uuid and bank_erp from context
            client_uuid = context.get("client_uuid", "")
            bank_erp = context.get("bank_erp", "")

            if not client_uuid or not bank_erp:
                logger.warning(f"[BANKING] Missing ERP config - client_uuid={bool(client_uuid)}, bank_erp={bank_erp}")

            # Use the same handler as dashboard orchestration
            from app.firebase_cache_handlers import get_firebase_cache_handlers
            cache_handlers = get_firebase_cache_handlers()

            result = await cache_handlers.get_bank_transactions(
                user_id=uid,
                company_id=company_id,
                client_uuid=client_uuid,
                bank_erp=bank_erp,
                mandate_path=mandate_path,
                force_refresh=force_refresh,
            )

            if result.get("data"):
                data = result["data"]
                to_process = data.get("to_process", [])
                in_process = data.get("in_process", [])
                pending = data.get("pending", [])
                processed = data.get("processed", [])

                logger.info(
                    f"[BANKING] Transactions loaded: "
                    f"to_process={len(to_process)}, "
                    f"in_process={len(in_process)}, "
                    f"pending={len(pending)}, "
                    f"processed={len(processed)}, "
                    f"source={result.get('source', 'unknown')}"
                )
            else:
                logger.warning(f"[BANKING] No transaction data returned")

        except Exception as tx_error:
            logger.error(f"[BANKING] Failed to fetch transactions: {tx_error}")

        # ════════════════════════════════════════════════════════════
        # STEP 2: Extract Bank Accounts from ALL normalized transactions
        # All lists now have account_id (str) and account_name (str)
        # Balance accumulated only from to_process (unreconciled)
        # ════════════════════════════════════════════════════════════
        accounts_map = {}  # account_id -> account info

        # Collect accounts from all lists
        for tx in to_process + in_process + pending + processed:
            a_id = tx.get("account_id")
            if a_id and a_id not in accounts_map:
                accounts_map[a_id] = {
                    "id": a_id,
                    "name": tx.get("account_name") or f"Account {a_id}",
                    "iban": tx.get("account_number", ""),
                    "currency": tx.get("currency", "CHF"),
                    "balance": 0.0,
                }

        # Accumulate balance only from to_process (unreconciled amount)
        for tx in to_process:
            a_id = tx.get("account_id")
            if a_id and a_id in accounts_map:
                accounts_map[a_id]["balance"] += float(tx.get("amount", 0) or 0)

        accounts = list(accounts_map.values())
        total_balance = sum(a["balance"] for a in accounts)

        # ════════════════════════════════════════════════════════════
        # STEP 3: Compute Batches on-the-fly from in_process flat list
        # Group by batch_id field present on each item.
        # Batches disappear naturally when all their items leave in_process.
        # ════════════════════════════════════════════════════════════
        batches_map = {}
        for tx in in_process:
            bid = tx.get("batch_id", "")
            if bid:
                batches_map.setdefault(bid, []).append(tx)

        batches = []
        for batch_id, items in batches_map.items():
            first_item = items[0] if items else {}
            batches.append({
                "batch_id": batch_id,
                "account_id": first_item.get("account_id", ""),
                "transaction_count": len(items),
                "status": "running",
                "progress": None,
            })

        # ════════════════════════════════════════════════════════════
        # STEP 3B: Fetch instruction templates
        # ════════════════════════════════════════════════════════════
        instruction_templates = []
        try:
            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()
            instruction_templates = firebase.fetch_instruction_templates(mandate_path, "banking")
            logger.info(f"[BANKING] Loaded {len(instruction_templates)} instruction templates")
        except Exception as tpl_err:
            logger.warning(f"[BANKING] Failed to load instruction templates: {tpl_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 4: Build combined banking data structure
        # ════════════════════════════════════════════════════════════
        banking_data = {
            "accounts": {
                "list": accounts,
                "total_balance": total_balance,
                "selected_account_id": account_id,
            },
            "transactions": {
                "to_process": to_process,
                "in_process": in_process,
                "pending": pending,
                "matched": processed,
            },
            "batches": batches,
            "counts": {
                "to_process": len(to_process),
                "in_process": len(in_process),
                "pending": len(pending),
                "matched": len(processed),
            },
            "pagination": {
                "page": 1,
                "pageSize": 20,
                "totalPages": 1,
                "totalItems": (
                    len(to_process) +
                    len(in_process) +
                    len(pending) +
                    len(processed)
                ),
            },
            "company": {
                "id": company_id,
                "mandate_path": mandate_path,
            },
            "instruction_templates": instruction_templates,
            "meta": {
                "loaded_at": datetime.utcnow().isoformat() + "Z",
                "version": "1.0",
                "source": "erp+firebase"
            }
        }

        # ════════════════════════════════════════════════════════════
        # STEP 6: Save page state for fast recovery
        # ════════════════════════════════════════════════════════════
        try:
            from app.wrappers.page_state_manager import get_page_state_manager
            page_manager = get_page_state_manager()
            page_manager.save_page_state(
                uid=uid,
                company_id=company_id,
                page="banking",
                mandate_path=mandate_path,
                data=banking_data
            )
            logger.info(f"[BANKING] Page state saved for fast recovery")
        except Exception as cache_err:
            logger.warning(f"[BANKING] Failed to save page state: {cache_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 7: Send response via WebSocket
        # ════════════════════════════════════════════════════════════
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.FULL_DATA,
            "payload": {
                "success": True,
                "data": banking_data,
                "company_id": company_id,
            }
        })

        logger.info(
            f"[BANKING] Orchestration completed for company={company_id}: "
            f"accounts={len(accounts)}, batches={len(batches)}, "
            f"transactions={banking_data['pagination']['totalItems']}"
        )

    except Exception as e:
        logger.error(f"[BANKING] Orchestration failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": str(e),
                "code": "ORCHESTRATION_ERROR"
            }
        })


def _format_transaction(tx: Dict[str, Any]) -> Dict[str, Any]:
    """Format a raw transaction from Firebase."""
    tx_data = tx.get('data', tx) if isinstance(tx, dict) else {}
    return {
        "id": tx.get('firebase_doc_id', tx_data.get('id', '')),
        "transaction_id": tx_data.get('transaction_id', ''),
        "account_id": tx_data.get('account_id', ''),
        "account_name": tx_data.get('account_name', ''),
        "date": tx_data.get('date', ''),
        "description": tx_data.get('description', ''),
        "reference": tx_data.get('reference', ''),
        "partner_name": tx_data.get('partner_name', ''),
        "transaction_type": tx_data.get('transaction_type', 'debit'),
        "payment_ref": tx_data.get('payment_ref', ''),
        "amount": float(tx_data.get('amount', 0) or 0),
        "currency": tx_data.get('currency', 'CHF'),
        "status": tx_data.get('status', 'to_process'),
        "job_id": tx_data.get('job_id', ''),
        "created_at": str(tx_data.get('created_at', '')),
        "updated_at": str(tx_data.get('updated_at', '')),
    }


async def handle_banking_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle banking.refresh WebSocket event.

    Refreshes transaction data by invalidating cache and re-fetching.
    
    Pattern: Invalidate cache → Re-fetch from source → Apply business logic → Cache result
    (Same pattern as routing.refresh)
    """
    company_id = payload.get("company_id")
    account_id = payload.get("account_id")

    logger.info(f"[BANKING] Refresh requested for company={company_id}, account={account_id}")

    # Invalidate bank cache first to force re-fetch from source
    try:
        from app.cache.unified_cache_manager import get_firebase_cache_manager
        cache = get_firebase_cache_manager()
        await cache.invalidate_business_domain(uid, company_id, "bank")
        logger.info(f"[BANKING] Bank cache invalidated")
    except Exception as e:
        logger.warning(f"[BANKING] Failed to invalidate bank cache: {e}")

    # Re-run orchestration with force_refresh to bypass cache entirely
    await handle_banking_orchestrate_init(uid, session_id, {
        "company_id": company_id,
        "account_id": account_id,
        "force_refresh": True,
    })


async def handle_banking_process(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle banking.process WebSocket event.

    Triggers processing of selected transactions via centralized job_actions_handler.
    Same pattern as handle_routing_process() — delegates to handle_job_process().

    Supports optimistic updates:
    - Frontend sends _optimistic_update_id with the request
    - On success: confirms the optimistic update via metrics.update_confirmed
    - On failure: rejects the update via metrics.update_failed (triggers rollback)
    """
    transaction_ids = payload.get("transaction_ids", [])
    company_id = payload.get("company_id")
    optimistic_update_id = payload.get("_optimistic_update_id")

    logger.info(f"[BANKING] ──────────────────────────────────────────────────────")
    logger.info(f"[BANKING] handle_banking_process - uid={uid} session={session_id}")
    logger.info(
        f"[BANKING] → transaction_ids count={len(transaction_ids)} company_id={company_id}"
        f"{f' optimistic_update_id={optimistic_update_id}' if optimistic_update_id else ''}"
    )

    if not transaction_ids:
        if optimistic_update_id:
            await _reject_optimistic_update(uid, optimistic_update_id, "banking", "No transactions selected")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": "No transactions selected",
                "code": "NO_TRANSACTIONS"
            }
        })
        return

    try:
        # Get company context from Level 2 cache
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path", "")

        # Build transactions_data from cache for _build_banker_jobs_data
        transactions_data = _get_transactions_from_cache(uid, company_id, transaction_ids)
        bank_account = ""
        bank_account_id = ""
        if transactions_data:
            first_tx = transactions_data[0]
            bank_account = first_tx.get("account_name", "")
            bank_account_id = first_tx.get("account_id", "")

        # Use centralized job actions handler (same pattern as routing)
        from app.wrappers.job_actions_handler import handle_job_process

        result = await handle_job_process(
            uid=uid,
            job_type="bankbookeeper",
            payload={
                "document_ids": transaction_ids,
                "transactions_data": transactions_data,
                "bank_account": bank_account,
                "bank_account_id": bank_account_id,
                "general_instructions": payload.get("general_instructions", ""),
            },
            company_data={
                "company_id": company_id,
                "mandate_path": mandate_path,
                "company_name": context.get("name", context.get("legal_name", company_id)),
                "client_uuid": context.get("client_uuid", ""),
                "dms_type": context.get("dms_type", "odoo"),
                "communication_mode": context.get("chat_type", "rag"),
                "log_communication_mode": context.get("communication_log_type", "rag"),
                "workflow_params": context.get("workflow_params", {}),
            },
        )

        if result.get("success"):
            job_id = result.get("job_id")
            logger.info(f"[BANKING] → Process SUCCESS - job_id={job_id}")

            # Notify processing started
            await hub.broadcast(uid, {
                "type": WS_EVENTS.BANKING.PROCESSING_STARTED,
                "payload": {
                    "transaction_ids": transaction_ids,
                    "count": len(transaction_ids),
                    "status": "started",
                    "job_id": job_id,
                    "_optimistic_update_id": optimistic_update_id,
                }
            })

            # Acknowledge the request was submitted
            await hub.broadcast(uid, {
                "type": WS_EVENTS.BANKING.PROCESSED,
                "payload": {
                    "success": True,
                    "processed": transaction_ids,
                    "failed": [],
                    "job_id": job_id,
                    "batch_id": result.get("batch_id"),
                    "summary": {
                        "totalProcessed": len(transaction_ids),
                        "totalFailed": 0,
                    },
                    "_optimistic_update_id": optimistic_update_id,
                }
            })

            # Confirm optimistic update if present
            if optimistic_update_id:
                logger.info(f"[BANKING] → Confirming optimistic update: {optimistic_update_id}")
                await _confirm_optimistic_update(uid, optimistic_update_id, "banking", company_id, mandate_path)

            logger.info(f"[BANKING] ──────────────────────────────────────────────────────")
        else:
            logger.warning(f"[BANKING] → Process FAILED - {result.get('error')}")

            if optimistic_update_id:
                await _reject_optimistic_update(uid, optimistic_update_id, "banking", result.get("error", "Process failed"))

            await hub.broadcast(uid, {
                "type": WS_EVENTS.BANKING.ERROR,
                "payload": {
                    "error": result.get("error", "Process failed"),
                    "code": result.get("code", "PROCESS_ERROR"),
                }
            })

    except Exception as e:
        logger.error(f"[BANKING] Process EXCEPTION: {e}", exc_info=True)

        if optimistic_update_id:
            await _reject_optimistic_update(uid, optimistic_update_id, "banking", str(e))

        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": str(e),
                "code": "PROCESS_ERROR"
            }
        })


async def handle_banking_stop(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle banking.stop WebSocket event.

    Stops processing of transactions or batches.
    """
    transaction_ids = payload.get("transaction_ids", [])
    batch_ids = payload.get("batch_ids", [])
    company_id = payload.get("company_id")

    logger.info(f"[BANKING] Stop requested: transactions={len(transaction_ids)}, batches={len(batch_ids)}")

    try:
        from .handlers import get_banking_handlers
        handlers = get_banking_handlers()
        result = await handlers.stop_processing(
            uid,
            company_id,
            transaction_ids=transaction_ids,
            batch_ids=batch_ids
        )

        stopped_payload = dict(result) if isinstance(result, dict) else {"success": True}
        stopped_payload["_optimistic_update_id"] = payload.get("_optimistic_update_id")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.STOPPED,
            "payload": stopped_payload
        })

    except Exception as e:
        logger.error(f"[BANKING] Stop failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": str(e),
                "code": "STOP_ERROR"
            }
        })


async def handle_banking_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle banking.delete WebSocket event.

    Deletes banking transactions.
    """
    transaction_ids = payload.get("transaction_ids", [])
    company_id = payload.get("company_id")

    logger.info(f"[BANKING] Delete requested for {len(transaction_ids)} transactions")

    try:
        from .handlers import get_banking_handlers
        handlers = get_banking_handlers()
        result = await handlers.delete_transactions(
            uid,
            company_id,
            transaction_ids
        )

        deleted_payload = dict(result) if isinstance(result, dict) else {"success": True}
        deleted_payload["_optimistic_update_id"] = payload.get("_optimistic_update_id")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.DELETED,
            "payload": deleted_payload
        })

    except Exception as e:
        logger.error(f"[BANKING] Delete failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.ERROR,
            "payload": {
                "error": str(e),
                "code": "DELETE_ERROR"
            }
        })


# ============================================
# OPTIMISTIC UPDATE HELPERS
# ============================================

async def _confirm_optimistic_update(
    uid: str,
    update_id: str,
    module: str,
    company_id: str,
    mandate_path: str,
) -> None:
    """
    Confirm an optimistic update after successful backend operation.
    """
    try:
        from ..metrics.orchestration import confirm_optimistic_update
        from ..metrics.handlers import get_metrics_handlers

        # Get fresh metrics after the operation
        handlers = get_metrics_handlers()
        result = await handlers.module_data(
            user_id=uid,
            company_id=company_id,
            module=module,
            mandate_path=mandate_path,
            force_refresh=True,
        )

        new_counts = result.get("data", {}) if result.get("success") else {}

        await confirm_optimistic_update(uid, update_id, module, new_counts)
        logger.info(f"[BANKING] Optimistic update confirmed: {update_id}")

    except Exception as e:
        logger.error(f"[BANKING] Failed to confirm optimistic update: {e}")


async def _reject_optimistic_update(
    uid: str,
    update_id: str,
    module: str,
    error: str,
) -> None:
    """
    Reject an optimistic update after failed backend operation.
    """
    try:
        from ..metrics.orchestration import reject_optimistic_update

        await reject_optimistic_update(uid, update_id, module, error)
        logger.info(f"[BANKING] Optimistic update rejected: {update_id}")

    except Exception as e:
        logger.error(f"[BANKING] Failed to reject optimistic update: {e}")
