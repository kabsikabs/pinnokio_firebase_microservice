# 🏗️ Architecture des Agents - Pinnokio Agentic Workflow

## 📊 Vue d'ensemble de l'architecture

Cette documentation décrit l'architecture complète et optimisée du système d'agents Pinnokio, incluant la gestion des workflows LPT, le mode UI/Backend, et la hiérarchie multi-niveaux des agents.

---

## 🎯 Architecture Multi-Niveaux

```
┌───────────────────────────────────────────────────────────────────┐
│ NIVEAU 0 : Agent Principal (PinnokioBrain)                        │
│ ─────────────────────────────────────────────────────────────────│
│ Rôle : Orchestration stratégique, compréhension mission globale   │
│ Vision : "Quoi faire ?" (pas comment le faire)                    │
│ Outils : SPT Agents (Niveau 1) + LPT Managers (Niveau 2)         │
│ Prompt : Vision d'ensemble, capacités, mission                    │
└───────────────────────────────────────────────────────────────────┘
                          ↓ Délégation ↓
        ┌─────────────────┴──────────────────┬────────────────────┐
        ↓                                     ↓                     ↓
┌──────────────────┐           ┌──────────────────────┐   ┌────────────────┐
│ NIVEAU 1         │           │ NIVEAU 2             │   │ Core Tools     │
│ SPT Agents       │           │ LPT HTTP Managers    │   │                │
│ (Court < 30s)    │           │ (Long > 30s)         │   ├────────────────┤
├──────────────────┤           ├──────────────────────┤   │ TERMINATE_TASK │
│ • JobManager     │           │ • APBookkeeper       │   │ GET_FIREBASE   │
│ • TaskManager    │           │ • Banker             │   │ SEARCH_CHROMA  │
│ • ContextManager │           │ • Router             │   │                │
│                  │           │ • AdminManager       │   └────────────────┘
│ Framework:       │           │ • ERPManager         │
│ agent_workflow   │           │                      │
│ + Chat history   │           │ HTTP + Callback      │
│ + Exit tool      │           │ + Stop tool          │
└──────────────────┘           └──────────────────────┘
```

**Principes clés** :
- **Niveau 0** : Gestion globale (LLMSessionManager)
- **Niveau 1** : Session utilisateur (LLMSession par user/société)
- **Niveau 2** : Traitement thread (PinnokioBrain par conversation)

---

## 🔄 Hiérarchie des instances

```
┌─────────────────────────────────────────────────────────────┐
│                  LLMSessionManager                           │  NIVEAU 0
│  Singleton global - Gère toutes les sessions                │
│  Responsabilité: Créer/gérer les sessions par user/company  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ sessions: Dict[session_key, LLMSession]
                       │ session_key = "user_id:collection_name"
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    LLMSession                                │  NIVEAU 1
│  Clé: "user_id:collection_name"                            │
│  Durée de vie: Tant que user actif dans cette société      │
│  1 instance PAR UTILISATEUR + SOCIÉTÉ                       │
├─────────────────────────────────────────────────────────────┤
│ 📦 Conteneurs:                                              │
│  • agent: BaseAIAgent (1 seul, partagé)                    │
│  • brains: Dict[thread_key, PinnokioBrain]  ⭐ NOUVEAU     │
│  • thread_contexts: Dict[thread_key, context]  ⭐ NOUVEAU  │
│  • conversations: Dict[thread_key, messages]                │
│  • active_tasks: Dict[thread_key, tasks]                   │
│                                                              │
│ 🎯 Responsabilités:                                         │
│  • Gérer BaseAIAgent (1 par session)                       │
│  • Gérer PinnokioBrain par thread (persistant)            │
│  • Cache contexte LPT (évite requêtes Firebase)            │
│  • Historique par thread                                    │
│  • Métriques et timing                                      │
└──────────────┬──────────────────────────────────────────────┘
               │
               ├─→ self.agent: BaseAIAgent
               │        └─→ Providers (Anthropic, OpenAI...)
               │
               └─→ self.brains[thread_key]: PinnokioBrain
                        └─→ Réutilise self.agent (pas de doublon)
```

---

## 🧩 Composants détaillés

### 1. **LLMSession** (Gestionnaire de session)

**Fichier**: `app/llm_service/llm_manager.py`

**Cycle de vie**: Créé à la première connexion de l'utilisateur à une société, persiste jusqu'à déconnexion ou expiration

**Attributs clés**:
```python
class LLMSession:
    session_key: str                  # "user_id:collection_name"
    agent: BaseAIAgent                # Agent IA partagé
    conversations: Dict[str, list]    # Historique par thread
    
    # ⭐ NOUVEAUX ATTRIBUTS
    brains: Dict[str, PinnokioBrain]  # {thread_key: brain}
    thread_contexts: Dict[str, Tuple[Dict, float]]  # Cache contexte LPT
    context_cache_ttl: int = 300      # TTL cache: 5 minutes
```

**Responsabilités**:
- ✅ Créer et gérer **1 seul** `BaseAIAgent` par session
- ✅ Stocker **1** `PinnokioBrain` par thread (réutilisable entre messages)
- ✅ Cacher les contextes LPT par thread (évite requêtes Firebase redondantes)
- ✅ Gérer l'historique des conversations
- ✅ Tracking des tâches actives

---

### 2. **BaseAIAgent** (Moteur IA)

**Fichier**: `app/llm/klk_agents.py`

**Cycle de vie**: Créé avec `LLMSession`, partagé par tous les threads de cette session

**Responsabilités**:
- ✅ Gérer les providers IA (Anthropic, OpenAI, Gemini, DeepSeek, Perplexity)
- ✅ Gérer `chat_history` par provider
- ✅ Comptabilité des tokens (`get_total_context_tokens`)
- ✅ Streaming des réponses (`process_tool_use_streaming`)
- ✅ Exécution des outils (tool use)

**⚠️ IMPORTANT**: 
- **1 seul** `BaseAIAgent` par `LLMSession`
- **Partagé** par tous les `PinnokioBrain` de cette session
- **Pas de doublon** !

---

### 3. **PinnokioBrain** (Orchestrateur - Niveau 0)

**Fichier**: `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py`

**Cycle de vie**: Créé au premier message d'un thread, **réutilisé** pour tous les messages suivants du même thread

**Responsabilités**:
- ✅ Orchestrer le workflow agentic (Agent Principal)
- ✅ Créer le system prompt stratégique
- ✅ Créer les outils (SPT Agents + LPT Managers + Core Tools)
- ✅ Gérer plans et approbations
- ✅ Générer résumés de conversation
- ✅ **Stocker le contexte utilisateur** (mandate_path, dms_system, etc.) ⭐ NOUVEAU

