"""
Gestionnaire LLM centralisé utilisant Firebase Realtime Database.
Gère les sessions LLM et l'intégration avec BaseAIAgent.
"""

import asyncio
import json
import uuid
import time
import logging
import threading
import copy
from typing import Dict, Optional, Any, Tuple, List, Awaitable, Callable, Set
from datetime import datetime, timezone, timedelta
from ..llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize
from .llm_context import LLMContext
from .rtdb_message_formatter import RTDBMessageFormatter
from .session_state_manager import SessionStateManager, get_session_state_manager

logger = logging.getLogger("llm_service.manager")


# ═══════════════════════════════════════════════════════════════
# BUILDER DE CARTES INTERACTIVES POUR REFLEX
# ═══════════════════════════════════════════════════════════════

class ApprovalCardBuilder:
    """
    Constructeur de cartes interactives compatibles Reflex.

    Format standardisé avec Google Chat Card API.
    Extensible pour nouveaux types de cartes.

    ✅ MODIFICATIONS RÉCENTES :
    - Ajout du champ 'execution_mode' explicite dans cardsV2, message.cardParams et racine
    - Valeurs possibles : 'ON_DEMAND', 'SCHEDULED', 'ONE_TIME', 'NOW'
    - Maintien de la compatibilité ascendante (champs existants préservés)
    """
    
    @staticmethod
    def build_approval_card(
        card_id: str,
        title: str,
        subtitle: str = "",
        text: str = "",
        input_label: str = "Votre commentaire (optionnel)",
        button_text: str = "Approuver",
        button_action: str = "approve_four_eyes",
        additional_params: Dict[str, Any] = None,
        execution_mode: str = None
    ) -> Dict[str, Any]:
        """
        Construit une carte d'approbation standard.

        Args:
            card_id: Identifiant unique de la carte (ex: 'approval_card')
            title: Titre principal
            subtitle: Sous-titre (optionnel)
            text: Description détaillée
            input_label: Label du champ de saisie
            button_text: Texte du bouton principal
            button_action: Action du bouton (ex: 'approve_four_eyes')
            additional_params: Paramètres additionnels pour extension
            execution_mode: Mode d'exécution explicite ('ON_DEMAND', 'SCHEDULED', 'ONE_TIME', 'NOW')

        Returns:
            Dict compatible Reflex (cardsV2 + message.cardParams)
        """
        base_params = {
            "cardId": card_id,
            "title": title,
            "subtitle": subtitle,
            "text": text,
            "input_label": input_label,
            "button_text": button_text,
            "button_action": button_action
        }

        # ✅ Ajouter le mode d'exécution explicite si fourni
        if execution_mode:
            base_params["execution_mode"] = execution_mode

        if additional_params:
            base_params.update(additional_params)
        
        # ✅ Construire le contenu de la carte
        card_content = {
            "cardsV2": [{
                "cardId": card_id,
                "card": {
                    "header": {
                        "title": title,
                        "subtitle": subtitle
                    },
                    "sections": [{
                        "widgets": [{
                            "textParagraph": {
                                "text": text
                            }
                        }]
                    }]
                }
            }],
            "message": {
                "cardType": card_id,
                "cardParams": base_params
            }
        }

        # ✅ Ajouter execution_mode dans cardsV2 aussi pour cohérence
        if execution_mode:
            card_content["cardsV2"][0]["execution_mode"] = execution_mode
            card_content["execution_mode"] = execution_mode

        return card_content
    
    @staticmethod
    def build_text_modification_card(
        context_type: str,
        original_text: str,
        operations_log: List[Dict],
        final_text: str,
        warnings: List[str] = None
    ) -> Dict[str, Any]:
        """
        Carte d'approbation pour modifications de contexte texte.
        
        Affiche :
        - Type de contexte modifié
        - Résumé des opérations (add/replace/delete)
        - Comparaison avant/après (preview)
        - Warnings éventuels
        
        Args:
            context_type: Type de contexte ("router", "accounting", "company")
            original_text: Texte original avant modification
            operations_log: Liste des opérations effectuées
            final_text: Texte final après modifications
            warnings: Liste d'avertissements (optionnel)
        
        Returns:
            Format carte compatible Reflex
        """
        
        # Générer résumé des opérations
        operations_summary = []
        for i, op in enumerate(operations_log):
            op_args = op.get("args_from_llm", {})
            op_type = op_args.get("operation", "unknown")
            section = op_args.get("section_type", "unknown")
            
            if op_type == "add":
                icon = "➕"
            elif op_type == "replace":
                icon = "🔄"
            elif op_type == "delete":
                icon = "❌"
            else:
                icon = "•"
            
            operations_summary.append({
                "index": i + 1,
                "icon": icon,
                "operation": op_type.upper(),
                "section": section.upper(),
                "success": op.get("success", False)
            })
        
        # Calculer diff (simplifiée)
        diff_preview = {
            "added_chars": len(final_text) - len(original_text),
            "total_operations": len(operations_log),
            "successful_operations": sum(1 for op in operations_log if op.get("success"))
        }
        
        # Construire sections de la carte
        sections = [
            {
                "header": "📋 Résumé des modifications",
                "widgets": [
                    {
                        "decoratedText": {
                            "topLabel": "Type de contexte",
                            "text": context_type.upper()
                        }
                    },
                    {
                        "decoratedText": {
                            "topLabel": "Changement de taille",
                            "text": f"{diff_preview['added_chars']:+d} caractères"
                        }
                    },
                    {
                        "decoratedText": {
                            "topLabel": "Opérations",
                            "text": f"{diff_preview['successful_operations']}/{diff_preview['total_operations']} réussies"
                        }
                    }
                ]
            },
            {
                "header": "🔧 Opérations proposées",
                "collapsible": True,
                "widgets": [{
                    "textParagraph": {
                        "text": "\n".join([
                            f"{op['icon']} **Op {op['index']}**: {op['operation']} ({op['section']}) {'✅' if op['success'] else '❌'}"
                            for op in operations_summary
                        ])
                    }
                }]
            },
            {
                "header": "👁️ Aperçu",
                "collapsible": True,
                "widgets": [
                    {
                        "textParagraph": {
                            "text": f"**Avant** ({len(original_text)} caractères):\n```\n{original_text[:300]}{'...' if len(original_text) > 300 else ''}\n```"
                        }
                    },
                    {
                        "textParagraph": {
                            "text": f"**Après** ({len(final_text)} caractères):\n```\n{final_text[:300]}{'...' if len(final_text) > 300 else ''}\n```"
                        }
                    }
                ]
            }
        ]
        
        # Ajouter section warnings si présents
        if warnings:
            sections.append({
                "header": "⚠️ Avertissements",
                "widgets": [{
                    "textParagraph": {
                        "text": "\n".join([f"• {w}" for w in warnings])
                    }
                }]
            })
        
        # Construire carte complète
        return {
            "cardsV2": [{
                "cardId": "text_modification_approval",
                "card": {
                    "header": {
                        "title": f"📝 Modification contexte {context_type.upper()}",
                        "subtitle": f"{diff_preview['successful_operations']}/{diff_preview['total_operations']} opérations réussies"
                    },
                    "sections": sections
                }
            }],
            "message": {
                "cardType": "text_modification_approval",
                "cardParams": {
                    "cardId": "text_modification_approval",
                    "context_type": context_type,
                    "original_text": original_text,
                    "final_text": final_text,
                    "operations_summary": operations_summary,
                    "diff_preview": diff_preview,
                    "warnings": warnings or [],
                    "input_label": "Commentaire sur la modification (optionnel)",
                    "button_text": "Approuver la modification"
                }
            }
        }


class StreamingController:
    """Contrôleur pour gérer les arrêts de streaming via WebSocket."""
    
    def __init__(self):
        self.active_streams: Dict[str, Dict[str, Any]] = {}  # {session_key: {thread_key: task_info}}
        self._lock = threading.Lock()
    
    async def register_stream(self, session_key: str, thread_key: str, task: asyncio.Task):
        """Enregistre un stream actif."""
        with self._lock:
            if session_key not in self.active_streams:
                self.active_streams[session_key] = {}
            
            self.active_streams[session_key][thread_key] = {
                "task": task,
                "started_at": datetime.now(timezone.utc),
                "status": "streaming"
            }
            logger.info(f"Stream enregistré: {session_key}:{thread_key}")
    
    async def stop_stream(self, session_key: str, thread_key: str) -> bool:
        """
        Arrête un stream spécifique.
        
        Returns:
            True si le stream a été arrêté ou était déjà terminé, False si introuvable.
        """
        with self._lock:
            if session_key not in self.active_streams:
                return False
            
            if thread_key not in self.active_streams[session_key]:
                return False
            
            stream_info = self.active_streams[session_key][thread_key]
            
            # Arrêter la tâche si elle n'est pas déjà terminée
            if not stream_info["task"].done():
                stream_info["task"].cancel()
                logger.info(f"Stream arrêté: {session_key}:{thread_key}")
            else:
                logger.info(f"Stream déjà terminé: {session_key}:{thread_key}")
            
            # Marquer comme interrompu et désenregistrer immédiatement
            stream_info["status"] = "interrupted"
            stream_info["interrupted_at"] = datetime.now(timezone.utc)
            
            # Désenregistrer immédiatement si interrompu manuellement
            del self.active_streams[session_key][thread_key]
            if not self.active_streams[session_key]:
                del self.active_streams[session_key]
            
            return True
    
    async def stop_all_streams(self, session_key: str) -> int:
        """Arrête tous les streams d'une session."""
        with self._lock:
            if session_key not in self.active_streams:
                return 0
            
            stopped_count = 0
            for thread_key, stream_info in self.active_streams[session_key].items():
                if not stream_info["task"].done():
                    stream_info["task"].cancel()
                    stream_info["status"] = "interrupted"
                    stream_info["interrupted_at"] = datetime.now(timezone.utc)
                    stopped_count += 1
            
            logger.info(f"Tous les streams arrêtés pour {session_key}: {stopped_count}")
            return stopped_count
    
    async def unregister_stream(self, session_key: str, thread_key: str, delay_seconds: float = 10.0):
        """
        Désenregistre un stream terminé après un délai (fenêtre de grâce pour stop).
        
        Args:
            session_key: Clé de session
            thread_key: Clé du thread
            delay_seconds: Délai avant désenregistrement (défaut: 10s pour permettre stop utilisateur)
        """
        # Note: asyncio est déjà importé au niveau du module
        # Attendre le délai avant de désenregistrer (fenêtre de grâce)
        await asyncio.sleep(delay_seconds)
        
        with self._lock:
            # Vérifier que le stream existe toujours (peut avoir été désenregistré manuellement)
            if session_key in self.active_streams and thread_key in self.active_streams[session_key]:
                stream_info = self.active_streams[session_key][thread_key]
                
                # Ne désenregistrer que si le stream est terminé et n'a pas été interrompu
                if stream_info.get("status") != "interrupted" and stream_info["task"].done():
                    del self.active_streams[session_key][thread_key]
                    if not self.active_streams[session_key]:
                        del self.active_streams[session_key]
                    logger.info(f"Stream désenregistré (auto): {session_key}:{thread_key}")
                elif stream_info.get("status") == "interrupted":
                    # Stream déjà interrompu, désenregistrer immédiatement
                    del self.active_streams[session_key][thread_key]
                    if not self.active_streams[session_key]:
                        del self.active_streams[session_key]
                    logger.info(f"Stream désenregistré (interrompu): {session_key}:{thread_key}")
    
    async def get_active_streams(self, session_key: str) -> Dict[str, Any]:
        """Retourne les streams actifs d'une session."""
        with self._lock:
            return self.active_streams.get(session_key, {}).copy()


class LLMSession:
    """Session LLM isolée pour un utilisateur/société.
    
    Gère l'agent BaseAIAgent et l'historique des conversations pour tous les threads
    de cet utilisateur dans cette société.
    
    ⭐ ARCHITECTURE STATELESS (Multi-Instance Ready):
    L'état de la session est externalisé dans Redis via SessionStateManager.
    Cela permet le scaling horizontal : n'importe quelle instance peut reprendre
    une session créée par une autre instance.
    
    État externalisé dans Redis (session:{user_id}:{company_id}:state):
    - user_context, jobs_data, jobs_metrics
    - is_on_chat_page, current_active_thread
    - thread_states, active_tasks, intermediation_mode
    - last_activity, thread_contexts
    
    État local (non externalisé - recréé à la demande):
    - active_brains (recréés dynamiquement, chat history via ChatHistoryManager)
    - _callback_loop/_callback_thread (infrastructure locale)
    - onboarding_listeners (listeners locaux)
    """
    
    def __init__(self, session_key: str, context: LLMContext):
        self.session_key = session_key  # user_id:collection_name
        self.context = context
        
        # Lock pour cette session spécifique (pas de conflit entre utilisateurs)
        self._lock = threading.Lock()
        
        # ⭐ GESTIONNAIRE D'ÉTAT REDIS (scaling horizontal)
        self._state_manager: SessionStateManager = get_session_state_manager()
        self._state_loaded_from_redis: bool = False
        
        # ═══════════════════════════════════════════════════════════════
        # ÉTAT HYBRIDE: Copie locale (cache) + Persistance Redis
        # - Lecture: D'abord Redis, puis cache local
        # - Écriture: D'abord local, puis Redis (async)
        # ═══════════════════════════════════════════════════════════════
        
        # ⭐ DONNÉES PERMANENTES (externalisées dans Redis)
        self.user_context: Optional[Dict] = None  # Métadonnées company (mandate_path, client_uuid, etc.)
        self.jobs_data: Optional[Dict] = None     # Jobs APBookkeeper, Router, Bank
        self.jobs_metrics: Optional[Dict] = None  # Métriques pour system prompt
        
        # ⭐ BRAINS ACTIFS: 1 brain par thread/chat (PAS externalisé - recréé à la demande)
        # Note: Le chat_history sera externalisé via ChatHistoryManager (Phase 2)
        self.active_brains: Dict[str, Any] = {}  # {thread_key: PinnokioBrain}
        self._brain_locks: Dict[str, asyncio.Lock] = {}  # {thread_key: Lock}
        
        # ⭐ ÉTAT PAR THREAD (externalisé dans Redis)
        self.active_tasks: Dict[str, list] = {}
        self.thread_states: Dict[str, str] = {}
        
        # ⭐ Cache contexte LPT par thread (externalisé dans Redis)
        self.thread_contexts: Dict[str, Tuple[Dict[str, Any], float]] = {}  # {thread_key: (context, timestamp)}
        self.context_cache_ttl = 300  # 5 minutes
        
        # ⭐ TRACKING PRÉSENCE UTILISATEUR (externalisé dans Redis)
        self.is_on_chat_page: bool = False  # Est-il actuellement sur la PAGE de chat?
        self.current_active_thread: Optional[str] = None  # Sur QUEL thread précis?
        
        # Métriques (partiellement externalisé)
        self.created_at = datetime.now(timezone.utc)
        self.last_activity: Dict[str, datetime] = {}
        self.response_times: Dict[str, list] = {}

        # ⭐ LISTENERS ONBOARDING (locaux - non externalisés)
        self.onboarding_listeners: Dict[str, Dict[str, Any]] = {}
        self.onboarding_processed_ids: Dict[str, Set[str]] = {}
        
        # ⭐ MODE INTERMÉDIATION (externalisé dans Redis)
        self.intermediation_mode: Dict[str, bool] = {}  # {thread_key: True/False}

        # ⭐ Boucle asyncio dédiée pour les callbacks RTDB (local)
        self._callback_loop: Optional[asyncio.AbstractEventLoop] = None
        self._callback_thread: Optional[threading.Thread] = None
        self._callback_loop_lock = threading.Lock()
        
        # ═══════════════════════════════════════════════════════════════
        # TENTATIVE DE RÉCUPÉRATION D'ÉTAT EXISTANT (Multi-Instance)
        # ═══════════════════════════════════════════════════════════════
        self._try_restore_from_redis()
        
        logger.info(f"[SESSION_INIT] 📦 LLMSession créée: {session_key} (restored_from_redis={self._state_loaded_from_redis})")
    
    def _try_restore_from_redis(self):
        """
        Tente de restaurer l'état depuis Redis (pour reprise multi-instance).
        
        Appelé dans __init__ pour récupérer une session créée par une autre instance.
        """
        try:
            user_id = self.context.user_id
            company_id = self.context.collection_name
            
            state = self._state_manager.load_session_state(user_id, company_id)
            
            if state:
                # Restaurer l'état depuis Redis
                self.user_context = state.get("user_context")
                self.jobs_data = state.get("jobs_data")
                self.jobs_metrics = state.get("jobs_metrics")
                self.is_on_chat_page = state.get("is_on_chat_page", False)
                self.current_active_thread = state.get("current_active_thread")
                self.thread_states = state.get("thread_states", {})
                self.active_tasks = state.get("active_tasks", {})
                self.intermediation_mode = state.get("intermediation_mode", {})
                self.last_activity = state.get("last_activity", {})
                self.thread_contexts = state.get("thread_contexts", {})
                
                self._state_loaded_from_redis = True
                
                logger.info(
                    f"[SESSION_RESTORE] ✅ État restauré depuis Redis: {self.session_key} "
                    f"(company={self.user_context.get('company_name') if self.user_context else 'N/A'})"
                )
            else:
                logger.debug(f"[SESSION_RESTORE] Pas d'état existant dans Redis: {self.session_key}")
                
        except Exception as e:
            logger.warning(f"[SESSION_RESTORE] Erreur restauration Redis: {e}")
    
    def _sync_to_redis(self):
        """
        Synchronise l'état local vers Redis (non-bloquant).
        
        Appelé après chaque modification d'état pour assurer la durabilité.
        """
        try:
            self._state_manager.save_session_state(
                user_id=self.context.user_id,
                company_id=self.context.collection_name,
                user_context=self.user_context,
                jobs_data=self.jobs_data,
                jobs_metrics=self.jobs_metrics,
                is_on_chat_page=self.is_on_chat_page,
                current_active_thread=self.current_active_thread,
                thread_states=self.thread_states,
                active_tasks=self.active_tasks,
                intermediation_mode=self.intermediation_mode,
                last_activity=self.last_activity,
                thread_contexts=self.thread_contexts,
                active_threads=list(self.active_brains.keys())
            )
        except Exception as e:
            logger.warning(f"[SESSION_SYNC] ⚠️ Erreur sync Redis: {e}")
    
    def ensure_callback_loop(self) -> asyncio.AbstractEventLoop:
        """Garantit qu'une boucle asyncio dédiée à la session est disponible."""

        with self._callback_loop_lock:
            if (
                self._callback_loop
                and self._callback_thread
                and self._callback_thread.is_alive()
                and not self._callback_loop.is_closed()
            ):
                return self._callback_loop

            loop = asyncio.new_event_loop()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(
                target=_run_loop,
                name=f"LLMSessionLoop-{self.session_key}",
                daemon=True
            )
            thread.start()

            self._callback_loop = loop
            self._callback_thread = thread

            logger.info(
                f"[SESSION_LOOP] 🔄 Boucle callbacks initialisée pour session={self.session_key}"
            )

            return loop

    def schedule_coroutine(
        self,
        coro: Awaitable[Any],
        timeout: Optional[float] = 1.0
    ):
        """Planifie l'exécution d'une coroutine sur la boucle dédiée."""

        loop = self.ensure_callback_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)

        if timeout is not None:
            future.result(timeout=timeout)

        return future

    def stop_callback_loop(self) -> None:
        """Arrête proprement la boucle dédiée (utilisée lors du nettoyage de session)."""

        loop: Optional[asyncio.AbstractEventLoop]
        thread: Optional[threading.Thread]

        with self._callback_loop_lock:
            loop = self._callback_loop
            thread = self._callback_thread
            self._callback_loop = None
            self._callback_thread = None

        if not loop:
            return

        try:
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)

            if thread and thread.is_alive():
                thread.join(timeout=1.0)

            loop.close()

            logger.info(
                f"[SESSION_LOOP] 📴 Boucle callbacks arrêtée pour session={self.session_key}"
            )
        except Exception:
            logger.exception(
                f"[SESSION_LOOP] ❌ Erreur lors de l'arrêt de la boucle callbacks session={self.session_key}"
            )

    async def initialize_session_data(self, client_uuid: str):
        """
        Charge les données permanentes de la session (une seule fois).
        NE crée PAS de brain ici - les brains sont créés par chat dans load_chat_history().
        
        ⭐ NOUVELLE ARCHITECTURE :
        - Données permanentes chargées UNE fois (user_context, jobs_data, jobs_metrics)
        - Brains créés dynamiquement par thread/chat (1 brain = 1 chat)
        - Isolation complète entre chats
        
        Données chargées :
        1. user_context : Métadonnées company (mandate_path, client_uuid, dms_system, etc.)
        2. jobs_data : Jobs complets (factures, documents, transactions)
        3. jobs_metrics : Compteurs pour system prompt
        """
        try:
            logger.info(f"[SESSION_DATA] 🔄 Chargement données permanentes pour session {self.session_key}")
            
            # ═══ ÉTAPE 1 : Détecter mode connexion ═══
            mode = await self._detect_connection_mode()
            logger.info(f"[SESSION_DATA] 🔍 Mode détecté: {mode}")
            
            # ═══ ÉTAPE 2 : Charger contexte utilisateur ═══
            from ..firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            
            # ⚠️ Vérifier que client_uuid n'est pas vide
            if not client_uuid or client_uuid.strip() == '':
                raise ValueError(
                    f"client_uuid vide ou invalide pour user_id={self.context.user_id}, collection_name={self.context.collection_name}"
                )
            
            # Utiliser le client_uuid passé en paramètre
            logger.info(f"[SESSION_DATA] ✅ client_uuid fourni: {client_uuid}")
            
            # Récupérer le profil complet depuis Firebase
            full_profile = await asyncio.to_thread(
                firebase_service.reconstruct_full_client_profile,
                self.context.user_id,
                client_uuid,
                self.context.collection_name
            )
            
            if not full_profile:
                raise ValueError(f"Profil client vide pour user={self.context.user_id}")

            workflow_params_raw = full_profile.get("workflow_params") or {}
            workflow_params = copy.deepcopy(workflow_params_raw)
            
            # Extraire les IDs pour construire mandate_path complet
            client_id = full_profile.get('_client_id')
            mandate_id = full_profile.get('_mandate_id')
            
            if not client_id or not mandate_id:
                raise ValueError(f"client_id ou mandate_id manquant dans full_profile")
            
            # ⭐ Construire le mandate_path complet (chemin Firebase réel)
            mandate_path = f'clients/{self.context.user_id}/bo_clients/{client_id}/mandates/{mandate_id}'

            # Charger la table des fonctions (function_table) pour les règles Router
            function_table_source_path = f"{mandate_path}/setup/function_table"
            function_table_info: Dict[str, Any] = {
                "raw": None,
                "ask_approval": {},
                "available": False,
                "source_path": function_table_source_path,
                "status_message": (
                    "Règles d'approbation par département non configurées. "
                    "Configurez-les dans le panneau de configuration de la société."
                ),
            }

            try:
                def _load_function_table() -> Tuple[Optional[Dict[str, Any]], Dict[str, bool]]:
                    doc_ref = firebase_service.firestore_client.document(function_table_source_path)
                    doc = doc_ref.get()
                    if not doc.exists:
                        return None, {}
                    data = doc.to_dict() or {}
                    approvals: Dict[str, bool] = {}
                    for service_name, payload in data.items():
                        if isinstance(payload, dict):
                            approvals[service_name] = bool(payload.get("ask_approval", False))
                    return data, approvals

                raw_data, approvals = await asyncio.to_thread(_load_function_table)
                if raw_data:
                    function_table_info["raw"] = raw_data
                    function_table_info["ask_approval"] = approvals
                    function_table_info["available"] = True
                    function_table_info["status_message"] = (
                        "Règles d'approbation par département chargées depuis Firebase."
                    )
            except Exception as function_table_error:
                logger.warning(
                    "[SESSION_DATA] ⚠️ Impossible de charger function_table pour %s : %s",
                    mandate_path,
                    function_table_error,
                )

            workflow_params["function_table"] = function_table_info
            
            # ⭐ Résolution timezone (sans appel LLM)
            # But: éviter le sentinel "no timezone found" dans le contexte, et persister dès qu'on peut.
            resolved_timezone = full_profile.get("mandate_timezone")
            if not resolved_timezone or resolved_timezone == "no timezone found":
                try:
                    from ..pinnokio_agentic_workflow.tools.timezone_enum import get_timezone_for_country
                    import pytz

                    country_value = (full_profile.get("mandate_country") or "").strip()
                    tz_guess = get_timezone_for_country(country_value) if country_value else None

                    if tz_guess:
                        # Valider IANA + persister dans le mandat
                        try:
                            pytz.timezone(tz_guess)
                            firebase_service.save_timezone_to_mandate(mandate_path, tz_guess)
                            resolved_timezone = tz_guess
                            logger.info(
                                "[SESSION_DATA] ✅ Timezone auto-déduite depuis pays=%s → %s (persistée)",
                                country_value,
                                tz_guess,
                            )
                        except Exception as persist_error:
                            # On garde la timezone déduite pour la session même si la persistance échoue
                            resolved_timezone = tz_guess
                            logger.warning(
                                "[SESSION_DATA] ⚠️ Timezone auto-déduite (%s) mais persistance a échoué: %s",
                                tz_guess,
                                persist_error,
                            )
                except Exception as tz_error:
                    logger.warning(
                        "[SESSION_DATA] ⚠️ Impossible de déduire la timezone depuis le pays: %s",
                        tz_error,
                    )
            
            # ⭐ Construire user_context avec les BONS noms de champs
            self.user_context = {
                # Identifiants
                "client_uuid": client_uuid,
                "client_id": client_id,
                "mandate_id": mandate_id,
                "mandate_path": mandate_path,  # Chemin complet Firebase
                
                # Noms (avec préfixes corrects depuis reconstruct_full_client_profile)
                "company_name": full_profile.get("mandate_legal_name") or full_profile.get("mandate_contact_space_name") or self.context.collection_name,
                "contact_space_id": full_profile.get("mandate_contact_space_id"),
                "contact_space_name": full_profile.get("mandate_contact_space_name"),
                "legal_name": full_profile.get("mandate_legal_name"),
                "country":full_profile.get("mandate_country",),
                "user_language": full_profile.get("mandate_user_language", "fr"),
                
                # DMS (Document Management System)
                "dms_system": full_profile.get("erp_dms_system", "google_drive"),
                "drive_space_parent_id": full_profile.get("mandate_drive_space_parent_id"),
                "input_drive_doc_id": full_profile.get("mandate_input_drive_doc_id"),
                "output_drive_doc_id": full_profile.get("mandate_output_drive_doc_id"),
                
                # ERP - Types
                "mandate_bank_erp": full_profile.get("mandate_bank_erp"),
                "mandate_ap_erp": full_profile.get("mandate_ap_erp"),
                "mandate_ar_erp": full_profile.get("mandate_ar_erp"),
                "mandate_gl_accounting_erp": full_profile.get("mandate_gl_accounting_erp"),
                
                # ERP - Connexion Odoo
                "erp_odoo_url": full_profile.get("erp_odoo_url"),
                "erp_odoo_username": full_profile.get("erp_odoo_username"),
                "erp_odoo_db": full_profile.get("erp_odoo_db"),
                "erp_odoo_company_name": full_profile.get("erp_odoo_company_name"),
                "erp_erp_type": full_profile.get("erp_erp_type"),
                "erp_secret_manager": full_profile.get("erp_secret_manager"),
                
                # Communication
                "communication_mode": full_profile.get("mandate_communication_chat_type", "pinnokio"),
                "log_communication_mode": full_profile.get("mandate_communication_log_type", "pinnokio"),
                
                # Devise
                "base_currency": full_profile.get("mandate_base_currency"),
                
                # ⭐ WORKFLOW PARAMS (paramètres d'approbation)
                "workflow_params": workflow_params
            }

            # Injecter timezone uniquement si valide/résolue
            if resolved_timezone and resolved_timezone != "no timezone found":
                self.user_context["timezone"] = resolved_timezone
            
            # 🔍 DEBUG : Vérifier que workflow_params est bien inclus
            workflow_params = self.user_context.get("workflow_params", {})
            logger.info(
                f"[SESSION_DATA] 🔍 DEBUG workflow_params inclus dans session.user_context: "
                f"{workflow_params is not None and workflow_params != {}}"
            )
            if workflow_params:
                logger.info(
                    f"[SESSION_DATA] 🔍 DEBUG workflow_params clés: {list(workflow_params.keys())}"
                )
                if "Apbookeeper_param" in workflow_params:
                    logger.info(
                        f"[SESSION_DATA] 🔍 DEBUG Apbookeeper_param: "
                        f"approval_required={workflow_params['Apbookeeper_param'].get('apbookeeper_approval_required')}, "
                        f"approval_contact_creation={workflow_params['Apbookeeper_param'].get('apbookeeper_approval_contact_creation')}"
                    )
                ft_info = workflow_params.get("function_table", {})
                logger.info(
                    "[SESSION_DATA] 🔍 DEBUG function_table disponible=%s, services=%s",
                    ft_info.get("available", False),
                    list((ft_info.get("ask_approval") or {}).keys()),
                )
            
            logger.info(
                f"[SESSION_DATA] ✅ Contexte utilisateur chargé - "
                f"company={self.user_context.get('company_name')}, "
                f"mandate_path={self.user_context.get('mandate_path')}"
            )
            
            # ═══ ÉTAPE 3 : Charger jobs et métriques ═══
            self.jobs_data, self.jobs_metrics = await self._load_jobs_with_metrics(mode)
            
            logger.info(
                f"[SESSION_DATA] ✅ Jobs chargés - "
                f"APBookkeeper: {self.jobs_metrics.get('APBOOKEEPER', {}).get('to_do', 0)} to_do, "
                f"Router: {self.jobs_metrics.get('ROUTER', {}).get('to_process', 0)} to_process, "
                f"Bank: {self.jobs_metrics.get('BANK', {}).get('to_reconcile', 0)} to_reconcile"
            )
            
            # ═══ ÉTAPE 4 : Synchroniser vers Redis (Multi-Instance Ready) ═══
            self._sync_to_redis()
            
            logger.info(f"[SESSION_DATA] 🎉 Données session initialisées + synchronisées Redis (SANS brain - créés par chat)")
            
        except Exception as e:
            logger.error(f"[SESSION_DATA] ❌ Erreur chargement données: {e}", exc_info=True)
            raise
    
    async def _detect_connection_mode(self) -> str:
        """
        Détecte si l'utilisateur est en mode UI (connecté) ou BACKEND (déconnecté).
        
        Logique :
        - Vérifier le heartbeat dans UnifiedRegistry
        - Si heartbeat récent (< 30s) → Mode UI
        - Sinon → Mode BACKEND
        
        Returns:
            "UI" ou "BACKEND"
        """
        try:
            from ..registry.unified_registry import UnifiedRegistryService
            
            registry = UnifiedRegistryService()
            
            # Vérifier si l'utilisateur a un heartbeat récent
            is_connected = registry.is_user_connected(
                self.context.user_id
            )
            
            return "UI" if is_connected else "BACKEND"
            
        except Exception as e:
            logger.warning(f"[SESSION] Erreur détection mode connexion: {e}")
            # Par défaut, mode BACKEND (plus sûr)
            return "BACKEND"
    
    async def _load_jobs_with_metrics(self, mode: str) -> Tuple[Dict, Dict]:
        """
        Charge les jobs depuis Firebase/Drive/ERP et calcule les métriques.
        
        Args:
            mode: "UI" (avec cache Redis) ou "BACKEND" (direct)
        
        Returns:
            Tuple[Dict, Dict]: (jobs_data, jobs_metrics)
        """
        try:
            from ..pinnokio_agentic_workflow.tools.job_loader import JobLoader
            
            loader = JobLoader(
                user_id=self.context.user_id,
                company_id=self.context.collection_name,
                client_uuid=self.user_context.get("client_uuid")
            )
            
            jobs_data, jobs_metrics = await loader.load_all_jobs(
                mode=mode,
                user_context=self.user_context
            )
            
            # 🔍 LOGS DE DIAGNOSTIC - Détails des jobs chargés
            logger.info(f"[SESSION] 🔍 DIAGNOSTIC jobs_data - Clés: {list(jobs_data.keys())}")
            logger.info(f"[SESSION] 🔍 DIAGNOSTIC ROUTER - Type: {type(jobs_data.get('ROUTER'))}, "
                       f"Clés: {list(jobs_data.get('ROUTER', {}).keys()) if isinstance(jobs_data.get('ROUTER'), dict) else 'N/A'}")
            
            if isinstance(jobs_data.get('ROUTER'), dict):
                router_data = jobs_data['ROUTER']
                unprocessed = router_data.get('to_process', [])
                in_process = router_data.get('in_process', [])
                processed = router_data.get('processed', [])
                logger.info(f"[SESSION] 🔍 DIAGNOSTIC ROUTER détails - "
                           f"to_process: {len(unprocessed) if isinstance(unprocessed, list) else 'Not a list'}, "
                           f"in_process: {len(in_process) if isinstance(in_process, list) else 'Not a list'}, "
                           f"processed: {len(processed) if isinstance(processed, list) else 'Not a list'}")
                
                # Afficher le premier document si présent
                if isinstance(unprocessed, list) and len(unprocessed) > 0:
                    first_doc = unprocessed[0]
                    logger.info(f"[SESSION] 🔍 DIAGNOSTIC ROUTER premier doc - "
                               f"Clés: {list(first_doc.keys()) if isinstance(first_doc, dict) else 'Not a dict'}")
                else:
                    logger.warning(f"[SESSION] ⚠️ DIAGNOSTIC ROUTER - Aucun document unprocessed !")
            
            logger.info(f"[SESSION] 🔍 DIAGNOSTIC jobs_metrics - "
                       f"ROUTER.to_process: {jobs_metrics.get('ROUTER', {}).get('to_process', 'N/A')}")
            
            return jobs_data, jobs_metrics
            
        except Exception as e:
            logger.error(f"[SESSION] Erreur chargement jobs: {e}", exc_info=True)
            # Retourner des structures vides avec message d'avertissement
            empty_metrics = {
                "APBOOKEEPER": {"to_do": 0, "in_process": 0, "done": 0},
                "ROUTER": {"to_process": 0, "in_process": 0, "done": 0},
                "BANK": {"to_reconcile": 0, "pending": 0, "in_process": 0},
                "warnings": [f"⚠️ Erreur lors du chargement des jobs: {str(e)}"]
            }
            return {}, empty_metrics
    
    def update_context(self, **kwargs):
        """
        Met à jour le contexte dynamiquement.
        
        ⚠️ PARTIALLY DEPRECATED: La partie agent est obsolète.
        """
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        
        # ⚠️ DEPRECATED: self.agent n'existe plus dans la nouvelle architecture
        # Les brains par thread gèrent maintenant leur propre contexte
    
    # ═══════════════════════════════════════════════════════════════
    # TRACKING PRÉSENCE UTILISATEUR (Mode UI vs BACKEND)
    # ═══════════════════════════════════════════════════════════════
    
    def enter_chat(self, thread_key: str, session_id: Optional[str] = None):
        """
        Marque que l'utilisateur vient d'envoyer un message sur ce thread.
        Appelé automatiquement par send_message().
        
        ⭐ Multi-Instance: Synchronise l'état avec Redis.
        ⭐ Multi-Onglet: Si session_id fourni, utilise update_presence_multi_tab()
           permettant à plusieurs onglets d'avoir différents threads ouverts.
        
        Args:
            thread_key: Thread sur lequel l'utilisateur est actif
            session_id: ID unique de l'onglet (optionnel, pour support multi-onglet)
        """
        self.is_on_chat_page = True
        self.current_active_thread = thread_key
        self.last_activity[thread_key] = datetime.now(timezone.utc)
        
        # ⭐ Sync Redis (multi-instance + multi-onglet)
        if session_id:
            # Mode multi-onglet: chaque onglet a son propre thread actif
            self._state_manager.update_presence_multi_tab(
                self.context.user_id,
                self.context.collection_name,
                session_id=session_id,
                thread_key=thread_key,
                is_on_chat_page=True
            )
        else:
            # Mode legacy: écrase le thread pour tous les onglets
            self._state_manager.update_presence(
                self.context.user_id,
                self.context.collection_name,
                is_on_chat_page=True,
                current_active_thread=thread_key
            )
        
        logger.info(
            f"[SESSION_TRACKING] 👤 User ENTRÉ sur chat - "
            f"session={self.session_key}, thread={thread_key}, tab_session={session_id or 'legacy'}"
        )
    
    def switch_thread(self, new_thread_key: str, session_id: Optional[str] = None):
        """
        Marque que l'utilisateur change de thread (toujours sur la page chat).
        Appelé par load_chat_history() quand user change de conversation.
        
        ⭐ Multi-Instance: Synchronise l'état avec Redis.
        ⭐ Multi-Onglet: Si session_id fourni, ne modifie que cet onglet.
        
        Args:
            new_thread_key: Nouveau thread actif
            session_id: ID unique de l'onglet (optionnel, pour support multi-onglet)
        """
        old_thread = self.current_active_thread
        self.current_active_thread = new_thread_key
        self.last_activity[new_thread_key] = datetime.now(timezone.utc)
        
        # ⭐ Sync Redis (multi-instance + multi-onglet)
        if session_id:
            # Mode multi-onglet: chaque onglet a son propre thread actif
            self._state_manager.update_presence_multi_tab(
                self.context.user_id,
                self.context.collection_name,
                session_id=session_id,
                thread_key=new_thread_key,
                is_on_chat_page=True
            )
        else:
            # Mode legacy: écrase le thread pour tous les onglets
            self._state_manager.update_presence(
                self.context.user_id,
                self.context.collection_name,
                is_on_chat_page=True,
                current_active_thread=new_thread_key
            )
        
        logger.info(
            f"[SESSION_TRACKING] 🔄 User SWITCH thread - "
            f"session={self.session_key}, {old_thread} → {new_thread_key}, tab_session={session_id or 'legacy'}"
        )
    
    def leave_chat(self, session_id: Optional[str] = None):
        """
        Marque que l'utilisateur quitte la page chat.
        Appelé par signal RPC depuis Reflex (unmount, navigation).
        
        ⭐ Multi-Instance: Synchronise l'état avec Redis.
        ⭐ Multi-Onglet: Si session_id fourni, ne supprime que cet onglet de la présence.
        
        Note: On conserve current_active_thread pour historique.
        
        Args:
            session_id: ID unique de l'onglet (optionnel, pour support multi-onglet)
        """
        old_thread = self.current_active_thread
        self.is_on_chat_page = False
        # ⚠️ NE PAS effacer current_active_thread (utile pour logs/debug)
        
        # ⭐ Sync Redis (multi-instance + multi-onglet)
        if session_id:
            # Mode multi-onglet: supprime uniquement cette session
            self._state_manager.remove_tab_presence(
                self.context.user_id,
                self.context.collection_name,
                session_id=session_id
            )
        else:
            # Mode legacy: marque tout comme quitté
            self._state_manager.update_presence(
                self.context.user_id,
                self.context.collection_name,
                is_on_chat_page=False,
                current_active_thread=old_thread
            )
        
        logger.info(
            f"[SESSION_TRACKING] 👋 User QUITTÉ chat - "
            f"session={self.session_key}, était sur thread={old_thread}, tab_session={session_id or 'legacy'}"
        )
    
    def is_user_on_specific_thread(self, thread_key: str, check_redis: bool = False) -> bool:
        """
        Vérifie si l'utilisateur est ACTUELLEMENT actif sur ce thread précis.
        
        ⭐ Multi-Instance: Peut vérifier dans Redis pour cross-instance.
        ⭐ Multi-Onglet: Utilise is_user_on_thread_multi_tab() qui vérifie
           si AU MOINS UN onglet a ce thread ouvert.
        
        Logique:
        - is_on_chat_page = False → False (pas sur la page)
        - is_on_chat_page = True + current_active_thread = thread_key → True
        - is_on_chat_page = True + current_active_thread ≠ thread_key → False
        
        Args:
            thread_key: Thread à vérifier
            check_redis: Si True, vérifie dans Redis (pour callbacks cross-instance)
            
        Returns:
            True si user est sur la page chat ET sur ce thread précis
        """
        # ⭐ Mode cross-instance: Lire directement depuis Redis (supporte multi-onglet)
        if check_redis:
            # Utilise la nouvelle méthode qui supporte multi-onglet
            is_on = self._state_manager.is_user_on_thread_multi_tab(
                self.context.user_id,
                self.context.collection_name,
                thread_key
            )
            logger.debug(
                f"[SESSION_TRACKING] Check user on thread={thread_key} (REDIS/multi-tab): {is_on}"
            )
            return is_on
        
        # Mode local (performance)
        is_on = self.is_on_chat_page and self.current_active_thread == thread_key
        
        logger.debug(
            f"[SESSION_TRACKING] Check user on thread={thread_key}: {is_on} "
            f"(is_on_chat_page={self.is_on_chat_page}, "
            f"current_active_thread={self.current_active_thread})"
        )
        
        return is_on
    
    
    
    def _extract_response_text(self, response) -> str:
        """Extrait le texte de la réponse de BaseAIAgent."""
        response_text = ""
        if isinstance(response, dict):
            if 'text_output' in response:
                text_output = response.get('text_output', {})
                if isinstance(text_output, dict):
                    content = text_output.get('content', {})
                    if isinstance(content, dict):
                        response_text = content.get('answer_text', '')
                    else:
                        response_text = str(content)
                else:
                    response_text = str(text_output)
            else:
                response_text = str(response)
        else:
            response_text = str(response)
        
        return response_text
    
    
    def get_last_response_duration_ms(self, thread_key: str) -> int:
        """Retourne la durée de la dernière réponse en ms."""
        if thread_key in self.response_times and self.response_times[thread_key]:
            return int(self.response_times[thread_key][-1])
        return 0


