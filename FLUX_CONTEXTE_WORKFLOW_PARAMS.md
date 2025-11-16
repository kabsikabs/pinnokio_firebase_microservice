# 📊 Flux de Chargement du Contexte et Workflow Params

## 🎯 Vue d'ensemble

Ce document décrit le flux complet de chargement du `user_context` et des `workflow_params` depuis Firebase jusqu'à leur utilisation dans les LPT et le system prompt.

---

## 🔄 FLUX COMPLET (Schéma)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   1️⃣ PREMIER APPEL - Initialisation Session             │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  LLMManager.initialize_session()        │
        │  - user_id, collection_name, client_uuid│
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  _ensure_session_initialized()          │
        │  ┌───────────────────────────────────┐  │
        │  │ Session existe ?                  │  │
        │  └───────────────────────────────────┘  │
        │           │                              │
        │           ├─ NON ──► Créer LLMSession   │
        │           │                              │
        │           └─ OUI ──► Session existante  │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  session.initialize_session_data()      │
        │  ⭐ MODE DÉTECTION                      │
        │  ┌───────────────────────────────────┐  │
        │  │ _detect_connection_mode()         │  │
        │  │ - UI : utilisateur connecté       │  │
        │  │ - BACKEND : tâche planifiée       │  │
        │  └───────────────────────────────────┘  │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  Firebase.reconstruct_full_client_      │
        │  profile(user_id, client_uuid,          │
        │          collection_name)               │
        │                                         │
        │  ═══ ÉTAPE 1 : Récupérer client_uuid   │
        │  Chemin: clients/{user_id}/bo_clients/  │
        │         {user_id}                       │
        │                                         │
        │  ═══ ÉTAPE 2 : Charger profil complet  │
        │  - Données client                       │
        │  - Données mandat                       │
        │  - Données ERP                          │
        │  ⭐ WORKFLOW_PARAMS                     │
        │  Chemin: .../mandates/{mandate_id}/     │
        │         setup/workflow_params           │
        │                                         │
        │  ┌───────────────────────────────────┐  │
        │  │ workflow_params = {               │  │
        │  │   "Apbookeeper_param": {          │  │
        │  │     "apbookeeper_approval_        │  │
        │  │     required": False,             │  │
        │  │     "apbookeeper_approval_        │  │
        │  │     contact_creation": True       │  │
        │  │   },                              │  │
        │  │   "Router_param": {...},          │  │
        │  │   "Banker_param": {...}           │  │
        │  │ }                                 │  │
        │  └───────────────────────────────────┘  │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  session.user_context = {               │
        │    "client_uuid": "...",                │
        │    "mandate_path": "...",               │
        │    "company_name": "...",               │
        │    "dms_system": "...",                 │
        │    ...                                  │
        │    ⭐ "workflow_params": {...}          │
        │  }                                      │
        │                                         │
        │  ✅ Stocké dans session.user_context    │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  session.jobs_data = {...}              │
        │  session.jobs_metrics = {...}           │
        │                                         │
        │  ✅ Données permanentes chargées        │
        └─────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│             2️⃣ CRÉATION BRAIN - Premier Message sur Thread              │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  LLMManager.send_message()              │
        │  - user_id, collection_name, thread_key │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  _ensure_brain_initialized(thread_key)  │
        │  ┌───────────────────────────────────┐  │
        │  │ Brain existe pour thread ?        │  │
        │  └───────────────────────────────────┘  │
        │           │                              │
        │           ├─ NON ──► Créer Brain        │
        │           │                              │
        │           └─ OUI ──► Brain existant     │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  PinnokioBrain.__init__()               │
        │  - self.user_context = None ❌          │
        │  - self.jobs_data = None                │
        │  - self.jobs_metrics = None             │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  brain.initialize_agents()              │
        │  - Créer agent principal                │
        │  - Créer outils SPT/LPT                 │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  ⭐ INJECTION DONNÉES PERMANENTES       │
        │                                         │
        │  brain.user_context =                   │
        │    session.user_context  ✅             │
        │                                         │
        │  brain.jobs_data =                      │
        │    session.jobs_data                    │
        │                                         │
        │  brain.jobs_metrics =                   │
        │    session.jobs_metrics                 │
        │                                         │
        │  ✅ workflow_params maintenant          │
        │     dans brain.user_context             │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  brain.initialize_system_prompt()       │
        │                                         │
        │  build_principal_agent_prompt(          │
        │    brain.user_context                   │
        │  )                                      │
        │                                         │
        │  ┌───────────────────────────────────┐  │
        │  │ workflow_params =                 │  │
        │  │   brain.user_context.get(         │  │
        │  │     "workflow_params", {}         │  │
        │  │   )                               │  │
        │  │                                   │  │
        │  │ apbookeeper_params =              │  │
        │  │   workflow_params.get(            │  │
        │  │     "Apbookeeper_param", {}       │  │
        │  │   )                               │  │
        │  │                                   │  │
        │  │ approval_required =               │  │
        │  │   apbookeeper_params.get(         │  │
        │  │     "apbookeeper_approval_        │  │
        │  │     required", False              │  │
        │  │   )                               │  │
        │  └───────────────────────────────────┘  │
        │                                         │
        │  ✅ Injecté dans system prompt          │
        └─────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│             3️⃣ UTILISATION LPT - Appel Outil LPT_APBookeeper            │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  Agent appelle LPT_APBookeeper          │
        │  {                                     │
        │    "job_ids": ["file_123", ...]        │
        │  }                                     │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  LPTClient.launch_apbookeeper()         │
        │                                         │
        │  context = brain.get_user_context()     │
        │  ┌───────────────────────────────────┐  │
        │  │ Retourne brain.user_context       │  │
        │  │ (DÉJÀ EN MÉMOIRE) ✅              │  │
        │  └───────────────────────────────────┘  │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  workflow_params =                      │
        │    context.get('workflow_params', {})   │
        │                                         │
        │  apbookeeper_params =                   │
        │    workflow_params.get(                 │
        │      'Apbookeeper_param', {}            │
        │    )                                    │
        │                                         │
        │  approval_required =                    │
        │    apbookeeper_params.get(              │
        │      'apbookeeper_approval_required',   │
        │      False                              │
        │    )                                    │
        │                                         │
        │  approval_contact_creation =            │
        │    apbookeeper_params.get(              │
        │      'apbookeeper_approval_contact_     │
        │      creation', False                   │
        │    )                                    │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  Construire payload LPT                 │
        │  {                                     │
        │    "jobs_data": [                      │
        │      {                                 │
        │        "job_id": "file_123",           │
        │        "approval_required": False,     │
        │        "approval_contact_creation":    │
        │          True  ✅                      │
        │      }                                 │
        │    ]                                   │
        │  }                                     │
        │                                         │
        │  ✅ Valeurs depuis workflow_params      │
        └─────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│        4️⃣ BRAIN EXISTANT - 2ème Message sur Même Thread                │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  _ensure_brain_initialized(thread_key)  │
        │  ┌───────────────────────────────────┐  │
        │  │ Brain existe ?                    │  │
        │  └───────────────────────────────────┘  │
        │           │                              │
        │           └─ OUI ──► brain =            │
        │                      session.active_    │
        │                      brains[thread_key] │
        │                                         │
        │  ✅ brain.user_context DÉJÀ EN          │
        │     MÉMOIRE (depuis création)           │
        │                                         │
        │  ❌ AUCUN APPEL Firebase                │
        │  ❌ AUCUN APPEL Redis                   │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  brain.get_user_context()               │
        │  ┌───────────────────────────────────┐  │
        │  │ Retourne brain.user_context       │  │
        │  │ (DÉJÀ EN MÉMOIRE) ✅              │  │
        │  └───────────────────────────────────┘  │
        └─────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│        5️⃣ CACHE REDIS - Brain Recréé après Expiration                  │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │  brain.load_user_context(mode="UI")     │
        │                                         │
        │  ┌───────────────────────────────────┐  │
        │  │ Vérifier CACHE REDIS              │  │
        │  │ Clé: context:{user_id}:           │  │
        │  │      {collection_name}            │  │
        │  └───────────────────────────────────┘  │
        │           │                              │
        │           ├─ CACHE HIT (< 1h) ──►       │
        │           │   context = Redis            │
        │           │   ✅ workflow_params         │
        │           │      depuis cache            │
        │           │                              │
        │           └─ CACHE MISS (> 1h) ──►      │
        │               Firebase                   │
        │               reconstruct_full_client_   │
        │               profile()                  │
        │               ✅ Re-lit workflow_params  │
        │               ✅ Met en cache Redis      │
        └─────────────────────────────────────────┘
