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
    - Level 3: business:{uid}:{cid}:bank → { to_reconcile, in_process, pending }

NOTE Banking Specificities:
- Bank transactions come from ERP (Odoo) via firebase_cache_handlers
- Only 3 statuses: to_reconcile, in_process, pending
- Accounts are extracted from transactions (no separate accounts endpoint)
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

    Returns:
        Dict with: mandate_path, client_uuid, bank_erp, etc.
    """
    redis_client = get_redis()

    # Level 2 context key (full company_data)
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

    logger.warning(f"[BANKING] No company context found for uid={uid}, company={company_id}")
    return {}


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

    logger.info(f"[BANKING] Orchestration started for company={company_id}, account={account_id}")

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
        # STEP 1: Bank Accounts
        # Note: Accounts are extracted from transactions, no separate endpoint
        # ════════════════════════════════════════════════════════════
        accounts = []
        total_balance = 0.0

        # ════════════════════════════════════════════════════════════
        # STEP 2: Fetch Bank Transactions (using cache handler)
        # Uses the same logic and cache as dashboard orchestration
        # ════════════════════════════════════════════════════════════
        to_process = []
        in_process = []
        pending = []
        batches = []

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
                mandate_path=mandate_path
            )

            if result.get("data"):
                data = result["data"]
                to_process = data.get("to_reconcile", [])
                in_process = data.get("in_process", [])
                pending = data.get("pending", [])
                
                logger.info(
                    f"[BANKING] Transactions loaded: "
                    f"to_reconcile={len(to_process)}, "
                    f"in_process={len(in_process)}, "
                    f"pending={len(pending)} "
                    f"source={result.get('source', 'unknown')}"
                )
            else:
                logger.warning(f"[BANKING] No transaction data returned")

        except Exception as tx_error:
            logger.error(f"[BANKING] Failed to fetch transactions: {tx_error}")

        # ════════════════════════════════════════════════════════════
        # STEP 3: Active Batches
        # Note: Batches are tracked in notifications, extracted during
        # _organize_bank_transactions_by_status in cache handler
        # ════════════════════════════════════════════════════════════
        batches = []  # Populated from in_process transactions if needed

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
            },
            "batches": batches,
            "counts": {
                "to_process": len(to_process),
                "in_process": len(in_process),
                "pending": len(pending),
            },
            "pagination": {
                "page": 1,
                "pageSize": 20,
                "totalPages": 1,
                "totalItems": (
                    len(to_process) +
                    len(in_process) +
                    len(pending)
                ),
            },
            "company": {
                "id": company_id,
                "mandate_path": mandate_path,
            },
            "meta": {
                "loaded_at": datetime.utcnow().isoformat() + "Z",
                "version": "1.0",
                "source": "firebase"
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
            f"accounts={len(accounts)}, transactions={banking_data['pagination']['totalItems']}"
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
        await cache.delete_cached_data(uid, company_id, "bank", "transactions")
        logger.info(f"[BANKING] Bank cache invalidated")
    except Exception as e:
        logger.warning(f"[BANKING] Failed to invalidate bank cache: {e}")

    # Re-run orchestration (will fetch fresh from ERP + apply business logic)
    await handle_banking_orchestrate_init(uid, session_id, {
        "company_id": company_id,
        "account_id": account_id
    })


async def handle_banking_process(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle banking.process WebSocket event.

    Triggers processing of selected transactions.

    Supports optimistic updates:
    - Frontend sends _optimistic_update_id with the request
    - On success: confirms the optimistic update via metrics.update_confirmed
    - On failure: rejects the update via metrics.update_failed (triggers rollback)
    """
    transaction_ids = payload.get("transaction_ids", [])
    company_id = payload.get("company_id")
    optimistic_update_id = payload.get("_optimistic_update_id")
    general_instructions = payload.get("general_instructions")

    logger.info(
        f"[BANKING] Process requested for {len(transaction_ids)} transactions"
        f"{f' (optimistic_update_id={optimistic_update_id})' if optimistic_update_id else ''}"
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
        # Get context
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path", "")

        # Process via handlers
        from .handlers import get_banking_handlers
        handlers = get_banking_handlers()
        result = await handlers.process_transactions(
            uid,
            company_id,
            mandate_path,
            transaction_ids,
            general_instructions=general_instructions,
            _optimistic_update_id=optimistic_update_id
        )

        # Send result
        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.PROCESSED,
            "payload": result
        })

        # Confirm optimistic update if present
        if optimistic_update_id and result.get("success"):
            await _confirm_optimistic_update(uid, optimistic_update_id, "banking", company_id, mandate_path)

    except Exception as e:
        logger.error(f"[BANKING] Process failed: {e}", exc_info=True)

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

        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.STOPPED,
            "payload": result
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

        await hub.broadcast(uid, {
            "type": WS_EVENTS.BANKING.DELETED,
            "payload": result
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
