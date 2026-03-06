"""
Company Settings Page Orchestration
====================================

Handles post-authentication data loading for the Company Settings page.

OPTIMIZED PATTERN (NEW):
    1. Frontend: page mount
    2. Frontend uses data from auth-store.selectedCompany (workflowParams, contexts)
    3. Frontend uses data from static-data-store (dropdowns)
    4. Frontend: wsClient.send({ type: 'company_settings.fetch_additional', payload: {...} })
    5. This handler loads ONLY: telegramUsers, communicationRoomsConfig, erpConnections

LEGACY PATTERN (kept for compatibility):
    1. Frontend: wsClient.send({ type: 'page.restore_state', payload: { page: 'company_settings', company_id } })
    2. Backend: Check Redis cache
    3a. Cache HIT: ws_hub.send({ type: 'page.state_restored', payload: { page, data } })
    3b. Cache MISS: triggers full_data() load

SYNC AFTER SAVE:
    After any save operation, re-broadcasts COMPANY.DETAILS to keep auth-store in sync.
"""

import asyncio
import logging
from typing import Dict, Any

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.wrappers.page_state_manager import get_page_state_manager
from app.firebase_providers import FirebaseManagement
from .handlers import get_company_settings_handlers

logger = logging.getLogger("company_settings.orchestration")


# ============================================
# HELPER: Re-broadcast COMPANY.DETAILS after save
# ============================================

async def _rebroadcast_company_details(
    uid: str,
    company_id: str,
    mandate_path: str,
) -> None:
    """
    Re-fetch and broadcast updated company details after a save operation.

    Uses fetch_single_mandate + _build_workflow_params (same path as initial load)
    to ensure the frontend receives the exact same payload format (snake_case keys).
    Includes workflow_params AND communication_settings so all sections stay in sync.
    """
    try:
        from app.wrappers.dashboard_orchestration_handlers import _build_workflow_params
        from app.firebase_providers import get_firebase_management

        firebase_mgmt = get_firebase_management()

        # Re-load mandate data (includes workflow_params subcollection)
        selected_mandate = firebase_mgmt.fetch_single_mandate(mandate_path)
        if not selected_mandate:
            logger.warning(f"[COMPANY_SETTINGS] Could not re-fetch mandate at {mandate_path}")
            return

        # Build workflow_params in the same snake_case format as initial company.details load
        workflow_params = _build_workflow_params(selected_mandate)

        # Build communication_settings (same format as dashboard_orchestration_handlers)
        communication_settings = {
            "dms_type": selected_mandate.get("dms_type", "odoo"),
            "chat_type": selected_mandate.get("chat_type", "pinnokio"),
            "communication_log_type": selected_mandate.get("communication_log_type", "pinnokio"),
        }

        # Load email_settings if present
        email_settings = None
        try:
            email_settings_doc = firebase_mgmt.get_document(f"{mandate_path}/setup/email_settings")
            if email_settings_doc:
                email_settings = email_settings_doc
        except Exception:
            pass

        # Read email_type from mandate
        email_type = selected_mandate.get("email_type")

        # Check email auth status (token exists?)
        email_auth_status = "none"
        try:
            token = firebase_mgmt.user_app_permission_token(uid, service="gmail")
            if token and token.get("token"):
                email_auth_status = "connected"
        except Exception:
            pass

        # Broadcast as COMPANY.DETAILS partial update so the existing
        # handleCompanyDetailsUpdate handler can pick it up and sync the store
        payload_data = {
            "contact_space_id": company_id,
            "mandate_path": mandate_path,
            "workflow_params": workflow_params,
            "communication_settings": communication_settings,
            "email_type": email_type,
            "email_auth_status": email_auth_status,
            "_partialUpdate": True,
        }
        if email_settings is not None:
            payload_data["email_settings"] = email_settings

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY.DETAILS,
            "payload": payload_data,
        })

        logger.info(f"[COMPANY_SETTINGS] Re-broadcasted company details for company_id={company_id}")

        # Refresh Redis L2 cache + invalidate Worker LLM cache
        _refresh_company_context_cache(uid, company_id, selected_mandate, workflow_params)

    except Exception as e:
        logger.warning(f"[COMPANY_SETTINGS] Failed to re-broadcast company details: {e}")


