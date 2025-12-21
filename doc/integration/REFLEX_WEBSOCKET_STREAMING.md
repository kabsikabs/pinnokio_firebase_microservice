# 🚀 Configuration du Streaming LLM via WebSocket pour Reflex

## 📡 Vue d'ensemble

Le microservice Python a été migré du streaming RTDB vers le streaming WebSocket pour améliorer la fluidité et réduire la latence des réponses IA.

**Changements clés :**
- ✅ **Streaming temps réel via WebSocket** : Latence réduite de ~50-200ms à ~1-5ms
- ✅ **1 seule écriture RTDB finale** : Économie sur les coûts Firebase
- ✅ **Format de canal identique** : Facilite la transition depuis RTDB
- ✅ **Compatibilité maintenue** : L'écriture finale dans RTDB reste pour l'historique

---

## 🔌 Connexion WebSocket

### Endpoint WebSocket
```
wss://your-microservice.com/ws?uid={user_id}&space_code={collection_name}&thread_key={thread_key}
```

### Paramètres de connexion
- `uid` **(requis)** : ID Firebase de l'utilisateur
- `space_code` **(optionnel)** : Code de la société/espace (collection_name)
- `thread_key` **(optionnel)** : Clé du thread de conversation
- `mode` **(optionnel)** : Mode de chat (défaut: "auto")

---

## 📨 Format du Canal WebSocket

Le canal WebSocket utilise **exactement le même format que RTDB** :

```
chat:{user_id}:{collection_name}:{thread_key}
```

### Exemple
```
chat:user123:company456:thread789
```

Ce format est retourné dans la réponse RPC `LLM.send_message` sous la clé `ws_channel`.

---

## 📥 Types d'Événements WebSocket

Tous les événements WebSocket contiennent :
- `type` : Type d'événement (voir ci-dessous)
- `channel` : Canal au format `chat:{user_id}:{collection_name}:{thread_key}`
- `payload` : Données de l'événement

### 1️⃣ **llm_stream_start** - Début du streaming

Reçu au début de la génération de la réponse IA.

```json
{
  "type": "llm_stream_start",
  "channel": "chat:user123:company456:thread789",
  "payload": {
    "message_id": "msg-uuid-1234",
    "thread_key": "thread789",
    "space_code": "company456",
    "timestamp": "2025-10-12T10:30:00.123456Z"
  }
}
```

**Action Reflex recommandée :**
- Créer un message temporaire avec `is_streaming=True`
- Afficher un indicateur "IA en train d'écrire..."

---

### 2️⃣ **llm_stream_chunk** - Chunk de contenu

Reçu pour chaque morceau de texte généré par l'IA (très haute fréquence).

```json
{
  "type": "llm_stream_chunk",
  "channel": "chat:user123:company456:thread789",
  "payload": {
    "message_id": "msg-uuid-1234",
    "thread_key": "thread789",
    "space_code": "company456",
    "chunk": " puis-je",
    "accumulated": "Bonjour, comment puis-je",
    "is_final": false
  }
}
```

**Champs importants :**
- `chunk` : Nouveau fragment de texte (à ajouter)
- `accumulated` : Contenu complet jusqu'à présent (à afficher)
- `is_final` : `true` si c'est le dernier chunk

**Action Reflex recommandée :**
- Mettre à jour le message temporaire avec `accumulated`
- Déclencher un re-render pour effet de "typing"

---

### 3️⃣ **llm_stream_complete** - Fin du streaming

Reçu une fois la génération terminée avec succès.

```json
{
  "type": "llm_stream_complete",
  "channel": "chat:user123:company456:thread789",
  "payload": {
    "message_id": "msg-uuid-1234",
    "thread_key": "thread789",
    "space_code": "company456",
    "full_content": "Bonjour, comment puis-je vous aider aujourd'hui ?",
    "metadata": {
      "tokens_used": {
        "prompt": 150,
        "completion": 25,
        "total": 175
      },
      "duration_ms": 2340,
      "model": "claude-3-7-sonnet-20250219",
      "status": "complete",
      "completed_at": "2025-10-12T10:30:02.463456Z"
    }
  }
}
```

**Action Reflex recommandée :**
- Convertir le message temporaire en message permanent
- Retirer l'indicateur de streaming
- Sauvegarder les métadonnées (tokens, durée, etc.)

