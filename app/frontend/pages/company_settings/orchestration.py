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

import logging
from typing import Dict, Any

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.wrappers.page_state_manager import get_page_state_manager
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
    Re-fetch and broadcast updated COMPANY.DETAILS after a save operation.

    This keeps auth-store.selectedCompany in sync with the latest changes.
    Only call this AFTER a successful save operation.
    """
    try:
        # Import here to avoid circular dependency
        from app.wrappers.dashboard_orchestration_handlers import (
            _load_full_workflow_params,
            _load_full_contexts,
        )
        from app.firebase_providers import get_firebase_management

        firebase_mgmt = get_firebase_management()

        # Re-load workflow params and contexts
        import asyncio
        full_workflow_params, full_contexts = await asyncio.gather(
            _load_full_workflow_params(firebase_mgmt, mandate_path),
            _load_full_contexts(firebase_mgmt, mandate_path),
        )

        # Broadcast partial update for COMPANY.DETAILS
        # Frontend should merge this with existing selectedCompany
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY.DETAILS,
            "payload": {
                "contact_space_id": company_id,
                "mandate_path": mandate_path,
                "workflowParams": full_workflow_params,
                "contexts": full_contexts,
                "_partialUpdate": True,  # Signal to frontend to merge, not replace
            }
        })

        logger.info(f"[COMPANY_SETTINGS] Re-broadcasted COMPANY.DETAILS for company_id={company_id}")

    except Exception as e:
        logger.warning(f"[COMPANY_SETTINGS] Failed to re-broadcast COMPANY.DETAILS: {e}")


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
