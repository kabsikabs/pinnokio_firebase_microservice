"""
Frontend Migration Module - Next.js Integration Layer
======================================================

This module contains all handlers and utilities specific to the Next.js frontend migration.
It wraps existing backend services without modifying them.

Structure:
    frontend/
    ├── core/                          # Centralized utilities for all pages
    │   ├── auth_handlers.py          # Firebase token verification & session
    │   ├── page_state_manager.py     # Page state recovery (Redis cache)
    │   └── pending_action_manager.py # OAuth/redirect state management
    │
    └── pages/                         # Page-specific handlers
        ├── dashboard/                # Dashboard page (85% complete)
        │   ├── handlers.py          # RPC endpoints (DASHBOARD.*)
        │   ├── orchestration.py     # Post-auth data orchestration
        │   ├── approval_handlers.py # Approval workflow
        │   ├── task_handlers.py     # Task management
        │   └── providers/           # Component data fetchers
        ├── chat/                     # (future)
        ├── invoices/                 # (future)
        ├── expenses/                 # (future)
        ├── banking/                  # (future)
        └── hr/                       # (future)

Architecture Pattern:
    Frontend (Next.js) → WebSocket → frontend/ handlers → Existing Services (Singletons)

Dependencies (DO NOT MODIFY - Use as-is):
    - firebase_client: Firebase Admin SDK singleton
    - redis_client: Redis connection singleton
    - firebase_providers: Firebase data layer (FirebaseManagement)
    - erp_service: ERP connection manager
    - driveClientService: Google Drive API
    - ws_hub: WebSocket broadcast hub

Migration Pattern for New Pages:
    1. Create frontend/pages/new_page/ directory
    2. Add handlers.py with RPC endpoints
    3. Add orchestration.py for post-auth flow
    4. Add providers/ for component data
    5. Register handlers in main.py

Author: Migration Team
Created: 2026-01-19
"""

# ============================================
# Core Utilities (shared across all pages)
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
    # Orchestration
    DashboardOrchestrationHandlers,
    process_post_authentication,
    handle_company_change,
    handle_switch_account,
    # RPC
    DashboardHandlers,
    get_dashboard_handlers,
    # Approvals
    ApprovalHandlers,
    get_approval_handlers,
    # Tasks
    TaskHandlers,
    get_task_handlers,
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
# Backward Compatibility - Data Providers
# ============================================
# Export get_account_balance_data for direct import: from app.frontend import get_account_balance_data
# New location: app.frontend.pages.dashboard.providers
from .pages.dashboard.providers import get_account_balance_data

# ============================================
# Legacy Alias (for existing imports in main.py)
# ============================================
# These allow: from app.frontend import handle_firebase_token (old path)
# As well as: from app.frontend.core import handle_firebase_token (new path)

__all__ = [
    # Core
    "AuthenticationError",
    "handle_firebase_token",
    "get_session",
    "update_session_activity",
    "invalidate_session",
    "PageStateManager",
    "get_page_state_manager",
    "PendingActionManager",
    "get_pending_action_manager",
    # Dashboard
    "DashboardOrchestrationHandlers",
    "DashboardHandlers",
    "ApprovalHandlers",
    "TaskHandlers",
    "process_post_authentication",
    "handle_company_change",
    "handle_switch_account",
    "get_dashboard_handlers",
    "get_approval_handlers",
    "get_task_handlers",
    # Chat (NEW)
    "ChatHandlers",
    "get_chat_handlers",
    "handle_chat_orchestrate_init",
    "handle_chat_session_select",
    "handle_chat_session_create",
    "handle_chat_session_delete",
    "handle_chat_session_rename",
    # Providers (backward compat)
    "get_account_balance_data",
]
