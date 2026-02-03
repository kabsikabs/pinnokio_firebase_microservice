# 📋 Mapping Complet: Chargement Données Métier & Mise en Cache

## 1. 🔍 Comment le Frontend Indique la Page Active (SANS page_state)

### Solution Actuelle: `page.context_change`

Le frontend envoie un événement WebSocket **séparé** pour indiquer la page active:

**Frontend → Backend:**
```typescript
// Quand l'utilisateur navigue vers une page
wsClient.send({
  type: "page.context_change",
  payload: { page: "banking" }  // ou "routing", "dashboard", etc.
})
```

**Backend Handler** ([app/main.py](app/main.py:1912-1922)):
```python
elif msg_type == "page.context_change":
    from .realtime.contextual_publisher import update_page_context
    page = msg_payload.get("page")
    if page:
        update_page_context(uid, page)
        logger.info(f"[WS] Page context updated - uid={uid} page={page}")
```

**Stockage Redis** ([app/realtime/contextual_publisher.py](app/realtime/contextual_publisher.py:650-670)):
```python
def update_page_context(uid: str, page: str) -> None:
    """
    Met à jour le contexte de page de l'utilisateur.
    Stocke dans Redis: session:context:{uid}:page
    """
    redis = get_redis()
    page_key = f"session:context:{uid}:page"
    redis.setex(page_key, RedisTTL.SESSION, json.dumps(page))
    
    # Stocke aussi le domaine métier correspondant
    domain = page_to_domain(page)  # "banking" → "bank"
    if domain:
        domain_key = f"session:context:{uid}:domain"
        redis.setex(domain_key, RedisTTL.SESSION, json.dumps(domain))
```

**Clé Redis:**
- `session:context:{uid}:page` → Page active (ex: "banking", "routing")
- `session:context:{uid}:domain` → Domaine métier (ex: "bank", "routing")

**✅ Conclusion:** On peut supprimer `page_state` car la page active est déjà trackée via `page.context_change` !

---

## 2. 📊 Méthodes de Chargement des Données Métier

### 🏦 BANKING (Transactions Bancaires)

#### Méthode Principale
**Fichier:** [app/firebase_cache_handlers.py](app/firebase_cache_handlers.py:464-617)

```python
async def get_bank_transactions(
    self,
    user_id: str,
    company_id: str,
    client_uuid: str = None,
    bank_erp: str = None,
    mandate_path: str = None
) -> Dict[str, Any]:
    """
    RPC: FIREBASE_CACHE.get_bank_transactions
    
    Source: ERP (Odoo) - PAS Firebase
    """
```

#### Flux de Chargement

```
1. TENTATIVE CACHE
   ↓
   cache.get_cached_data(user_id, company_id, "bank", "transactions")
   ↓
   Si HIT → Retourne données cache
   ↓
   Si MISS → Continue
   
2. FALLBACK ERP (Odoo)
   ↓
   ERPService.get_odoo_bank_statement_move_line_not_rec()
   ↓
   Récupère transactions non réconciliées depuis Odoo
   
3. ORGANISATION PAR STATUT
   ↓
   _organize_bank_transactions_by_status()
   ↓
   {
     "to_reconcile": [...],
     "in_process": [...],
     "pending": [...],
     "matched": [...]
   }
   
4. SAUVEGARDE CACHE
   ↓
   cache.set_cached_data(
       user_id, company_id, "bank", "transactions",
       organized_data,
       ttl=TTL_BANK_TRANSACTIONS  # 2400s (40 min)
   )
```

#### Clé Redis
- **Legacy:** `cache:{uid}:{cid}:bank:transactions`
- **Nouveau:** `business:{uid}:{cid}:bank` (via UnifiedCacheManager)

#### TTL
- **40 minutes** (2400s)

