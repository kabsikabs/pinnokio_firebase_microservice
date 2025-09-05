"""
Listener spécialisé pour le vrai temps réel Firebase.
Utilise les listeners Firebase natifs au lieu du polling.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod
from .base_listener import get_global_queue

class RealtimeListener(ABC):
    """Classe abstraite pour les listeners en vrai temps réel."""
    
    def __init__(self, name: str):
        self.name = name
        self.is_running = False
        self.listener_ref = None  # Référence Firebase listener
        self.logger = logging.getLogger(f"realtime_listener.{name}")
    
    @abstractmethod
    async def _setup_realtime_connection(self) -> Any:
        """Configure la connexion temps réel spécifique au listener."""
        pass
    
    @abstractmethod
    async def _handle_realtime_event(self, event_data: Any):
        """Traite un événement temps réel reçu."""
        pass
    
    @abstractmethod
    def _format_payload(self, event_data: Any) -> Dict[str, Any]:
        """Formate l'événement en payload pour la queue."""
        pass
    
    async def _put_to_queue(self, payload: Dict[str, Any]):
        """Place un payload dans la queue globale."""
        try:
            queue = get_global_queue()
            await queue.put((self.name, payload))
            self.logger.debug(f"Payload temps réel ajouté à la queue: {self.name}")
        except Exception as e:
            self.logger.error(f"Erreur lors de l'ajout à la queue: {e}")
    
    async def start(self):
        """Démarre le listener temps réel."""
        if self.is_running:
            self.logger.warning(f"Listener temps réel {self.name} déjà en cours d'exécution")
            return
        
        try:
            self.is_running = True
            self.listener_ref = await self._setup_realtime_connection()
            self.logger.info(f"Listener temps réel {self.name} démarré")
        except Exception as e:
            self.is_running = False
            self.logger.error(f"Erreur lors du démarrage du listener temps réel {self.name}: {e}")
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
            self.logger.info(f"Listener temps réel {self.name} arrêté")
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du listener temps réel {self.name}: {e}")
    
    def is_listener_active(self) -> bool:
        """Vérifie si le listener temps réel est actif."""
        return self.is_running and self.listener_ref is not None 