# Quick Start Guide - WebSocket Authentication for Frontend

## TL;DR

The backend now responds to `auth.firebase_token` messages. Just send the message and handle the response.

## 30-Second Integration

### 1. Send Authentication Message

```typescript
import { v4 as uuidv4 } from 'uuid';

// After Google OAuth success
const sessionId = uuidv4();
const token = await firebaseUser.getIdToken();

ws.send(JSON.stringify({
  type: "auth.firebase_token",
  payload: {
    token: token,
    uid: firebaseUser.uid,
    email: firebaseUser.email,
    displayName: firebaseUser.displayName,
    photoURL: firebaseUser.photoURL,
    sessionId: sessionId
  }
}));
```

### 2. Handle Response

```typescript
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);

  if (message.type === "auth.session_confirmed") {
    // ✅ SUCCESS
    console.log("Authenticated:", message.payload.user);
    // Save session ID for later use
    localStorage.setItem('sessionId', message.payload.sessionId);
  }

  else if (message.type === "auth.login_error") {
    // ❌ ERROR
    console.error("Auth failed:", message.payload.error);
  }
};
```

## That's It!

The backend now handles:
- Token verification
- Session creation in Redis
- Error handling
- Response formatting

## Need More Details?

See **WEBSOCKET_AUTH_IMPLEMENTATION.md** for complete documentation.

## Testing Locally

1. Start backend: `python -m app.main`
2. Connect WebSocket: `ws://localhost:8000/ws?uid={uid}`
3. Send auth message
4. Receive response within ~500ms

## Production URLs

Update WebSocket URL to your production backend:
```typescript
const wsUrl = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws';
```

## Questions?

Check the logs:
- Frontend: Browser console
- Backend: Terminal running `python -m app.main`
