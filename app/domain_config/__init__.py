"""
Domain Configuration Module.

This module provides centralized configuration for domain-specific behaviors:
- Action types (optimistic/pessimistic)
- Status-to-list mappings
- WebSocket event types
- List movement logic

Usage:
    from domain_config import (
        get_domain_config,
        ListManager,
        ActionType,
    )

    # Get domain configuration
    config = get_domain_config("routing")

    # Check if action is optimistic
    if config.is_optimistic("process"):
        # Apply optimistic update
        result = ListManager.apply_status_change(
            domain="routing",
            cache_data=cache,
            item_ids=["doc1"],
            new_status="in_queue",
            action="process"
        )

Supported Domains:
    - routing: Router document processing
    - invoices: APbookeeper invoice processing
    - banking: Banker bank transaction processing
    - expenses: Notes de Frais expense processing
"""

from .base import (
    BaseDomainConfig,
    ActionConfig,
    ActionType,
    StatusChangeResult,
)

from .routing import RoutingDomainConfig
from .invoices import InvoicesDomainConfig
from .banking import BankingDomainConfig
from .expenses import ExpensesDomainConfig

from .list_manager import (
    ListManager,
    ListChangeResult,
    rollback_status_change,
)


# Domain config registry
_DOMAIN_CONFIGS = {
    "routing": RoutingDomainConfig,
    "invoices": InvoicesDomainConfig,
    "banking": BankingDomainConfig,
    "bank": BankingDomainConfig,       # alias for JOB_TYPE_CONFIG["bankbookeeper"]["domain"]
    "expenses": ExpensesDomainConfig,
}


def get_domain_config(domain: str) -> BaseDomainConfig:
    """
    Get the configuration class for a domain.

    Args:
        domain: Domain name ("routing", "invoices", "banking", "expenses")

    Returns:
        Domain configuration class

    Raises:
        ValueError: If domain is not supported
    """
    if domain not in _DOMAIN_CONFIGS:
        raise ValueError(
            f"Unknown domain: {domain}. "
            f"Supported domains: {list(_DOMAIN_CONFIGS.keys())}"
        )
    return _DOMAIN_CONFIGS[domain]


def get_supported_domains() -> list:
    """Get list of supported domain names."""
    return list(_DOMAIN_CONFIGS.keys())


def register_domain(domain: str, config_class: BaseDomainConfig) -> None:
    """
    Register a new domain configuration.

    Args:
        domain: Domain name
        config_class: Configuration class (must inherit from BaseDomainConfig)
    """
    _DOMAIN_CONFIGS[domain] = config_class
    ListManager.register_domain(domain, config_class)


__all__ = [
    # Base classes
    "BaseDomainConfig",
    "ActionConfig",
    "ActionType",
    "StatusChangeResult",
    # Domain configs
    "RoutingDomainConfig",
    "InvoicesDomainConfig",
    "BankingDomainConfig",
    "ExpensesDomainConfig",
    # List manager
    "ListManager",
    "ListChangeResult",
    "rollback_status_change",
    # Functions
    "get_domain_config",
    "get_supported_domains",
    "register_domain",
]
