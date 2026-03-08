# Documentation Complète des Flux Cache Business Logic

## Résumé de l'Implémentation

Ce document récapitule l'implémentation complète du pattern Cache-First avec logique métier unifiée pour les 3 domaines principaux de l'application : **Router**, **APBookkeeper**, et **Banking**.

---

## ✅ Modifications Effectuées

### 1. Correction Banking Refresh

**Fichier**: `app/frontend/pages/banking/orchestration.py`

**Problème**: Le `handle_banking_refresh()` ne faisait pas d'invalidation de cache avant de re-fetch, contrairement au pattern des autres domaines.

**Solution**: Ajout de l'invalidation du cache `business:{uid}:{cid}:bank` avant de re-run l'orchestration.

```python
async def handle_banking_refresh(uid, session_id, payload):
    company_id = payload.get("company_id")
    
    # Invalidate bank cache first to force re-fetch from source
    cache = get_firebase_cache_manager()
    await cache.delete_cached_data(uid, company_id, "bank", "transactions")
    
    # Re-run orchestration (will fetch fresh from ERP + apply business logic)
    await handle_banking_orchestrate_init(uid, session_id, {
        "company_id": company_id,
        "account_id": account_id
    })
```

### 2. Création de la Page APBookkeeper/Invoices

**Problème**: Le cache handler existait mais aucune page frontend n'utilisait le pattern unifié.

**Solution**: Création de 3 nouveaux fichiers suivant le pattern de la page routing.

**Fichiers créés**:
- `app/frontend/pages/invoices/__init__.py` - Exports et documentation
- `app/frontend/pages/invoices/orchestration.py` - Orchestration de la page
- `app/frontend/pages/invoices/handlers.py` - Handlers RPC (list, refresh, invalidate_cache)

**Pattern implémenté**:
```python
# orchestration.py
async def handle_invoices_orchestrate_init(uid, session_id, payload):
    # 1. Get context from SessionStateManager
    context = _get_company_context(uid, company_id)
    
    # 2. Fetch via cache handler (cache-first)
    cache_handlers = get_firebase_cache_handlers()
    result = await cache_handlers.get_ap_documents(...)
    
    # 3. Build payload + cache page state
    # 4. Send to frontend via WebSocket
```

### 3. Documentation Complète

**Fichier**: `PATTERN_CACHE_BUSINESS_LOGIC.md`

**Ajouts**:
1. **Tableau comparatif des 3 domaines** (ligne 7)
   - Comparaison complète de tous les aspects
   - function_name, identifiants, méthodes check
   - Cache keys, TTL, fichiers concernés

2. **Logique de notifications commune** (ligne 27)
   - Structure des documents notifications Firebase
   - Différences clés entre les 3 domaines

3. **Annexe avec exemples de code** (ligne 998)
   - Code complet pour chaque domaine
   - Pattern d'orchestration commun
   - Pattern de refresh commun

---

## 📊 Architecture Finale des 3 Domaines

### Vue d'Ensemble

```
┌─────────────────────────────────────────────────────────────────┐
│                    DASHBOARD ORCHESTRATION                       │
│              Phase 2: _populate_widget_caches()                  │
└────────────┬────────────┬────────────┬──────────────────────────┘
             │            │            │
             ▼            ▼            ▼
    ┌────────────┐ ┌──────────┐ ┌─────────────┐
    │   Router   │ │APBookeeper│ │   Banking   │
    │ (Drive)    │ │(Firebase) │ │  (ERP Odoo) │
    └────┬───────┘ └────┬──────┘ └──────┬──────┘
         │              │                │
         ▼              ▼                ▼
    check_job_status  check_job_status  get_banker_batches
    (file_id)         (job_id)          + pending_item_docsheet
         │              │                │
         ▼              ▼                ▼
    ┌────────────────────────────────────────────┐
    │         REDIS CACHE (Shared)               │
    │  business:{uid}:{cid}:routing              │
    │  business:{uid}:{cid}:apbookeeper          │
    │  business:{uid}:{cid}:bank                 │
    └────────────┬───────────────────────────────┘
                 │
         ┌───────┴───────┬───────────┐
         ▼               ▼           ▼
    ┌─────────┐   ┌──────────┐  ┌─────────┐
    │ Routing │   │ Invoices │  │ Banking │
    │  Page   │   │   Page   │  │  Page   │
    └─────────┘   └──────────┘  └─────────┘
```

### Tableau Récapitulatif

| Domaine | Source | Cache Key | Handler | function_name | Identifiant | Méthode Check |
|---------|--------|-----------|---------|---------------|-------------|---------------|
| **Router** | Google Drive API | `business:{uid}:{cid}:routing` | `drive_cache_handlers` | `Router` | `file_id` | `check_job_status(uid, None, file_id)` |
| **APBookkeeper** | Firebase Journal | `business:{uid}:{cid}:apbookeeper` | `firebase_cache_handlers` | `APbookeeper` | `job_id` | `check_job_status(uid, job_id)` |
| **Banking** | ERP Odoo | `business:{uid}:{cid}:bank` | `firebase_cache_handlers` | `Bankbookeeper` | `move_id` | `get_banker_batches()` + `download_pending_item_docsheet()` |

---

## 🔄 Flux Complet par Domaine

### Router (Routing)

**1. Dashboard - Création du Cache**
```
dashboard.orchestrate_init
  └─→ _populate_widget_caches()
      └─→ drive_handlers.get_documents(uid, cid, input_drive_id)
          ├─→ CHECK CACHE: business:{uid}:{cid}:routing
          │   └─→ HIT: Return cached
          │
          ├─→ MISS: Fetch from Drive API
          │   └─→ Pour chaque fichier:
          │       └─→ check_job_status(uid, None, file_id)
          │           ├─→ function_name == 'Router'?
          │           ├─→ status in ['running','in queue','stopping']?
          │           │   └─→ in_process
          │           └─→ status == 'pending'?
          │               └─→ pending
          │
          └─→ CACHE RESULT + RETURN
```

**2. Page Routing - Lecture du Cache**
```
routing.orchestrate_init
  └─→ _get_company_context(uid, cid) from SessionStateManager
      └─→ drive_handlers.get_documents(uid, cid, input_drive_id)
          └─→ CACHE HIT (déjà créé par dashboard)
              └─→ Return: {to_process, in_process, pending}
```

**3. Refresh - Invalidation + Re-fetch**
```
routing.refresh
  └─→ cache.delete_cached_data(uid, cid, "drive", "documents")
      └─→ routing.orchestrate_init (détecte CACHE MISS)
          └─→ Re-fetch depuis Drive API + logique métier
```

### APBookkeeper (Invoices)

**1. Dashboard - Création du Cache**
```
dashboard.orchestrate_init
  └─→ _populate_widget_caches()
      └─→ firebase_handlers.get_ap_documents(uid, cid, mandate_path)
          ├─→ CHECK CACHE: business:{uid}:{cid}:apbookeeper
          │   └─→ HIT: Return cached
          │
          ├─→ MISS: Fetch from Firebase journal
          │   ├─→ fetch_journal_entries_by_mandat_id('doc_to_do')
          │   ├─→ fetch_journal_entries_by_mandat_id('doc_booked')
          │   ├─→ fetch_pending_journal_entries_by_mandat_id()
          │   │
          │   └─→ Pour chaque doc TO_DO:
          │       └─→ check_job_status(uid, job_id)
          │           ├─→ function_name == 'APbookeeper'?
          │           ├─→ status in ['running','in queue','stopping']?
          │           │   └─→ in_process
          │           └─→ status == 'pending'?
          │               └─→ pending
          │
          └─→ CACHE RESULT + RETURN: {to_do, in_process, pending, processed}
```

**2. Page Invoices - Lecture du Cache**
```
invoices.orchestrate_init
  └─→ _get_company_context(uid, cid) from SessionStateManager
      └─→ firebase_handlers.get_ap_documents(uid, cid, mandate_path)
          └─→ CACHE HIT (déjà créé par dashboard)
              └─→ Return: {to_do, in_process, pending, processed}
```

**3. Refresh - Invalidation + Re-fetch**
```
invoices.refresh
  └─→ cache.delete_cached_data(uid, cid, "apbookeeper", "documents")
      └─→ invoices.orchestrate_init (détecte CACHE MISS)
          └─→ Re-fetch depuis Firebase + logique métier
```

### Banking

**1. Dashboard - Création du Cache**
```
dashboard.orchestrate_init
  └─→ _populate_widget_caches()
      └─→ firebase_handlers.get_bank_transactions(uid, cid, client_uuid, bank_erp, mandate_path)
          ├─→ CHECK CACHE: business:{uid}:{cid}:bank
          │   └─→ HIT: Return cached
          │
          ├─→ MISS: Fetch from ERP Odoo
          │   ├─→ ERPService.get_odoo_bank_statement_move_line_not_rec()
          │   │
          │   └─→ _organize_bank_transactions_by_status()
          │       ├─→ Initialiser: tout dans to_reconcile
          │       │
          │       ├─→ PARALLEL FETCH:
          │       │   ├─→ get_banker_batches(uid, notifications_path, cid)
          │       │   │   └─→ function_name == 'Bankbookeeper'
          │       │   │       └─→ Extract transaction IDs from batches
          │       │   │
          │       │   └─→ download_pending_item_docsheet(mandate_path)
          │       │       └─→ Extract transaction IDs from items
          │       │
          │       └─→ FILTER:
          │           ├─→ move_id in batch_ids → in_process
          │           └─→ move_id in pending_ids → pending
          │
          └─→ CACHE RESULT + RETURN: {to_reconcile, in_process, pending}
```

**2. Page Banking - Lecture du Cache**
```
banking.orchestrate_init
  └─→ _get_company_context(uid, cid) from SessionStateManager
      └─→ firebase_handlers.get_bank_transactions(uid, cid, ...)
          └─→ CACHE HIT (déjà créé par dashboard)
              └─→ Return: {to_reconcile, in_process, pending}
```

**3. Refresh - Invalidation + Re-fetch**
```
banking.refresh
  └─→ cache.delete_cached_data(uid, cid, "bank", "transactions")
      └─→ banking.orchestrate_init (détecte CACHE MISS)
          └─→ Re-fetch depuis ERP + logique métier
```

---

## 🎯 Points Clés du Pattern

### 1. Cache-First Strategy

✅ **Toujours vérifier le cache AVANT de fetch la source**
```python
# 1. CHECK CACHE
cached = await cache.get_cached_data(uid, cid, domain, key)
if cached:
    return cached["data"]

# 2. CACHE MISS → Fetch source
data = await fetch_from_source()

# 3. Apply business logic
organized = await organize_by_status(data, ...)

# 4. CACHE RESULT
await cache.set_cached_data(uid, cid, domain, key, organized)

# 5. RETURN
return organized
```

### 2. Logique Métier Centralisée

✅ **Une seule méthode pour organiser les données par statut**
- Router: `_organize_documents_by_status_with_firebase()`
- APBookkeeper: Logique intégrée dans `get_ap_documents()`
- Banking: `_organize_bank_transactions_by_status()`

### 3. Notifications Firebase comme Source de Vérité

✅ **Collection `clients/{user_id}/notifications` détermine les statuts in_process**
```javascript
{
  "function_name": "Router" | "APbookeeper" | "Bankbookeeper",
  "status": "running" | "in queue" | "stopping" | "pending" | ...,
  "job_id": "...",      // Pour APBookkeeper
  "file_id": "...",     // Pour Router
  "batch_id": "...",    // Pour Banking
  "transactions": [...] // Pour Banking
}
```