def _refresh_company_context_cache(
    uid: str,
    company_id: str,
    selected_mandate: Dict[str, Any],
    workflow_params: Dict[str, Any],
) -> None:
    """
    Update Redis L2 company context cache after a settings save.

    Reads the existing cache, merges fresh fields from the re-fetched mandate,
    and writes it back. Also deletes the Worker LLM cache key to force reload.
    """
    import json
    from app.redis_client import get_redis
    from app.llm_service.redis_namespaces import build_company_context_key

    COMPANY_SELECTION_TTL = 86400  # 24h — same as dashboard_orchestration_handlers

    try:
        redis_client = get_redis()
        context_key = build_company_context_key(uid, company_id)

        # 1. Read existing L2 cache
        cached = redis_client.get(context_key)
        if not cached:
            logger.info(f"[SETTINGS_CACHE] No L2 cache to refresh for {context_key}")
            # No cache → nothing to merge. Next page access will do a full fetch.
            # Still delete Worker cache just in case.
            redis_client.delete(f"context:{uid}:{company_id}")
            return

        existing = json.loads(cached if isinstance(cached, str) else cached.decode())

        # 2. Merge workflow_params (complete dict from _build_workflow_params)
        existing["workflow_params"] = workflow_params

        # 3. Merge flat fields from re-fetched mandate (source of truth = root document).
        #    NOTE: workflow-specific fields (router_*, apbookeeper_*, banker_*) are intentionally
        #    excluded here — they live in the setup/workflow_params subcollection and will be
        #    overridden correctly in step 4 from workflow_params.
        mandate_flat_fields = [
            "dms_type", "chat_type", "communication_chat_type", "communication_log_type",
            "legal_name", "legal_status", "country", "address",
            "phone_number", "email", "website", "language",
            "has_vat", "vat_number", "ownership_type", "base_currency",
        ]
        for field in mandate_flat_fields:
            if field in selected_mandate:
                existing[field] = selected_mandate[field]

        # 4. Promote ALL workflow flat fields from workflow_params (source of truth = subcollection).
        #    IMPORTANT: save_workflow() writes ONLY to setup/workflow_params subcollection, NOT
        #    to the root mandate document. fetch_single_mandate() reads these flat fields from
        #    the root doc → they are always stale after a workflow save. We must override them
        #    here from the correctly-built workflow_params dict.
        workflow_flat_map = {
            # Router
            "router_automated_workflow": True,
            "router_approval_required": False,
            "router_communication_method": "",
            "router_approval_pendinglist_enabled": False,
            "router_trust_threshold_required": False,
            "router_trust_threshold_percent": 80,
            # APbookeeper
            "apbookeeper_approval_required": False,
            "apbookeeper_approval_contact_creation": False,
            "apbookeeper_communication_method": "",
            "apbookeeper_automated_workflow": False,
            "apbookeeper_approval_pendinglist_enabled": False,
            # Banker
            "banker_approval_required": False,
            "banker_approval_threshold_workflow": 0,
            "banker_communication_method": "",
            "banker_approval_pendinglist_enabled": False,
            "banker_gl_approval": False,
            "banker_voucher_approval": False,
        }
        for flat_key, default in workflow_flat_map.items():
            existing[flat_key] = workflow_params.get(flat_key, existing.get(flat_key, default))

        # 4b. Sync communication_chat_type ← chat_type (save_settings writes both)
        #     fetch_single_mandate may NOT return communication_chat_type,
        #     so we force-sync from chat_type to avoid stale L2 cache values.
        if "chat_type" in existing:
            existing["communication_chat_type"] = existing["chat_type"]

        # 5. Merge communication_settings (nested)
        prev_comm = existing.get("communication_settings", {})
        existing["communication_settings"] = {
            "dms_type": selected_mandate.get("dms_type", prev_comm.get("dms_type", "odoo")),
            "chat_type": selected_mandate.get("chat_type", prev_comm.get("chat_type", "pinnokio")),
            "communication_log_type": selected_mandate.get(
                "communication_log_type", prev_comm.get("communication_log_type", "pinnokio")
            ),
        }

        # 6. Merge context_details if present (save_context flow)
        if "context_details" in selected_mandate:
            existing["context_details"] = selected_mandate["context_details"]

        # 7. Write back to Redis
        redis_client.setex(context_key, COMPANY_SELECTION_TTL, json.dumps(existing))
        logger.info(f"[SETTINGS_CACHE] L2 cache refreshed: {context_key}")

        # 8. Delete Worker LLM Redis cache (forces reload from Firebase)
        worker_key = f"context:{uid}:{company_id}"
        deleted = redis_client.delete(worker_key)
        logger.info(f"[SETTINGS_CACHE] Worker cache deleted: {worker_key} (deleted={deleted})")

    except Exception as e:
        logger.warning(f"[SETTINGS_CACHE] Failed to refresh L2 cache: {e}")


