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
from typing import Dict, Optional, Any
from datetime import datetime, timezone
from ..llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize
from .llm_context import LLMContext
from .rtdb_message_formatter import RTDBMessageFormatter

logger = logging.getLogger("llm_service.manager")


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
        """Arrête un stream spécifique."""
        with self._lock:
            if session_key not in self.active_streams:
                return False
            
            if thread_key not in self.active_streams[session_key]:
                return False
            
            stream_info = self.active_streams[session_key][thread_key]
            
            # Arrêter la tâche
            if not stream_info["task"].done():
                stream_info["task"].cancel()
                logger.info(f"Stream arrêté: {session_key}:{thread_key}")
            
            # Marquer comme interrompu
            stream_info["status"] = "interrupted"
            stream_info["interrupted_at"] = datetime.now(timezone.utc)
            
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
    
    async def unregister_stream(self, session_key: str, thread_key: str):
        """Désenregistre un stream terminé."""
        with self._lock:
            if session_key in self.active_streams and thread_key in self.active_streams[session_key]:
                del self.active_streams[session_key][thread_key]
                if not self.active_streams[session_key]:
                    del self.active_streams[session_key]
                logger.info(f"Stream désenregistré: {session_key}:{thread_key}")
    
    async def get_active_streams(self, session_key: str) -> Dict[str, Any]:
        """Retourne les streams actifs d'une session."""
        with self._lock:
            return self.active_streams.get(session_key, {}).copy()


