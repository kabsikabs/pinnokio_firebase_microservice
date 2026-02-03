"""
Gestionnaire de cache Redis unifié pour tous les modules Pinnokio.
Architecture 3 Niveaux.

Ce module implémente un cache asynchrone générique pour optimiser les performances
des requêtes vers les différentes sources de données (PostgreSQL, Firebase, Google Drive).

Architecture:
    - Cache-first: Tentative de lecture depuis Redis avant la source
    - Write-through: Mise à jour du cache après écriture source
    - Invalidation sélective: Suppression ciblée après modifications

═══════════════════════════════════════════════════════════════════════════════
ARCHITECTURE 3 NIVEAUX (Aligné avec redis_namespaces.py)
═══════════════════════════════════════════════════════════════════════════════

NIVEAU 1 - USER (Global):
    user:{uid}:profile
    user:{uid}:preferences

NIVEAU 2 - COMPANY (Context Société):
    company:{uid}:{cid}:context   (anciennement mandate:snapshot)
    company:{uid}:{cid}:settings

NIVEAU 3 - BUSINESS (Logique Métier):
    business:{uid}:{cid}:bank       (anciennement bank:transactions)
    business:{uid}:{cid}:routing    (anciennement drive:documents)
    business:{uid}:{cid}:invoices   (anciennement apbookeeper:documents)
    business:{uid}:{cid}:expenses   (anciennement expenses:details)
    business:{uid}:{cid}:coa
    business:{uid}:{cid}:dashboard  (anciennement approval_pendinglist)
    business:{uid}:{cid}:chat
    business:{uid}:{cid}:hr         (anciennement hr:employees)

LEGACY (rétro-compatibilité avec migration automatique):
    cache:{user_id}:{company_id}:{data_type}:{sub_type}

═══════════════════════════════════════════════════════════════════════════════

@see app/llm_service/redis_namespaces.py
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
import redis.asyncio as redis
import os

from ..llm_service.redis_namespaces import (
    CacheLevel,
    BusinessDomain,
    RedisTTL,
    # Helpers Niveau 2
    build_company_context_key,
    build_company_settings_key,
    # Helpers Niveau 3
    build_business_key,
    # Legacy
    build_cache_key as build_legacy_cache_key,
    # TTL
    get_ttl_for_domain,
)

logger = logging.getLogger("cache.unified")


# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING LEGACY → NOUVELLE ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════════════════

LEGACY_TO_BUSINESS_MAP: Dict[Tuple[str, Optional[str]], Tuple[CacheLevel, str]] = {
    # Niveau 2 - Company
    ("mandate", "snapshot"): (CacheLevel.COMPANY, "context"),
    ("company", "context"): (CacheLevel.COMPANY, "context"),
    ("company", "settings"): (CacheLevel.COMPANY, "settings"),

    # Niveau 3 - Business
    ("bank", "transactions"): (CacheLevel.BUSINESS, BusinessDomain.BANK.value),
    ("bank", None): (CacheLevel.BUSINESS, BusinessDomain.BANK.value),

    ("drive", "documents"): (CacheLevel.BUSINESS, BusinessDomain.ROUTING.value),
    ("routing", None): (CacheLevel.BUSINESS, BusinessDomain.ROUTING.value),

    ("apbookeeper", "documents"): (CacheLevel.BUSINESS, BusinessDomain.INVOICES.value),
    ("apbookeeper", None): (CacheLevel.BUSINESS, BusinessDomain.INVOICES.value),
    ("invoices", None): (CacheLevel.BUSINESS, BusinessDomain.INVOICES.value),

    ("expenses", "details"): (CacheLevel.BUSINESS, BusinessDomain.EXPENSES.value),
    ("expenses", "open"): (CacheLevel.BUSINESS, BusinessDomain.EXPENSES.value),
    ("expenses", "closed"): (CacheLevel.BUSINESS, BusinessDomain.EXPENSES.value),
    ("expenses", None): (CacheLevel.BUSINESS, BusinessDomain.EXPENSES.value),

    ("coa", "accounts"): (CacheLevel.BUSINESS, BusinessDomain.COA.value),
    ("coa", "functions"): (CacheLevel.BUSINESS, BusinessDomain.COA.value),
    ("coa", None): (CacheLevel.BUSINESS, BusinessDomain.COA.value),

    ("approval_pendinglist", None): (CacheLevel.BUSINESS, BusinessDomain.DASHBOARD.value),
    ("dashboard", None): (CacheLevel.BUSINESS, BusinessDomain.DASHBOARD.value),

    ("tasks", "list"): (CacheLevel.BUSINESS, BusinessDomain.TASKS.value),
    ("tasks", None): (CacheLevel.BUSINESS, BusinessDomain.TASKS.value),

    ("hr", "employees"): (CacheLevel.BUSINESS, BusinessDomain.HR.value),
    ("hr", "contracts"): (CacheLevel.BUSINESS, BusinessDomain.HR.value),
    ("hr", "references"): (CacheLevel.BUSINESS, BusinessDomain.HR.value),
    ("hr", None): (CacheLevel.BUSINESS, BusinessDomain.HR.value),

    ("chat", None): (CacheLevel.BUSINESS, BusinessDomain.CHAT.value),
}


def _resolve_cache_level(data_type: str, sub_type: Optional[str] = None) -> Tuple[CacheLevel, str]:
    """
    Résout le niveau de cache et le domaine pour un type de données.

    Args:
        data_type: Type de données legacy (ex: "expenses", "bank", "mandate")
        sub_type: Sous-type optionnel (ex: "details", "transactions")

    Returns:
        Tuple (CacheLevel, domain/suffix)
    """
    # Essayer avec data_type + sub_type
    key = (data_type.lower(), sub_type.lower() if sub_type else None)
    if key in LEGACY_TO_BUSINESS_MAP:
        return LEGACY_TO_BUSINESS_MAP[key]

    # Essayer avec data_type seul
    key_no_subtype = (data_type.lower(), None)
    if key_no_subtype in LEGACY_TO_BUSINESS_MAP:
        return LEGACY_TO_BUSINESS_MAP[key_no_subtype]

    # Fallback: supposer Niveau 3 BUSINESS avec data_type comme domaine
    logger.debug(f"[CACHE] Unknown data_type={data_type}, sub_type={sub_type}, using as BUSINESS domain")
    return (CacheLevel.BUSINESS, data_type.lower())


class UnifiedCacheManager:
    """
    Gestionnaire de cache Redis asynchrone unifié pour tous les modules.

    Utilise redis.asyncio pour la cohérence avec l'architecture async/await.
    Supporte l'architecture 3 niveaux avec migration automatique depuis legacy.
    """

    def __init__(self, log_prefix: str = "CACHE"):
        """
        Initialise le cache manager.

        Args:
            log_prefix: Préfixe pour les logs (ex: "HR_CACHE", "FIREBASE_CACHE")
        """
        self.redis_client: Optional[redis.Redis] = None
        self._connection_config = None
        self.log_prefix = log_prefix
        self._use_new_keys = True  # Flag pour activer les nouvelles clés

    async def _get_redis_client(self) -> redis.Redis:
        """
        Récupère le client Redis async (même configuration que les listeners).
        """
        if self.redis_client is None:
            self._connection_config = self._load_redis_config()

            self.redis_client = redis.Redis(
                host=self._connection_config.get("host"),
                port=self._connection_config.get("port", 6379),
                password=self._connection_config.get("password"),
                ssl=self._connection_config.get("tls", False),
                db=self._connection_config.get("db", 0),
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            logger.info(f"[{self.log_prefix}] Client Redis async initialisé")

        return self.redis_client

    def _load_redis_config(self) -> Dict:
        """
        Charge la configuration Redis depuis les variables d'environnement.
        Utilise la même configuration que listeners_manager.
        """
        use_local = os.getenv("USE_LOCAL_REDIS", "false").lower() == "true"

        if use_local:
            return {
                "host": "127.0.0.1",
                "port": 6379,
                "password": None,
                "tls": False,
                "db": int(os.getenv("LISTENERS_REDIS_DB", "0")),
            }
        else:
            return {
                "host": os.getenv("LISTENERS_REDIS_HOST", "localhost"),
                "port": int(os.getenv("LISTENERS_REDIS_PORT", "6379")),
                "password": os.getenv("LISTENERS_REDIS_PASSWORD"),
                "tls": os.getenv("LISTENERS_REDIS_TLS", "false").lower() == "true",
                "db": int(os.getenv("LISTENERS_REDIS_DB", "0")),
            }

    # ════════════════════════════════════════════════════════════════════════════
    # TYPES REQUIRING ITEM-LEVEL CACHING
    # These data_types need the sub_type as an item_key in the cache key
    # to avoid cache collisions between different items (e.g., different threads)
    # Format: business:{uid}:{cid}:{domain}:{item_key}
    # ════════════════════════════════════════════════════════════════════════════
    ITEM_LEVEL_CACHE_TYPES = {
        "chat:history:raw",   # Per-thread chat history - sub_type is thread_key
        "chat:sessions",      # Per-mode sessions list - sub_type is mode (chats/active_chats)
    }

    def _build_cache_key(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None
    ) -> str:
        """
        Construit une clé de cache selon l'architecture 3 niveaux.

        Format:
        - company:{uid}:{cid}:context (Niveau 2)
        - business:{uid}:{cid}:{domain} (Niveau 3)
        - business:{uid}:{cid}:{domain}:{item_key} (Niveau 3 avec item)
        """
        if not self._use_new_keys:
            # Mode legacy (deprecated)
            return build_legacy_cache_key(user_id, company_id, data_type, sub_type)

        # Résoudre le niveau et domaine
        level, domain_or_suffix = _resolve_cache_level(data_type, sub_type)

        if level == CacheLevel.COMPANY:
            if domain_or_suffix == "settings":
                return build_company_settings_key(user_id, company_id)
            return build_company_context_key(user_id, company_id)

        elif level == CacheLevel.BUSINESS:
            # Check if this data_type requires item-level caching
            # If so, pass sub_type as item_key to create unique key per item
            if data_type.lower() in self.ITEM_LEVEL_CACHE_TYPES and sub_type:
                return build_business_key(user_id, company_id, domain_or_suffix, item_key=sub_type)
            return build_business_key(user_id, company_id, domain_or_suffix)

        # Fallback legacy (should not happen with proper mapping)
        return build_legacy_cache_key(user_id, company_id, data_type, sub_type)

    def _get_legacy_key(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None
    ) -> str:
        """Retourne la clé legacy pour migration/fallback."""
        return build_legacy_cache_key(user_id, company_id, data_type, sub_type)

    async def get_cached_data(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None,
        ttl_seconds: int = 3600
    ) -> Optional[Dict]:
        """
        Récupère des données du cache Redis.

        Essaie d'abord la nouvelle clé, puis fallback sur la clé legacy.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive", "bank")
            sub_type: Sous-type (ex: "employees", "details", "documents")
            ttl_seconds: TTL suggéré (non utilisé en lecture, info seulement)

        Returns:
            Dict avec structure {"data": ..., "cached_at": ..., "source": "cache"}
            ou None si non trouvé
        """
        new_cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        legacy_cache_key = self._get_legacy_key(user_id, company_id, data_type, sub_type)

        logger.debug(f"[{self.log_prefix}] GET: new={new_cache_key}, legacy={legacy_cache_key}")

        try:
            redis_client = await self._get_redis_client()

            # 1. Essayer la nouvelle clé
            cached_data = await redis_client.get(new_cache_key)

            # 2. Fallback sur la clé legacy si nouvelle clé non trouvée
            if not cached_data and new_cache_key != legacy_cache_key:
                cached_data = await redis_client.get(legacy_cache_key)
                if cached_data:
                    logger.info(f"[{self.log_prefix}] LEGACY HIT: {legacy_cache_key} (migrating)")
                    # Migration automatique: copier vers nouvelle clé
                    try:
                        await redis_client.setex(new_cache_key, ttl_seconds, cached_data)
                    except Exception as e:
                        logger.debug(f"[{self.log_prefix}] Migration failed: {e}")

            if cached_data:
                data = json.loads(cached_data)
                cache_info = data.get("cached_at", "unknown")
                data_content = data.get("data", {})

                # Validation: vérifier que les données ne sont pas vides
                if isinstance(data_content, list):
                    total_items = len(data_content)
                    logger.info(
                        f"[{self.log_prefix}] HIT: {new_cache_key} | "
                        f"Cached: {cache_info} | Items: {total_items}"
                    )

                    # Rejeter les listes vides et forcer le fallback
                    if total_items == 0:
                        logger.warning(
                            f"[{self.log_prefix}] Empty data detected: {new_cache_key}"
                        )
                        await redis_client.delete(new_cache_key)
                        return None

                    return data
                elif isinstance(data_content, dict):
                    data_size = len(data_content)
                    logger.info(
                        f"[{self.log_prefix}] HIT: {new_cache_key} | "
                        f"Cached: {cache_info} | Keys: {data_size}"
                    )
                    return data
                else:
                    logger.info(f"[{self.log_prefix}] HIT: {new_cache_key} | Cached: {cache_info}")
                    return data

            # Cache miss
            logger.info(f"[{self.log_prefix}] MISS: {new_cache_key}")
            return None

        except Exception as e:
            logger.error(f"[{self.log_prefix}] GET error: {new_cache_key} | {e}")
            return None

    async def set_cached_data(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None,
        data: Any = None,
        ttl_seconds: int = None
    ) -> bool:
        """
        Stocke des données dans le cache Redis.

        Écrit dans la nouvelle clé ET la clé legacy pour rétro-compatibilité.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive", "bank")
            sub_type: Sous-type (ex: "employees", "details", "documents")
            data: Données à mettre en cache
            ttl_seconds: Durée de vie du cache (auto-déterminé si non fourni)

        Returns:
            True si succès, False sinon
        """
        new_cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)

        # Déterminer le TTL automatiquement si non fourni
        if ttl_seconds is None:
            level, domain = _resolve_cache_level(data_type, sub_type)
            if level == CacheLevel.BUSINESS:
                ttl_seconds = get_ttl_for_domain(domain)
            elif level == CacheLevel.COMPANY:
                ttl_seconds = RedisTTL.COMPANY_CONTEXT
            else:
                ttl_seconds = RedisTTL.CACHE  # Default legacy

        logger.debug(f"[{self.log_prefix}] SET: {new_cache_key} | TTL: {ttl_seconds}s")

        try:
            if data is None:
                logger.warning(f"[{self.log_prefix}] Null data for: {new_cache_key}")
                return False

            redis_client = await self._get_redis_client()

            # Calculer la taille des données
            data_size = len(str(data)) if data else 0

            # Ajouter des métadonnées de cache
            cached_payload = {
                "data": data,
                "cached_at": datetime.now().isoformat(),
                "ttl_seconds": ttl_seconds,
                "source": f"{data_type}.{sub_type}" if sub_type else data_type,
                "cache_version": "3.0"  # Marqueur nouvelle architecture
            }

            json_payload = json.dumps(cached_payload)

            # Stocker dans la nouvelle clé
            await redis_client.setex(new_cache_key, ttl_seconds, json_payload)

            logger.info(
                f"[{self.log_prefix}] SET OK: {new_cache_key} | "
                f"TTL: {ttl_seconds}s | Size: {data_size}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.log_prefix}] SET error: {new_cache_key} | {e}")
            return False

    async def invalidate_cache(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None
    ) -> bool:
        """
        Invalide une entrée de cache spécifique.

        Supprime à la fois la nouvelle clé ET la clé legacy.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive")
            sub_type: Sous-type (ex: "employees", "details", "documents")

        Returns:
            True si succès, False sinon
        """
        new_cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        legacy_cache_key = self._get_legacy_key(user_id, company_id, data_type, sub_type)

        logger.info(f"[{self.log_prefix}] INVALIDATE: {new_cache_key}")

        try:
            redis_client = await self._get_redis_client()

            # Supprimer les deux clés
            keys_to_delete = [new_cache_key]
            if legacy_cache_key != new_cache_key:
                keys_to_delete.append(legacy_cache_key)

            deleted = await redis_client.delete(*keys_to_delete)
            logger.info(f"[{self.log_prefix}] DELETED: {deleted} keys")
            return True

        except Exception as e:
            logger.error(f"[{self.log_prefix}] INVALIDATE error: {new_cache_key} | {e}")
            return False

    async def invalidate_module_cache(
        self,
        user_id: str,
        company_id: str,
        data_type: str
    ) -> bool:
        """
        Invalide tout le cache d'un module pour une société et un utilisateur.

        Nettoie à la fois les nouvelles clés ET les clés legacy.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive")

        Returns:
            True si succès, False sinon
        """
        # Déterminer le domaine business correspondant
        level, domain = _resolve_cache_level(data_type, None)

        # Patterns à nettoyer
        patterns = []
        if level == CacheLevel.BUSINESS:
            patterns.append(f"business:{user_id}:{company_id}:{domain}")
        elif level == CacheLevel.COMPANY:
            patterns.append(f"company:{user_id}:{company_id}:{domain}")

        # Toujours inclure le pattern legacy
        patterns.append(f"cache:{user_id}:{company_id}:{data_type}:*")

        logger.info(f"[{self.log_prefix}] INVALIDATE MODULE: {patterns}")

        try:
            redis_client = await self._get_redis_client()
            total_deleted = 0

            for pattern in patterns:
                if pattern.endswith("*"):
                    # SCAN pour les patterns avec wildcard
                    cursor = 0
                    keys_to_delete = []

                    while True:
                        cursor, batch = await redis_client.scan(
                            cursor=cursor,
                            match=pattern,
                            count=100
                        )
                        keys_to_delete.extend(batch)
                        if cursor == 0:
                            break

                    if keys_to_delete:
                        await redis_client.delete(*keys_to_delete)
                        total_deleted += len(keys_to_delete)
                else:
                    # Clé exacte
                    deleted = await redis_client.delete(pattern)
                    total_deleted += deleted

            logger.info(f"[{self.log_prefix}] MODULE INVALIDATED: {total_deleted} keys")
            return True

        except Exception as e:
            logger.error(f"[{self.log_prefix}] INVALIDATE MODULE error: {e}")
            return False

    async def invalidate_business_domain(
        self,
        user_id: str,
        company_id: str,
        domain: str
    ) -> bool:
        """
        Invalide tout le cache d'un domaine business.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            domain: Domaine business (bank, routing, invoices, expenses, coa, dashboard, chat, hr)

        Returns:
            True si succès, False sinon
        """
        cache_key = build_business_key(user_id, company_id, domain)
        logger.info(f"[{self.log_prefix}] INVALIDATE DOMAIN: {cache_key}")

        try:
            redis_client = await self._get_redis_client()
            deleted = await redis_client.delete(cache_key)
            logger.info(f"[{self.log_prefix}] DOMAIN INVALIDATED: {domain} (deleted={deleted})")
            return True
        except Exception as e:
            logger.error(f"[{self.log_prefix}] INVALIDATE DOMAIN error: {e}")
            return False

    async def get_cache_stats(
        self,
        user_id: str,
        company_id: str,
        data_type: str = None
    ) -> Dict:
        """
        Retourne les statistiques du cache pour une société.

        Inclut à la fois les nouvelles clés et les clés legacy.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données optionnel (ex: "hr", "expenses")

        Returns:
            Dict avec statistiques (total_keys, data_types, etc.)
        """
        try:
            redis_client = await self._get_redis_client()

            # Patterns à chercher
            patterns = [
                f"business:{user_id}:{company_id}:*",
                f"company:{user_id}:{company_id}:*",
                f"cache:{user_id}:{company_id}:*",
            ]

            if data_type:
                level, domain = _resolve_cache_level(data_type, None)
                if level == CacheLevel.BUSINESS:
                    patterns = [f"business:{user_id}:{company_id}:{domain}"]
                patterns.append(f"cache:{user_id}:{company_id}:{data_type}:*")

            all_keys = []
            for pattern in patterns:
                cursor = 0
                while True:
                    cursor, batch = await redis_client.scan(
                        cursor=cursor,
                        match=pattern,
                        count=100
                    )
                    all_keys.extend(batch)
                    if cursor == 0:
                        break

            # Dédupliquer
            all_keys = list(set(all_keys))

            stats = {
                "total_keys": len(all_keys),
                "data_types": {},
                "levels": {"business": 0, "company": 0, "legacy": 0},
                "total_size_bytes": 0,
                "oldest_entry": None,
                "newest_entry": None
            }

            for key in all_keys:
                try:
                    data = await redis_client.get(key)
                    if data:
                        parsed = json.loads(data)

                        # Déterminer le niveau
                        if key.startswith("business:"):
                            stats["levels"]["business"] += 1
                            key_parts = key.split(":")
                            domain = key_parts[3] if len(key_parts) > 3 else "unknown"
                        elif key.startswith("company:"):
                            stats["levels"]["company"] += 1
                            domain = "company"
                        else:
                            stats["levels"]["legacy"] += 1
                            key_parts = key.split(":")
                            domain = key_parts[3] if len(key_parts) > 3 else "unknown"

                        if domain not in stats["data_types"]:
                            stats["data_types"][domain] = 0
                        stats["data_types"][domain] += 1

                        stats["total_size_bytes"] += len(data)

                        cached_at = parsed.get("cached_at")
                        if cached_at:
                            if not stats["oldest_entry"] or cached_at < stats["oldest_entry"]:
                                stats["oldest_entry"] = cached_at
                            if not stats["newest_entry"] or cached_at > stats["newest_entry"]:
                                stats["newest_entry"] = cached_at

                except Exception:
                    continue

            logger.info(f"[{self.log_prefix}] STATS: {stats['total_keys']} keys, {stats['total_size_bytes']} bytes")
            return stats

        except Exception as e:
            logger.error(f"[{self.log_prefix}] STATS error: {e}")
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# INSTANCES SPÉCIALISÉES (pour chaque module)
# ═══════════════════════════════════════════════════════════════════════════════

_firebase_cache_manager: Optional[UnifiedCacheManager] = None
_drive_cache_manager: Optional[UnifiedCacheManager] = None
_business_cache_manager: Optional[UnifiedCacheManager] = None


def get_firebase_cache_manager() -> UnifiedCacheManager:
    """
    Retourne l'instance singleton du cache manager Firebase.

    Usage:
        from app.cache.unified_cache_manager import get_firebase_cache_manager

        cache = get_firebase_cache_manager()
        cached = await cache.get_cached_data(user_id, company_id, "expenses", "details")
    """
    global _firebase_cache_manager
    if _firebase_cache_manager is None:
        _firebase_cache_manager = UnifiedCacheManager(log_prefix="FIREBASE_CACHE")
    return _firebase_cache_manager


def get_drive_cache_manager() -> UnifiedCacheManager:
    """
    Retourne l'instance singleton du cache manager Drive.

    Usage:
        from app.cache.unified_cache_manager import get_drive_cache_manager

        cache = get_drive_cache_manager()
        cached = await cache.get_cached_data(user_id, company_id, "drive", "documents")
    """
    global _drive_cache_manager
    if _drive_cache_manager is None:
        _drive_cache_manager = UnifiedCacheManager(log_prefix="DRIVE_CACHE")
    return _drive_cache_manager


def get_business_cache_manager() -> UnifiedCacheManager:
    """
    Retourne l'instance singleton du cache manager Business (Niveau 3).

    Usage:
        from app.cache.unified_cache_manager import get_business_cache_manager

        cache = get_business_cache_manager()
        cached = await cache.get_cached_data(user_id, company_id, "bank", None)
    """
    global _business_cache_manager
    if _business_cache_manager is None:
        _business_cache_manager = UnifiedCacheManager(log_prefix="BUSINESS_CACHE")
    return _business_cache_manager
