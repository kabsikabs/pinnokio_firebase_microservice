# 🔄 Schéma Complet: Flux Auth → Cache → Sources

## Vue d'Ensemble

```mermaid
flowchart TB
    subgraph Auth["1. AUTHENTIFICATION"]
        A1[Frontend: auth.firebase_token]
        A2[app/wrappers/auth_handlers.py<br/>handle_firebase_token]
        A3[Redis: session:{uid}:{session_id}]
        A4[WS: auth.session_confirmed]
    end
    
    subgraph Dashboard["2. DASHBOARD ORCHESTRATION"]
        D1[Frontend: dashboard.orchestrate_init]
        D2[app/wrappers/dashboard_orchestration_handlers.py<br/>handle_orchestrate_init]
        D3[Phase 0: User Setup]
        D4[Phase 1: Company Selection]
        D5[Phase 2: Data Loading]
    end
    
    subgraph DataLoading["3. CHARGEMENT DONNÉES MÉTIER"]
        DL1[app/frontend/pages/dashboard/handlers.py<br/>DashboardHandlers.full_data]
        DL2[Parallèle: 4 domaines]
    end
    
    subgraph Banking["4a. BANKING"]
        B1[app/firebase_cache_handlers.py<br/>get_bank_transactions]
        B2[app/cache/unified_cache_manager.py<br/>get_cached_data]
        B3{Cache HIT?}
        B4[app/erp_service.py<br/>ERPService.get_odoo_bank_statement]
        B5[ERP Odoo API]
        B6[app/cache/unified_cache_manager.py<br/>set_cached_data]
        B7[Redis: business:{uid}:{cid}:bank]
    end
    
    subgraph Routing["4b. ROUTING"]
        R1[app/drive_cache_handlers.py<br/>get_documents]
        R2[app/cache/unified_cache_manager.py<br/>get_cached_data]
        R3{Cache HIT?}
        R4[app/driveClientService.py<br/>fetch_from_drive]
        R5[Google Drive API]
        R6[app/firebase_providers.py<br/>fetch_journal_entries_by_mandat_id]
        R7[Firebase Journal]
        R8[app/cache/unified_cache_manager.py<br/>set_cached_data]
        R9[Redis: business:{uid}:{cid}:routing]
    end
    
    subgraph APBookkeeper["4c. APBOOKEEPER"]
        AP1[app/pinnokio_agentic_workflow/tools/job_loader.py<br/>_fetch_apbookeeper_from_firebase]
        AP2[app/firebase_providers.py<br/>fetch_journal_entries_by_mandat_id]
        AP3[Firebase Journal<br/>documents/accounting/invoices/doc_to_do]
        AP4[app/firebase_providers.py<br/>check_job_status]
        AP5[Firebase Notifications]
        AP6[app/cache/unified_cache_manager.py<br/>set_cached_data]
        AP7[Redis: business:{uid}:{cid}:invoices]
    end
    
    subgraph Expenses["4d. EXPENSES"]
        E1[app/firebase_cache_handlers.py<br/>get_expenses]
        E2[app/cache/unified_cache_manager.py<br/>get_cached_data]
        E3{Cache HIT?}
        E4[app/firebase_providers.py<br/>fetch_expenses_by_mandate]
        E5[Firebase<br/>mandates/{cid}/expenses]
        E6[app/cache/unified_cache_manager.py<br/>set_cached_data]
        E7[Redis: business:{uid}:{cid}:expenses]
    end
    
    A1 --> A2
    A2 --> A3
    A3 --> A4
    A4 --> D1
    D1 --> D2
    D2 --> D3
    D3 --> D4
    D4 --> D5
    D5 --> DL1
    DL1 --> DL2
    
    DL2 --> B1
    DL2 --> R1
    DL2 --> AP1
    DL2 --> E1
    
    B1 --> B2
    B2 --> B3
    B3 -->|HIT| B7
    B3 -->|MISS| B4
    B4 --> B5
    B5 --> B6
    B6 --> B7
    
    R1 --> R2
    R2 --> R3
    R3 -->|HIT| R9
    R3 -->|MISS| R4
    R4 --> R5
    R5 --> R6
    R6 --> R7
    R7 --> R8
    R8 --> R9
    
    AP1 --> AP2
    AP2 --> AP3
    AP3 --> AP4
    AP4 --> AP5
    AP5 --> AP6
    AP6 --> AP7
    
    E1 --> E2
    E2 --> E3
    E3 -->|HIT| E7
    E3 -->|MISS| E4
    E4 --> E5
    E5 --> E6
    E6 --> E7
```

