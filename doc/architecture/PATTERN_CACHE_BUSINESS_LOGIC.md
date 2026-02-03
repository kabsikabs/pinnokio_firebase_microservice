# Pattern Cache-First avec Logique Métier Unifiée

## Vue d'Ensemble

Ce document décrit le pattern architectural utilisé pour gérer les données métier (Banking, Routing, APBookkeeper) dans l'application. Le pattern garantit la **cohérence des données** entre le dashboard et les pages détaillées en centralisant la logique métier et en utilisant un cache Redis partagé.

## 📋 Tableau Comparatif des 3 Domaines

| Aspect | Router (Routing) | APBookkeeper (Invoices) | Banking |
|--------|------------------|-------------------------|---------|
| **Source de données** | Google Drive API | Firebase Journal | ERP Odoo |
| **Cache Key** | `business:{uid}:{cid}:routing` | `business:{uid}:{cid}:apbookeeper` | `business:{uid}:{cid}:bank` |
| **Cache Handler** | `drive_cache_handlers.get_documents()` | `firebase_cache_handlers.get_ap_documents()` | `firebase_cache_handlers.get_bank_transactions()` |
| **Méthode de tri** | `_organize_documents_by_status_with_firebase()` | `check_job_status()` dans boucle | `_organize_bank_transactions_by_status()` |
| **function_name** | `Router` | `APbookeeper` | `Bankbookeeper` |
| **Identifiant** | `file_id` (Drive) | `job_id` (Firebase) | `move_id` (Odoo) |
| **Méthode Check** | `check_job_status(uid, None, file_id)` | `check_job_status(uid, job_id)` | `get_banker_batches()` + `download_pending_item_docsheet()` |
| **Collection notifications** | `clients/{uid}/notifications` | `clients/{uid}/notifications` | `clients/{uid}/notifications` |
| **Statuts in_process** | `running`, `in queue`, `stopping` | `running`, `in queue`, `stopping` | Identifié via batches actifs |
| **Statuts pending** | `pending` | `pending` | Identifié via pending_item_docsheet |
| **Catégories finales** | `to_process`, `in_process`, `pending` | `to_do`, `in_process`, `pending`, `processed` | `to_reconcile`, `in_process`, `pending` |
| **TTL Cache** | 2400s (40 min) | 2400s (40 min) | 2400s (40 min) |
| **Page orchestration** | `app/frontend/pages/routing/orchestration.py` | `app/frontend/pages/invoices/orchestration.py` | `app/frontend/pages/banking/orchestration.py` |
| **Page handlers** | `app/frontend/pages/routing/handlers.py` | `app/frontend/pages/invoices/handlers.py` | `app/frontend/pages/banking/handlers.py` |
| **Refresh avec invalidation** | ✅ Oui | ✅ Oui | ✅ Oui |

### Logique de Notifications Commune

Tous les domaines utilisent la collection Firebase `clients/{user_id}/notifications` pour déterminer les documents en cours de traitement :

```javascript
// Structure d'une notification
{
  "function_name": "Router" | "APbookeeper" | "Bankbookeeper",
  "status": "running" | "in queue" | "stopping" | "pending" | "completed" | "error",
  "job_id": "...",           // Pour APBookkeeper et Router
  "file_id": "...",          // Pour Router (documents Drive)
  "batch_id": "...",         // Pour Banking (batches)
  "transactions": [...],     // Pour Banking (IDs des transactions)
  "collection_id": "company_id",
  "timestamp": "2024-01-20T12:00:00Z"
}
```

### Différences Clés

1. **Router** : Vérifie chaque fichier Drive individuellement via `check_job_status(file_id)`
2. **APBookkeeper** : Vérifie chaque document Firebase individuellement via `check_job_status(job_id)`
3. **Banking** : Récupère tous les batches actifs en une seule requête via `get_banker_batches()`, puis filtre les transactions

## 🎯 Principes Fondamentaux

### 1. Single Source of Truth (Cache Business)

```
business:{uid}:{cid}:{domain}
```

- **Une seule clé Redis** pour chaque domaine métier (`bank`, `routing`, `invoices`, `expenses`)
- **Une seule logique métier** pour transformer les données brutes en données exploitables
- **Une seule source de cache** utilisée par le dashboard ET les pages détaillées

### 2. Logique Métier Centralisée

Toute la transformation des données brutes (ERP, Firebase, Google Drive) en données métier structurées est effectuée dans les cache handlers :

**Routing (Drive)**
- Handler: `app/drive_cache_handlers.py`
- Méthode: `get_documents()` → `_organize_documents_by_status_with_firebase()`
- Source: Google Drive API + Firebase notifications

**APBookkeeper (Invoices)**
- Handler: `app/firebase_cache_handlers.py`
- Méthode: `get_ap_documents()`
- Source: Firebase journal (`clients/{uid}/klk_vision/APbookeeper/journal`)

**Banking**
- Handler: `app/firebase_cache_handlers.py`
- Méthode: `get_bank_transactions()` → `_organize_bank_transactions_by_status()`
- Source: ERP Odoo + Firebase notifications + Firebase pending_item_docsheet

### 3. Métriques Dérivées des Données Métier

Les métriques du dashboard sont **calculées depuis la logique métier**, garantissant que :
- Les compteurs reflètent toujours l'état réel des données
- Dashboard et pages détaillées montrent les mêmes chiffres
- Pas de désynchronisation possible

