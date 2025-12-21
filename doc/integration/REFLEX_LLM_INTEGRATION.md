# Intégration LLM avec Reflex

Ce document décrit comment modifier l'application Reflex pour utiliser le service LLM du microservice via RPC.

## 🎯 Objectif

Rediriger toutes les interactions LLM de `ChatState` vers le microservice, sans changer la logique métier de Reflex, uniquement la couche de communication.

## 📋 Modifications nécessaires

### 1. Modifier `ChatState.initialize_llm_agent()`

**Fichier:** `C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\base_state.py`

**Avant (code actuel):**
```python
async def initialize_llm_agent(self):
    """Initialise l'instance LLM avec le contexte utilisateur."""
    try:
        if not self._llm_instance:
            # Création directe de BaseAIAgent
            instance = BaseAIAgent(
                collection_name=self.user_info.collection_name,
                dms_system=self.user_info.dms_system or "google_drive",
                dms_mode=self.user_info.dms_mode or "prod",
                firebase_user_id=self.user_info.firebase_user_id,
                chat_instance=None,
                job_id=None
            )
            
            LLMSingleton.initialize(instance)
            self._llm_instance = LLMSingleton.get_instance()
        
        # Mise à jour du prompt système
        if self._llm_instance and hasattr(self, 'dms_system_prompt'):
            self._llm_instance.update_system_prompt(self.dms_system_prompt)
        
        return True
    except Exception as e:
        logger.error(f"Erreur initialisation LLM: {e}")
        return False
```

**Après (avec RPC):**
```python
async def initialize_llm_agent(self):
    """Initialise l'instance LLM via le microservice."""
    try:
        from .manager import get_manager
        
        # Appel RPC au microservice pour initialiser la session LLM
        result = await get_manager().rpc_call(
            method="LLM.initialize_session",
            args={
                "user_id": self.user_info.firebase_user_id,
                "collection_name": self.user_info.collection_name,
                "dms_system": self.user_info.dms_system or "google_drive",
                "dms_mode": self.user_info.dms_mode or "prod",
                "chat_mode": self.chat_mode or "general_chat"
            }
        )
        
        if result.get("success"):
            self._llm_session_id = result.get("session_id")
            logger.info(f"Session LLM initialisée: {self._llm_session_id}")
            
            # Marquer comme initialisé (pour compatibilité avec le code existant)
            # Note: On ne stocke plus l'instance localement, tout est côté microservice
            return True
        else:
            logger.error(f"Échec initialisation LLM: {result.get('error')}")
            return False
            
    except Exception as e:
        logger.error(f"Erreur initialisation LLM: {e}")
        return False
```

### 2. Modifier `ChatState.send_message()`

**Avant (code actuel):**
```python
async def send_message(self, form_data: dict):
    """Envoie un message au LLM."""
    message = form_data.get("message", "").strip()
    if not message:
        return
    
    # Ajouter message utilisateur (UI optimiste)
    user_msg = {
        "role": "user",
        "content": message,
        "timestamp": datetime.now().isoformat()
    }
    self.messages.append(user_msg)
    
    # Appeler le LLM local
    llm_instance = self._llm_instance
    if llm_instance:
        response = llm_instance.process_text(
            content=message,
            provider=ModelProvider.ANTHROPIC,
            size=ModelSize.MEDIUM
        )
        
        # Ajouter réponse
        assistant_msg = {
            "role": "assistant",
            "content": response.get("text_output", {}).get("content", {}).get("answer_text", ""),
            "timestamp": datetime.now().isoformat()
        }
        self.messages.append(assistant_msg)
```

**Après (avec RPC + Firebase RTDB):**
```python
async def send_message(self, form_data: dict):
    """Envoie un message au LLM via le microservice."""
    message = form_data.get("message", "").strip()
    if not message:
        return
    
    try:
        from .manager import get_manager
        
        # Appel RPC au microservice
        # Le microservice écrira directement dans Firebase RTDB
        # Le listener RTDB de Reflex mettra à jour l'UI automatiquement
        result = await get_manager().rpc_call(
            method="LLM.send_message",
            args={
                "user_id": self.user_info.firebase_user_id,
                "collection_name": self.user_info.collection_name,
                "space_code": self.user_info.space_code,
                "chat_thread": self.current_thread_key,
                "message": message,
                "chat_mode": self.chat_mode or "general_chat",
                "system_prompt": self.dms_system_prompt if hasattr(self, 'dms_system_prompt') else None
            }
        )
        
        if result.get("success"):
            logger.info(f"Message envoyé au microservice: {result.get('assistant_message_id')}")
            # Note: Pas besoin d'ajouter aux messages ici
            # Le listener RTDB _handle_chat_message() s'en chargera automatiquement
        else:
            logger.error(f"Échec envoi message: {result.get('error')}")
            # Afficher une erreur à l'utilisateur
            yield rx.toast.error(f"Erreur: {result.get('error')}")
            
    except Exception as e:
        logger.error(f"Erreur envoi message LLM: {e}")
        yield rx.toast.error(f"Erreur de communication: {str(e)}")
```

### 3. Le listener RTDB `_handle_chat_message()` reste INCHANGÉ

Le listener Firebase RTDB existant dans `ChatState` continue de fonctionner exactement comme avant:

