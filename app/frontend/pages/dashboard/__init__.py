"""
Dashboard Page Handlers
=======================

Complete handler suite for the Dashboard page migration.

Modules:
    - handlers.py: RPC endpoints (DASHBOARD.full_data, etc.)
    - orchestration.py: Post-authentication data orchestration
    - approval_handlers.py: Approval workflow (router, banker, apbookeeper)
    - task_handlers.py: Task management (list, execute, toggle)
    - providers/: Component-specific data fetchers

WebSocket Events Handled:
    - dashboard.orchestrate_init: Initial dashboard load after auth
    - dashboard.company_change: Switch company context
    - dashboard.refresh: Manual refresh
    - approval.*: Approval operations
    - task.*: Task operations

Usage:
    from app.frontend.pages.dashboard import (
        process_post_authentication,
        handle_company_change,
        DashboardHandlers,
    )

Implementation Status: 85% complete
"""

# Orchestration - Main entry points
from .orchestration import (
    # State managers
    UserSessionStateManager,
    OrchestrationStateManager,
    get_state_manager,
    get_user_session_manager,
    # Main handlers
    handle_orchestrate_init,
    handle_company_change,
    handle_refresh,
    handle_switch_account,
    # Utility
    transform_company_data_to_info,
)

# Alias for backward compatibility with wrappers/__init__.py
process_post_authentication = handle_orchestrate_init

# RPC Handlers
from .handlers import (
    DashboardHandlers,
    get_dashboard_handlers,
)

# Approval Handlers
from .approval_handlers import (
    ApprovalHandlers,
    get_approval_handlers,
    handle_approval_list,
    handle_send_router,
    handle_send_banker,
    handle_send_apbookeeper,
)

# Task Handlers
from .task_handlers import (
    TaskHandlers,
    get_task_handlers,
    handle_task_list,
    handle_task_execute,
    handle_task_toggle,
    handle_task_update,
)

# Balance Handlers
from .balance_handlers import (
    BalanceHandlers,
    get_balance_handlers,
    handle_top_up,
    handle_refresh_balance,
    handle_stripe_callback,
)

# Orchestration Handlers wrapper class for clean imports
class DashboardOrchestrationHandlers:
    """Wrapper class for dashboard orchestration handlers."""

    handle_orchestrate_init = staticmethod(handle_orchestrate_init)
    handle_company_change = staticmethod(handle_company_change)
    handle_refresh = staticmethod(handle_refresh)
    handle_switch_account = staticmethod(handle_switch_account)

    @staticmethod
    def get_state_manager():
        return get_state_manager()

    @staticmethod
    def get_user_session_manager():
        return get_user_session_manager()

__all__ = [
    # Orchestration
    "DashboardOrchestrationHandlers",
    "UserSessionStateManager",
    "OrchestrationStateManager",
    "get_state_manager",
    "get_user_session_manager",
    "handle_orchestrate_init",
    "handle_company_change",
    "handle_refresh",
    "handle_switch_account",
    "process_post_authentication",
    "transform_company_data_to_info",
    # RPC Handlers
    "DashboardHandlers",
    "get_dashboard_handlers",
    # Approval
    "ApprovalHandlers",
    "get_approval_handlers",
    "handle_approval_list",
    "handle_send_router",
    "handle_send_banker",
    "handle_send_apbookeeper",
    # Tasks
    "TaskHandlers",
    "get_task_handlers",
    "handle_task_list",
    "handle_task_execute",
    "handle_task_toggle",
    "handle_task_update",
    # Balance
    "BalanceHandlers",
    "get_balance_handlers",
    "handle_top_up",
    "handle_refresh_balance",
    "handle_stripe_callback",
]