# ============================================
# OPTIMIZED HANDLER: Fetch Additional Data Only
# ============================================

async def handle_fetch_additional(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.fetch_additional WebSocket event.

    NEW OPTIMIZED HANDLER that loads ONLY data not included in COMPANY.DETAILS:
    - telegramUsers
    - communicationRoomsConfig
    - erpConnections

    Frontend should already have from auth-store.selectedCompany:
    - companyInfo (from COMPANY.DETAILS flat fields)
    - workflowParams (from COMPANY.DETAILS.workflowParams)
    - contexts (from COMPANY.DETAILS.contexts)

    Frontend should already have from static-data-store:
    - dropdowns (countries, currencies, languages, etc.)

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            "company_id": str,
            "mandate_path": str
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {
                "error": "Missing required parameters: company_id and mandate_path",
                "code": "INVALID_PARAMS"
            }
        })
        return

    try:
        logger.info(f"[COMPANY_SETTINGS] Fetching additional data for company_id={company_id}")

        handlers = get_company_settings_handlers()
        result = await handlers.additional_data(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
        )

        if not result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to fetch additional data"),
                    "code": "FETCH_ERROR"
                }
            })
            return

        # Send response to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ADDITIONAL_DATA,
            "payload": {
                "success": True,
                "data": result.get("data"),
                "company_id": company_id,
            }
        })

        logger.info(f"[COMPANY_SETTINGS] Additional data sent for company_id={company_id}")

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Fetch additional data failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e), "code": "FETCH_ERROR"}
        })


# ============================================
# LEGACY HANDLER: Full Orchestration (kept for compatibility)
# ============================================

async def handle_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.orchestrate_init WebSocket event.

    LEGACY: This handler is kept for backward compatibility.
    NEW CODE should use handle_fetch_additional instead.

    This is called when:
    1. User navigates to Company Settings page
    2. Page state was not found in cache (cache miss)
    3. Force refresh was requested

    Flow:
    1. Validate company context (company_id, mandate_path)
    2. Fetch all page data using handlers.full_data()
    3. Save page state to Redis (keyed by company_id for cache persistence)
    4. Send response via WSS

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            "company_id": str,
            "mandate_path": str,
            "parent_doc_id": str (optional),
            "force_refresh": bool (optional)
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    force_refresh = payload.get("force_refresh", False)

    # Validate required params
    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {
                "error": "Missing required parameters: company_id and mandate_path",
                "code": "INVALID_PARAMS"
            }
        })
        logger.warning(f"[COMPANY_SETTINGS] Orchestration failed: missing company_id or mandate_path")
        return

    try:
        logger.info(f"[COMPANY_SETTINGS] Orchestration started for company_id={company_id}")

        # 1. Fetch data using handlers
        handlers = get_company_settings_handlers()
        result = await handlers.full_data(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            force_refresh=force_refresh,
        )

        if not result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to fetch company settings data"),
                    "code": "FETCH_ERROR"
                }
            })
            return

        # 2. Save page state for fast recovery (keyed by company_id, NOT session_id!)
        page_manager = get_page_state_manager()
        page_manager.save_page_state(
            uid=uid,
            company_id=company_id,
            page="company_settings",
            mandate_path=mandate_path,
            data=result.get("data", {})
        )

        # 3. Send response to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.FULL_DATA,
            "payload": {
                "success": True,
                "data": result.get("data"),
                "company_id": company_id,
            }
        })

        logger.info(f"[COMPANY_SETTINGS] Orchestration complete for company_id={company_id}")

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Orchestration failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {
                "error": str(e),
                "code": "ORCHESTRATION_ERROR"
            }
        })


async def handle_save_company_info(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_company_info WebSocket event.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "data": {...}
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    data = payload.get("data", {})

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id or mandate_path"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_company_info(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            data=data,
        )

        # Broadcast save result
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.COMPANY_INFO_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

        # Re-broadcast COMPANY.DETAILS to sync auth-store
        if result.get("success"):
            await _rebroadcast_company_details(uid, company_id, mandate_path)

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save company info failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_save_settings(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_settings WebSocket event.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "section": "dms" | "communication" | "accounting",
            "data": {...}
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    section = payload.get("section")
    data = payload.get("data", {})

    if not company_id or not mandate_path or not section:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id, mandate_path, or section"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_settings(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            section=section,
            data=data,
        )

        # Broadcast save result
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.SETTINGS_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "section": section,
                "company_id": company_id,
            }
        })

        # Re-broadcast COMPANY.DETAILS to sync auth-store
        if result.get("success"):
            await _rebroadcast_company_details(uid, company_id, mandate_path)

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save settings failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_save_workflow(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_workflow WebSocket event.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "section": "router" | "banker" | "apbookeeper" | "accountingDate",
            "data": {...}
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    section = payload.get("section")
    data = payload.get("data", {})

    if not company_id or not mandate_path or not section:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id, mandate_path, or section"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_workflow(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            section=section,
            data=data,
        )

        # Broadcast save result
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.WORKFLOW_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "section": section,
                "company_id": company_id,
            }
        })

        # Re-broadcast COMPANY.DETAILS to sync auth-store (workflow changes)
        if result.get("success"):
            await _rebroadcast_company_details(uid, company_id, mandate_path)

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save workflow failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_save_context(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_context WebSocket event.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "context_type": str,
            "content": str
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    context_type = payload.get("context_type")
    content = payload.get("content", "")

    if not company_id or not mandate_path or not context_type:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id, mandate_path, or context_type"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_context(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            context_type=context_type,
            content=content,
        )

        # Broadcast save result
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.CONTEXT_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "context_type": context_type,
                "company_id": company_id,
            }
        })

        # Re-broadcast COMPANY.DETAILS to sync auth-store (context changes)
        if result.get("success"):
            await _rebroadcast_company_details(uid, company_id, mandate_path)

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save context failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


# ============================================
# DMS OPERATIONS: Fiscal Folder Creation
# ============================================

