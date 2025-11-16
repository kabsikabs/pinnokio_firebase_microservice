"""
Buffer Redis pour stocker temporairement les messages WebSocket en attente.

Ce module gère le problème de timing où les messages d'intermédiation sont envoyés
avant que le WebSocket spécifique du chat soit connecté.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import timedelta


logger = logging.getLogger("ws.buffer")


class WebSocketMessageBuffer:
    """Gère le buffering de messages WebSocket dans Redis."""
    
    def __init__(self, redis_client=None):
        """
        Initialise le buffer de messages.
        
        Args:
            redis_client: Client Redis (si None, sera initialisé à la demande)
        """
        self._redis_client = redis_client
        self.ttl_seconds = 30  # TTL pour les messages bufferisés
    
    def _get_redis_client(self):
        """Récupère le client Redis (lazy loading)."""
        if self._redis_client is None:
            from .redis_client import get_redis
            self._redis_client = get_redis()
        return self._redis_client
    
    def _generate_key(self, user_id: str, thread_key: str) -> str:
        """
        Génère la clé Redis pour stocker les messages bufferisés.
        
        Args:
            user_id: ID de l'utilisateur
            thread_key: Clé du thread de chat
            
        Returns:
            Clé Redis au format: pending_ws_messages:{user_id}:{thread_key}
        """
        return f"pending_ws_messages:{user_id}:{thread_key}"
    
    def store_pending_message(
        self,
        user_id: str,
        thread_key: str,
        message: Dict[str, Any]
    ) -> bool:
        """
        Stocke un message WebSocket en attente dans Redis.
        
        Args:
            user_id: ID de l'utilisateur
            thread_key: Clé du thread de chat
            message: Message WebSocket à stocker (format hub.broadcast)
            
        Returns:
            True si succès, False sinon
        """
        try:
            redis_client = self._get_redis_client()
            key = self._generate_key(user_id, thread_key)
            
            # Sérialiser le message en JSON
            message_json = json.dumps(message)
            
            # Ajouter le message à une liste Redis (RPUSH)
            redis_client.rpush(key, message_json)
            
            # Définir le TTL (expire après 30 secondes)
            redis_client.expire(key, self.ttl_seconds)
            
            message_type = message.get("type", "unknown")
            logger.info(
                f"[WS_BUFFER] 📦 Message bufferisé - "
                f"user={user_id} thread={thread_key} type={message_type}"
            )
            
            return True
            
        except Exception as e:
            logger.error(
                f"[WS_BUFFER] ❌ Erreur stockage message - "
                f"user={user_id} thread={thread_key} error={e}",
                exc_info=True
            )
            return False
    
    def get_pending_messages(
        self,
        user_id: str,
        thread_key: str,
        delete_after: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Récupère les messages WebSocket en attente depuis Redis.
        
        Args:
            user_id: ID de l'utilisateur
            thread_key: Clé du thread de chat
            delete_after: Si True, supprime les messages après lecture
            
        Returns:
            Liste des messages bufferisés (format hub.broadcast)
        """
        try:
            redis_client = self._get_redis_client()
            key = self._generate_key(user_id, thread_key)
            
            # Récupérer tous les messages de la liste
            message_jsons = redis_client.lrange(key, 0, -1)
            
            if not message_jsons:
                logger.debug(
                    f"[WS_BUFFER] ℹ️ Aucun message en attente - "
                    f"user={user_id} thread={thread_key}"
                )
                return []
            
            # Désérialiser les messages
            messages = []
            for msg_json in message_jsons:
                try:
                    if isinstance(msg_json, bytes):
                        msg_json = msg_json.decode('utf-8')
                    message = json.loads(msg_json)
                    messages.append(message)
                except Exception as e:
                    logger.error(
                        f"[WS_BUFFER] ⚠️ Erreur désérialisation message: {e}"
                    )
            
            # Supprimer la clé après lecture si demandé
            if delete_after and messages:
                redis_client.delete(key)
                logger.info(
                    f"[WS_BUFFER] 🗑️ Messages supprimés après récupération - "
                    f"user={user_id} thread={thread_key} count={len(messages)}"
                )
            
            logger.info(
                f"[WS_BUFFER] 📬 Messages récupérés - "
                f"user={user_id} thread={thread_key} count={len(messages)}"
            )
            
            return messages
            
        except Exception as e:
            logger.error(
                f"[WS_BUFFER] ❌ Erreur récupération messages - "
                f"user={user_id} thread={thread_key} error={e}",
                exc_info=True
            )
            return []
    
    def has_pending_messages(self, user_id: str, thread_key: str) -> bool:
        """
        Vérifie s'il y a des messages en attente pour un thread.
        
        Args:
            user_id: ID de l'utilisateur
            thread_key: Clé du thread de chat
            
        Returns:
            True si des messages sont en attente, False sinon
        """
        try:
            redis_client = self._get_redis_client()
            key = self._generate_key(user_id, thread_key)
            count = redis_client.llen(key)
            return count > 0
        except Exception as e:
            logger.error(
                f"[WS_BUFFER] ❌ Erreur vérification messages: {e}"
            )
            return False
    
    def clear_pending_messages(self, user_id: str, thread_key: str) -> bool:
        """
        Supprime tous les messages en attente pour un thread.
        
        Args:
            user_id: ID de l'utilisateur
            thread_key: Clé du thread de chat
            
        Returns:
            True si succès, False sinon
        """
        try:
            redis_client = self._get_redis_client()
            key = self._generate_key(user_id, thread_key)
            redis_client.delete(key)
            logger.info(
                f"[WS_BUFFER] 🗑️ Messages supprimés - "
                f"user={user_id} thread={thread_key}"
            )
            return True
        except Exception as e:
            logger.error(
                f"[WS_BUFFER] ❌ Erreur suppression messages: {e}"
            )
            return False


# Singleton global
_message_buffer: Optional[WebSocketMessageBuffer] = None


def get_message_buffer() -> WebSocketMessageBuffer:
    """Récupère l'instance singleton du buffer de messages."""
    global _message_buffer
    if _message_buffer is None:
        _message_buffer = WebSocketMessageBuffer()
    return _message_buffer

