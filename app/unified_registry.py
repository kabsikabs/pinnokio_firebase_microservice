"""
Service de registre unifié pour la gestion centralisée des utilisateurs, sociétés et tâches.
Compatible avec l'infrastructure existante, fonctionne en parallèle sans impact.
"""

import json
import time
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from .redis_client import get_redis
from .firebase_client import get_firestore

class UnifiedRegistryService:
    """Service de gestion du registre unifié pour utilisateurs, sociétés et tâches."""
    
    def __init__(self):
        self.redis = get_redis()
        self.db = get_firestore()
        
    # ========== Gestion des utilisateurs ==========
    
    def register_user_session(
        self, 
        user_id: str, 
        session_id: str, 
        company_id: str,
        authorized_companies: List[str],
        backend_route: str = None
    ) -> dict:
        """Enregistre une session utilisateur complète avec société."""
        
        try:
            registry_data = {
                "user_info": {
                    "user_id": user_id,
                    "session_id": session_id,
                    "backend_route": backend_route or "",
                    "last_seen_at": datetime.now(timezone.utc).isoformat(),
                    "status": "online"
                },
                "companies": {
                    "current_company_id": company_id,
                    "authorized_companies_ids": authorized_companies,
                    "company_roles": self._get_user_company_roles(user_id, authorized_companies)
                },
                "services": {
                    "chroma": {"collections": [], "last_heartbeat": None},
                    "llm": {"active_conversations": [], "model_preferences": {}},
                    "tasks": {"active_tasks": [], "task_history": []}
                },
                "heartbeat": {
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "ttl_seconds": 90
                }
            }
            
            # Enregistrer dans Redis
            key = f"registry:unified:{user_id}"
            self.redis.hset(key, mapping={
                "data": json.dumps(registry_data),
                "last_update": str(time.time())
            })
            self.redis.expire(key, 24 * 3600)  # TTL 24h
            
            # Enregistrer dans le registre société
            self._register_user_to_company(user_id, company_id, session_id)
            
            # Maintenir la compatibilité avec Firestore
            self._sync_to_firestore_registry(user_id, registry_data)
            
            return registry_data
            
        except Exception as e:
            print(f"❌ Erreur register_user_session pour {user_id}: {e}")
            raise
    
    def update_user_heartbeat(self, user_id: str) -> bool:
        """Met à jour le heartbeat utilisateur."""
        try:
            key = f"registry:unified:{user_id}"
            if not self.redis.exists(key):
                return False
                
            # Récupérer les données actuelles
            data_json = self.redis.hget(key, "data")
            if not data_json:
                return False
                
            registry_data = json.loads(data_json)
            
            # Mettre à jour le heartbeat
            now = datetime.now(timezone.utc).isoformat()
            registry_data["user_info"]["last_seen_at"] = now
            registry_data["heartbeat"]["last_heartbeat"] = now
            
            # Sauvegarder
            self.redis.hset(key, mapping={
                "data": json.dumps(registry_data),
                "last_update": str(time.time())
            })
            self.redis.expire(key, 24 * 3600)
            
            return True
        except Exception as e:
            print(f"❌ Erreur heartbeat utilisateur {user_id}: {e}")
            return False
    
    def unregister_user_session(self, session_id: str) -> bool:
        """Désenregistre une session utilisateur."""
        try:
            # Trouver l'utilisateur par session_id
            pattern = "registry:unified:*"
            cursor = 0
            removed = False
            
            while True:
                cursor, keys = self.redis.scan(cursor=cursor, match=pattern, count=200)
                for key in keys:
                    try:
                        data_json = self.redis.hget(key, "data")
                        if data_json:
                            registry_data = json.loads(data_json)
                            if registry_data.get("user_info", {}).get("session_id") == session_id:
                                user_id = registry_data["user_info"]["user_id"]
                                company_id = registry_data["companies"]["current_company_id"]
                                
                                # Supprimer de Redis
                                self.redis.delete(key)
                                
                                # Supprimer du registre société
                                self._unregister_user_from_company(user_id, company_id)
                                
                                removed = True
                                break
                    except Exception:
                        continue
                
                if cursor == 0 or removed:
                    break
            
            return removed
        except Exception as e:
            print(f"❌ Erreur unregister_user_session pour {session_id}: {e}")
            return False
    
    # ========== Gestion des services ==========
    
    def update_user_service(self, user_id: str, service_name: str, service_data: dict) -> bool:
        """Met à jour les données d'un service pour un utilisateur."""
        try:
            key = f"registry:unified:{user_id}"
            data_json = self.redis.hget(key, "data")
            
            if not data_json:
                return False
                
            registry_data = json.loads(data_json)
            
            # Mettre à jour les données du service
            if "services" not in registry_data:
                registry_data["services"] = {}
            
            if service_name not in registry_data["services"]:
                registry_data["services"][service_name] = {}
            
            registry_data["services"][service_name].update(service_data)
            
            # Sauvegarder
            self.redis.hset(key, "data", json.dumps(registry_data))
            
            return True
        except Exception as e:
            print(f"❌ Erreur update_user_service {user_id}/{service_name}: {e}")
            return False
    
    # ========== Gestion des tâches ==========
    
    def register_task(
        self, 
        task_id: str, 
        task_type: str, 
        user_id: str, 
        company_id: str,
        priority: str = "normal",
        max_duration: int = 3600
    ) -> dict:
        """Enregistre une nouvelle tâche avec isolation."""
        
        try:
            task_data = {
                "task_info": {
                    "task_id": task_id,
                    "task_type": task_type,
                    "user_id": user_id,
                    "company_id": company_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "queued"
                },
                "isolation": {
                    "namespace": f"{user_id}_{company_id}",
                    "priority": priority,
                    "max_duration": max_duration
                },
                "progress": {
                    "current_step": "queued",
                    "progress_percent": 0,
                    "estimated_completion": None
                },
                "resources": {
                    "worker_id": None,
                    "memory_usage": None,
                    "cpu_usage": None
                }
            }
            
            # Enregistrer la tâche
            task_key = f"registry:task:{task_id}"
            self.redis.hset(task_key, mapping={
                "data": json.dumps(task_data),
                "created_at": str(time.time())
            })
            self.redis.expire(task_key, max_duration + 300)  # TTL = durée max + buffer
            
            # Ajouter à la liste des tâches utilisateur
            self._add_task_to_user_registry(user_id, task_id)
            
            # Ajouter à la liste des tâches société
            self._add_task_to_company_registry(company_id, task_id)
            
            return task_data
        except Exception as e:
            print(f"❌ Erreur register_task {task_id}: {e}")
            raise
    
    def update_task_progress(
        self, 
        task_id: str, 
        status: str, 
        progress_percent: int = None,
        current_step: str = None,
        worker_id: str = None
    ) -> bool:
        """Met à jour la progression d'une tâche."""
        try:
            task_key = f"registry:task:{task_id}"
            data_json = self.redis.hget(task_key, "data")
            if not data_json:
                return False
                
            task_data = json.loads(data_json)
            
            # Mettre à jour les informations
            task_data["task_info"]["status"] = status
            if progress_percent is not None:
                task_data["progress"]["progress_percent"] = progress_percent
            if current_step:
                task_data["progress"]["current_step"] = current_step
            if worker_id:
                task_data["resources"]["worker_id"] = worker_id
            
            # Sauvegarder
            self.redis.hset(task_key, "data", json.dumps(task_data))
            
            return True
        except Exception as e:
            print(f"❌ Erreur mise à jour tâche {task_id}: {e}")
            return False
    
    # ========== Méthodes utilitaires ==========
    
    def get_user_registry(self, user_id: str) -> Optional[dict]:
        """Récupère le registre complet d'un utilisateur."""
        try:
            key = f"registry:unified:{user_id}"
            data_json = self.redis.hget(key, "data")
            return json.loads(data_json) if data_json else None
        except Exception:
            return None
    
    def get_task_registry(self, task_id: str) -> Optional[dict]:
        """Récupère le registre d'une tâche."""
        try:
            key = f"registry:task:{task_id}"
            data_json = self.redis.hget(key, "data")
            return json.loads(data_json) if data_json else None
        except Exception:
            return None
    
    def get_company_active_users(self, company_id: str) -> List[str]:
        """Récupère la liste des utilisateurs actifs d'une société."""
        try:
            key = f"registry:company:{company_id}"
            data_json = self.redis.hget(key, "data")
            if data_json:
                company_data = json.loads(data_json)
                return list(company_data.get("active_users", {}).keys())
            return []
        except Exception:
            return []
    
    def cleanup_expired_entries(self):
        """Nettoie les entrées expirées (tâche de maintenance)."""
        # Cette méthode sera appelée périodiquement par Celery Beat
        try:
            # Nettoyer les tâches expirées
            pattern = "registry:task:*"
            cursor = 0
            
            while True:
                cursor, keys = self.redis.scan(cursor=cursor, match=pattern, count=100)
                for key in keys:
                    try:
                        created_at = self.redis.hget(key, "created_at")
                        if created_at and (time.time() - float(created_at)) > 7200:  # 2h
                            self.redis.delete(key)
                    except Exception:
                        continue
                
                if cursor == 0:
                    break
        except Exception as e:
            print(f"❌ Erreur cleanup_expired_entries: {e}")
    
    # ========== Méthodes privées ==========
    
    def _get_user_company_roles(self, user_id: str, companies: List[str]) -> Dict[str, str]:
        """Récupère les rôles de l'utilisateur dans chaque société."""
        # Implémentation simple - à adapter selon votre logique métier
        return {company: "user" for company in companies}
    
    def _get_user_role_in_company(self, user_id: str, company_id: str) -> str:
        """Récupère le rôle d'un utilisateur dans une société."""
        # Implémentation simple - à adapter selon votre logique métier
        return "user"
    
    def _register_user_to_company(self, user_id: str, company_id: str, session_id: str):
        """Enregistre un utilisateur dans le registre d'une société."""
        try:
            company_key = f"registry:company:{company_id}"
            
            # Récupérer ou créer les données société
            company_data = self._get_or_create_company_registry(company_id)
            
            # Ajouter l'utilisateur
            company_data["active_users"][user_id] = {
                "session_id": session_id,
                "last_activity": datetime.now(timezone.utc).isoformat(),
                "role": self._get_user_role_in_company(user_id, company_id)
            }
            
            # Sauvegarder
            self.redis.hset(company_key, "data", json.dumps(company_data))
            self.redis.expire(company_key, 24 * 3600)
        except Exception as e:
            print(f"❌ Erreur _register_user_to_company {user_id}/{company_id}: {e}")
    
    def _unregister_user_from_company(self, user_id: str, company_id: str):
        """Désenregistre un utilisateur d'une société."""
        try:
            company_key = f"registry:company:{company_id}"
            data_json = self.redis.hget(company_key, "data")
            
            if data_json:
                company_data = json.loads(data_json)
                if user_id in company_data.get("active_users", {}):
                    del company_data["active_users"][user_id]
                    self.redis.hset(company_key, "data", json.dumps(company_data))
        except Exception as e:
            print(f"❌ Erreur _unregister_user_from_company {user_id}/{company_id}: {e}")
    
    def _get_or_create_company_registry(self, company_id: str) -> dict:
        """Récupère ou crée le registre d'une société."""
        try:
            key = f"registry:company:{company_id}"
            data_json = self.redis.hget(key, "data")
            
            if data_json:
                return json.loads(data_json)
            
            # Créer nouveau registre société
            return {
                "company_info": {
                    "company_id": company_id,
                    "created_at": datetime.now(timezone.utc).isoformat()
                },
                "active_users": {},
                "services": {
                    "chroma_collections": [],
                    "active_tasks": [],
                    "llm_conversations": []
                },
                "quotas": {
                    "max_tasks_per_user": 10,
                    "max_llm_requests_per_hour": 1000,
                    "storage_limit_mb": 5000
                }
            }
        except Exception as e:
            print(f"❌ Erreur _get_or_create_company_registry {company_id}: {e}")
            return {"company_info": {"company_id": company_id}, "active_users": {}, "services": {}, "quotas": {}}
    
    def _add_task_to_user_registry(self, user_id: str, task_id: str):
        """Ajoute une tâche au registre utilisateur."""
        try:
            user_data = self.get_user_registry(user_id)
            if user_data:
                if task_id not in user_data["services"]["tasks"]["active_tasks"]:
                    user_data["services"]["tasks"]["active_tasks"].append(task_id)
                key = f"registry:unified:{user_id}"
                self.redis.hset(key, "data", json.dumps(user_data))
        except Exception as e:
            print(f"❌ Erreur _add_task_to_user_registry {user_id}/{task_id}: {e}")
    
    def _add_task_to_company_registry(self, company_id: str, task_id: str):
        """Ajoute une tâche au registre société."""
        try:
            company_data = self._get_or_create_company_registry(company_id)
            if task_id not in company_data["services"]["active_tasks"]:
                company_data["services"]["active_tasks"].append(task_id)
            key = f"registry:company:{company_id}"
            self.redis.hset(key, "data", json.dumps(company_data))
        except Exception as e:
            print(f"❌ Erreur _add_task_to_company_registry {company_id}/{task_id}: {e}")
    
    def _sync_to_firestore_registry(self, user_id: str, registry_data: dict):
        """Synchronise avec le registre Firestore existant pour compatibilité."""
        try:
            doc_ref = self.db.collection("listeners_registry").document(user_id)
            firestore_data = {
                "status": registry_data["user_info"]["status"],
                "heartbeat_at": registry_data["heartbeat"]["last_heartbeat"],
                "ttl_seconds": registry_data["heartbeat"]["ttl_seconds"],
                "authorized_companies_ids": registry_data["companies"]["authorized_companies_ids"]
            }
            doc_ref.set(firestore_data, merge=True)
        except Exception as e:
            print(f"⚠️ Erreur sync Firestore pour {user_id}: {e}")


# Singleton pour le service de registre
_unified_registry_service: Optional[UnifiedRegistryService] = None

def get_unified_registry() -> UnifiedRegistryService:
    """Récupère l'instance singleton du service de registre unifié."""
    global _unified_registry_service
    if _unified_registry_service is None:
        _unified_registry_service = UnifiedRegistryService()
    return _unified_registry_service

