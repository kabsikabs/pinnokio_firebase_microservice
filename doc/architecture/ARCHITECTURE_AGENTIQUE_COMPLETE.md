# 🏗️ Architecture Agentique Complète - Pinnokio

## 📊 Vue d'ensemble

Cette documentation décrit l'architecture complète du système agentique Pinnokio, incluant la structure des agents, les modes d'exécution, l'intégration des outils (SPT/LPT), les connexions WebSocket, RTDB, et la gestion des workflows planifiés.

---

## 🎯 Structure Agentique

### Architecture Multi-Niveaux

Le système Pinnokio utilise une architecture hiérarchique à 3 niveaux :

```
┌───────────────────────────────────────────────────────────────────┐
│ NIVEAU 0 : LLMSessionManager (Singleton Global)                  │
│ ─────────────────────────────────────────────────────────────────│
│ Rôle : Gestion centralisée de toutes les sessions                │
│ Responsabilité : Créer/gérer les sessions par user/company      │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       │ sessions: Dict[session_key, LLMSession]
                       │ session_key = "user_id:collection_name"
                       ▼
┌───────────────────────────────────────────────────────────────────┐
│ NIVEAU 1 : LLMSession (Par Utilisateur/Société)                  │
│ ─────────────────────────────────────────────────────────────────│
│ Clé: "user_id:collection_name"                                    │
│ Durée de vie: Tant que user actif dans cette société             │
│ 1 instance PAR UTILISATEUR + SOCIÉTÉ                             │
├───────────────────────────────────────────────────────────────────┤
│ 📦 Conteneurs:                                                    │
│  • agent: BaseAIAgent (1 seul, partagé)                          │
│  • brains: Dict[thread_key, PinnokioBrain]                       │
│  • thread_contexts: Dict[thread_key, context]                  │
│  • conversations: Dict[thread_key, messages]                     │
│  • active_tasks: Dict[thread_key, tasks]                        │
│                                                                    │
│ 🎯 Responsabilités:                                                │
│  • Gérer BaseAIAgent (1 par session)                            │
│  • Gérer PinnokioBrain par thread (persistant)                  │
│  • Cache contexte LPT (évite requêtes Firebase)                  │
│  • Historique par thread                                          │
│  • Métriques et timing                                            │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ├─→ self.agent: BaseAIAgent
               │        └─→ Providers (Anthropic, OpenAI...)
               │
               └─→ self.brains[thread_key]: PinnokioBrain
                        └─→ Réutilise self.agent (pas de doublon)
```

### Composants Principaux

#### 1. **LLMSessionManager** (Niveau 0)

**Fichier**: `app/llm_service/llm_manager.py`

**Rôle** : Singleton global qui gère toutes les sessions utilisateur.

**Caractéristiques** :
- ✅ Création et gestion des sessions par `session_key = "user_id:collection_name"`
- ✅ Expiration automatique des sessions inactives (> 1h)
- ✅ Gestion du streaming via `StreamingController`
- ✅ Externalisation de l'état dans Redis (scaling horizontal)

#### 2. **LLMSession** (Niveau 1)

**Fichier**: `app/llm_service/llm_manager.py`

**Rôle** : Session isolée pour un utilisateur/société.

**Attributs clés** :
```python
class LLMSession:
    session_key: str                  # "user_id:collection_name"
    agent: BaseAIAgent                # Agent IA partagé
    conversations: Dict[str, list]    # Historique par thread
    
    # ⭐ NOUVEAUX ATTRIBUTS
    brains: Dict[str, PinnokioBrain]  # {thread_key: brain}
    thread_contexts: Dict[str, Tuple[Dict, float]]  # Cache contexte LPT
    context_cache_ttl: int = 300      # TTL cache: 5 minutes
    
    # État externalisé dans Redis
    user_context: Dict[str, Any]      # Contexte utilisateur
    jobs_data: Dict[str, Any]         # Données jobs
    jobs_metrics: Dict[str, Any]      # Métriques jobs
```

**Responsabilités** :
- ✅ Créer et gérer **1 seul** `BaseAIAgent` par session
- ✅ Stocker **1** `PinnokioBrain` par thread (réutilisable entre messages)
- ✅ Cacher les contextes LPT par thread (évite requêtes Firebase redondantes)
- ✅ Gérer l'historique des conversations
- ✅ Tracking des tâches actives

#### 3. **PinnokioBrain** (Niveau 2 - Orchestrateur Principal)

**Fichier**: `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py`

**Rôle** : Agent cerveau principal avec capacité d'orchestration SPT/LPT.

**Cycle de vie** : Créé au premier message d'un thread, **réutilisé** pour tous les messages suivants du même thread.

**Responsabilités** :
- ✅ Orchestrer le workflow agentic (Agent Principal)
- ✅ Créer le system prompt stratégique
- ✅ Créer les outils (SPT Agents + LPT Managers + Core Tools)
- ✅ Gérer plans et approbations
- ✅ Générer résumés de conversation
- ✅ **Stocker le contexte utilisateur** (mandate_path, dms_system, etc.)

**Contexte Utilisateur (user_context)** :
```python
class PinnokioBrain:
    def __init__(self, ...):
        self.user_context: Optional[Dict[str, Any]] = None
        # Contient : mandate_path, dms_system, communication_mode, 
        #            client_uuid, company_name, drive_space_parent_id, bank_erp
        #            workflow_params (paramètres par agent)
    
    async def load_user_context(self, thread_key: str, session=None):
        """Charge le contexte utilisateur depuis Firebase (avec cache session)"""
        # 1. Vérifier cache session (TTL 5min)
        # 2. Si absent/expiré: Fetch Firebase
        # 3. Stocker dans self.user_context
    
    def get_user_context(self) -> Dict[str, Any]:
        """Retourne le contexte stocké (utilisé par SPT et LPT)"""
        return self.user_context or {}
```

---

## 🎭 Modes d'Agent

### Registre des Modes

Le système supporte plusieurs modes d'agents configurés dans `agent_modes.py` :

```python
_AGENT_MODE_REGISTRY: Dict[str, AgentModeConfig] = {
    "general_chat": AgentModeConfig(
        name="general_chat",
        prompt_builder=_build_general_prompt,
        tool_builder=_build_general_tools,
    ),
    "accounting_chat": AgentModeConfig(
        name="accounting_chat",
        prompt_builder=_build_general_prompt,
        tool_builder=_build_general_tools,
    ),
    "onboarding_chat": AgentModeConfig(
        name="onboarding_chat",
        prompt_builder=_build_onboarding_prompt,
        tool_builder=_build_general_tools,
    ),
    "apbookeeper_chat": AgentModeConfig(
        name="apbookeeper_chat",
        prompt_builder=_build_apbookeeper_prompt,
        tool_builder=_build_specialized_tools,
    ),
    "router_chat": AgentModeConfig(
        name="router_chat",
        prompt_builder=_build_router_prompt,
        tool_builder=_build_specialized_tools,
    ),
    "banker_chat": AgentModeConfig(
        name="banker_chat",
        prompt_builder=_build_banker_prompt,
        tool_builder=_build_specialized_tools,
    ),
    "task_execution": AgentModeConfig(
        name="task_execution",
        prompt_builder=_build_task_execution_prompt,
        tool_builder=_build_general_tools,
    ),
}
```

### Modes Disponibles

| Mode | Description | Prompt | Outils |
|------|-------------|--------|--------|
| **general_chat** | Agent général avec outils et RAG | Prompt général | Outils généraux (SPT + LPT) |
| **accounting_chat** | Agent comptable | Prompt général | Outils généraux |
| **onboarding_chat** | Agent spécialisé onboarding | Prompt onboarding | Outils généraux + écoute RTDB |
| **apbookeeper_chat** | Agent ApBookeeper | Prompt spécialisé | Outils spécialisés + écoute RTDB |
| **router_chat** | Agent routage documents | Prompt spécialisé | Outils spécialisés + écoute RTDB |
| **banker_chat** | Agent rapprochement bancaire | Prompt spécialisé | Outils spécialisés + écoute RTDB |
| **task_execution** | Agent exécution tâches planifiées | Prompt exécution | Outils généraux |

### Configuration des Modes

Chaque mode est configuré via `AgentConfigManager` :

```python
AGENT_CONFIGS = {
    'general_chat': {
        'system_prompt': None,  # Sera défini par le prompt existant
        'tools': None,  # Sera défini par les outils existants
        'enable_rag': True,
        'rtdb_listening': False,
        'context_injection': False,
        'message_log_container_id': None
    },
    'onboarding_chat': {
        'system_prompt': ONBOARDING_SYSTEM_PROMPT,
        'tools': [],
        'enable_rag': False,
        'rtdb_listening': True,  # ⭐ Écoute RTDB activée
        'context_injection': True,
        'message_log_container_id': 'onboarding_logs_container'
    },
    # ... autres modes
}
```

---

