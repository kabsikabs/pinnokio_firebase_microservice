"""
Expenses Page Orchestration
===========================

Handles initial page load via WebSocket.

Pattern:
    page.restore_state -> cache hit: instant data
    page.restore_state -> cache miss: expenses.orchestrate_init -> full load

Flow (3-Level Architecture):
    1. Frontend sends page.restore_state with page="expenses"
    2. If cache hit -> page.state_restored with data
    3. If cache miss -> page.state_not_found
    4. Frontend sends expenses.orchestrate_init with company_id only
    5. Backend reads company context from Level 2 cache: company:{uid}:{cid}:context
    6. Backend checks business cache (Level 3): business:{uid}:{cid}:expenses
    7. If cache miss -> fetch from Firebase using mandate_path from Level 2
    8. Backend saves to page state cache
    9. Backend sends expenses.full_data

ARCHITECTURE (3-Level Cache):
    - Level 2: company:{uid}:{cid}:context -> Full company_data (mandate_path, client_uuid, etc.)
    - Level 3: business:{uid}:{cid}:expenses -> { to_process, in_process, pending, processed, metrics }

Integration with Dashboard:
    All actions that modify expenses (close, reopen, update, delete, refresh)
    broadcast dashboard.expenses_update with updated metrics for widget sync.
    This uses the centralized ListManager pattern to update the business cache
    optimistically without refetching from Firebase.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.redis_client import get_redis
from .handlers import get_expenses_handlers

logger = logging.getLogger("expenses.orchestration")


def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """
    Retrieve company context from Level 2 cache.

    Cache hierarchy (tries in order):
    1. Level 2: company:{uid}:{company_id}:context (full company_data)
    2. Fallback Firebase: fetch_all_mandates_light → repopulate Level 2

    Returns:
        Dict with: mandate_path, client_uuid, etc.
    """
    redis_client = get_redis()

    # 1. Try Level 2 context key (full company_data)
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            logger.info(
                f"[EXPENSES] Context from Level 2: {level2_key} "
                f"mandate_path={data.get('mandate_path', '')[:30]}..."
            )
            return data
    except Exception as e:
        logger.warning(f"[EXPENSES] Level 2 context read error: {e}")

    # 2. Fallback Firebase: Level 2 expiré ou absent → récupérer depuis Firebase
    logger.warning(f"[EXPENSES] Level 2 cache MISS for uid={uid}, company={company_id} — fetching from Firebase...")
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
                    f"[EXPENSES] Context repopulated from Firebase: "
                    f"mandate_path={m.get('mandate_path', '')[:30]}..."
                )
                return m
    except Exception as e:
        logger.error(f"[EXPENSES] Firebase fallback failed: {e}")

    logger.error(f"[EXPENSES] No company context found for uid={uid}, company={company_id}")
    return {}


async def handle_expenses_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle expenses.orchestrate_init WebSocket event.

    This is the main orchestration entry point when the Expenses page loads
    and no cached state is available.

    ARCHITECTURE (3-Level Cache):
    - Company context (mandate_path, client_uuid) is retrieved
      from Level 2 cache: company:{uid}:{company_id}:context
    - This cache was populated during dashboard orchestration (_run_company_phase)
    - Frontend only needs to send company_id - backend retrieves the rest from cache

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            "company_id": str (required - used to lookup context from Level 2 cache)
            "mandate_path": str (optional - fallback when Level 2 cache not available)
        }
    """
    company_id = payload.get("company_id")
    # Fallback mandate_path from frontend (used when Level 2 cache is not populated)
    payload_mandate_path = payload.get("mandate_path")

    logger.info(f"[EXPENSES] Orchestration started for company={company_id}")

    # Validate company_id
    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID"
            }
        })
        return

    # ════════════════════════════════════════════════════════════
    # Get company context from Level 2 cache
    # Populated during dashboard orchestration (_run_company_phase)
    # Contains: mandate_path, client_uuid, etc.
    # ════════════════════════════════════════════════════════════
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")
    client_uuid = context.get("client_uuid", "")

    # Fallback: Use mandate_path from payload if Level 2 cache is not available
    # This happens when user navigates directly to expenses page or cache expired
    if not mandate_path and payload_mandate_path:
        logger.info(f"[EXPENSES] Using mandate_path from payload (Level 2 cache miss)")
        mandate_path = payload_mandate_path

    # Validate we have the required context
    if not mandate_path:
        logger.error(f"[EXPENSES] No mandate_path in session context - dashboard orchestration may not have run")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        # ════════════════════════════════════════════════════════════
        # STEP 1: Fetch expenses data
        # Uses handlers.list_expenses which:
        # - Checks Redis cache first
        # - On miss: fetches from Firebase
        # - Returns: to_process, in_process, pending, processed (correctly sorted)
        # ════════════════════════════════════════════════════════════
        handlers = get_expenses_handlers()
        result = await handlers.list_expenses(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path
        )

        if not result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to fetch expenses"),
                    "code": "FETCH_ERROR"
                }
            })
            return

        expenses_data = result.get("data", {})

        logger.info(
            f"[EXPENSES] Data loaded: "
            f"to_process={len(expenses_data.get('to_process', []))}, "
            f"in_process={len(expenses_data.get('in_process', []))}, "
            f"pending={len(expenses_data.get('pending', []))}, "
            f"processed={len(expenses_data.get('processed', []))} "
            f"from_cache={result.get('from_cache', False)}"
        )

        # ════════════════════════════════════════════════════════════
        # STEP 2: Build complete expenses data structure
        # ════════════════════════════════════════════════════════════
        full_data = {
            **expenses_data,
            "company": {
                "id": company_id,
                "mandate_path": mandate_path,
                "client_uuid": client_uuid,
            },
            "meta": {
                "loaded_at": datetime.utcnow().isoformat() + "Z",
                "version": "1.0",
                "source": "cache" if result.get("from_cache") else "firebase"
            }
        }

        # ════════════════════════════════════════════════════════════
        # STEP 3: Save page state for fast recovery
        # ════════════════════════════════════════════════════════════
        try:
            from app.wrappers.page_state_manager import get_page_state_manager
            page_manager = get_page_state_manager()
            page_manager.save_page_state(
                uid=uid,
                company_id=company_id,
                page="expenses",
                mandate_path=mandate_path,
                data=full_data
            )
            logger.info(f"[EXPENSES] Page state saved for fast recovery")
        except Exception as cache_err:
            logger.warning(f"[EXPENSES] Failed to save page state: {cache_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 4: Send response via WebSocket
        # ════════════════════════════════════════════════════════════
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.FULL_DATA,
            "payload": {
                "success": True,
                "data": full_data,
                "company_id": company_id,
                "from_cache": result.get("from_cache", False),
            }
        })

        logger.info(
            f"[EXPENSES] Orchestration completed for company={company_id}: "
            f"total={expenses_data.get('metrics', {}).get('totalToProcess', 0) + expenses_data.get('metrics', {}).get('totalInProcess', 0) + expenses_data.get('metrics', {}).get('totalPending', 0) + expenses_data.get('metrics', {}).get('totalProcessed', 0)} expenses"
        )

    except Exception as e:
        logger.error(f"[EXPENSES] Orchestration failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": str(e),
                "code": "ORCHESTRATION_ERROR"
            }
        })


