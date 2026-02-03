"""
COA (Chart of Accounts) Page Handlers
=====================================

Backend handlers for the COA page migration from Reflex to Next.js.

Usage:
    from app.frontend.pages.coa import get_coa_handlers, COA_EVENT_HANDLERS

    # Get handlers singleton
    handlers = get_coa_handlers()
    result = await handlers.load_accounts(uid, company_id, mandate_path)

    # Event routing
    handler = COA_EVENT_HANDLERS.get("coa.orchestrate_init")
    await handler(uid, session_id, payload)
"""

from .handlers import COAHandlers, get_coa_handlers
from .orchestration import (
    handle_orchestrate_init,
    handle_load_accounts,
    handle_load_functions,
    handle_save_changes,
    handle_sync_erp,
    handle_toggle_function,
    handle_create_function,
    handle_update_function,
    handle_delete_function,
    COA_EVENT_HANDLERS,
)

__all__ = [
    # Handlers
    "COAHandlers",
    "get_coa_handlers",
    # Orchestration
    "handle_orchestrate_init",
    "handle_load_accounts",
    "handle_load_functions",
    "handle_save_changes",
    "handle_sync_erp",
    "handle_toggle_function",
    "handle_create_function",
    "handle_update_function",
    "handle_delete_function",
    # Event map
    "COA_EVENT_HANDLERS",
]