async def handle_create_fiscal_folders(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.create_fiscal_folders WebSocket event.

    Launches DMS_CREATION(command='create_folders') in a background thread
    so Drive API calls don't block the event loop.
    The frontend button stays in "working" state until the response arrives.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "fiscal_year": int
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    fiscal_year = payload.get("fiscal_year")

    if not company_id or not mandate_path or not fiscal_year:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id, mandate_path, or fiscal_year"}
        })
        return

    try:
        logger.info(
            f"[COMPANY_SETTINGS] Creating fiscal folders for "
            f"company_id={company_id} fiscal_year={fiscal_year}"
        )

        handlers = get_company_settings_handlers()

        # Run synchronous DMS_CREATION in a thread to avoid blocking event loop
        result = await asyncio.to_thread(
            handlers.create_fiscal_folders,
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            fiscal_year=fiscal_year,
        )

        logger.info(
            f"[COMPANY_SETTINGS] Fiscal folders handler returned: {result}"
        )

        # Broadcast result to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.FISCAL_FOLDERS_CREATED,
            "payload": {
                "success": result.get("success", False),
                "message": result.get("message"),
                "folders_created": result.get("folders_created", 0),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

        logger.info(
            f"[COMPANY_SETTINGS] Fiscal folders creation completed for "
            f"company_id={company_id} success={result.get('success')}"
        )

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Create fiscal folders failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


# ============================================
# ASSET MANAGEMENT HANDLERS
# ============================================

async def handle_save_asset_config(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_asset_config WebSocket event.

    Args:
        payload: {
            company_id: string,
            mandate_path: string,
            data: {
                assetManagementActivated: boolean,
                assetAutomatedCreation: boolean,
                assetDefaultMethod: string,
                assetDefaultMethodPeriod: string,
            }
        }
    """
    company_id = payload.get("company_id", "")
    mandate_path = payload.get("mandate_path", "")
    data = payload.get("data", {})

    logger.info(f"[COMPANY_SETTINGS] save_asset_config company_id={company_id}")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_asset_config(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            data=data,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ASSET_CONFIG_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save asset config failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_list_asset_models(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.list_asset_models WebSocket event.

    Args:
        payload: {
            company_id: string,
            erp_type: string (e.g., 'odoo')
        }
    """
    company_id = payload.get("company_id", "")
    erp_type = payload.get("erp_type", "odoo")

    logger.info(f"[COMPANY_SETTINGS] list_asset_models company_id={company_id} erp_type={erp_type}")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.list_asset_models(
            user_id=uid,
            company_id=company_id,
            erp_type=erp_type,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ASSET_MODELS_DATA,
            "payload": {
                "success": result.get("success"),
                "models": result.get("models", []),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] List asset models failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_create_asset_model(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.create_asset_model WebSocket event.

    Creates a new asset model in the ERP with account mappings.

    Args:
        payload: {
            company_id: string,
            name: string,
            accountAssetId: int,
            accountDepreciationId: int,
            accountDepreciationExpenseId: int,
            method: string ('linear' or 'degressive'),
            methodPeriod: int (1, 3, 6, 12),
            durationYears: int
        }
    """
    company_id = payload.get("company_id", "")
    name = payload.get("name", "")
    account_asset_id = payload.get("accountAssetId", 0)
    account_depreciation_id = payload.get("accountDepreciationId", 0)
    account_depreciation_expense_id = payload.get("accountDepreciationExpenseId", 0)
    method = payload.get("method", "linear")
    method_period = payload.get("methodPeriod", 12)
    duration_years = payload.get("durationYears", 5)

    logger.info(f"[COMPANY_SETTINGS] create_asset_model company_id={company_id} name={name}")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.create_asset_model(
            user_id=uid,
            company_id=company_id,
            name=name,
            account_asset_id=account_asset_id,
            account_depreciation_id=account_depreciation_id,
            account_depreciation_expense_id=account_depreciation_expense_id,
            method=method,
            method_period=method_period,
            duration_years=duration_years,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ASSET_MODEL_CREATED,
            "payload": {
                "success": result.get("success"),
                "model": result.get("model"),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Create asset model failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_update_asset_model(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.update_asset_model WebSocket event.

    Updates an existing asset model in the ERP.

    Args:
        payload: {
            company_id: string,
            modelId: int,
            name: string (optional),
            method: string (optional),
            methodPeriod: int (optional),
            durationYears: int (optional)
        }
    """
    company_id = payload.get("company_id", "")
    model_id = payload.get("modelId", 0)
    name = payload.get("name")
    method = payload.get("method")
    method_period = payload.get("methodPeriod")
    duration_years = payload.get("durationYears")

    logger.info(f"[COMPANY_SETTINGS] update_asset_model company_id={company_id} model_id={model_id}")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.update_asset_model(
            user_id=uid,
            company_id=company_id,
            model_id=model_id,
            name=name,
            method=method,
            method_period=method_period,
            duration_years=duration_years,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ASSET_MODEL_UPDATED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "modelId": model_id,
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Update asset model failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_delete_asset_model(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.delete_asset_model WebSocket event.

    Deletes an asset model from the ERP.

    Args:
        payload: {
            company_id: string,
            modelId: int
        }
    """
    company_id = payload.get("company_id", "")
    model_id = payload.get("modelId", 0)

    logger.info(f"[COMPANY_SETTINGS] delete_asset_model company_id={company_id} model_id={model_id}")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.delete_asset_model(
            user_id=uid,
            company_id=company_id,
            model_id=model_id,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ASSET_MODEL_DELETED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "modelId": model_id,
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Delete asset model failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_load_asset_accounts(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.load_asset_accounts WebSocket event.

    Loads COA accounts filtered for asset model account mapping.

    Args:
        payload: {
            company_id: string,
            mandate_path: string
        }
    """
    company_id = payload.get("company_id", "")
    mandate_path = payload.get("mandate_path", "")

    logger.info(f"[COMPANY_SETTINGS] load_asset_accounts company_id={company_id}")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.load_asset_accounts(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ASSET_ACCOUNTS_DATA,
            "payload": {
                "success": result.get("success"),
                "data": result.get("data"),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Load asset accounts failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


# ============================================
# COMPANY DELETION HANDLER
# ============================================

async def handle_delete_company(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Orchestrate company deletion with progress broadcasting.

    Sends DELETION_PROGRESS events during the process and
    COMPANY_DELETED (or ERROR) when complete.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            "company_id": str,
            "mandate_path": str,
            "confirmation_name": str
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    confirmation_name = payload.get("confirmation_name")

    if not company_id or not mandate_path or not confirmation_name:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {
                "error": "Missing required parameters: company_id, mandate_path, confirmation_name",
                "code": "INVALID_PARAMS",
            }
        })
        return

    handlers = get_company_settings_handlers()

    # Progress callback - broadcast each step to frontend
    async def on_progress(step_name: str, step_index: int, total_steps: int):
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.DELETION_PROGRESS,
            "payload": {
                "step": step_name,
                "current": step_index,
                "total": total_steps,
                "company_id": company_id,
            }
        })

    try:
        logger.info(f"[COMPANY_SETTINGS] Delete company started for company_id={company_id}")

        # Run synchronous handler in thread to avoid blocking event loop
        # (Firebase, Drive, ChromaDB, GCS, etc.)
        result = await asyncio.to_thread(
            handlers.delete_company,
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            confirmation_name=confirmation_name,
            progress_callback=on_progress,
        )

        # ─────────────────────────────────────────────────
        # NEON HR DATABASE DELETION (async, dans la boucle principale)
        # Utilise le pool de connexions asyncpg correctement pour scalabilité
        # ─────────────────────────────────────────────────
        neon_report_entry = None
        try:
            from app.tools.neon_hr_manager import get_neon_hr_manager

            neon_manager = get_neon_hr_manager()
            neon_result = await neon_manager.delete_company(mandate_path, cascade=True)

            if neon_result.get("success"):
                counts = neon_result.get("deleted_counts", {})
                if neon_result.get("company_id"):
                    neon_report_entry = {
                        "name": "HR Database (Neon)",
                        "status": "success",
                        "detail": (
                            f"Deleted: {counts.get('employees', 0)} employees, "
                            f"{counts.get('contracts', 0)} contracts, "
                            f"{counts.get('payroll_results', 0)} payroll records, "
                            f"{counts.get('company_payroll_items', 0)} payroll items"
                        )
                    }
                    logger.info(
                        f"[COMPANY_SETTINGS] Neon HR deleted for company_id={company_id}: "
                        f"{counts}"
                    )
                else:
                    neon_report_entry = {
                        "name": "HR Database (Neon)",
                        "status": "skipped",
                        "detail": "Company not in Neon"
                    }
            else:
                neon_report_entry = {
                    "name": "HR Database (Neon)",
                    "status": "failed",
                    "detail": neon_result.get("error", "Unknown error")
                }
                logger.warning(
                    f"[COMPANY_SETTINGS] Neon HR delete failed for company_id={company_id}: "
                    f"{neon_result.get('error')}"
                )
        except Exception as neon_e:
            logger.warning(f"[COMPANY_SETTINGS] Neon HR cleanup failed: {neon_e}")
            neon_report_entry = {
                "name": "HR Database (Neon)",
                "status": "failed",
                "detail": str(neon_e)
            }

        # Ajouter le rapport Neon au résultat global
        report = result.get("report", [])
        if neon_report_entry:
            report.append(neon_report_entry)

        # Broadcast final result with report
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.COMPANY_DELETED,
            "payload": {
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "report": report,
                "company_id": company_id,
            }
        })

        logger.info(
            f"[COMPANY_SETTINGS] Delete company completed for company_id={company_id} "
            f"success={result.get('success')}"
        )

        # ─────────────────────────────────────────────────
        # POST-DELETION ACTION: Check remaining companies
        # ─────────────────────────────────────────────────
        if result.get("success"):
            try:
                firebase_mgmt = FirebaseManagement()
                
                # Fetch remaining mandates for this user
                remaining_mandates = await asyncio.to_thread(
                    firebase_mgmt.fetch_all_mandates_light,
                    uid
                )
                
                if remaining_mandates and len(remaining_mandates) > 0:
                    # Other companies exist - send switch_company action
                    first_mandate = remaining_mandates[0]
                    next_company_id = first_mandate.get("contact_space_id") or first_mandate.get("id")
                    
                    logger.info(
                        f"[COMPANY_SETTINGS] Post-deletion: {len(remaining_mandates)} companies remaining, "
                        f"switching to {next_company_id}"
                    )
                    
                    await hub.broadcast(uid, {
                        "type": WS_EVENTS.COMPANY_SETTINGS.POST_DELETION_ACTION,
                        "payload": {
                            "action": "switch_company",
                            "next_company_id": next_company_id,
                            "company_id": company_id,
                        }
                    })
                else:
                    # No companies left - send logout action
                    logger.info(
                        f"[COMPANY_SETTINGS] Post-deletion: No companies remaining for uid={uid}, "
                        "sending logout action"
                    )
                    
                    await hub.broadcast(uid, {
                        "type": WS_EVENTS.COMPANY_SETTINGS.POST_DELETION_ACTION,
                        "payload": {
                            "action": "logout",
                            "company_id": company_id,
                        }
                    })
                    
            except Exception as post_e:
                logger.error(f"[COMPANY_SETTINGS] Post-deletion action failed: {post_e}")
                # Don't fail the whole operation, just log the error

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Delete company failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e), "code": "DELETE_COMPANY_ERROR"}
        })


