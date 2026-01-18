# WebSocket Authentication Handler Implementation

## Overview

This implementation adds a WebSocket authentication handler to the Firebase microservice backend. The handler processes `auth.firebase_token` messages from the frontend and responds with `auth.session_confirmed` or `auth.login_error`.

## Architecture

### Wrapper Pattern

This implementation follows the **Wrapper/Facade Pattern** to maintain separation between new business logic and existing backend services:

```
Frontend (Next.js)
    ↓ WebSocket
    ↓ auth.firebase_token
    ↓
WebSocket Endpoint (main.py)
    ↓
Message Router (NEW)
    ↓
auth_handlers.py (NEW - Wrapper Layer)
    ↓
Existing Services (UNCHANGED)
    ├── firebase_client.py → Firebase Admin SDK
    └── redis_client.py → Redis connection
```

### Key Principle: Zero Modification to Existing Code

All new functionality is **ADDITIVE**:
- Created new `/app/wrappers/` directory
- Added message routing logic to existing WebSocket loop
- Existing services remain untouched and continue functioning as before

## Files Modified/Created

### Created Files

1. **`/app/wrappers/auth_handlers.py`** (368 lines)
   - Main authentication handler module
   - Functions:
     - `handle_firebase_token()`: Main handler for token verification
     - `get_session()`: Retrieve session from Redis
     - `update_session_activity()`: Update session last activity
     - `invalidate_session()`: Remove session from Redis

2. **`/app/wrappers/__init__.py`** (23 lines)
   - Module exports for clean imports

3. **`/app/wrappers/README.md`** (This file)
   - Implementation documentation

### Modified Files

1. **`/app/main.py`** (Lines 1851-1902)
   - Updated WebSocket message reception loop
   - Added JSON parsing and message routing
   - Integrated auth handler call
   - Added error handling for malformed messages

**Changes made:**
```python
# BEFORE (Line 1851-1853):
while True:
    # Lectures éventuellement inutilisées (backend peut ne rien envoyer)
    await ws.receive_text()

# AFTER (Lines 1851-1902):
while True:
    # Reception et traitement des messages WebSocket du client
    try:
        raw_message = await ws.receive_text()

        # Parse le message JSON
        try:
            message = _json.loads(raw_message)
            msg_type = message.get("type")
            msg_payload = message.get("payload", {})

            # Routage des messages vers les handlers appropriés
            if msg_type == "auth.firebase_token":
                from .wrappers.auth_handlers import handle_firebase_token
                response = await handle_firebase_token(msg_payload)
                await ws.send_text(_json.dumps(response))
            # ... error handling
```

## Message Flow

### 1. Frontend Sends Authentication Request

```json
{
  "type": "auth.firebase_token",
  "payload": {
    "token": "eyJhbGciOiJSUzI1NiIs...",
    "uid": "abc123xyz",
    "email": "user@gmail.com",
    "displayName": "John Doe",
    "photoURL": "https://...",
    "sessionId": "uuid-session-id"
  }
}
```

### 2. Backend Processing Steps

1. **Message Reception** (main.py:1854)
   - WebSocket receives raw text message

2. **JSON Parsing** (main.py:1858)
   - Parse JSON and extract type and payload

3. **Message Routing** (main.py:1865)
   - Route to appropriate handler based on message type

4. **Token Verification** (auth_handlers.py:89)
   - Call `firebase_admin.auth.verify_id_token()`
   - Verify UID consistency

5. **Session Creation** (auth_handlers.py:120)
   - Create session data structure
   - Store in Redis with key: `session:{uid}:{session_id}`
   - Set TTL to 3600 seconds (1 hour)

6. **Response Generation** (auth_handlers.py:136)
   - Build success or error response

7. **Send Response** (main.py:1871)
   - Send JSON response back to client via WebSocket

### 3. Backend Response (Success)