async def handle_expenses_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle expenses.refresh WebSocket event.

    Refreshes expenses data by invalidating cache and re-fetching.
    Also updates dashboard widget metrics.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {"company_id": str}
    """
    company_id = payload.get("company_id")

    logger.info(f"[EXPENSES] Refresh requested for company={company_id}")

    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID"
            }
        })
        return

    # Get company context
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing mandate_path",
                "code": "MISSING_MANDATE_PATH"
            }
        })
        return

    try:
        # Refresh data
        handlers = get_expenses_handlers()
        result = await handlers.refresh_expenses(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path
        )

        if result.get("success"):
            expenses_data = result.get("data", {})

            # Update page state
            try:
                from app.wrappers.page_state_manager import get_page_state_manager
                page_manager = get_page_state_manager()
                page_manager.save_page_state(
                    uid=uid,
                    company_id=company_id,
                    page="expenses",
                    mandate_path=mandate_path,
                    data=expenses_data
                )
            except Exception as cache_err:
                logger.warning(f"[EXPENSES] Failed to update page state: {cache_err}")

            # Send refreshed data
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.REFRESHED,
                "payload": {
                    "success": True,
                    "data": expenses_data,
                    "company_id": company_id,
                }
            })

            # Also update dashboard metrics (for widget sync)
            await hub.broadcast(uid, {
                "type": WS_EVENTS.DASHBOARD.EXPENSES_UPDATE,
                "payload": {
                    "metrics": expenses_data.get("metrics", {}),
                }
            })

            logger.info(f"[EXPENSES] Refresh completed for company={company_id}")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.ERROR,
                "payload": {
                    "error": result.get("error", "Refresh failed"),
                    "code": "REFRESH_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[EXPENSES] Refresh failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": str(e),
                "code": "REFRESH_ERROR"
            }
        })


async def handle_expenses_close(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle expenses.close WebSocket event.

    Closes an expense (updates status to 'close').

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {"company_id": str, "expense_id": str}
    """
    company_id = payload.get("company_id")
    expense_id = payload.get("expense_id")

    logger.info(f"[EXPENSES] Close requested for expense={expense_id}")

    if not company_id or not expense_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing company_id or expense_id",
                "code": "MISSING_PARAMS"
            }
        })
        return

    # Get company context
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing mandate_path",
                "code": "MISSING_MANDATE_PATH"
            }
        })
        return

    try:
        handlers = get_expenses_handlers()
        result = await handlers.close_expense(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            expense_id=expense_id
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.CLOSED,
                "payload": {
                    "success": True,
                    "expense_id": expense_id,
                    "message": "Expense closed successfully"
                }
            })

            # Broadcast dashboard metrics update for widget sync (real-time update if user is on dashboard)
            metrics = result.get("metrics")
            if metrics:
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.DASHBOARD.EXPENSES_UPDATE,
                    "payload": {"metrics": metrics}
                })
                logger.info(f"[EXPENSES] Dashboard metrics broadcasted after close")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to close expense"),
                    "code": "CLOSE_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[EXPENSES] Close failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": str(e),
                "code": "CLOSE_ERROR"
            }
        })