## 📊 Architecture du Pattern (Banking)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DASHBOARD ORCHESTRATION                      │
│                    (dashboard_orchestration_handlers.py)             │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                 Phase 2: _populate_widget_caches()
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│              FIREBASE CACHE HANDLERS (Logique Métier)               │
│                   (firebase_cache_handlers.py)                       │
│                                                                       │
│  get_bank_transactions(uid, cid, client_uuid, bank_erp, mandate)   │
│  ┌────────────────────────────────────────────────────────┐        │
│  │ 1. CHECK CACHE: business:{uid}:{cid}:bank              │        │
│  │    └─→ CACHE HIT: Return cached data ✅                │        │
│  │                                                          │        │
│  │ 2. CACHE MISS: Fetch from SOURCE                       │        │
│  │    ├─→ ERPService.get_odoo_bank_statement_...()        │        │
│  │    │   (Récupère transactions brutes Odoo)             │        │
│  │    │                                                    │        │
│  │    └─→ _organize_bank_transactions_by_status()         │        │
│  │        ├─→ get_banker_batches() // Transactions en cours│        │
│  │        └─→ download_pending_item_docsheet() // Pending  │        │
│  │                                                          │        │
│  │ 3. APPLY BUSINESS LOGIC                                │        │
│  │    ├─→ Filtrage des transactions in_process            │        │
│  │    ├─→ Filtrage des transactions pending               │        │
│  │    └─→ Reste = to_reconcile                            │        │
│  │                                                          │        │
│  │ 4. CACHE RESULT: Redis SET with TTL 2400s             │        │
│  │    business:{uid}:{cid}:bank = {                       │        │
│  │      "to_reconcile": [...],                            │        │
│  │      "in_process": [...],                              │        │
│  │      "pending": [...]                                  │        │
│  │    }                                                    │        │
│  │                                                          │        │
│  │ 5. RETURN structured data                              │        │
│  └────────────────────────────────────────────────────────┘        │
└─────────────┬───────────────────────────────────────────────────────┘
              │
              ├──────────────────────────┬─────────────────────────────┐
              ▼                          ▼                             ▼
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│   DASHBOARD PAGE     │   │   BANKING PAGE       │   │   ANY OTHER PAGE     │
│  (dashboard/handlers)│   │(banking/orchestration)│   │   (future use)       │
└──────────────────────┘   └──────────────────────┘   └──────────────────────┘
         │                          │                          │
         │ _get_metrics()          │ handle_banking_          │ (peut utiliser
         │                         │ orchestrate_init()       │  le même cache)
         ▼                          ▼                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    REDIS CACHE (Shared)                              │
│                  business:{uid}:{cid}:bank                           │
│                                                                       │
│  {                                                                    │
│    "to_reconcile": [                                                 │
│      {"move_id": 123, "amount": 1000, ...},                         │
│      {"move_id": 124, "amount": 500, ...}                           │
│    ],                                                                 │
│    "in_process": [                                                   │
│      {"move_id": 125, "batch_id": "xyz", ...}                       │
│    ],                                                                 │
│    "pending": [                                                      │
│      {"move_id": 126, "reason": "approval", ...}                    │
│    ]                                                                  │
│  }                                                                    │
│                                                                       │
│  TTL: 2400 secondes (40 minutes)                                    │
└─────────────────────────────────────────────────────────────────────┘
         │                          │
         │ Calculate metrics        │ Display details
         ▼                          ▼
┌──────────────────────┐   ┌──────────────────────┐
│ DASHBOARD METRICS    │   │ BANKING TRANSACTIONS │
│                      │   │                      │
│ bank: {              │   │ to_reconcile: [...]  │
│   toProcess: 2       │   │ in_process: [...]    │
│   inProcess: 1       │   │ pending: [...]       │
│   pending: 1         │   │                      │
│ }                    │   │ (Full details)       │
└──────────────────────┘   └──────────────────────┘
```

## 🔄 Flux Détaillé

### Phase 1 : Dashboard - Chargement Initial

**Fichier**: `app/wrappers/dashboard_orchestration_handlers.py`

```python
async def orchestrate_init():
    # Phase 1: Load company data
    company_data = await _load_company_data(uid, company_id)
    
    # Phase 2: Populate Business Caches
    await _populate_widget_caches(uid, company_id, context)
        │
        ├─→ await firebase_handlers.get_bank_transactions(
        │       uid, company_id, client_uuid, bank_erp, mandate_path
        │   )
        │   ↓
        │   Cache: business:{uid}:{cid}:bank = {
        │       "to_reconcile": [...],
        │       "in_process": [...],
        │       "pending": [...]
        │   }
        │
        ├─→ await drive_handlers.get_documents(...)
        │   ↓ Cache: business:{uid}:{cid}:routing
        │
        └─→ await firebase_handlers.get_invoices(...)
            ↓ Cache: business:{uid}:{cid}:invoices
    
    # Phase 3: Calculate metrics from caches
    metrics = await _get_metrics(uid, company_id)
        │
        └─→ Read business:{uid}:{cid}:bank
            ├─→ metrics["bank"]["toProcess"] = len(data["to_reconcile"])
            ├─→ metrics["bank"]["inProcess"] = len(data["in_process"])
            └─→ metrics["bank"]["pending"] = len(data["pending"])
    
    # Phase 4: Send to frontend
    await hub.broadcast(uid, {
        "type": "dashboard.full_data",
        "payload": {
            "metrics": metrics,
            "company": company_data
        }
    })
