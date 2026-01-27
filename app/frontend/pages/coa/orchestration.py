"""
COA Page Orchestration
======================

Handles post-authentication data loading for the COA page.

Pattern (avec Cache Niveau 2):
1. Frontend sends coa.orchestrate_init (company_id requis, mandate_path optionnel)
2. Si mandate_path non fourni, le récupérer du cache niveau 2 (company context)
3. Backend lit depuis cache niveau 2 (company:{uid}:{cid}:coa)
4. Si HIT: Affiche données immédiatement
5. Si MISS: Charge depuis Firebase -> Cache -> Affiche

Events handled:
- coa.orchestrate_init -> Load all COA data
- coa.load_accounts -> Load accounts only
- coa.load_functions -> Load functions only
- coa.save_changes -> Save modifications
- coa.sync_erp -> Sync from ERP
- coa.toggle_function -> Toggle function active
- coa.create_function -> Create custom function
- coa.update_function -> Update custom function
- coa.delete_function -> Delete custom function
"""

import json
import logging
from typing import Any, Dict

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.wrappers.page_state_manager import get_page_state_manager
from app.redis_client import get_redis
from app.llm_service.redis_namespaces import build_company_context_key

from .handlers import get_coa_handlers

logger = logging.getLogger("coa.orchestration")


async def handle_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle coa.orchestrate_init WebSocket event.

    Loads all COA page data and sends coa.full_data response.

    Architecture Cache Niveau 2:
    1. company_id est requis, mandate_path peut être récupéré du context
    2. Les données COA sont lues depuis cache niveau 2 si disponible
    3. Si MISS, chargées depuis Firebase puis cachées

    Flow:
    1. Validate company_id (requis)
    2. Si mandate_path non fourni, le récupérer du cache niveau 2 (company context)
    3. Fetch all page data (depuis cache niveau 2 ou Firebase)
    4. Save to page state cache
    5. Send response via WSS
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")

    logger.info(f"[COA] orchestrate_init uid={uid} company_id={company_id} mandate_path_provided={bool(mandate_path)}")

    # company_id est obligatoire
    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID"
            }
        })
        return

    # Si mandate_path non fourni, le récupérer du cache niveau 2 (company context)
    if not mandate_path:
        try:
            redis_client = get_redis()
            context_key = build_company_context_key(uid, company_id)
            cached = redis_client.get(context_key)
            if cached:
                context = json.loads(cached if isinstance(cached, str) else cached.decode())
                mandate_path = context.get("mandate_path", "")
                logger.info(f"[COA] mandate_path retrieved from company context: {mandate_path[:50] if mandate_path else 'none'}...")
        except Exception as e:
            logger.warning(f"[COA] Failed to get mandate_path from context: {e}")

    # Si toujours pas de mandate_path, erreur
    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {
                "error": "Missing mandate_path - return to dashboard",
                "code": "MISSING_MANDATE_PATH"
            }
        })
        return

    try:
        # 1. Fetch all data
        handlers = get_coa_handlers()
        result = await handlers.full_data(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
        )

        if not result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.COA.ERROR,
                "payload": result.get("error", {"message": "Failed to load COA data"})
            })
            return

        data = result.get("data", {})

        # 2. Save page state for fast recovery
        page_manager = get_page_state_manager()
        page_manager.save_page_state(
            uid=uid,
            company_id=company_id,
            page="coa",
            mandate_path=mandate_path,
            data=data
        )

        # 3. Send full data response
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.FULL_DATA,
            "payload": {
                "success": True,
                "data": data,
                "company_id": company_id,
            }
        })

        logger.info(f"[COA] orchestrate_init complete: {data.get('total_accounts', 0)} accounts, {len(data.get('functions', []))} functions")

    except Exception as e:
        logger.error(f"[COA] orchestrate_init failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {
                "error": str(e),
                "code": "ORCHESTRATION_ERROR"
            }
        })


async def handle_load_accounts(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.load_accounts WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    force_refresh = payload.get("force_refresh", False)

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing company context"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.load_accounts(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            force_refresh=force_refresh,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ACCOUNTS_LOADED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] load_accounts failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_load_functions(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.load_functions WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    force_refresh = payload.get("force_refresh", False)

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing company context"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.load_functions(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            force_refresh=force_refresh,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.FUNCTIONS_LOADED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] load_functions failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_save_changes(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.save_changes WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    modified_rows = payload.get("modified_rows", {})
    client_uuid = payload.get("client_uuid")

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing company context"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.save_changes(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            modified_rows=modified_rows,
            client_uuid=client_uuid,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.CHANGES_SAVED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] save_changes failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_sync_erp(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.sync_erp WebSocket event."""
    company_id = payload.get("company_id")
    client_uuid = payload.get("client_uuid")

    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing company_id"}
        })
        return

    try:
        # Send progress start
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.SYNC_PROGRESS,
            "payload": {
                "stage": "starting",
                "progress": 0,
                "message": "Starting ERP synchronization..."
            }
        })

        handlers = get_coa_handlers()
        result = await handlers.sync_from_erp(
            uid=uid,
            company_id=company_id,
            client_uuid=client_uuid,
        )

        # Send complete
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.SYNC_COMPLETE,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] sync_erp failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_toggle_function(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.toggle_function WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    function_name = payload.get("function_name")
    active = payload.get("active", True)

    if not company_id or not mandate_path or not function_name:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing required parameters"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.toggle_function(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            function_name=function_name,
            active=active,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.FUNCTION_TOGGLED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] toggle_function failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_create_function(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.create_function WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    display_name = payload.get("display_name")
    nature = payload.get("nature", "PROFIT_AND_LOSS")
    definition = payload.get("definition", "")
    active = payload.get("active", True)

    if not company_id or not mandate_path or not display_name:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing required parameters"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.create_function(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            display_name=display_name,
            nature=nature,
            definition=definition,
            active=active,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.FUNCTION_SAVED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] create_function failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_update_function(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.update_function WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    function_name = payload.get("function_name")
    display_name = payload.get("display_name")
    definition = payload.get("definition")
    nature = payload.get("nature")
    active = payload.get("active")

    if not company_id or not mandate_path or not function_name:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing required parameters"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.update_function(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            function_name=function_name,
            display_name=display_name,
            definition=definition,
            nature=nature,
            active=active,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.FUNCTION_SAVED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] update_function failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_delete_function(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle coa.delete_function WebSocket event."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    function_name = payload.get("function_name")

    if not company_id or not mandate_path or not function_name:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": "Missing required parameters"}
        })
        return

    try:
        handlers = get_coa_handlers()
        result = await handlers.delete_function(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            function_name=function_name,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.FUNCTION_DELETED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"[COA] delete_function failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COA.ERROR,
            "payload": {"error": str(e)}
        })


# ===================================================================
# EVENT ROUTING MAP
# ===================================================================

COA_EVENT_HANDLERS = {
    "coa.orchestrate_init": handle_orchestrate_init,
    "coa.load_accounts": handle_load_accounts,
    "coa.load_functions": handle_load_functions,
    "coa.save_changes": handle_save_changes,
    "coa.sync_erp": handle_sync_erp,
    "coa.toggle_function": handle_toggle_function,
    "coa.create_function": handle_create_function,
    "coa.update_function": handle_update_function,
    "coa.delete_function": handle_delete_function,
}
