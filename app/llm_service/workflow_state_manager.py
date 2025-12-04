"""
WorkflowStateManager - Gestionnaire d'état workflow externalisé dans Redis.

Ce module permet le basculement dynamique UI ↔ BACKEND pendant l'exécution
d'un workflow et la gestion des interactions utilisateur.

Architecture:
    - Clé Redis: workflow:{user_id}:{company_id}:{thread_key}:state
    - TTL: 1 heure (workflows actifs)
    - Format: JSON sérialisé avec métadonnées

Cas d'usage:
    1. Tâche planifiée démarre en BACKEND
    2. User entre → bascule UI, streaming activé
    3. User envoie message → workflow pause, conversation normale
    
    DISTINCTION TERMINATE vs LEAVE_CHAT:
    
    4a. User envoie "...TERMINATE" → workflow reprend avec pré-prompt
        - L'utilisateur RESTE sur le chat
        - Le workflow reprend en mode "UI" (streaming activé)
        - Le mode reste "UI" car l'utilisateur est présent
        
    4b. User quitte le chat → workflow reprend en BACKEND
        - L'utilisateur n'est PLUS sur le chat
        - Si le workflow était en pause, il reprend en mode "BACKEND"
        - Si le workflow n'était pas en pause (ex: après TERMINATE), il continue en "BACKEND"
        - Pas de streaming car l'utilisateur est absent

    5. Workflow attend LPT → état "waiting_lpt"
        - L'agent a appelé WAIT_ON_LPT
        - Le workflow se met en pause proprement
        - Reprend automatiquement au callback LPT
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger("pinnokio.workflow_state")


class WorkflowStateManager:
    """
    Gestionnaire d'état workflow externalisé dans Redis.
    
    Permet le basculement dynamique UI ↔ BACKEND et la gestion
    des interactions utilisateur pendant un workflow en cours.
    
    États possibles:
        - "running": Workflow en cours d'exécution
        - "paused": Workflow en pause (conversation utilisateur)
        - "waiting_lpt": Workflow en attente de callback LPT
        - "completed": Workflow terminé
    
    Modes possibles:
        - "UI": Utilisateur présent, streaming activé
        - "BACKEND": Utilisateur absent, pas de streaming
    
    Distinction TERMINATE vs leave_chat:
        - TERMINATE: L'utilisateur demande la reprise mais RESTE sur le chat
                     → Mode reste "UI", workflow reprend avec streaming
        - leave_chat: L'utilisateur QUITTE le chat
                     → Mode passe à "BACKEND", workflow reprend SANS streaming
    """
    
    KEY_PREFIX = "workflow"
    DEFAULT_TTL = 3600  # 1h
    COMPLETED_TTL = 300  # 5 min après fin (pour debug)
    
    def __init__(self, redis_client=None):
        """
        Initialise le WorkflowStateManager.
        
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
        Construit la clé Redis pour un état workflow.
        
        Format: workflow:{user_id}:{company_id}:{thread_key}:state
        """
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:{thread_key}:state"
    
    # ═══════════════════════════════════════════════════════════════
    # GESTION ÉTAT WORKFLOW
    # ═══════════════════════════════════════════════════════════════
    
    def start_workflow(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str,
        initial_mode: str = "BACKEND"
    ) -> bool:
        """
        Marque le début d'un workflow sur un thread.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société (collection_name)
            thread_key: Clé du thread de chat
            initial_mode: Mode initial ("UI" ou "BACKEND")
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        now = datetime.now(timezone.utc).isoformat()
        
        state = {
            "status": "running",
            "mode": initial_mode,
            "user_present": initial_mode == "UI",
            "paused_at": None,
            "pause_reason": None,
            "pending_user_message": None,
            "current_turn": 0,
            "started_at": now,
            "last_activity": now,
            # ⭐ Nouveau: Gestion attente LPT
            "waiting_lpt_info": None,
            "waiting_lpt_since": None
        }
        
        try:
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            logger.info(
                f"[WORKFLOW_STATE] 🚀 Workflow démarré: "
                f"thread={thread_key}, mode={initial_mode}"
            )
            return True
        except Exception as e:
            logger.error(f"[WORKFLOW_STATE] ❌ Erreur start_workflow: {e}")
            return False
    
    def end_workflow(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str,
        status: str = "completed"
    ) -> bool:
        """
        Marque la fin d'un workflow.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            status: Statut final ("completed", "error", "cancelled")
            
        Returns:
            True si succès
        """
        key = self._build_key(user_id, company_id, thread_key)
        
        try:
            state = self.get_workflow_state(user_id, company_id, thread_key)
            if state:
                state["status"] = status
                state["last_activity"] = datetime.now(timezone.utc).isoformat()
                state["ended_at"] = datetime.now(timezone.utc).isoformat()
                # Garder l'état un peu après fin (pour debug)
                self.redis.setex(key, self.COMPLETED_TTL, json.dumps(state))
                logger.info(
                    f"[WORKFLOW_STATE] ✅ Workflow terminé: "
                    f"thread={thread_key}, status={status}"
                )
            return True
        except Exception as e:
            logger.error(f"[WORKFLOW_STATE] ❌ Erreur end_workflow: {e}")
            return False
    
    def get_workflow_state(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère l'état actuel du workflow.
        
        Returns:
            Dict avec l'état ou None si pas de workflow
        """
        key = self._build_key(user_id, company_id, thread_key)
        try:
            data = self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"[WORKFLOW_STATE] ❌ Erreur get_workflow_state: {e}")
            return None
    
    def _save_state(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str, 
        state: Dict[str, Any]
    ) -> bool:
        """Sauvegarde l'état dans Redis."""
        key = self._build_key(user_id, company_id, thread_key)
        try:
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            return True
        except Exception as e:
            logger.error(f"[WORKFLOW_STATE] ❌ Erreur _save_state: {e}")
            return False
    
    def is_workflow_running(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> bool:
        """Vérifie si un workflow est en cours sur ce thread."""
        state = self.get_workflow_state(user_id, company_id, thread_key)
        return state is not None and state.get("status") in ("running", "paused")
    
    def is_workflow_paused(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> bool:
        """Vérifie si le workflow est en pause (conversation utilisateur)."""
        state = self.get_workflow_state(user_id, company_id, thread_key)
        return state is not None and state.get("status") == "paused"
    
    def is_waiting_for_lpt(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> bool:
        """Vérifie si le workflow est en attente d'un callback LPT."""
        state = self.get_workflow_state(user_id, company_id, thread_key)
        return state is not None and state.get("status") == "waiting_lpt"
    
    # ═══════════════════════════════════════════════════════════════
    # GESTION ATTENTE LPT
    # ═══════════════════════════════════════════════════════════════
    
    async def set_waiting_for_lpt(
        self, 
        thread_key: str,
        lpt_info: Dict[str, Any]
    ) -> bool:
        """
        Marque le workflow comme en attente d'un callback LPT.
        
        Appelé par l'outil WAIT_ON_LPT quand l'agent doit attendre
        le retour d'un LPT avant de pouvoir continuer.
        
        Args:
            thread_key: Clé du thread
            lpt_info: Informations sur le LPT attendu (voir WaitOnLPTTool)
            
        Returns:
            True si succès
        """
        # Extraire user_id et company_id depuis le thread_key ou lpt_info
        # Format thread_key: généralement {user_id}_{company_id}_{thread_id}
        # Ou utiliser les infos passées dans lpt_info
        
        try:
            # Chercher l'état existant
            pattern = f"{self.KEY_PREFIX}:*:*:{thread_key}:state"
            keys = self.redis.keys(pattern)
            
            if not keys:
                logger.warning(f"[WORKFLOW_STATE] ⚠️ Aucun workflow trouvé pour thread={thread_key}")
                return False
            
            # Prendre le premier (il ne devrait y en avoir qu'un)
            key = keys[0] if isinstance(keys[0], str) else keys[0].decode('utf-8')
            data = self.redis.get(key)
            
            if not data:
                return False
            
            state = json.loads(data)
            now = datetime.now(timezone.utc).isoformat()
            
            state["status"] = "waiting_lpt"
            state["waiting_lpt_info"] = lpt_info
            state["waiting_lpt_since"] = now
            state["last_activity"] = now
            
            self.redis.setex(key, self.DEFAULT_TTL, json.dumps(state))
            
            logger.info(
                f"[WORKFLOW_STATE] ⏳ Workflow en attente LPT: "
                f"thread={thread_key}, lpt={lpt_info.get('expected_lpt')}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[WORKFLOW_STATE] ❌ Erreur set_waiting_for_lpt: {e}")
            return False
    
    def clear_waiting_lpt(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> Dict[str, Any]:
        """
        Efface l'état d'attente LPT (appelé au callback).
        
        Returns:
            Dict avec les infos LPT qui étaient en attente
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        
        if not state:
            return {"cleared": False, "reason": "no_workflow_state"}
        
        lpt_info = state.get("waiting_lpt_info")
        
        if state.get("status") == "waiting_lpt":
            state["status"] = "running"
            state["waiting_lpt_info"] = None
            state["waiting_lpt_since"] = None
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            self._save_state(user_id, company_id, thread_key, state)
            
            logger.info(
                f"[WORKFLOW_STATE] ✅ Attente LPT effacée: thread={thread_key}"
            )
            
            return {
                "cleared": True,
                "lpt_info": lpt_info
            }
        
        return {"cleared": False, "reason": "not_waiting_lpt"}
    
    # ═══════════════════════════════════════════════════════════════
    # GESTION PRÉSENCE UTILISATEUR
    # ═══════════════════════════════════════════════════════════════
    
    def user_entered(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> Dict[str, Any]:
        """
        Signale que l'utilisateur est entré sur le thread.
        Bascule en mode UI si workflow actif.
        
        Returns:
            Dict avec:
                - changed: bool - si bascule de mode effectuée
                - previous_mode: str - mode avant bascule
                - new_mode: str - nouveau mode
                - workflow_paused: bool - si workflow était en pause
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        
        if not state or state.get("status") not in ("running", "paused"):
            return {"changed": False, "reason": "no_active_workflow"}
        
        previous_mode = state.get("mode")
        was_paused = state.get("status") == "paused"
        
        state["user_present"] = True
        state["mode"] = "UI"
        state["last_activity"] = datetime.now(timezone.utc).isoformat()
        
        self._save_state(user_id, company_id, thread_key, state)
        
        changed = previous_mode != "UI"
        logger.info(
            f"[WORKFLOW_STATE] 👤 User entré: thread={thread_key}, "
            f"bascule={changed}, was_paused={was_paused}"
        )
        
        return {
            "changed": changed,
            "previous_mode": previous_mode,
            "new_mode": "UI",
            "workflow_paused": was_paused,
            "workflow_active": True
        }
    
    def user_left(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> Dict[str, Any]:
        """
        Signale que l'utilisateur a quitté le thread.
        Bascule en mode BACKEND et reprend workflow si pausé OU s'il était actif.
        
        DISTINCTION avec TERMINATE:
        - TERMINATE: L'utilisateur RESTE, le workflow reprend en mode UI
        - leave_chat: L'utilisateur PART, le workflow reprend en mode BACKEND
        
        Comportement:
        - Si workflow "paused": reprendre en BACKEND
        - Si workflow "running": continuer en BACKEND (désactiver streaming)
        - Si workflow "waiting_lpt": rester en attente mais passer en BACKEND
        
        Returns:
            Dict avec:
                - changed: bool - si bascule de mode effectuée
                - needs_resume: bool - si workflow doit reprendre (était pausé)
                - was_paused: bool - si workflow était en pause
                - was_running: bool - si workflow était actif
                - resume_reason: str - raison de la reprise ("user_left")
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        
        if not state:
            return {"changed": False, "reason": "no_workflow_state"}
        
        previous_status = state.get("status")
        was_paused = previous_status == "paused"
        was_running = previous_status == "running"
        was_waiting_lpt = previous_status == "waiting_lpt"
        previous_mode = state.get("mode")
        
        # ⭐ IMPORTANT: L'utilisateur quitte → mode BACKEND
        state["user_present"] = False
        state["mode"] = "BACKEND"
        state["last_activity"] = datetime.now(timezone.utc).isoformat()
        
        # Déterminer si le workflow doit reprendre
        needs_resume = False
        resume_reason = None
        
        if was_paused:
            # ═══ CAS: Workflow était en pause (conversation utilisateur) ═══
            # → Le workflow DOIT reprendre car l'utilisateur est parti
            state["status"] = "running"
            state["paused_at"] = None
            state["pause_reason"] = "user_left"  # Gardé pour le pré-prompt de reprise
            needs_resume = True
            resume_reason = "user_left"
            
            logger.info(
                f"[WORKFLOW_STATE] 👋 User parti (workflow pausé → reprend): "
                f"thread={thread_key}"
            )
            
        elif was_running:
            # ═══ CAS: Workflow était actif (ex: après TERMINATE) ═══
            # → Le workflow continue mais passe en BACKEND (pas de streaming)
            # → Pas besoin de "reprendre", juste changer le mode
            needs_resume = False
            resume_reason = None
            
            logger.info(
                f"[WORKFLOW_STATE] 👋 User parti (workflow actif → BACKEND): "
                f"thread={thread_key}"
            )
            
        elif was_waiting_lpt:
            # ═══ CAS: Workflow attend un LPT ═══
            # → Le workflow reste en attente mais passe en BACKEND
            # → Quand le callback arrive, il reprendra en mode BACKEND
            needs_resume = False
            resume_reason = None
            
            logger.info(
                f"[WORKFLOW_STATE] 👋 User parti (workflow attend LPT → BACKEND): "
                f"thread={thread_key}"
            )
        
        self._save_state(user_id, company_id, thread_key, state)
        
        return {
            "changed": previous_mode != "BACKEND",
            "needs_resume": needs_resume,
            "was_paused": was_paused,
            "was_running": was_running,
            "was_waiting_lpt": was_waiting_lpt,
            "resume_reason": resume_reason,
            "new_mode": "BACKEND"
        }
    
    # ═══════════════════════════════════════════════════════════════
    # GESTION MESSAGES UTILISATEUR PENDANT WORKFLOW
    # ═══════════════════════════════════════════════════════════════
    
    def queue_user_message(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Ajoute un message utilisateur et gère la pause/reprise du workflow.
        
        DISTINCTION IMPORTANTE:
        - Si message termine par "TERMINATE": reprise du workflow EN MODE UI
          → L'utilisateur RESTE sur le chat, le workflow reprend avec streaming
        - Sinon: pause du workflow, conversation normale
          → Le workflow ne reprendra que si l'utilisateur quitte OU envoie TERMINATE
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            message: Message de l'utilisateur
            
        Returns:
            Dict avec:
                - queued: bool - si message a été traité
                - is_terminate: bool - si c'est une demande de reprise
                - action: str - "resume_workflow_ui" ou "pause_workflow"
                - clean_message: str - message nettoyé (si TERMINATE)
                - mode: str - mode après traitement ("UI" ou inchangé)
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        
        if not state or state.get("status") not in ("running", "paused", "waiting_lpt"):
            return {"queued": False, "reason": "no_active_workflow"}
        
        # Vérifier si c'est une demande TERMINATE
        is_terminate = message.strip().upper().endswith("TERMINATE")
        
        if is_terminate:
            # ═══ CAS TERMINATE ═══
            # L'utilisateur demande la reprise MAIS RESTE sur le chat
            # → Le workflow reprend EN MODE UI (avec streaming)
            
            # Extraire le message sans TERMINATE
            clean_message = message.strip()
            if clean_message.upper().endswith("TERMINATE"):
                clean_message = clean_message[:-9].strip()  # Retirer "TERMINATE"
            
            state["pending_user_message"] = clean_message if clean_message else None
            state["pause_reason"] = "terminate_request"
            state["status"] = "running"  # Reprendre
            state["paused_at"] = None
            # ⭐ IMPORTANT: Le mode RESTE "UI" car l'utilisateur est présent
            state["mode"] = "UI"
            state["user_present"] = True
            
            # Effacer l'état d'attente LPT si présent
            if state.get("waiting_lpt_info"):
                state["waiting_lpt_info"] = None
                state["waiting_lpt_since"] = None
            
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            logger.info(
                f"[WORKFLOW_STATE] 🔄 TERMINATE reçu (mode UI): thread={thread_key}, "
                f"clean_message='{clean_message[:50] if clean_message else ''}...'"
            )
            
            self._save_state(user_id, company_id, thread_key, state)
            
            return {
                "queued": True,
                "is_terminate": True,
                "clean_message": clean_message,
                "action": "resume_workflow_ui",  # ⭐ Action spécifique: reprise EN mode UI
                "mode": "UI"
            }
        else:
            # ═══ CAS MESSAGE NORMAL ═══
            # L'utilisateur envoie un message → pause workflow, conversation normale
            # Le mode reste "UI" car l'utilisateur est présent
            
            state["pending_user_message"] = message
            state["status"] = "paused"
            state["paused_at"] = datetime.now(timezone.utc).isoformat()
            state["pause_reason"] = "user_message"
            # Le mode reste inchangé (devrait être "UI" si user est présent)
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            
            logger.info(
                f"[WORKFLOW_STATE] ⏸️ Workflow pausé: thread={thread_key}"
            )
            
            self._save_state(user_id, company_id, thread_key, state)
            
            return {
                "queued": True,
                "is_terminate": False,
                "action": "pause_workflow",
                "mode": state.get("mode", "UI")
            }
    
    def get_pending_message(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str,
        clear: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère le message utilisateur en attente et la raison de pause.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            thread_key: Clé du thread
            clear: Si True, efface le message après lecture
            
        Returns:
            Dict avec:
                - message: str - message en attente
                - reason: str - raison de pause
                - is_terminate: bool - si c'était un TERMINATE
                - is_user_left: bool - si user a quitté
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        
        if not state:
            return None
        
        message = state.get("pending_user_message")
        reason = state.get("pause_reason")
        
        if not message and not reason:
            return None
        
        result = {
            "message": message,
            "reason": reason,
            "is_terminate": reason == "terminate_request",
            "is_user_left": reason == "user_left"
        }
        
        if clear:
            state["pending_user_message"] = None
            state["pause_reason"] = None
            self._save_state(user_id, company_id, thread_key, state)
        
        return result
    
    def update_turn(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str, 
        turn: int
    ) -> bool:
        """
        Met à jour le numéro de tour actuel.
        
        Args:
            turn: Numéro du tour actuel
            
        Returns:
            True si succès
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        
        if state:
            state["current_turn"] = turn
            state["last_activity"] = datetime.now(timezone.utc).isoformat()
            return self._save_state(user_id, company_id, thread_key, state)
        return False
    
    def get_current_mode(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str
    ) -> str:
        """
        Récupère le mode actuel du workflow.
        
        Returns:
            "UI", "BACKEND", ou "NONE" si pas de workflow
        """
        state = self.get_workflow_state(user_id, company_id, thread_key)
        if state and state.get("status") in ("running", "paused"):
            return state.get("mode", "BACKEND")
        return "NONE"


# ═══════════════════════════════════════════════════════════════
# SINGLETON POUR ACCÈS GLOBAL
# ═══════════════════════════════════════════════════════════════

_workflow_state_manager: Optional[WorkflowStateManager] = None


def get_workflow_state_manager() -> WorkflowStateManager:
    """
    Retourne l'instance singleton du WorkflowStateManager.
    
    Returns:
        Instance de WorkflowStateManager
    """
    global _workflow_state_manager
    if _workflow_state_manager is None:
        _workflow_state_manager = WorkflowStateManager()
    return _workflow_state_manager