## 🔧 Intégration des Outils

### Méthode d'Intégration

Les outils sont intégrés dans le `PinnokioBrain` via la méthode `create_workflow_tools()` :

```python
def create_workflow_tools(
    self,
    thread_key: str,
    session=None,
    chat_mode: str = "general_chat",
    mode: str = "UI",  # ⭐ Mode UI ou BACKEND
) -> Tuple[List[Dict], Dict]:
    """
    Crée l'ensemble des outils disponibles pour le workflow.
    
    Args:
        thread_key: Clé du thread
        session: Session LLM (optionnel)
        chat_mode: Mode de chat (general_chat, onboarding_chat, etc.)
        mode: "UI" (utilisateur connecté) ou "BACKEND" (tâche planifiée)
    
    Returns:
        Tuple[tool_set, tool_mapping]
    """
    # 1. Récupérer la configuration du mode
    mode_config = get_agent_mode_config(chat_mode)
    
    # 2. Construire les outils selon le mode
    tool_set, tool_mapping = mode_config.tool_builder(
        brain=self,
        thread_key=thread_key,
        session=session,
        chat_mode=chat_mode,
        mode=mode  # ⭐ Passer le mode
    )
    
    return tool_set, tool_mapping
```

### Types d'Outils

Le système distingue **3 types d'outils** :

1. **SPT (Short Process Tooling)** : Outils rapides (< 30 secondes)
2. **LPT (Long Process Tooling)** : Tâches longues (> 30 secondes)
3. **Core Tools** : Outils de base (TERMINATE_TASK, etc.)

---

## ⚡ Outils SPT (Short Process Tooling)

### Définition

Les **SPT** sont des outils rapides exécutés de manière synchrone dans le workflow de l'agent principal.

**Caractéristiques** :
- ⏱️ Durée : < 30 secondes
- 🔄 Exécution : Synchrone (bloquant)
- 📊 Budget tokens : Hérité du PinnokioBrain (80K tokens)
- 🧠 Historique : Partagé avec l'agent principal
- 🎯 Usage : Recherche, filtrage, vérification rapide

