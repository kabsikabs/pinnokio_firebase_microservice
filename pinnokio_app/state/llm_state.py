"""
État indépendant pour la gestion LLM via microservice.
Gère les sessions LLM et la communication avec le microservice Firebase.
"""

import reflex as rx
from typing import Optional
import logging

logger = logging.getLogger("reflex.llm_state")


class LLMState(rx.State):
    """État indépendant pour la gestion LLM via microservice.
    
    Ce state gère :
    - L'initialisation des sessions LLM
    - La communication avec le microservice via RPC
    - La mise à jour du contexte lors des changements de société
    - L'envoi de messages via le microservice
    """
    
    # Variables d'état LLM
    _llm_session_id: Optional[str] = None
    _llm_connected: bool = False
    _llm_collection_name: str = ""
    _llm_user_id: str = ""
    _llm_dms_system: str = "google_drive"
    _llm_dms_mode: str = "prod"
    _llm_chat_mode: str = "general_chat"
    
    # État de connexion
    _llm_init_inflight: bool = False
    _llm_error: Optional[str] = None
    
    async def initialize_llm_session(
        self, 
        user_id: str, 
        collection_name: str,
        dms_system: str = "google_drive",
        dms_mode: str = "prod",
        chat_mode: str = "general_chat"
    ) -> bool:
        """Initialise une session LLM via le microservice.
        
        Args:
            user_id: ID de l'utilisateur Firebase
            collection_name: Nom de la société/collection
            dms_system: Système DMS (google_drive, etc.)
            dms_mode: Mode DMS (prod, dev, etc.)
            chat_mode: Mode de chat (general_chat, etc.)
            
        Returns:
            bool: True si l'initialisation réussit, False sinon
        """
        try:
            if self._llm_init_inflight:
                logger.warning("Initialisation LLM déjà en cours")
                return False
                
            self._llm_init_inflight = True
            self._llm_error = None
            yield
            
            logger.info(f"Initialisation session LLM pour {user_id} dans {collection_name}")
            
            # Import du manager RPC (à adapter selon votre structure)
            try:
                from .manager import get_manager
                manager = get_manager()
            except ImportError:
                logger.error("Impossible d'importer le manager RPC")
                self._llm_error = "Manager RPC non disponible"
                return False
            
            # Appel RPC au microservice
            result = await manager.rpc_call(
                method="LLM.initialize_session",
                args={
                    "user_id": user_id,
                    "collection_name": collection_name,
                    "dms_system": dms_system,
                    "dms_mode": dms_mode,
                    "chat_mode": chat_mode
                },
                user_id=user_id,
                timeout_ms=30000
            )
            
            if result and result.get("success"):
                self._llm_session_id = result.get("session_id")
                self._llm_connected = True
                self._llm_collection_name = collection_name
                self._llm_user_id = user_id
                self._llm_dms_system = dms_system
                self._llm_dms_mode = dms_mode
                self._llm_chat_mode = chat_mode
                
                logger.info(f"✅ LLM initialisé: {self._llm_session_id}")
                return True
            else:
                error_msg = result.get("error", "Unknown error") if result else "No response"
                self._llm_error = error_msg
                logger.error(f"❌ Erreur initialisation LLM: {error_msg}")
                return False
                
        except Exception as e:
            self._llm_error = str(e)
            logger.error(f"❌ Exception initialisation LLM: {e}", exc_info=True)
            return False
        finally:
            self._llm_init_inflight = False
            yield
    
    async def update_company_context(self, new_collection_name: str) -> bool:
        """Met à jour le contexte LLM lors du changement de société.
        
        Args:
            new_collection_name: Nouveau nom de société/collection
            
        Returns:
            bool: True si la mise à jour réussit, False sinon
        """
        try:
            logger.info(f"Mise à jour contexte LLM pour société: {new_collection_name}")
            
            if not self._llm_connected or not self._llm_session_id:
                # Réinitialiser avec la nouvelle société
                logger.info("Session LLM non connectée, réinitialisation...")
                return await self.initialize_llm_session(
                    user_id=self._llm_user_id,
                    collection_name=new_collection_name,
                    dms_system=self._llm_dms_system,
                    dms_mode=self._llm_dms_mode,
                    chat_mode=self._llm_chat_mode
                )
            
            # Mettre à jour le contexte existant
            self._llm_collection_name = new_collection_name
            
            # Note: Le microservice gère automatiquement le changement de contexte
            # via la session existante (pas besoin d'appel RPC supplémentaire)
            logger.info(f"✅ Contexte LLM mis à jour pour société: {new_collection_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erreur mise à jour contexte LLM: {e}", exc_info=True)
            return False
    
    async def send_message(
        self,
        space_code: str,
        chat_thread: str,
        message: str,
        system_prompt: str = None
    ) -> dict:
        """Envoie un message via le microservice.
        
        Args:
            space_code: Code de l'espace (généralement = collection_name)
            chat_thread: Clé du thread de conversation
            message: Message à envoyer
            system_prompt: Prompt système optionnel
            
        Returns:
            dict: Résultat de l'envoi avec success/error
        """
        try:
            if not self._llm_connected:
                logger.warning("Tentative d'envoi de message sans connexion LLM")
                return {"success": False, "error": "LLM non connecté"}
            
            logger.info(f"Envoi message LLM pour thread: {chat_thread}")
            
            # Import du manager RPC
            try:
                from .manager import get_manager
                manager = get_manager()
            except ImportError:
                logger.error("Impossible d'importer le manager RPC")
                return {"success": False, "error": "Manager RPC non disponible"}
            
            # Appel RPC au microservice
            result = await manager.rpc_call(
                method="LLM.send_message",
                args={
                    "user_id": self._llm_user_id,
                    "collection_name": self._llm_collection_name,
                    "space_code": space_code,
                    "chat_thread": chat_thread,
                    "message": message,
                    "chat_mode": self._llm_chat_mode,
                    "system_prompt": system_prompt
                },
                user_id=self._llm_user_id,
                timeout_ms=5000
            )
            
            if result and result.get("success"):
                logger.info(f"✅ Message envoyé au microservice: {result.get('assistant_message_id')}")
            else:
                error_msg = result.get("error", "Unknown error") if result else "No response"
                logger.error(f"❌ Erreur envoi message: {error_msg}")
            
            return result if result else {"success": False, "error": "No response"}
            
        except Exception as e:
            logger.error(f"❌ Exception envoi message LLM: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def get_llm_status(self) -> dict:
        """Retourne le statut de la connexion LLM.
        
        Returns:
            dict: Statut complet de la connexion LLM
        """
        return {
            "connected": self._llm_connected,
            "session_id": self._llm_session_id,
            "collection_name": self._llm_collection_name,
            "user_id": self._llm_user_id,
            "dms_system": self._llm_dms_system,
            "dms_mode": self._llm_dms_mode,
            "chat_mode": self._llm_chat_mode,
            "error": self._llm_error,
            "init_inflight": self._llm_init_inflight
        }
    
    def is_llm_ready(self) -> bool:
        """Vérifie si le service LLM est prêt à être utilisé.
        
        Returns:
            bool: True si LLM est connecté et prêt, False sinon
        """
        return self._llm_connected and not self._llm_init_inflight and self._llm_error is None
    
    def get_llm_error(self) -> Optional[str]:
        """Retourne la dernière erreur LLM.
        
        Returns:
            Optional[str]: Message d'erreur ou None
        """
        return self._llm_error
    
    def clear_llm_error(self):
        """Efface l'erreur LLM actuelle."""
        self._llm_error = None
        yield

