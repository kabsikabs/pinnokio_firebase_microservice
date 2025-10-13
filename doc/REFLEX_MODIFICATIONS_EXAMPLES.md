# 🔧 Modifications à apporter dans l'application Reflex

## 📍 Localisation des fichiers

Ces modifications doivent être apportées dans votre application Reflex, probablement dans :
- `C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\`

## 📋 Fichiers à modifier/créer

### 1. Créer `llm_state.py` (NOUVEAU)

**Fichier :** `pinnokio_app/state/llm_state.py`

```python
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
        """Initialise une session LLM via le microservice."""
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
        """Met à jour le contexte LLM lors du changement de société."""
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
        """Envoie un message via le microservice."""
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
        """Retourne le statut de la connexion LLM."""
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
        """Vérifie si le service LLM est prêt à être utilisé."""
        return self._llm_connected and not self._llm_init_inflight and self._llm_error is None
    
    def get_llm_error(self) -> Optional[str]:
        """Retourne la dernière erreur LLM."""
        return self._llm_error
    
    def clear_llm_error(self):
        """Efface l'erreur LLM actuelle."""
        self._llm_error = None
        yield
```

### 2. Modifier AuthState (si existant)

**Fichier :** `pinnokio_app/state/auth_state.py` (MODIFIER)

```python
# Ajouter ces imports et méthodes dans votre AuthState existant

from .llm_state import LLMState

class AuthState(rx.State):
    # ... vos variables existantes ...
    
    # 🆕 NOUVEAU: Référence à LLMState
    _llm_state: Optional[LLMState] = None
    
    def __init__(self):
        super().__init__()
        self._llm_state = LLMState()
    
    async def on_auth_success(self, user_id: str, authorized_companies: list, current_company: str):
        """Appelé après authentification réussie."""
        # ... votre logique existante ...
        
        # 🆕 NOUVEAU: Initialiser LLMState
        await self.initialize_llm_for_user(user_id, current_company)
    
    async def initialize_llm_for_user(self, user_id: str, collection_name: str):
        """Initialise le service LLM pour l'utilisateur authentifié."""
        try:
            success = await self._llm_state.initialize_llm_session(
                user_id=user_id,
                collection_name=collection_name,
                dms_system="google_drive",  # À récupérer depuis user_info
                dms_mode="prod",
                chat_mode="general_chat"
            )
            
            if success:
                print(f"✅ LLM initialisé pour {user_id} dans société {collection_name}")
            else:
                print(f"❌ Échec initialisation LLM pour {user_id}")
                
        except Exception as e:
            print(f"❌ Erreur initialisation LLM: {e}")
    
    async def switch_company(self, new_company_id: str):
        """Change de société et met à jour le contexte LLM."""
        # ... votre logique existante de changement de société ...
        
        # 🆕 NOUVEAU: Mettre à jour LLMState
        await self.update_llm_company_context(new_company_id)
    
    async def update_llm_company_context(self, new_collection_name: str):
        """Met à jour le contexte LLM lors du changement de société."""
        try:
            success = await self._llm_state.update_company_context(new_collection_name)
            
            if success:
                print(f"✅ Contexte LLM mis à jour pour société: {new_collection_name}")
            else:
                print(f"❌ Échec mise à jour contexte LLM")
                
        except Exception as e:
            print(f"❌ Erreur mise à jour contexte LLM: {e}")
```

### 3. Modifier ChatState

**Fichier :** `pinnokio_app/state/base_state.py` (MODIFIER)

```python
# Ajouter ces imports et modifications dans votre ChatState existant

from .llm_state import LLMState

