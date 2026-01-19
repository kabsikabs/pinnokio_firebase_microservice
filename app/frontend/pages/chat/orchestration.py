"""
Chat Page Orchestration Handlers
================================

Handles post-authentication data loading for the chat page.
Pattern: page.restore_state -> cache hit OR chat.orchestrate_init -> full load

Flow:
1. Frontend navigates to /chat
2. Frontend sends chat.orchestrate_init (or page.restore_state first)
3. Backend loads chat sessions, optionally selects last active chat
4. Backend sends chat.full_data with sessions list
5. Frontend displays chat interface

Dependencies (Existing Services - DO NOT MODIFY):
- firebase_realtime_chat.py: FirebaseRealtimeChat singleton
- firebase_providers.py: FirebaseManagement singleton
- redis_client.py: Redis session storage
- ws_hub.py: WebSocket broadcasting
- llm_service/session_state_manager.py: LLM session state

Author: Migration Agent
Created: 2026-01-19
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from app.redis_client import get_redis
from app.ws_events import WS_EVENTS
from app.ws_hub import hub
from app.frontend.core.page_state_manager import get_page_state_manager

from .handlers import get_chat_handlers

logger = logging.getLogger("chat.orchestration")

# ============================================
# CONSTANTS
# ============================================

ORCHESTRATION_TTL = 3600  # 1 hour
DEFAULT_CHAT_MODE = "general_chat"


# ============================================
# MAIN ORCHESTRATION HANDLERS
# ============================================

async def handle_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.orchestrate_init WebSocket event.

    Triggers the chat page data loading sequence:
    1. Load chat sessions list
    2. Optionally load last active chat history
    3. Load tasks for chat context
    4. Save page state for fast recovery

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain company_id, mandate_path

    Returns:
        Response dict with orchestration status
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    selected_session_id = payload.get("session_id")  # Optional pre-selected session

    if not company_id or not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.ERROR,
            "payload": {"error": "Missing company context", "code": "MISSING_CONTEXT"}
        })
        return {
            "type": "chat.orchestrate_init",
            "payload": {"success": False, "error": "Missing company context"}
        }

    logger.info(
        f"[CHAT] Orchestration started: uid={uid} company={company_id} "
        f"session_id={selected_session_id}"
    )

    try:
        # Run orchestration in background
        asyncio.create_task(
            _run_chat_orchestration(
                uid=uid,
                session_id=session_id,
                company_id=company_id,
                mandate_path=mandate_path,
                selected_session_id=selected_session_id,
            )
        )

        return {
            "type": "chat.orchestrate_init",
            "payload": {"success": True, "message": "Orchestration started"}
        }

    except Exception as e:
        logger.error(f"[CHAT] Orchestration init error: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.ERROR,
            "payload": {"error": str(e), "code": "ORCHESTRATION_ERROR"}
        })
        return {
            "type": "chat.orchestrate_init",
            "payload": {"success": False, "error": str(e)}
        }


async def _run_chat_orchestration(
    uid: str,
    session_id: str,
    company_id: str,
    mandate_path: str,
    selected_session_id: Optional[str] = None,
):
    """
    Run the full chat page orchestration sequence.

    Steps:
    1. Load all chat sessions
    2. Load tasks for task sidebar
    3. If selected_session_id provided, load its history
    4. Save page state for recovery
    5. Broadcast full_data
    """
    chat_handlers = get_chat_handlers()
    page_manager = get_page_state_manager()

    try:
        # ─────────────────────────────────────────────────
        # Step 1: Load chat sessions
        # ─────────────────────────────────────────────────
        logger.info(f"[CHAT] Step 1: Loading sessions for {company_id}")

        sessions_result = await chat_handlers.list_sessions(
            uid=uid,
            company_id=company_id,
            space_code=company_id,  # Usually same as company_id
            mode="chats"
        )

        sessions = sessions_result.get("sessions", [])
        logger.info(f"[CHAT] Loaded {len(sessions)} sessions")

        # ─────────────────────────────────────────────────
        # Step 2: Load tasks
        # ─────────────────────────────────────────────────
        logger.info(f"[CHAT] Step 2: Loading tasks")

        tasks_result = await chat_handlers.list_tasks(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path
        )

        tasks = tasks_result.get("tasks", [])
        logger.info(f"[CHAT] Loaded {len(tasks)} tasks")

        # ─────────────────────────────────────────────────
        # Step 3: Load selected session history (if provided)
        # ─────────────────────────────────────────────────
        messages = []
        current_session = None

        if selected_session_id:
            logger.info(f"[CHAT] Step 3: Loading history for {selected_session_id}")

            history_result = await chat_handlers.load_history(
                uid=uid,
                company_id=company_id,
                space_code=company_id,
                thread_key=selected_session_id,
                mode="chats"
            )

            messages = history_result.get("messages", [])
            current_session = next(
                (s for s in sessions if s.get("thread_key") == selected_session_id),
                None
            )

            logger.info(f"[CHAT] Loaded {len(messages)} messages")

        # ─────────────────────────────────────────────────
        # Step 4: Build full data payload
        # ─────────────────────────────────────────────────
        full_data = {
            "sessions": sessions,
            "tasks": tasks,
            "current_session": current_session,
            "messages": messages,
            "company_id": company_id,
            "mandate_path": mandate_path,
        }

        # ─────────────────────────────────────────────────
        # Step 5: Save page state for recovery
        # ─────────────────────────────────────────────────
        page_manager.save_page_state(
            uid=uid,
            company_id=company_id,
            page="chat",
            mandate_path=mandate_path,
            data=full_data
        )

        logger.info(f"[CHAT] Page state saved for recovery")

        # ─────────────────────────────────────────────────
        # Step 6: Broadcast full data
        # ─────────────────────────────────────────────────
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.FULL_DATA,
            "payload": {
                "success": True,
                "data": full_data,
                "company_id": company_id,
            }
        })

        logger.info(f"[CHAT] Orchestration complete: uid={uid}")

        # ─────────────────────────────────────────────────
        # Step 7: Update LLM session state (mark as on chat page)
        # ─────────────────────────────────────────────────
        asyncio.create_task(
            _update_llm_session_for_chat(
                uid=uid,
                company_id=company_id,
                mandate_path=mandate_path,
                thread_key=selected_session_id,
            )
        )

    except Exception as e:
        logger.error(f"[CHAT] Orchestration error: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.ERROR,
            "payload": {"error": str(e), "code": "ORCHESTRATION_ERROR"}
        })


# ============================================
# LLM SESSION HELPERS
# ============================================

async def _update_llm_session_for_chat(
    uid: str,
    company_id: str,
    mandate_path: str,
    thread_key: Optional[str] = None,
):
    """
    Update LLM session state to indicate user is on chat page.

    This allows the LLM service to:
    - Enable real-time streaming to the chat interface
    - Track the active thread for context
    - Adjust tool availability based on chat mode
    """
    try:
        from app.llm_service.session_state_manager import get_session_state_manager

        session_manager = get_session_state_manager()

        # Build updates dict with fields to update
        updates = {
            "is_on_chat_page": True,
            "current_active_thread": thread_key,
            "mandate_path": mandate_path,
        }

        # Update session state to mark user as on chat page
        session_manager.update_session_state(
            user_id=uid,  # SessionStateManager uses user_id, not uid
            company_id=company_id,
            updates=updates
        )

        logger.info(
            f"[CHAT] LLM session updated: uid={uid} company={company_id} "
            f"thread={thread_key} is_on_chat_page=True"
        )

    except Exception as e:
        # Non-critical - log but don't fail orchestration
        logger.warning(f"[CHAT] Failed to update LLM session state: {e}")


# ============================================
# SESSION SELECTION HANDLER
# ============================================

async def handle_session_select(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.session_select WebSocket event.

    Loads message history for the selected session.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain thread_key, company_id

    Returns:
        Response dict
    """
    thread_key = payload.get("thread_key")
    company_id = payload.get("company_id")

    if not thread_key or not company_id:
        return {
            "type": "chat.session_select",
            "payload": {"success": False, "error": "Missing thread_key or company_id"}
        }

    logger.info(f"[CHAT] Session select: {thread_key}")

    try:
        chat_handlers = get_chat_handlers()

        # Load history for selected session
        history_result = await chat_handlers.load_history(
            uid=uid,
            company_id=company_id,
            space_code=company_id,
            thread_key=thread_key,
            mode="chats"
        )

        # Broadcast history loaded
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.HISTORY_LOADED,
            "payload": {
                "success": True,
                "thread_key": thread_key,
                "messages": history_result.get("messages", []),
                "total": history_result.get("total", 0),
            }
        })

        return {
            "type": "chat.session_select",
            "payload": {"success": True, "thread_key": thread_key}
        }

    except Exception as e:
        logger.error(f"[CHAT] Session select error: {e}")
        return {
            "type": "chat.session_select",
            "payload": {"success": False, "error": str(e)}
        }


# ============================================
# SESSION CRUD HANDLERS
# ============================================

async def handle_session_create(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.session_create WebSocket event.
    """
    company_id = payload.get("company_id")
    chat_mode = payload.get("chat_mode", DEFAULT_CHAT_MODE)
    thread_name = payload.get("thread_name")

    if not company_id:
        return {
            "type": "chat.session_create",
            "payload": {"success": False, "error": "Missing company_id"}
        }

    chat_handlers = get_chat_handlers()

    result = await chat_handlers.create_session(
        uid=uid,
        company_id=company_id,
        space_code=company_id,
        chat_mode=chat_mode,
        thread_name=thread_name,
    )

    if result.get("success"):
        # Broadcast new session to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.SESSIONS_LIST,
            "payload": {
                "action": "created",
                "session": result.get("session"),
            }
        })

    return {
        "type": "chat.session_create",
        "payload": result
    }


async def handle_session_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.session_delete WebSocket event.
    """
    thread_key = payload.get("thread_key")
    company_id = payload.get("company_id")

    if not thread_key or not company_id:
        return {
            "type": "chat.session_delete",
            "payload": {"success": False, "error": "Missing thread_key or company_id"}
        }

    chat_handlers = get_chat_handlers()

    result = await chat_handlers.delete_session(
        uid=uid,
        company_id=company_id,
        space_code=company_id,
        thread_key=thread_key,
    )

    if result.get("success"):
        # Broadcast deletion to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.SESSIONS_LIST,
            "payload": {
                "action": "deleted",
                "thread_key": thread_key,
            }
        })

    return {
        "type": "chat.session_delete",
        "payload": result
    }


async def handle_session_rename(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.session_rename WebSocket event.
    """
    thread_key = payload.get("thread_key")
    new_name = payload.get("new_name")
    company_id = payload.get("company_id")

    if not thread_key or not new_name or not company_id:
        return {
            "type": "chat.session_rename",
            "payload": {"success": False, "error": "Missing required fields"}
        }

    chat_handlers = get_chat_handlers()

    result = await chat_handlers.rename_session(
        uid=uid,
        company_id=company_id,
        space_code=company_id,
        thread_key=thread_key,
        new_name=new_name,
    )

    if result.get("success"):
        # Broadcast rename to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.SESSIONS_LIST,
            "payload": {
                "action": "renamed",
                "thread_key": thread_key,
                "new_name": new_name,
            }
        })

    return {
        "type": "chat.session_rename",
        "payload": result
    }


# ============================================
# CHAT MODE HANDLERS
# ============================================

# Available chat modes
CHAT_MODES = {
    "general_chat": {
        "name": "General Chat",
        "description": "General purpose assistant",
        "tools": ["search", "analyze", "report"],
    },
    "onboarding_chat": {
        "name": "Onboarding Assistant",
        "description": "Guided setup and configuration",
        "tools": ["setup", "configure", "verify"],
    },
    "ap_chat": {
        "name": "Accounts Payable",
        "description": "Invoice processing and payments",
        "tools": ["invoice", "payment", "vendor"],
    },
    "ar_chat": {
        "name": "Accounts Receivable",
        "description": "Customer invoicing and collections",
        "tools": ["customer", "invoice", "collection"],
    },
    "banking_chat": {
        "name": "Banking",
        "description": "Bank reconciliation and transactions",
        "tools": ["reconcile", "transaction", "balance"],
    },
}


async def handle_mode_change(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.mode_change WebSocket event.

    Changes the chat mode which affects:
    - Available tools for the LLM
    - System prompt context
    - UI presentation

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain mode, company_id

    Returns:
        Response dict with new mode info
    """
    new_mode = payload.get("mode")
    company_id = payload.get("company_id")
    thread_key = payload.get("thread_key")

    if not new_mode or not company_id:
        return {
            "type": "chat.mode_change",
            "payload": {"success": False, "error": "Missing mode or company_id"}
        }

    # Validate mode
    if new_mode not in CHAT_MODES:
        return {
            "type": "chat.mode_change",
            "payload": {
                "success": False,
                "error": f"Invalid mode: {new_mode}",
                "available_modes": list(CHAT_MODES.keys())
            }
        }

    logger.info(f"[CHAT] Mode change: {new_mode} for uid={uid}")

    try:
        # Update LLM session with new mode
        from app.llm_service.session_state_manager import get_session_state_manager

        session_manager = get_session_state_manager()

        # Build updates dict
        updates = {
            "chat_mode": new_mode,
            "current_active_thread": thread_key,
        }

        session_manager.update_session_state(
            user_id=uid,  # SessionStateManager uses user_id, not uid
            company_id=company_id,
            updates=updates
        )

        # Broadcast mode change confirmation
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.MODE_CHANGED,
            "payload": {
                "success": True,
                "mode": new_mode,
                "mode_info": CHAT_MODES[new_mode],
                "thread_key": thread_key,
            }
        })

        return {
            "type": "chat.mode_change",
            "payload": {
                "success": True,
                "mode": new_mode,
                "mode_info": CHAT_MODES[new_mode],
            }
        }

    except Exception as e:
        logger.error(f"[CHAT] Mode change error: {e}")
        return {
            "type": "chat.mode_change",
            "payload": {"success": False, "error": str(e)}
        }


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "handle_orchestrate_init",
    "handle_session_select",
    "handle_session_create",
    "handle_session_delete",
    "handle_session_rename",
    "handle_mode_change",
    "CHAT_MODES",
]
