"""
TaskTools - Outils de gestion des tâches planifiées
CREATE_TASK avec mini-workflow pour déterminer la timezone via agent
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone as dt_timezone
import uuid

logger = logging.getLogger("pinnokio.task_tools")


class TaskTools:
    """
    Outil CREATE_TASK pour créer des tâches planifiées (SCHEDULED, ONE_TIME, ON_DEMAND).

    Workflow timezone:
    1. Vérifier si timezone existe dans mandate
    2. Si non : mini-workflow agent pour déterminer timezone depuis country
    3. Sauvegarder timezone dans mandate pour réutilisation
    """

    def __init__(self, brain):
        """
        Initialise TaskTools avec référence au brain.

        Args:
            brain: Instance PinnokioBrain (accès user_context, agent principal)
        """
        self.brain = brain
        logger.info("[TASK_TOOLS] Initialisé")

    def get_tool_definition(self) -> Dict:
        """Définition de l'outil CREATE_TASK."""
        return {
            "name": "CREATE_TASK",
            "description": """🔧 **Créer une tâche planifiée ou unique**

**Modes d'exécution** :
- **SCHEDULED** : Exécution récurrente (quotidienne, hebdomadaire, mensuelle)
- **ONE_TIME** : Exécution unique à une date/heure précise
- **ON_DEMAND** : Exécution immédiate (pas de sauvegarde, lance directement)

**Paramètres automatiques** :
Les métadonnées contextuelles sont ajoutées automatiquement :
- mandate_path, user_id, company_id
- timezone (calculé depuis le pays de la société, sauvegardé dans mandate)
- mandate_country, client_uuid, etc.

**Votre responsabilité** :
Définir clairement la mission et le planning.

**Format mission_plan** :
Soyez PRÉCIS et EXHAUSTIF. Numérotez les étapes :

```
1. GET_BANK_TRANSACTIONS
   - Période : mois en cours
   - Compte : principal
   - Filtres : status="pending"

2. Filtrer transactions non rapprochées
   - Critère : reconciled=false

3. CALL_BANKER_AGENT
   - transaction_ids : résultat étape 1
   - instructions : "Rapprocher automatiquement"

4. Vérifier taux de rapprochement
   - Si < 80% : alerte utilisateur
   - Sinon : rapport de synthèse

5. TERMINATE_TASK
   - Rapport complet avec statistiques
```

**Lors de l'exécution automatique** :
- L'agent dispose du dernier rapport d'exécution (si existant)
- Peut adapter son comportement selon les résultats précédents""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "mission_title": {
                        "type": "string",
                        "description": "Titre court de la tâche (ex: 'Rapprochement bancaire mensuel')"
                    },
                    "mission_description": {
                        "type": "string",
                        "description": "Description détaillée de l'objectif et des conditions d'exécution"
                    },
                    "mission_plan": {
                        "type": "string",
                        "description": "Plan d'action détaillé : outils à utiliser, ordre, arguments, conditions. Format numéroté recommandé"
                    },
                    "execution_plan": {
                        "type": "string",
                        "enum": ["SCHEDULED", "ONE_TIME", "ON_DEMAND", "NOW"],
                        "description": "Mode d'exécution de la tâche"
                    },
                    "schedule": {
                        "type": "object",
                        "description": "Configuration du planning (obligatoire si SCHEDULED)",
                        "properties": {
                            "frequency": {
                                "type": "string",
                                "enum": ["daily", "weekly", "monthly"],
                                "description": "Fréquence d'exécution"
                            },
                            "time": {
                                "type": "string",
                                "description": "Heure d'exécution (format HH:MM en heure locale, ex: '03:00')"
                            },
                            "day_of_week": {
                                "type": "string",
                                "enum": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                                "description": "Jour de la semaine (pour frequency=weekly)"
                            },
                            "day_of_month": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 31,
                                "description": "Jour du mois (pour frequency=monthly)"
                            }
                        },
                        "required": ["frequency", "time"]
                    },
                    "one_time_execution": {
                        "type": "object",
                        "description": "Date/heure unique (obligatoire si ONE_TIME)",
                        "properties": {
                            "execution_datetime": {
                                "type": "string",
                                "description": "Date et heure d'exécution en heure locale (format ISO: 2025-11-15T14:30:00)"
                            }
                        },
                        "required": ["execution_datetime"]
                    }
                },
                "required": ["mission_title", "mission_description", "mission_plan", "execution_plan"]
            }
        }

    async def create_task(self, **kwargs) -> Dict[str, Any]:
        """
        Crée une tâche planifiée, unique ou immédiate.

        Flow:
            1. Valider les paramètres selon execution_plan
            2. Récupérer timezone (avec mini-workflow si nécessaire)
            3. Selon execution_plan:
               a. SCHEDULED/ONE_TIME : Préparer données + Demander approbation
               b. ON_DEMAND : Exécuter immédiatement (pas de sauvegarde)
            4. Attendre approbation utilisateur
            5. Si approuvé : Sauvegarder tâche + scheduler
            6. Retourner résultat pour l'agent
        """
        try:
            execution_plan = kwargs["execution_plan"]

            logger.info(f"[CREATE_TASK] Mode: {execution_plan}")

            # NOW : Exécution immédiate (pas de sauvegarde, pas d'approbation)
            if execution_plan == "NOW":
                return await self._execute_immediate_task(kwargs)

            # SCHEDULED / ONE_TIME / ON_DEMAND : Préparer données + Demander approbation
            else:
                return await self._prepare_and_request_approval(kwargs, execution_plan)

        except Exception as e:
            logger.error(f"[CREATE_TASK] Erreur: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"Erreur lors de la création de la tâche: {str(e)}"
            }

    async def _prepare_and_request_approval(self, kwargs: Dict, execution_plan: str) -> Dict[str, Any]:
        """
        Prépare les données de la tâche et demande l'approbation utilisateur.
        
        Flow:
            1. Préparer toutes les données de la tâche (timezone, schedule, etc.)
            2. Construire carte d'approbation
            3. Envoyer carte via LLMManager.request_approval_with_card()
            4. Attendre réponse utilisateur (timeout 15 min)
            5. Si approuvé → _save_scheduled_task()
            6. Si rejeté → Annuler et informer l'agent
        """
        try:
            from ...llm_service.llm_manager import get_llm_manager
            
            # ═══ ÉTAPE 1 : Préparer données tâche ═══
            logger.info("[CREATE_TASK] 📋 Préparation données tâche pour approbation...")
            
            # Extraire contexte
            # ⭐ mandate_path et country viennent de user_context
            mandate_path = self.brain.user_context.get("mandate_path") if self.brain.user_context else None
            country = self.brain.user_context.get("country") if self.brain.user_context else None
            
            # ⭐ company_id et firebase_user_id viennent directement du brain
            company_id = self.brain.collection_name
            firebase_user_id = self.brain.firebase_user_id
            
            logger.info(f"[CREATE_TASK] 📊 Contexte extrait - mandate_path={mandate_path}, country={country}, company_id={company_id}, firebase_user_id={firebase_user_id}")
            
            if not all([mandate_path, company_id, firebase_user_id]):
                logger.error(f"[CREATE_TASK] ❌ Contexte incomplet - mandate_path={mandate_path}, company_id={company_id}, firebase_user_id={firebase_user_id}")
                return {
                    "type": "error",
                    "message": "Contexte utilisateur incomplet (mandate_path, company_id ou firebase_user_id manquant)"
                }
            
            # Déterminer timezone si nécessaire
            logger.info(f"[CREATE_TASK] 🌍 Appel _get_or_determine_timezone(mandate_path={mandate_path}, country={country})")
            
            try:
                timezone = await self._get_or_determine_timezone(mandate_path, country)
                logger.info(f"[CREATE_TASK] 🌍 Timezone reçue: {timezone}")
            except Exception as tz_error:
                logger.error(f"[CREATE_TASK] ❌ Exception dans _get_or_determine_timezone: {tz_error}", exc_info=True)
                return {
                    "type": "error",
                    "message": f"Erreur lors de la détermination du fuseau horaire: {str(tz_error)}"
                }
            
            if not timezone:
                logger.error(f"[CREATE_TASK] ❌ Timezone None reçue (country={country})")
                return {
                    "type": "error",
                    "message": "Impossible de déterminer le fuseau horaire. Vérifiez le pays de la société."
                }
            
            logger.info(f"[CREATE_TASK] ⏰ Timezone validée: {timezone}")
            
            # Construire schedule_data pour preview
            schedule_info = self._build_schedule_preview(kwargs, execution_plan, timezone)
            
            # ═══ ÉTAPE 2 : Construire carte d'approbation ═══
            logger.info(f"[CREATE_TASK] 📝 Construction carte d'approbation...")
            
            # Adapter le titre selon le mode
            if execution_plan == "ON_DEMAND":
                card_title = "👆 Créer tâche manuelle"
                button_text = "✅ Créer la tâche manuelle"
            elif execution_plan == "NOW":
                card_title = "🚀 Exécuter immédiatement"
                button_text = "✅ Lancer l'exécution"
            else:
                card_title = f"📅 Créer tâche {execution_plan}"
                button_text = "✅ Créer la tâche"
            
            card_params = {
                "title": card_title,
                "subtitle": kwargs.get("mission_title", "Nouvelle tâche"),
                "text": self._build_approval_card_text(kwargs, execution_plan, schedule_info, timezone),
                "input_label": "Commentaire sur la tâche (optionnel)",
                "button_text": button_text,
                "button_action": "approve_task_creation",
                "execution_mode": execution_plan  # ✅ Ajout du mode d'exécution explicite
            }
            
            logger.info(f"[CREATE_TASK] ✅ Carte construite - title={card_params['title']}, subtitle={card_params['subtitle']}")
            
            # ═══ ÉTAPE 3 : Envoyer carte et attendre réponse ═══
            thread_key = self.brain.active_thread_key
            
            logger.info(f"[CREATE_TASK] 🔑 thread_key récupéré: {thread_key}")
            
            if not thread_key:
                logger.error("[CREATE_TASK] ❌ thread_key non disponible, création directe sans approbation")
                # Fallback : créer directement
                return await self._save_scheduled_task(kwargs, execution_plan)
            
            logger.info(f"[CREATE_TASK] 🃏 Préparation envoi carte (user={firebase_user_id}, company={company_id}, thread={thread_key})")
            
            try:
                llm_manager = get_llm_manager()
                logger.info(f"[CREATE_TASK] ✅ LLMManager récupéré, appel request_approval_with_card...")
                
                approval_result = await llm_manager.request_approval_with_card(
                    user_id=firebase_user_id,
                    collection_name=company_id,
                    thread_key=thread_key,
                    card_type="task_creation_approval",
                    card_params=card_params,
                    timeout=900  # 15 minutes
                )
                
                logger.info(f"[CREATE_TASK] 📬 Réponse reçue: {approval_result}")
                
            except Exception as card_error:
                logger.error(f"[CREATE_TASK] ❌ Exception lors de l'envoi de la carte: {card_error}", exc_info=True)
                return {
                    "type": "error",
                    "message": f"Erreur lors de l'envoi de la carte d'approbation: {str(card_error)}"
                }
            
            # ═══ ÉTAPE 4 : Traiter réponse ═══
            if approval_result.get("timeout"):
                logger.warning("[CREATE_TASK] ⏱️ Timeout approbation (15 min)")
                return {
                    "type": "error",
                    "message": "Timeout : aucune réponse reçue après 15 minutes. Création annulée."
                }
            
            if not approval_result.get("approved"):
                logger.info("[CREATE_TASK] ❌ Tâche rejetée par l'utilisateur")
                user_comment = approval_result.get("user_message", "")
                return {
                    "type": "cancelled",
                    "message": f"Création de tâche annulée par l'utilisateur.{' Raison : ' + user_comment if user_comment else ''}"
                }
            
            # ═══ ÉTAPE 5 : Approbation OK → Créer tâche ═══
            logger.info("[CREATE_TASK] ✅ Tâche approuvée, création en cours...")
            user_comment = approval_result.get("user_message", "")
            
            # Ajouter commentaire utilisateur aux kwargs si présent
            if user_comment:
                kwargs["approval_comment"] = user_comment
            
            return await self._save_scheduled_task(kwargs, execution_plan)
            
        except Exception as e:
            logger.error(f"[CREATE_TASK] Erreur préparation/approbation: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"Erreur lors de la préparation de la tâche: {str(e)}"
            }
    
    def _build_schedule_preview(self, kwargs: Dict, execution_plan: str, timezone: str) -> str:
        """Construit un aperçu lisible du schedule."""
        if execution_plan == "SCHEDULED":
            schedule = kwargs.get("schedule", {})
            frequency = schedule.get("frequency", "?")
            time = schedule.get("time", "?")
            
            if frequency == "daily":
                return f"Tous les jours à {time} ({timezone})"
            elif frequency == "weekly":
                day = schedule.get("day_of_week", "?")
                return f"Chaque {day} à {time} ({timezone})"
            elif frequency == "monthly":
                day = schedule.get("day_of_month", "?")
                return f"Le {day} de chaque mois à {time} ({timezone})"
            else:
                return f"{frequency} à {time} ({timezone})"
        
        elif execution_plan == "ONE_TIME":
            one_time = kwargs.get("one_time_execution", {})
            exec_dt = one_time.get("execution_datetime", "?")
            return f"Une fois le {exec_dt} ({timezone})"
        
        elif execution_plan == "ON_DEMAND":
            return f"Exécution manuelle (pas de planification automatique)"

        elif execution_plan == "NOW":
            return f"Exécution immédiate (pas de planification)"

        return "?"
    
    def _build_approval_card_text(self, kwargs: Dict, execution_plan: str, schedule_info: str, timezone: str) -> str:
        """Construit le texte de la carte d'approbation."""
        mission_title = kwargs.get("mission_title", "Sans titre")
        mission_desc = kwargs.get("mission_description", "")
        mission_plan = kwargs.get("mission_plan", "")
        
        text = f"""**📋 Titre :** {mission_title}

**📝 Description :**
{mission_desc}

**🎯 Plan d'action :**
{mission_plan[:300]}{'...' if len(mission_plan) > 300 else ''}

**⏰ Planification :**
{schedule_info}

**🌍 Fuseau horaire :** {timezone}
"""
        return text

    async def _save_scheduled_task(self, kwargs: Dict, execution_plan: str) -> Dict[str, Any]:
        """
        Sauvegarde une tâche SCHEDULED, ONE_TIME ou ON_DEMAND.

        Steps:
            1. Générer task_id
            2. Extraire contexte brain (mandate_path, country, company_id, user_id)
            3. Obtenir/déterminer timezone (avec mini-workflow agent si nécessaire)
            4. Construire schedule_data:
               - SCHEDULED: CRON + next_execution
               - ONE_TIME: next_execution direct
               - ON_DEMAND: manual_execution (pas de planning)
               - NOW: pas de schedule_data (exécution immédiate)
            5. Construire task_data complet
            6. Appeler firebase.create_task()
            7. Si SCHEDULED/ONE_TIME: Mettre à jour scheduler (ON_DEMAND/NOW n'est pas ajouté)
            8. Retourner succès avec infos UI
        """
        try:
            from ...firebase_providers import get_firebase_management
            fbm = get_firebase_management()

            # 1. Générer task_id
            task_id = f"task_{uuid.uuid4().hex[:12]}"

            # 2. Extraire contexte
            user_context = self.brain.user_context
            mandate_path = user_context.get("mandate_path")
            country = user_context.get("country")
            user_id = self.brain.firebase_user_id
            company_id = self.brain.collection_name

            if not mandate_path:
                return {
                    "type": "error",
                    "message": "mandate_path non disponible dans le contexte"
                }

            # 3. Obtenir timezone (avec mini-workflow si nécessaire)
            timezone_str = await self._get_or_determine_timezone(mandate_path, country)

            if not timezone_str:
                return {
                    "type": "error",
                    "message": "Impossible de déterminer la timezone"
                }

            logger.info(f"[CREATE_TASK] Timezone: {timezone_str}")

            # 4. Construire schedule_data
            schedule_data = {}

            if execution_plan == "SCHEDULED":
                schedule = kwargs.get("schedule", {})
                frequency = schedule.get("frequency")
                time_str = schedule.get("time")
                day_of_week = schedule.get("day_of_week")
                day_of_month = schedule.get("day_of_month")

                # Valider
                if not frequency or not time_str:
                    return {
                        "type": "error",
                        "message": "schedule.frequency et schedule.time sont requis pour SCHEDULED"
                    }

                # Construire CRON
                cron_expression = fbm.build_task_cron_expression(
                    frequency, time_str, day_of_week, day_of_month
                )
                
                # ⭐ VALIDATION : Vérifier que cron_expression n'est pas vide
                if not cron_expression:
                    logger.error(f"[CREATE_TASK] ❌ Expression CRON vide - frequency={frequency}, time={time_str}, day_of_week={day_of_week}, day_of_month={day_of_month}")
                    return {
                        "type": "error",
                        "message": f"Impossible de construire l'expression CRON. Vérifiez le format de l'heure ({time_str}) et la fréquence ({frequency})."
                    }
                
                logger.info(f"[CREATE_TASK] ✅ CRON expression construite : '{cron_expression}'")

                # Calculer next_execution (local_time et UTC)
                next_local, next_utc = fbm.calculate_task_next_execution(
                    cron_expression, timezone_str
                )
                
                # ⭐ VALIDATION : Vérifier que les valeurs calculées ne sont pas vides
                if not next_local or not next_utc:
                    logger.error(f"[CREATE_TASK] ❌ Calcul next_execution échoué - cron='{cron_expression}', timezone='{timezone_str}', next_local='{next_local}', next_utc='{next_utc}'")
                    return {
                        "type": "error",
                        "message": f"Impossible de calculer la prochaine exécution. Timezone: {timezone_str}, CRON: {cron_expression}"
                    }
                
                logger.info(f"[CREATE_TASK] ✅ Next execution calculée - local: {next_local}, UTC: {next_utc}")

                schedule_data = {
                    "frequency": frequency,
                    "time": time_str,
                    "day_of_week": day_of_week,
                    "day_of_month": day_of_month,
                    "timezone": timezone_str,
                    "cron_expression": cron_expression,
                    "next_execution_local_time": next_local,
                    "next_execution_utc": next_utc
                }

            elif execution_plan == "ONE_TIME":
                one_time = kwargs.get("one_time_execution", {})
                execution_datetime = one_time.get("execution_datetime")

                if not execution_datetime:
                    return {
                        "type": "error",
                        "message": "one_time_execution.execution_datetime est requis pour ONE_TIME"
                    }

                # Parser et convertir en UTC
                import pytz
                from dateutil import parser

                tz = pytz.timezone(timezone_str)
                local_dt = parser.isoparse(execution_datetime)

                # Ajouter timezone si absent
                if local_dt.tzinfo is None:
                    local_dt = tz.localize(local_dt)

                # Convertir en UTC
                utc_dt = local_dt.astimezone(pytz.utc)

                schedule_data = {
                    "frequency": "one_time",
                    "timezone": timezone_str,
                    "next_execution_local_time": local_dt.isoformat(),
                    "next_execution_utc": utc_dt.isoformat()
                }

            elif execution_plan == "ON_DEMAND":
                # ON_DEMAND : pas de schedule (exécution manuelle)
                schedule_data = {
                    "frequency": "on_demand",
                    "timezone": timezone_str,
                    "manual_execution": True
                }

            # 5. Construire task_data complet
            mission_data = {
                "title": kwargs.get("mission_title"),
                "description": kwargs.get("mission_description"),
                "plan": kwargs.get("mission_plan")
            }

            task_data = {
                "task_id": task_id,
                "user_id": user_id,
                "company_id": company_id,
                "mandate_path": mandate_path,
                "execution_plan": execution_plan,
                "mission": mission_data,
                "schedule": schedule_data,
                "status": "active",
                "enabled": True,
                "last_execution_report": None
            }

            # 6. Sauvegarder
            result = fbm.create_task(mandate_path, task_data)

            if not result.get("success"):
                return {
                    "type": "error",
                    "message": f"Échec sauvegarde: {result.get('error')}"
                }

            # 7. Construire réponse
            if execution_plan == "SCHEDULED":
                schedule_summary = self._build_schedule_summary(schedule_data)
                return {
                    "type": "success",
                    "task_id": task_id,
                    "execution_plan": execution_plan,
                    "message": f"✅ Tâche '{mission_data['title']}' créée avec succès",
                    "next_execution_local_time": schedule_data["next_execution_local_time"],
                    "next_execution_utc": schedule_data["next_execution_utc"],
                    "schedule_summary": schedule_summary,
                    "ui_payload": {
                        "mission_title": mission_data['title'],
                        "mission_description": mission_data['description'],
                        "execution_plan": execution_plan,
                        "schedule_summary": schedule_summary,
                        "status": "active"
                    }
                }

            elif execution_plan == "ONE_TIME":
                return {
                    "type": "success",
                    "task_id": task_id,
                    "execution_plan": execution_plan,
                    "message": f"✅ Tâche unique '{mission_data['title']}' créée",
                    "execution_datetime_local": schedule_data["next_execution_local_time"],
                    "execution_datetime_utc": schedule_data["next_execution_utc"],
                    "ui_payload": {
                        "mission_title": mission_data['title'],
                        "mission_description": mission_data['description'],
                        "execution_plan": execution_plan,
                        "execution_datetime": schedule_data["next_execution_local_time"],
                        "status": "active"
                    }
                }

            elif execution_plan == "ON_DEMAND":
                return {
                    "type": "success",
                    "task_id": task_id,
                    "execution_plan": execution_plan,
                    "message": f"✅ Tâche ON_DEMAND '{mission_data['title']}' créée",
                    "manual_execution": True,
                    "ui_payload": {
                        "mission_title": mission_data['title'],
                        "mission_description": mission_data['description'],
                        "execution_plan": execution_plan,
                        "status": "active",
                        "manual_execution": True
                    }
                }

        except Exception as e:
            logger.error(f"[CREATE_TASK] Erreur _save_scheduled_task: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"Erreur: {str(e)}"
            }

    async def _get_or_determine_timezone(self, mandate_path: str, country: str) -> Optional[str]:
        """
        Obtient ou détermine la timezone IANA via workflow agent.

        ⭐ NOUVEAU Workflow avec YES_OR_NO:
            1. Vérifier si timezone existe dans brain.user_context
            2. Si oui: Demander à l'agent via YES_OR_NO si mise à jour nécessaire
               - Si NO: Retourner timezone existante
               - Si YES: Continuer vers DETERMINE_TIMEZONE
            3. Utiliser DETERMINE_TIMEZONE pour sélectionner/mettre à jour
            4. Retourner nouvelle timezone
        """
        logger.info(f"[TIMEZONE] 🚀 DÉBUT _get_or_determine_timezone(mandate_path={mandate_path}, country={country})")
        
        try:
            from ...llm.klk_agents import ModelSize
            from .timezone_enum import get_timezone_choices_for_tool
            
            logger.info(f"[TIMEZONE] ✅ Imports réussis")

            if not self.brain or not self.brain.pinnokio_agent:
                logger.error("[TIMEZONE] ❌ Agent principal non disponible")
                return None
            
            logger.info(f"[TIMEZONE] ✅ Brain et agent disponibles")

            # 1. Vérifier si timezone existe dans brain.user_context
            existing_timezone = self.brain.user_context.get("timezone") if self.brain.user_context else None
            existing_country = self.brain.user_context.get("country") if self.brain.user_context else None
            
            logger.info(f"[TIMEZONE] 📊 Contexte actuel - timezone={existing_timezone}, country={existing_country}")

            # 2. Si timezone existe, demander confirmation via YES_OR_NO
            if existing_timezone and existing_timezone != "no timezone found":
                logger.info(f"[TIMEZONE] ✅ Timezone existante valide: {existing_timezone} (pays: {existing_country})")
                logger.info(f"[TIMEZONE] 🔄 Lancement workflow YES_OR_NO pour validation...")
                
                # Créer l'outil YES_OR_NO
                yes_or_no_tool = {
                    "name": "YES_OR_NO",
                    "description": "❓ Répondez par YES ou NO pour indiquer si une mise à jour est nécessaire.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "answer": {
                                "type": "string",
                                "enum": ["YES", "NO"],
                                "description": "YES si mise à jour nécessaire, NO si timezone actuelle convient"
                            }
                        },
                        "required": ["answer"]
                    }
                }
                
                # Mapping simple (synchrone pour process_tool_use)
                def _yes_or_no_handler(answer: str):
                    return {"success": True, "answer": answer}
                
                # Prompt pour validation
                validation_prompt = f"""**Timezone actuelle configurée:** `{existing_timezone}`
**Pays actuel:** `{existing_country or "Non spécifié"}`
**Nouveau pays demandé par l'utilisateur:** `{country}`

Analysez ces informations:
- La timezone actuelle `{existing_timezone}` correspond-elle au nouveau pays `{country}`?
- Est-il nécessaire de mettre à jour le fuseau horaire?

**Utilisez l'outil YES_OR_NO pour répondre:**
- Répondez **YES** si la timezone doit être mise à jour (pays différent ou timezone incorrecte)
- Répondez **NO** si la timezone actuelle convient parfaitement"""

                # Appel agent pour validation
                response = self.brain.pinnokio_agent.process_tool_use(
                    content=validation_prompt,
                    tools=[yes_or_no_tool],
                    tool_mapping={"YES_OR_NO": _yes_or_no_handler},
                    provider=self.brain.default_provider,
                    size=ModelSize.SMALL,
                    tool_choice={'type':'tool','name':'YES_OR_NO'},
                    raw_output=False
                )
                
                # ⭐ Extraire la réponse avec la bonne clé "answer"
                answer = response.get("answer") if isinstance(response, dict) else None
                
                logger.info(f"[TIMEZONE] 📋 Validation agent reçue: {answer}")
                
                if answer == "NO":
                    logger.info(f"[TIMEZONE] ✅ Timezone actuelle conservée: {existing_timezone}")
                    return existing_timezone
                else:
                    logger.info(f"[TIMEZONE] 🔄 Réponse YES ou invalide, passage à DETERMINE_TIMEZONE")
                # Si YES, continuer vers DETERMINE_TIMEZONE
            else:
                logger.info(f"[TIMEZONE] ⚠️ Pas de timezone valide (timezone={existing_timezone}), passage direct à DETERMINE_TIMEZONE")

            # 3. Pas de timezone OU mise à jour nécessaire → DETERMINE_TIMEZONE
            logger.info(f"[TIMEZONE] 🌍 Configuration timezone pour pays: {country}")
            
            if not country or country == "None":
                logger.error(f"[TIMEZONE] ❌ Country invalide ({country}), impossible de déterminer timezone")
                return None
            
            determine_tz_tool = {
                "name": "DETERMINE_TIMEZONE",
                "description": "🌍 Sélectionnez la timezone IANA appropriée pour le pays.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "country": {
                            "type": "string",
                            "description": "Pays de la société"
                        },
                        "timezone": {
                            "type": "string",
                            "enum": get_timezone_choices_for_tool(),
                            "description": "Timezone IANA à utiliser"
                        }
                    },
                    "required": ["country", "timezone"]
                }
            }
            
            # Mapping qui met à jour user_context ET sauvegarde dans Firebase
            # Synchrone pour être appelable depuis process_tool_use sans await
            def _determine_tz_handler(country: str, timezone: str):
                if self.brain.user_context:
                    # 1. Mettre à jour en mémoire (pour la session actuelle)
                    self.brain.user_context["timezone"] = timezone
                    self.brain.user_context["country"] = country
                    
                    # 2. ⭐ Sauvegarder dans Firebase (pour la persistance)
                    mandate_path = self.brain.user_context.get("mandate_path")
                    if mandate_path:
                        from ...firebase_providers import get_firebase_management
                        fbm = get_firebase_management()
                        fbm.save_timezone_to_mandate(mandate_path, timezone)
                        logger.info(f"[DETERMINE_TIMEZONE] ✅ Timezone sauvegardée dans Firebase: {timezone}")
                    
                    return {
                        "success": True,
                        "timezone": timezone,
                        "country": country
                    }
                return {"success": False, "error": "user_context non disponible"}
            
            # Prompt pour sélection
            selection_prompt = f"""**Pays de la société:** `{country}`

Utilisez l'outil **DETERMINE_TIMEZONE** pour sélectionner le fuseau horaire IANA approprié.

Sélectionnez le fuseau horaire qui correspond exactement au pays `{country}` dans la liste disponible."""

            # ⭐ Appel agent via process_tool_use (comme dans pinnokio_brain.py)
            response = self.brain.pinnokio_agent.process_tool_use(
                content=selection_prompt,
                tools=[determine_tz_tool],
                tool_mapping={"DETERMINE_TIMEZONE": _determine_tz_handler},
                provider=self.brain.default_provider,
                size=ModelSize.SMALL,
                tool_choice={'type':'tool','name':'DETERMINE_TIMEZONE'},
                raw_output=False
            )
            
            # ⭐ Extraire la timezone avec la bonne clé "timezone"
            logger.info(f"[TIMEZONE] 📬 Réponse DETERMINE_TIMEZONE reçue: {response}")
            
            timezone = response.get("timezone") if isinstance(response, dict) else None
            
            if timezone:
                logger.info(f"[TIMEZONE] ✅ Timezone configurée avec succès: {timezone}")
                logger.info(f"[TIMEZONE] 🎉 FIN _get_or_determine_timezone - retour: {timezone}")
                return timezone
            else:
                logger.error(f"[TIMEZONE] ❌ Échec configuration, timezone non extraite de la réponse: {response}")
                return None

        except Exception as e:
            logger.error(f"[TIMEZONE] ❌ Exception capturée: {e}", exc_info=True)
            logger.info(f"[TIMEZONE] 💥 FIN _get_or_determine_timezone - retour: None (exception)")
            return None

    async def _execute_immediate_task(self, kwargs: Dict) -> Dict[str, Any]:
        """
        Exécute une tâche immédiatement (NOW).

        Steps:
            1. Construire task_data (comme pour les autres modes)
            2. Demander approbation utilisateur
            3. Si approuvé : exécuter immédiatement via LLM Manager
            4. Retourner confirmation de lancement

        Note: Pas de sauvegarde dans tasks/, pas dans scheduler
        """
        try:
            from ...llm_service.llm_manager import get_llm_manager

            # 1. Construire task_data (similaire aux autres modes)
            task_id = f"task_{uuid.uuid4().hex[:12]}"
            execution_id = f"exec_{uuid.uuid4().hex[:12]}"

            # Extraire contexte
            user_context = self.brain.user_context
            mandate_path = user_context.get("mandate_path")
            country = user_context.get("country")
            user_id = self.brain.firebase_user_id
            company_id = self.brain.collection_name

            if not mandate_path:
                return {
                        "type": "error",
                        "message": "mandate_path non disponible dans le contexte"
                    }

            # Obtenir timezone
            timezone_str = await self._get_or_determine_timezone(mandate_path, country)
            if not timezone_str:
                return {
                    "type": "error",
                    "message": "Impossible de déterminer la timezone"
                }

            # Construire task_data
            mission_data = {
                "title": kwargs.get("mission_title"),
                "description": kwargs.get("mission_description"),
                "plan": kwargs.get("mission_plan")
            }

            task_data = {
                "task_id": task_id,
                "user_id": user_id,
                "company_id": company_id,
                "mandate_path": mandate_path,
                "execution_plan": "NOW",
                "mission": mission_data,
                "schedule": {},
                "status": "executing",
                "enabled": True,
                "last_execution_report": None
            }

            # 2. Demander approbation (comme pour les autres modes)
            logger.info("[CREATE_TASK] 📋 Préparation approbation pour exécution immédiate...")

            # Construire carte d'approbation
            schedule_info = "Exécution immédiate (pas de planification)"
            card_params = {
                "title": "🚀 Exécuter immédiatement",
                "subtitle": kwargs.get("mission_title", "Exécution immédiate"),
                "text": self._build_approval_card_text(kwargs, "NOW", schedule_info, timezone_str),
                "input_label": "Commentaire sur l'exécution (optionnel)",
                "button_text": "✅ Lancer l'exécution",
                "button_action": "approve_task_creation"
            }

            # Envoyer carte et attendre réponse
            thread_key = self.brain.active_thread_key
            if not thread_key:
                logger.error("[CREATE_TASK] ❌ thread_key non disponible")
                return {
                    "type": "error",
                    "message": "Thread non disponible pour l'approbation"
                }

            llm_manager = get_llm_manager()
            approval_result = await llm_manager.request_approval_with_card(
                user_id=user_id,
                collection_name=company_id,
                thread_key=thread_key,
                card_type="task_creation_approval",
                card_params=card_params,
                timeout=900
            )

            # Traiter réponse
            if approval_result.get("timeout"):
                return {
                    "type": "error",
                    "message": "Timeout : aucune réponse reçue après 15 minutes."
                }

            if not approval_result.get("approved"):
                user_comment = approval_result.get("user_message", "")
                return {
                    "type": "cancelled",
                    "message": f"Exécution annulée par l'utilisateur.{' Raison : ' + user_comment if user_comment else ''}"
                }

            # 3. Exécuter immédiatement via LLM Manager
            # Pour NOW : PAS stocké dans Firebase (éphémère)
            logger.info("[CREATE_TASK] ✅ Approbation reçue, lancement exécution immédiate...")

            # Modifier task_data pour indiquer que c'est NOW (pas stocké)
            task_data["execution_plan"] = "NOW"
            task_data["stored_in_firebase"] = False

            await llm_manager._execute_scheduled_task(
                user_id=user_id,
                company_id=company_id,
                task_data=task_data,
                thread_key=thread_key,
                execution_id=execution_id
            )

            # 4. Retourner confirmation
            return {
                "type": "success",
                "task_id": task_id,
                "execution_id": execution_id,
                "execution_plan": "NOW",
                "message": f"✅ Exécution NOW '{mission_data['title']}' lancée immédiatement",
                "status": "executing"
            }

        except Exception as e:
            logger.error(f"[CREATE_TASK] Erreur _execute_immediate_task: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"Erreur lors du lancement: {str(e)}"
            }

    def _build_schedule_summary(self, schedule_data: Dict) -> str:
        """Construit un résumé lisible du schedule."""
        frequency = schedule_data.get("frequency")
        time_str = schedule_data.get("time")
        timezone = schedule_data.get("timezone")

        if frequency == "daily":
            return f"Quotidien à {time_str} ({timezone})"

        elif frequency == "weekly":
            day_of_week = schedule_data.get("day_of_week")
            return f"Hebdomadaire - {day_of_week} à {time_str} ({timezone})"

        elif frequency == "monthly":
            day_of_month = schedule_data.get("day_of_month")
            return f"Mensuel - le {day_of_month} à {time_str} ({timezone})"

        else:
            return f"{frequency} à {time_str} ({timezone})"
