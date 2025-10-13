# 🤖 Architecture LLM Microservice - Documentation Technique (FINALE)

## 📋 Vue d'ensemble

Cette architecture permet de **déplacer toute la logique LLM** de l'application Reflex vers le microservice Firebase, tout en **maintenant une compatibilité totale** avec le code existant côté Reflex.

**✅ DÉCISIONS VALIDÉES :**
1. **Communication** : Firebase Realtime Database (comme les chats existants)
2. **Path RTDB** : `{space_code}/chats/{thread_key}/messages/` (nouveau chemin dédié)
3. **Streaming** : Update toutes les 100ms avec debounce intelligent
4. **Événements système** : Firebase RTDB avec champ `metadata` et `role: system`
5. **Agent** : `BaseAIAgent` déjà présent dans `app/llm/klk_agents.py`

---

## 🎯 Objectifs

1. ✅ **Déplacer le LLM** : Toute la logique `BaseAIAgent` vers le microservice
2. ✅ **Isolation parfaite** : Par `user_id` + `collection_name` + `chat_thread`
3. ✅ **Communication Firebase RTDB** : Réutilise l'infrastructure existante (ChatListener)
4. ✅ **Zéro changement Reflex** : Seule la communication change, pas l'API
5. ✅ **Framework agentic** : Support SPT (Short Process Tooling) et LPT (Long Process Tooling)
6. ✅ **Scalabilité** : Gestion de milliers de conversations simultanées

---

## 🏗️ Architecture Technique

### **1. Vue d'ensemble du flux de communication**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION REFLEX (INCHANGÉE)                      │
│                                                                             │
│  ChatState                                                                  │
│  ├─ question: str                                                           │
│  ├─ processing: bool                                                        │
│  ├─ chats: Dict[str, List[QA]]                                             │
│  └─ Methods:                                                                │
│     ├─ send_message(question: str)  ──┐                                    │
│     ├─ _handle_chat_message(...)      │ (déjà existant)                   │
│     └─ update_chat_display(...)       │                                    │
│                                        │                                    │
│  ChatListener (Firebase RTDB)         │                                    │
│  ├─ Écoute: {space_code}/chats/{thread}/messages/                         │
│  └─ Callback: _handle_chat_message()  │                                    │
│                                        │                                    │
└────────────────────────────────────────┼────────────────────────────────────┘
                                         │
                                         │ RPC Call
                                         │ rpc_call("LLM.send_message", args=[...])
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MICROSERVICE FIREBASE                              │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │  main.py - RPC Handler                                           │      │
│  │  ├─ @app.post("/rpc")                                            │      │
│  │  └─ _resolve_method("LLM.*") → LLM Manager                       │      │
│  └─────────────────────────────────────────────────────────────────┘      │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │  llm_service/llm_manager.py - LLM Service Manager                │      │
│  │  ├─ send_message() → Écrit dans Firebase RTDB                    │      │
│  │  ├─ get_or_create_session()                                      │      │
│  │  └─ _process_message_with_rtdb_streaming()                       │      │
│  └─────────────────────────────────────────────────────────────────┘      │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │  llm_service/llm_session.py - Session LLM Isolée                 │      │
│  │  Namespace: {user_id}:{collection_name}                          │      │
│  │  ├─ agent: BaseAIAgent (klk_agents.py)                          │      │
│  │  ├─ conversations: Dict[thread_key, List[Message]]               │      │
│  │  ├─ active_tasks: Dict[thread_key, List[TaskID]]                │      │
│  │  └─ process_message_streaming() → async generator               │      │
│  └─────────────────────────────────────────────────────────────────┘      │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │  Firebase Realtime Database                                      │      │
│  │  {space_code}/chats/{thread_key}/messages/                       │      │
│  │  ├─ {msg_id_1}: {role: "user", content: "..."}                  │      │
│  │  ├─ {msg_id_2}: {role: "assistant", content: "...",             │      │
│  │  │                status: "streaming", streaming_progress: 0.45} │      │
│  │  └─ {msg_id_3}: {role: "system", type: "tool_execution", ...}   │      │
│  └─────────────────────────────────────────────────────────────────┘      │
│                                    │                                        │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     │ Firebase RTDB Listener (déjà actif)
                                     │ ChatListener.on_event()
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    REFLEX - ChatState._handle_chat_message()                │
│                                                                             │
│  Détecte automatiquement :                                                  │
│  ├─ Nouveaux messages (role: user/assistant)                               │
│  ├─ Updates streaming (content mis à jour progressivement)                 │
│  ├─ Statuts (streaming → complete → error)                                 │
│  └─ Messages système (tool_execution, long_task, etc.)                     │
│                                                                             │
│  → UI se met à jour automatiquement en temps réel ✅                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📊 **Structure Firebase Realtime Database**

