"""
Listener pour les messages Firebase.
Récupère les messages directs non lus toutes les 5 secondes.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Set
from ..code.tools.g_cred import FirebaseRealtimeChat

from .base_listener import BaseListener

class MessageListener(BaseListener):
    """Listener pour les messages directs Firebase."""
    
    def __init__(self, firebase_user_id: str, companies: List[Dict]):
        super().__init__("messages")
        self.firebase_user_id = firebase_user_id
        self.companies = companies
        self._known_message_ids: Set[str] = set()
    
    def get_interval(self) -> int:
        """Intervalle spécifique pour les messages : 5 secondes."""
        return 5
    
    def get_realtime_service(self) -> FirebaseRealtimeChat:
        """Obtient le service Firebase Realtime Chat."""
        return FirebaseRealtimeChat(user_id=self.firebase_user_id)
    
    async def _fetch_data(self) -> List[Dict]:
        """Récupère les messages directs non lus depuis Firebase."""
        try:
            # Exécuter dans un thread pour ne pas bloquer l'event loop
            return await asyncio.to_thread(self._fetch_messages_sync)
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des messages: {e}")
            return []
    
    def _fetch_messages_sync(self) -> List[Dict]:
        """Fonction synchrone pour récupérer les messages."""
        try:
            if not self.firebase_user_id:
                return []
            
            # Obtenir le service Firebase Realtime
            realtime_service = self.get_realtime_service()
            
            # Récupérer les messages non lus
            messages = realtime_service.get_unread_direct_messages(
                user_id=self.firebase_user_id,
                companies=self.companies
            )
            
            return messages
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des messages : {e}")
            return []
    
    def _format_payload(self, messages: List[Dict]) -> Dict[str, Any]:
        """Formate les messages en payload pour la queue."""
        try:
            new_message_ids = set()
            formatted_messages = []
            
            for msg in messages:
                doc_id = msg.get('doc_id', '')
                new_message_ids.add(doc_id)
                
                formatted_messages.append({
                    "message": f"{msg.get('file_name', 'Document')} - {msg.get('status', 'info')}",
                    "file_name": msg.get('file_name', 'Document'),
                    "collection_id": msg.get('collection_id', ''),
                    "collection_name": msg.get('collection_name', ''),
                    "url": f"/edit_form/{msg.get('job_id', '')}",
                    "status": msg.get('status', 'info'),
                    "doc_id": doc_id,
                    "job_id": msg.get('job_id', ''),
                    "file_id": msg.get('file_id', ''),
                    "function_name": msg.get('function_name', ""),
                    "timestamp": msg.get('timestamp', ''),
                    "additional_info": msg.get('additional_info', '{}')
                })
            
            # Détecter les nouveaux messages
            truly_new = new_message_ids - self._known_message_ids
            latest_new_message = None
            
            if truly_new:
                latest_new_message = next((msg for msg in formatted_messages if msg['doc_id'] in truly_new), None)
            
            # Mettre à jour les IDs connus
            self._known_message_ids = new_message_ids
            
            return {
                "messages": formatted_messages,
                "count": len(formatted_messages),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "new_message": latest_new_message,
                "has_new_messages": bool(truly_new)
            }
            
        except Exception as e:
            self.logger.error(f"Erreur lors du formatage des messages : {e}")
            return {
                "messages": [], 
                "count": 0, 
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "new_message": None,
                "has_new_messages": False
            }
    
    def update_user_context(self, firebase_user_id: str, companies: List[Dict]):
        """Met à jour le contexte utilisateur du listener."""
        self.firebase_user_id = firebase_user_id
        self.companies = companies
        self._known_message_ids = set()  # Reset les IDs connus
        self.logger.info(f"Contexte utilisateur mis à jour pour {firebase_user_id}") 