```python
def _handle_chat_message(self, event):
    """Géré automatiquement par le listener RTDB.
    Reçoit les messages (user + assistant streaming) depuis Firebase RTDB.
    """
    # Ce code reste identique - il écoute déjà Firebase RTDB
    # et met à jour self.messages automatiquement
    pass
```

**Pourquoi ça fonctionne:**
- Le microservice écrit dans `{space_code}/chats/{thread_key}/messages/`
- Le `ChatListener` de Reflex écoute déjà ce chemin
- Quand un nouveau message arrive (user ou assistant), le listener le détecte
- Il appelle `_handle_chat_message()` qui met à jour l'UI

## 🔄 Flux de communication complet

```
┌─────────────────┐
│  Reflex (UI)    │
│   ChatState     │
└────────┬────────┘
         │
         │ 1. send_message()
         │    via RPC: LLM.send_message
         ▼
┌─────────────────┐
│  Microservice   │
│   LLMManager    │
└────────┬────────┘
         │
         │ 2. Écrit message user dans Firebase RTDB
         │    {space_code}/chats/{thread}/messages/{user_msg_id}
         │
         │ 3. Traite avec BaseAIAgent
         │
         │ 4. Stream réponse assistant dans Firebase RTDB
         │    {space_code}/chats/{thread}/messages/{assistant_msg_id}
         │    (mise à jour toutes les 100ms)
         ▼
┌─────────────────┐
│ Firebase RTDB   │
│   Messages      │
└────────┬────────┘
         │
         │ 5. Listener RTDB détecte les nouveaux messages
         ▼
┌─────────────────┐
│  Reflex (UI)    │
│ _handle_chat_   │
│    message()    │
│                 │
│ Met à jour UI   │
└─────────────────┘
```

## 🧪 Tests

### Test 1: Vérifier la connexion RPC

```python
# Dans le microservice
python test_llm_connection.py
```

### Test 2: Tester depuis Reflex

```python
# Dans l'application Reflex, ajouter un test dans ChatState
async def test_llm_connection(self):
    """Test de connexion LLM avec le microservice."""
    from .manager import get_manager
    
    result = await get_manager().rpc_call(
        method="LLM.initialize_session",
        args={
            "user_id": "test_user",
            "collection_name": "test_company",
            "dms_system": "google_drive"
        }
    )
    
    print(f"Résultat: {result}")
    return result.get("success", False)
```

## 📝 Variables d'état à ajouter dans `ChatState`

```python
class ChatState(rx.State):
    # ... code existant ...
    
    # 🆕 NOUVEAU: ID de session LLM côté microservice
    _llm_session_id: Optional[str] = None
    
    # ... reste du code ...
```

## ⚙️ Configuration requise

### Dans le microservice

Aucune configuration supplémentaire requise. Le service LLM utilise:
- Firebase RTDB (déjà configuré)
- BaseAIAgent (déjà dans `app/llm/klk_agents.py`)
- RPC existant

### Dans Reflex

S'assurer que le `ListenerManager` de Reflex est correctement configuré pour:
1. Écouter `{space_code}/chats/{thread_key}/messages/`
2. Appeler `_handle_chat_message()` sur nouveaux messages

## 🚀 Déploiement

### Étape 1: Déployer le microservice

```bash
# Le microservice inclut maintenant le service LLM
# Pas de changement dans le Dockerfile
docker build -t firebase-microservice .
docker push ...
```

### Étape 2: Mettre à jour Reflex

```bash
# Modifier les 2 méthodes dans base_state.py
# Tester localement
reflex run
```

### Étape 3: Vérifier

1. Ouvrir l'application Reflex
2. Initialiser un chat
3. Envoyer un message
4. Vérifier que la réponse apparaît (streaming)
5. Vérifier les logs du microservice

## 🔍 Debug

### Logs microservice

```bash
# Vérifier l'initialisation
docker logs <container> | grep "Session LLM initialisée"

# Vérifier les messages
docker logs <container> | grep "Message assistant complété"
```

### Logs Reflex

```python
# Dans ChatState
import logging
logger = logging.getLogger("reflex.chat")

# Dans send_message()
logger.info(f"Envoi message au microservice: user={self.user_info.firebase_user_id}")
```

## ✅ Checklist

- [ ] Modifier `initialize_llm_agent()` dans `ChatState`
- [ ] Modifier `send_message()` dans `ChatState`
- [ ] Ajouter `_llm_session_id` à `ChatState`
- [ ] Tester connexion RPC (test_llm_connection.py)
- [ ] Tester depuis Reflex
- [ ] Vérifier listeners RTDB
- [ ] Vérifier streaming des réponses
- [ ] Déployer en production

## 🎓 Notes importantes

1. **Pas d'UI optimiste nécessaire**: Le microservice écrit directement dans RTDB, le listener met à jour l'UI immédiatement

2. **Streaming automatique**: Le buffer intelligent du microservice optimise les écritures RTDB (100ms)

3. **Session réutilisable**: Une session LLM par `user_id:collection_name`, partagée entre tous les threads

4. **Backward compatible**: Si le microservice est indisponible, on peut ajouter un fallback vers l'ancien système

## 🔮 Prochaines étapes

Une fois la connexion de base établie:
1. Ajouter le framework agentic (SPT/LPT)
2. Gérer les événements système (thinking, tool execution)
3. Ajouter les quotas/limites de tâches
4. Monitoring et métriques


