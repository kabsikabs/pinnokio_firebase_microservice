"""
CRUD_STEP - Outil pour créer, modifier et supprimer des étapes de la checklist workflow.

Cet outil permet à l'agent de :
- CREATE : Ajouter une nouvelle étape à la checklist
- UPDATE : Modifier une étape existante (statut, nom, message)
- DELETE : Supprimer une étape de la checklist

Cas d'usage :
1. L'utilisateur demande une modification du plan → l'agent ajoute/supprime des étapes
2. Une étape doit être divisée en sous-étapes → l'agent crée de nouvelles étapes
3. Une étape devient inutile → l'agent la supprime
4. Le résultat d'un LPT nécessite un ajustement du plan → l'agent modifie les étapes
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import json

logger = logging.getLogger("pinnokio.crud_step")


def get_crud_step_tool_definition() -> Dict:
    """Retourne la définition de l'outil CRUD_STEP."""
    return {
        "name": "CRUD_STEP",
        "description": """📝 **Gérer les étapes de la checklist workflow (Create/Update/Delete)**

**QUAND UTILISER CET OUTIL :**

1. **CREATE** - Ajouter une nouvelle étape :
   - L'utilisateur demande une modification du plan
   - Un résultat LPT nécessite des actions supplémentaires
   - Vous devez diviser une étape complexe en sous-étapes

2. **UPDATE** - Modifier une étape existante :
   - Changer le statut (pending → in_progress → completed/error)
   - Modifier le nom ou la description
   - Ajouter un message de résultat

3. **DELETE** - Supprimer une étape :
   - L'étape devient inutile suite à un changement de plan
   - L'utilisateur demande explicitement de retirer une étape
   - Un résultat LPT rend l'étape obsolète

**⚠️ IMPORTANT :**
- Vous pouvez SEULEMENT supprimer des étapes dont le statut est "pending"
- Vous NE POUVEZ PAS supprimer une étape "in_progress" ou "completed"
- Pour ajouter une étape après une autre, utilisez `insert_after`

**EXEMPLES :**

**Créer une étape :**
```json
{
    "action": "create",
    "step_id": "STEP_4_VERIFICATION",
    "step_name": "Vérification des résultats",
    "insert_after": "STEP_3_TRAITEMENT"
}
```

**Mettre à jour le statut :**
```json
{
    "action": "update",
    "step_id": "STEP_2_SAISIE",
    "status": "completed",
    "message": "50 factures saisies avec succès"
}
```

**Supprimer une étape :**
```json
{
    "action": "delete",
    "step_id": "STEP_5_OPTIONNEL",
    "reason": "Étape non nécessaire car déjà traité par LPT"
}
```

**Note :** Cet outil remplace UPDATE_STEP avec plus de fonctionnalités.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "delete"],
                    "description": "Action à effectuer sur la checklist"
                },
                "step_id": {
                    "type": "string",
                    "description": "ID de l'étape (ex: 'STEP_3_VERIFICATION')"
                },
                "step_name": {
                    "type": "string",
                    "description": "Nom de l'étape (requis pour CREATE)"
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "error"],
                    "description": "Nouveau statut (pour UPDATE)"
                },
                "message": {
                    "type": "string",
                    "description": "Message descriptif (pour UPDATE)"
                },
                "insert_after": {
                    "type": "string",
                    "description": "ID de l'étape après laquelle insérer (pour CREATE). Si omis, ajoute à la fin."
                },
                "reason": {
                    "type": "string",
                    "description": "Raison de la suppression (pour DELETE)"
                }
            },
            "required": ["action", "step_id"]
        }
    }


class CRUDStepTool:
    """
    Gestionnaire des opérations CRUD sur les étapes de checklist.
    """
    
    def __init__(self, brain=None):
        """
        Initialise le gestionnaire CRUD_STEP.
        
        Args:
            brain: Instance PinnokioBrain pour accès aux données de tâche
        """
        self.brain = brain
    
    async def execute(
        self,
        action: str,
        step_id: str,
        step_name: str = None,
        status: str = None,
        message: str = None,
        insert_after: str = None,
        reason: str = None
    ) -> Dict[str, Any]:
        """
        Exécute l'action CRUD sur la checklist.
        
        Args:
            action: "create", "update", ou "delete"
            step_id: ID de l'étape
            step_name: Nom de l'étape (pour create)
            status: Nouveau statut (pour update)
            message: Message descriptif (pour update)
            insert_after: ID après lequel insérer (pour create)
            reason: Raison de suppression (pour delete)
            
        Returns:
            Dict avec le résultat de l'opération
        """
        try:
            # Valider mode tâche
            if not self.brain or not self.brain.active_task_data:
                return {
                    "type": "error",
                    "message": "CRUD_STEP disponible uniquement en mode exécution de tâche"
                }
            
            task_id = self.brain.active_task_data["task_id"]
            execution_id = self.brain.active_task_data["execution_id"]
            mandate_path = self.brain.active_task_data["mandate_path"]
            thread_key = self.brain.active_thread_key
            
            # Récupérer l'exécution actuelle
            from ...firebase_providers import get_firebase_management
            fbm = get_firebase_management()
            
            execution = fbm.get_task_execution(mandate_path, task_id, execution_id)
            if not execution:
                return {"type": "error", "message": "Exécution non trouvée"}
            
            checklist = execution.get("workflow_checklist", {})
            steps = checklist.get("steps", [])
            
            # Dispatcher selon l'action
            if action == "create":
                result = await self._create_step(
                    steps, step_id, step_name, insert_after
                )
            elif action == "update":
                result = await self._update_step(
                    steps, step_id, status, message
                )
            elif action == "delete":
                result = await self._delete_step(
                    steps, step_id, reason
                )
            else:
                return {"type": "error", "message": f"Action inconnue: {action}"}
            
            if result.get("type") == "error":
                return result
            
            # Mettre à jour le total
            checklist["total_steps"] = len(steps)
            checklist["steps"] = steps
            
            # Sauvegarder dans Firebase
            fbm.update_task_execution(
                mandate_path, task_id, execution_id,
                {"workflow_checklist": checklist}
            )
            
            # Broadcaster via WebSocket + sauvegarder RTDB
            await self._broadcast_checklist_update(
                action, step_id, steps, thread_key, result
            )
            
            logger.info(f"[CRUD_STEP] ✅ {action.upper()} sur {step_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"[CRUD_STEP] ❌ Erreur: {e}", exc_info=True)
            return {"type": "error", "message": str(e)}
    
    async def _create_step(
        self,
        steps: List[Dict],
        step_id: str,
        step_name: str,
        insert_after: str = None
    ) -> Dict[str, Any]:
        """Crée une nouvelle étape dans la checklist."""
        
        # Valider que step_name est fourni
        if not step_name:
            return {
                "type": "error",
                "message": "step_name est requis pour CREATE"
            }
        
        # Vérifier que l'ID n'existe pas déjà
        for step in steps:
            if step["id"] == step_id:
                return {
                    "type": "error",
                    "message": f"L'étape {step_id} existe déjà"
                }
        
        # Créer la nouvelle étape
        new_step = {
            "id": step_id,
            "name": step_name,
            "status": "pending",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": ""
        }
        
        # Insérer à la bonne position
        if insert_after:
            # Trouver l'index de l'étape après laquelle insérer
            insert_index = None
            for i, step in enumerate(steps):
                if step["id"] == insert_after:
                    insert_index = i + 1
                    break
            
            if insert_index is None:
                return {
                    "type": "error",
                    "message": f"Étape de référence {insert_after} non trouvée"
                }
            
            steps.insert(insert_index, new_step)
        else:
            # Ajouter à la fin
            steps.append(new_step)
        
        return {
            "type": "success",
            "action": "create",
            "step_id": step_id,
            "message": f"Étape '{step_name}' créée avec succès",
            "total_steps": len(steps),
            "new_step": new_step
        }
    
    async def _update_step(
        self,
        steps: List[Dict],
        step_id: str,
        status: str = None,
        message: str = None
    ) -> Dict[str, Any]:
        """Met à jour une étape existante."""
        
        # Trouver l'étape
        step_found = None
        for step in steps:
            if step["id"] == step_id:
                step_found = step
                break
        
        if not step_found:
            return {
                "type": "error",
                "message": f"Étape {step_id} non trouvée"
            }
        
        # Mettre à jour les champs fournis
        if status:
            step_found["status"] = status
        if message is not None:  # Permettre message vide
            step_found["message"] = message
        
        step_found["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        return {
            "type": "success",
            "action": "update",
            "step_id": step_id,
            "message": f"Étape {step_id} mise à jour",
            "status": step_found.get("status"),
            "updated_step": step_found
        }
    
    async def _delete_step(
        self,
        steps: List[Dict],
        step_id: str,
        reason: str = None
    ) -> Dict[str, Any]:
        """Supprime une étape de la checklist."""
        
        # Trouver l'étape
        step_index = None
        step_to_delete = None
        for i, step in enumerate(steps):
            if step["id"] == step_id:
                step_index = i
                step_to_delete = step
                break
        
        if step_index is None:
            return {
                "type": "error",
                "message": f"Étape {step_id} non trouvée"
            }
        
        # Vérifier que l'étape est "pending"
        if step_to_delete.get("status") != "pending":
            return {
                "type": "error",
                "message": f"Impossible de supprimer l'étape {step_id} "
                          f"(statut={step_to_delete.get('status')}). "
                          f"Seules les étapes 'pending' peuvent être supprimées."
            }
        
        # Supprimer l'étape
        deleted_step = steps.pop(step_index)
        
        return {
            "type": "success",
            "action": "delete",
            "step_id": step_id,
            "message": f"Étape {step_id} supprimée" + (f" - Raison: {reason}" if reason else ""),
            "deleted_step": deleted_step,
            "total_steps": len(steps)
        }
    
    async def _broadcast_checklist_update(
        self,
        action: str,
        step_id: str,
        steps: List[Dict],
        thread_key: str,
        result: Dict[str, Any]
    ):
        """Broadcast la mise à jour via WebSocket et sauvegarde dans RTDB."""
        try:
            from ...ws_hub import hub
            from ...firebase_providers import get_firebase_realtime
            import uuid
            
            message_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            
            # Construire la commande selon l'action
            if action == "create":
                cmmd_action = "ADD_STEP"
                params = {
                    "step": result.get("new_step"),
                    "total_steps": result.get("total_steps")
                }
            elif action == "update":
                cmmd_action = "UPDATE_STEP_STATUS"
                params = {
                    "step_id": step_id,
                    "status": result.get("status"),
                    "timestamp": timestamp,
                    "message": result.get("updated_step", {}).get("message", "")
                }
            elif action == "delete":
                cmmd_action = "DELETE_STEP"
                params = {
                    "step_id": step_id,
                    "total_steps": result.get("total_steps")
                }
            else:
                return
            
            command = {
                "action": cmmd_action,
                "params": params
            }
            
            # WebSocket
            ws_message = {
                "type": f"WORKFLOW_{cmmd_action}",
                "thread_key": thread_key,
                "timestamp": timestamp,
                "message_id": message_id,
                "content": json.dumps({"message": {"cmmd": command}})
            }
            
            ws_channel = f"chat:{self.brain.firebase_user_id}:{self.brain.collection_name}:{thread_key}"
            
            # Broadcast conditionnel selon le mode
            current_mode = getattr(self.brain, "_current_mode", "UI")
            if current_mode == "UI":
                await hub.broadcast(self.brain.firebase_user_id, {
                    "type": f"WORKFLOW_{cmmd_action}",
                    "channel": ws_channel,
                    "payload": ws_message
                })
                logger.info(f"[CRUD_STEP] 📡 Mise à jour broadcastée (mode={current_mode})")
            
            # Sauvegarde RTDB
            rtdb = get_firebase_realtime()
            thread_path = f"{self.brain.collection_name}/chats/{thread_key}"
            
            message_data = {
                'content': json.dumps({'message': {'cmmd': command}}),
                'sender_id': self.brain.firebase_user_id,
                'timestamp': timestamp,
                'message_type': 'CMMD',
                'read': False,
                'role': 'assistant'
            }
            
            messages_ref = rtdb.db.child(f'{thread_path}/messages')
            messages_ref.push(message_data)
            
        except Exception as e:
            logger.warning(f"[CRUD_STEP] ⚠️ Erreur broadcast: {e}")


def create_crud_step_tool(brain=None) -> tuple:
    """
    Factory pour créer l'outil CRUD_STEP.
    
    Returns:
        Tuple (definition, tool_instance)
    """
    tool = CRUDStepTool(brain=brain)
    return (get_crud_step_tool_definition(), tool)

