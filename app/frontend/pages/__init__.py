"""
Frontend Pages - Page-Specific Handlers
=======================================

Each subdirectory contains handlers specific to a frontend page.

Structure:
    pages/
    ├── dashboard/     # Dashboard page - MIGRATED (85%)
    ├── chat/          # Chat page - TODO
    ├── invoices/      # Invoices page - TODO
    ├── expenses/      # Expenses page - TODO
    ├── banking/       # Banking page - TODO
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

__all__ = [
    # Dashboard
    "DashboardHandlers",
    "DashboardOrchestrationHandlers",
    "ApprovalHandlers",
    "TaskHandlers",
    "process_post_authentication",
    "handle_company_change",
    "handle_switch_account",
]