#### Appelé Par
- [app/wrappers/dashboard_orchestration_handlers.py](app/wrappers/dashboard_orchestration_handlers.py:1784) - Dashboard orchestration
- [app/frontend/pages/banking/orchestration.py](app/frontend/pages/banking/orchestration.py:174) - Banking page
- [app/frontend/pages/dashboard/handlers.py](app/frontend/pages/dashboard/handlers.py:579) - Dashboard metrics

---

### 📁 ROUTING (Documents Google Drive)

#### Méthode Principale
**Fichier:** [app/drive_cache_handlers.py](app/drive_cache_handlers.py:69-166)

```python
async def get_documents(
    self,
    user_id: str,
    company_id: str,
    input_drive_id: str
) -> Dict[str, Any]:
    """
    RPC: DRIVE_CACHE.get_documents
    
    Source: Google Drive API
    """
```

#### Flux de Chargement

```
1. TENTATIVE CACHE
   ↓
   cache.get_cached_data(user_id, company_id, "drive", "documents")
   ↓
   Si HIT → Retourne données cache
   ↓
   Si MISS → Continue
   
2. FALLBACK GOOGLE DRIVE API
   ↓
   _fetch_from_drive(user_id, input_drive_id)
   ↓
   - Liste fichiers Drive
   - Vérifie statuts Firebase (via fetch_journal_entries_by_mandat_id)
   - Organise par statut
   
3. ORGANISATION PAR STATUT
   ↓
   {
     "to_process": [...],    # Documents non traités
     "in_process": [...],    # Documents en cours (Firebase status)
     "pending": [...],       # Documents en attente
     "processed": [...]      # Documents traités
   }
   
4. SAUVEGARDE CACHE
   ↓
   cache.set_cached_data(
       user_id, company_id, "drive", "documents",
       organized_data,
       ttl=TTL_DRIVE_DOCUMENTS  # 1800s (30 min)
   )
```

#### Clé Redis
- **Legacy:** `cache:{uid}:{cid}:drive:documents`
- **Nouveau:** `business:{uid}:{cid}:routing` (via UnifiedCacheManager)

#### TTL
- **30 minutes** (1800s)

#### Appelé Par
- [app/frontend/pages/routing/orchestration.py](app/frontend/pages/routing/orchestration.py:154) - Routing page
- [app/wrappers/dashboard_orchestration_handlers.py](app/wrappers/dashboard_orchestration_handlers.py:1758) - Dashboard orchestration
- [app/frontend/pages/dashboard/handlers.py](app/frontend/pages/dashboard/handlers.py:544) - Dashboard metrics

---

### 📋 APBOOKEEPER (Factures Fournisseur)

#### Méthode Principale
**Fichier:** [app/pinnokio_agentic_workflow/tools/job_loader.py](app/pinnokio_agentic_workflow/tools/job_loader.py:555-655)

```python
async def _fetch_apbookeeper_from_firebase(self) -> Dict:
    """
    Récupère les documents APBookkeeper depuis Firebase.
    Source: Firebase Journal (documents/accounting/invoices/doc_to_do)
    """
```

#### Flux de Chargement

```
1. FETCH FIREBASE JOURNAL
   ↓
   firebase_service.fetch_journal_entries_by_mandat_id(
       source='documents/accounting/invoices/doc_to_do',
       departement='APbookeeper'
   )
   ↓
   Récupère documents "to_do"
   
2. VERIFICATION STATUTS FIREBASE
   ↓
   Pour chaque document:
     - check_job_status(job_id)
     - Si status = "running"|"in queue"|"stopping" → in_process
     - Si status = "pending" → pending
     - Si status = "completed"|"success"|"close" → completed
   
3. FETCH PENDING
   ↓
   fetch_pending_journal_entries_by_mandat_id()
   ↓
   Documents en attente d'approbation
   
4. FETCH PROCESSED
   ↓
   fetch_journal_entries_by_mandat_id(
       source='documents/invoices/doc_booked'
   )
   ↓
   Documents déjà comptabilisés
   
5. ORGANISATION
   ↓
   {
     "to_do": [...],
     "in_process": [...],
     "pending": [...],
     "processed": [...]
   }
```

#### Clé Redis
- **Legacy:** `cache:{uid}:{cid}:apbookeeper:documents`
- **Nouveau:** `business:{uid}:{cid}:invoices` (via UnifiedCacheManager)

#### TTL
- **40 minutes** (2400s) - via UnifiedCacheManager

#### Appelé Par
- [app/pinnokio_agentic_workflow/tools/job_loader.py](app/pinnokio_agentic_workflow/tools/job_loader.py:216) - Job loader (LLM tools)
- [app/frontend/pages/dashboard/handlers.py](app/frontend/pages/dashboard/handlers.py:561) - Dashboard metrics

**Note:** APBookkeeper n'a PAS de handler dédié dans `firebase_cache_handlers.py`. Les données sont chargées via `job_loader` qui les met en cache via `UnifiedCacheManager`.

---

### 💰 EXPENSES (Notes de Frais)

#### Méthode Principale
**Fichier:** [app/firebase_cache_handlers.py](app/firebase_cache_handlers.py:183-256)

```python
async def get_expenses(
    self,
    user_id: str,
    company_id: str
) -> Dict[str, Any]:
    """
    RPC: FIREBASE_CACHE.get_expenses
    
    Source: Firebase (mandates/{company_id}/expenses)
    """
```

#### Flux de Chargement

```
1. TENTATIVE CACHE
   ↓
   cache.get_cached_data(user_id, company_id, "expenses", "details")
   ↓
   Si HIT → Retourne données cache
   ↓
   Si MISS → Continue
   
2. FALLBACK FIREBASE
   ↓
   db.collection("mandates")
     .document(company_id)
     .collection("expenses")
     .stream()
   ↓
   Récupère toutes les notes de frais
   
3. SAUVEGARDE CACHE
   ↓
   cache.set_cached_data(
       user_id, company_id, "expenses", "details",
       expenses_list,
       ttl=TTL_EXPENSES  # 2400s (40 min)
   )
```

#### Clé Redis
- **Legacy:** `cache:{uid}:{cid}:expenses:details`
- **Nouveau:** `business:{uid}:{cid}:expenses` (via UnifiedCacheManager)

#### TTL
- **40 minutes** (2400s)

#### Appelé Par
- [app/frontend/pages/dashboard/handlers.py](app/frontend/pages/dashboard/handlers.py:602) - Dashboard expenses widget
- [app/wrappers/dashboard_orchestration_handlers.py](app/wrappers/dashboard_orchestration_handlers.py:1712) - Dashboard orchestration

---

## 3. 📦 Architecture de Cache

### UnifiedCacheManager - Migration Legacy → Nouveau

**Fichier:** [app/cache/unified_cache_manager.py](app/cache/unified_cache_manager.py)

Le `UnifiedCacheManager` gère automatiquement la migration des clés legacy vers la nouvelle architecture:

```python
LEGACY_TO_BUSINESS_MAP = {
    # Niveau 3 - Business
    ("bank", "transactions"): (CacheLevel.BUSINESS, BusinessDomain.BANK.value),
    ("drive", "documents"): (CacheLevel.BUSINESS, BusinessDomain.ROUTING.value),
    ("apbookeeper", "documents"): (CacheLevel.BUSINESS, BusinessDomain.INVOICES.value),
    ("expenses", "details"): (CacheLevel.BUSINESS, BusinessDomain.EXPENSES.value),
}
```

**Clés Redis Finales:**
- `business:{uid}:{cid}:bank` - Transactions bancaires
- `business:{uid}:{cid}:routing` - Documents Drive
- `business:{uid}:{cid}:invoices` - Factures APBookkeeper
- `business:{uid}:{cid}:expenses` - Notes de frais

---

