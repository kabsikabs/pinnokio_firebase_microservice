"""
Dashboard Page Handlers
=======================

Handler suite for the Dashboard page migration.
Orchestration, tasks, and approval handlers are now in app.wrappers/.

Current modules in this directory:
    - handlers.py: RPC endpoints (DASHBOARD.full_data, etc.)
    - balance_handlers.py: Balance/top-up handlers (REAL IMPLEMENTATION)
    - providers/: Component-specific data fetchers

WebSocket Events Handled (via wrappers):
    - dashboard.orchestrate_init: Initial dashboard load after auth
    - dashboard.company_change: Switch company context
    - dashboard.refresh: Manual refresh
    - balance.top_up: Top-up balance
    - balance.refresh: Refresh balance

For orchestration, approval, and task handlers, import from app.wrappers:
    from app.wrappers import (
        handle_orchestrate_init,
        handle_company_change,
        handle_approval_list,
        handle_task_list,
    )

Usage:
    from app.frontend.pages.dashboard import (
        DashboardHandlers,
        BalanceHandlers,
        get_account_balance_data,
    )

Implementation Status: 85% complete
"""

# RPC Handlers
from .handlers import (
    DashboardHandlers,
    get_dashboard_handlers,
)

# Balance Handlers - REAL IMPLEMENTATION (wrappers re-exports from here)
from .balance_handlers import (
    BalanceHandlers,
    get_balance_handlers,
    handle_top_up,
    handle_refresh_balance,
    handle_stripe_callback,
)

# Data Providers
from .providers import (
    get_account_balance_data,
)

# Re-export orchestration/approval/task handlers from wrappers for backward compatibility
from ....wrappers.dashboard_orchestration_handlers import (
    # State managers
    OrchestrationStateManager,
    get_state_manager,
    # Main handlers
    handle_orchestrate_init,
    handle_company_change,
    handle_refresh,
    handle_switch_account,
    # Utility
    transform_company_data_to_info,
)

from ....wrappers.approval_handlers import (
    ApprovalHandlers,
    get_approval_handlers,
    handle_approval_list,
    handle_send_router,
    handle_send_banker,
    handle_send_apbookeeper,
)

from ....wrappers.task_handlers import (
    TaskHandlers,
    get_task_handlers,
    handle_task_list,
    handle_task_execute,
    handle_task_toggle,
    handle_task_update,
)

# Alias for backward compatibility
process_post_authentication = handle_orchestrate_init

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

__all__ = [
    # RPC Handlers
    "DashboardHandlers",
    "get_dashboard_handlers",
    # Balance Handlers (REAL IMPLEMENTATION)
    "BalanceHandlers",
    "get_balance_handlers",
    "handle_top_up",
    "handle_refresh_balance",
    "handle_stripe_callback",
    # Data Providers
    "get_account_balance_data",
    # Orchestration (re-exported from wrappers)
    "DashboardOrchestrationHandlers",
    "OrchestrationStateManager",
    "get_state_manager",
    "handle_orchestrate_init",
    "handle_company_change",
    "handle_refresh",
    "handle_switch_account",
    "process_post_authentication",
    "transform_company_data_to_info",
    # Approval (re-exported from wrappers)
    "ApprovalHandlers",
    "get_approval_handlers",
    "handle_approval_list",
    "handle_send_router",
    "handle_send_banker",
    "handle_send_apbookeeper",
    # Tasks (re-exported from wrappers)
    "TaskHandlers",
    "get_task_handlers",
    "handle_task_list",
    "handle_task_execute",
    "handle_task_toggle",
    "handle_task_update",
]
