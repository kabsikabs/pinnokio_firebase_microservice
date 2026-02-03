"""
SessionRegistryManager - Gestionnaire du registre des sessions distribué.

Ce module permet de tracker les sessions actives inter-instances en utilisant Redis.
Cela permet de savoir si une session existe déjà sur une autre instance ECS.

Architecture:
    - Clé Redis: session:registry:{user_id}:{company_id}
    - TTL: 2 heures (même que SessionStateManager)
    - Format: JSON sérialisé avec métadonnées

Workflow:
    1. Créer session → register() dans Redis
    2. get_or_create → check exists() dans Redis
    3. Si existe → reconstruire depuis SessionStateManager
    4. Si n'existe pas → créer nouvelle session

Author: Scalability Team
Created: 2026-01-20
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger("llm_service.session_registry")


class SessionRegistryManager:
    """
    Gestionnaire du registre des sessions distribué.
    
    Permet de savoir si une session existe déjà sur une autre instance ECS.
    """
    
    KEY_PREFIX = "session:registry"
    DEFAULT_TTL = 7200  # 2 heures
    
    def __init__(self, redis_client=None):
        """
        Initialise le SessionRegistryManager.
        
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
    
    def _build_key(self, user_id: str, company_id: str) -> str:
        """
        Construit la clé Redis pour une session.
        
        Format: session:registry:{user_id}:{company_id}
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}"
    
    # ═══════════════════════════════════════════════════════════════
    # ENREGISTREMENT ET VÉRIFICATION
    # ═══════════════════════════════════════════════════════════════
    
    def register(
        self,
        user_id: str,
        company_id: str,
        instance_id: Optional[str] = None
    ) -> bool:
        """
        Enregistre une session dans le registre.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            instance_id: ID de l'instance ECS (optionnel, pour debug)
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id)
        now = datetime.now(timezone.utc).isoformat()
        
        registry = {
            "session_key": f"{user_id}:{company_id}",
            "created_at": now,
            "last_activity": now,
            "instance_id": instance_id or "unknown"
        }
        
        try:
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(registry))
            
            logger.debug(
                f"[SESSION_REGISTRY] ✅ Session enregistrée: "
                f"user={user_id[:8]}..., company={company_id}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_REGISTRY] ❌ Erreur register: {e}")
            return False
    
    def exists(self, user_id: str, company_id: str) -> bool:
        """
        Vérifie si une session existe dans le registre.
        
        Returns:
            True si la session existe (même sur autre instance)
        """
        key = self._build_key(user_id, company_id)
        
        try:
            exists = self.redis.exists(key)
            return bool(exists)
            
        except Exception as e:
            logger.error(f"[SESSION_REGISTRY] ❌ Erreur exists: {e}")
            return False
    
    def update_activity(
        self,
        user_id: str,
        company_id: str
    ) -> bool:
        """
        Met à jour le timestamp last_activity.
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id)
        
        try:
            data = self.redis.get(key)
            if not data:
                return False
            
            registry = json.loads(data)
            registry["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(registry))
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_REGISTRY] ❌ Erreur update_activity: {e}")
            return False
    
    def get_info(
        self,
        user_id: str,
        company_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère les informations d'une session.
        
        Returns:
            Dict avec les infos ou None si non trouvé
        """
        key = self._build_key(user_id, company_id)
        
        try:
            data = self.redis.get(key)
            if data:
                return json.loads(data)
            return None
            
        except Exception as e:
            logger.error(f"[SESSION_REGISTRY] ❌ Erreur get_info: {e}")
            return None
    
    def unregister(
        self,
        user_id: str,
        company_id: str
    ) -> bool:
        """
        Supprime une session du registre.
        
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id)
        
        try:
            self.redis.delete(key)
            
            logger.info(
                f"[SESSION_REGISTRY] 🗑️ Session désenregistrée: "
                f"user={user_id[:8]}..., company={company_id}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_REGISTRY] ❌ Erreur unregister: {e}")
            return False


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_session_registry_manager: Optional[SessionRegistryManager] = None


def get_session_registry_manager() -> SessionRegistryManager:
    """
    Retourne l'instance singleton du SessionRegistryManager.
    
    Returns:
        Instance de SessionRegistryManager
    """
    global _session_registry_manager
    if _session_registry_manager is None:
        _session_registry_manager = SessionRegistryManager()
    return _session_registry_manager
