"""
Invoices (APbookeeper) Domain Configuration.

Defines action configurations and status mappings specific to the APbookeeper module.

Actions:
- process: Optimistic - items move to in_process immediately
- stop: Pessimistic - wait for backend confirmation
- delete: Pessimistic - wait for backend confirmation
- restart: Pessimistic - wait for backend confirmation
- approve: Pessimistic - wait for backend confirmation
"""

from typing import Dict
from .base import BaseDomainConfig, ActionConfig, ActionType


class InvoicesDomainConfig(BaseDomainConfig):
    """Configuration for the Invoices (APbookeeper) domain."""

    DOMAIN = "invoices"

    # List name mappings (universal)
    LIST_NAMES = {
        "to_process": "to_process",
        "in_process": "in_process",
        "pending": "pending",
        "processed": "processed",
    }

    # Action configurations
    ACTIONS: Dict[str, ActionConfig] = {
        "process": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status="in_queue",      # Items go to in_process list
            success_status="completed",     # On completion → processed list
            error_status="error",           # On error → to_do list
            ws_event_started="invoices.processing_started",
            ws_event_completed="invoices.item_update",
            ws_event_error="invoices.error",
        ),
        "stop": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="stopping",
            success_status="stopped",       # On completion → to_do list
            error_status="error",
            ws_event_started="invoices.stop_started",
            ws_event_completed="invoices.stopped",
            ws_event_error="invoices.error",
        ),
        "delete": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status=None,
            success_status=None,
            error_status=None,
            ws_event_started="invoices.delete_started",
            ws_event_completed="invoices.deleted",
            ws_event_error="invoices.error",
        ),
        "restart": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="to_process",
            success_status="to_process",
            error_status="error",
            ws_event_started="invoices.restart_started",
            ws_event_completed="invoices.restarted",
            ws_event_error="invoices.error",
        ),
        "approve": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="pending",       # Items waiting for approval
            success_status="completed",     # After approval → processed
            error_status="error",
            ws_event_started="invoices.approval_started",
            ws_event_completed="invoices.approved",
            ws_event_error="invoices.error",
        ),
    }

    # Domain-specific status to list overrides
    STATUS_TO_LIST_OVERRIDE: Dict[str, str] = {
        # Invoices-specific overrides if needed
    }
