# Architecture Redis - Jobs & Metrics

## 📋 Vue d'ensemble

Cette documentation décrit l'architecture actuelle pour le chargement et la mise en cache des données de jobs (APBookkeeper, Router, Bank) dans Redis, ainsi que leur utilisation par l'agent Pinnokio.

### Objectifs

1. **Source unique de vérité** : Utiliser le namespace `cache:*` comme source unique pour frontend et backend
2. **Données à jour** : Rechargement depuis Redis à chaque appel d'outil en mode UI
3. **Format uniforme** : Format Reflex compatible pour interopérabilité frontend/backend
4. **Performance** : Cache Redis pour réduire les appels aux sources (Firebase, Drive, ERP)

---

## 🏗️ Architecture

### Composants principaux

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Reflex)                        │
│  - Charge les données dans Redis (cache:*)                      │
│  - Format uniforme Reflex                                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Redis (cache:*)
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                    BACKEND (Python)                             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              JobLoader (job_loader.py)                   │  │
│  │  - Charge depuis Redis (mode UI) ou sources (BACKEND)   │  │
│  │  - Écrit toujours dans Redis après fetch                 │  │
│  └────────────────────┬─────────────────────────────────────┘  │
│                       │                                          │
│  ┌────────────────────┴─────────────────────────────────────┐  │
│  │         JobTools (job_tools.py)                          │  │
│  │  - RouterJobTools, APBookkeeperJobTools, BankJobTools    │  │
│  │  - Recharge depuis Redis à chaque appel (mode UI)        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              PinnokioBrain (pinnokio_brain.py)           │  │
│  │  - Initialise les outils avec mode (UI/BACKEND)          │  │
│  │  - Passe user_id, company_id, user_context aux outils    │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Modes de fonctionnement

### Mode UI (Utilisateur connecté)

**Caractéristiques** :
- Utilisateur connecté via le frontend
- Cache Redis est à jour (mis à jour par le frontend)
- Streaming activé (`enable_streaming=True`)

**Workflow** :
1. **Initialisation session** (`_load_jobs_with_metrics`) :
   - ✅ Vérifier cache Redis (`cache:*`)
   - ✅ Si cache HIT → utiliser données Redis
   - ✅ Si cache MISS → fetch depuis source → écrire dans Redis
   - ✅ Calculer métriques pour le system prompt

2. **Appel outil** (ex: `GET_ROUTER_JOBS`) :
   - ✅ Recharger depuis Redis à chaque appel (données à jour)
   - ✅ Si erreur → fallback vers données initiales

**Avantages** :
- Données toujours à jour (rechargement à chaque appel)
- Performance (cache Redis)
- Cohérence avec le frontend (même source de vérité)

### Mode BACKEND (Utilisateur déconnecté / Tâche planifiée)

**Caractéristiques** :
- Utilisateur déconnecté ou tâche planifiée
- Cache Redis peut être obsolète
- Pas de streaming (`enable_streaming=False`)

**Workflow** :
1. **Initialisation session** (`_load_jobs_with_metrics`) :
   - ✅ Toujours fetch depuis source (Firebase/Drive/ERP)
   - ✅ Écrire dans Redis (pour prochain mode UI)
   - ✅ Calculer métriques pour le system prompt

