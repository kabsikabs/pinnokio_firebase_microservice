"""
Company Settings Page Handlers
==============================

Backend handlers for the Company Settings page in Next.js frontend.

NAMESPACE: COMPANY_SETTINGS

This module provides:
- Orchestration for initial page load
- Company info CRUD operations
- Workflow parameters management (APbookeeper, Banker, Router)
- Context management
- Telegram room registration (CRITICAL)
- Asset management
- ERP connections
- User sharing (company access)
- Company deletion

Architecture:
    Frontend (Next.js) -> wsClient.send({type: "company_settings.*", ...})
                       -> WebSocket Hub
                       -> handlers.py
                       -> Redis Cache | Firebase

Note: user_id and company_id are injected automatically from WebSocket context.
"""

from .handlers import (
    CompanySettingsHandlers,
    get_company_settings_handlers,
)
from .orchestration import (
    handle_orchestrate_init,
    handle_fetch_additional,
    handle_save_company_info,
    handle_save_settings,
    handle_save_workflow,
    handle_save_context,
    handle_save_asset_config,
    handle_list_asset_models,
    handle_create_asset_model,
    handle_update_asset_model,
    handle_delete_asset_model,
    handle_load_asset_accounts,
)
from .telegram_handler import (
    handle_telegram_start_registration,
    handle_telegram_remove_user,
    handle_telegram_reset_room,
)

__all__ = [
    # Handlers
    "CompanySettingsHandlers",
    "get_company_settings_handlers",
    # Orchestration
    "handle_orchestrate_init",
    "handle_fetch_additional",
    "handle_save_company_info",
    "handle_save_settings",
    "handle_save_workflow",
    "handle_save_context",
    # Asset Management
    "handle_save_asset_config",
    "handle_list_asset_models",
    "handle_create_asset_model",
    "handle_update_asset_model",
    "handle_delete_asset_model",
    "handle_load_asset_accounts",
    # Telegram
    "handle_telegram_start_registration",
    "handle_telegram_remove_user",
    "handle_telegram_reset_room",
]