---

## 📋 Détail Fichier par Fichier

### 1️⃣ AUTHENTIFICATION

```
┌─────────────────────────────────────────────────────────────┐
│ FRONTEND                                                     │
│ ──────────────────────────────────────────────────────────── │
│ wsClient.send({                                              │
│   type: "auth.firebase_token",                               │
│   payload: { token, uid, sessionId }                        │
│ })                                                           │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/main.py                                                  │
│ ──────────────────────────────────────────────────────────── │
│ @app.websocket("/ws")                                        │
│ async def websocket_endpoint(ws: WebSocket):                │
│   if msg_type == "auth.firebase_token":                      │
│     from .wrappers.auth_handlers import handle_firebase_token│
│     response = await handle_firebase_token(msg_payload)      │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/wrappers/auth_handlers.py                                │
│ ──────────────────────────────────────────────────────────── │
│ async def handle_firebase_token(payload):                   │
│   1. firebase_admin.auth.verify_id_token(token)              │
│   2. session_key = f"session:{uid}:{session_id}"             │
│   3. redis.setex(session_key, 3600, session_data)           │
│   4. return {"type": "auth.session_confirmed", ...}         │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ REDIS                                                        │
│ ──────────────────────────────────────────────────────────── │
│ session:{uid}:{session_id}                                   │
│ {                                                            │
│   "token": "...",                                          │
│   "user": { "id": "...", "email": "..." },                   │
│   "created_at": "2026-01-23T..."                            │
│ }                                                            │
│ TTL: 3600s (1h)                                              │
└─────────────────────────────────────────────────────────────┘
```

---

### 2️⃣ DASHBOARD ORCHESTRATION

```
┌─────────────────────────────────────────────────────────────┐
│ FRONTEND                                                     │
│ ──────────────────────────────────────────────────────────── │
│ wsClient.send({                                              │
│   type: "dashboard.orchestrate_init",                        │
│   payload: { sessionId, target_company_id? }                │
│ })                                                           │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/main.py                                                  │
│ ──────────────────────────────────────────────────────────── │
│ elif msg_type == "dashboard.orchestrate_init":               │
│   from .wrappers.dashboard_orchestration_handlers import     │
│       handle_orchestrate_init                                │
│   await handle_orchestrate_init(uid, session_id, payload)   │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/wrappers/dashboard_orchestration_handlers.py             │
│ ──────────────────────────────────────────────────────────── │
│ async def handle_orchestrate_init(...):                      │
│   ├─► Phase 0: _run_user_setup_phase()                      │
│   │   └─► app/wrappers/static_data_handlers.py              │
│   │       └─► static_data.loaded                            │
│   │                                                          │
│   ├─► Phase 1: _run_company_phase()                         │
│   │   ├─► app/firebase_providers.py                         │
│   │   │   └─► fetch_all_mandates_light()                   │
│   │   └─► app/firebase_providers.py                         │
│   │       └─► fetch_single_mandate()                        │
│   │                                                          │
│   └─► Phase 2: _run_data_phase()                            │
│       └─► app/frontend/pages/dashboard/handlers.py          │
│           └─► DashboardHandlers.full_data()                  │
└─────────────────────────────────────────────────────────────┘
```

---

### 3️⃣ CHARGEMENT DONNÉES MÉTIER (Dashboard)

```
┌─────────────────────────────────────────────────────────────┐
│ app/frontend/pages/dashboard/handlers.py                    │
│ ──────────────────────────────────────────────────────────── │
│ class DashboardHandlers:                                     │
│   async def full_data(self, user_id, company_id, ...):      │
│     # Chargement PARALLÈLE via asyncio.gather()             │
│     results = await asyncio.gather(                         │
│       self._get_company_info(...),                           │
│       self._get_storage_info(...),                           │
│       self._get_metrics(...),        ← 4 domaines           │
│       self._get_jobs_by_category(...),                      │
│       self._get_pending_approvals(...),                      │
│       self._get_tasks(...),                                  │
│       self._get_expenses(...),                               │
│       self._get_activity(...),                                │
│       self._get_alerts(...),                                  │
│       self._get_balance_info(...)                             │
│     )                                                        │
│                                                              │
│   async def _get_metrics(...):                              │
│     # Appelle les 4 domaines                                │
│     ├─► _get_bank_metrics()                                 │
│     ├─► _get_routing_metrics()                               │
│     ├─► _get_ap_metrics()                                    │
│     └─► _get_expenses_metrics()                             │
└─────────────────────────────────────────────────────────────┘
```

