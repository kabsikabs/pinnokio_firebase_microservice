"""
Banking Page Handlers
=====================

RPC handlers and orchestration for the Banking/Transactions page.

This module manages bank transaction processing through stages:
- To Process: Transactions awaiting categorization
- In Process: Transactions currently being matched
- Pending: Transactions awaiting approval/action

Events:
- BANKING.orchestrate_init: Initialize page data
- BANKING.full_data: Complete page data response
- BANKING.list: List transactions by category
- BANKING.process: Process selected transactions
- BANKING.stop: Stop processing
- BANKING.delete: Delete transactions
- BANKING.instructions_save: Save transaction instructions

NOTE Banking Specificities:
- Only 3 statuses: to_process, in_process, pending (no completed/matched status)
- Bank account selector as primary filter
- Balance display with color coding (positive=green, negative=red)
- Batch management in in_process tab
"""

from .handlers import BankingHandlers, get_banking_handlers
from .orchestration import (
    handle_banking_orchestrate_init,
    handle_banking_refresh,
    handle_banking_process,
    handle_banking_stop,
    handle_banking_restart,
    handle_banking_delete,
    handle_banking_dismiss_match,
)

__all__ = [
    "BankingHandlers",
    "get_banking_handlers",
    "handle_banking_orchestrate_init",
    "handle_banking_refresh",
    "handle_banking_process",
    "handle_banking_stop",
    "handle_banking_restart",
    "handle_banking_delete",
    "handle_banking_dismiss_match",
]
