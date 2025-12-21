# 🤖 Intégration LLM Reflex - Plan d'implémentation FINAL

## 📋 Vue d'ensemble

Ce document décrit l'intégration complète du service LLM microservice dans l'application Reflex, avec une architecture indépendante et une gestion intelligente des changements de société.

## 🎯 Architecture proposée

```
┌─────────────────────────────────────────────────────────────┐
│                    REFLEX APPLICATION                        │
│                                                             │
│  AuthState (authentification)                               │
│  ├─ user_id: str                                           │
│  ├─ authorized_companies: List[str]                        │
│  ├─ current_company_id: str                                │
│  └─ on_auth_success() → initialize_llm_state()            │
│                                                             │
│  LLMState (NOUVEAU - indépendant)                           │
│  ├─ _llm_session_id: Optional[str]                          │
│  ├─ _llm_connected: bool                                   │
│  ├─ _llm_collection_name: str                              │
│  ├─ initialize_session() → RPC LLM.initialize_session     │
│  ├─ update_company() → RPC LLM.update_context             │
│  └─ send_message() → RPC LLM.send_message                 │
│                                                             │
│  ChatState (MODIFIÉ)                                       │
│  ├─ question: str                                          │
│  ├─ processing: bool                                       │
│  ├─ chats: Dict[str, List[QA]]                             │
│  ├─ send_message() → LLMState.send_message()              │
│  └─ _handle_chat_message() (INCHANGÉ - listener RTDB)      │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 Implémentation détaillée

### **1. Créer LLMState indépendant**

**Fichier :** `pinnokio_app/state/llm_state.py` (NOUVEAU)

```python
import reflex as rx
from typing import Optional
from .manager import get_manager  # RPC manager existant