```

**Points clés** :
1. ✅ La logique métier est appliquée **une seule fois** lors du chargement initial
2. ✅ Le résultat est **mis en cache** pour 40 minutes
3. ✅ Les métriques sont **calculées depuis le cache**, pas recalculées

### Phase 2 : Banking Page - Accès au Cache

**Fichier**: `app/frontend/pages/banking/orchestration.py`

```python
async def handle_banking_orchestrate_init():
    # STEP 2: Fetch Bank Transactions (uses cache handler)
    from app.firebase_cache_handlers import get_firebase_cache_handlers
    cache_handlers = get_firebase_cache_handlers()
    
    result = await cache_handlers.get_bank_transactions(
        user_id=uid,
        company_id=company_id,
        client_uuid=client_uuid,
        bank_erp=bank_erp,
        mandate_path=mandate_path
    )
    
    # ┌─────────────────────────────────────────────┐
    # │ CACHE HIT:                                  │
    # │   → Retourne immédiatement depuis Redis     │
    # │   → source="cache"                          │
    # │   → Latency: ~2-5ms                         │
    # └─────────────────────────────────────────────┘
    # 
    # ┌─────────────────────────────────────────────┐
    # │ CACHE MISS:                                 │
    # │   → Appelle ERP Odoo                        │
    # │   → Applique logique métier                 │
    # │   → Met en cache                            │
    # │   → source="erp"                            │
    # │   → Latency: ~200-500ms                     │
    # └─────────────────────────────────────────────┘
    
    if result.get("data"):
        data = result["data"]
        to_process = data.get("to_reconcile", [])
        in_process = data.get("in_process", [])
        pending = data.get("pending", [])
        
        # Display full transaction details
        await hub.broadcast(uid, {
            "type": "banking.full_data",
            "payload": {
                "transactions": {
                    "to_process": to_process,
                    "in_process": in_process,
                    "pending": pending
                },
                "counts": {
                    "to_process": len(to_process),
                    "in_process": len(in_process),
                    "pending": len(pending)
                }
            }
        })
```

**Points clés** :
1. ✅ **Même méthode** que le dashboard (`get_bank_transactions()`)
2. ✅ **Même cache** (`business:{uid}:{cid}:bank`)
3. ✅ **Cache HIT** = données instantanées (déjà chargées par dashboard)
4. ✅ **Cache MISS** = logique métier appliquée automatiquement

### Phase 3 : Refresh - Mise à Jour depuis Source

**Déclenchement** : Depuis Dashboard ou Banking Page

```python
async def handle_banking_refresh():
    # 1. Invalidate cache
    cache = get_firebase_cache_manager()
    await cache.delete_cached_data(uid, company_id, "bank", "transactions")
    
    # 2. Re-fetch from source (automatically applies business logic)
    result = await cache_handlers.get_bank_transactions(
        user_id=uid,
        company_id=company_id,
        client_uuid=client_uuid,
        bank_erp=bank_erp,
        mandate_path=mandate_path
    )
    # → CACHE MISS détecté
    # → Appelle ERP Odoo
    # → Applique _organize_bank_transactions_by_status()
    # → Met en cache le résultat
    # → Retourne les données fraîches
    
    # 3. Broadcast updated data
    await hub.broadcast(uid, {
        "type": "banking.refreshed",
        "payload": result["data"]
    })
