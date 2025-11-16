"""
SPTContextManager - Agent SPT pour gestion des contextes départementaux
Hérite de BaseSPTAgent avec structure synchrone standard
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from enum import Enum

# ⭐ Import de la classe abstraite
from .base_spt_agent import BaseSPTAgent, SPTStatus
# ⭐ Import de Firebase Management (au niveau app/)
from ...firebase_providers import FirebaseManagement

logger = logging.getLogger("pinnokio.spt_context_manager")


class DepartmentEnum(str, Enum):
    """Énumération des départements principaux"""
    ACCOUNTING = "ACCOUNTING"
    HR = "HR"
    LEGAL = "LEGAL"


class ServiceEnum(str, Enum):
    """Énumération des services par département"""
    BANKS_CASH = "banks_cash"
    CONTRATS = "contrats"
    EXPENSES = "expenses"
    FINANCIAL_STATEMENT = "financial_statement"
    HR = "hr"
    INVOICES = "invoices"
    LETTERS = "letters"
    TAXES = "taxes"


class SPTContextManager(BaseSPTAgent):
    """
    ⭐ REFACTORISÉ: Hérite de BaseSPTAgent
    
    Agent SPT spécialisé dans la gestion des contextes départementaux.
    
    Responsabilités :
    - Récupérer et fournir les contextes des différents départements
    - Répondre aux questions sur les contextes et workflows
    - Mettre à jour les contextes avec workflow d'approbation
    - Publier les modifications avec timestamps
    """

    def __init__(self, 
                 firebase_user_id: str, 
                 collection_name: str,
                 brain_context: Optional[Dict[str, Any]] = None):
        """
        Initialise l'agent SPT ContextManager.
        
        Args:
            firebase_user_id: ID utilisateur Firebase
            collection_name: Nom de la collection (société)
            brain_context: Contexte du brain principal (mandate_path, dms_system, etc.)
        """
        # Récupérer dms_system depuis le contexte du brain si disponible
        dms_system = brain_context.get('dms_system', 'google_drive') if brain_context else 'google_drive'
        dms_mode = brain_context.get('dms_mode', 'prod') if brain_context else 'prod'
        
        # ⭐ Appeler le parent avec tous les params
        super().__init__(
            firebase_user_id=firebase_user_id,
            collection_name=collection_name,
            dms_system=dms_system,
            dms_mode=dms_mode,
            max_turns=7,
            token_budget=15000
        )
        
        # Contexte du brain (lecture seule)
        self.brain_context: Dict[str, Any] = brain_context or {}
        
        # ⭐ Instance Firebase Management pour accès aux données
        self.firebase_management = FirebaseManagement()
        
        # ⭐ Extraire le mandate_path du contexte (CRITIQUE)
        self.mandate_path: Optional[str] = self.brain_context.get('mandate_path')
        if not self.mandate_path:
            logger.warning(
                f"[SPTContextManager] ⚠️ mandate_path non trouvé dans brain_context. "
                f"Contexte disponible: {list(self.brain_context.keys())}"
            )
        
        logger.info(
            f"[SPTContextManager] Initialisé pour user={firebase_user_id}, "
            f"collection={collection_name}, mandate_path={self.mandate_path}"
        )
    
    def validate_instructions(self, instructions: str) -> Tuple[bool, Optional[str]]:
        """
        ⭐ À implémenter : Valide les instructions SPT.
        
        Pour ContextManager:
        - Instructions ne doivent pas être vides
        - Doivent contenir une question ou demande valide
        """
        if not instructions or len(instructions.strip()) < 3:
            return False, "Instructions trop courtes (min 3 caractères)"
        
        if len(instructions) > 5000:
            return False, "Instructions trop longues (max 5000 caractères)"
        
        return True, None
    
    def initialize_system_prompt(self) -> None:
        """
        ⭐ À implémenter : Initialise le prompt système spécialisé.
        """
        self.system_prompt = f"""Vous êtes un agent SPT spécialisé dans la gestion des contextes départementaux d'entreprise.

RÔLE PRINCIPAL :
Vous gérez les contextes et workflows des 3 départements principaux :
- ACCOUNTING : Gestion comptable, factures, taxes, états financiers
- HR : Ressources humaines, contrats employés, notes de frais
- LEGAL : Aspects juridiques, contrats, lettres officielles