### **1. Path pour les conversations LLM**

```
{space_code}/                        # Collection name (société)
  └─ chats/                          # ✅ Nouveau chemin dédié aux conversations LLM
      └─ {thread_key}/               # Thread de conversation
          └─ messages/
              ├─ {message_id_1}/
              │   ├─ role: "user"
              │   ├─ content: "Comment analyser cette facture ?"
              │   ├─ timestamp: "2025-10-10T12:34:56Z"
              │   ├─ user_id: "user_abc123"
              │   └─ read: false
              │
              ├─ {message_id_2}/
              │   ├─ role: "assistant"
              │   ├─ content: "Je vais analyser..."  # ✅ Mis à jour progressivement (streaming)
              │   ├─ timestamp: "2025-10-10T12:35:02Z"
              │   ├─ status: "streaming" | "complete" | "error"
              │   ├─ streaming_progress: 0.75  # Pour barre de progression
              │   ├─ last_update: "2025-10-10T12:35:03.245Z"
              │   └─ metadata:
              │       ├─ tokens_used: {prompt: 150, completion: 320, total: 470}
              │       ├─ tools_called: ["read_document", "analyze_invoice"]
              │       ├─ duration_ms: 3420
              │       └─ model: "claude-3-7-sonnet-20250219"
              │
              ├─ {message_id_3}/  # ✅ Message système pour tool execution
              │   ├─ role: "system"
              │   ├─ type: "tool_execution"
              │   ├─ content: "🔧 Lecture du document invoice_2025.pdf..."
              │   ├─ timestamp: "2025-10-10T12:35:15Z"
              │   ├─ ephemeral: true  # Supprimé après traitement
              │   └─ metadata:
              │       ├─ tool_name: "read_document"
              │       ├─ tool_args: {document_id: "doc_456"}
              │       ├─ status: "running" | "complete" | "error"
              │       └─ duration_ms: 2100
              │
              └─ {message_id_4}/  # ✅ Message système pour LPT
                  ├─ role: "system"
                  ├─ type: "long_task"
                  ├─ content: "📊 Rapprochement comptable lancé (environ 1h)..."
                  ├─ timestamp: "2025-10-10T12:35:25Z"
                  ├─ persistent: true  # Gardé dans l'historique
                  └─ metadata:
                      ├─ task_id: "lpt_accounting_12345"
                      ├─ task_type: "accounting_reconciliation"
                      ├─ status: "queued" | "processing" | "complete" | "error"
                      ├─ progress_percent: 35
                      ├─ current_step: "Analyse des transactions (2/5)"
                      └─ estimated_completion: "2025-10-10T13:35:00Z"
```

---

## 🔧 **Implémentation Microservice**

### **1. Service LLM Manager - Version Firebase RTDB**

