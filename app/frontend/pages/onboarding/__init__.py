"""
Onboarding Page Handlers
========================

Backend handlers for the company onboarding flow in Next.js frontend.

NAMESPACE: ONBOARDING

This module provides:
- ERP connection testing (Odoo, Banana)
- Client management (for managing companies on behalf of others)
- Form submission and company creation
- OAuth flows for Google Drive/Chat

Architecture:
    Frontend (Next.js) -> wsClient.send({type: "onboarding.*", ...})
                       -> WebSocket Hub
                       -> handlers.py
                       -> Firebase | ERP Services

Note: user_id is injected automatically from WebSocket context.
"""

from .handlers import (
    OnboardingHandlers,
    get_onboarding_handlers,
)
from .orchestration import (
    handle_test_erp_connection,
    handle_load_clients,
    handle_save_client,
    handle_update_client,
    handle_delete_client,
    handle_submit,
    handle_oauth_complete,
)

__all__ = [
    # Handlers
    "OnboardingHandlers",
    "get_onboarding_handlers",
    # Orchestration functions
    "handle_test_erp_connection",
    "handle_load_clients",
    "handle_save_client",
    "handle_update_client",
    "handle_delete_client",
    "handle_submit",
    "handle_oauth_complete",
]