class LLMManager:
    """Gestionnaire LLM utilisant Firebase Realtime Database."""
    ONBOARDING_LIKE_MODES = {"onboarding_chat", "apbookeeper_chat", "router_chat", "banker_chat"}
    ACTIVE_CHAT_MODES = {"apbookeeper_chat", "router_chat", "banker_chat"}
    
    def __init__(self):
        self.sessions: Dict[str, LLMSession] = {}  # Cache local (reconstruction à la demande)
        self._lock = threading.Lock()
        self.rtdb_formatter = RTDBMessageFormatter()
        self.streaming_controller = StreamingController()
        
        # ═══════════════════════════════════════════════════════════════
        # MANAGERS REDIS POUR SCALABILITÉ MULTI-INSTANCE
        # ═══════════════════════════════════════════════════════════════
        from .approval_state_manager import get_approval_state_manager
        from .session_registry_manager import get_session_registry_manager
        from .brain_state_manager import get_brain_state_manager
        from .processed_messages_manager import get_processed_messages_manager
        
        self.approval_manager = get_approval_state_manager()
        self.session_registry = get_session_registry_manager()
        self.brain_state_manager = get_brain_state_manager()
        self.processed_messages = get_processed_messages_manager()
    
    def _is_onboarding_like(self, chat_mode: Optional[str]) -> bool:
        return (chat_mode or "") in self.ONBOARDING_LIKE_MODES

    def _resolve_messages_container(self, chat_mode: Optional[str]) -> str:
        if (chat_mode or "") in self.ACTIVE_CHAT_MODES:
            return "active_chats"
        return "chats"

    def _get_messages_base_path(self, collection_name: str, thread_key: str, chat_mode: Optional[str]) -> str:
        container = self._resolve_messages_container(chat_mode)
        return f"{collection_name}/{container}/{thread_key}/messages"
    
    def _get_rtdb_ref(self, path: str):
        """Obtient une référence Firebase RTDB."""
        from ..listeners_manager import _get_rtdb_ref
        return _get_rtdb_ref(path)
    
    async def _load_history_from_rtdb(self, collection_name: str, thread_key: str, chat_mode: Optional[str] = None) -> list:
        """
        Charge l'historique d'un chat depuis Firebase RTDB (mode BACKEND uniquement).
        En mode UI, l'historique est déjà fourni via WebSocket.
        
        Args:
            collection_name: Nom de la collection (société)
            thread_key: Clé du thread de chat
            mode: Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            Liste des messages [{"role": "user/assistant", "content": "..."}]
        """
        try:
            messages_mode = self._resolve_messages_container(chat_mode)
            logger.info(
                f"[LOAD_RTDB] 📥 Chargement historique depuis RTDB (BACKEND): {collection_name}/{messages_mode}/{thread_key}"
            )
            
            # Utiliser la méthode existante get_channel_messages de FirebaseRealtimeChat
            from ..firebase_providers import FirebaseRealtimeChat
            
            firebase_mgmt = FirebaseRealtimeChat()
            messages = firebase_mgmt.get_channel_messages(
                space_code=collection_name,
                thread_key=thread_key,
                limit=1000,  # Charger tous les messages récents
                mode=messages_mode
            )
            
            if not messages:
                logger.info(f"[LOAD_RTDB] ℹ️ Aucun historique trouvé (nouveau chat)")
                return []
            
            # Transformer au format attendu par BaseAIAgent
            history = []
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content", "")
                message_type = msg.get("message_type")
                
                # Compat héritée : ignorer les anciens messages LOG_FOLLOW_UP persistés côté agent utilisateur
                if message_type == "LOG_FOLLOW_UP":
                    logger.debug(f"[LOAD_RTDB] ⏭️ Message LOG_FOLLOW_UP filtré: {msg.get('message_id', 'unknown')}")
                    continue
                
                # ⭐ FILTRER les messages non-MESSAGE (CARD, WORKFLOW, CMMD, FOLLOW_MESSAGE, etc.)
                # Ces messages ne doivent PAS être injectés dans l'historique LLM
                # - FOLLOW_MESSAGE : Géré en mode intermédiation, pas dans l'historique agent
                # - Autres types : Envoyés uniquement via WebSocket (géré dans _handle_onboarding_log_event)
                if message_type and message_type not in ["MESSAGE", None]:
                    logger.debug(
                        f"[LOAD_RTDB] ⏭️ Message non-MESSAGE filtré: "
                        f"type={message_type} message_id={msg.get('id', msg.get('message_id', 'unknown'))}"
                    )
                    continue
                
                # Filtrer les messages vides ou invalides
                if role in ["user", "assistant"] and content:
                    history.append({
                        "role": role,
                        "content": content
                    })
            
            logger.info(f"[LOAD_RTDB] ✅ Historique chargé: {len(history)} messages")
            return history
            
        except Exception as e:
            logger.error(f"[LOAD_RTDB] ❌ Erreur chargement historique: {e}", exc_info=True)
            return []
    
    async def _ensure_session_initialized(
        self,
        user_id: str,
        collection_name: str,
        chat_mode: str = "general_chat"
        ) -> LLMSession:
        """
        Garantit qu'une session existe avec toutes les données permanentes chargées.
        
        ⭐ CRITIQUE pour isolation: Charge user_context, jobs_data, jobs_metrics
        
        Utilisé par:
        - Mode UI (send_message)
        - Mode BACKEND (callback LPT, scheduler)
        
        Returns:
            LLMSession avec données permanentes chargées
        
        Raises:
            Exception si l'initialisation échoue
        """
        session_key = f"{user_id}:{collection_name}"

        def _schedule_billing_catchup(s: "LLMSession") -> None:
            """
            Lance un rattrapage de facturation en arrière-plan (non-bloquant) au niveau session (user+collection).
            Utilise un garde-fou Redis pour éviter de relancer en boucle.
            """
            try:
                if not s or not s.user_context:
                    return

                mandate_path = (s.user_context or {}).get("mandate_path")
                if not mandate_path:
                    return

                # Garde-fou (cross-instance): 1 fois / heure par user+collection
                should_run = True
                try:
                    from ..redis_client import get_redis
                    r = get_redis()
                    key = f"billing:catchup:{user_id}:{collection_name}"
                    should_run = bool(r.set(key, "1", nx=True, ex=3600))
                except Exception:
                    # Redis indisponible: fallback best-effort (pas de garde-fou cross-instance)
                    should_run = True

                if not should_run:
                    return

                from ..firebase_providers import get_firebase_management
                fbm = get_firebase_management()

                async def _run():
                    await asyncio.to_thread(
                        fbm.ensure_chat_daily_expenses,
                        mandate_path,
                        collection_name,
                        user_id,
                        7,
                        False,
                    )
                    await asyncio.to_thread(
                        fbm.get_user_balance,
                        mandate_path,
                        user_id,
                    )

                asyncio.create_task(_run())
            except Exception as e:
                logger.warning("[ENSURE_SESSION] billing_catchup_error session=%s err=%s", session_key, str(e))
        
        # Vérifier si session existe avec données chargées
        session = None
        with self._lock:
            if session_key in self.sessions:
                session = self.sessions[session_key]
                
                # Si données permanentes chargées → Vérifier si chat_mode doit être mis à jour
                if session.user_context is not None:
                    # ⭐ NOUVEAU : Mettre à jour le chat_mode si différent
                    if session.context.chat_mode != chat_mode:
                        logger.info(
                            f"[ENSURE_SESSION] 🔄 Mise à jour chat_mode: "
                            f"{session.context.chat_mode} → {chat_mode}"
                        )
                        session.update_context(chat_mode=chat_mode)
                    else:
                        logger.info(
                            f"[ENSURE_SESSION] ✅ Session OK avec données permanentes: {session_key}"
                        )
                        _schedule_billing_catchup(session)
                        return session
                else:
                    # Session existe mais données manquantes
                    logger.warning(
                        f"[ENSURE_SESSION] Session existe mais user_context=None, "
                        f"rechargement des données permanentes..."
                    )
                    session = None
        
        # Si session existe avec user_context et chat_mode différent → Mettre à jour les brains
        if session is not None and session.user_context is not None:
            try:
                # ⭐ Mettre à jour tous les brains actifs avec le nouveau chat_mode
                for thread_key, brain in session.active_brains.items():
                    brain.initialize_system_prompt(
                        chat_mode=chat_mode,
                        jobs_metrics=session.jobs_metrics
                    )
                    # Charger les données selon le mode
                    if chat_mode == "onboarding_chat":
                        await brain.load_onboarding_data()
                    elif chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                        # Pour ces modes, le job_id est le thread_key
                        job_id = thread_key
                        await brain.load_job_data(job_id)
                    logger.info(
                        f"[ENSURE_SESSION] ✅ Brain thread={thread_key} "
                        f"mis à jour avec chat_mode={chat_mode}"
                    )
            except Exception as e:
                logger.warning(
                    f"[ENSURE_SESSION] ⚠️ Erreur mise à jour brains: {e}"
                )
            
            logger.info(
                f"[ENSURE_SESSION] ✅ Session OK avec données permanentes: {session_key}"
            )
            _schedule_billing_catchup(session)
            return session
        
        # Session n'existe pas en mémoire → Vérifier si état existe dans Redis
        logger.info(
            f"[ENSURE_SESSION] Session absente de la mémoire, vérification Redis: {session_key}"
        )
        
        # ⭐ OPTIMISATION : Vérifier si l'état existe déjà dans Redis (créé par orchestrateur dashboard)
        from .session_state_manager import get_session_state_manager
        session_state_manager = get_session_state_manager()
        
        redis_state_exists = session_state_manager.session_exists(user_id, collection_name)
        
        if redis_state_exists:
            logger.info(
                f"[ENSURE_SESSION] ✅ État trouvé dans Redis, restauration session: {session_key}"
            )
            
            # Charger l'état depuis Redis pour vérifier s'il est complet
            redis_state = session_state_manager.load_session_state(user_id, collection_name)
            
            if redis_state and redis_state.get("user_context"):
                # ⭐ État Redis complet → Créer session qui restaurera depuis Redis
                logger.info(
                    f"[ENSURE_SESSION] 📥 État Redis complet, création session avec restauration: {session_key}"
                )
                
                # Créer le contexte avec les paramètres par défaut
                # Le chat_mode sera mis à jour après restauration si nécessaire
                context = LLMContext(
                    user_id=user_id,
                    collection_name=collection_name,
                    dms_system="google_drive",  # Sera mis à jour depuis user_context restauré
                    dms_mode="prod",
                    chat_mode=chat_mode
                )
                
                # Créer la session (elle restaurera automatiquement depuis Redis via _try_restore_from_redis)
                session = LLMSession(
                    session_key=session_key,
                    context=context
                )
                
                # Vérifier que la restauration a réussi
                if session.user_context is not None:
                    # Mettre à jour le dms_system depuis le user_context restauré
                    if session.user_context.get("dms_system"):
                        actual_dms_system = session.user_context.get("dms_system", "google_drive")
                        if session.context.dms_system != actual_dms_system:
                            session.update_context(dms_system=actual_dms_system)
                            logger.info(
                                f"[ENSURE_SESSION] 🔄 DMS mis à jour depuis Redis: {actual_dms_system}"
                            )
                    
                    # Mettre à jour le chat_mode si nécessaire
                    if session.context.chat_mode != chat_mode:
                        logger.info(
                            f"[ENSURE_SESSION] 🔄 Mise à jour chat_mode après restauration: "
                            f"{session.context.chat_mode} → {chat_mode}"
                        )
                        session.update_context(chat_mode=chat_mode)
                    
                    # Stocker en mémoire
                    with self._lock:
                        # Double vérification pour éviter race condition
                        if session_key not in self.sessions:
                            self.sessions[session_key] = session
                            logger.info(
                                f"[ENSURE_SESSION] 💾 Session restaurée depuis Redis et stockée en mémoire: {session_key}"
                            )
                        else:
                            # Une autre instance a créé la session entre temps
                            session = self.sessions[session_key]
                            logger.info(
                                f"[ENSURE_SESSION] ✅ Session déjà créée par autre thread, réutilisation: {session_key}"
                            )
                    
                    _schedule_billing_catchup(session)
                    return session
                else:
                    logger.warning(
                        f"[ENSURE_SESSION] ⚠️ État Redis incomplet (user_context=None), "
                        f"initialisation complète nécessaire: {session_key}"
                    )
                    # Continuer vers initialisation complète
            else:
                logger.warning(
                    f"[ENSURE_SESSION] ⚠️ État Redis incomplet ou vide, "
                    f"initialisation complète nécessaire: {session_key}"
                )
                # Continuer vers initialisation complète
        else:
            logger.info(
                f"[ENSURE_SESSION] 📝 Aucun état Redis trouvé, initialisation complète: {session_key}"
            )
        
        # ⭐ État Redis absent ou incomplet → Initialisation complète depuis Firebase
        logger.info(
            f"[ENSURE_SESSION] Initialisation session complète (nouvelle ou données manquantes): {session_key}"
        )
        
        # ⭐ CRITIQUE : Récupérer client_uuid depuis contact_space_id AVANT d'initialiser
        client_uuid = None
        try:
            from ..firebase_providers import FirebaseManagement
            firebase_service = FirebaseManagement()
            lookup = firebase_service.resolve_client_by_contact_space(user_id, collection_name)
            if lookup and lookup.get("client_uuid"):
                client_uuid = lookup["client_uuid"]
                logger.info(
                    f"[ENSURE_SESSION] ✅ client_uuid résolu: {client_uuid} "
                    f"(contact_space={collection_name})"
                )
            else:
                logger.error(
                    f"[ENSURE_SESSION] ❌ Impossible de résoudre client_uuid pour "
                    f"user_id={user_id}, collection_name={collection_name}"
                )
                raise Exception(
                    f"client_uuid introuvable pour contact_space={collection_name}. "
                    "Session corrompue dans Redis?"
                )
        except Exception as e:
            logger.error(f"[ENSURE_SESSION] ❌ Erreur résolution client_uuid: {e}")
            raise Exception(f"Impossible de résoudre client_uuid: {e}")
        
        result = await self.initialize_session(
            user_id=user_id,
            collection_name=collection_name,
            client_uuid=client_uuid,
            chat_mode=chat_mode
        )
        
        if not result.get("success"):
            raise Exception(f"Échec initialisation session: {result.get('error')}")
        
        # ⭐ CRITIQUE : Si initialisation en background, ATTENDRE qu'elle se termine
        status = result.get("status")
        if status in ["initializing", "started"]:
            logger.info(
                f"[ENSURE_SESSION] ⏳ Attente fin initialisation background: {session_key} (status={status})"
            )
            
            # Attendre max 30 secondes que la session apparaisse en mémoire
            max_wait = 30
            waited = 0
            while waited < max_wait:
                await asyncio.sleep(0.5)  # Vérifier toutes les 500ms
                waited += 0.5
                
                with self._lock:
                    if session_key in self.sessions:
                        session = self.sessions[session_key]
                        if session.user_context is not None:
                            logger.info(
                                f"[ENSURE_SESSION] ✅ Session prête après {waited}s: {session_key}"
                            )
                            _schedule_billing_catchup(session)
                            return session
            
            # Timeout
            raise Exception(
                f"Timeout ({max_wait}s) en attendant l'initialisation de la session. "
                "Le background thread est peut-être bloqué."
            )
        
        # Si status = "already_initialized", la session devrait être immédiatement disponible
        with self._lock:
            if session_key not in self.sessions:
                raise Exception(
                    f"Session non trouvée après initialisation (status={status}). "
                    "Désynchronisation Redis ↔ Mémoire détectée."
                )
            
            session = self.sessions[session_key]
            
            # Vérification finale
            if session.user_context is None:
                raise Exception("Session initialisée mais user_context toujours None")
            
            logger.info(
                f"[ENSURE_SESSION] ✅ Session initialisée avec données permanentes: {session_key}"
            )
            _schedule_billing_catchup(session)
            return session
    
    async def _initialize_session_background(
        self,
        user_id: str,
        collection_name: str,
        client_uuid: str,
        dms_system: str = "google_drive",
        dms_mode: str = "prod",
        chat_mode: str = "general_chat"
    ):
        """
        Fonction de background pour l'initialisation réelle de la session LLM.
        Appelée en arrière-plan par initialize_session().
        """
        from ..ws_hub import hub
        from ..redis_client import get_redis

        base_session_key = f"{user_id}:{collection_name}"
        redis_init_key = f"llm_init:{base_session_key}"

        try:
            logger.info(f"[LLM_INIT_BG] 🔄 Début initialisation background pour {base_session_key}")

            # Créer nouvelle session SANS lock (opération rapide)
            context = LLMContext(
                user_id=user_id,
                collection_name=collection_name,
                dms_system=dms_system,
                dms_mode=dms_mode,
                chat_mode=chat_mode
            )

            session = LLMSession(
                session_key=base_session_key,
                context=context
            )

            # ⚠️ CRITIQUE: Charger les données HORS du lock (opération longue ~plusieurs secondes)
            # Sinon, le lock bloque tous les autres threads pendant le chargement Firebase
            logger.info(f"[LLM_INIT_BG] 📥 Chargement données session...")
            await session.initialize_session_data(client_uuid)
            logger.info(f"[LLM_INIT_BG] ✅ Données session initialisées")

            # Mettre à jour le dms_system depuis le user_context chargé
            if session.user_context and session.user_context.get("dms_system"):
                actual_dms_system = session.user_context.get("dms_system", "google_drive")
                if session.context.dms_system != actual_dms_system:
                    session.update_context(dms_system=actual_dms_system)
                    logger.info(f"[LLM_INIT_BG] 🔄 DMS mis à jour: {actual_dms_system}")

            # Stocker en cache mémoire (avec lock pour thread-safety)
            with self._lock:
                # ⚠️ Double vérification : si une session existe déjà, ne pas écraser
                # (Protection contre race condition si Redis est DOWN)
                if base_session_key in self.sessions:
                    existing_session = self.sessions[base_session_key]
                    # Si la session existante a déjà des données, la garder
                    if existing_session.user_context is not None:
                        logger.warning(
                            f"[LLM_INIT_BG] ⚠️ Session déjà présente en mémoire avec données - "
                            f"Abandon stockage pour éviter écrasement"
                        )
                        return
                
                self.sessions[base_session_key] = session
                logger.info(f"[LLM_INIT_BG] 💾 Session stockée en cache mémoire")

            # Marquer comme terminé dans Redis
            redis_client = get_redis()
            redis_client.setex(
                redis_init_key,
                3600,  # TTL 1 heure
                json.dumps({
                    "status": "ready",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "session_id": base_session_key
                })
            )
            logger.info(f"[LLM_INIT_BG] ✅ Redis marqué comme 'ready'")

            # Publier événement de succès via WebSocket
            await hub.broadcast(user_id, {
                "type": "llm.initialization_complete",
                "payload": {
                    "session_id": base_session_key,
                    "collection_name": collection_name,
                    "status": "ready",
                    "context_loaded": True,
                    "jobs_data_loaded": bool(session.jobs_data),
                    "completed_at": datetime.now(timezone.utc).isoformat()
                }
            })
            logger.info(f"[LLM_INIT_BG] 📡 Événement 'llm.initialization_complete' publié")

        except Exception as e:
            logger.error(f"[LLM_INIT_BG] ❌ Erreur initialisation: {e}", exc_info=True)

            # Marquer comme erreur dans Redis
            try:
                redis_client = get_redis()
                redis_client.setex(
                    redis_init_key,
                    300,  # TTL 5 minutes pour erreurs
                    json.dumps({
                        "status": "error",
                        "error": str(e),
                        "failed_at": datetime.now(timezone.utc).isoformat()
                    })
                )
            except Exception:
                pass

            # Publier événement d'erreur
            try:
                await hub.broadcast(user_id, {
                    "type": "llm.initialization_error",
                    "payload": {
                        "session_id": base_session_key,
                        "collection_name": collection_name,
                        "status": "error",
                        "error": str(e),
                        "failed_at": datetime.now(timezone.utc).isoformat()
                    }
                })
                logger.info(f"[LLM_INIT_BG] 📡 Événement 'llm.initialization_error' publié")
            except Exception as broadcast_error:
                logger.error(f"[LLM_INIT_BG] Erreur broadcast: {broadcast_error}")

    async def initialize_session(
        self,
        user_id: str,
        collection_name: str,
        client_uuid: str,
        dms_system: str = "google_drive",
        dms_mode: str = "prod",
        chat_mode: str = "general_chat"
        ) -> dict:
        """
        Initialise une session LLM pour un utilisateur/société.

        ⭐ NOUVELLE ARCHITECTURE ASYNCHRONE :
        - Vérification cache Redis immédiate
        - Retour instantané (< 100ms) si session existe ou en cours d'initialisation
        - Initialisation réelle en arrière-plan
        - Notification via WebSocket quand terminé

        Cette approche évite les blocages de 19 secondes lors du changement de société.
        """
        from ..redis_client import get_redis

        try:
            logger.info(f"[LLM_INIT] 📥 Requête initialize_session")
            logger.info(f"[LLM_INIT] Paramètres: user_id={user_id}, collection_name={collection_name}, client_uuid={client_uuid}")

            base_session_key = f"{user_id}:{collection_name}"
            redis_init_key = f"llm_init:{base_session_key}"

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : Vérifier cache Redis (état d'initialisation)
            # ═══════════════════════════════════════════════════════════
            redis_client = get_redis()

            try:
                cached_status = redis_client.get(redis_init_key)
                if cached_status:
                    status_data = json.loads(cached_status.decode('utf-8'))
                    current_status = status_data.get("status")

                    if current_status == "ready":
                        # ⭐ CRITIQUE : Vérifier que la session existe AUSSI en mémoire
                        # (En cas de redémarrage après crash, Redis peut dire "ready" mais mémoire vide)
                        with self._lock:
                            if base_session_key not in self.sessions:
                                logger.warning(f"[LLM_INIT] ⚠️ Redis dit 'ready' mais session absente de la mémoire → Réinitialisation")
                                # Invalider cache Redis pour forcer réinitialisation
                                redis_client.delete(redis_init_key)
                                # Continuer vers ÉTAPE 2 pour réinitialiser
                            else:
                                logger.info(f"[LLM_INIT] ✅ Session déjà initialisée (Redis + Mémoire): {base_session_key}")
                                return {
                                    "success": True,
                                    "session_id": base_session_key,
                                    "status": "already_initialized",
                                    "message": "Session LLM déjà active",
                                    "completed_at": status_data.get("completed_at")
                                }

                    elif current_status == "initializing":
                        logger.info(f"[LLM_INIT] ⏳ Initialisation déjà en cours (Redis): {base_session_key}")
                        return {
                            "success": True,
                            "session_id": base_session_key,
                            "status": "initializing",
                            "message": "Initialisation en cours...",
                            "started_at": status_data.get("started_at")
                        }
            except Exception as redis_error:
                logger.warning(f"[LLM_INIT] ⚠️ Erreur lecture Redis: {redis_error}")

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : Vérifier cache mémoire local
            # ═══════════════════════════════════════════════════════════
            with self._lock:
                # Vérifier si session existe déjà en mémoire
                if base_session_key in self.sessions:
                    session = self.sessions[base_session_key]
                    logger.info(f"[LLM_INIT] ✅ Session existante en mémoire: {base_session_key}")

                    # Vérifier si contexte doit être rafraîchi
                    current_client_uuid = (session.user_context or {}).get("client_uuid") if session.user_context else None

                    # Si client_uuid a changé → invalider et relancer
                    if current_client_uuid and current_client_uuid != client_uuid:
                        logger.info(f"[LLM_INIT] 🔄 client_uuid changé: {current_client_uuid} → {client_uuid}")
                        # Supprimer session existante
                        del self.sessions[base_session_key]
                        # Marquer pour réinitialisation
                        pass
                    else:
                        # Mettre à jour cache Redis
                        try:
                            redis_client.setex(
                                redis_init_key,
                                3600,
                                json.dumps({
                                    "status": "ready",
                                    "completed_at": datetime.now(timezone.utc).isoformat(),
                                    "session_id": base_session_key
                                })
                            )
                        except Exception:
                            pass

                        logger.info(f"[LLM_INIT] ✅ Session réutilisée: {base_session_key}")
                        return {
                            "success": True,
                            "session_id": base_session_key,
                            "status": "already_initialized",
                            "message": "Session LLM déjà active (cache mémoire)"
                        }

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : Marquer comme "initializing" dans Redis (atomique)
            # ═══════════════════════════════════════════════════════════
            try:
                # Utiliser SETNX pour atomicité (évite race conditions)
                was_set = redis_client.setnx(
                    redis_init_key,
                    json.dumps({
                        "status": "initializing",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "session_id": base_session_key
                    })
                )

                if not was_set:
                    # Une autre instance a déjà commencé l'initialisation
                    logger.info(f"[LLM_INIT] ⏳ Initialisation déjà lancée par une autre instance: {base_session_key}")
                    return {
                        "success": True,
                        "session_id": base_session_key,
                        "status": "initializing",
                        "message": "Initialisation en cours (autre instance)..."
                    }

                # Définir TTL après le SETNX réussi
                redis_client.expire(redis_init_key, 300)  # TTL 5 minutes pour "initializing"
                logger.info(f"[LLM_INIT] ✅ Marqué comme 'initializing' dans Redis")

            except Exception as redis_error:
                logger.warning(f"[LLM_INIT] ⚠️ Erreur Redis SETNX: {redis_error}")
                # Continuer quand même (fallback si Redis indisponible)

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : Lancer initialisation en arrière-plan
            # ═══════════════════════════════════════════════════════════
            logger.info(f"[LLM_INIT] 🚀 Lancement initialisation background pour {base_session_key}")

            asyncio.create_task(
                self._initialize_session_background(
                    user_id=user_id,
                    collection_name=collection_name,
                    client_uuid=client_uuid,
                    dms_system=dms_system,
                    dms_mode=dms_mode,
                    chat_mode=chat_mode
                )
            )

            logger.info(f"[LLM_INIT] ⚡ Retour immédiat - initialisation en cours en background")
            return {
                "success": True,
                "session_id": base_session_key,
                "status": "initializing",
                "message": "Initialisation LLM lancée en arrière-plan",
                "started_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"[LLM_INIT] ❌ Erreur: {e}", exc_info=True)

            # Nettoyer Redis en cas d'erreur
            try:
                redis_client = get_redis()
                redis_client.delete(redis_init_key)
            except Exception:
                pass

            return {
                "success": False,
                "error": str(e),
                "message": "Échec de l'initialisation LLM"
            }
    
    async def invalidate_user_context(self, user_id: str, collection_name: str) -> dict:
        """
        Invalide le contexte utilisateur en mémoire et dans Redis pour forcer un rechargement.
        """
        session_key = f"{user_id}:{collection_name}"
        logger.info(f"[INVALIDATE_CONTEXT] Requête reçue pour {session_key}")

        with self._lock:
            session = self.sessions.get(session_key)

        brains_snapshot: List[Tuple[str, Any]] = []
        if session:
            with session._lock:
                session.user_context = None
                session.jobs_data = None
                session.jobs_metrics = None
                session.thread_contexts.clear()
                brains_snapshot = list(session.active_brains.items())

            logger.info(
                "[INVALIDATE_CONTEXT] Session trouvée, contexte remis à zéro pour %s",
                session_key,
            )
        else:
            logger.info(
                "[INVALIDATE_CONTEXT] Session introuvable pour %s (aucune donnée à invalider)",
                session_key,
            )

        brains_invalidated = 0
        for thread_key, brain in brains_snapshot:
            if not brain:
                continue
            try:
                brain.user_context = None
                brain.jobs_data = None
                brain.jobs_metrics = None
                brains_invalidated += 1
                logger.info(
                    "[INVALIDATE_CONTEXT] Brain thread=%s marqué pour rechargement",
                    thread_key,
                )
            except Exception as brain_error:
                logger.warning(
                    "[INVALIDATE_CONTEXT] Erreur remise à zéro brain thread=%s: %s",
                    thread_key,
                    brain_error,
                )

        redis_deleted: Optional[bool] = None
        try:
            from ..redis_client import get_redis

            redis_client = get_redis()
            context_key = f"context:{user_id}:{collection_name}"
            deleted = redis_client.delete(context_key)
            redis_deleted = bool(deleted)
            if redis_deleted:
                logger.info(
                    "[INVALIDATE_CONTEXT] Clé Redis supprimée: %s",
                    context_key,
                )
            else:
                logger.info(
                    "[INVALIDATE_CONTEXT] Clé Redis absente (aucune suppression): %s",
                    context_key,
                )
        except Exception as redis_error:
            logger.warning(
                "[INVALIDATE_CONTEXT] Erreur suppression Redis pour %s: %s",
                session_key,
                redis_error,
            )

        status = "session_reset" if session else "session_absent"
        return {
            "success": True,
            "status": status,
            "session_key": session_key,
            "brains_invalidated": brains_invalidated,
            "redis_deleted": redis_deleted,
        }
    
    async def start_onboarding_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        chat_mode: str = "onboarding_chat",
        initial_message: Optional[str] = None,
        system_prompt: Optional[str] = None,
        job_status: Optional[str] = None
        ) -> dict:
        """
        Lance le job LPT d'onboarding pour un thread.

        ⚠️ IMPORTANT : Le thread RTDB existe DÉJÀ (créé côté frontend).

        ⭐ Rôle :
        - Crée/initialise le brain si nécessaire
        - Charge les données onboarding
        - Lance TOUJOURS le job LPT (sauf si déjà lancé)
        - Envoie le message de notification à l'utilisateur

        ⭐ Différence avec enter_chat() :
        - start_onboarding_chat() : LANCE le job LPT (appelé via bouton "Lancer onboarding")
        - enter_chat() : Initialise juste le brain/context (appelé quand on sélectionne le chat)
        - enter_chat() NE lance PAS le LPT (juste conversation avec l'agent)

        Scénarios :
        1. Nouveau chat → start_onboarding_chat() → crée brain → lance LPT → message
        2. Chat existant (sélection) → enter_chat() → initialise brain → PAS de LPT
        3. Chat existant (bouton lancement) → start_onboarding_chat() → vérifie si déjà lancé → lance LPT si pas lancé → message
        """

        try:
            logger.info(
                f"[ONBOARDING_START] 🚀 user={user_id} collection={collection_name} thread={thread_key}"
            )

            # 1. Initialiser la session en mode onboarding et marquer la présence
            session = await self._ensure_session_initialized(
                user_id=user_id,
                collection_name=collection_name,
                chat_mode="onboarding_chat"
            )

            session.enter_chat(thread_key)

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : CHARGER/CRÉER LE BRAIN POUR CE THREAD
            # ═══════════════════════════════════════════════════════════
            history = await self._load_history_from_rtdb(collection_name, thread_key, chat_mode)
            load_result = await self.load_chat_history(
                user_id=user_id,
                collection_name=collection_name,
                thread_key=thread_key,
                history=history
            )

            if not load_result.get("success"):
                return {
                    "success": False,
                    "error": "brain_initialization_failed",
                    "message": load_result.get("message", "Échec création brain"),
                    "details": load_result
                }

            brain = session.active_brains.get(thread_key)
            if not brain:
                raise RuntimeError("Brain introuvable après initialisation")

            await brain.load_onboarding_data()
            onboarding_data = brain.onboarding_data or {}

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : VÉRIFIER SI LE JOB A DÉJÀ ÉTÉ LANCÉ (protection doublons)
            # ═══════════════════════════════════════════════════════════
            # Utiliser l'historique déjà chargé (pas de double chargement)
            job_already_launched = len(history) > 0

            # Précharger l'historique des logs (utilisé après lancement du job)
            log_entries = await self._load_onboarding_log_history(
                brain=brain,
                collection_name=collection_name,
                session=session,
                thread_key=thread_key
            )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : LANCER AUTOMATIQUEMENT LE JOB LPT
            # (Identique à enter_chat - même format, même logique)
            # ═══════════════════════════════════════════════════════════
            lpt_status = None
            job_id = (brain.onboarding_data or {}).get("job_id") if brain.onboarding_data else None
            launch_result = None

            if not job_already_launched:
                logger.info(f"[ONBOARDING_START] 🚀 Lancement automatique du job onboarding pour thread={thread_key}")
                
                from ..pinnokio_agentic_workflow.tools.lpt_client import LPTClient
                lpt_client = LPTClient()
                launch_result = await lpt_client.launch_onboarding_job(
                    user_id=user_id,
                    company_id=collection_name,
                    thread_key=thread_key,
                    session=session,
                    brain=brain
                )

                lpt_status = launch_result.get("status") if isinstance(launch_result, dict) else None
                job_id = (brain.onboarding_data or {}).get("job_id") if brain.onboarding_data else None

                # ═══════════════════════════════════════════════════════════
                # ÉTAPE 4 : ENVOYER LE PREMIER MESSAGE ASSISTANT
                # (Identique à enter_chat - même méthode, même format)
                # ═══════════════════════════════════════════════════════════
                if lpt_status in ("queued", "started"):
                    await self._send_onboarding_start_message(
                        user_id=user_id,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        job_id=job_id or "onboarding"
                    )
                    logger.info(f"[ONBOARDING_START] ✅ Message de démarrage envoyé pour job={job_id}")
            else:
                logger.info(
                    f"[ONBOARDING_START] ⏭️ Job déjà lancé pour thread={thread_key}, "
                    f"pas de relance (protection doublons)"
                )
                # Récupérer le job_id depuis les données onboarding même si job déjà lancé
                job_id = (brain.onboarding_data or {}).get("job_id") if brain.onboarding_data else None
                
                # ⭐ ENVOYER QUAND MÊME UN MESSAGE pour informer l'utilisateur que le job est actif
                await self._send_onboarding_start_message(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    job_id=job_id or "onboarding"
                )
                logger.info(f"[ONBOARDING_START] ✅ Message informatif envoyé pour job existant={job_id}")

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 5 : DÉMARRER L'ÉCOUTE RTDB
            # (Identique à enter_chat - même canaux, même logique)
            # ═══════════════════════════════════════════════════════════
            await self._ensure_onboarding_listener(
                session=session,
                brain=brain,
                collection_name=collection_name,
                thread_key=thread_key,
                initial_entries=log_entries
            )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 5.5 : VÉRIFIER MODE INTERMÉDIATION AU CHARGEMENT
            # ═══════════════════════════════════════════════════════════
            # ⭐ Forcer le rechargement du status depuis Firestore pour avoir la valeur à jour
            if self._is_onboarding_like(session.context.chat_mode) and session.context.chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                job_id = thread_key
                await brain.load_job_data(job_id, force_reload=True)
                job_status = brain.job_data.get("status") if brain.job_data else job_status
            
            await self._check_intermediation_on_load(
                session=session,
                collection_name=collection_name,
                thread_key=thread_key,
                job_status=job_status
            )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 6 : CONFIGURATION TRACABILITÉ (optionnel, pour compatibilité)
            # ═══════════════════════════════════════════════════════════
            if isinstance(launch_result, dict) and launch_result.get("status") == "queued":
                if session.thread_states is not None:
                    session.thread_states[thread_key] = "onboarding_running"
                
                brain.active_task_data = {
                    "task_id": launch_result.get("task_id") or launch_result.get("job_id"),
                    "execution_id": launch_result.get("execution_id"),
                    "mandate_path": (session.user_context or {}).get("mandate_path") if session.user_context else None,
                    "thread_key": thread_key,
                    "task_type": "ONBOARDING"
                }

            # Gérer les différents cas de succès
            if job_already_launched:
                # Job déjà lancé (détecté par protection doublons)
                success = True
                message = "Onboarding déjà initialisé (job précédemment lancé)"
            elif lpt_status in ("queued", "started"):
                # Job lancé avec succès
                success = True
                message = "Onboarding démarré avec succès"
            else:
                # Échec du lancement
                success = False
                message = "Onboarding initialisé avec avertissement"

            response = {
                "success": success,
                "message": message,
                "thread_key": thread_key,
                "job_id": job_id,
                "lpt_status": lpt_status,
                "job_already_launched": job_already_launched,
                "lpt": launch_result
            }

            if job_already_launched:
                response["info"] = "Le job était déjà lancé, pas de relance effectuée"
            elif not success:
                response["warning"] = "Le job onboarding n'a pas pu être lancé"
                if isinstance(launch_result, dict) and launch_result.get("error"):
                    response["error"] = launch_result.get("error")

            logger.info(
                f"[ONBOARDING_START] ✅ Terminé - success={success} status={lpt_status} thread={thread_key}"
            )

            return response

        except Exception as e:
            logger.error(f"[ONBOARDING_START] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec du démarrage de l'onboarding"
            }

    async def stop_onboarding_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        chat_mode: str = "onboarding_chat",
        job_ids: Optional[str] = None,
        mandates_path: Optional[str] = None
        ) -> dict:
        """
        Arrête immédiatement le job d'onboarding (action synchrone, pas LPT).
        
        ⚠️ IMPORTANT : Le thread RTDB existe DÉJÀ (créé côté frontend).
        
        ⭐ Rôle :
        - Initialise le brain/context (même logique que enter_chat)
        - Envoie directement une requête HTTP d'arrêt au point de terminaison
        - Attend le retour 200/202
        - Écrit le résultat dans RTDB pour que l'agent informe l'utilisateur
        
        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société
            thread_key: Thread sur lequel le job tourne
            chat_mode: Mode de chat (default: "onboarding_chat")
            job_ids: ID du job à arrêter (dans payload)
            mandates_path: Chemin du mandat (dans payload)
        """
        try:
            logger.info(
                f"[ONBOARDING_STOP] 🛑 user={user_id} collection={collection_name} "
                f"thread={thread_key} job_ids={job_ids}"
            )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : INITIALISER SESSION ET BRAIN (identique à enter_chat)
            # ═══════════════════════════════════════════════════════════
            session = await self._ensure_session_initialized(
                user_id=user_id,
                collection_name=collection_name,
                chat_mode="onboarding_chat"
            )

            session.enter_chat(thread_key)

            # Charger/Créer le brain pour ce thread
            history = await self._load_history_from_rtdb(collection_name, thread_key, session.context.chat_mode)
            load_result = await self.load_chat_history(
                user_id=user_id,
                collection_name=collection_name,
                thread_key=thread_key,
                history=history
            )

            if not load_result.get("success"):
                return {
                    "success": False,
                    "error": "brain_initialization_failed",
                    "message": load_result.get("message", "Échec création brain"),
                    "details": load_result
                }

            brain = session.active_brains.get(thread_key)
            if not brain:
                raise RuntimeError("Brain introuvable après initialisation")

            await brain.load_onboarding_data()
            
            # Récupérer mandate_path depuis le contexte si non fourni
            if not mandates_path:
                # Essayer depuis session.user_context d'abord
                mandates_path = (session.user_context or {}).get("mandate_path") if session.user_context else None
                
                # Fallback sur brain.user_context si pas trouvé
                if not mandates_path:
                    context = brain.get_user_context()
                    mandates_path = context.get("mandate_path") if context else None
                
                # Fallback sur session.context.mandate_path
                if not mandates_path:
                    mandates_path = session.context.mandate_path if session.context else None
                
                if not mandates_path:
                    return {
                        "success": False,
                        "error": "mandates_path_required",
                        "message": "mandates_path est requis pour arrêter le job"
                    }

            # Récupérer job_id depuis les données onboarding si non fourni
            if not job_ids:
                job_ids = (brain.onboarding_data or {}).get("job_id") if brain.onboarding_data else None
                if not job_ids:
                    return {
                        "success": False,
                        "error": "job_id_required",
                        "message": "job_ids est requis pour arrêter le job"
                    }

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : CONSTRUIRE LE PAYLOAD D'ARRÊT
            # ═══════════════════════════════════════════════════════════
            payload = {
                "job_ids": job_ids,
                "mandates_path": mandates_path
            }

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : ENVOYER REQUÊTE HTTP DIRECTE (action immédiate, pas LPT)
            # ═══════════════════════════════════════════════════════════
            # Déterminer l'URL selon l'environnement
            import os
            environment = os.getenv('PINNOKIO_ENVIRONMENT', 'LOCAL').upper()
            
            if environment == 'LOCAL':
                base_url = 'http://127.0.0.1:8080'
            else:  # PROD
                base_url = os.getenv(
                    'PINNOKIO_AWS_URL', 
                    'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
                )
            
            # ⭐ Format attendu: /stop-onboarding/<job_id> avec job_id dans le chemin
            stop_url = f"{base_url}/stop-onboarding/{job_ids}"
            
            logger.info(f"[ONBOARDING_STOP] 📤 Envoi HTTP POST vers: {stop_url}")
            logger.info(f"[ONBOARDING_STOP] 📦 Payload: {payload}")
            
            import aiohttp
            stop_result = None
            http_status = None
            
            try:
                async with aiohttp.ClientSession() as session_http:
                    async with session_http.post(
                        stop_url, 
                        json=payload, 
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        http_status = response.status
                        
                        if http_status in (200, 202):
                            try:
                                stop_result = await response.json()
                            except Exception:
                                stop_result = {"status": "stopped", "message": await response.text()}
                            
                            logger.info(
                                f"[ONBOARDING_STOP] ✅ Réponse HTTP {http_status} - "
                                f"job_ids={job_ids} thread={thread_key}"
                            )
                        else:
                            error_text = await response.text()
                            logger.error(
                                f"[ONBOARDING_STOP] ❌ Erreur HTTP {http_status}: {error_text}"
                            )
                            stop_result = {
                                "status": "error",
                                "error": f"HTTP {http_status}: {error_text}"
                            }

            except aiohttp.ClientError as ce:
                logger.error(f"[ONBOARDING_STOP] ❌ Erreur de connexion HTTP: {ce}", exc_info=True)
                stop_result = {
                    "status": "error",
                    "error": f"Erreur de connexion: {str(ce)}",
                    "error_type": "connection_error"
                }
            except asyncio.TimeoutError:
                logger.error(f"[ONBOARDING_STOP] ⏱️ Timeout après 30s vers {stop_url}")
                stop_result = {
                    "status": "error",
                    "error": "Timeout de connexion (30s)",
                    "error_type": "timeout"
                }

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : ÉCRIRE LE RÉSULTAT DANS RTDB POUR QUE L'AGENT INFORME L'UTILISATEUR
            # ═══════════════════════════════════════════════════════════
            import uuid
            from datetime import datetime, timezone
            import json as _json
            
            assistant_message_id = str(uuid.uuid4())
            assistant_timestamp = datetime.now(timezone.utc).isoformat()
            
            # Construire le message selon le résultat
            if http_status in (200, 202) and stop_result and stop_result.get("status") != "error":
                message_content = (
                    f"✅ **Arrêt du job d'onboarding**\n\n"
                    f"Le job **{job_ids}** a été arrêté avec succès.\n\n"
                    f"Le processus d'onboarding a été interrompu. Vous pouvez continuer à me poser "
                    f"des questions si vous avez besoin d'aide."
                )
                if isinstance(stop_result, dict) and stop_result.get("message"):
                    message_content += f"\n\n**Détails** : {stop_result.get('message')}"
            else:
                error_msg = (
                    stop_result.get("error") if isinstance(stop_result, dict) 
                    else "Erreur inconnue lors de l'arrêt"
                )
                message_content = (
                    f"❌ **Erreur lors de l'arrêt du job**\n\n"
                    f"Impossible d'arrêter le job **{job_ids}**.\n\n"
                    f"**Erreur** : {error_msg}\n\n"
                    f"Veuillez réessayer ou contacter le support."
                )
            
            # Écrire le message dans RTDB
            messages_base_path = self._get_messages_base_path(
                collection_name, thread_key, session.context.chat_mode
            )
            assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            
            message_data = self.rtdb_formatter.format_ai_message(
                content=message_content,
                user_id=user_id,
                message_id=assistant_message_id,
                timestamp=assistant_timestamp,
                metadata={
                    "status": "completed",
                    "automation": "onboarding_stop",
                    "job_ids": job_ids,
                    "http_status": http_status,
                    "stop_result": stop_result
                }
            )
            
            assistant_msg_ref.set(message_data)
            
            logger.info(
                f"[ONBOARDING_STOP] ✅ Message résultat envoyé - "
                f"thread={thread_key}, job_ids={job_ids}, http_status={http_status}"
            )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 5 : RETOURNER LA RÉPONSE
            # ═══════════════════════════════════════════════════════════
            success = http_status in (200, 202) and stop_result and stop_result.get("status") != "error"
            
            response = {
                "success": success,
                "message": "Job arrêté avec succès" if success else "Erreur lors de l'arrêt du job",
                "thread_key": thread_key,
                "job_ids": job_ids,
                "http_status": http_status,
                "stop_result": stop_result,
                "assistant_message_id": assistant_message_id
            }

            if not success:
                if isinstance(stop_result, dict) and stop_result.get("error"):
                    response["error"] = stop_result.get("error")

            logger.info(
                f"[ONBOARDING_STOP] ✅ Terminé - success={success} http_status={http_status} "
                f"thread={thread_key}"
            )

            return response

        except Exception as e:
            logger.error(f"[ONBOARDING_STOP] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec de l'arrêt de l'onboarding"
            }
    
    async def send_message(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message: str,
        chat_mode: str = "general_chat",
        system_prompt: str = None,
        selected_tool: str = None
        ) -> dict:
        """
        Point d'entrée MODE UI : Envoie un message et stream la réponse via WebSocket.
        
        ⭐ FLUX UNIFIÉ : Utilise _process_unified_workflow() avec enable_streaming=True
        """
        try:
            logger.info(
                f"[SEND_MESSAGE] 🚀 MODE UI - user={user_id} collection={collection_name} "
                f"thread={thread_key} message={message[:100]}..."
            )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : GARANTIR INITIALISATION SESSION (avec données permanentes)
            # ═══════════════════════════════════════════════════════════
            session = await self._ensure_session_initialized(
                user_id=user_id,
                collection_name=collection_name,
                chat_mode=chat_mode
            )
            
            logger.info(f"[SEND_MESSAGE] ✅ Session garantie avec données permanentes")
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1.5 : VÉRIFIER MODE INTERMÉDIATION
            # ═══════════════════════════════════════════════════════════
            # Si le thread est en mode intermédiation, rediriger vers le handler spécial
            if session.intermediation_mode.get(thread_key, False):
                logger.info(
                    f"[SEND_MESSAGE] 🔄 Mode intermédiation actif - "
                    f"redirection vers handler spécial pour thread={thread_key}"
                )
                return await self._handle_intermediation_response(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message,
                    session=session
                )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1.6 : VÉRIFIER SI WORKFLOW ACTIF SUR CE THREAD
            # ═══════════════════════════════════════════════════════════
            # Si un workflow (tâche planifiée) est en cours, gérer l'interaction
            from .workflow_state_manager import get_workflow_state_manager
            workflow_manager = get_workflow_state_manager()
            
            if workflow_manager.is_workflow_running(user_id, collection_name, thread_key):
                logger.info(
                    f"[SEND_MESSAGE] 🔄 Workflow actif détecté sur thread={thread_key}"
                )
                
                # Queue le message et détermine l'action
                queue_result = workflow_manager.queue_user_message(
                    user_id, collection_name, thread_key, message
                )
                
                if queue_result.get("is_terminate"):
                    # TERMINATE : Le workflow reprendra avec le pré-prompt
                    # On ne fait rien ici, le workflow verra le pending_message
                    logger.info(
                        f"[SEND_MESSAGE] 🔄 TERMINATE détecté - workflow reprendra"
                    )
                    
                    # Envoyer signal WebSocket au frontend
                    from ..ws_hub import hub
                    ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
                    await hub.broadcast(user_id, {
                        "type": "WORKFLOW_RESUMING",
                        "channel": ws_channel,
                        "payload": {
                            "thread_key": thread_key,
                            "message": "Reprise du workflow en cours..."
                        }
                    })
                    
                    return {
                        "success": True,
                        "status": "workflow_resuming",
                        "message": "Reprise du workflow en cours...",
                        "thread_key": thread_key
                    }
                else:
                    # Message normal : Workflow pausé, conversation normale
                    # ⭐ BASCULER chat_mode de "task_execution" à "general_chat" pour conversation normale
                    if session.context.chat_mode == "task_execution":
                        logger.info(
                            f"[SEND_MESSAGE] 🔄 Basculement chat_mode: task_execution → general_chat "
                            f"(workflow pausé, conversation utilisateur)"
                        )
                        # Mettre à jour le chat_mode dans la session
                        session.context.chat_mode = "general_chat"
                        # Mettre à jour le brain si nécessaire
                        if thread_key in session.active_brains:
                            brain = session.active_brains[thread_key]
                            # Réinitialiser le system prompt avec le nouveau chat_mode
                            brain.initialize_system_prompt(chat_mode="general_chat")
                            logger.info(
                                f"[SEND_MESSAGE] 🔄 Brain mis à jour avec chat_mode=general_chat"
                            )
                    
                    # Envoyer signal WebSocket au frontend
                    from ..ws_hub import hub
                    ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
                    await hub.broadcast(user_id, {
                        "type": "WORKFLOW_PAUSED",
                        "channel": ws_channel,
                        "payload": {
                            "thread_key": thread_key,
                            "message": "Workflow en pause - Conversation active"
                        }
                    })
                    
                    logger.info(
                        f"[SEND_MESSAGE] ⏸️ Workflow pausé - conversation normale (chat_mode={session.context.chat_mode})"
                    )
                    # On continue avec le flux normal de send_message ci-dessous
            else:
                # ⭐ IMPORTANT: Pas de workflow actif
                # Si thread task_* mais chat_mode=task_execution → basculer vers general_chat
                if thread_key.startswith("task_") and session.context.chat_mode == "task_execution":
                    logger.info(
                        f"[SEND_MESSAGE] 🔄 Thread task_* sans workflow actif - "
                        f"Basculement chat_mode: task_execution → general_chat"
                    )
                    session.context.chat_mode = "general_chat"
                    
                    # Mettre à jour le brain si nécessaire
                    if thread_key in session.active_brains:
                        brain = session.active_brains[thread_key]
                        brain.initialize_system_prompt(chat_mode="general_chat")
                        logger.info(f"[SEND_MESSAGE] ✅ Brain mis à jour avec chat_mode=general_chat")
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : VÉRIFIER/CRÉER LE BRAIN
            # ═══════════════════════════════════════════════════════════
            # Auto-créer le brain si il n'existe pas (migration Next.js)
            if thread_key not in session.active_brains:
                logger.warning(
                    f"[SEND_MESSAGE] ⚠️ Brain non trouvé pour thread={thread_key}. "
                    f"Auto-création en cours..."
                )

                # Charger l'historique depuis RTDB pour créer le brain
                history = await self._load_history_from_rtdb(
                    collection_name, thread_key, chat_mode
                )

                # Créer le brain via load_chat_history
                load_result = await self.load_chat_history(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    history=history or []
                )

                if not load_result.get("success"):
                    logger.error(
                        f"[SEND_MESSAGE] ❌ Échec auto-création brain: {load_result.get('error')}"
                    )
                    return {
                        "success": False,
                        "error": "Brain auto-initialization failed",
                        "message": load_result.get("message", "Échec création brain"),
                        "thread_key": thread_key
                    }

                # Marquer la présence de l'utilisateur
                session.enter_chat(thread_key)
                logger.info(f"[SEND_MESSAGE] ✅ Brain auto-créé avec succès")
            else:
                logger.info(f"[SEND_MESSAGE] ✅ Brain trouvé et prêt")
            
            brain = session.active_brains[thread_key]

            # Charger les données selon le mode
            if session.context.chat_mode == "onboarding_chat":
                await brain.load_onboarding_data()
            elif session.context.chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                # Pour ces modes, le job_id est le thread_key
                # Si le document n'existe pas encore, c'est normal (job pas encore lancé)
                job_id = thread_key
                await brain.load_job_data(job_id)

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2.5 : SAUVEGARDER MESSAGE UTILISATEUR DANS RTDB (Next.js)
            # ═══════════════════════════════════════════════════════════
            # Note: Le frontend Next.js ne sauvegarde pas les messages - on le fait ici
            user_message_id = str(uuid.uuid4())
            user_timestamp = datetime.now(timezone.utc).isoformat()
            messages_base_path = self._get_messages_base_path(
                collection_name, thread_key, session.context.chat_mode
            )

            # Sauvegarder le message utilisateur
            user_msg_path = f"{messages_base_path}/{user_message_id}"
            user_msg_ref = self._get_rtdb_ref(user_msg_path)

            user_message_data = self.rtdb_formatter.format_user_message(
                content=message,
                user_id=user_id,
                message_id=user_message_id,
                timestamp=user_timestamp
            )

            user_msg_ref.set(user_message_data)
            logger.info(f"[SEND_MESSAGE] ✅ Message utilisateur sauvegardé: {user_message_id}")

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2.6 : AUTO-NAMING POUR CHAT VIERGE
            # ═══════════════════════════════════════════════════════════
            # Détecter si c'est le premier message d'un chat (nouveau chat sans historique)
            # Note: On vérifie par le nombre de messages, pas par le préfixe du thread_key
            try:
                from ..firebase_providers import get_firebase_realtime

                realtime_service = get_firebase_realtime()
                # Si le thread n'a pas de messages (ou juste celui qu'on vient d'ajouter), c'est le premier
                existing_messages = realtime_service.get_thread_messages(
                    space_code=collection_name,
                    thread_key=thread_key,
                    mode="chats",
                    limit=3
                )
                # Si seulement 1 message (celui qu'on vient d'ajouter), c'est le premier
                is_first_message = len(existing_messages or []) <= 1

                if is_first_message:
                    logger.info(f"[SEND_MESSAGE] 🏷️ Premier message détecté - lancement auto-naming pour thread={thread_key}")
                    # Lancer l'auto-naming en arrière-plan
                    asyncio.create_task(
                        self._auto_name_virgin_chat(
                            user_id=user_id,
                            collection_name=collection_name,
                            thread_key=thread_key,
                            first_message=message,
                            chat_mode=chat_mode
                        )
                    )
            except Exception as e:
                logger.warning(f"[SEND_MESSAGE] Auto-naming check failed: {e}")

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : PRÉPARER MESSAGE ASSISTANT RTDB
            # ═══════════════════════════════════════════════════════════
            assistant_message_id = str(uuid.uuid4())
            assistant_timestamp = datetime.now(timezone.utc).isoformat()
            
            # Message assistant initial (vide, pour streaming)
            assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            
            # ⭐ Utiliser le formatter pour garantir compatibilité UI
            initial_message_data = self.rtdb_formatter.format_ai_message(
                content="",
                user_id=user_id,
                message_id=assistant_message_id,
                timestamp=assistant_timestamp,
                metadata={
                    "status": "streaming",
                    "streaming_progress": 0.0
                }
            )
            
            assistant_msg_ref.set(initial_message_data)
            
            logger.info(f"[SEND_MESSAGE] Messages RTDB créés")
            
            if self._is_onboarding_like(session.context.chat_mode) and message.strip().upper().endswith("TERMINATE"):
                await self._synthesize_and_send_terminate_response(
                    session=session,
                    brain=brain,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    user_message=message
                )

            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : LANCER WORKFLOW UNIFIÉ EN ARRIÈRE-PLAN
            # ═══════════════════════════════════════════════════════════
            # ⭐ Utiliser le chat_mode de la session (peut avoir changé si workflow pausé)
            effective_chat_mode = session.context.chat_mode
            
            task = asyncio.create_task(
                self._process_unified_workflow(
                    session=session,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message,
                    assistant_message_id=assistant_message_id,
                    assistant_timestamp=assistant_timestamp,
                    enable_streaming=True,  # ← MODE UI : Streaming WebSocket activé
                    chat_mode=effective_chat_mode,  # ⭐ Utilise le chat_mode actuel de la session
                    system_prompt=system_prompt
                )
            )
            
            # Enregistrer stream pour contrôle d'arrêt
            await self.streaming_controller.register_stream(
                session_key=f"{user_id}:{collection_name}",
                thread_key=thread_key,
                task=task
            )
            
            logger.info(f"[SEND_MESSAGE] ✅ Workflow unifié lancé en arrière-plan (MODE UI)")
            
            return {
                "success": True,
                "user_message_id": user_message_id,  # ✅ Backend saves user message now
                "assistant_message_id": assistant_message_id,
                "ws_channel": f"chat:{user_id}:{collection_name}:{thread_key}",
                "message": "Message envoyé, réponse en cours de streaming via WebSocket"
            }
            
        except Exception as e:
            logger.error(f"Erreur envoi message LLM: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _auto_name_virgin_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        first_message: str,
        chat_mode: str = "general_chat"
    ) -> None:
        """
        Auto-name a virgin chat based on the first message.
        Runs in background - does not block message processing.

        Uses heuristics to generate a name (can be enhanced with LLM later).
        """
        try:
            logger.info(f"[AUTO_NAMING] 🏷️ Starting auto-naming for thread={thread_key}")

            # Generate name using heuristics
            generated_name = self._generate_chat_name_from_message(first_message)

            if not generated_name:
                logger.warning(f"[AUTO_NAMING] ⚠️ Failed to generate name, using default")
                generated_name = f"Chat {datetime.now().strftime('%d/%m %H:%M')}"

            # Rename the chat in Firebase
            from ..firebase_providers import get_firebase_realtime

            realtime_service = get_firebase_realtime()
            mode = "chats"  # Default mode for user chats

            success = realtime_service.rename_chat(
                space_code=collection_name,
                thread_key=thread_key,
                new_name=generated_name,
                mode=mode
            )

            if success:
                logger.info(f"[AUTO_NAMING] ✅ Chat renamed to: '{generated_name}'")

                # Notify frontend via WebSocket
                from ..ws_hub import hub
                await hub.broadcast(user_id, {
                    "type": "chat.sessions_list",
                    "payload": {
                        "action": "renamed",
                        "thread_key": thread_key,
                        "new_name": generated_name
                    }
                })
            else:
                logger.error(f"[AUTO_NAMING] ❌ Failed to rename chat")

        except Exception as e:
            logger.error(f"[AUTO_NAMING] ❌ Error: {e}", exc_info=True)

    def _generate_chat_name_from_message(self, first_message: str, max_length: int = 50) -> str:
        """
        Generate a chat name from the first message using heuristics.

        Algorithm:
        1. Clean and sanitize the message
        2. Remove common greetings (Bonjour, Hello, Hi, etc.)
        3. Truncate at first sentence boundary
        4. Limit to max_length characters (break at word boundary)
        5. Capitalize first letter
        6. Fallback: "Chat DD/MM HH:MM" if empty
        """
        if not first_message or not first_message.strip():
            return f"Chat {datetime.now().strftime('%d/%m %H:%M')}"

        # Clean the message
        name = first_message.strip()

        # Remove common greetings at the start (multilingual)
        greetings = [
            "bonjour", "bonsoir", "salut", "hello", "hi", "hey",
            "guten tag", "hallo", "s'il vous plaît", "please",
            "j'aimerais", "je voudrais", "i would like", "i want to",
            "peux-tu", "pouvez-vous", "can you", "could you",
            "ich möchte", "können sie", "könntest du",
        ]
        name_lower = name.lower()
        for greeting in greetings:
            if name_lower.startswith(greeting):
                # Remove greeting and any following punctuation/space
                name = name[len(greeting):].lstrip(" ,.:!?")
                break

        # Truncate at sentence boundary if possible
        for delimiter in [". ", "? ", "! ", "\n"]:
            if delimiter in name:
                name = name.split(delimiter)[0]
                break

        # Truncate to max length
        if len(name) > max_length:
            # Try to break at word boundary
            name = name[:max_length]
            last_space = name.rfind(" ")
            if last_space > max_length * 0.6:  # Don't truncate too much
                name = name[:last_space]
            name = name.rstrip() + "..."

        # Capitalize first letter
        if name:
            name = name[0].upper() + name[1:] if len(name) > 1 else name.upper()

        # Fallback if empty after processing
        if not name or len(name) < 3:
            return f"Chat {datetime.now().strftime('%d/%m %H:%M')}"

        return name

    async def update_context(
        self,
        user_id: str,
        collection_name: str,
        system_prompt: str = None
        ) -> dict:
        """
        Met à jour le contexte de société pour le LLM.
        Appelle update_system_prompt de BaseAIAgent.
        
        Args:
            user_id: ID de l'utilisateur
            collection_name: ID de la société
            system_prompt: Prompt système personnalisé (optionnel)
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(f"Mise à jour contexte pour session: {base_session_key}")
            
            # Récupérer la session existante (lock MINIMAL)
            with self._lock:
                if base_session_key not in self.sessions:
                    return {
                        "success": False,
                        "error": "Session non trouvée",
                        "message": "Session LLM non initialisée"
                    }
                session = self.sessions[base_session_key]
            
            # Mettre à jour le prompt système via BaseAIAgent
            if session.agent:
                # Utiliser le system_prompt fourni ou créer un prompt par défaut
                if system_prompt:
                    new_system_prompt = system_prompt
                    logger.info(f"System prompt fourni: {system_prompt[:100]}...")
                else:
                    # Créer un nouveau prompt système basé sur le contexte
                    new_system_prompt = f"""
                    Contexte mis à jour pour l'utilisateur {user_id} dans la société {collection_name}.
                    Vous êtes maintenant configuré pour cette société spécifique.
                    """
                    logger.info(f"System prompt par défaut créé pour {collection_name}")
                
                # Appeler update_system_prompt de BaseAIAgent
                session.agent.update_system_prompt(new_system_prompt)
                
                logger.info(f"Contexte mis à jour pour session: {base_session_key}")
                
                return {
                    "success": True,
                    "message": "Contexte mis à jour avec succès",
                    "session_id": base_session_key
                }
            else:
                return {
                    "success": False,
                    "error": "Agent non initialisé",
                    "message": "Agent LLM non disponible"
                }
                
        except Exception as e:
            logger.error(f"Erreur update_context: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec de la mise à jour du contexte"
            }

    def _build_onboarding_intro_message(
        self,
        onboarding_data: Dict[str, Any],
        user_context: Optional[Dict[str, Any]],
        lpt_status: Optional[str],
        lpt_error: Optional[str] = None
        ) -> str:
        """Construit le message d'introduction envoyé à l'utilisateur."""

        company_name = (
            (onboarding_data.get("base_info") or {}).get("company_name")
            or onboarding_data.get("company_name")
            or (user_context or {}).get("company_name")
            or "votre entreprise"
        )

        if lpt_status == "queued":
            return (
                f"Bonjour ! Je suis Pinnokio, votre agent onboarding dédié à {company_name}. "
                "Je viens de lancer automatiquement l'analyse de vos données comptables. "
                "Je vous expliquerai chaque étape et vous alerterai dès qu'une action sera nécessaire. "
                "Pour commencer, pourriez-vous vous présenter brièvement et partager vos attentes principales pour cette intégration ?"
            )

        error_part = (
            f" Je n'ai toutefois pas pu démarrer automatiquement le processus (détail : {lpt_error})."
            if lpt_error else " Je n'ai toutefois pas pu démarrer automatiquement le processus."
        )

        return (
            f"Bonjour ! Je suis Pinnokio, votre agent onboarding dédié à {company_name}."
            + error_part +
            " Je reste à vos côtés pour relancer la procédure et vous guider pas à pas. "
            "Présentez-vous brièvement et dites-moi ce dont vous avez besoin en priorité pour que nous puissions avancer ensemble."
        )
    
    async def load_chat_history(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        history: list
        ) -> dict:
        """
        ⭐ NOUVELLE ARCHITECTURE: Charge l'historique = Créer/Ouvrir brain pour ce chat
        
        Cette méthode est appelée quand l'utilisateur change de chat.
        Elle crée un brain spécifique pour ce thread s'il n'existe pas, ou le réutilise.
        
        Workflow:
        1. Vérifier si brain existe déjà pour ce thread
        2. Si oui: Recharger l'historique (peut avoir changé)
        3. Si non: Créer nouveau brain avec agents + charger historique
        
        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société (space_code)
            thread_key: Clé du thread de chat (ex: "new_chat_1019eff4")
            history: Historique du chat au format [{"role": "user", "content": "..."}, ...]
            
        Returns:
            dict: {"success": bool, "status": "created"|"updated", "loaded_messages": int}
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(f"[LOAD_CHAT] 📚 Chargement chat pour session={base_session_key}, thread={thread_key}")
            logger.info(f"[LOAD_CHAT] Historique fourni: {len(history)} messages")
            
            # Récupérer la session existante
            with self._lock:
                if base_session_key not in self.sessions:
                    return {
                        "success": False,
                        "error": "Session non trouvée",
                        "message": "Session LLM non initialisée. Appelez initialize_session d'abord.",
                        "loaded_messages": 0
                    }
                session = self.sessions[base_session_key]
            
            # ⭐ TRACKER: Si user est déjà sur la page chat, c'est un changement de thread
            if session.is_on_chat_page:
                session.switch_thread(thread_key)
            
            # ═══════════════════════════════════════════════════════
            # CAS 1: Brain existe déjà pour ce thread
            # ═══════════════════════════════════════════════════════
            if thread_key in session.active_brains:
                brain = session.active_brains[thread_key]
                logger.info(f"[LOAD_CHAT] ♻️ Brain existant trouvé pour thread={thread_key}, rechargement historique...")
                
                # Recharger l'historique (peut avoir été mis à jour)
                brain.pinnokio_agent.load_chat_history(history=history)
                
                if self._is_onboarding_like(session.context.chat_mode):
                    # Charger les données selon le mode
                    if session.context.chat_mode == "onboarding_chat":
                        await brain.load_onboarding_data()
                    elif session.context.chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                        # Pour ces modes, le job_id est le thread_key
                        job_id = thread_key
                        await brain.load_job_data(job_id)
                    log_entries = await self._load_onboarding_log_history(
                        brain=brain,
                        collection_name=collection_name,
                        session=session,
                        thread_key=thread_key
                    )
                    await self._ensure_onboarding_listener(
                        session=session,
                        brain=brain,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        initial_entries=log_entries
                    )

                    # ═══ VÉRIFIER MODE INTERMÉDIATION AU CHARGEMENT ═══
                    # ⚠️ REMOVED: Cette vérification est déjà effectuée dans enter_chat() et start_onboarding_chat()
                    # pour éviter les appels en double qui causent l'envoi dupliqué des messages d'intermédiation.
                    # Le mode intermédiation est vérifié après la création complète du brain et du listener.

                session.last_activity[thread_key] = datetime.now(timezone.utc)
                
                logger.info(f"[LOAD_CHAT] ✅ Brain mis à jour: {len(history)} messages chargés")
                return {
                    "success": True,
                    "status": "updated",
                    "message": f"Brain existant mis à jour: {len(history)} messages",
                    "loaded_messages": len(history),
                    "thread_key": thread_key
                }
            
            # ═══════════════════════════════════════════════════════
            # CAS 2: Créer nouveau brain pour ce thread
            # ═══════════════════════════════════════════════════════
            logger.info(f"[LOAD_CHAT] 🆕 Création nouveau brain pour thread={thread_key}")
            
            # Créer lock pour ce thread
            if thread_key not in session._brain_locks:
                session._brain_locks[thread_key] = asyncio.Lock()
            
            async with session._brain_locks[thread_key]:
                # Double-check après le lock
                if thread_key in session.active_brains:
                    logger.info(f"[LOAD_CHAT] Brain créé par autre tâche, réutilisation")
                    return await self.load_chat_history(user_id, collection_name, thread_key, history)
                
                # ═══ Créer le brain ═══
                from ..pinnokio_agentic_workflow.orchestrator.pinnokio_brain import PinnokioBrain
                
                brain = PinnokioBrain(
                    collection_name=collection_name,
                    firebase_user_id=user_id,
                    dms_system=session.context.dms_system,
                    dms_mode=session.context.dms_mode
                )
                
                logger.info(f"[LOAD_CHAT] 🤖 Création agents du brain...")
                
                # ═══ Créer les agents du brain ═══
                await brain.initialize_agents()  # ← Méthode à créer dans PinnokioBrain
                
                logger.info(f"[LOAD_CHAT] ✅ Agents créés (principal + outils)")
                
                # ═══ Injecter données permanentes ═══
                brain.user_context = session.user_context  # Référence partagée
                brain.jobs_data = session.jobs_data
                brain.jobs_metrics = session.jobs_metrics
                
                # 🔍 DEBUG : Vérifier workflow_params dans session.user_context
                if session.user_context:
                    workflow_params = session.user_context.get("workflow_params", {})
                    logger.info(f"[LOAD_CHAT] 🔍 DEBUG session.user_context.workflow_params existe: {workflow_params is not None and workflow_params != {}}")
                    logger.info(f"[LOAD_CHAT] 🔍 DEBUG session.user_context.workflow_params clés: {list(workflow_params.keys()) if workflow_params else 'VIDE'}")
                    if workflow_params and "Apbookeeper_param" in workflow_params:
                        logger.info(f"[LOAD_CHAT] 🔍 DEBUG Apbookeeper_param dans session: {workflow_params['Apbookeeper_param']}")
                else:
                    logger.warning(f"[LOAD_CHAT] ⚠️ session.user_context est None !")
                
                # 🔍 DEBUG : Vérifier workflow_params dans brain.user_context après injection
                if brain.user_context:
                    brain_workflow_params = brain.user_context.get("workflow_params", {})
                    logger.info(f"[LOAD_CHAT] 🔍 DEBUG brain.user_context.workflow_params existe: {brain_workflow_params is not None and brain_workflow_params != {}}")
                    logger.info(f"[LOAD_CHAT] 🔍 DEBUG brain.user_context.workflow_params clés: {list(brain_workflow_params.keys()) if brain_workflow_params else 'VIDE'}")
                else:
                    logger.warning(f"[LOAD_CHAT] ⚠️ brain.user_context est None après injection !")
                
                # Charger les données spécifiques selon le mode
                if session.context.chat_mode == "onboarding_chat":
                    await brain.load_onboarding_data()
                elif session.context.chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                    # Pour ces modes, le job_id est le thread_key
                    job_id = thread_key
                    await brain.load_job_data(job_id)

                # 🔍 LOGS DE DIAGNOSTIC - Vérifier données injectées au brain
                logger.info(f"[LOAD_CHAT] 📊 Données permanentes injectées")
                logger.info(f"[LOAD_CHAT] 🔍 DIAGNOSTIC brain.jobs_data - Clés: {list(brain.jobs_data.keys()) if brain.jobs_data else 'None'}")
                if brain.jobs_data and 'ROUTER' in brain.jobs_data:
                    router_count = len(brain.jobs_data['ROUTER'].get('unprocessed', []))
                    logger.info(f"[LOAD_CHAT] 🔍 DIAGNOSTIC brain ROUTER - {router_count} documents unprocessed injectés")
                else:
                    logger.warning(f"[LOAD_CHAT] ⚠️ DIAGNOSTIC brain - Pas de données ROUTER dans jobs_data !")
                
                logger.info(f"[LOAD_CHAT] 🔍 DIAGNOSTIC brain.jobs_metrics - "
                           f"ROUTER.to_process: {brain.jobs_metrics.get('ROUTER', {}).get('to_process', 'N/A') if brain.jobs_metrics else 'None'}")
                
                # ═══ Initialiser system prompt ═══
                brain.initialize_system_prompt(
                    chat_mode=session.context.chat_mode,
                    jobs_metrics=session.jobs_metrics
                )
                
                logger.info(f"[LOAD_CHAT] 📝 System prompt initialisé")
                
                # ═══ Charger historique ═══
                brain.pinnokio_agent.load_chat_history(history=history)

                if self._is_onboarding_like(session.context.chat_mode):
                    log_entries = await self._load_onboarding_log_history(
                        brain=brain,
                        collection_name=collection_name,
                        session=session,
                        thread_key=thread_key
                    )
                    await self._ensure_onboarding_listener(
                        session=session,
                        brain=brain,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        initial_entries=log_entries
                    )

                    # ═══ VÉRIFIER MODE INTERMÉDIATION AU CHARGEMENT ═══
                    # ⭐ Forcer le rechargement du status depuis Firestore (éviter stale data)
                    job_id = thread_key
                    await brain.load_job_data(job_id, force_reload=True)
                    job_status = brain.job_data.get("status") if brain.job_data else None
                    await self._check_intermediation_on_load(
                        session=session,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        job_status=job_status
                    )

                logger.info(f"[LOAD_CHAT] 💾 Historique chargé: {len(history)} messages")
                
                # ═══ Enregistrer le brain ═══
                session.active_brains[thread_key] = brain
                session.last_activity[thread_key] = datetime.now(timezone.utc)
                
                logger.info(f"[LOAD_CHAT] 🎉 Brain créé et prêt pour thread={thread_key}")
                
                return {
                    "success": True,
                    "status": "created",
                    "message": f"Nouveau brain créé: {len(history)} messages chargés",
                    "loaded_messages": len(history),
                    "thread_key": thread_key,
                    "active_brains_count": len(session.active_brains)
                }
                
        except Exception as e:
            logger.error(f"[LOAD_CHAT] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Échec du chargement: {str(e)}",
                "loaded_messages": 0
            }
    
    async def flush_chat_history(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str = None
        ) -> dict:
        """
        ⭐ NOUVELLE ARCHITECTURE: Flush = Fermer brain(s) et nettoyer
        
        Vide l'historique = Ferme le brain du thread et nettoie les ressources.
        ⚠️ IMPORTANT: Cette opération est NON-BLOQUANTE et rapide.
        Les tâches LPT en cours continuent en arrière-plan de manière indépendante.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société (space_code)
            thread_key: Thread spécifique à fermer (optionnel)
                       - Si fourni: ferme uniquement ce thread
                       - Si None: ferme tous les threads de la session
            
        Returns:
            dict: {"success": bool, "message": str, "threads_cleared": int}
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(f"[FLUSH_CHAT] 🗑️ Demande fermeture pour session={base_session_key}, thread={thread_key or 'TOUS'}")
            
            # Récupérer la session existante
            with self._lock:
                if base_session_key not in self.sessions:
                    return {
                        "success": False,
                        "error": "Session non trouvée",
                        "message": "Session LLM non initialisée."
                    }
                session = self.sessions[base_session_key]
            
            threads_cleared = 0
            
            if thread_key:
                # ═══════════════════════════════════════════════════════
                # FERMER UN SEUL THREAD
                # ═══════════════════════════════════════════════════════
                if thread_key in session.active_brains:
                    brain = session.active_brains[thread_key]
                    
                    logger.info(f"[FLUSH_CHAT] 🔒 Fermeture brain pour thread={thread_key}")
                    
                    # ⚠️ Note: Les tâches LPT continuent en arrière-plan de manière indépendante
                    # On ne les attend pas car flush_chat_history doit être rapide et non-bloquant
                    if brain.has_active_lpt_tasks(thread_key):
                        logger.info(f"[FLUSH_CHAT] ⚠️ Tâches LPT actives détectées - elles continueront en arrière-plan (fermeture non-bloquante)")
                    
                    # Nettoyer le brain
                    try:
                        brain.pinnokio_agent.clear_chat_history()
                        logger.info(f"[FLUSH_CHAT] 🧹 Historique brain vidé")
                    except Exception as e:
                        logger.warning(f"[FLUSH_CHAT] Erreur nettoyage brain: {e}")
                    
                    # Supprimer le brain
                    del session.active_brains[thread_key]
                    if thread_key in session._brain_locks:
                        del session._brain_locks[thread_key]

                    self._stop_onboarding_listener(session, thread_key)
                    
                    # ═══ NETTOYER TOUS LES ÉTATS DU THREAD ═══
                    # Supprimer le mode d'intermédiation
                    if thread_key in session.intermediation_mode:
                        del session.intermediation_mode[thread_key]
                    
                    # Supprimer les IDs traités
                    if thread_key in session.onboarding_processed_ids:
                        del session.onboarding_processed_ids[thread_key]
                    
                    # Supprimer l'activité
                    if thread_key in session.last_activity:
                        del session.last_activity[thread_key]
                    
                    # Supprimer l'état du thread
                    if thread_key in session.thread_states:
                        del session.thread_states[thread_key]
                    
                    # Supprimer le cache de contexte
                    if thread_key in session.thread_contexts:
                        del session.thread_contexts[thread_key]
                    
                    threads_cleared = 1
                    
                    logger.info(f"[FLUSH_CHAT] ✅ Brain fermé et états nettoyés pour thread={thread_key}")
                    
                    return {
                        "success": True,
                        "message": f"Brain fermé avec succès pour thread {thread_key}",
                        "session_id": base_session_key,
                        "thread_key": thread_key,
                        "threads_cleared": threads_cleared,
                        "active_brains_remaining": len(session.active_brains)
                    }
                else:
                    logger.warning(f"[FLUSH_CHAT] ⚠️ Aucun brain actif trouvé pour thread={thread_key}")
                    return {
                        "success": False,
                        "error": "Thread non trouvé",
                        "message": f"Aucun brain actif pour thread {thread_key}",
                        "threads_cleared": 0
                    }
            else:
                # ═══════════════════════════════════════════════════════
                # FERMER TOUS LES THREADS
                # ═══════════════════════════════════════════════════════
                threads_count = len(session.active_brains)
                
                logger.info(f"[FLUSH_CHAT] 🔒 Fermeture de {threads_count} brains...")
                
                # Fermer tous les brains
                for t_key, brain in list(session.active_brains.items()):
                    try:
                        brain.pinnokio_agent.clear_chat_history()
                        logger.info(f"[FLUSH_CHAT] 🧹 Brain thread={t_key} nettoyé")
                    except Exception as e:
                        logger.warning(f"[FLUSH_CHAT] Erreur nettoyage brain {t_key}: {e}")
                
                # Tout vider
                session.active_brains.clear()
                session._brain_locks.clear()
                session.last_activity.clear()
                
                # ═══ NETTOYER TOUS LES ÉTATS DE TOUS LES THREADS ═══
                session.intermediation_mode.clear()
                session.onboarding_processed_ids.clear()
                session.thread_states.clear()
                session.thread_contexts.clear()

                self._stop_onboarding_listener(session)
                
                logger.info(f"[FLUSH_CHAT] ✅ Tous les brains fermés et états nettoyés ({threads_count})")
                
                return {
                    "success": True,
                    "message": f"Tous les brains fermés avec succès",
                    "session_id": base_session_key,
                    "threads_cleared": threads_count
                }
                
        except Exception as e:
            logger.error(f"[FLUSH_CHAT] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Échec fermeture: {str(e)}",
                "threads_cleared": 0
            }
    
    async def stop_streaming(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str = None
        ) -> dict:
        """
        Arrête le streaming via WebSocket pour un thread spécifique ou tous les threads.
        
        Args:
            user_id: ID de l'utilisateur
            collection_name: ID de la société
            thread_key: Thread spécifique (optionnel, arrête tous si omis)
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(
                f"[STOP_STREAMING] 🛑 Demande reçue - "
                f"session={base_session_key}, thread={thread_key or 'ALL'}"
            )
            
            # Debug: Afficher les streams actifs avant l'arrêt
            active_streams = await self.streaming_controller.get_active_streams(base_session_key)
            logger.info(
                f"[STOP_STREAMING] 📊 Streams actifs pour cette session: "
                f"{list(active_streams.keys()) if active_streams else 'AUCUN'}"
            )
            
            if thread_key:
                # Arrêter un thread spécifique
                logger.info(f"[STOP_STREAMING] 🎯 Tentative d'arrêt du thread: {thread_key}")
                success = await self.streaming_controller.stop_stream(base_session_key, thread_key)
                
                if success:
                    logger.info(
                        f"[STOP_STREAMING] ✅ Stream arrêté avec succès - "
                        f"thread={thread_key}"
                    )
                    return {
                        "success": True,
                        "message": f"Stream arrêté pour thread {thread_key}",
                        "thread_key": thread_key
                    }
                else:
                    # Stream introuvable - peut être déjà terminé et désenregistré
                    # Retourner un succès avec message informatif plutôt qu'une erreur
                    logger.info(
                        f"[STOP_STREAMING] ℹ️ Thread non trouvé (probablement déjà terminé) - "
                        f"thread={thread_key}, active_streams={list(active_streams.keys())}"
                    )
                    return {
                        "success": True,
                        "message": f"Thread {thread_key} non trouvé (probablement déjà terminé)",
                        "thread_key": thread_key,
                        "already_completed": True
                    }
            else:
                # Arrêter tous les threads de la session
                logger.info(f"[STOP_STREAMING] 🌐 Arrêt de TOUS les streams de la session")
                stopped_count = await self.streaming_controller.stop_all_streams(base_session_key)
                
                logger.info(
                    f"[STOP_STREAMING] ✅ Tous les streams arrêtés - "
                    f"count={stopped_count}"
                )
                return {
                    "success": True,
                    "message": f"Tous les streams arrêtés ({stopped_count} threads)",
                    "stopped_count": stopped_count
                }
                
        except Exception as e:
            logger.error(f"[STOP_STREAMING] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec de l'arrêt du streaming"
            }
    
    async def enter_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        chat_mode: str = "general_chat",
        job_status: Optional[str] = None,
        session_id: Optional[str] = None
        ) -> dict:
        """
        ⭐ NOUVEAU: Notifie que l'utilisateur ENTRE sur un thread de chat.
        Appelé par Reflex via RPC quand user ouvre/entre sur un thread.

        Permet de capturer la présence AVANT l'envoi du premier message,
        ce qui active le mode UI pour le streaming et les notifications temps réel.

        ⭐ Multi-Onglet: Si session_id fourni, permet à plusieurs onglets
        d'avoir différents threads ouverts simultanément sans conflit.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société
            thread_key: Thread sur lequel l'utilisateur entre
            chat_mode: Mode de chat (default: "general_chat")
            job_status: Statut du job (optionnel) - "running", "in queue", "completed", etc.
            session_id: ID unique de l'onglet WebSocket (pour support multi-onglet)

        Returns:
            dict: {"success": bool, "message": str, "thread_key": str}
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(
                f"[ENTER_CHAT] 📥 Signal reçu - "
                f"session={base_session_key}, thread_key={thread_key}, tab_session={session_id or 'legacy'}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : GARANTIR INITIALISATION SESSION
            # ═══════════════════════════════════════════════════════════
            session = await self._ensure_session_initialized(
                user_id=user_id,
                collection_name=collection_name,
                chat_mode=chat_mode
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : MARQUER PRÉSENCE SUR LE THREAD (Multi-onglet)
            # ═══════════════════════════════════════════════════════════
            # Si user est déjà sur la page chat, c'est un changement de thread
            if session.is_on_chat_page and session.current_active_thread != thread_key:
                session.switch_thread(thread_key, session_id=session_id)
            else:
                # Première entrée sur la page chat
                session.enter_chat(thread_key, session_id=session_id)
            
            logger.info(
                f"[ENTER_CHAT] ✅ User {user_id} marqué comme PRÉSENT sur chat - "
                f"thread_key={thread_key}, is_on_chat_page={session.is_on_chat_page}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : CRÉER LE BRAIN POUR CE THREAD (pré-chargement)
            # ═══════════════════════════════════════════════════════════
            if thread_key not in session.active_brains:
                logger.info(
                    f"[ENTER_CHAT] 🧠 Brain non trouvé pour thread={thread_key}, "
                    f"création et chargement historique..."
                )
                
                # Charger historique depuis RTDB
                history = await self._load_history_from_rtdb(collection_name, thread_key, session.context.chat_mode)
                
                # Créer brain pour ce thread
                load_result = await self.load_chat_history(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    history=history
                )
                
                if not load_result.get("success"):
                    logger.error(f"[ENTER_CHAT] ❌ Échec création brain: {load_result}")
                    return {
                        "success": False,
                        "error": "Brain creation failed",
                        "message": f"Impossible de créer le brain pour thread={thread_key}",
                        "details": load_result
                    }
                
                logger.info(f"[ENTER_CHAT] ✅ Brain créé et historique chargé")
            else:
                logger.info(f"[ENTER_CHAT] ✅ Brain existant trouvé")

            if self._is_onboarding_like(session.context.chat_mode):
                brain = session.active_brains.get(thread_key)
                if brain:
                    # ═══════════════════════════════════════════════════════════
                    # ENTER_CHAT : Juste initialiser le brain et charger l'historique
                    # PAS de lancement LPT (réservé à start_onboarding_chat)
                    # ═══════════════════════════════════════════════════════════
                    # Charger les données selon le mode
                    if session.context.chat_mode == "onboarding_chat":
                        await brain.load_onboarding_data()
                    elif session.context.chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                        # Pour ces modes, le job_id est le thread_key
                        job_id = thread_key
                        await brain.load_job_data(job_id)
                    
                    # Charger l'historique des logs (pour injection dans contexte LLM)
                    log_entries = await self._load_onboarding_log_history(
                        brain=brain,
                        collection_name=collection_name,
                        session=session,
                        thread_key=thread_key
                    )
                    
                    # Démarrer l'écoute RTDB (pour suivre les logs métier)
                    await self._ensure_onboarding_listener(
                        session=session,
                        brain=brain,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        initial_entries=log_entries
                    )
                    
                    # ⭐ Note : _check_intermediation_on_load() est déjà appelé dans _load_chat()
                    # Pas besoin de le rappeler ici pour éviter les doublons
                    
                    logger.info(
                        f"[ENTER_CHAT] ✅ Brain initialisé pour mode onboarding-like - "
                        f"thread={thread_key}, job_id={brain.onboarding_data.get('job_id') if brain.onboarding_data else None}"
                    )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : VÉRIFIER SI WORKFLOW ACTIF ET BASCULER EN MODE UI
            # ═══════════════════════════════════════════════════════════
            from .workflow_state_manager import get_workflow_state_manager
            from ..ws_hub import hub
            
            workflow_manager = get_workflow_state_manager()
            workflow_switch = workflow_manager.user_entered(user_id, collection_name, thread_key)
            
            workflow_active = workflow_switch.get("workflow_active", False)
            
            # ⭐ IMPORTANT: Si thread task_* mais workflow NON actif → basculer vers general_chat
            # Cela permet une conversation normale sur un thread de tâche terminée
            if thread_key.startswith("task_") and not workflow_active:
                if session.context.chat_mode == "task_execution":
                    logger.info(
                        f"[ENTER_CHAT] 🔄 Thread task_* sans workflow actif - "
                        f"Basculement chat_mode: task_execution → general_chat"
                    )
                    session.update_context(chat_mode="general_chat")
                    
                    # Mettre à jour le brain si nécessaire
                    if thread_key in session.active_brains:
                        brain = session.active_brains[thread_key]
                        brain.initialize_system_prompt(chat_mode="general_chat")
                        logger.info(f"[ENTER_CHAT] ✅ Brain mis à jour avec chat_mode=general_chat")
            
            if workflow_switch.get("changed"):
                logger.info(
                    f"[ENTER_CHAT] 🔄 Bascule workflow BACKEND → UI pour thread={thread_key}"
                )
                
                # Envoyer signal au frontend
                ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
                await hub.broadcast(user_id, {
                    "type": "WORKFLOW_USER_JOINED",
                    "channel": ws_channel,
                    "payload": {
                        "thread_key": thread_key,
                        "workflow_active": True,
                        "workflow_paused": workflow_switch.get("workflow_paused", False),
                        "message": "Vous avez rejoint un workflow en cours. Vous pouvez interagir."
                    }
                })

            # NOTE: Le catch-up billing est déclenché au niveau session dans _ensure_session_initialized()
            
            return {
                "success": True,
                "message": "User marked as entered chat, brain ready",
                "thread_key": thread_key,
                "is_on_chat_page": session.is_on_chat_page,
                "current_active_thread": session.current_active_thread,
                "session_key": base_session_key,
                "brain_ready": True,
                "workflow_active": workflow_active,
                "workflow_switch": workflow_switch
            }
            
        except Exception as e:
            logger.error(f"[ENTER_CHAT] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec du traitement enter_chat"
            }
    
    async def leave_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str = None
        ) -> dict:
        """
        Notifie que l'utilisateur quitte la page chat.
        Appelé par Reflex via RPC quand user ferme l'onglet ou change de module.
        
        ⭐ IMPORTANT: thread_key n'est pas utilisé car on veut juste marquer
        que l'utilisateur n'est plus sur la page (indépendamment du thread).
        
        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société
            thread_key: Thread actuel (optionnel, non utilisé)
            
        Returns:
            dict: {"success": bool, "message": str, "was_on_thread": str}
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(
                f"[LEAVE_CHAT] 📥 Signal reçu - "
                f"session={base_session_key}, thread_key={thread_key}"
            )
            
            # Vérifier si session existe
            with self._lock:
                if base_session_key not in self.sessions:
                    logger.warning(
                        f"[LEAVE_CHAT] ⚠️ Session non trouvée: {base_session_key}"
                    )
                    return {
                        "success": False,
                        "error": "Session not found",
                        "message": "Session LLM non trouvée (peut-être déjà fermée)"
                    }
                
                session = self.sessions[base_session_key]
            
            # Sauvegarder l'état avant modification (pour log)
            was_on_chat_page = session.is_on_chat_page
            was_on_thread = session.current_active_thread
            
            # Marquer user comme absent de la page chat
            session.leave_chat()
            
            logger.info(
                f"[LEAVE_CHAT] ✅ User {user_id} marqué comme HORS chat - "
                f"was_on_chat_page={was_on_chat_page}, "
                f"was_on_thread={was_on_thread}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # VÉRIFIER SI WORKFLOW ACTIF ET REPRENDRE SI PAUSÉ
            # ═══════════════════════════════════════════════════════════
            workflow_resumed = False
            if was_on_thread:
                from .workflow_state_manager import get_workflow_state_manager
                workflow_manager = get_workflow_state_manager()
                
                leave_result = workflow_manager.user_left(user_id, collection_name, was_on_thread)
                
                if leave_result.get("needs_resume"):
                    logger.info(
                        f"[LEAVE_CHAT] 🔄 Workflow pausé détecté - "
                        f"marqué pour reprise automatique thread={was_on_thread}"
                    )
                    workflow_resumed = True
                    # Note: Le workflow verra "user_left" comme pause_reason 
                    # au prochain check dans la boucle et reprendra
            
            return {
                "success": True,
                "message": "User marked as left chat",
                "was_on_chat_page": was_on_chat_page,
                "was_on_thread": was_on_thread,
                "session_key": base_session_key,
                "workflow_resumed": workflow_resumed
            }
            
        except Exception as e:
            logger.error(f"[LEAVE_CHAT] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec du traitement leave_chat"
            }
    
    
    # ═══════════════════════════════════════════════════════════════
    # PINNOKIO AGENTIC WORKFLOW
    # ═══════════════════════════════════════════════════════════════
    
    
    # ═══════════════════════════════════════════════════════════════
    # EXÉCUTION DES TÂCHES PLANIFIÉES
    # ═══════════════════════════════════════════════════════════════

    async def _execute_scheduled_task(
        self,
        user_id: str,
        company_id: str,
        task_data: dict,
        thread_key: str,
        execution_id: str
        ):
        """
        Exécute une tâche planifiée.

        Workflow:
            1. Initialiser session/brain (comme send_message)
            2. Charger le mission_plan
            3. Construire system prompt spécifique tâche
            4. Exécuter le workflow avec l'agent
            5. L'agent créera la checklist via CREATE_CHECKLIST
            6. L'agent mettra à jour les étapes via UPDATE_STEP
            7. Gérer les LPT (attente callback)
            8. Finaliser l'exécution via TERMINATE_TASK
        """
        t0 = time.time()
        task_id = task_data["task_id"]
        mission = task_data["mission"]
        mandate_path = task_data["mandate_path"]

        logger.info(
            f"[TASK_EXEC] Début: task_id={task_id}, thread={thread_key}, "
            f"execution_id={execution_id}"
        )

        try:
            # 1. Initialiser la session
            session = await self._ensure_session_initialized(
                user_id=user_id,
                collection_name=company_id,
                chat_mode="task_execution"
            )

            # 2. Récupérer ou créer le brain pour ce thread (avec historique)
            if thread_key not in session.active_brains:
                logger.info(
                    f"[TASK_EXEC] Création brain pour thread de tâche: {thread_key}"
                )

                # ⭐ CHARGER L'HISTORIQUE du chat (continuité entre exécutions)
                logger.info(f"[TASK_EXEC] 📚 Chargement historique du chat...")
                history = await self._load_history_from_rtdb(
                    collection_name=company_id, 
                    thread_key=thread_key, 
                    chat_mode="task_execution"
                )
                
                logger.info(
                    f"[TASK_EXEC] 📚 Historique chargé: {len(history)} message(s) - "
                    f"L'agent aura le contexte des exécutions précédentes"
                )

                # Créer brain avec historique (comme pour enter_chat)
                load_result = await self.load_chat_history(
                    user_id=user_id,
                    collection_name=company_id,
                    thread_key=thread_key,
                    history=history
                )

                if not load_result.get("success"):
                    raise Exception(f"Échec création brain avec historique: {load_result}")

                logger.info(f"[TASK_EXEC] 🎉 Brain créé et historique chargé - Mémoire des exécutions précédentes disponible")

            brain = session.active_brains.get(thread_key)

            if not brain:
                raise Exception(f"Brain non trouvé pour thread: {thread_key}")

            logger.info(f"[TASK_EXEC] Brain actif récupéré pour thread: {thread_key}")

            # 4. Stocker les infos de la tâche dans le brain
            brain.active_task_data = {
                "task_id": task_id,
                "execution_id": execution_id,
                "mission": mission,
                "mandate_path": mandate_path,
                "execution_plan": task_data.get("execution_plan"),
                "last_execution_report": task_data.get("last_execution_report")
            }

            # 5. Construire le system prompt spécifique (extension du prompt principal)
            task_specific_addition = self._build_task_execution_addition(
                mission=mission,
                last_report=task_data.get("last_execution_report"),
                execution_plan=task_data.get("execution_plan")
            )

            # Combiner avec le system prompt principal
            base_prompt = brain.pinnokio_agent.system_prompt if brain.pinnokio_agent else ""
            task_specific_prompt = f"{base_prompt}\n\n{task_specific_addition}"

            # 5. Construire le message initial
            # Mapping textuel pour le message initial
            mode_mapping = {
                "ON_DEMAND": "Action manuelle de l'utilisateur",
                "SCHEDULED": "Exécution récurrente planifiée",
                "ONE_TIME": "Exécution unique programmée",
                "NOW": "Exécution immédiate"
            }
            mode_text = mode_mapping.get(task_data.get("execution_plan"), task_data.get("execution_plan", "Mode automatique"))

            initial_message = f"""🎯 **Exécution Automatique de Tâche**

                **Titre** : {mission['title']}
                **Description** : {mission['description']}
                **Mode d'exécution** : {mode_text}

                **Plan d'Action** :
                {mission['plan']}

                **Instructions** :
                1. Créer la workflow checklist avec CREATE_CHECKLIST
                2. Exécuter le plan d'action étape par étape
                3. Mettre à jour chaque étape avec UPDATE_STEP
                4. Finaliser avec TERMINATE_TASK

                Commence maintenant l'exécution."""

            # 6. Déterminer mode (UI/BACKEND) - Vérifier si user est sur ce thread spécifique
            # ⭐ CORRECTION : Utiliser is_user_on_specific_thread comme dans le callback LPT
            # Logique:
            # - is_on_chat_page = False → Mode BACKEND (user pas sur la page)
            # - is_on_chat_page = True + current_active_thread = thread_key → Mode UI
            # - is_on_chat_page = True + current_active_thread ≠ thread_key → Mode BACKEND
            user_on_active_chat = session.is_user_on_specific_thread(thread_key)

            mode = "UI" if user_on_active_chat else "BACKEND"

            logger.info(
                f"[TASK_EXEC] Démarrage workflow - mode={mode} "
                f"user_on_active_chat={user_on_active_chat} is_on_chat_page={session.is_on_chat_page} "
                f"current_active_thread={session.current_active_thread} thread={thread_key}"
            )

            # 7. Préparer assistant_message_id
            assistant_message_id = f"task_{execution_id}"
            assistant_timestamp = datetime.now(timezone.utc).isoformat()

            # ═══════════════════════════════════════════════════════════
            # 7.5 INITIALISER L'ÉTAT WORKFLOW DANS REDIS
            # ═══════════════════════════════════════════════════════════
            # Permet le basculement dynamique UI ↔ BACKEND si l'utilisateur
            # entre/quitte le thread pendant l'exécution
            from .workflow_state_manager import get_workflow_state_manager
            workflow_manager = get_workflow_state_manager()
            workflow_manager.start_workflow(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                initial_mode=mode
            )
            
            logger.info(
                f"[TASK_EXEC] 🚀 Workflow state initialisé dans Redis - "
                f"mode={mode}, thread={thread_key}"
            )

            # 8. Exécuter le workflow
            try:
                await self._process_unified_workflow(
                    session=session,
                    user_id=user_id,
                    collection_name=company_id,
                    thread_key=thread_key,
                    message=initial_message,
                    assistant_message_id=assistant_message_id,
                    assistant_timestamp=assistant_timestamp,
                    enable_streaming=user_on_active_chat,  # ← Streaming seulement si user sur ce thread
                    chat_mode="task_execution",
                    system_prompt=task_specific_prompt
                )
            finally:
                # ═══════════════════════════════════════════════════════════
                # NETTOYER L'ÉTAT WORKFLOW DANS REDIS
                # ═══════════════════════════════════════════════════════════
                workflow_manager.end_workflow(
                    user_id=user_id,
                    company_id=company_id,
                    thread_key=thread_key,
                    status="completed"
                )
                logger.info(f"[TASK_EXEC] 🏁 Workflow state nettoyé dans Redis")

            dt_ms = int((time.time() - t0) * 1000)
            logger.info(f"[TASK_EXEC] Terminé: task_id={task_id}, dt_ms={dt_ms}")

        except Exception as e:
            dt_ms = int((time.time() - t0) * 1000)
            logger.error(
                f"[TASK_EXEC] Erreur: task_id={task_id}, error={repr(e)}",
                exc_info=True
            )

            # ═══════════════════════════════════════════════════════════
            # NETTOYER L'ÉTAT WORKFLOW EN CAS D'ERREUR
            # ═══════════════════════════════════════════════════════════
            try:
                from .workflow_state_manager import get_workflow_state_manager
                workflow_manager = get_workflow_state_manager()
                workflow_manager.end_workflow(
                    user_id=user_id,
                    company_id=company_id,
                    thread_key=thread_key,
                    status="error"
                )
                logger.info(f"[TASK_EXEC] 🏁 Workflow state nettoyé (erreur) dans Redis")
            except:
                pass

            # Marquer l'exécution comme échouée
            try:
                from ..firebase_providers import get_firebase_management
                fbm = get_firebase_management()

                # Créer rapport d'échec
                error_report = {
                    "execution_id": execution_id,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": int(time.time() - t0),
                    "status": "failed",
                    "summary": f"Erreur d'exécution: {str(e)}",
                    "errors": [str(e)]
                }

                # Finaliser l'exécution
                fbm.complete_task_execution(
                    mandate_path, task_id, execution_id, error_report
                )
            except:
                pass

    async def execute_task_now(
        self,
        mandate_path: str,
        task_id: str,
        user_id: str,
        company_id: str
    ) -> dict:
        """
        Exécute une tâche immédiatement (déclenchée manuellement depuis le frontend).
        
        Cette méthode réplique la logique du CRON mais est appelée à la demande.
        Elle est exposée via RPC: LLM.execute_task_now
        
        Args:
            mandate_path: Chemin du mandat Firebase
            task_id: ID de la tâche à exécuter
            user_id: ID de l'utilisateur Firebase
            company_id: ID de la société (collection_name)
            
        Returns:
            dict: {"success": True, "execution_id": "...", "thread_key": "..."} ou {"success": False, "error": "..."}
        """
        import asyncio
        
        try:
            logger.info(f"[EXECUTE_NOW] 🚀 Exécution immédiate: task_id={task_id}, user_id={user_id}, company_id={company_id}")
            
            # 1. Récupérer les données de la tâche depuis Firebase
            from ..firebase_providers import get_firebase_management, get_firebase_realtime
            fbm = get_firebase_management()
            
            task_data = fbm.get_task(mandate_path, task_id)
            if not task_data:
                logger.error(f"[EXECUTE_NOW] ❌ Tâche non trouvée: {task_id}")
                return {
                    "success": False,
                    "error": f"Tâche non trouvée: {task_id}"
                }
            
            # Enrichir task_data avec les infos nécessaires
            task_data["task_id"] = task_id
            task_data["user_id"] = user_id
            task_data["company_id"] = company_id
            task_data["mandate_path"] = mandate_path
            
            logger.info(f"[EXECUTE_NOW] ✅ Tâche récupérée: {task_data.get('mission', {}).get('title', task_id)}")
            
            # 2. Générer IDs
            triggered_at = datetime.now(timezone.utc)
            execution_id = f"exec_{uuid.uuid4().hex[:12]}"
            
            # ⭐ Utiliser task_id comme thread_key (chat persistant)
            thread_key = task_id
            logger.info(f"[EXECUTE_NOW] 📝 Utilisation du thread_key persistant: {thread_key}")
            
            # 3. Créer document d'exécution dans Firebase
            execution_data = {
                "execution_id": execution_id,
                "task_id": task_id,
                "thread_key": thread_key,
                "status": "running",
                "started_at": triggered_at.isoformat(),
                "triggered_by": "manual",  # Distinguer des exécutions CRON
                "workflow_checklist": None,
                "lpt_tasks": {}
            }
            
            fbm.create_task_execution(mandate_path, task_id, execution_data)
            logger.info(f"[EXECUTE_NOW] 📄 Exécution créée: {execution_id}")
            
            # 4. Vérifier et créer chat RTDB SEULEMENT s'il n'existe pas
            rtdb = get_firebase_realtime()
            
            chat_path = f"{company_id}/chats/{thread_key}"
            existing_chat = rtdb.db.child(chat_path).get()
            
            if existing_chat:
                logger.info(f"[EXECUTE_NOW] ✅ Chat existant trouvé: {thread_key} - Réutilisation avec historique")
            else:
                logger.info(f"[EXECUTE_NOW] 🆕 Création nouveau chat: {thread_key}")
                mission_title = task_data.get("mission", {}).get("title", "Tâche manuelle")
                
                chat_result = rtdb.create_chat(
                    user_id=user_id,
                    space_code=company_id,
                    thread_name=mission_title,
                    mode="chats",
                    chat_mode="task_execution",
                    thread_key=thread_key
                )
                
                if not chat_result.get("success"):
                    logger.error(f"[EXECUTE_NOW] ❌ Échec création chat: {chat_result}")
                    return {
                        "success": False,
                        "error": f"Échec création chat: {chat_result.get('error', 'Unknown')}"
                    }
            
            # 5. Lancer l'exécution en background (comme le CRON)
            asyncio.create_task(
                self._execute_scheduled_task(
                    user_id=user_id,
                    company_id=company_id,
                    task_data=task_data,
                    thread_key=thread_key,
                    execution_id=execution_id
                )
            )
            
            logger.info(f"[EXECUTE_NOW] ✅ Tâche lancée: {task_id} | Thread: {thread_key} | Execution: {execution_id}")
            
            return {
                "success": True,
                "execution_id": execution_id,
                "thread_key": thread_key,
                "task_title": task_data.get("mission", {}).get("title", task_id)
            }
            
        except Exception as e:
            logger.error(f"[EXECUTE_NOW] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    def _build_task_execution_addition(self, mission: dict, last_report: Optional[dict], execution_plan: str = None) -> str:
        """
        Construit l'ADDITION au system prompt principal pour l'exécution d'une tâche.
        Cette section s'ajoute au prompt principal existant.

        Args:
            mission: Dictionnaire de la mission
            last_report: Rapport de la dernière exécution (optionnel)
            execution_plan: Mode d'exécution (ON_DEMAND, SCHEDULED, NOW, etc.)
        """

        # Mapping textuel des modes d'exécution
        mode_mapping = {
            "ON_DEMAND": "Cette tâche est paramétrée pour être effectuée par une action manuelle de l'utilisateur",
            "SCHEDULED": "Cette tâche a une récurrence planifiée et s'exécute automatiquement selon le calendrier défini",
            "ONE_TIME": "Cette tâche est programmée pour s'exécuter une seule fois à une date et heure précise",
            "NOW": "Cette tâche doit être exécutée immédiatement sans attendre de planification"
        }

        mode_description = mode_mapping.get(execution_plan, f"Mode d'exécution: {execution_plan}")

        prompt = f"""
            ═══════════════════════════════════════════════════════════════════════════════
            🎯 MODE EXÉCUTION AUTOMATIQUE DE TÂCHE
            ═══════════════════════════════════════════════════════════════════════════════

            Vous exécutez une tâche planifiée de manière autonome (pas d'interaction utilisateur).

            **MISSION** : {mission['title']}
            **DESCRIPTION** : {mission['description']}

            **MODE D'EXÉCUTION** : {mode_description}

            **PLAN D'ACTION** :
            {mission['plan']}
            """

        # Ajouter le rapport de la dernière exécution si disponible
        if last_report:
            prompt += f"""
                📊 **DERNIÈRE EXÉCUTION** ({last_report.get('executed_at')}) :
                - Statut : {last_report.get('status')}
                - Résumé : {last_report.get('summary')}
                """
            if last_report.get('warnings'):
                prompt += "- ⚠️ Warnings : " + ", ".join(last_report['warnings']) + "\n"
            if last_report.get('errors'):
                prompt += "- ❌ Erreurs : " + ", ".join(last_report['errors']) + "\n"

        prompt += """
                📋 **WORKFLOW OBLIGATOIRE** :

                1. **CREATE_CHECKLIST** au début (étapes basées sur le plan)
                2. Pour chaque étape :
                - **UPDATE_STEP** status="in_progress" avant de commencer
                - Exécuter l'outil ou l'action
                - **UPDATE_STEP** status="completed" (ou "error")
                3. **TERMINATE_TASK** à la fin avec rapport détaillé

                🔧 **Outils disponibles** : CREATE_CHECKLIST, UPDATE_STEP + tous vos outils habituels
                ⚡ **Autonomie** : Prenez des décisions basées sur le plan et les résultats

                Commencez maintenant l'exécution.
                ═══════════════════════════════════════════════════════════════════════════════
                """

        return prompt

    async def x_finalize_task_execution_if_needed(self, brain, terminate_kwargs: dict):
        """
        ⚠️ MÉTHODE DÉPRÉCIÉE - À SUPPRIMER
        
        Finalise l'exécution d'une tâche si on est en mode task_execution.
        
        Cette méthode n'est plus utilisée car TERMINATE_TASK peut terminer une session
        de travail sans finaliser la tâche complète. Une tâche peut avoir plusieurs
        sessions de travail.

        Steps:
            1. Vérifier si brain.active_task_data existe
            2. Récupérer l'exécution depuis Firebase
            3. Construire le rapport final
            4. Appeler firebase.complete_task_execution()
        """
        try:
            # Vérifier si on est en mode tâche
            if not hasattr(brain, 'active_task_data') or not brain.active_task_data:
                logger.debug("[FINALIZE_TASK] Pas en mode tâche, skip")
                return

            task_id = brain.active_task_data["task_id"]
            execution_id = brain.active_task_data["execution_id"]
            mandate_path = brain.active_task_data["mandate_path"]
            execution_plan = brain.active_task_data.get("execution_plan")

            # NOW ou tâches non stockées : PAS stocké (éphémère), ne pas finaliser dans Firebase
            stored_in_firebase = brain.active_task_data.get("stored_in_firebase", True)
            if execution_plan == "NOW" or not stored_in_firebase:
                logger.info(f"[FINALIZE_TASK] Tâche {execution_plan} {task_id} - pas de finalisation Firebase (non stockée)")
                return

            logger.info(f"[FINALIZE_TASK] Finalisation tâche: {task_id}, execution: {execution_id}")

            # Récupérer l'exécution
            from ..firebase_providers import get_firebase_management
            fbm = get_firebase_management()

            execution = fbm.get_task_execution(mandate_path, task_id, execution_id)

            if not execution:
                logger.error(f"[FINALIZE_TASK] Exécution {execution_id} non trouvée")
                return

            # Calculer durée
            from dateutil import parser
            started_at = parser.isoparse(execution["started_at"])
            duration_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())

            # Extraire checklist
            checklist = execution.get("workflow_checklist", {})
            steps = checklist.get("steps", [])

            steps_completed = sum(1 for s in steps if s.get("status") == "completed")
            steps_total = len(steps)

            errors = [s.get("message") for s in steps if s.get("status") == "error"]

            # Déterminer status global
            if steps_completed == steps_total and not errors:
                status = "completed"
            elif errors:
                status = "failed"
            else:
                status = "partial"

            # Extraire infos LPT
            lpt_executions = []
            for lpt_id, lpt_data in execution.get("lpt_tasks", {}).items():
                lpt_executions.append({
                    "lpt_type": lpt_data.get("task_type"),
                    "status": lpt_data.get("status"),
                    "summary": lpt_data.get("result", {}).get("summary", "")
                })

            # Construire rapport final
            final_report = {
                "execution_id": execution_id,
                "executed_at": execution["started_at"],
                "duration_seconds": duration_seconds,
                "status": status,
                "steps_completed": steps_completed,
                "steps_total": steps_total,
                "summary": terminate_kwargs.get("conclusion", "Exécution terminée"),
                "errors": errors,
                "warnings": [],  # À extraire si nécessaire
                "lpt_executions": lpt_executions}

            # Finaliser (sauvegarde rapport + marquage execution comme completed, sans suppression pour permettre callbacks LPT)
            fbm.complete_task_execution(
                mandate_path, task_id, execution_id, final_report)

            logger.info(
                f"[FINALIZE_TASK] Tâche finalisée: {task_id}, status={status}, "
                f"steps={steps_completed}/{steps_total}, duration={duration_seconds}s"
            )

        except Exception as e:
            logger.error(f"[FINALIZE_TASK] Erreur: {e}", exc_info=True)

    async def _ensure_onboarding_listener(
        self,
        session: LLMSession,
        brain,
        collection_name: str,
        thread_key: str,
        initial_entries: Optional[List[str]] = None
        ) -> None:
        """
        Configure l'écoute onboarding via PubSub Redis.
        
        ⭐ MIGRATION: Utilise maintenant PubSub Redis au lieu de RTDB listener.
        Le RedisSubscriber centralisé gère l'écoute et route vers cette méthode.
        """

        existing_listener = session.onboarding_listeners.get(thread_key)
        initial_processed_ids = session.onboarding_processed_ids.get(thread_key)
        if initial_processed_ids is None:
            initial_processed_ids = set()
            session.onboarding_processed_ids[thread_key] = initial_processed_ids

        if existing_listener:
            if initial_entries is not None:
                existing_listener["log_entries"] = list(initial_entries)
            existing_listener.setdefault("processed_message_ids", initial_processed_ids)
            return

        # Récupérer job_id selon le mode
        job_id = None
        if session.context.chat_mode == "onboarding_chat":
            onboarding_data = brain.onboarding_data or await brain.load_onboarding_data()
            job_id = (onboarding_data or {}).get("job_id")
        elif session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
            # Pour ces modes, charger job_data et utiliser thread_key comme job_id
            job_id = thread_key
            await brain.load_job_data(job_id)
        
        # ⭐ CORRECTION: Pour les modes onboarding-like (apbookeeper_chat, router_chat, banker_chat), 
        # le thread_key est le job_id (fallback si job_id non trouvé)
        if not job_id and session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
            # Le thread_key qui commence par 'klk_' est directement le job_id
            if thread_key.startswith("klk_"):
                job_id = thread_key
                logger.info(
                    f"[ONBOARDING_LISTENER] 🔧 Utilisation thread_key comme job_id pour {session.context.chat_mode}: {job_id}"
                )
            else:
                # Fallback: utiliser le thread_key même s'il ne commence pas par klk_
                job_id = thread_key
                logger.info(
                    f"[ONBOARDING_LISTENER] 🔧 Utilisation thread_key comme job_id (fallback) pour {session.context.chat_mode}: {job_id}"
                )

        if not job_id:
            logger.warning(
                f"[ONBOARDING_LISTENER] job_id manquant pour thread={thread_key}, écoute non démarrée"
            )
            return

        follow_thread = f"follow_{job_id}"

        try:
            # ⭐ MIGRATION: Plus de listener RTDB, on utilise PubSub Redis
            # Le RedisSubscriber centralisé écoute le canal: user:{uid}/{collection}/job_chats/{job_id}/messages
            # et route vers _handle_onboarding_log_event() via _handle_job_chat_message()
            
            # Marquer la session comme "onboarding active" pour PubSub
            session.onboarding_listeners[thread_key] = {
                "listener": None,  # Plus de listener RTDB
                "job_id": job_id,
                "follow_thread": follow_thread,
                "log_entries": list(initial_entries) if initial_entries else [],
                "processed_message_ids": initial_processed_ids,
                "source": "pubsub"  # ⭐ Indique la source PubSub
            }

            pubsub_channel = f"user:{session.context.user_id}/{collection_name}/job_chats/{job_id}/messages"
            logger.info(
                f"[ONBOARDING_LISTENER] ✅ Configuration PubSub démarrée pour job_id={job_id} thread={thread_key} "
                f"- Canal PubSub: {pubsub_channel}"
            )
            logger.info(
                f"[ONBOARDING_LISTENER] 🔍 Écoute gérée par RedisSubscriber centralisé (pattern: user:*)"
            )

        except Exception as e:
            logger.error(
                f"[ONBOARDING_LISTENER] ❌ Échec configuration PubSub pour job_id={job_id}: {e}",
                exc_info=True
            )

    async def _load_onboarding_log_history(
        self,
        brain,
        collection_name: str,
        session: LLMSession,
        thread_key: str
        ) -> List[str]:
        """Charge les logs onboarding depuis le RTDB métier et les injecte dans l'agent."""

        # Récupérer job_id selon le mode
        job_id = None
        if session.context.chat_mode == "onboarding_chat":
            job_id = (brain.onboarding_data or {}).get("job_id") if brain.onboarding_data else None
        elif session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
            # Pour ces modes, utiliser thread_key comme job_id
            job_id = thread_key
        
        # ⭐ CORRECTION: Pour les modes onboarding-like (apbookeeper_chat, router_chat, banker_chat),
        # le thread_key est le job_id (fallback si job_id non trouvé)
        if not job_id and session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
            # Le thread_key qui commence par 'klk_' est directement le job_id
            if thread_key.startswith("klk_"):
                job_id = thread_key
                logger.info(
                    f"[ONBOARDING_LOG] 🔧 Utilisation thread_key comme job_id pour {session.context.chat_mode}: {job_id}"
                )
            else:
                # Fallback: utiliser le thread_key même s'il ne commence pas par klk_
                job_id = thread_key
                logger.info(
                    f"[ONBOARDING_LOG] 🔧 Utilisation thread_key comme job_id (fallback) pour {session.context.chat_mode}: {job_id}"
                )
        
        if not job_id:
            logger.debug("[ONBOARDING_LOG] job_id manquant pour chargement historique")
            return []

        job_messages_path = f"{collection_name}/job_chats/{job_id}/messages"
        logger.info(
            f"[ONBOARDING_LOG] 📖 Chargement historique depuis job_chats - "
            f"thread={thread_key} job_id={job_id} path={job_messages_path}"
        )

        try:
            from ..firebase_providers import get_firebase_realtime

            rtdb = get_firebase_realtime()

            def _fetch_existing():
                ref = rtdb.db.child(job_messages_path)
                return ref.get()

            data = await asyncio.to_thread(_fetch_existing)

            if not data or not isinstance(data, dict):
                logger.info(
                    f"[ONBOARDING_LOG] ℹ️ Aucun historique trouvé côté métier - "
                    f"thread={thread_key} job_id={job_id}"
                )
                return []

            messages: List[Dict[str, Any]] = []
            for message_id, payload in data.items():
                if not isinstance(payload, dict):
                    continue
                payload_with_id = dict(payload)
                payload_with_id.setdefault("id", message_id)
                messages.append(payload_with_id)

            if not messages:
                logger.info(
                    f"[ONBOARDING_LOG] ℹ️ Aucun message exploitable côté métier - "
                    f"thread={thread_key} job_id={job_id}"
                )
                return []

            def _sort_key(msg: Dict[str, Any]) -> float:
                ts = msg.get("timestamp")
                if ts is None:
                    return 0.0
                try:
                    if isinstance(ts, str):
                        return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                    return float(ts)
                except (ValueError, TypeError):
                    return 0.0

            messages.sort(key=_sort_key)

            log_entries: List[str] = []
            processed_ids: Set[str] = set()
            last_timestamp = datetime.now(timezone.utc)

            for msg in messages:
                msg_type = msg.get('message_type') or msg.get('type') or 'MESSAGE'
                if msg_type != 'MESSAGE':
                    continue

                log_text, timestamp_obj = self._format_onboarding_log_entry(msg)
                log_entries.append(log_text)
                last_timestamp = timestamp_obj

                message_id = msg.get("id") or msg.get("message_id")
                if message_id:
                    processed_ids.add(message_id)

            if not log_entries:
                logger.info(
                    f"[ONBOARDING_LOG] ℹ️ Aucun log MESSAGE trouvé côté métier - "
                    f"thread={thread_key} job_id={job_id}"
                )
                return []

            combined_text = "\n".join(log_entries)
            logger.info(
                f"[ONBOARDING_LOG] ✅ Historique métier chargé - "
                f"thread={thread_key} job_id={job_id} entries_count={len(log_entries)}"
            )

            if brain and getattr(brain, 'pinnokio_agent', None):
                brain.pinnokio_agent.append_system_log(
                    message_id=job_id,
                    timestamp=last_timestamp.isoformat(),
                    payload=combined_text
                )
                logger.info(
                    f"[ONBOARDING_LOG] ✅ Logs injectés dans brain agent - "
                    f"thread={thread_key} job_id={job_id}"
                )

            session.onboarding_processed_ids[thread_key] = processed_ids

            listener_info = session.onboarding_listeners.get(thread_key)
            if listener_info is not None:
                listener_info["log_entries"] = list(log_entries)
                listener_info["processed_message_ids"] = processed_ids
                session.onboarding_listeners[thread_key] = listener_info

            return log_entries

        except Exception as e:
            logger.error(f"[ONBOARDING_LOG] ❌ Erreur chargement historique: {e}", exc_info=True)
            return []

    async def _start_intermediation_mode(
        self,
        session: LLMSession,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message: Dict[str, Any],
        job_id: Optional[str] = None
        ) -> None:
        """
        Démarre le mode intermédiation pour un thread donné.

        Actions:
        1. Active le flag intermediation_mode dans la session
        2. Envoie un message système au frontend (visible, mais NON sauvé en RTDB)
        3. Envoie un signal RPC au frontend pour notifier le démarrage

        Args:
            session: Session LLM active
            user_id: ID Firebase utilisateur
            collection_name: ID société
            thread_key: Clé du thread
            message: Message RTDB qui a déclenché l'intermédiation
            job_id: ID du job (optionnel)
        """
        try:
            from ..ws_hub import hub

            # ═══ 0. VÉRIFIER SI DÉJÀ ACTIF (éviter double activation) ═══
            already_active = session.intermediation_mode.get(thread_key, False)
            if already_active:
                logger.info(
                    f"[INTERMEDIATION] ⏭️ Mode DÉJÀ actif - thread={thread_key} - "
                    f"Ignorer réactivation (éviter doublons)"
                )
                return False  # Retourner False pour indiquer que le mode n'a pas été activé

            # ═══ 1. ACTIVER LE FLAG INTERMÉDIATION ═══
            session.intermediation_mode[thread_key] = True

            # ═══ 2. EXTRAIRE LES OUTILS DISPONIBLES ═══
            # Format attendu pour FOLLOW_MESSAGE et FOLLOW_CARD :
            # message["message"]["availableTools"] = ["tool1", "tool2", "tool3"]
            tool_names = []
            
            # ⭐ NOUVEAU FORMAT : Extraire depuis message["message"]["availableTools"]
            message_payload = message.get("message", {})
            if isinstance(message_payload, dict):
                available_tools = message_payload.get("availableTools")
                if available_tools and isinstance(available_tools, list):
                    tool_names = available_tools
                    logger.info(
                        f"[INTERMEDIATION] 🔧 Outils extraits depuis message.availableTools - "
                        f"count={len(tool_names)} tools={tool_names}"
                    )
            
            # ⭐ FALLBACK : Format legacy (tools_config/tools) pour compatibilité
            if not tool_names:
                tools_config_anthropic = message.get("tools_config") or message.get("tools") or []
                if isinstance(tools_config_anthropic, list):
                    # Format Anthropic (liste de dicts avec "name")
                    tool_names = [tool.get("name") for tool in tools_config_anthropic if isinstance(tool, dict) and "name" in tool]
                    logger.info(
                        f"[INTERMEDIATION] 🔧 Outils extraits depuis tools_config (legacy) - "
                        f"count={len(tool_names)} tools={tool_names}"
                    )

            # Construire la liste des outils formatée pour l'affichage dans le message système
            tools_list_text = ""
            if tool_names:
                tools_list_text = "\n\n**Available tools:**\n"
                # Afficher la liste des noms d'outils
                for tool_name in tool_names:
                    tools_list_text += f"- **{tool_name}**\n"

            # ═══ 3. ENVOYER MESSAGE SYSTÈME AU FRONTEND (VISIBLE, NON SAUVÉ RTDB) ═══
            system_message_content = f"""🔄 **Intermediation Mode Activated**

            You are now in direct communication with the business application. Messages will be processed by the business system and not by the main agent.
            {tools_list_text}
            You can use the keywords **TERMINATE**, **PENDING**, or **NEXT** to close this mode, or click on a card if available."""

            # Envoyer via WebSocket comme message système (pas de sauvegarde RTDB)
            system_message_payload = {
                "type": "SYSTEM_MESSAGE_INTERMEDIATION",
                "thread_key": thread_key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message_id": str(uuid.uuid4()),
                "content": system_message_content,
                "system_type": "intermediation_start",
                "title": "Intermediation Mode",
                "from_intermediation": True,
                "tool_names": tool_names  # ⭐ Juste les noms, pas le format Anthropic complet
            }

            ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
            
            # ═══ 4. ENVOYER SIGNAL RPC AU FRONTEND ═══
            # Signal pour que le frontend active l'état intermediation_active
            # ⭐ On envoie juste les noms des outils (tool_names) comme le fait send_tools_list()
            # Le frontend chargera les détails depuis config_tools.json
            rpc_signal = {
                "type": "RPC_INTERMEDIATION_STATE",
                "channel": ws_channel,
                "payload": {
                    "action": "start",
                    "thread_key": thread_key,
                    "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tool_names": tool_names  # ⭐ Liste de strings ["TOOL_1", "TOOL_2"]
                }
            }

            # ═══ 5. ENVOYER MESSAGES VIA WEBSOCKET ═══
            # ⭐ NOUVEAU : Envoyer directement comme les CARDs (pas de vérification listener)
            # Le hub.broadcast gère automatiquement les WebSockets connectés.
            # Si le WebSocket n'est pas connecté, le message est simplement ignoré (comportement identique aux CARDs).
            
            try:
                # Envoyer le message système
                await hub.broadcast(user_id, {
                    "type": "SYSTEM_MESSAGE_INTERMEDIATION",
                    "channel": ws_channel,
                    "payload": system_message_payload
                })
                
                # Envoyer le signal RPC
                await hub.broadcast(user_id, rpc_signal)
                
                logger.info(
                    f"[INTERMEDIATION] 📡 Messages système envoyés via WebSocket - "
                    f"thread={thread_key} (comportement identique aux CARDs)"
                )
            except Exception as e:
                logger.warning(
                    f"[INTERMEDIATION] ⚠️ Erreur envoi WebSocket (ignorée) - "
                    f"thread={thread_key} error={e}"
                )

            logger.info(
                f"[INTERMEDIATION] 🔄 Mode activé avec message système - "
                f"thread={thread_key} job_id={job_id} tools_count={len(tool_names)}"
            )
            
            return True  # Retourner True pour indiquer que le mode a été activé

        except Exception as e:
            logger.error(
                f"[INTERMEDIATION] ❌ Erreur démarrage mode intermédiation: {e}",
                exc_info=True
            )
            return False  # Retourner False en cas d'erreur

    async def _stop_intermediation_mode(
        self,
        session: LLMSession,
        user_id: str,
        collection_name: str,
        thread_key: str,
        job_id: Optional[str] = None,
        reason: str = "user_action"
    ) -> None:
        """
        Arrête le mode intermédiation pour un thread donné.

        Actions:
        1. Désactive le flag intermediation_mode dans la session
        2. Envoie un message système au frontend (visible, mais NON sauvé en RTDB)
        3. Envoie un signal RPC au frontend pour notifier l'arrêt

        Args:
            session: Session LLM active
            user_id: ID Firebase utilisateur
            collection_name: ID société
            thread_key: Clé du thread
            job_id: ID du job (optionnel)
            reason: Raison de la clôture ("user_action", "timeout", "card_click", "termination_word")
        """
        try:
            from ..ws_hub import hub

            # ═══ 1. DÉSACTIVER LE FLAG INTERMÉDIATION ═══
            if thread_key in session.intermediation_mode:
                session.intermediation_mode[thread_key] = False

            # ═══ 2. ENVOYER MESSAGE SYSTÈME AU FRONTEND (VISIBLE, NON SAUVÉ RTDB) ═══
            reason_text = {
                "user_action": "by user action",
                "timeout": "due to timeout",
                "card_click": "by card selection",
                "termination_word": "by termination keyword"
            }.get(reason, reason)

            system_message_content = f"""✅ **Intermediation Mode Terminated**

The intermediation session has been closed {reason_text}. You can now continue to chat normally with the agent."""

            # Envoyer via WebSocket comme message système (pas de sauvegarde RTDB)
            system_message_payload = {
                "type": "SYSTEM_MESSAGE_INTERMEDIATION",
                "thread_key": thread_key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message_id": str(uuid.uuid4()),
                "content": system_message_content,
                "system_type": "intermediation_end",
                "title": "End of Intermediation",
                "from_intermediation": False
            }

            ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
            await hub.broadcast(user_id, {
                "type": "SYSTEM_MESSAGE_INTERMEDIATION",
                "channel": ws_channel,
                "payload": system_message_payload
            })

            # ═══ 3. ENVOYER SIGNAL RPC AU FRONTEND ═══
            rpc_signal = {
                "type": "RPC_INTERMEDIATION_STATE",
                "channel": ws_channel,
                "payload": {
                    "action": "stop",
                    "thread_key": thread_key,
                    "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": reason
                }
            }

            await hub.broadcast(user_id, rpc_signal)

            logger.info(
                f"[INTERMEDIATION] 🔚 Mode désactivé avec message système - "
                f"thread={thread_key} job_id={job_id} reason={reason}"
            )

        except Exception as e:
            logger.error(
                f"[INTERMEDIATION] ❌ Erreur arrêt mode intermédiation: {e}",
                exc_info=True
            )

    async def _send_websocket_message(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message_type: str,
        payload_data: Dict[str, Any],
        additional_fields: Optional[Dict[str, Any]] = None
        ) -> None:
        """
        Méthode centralisée pour envoyer des messages via WebSocket.
        
        Utilise le même format que request_approval_with_card (general_chat) pour garantir
        la cohérence entre tous les modes de chat.
        
        Args:
            user_id: ID Firebase utilisateur
            collection_name: ID société (space_code)
            thread_key: Clé du thread de chat
            message_type: Type du message WebSocket ("CARD", "WORKFLOW", "CMMD", etc.)
            payload_data: Données du payload WebSocket
            additional_fields: Champs additionnels à ajouter au payload (optionnel)
        """
        try:
            from ..ws_hub import hub
            
            # Construire le payload avec structure identique à general_chat
            ws_message = {
                "type": message_type,
                "thread_key": thread_key,
                **payload_data
            }
            
            # Ajouter champs additionnels si fournis
            if additional_fields:
                ws_message.update(additional_fields)
            
            # Channel WebSocket (format standardisé)
            ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
            
            # Broadcast via hub (format identique à request_approval_with_card)
            await hub.broadcast(user_id, {
                "type": message_type,  # ✅ Type explicite (CARD, WORKFLOW, CMMD, etc.)
                "channel": ws_channel,
                "payload": ws_message
            })
            
            logger.info(
                f"[WSS_CENTRAL] 📡 Message WebSocket envoyé - "
                f"type={message_type} thread={thread_key} channel={ws_channel}"
            )
            
        except Exception as e:
            logger.error(
                f"[WSS_CENTRAL] ❌ Erreur envoi WebSocket: {e}",
                exc_info=True
            )

    async def _send_non_message_via_websocket(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message: Dict[str, Any]
        ) -> None:
        """
        Envoie les messages non-MESSAGE (CARD, WORKFLOW, CMMD, etc.) via WebSocket.
        
        Ces messages ne doivent PAS être injectés dans l'historique LLM mais envoyés
        directement au frontend via WebSocket. Utilise la méthode centralisée
        _send_websocket_message pour garantir la cohérence du format.
        
        Args:
            user_id: ID Firebase utilisateur
            collection_name: ID société (space_code)
            thread_key: Clé du thread de chat
            message: Message RTDB complet avec tous ses arguments
        """
        try:
            # Extraire le type de message depuis le message RTDB
            original_message_type = message.get('message_type') or message.get('type')
            
            if not original_message_type:
                logger.warning(
                    f"[ONBOARDING_WSS] ⚠️ Type de message manquant, utilisation de 'MESSAGE' par défaut "
                    f"thread={thread_key}"
                )
                original_message_type = 'MESSAGE'

            # ⭐ Normalisation WSS : certains types RTDB sont des signaux internes mais doivent
            # être affichés côté UI comme des événements génériques (ex: FOLLOW_CARD → CARD).
            ws_message_type = original_message_type
            if original_message_type == "FOLLOW_CARD":
                ws_message_type = "CARD"
            
            # Construire le payload avec les champs essentiels
            payload_data = {
                "timestamp": message.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "message_id": message.get("id") or message.get("message_id"),
            }
            
            # Préserver le contenu du message
            if "content" in message:
                payload_data["content"] = message["content"]
            
            # Préserver les autres champs du message (event, message, cardParams, etc.)
            # mais exclure les champs déjà traités
            excluded_fields = {"id", "message_id", "timestamp", "message_type", "type"}
            additional_fields = {
                k: v for k, v in message.items() 
                if k not in excluded_fields
            }

            # ✅ IMPORTANT (frontend streaming guard):
            # ChatState déduit souvent `mt = message_type or type` depuis le payload.
            # Donc on force `message_type` à matcher le type WSS (ex: CARD) pour éviter le DROP.
            # On conserve le type RTDB d'origine dans un champ séparé pour debug/compat.
            additional_fields["message_type"] = ws_message_type
            additional_fields["rtdb_type"] = original_message_type
            
            # Utiliser la méthode centralisée avec le format général_chat
            await self._send_websocket_message(
                user_id=user_id,
                collection_name=collection_name,
                thread_key=thread_key,
                message_type=ws_message_type,  # ✅ Type WSS normalisé (ex: FOLLOW_CARD → CARD)
                payload_data=payload_data,
                additional_fields=additional_fields if additional_fields else None
            )
            
            logger.info(
                f"[ONBOARDING_WSS] ✅ Message non-MESSAGE routé via WebSocket centralisé - "
                f"type={ws_message_type} rtdb_type={original_message_type} thread={thread_key}"
            )
            
        except Exception as e:
            logger.error(
                f"[ONBOARDING_WSS] ❌ Erreur envoi WebSocket: {e}",
                exc_info=True
            )

    def _format_onboarding_log_entry(self, message: Dict[str, Any]) -> Tuple[str, datetime]:
        """Formate un message métier en entrée de log horodaté."""

        raw_content = message.get("content")

        if isinstance(raw_content, str):
            try:
                content_dict = json.loads(raw_content)
                if isinstance(content_dict, dict):
                    if "message" in content_dict and isinstance(content_dict["message"], dict):
                        if "argumentText" in content_dict["message"]:
                            text_content = content_dict["message"]["argumentText"]
                        else:
                            text_content = json.dumps(content_dict["message"], ensure_ascii=False)
                    else:
                        text_content = raw_content
                else:
                    text_content = raw_content
            except (json.JSONDecodeError, TypeError):
                text_content = raw_content
        elif isinstance(raw_content, dict):
            message_payload = raw_content.get("message") if isinstance(raw_content.get("message"), dict) else None
            if message_payload and "argumentText" in message_payload:
                text_content = message_payload["argumentText"]
            else:
                text_content = json.dumps(raw_content, ensure_ascii=False)
        else:
            text_content = (
                message.get("message")
                or message.get("description")
                or json.dumps(message, ensure_ascii=False)
            )

        timestamp_obj = datetime.now(timezone.utc)
        message_timestamp = message.get("timestamp")
        if message_timestamp:
            try:
                if isinstance(message_timestamp, str):
                    timestamp_obj = datetime.fromisoformat(message_timestamp.replace('Z', '+00:00'))
                else:
                    timestamp_obj = datetime.fromtimestamp(message_timestamp, tz=timezone.utc)
            except (ValueError, TypeError):
                timestamp_obj = datetime.now(timezone.utc)

        timestamp_formatted = timestamp_obj.strftime("%Y-%m-%d %H:%M:%S")
        log_text = f"{timestamp_formatted} | {text_content}"

        return log_text, timestamp_obj

    async def _handle_onboarding_log_event(
        self,
        session: LLMSession,
        brain,
        collection_name: str,
        thread_key: str,
        follow_thread_key: str,  # ⚠️ Conservé pour compatibilité mais non utilisé
        message: Dict[str, Any]
        ) -> None:
        """
        Traite chaque log onboarding reçu depuis RTDB.
        
        ⭐ MODIFIÉ: Filtre par message_type :
        - MESSAGE → Injection directe dans l'historique agent avec format horodaté
        - Autres types (CARD, WORKFLOW, CMMD) → Envoi via WebSocket uniquement
        """

        try:
            listener_info = session.onboarding_listeners.get(thread_key)
            if not listener_info:
                logger.debug(
                    f"[ONBOARDING_LOG] Listener non trouvé pour thread={thread_key}, message ignoré"
                )
                return

            job_id = listener_info.get("job_id")
            user_id = session.context.user_id
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 0 : Vérifier si le message a déjà été traité localement
            # ═══════════════════════════════════════════════════════════
            # ⭐ FIX DOUBLON : Ignorer les messages créés localement par ce backend
            # (ex: CLOSE_INTERMEDIATION écrit lors de la détection d'un mot de terminaison)
            message_id = message.get("id") or message.get("message_id")
            if message_id:
                existing_processed = session.onboarding_processed_ids.get(thread_key)
                if existing_processed and message_id in existing_processed:
                    logger.info(
                        f"[ONBOARDING_LOG] ⏭️ Message déjà traité localement ignoré - "
                        f"thread={thread_key} message_id={message_id} "
                        f"(évite double traitement des messages créés par ce backend)"
                    )
                    return
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : Extraction du type de message
            # ═══════════════════════════════════════════════════════════
            message_type = message.get('message_type') or message.get('type')
            
            # Si pas de type explicite, supposer MESSAGE par défaut (compatibilité)
            if not message_type:
                message_type = 'MESSAGE'
                logger.debug(
                    f"[ONBOARDING_LOG] ⚠️ Type manquant, supposé MESSAGE pour thread={thread_key}"
                )
            
            logger.info(
                f"[ONBOARDING_LOG] 📨 Message reçu - "
                f"type={message_type} thread={thread_key} job_id={job_id} "
                f"message_id={message.get('id', 'N/A')}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : Routage selon le type
            # ═══════════════════════════════════════════════════════════
            
            if message_type == 'MESSAGE':
                # ═══ VÉRIFICATION MODE INTERMÉDIATION ═══
                # Si mode intermédiation actif, rediriger vers WebSocket au lieu d'injecter
                if session.intermediation_mode.get(thread_key, False):
                    # En mode intermédiation, les MESSAGE sont envoyés via WebSocket avec llm_message_direct
                    from ..ws_hub import hub
                    
                    message_id = message.get("id") or message.get("message_id")
                    timestamp = message.get("timestamp") or datetime.now(timezone.utc).isoformat()
                    
                    # Extraire le contenu du message
                    content = message.get("content", "")
                    # Si le contenu est un JSON string, le parser
                    if isinstance(content, str):
                        try:
                            import json
                            content_dict = json.loads(content)
                            if isinstance(content_dict, dict) and "message" in content_dict:
                                if isinstance(content_dict["message"], dict) and "argumentText" in content_dict["message"]:
                                    content = content_dict["message"]["argumentText"]
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass  # Garder le contenu tel quel si pas de JSON valide
                    
                    ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
                    
                    await hub.broadcast(user_id, {
                        "type": "llm_message_direct",
                        "channel": ws_channel,
                        "payload": {
                            "message_id": message_id or str(uuid.uuid4()),
                            "thread_key": thread_key,
                            "space_code": collection_name,
                            "content": content,
                            "timestamp": timestamp,
                            "intermediation": True,
                            "from_agent": True  # Indique que c'est une réponse de l'agent
                        }
                    })
                    
                    logger.info(
                        f"[INTERMEDIATION] 📡 MESSAGE redirigé vers WebSocket (mode intermédiation) - "
                        f"type=llm_message_direct thread={thread_key} message_id={message_id}"
                    )
                    return
                
                # ═══ MODE NORMAL : Injection dans l'historique agent ═══
                log_entries = listener_info.setdefault("log_entries", [])
                existing_processed = session.onboarding_processed_ids.get(thread_key)
                if existing_processed is None:
                    existing_processed = set()
                    session.onboarding_processed_ids[thread_key] = existing_processed
                processed_ids = listener_info.setdefault("processed_message_ids", existing_processed)

                message_id = message.get("id") or message.get("message_id")
                if message_id and message_id in processed_ids:
                    logger.debug(
                        f"[ONBOARDING_LOG] 🔁 Message déjà traité ignoré - "
                        f"thread={thread_key} job_id={job_id} message_id={message_id}"
                    )
                    return

                log_text, timestamp_obj = self._format_onboarding_log_entry(message)

                if message_id:
                    processed_ids.add(message_id)

                log_entries.append(log_text)
                listener_info["log_entries"] = log_entries
                session.onboarding_processed_ids[thread_key] = processed_ids

                combined_text = "\n".join(log_entries)

                if brain and getattr(brain, "pinnokio_agent", None):
                    brain.pinnokio_agent.append_system_log(
                        message_id=job_id or thread_key,
                        timestamp=timestamp_obj.isoformat(),
                        payload=combined_text
                    )

                session.onboarding_listeners[thread_key] = listener_info

                logger.info(
                    f"[ONBOARDING_LOG] ✅ MESSAGE injecté dans l'historique agent - "
                    f"thread={thread_key} job_id={job_id} entries_count={len(log_entries)}"
                )
            
            elif message_type == 'FOLLOW_MESSAGE':
                # ═══ MODE INTERMÉDIATION ACTIVÉ ═══
                # L'application métier requiert une interaction directe avec l'utilisateur

                # Envoyer le message via WebSocket (sans streaming)
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )

                # ⭐ DÉMARRER MODE INTERMÉDIATION (pour tous les modes)
                await self._start_intermediation_mode(
                    session=session,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message,
                    job_id=job_id
                )

                logger.info(
                    f"[INTERMEDIATION] 🔄 Mode activé via FOLLOW_MESSAGE - "
                    f"thread={thread_key} job_id={job_id} message_id={message.get('id', 'N/A')}"
                )
                return

            elif message_type == 'CLOSE_INTERMEDIATION':
                # ═══ MODE INTERMÉDIATION DÉSACTIVÉ ═══
                # L'application métier signale explicitement la fin de l'intermédiation
                # Peut être déclenché par: timeout, action utilisateur, carte cliquée, mot de terminaison

                # Détecter la raison de la fermeture depuis le message
                # Le message peut contenir un champ 'reason' ou 'timeout' pour indiquer l'origine
                close_reason = message.get('reason') or message.get('close_reason')
                is_timeout = message.get('timeout', False) or close_reason == 'timeout'
                
                # Déterminer la raison appropriée
                if is_timeout or close_reason == 'timeout':
                    reason = "timeout"
                elif close_reason == 'card_click':
                    reason = "card_click"
                elif close_reason == 'termination_word':
                    reason = "termination_word"
                else:
                    reason = "user_action"  # Par défaut

                # Envoyer via WebSocket pour notifier le frontend
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )

                # ⭐ ARRÊTER MODE INTERMÉDIATION avec la raison appropriée
                await self._stop_intermediation_mode(
                    session=session,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    job_id=job_id,
                    reason=reason
                )

                logger.info(
                    f"[INTERMEDIATION] 🔚 Mode désactivé via CLOSE_INTERMEDIATION - "
                    f"thread={thread_key} job_id={job_id} message_id={message.get('id', 'N/A')} reason={reason}"
                )
                return

            elif message_type == "FOLLOW_CARD":
                # ═══ ENVOI VIA WEBSOCKET + ACTIVATION MODE INTERMÉDIATION ═══
                # FOLLOW_CARD contient une carte interactive avec outils disponibles
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )

                await self._notify_agent_of_waiting_context(
                    session=session,
                    brain=brain,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    job_id=job_id,
                    message_type=message_type,
                    message=message
                )

                # ⭐ NOUVELLE LOGIQUE: Démarrer mode intermédiation pour FOLLOW_CARD
                # UNIQUEMENT pour apbookeeper_chat, router_chat, banker_chat
                if session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
                    await self._start_intermediation_mode(
                        session=session,
                        user_id=user_id,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        message=message,
                        job_id=job_id
                    )
                    logger.info(
                        f"[INTERMEDIATION] 🔄 Mode activé via FOLLOW_CARD pour {session.context.chat_mode} - "
                        f"thread={thread_key} job_id={job_id}"
                    )

                logger.info(
                    f"[ONBOARDING_LOG] ✅ Message FOLLOW_CARD routé via WebSocket "
                    f"et contexte partagé avec l'agent"
                )

            elif message_type in {"CARD", "WAITING_MESSAGE"}:
                # ═══ ENVOI VIA WEBSOCKET + NOTIFICATION AGENT ═══
                # Messages de type CARD/WAITING_MESSAGE (sans intermédiation)
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )

                await self._notify_agent_of_waiting_context(
                    session=session,
                    brain=brain,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    job_id=job_id,
                    message_type=message_type,
                    message=message
                )

                logger.info(
                    f"[ONBOARDING_LOG] ✅ Message {message_type} routé via WebSocket "
                    f"et contexte partagé avec l'agent"
                )

            elif message_type == "TOOL":
                # ═══ ENVOI VIA WEBSOCKET (LEGACY) ═══
                # Message TOOL envoyé via WebSocket pour compatibilité
                # N'active PLUS le mode intermédiation (géré par FOLLOW_MESSAGE/FOLLOW_CARD)
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )
                logger.info(
                    f"[ONBOARDING_LOG] ✅ Message TOOL routé via WebSocket "
                    f"(legacy - pas d'activation intermédiation)"
                )

            elif message_type == "CARD_CLICKED_PINNOKIO":
                # ═══ CARTE CLIQUÉE - FERMETURE MODE INTERMÉDIATION ═══
                # Quand l'utilisateur clique sur une carte, cela ferme le mode intermédiation
                # Le message CARD_CLICKED_PINNOKIO est écrit dans RTDB par le frontend
                # et doit être traité pour fermer le mode intermédiation
                
                # Envoyer via WebSocket pour notifier le frontend
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )
                
                # ⭐ FERMER MODE INTERMÉDIATION si actif
                if session.intermediation_mode.get(thread_key, False):
                    await self._stop_intermediation_mode(
                        session=session,
                        user_id=user_id,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        job_id=job_id,
                        reason="card_click"
                    )
                    logger.info(
                        f"[INTERMEDIATION] 🔚 Mode désactivé via CARD_CLICKED_PINNOKIO - "
                        f"thread={thread_key} job_id={job_id} message_id={message.get('id', 'N/A')}"
                    )
                else:
                    logger.debug(
                        f"[INTERMEDIATION] ℹ️ CARD_CLICKED_PINNOKIO reçu mais mode intermédiation déjà inactif - "
                        f"thread={thread_key}"
                    )
                return

            else:
                # ═══ ENVOI VIA WEBSOCKET UNIQUEMENT ═══
                # Types: WORKFLOW, CMMD, etc.
                await self._send_non_message_via_websocket(
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message
                )
                logger.info(
                    f"[ONBOARDING_LOG] ✅ Message {message_type} routé via WebSocket "
                    f"(pas d'injection dans historique LLM)"
                )

        except Exception as e:
            logger.error(f"[ONBOARDING_LOG] ❌ Erreur traitement log: {e}", exc_info=True)

    async def _handle_intermediation_response(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message: str,
        session: LLMSession
    ) -> dict:
        """
        Traite les réponses utilisateur pendant le mode intermédiation.
        Envoie la réponse au RTDB de l'application métier ET via WebSocket au frontend.
        
        Args:
            user_id: ID de l'utilisateur Firebase
            collection_name: ID de la société (space_code)
            thread_key: Clé du thread de chat
            message: Message de l'utilisateur
            session: Session LLM active
            
        Returns:
            dict: Résultat de l'opération
        """
        from ..ws_hub import hub
        
        try:
            listener_info = session.onboarding_listeners.get(thread_key)
            if not listener_info:
                logger.error(
                    f"[INTERMEDIATION] ❌ Listener introuvable pour thread={thread_key}"
                )
                return {
                    "success": False,
                    "error": "Listener not found"
                }
            
            job_id = listener_info.get("job_id")
            
            # Vérifier si le message SE TERMINE par un mot de terminaison (détection stricte)
            termination_words = ["TERMINATE", "PENDING", "NEXT"]
            message_stripped = message.strip()
            message_upper = message_stripped.upper()
            
            # Détecter quel mot de terminaison est utilisé (à la fin du message)
            detected_word = None
            for word in termination_words:
                if message_upper.endswith(word):
                    detected_word = word
                    break
            
            has_termination = detected_word is not None
            
            # Envoyer la réponse au RTDB de l'application métier
            messages_path = f"{collection_name}/job_chats/{job_id}/messages"
            message_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            message_ref = self._get_rtdb_ref(f"{messages_path}/{message_id}")
            
            payload = {
                "id": message_id,
                "message_type": "MESSAGE_PINNOKIO",
                "content": message,
                "sender_id": user_id,
                "timestamp": timestamp,
                "read": False,
                "local_processed": False
            }
            
            message_ref.set(payload)
            
            logger.info(
                f"[INTERMEDIATION] ✅ Réponse utilisateur envoyée vers RTDB métier - "
                f"thread={thread_key} job_id={job_id} has_termination={has_termination}"
            )
            
            # ⚠️ NOTE : On n'envoie PAS le message utilisateur via WebSocket
            # Le frontend a déjà sauvegardé le message utilisateur dans active_chats
            # Envoyer llm_message_direct ici créerait une duplication où le message utilisateur
            # apparaîtrait comme message de l'agent dans active_chats
            # Seules les réponses de l'agent métier (MESSAGE avec from_agent=True) sont envoyées

            # Si mot de terminaison détecté, attendre que Pinnokio traite puis fermer
            if has_termination:
                logger.info(
                    f"[INTERMEDIATION] 🔚 Mot de terminaison détecté : {detected_word} - "
                    f"thread={thread_key} - Attente traitement Pinnokio avant fermeture"
                )
                
                # ⭐ Attendre un court délai pour que Pinnokio traite le message avant qu'on ferme
                await asyncio.sleep(0.1)  # 100ms pour que Pinnokio traite
                
                # Écrire le message CLOSE_INTERMEDIATION dans RTDB
                close_message_id = str(uuid.uuid4())
                close_timestamp = datetime.now(timezone.utc).isoformat()
                
                # ⭐ FIX DOUBLON : Marquer ce message comme "déjà traité" AVANT de l'écrire
                # Cela évite que le listener ne le retraite et renvoie les messages système 2x
                existing_processed = session.onboarding_processed_ids.get(thread_key)
                if existing_processed is None:
                    existing_processed = set()
                    session.onboarding_processed_ids[thread_key] = existing_processed
                existing_processed.add(close_message_id)
                
                # Aussi mettre à jour dans le listener_info pour cohérence
                listener_info = session.onboarding_listeners.get(thread_key)
                if listener_info:
                    listener_info.setdefault("processed_message_ids", existing_processed)
                
                logger.info(
                    f"[INTERMEDIATION] 🏷️ Message CLOSE_INTERMEDIATION marqué comme traité localement - "
                    f"close_message_id={close_message_id} (évite double envoi messages système)"
                )
                
                close_message_ref = self._get_rtdb_ref(f"{messages_path}/{close_message_id}")

                close_payload = {
                    "id": close_message_id,
                    "message_type": "CLOSE_INTERMEDIATION",
                    "content": "Intermediation closed by user",
                    "reason": "termination_word",
                    "termination_word": detected_word,  # TERMINATE, PENDING ou NEXT
                    "original_message_id": message_id,
                    "timestamp": close_timestamp,
                    "read": False,
                    "eventTime": close_timestamp
                }

                close_message_ref.set(close_payload)

                # ⭐ ARRÊTER MODE INTERMÉDIATION avec message système
                await self._stop_intermediation_mode(
                    session=session,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    job_id=job_id,
                    reason="termination_word"
                )

                logger.info(
                    f"[INTERMEDIATION] 🔚 Mode désactivé - CLOSE_INTERMEDIATION écrit dans RTDB - "
                    f"thread={thread_key} termination_word={detected_word}"
                )
            
            return {
                "success": True,
                "message_id": message_id,
                "intermediation_active": not has_termination,
                "job_id": job_id
            }
            
        except Exception as e:
            logger.error(f"[INTERMEDIATION] ❌ Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _check_intermediation_on_load(
        self,
        session: LLMSession,
        collection_name: str,
        thread_key: str,
        job_status: Optional[str] = None
    ) -> None:
        """
        Vérifie si le chat doit être en mode intermédiation au chargement.

        ⭐ LOGIQUE CORRECTE :
        1. Cherche dans TOUT l'historique s'il existe un FOLLOW_CARD/FOLLOW_MESSAGE
        2. Vérifie s'il y a un CLOSE_INTERMEDIATION après ce message
        3. Si OUI → mode normal (intermédiation terminée)
        4. Si NON → activer mode intermédiation (peu importe les messages entre)
        
        Le dernier message n'a pas d'importance : ce qui compte c'est l'existence
        d'un FOLLOW_CARD/FOLLOW_MESSAGE sans CLOSE_INTERMEDIATION après.
        
        ⭐ RENVOI DE CARTE :
        Si une FOLLOW_CARD existe dans l'historique et n'a pas été cliquée (CARD_CLICKED_PINNOKIO),
        elle est renvoyée au frontend pour permettre à l'utilisateur d'interagir avec
        les boutons d'action (même s'il y a eu des échanges après).
        
        ⭐ CONDITIONS D'ACTIVATION :
        - FOLLOW_CARD/FOLLOW_MESSAGE trouvé dans l'historique
        - Pas de CLOSE_INTERMEDIATION après
        - Job actif (job_status == 'running')

        Args:
            session: Session LLM active
            collection_name: ID de la société
            thread_key: Clé du thread de chat
            job_status: Statut du job (optionnel) - "running", "in queue", "completed", etc.
        """
        try:
            logger.info(
                f"[INTERMEDIATION_LOAD] 🔍 Vérification mode intermédiation au chargement - "
                f"thread={thread_key} job_status={job_status}"
            )
            
            # ⭐ NOUVEAU : Récupérer job_id depuis listener ou utiliser thread_key comme fallback
            listener_info = session.onboarding_listeners.get(thread_key)
            job_id = None
            
            if listener_info:
                job_id = listener_info.get("job_id")
                logger.info(
                    f"[INTERMEDIATION_LOAD] Listener trouvé - thread={thread_key} job_id={job_id}"
                )
            
            # ⭐ FALLBACK : Pour les modes onboarding-like, thread_key = job_id
            if not job_id and session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
                job_id = thread_key
                logger.info(
                    f"[INTERMEDIATION_LOAD] ⚠️ job_id non trouvé dans listener, utilisation thread_key comme fallback - "
                    f"thread={thread_key} job_id={job_id}"
                )
            
            if not job_id:
                logger.info(
                    f"[INTERMEDIATION_LOAD] ⏭️ job_id introuvable pour thread={thread_key}, "
                    f"vérification ignorée (mode={session.context.chat_mode})"
                )
                return
            
            # Charger les derniers messages de l'application métier
            from ..firebase_providers import FirebaseRealtimeChat
            firebase_mgmt = FirebaseRealtimeChat()
            
            messages = firebase_mgmt.get_channel_messages(
                space_code=collection_name,
                thread_key=job_id,
                limit=50,
                mode="job_chats"
            )
            
            if not messages:
                logger.info(
                    f"[INTERMEDIATION_LOAD] ⏭️ Aucun message métier pour thread={thread_key} job_id={job_id}, "
                    f"mode normal"
                )
                return
            
            # Trier par timestamp (du plus récent au plus ancien)
            def _sort_key(msg):
                ts = msg.get('timestamp', '')
                if isinstance(ts, str):
                    try:
                        return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                    except:
                        return 0
                return ts if isinstance(ts, (int, float)) else 0
            
            messages.sort(key=_sort_key, reverse=True)
            
            # ⭐ LOGIQUE CORRIGÉE AVEC VÉRIFICATION CHRONOLOGIQUE:
            # 1. Trouver la FOLLOW_CARD/FOLLOW_MESSAGE la plus récente
            # 2. Vérifier s'il y a un CLOSE_INTERMEDIATION APRÈS cette FOLLOW_CARD (plus récent chronologiquement)
            # 3. Si CLOSE_INTERMEDIATION est APRÈS la FOLLOW_CARD → mode fermé
            # 4. Si CLOSE_INTERMEDIATION est AVANT la FOLLOW_CARD → mode doit être activé (nouvelle intermédiation)
            
            has_card_clicked = False
            card_or_follow_message = None  # Dernier FOLLOW_CARD/FOLLOW_MESSAGE trouvé
            card_or_follow_index = None
            last_card_for_display = None  # Dernière FOLLOW_CARD à afficher (si pas cliquée)
            last_card_index = None
            close_message_index = None  # Index du CLOSE_INTERMEDIATION le plus récent
            
            # 1. Parcourir TOUS les messages pour trouver :
            #    - Dernier FOLLOW_CARD/FOLLOW_MESSAGE (le plus récent)
            #    - Dernière FOLLOW_CARD (pour affichage)
            #    - CLOSE_INTERMEDIATION le plus récent (pour comparaison chronologique)
            for idx, msg in enumerate(messages):
                msg_type = msg.get('message_type')
                
                # Sauvegarder le CLOSE_INTERMEDIATION le plus récent (premier trouvé = plus récent)
                if msg_type == 'CLOSE_INTERMEDIATION' and close_message_index is None:
                    close_message_index = idx
                    logger.info(
                        f"[INTERMEDIATION_LOAD] ✅ CLOSE_INTERMEDIATION trouvé à l'index {idx} - "
                        f"thread={thread_key} message_id={msg.get('id', 'N/A')}"
                    )
                
                # Sauvegarder le premier (plus récent) FOLLOW_CARD/FOLLOW_MESSAGE trouvé
                if msg_type in ('FOLLOW_CARD', 'FOLLOW_MESSAGE') and card_or_follow_message is None:
                    card_or_follow_message = msg
                    card_or_follow_index = idx
                    logger.info(
                        f"[INTERMEDIATION_LOAD] 🔧 Dernier {msg_type} trouvé à l'index {idx} - "
                        f"thread={thread_key} message_id={msg.get('id', 'N/A')}"
                    )
                
                # Sauvegarder la première (plus récente) FOLLOW_CARD trouvée pour affichage
                if msg_type == 'FOLLOW_CARD' and last_card_for_display is None:
                    last_card_for_display = msg
                    last_card_index = idx
                    logger.info(
                        f"[INTERMEDIATION_LOAD] 🃏 Dernière FOLLOW_CARD trouvée à l'index {idx} - "
                        f"thread={thread_key} card_id={msg.get('id', 'N/A')}"
                    )
            
            # 2. Vérifier l'ordre chronologique : CLOSE_INTERMEDIATION est-il APRÈS la FOLLOW_CARD ?
            # Les messages sont triés du plus récent (idx 0) au plus ancien
            # Si close_message_index < card_or_follow_index → CLOSE est plus récent que FOLLOW_CARD → mode fermé
            # Si close_message_index > card_or_follow_index ou None → CLOSE est plus ancien ou absent → mode doit être activé
            has_close_after_card = False
            if card_or_follow_index is not None and close_message_index is not None:
                if close_message_index < card_or_follow_index:
                    # CLOSE_INTERMEDIATION est plus récent que la FOLLOW_CARD → mode fermé
                    has_close_after_card = True
                    logger.info(
                        f"[INTERMEDIATION_LOAD] 🔚 CLOSE_INTERMEDIATION est APRÈS la FOLLOW_CARD "
                        f"(close_idx={close_message_index} < card_idx={card_or_follow_index}) - "
                        f"thread={thread_key} → Mode fermé"
                    )
                else:
                    # CLOSE_INTERMEDIATION est plus ancien que la FOLLOW_CARD → nouvelle intermédiation
                    logger.info(
                        f"[INTERMEDIATION_LOAD] ✅ CLOSE_INTERMEDIATION est AVANT la FOLLOW_CARD "
                        f"(close_idx={close_message_index} >= card_idx={card_or_follow_index}) - "
                        f"thread={thread_key} → Nouvelle intermédiation détectée"
                    )
            elif close_message_index is not None and card_or_follow_index is None:
                # CLOSE_INTERMEDIATION existe mais pas de FOLLOW_CARD → mode fermé
                has_close_after_card = True
                logger.info(
                    f"[INTERMEDIATION_LOAD] 🔚 CLOSE_INTERMEDIATION trouvé sans FOLLOW_CARD/FOLLOW_MESSAGE - "
                    f"thread={thread_key} → Mode fermé"
                )
            
            # 3. Si une FOLLOW_CARD a été trouvée, vérifier si elle a été cliquée
            if last_card_for_display and last_card_index is not None:
                for msg in messages[:last_card_index]:  # Messages plus récents que la FOLLOW_CARD
                    if msg.get('message_type') == 'CARD_CLICKED_PINNOKIO':
                        has_card_clicked = True
                        logger.info(
                            f"[INTERMEDIATION_LOAD] ✅ CARD_CLICKED_PINNOKIO trouvé après FOLLOW_CARD - "
                            f"thread={thread_key} message_id={msg.get('id', 'N/A')}"
                        )
                        break
            
            # 4. Décider d'activer le mode intermédiation
            if card_or_follow_message and not has_close_after_card:
                # Un FOLLOW_CARD/FOLLOW_MESSAGE existe ET pas de CLOSE_INTERMEDIATION après
                # → Activer le mode intermédiation

                # Déterminer si le job est en cours de traitement
                job_in_process = True  # Par défaut, on suppose que le job est en cours

                if job_status:
                    # Si job_status est fourni, vérifier qu'il est bien "running" uniquement
                    job_in_process = job_status == 'running'
                    logger.info(
                        f"[INTERMEDIATION_LOAD] 🔍 job_status={job_status} → "
                        f"job_in_process={job_in_process}"
                    )

                # Ne réactiver l'intermédiation QUE si le job est en cours
                if job_in_process:
                    # Réactiver le mode intermédiation avec message système
                    # Utiliser card_or_follow_message pour les outils
                    mode_activated = await self._start_intermediation_mode(
                        session=session,
                        user_id=session.context.user_id,
                        collection_name=collection_name,
                        thread_key=thread_key,
                        message=card_or_follow_message,
                        job_id=job_id
                    )
                    
                    if mode_activated:
                        logger.info(
                            f"[INTERMEDIATION_LOAD] ✅ Mode réactivé au chargement - "
                            f"thread={thread_key} job_id={job_id} "
                            f"(FOLLOW_CARD/FOLLOW_MESSAGE trouvé sans CLOSE_INTERMEDIATION, job_status={job_status})"
                        )
                    else:
                        logger.info(
                            f"[INTERMEDIATION_LOAD] ⏭️ Mode DÉJÀ actif - thread={thread_key} - "
                            f"Ignorer réactivation (éviter doublons)"
                        )

                    # ⭐ Renvoyer la CARD si elle existe et n'a pas été cliquée
                    if last_card_for_display and not has_card_clicked and mode_activated:
                        from ..ws_hub import hub
                        ws_channel = f"chat:{session.context.user_id}:{collection_name}:{thread_key}"

                        # Préparer le message de la carte
                        card_message = {
                            "type": "CARD",
                            "channel": ws_channel,
                            "payload": last_card_for_display
                        }

                        # Vérifier si le listener du chat est actif
                        from ..registry.registry_listeners import get_registry_listeners
                        registry = get_registry_listeners()
                        
                        listener_status = registry.check_listener_status(
                            user_id=session.context.user_id,
                            listener_type="chat",
                            space_code=collection_name,
                            thread_key=thread_key
                        )
                        
                        is_listener_active = listener_status.get("active", False)
                        
                        if is_listener_active:
                            # ✅ WebSocket connecté → Envoyer immédiatement
                            await hub.broadcast(session.context.user_id, card_message)
                            
                            logger.info(
                                f"[INTERMEDIATION_LOAD] 🃏 Carte renvoyée immédiatement (WebSocket actif) - "
                                f"thread={thread_key} card_id={last_card_for_display.get('id', 'N/A')}"
                            )
                        else:
                            # ⏳ WebSocket pas encore connecté → Bufferiser
                            from ..ws_message_buffer import get_message_buffer
                            buffer = get_message_buffer()
                            
                            buffer.store_pending_message(
                                user_id=session.context.user_id,
                                thread_key=thread_key,
                                message=card_message
                            )
                            
                            logger.info(
                                f"[INTERMEDIATION_LOAD] 🃏 Carte bufferisée (WebSocket pas encore connecté) - "
                                f"thread={thread_key} card_id={last_card_for_display.get('id', 'N/A')} "
                                f"listener_status={listener_status.get('status')}"
                            )
                else:
                    logger.info(
                        f"[INTERMEDIATION_LOAD] ⏸️ Mode intermédiation NON réactivé - "
                        f"thread={thread_key} job_id={job_id} "
                        f"(job terminé ou non démarré, job_status={job_status})"
                    )
            else:
                # Pas de FOLLOW_CARD/FOLLOW_MESSAGE OU CLOSE_INTERMEDIATION détecté APRÈS
                if has_close_after_card:
                    logger.info(
                        f"[INTERMEDIATION_LOAD] ⏭️ Mode normal conservé - "
                        f"thread={thread_key} (CLOSE_INTERMEDIATION détecté APRÈS la FOLLOW_CARD/FOLLOW_MESSAGE)"
                    )
                elif not card_or_follow_message:
                    logger.info(
                        f"[INTERMEDIATION_LOAD] ⏭️ Mode normal - "
                        f"thread={thread_key} (aucun FOLLOW_CARD/FOLLOW_MESSAGE trouvé)"
                    )
                else:
                    logger.info(
                        f"[INTERMEDIATION_LOAD] ⏭️ Mode normal - "
                        f"thread={thread_key} (condition non remplie pour activation)"
                    )
        
        except Exception as e:
            logger.error(
                f"[INTERMEDIATION_LOAD] ❌ Erreur vérification chargement: {e}",
                exc_info=True
            )

    async def _notify_agent_of_waiting_context(
        self,
        session: LLMSession,
        brain,
        collection_name: str,
        thread_key: str,
        job_id: Optional[str],
        message_type: str,
        message: Dict[str, Any]
        ) -> None:
        """Informe l'agent principal qu'un événement CARD/WAITING_MESSAGE attend une action."""

        try:
            listener_info = session.onboarding_listeners.get(thread_key)
            if listener_info is None:
                logger.debug(
                    "[WAITING_CONTEXT] Listener introuvable pour thread=%s, notification ignorée",
                    thread_key
                )
                return

            event_id = (
                message.get("id")
                or message.get("message_id")
                or str(uuid.uuid4())
            )
            received_at = datetime.now(timezone.utc).isoformat()
            normalized_payload = self._decode_waiting_event_payload(message)

            summary_preview = json.dumps(normalized_payload, ensure_ascii=False)[:400]
            context_text = (
                f"Type: {message_type}\n"
                f"Job: {job_id or 'inconnu'}\n"
                f"Événement: {event_id}\n"
                f"Détails (preview): {summary_preview}"
            )

            waiting_context = {
                "event_id": event_id,
                "message_type": message_type,
                "job_id": job_id,
                "received_at": received_at,
                "payload": normalized_payload,
                "summary": context_text
            }

            listener_info.setdefault("waiting_events", []).append(waiting_context)
            listener_info["pending_waiting_event"] = waiting_context
            session.onboarding_listeners[thread_key] = listener_info

            if brain and getattr(brain, "pinnokio_agent", None):
                instruction = (
                    "🟠 **Nouvelle attente application métier**\n"
                    f"{context_text}\n\n"
                    "L'application métier est en pause et attend soit un clic sur la carte, soit une réponse "
                    "terminant par `TERMINATE`. Guide l'utilisateur, rappelle-lui d'ajouter `TERMINATE` à la fin "
                    "de sa réponse et prépare-toi à synthétiser l'échange si nécessaire."
                )

                brain.pinnokio_agent.append_system_log(
                    message_id=f"waiting_ctx_{event_id}",
                    timestamp=received_at,
                    payload=instruction
                )

                logger.info(
                    "[WAITING_CONTEXT] Notification agent envoyée - thread=%s event=%s",
                    thread_key,
                    event_id
                )

        except Exception as exc:
            logger.error(
                "[WAITING_CONTEXT] ❌ Erreur notification agent: %s",
                exc,
                exc_info=True
            )

    def _decode_waiting_event_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise le contenu d'un événement CARD/WAITING_MESSAGE pour logs et outils."""

        content = message.get("content")

        if isinstance(content, dict):
            return content

        if isinstance(content, str):
            try:
                decoded = json.loads(content)
                if isinstance(decoded, dict):
                    return decoded
                return {"value": decoded}
            except json.JSONDecodeError:
                return {"raw": content}

        # Fallback: inclure message brut
        return {
            "raw": content,
            "message": message
        }

    async def _synthesize_and_send_terminate_response(
        self,
        session: LLMSession,
        brain,
        user_id: str,
        collection_name: str,
        thread_key: str,
        user_message: str
        ) -> Optional[str]:
        """Synthétise la réponse TERMINATE et l'envoie au canal métier job_chats."""

        try:
            if brain is None or getattr(brain, "pinnokio_agent", None) is None:
                logger.warning(
                    "[WAITING_TERMINATE] Brain ou agent indisponible pour thread=%s",
                    thread_key
                )
                return None

            listener_info = session.onboarding_listeners.get(thread_key) or {}
            job_id = (
                listener_info.get("job_id")
                or (brain.onboarding_data or {}).get("job_id")
            )

            if not job_id:
                logger.warning(
                    "[WAITING_TERMINATE] job_id introuvable pour thread=%s",
                    thread_key
                )
                return None

            pending_event = listener_info.get("pending_waiting_event")
            if not pending_event:
                waiting_events = listener_info.get("waiting_events", [])
                pending_event = waiting_events[-1] if waiting_events else None

            event_summary = pending_event.get("summary") if pending_event else ""
            waiting_payload = pending_event.get("payload") if pending_event else {}

            user_message_clean = user_message.strip()
            if user_message_clean.upper().endswith("TERMINATE"):
                user_message_clean = user_message_clean[:-9].rstrip()

            instructions = (
                "L'application métier attend une réponse structurée terminant par le mot-clé `TERMINATE`.\n"
                "Analyse la conversation récente, synthétise la réponse de l'utilisateur et remplis l'outil "
                "`SUBMIT_WAITING_RESPONSE` avec: \n"
                "- `response_to_application`: message final à envoyer au système métier (doit se terminer par `TERMINATE`).\n"
                "- `user_summary`: résumé concis (3-4 phrases) de ce que l'utilisateur a fourni.\n"
                "- `context_notes` (optionnel): informations utiles supplémentaires.\n"
                "N'ajoute aucun texte hors de l'appel outil."
            )

            if event_summary:
                instructions += f"\n\nContexte métier: {event_summary}"

            if waiting_payload:
                payload_preview = json.dumps(waiting_payload, ensure_ascii=False)[:400]
                instructions += f"\n\nPayload brut: {payload_preview}"

            if user_message_clean:
                instructions += f"\n\nDernier message utilisateur sans mot-clé: {user_message_clean}"

            summary_tool = [{
                "name": "SUBMIT_WAITING_RESPONSE",
                "description": (
                    "Soumets la réponse finale pour l'application métier. `response_to_application` doit se "
                    "terminer par `TERMINATE`."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "response_to_application": {
                            "type": "string",
                            "description": "Message envoyé côté métier, doit se terminer par TERMINATE"
                        },
                        "user_summary": {
                            "type": "string",
                            "description": "Synthèse des échanges avec l'utilisateur"
                        },
                        "context_notes": {
                            "type": "string",
                            "description": "Informations complémentaires pour le suivi",
                            "nullable": True
                        }
                    },
                    "required": ["response_to_application", "user_summary"]
                }
            }]

            def _noop_callback(**kwargs):
                return {"status": "captured", **kwargs}

            tool_mapping = {"SUBMIT_WAITING_RESPONSE": _noop_callback}

            response = await asyncio.to_thread(
                brain.pinnokio_agent.process_tool_use,
                instructions,
                summary_tool,
                tool_mapping,
                brain.default_size,
                {"type": "tool", "name": "SUBMIT_WAITING_RESPONSE"},
                False,
                brain.default_provider,
                False,
                1024,
                False
            )

            tool_payload, _, used_tool_name, _ = brain.pinnokio_agent.new_extract_tool_use_data(response)

            if not tool_payload or used_tool_name != "SUBMIT_WAITING_RESPONSE":
                logger.warning(
                    "[WAITING_TERMINATE] Outil non déclenché correctement pour thread=%s",
                    thread_key
                )
                return None

            response_text = (tool_payload.get("response_to_application") or "").strip()
            user_summary = (tool_payload.get("user_summary") or "").strip()
            context_notes = (tool_payload.get("context_notes") or "").strip()

            if not response_text:
                logger.warning(
                    "[WAITING_TERMINATE] Champ response_to_application vide pour thread=%s",
                    thread_key
                )
                return None

            if not response_text.endswith("TERMINATE"):
                response_text = f"{response_text.rstrip()} TERMINATE"

            message_id = tool_payload.get("message_id") or str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()

            payload = {
                "id": message_id,
                "message_type": "MESSAGE_PINNOKIO",
                "content": response_text,
                "sender_id": user_id,
                "timestamp": timestamp,
                "read": False,
                "local_processed": False,
                "metadata": {
                    "user_summary": user_summary,
                    "context_notes": context_notes,
                    "waiting_event_id": pending_event.get("event_id") if pending_event else None,
                    "waiting_event_type": pending_event.get("message_type") if pending_event else None
                }
            }

            # Nettoyer metadata des valeurs vides
            payload["metadata"] = {
                k: v for k, v in payload["metadata"].items() if v
            }

            rtdb_path = f"{collection_name}/job_chats/{job_id}/messages/{message_id}"
            self._get_rtdb_ref(rtdb_path).set(payload)

            listener_info["pending_waiting_event"] = None
            listener_info["last_terminate_sent"] = {
                "timestamp": timestamp,
                "message_id": message_id,
                "payload": payload
            }
            session.onboarding_listeners[thread_key] = listener_info

            confirmation_log = (
                "🟢 **Réponse envoyée à l'application métier**\n"
                f"Job: {job_id}\n"
                f"Message ID: {message_id}\n"
                f"Résumé utilisateur: {user_summary or '—'}"
            )

            brain.pinnokio_agent.append_system_log(
                message_id=f"waiting_ctx_ack_{message_id}",
                timestamp=timestamp,
                payload=confirmation_log
            )

            logger.info(
                "[WAITING_TERMINATE] ✅ Réponse envoyée au job %s avec message_id=%s",
                job_id,
                message_id
            )

            return response_text

        except Exception as exc:
            logger.error(
                "[WAITING_TERMINATE] ❌ Erreur lors de la synthèse/émission TERMINATE: %s",
                exc,
                exc_info=True
            )
            return None

    async def _send_onboarding_start_message(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        job_id: str
        ) -> None:
        """
        Envoie automatiquement le premier message assistant pour informer l'utilisateur du démarrage du job.
        
        ⭐ GESTION UI/BACKEND : Suit le même pattern que _resume_workflow_after_lpt
        - Mode UI (user connecté sur thread) : Streaming WebSocket + _process_unified_workflow
        - Mode BACKEND (user déconnecté) : Écriture RTDB directe
        """
        try:
            import uuid
            from datetime import datetime, timezone
            from ..ws_hub import hub
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : RÉCUPÉRER LA SESSION EXISTANTE
            # (Déjà initialisée par start_onboarding_chat)
            # ═══════════════════════════════════════════════════════════
            session_key = f"{user_id}:{collection_name}"
            session = None
            with self._lock:
                session = self.sessions.get(session_key)
            
            if not session:
                logger.warning(
                    f"[ONBOARDING_START_MSG] ⚠️ Session non trouvée pour {session_key}, "
                    f"fallback mode BACKEND"
                )
                # Fallback : Mode BACKEND (écriture RTDB directe)
                session = None
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : DÉTERMINER MODE UI/BACKEND
            # (Même logique que LPT callback)
            # ═══════════════════════════════════════════════════════════
            user_on_active_chat = False
            if session:
                user_on_active_chat = session.is_user_on_specific_thread(thread_key)
            
            mode = "UI" if user_on_active_chat else "BACKEND"
            
            logger.info(
                f"[ONBOARDING_START_MSG] Mode détecté: {mode} - "
                f"user_on_active_chat={user_on_active_chat} thread={thread_key} job_id={job_id}"
            )
            
            # Construire le message informatif pour l'utilisateur
            message_content = (
                f"🎯 **Démarrage du processus d'onboarding**\n\n"
                f"Le job **{job_id}** a été lancé avec succès pour initier votre phase d'onboarding.\n\n"
                f"Je suis là pour vous accompagner tout au long de ce processus. N'hésitez pas à me poser "
                f"des questions ou à me demander de l'aide à tout moment. Je suivrai l'avancement du job "
                f"et vous tiendrai informé des étapes importantes."
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : PRÉPARER MESSAGE RTDB
            # ═══════════════════════════════════════════════════════════
            assistant_message_id = str(uuid.uuid4())
            assistant_timestamp = datetime.now(timezone.utc).isoformat()
            chat_mode = session.context.chat_mode if session else None
            assistant_msg_base = self._get_messages_base_path(
                collection_name, thread_key, chat_mode
            )
            assistant_msg_path = f"{assistant_msg_base}/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : MODE UI - STREAMING WEBSOCKET
            # ═══════════════════════════════════════════════════════════
            if user_on_active_chat and session:
                # ⭐ CRITIQUE : Broadcaster AVANT de créer le message RTDB
                # (Même pattern que _resume_workflow_after_lpt ligne 4448-4469)
                ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
                
                placeholder_event = {
                    "type": "assistant_message_placeholder",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,
                        "thread_key": thread_key,
                        "space_code": collection_name,
                        "timestamp": assistant_timestamp,
                        "triggered_by": "onboarding_start",
                        "mode": mode,
                        "job_id": job_id
                    }
                }
                
                await hub.broadcast(user_id, placeholder_event)
                logger.info(f"[ONBOARDING_START_MSG] ⚡ Signal placeholder envoyé au frontend (message_id={assistant_message_id})")
                
                # Créer message RTDB avec status "streaming" pour activer le streaming UI
                initial_message_data = self.rtdb_formatter.format_ai_message(
                    content="",
                    user_id=user_id,
                    message_id=assistant_message_id,
                    timestamp=assistant_timestamp,
                    metadata={
                        "status": "streaming",
                        "streaming_progress": 0.0,
                        "automation": "onboarding_start",
                        "job_id": job_id,
                        "mode": mode
                    }
                )
                
                assistant_msg_ref.set(initial_message_data)
                logger.info(f"[ONBOARDING_START_MSG] Message RTDB initial créé (status=streaming)")
                
                # ═══════════════════════════════════════════════════════════
                # ÉTAPE 5 : LANCER WORKFLOW UNIFIÉ AVEC STREAMING
                # ═══════════════════════════════════════════════════════════
                result = await self._process_unified_workflow(
                    session=session,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    message=message_content,
                    assistant_message_id=assistant_message_id,
                    assistant_timestamp=assistant_timestamp,
                    enable_streaming=True,  # ← Streaming activé pour Mode UI
                    chat_mode="onboarding_chat",
                    system_prompt=None
                )
                
                if result.get("success"):
                    logger.info(
                        f"[ONBOARDING_START_MSG] ✅ Message streaming terminé avec succès - "
                        f"mode={mode} content_length={len(result.get('content', ''))}"
                    )
                else:
                    logger.error(
                        f"[ONBOARDING_START_MSG] ❌ Échec workflow streaming - "
                        f"mode={mode} error={result.get('error')}"
                    )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 6 : MODE BACKEND - ÉCRITURE RTDB DIRECTE
            # ═══════════════════════════════════════════════════════════
            else:
                # Mode BACKEND : Écriture RTDB directe (comportement original)
                message_data = self.rtdb_formatter.format_ai_message(
                    content=message_content,
                    user_id=user_id,
                    message_id=assistant_message_id,
                    timestamp=assistant_timestamp,
                    metadata={
                        "status": "completed",
                        "automation": "onboarding_start",
                        "job_id": job_id,
                        "mode": mode
                    }
                )
                
                assistant_msg_ref.set(message_data)
                
                logger.info(
                    f"[ONBOARDING_START_MSG] ✅ Message automatique envoyé (BACKEND) - "
                    f"thread={thread_key}, job_id={job_id}, message_id={assistant_message_id}"
                )
            
        except Exception as e:
            logger.error(
                f"[ONBOARDING_START_MSG] ❌ Erreur envoi message automatique: {e}",
                exc_info=True
            )

    def _stop_onboarding_listener(self, session: LLMSession, thread_key: Optional[str] = None) -> None:
        """
        Arrête les écouteurs onboarding pour un thread ou pour tous.
        
        Utilise PubSub Redis uniquement. Supprime simplement l'entrée du registre.
        Le RedisSubscriber continue d'écouter mais ignorera les messages
        si la session n'a plus de listener actif.
        
        Args:
            session: Session LLM contenant les listeners
            thread_key: Thread spécifique à arrêter (None = tous)
        """

        if thread_key:
            listeners = {thread_key: session.onboarding_listeners.get(thread_key)}
        else:
            listeners = session.onboarding_listeners.copy()

        for key, info in listeners.items():
            if not info:
                continue
            
            # Supprimer simplement l'entrée du registre
            # Plus de fermeture de listener RTDB nécessaire (tous en PubSub maintenant)
            session.onboarding_listeners.pop(key, None)
            logger.debug(
                f"[ONBOARDING_LISTENER] 🛑 Arrêt listener PubSub pour thread={key} "
                f"(suppression du registre uniquement)"
            )

    # ═══════════════════════════════════════════════════════════════
    # COEUR MÉTIER UNIFIÉ - UI ET BACKEND
    # ═══════════════════════════════════════════════════════════════

    async def _process_unified_workflow(
        self,
        session: LLMSession,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message: str,
        assistant_message_id: str,
        assistant_timestamp: str,
        enable_streaming: bool = True,
        chat_mode: str = "general_chat",
        system_prompt: str = None
        ):
        """
        Coeur métier unifié pour traitement de messages en mode UI et BACKEND.
        
        ⭐ UNIFICATION : Cette méthode est utilisée par :
        - Mode UI : send_message() avec enable_streaming=True
        - Mode BACKEND : _resume_workflow_after_lpt() avec enable_streaming=False (ou True si user connecté)
        - Mode SCHEDULER (futur) : avec enable_streaming=False
        
        Args:
            session: Session LLM (DOIT avoir user_context, jobs_data chargés)
            user_id: ID utilisateur Firebase
            collection_name: Nom société/collection
            thread_key: Clé du thread de conversation
            message: Message utilisateur ou continuation
            assistant_message_id: ID du message assistant (déjà créé dans RTDB)
            assistant_timestamp: Timestamp du message assistant
            enable_streaming: Si True, broadcast chunks WebSocket (Mode UI)
                             Si False, accumule en silence (Mode BACKEND)
            chat_mode: Mode de chat actif (détermine prompt/outil)
            system_prompt: Prompt système optionnel
        
        Returns:
            dict: {"success": bool, "content": str, ...}
        """
        from ..ws_hub import hub
        from ..pinnokio_agentic_workflow.orchestrator.pinnokio_brain import PinnokioBrain
        
        ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
        messages_base_path = self._get_messages_base_path(collection_name, thread_key, chat_mode)
        accumulated_content = ""
        mode = "UI" if enable_streaming else "BACKEND"
        
        try:
            logger.info(
                f"[UNIFIED_WORKFLOW] 🚀 Démarrage - mode={mode} chat_mode={chat_mode} thread={thread_key} "
                f"streaming={'ON' if enable_streaming else 'OFF'}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : RÉCUPÉRER BRAIN POUR CE THREAD
            # ═══════════════════════════════════════════════════════════
            if thread_key not in session.active_brains:
                error_msg = (
                    f"Brain non trouvé pour thread={thread_key}. "
                    f"load_chat_history() doit être appelé avant."
                )
                logger.error(f"[UNIFIED_WORKFLOW] ❌ {error_msg}")
                raise ValueError(error_msg)
            
            brain = session.active_brains[thread_key]
            logger.info(f"[UNIFIED_WORKFLOW] ✅ Brain récupéré pour thread={thread_key}")

            if self._is_onboarding_like(chat_mode):
                # Charger les données selon le mode
                if chat_mode == "onboarding_chat":
                    await brain.load_onboarding_data()
                elif chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                    # Pour ces modes, le job_id est le thread_key
                    job_id = thread_key
                    await brain.load_job_data(job_id)
            
            # ⭐ DÉFINIR LE THREAD ACTIF (pour workflows d'approbation)
            brain.set_active_thread(thread_key)
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : CRÉER OUTILS (SPT/LPT)
            # ═══════════════════════════════════════════════════════════
            tools, tool_mapping = brain.create_workflow_tools(
                thread_key,
                session,
                chat_mode=chat_mode,
                mode=mode,  # ⭐ Passer le mode (UI/BACKEND) pour rechargement Redis
            )
            logger.info(f"[UNIFIED_WORKFLOW] Outils créés: {len(tools)} outils")
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : NOTIFIER DÉBUT (SI STREAMING ACTIVÉ)
            # ═══════════════════════════════════════════════════════════
            if enable_streaming:
                await hub.broadcast(user_id, {
                    "type": "llm_stream_start",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,
                        "thread_key": thread_key,
                        "space_code": collection_name,
                        "mode": mode
                    }
                })
                logger.info(f"[UNIFIED_WORKFLOW] WebSocket stream_start envoyé")
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : BOUCLE AGENTIC AVEC BUDGET TOKENS
            # ═══════════════════════════════════════════════════════════
            max_turns = 20  # Garde-fou temporaire
            max_tokens_budget = 80000  # Budget principal: 80K tokens
            turn_count = 0
            current_input = message
            mission_completed = False
            
            logger.info(
                f"[UNIFIED_WORKFLOW] Budget tokens: {max_tokens_budget:,} "
                f"(tours max: {max_turns})"
            )
            
            while turn_count < max_turns and not mission_completed:
                turn_count += 1
                
                # ═══════════════════════════════════════════════════════════
                # ⭐ VÉRIFICATION DYNAMIQUE DU MODE WORKFLOW À CHAQUE TOUR
                # ═══════════════════════════════════════════════════════════
                from .workflow_state_manager import get_workflow_state_manager
                workflow_manager = get_workflow_state_manager()
                
                # Vérifier si un workflow est actif pour ce thread
                workflow_state = workflow_manager.get_workflow_state(
                    user_id, collection_name, thread_key
                )
                
                if workflow_state and workflow_state.get("status") in ("running", "paused"):
                    # Mettre à jour le compteur de tour
                    workflow_manager.update_turn(user_id, collection_name, thread_key, turn_count)
                    
                    # Bascule dynamique du mode streaming
                    current_mode = workflow_state.get("mode", "BACKEND")
                    enable_streaming = (current_mode == "UI")
                    
                    # ─── VÉRIFIER SI WORKFLOW PAUSÉ (conversation utilisateur) ───
                    if workflow_state.get("status") == "paused":
                        logger.info(
                            f"[UNIFIED_WORKFLOW] ⏸️ Workflow pausé - "
                            f"en attente de reprise (tour {turn_count})"
                        )
                        
                        # Envoyer signal au frontend si en mode UI
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "WORKFLOW_PAUSED",
                                "channel": ws_channel,
                                "payload": {
                                    "thread_key": thread_key,
                                    "turn": turn_count,
                                    "message": "Workflow en pause - Conversation avec l'utilisateur"
                                }
                            })
                        
                        # Sortir de la boucle, le workflow sera repris par leave_chat ou TERMINATE
                        break
                    
                    # ─── VÉRIFIER SI MESSAGE EN ATTENTE (TERMINATE ou user_left) ───
                    pending = workflow_manager.get_pending_message(
                        user_id, collection_name, thread_key, clear=True
                    )
                    
                    if pending:
                        user_msg = pending.get("message", "")
                        
                        if pending.get("is_terminate") or pending.get("is_user_left"):
                            # ⭐ REMETTRE chat_mode à "task_execution" pour reprendre le workflow
                            if session.context.chat_mode != "task_execution":
                                logger.info(
                                    f"[UNIFIED_WORKFLOW] 🔄 Basculement chat_mode: {session.context.chat_mode} → task_execution "
                                    f"(reprise workflow)"
                                )
                                session.context.chat_mode = "task_execution"
                                # ⭐ Mettre à jour le chat_mode utilisé dans le workflow
                                chat_mode = "task_execution"
                                # Mettre à jour le brain si nécessaire
                                if thread_key in session.active_brains:
                                    brain = session.active_brains[thread_key]
                                    # Réinitialiser le system prompt avec le nouveau chat_mode
                                    brain.initialize_system_prompt(chat_mode=chat_mode)
                                    # Recréer les outils avec le nouveau chat_mode
                                    tools, tool_mapping = brain.create_workflow_tools(
                                        thread_key,
                                        session,
                                        chat_mode=chat_mode,
                                        mode=mode
                                    )
                                    logger.info(
                                        f"[UNIFIED_WORKFLOW] 🔄 Outils recréés avec chat_mode={chat_mode}"
                                    )
                            
                            # Construire pré-prompt de reprise
                            if pending.get("is_user_left"):
                                resume_prompt = f"""
🔄 **REPRISE DU WORKFLOW**

L'utilisateur a quitté le chat. Vous devez reprendre le workflow là où vous l'avez laissé.

**Instructions** :
1. Consultez votre checklist workflow (via UPDATE_STEP si nécessaire)
2. Continuez l'exécution de la prochaine étape
3. Travaillez en mode autonome jusqu'à TERMINATE_TASK
"""
                            else:
                                resume_prompt = f"""
🔄 **REPRISE DU WORKFLOW**

L'utilisateur a demandé la reprise du workflow{f' avec le message suivant: "{user_msg}"' if user_msg else ''}.

**Instructions** :
1. {'Tenez compte du message de l utilisateur si pertinent' if user_msg else 'Reprenez le workflow'}
2. Consultez votre checklist workflow
3. Continuez l exécution là où vous l avez laissée
4. Travaillez jusqu à TERMINATE_TASK
"""
                            
                            # Injecter comme prochain input
                            current_input = resume_prompt
                            
                            logger.info(
                                f"[UNIFIED_WORKFLOW] 🔄 Reprise workflow - "
                                f"reason={pending.get('reason')}, chat_mode={session.context.chat_mode}"
                            )
                            
                            # Signal de reprise
                            if enable_streaming:
                                await hub.broadcast(user_id, {
                                    "type": "WORKFLOW_RESUMED",
                                    "channel": ws_channel,
                                    "payload": {
                                        "thread_key": thread_key,
                                        "turn": turn_count,
                                        "message": "Workflow repris"
                                    }
                                })
                
                # ─── VÉRIFICATION BUDGET TOKENS ───
                try:
                    tokens_before = brain.pinnokio_agent.get_total_context_tokens(
                        brain.default_provider
                    )
                    
                    logger.info(
                        f"[UNIFIED_WORKFLOW] Tour {turn_count}/{max_turns} - "
                        f"Tokens: {tokens_before:,}/{max_tokens_budget:,}"
                    )
                    
                    # Si budget dépassé, générer résumé et réinitialiser
                    if tokens_before >= max_tokens_budget:
                        logger.warning(
                            f"[TOKENS] Budget atteint ({tokens_before:,} tokens) - "
                            f"Réinitialisation contexte"
                        )
                        
                        summary = brain.generate_conversation_summary(
                            thread_key=thread_key,
                            total_tokens_used=tokens_before
                        )
                        
                        tokens_after_reset = brain.reset_context_with_summary(summary)
                        
                        logger.info(
                            f"[TOKENS] Contexte réinitialisé - "
                            f"Avant: {tokens_before:,} → Après: {tokens_after_reset:,}"
                        )
                        
                        tokens_before = tokens_after_reset
                        
                except Exception as e:
                    logger.warning(f"[TOKENS] Erreur calcul tokens: {e}")
                
                # Variables pour détecter TEXT_OUTPUT simple
                tools_used_this_turn = False
                text_generated_this_turn = False
                
                # ─── APPEL AGENT AVEC STREAMING ───
                async for event in brain.pinnokio_agent.process_tool_use_streaming(
                    content=current_input,
                    tools=tools,
                    tool_mapping=tool_mapping,
                    provider=brain.default_provider,
                    size=brain.default_size,  # Utiliser la taille par défaut du brain (REASONING_MEDIUM pour Groq/Kimi K2)
                    max_tokens=2048
                    ):
                    event_type = event.get("type")

                    # ═════════════════════════════════════════════════
                    # CAS 0a : DÉBUT PHASE THINKING
                    # ═════════════════════════════════════════════════
                    if event_type == "thinking_start":
                        logger.info(f"[UNIFIED_WORKFLOW] 🧠 Début phase thinking")

                        # Broadcast SI streaming activé
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "thinking_start",
                                "channel": ws_channel,
                                "payload": {
                                    "message_id": assistant_message_id,
                                    "thread_key": thread_key,
                                    "space_code": collection_name,
                                    "timestamp": event.get("timestamp")
                                }
                            })

                    # ═════════════════════════════════════════════════
                    # CAS 0b : CHUNK THINKING (contenu reasoning)
                    # ═════════════════════════════════════════════════
                    elif event_type == "thinking_chunk":
                        chunk = event.get("chunk")

                        # Broadcast SI streaming activé (optionnel - pour afficher le contenu thinking)
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "thinking_delta",
                                "channel": ws_channel,
                                "payload": {
                                    "message_id": assistant_message_id,
                                    "thread_key": thread_key,
                                    "delta": chunk
                                }
                            })

                    # ═════════════════════════════════════════════════
                    # CAS 0c : FIN PHASE THINKING
                    # ═════════════════════════════════════════════════
                    elif event_type == "thinking_end":
                        logger.info(f"[UNIFIED_WORKFLOW] 🧠 Fin phase thinking")

                        # Broadcast SI streaming activé
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "thinking_end",
                                "channel": ws_channel,
                                "payload": {
                                    "message_id": assistant_message_id,
                                    "thread_key": thread_key,
                                    "space_code": collection_name,
                                    "thinking_content": event.get("thinking_content"),
                                    "timestamp": event.get("timestamp")
                                }
                            })

                    # ═════════════════════════════════════════════════
                    # CAS 1 : TEXTE (streaming normal)
                    # ═════════════════════════════════════════════════
                    elif event_type == "text_chunk":
                        text_generated_this_turn = True
                        chunk = event.get("chunk")
                        accumulated_content += chunk
                        
                        # Broadcast SI streaming activé
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "llm_stream_chunk",
                                "channel": ws_channel,
                                "payload": {
                                    "message_id": assistant_message_id,
                                    "thread_key": thread_key,
                                    "space_code": collection_name,
                                    "chunk": chunk,
                                    "accumulated": accumulated_content
                                }
                            })
                    
                    # ═════════════════════════════════════════════════
                    # CAS 2 : DÉBUT D'UTILISATION D'OUTIL
                    # ═════════════════════════════════════════════════
                    elif event_type == "tool_use_start":
                        tool_name = event.get("tool_name")
                        logger.info(f"[UNIFIED_WORKFLOW] Début outil: {tool_name}")
                        
                        # Broadcast SI streaming activé
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "tool_use_start",
                                "channel": ws_channel,
                                "payload": {
                                    "message_id": assistant_message_id,
                                    "thread_key": thread_key,
                                    "tool_name": tool_name,
                                    "tool_icon": "🔄"
                                }
                            })
                    
                    # ═════════════════════════════════════════════════
                    # CAS 3 : OUTIL UTILISÉ (décision prise)
                    # ═════════════════════════════════════════════════
                    elif event_type == "tool_use":
                        tools_used_this_turn = True
                        
                        tool_name = event.get("tool_name")
                        tool_input = event.get("tool_input")
                        tool_id = event.get("tool_id")
                        
                        logger.info(f"[UNIFIED_WORKFLOW] Outil utilisé: {tool_name}")
                        
                        # ─── TERMINATE_TASK ───
                        if tool_name == "TERMINATE_TASK":
                            conclusion = tool_input.get("conclusion", "")
                            accumulated_content += f"\n\n{conclusion}"

                            if enable_streaming:
                                await hub.broadcast(user_id, {
                                    "type": "llm_stream_chunk",
                                    "channel": ws_channel,
                                    "payload": {
                                        "message_id": assistant_message_id,
                                        "thread_key": thread_key,
                                        "chunk": f"\n\n{conclusion}",
                                        "is_final": True
                                    }
                                })

                            # ⚠️ NOTE: Ne PAS mettre mission_completed = True ici
                            # Il faut attendre le tool_result pour vérifier si la validation a réussi
                            # Si _terminate_blocked est présent dans tool_result, on doit continuer la boucle
                            # La gestion se fait dans la section tool_result (ligne ~6273)
                        
                        # ─── WAIT_ON_LPT ───
                        elif tool_name == "WAIT_ON_LPT":
                            # L'agent demande une pause en attente d'un callback LPT
                            # Le message sera ajouté dans tool_result
                            # Le flag _wait_on_lpt sera géré dans tool_result
                            pass
                        
                        # ─── LPT (tâche longue) ───
                        elif tool_name.startswith("LPT_"):
                            lpt_message = (
                                f"\n\n🔄 Tâche longue {tool_name} lancée en arrière-plan.\n"
                                f"Je continue à être disponible pendant son exécution."
                            )
                            accumulated_content += lpt_message
                            
                            if enable_streaming:
                                await hub.broadcast(user_id, {
                                    "type": "llm_stream_chunk",
                                    "channel": ws_channel,
                                    "payload": {
                                        "message_id": assistant_message_id,
                                        "thread_key": thread_key,
                                        "chunk": lpt_message
                                    }
                                })
                        
                        # ─── SPT (tâche courte) ───
                        else:
                            # SPT exécuté, feedback ajouté automatiquement
                            pass
                    
                    # ═════════════════════════════════════════════════
                    # CAS 4 : RÉSULTAT D'OUTIL
                    # ═════════════════════════════════════════════════
                    elif event_type == "tool_result":
                        tool_name = event.get("tool_name")
                        tool_result = event.get("result")
                        logger.info(f"[UNIFIED_WORKFLOW] Résultat outil reçu")
                        
                        # ─── GESTION TERMINATE_TASK ───
                        if tool_name == "TERMINATE_TASK" and tool_result:
                            # Vérifier si la validation a échoué (_terminate_blocked)
                            if tool_result.get("_terminate_blocked"):
                                # ❌ Validation échouée → TERMINATE refusé, continuer la boucle
                                logger.warning(
                                    f"[UNIFIED_WORKFLOW] ⚠️ TERMINATE_TASK refusé - "
                                    f"Étapes incomplètes détectées, workflow continue"
                                )
                                
                                # Ajouter le message d'erreur au contenu accumulé
                                error_message = tool_result.get("message", "TERMINATE_TASK refusé - Étapes incomplètes")
                                accumulated_content += f"\n\n{error_message}"
                                
                                # Broadcast du message si streaming activé
                                if enable_streaming:
                                    await hub.broadcast(user_id, {
                                        "type": "llm_stream_chunk",
                                        "channel": ws_channel,
                                        "payload": {
                                            "message_id": assistant_message_id,
                                            "thread_key": thread_key,
                                            "chunk": f"\n\n{error_message}",
                                            "is_final": False  # Pas final car workflow continue
                                        }
                                    })
                                
                                # ⚠️ NE PAS mettre mission_completed = True → la boucle continue
                                logger.info(
                                    f"[UNIFIED_WORKFLOW] ✅ Workflow continue - "
                                    f"Agent doit compléter les étapes restantes"
                                )
                            elif tool_result.get("type") == "success" or tool_result.get("success"):
                                # ✅ Validation réussie → TERMINATE autorisé, arrêter la boucle
                                logger.info(f"[UNIFIED_WORKFLOW] ✅ TERMINATE_TASK validé - Arrêt du workflow")
                                
                                # Ajouter le message de succès au contenu accumulé
                                success_message = tool_result.get("message", "Task terminated successfully")
                                accumulated_content += f"\n\n{success_message}"
                                
                                # Broadcast du message si streaming activé
                                if enable_streaming:
                                    await hub.broadcast(user_id, {
                                        "type": "llm_stream_chunk",
                                        "channel": ws_channel,
                                        "payload": {
                                            "message_id": assistant_message_id,
                                            "thread_key": thread_key,
                                            "chunk": f"\n\n{success_message}",
                                            "is_final": True
                                        }
                                    })
                                
                                # Marquer la mission comme complétée
                                mission_completed = True
                                logger.info(f"[UNIFIED_WORKFLOW] ✅ Workflow terminé avec succès")
                        
                        # ─── GESTION WAIT_ON_LPT ───
                        elif tool_name == "WAIT_ON_LPT" and tool_result:
                            # Vérifier le flag _wait_on_lpt pour mettre en pause le workflow
                            if tool_result.get("_wait_on_lpt") or tool_result.get("_terminate_workflow"):
                                logger.info(f"[UNIFIED_WORKFLOW] ⏳ WAIT_ON_LPT détecté - Mise en pause workflow")
                                
                                # Ajouter le message de confirmation au contenu accumulé
                                wait_message = tool_result.get("message", "Workflow en pause - Attente callback LPT")
                                accumulated_content += f"\n\n{wait_message}"
                                
                                # Broadcast du message si streaming activé
                                if enable_streaming:
                                    await hub.broadcast(user_id, {
                                        "type": "llm_stream_chunk",
                                        "channel": ws_channel,
                                        "payload": {
                                            "message_id": assistant_message_id,
                                            "thread_key": thread_key,
                                            "chunk": f"\n\n{wait_message}",
                                            "is_final": True
                                        }
                                    })
                                
                                # Marquer la mission comme complétée (pause propre)
                                mission_completed = True
                                logger.info(f"[UNIFIED_WORKFLOW] ✅ Workflow mis en pause - Attente callback LPT")
                        
                        # Notifier fin d'utilisation d'outil
                        if enable_streaming:
                            await hub.broadcast(user_id, {
                                "type": "tool_use_complete",
                                "channel": ws_channel,
                                "payload": {
                                    "message_id": assistant_message_id,
                                    "thread_key": thread_key,
                                    "tool_name": tool_name,
                                    "success": "error" not in tool_result if tool_result else True
                                }
                            })
                        
                        # Le résultat sera réinjecté dans le prochain tour
                
                # ───────────────────────────────────────────────────────
                # 💳 BILLING (CHAT): Enregistrer l'usage tokens par "bucket" journalier
                # - Compatible multi-instances (Firestore entries/ + agrégats)
                # - Idempotence via entry_id basé sur assistant_message_id + turn
                # ───────────────────────────────────────────────────────
                try:
                    billing_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    mandate_path = (
                        (session.user_context or {}).get("mandate_path")
                        or (getattr(brain, "user_context", None) or {}).get("mandate_path")
                    )

                    # Par défaut: job virtuel journalier pour le chat général
                    if chat_mode == "general_chat":
                        billing_job_id = f"chat:{user_id}:{collection_name}:{billing_date}"
                        billing_project_id = f"chat_{user_id}_{collection_name}_{billing_date}"
                    else:
                        # Pour les modes job-like (router_chat/banker_chat/...), garder une clé stable par thread
                        billing_job_id = thread_key
                        billing_project_id = collection_name

                    brain.pinnokio_agent.load_token_usage_to_db(
                        project_id=billing_project_id,
                        job_id=billing_job_id,
                        workflow_step=f"chat_turn_{turn_count}",
                        file_name=f"assistant_message:{assistant_message_id}",
                        entry_id=f"{assistant_message_id}:{turn_count}",
                        function="chat",
                        thread_key=thread_key,
                        message_id=assistant_message_id,
                        collection_name=collection_name,
                        mandate_path=mandate_path,
                    )
                except Exception as e:
                    logger.warning(
                        "[BILLING] Impossible d'enregistrer token_usage (thread=%s, msg=%s, turn=%s): %s",
                        thread_key,
                        assistant_message_id,
                        turn_count,
                        str(e),
                        exc_info=True,
                    )

                # ─── FIN DU TOUR : Préparer prochain input ───
                if mission_completed:
                    break
                
                # ⚠️ MODE TASK_EXECUTION : Désactiver détection automatique "texte sans outils"
                # En mode task_execution, seuls TERMINATE_TASK et WAIT_ON_LPT peuvent clôturer/pauser
                if chat_mode == "task_execution":
                    # En mode task_execution, on ne termine PAS automatiquement sur texte sans outils
                    # L'agent DOIT utiliser TERMINATE_TASK ou WAIT_ON_LPT explicitement
                    if text_generated_this_turn and not tools_used_this_turn:
                        logger.warning(
                            f"[UNIFIED_WORKFLOW] ⚠️ Mode task_execution: Texte sans outils détecté, "
                            f"mais workflow continue (attente TERMINATE_TASK ou WAIT_ON_LPT)"
                        )
                        # Ne pas mettre mission_completed = True, continuer le workflow
                else:
                    # Mode normal (general_chat, etc.) : Comportement habituel
                    # Si que du texte (pas d'outils) → Mission complétée
                    if text_generated_this_turn and not tools_used_this_turn:
                        logger.info(
                            f"[UNIFIED_WORKFLOW] Texte simple sans outils → "
                            f"Mission complétée"
                        )
                        mission_completed = True
                        break
                
                # Continuer avec feedback des outils
                # (Le feedback est déjà dans l'historique du provider)
                current_input = ""  # Input vide pour continuer avec l'historique
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 5 : ÉCRITURE FINALE RTDB (TOUJOURS, UI ET BACKEND)
            # ═══════════════════════════════════════════════════════════
            assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            
            # ⭐ Utiliser le formatter pour garantir compatibilité UI
            final_message_data = self.rtdb_formatter.format_ai_message(
                content=accumulated_content,
                user_id=user_id,
                message_id=assistant_message_id,
                timestamp=assistant_timestamp,
                metadata={
                    "status": "complete",
                    "streaming_progress": 1.0,
                    "mode": mode,
                    "turns": turn_count,
                    "mission_completed": mission_completed,
                    "completed_at": datetime.now(timezone.utc).isoformat()
                }
            )
            
            assistant_msg_ref.set(final_message_data)
            
            logger.info(
                f"[UNIFIED_WORKFLOW] Message final écrit dans RTDB - "
                f"length={len(accumulated_content)}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 6 : NOTIFIER FIN (SI STREAMING ACTIVÉ)
            # ═══════════════════════════════════════════════════════════
            if enable_streaming:
                await hub.broadcast(user_id, {
                    "type": "llm_stream_complete",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,
                        "thread_key": thread_key,
                        "full_content": accumulated_content,
                        "turns": turn_count,
                        "mission_completed": mission_completed
                    }
                })
                logger.info(f"[UNIFIED_WORKFLOW] WebSocket stream_complete envoyé")
            
            logger.info(
                f"[UNIFIED_WORKFLOW] ✅ Terminé - mode={mode} "
                f"turns={turn_count} content_length={len(accumulated_content)}"
            )
            
            return {
                "success": True,
                "content": accumulated_content,
                "turns": turn_count,
                "mission_completed": mission_completed,
                "mode": mode
            }
            
        except asyncio.CancelledError:
            # Gestion spécifique de l'interruption par l'utilisateur
            logger.info(
                f"[UNIFIED_WORKFLOW] ⏸️ Streaming interrompu par l'utilisateur - "
                f"thread={thread_key} content_length={len(accumulated_content)}"
            )
            
            # Sauvegarder le contenu accumulé jusqu'à l'interruption
            try:
                assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
                assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
                assistant_msg_ref.update({
                    "content": accumulated_content
                })
                logger.info(f"[UNIFIED_WORKFLOW] 💾 Contenu partiel sauvegardé dans RTDB")
            except Exception as update_error:
                logger.error(
                    f"[UNIFIED_WORKFLOW] Erreur sauvegarde contenu partiel: {update_error}"
                )
            
            # Notifier interruption via WebSocket (si streaming activé)
            if enable_streaming:
                try:
                    await hub.broadcast(user_id, {
                        "type": "llm_stream_interrupted",
                        "channel": ws_channel,
                        "payload": {
                            "message_id": assistant_message_id,
                            "thread_key": thread_key,
                            "accumulated_content": accumulated_content,
                            "reason": "user_cancelled"
                        }
                    })
                    logger.info(f"[UNIFIED_WORKFLOW] 📡 Notification interruption envoyée")
                except Exception as broadcast_error:
                    logger.error(
                        f"[UNIFIED_WORKFLOW] Erreur notification interruption: {broadcast_error}"
                    )
            
            # Re-raise pour propager l'annulation
            raise
            
        except Exception as e:
            logger.error(f"[UNIFIED_WORKFLOW] ❌ Erreur: {e}", exc_info=True)
            
            # Marquer comme erreur dans RTDB (toujours)
            try:
                assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
                assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
                assistant_msg_ref.update({
                    "status": "error",
                    "error": str(e),
                    "error_at": datetime.now(timezone.utc).isoformat()
                })
            except Exception as update_error:
                logger.error(
                    f"[UNIFIED_WORKFLOW] Erreur mise à jour erreur RTDB: {update_error}"
                )
            
            # Notifier erreur (si streaming activé)
            if enable_streaming:
                try:
                    await hub.broadcast(user_id, {
                        "type": "llm_stream_error",
                        "channel": ws_channel,
                        "payload": {
                            "message_id": assistant_message_id,
                            "error": str(e)
                        }
                    })
                except Exception:
                    pass
            
            return {
                "success": False,
                "error": str(e),
                "mode": mode
            }
        
        finally:
            # Désenregistrer le stream avec délai (fenêtre de grâce pour stop utilisateur)
            # Le stream reste enregistré pendant 10 secondes après la fin pour permettre
            # à l'utilisateur de cliquer sur "stop" même si le stream est déjà terminé
            try:
                # Lancer le désenregistrement différé en arrière-plan (non-bloquant)
                # Note: asyncio est déjà importé au niveau du module
                asyncio.create_task(
                    self.streaming_controller.unregister_stream(
                        session_key=f"{user_id}:{collection_name}",
                        thread_key=thread_key,
                        delay_seconds=10.0  # Fenêtre de grâce de 10 secondes
                    )
                )
                logger.info(
                    f"[UNIFIED_WORKFLOW] 🧹 Désenregistrement différé programmé - "
                    f"session={user_id}:{collection_name} thread={thread_key} (délai: 10s)"
                )
            except Exception as cleanup_error:
                logger.error(
                    f"[UNIFIED_WORKFLOW] Erreur programmation désenregistrement stream: {cleanup_error}"
                )
    
   
    # ═══════════════════════════════════════════════════════════════
    # MÉTHODES AUXILIAIRES POUR LPT
    # ═══════════════════════════════════════════════════════════════
    
    

    async def handle_approval_response(
        self,
        user_id: str,
        thread_key: str,
        plan_id: str,
        approved: bool,
        collection_name: str = "",
        user_comment: str = ""
        ):
        """
        Appelé quand l'utilisateur répond à une demande d'approbation.
        Cette méthode sera appelée via WebSocket ou RPC depuis Reflex.
        
        Args:
            user_id: Firebase user ID
            thread_key: Chat thread key
            plan_id: Unique plan ID
            approved: True if approved, False if rejected
            collection_name: Company ID (for validation and logging)
            user_comment: Optional user comment explaining the decision
            
        Returns:
            dict: {"success": bool, "error": str (if failure)}
        """
        approval_key = f"{user_id}:{thread_key}:{plan_id}"
        
        # Logging enrichi avec collection_name et commentaire
        log_msg = f"[APPROBATION] {user_id}"
        if collection_name:
            log_msg += f"@{collection_name}"
        log_msg += f" - plan={plan_id} - {'✅ APPROUVÉ' if approved else '❌ REFUSÉ'}"
        logger.info(log_msg)
        
        if user_comment:
            logger.info(f"[APPROBATION] Commentaire utilisateur: {user_comment}")
        
        if not hasattr(self, 'pending_approvals'):
            logger.warning(f"[APPROBATION] Aucune approbation en attente pour: {approval_key}")
            return {"success": False, "error": "No pending approval system initialized"}
        
        future = self.pending_approvals.get(approval_key)
        
        if future and not future.done():
            # Résoudre le Future avec approved ET user_comment
            future.set_result({
                "approved": approved,
                "user_comment": user_comment,
                "collection_name": collection_name
            })
            logger.info(f"[APPROBATION] Réponse enregistrée avec succès: {approval_key}")
            return {"success": True}
        else:
            logger.warning(f"[APPROBATION] Future non trouvée ou déjà terminée: {approval_key}")
            return {"success": False, "error": "No pending approval found or already processed"}
    
    async def _resume_workflow_after_lpt(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        task_id: str,
        task_data: dict,
        lpt_response: dict,
        original_payload: dict,
        user_connected: bool,
        is_planned_task: bool = False
        ):
        """
        Point d'entrée MODE BACKEND/UI : Reprend le workflow après qu'un LPT ait terminé.
        
        ⭐ COMPORTEMENT ADAPTATIF :
        
        CAS 1 - Tâche Planifiée (is_planned_task=True) :
        - Checklist existe → Demander UPDATE_STEP
        - Prompt système avec instructions checklist
        - Mode UI ou BACKEND selon user_connected
        
        CAS 2 - LPT Simple + User Actif (is_planned_task=False + user_connected=True) :
        - Session active → Chat history déjà chargé
        - Message simple sans mention de checklist
        - Pas de prompt système spécial
        - Réponse LPT injectée comme continuation naturelle
        
        CAS 3 - LPT Simple + User Inactif (is_planned_task=False + user_connected=False) :
        - Charger historique RTDB
        - Message simple sauvegardé dans RTDB
        - User verra au retour
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société (collection_name)
            thread_key: Clé du thread de conversation
            task_id: ID de la tâche LPT qui a terminé
            task_data: Données de la tâche (déjà récupérées depuis Firebase)
            lpt_response: Réponse du LPT (status, result, error, etc.)
            original_payload: Payload complet envoyé au LPT (format englobeur)
            user_connected: True si user sur ce thread (Mode UI), False sinon (Mode BACKEND)
            is_planned_task: True si tâche planifiée (avec checklist), False si LPT simple
        """
        # ⭐ CORRECTION : Import de hub pour les broadcasts WebSocket
        from ..ws_hub import hub
        
        messages_base_path = self._get_messages_base_path(company_id, thread_key, None)

        try:
            mode = "UI" if user_connected else "BACKEND"
            
            logger.info(
                f"[WORKFLOW_RESUME] 🚀 MODE {mode} - user={user_id} company={company_id} "
                f"thread={thread_key} task={task_id} is_planned_task={is_planned_task}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 1 : GARANTIR INITIALISATION SESSION (⭐ CRITIQUE)
            # ═══════════════════════════════════════════════════════════
            # ⭐ IMPORTANT: Tâche planifiée = task_execution, LPT simple = general_chat
            resume_chat_mode = "task_execution" if is_planned_task else "general_chat"
            
            session = await self._ensure_session_initialized(
                user_id=user_id,
                collection_name=company_id,
                chat_mode=resume_chat_mode
            )
            
            logger.info(
                f"[WORKFLOW_RESUME] ✅ Session garantie avec données permanentes "
                f"(user_context, jobs_data, jobs_metrics) - chat_mode={resume_chat_mode}"
            )
            messages_base_path = self._get_messages_base_path(
                company_id, thread_key, session.context.chat_mode
            )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : GARANTIR BRAIN POUR CE THREAD
            # ═══════════════════════════════════════════════════════════
            if thread_key not in session.active_brains:
                logger.warning(
                    f"[WORKFLOW_RESUME] ⚠️ Brain non trouvé pour thread={thread_key}, "
                    f"chargement automatique..."
                )
                
                # Charger historique depuis RTDB
                history = await self._load_history_from_rtdb(company_id, thread_key, session.context.chat_mode)
                
                # Créer brain pour ce thread
                load_result = await self.load_chat_history(
                    user_id=user_id,
                    collection_name=company_id,
                    thread_key=thread_key,
                    history=history
                )
                
                if not load_result.get("success"):
                    logger.error(
                        f"[WORKFLOW_RESUME] ❌ Échec création brain: {load_result}"
                    )
                    return
                
                logger.info(f"[WORKFLOW_RESUME] ✅ Brain créé automatiquement")
            else:
                logger.info(f"[WORKFLOW_RESUME] ✅ Brain existant trouvé")
            
            # ⭐ IMPORTANT: Mettre à jour le brain avec le bon chat_mode
            brain = session.active_brains.get(thread_key)
            if brain and is_planned_task:
                brain.initialize_system_prompt(chat_mode="task_execution", jobs_metrics=session.jobs_metrics)
                logger.info(f"[WORKFLOW_RESUME] 🔄 Brain mis à jour avec chat_mode=task_execution")
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 3 : RÉCUPÉRER MISSION DEPUIS FIREBASE (si execution_id)
            # ═══════════════════════════════════════════════════════════
            mission_data = None
            execution_data = None
            task_id = None
            execution_id = None
            mandate_path = None
            
            if brain and self._is_onboarding_like(session.context.chat_mode):
                # Charger les données selon le mode
                if session.context.chat_mode == "onboarding_chat":
                    await brain.load_onboarding_data()
                elif session.context.chat_mode in ("router_chat", "banker_chat", "apbookeeper_chat"):
                    # Pour ces modes, le job_id est le thread_key
                    job_id = thread_key
                    await brain.load_job_data(job_id)
                log_entries = await self._load_onboarding_log_history(
                    brain=brain,
                    collection_name=company_id,
                    session=session,
                    thread_key=thread_key
                )
                await self._ensure_onboarding_listener(
                    session=session,
                    brain=brain,
                    collection_name=company_id,
                    thread_key=thread_key,
                    initial_entries=log_entries
                )

            # Essayer de récupérer les IDs depuis brain.active_task_data
            if brain and hasattr(brain, 'active_task_data') and brain.active_task_data:
                task_id = brain.active_task_data.get("task_id")
                execution_id = brain.active_task_data.get("execution_id")
                mandate_path = brain.active_task_data.get("mandate_path")
                logger.info(f"[WORKFLOW_RESUME] IDs récupérés depuis brain.active_task_data")
            
            # Sinon, essayer depuis le traceability du payload original
            if not (task_id and execution_id):
                traceability = original_payload.get("traceability", {})
                execution_id = traceability.get("execution_id")
                mandate_path = original_payload.get("mandates_path")
                
                # ⭐ Pour les tâches planifiées, thread_key = task_id de la tâche
                # (voir cron_scheduler.py ligne 295: thread_key = task_id)
                if is_planned_task and not task_id:
                    task_id = thread_key
                    logger.info(f"[WORKFLOW_RESUME] task_id déduit du thread_key: {task_id}")
                
                if execution_id and mandate_path:
                    logger.info(f"[WORKFLOW_RESUME] execution_id trouvé dans traceability: {execution_id}")
            
            # Si on a execution_id et mandate_path, récupérer la mission depuis Firebase
            workflow_checklist = None  # ⭐ Variable pour stocker la checklist
            
            if execution_id and mandate_path:
                try:
                    from ..firebase_providers import get_firebase_management
                    fbm = get_firebase_management()
                    
                    # Si on n'a pas task_id, essayer de le trouver via l'execution
                    if not task_id:
                        # L'execution_id contient normalement le task_id
                        # Format: exec_{task_id}_{timestamp}
                        # On doit chercher dans Firebase
                        logger.warning(f"[WORKFLOW_RESUME] task_id manquant, impossible de récupérer mission")
                    else:
                        # Récupérer execution depuis Firebase
                        execution_data = fbm.get_task_execution(mandate_path, task_id, execution_id)
                        
                        if execution_data:
                            mission_data = execution_data.get("mission")
                            # ⭐ NOUVEAU : Récupérer la workflow_checklist
                            workflow_checklist = execution_data.get("workflow_checklist")
                            logger.info(
                                f"[WORKFLOW_RESUME] ✅ Mission récupérée depuis Firebase: "
                                f"task_id={task_id}, execution_id={execution_id}, "
                                f"has_checklist={workflow_checklist is not None}"
                            )
                        else:
                            logger.warning(
                                f"[WORKFLOW_RESUME] ⚠️ Execution non trouvée dans Firebase: "
                                f"task_id={task_id}, execution_id={execution_id}"
                            )
                except Exception as e:
                    logger.warning(f"[WORKFLOW_RESUME] ⚠️ Erreur récupération mission: {e}")
            
            # ⭐ NOUVEAU : Remplir brain.active_task_data si on a les IDs (nécessaire pour UPDATE_STEP)
            if is_planned_task and brain and task_id and execution_id and mandate_path:
                if not brain.active_task_data:
                    brain.active_task_data = {
                        "task_id": task_id,
                        "execution_id": execution_id,
                        "mandate_path": mandate_path,
                        "mission": mission_data
                    }
                    logger.info(
                        f"[WORKFLOW_RESUME] 📝 brain.active_task_data initialisé: "
                        f"task_id={task_id}, execution_id={execution_id}"
                    )
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 4 : CONSTRUIRE PROMPT SYSTÈME + MESSAGE (ADAPTATIF)
            # ═══════════════════════════════════════════════════════════
            
            # Récupérer user_context depuis session
            user_context = session.user_context or {}
            
            # ⭐ COMPORTEMENT ADAPTATIF selon le type de tâche
            if is_planned_task:
                # ═══ CAS 1 : TÂCHE PLANIFIÉE (avec checklist) ═══
                logger.info(f"[WORKFLOW_RESUME] 📋 Tâche planifiée → Prompt avec checklist")
                
                # Construire prompt système de base
                from app.pinnokio_agentic_workflow.orchestrator.system_prompt_principal_agent import build_principal_agent_prompt
                base_system_prompt = build_principal_agent_prompt(
                    user_context=user_context,
                    jobs_metrics=session.jobs_metrics or {}
                )
            
                # Construire le prompt callback LPT avec instructions checklist
                from app.pinnokio_agentic_workflow.orchestrator.system_prompt_lpt_callback import build_lpt_callback_prompt
                lpt_callback_addition = build_lpt_callback_prompt(
                    user_context=user_context,
                    lpt_response=lpt_response,
                    original_payload=original_payload
                )
                
                # Si mission disponible, ajouter le context de la tâche
                if mission_data:
                    task_context_addition = self._build_task_execution_addition(
                        mission=mission_data,
                        last_report=execution_data.get("last_execution_report") if execution_data else None,
                        execution_plan=original_payload.get("traceability", {}).get("execution_plan")
                    )
                    lpt_callback_system_prompt = f"{base_system_prompt}\n\n{task_context_addition}\n\n{lpt_callback_addition}"
                else:
                    lpt_callback_system_prompt = f"{base_system_prompt}\n\n{lpt_callback_addition}"
                
            else:
                # ═══ CAS 2 : LPT SIMPLE (sans checklist) ═══
                logger.info(f"[WORKFLOW_RESUME] 💬 LPT simple → Pas de prompt spécial")
                
                # Pas de prompt système spécial pour LPT simple
                # L'agent continue naturellement la conversation
                lpt_callback_system_prompt = None
            
            logger.info(f"[WORKFLOW_RESUME] ✅ Configuration prompt terminée (is_planned={is_planned_task})")
            
            # Extraire informations pour le message
            task_type = original_payload.get("task_type", "LPT")
            status = lpt_response.get("status", "completed")
            result = lpt_response.get("result", {})
            summary = result.get("summary", "Tâche terminée") if result else "Tâche terminée"
            error = lpt_response.get("error")
            
            # ⭐ CONSTRUCTION MESSAGE ADAPTATIF selon le type
            if is_planned_task:
                # ═══ CAS 1 : TÂCHE PLANIFIÉE → Demander UPDATE_STEP ═══
                
                # ⭐ NOUVEAU : Formater la checklist pour l'afficher à l'agent
                checklist_section = ""
                step_in_progress_id = None
                step_in_progress_name = None
                
                if workflow_checklist and workflow_checklist.get("steps"):
                    steps = workflow_checklist.get("steps", [])
                    total_steps = workflow_checklist.get("total_steps", len(steps))
                    completed_count = sum(1 for s in steps if s.get("status") == "completed")
                    
                    checklist_lines = []
                    for step in steps:
                        step_id = step.get("id", "?")
                        step_name = step.get("name", "Étape sans nom")
                        step_status = step.get("status", "pending")
                        step_message = step.get("message", "")
                        
                        # Icône selon le statut
                        if step_status == "completed":
                            icon = "✅"
                        elif step_status == "in_progress":
                            icon = "🔄"
                            step_in_progress_id = step_id
                            step_in_progress_name = step_name
                        elif step_status == "error":
                            icon = "❌"
                        else:  # pending
                            icon = "⏳"
                        
                        step_line = f"   - {icon} `{step_id}` : {step_name}"
                        if step_message:
                            step_line += f" → {step_message}"
                        checklist_lines.append(step_line)
                    
                    checklist_section = f"""
📋 **ÉTAT ACTUEL DE VOTRE CHECKLIST** ({completed_count}/{total_steps} étapes terminées) :

{chr(10).join(checklist_lines)}

"""
                    logger.info(
                        f"[WORKFLOW_RESUME] 📋 Checklist chargée: {completed_count}/{total_steps} étapes, "
                        f"step_in_progress={step_in_progress_id}"
                    )
                else:
                    checklist_section = """
⚠️ **CHECKLIST NON TROUVÉE** - Consultez votre historique pour retrouver les étapes créées.

"""
                
                # Identifier l'étape à mettre à jour
                step_to_update_hint = ""
                if step_in_progress_id:
                    step_to_update_hint = f"""
🎯 **ÉTAPE À METTRE À JOUR** : `{step_in_progress_id}` ({step_in_progress_name})
   Cette étape était en "in_progress" et correspond au LPT {task_type} qui vient de se terminer.
"""
                
                if status == "completed":
                    continuation_message = f"""
🔄 **RÉPONSE DE L'OUTIL {task_type}**

✅ **{summary}**

---

{checklist_section}{step_to_update_hint}
---

⚠️ **ACTIONS REQUISES** (dans cet ordre) :

1. **METTRE À JOUR LA CHECKLIST** (🔴 OBLIGATOIRE EN PREMIER)
   ```json
   {{
     "step_id": "{step_in_progress_id or 'STEP_X_XXX'}",
     "status": "completed",
     "message": "✅ {summary[:50]}..."
   }}
   ```

2. **ANALYSER ET DÉCIDER DE LA SUITE**
   - Si des étapes restent en "pending" → Marquer la prochaine en "in_progress" et l'exécuter
   - Si TOUTES les étapes sont "completed" → Appeler `TERMINATE_TASK` avec un résumé complet

**Rappel** : Vous avez accès à tous les outils (SPT et LPT) pour continuer le workflow.
                        """
                elif status == "failed":
                    continuation_message = f"""
🔄 **RÉPONSE DE L'OUTIL {task_type}**

❌ **{error or "Échec de l'exécution"}**

---

{checklist_section}{step_to_update_hint}
---

⚠️ **ACTIONS REQUISES** (dans cet ordre) :

1. **METTRE À JOUR LA CHECKLIST** (🔴 OBLIGATOIRE EN PREMIER)
   ```json
   {{
     "step_id": "{step_in_progress_id or 'STEP_X_XXX'}",
     "status": "error",
     "message": "❌ {(error or 'Échec')[:50]}..."
   }}
   ```

2. **ANALYSER ET DÉCIDER**
   - Proposez des actions correctives si possible
   - Ajustez le plan si nécessaire
   - Continuez avec les étapes restantes ou terminez avec un rapport d'échec

**Rappel** : Gérez l'échec de manière proactive et proposez une solution.
                        """
                else:  # partial
                    continuation_message = f"""
🔄 **RÉPONSE DE L'OUTIL {task_type}**

⚠️ **{summary}**

---

{checklist_section}{step_to_update_hint}
---

⚠️ **ACTIONS REQUISES** (dans cet ordre) :

1. **METTRE À JOUR LA CHECKLIST** (🔴 OBLIGATOIRE EN PREMIER)
   ```json
   {{
     "step_id": "{step_in_progress_id or 'STEP_X_XXX'}",
     "status": "completed",
     "message": "⚠️ Partiel: {summary[:40]}..."
   }}
   ```

2. **ANALYSER ET CONTINUER**
   - Expliquez pourquoi le résultat est partiel
   - Proposez des actions pour compléter (relancer, ajuster, etc.)
   - Continuez selon le plan ajusté

**Rappel** : Un résultat partiel nécessite une attention particulière.
                    """
            else:
                # ═══ CAS 2 : LPT SIMPLE → Message simple, pas de checklist ═══
                if status == "completed":
                    # Résultat détaillé si disponible
                    result_details = ""
                    if result and isinstance(result, dict):
                        processed_items = result.get("processed_items", 0)
                        if processed_items:
                            result_details = f"\n\n**Items traités** : {processed_items}"
                    
                    continuation_message = f"✅ {task_type} terminé avec succès.\n\n**Résultat** : {summary}{result_details}"
                
                elif status == "failed":
                    continuation_message = f"❌ {task_type} a échoué.\n\n**Erreur** : {error or 'Erreur inconnue'}"
                
                else:  # partial
                    continuation_message = f"⚠️ {task_type} terminé partiellement.\n\n**Résumé** : {summary}"
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 5 : PRÉPARER MESSAGE RTDB
            # ═══════════════════════════════════════════════════════════
            assistant_message_id = str(uuid.uuid4())
            assistant_timestamp = datetime.now(timezone.utc).isoformat()
            
            assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            
            # ⭐ CRITIQUE : Pour les callbacks LPT, broadcaster AVANT de créer le message RTDB
            # Cela permet à Reflex de créer le placeholder SYNCHRONEMENT avant que les chunks n'arrivent
            if user_connected:
                ws_channel = f"chat:{user_id}:{company_id}:{thread_key}"
                
                placeholder_event = {
                    "type": "assistant_message_placeholder",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,  # ✅ Structure standard : message_id (comme llm_stream_start)
                        "thread_key": thread_key,
                        "space_code": company_id,
                        "timestamp": assistant_timestamp,
                        "triggered_by": "lpt_callback",
                        "mode": mode,
                        "task_id": task_id,
                        "task_type": task_type
                    }
                }
                
                await hub.broadcast(user_id, placeholder_event)
                logger.info(f"[WORKFLOW_RESUME] ⚡ Signal placeholder envoyé au frontend (message_id={assistant_message_id})")
            
            # ⭐ Utiliser le formatter pour garantir compatibilité UI
            initial_message_data = self.rtdb_formatter.format_ai_message(
                content="",
                user_id=user_id,
                message_id=assistant_message_id,
                timestamp=assistant_timestamp,
                metadata={
                    "status": "streaming" if user_connected else "thinking",  # ✅ CORRECTION : Même status que send_message pour activer streaming UI
                    "streaming_progress": 0.0 if user_connected else None,
                    "triggered_by": "lpt_callback",
                    "mode": mode,
                    "task_id": task_id,
                    "task_type": task_type
                }
            )
            
            assistant_msg_ref.set(initial_message_data)
            
            logger.info(f"[WORKFLOW_RESUME] Message RTDB initial créé")
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 6 : LANCER WORKFLOW UNIFIÉ AVEC PROMPT SPÉCIAL
            # ═══════════════════════════════════════════════════════════
            
            # ═══════════════════════════════════════════════════════════
            # ⭐ CRITIQUE: Attendre la fin d'une conversation en cours
            # ═══════════════════════════════════════════════════════════
            # Si l'utilisateur est en train de converser (streaming en cours),
            # on attend la fin de la conversation avant de reprendre le workflow
            import asyncio
            base_session_key = f"{user_id}:{company_id}"
            
            max_wait_seconds = 60  # Attendre max 60 secondes
            wait_interval = 0.5   # Vérifier toutes les 0.5 secondes
            waited = 0
            
            # Utiliser le streaming_controller pour vérifier les streams actifs
            active_streams = await self.streaming_controller.get_active_streams(base_session_key)
            
            while thread_key in active_streams and waited < max_wait_seconds:
                logger.info(
                    f"[WORKFLOW_RESUME] ⏳ Conversation en cours détectée - "
                    f"Attente fin streaming... (waited={waited}s)"
                )
                await asyncio.sleep(wait_interval)
                waited += wait_interval
                # Recharger les streams actifs
                active_streams = await self.streaming_controller.get_active_streams(base_session_key)
            
            if waited > 0:
                if thread_key in active_streams:
                    logger.warning(
                        f"[WORKFLOW_RESUME] ⚠️ Timeout attente conversation - "
                        f"Forçage reprise workflow après {waited}s"
                    )
                else:
                    logger.info(
                        f"[WORKFLOW_RESUME] ✅ Conversation terminée après {waited}s - "
                        f"Reprise workflow"
                    )
            
            # ⭐ IMPORTANT: Enregistrer le workflow dans Redis pour éviter les conflits
            # Si l'utilisateur envoie un message pendant le workflow, is_workflow_running() retournera True
            from .workflow_state_manager import get_workflow_state_manager
            workflow_manager = get_workflow_state_manager()
            
            if is_planned_task:
                workflow_manager.start_workflow(
                    user_id=user_id,
                    company_id=company_id,
                    thread_key=thread_key,
                    initial_mode=mode
                )
                logger.info(f"[WORKFLOW_RESUME] 🚀 Workflow enregistré dans Redis - mode={mode}")
            
            result = None  # Initialiser pour le finally
            try:
                result = await self._process_unified_workflow(
                    session=session,
                    user_id=user_id,
                    collection_name=company_id,
                    thread_key=thread_key,
                    message=continuation_message,
                    assistant_message_id=assistant_message_id,
                    assistant_timestamp=assistant_timestamp,
                    enable_streaming=user_connected,  # ← Streaming conditionnel basé sur connexion user
                    chat_mode=session.context.chat_mode,
                    system_prompt=lpt_callback_system_prompt  # ⭐ NOUVEAU : Prompt système spécial callback
                )
                
                if result.get("success"):
                    logger.info(
                        f"[WORKFLOW_RESUME] ✅ Terminé avec succès - mode={mode} "
                        f"content_length={len(result.get('content', ''))}"
                    )
                else:
                    logger.error(
                        f"[WORKFLOW_RESUME] ❌ Échec workflow - mode={mode} "
                        f"error={result.get('error')}"
                    )
            finally:
                # ⭐ Toujours nettoyer l'état workflow
                if is_planned_task:
                    workflow_manager.end_workflow(
                        user_id=user_id,
                        company_id=company_id,
                        thread_key=thread_key,
                        status="completed" if result and result.get("success") else "error"
                    )
                    logger.info(f"[WORKFLOW_RESUME] 🏁 Workflow nettoyé dans Redis")
            
        except Exception as e:
            logger.error(f"[WORKFLOW_RESUME] ❌ Erreur: {e}", exc_info=True)
            
            # ⭐ Nettoyer le workflow dans Redis en cas d'erreur
            if is_planned_task:
                try:
                    from .workflow_state_manager import get_workflow_state_manager
                    workflow_manager = get_workflow_state_manager()
                    workflow_manager.end_workflow(
                        user_id=user_id,
                        company_id=company_id,
                        thread_key=thread_key,
                        status="error"
                    )
                except Exception as cleanup_error:
                    logger.error(f"[WORKFLOW_RESUME] Erreur nettoyage Redis: {cleanup_error}")
            
            # Tenter de marquer comme erreur dans RTDB
            try:
                assistant_msg_path = f"{messages_base_path}/{assistant_message_id}"
                assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
                assistant_msg_ref.update({
                    "status": "error",
                    "error": str(e),
                    "error_at": datetime.now(timezone.utc).isoformat()
                })
            except Exception as update_error:
                logger.error(
                    f"[WORKFLOW_RESUME] Erreur mise à jour erreur RTDB: {update_error}"
                )
    
    # ═══════════════════════════════════════════════════════════════
    # SYSTÈME D'APPROBATION GÉNÉRIQUE VIA CARTES INTERACTIVES
    # ═══════════════════════════════════════════════════════════════
    
    async def request_approval_with_card(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        card_type: str,
        card_params: Dict[str, Any],
        timeout: int = 900,
        assistant_message_id: str = None
        ) -> Dict[str, Any]:
        """
        Workflow complet d'approbation via carte interactive.
        
        🔄 FLUX COMPLET :
        1. Construction carte (via ApprovalCardBuilder)
        2. Génération message_id unique
        3. Envoi WebSocket (type: "CARD")
        4. Sauvegarde RTDB (persistence)
        5. Création Future (attente réponse)
        6. Attente avec timeout (15 min par défaut)
        7. Résolution Future (via RPC send_card_response)
        8. Mise à jour RTDB (status: responded/timeout)
        9. Retour résultat
        
        Args:
            user_id: ID Firebase utilisateur
            collection_name: ID société (space_code)
            thread_key: Clé du thread de chat
            card_type: Type de carte ('approval_card', 'text_modification_approval')
            card_params: Paramètres spécifiques à la carte
                Exemple approval_card:
                {
                    "title": "Confirmer l'action",
                    "subtitle": "Saisie de 5 factures",
                    "text": "Détails...",
                    "input_label": "Commentaire optionnel",
                    "button_text": "Approuver"
                }
                
                Exemple text_modification_approval:
                {
                    "context_type": "router",
                    "original_text": "...",
                    "operations_log": [...],
                    "final_text": "...",
                    "warnings": [...]
                }
            timeout: Timeout en secondes (défaut: 900s = 15 min)
            assistant_message_id: ID message assistant (pour lien visuel)
            
        Returns:
            {
                "approved": bool,
                "action": str,  # 'approve_four_eyes' | 'reject_four_eyes'
                "user_message": str,
                "card_message_id": str,
                "responded_at": str (ISO),
                "timeout": bool
            }
        
        Raises:
            ValueError: Si card_type inconnu
            Exception: Si erreur construction/envoi
        """
        from ..ws_hub import hub
        
        try:
            logger.info(
                f"[APPROVAL_CARD] 🃏 Demande approbation - "
                f"type={card_type}, thread={thread_key}"
            )
            session_key = f"{user_id}:{collection_name}"
            with self._lock:
                session = self.sessions.get(session_key)

            chat_mode = session.context.chat_mode if session else None
            messages_base_path = self._get_messages_base_path(
                collection_name, thread_key, chat_mode
            )
            
            # ═══ ÉTAPE 1 : Construction de la carte ═══
            builder = ApprovalCardBuilder()
            
            if card_type == "approval_card":
                card_content = builder.build_approval_card(
                    card_id=card_type,
                    **card_params
                )
            elif card_type == "task_creation_approval":
                # Carte d'approbation de création de tâche (même format que approval_card)
                card_content = builder.build_approval_card(
                    card_id=card_type,
                    execution_mode=card_params.get("execution_mode"),  # ✅ Passer le mode d'exécution
                    **{k: v for k, v in card_params.items() if k != "execution_mode"}
                )
            elif card_type == "text_modification_approval":
                card_content = builder.build_text_modification_card(**card_params)
            else:
                raise ValueError(f"Type de carte inconnu: {card_type}")
            
            logger.info(f"[APPROVAL_CARD] ✅ Carte construite: {card_type}")
            
            # ═══ ÉTAPE 2 : Génération IDs ═══
            card_message_id = f"card_{uuid.uuid4().hex[:12]}"
            approval_key = f"{user_id}:{thread_key}:{card_message_id}"
            
            # ═══ ÉTAPE 3 : Création état d'approbation dans Redis ═══
            from .approval_state_manager import get_approval_state_manager
            approval_manager = get_approval_state_manager()
            
            approval_manager.create_pending_approval(
                user_id=user_id,
                thread_key=thread_key,
                card_message_id=card_message_id,
                card_type=card_type,
                card_params=card_params,
                timeout=timeout
            )
            
            logger.info(
                f"[APPROVAL_CARD] ✅ Approbation créée dans Redis: {approval_key} "
                f"(timeout={timeout}s)"
            )
            
            # ═══ ÉTAPE 4 : Construction message WebSocket ═══
            ws_message = {
                "type": "CARD",
                "thread_key": thread_key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message_id": card_message_id,
                "content": json.dumps(card_content)  # ← IMPORTANT: JSON stringifié
            }
            
            # ═══ ÉTAPE 5 : Envoi WebSocket ═══
            ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
            
            await hub.broadcast(user_id, {
                "type": "CARD",  # ✅ Type explicite et cohérent avec llm_stream_*, tool_use_*
                "channel": ws_channel,
                "payload": ws_message
            })
            
            logger.info(f"[APPROVAL_CARD] 📡 Carte envoyée via WebSocket")
            
            # ═══ ÉTAPE 6 : Sauvegarde RTDB (OBLIGATOIRE pour persistence) ═══
            rtdb_path = f"{messages_base_path}/{card_message_id}"
            rtdb_ref = self._get_rtdb_ref(rtdb_path)
            
            rtdb_ref.set({
                **ws_message,
                "role": "assistant",  # ← Pour cohérence avec messages chat
                "status": "pending_approval",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "timeout_at": (datetime.now(timezone.utc) + timedelta(seconds=timeout)).isoformat(),
                "card_type": card_type
            })
            
            logger.info(f"[APPROVAL_CARD] 💾 Carte sauvegardée dans RTDB: {rtdb_path}")
            
            # ═══ ÉTAPE 6.5 : Envoi notification de message direct ═══
            notif_message_id = None
            try:
                from ..firebase_providers import FirebaseRealtimeChat
                
                # Récupérer le nom du thread
                realtime = FirebaseRealtimeChat()
                
                preferred_mode = self._resolve_messages_container(chat_mode)
                chat_name = realtime.get_thread_name(
                    space_code=collection_name,
                    thread_key=thread_key,
                    mode=preferred_mode
                )

                if not chat_name and preferred_mode != 'chats':
                    chat_name = realtime.get_thread_name(
                        space_code=collection_name,
                        thread_key=thread_key,
                        mode='chats'
                    )

                if not chat_name:
                    chat_name = realtime.get_thread_name(
                        space_code=collection_name,
                        thread_key=thread_key,
                        mode='job_chats'
                    )
                
                # Si toujours pas trouvé, fallback sur thread_key
                if not chat_name:
                    chat_name = thread_key
                    logger.warning(
                        f"[APPROVAL_CARD] ⚠️ thread_name non trouvé dans 'chats' ni 'job_chats', "
                        f"utilisation de thread_key comme fallback"
                    )
                
                # Construire le message de notification
                direct_message_notif = {
                    "file_name": f"Approbation - {card_type}",
                    "job_id": card_message_id,
                    "file_id": None,
                    "function_name": "Chat",
                    "collection_id": collection_name,
                    "status": "Action required",
                    "timestamp": str(datetime.now()),
                    "chat_mode": chat_mode or "general_chat",
                    "thread_key": thread_key,
                    "chat_name": chat_name  # ✅ Utiliser le thread_name récupéré
                }
                
                notif_message_id = realtime.send_direct_message(user_id, user_id, direct_message_notif)
                
                logger.info(
                    f"[APPROVAL_CARD] 🔔 Notification envoyée - "
                    f"notif_id={notif_message_id}"
                )
            except Exception as notif_error:
                logger.warning(
                    f"[APPROVAL_CARD] ⚠️ Échec envoi notification: {notif_error}"
                )
                # Continuer même si notification échoue
            
            # ═══ ÉTAPE 7 : Attente réponse avec polling Redis ═══
            try:
                logger.info(
                    f"[APPROVAL_CARD] ⏳ Attente réponse utilisateur via Redis polling "
                    f"(timeout={timeout}s)..."
                )
                
                # Polling Redis avec asyncio.sleep()
                start_time = datetime.now(timezone.utc)
                poll_interval = 1.0  # Vérifier toutes les secondes
                result = None
                
                while True:
                    # Vérifier l'état dans Redis
                    state = approval_manager.get_approval_state(
                        user_id, thread_key, card_message_id
                    )
                    
                    if state and state.get("status") != "pending":
                        # Approbation résolue !
                        approved = state.get("status") == "approved"
                        result = {
                            "approved": approved,
                            "action": state.get("action"),
                            "user_message": state.get("user_message", ""),
                            "card_name": card_type,
                            "card_message_id": card_message_id,
                            "collection_name": collection_name,
                            "responded_at": state.get("responded_at")
                        }
                        
                        logger.info(
                            f"[APPROVAL_CARD] ✅ Réponse reçue: approved={approved}, "
                            f"action={result['action']}"
                        )
                        break
                    
                    # Vérifier timeout
                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                    if elapsed >= timeout:
                        # Timeout atteint
                        approval_manager.mark_timeout(user_id, thread_key, card_message_id)
                        
                        logger.warning(
                            f"[APPROVAL_CARD] ⏰ Timeout atteint après {elapsed:.1f}s"
                        )
                        result = None
                        break
                    
                    # Attendre avant prochaine vérification
                    await asyncio.sleep(poll_interval)
                
                if result:
                    # Mise à jour RTDB
                    rtdb_ref.update({
                        "status": "responded",
                        "responded_at": result.get("responded_at"),
                        "action": result.get("action"),
                        "user_message": result.get("user_message", "")
                })
                
                logger.info(f"[APPROVAL_CARD] 💾 RTDB mis à jour: status=responded")
                
                return result
                
            except Exception as wait_error:
                # Erreur pendant le polling
                logger.error(
                    f"[APPROVAL_CARD] ❌ Erreur pendant polling: {wait_error}",
                    exc_info=True
                )
                
                # Marquer comme timeout dans RTDB
                try:
                    rtdb_ref.update({
                        "status": "error",
                        "error_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(wait_error)
                    })
                except Exception:
                    pass
                
                return {
                    "approved": False,
                    "timeout": False,
                    "error": True,
                    "card_message_id": card_message_id,
                    "reason": str(wait_error)
                }
                
            finally:
                # Nettoyer (optionnel, le TTL Redis s'en chargera)
                pass
                
                # Supprimer la notification de message direct
                if notif_message_id:
                    try:
                        from ..firebase_providers import FirebaseRealtimeChat
                        realtime = FirebaseRealtimeChat()  # ✅ Singleton - pas d'argument
                        realtime.delete_direct_message(user_id, notif_message_id)
                        logger.info(
                            f"[APPROVAL_CARD] 🗑️ Notification supprimée - "
                            f"notif_id={notif_message_id}"
                        )
                    except Exception as del_error:
                        logger.warning(
                            f"[APPROVAL_CARD] ⚠️ Échec suppression notification: {del_error}"
                        )
        
        except Exception as e:
            logger.error(
                f"[APPROVAL_CARD] ❌ Erreur workflow approbation: {e}",
                exc_info=True
            )
            raise
    
    async def send_card_response(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        card_name: str,
        card_message_id: str,
        action: str,
        user_message: str = "",
        message_data: Dict[str, Any] = None
        ) -> Dict[str, Any]:
        """
        Point de terminaison RPC pour réception de réponse carte.
        
        Appelé par Reflex via LLM.send_card_response.
        
        Args:
            user_id: ID Firebase utilisateur
            collection_name: ID société
            thread_key: Clé du thread
            card_name: Type de carte (ex: 'approval_card', 'klk_router_approval_card')
            card_message_id: ID du message RTDB
            action: Action utilisateur ('approve_four_eyes', 'answer_pinnokio', etc.)
            user_message: Commentaire optionnel
            message_data: Données complètes de la carte (pour intermédiation) - Format CARD_CLICKED complet
            
        Returns:
            {"success": bool, "error": str (si échec)}
        """
        approval_key = f"{user_id}:{thread_key}:{card_message_id}"
        
        logger.info(
            f"[CARD_RESPONSE] 📥 Réception réponse - "
            f"card={card_name}, action={action}, key={approval_key}"
        )
        
        # ⭐ NOUVEAU : Détecter le mode intermédiation
        if message_data:
            logger.info(f"[CARD_RESPONSE] 🔄 Mode intermédiation détecté - message_data fourni")
        
        # ═══════════════════════════════════════════════════════════
        # ÉTAPE 1 : VÉRIFIER SI MODE ONBOARDING_CHAT
        # ═══════════════════════════════════════════════════════════
        try:
            session_key = f"{user_id}:{collection_name}"
            with self._lock:
                session = self.sessions.get(session_key)
            
            if session and self._is_onboarding_like(session.context.chat_mode):
                # ═══ MODE ONBOARDING/ROUTER/BANKER : Envoyer à l'application métier ═══
                listener_info = session.onboarding_listeners.get(thread_key)
                if not listener_info:
                    logger.warning(
                        f"[CARD_RESPONSE_ONBOARDING] ⚠️ Listener introuvable pour thread={thread_key}"
                    )
                    return {"success": False, "error": "Onboarding listener not found"}
                
                job_id = listener_info.get("job_id")
                if not job_id:
                    logger.warning(
                        f"[CARD_RESPONSE_ONBOARDING] ⚠️ job_id introuvable pour thread={thread_key}"
                    )
                    return {"success": False, "error": "Job ID not found"}
                
                # ⭐ MODE INTERMÉDIATION : Transférer message_data TEL QUEL (sans transformation)
                if message_data:
                    logger.info(
                        f"[CARD_RESPONSE_INTERMEDIATION] 🔄 Transfert message_data complet (sans transformation) - "
                        f"card={card_name}, job_id={job_id}"
                    )
                    # ✅ Utiliser le message_data tel quel (déjà au bon format depuis Reflex)
                    card_response_data = message_data
                    message_id = card_response_data.get("message", {}).get("name", str(uuid.uuid4()))
                else:
                    # ⚠️ MODE LEGACY : Construire le payload (pour compatibilité avec anciennes cartes)
                    logger.info(
                        f"[CARD_RESPONSE_LEGACY] 🔧 Construction du payload (mode legacy) - "
                        f"card={card_name}, action={action}"
                    )
                    message_id = str(uuid.uuid4())
                    timestamp = datetime.now(timezone.utc).isoformat()
                    action_status = "APPROUVÉ" if "approve" in action else "REFUSÉ"
                    
                    card_response_data = {
                        "type": "CARD_CLICKED",
                        "threadKey": thread_key,
                        "message": {
                            "cardsV2": [{
                                "cardId": card_name,
                                "card": {
                                    "header": {
                                        "title": "Réponse de validation",
                                        "subtitle": f"Action: {action_status}"
                                    },
                                    "sections": [{
                                        "widgets": [{
                                            "textParagraph": {
                                                "text": user_message or ""
                                            }
                                        }]
                                    }]
                                }
                            }]
                        },
                        "common": {
                            "formInputs": {
                                "user_message": {
                                    "stringInputs": {
                                        "value": [user_message or ""]
                                    }
                                },
                                "action": {
                                    "stringInputs": {
                                        "value": [action]
                                    }
                                }
                            },
                            "invokedFunction": action
                        },
                        "message_type": "CARD_CLICKED_PINNOKIO",
                        "timestamp": timestamp,
                        "sender_id": user_id,
                        "read": False
                    }
                
                # Envoyer dans job_chats/{job_id}/messages
                rtdb_path = f"{collection_name}/job_chats/{job_id}/messages/{message_id}"
                self._get_rtdb_ref(rtdb_path).set(card_response_data)
                
                # Déterminer le mode pour les logs
                mode_label = session.context.chat_mode if session else "onboarding"
                transfer_mode = "intermediation" if message_data else "legacy"
                logger.info(
                    f"[CARD_RESPONSE] ✅ Réponse carte envoyée à application métier - "
                    f"chat_mode={mode_label}, transfer={transfer_mode}, job_id={job_id}, "
                    f"message_id={message_id}, action={action}"
                )
                
                return {"success": True, "message_id": message_id, "mode": mode_label, "transfer": transfer_mode}
            
            # ═══════════════════════════════════════════════════════════
            # ÉTAPE 2 : MODE GENERAL_CHAT (mise à jour Redis)
            # ═══════════════════════════════════════════════════════════
            from .approval_state_manager import get_approval_state_manager
            approval_manager = get_approval_state_manager()
            
            # Résoudre l'approbation dans Redis
            success = approval_manager.resolve_approval(
                user_id=user_id,
                thread_key=thread_key,
                card_message_id=card_message_id,
                action=action,
                user_message=user_message
            )
            
            if success:
                logger.info(
                    f"[CARD_RESPONSE] ✅ Approbation résolue dans Redis - "
                    f"action={action}, comment={'Yes' if user_message else 'No'}"
                )
                return {"success": True}
            else:
                logger.warning(
                    f"[CARD_RESPONSE] ⚠️ Approbation introuvable ou déjà résolue: {approval_key}"
                )
                return {
                    "success": False,
                    "error": "No pending approval found or already processed"
                }
                
        except Exception as e:
            logger.error(
                f"[CARD_RESPONSE] ❌ Erreur traitement réponse carte: {e}",
                exc_info=True
            )
            return {
                "success": False,
                "error": str(e)
            }


# Singleton pour le gestionnaire LLM
_llm_manager: Optional[LLMManager] = None

def get_llm_manager() -> LLMManager:
    """Récupère l'instance singleton du LLM Manager."""
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager


