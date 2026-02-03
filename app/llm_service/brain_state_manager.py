"""
BrainStateManager - Gestionnaire d'état des brains distribué.

Ce module permet de gérer l'état des brains (active_plans, active_lpt_tasks)
de manière distribuée en utilisant Redis au lieu d'un Dict local.

Architecture:
    - Clé Redis: brain:{user_id}:{company_id}:{thread_key}:state
    - TTL: 1 heure
    - Format: JSON sérialisé avec métadonnées

Workflow:
    1. Créer brain → save_state() dans Redis
    2. get_or_create_brain → check exists() + load_state()
    3. Modifier state → save_state()
    4. Fermer brain → delete_state()

Author: Scalability Team
Created: 2026-01-20
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger("llm_service.brain_state")


class BrainStateManager:
    """
    Gestionnaire d'état des brains distribué.
    
    Permet de persister et récupérer l'état des brains (active_plans, active_lpt_tasks)
    pour permettre la reconstruction inter-instances.
    """
    
    KEY_PREFIX = "brain"
    DEFAULT_TTL = 3600  # 1 heure
    
    def __init__(self, redis_client=None):
        """
        Initialise le BrainStateManager.
        
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
        Construit la clé Redis pour un brain.
        
        Format: brain:{user_id}:{company_id}:{thread_key}:state
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:{thread_key}:state"
    
    # ═══════════════════════════════════════════════════════════════
    # SAUVEGARDE ET CHARGEMENT
    # ═══════════════════════════════════════════════════════════════
    
    def save_state(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        active_plans: Optional[Dict] = None,
        active_lpt_tasks: Optional[Dict] = None,
        mode: Optional[str] = None,
        additional_data: Optional[Dict] = None
    ) -> bool:
        """
        Sauvegarde l'état d'un brain dans Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            active_plans: Plans actifs (dict)
            active_lpt_tasks: Tâches LPT actives (dict)
            mode: Mode du chat (ex: general_chat, onboarding_chat)
            additional_data: Données supplémentaires optionnelles
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        now = datetime.now(timezone.utc).isoformat()
        
        state = {
            "active_plans": active_plans or {},
            "active_lpt_tasks": active_lpt_tasks or {},
            "mode": mode,
            "last_activity": now,
            **(additional_data or {})
        }
        
        try:
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            
            logger.debug(
                f"[BRAIN_STATE] ✅ État sauvegardé: thread={thread_key}, "
                f"plans={len(active_plans or {})}, tasks={len(active_lpt_tasks or {})}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur save_state: {e}")
            return False
    
    def load_state(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Charge l'état d'un brain depuis Redis.
        
        Returns:
            Dict avec l'état ou None si non trouvé
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            data = self.redis.get(key)
            if data:
                state = json.loads(data)
                
                logger.debug(
                    f"[BRAIN_STATE] ✅ État chargé: thread={thread_key}, "
                    f"plans={len(state.get('active_plans', {}))}, "
                    f"tasks={len(state.get('active_lpt_tasks', {}))}"
                )
                return state
            
            return None
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur load_state: {e}")
            return None
    
    def exists(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Vérifie si un brain existe dans Redis.
        
        Returns:
            True si existe
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            exists = self.redis.exists(key)
            return bool(exists)
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur exists: {e}")
            return False
    
    def delete_state(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Supprime l'état d'un brain.
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            self.redis.delete(key)
            
            logger.info(
                f"[BRAIN_STATE] 🗑️ État supprimé: thread={thread_key}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur delete_state: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # MISE À JOUR PARTIELLE
    # ═══════════════════════════════════════════════════════════════
    
    def update_plans(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        active_plans: Dict
    ) -> bool:
        """
        Met à jour uniquement les active_plans.
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            data = self.redis.get(key)
            if not data:
                # Si le brain n'existe pas encore, créer un nouvel état
                return self.save_state(
                    user_id, company_id, thread_key,
                    active_plans=active_plans
                )
            
            state = json.loads(data)
            state["active_plans"] = active_plans
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            
            logger.debug(
                f"[BRAIN_STATE] ✅ Plans mis à jour: thread={thread_key}, "
                f"count={len(active_plans)}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur update_plans: {e}")
            return False
    
    def update_lpt_tasks(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        active_lpt_tasks: Dict
    ) -> bool:
        """
        Met à jour uniquement les active_lpt_tasks.
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            data = self.redis.get(key)
            if not data:
                # Si le brain n'existe pas encore, créer un nouvel état
                return self.save_state(
                    user_id, company_id, thread_key,
                    active_lpt_tasks=active_lpt_tasks
                )
            
            state = json.loads(data)
            state["active_lpt_tasks"] = active_lpt_tasks
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            
            logger.debug(
                f"[BRAIN_STATE] ✅ LPT tasks mis à jour: thread={thread_key}, "
                f"count={len(active_lpt_tasks)}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur update_lpt_tasks: {e}")
            return False
    
    def update_activity(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Met à jour uniquement le timestamp last_activity (refresh TTL).
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            data = self.redis.get(key)
            if not data:
                return False
            
            state = json.loads(data)
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            return True
            
        except Exception as e:
            logger.error(f"[BRAIN_STATE] ❌ Erreur update_activity: {e}")
            return False


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_brain_state_manager: Optional[BrainStateManager] = None


def get_brain_state_manager() -> BrainStateManager:
    """
    Retourne l'instance singleton du BrainStateManager.
    
    Returns:
        Instance de BrainStateManager
    """
    global _brain_state_manager
    if _brain_state_manager is None:
        _brain_state_manager = BrainStateManager()
    return _brain_state_manager