---

### 4️⃣ BANKING - Flux Complet

```
┌─────────────────────────────────────────────────────────────┐
│ app/frontend/pages/dashboard/handlers.py                    │
│ ──────────────────────────────────────────────────────────── │
│ async def _get_bank_metrics(user_id, company_id):           │
│   from app.firebase_cache_handlers import                   │
│       get_firebase_cache_handlers                           │
│   handlers = get_firebase_cache_handlers()                  │
│   data = await handlers.get_bank_transactions(...)          │
│   # Calcule métriques depuis data                           │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/firebase_cache_handlers.py                              │
│ ──────────────────────────────────────────────────────────── │
│ class FirebaseCacheHandlers:                                │
│   async def get_bank_transactions(                          │
│       self, user_id, company_id, client_uuid, bank_erp      │
│   ):                                                        │
│     # 1. TENTATIVE CACHE                                     │
│     from app.cache.unified_cache_manager import              │
│         get_unified_cache_manager                            │
│     cache = get_unified_cache_manager()                      │
│     cached = await cache.get_cached_data(                    │
│         user_id, company_id, "bank", "transactions",         │
│         ttl_seconds=2400                                     │
│     )                                                        │
│                                                              │
│     if cached:                                               │
│       return {"data": cached["data"], "source": "cache"}    │
│                                                              │
│     # 2. FALLBACK ERP                                        │
│     from app.erp_service import ERPService                   │
│     bank_transactions = await asyncio.to_thread(            │
│       ERPService.get_odoo_bank_statement_move_line_not_rec, │
│       user_id, company_id, client_uuid, None, False         │
│     )                                                        │
│                                                              │
│     # 3. ORGANISATION                                        │
│     organized = self._organize_bank_transactions_by_status(  │
│       bank_transactions                                      │
│     )                                                        │
│                                                              │
│     # 4. SAUVEGARDE CACHE                                    │
│     await cache.set_cached_data(                             │
│       user_id, company_id, "bank", "transactions",           │
│       organized, ttl_seconds=2400                            │
│     )                                                        │
│                                                              │
│     return {"data": organized, "source": "erp"}             │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/cache/unified_cache_manager.py                          │
│ ──────────────────────────────────────────────────────────── │
│ class UnifiedCacheManager:                                  │
│   async def get_cached_data(                                 │
│       self, user_id, company_id, data_type, sub_type, ttl   │
│   ):                                                        │
│     # Migration legacy → nouveau                             │
│     level, domain = LEGACY_TO_BUSINESS_MAP.get(              │
│       (data_type, sub_type)                                 │
│     )                                                        │
│     # "bank", "transactions" → BUSINESS, "bank"             │
│                                                              │
│     # Construit clé: business:{uid}:{cid}:bank             │
│     cache_key = build_business_key(user_id, company_id,      │
│                                    domain)                   │
│                                                              │
│     # Lit Redis                                              │
│     raw = await self.redis.get(cache_key)                    │
│     if raw:                                                  │
│       return json.loads(raw)                                 │
│     return None                                              │
│                                                              │
│   async def set_cached_data(...):                            │
│     cache_key = build_business_key(...)                      │
│     await self.redis.setex(                                  │
│       cache_key, ttl, json.dumps(data)                       │
│     )                                                        │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/erp_service.py                                          │
│ ──────────────────────────────────────────────────────────── │
│ class ERPService:                                            │
│   @staticmethod                                              │
│   def get_odoo_bank_statement_move_line_not_rec(            │
│       user_id, company_id, client_uuid, journal_id,          │
│       reconciled                                             │
│   ):                                                        │
│     # Connexion Odoo (cache 30min)                           │
│     odoo = get_odoo_connection(client_uuid)                 │
│                                                              │
│     # Requête Odoo API                                       │
│     transactions = odoo.env['account.move.line'].search([   │
│       ('reconciled', '=', False),                           │
│       ('account_id.type', '=', 'liquidity')                 │
│     ])                                                       │
│                                                              │
│     return transactions                                      │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ REDIS                                                        │
│ ──────────────────────────────────────────────────────────── │
│ business:{uid}:{cid}:bank                                    │
│ {                                                            │
│   "to_reconcile": [...],                                     │
│   "in_process": [...],                                       │
│   "pending": [...],                                          │
│   "matched": [...]                                           │
│ }                                                            │
│ TTL: 2400s (40min)                                           │
└─────────────────────────────────────────────────────────────┘
```

