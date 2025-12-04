"""
Validateur pour TERMINATE_TASK - Vérifie que toutes les étapes sont "completed".

En mode "execution" (tâche planifiée), TERMINATE_TASK ne peut être appelé
que si TOUTES les étapes de la checklist sont au statut "completed".

Sinon, l'appel est refusé avec un message listant les étapes manquantes.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("pinnokio.terminate_validator")


def validate_terminate_task(
    brain,
    reason: str,
    conclusion: str
) -> Tuple[bool, Dict[str, Any]]:
    """
    Valide si TERMINATE_TASK peut être appelé.
    
    En mode execution (tâche planifiée), vérifie que toutes les étapes
    de la checklist sont "completed".
    
    Args:
        brain: Instance PinnokioBrain
        reason: Raison de terminaison fournie par l'agent
        conclusion: Conclusion fournie par l'agent
        
    Returns:
        Tuple (is_valid, result_dict)
        - is_valid: True si TERMINATE_TASK peut être exécuté
        - result_dict: Résultat à retourner (succès ou erreur avec détails)
    """
    
    # Vérifier si on est en mode execution (tâche planifiée)
    if not brain or not brain.active_task_data:
        # Mode normal (conversation utilisateur) → pas de vérification
        logger.debug("[TERMINATE_VALIDATOR] Mode normal, pas de vérification")
        return True, {
            "type": "success",
            "reason": reason,
            "conclusion": conclusion,
            "mode": "normal"
        }
    
    # Mode execution → vérification obligatoire
    logger.info("[TERMINATE_VALIDATOR] 🔍 Vérification checklist en mode execution...")
    
    task_id = brain.active_task_data.get("task_id")
    execution_id = brain.active_task_data.get("execution_id")
    mandate_path = brain.active_task_data.get("mandate_path")
    
    if not all([task_id, execution_id, mandate_path]):
        logger.warning("[TERMINATE_VALIDATOR] ⚠️ Données de tâche incomplètes")
        return True, {
            "type": "success",
            "reason": reason,
            "conclusion": conclusion,
            "mode": "execution",
            "warning": "Données de tâche incomplètes, validation ignorée"
        }
    
    try:
        from ...firebase_providers import get_firebase_management
        fbm = get_firebase_management()
        
        execution = fbm.get_task_execution(mandate_path, task_id, execution_id)
        
        if not execution:
            logger.warning("[TERMINATE_VALIDATOR] ⚠️ Exécution non trouvée")
            return True, {
                "type": "success",
                "reason": reason,
                "conclusion": conclusion,
                "mode": "execution",
                "warning": "Exécution non trouvée, validation ignorée"
            }
        
        checklist = execution.get("workflow_checklist", {})
        steps = checklist.get("steps", [])
        
        if not steps:
            # Pas de checklist → on autorise
            logger.info("[TERMINATE_VALIDATOR] ✅ Pas de checklist, TERMINATE autorisé")
            return True, {
                "type": "success",
                "reason": reason,
                "conclusion": conclusion,
                "mode": "execution",
                "checklist": "none"
            }
        
        # Analyser les étapes
        incomplete_steps = []
        completed_count = 0
        
        for step in steps:
            step_id = step.get("id", "?")
            step_name = step.get("name", "Étape")
            step_status = step.get("status", "pending")
            
            if step_status == "completed":
                completed_count += 1
            else:
                incomplete_steps.append({
                    "id": step_id,
                    "name": step_name,
                    "status": step_status
                })
        
        total_steps = len(steps)
        
        if incomplete_steps:
            # ❌ Des étapes ne sont pas "completed" → REFUSER
            logger.warning(
                f"[TERMINATE_VALIDATOR] ❌ TERMINATE refusé: "
                f"{len(incomplete_steps)}/{total_steps} étapes non complétées"
            )
            
            # Construire le message d'erreur détaillé
            incomplete_list = "\n".join([
                f"   - `{s['id']}` ({s['name']}) : status = `{s['status']}`"
                for s in incomplete_steps
            ])
            
            error_message = f"""❌ **TERMINATE_TASK REFUSÉ**

**Raison :** Certaines étapes de la checklist ne sont pas au statut "completed".

**Progression actuelle :** {completed_count}/{total_steps} étapes terminées