```

**Points clés** :
1. ✅ Invalidation propre du cache
2. ✅ Re-application automatique de la logique métier
3. ✅ Nouveau cache créé avec TTL complet (40 min)
4. ✅ Cohérence garantie entre dashboard et page détaillée

## 🧩 Logique Métier : Organisation des Transactions Bancaires

**Fichier** : `app/firebase_cache_handlers.py`

```python
async def _organize_bank_transactions_by_status(
    self, 
    transactions: List[Dict],
    user_id: str,
    company_id: str,
    mandate_path: str = None
) -> Dict[str, List]:
    """
    Applique la logique métier pour organiser les transactions.
    
    ÉTAPES:
    -------
    1. Initialiser toutes les transactions dans to_reconcile
    2. Identifier les transactions "in_process" via get_banker_batches()
    3. Identifier les transactions "pending" via download_pending_item_docsheet()
    4. Filtrer et organiser en 3 catégories finales
    
    SOURCES DE VÉRITÉ:
    ------------------
    - Batches actifs: clients/{user_id}/notifications (Firebase)
    - Pending items: {mandate_path}/working_doc/pending_item_docsheet (Firebase)
    
    RÉSULTAT:
    ---------
    {
      "to_reconcile": [...],  // À traiter
      "in_process": [...],    // En cours (batches)
      "pending": [...]        // En attente (validation)
    }
    """
    
    # 1. Initialisation : tout est "to_reconcile" par défaut
    organized = {
        "to_reconcile": [],
        "in_process": [],
        "pending": []
    }
    
    tx_by_id = {}
    for tx in transactions:
        move_id = tx.get("move_id")
        if move_id is not None:
            tx_by_id[str(move_id)] = tx
            organized["to_reconcile"].append(tx)
    
    # 2. Récupération parallèle des données de statut
    async def fetch_batches():
        """Récupère les IDs des transactions en cours de traitement."""
        try:
            from .firebase_providers import FirebaseManagement
            firebase = FirebaseManagement()
            notifications_path = f"clients/{user_id}/notifications"
            
            batches = await asyncio.to_thread(
                firebase.get_banker_batches,
                user_id,
                notifications_path,
                company_id
            )
            
            batch_tx_ids = set()
            if batches and isinstance(batches, list):
                for batch in batches:
                    batch_transactions = batch.get('transactions', [])
                    if isinstance(batch_transactions, list):
                        for tx_item in batch_transactions:
                            if isinstance(tx_item, dict):
                                tx_id = (tx_item.get('move_id') or 
                                        tx_item.get('transaction_id') or 
                                        tx_item.get('Id'))
                                if tx_id:
                                    batch_tx_ids.add(str(tx_id))
            
            logger.info(f"[BANK_ORGANIZE] Found {len(batch_tx_ids)} in batches")
            return batch_tx_ids
            
        except Exception as e:
            logger.warning(f"[BANK_ORGANIZE] Failed to fetch batches: {e}")
            return set()
    
    async def fetch_pending():
        """Récupère les IDs des transactions en attente."""
        try:
            if not mandate_path:
                return set()
                
            from .firebase_providers import FirebaseManagement
            firebase = FirebaseManagement()
            
            pending_doc = await asyncio.to_thread(
                firebase.download_pending_item_docsheet,
                mandate_path
            )
            
            pending_tx_ids = set()
            if pending_doc and isinstance(pending_doc, dict):
                items = pending_doc.get('items', {})
                if isinstance(items, dict):
                    for key, item in items.items():
                        if isinstance(item, dict):
                            tx_id = (item.get('Id') or 
                                    item.get('move_id') or 
                                    item.get('transaction_id'))
                            if tx_id:
                                pending_tx_ids.add(str(tx_id))
            
            logger.info(f"[BANK_ORGANIZE] Found {len(pending_tx_ids)} pending")
            return pending_tx_ids
            
        except Exception as e:
            logger.warning(f"[BANK_ORGANIZE] Failed to fetch pending: {e}")
            return set()
    
    # 3. Exécution parallèle (Performance ++)
    batch_ids, pending_ids = await asyncio.gather(
        fetch_batches(),
        fetch_pending(),
        return_exceptions=False
    )
    
    # 4. Filtrage et organisation finale
    to_remove_from_reconcile = []
    
    for tx in organized["to_reconcile"]:
        move_id = str(tx.get("move_id", ""))
        
        if move_id in batch_ids:
            organized["in_process"].append(tx)
            to_remove_from_reconcile.append(tx)
        elif move_id in pending_ids:
            organized["pending"].append(tx)
            to_remove_from_reconcile.append(tx)
    
    # Retrait des transactions déplacées
    for tx in to_remove_from_reconcile:
        organized["to_reconcile"].remove(tx)
    
    logger.info(
        f"[BANK_ORGANIZE] Final counts - "
        f"to_reconcile={len(organized['to_reconcile'])}, "
        f"in_process={len(organized['in_process'])}, "
        f"pending={len(organized['pending'])}"
    )
    
    return organized
```

### Gestion des Erreurs

```python
# Si get_banker_batches() échoue:
#   → batch_ids = set() (vide)
#   → Aucune transaction marquée "in_process"
#   → Toutes restent dans "to_reconcile"
#   → L'application continue normalement

# Si download_pending_item_docsheet() échoue:
#   → pending_ids = set() (vide)
#   → Aucune transaction marquée "pending"
#   → L'application continue normalement

# Principe: Graceful degradation
```

## 📈 Calcul des Métriques

**Fichier** : `app/frontend/pages/dashboard/handlers.py`

```python
async def _get_metrics(user_id: str, company_id: str) -> Dict:
    """
    Calcule les métriques depuis les caches business.
    
    IMPORTANT: Les métriques sont DÉRIVÉES des données métier,
               pas calculées indépendamment.
    """
    metrics = {
        "router": {"toProcess": 0, "inProcess": 0, "processed": 0},
        "ap": {"toProcess": 0, "inProcess": 0, "pending": 0, "processed": 0},
        "bank": {"toProcess": 0, "inProcess": 0, "pending": 0},
        "expenses": {"open": 0, "closed": 0, "pendingApproval": 0}
    }
    
    cache = get_firebase_cache_manager()
    
    # ═══════════════════════════════════════════════════════════════
    # BANK METRICS - Read from business cache
    # ═══════════════════════════════════════════════════════════════
    try:
        bank_data = await cache.get_cached_data(
            user_id, company_id, "bank", "transactions",
            ttl_seconds=300
        )
        if bank_data and "data" in bank_data:
            data = bank_data["data"]
            
            # Métriques = len() des listes dans le cache
            metrics["bank"]["toProcess"] = len(data.get("to_reconcile", []))
            metrics["bank"]["inProcess"] = len(data.get("in_process", []))
            metrics["bank"]["pending"] = len(data.get("pending", []))
            
            # ✅ Garantie de cohérence:
            #    Si banking page affiche 5 transactions "to_reconcile",
            #    dashboard affichera "toProcess: 5"
            
            logger.info(
                f"_get_metrics: Bank from cache - "
                f"toProcess={metrics['bank']['toProcess']}"
            )
    except Exception as e:
        logger.warning(f"_get_metrics: Bank cache error: {e}")
    
    # ... autres domaines (routing, ap, expenses) ...
    
    # ═══════════════════════════════════════════════════════════════
    # SUMMARY - Agrégation
    # ═══════════════════════════════════════════════════════════════
    total_to_process = (
        metrics["router"]["toProcess"] +
        metrics["ap"]["toProcess"] +
        metrics["bank"]["toProcess"]
    )
    total_in_progress = (
        metrics["router"]["inProcess"] +
        metrics["ap"]["inProcess"] +
        metrics["bank"]["inProcess"]
    )
    total_completed = (
        metrics["router"]["processed"] +
        metrics["ap"]["processed"]
        # Bank n'a pas de statut "completed"
    )
    
    metrics["summary"] = {
        "totalDocumentsToProcess": total_to_process,
        "totalInProgress": total_in_progress,
        "totalCompleted": total_completed,
        "completionRate": round(
            (total_completed / (total_to_process + total_in_progress + total_completed)) * 100, 
            1
        ) if (total_to_process + total_in_progress + total_completed) > 0 else 0
    }
    
    return metrics
