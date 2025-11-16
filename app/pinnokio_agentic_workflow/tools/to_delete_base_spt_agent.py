"""
BaseSPTAgent - Classe abstraite pour tous les SPT Agents (Short Process Tooling)
Structure synchrone, boucle limitée, gestion d'erreurs robuste
⭐ CORRECTION: Chaque SPT a son propre BaseAIAgent isolé
"""

import logging
import time
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from enum import Enum

logger = logging.getLogger("pinnokio.base_spt_agent")


class SPTStatus(str, Enum):
    """Statuts de fin d'exécution SPT"""
    MISSION_COMPLETED = "MISSION_COMPLETED"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    MAX_TURNS_REACHED = "MAX_TURNS_REACHED"
    NO_IA_ACTION = "NO_IA_ACTION"
    ERROR = "ERROR"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"


class BaseSPTAgent(ABC):
    """
    Classe abstraite pour tous les SPT Agents.
    
    ⭐ ISOLATION IMPORTANTE:
    - Chaque SPT crée son PROPRE BaseAIAgent (pas de partage)
    - Chat history complètement isolé du brain
    - Aucune contamination possible
    
    Caractéristiques :
    - Synchrone (pas d'async)
    - Chat history isolé, effacé après TERMINATE_TASK
    - Budget tokens : 15K
    - Max tours : 7
    - Argument unique : instructions (str)
    - Sorties : TERMINATE_TASK (dict) ou clarification (str brut)
    - TTL 1h pour clarifications en mémoire
    """
    
    # Configuration par défaut
    DEFAULT_MAX_TURNS = 7
    DEFAULT_TOKEN_BUDGET = 15000
    DEFAULT_CLARIFICATION_TTL = 3600  # 1 heure en secondes
    
    def __init__(self, 
                 firebase_user_id: str, 
                 collection_name: str,
                 dms_system: str = "google_drive",
                 dms_mode: str = "prod",
                 max_turns: int = DEFAULT_MAX_TURNS,
                 token_budget: int = DEFAULT_TOKEN_BUDGET):
        """
        Initialise l'agent SPT.
        
        Args:
            firebase_user_id: ID utilisateur Firebase
            collection_name: Nom de la collection (société)
            dms_system: Système DMS (pour la création du propre agent)
            dms_mode: Mode DMS (pour la création du propre agent)
            max_turns: Nombre maximum de tours (défaut: 7)
            token_budget: Budget tokens max (défaut: 15K)
        """
        self.firebase_user_id = firebase_user_id
        self.collection_name = collection_name
        self.dms_system = dms_system
        self.dms_mode = dms_mode
        self.max_turns = max_turns
        self.token_budget = token_budget
        
        # ⭐ Chat history local (ISOLÉ)
        self.chat_history: List[Dict[str, Any]] = []
        
        # ⭐ CORRECTION: Sera créé au démarrage de execute()
        # Pas injecté de l'extérieur = pas de contamination
        self._own_ai_agent: Optional[Any] = None
        
        # Configuration des outils
        self.tools: List[Dict[str, Any]] = []
        self.tool_mapping: Dict[str, Any] = {}
        
        # System prompt spécialisé
        self.system_prompt: str = ""
        
        # Cache des clarifications (TTL 1h)
        self.clarification_cache: Dict[str, Tuple[str, float]] = {}
        
        logger.info(
            f"[{self.__class__.__name__}] Initialisé pour user={firebase_user_id}, "
            f"collection={collection_name}, max_turns={max_turns}, token_budget={token_budget}"
        )
    
    @property
    def ai_agent(self) -> Any:
        """
        ⭐ Propriété pour accéder au propre BaseAIAgent (créé à l'exécution)
        Garantit qu'on utilise toujours le notre, pas celui du brain
        """
        if self._own_ai_agent is None:
            raise RuntimeError(
                f"AI Agent not initialized. Call execute() to initialize it."
            )
        return self._own_ai_agent
    
    def _initialize_own_ai_agent(self) -> None:
        """
        ⭐ NOUVEAU: Crée le propre BaseAIAgent du SPT au démarrage
        Aucun partage avec le brain - isolation complète
        """
        try:
            from ...llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize, NEW_Anthropic_Agent
            
            logger.info(
                f"[{self.__class__.__name__}] Création propre BaseAIAgent "
                f"(user={self.firebase_user_id}, collection={self.collection_name})"
            )
            
            self._own_ai_agent = BaseAIAgent(
                collection_name=self.collection_name,
                dms_system=self.dms_system,
                dms_mode=self.dms_mode,
                firebase_user_id=self.firebase_user_id
            )
            
            # ═══ CONFIGURATION DU PROVIDER ═══
            # Configurer le provider par défaut
            self._own_ai_agent.default_provider = ModelProvider.ANTHROPIC
            self._own_ai_agent.default_model_size = ModelSize.MEDIUM
            
            # Créer l'instance du provider Anthropic
            anthropic_instance = NEW_Anthropic_Agent()
            
            # Enregistrer le provider dans BaseAIAgent
            self._own_ai_agent.register_provider(
                provider=ModelProvider.ANTHROPIC,
                instance=anthropic_instance,
                default_model_size=ModelSize.MEDIUM
            )
            
            logger.info(f"[{self.__class__.__name__}] ✅ BaseAIAgent créé avec provider configuré (isolé)")
        
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] ❌ Erreur création BaseAIAgent: {e}",
                exc_info=True
            )
            raise
    
    @abstractmethod
    def initialize_system_prompt(self) -> None:
        """Initialise le system prompt spécialisé de l'agent. À implémenter."""
        pass
    
    @abstractmethod
    def initialize_tools(self) -> None:
        """Initialise les outils disponibles. À implémenter."""
        pass
    
    @abstractmethod
    def validate_instructions(self, instructions: str) -> Tuple[bool, Optional[str]]:
        """
        Valide les instructions d'entrée.
        
        Returns:
            Tuple[bool, Optional[str]]: (is_valid, error_message)
        """
        pass
    
    def execute(self, instructions: str) -> Dict[str, Any]:
        """
        Boucle principale synchrone d'exécution du SPT Agent.
        
        ⭐ ISOLATION: Crée son propre BaseAIAgent au démarrage
        
        Args:
            instructions: Instruction unique (string) pour démarrer la mission
        
        Returns:
            Dict avec keys:
            - success: bool
            - status: SPTStatus value
            - result: contenu de la réponse
            - turn_count: nombre de tours effectués
            - tokens_used: tokens approximatifs utilisés
        """
        try:
            logger.info(f"[{self.__class__.__name__}] Démarrage exécution")
            
            # ═══ CRÉATION DU PROPRE AGENT ═══
            # ⭐ IMPORTANT: Chaque SPT a son propre BaseAIAgent
            self._initialize_own_ai_agent()
            
            # ═══ VALIDATION ═══
            is_valid, error_msg = self.validate_instructions(instructions)
            if not is_valid:
                logger.error(f"[{self.__class__.__name__}] Instructions invalides: {error_msg}")
                return {
                    "success": False,
                    "status": SPTStatus.ERROR,
                    "result": f"Instructions invalides: {error_msg}",
                    "turn_count": 0,
                    "tokens_used": 0
                }
            
            # ═══ INITIALISATION ═══
            self.initialize_system_prompt()
            self.initialize_tools()
            
            # ⭐ Mettre à jour le system prompt de notre agent (pas du brain)
            self.ai_agent.update_system_prompt(self.system_prompt)
            
            turn_count = 0
            current_input = instructions
            total_tokens_used = 0
            
            # ═══ BOUCLE PRINCIPALE ═══
            while turn_count < self.max_turns:
                turn_count += 1
                
                logger.info(
                    f"[{self.__class__.__name__}] Tour {turn_count}/{self.max_turns}"
                )
                
                # ═══ VÉRIFICATION BUDGET TOKENS ═══
                try:
                    tokens_before = self.ai_agent.get_total_context_tokens(
                        self.ai_agent.default_provider
                    )
                    
                    if tokens_before >= self.token_budget:
                        logger.warning(
                            f"[{self.__class__.__name__}] Budget tokens atteint: "
                            f"{tokens_before}/{self.token_budget}"
                        )
                        
                        # ⭐ Self-healing: résumé + relance
                        current_input = self._handle_token_overflow(current_input)
                        
                        return {
                            "success": False,
                            "status": SPTStatus.TOKEN_BUDGET_EXCEEDED,
                            "result": current_input,
                            "turn_count": turn_count,
                            "tokens_used": tokens_before
                        }
                except Exception as e:
                    logger.warning(f"[{self.__class__.__name__}] Erreur vérif tokens: {e}")
                
                # ═══ APPEL LLM SYNCHRONE ═══
                try:
                    ia_responses = self.ai_agent.process_tool_use(
                        content=current_input,
                        tools=self.tools,
                        tool_mapping=self.tool_mapping,
                        size=None,  # Utiliser la taille par défaut
                        max_tokens=1024,
                        raw_output=True  # ⭐ Important: raw_output=True
                    )
                    
                    # Normaliser les réponses
                    if not isinstance(ia_responses, list):
                        ia_responses = [ia_responses] if ia_responses else []
                    
                    total_tokens_used = tokens_before
                    
                except Exception as e:
                    logger.error(f"[{self.__class__.__name__}] Erreur appel LLM: {e}", exc_info=True)
                    return {
                        "success": False,
                        "status": SPTStatus.ERROR,
                        "result": f"Erreur LLM: {str(e)}",
                        "turn_count": turn_count,
                        "tokens_used": total_tokens_used
                    }
                
                # ═══ TRAITEMENT DES RÉPONSES ═══
                next_user_input_parts = []
                mission_completed = False
                
                for response_block in ia_responses:
                    # CAS 1: TOOL_OUTPUT (dict)
                    if isinstance(response_block, dict) and "tool_output" in response_block:
                        tool_block = response_block["tool_output"]
                        tool_name = tool_block.get('tool_name', 'UnknownTool')
                        tool_content = tool_block.get('content', '')
                        
                        logger.info(f"[{self.__class__.__name__}] Outil: {tool_name}")
                        
                        # ⭐ Détection TERMINATE_TASK
                        if tool_name == 'TERMINATE_TASK':
                            logger.info(f"[{self.__class__.__name__}] ✓ TERMINATE_TASK détecté")
                            
                            # Nettoyage immédiat
                            self._cleanup()
                            
                            return {
                                "success": True,
                                "status": SPTStatus.MISSION_COMPLETED,
                                "result": tool_content,
                                "turn_count": turn_count,
                                "tokens_used": total_tokens_used
                            }
                        
                        # Autres outils: intégrer résultat
                        next_user_input_parts.append(
                            f"Résultat {tool_name}: {str(tool_content)[:500]}"
                        )
                    
                    # CAS 2: TEXT_OUTPUT (string brut)
                    elif isinstance(response_block, str):
                        logger.info(
                            f"[{self.__class__.__name__}] Clarification demandée: "
                            f"{response_block[:100]}..."
                        )
                        
                        # ⭐ Cache la clarification avec TTL
                        clarification_id = self._cache_clarification(response_block)
                        
                        # Nettoyage avant sortie
                        self._cleanup()
                        
                        return {
                            "success": True,
                            "status": SPTStatus.CLARIFICATION_NEEDED,
                            "result": response_block,
                            "clarification_id": clarification_id,
                            "turn_count": turn_count,
                            "tokens_used": total_tokens_used
                        }
                    
                    # Cas inattendu
                    else:
                        next_user_input_parts.append(
                            f"Réponse inattendue: {str(response_block)[:200]}"
                        )
                
                # ═══ PRÉPARER INPUT PROCHAIN TOUR ═══
                if next_user_input_parts:
                    current_input = "\n".join(next_user_input_parts)
                else:
                    logger.warning(f"[{self.__class__.__name__}] Aucune réponse utilisable")
                    self._cleanup()
                    
                    return {
                        "success": False,
                        "status": SPTStatus.NO_IA_ACTION,
                        "result": "L'IA n'a pas fourni de réponse claire.",
                        "turn_count": turn_count,
                        "tokens_used": total_tokens_used
                    }
            
            # ═══ MAX TOURS ATTEINT ═══
            logger.warning(
                f"[{self.__class__.__name__}] Maximum de {self.max_turns} tours atteint"
            )
            
            self._cleanup()
            
            return {
                "success": False,
                "status": SPTStatus.MAX_TURNS_REACHED,
                "result": f"Maximum de {self.max_turns} tours atteint. Dernier état: {current_input[:500]}",
                "turn_count": turn_count,
                "tokens_used": total_tokens_used
            }
        
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] ERREUR FATALE: {e}", exc_info=True)
            self._cleanup()
            
            return {
                "success": False,
                "status": SPTStatus.ERROR,
                "result": f"Erreur fatale: {str(e)}",
                "turn_count": 0,
                "tokens_used": 0
            }
    
    # ═══════════════════════════════════════════════════════════════
    # MÉTHODES UTILITAIRES
    # ═══════════════════════════════════════════════════════════════
    
    def _handle_token_overflow(self, current_state: str) -> str:
        """
        ⭐ Self-healing : quand budget tokens dépassé, génère un résumé et relance
        """
        logger.info(f"[{self.__class__.__name__}] Traitement dépassement tokens")
        
        try:
            # Générer un résumé compact
            summary_prompt = f"""Résume BRIÈVEMENT ce qui s'est passé (max 100 tokens):
            
État actuel: {current_state[:300]}

Réponds directement, sans explications supplémentaires."""
            
            summary = self.ai_agent.process_text(
                content=summary_prompt,
                size=None,
                max_tokens=200
            )
            
            summary_text = summary if isinstance(summary, str) else str(summary)[:200]
            
            # Vider l'historique et reprendre
            self.chat_history.clear()
            
            return f"""RÉSUMÉ PRÉCÉDENT:
{summary_text}

REPRENDRE MISSION avec ce contexte compressé."""
        
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Erreur self-healing: {e}")
            return current_state
    
    def _cache_clarification(self, clarification: str) -> str:
        """
        ⭐ Cache la clarification avec TTL 1h.
        Retourne un ID pour tracking.
        """
        import uuid
        
        clarification_id = f"clarif_{uuid.uuid4().hex[:8]}"
        expiration_time = time.time() + self.DEFAULT_CLARIFICATION_TTL
        
        self.clarification_cache[clarification_id] = (clarification, expiration_time)
        
        logger.info(
            f"[{self.__class__.__name__}] Clarification cachée: {clarification_id} "
            f"(TTL 1h)"
        )
        
        return clarification_id
    
    def get_cached_clarification(self, clarification_id: str) -> Optional[str]:
        """Récupère une clarification du cache si elle n'a pas expiré."""
        if clarification_id not in self.clarification_cache:
            return None
        
        clarification, expiration_time = self.clarification_cache[clarification_id]
        
        if time.time() > expiration_time:
            del self.clarification_cache[clarification_id]
            logger.info(f"[{self.__class__.__name__}] Clarification expirée: {clarification_id}")
            return None
        
        return clarification
    
    def _cleanup_expired_clarifications(self) -> None:
        """Nettoie les clarifications expirées du cache."""
        current_time = time.time()
        expired_ids = [
            cid for cid, (_, exp_time) in self.clarification_cache.items()
            if current_time > exp_time
        ]
        
        for cid in expired_ids:
            del self.clarification_cache[cid]
        
        if expired_ids:
            logger.info(f"[{self.__class__.__name__}] Nettoyage: {len(expired_ids)} clarifications expirées")
    
    def _cleanup(self) -> None:
        """Nettoie l'agent après exécution."""
        self.chat_history.clear()
        self._cleanup_expired_clarifications()
        logger.info(f"[{self.__class__.__name__}] Chat history effacé, nettoyage des ressources")
