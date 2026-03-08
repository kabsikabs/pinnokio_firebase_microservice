"""
Balance Handlers - Wrapper for backward compatibility
======================================================

Re-exports balance handlers from the new frontend location.

New imports should use:
    from app.frontend.pages.dashboard import (
        BalanceHandlers,
        get_balance_handlers,
        handle_top_up,
        handle_refresh_balance,
        handle_stripe_callback,
    )
"""

from ..frontend.pages.dashboard.balance_handlers import (
    BalanceHandlers,
    get_balance_handlers,
    handle_top_up,
    handle_refresh_balance,
    handle_stripe_callback,
)

__all__ = [
    "BalanceHandlers",
    "get_balance_handlers",
    "handle_top_up",
    "handle_refresh_balance",
    "handle_stripe_callback",
]