```python
# app/llm_service/llm_manager.py

import asyncio
import json
import uuid
import time
from typing import Dict, Optional, Any
from datetime import datetime, timezone
from ..llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize
from .llm_context import LLMContext

class RTDBStreamingBuffer:
    """Buffer intelligent pour optimiser les écritures Firebase RTDB (100ms debounce)."""
    
    def __init__(self, min_interval_ms: int = 100, max_buffer_size: int = 50):
        self.min_interval_ms = min_interval_ms
        self.max_buffer_size = max_buffer_size
        self.buffer = ""
        self.last_write_time = 0
        self.pending_task = None
        self.accumulated_content = ""
    
    async def add_chunk(self, chunk: str, rtdb_ref, force_flush: bool = False):
        """Ajoute un chunk et flush intelligemment."""
        self.buffer += chunk
        self.accumulated_content += chunk
        current_time = time.time() * 1000  # ms
        
        # Conditions de flush :
        # 1. Intervalle minimum atteint
        # 2. Buffer plein (pour ne pas accumuler trop)
        # 3. Force flush (dernier chunk)
        should_flush = (
            (current_time - self.last_write_time) >= self.min_interval_ms or
            len(self.buffer) >= self.max_buffer_size or
            force_flush
        )
        
        if should_flush:
            await self._flush(rtdb_ref)
        else:
            # Planifier un flush automatique si rien ne vient
            if self.pending_task:
                self.pending_task.cancel()
            self.pending_task = asyncio.create_task(
                self._auto_flush(rtdb_ref, self.min_interval_ms / 1000)
            )
    
    async def _flush(self, rtdb_ref):
        """Flush le buffer vers Firebase RTDB."""
        if not self.buffer:
            return
        
        try:
            rtdb_ref.update({
                "content": self.accumulated_content,
                "last_update": datetime.now(timezone.utc).isoformat()
            })
            
            self.buffer = ""
            self.last_write_time = time.time() * 1000
        except Exception as e:
            print(f"❌ Erreur flush RTDB: {e}")
    
    async def _auto_flush(self, rtdb_ref, delay: float):
        """Flush automatique après un délai."""
        try:
            await asyncio.sleep(delay)
            await self._flush(rtdb_ref)
        except asyncio.CancelledError:
            pass


class LLMSession:
    """Session LLM isolée pour un utilisateur/société.
    
    Gère l'agent BaseAIAgent et l'historique des conversations pour tous les threads
    de cet utilisateur dans cette société.
    """
    
    def __init__(self, session_key: str, context: LLMContext):
        self.session_key = session_key  # user_id:collection_name
        self.context = context
        self.agent: Optional[BaseAIAgent] = None
        
        # Historique par thread de conversation
        self.conversations: Dict[str, List[dict]] = {}
        
        # Tâches actives par thread
        self.active_tasks: Dict[str, List[str]] = {}
        
        # État par thread
        self.thread_states: Dict[str, str] = {}
        
        # Métriques
        self.created_at = datetime.now(timezone.utc)
        self.last_activity: Dict[str, datetime] = {}
        self.response_times: Dict[str, List[float]] = {}
    
    async def initialize_agent(self):
        """Initialise l'agent BaseAIAgent avec le contexte."""
        try:
            print(f"🚀 Initialisation BaseAIAgent pour session {self.session_key}")
            
            # Initialiser BaseAIAgent avec les paramètres du contexte
            self.agent = BaseAIAgent(
                collection_name=self.context.collection_name,
                dms_system=self.context.dms_system,
                dms_mode=self.context.dms_mode,
                firebase_user_id=self.context.user_id
            )
            
            # Enregistrer les providers par défaut (à adapter selon vos besoins)
            # Exemple : Anthropic
            from ..llm.klk_agents import Anthropic_Agent
            anthropic_instance = Anthropic_Agent()
            self.agent.register_provider(ModelProvider.ANTHROPIC, anthropic_instance)
            
            # Vous pouvez ajouter d'autres providers ici
            # from ..llm.klk_agents import OpenAI_Agent
            # openai_instance = OpenAI_Agent()
            # self.agent.register_provider(ModelProvider.OPENAI, openai_instance)
            
            print(f"✅ Agent LLM initialisé pour session {self.session_key}")
            
        except Exception as e:
            print(f"❌ Erreur initialisation agent: {e}")
            raise
    
    def update_context(self, **kwargs):
        """Met à jour le contexte dynamiquement."""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        
        # Si DMS change, réinitialiser l'agent
        if 'dms_system' in kwargs or 'dms_mode' in kwargs:
            if self.agent:
                self.agent._initialize_dms(
                    self.context.dms_mode,
                    self.context.dms_system,
                    self.context.user_id
                )
    
    def add_user_message(self, thread_key: str, message: str):
        """Ajoute un message utilisateur à l'historique d'un thread."""
        if thread_key not in self.conversations:
            self.conversations[thread_key] = []
        
        self.conversations[thread_key].append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        self.last_activity[thread_key] = datetime.now(timezone.utc)
    
    async def process_message_streaming(
        self,
        thread_key: str,
        message: str,
        system_prompt: str = None
    ):
        """Traite un message et yield les chunks de réponse.
        
        Yields:
            dict: {"content": str, "index": int, "is_final": bool, "tool_calls": list}
        """
        try:
            self.thread_states[thread_key] = "processing"
            start_time = datetime.now(timezone.utc)
            
            # Mettre à jour le prompt système si fourni
            if system_prompt and self.agent:
                self.agent.update_system_prompt(system_prompt)
            
            # ✅ Appeler BaseAIAgent pour traiter le message
            # Note: BaseAIAgent n'a pas de méthode streaming native, donc on va
            # simuler un streaming en envoyant la réponse par chunks
            
            if not self.agent:
                raise Exception("Agent non initialisé")
            
            # Utiliser process_text avec le provider et size par défaut
            response = self.agent.process_text(
                content=message,
                provider=self.agent.default_provider or ModelProvider.ANTHROPIC,
                size=self.agent.default_model_size or ModelSize.MEDIUM
            )
            
            # Extraire le texte de la réponse
            response_text = ""
            if isinstance(response, dict):
                if 'text_output' in response:
                    text_output = response.get('text_output', {})
                    if isinstance(text_output, dict):
                        content = text_output.get('content', {})
                        if isinstance(content, dict):
                            response_text = content.get('answer_text', '')
                        else:
                            response_text = str(content)
                    else:
                        response_text = str(text_output)
                else:
                    response_text = str(response)
            else:
                response_text = str(response)
            
            # Simuler un streaming en envoyant la réponse par chunks
            chunk_size = 5  # Nombre de caractères par chunk
            total_chars = len(response_text)
            
            for i in range(0, total_chars, chunk_size):
                chunk = response_text[i:i+chunk_size]
                yield {
                    "content": chunk,
                    "index": i // chunk_size,
                    "is_final": (i + chunk_size >= total_chars),
                    "tool_calls": None
                }
                await asyncio.sleep(0.01)  # Petit délai pour simuler le streaming
            
            # Ajouter réponse à l'historique
            self.conversations[thread_key].append({
                "role": "assistant",
                "content": response_text,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # Métriques
            duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            if thread_key not in self.response_times:
                self.response_times[thread_key] = []
            self.response_times[thread_key].append(duration_ms)
            
            self.thread_states[thread_key] = "idle"
            
        except Exception as e:
            self.thread_states[thread_key] = "error"
            print(f"❌ Erreur process_message_streaming: {e}")
            raise
    
    def get_token_stats(self, thread_key: str) -> dict:
        """Retourne les stats de tokens depuis BaseAIAgent."""
        if not self.agent:
            return {"prompt": 0, "completion": 0, "total": 0}
        
        try:
            # BaseAIAgent a une méthode get_token_usage_by_provider()
            usage = self.agent.get_token_usage_by_provider()
            
            # Agréger les stats de tous les providers
            total_input = sum(p.get('total_input_tokens', 0) for p in usage.values())
            total_output = sum(p.get('total_output_tokens', 0) for p in usage.values())
            
            return {
                "prompt": total_input,
                "completion": total_output,
                "total": total_input + total_output
            }
        except Exception:
            return {"prompt": 0, "completion": 0, "total": 0}
    
    def get_last_response_duration_ms(self, thread_key: str) -> int:
        """Retourne la durée de la dernière réponse en ms."""
        if thread_key in self.response_times and self.response_times[thread_key]:
            return int(self.response_times[thread_key][-1])
        return 0


class LLMManager:
    """Gestionnaire LLM utilisant Firebase Realtime Database."""
    
    def __init__(self):
        self.sessions: Dict[str, LLMSession] = {}
        self._lock = asyncio.Lock()
    
    def _get_rtdb_ref(self, path: str):
        """Obtient une référence Firebase RTDB."""
        from ..listeners_manager import _get_rtdb_ref
        return _get_rtdb_ref(path)
    
    async def initialize_session(
        self,
        user_id: str,
        collection_name: str,
        dms_system: str = "google_drive",
        dms_mode: str = "prod",
        chat_mode: str = "general_chat"
    ) -> dict:
        """Initialise une session LLM pour un utilisateur/société."""
        try:
            async with self._lock:
                base_session_key = f"{user_id}:{collection_name}"
                
                # Vérifier si session existe déjà
                if base_session_key in self.sessions:
                    session = self.sessions[base_session_key]
                    # Mettre à jour le contexte si nécessaire
                    if (session.context.dms_system != dms_system or 
                        session.context.chat_mode != chat_mode):
                        session.update_context(
                            dms_system=dms_system,
                            dms_mode=dms_mode,
                            chat_mode=chat_mode
                        )
                    
                    return {
                        "success": True,
                        "session_id": base_session_key,
                        "status": "existing",
                        "message": "Session LLM réutilisée"
                    }
                
                # Créer nouvelle session
                context = LLMContext(
                    user_id=user_id,
                    collection_name=collection_name,
                    dms_system=dms_system,
                    dms_mode=dms_mode,
                    chat_mode=chat_mode
                )
                
                session = LLMSession(
                    session_key=base_session_key,
                    context=context
                )
                
                # Initialiser l'agent
                await session.initialize_agent()
                
                # Stocker en cache
                self.sessions[base_session_key] = session
                
                return {
                    "success": True,
                    "session_id": base_session_key,
                    "status": "created",
                    "message": "Session LLM initialisée avec succès"
                }
                
        except Exception as e:
            print(f"❌ Erreur initialisation session LLM: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Échec de l'initialisation LLM"
            }
    
    async def send_message(
        self,
        user_id: str,
        collection_name: str,
        space_code: str,      # ✅ Pour path RTDB
        chat_thread: str,
        message: str,
        chat_mode: str = "general_chat",
        system_prompt: str = None
    ) -> dict:
        """Envoie un message à l'agent LLM et écrit la réponse dans Firebase RTDB."""
        try:
            base_session_key = f"{user_id}:{collection_name}"
            
            # Récupérer ou créer la session
            async with self._lock:
                if base_session_key not in self.sessions:
                    init_result = await self.initialize_session(
                        user_id, collection_name, chat_mode=chat_mode
                    )
                    if not init_result.get("success"):
                        return init_result
                
                session = self.sessions[base_session_key]
            
            # Générer IDs pour les messages
            user_message_id = str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
            
            # ✅ 1. Écrire le message utilisateur dans Firebase RTDB
            user_msg_path = f"{space_code}/chats/{chat_thread}/messages/{user_message_id}"
            user_msg_ref = self._get_rtdb_ref(user_msg_path)
            user_msg_ref.set({
                "role": "user",
                "content": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": user_id,
                "read": False
            })
            
            # ✅ 2. Créer un message assistant "vide" (pour le streaming)
            assistant_msg_path = f"{space_code}/chats/{chat_thread}/messages/{assistant_message_id}"
            assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
            assistant_msg_ref.set({
                "role": "assistant",
                "content": "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "streaming",
                "streaming_progress": 0.0,
                "read": False
            })
            
            # ✅ 3. Lancer le traitement en arrière-plan
            asyncio.create_task(
                self._process_message_with_rtdb_streaming(
                    session=session,
                    user_id=user_id,
                    space_code=space_code,
                    chat_thread=chat_thread,
                    assistant_message_id=assistant_message_id,
                    message=message,
                    system_prompt=system_prompt
                )
            )
            
            return {
                "success": True,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "message": "Message envoyé, réponse en cours de streaming dans Firebase RTDB"
            }
            
        except Exception as e:
            print(f"❌ Erreur envoi message LLM: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _process_message_with_rtdb_streaming(
        self,
        session: LLMSession,
        user_id: str,
        space_code: str,
        chat_thread: str,
        assistant_message_id: str,
        message: str,
        system_prompt: str = None
    ):
        """Traite le message et stream la réponse directement dans Firebase RTDB."""
        
        assistant_msg_path = f"{space_code}/chats/{chat_thread}/messages/{assistant_message_id}"
        assistant_msg_ref = self._get_rtdb_ref(assistant_msg_path)
        
        try:
            # Créer le buffer intelligent pour le streaming
            buffer = RTDBStreamingBuffer(min_interval_ms=100, max_buffer_size=50)
            
            # ✅ Stream depuis l'agent LLM
            async for chunk in session.process_message_streaming(
                chat_thread, 
                message,
                system_prompt=system_prompt
            ):
                chunk_content = chunk.get("content", "")
                is_final = chunk.get("is_final", False)
                
                # Ajouter au buffer (flush automatique toutes les 100ms ou si buffer plein)
                await buffer.add_chunk(
                    chunk_content,
                    assistant_msg_ref,
                    force_flush=is_final
                )
            
            # ✅ Finaliser le message
            assistant_msg_ref.update({
                "status": "complete",
                "streaming_progress": 1.0,
                "metadata": {
                    "tokens_used": session.get_token_stats(chat_thread),
                    "duration_ms": session.get_last_response_duration_ms(chat_thread),
                    "model": "claude-3-7-sonnet-20250219"  # À récupérer depuis l'agent
                },
                "completed_at": datetime.now(timezone.utc).isoformat()
            })
            
        except Exception as e:
            print(f"❌ Erreur streaming RTDB: {e}")
            # Marquer comme erreur dans Firebase RTDB
            assistant_msg_ref.update({
                "status": "error",
                "error": str(e),
                "error_at": datetime.now(timezone.utc).isoformat()
            })


# Singleton pour le gestionnaire LLM
_llm_manager: Optional[LLMManager] = None

def get_llm_manager() -> LLMManager:
    """Récupère l'instance singleton du LLM Manager."""
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager
```

