# Dashboard Orchestration Handlers - Implementation Guide

## Overview

This module implements the WebSocket-based dashboard orchestration flow that automatically loads data after Firebase authentication.

**File**: `C:\Users\Cedri\Coding\firebase_microservice\app\wrappers\dashboard_orchestration_handlers.py`

**Lines of Code**: 758

**Created**: 2026-01-18

---

## Architecture

```
Frontend                                Backend (This Module)
   |                                       |
   |-- auth.firebase_token --------------->|
   |<- auth.session_confirmed -------------|
   |                                       |
   |-- dashboard.orchestrate_init -------->| [NEW]
   |                                       |
   |                                       |-- Phase 1: Company Selection
   |<- dashboard.phase_start (company) ----| [NEW]
   |<- company.list -----------------------|
   |<- company.details --------------------|
   |<- dashboard.phase_complete (company) -| [NEW]
   |                                       |
   |                                       |-- Phase 2: Data Loading
   |<- dashboard.phase_start (data) -------| [NEW]
   |<- dashboard.data_loading_progress ----| [NEW]
   |<- dashboard.full_data ----------------|
   |<- dashboard.phase_complete (data) ----| [NEW]
   |                                       |
   |                                       |-- Phase 3: LLM Session
   |<- dashboard.phase_start (llm) --------| [NEW]
   |<- llm.session_ready ------------------|
   |<- dashboard.phase_complete (llm) -----| [NEW]
```

---

## Implementation Details

### 1. Wrapper Pattern

This module is **ADDITIVE ONLY** - it wraps existing services without modifying them:

- Uses `dashboard_handlers.get_dashboard_handlers()` for data fetching
- Uses `firebase_client.get_firestore()` for Firestore access
- Uses `redis_client.get_redis()` for state management
- Uses `ws_hub.hub.broadcast()` for WebSocket communication
- Uses `llm_service.session_state_manager.SessionStateManager` for LLM sessions

### 2. Redis Key Structure

#### Orchestration State
**Key**: `orchestration:{uid}:{session_id}:state`
**TTL**: 1 hour
**Structure**:
```json
{
  "orchestration_id": "uuid-string",
  "phase": "company|data|llm|completed|error",
  "started_at": "2026-01-18T10:00:00Z",
  "updated_at": "2026-01-18T10:00:05Z",
  "cancellation_requested": false,
  "selected_company_id": "company-uuid",
  "widgets_status": {
    "balance": "pending|loading|completed|error",
    "metrics": "pending|loading|completed|error",
    ...
  },
  "errors": []
}
```

#### Company Selection Cache
**Key**: `company:{uid}:selected`
**TTL**: 24 hours
**Structure**:
```json
{
  "company_id": "company-uuid",
  "company_name": "ACME Corp",
  "selected_at": "2026-01-18T10:00:00Z"
}
```

### 3. WebSocket Events Handled

| Event Name | Direction | Description |
|------------|-----------|-------------|
| `dashboard.orchestrate_init` | FE -> BE | Trigger full orchestration after auth |
| `dashboard.company_change` | FE -> BE | Trigger company switch with cancellation |
| `dashboard.refresh` | FE -> BE | Force refresh all data |
| `dashboard.phase_start` | BE -> FE | Notify phase start (company/data/llm) |
| `dashboard.phase_complete` | BE -> FE | Notify phase completion |
| `dashboard.data_loading_progress` | BE -> FE | Individual widget loading status |

---

## Firestore Structure Used

The module adapts to the existing Firestore structure:

```
clients/{uid}/
  ├── companies/{company_id}         # Company list (light version)
  │   ├── name
  │   ├── legal_name
  │   └── is_default
  │
  └── ... (other collections)

mandates/{company_id}                 # Company details (full version)
  ├── name
  ├── legal_name
  ├── balance
  ├── currency
  ├── mandate_path
  └── client_uuid
```

---

## Public API

### Handler Functions

#### `handle_orchestrate_init(uid, session_id, payload)`
Triggers the full dashboard orchestration sequence.

**Request**:
```json
{
  "type": "dashboard.orchestrate_init",
  "payload": {}
}
```

**Response**:
```json
{
  "type": "dashboard.orchestrate_init",
  "payload": {
    "success": true,
    "orchestration_id": "uuid-string",
    "message": "Orchestration started"
  }
}
```

#### `handle_company_change(uid, session_id, payload)`
Cancels current orchestration and starts new one for selected company.

**Request**:
```json
{
  "type": "dashboard.company_change",
  "payload": {
    "company_id": "company-uuid"
  }
}
```

**Response**:
```json
{
  "type": "dashboard.company_change",
  "payload": {
    "success": true,
    "orchestration_id": "uuid-string",
    "company_id": "company-uuid"
  }
}
```

#### `handle_refresh(uid, session_id, payload)`
Forces refresh of all dashboard data from source.

**Request**:
```json
{
  "type": "dashboard.refresh",
  "payload": {}
}
```

**Response**:
```json
{
  "type": "dashboard.refresh",
  "payload": {
    "success": true,
    "company_id": "company-uuid"
  }
}
```

---

## Integration with main.py

To activate this module, add the following routing in `main.py` WebSocket message handler:

```python
# In main.py, add to the message routing section:

elif msg_type == "dashboard.orchestrate_init":
    from .wrappers.dashboard_orchestration_handlers import handle_orchestrate_init
    response = await handle_orchestrate_init(
        uid=uid,
        session_id=session_id,
        payload=msg_payload
    )
    await ws.send_text(_json.dumps(response))
    logger.info(f"[WS] Orchestration init response sent - uid={uid}")

elif msg_type == "dashboard.company_change":
    from .wrappers.dashboard_orchestration_handlers import handle_company_change
    response = await handle_company_change(
        uid=uid,
        session_id=session_id,
        payload=msg_payload
    )
    await ws.send_text(_json.dumps(response))
    logger.info(f"[WS] Company change response sent - uid={uid}")

elif msg_type == "dashboard.refresh":
    from .wrappers.dashboard_orchestration_handlers import handle_refresh
    response = await handle_refresh(
        uid=uid,
        session_id=session_id,
        payload=msg_payload
    )
    await ws.send_text(_json.dumps(response))
    logger.info(f"[WS] Dashboard refresh response sent - uid={uid}")
```

---

## Testing

### Manual Testing with WebSocket Client

1. Authenticate and get session confirmed
2. Send orchestration init:
```json
{
  "type": "dashboard.orchestrate_init",
  "payload": {}
}
```

3. Observe events:
   - `dashboard.phase_start` (company)
   - `company.list`
   - `company.details`
   - `dashboard.phase_complete` (company)
   - `dashboard.phase_start` (data)
   - `dashboard.data_loading_progress`
   - `dashboard.full_data`
   - `dashboard.phase_complete` (data)
   - `dashboard.phase_start` (llm)
   - `llm.session_ready`
   - `dashboard.phase_complete` (llm)

### Testing Company Change

```json
{
  "type": "dashboard.company_change",
  "payload": {
    "company_id": "your-company-id"
  }
}
```

### Testing Refresh

```json
{
  "type": "dashboard.refresh",
  "payload": {}
}
```

---

## Error Handling

### Phase Failures

- **Company Phase**: Returns error, orchestration stops
- **Data Phase**: Partial data returned, widgets marked as error
- **LLM Phase**: Silent failure, no impact on dashboard

### Cancellation

Orchestrations can be cancelled by:
1. Starting a new orchestration (auto-cancels previous)
2. Company change (auto-cancels current)
3. Internal cancellation flag in Redis

---

## Dependencies

### Required Services (Existing)

| Service | Module | Function |
|---------|--------|----------|
| Firestore | `firebase_client` | `get_firestore()` |
| Redis | `redis_client` | `get_redis()` |
| WebSocket Hub | `ws_hub` | `hub.broadcast()` |
| Dashboard Handlers | `dashboard_handlers` | `get_dashboard_handlers()` |
| Session State Manager | `llm_service.session_state_manager` | `SessionStateManager()` |
| WS Events | `ws_events` | `WS_EVENTS` |

### No External Dependencies

This module uses only Python standard library and existing backend services.

---

## Logging

All operations are logged with the prefix `[ORCHESTRATION]`:

```
[ORCHESTRATION] Created: uid=xxx id=yyy
[ORCHESTRATION] Init requested: uid=xxx session=yyy
[ORCHESTRATION] Company change: uid=xxx company=yyy
[ORCHESTRATION] Refresh requested: uid=xxx
[ORCHESTRATION] Cancelled during company phase
[ORCHESTRATION] Completed successfully: uid=xxx orchestration_id=yyy
```

---

## Performance Considerations

### Caching Strategy

- Orchestration state: 1 hour TTL
- Company selection: 24 hours TTL
- Dashboard data: Handled by existing `dashboard_handlers` cache (60s TTL)

### Parallel Execution

- Phase 2 uses `dashboard_handlers.full_data()` which already fetches data in parallel
- Phase 3 runs in background (non-blocking)

### Cancellation Handling

- Checks for cancellation between phases
- Prevents multiple concurrent orchestrations per session
- Clean state transitions in Redis

---

## Future Enhancements

1. **Retry Logic**: Add automatic retry for failed phases
2. **Progress Tracking**: More granular progress updates per widget
3. **Priority Loading**: Load critical widgets first
4. **Partial Data Recovery**: Continue orchestration even if some widgets fail
5. **Metrics**: Track orchestration performance and success rates

---

## Contact

For questions or issues:
- Lead Migration Architect
- Backend Wrapper Architect
- Date: 2026-01-18
