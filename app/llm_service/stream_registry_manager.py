"""
StreamRegistryManager - Gestionnaire de streaming distribué avec Redis.

Ce module permet de gérer les streams actifs inter-instances en utilisant Redis
pour l'enregistrement et Redis Pub/Sub pour les signaux d'arrêt cross-instance.

Architecture:
    - Clé Redis: stream:{user_id}:{company_id}:{thread_key}:active
    - TTL: 10 minutes (auto-expire si crash)
    - Pub/Sub Channel: signals:{user_id}
    - Format: JSON sérialisé avec métadonnées

Workflow:
    1. Instance A: register_stream() → Redis + garde asyncio.Task local
    2. Instance B: publish_stop_signal() → Redis Pub/Sub
    3. Instance A: reçoit signal → cancel() sur asyncio.Task local
    
Author: Scalability Team
Created: 2026-01-20
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import redis.asyncio as redis

logger = logging.getLogger("llm_service.stream_registry")


class StreamRegistryManager:
    """
    Gestionnaire de streaming distribué avec Redis Pub/Sub.
    
    Permet à n'importe quelle instance ECS d'arrêter un stream actif
    sur une autre instance via Redis Pub/Sub.
    """
    
    KEY_PREFIX = "stream"
    DEFAULT_TTL = 600  # 10 minutes
    PUBSUB_PREFIX = "signals"
    
    def __init__(self, redis_client=None):
        """
        Initialise le StreamRegistryManager.
        
        Args:
            redis_client: Client Redis async optionnel
        """
        self._redis = redis_client
        self._pubsub_task: Optional[asyncio.Task] = None
        self._stop_callbacks: Dict[str, callable] = {}  # {thread_key: callback}
        self._instance_id = os.getenv("HOSTNAME", "unknown")  # ECS task ID
    
    async def _get_redis_client(self) -> redis.Redis:
        """Lazy loading du client Redis async."""
        if self._redis is None:
            config = self._load_redis_config()
            self._redis = redis.Redis(
                host=config.get("host"),
                port=config.get("port", 6379),
                password=config.get("password"),
                ssl=config.get("tls", False),
                db=config.get("db", 0),
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        return self._redis
    
    def _load_redis_config(self) -> Dict:
        """Charge la configuration Redis depuis les variables d'environnement."""
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
    
    def _build_key(self, user_id: str, company_id: str, thread_key: str) -> str:
        """
        Construit la clé Redis pour un stream.
        
        Format: stream:{user_id}:{company_id}:{thread_key}:active
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:{thread_key}:active"
    
    def _build_pubsub_channel(self, user_id: str) -> str:
        """
        Construit le canal Pub/Sub pour un utilisateur.
        
        Format: signals:{user_id}
        """
        return f"{self.PUBSUB_PREFIX}:{user_id}"
    
    # ═══════════════════════════════════════════════════════════════
    # ENREGISTREMENT ET ARRÊT DES STREAMS
    # ═══════════════════════════════════════════════════════════════
    
    async def register_stream(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Enregistre un stream actif dans Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        now = datetime.now(timezone.utc).isoformat()
        
        state = {
            "status": "streaming",
            "started_at": now,
            "instance_id": self._instance_id
        }
        
        try:
            redis_client = await self._get_redis_client()
            await redis_client.setex(key, self.DEFAULT_TTL, json.dumps(state))
            
            logger.info(
                f"[STREAM_REGISTRY] ✅ Stream enregistré: "
                f"thread={thread_key}, instance={self._instance_id}"
            )
            return True
        except Exception as e:
            logger.error(f"[STREAM_REGISTRY] ❌ Erreur register_stream: {e}")
            return False
    
    async def unregister_stream(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Désenregistre un stream terminé.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            redis_client = await self._get_redis_client()
            await redis_client.delete(key)
            
            logger.info(
                f"[STREAM_REGISTRY] 🗑️ Stream désenregistré: thread={thread_key}"
            )
            return True
        except Exception as e:
            logger.error(f"[STREAM_REGISTRY] ❌ Erreur unregister_stream: {e}")
            return False
    
    async def is_stream_active(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Vérifie si un stream est actif.
        
        Returns:
            True si stream actif
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            redis_client = await self._get_redis_client()
            exists = await redis_client.exists(key)
            return exists > 0
        except Exception as e:
            logger.error(f"[STREAM_REGISTRY] ❌ Erreur is_stream_active: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # SIGNAUX CROSS-INSTANCE VIA REDIS PUB/SUB
    # ═══════════════════════════════════════════════════════════════
    
    async def publish_stop_signal(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Envoie un signal d'arrêt via Redis Pub/Sub.
        
        Cette méthode peut être appelée depuis n'importe quelle instance.
        L'instance qui a le stream local recevra le signal et arrêtera le stream.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            True si succès
        """
        channel = self._build_pubsub_channel(user_id)
        message = {
            "action": "stop_stream",
            "thread_key": thread_key,
            "company_id": company_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from_instance": self._instance_id
        }
        
        try:
            redis_client = await self._get_redis_client()
            await redis_client.publish(channel, json.dumps(message))
            
            logger.info(
                f"[STREAM_REGISTRY] 📡 Signal d'arrêt envoyé: "
                f"thread={thread_key}, channel={channel}"
            )
            return True
        except Exception as e:
            logger.error(f"[STREAM_REGISTRY] ❌ Erreur publish_stop_signal: {e}")
            return False
    
    async def start_listening(self, user_id: str):
        """
        Démarre l'écoute des signaux Pub/Sub pour cet utilisateur.
        
        Cette méthode doit être appelée au démarrage de la session.
        
        Args:
            user_id: ID Firebase de l'utilisateur
        """
        if self._pubsub_task and not self._pubsub_task.done():
            logger.warning(f"[STREAM_REGISTRY] Listener déjà actif pour {user_id}")
            return
        
        self._pubsub_task = asyncio.create_task(self._pubsub_listener(user_id))
        logger.info(f"[STREAM_REGISTRY] 👂 Listener démarré pour {user_id}")
    
    async def stop_listening(self):
        """Arrête l'écoute des signaux Pub/Sub."""
        if self._pubsub_task:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None
            logger.info("[STREAM_REGISTRY] 🛑 Listener arrêté")
    
    async def _pubsub_listener(self, user_id: str):
        """
        Boucle d'écoute Redis Pub/Sub.
        
        Écoute les signaux stop_stream et appelle les callbacks enregistrés.
        """
        redis_client = await self._get_redis_client()
        pubsub = redis_client.pubsub()
        channel = self._build_pubsub_channel(user_id)
        
        try:
            await pubsub.subscribe(channel)
            logger.info(f"[STREAM_REGISTRY] ✅ Abonné au canal: {channel}")
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        action = data.get("action")
                        
                        if action == "stop_stream":
                            thread_key = data.get("thread_key")
                            from_instance = data.get("from_instance", "unknown")
                            
                            logger.info(
                                f"[STREAM_REGISTRY] 📨 Signal reçu: stop_stream "
                                f"thread={thread_key}, from={from_instance}"
                            )
                            
                            # Appeler le callback si enregistré
                            callback = self._stop_callbacks.get(thread_key)
                            if callback:
                                try:
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback()
                                    else:
                                        callback()
                                    logger.info(
                                        f"[STREAM_REGISTRY] ✅ Callback exécuté pour {thread_key}"
                                    )
                                except Exception as cb_error:
                                    logger.error(
                                        f"[STREAM_REGISTRY] ❌ Erreur callback: {cb_error}"
                                    )
                            else:
                                logger.warning(
                                    f"[STREAM_REGISTRY] ⚠️ Pas de callback pour {thread_key}"
                                )
                    
                    except Exception as msg_error:
                        logger.error(
                            f"[STREAM_REGISTRY] ❌ Erreur traitement message: {msg_error}"
                        )
        
        except asyncio.CancelledError:
            logger.info(f"[STREAM_REGISTRY] Listener annulé pour {user_id}")
        except Exception as e:
            logger.error(f"[STREAM_REGISTRY] ❌ Erreur listener: {e}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
    
    def register_stop_callback(self, thread_key: str, callback: callable):
        """
        Enregistre un callback à appeler lors de la réception d'un signal stop.
        
        Args:
            thread_key: Clé du thread
            callback: Fonction à appeler (peut être async)
        """
        self._stop_callbacks[thread_key] = callback
        logger.debug(f"[STREAM_REGISTRY] Callback enregistré pour {thread_key}")
    
    def unregister_stop_callback(self, thread_key: str):
        """Désenregistre un callback."""
        self._stop_callbacks.pop(thread_key, None)
        logger.debug(f"[STREAM_REGISTRY] Callback désenregistré pour {thread_key}")


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_stream_registry_manager: Optional[StreamRegistryManager] = None


def get_stream_registry_manager() -> StreamRegistryManager:
    """
    Retourne l'instance singleton du StreamRegistryManager.
    
    Returns:
        Instance de StreamRegistryManager
    """
    global _stream_registry_manager
    if _stream_registry_manager is None:
        _stream_registry_manager = StreamRegistryManager()
    return _stream_registry_manager