async def handle_expenses_reopen(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle expenses.reopen WebSocket event.

    Reopens a closed expense (updates status to 'to_process').

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {"company_id": str, "expense_id": str}
    """
    company_id = payload.get("company_id")
    expense_id = payload.get("expense_id")

    logger.info(f"[EXPENSES][FLOW] ══════════════════════════════════════════════════════")
    logger.info(f"[EXPENSES][FLOW] STEP 1: Backend received REOPEN request")
    logger.info(f"[EXPENSES][FLOW] expense_id={expense_id}, company_id={company_id}")

    if not company_id or not expense_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing company_id or expense_id",
                "code": "MISSING_PARAMS"
            }
        })
        return

    # Get company context
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing mandate_path",
                "code": "MISSING_MANDATE_PATH"
            }
        })
        return

    try:
        handlers = get_expenses_handlers()
        logger.info(f"[EXPENSES][FLOW] STEP 2: Calling handlers.reopen_expense()")
        result = await handlers.reopen_expense(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            expense_id=expense_id
        )
        logger.info(f"[EXPENSES][FLOW] Handler result: success={result.get('success')}")
        logger.info(f"[EXPENSES][FLOW] Handler metrics: {result.get('metrics')}")

        if result.get("success"):
            logger.info(f"[EXPENSES][FLOW] STEP 3: Broadcasting REOPENED event to frontend")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.REOPENED,
                "payload": {
                    "success": True,
                    "expense_id": expense_id,
                    "message": "Expense reopened successfully"
                }
            })

            # Broadcast dashboard metrics update for widget sync (real-time update if user is on dashboard)
            metrics = result.get("metrics")
            if metrics:
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.DASHBOARD.EXPENSES_UPDATE,
                    "payload": {"metrics": metrics}
                })
                logger.info(f"[EXPENSES][FLOW] Dashboard metrics broadcasted: {metrics}")
            logger.info(f"[EXPENSES][FLOW] ══════════════════════════════════════════════════════")
        else:
            logger.error(f"[EXPENSES][FLOW] ❌ Handler failed: {result.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to reopen expense"),
                    "code": "REOPEN_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[EXPENSES][FLOW] ❌ Exception: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": str(e),
                "code": "REOPEN_ERROR"
            }
        })


async def handle_expenses_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle expenses.update WebSocket event.

    Updates expense fields.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {"company_id": str, "expense_id": str, "update_data": dict}
    """
    company_id = payload.get("company_id")
    expense_id = payload.get("expense_id")
    update_data = payload.get("update_data", {})

    logger.info(f"[EXPENSES] Update requested for expense={expense_id} fields={list(update_data.keys())}")

    if not company_id or not expense_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing company_id or expense_id",
                "code": "MISSING_PARAMS"
            }
        })
        return

    # Get company context
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing mandate_path",
                "code": "MISSING_MANDATE_PATH"
            }
        })
        return

    try:
        handlers = get_expenses_handlers()
        result = await handlers.update_expense(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            expense_id=expense_id,
            update_data=update_data
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.UPDATED,
                "payload": {
                    "success": True,
                    "expense_id": expense_id,
                    "message": "Expense updated successfully"
                }
            })

            # Broadcast dashboard metrics update for widget sync (real-time update if user is on dashboard)
            # (amount changes affect totalAmount in metrics)
            metrics = result.get("metrics")
            if metrics:
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.DASHBOARD.EXPENSES_UPDATE,
                    "payload": {"metrics": metrics}
                })
                logger.info(f"[EXPENSES] Dashboard metrics broadcasted after update")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to update expense"),
                    "code": "UPDATE_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[EXPENSES] Update failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": str(e),
                "code": "UPDATE_ERROR"
            }
        })


