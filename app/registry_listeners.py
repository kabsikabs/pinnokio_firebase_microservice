"""
Service de registre centralisé pour les listeners actifs.
Permet la traçabilité et la détection des écoutes zombies.

Collection Firestore : listeners_active/{uid}/listeners/{listener_id}

Exemple de document :
{
    "listener_type": "chat",
    "space_code": "space1",
    "thread_key": "thread1",
    "mode": "job_chats",
    "created_at": "2025-10-03T10:30:00Z",
    "last_heartbeat": "2025-10-03T10:35:00Z",
    "status": "active",
    "channel_name": "chat:user123:space1:thread1",
    "ttl_seconds": 90
}
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from google.cloud import firestore
from .firebase_client import get_firestore


class RegistryListeners:
    """Gestion du registre centralisé des listeners actifs."""
    
    def __init__(self):
        self.db = get_firestore()
        self.logger = logging.getLogger("registry.listeners")
        self.ttl_seconds = 90  # TTL par défaut
    
    def _generate_listener_id(
        self, 
        user_id: str, 
        listener_type: str,
        space_code: str = None,
        thread_key: str = None
    ) -> str:
        """Génère un ID unique pour un listener."""
        if listener_type == "chat":
            if not space_code or not thread_key:
                raise ValueError("space_code et thread_key sont requis pour le type chat")
            return f"chat_{user_id}_{space_code}_{thread_key}"
        elif listener_type == "notif":
            return f"notif_{user_id}"
        elif listener_type == "msg":
            return f"msg_{user_id}"
        elif listener_type == "workflow":
            return f"workflow_{user_id}"
        else:
            return f"{listener_type}_{user_id}"
    
    def _generate_channel_name(
        self,
        user_id: str,
        listener_type: str,
        space_code: str = None,
        thread_key: str = None
    ) -> str:
        """Génère le nom du canal Redis correspondant."""
        if listener_type == "chat":
            return f"chat:{user_id}:{space_code}:{thread_key}"
        else:
            return f"user:{user_id}"
    
    def check_listener_status(
        self,
        user_id: str,
        listener_type: str,
        space_code: str = None,
        thread_key: str = None
    ) -> dict:
        """Vérifie si un listener est actif.
        
        Args:
            user_id: ID de l'utilisateur
            listener_type: Type de listener ("chat", "notif", "msg", "workflow")
            space_code: Code de l'espace (requis pour "chat")
            thread_key: Clé du thread (requis pour "chat")
        
        Returns:
            dict avec keys: success, active, listener_id, status, created_at, 
                           last_heartbeat, channel_name, details
        """
        try:
            listener_id = self._generate_listener_id(user_id, listener_type, space_code, thread_key)
            doc_ref = (
                self.db.collection("listeners_active")
                .document(user_id)
                .collection("listeners")
                .document(listener_id)
            )
            doc = doc_ref.get()
            
            if not doc.exists:
                return {
                    "success": True,
                    "active": False,
                    "listener_id": listener_id,
                    "status": "not_found"
                }
            
            data = doc.to_dict()
            
            # Vérifier la fraîcheur du heartbeat
            last_heartbeat = data.get("last_heartbeat")
            if last_heartbeat:
                if isinstance(last_heartbeat, str):
                    last_heartbeat_dt = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                elif hasattr(last_heartbeat, "to_datetime"):
                    last_heartbeat_dt = last_heartbeat.to_datetime()
                else:
                    last_heartbeat_dt = None
                
                if last_heartbeat_dt:
                    now = datetime.now(timezone.utc)
                    age = (now - last_heartbeat_dt).total_seconds()
                    ttl = data.get("ttl_seconds", self.ttl_seconds)
                    
                    if age > ttl:
                        status = "expired"
                        active = False
                    else:
                        status = data.get("status", "active")
                        active = status == "active"
                else:
                    status = "zombie"
                    active = False
            else:
                status = "zombie"
                active = False
            
            return {
                "success": True,
                "active": active,
                "listener_id": listener_id,
                "status": status,
                "created_at": data.get("created_at"),
                "last_heartbeat": data.get("last_heartbeat"),
                "channel_name": data.get("channel_name"),
                "details": {
                    "listener_type": data.get("listener_type"),
                    "space_code": data.get("space_code"),
                    "thread_key": data.get("thread_key"),
                    "mode": data.get("mode")
                }
            }
            
        except Exception as e:
            self.logger.error(f"check_listener_status_error uid={user_id} type={listener_type} error={e}")
            return {
                "success": False,
                "error": "INTERNAL_ERROR",
                "message": str(e)
            }
    
    def register_listener(
        self,
        user_id: str,
        listener_type: str,
        space_code: str = None,
        thread_key: str = None,
        mode: str = None
    ) -> dict:
        """Enregistre un listener dans le registre (traçabilité uniquement).
        
        Cette méthode n'impacte PAS le démarrage réel du listener.
        Elle sert uniquement à enregistrer son existence pour la traçabilité.
        
        Args:
            user_id: ID de l'utilisateur
            listener_type: Type de listener ("chat", "notif", "msg", "workflow")
            space_code: Code de l'espace (requis pour "chat")
            thread_key: Clé du thread (requis pour "chat")
            mode: Mode du listener (ex: "job_chats")
        
        Returns:
            dict avec keys: success, listener_id, channel_name, created_at, message
                           ou error si échec
        """
        try:
            # Validation
            if not user_id:
                return {
                    "success": False,
                    "error": "MISSING_REQUIRED_PARAM",
                    "message": "user_id est requis"
                }
            
            if listener_type not in ["chat", "notif", "msg", "workflow"]:
                return {
                    "success": False,
                    "error": "INVALID_LISTENER_TYPE",
                    "message": f"Type de listener invalide: {listener_type}"
                }
            
            if listener_type == "chat" and (not space_code or not thread_key):
                return {
                    "success": False,
                    "error": "MISSING_REQUIRED_PARAM",
                    "message": "space_code et thread_key sont requis pour le type chat"
                }
            
            # Vérifier si existe déjà
            status_check = self.check_listener_status(user_id, listener_type, space_code, thread_key)
            if status_check.get("active") and status_check.get("status") == "active":
                self.logger.warning(
                    f"listener_already_exists uid={user_id} type={listener_type} "
                    f"space={space_code} thread={thread_key}"
                )
                return {
                    "success": False,
                    "error": "LISTENER_ALREADY_EXISTS",
                    "message": "Un listener actif existe déjà pour ces paramètres",
                    "existing_listener": status_check
                }
            
            # Générer les identifiants
            listener_id = self._generate_listener_id(user_id, listener_type, space_code, thread_key)
            channel_name = self._generate_channel_name(user_id, listener_type, space_code, thread_key)
            
            now = datetime.now(timezone.utc).isoformat()
            
            # Enregistrer dans Firestore
            listener_data = {
                "listener_type": listener_type,
                "space_code": space_code,
                "thread_key": thread_key,
                "mode": mode,
                "created_at": now,
                "last_heartbeat": now,
                "status": "active",
                "channel_name": channel_name,
                "ttl_seconds": self.ttl_seconds
            }
            
            doc_ref = (
                self.db.collection("listeners_active")
                .document(user_id)
                .collection("listeners")
                .document(listener_id)
            )
            doc_ref.set(listener_data)
            
            self.logger.info(
                f"listener_register uid={user_id} type={listener_type} "
                f"listener_id={listener_id} channel={channel_name} "
                f"space={space_code} thread={thread_key} mode={mode}"
            )
            
            return {
                "success": True,
                "listener_id": listener_id,
                "channel_name": channel_name,
                "created_at": now,
                "message": "Listener enregistré avec succès"
            }
            
        except Exception as e:
            self.logger.error(
                f"register_listener_error uid={user_id} type={listener_type} "
                f"space={space_code} thread={thread_key} error={e}",
                exc_info=True
            )
            return {
                "success": False,
                "error": "INTERNAL_ERROR",
                "message": str(e)
            }
    
    def unregister_listener(
        self,
        user_id: str,
        listener_type: str,
        space_code: str = None,
        thread_key: str = None
    ) -> dict:
        """Désenregistre un listener du registre (traçabilité uniquement).
        
        Cette méthode n'impacte PAS l'arrêt réel du listener.
        Elle sert uniquement à nettoyer le registre.
        
        Args:
            user_id: ID de l'utilisateur
            listener_type: Type de listener
            space_code: Code de l'espace (requis pour "chat")
            thread_key: Clé du thread (requis pour "chat")
        
        Returns:
            dict avec keys: success, listener_id, message
        """
        try:
            listener_id = self._generate_listener_id(user_id, listener_type, space_code, thread_key)
            doc_ref = (
                self.db.collection("listeners_active")
                .document(user_id)
                .collection("listeners")
                .document(listener_id)
            )
            
            # Vérifier si existe
            doc = doc_ref.get()
            if not doc.exists:
                self.logger.info(
                    f"listener_unregister_skip uid={user_id} listener_id={listener_id} reason=not_found"
                )
                return {
                    "success": True,
                    "listener_id": listener_id,
                    "message": "Listener déjà absent du registre"
                }
            
            # Supprimer
            doc_ref.delete()
            
            self.logger.info(
                f"listener_unregister uid={user_id} type={listener_type} "
                f"listener_id={listener_id} space={space_code} thread={thread_key}"
            )
            
            return {
                "success": True,
                "listener_id": listener_id,
                "message": "Listener désenregistré avec succès"
            }
            
        except Exception as e:
            self.logger.error(
                f"unregister_listener_error uid={user_id} type={listener_type} "
                f"space={space_code} thread={thread_key} error={e}",
                exc_info=True
            )
            return {
                "success": False,
                "error": "INTERNAL_ERROR",
                "message": str(e)
            }
    
    def update_listener_heartbeat(
        self,
        user_id: str,
        listener_type: str,
        space_code: str = None,
        thread_key: str = None
    ) -> bool:
        """Met à jour le heartbeat d'un listener.
        
        Args:
            user_id: ID de l'utilisateur
            listener_type: Type de listener
            space_code: Code de l'espace (requis pour "chat")
            thread_key: Clé du thread (requis pour "chat")
        
        Returns:
            True si succès, False sinon
        """
        try:
            listener_id = self._generate_listener_id(user_id, listener_type, space_code, thread_key)
            doc_ref = (
                self.db.collection("listeners_active")
                .document(user_id)
                .collection("listeners")
                .document(listener_id)
            )
            
            doc_ref.update({
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "active"
            })
            
            self.logger.debug(f"listener_heartbeat uid={user_id} listener_id={listener_id}")
            
            return True
            
        except Exception as e:
            self.logger.error(
                f"update_heartbeat_error uid={user_id} listener_id={listener_id} error={e}"
            )
            return False
    
    def list_user_listeners(
        self,
        user_id: str,
        include_expired: bool = False
    ) -> dict:
        """Liste tous les listeners d'un utilisateur.
        
        Args:
            user_id: ID de l'utilisateur
            include_expired: Inclure les listeners expirés
        
        Returns:
            dict avec keys: success, user_id, listeners, total_count, 
                           active_count, expired_count
        """
        try:
            col_ref = (
                self.db.collection("listeners_active")
                .document(user_id)
                .collection("listeners")
            )
            docs = col_ref.stream()
            
            listeners = []
            active_count = 0
            expired_count = 0
            
            for doc in docs:
                data = doc.to_dict()
                
                # Vérifier le heartbeat
                last_heartbeat = data.get("last_heartbeat")
                status = data.get("status", "unknown")
                
                if last_heartbeat:
                    if isinstance(last_heartbeat, str):
                        last_heartbeat_dt = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                    elif hasattr(last_heartbeat, "to_datetime"):
                        last_heartbeat_dt = last_heartbeat.to_datetime()
                    else:
                        last_heartbeat_dt = None
                    
                    if last_heartbeat_dt:
                        now = datetime.now(timezone.utc)
                        age = (now - last_heartbeat_dt).total_seconds()
                        ttl = data.get("ttl_seconds", self.ttl_seconds)
                        
                        if age > ttl:
                            status = "expired"
                            data["status"] = "expired"
                            expired_count += 1
                        else:
                            active_count += 1
                    else:
                        status = "zombie"
                        data["status"] = "zombie"
                        expired_count += 1
                else:
                    status = "zombie"
                    data["status"] = "zombie"
                    expired_count += 1
                
                # Filtrer selon include_expired
                if not include_expired and status in ["expired", "zombie"]:
                    continue
                
                listeners.append({
                    "listener_id": doc.id,
                    **data
                })
            
            self.logger.info(
                f"list_listeners uid={user_id} total={len(listeners)} "
                f"active={active_count} expired={expired_count}"
            )
            
            return {
                "success": True,
                "user_id": user_id,
                "listeners": listeners,
                "total_count": len(listeners),
                "active_count": active_count,
                "expired_count": expired_count
            }
            
        except Exception as e:
            self.logger.error(f"list_listeners_error uid={user_id} error={e}", exc_info=True)
            return {
                "success": False,
                "error": "INTERNAL_ERROR",
                "message": str(e)
            }
    
    def cleanup_user_listeners(
        self,
        user_id: str,
        listener_types: List[str] = None
    ) -> dict:
        """Nettoie les listeners d'un utilisateur.
        
        Args:
            user_id: ID de l'utilisateur
            listener_types: Liste des types à nettoyer (None = tous)
        
        Returns:
            dict avec keys: success, cleaned_count, cleaned_listeners, message
        """
        try:
            col_ref = (
                self.db.collection("listeners_active")
                .document(user_id)
                .collection("listeners")
            )
            docs = col_ref.stream()
            
            cleaned_listeners = []
            cleaned_count = 0
            
            for doc in docs:
                data = doc.to_dict()
                listener_type = data.get("listener_type")
                
                # Filtrer par type si spécifié
                if listener_types and listener_type not in listener_types:
                    continue
                
                # Supprimer
                doc.reference.delete()
                cleaned_count += 1
                
                cleaned_listeners.append({
                    "listener_id": doc.id,
                    "listener_type": listener_type,
                    "status": data.get("status"),
                    "space_code": data.get("space_code"),
                    "thread_key": data.get("thread_key")
                })
                
                self.logger.info(
                    f"listener_cleanup uid={user_id} listener_id={doc.id} type={listener_type}"
                )
            
            return {
                "success": True,
                "cleaned_count": cleaned_count,
                "cleaned_listeners": cleaned_listeners,
                "message": f"{cleaned_count} listener(s) nettoyé(s) pour {user_id}"
            }
            
        except Exception as e:
            self.logger.error(f"cleanup_listeners_error uid={user_id} error={e}", exc_info=True)
            return {
                "success": False,
                "error": "INTERNAL_ERROR",
                "message": str(e)
            }


# Singleton
_registry_listeners: Optional[RegistryListeners] = None


def get_registry_listeners() -> RegistryListeners:
    """Récupère l'instance singleton du registre des listeners."""
    global _registry_listeners
    if _registry_listeners is None:
        _registry_listeners = RegistryListeners()
    return _registry_listeners

