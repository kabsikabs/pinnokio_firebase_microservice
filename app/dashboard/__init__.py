"""
DEPRECATED - Dashboard Data Providers
=====================================

This module has been moved to: app/frontend/pages/dashboard/providers/

The new structure organizes backend data by frontend page/component:
    frontend/
    └── pages/
        └── dashboard/
            └── providers/
                ├── account_balance_card.py  # AccountBalanceCard
                └── ...

New usage:
    from app.frontend.pages.dashboard.providers import get_account_balance_data

    data = await get_account_balance_data(user_id, company_id, mandate_path)

See: app/frontend/README.md
"""

import warnings

warnings.warn(
    "app.dashboard is deprecated. Use app.frontend.pages.dashboard.providers instead.",
    DeprecationWarning,
    stacklevel=2
)

# Re-export from new location for backwards compatibility
from ..frontend.pages.dashboard.providers import get_account_balance_data

__all__ = [
    'get_account_balance_data',
]