---

### 4️⃣ **llm_stream_interrupted** - Streaming interrompu

Reçu si l'utilisateur interrompt le streaming (stop_streaming).

```json
{
  "type": "llm_stream_interrupted",
  "channel": "chat:user123:company456:thread789",
  "payload": {
    "message_id": "msg-uuid-1234",
    "thread_key": "thread789",
    "space_code": "company456",
    "accumulated": "Bonjour, comment"
  }
}
```

**Action Reflex recommandée :**
- Afficher le contenu partiel `accumulated`
- Ajouter une note "⚠️ Réponse interrompue"
- Marquer le message comme interrompu

---

### 5️⃣ **llm_stream_error** - Erreur pendant le streaming

Reçu en cas d'erreur pendant la génération.

```json
{
  "type": "llm_stream_error",
  "channel": "chat:user123:company456:thread789",
  "payload": {
    "message_id": "msg-uuid-1234",
    "thread_key": "thread789",
    "space_code": "company456",
    "error": "Anthropic API rate limit exceeded"
  }
}
```

**Action Reflex recommandée :**
- Afficher un message d'erreur à l'utilisateur
- Supprimer le message temporaire ou le marquer comme erreur
- Logger l'erreur pour debugging

---

## 🔄 Flux Complet : Envoi d'un Message

### 1. Envoi du message via RPC (inchangé)

```python
# Code Reflex côté client
response = await rpc_client.call(
    method="LLM.send_message",
    kwargs={
        "user_id": "user123",
        "collection_name": "company456",
        "thread_key": "thread789",
        "message": "Bonjour, peux-tu m'aider ?",
        "chat_mode": "general_chat",
        "system_prompt": "Tu es un assistant utile...",
        "selected_tool": None
    }
)

# Réponse RPC
{
  "success": True,
  "user_message_id": "msg-user-uuid",
  "assistant_message_id": "msg-assistant-uuid",
  "ws_channel": "chat:user123:company456:thread789",  # ← NOUVEAU
  "message": "Message envoyé, réponse en cours de streaming via WebSocket"
}
```

### 2. Écoute des événements WebSocket

```python
# Code Reflex - Gestion des événements WebSocket
class ChatState(rx.State):
    messages: List[Message] = []
    streaming_message: Optional[StreamingMessage] = None
    
    async def handle_websocket_event(self, event: dict):
        """Gestionnaire unifié des événements WebSocket LLM"""
        event_type = event.get("type")
        channel = event.get("channel")
        payload = event.get("payload", {})
        
        # Vérifier que c'est bien notre canal
        expected_channel = f"chat:{self.user_id}:{self.space_code}:{self.thread_key}"
        if channel != expected_channel:
            return  # Ignorer si ce n'est pas notre canal
        
        if event_type == "llm_stream_start":
            self._handle_stream_start(payload)
        
        elif event_type == "llm_stream_chunk":
            self._handle_stream_chunk(payload)
        
        elif event_type == "llm_stream_complete":
            self._handle_stream_complete(payload)
        
        elif event_type == "llm_stream_interrupted":
            self._handle_stream_interrupted(payload)
        
        elif event_type == "llm_stream_error":
            self._handle_stream_error(payload)
    
    def _handle_stream_start(self, payload: dict):
        """Début du streaming : créer un message temporaire"""
        self.streaming_message = StreamingMessage(
            id=payload["message_id"],
            thread_key=payload["thread_key"],
            content="",
            is_streaming=True,
            timestamp=payload["timestamp"]
        )
    
    def _handle_stream_chunk(self, payload: dict):
        """Chunk reçu : mettre à jour le contenu"""
        if self.streaming_message and self.streaming_message.id == payload["message_id"]:
            self.streaming_message.content = payload["accumulated"]
            # ✨ Forcer le re-render pour l'effet de typing
            self.streaming_message = self.streaming_message
    
    def _handle_stream_complete(self, payload: dict):
        """Streaming terminé : convertir en message permanent"""
        if self.streaming_message:
            final_message = Message(
                id=self.streaming_message.id,
                content=payload["full_content"],
                role="assistant",
                timestamp=self.streaming_message.timestamp,
                metadata=payload.get("metadata", {})
            )
            self.messages.append(final_message)
            self.streaming_message = None
    
    def _handle_stream_interrupted(self, payload: dict):
        """Streaming interrompu : afficher contenu partiel"""
        if self.streaming_message:
            partial_message = Message(
                id=self.streaming_message.id,
                content=payload["accumulated"] + "\n\n⚠️ *Réponse interrompue*",
                role="assistant",
                timestamp=self.streaming_message.timestamp,
                is_interrupted=True
            )
            self.messages.append(partial_message)
            self.streaming_message = None
    
    def _handle_stream_error(self, payload: dict):
        """Erreur de streaming : afficher l'erreur"""
        if self.streaming_message:
            error_message = Message(
                id=self.streaming_message.id,
                content=f"❌ Erreur : {payload['error']}",
                role="assistant",
                timestamp=datetime.now(),
                is_error=True
            )
            self.messages.append(error_message)
            self.streaming_message = None
```

