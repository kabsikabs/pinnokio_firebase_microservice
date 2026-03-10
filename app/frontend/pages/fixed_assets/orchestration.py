"""
Fixed Assets Page Orchestration
================================

Handles WebSocket events for the Fixed Assets module (PostgreSQL Neon).
Routes events to fixed_asset_rpc_handlers and broadcasts responses.

Architecture:
    Frontend (Next.js) -> wsClient.send({type: 'fixed_assets.orchestrate_init'})
                       -> WebSocket -> main.py router
                       -> handle_orchestrate_init()
                       -> fixed_asset_rpc_handlers.list_assets()
                       -> hub.broadcast(uid, {type: 'fixed_assets.full_data', payload: ...})
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.fixed_asset_rpc_handlers import get_fixed_asset_handlers

logger = logging.getLogger("fixed_assets.orchestration")


def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """Retrieve company context from Level 2 cache (same as HR pattern)."""
    from app.redis_client import get_redis
    redis_client = get_redis()
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            return data
    except Exception as e:
        logger.warning(f"[FA] Level 2 context read error: {e}")

    # Fallback Firebase
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
                return m
    except Exception as e:
        logger.error(f"[FA] Firebase fallback failed: {e}")
    return {}


async def handle_orchestrate_init(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """
    Handle fixed_assets.orchestrate_init - Load full page data.

    Fetches assets, models, eligible accounts, counts.

    payload: { company_id: str, mandate_path?: str }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing company_id or mandate_path", "code": "MISSING_CONTEXT"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()

        # Fetch in parallel
        assets_task = handlers.list_assets(mandate_path=mandate_path)
        models_task = handlers.list_asset_models(mandate_path=mandate_path)
        accounts_task = handlers.get_eligible_accounts(mandate_path=mandate_path)

        assets_result, models_result, accounts_result = await asyncio.gather(
            assets_task, models_task, accounts_task,
            return_exceptions=True
        )

        assets = assets_result if not isinstance(assets_result, Exception) else {"success": False, "error": str(assets_result)}
        models = models_result if not isinstance(models_result, Exception) else {"success": False, "error": str(models_result)}
        accounts = accounts_result if not isinstance(accounts_result, Exception) else {"success": False, "error": str(accounts_result)}

        # Count by state
        assets_list = assets.get("assets", []) if isinstance(assets, dict) else []
        counts = {"draft": 0, "running": 0, "close": 0, "disposed": 0}
        for a in assets_list:
            state = a.get("state", "draft")
            if state in counts:
                counts[state] += 1

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.FULL_DATA,
            "payload": {
                "success": True,
                "assets": assets_list,
                "counts": counts,
                "models": models.get("models", []) if isinstance(models, dict) else [],
                "eligible_accounts": accounts.get("accounts", []) if isinstance(accounts, dict) else [],
            }
        })

    except Exception as e:
        logger.error(f"[FA] orchestrate_init failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_refresh(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Handle fixed_assets.refresh - just re-run orchestrate_init."""
    await handle_orchestrate_init(uid, session_id, payload)


async def handle_asset_get(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Get single asset with depreciation lines."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        asset_result, lines_result = await asyncio.gather(
            handlers.get_asset(mandate_path=mandate_path, asset_id=asset_id),
            handlers.get_depreciation_schedule(mandate_path=mandate_path, asset_id=asset_id),
            return_exceptions=True
        )

        asset = asset_result if not isinstance(asset_result, Exception) else {"success": False}
        lines = lines_result if not isinstance(lines_result, Exception) else {"success": False}

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ASSET_LOADED,
            "payload": {
                "success": True,
                "asset": asset.get("asset") if isinstance(asset, dict) else None,
                "lines": lines.get("lines", []) if isinstance(lines, dict) else [],
            }
        })
    except Exception as e:
        logger.error(f"[FA] asset_get failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_create(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Create a new asset."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    data = payload.get("data", {})

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.create_asset(mandate_path=mandate_path, **data)

        if isinstance(result, dict) and result.get("success") is not False:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.ASSET_CREATED,
                "payload": {"success": True, "asset": result.get("asset")}
            })
            # Refresh full list
            await handle_orchestrate_init(uid, session_id, payload)
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.ERROR,
                "payload": {"error": result.get("error", "Create failed") if isinstance(result, dict) else "Create failed"}
            })
    except Exception as e:
        logger.error(f"[FA] asset_create failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_update(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Update an existing asset."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")
    data = payload.get("data", {})

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.update_asset(mandate_path=mandate_path, asset_id=asset_id, **data)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ASSET_UPDATED,
            "payload": {"success": True, "asset": result.get("asset") if isinstance(result, dict) else None}
        })
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] asset_update failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_delete(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Delete a draft asset."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.delete_asset(mandate_path=mandate_path, asset_id=asset_id)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ASSET_DELETED,
            "payload": {"success": True, "asset_id": asset_id}
        })
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] asset_delete failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_confirm(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Confirm asset (draft -> running)."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.confirm_asset(mandate_path=mandate_path, asset_id=asset_id)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ASSET_CONFIRMED,
            "payload": {"success": True, "asset": result.get("asset") if isinstance(result, dict) else None}
        })
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] asset_confirm failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_reset_draft(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Reset running asset back to draft (only if no posted lines)."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.reset_to_draft(mandate_path=mandate_path, asset_id=asset_id)

        if isinstance(result, dict) and result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.ASSET_RESET_DRAFT_DONE,
                "payload": {"success": True, "asset_id": asset_id}
            })
            await handle_orchestrate_init(uid, session_id, payload)
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.ERROR,
                "payload": {"error": result.get("error", "Reset to draft failed") if isinstance(result, dict) else "Reset to draft failed"}
            })
    except Exception as e:
        logger.error(f"[FA] asset_reset_draft failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_dispose(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Dispose asset (running -> disposed)."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")
    data = payload.get("data", {})

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.dispose_asset(
            mandate_path=mandate_path,
            asset_id=asset_id,
            **data
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ASSET_DISPOSED,
            "payload": {"success": True}
        })
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] asset_dispose failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_run_depreciation(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Run depreciation up to a given date, optionally for a single asset."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    date_up_to = payload.get("date_up_to")
    asset_id = payload.get("asset_id")  # optional — single-asset mode

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not date_up_to:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or date_up_to"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.run_depreciation(
            mandate_path=mandate_path, date_up_to=date_up_to, asset_id=asset_id,
            uid=uid, collection_id=company_id,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.DEPRECIATION_RESULT,
            "payload": result if isinstance(result, dict) else {"success": False}
        })
        # Refresh asset detail if single-asset mode (so lines show updated is_posted + gl_entry_ref)
        if asset_id:
            await handle_asset_get(uid, session_id, payload)
        # Refresh list to show updated states
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] run_depreciation failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_reverse_depreciation(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Reverse posted depreciation lines (creates counter-entries in ERP)."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    line_ids = payload.get("line_ids")  # optional — specific lines
    date_from = payload.get("date_from")  # optional — mass reversal range start
    date_to = payload.get("date_to")  # optional — mass reversal range end
    asset_id = payload.get("asset_id")  # optional — single-asset scope

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path"}
        })
        return

    if not line_ids and not date_from and not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Must provide line_ids, date_from/date_to, or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.reverse_depreciation(
            mandate_path=mandate_path,
            uid=uid,
            collection_id=company_id,
            line_ids=line_ids,
            date_from=date_from,
            date_to=date_to,
            asset_id=asset_id,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.REVERSAL_RESULT,
            "payload": result if isinstance(result, dict) else {"success": False}
        })
        # Refresh asset detail if single-asset mode
        if asset_id:
            await handle_asset_get(uid, session_id, payload)
        # Refresh list
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] reverse_depreciation failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_generate_pdf(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Generate depreciation schedule PDF."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    asset_id = payload.get("asset_id")
    language = payload.get("language", "fr")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not asset_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or asset_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.generate_depreciation_pdf(
            mandate_path=mandate_path,
            asset_id=asset_id,
            language=language
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.PDF_GENERATED,
            "payload": result if isinstance(result, dict) else {"success": False}
        })
    except Exception as e:
        logger.error(f"[FA] generate_pdf failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_asset_report(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """
    Generate aggregated asset report by account for a given period.

    Payload: { company_id, mandate_path?, period_date: "YYYY-MM" }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    period_date = payload.get("period_date")  # "YYYY-MM"

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not period_date:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or period_date"}
        })
        return

    try:
        from datetime import date as _date

        handlers = get_fixed_asset_handlers()
        result = await handlers.get_asset_report(
            mandate_path=mandate_path,
            period_date=period_date,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ASSET_REPORT_DATA,
            "payload": result if isinstance(result, dict) else {"success": False}
        })
    except Exception as e:
        logger.error(f"[FA] asset_report failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


# ─── Model CRUD ───────────────────────────────────────────────────────────────

async def handle_model_create(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Create a new asset model."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    data = payload.get("data", {})

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.create_asset_model(mandate_path=mandate_path, **data)

        if isinstance(result, dict) and result.get("success") is not False:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.MODEL_CREATED,
                "payload": {"success": True, "model": result.get("model")}
            })
            await handle_orchestrate_init(uid, session_id, payload)
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.ERROR,
                "payload": {"error": result.get("error", "Create model failed") if isinstance(result, dict) else "Create model failed"}
            })
    except Exception as e:
        logger.error(f"[FA] model_create failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_model_update(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Update an existing asset model."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    model_id = payload.get("model_id")
    data = payload.get("data", {})

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not model_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or model_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.update_asset_model(mandate_path=mandate_path, model_id=model_id, **data)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.MODEL_UPDATED,
            "payload": {"success": True, "model_id": model_id}
        })
        await handle_orchestrate_init(uid, session_id, payload)
    except Exception as e:
        logger.error(f"[FA] model_update failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_model_delete(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Delete an asset model (soft delete)."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    model_id = payload.get("model_id")

    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path or not model_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": "Missing mandate_path or model_id"}
        })
        return

    try:
        handlers = get_fixed_asset_handlers()
        result = await handlers.delete_asset_model(mandate_path=mandate_path, model_id=model_id)

        if isinstance(result, dict) and result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.MODEL_DELETED,
                "payload": {"success": True, "model_id": model_id}
            })
            await handle_orchestrate_init(uid, session_id, payload)
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.FIXED_ASSETS.ERROR,
                "payload": {"error": result.get("error", "Delete model failed") if isinstance(result, dict) else "Delete model failed"}
            })
    except Exception as e:
        logger.error(f"[FA] model_delete failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.FIXED_ASSETS.ERROR,
            "payload": {"error": str(e)}
        })
