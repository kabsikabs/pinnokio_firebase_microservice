"""
Frontend Core - Re-exports from wrappers (DEPRECATED)
======================================================

This module is maintained for backward compatibility.
All implementations have been moved to app/wrappers/.

The frontend/core directory now only contains this __init__.py file
which re-exports from wrappers/ for backward compatibility.

New code should import directly from app.wrappers:
    from app.wrappers import (
        handle_firebase_token,
        get_page_state_manager,
        get_pending_action_manager,
    )

Migration Path:
    OLD: from app.frontend.core import handle_firebase_token
    NEW: from app.wrappers import handle_firebase_token
"""

# Re-export from wrappers for backward compatibility
from ...wrappers.auth_handlers import (
    AuthenticationError,
    handle_firebase_token,
    get_session,
    update_session_activity,
    invalidate_session,
)

from ...wrappers.page_state_manager import (
    PageStateManager,
    get_page_state_manager,
)

from ...wrappers.pending_action_manager import (
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
