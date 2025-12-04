"""
ChatHistoryManager - Gestionnaire d'historique chat externalisé dans Redis.

Ce module permet aux PinnokioBrain de devenir stateless en externalisant
leur historique de chat dans Redis, permettant ainsi le scaling horizontal.

Architecture:
    - Clé Redis: chat:{user_id}:{company_id}:{thread_key}:history
    - TTL: 24 heures (conversations actives)
    - Format: JSON sérialisé avec métadonnées

Données externalisées:
    - messages: Liste des messages (user, assistant, tool_results)
    - system_prompt: System prompt actif (avec résumés éventuels)
    - metadata: Métadonnées du chat (created_at, last_activity, mode, etc.)
    - status: État du chat (active, idle, terminated)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List

logger = logging.getLogger("llm_service.chat_history")


class ChatHistoryManager:
    """
    Gestionnaire d'historique chat externalisé dans Redis.
    
    Permet aux PinnokioBrain de devenir stateless en stockant leur
    historique de chat dans Redis. Chaque instance du microservice
    peut ainsi reprendre une conversation créée par une autre instance.
    """
    
    # TTL par défaut: 24 heures
    DEFAULT_TTL = 86400
    
    # Préfixe pour les clés Redis
    KEY_PREFIX = "chat"
    
    def __init__(self, redis_client=None):
        """
        Initialise le ChatHistoryManager.
        
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
        Construit la clé Redis pour un historique de chat.
        
        Format: chat:{user_id}:{company_id}:{thread_key}:history
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:{thread_key}:history"
    
    def _serialize_messages(self, messages: List[Dict]) -> str:
        """Sérialise les messages en JSON."""
        return json.dumps(messages, ensure_ascii=False, default=str)
    
    def _deserialize_messages(self, json_str: str) -> List[Dict]:
        """Désérialise les messages depuis JSON."""
        return json.loads(json_str)
    
    # ═══════════════════════════════════════════════════════════════
    # OPÉRATIONS CRUD PRINCIPALES
    # ═══════════════════════════════════════════════════════════════
    
    def save_chat_history(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        messages: List[Dict],
        system_prompt: str = "",
        metadata: Optional[Dict] = None,
        status: str = "active",
        ttl: int = None
    ) -> bool:
        """
        Sauvegarde l'historique complet d'un chat dans Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société (collection_name)
            thread_key: Clé du thread de chat
            messages: Liste des messages (format Anthropic/OpenAI)
            system_prompt: System prompt actif
            metadata: Métadonnées additionnelles (chat_mode, etc.)
            status: Statut du chat (active, idle, terminated)
            ttl: TTL personnalisé (défaut: 24h)
            
        Returns:
            True si sauvegarde réussie
        """
        try:
            key = self._build_key(user_id, company_id, thread_key)
            
            history = {
                "messages": messages,
                "system_prompt": system_prompt,
                "metadata": metadata or {},
                "status": status,
                "message_count": len(messages),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "version": "1.0"
            }
            
            ttl_seconds = ttl or self.DEFAULT_TTL
            serialized = json.dumps(history, ensure_ascii=False, default=str)
            
            self.redis.setex(key, ttl_seconds, serialized)
            
            logger.debug(
                f"[CHAT_HISTORY] 💾 Historique sauvegardé: {thread_key} "
                f"({len(messages)} messages, TTL: {ttl_seconds}s)"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur sauvegarde: {e}", exc_info=True)
            return False
    
    def load_chat_history(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Charge l'historique d'un chat depuis Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            Dict avec l'historique, ou None si non trouvé
        """
        try:
            key = self._build_key(user_id, company_id, thread_key)
            
            data = self.redis.get(key)
            
            if not data:
                logger.debug(f"[CHAT_HISTORY] ❌ Historique non trouvé: {thread_key}")
                return None
            
            # Décoder bytes si nécessaire
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            
            history = json.loads(data)
            
            logger.debug(
                f"[CHAT_HISTORY] ✅ Historique chargé: {thread_key} "
                f"({history.get('message_count', 0)} messages)"
            )
            
            return history
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur chargement: {e}", exc_info=True)
            return None
    
    def get_messages(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> List[Dict]:
        """
        Récupère uniquement les messages d'un chat.
        
        Optimisation: Évite de charger tout l'historique.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            Liste des messages, ou liste vide si non trouvé
        """
        history = self.load_chat_history(user_id, company_id, thread_key)
        
        if history:
            return history.get("messages", [])
        return []
    
    def append_message(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        message: Dict,
        extend_ttl: bool = True
    ) -> bool:
        """
        Ajoute un message à l'historique de manière atomique.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            message: Message à ajouter (format Anthropic/OpenAI)
            extend_ttl: Prolonger le TTL à chaque ajout?
            
        Returns:
            True si ajout réussi
        """
        try:
            history = self.load_chat_history(user_id, company_id, thread_key)
            
            if history is None:
                # Créer un nouvel historique
                history = {
                    "messages": [],
                    "system_prompt": "",
                    "metadata": {},
                    "status": "active"
                }
            
            # Ajouter le message
            history["messages"].append(message)
            
            # Sauvegarder
            return self.save_chat_history(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                messages=history["messages"],
                system_prompt=history.get("system_prompt", ""),
                metadata=history.get("metadata"),
                status=history.get("status", "active"),
                ttl=self.DEFAULT_TTL if extend_ttl else None
            )
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur append_message: {e}", exc_info=True)
            return False
    
    def append_messages_batch(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        messages: List[Dict],
        extend_ttl: bool = True
    ) -> bool:
        """
        Ajoute plusieurs messages d'un coup (optimisation).
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            messages: Liste de messages à ajouter
            extend_ttl: Prolonger le TTL?
            
        Returns:
            True si ajout réussi
        """
        try:
            history = self.load_chat_history(user_id, company_id, thread_key)
            
            if history is None:
                history = {
                    "messages": [],
                    "system_prompt": "",
                    "metadata": {},
                    "status": "active"
                }
            
            # Ajouter tous les messages
            history["messages"].extend(messages)
            
            # Sauvegarder
            return self.save_chat_history(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                messages=history["messages"],
                system_prompt=history.get("system_prompt", ""),
                metadata=history.get("metadata"),
                status=history.get("status", "active"),
                ttl=self.DEFAULT_TTL if extend_ttl else None
            )
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur append_messages_batch: {e}", exc_info=True)
            return False
    
    def update_system_prompt(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        system_prompt: str
    ) -> bool:
        """
        Met à jour le system prompt d'un chat.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            system_prompt: Nouveau system prompt
            
        Returns:
            True si mise à jour réussie
        """
        try:
            history = self.load_chat_history(user_id, company_id, thread_key)
            
            if history is None:
                # Créer un nouvel historique avec le system prompt
                return self.save_chat_history(
                    user_id=user_id,
                    company_id=company_id,
                    thread_key=thread_key,
                    messages=[],
                    system_prompt=system_prompt,
                    metadata={},
                    status="active"
                )
            
            # Mettre à jour le system prompt
            history["system_prompt"] = system_prompt
            
            return self.save_chat_history(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                messages=history.get("messages", []),
                system_prompt=system_prompt,
                metadata=history.get("metadata"),
                status=history.get("status", "active")
            )
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur update_system_prompt: {e}", exc_info=True)
            return False
    
    def clear_messages(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        keep_system_prompt: bool = True
    ) -> bool:
        """
        Vide les messages d'un chat (garde le system prompt si demandé).
        
        Utilisé lors du reset de contexte avec résumé.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            keep_system_prompt: Conserver le system prompt?
            
        Returns:
            True si vidage réussi
        """
        try:
            history = self.load_chat_history(user_id, company_id, thread_key)
            
            system_prompt = ""
            metadata = {}
            
            if history and keep_system_prompt:
                system_prompt = history.get("system_prompt", "")
                metadata = history.get("metadata", {})
            
            return self.save_chat_history(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                messages=[],  # Vider les messages
                system_prompt=system_prompt,
                metadata=metadata,
                status="active"
            )
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur clear_messages: {e}", exc_info=True)
            return False
    
    def delete_chat_history(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Supprime complètement l'historique d'un chat.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            True si suppression réussie
        """
        try:
            key = self._build_key(user_id, company_id, thread_key)
            deleted = self.redis.delete(key)
            
            if deleted:
                logger.info(f"[CHAT_HISTORY] 🗑️ Historique supprimé: {thread_key}")
            else:
                logger.debug(f"[CHAT_HISTORY] Historique déjà absent: {thread_key}")
            
            return True
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur suppression: {e}", exc_info=True)
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # OPÉRATIONS SPÉCIFIQUES
    # ═══════════════════════════════════════════════════════════════
    
    def get_message_count(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> int:
        """
        Récupère le nombre de messages dans un chat.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread de chat
            
        Returns:
            Nombre de messages, ou 0 si chat non trouvé
        """
        history = self.load_chat_history(user_id, company_id, thread_key)
        
        if history:
            return history.get("message_count", len(history.get("messages", [])))
        return 0
    
    def chat_exists(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Vérifie si un historique de chat existe dans Redis.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            
        Returns:
            True si l'historique existe
        """
        key = self._build_key(user_id, company_id, thread_key)
        return bool(self.redis.exists(key))
    
    def extend_ttl(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        ttl: int = None
    ) -> bool:
        """
        Prolonge le TTL d'un historique de chat.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            ttl: Nouveau TTL en secondes (défaut: 24h)
            
        Returns:
            True si TTL prolongé
        """
        try:
            key = self._build_key(user_id, company_id, thread_key)
            ttl_seconds = ttl or self.DEFAULT_TTL
            
            result = self.redis.expire(key, ttl_seconds)
            
            if result:
                logger.debug(f"[CHAT_HISTORY] ⏰ TTL prolongé: {thread_key} ({ttl_seconds}s)")
            
            return bool(result)
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur extend_ttl: {e}", exc_info=True)
            return False
    
    def update_status(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        status: str
    ) -> bool:
        """
        Met à jour le statut d'un chat.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            status: Nouveau statut (active, idle, terminated)
            
        Returns:
            True si mise à jour réussie
        """
        try:
            history = self.load_chat_history(user_id, company_id, thread_key)
            
            if history is None:
                return False
            
            history["status"] = status
            
            return self.save_chat_history(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                messages=history.get("messages", []),
                system_prompt=history.get("system_prompt", ""),
                metadata=history.get("metadata"),
                status=status
            )
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur update_status: {e}", exc_info=True)
            return False
    
    def update_metadata(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        metadata_updates: Dict
    ) -> bool:
        """
        Met à jour les métadonnées d'un chat.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            metadata_updates: Dict des métadonnées à mettre à jour
            
        Returns:
            True si mise à jour réussie
        """
        try:
            history = self.load_chat_history(user_id, company_id, thread_key)
            
            if history is None:
                return False
            
            metadata = history.get("metadata", {})
            metadata.update(metadata_updates)
            
            return self.save_chat_history(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                messages=history.get("messages", []),
                system_prompt=history.get("system_prompt", ""),
                metadata=metadata,
                status=history.get("status", "active")
            )
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur update_metadata: {e}", exc_info=True)
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # UTILITAIRES
    # ═══════════════════════════════════════════════════════════════
    
    def list_user_chats(
        self,
        user_id: str,
        company_id: str
    ) -> List[str]:
        """
        Liste tous les threads de chat d'un utilisateur pour une société.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            
        Returns:
            Liste des thread_key
        """
        try:
            pattern = f"{self.KEY_PREFIX}:{user_id}:{company_id}:*:history"
            keys = list(self.redis.scan_iter(match=pattern))
            
            thread_keys = []
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                # chat:{user_id}:{company_id}:{thread_key}:history
                parts = key.split(":")
                if len(parts) >= 4:
                    thread_keys.append(parts[3])
            
            return thread_keys
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur list_user_chats: {e}")
            return []
    
    def get_chat_stats(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Retourne des statistiques sur les chats d'un utilisateur.
        
        Returns:
            Dict avec statistiques (total_chats, total_messages, etc.)
        """
        try:
            thread_keys = self.list_user_chats(user_id, company_id)
            
            stats = {
                "total_chats": len(thread_keys),
                "total_messages": 0,
                "active_chats": 0,
                "chats_by_status": {}
            }
            
            for thread_key in thread_keys:
                history = self.load_chat_history(user_id, company_id, thread_key)
                if history:
                    stats["total_messages"] += history.get("message_count", 0)
                    status = history.get("status", "unknown")
                    stats["chats_by_status"][status] = stats["chats_by_status"].get(status, 0) + 1
                    if status == "active":
                        stats["active_chats"] += 1
            
            return stats
            
        except Exception as e:
            logger.error(f"[CHAT_HISTORY] ❌ Erreur get_chat_stats: {e}")
            return {"error": str(e)}
    
    def estimate_token_count(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> int:
        """
        Estime le nombre de tokens dans un historique de chat.
        
        Estimation: ~4 caractères = 1 token
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            
        Returns:
            Estimation du nombre de tokens
        """
        history = self.load_chat_history(user_id, company_id, thread_key)
        
        if not history:
            return 0
        
        total_chars = 0
        
        # Compter les caractères du system prompt
        system_prompt = history.get("system_prompt", "")
        total_chars += len(system_prompt)
        
        # Compter les caractères des messages
        for message in history.get("messages", []):
            content = message.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # Format multi-block (Anthropic)
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            total_chars += len(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            total_chars += len(str(block.get("content", "")))
        
        # Estimation: 4 caractères = 1 token
        return total_chars // 4


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_chat_history_manager: Optional[ChatHistoryManager] = None


def get_chat_history_manager() -> ChatHistoryManager:
    """
    Récupère l'instance singleton du ChatHistoryManager.
    """
    global _chat_history_manager
    if _chat_history_manager is None:
        _chat_history_manager = ChatHistoryManager()
    return _chat_history_manager

