"""
Instruction Templates Handlers (Shared)
========================================

Handlers partagés pour le CRUD des instruction templates.
Utilisés par les 3 pages: routing, invoices, banking.

Path Firestore:
    {mandate_path}/working_doc/instruction_templates/{page_name}/items/{template_id}

Events WS (par page):
    {page}.templates_list    → handle_templates_list    → {page}.templates_data
    {page}.templates_create  → handle_templates_create  → {page}.templates_created
    {page}.templates_update  → handle_templates_update  → {page}.templates_updated
    {page}.templates_delete  → handle_templates_delete  → {page}.templates_deleted
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.ws_hub import hub
from app.redis_client import get_redis

logger = logging.getLogger("shared.instruction_templates")


def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """Retrieve company context from Level 2 cache (same pattern as page orchestrations)."""
    redis_client = get_redis()

    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            return data
    except Exception as e:
        logger.warning(f"[TEMPLATES] Level 2 context read error: {e}")

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
        logger.error(f"[TEMPLATES] Firebase fallback failed: {e}")

    return {}


async def handle_templates_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
    page_name: str,
) -> None:
    """Fetch all instruction templates for a page."""
    company_id = payload.get("company_id")
    if not company_id:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_data",
            "payload": {"success": False, "error": "Missing company_id"}
        })
        return

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_data",
            "payload": {"success": False, "error": "Session context not initialized"}
        })
        return

    try:
        from app.firebase_providers import get_firebase_management
        firebase = get_firebase_management()

        templates = firebase.fetch_instruction_templates(mandate_path, page_name)

        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_data",
            "payload": {
                "success": True,
                "templates": templates,
                "page_name": page_name,
            }
        })
        logger.info(f"[TEMPLATES] Listed {len(templates)} templates for {page_name}")

    except Exception as e:
        logger.error(f"[TEMPLATES] List error for {page_name}: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_data",
            "payload": {"success": False, "error": str(e)}
        })


async def handle_templates_create(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
    page_name: str,
) -> None:
    """Create a new instruction template."""
    company_id = payload.get("company_id")
    title = payload.get("title", "").strip()
    content = payload.get("content", "").strip()

    if not company_id or not title or not content:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_created",
            "payload": {"success": False, "error": "Missing required fields (company_id, title, content)"}
        })
        return

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_created",
            "payload": {"success": False, "error": "Session context not initialized"}
        })
        return

    try:
        from app.firebase_providers import get_firebase_management
        firebase = get_firebase_management()

        template_data = {
            "title": title,
            "content": content,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        result = firebase.create_instruction_template(mandate_path, page_name, template_data)

        if result:
            await hub.broadcast(uid, {
                "type": f"{page_name}.templates_created",
                "payload": {
                    "success": True,
                    "template": result,
                    "page_name": page_name,
                }
            })
            logger.info(f"[TEMPLATES] Created template '{title}' for {page_name}")
        else:
            await hub.broadcast(uid, {
                "type": f"{page_name}.templates_created",
                "payload": {"success": False, "error": "Failed to create template"}
            })

    except Exception as e:
        logger.error(f"[TEMPLATES] Create error for {page_name}: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_created",
            "payload": {"success": False, "error": str(e)}
        })


async def handle_templates_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
    page_name: str,
) -> None:
    """Update an existing instruction template."""
    company_id = payload.get("company_id")
    template_id = payload.get("template_id")
    title = payload.get("title", "").strip()
    content = payload.get("content", "").strip()

    if not company_id or not template_id:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_updated",
            "payload": {"success": False, "error": "Missing required fields (company_id, template_id)"}
        })
        return

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_updated",
            "payload": {"success": False, "error": "Session context not initialized"}
        })
        return

    try:
        from app.firebase_providers import get_firebase_management
        firebase = get_firebase_management()

        update_data: Dict[str, Any] = {}
        if title:
            update_data["title"] = title
        if content:
            update_data["content"] = content

        if not update_data:
            await hub.broadcast(uid, {
                "type": f"{page_name}.templates_updated",
                "payload": {"success": False, "error": "No fields to update"}
            })
            return

        success = firebase.update_instruction_template(mandate_path, page_name, template_id, update_data)

        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_updated",
            "payload": {
                "success": success,
                "template_id": template_id,
                "update_data": update_data,
                "page_name": page_name,
            }
        })
        logger.info(f"[TEMPLATES] Updated template {template_id} for {page_name}: success={success}")

    except Exception as e:
        logger.error(f"[TEMPLATES] Update error for {page_name}: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_updated",
            "payload": {"success": False, "error": str(e)}
        })


async def handle_templates_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
    page_name: str,
) -> None:
    """Delete an instruction template."""
    company_id = payload.get("company_id")
    template_id = payload.get("template_id")

    if not company_id or not template_id:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_deleted",
            "payload": {"success": False, "error": "Missing required fields (company_id, template_id)"}
        })
        return

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_deleted",
            "payload": {"success": False, "error": "Session context not initialized"}
        })
        return

    try:
        from app.firebase_providers import get_firebase_management
        firebase = get_firebase_management()

        success = firebase.delete_instruction_template(mandate_path, page_name, template_id)

        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_deleted",
            "payload": {
                "success": success,
                "template_id": template_id,
                "page_name": page_name,
            }
        })
        logger.info(f"[TEMPLATES] Deleted template {template_id} for {page_name}: success={success}")

    except Exception as e:
        logger.error(f"[TEMPLATES] Delete error for {page_name}: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": f"{page_name}.templates_deleted",
            "payload": {"success": False, "error": str(e)}
        })
