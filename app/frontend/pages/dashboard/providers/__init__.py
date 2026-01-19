"""
Dashboard Data Providers
========================

Component-specific data fetching functions for dashboard widgets.

Each provider corresponds to a frontend component:
    | Python Module              | Frontend Component    |
    |---------------------------|-----------------------|
    | account_balance_card.py   | AccountBalanceCard    |

Usage:
    from app.frontend.pages.dashboard.providers import get_account_balance_data
"""

from .account_balance_card import get_account_balance_data

__all__ = [
    "get_account_balance_data",
]