---

### 5️⃣ ROUTING - Flux Complet

```
┌─────────────────────────────────────────────────────────────┐
│ app/frontend/pages/dashboard/handlers.py                    │
│ ──────────────────────────────────────────────────────────── │
│ async def _get_routing_metrics(user_id, company_id):        │
│   from app.drive_cache_handlers import                      │
│       get_drive_cache_handlers                              │
│   handlers = get_drive_cache_handlers()                     │
│   data = await handlers.get_documents(                       │
│     user_id, company_id, input_drive_id                     │
│   )                                                          │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/drive_cache_handlers.py                                 │
│ ──────────────────────────────────────────────────────────── │
│ class DriveCacheHandlers:                                    │
│   async def get_documents(                                   │
│       self, user_id, company_id, input_drive_id             │
│   ):                                                        │
│     # 1. TENTATIVE CACHE                                     │
│     from app.cache.unified_cache_manager import              │
│         get_unified_cache_manager                            │
│     cache = get_unified_cache_manager()                      │
│     cached = await cache.get_cached_data(                    │
│       user_id, company_id, "drive", "documents",            │
│       ttl_seconds=1800                                       │
│     )                                                        │
│                                                              │
│     if cached:                                               │
│       return {"data": cached["data"], "source": "cache"}    │
│                                                              │
│     # 2. FALLBACK GOOGLE DRIVE                               │
│     drive_data = await self._fetch_from_drive(               │
│       user_id, input_drive_id                               │
│     )                                                        │
│                                                              │
│     # 3. VÉRIFICATION STATUTS FIREBASE                       │
│     from app.firebase_providers import FirebaseManagement    │
│     firebase_mgmt = FirebaseManagement()                     │
│     journal_entries = firebase_mgmt.                         │
│       fetch_journal_entries_by_mandat_id(...)              │
│                                                              │
│     # 4. ORGANISATION                                        │
│     organized = self._organize_documents_with_firebase(      │
│       drive_data, journal_entries                           │
│     )                                                        │
│                                                              │
│     # 5. SAUVEGARDE CACHE                                    │
│     await cache.set_cached_data(...)                         │
│                                                              │
│     return {"data": organized, "source": "drive"}           │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/driveClientService.py                                    │
│ ──────────────────────────────────────────────────────────── │
│ async def _fetch_from_drive(self, user_id, drive_id):       │
│   # Authentification OAuth                                  │
│   credentials = get_google_credentials(user_id)              │
│                                                              │
│   # Google Drive API                                         │
│   service = build('drive', 'v3', credentials=credentials)   │
│   files = service.files().list(                              │
│     q=f"'{drive_id}' in parents",                           │
│     fields="files(id,name,mimeType,createdTime)"            │
│   ).execute()                                                │
│                                                              │
│   return files.get('files', [])                              │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/firebase_providers.py                                   │
│ ──────────────────────────────────────────────────────────── │
│ class FirebaseManagement:                                   │
│   def fetch_journal_entries_by_mandat_id(                   │
│       self, user_id, company_id, source, departement        │
│   ):                                                        │
│     # Firebase Firestore                                     │
│     db = get_firestore()                                     │
│     journal_ref = db.collection("journal")                   │
│       .document(user_id)                                     │
│       .collection("entries")                                 │
│       .where("mandate_id", "==", company_id)                │
│       .where("source", "==", source)                        │
│       .where("departement", "==", departement)              │
│                                                              │
│     return [doc.to_dict() for doc in journal_ref.stream()]  │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ REDIS                                                        │
│ ──────────────────────────────────────────────────────────── │
│ business:{uid}:{cid}:routing                                 │
│ {                                                            │
│   "to_process": [...],                                       │
│   "in_process": [...],                                       │
│   "pending": [...],                                          │
│   "processed": [...]                                         │
│ }                                                            │
│ TTL: 1800s (30min)                                           │
└─────────────────────────────────────────────────────────────┘
```

---

### 6️⃣ APBOOKKEEPER - Flux Complet