async def handle_expenses_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle expenses.delete WebSocket event.

    Deletes an expense.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {"company_id": str, "expense_id": str, "job_id"?: str, "drive_file_id"?: str}
    """
    company_id = payload.get("company_id")
    expense_id = payload.get("expense_id")
    job_id = payload.get("job_id")
    drive_file_id = payload.get("drive_file_id")

    logger.info(f"[EXPENSES] Delete requested for expense={expense_id}")

    if not company_id or not expense_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing company_id or expense_id",
                "code": "MISSING_PARAMS"
            }
        })
        return

    # Get company context
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": "Missing mandate_path",
                "code": "MISSING_MANDATE_PATH"
            }
        })
        return

    try:
        handlers = get_expenses_handlers()
        result = await handlers.delete_expense(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            expense_id=expense_id,
            job_id=job_id,
            drive_file_id=drive_file_id
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.DELETED,
                "payload": {
                    "success": True,
                    "expense_id": expense_id,
                    "message": "Expense deleted successfully"
                }
            })

            # Broadcast dashboard metrics update for widget sync
            metrics = result.get("metrics")
            if metrics:
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.DASHBOARD.EXPENSES_UPDATE,
                    "payload": {"metrics": metrics}
                })
                logger.info(f"[EXPENSES] Dashboard metrics broadcasted after delete")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.EXPENSES.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to delete expense"),
                    "code": "DELETE_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[EXPENSES] Delete failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.EXPENSES.ERROR,
            "payload": {
                "error": str(e),
                "code": "DELETE_ERROR"
            }
        })