### Architecture SPT Actuelle (Implémentée)

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
│ • ContextTools   │                       │ • Router              │
│                  │                       │ • AdminManager        │
│ Fonctions async  │                       │ HTTP + Callback       │
│ Retour direct    │                       │ + Stop tool           │
└──────────────────┘                       └──────────────────────┘
```

### Outils SPT Disponibles

**Fichier**: `app/pinnokio_agentic_workflow/tools/spt_tools.py`

```python
class SPTTools:
    """Outils SPT (Short Process Tooling)"""
    
    def get_tools_definitions(self) -> List[Dict]:
        """Retourne les définitions des outils SPT"""
        return [
            {
                "name": "GET_FIREBASE_DATA",
                "description": "Récupère des données depuis Firebase Firestore",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "query_filters": {"type": "object"}
                    }
                }
            },
            {
                "name": "SEARCH_CHROMADB",
                "description": "Recherche vectorielle dans ChromaDB",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "n_results": {"type": "integer"}
                    }
                }
            },
            {
                "name": "GET_USER_CONTEXT",
                "description": "Récupère le contexte utilisateur complet",
                "input_schema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]
```

### Outils de contexte (ContextTools) - Firestore (Implémenté)

En complément de `SPTTools`, le `PinnokioBrain` expose des **outils de contexte** (accès direct Firestore) dans les modes qui utilisent `_build_general_tools` (ex: `general_chat`, `accounting_chat`, `onboarding_chat`, `task_execution`).

**Outils disponibles** :

- `ROUTER_PROMPT(service)` : lire les règles de routage/classification (source: `{mandate_path}/context/router_context`, champ `router_prompt`)
- `APBOOKEEPER_CONTEXT()` : lire le contexte comptable (source: `{mandate_path}/context/accounting_context`, champ `data.accounting_context_0`)
- `BANK_CONTEXT()` : lire le contexte bancaire (source: `{mandate_path}/context/bank_context`, champ `data.bank_context_0`)
- `COMPANY_CONTEXT()` : lire le profil entreprise (source: `{mandate_path}/context/general_context`, champ `context_company_profile_report`)
- `UPDATE_CONTEXT(...)` : modifier un contexte via opérations `add/replace/delete` + approbation + sauvegarde Firestore
  - `context_type` supporte : `router`, `accounting`, `bank`, `company`
  - `service_name` requis uniquement pour `router`

⚠️ **RÈGLE CRITIQUE (anti-confusion)** :

- `router_context/router_prompt` = **règles de routage** (choix du département/service)
- `bank_context` = **contexte bancaire** (règles de rapprochement)
- `{mandate_path}/setup/function_table` = **règles d’approbation** par département (lecture seule), **ce n’est PAS un contexte métier**.

### Architecture Future : SPT Agents Autonomes

**⚠️ Note** : Une architecture future prévoit des **SPT Agents autonomes** avec leur propre boucle de tours et chat_history isolé. Cette architecture n'est pas encore implémentée mais est documentée dans `SPT_AGENT_INTEGRATION_GUIDE.md`.

**Caractéristiques des SPT Agents (Future)** :
- 🧠 Agent autonome avec propre `BaseAIAgent`
- 📝 Chat history isolé du brain principal
- 💰 Budget tokens : 15K (indépendant)
- 🔄 Boucle de tours : Max 7 tours
- 🧹 Nettoyage automatique après exécution

---

## 🚀 Outils LPT (Long Process Tooling)

### Définition

Les **LPT** sont des tâches longues exécutées de manière asynchrone par des agents externes.

**Caractéristiques** :
- ⏱️ Durée : > 30 secondes (jusqu'à 30 minutes)
- 🔄 Exécution : Asynchrone (non-bloquant)
- 📡 Communication : HTTP + Callback
- 🎯 Usage : Traitements en masse, workflows complexes

### Architecture LPT

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

### Outils LPT Disponibles

**Fichier**: `app/pinnokio_agentic_workflow/tools/lpt_client.py`

#### 1. **LPT_APBookkeeper** - Saisie de Factures Fournisseur

**Ce que l'agent fournit** :
```json
{
    "job_ids": ["file_abc123", "file_def456"],
    "general_instructions": "Vérifier les montants HT/TTC",
    "file_instructions": {
        "file_abc123": "Facture urgente, prioriser"
    }
}
```

**Ce que le système construit automatiquement** :
```python
payload = {
    "collection_name": company_id,              # ✅ Automatique
    "user_id": user_id,                         # ✅ Automatique
    "thread_key": thread_key,                   # ✅ Automatique
    "client_uuid": context['client_uuid'],      # ✅ Automatique
    "mandates_path": context['mandate_path'],   # ✅ Automatique
    "settings": [...],                          # ✅ Automatique
    "batch_id": f'batch_{uuid.uuid4().hex[:10]}',  # ✅ Généré
    "jobs_data": [...],                         # ✅ Construit depuis job_ids
    "start_instructions": "Vérifier les montants HT/TTC"
}
```

**Endpoint HTTP** :
```
POST http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com/apbookeeper-event-trigger
```

#### 2. **LPT_Router** - Routage de Documents

**Ce que l'agent fournit** :
```json
{
    "drive_file_id": "file_xyz789",
    "instructions": "Router vers le dossier Factures",
    "approval_required": false,
    "automated_workflow": true
}
```

**Endpoint HTTP** :
```
POST http://klk-load-balancer.../event-trigger
```

#### 3. **LPT_Banker** - Réconciliation Bancaire

**Ce que l'agent fournit** :
```json
{
    "bank_account": "FR76 1234 5678 9012 3456",
    "transaction_ids": ["tx_001", "tx_002", "tx_003"],
    "instructions": "Vérifier les doublons",
    "approval_required": false
}
```

**Endpoint HTTP** :
```
POST http://klk-load-balancer.../banker-event-trigger
```

### Principe Clé : Simplification pour l'Agent

**❌ AVANT** : L'agent devait fournir TOUT le payload

**✅ MAINTENANT** : L'agent fournit SEULEMENT les IDs + instructions

**Tout le reste est automatique !** Le système complète automatiquement :
- `collection_name`, `user_id`, `thread_key`
- `client_uuid`, `settings`, `communication_mode`
- `dms_system`, `mandates_path`
- `workflow_params` (paramètres par agent depuis le contexte)

---

## 🔌 Connexion WebSocket (WSS)

### Vue d'ensemble

Le système utilise **WebSocket (WSS)** pour le streaming temps réel des réponses IA, remplaçant le streaming RTDB pour améliorer la latence.

**Changements clés** :
- ✅ **Streaming temps réel via WebSocket** : Latence réduite de ~50-200ms à ~1-5ms
- ✅ **1 seule écriture RTDB finale** : Économie sur les coûts Firebase
- ✅ **Format de canal identique** : Facilite la transition depuis RTDB
- ✅ **Compatibilité maintenue** : L'écriture finale dans RTDB reste pour l'historique

### Endpoint WebSocket

```
wss://your-microservice.com/ws?uid={user_id}&space_code={collection_name}&thread_key={thread_key}
```

**Paramètres de connexion** :
- `uid` **(requis)** : ID Firebase de l'utilisateur
- `space_code` **(optionnel)** : Code de la société/espace (collection_name)
- `thread_key` **(optionnel)** : Clé du thread de conversation
- `mode` **(optionnel)** : Mode de chat (défaut: "auto")

### Format du Canal WebSocket

Le canal WebSocket utilise **exactement le même format que RTDB** :

```
chat:{user_id}:{collection_name}:{thread_key}
```

**Exemple** :
```
chat:user123:company456:thread789
```

### Types d'Événements WebSocket

Tous les événements WebSocket contiennent :
- `type` : Type d'événement
- `channel` : Canal au format `chat:{user_id}:{collection_name}:{thread_key}`
- `payload` : Données de l'événement

#### 1️⃣ **llm_stream_start** - Début du streaming

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

#### 2️⃣ **llm_stream_chunk** - Chunk de contenu

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

#### 3️⃣ **llm_stream_complete** - Fin du streaming

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
      "status": "complete"
    }
  }
}
```

#### 4️⃣ **llm_stream_interrupted** - Streaming interrompu

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

#### 5️⃣ **llm_stream_error** - Erreur pendant le streaming

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

### Gestion du Streaming

**Fichier**: `app/llm_service/llm_manager.py`

```python
class StreamingController:
    """Contrôleur pour gérer les arrêts de streaming via WebSocket."""
    
    def __init__(self):
        self.active_streams: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    async def register_stream(self, session_key: str, thread_key: str, task: asyncio.Task):
        """Enregistre un stream actif."""
        # ...
    
    async def stop_stream(self, session_key: str, thread_key: str) -> bool:
        """Arrête un stream spécifique."""
        # ...
    
    async def stop_all_streams(self, session_key: str) -> int:
        """Arrête tous les streams d'une session."""
        # ...
```

---

## 💾 RTDB (Realtime Database) selon les Modes

### Infrastructure Firebase Duale

Le système utilise **DEUX bases de données Firebase distinctes** :

```
┌─────────────────────────────────────────────────────────────────────┐
│ FIREBASE FIRESTORE (FirebaseManagement)                             │
├─────────────────────────────────────────────────────────────────────┤
│ Utilisation : Données structurées et tâches LPT                    │
│                                                                      │
│ Structure des données :                                             │
│ clients/{user_id}/workflow_pinnokio/{thread_key}                    │
│   └── tasks/{task_id} (tâches LPT, métadonnées)                     │
│                                                                      │
│ Avantages :                                                         │
│ • Requêtes complexes et filtres                                    │
│ • Persistence fiable des tâches                                    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ FIREBASE REALTIME DATABASE (FirebaseRealtimeChat)                  │
├─────────────────────────────────────────────────────────────────────┤
│ Utilisation : Messages et conversations temps réel                  │
│                                                                      │
│ Structure des données :                                             │
│ {collection_name}/job_chats/{thread_key}/messages                  │
│   └── Messages avec timestamps et métadonnées                      │
│                                                                      │
│ Avantages :                                                         │
│ • Synchronisation temps réel                                        │
│ • Historique conversationnel                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Utilisation RTDB selon les Modes

#### Mode UI (Utilisateur Connecté)

**Détection** : `heartbeat < 5 minutes` dans UnifiedRegistry

**Comportement** :
- ✅ Streaming WebSocket activé ⚡
- ✅ Broadcast `stream_start`, `stream_chunk`, `stream_complete`
- ✅ Persistence RTDB (toujours activée) pour l'historique
- ✅ 1 seule écriture RTDB finale (après streaming complet)

**Utilisé pour** :
- Conversations en temps réel
- Feedback immédiat à l'utilisateur

#### Mode BACKEND (User Déconnecté)

**Détection** : `heartbeat > 5 minutes` ou absent OU `is_on_chat_page = False` OU `current_active_thread ≠ thread_key`

**Comportement** :
- ❌ Streaming WebSocket désactivé
- ❌ Pas de broadcast WebSocket (économie ressources)
  - ❌ Pas de `llm_stream_start`, `llm_stream_chunk`, `llm_stream_complete`
  - ❌ Pas de `WORKFLOW_CHECKLIST` broadcast
  - ❌ Pas de `WORKFLOW_STEP_UPDATE` broadcast
  - ❌ Pas de `chat.message` broadcast
- ✅ Persistence RTDB uniquement 💾 (messages complets)
- ✅ Redis toujours publié (pour cohérence, mais pas de WebSocket)

**Utilisé pour** :
- Workflows automatisés (tâches planifiées)
- Continuation après LPT callback
- Traitement en arrière-plan

**⚠️ Important** : Même si l'utilisateur est connecté globalement (`heartbeat < 5 min`), si il n'est **pas sur le thread spécifique** où le workflow s'exécute, le mode BACKEND est activé pour éviter les broadcasts inutiles.

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

**Conditionnement des broadcasts dans les outils** (`pinnokio_brain.py`) :
```python
# Dans CREATE_CHECKLIST et UPDATE_STEP
current_mode = getattr(self, "_current_mode", "UI")

if current_mode == "UI":
    await hub.broadcast(self.firebase_user_id, {
        "type": "WORKFLOW_CHECKLIST",  # ou "WORKFLOW_STEP_UPDATE"
        "channel": ws_channel,
        "payload": ws_message
    })
else:
    # Mode BACKEND : pas de broadcast, seulement RTDB
    logger.info(f"[TOOL] ⏭️ Broadcast WebSocket ignoré (mode={current_mode})")
```

**Conditionnement des broadcasts dans ListenersManager** (`listeners_manager.py`) :
```python
def _publish(self, uid: str, payload: dict) -> None:
    evt_type = str(payload.get("type", ""))
    
    # ⭐ Événements workflow : Vérifier mode avant broadcast
    if evt_type.startswith("workflow"):
        space_code = payload.get("payload", {}).get("space_code")
        thread_key = payload.get("payload", {}).get("thread_key")
        
        # ⭐ Vérifier si user est sur ce thread spécifique
        should_broadcast_ws = True
        if space_code and thread_key:
            from .llm_service.session_state_manager import SessionStateManager
            state_manager = SessionStateManager()
            user_on_thread = state_manager.is_user_on_thread(uid, space_code, thread_key)
            
            if not user_on_thread:
                # Mode BACKEND : pas de broadcast WebSocket
                should_broadcast_ws = False
            else:
                # Mode UI : broadcast activé
                should_broadcast_ws = True
        
        # WebSocket conditionnel (pas de Redis pour workflow)
        if should_broadcast_ws:
            hub.broadcast_threadsafe(uid, payload)
        return
    
    # Pour les messages chat.message, vérifier si user est sur le thread
    should_broadcast_ws = True  # Par défaut
    if evt_type.startswith("chat"):
        sc = payload.get("payload", {}).get("space_code")
        tk = payload.get("payload", {}).get("thread_key")
        
        if sc and tk:
            # ⭐ Vérifier si user est sur ce thread spécifique
            from .llm_service.session_state_manager import SessionStateManager
            state_manager = SessionStateManager()
            user_on_thread = state_manager.is_user_on_thread(uid, sc, tk)
            
            if not user_on_thread:
                # Mode BACKEND : pas de broadcast WebSocket
                should_broadcast_ws = False
            else:
                # Mode UI : broadcast activé
                should_broadcast_ws = True
    
    # Redis toujours publié (cohérence)
    self.redis.publish(channel, json.dumps(payload))
    
    # WebSocket conditionnel
    if should_broadcast_ws:
        hub.broadcast_threadsafe(uid, payload)
```

**Méthode de filtrage** : `SessionStateManager.is_user_on_thread()`

**Fichier** : `app/llm_service/session_state_manager.py`

**Implémentation** :
```python
def is_user_on_thread(
    self,
    user_id: str,
    company_id: str,
    thread_key: str
) -> bool:
    """
    Vérifie si l'utilisateur est actuellement sur un thread spécifique.
    
    Logique :
    1. Charge l'état de session depuis Redis (clé: session:{user_id}:{company_id}:state)
    2. Vérifie is_on_chat_page = True
    3. Vérifie current_active_thread = thread_key
    
    Returns:
        True si l'utilisateur est sur la page chat ET sur ce thread
    """
    state = self.load_session_state(user_id, company_id)
    
    if not state:
        return False
    
    is_on_chat = state.get("is_on_chat_page", False)
    current_thread = state.get("current_active_thread")
    
    return is_on_chat and current_thread == thread_key
```

**Conditions de filtrage** :
- ✅ **Mode UI** : `is_on_chat_page = True` ET `current_active_thread = thread_key` → Broadcast WebSocket activé
- ❌ **Mode BACKEND** : Sinon → Broadcast WebSocket désactivé (économie ressources)

**Source de données** : Redis (clé `session:{user_id}:{company_id}:state`)
- Mise à jour via `enter_chat()` / `leave_chat()` / `switch_thread()`
- Synchronisé cross-instance (multi-instance ready)

**Types d'événements filtrés** :
- ✅ `workflow*` : `WORKFLOW_CHECKLIST`, `WORKFLOW_STEP_UPDATE`, `WORKFLOW_USER_JOINED`, `WORKFLOW_PAUSED`, `WORKFLOW_RESUMING`, `WORKFLOW_RESUMED`
- ✅ `chat*` : `chat.message`, `chat.sync`, etc.

**Note** : Redis est toujours publié pour la cohérence, même en mode BACKEND. Seul le broadcast WebSocket est conditionnel.

---

## 🎯 Mode UI et BACKEND pour Tâches Planifiées

### Vue d'ensemble

Le système distingue **2 modes d'exécution** pour les tâches planifiées :

1. **Mode UI** : Utilisateur connecté, streaming activé
2. **Mode BACKEND** : Utilisateur déconnecté, pas de streaming

### Détection du Mode

**Fichier**: `app/llm_service/llm_manager.py`

```python
async def _execute_scheduled_task(
    self,
    user_id: str,
    company_id: str,
    task_data: dict,
    thread_key: str,
    execution_id: str
):
    # ...
    
    # 6. Déterminer mode (UI/BACKEND)
    # Vérifier si user est sur ce thread spécifique
    user_on_active_chat = session.is_user_on_specific_thread(thread_key)
    
    mode = "UI" if user_on_active_chat else "BACKEND"
    
    logger.info(
        f"[TASK_EXEC] Démarrage workflow - mode={mode} "
        f"user_on_active_chat={user_on_active_chat} is_on_chat_page={session.is_on_chat_page} "
        f"current_active_thread={session.current_active_thread} thread={thread_key}"
    )
```

**Logique de détection** :
- `is_on_chat_page = False` → Mode BACKEND (user pas sur la page)
- `is_on_chat_page = True + current_active_thread = thread_key` → Mode UI
- `is_on_chat_page = True + current_active_thread ≠ thread_key` → Mode BACKEND

**⚠️ Nuance importante** : Même si l'utilisateur est connecté globalement (heartbeat récent), si il n'est **pas sur le thread spécifique** où le workflow s'exécute, le mode BACKEND est activé. Cela évite les broadcasts WebSocket inutiles vers un utilisateur qui regarde une autre partie de l'application.

### Flux dans le Mode UI

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Tâche planifiée déclenchée (CRON)                           │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Vérification mode utilisateur                                │
│    • is_on_chat_page = True                                     │
│    • current_active_thread = thread_key                         │
│    → Mode UI détecté ✅                                         │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Initialisation session                                       │
│    • Mode UI : Vérifier cache Redis                             │
│    • Si cache HIT → utiliser données Redis                      │
│    • Si cache MISS → fetch depuis source → écrire dans Redis    │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Exécution workflow avec streaming                            │
│    • enable_streaming = True                                    │
│    • Broadcast WebSocket activé                                 │
│    • Chaque chunk envoyé via WebSocket                          │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Persistence RTDB                                             │
│    • Message final écrit dans RTDB                             │
│    • Historique conservé                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Flux dans le Mode BACKEND

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Tâche planifiée déclenchée (CRON)                           │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Vérification mode utilisateur                                │
│    • is_on_chat_page = False OU                                 │
│    • current_active_thread ≠ thread_key                        │
│    → Mode BACKEND détecté ✅                                    │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Initialisation session                                       │
│    • Mode BACKEND : Toujours fetch depuis source               │
│    • Écrire dans Redis (pour prochain mode UI)                 │
│    • Pas de dépendance au cache                                │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Exécution workflow sans streaming                           │
│    • enable_streaming = False                                  │
│    • Pas de broadcast WebSocket                                 │
│    • Traitement en arrière-plan                                │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Persistence RTDB uniquement                                  │
│    • Message complet écrit dans RTDB                           │
│    • Historique conservé                                       │
│    • Pas de streaming pour l'utilisateur                       │
└─────────────────────────────────────────────────────────────────┘
```

### Gestion du Cache Redis selon le Mode

**Mode UI** :
```python
# Initialisation session
if mode == "UI":
    cached_data = redis_client.get(cache_key)
    if cached_data:
        context = json.loads(cached_data)  # ✅ CACHE HIT
    else:
        # CACHE MISS → Firebase
        context = await lpt_client._reconstruct_full_company_profile(...)
        # Mettre en cache (TTL 1h)
        redis_client.setex(cache_key, 3600, json.dumps(context))

# Appel outil
if mode == "UI":
    # Recharger depuis Redis à chaque appel (données à jour)
    cached_data = redis_client.get(cache_key)
    if cached_data:
        jobs_data = json.loads(cached_data)
```

**Mode BACKEND** :
```python
# Initialisation session
if mode == "BACKEND":
    # Toujours Firebase direct
    context = await lpt_client._reconstruct_full_company_profile(...)
    # Mettre en cache (pour prochain mode UI)
    redis_client.setex(cache_key, 3600, json.dumps(context))

# Appel outil
if mode == "BACKEND":
    # Utiliser données statiques initiales (pas de rechargement)
    jobs_data = session.jobs_data  # Données chargées à l'initialisation
```

---

## 🛑 Systèmes d'Arrêt

### Arrêt du Streaming

Le système permet d'arrêter le streaming en cours via l'API RPC `LLM.stop_streaming`.

**Fichier**: `app/llm_service/llm_manager.py`

```python
async def stop_streaming(
    self,
    user_id: str,
    collection_name: str,
    thread_key: str = None
) -> dict:
    """
    Arrête le streaming via WebSocket pour un thread spécifique ou tous les threads.
    
    Args:
        user_id: ID de l'utilisateur
        collection_name: ID de la société
        thread_key: Thread spécifique (optionnel, arrête tous si omis)
    """
    try:
        base_session_key = f"{user_id}:{collection_name}"
        
        if thread_key:
            # Arrêter un thread spécifique
            stopped = await self.streaming_controller.stop_stream(
                base_session_key, thread_key
            )
        else:
            # Arrêter tous les threads
            stopped_count = await self.streaming_controller.stop_all_streams(
                base_session_key
            )
        
        # Envoyer événement WebSocket d'interruption
        await hub.broadcast(user_id, {
            "type": "llm_stream_interrupted",
            "channel": f"chat:{user_id}:{collection_name}:{thread_key}",
            "payload": {
                "thread_key": thread_key,
                "accumulated": accumulated_content
            }
        })
        
        return {"success": True, "message": f"Stream arrêté pour thread {thread_key}"}
    except Exception as e:
        logger.error(f"[STOP_STREAMING] ❌ Erreur: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
```

### Arrêt des Workflows

**Fichier**: `app/listeners_manager.py`

```python
def stop_workflow_listener_for_job(self, uid: str, job_id: str) -> bool:
    """
    Arrête le listener workflow pour un job spécifique.
    
    Args:
        uid (str): User ID
        job_id (str): Job ID à arrêter
        
    Returns:
        bool: True si succès, False sinon
    """
    try:
        key = f"{uid}_{job_id}"
        
        with self._lock:
            unsubs = self._workflow_unsubs.get(key)
            if not unsubs:
                return False
            
            # Détacher le listener
            for unsub in unsubs:
                try:
                    unsub()
                except Exception as e:
                    self.logger.error("workflow_listener_detach_error", ...)
            
            # Supprimer de la registry
            del self._workflow_unsubs[key]
            
            # Nettoyer le cache
            cache_key_invoice = f"{uid}_invoice_{job_id}"
            cache_key_steps = f"{uid}_steps_{job_id}"
            self._workflow_cache.pop(cache_key_invoice, None)
            self._workflow_cache.pop(cache_key_steps, None)
        
        return True
    except Exception as e:
        self.logger.error("workflow_listener_stop_error", ...)
        return False
```

### Arrêt des Tâches Planifiées

Les tâches planifiées peuvent être arrêtées via :

1. **Annulation dans Firebase** : Mise à jour du statut de la tâche
2. **Arrêt du CRON** : Désactivation de la tâche dans le scheduler
3. **Interruption manuelle** : Via l'interface utilisateur

---

## 🔄 Gestion du Workflow

### Workflow Principal

Le workflow principal est géré par `PinnokioBrain` via la méthode `_process_message_with_agentic_streaming()`.

**Flux d'exécution** :

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Message utilisateur reçu                                     │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Initialisation/Création Brain                                  │
│    • Vérifier si brain existe pour thread_key                   │
│    • Si non : Créer nouveau PinnokioBrain                      │
│    • Si oui : Réutiliser brain existant                        │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Chargement contexte utilisateur                               │
│    • Mode UI : Vérifier cache Redis                             │
│    • Mode BACKEND : Fetch Firebase direct                       │
│    • Stocker dans brain.user_context                            │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Création des outils                                          │
│    • SPT Tools (GET_FIREBASE_DATA, SEARCH_CHROMADB, etc.)      │
│    • LPT Tools (APBookkeeper, Router, Banker)                  │
│    • Core Tools (TERMINATE_TASK)                                │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Boucle de tours (max 20 tours)                              │
│    ├─ Tour 1: Analyse requête                                   │
│    ├─ Tour 2: Appel outil SPT                                  │
│    ├─ Tour 3: Appel outil LPT                                  │
│    ├─ Tour 4: Attente callback LPT                             │
│    └─ Tour 5: TERMINATE_TASK                                   │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. Persistence                                                  │
│    • Messages dans RTDB                                         │
│    • Tâches LPT dans Firestore                                  │
│    • Historique conservé                                       │
└─────────────────────────────────────────────────────────────────┘
```

### Gestion des LPT Callbacks

Quand un LPT termine, il envoie un callback au microservice :

**Endpoint** : `POST /lpt/callback`

**Flux** :
```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Agent externe termine (APBookkeeper, Router, Banker)         │
│    └─→ POST /lpt/callback                                       │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Récupération tâche sauvegardée                               │
│    • Path: clients/{user_id}/workflow_pinnokio/{thread_key}    │
│    • Extraire company_id depuis document                       │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Vérification session                                         │
│    • Vérifier si session LLM existe                             │
│    • Si expirée → Pas de reprise workflow                      │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Détection mode (UI/Backend)                                  │
│    • user_connected = registry.is_user_connected(user_id)      │
│    • mode = "UI" if user_connected else "BACKEND"              │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Reprise workflow                                             │
│    • _resume_workflow_after_lpt()                              │
│    • Récupérer/créer brain pour thread_key                     │
│    • Construire message de continuation                        │
│    • Exécuter workflow (streaming conditionnel)                │
└─────────────────────────────────────────────────────────────────┘
```

### Gestion des Tâches Planifiées

**Fichier**: `app/llm_service/llm_manager.py`

```python
async def _execute_scheduled_task(
    self,
    user_id: str,
    company_id: str,
    task_data: dict,
    thread_key: str,
    execution_id: str
):
    """
    Exécute une tâche planifiée.
    
    Étapes :
    1. Charger la mission depuis Firebase
    2. Créer/initialiser la session LLM
    3. Déterminer le mode (UI/BACKEND)
    4. Construire le message initial
    5. Exécuter le workflow
    """
    # 1. Charger mission
    mission = task_data.get("mission", {})
    
    # 2. Initialiser session
    session = await self.initialize_session(user_id, company_id, client_uuid)
    
    # 3. Déterminer mode
    user_on_active_chat = session.is_user_on_specific_thread(thread_key)
    mode = "UI" if user_on_active_chat else "BACKEND"
    
    # 4. Construire message initial
    initial_message = f"""🎯 **Exécution Automatique de Tâche**
    
    **Titre** : {mission['title']}
    **Description** : {mission['description']}
    **Mode d'exécution** : {mode_text}
    
    **Plan d'Action** :
    {mission['plan']}
    
    **Instructions** :
    1. Créer la workflow checklist avec CREATE_CHECKLIST
    2. Exécuter le plan d'action étape par étape
    3. Mettre à jour chaque étape avec UPDATE_STEP
    4. Finaliser avec TERMINATE_TASK
    
    Commence maintenant l'exécution."""
    
    # 5. Exécuter workflow
    await self._process_unified_workflow(
        session=session,
        user_id=user_id,
        collection_name=company_id,
        thread_key=thread_key,
        message=initial_message,
        assistant_message_id=f"task_{execution_id}",
        assistant_timestamp=datetime.now(timezone.utc).isoformat(),
        enable_streaming=user_on_active_chat,  # ⭐ Streaming conditionnel
        system_prompt=task_specific_prompt
    )
```

---

## 📊 Résumé des Concepts Clés

### Architecture

| Niveau | Composant | Responsabilité | Durée de vie |
|--------|-----------|----------------|--------------|
| **0** | LLMSessionManager | Gestion globale sessions | Singleton |
| **1** | LLMSession | Session user/société | Tant que user actif |
| **2** | PinnokioBrain | Orchestration thread | Par thread (persistant) |

### Modes d'Exécution

| Mode | Détection | Streaming | Cache Redis | Usage |
|------|-----------|-----------|-------------|-------|
| **UI** | `heartbeat < 5 min` | ✅ Activé | Rechargement à chaque appel | Conversations temps réel |
| **BACKEND** | `heartbeat > 5 min` | ❌ Désactivé | Données statiques initiales | Tâches planifiées |

### Types d'Outils

| Type | Durée | Exécution | Communication | Budget Tokens |
|------|-------|-----------|---------------|---------------|
| **SPT** | < 30s | Synchrone | Direct | 80K (hérité) |
| **LPT** | > 30s | Asynchrone | HTTP + Callback | N/A (externe) |

### Infrastructure

| Service | Base de données | Usage | Structure |
|---------|----------------|-------|-----------|
| **FirebaseManagement** | Firestore | Tâches LPT | `clients/{user_id}/workflow_pinnokio/{thread_key}` |
| **FirebaseRealtimeChat** | RTDB | Messages | `{collection}/job_chats/{thread_key}/messages` |
| **WebSocket Hub** | WSS | Streaming | `chat:{user_id}:{collection}:{thread_key}` |

---

## 🔄 Basculement Dynamique UI ↔ BACKEND (Tâches Planifiées)

### Vue d'ensemble

Le système permet le **basculement dynamique** entre les modes UI et BACKEND pendant l'exécution d'un workflow (tâche planifiée). Cela permet à l'utilisateur d'interagir avec un workflow en cours.

### Architecture

**Fichier principal** : `app/llm_service/workflow_state_manager.py`

**Clé Redis** : `workflow:{user_id}:{company_id}:{thread_key}:state`

**États possibles** :
- `running` : Workflow en cours d'exécution
- `paused` : Workflow en pause (conversation utilisateur)
- `completed` : Workflow terminé

### Flux de Basculement

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1 : BACKEND (user absent)                                             │
│ • workflow_mode = "BACKEND", enable_streaming = False                       │
│ • Boucle agentic tourne (tours 1, 2, 3...)                                  │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ User entre (enter_chat)
┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 2 : UI + WORKFLOW ACTIF                                               │
│ • ⚡ BASCULE → workflow_mode = "UI", enable_streaming = True                │
│ • Signal WebSocket "WORKFLOW_USER_JOINED" envoyé                            │
│ • User voit le travail en cours                                             │
│                                                                              │
│ [SI MESSAGE UTILISATEUR]                                                    │
│ ├─→ Message normal : workflow_paused = True, conversation normale           │
│ │   • ⚡ BASCULE chat_mode: task_execution → general_chat                    │
│ │   • Brain mis à jour avec mode conversationnel                            │
│ │   • L'agent peut dialoguer normalement (pas de règles strictes)           │
│ └─→ Message "...TERMINATE" : reprise workflow avec pré-prompt               │
│     • ⚡ BASCULE chat_mode: general_chat → task_execution                    │
│     • Brain remis à jour avec mode task_execution                            │
│     • Workflow reprend avec règles strictes (TERMINATE_TASK/WAIT_ON_LPT)    │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ User quitte (leave_chat)
┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 3 : RETOUR BACKEND                                                    │
│ • Si workflow_paused → reprise automatique avec pré-prompt                  │
│ • ⚡ BASCULE → workflow_mode = "BACKEND", enable_streaming = False          │
│ • ⚡ BASCULE chat_mode: general_chat → task_execution (si changé)           │
│ • Brain remis à jour avec mode task_execution                                │
│ • Workflow continue en silence                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Signaux WebSocket

| Signal | Description | Payload |
|--------|-------------|---------|
| `WORKFLOW_USER_JOINED` | User entre pendant workflow actif | `{thread_key, workflow_active, workflow_paused}` |
| `WORKFLOW_PAUSED` | Workflow pausé (message user) | `{thread_key, turn, message}` |
| `WORKFLOW_RESUMING` | Reprise après TERMINATE | `{thread_key, message}` |
| `WORKFLOW_RESUMED` | Workflow repris | `{thread_key, turn, message}` |

### Implémentation

**Dans `send_message()`** :
```python
# Vérifier si workflow actif
if workflow_manager.is_workflow_running(user_id, collection_name, thread_key):
    queue_result = workflow_manager.queue_user_message(...)
    
    if queue_result.get("is_terminate"):
        # Signal de reprise, workflow reprendra au prochain tour
        # ⚡ Le chat_mode sera remis à "task_execution" dans _process_unified_workflow
        return {"status": "workflow_resuming"}
    else:
        # Message normal, workflow pausé, conversation normale continue
        # ⚡ BASCULER chat_mode de "task_execution" à "general_chat"
        if session.context.chat_mode == "task_execution":
            session.context.chat_mode = "general_chat"
            # Mettre à jour le brain avec le nouveau chat_mode
            if thread_key in session.active_brains:
                brain = session.active_brains[thread_key]
                brain.initialize_system_prompt(chat_mode="general_chat")
        pass
```

**Dans `enter_chat()`** :
```python
# Basculer en mode UI si workflow actif
workflow_switch = workflow_manager.user_entered(user_id, collection_name, thread_key)
if workflow_switch.get("changed"):
    await hub.broadcast(user_id, {"type": "WORKFLOW_USER_JOINED", ...})
```

**Dans `leave_chat()`** :
```python
# Reprendre workflow si pausé
leave_result = workflow_manager.user_left(user_id, collection_name, thread_key)
if leave_result.get("needs_resume"):
    # Workflow reprendra automatiquement au prochain tour
    pass
```

**Dans `_process_unified_workflow()`** :
```python
# À chaque tour, vérifier l'état du workflow
workflow_state = workflow_manager.get_workflow_state(...)
if workflow_state:
    # Bascule dynamique du streaming
    enable_streaming = (workflow_state.get("mode") == "UI")
    
    # Vérifier si pausé
    if workflow_state.get("status") == "paused":
        break  # Sortir de la boucle, reprise via leave_chat ou TERMINATE
    
    # Vérifier si message en attente (TERMINATE ou user_left)
    pending = workflow_manager.get_pending_message(...)
    if pending:
        # ⚡ REMETTRE chat_mode à "task_execution" pour reprendre le workflow
        if session.context.chat_mode != "task_execution":
            session.context.chat_mode = "task_execution"
            chat_mode = "task_execution"
            # Mettre à jour le brain et recréer les outils
            brain.initialize_system_prompt(chat_mode=chat_mode)
            tools, tool_mapping = brain.create_workflow_tools(
                thread_key, session, chat_mode=chat_mode, mode=mode
            )
        
        current_input = "🔄 REPRISE DU WORKFLOW..."  # Pré-prompt de reprise
```

---

## 🔄 Changement de Chat Mode pendant Workflow

### Vue d'ensemble

Le système gère dynamiquement le `chat_mode` pour les threads de tâches planifiées (`task_*`) :

1. **Workflow ACTIF** : `chat_mode = task_execution` (règles strictes)
2. **Workflow TERMINÉ** : `chat_mode = general_chat` (conversation normale)
3. **Workflow PAUSÉ** (message utilisateur) : `chat_mode = general_chat` (conversation normale)
4. **Workflow REPRIS** (TERMINATE/leave_chat) : `chat_mode = task_execution` (retour au workflow)

### Règle de Basculement

**Entrée dans un chat `task_*` (enter_chat)** :
- ⚠️ VÉRIFICATION : Le workflow est-il **réellement actif** ?
- Si OUI → `chat_mode = task_execution`
- Si NON → `chat_mode = general_chat` (le workflow est terminé, conversation normale)

**Message normal (sans TERMINATE) pendant workflow actif** :
- ⚡ `chat_mode` : `task_execution` → `general_chat`
- Le brain est mis à jour avec le nouveau `chat_mode`
- Le system prompt est réinitialisé avec `general_chat`
- L'agent peut dialoguer normalement (pas de règles strictes TERMINATE_TASK/WAIT_ON_LPT)

**Message sur thread `task_*` sans workflow actif** :
- ⚡ `chat_mode` forcé à `general_chat` (le workflow est terminé)
- L'utilisateur peut discuter normalement avec l'agent

**Reprise du workflow (TERMINATE ou leave_chat)** :
- ⚡ `chat_mode` : `general_chat` → `task_execution`
- Le brain est remis à jour avec `task_execution`
- Le system prompt est réinitialisé avec `task_execution`
- Les outils sont recréés avec le bon `chat_mode`
- L'agent reprend avec les règles strictes (TERMINATE_TASK/WAIT_ON_LPT uniquement)

### Implémentation

**Dans `enter_chat()`** (à l'entrée dans le chat) :
```python
# Thread task_* mais workflow NON actif → conversation normale
if thread_key.startswith("task_") and not workflow_active:
    if session.context.chat_mode == "task_execution":
        session.update_context(chat_mode="general_chat")
        brain.initialize_system_prompt(chat_mode="general_chat")
```

**Dans `send_message()`** (vérification supplémentaire) :
```python
# Pas de workflow actif + thread task_* + chat_mode=task_execution → forcer general_chat
if not workflow_manager.is_workflow_running(...):
    if thread_key.startswith("task_") and session.context.chat_mode == "task_execution":
        session.context.chat_mode = "general_chat"
        brain.initialize_system_prompt(chat_mode="general_chat")
```

**Dans `send_message()`** (quand workflow pausé par message utilisateur) :
```python
# Message normal, workflow pausé
if session.context.chat_mode == "task_execution":
    session.context.chat_mode = "general_chat"
    brain.initialize_system_prompt(chat_mode="general_chat")
```

**Dans `_process_unified_workflow()`** (quand workflow reprend via TERMINATE/leave_chat) :
```python
# Reprise workflow (TERMINATE ou user_left)
if session.context.chat_mode != "task_execution":
    session.context.chat_mode = "task_execution"
    chat_mode = "task_execution"
    brain.initialize_system_prompt(chat_mode=chat_mode)
    tools, tool_mapping = brain.create_workflow_tools(
        thread_key, session, chat_mode=chat_mode, mode=mode
    )
```

**Dans `_resume_workflow_after_lpt()`** (callback LPT) :
```python
# Tâche planifiée = task_execution, LPT simple = general_chat
resume_chat_mode = "task_execution" if is_planned_task else "general_chat"

session = await self._ensure_session_initialized(
    user_id=user_id,
    collection_name=company_id,
    chat_mode=resume_chat_mode
)

# Mettre à jour le brain avec le bon chat_mode
if brain and is_planned_task:
    brain.initialize_system_prompt(chat_mode="task_execution", jobs_metrics=session.jobs_metrics)
```

### Pourquoi ce changement ?

En mode `task_execution`, l'agent est contraint par des règles strictes :
- Seuls `TERMINATE_TASK` et `WAIT_ON_LPT` peuvent clôturer/pauser
- Pas de détection automatique "texte sans outils = mission complétée"
- L'agent ne peut pas avoir une conversation normale
- **PROBLÈME** : Si l'utilisateur entre dans un chat `task_*` terminé avec `task_execution`, l'agent boucle sans fin !

En passant à `general_chat` quand le workflow n'est pas actif ou est pausé, l'agent peut :
- Dialoguer librement avec l'utilisateur
- Répondre à des questions
- Utiliser tous les outils disponibles
- Avoir une conversation naturelle

Quand le workflow reprend (TERMINATE ou leave_chat), le retour à `task_execution` garantit que l'agent respecte à nouveau les règles strictes du workflow.

### Cas problématique résolu

**Avant** :
1. Workflow terminé sur thread `task_057caf1d139b`
2. Utilisateur entre dans le chat → `chat_mode = task_execution` (basé sur le nom du thread)
3. Utilisateur envoie "Salut" → Agent en `task_execution` boucle sans fin (attend TERMINATE_TASK)

**Après** :
1. Workflow terminé sur thread `task_057caf1d139b`
2. Utilisateur entre dans le chat → Système vérifie : workflow actif ? **NON**
3. ⚡ `chat_mode` forcé à `general_chat`
4. Utilisateur envoie "Salut" → Agent répond normalement

---

## 🔒 Protection : Callback LPT pendant Conversation

### Règle critique

Quand un callback LPT arrive **pendant** que l'utilisateur est en conversation, le système **attend la fin de la conversation** avant de reprendre le workflow.

### Flux protégé

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Utilisateur en conversation (streaming en cours)                 │
│                                                                      │
│ 2. Callback LPT arrive                                              │
│    → ⏳ Détection : streaming actif sur ce thread                   │
│    → Attente fin conversation (max 60 secondes)                     │
│                                                                      │
│ 3. Conversation terminée (streaming fini)                           │
│    → ✅ Workflow task_execution reprend                             │
│    → Pas de conflit, pas de messages mélangés                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Implémentation

**Dans `_resume_workflow_after_lpt()`** :
```python
# Attendre la fin d'une conversation en cours
stream_key = f"{user_id}:{company_id}:{thread_key}"
max_wait_seconds = 60

while stream_key in self.active_streams and waited < max_wait_seconds:
    logger.info(f"[WORKFLOW_RESUME] ⏳ Attente fin conversation...")
    await asyncio.sleep(0.5)
    waited += 0.5

# Puis démarrer le workflow
workflow_manager.start_workflow(...)
result = await self._process_unified_workflow(...)
```

### Tableau récapitulatif des protections

| Situation | Protection | Comportement |
|-----------|------------|--------------|
| User message pendant **workflow actif** | ✅ | Workflow pausé, conversation normale |
| Callback LPT pendant **conversation** | ✅ | Attente fin conversation, puis reprise workflow |
| 2 callbacks LPT en parallèle | ✅ | Redis `start_workflow` empêche conflits |

---

## 🎯 Points Clés à Retenir

1. **Architecture 3 niveaux** : LLMSessionManager → LLMSession → PinnokioBrain
2. **Pas de duplication** : 1 seul BaseAIAgent par session, partagé par tous les brains
3. **Réutilisation intelligente** : PinnokioBrain stocké dans `session.brains[thread_key]`
4. **Cache contexte LPT** : Stocké dans `session.thread_contexts[thread_key]`, TTL 5 minutes
5. **Mode UI/BACKEND automatique** : Détection basée sur heartbeat et thread actif
6. **Streaming conditionnel** : Activé uniquement en mode UI
7. **Persistence RTDB** : Toujours activée pour l'historique (mode UI et BACKEND)
8. **Simplification LPT** : Agent fournit seulement IDs + instructions, reste automatique
9. **Basculement dynamique** : UI ↔ BACKEND pendant les workflows via `WorkflowStateManager`
10. **Interaction utilisateur** : Message normal = pause, "TERMINATE" = reprise UI, quitter = reprise BACKEND
11. **Changement de chat_mode** : 
    - Message normal pendant workflow → `task_execution` → `general_chat` (conversation normale)
    - TERMINATE ou leave_chat → `general_chat` → `task_execution` (retour au workflow)

---

## 🔄 Distinction TERMINATE vs Leave Chat

Cette distinction est **CRITIQUE** pour comprendre le comportement du système.

### Scénario : Workflow en cours, utilisateur entre dans le chat

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        WORKFLOW EN COURS (BACKEND)                       │
│                     (tâche planifiée, pas d'utilisateur)                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
               ┌───────────────────────────────────────┐
               │      👤 UTILISATEUR ENTRE DANS LE CHAT │
               │           → Mode passe à "UI"          │
               │           → Streaming activé           │
               └───────────────────────────────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
    ┌────────────────────────────┐   ┌────────────────────────────────┐
    │   📝 MESSAGE NORMAL        │   │   📝 MESSAGE + "TERMINATE"     │
    │   (sans TERMINATE)         │   │   (termine par TERMINATE)      │
    └────────────────────────────┘   └────────────────────────────────┘
                     │                             │
                     ▼                             ▼
    ┌────────────────────────────┐   ┌────────────────────────────────┐
    │   ⏸️ WORKFLOW PAUSÉ        │   │   🔄 WORKFLOW REPREND          │
    │   → Conversation normale   │   │   → Mode reste "UI" (streaming)│
    │   → chat_mode: general_chat│   │   → chat_mode: task_execution │
    │   → L'utilisateur est servi│   │   → Pré-prompt avec message    │
    └────────────────────────────┘   └────────────────────────────────┘
                     │                             │
          ┌─────────┴─────────┐                    │
          ▼                   ▼                    │
┌─────────────────┐ ┌─────────────────┐            │
│ 📤 AUTRE MSG    │ │ 👋 USER QUITTE  │            │
│ (normal/TERM)   │ │    LE CHAT      │            │
│ → Répéter       │ │                 │            │
└─────────────────┘ └─────────────────┘            │
                          │                        │
                          ▼                        │
          ┌────────────────────────────┐           │
          │   🔄 WORKFLOW REPREND      │           │
          │   → Mode passe à "BACKEND" │◄──────────┘
          │   → Streaming désactivé    │    (si user quitte)
          │   → chat_mode: task_execution│
          │   → Pré-prompt "user_left" │
          └────────────────────────────┘
```

### Tableau comparatif

| Action | Mode résultant | Streaming | Workflow | Chat Mode | Pré-prompt |
|--------|---------------|-----------|----------|-----------|------------|
| User entre | UI | ✅ Activé | Continue | `task_execution` | Non |
| Message normal | UI | ✅ Activé | ⏸️ Pausé | `general_chat` ⚡ | Non |
| Message + TERMINATE | UI | ✅ Activé | 🔄 Reprend | `task_execution` ⚡ | ✅ `terminate_request` |
| User quitte (pausé) | BACKEND | ❌ Désactivé | 🔄 Reprend | `task_execution` ⚡ | ✅ `user_left` |
| User quitte (actif) | BACKEND | ❌ Désactivé | Continue | `task_execution` | Non |

**⚡ Changement de chat_mode** :
- **Message normal** : `task_execution` → `general_chat` (conversation normale)
- **TERMINATE ou leave_chat** : `general_chat` → `task_execution` (retour au workflow)

### Fichiers concernés

| Fichier | Rôle |
|---------|------|
| `app/llm_service/workflow_state_manager.py` | Gestion état workflow Redis |
| `app/pinnokio_agentic_workflow/orchestrator/system_prompt_workflow_resume.py` | Prompt de reprise workflow |
| `app/pinnokio_agentic_workflow/tools/wait_on_lpt.py` | Outil WAIT_ON_LPT |

---

## ⏳ Outil WAIT_ON_LPT

### Cas d'usage

Quand l'agent a lancé un LPT et doit attendre son retour **avant** de pouvoir continuer :

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        WORKFLOW EN COURS                                 │
│                                                                          │
│  1. Étape terminée ✅                                                    │
│  2. LPT_APBookkeeper lancé 📤                                            │
│  3. Prochaine étape dépend du résultat du LPT... ⏳                      │
│                                                                          │
│  → L'agent appelle WAIT_ON_LPT                                           │
│  → Le workflow se met en "pause propre"                                  │
│  → Le callback LPT réveillera le workflow                                │
└─────────────────────────────────────────────────────────────────────────┘
```

### Définition de l'outil

```python
# app/pinnokio_agentic_workflow/tools/wait_on_lpt.py

{
    "name": "WAIT_ON_LPT",
    "description": "⏳ Mettre le workflow en pause en attente d'un callback LPT",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Raison de l'attente"
            },
            "expected_lpt": {
                "type": "string",
                "enum": ["LPT_APBookkeeper", "LPT_Router", "LPT_Banker", "LPT_FileManager", "OTHER"]
            },
            "step_waiting": {
                "type": "string",
                "description": "ID de l'étape en attente"
            },
            "task_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs envoyés au LPT"
            }
        },
        "required": ["reason", "expected_lpt"]
    }
}
```

### Exemple d'appel

```json
{
    "reason": "Attente du retour de LPT_APBookkeeper pour la saisie des 5 factures",
    "expected_lpt": "LPT_APBookkeeper",
    "step_waiting": "STEP_2_SAISIE_FACTURES",
    "task_ids": ["file_abc123", "file_def456"]
}
```

### Comportement

1. **L'agent appelle WAIT_ON_LPT** → Le workflow passe en état `waiting_lpt`
2. **Le workflow s'arrête proprement** → Comme si `mission_completed = True`
3. **Le callback LPT arrive** → Le workflow reprend automatiquement
4. **L'agent continue sa checklist** → Avec le résultat du LPT

---

## 📝 Prompt de Reprise Workflow

Le fichier `system_prompt_workflow_resume.py` génère un prompt spécifique pour la reprise du workflow :

```python
# app/pinnokio_agentic_workflow/orchestrator/system_prompt_workflow_resume.py

