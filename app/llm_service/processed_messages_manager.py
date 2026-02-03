"""
ProcessedMessagesManager - Gestionnaire de déduplication des messages avec Redis.

Ce module permet de gérer la déduplication des messages onboarding de manière
distribuée en utilisant Redis SET au lieu d'un Set local en mémoire.

Architecture:
    - Clé Redis: processed:{user_id}:{company_id}:{thread_key}
    - Type: SET (optimisé pour SISMEMBER O(1))
    - TTL: 24 heures (messages anciens auto-nettoyés)

Usage:
    manager = get_processed_messages_manager()
    if await manager.is_processed(user_id, company_id, thread_key, message_id):
        return  # Skip
    await manager.mark_processed(user_id, company_id, thread_key, message_id)

Author: Scalability Team
Created: 2026-01-20
"""

import logging
from typing import Optional, Set

logger = logging.getLogger("llm_service.processed_messages")


class ProcessedMessagesManager:
    """
    Gestionnaire de déduplication des messages avec Redis SET.
    
    Remplace Dict[str, Set[str]] local par Redis SET distribué.
    """
    
    KEY_PREFIX = "processed"
    DEFAULT_TTL = 86400  # 24 heures
    
    def __init__(self, redis_client=None):
        """
        Initialise le ProcessedMessagesManager.
        
        Args:
            redis_client: Client Redis optionnel (utilise get_redis() si non fourni)
        """
        self._redis = redis_client
    
    @property
    def redis(self):
        """Lazy loading du client Redis."""
        if self._redis is None:
            from ..redis_client import get_redis
            self._redis = get_redis()
        return self._redis
    
    def _build_key(self, user_id: str, company_id: str, thread_key: str) -> str:
        """
        Construit la clé Redis pour un thread.
        
        Format: processed:{user_id}:{company_id}:{thread_key}
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:{thread_key}"
    
    # ═══════════════════════════════════════════════════════════════
    # VÉRIFICATION ET MARQUAGE
    # ═══════════════════════════════════════════════════════════════
    
    def is_processed(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        message_id: str
    ) -> bool:
        """
        Vérifie si un message a déjà été traité.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            message_id: ID du message
            
        Returns:
            True si déjà traité
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            # SISMEMBER = O(1)
            exists = self.redis.sismember(key, message_id)
            
            if exists:
                logger.debug(
                    f"[PROCESSED_MSG] ✅ Message déjà traité: "
                    f"thread={thread_key}, msg={message_id}"
                )
            
            return bool(exists)
            
        except Exception as e:
            logger.error(f"[PROCESSED_MSG] ❌ Erreur is_processed: {e}")
            # En cas d'erreur Redis, laisser passer (mieux que bloquer)
            return False
    
    def mark_processed(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        message_id: str,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Marque un message comme traité.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            message_id: ID du message
            ttl: TTL personnalisé (ou DEFAULT_TTL)
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        ttl = ttl or self.DEFAULT_TTL
        
        try:
            # Ajouter au SET
            self.redis.sadd(key, message_id)
            
            # Mettre à jour le TTL (reset à chaque nouveau message)
            self.redis.expire(key, ttl)
            
            logger.debug(
                f"[PROCESSED_MSG] ✅ Message marqué traité: "
                f"thread={thread_key}, msg={message_id}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[PROCESSED_MSG] ❌ Erreur mark_processed: {e}")
            return False
    
    def mark_many_processed(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        message_ids: Set[str],
        ttl: Optional[int] = None
    ) -> bool:
        """
        Marque plusieurs messages comme traités (bulk).
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            message_ids: Set d'IDs de messages
            ttl: TTL personnalisé (ou DEFAULT_TTL)
            
        Returns:
            True si succès
        """
        if not message_ids:
            return True
        
        key = self._build_key(user_id, company_id, thread_key)
        ttl = ttl or self.DEFAULT_TTL
        
        try:
            # Ajouter tous les IDs au SET
            self.redis.sadd(key, *message_ids)
            
            # Mettre à jour le TTL
            self.redis.expire(key, ttl)
            
            logger.debug(
                f"[PROCESSED_MSG] ✅ {len(message_ids)} messages marqués traités: "
                f"thread={thread_key}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[PROCESSED_MSG] ❌ Erreur mark_many_processed: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # RÉCUPÉRATION ET NETTOYAGE
    # ═══════════════════════════════════════════════════════════════
    
    def get_processed_ids(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> Set[str]:
        """
        Récupère tous les IDs traités pour un thread.
        
        Returns:
            Set d'IDs de messages
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            # SMEMBERS = O(N) mais N est petit (~dizaines de messages)
            ids = self.redis.smembers(key)
            return set(ids) if ids else set()
            
        except Exception as e:
            logger.error(f"[PROCESSED_MSG] ❌ Erreur get_processed_ids: {e}")
            return set()
    
    def count_processed(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> int:
        """
        Compte le nombre de messages traités.
        
        Returns:
            Nombre de messages
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            # SCARD = O(1)
            count = self.redis.scard(key)
            return int(count) if count else 0
            
        except Exception as e:
            logger.error(f"[PROCESSED_MSG] ❌ Erreur count_processed: {e}")
            return 0
    
    def clear_thread(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Supprime tous les IDs traités pour un thread.
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            self.redis.delete(key)
            
            logger.info(
                f"[PROCESSED_MSG] 🗑️ IDs traités supprimés: thread={thread_key}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[PROCESSED_MSG] ❌ Erreur clear_thread: {e}")
            return False


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_processed_messages_manager: Optional[ProcessedMessagesManager] = None


def get_processed_messages_manager() -> ProcessedMessagesManager:
    """
    Retourne l'instance singleton du ProcessedMessagesManager.
    
    Returns:
        Instance de ProcessedMessagesManager
    """
    global _processed_messages_manager
    if _processed_messages_manager is None:
        _processed_messages_manager = ProcessedMessagesManager()
    return _processed_messages_manager
