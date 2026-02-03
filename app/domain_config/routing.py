"""
Routing Domain Configuration.

Defines action configurations and status mappings specific to the Router module.

Actions:
- process: Optimistic - items move to in_process immediately
- stop: Pessimistic - wait for backend confirmation
- delete: Pessimistic - wait for backend confirmation
- restart: Pessimistic - wait for backend confirmation
"""

from typing import Dict
from .base import BaseDomainConfig, ActionConfig, ActionType


class RoutingDomainConfig(BaseDomainConfig):
    """Configuration for the Routing domain."""

    DOMAIN = "routing"

    # List name mappings (must match cache keys from drive_cache_handlers)
    # Note: The cache uses "to_process", not "unprocessed"
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
            success_status="routed",        # On completion → processed list
            error_status="error",           # On error → to_process list
            ws_event_started="routing.processing_started",
            ws_event_completed="routing.item_update",
            ws_event_error="routing.error",
        ),
        "stop": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="stopping",      # Items stay in in_process with "stopping" status
            success_status="stopped",       # On completion → to_process list
            error_status="error",
            ws_event_started="routing.stop_started",
            ws_event_completed="routing.stopped",
            ws_event_error="routing.error",
        ),
        "delete": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status=None,            # No status change, items are removed
            success_status=None,
            error_status=None,
            ws_event_started="routing.delete_started",
            ws_event_completed="routing.deleted",
            ws_event_error="routing.error",
        ),
        "restart": ActionConfig(
            type=ActionType.PESSIMISTIC,
            initial_status="to_process",    # Items reset to to_process
            success_status="to_process",
            error_status="error",
            ws_event_started="routing.restart_started",
            ws_event_completed="routing.restarted",
            ws_event_error="routing.error",
        ),
    }

    # Domain-specific status to list overrides
    # (These override the centralized StatusNormalizer mappings if needed)
    STATUS_TO_LIST_OVERRIDE: Dict[str, str] = {
        # "routed" already maps to "processed" via StatusNormalizer
        # Add any routing-specific overrides here if needed
    }
