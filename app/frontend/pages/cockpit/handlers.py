"""
Cockpit Dashboard Handlers
===========================

Handles all cockpit WSS events:
- cockpit.create_session  → Create a cockpit session in Firestore (sync)
- cockpit.list_sessions   → List cockpit sessions from Firestore (sync)
- cockpit.delete_session  → Delete a session + its widgets from Firestore (sync)
- cockpit.rename_session  → Rename a cockpit session in Firestore (sync)
- cockpit.generate        → Enqueue prompt to LLM worker (async, chat_mode=cockpit_chat)
- cockpit.list_widgets    → Read widgets from Firestore filtered by session (sync)
- cockpit.delete_widget   → Delete widget from Firestore (sync)
- cockpit.refresh_widget  → Re-execute saved RPC params (sync, no LLM)
- cockpit.update_layout   → Save grid layout positions (sync)

Persistence paths:
  {mandate_path}/cockpit_sessions/{session_id}
  {mandate_path}/cockpit_widgets/{widget_id}
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.redis_client import get_redis

logger = logging.getLogger("cockpit.handlers")


# =============================================================================
# HELPERS
# =============================================================================

def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """Retrieve company context from Level 2 cache."""
    redis_client = get_redis()
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            return data
    except Exception as e:
        logger.warning(f"[COCKPIT] Level 2 context read error: {e}")

    # Fallback Firebase
    try:
        from app.firebase_providers import get_firebase_management
        fb = get_firebase_management()
        mandates = fb.fetch_all_mandates_light(uid)
        for m in mandates:
            if m.get("id") == company_id or m.get("collection_id") == company_id:
                redis_client.setex(level2_key, 3600, json.dumps(m))
                return m
    except Exception as e:
        logger.error(f"[COCKPIT] Firebase fallback failed: {e}")

    return {}


def _get_firestore():
    """Get Firestore client."""
    from app.firebase_providers import get_firebase_management
    fb = get_firebase_management()
    return fb.db


# =============================================================================
# cockpit.generate — Async via LLM worker
# =============================================================================

async def handle_cockpit_generate(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enqueue a cockpit generation request to the LLM worker.

    The worker's AccountingAgent in COCKPIT mode will:
    1. Call the appropriate ACCOUNTING.* RPC tools
    2. Choose chart_type + config
    3. Return widget data via ws:broadcast:{uid}

    Frontend receives cockpit.widget_ready when done.
    """
    from app.llm_service.llm_gateway import get_llm_gateway

    company_id = payload.get("company_id")
    query = payload.get("query", "")
    cockpit_session_id = payload.get("session_id")

    if not company_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id requis"},
        }

    if not query.strip():
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "query vide"},
        }

    if not cockpit_session_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "session_id requis"},
        }

    # Look up session to get thread_key
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    try:
        db = _get_firestore()
        session_ref = db.document(f"{mandate_path}/cockpit_sessions/{cockpit_session_id}")
        session_doc = session_ref.get()
        if not session_doc.exists:
            return {
                "type": WS_EVENTS.COCKPIT.ERROR,
                "payload": {"error": f"Session {cockpit_session_id} introuvable"},
            }
        session_data = session_doc.to_dict()
        thread_key = session_data.get("thread_key", f"cockpit_{uid}_{cockpit_session_id}")
    except Exception as e:
        logger.error(f"[COCKPIT] Session lookup error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }

    # Balance check
    try:
        from app.wrappers.balance_handlers import get_balance_service
        bal_svc = get_balance_service()
        bal_result = await bal_svc.check_balance(uid, company_id)
        if bal_result and bal_result.get("balance", 1) <= 0:
            return {
                "type": WS_EVENTS.COCKPIT.ERROR,
                "payload": {
                    "error": "Solde insuffisant",
                    "balance": bal_result.get("balance", 0),
                },
            }
    except Exception as e:
        logger.warning(f"[COCKPIT] Balance check failed (non-blocking): {e}")

    # Enqueue to LLM worker with cockpit_chat mode
    gateway = get_llm_gateway()
    queue_result = await gateway.enqueue_message(
        user_id=uid,
        collection_name=company_id,
        thread_key=thread_key,
        message=query,
        chat_mode="cockpit_chat",
        session_id=cockpit_session_id,
    )

    # Update session's updated_at timestamp
    try:
        now = datetime.now(timezone.utc).isoformat()
        session_ref.update({"updated_at": now})
    except Exception as e:
        logger.warning(f"[COCKPIT] Session updated_at update failed (non-blocking): {e}")

    logger.info(
        f"[COCKPIT] Generate enqueued: uid={uid}, company={company_id}, "
        f"session={cockpit_session_id}, job_id={queue_result.get('job_id')}, query={query[:80]}..."
    )

    return {
        "type": WS_EVENTS.COCKPIT.GENERATE_QUEUED,
        "payload": {
            "success": True,
            "status": "queued",
            "job_id": queue_result.get("job_id"),
            "thread_key": thread_key,
            "session_id": cockpit_session_id,
        },
    }


# =============================================================================
# cockpit.list_widgets — Sync read from Firestore
# =============================================================================

async def handle_cockpit_list_widgets(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """List cockpit widgets for a company, filtered by session_id."""
    company_id = payload.get("company_id")
    cockpit_session_id = payload.get("session_id")

    if not company_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.WIDGETS_LOADED,
            "payload": {"widgets": [], "count": 0},
        }

    try:
        db = _get_firestore()
        widgets_ref = db.collection(f"{mandate_path}/cockpit_widgets")

        # Filter by session_id if provided
        if cockpit_session_id:
            query = widgets_ref.where("session_id", "==", cockpit_session_id)
            docs = query.stream()
        else:
            docs = widgets_ref.stream()

        widgets = [{"id": doc.id, **doc.to_dict()} for doc in docs]

        # Also load layout
        layout_ref = db.document(f"{mandate_path}/cockpit/layout")
        layout_doc = layout_ref.get()
        layout = layout_doc.to_dict() if layout_doc.exists else {}

        logger.info(f"[COCKPIT] Loaded {len(widgets)} widgets for {mandate_path} session={cockpit_session_id}")
        return {
            "type": WS_EVENTS.COCKPIT.WIDGETS_LOADED,
            "payload": {"widgets": widgets, "count": len(widgets), "layout": layout},
        }
    except Exception as e:
        logger.error(f"[COCKPIT] list_widgets error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.create_session — Create a cockpit session
# =============================================================================

async def handle_cockpit_create_session(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a new cockpit session in Firestore."""
    company_id = payload.get("company_id")
    name = payload.get("name")

    if not company_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    cockpit_session_id = f"cs-{uuid.uuid4().hex[:8]}"
    thread_key = f"cockpit_{uid}_{cockpit_session_id}"
    now = datetime.now(timezone.utc).isoformat()

    # Default name: count existing sessions
    if not name:
        try:
            db = _get_firestore()
            existing_docs = list(db.collection(f"{mandate_path}/cockpit_sessions").stream())
            name = f"Session {len(existing_docs) + 1}"
        except Exception:
            name = "Session 1"

    session_doc = {
        "id": cockpit_session_id,
        "name": name,
        "thread_key": thread_key,
        "created_at": now,
        "updated_at": now,
    }

    try:
        db = _get_firestore()
        doc_ref = db.document(f"{mandate_path}/cockpit_sessions/{cockpit_session_id}")
        doc_ref.set(session_doc)

        logger.info(f"[COCKPIT] Session created: {cockpit_session_id} name={name}")
        return {
            "type": WS_EVENTS.COCKPIT.SESSION_CREATED,
            "payload": {"success": True, "session": session_doc},
        }
    except Exception as e:
        logger.error(f"[COCKPIT] create_session error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.list_sessions — List all cockpit sessions
# =============================================================================

async def handle_cockpit_list_sessions(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """List all cockpit sessions for a company, sorted by updated_at desc."""
    company_id = payload.get("company_id")

    if not company_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.SESSIONS_LOADED,
            "payload": {"sessions": [], "count": 0},
        }

    try:
        db = _get_firestore()
        sessions_ref = db.collection(f"{mandate_path}/cockpit_sessions")
        docs = sessions_ref.stream()
        sessions = [{"id": doc.id, **doc.to_dict()} for doc in docs]

        # Sort by updated_at descending
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

        logger.info(f"[COCKPIT] Loaded {len(sessions)} sessions for {mandate_path}")
        return {
            "type": WS_EVENTS.COCKPIT.SESSIONS_LOADED,
            "payload": {"sessions": sessions, "count": len(sessions)},
        }
    except Exception as e:
        logger.error(f"[COCKPIT] list_sessions error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.delete_session — Delete session + its widgets
# =============================================================================

async def handle_cockpit_delete_session(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Delete a cockpit session and all its associated widgets."""
    company_id = payload.get("company_id")
    cockpit_session_id = payload.get("session_id")

    if not company_id or not cockpit_session_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id et session_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    try:
        db = _get_firestore()

        # Batch-delete all widgets belonging to this session
        widgets_ref = db.collection(f"{mandate_path}/cockpit_widgets")
        widget_query = widgets_ref.where("session_id", "==", cockpit_session_id)
        widget_docs = list(widget_query.stream())
        batch = db.batch()
        for w_doc in widget_docs:
            batch.delete(w_doc.reference)

        # Delete the session doc itself
        session_ref = db.document(f"{mandate_path}/cockpit_sessions/{cockpit_session_id}")
        batch.delete(session_ref)
        batch.commit()

        logger.info(
            f"[COCKPIT] Session deleted: {cockpit_session_id} "
            f"(+{len(widget_docs)} widgets)"
        )
        return {
            "type": WS_EVENTS.COCKPIT.SESSION_DELETED,
            "payload": {
                "success": True,
                "session_id": cockpit_session_id,
                "widgets_deleted": len(widget_docs),
            },
        }
    except Exception as e:
        logger.error(f"[COCKPIT] delete_session error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.rename_session — Rename a cockpit session
# =============================================================================

async def handle_cockpit_rename_session(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Rename a cockpit session in Firestore."""
    company_id = payload.get("company_id")
    cockpit_session_id = payload.get("session_id")
    new_name = payload.get("name", "").strip()

    if not company_id or not cockpit_session_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id et session_id requis"},
        }

    if not new_name:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "name requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    try:
        db = _get_firestore()
        session_ref = db.document(f"{mandate_path}/cockpit_sessions/{cockpit_session_id}")
        session_doc = session_ref.get()
        if not session_doc.exists:
            return {
                "type": WS_EVENTS.COCKPIT.ERROR,
                "payload": {"error": f"Session {cockpit_session_id} introuvable"},
            }

        now = datetime.now(timezone.utc).isoformat()
        session_ref.update({"name": new_name, "updated_at": now})

        logger.info(f"[COCKPIT] Session renamed: {cockpit_session_id} → '{new_name}'")
        return {
            "type": WS_EVENTS.COCKPIT.SESSION_RENAMED,
            "payload": {
                "success": True,
                "session_id": cockpit_session_id,
                "name": new_name,
            },
        }
    except Exception as e:
        logger.error(f"[COCKPIT] rename_session error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.delete_widget — Remove widget from Firestore
# =============================================================================

async def handle_cockpit_delete_widget(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Delete a pinned widget."""
    company_id = payload.get("company_id")
    widget_id = payload.get("widget_id")

    if not company_id or not widget_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id et widget_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    try:
        db = _get_firestore()
        doc_ref = db.document(f"{mandate_path}/cockpit_widgets/{widget_id}")
        doc = doc_ref.get()
        if not doc.exists:
            return {
                "type": WS_EVENTS.COCKPIT.ERROR,
                "payload": {"error": f"Widget {widget_id} introuvable"},
            }
        doc_ref.delete()
        logger.info(f"[COCKPIT] Widget deleted: {widget_id}")
        return {
            "type": WS_EVENTS.COCKPIT.WIDGET_DELETED,
            "payload": {"success": True, "widget_id": widget_id},
        }
    except Exception as e:
        logger.error(f"[COCKPIT] delete_widget error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.refresh_widget — Re-execute RPC (no LLM)
# =============================================================================

async def handle_cockpit_refresh_widget(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Refresh a widget by re-executing its saved RPC params.

    This is a DIRECT RPC call — no LLM involved, zero token cost.
    """
    company_id = payload.get("company_id")
    widget_id = payload.get("widget_id")

    if not company_id or not widget_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id et widget_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    try:
        db = _get_firestore()
        doc_ref = db.document(f"{mandate_path}/cockpit_widgets/{widget_id}")
        doc = doc_ref.get()
        if not doc.exists:
            return {
                "type": WS_EVENTS.COCKPIT.ERROR,
                "payload": {"error": f"Widget {widget_id} introuvable"},
            }

        widget = doc.to_dict()
        rpc_method = widget.get("rpc_method", "")
        rpc_params = widget.get("rpc_params", {})

        if not rpc_method:
            return {
                "type": WS_EVENTS.COCKPIT.ERROR,
                "payload": {"error": "Widget sans rpc_method — impossible de rafraichir"},
            }

        # Execute RPC directly via the accounting service
        from app.frontend.pages.cockpit.rpc_executor import execute_accounting_rpc
        data = await execute_accounting_rpc(mandate_path, rpc_method, rpc_params)

        logger.info(f"[COCKPIT] Widget refreshed: {widget_id} via {rpc_method}")
        return {
            "type": WS_EVENTS.COCKPIT.WIDGET_REFRESHED,
            "payload": {
                "success": True,
                "widget_id": widget_id,
                "title": widget.get("title", ""),
                "chart_type": widget.get("chart_type", "table"),
                "chart_config": widget.get("chart_config", {}),
                "columns": widget.get("columns", []),
                "data": data,
            },
        }
    except Exception as e:
        logger.error(f"[COCKPIT] refresh_widget error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }


# =============================================================================
# cockpit.update_layout — Save grid positions
# =============================================================================

async def handle_cockpit_update_layout(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Save cockpit grid layout (widget positions and order)."""
    company_id = payload.get("company_id")
    layout = payload.get("layout", {})

    if not company_id:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "company_id requis"},
        }

    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    if not mandate_path:
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": "mandate_path introuvable"},
        }

    try:
        db = _get_firestore()
        doc_ref = db.document(f"{mandate_path}/cockpit/layout")
        doc_ref.set({
            **layout,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, merge=True)

        logger.info(f"[COCKPIT] Layout updated for {mandate_path}")
        return {
            "type": WS_EVENTS.COCKPIT.LAYOUT_UPDATED,
            "payload": {"success": True},
        }
    except Exception as e:
        logger.error(f"[COCKPIT] update_layout error: {e}")
        return {
            "type": WS_EVENTS.COCKPIT.ERROR,
            "payload": {"error": str(e)},
        }
