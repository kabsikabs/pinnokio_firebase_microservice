"""
Pending Action Manager - Manages external redirect flows.
==========================================================

This manager handles state preservation during external redirects:
- OAuth flows (Google, Microsoft, etc.)
- Payment flows (Stripe, PayPal, etc.)
- Email verification flows
- Any external service requiring redirect

Architecture:
    Frontend -> save pending_action -> redirect to external
    External -> callback -> get pending_action -> restore state

Redis Key Pattern:
    pending_action:{uid}:{session_id}

TTL: 15 minutes (sufficient for OAuth/payment flows)

Security:
    - State token for CSRF protection
    - One-time use (deleted after retrieval)
    - Short TTL to prevent replay attacks

Usage:
    # Before redirect
    manager = get_pending_action_manager()
    state_token = manager.save_pending_action(
        uid="user123",
        session_id="sess456",
        action_type="oauth",
        provider="google_drive",
        return_page="settings",
        return_path="/settings?tab=integrations",
        context={"scope": "drive.readonly"}
    )
    redirect_url = f"https://oauth.google.com/...&state={uid}:{session_id}:{state_token}"

    # After callback
    action = manager.complete_pending_action(uid, session_id, state_token)
    if action:
        # Process OAuth tokens
        # Redirect to action["return_path"]

Author: Migration Team
Created: 2026-01-19
"""

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..redis_client import get_redis

logger = logging.getLogger("pending_action")

# =============================================================================
# CONSTANTS
# =============================================================================

PENDING_ACTION_TTL = 900  # 15 minutes
PENDING_ACTION_PREFIX = "pending_action"

# Valid action types
VALID_ACTION_TYPES = [
    "oauth",
    "payment",
    "verification",
    "subscription",
    "connect_service",
]

# Valid providers
VALID_PROVIDERS = [
    # OAuth providers
    "google",
    "google_drive",
    "microsoft",
    "microsoft_365",
    "github",
    "slack",
    # Payment providers
    "stripe",
    "paypal",
    # Verification
    "email",
    "phone",
    # ERP/Services
    "erp_connect",
    "bank_connect",
]


# =============================================================================
# PENDING ACTION MANAGER
# =============================================================================

class PendingActionManager:
    """
    Manages pending external actions (OAuth, payments, etc.).

    This enables:
    - State preservation during external redirects
    - CSRF protection via state tokens
    - Automatic cleanup via TTL
    - One-time use for security
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def _build_key(self, uid: str, session_id: str) -> str:
        """Build Redis key for pending action."""
        return f"{PENDING_ACTION_PREFIX}:{uid}:{session_id}"

    def _generate_state_token(self) -> str:
        """Generate a secure random state token."""
        return secrets.token_urlsafe(32)

    # =========================================================================
    # SAVE OPERATIONS
    # =========================================================================

    def save_pending_action(
        self,
        uid: str,
        session_id: str,
        action_type: str,
        provider: str,
        return_page: str,
        return_path: str,
        context: Optional[Dict[str, Any]] = None,
        ttl: int = PENDING_ACTION_TTL
    ) -> str:
        """
        Save pending action and return state token.

        Args:
            uid: Firebase user ID
            session_id: WebSocket session ID
            action_type: Type of action (oauth, payment, verification)
            provider: External provider (google, stripe, etc.)
            return_page: Page to return to (for page_state invalidation)
            return_path: Full path with query params for redirect
            context: Additional context data
            ttl: Time-to-live in seconds

        Returns:
            State token for CSRF validation

        Raises:
            ValueError: If action_type or provider is invalid
        """
        if action_type not in VALID_ACTION_TYPES:
            raise ValueError(f"Invalid action_type: {action_type}")

        if provider not in VALID_PROVIDERS:
            logger.warning(f"[PENDING_ACTION] Unknown provider: {provider}")
            # Don't raise - allow unknown providers for flexibility

        key = self._build_key(uid, session_id)
        state_token = self._generate_state_token()
        now = datetime.now(timezone.utc)

        action = {
            "action_type": action_type,
            "provider": provider,
            "return_page": return_page,
            "return_path": return_path,
            "context": context or {},
            "initiated_at": now.isoformat(),
            "state_token": state_token,
            "uid": uid,
            "session_id": session_id,
        }

        try:
            self.redis.setex(key, ttl, json.dumps(action))
            logger.info(
                f"[PENDING_ACTION] Saved: action={action_type} provider={provider} "
                f"uid={uid} return_page={return_page}"
            )
            return state_token
        except Exception as e:
            logger.error(f"[PENDING_ACTION] Save error: {e}", exc_info=True)
            raise

    # =========================================================================
    # GET OPERATIONS
    # =========================================================================

    def get_pending_action(
        self,
        uid: str,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get pending action without deleting it.
        Use for validation before processing.
        """
        key = self._build_key(uid, session_id)

        try:
            raw = self.redis.get(key)
            if not raw:
                logger.info(f"[PENDING_ACTION] Not found: uid={uid}")
                return None

            action = json.loads(raw if isinstance(raw, str) else raw.decode())
            logger.info(
                f"[PENDING_ACTION] Found: action={action.get('action_type')} "
                f"provider={action.get('provider')}"
            )
            return action

        except Exception as e:
            logger.error(f"[PENDING_ACTION] Get error: {e}", exc_info=True)
            return None

    def validate_state_token(
        self,
        uid: str,
        session_id: str,
        state_token: str
    ) -> bool:
        """
        Validate state token matches pending action.
        Does NOT delete the action - use complete_pending_action for that.
        """
        action = self.get_pending_action(uid, session_id)
        if not action:
            return False
        return action.get("state_token") == state_token

    # =========================================================================
    # COMPLETE OPERATIONS
    # =========================================================================

    def complete_pending_action(
        self,
        uid: str,
        session_id: str,
        state_token: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get pending action and delete it (one-time use).

        Args:
            uid: Firebase user ID
            session_id: WebSocket session ID
            state_token: Optional state token for validation

        Returns:
            The pending action data, or None if not found/invalid
        """
        action = self.get_pending_action(uid, session_id)

        if not action:
            logger.warning(f"[PENDING_ACTION] Complete failed - not found: uid={uid}")
            return None

        # Validate state token if provided
        if state_token and action.get("state_token") != state_token:
            logger.warning(
                f"[PENDING_ACTION] Complete failed - invalid state token: uid={uid}"
            )
            return None

        # Delete the action (one-time use)
        key = self._build_key(uid, session_id)
        try:
            self.redis.delete(key)
            logger.info(
                f"[PENDING_ACTION] Completed: action={action.get('action_type')} "
                f"provider={action.get('provider')} uid={uid}"
            )
            return action
        except Exception as e:
            logger.error(f"[PENDING_ACTION] Delete error: {e}", exc_info=True)
            return action  # Return action even if delete fails

    # =========================================================================
    # CANCEL OPERATIONS
    # =========================================================================

    def cancel_pending_action(self, uid: str, session_id: str) -> bool:
        """
        Cancel/delete a pending action without completing it.
        Use when user cancels the flow or timeout occurs.
        """
        key = self._build_key(uid, session_id)

        try:
            deleted = self.redis.delete(key)
            logger.info(
                f"[PENDING_ACTION] Cancelled: uid={uid} deleted={deleted}"
            )
            return deleted > 0
        except Exception as e:
            logger.error(f"[PENDING_ACTION] Cancel error: {e}", exc_info=True)
            return False

    # =========================================================================
    # UTILITY OPERATIONS
    # =========================================================================

    def has_pending_action(self, uid: str, session_id: str) -> bool:
        """Check if there's a pending action for this session."""
        key = self._build_key(uid, session_id)
        return self.redis.exists(key) > 0

    def get_pending_action_type(
        self,
        uid: str,
        session_id: str
    ) -> Optional[str]:
        """Get just the action type without full data."""
        action = self.get_pending_action(uid, session_id)
        return action.get("action_type") if action else None

    def build_oauth_state(
        self,
        uid: str,
        session_id: str,
        state_token: str
    ) -> str:
        """
        Build the state parameter for OAuth redirect.
        Format: {uid}:{session_id}:{state_token}
        """
        return f"{uid}:{session_id}:{state_token}"

    @staticmethod
    def parse_oauth_state(state: str) -> Optional[Dict[str, str]]:
        """
        Parse the state parameter from OAuth callback.

        Args:
            state: The state string from OAuth callback

        Returns:
            Dict with uid, session_id, state_token or None if invalid
        """
        try:
            parts = state.split(":")
            if len(parts) != 3:
                logger.warning(f"[PENDING_ACTION] Invalid state format: {state[:20]}...")
                return None

            return {
                "uid": parts[0],
                "session_id": parts[1],
                "state_token": parts[2],
            }
        except Exception as e:
            logger.error(f"[PENDING_ACTION] Parse state error: {e}")
            return None


# =============================================================================
# SINGLETON
# =============================================================================

_pending_action_manager: Optional[PendingActionManager] = None


def get_pending_action_manager() -> PendingActionManager:
    """Get singleton instance of PendingActionManager."""
    global _pending_action_manager
    if _pending_action_manager is None:
        _pending_action_manager = PendingActionManager()
    return _pending_action_manager
