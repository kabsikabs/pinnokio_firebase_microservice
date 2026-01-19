"""
WebSocket Authentication Handlers - Wrapper Layer
==================================================

This module provides wrapper handlers for WebSocket authentication events.
It encapsulates the business logic for Firebase token verification and session
management, which was previously handled by the frontend.

CRITICAL: This is ADDITIVE code - it wraps existing Firebase and Redis services
without modifying them.

Architecture:
    Frontend → WebSocket → auth_handlers.py → Existing Services

Events Handled:
    - auth.firebase_token: Verify Firebase ID token and create session

Dependencies (Existing Services - DO NOT MODIFY):
    - firebase_client.get_firebase_app(): Firebase Admin SDK
    - redis_client.get_redis(): Redis connection
    - ws_events.WS_EVENTS: Event type constants

Author: Backend Wrapper Agent
Created: 2026-01-17
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Import existing services (READ-ONLY)
import firebase_admin.auth
from firebase_admin import auth as firebase_auth

from app.firebase_client import get_firebase_app
from app.redis_client import get_redis
from app.ws_events import WS_EVENTS

logger = logging.getLogger("auth_handlers")


class AuthenticationError(Exception):
    """Custom exception for authentication failures."""
    pass


async def handle_firebase_token(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle auth.firebase_token event from frontend.

    This wrapper function verifies a Firebase ID token and creates a session
    in Redis. It orchestrates existing Firebase and Redis services without
    modifying their behavior.

    Flow:
        1. Extract token and user info from payload
        2. Verify token using Firebase Admin SDK (existing service)
        3. Validate UID consistency
        4. Create session in Redis with 1-hour TTL
        5. Return success response

    Args:
        payload: WebSocket message payload containing:
            - token (str): Firebase ID token
            - uid (str): User ID to verify
            - email (str): User email
            - displayName (str, optional): User display name
            - photoURL (str, optional): User photo URL
            - sessionId (str): Session identifier

    Returns:
        Dict containing response type and payload:
            - type: "auth.session_confirmed" on success
            - type: "auth.login_error" on failure
            - payload: Session details or error message

    Raises:
        AuthenticationError: On validation or verification failures
    """
    try:
        # 1. Extract required fields
        token = payload.get("token")
        uid = payload.get("uid")
        email = payload.get("email")
        session_id = payload.get("sessionId")

        # Validate required fields
        if not token:
            raise AuthenticationError("Missing Firebase token")
        if not uid:
            raise AuthenticationError("Missing user ID")
        if not session_id:
            raise AuthenticationError("Missing session ID")

        logger.info(f"[AUTH] Processing Firebase token for uid={uid} session={session_id}")

        # 2. Verify token using existing Firebase Admin SDK
        # clock_skew_seconds=5 allows for minor clock differences between client and server
        # This prevents "Token used too early" errors when clocks are slightly out of sync
        try:
            # Call existing Firebase service (READ-ONLY operation)
            firebase_app = get_firebase_app()
            decoded_token = firebase_auth.verify_id_token(
                token,
                app=firebase_app,
                clock_skew_seconds=5  # Allow 5 seconds tolerance for clock skew
            )

            logger.info(f"[AUTH] Token verified successfully for uid={uid}")

        except firebase_admin.auth.InvalidIdTokenError as e:
            error_msg = str(e)
            # Check if this is a clock skew issue (Token used too early)
            if "Token used too early" in error_msg:
                logger.warning(f"[AUTH] Clock skew detected for uid={uid}: {e}")
                # Log additional info for debugging
                logger.warning(f"[AUTH] This may indicate server clock is behind. Consider NTP sync.")
            logger.error(f"[AUTH] Invalid Firebase token for uid={uid}: {e}")
            raise AuthenticationError("Invalid Firebase token")
        except firebase_admin.auth.ExpiredIdTokenError as e:
            logger.error(f"[AUTH] Expired Firebase token for uid={uid}: {e}")
            raise AuthenticationError("Firebase token expired")
        except Exception as e:
            logger.error(f"[AUTH] Firebase token verification failed for uid={uid}: {e}")
            raise AuthenticationError(f"Token verification failed: {str(e)}")

        # 3. Validate UID consistency
        verified_uid = decoded_token.get("uid")
        if verified_uid != uid:
            logger.error(
                f"[AUTH] UID mismatch - provided: {uid}, verified: {verified_uid}"
            )
            raise AuthenticationError("UID mismatch between token and payload")

        # Extract additional user info from token
        email_verified = decoded_token.get("email_verified", False)

        # 4. Create session data structure
        session_data = {
            "token": token,
            "user": {
                "id": uid,
                "email": email,
                "displayName": payload.get("displayName", ""),
                "photoURL": payload.get("photoURL", ""),
                "emailVerified": email_verified
            },
            "auth_provider": "google",  # Since frontend uses Google OAuth
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_activity": datetime.now(timezone.utc).isoformat()
        }

        # 5. Store session in Redis using existing service
        try:
            redis_client = get_redis()
            session_key = f"session:{uid}:{session_id}"
            session_ttl = 3600  # 1 hour in seconds

            # Serialize session data to JSON
            session_json = json.dumps(session_data)

            # Store in Redis with TTL (existing service, READ-ONLY usage)
            redis_client.setex(session_key, session_ttl, session_json)

            logger.info(
                f"[AUTH] Session created in Redis - key={session_key} ttl={session_ttl}s"
            )

        except Exception as e:
            logger.error(f"[AUTH] Redis session creation failed for uid={uid}: {e}")
            raise AuthenticationError(f"Session storage failed: {str(e)}")

        # 6. Return success response using standardized event types
        response = {
            "type": WS_EVENTS.AUTH.SESSION_CONFIRMED,
            "payload": {
                "success": True,
                "sessionId": session_id,
                "user": session_data["user"],
                "permissions": ["read", "write"]  # Basic permissions
            }
        }

        logger.info(
            f"[AUTH] Authentication successful - uid={uid} session={session_id}"
        )

        return response

    except AuthenticationError as e:
        # Handle known authentication errors
        logger.warning(f"[AUTH] Authentication failed: {e}")
        return {
            "type": WS_EVENTS.AUTH.LOGIN_ERROR,
            "payload": {
                "success": False,
                "error": str(e),
                "code": "AUTH_FAILED"
            }
        }

    except Exception as e:
        # Handle unexpected errors
        logger.error(f"[AUTH] Unexpected error during authentication: {e}", exc_info=True)
        return {
            "type": WS_EVENTS.AUTH.LOGIN_ERROR,
            "payload": {
                "success": False,
                "error": "Internal authentication error",
                "code": "INTERNAL_ERROR"
            }
        }


async def get_session(uid: str, session_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve session data from Redis.

    Helper function to fetch existing session data. Uses existing Redis service
    without modification.

    Args:
        uid: User ID
        session_id: Session identifier

    Returns:
        Session data dict if found, None otherwise
    """
    try:
        redis_client = get_redis()
        session_key = f"session:{uid}:{session_id}"

        session_json = redis_client.get(session_key)
        if session_json:
            return json.loads(session_json)

        return None

    except Exception as e:
        logger.error(f"[AUTH] Failed to retrieve session {session_id} for {uid}: {e}")
        return None


async def update_session_activity(uid: str, session_id: str) -> bool:
    """
    Update last activity timestamp for a session.

    Wrapper to extend session lifetime on user activity. Uses existing Redis
    service without modification.

    Args:
        uid: User ID
        session_id: Session identifier

    Returns:
        True if updated successfully, False otherwise
    """
    try:
        redis_client = get_redis()
        session_key = f"session:{uid}:{session_id}"

        # Get existing session
        session_json = redis_client.get(session_key)
        if not session_json:
            logger.warning(f"[AUTH] Session not found for update: {session_key}")
            return False

        # Update last activity
        session_data = json.loads(session_json)
        session_data["last_activity"] = datetime.now(timezone.utc).isoformat()

        # Store back with refreshed TTL
        session_ttl = 3600  # 1 hour
        redis_client.setex(session_key, session_ttl, json.dumps(session_data))

        logger.debug(f"[AUTH] Session activity updated: {session_key}")
        return True

    except Exception as e:
        logger.error(f"[AUTH] Failed to update session activity: {e}")
        return False


async def invalidate_session(uid: str, session_id: str) -> bool:
    """
    Invalidate a session by removing it from Redis.

    Wrapper for logout or session cleanup. Uses existing Redis service.

    Args:
        uid: User ID
        session_id: Session identifier

    Returns:
        True if invalidated successfully, False otherwise
    """
    try:
        redis_client = get_redis()
        session_key = f"session:{uid}:{session_id}"

        deleted = redis_client.delete(session_key)

        if deleted:
            logger.info(f"[AUTH] Session invalidated: {session_key}")
            return True
        else:
            logger.warning(f"[AUTH] Session not found for invalidation: {session_key}")
            return False

    except Exception as e:
        logger.error(f"[AUTH] Failed to invalidate session: {e}")
        return False


# Export public API
__all__ = [
    "handle_firebase_token",
    "get_session",
    "update_session_activity",
    "invalidate_session",
    "AuthenticationError"
]
