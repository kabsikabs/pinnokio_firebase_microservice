# 🗺️ CARTographie COMPLÈTE - Flux d'Authentification

**Date:** 2026-01-23  
**Scope:** Flux complet depuis la connexion WebSocket jusqu'à l'affichage du dashboard

---

## 📋 TABLE DES MATIÈRES

1. [Vue d'ensemble du flux](#vue-densemble)
2. [Cartographie des composants](#composants)
3. [Cartographie des caches Redis](#caches-redis)
4. [Flux détaillé étape par étape](#flux-détaillé)
5. [Dépendances entre composants](#dépendances)

---

## 🎯 VUE D'ENSEMBLE

```
┌─────────────────────────────────────────────────────────────────┐
│                    FLUX D'AUTHENTIFICATION                       │
└─────────────────────────────────────────────────────────────────┘

1. WebSocket Connection
   ↓
2. Authentification (auth.firebase_token)
   ↓
3. Dashboard Orchestration (dashboard.orchestrate_init)
   ├─ Phase 0: User Setup
   ├─ Phase 1: Company Selection
   ├─ Phase 2: Data Loading
   ├─ Phase 3: LLM Session
   └─ Phase 4: Realtime Subscriptions
   ↓
4. Page Navigation (page.restore_state)
   ↓
5. Page Orchestration (si cache MISS)
```

---

## 🧩 COMPOSANTS ET INSTANCES

### 1. **WebSocket Hub** (`app/ws_hub.py`)
- **Rôle:** Gestion des connexions WebSocket
- **Instance:** `hub = WebSocketHub()` (singleton)
- **Méthodes clés:**
  - `register(uid, ws)` - Enregistre une connexion
  - `broadcast(uid, message)` - Diffuse un message
  - `broadcast_threadsafe(uid, message)` - Broadcast depuis thread

### 2. **Auth Handlers** (`app/wrappers/auth_handlers.py`)
- **Rôle:** Gestion de l'authentification Firebase
- **Fonction principale:** `handle_firebase_token(payload)`
- **Dépendances:**
  - `firebase_admin.auth` - Vérification token
  - `redis_client` - Stockage session

### 3. **Dashboard Orchestration Handlers** (`app/wrappers/dashboard_orchestration_handlers.py`)
- **Rôle:** Orchestration complète du dashboard
- **Fonction principale:** `handle_orchestrate_init(uid, session_id, payload)`
- **Sous-composants:**
  - `UserSessionStateManager` - Gestion session utilisateur
  - `OrchestrationStateManager` - Gestion état orchestration
  - `_run_orchestration()` - Runner principal

### 4. **Session State Manager** (`app/llm_service/session_state_manager.py`)
- **Rôle:** Gestion état session LLM externalisé
- **Instance:** `SessionStateManager()` (singleton)
- **Méthodes clés:**
  - `save_session_state()` - Sauvegarde état complet
  - `load_session_state()` - Charge état
  - `update_session_state()` - Met à jour partiel

### 5. **Page State Manager** (`app/wrappers/page_state_manager.py`)
- **Rôle:** Cache des snapshots de pages
- **Instance:** `PageStateManager()` (singleton)
- **Méthodes clés:**
  - `save_page_state()` - Sauvegarde snapshot
  - `get_page_state()` - Récupère snapshot
  - `invalidate_page_state()` - Invalide cache

### 6. **Drive Cache Handlers** (`app/drive_cache_handlers.py`)
- **Rôle:** Cache des documents Google Drive
- **Instance:** `DriveCacheHandlers()` (singleton)
- **Méthodes clés:**
  - `get_documents()` - Récupère documents avec cache
  - `refresh_documents()` - Force refresh

### 7. **Firebase Management** (`app/firebase_providers.py`)
- **Rôle:** Accès aux données Firebase
- **Instance:** `FirebaseManagement()` (singleton)
- **Méthodes utilisées:**
  - `check_and_create_client_document()`
  - `fetch_all_mandates_light()`
  - `fetch_single_mandate()`
  - `fetch_journal_entries_by_mandat_id_without_source()`

### 8. **Dashboard Handlers** (`app/frontend/pages/dashboard/handlers.py`)
- **Rôle:** Chargement données dashboard
- **Instance:** `DashboardHandlers()` (singleton)
- **Méthode principale:** `full_data()`

### 9. **Static Data Handlers** (`app/wrappers/static_data_handlers.py`)
- **Rôle:** Données statiques (pays, langues, devises)
- **Instance:** `StaticDataHandlers()` (singleton)

### 10. **Listeners Manager** (`app/listeners_manager.py`)
- **Rôle:** Gestion listeners Firebase (notifications, messages)
- **Instance:** `ListenersManager()` (singleton)
- **Méthodes clés:**
  - `_ensure_user_watchers(uid)` - Attache listeners

---

## 💾 CACHES REDIS - CARTographie COMPLÈTE

### 🔐 NIVEAU 1: AUTHENTIFICATION & SESSION

#### 1.1 Session WebSocket (Auth)
**Clé:** `session:{uid}:{session_id}`  
**TTL:** 3600s (1 heure)  
**Créé par:** `auth_handlers.handle_firebase_token()`  
**Contenu:**
```json
{
  "token": "firebase_id_token",
  "user": {
    "id": "uid",
    "email": "user@example.com",
    "displayName": "John Doe",
    "photoURL": "https://...",
    "emailVerified": true
  },
  "auth_provider": "google",
  "created_at": "2026-01-23T20:57:52Z",
  "last_activity": "2026-01-23T20:57:52Z"
}
```

#### 1.2 User Session State
**Clé:** `user_session:{uid}:{session_id}`  
**TTL:** 7200s (2 heures)  
**Créé par:** `UserSessionStateManager.save_user_session()`  
**Contenu:**
```json
{
  "uid": "uid",
  "session_id": "session_id",
  "email": "user@example.com",
  "display_name": "John Doe",
  "photo_url": "https://...",
  "is_invited_user": false,
  "user_profile": "admin",
  "authorized_companies_ids": ["company1", "company2"],
  "share_settings": {},
  "created_at": "2026-01-23T20:57:52Z",
  "updated_at": "2026-01-23T20:57:52Z"
}
```

#### 1.3 Selected Company (User Session)
**Clé:** `user_session:{uid}:{session_id}:company`  
**TTL:** 86400s (24 heures)  
**Créé par:** `UserSessionStateManager.save_selected_company()`  
**Contenu:**
```json
{
  "company_id": "klk_space_id_685096",
  "company_name": "Ma Société",
  "mandate_path": "clients/.../mandates/...",
  "client_uuid": "uuid-123",
  "selected_at": "2026-01-23T20:57:52Z",
  "input_drive_doc_id": "1a2b3c4d5e6f",
  "base_currency": "CHF"
}
```

---

### 🎯 NIVEAU 2: ORCHESTRATION

#### 2.1 Orchestration State
**Clé:** `orchestration:{uid}:{session_id}:state`  
**TTL:** 3600s (1 heure)  
**Créé par:** `OrchestrationStateManager.create_orchestration()`  
**Contenu:**
```json
{
  "orchestration_id": "uuid-orchestration",
  "phase": "user_setup|company|data|llm|realtime|completed",
  "started_at": "2026-01-23T20:57:52Z",
  "updated_at": "2026-01-23T20:57:52Z",
  "cancellation_requested": false,
  "selected_company_id": "klk_space_id_685096",
  "is_first_connect": false,
  "is_invited_user": false,
  "widgets_status": {
    "balance": "pending|loading|completed|error",
    "metrics": "pending|loading|completed|error",
    "storage": "pending|loading|completed|error",
    "expenses": "pending|loading|completed|error",
    "tasks": "pending|loading|completed|error",
    "apbookeeper_jobs": "pending|loading|completed|error",
    "router_jobs": "pending|loading|completed|error",
    "banker_jobs": "pending|loading|completed|error",
    "approval_waitlist": "pending|loading|completed|error"
  },
  "errors": []
}
```

---

### 🏢 NIVEAU 3: SESSION STATE (LLM)

#### 3.1 LLM Session State
**Clé:** `session:{uid}:{company_id}:state`  
**TTL:** 7200s (2 heures)  
**Créé par:** `SessionStateManager.save_session_state()`  
**Contenu:**
```json
{
  "user_context": {
    "mandate_path": "clients/.../mandates/...",
    "client_uuid": "uuid-123",
    "input_drive_doc_id": "1a2b3c4d5e6f",
    "collection_name": "klk_space_id_685096",
    "base_currency": "CHF",
    "company_name": "Ma Société"
  },
  "jobs_data": {
    "APBOOKEEPER": [...],
    "ROUTER": [...],
    "BANK": [...]
  },
  "jobs_metrics": {
    "APBOOKEEPER": {
      "to_process": 10,
      "in_process": 5,
      "pending": 3
    },
    "ROUTING": {...},
    "BANK": {...}
  },
  "is_on_chat_page": false,
  "current_active_thread": null,
  "active_threads_by_session": {
    "session_1": "thread_abc123",
    "session_2": "thread_xyz789"
  },
  "thread_states": {},
  "active_tasks": {},
  "intermediation_mode": {},
  "last_activity": {},
  "thread_contexts": {},
  "active_threads": [],
  "updated_at": "2026-01-23T20:57:52Z",
  "version": "1.0"
}
```

---

### 📄 NIVEAU 4: PAGE STATE

#### 4.1 Page State (Dashboard)
**Clé:** `page_state:{uid}:{company_id}:dashboard`  
**TTL:** 1800s (30 minutes)  
**Créé par:** `PageStateManager.save_page_state()`  
**Contenu:**
```json
{
  "version": "1.0",
  "page": "dashboard",
  "company_id": "klk_space_id_685096",
  "mandate_path": "clients/.../mandates/...",
  "loaded_at": "2026-01-23T20:57:52Z",
  "data": {
    "balance": {...},
    "metrics": {...},
    "storage": {...},
    "expenses": {...},
    "tasks": {...},
    "approvals": {...}
  }
}
```

#### 4.2 Page State (Routing)
**Clé:** `page_state:{uid}:{company_id}:routing`  
**TTL:** 1800s (30 minutes)  
**Créé par:** `PageStateManager.save_page_state()` (via routing orchestration)

#### 4.3 Page State (Banking)
**Clé:** `page_state:{uid}:{company_id}:banking`  
**TTL:** 1800s (30 minutes)  
**Créé par:** `PageStateManager.save_page_state()` (via banking orchestration)

#### 4.4 Page State (Autres pages)
**Clés:**
- `page_state:{uid}:{company_id}:chat`
- `page_state:{uid}:{company_id}:coa`
- `page_state:{uid}:{company_id}:company_settings`
- `page_state:{uid}:{company_id}:hr`
- etc.

---

### 📦 NIVEAU 5: BUSINESS DATA (Architecture 3 Niveaux)

#### 5.1 Business - Bank
**Clé:** `business:{uid}:{company_id}:bank`  
**TTL:** 2400s (40 minutes)  
**Créé par:** `UnifiedCacheManager` / `BankingHandlers`  
**Contenu:** Comptes, transactions, batches

#### 5.2 Business - Routing
**Clé:** `business:{uid}:{company_id}:routing`  
**TTL:** 1800s (30 minutes)  
**Créé par:** `UnifiedCacheManager` / `RoutingHandlers`  
**Contenu:** Documents Drive, statuts

#### 5.3 Business - Invoices
**Clé:** `business:{uid}:{company_id}:invoices`  
**TTL:** 2400s (40 minutes)  
**Créé par:** `UnifiedCacheManager` / `APBookkeeperHandlers`  
**Contenu:** Factures fournisseurs

#### 5.4 Business - Expenses
**Clé:** `business:{uid}:{company_id}:expenses`  
**TTL:** 2400s (40 minutes)  
**Créé par:** `UnifiedCacheManager` / `ExpensesHandlers`  
**Contenu:** Notes de frais

#### 5.5 Business - COA
**Clé:** `business:{uid}:{company_id}:coa`  
**TTL:** 3600s (1 heure)  
**Créé par:** `UnifiedCacheManager` / `COAHandlers`  
**Contenu:** Plan comptable, fonctions

#### 5.6 Business - Dashboard
**Clé:** `business:{uid}:{company_id}:dashboard`  
**TTL:** 1800s (30 minutes)  
**Créé par:** `UnifiedCacheManager` / `DashboardHandlers`  
**Contenu:** Tasks, approvals, activity

#### 5.7 Business - Chat
**Clé:** `business:{uid}:{company_id}:chat`  
**TTL:** 86400s (24 heures)  
**Créé par:** `UnifiedCacheManager` / `ChatHandlers`  
**Contenu:** Sessions chat, messages

#### 5.8 Business - HR
**Clé:** `business:{uid}:{company_id}:hr`  
**TTL:** 3600s (1 heure)  
**Créé par:** `UnifiedCacheManager` / `HRHandlers`  
**Contenu:** Employés, contrats

---

### 🏢 NIVEAU 6: COMPANY CONTEXT

#### 6.1 Company List
**Clé:** `company:{uid}:list`  
**TTL:** 3600s (1 heure)  
**Créé par:** `UnifiedCacheManager`  
**Contenu:** Liste des sociétés de l'utilisateur

#### 6.2 Company Context
**Clé:** `company:{uid}:{company_id}:context`  
**TTL:** 3600s (1 heure)  
**Créé par:** `UnifiedCacheManager`  
**Contenu:** Contexte société (mandatePath, clientUuid, etc.)

#### 6.3 Company Settings
**Clé:** `company:{uid}:{company_id}:settings`  
**TTL:** 3600s (1 heure)  
**Créé par:** `UnifiedCacheManager`  
**Contenu:** Paramètres société (workflow, telegram, erp)

---

### 👤 NIVEAU 7: USER DATA

#### 7.1 User Profile
**Clé:** `user:{uid}:profile`  
**TTL:** 86400s (24 heures)  
**Créé par:** `UnifiedCacheManager`  
**Contenu:** Profil utilisateur (email, displayName, photoURL)

#### 7.2 User Preferences
**Clé:** `user:{uid}:preferences`  
**TTL:** 86400s (24 heures)  
**Créé par:** `UnifiedCacheManager`  
**Contenu:** Préférences (locale, theme)

#### 7.3 Static Data
**Clé:** `static_data:v1`  
**TTL:** 86400s (24 heures)  
**Créé par:** `StaticDataHandlers`  
**Contenu:** Pays, langues, devises, ERPs, DMS

---

### 💬 NIVEAU 8: CHAT & MESSAGING

#### 8.1 Chat History
**Clé:** `chat:{uid}:{company_id}:{thread_key}:history`  
**TTL:** 86400s (24 heures)  
**Créé par:** `ChatHistoryManager`  
**Contenu:** Historique conversation LLM

#### 8.2 WS Message Buffer
**Clé:** `pending_ws_messages:{uid}:{thread_key}`  
**TTL:** 300s (5 minutes)  
**Créé par:** `WSMessageBuffer`  
**Contenu:** Messages WebSocket en attente

---

### 🔒 NIVEAU 9: INFRASTRUCTURE

#### 9.1 Distributed Lock
**Clé:** `lock:{type}:{resource_id}`  
**TTL:** 300s (5 minutes)  
**Créé par:** `DistributedLock`  
**Contenu:** Lock distribué pour opérations critiques

#### 9.2 Registry - User
**Clé:** `registry:user:{uid}`  
**TTL:** 86400s (24 heures)  
**Créé par:** `RegistryWrapper`  
**Contenu:** Registre utilisateur (sessions actives)

#### 9.3 Registry - Listeners
**Clé:** `registry:listeners:{uid}`  
**TTL:** Variable  
**Créé par:** `RegistryListeners`  
**Contenu:** État des listeners Firebase

#### 9.4 Idempotency
**Clé:** `idemp:{key}`  
**TTL:** 900s (15 minutes)  
**Créé par:** `_idempotency_mark_if_new()`  
**Contenu:** Marqueur idempotence pour requêtes

---

### 📁 NIVEAU 10: DRIVE CACHE

#### 10.1 Drive Documents Cache
**Clé:** `drive_cache:{uid}:{company_id}:{drive_id}`  
**TTL:** 1800s (30 minutes)  
**Créé par:** `DriveCacheHandlers.get_documents()`  
**Contenu:** Documents Google Drive avec statuts Firebase

---

## 🔄 FLUX DÉTAILLÉ ÉTAPE PAR ÉTAPE

### ÉTAPE 1: Connexion WebSocket
**Composant:** `main.py` → `websocket_endpoint()`  
**Actions:**
1. Accept WebSocket
2. Extract `uid` from query params
3. Register connection: `hub.register(uid, ws)`
4. Start listeners: `listeners_manager._ensure_user_watchers(uid)`

**Caches créés:**
- Aucun (juste enregistrement connexion)

---

### ÉTAPE 2: Authentification Firebase Token
**Composant:** `auth_handlers.handle_firebase_token()`  
**Actions:**
1. Extract token, uid, session_id from payload
2. Verify token: `firebase_admin.auth.verify_id_token()`
3. Validate UID consistency
4. Create session data
5. Store in Redis

**Caches créés:**
```
✅ session:{uid}:{session_id}
   TTL: 3600s
   Contenu: Token, user info, timestamps
```

**Réponse:** `auth.session_confirmed`

---

### ÉTAPE 3: Dashboard Orchestration Init
**Composant:** `dashboard_orchestration_handlers.handle_orchestrate_init()`  
**Actions:**
1. Cancel existing orchestration (if any)
2. Create new orchestration state
3. Start background orchestration task

**Caches créés:**
```
✅ orchestration:{uid}:{session_id}:state
   TTL: 3600s
   Contenu: orchestration_id, phase, widgets_status
```

**Réponse:** `dashboard.orchestrate_init` (avec orchestration_id)

---

### ÉTAPE 4: Phase 0 - User Setup
**Composant:** `_run_user_setup_phase()`  
**Actions:**
1. Check/create client document in Firebase
2. Check first_connect → process top-up (50$)
3. Process share settings
4. Save user session

**Caches créés:**
```
✅ user_session:{uid}:{session_id}
   TTL: 7200s
   Contenu: user_data, share_settings, authorized_companies_ids
```

**Événements envoyés:**
- `dashboard.phase_start` (phase: "user_setup")
- `dashboard.phase_complete` (phase: "user_setup")

---

### ÉTAPE 5: Phase 1 - Company Selection
**Composant:** `_run_company_phase()`  
**Actions:**
1. Fetch all mandates (light): `fetch_all_mandates_light()`
2. Auto-select first company (or use target_company_id)
3. Fetch full company details: `fetch_single_mandate()`
4. Save selected company

**Caches créés:**
```
✅ user_session:{uid}:{session_id}:company
   TTL: 86400s
   Contenu: company_id, mandate_path, client_uuid, input_drive_doc_id

✅ company:{uid}:list
   TTL: 3600s
   Contenu: Liste des sociétés

✅ company:{uid}:{company_id}:context
   TTL: 3600s
   Contenu: Contexte société complet
```

**Événements envoyés:**
- `dashboard.phase_start` (phase: "company")
- `company.list` (liste des sociétés)
- `company.select` (société sélectionnée)
- `dashboard.phase_complete` (phase: "company")

---

### ÉTAPE 6: Phase 2 - Data Loading
**Composant:** `_run_data_phase()`  
**Actions:**
1. Load static data (pays, langues, devises)
2. Load dashboard widgets in parallel:
   - Balance widget
   - Metrics widget
   - Storage widget
   - Expenses widget
   - Tasks widget
   - APBookkeeper jobs
   - Router jobs
   - Banker jobs
   - Approval waitlist
3. Save page state for dashboard

**Caches créés:**
```
✅ static_data:v1
   TTL: 86400s
   Contenu: Pays, langues, devises, ERPs, DMS

✅ business:{uid}:{company_id}:dashboard
   TTL: 1800s
   Contenu: Tasks, approvals, activity

✅ page_state:{uid}:{company_id}:dashboard
   TTL: 1800s
   Contenu: Snapshot complet dashboard

✅ drive_cache:{uid}:{company_id}:{drive_id}
   TTL: 1800s
   Contenu: Documents Drive (si input_drive_doc_id existe)
```

**Événements envoyés:**
- `dashboard.phase_start` (phase: "data")
- `static_data.loaded` (données statiques)
- `dashboard.data_loading_progress` (pour chaque widget)
- `dashboard.full_data` (données complètes)
- `dashboard.phase_complete` (phase: "data")

---

### ÉTAPE 7: Phase 3 - LLM Session
**Composant:** `_run_llm_phase()`  
**Actions:**
1. Initialize LLM session
2. Save session state with user_context

**Caches créés:**
```
✅ session:{uid}:{company_id}:state
   TTL: 7200s
   Contenu: user_context, jobs_data, jobs_metrics, presence
```

**Événements envoyés:**
- `dashboard.phase_start` (phase: "llm")
- `dashboard.phase_complete` (phase: "llm")

---

### ÉTAPE 8: Phase 4 - Realtime Subscriptions
**Composant:** `_run_realtime_phase()`  
**Actions:**
1. Setup realtime subscriptions
2. Attach Firebase listeners (notifications, messages)

**Caches créés:**
```
✅ registry:listeners:{uid}
   TTL: Variable
   Contenu: État des listeners (notif, msg)
```

**Événements envoyés:**
- `dashboard.phase_start` (phase: "realtime")
- `notif.sync` (synchronisation notifications)
- `msg.sync` (synchronisation messages)
- `dashboard.phase_complete` (phase: "realtime")

---

### ÉTAPE 9: Navigation vers une page (ex: Routing)
**Composant:** `main.py` → `page.restore_state` handler  
**Actions:**
1. Frontend envoie `page.restore_state`
2. Backend vérifie `page_state` cache
3. Si HIT → retourne données immédiatement
4. Si MISS → retourne `page.state_not_found`

**Caches lus:**
```
🔍 page_state:{uid}:{company_id}:routing
   Si trouvé → retour immédiat
   Si non trouvé → déclenche orchestration
```

---

### ÉTAPE 10: Page Orchestration (si cache MISS)
**Composant:** `routing/orchestration.handle_routing_orchestrate_init()`  
**Actions:**
1. Get company context from SessionStateManager
2. Fetch Drive documents (via drive_cache_handlers)
3. Fetch Firebase processed documents
4. Build routing data structure
5. Save page state
6. Send routing.full_data

**Caches créés:**
```
✅ page_state:{uid}:{company_id}:routing
   TTL: 1800s
   Contenu: Documents, counts, pagination, oauth status
```

**Caches lus:**
```
🔍 session:{uid}:{company_id}:state
   → Récupère: input_drive_doc_id, mandate_path, client_uuid

🔍 drive_cache:{uid}:{company_id}:{drive_id}
   → Si HIT: retourne documents
   → Si MISS: appelle Drive API + Firebase status check
```

---

## 🔗 DÉPENDANCES ENTRE COMPOSANTS

```
┌─────────────────────────────────────────────────────────────┐
│                    GRAPHE DE DÉPENDANCES                     │
└─────────────────────────────────────────────────────────────┘

WebSocket Hub
    ↓
Auth Handlers
    ↓ (créé: session:{uid}:{session_id})
    ↓
Dashboard Orchestration
    ├─ UserSessionStateManager
    │   └─ (créé: user_session:{uid}:{session_id})
    │
    ├─ OrchestrationStateManager
    │   └─ (créé: orchestration:{uid}:{session_id}:state)
    │
    ├─ StaticDataHandlers
    │   └─ (créé: static_data:v1)
    │
    ├─ DashboardHandlers
    │   └─ (créé: business:{uid}:{cid}:dashboard)
    │
    ├─ DriveCacheHandlers
    │   └─ (créé: drive_cache:{uid}:{cid}:{drive_id})
    │
    ├─ SessionStateManager
    │   └─ (créé: session:{uid}:{cid}:state)
    │       └─ Contient: user_context (mandate_path, input_drive_doc_id)
    │
    └─ PageStateManager
        └─ (créé: page_state:{uid}:{cid}:dashboard)

Page Orchestration (Routing, Banking, etc.)
    ↓ (lit: session:{uid}:{cid}:state)
    │   └─ Récupère: input_drive_doc_id, mandate_path
    ↓
    ├─ DriveCacheHandlers (si routing)
    │   └─ (lit/créé: drive_cache:{uid}:{cid}:{drive_id})
    │
    ├─ FirebaseManagement
    │   └─ (lit Firebase directement)
    │
    └─ PageStateManager
        └─ (créé: page_state:{uid}:{cid}:{page})
```

---

## 📊 RÉSUMÉ DES CLÉS REDIS PAR NIVEAU

### 🔐 AUTHENTIFICATION (Niveau 1)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `session:{uid}:{session_id}` | 1h | `auth_handlers` |
| `user_session:{uid}:{session_id}` | 2h | `UserSessionStateManager` |
| `user_session:{uid}:{session_id}:company` | 24h | `UserSessionStateManager` |

### 🎯 ORCHESTRATION (Niveau 2)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `orchestration:{uid}:{session_id}:state` | 1h | `OrchestrationStateManager` |

### 🏢 SESSION STATE (Niveau 3)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `session:{uid}:{company_id}:state` | 2h | `SessionStateManager` |

### 📄 PAGE STATE (Niveau 4)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `page_state:{uid}:{company_id}:dashboard` | 30min | `PageStateManager` |
| `page_state:{uid}:{company_id}:routing` | 30min | `PageStateManager` |
| `page_state:{uid}:{company_id}:banking` | 30min | `PageStateManager` |
| `page_state:{uid}:{company_id}:{page}` | 30min | `PageStateManager` |

### 📦 BUSINESS DATA (Niveau 5)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `business:{uid}:{cid}:bank` | 40min | `UnifiedCacheManager` |
| `business:{uid}:{cid}:routing` | 30min | `UnifiedCacheManager` |
| `business:{uid}:{cid}:invoices` | 40min | `UnifiedCacheManager` |
| `business:{uid}:{cid}:expenses` | 40min | `UnifiedCacheManager` |
| `business:{uid}:{cid}:coa` | 1h | `UnifiedCacheManager` |
| `business:{uid}:{cid}:dashboard` | 30min | `UnifiedCacheManager` |
| `business:{uid}:{cid}:chat` | 24h | `UnifiedCacheManager` |
| `business:{uid}:{cid}:hr` | 1h | `UnifiedCacheManager` |

### 🏢 COMPANY CONTEXT (Niveau 6)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `company:{uid}:list` | 1h | `UnifiedCacheManager` |
| `company:{uid}:{cid}:context` | 1h | `UnifiedCacheManager` |
| `company:{uid}:{cid}:settings` | 1h | `UnifiedCacheManager` |

### 👤 USER DATA (Niveau 7)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `user:{uid}:profile` | 24h | `UnifiedCacheManager` |
| `user:{uid}:preferences` | 24h | `UnifiedCacheManager` |
| `static_data:v1` | 24h | `StaticDataHandlers` |

### 💬 CHAT & MESSAGING (Niveau 8)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `chat:{uid}:{cid}:{thread}:history` | 24h | `ChatHistoryManager` |
| `pending_ws_messages:{uid}:{thread}` | 5min | `WSMessageBuffer` |

### 🔒 INFRASTRUCTURE (Niveau 9)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `lock:{type}:{resource_id}` | 5min | `DistributedLock` |
| `registry:user:{uid}` | 24h | `RegistryWrapper` |
| `registry:listeners:{uid}` | Variable | `RegistryListeners` |
| `idemp:{key}` | 15min | `_idempotency_mark_if_new()` |

### 📁 DRIVE CACHE (Niveau 10)
| Clé | TTL | Créé par |
|-----|-----|----------|
| `drive_cache:{uid}:{cid}:{drive_id}` | 30min | `DriveCacheHandlers` |

---

## 🎯 POINTS CLÉS

1. **Session WebSocket** (`session:{uid}:{session_id}`) est créée lors de l'auth
2. **SessionStateManager** (`session:{uid}:{cid}:state`) est la source de vérité pour le contexte société
3. **PageStateManager** (`page_state:{uid}:{cid}:{page}`) permet le chargement rapide après refresh
4. **Business caches** (`business:{uid}:{cid}:{domain}`) stockent les données métier brutes
5. **Drive cache** (`drive_cache:{uid}:{cid}:{drive_id}`) est partagé entre dashboard et routing

---

**Document généré le:** 2026-01-23  
**Version:** 1.0
