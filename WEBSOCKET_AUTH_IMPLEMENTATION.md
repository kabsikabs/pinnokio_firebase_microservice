# WebSocket Authentication Handler - Implementation Summary

**Date:** 2026-01-17
**Backend Location:** `C:\Users\Cedri\Coding\firebase_microservice\app`
**Status:** ✅ Implemented and Ready for Testing

---

## What Was Implemented

The backend now **processes** WebSocket messages from the frontend and specifically handles the `auth.firebase_token` event for OAuth authentication flow.

### Problem Solved

**Before:**
- Backend WebSocket received messages but didn't process them
- Frontend sent `auth.firebase_token` → backend read it but ignored it → frontend timeout

**After:**
- Backend WebSocket receives messages → parses JSON → routes to handler → verifies token → creates session → responds
- Frontend sends `auth.firebase_token` → backend processes → returns `auth.session_confirmed` ✅

---

## Message Contract

### Frontend → Backend

**Event Type:** `auth.firebase_token`

```json
{
  "type": "auth.firebase_token",
  "payload": {
    "token": "eyJhbGciOiJSUzI1NiIs...",
    "uid": "user_firebase_uid",
    "email": "user@example.com",
    "displayName": "John Doe",
    "photoURL": "https://...",
    "sessionId": "unique_session_id"
  }
}
```

**Required Fields:**
- `token`: Firebase ID token from `getIdToken()`
- `uid`: Firebase user UID
- `sessionId`: Unique session identifier (generate with UUID)

**Optional Fields:**
- `email`: User email
- `displayName`: User display name
- `photoURL`: User photo URL

### Backend → Frontend (Success)

**Event Type:** `auth.session_confirmed`

```json
{
  "type": "auth.session_confirmed",
  "payload": {
    "success": true,
    "sessionId": "unique_session_id",
    "user": {
      "id": "user_firebase_uid",
      "email": "user@example.com",
      "displayName": "John Doe",
      "photoURL": "https://...",
      "emailVerified": true
    },
    "permissions": ["read", "write"]
  }
}
```

### Backend → Frontend (Error)

**Event Type:** `auth.login_error`

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

**Error Codes:**
- `AUTH_FAILED`: Token invalid, expired, or UID mismatch
- `INTERNAL_ERROR`: Backend service error (Redis/Firebase)
- `PARSE_ERROR`: Malformed JSON message

---

## Backend Architecture

### New Files Created

```
firebase_microservice/app/
├── wrappers/                          ← NEW DIRECTORY
│   ├── __init__.py                   ← Module exports
│   ├── auth_handlers.py              ← Authentication handler (368 lines)
│   └── README.md                     ← Implementation documentation
```

### Modified Files

```
firebase_microservice/app/
└── main.py                            ← Modified WebSocket message loop
    Lines 1851-1902: Added message parsing and routing
```

### Integration Point

**File:** `app/main.py`
**Function:** `websocket_endpoint()`
**Lines:** 1851-1902

**What Changed:**
```python
# OLD: Just read messages, don't process
while True:
    await ws.receive_text()

# NEW: Read, parse, route, and respond
while True:
    raw_message = await ws.receive_text()
    message = _json.loads(raw_message)

    if message["type"] == "auth.firebase_token":
        from .wrappers.auth_handlers import handle_firebase_token
        response = await handle_firebase_token(message["payload"])
        await ws.send_text(_json.dumps(response))
```

---

## Session Management

### Redis Storage

Sessions are stored in Redis with the following structure:

**Key:** `session:{uid}:{session_id}`

**Value:**
```json
{
  "token": "firebase_id_token",
  "user": {
    "id": "user_firebase_uid",
    "email": "user@example.com",
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

### Session Lifecycle

1. **Creation:** On successful `auth.firebase_token` processing
2. **Validation:** On each subsequent WebSocket message (future)
3. **Refresh:** TTL refreshed on activity updates
4. **Expiration:** Automatic after 1 hour of inactivity
5. **Invalidation:** Manual on logout

---

## Frontend Integration Checklist

### 1. WebSocket Connection

Ensure WebSocket connects with UID parameter:

```typescript
const ws = new WebSocket(`ws://backend-url/ws?uid=${firebaseUser.uid}`);
```

### 2. Send Authentication Message

After Google OAuth success:

```typescript
import { v4 as uuidv4 } from 'uuid';