class ChatState(rx.State):
    # ... vos variables existantes INCHANGÉES ...
    
    # 🆕 NOUVEAU: Référence à LLMState
    _llm_state: Optional[LLMState] = None
    
    def __init__(self):
        super().__init__()
        self._llm_state = LLMState()
    
    @rx.event(background=True)
    async def initialize_llm_agent(self):
        """✅ MODIFIÉ: Utilise LLMState au lieu de l'ancien système."""
        try:
            # Vérifier si LLMState est déjà connecté
            if self._llm_state._llm_connected:
                print("✅ LLM déjà connecté via LLMState")
                return True
            
            # Récupérer les infos utilisateur depuis AuthState
            user_id = getattr(self, 'firebase_user_id', None)
            collection_name = getattr(self, 'base_collection_id', None)
            
            if not user_id or not collection_name:
                print("❌ Infos utilisateur manquantes pour LLM")
                return False
            
            # Initialiser via LLMState
            success = await self._llm_state.initialize_llm_session(
                user_id=user_id,
                collection_name=collection_name,
                dms_system=getattr(self, 'dms_type_extracted', 'google_drive'),
                dms_mode="prod",
                chat_mode=getattr(self, 'chat_mode', 'general_chat')
            )
            
            return success
            
        except Exception as e:
            print(f"❌ Erreur initialisation LLM: {e}")
            return False
    
    @rx.event(background=True)
    async def send_message(self):
        """✅ MODIFIÉ: Utilise LLMState pour envoyer via microservice."""
        if not self.question.strip():
            return
        
        try:
            # Vérifier que LLMState est connecté
            if not self._llm_state._llm_connected:
                print("⚠️ LLM non connecté, initialisation...")
                success = await self.initialize_llm_agent()
                if not success:
                    yield rx.toast.error("Impossible de se connecter à l'assistant")
                    return
            
            question = self.question
            self.question = ""
            self.processing = True
            current_chat_key = self.current_chat
            
            yield
            
            # Récupérer le system prompt selon le mode
            system_prompt = self._get_system_prompt_by_mode()
            
            # ✅ Envoi via LLMState (qui appelle le microservice)
            result = await self._llm_state.send_message(
                space_code=self.base_collection_id,  # collection_name = space_code
                chat_thread=current_chat_key,
                message=question,
                system_prompt=system_prompt
            )
            
            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                print(f"❌ Erreur envoi message: {error_msg}")
                self.processing = False
                yield rx.toast.error(f"Erreur: {error_msg}")
                return
            
            # ✅ C'EST TOUT ! Le listener RTDB va gérer le reste automatiquement
            print(f"✅ Message envoyé au microservice: {result.get('assistant_message_id')}")
            
        except Exception as e:
            print(f"❌ Exception send_message: {e}")
            self.processing = False
            yield rx.toast.error(f"Erreur: {str(e)}")
    
    def _get_system_prompt_by_mode(self) -> str:
        """Retourne le prompt système selon le chat_mode."""
        # ... votre logique existante inchangée ...
        pass
    
    # ✅ _handle_chat_message() reste INCHANGÉ
    # Le listener RTDB continue de fonctionner exactement comme avant
```

## 🎯 Points importants

1. **Créer le fichier `llm_state.py`** dans votre application Reflex
2. **Modifier AuthState** pour initialiser LLMState après authentification
3. **Modifier ChatState** pour utiliser LLMState au lieu de l'ancien système
4. **Garder `_handle_chat_message()` inchangé** - le listener RTDB continue de fonctionner

## 🔄 Flux complet

```
1. AuthState.on_auth_success() → LLMState.initialize_llm_session()
2. ChatState.send_message() → LLMState.send_message() → RPC microservice
3. Microservice écrit dans Firebase RTDB
4. ChatState._handle_chat_message() (listener RTDB) → UI mise à jour
```

## ✅ Checklist

- [ ] Créer `pinnokio_app/state/llm_state.py`
- [ ] Modifier AuthState pour initialiser LLMState
- [ ] Modifier ChatState pour utiliser LLMState
- [ ] Tester initialisation après authentification
- [ ] Tester changement de société
- [ ] Tester conversation complète end-to-end

**Ces modifications doivent être apportées dans votre application Reflex, pas dans le microservice !** 🚀