```

## 🎯 Garanties de Cohérence

### 1. Cohérence Temporelle

```
Timeline:
─────────────────────────────────────────────────────────────────
T0: Dashboard loads
    └─→ business:{uid}:{cid}:bank cached (TTL: 40min)
        Data: {to_reconcile: [A, B], in_process: [C], pending: [D]}

T0+1s: User clicks "Banking" tab
    └─→ CACHE HIT
        Same data: {to_reconcile: [A, B], in_process: [C], pending: [D]}

T0+30min: User refreshes dashboard
    └─→ CACHE HIT (still valid)
        Same data: {to_reconcile: [A, B], in_process: [C], pending: [D]}

T0+45min: Cache expires
    └─→ Next access = CACHE MISS
        Re-fetch from ERP + apply business logic
        New cache with fresh data
```

### 2. Cohérence Structurelle

```python
# Dashboard et Banking page utilisent la MÊME structure:

# Dashboard _get_metrics():
metrics["bank"]["toProcess"] = len(data.get("to_reconcile", []))
                                    ^^^^^^^^^^^^^^^^^^^^^
# Banking orchestration:
to_process = data.get("to_reconcile", [])
                 ^^^^^^^^^^^^^^^^^^^^^

# ✅ Même clé = Même compteur
```

### 3. Cohérence des IDs

```python
# Exemple de transaction:
{
  "move_id": 12345,  # ← Clé d'identification unique
  "amount": 1000.00,
  "partner_name": "Client ABC",
  ...
}

# Utilisée pour:
# 1. Identifier les batches actifs (get_banker_batches)
# 2. Identifier les pending items (download_pending_item_docsheet)
# 3. Afficher dans la UI (banking page)
# 4. Compter dans les métriques (dashboard)
```

## 🔍 Cas d'Usage et Scénarios

### Scénario 1 : Première Visite

```
1. User logs in
2. Dashboard loads → orchestrate_init()
   ├─→ get_bank_transactions() called
   ├─→ CACHE MISS
   ├─→ Fetch from ERP Odoo (200-500ms)
   ├─→ Apply business logic
   ├─→ Cache result (40min TTL)
   └─→ Display metrics
3. User clicks "Banking" tab
   ├─→ get_bank_transactions() called again
   ├─→ CACHE HIT (2-5ms)
   └─→ Display transaction list

✅ Bénéfice: Banking page s'affiche instantanément
```

### Scénario 2 : Refresh après Traitement

```
1. User processes transactions on Banking page
2. User clicks "Refresh"
   ├─→ Invalidate cache: business:{uid}:{cid}:bank
   ├─→ get_bank_transactions() called
   ├─→ CACHE MISS (cache invalidé)
   ├─→ Fetch fresh data from ERP
   ├─→ Apply business logic (batches, pending)
   ├─→ Cache new result
   └─→ Display updated list
3. User returns to Dashboard
   ├─→ _get_metrics() called
   ├─→ CACHE HIT (fresh data cached)
   └─→ Display updated metrics

✅ Bénéfice: Données synchronisées automatiquement
```

### Scénario 3 : Concurrent Access

```
Timeline:
─────────────────────────────────────────────────────────────
T0: User A loads dashboard
    └─→ Cache populated: business:{uid}:{cid}:bank

T0+5s: User B (same company) loads dashboard
       └─→ CACHE HIT (shared cache)

T0+10s: User A opens Banking page
        └─→ CACHE HIT (same cache)

T0+15s: User B opens Banking page
        └─→ CACHE HIT (same cache)

✅ Bénéfice: 1 appel ERP pour N utilisateurs
```

## 🚀 Performance et Optimisations

### Optimisation 1 : Parallel Fetching

```python
# Dans _organize_bank_transactions_by_status():
batch_ids, pending_ids = await asyncio.gather(
    fetch_batches(),      # Firebase query
    fetch_pending(),      # Firebase query
    return_exceptions=False
)

# Au lieu de:
# batch_ids = await fetch_batches()     # 150ms
# pending_ids = await fetch_pending()   # 150ms
# Total: 300ms

# Avec asyncio.gather():
# Total: ~150ms (parallel execution)
```

### Optimisation 2 : Cache TTL Strategy

```python
# Cache business (données métier):
TTL_BANK_TRANSACTIONS = 2400  # 40 minutes

# Cache page state (UI state):
TTL_PAGE_STATE = 1800  # 30 minutes

