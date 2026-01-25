"""
APBookkeeper/Invoices Page Handlers
====================================

RPC handlers and orchestration for the APBookkeeper (Invoices) page.

This module manages invoice processing through stages:
- To Do: Invoices awaiting processing
- In Process: Invoices currently being processed
- Pending: Invoices awaiting approval/action
- Processed: Successfully processed invoices (final state)

Events:
- INVOICES.orchestrate_init: Initialize page data
- INVOICES.full_data: Complete page data response
- INVOICES.list: List invoices by category
- INVOICES.process: Process selected invoices
- INVOICES.stop: Stop processing
- INVOICES.delete: Delete processed invoices
- INVOICES.refresh: Refresh data from source

NOTE APBookkeeper Specificities:
- Uses Firebase journal as data source
- check_job_status via job_id (not file_id like Router)
- function_name filter: "APbookeeper"
- 4 statuses: to_do, in_process, pending, processed
"""

from .handlers import InvoicesHandlers, get_invoices_handlers
from .orchestration import (
    handle_invoices_orchestrate_init,
    handle_invoices_refresh,
    handle_invoices_process,
    handle_invoices_stop,
    handle_invoices_delete,
    handle_invoices_restart,
    handle_invoices_instructions_save,
)

__all__ = [
    "InvoicesHandlers",
    "get_invoices_handlers",
    "handle_invoices_orchestrate_init",
    "handle_invoices_refresh",
    "handle_invoices_process",
    "handle_invoices_stop",
    "handle_invoices_delete",
    "handle_invoices_restart",
    "handle_invoices_instructions_save",
]