**⭐ Contexte Utilisateur (user_context)** :
```python
class PinnokioBrain:
    def __init__(self, ...):
        self.user_context: Optional[Dict[str, Any]] = None
        # Contient : mandate_path, dms_system, communication_mode, 
        #            client_uuid, company_name, drive_space_parent_id, bank_erp
    
    async def load_user_context(self, thread_key: str, session=None):
        """Charge le contexte utilisateur depuis Firebase (avec cache session)"""
        # 1. Vérifier cache session (TTL 5min)
        # 2. Si absent/expiré: Fetch Firebase
        # 3. Stocker dans self.user_context
    
    def get_user_context(self) -> Dict[str, Any]:
        """Retourne le contexte stocké (utilisé par SPT et LPT)"""
        return self.user_context or {}
```

**Flux d'initialisation** :
```python
# CRÉATION/RÉCUPÉRATION DU BRAIN
if thread_key not in session.brains:
    brain = PinnokioBrain(...)
    brain.agent = session.agent  # ⭐ Partage agent
    session.brains[thread_key] = brain
    
    # ⭐ Charger le contexte utilisateur immédiatement
    await brain.load_user_context(thread_key, session)
else:
    brain = session.brains[thread_key]
    
    # ⭐ Recharger le contexte (avec cache pour éviter Firebase redondant)
    await brain.load_user_context(thread_key, session)
```

**Avantages** :
- ✅ Contexte disponible pour **TOUS** les outils (SPT et LPT)
- ✅ Cache session (TTL 5min) évite requêtes Firebase redondantes
- ✅ Rechargement automatique si contexte expiré
- ✅ Mode Backend fonctionne car contexte chargé dans le brain

---

### 4. **LPTClient** (Client outils longs)

**Fichier**: `app/pinnokio_agentic_workflow/tools/lpt_client.py`

**Cycle de vie**: Créé à chaque appel de `create_workflow_tools()`

**Responsabilités**:
- ✅ Définir les outils LPT (APBookkeeper, Router, Banker)
- ✅ Construire automatiquement les payloads complets
- ✅ **Récupérer le contexte utilisateur avec cache** ⭐ NOUVEAU
- ✅ Envoyer les requêtes HTTP vers agents externes
- ✅ Sauvegarder les tâches dans Firebase

**⭐ Cache contexte** (évite requêtes Firebase redondantes):

```python
async def _get_user_context_data(self, user_id, company_id, thread_key, session):
    # 1. Vérifier cache session
    if thread_key in session.thread_contexts:
        context, timestamp = session.thread_contexts[thread_key]
        if time.time() - timestamp < session.context_cache_ttl:
            return context  # ← Retour immédiat, pas de Firebase
    
    # 2. Si absent/expiré: Fetch Firebase
    context = await _fetch_from_firebase(...)
    
    # 3. Sauvegarder dans cache
    session.thread_contexts[thread_key] = (context, time.time())
    return context
```

**Avantages du cache**:
- ✅ **1 requête Firebase** au lieu de 3-9 par conversation
- ✅ Cache par thread (changement de chat = nouveau cache)
- ✅ TTL 5 minutes (balance performance/cohérence)
- ✅ User déconnecte = registre Redis reste 24h (LPTs continuent)

---

## 🔄 Mode UI / Backend (Dual-Mode Architecture)

### Infrastructure Firebase Duale

**IMPORTANT** : Le système utilise **DEUX bases de données Firebase distinctes** :

```
┌─────────────────────────────────────────────────────────────────────┐
│ FIREBASE FIRESTORE (FirebaseManagement)                             │
├─────────────────────────────────────────────────────────────────────┤
│ Utilisation : Données structurées et tâches LPT                    │
│                                                                 │
│ Structure des données :                                             │
│ clients/{user_id}/workflow_pinnokio/{thread_key}                    │
│   └── tasks/{task_id} (tâches LPT, métadonnées)                   │
│                                                                 │
│ Avantages :                                                            │
│ • Requêtes complexes et filtres                                    │
│ • Persistence fiable des tâches                                   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ FIREBASE REALTIME DATABASE (FirebaseRealtimeChat)                  │
├─────────────────────────────────────────────────────────────────────┤
│ Utilisation : Messages et conversations temps réel                 │
│                                                                 │
│ Structure des données :                                             │
│ {collection_name}/job_chats/{thread_key}/messages                  │
│   └── Messages avec timestamps et métadonnées                     │
│                                                                 │
│ Avantages :                                                            │
│ • Synchronisation temps réel                                       │
│ • Historique conversationnel                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Modes d'exécution

Le microservice supporte **2 modes d'exécution** en fonction de la connexion utilisateur :

```
┌────────────────────────────────────────────────────────────────┐
│ MODE UI (User Connecté)                                        │
├────────────────────────────────────────────────────────────────┤
│ Détection : heartbeat < 5 minutes dans UnifiedRegistry        │
│                                                                 │
│ Comportement :                                                  │
│ • Streaming WebSocket activé ⚡                                │
│ • Broadcast stream_start, stream_chunk, stream_complete       │
│ • Persistence RTDB (toujours activée)                         │
│                                                                 │
│ Utilisé pour :                                                  │
│ • Conversations en temps réel                                  │
│ • Feedback immédiat à l'utilisateur                           │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│ MODE BACKEND (User Déconnecté)                                 │
├────────────────────────────────────────────────────────────────┤
│ Détection : heartbeat > 5 minutes ou absent                   │
│                                                                 │
│ Comportement :                                                  │
│ • Streaming WebSocket désactivé ❌                             │
│ • Pas de broadcast (économie ressources)                       │
│ • Persistence RTDB uniquement 💾                               │
│                                                                 │
│ Utilisé pour :                                                  │
│ • Workflows automatisés (tâches planifiées)                   │
│ • Continuation après LPT callback                              │
│ • Traitement en arrière-plan                                   │
└────────────────────────────────────────────────────────────────┘
```

### Implémentation

**Détection du mode** (`unified_registry.py`) :
```python
def is_user_connected(self, user_id: str) -> bool:
    """Vérifie si user connecté (heartbeat < 5 min)"""
    registry_data = self.get_user_registry(user_id)
    last_heartbeat = registry_data.get("heartbeat", {}).get("last_heartbeat")
    age_seconds = (now - last_heartbeat_dt).total_seconds()
    return age_seconds < 300  # 5 minutes

def get_user_connection_mode(self, user_id: str) -> str:
    """Retourne 'UI' ou 'BACKEND'"""
    return "UI" if self.is_user_connected(user_id) else "BACKEND"
