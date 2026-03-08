"""
SessionStateManager - Gestionnaire d'état session externalisé dans Redis.

Ce module permet de rendre les LLMSession stateless en externalisant
leur état dans Redis, permettant ainsi le scaling horizontal.

Architecture:
    - Clé Redis: session:{user_id}:{company_id}:state
    - TTL: 2 heures (prolongé à chaque activité)
    - Format: JSON sérialisé avec métadonnées

Données externalisées:
    - user_context: Métadonnées company (mandate_path, client_uuid, etc.)
    - jobs_data: Jobs par département (APBOOKEEPER, ROUTER, BANK)
    - jobs_metrics: Compteurs pour system prompt
    - presence: Tracking présence (is_on_chat_page, current_active_thread)
    - thread_states: État par thread
    - active_tasks: Tâches actives par thread
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any, Tuple

logger = logging.getLogger("llm_service.session_state")


class SessionStateManager:
    """
    Gestionnaire d'état session externalisé dans Redis.
    
    Permet aux LLMSession de devenir stateless en stockant leur état
    dans Redis. Chaque instance du microservice peut ainsi reprendre
    une session créée par une autre instance.
    """
    
    # TTL par défaut: 2 heures
    DEFAULT_TTL = 7200
    
    # Préfixe pour les clés Redis
    KEY_PREFIX = "session"
    
    def __init__(self, redis_client=None):
        """
        Initialise le SessionStateManager.
        
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
        
        Format: session:{user_id}:{company_id}:state
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:state"
    
    def _serialize_datetime(self, dt: datetime) -> str:
        """Convertit un datetime en string ISO."""
        if isinstance(dt, datetime):
            return dt.isoformat()
        return str(dt)
    
    def _deserialize_datetime(self, dt_str: str) -> Optional[datetime]:
        """Convertit une string ISO en datetime."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None
    
    def _serialize_state(self, state: Dict[str, Any]) -> str:
        """
        Sérialise l'état de session en JSON.
        
        Gère les types spéciaux:
        - datetime → ISO string
        - set → list
        """
        def _serialize_value(v):
            if isinstance(v, datetime):
                return {"__type__": "datetime", "value": v.isoformat()}
            elif isinstance(v, set):
                return {"__type__": "set", "value": list(v)}
            elif isinstance(v, dict):
                return {k: _serialize_value(val) for k, val in v.items()}
            elif isinstance(v, list):
                return [_serialize_value(item) for item in v]
            return v
        
        serialized = {k: _serialize_value(v) for k, v in state.items()}
        return json.dumps(serialized, ensure_ascii=False, default=str)
    
    def _deserialize_state(self, json_str: str) -> Dict[str, Any]:
        """
        Désérialise l'état de session depuis JSON.
        
        Reconvertit les types spéciaux:
        - datetime ISO string → datetime
        - set list → set
        """
        def _deserialize_value(v):
            if isinstance(v, dict):
                if v.get("__type__") == "datetime":
                    return datetime.fromisoformat(v["value"])
                elif v.get("__type__") == "set":
                    return set(v["value"])
                else:
                    return {k: _deserialize_value(val) for k, val in v.items()}
            elif isinstance(v, list):
                return [_deserialize_value(item) for item in v]
            return v
        
        data = json.loads(json_str)
        return {k: _deserialize_value(v) for k, v in data.items()}
    
    # ═══════════════════════════════════════════════════════════════
    # OPÉRATIONS CRUD PRINCIPALES
    # ═══════════════════════════════════════════════════════════════
    
    def save_session_state(
        self,
        user_id: str,
        company_id: str,
        user_context: Optional[Dict] = None,
        jobs_data: Optional[Dict] = None,
        jobs_metrics: Optional[Dict] = None,
        is_on_chat_page: bool = False,
        current_active_thread: Optional[str] = None,
        thread_states: Optional[Dict[str, str]] = None,
        active_tasks: Optional[Dict[str, list]] = None,
        last_activity: Optional[Dict[str, datetime]] = None,
        thread_contexts: Optional[Dict[str, Tuple[Dict, float]]] = None,
        active_threads: Optional[list] = None,
        ttl: int = None
    ) -> bool:
        """
        Sauvegarde l'état complet d'une session dans Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société (collection_name)
            user_context: Métadonnées company
            jobs_data: Jobs par département
            jobs_metrics: Compteurs pour system prompt
            is_on_chat_page: Utilisateur sur la page chat?
            current_active_thread: Thread actuellement actif
            thread_states: État par thread
            active_tasks: Tâches actives par thread
            last_activity: Dernière activité par thread
            thread_contexts: Cache contexte LPT par thread
            active_threads: Liste des threads actifs
            ttl: TTL personnalisé (défaut: 2h)
            
        Returns:
            True si sauvegarde réussie
        """
        try:
            key = self._build_key(user_id, company_id)
            
            # Convertir last_activity (Dict[str, datetime]) en Dict[str, str]
            last_activity_serialized = {}
            if last_activity:
                for thread_key, dt in last_activity.items():
                    last_activity_serialized[thread_key] = self._serialize_datetime(dt)
            
            state = {
                "user_context": user_context or {},
                "jobs_data": jobs_data or {},
                "jobs_metrics": jobs_metrics or {},
                "is_on_chat_page": is_on_chat_page,
                "current_active_thread": current_active_thread,
                "thread_states": thread_states or {},
                "active_tasks": active_tasks or {},
                "last_activity": last_activity_serialized,
                "thread_contexts": thread_contexts or {},
                "active_threads": active_threads or [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "version": "1.0"
            }
            
            ttl_seconds = ttl or self.DEFAULT_TTL
            serialized = self._serialize_state(state)
            
            self.redis.setex(key, ttl_seconds, serialized)
            
            logger.debug(
                f"[SESSION_STATE] 💾 Session sauvegardée: {key} (TTL: {ttl_seconds}s)"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur sauvegarde: {e}", exc_info=True)
            return False
    
    def load_session_state(
        self,
        user_id: str,
        company_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Charge l'état d'une session depuis Redis.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            
        Returns:
            Dict avec l'état de session, ou None si non trouvé
        """
        try:
            key = self._build_key(user_id, company_id)
            
            data = self.redis.get(key)
            
            if not data:
                logger.debug(f"[SESSION_STATE] ❌ Session non trouvée: {key}")
                return None
            
            # Décoder bytes si nécessaire
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            
            state = self._deserialize_state(data)
            
            # Reconvertir last_activity en Dict[str, datetime]
            if "last_activity" in state:
                last_activity_raw = state["last_activity"]
                state["last_activity"] = {}
                for thread_key, dt_str in last_activity_raw.items():
                    dt = self._deserialize_datetime(dt_str)
                    if dt:
                        state["last_activity"][thread_key] = dt
            
            logger.debug(f"[SESSION_STATE] ✅ Session chargée: {key}")
            
            return state
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur chargement: {e}", exc_info=True)
            return None
    
    def update_session_state(
        self,
        user_id: str,
        company_id: str,
        updates: Dict[str, Any],
        extend_ttl: bool = True
    ) -> bool:
        """
        Met à jour partiellement l'état d'une session.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            updates: Dict des champs à mettre à jour
            extend_ttl: Prolonger le TTL à chaque update?
            
        Returns:
            True si mise à jour réussie
        """
        try:
            # Charger l'état existant
            state = self.load_session_state(user_id, company_id)
            
            if state is None:
                logger.warning(
                    f"[SESSION_STATE] ⚠️ Session inexistante pour update: "
                    f"{user_id}:{company_id}"
                )
                return False
            
            # Appliquer les mises à jour
            state.update(updates)
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            # Sauvegarder
            key = self._build_key(user_id, company_id)
            serialized = self._serialize_state(state)
            
            if extend_ttl:
                self.redis.setex(key, self.DEFAULT_TTL, serialized)
            else:
                self.redis.set(key, serialized)
            
            logger.debug(
                f"[SESSION_STATE] 🔄 Session mise à jour: {key} "
                f"(champs: {list(updates.keys())})"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur update: {e}", exc_info=True)
            return False
    
    def delete_session_state(
        self,
        user_id: str,
        company_id: str
    ) -> bool:
        """
        Supprime l'état d'une session.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            
        Returns:
            True si suppression réussie
        """
        try:
            key = self._build_key(user_id, company_id)
            deleted = self.redis.delete(key)
            
            if deleted:
                logger.info(f"[SESSION_STATE] 🗑️ Session supprimée: {key}")
            else:
                logger.debug(f"[SESSION_STATE] Session déjà absente: {key}")
            
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur suppression: {e}", exc_info=True)
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # OPÉRATIONS SPÉCIFIQUES
    # ═══════════════════════════════════════════════════════════════
    
    def update_presence(
        self,
        user_id: str,
        company_id: str,
        is_on_chat_page: bool,
        current_active_thread: Optional[str] = None
    ) -> bool:
        """
        Met à jour uniquement les informations de présence.
        
        Utilisé par enter_chat(), leave_chat(), switch_thread().
        
        ⚠️ DEPRECATED: Utiliser update_presence_multi_tab() pour le support multi-onglet.
        Cette méthode est conservée pour rétrocompatibilité mais écrase current_active_thread
        pour tous les onglets.
        """
        updates = {
            "is_on_chat_page": is_on_chat_page,
            "current_active_thread": current_active_thread
        }
        
        return self.update_session_state(user_id, company_id, updates)
    
    # ═══════════════════════════════════════════════════════════════
    # MULTI-ONGLET PRESENCE (NOUVEAU)
    # ═══════════════════════════════════════════════════════════════
    
    def update_presence_multi_tab(
        self,
        user_id: str,
        company_id: str,
        session_id: str,
        thread_key: str,
        is_on_chat_page: bool = True
    ) -> bool:
        """
        Met à jour la présence pour un onglet spécifique (identifié par session_id).
        
        Permet à plusieurs onglets d'avoir différents threads ouverts simultanément.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            session_id: ID unique de l'onglet/connexion WebSocket
            thread_key: Thread sur lequel l'utilisateur entre
            is_on_chat_page: True si sur la page chat
            
        Returns:
            True si mise à jour réussie
        """
        try:
            state = self.load_session_state(user_id, company_id)
            
            if state is None:
                logger.warning(
                    f"[SESSION_STATE] ⚠️ Session inexistante pour update_presence_multi_tab: "
                    f"{user_id}:{company_id}"
                )
                return False
            
            # Récupérer ou initialiser le dictionnaire des threads actifs par session
            active_threads_by_session = state.get("active_threads_by_session", {})
            
            # Mettre à jour le thread pour cette session/onglet
            active_threads_by_session[session_id] = thread_key
            
            # Mettre à jour l'état
            updates = {
                "active_threads_by_session": active_threads_by_session,
                "is_on_chat_page": True,  # Au moins un onglet est sur le chat
                # Conserver current_active_thread pour rétrocompatibilité (dernier thread actif)
                "current_active_thread": thread_key
            }
            
            success = self.update_session_state(user_id, company_id, updates)
            
            if success:
                logger.info(
                    f"[SESSION_STATE] 📍 Multi-tab presence updated: "
                    f"user={user_id}, session={session_id[:8]}..., thread={thread_key}"
                )
            
            return success
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur update_presence_multi_tab: {e}", exc_info=True)
            return False
    
    def remove_tab_presence(
        self,
        user_id: str,
        company_id: str,
        session_id: str
    ) -> bool:
        """
        Supprime la présence d'un onglet spécifique (appelé lors de la déconnexion WebSocket).
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            session_id: ID unique de l'onglet qui se déconnecte
            
        Returns:
            True si mise à jour réussie
        """
        try:
            state = self.load_session_state(user_id, company_id)
            
            if state is None:
                return True  # Session déjà supprimée, c'est OK
            
            active_threads_by_session = state.get("active_threads_by_session", {})
            
            # Supprimer cette session du dictionnaire
            removed_thread = active_threads_by_session.pop(session_id, None)
            
            # Déterminer le nouvel état
            has_active_tabs = len(active_threads_by_session) > 0
            
            updates = {
                "active_threads_by_session": active_threads_by_session,
                "is_on_chat_page": has_active_tabs
            }
            
            # Si plus aucun onglet actif, effacer current_active_thread
            if not has_active_tabs:
                updates["current_active_thread"] = None
            
            success = self.update_session_state(user_id, company_id, updates)
            
            if success:
                logger.info(
                    f"[SESSION_STATE] 🚪 Tab presence removed: "
                    f"user={user_id}, session={session_id[:8]}..., "
                    f"removed_thread={removed_thread}, remaining_tabs={len(active_threads_by_session)}"
                )
            
            return success
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur remove_tab_presence: {e}", exc_info=True)
            return False
    
    def is_user_on_thread_multi_tab(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Vérifie si AU MOINS UN onglet a ce thread ouvert.
        
        À utiliser à la place de is_user_on_thread() pour le support multi-onglet.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Thread à vérifier
            
        Returns:
            True si au moins un onglet a ce thread ouvert
        """
        try:
            state = self.load_session_state(user_id, company_id)
            
            if not state:
                return False
            
            # Vérifier dans le nouveau dictionnaire multi-tab
            active_threads_by_session = state.get("active_threads_by_session", {})
            
            if active_threads_by_session:
                return thread_key in active_threads_by_session.values()
            
            # Fallback: utiliser l'ancien champ pour rétrocompatibilité
            is_on_chat = state.get("is_on_chat_page", False)
            current_thread = state.get("current_active_thread")
            
            return is_on_chat and current_thread == thread_key
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur is_user_on_thread_multi_tab: {e}")
            return False
    
    def get_active_tabs_for_thread(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> list:
        """
        Retourne la liste des session_ids qui ont ce thread ouvert.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Thread à vérifier
            
        Returns:
            Liste des session_ids avec ce thread ouvert
        """
        try:
            state = self.load_session_state(user_id, company_id)
            
            if not state:
                return []
            
            active_threads_by_session = state.get("active_threads_by_session", {})
            
            return [
                session_id 
                for session_id, thread in active_threads_by_session.items() 
                if thread == thread_key
            ]
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur get_active_tabs_for_thread: {e}")
            return []
    
    def get_all_active_tabs(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, str]:
        """
        Retourne tous les onglets actifs et leurs threads.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            
        Returns:
            Dict {session_id: thread_key}
        """
        try:
            state = self.load_session_state(user_id, company_id)
            
            if not state:
                return {}
            
            return state.get("active_threads_by_session", {})
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur get_all_active_tabs: {e}")
            return {}
    
    def update_thread_activity(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Met à jour la dernière activité pour un thread.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Thread sur lequel il y a eu activité
        """
        state = self.load_session_state(user_id, company_id)
        
        if state is None:
            return False
        
        last_activity = state.get("last_activity", {})
        last_activity[thread_key] = datetime.now(timezone.utc)
        
        return self.update_session_state(
            user_id, 
            company_id, 
            {"last_activity": last_activity}
        )
    
    def update_jobs_data(
        self,
        user_id: str,
        company_id: str,
        jobs_data: Dict,
        jobs_metrics: Dict
    ) -> bool:
        """
        Met à jour les données de jobs.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            jobs_data: Nouvelles données de jobs
            jobs_metrics: Nouvelles métriques
        """
        return self.update_session_state(
            user_id,
            company_id,
            {
                "jobs_data": jobs_data,
                "jobs_metrics": jobs_metrics
            }
        )
    
    def get_user_context(
        self,
        user_id: str,
        company_id: str
    ) -> Optional[Dict]:
        """
        Récupère uniquement le contexte utilisateur.
        
        Optimisation: Évite de charger tout l'état si seul user_context est nécessaire.
        """
        state = self.load_session_state(user_id, company_id)
        
        if state:
            return state.get("user_context")
        return None
    
    def get_jobs_data(
        self,
        user_id: str,
        company_id: str
    ) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        Récupère uniquement les données de jobs.
        
        Returns:
            Tuple (jobs_data, jobs_metrics)
        """
        state = self.load_session_state(user_id, company_id)
        
        if state:
            return state.get("jobs_data"), state.get("jobs_metrics")
        return None, None
    
    def is_user_on_thread(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> bool:
        """
        Vérifie si l'utilisateur est actuellement sur un thread spécifique.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            thread_key: Thread à vérifier
            
        Returns:
            True si l'utilisateur est sur la page chat ET sur ce thread
        """
        state = self.load_session_state(user_id, company_id)
        
        if not state:
            return False
        
        is_on_chat = state.get("is_on_chat_page", False)
        current_thread = state.get("current_active_thread")
        
        return is_on_chat and current_thread == thread_key
    
    def session_exists(
        self,
        user_id: str,
        company_id: str
    ) -> bool:
        """
        Vérifie si une session existe dans Redis.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            
        Returns:
            True si la session existe
        """
        key = self._build_key(user_id, company_id)
        return bool(self.redis.exists(key))
    
    def extend_ttl(
        self,
        user_id: str,
        company_id: str,
        ttl: int = None
    ) -> bool:
        """
        Prolonge le TTL d'une session.
        
        Args:
            user_id: ID de l'utilisateur
            company_id: ID de la société
            ttl: Nouveau TTL en secondes (défaut: 2h)
            
        Returns:
            True si TTL prolongé
        """
        try:
            key = self._build_key(user_id, company_id)
            ttl_seconds = ttl or self.DEFAULT_TTL
            
            result = self.redis.expire(key, ttl_seconds)
            
            if result:
                logger.debug(f"[SESSION_STATE] ⏰ TTL prolongé: {key} ({ttl_seconds}s)")
            
            return bool(result)
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur extend_ttl: {e}", exc_info=True)
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # UTILITAIRES
    # ═══════════════════════════════════════════════════════════════
    
    def list_user_sessions(self, user_id: str) -> list:
        """
        Liste toutes les sessions d'un utilisateur.
        
        Args:
            user_id: ID de l'utilisateur
            
        Returns:
            Liste des company_id pour lesquels une session existe
        """
        try:
            pattern = f"{self.KEY_PREFIX}:{user_id}:*:state"
            keys = list(self.redis.scan_iter(match=pattern))
            
            company_ids = []
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                # session:{user_id}:{company_id}:state
                parts = key.split(":")
                if len(parts) >= 3:
                    company_ids.append(parts[2])
            
            return company_ids
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur list_user_sessions: {e}")
            return []
    
    def cleanup_expired_sessions(self) -> int:
        """
        Nettoie les sessions expirées (normalement géré par TTL Redis).
        
        Cette méthode est pour le cas où on veut forcer un nettoyage.
        
        Returns:
            Nombre de sessions nettoyées
        """
        # Redis gère automatiquement les TTL, cette méthode est là
        # pour un éventuel nettoyage forcé futur
        logger.info("[SESSION_STATE] 🧹 Redis gère automatiquement les TTL")
        return 0
    
    def get_session_stats(self) -> Dict[str, Any]:
        """
        Retourne des statistiques sur les sessions Redis.
        
        Returns:
            Dict avec statistiques (count, memory, etc.)
        """
        try:
            pattern = f"{self.KEY_PREFIX}:*:state"
            
            count = 0
            total_size = 0
            
            for key in self.redis.scan_iter(match=pattern):
                count += 1
                size = self.redis.strlen(key)
                total_size += size
            
            return {
                "total_sessions": count,
                "total_size_bytes": total_size,
                "avg_size_bytes": total_size // count if count > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"[SESSION_STATE] ❌ Erreur get_session_stats: {e}")
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_session_state_manager: Optional[SessionStateManager] = None


def get_session_state_manager() -> SessionStateManager:
    """
    Récupère l'instance singleton du SessionStateManager.
    """
    global _session_state_manager
    if _session_state_manager is None:
        _session_state_manager = SessionStateManager()
    return _session_state_manager

