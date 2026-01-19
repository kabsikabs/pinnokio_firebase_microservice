# Frontend Migration Module

This module contains all handlers and utilities for the Next.js frontend migration.
It wraps existing backend services without modifying them.

## Directory Structure

```
frontend/
├── __init__.py                       # Main exports
├── README.md                         # This file
│
├── core/                             # Shared utilities (all pages)
│   ├── __init__.py
│   ├── auth_handlers.py             # Firebase token verification & session
│   ├── page_state_manager.py        # Page state recovery (Redis cache)
│   └── pending_action_manager.py    # OAuth/redirect state management
│
└── pages/                            # Page-specific handlers
    ├── __init__.py
    │
    ├── dashboard/                    # Dashboard page (85% complete)
    │   ├── __init__.py
    │   ├── handlers.py              # RPC endpoints (DASHBOARD.*)
    │   ├── orchestration.py         # Post-auth data orchestration
    │   ├── approval_handlers.py     # Approval workflow (router, banker, apbookeeper)
    │   ├── task_handlers.py         # Task management (list, execute, toggle)
    │   └── providers/               # Component-specific data fetchers
    │       ├── __init__.py
    │       └── account_balance_card.py
    │
    ├── chat/                         # Chat page (TODO)
    ├── invoices/                     # Invoices page (TODO)
    ├── expenses/                     # Expenses page (TODO)
    ├── banking/                      # Banking page (TODO)
    └── hr/                           # HR page (TODO)
```

## Architecture Pattern

```
Frontend (Next.js)
        │
        │  wsClient.send({ type: 'dashboard.orchestrate_init', ... })
        │
        ▼
frontend/pages/dashboard/orchestration.py
        │
        │  Uses existing singletons
        │
        ├──► firebase_providers.py (FirebaseManagement)
        ├──► redis_client.py (Sessions, Cache)
        ├──► erp_service.py (Odoo)
        ├──► driveClientService.py (Drive)
        └──► llm_service/ (Chat)
        │
        ▼
Redis Cache + WebSocket Response
```

## Usage Examples

### Import from main frontend module
```python
from app.frontend import (
    handle_firebase_token,
    get_page_state_manager,
    DashboardHandlers,
)
```

### Import from specific module
```python
from app.frontend.core import handle_firebase_token
from app.frontend.pages.dashboard import handle_orchestrate_init
```

### Backward compatibility (deprecated)
```python
# Still works but deprecated - use app.frontend instead
from app.wrappers import handle_firebase_token
```

## Adding a New Page

### Step 1: Create directory structure
```bash
mkdir -p app/frontend/pages/new_page/providers
touch app/frontend/pages/new_page/__init__.py
touch app/frontend/pages/new_page/handlers.py
touch app/frontend/pages/new_page/orchestration.py
touch app/frontend/pages/new_page/providers/__init__.py
```

### Step 2: Implement handlers.py (RPC endpoints)
```python
"""
New Page RPC Handlers
"""
from typing import Dict, Any

def get_new_page_handlers() -> "NewPageHandlers":
    return NewPageHandlers()

class NewPageHandlers:
    """RPC endpoints for NEW_PAGE namespace."""

    async def full_data(self, uid: str, mandate_path: str, ...) -> Dict[str, Any]:
        """NEW_PAGE.full_data - Fetch all data for the page."""
        pass
```

### Step 3: Implement orchestration.py (post-auth flow)
```python
"""
New Page Orchestration
"""
async def handle_orchestrate_init(payload: Dict[str, Any]) -> None:
    """Handle new_page.orchestrate_init WebSocket event."""
    pass
```

### Step 4: Add providers for components
```python
# providers/my_component.py
async def get_my_component_data(uid: str, mandate_path: str) -> Dict[str, Any]:
    """Fetch data for MyComponent."""
    pass
```

### Step 5: Export in __init__.py files
```python
# pages/new_page/__init__.py
from .handlers import NewPageHandlers, get_new_page_handlers
from .orchestration import handle_orchestrate_init

__all__ = ["NewPageHandlers", "get_new_page_handlers", "handle_orchestrate_init"]
```

### Step 6: Register in main.py
```python
# In main.py WebSocket handler
elif msg_type == "new_page.orchestrate_init":
    await handle_new_page_orchestrate_init(payload)
```

### Step 7: Update frontend/__init__.py
```python
from .pages.new_page import NewPageHandlers
```

## Core Module Details

### auth_handlers.py
- `handle_firebase_token(payload)`: Verify Firebase ID token, create session
- `get_session(uid, session_id)`: Retrieve session from Redis
- `invalidate_session(uid, session_id)`: Logout/cleanup

### page_state_manager.py
- `PageStateManager.save_page_state()`: Cache page data in Redis
- `PageStateManager.get_page_state()`: Retrieve cached data for fast refresh

### pending_action_manager.py
- `PendingActionManager.save_pending_action()`: Store OAuth/payment state
- `PendingActionManager.complete_pending_action()`: Resume after redirect

## WebSocket Events (Dashboard)

| Event | Handler | Purpose |
|-------|---------|---------|
| `auth.firebase_token` | `handle_firebase_token()` | Login/session creation |
| `dashboard.orchestrate_init` | `handle_orchestrate_init()` | Initial dashboard load |
| `dashboard.company_change` | `handle_company_change()` | Switch company context |
| `dashboard.refresh` | `handle_refresh()` | Manual refresh |
| `page.restore_state` | `PageStateManager.get_page_state()` | Fast refresh recovery |
| `approval.*` | `ApprovalHandlers` | Approval operations |
| `task.*` | `TaskHandlers` | Task operations |

## Dependencies (Singletons - DO NOT MODIFY)

| Singleton | File | Purpose |
|-----------|------|---------|
| `get_firebase_app()` | `firebase_client.py` | Firebase Admin SDK |
| `get_redis()` | `redis_client.py` | Redis connection |
| `FirebaseManagement` | `firebase_providers.py` | Firebase data layer |
| `ERPService` | `erp_service.py` | Odoo ERP connection |
| `DriveClientService` | `driveClientService.py` | Google Drive API |
| `ws_hub` | `ws_hub.py` | WebSocket broadcast |

## Migration Status

| Page | Status | Notes |
|------|--------|-------|
| Dashboard | 85% | Tasks, Approvals, Metrics complete |
| Chat | TODO | LLM service ready |
| Invoices | TODO | Spec in docs/pages/invoices.md |
| Expenses | TODO | Spec in docs/pages/expenses.md |
| Banking | TODO | Spec in docs/pages/banking.md |
| HR | TODO | hr_rpc_handlers.py exists |