```json
{
  "type": "auth.session_confirmed",
  "payload": {
    "success": true,
    "sessionId": "uuid-session-id",
    "user": {
      "id": "abc123xyz",
      "email": "user@gmail.com",
      "displayName": "John Doe",
      "photoURL": "https://...",
      "emailVerified": true
    },
    "permissions": ["read", "write"]
  }
}
```

### 4. Backend Response (Error)

```json
{
  "type": "auth.login_error",
  "payload": {
    "success": false,
    "error": "Invalid Firebase token",
    "code": "AUTH_FAILED"
  }
}
```

## Redis Session Structure

Sessions are stored in Redis with the following structure:

**Key Format:** `session:{uid}:{session_id}`

**Value (JSON):**
```json
{
  "token": "eyJhbGciOiJSUzI1NiIs...",
  "user": {
    "id": "abc123xyz",
    "email": "user@gmail.com",
    "displayName": "John Doe",
    "photoURL": "https://...",
    "emailVerified": true
  },
  "auth_provider": "google",
  "created_at": "2026-01-17T22:30:00.000Z",
  "last_activity": "2026-01-17T22:30:00.000Z"
}
```

**TTL:** 3600 seconds (1 hour)

## Error Handling

### Token Validation Errors

1. **Invalid Token**
   - Cause: Malformed or tampered token
   - Response: `auth.login_error` with code `AUTH_FAILED`

2. **Expired Token**
   - Cause: Token past expiration time
   - Response: `auth.login_error` with code `AUTH_FAILED`

3. **UID Mismatch**
   - Cause: Token UID doesn't match payload UID
   - Response: `auth.login_error` with code `AUTH_FAILED`

### System Errors

1. **Redis Connection Error**
   - Cause: Cannot connect to Redis
   - Response: `auth.login_error` with code `INTERNAL_ERROR`

2. **Firebase Admin SDK Error**
   - Cause: Firebase service unavailable
   - Response: `auth.login_error` with code `INTERNAL_ERROR`

### Message Parsing Errors

1. **Invalid JSON**
   - Cause: Malformed JSON in WebSocket message
   - Response: `error` with code `PARSE_ERROR`

## Logging

All operations are logged with structured information:

```
[AUTH] Processing Firebase token for uid=abc123xyz session=uuid-session-id
[AUTH] Token verified successfully for uid=abc123xyz
[AUTH] Session created in Redis - key=session:abc123xyz:uuid-session-id ttl=3600s
[AUTH] Authentication successful - uid=abc123xyz session=uuid-session-id
[WS] Auth response sent - uid=abc123xyz type=auth.session_confirmed success=True
```

Error logging includes stack traces:

```
[AUTH] Invalid Firebase token for uid=abc123xyz: Token signature invalid
[AUTH] Authentication failed: Invalid Firebase token
[WS] Auth response sent - uid=abc123xyz type=auth.login_error success=False
```

## Testing

### Manual Testing Steps

1. **Start Backend Server**
   ```bash
   cd /c/Users/Cedri/Coding/firebase_microservice
   python -m app.main
   ```

2. **Connect WebSocket**
   - Frontend should connect to `ws://localhost:8000/ws?uid={firebase_uid}`

3. **Send Authentication Message**
   ```javascript
   ws.send(JSON.stringify({
     type: "auth.firebase_token",
     payload: {
       token: "valid_firebase_token",
       uid: "user_firebase_uid",
       email: "user@example.com",
       displayName: "Test User",
       sessionId: "test-session-123"
     }
   }));
   ```

4. **Verify Response**
   - Should receive `auth.session_confirmed` with user data
   - Check Redis: `redis-cli GET session:user_firebase_uid:test-session-123`

### Unit Testing

Create test file: `/c/Users/Cedri/Coding/firebase_microservice/test_auth_handler.py`

