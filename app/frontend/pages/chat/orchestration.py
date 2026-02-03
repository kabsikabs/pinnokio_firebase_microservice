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
from app.wrappers.page_state_manager import get_page_state_manager

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
        # Step 1: Load chat sessions (only from 'chats' compartment)
        # Note: 'active_chats' compartment is no longer used (Phase 10 simplification)
        # ─────────────────────────────────────────────────
        logger.info(f"[CHAT] Step 1: Loading sessions for {company_id}")

        sessions_result = await chat_handlers.list_sessions(
            uid=uid,
            company_id=company_id,
            space_code=company_id,  # Usually same as company_id
            mode="chats",  # Only load from chats compartment
        )

        sessions = sessions_result.get("sessions", [])
        logger.info(f"[CHAT] Loaded {len(sessions)} sessions from chats compartment")

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

        # NOTE: is_on_chat_page est mis à jour par enter_chat() lors de la sélection
        # d'un thread (handle_session_select), ce qui évite les duplications et
        # garantit un thread_key toujours défini.

    except Exception as e:
        logger.error(f"[CHAT] Orchestration error: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.ERROR,
            "payload": {"error": str(e), "code": "ORCHESTRATION_ERROR"}
        })


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

    Loads message history for the selected session AND connects to the LLM context.

    ⚠️ IMPORTANT: This handler MUST call llm_manager.enter_chat() to:
    1. Initialize/retrieve the LLM session
    2. Mark user presence on the thread
    3. Create the Brain with history loaded from RTDB
    4. Activate listeners for onboarding-like modes

    Without enter_chat(), the messages are displayed but the LLM agent has no context.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain thread_key, company_id, optionally chat_mode

    Returns:
        Response dict
    """
    thread_key = payload.get("thread_key")
    company_id = payload.get("company_id")
    chat_mode = payload.get("chat_mode", DEFAULT_CHAT_MODE)

    if not thread_key or not company_id:
        return {
            "type": "chat.session_select",
            "payload": {"success": False, "error": "Missing thread_key or company_id"}
        }

    logger.info(f"[CHAT] Session select: {thread_key} (mode={chat_mode})")

    try:
        # ─────────────────────────────────────────────────
        # STEP 1: Connect to LLM context via enter_chat
        # This is CRITICAL - same as LLM.enter_chat in _resolve_method
        # ─────────────────────────────────────────────────
        from app.llm_service.llm_manager import get_llm_manager

        llm_manager = get_llm_manager()

        enter_result = await llm_manager.enter_chat(
            user_id=uid,
            collection_name=company_id,
            thread_key=thread_key,
            chat_mode=chat_mode,
        )

        if not enter_result.get("success", False):
            logger.warning(
                f"[CHAT] enter_chat returned non-success: {enter_result}. "
                f"Continuing with history load anyway."
            )

        logger.info(f"[CHAT] LLM context connected for thread={thread_key}")

        # ─────────────────────────────────────────────────
        # STEP 2: Load formatted history for frontend display
        # ─────────────────────────────────────────────────
        chat_handlers = get_chat_handlers()

        # Get mode from payload (defaults to "chats" for backwards compatibility)
        mode = payload.get("mode", "chats")

        history_result = await chat_handlers.load_history(
            uid=uid,
            company_id=company_id,
            space_code=company_id,
            thread_key=thread_key,
            mode=mode
        )

        # NOTE: job_data for specialized chat modes (apbookeeper, banker, router)
        # has been removed in Phase 10 simplification. The frontend no longer
        # uses this data as the specialized sidebars have been removed.

        # ─────────────────────────────────────────────────
        # STEP 3: Broadcast to frontend
        # ─────────────────────────────────────────────────
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.HISTORY_LOADED,
            "payload": {
                "success": True,
                "thread_key": thread_key,
                "messages": history_result.get("messages", []),
                "total": history_result.get("total", 0),
                "pending_card": history_result.get("pending_card"),
            }
        })

        return {
            "type": "chat.session_select",
            "payload": {"success": True, "thread_key": thread_key}
        }

    except Exception as e:
        logger.error(f"[CHAT] Session select error: {e}", exc_info=True)
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
# SESSION AUTO-NAMING HANDLER
# ============================================

async def handle_session_auto_name(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.session_auto_name WebSocket event.

    Automatically generates a name for a virgin chat session based on
    the first message content. Uses LLM for intelligent naming with
    heuristic fallback.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain thread_key, first_message, company_id

    Returns:
        Response dict with generated name
    """
    thread_key = payload.get("thread_key")
    first_message = payload.get("first_message")
    company_id = payload.get("company_id")
    use_llm = payload.get("use_llm", True)  # Default to LLM

    if not thread_key or not first_message or not company_id:
        return {
            "type": "chat.session_auto_name",
            "payload": {"success": False, "error": "Missing required fields"}
        }

    logger.info(f"[CHAT] Auto-naming session {thread_key} with LLM={use_llm}")

    try:
        chat_handlers = get_chat_handlers()

        if use_llm:
            result = await chat_handlers.auto_name_session_llm(
                uid=uid,
                company_id=company_id,
                space_code=company_id,
                thread_key=thread_key,
                first_message=first_message,
            )
        else:
            result = await chat_handlers.auto_name_session(
                uid=uid,
                company_id=company_id,
                space_code=company_id,
                thread_key=thread_key,
                first_message=first_message,
            )

        if result.get("success"):
            # Broadcast the renamed session
            await hub.broadcast(uid, {
                "type": WS_EVENTS.CHAT.SESSIONS_LIST,
                "payload": {
                    "action": "renamed",
                    "thread_key": thread_key,
                    "new_name": result.get("new_name"),
                    "method": result.get("method", "unknown"),
                }
            })

        return {
            "type": "chat.session_auto_name",
            "payload": result
        }

    except Exception as e:
        logger.error(f"[CHAT] Auto-naming error: {e}")
        return {
            "type": "chat.session_auto_name",
            "payload": {"success": False, "error": str(e)}
        }


# ============================================
# WORKFLOW CHECKLIST HANDLERS
# ============================================

async def handle_workflow_checklist_set(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.workflow_set WebSocket event.

    Sets the workflow checklist for the current chat session.
    This is typically called by the microservice when starting
    an onboarding or multi-step workflow.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain checklist data

    Returns:
        Response dict
    """
    checklist = payload.get("checklist", {})
    thread_key = payload.get("thread_key")
    company_id = payload.get("company_id")

    if not checklist:
        return {
            "type": "chat.workflow_set",
            "payload": {"success": False, "error": "Missing checklist data"}
        }

    logger.info(f"[CHAT] Setting workflow checklist with {checklist.get('total_steps', 0)} steps")

    try:
        # Broadcast the checklist to the frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.WORKFLOW_SET,
            "payload": {
                "success": True,
                "checklist": checklist,
                "thread_key": thread_key,
                "company_id": company_id,
            }
        })

        return {
            "type": "chat.workflow_set",
            "payload": {"success": True}
        }

    except Exception as e:
        logger.error(f"[CHAT] Workflow set error: {e}")
        return {
            "type": "chat.workflow_set",
            "payload": {"success": False, "error": str(e)}
        }


async def handle_workflow_step_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.workflow_step_update WebSocket event.

    Updates the status of a specific step in the workflow checklist.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain step_id, status

    Returns:
        Response dict
    """
    step_id = payload.get("step_id")
    status = payload.get("status")  # pending, in_progress, completed, error
    message = payload.get("message", "")
    timestamp = payload.get("timestamp")

    if not step_id or not status:
        return {
            "type": "chat.workflow_step_update",
            "payload": {"success": False, "error": "Missing step_id or status"}
        }

    logger.info(f"[CHAT] Workflow step update: {step_id} -> {status}")

    try:
        from datetime import datetime, timezone

        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()

        # Broadcast the step update to the frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.WORKFLOW_STEP_UPDATE,
            "payload": {
                "success": True,
                "step_id": step_id,
                "status": status,
                "message": message,
                "timestamp": timestamp,
            }
        })

        return {
            "type": "chat.workflow_step_update",
            "payload": {"success": True, "step_id": step_id, "status": status}
        }

    except Exception as e:
        logger.error(f"[CHAT] Workflow step update error: {e}")
        return {
            "type": "chat.workflow_step_update",
            "payload": {"success": False, "error": str(e)}
        }


# ============================================
# INTERACTIVE CARD HANDLERS
# ============================================

async def handle_card_clicked(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle chat.card_clicked WebSocket event.

    Bridge between Next.js frontend and send_card_response().
    Routes the card click to the LLM manager for processing.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain card_id, action, params, company_id, thread_key

    Returns:
        Response dict with result
    """
    card_id = payload.get("card_id")
    action = payload.get("action")
    params = payload.get("params", {})
    company_id = payload.get("company_id")
    thread_key = payload.get("thread_key")
    message_id = payload.get("message_id")

    # Validation
    if not card_id or not action:
        return {
            "type": "chat.card_clicked",
            "payload": {"success": False, "error": "Missing card_id or action"}
        }

    if not company_id or not thread_key:
        return {
            "type": "chat.card_clicked",
            "payload": {"success": False, "error": "Missing company_id or thread_key"}
        }

    logger.info(
        f"[CHAT] Card clicked: card={card_id} action={action} "
        f"thread={thread_key} message_id={message_id}"
    )

    try:
        from app.llm_service.llm_manager import get_llm_manager

        llm_manager = get_llm_manager()

        # Extract comment from params
        user_message = params.get("comment", "")

        # Call the existing send_card_response method
        result = await llm_manager.send_card_response(
            user_id=uid,
            collection_name=company_id,
            thread_key=thread_key,
            card_name=card_id,
            card_message_id=message_id or "",
            action=action,
            user_message=user_message,
            message_data=params,  # Pass all params for intermediation mode
        )

        # Broadcast result to frontend
        await hub.broadcast(uid, {
            "type": WS_EVENTS.CHAT.CARD_RECEIVED,  # Reuse existing event to clear card
            "payload": {
                "success": result.get("success", False),
                "action": action,
                "card_id": card_id,
                "responded": True,
            }
        })

        logger.info(f"[CHAT] Card response sent: success={result.get('success')}")

        return {
            "type": "chat.card_clicked",
            "payload": result
        }

    except Exception as e:
        logger.error(f"[CHAT] Card click error: {e}", exc_info=True)
        return {
            "type": "chat.card_clicked",
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
    "handle_session_auto_name",
    "handle_workflow_checklist_set",
    "handle_workflow_step_update",
    "handle_card_clicked",
    "CHAT_MODES",
]
