"""
Chat Page Handlers
==================

RPC handlers and orchestration for the Chat page.

Usage:
    from app.frontend.pages.chat import (
        ChatHandlers,
        get_chat_handlers,
        handle_orchestrate_init,
        handle_session_select,
    )

Event Mapping:
    chat.orchestrate_init  -> handle_orchestrate_init()
    chat.session_select    -> handle_session_select()
    chat.session_create    -> handle_session_create()
    chat.session_delete    -> handle_session_delete()
    chat.session_rename    -> handle_session_rename()
"""

from .handlers import (
    ChatHandlers,
    get_chat_handlers,
)

from .orchestration import (
    handle_orchestrate_init,
    handle_session_select,
    handle_session_create,
    handle_session_delete,
    handle_session_rename,
    handle_mode_change,
    CHAT_MODES,
)

__all__ = [
    # Handler class
    "ChatHandlers",
    "get_chat_handlers",
    # Orchestration handlers
    "handle_orchestrate_init",
    "handle_session_select",
    "handle_session_create",
    "handle_session_delete",
    "handle_session_rename",
    "handle_mode_change",
    # Constants
    "CHAT_MODES",
]
