"""
Balance Service — Centralised balance checking, caching (L1 Redis) and WSS propagation.

Singleton service used by:
- chat handler (main.py)          → check before enqueue
- job handler (job_actions_handler) → check before dispatch
- balance_handlers.py             → refresh_and_broadcast after top-up / Stripe
- maintenance_tasks.py            → cache update after CRON billing
- firebase_providers.py           → cache update after billing catchup

Cache key: user:{uid}:balance  (TTL 5 min)
WSS event: balance.balance_update (level USER — always pushed if connected)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("balance.service")

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

COST_PER_ITEM = {
    "router": 0.5,
    "apbookeeper": 1.0,
    "bankbookeeper": 0.3,
    "exbookeeper": 0.0,
    "onboarding": 0.0,
}

CHAT_COST_PER_TURN = 0.05
SAFETY_MARGIN = 1.2
CHAT_LOW_BALANCE_THRESHOLD = 1.0
CHAT_ZERO_BALANCE_THRESHOLD = 0.0


# ═══════════════════════════════════════════════════════════════
# Result dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class BalanceCheckResult:
    sufficient: bool
    current_balance: float
    required_balance: float
    estimated_cost: float
    missing_amount: float = 0.0
    message: str = ""
    warning: bool = False  # True when low balance (soft warning, not a block)


# ═══════════════════════════════════════════════════════════════
# Service
# ═══════════════════════════════════════════════════════════════

class BalanceService:
    """Singleton for balance check, L1 cache and WSS delta."""

    def __init__(self):
        from .redis_client import get_redis
        from .llm_service.redis_namespaces import RedisTTL
        self._redis = get_redis
        self._ttl = getattr(RedisTTL, "USER_BALANCE", 300)

    # ─── helpers ───────────────────────────────────────────────

    @staticmethod
    def _balance_key(uid: str) -> str:
        return f"user:{uid}:balance"

    def _r(self):
        """Lazy Redis client."""
        return self._redis()

    # ─── CACHE L1 ─────────────────────────────────────────────

    def get_cached_balance(self, uid: str) -> Optional[dict]:
        """Read user:{uid}:balance from Redis. Returns dict or None."""
        try:
            raw = self._r().get(self._balance_key(uid))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    def update_balance_cache(self, uid: str, balance_data: dict) -> None:
        """Write user:{uid}:balance into Redis with TTL."""
        try:
            self._r().setex(
                self._balance_key(uid),
                self._ttl,
                json.dumps(balance_data),
            )
        except Exception as exc:
            logger.warning("[BALANCE_SVC] cache update failed: %s", exc)

    def invalidate_balance_cache(self, uid: str) -> None:
        """Delete user:{uid}:balance."""
        try:
            self._r().delete(self._balance_key(uid))
        except Exception:
            pass

    # ─── CHECK ────────────────────────────────────────────────

    async def check_balance(
        self,
        uid: str,
        mandate_path: Optional[str] = None,
        estimated_cost: float = 0.05,
        operation: str = "unknown",
    ) -> BalanceCheckResult:
        """
        Check whether the user has enough balance.

        1. Try Redis L1 cache
        2. Fallback to Firestore via get_balance_info()
        3. Populate cache on miss
        4. Apply 20 % safety margin
        5. Failsafe: on error → sufficient=True
        """
        try:
            # 1. Cache hit?
            cached = self.get_cached_balance(uid)
            if cached is not None:
                balance_info = cached
                logger.debug("[BALANCE_SVC] cache HIT uid=%s", uid)
            else:
                # 2. Firestore fallback
                from .firebase_providers import get_firebase_management
                fbm = get_firebase_management()
                balance_info = await asyncio.to_thread(
                    fbm.get_balance_info,
                    mandate_path=mandate_path,
                    user_id=uid,
                )
                # 3. Populate cache
                self.update_balance_cache(uid, balance_info)
                logger.debug("[BALANCE_SVC] cache MISS uid=%s → populated", uid)

            current_balance = float(balance_info.get("current_balance", 0.0))

            # 4. Safety margin
            required = estimated_cost * SAFETY_MARGIN

            sufficient = current_balance >= required
            missing = max(0.0, required - current_balance)
            warning = (
                not sufficient
                or current_balance < CHAT_LOW_BALANCE_THRESHOLD
            )

            message = ""
            if not sufficient:
                message = (
                    f"Insufficient balance for {operation}. "
                    f"Current: ${current_balance:.2f}, required: ${required:.2f}. "
                    f"Please top up your account."
                )

            logger.info(
                "[BALANCE_SVC] check op=%s uid=%s balance=%.2f required=%.2f → %s",
                operation, uid, current_balance, required,
                "OK" if sufficient else "INSUFFICIENT",
            )

            return BalanceCheckResult(
                sufficient=sufficient,
                current_balance=current_balance,
                required_balance=required,
                estimated_cost=estimated_cost,
                missing_amount=missing,
                message=message,
                warning=warning,
            )

        except Exception as exc:
            logger.error("[BALANCE_SVC] check error (failsafe=True): %s", exc, exc_info=True)
            return BalanceCheckResult(
                sufficient=True,
                current_balance=0.0,
                required_balance=0.0,
                estimated_cost=estimated_cost,
                message="Unable to verify balance. Operation allowed by default.",
            )

    # ─── DELTA WSS ────────────────────────────────────────────

    async def refresh_and_broadcast(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
    ) -> dict:
        """
        1. Read fresh balance from Firestore
        2. Update L1 cache
        3. Broadcast via WSS (level USER)
        """
        from .firebase_providers import get_firebase_management
        from .realtime.contextual_publisher import publish_user_event
        from .ws_events import WS_EVENTS

        try:
            fbm = get_firebase_management()
            balance_info = await asyncio.to_thread(
                fbm.get_balance_info,
                mandate_path=mandate_path,
                user_id=uid,
            )

            self.update_balance_cache(uid, balance_info)

            payload = {
                "action": "update",
                "data": {
                    "currentBalance": balance_info.get("current_balance", 0.0),
                    "currentTopping": balance_info.get("current_topping", 0.0),
                    "currentExpenses": balance_info.get("current_expenses", 0.0),
                },
            }

            await publish_user_event(
                uid=uid,
                event_type=WS_EVENTS.BALANCE.BALANCE_UPDATE,
                payload=payload,
                cache_subkey="balance",
            )

            logger.info(
                "[BALANCE_SVC] refresh_and_broadcast uid=%s balance=%.2f",
                uid, balance_info.get("current_balance", 0.0),
            )
            return balance_info

        except Exception as exc:
            logger.error("[BALANCE_SVC] refresh_and_broadcast error: %s", exc, exc_info=True)
            return {}


# ═══════════════════════════════════════════════════════════════
# Singleton access
# ═══════════════════════════════════════════════════════════════

_instance: Optional[BalanceService] = None


def get_balance_service() -> BalanceService:
    """Get the async-capable singleton (for FastAPI / async handlers)."""
    global _instance
    if _instance is None:
        _instance = BalanceService()
    return _instance


# Alias for synchronous callers (Celery tasks, firebase_providers)
get_balance_service_sync = get_balance_service