# ============================================
# EMAIL SETTINGS
# ============================================

async def handle_save_email_settings(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_email_settings WebSocket event.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "data": {
                "contact_groups": [...],
                "default_policy": {...}
            }
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    data = payload.get("data", {})

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id or mandate_path"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_email_settings(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            data=data,
        )

        # Broadcast save result
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.EMAIL_SETTINGS_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "company_id": company_id,
            }
        })

        # Re-broadcast COMPANY.DETAILS to sync stores (includes email_settings)
        if result.get("success"):
            await _rebroadcast_company_details(uid, company_id, mandate_path)

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save email settings failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_email_approve_draft(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.email_approve_draft WebSocket event.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "draft_id": str,
            "decision": "approve" | "reject" | "modify",
            "modified_body": str (optional, for modify)
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    draft_id = payload.get("draft_id")
    decision = payload.get("decision")

    if not company_id or not mandate_path or not draft_id or not decision:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing required parameters for email approval"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.handle_email_approval(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            draft_id=draft_id,
            decision=decision,
            modified_body=payload.get("modified_body"),
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.EMAIL_DRAFT_APPROVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "draft_id": draft_id,
                "decision": decision,
                "company_id": company_id,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Email approval failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


# ============================================
# EMAIL PROVIDER TYPE
# ============================================

async def handle_save_email_type(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.save_email_type WebSocket event.

    Payload: { company_id, mandate_path, email_type }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    email_type = payload.get("email_type")

    if not company_id or not mandate_path or not email_type:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing company_id, mandate_path or email_type"}
        })
        return

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.save_email_type(
            user_id=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            email_type=email_type,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.EMAIL_TYPE_SAVED,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "email_type": email_type,
                "company_id": company_id,
            }
        })

        if result.get("success"):
            await _rebroadcast_company_details(uid, company_id, mandate_path)

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Save email type failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_initiate_email_auth(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.initiate_email_auth WebSocket event.

    Payload: { provider }
    """
    provider = payload.get("provider", "gmail")

    try:
        handlers = get_company_settings_handlers()
        result = await handlers.initiate_email_auth(
            user_id=uid,
            provider=provider,
            session_id=session_id,
        )

        if result.get("coming_soon"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.COMPANY_SETTINGS.EMAIL_AUTH_URL,
                "payload": {
                    "success": True,
                    "coming_soon": True,
                    "provider": provider,
                }
            })
            return

        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.EMAIL_AUTH_URL,
            "payload": {
                "success": result.get("success"),
                "error": result.get("error"),
                "auth_url": result.get("auth_url"),
                "provider": provider,
            }
        })

    except Exception as e:
        logger.error(f"[COMPANY_SETTINGS] Initiate email auth failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })
