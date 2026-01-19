"""
Frontend Core - Shared Utilities
================================

Centralized utilities shared across all frontend pages.
These handle authentication, page state recovery, and redirect flows.

Modules:
    - auth_handlers: Firebase token verification & session management
    - page_state_manager: Page state caching for fast refresh recovery
    - pending_action_manager: OAuth/payment redirect state management

Usage:
    from app.frontend.core import (
        handle_firebase_token,
        get_page_state_manager,
        get_pending_action_manager,
    )
"""

from .auth_handlers import (
    AuthenticationError,
    handle_firebase_token,
    get_session,
    update_session_activity,
    invalidate_session,
)

from .page_state_manager import (
    PageStateManager,
    get_page_state_manager,
)

from .pending_action_manager import (
    PendingActionManager,
    get_pending_action_manager,
)

__all__ = [
    # Auth
    "AuthenticationError",
    "handle_firebase_token",
    "get_session",
    "update_session_activity",
    "invalidate_session",
    # Page State
    "PageStateManager",
    "get_page_state_manager",
    # Pending Actions
    "PendingActionManager",
    "get_pending_action_manager",
]