2. **Appel outil** (ex: `GET_ROUTER_JOBS`) :
   - ✅ Utiliser données statiques initiales (pas de rechargement)
   - ⚠️ Données peuvent être obsolètes (mais cohérentes avec l'initialisation)

**Avantages** :
- Données fraîches à l'initialisation (source directe)
- Pas de dépendance au cache (qui peut être obsolète)
- Mise à jour du cache pour prochain mode UI

---

## 🔑 Format des clés Redis

### Structure

```
cache:{user_id}:{company_id}:{data_type}:{sub_type}
```

### Mapping départements → clés

| Département | Clé Redis | Exemple |
|------------|-----------|---------|
| **APBOOKEEPER** | `cache:{user_id}:{company_id}:apbookeeper:documents` | `cache:user123:company456:apbookeeper:documents` |
| **ROUTER** | `cache:{user_id}:{company_id}:drive:documents` | `cache:user123:company456:drive:documents` |
| **BANK** | `cache:{user_id}:{company_id}:bank:transactions` | `cache:user123:company456:bank:transactions` |

### Code de construction

```python
def _build_reflex_cache_key(self, department: str) -> str:
    """Construit la clé Redis compatible avec le format Reflex."""
    reflex_mapping = {
        "BANK": "bank:transactions",
        "ROUTER": "drive:documents",
        "APBOOKEEPER": "apbookeeper:documents"
    }
    
    data_type_sub = reflex_mapping.get(department)
    cache_key = f"cache:{self.user_id}:{self.company_id}:{data_type_sub}"
    return cache_key
```

---

## 📦 Format des données dans Redis

### Structure JSON

```json
{
  "data": {
    // Données du département (format Reflex)
  },
  "cached_at": "2025-12-03T10:30:00.123456",
  "source": "router.documents",
  "ttl_seconds": 3600
}
```

### Format par département

#### 1. APBOOKEEPER (`apbookeeper:documents`)

```json
{
  "data": {
    "to_do": [
      {
        "id": "invoice_123",
        "name": "Facture_Fournisseur_2025.pdf",
        "status": "to_do",
        "created_time": "2025-12-01T10:00:00",
        "drive_file_id": "1a2b3c4d5e6f",
        "amount": 1500.00,
        "currency": "EUR"
      }
    ],
    "in_process": [],
    "pending": [],
    "processed": []
  },
  "cached_at": "2025-12-03T10:30:00.123456",
  "source": "apbookeeper.documents",
  "ttl_seconds": 3600
}
```
```

**Statuts** :
- `to_do` : Factures à traiter
- `in_process` : En cours de traitement
- `pending` : En attente
- `processed` : Traitées

#### 2. ROUTER (`drive:documents`)

```json
{
  "data": {
    "to_process": [
      {
        "id": "doc_123",
        "name": "Contrat_Client_ABC.pdf",
        "status": "to_process",
        "created_time": "2025-12-01T10:00:00",
        "router_drive_view_link": "https://drive.google.com/file/d/...",
        "drive_file_id": "1a2b3c4d5e6f"
      }
    ],
    "in_process": [],
    "processed": []
  },
  "cached_at": "2025-12-03T10:30:00.123456",
  "source": "router.documents",
  "ttl_seconds": 3600
}
```
```

**Statuts** :
- `to_process` : Documents à router
- `in_process` : En cours de routage
- `processed` : Routés

#### 3. BANK (`bank:transactions`)

```json
{
  "data": {
    "to_reconcile": [
      {
        "transaction_id": "txn_123",
        "journal_id": "bank_account_001",
        "date": "2025-12-01",
        "amount": 5000.00,
        "currency_id": "EUR",
        "partner_name": "Client ABC",
        "partner_id": "partner_123",
        "payment_ref": "REF-2025-001",
        "ref": "Internal-REF-001",
        "transaction_type": "inbound",
        "amount_residual": 5000.00,
        "is_reconciled": false,
        "display_name": "Payment from Client ABC",
        "state": "posted"
      }
    ],
    "pending": [],
    "in_process": [],
    "in_process_batches": []
  },
  "cached_at": "2025-12-03T10:30:00.123456",
  "source": "bank.transactions",
  "ttl_seconds": 3600
}
```
```

**Statuts** :
- `to_reconcile` : Transactions à réconcilier
- `pending` : En attente
- `in_process` : En cours de réconciliation
- `in_process_batches` : Lots en cours

---

## 📊 Sources de données

### 1. APBOOKEEPER

**Source** : Firebase Firestore
- Collection : `{company_id}/apbookeeper/invoices`
- Filtres : Statut (`to_do`, `in_process`, `pending`, `processed`)
- Format : Documents Firestore avec métadonnées

**Méthode** : `_fetch_apbookeeper_from_firebase()`

### 2. ROUTER

**Source** : Google Drive + Firebase
- Drive : Recherche fichiers dans le dossier Router
- Firebase : Métadonnées des documents (statut, etc.)
- Format : Fichiers Drive avec métadonnées Firebase

**Méthode** : `_fetch_router_from_drive_and_firebase()`

### 3. BANK

**Source** : ERP Odoo (API REST)
- Endpoint : `{odoo_url}/api/bank/transactions`
- Authentification : OAuth2 / API Key
- Format : Transactions bancaires Odoo

**Méthode** : `_fetch_bank_from_erp()`

---

## 🔄 Flux de données

### 1. Initialisation de la session (mode UI)

```
┌─────────────┐
│ LLMSession  │
│ _load_jobs  │
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  JobLoader      │
│ load_all_jobs() │
└──────┬──────────┘
       │
       ├─► Mode UI ?
       │   ├─► OUI → Vérifier Redis cache
       │   │   ├─► Cache HIT → Utiliser données Redis
       │   │   └─► Cache MISS → Fetch source → Écrire Redis
       │   │
       │   └─► NON (BACKEND) → Fetch source → Écrire Redis
       │
       ▼
┌─────────────────┐
│ calculate_      │
│ metrics()       │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ PinnokioBrain   │
│ jobs_metrics    │
│ (system prompt) │
└─────────────────┘
```

### 2. Appel outil (ex: GET_ROUTER_JOBS) - Mode UI

```
┌─────────────────┐
│ Agent appelle   │
│ GET_ROUTER_JOBS │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ RouterJobTools  │
│ search()        │
└──────┬──────────┘
       │
       ├─► Mode UI ?
       │   ├─► OUI → Recharger depuis Redis
       │   │   ├─► JobLoader.load_router_jobs(mode="UI")
       │   │   │   ├─► Vérifier Redis cache
       │   │   │   ├─► Cache HIT → Retourner données
       │   │   │   └─► Cache MISS → Fetch source → Écrire Redis
       │   │   │
       │   │   └─► Filtrer et retourner résultats
       │   │
       │   └─► NON (BACKEND) → Utiliser données statiques initiales
       │
       ▼
┌─────────────────┐
│ Résultats       │
│ filtrés         │
└─────────────────┘
```

---

## 🛠️ Implémentation technique

### JobLoader (`job_loader.py`)

**Responsabilités** :
- Charger les jobs depuis Redis ou sources
- Écrire dans Redis après fetch
- Calculer les métriques agrégées

**Méthodes principales** :
- `load_all_jobs(mode, user_context)` : Charge tous les départements
- `load_apbookeeper_jobs(mode)` : Charge APBookkeeper
- `load_router_jobs(mode, user_context)` : Charge Router
- `load_bank_transactions(mode, user_context)` : Charge Bank
- `_get_from_cache(department)` : Lit depuis Redis
- `_set_to_cache(department, data, ttl)` : Écrit dans Redis
- `_build_reflex_cache_key(department)` : Construit la clé Redis

### JobTools (`job_tools.py`)

**Responsabilités** :
- Rechercher et filtrer les jobs par département
- Recharger depuis Redis à chaque appel (mode UI)

**Classes** :
- `RouterJobTools` : Outil `GET_ROUTER_JOBS`
- `APBookkeeperJobTools` : Outil `GET_APBOOKEEPER_JOBS`
- `BankJobTools` : Outil `GET_BANK_TRANSACTIONS`

**Rechargement Redis** :
```python
async def search(self, ...):
    # ⭐ Recharger depuis Redis si mode UI
    if self.mode == "UI" and self.user_id and self.company_id:
        loader = JobLoader(
            user_id=self.user_id,
            company_id=self.company_id,
            client_uuid=self.user_context.get("client_uuid")
        )
        fresh_data = await loader.load_router_jobs(mode="UI", user_context=self.user_context)
        if fresh_data:
            router_data = fresh_data  # Utiliser données fraîches
```

### PinnokioBrain (`pinnokio_brain.py`)

**Responsabilités** :
- Initialiser les outils avec les paramètres nécessaires
- Passer le mode (UI/BACKEND) aux outils

**Initialisation des outils** :
```python
def _build_general_chat_tools(self, thread_key, session=None, mode="UI"):
    # Créer les outils avec mode, user_id, company_id, user_context
    router_tools = RouterJobTools(
        jobs_data=self.jobs_data,
        user_id=self.firebase_user_id,
        company_id=self.collection_name,
        user_context=self.user_context,
        mode=mode  # ⭐ Mode UI ou BACKEND
    )
```

---

## ⚙️ Configuration

### TTL (Time To Live)

**Valeur par défaut** : `3600` secondes (1 heure)

**Configuration** :
```python
await self._set_to_cache("ROUTER", data, ttl=3600)
```

### Mode détermination

**Dans `llm_manager.py`** :
```python
mode = "UI" if enable_streaming else "BACKEND"
```

**Dans `pinnokio_brain.py`** :
```python
tools, tool_mapping = brain.create_workflow_tools(
    thread_key,
    session,
    chat_mode=chat_mode,
    mode=mode  # ⭐ Passé depuis _process_unified_workflow
)
```

---

## 🔍 Logs et debugging

### Logs JobLoader

```
[JOB_LOADER] ✅ CACHE HIT (Reflex): cache:user123:company456:drive:documents | Cached: 2025-12-03T10:30:00
[JOB_LOADER] ❌ CACHE MISS (Reflex): cache:user123:company456:drive:documents
[JOB_LOADER] Fetch Router depuis Drive + Firebase...
[JOB_LOADER] ✅ Écriture cache Redis: cache:user123:company456:drive:documents (TTL: 3600s)
```

### Logs JobTools

```
[ROUTER_TOOLS] Initialisé avec 5 documents to_process (mode=UI)
[GET_ROUTER_JOBS] Recherche - status=to_process, file_name=None, limit=50
[GET_ROUTER_JOBS] ✅ Données rechargées depuis Redis - 5 documents to_process
```

### Logs PinnokioBrain

```
[BRAIN] 🔍 DIAGNOSTIC self.jobs_data avant création outils - Clés: ['ROUTER', 'APBOOKEEPER', 'BANK']
[BRAIN] 🔍 DIAGNOSTIC self.jobs_data['ROUTER']['to_process'] - Longueur: 5
```

---

## ✅ Avantages de l'architecture

1. **Source unique de vérité** : `cache:*` utilisé par frontend et backend
2. **Données à jour** : Rechargement Redis à chaque appel outil (mode UI)
3. **Format uniforme** : Format Reflex compatible
4. **Performance** : Cache Redis réduit les appels sources
5. **Cohérence** : Frontend et backend utilisent les mêmes données
6. **Flexibilité** : Mode BACKEND pour données fraîches à l'initialisation

---

## 🚨 Points d'attention

1. **TTL** : Le cache expire après 1h par défaut. Ajuster selon besoins.
2. **Mode BACKEND** : Les outils utilisent données statiques (pas de rechargement). Les données peuvent être obsolètes si la session est longue.
3. **Erreurs Redis** : En cas d'erreur de rechargement, fallback vers données initiales (pas d'exception).
4. **Format Reflex** : Respecter le format `cache:{user_id}:{company_id}:{data_type}:{sub_type}` pour compatibilité frontend.

---

## 📝 Exemple d'utilisation

### Frontend (Reflex) - Écriture dans Redis

```python
# Frontend écrit dans Redis
cache_key = f"cache:{user_id}:{company_id}:drive:documents"
redis_client.set(
    cache_key,
    json.dumps({
        "data": router_documents,
        "cached_at": datetime.now().isoformat(),
        "source": "router.documents",
        "ttl": 3600
    }),
    ex=3600  # TTL 1h
)
```

### Backend - Lecture depuis Redis

```python
# Backend lit depuis Redis (mode UI)
loader = JobLoader(user_id=user_id, company_id=company_id)
router_data = await loader.load_router_jobs(mode="UI", user_context=user_context)
```

### Agent - Utilisation des outils

```python
# Agent appelle l'outil
result = await router_tools.search(status="to_process", limit=10)
# → Recharge automatiquement depuis Redis (mode UI)
# → Retourne les documents à jour
```

---

## 🔗 Fichiers concernés

- `app/pinnokio_agentic_workflow/tools/job_loader.py` : Chargement et cache
- `app/pinnokio_agentic_workflow/tools/job_tools.py` : Outils de recherche
- `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` : Initialisation outils
- `app/llm_service/llm_manager.py` : Détermination mode et workflow
- `app/main.py` : Invalidation cache (endpoint `/invalidate_cache`)

---

**Dernière mise à jour** : 2025-12-03
**Version** : 1.0