const sessionId = uuidv4();
const idToken = await user.getIdToken();

const authMessage = {
  type: "auth.firebase_token",
  payload: {
    token: idToken,
    uid: user.uid,
    email: user.email,
    displayName: user.displayName,
    photoURL: user.photoURL,
    sessionId: sessionId
  }
};

ws.send(JSON.stringify(authMessage));
```

### 3. Handle Response

Listen for backend response:

```typescript
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);

  if (message.type === "auth.session_confirmed") {
    // Success - session is active
    console.log("Session confirmed:", message.payload);
    setAuthState({
      isAuthenticated: true,
      user: message.payload.user,
      sessionId: message.payload.sessionId
    });
  }

  else if (message.type === "auth.login_error") {
    // Error - show error to user
    console.error("Auth error:", message.payload.error);
    setError(message.payload.error);
  }
};
```

### 4. Store Session ID

Save the session ID for subsequent requests:

```typescript
// In your auth store (Zustand example)
setSessionId(message.payload.sessionId);
localStorage.setItem('sessionId', message.payload.sessionId);
```

---

## Testing

### Test Script Provided

**Location:** `/c/Users/Cedri/Coding/firebase_microservice/test_auth_websocket.py`

**Usage:**
```bash
# Start backend server first
cd /c/Users/Cedri/Coding/firebase_microservice
python -m app.main

# In another terminal, run test
python test_auth_websocket.py
```

**Expected Output (Dummy Token):**
```
✓ Connected successfully
✓ Message sent
✓ Response received
❌ FAILED: Authentication error
   Error: Invalid Firebase token
   Code: AUTH_FAILED
```

This is expected because the test uses a dummy token. The important part is:
- ✅ Connection succeeds
- ✅ Message is sent
- ✅ Response is received
- ✅ Handler processes the request

### Testing with Real Token

For full integration testing with a real Firebase token:

1. Get a real Firebase ID token from your frontend console:
   ```javascript
   const token = await firebase.auth().currentUser.getIdToken();
   console.log(token);
   ```

2. Update `test_auth_websocket.py` → `test_with_real_token()` function
3. Replace dummy values with real token, UID, email
4. Run the test

**Expected Output (Real Token):**
```
✓ Connected successfully
✓ Message sent
✓ Response received
✅ SUCCESS: Authentication confirmed!
   User ID: abc123xyz
   Session ID: test_session_real
   Permissions: ['read', 'write']
```

---

## Error Handling

### Common Errors and Solutions

#### 1. WebSocket Connection Timeout

**Symptom:** Frontend can't connect to WebSocket

**Solutions:**
- ✅ Backend server is running
- ✅ WebSocket URL is correct
- ✅ UID parameter is included in URL
- ✅ Firewall allows WebSocket connections

#### 2. Authentication Timeout

**Symptom:** No response received after 5 seconds

**Solutions:**
- ✅ Message format is correct (check JSON structure)
- ✅ Token is valid and not expired
- ✅ Backend logs show message received
- ✅ Redis is running and accessible

#### 3. Token Verification Fails

**Symptom:** Receive `auth.login_error` with AUTH_FAILED

**Causes:**
- Token is expired (Firebase tokens expire after 1 hour)
- Token signature invalid (network corruption)
- UID mismatch (token UID ≠ payload UID)
- Firebase credentials not configured on backend

**Solutions:**
- Get fresh token with `getIdToken(forceRefresh: true)`
- Verify UID matches between token and payload
- Check backend has correct Firebase credentials

#### 4. Session Not Created

**Symptom:** Auth succeeds but session not in Redis

**Solutions:**
- ✅ Redis server is running
- ✅ Backend can connect to Redis (check logs)
- ✅ No Redis connection errors in backend logs

---

## Backend Logs

### Success Flow

```
[WS] Message reçu - uid=abc123xyz type=auth.firebase_token
[AUTH] Processing Firebase token for uid=abc123xyz session=session_123
[AUTH] Token verified successfully for uid=abc123xyz
[AUTH] Session created in Redis - key=session:abc123xyz:session_123 ttl=3600s
[AUTH] Authentication successful - uid=abc123xyz session=session_123
[WS] Auth response sent - uid=abc123xyz type=auth.session_confirmed success=True
```

### Error Flow

```
[WS] Message reçu - uid=abc123xyz type=auth.firebase_token
[AUTH] Processing Firebase token for uid=abc123xyz session=session_123
[AUTH] Invalid Firebase token for uid=abc123xyz: Token expired
[AUTH] Authentication failed: Firebase token expired
[WS] Auth response sent - uid=abc123xyz type=auth.login_error success=False
```

---

## Environment Variables

### Required for Backend

```bash
# Firebase Configuration
FIREBASE_ADMIN_JSON='{...}'  # Service account JSON
# OR
FIREBASE_ADMIN_SECRET_NAME=pinnokio-listeners-firebase-admin

