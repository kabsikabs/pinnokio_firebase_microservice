"""
Metrics Orchestration Handlers
==============================

Handles WebSocket events for metrics operations:
- metrics.refresh: Refresh all modules
- metrics.refresh_module: Refresh specific module

Emits:
- metrics.full_data: Complete metrics data for all modules
"""

import logging
from typing import Any, Dict

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from .handlers import get_metrics_handlers

logger = logging.getLogger("metrics.orchestration")


async def handle_metrics_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle metrics.refresh WebSocket event.

    Refreshes all metrics and emits metrics.full_data.

    Payload:
        {
            "company_id": "xxx",
            "mandate_path": "clients/xxx/companies/xxx",
            "force_refresh": true
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    force_refresh = payload.get("force_refresh", True)

    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.METRICS.UPDATE_FAILED,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID",
            }
        })
        return

    try:
        logger.info(f"[METRICS] Refresh all - uid={uid} company_id={company_id}")

        # Get metrics using handlers
        handlers = get_metrics_handlers()
        result = await handlers.full_data(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            force_refresh=force_refresh,
        )

        if result.get("success"):
            # Emit metrics.full_data
            await hub.broadcast(uid, {
                "type": WS_EVENTS.METRICS.FULL_DATA,
                "payload": {
                    "success": True,
                    **result["data"],
                }
            })
            logger.info(f"[METRICS] Refresh complete - company_id={company_id}")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.METRICS.UPDATE_FAILED,
                "payload": {
                    "error": result.get("error", {}).get("message", "Unknown error"),
                    "code": result.get("error", {}).get("code", "REFRESH_ERROR"),
                }
            })

    except Exception as e:
        logger.error(f"[METRICS] Refresh error: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.METRICS.UPDATE_FAILED,
            "payload": {
                "error": str(e),
                "code": "REFRESH_ERROR",
            }
        })


async def handle_metrics_refresh_module(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle metrics.refresh_module WebSocket event.

    Refreshes a specific module's metrics.

    Payload:
        {
            "company_id": "xxx",
            "mandate_path": "clients/xxx/companies/xxx",
            "module": "routing",  # routing, ap, bank, expenses
            "force_refresh": true
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    module = payload.get("module")
    force_refresh = payload.get("force_refresh", True)

    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.METRICS.UPDATE_FAILED,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID",
            }
        })
        return

    if not module:
        # If no module specified, refresh all
        await handle_metrics_refresh(uid, session_id, payload)
        return

    try:
        logger.info(f"[METRICS] Refresh module={module} - uid={uid} company_id={company_id}")

        handlers = get_metrics_handlers()
        result = await handlers.module_data(
            user_id=uid,
            company_id=company_id,
            module=module,
            mandate_path=mandate_path,
            force_refresh=force_refresh,
        )

        if result.get("success"):
            # Emit module-specific update event
            event_type_map = {
                "routing": WS_EVENTS.METRICS.ROUTING_UPDATE,
                "ap": WS_EVENTS.METRICS.AP_UPDATE,
                "bank": WS_EVENTS.METRICS.BANK_UPDATE,
                "expenses": WS_EVENTS.METRICS.EXPENSES_UPDATE,
            }

            event_type = event_type_map.get(module, WS_EVENTS.METRICS.FULL_DATA)

            await hub.broadcast(uid, {
                "type": event_type,
                "payload": {
                    "success": True,
                    "module": module,
                    "data": result["data"],
                    "company_id": company_id,
                }
            })
            logger.info(f"[METRICS] Module refresh complete - module={module}")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.METRICS.UPDATE_FAILED,
                "payload": {
                    "error": result.get("error", {}).get("message", "Unknown error"),
                    "code": result.get("error", {}).get("code", "MODULE_REFRESH_ERROR"),
                    "module": module,
                }
            })

    except Exception as e:
        logger.error(f"[METRICS] Module refresh error: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.METRICS.UPDATE_FAILED,
            "payload": {
                "error": str(e),
                "code": "MODULE_REFRESH_ERROR",
                "module": module,
            }
        })


async def emit_metrics_full_data(
    uid: str,
    company_id: str,
    mandate_path: str,
    force_refresh: bool = False,
) -> None:
    """
    Utility function to emit metrics.full_data.

    Can be called from other handlers (e.g., dashboard orchestration)
    to populate metrics stores.

    Args:
        uid: Firebase user ID
        company_id: Company/mandate ID
        mandate_path: Full Firestore path to mandate
        force_refresh: Force cache invalidation
    """
    try:
        handlers = get_metrics_handlers()
        result = await handlers.full_data(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            force_refresh=force_refresh,
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.METRICS.FULL_DATA,
                "payload": {
                    "success": True,
                    **result["data"],
                }
            })
            logger.info(f"[METRICS] Emitted full_data for company_id={company_id}")
        else:
            logger.warning(
                f"[METRICS] Failed to emit full_data: {result.get('error')}"
            )

    except Exception as e:
        logger.error(f"[METRICS] emit_metrics_full_data error: {e}", exc_info=True)


async def confirm_optimistic_update(
    uid: str,
    update_id: str,
    module: str,
    new_counts: Dict[str, int],
) -> None:
    """
    Confirm an optimistic update after successful backend operation.

    Args:
        uid: Firebase user ID
        update_id: The optimistic update ID from frontend
        module: Module name (routing, ap, bank, expenses)
        new_counts: The actual counts after operation
    """
    try:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.METRICS.UPDATE_CONFIRMED,
            "payload": {
                "update_id": update_id,
                "module": module,
                "counts": new_counts,
            }
        })
        logger.info(f"[METRICS] Confirmed update_id={update_id} module={module}")

    except Exception as e:
        logger.error(f"[METRICS] confirm_optimistic_update error: {e}")


async def reject_optimistic_update(
    uid: str,
    update_id: str,
    module: str,
    error: str,
) -> None:
    """
    Reject an optimistic update after failed backend operation.

    This triggers rollback on the frontend.

    Args:
        uid: Firebase user ID
        update_id: The optimistic update ID from frontend
        module: Module name (routing, ap, bank, expenses)
        error: Error message
    """
    try:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.METRICS.UPDATE_FAILED,
            "payload": {
                "update_id": update_id,
                "module": module,
                "error": error,
            }
        })
        logger.info(f"[METRICS] Rejected update_id={update_id} module={module}")

    except Exception as e:
        logger.error(f"[METRICS] reject_optimistic_update error: {e}")