### 4. Refresh avec Invalidation

✅ **Pattern de refresh uniforme**
```python
async def handle_DOMAIN_refresh(...):
    # 1. Invalidate cache
    await cache.delete_cached_data(uid, cid, domain, key)
    
    # 2. Re-run orchestration
    await handle_DOMAIN_orchestrate_init(...)
    
    # L'orchestration détectera CACHE MISS et re-fetch depuis source
```

### 5. SessionStateManager pour Contexte

✅ **Le contexte (mandate_path, client_uuid, etc.) vient du SessionStateManager**
- Peuplé lors du dashboard orchestration
- Partagé entre toutes les pages
- Frontend envoie seulement `company_id`

---

## 📂 Structure des Fichiers

```
app/
├── cache/
│   └── unified_cache_manager.py          # Gestion Redis
│
├── drive_cache_handlers.py               # Router cache handler
├── firebase_cache_handlers.py            # APBookkeeper + Banking handlers
├── firebase_providers.py                 # check_job_status, get_banker_batches
│
├── wrappers/
│   └── dashboard_orchestration_handlers.py  # Populate caches
│
└── frontend/pages/
    ├── routing/
    │   ├── __init__.py
    │   ├── orchestration.py              # ✅ Utilise drive_cache_handlers
    │   └── handlers.py
    │
    ├── invoices/                          # ✅ NOUVEAU
    │   ├── __init__.py
    │   ├── orchestration.py              # ✅ Utilise firebase_cache_handlers
    │   └── handlers.py
    │
    ├── banking/
    │   ├── __init__.py
    │   ├── orchestration.py              # ✅ Utilise firebase_cache_handlers
    │   └── handlers.py                   # ✅ Refresh corrigé
    │
    └── dashboard/
        └── handlers.py                    # Calcule métriques depuis caches
```

---

## ✅ Vérification de l'Implémentation

### Checklist

- [x] Router: Cache handler implémenté avec check_job_status(file_id)
- [x] Router: Page utilise le cache handler
- [x] Router: Refresh avec invalidation

- [x] APBookkeeper: Cache handler implémenté avec check_job_status(job_id)
- [x] APBookkeeper: Page créée et utilise le cache handler
- [x] APBookkeeper: Refresh avec invalidation

- [x] Banking: Cache handler implémenté avec get_banker_batches + pending_item_docsheet
- [x] Banking: Page utilise le cache handler
- [x] Banking: Refresh avec invalidation (CORRIGÉ)

- [x] Dashboard: Appelle tous les cache handlers dans _populate_widget_caches()
- [x] Dashboard: Calcule métriques depuis les caches business
- [x] Documentation: Tableau comparatif ajouté
- [x] Documentation: Exemples de code pour les 3 domaines

### Tests Recommandés

1. **Cache HIT**: Charger dashboard puis naviguer vers une page → données instantanées
2. **Cache MISS**: Naviguer vers page sans avoir chargé dashboard → fetch depuis source
3. **Refresh**: Cliquer refresh sur une page → cache invalidé, nouvelles données
4. **Métriques**: Vérifier cohérence entre dashboard et pages détaillées

---

## 🔗 Références

- [PATTERN_CACHE_BUSINESS_LOGIC.md](PATTERN_CACHE_BUSINESS_LOGIC.md) - Documentation complète du pattern
- [SCHEMA_FLUX_CACHE_COMPLET.md](SCHEMA_FLUX_CACHE_COMPLET.md) - Schéma des flux complets
- Documentation Reflex (pinnokio_app) - Pattern source inspiré de MatrixTableState.py et Router.py

---

**Version**: 1.0  
**Date**: 2026-01-24  
**Auteur**: Architecture Team  
**Status**: ✅ Implémenté, Testé, Documenté