class LLMSession:
    """Session LLM isolée pour un utilisateur/société.
    
    Gère l'agent BaseAIAgent et l'historique des conversations pour tous les threads
    de cet utilisateur dans cette société.
    """
    
    def __init__(self, session_key: str, context: LLMContext):
        self.session_key = session_key  # user_id:collection_name
        self.context = context
        self.agent: Optional[BaseAIAgent] = None
        
        # Lock pour cette session spécifique (pas de conflit entre utilisateurs)
        self._lock = threading.Lock()
        
        # Historique par thread de conversation
        self.conversations: Dict[str, list] = {}
        
        # Tâches actives par thread
        self.active_tasks: Dict[str, list] = {}
        
        # État par thread
        self.thread_states: Dict[str, str] = {}
        
        # Métriques
        self.created_at = datetime.now(timezone.utc)
        self.last_activity: Dict[str, datetime] = {}
        self.response_times: Dict[str, list] = {}
    
    async def initialize_agent(self):
        """Initialise l'agent BaseAIAgent avec le contexte."""
        try:
            logger.info(f"Initialisation BaseAIAgent pour session {self.session_key}")
            
            # Initialiser BaseAIAgent avec les paramètres du contexte
            self.agent = BaseAIAgent(
                collection_name=self.context.collection_name,
                dms_system=self.context.dms_system,
                dms_mode=self.context.dms_mode,
                firebase_user_id=self.context.user_id
            )
            
            # Enregistrer les providers par défaut
            # Note: À adapter selon vos besoins - ici on initialise Anthropic par défaut
            try:
                from ..llm.klk_agents import NEW_Anthropic_Agent
                anthropic_instance = NEW_Anthropic_Agent()
                self.agent.register_provider(ModelProvider.ANTHROPIC, anthropic_instance)
                logger.info("Provider Anthropic enregistré")
            except Exception as e:
                logger.warning(f"Impossible d'enregistrer Anthropic: {e}")
            
            # Définir les valeurs par défaut
            self.agent.default_provider = ModelProvider.ANTHROPIC
            self.agent.default_model_size = ModelSize.MEDIUM
            
            logger.info(f"Agent LLM initialisé pour session {self.session_key}")
            
        except Exception as e:
            logger.error(f"Erreur initialisation agent: {e}", exc_info=True)
            raise
    
    def update_context(self, **kwargs):
        """Met à jour le contexte dynamiquement."""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        
        # Si DMS change, réinitialiser l'agent
        if 'dms_system' in kwargs or 'dms_mode' in kwargs:
            if self.agent:
                self.agent._initialize_dms(
                    self.context.dms_mode,
                    self.context.dms_system,
                    self.context.user_id
                )
    
    def add_user_message(self, thread_key: str, message: str):
        """Ajoute un message utilisateur à l'historique d'un thread."""
        if thread_key not in self.conversations:
            self.conversations[thread_key] = []
        
        self.conversations[thread_key].append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        self.last_activity[thread_key] = datetime.now(timezone.utc)
    
    async def process_message_streaming(
        self,
        thread_key: str,
        message: str,
        system_prompt: str = None
    ):
        """Traite un message et yield les chunks de réponse.
        
        Yields:
            dict: {"content": str, "index": int, "is_final": bool, "tool_calls": list}
        """
        try:
            self.thread_states[thread_key] = "processing"
            start_time = datetime.now(timezone.utc)
            
            # Mettre à jour le prompt système si fourni
            if system_prompt and self.agent:
                logger.info(f"Mise à jour system_prompt: {system_prompt[:100]}...")
                self.agent.update_system_prompt(system_prompt)
            
            if not self.agent:
                raise Exception("Agent non initialisé")
            
            # Utiliser le vrai streaming depuis l'agent Anthropic
            logger.info(f"Utilisation du vrai streaming Anthropic...")
            
            # Vérifier que l'agent existe
            if not self.agent:
                logger.error("Agent non initialisé !")
                raise Exception("Agent non initialisé")
            
            logger.info(f"Agent trouvé: {type(self.agent)}")
            
            # Vérifier que la méthode existe
            if not hasattr(self.agent, 'process_text_streaming'):
                logger.error("Méthode process_text_streaming non trouvée !")
                raise Exception("Méthode process_text_streaming non trouvée")
            
            logger.info("Méthode process_text_streaming trouvée")
            
            # Ajouter le message utilisateur à l'historique
            logger.info("Ajout du message utilisateur à l'historique...")
            self.agent.add_user_message(message)
            logger.info("Message utilisateur ajouté")
            
            # Utiliser le streaming via BaseAIAgent
            logger.info("Début du streaming via BaseAIAgent...")
            async for chunk in self.agent.process_text_streaming(
                content=message,
                max_tokens=1024
            ):
                chunk_content = chunk.get("content", "")
                is_final = chunk.get("is_final", False)
                
                logger.info(f"Chunk streaming reçu: '{chunk_content[:50]}...' (final: {is_final})")
                
                yield {
                    "content": chunk_content,
                    "index": 0,  # Pas d'index pour le vrai streaming
                    "is_final": is_final,
                    "tool_calls": None
                }
                
                # Si c'est le chunk final, on peut sortir
                if is_final:
                    logger.info(f"Streaming terminé")
                    break
            
            # Ajouter réponse à l'historique (le contenu est déjà dans l'agent)
            # L'agent a déjà ajouté la réponse complète via add_ai_message
            logger.info(f"Réponse ajoutée à l'historique de l'agent")
            
            # Métriques
            duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            if thread_key not in self.response_times:
                self.response_times[thread_key] = []
            self.response_times[thread_key].append(duration_ms)
            
            self.thread_states[thread_key] = "idle"
            
        except Exception as e:
            self.thread_states[thread_key] = "error"
            logger.error(f"Erreur process_message_streaming: {e}", exc_info=True)
            raise
    
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
    
    def get_token_stats(self, thread_key: str) -> dict:
        """Retourne les stats de tokens depuis BaseAIAgent."""
        if not self.agent:
            return {"prompt": 0, "completion": 0, "total": 0}
        
        try:
            usage = self.agent.get_token_usage_by_provider()
            
            # Agréger les stats de tous les providers
            total_input = sum(p.get('total_input_tokens', 0) for p in usage.values())
            total_output = sum(p.get('total_output_tokens', 0) for p in usage.values())
            
            return {
                "prompt": total_input,
                "completion": total_output,
                "total": total_input + total_output
            }
        except Exception:
            return {"prompt": 0, "completion": 0, "total": 0}
    
    def get_last_response_duration_ms(self, thread_key: str) -> int:
        """Retourne la durée de la dernière réponse en ms."""
        if thread_key in self.response_times and self.response_times[thread_key]:
            return int(self.response_times[thread_key][-1])
        return 0


