"""
WAIT_ON_LPT - Outil pour mettre en pause le workflow en attente d'un callback LPT.

Cet outil est utilisé quand :
1. L'agent est en workflow (tâche planifiée)
2. Un LPT a été lancé mais pas encore retourné
3. La prochaine étape dépend du résultat du LPT
4. L'agent doit "s'éteindre" proprement en attendant le callback

Workflow :
- L'agent appelle WAIT_ON_LPT avec les infos du LPT attendu
- Le workflow se met en pause (mission_completed = True de facto)
- Quand le callback arrive, le workflow reprend automatiquement
- L'agent reçoit le résultat du LPT et continue sa checklist
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger("pinnokio.wait_on_lpt")


class WaitOnLPTTool:
    """
    Outil WAIT_ON_LPT pour mettre le workflow en attente d'un callback LPT.
    
    Usage :
    - L'agent l'utilise quand il a lancé un LPT et doit attendre son retour
    - Le workflow se met en pause proprement
    - Le système reprendra automatiquement au callback
    """
    
    def __init__(self, brain=None, thread_key: str = None, mode: str = "UI"):
        """
        Initialise l'outil WAIT_ON_LPT.
        
        Args:
            brain: Instance PinnokioBrain (optionnel)
            thread_key: Clé du thread actuel
            mode: Mode d'exécution ("UI" ou "BACKEND")
        """
        self.brain = brain
        self.thread_key = thread_key
        self.mode = mode
        logger.info(f"[WAIT_ON_LPT] Initialisé (thread={thread_key}, mode={mode})")
    
    def get_tool_definition(self) -> Dict:
        """Définition de l'outil WAIT_ON_LPT."""
        return {
            "name": "WAIT_ON_LPT",
            "description": """⏳ **Mettre le workflow en pause en attente d'un callback LPT**

**QUAND UTILISER CET OUTIL :**
- Vous avez lancé un LPT (ex: LPT_APBookkeeper, LPT_Router, etc.)
- Ce LPT n'a PAS encore retourné son résultat (pas de callback reçu)
- La suite de votre workflow DÉPEND du résultat de ce LPT
- Vous devez attendre avant de pouvoir continuer

**CE QUI SE PASSE :**
1. Le workflow se met en pause proprement
2. Vous serez automatiquement réactivé quand le LPT terminera
3. Vous recevrez le résultat du LPT et pourrez continuer votre checklist

**QUAND NE PAS UTILISER :**
- Si vous n'avez pas lancé de LPT
- Si le LPT a déjà retourné son callback (résultat déjà disponible)
- Si vous pouvez continuer avec d'autres tâches en parallèle

**EXEMPLE D'APPEL :**
```json
{
    "reason": "Attente du retour de LPT_APBookkeeper pour la saisie des 5 factures",
    "expected_lpt": "LPT_APBookkeeper",
    "step_waiting": "STEP_2_SAISIE_FACTURES",
    "task_ids": ["file_abc123", "file_def456"]
}
```

**RAPPEL :**
- Cet outil est une "mise en veille" du workflow, pas un arrêt définitif
- Le workflow reprendra automatiquement au callback LPT
- Votre contexte et votre checklist seront préservés""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Raison de l'attente (ex: 'Attente du retour de LPT_APBookkeeper pour saisie factures')"
                    },
                    "expected_lpt": {
                        "type": "string",
                        "enum": ["LPT_APBookkeeper", "LPT_Router", "LPT_Banker", "LPT_FileManager", "OTHER"],
                        "description": "Type de LPT attendu"
                    },
                    "step_waiting": {
                        "type": "string",
                        "description": "ID de l'étape de la checklist en attente (ex: 'STEP_2_SAISIE_FACTURES')"
                    },
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des IDs de tâches/fichiers envoyés au LPT (optionnel)"
                    },
                    "additional_context": {
                        "type": "string",
                        "description": "Contexte additionnel pour la reprise (optionnel)"
                    }
                },
                "required": ["reason", "expected_lpt"]
            }
        }
    
    async def execute(
        self,
        reason: str,
        expected_lpt: str,
        step_waiting: str = None,
        task_ids: list = None,
        additional_context: str = None
    ) -> Dict[str, Any]:
        """
        Met le workflow en pause en attente d'un callback LPT.
        
        Args:
            reason: Raison de l'attente
            expected_lpt: Type de LPT attendu
            step_waiting: ID de l'étape en attente
            task_ids: Liste des IDs envoyés au LPT
            additional_context: Contexte additionnel
            
        Returns:
            Dict avec les informations de mise en pause
        """
        try:
            logger.info(f"[WAIT_ON_LPT] 🔄 Mise en pause workflow - Raison: {reason}")
            logger.info(f"[WAIT_ON_LPT] 📋 LPT attendu: {expected_lpt}")
            
            if step_waiting:
                logger.info(f"[WAIT_ON_LPT] 📍 Étape en attente: {step_waiting}")
            
            if task_ids:
                logger.info(f"[WAIT_ON_LPT] 📝 Task IDs: {task_ids}")
            
            # Préparer les données de pause pour Redis
            pause_data = {
                "reason": reason,
                "expected_lpt": expected_lpt,
                "step_waiting": step_waiting,
                "task_ids": task_ids or [],
                "additional_context": additional_context,
                "paused_at": datetime.now(timezone.utc).isoformat(),
                "mode": self.mode,
                "thread_key": self.thread_key
            }
            
            # Sauvegarder dans Redis via WorkflowStateManager
            if self.thread_key:
                try:
                    from ...llm_service.workflow_state_manager import get_workflow_state_manager
                    
                    workflow_manager = get_workflow_state_manager()
                    
                    # Marquer le workflow comme en attente de LPT
                    await workflow_manager.set_waiting_for_lpt(
                        thread_key=self.thread_key,
                        lpt_info=pause_data
                    )
                    
                    logger.info(f"[WAIT_ON_LPT] ✅ État sauvegardé dans Redis")
                    
                except Exception as redis_error:
                    logger.warning(f"[WAIT_ON_LPT] ⚠️ Erreur Redis (non bloquant): {redis_error}")
            
            # Préparer le message de confirmation pour l'agent
            # Ce message sera le dernier avant la "mise en veille"
            confirmation_message = f"""⏳ **Workflow en pause - Attente de callback LPT**

**Raison :** {reason}
**LPT attendu :** {expected_lpt}
{f'**Étape en attente :** {step_waiting}' if step_waiting else ''}
{f'**Tâches concernées :** {len(task_ids)} fichier(s)' if task_ids else ''}

Le workflow reprendra automatiquement quand le LPT terminera son traitement.
Votre contexte et votre checklist sont préservés.
"""
            
            logger.info(f"[WAIT_ON_LPT] 📤 Retour résultat - workflow mis en pause")
            
            # Retourner un résultat spécial qui sera traité par la boucle agentic
            # Le flag "_wait_on_lpt" indique à la boucle de terminer proprement
            return {
                "type": "wait_on_lpt",
                "status": "paused",
                "message": confirmation_message,
                "pause_data": pause_data,
                "_wait_on_lpt": True,  # Flag spécial pour la boucle agentic
                "_terminate_workflow": True  # Indique que le workflow doit s'arrêter proprement
            }
            
        except Exception as e:
            logger.error(f"[WAIT_ON_LPT] ❌ Erreur: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"Erreur lors de la mise en pause: {str(e)}",
                "_wait_on_lpt": False
            }


def create_wait_on_lpt_tool(brain=None, thread_key: str = None, mode: str = "UI") -> tuple:
    """
    Factory pour créer l'outil WAIT_ON_LPT.
    
    Returns:
        Tuple (definition, handler_mapping)
    """
    tool = WaitOnLPTTool(brain=brain, thread_key=thread_key, mode=mode)
    
    return (
        tool.get_tool_definition(),
        {"WAIT_ON_LPT": tool.execute}
    )

