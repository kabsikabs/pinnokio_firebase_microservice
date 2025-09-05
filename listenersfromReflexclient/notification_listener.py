"""
Listener pour les notifications Firebase.
Récupère les notifications non lues toutes les 30 secondes.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from firebase_admin import firestore

from .base_listener import BaseListener

class NotificationListener(BaseListener):
    """Listener pour les notifications Firebase."""
    
    def __init__(self, firebase_user_id: str, authorized_companies_ids: List[str], output_queue: Optional[asyncio.Queue] = None):
        super().__init__("notifications")
        self.firebase_user_id = firebase_user_id
        self.authorized_companies_ids = authorized_companies_ids
        self.db = firestore.client()
        self.output_queue: Optional[asyncio.Queue] = output_queue
        self._detach_listener = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def get_interval(self) -> int:
        """Intervalle spécifique pour les notifications : 30 secondes."""
        return 30
    
    async def _fetch_data(self) -> List[Dict]:
        """(Non utilisé en mode snapshot) Conserve pour compatibilité."""
        return []
    
    def _snapshot_to_notifications(self, snapshot) -> List[Dict]:
        notifications: List[Dict] = []
        try:
            for doc in snapshot:
                data = doc.to_dict() or {}
                data['doc_id'] = doc.id

                timestamp = data.get('timestamp')
                if timestamp:
                    if isinstance(timestamp, str):
                        try:
                            dt = datetime.fromisoformat(timestamp)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            data['timestamp'] = dt
                        except ValueError:
                            data['timestamp'] = datetime.min.replace(tzinfo=timezone.utc)
                    elif hasattr(timestamp, "to_datetime"):
                        dt = timestamp.to_datetime()
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        data['timestamp'] = dt
                    else:
                        data['timestamp'] = datetime.min.replace(tzinfo=timezone.utc)

                collection_id = data.get('collection_id')
                if self.authorized_companies_ids:
                    if collection_id and collection_id in self.authorized_companies_ids:
                        notifications.append(data)
                else:
                    notifications.append(data)

            notifications.sort(key=lambda x: x.get('timestamp') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        except Exception as e:
            self.logger.error(f"Erreur snapshot→notifications: {e}")
        return notifications
    
    def _format_payload(self, notifications: List[Dict]) -> Dict[str, Any]:
        """Formate les notifications en payload pour la queue."""
        try:
            formatted_notifications = []
            
            for notif in notifications:
                # Extraire le message supplémentaire
                additional_info_message = self._extract_additional_info_message(
                    notif.get('additional_info', '{}')
                )
                
                formatted_notif = {
                    "message": f"{notif.get('file_name', 'Document')} - {notif.get('status', 'info')}",
                    "file_name": notif.get('file_name', 'Document'),
                    "collection_id": notif.get('collection_id', ''),
                    "collection_name": notif.get('collection_name', ''),
                    "url": f"/edit_form/{notif.get('job_id', '')}",
                    "status": notif.get('status', 'info'),
                    "read": notif.get('read', False),
                    "doc_id": notif.get('doc_id', ""),
                    "job_id": notif.get('job_id', ''),
                    "file_id": notif.get('file_id', ''),
                    "function_name": notif.get('function_name', ""),
                    "timestamp": notif.get('timestamp', ''),
                    "additional_info": notif.get('additional_info', '{}'),
                    "info_message": additional_info_message
                }
                formatted_notifications.append(formatted_notif)
            
            return {
                "notifications": formatted_notifications,
                "count": len(formatted_notifications),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Erreur lors du formatage des notifications : {e}")
            return {"notifications": [], "count": 0, "timestamp": datetime.now(timezone.utc).isoformat()}
    
    def _extract_additional_info_message(self, additional_info_str: str) -> str:
        """Extrait le message supplémentaire du champ additional_info."""
        try:
            info = json.loads(additional_info_str)
            if "error_message" in info and info["error_message"]:
                return info["error_message"]
            elif "message" in info and info["message"]:
                return info["message"]
            return ""
        except (json.JSONDecodeError, TypeError):
            return ""
    
    def update_user_context(self, firebase_user_id: str, authorized_companies_ids: List[str]):
        """Met à jour le contexte utilisateur du listener."""
        self.firebase_user_id = firebase_user_id
        self.authorized_companies_ids = authorized_companies_ids
        self.logger.info(f"Contexte utilisateur mis à jour pour {firebase_user_id}") 

    async def _put_to_queue(self, payload: Dict[str, Any]):
        """Place un payload dans la queue dédiée si fournie, sinon queue globale."""
        try:
            if self.output_queue is not None:
                await self.output_queue.put((self.name, payload))
            else:
                await super()._put_to_queue(payload)
        except Exception as e:
            self.logger.error(f"Erreur lors de l'ajout à la queue (notif): {e}")

    async def start(self):
        """Démarre le listener en mode on_snapshot (temps réel)."""
        if self.is_running:
            self.logger.warning(f"Listener {self.name} déjà en cours d'exécution")
            return
        self.is_running = True

        async def _runner():
            try:
                self._loop = asyncio.get_running_loop()
                if not self.firebase_user_id:
                    self.logger.warning("firebase_user_id manquant, arrêt du listener")
                    return
                path = f"clients/{self.firebase_user_id}/notifications"
                query = self.db.collection(path).where('read', '==', False)

                def _on_snapshot(snapshot, changes, read_time):
                    try:
                        notifs = self._snapshot_to_notifications(snapshot)
                        payload = self._format_payload(notifs)
                        if self._loop:
                            fut = asyncio.run_coroutine_threadsafe(self._put_to_queue(payload), self._loop)
                            try:
                                fut.result(timeout=1.0)
                            except Exception:
                                pass
                    except Exception as e:
                        self.logger.error(f"Erreur callback snapshot notifications: {e}")

                self._detach_listener = query.on_snapshot(_on_snapshot)
                self.logger.info("on_snapshot notifications attaché")

                while self.is_running:
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.logger.info("Listener notifications annulé")
            except Exception as e:
                self.logger.error(f"Erreur dans le runner notifications: {e}")
            finally:
                if self._detach_listener is not None:
                    try:
                        self._detach_listener.unsubscribe()
                    except Exception:
                        pass
                    self._detach_listener = None
                self.logger.info("Listener notifications arrêté")

        self.task = asyncio.create_task(_runner())

    async def stop(self):
        """Arrête le listener on_snapshot."""
        if not self.is_running:
            return
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass