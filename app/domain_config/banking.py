"""
Banking (Banker) Domain Configuration.

Defines action configurations and status mappings specific to the Banker module.

Actions:
- process: Optimistic - items move to in_process immediately
- stop: Pessimistic - wait for backend confirmation
- delete: Pessimistic - wait for backend confirmation
- restart: Pessimistic - wait for backend confirmation
- match: Pessimistic - wait for backend confirmation (bank reconciliation)
"""

from typing import Dict
from .base import BaseDomainConfig, ActionConfig, ActionType


class BankingDomainConfig(BaseDomainConfig):
    """Configuration for the Banking (Banker) domain."""

    DOMAIN = "banking"

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
            success_status="completed",     # On completion → matched list
            error_status="error",           # On error → to_process list
            ws_event_started="banking.processing_started",
            ws_event_completed="banking.item_update",
            ws_event_error="banking.error",
        ),
        "stop": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="stopping",
            success_status="stopped",       # On completion → to_process list
            error_status="error",
            ws_event_started="banking.stop_started",
            ws_event_completed="banking.stopped",
            ws_event_error="banking.error",
        ),
        "delete": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status=None,
            success_status=None,
            error_status=None,
            ws_event_started="banking.delete_started",
            ws_event_completed="banking.deleted",
            ws_event_error="banking.error",
        ),
        "restart": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="to_process",
            success_status="to_process",
            error_status="error",
            ws_event_started="banking.restart_started",
            ws_event_completed="banking.restarted",
            ws_event_error="banking.error",
        ),
        "match": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="pending",       # Items awaiting matching confirmation
            success_status="completed",     # After matching → matched list
            error_status="error",
            ws_event_started="banking.match_started",
            ws_event_completed="banking.matched",
            ws_event_error="banking.error",
        ),
    }

    # Domain-specific status to list overrides
    STATUS_TO_LIST_OVERRIDE: Dict[str, str] = {
        # Banking-specific overrides if needed
    }