## 4. 🔄 Flux Complet: Dashboard → Page Spécifique

### Exemple: Banking

```
┌─────────────────────────────────────────────────────────────┐
│ DASHBOARD ORCHESTRATION                                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ 1. dashboard.orchestrate_init                               │
│    ↓                                                         │
│ 2. firebase_cache_handlers.get_bank_transactions()           │
│    ├─► Cache HIT → Retourne données                         │
│    └─► Cache MISS → ERP Odoo → Cache → Retourne             │
│    ↓                                                         │
│ 3. Calcule métriques depuis données                         │
│    metrics = {                                               │
│      to_process: count(status="to_reconcile"),              │
│      in_process: count(status="in_process"),                │
│      matched: count(status="matched")                       │
│    }                                                         │
│    ↓                                                         │
│ 4. Broadcast                                                 │
│    ├─► metrics.bank_update → bank-metrics-store             │
│    └─► dashboard.full_data → dashboard-store                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ NAVIGATION VERS /banking                                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ 1. Frontend: page.context_change { page: "banking" }        │
│    ↓                                                         │
│ 2. Backend: update_page_context(uid, "banking")              │
│    Stocke: session:context:{uid}:page = "banking"          │
│    ↓                                                         │
│ 3. Frontend: page.restore_state { page: "banking" }         │
│    ↓                                                         │
│ 4. Backend: Lit business:{uid}:{cid}:bank (MÊME cache!)    │
│    ↓                                                         │
│ 5. Si HIT: page.state_restored → banking-store.setData()   │
│    Si MISS: page.state_not_found                            │
│    ↓                                                         │
│ 6. Frontend: banking.orchestrate_init                        │
│    ↓                                                         │
│ 7. Backend: firebase_cache_handlers.get_bank_transactions() │
│    (Lit MÊME cache que dashboard!)                          │
│    ↓                                                         │
│ 8. banking.full_data → banking-store.setData()              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**✅ Point Clé:** Dashboard et Banking utilisent le **MÊME cache** (`business:{uid}:{cid}:bank`). Les métriques sont calculées depuis ce cache, pas stockées séparément.

---

## 5. 📝 Résumé: Méthodes de Chargement

| Domaine | Méthode | Fichier | Source | Cache Key | TTL |
|---------|---------|---------|--------|-----------|-----|
| **Banking** | `get_bank_transactions()` | `firebase_cache_handlers.py:464` | ERP (Odoo) | `business:{uid}:{cid}:bank` | 40min |
| **Routing** | `get_documents()` | `drive_cache_handlers.py:69` | Google Drive API | `business:{uid}:{cid}:routing` | 30min |
| **APBookkeeper** | `_fetch_apbookeeper_from_firebase()` | `job_loader.py:555` | Firebase Journal | `business:{uid}:{cid}:invoices` | 40min |
| **Expenses** | `get_expenses()` | `firebase_cache_handlers.py:183` | Firebase | `business:{uid}:{cid}:expenses` | 40min |

---

## 6. ✅ Réponse à ta Question

### Q1: Comment indiquer la page active sans page_state?

**Réponse:** Le frontend utilise déjà `page.context_change` qui stocke dans `session:context:{uid}:page`. On peut supprimer `page_state` car:
- La page active est trackée via `page.context_change`
- Les données de page sont dans `business cache` (même source que dashboard)
- `page.restore_state` peut lire directement `business cache`

### Q2: Où sont les méthodes de chargement et comment elles sont mises en cache?

**Réponse:** Voir tableau ci-dessus. Toutes les méthodes suivent le pattern:
1. **Tentative cache** (Redis)
2. **Fallback source** (ERP/Firebase/Drive)
3. **Organisation par statut**
4. **Sauvegarde cache** (Redis avec TTL)

Toutes utilisent `UnifiedCacheManager` qui migre automatiquement vers `business:{uid}:{cid}:{domain}`.
