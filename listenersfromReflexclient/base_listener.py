"""
Classe de base pour tous les listeners temps-réel.
Implémente le pipeline unique avec asyncio.Queue.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod

# Queue globale partagée entre tous les listeners
global_queue: asyncio.Queue = asyncio.Queue()

class BaseListener(ABC):
    """Classe abstraite de base pour tous les listeners."""
    
    def __init__(self, name: str):
        self.name = name
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.logger = logging.getLogger(f"listener.{name}")
        
    @abstractmethod
    async def _fetch_data(self) -> Any:
        """Méthode abstraite pour récupérer les données spécifiques au listener."""
        pass
    
    @abstractmethod
    def _format_payload(self, data: Any) -> Dict[str, Any]:
        """Méthode abstraite pour formater les données en payload."""
        pass
    
    async def _put_to_queue(self, payload: Dict[str, Any]):
        """Place un payload dans la queue globale."""
        try:
            await global_queue.put((self.name, payload))
            self.logger.debug(f"Payload ajouté à la queue: {self.name}")
        except Exception as e:
            self.logger.error(f"Erreur lors de l'ajout à la queue: {e}")
    
    async def start(self):
        """Démarre le listener."""
        if self.is_running:
            self.logger.warning(f"Listener {self.name} déjà en cours d'exécution")
            return
        
        self.is_running = True
        self.task = asyncio.create_task(self._run())
        self.logger.info(f"Listener {self.name} démarré")
    
    async def stop(self):
        """Arrête le listener."""
        if not self.is_running:
            return
        
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.logger.info(f"Listener {self.name} arrêté")
    
    async def _run(self):
        """Boucle principale du listener."""
        self.logger.info(f"Démarrage de la boucle {self.name}")
        
        while self.is_running:
            try:
                # Récupérer les données
                data = await self._fetch_data()
                
                # Formater et envoyer à la queue
                if data:
                    payload = self._format_payload(data)
                    await self._put_to_queue(payload)
                
                # Attendre avant la prochaine itération
                await asyncio.sleep(self.get_interval())
                
            except asyncio.CancelledError:
                self.logger.info(f"Listener {self.name} annulé")
                break
            except Exception as e:
                self.logger.error(f"Erreur dans le listener {self.name}: {e}")
                # Attendre un peu avant de réessayer en cas d'erreur
                await asyncio.sleep(5)
    
    def get_interval(self) -> int:
        """Retourne l'intervalle entre les vérifications (en secondes)."""
        return 30  # Par défaut 30 secondes

# Fonction utilitaire pour obtenir la queue globale
def get_global_queue() -> asyncio.Queue:
    """Retourne la queue globale partagée."""
    return global_queue 