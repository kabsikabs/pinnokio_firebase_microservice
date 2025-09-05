import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable
from ..code.tools.firebase_realtime import FirebaseRealtimeChat

class ChatListener:
    """Listener autonome pour les messages de chat Firebase Realtime."""
    
    def __init__(self, space_code: str, thread_key: str, user_id: str, mode: str = 'job_chats'):
        self.name = f"chat_{space_code}_{thread_key}"
        self.space_code = space_code
        self.thread_key = thread_key
        self.user_id = user_id
        self.mode = mode
        self.is_running = False
        self.listener_ref = None
        self.realtime_service = FirebaseRealtimeChat(user_id=self.user_id)
        self._message_callback = None
        self.logger = logging.getLogger(f"chat_listener.{self.name}")
    
    async def start(self, message_callback: Callable[[dict], None]):
        """Démarre le listener temps réel avec un callback direct."""
        if self.is_running:
            self.logger.warning(f"Listener {self.name} déjà en cours d'exécution")
            return
        
        try:
            self.is_running = True
            self._message_callback = message_callback
            
            self.logger.info(f"🚀 Démarrage de l'écoute temps réel pour {self.space_code}/{self.thread_key}")
            
            # Wrapper pour le callback qui assure l'appel asynchrone
            async def callback_wrapper(message_data: dict):
                try:
                    if self._message_callback:
                        # Appeler le callback directement (il doit être async)
                        await self._message_callback(message_data)
                    return True
                except Exception as e:
                    self.logger.error(f"❌ Erreur dans le callback: {e}")
                    return False
            
            # Utiliser directement la méthode de FirebaseRealtimeChat
            self.listener_ref = await self.realtime_service.listen_realtime_channel(
                space_code=self.space_code,
                thread_key=self.thread_key,
                callback=callback_wrapper,
                mode=self.mode,
            )
            
            self.logger.info(f"✅ Écoute temps réel configurée pour {self.space_code}/{self.thread_key}")
            
        except Exception as e:
            self.is_running = False
            self.logger.error(f"❌ Erreur lors du démarrage du listener: {e}")
            raise
    
    async def stop(self):
        """Arrête le listener temps réel."""
        if not self.is_running:
            return
        
        try:
            self.is_running = False
            
            # Arrêter le listener Firebase
            if self.listener_ref and hasattr(self.listener_ref, 'close'):
                self.listener_ref.close()
                self.logger.info(f"Listener Firebase {self.name} fermé")
            
            self.listener_ref = None
            self._message_callback = None
            self.logger.info(f"Listener {self.name} arrêté")
            
        except Exception as e:
            self.logger.error(f"❌ Erreur lors de l'arrêt du listener: {e}")
    
    def is_listener_active(self) -> bool:
        """Vérifie si le listener temps réel est actif."""
        return self.is_running and self.listener_ref is not None