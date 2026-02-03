"""
Expenses (Notes de Frais) Domain Configuration.

Defines action configurations and status mappings specific to the Expenses module.

Actions:
- close: Optimistic - expense moves to closed list immediately
- reopen: Optimistic - expense moves back to open list
- update: Optimistic - expense data updated in place
- delete: Optimistic - expense removed from list

Status Flow:
    to_process (open) -> running -> close (closed)
                     <- reopen <-
"""

from typing import Dict
from .base import BaseDomainConfig, ActionConfig, ActionType


class ExpensesDomainConfig(BaseDomainConfig):
    """Configuration for the Expenses (Notes de Frais) domain."""

    DOMAIN = "expenses"

    # List name mappings (matches the cache structure from expenses/handlers.py)
    # Cache format: {open: [...], running: [...], closed: [...], metrics: {...}}
    LIST_NAMES = {
        "to_process": "open",       # to_process status -> open list
        "in_process": "running",    # running status -> running list
        "pending": "running",       # pending -> running list (same as in_process)
        "processed": "closed",      # close/completed status -> closed list
    }

    # Action configurations
    ACTIONS: Dict[str, ActionConfig] = {
        "close": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status="close",         # Items go to closed list
            success_status="close",
            error_status="to_process",      # On error -> back to open
            ws_event_started="expenses.close_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
        "reopen": ActionConfig(
            type=ActionType.OPTIMISTIC,
            initial_status="to_process",    # Items go back to open list
            success_status="to_process",
            error_status="close",           # On error -> stay closed
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
            initial_status="running",       # Items go to running list
            success_status="close",         # On completion -> closed
            error_status="to_process",      # On error -> back to open
            ws_event_started="expenses.processing_started",
            ws_event_completed="expenses.item_update",
            ws_event_error="expenses.error",
        ),
    }

    # Domain-specific status to list overrides
    # Maps normalized status -> category (which maps to list via LIST_NAMES)
    STATUS_TO_LIST_OVERRIDE: Dict[str, str] = {
        # Expenses-specific status mappings
        "to_process": "to_process",     # -> open list
        "open": "to_process",           # -> open list
        "draft": "to_process",          # -> open list
        "running": "in_process",        # -> running list
        "in_process": "in_process",     # -> running list
        "processing": "in_process",     # -> running list
        "close": "processed",           # -> closed list
        "closed": "processed",          # -> closed list
        "completed": "processed",       # -> closed list
    }

    @classmethod
    def get_list_for_status(cls, status: str) -> str:
        """
        Get the list name for a given status.
        Overridden to handle expenses-specific status values.

        Args:
            status: Status string (to_process, running, close, etc.)

        Returns:
            List name (open, running, closed)
        """
        # Normalize status to lowercase
        status_lower = status.lower() if status else "to_process"

        # Check domain-specific override first
        if status_lower in cls.STATUS_TO_LIST_OVERRIDE:
            category = cls.STATUS_TO_LIST_OVERRIDE[status_lower]
            return cls.LIST_NAMES.get(category, "open")

        # Default to open list
        return "open"
