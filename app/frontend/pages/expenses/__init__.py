"""
Expenses Page - Notes de Frais
==============================

Handlers and orchestration for the Expenses page.

Pattern: 3-Level Cache Architecture
    - Level 1: user:{uid}:profile
    - Level 2: company:{uid}:{cid}:context (mandate_path, client_uuid, etc.)
    - Level 3: business:{uid}:{cid}:expenses (open, running, closed)

Frontend Events (WebSocket):
    - expenses.orchestrate_init -> Initialize page data loading
    - expenses.full_data -> Complete expenses data received
    - expenses.close -> Close an expense
    - expenses.reopen -> Reopen a closed expense
    - expenses.update -> Update expense fields
    - expenses.delete -> Delete an expense
    - expenses.refresh -> Refresh expenses data
    - expenses.error -> Expenses-specific error

Usage:
    from app.frontend.pages.expenses import (
        get_expenses_handlers,
        handle_expenses_orchestrate_init,
        handle_expenses_refresh,
        handle_expenses_close,
        handle_expenses_reopen,
        handle_expenses_update,
        handle_expenses_delete,
    )
"""

from .handlers import get_expenses_handlers, ExpensesHandlers
from .orchestration import (
    handle_expenses_orchestrate_init,
    handle_expenses_refresh,
    handle_expenses_close,
    handle_expenses_reopen,
    handle_expenses_update,
    handle_expenses_delete,
)

__all__ = [
    # Handlers
    "get_expenses_handlers",
    "ExpensesHandlers",
    # Orchestration handlers
    "handle_expenses_orchestrate_init",
    "handle_expenses_refresh",
    "handle_expenses_close",
    "handle_expenses_reopen",
    "handle_expenses_update",
    "handle_expenses_delete",
]
