"""
Metrics Module - Shared Metrics Stores Handlers
================================================

Handles metrics for all modules (routing, ap, bank, expenses).
These metrics are shared between dashboard widgets and detail pages.

Events handled:
- metrics.refresh: Refresh all modules
- metrics.refresh_module: Refresh specific module

Events emitted:
- metrics.full_data: All metrics data
- metrics.update_confirmed: Optimistic update confirmed
- metrics.update_failed: Optimistic update failed

Architecture:
- Metrics are loaded once at onboarding/company change
- Shared between dashboard and detail pages (routing, ap, bank, expenses)
- Optimistic/pessimistic pattern for user actions
"""

from .handlers import MetricsHandlers, get_metrics_handlers
from .orchestration import (
    handle_metrics_refresh,
    handle_metrics_refresh_module,
    emit_metrics_full_data,
)

__all__ = [
    "MetricsHandlers",
    "get_metrics_handlers",
    "handle_metrics_refresh",
    "handle_metrics_refresh_module",
    "emit_metrics_full_data",
]