```python
import pytest
import json
from app.wrappers.auth_handlers import handle_firebase_token

@pytest.mark.asyncio
async def test_handle_firebase_token_success():
    """Test successful token verification."""
    payload = {
        "token": "valid_test_token",
        "uid": "test_uid",
        "email": "test@example.com",
        "sessionId": "test_session"
    }

    # Mock Firebase and Redis services here
    response = await handle_firebase_token(payload)

    assert response["type"] == "auth.session_confirmed"
    assert response["payload"]["success"] is True
    assert response["payload"]["user"]["id"] == "test_uid"
```

## Dependencies

### Existing Services (No Changes Required)

1. **Firebase Admin SDK**
   - File: `app/firebase_client.py`
   - Function: `get_firebase_app()`
   - Used for: Token verification

2. **Redis Client**
   - File: `app/redis_client.py`
   - Function: `get_redis()`
   - Used for: Session storage

3. **WebSocket Events**
   - File: `app/ws_events.py`
   - Constants: `WS_EVENTS.AUTH.*`
   - Used for: Event type definitions

### Environment Variables Required

```bash
# Firebase Configuration
FIREBASE_ADMIN_JSON='{...}'  # Service account JSON
# OR
FIREBASE_ADMIN_SECRET_NAME=pinnokio-listeners-firebase-admin

# Redis Configuration
LISTENERS_REDIS_HOST=127.0.0.1
LISTENERS_REDIS_PORT=6379
LISTENERS_REDIS_PASSWORD=  # Optional
LISTENERS_REDIS_DB=0
LISTENERS_REDIS_TLS=false
```

## Future Extensions

This message routing architecture supports easy addition of new handlers:

```python
# In main.py WebSocket loop:
if msg_type == "auth.firebase_token":
    from .wrappers.auth_handlers import handle_firebase_token
    response = await handle_firebase_token(msg_payload)
    await ws.send_text(_json.dumps(response))

elif msg_type == "auth.logout":  # NEW
    from .wrappers.auth_handlers import handle_logout
    response = await handle_logout(msg_payload)
    await ws.send_text(_json.dumps(response))

elif msg_type == "company.select":  # NEW
    from .wrappers.company_handlers import handle_company_select
    response = await handle_company_select(msg_payload)
    await ws.send_text(_json.dumps(response))
```

## Security Considerations

1. **Token Verification**
   - All tokens verified via Firebase Admin SDK
   - Signature validation ensures token authenticity
   - Expiration checked automatically

2. **Session Security**
   - Sessions expire after 1 hour
   - Stored in Redis with proper TTL
   - UID and session ID must match for access

3. **Error Messages**
   - Generic error messages to prevent information leakage
   - Detailed errors logged server-side only

4. **Input Validation**
   - All required fields validated before processing
   - JSON parsing errors handled gracefully

## Troubleshooting

### Frontend Timeout

**Symptom:** Frontend shows "Session confirmation timeout"

**Solutions:**
1. Check backend logs for authentication errors
2. Verify Firebase token is valid
3. Ensure Redis is running and accessible
4. Check WebSocket connection is established

### Token Verification Fails

**Symptom:** Receive `auth.login_error` response

**Solutions:**
1. Verify Firebase service account JSON is correct
2. Check token hasn't expired (lifetime: 1 hour)
3. Ensure UID in payload matches token UID
4. Verify Firebase project ID matches

### Session Not Found in Redis

**Symptom:** Session created but not retrievable

**Solutions:**
1. Check Redis connection settings
2. Verify Redis server is running
3. Check session hasn't expired (TTL: 1 hour)
4. Ensure correct session key format

## Contact

For questions or issues with this implementation:
- Review this documentation
- Check backend logs at `/app/main.py`
- Examine wrapper code at `/app/wrappers/auth_handlers.py`

## Changelog

### 2026-01-17 - Initial Implementation
- Created `auth_handlers.py` wrapper module
- Added message routing to WebSocket endpoint
- Implemented session storage in Redis
- Added comprehensive error handling and logging