# Redis Configuration
LISTENERS_REDIS_HOST=127.0.0.1
LISTENERS_REDIS_PORT=6379
LISTENERS_REDIS_PASSWORD=         # Optional
LISTENERS_REDIS_DB=0
LISTENERS_REDIS_TLS=false
```

---

## Next Steps for Frontend Team

1. **Update WebSocket Connection Code**
   - Add `sessionId` generation (UUID)
   - Include all required fields in `auth.firebase_token` payload

2. **Update Message Handler**
   - Listen for `auth.session_confirmed` response
   - Handle `auth.login_error` with user-friendly messages

3. **Update Auth Store**
   - Store `sessionId` received from backend
   - Use session ID for subsequent authenticated requests

4. **Test Integration**
   - Test with real Firebase credentials
   - Verify session appears in Redis
   - Test token expiration handling
   - Test error scenarios (invalid token, network errors)

5. **Production Deployment**
   - Update WebSocket URL to production backend
   - Ensure environment variables are set
   - Monitor backend logs for auth events

---

## Support and Documentation

### Documentation Files

1. **Implementation Details:**
   `/c/Users/Cedri/Coding/firebase_microservice/app/wrappers/README.md`

2. **Handler Source Code:**
   `/c/Users/Cedri/Coding/firebase_microservice/app/wrappers/auth_handlers.py`

3. **Test Script:**
   `/c/Users/Cedri/Coding/firebase_microservice/test_auth_websocket.py`

### Code References

- **WebSocket Endpoint:** `app/main.py` lines 1775-1902
- **Auth Handler:** `app/wrappers/auth_handlers.py` line 44-153
- **Event Constants:** `app/ws_events.py` lines 45-54

---

## Architecture Principles Applied

This implementation follows the **Backend Wrapper Architecture** principles:

✅ **ADDITIVE ONLY** - No existing code was modified (except WebSocket loop)
✅ **WRAPPER PATTERN** - New logic wraps existing services
✅ **READ-ONLY USAGE** - Existing services used as-is
✅ **CLEAR SEPARATION** - Wrapper code in dedicated `/wrappers` directory
✅ **COMPREHENSIVE DOCS** - Full documentation and test scripts provided

---

## Summary

The WebSocket authentication handler is **fully implemented and ready for integration**. The backend now:

- ✅ Receives `auth.firebase_token` messages
- ✅ Verifies Firebase ID tokens
- ✅ Creates sessions in Redis
- ✅ Responds with `auth.session_confirmed` or `auth.login_error`
- ✅ Provides detailed logging for debugging
- ✅ Handles all error cases gracefully

The frontend needs to:

- 📝 Update WebSocket message sending to include all required fields
- 📝 Handle `auth.session_confirmed` response
- 📝 Store and use session ID for authenticated requests
- 📝 Test integration with real Firebase tokens

**Implementation Status:** ✅ Complete
**Testing Status:** ⏳ Awaiting Frontend Integration
**Production Ready:** ✅ Yes (after integration testing)