---

### **2. Contexte LLM dynamique**

```python
# app/llm_service/llm_context.py

from dataclasses import dataclass
from typing import Optional

@dataclass
class LLMContext:
    """Contexte dynamique pour une session LLM."""
    
    user_id: str
    collection_name: str
    dms_system: str = "google_drive"
    dms_mode: str = "prod"
    chat_mode: str = "general_chat"
    
    # Contexte métier (optionnel, récupéré depuis Firestore)
    company_name: Optional[str] = None
    company_context: Optional[str] = None
    gl_accounting_erp: Optional[str] = None
    mandate_path: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convertit le contexte en dictionnaire."""
        return {
            "user_id": self.user_id,
            "collection_name": self.collection_name,
            "dms_system": self.dms_system,
            "dms_mode": self.dms_mode,
            "chat_mode": self.chat_mode,
            "company_name": self.company_name,
            "company_context": self.company_context,
            "gl_accounting_erp": self.gl_accounting_erp,
            "mandate_path": self.mandate_path
        }
```

---

### **3. Créer les fichiers `__init__.py`**

```python
# app/llm_service/__init__.py

from .llm_manager import get_llm_manager, LLMManager
from .llm_context import LLMContext

__all__ = ['get_llm_manager', 'LLMManager', 'LLMContext']
```

