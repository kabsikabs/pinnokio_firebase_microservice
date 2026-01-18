"""
Dashboard Page - Data Providers
===============================

This module provides data fetching for all dashboard components.

Component Mapping:
    | Python Module              | Frontend Component    | Path                                           |
    |---------------------------|-----------------------|------------------------------------------------|
    | account_balance_card.py   | AccountBalanceCard    | src/components/dashboard/account-balance-card.tsx |
    | storage_card.py           | StorageCard           | src/components/dashboard/storage-card.tsx      |
    | metrics_cards.py          | MetricsCards          | src/components/dashboard/metrics-cards.tsx     |
    | jobs_widget.py            | JobsWidget            | src/components/dashboard/jobs-widget.tsx       |
    | approvals_widget.py       | ApprovalsWidget       | src/components/dashboard/approvals-widget.tsx  |
    | tasks_widget.py           | TasksWidget           | src/components/dashboard/tasks-widget.tsx      |
    | expenses_table.py         | ExpensesTable         | src/components/dashboard/expenses-table.tsx    |
    | activity_feed.py          | ActivityFeed          | src/components/dashboard/activity-feed.tsx     |

Usage:
    from app.frontend.dashboard import get_account_balance_data

    data = await get_account_balance_data(user_id, company_id, mandate_path)
"""

from .account_balance_card import get_account_balance_data

__all__ = [
    "get_account_balance_data",
]
