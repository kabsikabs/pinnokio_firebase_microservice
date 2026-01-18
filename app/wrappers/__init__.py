"""
Wrappers Module - Business Logic Abstraction Layer
===================================================

This module contains wrapper functions that encapsulate business logic
previously handled by the frontend. All code here is ADDITIVE and does
not modify existing backend services.

Submodules:
    - auth_handlers: WebSocket authentication event handlers
    - dashboard_orchestration_handlers: Dashboard data loading orchestration
"""

from .auth_handlers import (
    handle_firebase_token,
    get_session,
    update_session_activity,
    invalidate_session,
    AuthenticationError
)

from .dashboard_orchestration_handlers import (
    handle_orchestrate_init,
    handle_company_change,
    handle_refresh,
    get_state_manager,
    OrchestrationStateManager
)

__all__ = [
    # Auth handlers
    "handle_firebase_token",
    "get_session",
    "update_session_activity",
    "invalidate_session",
    "AuthenticationError",
    # Dashboard orchestration handlers
    "handle_orchestrate_init",
    "handle_company_change",
    "handle_refresh",
    "get_state_manager",
    "OrchestrationStateManager"
]