---

## 🔧 Intégration avec le Système Existant

### RTDB : Uniquement pour l'historique

L'écoute RTDB **n'est plus nécessaire pour le streaming LLM**, mais doit être conservée pour :

1. **Chargement de l'historique au démarrage**
   ```python
   # Charger les messages existants depuis RTDB
   async def load_chat_history(self, thread_key: str):
       history = await firebase_rtdb.get_messages(thread_key)
       self.messages = history
   ```

2. **Messages non-LLM** (notifications, messages système, autres utilisateurs)
   ```python
   # Conserver le listener RTDB pour les événements non-streaming
   async def listen_rtdb_for_system_messages(self, thread_key: str):
       def callback(message_data):
           # Ignorer les messages assistant (gérés par WebSocket)
           if message_data.get("role") != "assistant":
               self.messages.append(Message.from_dict(message_data))
       
       firebase_rtdb.listen_channel(thread_key, callback)
   ```

### Architecture hybride recommandée

```
┌─────────────────────────────────────────────────────────────┐
│                    Client Reflex                             │
│                                                               │
│  ┌──────────────────┐         ┌────────────────────┐        │
│  │   WebSocket      │         │   RTDB Listener     │        │
│  │   (Streaming)    │         │   (Historique)      │        │
│  │                  │         │                     │        │
│  │  - llm_stream_*  │         │  - load_history     │        │
│  │  - Temps réel    │         │  - system_messages  │        │
│  │  - Ultra rapide  │         │  - notifications    │        │
│  └──────────────────┘         └────────────────────┘        │
│           ↑                            ↑                      │
└───────────┼────────────────────────────┼──────────────────────┘
            │                            │
            │ (WebSocket)                │ (RTDB Listener)
            │                            │
┌───────────┼────────────────────────────┼──────────────────────┐
│           │    Python Microservice     │                       │
│           │                            │                       │
│  ┌────────▼────────────┐    ┌─────────▼─────────┐           │
│  │  WebSocket Hub      │    │   RTDB Writer      │           │
│  │  (ws_hub.py)        │    │   (1 write/msg)    │           │
│  │                     │    │                    │           │
│  │  - broadcast()      │    │  - Final message   │           │
│  │  - Streaming chunks │    │  - Persistence     │           │
│  └─────────────────────┘    └────────────────────┘           │
│                                                                │
│  ┌──────────────────────────────────────────────┐            │
│  │        LLMManager                             │            │
│  │  (_process_message_with_ws_streaming)        │            │
│  │                                               │            │
│  │  1. hub.broadcast(llm_stream_start)          │            │
│  │  2. For each chunk:                          │            │
│  │     hub.broadcast(llm_stream_chunk)          │            │
│  │  3. rtdb.set(final_message) ← 1 write only   │            │
│  │  4. hub.broadcast(llm_stream_complete)       │            │
│  └──────────────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────┘
```

---

## 🛠️ APIs RPC Disponibles

### LLM.send_message

Envoie un message et démarre le streaming via WebSocket.

```python
response = rpc_client.call(
    method="LLM.send_message",
    kwargs={
        "user_id": "user123",
        "collection_name": "company456",
        "thread_key": "thread789",
        "message": "Bonjour",
        "chat_mode": "general_chat",
        "system_prompt": "Tu es un assistant...",
        "selected_tool": None
    }
)
```

