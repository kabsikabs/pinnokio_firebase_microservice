"""
Dashboard Handlers — Lazy re-export from canonical location.

The single source of truth is:
    app.frontend.pages.dashboard.handlers

This file exists only for backward-compatibility with existing imports:
    - app.main (RPC routing: DASHBOARD.*)
    - app.wrappers.dashboard_orchestration_handlers
    - app.frontend.pages.metrics.handlers

Uses lazy import to avoid circular import during module initialization
(dashboard_orchestration_handlers → dashboard_handlers → frontend.pages.dashboard.handlers).
"""


def get_dashboard_handlers():
    """Lazy singleton accessor — avoids circular import at module load time."""
    from .frontend.pages.dashboard.handlers import get_dashboard_handlers as _get
    return _get()


def __getattr__(name):
    """Lazy attribute access for DashboardHandlers class."""
    if name == "DashboardHandlers":
        from .frontend.pages.dashboard.handlers import DashboardHandlers
        return DashboardHandlers
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
