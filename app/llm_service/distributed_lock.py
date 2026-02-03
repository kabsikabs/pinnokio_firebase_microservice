"""
DistributedLock - Verrou distribué Redis.

Ce module implémente un verrou distribué utilisant Redis pour remplacer
les asyncio.Lock locaux qui ne fonctionnent pas en multi-instance.

Architecture:
    - Clé Redis: lock:{resource_name}
    - TTL: 30 secondes par défaut (auto-release si crash)
    - Lua script pour release atomique
    - Pattern: SET NX EX + Lua script pour vérifier ownership

Usage:
    async with DistributedLock("brain:user123:company456:thread789"):
        # Code critique protégé
        pass

Author: Scalability Team
Created: 2026-01-20
"""

import asyncio
import logging
import uuid
from typing import Optional
from contextlib import asynccontextmanager

logger = logging.getLogger("llm_service.distributed_lock")


class DistributedLock:
    """
    Verrou distribué utilisant Redis.
    
    Remplace asyncio.Lock pour permettre la synchronisation inter-instances.
    Utilise SET NX EX pour acquérir et un Lua script pour release atomique.
    """
    
    DEFAULT_TTL = 30  # 30 secondes
    DEFAULT_TIMEOUT = 10  # 10 secondes d'attente max
    RETRY_INTERVAL = 0.1  # 100ms entre chaque tentative
    
    def __init__(
        self,
        resource_name: str,
        ttl: int = DEFAULT_TTL,
        timeout: int = DEFAULT_TIMEOUT,
        redis_client=None
    ):
        """
        Initialise le verrou distribué.
        
        Args:
            resource_name: Nom de la ressource à verrouiller (ex: "brain:user:thread")
            ttl: Durée de vie du verrou en secondes (auto-release si crash)
            timeout: Temps d'attente max pour acquérir le verrou
            redis_client: Client Redis optionnel
        """
        self.resource_name = resource_name
        self.key = f"lock:{resource_name}"
        self.ttl = ttl
        self.timeout = timeout
        self._redis = redis_client
        self._lock_id: Optional[str] = None
        self._acquired = False
        
        # Lua script pour release atomique
        # Vérifie que le lock appartient bien à l'appelant avant de le supprimer
        self._release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
    
    @property
    def redis(self):
        """Lazy loading du client Redis."""
        if self._redis is None:
            from ..redis_client import get_redis
            self._redis = get_redis()
        return self._redis
    
    async def acquire(self) -> bool:
        """
        Acquiert le verrou de manière asynchrone.
        
        Essaie d'acquérir le verrou avec retry jusqu'au timeout.
        
        Returns:
            True si verrou acquis, False sinon
        """
        if self._acquired:
            logger.warning(
                f"[LOCK] ⚠️ Verrou déjà acquis: {self.resource_name}"
            )
            return True
        
        self._lock_id = str(uuid.uuid4())
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Tentative d'acquisition (SET NX EX)
            acquired = self.redis.set(
                self.key,
                self._lock_id,
                nx=True,  # Only set if not exists
                ex=self.ttl  # Expiration
            )
            
            if acquired:
                self._acquired = True
                logger.debug(
                    f"[LOCK] ✅ Verrou acquis: {self.resource_name} "
                    f"(id={self._lock_id[:8]}...)"
                )
                return True
            
            # Vérifier timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= self.timeout:
                logger.warning(
                    f"[LOCK] ⏰ Timeout acquisition: {self.resource_name} "
                    f"après {elapsed:.1f}s"
                )
                return False
            
            # Attendre avant retry
            await asyncio.sleep(self.RETRY_INTERVAL)
    
    def release(self):
        """
        Libère le verrou de manière synchrone.
        
        Utilise un Lua script pour vérifier l'ownership avant de supprimer.
        """
        if not self._acquired or not self._lock_id:
            logger.warning(
                f"[LOCK] ⚠️ Tentative de release sans acquisition: {self.resource_name}"
            )
            return
        
        try:
            # Exécuter le Lua script (atomique)
            result = self.redis.eval(
                self._release_script,
                1,  # Number of keys
                self.key,
                self._lock_id
            )
            
            if result == 1:
                logger.debug(
                    f"[LOCK] 🔓 Verrou libéré: {self.resource_name} "
                    f"(id={self._lock_id[:8]}...)"
                )
            else:
                logger.warning(
                    f"[LOCK] ⚠️ Verrou non libéré (pas propriétaire ou expiré): "
                    f"{self.resource_name}"
                )
        except Exception as e:
            logger.error(
                f"[LOCK] ❌ Erreur release: {self.resource_name} - {e}"
            )
        finally:
            self._acquired = False
            self._lock_id = None
    
    def extend(self, additional_ttl: Optional[int] = None):
        """
        Prolonge la durée de vie du verrou.
        
        Utile pour les opérations longues qui dépassent le TTL initial.
        
        Args:
            additional_ttl: TTL additionnel (ou self.ttl par défaut)
        """
        if not self._acquired or not self._lock_id:
            logger.warning(
                f"[LOCK] ⚠️ Tentative d'extend sans acquisition: {self.resource_name}"
            )
            return False
        
        try:
            # Vérifier qu'on est toujours propriétaire
            current_value = self.redis.get(self.key)
            if current_value != self._lock_id:
                logger.warning(
                    f"[LOCK] ⚠️ Cannot extend, not owner: {self.resource_name}"
                )
                self._acquired = False
                return False
            
            # Prolonger le TTL
            ttl = additional_ttl or self.ttl
            self.redis.expire(self.key, ttl)
            
            logger.debug(
                f"[LOCK] ⏰ Verrou prolongé: {self.resource_name} (+{ttl}s)"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"[LOCK] ❌ Erreur extend: {self.resource_name} - {e}"
            )
            return False
    
    def __enter__(self):
        """Support for synchronous context manager (not recommended)."""
        # Note: This is synchronous, prefer async context manager
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError(
                "Cannot use synchronous context manager in async context. "
                "Use 'async with' instead."
            )
        return loop.run_until_complete(self.acquire())
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Support for synchronous context manager (not recommended)."""
        self.release()
        return False
    
    async def __aenter__(self):
        """Support for async context manager (recommended)."""
        acquired = await self.acquire()
        if not acquired:
            raise TimeoutError(
                f"Failed to acquire lock: {self.resource_name} "
                f"after {self.timeout}s"
            )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Support for async context manager (recommended)."""
        self.release()
        return False
    
    @property
    def is_acquired(self) -> bool:
        """Vérifie si le verrou est actuellement acquis."""
        return self._acquired


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def distributed_lock(
    resource_name: str,
    ttl: int = DistributedLock.DEFAULT_TTL,
    timeout: int = DistributedLock.DEFAULT_TIMEOUT,
    redis_client=None
):
    """
    Context manager helper pour verrou distribué.
    
    Usage:
        async with distributed_lock("brain:user:thread"):
            # Code protégé
            pass
    
    Args:
        resource_name: Nom de la ressource
        ttl: Durée de vie du verrou
        timeout: Temps d'attente max
        redis_client: Client Redis optionnel
    """
    lock = DistributedLock(resource_name, ttl, timeout, redis_client)
    try:
        acquired = await lock.acquire()
        if not acquired:
            raise TimeoutError(
                f"Failed to acquire lock: {resource_name} after {timeout}s"
            )
        yield lock
    finally:
        lock.release()
