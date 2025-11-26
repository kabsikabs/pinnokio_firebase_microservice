"""
Client LPT (Long Process Tooling) pour l'agent Pinnokio.
Gère les appels HTTP vers les agents externes (APBookkeeper, Router, Banker).
"""

import logging
import uuid
import aiohttp
import asyncio
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("pinnokio.lpt_client")


class LPTClient:
    """
    Client pour gérer les outils LPT (Long Process Tooling).
    
    Responsabilités :
    1. Fournir des définitions d'outils SIMPLIFIÉES pour l'agent (seulement IDs + instructions)
    2. Construire automatiquement les payloads complets avec les valeurs de contexte
    3. Envoyer les requêtes HTTP vers les agents externes
    4. Créer les notifications Firebase
    5. Sauvegarder les tâches dans Firebase pour suivi UI
    """
    
    def __init__(self):
        # Configuration environnement LOCAL vs PROD
        self.environment = os.getenv('PINNOKIO_ENVIRONMENT', 'LOCAL').upper()
        self.source = os.getenv('PINNOKIO_SOURCE', 'aws')

        # URLs par environnement
        if self.environment == 'LOCAL':
            self.base_url = 'http://127.0.0.1'
            self.router_url = f"{self.base_url}:8080/event-trigger"
            self.apbookeeper_url = f"{self.base_url}:8081/apbookeeper-event-trigger"
            self.banker_url = f"{self.base_url}:8082/banker-event-trigger"
            self.onboarding_url = f"{self.base_url}:8080/onboarding_manager_agent"
        else:  # PROD
            self.base_url = os.getenv('PINNOKIO_AWS_URL', 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com')
            # En PROD, tous les services utilisent le même ALB AWS
            self.router_url = f"{self.base_url}/event-trigger"
            self.apbookeeper_url = f"{self.base_url}/apbookeeper-event-trigger"
            self.banker_url = f"{self.base_url}/banker-event-trigger"
            self.onboarding_url = f"{self.base_url}/onboarding_manager_agent"

        logger.info(f"LPTClient initialisé (env={self.environment}, source={self.source})")
        logger.info(
            f"URLs - Router: {self.router_url}, APBookkeeper: {self.apbookeeper_url}, Banker: {self.banker_url}, "
            f"Onboarding: {self.onboarding_url}"
        )
    
    def check_balance_before_lpt(
        self, 
        user_id: str = None,
        mandate_path: str = None,
        estimated_cost: float = 1.0,
        lpt_tool_name: str = "LPT"
    ) -> Dict[str, Any]:
        """
        Vérifie si l'utilisateur a un solde suffisant avant d'exécuter un outil LPT.
        
        Args:
            user_id: ID de l'utilisateur
            mandate_path: Chemin du mandat dans Firebase (prioritaire sur user_id)
            estimated_cost: Coût estimé de l'opération en $ (par défaut 1.0)
            lpt_tool_name: Nom de l'outil LPT pour les logs (APBookkeeper, Router, Banker, etc.)
            
        Returns:
            dict: 
                - "sufficient": True si le solde est suffisant, False sinon
                - "current_balance": Solde actuel du compte
                - "required_balance": Solde requis (estimated_cost * 1.2)
                - "message": Message à retourner à l'agent si insuffisant
        """
        try:
            from ...firebase_providers import FirebaseManagement
            firebase_management = FirebaseManagement()
            
            # 🔍 Récupérer les informations de solde
            balance_info = firebase_management.get_balance_info(
                mandate_path=mandate_path,
                user_id=user_id
            )
            
            current_balance = balance_info.get('current_balance', 0.0)
            
            # 💰 Calculer le solde requis (marge de sécurité de 20%)
            required_balance = estimated_cost * 1.2
            
            is_sufficient = current_balance >= required_balance
            
            logger.info(
                f"[BALANCE_CHECK_{lpt_tool_name}] 💰 Vérification solde - "
                f"Solde actuel: {current_balance:.2f}$ | "
                f"Requis: {required_balance:.2f}$ (coût estimé: {estimated_cost:.2f}$) | "
                f"Statut: {'✅ SUFFISANT' if is_sufficient else '❌ INSUFFISANT'}"
            )
            
            if not is_sufficient:
                # 📢 Message clair à retourner à l'agent
                insufficient_message = (
                    f"⚠️ **SOLDE INSUFFISANT** ⚠️\n\n"
                    f"L'exécution de l'outil **{lpt_tool_name}** nécessite un solde minimum.\n\n"
                    f"📊 **État du compte :**\n"
                    f"• Solde actuel : **{current_balance:.2f} $**\n"
                    f"• Solde requis : **{required_balance:.2f} $**\n"
                    f"• Montant manquant : **{(required_balance - current_balance):.2f} $**\n\n"
                    f"💡 **Action requise :**\n"
                    f"Veuillez inviter l'utilisateur à **recharger son compte** depuis le tableau de bord "
                    f"pour continuer à utiliser les services.\n\n"
                    f"🔗 L'utilisateur peut recharger son compte dans la section **Facturation** du tableau de bord."
                )
                
                logger.warning(
                    f"[BALANCE_CHECK_{lpt_tool_name}] ⚠️ SOLDE INSUFFISANT - "
                    f"Besoin de {(required_balance - current_balance):.2f}$ supplémentaires"
                )
                
                return {
                    "sufficient": False,
                    "current_balance": current_balance,
                    "required_balance": required_balance,
                    "estimated_cost": estimated_cost,
                    "missing_amount": required_balance - current_balance,
                    "message": insufficient_message
                }
            
            return {
                "sufficient": True,
                "current_balance": current_balance,
                "required_balance": required_balance,
                "estimated_cost": estimated_cost
            }
            
        except Exception as e:
            logger.error(f"[BALANCE_CHECK_{lpt_tool_name}] ❌ Erreur vérification solde: {e}", exc_info=True)
            # En cas d'erreur, on autorise par défaut (failsafe)
            return {
                "sufficient": True,
                "current_balance": 0.0,
                "required_balance": 0.0,
                "error": str(e),
                "message": "⚠️ Impossible de vérifier le solde. Opération autorisée par défaut."
            }
    
    def get_tools_definitions_and_mapping(
        self, 
        user_id: str, 
        company_id: str, 
        thread_key: str,
        session=None,  # ⭐ LLMSession pour cache contexte thread
        brain=None     # ⭐ NOUVEAU: PinnokioBrain pour accès au contexte utilisateur
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Retourne les définitions d'outils LPT SIMPLIFIÉES et leur mapping.
        
        Important : L'agent ne voit QUE les champs essentiels (IDs + instructions).
        Le reste est automatiquement construit par les fonctions de payload.
        
        Args:
            session: LLMSession (optionnel) pour cache contexte thread
            brain: PinnokioBrain (optionnel) pour accès au contexte utilisateur déjà chargé
        """
        # Définitions d'outils SIMPLIFIÉES pour l'agent
        tools_list = [
            {
                "name": "LPT_APBookkeeper",
                "description": """📋 Saisie automatique de factures fournisseur (AP Bookkeeper).
                
Utilisez cet outil pour traiter et saisir des factures fournisseur dans l'ERP.

INSTRUCTIONS POUR L'AGENT :
- Fournissez UNIQUEMENT les IDs des fichiers (job_ids) à traiter
- Ajoutez des instructions spécifiques si nécessaire (optionnel)
- Tout le reste (collection, user, settings, approbations) est automatique

⚙️ PARAMÈTRES AUTOMATIQUES (configurés dans les paramètres système) :
- approval_required : Configuré dans workflow_params
- approval_contact_creation : Configuré dans workflow_params

EXEMPLE D'UTILISATION :
{
    "job_ids": ["file_abc123", "file_def456"],
    "general_instructions": "Vérifier les montants HT/TTC",
    "file_instructions": {
        "file_abc123": "Facture urgente, prioriser"
    }
}""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Liste des IDs de fichiers (drive_file_id) à traiter"
                        },
                        "general_instructions": {
                            "type": "string",
                            "description": "Instructions générales pour toutes les factures (optionnel)"
                        },
                        "file_instructions": {
                            "type": "object",
                            "description": "Instructions spécifiques par fichier {file_id: instructions} (optionnel)"
                        }
                    },
                    "required": ["job_ids"]
                }
            },
            {
                "name": "LPT_Router",
                "description": """🗂️ Routage automatique de documents (Router).
                
Utilisez cet outil pour router et classifier des documents automatiquement.

INSTRUCTIONS POUR L'AGENT :
- Fournissez l'ID du fichier Drive à router
- Ajoutez des instructions si nécessaire (optionnel)
- Tout le reste (approbations, workflows) est automatique

⚙️ PARAMÈTRES AUTOMATIQUES (configurés dans les paramètres système) :
- approval_required : Configuré dans workflow_params
- automated_workflow : Configuré dans workflow_params

EXEMPLE D'UTILISATION :
{
    "drive_file_id": "file_xyz789",
    "instructions": "Router vers le dossier Factures"
}""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "drive_file_id": {
                            "type": "string",
                            "description": "ID du fichier Drive à router"
                        },
                        "instructions": {
                            "type": "string",
                            "description": "Instructions de routage (optionnel)"
                        }
                    },
                    "required": ["drive_file_id"]
                }
            },
            {
                "name": "LPT_Banker",
                "description": """🏦 Réconciliation bancaire automatique (Banker).
                
Utilisez cet outil pour réconcilier des transactions bancaires avec l'ERP.

INSTRUCTIONS POUR L'AGENT :
- Fournissez le compte bancaire et les IDs de transactions
- Ajoutez des instructions si nécessaire (optionnel)

📝 TYPES D'INSTRUCTIONS :
- `instructions` : Instructions spécifiques pour ce job (placées dans jobs_data[].instructions)
- `start_instructions` : Instructions générales pour tout le batch (placées au niveau racine du payload)
- `transaction_instructions` : Instructions par transaction {transaction_id: instructions} (optionnel)

⚙️ PARAMÈTRES AUTOMATIQUES (configurés dans les paramètres système) :
- approval_required : Configuré dans workflow_params

EXEMPLE D'UTILISATION :
{
    "bank_account": "FR76 1234 5678 9012 3456",
    "transaction_ids": ["tx_001", "tx_002", "tx_003"],
    "instructions": "Vérifier les doublons",
    "start_instructions": "Instructions générales pour tout le batch",
    "transaction_instructions": {
        "tx_001": "Transaction urgente à traiter en priorité"
    }
}""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bank_account": {
                            "type": "string",
                            "description": "Compte bancaire concerné"
                        },
                        "transaction_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Liste des IDs de transactions à réconcilier"
                        },
                        "instructions": {
                            "type": "string",
                            "description": "Instructions spécifiques pour ce job (placées dans jobs_data[].instructions) - optionnel"
                        },
                        "start_instructions": {
                            "type": "string",
                            "description": "Instructions générales pour tout le batch (placées au niveau racine) - optionnel"
                        },
                        "transaction_instructions": {
                            "type": "object",
                            "description": "Instructions par transaction {transaction_id: instructions} - optionnel",
                            "additionalProperties": {"type": "string"}
                        }
                    },
                    "required": ["bank_account", "transaction_ids"]
                }
            },
            {
                "name": "LPT_APBookkeeper_ALL",
                "description": """🚀 Traitement complet des factures fournisseur disponibles.
                
Utilisez cet outil pour lancer automatiquement *toutes* les factures prêtes dans APBookkeeper (statut `to_do`).
L'outil construit le payload complet (job_ids, instructions, paramètres système) sans intervention.

⚠️ Si aucune facture n'est disponible, l'outil retourne une alerte sans lancer de traitement.""",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "LPT_Router_ALL",
                "description": """🚀 Routage automatique de tous les documents disponibles.
                
Utilisez cet outil pour router *tous* les documents en attente (statut `to_process`).
Le payload complet est généré automatiquement avec instructions et paramètres workflow.

⚠️ Aucun paramètre requis. Si aucun document n'est disponible, l'outil retourne une alerte.""",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "LPT_Banker_ALL",
                "description": """🚀 Réconciliation bancaire globale.
                
Lance automatiquement la réconciliation de toutes les transactions disponibles.
- Sans argument → toutes les banques
- Avec `bank_account` → uniquement la banque ciblée (journal_id ou nom)

📝 INSTRUCTIONS :
- `start_instructions` : Instructions générales pour tout le batch (placées au niveau racine) - optionnel

Le payload respecte le format notifications Banker (transactions regroupées par compte).
⚠️ Si aucune transaction à traiter, l'outil retourne une alerte.""",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bank_account": {
                            "type": "string",
                            "description": "Journal bancaire (ID ou nom) à traiter en priorité (optionnel)."
                        },
                        "start_instructions": {
                            "type": "string",
                            "description": "Instructions générales pour tout le batch (placées au niveau racine) - optionnel"
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "LPT_STOP_APBookkeeper",
                "description": "⏹️ Arrêter une tâche APBookkeeper en cours d'exécution",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "ID de la tâche à arrêter"
                        }
                    },
                    "required": ["job_id"]
                }
            },
            {
                "name": "LPT_STOP_Router",
                "description": "⏹️ Arrêter une tâche Router en cours d'exécution",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "ID de la tâche à arrêter"
                        }
                    },
                    "required": ["job_id"]
                }
            },
            {
                "name": "LPT_STOP_Banker",
                "description": "⏹️ Arrêter une tâche Banker en cours d'exécution",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "ID de la tâche à arrêter"
                        },
                        "batch_id": {
                            "type": "string",
                            "description": "ID du batch (optionnel)"
                        }
                    },
                    "required": ["job_id"]
                }
            }
        ]
        
        # Mapping des outils vers leurs fonctions
        # ⭐ Utiliser des fonctions async explicites au lieu de lambdas
        # pour que asyncio.iscoroutinefunction() fonctionne correctement
        
        async def handle_lpt_apbookeeper(**kwargs):
            return await self.launch_apbookeeper(
                user_id=user_id, 
                company_id=company_id, 
                thread_key=thread_key,
                session=session,
                brain=brain,
                **kwargs
            )
        
        async def handle_lpt_router(**kwargs):
            return await self.launch_router(
                user_id=user_id, 
                company_id=company_id, 
                thread_key=thread_key,
                session=session,
                brain=brain,
                **kwargs
            )
        
        async def handle_lpt_banker(**kwargs):
            return await self.launch_banker(
                user_id=user_id, 
                company_id=company_id, 
                thread_key=thread_key,
                session=session,
                brain=brain,
                **kwargs
            )
        
        async def handle_lpt_apbookeeper_all(**kwargs):
            return await self.launch_apbookeeper_all(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                session=session,
                brain=brain,
                **kwargs
            )
        
        async def handle_lpt_router_all(**kwargs):
            return await self.launch_router_all(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                session=session,
                brain=brain,
                **kwargs
            )
        
        async def handle_lpt_banker_all(**kwargs):
            return await self.launch_banker_all(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                session=session,
                brain=brain,
                **kwargs
            )
        
        async def handle_lpt_stop_apbookeeper(**kwargs):
            return await self.stop_apbookeeper(
                user_id=user_id, 
                company_id=company_id, 
                **kwargs
            )
        
        async def handle_lpt_stop_router(**kwargs):
            return await self.stop_router(
                user_id=user_id, 
                company_id=company_id, 
                **kwargs
            )
        
        async def handle_lpt_stop_banker(**kwargs):
            return await self.stop_banker(
                user_id=user_id, 
                company_id=company_id, 
                **kwargs
            )
        
        tools_mapping = {
            "LPT_APBookkeeper": handle_lpt_apbookeeper,
            "LPT_Router": handle_lpt_router,
            "LPT_Banker": handle_lpt_banker,
            "LPT_APBookkeeper_ALL": handle_lpt_apbookeeper_all,
            "LPT_Router_ALL": handle_lpt_router_all,
            "LPT_Banker_ALL": handle_lpt_banker_all,
            "LPT_STOP_APBookkeeper": handle_lpt_stop_apbookeeper,
            "LPT_STOP_Router": handle_lpt_stop_router,
            "LPT_STOP_Banker": handle_lpt_stop_banker
        }
        
        return tools_list, tools_mapping
    
    async def launch_apbookeeper_all(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        session=None,
        brain=None,
        execution_id: Optional[str] = None,
        execution_plan: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lance automatiquement toutes les factures APBookkeeper disponibles (statut to_do).
        """
        try:
            if not brain:
                raise ValueError("Brain est requis pour lancer APBookkeeper_ALL")
            
            context = brain.get_user_context()
            mandate_path = context.get('mandate_path')
            
            # 🔍 Compter le nombre de factures à traiter
            apbookeeper_jobs = ((brain.jobs_data or {}).get("APBOOKEEPER", {}) if brain else {}).get("to_do", []) or []
            nb_invoices = len(apbookeeper_jobs)
            
            # 🛡️ VÉRIFICATION DU SOLDE AVANT L'ENVOI
            # Coût estimé : 1.0$ par facture
            estimated_cost = nb_invoices * 1.0
            
            balance_check = self.check_balance_before_lpt(
                mandate_path=mandate_path,
                user_id=user_id,
                estimated_cost=estimated_cost,
                lpt_tool_name="APBookkeeper_ALL"
            )
            
            if not balance_check.get("sufficient", False):
                # ❌ SOLDE INSUFFISANT - Retourner le message à l'agent
                logger.warning(
                    f"[LPT_APBookkeeper_ALL] ❌ BLOCAGE - Solde insuffisant "
                    f"({balance_check.get('current_balance', 0):.2f}$ < {balance_check.get('required_balance', 0):.2f}$)"
                )
                return {
                    "status": "insufficient_balance",
                    "error": "Solde insuffisant pour exécuter cette opération",
                    "balance_info": {
                        "current_balance": balance_check.get("current_balance", 0.0),
                        "required_balance": balance_check.get("required_balance", 0.0),
                        "missing_amount": balance_check.get("missing_amount", 0.0)
                    },
                    "nb_invoices_to_process": nb_invoices,
                    "message": balance_check.get("message", "Solde insuffisant")
                }
            
            # ✅ SOLDE SUFFISANT - Continuer l'exécution
            logger.info(
                f"[LPT_APBookkeeper_ALL] ✅ Solde vérifié et suffisant "
                f"({balance_check.get('current_balance', 0):.2f}$ >= {balance_check.get('required_balance', 0):.2f}$) "
                f"pour {nb_invoices} factures"
            )
            
            ap_jobs = (brain.jobs_data or {}).get("APBOOKEEPER", {}) if brain else {}
            to_do_jobs = ap_jobs.get("to_do", []) or []
            
            job_ids: List[str] = []
            file_instructions: Dict[str, str] = {}
            
            for job in to_do_jobs:
                job_id = job.get("job_id")
                if not job_id:
                    continue
                job_ids.append(job_id)
                instruction = job.get("instructions") or job.get("instruction") or ""
                if instruction:
                    file_instructions[job_id] = instruction
            
            if not job_ids:
                logger.warning("[LPT_APBookkeeper_ALL] Aucun job to_do disponible pour lancement automatique.")
                return {
                    "status": "no_jobs_available",
                    "message": "Aucune facture APBookkeeper au statut 'to_do' n'est disponible pour lancement."
                }
            
            logger.info(f"[LPT_APBookkeeper_ALL] Lancement automatique de {len(job_ids)} facture(s).")
            
            result = await self.launch_apbookeeper(
                user_id=user_id,
                company_id=company_id,
                thread_key=thread_key,
                job_ids=job_ids,
                general_instructions=None,
                file_instructions=file_instructions if file_instructions else None,
                session=session,
                brain=brain,
                execution_id=execution_id,
                execution_plan=execution_plan
            )
            
            if isinstance(result, dict) and result.get("status") == "queued":
                result["nb_jobs_launched"] = len(job_ids)
                result["launched_from"] = "LPT_APBookkeeper_ALL"
            
            return result
        
        except Exception as e:
            logger.error(f"[LPT_APBookkeeper_ALL] Erreur: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def launch_apbookeeper(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        job_ids: List[str],
        general_instructions: Optional[str] = None,
        file_instructions: Optional[Dict[str, str]] = None,
        session=None,  # ⭐ LLMSession pour cache (non utilisé, contexte vient du brain)
        brain=None,    # ⭐ PinnokioBrain pour accès au contexte utilisateur
        execution_id: Optional[str] = None,  # ⭐ ID d'exécution pour traçabilité
        execution_plan: Optional[str] = None  # ⭐ Mode d'exécution (NOW, ON_DEMAND, etc.)
    ) -> Dict[str, Any]:
        """
        Lance une tâche APBookkeeper pour la saisie de factures fournisseur.
        
        Cette fonction construit automatiquement le payload complet et l'envoie à l'agent APBookkeeper.
        Les paramètres d'approbation (approval_required, approval_contact_creation) sont récupérés
        automatiquement depuis workflow_params.
        """
        try:
            # ⭐ Récupérer le contexte depuis le brain (OBLIGATOIRE)
            if not brain:
                raise ValueError("Brain est requis pour lancer APBookkeeper")
            
            context = brain.get_user_context()
            mandate_path = context.get('mandate_path')
            
            # 🛡️ VÉRIFICATION DU SOLDE AVANT L'ENVOI
            # Coût estimé : 1.0$ par facture (ajustable selon vos tarifs)
            estimated_cost = len(job_ids) * 1.0
            
            balance_check = self.check_balance_before_lpt(
                mandate_path=mandate_path,
                user_id=user_id,
                estimated_cost=estimated_cost,
                lpt_tool_name="APBookkeeper"
            )
            
            if not balance_check.get("sufficient", False):
                # ❌ SOLDE INSUFFISANT - Retourner le message à l'agent
                logger.warning(
                    f"[LPT_APBookkeeper] ❌ BLOCAGE - Solde insuffisant "
                    f"({balance_check.get('current_balance', 0):.2f}$ < {balance_check.get('required_balance', 0):.2f}$)"
                )
                return {
                    "status": "insufficient_balance",
                    "error": "Solde insuffisant pour exécuter cette opération",
                    "balance_info": {
                        "current_balance": balance_check.get("current_balance", 0.0),
                        "required_balance": balance_check.get("required_balance", 0.0),
                        "missing_amount": balance_check.get("missing_amount", 0.0)
                    },
                    "message": balance_check.get("message", "Solde insuffisant")
                }
            
            # ✅ SOLDE SUFFISANT - Continuer l'exécution
            logger.info(
                f"[LPT_APBookkeeper] ✅ Solde vérifié et suffisant "
                f"({balance_check.get('current_balance', 0):.2f}$ >= {balance_check.get('required_balance', 0):.2f}$)"
            )
            logger.info(f"[LPT_APBookkeeper] Contexte récupéré depuis brain: mandate_path={context.get('mandate_path')}")
            
            # ⭐ NOUVEAU: Récupérer les paramètres d'approbation depuis workflow_params
            workflow_params = context.get('workflow_params', {})
            apbookeeper_params = workflow_params.get('Apbookeeper_param', {})
            
            approval_required = apbookeeper_params.get('apbookeeper_approval_required', False)
            approval_contact_creation = apbookeeper_params.get('apbookeeper_approval_contact_creation', False)
            
            logger.info(
                f"[LPT_APBookkeeper] Paramètres workflow: "
                f"approval_required={approval_required}, "
                f"approval_contact_creation={approval_contact_creation}"
            )
            
            # ⭐ RÉSOLUTION DES FILE_NAMES depuis le cache APBookkeeper
            # Chercher les job_ids dans brain.jobs_data['APBOOKEEPER']
            apbookeeper_jobs = (brain.jobs_data or {}).get("APBOOKEEPER", {}) if brain else {}
            
            # Agréger toutes les listes possibles
            aggregated_ap_jobs = []
            for key in ["to_do", "in_process", "pending", "processed"]:
                jobs_list = apbookeeper_jobs.get(key)
                if isinstance(jobs_list, list):
                    aggregated_ap_jobs.extend(jobs_list)
            
            logger.info(f"[LPT_APBookkeeper] 🔍 Cache chargé: {len(aggregated_ap_jobs)} factures disponibles")
            
            # Valider et résoudre chaque job_id
            jobs_data = []
            invalid_ids = []
            
            for job_id in job_ids:
                found = False
                resolved_file_name = None
                
                # Rechercher le job_id dans le cache
                for job in aggregated_ap_jobs:
                    if job.get("job_id") == job_id:
                        resolved_file_name = job.get("file_name", f"document_{job_id}")
                        found = True
                        logger.info(f"[LPT_APBookkeeper] ✅ job_id={job_id} résolu → file_name={resolved_file_name}")
                        break
                
                if not found:
                    invalid_ids.append(job_id)
                    logger.warning(f"[LPT_APBookkeeper] ⚠️ job_id={job_id} non trouvé dans le cache")
                else:
                    # Construire job_data avec le vrai nom de fichier
                    job_data = {
                        "file_name": resolved_file_name,
                        "job_id": job_id,
                        "instructions": file_instructions.get(job_id, "") if file_instructions else "",
                        "status": "to_process",
                        "approval_required": approval_required,
                        "approval_contact_creation": approval_contact_creation
                    }
                    jobs_data.append(job_data)
            
            # ⭐ Si AUCUN job_id valide : retourner erreur avec liste des IDs disponibles
            if len(jobs_data) == 0:
                error_msg = (
                    f"❌ Aucune facture valide trouvée parmi les job_ids fournis.\n\n"
                    f"📋 job_ids invalides ({len(invalid_ids)}) : {invalid_ids}\n\n"
                    f"⚠️ Les valeurs fournies ne correspondent à aucun job. Fournissez le `job_id` exact ; "
                    f"sinon la facture ne sera pas lancée.\n\n"
                    f"📄 Factures disponibles ({len(aggregated_ap_jobs)} factures) :\n"
                )
                # Lister les 10 premières factures disponibles
                for idx, job in enumerate(aggregated_ap_jobs[:10], 1):
                    doc_name = job.get('file_name', 'Sans nom')
                    doc_id = job.get('job_id', 'Sans ID')
                    error_msg += f"  {idx}. {doc_name} (job_id: {doc_id})\n"
                
                if len(aggregated_ap_jobs) > 10:
                    error_msg += f"  ... et {len(aggregated_ap_jobs) - 10} autres factures.\n"
                
                error_msg += (
                    f"\n💡 Pour traiter des factures, utilisez l'un des 'job_id' listés ci-dessus.\n"
                    f"Vous pouvez demander à l'utilisateur de préciser quelles factures traiter si besoin."
                )
                
                logger.warning(f"[LPT_APBookkeeper] ❌ Aucun job_id valide - Retour erreur à l'agent")
                
                return {
                    "status": "error",
                    "error": error_msg,
                    "invalid_ids": invalid_ids,
                    "available_invoices_count": len(aggregated_ap_jobs)
                }
            
            # ⭐ Si certains job_ids invalides : continuer avec warning
            if len(invalid_ids) > 0:
                logger.warning(
                    f"[LPT_APBookkeeper] ⚠️ {len(invalid_ids)} job_id(s) invalide(s) ignoré(s): {invalid_ids}. "
                    f"Continuation avec {len(jobs_data)} facture(s) valide(s)."
                )
            
            # Générer batch_id
            batch_id = f'batch_{uuid.uuid4().hex[:10]}'
            
            # ⭐ Construire settings (ALIGNÉ avec Router)
            settings = [
                {'communication_mode': context['communication_mode']},
                {'log_communication_mode': context['log_communication_mode']},
                {'dms_system': context['dms_system']}
            ]
            
            # ⭐ NOUVEAU: Récupérer les informations de traçabilité depuis le brain
            execution_id = execution_id or (brain.active_task_data.get("execution_id") if brain and brain.active_task_data else None)
            execution_plan = execution_plan or (brain.active_task_data.get("execution_plan") if brain and brain.active_task_data else None)

            # ⭐ Récupérer le vrai nom du thread depuis RTDB (ALIGNÉ avec Router)
            from ...firebase_providers import FirebaseRealtimeChat
            rtdb = FirebaseRealtimeChat()
            thread_name = rtdb.get_thread_name(
                space_code=company_id,
                thread_key=thread_key,
                mode='chats'  # Mode par défaut pour les conversations agent
            ) or thread_key  # Fallback sur thread_key si non trouvé
            
            # ⭐ Informations de traçabilité pour le callback (ALIGNÉ avec Router)
            traceability_info = {
                "thread_key": thread_key,
                "thread_name": thread_name,  # ✅ Vrai nom du thread depuis RTDB
                "execution_id": execution_id,  # ⭐ ID d'exécution pour traçabilité complète
                "execution_plan": execution_plan,  # ⭐ mode d'exécution (NOW, ON_DEMAND, etc.)
                "initiated_at": datetime.now(timezone.utc).isoformat(),  # ⭐ timestamp d'initiation
                "source": "pinnokio_brain"  # ⭐ source de l'appel
            }

            payload = {
                # Informations de base
                "collection_name": company_id,
                "user_id": user_id,
                "client_uuid": context['client_uuid'],
                "mandates_path": context['mandate_path'],
                "batch_id": batch_id,

                # Données de la tâche
                "jobs_data": jobs_data,
                "start_instructions": general_instructions,

                # Configuration
                "settings": settings,

                # Informations de traçabilité pour callback
                "traceability": traceability_info,  # ⭐ NOUVEAU: Section traçabilité complète
                "pub_sub_id": batch_id  # Utiliser batch_id comme pub_sub_id
            }
            
            logger.info(f"[LPT_APBookkeeper] Lancement - batch_id={batch_id}, nb_jobs={len(jobs_data)}, thread={thread_key}, execution_id={execution_id}, plan={execution_plan}")
            
            # Envoyer la requête HTTP
            url = self.apbookeeper_url
            
            # ⭐ LOG: Afficher l'URL et le payload avant l'envoi
            logger.info(f"[LPT_APBookkeeper] 📤 Envoi HTTP POST vers: {url}")
            logger.info(f"[LPT_APBookkeeper] 📦 Payload complet: {payload}")
            
            try:
                async with aiohttp.ClientSession() as session:
                    logger.info(f"[LPT_APBookkeeper] 🔄 Connexion en cours vers {url}...")
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        status = response.status
                        logger.info(f"[LPT_APBookkeeper] ✅ Réponse HTTP reçue: status={status}")
                        
                        if status == 202:
                            logger.info(f"[LPT_APBookkeeper] ✓ Lancé avec succès - batch_id={batch_id}")
                            
                            # Sauvegarder la tâche dans Firebase
                            task_id = batch_id
                            await self._save_task_to_firebase(
                                user_id=user_id,
                                thread_key=thread_key,
                                task_id=task_id,
                                task_type="APBookkeeper",
                                payload=payload,
                                status="queued"
                            )
                            
                            # Créer les notifications Firebase
                            await self._create_apbookeeper_notifications(
                                user_id=user_id,
                                company_id=company_id,
                                company_name=context['company_name'],
                                batch_id=batch_id,
                                jobs_data=jobs_data
                            )
                            
                            # Message de retour avec warning si certains job_ids invalides
                            success_message = f"✓ APBookkeeper lancé : {len(jobs_data)} facture(s) en cours de traitement"
                            if len(invalid_ids) > 0:
                                success_message += f"\n⚠️ {len(invalid_ids)} job_id(s) invalide(s) ignoré(s): {invalid_ids}"
                            
                            return {
                                "status": "queued",
                                "task_id": task_id,
                                "batch_id": batch_id,
                                "nb_jobs_valid": len(jobs_data),
                                "nb_jobs_invalid": len(invalid_ids),
                                "invalid_ids": invalid_ids if len(invalid_ids) > 0 else [],
                                "thread_key": thread_key,
                                "message": success_message
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"[LPT_APBookkeeper] ❌ Erreur HTTP {status}: {error_text}")
                            return {
                                "status": "error",
                                "error": f"HTTP {status}: {error_text}"
                            }
            
            except aiohttp.ClientError as ce:
                # Erreur de connexion ou réseau
                logger.error(f"[LPT_APBookkeeper] ❌ Erreur de connexion HTTP: {ce}", exc_info=True)
                return {
                    "status": "error",
                    "error": f"Erreur de connexion: {str(ce)}",
                    "error_type": "connection_error"
                }
            
            except asyncio.TimeoutError:
                # Timeout
                logger.error(f"[LPT_APBookkeeper] ⏱️ Timeout après 30s vers {url}")
                return {
                    "status": "error",
                    "error": "Timeout de connexion (30s)",
                    "error_type": "timeout"
                }
        
        except ValueError as ve:
            # Erreur de configuration (données manquantes)
            logger.error(f"[LPT_APBookkeeper] Erreur de configuration: {ve}")
            return {
                "status": "configuration_error",
                "error": str(ve),
                "error_type": "missing_user_data",
                "message": "⚠️ Configuration utilisateur incomplète. Vérifiez que l'utilisateur est correctement enregistré."
            }
        
        except Exception as e:
            # Erreur technique
            logger.error(f"[LPT_APBookkeeper] Erreur technique: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "error_type": "technical_error"
            }
    
    async def launch_router_all(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        session=None,
        brain=None,
        execution_id: Optional[str] = None,
        execution_plan: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lance automatiquement tous les documents Router disponibles (statut to_process).
        """
        try:
            if not brain:
                raise ValueError("Brain est requis pour lancer Router_ALL")
            
            context = brain.get_user_context()
            mandate_path = context.get('mandate_path')
            
            # 🔍 Compter le nombre de documents à router
            router_jobs = ((brain.jobs_data or {}).get("ROUTER", {}) if brain else {}).get("to_process", []) or []
            nb_documents = len(router_jobs)
            
            # 🛡️ VÉRIFICATION DU SOLDE AVANT L'ENVOI
            # Coût estimé : 0.5$ par document
            estimated_cost = nb_documents * 0.5
            
            balance_check = self.check_balance_before_lpt(
                mandate_path=mandate_path,
                user_id=user_id,
                estimated_cost=estimated_cost,
                lpt_tool_name="Router_ALL"
            )
            
            if not balance_check.get("sufficient", False):
                # ❌ SOLDE INSUFFISANT - Retourner le message à l'agent
                logger.warning(
                    f"[LPT_Router_ALL] ❌ BLOCAGE - Solde insuffisant "
                    f"({balance_check.get('current_balance', 0):.2f}$ < {balance_check.get('required_balance', 0):.2f}$)"
                )
                return {
                    "status": "insufficient_balance",
                    "error": "Solde insuffisant pour exécuter cette opération",
                    "balance_info": {
                        "current_balance": balance_check.get("current_balance", 0.0),
                        "required_balance": balance_check.get("required_balance", 0.0),
                        "missing_amount": balance_check.get("missing_amount", 0.0)
                    },
                    "nb_documents_to_route": nb_documents,
                    "message": balance_check.get("message", "Solde insuffisant")
                }
            
            # ✅ SOLDE SUFFISANT - Continuer l'exécution
            logger.info(
                f"[LPT_Router_ALL] ✅ Solde vérifié et suffisant "
                f"({balance_check.get('current_balance', 0):.2f}$ >= {balance_check.get('required_balance', 0):.2f}$) "
                f"pour {nb_documents} documents"
            )
            logger.info(f"[LPT_Router_ALL] Contexte récupéré: mandate_path={mandate_path}")
            
            workflow_params = context.get('workflow_params', {})
            router_params = workflow_params.get('Router_param', {})
            
            approval_required = router_params.get('router_approval_required', False)
            automated_workflow = router_params.get('router_automated_workflow', True)
            
            router_jobs = ((brain.jobs_data or {}).get("ROUTER", {}) if brain else {}).get("to_process", []) or []
            
            documents_to_route: List[Dict[str, Any]] = []
            for job in router_jobs:
                drive_file_id = job.get("id") or job.get("drive_file_id")
                if not drive_file_id:
                    continue
                documents_to_route.append({
                    "drive_file_id": drive_file_id,
                    "file_name": job.get("name") or job.get("file_name") or drive_file_id,
                    "instructions": job.get("instructions") or ""
                })
            
            if not documents_to_route:
                logger.warning("[LPT_Router_ALL] Aucun document à router disponible.")
                return {
                    "status": "no_jobs_available",
                    "message": "Aucun document au statut 'to_process' n'est disponible pour routage."
                }
            
            execution_id = execution_id or (brain.active_task_data.get("execution_id") if brain and brain.active_task_data else None)
            execution_plan = execution_plan or (brain.active_task_data.get("execution_plan") if brain and brain.active_task_data else None)
            
            from ...firebase_providers import FirebaseRealtimeChat
            rtdb = FirebaseRealtimeChat()
            thread_name = rtdb.get_thread_name(
                space_code=company_id,
                thread_key=thread_key,
                mode='chats'
            ) or thread_key
            
            pub_sub_id = f"router_batch_{uuid.uuid4().hex[:10]}"
            traceability_info = {
                "thread_key": thread_key,
                "thread_name": thread_name,
                "execution_id": execution_id,
                "execution_plan": execution_plan,
                "initiated_at": datetime.now(timezone.utc).isoformat(),
                "source": "pinnokio_brain"
            }
            
            jobs_data_payload = [
                {
                    "file_name": doc["file_name"],
                    "drive_file_id": doc["drive_file_id"],
                    "instructions": doc["instructions"],
                    "status": 'to_route',
                    "approval_required": approval_required,
                    "automated_workflow": automated_workflow
                }
                for doc in documents_to_route
            ]
            
            payload = {
                "collection_name": company_id,
                "user_id": user_id,
                "client_uuid": context['client_uuid'],
                "mandates_path": context['mandate_path'],
                "batch_id": pub_sub_id,
                "jobs_data": jobs_data_payload,
                "settings": [
                    {"communication_mode": context['communication_mode']},
                    {"log_communication_mode": context['log_communication_mode']},
                    {"dms_system": context['dms_system']}
                ],
                "traceability": traceability_info,
                "pub_sub_id": pub_sub_id,
                "start_instructions": None
            }
            
            logger.info(f"[LPT_Router_ALL] Lancement batch - documents={len(documents_to_route)}, pub_sub_id={pub_sub_id}")
            logger.info(f"[LPT_Router_ALL] Payload: {payload}")
            
            url = self.router_url
            try:
                async with aiohttp.ClientSession() as session_http:
                    async with session_http.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        status = response.status
                        logger.info(f"[LPT_Router_ALL] Réponse HTTP: {status}")
                        
                        if status == 202:
                            task_id = pub_sub_id
                            await self._save_task_to_firebase(
                                user_id=user_id,
                                thread_key=thread_key,
                                task_id=task_id,
                                task_type="Router",
                                payload=payload,
                                status="queued"
                            )
                            
                            for doc in documents_to_route:
                                await self._create_router_notification(
                                    user_id=user_id,
                                    company_id=company_id,
                                    company_name=context['company_name'],
                                    drive_file_id=doc["drive_file_id"],
                                    file_name=doc["file_name"],
                                    pub_sub_id=pub_sub_id,
                                    instructions=doc["instructions"]
                                )
                            
                            return {
                                "status": "queued",
                                "task_id": task_id,
                                "batch_id": pub_sub_id,
                                "nb_documents": len(documents_to_route),
                                "thread_key": thread_key,
                                "message": f"✓ Router lancé pour {len(documents_to_route)} document(s)."
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"[LPT_Router_ALL] Erreur HTTP {status}: {error_text}")
                            return {
                                "status": "error",
                                "error": f"HTTP {status}: {error_text}"
                            }
            except aiohttp.ClientError as ce:
                logger.error(f"[LPT_Router_ALL] Erreur connexion HTTP: {ce}", exc_info=True)
                return {
                    "status": "error",
                    "error": f"Erreur de connexion: {str(ce)}",
                    "error_type": "connection_error"
                }
            except asyncio.TimeoutError:
                logger.error("[LPT_Router_ALL] Timeout HTTP (30s)")
                return {
                    "status": "error",
                    "error": "Timeout de connexion (30s)",
                    "error_type": "timeout"
                }
        
        except ValueError as ve:
            logger.error(f"[LPT_Router_ALL] Erreur de configuration: {ve}")
            return {
                "status": "configuration_error",
                "error": str(ve),
                "error_type": "missing_user_data"
            }
        except Exception as e:
            logger.error(f"[LPT_Router_ALL] Erreur technique: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "error_type": "technical_error"
            }
    
    async def launch_router(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        drive_file_id: str,
        instructions: Optional[str] = None,
        session=None,  # ⭐ LLMSession pour cache (non utilisé, contexte vient du brain)
        brain=None,    # ⭐ PinnokioBrain pour accès au contexte utilisateur
        execution_id: Optional[str] = None,  # ⭐ ID d'exécution pour traçabilité
        execution_plan: Optional[str] = None  # ⭐ Mode d'exécution (NOW, ON_DEMAND, etc.)
    ) -> Dict[str, Any]:
        """
        Lance une tâche Router pour le routage de documents.
        Les paramètres (approval_required, automated_workflow) sont récupérés automatiquement
        depuis workflow_params.
        """
        try:
            # ⭐ Récupérer le contexte depuis le brain (OBLIGATOIRE)
            if not brain:
                raise ValueError("Brain est requis pour lancer Router")
            
            context = brain.get_user_context()
            mandate_path = context.get('mandate_path')
            
            # 🛡️ VÉRIFICATION DU SOLDE AVANT L'ENVOI
            # Coût estimé : 0.5$ par document (ajustable selon vos tarifs)
            estimated_cost = 0.5
            
            balance_check = self.check_balance_before_lpt(
                mandate_path=mandate_path,
                user_id=user_id,
                estimated_cost=estimated_cost,
                lpt_tool_name="Router"
            )
            
            if not balance_check.get("sufficient", False):
                # ❌ SOLDE INSUFFISANT - Retourner le message à l'agent
                logger.warning(
                    f"[LPT_Router] ❌ BLOCAGE - Solde insuffisant "
                    f"({balance_check.get('current_balance', 0):.2f}$ < {balance_check.get('required_balance', 0):.2f}$)"
                )
                return {
                    "status": "insufficient_balance",
                    "error": "Solde insuffisant pour exécuter cette opération",
                    "balance_info": {
                        "current_balance": balance_check.get("current_balance", 0.0),
                        "required_balance": balance_check.get("required_balance", 0.0),
                        "missing_amount": balance_check.get("missing_amount", 0.0)
                    },
                    "message": balance_check.get("message", "Solde insuffisant")
                }
            
            # ✅ SOLDE SUFFISANT - Continuer l'exécution
            logger.info(
                f"[LPT_Router] ✅ Solde vérifié et suffisant "
                f"({balance_check.get('current_balance', 0):.2f}$ >= {balance_check.get('required_balance', 0):.2f}$)"
            )
            logger.info(f"[LPT_Router] Contexte récupéré depuis brain: mandate_path={mandate_path}")
            
            # ⭐ NOUVEAU: Récupérer les paramètres depuis workflow_params
            workflow_params = context.get('workflow_params', {})
            router_params = workflow_params.get('Router_param', {})
            
            approval_required = router_params.get('router_approval_required', False)
            automated_workflow = router_params.get('router_automated_workflow', True)
            
            logger.info(
                f"[LPT_Router] Paramètres workflow: "
                f"approval_required={approval_required}, "
                f"automated_workflow={automated_workflow}"
            )
            
            # Construire le payload
            job_id = str(uuid.uuid4())
            pub_sub_id = f"router_{drive_file_id}_{uuid.uuid4().hex[:6]}"

            # ⭐ NOUVEAU: Récupérer les informations de traçabilité depuis le brain
            execution_id = execution_id or (brain.active_task_data.get("execution_id") if brain and brain.active_task_data else None)
            execution_plan = execution_plan or (brain.active_task_data.get("execution_plan") if brain and brain.active_task_data else None)

            # ⭐ RÉSOLUTION DU FILE_NAME depuis le cache jobs_data
            # Chercher le job correspondant au drive_file_id dans brain.jobs_data['ROUTER']
            resolved_file_name = drive_file_id  # Fallback par défaut
            
            # 🔍 DEBUG: Afficher la structure complète de ROUTER
            if brain and brain.jobs_data:
                logger.info(f"[LPT_Router] 🔍 DEBUG brain.jobs_data keys: {list(brain.jobs_data.keys())}")
                if 'ROUTER' in brain.jobs_data:
                    logger.info(f"[LPT_Router] 🔍 DEBUG brain.jobs_data['ROUTER'] keys: {list(brain.jobs_data['ROUTER'].keys())}")
                else:
                    logger.warning(f"[LPT_Router] ⚠️ brain.jobs_data existe MAIS pas de clé 'ROUTER'")
            else:
                logger.warning(f"[LPT_Router] ⚠️ brain.jobs_data est None ou vide")
            
            if brain and brain.jobs_data and 'ROUTER' in brain.jobs_data:
                # ✅ Correction: c'est 'to_process' et non 'unprocessed'
                router_jobs = brain.jobs_data['ROUTER'].get('to_process', [])
                
                # 🔍 DEBUG: Afficher la structure
                logger.info(f"[LPT_Router] 🔍 DEBUG - Nombre de jobs to_process: {len(router_jobs)}")
                logger.info(f"[LPT_Router] 🔍 DEBUG - Recherche drive_file_id: {drive_file_id}")
                
                if router_jobs and len(router_jobs) > 0:
                    # Afficher un exemple de structure
                    logger.info(f"[LPT_Router] 🔍 DEBUG - Exemple job[0] keys: {list(router_jobs[0].keys()) if router_jobs else 'vide'}")
                
                found = False
                for job in router_jobs:
                    job_id = job.get('id')
                    logger.info(f"[LPT_Router] 🔍 DEBUG - Comparaison job_id={job_id} vs drive_file_id={drive_file_id}")
                    if job_id == drive_file_id:
                        # Les jobs Router utilisent 'name' pour le nom du fichier
                        resolved_file_name = job.get('name', drive_file_id)
                        logger.info(f"[LPT_Router] ✅ file_name résolu depuis cache: {resolved_file_name}")
                        found = True
                        break
                
                # ⭐ CORRECTION : Si fichier non trouvé, retourner une ERREUR au lieu de continuer
                if not found:
                    error_msg = (
                        f"❌ Le document avec drive_file_id '{drive_file_id}' n'existe pas dans la liste des documents à router.\n\n"
                        f"⚠️ La valeur fournie ne correspond à aucun document connu. Fournissez le `drive_file_id` exact ; "
                        f"sinon le routage ne sera pas lancé.\n\n"
                        f"📋 Documents disponibles ({len(router_jobs)} documents) :\n"
                    )
                    # Lister les 10 premiers documents disponibles pour aider l'agent
                    for idx, job in enumerate(router_jobs[:10], 1):
                        doc_name = job.get('name', 'Sans nom')
                        doc_id = job.get('id', 'Sans ID')
                        error_msg += f"  {idx}. {doc_name} (ID: {doc_id})\n"
                    
                    if len(router_jobs) > 10:
                        error_msg += f"  ... et {len(router_jobs) - 10} autres documents.\n"
                    
                    error_msg += (
                        f"\n💡 Pour router un document, utilisez l'un des 'id' listés ci-dessus.\n"
                        f"Vous pouvez demander à l'utilisateur de préciser quel document router si besoin."
                    )
                    
                    logger.warning(f"[LPT_Router] ⚠️ drive_file_id={drive_file_id} non trouvé dans {len(router_jobs)} jobs to_process")
                    
                    # Retourner immédiatement l'erreur à l'agent
                    return {
                        "status": "error",
                        "error": error_msg,
                        "available_documents_count": len(router_jobs)
                    }
            else:
                # Si jobs_data non disponible, retourner une erreur explicite
                error_msg = (
                    "❌ Impossible d'accéder à la liste des documents à router.\n"
                    "Les données des jobs Router ne sont pas disponibles dans le contexte actuel.\n"
                    "Veuillez rafraîchir les données ou contacter le support."
                )
                logger.warning(f"[LPT_Router] ⚠️ jobs_data['ROUTER'] non disponible")
                return {
                    "status": "error",
                    "error": error_msg
                }
            
            # Récupérer le vrai nom du thread depuis RTDB
            from ...firebase_providers import FirebaseRealtimeChat
            rtdb = FirebaseRealtimeChat()
            thread_name = rtdb.get_thread_name(
                space_code=company_id,
                thread_key=thread_key,
                mode='chats'  # Mode par défaut pour les conversations agent
            ) or thread_key  # Fallback sur thread_key si non trouvé
            
            # Informations de traçabilité pour le callback
            traceability_info = {
                "thread_key": thread_key,
                "thread_name": thread_name,  # ✅ Vrai nom du thread depuis RTDB
                "execution_id": execution_id,  # ⭐ NOUVEAU: ID d'exécution pour traçabilité complète
                "execution_plan": execution_plan,  # ⭐ NOUVEAU: mode d'exécution (NOW, ON_DEMAND, etc.)
                "initiated_at": datetime.now(timezone.utc).isoformat(),  # ⭐ NOUVEAU: timestamp d'initiation
                "source": "pinnokio_brain"  # ⭐ NOUVEAU: source de l'appel
            }

            payload = {
                # Informations de base
                "collection_name": company_id,
                "user_id": user_id,
                "client_uuid": context['client_uuid'],
                "mandates_path": context['mandate_path'],

                # Données de la tâche
                "jobs_data": [{
                    "file_name": resolved_file_name,
                    "drive_file_id": drive_file_id,
                    "instructions": instructions or "",
                    "status": 'to_route',
                    "approval_required": approval_required,
                    "automated_workflow": automated_workflow
                }],

                # Configuration
                "settings": [
                    {"communication_mode": context['communication_mode']},
                    {"log_communication_mode": context['log_communication_mode']},
                    {"dms_system": context['dms_system']}
                ],

                # Informations de traçabilité pour callback
                "traceability": traceability_info,  # ⭐ NOUVEAU: Section traçabilité complète
                "pub_sub_id": pub_sub_id,  # ID unique pour cette exécution

                # Instructions
                "start_instructions": None
            }
            
            logger.info(f"[LPT_Router] Lancement - file_id={drive_file_id}, thread={thread_key}, execution_id={execution_id}, plan={execution_plan}")
            
            # Envoyer la requête HTTP
            url = self.router_url
            
            # ⭐ LOG: Afficher l'URL et le payload avant l'envoi
            logger.info(f"[LPT_Router] 📤 Envoi HTTP POST vers: {url}")
            logger.info(f"[LPT_Router] 📦 Payload complet: {payload}")
            
            try:
                async with aiohttp.ClientSession() as session:
                    logger.info(f"[LPT_Router] 🔄 Connexion en cours vers {url}...")
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        status = response.status
                        logger.info(f"[LPT_Router] ✅ Réponse HTTP reçue: status={status}")
                        
                        if status == 202:
                            logger.info(f"[LPT_Router] ✓ Lancé avec succès - file_id={drive_file_id}")
                            
                            # Sauvegarder la tâche
                            task_id = pub_sub_id
                            await self._save_task_to_firebase(
                                user_id=user_id,
                                thread_key=thread_key,
                                task_id=task_id,
                                task_type="Router",
                                payload=payload,
                                status="queued"
                            )
                            
                            # Créer la notification
                            await self._create_router_notification(
                                user_id=user_id,
                                company_id=company_id,
                                company_name=context['company_name'],
                                drive_file_id=drive_file_id,
                                file_name=resolved_file_name,  # ⭐ NOUVEAU: Passer le vrai nom du fichier
                                pub_sub_id=pub_sub_id,
                                instructions=instructions
                            )
                            
                            return {
                                "status": "queued",
                                "task_id": task_id,
                                "file_id": drive_file_id,
                                "thread_key": thread_key,
                                "message": f"✓ Router lancé pour le document {drive_file_id}"
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"[LPT_Router] ❌ Erreur HTTP {status}: {error_text}")
                            return {
                                "status": "error",
                                "error": f"HTTP {status}: {error_text}"
                            }
            
            except aiohttp.ClientError as ce:
                # Erreur de connexion ou réseau
                logger.error(f"[LPT_Router] ❌ Erreur de connexion HTTP: {ce}", exc_info=True)
                return {
                    "status": "error",
                    "error": f"Erreur de connexion: {str(ce)}",
                    "error_type": "connection_error"
                }
            
            except asyncio.TimeoutError:
                # Timeout
                logger.error(f"[LPT_Router] ⏱️ Timeout après 30s vers {url}")
                return {
                    "status": "error",
                    "error": "Timeout de connexion (30s)",
                    "error_type": "timeout"
                }
        
        except ValueError as ve:
            # Erreur de configuration (données manquantes)
            logger.error(f"[LPT_Router] Erreur de configuration: {ve}")
            return {
                "status": "configuration_error",
                "error": str(ve),
                "error_type": "missing_user_data",
                "message": "⚠️ Configuration utilisateur incomplète. Vérifiez que l'utilisateur est correctement enregistré."
            }
        
        except Exception as e:
            # Erreur technique
            logger.error(f"[LPT_Router] Erreur technique: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "error_type": "technical_error"
            }
    
    async def launch_onboarding_job(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        session=None,
        brain=None,
        job_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        execution_plan: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lance une tâche Onboarding pour le processus d'onboarding client.
        Format standard LPT (identique à Router/APBookkeeper/Banker).
        """
        try:
            # ⭐ Récupérer le contexte depuis le brain (OBLIGATOIRE)
            if not brain:
                raise ValueError("Brain est requis pour lancer le job d'onboarding")
            
            context = brain.get_user_context()
            logger.info(f"[LPT_Onboarding] Contexte récupéré depuis brain: mandate_path={context.get('mandate_path')}")
            
            # Charger les données onboarding
            onboarding_data = brain.onboarding_data or await brain.load_onboarding_data()
            onboarding_data = onboarding_data or {}

            if not onboarding_data:
                raise ValueError("Données d'onboarding introuvables pour ce client")

            # Construire les identifiants (format standard LPT)
            job_id = job_id or onboarding_data.get("job_id") or f"onboarding_{uuid.uuid4().hex[:8]}"
            onboarding_data.setdefault("job_id", job_id)
            
            # ⭐ Utiliser batch_id au niveau racine (format standard LPT)
            batch_id = job_id  # Pour onboarding, batch_id = job_id (un seul job par batch)
            pub_sub_id = f"onboarding_{job_id}_{uuid.uuid4().hex[:6]}"

            # ⭐ Récupérer les informations de traçabilité depuis le brain
            execution_id = execution_id or (brain.active_task_data.get("execution_id") if brain and brain.active_task_data else None) or f"exec_{uuid.uuid4().hex}"
            execution_plan = execution_plan or (brain.active_task_data.get("execution_plan") if brain and brain.active_task_data else None) or "NOW"

            # Récupérer le vrai nom du thread depuis RTDB
            from ...firebase_providers import FirebaseRealtimeChat
            rtdb = FirebaseRealtimeChat()
            thread_name = rtdb.get_thread_name(
                space_code=company_id,
                thread_key=thread_key,
                mode='chats'
            ) or thread_key

            # Informations de traçabilité pour le callback (format standard LPT)
            traceability_info = {
                "thread_key": thread_key,
                "thread_name": thread_name,
                "execution_id": execution_id,
                "execution_plan": execution_plan,
                "initiated_at": datetime.now(timezone.utc).isoformat(),
                "source": "pinnokio_onboarding"  # ⭐ Source de l'appel
            }

            # ⭐ Récupérer les données nécessaires pour le payload
            context_data = onboarding_data.get("initial_context_data", "")
            setup_coa_type = onboarding_data.get("analysis_method")
            accounting_systems = onboarding_data.get("accounting_systems", {})
            erp_system = accounting_systems.get("accounting_system") if isinstance(accounting_systems, dict) else None
            mandate_path = context.get("mandate_path")

            # ⭐ Payload au format attendu par l'endpoint onboarding_manager_agent
            payload = {
                "firebase_user_id": user_id,
                "job_id": job_id,
                "mandate_path": mandate_path,
                "mode": "onboarding",
                "setup_coa_type": setup_coa_type,
                "erp_system": erp_system,
                "context": context_data
            }
            
            logger.info(f"[LPT_Onboarding] Lancement - job_id={job_id}, thread={thread_key}, execution_id={execution_id}, plan={execution_plan}")
            
            # ⭐ Placer le verrou persistant avant d'appeler le service (best-effort)
            try:
                from ...firebase_providers import FirebaseManagement
                fbm = FirebaseManagement()
                onboarding_path = f"clients/{user_id}/temp_data/onboarding"
                # Utiliser asyncio.to_thread car set_document est synchrone
                await asyncio.to_thread(fbm.set_document, onboarding_path, {
                    "job_active": True,
                    "job_id": job_id,
                    "lock_timestamp": datetime.now(timezone.utc).isoformat()
                }, True)  # merge=True
                logger.info(f"[LPT_Onboarding] 🔒 Verrou d'onboarding placé pour job_id={job_id}")
            except Exception as e:
                logger.warning(f"[LPT_Onboarding] ⚠️ Impossible d'écrire le verrou d'onboarding: {e}")
            
            # ═══════════════════════════════════════════════════════════
            # ⚡ OPTIMISATION : MODE FIRE-AND-FORGET (comme general_chat)
            # ═══════════════════════════════════════════════════════════
            # Au lieu d'attendre la réponse HTTP (bloquant 5-20s), on :
            # 1. Lance le POST HTTP en arrière-plan (non-bloquant)
            # 2. Retourne immédiatement "queued"
            # 3. L'agent notifie via /lpt/callback quand terminé
            # ✅ Identique au flux de send_message (homogénéité totale)
            # ═══════════════════════════════════════════════════════════
            
            url = self.onboarding_url
            
            # ⭐ LOG: Afficher l'URL et le payload avant l'envoi
            logger.info(f"[LPT_Onboarding] 📤 Envoi HTTP POST vers: {url}")
            logger.info(f"[LPT_Onboarding] 📦 Payload complet: {payload}")
            
            # Sauvegarder la tâche AVANT d'envoyer (pour avoir le contexte si callback arrive vite)
            task_id = pub_sub_id
            await self._save_task_to_firebase(
                user_id=user_id,
                thread_key=thread_key,
                task_id=task_id,
                task_type="Onboarding",
                payload=payload,
                status="queued"
            )
            
            # ⚡ Lancer le POST HTTP en arrière-plan (fire-and-forget)
            async def _fire_and_forget_post():
                """Envoie le POST HTTP sans bloquer, notifie via logs."""
                try:
                    # ⚡ OPTIMISATION: Créer la notification en arrière-plan (ne bloque pas le retour)
                    await self._create_onboarding_notification(
                        user_id=user_id,
                        company_id=company_id,
                        company_name=context.get("company_name"),
                        thread_key=thread_key,
                        job_id=job_id,
                        execution_id=execution_id
                    )
                    
                    async with aiohttp.ClientSession() as session_http:
                        logger.info(f"[LPT_Onboarding] 🔄 [BG] Connexion en cours vers {url}...")
                        async with session_http.post(
                            url, 
                            json=payload, 
                            timeout=aiohttp.ClientTimeout(total=60)  # Timeout généreux pour agent externe
                        ) as response:
                            status = response.status
                            logger.info(f"[LPT_Onboarding] ✅ [BG] Réponse HTTP reçue: status={status}")
                            
                            if status in (200, 202):
                                logger.info(f"[LPT_Onboarding] ✓ [BG] Job envoyé avec succès - job_id={job_id}")
                            else:
                                error_text = await response.text()
                                logger.error(f"[LPT_Onboarding] ❌ [BG] Erreur HTTP {status}: {error_text}")
                                
                                # Mettre à jour le statut de la tâche en erreur
                                await self._update_task_status(
                                    user_id=user_id,
                                    task_id=task_id,
                                    status="error",
                                    error=f"HTTP {status}: {error_text}"
                                )
                
                except aiohttp.ClientError as ce:
                    logger.error(f"[LPT_Onboarding] ❌ [BG] Erreur de connexion HTTP: {ce}", exc_info=True)
                    await self._update_task_status(
                        user_id=user_id,
                        task_id=task_id,
                        status="error",
                        error=f"Erreur de connexion: {str(ce)}"
                    )
                
                except asyncio.TimeoutError:
                    logger.error(f"[LPT_Onboarding] ⏱️ [BG] Timeout après 60s vers {url}")
                    await self._update_task_status(
                        user_id=user_id,
                        task_id=task_id,
                        status="error",
                        error="Timeout de connexion (60s)"
                    )
                
                except Exception as e:
                    logger.error(f"[LPT_Onboarding] ❌ [BG] Erreur inattendue: {e}", exc_info=True)
                    await self._update_task_status(
                        user_id=user_id,
                        task_id=task_id,
                        status="error",
                        error=str(e)
                    )
            
            # Lancer en arrière-plan (non-bloquant)
            asyncio.create_task(_fire_and_forget_post())
            
            logger.info(
                f"[LPT_Onboarding] ⚡ Retour immédiat (fire-and-forget) - "
                f"job_id={job_id}, task_id={task_id}"
            )
            
            # ✅ RETOUR IMMÉDIAT (comme send_message)
            return {
                "status": "queued",
                "task_id": task_id,
                "job_id": job_id,
                "batch_id": batch_id,
                "execution_id": execution_id,
                "thread_key": thread_key,
                "message": "✓ Onboarding lancé (mode asynchrone)"
            }

        except Exception as e:
            logger.error(f"[LPT_Onboarding] ❌ Erreur lancement onboarding: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }

    async def launch_banker_all(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        bank_account: Optional[str] = None,
        start_instructions: Optional[str] = None,
        session=None,
        brain=None,
        execution_id: Optional[str] = None,
        execution_plan: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lance automatiquement la réconciliation de toutes les transactions bancaires disponibles.
        Optionnellement filtrée par compte bancaire.
        """
        try:
            if not brain:
                raise ValueError("Brain est requis pour lancer Banker_ALL")
            
            context = brain.get_user_context()
            mandate_path = context.get('mandate_path')
            
            # 🔍 Compter le nombre de transactions à traiter
            bank_data = (brain.jobs_data or {}).get("BANK", {}) if brain else {}
            unprocessed_transactions = bank_data.get("unprocessed", []) or []
            nb_transactions = len(unprocessed_transactions)
            
            # 🛡️ VÉRIFICATION DU SOLDE AVANT L'ENVOI
            # Coût estimé : 0.3$ par transaction
            estimated_cost = nb_transactions * 0.3
            
            balance_check = self.check_balance_before_lpt(
                mandate_path=mandate_path,
                user_id=user_id,
                estimated_cost=estimated_cost,
                lpt_tool_name="Banker_ALL"
            )
            
            if not balance_check.get("sufficient", False):
                # ❌ SOLDE INSUFFISANT - Retourner le message à l'agent
                logger.warning(
                    f"[LPT_Banker_ALL] ❌ BLOCAGE - Solde insuffisant "
                    f"({balance_check.get('current_balance', 0):.2f}$ < {balance_check.get('required_balance', 0):.2f}$)"
                )
                return {
                    "status": "insufficient_balance",
                    "error": "Solde insuffisant pour exécuter cette opération",
                    "balance_info": {
                        "current_balance": balance_check.get("current_balance", 0.0),
                        "required_balance": balance_check.get("required_balance", 0.0),
                        "missing_amount": balance_check.get("missing_amount", 0.0)
                    },
                    "nb_transactions_to_process": nb_transactions,
                    "message": balance_check.get("message", "Solde insuffisant")
                }
            
            # ✅ SOLDE SUFFISANT - Continuer l'exécution
            logger.info(
                f"[LPT_Banker_ALL] ✅ Solde vérifié et suffisant "
                f"({balance_check.get('current_balance', 0):.2f}$ >= {balance_check.get('required_balance', 0):.2f}$) "
                f"pour {nb_transactions} transactions"
            )
            
            workflow_params = context.get('workflow_params', {})
            banker_params = workflow_params.get('Banker_param', {})
            approval_required = banker_params.get('banker_approval_required', False)
            approval_threshold = banker_params.get('banker_approval_thresholdworkflow', '95')
            
            bank_jobs = (brain.jobs_data or {}).get("BANK", {}) if brain else {}
            aggregated_transactions: List[Dict[str, Any]] = []
            for key in ["to_reconcile", "pending", "in_process"]:
                tx_list = bank_jobs.get(key)
                if isinstance(tx_list, list):
                    aggregated_transactions.extend(tx_list)
            
            if not aggregated_transactions:
                logger.warning("[LPT_Banker_ALL] Aucune transaction disponible pour lancement.")
                return {
                    "status": "no_transactions_available",
                    "message": "Aucune transaction bancaire disponible (to_reconcile/pending/in_process)."
                }
            
            if bank_account:
                target = bank_account.lower()
                filtered_transactions = [
                    tx for tx in aggregated_transactions
                    if str(tx.get('journal_id', '')).lower() == target
                    or str(tx.get('journal_name', '')).lower() == target
                ]
                if not filtered_transactions:
                    logger.warning(f"[LPT_Banker_ALL] Aucun mouvement pour le compte {bank_account}.")
                    return {
                        "status": "no_transactions_for_account",
                        "message": f"Aucune transaction trouvée pour le compte bancaire '{bank_account}'."
                    }
            else:
                filtered_transactions = aggregated_transactions
            
            grouped_transactions: Dict[str, Dict[str, Any]] = {}
            for tx in filtered_transactions:
                account_id = str(tx.get('journal_id', '') or '')
                account_name = str(tx.get('journal_name') or account_id or 'Compte bancaire')
                group_key = account_id or account_name
                if group_key not in grouped_transactions:
                    grouped_transactions[group_key] = {
                        "account_id": account_id,
                        "account_name": account_name,
                        "transactions": []
                    }
                grouped_transactions[group_key]["transactions"].append(tx)
            
            if not grouped_transactions:
                logger.warning("[LPT_Banker_ALL] Aucune transaction regroupée après filtrage.")
                return {
                    "status": "no_transactions_available",
                    "message": "Aucune transaction à réconcilier après filtrage."
                }
            
            execution_id = execution_id or (brain.active_task_data.get("execution_id") if brain and brain.active_task_data else None)
            execution_plan = execution_plan or (brain.active_task_data.get("execution_plan") if brain and brain.active_task_data else None)
            
            from ...firebase_providers import FirebaseRealtimeChat
            rtdb = FirebaseRealtimeChat()
            thread_name = rtdb.get_thread_name(
                space_code=company_id,
                thread_key=thread_key,
                mode='chats'
            ) or thread_key
            
            batch_id = f'bank_batch_{uuid.uuid4().hex[:10]}'
            traceability_info = {
                "thread_key": thread_key,
                "thread_name": thread_name,
                "execution_id": execution_id,
                "execution_plan": execution_plan,
                "initiated_at": datetime.now(timezone.utc).isoformat(),
                "source": "pinnokio_brain"
            }
            
            jobs_data_payload: List[Dict[str, Any]] = []
            notifications_payload: List[Dict[str, Any]] = []
            
            for idx, (_group_key, group_data) in enumerate(grouped_transactions.items(), start=1):
                account_name = group_data["account_name"]
                account_id = group_data["account_id"]
                tx_payload = []
                
                for tx in group_data["transactions"]:
                    transaction_id = tx.get('transaction_id')
                    if transaction_id is None:
                        continue

                    # Formater transaction_name avec (#ref) si ref existe
                    display_name = tx.get('display_name', f"Transaction {transaction_id}")
                    ref = str(tx.get('ref', '')).strip()
                    if ref and not ref.startswith('#'):
                        ref = f"#{ref}"
                    transaction_name = f"{display_name} ({ref})" if ref else display_name
                    
                    # Normaliser partner_name et transaction_type ('' au lieu de 'None'/'False')
                    partner_name = str(tx.get('partner_name', '') or '').strip()
                    if partner_name.lower() in ('none', 'null', 'false'):
                        partner_name = ''
                    
                    transaction_type = str(tx.get('transaction_type', '') or '').strip()
                    if transaction_type.lower() in ('none', 'null', 'false'):
                        transaction_type = ''
                    
                    tx_payload.append({
                        "transaction_id": str(transaction_id),
                        "transaction_name": transaction_name,
                        "date": str(tx.get('date', '')),
                        "ref": ref,
                        "amount": float(tx.get('amount', 0)),
                        "currency_name": str(tx.get('currency_id', 'EUR')),
                        "partner_name": partner_name,
                        "transaction_type": transaction_type,
                        "payment_ref": str(tx.get('payment_ref', '')),
                        "journal_name": str(tx.get('journal_name') or account_name),
                        "journal_id": str(tx.get('journal_id') or account_id),
                        "instructions": tx.get('instructions', ''),
                        "pending": str(tx.get('status', '')).lower() == 'pending',
                        "status": 'in_queue'  # Toujours 'in_queue' pour les nouvelles transactions
                    })
                
                if not tx_payload:
                    continue
                
                # Note: job_id utilisé uniquement pour les notifications internes
                job_id = f"{batch_id}_{idx}"
                
                jobs_data_payload.append({
                    "bank_account": account_name,
                    "bank_account_id": account_id,
                    "transactions": tx_payload,
                    "instructions": "",  # Instructions spécifiques au job (vide par défaut)
                    "banker_approval_required": approval_required,
                    "banker_approval_thresholdworkflow": str(approval_threshold)
                })
                notifications_payload.append({
                    "job_id": job_id,
                    "bank_account": account_name,
                    "transactions": tx_payload
                })
            
            if not jobs_data_payload:
                logger.warning("[LPT_Banker_ALL] Aucun payload valide après transformation.")
                return {
                    "status": "no_transactions_available",
                    "message": "Les transactions disponibles ne contiennent pas d'identifiants exploitables."
                }
            
            # Récupérer le journal_name du premier job pour le niveau racine
            first_journal_name = jobs_data_payload[0].get("transactions", [{}])[0].get("journal_name", "") if jobs_data_payload and jobs_data_payload[0].get("transactions") else ""
            
            payload = {
                "collection_name": company_id,
                "batch_id": batch_id,
                "journal_name": first_journal_name,
                "jobs_data": jobs_data_payload,
                "start_instructions": start_instructions or "",  # Instructions générales pour tout le batch
                "settings": [
                    {'communication_mode': context['communication_mode']},
                    {'log_communication_mode': context['log_communication_mode']},
                    {'dms_system': context['dms_system']}
                ],
                "client_uuid": context['client_uuid'],
                "user_id": user_id,
                "mandates_path": context['mandate_path'],
                "proxy": False,
                "traceability": traceability_info
            }
            
            logger.info(f"[LPT_Banker_ALL] Lancement batch - comptes={len(jobs_data_payload)}, batch_id={batch_id}")
            logger.info(f"[LPT_Banker_ALL] Payload: {payload}")
            
            url = self.banker_url
            try:
                async with aiohttp.ClientSession() as session_http:
                    async with session_http.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        status = response.status
                        logger.info(f"[LPT_Banker_ALL] Réponse HTTP: {status}")
                        
                        if status == 202:
                            task_id = batch_id
                            await self._save_task_to_firebase(
                                user_id=user_id,
                                thread_key=thread_key,
                                task_id=task_id,
                                task_type="Banker",
                                payload=payload,
                                status="queued"
                            )
                            
                            for notif in notifications_payload:
                                await self._create_banker_notification(
                                    user_id=user_id,
                                    company_id=company_id,
                                    company_name=context['company_name'],
                                    batch_id=batch_id,
                                    job_id=notif["job_id"],
                                    bank_account=notif["bank_account"],
                                    transactions=notif["transactions"]
                                )
                            
                            return {
                                "status": "queued",
                                "task_id": task_id,
                                "batch_id": batch_id,
                                "nb_accounts": len(jobs_data_payload),
                                "nb_transactions": sum(len(n["transactions"]) for n in notifications_payload),
                                "thread_key": thread_key,
                                "message": f"✓ Banker lancé pour {len(jobs_data_payload)} compte(s) bancaire(s)."
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"[LPT_Banker_ALL] Erreur HTTP {status}: {error_text}")
                            return {
                                "status": "error",
                                "error": f"HTTP {status}: {error_text}"
                            }
            except aiohttp.ClientError as ce:
                logger.error(f"[LPT_Banker_ALL] Erreur connexion HTTP: {ce}", exc_info=True)
                return {
                    "status": "error",
                    "error": f"Erreur de connexion: {str(ce)}",
                    "error_type": "connection_error"
                }
            except asyncio.TimeoutError:
                logger.error("[LPT_Banker_ALL] Timeout HTTP (30s)")
                return {
                    "status": "error",
                    "error": "Timeout de connexion (30s)",
                    "error_type": "timeout"
                }
        
        except ValueError as ve:
            logger.error(f"[LPT_Banker_ALL] Erreur de configuration: {ve}")
            return {
                "status": "configuration_error",
                "error": str(ve),
                "error_type": "missing_user_data"
            }
        except Exception as e:
            logger.error(f"[LPT_Banker_ALL] Erreur technique: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "error_type": "technical_error"
            }
    
    async def launch_banker(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        bank_account: str,
        transaction_ids: List[str],
        instructions: Optional[str] = None,
        start_instructions: Optional[str] = None,
        transaction_instructions: Optional[Dict[str, str]] = None,
        session=None,  # ⭐ LLMSession pour cache (non utilisé, contexte vient du brain)
        brain=None,    # ⭐ PinnokioBrain pour accès au contexte utilisateur
        execution_id: Optional[str] = None,  # ⭐ ID d'exécution pour traçabilité
        execution_plan: Optional[str] = None  # ⭐ Mode d'exécution (NOW, ON_DEMAND, etc.)
    ) -> Dict[str, Any]:
        """
        Lance une tâche Banker pour la réconciliation bancaire.
        Le paramètre approval_required est récupéré automatiquement depuis workflow_params.
        """
        try:
            # ⭐ Récupérer le contexte depuis le brain (OBLIGATOIRE)
            if not brain:
                raise ValueError("Brain est requis pour lancer Banker")
            
            context = brain.get_user_context()
            mandate_path = context.get('mandate_path')
            
            # 🛡️ VÉRIFICATION DU SOLDE AVANT L'ENVOI
            # Coût estimé : 0.3$ par transaction (ajustable selon vos tarifs)
            estimated_cost = len(transaction_ids) * 0.3
            
            balance_check = self.check_balance_before_lpt(
                mandate_path=mandate_path,
                user_id=user_id,
                estimated_cost=estimated_cost,
                lpt_tool_name="Banker"
            )
            
            if not balance_check.get("sufficient", False):
                # ❌ SOLDE INSUFFISANT - Retourner le message à l'agent
                logger.warning(
                    f"[LPT_Banker] ❌ BLOCAGE - Solde insuffisant "
                    f"({balance_check.get('current_balance', 0):.2f}$ < {balance_check.get('required_balance', 0):.2f}$)"
                )
                return {
                    "status": "insufficient_balance",
                    "error": "Solde insuffisant pour exécuter cette opération",
                    "balance_info": {
                        "current_balance": balance_check.get("current_balance", 0.0),
                        "required_balance": balance_check.get("required_balance", 0.0),
                        "missing_amount": balance_check.get("missing_amount", 0.0)
                    },
                    "message": balance_check.get("message", "Solde insuffisant")
                }
            
            # ✅ SOLDE SUFFISANT - Continuer l'exécution
            logger.info(
                f"[LPT_Banker] ✅ Solde vérifié et suffisant "
                f"({balance_check.get('current_balance', 0):.2f}$ >= {balance_check.get('required_balance', 0):.2f}$)"
            )
            logger.info(f"[LPT_Banker] Contexte récupéré depuis brain: mandate_path={mandate_path}")
            
            # ⭐ NOUVEAU: Récupérer les paramètres depuis workflow_params
            workflow_params = context.get('workflow_params', {})
            banker_params = workflow_params.get('Banker_param', {})
            
            approval_required = banker_params.get('banker_approval_required', False)
            approval_threshold = banker_params.get('banker_approval_thresholdworkflow', '95')
            
            logger.info(
                f"[LPT_Banker] Paramètres workflow: "
                f"approval_required={approval_required}"
            )
            
            # ⭐ RÉSOLUTION DES TRANSACTIONS depuis le cache BANK
            # Chercher les transaction_ids dans brain.jobs_data['BANK']
            bank_jobs = (brain.jobs_data or {}).get("BANK", {}) if brain else {}
            
            # Agréger toutes les listes possibles
            all_transactions = []
            for key in ["to_reconcile", "pending", "in_process"]:
                txs_list = bank_jobs.get(key)
                if isinstance(txs_list, list):
                    all_transactions.extend(txs_list)
            
            logger.info(f"[LPT_Banker] 🔍 Cache chargé: {len(all_transactions)} transactions disponibles")
            
            # Valider et résoudre chaque transaction_id
            valid_transactions = []
            invalid_ids = []
            
            for tx_id in transaction_ids:
                found = False

                # Rechercher le transaction_id dans le cache
                for tx in all_transactions:
                    if str(tx.get('transaction_id')) == str(tx_id):
                        # Formater transaction_name avec (#ref) si ref existe
                        display_name = tx.get('display_name', f"Transaction {tx_id}")
                        ref = str(tx.get('ref', '')).strip()
                        if ref and not ref.startswith('#'):
                            ref = f"#{ref}"
                        transaction_name = f"{display_name} ({ref})" if ref else display_name

                        # Normaliser partner_name et transaction_type ('' au lieu de 'None'/'False')
                        partner_name = str(tx.get('partner_name', '') or '').strip()
                        if partner_name.lower() in ('none', 'null', 'false'):
                            partner_name = ''

                        transaction_type = str(tx.get('transaction_type', '') or '').strip()
                        if transaction_type.lower() in ('none', 'null', 'false'):
                            transaction_type = ''

                        # Récupérer journal_name et journal_id correctement
                        journal_name = str(tx.get('journal_name') or tx.get('journal_id') or bank_account or 'Bank')
                        journal_id = str(tx.get('journal_id') or '')

                        # Récupérer les instructions spécifiques pour cette transaction si fournies
                        tx_instructions = ""
                        if transaction_instructions and str(tx_id) in transaction_instructions:
                            tx_instructions = transaction_instructions[str(tx_id)]

                        # Construire transaction complète selon le format attendu
                        transaction_data = {
                            "transaction_id": str(tx.get('transaction_id')),
                            "transaction_name": transaction_name,
                            "date": str(tx.get('date', '')),
                            "ref": ref,
                            "amount": float(tx.get('amount', 0)),
                            "currency_name": str(tx.get('currency_id', 'EUR')),
                            "partner_name": partner_name,
                            "transaction_type": transaction_type,
                            "payment_ref": str(tx.get('payment_ref', '')),
                            "journal_name": journal_name,
                            "journal_id": journal_id,
                            "instructions": tx_instructions,
                            "pending": str(tx.get('status', '')).lower() == 'pending',
                            "status": 'in_queue'  # Toujours 'in_queue' pour les nouvelles transactions
                        }
                        valid_transactions.append(transaction_data)
                        found = True
                        logger.info(
                            f"[LPT_Banker] ✅ transaction_id={tx_id} résolu → "
                            f"{tx.get('partner_name')} - {tx.get('amount')}€"
                        )
                        break
                
                if not found:
                    invalid_ids.append(tx_id)
                    logger.warning(f"[LPT_Banker] ⚠️ transaction_id={tx_id} non trouvé dans le cache")
            
            # ⭐ Si AUCUNE transaction valide : retourner erreur avec liste des IDs disponibles
            if len(valid_transactions) == 0:
                error_msg = (
                    f"❌ Aucune transaction valide trouvée parmi les transaction_ids fournis.\n\n"
                    f"📋 transaction_ids invalides ({len(invalid_ids)}) : {invalid_ids}\n\n"
                    f"⚠️ Chaque valeur doit correspondre exactement à un `transaction_id`. "
                    f"Si le `transaction_id` précis n'est pas fourni, la transaction ne sera pas exécutée.\n\n"
                    f"💳 Transactions disponibles ({len(all_transactions)} transactions) :\n"
                )
                # Lister les 10 premières transactions disponibles
                for idx, tx in enumerate(all_transactions[:10], 1):
                    tx_name = tx.get('display_name', 'Sans nom')
                    tx_id = tx.get('transaction_id', 'Sans ID')
                    tx_amount = tx.get('amount', 0)
                    tx_partner = tx.get('partner_name', 'N/A')
                    error_msg += f"  {idx}. {tx_name} - {tx_partner} - {tx_amount}€ (transaction_id: {tx_id})\n"

                if len(all_transactions) > 10:
                    error_msg += f"  ... et {len(all_transactions) - 10} autres transactions.\n"

                error_msg += (
                    f"\n💡 Pour réconcilier des transactions, utilisez l'un des 'transaction_id' listés ci-dessus.\n"
                    f"Vous pouvez demander à l'utilisateur de préciser quelles transactions traiter si besoin."
                )
                
                logger.warning(f"[LPT_Banker] ❌ Aucune transaction valide - Retour erreur à l'agent")
                
                return {
                    "status": "error",
                    "error": error_msg,
                    "invalid_ids": invalid_ids,
                    "available_transactions_count": len(all_transactions)
                }
            
            # ⭐ Si certaines transactions invalides : continuer avec warning
            if len(invalid_ids) > 0:
                logger.warning(
                    f"[LPT_Banker] ⚠️ {len(invalid_ids)} transaction_id(s) invalide(s) ignoré(s): {invalid_ids}. "
                    f"Continuation avec {len(valid_transactions)} transaction(s) valide(s)."
                )
            
            # Construire le payload
            batch_id = f'bank_batch_{uuid.uuid4().hex[:10]}'
            
            # ⭐ NOUVEAU: Récupérer les informations de traçabilité depuis le brain
            execution_id = execution_id or (brain.active_task_data.get("execution_id") if brain and brain.active_task_data else None)
            execution_plan = execution_plan or (brain.active_task_data.get("execution_plan") if brain and brain.active_task_data else None)

            # ⭐ Récupérer le vrai nom du thread depuis RTDB (ALIGNÉ avec Router)
            from ...firebase_providers import FirebaseRealtimeChat
            rtdb = FirebaseRealtimeChat()
            thread_name = rtdb.get_thread_name(
                space_code=company_id,
                thread_key=thread_key,
                mode='chats'  # Mode par défaut pour les conversations agent
            ) or thread_key  # Fallback sur thread_key si non trouvé

            # ⭐ Informations de traçabilité pour le callback (ALIGNÉ avec Router)
            traceability_info = {
                "thread_key": thread_key,
                "thread_name": thread_name,  # ✅ Vrai nom du thread depuis RTDB
                "execution_id": execution_id,  # ⭐ ID d'exécution pour traçabilité complète
                "execution_plan": execution_plan,  # ⭐ mode d'exécution (NOW, ON_DEMAND, etc.)
                "initiated_at": datetime.now(timezone.utc).isoformat(),  # ⭐ timestamp d'initiation
                "source": "pinnokio_brain"  # ⭐ source de l'appel
            }

            # Récupérer le journal_name et bank_account_id depuis la première transaction
            first_journal_name = valid_transactions[0].get("journal_name", bank_account) if valid_transactions else bank_account
            first_bank_account_id = valid_transactions[0].get("journal_id", "") if valid_transactions else ""

            payload = {
                "collection_name": company_id,
                "batch_id": batch_id,
                "journal_name": first_journal_name,
                "jobs_data": [{
                    "bank_account": bank_account,
                    "bank_account_id": first_bank_account_id,
                    "transactions": valid_transactions,  # ✅ Utiliser valid_transactions résolues depuis le cache
                    "instructions": instructions or "",  # Instructions spécifiques au job
                    "banker_approval_required": approval_required,
                    "banker_approval_thresholdworkflow": str(approval_threshold)
                }],
                "start_instructions": start_instructions or "",  # Instructions générales pour tout le batch
                "settings": [
                    {'communication_mode': context['communication_mode']},
                    {'log_communication_mode': context['log_communication_mode']},
                    {'dms_system': context['dms_system']}
                ],
                "client_uuid": context['client_uuid'],
                "user_id": user_id,
                "mandates_path": context['mandate_path'],
                "proxy": False,
                "traceability": traceability_info
            }
            
            logger.info(
                f"[LPT_Banker] Lancement - batch_id={batch_id}, "
                f"nb_tx_valides={len(valid_transactions)}, nb_tx_invalides={len(invalid_ids)}, "
                f"thread={thread_key}, execution_id={execution_id}, plan={execution_plan}"
            )
            
            # Envoyer la requête HTTP
            url = self.banker_url
            
            # ⭐ LOG: Afficher l'URL et le payload avant l'envoi
            logger.info(f"[LPT_Banker] 📤 Envoi HTTP POST vers: {url}")
            logger.info(f"[LPT_Banker] 📦 Payload complet: {payload}")
            
            try:
                async with aiohttp.ClientSession() as session:
                    logger.info(f"[LPT_Banker] 🔄 Connexion en cours vers {url}...")
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        status = response.status
                        logger.info(f"[LPT_Banker] ✅ Réponse HTTP reçue: status={status}")
                        
                        if status == 202:
                            logger.info(f"[LPT_Banker] ✓ Lancé avec succès - batch_id={batch_id}")
                            
                            # Sauvegarder la tâche
                            task_id = batch_id
                            await self._save_task_to_firebase(
                                user_id=user_id,
                                thread_key=thread_key,
                                task_id=task_id,
                                task_type="Banker",
                                payload=payload,
                                status="queued"
                            )
                            
                            # Créer la notification
                            await self._create_banker_notification(
                                user_id=user_id,
                                company_id=company_id,
                                company_name=context['company_name'],
                                batch_id=batch_id,
                                job_id=batch_id,
                                bank_account=bank_account,
                                transactions=valid_transactions
                            )
                            
                            # Message de retour avec warning si certaines transactions invalides
                            success_message = f"✓ Banker lancé : {len(valid_transactions)} transaction(s) en cours de traitement"
                            if len(invalid_ids) > 0:
                                success_message += f"\n⚠️ {len(invalid_ids)} transaction(s) invalide(s) ignorée(s): {invalid_ids}"
                            
                            return {
                                "status": "queued",
                                "task_id": task_id,
                                "batch_id": batch_id,
                                "nb_transactions_valid": len(valid_transactions),
                                "nb_transactions_invalid": len(invalid_ids),
                                "invalid_ids": invalid_ids if len(invalid_ids) > 0 else [],
                                "thread_key": thread_key,
                                "message": success_message
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"[LPT_Banker] ❌ Erreur HTTP {status}: {error_text}")
                            return {
                                "status": "error",
                                "error": f"HTTP {status}: {error_text}"
                            }
            
            except aiohttp.ClientError as ce:
                # Erreur de connexion ou réseau
                logger.error(f"[LPT_Banker] ❌ Erreur de connexion HTTP: {ce}", exc_info=True)
                return {
                    "status": "error",
                    "error": f"Erreur de connexion: {str(ce)}",
                    "error_type": "connection_error"
                }
            
            except asyncio.TimeoutError:
                # Timeout
                logger.error(f"[LPT_Banker] ⏱️ Timeout après 30s vers {url}")
                return {
                    "status": "error",
                    "error": "Timeout de connexion (30s)",
                    "error_type": "timeout"
                }
        
        except ValueError as ve:
            # Erreur de configuration (données manquantes)
            logger.error(f"[LPT_Banker] Erreur de configuration: {ve}")
            return {
                "status": "configuration_error",
                "error": str(ve),
                "error_type": "missing_user_data",
                "message": "⚠️ Configuration utilisateur incomplète. Vérifiez que l'utilisateur est correctement enregistré."
            }
        
        except Exception as e:
            # Erreur technique
            logger.error(f"[LPT_Banker] Erreur technique: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "error_type": "technical_error"
            }
    
    async def stop_apbookeeper(self, user_id: str, company_id: str, job_id: str) -> Dict[str, Any]:
        """Arrête une tâche APBookkeeper en cours."""
        try:
            payload = {"collection_name": company_id, "user_id": user_id, "job_id": job_id}

            # Construire l'URL de stop selon l'environnement
            if self.environment == 'LOCAL':
                url = f"{self.base_url}:8081/stop_apbookeeper"
            else:  # PROD
                url = f"{self.apbookeeper_url}/stop_apbookeeper".replace("/apbookeeper-event-trigger", "/stop_apbookeeper")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return {"success": True, "message": f"APBookkeeper {job_id} arrêté"}
                    else:
                        return {"success": False, "error": await response.text()}
        except Exception as e:
            logger.error(f"Erreur stop_apbookeeper: {e}")
            return {"success": False, "error": str(e)}
    
    async def stop_router(self, user_id: str, company_id: str, job_id: str) -> Dict[str, Any]:
        """Arrête une tâche Router en cours."""
        try:
            payload = {"collection_name": company_id, "user_id": user_id, "job_id": job_id}

            # Construire l'URL de stop selon l'environnement
            if self.environment == 'LOCAL':
                url = f"{self.base_url}:8080/stop_router"
            else:  # PROD
                url = f"{self.router_url}/stop_router".replace("/event-trigger", "/stop_router")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return {"success": True, "message": f"Router {job_id} arrêté"}
                    else:
                        return {"success": False, "error": await response.text()}
        except Exception as e:
            logger.error(f"Erreur stop_router: {e}")
            return {"success": False, "error": str(e)}
    
    async def stop_banker(self, user_id: str, company_id: str, job_id: str, batch_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Arrête une tâche Banker en cours.
        
        Args:
            user_id: ID utilisateur Firebase
            company_id: ID de la société
            job_id: ID du job à arrêter
            batch_id: ID du batch (optionnel, pour cohérence avec APBookkeeper)
        """
        try:
            payload = {
                "collection_name": company_id, 
                "user_id": user_id, 
                "job_id": job_id
            }
            
            # Ajouter batch_id si fourni
            if batch_id:
                payload["batch_id"] = batch_id

            # Construire l'URL de stop selon l'environnement
            if self.environment == 'LOCAL':
                url = f"{self.base_url}:8082/stop_banker"
            else:  # PROD
                url = f"{self.banker_url}/stop_banker".replace("/banker-event-trigger", "/stop_banker")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return {"success": True, "message": f"Banker {job_id} arrêté"}
                    else:
                        return {"success": False, "error": await response.text()}
        except Exception as e:
            logger.error(f"Erreur stop_banker: {e}")
            return {"success": False, "error": str(e)}
    
    async def _save_task_to_firebase(
        self,
        user_id: str,
        thread_key: str,
        task_id: str,
        task_type: str,
        payload: Dict[str, Any],
        status: str
    ):
        """
        Sauvegarde une tâche LPT dans Firebase pour suivi UI.
        
        Path : clients/{user_id}/workflow_pinnokio/{doc_id}
        Structure : Dictionnaire indexé par thread_key
        """
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            
            # Path de sauvegarde
            workflow_path = f"clients/{user_id}/workflow_pinnokio"
            
            # Récupérer ou créer le document pour ce thread
            # Le document sera créé avec le thread_key comme ID
            doc_ref = firebase_service.db.collection(workflow_path).document(thread_key)
            
            # Données de la tâche
            task_data = {
                "task_id": task_id,
                "task_type": task_type,
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "payload_summary": {
                    "collection_name": payload.get("collection_name"),
                    "user_id": payload.get("user_id"),
                    "thread_key": thread_key
                }
            }
            
            # Mettre à jour ou créer le document
            doc_ref.set({
                "thread_key": thread_key,
                "user_id": user_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                f"tasks.{task_id}": task_data
            }, merge=True)
            
            logger.info(f"Tâche sauvegardée dans Firebase: {workflow_path}/{thread_key}/tasks/{task_id}")
        
        except Exception as e:
            logger.error(f"Erreur sauvegarde tâche Firebase: {e}", exc_info=True)
    
    async def _update_task_status(
        self,
        user_id: str,
        task_id: str,
        status: str,
        error: str = None
    ):
        """
        Met à jour le statut d'une tâche LPT dans Firebase.
        Utilisé en mode fire-and-forget pour notifier les erreurs HTTP.
        """
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            
            # Trouver le document contenant cette tâche
            # Format : clients/{user_id}/workflow_pinnokio/{thread_key}
            workflow_path = f"clients/{user_id}/workflow_pinnokio"
            
            # Requête pour trouver le document contenant cette task_id
            query = firebase_service.db.collection(workflow_path).where(f"tasks.{task_id}.task_id", "==", task_id).limit(1)
            docs = query.get()
            
            if not docs:
                logger.warning(f"Aucun document trouvé pour task_id={task_id}")
                return
            
            doc = docs[0]
            
            # Mettre à jour le statut
            update_data = {
                f"tasks.{task_id}.status": status,
                f"tasks.{task_id}.updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            if error:
                update_data[f"tasks.{task_id}.error"] = error
            
            doc.reference.update(update_data)
            
            logger.info(f"Statut de tâche mis à jour: task_id={task_id}, status={status}")
        
        except Exception as e:
            logger.error(f"Erreur mise à jour statut tâche Firebase: {e}", exc_info=True)
    
    async def _create_apbookeeper_notifications(
        self,
        user_id: str,
        company_id: str,
        company_name: str,
        batch_id: str,
        jobs_data: List[Dict[str, Any]]
    ):
        """Crée les notifications Firebase pour APBookkeeper."""
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            notification_path = f"clients/{user_id}/notifications"
            
            # Créer une notification par fichier
            for index, job in enumerate(jobs_data):
                notification_data = {
                    'function_name': 'APbookeeper',
                    'file_id': job['job_id'],
                    'job_id': job['job_id'],
                    'file_name': job['file_name'],
                    'journal_entries': "",
                    'status': 'in queue',
                    'read': False,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'collection_id': company_id,
                    'collection_name': company_name,
                    'total_files': 1,
                    'batch_index': index + 1,
                    'batch_total': len(jobs_data),
                    'batch_id': batch_id
                }
                
                # Utiliser la méthode existante de FirebaseManagement
                firebase_service.add_or_update_job_by_file_id(notification_path, notification_data)
                
            logger.info(f"Notifications APBookkeeper créées: {len(jobs_data)} notifications")
        
        except Exception as e:
            logger.error(f"Erreur création notifications APBookkeeper: {e}", exc_info=True)
    
    async def _create_router_notification(
        self,
        user_id: str,
        company_id: str,
        company_name: str,
        drive_file_id: str,
        file_name: str,  # ⭐ NOUVEAU: Nom du fichier résolu
        pub_sub_id: str,
        instructions: Optional[str]
    ):
        """Crée la notification Firebase pour Router."""
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            notification_path = f"clients/{user_id}/notifications"
            
            notification_data = {
                'function_name': 'Router',
                'file_id': drive_file_id,
                'job_id': "",
                'file_name': file_name,  # ✅ Utiliser le vrai nom du fichier résolu
                'journal_entries': "",
                'pub_sub_id': pub_sub_id,
                'status': 'in queue',
                'read': False,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'collection_id': company_id,
                'collection_name': company_name,
                "instructions": instructions or ""
            }
            
            firebase_service.add_or_update_job_by_file_id(notification_path, notification_data)
            logger.info(f"Notification Router créée: {drive_file_id}")
        
        except Exception as e:
            logger.error(f"Erreur création notification Router: {e}", exc_info=True)
    
    async def _create_banker_notification(
        self,
        user_id: str,
        company_id: str,
        company_name: str,
        batch_id: str,
        job_id: str,
        bank_account: str,
        transactions: List[Dict[str, Any]]
    ):
        """Crée la notification Firebase pour Banker."""
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            notification_path = f"clients/{user_id}/notifications"
            
            notification_data = {
                'function_name': 'Bankbookeeper',
                'job_id': job_id,
                'batch_id': batch_id,
                'bank_account': bank_account,
                'transactions': transactions,
                'status': 'in queue',
                'read': False,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'collection_id': company_id,
                'collection_name': company_name
            }
            
            firebase_service.add_or_update_job_by_job_id(notification_path, notification_data)
            logger.info(f"Notification Banker créée: {batch_id}")
        
        except Exception as e:
            logger.error(f"Erreur création notification Banker: {e}", exc_info=True)

    async def _create_onboarding_notification(
        self,
        user_id: str,
        company_id: str,
        company_name: Optional[str],
        thread_key: str,
        job_id: str,
        execution_id: str
    ):
        """Crée la notification Firebase pour le job d'onboarding."""
        try:
            from ...firebase_providers import FirebaseManagement

            firebase_service = FirebaseManagement()
            notification_path = f"clients/{user_id}/notifications"

            notification_data = {
                'function_name': 'Onboarding',
                'job_id': job_id,
                'thread_key': thread_key,
                'execution_id': execution_id,
                'status': 'in queue',
                'read': False,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'collection_id': company_id,
                'collection_name': company_name,
                'message': 'Processus onboarding démarré'
            }

            firebase_service.add_or_update_job_by_job_id(notification_path, notification_data)
            logger.info(f"[LPT_Onboarding] Notification créée: {job_id}")

        except Exception as e:
            logger.error(f"[LPT_Onboarding] Erreur création notification Onboarding: {e}", exc_info=True)

    
    async def _reconstruct_full_company_profile(self, user_id: str, collection_name: str) -> Dict[str, Any]:
        """
        Reconstruit le profil complet de l'entreprise depuis Firebase.
        
        Cette méthode charge toutes les données de contexte nécessaires :
        - Informations client (client_uuid, client_name, etc.)
        - Informations mandat (mandate_*, contact_space_*, etc.)
        - Configuration ERP (erp_*, communication_mode, dms_system, etc.)
        - ⭐ Paramètres de workflow (workflow_params avec approbations)
        
        Args:
            user_id: ID utilisateur Firebase
            collection_name: ID de la société (contact_space_id)
        
        Returns:
            Dict contenant le profil complet avec workflow_params
        """
        try:
            logger.info(
                f"[LPT_CONTEXT] Reconstruction profil complet - "
                f"user_id={user_id}, collection_name={collection_name}"
            )
            
            from ...firebase_providers import FirebaseManagement
            import asyncio
            
            firebase_service = FirebaseManagement()
            
            # Étape 1 : Récupérer le client_uuid depuis bo_clients/{user_id}
            doc_ref = firebase_service.db.collection(
                f'clients/{user_id}/bo_clients'
            ).document(user_id)
            
            doc = await asyncio.to_thread(doc_ref.get)
            
            if not doc.exists:
                raise ValueError(
                    f"Document client non trouvé: clients/{user_id}/bo_clients/{user_id}"
                )
            
            client_data = doc.to_dict()
            client_uuid = client_data.get('client_uuid')
            
            if not client_uuid:
                raise ValueError(
                    f"client_uuid manquant dans le document: clients/{user_id}/bo_clients/{user_id}"
                )
            
            logger.info(f"[LPT_CONTEXT] ✅ client_uuid récupéré: {client_uuid}")
            
            # Étape 2 : Appeler reconstruct_full_client_profile pour tout charger
            # ⭐ Cette méthode charge TOUT : client, mandat, ERP, ET workflow_params
            full_profile = await asyncio.to_thread(
                firebase_service.reconstruct_full_client_profile,
                user_id,
                client_uuid,
                collection_name  # contact_space_id
            )
            
            if not full_profile:
                raise ValueError(
                    f"Profil vide depuis reconstruct_full_client_profile - "
                    f"user_id={user_id}, client_uuid={client_uuid}, collection_name={collection_name}"
                )
            
            # Extraire les IDs pour construire mandate_path complet
            client_id = full_profile.get('_client_id')
            mandate_id = full_profile.get('_mandate_id')
            
            if not client_id or not mandate_id:
                raise ValueError(
                    f"client_id ou mandate_id manquant dans full_profile - "
                    f"client_id={client_id}, mandate_id={mandate_id}"
                )
            
            # ⭐ Construire le mandate_path complet (chemin Firebase réel)
            mandate_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}'
            
            # ⭐ Construire le contexte avec les noms de champs standardisés
            context = {
                # Identifiants
                "user_id": user_id,
                "collection_name": collection_name,
                "client_uuid": client_uuid,
                "client_id": client_id,
                "mandate_id": mandate_id,
                "mandate_path": mandate_path,
                "contact_space_id": full_profile.get("mandate_contact_space_id", collection_name),
                
                # Entreprise
                "company_name": full_profile.get("mandate_contact_space_name") or full_profile.get("client_name", "Entreprise"),
                "legal_name": full_profile.get("mandate_legal_name", ""),
                
                # Systèmes
                "dms_system": full_profile.get("mandate_dms_type", "google_drive"),
                "communication_mode": full_profile.get("mandate_communication_chat_type", "webhook"),
                "log_communication_mode": full_profile.get("mandate_communication_log_type", "firebase"),
                
                # Drive
                "drive_space_parent_id": full_profile.get("mandate_drive_space_parent_id", ""),
                "input_drive_doc_id": full_profile.get("mandate_input_drive_doc_id", ""),
                "output_drive_doc_id": full_profile.get("mandate_output_drive_doc_id", ""),
                
                # ERP
                "bank_erp": full_profile.get("mandate_bank_erp", ""),
                "ap_erp": full_profile.get("mandate_ap_erp", ""),
                "ar_erp": full_profile.get("mandate_ar_erp", ""),
                "gl_accounting_erp": full_profile.get("mandate_gl_accounting_erp", ""),
                
                # Configuration ERP Odoo
                "erp_odoo_url": full_profile.get("erp_odoo_url", ""),
                "erp_odoo_db": full_profile.get("erp_odoo_db", ""),
                "erp_odoo_username": full_profile.get("erp_odoo_username", ""),
                "erp_odoo_company_name": full_profile.get("erp_odoo_company_name", ""),
                "erp_secret_manager": full_profile.get("erp_secret_manager", ""),
                "erp_erp_type": full_profile.get("erp_erp_type", ""),
                
                # Localisation
                "country": full_profile.get("mandate_country", ""),
                "timezone": full_profile.get("mandate_timezone", "UTC"),
                "user_language": full_profile.get("mandate_user_language", "fr"),
                "base_currency": full_profile.get("mandate_base_currency", "CHF"),
                
                # ⭐ WORKFLOW PARAMS (paramètres d'approbation)
                "workflow_params": full_profile.get("workflow_params", {})
            }
            
            logger.info(
                f"[LPT_CONTEXT] ✅ Contexte reconstruit - "
                f"mandate_path={mandate_path}, "
                f"company_name={context['company_name']}, "
                f"dms_system={context['dms_system']}"
            )
            
            # 🔍 DEBUG : Afficher les workflow_params
            workflow_params = context.get("workflow_params", {})
            logger.info(f"[LPT_CONTEXT] 🔍 workflow_params chargés: {workflow_params}")
            
            if "Apbookeeper_param" in workflow_params:
                logger.info(
                    f"[LPT_CONTEXT] 🔍 Apbookeeper_param: "
                    f"approval_required={workflow_params['Apbookeeper_param'].get('apbookeeper_approval_required')}, "
                    f"approval_contact_creation={workflow_params['Apbookeeper_param'].get('apbookeeper_approval_contact_creation')}"
                )
            
            return context
        
        except Exception as e:
            logger.error(
                f"[LPT_CONTEXT] ❌ Erreur reconstruction profil - "
                f"user_id={user_id}, collection_name={collection_name}: {e}",
                exc_info=True
            )
            raise