```

---

## 📍 POINTS D'APPEL ET CONDITIONS

### 1️⃣ **Initialisation Session** (`initialize_session`)

**Quand ?**
- Premier appel RPC avec `user_id` + `collection_name`
- Session n'existe pas encore

**Conditions :**
```python
if session_key not in self.sessions:
    # Créer nouvelle session
    session = LLMSession(...)
    await session.initialize_session_data(client_uuid)
```

**Appels Firebase :**
- ✅ `reconstruct_full_client_profile()` → Lit `workflow_params`
- ✅ Stocke dans `session.user_context["workflow_params"]`

**Fréquence :** 1 fois par utilisateur/société

---

### 2️⃣ **Création Brain** (`_ensure_brain_initialized`)

**Quand ?**
- Premier message sur un thread
- Brain n'existe pas pour ce `thread_key`

**Conditions :**
```python
if thread_key not in session.active_brains:
    # Créer nouveau brain
    brain = PinnokioBrain(...)
    await brain.initialize_agents()
    
    # ⭐ INJECTION
    brain.user_context = session.user_context  # ← workflow_params inclus
    brain.jobs_data = session.jobs_data
    brain.jobs_metrics = session.jobs_metrics
```

**Appels Firebase :** ❌ AUCUN (utilise `session.user_context`)

**Fréquence :** 1 fois par thread

---

### 3️⃣ **Brain Existant** (`_ensure_brain_initialized`)

**Quand ?**
- 2ème message sur le même thread
- Brain existe déjà

**Conditions :**
```python
if thread_key in session.active_brains:
    brain = session.active_brains[thread_key]
    # ✅ brain.user_context DÉJÀ EN MÉMOIRE