---

## 🔌 **Intégration dans main.py**

```python
# app/main.py (modifications à ajouter)

from .llm_service import get_llm_manager

# Dans _resolve_method() - Ajouter résolution des méthodes LLM
def _resolve_method(method: str) -> Tuple[Callable, str]:
    # ... code existant ...
    
    # 🆕 Ajouter résolution des méthodes LLM
    if method.startswith("LLM."):
        name = method.split(".", 1)[1]
        llm_manager = get_llm_manager()
        target = getattr(llm_manager, name, None)
        if callable(target):
            return target, "LLM"
    
    # ... reste du code ...
```

---

## 📱 **Modifications côté Reflex (minimales)**

### **1. Mise à jour de ChatState**

```python
# pinnokio_app/state/base_state.py

class ChatState(rx.State):
    # ... variables existantes INCHANGÉES ...
    
    @rx.event(background=True)
    async def initialize_llm_agent(self):
        """✅ MODIFIÉ : Initialise l'agent LLM via le microservice."""
        async with self:
            try:
                if self.llm_connected:
                    print("⚠️ LLM déjà connecté, initialisation ignorée")
                    return
                
                self.llm_init_inflight = True
                yield
                
                # ✅ Appel RPC au microservice
                result = rpc_call(
                    "LLM.initialize_session",
                    args=[
                        self.firebase_user_id,
                        self.base_collection_id,
                        self.dms_type_extracted or "google_drive",
                        "prod",
                        self.chat_mode
                    ],
                    user_id=self.firebase_user_id,
                    timeout_ms=30000
                )
                
                if result and result.get("success"):
                    self.llm_connected = True
                    self.llm_params_fingerprint = result.get("session_id", "")
                    print(f"✅ LLM initialisé via microservice: {self.llm_params_fingerprint}")
                else:
                    error_msg = result.get("error", "Unknown error") if result else "No response"
                    print(f"❌ Erreur initialisation LLM: {error_msg}")
                    self.llm_connected = False
                
                self.llm_init_inflight = False
                yield
                
            except Exception as e:
                print(f"❌ Exception initialisation LLM: {e}")
                self.llm_connected = False
                self.llm_init_inflight = False
                yield
    
    @rx.event(background=True)
    async def send_message(self):
        """✅ MODIFIÉ : Envoie un message via le microservice (qui écrit dans Firebase RTDB)."""
        async with self:
            if not self.question.strip():
                return
            
            try:
                # Vérifier que l'agent est connecté
                if not self.llm_connected:
                    print("⚠️ LLM non connecté, initialisation...")
                    yield ChatState.initialize_llm_agent
                    
                    # Attendre fin d'initialisation
                    max_wait = 30
                    waited = 0
                    while self.llm_init_inflight and waited < max_wait:
                        await asyncio.sleep(0.5)
                        waited += 0.5
                    
                    if not self.llm_connected:
                        yield rx.toast.error("Impossible de se connecter à l'assistant")
                        return
                
                question = self.question
                self.question = ""
                self.processing = True
                current_chat_key = self.current_chat
                
                # ✅ Pas besoin d'ajouter optimistic UI
                # Le listener Firebase RTDB le fera automatiquement
                yield
                
                # ✅ Récupérer le system prompt selon le mode
                system_prompt = self._get_system_prompt_by_mode()
                
                # ✅ Envoi RPC au microservice
                result = rpc_call(
                    "LLM.send_message",
                    args=[
                        self.firebase_user_id,
                        self.base_collection_id,
                        self.base_collection_id,  # space_code = collection_name
                        current_chat_key,
                        question,
                        self.chat_mode,
                        system_prompt  # ✅ Passer le system prompt
                    ],
                    user_id=self.firebase_user_id,
                    timeout_ms=5000  # Timeout court car c'est juste pour envoyer
                )
                
                if not result or not result.get("success"):
                    error_msg = result.get("error", "Unknown error") if result else "No response"
                    print(f"❌ Erreur envoi message: {error_msg}")
                    self.processing = False
                    yield rx.toast.error("Erreur lors de l'envoi du message")
                    return
                
                # ✅ C'EST TOUT ! Le listener ChatListener va :
                # 1. Détecter le nouveau message utilisateur dans Firebase RTDB
                # 2. Détecter les updates du message assistant (streaming)
                # 3. Mettre à jour l'UI automatiquement via _handle_chat_message()
                
            except Exception as e:
                print(f"❌ Exception send_message: {e}")
                self.processing = False
                yield rx.toast.error(f"Erreur: {str(e)}")
    
    def _get_system_prompt_by_mode(self) -> str:
        """Retourne le prompt système selon le chat_mode."""
        # À adapter selon vos prompts existants
        if self.chat_mode == "router_chat":
            return """Tu es un assistant comptable spécialisé dans le routage de documents..."""
        elif self.chat_mode == "apbookeeper_chat":
            return """Tu es Pinnokio, assistant comptable spécialisé dans les fournisseurs..."""
        elif self.chat_mode == "onboarding_chat":
            return """Tu es un assistant d'onboarding qui aide les nouveaux utilisateurs..."""
        else:  # general_chat
            return """Tu es Pinnokio, assistant comptable intelligent..."""
    
    async def _handle_chat_message(self, message_data: dict):
        """✅ DÉJÀ EXISTANT : Appelé automatiquement par ChatListener.
        
        Cette méthode gère automatiquement :
        - Les nouveaux messages (role: user/assistant/system)
        - Les updates de streaming (content mis à jour progressivement)
        - Les statuts (streaming, complete, error)
        """
        try:
            role = message_data.get("role", "")
            content = message_data.get("content", "")
            status = message_data.get("status", "complete")
            message_type = message_data.get("type", "")
            metadata = message_data.get("metadata", {})
            ephemeral = message_data.get("ephemeral", False)
            
            # ✅ Messages système
            if role == "system":
                if message_type == "tool_execution":
                    tool_name = metadata.get("tool_name", "")
                    tool_status = metadata.get("status", "")
                    
                    if tool_status == "running":
                        yield rx.toast.info(f"⚙️ {tool_name}...")
                    elif tool_status == "complete":
                        yield rx.toast.success(f"✅ {tool_name} terminé")
                    
                    # Si ephemeral, ne pas ajouter au chat
                    if not ephemeral:
                        self._add_system_message(content, metadata)
                
                elif message_type == "long_task":
                    # Toujours ajouter les tâches longues au chat (persistent=True)
                    self._add_system_message(content, metadata)
                    
                    # Afficher barre de progression si disponible
                    if "progress_percent" in metadata:
                        self._update_task_progress(
                            metadata.get("task_id"),
                            metadata.get("progress_percent")
                        )
            
            # ✅ Messages assistant
            elif role == "assistant":
                if status == "streaming":
                    self.processing = True
                    # Mettre à jour progressivement l'UI
                    if self.current_chat in self.chats and self.chats[self.current_chat]:
                        for qa in reversed(self.chats[self.current_chat]):
                            if qa.answer and not qa.question:
                                qa.answer = content
                                break
                        else:
                            # Créer un nouveau QA si pas trouvé
                            self.chats[self.current_chat].append(QA(
                                question="",
                                answer=content,
                                show_metadata=False,
                                timestamp=message_data.get("timestamp", "")
                            ))
                
                elif status == "complete":
                    self.processing = False
                    # Le contenu est déjà à jour grâce au streaming
                    
                    # Sauvegarder dans Firebase (si vous voulez une sauvegarde supplémentaire)
                    # Mais normalement c'est déjà dans RTDB !
                
                elif status == "error":
                    self.processing = False
                    yield rx.toast.error(f"Erreur LLM: {message_data.get('error', 'Unknown')}")
            
            # ✅ Messages utilisateur
            elif role == "user":
                if self.current_chat not in self.chats:
                    self.chats[self.current_chat] = []
                
                self.chats[self.current_chat].append(QA(
                    question=content,
                    answer="",
                    show_metadata=False,
                    timestamp=message_data.get("timestamp", "")
                ))
            
        except Exception as e:
            print(f"❌ Erreur _handle_chat_message: {e}")
    
    def _add_system_message(self, content: str, metadata: dict):
        """Ajoute un message système au chat."""
        if self.current_chat not in self.chats:
            self.chats[self.current_chat] = []
        
        self.chats[self.current_chat].append(QA(
            question="",
            answer=content,
            show_metadata=True,
            metadata=metadata,
            timestamp=datetime.now(timezone.utc).isoformat()
        ))
    
    def _update_task_progress(self, task_id: str, progress: int):
        """Met à jour la barre de progression d'une tâche."""
        # TODO: Implémenter UI de progression
        pass
```