```

**Utilisation dans le workflow** (`llm_manager.py`) :
```python
async def _resume_workflow_after_lpt(..., user_connected: bool):
    mode = "UI" if user_connected else "BACKEND"
    
    # Streaming conditionnel
    async for chunk in session.process_message_streaming(...):
        accumulated_content += chunk.get("content", "")
        
        # ⭐ Broadcast UNIQUEMENT si Mode UI
        if user_connected:
            await hub.broadcast(user_id, {
                "type": "llm_stream_chunk",
                "payload": {"chunk": chunk_content}
            })
    
    # ⭐ Persistence RTDB TOUJOURS (Mode UI et Backend)
    assistant_msg_ref.set({
        "role": "assistant",
        "content": accumulated_content,
        "status": "complete"
    })
```

---

## 🔄 Callbacks LPT (Workflow Continuation)

### Principe

Les **LPT (Long Process Tooling)** sont des tâches longues (>30s) exécutées par des agents externes (APBookkeeper, Router, Banker). Lorsqu'un LPT termine, il envoie un **callback** au microservice pour reprendre le workflow.

### Architecture du Callback

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Agent Principal (PinnokioBrain)                              │
│    └─→ Décide de lancer LPT_APBookkeeper                       │
│                                                                  │
│ 2. LPTClient.launch_apbookeeper()                              │
│    ├─→ Récupère contexte depuis brain.get_user_context()      │
│    ├─→ Construit payload complet                               │
│    ├─→ Envoie requête HTTP vers agent externe                 │
│    └─→ Sauvegarde task dans Firebase                           │
│                                                                  │
│ 3. Agent Externe (APBookkeeper)                                 │
│    ├─→ Traite les factures (peut prendre 5-30 minutes)        │
│    └─→ Envoie callback : POST /lpt/callback                    │
│                                                                  │
│ 4. Microservice reçoit callback                                 │
│    ├─→ Vérifie company_id, session existence                  │
│    ├─→ Détecte mode (UI/Backend)                              │
│    └─→ Lance _resume_workflow_after_lpt()                      │
│                                                                  │
│ 5. _resume_workflow_after_lpt()                                │
│    ├─→ Récupère/crée brain pour thread_key                    │
│    ├─→ Charge user_context dans le brain                       │
│    ├─→ Construit message de continuation                       │
│    ├─→ Exécute workflow (streaming conditionnel selon mode)   │
│    └─→ Persiste dans RTDB                                      │
└─────────────────────────────────────────────────────────────────┘
```

### Point d'Entrée : `/lpt/callback`

**Fichier** : `app/main.py`

```python
@app.post("/lpt/callback")
async def lpt_callback(req: LPTCallbackRequest, ...):
    # ⭐ ÉTAPE 1 : Récupérer la tâche sauvegardée dans Firebase
    workflow_path = f"clients/{req.user_id}/workflow_pinnokio"
    doc_ref = get_firestore().collection(workflow_path).document(req.thread_key)
    doc = doc_ref.get()

    # 2. Extraire company_id depuis le document
    company_id = doc.get("company_id")

    # 3. Vérifier session LLM existe
    session_key = f"{req.user_id}:{company_id}"
    if session_key not in llm_manager.sessions:
        # Session expirée → Pas de reprise workflow
        return {"ok": True, "message": "Session expirée"}

    # 4. Détecter mode (UI/Backend)
    user_connected = registry.is_user_connected(req.user_id)
    mode = "UI" if user_connected else "BACKEND"

    # 5. Lancer reprise workflow en arrière-plan
    asyncio.create_task(
        llm_manager._resume_workflow_after_lpt(
            user_id=req.user_id,
            company_id=company_id,
            thread_key=req.thread_key,
            task_id=req.task_id,
            task_data=task_data,
            lpt_result=req.result,
            user_connected=user_connected
        )
    )
```

### Données Persistées dans Firebase

**DEUX STRUCTURES DISTINCTES :**

#### **A. Données de tâches LPT (Firestore)**
**Chemin** : `clients/{user_id}/workflow_pinnokio/{thread_key}`

```json
{
  "thread_key": "chat_abc123",
  "user_id": "uid_user",
  "company_id": "company_456",
  "tasks": {
    "task_abc123": {
      "task_id": "task_abc123",
      "task_type": "APBookkeeper",
      "status": "completed",
      "result": {
        "summary": "15 factures traitées",
        "processed_items": 15
      },
      "created_at": "2025-01-15T10:00:00Z",
      "completed_at": "2025-01-15T10:25:00Z"
    }
  }
}
```

#### **B. Messages conversationnels (Realtime Database)**
**Chemin** : `{collection_name}/job_chats/{thread_key}/messages`

```json
{
  "msg_001": {
    "content": "Bonjour, traitez ces factures",
    "sender_id": "user_123",
    "timestamp": "2025-01-15T10:00:00Z",
    "message_type": "USER_MESSAGE",
    "read": false
  },
  "msg_002": {
    "content": "✅ 15 factures saisies avec succès !",
    "sender_id": "system",
    "timestamp": "2025-01-15T10:25:00Z",
    "message_type": "LPT_RESULT",
    "read": false
  }
}
```

**Utilisation** :
- ✅ **Firestore** : Tâches LPT, métadonnées, suivi d'exécution
- ✅ **RTDB** : Messages temps réel, historique conversationnel
- ✅ Reprise workflow même si session a expiré (recrée brain)

---

## 📐 Structure Agent_Workflow (Framework Unifié)

### ⚠️ État Actuel vs. Architecture Future

**IMPORTANT** : Il existe actuellement deux types de SPT dans le système :

1. **SPT ACTUELS (Implémentés)** : Outils simples appelés par PinnokioBrain
   - Fichier : `tools/spt_tools.py`
   - Ce sont des **fonctions simples** (GET_FIREBASE_DATA, SEARCH_CHROMADB, GET_USER_CONTEXT)
   - **Pas d'agent autonome**, pas de boucle de tours, pas de chat_history
   - Exécutés directement par le PinnokioBrain lors de son workflow
   - Gestion de tokens : **héritée du PinnokioBrain** (budget global 80K tokens)

2. **SPT AGENTS (Architecture Future)** : Agents autonomes avec workflow
   - Non implémentés actuellement
   - Suivraient le framework `agent_workflow` de la documentation
   - Auraient leur propre boucle de tours, chat_history isolé, gestion de tokens
   - Exemple : JobManager, TaskManager, ContextManager

Cette section décrit **les deux modèles** pour comprendre l'évolution architecturale.

---

### 🔧 Modèle Actuel : SPT Outils Simples (Implémenté)

Dans l'implémentation actuelle, les SPT sont des **outils directs** du PinnokioBrain, pas des agents autonomes.