CONTEXTE ENTREPRISE :
- Société : {self.brain_context.get('company_name', 'Non spécifiée')}
- Mandat : {self.mandate_path or 'Non spécifié'}
- DMS : {self.brain_context.get('dms_system', 'Non spécifié')}
- Utilisateur : {self.firebase_user_id}

STRUCTURE DES CONTEXTES (IMPORTANTE) :
Les contextes sont stockés dans Firebase à {self.mandate_path}/context/ :

1. accounting_context:
   - Contient un dictionnaire 'data' avec:
     * accounting_context_0: Les directives comptables
     * last_refresh: Timestamp de mise à jour

2. general_context:
   - Champs directement sur le document:
     * context_company_profile_report: Profil de l'entreprise
     * last_refresh: Timestamp

3. router_context:
   - Champs directement sur le document:
     * router_prompt: Dictionnaire avec prompts par service
       - banks_cash, contrats, expenses, financial_statement, hr, invoices, letters, taxes
     * last_refresh: Timestamp

DÉPARTEMENT → SERVICES MAPPING :
- ACCOUNTING : financial_statement, invoices, expenses, banks_cash
- HR : hr
- LEGAL / ADMINISTRATION : taxes, letters, contrats

INSTRUCTIONS :
1. Répondez aux questions sur les contextes en utilisant les outils disponibles
2. Si vous avez besoin de clarification, posez directement la question (string brut)
3. Maximum 7 tours pour la conversation
4. Budget tokens : 15K tokens

🎯 TERMINAISON MISSION (CRITIQUE) :
Quand votre mission est complète et que vous avez une réponse structurée à fournir, vous DEVEZ :
- **APPELER L'OUTIL TERMINATE_TASK** (pas juste mentionner le mot dans votre texte)
- Fournir un résultat structuré complet
- Ne JAMAIS écrire "TERMINATE_TASK" dans votre réponse textuelle
- L'appel de l'outil TERMINE immédiatement votre exécution

IMPORTANT :
- Pas d'explicitations inutiles
- Réponses structurées et factuelles
- Appel d'outil uniquement, pas de texte descriptif
"""
    
    def initialize_tools(self) -> None:
        """
        ⭐ À implémenter : Initialise les outils disponibles.
        """
        self.tools = [
            {
                "name": "GET_DEPARTMENT_CONTEXT",
                "description": "📋 Récupère le contexte complet d'un département (ACCOUNTING, HR, LEGAL) depuis Firebase",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "department": {
                            "type": "string",
                            "enum": ["ACCOUNTING", "HR", "LEGAL"],
                            "description": "Département cible"
                        }
                    },
                    "required": ["department"]
                }
            },
            {
                "name": "GET_SERVICE_CONTEXT",
                "description": "🔍 Récupère les détails d'un service spécifique depuis les contextes",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "enum": ["banks_cash", "contrats", "expenses", "financial_statement", "hr", "invoices", "letters", "taxes"],
                            "description": "Service cible"
                        }
                    },
                    "required": ["service"]
                }
            },
            {
                "name": "GET_ALL_CONTEXTS",
                "description": "📚 Récupère tous les contextes disponibles (accounting, general, router)",
                "input_schema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "TERMINATE_TASK",
                "description": """🎯 **APPELER CET OUTIL** pour terminer la mission.
                
QUAND L'UTILISER:
- Vous avez collecté toutes les informations demandées
- Votre résultat est structuré et complet
- La mission est accomplie