```
┌─────────────────────────────────────────────────────────────┐
│ app/frontend/pages/dashboard/handlers.py                    │
│ ──────────────────────────────────────────────────────────── │
│ async def _get_ap_metrics(user_id, company_id):             │
│   from app.cache.unified_cache_manager import                │
│       get_unified_cache_manager                              │
│   cache = get_unified_cache_manager()                        │
│   cached = await cache.get_cached_data(                      │
│     user_id, company_id, "apbookeeper", "documents"         │
│   )                                                          │
│   # Calcule métriques depuis cached                          │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/pinnokio_agentic_workflow/tools/job_loader.py            │
│ ──────────────────────────────────────────────────────────── │
│ class JobLoader:                                             │
│   async def _fetch_apbookeeper_from_firebase(self):         │
│     from app.firebase_providers import FirebaseManagement   │
│     firebase_service = FirebaseManagement()                  │
│                                                              │
│     # 1. FETCH TO_DO                                         │
│     todo_docs = firebase_service.                            │
│       fetch_journal_entries_by_mandat_id(                   │
│         user_id=self.user_id,                                │
│         company_id=self.company_id,                          │
│         source='documents/accounting/invoices/doc_to_do',   │
│         departement='APbookeeper'                            │
│       )                                                      │
│                                                              │
│     # 2. VÉRIFICATION STATUTS                               │
│     for item in todo_docs:                                   │
│       notification = firebase_service.check_job_status(      │
│         user_id=self.user_id,                                │
│         job_id=item["job_id"]                                │
│       )                                                      │
│       # Organise par statut                                  │
│                                                              │
│     # 3. FETCH PENDING                                       │
│     pending_docs = firebase_service.                         │
│       fetch_pending_journal_entries_by_mandat_id(...)       │
│                                                              │
│     # 4. FETCH PROCESSED                                    │
│     booked_docs = firebase_service.                          │
│       fetch_journal_entries_by_mandat_id(                   │
│         source='documents/invoices/doc_booked'               │
│       )                                                      │
│                                                              │
│     return {                                                 │
│       "to_do": [...],                                        │
│       "in_process": [...],                                   │
│       "pending": [...],                                      │
│       "processed": [...]                                     │
│     }                                                        │
│                                                              │
│   async def load_apbookeeper_data(self):                     │
│     data = await self._fetch_apbookeeper_from_firebase()    │
│                                                              │
│     # SAUVEGARDE CACHE                                       │
│     from app.cache.unified_cache_manager import              │
│         get_unified_cache_manager                            │
│     cache = get_unified_cache_manager()                      │
│     await cache.set_cached_data(                             │
│       self.user_id, self.company_id,                        │
│       "apbookeeper", "documents",                            │
│       data, ttl_seconds=2400                                 │
│     )                                                        │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/firebase_providers.py                                   │
│ ──────────────────────────────────────────────────────────── │
│ class FirebaseManagement:                                   │
│   def fetch_journal_entries_by_mandat_id(...):              │
│     # Firebase Firestore                                     │
│     db = get_firestore()                                     │
│     journal_ref = db.collection("journal")                   │
│       .document(user_id)                                     │
│       .collection("entries")                                 │
│       .where("mandate_id", "==", company_id)                │
│       .where("source", "==", source)                        │
│                                                              │
│   def check_job_status(user_id, job_id):                     │
│     # Firebase Firestore Notifications                       │
│     notif_ref = db.collection("notifications")               │
│       .document(user_id)                                     │
│       .collection("jobs")                                     │
│       .document(job_id)                                       │
│                                                              │
│     return notif_ref.get().to_dict()                         │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ REDIS                                                        │
│ ──────────────────────────────────────────────────────────── │
│ business:{uid}:{cid}:invoices                                │
│ {                                                            │
│   "to_do": [...],                                            │
│   "in_process": [...],                                       │
│   "pending": [...],                                          │
│   "processed": [...]                                         │
│ }                                                            │
│ TTL: 2400s (40min)                                           │
└─────────────────────────────────────────────────────────────┘
```

---

### 7️⃣ EXPENSES - Flux Complet