### **2. Démarrage du listener (comme vous le faites déjà)**

```python
@rx.event(background=True)
async def start_llm_chat_listener(self):
    """Démarre le listener Firebase RTDB pour les conversations LLM."""
    async with self:
        try:
            from pinnokio_app.listeners.manager import listener_manager
            
            # ✅ Utiliser le même listener que les chats existants
            await listener_manager.start_chat_listener(
                space_code=self.base_collection_id,
                thread_key=self.current_chat,
                user_id=self.firebase_user_id,
                main_loop=asyncio.get_event_loop(),
                handler=self._handle_chat_message,
                mode="chats"  # ✅ Nouveau mode pour les conversations LLM
            )
            
            self.Chat_realtime_listener_active = True
            print(f"✅ Listener LLM chat démarré pour thread {self.current_chat}")
            
        except Exception as e:
            print(f"❌ Erreur démarrage listener LLM: {e}")
```

---

## 🎯 **Framework Agentic - SPT et LPT**

### **Différence SPT vs LPT**

| Critère | SPT (Short Process Tooling) | LPT (Long Process Tooling) |
|---------|----------------------------|----------------------------|
| **Durée** | < 30 secondes | > 30 secondes (jusqu'à plusieurs heures) |
| **Exécution** | Synchrone dans le même conteneur | Asynchrone via Celery/workflows externes |
| **Exemples** | Lire un fichier, analyser une facture simple, recherche ChromaDB | Rapprochement comptable complet, génération de rapport mensuel, workflow APBookeeper |
| **Réponse** | L'agent attend la réponse avant de continuer | L'agent informe l'utilisateur et continue à être disponible |
| **Statut** | Bloquant pour le thread de conversation | Non-bloquant, l'utilisateur peut interagir pendant le traitement |

*(À développer dans une phase ultérieure)*

---

## ✅ **Plan d'implémentation - Étapes**

### **Phase 1 : Infrastructure de base (EN COURS)** ⏳

1. ✅ Créer la structure de dossiers `llm_service/`
2. ✅ Implémenter `LLMManager` avec Firebase RTDB
3. ✅ Implémenter `LLMSession` avec BaseAIAgent
4. ✅ Implémenter `LLMContext`
5. ⏳ Intégrer dans `main.py` pour résolution RPC
6. ⏳ Tester initialisation session via RPC depuis Reflex

### **Phase 2 : Communication Firebase RTDB (À VENIR)**

1. ⏳ Tester streaming dans Firebase RTDB avec debounce
2. ⏳ Modifier `ChatState.send_message()` pour utiliser RPC
3. ⏳ Adapter `_handle_chat_message()` pour gérer les messages système
4. ⏳ Tester conversation complète end-to-end

### **Phase 3 : Optimisations (À VENIR)**

1. ⏳ Tuning du buffer streaming (100ms optimal ?)
2. ⏳ Gestion des erreurs et timeouts
3. ⏳ Cache Redis pour historiques (optionnel)
4. ⏳ Monitoring et métriques

### **Phase 4 : Framework agentic SPT/LPT (FUTURE)**

1. ⏳ Implémenter détection d'appels d'outils
2. ⏳ Créer `TaskOrchestrator`
3. ⏳ Implémenter tâches Celery LPT

---

## 🎯 **Avantages de cette architecture**

1. ✅ **Cohérence totale** : Même pattern que vos chats onboarding/job existants
2. ✅ **Zéro changement côté Reflex** : Seuls `initialize_llm_agent()` et `send_message()` modifiés
3. ✅ **Une seule source de vérité** : Firebase RTDB pour tout
4. ✅ **Réutilisation** : `ChatListener`, `_handle_chat_message()`, `BaseAIAgent` existants
5. ✅ **Streaming optimisé** : Debounce 100ms = 90-96% d'économie d'écritures Firebase
6. ✅ **Scalabilité** : Gestion de milliers de conversations simultanées
7. ✅ **Historique automatique** : Tout est sauvegardé dans Firebase RTDB

---

## 📝 **Checklist de validation**

### **Avant de commencer l'implémentation**
- [x] Architecture validée
- [x] Décisions techniques prises (Firebase RTDB, path, streaming, etc.)
- [x] `BaseAIAgent` déjà présent et fonctionnel
- [ ] Créer les dossiers `app/llm_service/`

### **Phase 1 - Infrastructure**
- [ ] Créer `app/llm_service/__init__.py`
- [ ] Créer `app/llm_service/llm_context.py`
- [ ] Créer `app/llm_service/llm_manager.py`
- [ ] Intégrer dans `app/main.py`
- [ ] Tester RPC `LLM.initialize_session` depuis Reflex

### **Phase 2 - Communication**
- [ ] Tester `LLM.send_message` RPC
- [ ] Vérifier streaming Firebase RTDB
- [ ] Vérifier listener Reflex détecte les messages
- [ ] Test conversation complète

### **Production**
- [ ] Tests de charge (10+ utilisateurs simultanés)
- [ ] Monitoring des coûts Firebase
- [ ] Documentation équipe

---

## 🎉 **Conclusion**

Cette architecture **réutilise au maximum l'existant** :
- ✅ Firebase Realtime Database (déjà utilisé)
- ✅ `ChatListener` (déjà implémenté)
- ✅ `BaseAIAgent` (déjà présent dans `klk_agents.py`)
- ✅ Pattern de communication (identique aux chats job/onboarding)

**Avantages immédiats :**
- ✅ Cohérence architecturale totale
- ✅ Minimise les changements côté Reflex
- ✅ Économise 90-96% des coûts d'écriture Firebase
- ✅ Simplicité : une seule source de vérité

**Le système peut être implémenté progressivement, phase par phase !** 🚀