# Rationale:
# - Business data changes less frequently
# - Longer TTL = fewer ERP calls
# - 40min = bon équilibre refresh/performance
```

### Optimisation 3 : Graceful Degradation

```python
# Si get_banker_batches() échoue:
try:
    batches = await fetch_batches()
except Exception as e:
    logger.warning(f"Batches fetch failed: {e}")
    batches = set()  # Empty set, not crash

# Application continue avec données partielles
# Mieux que: crash total
```

## 📋 Checklist d'Implémentation

Pour implémenter ce pattern sur un nouveau domaine :

### ✅ Étape 1 : Cache Handler

```python
# app/firebase_cache_handlers.py

async def get_<domain>_data(
    self,
    user_id: str,
    company_id: str,
    **kwargs
) -> Dict[str, Any]:
    """
    1. Check cache: business:{uid}:{cid}:<domain>
    2. If miss: fetch from source
    3. Apply business logic via _organize_<domain>_by_status()
    4. Cache result
    5. Return structured data
    """
    pass

async def _organize_<domain>_by_status(
    self,
    items: List[Dict],
    user_id: str,
    company_id: str,
    **context
) -> Dict[str, List]:
    """
    Apply domain-specific business logic.
    
    Returns:
        {
            "category1": [...],
            "category2": [...],
            "category3": [...]
        }
    """
    pass
```

### ✅ Étape 2 : Dashboard Integration

```python
# app/wrappers/dashboard_orchestration_handlers.py

async def _populate_widget_caches(...):
    # Add call to new domain handler
    <domain>_data = await firebase_handlers.get_<domain>_data(
        uid, company_id, **context
    )
    # Data is now cached

# app/frontend/pages/dashboard/handlers.py

async def _get_metrics(...):
    # Add metrics calculation from cache
    <domain>_data = await cache.get_cached_data(
        user_id, company_id, "<domain>", "items"
    )
    if <domain>_data and "data" in <domain>_data:
        data = <domain>_data["data"]
        metrics["<domain>"]["status1"] = len(data.get("category1", []))
        metrics["<domain>"]["status2"] = len(data.get("category2", []))
```

### ✅ Étape 3 : Page Integration

```python
# app/frontend/pages/<domain>/orchestration.py

async def handle_<domain>_orchestrate_init(...):
    # Use same cache handler as dashboard
    from app.firebase_cache_handlers import get_firebase_cache_handlers
    cache_handlers = get_firebase_cache_handlers()
    
    result = await cache_handlers.get_<domain>_data(
        user_id=uid,
        company_id=company_id,
        **context
    )
    
    # Display full details
    if result.get("data"):
        data = result["data"]
        # Use categories from business logic
        category1_items = data.get("category1", [])
        category2_items = data.get("category2", [])
```

### ✅ Étape 4 : Refresh Implementation

```python
async def handle_<domain>_refresh(...):
    # 1. Invalidate cache
    cache = get_firebase_cache_manager()
    await cache.delete_cached_data(uid, company_id, "<domain>", "items")
    
    # 2. Re-fetch (will apply business logic automatically)
    result = await cache_handlers.get_<domain>_data(...)
    
    # 3. Broadcast updated data
    await hub.broadcast(uid, {
        "type": "<domain>.refreshed",
        "payload": result["data"]
    })
```

## 🎓 Principes et Best Practices

### 1. DRY (Don't Repeat Yourself)

❌ **Mauvais** :
```python
# Dashboard
bank_data = fetch_from_erp()
organized = organize_by_status_dashboard(bank_data)

# Banking page
bank_data = fetch_from_erp()  # Duplicate call
organized = organize_by_status_banking(bank_data)  # Duplicate logic
```

✅ **Bon** :
```python
# Dashboard ET Banking page
result = await cache_handlers.get_bank_transactions(...)
# Logique centralisée + cache partagé
```

### 2. Single Source of Truth

❌ **Mauvais** :
```python
# Dashboard calcule les métriques depuis Firebase
# Banking page calcule depuis ERP
# → Désynchronisation possible
```

✅ **Bon** :
```python
# Dashboard ET Banking lisent depuis business:{uid}:{cid}:bank
# → Garantie de cohérence
```

### 3. Cache-First Strategy

✅ **Ordre de priorité** :
```
1. Redis Cache (business:{uid}:{cid}:{domain})
2. Source externe (ERP, Firebase, Google Drive)
3. Apply business logic
4. Cache result
```

### 4. Fail Gracefully

✅ **Gestion d'erreurs** :
```python
try:
    special_logic_data = await fetch_special_data()
except Exception as e:
    logger.warning(f"Special data unavailable: {e}")
    special_logic_data = {}  # Continue with empty data

# Application continue normalement
```

## 📊 Métriques et Monitoring

### Logs à Surveiller

```python
# Cache HIT
logger.info(
    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
    f"count={total} source=cache"
)

# Cache MISS
logger.info(
    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
    f"count={len(bank_transactions)} source=erp"
)

# Business logic applied
logger.info(
    f"[BANK_ORGANIZE] Final counts - "
    f"to_reconcile={len(organized['to_reconcile'])}, "
    f"in_process={len(organized['in_process'])}, "
    f"pending={len(organized['pending'])}"
)
```

### Métriques Importantes

```
1. Cache Hit Rate
   = (cache_hits / total_requests) * 100
   Objectif: > 80%

2. Business Logic Duration
   = time(_organize_bank_transactions_by_status)
   Objectif: < 500ms

