"""
Task Tracker - Gestion et suivi des tâches SPT/LPT
Sauvegarde dans Firebase pour visibilité UI et tracking
"""

import logging
import uuid
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("pinnokio.task_tracker")


class TaskTracker:
    """
    Gestionnaire de suivi des tâches SPT et LPT
    
    Responsabilités:
    - Créer et enregistrer les tâches
    - Sauvegarder dans Firebase (visible UI)
    - Tracker l'état et la progression
    - Gérer les callbacks LPT
    """
    
    def __init__(self, user_id: str, collection_name: str):
        """
        Initialise le tracker
        
        Args:
            user_id: ID utilisateur Firebase
            collection_name: Nom de la collection (société)
        """
        self.user_id = user_id
        self.collection_name = collection_name
        self.tasks: Dict[str, Dict] = {}  # Cache local des tâches
    
    def create_lpt_task(self,
                       thread_key: str,
                       agent_type: str,
                       action: str,
                       params: Dict,
                       task_title: str) -> str:
        """
        Crée une tâche LPT et l'enregistre dans Firebase
        
        Args:
            thread_key: Clé du thread de conversation
            agent_type: Type d'agent (file_manager, accounting, etc.)
            action: Action à effectuer
            params: Paramètres de l'action
            task_title: Titre descriptif
            
        Returns:
            str: ID de la tâche créée
        """
        task_id = f"lpt_{uuid.uuid4().hex[:12]}"
        
        task_data = {
            "task_id": task_id,
            "type": "LPT",
            "agent_type": agent_type,
            "action": action,
            "params": params,
            "task_title": task_title,
            "thread_key": thread_key,
            "user_id": self.user_id,
            "collection_name": self.collection_name,
            "status": "queued",
            "progress": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "estimated_duration": self._estimate_duration(agent_type, action)
            }
        }
        
        # Sauvegarder dans Firebase
        self._save_to_firebase(task_id, task_data)
        
        # Envoyer la requête HTTP à l'agent externe
        self._send_lpt_request(task_id, task_data)
        
        # Cache local
        self.tasks[task_id] = task_data
        
        logger.info(f"Tâche LPT créée: {task_id}, agent={agent_type}, action={action}")
        
        return task_id
    
    def update_task_progress(self,
                            task_id: str,
                            status: str,
                            progress: int = None,
                            current_step: str = None,
                            result_data: Dict = None):
        """
        Met à jour la progression d'une tâche
        
        Args:
            task_id: ID de la tâche
            status: Nouveau statut (queued, processing, completed, failed)
            progress: Progression en % (0-100)
            current_step: Étape actuelle
            result_data: Données de résultat (si terminé)
        """
        if task_id not in self.tasks:
            logger.warning(f"Tâche {task_id} non trouvée dans le cache")
            return
        
        task_data = self.tasks[task_id]
        task_data["status"] = status
        task_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        if progress is not None:
            task_data["progress"] = progress
        
        if current_step:
            task_data["current_step"] = current_step
        
        if result_data:
            task_data["result"] = result_data
            if status == "completed":
                task_data["completed_at"] = datetime.now(timezone.utc).isoformat()
        
        # Mettre à jour dans Firebase
        self._update_firebase(task_id, task_data)
        
        logger.info(f"Tâche {task_id} mise à jour: status={status}, progress={progress}")
    
    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """Récupère le statut d'une tâche"""
        return self.tasks.get(task_id)
    
    def get_active_tasks(self, thread_key: str) -> List[str]:
        """Récupère les tâches actives pour un thread"""
        return [
            task_id for task_id, task in self.tasks.items()
            if task["thread_key"] == thread_key and task["status"] in ["queued", "processing"]
        ]
    
    def _save_to_firebase(self, task_id: str, task_data: Dict):
        """
        Sauvegarde la tâche dans Firebase
        Path: {collection_name}/tasks/{thread_key}/lpt_tasks/{task_id}
        """
        try:
            from ...firebase_providers import get_firebase_realtime
            rtdb = get_firebase_realtime()
            
            thread_key = task_data["thread_key"]
            path = f"{self.collection_name}/tasks/{thread_key}/lpt_tasks/{task_id}"
            
            rtdb.set_data(path, task_data)
            
            logger.debug(f"Tâche {task_id} sauvegardée dans Firebase: {path}")
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde Firebase tâche {task_id}: {e}")
    
    def _update_firebase(self, task_id: str, task_data: Dict):
        """Met à jour la tâche dans Firebase"""
        try:
            from ...firebase_providers import get_firebase_realtime
            rtdb = get_firebase_realtime()
            
            thread_key = task_data["thread_key"]
            path = f"{self.collection_name}/tasks/{thread_key}/lpt_tasks/{task_id}"
            
            rtdb.update_data(path, task_data)
            
            logger.debug(f"Tâche {task_id} mise à jour dans Firebase")
            
        except Exception as e:
            logger.error(f"Erreur mise à jour Firebase tâche {task_id}: {e}")
    
    def _send_lpt_request(self, task_id: str, task_data: Dict):
        """
        Envoie la requête HTTP à l'agent externe
        
        Cette méthode construit et envoie une requête HTTP POST vers le conteneur
        de l'agent spécialisé avec les métadonnées nécessaires.
        """
        import requests
        import os
        
        agent_type = task_data["agent_type"]
        
        # URL des agents externes (à configurer via env vars)
        agent_urls = {
            "file_manager": os.getenv("FILE_MANAGER_AGENT_URL", "http://file-manager-agent:8001"),
            "accounting": os.getenv("ACCOUNTING_AGENT_URL", "http://accounting-agent:8002")
        }
        
        if agent_type not in agent_urls:
            logger.error(f"Agent type inconnu: {agent_type}")
            return
        
        agent_url = agent_urls[agent_type]
        callback_url = os.getenv("MICROSERVICE_URL", "http://localhost:8000") + f"/api/v1/lpt/callback"
        
        payload = {
            "task_id": task_id,
            "action": task_data["action"],
            "params": task_data["params"],
            "metadata": {
                "user_id": self.user_id,
                "collection_name": self.collection_name,
                "thread_key": task_data["thread_key"],
                "task_title": task_data["task_title"],
                "created_at": task_data["created_at"]
            },
            "callback_url": callback_url
        }
        
        try:
            # Envoi asynchrone (pas d'attente de réponse)
            response = requests.post(
                f"{agent_url}/execute",
                json=payload,
                timeout=5  # Timeout court car on n'attend pas le résultat
            )
            
            if response.status_code in [200, 202]:
                logger.info(f"Requête LPT envoyée avec succès à {agent_type}: {task_id}")
            else:
                logger.error(f"Erreur envoi LPT à {agent_type}: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Erreur réseau lors de l'envoi LPT: {e}")
            # Mettre la tâche en erreur
            self.update_task_progress(task_id, "failed", 0, "network_error")
    
    def _estimate_duration(self, agent_type: str, action: str) -> str:
        """Estime la durée d'une tâche LPT"""
        estimates = {
            "file_manager": {
                "search_document": "30-60 seconds",
                "analyze_document": "1-2 minutes",
                "search_and_analyze_document": "2-3 minutes",
                "batch_process": "5-10 minutes"
            },
            "accounting": {
                "invoice_entry": "30 seconds",
                "batch_invoice_entry": "5-10 minutes",
                "bank_reconciliation": "2-5 minutes",
                "generate_entries": "1-3 minutes"
            }
        }
        
        return estimates.get(agent_type, {}).get(action, "Durée variable")