```
┌───────────────────────────────────────────────────────────────────┐
│                  AGENT PRINCIPAL (PinnokioBrain)                   │
│                  Niveau 0 - Orchestration stratégique              │
│                  • Gestion tokens : 80K budget                     │
│                  • Boucle de tours avec max_turns=20               │
│                  • process_tool_use_streaming                      │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               │ Appelle directement les outils SPT
                               │ (pas d'agent intermédiaire)
                               │
        ┌──────────────────────┴──────────────────────┐
        ↓                                             ↓
┌──────────────────┐                       ┌──────────────────────┐
│ SPT OUTILS       │                       │ LPT HTTP Managers     │
│ (Court < 5s)     │                       │ (Long > 30s)          │
├──────────────────┤                       ├──────────────────────┤
│ • GET_FIREBASE   │                       │ • APBookkeeper        │
│ • SEARCH_CHROMA  │                       │ • Banker              │
│ • GET_CONTEXT    │                       │ • Router              │
│                  │                       │ • AdminManager        │
│ Fonctions async  │                       │ HTTP + Callback       │
│ Retour direct    │                       │ + Stop tool           │
└──────────────────┘                       └──────────────────────┘
```

**Caractéristiques des SPT actuels** :

- **Type** : Fonctions async simples (pas d'agent autonome)
- **Fichier** : `app/pinnokio_agentic_workflow/tools/spt_tools.py`
- **Classe** : `SPTTools`
- **Outils** :
  ```python
  GET_FIREBASE_DATA(path, query_filters) → Dict
  SEARCH_CHROMADB(query, n_results) → Dict
  GET_USER_CONTEXT() → Dict
  ```
- **Exécution** : Synchrone/Async dans le tour actuel du PinnokioBrain
- **Historique** : Utilise le `chat_history` du PinnokioBrain (partagé)
- **Gestion tokens** : Hérite du budget du PinnokioBrain (80K tokens)
- **Résumé** : Géré par le PinnokioBrain quand budget atteint
- **Contexte** : Accès via `self.brain.get_user_context()`

**Flux d'exécution actuel** :

```python
# Dans llm_manager.py : _process_message_with_agentic_streaming()

# 1. Configuration du budget tokens
max_tokens_budget = 80000  # 80K pour le PinnokioBrain
max_turns = 20

# 2. Boucle de tours
while turn_count < max_turns and not mission_completed:
    turn_count += 1
    
    # ═══ VÉRIFICATION BUDGET TOKENS ═══
    tokens_before = brain.agent.get_total_context_tokens(brain.default_provider)
    
    # Si budget dépassé, générer résumé et RÉINITIALISER
    if tokens_before >= max_tokens_budget:
        summary = brain.generate_conversation_summary(thread_key, tokens_before)
        tokens_after_reset = brain.reset_context_with_summary(summary)
        # La conversation continue transparemment pour l'utilisateur
    
    # ═══ APPEL AGENT AVEC OUTILS SPT ═══
    async for event in brain.agent.process_tool_use_streaming(
        content=current_input,
        tools=tools,  # ← Inclut GET_FIREBASE_DATA, SEARCH_CHROMADB, etc.
        tool_mapping=tool_mapping,
        ...
    ):
        if event["type"] == "tool_use":
            tool_name = event["tool_name"]
            
            # Si SPT tool (ex: GET_FIREBASE_DATA)
            if tool_name == "GET_FIREBASE_DATA":
                # Exécuté IMMÉDIATEMENT dans ce tour
                result = await spt_tools.get_firebase_data(path, filters)
                # Résultat ajouté au chat_history du brain
                # Prochain tour continue avec ce résultat
```

**Paramétrage des SPT actuels** :

Les SPT sont paramétrés lors de la création des outils dans `pinnokio_brain.py` :

```python
# Dans pinnokio_brain.py : create_workflow_tools()

def create_workflow_tools(self, thread_key: str, session=None):
    # Créer les outils SPT
    spt_tools = SPTTools(
        firebase_user_id=self.firebase_user_id,
        collection_name=self.collection_name,
        brain=self  # ⭐ Passer le brain pour accès au contexte
    )
    
    # Obtenir les définitions d'outils
    spt_tools_list = spt_tools.get_tools_definitions()
    spt_tools_mapping = spt_tools.get_tools_mapping()
    
    # Les outils sont ajoutés au tool_set du PinnokioBrain
    tool_set = spt_tools_list + lpt_tools_list + [terminate_tool]
    tool_mapping = {**spt_tools_mapping, **lpt_tools_mapping}
    
    return tool_set, tool_mapping
```

**Avantages du modèle actuel** :
- ✅ Simple à implémenter
- ✅ Pas de duplication d'agents
- ✅ Gestion de tokens centralisée
- ✅ Accès direct au contexte du brain

**Limitations du modèle actuel** :
- ❌ Pas de raisonnement autonome pour les SPT
- ❌ Pas d'historique isolé par SPT
- ❌ Tous les appels SPT consomment le budget du brain
- ❌ Pas de spécialisation possible (tous utilisent le même agent)

---

### 🚀 Modèle Futur : SPT Agents Autonomes (Architecture Cible)

Ce modèle n'est **pas encore implémenté** mais décrit l'architecture cible inspirée de `DOCUMENTATION_FRAMEWORK_AGENTIC_WORKFLOW.md`.

### Architecture des SPT Agents

```
┌─────────────────────────────────────────────────────────────────┐
│                  AGENT PRINCIPAL (PinnokioBrain)                 │
│                  Appelle un agent SPT comme outil                │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                ┌──────────────▼───────────────┐
                │     AGENT SPT (ex: JobManager) │
                │     Niveau 1 - Court < 30s     │
                └──────────────┬───────────────┘
                               │
        ┌──────────────────────┴──────────────────────┐
        │ 1. SYSTEM_PROMPT (contexte + rôle)          │
        │ 2. PREMIER MESSAGE (vient de l'agent parent)│
        │    → C'est le paramètre "query" de l'outil  │
        └──────────────────┬─────────────────────────┘
                           │
    ┌──────────────────────▼──────────────────────────┐
    │   BOUCLE DE TOURS (agent_workflow)               │
    │   Max: 7-10 tours par agent SPT                  │
    │                                                   │
    │   Pour chaque tour:                              │
    │   1. Appel LLM (process_tool_use)               │
    │   2. Réception tool_output ou text_output        │
    │   3. Si EXIT_WITH_RESULT → Sortie               │
    │   4. Si text_output seul → Renvoi à parent      │
    │   5. Sinon continuer avec résultats outils       │
    └──────────────────┬─────────────────────────────┘
                       │
    ┌──────────────────▼──────────────────────────────┐
    │   GESTION DES OUTPUTS                            │
    │   • tool_output → Intégré au contexte           │
    │   • text_output → Renvoyé à l'agent principal   │
    │   • EXIT_WITH_RESULT → Résultat final           │
    └──────────────────┬─────────────────────────────┘
                       │
    ┌──────────────────▼──────────────────────────────┐
    │   NETTOYAGE CHAT_HISTORY                         │
    │   Après EXIT_WITH_RESULT, l'historique est       │
    │   effacé pour éviter pollution entre missions    │
    └─────────────────────────────────────────────────┘
```

### Composants du Framework

```python
class SPTAgentBase:
    """
    Base pour tous les agents SPT (framework agent_workflow unifié).
    
    Principes (conformes à DOCUMENTATION_FRAMEWORK_AGENTIC_WORKFLOW.md) :
    - System prompt avec rôle et contexte métier
    - Premier message = paramètre du parent (agent principal)
    - Boucle de tours avec budget tokens
    - Gestion text_output (clarification) et tool_output (action)
    - Résumé automatique si dépassement tokens
    - Nettoyage historique après EXIT_WITH_RESULT
    """
    
    def __init__(self, brain_context: Dict):
        # 1. Contexte hérité du brain (mandate_path, dms_system, etc.)
        self.context = brain_context
        
        # 2. Historique local (isolé du brain principal)
        self.chat_history: List[Dict[str, Any]] = []
        
        # 3. Agent IA propre (petit modèle pour tâches simples)
        # ⚠️ IMPORTANT : Peut réutiliser brain.agent si architecture partagée
        self.agent: BaseAIAgent = None
        
        # 4. Outils spécifiques à cet agent
        self.tools: List[Dict] = []
        
        # 5. System prompt (défini dans INIT)
        self.system_prompt: str = ""
        
        # 6. Protection
        self.max_turns: int = 7  # Tours maximum par appel
        self.max_tokens_budget: int = 15000  # Budget tokens (plus petit que l'agent principal)
    
    def INIT_AGENT(self):
        """
        Initialise le system prompt de l'agent SPT.
        
        Structure du prompt (conforme à la documentation) :
        1. RÔLE : Qui est l'agent et quelle est sa mission
        2. CONTEXTE : Informations métier et données disponibles
        3. OUTILS DISPONIBLES : Liste et description des outils
        4. STRATÉGIE : Workflow recommandé pour accomplir la mission
        5. CRITÈRES DE SUCCÈS : Comment savoir si mission accomplie
        6. RAPPORT DE SORTIE : Format attendu pour EXIT_WITH_RESULT
        7. TERMINAISON : Quand et comment utiliser EXIT_WITH_RESULT
        """
        self.system_prompt = f'''Vous êtes un agent SPT spécialisé dans [DOMAINE].
        
RÔLE :
Votre mission principale est de [DÉCRIRE LA MISSION].

CONTEXTE UTILISATEUR :
- Société : {self.context.get('company_name')}
- Mandat : {self.context.get('mandate_path')}
- DMS : {self.context.get('dms_system')}

OUTILS DISPONIBLES :
[LISTE DES OUTILS ET LEUR UTILITÉ]

STRATÉGIE RECOMMANDÉE :
1. [ÉTAPE 1]
2. [ÉTAPE 2]
3. Si besoin de clarification : répondez en text_output
4. Une fois mission accomplie : EXIT_WITH_RESULT

CRITÈRES DE SUCCÈS :
- [CRITÈRE 1]
- [CRITÈRE 2]

RAPPORT DE SORTIE OBLIGATOIRE (via EXIT_WITH_RESULT) :
- [CHAMP 1]
- [CHAMP 2]

⚠️ TERMINAISON :
Utilisez EXIT_WITH_RESULT dès que [CONDITION DE TERMINAISON].
Si vous avez besoin de clarification de l'utilisateur, utilisez text_output.
'''
        
        # Appliquer le prompt à l'agent
        if self.agent:
            self.agent.update_system_prompt(self.system_prompt)
    
    async def execute(self, query: str) -> Dict[str, Any]:
        """
        Boucle agent_workflow standard (conforme à la documentation).
        
        Flux :
        1. Premier message = query (vient de l'agent parent)
        2. Boucle de tours avec process_tool_use
        3. Gestion tool_output (action) et text_output (clarification)
        4. Si EXIT_WITH_RESULT : sortie immédiate
        5. Si text_output seul : retour au parent
        6. Si dépassement tokens : résumé et réinitialisation
        7. Nettoyage historique après sortie
        
        Returns:
            Dict avec keys:
            - success: bool
            - status: "MISSION_COMPLETED" | "MAX_TURNS_REACHED" | "TEXT_OUTPUT" | "ERROR"
            - result: contenu du résultat
        """
        try:
            logger.info(f"[{self.__class__.__name__}] Démarrage workflow - Tours max: {self.max_turns}")
            
            turn_count = 0
            current_input = query  # ⭐ Premier message = paramètre de l'outil
            next_user_input_parts = []
            
            while turn_count < self.max_turns:
                turn_count += 1
                
                # ═══ VÉRIFICATION BUDGET TOKENS ═══
                try:
                    tokens_before = self.agent.get_total_context_tokens(self.agent.default_provider)
                    
                    # Si budget dépassé, générer résumé et réinitialiser
                    if tokens_before >= self.max_tokens_budget:
                        logger.warning(
                            f"[{self.__class__.__name__}] Budget tokens atteint ({tokens_before}) - "
                            f"Génération résumé"
                        )
                        
                        # Générer résumé de la conversation
                        summary = self._generate_summary()
                        
                        # Réinitialiser historique avec résumé intégré
                        self.chat_history.clear()
                self.chat_history.append({
                            "role": "user",
                            "content": f"RÉSUMÉ DE LA CONVERSATION PRÉCÉDENTE:\n{summary}\n\nREPRISE DE LA MISSION:\n{query}"
                        })
                        
                        logger.info(f"[{self.__class__.__name__}] Contexte réinitialisé avec résumé")
                except Exception as e:
                    logger.warning(f"[{self.__class__.__name__}] Erreur calcul tokens: {e}")
                
                logger.info(f"[{self.__class__.__name__}] Tour {turn_count}/{self.max_turns}")
                
                # ═══ APPEL LLM AVEC OUTILS ═══
                ia_responses = self.agent.process_tool_use(
                    content=current_input,
                    tools=self.tools,
                    tool_mapping=self.tool_mapping,
                    size=ModelSize.SMALL,  # SPT utilise petit modèle
                    max_tokens=1024,
                    raw_output=True
                )
                
                # Normaliser les réponses
                if not isinstance(ia_responses, list):
                    ia_responses = [ia_responses] if ia_responses else []
                
                next_user_input_parts = []
                
                # ═══ TRAITEMENT DES RÉPONSES ═══
                for response_block in ia_responses:
                    if not isinstance(response_block, dict):
                        next_user_input_parts.append(f"Réponse inattendue: {str(response_block)[:200]}")
                        continue
                    
                    # ────────────────────────────────────────────
                    # CAS 1 : TOOL_OUTPUT (action exécutée)
                    # ────────────────────────────────────────────
                    if "tool_output" in response_block:
                        tool_block = response_block["tool_output"]
                        tool_name = tool_block.get('tool_name', 'UnknownTool')
                        tool_content = tool_block.get('content', '')
                        
                        logger.info(f"[{self.__class__.__name__}] Outil utilisé: {tool_name}")
                        
                        # ▼▼▼ DÉTECTION EXIT_WITH_RESULT ▼▼▼
                        if tool_name == 'EXIT_WITH_RESULT':
                            logger.info(f"[{self.__class__.__name__}] ✓ EXIT_WITH_RESULT détecté")
                            
                            # Nettoyage historique IMMÉDIAT
                            self._clear_history()
                            
                            # 🚪 SORTIE IMMÉDIATE avec résultat
                            return {
                                "success": True,
                                "status": "MISSION_COMPLETED",
                                "result": tool_content
                            }
                        
                        # Autres outils : intégrer résultat pour prochain tour
                        next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                    
                    # ────────────────────────────────────────────
                    # CAS 2 : TEXT_OUTPUT (clarification/réflexion)
                    # ────────────────────────────────────────────
                    elif "text_output" in response_block:
                        text_block = response_block["text_output"]
                        extracted_text = "Pas de texte"
                        
                        if isinstance(text_block, dict) and "content" in text_block:
                            content = text_block["content"]
                            if isinstance(content, dict):
                                extracted_text = content.get('answer_text', str(content))
                else:
                                extracted_text = str(content)
                        elif isinstance(text_block, str):
                            extracted_text = text_block
                        
                        logger.info(f"[{self.__class__.__name__}] Text output: {extracted_text[:200]}...")
                        
                        # ⚠️ Si text_output SEUL (pas d'outils), renvoyer au parent
                        # (l'agent demande une clarification à l'utilisateur)
                        if len(ia_responses) == 1:
                            logger.info(
                                f"[{self.__class__.__name__}] Text output seul détecté - "
                                f"Renvoi à l'agent principal"
                            )
                    return {
                        "success": True,
                                "status": "TEXT_OUTPUT",
                                "result": extracted_text,
                                "needs_clarification": True
                            }
                        
                        # Sinon, intégrer au contexte pour prochain tour
                        next_user_input_parts.append(f"Réflexion précédente: {extracted_text[:300]}")
                
                # ═══ PRÉPARER INPUT POUR PROCHAIN TOUR ═══
                if next_user_input_parts:
                    current_input = "\n".join(next_user_input_parts)
                else:
                    logger.warning(f"[{self.__class__.__name__}] Aucune réponse utilisable de l'IA")
            return {
                "success": False,
                        "status": "NO_IA_ACTION",
                        "result": "L'IA n'a pas fourni de réponse claire."
                    }
            
            # ═══ MAX TOURS ATTEINT ═══
            logger.warning(f"[{self.__class__.__name__}] Maximum de {self.max_turns} tours atteint")
            
            # Générer rapport de ce qui s'est passé
            summary = f"Maximum de {self.max_turns} tours atteint. Dernier état: {current_input[:500]}"
            
            # Nettoyage historique
            self._clear_history()
            
            return {
                "success": False,
                "status": "MAX_TURNS_REACHED",
                "result": summary
            }
            
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] ERREUR FATALE: {e}", exc_info=True)
            
            # Nettoyage historique même en cas d'erreur
            self._clear_history()
            
            return {
                "success": False,
                "status": "ERROR",
                "result": f"Erreur: {str(e)}"
            }
    
    def _generate_summary(self) -> str:
        """
        Génère un résumé de la conversation pour réinitialisation du contexte.
        
        ⚠️ Cette méthode devrait utiliser l'agent pour générer un résumé intelligent,
        mais pour simplifier on peut faire un résumé basique des derniers messages.
        """
        # TODO: Implémenter résumé intelligent via LLM
        last_messages = self.chat_history[-5:]  # Garder 5 derniers messages
        summary_parts = []
        
        for msg in last_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # Tronquer
            summary_parts.append(f"[{role}] {content}")
        
        return "\n".join(summary_parts)
    
    def _clear_history(self):
        """
        Efface l'historique pour éviter pollution entre missions.
        
        ⚠️ IMPORTANT : Appelé UNIQUEMENT après EXIT_WITH_RESULT ou erreur fatale.
        Pendant la boucle de tours, l'historique DOIT être maintenu.
        """
        self.chat_history.clear()
        logger.info(f"[{self.__class__.__name__}] Chat history cleared après sortie de mission")
```

### Outils Standards

**Chaque agent SPT doit avoir** :
1. **Outils métier** : Spécifiques à sa fonction (GET_ROUTER_JOBS, SEARCH_CONTEXT, etc.)
2. **EXIT_WITH_RESULT** : Outil de sortie obligatoire pour terminer la mission
   ```python
   {
       "name": "EXIT_WITH_RESULT",
       "description": "🎯 Terminer la mission et retourner le résultat final à l'agent principal.",
       "input_schema": {
           "type": "object",
           "properties": {
               "reason": {"type": "string", "description": "Raison de la terminaison"},
               "result": {"type": "object", "description": "Résultat structuré de la mission"},
               "conclusion": {"type": "string", "description": "Résumé textuel pour l'utilisateur"}
           },
           "required": ["reason", "result", "conclusion"]
       }
   }
   ```

3. **ASK_PRINCIPAL_AGENT** (optionnel) : Question à l'agent principal si besoin de clarification
   ```python
   {
       "name": "ASK_PRINCIPAL_AGENT",
       "description": "❓ Demander une clarification à l'agent principal ou à l'utilisateur.",
       "input_schema": {
           "type": "object",
           "properties": {
               "question": {"type": "string", "description": "Question à poser"}
           },
           "required": ["question"]
       }
   }
   ```

### Différence avec les LPT

| Aspect | SPT (Short Process) | LPT (Long Process) |
|--------|---------------------|---------------------|
| Durée | < 30 secondes | > 30 secondes (jusqu'à 30 min) |
| Framework | agent_workflow (boucle de tours) | HTTP + Callback asynchrone |
| Historique | chat_history local (isolé) | Pas d'historique (stateless) |
| Sortie | EXIT_WITH_RESULT (synchrone) | Callback POST /lpt/callback |
| Budget tokens | 15K tokens | Pas de limite (instance externe) |
| text_output | Renvoie à l'agent principal | N/A (pas de clarification) |

---

## 🔄 Flux complet d'un message

**TROIS ARCHITECTURES PARALLÈLES DANS `llm_manager.py` :**

### **Architecture 1 : Workflow Agentic Classique**
**Méthode** : `send_message_with_agentic_streaming()`

```
┌──────────────────────────────────────────────────────────────┐
│ 1. USER envoie message dans thread_key="chat_abc123"        │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. llm_manager.send_message()                                │
│    • Récupère/crée LLMSession (user_id:collection_name)     │
│    • session_key = "user_123:company_456"                   │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. _process_message_with_agentic_streaming()                │
│    • Récupère/crée PinnokioBrain pour ce thread            │
│    • brain.agent = session.agent (partage)                 │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. brain.create_workflow_tools()                            │
│    • Crée outils SPT (GET_FIREBASE_DATA, etc.)             │
│    • Crée outils LPT (APBookkeeper, Router, etc.)          │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Agent utilise LPT_APBookkeeper                           │
│    • _get_user_context_data() avec cache session           │
│    • HTTP vers agent externe                               │
└──────────────────────────────────────────────────────────────┘
```

### **Architecture 2 : Workflow Pinnokio Spécifique**
**Méthode** : `send_message_with_pinnokio()`

```
┌──────────────────────────────────────────────────────────────┐
│ 1. Même point d'entrée que l'architecture classique        │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. _process_pinnokio_workflow()                             │
│    • Workflow spécialisé pour Pinnokio                    │
│    • Gestion spécifique des LPT callbacks                  │
└──────────────────────────────────────────────────────────────┘
```

### **Architecture 3 : Infrastructure Support**
**Méthodes** : `load_chat_history()`, `flush_chat()`

```
┌──────────────────────────────────────────────────────────────┐
│ INFRASTRUCTURE : Chargement/Sauvegarde                     │
├─────────────────────────────────────────────────────────────┤
│ • load_chat_history() : RTDB → Mémoire                    │
│ • flush_chat() : Mémoire → RTDB                           │
│ • Cache contexte LPT par thread                            │
└──────────────────────────────────────────────────────────────┘
```

**Cache contexte LPT (Optimisation clé) :**

```python
# Dans LLMSession
self.thread_contexts: Dict[str, Tuple[Dict[str, Any], float]] = {}
self.context_cache_ttl = 300  # 5 minutes

# Flux d'utilisation
_get_user_context_data(thread_key, session):
    ┌─────────────────────────────────────┐
    │ Cache Hit? (thread_contexts)       │
    ├─────────────────────────────────────┤
    │ ✅ Oui → Retour immédiat            │
    │ ❌ Non → Fetch Firebase + Cache     │
    └─────────────────────────────────────┘
```

---

## 📈 Comparaison Documentation/Code Réel

### ❌ **DOCUMENTATION PRÉCÉDENTE** (Inexacte)

**Ce que la documentation disait :**
- Collection Firestore unique : `pinnokio_workflow`
- Workflow unifié centralisé
- Architecture théorique simple

**Problèmes identifiés :**
- ❌ **Infrastructure duale ignorée** (Firestore + RTDB)
- ❌ **3 méthodes de workflow parallèles** non documentées
- ❌ **Chemins Firebase réels** différents de ceux documentés
- ❌ **Séparation des données** (tâches vs messages) non expliquée

---

### ✅ **CODE RÉEL** (Implémentation actuelle)

**Infrastructure réelle :**
```python
# DEUX services Firebase distincts
class FirebaseManagement:        # Firestore pour tâches LPT
    self.db = get_firestore()

class FirebaseRealtimeChat:      # RTDB pour messages temps réel
    database_url = "https://pinnokio-gpt-default-rtdb..."
    self.db = rtdb.reference("/", url=database_url)
```

**Architecture réelle des workflows :**
```python
# TROIS méthodes parallèles dans llm_manager.py
async def send_message_with_agentic_streaming()    # Workflow classique
async def send_message_with_pinnokio()             # Workflow spécialisé
async def _process_pinnokio_workflow()             # Traitement interne
```

**Chemins de données réels :**
```python
# Firestore (tâches LPT)
workflow_path = f"clients/{user_id}/workflow_pinnokio"

# RTDB (messages)
thread_path = f'{collection_name}/job_chats/{thread_key}/messages'
```

**Avantages du code réel :**
- ✅ **Infrastructure duale performante** (séparation des usages)
- ✅ **Cache contexte LPT efficace** (1 requête au lieu de 3-9)
- ✅ **Brains réutilisables** (persistance par thread)
- ✅ **Mode UI/Backend automatique** (détection connexion)
- ✅ **Gestion d'erreurs robuste** (callbacks, timeouts)

---

## 🎯 Points clés à retenir

### 1. **Architecture 3 niveaux**
- **Niveau 0**: LLMSessionManager (singleton)
- **Niveau 1**: LLMSession (par user+company)
- **Niveau 2**: PinnokioBrain (par thread)

### 2. **Pas de duplication**
- ❌ **1 seul** BaseAIAgent par session
- ✅ Partagé par tous les PinnokioBrain

### 3. **Réutilisation intelligente**
- ✅ PinnokioBrain stocké dans `session.brains[thread_key]`
- ✅ Persistant entre messages du même thread

### 4. **Cache contexte LPT**
- ✅ Stocké dans `session.thread_contexts[thread_key]`
- ✅ TTL 5 minutes
- ✅ Évite 3-9 requêtes Firebase par conversation

### 5. **Séparation des responsabilités**
| Composant | Responsabilité |
|-----------|----------------|
| `LLMSession` | Gestion session, cache, historique |
| `BaseAIAgent` | Moteur IA, providers, tokens |
| `PinnokioBrain` | Orchestration, workflow, outils |
| `LPTClient` | Construction payloads, HTTP, Firebase |
| `SPTTools` | Outils rapides (Firebase, Chroma) |

---

## 🔍 Cas d'usage typiques

### Cas 1: User change de chat dans la même société

```
Session: user_123:company_456  (persiste)
├─→ Thread "chat_abc"  → brain_1 (réutilisé)
└─→ Thread "chat_xyz"  → brain_2 (créé au premier message)
```

**Résultat**: 
- ✅ 2 PinnokioBrain distincts
- ✅ 2 caches contexte distincts
- ✅ 1 seul BaseAIAgent partagé

---

### Cas 2: User change de société

```
Session 1: user_123:company_456  (expire)
Session 2: user_123:company_789  (nouvelle session)
├─→ Nouveau BaseAIAgent
├─→ Nouveaux PinnokioBrain
└─→ Nouveaux caches
```

**Résultat**:
- ✅ Isolation complète entre sociétés
- ✅ Contexte approprié pour chaque société

---

### Cas 3: Appels LPT multiples dans même conversation

```
Tour 1: LPT_APBookkeeper
  → _get_user_context_data() → Firebase (cache MISS)
  → Sauvegarde dans session.thread_contexts

Tour 2: LPT_Router (5s après)
  → _get_user_context_data() → Cache HIT! (pas de Firebase)

Tour 3: LPT_Banker (10s après)
  → _get_user_context_data() → Cache HIT! (pas de Firebase)
```

**Résultat**:
- ✅ **1 requête Firebase** au lieu de 3
- ✅ Performance optimale

---

## 📝 Checklist développeur

Lors de l'ajout d'un nouveau composant, vérifier :

- [ ] Est-ce que ce composant doit être **partagé** ou **isolé** ?
- [ ] Si partagé : stocker dans `LLMSession` (niveau session)
- [ ] Si isolé : stocker dans `PinnokioBrain` (niveau thread)
- [ ] Les données contextuelles doivent-elles être **cachées** ?
- [ ] Si oui : utiliser `session.thread_contexts`
- [ ] Le composant a-t-il besoin de `BaseAIAgent` ?
- [ ] Si oui : utiliser `session.agent` (pas de nouveau agent)
- [ ] Le composant persiste-t-il entre messages ?
- [ ] Si oui : stocker dans `session.brains[thread_key]`

---

## 🚀 Migration depuis l'ancien système

Si vous avez du code qui crée de nouvelles instances à chaque message :

```python
# ❌ ANCIEN
brain = PinnokioBrain(...)
brain.agent = BaseAIAgent(...)  # DOUBLON!

# ✅ NOUVEAU
if thread_key not in session.brains:
    brain = PinnokioBrain(...)
    brain.agent = session.agent  # Partage
    session.brains[thread_key] = brain
else:
    brain = session.brains[thread_key]
```

---

## 📚 Fichiers principaux (Architecture Réelle)

| Fichier | Responsabilité | Classe/Service |
|---------|----------------|----------------|
| `llm_service/llm_manager.py` | **LLMSessionManager** + **LLMSession** (niveaux 0-1) | Gestion sessions, cache contexte |
| `llm/klk_agents.py` | **BaseAIAgent** (moteur IA) | Providers IA, tokens, streaming |
| `pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` | **PinnokioBrain** (niveau 2) | Orchestration, outils SPT/LPT |
| `pinnokio_agentic_workflow/tools/lpt_client.py` | **LPTClient** | Construction payloads, HTTP externes |
| `pinnokio_agentic_workflow/tools/spt_tools.py` | **SPTTools** | Outils rapides (Firebase, ChromaDB) |
| `firebase_providers.py` | **FirebaseManagement** + **FirebaseRealtimeChat** | **Infrastructure duale** |

## 🔧 Agent SPT ContextManager - Implémenté

### **Vue d'ensemble**

**SPTContextManager** est le **premier agent SPT autonome** implémenté dans votre architecture. Il suit parfaitement le pattern agentique décrit dans la documentation et constitue un exemple concret de l'évolution vers des agents SPT plus sophistiqués.

### **Architecture de l'agent**

```
┌─────────────────────────────────────────────────────────────────┐
│                  SPTContextManager                              │
│                  Agent SPT Autonome                             │
├─────────────────────────────────────────────────────────────────┤
│ • Pattern agentique identique à l'agent principal              │
│ • 6 outils spécialisés dans la gestion des contextes           │
│ • Chat history isolé avec nettoyage automatique                │
│ • Gestion des tokens avec résumé automatique                    │
│ • Workflow d'approbation utilisateur intégré                   │
└─────────────────────────────────────────────────────────────────┘
                          ↓
        ┌─────────────────┴──────────────────┬────────────────────┐
        ↓                                     ↓                     ↓
┌──────────────────┐           ┌──────────────────────┐   ┌────────────────┐
│ Outils Contexte  │           │ Outils Modification  │   │ Outils        │
│ • GET_DEPT_CTX   │           │ • UPDATE_TEXT        │   │ Workflow      │
│ • GET_ACCOUNTING │           │ • PUBLISH_UPDATES    │   │ • TASK_TERM   │
│ • GET_GENERAL    │           │                      │   │ • APPROVAL    │
└──────────────────┘           └──────────────────────┘   └────────────────┘
```

### **Outils implémentés**

| Outil | Description | Implémentation |
|-------|-------------|----------------|
| **GET_DEPARTMENT_CONTEXT** | Recherche contexte par département | ✅ Firebase + filtrage |
| **GET_ACCOUNTING_CONTEXT** | Contexte comptable détaillé | ✅ `mandate_path/accounting_context` |
| **GET_GENERAL_CONTEXT** | Contexte général entreprise | ✅ `mandate_path/general_context` |
| **UPDATE_CONTEXT_TEXT** | Modification avec text_updater | ✅ Workflow d'approbation |
| **PUBLISH_UPDATES** | Publication avec timestamps | ✅ `last_refresh` automatique |
| **TASK_TERMINATE** | Clôture avec rapport activité | ✅ Nettoyage automatique |

## 🗄️ Infrastructure de données réelle

| Service | Base de données | Utilisation | Structure |
|---------|----------------|-------------|-----------|
| **FirebaseManagement** | **Firestore** | Tâches LPT, métadonnées | `clients/{user_id}/workflow_pinnokio/{thread_key}` |
| **FirebaseRealtimeChat** | **Realtime DB** | Messages temps réel | `{collection}/job_chats/{thread_key}/messages` |
| **ChromaDB** | Vectorielle | Recherche sémantique | Collections de documents |

---

**⚠️ Note importante** : Cette documentation reflète maintenant **fidèlement** l'implémentation réelle du code, contrairement aux versions précédentes qui présentaient une architecture théorique différente de la pratique.

## ✅ Tests recommandés

1. **Test cache contexte**:
   - Envoyer 3 messages utilisant LPT dans le même thread
   - Vérifier logs: 1 seul `[CACHE] 🔄 Requête Firebase`, puis 2× `[CACHE] ✅ Hit`

2. **Test réutilisation brain**:
   - Envoyer 2 messages dans le même thread
   - Vérifier logs: 1× `Création nouveau PinnokioBrain`, 1× `Réutilisation PinnokioBrain existant`

3. **Test changement thread**:
   - Envoyer message dans thread_1, puis thread_2
   - Vérifier: 2 PinnokioBrain créés, caches distincts

---

**Dernière mise à jour**: 2025-01-15
**Version**: 3.0 (Architecture Multi-Niveaux + Mode UI/Backend + Callbacks LPT)

