"""
ApprovalStateManager - Gestionnaire d'état des approbations externalisé dans Redis.

Ce module permet de gérer les approbations inter-instances en stockant l'état
dans Redis au lieu d'utiliser asyncio.Future en mémoire locale.

Architecture:
    - Clé Redis: approval:{user_id}:{thread_key}:{card_message_id}:state
    - TTL: 20 minutes (> timeout approbation de 15 min)
    - Format: JSON sérialisé avec métadonnées

Workflow:
    1. Instance A: create_pending_approval() → Redis
    2. Instance A: poll Redis jusqu'à résolution ou timeout
    3. Instance B (ou A): resolve_approval() → mise à jour Redis
    4. Instance A: détecte le changement et retourne le résultat

Author: Scalability Team
Created: 2026-01-20
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger("llm_service.approval_state")


class ApprovalStateManager:
    """
    Gestionnaire d'état des approbations externalisé dans Redis.
    
    Remplace le système local asyncio.Future par un système distribué
    permettant à n'importe quelle instance ECS de résoudre une approbation.
    """
    
    KEY_PREFIX = "approval"
    DEFAULT_TTL = 1200  # 20 minutes (> 15 min timeout)
    
    def __init__(self, redis_client=None):
        """
        Initialise le ApprovalStateManager.
        
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
    
    def _build_key(self, user_id: str, thread_key: str, card_message_id: str) -> str:
        """
        Construit la clé Redis pour une approbation.
        
        Format: approval:{user_id}:{thread_key}:{card_message_id}:state
        """
        return f"{self.KEY_PREFIX}:{user_id}:{thread_key}:{card_message_id}:state"
    
    # ═══════════════════════════════════════════════════════════════
    # CRÉATION ET RÉSOLUTION D'APPROBATIONS
    # ═══════════════════════════════════════════════════════════════
    
    def create_pending_approval(
        self,
        user_id: str,
        thread_key: str,
        card_message_id: str,
        card_type: str,
        card_params: Dict[str, Any],
        timeout: int = 900
    ) -> bool:
        """
        Crée une nouvelle approbation en attente dans Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            thread_key: Clé du thread de chat
            card_message_id: ID unique du message carte
            card_type: Type de carte (approval_card, text_modification_approval, etc.)
            card_params: Paramètres de la carte
            timeout: Timeout en secondes (défaut 900s = 15min)
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, thread_key, card_message_id)
        now = datetime.now(timezone.utc).isoformat()
        
        state = {
            "status": "pending",
            "card_type": card_type,
            "card_params": card_params,
            "created_at": now,
            "timeout": timeout,
            "responded_at": None,
            "action": None,
            "user_message": None
        }
        
        try:
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            logger.info(
                f"[APPROVAL_STATE] ✅ Approbation créée: "
                f"card={card_message_id}, type={card_type}, timeout={timeout}s"
            )
            return True
        except Exception as e:
            logger.error(f"[APPROVAL_STATE] ❌ Erreur create_pending_approval: {e}")
            return False
    
    def resolve_approval(
        self,
        user_id: str,
        thread_key: str,
        card_message_id: str,
        action: str,
        user_message: str = ""
    ) -> bool:
        """
        Résout une approbation en attente (approve/reject).
        
        Cette méthode peut être appelée depuis n'importe quelle instance ECS.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            thread_key: Clé du thread de chat
            card_message_id: ID unique du message carte
            action: Action utilisateur ('approve', 'reject', 'approve_four_eyes', etc.)
            user_message: Commentaire optionnel de l'utilisateur
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, thread_key, card_message_id)
        
        try:
            # Récupérer l'état actuel
            data = self.redis.get(key)
            if not data:
                logger.warning(
                    f"[APPROVAL_STATE] ⚠️ Approbation introuvable: {card_message_id}"
                )
                return False
            
            state = json.loads(data)
            
            # Vérifier que l'approbation est toujours en attente
            if state.get("status") != "pending":
                logger.warning(
                    f"[APPROVAL_STATE] ⚠️ Approbation déjà résolue: "
                    f"{card_message_id}, status={state.get('status')}"
                )
                return False
            
            # Mettre à jour l'état
            state["status"] = "approved" if "approve" in action else "rejected"
            state["action"] = action
            state["user_message"] = user_message
            state["responded_at"] = datetime.now(timezone.utc).isoformat()
            
            # Sauvegarder avec TTL court (5 min) pour permettre la lecture
            self.redis.setex(key, 300, json.dumps(state))
            
            logger.info(
                f"[APPROVAL_STATE] ✅ Approbation résolue: "
                f"card={card_message_id}, action={action}, status={state['status']}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[APPROVAL_STATE] ❌ Erreur resolve_approval: {e}")
            return False
    
    def get_approval_state(
        self,
        user_id: str,
        thread_key: str,
        card_message_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère l'état actuel d'une approbation.
        
        Utilisé pour le polling depuis request_approval_with_card().
        
        Returns:
            Dict avec l'état ou None si non trouvé
        """
        key = self._build_key(user_id, thread_key, card_message_id)
        
        try:
            data = self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"[APPROVAL_STATE] ❌ Erreur get_approval_state: {e}")
            return None
    
    def mark_timeout(
        self,
        user_id: str,
        thread_key: str,
        card_message_id: str
    ) -> bool:
        """
        Marque une approbation comme expirée (timeout).
        
        Args:
            user_id: ID Firebase de l'utilisateur
            thread_key: Clé du thread de chat
            card_message_id: ID unique du message carte
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, thread_key, card_message_id)
        
        try:
            data = self.redis.get(key)
            if not data:
                return False
            
            state = json.loads(data)
            
            # Ne marquer que si encore pending
            if state.get("status") == "pending":
                state["status"] = "timeout"
                state["responded_at"] = datetime.now(timezone.utc).isoformat()
                
                # Garder 5 min pour debug
                self.redis.setex(key, 300, json.dumps(state))
                
                logger.info(
                    f"[APPROVAL_STATE] ⏰ Approbation timeout: {card_message_id}"
                )
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"[APPROVAL_STATE] ❌ Erreur mark_timeout: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # UTILITAIRES
    # ═══════════════════════════════════════════════════════════════
    
    def cleanup_expired(self, user_id: str, thread_key: str) -> int:
        """
        Nettoie les approbations expirées pour un thread.
        
        Returns:
            Nombre d'approbations nettoyées
        """
        try:
            pattern = f"{self.KEY_PREFIX}:{user_id}:{thread_key}:*:state"
            keys = self.redis.keys(pattern)
            
            cleaned = 0
            for key in keys:
                data = self.redis.get(key)
                if data:
                    state = json.loads(data)
                    # Nettoyer si timeout ou très ancien
                    if state.get("status") == "timeout":
                        self.redis.delete(key)
                        cleaned += 1
            
            if cleaned > 0:
                logger.info(
                    f"[APPROVAL_STATE] 🧹 Nettoyage: {cleaned} approbations expirées"
                )
            
            return cleaned
            
        except Exception as e:
            logger.error(f"[APPROVAL_STATE] ❌ Erreur cleanup_expired: {e}")
            return 0


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_approval_state_manager: Optional[ApprovalStateManager] = None


def get_approval_state_manager() -> ApprovalStateManager:
    """
    Retourne l'instance singleton du ApprovalStateManager.
    
    Returns:
        Instance de ApprovalStateManager
    """
    global _approval_state_manager
    if _approval_state_manager is None:
        _approval_state_manager = ApprovalStateManager()
    return _approval_state_manager
