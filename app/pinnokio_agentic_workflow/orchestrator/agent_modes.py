"""Configurations de modes pour PinnokioBrain (prompts + outils)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import circulaire évité à l'exécution
    from .pinnokio_brain import PinnokioBrain
    from ...llm_service.llm_manager import LLMSession


PromptBuilder = Callable[["PinnokioBrain", Optional[Dict], str], str]
ToolBuilder = Callable[["PinnokioBrain", str, Optional["LLMSession"], str], Tuple[List[Dict], Dict]]


def _get_current_datetime_section(timezone: str = "UTC", country: str = None) -> str:
    """
    Génère une section markdown avec la date et l'heure actuelles.
    
    Args:
        timezone: Timezone IANA (ex: "Europe/Zurich")
        country: Pays optionnel pour contexte
        
    Returns:
        str: Section markdown formatée
    """
    from datetime import datetime
    import pytz
    
    try:
        tz = pytz.timezone(timezone)
        current_datetime = datetime.now(tz)
        
        # Format français pour lisibilité
        current_date_str = current_datetime.strftime("%A %d %B %Y")  # Ex: "lundi 11 novembre 2025"
        current_time_str = current_datetime.strftime("%H:%M")  # Ex: "18:20"
        
        country_info = f" ({country})" if country else ""
        
        return f"""
## 📅 DATE ET HEURE ACTUELLES

**Date du jour** : {current_date_str}
**Heure actuelle** : {current_time_str} (Timezone: {timezone}{country_info})

⚠️ **IMPORTANT** : Utilisez TOUJOURS cette date comme référence pour :
- Calculs de dates futures (demain, la semaine prochaine, le mois prochain)
- Planification de tâches (SCHEDULED, ONE_TIME)
- Délais et échéances

---
"""
    except Exception as e:
        # Fallback en cas d'erreur
        return f"""
## 📅 DATE ET HEURE ACTUELLES

⚠️ Erreur lors du calcul de la date/heure pour timezone: {timezone}

