"""
Frontend Pages - Page-Specific Handlers
=======================================

Each subdirectory contains handlers specific to a frontend page.

Structure:
    pages/
    ├── dashboard/     # Dashboard page - MIGRATED (85%)
    ├── chat/          # Chat page - MIGRATED
    ├── invoices/      # Invoices page - MIGRATED
    ├── expenses/      # Expenses page - MIGRATED
    ├── banking/       # Banking page - MIGRATED
    ├── routing/       # Routing page - MIGRATED
    ├── coa/           # Chart of Accounts page - MIGRATED
    ├── company_settings/  # Company Settings page - MIGRATED
    └── hr/            # HR page - TODO

Migration Pattern:
    For each new page:
    1. Create subdirectory: pages/new_page/
    2. Add handlers.py: RPC endpoints (NEW_PAGE.*)
    3. Add orchestration.py: Post-auth data loading
    4. Add providers/: Component-specific data fetchers
    5. Update this __init__.py with exports
"""

# Dashboard - Migrated
from .dashboard import (
    DashboardHandlers,
    DashboardOrchestrationHandlers,
    ApprovalHandlers,
    TaskHandlers,
    process_post_authentication,
    handle_company_change,
    handle_switch_account,
)

# Expenses - Migrated
from .expenses import (
    get_expenses_handlers,
    ExpensesHandlers,
    handle_expenses_orchestrate_init,
    handle_expenses_refresh,
    handle_expenses_close,
    handle_expenses_reopen,
    handle_expenses_update,
    handle_expenses_delete,
)

__all__ = [
    # Dashboard
    "DashboardHandlers",
    "DashboardOrchestrationHandlers",
    "ApprovalHandlers",
    "TaskHandlers",
    "process_post_authentication",
    "handle_company_change",
    "handle_switch_account",
    # Expenses
    "get_expenses_handlers",
    "ExpensesHandlers",
    "handle_expenses_orchestrate_init",
    "handle_expenses_refresh",
    "handle_expenses_close",
    "handle_expenses_reopen",
    "handle_expenses_update",
    "handle_expenses_delete",
]