3. Source Fetch Duration
   = time(fetch_from_erp/firebase/drive)
   Objectif: < 1000ms

4. End-to-End Latency
   Dashboard: < 2s (cache miss), < 100ms (cache hit)
   Pages: < 1s (cache miss), < 50ms (cache hit)
```

## 🔐 Sécurité et Isolation

### Isolation par User/Company

```
Cache Key Pattern: business:{uid}:{cid}:{domain}
                          ───   ───
                           │     └─→ Company ID (isolation tenant)
                           └────────→ User ID (isolation utilisateur)

✅ User A (company X) ne peut pas voir les données de User B (company Y)
✅ Chaque company a son propre cache isolé
```

### TTL et Expiration

```python
# Données bancaires: 40 minutes
TTL_BANK_TRANSACTIONS = 2400

# Rationale:
# - Données ERP changent peu (nouvelles transactions ~1x/jour)
# - TTL 40min = bon compromis fraîcheur/performance
# - Refresh manuel disponible si besoin immédiat
```

## 📚 Ressources et Références

### Fichiers Clés

```
app/
├── firebase_cache_handlers.py          # Logique métier centralisée
├── cache/
│   └── unified_cache_manager.py        # Gestion cache Redis
├── wrappers/
│   └── dashboard_orchestration_handlers.py  # Chargement initial
└── frontend/
    ├── pages/dashboard/handlers.py     # Calcul métriques
    └── pages/banking/orchestration.py  # Accès données détaillées
```

### Documentation Connexe

- `SCHEMA_FLUX_CACHE_COMPLET.md` - Vue d'ensemble des flux
- `MAPPING_CHARGEMENT_DONNEES.md` - Mapping des sources de données
- `docs/architecture/CACHE_STRATEGY.md` - Stratégie de cache détaillée

---

**Version**: 2.0  
**Date**: 2026-01-24  
**Auteur**: Architecture Team  
**Status**: ✅ Implémenté et validé

## 📚 Annexe: Exemples de Code par Domaine

### A. Router - Logique de Tri

```python
# app/drive_cache_handlers.py - _organize_documents_by_status_with_firebase()

async def _organize_documents_by_status_with_firebase(
    self,
    user_id: str,
    drive_files: List[Dict]
) -> Dict[str, List]:
    organized = {
        "to_process": [],
        "in_process": [],
        "pending": []
    }
    
    firebase_mgmt = get_firebase_management()
    
    for doc in drive_files:
        file_id = doc.get('id', '')
        
        # Vérifier le status dans Firebase notifications
        notification = await asyncio.to_thread(
            firebase_mgmt.check_job_status,
            user_id,
            None,      # job_id (None pour Router)
            file_id    # file_id (identifiant Drive)
        )
        
        # Si notification existe et correspond à la fonction Router
        if notification and notification.get('function_name') == 'Router':
            firebase_status = notification.get('status', '')
            
            # Catégoriser selon le statut
            if firebase_status in ['running', 'in queue', 'stopping']:
                doc_item['status'] = firebase_status
                organized["in_process"].append(doc_item)
            elif firebase_status == 'pending':
                doc_item['status'] = 'pending'
                organized["pending"].append(doc_item)
            else:
                # Autres statuts → to_process
                organized["to_process"].append(doc_item)
        else:
            # Pas de notification Router → À traiter
            organized["to_process"].append(doc_item)
    
    return organized
```

### B. APBookkeeper - Logique de Tri

```python
# app/firebase_cache_handlers.py - get_ap_documents()

# Fetch TO_DO documents from Firebase journal
todo_docs = await asyncio.to_thread(
    firebase_mgmt.fetch_journal_entries_by_mandat_id,
    user_id,
    company_id,
    'documents/accounting/invoices/doc_to_do',
    'APbookeeper'
)

# Process TO_DO documents
items_to_do = []
items_in_process = []
items_pending = []

for doc in todo_docs:
    doc_data = doc.get('data', {})
    job_id = doc_data.get('job_id', '')
    
    if job_id:
        # Vérifier dans notifications
        notification = firebase_mgmt.check_job_status(user_id, job_id)
        
        if notification and notification.get('function_name') == 'APbookeeper':
            status = notification.get('status', '')
            
            if status in ['running', 'in queue', 'stopping']:
                doc_data['status'] = status
                items_in_process.append(doc_data)
                continue
            elif status == 'pending':
                doc_data['status'] = 'pending'
                items_pending.append(doc_data)
                continue
    
    # Pas de notification ou autre statut → to_do
    items_to_do.append(doc_data)

organized = {
    "to_do": items_to_do,
    "in_process": items_in_process,
    "pending": items_pending,
    "processed": items_processed
}
```

### C. Banking - Logique de Tri

```python
# app/firebase_cache_handlers.py - _organize_bank_transactions_by_status()