def build_workflow_resume_prompt(
    user_context: dict,
    resume_reason: str,  # "terminate_request" | "user_left"
    user_message: Optional[str] = None,
    workflow_checklist: Optional[Dict[str, Any]] = None,
    active_lpt_tasks: Optional[list] = None,
    current_turn: int = 0
) -> str:
```

### Contenu du prompt

Le prompt inclut :
1. **Contexte de reprise** : Pourquoi le workflow reprend (TERMINATE ou user_left)
2. **Message utilisateur** : Si TERMINATE avec message
3. **État de la checklist** : Étapes terminées, en cours, à faire
4. **LPT en attente** : Si des callbacks sont attendus
5. **Instructions claires** : Quand utiliser WAIT_ON_LPT

### Instructions pour WAIT_ON_LPT dans le prompt

```markdown
## 🛑 RÈGLE CRITIQUE : WAIT_ON_LPT

**Quand utiliser `WAIT_ON_LPT` :**

Utilisez cet outil si et SEULEMENT si :
1. Vous avez lancé un LPT (ex: LPT_APBookkeeper, LPT_Router, etc.)
2. Ce LPT n'a pas encore retourné son résultat (pas de callback reçu)
3. La suite de votre workflow DÉPEND du résultat de ce LPT

**CE QUI SE PASSE :**
- Le workflow se met en pause proprement
- Quand le LPT terminera, vous serez automatiquement réactivé
- Vous recevrez le résultat du LPT et pourrez continuer
```

---

## 📝 Outil CRUD_STEP - Gestion de la Checklist

### Description

L'outil `CRUD_STEP` permet à l'agent de modifier dynamiquement la checklist du workflow :
- **CREATE** : Ajouter une nouvelle étape
- **UPDATE** : Modifier le statut ou les infos d'une étape
- **DELETE** : Supprimer une étape "pending"

### Fichier

📄 `app/pinnokio_agentic_workflow/tools/crud_step.py`

### Définition

```python
{
    "name": "CRUD_STEP",
    "input_schema": {
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "delete"]
            },
            "step_id": {"type": "string"},
            "step_name": {"type": "string"},      # Pour create
            "status": {"type": "string"},          # Pour update
            "message": {"type": "string"},         # Pour update
            "insert_after": {"type": "string"},    # Pour create
            "reason": {"type": "string"}           # Pour delete
        },
        "required": ["action", "step_id"]
    }
}
```

### Exemples d'utilisation

**Ajouter une étape :**
```json
{
    "action": "create",
    "step_id": "STEP_4_VERIFICATION",
    "step_name": "Vérification des résultats",
    "insert_after": "STEP_3_TRAITEMENT"
}
```

**Mettre à jour le statut :**
```json
{
    "action": "update",
    "step_id": "STEP_2_SAISIE",
    "status": "completed",
    "message": "50 factures saisies avec succès"
}
```

**Supprimer une étape :**
```json
{
    "action": "delete",
    "step_id": "STEP_5_OPTIONNEL",
    "reason": "Non nécessaire car déjà traité par LPT"
}
```

### Règles

- ⚠️ Seules les étapes `pending` peuvent être supprimées
- Les étapes `in_progress` ou `completed` ne peuvent PAS être supprimées
- L'outil remplace/complète `UPDATE_STEP` avec plus de fonctionnalités

---

## ✉️ Text Wrapper - Message Utilisateur en Workflow

### Description

Quand l'utilisateur envoie un message pendant un workflow actif, ce message est "encapsulé" avec un contexte expliquant la situation à l'agent.

### Fonction

```python
# app/pinnokio_agentic_workflow/orchestrator/system_prompt_workflow_resume.py

