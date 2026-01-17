"""
Gestionnaire de cache Redis unifié pour tous les modules Pinnokio.

Ce module implémente un cache asynchrone générique pour optimiser les performances
des requêtes vers les différentes sources de données (PostgreSQL, Firebase, Google Drive).

Architecture:
    - Cache-first: Tentative de lecture depuis Redis avant la source
    - Write-through: Mise à jour du cache après écriture source
    - Invalidation sélective: Suppression ciblée après modifications

Structure des clés Redis:
    - cache:{user_id}:{company_id}:{data_type}:{sub_type}

Exemples:
    - cache:{user_id}:{company_id}:hr:employees
    - cache:{user_id}:{company_id}:expenses:details
    - cache:{user_id}:{company_id}:drive:documents
    - cache:{user_id}:{company_id}:apbookeeper:documents
    - cache:{user_id}:{company_id}:bank:transactions
    - cache:{user_id}:{company_id}:mandate:snapshot

TTLs configurables par module via RedisTTL.
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import redis.asyncio as redis
import os

logger = logging.getLogger("cache.unified")


class UnifiedCacheManager:
    """
    Gestionnaire de cache Redis asynchrone unifié pour tous les modules.

    Utilise redis.asyncio pour la cohérence avec l'architecture async/await.
    Suit la structure de clés existante du projet.
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
            logger.info(f"✅ [{self.log_prefix}] Client Redis async initialisé")

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

    def _build_cache_key(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None
    ) -> str:
        """
        Construit une clé de cache standardisée conforme à l'existant.

        Format: cache:{user_id}:{company_id}:{data_type}[:sub_type]

        Exemples:
            - cache:uid123:comp456:hr:employees
            - cache:uid123:comp456:expenses:details
            - cache:uid123:comp456:drive:documents
        """
        key = f"cache:{user_id}:{company_id}:{data_type}"
        if sub_type:
            key += f":{sub_type}"
        return key

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

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive")
            sub_type: Sous-type (ex: "employees", "details", "documents")
            ttl_seconds: TTL suggéré (non utilisé en lecture, info seulement)

        Returns:
            Dict avec structure {"data": ..., "cached_at": ..., "source": "cache"}
            ou None si non trouvé
        """
        cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        logger.info(f"🔍 [{self.log_prefix}] Tentative de récupération: {cache_key}")

        try:
            redis_client = await self._get_redis_client()

            # Tentative de récupération depuis le cache
            cached_data = await redis_client.get(cache_key)

            if cached_data:
                data = json.loads(cached_data)
                cache_info = data.get("cached_at", "unknown")
                data_content = data.get("data", {})

                # Validation: vérifier que les données ne sont pas vides
                if isinstance(data_content, list):
                    total_items = len(data_content)
                    logger.info(
                        f"✅ [{self.log_prefix}] HIT: {cache_key} | "
                        f"Cached: {cache_info} | Items: {total_items}"
                    )

                    # Rejeter les listes vides et forcer le fallback
                    if total_items == 0:
                        logger.warning(
                            f"⚠️ [{self.log_prefix}] Données VIDES détectées: {cache_key}"
                        )
                        await redis_client.delete(cache_key)
                        return None

                    return data
                elif isinstance(data_content, dict):
                    data_size = len(data_content)
                    logger.info(
                        f"✅ [{self.log_prefix}] HIT: {cache_key} | "
                        f"Cached: {cache_info} | Keys: {data_size}"
                    )
                    return data
                else:
                    logger.info(f"✅ [{self.log_prefix}] HIT: {cache_key} | Cached: {cache_info}")
                    return data

            # Cache miss
            logger.info(f"❌ [{self.log_prefix}] MISS: {cache_key}")
            return None

        except Exception as e:
            logger.error(f"❌ [{self.log_prefix}] Erreur lors de la récupération: {cache_key} | Error: {e}")
            # En cas d'erreur Redis, retourner None pour continuer avec la source
            return None

    async def set_cached_data(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None,
        data: Any = None,
        ttl_seconds: int = 3600
    ) -> bool:
        """
        Stocke des données dans le cache Redis.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive")
            sub_type: Sous-type (ex: "employees", "details", "documents")
            data: Données à mettre en cache
            ttl_seconds: Durée de vie du cache en secondes

        Returns:
            True si succès, False sinon
        """
        cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        logger.info(f"💾 [{self.log_prefix}] Tentative de stockage: {cache_key} | TTL: {ttl_seconds}s")

        try:
            if data is None:
                logger.warning(f"⚠️ [{self.log_prefix}] Données None pour: {cache_key}")
                return False

            redis_client = await self._get_redis_client()

            # Calculer la taille des données
            data_size = len(str(data)) if data else 0
            logger.debug(f"📊 [{self.log_prefix}] Taille des données: {data_size} caractères")

            # Ajouter des métadonnées de cache
            cached_payload = {
                "data": data,
                "cached_at": datetime.now().isoformat(),
                "ttl_seconds": ttl_seconds,
                "source": f"{data_type}.{sub_type}" if sub_type else data_type
            }

            # Stocker avec TTL
            await redis_client.setex(
                cache_key,
                ttl_seconds,
                json.dumps(cached_payload)
            )

            logger.info(
                f"✅ [{self.log_prefix}] Stockage réussi: {cache_key} | "
                f"TTL: {ttl_seconds}s | Taille: {data_size}"
            )
            return True

        except Exception as e:
            logger.error(f"❌ [{self.log_prefix}] Erreur de stockage: {cache_key} | Error: {e}")
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

        Utilisé après les opérations CRUD pour forcer le rechargement.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive")
            sub_type: Sous-type (ex: "employees", "details", "documents")

        Returns:
            True si succès, False sinon
        """
        cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        logger.info(f"🗑️ [{self.log_prefix}] Invalidation demandée: {cache_key}")

        try:
            redis_client = await self._get_redis_client()
            deleted = await redis_client.delete(cache_key)
            logger.info(f"✅ [{self.log_prefix}] Clé supprimée: {cache_key} | Deleted={deleted}")
            return True

        except Exception as e:
            logger.error(f"❌ [{self.log_prefix}] Erreur d'invalidation: {cache_key} | Error: {e}")
            return False

    async def invalidate_module_cache(
        self,
        user_id: str,
        company_id: str,
        data_type: str
    ) -> bool:
        """
        Invalide tout le cache d'un module pour une société et un utilisateur.

        Utilise SCAN pour éviter de bloquer Redis avec KEYS.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données (ex: "hr", "expenses", "drive")

        Returns:
            True si succès, False sinon
        """
        pattern = f"cache:{user_id}:{company_id}:{data_type}:*"
        logger.info(f"🗑️ [{self.log_prefix}] Invalidation module complète: {pattern}")

        try:
            redis_client = await self._get_redis_client()

            # SCAN au lieu de KEYS - ne bloque pas Redis
            cursor = 0
            keys_to_delete = []

            while True:
                # Scanner par lots de 100 clés à la fois
                cursor, batch = await redis_client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100
                )

                keys_to_delete.extend(batch)

                # Si cursor revient à 0, on a tout scanné
                if cursor == 0:
                    break

            logger.info(f"🔍 [{self.log_prefix}] Clés trouvées pour invalidation: {len(keys_to_delete)}")

            if keys_to_delete:
                # Supprimer par lots de 1000 max
                batch_size = 1000
                total_deleted = 0
                for i in range(0, len(keys_to_delete), batch_size):
                    batch = keys_to_delete[i:i+batch_size]
                    await redis_client.delete(*batch)
                    total_deleted += len(batch)
                    logger.debug(
                        f"🗑️ [{self.log_prefix}] Supprimé lot {i//batch_size + 1}: {len(batch)} clés"
                    )

                logger.info(
                    f"✅ [{self.log_prefix}] Invalidation réussie: {total_deleted} clés supprimées "
                    f"pour user={user_id}, company={company_id}, module={data_type}"
                )
            else:
                logger.info(f"ℹ️ [{self.log_prefix}] Aucune clé à invalider pour: {pattern}")

            return True

        except Exception as e:
            logger.error(f"❌ [{self.log_prefix}] Erreur d'invalidation: {pattern} | Error: {e}")
            return False

    async def get_cache_stats(
        self,
        user_id: str,
        company_id: str,
        data_type: str = None
    ) -> Dict:
        """
        Retourne les statistiques du cache pour une société.

        Utile pour le monitoring et le debugging.

        Args:
            user_id: Firebase UID de l'utilisateur
            company_id: UUID ou ID de la société
            data_type: Type de données optionnel (ex: "hr", "expenses")

        Returns:
            Dict avec statistiques (total_keys, data_types, etc.)
        """
        try:
            redis_client = await self._get_redis_client()

            if data_type:
                pattern = f"cache:{user_id}:{company_id}:{data_type}:*"
            else:
                pattern = f"cache:{user_id}:{company_id}:*"

            # SCAN pour trouver toutes les clés
            cursor = 0
            keys = []

            while True:
                cursor, batch = await redis_client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100
                )
                keys.extend(batch)
                if cursor == 0:
                    break

            stats = {
                "total_keys": len(keys),
                "data_types": {},
                "total_size_bytes": 0,
                "oldest_entry": None,
                "newest_entry": None
            }

            for key in keys:
                try:
                    data = await redis_client.get(key)
                    if data:
                        parsed = json.loads(data)

                        # Extraire le type de données depuis la clé
                        # Format: cache:user:company:TYPE:SUBTYPE
                        key_parts = key.split(":")
                        key_data_type = key_parts[3] if len(key_parts) > 3 else "unknown"

                        if key_data_type not in stats["data_types"]:
                            stats["data_types"][key_data_type] = 0
                        stats["data_types"][key_data_type] += 1

                        stats["total_size_bytes"] += len(data)

                        cached_at = parsed.get("cached_at")
                        if cached_at:
                            if not stats["oldest_entry"] or cached_at < stats["oldest_entry"]:
                                stats["oldest_entry"] = cached_at
                            if not stats["newest_entry"] or cached_at > stats["newest_entry"]:
                                stats["newest_entry"] = cached_at

                except Exception:
                    continue

            logger.info(f"📊 [{self.log_prefix}] Stats: {stats['total_keys']} clés, {stats['total_size_bytes']} bytes")
            return stats

        except Exception as e:
            logger.error(f"⚠️ [{self.log_prefix}] Stats error: {e}")
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCES SPÉCIALISÉES (pour chaque module)
# ═══════════════════════════════════════════════════════════════════════════

_firebase_cache_manager: Optional[UnifiedCacheManager] = None
_drive_cache_manager: Optional[UnifiedCacheManager] = None


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