async def _organize_bank_transactions_by_status(
    self, 
    transactions: List[Dict],
    user_id: str,
    company_id: str,
    mandate_path: str = None
) -> Dict[str, List]:
    organized = {
        "to_reconcile": [],
        "in_process": [],
        "pending": []
    }
    
    # Initialiser toutes les transactions dans to_reconcile
    tx_by_id = {}
    for tx in transactions:
        move_id = tx.get("move_id")
        if move_id is not None:
            tx_by_id[str(move_id)] = tx
            organized["to_reconcile"].append(tx)
    
    # Récupération parallèle des batches et pending
    async def fetch_batches():
        firebase = FirebaseManagement()
        notifications_path = f"clients/{user_id}/notifications"
        
        batches = await asyncio.to_thread(
            firebase.get_banker_batches,
            user_id,
            notifications_path,
            company_id
        )
        
        batch_tx_ids = set()
        if batches:
            for batch in batches:
                for tx_item in batch.get('transactions', []):
                    tx_id = tx_item.get('move_id')
                    if tx_id:
                        batch_tx_ids.add(str(tx_id))
        
        return batch_tx_ids
    
    async def fetch_pending():
        firebase = FirebaseManagement()
        pending_doc = await asyncio.to_thread(
            firebase.download_pending_item_docsheet,
            mandate_path
        )
        
        pending_tx_ids = set()
        if pending_doc:
            items = pending_doc.get('items', {})
            for item in items.values():
                tx_id = item.get('Id')
                if tx_id:
                    pending_tx_ids.add(str(tx_id))
        
        return pending_tx_ids
    
    # Exécution parallèle
    batch_ids, pending_ids = await asyncio.gather(
        fetch_batches(),
        fetch_pending()
    )
    
    # Filtrage: retirer de to_reconcile et placer dans bonnes catégories
    to_remove = []
    for tx in organized["to_reconcile"]:
        move_id = str(tx.get("move_id", ""))
        
        if move_id in batch_ids:
            organized["in_process"].append(tx)
            to_remove.append(tx)
        elif move_id in pending_ids:
            organized["pending"].append(tx)
            to_remove.append(tx)
    
    for tx in to_remove:
        organized["to_reconcile"].remove(tx)
    
    return organized
```

### D. Page Orchestration - Pattern Commun

```python
# Pattern utilisé par les 3 domaines (exemple: invoices)

async def handle_invoices_orchestrate_init(uid, session_id, payload):
    company_id = payload.get("company_id")
    
    # 1. Récupérer contexte depuis SessionStateManager
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path")
    
    # 2. Fetch via cache handler (cache-first)
    cache_handlers = get_firebase_cache_handlers()
    result = await cache_handlers.get_ap_documents(
        user_id=uid,
        company_id=company_id,
        mandate_path=mandate_path
    )
    
    # 3. Extraire données organisées
    data = result["data"]
    to_do = data.get("to_do", [])
    in_process = data.get("in_process", [])
    pending = data.get("pending", [])
    processed = data.get("processed", [])
    
    # 4. Construire payload complet
    invoices_data = {
        "documents": {"to_do": to_do, "in_process": in_process, ...},
        "counts": {"to_do": len(to_do), ...},
        "pagination": {...},
        "meta": {"loaded_at": ..., "source": result.get("source")}
    }
    
    # 5. Cacher page state
    page_state_mgr = get_page_state_manager()
    await page_state_mgr.save_page_state(uid, company_id, "invoices", invoices_data)
    
    # 6. Envoyer au frontend
    await hub.broadcast(uid, {
        "type": WS_EVENTS.INVOICES.FULL_DATA,
        "payload": invoices_data
    })
```

### E. Refresh - Pattern Commun

```python
# Pattern de refresh (exemple: banking)

async def handle_banking_refresh(uid, session_id, payload):
    company_id = payload.get("company_id")
    
    # 1. Invalider cache AVANT re-fetch
    cache = get_firebase_cache_manager()
    await cache.delete_cached_data(uid, company_id, "bank", "transactions")
    
    # 2. Re-run orchestration (détectera cache MISS → fetch source)
    await handle_banking_orchestrate_init(uid, session_id, {
        "company_id": company_id
    })
    
    # L'orchestration va:
    # - Détecter cache MISS
    # - Fetch depuis source (ERP)
    # - Appliquer logique métier
    # - Mettre en cache résultat
    # - Envoyer au frontend
```

## 🔗 Références Croisées

### Documentation Connexe

- [SCHEMA_FLUX_CACHE_COMPLET.md](SCHEMA_FLUX_CACHE_COMPLET.md) - Vue d'ensemble des flux complets
- [MAPPING_CHARGEMENT_DONNEES.md](MAPPING_CHARGEMENT_DONNEES.md) - Mapping détaillé des sources
- Documentation Reflex originale (pinnokio_app) - Pattern source

### Fichiers Clés Mentionnés

**Cache Handlers:**
- [app/drive_cache_handlers.py](app/drive_cache_handlers.py) - Router/Routing
- [app/firebase_cache_handlers.py](app/firebase_cache_handlers.py) - APBookkeeper + Banking
- [app/cache/unified_cache_manager.py](app/cache/unified_cache_manager.py) - Gestion Redis

**Pages:**
- [app/frontend/pages/routing/orchestration.py](app/frontend/pages/routing/orchestration.py)
- [app/frontend/pages/invoices/orchestration.py](app/frontend/pages/invoices/orchestration.py)
- [app/frontend/pages/banking/orchestration.py](app/frontend/pages/banking/orchestration.py)

**Dashboard:**
- [app/wrappers/dashboard_orchestration_handlers.py](app/wrappers/dashboard_orchestration_handlers.py)
- [app/frontend/pages/dashboard/handlers.py](app/frontend/pages/dashboard/handlers.py)

**Firebase:**
- [app/firebase_providers.py](app/firebase_providers.py) - check_job_status, get_banker_batches, etc.
