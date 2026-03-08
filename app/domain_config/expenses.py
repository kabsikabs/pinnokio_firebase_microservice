"""
Expenses (Notes de Frais) Domain Configuration.

Defines action configurations and status mappings specific to the Expenses module.

Actions:
- close: Optimistic - expense moves to processed list immediately
- reopen: Optimistic - expense moves back to to_process list
- update: Optimistic - expense data updated in place
- delete: Optimistic - expense removed from list

Status Flow:
    to_process -> in_process -> processed
              <- reopen <-
"""

from typing import Dict
from .base import BaseDomainConfig, ActionConfig, ActionType


class ExpensesDomainConfig(BaseDomainConfig):
    """Configuration for the Expenses (Notes de Frais) domain."""

    DOMAIN = "expenses"

    # List name mappings — NORMALIZED: same keys as routing/invoices/bank
    # Cache format: {to_process: [...], in_process: [...], pending: [...], processed: [...], metrics: {...}}
    LIST_NAMES = {
        "to_process": "to_process",
        "in_process": "in_process",
        "pending": "pending",
        "processed": "processed",
    }

    # Action configurations
    ACTIONS: Dict[str, ActionConfig] = {
        "close": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status="processed",         # Items go to processed list
            success_status="processed",
            error_status="to_process",           # On error -> back to to_process
            ws_event_started="expenses.close_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
        "reopen": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status="to_process",         # Items go back to to_process list
            success_status="to_process",
            error_status="processed",            # On error -> stay processed
            ws_event_started="expenses.reopen_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
        "update": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status=None,            # Status doesn't change on update
            success_status=None,
            error_status=None,
            ws_event_started="expenses.update_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
        "delete": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status=None,            # Item removed from list
            success_status=None,
            error_status=None,
            ws_event_started="expenses.delete_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
        "process": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status="in_process",         # Items go to in_process list
            success_status="processed",          # On completion -> processed
            error_status="to_process",           # On error -> back to to_process
            ws_event_started="expenses.processing_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
    }

    # Domain-specific status to list overrides
    # Maps normalized status -> category (which maps to list via LIST_NAMES)
    STATUS_TO_LIST_OVERRIDE: Dict[str, str] = {
        # Expenses-specific status mappings
        "to_process": "to_process",
        "open": "to_process",           # Legacy value
        "draft": "to_process",
        "running": "in_process",        # Legacy value
        "in_process": "in_process",
        "processing": "in_process",
        "pending": "pending",
        "close": "processed",           # Legacy Firebase value
        "closed": "processed",          # Legacy Firebase value
        "completed": "processed",
        "processed": "processed",
    }

    @classmethod
    def get_list_for_status(cls, status: str) -> str:
        """
        Get the list name for a given status.
        Overridden to handle expenses-specific status values.

        Args:
            status: Status string (to_process, running, close, etc.)

        Returns:
            List name (to_process, in_process, pending, processed)
        """
        # Normalize status to lowercase
        status_lower = status.lower() if status else "to_process"

        # Check domain-specific override first
        if status_lower in cls.STATUS_TO_LIST_OVERRIDE:
            category = cls.STATUS_TO_LIST_OVERRIDE[status_lower]
            return cls.LIST_NAMES.get(category, "to_process")

        # Default to to_process list
        return "to_process"