def build_user_message_wrapper(
    user_message: str,
    is_first_message: bool = True,
    workflow_title: str = None,
    steps_summary: str = None
) -> str:
```

### Contenu du wrapper (premier message)

Le wrapper inclut :
1. **Bannière visuelle** : Indique clairement que le workflow est en pause
2. **Contexte de la tâche** : Titre et progression
3. **Le message original** de l'utilisateur
4. **Instructions** : Outils disponibles pendant la pause
5. **Moyens de terminaison** : TERMINATE ou quitter le chat

### Exemple de message encapsulé

```
╔══════════════════════════════════════════════════════════════════════════╗
║  👤 L'UTILISATEUR EST ENTRÉ DANS LE CHAT - WORKFLOW EN PAUSE             ║
╚══════════════════════════════════════════════════════════════════════════╝

📋 **Tâche en cours :** Rapprochement bancaire mensuel
📊 **Progression :** 3/5 terminées

⚠️ **SITUATION ACTUELLE :**
Vous étiez en train d'exécuter un workflow planifié.
L'utilisateur vient d'entrer dans le chat et vous envoie un message.
Le workflow est maintenant EN PAUSE pour vous permettre de dialoguer avec lui.

---

📩 **MESSAGE DE L'UTILISATEUR :**

[Contenu du message]

---

## 🎯 COMMENT RÉPONDRE
...

## 🔄 TERMINAISON DE LA CONVERSATION
...
```

---

## ⛔ Validation TERMINATE_TASK

### Description

En mode **execution** (tâche planifiée), `TERMINATE_TASK` ne peut être appelé que si **TOUTES** les étapes de la checklist sont au statut `completed`.

### Fichier

📄 `app/pinnokio_agentic_workflow/tools/terminate_task_validator.py`

### Fonction principale

```python
def validate_terminate_task(
    brain,
    reason: str,
    conclusion: str
) -> Tuple[bool, Dict[str, Any]]:
    """
    Valide si TERMINATE_TASK peut être appelé.
    
    Returns:
        (is_valid, result_dict)
        - is_valid: True si autorisé
        - result_dict: Résultat ou message d'erreur détaillé
    """
```

### Comportement

| Mode | Vérification | Comportement si étapes incomplètes |
|------|-------------|-----------------------------------|
| Normal (conversation) | ❌ Non | TERMINATE autorisé |
| Execution (tâche planifiée) | ✅ Oui | TERMINATE **REFUSÉ** + message détaillé |

### Message de refus

Si des étapes ne sont pas `completed`, l'agent reçoit :
- Liste des étapes incomplètes avec leur statut
- Instructions pour les compléter (CRUD_STEP update)
- Instructions pour les supprimer (CRUD_STEP delete) si `pending`
- Rappel de rappeler TERMINATE_TASK ensuite

---

## 🎯 Points Clés à Retenir (Mis à jour)

1. **Architecture 3 niveaux** : LLMSessionManager → LLMSession → PinnokioBrain
2. **Pas de duplication** : 1 seul BaseAIAgent par session, partagé par tous les brains
3. **Réutilisation intelligente** : PinnokioBrain stocké dans `session.brains[thread_key]`
4. **Cache contexte LPT** : Stocké dans `session.thread_contexts[thread_key]`, TTL 5 minutes
5. **Mode UI/BACKEND automatique** : Détection basée sur heartbeat et thread actif
6. **Streaming conditionnel** : Activé uniquement en mode UI
7. **Persistence RTDB** : Toujours activée pour l'historique (mode UI et BACKEND)
8. **Simplification LPT** : Agent fournit seulement IDs + instructions, reste automatique
9. **Basculement dynamique** : UI ↔ BACKEND pendant les workflows via `WorkflowStateManager`
10. **TERMINATE ≠ Leave Chat** : 
    - TERMINATE = reprise EN MODE UI (streaming ON)
    - Leave Chat = reprise EN MODE BACKEND (streaming OFF)
11. **WAIT_ON_LPT** : L'agent peut se mettre en pause proprement en attendant un callback LPT
12. **CRUD_STEP** : L'agent peut ajouter/modifier/supprimer des étapes de la checklist
13. **Text Wrapper** : Le premier message utilisateur en workflow est encapsulé avec contexte
14. **Validation TERMINATE_TASK** : En mode execution, toutes les étapes doivent être "completed"

---

## 📁 Fichiers Créés/Modifiés

| Fichier | Description |
|---------|-------------|
| `app/llm_service/workflow_state_manager.py` | Gestionnaire état workflow Redis |
| `app/pinnokio_agentic_workflow/orchestrator/system_prompt_workflow_resume.py` | Prompts reprise + text wrapper |
| `app/pinnokio_agentic_workflow/tools/wait_on_lpt.py` | Outil WAIT_ON_LPT |
| `app/pinnokio_agentic_workflow/tools/crud_step.py` | Outil CRUD_STEP |
| `app/pinnokio_agentic_workflow/tools/terminate_task_validator.py` | Validateur TERMINATE_TASK |

---

**Version** : 1.3.0  
**Date** : Décembre 2025  
**Auteur** : Équipe Backend Python

### Changelog

#### v1.3.0 (Décembre 2025)
- ✅ Création de l'outil `CRUD_STEP` pour gestion dynamique de la checklist
- ✅ Création du text wrapper `build_user_message_wrapper()` pour messages utilisateur en workflow
- ✅ Validation `TERMINATE_TASK` en mode execution : toutes les étapes doivent être "completed"
- ✅ `terminate_task_validator.py` avec message d'erreur détaillé si validation échoue
- ✅ Documentation complète des nouveaux outils

#### v1.2.0 (Décembre 2025)
- ✅ Création de l'outil `WAIT_ON_LPT` pour pause propre en attente de callback
- ✅ Création du prompt `system_prompt_workflow_resume.py` pour reprise workflow
- ✅ Distinction claire TERMINATE vs leave_chat :
  - TERMINATE = reprise en mode UI (streaming activé)
  - leave_chat = reprise en mode BACKEND (streaming désactivé)
- ✅ État `waiting_lpt` ajouté au WorkflowStateManager
- ✅ Documentation complète des scénarios de basculement

#### v1.1.0 (Décembre 2025)
- ✅ Ajout du `WorkflowStateManager` pour gestion état workflow dans Redis
- ✅ Basculement dynamique UI ↔ BACKEND pendant les tâches planifiées
- ✅ Gestion interaction utilisateur pendant workflow (pause/reprise)
- ✅ Signaux WebSocket pour notification frontend (`WORKFLOW_USER_JOINED`, `WORKFLOW_PAUSED`, etc.)
- ✅ Pré-prompt de reprise workflow après TERMINATE ou leave_chat