**Réponse :**
```json
{
  "success": true,
  "user_message_id": "msg-user-uuid",
  "assistant_message_id": "msg-assistant-uuid",
  "ws_channel": "chat:user123:company456:thread789",
  "message": "Message envoyé, réponse en cours de streaming via WebSocket"
}
```

### LLM.stop_streaming

Interrompt un streaming en cours.

```python
response = rpc_client.call(
    method="LLM.stop_streaming",
    kwargs={
        "user_id": "user123",
        "collection_name": "company456",
        "thread_key": "thread789"  # Optionnel
    }
)
```

**Réponse :**
```json
{
  "success": true,
  "message": "Stream arrêté pour thread thread789",
  "thread_key": "thread789"
}
```

---

## ⚡ Avantages de cette Architecture

| Critère | RTDB (Ancien) | WebSocket (Nouveau) |
|---------|---------------|---------------------|
| **Latence chunk** | ~50-200ms | ~1-5ms ⚡ |
| **Écritures Firebase** | ~50-100 par message | 1 seule ✅ |
| **Coût Firebase** | Élevé 💸 | Minimal 💰 |
| **Fluidité UX** | Saccadé 😕 | Fluide comme ChatGPT 🎯 |
| **Complexité** | Buffer + Debounce | Direct 🚀 |
| **Historique** | ✅ Persisté | ✅ Persisté (final) |
| **Scaling** | Limité | Excellent |

---

## 🔒 Gestion des Déconnexions

### Auto-reconnexion recommandée

```python
class WebSocketManager:
    async def connect(self):
        """Connexion avec auto-reconnexion"""
        while True:
            try:
                await self._connect_websocket()
                break  # Succès
            except Exception as e:
                logger.error(f"Erreur connexion WebSocket: {e}")
                await asyncio.sleep(2)  # Backoff
    
    async def on_disconnect(self):
        """Gestion de déconnexion"""
        logger.info("WebSocket déconnecté, tentative de reconnexion...")
        await self.connect()
```

### Récupération après déconnexion

Si le WebSocket se déconnecte pendant un streaming :

1. **Tentative de reconnexion automatique**
2. **Rechargement depuis RTDB** : Le message final sera dans RTDB une fois le streaming terminé
3. **Vérification des messages manqués** : Comparer les `message_id` locaux avec RTDB

---

## 📊 Monitoring & Debugging

### Logs côté Backend

```python
# Les logs suivants sont automatiquement générés :
logger.info(f"Traitement message avec streaming WebSocket pour thread: {thread_key}")
logger.info(f"Canal WebSocket: {ws_channel}")
logger.info(f"Chunk #{chunk_count} reçu: '{chunk_content[:50]}...'")
logger.info(f"Streaming terminé. Total chunks: {chunk_count}")
```

### Logs côté Reflex (recommandés)

```python
def _handle_stream_chunk(self, payload: dict):
    logger.debug(f"WS chunk reçu : {payload['message_id'][:8]}... | {len(payload['accumulated'])} chars")
    self.streaming_message.content = payload["accumulated"]
```

---

## 🎯 Checklist de Migration

- [ ] Connecter WebSocket avec `uid`, `space_code`, `thread_key`
- [ ] Implémenter `handle_websocket_event()` pour gérer les 5 types d'événements
- [ ] Gérer `streaming_message` temporaire pendant le streaming
- [ ] Convertir en message permanent à la fin (`llm_stream_complete`)
- [ ] Gérer les interruptions (`llm_stream_interrupted`)
- [ ] Gérer les erreurs (`llm_stream_error`)
- [ ] Conserver l'écoute RTDB pour l'historique et les messages non-LLM
- [ ] Implémenter auto-reconnexion WebSocket
- [ ] Tester la récupération après déconnexion
- [ ] Monitorer les performances (latence, chunks/sec)

---

## 📞 Support

Pour toute question ou problème :
- Consulter les logs backend avec `LISTENERS_DEBUG=true`
- Vérifier la connexion WebSocket dans les dev tools du navigateur
- Vérifier que le `ws_channel` correspond au format attendu

---

**Version** : 1.0.0  
**Date** : Octobre 2025  
**Auteur** : Équipe Backend Python

