"""
Base Domain Configuration - Abstract base class for domain-specific configurations.

This module provides the foundation for domain-specific configurations that define:
- Action types (optimistic/pessimistic)
- Initial status assigned per action
- Status-to-list mappings
- WebSocket event types

Usage:
    from domain_config import get_domain_config

    config = get_domain_config("routing")
    action_type = config.get_action_type("process")  # → "optimistic"
    initial_status = config.get_initial_status("process")  # → "in_queue"
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Optional, List, Any
from dataclasses import dataclass


class ActionType(str, Enum):
    """Type of action behavior for UI updates."""
    OPTIMISTIC = "optimistic"   # Frontend updates immediately, rollback on error
    PESSIMISTIC = "pessimistic"  # Frontend waits for backend confirmation


@dataclass
class ActionConfig:
    """Configuration for a specific action."""
    type: ActionType
    initial_status: str
    success_status: Optional[str] = None
    error_status: str = "error"
    ws_event_started: Optional[str] = None
    ws_event_completed: Optional[str] = None
    ws_event_error: Optional[str] = None


@dataclass
class StatusChangeResult:
    """Result of a status change operation."""
    item_id: str
    from_status: str
    to_status: str
    from_list: str
    to_list: str
    item_data: Dict[str, Any]


class BaseDomainConfig(ABC):
    """
    Abstract base class for domain-specific configurations.

    Each domain (routing, invoices, banking) must implement this class
    to define its specific action configurations and status mappings.
    """

    # Domain identifier (must be set by subclasses)
    DOMAIN: str = ""

    # Default list names (universal across all domains)
    LIST_NAMES = {
        "to_process": "to_process",
        "in_process": "in_process",
        "pending": "pending",
        "processed": "processed",
    }

    # Action configurations (must be set by subclasses)
    ACTIONS: Dict[str, ActionConfig] = {}

    # Status to list override (domain-specific mappings)
    STATUS_TO_LIST_OVERRIDE: Dict[str, str] = {}

    @classmethod
    def get_action_type(cls, action: str) -> ActionType:
        """
        Get the action type (optimistic/pessimistic) for a given action.

        Args:
            action: Action name (e.g., "process", "stop", "delete")

        Returns:
            ActionType enum value
        """
        if action in cls.ACTIONS:
            return cls.ACTIONS[action].type
        return ActionType.PESSIMISTIC  # Default to pessimistic

    @classmethod
    def is_optimistic(cls, action: str) -> bool:
        """Check if an action is optimistic."""
        return cls.get_action_type(action) == ActionType.OPTIMISTIC

    @classmethod
    def is_pessimistic(cls, action: str) -> bool:
        """Check if an action is pessimistic."""
        return cls.get_action_type(action) == ActionType.PESSIMISTIC

    @classmethod
    def get_initial_status(cls, action: str) -> Optional[str]:
        """
        Get the initial status to assign when an action starts.

        Args:
            action: Action name

        Returns:
            Status string to assign, or None if no status change
        """
        if action in cls.ACTIONS:
            return cls.ACTIONS[action].initial_status
        return None

    @classmethod
    def get_success_status(cls, action: str) -> Optional[str]:
        """
        Get the status to assign when an action succeeds.

        Args:
            action: Action name

        Returns:
            Status string to assign on success
        """
        if action in cls.ACTIONS:
            return cls.ACTIONS[action].success_status
        return None

    @classmethod
    def get_error_status(cls, action: str) -> str:
        """
        Get the status to assign when an action fails.

        Args:
            action: Action name

        Returns:
            Status string to assign on error
        """
        if action in cls.ACTIONS:
            return cls.ACTIONS[action].error_status
        return "error"

    @classmethod
    def get_action_config(cls, action: str) -> Optional[ActionConfig]:
        """Get the full action configuration."""
        return cls.ACTIONS.get(action)

    @classmethod
    def get_list_for_status(cls, status: str) -> str:
        """
        Get the list name for a given status.
        Uses domain-specific overrides first, then falls back to StatusNormalizer.

        Args:
            status: Normalized status string

        Returns:
            List name (e.g., "to_process", "in_process", "pending", "processed")
        """
        # Check domain-specific override first
        if status in cls.STATUS_TO_LIST_OVERRIDE:
            category = cls.STATUS_TO_LIST_OVERRIDE[status]
            return cls.LIST_NAMES.get(category, category)

        # Fall back to centralized StatusNormalizer
        from ..status_normalization import StatusNormalizer
        category = StatusNormalizer.get_category(status)
        return cls.LIST_NAMES.get(category, category)

    @classmethod
    def get_ws_event_type(cls, action: str, event_phase: str) -> Optional[str]:
        """
        Get the WebSocket event type for an action phase.

        Args:
            action: Action name (e.g., "process")
            event_phase: Phase name ("started", "completed", "error")

        Returns:
            WebSocket event type string
        """
        config = cls.ACTIONS.get(action)
        if not config:
            return None

        if event_phase == "started":
            return config.ws_event_started or f"{cls.DOMAIN}.{action}_started"
        elif event_phase == "completed":
            return config.ws_event_completed or f"{cls.DOMAIN}.{action}_completed"
        elif event_phase == "error":
            return config.ws_event_error or f"{cls.DOMAIN}.error"

        return None

    @classmethod
    def get_all_actions(cls) -> List[str]:
        """Get list of all configured actions."""
        return list(cls.ACTIONS.keys())

    @classmethod
    def get_optimistic_actions(cls) -> List[str]:
        """Get list of optimistic actions."""
        return [
            action for action, config in cls.ACTIONS.items()
            if config.type == ActionType.OPTIMISTIC
        ]

    @classmethod
    def get_pessimistic_actions(cls) -> List[str]:
        """Get list of pessimistic actions."""
        return [
            action for action, config in cls.ACTIONS.items()
            if config.type == ActionType.PESSIMISTIC
        ]