class LLMState(rx.State):
    """État indépendant pour la gestion LLM via microservice."""
    
    # Variables d'état
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
                return False
                
            self._llm_init_inflight = True
            self._llm_error = None
            yield
            
            # Appel RPC au microservice
            result = await get_manager().rpc_call(
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
                
                print(f"✅ LLM initialisé: {self._llm_session_id}")
                return True
            else:
                error_msg = result.get("error", "Unknown error") if result else "No response"
                self._llm_error = error_msg
                print(f"❌ Erreur initialisation LLM: {error_msg}")
                return False
                
        except Exception as e:
            self._llm_error = str(e)
            print(f"❌ Exception initialisation LLM: {e}")
            return False
        finally:
            self._llm_init_inflight = False
            yield
    
    async def update_company_context(self, new_collection_name: str) -> bool:
        """Met à jour le contexte LLM lors du changement de société."""
        try:
            if not self._llm_connected or not self._llm_session_id:
                # Réinitialiser avec la nouvelle société
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
            # via la session existante
            print(f"✅ Contexte LLM mis à jour pour société: {new_collection_name}")
            return True
            
        except Exception as e:
            print(f"❌ Erreur mise à jour contexte LLM: {e}")
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
                return {"success": False, "error": "LLM non connecté"}
            
            # Appel RPC au microservice
            result = await get_manager().rpc_call(
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
            
            return result if result else {"success": False, "error": "No response"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_llm_status(self) -> dict:
        """Retourne le statut de la connexion LLM."""
        return {
            "connected": self._llm_connected,
            "session_id": self._llm_session_id,
            "collection_name": self._llm_collection_name,
            "error": self._llm_error,
            "init_inflight": self._llm_init_inflight
        }
```

### **2. Modifier AuthState pour initialiser LLMState**

**Fichier :** `pinnokio_app/state/auth_state.py` (MODIFIÉ)

```python
# Ajouter dans AuthState après authentification réussie
from .llm_state import LLMState

class AuthState(rx.State):
    # ... variables existantes ...
    
    async def on_auth_success(self, user_id: str, authorized_companies: list, current_company: str):
        """Appelé après authentification réussie."""
        # ... logique existante ...
        
        # 🆕 NOUVEAU: Initialiser LLMState
        await self.initialize_llm_for_user(user_id, current_company)
    
    async def initialize_llm_for_user(self, user_id: str, collection_name: str):
        """Initialise le service LLM pour l'utilisateur authentifié."""
        try:
            # Accéder à LLMState depuis l'instance globale
            llm_state = LLMState()
            success = await llm_state.initialize_llm_session(
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
        # ... logique existante de changement de société ...
        
        # 🆕 NOUVEAU: Mettre à jour LLMState
        await self.update_llm_company_context(new_company_id)
    
    async def update_llm_company_context(self, new_collection_name: str):
        """Met à jour le contexte LLM lors du changement de société."""
        try:
            llm_state = LLMState()
            success = await llm_state.update_company_context(new_collection_name)
            
            if success:
                print(f"✅ Contexte LLM mis à jour pour société: {new_collection_name}")
            else:
                print(f"❌ Échec mise à jour contexte LLM")
                
        except Exception as e:
            print(f"❌ Erreur mise à jour contexte LLM: {e}")
```

### **3. Modifier ChatState pour utiliser LLMState**

**Fichier :** `pinnokio_app/state/base_state.py` (MODIFIÉ)

```python
from .llm_state import LLMState

class ChatState(rx.State):
    # ... variables existantes INCHANGÉES ...
    
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
        # ... logique existante inchangée ...
        pass
    
    # ✅ _handle_chat_message() reste INCHANGÉ
    # Le listener RTDB continue de fonctionner exactement comme avant
```

## 🔄 Flux de communication complet

```
1. AuthState.on_auth_success()
   ↓
2. AuthState.initialize_llm_for_user()
   ↓
3. LLMState.initialize_llm_session() → RPC LLM.initialize_session
   ↓
4. ChatState.send_message()
   ↓
5. LLMState.send_message() → RPC LLM.send_message
   ↓
6. Microservice écrit dans Firebase RTDB
   ↓
7. ChatState._handle_chat_message() (listener RTDB)
   ↓
8. UI mise à jour automatiquement
```

## 🎯 Avantages de cette architecture

1. **✅ Séparation claire** : LLMState indépendant d'AuthState et ChatState
2. **✅ Gestion société** : Changement automatique du contexte LLM
3. **✅ Réutilisation** : LLMState peut être utilisé par d'autres composants
4. **✅ Compatibilité** : ChatState garde la même interface
5. **✅ Évolutivité** : Facile d'ajouter de nouvelles fonctionnalités LLM

## 📋 Checklist d'implémentation

- [ ] Créer `pinnokio_app/state/llm_state.py`
- [ ] Modifier `AuthState` pour initialiser LLMState
- [ ] Modifier `ChatState` pour utiliser LLMState
- [ ] Tester initialisation après authentification
- [ ] Tester changement de société
- [ ] Tester conversation complète end-to-end

## 🚀 Déploiement

### Étape 1: Créer les fichiers
1. Créer `pinnokio_app/state/llm_state.py`
2. Modifier `pinnokio_app/state/auth_state.py`
3. Modifier `pinnokio_app/state/base_state.py`

### Étape 2: Tests
1. Tester authentification → initialisation LLM
2. Tester changement de société → mise à jour contexte
3. Tester conversation complète

### Étape 3: Production
1. Déployer microservice avec service LLM
2. Déployer Reflex avec nouvelles modifications
3. Vérifier fonctionnement end-to-end

## 🔍 Points d'attention

1. **Gestion d'erreurs** : Fallback si microservice indisponible
2. **Performance** : Cache des sessions LLM
3. **Sécurité** : Validation des paramètres utilisateur
4. **Monitoring** : Logs et métriques de performance

---

**Cette architecture garantit une intégration propre et évolutive du service LLM dans Reflex !** 🚀

