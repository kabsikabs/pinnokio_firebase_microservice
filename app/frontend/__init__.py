"""
Frontend Migration Module - Next.js Integration Layer
======================================================

This module contains handlers and utilities specific to the Next.js frontend migration.
It wraps existing backend services without modifying them.

Structure:
    frontend/
    ├── core/                          # Re-exports from wrappers (backward compat)
    │   └── __init__.py               # Re-exports auth/state managers
    │
    └── pages/                         # Page-specific handlers
        ├── dashboard/                # Dashboard page (85% complete)
        │   ├── handlers.py          # RPC endpoints (DASHBOARD.*)
        │   ├── balance_handlers.py  # Balance/top-up (REAL IMPLEMENTATION)
        │   └── providers/           # Component data fetchers
        └── chat/                     # Chat page (NEW)
            ├── handlers.py
            └── orchestration.py

Note: Most handlers are now in app.wrappers/ (SOURCE OF TRUTH)
      frontend/ modules re-export from wrappers for backward compatibility

Architecture Pattern:
    Frontend (Next.js) → WebSocket → wrappers/ handlers → Existing Services (Singletons)

Dependencies (DO NOT MODIFY - Use as-is):
    - firebase_client: Firebase Admin SDK singleton
    - redis_client: Redis connection singleton
    - firebase_providers: Firebase data layer (FirebaseManagement)
    - erp_service: ERP connection manager
    - driveClientService: Google Drive API
    - ws_hub: WebSocket broadcast hub

Migration Pattern for New Pages:
    1. Create app/wrappers/new_page_handlers.py with implementation
    2. Add frontend/pages/new_page/ directory if needed
    3. Re-export from wrappers in frontend/__init__.py
    4. Register handlers in main.py

Author: Migration Team
Created: 2026-01-19
"""

# ============================================
# Core Utilities (re-exported from wrappers)
# ============================================
from .core import (
    # Auth
    AuthenticationError,
    handle_firebase_token,
    get_session,
    update_session_activity,
    invalidate_session,
    # Page State
    PageStateManager,
    get_page_state_manager,
    # Pending Actions
    PendingActionManager,
    get_pending_action_manager,
)

# ============================================
# Dashboard Page (85% complete)
# ============================================
from .pages.dashboard import (
    # Orchestration (re-exported from wrappers)
    DashboardOrchestrationHandlers,
    process_post_authentication,
    handle_company_change,
    handle_switch_account,
    # RPC (local implementation)
    DashboardHandlers,
    get_dashboard_handlers,
    # Balance (REAL IMPLEMENTATION - wrappers re-exports from here)
    BalanceHandlers,
    get_balance_handlers,
    handle_top_up,
    handle_refresh_balance,
    # Approvals (re-exported from wrappers)
    ApprovalHandlers,
    get_approval_handlers,
    # Tasks (re-exported from wrappers)
    TaskHandlers,
    get_task_handlers,
    # Providers (local implementation)
    get_account_balance_data,
)

# ============================================
# Chat Page (NEW)
# ============================================
from .pages.chat import (
    # Handlers
    ChatHandlers,
    get_chat_handlers,
    # Orchestration
    handle_orchestrate_init as handle_chat_orchestrate_init,
    handle_session_select as handle_chat_session_select,
    handle_session_create as handle_chat_session_create,
    handle_session_delete as handle_chat_session_delete,
    handle_session_rename as handle_chat_session_rename,
)

# ============================================
# Notifications Page (NEW)
# ============================================
from .pages.notifications import (
    NotificationHandlers,
    get_notification_handlers,
    handle_notification_mark_read,
    handle_notification_click,
)

# ============================================
# Messenger Page (NEW)
# ============================================
from .pages.messenger import (
    MessengerHandlers,
    get_messenger_handlers,
    handle_messenger_mark_read,
    handle_messenger_click,
)

# ============================================
# Metrics Module (NEW) - Shared metrics stores
# ============================================
from .pages.metrics import (
    MetricsHandlers,
    get_metrics_handlers,
    handle_metrics_refresh,
    handle_metrics_refresh_module,
    emit_metrics_full_data,
)

__all__ = [
    # Core (re-exported from wrappers)
    "AuthenticationError",
    "handle_firebase_token",
    "get_session",
    "update_session_activity",
    "invalidate_session",
    "PageStateManager",
    "get_page_state_manager",
    "PendingActionManager",
    "get_pending_action_manager",
    # Dashboard - Orchestration (re-exported from wrappers)
    "DashboardOrchestrationHandlers",
    "process_post_authentication",
    "handle_company_change",
    "handle_switch_account",
    # Dashboard - RPC (local)
    "DashboardHandlers",
    "get_dashboard_handlers",
    # Dashboard - Balance (REAL IMPLEMENTATION)
    "BalanceHandlers",
    "get_balance_handlers",
    "handle_top_up",
    "handle_refresh_balance",
    # Dashboard - Approvals (re-exported from wrappers)
    "ApprovalHandlers",
    "get_approval_handlers",
    # Dashboard - Tasks (re-exported from wrappers)
    "TaskHandlers",
    "get_task_handlers",
    # Dashboard - Providers (local)
    "get_account_balance_data",
    # Chat (NEW)
    "ChatHandlers",
    "get_chat_handlers",
    "handle_chat_orchestrate_init",
    "handle_chat_session_select",
    "handle_chat_session_create",
    "handle_chat_session_delete",
    "handle_chat_session_rename",
    # Notifications (NEW)
    "NotificationHandlers",
    "get_notification_handlers",
    "handle_notification_mark_read",
    "handle_notification_click",
    # Messenger (NEW)
    "MessengerHandlers",
    "get_messenger_handlers",
    "handle_messenger_mark_read",
    "handle_messenger_click",
    # Metrics (NEW) - Shared metrics stores
    "MetricsHandlers",
    "get_metrics_handlers",
    "handle_metrics_refresh",
    "handle_metrics_refresh_module",
    "emit_metrics_full_data",
]