---
"""


@dataclass(frozen=True)
class AgentModeConfig:
    """Décrit comment construire le prompt + les outils pour un mode donné."""

    name: str
    prompt_builder: PromptBuilder
    tool_builder: ToolBuilder


# ---------------------------------------------------------------------------
# PROMPT BUILDERS
# ---------------------------------------------------------------------------

_FALLBACK_PROMPT = """⚠️ CONTEXTE NON CHARGÉ - Mode dégradé

    Vous êtes Pinnokio, l'assistant comptable intelligent et agent orchestrateur principal.

    🎯 VOTRE RÔLE :
    Vous êtes le cerveau de l'application. Vous avez une compréhension générale de toutes les fonctionnalités
    et vous savez quand déléguer des tâches complexes à des agents spécialisés.

    🧠 CAPACITÉS DE RAISONNEMENT :
    - Analyser des requêtes complexes multi-étapes
    - Élaborer des plans d'action structurés
    - Identifier quand utiliser SPT (outils rapides) vs LPT (tâches longues)
    - Maintenir le contexte pendant l'exécution de tâches asynchrones
    - Interagir avec l'utilisateur pendant le traitement

    📊 TYPES D'OUTILS DISPONIBLES :

    1. SPT (Short Process Tooling) - Temps < 30 secondes :
    - Accès Firebase (lecture/écriture documents)
    - Recherche ChromaDB (recherche vectorielle)
    - Requêtes API simples
    - Calculs et transformations rapides

    2. LPT (Long Process Tooling) - Temps > 30 secondes :
    - Agent File Manager (gestion documents Drive, analyses complexes)
    - Agent Comptable (saisie factures, rapprochements)
    - Traitements lourds (génération rapports, workflows complexes)

    🎯 STRATÉGIE D'ORCHESTRATION :

    1. ANALYSE DE LA REQUÊTE :
    - Identifier les sous-tâches nécessaires
    - Classifier chaque sous-tâche (SPT ou LPT)
    - Identifier les dépendances entre tâches

    2. ÉLABORATION DU PLAN :
    - Créer un plan structuré avec ordre d'exécution
    - Définir les métadonnées de traçabilité (client, projet, titre)
    - Sauvegarder le plan dans Firebase (visible UI)

    3. EXÉCUTION :
    - SPT : Exécution immédiate, attendre le résultat
    - LPT : Envoi HTTP à l'agent spécialisé, continuer sans bloquer
    - Mise à jour du plan au fur et à mesure

    4. DISPONIBILITÉ :
    - Vous restez DISPONIBLE pour l'utilisateur pendant les LPT
    - Vous pouvez répondre à des questions via SPT pendant le traitement
    - Vous reprenez le contexte quand un LPT se termine

    🔄 WORKFLOW TYPE :

    Exemple : "Accède au dossier Drive 'Factures Q1', analyse le document, et saisis les 15 factures"

    PLAN GÉNÉRÉ :
    1. [LPT] Agent File Manager : Accès Drive + Analyse document (2-3 min)
    └─> Attente callback, agent disponible pour l'utilisateur
    2. [LPT] Agent Comptable : Saisie 15 factures (5-10 min)
    └─> Attente callback, agent disponible pour l'utilisateur
    3. [SPT] Vérification statut facture particulière (< 5 sec)
    └─> Réponse immédiate

    📝 RAPPORT DE SORTIE OBLIGATOIRE :

    Quand vous utilisez TERMINATE_TASK, votre conclusion doit inclure :
    - Résumé des actions effectuées
    - Résultats de chaque tâche (SPT et LPT)
    - Statut global (succès/échec/partiel)
    - Prochaines actions suggérées

    ⚠️ RÈGLES IMPORTANTES :

    1. Ne JAMAIS bloquer l'utilisateur pendant un LPT
    2. Toujours sauvegarder le plan dans Firebase avant l'exécution
    3. Mettre à jour le plan après chaque tâche terminée
    4. Utiliser TERMINATE_TASK seulement quand TOUT est terminé
    5. En cas de LPT en cours, répondre aux questions utilisateur via SPT

    🎯 UTILISATION DES LPT - IMPORTANT :

    Quand vous utilisez un **LPT**, vous devez fournir **UNIQUEMENT** :
    1. **Les IDs des pièces** (job_ids, drive_file_id, transaction_ids)
    2. **Instructions optionnelles** (si l'utilisateur en donne)

    ❌ **NE FOURNISSEZ PAS** : collection_name, user_id, settings, client_uuid, mandates_path, etc.
    ✅ **Tout le reste est automatique** ! Le système complète automatiquement :
    - collection_name, user_id, thread_key
    - client_uuid, settings, communication_mode
    - dms_system, mandates_path
"""

def _build_onboarding_prompt(brain: "PinnokioBrain", jobs_metrics: Optional[Dict], chat_mode: str) -> str:
    """Prompt dédié à l'onboarding."""

    from .system_prompt_onboarding_agent import build_onboarding_agent_prompt

    onboarding_data = brain.onboarding_data or {}
    prompt = build_onboarding_agent_prompt(
        onboarding_data, 
        lpt_response={},
        timezone=brain.user_context.get("timezone", "UTC") if brain.user_context else "UTC",
        country=brain.user_context.get("country") if brain.user_context else None
    )

    initial_context = onboarding_data.get("initial_context_data")
    if initial_context:
        prompt += f"\n\n📎 CONTEXTE INITIAL FOURNI PAR LE CLIENT :\n{initial_context}\n"

    language = (
        (onboarding_data.get("base_info") or {}).get("language")
        or onboarding_data.get("language")
    )
    if language:
        prompt += f"\n\n🗣️ RÈGLE DE LANGUE : Réponds toujours en {language}."

    return prompt


def _build_apbookeeper_prompt(brain: "PinnokioBrain", jobs_metrics: Optional[Dict], chat_mode: str) -> str:
    """Prompt pour le mode ApBookeeper."""
    
    from ...llm_service.agent_config import AgentConfigManager
    
    base_prompt = AgentConfigManager.APBOOKEEPER_SYSTEM_PROMPT
    
    # Ajouter date/heure actuelle
    timezone = brain.user_context.get("timezone", "UTC") if brain.user_context else "UTC"
    country = brain.user_context.get("country") if brain.user_context else None
    base_prompt += _get_current_datetime_section(timezone, country)

    # Utiliser job_data au lieu de onboarding_data pour apbookeeper_chat
    job_data = brain.job_data or {}
    
    # Intégrer les champs du job dans le contexte
    if job_data:
        job_id = job_data.get("job_id", "")
        file_id = job_data.get("file_id", "")
        instructions = job_data.get("instructions", "")
        status = job_data.get("status", "")
        
        context_section = "\n\n📋 CONTEXTE DU JOB :\n"
        if job_id:
            context_section += f"- Job ID : {job_id}\n"
        if file_id:
            context_section += f"- File ID : {file_id}\n"
        if status:
            context_section += f"- Statut : {status}\n"
        if instructions:
            context_section += f"\n📝 INSTRUCTIONS :\n{instructions}\n"
        
        if context_section != "\n\n📋 CONTEXTE DU JOB :\n":
            base_prompt += context_section

    return base_prompt


def _build_router_prompt(brain: "PinnokioBrain", jobs_metrics: Optional[Dict], chat_mode: str) -> str:
    """Prompt pour le mode router_chat (routage automatique des documents)."""
    
    from ...llm_service.agent_config import AgentConfigManager
    
    base_prompt = AgentConfigManager.ROUTER_SYSTEM_PROMPT
    
    # Ajouter date/heure actuelle
    timezone = brain.user_context.get("timezone", "UTC") if brain.user_context else "UTC"
    country = brain.user_context.get("country") if brain.user_context else None
    base_prompt += _get_current_datetime_section(timezone, country)
    
    # Utiliser job_data au lieu de onboarding_data pour router_chat
    job_data = brain.job_data or {}
    
    # Intégrer les champs du job dans le contexte
    if job_data:
        job_id = job_data.get("job_id", "")
        file_id = job_data.get("file_id", "")
        instructions = job_data.get("instructions", "")
        status = job_data.get("status", "")
        
        context_section = "\n\n📋 CONTEXTE DU JOB :\n"
        if job_id:
            context_section += f"- Job ID : {job_id}\n"
        if file_id:
            context_section += f"- File ID : {file_id}\n"
        if status:
            context_section += f"- Statut : {status}\n"
        if instructions:
            context_section += f"\n📝 INSTRUCTIONS :\n{instructions}\n"
        
        if context_section != "\n\n📋 CONTEXTE DU JOB :\n":
            base_prompt += context_section
    
    return base_prompt


def _build_banker_prompt(brain: "PinnokioBrain", jobs_metrics: Optional[Dict], chat_mode: str) -> str:
    """Prompt pour le mode banker_chat (rapprochement bancaire)."""
    
    from ...llm_service.agent_config import AgentConfigManager
    
    base_prompt = AgentConfigManager.BANKER_SYSTEM_PROMPT
    
    # Ajouter date/heure actuelle
    timezone = brain.user_context.get("timezone", "UTC") if brain.user_context else "UTC"
    country = brain.user_context.get("country") if brain.user_context else None
    base_prompt += _get_current_datetime_section(timezone, country)
    
    # Utiliser job_data au lieu de onboarding_data pour banker_chat
    job_data = brain.job_data or {}
    
    # Intégrer les champs du job dans le contexte
    if job_data:
        job_id = job_data.get("job_id", "")
        file_id = job_data.get("file_id", "")
        instructions = job_data.get("instructions", "")
        status = job_data.get("status", "")
        
        context_section = "\n\n📋 CONTEXTE DU JOB :\n"
        if job_id:
            context_section += f"- Job ID : {job_id}\n"
        if file_id:
            context_section += f"- File ID : {file_id}\n"
        if status:
            context_section += f"- Statut : {status}\n"
        if instructions:
            context_section += f"\n📝 INSTRUCTIONS :\n{instructions}\n"
        
        # ═══ INJECTION DES TRANSACTIONS POUR BANKER_CHAT ═══
        formatted_transactions = job_data.get("formatted_transactions", [])
        if formatted_transactions:
            context_section += f"\n💳 TRANSACTIONS À TRAITER ({len(formatted_transactions)} transaction(s)) :\n"
            for idx, transaction in enumerate(formatted_transactions, 1):
                amount = transaction.get("amount", "")
                currency = transaction.get("currency_name", "")
                date = transaction.get("date", "")
                payment_ref = transaction.get("payment_ref", "")
                trans_status = transaction.get("status", "")
                transaction_id = transaction.get("transaction_id", "")
                
                context_section += f"\n  Transaction #{idx}:\n"
                if transaction_id:
                    context_section += f"    - ID Transaction : {transaction_id}\n"
                if amount is not None:
                    context_section += f"    - Montant : {amount} {currency}\n"
                if date:
                    context_section += f"    - Date : {date}\n"
                if payment_ref:
                    context_section += f"    - Référence paiement : {payment_ref}\n"
                if trans_status:
                    context_section += f"    - Statut : {trans_status}\n"
        
        if context_section != "\n\n📋 CONTEXTE DU JOB :\n":
            base_prompt += context_section
    
    return base_prompt


def _build_general_prompt(brain: "PinnokioBrain", jobs_metrics: Optional[Dict], chat_mode: str) -> str:
    """Construit le prompt pour les modes par défaut (general/accounting/onboarding)."""

    if brain.user_context:
        from .system_prompt_principal_agent import build_principal_agent_prompt

        metrics_to_use = jobs_metrics or brain.jobs_metrics or {}
        base_prompt = build_principal_agent_prompt(brain.user_context, metrics_to_use)
    else:
        base_prompt = _FALLBACK_PROMPT

    if chat_mode == "accounting_chat":
        base_prompt += """

        🧾 MODE COMPTABILITÉ :
        Vous êtes spécialisé dans les tâches comptables :
        - Saisie de factures fournisseurs/clients
        - Rapprochements bancaires
        - Génération d'écritures comptables
        - Vérification de TVA
        """

    return base_prompt


def _build_task_execution_prompt(brain: "PinnokioBrain", jobs_metrics: Optional[Dict], chat_mode: str) -> str:
    """Prompt dédié aux exécutions programmées (fallback sur général + ajout)."""

    base_prompt = _build_general_prompt(brain, jobs_metrics, "general_chat")
    base_prompt += """

        ⚙️ MODE EXÉCUTION AUTOMATIQUE :
        Vous exécutez de manière autonome une mission planifiée. Respectez strictement le plan,
        mettez à jour la checklist (CREATE_CHECKLIST / UPDATE_STEP) et concluez avec TERMINATE_TASK.
        """
    return base_prompt


# ---------------------------------------------------------------------------
# TOOL BUILDERS
# ---------------------------------------------------------------------------

def _build_general_tools(
    brain: "PinnokioBrain",
    thread_key: str,
    session: Optional["LLMSession"],
    chat_mode: str,
    ) -> Tuple[List[Dict], Dict]:
    return brain._build_general_chat_tools(thread_key=thread_key, session=session)


def _build_specialized_tools(
    brain: "PinnokioBrain",
    thread_key: str,
    session: Optional["LLMSession"],
    chat_mode: str,
    ) -> Tuple[List[Dict], Dict]:
    """Builder d'outils vide pour les agents spécialisés (pas d'outils pour l'instant)."""
    return [], {}


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------

_AGENT_MODE_REGISTRY: Dict[str, AgentModeConfig] = {
    "general_chat": AgentModeConfig(
        name="general_chat",
        prompt_builder=_build_general_prompt,
        tool_builder=_build_general_tools,
    ),
    "accounting_chat": AgentModeConfig(
        name="accounting_chat",
        prompt_builder=_build_general_prompt,
        tool_builder=_build_general_tools,
    ),
    "onboarding_chat": AgentModeConfig(
        name="onboarding_chat",
        prompt_builder=_build_onboarding_prompt,
        tool_builder=_build_general_tools,
    ),
    "apbookeeper_chat": AgentModeConfig(
        name="apbookeeper_chat",
        prompt_builder=_build_apbookeeper_prompt,
        tool_builder=_build_specialized_tools,
    ),
    "router_chat": AgentModeConfig(
        name="router_chat",
        prompt_builder=_build_router_prompt,
        tool_builder=_build_specialized_tools,
    ),
    "banker_chat": AgentModeConfig(
        name="banker_chat",
        prompt_builder=_build_banker_prompt,
        tool_builder=_build_specialized_tools,
    ),
    "task_execution": AgentModeConfig(
        name="task_execution",
        prompt_builder=_build_task_execution_prompt,
        tool_builder=_build_general_tools,
    ),
}


def get_agent_mode_config(chat_mode: str) -> AgentModeConfig:
    """Retourne la configuration du mode demandé (fallback sur general_chat)."""

    if not chat_mode:
        return _AGENT_MODE_REGISTRY["general_chat"]

    config = _AGENT_MODE_REGISTRY.get(chat_mode)
    if config is None:
        return _AGENT_MODE_REGISTRY["general_chat"]
    return config


