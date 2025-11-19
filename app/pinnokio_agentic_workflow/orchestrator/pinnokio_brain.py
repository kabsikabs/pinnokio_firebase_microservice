"""
Pinnokio Brain - Agent Cerveau Principal
Agent orchestrateur intelligent avec capacité de raisonnement pour gérer SPT et LPT
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import json

from ...llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize, NEW_Anthropic_Agent, NEW_OpenAiAgent
from .agent_modes import get_agent_mode_config

logger = logging.getLogger("pinnokio.brain")


class PinnokioBrain:
    """
    Agent cerveau principal (Pinnokio) avec capacité d'orchestration SPT/LPT
    
    Responsabilités:
    - Comprendre les requêtes utilisateur complexes
    - Élaborer des plans d'action structurés
    - Orchestrer l'exécution SPT (synchrone) et LPT (asynchrone)
    - Maintenir le contexte pendant l'exécution
    - Communiquer avec l'utilisateur pendant les LPT
    """
    
    def __init__(self, 
                 collection_name: str,
                 firebase_user_id: str,
                 dms_system: str = "google_drive",
                 dms_mode: str = "prod"):
        """
        Initialise l'agent cerveau Pinnokio
        
        Args:
            collection_name: Nom de la collection (société)
            firebase_user_id: ID utilisateur Firebase
            dms_system: Système DMS (google_drive, etc.)
            dms_mode: Mode DMS (prod, test)
        """
        self.collection_name = collection_name
        self.firebase_user_id = firebase_user_id
        self.dms_system = dms_system
        self.dms_mode = dms_mode
        
        # ⭐ NOUVELLE ARCHITECTURE: Agent principal créé via initialize_agents()
        # Chaque brain a son propre agent principal isolé
        self.pinnokio_agent: Optional[BaseAIAgent] = None
        
        # Configuration du provider (modèle de raisonnement)
        self.default_provider = ModelProvider.OPENAI
        self.default_size = ModelSize.MEDIUM  # Kimi K2 pour raisonnement + streaming + tools
        
        # ⭐ NOUVELLE ARCHITECTURE: L'historique est géré par self.pinnokio_agent
        # Plus de duplication d'historique au niveau du brain
        
        # État de l'orchestration
        self.active_plans: Dict[str, Dict] = {}  # {thread_key: plan_data}
        self.active_lpt_tasks: Dict[str, List[str]] = {}  # {thread_key: [task_ids]}
        
        # ⭐ NOUVEAU: Contexte utilisateur (métadonnées société)
        # Contient: mandate_path, dms_system, communication_mode, etc.
        # Accessible par tous les outils (SPT et LPT)
        self.user_context: Optional[Dict[str, Any]] = None
        
        # ⭐ NOUVEAU: Agent SPT ContextManager (sera initialisé dans initialize_spt_agents)
        # Chaque agent SPT a son propre BaseAIAgent et chat_history isolé
        self.context_manager = None
        
        # ⭐ NOUVEAU: Jobs data et métriques (assignés depuis LLMSession)
        # Ces données sont chargées à l'initialisation de la session pour alléger le contexte
        self.jobs_data: Dict[str, Any] = {}  # Données complètes des jobs (pour GET_JOBS)
        self.jobs_metrics: Dict[str, Any] = {}  # Métriques pour le system prompt
        
        # ⭐ NOUVEAU: Thread actif (pour workflows d'approbation avec cartes)
        self.active_thread_key: Optional[str] = None
        
        # ⭐ NOUVEAU: Proposition de contexte en attente (pour UPDATE_CONTEXT → PUBLISH_CONTEXT)
        self.context_proposal: Optional[Dict[str, Any]] = None

        # ⭐ NOUVEAU: Données de la tâche en cours d'exécution (si mode task_execution)
        self.active_task_data: Optional[Dict[str, Any]] = None

        # ⭐ Mode de chat courant (utilisé pour la config prompt/outils)
        self.current_chat_mode: str = "general_chat"

        # ⭐ Données spécifiques onboarding (chargées à la demande, uniquement pour onboarding_chat)
        self.onboarding_data: Optional[Dict[str, Any]] = None
        
        # ⭐ Données spécifiques job (chargées à la demande, pour router_chat, banker_chat, etc.)
        self.job_data: Optional[Dict[str, Any]] = None

        logger.info(f"PinnokioBrain initialisé pour user={firebase_user_id}, collection={collection_name}")
    
    async def initialize_agents(self):
        """
        Crée les agents du brain (principal + outils SPT).
        
        ⭐ NOUVELLE ARCHITECTURE : Chaque brain a ses propres agents isolés
        
        Création:
        1. Agent principal (pinnokio_agent) - BaseAIAgent pour interaction utilisateur
        2. Agents SPT (context_manager, etc.) - Pour outils rapides
        
        Cette méthode doit être appelée immédiatement après la création du brain,
        avant d'injecter les données de session et d'initialiser le system prompt.
        """
        try:
            logger.info(f"[BRAIN] 🤖 Création agents pour brain (user={self.firebase_user_id}, collection={self.collection_name})")
            
            # ═══ 1. Créer l'agent principal ═══
            self.pinnokio_agent = BaseAIAgent(
                collection_name=self.collection_name,
                dms_system=self.dms_system,
                dms_mode=self.dms_mode,
                firebase_user_id=self.firebase_user_id
            )
            
            # Configurer le provider et la taille par défaut
            self.pinnokio_agent.default_provider = self.default_provider
            self.pinnokio_agent.default_model_size = self.default_size
            
            # ═══ 2. Créer et enregistrer l'instance du provider ═══
            # Créer l'instance OpenAI (sans arguments)
            openai_instance = NEW_OpenAiAgent()
            
            # Enregistrer le provider dans BaseAIAgent
            # BaseAIAgent a déjà collection_name, dms_system, dms_mode, firebase_user_id
            self.pinnokio_agent.register_provider(
                provider=self.default_provider,
                instance=openai_instance,
                default_model_size=self.default_size
            )
            
            logger.info(f"[BRAIN] ✅ Agent principal créé (provider={self.default_provider.value}, size={self.default_size.value}, model=Kimi K2)")
            
            # ═══ 3. Créer les agents SPT ═══
            
            logger.info(f"[BRAIN] ✅ Agents SPT créés")
            
            logger.info(f"[BRAIN] 🎉 Tous les agents créés avec succès")
            
        except Exception as e:
            logger.error(f"[BRAIN] ❌ Erreur création agents: {e}", exc_info=True)
            raise
    
    def initialize_system_prompt(self, chat_mode: str = "general_chat", jobs_metrics: Dict = None):
        """Initialise le system prompt en fonction du mode déclaré."""

        config = get_agent_mode_config(chat_mode)

        if not self.pinnokio_agent:
            raise RuntimeError("Pinnokio agent non initialisé avant initialize_system_prompt")

        prompt = config.prompt_builder(self, jobs_metrics, chat_mode)
        self.pinnokio_agent.update_system_prompt(prompt)
        self.current_chat_mode = config.name

        logger.info(
            f"System prompt initialisé pour mode={chat_mode} (config={config.name})"
        )
    
    
    def create_workflow_tools(
        self,
        thread_key: str,
        session=None,
        chat_mode: str = "general_chat",
    ) -> Tuple[List[Dict], Dict]:
        """Retourne l'ensemble d'outils configuré pour le mode de chat."""

        config = get_agent_mode_config(chat_mode)
        tool_set, tool_mapping = config.tool_builder(self, thread_key, session, chat_mode)

        logger.info(
            f"Outils initialisés pour mode={chat_mode} (config={config.name}) : {len(tool_set)} outils"
        )
        return tool_set, tool_mapping


    def _build_general_chat_tools(self, thread_key: str, session=None) -> Tuple[List[Dict], Dict]:
        """Construit l'ensemble d'outils standard (mode général)."""
        from ..tools.spt_tools import SPTTools
        from ..tools.lpt_client import LPTClient
        
        
        # Créer les outils SPT
        # ⭐ IMPORTANT : Passer le brain pour accès au contexte utilisateur
        spt_tools = SPTTools(
            firebase_user_id=self.firebase_user_id,
            collection_name=self.collection_name,
            brain=self
        )
        spt_tools_list = spt_tools.get_tools_definitions()
        spt_tools_mapping = spt_tools.get_tools_mapping()

        # ⚠️ SPT_CONTEXT_MANAGER DÉSACTIVÉ TEMPORAIREMENT
        # Les outils de contexte sont maintenant intégrés directement dans l'agent principal
        # via ContextTools (job_tools.py) pour un accès plus rapide et direct.
        # Le code SPT est conservé pour usage futur avec d'autres agents SPT.
        #
        # from ..tools.spt_context_manager import create_spt_context_manager_wrapper
        # tool_def, handler = create_spt_context_manager_wrapper(self)
        # spt_tools_list.append(tool_def)
        # spt_tools_mapping["SPT_CONTEXT_MANAGER"] = handler
        
        # Créer les outils LPT avec session pour cache
        lpt_client = LPTClient()
        lpt_tools_list, lpt_tools_mapping = lpt_client.get_tools_definitions_and_mapping(
            user_id=self.firebase_user_id,
            company_id=self.collection_name,
            thread_key=thread_key,
            session=session,  # ⭐ Passer la session pour le cache
            brain=self        # ⭐ IMPORTANT: Passer le brain pour accès au contexte utilisateur
        )
        
        # ═══ OUTILS JOBS (3 outils séparés par département) ═══
        # Créer les 3 outils jobs avec leurs handlers
        from ..tools.job_tools import APBookkeeperJobTools, RouterJobTools, BankJobTools, ContextTools
        
        # 🔍 LOGS DE DIAGNOSTIC - Vérifier jobs_data avant création outils
        logger.info(f"[BRAIN] 🔍 DIAGNOSTIC self.jobs_data avant création outils - "
                   f"Clés: {list(self.jobs_data.keys()) if self.jobs_data else 'None'}")
        if self.jobs_data and 'ROUTER' in self.jobs_data:
            router_unprocessed = self.jobs_data['ROUTER'].get('unprocessed', [])
            logger.info(f"[BRAIN] 🔍 DIAGNOSTIC self.jobs_data['ROUTER']['unprocessed'] - "
                       f"Longueur: {len(router_unprocessed) if isinstance(router_unprocessed, list) else 'N/A'}")
        else:
            logger.warning(f"[BRAIN] ⚠️ DIAGNOSTIC - Pas de données ROUTER dans self.jobs_data !")
        
        # 1. APBookkeeper Jobs
        apbookeeper_tools = APBookkeeperJobTools(jobs_data=self.jobs_data)
        get_apbookeeper_jobs_def = apbookeeper_tools.get_tool_definition()
        
        async def handle_get_apbookeeper_jobs(**kwargs):
            return await apbookeeper_tools.search(**kwargs)
        
        # 2. Router Jobs
        router_tools = RouterJobTools(jobs_data=self.jobs_data)
        get_router_jobs_def = router_tools.get_tool_definition()
        
        async def handle_get_router_jobs(**kwargs):
            return await router_tools.search(**kwargs)
        
        # 3. Bank Transactions
        bank_tools = BankJobTools(jobs_data=self.jobs_data)
        get_bank_transactions_def = bank_tools.get_tool_definition()
        
        async def handle_get_bank_transactions(**kwargs):
            return await bank_tools.search(**kwargs)
        
        # ═══ OUTILS CONTEXT (5 outils d'accès et modification des contextes) ═══
        # Créer les outils de contexte avec leurs handlers
        from ...firebase_providers import FirebaseManagement
        firebase_management = FirebaseManagement()
        
        context_tools = ContextTools(
            firebase_management=firebase_management,
            firebase_user_id=self.firebase_user_id,
            collection_name=self.collection_name,
            brain=self  # ✅ Passer le brain pour accès au user_context
        )
        
        # Définitions des outils de contexte
        router_prompt_def = context_tools.get_router_prompt_definition()
        apbookeeper_context_def = context_tools.get_apbookeeper_context_definition()
        company_context_def = context_tools.get_company_context_definition()
        update_context_def = context_tools.get_update_context_definition()
        
        # Handlers pour les outils de contexte
        async def handle_router_prompt(**kwargs):
            return await context_tools.get_router_prompt(**kwargs)
        
        async def handle_apbookeeper_context(**kwargs):
            return await context_tools.get_apbookeeper_context(**kwargs)
        
        async def handle_company_context(**kwargs):
            return await context_tools.get_company_context(**kwargs)
        
        async def handle_update_context(**kwargs):
            return await context_tools.update_context(**kwargs)

        # ═══ OUTIL VISION DOCUMENT DRIVE ═══
        view_drive_document_def = {
            "name": "VIEW_DRIVE_DOCUMENT",
            "description": """🖼️ Visionner et analyser un document Google Drive.
            
            Utilisez cet outil pour:
            - Voir le contenu d'un document/image dans Google Drive
            - Analyser des factures, PDF, images
            - Répondre aux questions sur le contenu visuel d'un document
            
            ⚠️ **WORKFLOW OBLIGATOIRE** :
            1. **D'ABORD** : Récupérer le `drive_file_id` avec :
               - `GET_APBOOKEEPER_JOBS` pour les factures
               - `GET_ROUTER_JOBS` pour les documents à router
               - `GET_BANK_TRANSACTIONS` (pas de file_id ici, ne pas utiliser)
            2. **ENSUITE** : Utiliser ce `drive_file_id` avec VIEW_DRIVE_DOCUMENT
            
            ❌ **NE PAS** inventer ou deviner un file_id !
            ❌ **NE PAS** utiliser un nom de fichier comme file_id !
            
            Exemples corrects:
            1. GET_APBOOKEEPER_JOBS(file_name_contains="38653") → obtenir drive_file_id
            2. VIEW_DRIVE_DOCUMENT(file_id="1A2B3C4D5E...", question="Détails de la facture")
            """,
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "ID du fichier Google Drive à visionner (ex: '1A2B3C4D5E')"
                    },
                    "question": {
                        "type": "string",
                        "description": "Question spécifique sur le document (optionnel). Si non fourni, fait une analyse générale."
                    }
                },
                "required": ["file_id"]
            }
        }
        
        async def handle_view_drive_document(**kwargs):
            """Handler pour visionner un document Google Drive."""
            try:
                file_id = kwargs.get("file_id")
                question = kwargs.get("question", "Décris le contenu de ce document en détail.")
                
                # ✅ VALIDATION : Vérifier que file_id est fourni et non vide
                if not file_id or not isinstance(file_id, str) or len(file_id.strip()) == 0:
                    error_msg = (
                        "❌ Paramètre 'file_id' manquant ou invalide. "
                        "Pour voir un document, tu DOIS d'abord récupérer son drive_file_id "
                        "en utilisant GET_APBOOKEEPER_JOBS, GET_ROUTER_JOBS ou GET_BANK_TRANSACTIONS."
                    )
                    logger.warning(f"[VIEW_DRIVE_DOCUMENT] {error_msg}")
                    return {
                        "type": "error",
                        "message": error_msg
                    }
                
                # Vérifier que le DMS est disponible
                if not self.pinnokio_agent or not self.pinnokio_agent.dms_system:
                    return {
                        "type": "error",
                        "message": "Système DMS non initialisé. Impossible d'accéder aux documents Drive."
                    }
                
                logger.info(f"[VIEW_DRIVE_DOCUMENT] 🖼️ Vision du document: file_id={file_id}")
                
                # Utiliser process_vision de BaseAIAgent avec Groq (Llama Scout)
                response = await asyncio.to_thread(
                    self.pinnokio_agent.process_vision,
                    text=question,
                    provider=self.default_provider,  # GROQ
                    size=ModelSize.MEDIUM,  # Llama Scout 17B (vision)
                    file_ids=[file_id],  # 🔥 CORRECTION: paramètre renommé drive_file_ids -> file_ids
                    method='batch',
                    max_tokens=2000,
                    final_resume=True
                )
                
                logger.info(f"[VIEW_DRIVE_DOCUMENT] ✅ Analyse terminée")
                
                return {
                    "type": "success",
                    "file_id": file_id,
                    "analysis": response if isinstance(response, str) else response.get('text_output', str(response))
                }
                
            except Exception as e:
                logger.error(f"[VIEW_DRIVE_DOCUMENT] ❌ Erreur: {e}", exc_info=True)
                return {
                    "type": "error",
                    "message": f"Erreur lors de la vision du document: {str(e)}"
                }

        # ═══ OUTILS TASK (gestion tâches planifiées) ═══
        from ..tools.task_tools import TaskTools

        task_tools = TaskTools(brain=self)
        create_task_def = task_tools.get_tool_definition()

        async def handle_create_task(**kwargs):
            return await task_tools.create_task(**kwargs)

        # ═══ OUTILS WORKFLOW CHECKLIST (pour tâches planifiées) ═══
        create_checklist_tool = {
            "name": "CREATE_CHECKLIST",
            "description": """📋 Créer la workflow checklist pour l'exécution de la tâche.

                **À utiliser uniquement en mode task_execution.**

                Créez une liste d'étapes basée sur le plan d'action de la mission.
                Chaque étape doit avoir un ID unique et un nom descriptif.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Liste des étapes à réaliser",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "ID unique (ex: 'STEP_1_GET_TRANSACTIONS')"
                                },
                                "name": {
                                    "type": "string",
                                    "description": "Nom descriptif de l'étape"
                                }
                            },
                            "required": ["id", "name"]
                        }
                    }
                },
                "required": ["steps"]
            }
        }

        update_step_tool = {
            "name": "UPDATE_STEP",
            "description": """📊 Mettre à jour l'état d'une étape de la checklist.

                **OBLIGATOIRE lors de l'exécution de tâches planifiées.**

                Utilisez cet outil pour signaler la progression :
                - Avant de commencer une étape : status="in_progress"
                - Après avoir terminé une étape : status="completed"
                - En cas d'erreur : status="error"
                """,
            "input_schema": {
                "type": "object",
                "properties": {
                    "step_id": {
                        "type": "string",
                        "description": "ID de l'étape"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["in_progress", "completed", "error"],
                        "description": "Nouveau statut"
                    },
                    "message": {
                        "type": "string",
                        "description": "Message descriptif"
                    }
                },
                "required": ["step_id", "status", "message"]
            }
        }

        async def handle_create_checklist(**kwargs):
            """Crée la workflow checklist."""
            try:
                steps = kwargs["steps"]

                # Valider qu'on est en mode tâche
                if not self.active_task_data:
                    return {"type": "error", "message": "Non disponible (mode normal)"}

                task_id = self.active_task_data["task_id"]
                execution_id = self.active_task_data["execution_id"]
                mandate_path = self.active_task_data["mandate_path"]
                thread_key = self.active_thread_key

                # Préparer les étapes
                formatted_steps = []
                for step in steps:
                    formatted_steps.append({
                        "id": step["id"],
                        "name": step["name"],
                        "status": "pending",
                        "timestamp": "",
                        "message": ""
                    })

                checklist_data = {
                    "total_steps": len(formatted_steps),
                    "current_step": 0,
                    "steps": formatted_steps
                }

                # Sauvegarder dans execution
                from ...firebase_providers import get_firebase_management
                fbm = get_firebase_management()

                fbm.update_task_execution(
                    mandate_path, task_id, execution_id,
                    {"workflow_checklist": checklist_data}
                )

                # ═══════════════════════════════════════════════════════════
                # ENVOI PAR WEBSOCKET + RTDB (comme pour les messages de chat)
                # ═══════════════════════════════════════════════════════════
                
                from ...ws_hub import hub
                from ...firebase_providers import get_firebase_realtime
                import uuid
                
                checklist_message_id = str(uuid.uuid4())
                timestamp = datetime.now(timezone.utc).isoformat()
                
                # Construire le message de commande
                user_language = self.user_context.get("user_language", "fr") if self.user_context else "fr"
                
                checklist_command = {
                    "action": "SET_WORKFLOW_CHECKLIST",
                    "params": {
                        "checklist": checklist_data,
                        "user_language": user_language
                    }
                }
                
                # 1. Envoi immédiat par WebSocket
                ws_message = {
                    "type": "WORKFLOW_CHECKLIST",
                    "thread_key": thread_key,
                    "timestamp": timestamp,
                    "message_id": checklist_message_id,
                    "content": json.dumps({
                        "message": {
                            "cmmd": checklist_command
                        }
                    })
                }
                
                ws_channel = f"chat:{self.firebase_user_id}:{self.collection_name}:{thread_key}"
                
                await hub.broadcast(self.firebase_user_id, {
                    "type": "WORKFLOW_CHECKLIST",
                    "channel": ws_channel,
                    "payload": ws_message
                })
                
                logger.info(f"[CREATE_CHECKLIST] 📡 Checklist envoyée via WebSocket")
                
                # 2. Sauvegarde dans RTDB pour persistence
                rtdb = get_firebase_realtime()
                rtdb_path = f"{self.collection_name}/chats/{thread_key}/messages/{checklist_message_id}"
                
                message_data = {
                    'content': json.dumps({
                        'message': {
                            'cmmd': checklist_command
                        }
                    }),
                    'sender_id': self.firebase_user_id,
                    'timestamp': timestamp,
                    'message_type': 'CMMD',
                    'read': False,
                    'role': 'assistant'
                }
                
                # Utiliser push() pour générer une clé unique
                thread_path = f"{self.collection_name}/chats/{thread_key}"
                messages_ref = rtdb.db.child(f'{thread_path}/messages')
                messages_ref.push(message_data)
                
                logger.info(f"[CREATE_CHECKLIST] 💾 Checklist sauvegardée dans RTDB")
                logger.info(f"[CREATE_CHECKLIST] ✅ {len(formatted_steps)} étapes créées")

                return {
                    "type": "success",
                    "message": f"Checklist créée : {len(formatted_steps)} étapes",
                    "total_steps": len(formatted_steps)
                }

            except Exception as e:
                logger.error(f"[CREATE_CHECKLIST] Erreur: {e}", exc_info=True)
                return {"type": "error", "message": str(e)}

        async def handle_update_step(**kwargs):
            """Met à jour une étape de la checklist."""
            try:
                step_id = kwargs["step_id"]
                status = kwargs["status"]
                message = kwargs["message"]

                # Valider mode tâche
                if not self.active_task_data:
                    return {"type": "error", "message": "Non disponible (mode normal)"}

                task_id = self.active_task_data["task_id"]
                execution_id = self.active_task_data["execution_id"]
                mandate_path = self.active_task_data["mandate_path"]
                thread_key = self.active_thread_key

                # Récupérer l'exécution
                from ...firebase_providers import get_firebase_management
                fbm = get_firebase_management()

                execution = fbm.get_task_execution(mandate_path, task_id, execution_id)

                if not execution:
                    return {"type": "error", "message": "Exécution non trouvée"}

                checklist = execution.get("workflow_checklist", {})
                steps = checklist.get("steps", [])

                # Trouver et mettre à jour l'étape
                step_found = False
                for step in steps:
                    if step["id"] == step_id:
                        step["status"] = status
                        step["timestamp"] = datetime.now(timezone.utc).isoformat()
                        step["message"] = message
                        step_found = True
                        break

                if not step_found:
                    return {"type": "error", "message": f"Étape {step_id} non trouvée"}

                # Sauvegarder
                fbm.update_task_execution(
                    mandate_path, task_id, execution_id,
                    {"workflow_checklist.steps": steps}
                )

                # ═══════════════════════════════════════════════════════════
                # ENVOI PAR WEBSOCKET + RTDB (comme pour les messages de chat)
                # ═══════════════════════════════════════════════════════════
                
                from ...ws_hub import hub
                from ...firebase_providers import get_firebase_realtime
                import uuid
                
                update_message_id = str(uuid.uuid4())
                timestamp = datetime.now(timezone.utc).isoformat()
                
                # Construire le message de commande
                update_command = {
                    "action": "UPDATE_STEP_STATUS",
                    "params": {
                        "step_id": step_id,
                        "status": status,
                        "timestamp": timestamp,
                        "message": message
                    }
                }
                
                # 1. Envoi immédiat par WebSocket
                ws_message = {
                    "type": "WORKFLOW_STEP_UPDATE",
                    "thread_key": thread_key,
                    "timestamp": timestamp,
                    "message_id": update_message_id,
                    "content": json.dumps({
                        "message": {
                            "cmmd": update_command
                        }
                    })
                }
                
                ws_channel = f"chat:{self.firebase_user_id}:{self.collection_name}:{thread_key}"
                
                await hub.broadcast(self.firebase_user_id, {
                    "type": "WORKFLOW_STEP_UPDATE",
                    "channel": ws_channel,
                    "payload": ws_message
                })
                
                logger.info(f"[UPDATE_STEP] 📡 Mise à jour envoyée via WebSocket")
                
                # 2. Sauvegarde dans RTDB pour persistence
                rtdb = get_firebase_realtime()
                
                message_data = {
                    'content': json.dumps({
                        'message': {
                            'cmmd': update_command
                        }
                    }),
                    'sender_id': self.firebase_user_id,
                    'timestamp': timestamp,
                    'message_type': 'CMMD',
                    'read': False,
                    'role': 'assistant'
                }
                
                # Utiliser push() pour générer une clé unique
                thread_path = f"{self.collection_name}/chats/{thread_key}"
                messages_ref = rtdb.db.child(f'{thread_path}/messages')
                messages_ref.push(message_data)
                
                logger.info(f"[UPDATE_STEP] 💾 Mise à jour sauvegardée dans RTDB")
                logger.info(f"[UPDATE_STEP] ✅ {step_id} → {status}: {message}")

                return {
                    "type": "success",
                    "message": f"Étape {step_id} mise à jour : {status}"
                }

            except Exception as e:
                logger.error(f"[UPDATE_STEP] Erreur: {e}", exc_info=True)
                return {"type": "error", "message": str(e)}

        # Outil TERMINATE_TASK
        terminate_tool = {
            "name": "TERMINATE_TASK",
            "description": "🎯 Terminer la tâche quand la mission est accomplie. Utilisez cet outil dès que vous avez résolu la requête de l'utilisateur et fourni une réponse complète.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Raison de la terminaison (ex: 'Mission accomplie', 'Information fournie', 'Tâche longue lancée')"
                    },
                    "conclusion": {
                        "type": "string",
                        "description": "Votre réponse finale COMPLÈTE pour l'utilisateur, résumant les actions effectuées et les résultats."
                    }
                },
                "required": ["reason", "conclusion"]
            }
        }
        
        # Combiner tous les outils (avec les 3 outils jobs + 4 outils context + VIEW_DRIVE_DOCUMENT + CREATE_TASK + checklist)
        tool_set = [
            get_apbookeeper_jobs_def,
            get_router_jobs_def,
            get_bank_transactions_def,
            router_prompt_def,
            apbookeeper_context_def,
            company_context_def,
            update_context_def,
            view_drive_document_def,  # ⭐ Outil de vision Drive
            create_task_def,
            create_checklist_tool,
            update_step_tool
        ] + spt_tools_list + lpt_tools_list + [terminate_tool]

        tool_mapping = {
            "GET_APBOOKEEPER_JOBS": handle_get_apbookeeper_jobs,
            "GET_ROUTER_JOBS": handle_get_router_jobs,
            "GET_BANK_TRANSACTIONS": handle_get_bank_transactions,
            "ROUTER_PROMPT": handle_router_prompt,
            "APBOOKEEPER_CONTEXT": handle_apbookeeper_context,
            "COMPANY_CONTEXT": handle_company_context,
            "UPDATE_CONTEXT": handle_update_context,
            "VIEW_DRIVE_DOCUMENT": handle_view_drive_document,  # ⭐ Handler vision Drive
            "CREATE_TASK": handle_create_task,
            "CREATE_CHECKLIST": handle_create_checklist,
            "UPDATE_STEP": handle_update_step,
            **spt_tools_mapping,
            **lpt_tools_mapping
        }
        
        # Ancienne définition en dur - CONSERVÉE CI-DESSOUS POUR COMPATIBILITÉ (à supprimer plus tard)
        old_tool_set = [
            # ═══════════════════════════════════════════════════════
            # SPT TOOLS - Outils rapides (<30s)
            # ═══════════════════════════════════════════════════════
            {
                "name": "READ_FIREBASE_DOCUMENT",
                "description": "📄 [SPT] Lire un document Firebase. Temps < 5 secondes.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection_path": {
                            "type": "string",
                            "description": "Chemin de la collection (ex: 'invoices', 'clients')"
                        },
                        "document_id": {
                            "type": "string",
                            "description": "ID du document"
                        }
                    },
                    "required": ["collection_path", "document_id"]
                }
            },
            {
                "name": "SEARCH_CHROMADB",
                "description": "🔍 [SPT] Recherche vectorielle dans ChromaDB. Temps < 10 secondes.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Question ou requête de recherche"
                        },
                        "n_results": {
                            "type": "integer",
                            "description": "Nombre de résultats (défaut: 5)"
                        }
                    },
                    "required": ["query"]
                }
            },
            
            # ═══════════════════════════════════════════════════════
            # LPT TOOLS - Tâches longues (>30s)
            # ═══════════════════════════════════════════════════════
            {
                "name": "CALL_FILE_MANAGER_AGENT",
                "description": """📂 [LPT] Appeler l'Agent File Manager pour des tâches de gestion documentaire complexes.
                
                Utilisez cet outil pour :
                - Accéder à des dossiers dans Drive
                - Rechercher des documents spécifiques
                - Analyser et extraire des informations de documents
                - Traiter des lots de fichiers
                
                ⚠️ Tâche asynchrone : Vous serez notifié quand terminé, restez disponible pour l'utilisateur.""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Action à effectuer (ex: 'search_and_analyze_document')"
                        },
                        "params": {
                            "type": "object",
                            "description": "Paramètres de l'action"
                        },
                        "task_title": {
                            "type": "string",
                            "description": "Titre descriptif de la tâche"
                        }
                    },
                    "required": ["action", "params", "task_title"]
                }
            },
            {
                "name": "CALL_ACCOUNTING_AGENT",
                "description": """🧾 [LPT] Appeler l'Agent Comptable pour des tâches de saisie et traitement comptable.
                
                Utilisez cet outil pour :
                - Saisir des factures fournisseurs en lot
                - Effectuer des rapprochements bancaires
                - Générer des écritures comptables
                - Traiter des paiements
                
                ⚠️ Tâche asynchrone : Vous serez notifié quand terminé, restez disponible pour l'utilisateur.""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Action comptable (ex: 'batch_invoice_entry')"
                        },
                        "params": {
                            "type": "object",
                            "description": "Paramètres de l'action"
                        },
                        "task_title": {
                            "type": "string",
                            "description": "Titre descriptif de la tâche"
                        }
                    },
                    "required": ["action", "params", "task_title"]
                }
            },
            
            # ═══════════════════════════════════════════════════════
            # TOOL DE TERMINAISON
            # ═══════════════════════════════════════════════════════
            {
                "name": "TERMINATE_TASK",
                "description": """🎯 Terminer la tâche quand TOUTES les actions sont complétées.
                
                Utilisez cet outil UNIQUEMENT quand :
                - Tous les SPT sont exécutés
                - Tous les LPT ont reçu leurs callbacks
                - Toutes les informations sont collectées
                - La mission est accomplie OU impossible à terminer
                
                Ne terminez PAS s'il y a encore des LPT en cours d'exécution !""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Raison de la terminaison"
                        },
                        "conclusion": {
                            "type": "string",
                            "description": "Rapport final complet avec résumé de toutes les actions"
                        },
                        "tasks_completed": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Liste des IDs de tâches complétées"
                        }
                    },
                    "required": ["reason", "conclusion"]
                }
            }
        ]
        
        # Tool mapping (les fonctions seront implémentées dans TaskExecutor)
        old_tool_map = {
            "READ_FIREBASE_DOCUMENT": lambda **kwargs: self._spt_read_firebase(**kwargs),
            "SEARCH_CHROMADB": lambda **kwargs: self._spt_search_chromadb(**kwargs),
            "CALL_FILE_MANAGER_AGENT": lambda **kwargs: self._lpt_file_manager(thread_key, **kwargs),
            "CALL_ACCOUNTING_AGENT": lambda **kwargs: self._lpt_accounting(thread_key, **kwargs)
        }
        
        # ⭐ RETOURNER LES NOUVEAUX OUTILS (SPT + LPT simplifiés)
        logger.info(f"Outils créés: {len(tool_set)} outils (SPT: {len(spt_tools_list)}, LPT: {len(lpt_tools_list)})")
        return tool_set, tool_mapping

    async def load_onboarding_data(self) -> Dict[str, Any]:
        """Charge les données d'onboarding spécifiques à l'utilisateur."""

        if self.onboarding_data is not None:
            return self.onboarding_data

        try:
            from ...firebase_providers import FirebaseManagement

            firebase = FirebaseManagement()
            onboarding_path = f"clients/{self.firebase_user_id}/temp_data/onboarding"
            doc_ref = firebase.db.document(onboarding_path)
            doc = await asyncio.to_thread(doc_ref.get)

            if doc.exists:
                self.onboarding_data = doc.to_dict() or {}
                logger.info(
                    f"[BRAIN_ONBOARDING] Données onboarding chargées ({list(self.onboarding_data.keys())})"
                )
            else:
                logger.warning(
                    f"[BRAIN_ONBOARDING] Aucun document onboarding trouvé pour path={onboarding_path}"
                )
                self.onboarding_data = {}

        except Exception as e:
            logger.error(f"[BRAIN_ONBOARDING] Erreur chargement données: {e}", exc_info=True)
            self.onboarding_data = {}

        return self.onboarding_data
    
    async def load_job_data(self, job_id: str) -> Dict[str, Any]:
        """Charge les données de job depuis notifications/{job_id}."""
        
        if self.job_data is not None and self.job_data.get("job_id") == job_id:
            return self.job_data
        
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase = FirebaseManagement()
            job_path = f"clients/{self.firebase_user_id}/notifications/{job_id}"
            doc_ref = firebase.db.document(job_path)
            doc = await asyncio.to_thread(doc_ref.get)
            
            if doc.exists:
                doc_data = doc.to_dict() or {}
                # Extraire les champs requis : instructions, job_id, file_id, status
                self.job_data = {
                    "instructions": doc_data.get("instructions", ""),
                    "job_id": doc_data.get("job_id", job_id),
                    "file_id": doc_data.get("file_id", ""),
                    "status": doc_data.get("status", ""),
                    # Conserver les autres champs au cas où
                    **{k: v for k, v in doc_data.items() if k not in ["instructions", "job_id", "file_id", "status"]}
                }
                
                # ═══ EXTRACTION DES TRANSACTIONS POUR BANKER_CHAT ═══
                # Extraire et formater les transactions depuis le champ 'transactions'
                transactions_raw = doc_data.get("transactions", {})
                formatted_transactions = []
                
                if transactions_raw:
                    # Cas 1: transactions est un dictionnaire avec des clés numériques (0, 1, 2, ...)
                    if isinstance(transactions_raw, dict):
                        # Trier les clés numériquement pour maintenir l'ordre
                        for key in sorted(transactions_raw.keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
                            transaction = transactions_raw[key]
                            if isinstance(transaction, dict):
                                # Extraire uniquement les champs importants
                                formatted_transaction = {
                                    "amount": transaction.get("amount"),
                                    "currency_name": transaction.get("currency_name", ""),
                                    "date": transaction.get("date", ""),
                                    "payment_ref": transaction.get("payment_ref", ""),
                                    "status": transaction.get("status", ""),
                                    "transaction_id": transaction.get("transaction_id", "")
                                }
                                formatted_transactions.append(formatted_transaction)
                    # Cas 2: transactions est une liste
                    elif isinstance(transactions_raw, list):
                        for transaction in transactions_raw:
                            if isinstance(transaction, dict):
                                # Extraire uniquement les champs importants
                                formatted_transaction = {
                                    "amount": transaction.get("amount"),
                                    "currency_name": transaction.get("currency_name", ""),
                                    "date": transaction.get("date", ""),
                                    "payment_ref": transaction.get("payment_ref", ""),
                                    "status": transaction.get("status", ""),
                                    "transaction_id": transaction.get("transaction_id", "")
                                }
                                formatted_transactions.append(formatted_transaction)
                    
                    if formatted_transactions:
                        self.job_data["formatted_transactions"] = formatted_transactions
                        logger.info(
                            f"[BRAIN_JOB_DATA] {len(formatted_transactions)} transactions formatées "
                            f"pour job_id={job_id}"
                        )
                
                logger.info(
                    f"[BRAIN_JOB_DATA] Données job chargées pour job_id={job_id} "
                    f"(instructions={bool(self.job_data.get('instructions'))}, "
                    f"file_id={self.job_data.get('file_id')}, "
                    f"status={self.job_data.get('status')}, "
                    f"transactions={len(self.job_data.get('formatted_transactions', []))})"
                )
            else:
                # C'est normal si le document n'existe pas encore (job pas encore lancé)
                # On initialise avec des valeurs par défaut
                self.job_data = {
                    "instructions": "",
                    "job_id": job_id,
                    "file_id": "",
                    "status": "pending"
                }
                logger.debug(
                    f"[BRAIN_JOB_DATA] Document job non trouvé pour path={job_path} "
                    f"(job_id={job_id}) - Initialisation avec valeurs par défaut. "
                    f"C'est normal si le job n'a pas encore été lancé."
                )
        
        except Exception as e:
            logger.error(f"[BRAIN_JOB_DATA] Erreur chargement données: {e}", exc_info=True)
            self.job_data = {
                "instructions": "",
                "job_id": job_id,
                "file_id": "",
                "status": ""
            }
        
        return self.job_data
    
    # ═══════════════════════════════════════════════════════════════
    # MÉTHODES SPT (synchrones)
    # ═══════════════════════════════════════════════════════════════
    
    def _spt_read_firebase(self, collection_path: str, document_id: str) -> Dict:
        """SPT: Lecture Firebase"""
        try:
            from ...firebase_client import get_firestore
            db = get_firestore()
            doc = db.collection(f"{self.collection_name}/{collection_path}").document(document_id).get()
            
            if doc.exists:
                return {
                    'type': 'success',
                    'data': doc.to_dict(),
                    'document_id': document_id
                }
            else:
                return {
                    'type': 'not_found',
                    'message': f"Document {document_id} non trouvé"
                }
        except Exception as e:
            logger.error(f"Erreur lecture Firebase: {e}")
            return {'type': 'error', 'message': str(e)}
    
    def _spt_search_chromadb(self, query: str, n_results: int = 5) -> Dict:
        """SPT: Recherche ChromaDB"""
        try:
            from ...chroma_vector_service import get_chroma_vector_service
            chroma = get_chroma_vector_service()
            
            results = chroma.query_collection(
                user_id=self.firebase_user_id,
                collection_name=self.collection_name,
                query_texts=[query],
                n_results=n_results
            )
            
            return {
                'type': 'success',
                'results': results,
                'count': len(results.get('documents', [[]])[0]) if results else 0
            }
        except Exception as e:
            logger.error(f"Erreur recherche ChromaDB: {e}")
            return {'type': 'error', 'message': str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # MÉTHODES LPT (asynchrones via HTTP)
    # ═══════════════════════════════════════════════════════════════
    
    def _lpt_file_manager(self, thread_key: str, action: str, params: Dict, task_title: str) -> Dict:
        """
        LPT: Appel à l'Agent File Manager (HTTP)
        
        Cette méthode déclenche une tâche asynchrone et retourne immédiatement.
        Le résultat arrivera plus tard via callback.
        """
        try:
            from .task_tracker import TaskTracker
            tracker = TaskTracker(self.firebase_user_id, self.collection_name)
            
            # Créer une tâche LPT
            task_id = tracker.create_lpt_task(
                thread_key=thread_key,
                agent_type="file_manager",
                action=action,
                params=params,
                task_title=task_title
            )
            
            # Enregistrer la tâche active
            if thread_key not in self.active_lpt_tasks:
                self.active_lpt_tasks[thread_key] = []
            self.active_lpt_tasks[thread_key].append(task_id)
            
            logger.info(f"Tâche LPT créée: {task_id} pour File Manager")
            
            return {
                'type': 'lpt_started',
                'task_id': task_id,
                'agent': 'file_manager',
                'estimated_duration': '2-3 minutes',
                'message': f"✅ Tâche '{task_title}' envoyée à l'Agent File Manager. Traitement en cours..."
            }
            
        except Exception as e:
            logger.error(f"Erreur démarrage LPT File Manager: {e}")
            return {'type': 'error', 'message': str(e)}
    
    def _lpt_accounting(self, thread_key: str, action: str, params: Dict, task_title: str) -> Dict:
        """
        LPT: Appel à l'Agent Comptable (HTTP)
        
        Cette méthode déclenche une tâche asynchrone et retourne immédiatement.
        Le résultat arrivera plus tard via callback.
        """
        try:
            from .task_tracker import TaskTracker
            tracker = TaskTracker(self.firebase_user_id, self.collection_name)
            
            # Créer une tâche LPT
            task_id = tracker.create_lpt_task(
                thread_key=thread_key,
                agent_type="accounting",
                action=action,
                params=params,
                task_title=task_title
            )
            
            # Enregistrer la tâche active
            if thread_key not in self.active_lpt_tasks:
                self.active_lpt_tasks[thread_key] = []
            self.active_lpt_tasks[thread_key].append(task_id)
            
            logger.info(f"Tâche LPT créée: {task_id} pour Accounting")
            
            return {
                'type': 'lpt_started',
                'task_id': task_id,
                'agent': 'accounting',
                'estimated_duration': '5-10 minutes',
                'message': f"✅ Tâche '{task_title}' envoyée à l'Agent Comptable. Traitement en cours..."
            }
            
        except Exception as e:
            logger.error(f"Erreur démarrage LPT Accounting: {e}")
            return {'type': 'error', 'message': str(e)}
    
    def has_active_lpt_tasks(self, thread_key: str) -> bool:
        """Vérifie si des tâches LPT sont en cours pour ce thread"""
        return thread_key in self.active_lpt_tasks and len(self.active_lpt_tasks[thread_key]) > 0
    
    def get_active_lpt_count(self, thread_key: str) -> int:
        """Retourne le nombre de tâches LPT actives"""
        return len(self.active_lpt_tasks.get(thread_key, []))
    
    def reset_context_with_summary(self, summary: str) -> int:
        """
        Réinitialise le contexte avec un résumé intégré au system prompt.
        
        Cette méthode :
        1. Ajoute le résumé au system prompt de base
        2. Vide l'historique du chat
        3. Calcule et retourne le nombre de tokens du nouveau contexte
        
        Args:
            summary: Résumé de la conversation à intégrer
        
        Returns:
            Nombre de tokens du nouveau contexte (system prompt + résumé)
        """
        logger.info("[RESET] Réinitialisation du contexte avec résumé")
        
        # Récupérer l'instance provider
        provider_instance = self.pinnokio_agent.get_provider_instance(self.default_provider)
        
        # Sauvegarder le system prompt de base (si pas déjà sauvegardé)
        if not hasattr(self, '_base_system_prompt'):
            self._base_system_prompt = provider_instance.system_prompt if hasattr(provider_instance, 'system_prompt') else ""
        
        # Créer le nouveau system prompt avec résumé intégré
        new_system_prompt = f"""{self._base_system_prompt}

                ═══════════════════════════════════════════
                📋 CONTEXTE DE LA CONVERSATION PRÉCÉDENTE :

                {summary}

                ═══════════════════════════════════════════

                Continue la conversation en tenant compte de ce contexte historique.
                """
        
        # Mettre à jour le system prompt
        if hasattr(provider_instance, 'update_system_prompt'):
            provider_instance.update_system_prompt(new_system_prompt)
        elif hasattr(provider_instance, 'system_prompt'):
            provider_instance.system_prompt = new_system_prompt
        
        # ⭐ NOUVEAU: Vider l'historique du brain (isolé par thread)
        self.clear_chat_history()
        
        # Calculer les tokens du nouveau contexte
        # ⭐ get_history_token_count() compte maintenant automatiquement :
        # - L'historique du chat (vide après clear)
        # - Le system prompt complet (avec résumé intégré)
        tokens_after_reset = self.get_history_token_count()
        
        logger.info(
            f"[RESET] Contexte réinitialisé - "
            f"Nouveau contexte: {tokens_after_reset:,} tokens "
            f"(system prompt avec résumé + historique vide)"
        )
        
        return tokens_after_reset
    
    def generate_conversation_summary(self, thread_key: str, total_tokens_used: int) -> str:
        """
        Génère un résumé compressé de la conversation actuelle
        pour réinitialiser le contexte tout en gardant l'essentiel.
        
        Cette méthode est appelée quand le budget de tokens est atteint (80K)
        pour compresser l'historique et permettre de continuer la conversation.
        
        Args:
            thread_key: Clé du thread de conversation
            total_tokens_used: Nombre total de tokens utilisés dans la session
        
        Returns:
            Résumé compressé de la conversation (max 500 tokens)
        """
        logger.info(f"[SUMMARY] Génération résumé - thread={thread_key}, tokens={total_tokens_used}")
        
        summary_prompt = f"""Résume cette conversation en gardant UNIQUEMENT les informations critiques:

                **Instructions de Résumé** :
                1. **Contexte initial**: Quelle était la demande originale de l'utilisateur ?
                2. **Actions effectuées**: Quels outils ont été utilisés (SPT/LPT) et pourquoi ?
                3. **Résultats clés**: Qu'avons-nous découvert ou accompli ?
                4. **État actuel**: Où en sommes-nous maintenant ? Que reste-t-il à faire ?
                5. **Tâches LPT en cours**: Y a-t-il des tâches longues en cours d'exécution ?

                **Contraintes** :
                - Maximum 500 tokens
                - Format concis et structuré
                - Garde uniquement l'essentiel pour continuer efficacement

                Tokens utilisés dans cette session: {total_tokens_used:,}
                """
        
        try:
            # Utiliser l'agent pour générer le résumé (sans outils)
            summary_response = self.pinnokio_agent.process_tool_use(
                content=summary_prompt,
                tools=[],  # Pas d'outils pour le résumé
                tool_mapping={},
                provider=self.default_provider,
                size=ModelSize.SMALL,  # Modèle rapide suffit pour un résumé
                max_tokens=600,
                raw_output=True
            )
            
            # Extraire le texte du résumé
            summary_text = self._extract_text_from_summary_response(summary_response)
            
            logger.info(f"[SUMMARY] Résumé généré - longueur={len(summary_text)} caractères")
            
            return summary_text
            
        except Exception as e:
            logger.error(f"[SUMMARY] Erreur génération résumé: {e}", exc_info=True)
            
            # Résumé de fallback en cas d'erreur
            return f"""Résumé automatique de la session:
            - Tokens utilisés: {total_tokens_used:,}
            - Thread: {thread_key}
            - Tâches LPT actives: {self.get_active_lpt_count(thread_key)}
            - Budget tokens atteint, contexte réinitialisé.
            """
    
    def _extract_text_from_summary_response(self, response: Any) -> str:
        """
        Extrait le texte d'une réponse de résumé.
        Helper method pour extract le texte peu importe le format de réponse.
        """
        if not response:
            return "Aucun résumé généré."
        
        # Si c'est une liste de réponses
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict):
                    # Chercher text_output
                    if "text_output" in item:
                        text_block = item["text_output"]
                        if isinstance(text_block, dict) and "content" in text_block:
                            return str(text_block["content"])
                        elif isinstance(text_block, str):
                            return text_block
        
        # Si c'est directement un dict
        if isinstance(response, dict):
            if "text_output" in response:
                text_block = response["text_output"]
                if isinstance(text_block, dict) and "content" in text_block:
                    return str(text_block["content"])
                elif isinstance(text_block, str):
                    return text_block
        
        # Fallback: convertir en string
        return str(response)[:1000]  # Limiter à 1000 chars par sécurité

    # ═══════════════════════════════════════════════════════
    # GESTION HISTORIQUE CHAT (ISOLÉ PAR THREAD)
    # ═══════════════════════════════════════════════════════
    
    def add_user_message(self, content):
        """
        Ajoute un message utilisateur à l'historique du chat.
        Proxy vers self.pinnokio_agent.add_user_message()
        
        Args:
            content: Contenu du message utilisateur (str ou list pour tool_results)
        """
        if self.pinnokio_agent:
            self.pinnokio_agent.add_user_message(content, provider=self.default_provider)
            content_len = len(content) if isinstance(content, (str, list)) else 0
            logger.debug(f"[CHAT_HISTORY] Message utilisateur ajouté via agent (type={type(content).__name__}, len={content_len})")
        else:
            logger.warning(f"[CHAT_HISTORY] Agent non initialisé, message non ajouté")
    
    def add_assistant_message(self, content: Any):
        """
        Ajoute un message assistant à l'historique du chat.
        Proxy vers self.pinnokio_agent.add_ai_message()
        
        Args:
            content: Contenu du message assistant (str, list ou dict)
                     - str: texte simple
                     - list: blocs Anthropic (text, tool_use, etc.)
                     - dict: ancien format (sera préservé)
        """
        if self.pinnokio_agent:
            self.pinnokio_agent.add_ai_message(content, provider=self.default_provider)
            content_type = type(content).__name__
            content_len = len(content) if isinstance(content, (str, list)) else 1
            logger.debug(f"[CHAT_HISTORY] Message assistant ajouté via agent (type={content_type}, len={content_len})")
        else:
            logger.warning(f"[CHAT_HISTORY] Agent non initialisé, message non ajouté")
    
    def get_chat_history(self) -> List[Dict[str, Any]]:
        """
        Retourne l'historique complet du chat.
        Proxy vers self.pinnokio_agent.chat_history
        
        Returns:
            Liste des messages du chat
        """
        if self.pinnokio_agent:
            return self.pinnokio_agent.chat_history.get(self.default_provider.value, []).copy()
        return []
    
    def clear_chat_history(self):
        """
        Vide l'historique du chat pour ce thread.
        Proxy vers self.pinnokio_agent.clear_chat_history()
        """
        if self.pinnokio_agent:
            current_history = self.get_chat_history()
            message_count = len(current_history)
            self.pinnokio_agent.clear_chat_history()
            logger.info(f"[CHAT_HISTORY] Historique vidé via agent ({message_count} messages supprimés)")
        else:
            logger.warning(f"[CHAT_HISTORY] Agent non initialisé, rien à vider")
    
    async def load_user_context(self, mode: str = "UI") -> Dict[str, Any]:
        """
        Charge le contexte utilisateur (métadonnées société) dans le brain SESSION.
        
        ⭐ NOUVEAU : Support dual-mode (UI/BACKEND)
        
        Mode UI (utilisateur connecté) :
        1. Tenter cache Redis (TTL 1h)
        2. Si CACHE MISS → Fallback Firebase
        3. Mettre en cache pour prochains appels
        
        Mode BACKEND (utilisateur déconnecté) :
        1. Accès direct Firebase (source de vérité)
        2. Pas de cache
        
        Ce contexte contient toutes les métadonnées importantes :
        - mandate_path, client_uuid, company_name
        - dms_system, drive_space_parent_id
        - communication_mode, log_communication_mode
        - bank_erp (odoo_url, odoo_db, etc.)
        
        ⭐ IMPORTANT : Appelé lors de initialize_agent() (pas besoin de thread_key)
        
        Args:
            mode: "UI" (cache prioritaire) ou "BACKEND" (Firebase direct)
        
        Returns:
            Dict contenant le contexte utilisateur
        """
        try:
            logger.info(f"[BRAIN_CONTEXT] Chargement contexte utilisateur - Mode: {mode}")
            
            import json
            from ...redis_client import get_redis
            
            context = None
            
            # ═══ MODE UI : Cache Redis → Fallback Firebase ═══
            if mode == "UI":
                try:
                    redis_client = get_redis()
                    cache_key = f"context:{self.firebase_user_id}:{self.collection_name}"
                    
                    logger.info(f"[BRAIN_CONTEXT] 🔍 DEBUG - Tentative lecture cache: {cache_key}")
                    cached_data = redis_client.get(cache_key)
                    logger.info(f"[BRAIN_CONTEXT] 🔍 DEBUG - cached_data type: {type(cached_data)}, value: {cached_data[:100] if cached_data else None}")
                    
                    if cached_data:
                        context = json.loads(cached_data)
                        logger.info(f"[BRAIN_CONTEXT] ✅ CACHE HIT: {cache_key}")
                    else:
                        logger.info(f"[BRAIN_CONTEXT] ❌ CACHE MISS: {cache_key} - Fallback Firebase")
                
                except Exception as e:
                    logger.warning(f"[BRAIN_CONTEXT] Erreur accès cache: {e} - Fallback Firebase")
            
            # ═══ Si pas de cache OU mode BACKEND : Firebase direct ═══
            if context is None:
                logger.info(f"[BRAIN_CONTEXT] Récupération depuis Firebase...")
                
                from ..tools.lpt_client import LPTClient
                
                lpt_client = LPTClient()
                
                # Récupérer depuis Firebase (sans cache)
                context = await lpt_client._reconstruct_full_company_profile(
                    self.firebase_user_id,
                    self.collection_name
                )
                
                # Si mode UI, mettre en cache
                if mode == "UI" and context:
                    try:
                        redis_client = get_redis()
                        cache_key = f"context:{self.firebase_user_id}:{self.collection_name}"
                        
                        # Ajouter timestamp
                        context["cached_at"] = datetime.now().isoformat()
                        
                        redis_client.setex(
                            cache_key,
                            3600,  # TTL 1 heure
                            json.dumps(context)
                        )
                        
                        logger.info(f"[BRAIN_CONTEXT] ✅ Contexte mis en cache: {cache_key}")
                    
                    except Exception as e:
                        logger.warning(f"[BRAIN_CONTEXT] Erreur mise en cache: {e}")
            
            # ═══ Stocker dans le brain ═══
            if context:
                self.user_context = context
                
                logger.info(
                    f"[BRAIN_CONTEXT] ✅ Contexte chargé: mandate_path={context.get('mandate_path')}, "
                    f"dms_system={context.get('dms_system')}, "
                    f"client_uuid={context.get('client_uuid')}, "
                    f"mode={mode}"
                )
                
                # 🔍 DEBUG : Afficher les champs critiques pour Router et Bank
                logger.info(
                    f"[BRAIN_CONTEXT] 🔍 DEBUG - Champs Drive: "
                    f"drive_space_parent_id={context.get('drive_space_parent_id')}, "
                    f"input_drive_doc_id={context.get('input_drive_doc_id')}"
                )
                logger.info(
                    f"[BRAIN_CONTEXT] 🔍 DEBUG - Champs ERP Bank: "
                    f"mandate_bank_erp={context.get('mandate_bank_erp')}, "
                    f"erp_odoo_url={context.get('erp_odoo_url')}, "
                    f"erp_erp_type={context.get('erp_erp_type')}"
                )
                logger.info(
                    f"[BRAIN_CONTEXT] 🔍 DEBUG - Toutes les clés: {list(context.keys())}"
                )
                
                return context
            
            else:
                raise ValueError("Contexte vide depuis Firebase")
        
        except Exception as e:
            logger.error(f"[BRAIN_CONTEXT] ❌ Erreur chargement contexte: {e}", exc_info=True)
            
            # Retourner un contexte minimal pour ne pas bloquer
            self.user_context = {
                "mandate_path": self.collection_name,
                "dms_system": "google_drive",
                "communication_mode": "webhook",
                "log_communication_mode": "firebase",
                "user_language": "fr",
                "mode": mode
            }
            
            return self.user_context
    
    def get_user_context(self) -> Dict[str, Any]:
        """
        Récupère le contexte utilisateur stocké dans le brain.
        
        Returns:
            Dict contenant le contexte utilisateur, ou dict vide si non chargé
        """
        if self.user_context is None:
            logger.warning(
                f"[BRAIN_CONTEXT] ⚠️ Contexte non chargé. "
                f"Appelez load_user_context() après création du brain."
            )
            return {}
        
        return self.user_context
    
    def set_active_thread(self, thread_key: str):
        """
        Définit le thread actif pour les workflows d'approbation.
        
        Cette méthode doit être appelée au début du traitement d'un message
        pour que les outils sachent sur quel thread envoyer les cartes d'approbation.
        
        Args:
            thread_key: Clé du thread de conversation actif
        """
        self.active_thread_key = thread_key
        logger.info(f"[BRAIN] Thread actif défini: {thread_key}")
    
    def get_history_token_count(self) -> int:
        """
        Estime le nombre de tokens dans le contexte actuel complet.
        
        ⭐ PROXY vers BaseAIAgent.get_total_context_tokens()
        
        Calcule automatiquement :
        - Chat history (messages utilisateur + assistant + tool_results)
        - System prompt (avec résumés éventuels)
        
        Returns:
            Nombre approximatif de tokens dans le contexte complet
        """
        if not self.pinnokio_agent:
            return 0
        
        # Déléguer le calcul à BaseAIAgent (évite duplication de code)
        return self.pinnokio_agent.get_total_context_tokens(self.default_provider)

