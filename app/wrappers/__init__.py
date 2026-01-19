"""
Wrappers Module - DEPRECATED - Use app.frontend instead
========================================================

This module is maintained for backward compatibility.
New code should import from app.frontend directly.

Migration Guide:
    OLD: from app.wrappers import handle_firebase_token
    NEW: from app.frontend.core import handle_firebase_token

    OLD: from app.wrappers import handle_orchestrate_init
    NEW: from app.frontend.pages.dashboard import handle_orchestrate_init

Structure (NEW):
    app/frontend/
    ├── core/                          # Shared utilities
    │   ├── auth_handlers.py
    │   ├── page_state_manager.py
    │   └── pending_action_manager.py
    └── pages/
        └── dashboard/
            ├── orchestration.py
            ├── approval_handlers.py
            └── task_handlers.py
"""

import warnings

# Re-export from new locations for backward compatibility
# These imports will still work but new code should use app.frontend

# Auth handlers (from app.frontend.core)
from .auth_handlers import (
    handle_firebase_token,
    get_session,
    update_session_activity,
    invalidate_session,
    AuthenticationError
)

# Dashboard orchestration (from app.frontend.pages.dashboard)
from .dashboard_orchestration_handlers import (
    handle_orchestrate_init,
    handle_company_change,
    handle_refresh,
    handle_switch_account,
    get_state_manager,
    get_user_session_manager,
    OrchestrationStateManager,
    UserSessionStateManager,
)

# Approval handlers
from .approval_handlers import (
    ApprovalHandlers,
    get_approval_handlers,
    handle_approval_list,
    handle_send_router,
    handle_send_banker,
    handle_send_apbookeeper,
)

# Task handlers
from .task_handlers import (
    TaskHandlers,
    get_task_handlers,
    handle_task_list,
    handle_task_execute,
    handle_task_toggle,
    handle_task_update,
)

# Balance handlers
from .balance_handlers import (
    BalanceHandlers,
    get_balance_handlers,
    handle_top_up,
    handle_refresh_balance,
    handle_stripe_callback,
)

# Page state manager
from .page_state_manager import (
    PageStateManager,
    get_page_state_manager,
)

# Pending action manager
from .pending_action_manager import (
    PendingActionManager,
    get_pending_action_manager,
)

# Alias for main.py compatibility
process_post_authentication = handle_orchestrate_init

__all__ = [
    # Auth handlers
    "handle_firebase_token",
    "get_session",
    "update_session_activity",
    "invalidate_session",
    "AuthenticationError",
    # Dashboard orchestration
    "handle_orchestrate_init",
    "handle_company_change",
    "handle_refresh",
    "handle_switch_account",
    "get_state_manager",
    "get_user_session_manager",
    "OrchestrationStateManager",
    "UserSessionStateManager",
    "process_post_authentication",
    # Approval handlers
    "ApprovalHandlers",
    "get_approval_handlers",
    "handle_approval_list",
    "handle_send_router",
    "handle_send_banker",
    "handle_send_apbookeeper",
    # Task handlers
    "TaskHandlers",
    "get_task_handlers",
    "handle_task_list",
    "handle_task_execute",
    "handle_task_toggle",
    "handle_task_update",
    # Balance handlers
    "BalanceHandlers",
    "get_balance_handlers",
    "handle_top_up",
    "handle_refresh_balance",
    "handle_stripe_callback",
    # Page state
    "PageStateManager",
    "get_page_state_manager",
    # Pending actions
    "PendingActionManager",
    "get_pending_action_manager",
]