class LLMManager:
    """Gestionnaire LLM utilisant Firebase Realtime Database."""
    
    def __init__(self):
        self.sessions: Dict[str, LLMSession] = {}
        self._lock = threading.Lock()
        self.rtdb_formatter = RTDBMessageFormatter()
        self.streaming_controller = StreamingController()
    
    def _get_rtdb_ref(self, path: str):
        """Obtient une référence Firebase RTDB."""
        from ..listeners_manager import _get_rtdb_ref
        return _get_rtdb_ref(path)
    
    async def initialize_session(
        self,
        user_id: str,
        collection_name: str,
        dms_system: str = "google_drive",
        dms_mode: str = "prod",
        chat_mode: str = "general_chat"
    ) -> dict:
        """Initialise une session LLM pour un utilisateur/société."""
        try:
            logger.info(f"=== DÉBUT initialize_session ===")
            logger.info(f"Paramètres: user_id={user_id}, collection_name={collection_name}")
            logger.info(f"Chat mode: {chat_mode}")
            
            with self._lock:
                base_session_key = f"{user_id}:{collection_name}"
                
                logger.info(f"Initialisation session LLM: {base_session_key}")
                
                # Vérifier si session existe déjà
                if base_session_key in self.sessions:
                    session = self.sessions[base_session_key]
                    # Mettre à jour le contexte si nécessaire
                    if (session.context.dms_system != dms_system or 
                        session.context.chat_mode != chat_mode):
                        session.update_context(
                            dms_system=dms_system,
                            dms_mode=dms_mode,
                            chat_mode=chat_mode
                        )
                    
                    logger.info(f"Session existante réutilisée: {base_session_key}")
                    return {
                        "success": True,
                        "session_id": base_session_key,
                        "status": "existing",
                        "message": "Session LLM réutilisée"
                    }
                
                # Créer nouvelle session
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
                
                # Initialiser l'agent
                logger.info(f"Initialisation de l'agent...")
                await session.initialize_agent()
                logger.info(f"Agent initialisé avec succès")
                
                # Stocker en cache
                logger.info(f"Stockage de la session en cache...")
                self.sessions[base_session_key] = session
                logger.info(f"Session stockée en cache")
                
                logger.info(f"Nouvelle session créée: {base_session_key}")
                logger.info(f"=== FIN initialize_session ===")
                return {
                    "success": True,
                    "session_id": base_session_key,
                    "status": "created",
                    "message": "Session LLM initialisée avec succès"
                }
                
        except Exception as e:
            logger.error(f"Erreur initialisation session LLM: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec de l'initialisation LLM"
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
        """Envoie un message à l'agent LLM et écrit la réponse dans Firebase RTDB."""
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(f"=== DÉBUT send_message ===")
            logger.info(f"Envoi message pour session: {base_session_key}, thread: {thread_key}")
            logger.info(f"Paramètres reçus: user_id={user_id}, collection_name={collection_name}")
            logger.info(f"Message: {message[:100]}...")
            logger.info(f"System prompt: {system_prompt}")
            logger.info(f"Selected tool: {selected_tool}")
            
            # Récupérer ou créer la session (lock MINIMAL pour accès au dict global)
            logger.info(f"Vérification de l'existence de la session: {base_session_key}")
            with self._lock:
                # Vérifier rapidement si la session existe
                session_exists = base_session_key in self.sessions
                if session_exists:
                    session = self.sessions[base_session_key]
                    logger.info(f"Session existante trouvée")
            
            # Si session n'existe pas, l'initialiser (hors du lock global)
            if not session_exists:
                logger.info(f"Session non trouvée, initialisation...")
                init_result = await self.initialize_session(
                    user_id, collection_name, chat_mode=chat_mode
                )
                if not init_result.get("success"):
                    logger.error(f"Échec de l'initialisation: {init_result}")
                    return init_result
                logger.info(f"Session initialisée avec succès")
                
                # Récupérer la session nouvellement créée
                with self._lock:
                    session = self.sessions[base_session_key]
            
            logger.info(f"Session récupérée: {type(session)}")
            
            # Générer IDs pour les messages
            logger.info(f"Génération des IDs pour les messages...")
            user_message_id = str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
            logger.info(f"User message ID: {user_message_id}")
            logger.info(f"Assistant message ID: {assistant_message_id}")
            
            # Écrire le message utilisateur dans Firebase RTDB
            user_msg_path = f"{collection_name}/chats/{thread_key}/messages/{user_message_id}"
            user_msg_ref = self._get_rtdb_ref(user_msg_path)
            user_msg_ref.set({
                "role": "user",
                "content": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": user_id,
                "read": False
            })
            logger.info(f"Message utilisateur écrit dans RTDB: {user_msg_path}")
            
            # Créer un message assistant "vide" (pour le streaming)
            # IMPORTANT: Capturer le timestamp pour le réutiliser à la fin
            assistant_timestamp = datetime.now(timezone.utc).isoformat()
            assistant_msg_path = f"{collection_name}/chats/{thread_key}/messages/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            assistant_msg_ref.set({
                "role": "assistant",
                "content": "",
                "timestamp": assistant_timestamp,
                "status": "streaming",
                "streaming_progress": 0.0,
                "read": False
            })
            logger.info(f"Message assistant créé dans RTDB: {assistant_msg_path}")
            
            # Lancer le traitement en arrière-plan
            logger.info(f"=== LANCEMENT DU TRAITEMENT EN ARRIÈRE-PLAN ===")
            logger.info(f"Lancement du traitement en arrière-plan pour thread: {thread_key}")
            logger.info(f"Session: {session}")
            logger.info(f"Assistant message ID: {assistant_message_id}")
            logger.info(f"Message: {message[:100]}...")
            logger.info(f"System prompt: {system_prompt}")
            logger.info(f"Selected tool: {selected_tool}")
            
            logger.info(f"Création de la tâche asyncio...")
            task = asyncio.create_task(
                self._process_message_with_ws_streaming(
                    session=session,
                    user_id=user_id,
                    collection_name=collection_name,
                    thread_key=thread_key,
                    assistant_message_id=assistant_message_id,
                    assistant_timestamp=assistant_timestamp,
                    message=message,
                    system_prompt=system_prompt,
                    selected_tool=selected_tool
                )
            )
            
            # Enregistrer le stream pour contrôle d'arrêt
            await self.streaming_controller.register_stream(
                session_key=base_session_key,
                thread_key=thread_key,
                task=task
            )
            
            logger.info(f"Tâche de traitement lancée pour thread: {thread_key}")
            logger.info(f"=== FIN send_message ===")
            
            # Format de canal WebSocket identique à RTDB pour faciliter la transition
            ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
            
            return {
                "success": True,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "ws_channel": ws_channel,
                "message": "Message envoyé, réponse en cours de streaming via WebSocket"
            }
            
        except Exception as e:
            logger.error(f"Erreur envoi message LLM: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
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
    
    async def load_chat_history(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        history: list
    ) -> dict:
        """
        Charge l'historique d'un chat dans la session LLM.
        
        Cette méthode doit être appelée chaque fois qu'on change de chat
        pour que le microservice ait le contexte complet de la conversation.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société (space_code)
            thread_key: Clé du thread de chat
            history: Historique du chat au format [{"role": "user", "content": "..."}, ...]
            
        Returns:
            dict: {"success": bool, "message": str, "loaded_messages": int}
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(f"📚 Chargement historique pour session: {base_session_key}, thread: {thread_key}")
            logger.info(f"Nombre de messages à charger: {len(history)}")
            
            # Récupérer la session existante (lock MINIMAL)
            with self._lock:
                if base_session_key not in self.sessions:
                    return {
                        "success": False,
                        "error": "Session non trouvée",
                        "message": "Session LLM non initialisée. Appelez initialize_session d'abord.",
                        "loaded_messages": 0
                    }
                session = self.sessions[base_session_key]
            
            # Charger l'historique dans l'agent BaseAIAgent
            if session.agent:
                # Utiliser la méthode load_chat_history de BaseAIAgent
                session.agent.load_chat_history(
                    provider=ModelProvider.ANTHROPIC,  # Provider par défaut
                    history=history
                )
                
                # Mettre à jour l'historique local de la session
                session.conversations[thread_key] = history.copy()
                
                # Mettre à jour la dernière activité
                session.last_activity[thread_key] = datetime.now(timezone.utc)
                
                logger.info(f"✅ Historique chargé: {len(history)} messages pour thread {thread_key}")
                
                return {
                    "success": True,
                    "message": f"Historique chargé avec succès: {len(history)} messages",
                    "loaded_messages": len(history),
                    "session_id": base_session_key,
                    "thread_key": thread_key
                }
            else:
                return {
                    "success": False,
                    "error": "Agent non initialisé",
                    "message": "Agent LLM non disponible",
                    "loaded_messages": 0
                }
                
        except Exception as e:
            logger.error(f"❌ Erreur chargement historique: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Échec du chargement de l'historique: {str(e)}",
                "loaded_messages": 0
            }
    
    async def flush_chat_history(
        self,
        user_id: str,
        collection_name: str,
        provider: str = "ANTHROPIC"
    ) -> dict:
        """
        Vide l'historique de chat de l'agent BaseAIAgent.
        
        Cette méthode réinitialise complètement l'historique des conversations
        pour permettre de démarrer une nouvelle conversation sans contexte.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la société (space_code)
            provider: Provider LLM (par défaut "ANTHROPIC")
            
        Returns:
            dict: {"success": bool, "message": str}
        """
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            logger.info(f"🗑️ Flush historique pour session: {base_session_key}")
            
            # Récupérer la session existante (lock MINIMAL)
            with self._lock:
                if base_session_key not in self.sessions:
                    return {
                        "success": False,
                        "error": "Session non trouvée",
                        "message": "Session LLM non initialisée. Appelez initialize_session d'abord."
                    }
                session = self.sessions[base_session_key]
            
            # Vider l'historique dans l'agent BaseAIAgent
            if session.agent:
                # Convertir le provider string en enum
                try:
                    provider_enum = ModelProvider[provider.upper()]
                except KeyError:
                    provider_enum = ModelProvider.ANTHROPIC
                
                # Appeler flush_chat_history de BaseAIAgent
                session.agent.flush_chat_history(provider=provider_enum)
                
                # Vider également l'historique local de la session
                session.conversations.clear()
                session.last_activity.clear()
                
                logger.info(f"✅ Historique vidé pour session {base_session_key}")
                
                return {
                    "success": True,
                    "message": f"Historique de chat vidé avec succès pour le provider {provider}",
                    "session_id": base_session_key
                }
            else:
                return {
                    "success": False,
                    "error": "Agent non initialisé",
                    "message": "Agent LLM non disponible"
                }
                
        except Exception as e:
            logger.error(f"❌ Erreur flush historique: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Échec du flush de l'historique: {str(e)}"
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
            
            logger.info(f"Arrêt streaming pour session: {base_session_key}")
            
            if thread_key:
                # Arrêter un thread spécifique
                success = await self.streaming_controller.stop_stream(base_session_key, thread_key)
                if success:
                    logger.info(f"Stream arrêté pour thread: {thread_key}")
                    return {
                        "success": True,
                        "message": f"Stream arrêté pour thread {thread_key}",
                        "thread_key": thread_key
                    }
                else:
                    return {
                        "success": False,
                        "error": "Thread non trouvé ou déjà arrêté",
                        "message": f"Thread {thread_key} non trouvé"
                    }
            else:
                # Arrêter tous les threads de la session
                stopped_count = await self.streaming_controller.stop_all_streams(base_session_key)
                
                logger.info(f"Tous les streams arrêtés: {stopped_count}")
                return {
                    "success": True,
                    "message": f"Tous les streams arrêtés ({stopped_count} threads)",
                    "stopped_count": stopped_count
                }
                
        except Exception as e:
            logger.error(f"Erreur stop_streaming: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Échec de l'arrêt du streaming"
            }
    
    async def _process_message_with_ws_streaming(
        self,
        session: LLMSession,
        user_id: str,
        collection_name: str,
        thread_key: str,
        assistant_message_id: str,
        assistant_timestamp: str,
        message: str,
        system_prompt: str = None,
        selected_tool: str = None
    ):
        """Traite le message et stream la réponse via WebSocket + 1 écriture RTDB finale."""
        
        base_session_key = f"{user_id}:{collection_name}"
        ws_channel = f"chat:{user_id}:{collection_name}:{thread_key}"
        accumulated_content = ""
        
        try:
            logger.info(f"Traitement message avec streaming WebSocket pour thread: {thread_key}")
            logger.info(f"Canal WebSocket: {ws_channel}")
            
            # Importer hub WebSocket
            from ..ws_hub import hub
            
            # Gérer selected_tool si fourni
            final_system_prompt = system_prompt
            logger.info(f"System prompt reçu: {system_prompt}")
            logger.info(f"Selected tool: {selected_tool}")
            if selected_tool:
                tool_prompt = f"Utilise l'outil sélectionné : {selected_tool}"
                if final_system_prompt:
                    final_system_prompt = f"{final_system_prompt}\n\n{tool_prompt}"
                else:
                    final_system_prompt = tool_prompt
                logger.info(f"Outil sélectionné : {selected_tool}")
            
            logger.info(f"Final system prompt: {final_system_prompt}")
            
            # 1️⃣ Notifier le début du streaming via WebSocket
            await hub.broadcast(user_id, {
                "type": "llm_stream_start",
                "channel": ws_channel,
                "payload": {
                    "message_id": assistant_message_id,
                    "thread_key": thread_key,
                    "space_code": collection_name,
                    "timestamp": assistant_timestamp
                }
            })
            
            # 2️⃣ Stream depuis l'agent LLM
            logger.info(f"Début du streaming depuis l'agent LLM...")
            chunk_count = 0
            try:
                async for chunk in session.process_message_streaming(
                    thread_key, 
                    message,
                    system_prompt=final_system_prompt
                ):
                    chunk_count += 1
                    chunk_content = chunk.get("content", "")
                    is_final = chunk.get("is_final", False)
                    
                    accumulated_content += chunk_content
                    
                    logger.info(f"Chunk #{chunk_count} reçu: '{chunk_content[:50]}...' (final: {is_final})")
                    
                    # 🚀 BROADCAST IMMÉDIAT via WebSocket (aucun buffer)
                    await hub.broadcast(user_id, {
                        "type": "llm_stream_chunk",
                        "channel": ws_channel,
                        "payload": {
                            "message_id": assistant_message_id,
                            "thread_key": thread_key,
                            "space_code": collection_name,
                            "chunk": chunk_content,
                            "accumulated": accumulated_content,
                            "is_final": is_final
                        }
                    })
                    
                    # Si c'est le chunk final, on peut sortir de la boucle
                    if is_final:
                        logger.info(f"Chunk final reçu, fin du streaming")
                        break
                        
            except asyncio.CancelledError:
                logger.info(f"Streaming interrompu par l'utilisateur")
                # Notifier l'interruption via WebSocket
                await hub.broadcast(user_id, {
                    "type": "llm_stream_interrupted",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,
                        "thread_key": thread_key,
                        "space_code": collection_name,
                        "accumulated": accumulated_content
                    }
                })
                raise
            except Exception as streaming_error:
                logger.error(f"Erreur pendant le streaming: {streaming_error}")
                # Notifier l'erreur via WebSocket
                await hub.broadcast(user_id, {
                    "type": "llm_stream_error",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,
                        "thread_key": thread_key,
                        "space_code": collection_name,
                        "error": str(streaming_error)
                    }
                })
                raise
            
            logger.info(f"Streaming terminé. Total chunks: {chunk_count}")
            logger.info(f"Contenu final accumulé: '{accumulated_content[:200]}...'")
            
            # 3️⃣ UNE SEULE ÉCRITURE RTDB FINALE (persistence)
            assistant_msg_path = f"{collection_name}/chats/{thread_key}/messages/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            
            final_message_data = self.rtdb_formatter.format_ai_message(
                content=accumulated_content,
                user_id=user_id,
                message_id=assistant_message_id,
                timestamp=assistant_timestamp,
                metadata={
                    "tokens_used": session.get_token_stats(thread_key),
                    "duration_ms": session.get_last_response_duration_ms(thread_key),
                    "model": "claude-3-7-sonnet-20250219",
                    "status": "complete",
                    "streaming_progress": 1.0,
                    "completed_at": datetime.now(timezone.utc).isoformat()
                }
            )
            
            logger.info(f"Écriture finale dans RTDB: {assistant_msg_path}")
            assistant_msg_ref.set(final_message_data)
            
            # 4️⃣ Notifier la fin du streaming via WebSocket
            await hub.broadcast(user_id, {
                "type": "llm_stream_complete",
                "channel": ws_channel,
                "payload": {
                    "message_id": assistant_message_id,
                    "thread_key": thread_key,
                    "space_code": collection_name,
                    "full_content": accumulated_content,
                    "metadata": final_message_data.get("metadata", {})
                }
            })
            
            # Désenregistrer le stream terminé
            await self.streaming_controller.unregister_stream(base_session_key, thread_key)
            
            logger.info(f"Message assistant complété (WebSocket + RTDB)")
            
        except asyncio.CancelledError:
            logger.info(f"Tâche de streaming annulée")
            # Désenregistrer le stream
            await self.streaming_controller.unregister_stream(base_session_key, thread_key)
            raise
        except Exception as e:
            logger.error(f"Erreur streaming WebSocket: {e}", exc_info=True)
            
            # Marquer comme erreur dans Firebase RTDB
            try:
                assistant_msg_path = f"{collection_name}/chats/{thread_key}/messages/{assistant_message_id}"
                assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
                assistant_msg_ref.update({
                    "status": "error",
                    "error": str(e),
                    "error_at": datetime.now(timezone.utc).isoformat()
                })
            except Exception as update_error:
                logger.error(f"Impossible de mettre à jour l'erreur dans RTDB: {update_error}")
            
            # Notifier l'erreur via WebSocket
            try:
                from ..ws_hub import hub
                await hub.broadcast(user_id, {
                    "type": "llm_stream_error",
                    "channel": ws_channel,
                    "payload": {
                        "message_id": assistant_message_id,
                        "thread_key": thread_key,
                        "space_code": collection_name,
                        "error": str(e)
                    }
                })
            except Exception:
                pass
            
            # Désenregistrer le stream
            await self.streaming_controller.unregister_stream(base_session_key, thread_key)


# Singleton pour le gestionnaire LLM
_llm_manager: Optional[LLMManager] = None

def get_llm_manager() -> LLMManager:
    """Récupère l'instance singleton du LLM Manager."""
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager


