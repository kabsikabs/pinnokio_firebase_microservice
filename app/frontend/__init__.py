"""
Frontend Data Providers
=======================

This module organizes backend data fetching by frontend page/component.
Each subfolder corresponds to a page, and each file corresponds to a component.

Structure:
    frontend/
    ├── __init__.py
    ├── dashboard/                    # Dashboard page
    │   ├── __init__.py
    │   ├── account_balance_card.py  # AccountBalanceCard component
    │   ├── storage_card.py          # StorageCard component (TODO)
    │   ├── metrics_cards.py         # MetricsCards component (TODO)
    │   └── ...
    └── (other pages as needed)

Usage:
    from app.frontend.dashboard import get_account_balance_data

    data = await get_account_balance_data(user_id, company_id, mandate_path)
"""

from .dashboard import get_account_balance_data

__all__ = [
    "get_account_balance_data",
]
