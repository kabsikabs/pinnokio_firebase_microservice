"""
Page State Manager - Manages page-level state in Redis.
=========================================================

This manager handles caching and restoration of page state for fast recovery
after page refresh, navigation, or return from external OAuth/payment flows.

Architecture:
    Frontend -> WebSocket -> page_state_manager -> Redis

Redis Key Pattern:
    page_state:{uid}:{company_id}:{page_name}

    NOTE: We use company_id (not session_id) because:
    - session_id is regenerated on page refresh
    - company_id is persisted in localStorage and survives refresh
    - Page state is company-specific anyway

TTL: 30 minutes (PAGE_STATE_TTL)

Usage:
    # Save page state after orchestration
    manager = get_page_state_manager()
    manager.save_page_state(
        uid="user123",
        company_id="comp789",
        page="dashboard",
        mandate_path="clients/.../mandates/...",
        data=dashboard_full_data
    )

    # Restore page state on page load
    state = manager.get_page_state(uid, "dashboard", company_id)
    if state:
        return state["data"]  # Fast restore from cache
    else:
        # Trigger full orchestration
        pass

Author: Migration Team
Created: 2026-01-19
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..redis_client import get_redis

logger = logging.getLogger("page_state")

# =============================================================================
# CONSTANTS
# =============================================================================

PAGE_STATE_TTL = 1800  # 30 minutes
PAGE_STATE_PREFIX = "page_state"

# Valid page names (for validation)
VALID_PAGES = [
    "dashboard",
    "invoices",
    "expenses",
    "chat",
    "hr",
    "settings",
    "onboarding",
    "billing",
]


# =============================================================================
# PAGE STATE MANAGER
# =============================================================================

class PageStateManager:
    """
    Manages page state in Redis for fast recovery.

    This enables:
    - Fast page restore after refresh (no full re-orchestration)
    - State preservation during OAuth/payment redirects
    - Consistent experience across navigation
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def _build_key(self, uid: str, company_id: str, page: str) -> str:
        """Build Redis key for page state."""
        return f"{PAGE_STATE_PREFIX}:{uid}:{company_id}:{page}"

    def _validate_page(self, page: str) -> bool:
        """Validate page name."""
        return page in VALID_PAGES

    # =========================================================================
    # SAVE OPERATIONS
    # =========================================================================

    def save_page_state(
        self,
        uid: str,
        company_id: str,
        page: str,
        mandate_path: str,
        data: Dict[str, Any],
        ttl: int = PAGE_STATE_TTL
    ) -> bool:
        """
        Save page state to Redis.

        Args:
            uid: Firebase user ID
            company_id: Company ID (used in key - persists across refresh)
            page: Page name (dashboard, invoices, etc.)
            mandate_path: Current mandate path (for validation)
            data: Page data to cache
            ttl: Time-to-live in seconds (default 30min)

        Returns:
            True if saved successfully, False otherwise
        """
        if not self._validate_page(page):
            logger.warning(f"[PAGE_STATE] Invalid page name: {page}")
            return False

        key = self._build_key(uid, company_id, page)
        now = datetime.now(timezone.utc)

        state = {
            "version": "1.0",
            "page": page,
            "company_id": company_id,
            "mandate_path": mandate_path,
            "loaded_at": now.isoformat(),
            "data": data
        }

        try:
            self.redis.setex(key, ttl, json.dumps(state, default=str))
            logger.info(
                f"[PAGE_STATE] Saved: page={page} uid={uid} "
                f"company={company_id} ttl={ttl}s"
            )
            return True
        except Exception as e:
            logger.error(f"[PAGE_STATE] Save error: {e}", exc_info=True)
            return False

    # =========================================================================
    # GET OPERATIONS
    # =========================================================================

    def get_page_state(
        self,
        uid: str,
        company_id: str,
        page: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get page state from Redis.

        Args:
            uid: Firebase user ID
            company_id: Company ID (used in key)
            page: Page name

        Returns:
            Page state dict with 'data' field, or None if not found
        """
        if not self._validate_page(page):
            logger.warning(f"[PAGE_STATE] Invalid page name: {page}")
            return None

        if not company_id:
            logger.warning(f"[PAGE_STATE] No company_id provided for page={page}")
            return None

        key = self._build_key(uid, company_id, page)

        try:
            raw = self.redis.get(key)
            if not raw:
                logger.info(f"[PAGE_STATE] Cache MISS: page={page} uid={uid} company={company_id}")
                return None

            state = json.loads(raw if isinstance(raw, str) else raw.decode())

            logger.info(
                f"[PAGE_STATE] Cache HIT: page={page} uid={uid} company={company_id} "
                f"loaded_at={state.get('loaded_at')}"
            )
            return state

        except Exception as e:
            logger.error(f"[PAGE_STATE] Get error: {e}", exc_info=True)
            return None

    def get_page_state_metadata(
        self,
        uid: str,
        company_id: str,
        page: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get page state metadata without the full data.
        Useful for checking if state exists and when it was loaded.
        """
        state = self.get_page_state(uid, company_id, page)
        if state:
            return {
                "version": state.get("version"),
                "page": state.get("page"),
                "company_id": state.get("company_id"),
                "mandate_path": state.get("mandate_path"),
                "loaded_at": state.get("loaded_at"),
                "has_data": bool(state.get("data")),
            }
        return None

    # =========================================================================
    # INVALIDATION OPERATIONS
    # =========================================================================

    def invalidate_page_state(
        self,
        uid: str,
        company_id: str,
        page: Optional[str] = None
    ) -> bool:
        """
        Invalidate page state (single page or all pages for a company).

        Args:
            uid: Firebase user ID
            company_id: Company ID
            page: Specific page to invalidate, or None for all pages

        Returns:
            True if invalidated successfully
        """
        try:
            if page:
                # Invalidate specific page
                if not self._validate_page(page):
                    logger.warning(f"[PAGE_STATE] Invalid page name: {page}")
                    return False

                key = self._build_key(uid, company_id, page)
                deleted = self.redis.delete(key)
                logger.info(
                    f"[PAGE_STATE] Invalidated: page={page} uid={uid} "
                    f"deleted={deleted}"
                )
            else:
                # Invalidate all pages for this company
                pattern = f"{PAGE_STATE_PREFIX}:{uid}:{company_id}:*"
                keys = self.redis.keys(pattern)
                if keys:
                    deleted = self.redis.delete(*keys)
                    logger.info(
                        f"[PAGE_STATE] Invalidated ALL pages for company: uid={uid} "
                        f"company={company_id} count={deleted}"
                    )
                else:
                    logger.info(f"[PAGE_STATE] No pages to invalidate: uid={uid} company={company_id}")
            return True

        except Exception as e:
            logger.error(f"[PAGE_STATE] Invalidate error: {e}", exc_info=True)
            return False

    def invalidate_all_for_user(self, uid: str) -> bool:
        """
        Invalidate all page states for all companies of a user.
        Use when user logs out or changes critical settings.
        """
        try:
            pattern = f"{PAGE_STATE_PREFIX}:{uid}:*"
            keys = self.redis.keys(pattern)
            if keys:
                deleted = self.redis.delete(*keys)
                logger.info(
                    f"[PAGE_STATE] Invalidated ALL for user: uid={uid} "
                    f"count={deleted}"
                )
            return True
        except Exception as e:
            logger.error(f"[PAGE_STATE] Invalidate all error: {e}", exc_info=True)
            return False

    # =========================================================================
    # UTILITY OPERATIONS
    # =========================================================================

    def get_cached_pages(self, uid: str, company_id: str) -> List[str]:
        """Get list of pages currently cached for a company."""
        try:
            pattern = f"{PAGE_STATE_PREFIX}:{uid}:{company_id}:*"
            keys = self.redis.keys(pattern)
            pages = []
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode()
                # Extract page name from key
                parts = key_str.split(":")
                if len(parts) >= 4:
                    pages.append(parts[3])
            return pages
        except Exception as e:
            logger.error(f"[PAGE_STATE] Get cached pages error: {e}")
            return []

    def extend_ttl(
        self,
        uid: str,
        company_id: str,
        page: str,
        ttl: int = PAGE_STATE_TTL
    ) -> bool:
        """Extend TTL of existing page state without modifying data."""
        try:
            key = self._build_key(uid, company_id, page)
            if self.redis.exists(key):
                self.redis.expire(key, ttl)
                logger.debug(f"[PAGE_STATE] Extended TTL: page={page}")
                return True
            return False
        except Exception as e:
            logger.error(f"[PAGE_STATE] Extend TTL error: {e}")
            return False


# =============================================================================
# SINGLETON
# =============================================================================

_page_state_manager: Optional[PageStateManager] = None


def get_page_state_manager() -> PageStateManager:
    """Get singleton instance of PageStateManager."""
    global _page_state_manager
    if _page_state_manager is None:
        _page_state_manager = PageStateManager()
    return _page_state_manager