⚠️ IMPORTANT: APPELEZ cet outil, ne l'écrivez pas dans votre réponse textuelle!
L'appel de cet outil termine immédiatement votre exécution.""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Raison de la terminaison (ex: 'Mission complétée', 'Informations collectées')"
                        },
                        "result": {
                            "type": "object",
                            "description": "Résultat complet structuré contenant les données demandées"
                        },
                        "conclusion": {
                            "type": "string",
                            "description": "Résumé final concis pour l'utilisateur (2-3 phrases max)"
                        }
                    },
                    "required": ["reason", "result", "conclusion"]
                }
            }
        ]
        
        # Tool mapping
        self.tool_mapping = {
            "GET_DEPARTMENT_CONTEXT": self._get_department_context,
            "GET_SERVICE_CONTEXT": self._get_service_context,
            "GET_ALL_CONTEXTS": self._get_all_contexts
            # TERMINATE_TASK n'est pas dans le mapping (géré par la boucle parent)
        }
        
        logger.info("[SPTContextManager] Outils initialisés")
    
    # ═══════════════════════════════════════════════════════════════
    # IMPLÉMENTATION DES OUTILS - INTÉGRATION FIREBASE RÉELLE
    # ═══════════════════════════════════════════════════════════════
    
    def _get_department_context(self, department: str) -> Dict[str, Any]:
        """
        Récupère le contexte complet d'un département depuis Firebase.
        
        ⭐ INTÉGRATION RÉELLE:
        - Utilise firebase_management pour accéder à Firestore
        - Gère les 3 structures différentes (accounting/data, general/direct, router/direct)
        - Mapping automatique du département aux ressources Firebase
        - Gestion des erreurs robuste
        """
        try:
            logger.info(f"[SPTContextManager] GET_DEPARTMENT_CONTEXT: {department}")
            logger.info(f"[SPTContextManager] 📍 mandate_path utilisé: {self.mandate_path}")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non défini - impossible d'accéder aux contextes Firebase"
                }
            
            # Mapping des départements aux ressources Firebase
            department_upper = department.upper()
            
            if department_upper not in DepartmentEnum.__members__:
                return {
                    "success": False,
                    "error": f"Département '{department}' non reconnu. Valides: ACCOUNTING, HR, LEGAL"
                }
            
            # ⭐ Récupérer tous les contextes depuis Firebase
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            logger.info(f"[SPTContextManager] 📦 Contextes Firebase récupérés: {list(all_contexts.keys()) if all_contexts else 'VIDE'}")
            logger.info(f"[SPTContextManager] 📊 Contenu: accounting={bool(all_contexts.get('accounting'))}, general={bool(all_contexts.get('general'))}, router={bool(all_contexts.get('router'))}")
            
            if not all_contexts:
                logger.error(f"[SPTContextManager] ❌ AUCUN contexte trouvé pour mandate_path={self.mandate_path}")
                return {
                    "success": False,
                    "error": f"Aucun contexte trouvé pour le mandat {self.mandate_path}"
                }
            
            # Mapper le département aux données appropriées
            # ⭐ IMPORTANT: Les structures sont différentes !
            department_data = {
                "ACCOUNTING": {
                    "name": "Accounting (Comptabilité)",
                    "description": "Gestion comptable et financière",
                    "services": ["banks_cash", "invoices", "taxes", "financial_statement"],
                    "accounting_context": all_contexts.get("accounting", {}).get("accounting_context_0"),
                    "general_profile": all_contexts.get("general", {}).get("context_company_profile_report"),
                    "last_refresh": all_contexts.get("accounting", {}).get("last_refresh")
                },
                "HR": {
                    "name": "Human Resources (RH)",
                    "description": "Ressources humaines et gestion du personnel",
                    "services": ["hr"],
                    "general_profile": all_contexts.get("general", {}).get("context_company_profile_report"),
                    "last_refresh": all_contexts.get("general", {}).get("last_refresh")
                },
                "LEGAL": {
                    "name": "Legal (Juridique)",
                    "description": "Gestion juridique et contrats",
                    "services": ["contrats", "letters", "taxes"],
                    "general_profile": all_contexts.get("general", {}).get("context_company_profile_report"),
                    "router_rules": all_contexts.get("router", {}).get("router_prompt", {}),
                    "last_refresh": all_contexts.get("router", {}).get("last_refresh")
                }
            }
            
            if department_upper in department_data:
                dept_info = department_data[department_upper]
                logger.info(f"[SPTContextManager] ✅ Données département {department_upper} mappées: services={dept_info.get('services')}")
                return {
                    "success": True,
                    "department": department,
                    "context": {
                        **dept_info,
                        "retrieved_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            else:
                return {
                    "success": False,
                    "error": f"Département '{department}' non mappé"
                }
        
        except Exception as e:
            logger.error(f"[SPTContextManager] Erreur GET_DEPARTMENT_CONTEXT: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _get_service_context(self, service: str) -> Dict[str, Any]:
        """
        Récupère les détails d'un service spécifique.
        
        ⭐ INTÉGRATION RÉELLE:
        - Récupère les prompts du router_context pour ce service
        - Utilise les contextes chargés depuis Firebase
        - Mapping service -> département -> données
        """
        try:
            logger.info(f"[SPTContextManager] GET_SERVICE_CONTEXT: {service}")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non défini - impossible d'accéder aux contextes Firebase"
                }
            
            # Validation du service
            service_lower = service.lower()
            if service_lower not in ServiceEnum.__members__:
                return {
                    "success": False,
                    "error": f"Service '{service}' non reconnu. Valides: {', '.join([s.value for s in ServiceEnum])}"
                }
            
            # ⭐ Récupérer tous les contextes depuis Firebase
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            if not all_contexts:
                return {
                    "success": False,
                    "error": f"Aucun contexte trouvé pour le mandat {self.mandate_path}"
                }
            
            # Récupérer router_prompt (directement sur le document router_context)
            router_context = all_contexts.get("router", {})
            router_prompt = router_context.get("router_prompt", {})
            
            # Mapping des services aux informations
            service_details = {
                "banks_cash": {
                    "name": "Bank & Cash Management",
                    "department": "ACCOUNTING",
                    "description": "Gestion des comptes bancaires et trésorerie",
                },
                "contrats": {
                    "name": "Contract Management",
                    "department": "LEGAL / HR",
                    "description": "Gestion des contrats et conventions",
                },
                "expenses": {
                    "name": "Expense Management",
                    "department": "HR",
                    "description": "Gestion des notes de frais employés",
                },
                "financial_statement": {
                    "name": "Financial Statement",
                    "department": "ACCOUNTING",
                    "description": "États financiers et reporting",
                },
                "hr": {
                    "name": "Human Resources",
                    "department": "HR",
                    "description": "Gestion des ressources humaines",
                },
                "invoices": {
                    "name": "Invoice Management",
                    "department": "ACCOUNTING",
                    "description": "Gestion des factures fournisseur et client",
                },
                "letters": {
                    "name": "Legal Correspondence",
                    "department": "LEGAL",
                    "description": "Correspondance légale et officielle",
                },
                "taxes": {
                    "name": "Tax Compliance",
                    "department": "ACCOUNTING / LEGAL",
                    "description": "Gestion fiscale et reporting",
                }
            }
            
            if service_lower in service_details:
                details = service_details[service_lower]
                
                # ⭐ Récupérer le prompt spécifique du service depuis router_prompt
                service_prompt = router_prompt.get(service_lower, "Pas de prompt défini")
                
                return {
                    "success": True,
                    "service": service,
                    "details": {
                        **details,
                        "router_prompt": service_prompt,
                        "last_refresh": router_context.get("last_refresh"),
                        "retrieved_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            else:
                return {
                    "success": False,
                    "error": f"Service '{service}' non configuré"
                }
        
        except Exception as e:
            logger.error(f"[SPTContextManager] Erreur GET_SERVICE_CONTEXT: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _get_all_contexts(self) -> Dict[str, Any]:
        """
        Récupère tous les contextes disponibles depuis Firebase.
        
        ⭐ INTÉGRATION RÉELLE:
        - Récupère accounting_context_0, general_context_0, router_context_0
        - Inclut les metadata (timestamps, sources)
        """
        try:
            logger.info("[SPTContextManager] GET_ALL_CONTEXTS")
            logger.info(f"[SPTContextManager] 📍 mandate_path utilisé: {self.mandate_path}")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non défini - impossible d'accéder aux contextes Firebase"
                }
            
            # ⭐ Récupérer tous les contextes depuis Firebase
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            logger.info(f"[SPTContextManager] 📦 Contextes bruts Firebase: {list(all_contexts.keys()) if all_contexts else 'VIDE'}")
            
            if not all_contexts:
                logger.error(f"[SPTContextManager] ❌ AUCUN contexte trouvé pour mandate_path={self.mandate_path}")
                return {
                    "success": False,
                    "error": f"Aucun contexte trouvé pour le mandat {self.mandate_path}"
                }
            
            # Extraire les contextes avec les bonnes clés
            accounting_ctx = all_contexts.get("accounting", {})
            general_ctx = all_contexts.get("general", {})
            router_ctx = all_contexts.get("router", {})
            
            logger.info(f"[SPTContextManager] 📋 Contextes extraits: accounting={len(str(accounting_ctx))} chars, general={len(str(general_ctx))} chars, router={len(str(router_ctx))} chars")
            
            return {
                "success": True,
                "contexts": {
                    "accounting_context": accounting_ctx,
                    "general_context": general_ctx,
                    "router_context": router_ctx
                },
                "retrieved_at": datetime.now(timezone.utc).isoformat()
            }
        
        except Exception as e:
            logger.error(f"[SPTContextManager] Erreur GET_ALL_CONTEXTS: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# WRAPPER SYNCHRONE POUR INTÉGRATION AU BRAIN
# ═══════════════════════════════════════════════════════════════

def create_spt_context_manager_wrapper(brain) -> Tuple[Dict[str, Any], callable]:
    """
    Crée l'outil SPT_CONTEXT_MANAGER et son handler synchrone pour le brain.
    
    ⭐ CORRECTION - ISOLATION:
    - SPT créera son PROPRE BaseAIAgent
    - Pas de partage avec brain.pinnokio_agent
    - Chat history complètement isolé
    
    RETOURNE:
    - Définition de l'outil (pour le prompt de l'agent principal)
    - Handler synchrone (appelé par le brain)
    """
    
    # Instance persistante du SPT Agent
    # ⭐ IMPORTANT: On passe les params pour que le SPT crée son propre agent
    spt_agent = SPTContextManager(
        firebase_user_id=brain.firebase_user_id,
        collection_name=brain.collection_name,
        brain_context=brain.get_user_context()
    )
    
    # ⭐ PAS d'injection du brain.pinnokio_agent
    # Le SPT va créer le sien lors de execute()
    
    # Définition de l'outil
    tool_definition = {
        "name": "SPT_CONTEXT_MANAGER",
        "description": """🔧 Agent SPT pour gestion des contextes départementaux (ACCOUNTING, HR, LEGAL).
        
Utilisez cet outil pour :
- Expliquez les rôles et workflows des départements
- Répondez aux questions sur les contextes métier
- Consultez la structure organisationnelle
- Accédez aux informations sur les services disponibles
- Récupérez les contextes depuis Firebase

EXEMPLE :
- "Explique-moi le rôle du département ACCOUNTING"
- "Quels sont les workflows disponibles pour le service taxes?"
- "Quelle est la structure du département HR?"
- "Donne-moi tous les contextes disponibles"
""",
        "input_schema": {
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": "Instruction pour le SPT Context Manager (question, demande, etc.)"
                }
            },
            "required": ["instructions"]
        }
    }
    
    # Handler synchrone
    def handle_spt_context_manager(instructions: str, **kwargs) -> Dict[str, Any]:
        """
        ⭐ HANDLER SYNCHRONE
        Appelé depuis l'agent principal (asynchrone) via executor.
        
        ⭐ ISOLATION:
        - SPT exécute dans son propre contexte (propre BaseAIAgent)
        - Brain n'est pas affecté
        - Chat history isolé
        """
        try:
            logger.info(f"[BRAIN] 🔧 SPTContextManager appelé: {instructions[:100]}...")
            
            # Exécuter l'agent SPT de manière synchrone
            # Le SPT va créer son propre BaseAIAgent à l'intérieur
            result = spt_agent.execute(instructions)
            
            # Traiter selon le statut
            if result["status"] == SPTStatus.MISSION_COMPLETED:
                logger.info("[BRAIN] SPTContextManager mission complétée")
                return {
                    "success": True,
                    "response_type": "completed",
                    "result": result.get("result"),
                    "turn_count": result.get("turn_count"),
                    "tokens_used": result.get("tokens_used")
                }
            
            elif result["status"] == SPTStatus.CLARIFICATION_NEEDED:
                logger.info("[BRAIN] SPTContextManager demande clarification")
                return {
                    "success": True,
                    "response_type": "clarification_needed",
                    "clarification": result.get("result"),
                    "clarification_id": result.get("clarification_id"),
                    "turn_count": result.get("turn_count"),
                    "message": "L'agent SPT a besoin de clarification"
                }
            
            else:
                logger.warning(f"[BRAIN] SPTContextManager statut: {result['status']}")
                return {
                    "success": False,
                    "response_type": result.get("status"),
                    "error": result.get("result"),
                    "turn_count": result.get("turn_count")
                }
        
        except Exception as e:
            logger.error(f"[BRAIN] Erreur SPTContextManager: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Erreur SPT Context Manager: {str(e)}"
            }
    
    return tool_definition, handle_spt_context_manager