```
┌─────────────────────────────────────────────────────────────┐
│ app/frontend/pages/dashboard/handlers.py                    │
│ ──────────────────────────────────────────────────────────── │
│ async def _get_expenses(user_id, company_id, mandate_path): │
│   from app.firebase_cache_handlers import                   │
│       get_firebase_cache_handlers                           │
│   handlers = get_firebase_cache_handlers()                  │
│   data = await handlers.get_expenses(user_id, company_id)   │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/firebase_cache_handlers.py                              │
│ ──────────────────────────────────────────────────────────── │
│ class FirebaseCacheHandlers:                                │
│   async def get_expenses(self, user_id, company_id):        │
│     # 1. TENTATIVE CACHE                                     │
│     from app.cache.unified_cache_manager import              │
│         get_unified_cache_manager                            │
│     cache = get_unified_cache_manager()                      │
│     cached = await cache.get_cached_data(                    │
│       user_id, company_id, "expenses", "details",           │
│       ttl_seconds=2400                                       │
│     )                                                        │
│                                                              │
│     if cached:                                               │
│       return {"data": cached["data"], "source": "cache"}    │
│                                                              │
│     # 2. FALLBACK FIREBASE                                   │
│     from app.firebase_providers import FirebaseManagement   │
│     firebase_mgmt = FirebaseManagement()                     │
│     expenses = firebase_mgmt.fetch_expenses_by_mandate(      │
│       mandate_path                                           │
│     )                                                        │
│                                                              │
│     # 3. SAUVEGARDE CACHE                                    │
│     await cache.set_cached_data(                             │
│       user_id, company_id, "expenses", "details",             │
│       expenses, ttl_seconds=2400                             │
│     )                                                        │
│                                                              │
│     return {"data": expenses, "source": "firebase"}         │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ app/firebase_providers.py                                   │
│ ──────────────────────────────────────────────────────────── │
│ class FirebaseManagement:                                   │
│   def fetch_expenses_by_mandate(                             │
│       self, mandate_path, status=None                       │
│   ):                                                        │
│     # Firebase Firestore                                     │
│     db = get_firestore()                                     │
│     expenses_ref = db.document(mandate_path)                 │
│       .collection("working_doc")                             │
│       .document("expenses_details")                         │
│       .collection("items")                                   │
│                                                              │
│     if status:                                               │
│       expenses_ref = expenses_ref.where("status", "==",     │
│                                         status)              │
│                                                              │
│     return [doc.to_dict() for doc in expenses_ref.stream()] │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ REDIS                                                        │
│ ──────────────────────────────────────────────────────────── │
│ business:{uid}:{cid}:expenses                                │
│ [                                                            │
│   { "id": "...", "amount": 100, "status": "open" },        │
│   { "id": "...", "amount": 200, "status": "closed" }        │
│ ]                                                            │
│ TTL: 2400s (40min)                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 Tableau Récapitulatif: Fichiers & Sources

| Domaine | Handler Principal | Cache Manager | Source Externe | Clé Redis | TTL |
|---------|-------------------|---------------|----------------|-----------|-----|
| **Banking** | `app/firebase_cache_handlers.py`<br/>`get_bank_transactions()` | `app/cache/unified_cache_manager.py` | `app/erp_service.py`<br/>`ERPService.get_odoo_bank_statement()` | `business:{uid}:{cid}:bank` | 40min |
| **Routing** | `app/drive_cache_handlers.py`<br/>`get_documents()` | `app/cache/unified_cache_manager.py` | `app/driveClientService.py`<br/>Google Drive API<br/>`app/firebase_providers.py`<br/>Firebase Journal | `business:{uid}:{cid}:routing` | 30min |
| **APBookkeeper** | `app/pinnokio_agentic_workflow/tools/job_loader.py`<br/>`_fetch_apbookeeper_from_firebase()` | `app/cache/unified_cache_manager.py` | `app/firebase_providers.py`<br/>Firebase Journal<br/>Firebase Notifications | `business:{uid}:{cid}:invoices` | 40min |
| **Expenses** | `app/firebase_cache_handlers.py`<br/>`get_expenses()` | `app/cache/unified_cache_manager.py` | `app/firebase_providers.py`<br/>`fetch_expenses_by_mandate()` | `business:{uid}:{cid}:expenses` | 40min |

---

## 🔑 Points Clés

1. **Tous les domaines passent par `UnifiedCacheManager`** qui gère la migration legacy → nouveau format
2. **Pattern uniforme:** Cache HIT → Retourne | Cache MISS → Source → Cache → Retourne
3. **Clés Redis unifiées:** `business:{uid}:{cid}:{domain}` pour tous les domaines
4. **Sources externes:**
   - Banking: ERP Odoo (via `erp_service.py`)
   - Routing: Google Drive API + Firebase Journal
   - APBookkeeper: Firebase Journal + Notifications
   - Expenses: Firebase Firestore