**Étapes à compléter :**
{incomplete_list}

---

**Actions requises :**

1. **Si les étapes sont terminées mais non mises à jour :**
   → Utilisez `CRUD_STEP` avec `action: "update"` pour les marquer "completed"
   
   ```json
   {{
       "action": "update",
       "step_id": "STEP_ID",
       "status": "completed",
       "message": "Description du résultat"
   }}
   ```

2. **Si les étapes sont en erreur :**
   → Corrigez l'erreur ou marquez-les en "completed" avec un message explicatif

3. **Si les étapes ne sont plus nécessaires :**
   → Utilisez `CRUD_STEP` avec `action: "delete"` (seulement pour les étapes "pending")
   
   ```json
   {{
       "action": "delete",
       "step_id": "STEP_ID",
       "reason": "Raison de la suppression"
   }}
   ```

4. **Ensuite, rappelez `TERMINATE_TASK`.**

---

⚠️ **Rappel :** En mode exécution de tâche planifiée, TOUTES les étapes 
doivent être "completed" avant de pouvoir terminer le workflow.
"""
            
            return False, {
                "type": "error",
                "message": error_message,
                "incomplete_steps": incomplete_steps,
                "completed": completed_count,
                "total": total_steps,
                "_terminate_blocked": True  # Flag pour la boucle agentic
            }
        
        # ✅ Toutes les étapes sont "completed" → AUTORISER
        logger.info(
            f"[TERMINATE_VALIDATOR] ✅ TERMINATE autorisé: "
            f"{completed_count}/{total_steps} étapes terminées"
        )
        
        return True, {
            "type": "success",
            "reason": reason,
            "conclusion": conclusion,
            "mode": "execution",
            "checklist_status": f"{completed_count}/{total_steps} completed",
            "all_steps_completed": True
        }
        
    except Exception as e:
        logger.error(f"[TERMINATE_VALIDATOR] ❌ Erreur validation: {e}", exc_info=True)
        # En cas d'erreur, on autorise (fail-safe)
        return True, {
            "type": "success",
            "reason": reason,
            "conclusion": conclusion,
            "mode": "execution",
            "warning": f"Erreur validation: {e}"
        }


def get_checklist_summary(brain) -> Optional[str]:
    """
    Retourne un résumé de la checklist pour le text wrapper.
    
    Args:
        brain: Instance PinnokioBrain
        
    Returns:
        str: Résumé (ex: "3/5 terminées") ou None si pas de checklist
    """
    if not brain or not brain.active_task_data:
        return None
    
    try:
        from ...firebase_providers import get_firebase_management
        fbm = get_firebase_management()
        
        task_id = brain.active_task_data.get("task_id")
        execution_id = brain.active_task_data.get("execution_id")
        mandate_path = brain.active_task_data.get("mandate_path")
        
        if not all([task_id, execution_id, mandate_path]):
            return None
        
        execution = fbm.get_task_execution(mandate_path, task_id, execution_id)
        if not execution:
            return None
        
        checklist = execution.get("workflow_checklist", {})
        steps = checklist.get("steps", [])
        
        if not steps:
            return None
        
        completed = sum(1 for s in steps if s.get("status") == "completed")
        total = len(steps)
        
        return f"{completed}/{total} terminées"
        
    except Exception as e:
        logger.warning(f"[TERMINATE_VALIDATOR] Erreur get_checklist_summary: {e}")
        return None


def get_workflow_title(brain) -> Optional[str]:
    """
    Retourne le titre de la tâche en cours pour le text wrapper.
    
    Args:
        brain: Instance PinnokioBrain
        
    Returns:
        str: Titre de la tâche ou None
    """
    if not brain or not brain.active_task_data:
        return None
    
    try:
        from ...firebase_providers import get_firebase_management
        fbm = get_firebase_management()
        
        task_id = brain.active_task_data.get("task_id")
        mandate_path = brain.active_task_data.get("mandate_path")
        
        if not task_id or not mandate_path:
            return None
        
        task = fbm.get_task(mandate_path, task_id)
        if not task:
            return None
        
        mission = task.get("mission", {})
        return mission.get("title")
        
    except Exception as e:
        logger.warning(f"[TERMINATE_VALIDATOR] Erreur get_workflow_title: {e}")
        return None