```

**Appels Firebase :** ❌ AUCUN

**Fréquence :** Tous les messages suivants sur le même thread

---

### 4️⃣ **Construction System Prompt** (`initialize_system_prompt`)

**Quand ?**
- Après création du brain
- Lors de chaque initialisation du brain

**Conditions :**
```python
brain.initialize_system_prompt(chat_mode, jobs_metrics)
    ↓
build_principal_agent_prompt(brain.user_context)
    ↓
workflow_params = brain.user_context.get("workflow_params", {})
```

**Source :** `brain.user_context` (en mémoire)

**Fréquence :** 1 fois par création de brain

---

### 5️⃣ **Appel LPT** (`LPTClient.launch_apbookeeper`)

**Quand ?**
- Agent décide d'utiliser un outil LPT
- Ex: `LPT_APBookkeeper`, `LPT_Router`, `LPT_Banker`

**Conditions :**
```python
context = brain.get_user_context()  # ← Retourne brain.user_context
workflow_params = context.get('workflow_params', {})
```

**Source :** `brain.user_context` (en mémoire)

**Appels Firebase :** ❌ AUCUN

**Fréquence :** À chaque appel d'outil LPT

---

### 6️⃣ **Cache Redis** (`brain.load_user_context`)

**Quand ?**
- Brain recréé après expiration session (> 1h)
- Mode BACKEND (tâche planifiée)

**Conditions :**
```python
# Mode UI
if mode == "UI":
    cached_data = redis_client.get(cache_key)
    if cached_data:
        context = json.loads(cached_data)  # ✅ CACHE HIT
    else:
        # CACHE MISS → Firebase
        context = await lpt_client._reconstruct_full_company_profile(...)
        # Mettre en cache (TTL 1h)
        redis_client.setex(cache_key, 3600, json.dumps(context))

# Mode BACKEND
if mode == "BACKEND":
    # Toujours Firebase direct
    context = await lpt_client._reconstruct_full_company_profile(...)
```

**Appels Firebase :**
- ✅ Si CACHE MISS (Redis)
- ✅ Si mode BACKEND (toujours)

**Fréquence :** Rare (après expiration cache ou mode BACKEND)

---

## 🔍 CONDITIONS DE RÉCUPÉRATION

### **workflow_params dans session.user_context**

**Condition :** ✅ TOUJOURS (depuis correction)
```python
# Dans initialize_session_data()
self.user_context = {
    ...
    "workflow_params": full_profile.get("workflow_params", {})  # ✅
}
```

### **workflow_params dans brain.user_context**

**Condition :** ✅ SI brain créé APRÈS `initialize_session_data()`
```python
# Dans _ensure_brain_initialized()
brain.user_context = session.user_context  # ✅ workflow_params inclus
```

**⚠️ Problème si :**
- Brain créé AVANT `initialize_session_data()` → `session.user_context = None`
- Solution : Vérifier `session.user_context is not None` avant création brain

### **workflow_params dans System Prompt**

**Condition :** ✅ SI `brain.user_context` contient `workflow_params`
```python
# Dans build_principal_agent_prompt()
workflow_params = user_context.get("workflow_params", {})  # ← brain.user_context
```

### **workflow_params dans Payload LPT**

**Condition :** ✅ SI `brain.user_context` contient `workflow_params`
```python
# Dans launch_apbookeeper()
context = brain.get_user_context()  # ← Retourne brain.user_context
workflow_params = context.get('workflow_params', {})
```

---

## 📊 RÉSUMÉ DES SOURCES

| Étape | Source workflow_params | Firebase ? | Redis ? | Mémoire ? |
|-------|----------------------|-----------|---------|-----------|
| 1. initialize_session | Firebase | ✅ OUI | ❌ NON | ✅ session.user_context |
| 2. Création brain | session.user_context | ❌ NON | ❌ NON | ✅ brain.user_context |
| 3. Brain existant | brain.user_context | ❌ NON | ❌ NON | ✅ brain.user_context |
| 4. System prompt | brain.user_context | ❌ NON | ❌ NON | ✅ brain.user_context |
| 5. LPT payload | brain.user_context | ❌ NON | ❌ NON | ✅ brain.user_context |
| 6. Cache Redis | Redis → Firebase | ✅ SI MISS | ✅ SI HIT | ✅ brain.user_context |

---

## ⚠️ POINTS D'ATTENTION

1. **Ordre d'exécution :** `initialize_session_data()` DOIT être appelé AVANT création du brain
2. **Cache Redis :** TTL 1h, peut contenir d'anciennes valeurs
3. **Mode BACKEND :** Toujours Firebase direct (pas de cache)
4. **Référence partagée :** `brain.user_context = session.user_context` (même objet)

---

## ✅ CORRECTION APPLIQUÉE

**Avant :**
```python
self.user_context = {
    "client_uuid": ...,
    "mandate_path": ...,
    # ❌ workflow_params MANQUANT
}
```

**Après :**
```python
self.user_context = {
    "client_uuid": ...,
    "mandate_path": ...,
    "workflow_params": full_profile.get("workflow_params", {})  # ✅
}
```

