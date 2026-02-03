"""
Centralized List Manager - Handles item movement between lists based on status.

This module provides the core functionality for moving items between lists
when their status changes. It's designed to be reusable across all domains
(routing, invoices, banking) and supports both optimistic and pessimistic updates.

Usage:
    from domain_config import ListManager

    # Process action (optimistic)
    result = ListManager.apply_status_change(
        domain="routing",
        cache_data=current_cache,
        item_ids=["doc1", "doc2"],
        new_status="in_queue",
        action="process"
    )

    # The result contains items to move and the WebSocket payload to broadcast
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

from ..status_normalization import StatusNormalizer

logger = logging.getLogger(__name__)


@dataclass
class ListChangeResult:
    """Result of a list change operation."""
    success: bool
    items_moved: List[Dict[str, Any]] = field(default_factory=list)
    from_list: str = ""
    to_list: str = ""
    new_status: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None
    ws_payload: Optional[Dict[str, Any]] = None


class ListManager:
    """
    Centralized manager for item list movements.

    Handles:
    - Finding items in cache
    - Moving items between lists
    - Updating item status
    - Generating WebSocket payloads
    - Calculating updated counts
    """

    # Domain config registry - populated by get_domain_config()
    _domain_configs = {}

    @classmethod
    def register_domain(cls, domain: str, config_class) -> None:
        """Register a domain configuration class."""
        cls._domain_configs[domain] = config_class

    @classmethod
    def get_domain_config(cls, domain: str):
        """Get the configuration class for a domain."""
        if domain not in cls._domain_configs:
            # Lazy import to avoid circular dependencies
            from .routing import RoutingDomainConfig
            from .invoices import InvoicesDomainConfig
            from .banking import BankingDomainConfig
            from .expenses import ExpensesDomainConfig

            cls._domain_configs = {
                "routing": RoutingDomainConfig,
                "invoices": InvoicesDomainConfig,
                "banking": BankingDomainConfig,
                "expenses": ExpensesDomainConfig,
            }

        return cls._domain_configs.get(domain)

    @classmethod
    def apply_status_change(
        cls,
        domain: str,
        cache_data: Dict[str, Any],
        item_ids: List[str],
        new_status: str,
        action: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> ListChangeResult:
        """
        Apply a status change to items and move them between lists.

        This is the main entry point for status-based list changes.
        It handles:
        1. Finding items in the current lists
        2. Determining the target list based on status
        3. Moving items with updated status
        4. Calculating new counts
        5. Generating WebSocket payload

        Args:
            domain: Domain name (e.g., "routing", "invoices", "banking")
            cache_data: Current cache data with lists and counts
            item_ids: List of item IDs to process
            new_status: New status to assign
            action: Optional action name for context
            extra_data: Optional extra data to merge into items

        Returns:
            ListChangeResult with moved items and WebSocket payload
        """
        config = cls.get_domain_config(domain)
        if not config:
            return ListChangeResult(
                success=False,
                error=f"Unknown domain: {domain}"
            )

        try:
            # Normalize the status
            normalized_status = StatusNormalizer.normalize(new_status)

            # Get target list based on status
            to_list = config.get_list_for_status(normalized_status)

            # Get all list names for this domain
            list_names = list(config.LIST_NAMES.values())

            # Find and move items
            items_moved = []
            from_list = None

            for item_id in item_ids:
                # Find item in any list
                found_item, source_list = cls._find_item_in_lists(
                    cache_data, item_id, list_names
                )

                if found_item:
                    # Store source list (should be same for all items in batch)
                    if from_list is None:
                        from_list = source_list

                    # Remove from source list
                    cls._remove_item_from_list(cache_data, item_id, source_list)

                    # Update item data
                    updated_item = cls._update_item(
                        found_item,
                        normalized_status,
                        extra_data
                    )

                    # Add to target list (if not delete action)
                    if action != "delete" and to_list:
                        cls._add_item_to_list(cache_data, updated_item, to_list)

                    items_moved.append(updated_item)

                    logger.debug(
                        f"[LIST_MANAGER] Item {item_id} moved: "
                        f"{source_list} → {to_list} (status: {normalized_status})"
                    )
                else:
                    logger.warning(
                        f"[LIST_MANAGER] Item {item_id} not found in any list"
                    )

            # Calculate updated counts
            counts = cls._calculate_counts(cache_data, list_names)
            cache_data["counts"] = counts

            # Generate WebSocket payload
            ws_payload = cls._build_ws_payload(
                domain=domain,
                action=action,
                items=items_moved,
                from_list=from_list or "",
                to_list=to_list,
                new_status=normalized_status,
                counts=counts,
            )

            logger.info(
                f"[LIST_MANAGER] Status change applied: domain={domain} "
                f"action={action} items={len(items_moved)} "
                f"from={from_list} to={to_list} status={normalized_status}"
            )

            return ListChangeResult(
                success=True,
                items_moved=items_moved,
                from_list=from_list or "",
                to_list=to_list,
                new_status=normalized_status,
                counts=counts,
                ws_payload=ws_payload,
            )

        except Exception as e:
            logger.error(f"[LIST_MANAGER] Error applying status change: {e}", exc_info=True)
            return ListChangeResult(
                success=False,
                error=str(e)
            )

    @classmethod
    def _find_item_in_lists(
        cls,
        cache_data: Dict[str, Any],
        item_id: str,
        list_names: List[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Find an item in any of the lists."""
        logger.info(
            f"[LIST_MANAGER][FLOW] _find_item_in_lists: searching for '{item_id}' "
            f"in lists: {list_names}"
        )

        for list_name in list_names:
            items = cache_data.get(list_name, [])
            logger.info(
                f"[LIST_MANAGER][FLOW] Checking list '{list_name}': {len(items)} items"
            )
            for item in items:
                # Check multiple ID fields (different domains use different ID fields)
                # - routing/invoices/banking use: id, job_id
                # - expenses use: expense_id
                item_id_val = item.get("id")
                job_id_val = item.get("job_id")
                expense_id_val = item.get("expense_id")

                if (item_id_val == item_id or
                    job_id_val == item_id or
                    expense_id_val == item_id):
                    logger.info(
                        f"[LIST_MANAGER][FLOW] ✓ FOUND item in '{list_name}'! "
                        f"id={item_id_val}, job_id={job_id_val}, expense_id={expense_id_val}"
                    )
                    return item.copy(), list_name

        # Not found - log all available IDs for debugging
        logger.warning(f"[LIST_MANAGER][FLOW] ❌ Item '{item_id}' NOT FOUND in any list")
        for list_name in list_names:
            items = cache_data.get(list_name, [])
            if items:
                ids = [(item.get('id'), item.get('job_id'), item.get('expense_id')) for item in items[:3]]
                logger.warning(f"[LIST_MANAGER][FLOW]   {list_name} sample IDs (id, job_id, expense_id): {ids}")
        return None, None

    @classmethod
    def _remove_item_from_list(
        cls,
        cache_data: Dict[str, Any],
        item_id: str,
        list_name: str,
    ) -> bool:
        """Remove an item from a specific list."""
        if list_name not in cache_data:
            return False

        items = cache_data[list_name]
        for i, item in enumerate(items):
            # Check multiple ID fields (same as _find_item_in_lists)
            if (item.get("id") == item_id or
                item.get("job_id") == item_id or
                item.get("expense_id") == item_id):
                items.pop(i)
                return True
        return False

    @classmethod
    def _add_item_to_list(
        cls,
        cache_data: Dict[str, Any],
        item: Dict[str, Any],
        list_name: str,
    ) -> None:
        """Add an item to a specific list (at the beginning)."""
        if list_name not in cache_data:
            cache_data[list_name] = []
        cache_data[list_name].insert(0, item)

    @classmethod
    def _update_item(
        cls,
        item: Dict[str, Any],
        new_status: str,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update item with new status and extra data."""
        item["status"] = new_status
        item["updated_at"] = datetime.now(timezone.utc).isoformat()

        if extra_data:
            item.update(extra_data)

        return item

    @classmethod
    def _calculate_counts(
        cls,
        cache_data: Dict[str, Any],
        list_names: List[str],
    ) -> Dict[str, int]:
        """Calculate counts for all lists."""
        return {
            list_name: len(cache_data.get(list_name, []))
            for list_name in list_names
        }

    @classmethod
    def _build_ws_payload(
        cls,
        domain: str,
        action: Optional[str],
        items: List[Dict[str, Any]],
        from_list: str,
        to_list: str,
        new_status: str,
        counts: Dict[str, int],
    ) -> Dict[str, Any]:
        """Build WebSocket payload for the status change event."""
        return {
            "type": f"{domain}.item_update",
            "payload": {
                "action": "status_change",
                "trigger_action": action,
                "items": items,
                "item_ids": [item.get("id") for item in items],
                "from_list": from_list,
                "to_list": to_list,
                "new_status": new_status,
                "counts": counts,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }

    @classmethod
    def get_initial_status_for_action(
        cls,
        domain: str,
        action: str,
    ) -> Optional[str]:
        """Get the initial status to assign for an action."""
        config = cls.get_domain_config(domain)
        if config:
            return config.get_initial_status(action)
        return None

    @classmethod
    def is_action_optimistic(cls, domain: str, action: str) -> bool:
        """Check if an action should be handled optimistically."""
        config = cls.get_domain_config(domain)
        if config:
            return config.is_optimistic(action)
        return False

    @classmethod
    def is_action_pessimistic(cls, domain: str, action: str) -> bool:
        """Check if an action should be handled pessimistically."""
        config = cls.get_domain_config(domain)
        if config:
            return config.is_pessimistic(action)
        return True  # Default to pessimistic


# Convenience function for rollback
def rollback_status_change(
    domain: str,
    cache_data: Dict[str, Any],
    items: List[Dict[str, Any]],
    original_status: str,
    original_list: str,
) -> ListChangeResult:
    """
    Rollback a status change (for optimistic updates that failed).

    Args:
        domain: Domain name
        cache_data: Current cache data
        items: Items to rollback
        original_status: Original status before the change
        original_list: Original list name

    Returns:
        ListChangeResult with rollback details
    """
    item_ids = [item.get("id") for item in items]
    return ListManager.apply_status_change(
        domain=domain,
        cache_data=cache_data,
        item_ids=item_ids,
        new_status=original_status,
        action="rollback",
    )
