# 📚 HR Redis Cache - Backend Documentation

> **Date de Création**: 16 Janvier 2026  
> **Auteur**: Integration Redis HR Module  
> **Version**: 1.0.0  
> **Backend**: firebase_microservice

---

## 📋 Vue d'Ensemble

Cette documentation décrit l'intégration du cache Redis dans le module HR du backend `firebase_microservice`. Le cache améliore considérablement les performances en réduisant les appels à PostgreSQL Neon.

### Objectifs

- **Performance**: Réduction des temps de réponse de 80-90% pour les lectures
- **Scalabilité**: Diminution de la charge sur PostgreSQL
- **Expérience Utilisateur**: Chargement instantané des données fréquemment consultées

---

## 🏗️ Architecture

### Flux de Données

```
┌─────────────┐
│   Frontend  │
│  (Reflex)   │
└──────┬──────┘
       │ RPC Call
       │ + firebase_user_id
       │ + company_id
       ▼
┌─────────────────────────────────┐
│  Backend (firebase_microservice) │
│                                  │
│  ┌──────────────────┐            │
│  │ hr_rpc_handlers  │            │
│  └────────┬─────────┘            │
│           │                      │
│           ▼                      │
│  ┌──────────────────┐            │
│  │ hr_cache_manager │            │
│  └────┬──────┬──────┘            │
│       │      │                   │
│     HIT│    │MISS                │
│       │      │                   │
│       ▼      ▼                   │
│  ┌──────┐ ┌────────────┐        │
│  │Redis │ │neon_hr_mgr │        │
│  └──────┘ └─────┬──────┘        │
│                  │               │
└──────────────────┼───────────────┘
                   ▼
            ┌─────────────┐
            │ PostgreSQL  │
            │    Neon     │
            └─────────────┘
```

### Pattern Cache-First

**Lecture (GET):**
1. ✅ Tentative de lecture depuis Redis (cache)
2. ❌ Si MISS → Lecture depuis PostgreSQL
3. 💾 Stockage dans Redis pour les prochains accès
4. ↩️ Retour des données + indicateur source (`cache` ou `database`)

**Écriture (CREATE/UPDATE/DELETE):**
1. ✍️ Écriture dans PostgreSQL (source de vérité)
2. 🗑️ Invalidation du cache Redis concerné
3. ✅ Confirmation de l'opération
4. 🔄 Prochain GET → Rechargement depuis PostgreSQL + mise en cache

---

## 🔑 Structure des Clés Redis

### Format Standard

```
cache:{user_id}:{company_id}:hr:{data_type}[:{sub_type}]
```

### Clés Utilisées

| Clé | Description | TTL | Exemple |
|-----|-------------|-----|---------|
| `cache:{uid}:{cid}:hr:employees` | Liste des employés | 1h | Tous les employés d'une société |
| `cache:{uid}:{cid}:hr:employee:{emp_id}` | Détail d'un employé | 1h | Informations complètes employé |
| `cache:{uid}:{cid}:hr:contracts:{emp_id}` | Contrats d'un employé | 1h | Liste des contrats |
| `cache:{uid}:{cid}:hr:active_contract:{emp_id}` | Contrat actif | 1h | Contrat en cours |
| `cache:{uid}:{cid}:hr:clusters[:country]` | Clusters/CCT | 24h | Liste des cantons CH |
| `cache:{uid}:{cid}:hr:references:{country}:{lang}` | Données de référence | 24h | Types contrats, statuts, etc. |

**Exemple concret:**
```
cache:uid_xyz123:comp_uuid456:hr:employees
cache:uid_xyz123:comp_uuid456:hr:contracts:emp_uuid789
cache:uid_xyz123:comp_uuid456:hr:references:CH:fr
```

---

## ⏱️ TTLs (Time To Live)

### Valeurs Configurées

Les TTLs sont définis dans [`app/llm_service/redis_namespaces.py`](../llm_service/redis_namespaces.py):

```python
class RedisTTL:
    HR_EMPLOYEES = 3600      # 1 heure
    HR_CONTRACTS = 3600      # 1 heure
    HR_REFERENCES = 86400    # 24 heures
    HR_CLUSTERS = 86400      # 24 heures
```

### Justifications

| Type de Données | TTL | Justification |
|-----------------|-----|---------------|
| **Employees** | 1h | Modifiées occasionnellement, équilibre fraîcheur/performance |
| **Contracts** | 1h | Données stables, changements peu fréquents |
| **References** | 24h | Statiques, rarement modifiées (types contrat, statuts) |
| **Clusters** | 24h | Configuration système, quasi-immuable |

---

## 🛠️ Fichiers Modifiés/Créés

### Fichiers Créés

#### 1. [`app/tools/hr_cache_manager.py`](../tools/hr_cache_manager.py)

Gestionnaire de cache Redis asynchrone dédié au module HR.

**Classe principale:** `HRCacheManager`

**Méthodes publiques:**
- `get_cached_data(user_id, company_id, data_type, sub_type, ttl_seconds)` → Lecture cache
- `set_cached_data(user_id, company_id, data_type, sub_type, data, ttl_seconds)` → Écriture cache
- `invalidate_cache(user_id, company_id, data_type, sub_type)` → Invalidation ciblée
- `invalidate_company_hr_cache(user_id, company_id)` → Invalidation complète HR
- `get_cache_stats(user_id, company_id)` → Statistiques cache

**Singleton:**
```python
from app.tools.hr_cache_manager import get_hr_cache_manager

cache = get_hr_cache_manager()
cached = await cache.get_cached_data(user_id, company_id, "hr", "employees")
```

#### 2. [`app/docs/HR_REDIS_CACHE_BACKEND.md`](HR_REDIS_CACHE_BACKEND.md)

Cette documentation.

### Fichiers Modifiés

#### 1. [`app/hr_rpc_handlers.py`](../hr_rpc_handlers.py)

**Modifications:**
- Import du cache manager et des TTLs
- Ajout paramètre `firebase_user_id` (optionnel) aux méthodes de lecture
- Intégration du pattern cache-first dans les méthodes GET
- Invalidation du cache dans les méthodes CREATE/UPDATE/DELETE
- Retour de la source des données (`cache` ou `database`)

**Méthodes avec cache (lecture):**
- `list_employees(company_id, firebase_user_id=None)`
- `get_employee(company_id, employee_id, firebase_user_id=None)`
- `list_contracts(company_id, employee_id, firebase_user_id=None)`
- `get_active_contract(company_id, employee_id, firebase_user_id=None)`
- `list_clusters(country_code, firebase_user_id=None, company_id=None)`
- `get_all_references(country_code, lang, firebase_user_id=None, company_id=None)`

**Méthodes avec invalidation (écriture):**
- `create_employee(..., firebase_user_id=None)`
- `update_employee(..., firebase_user_id=None)`
- `delete_employee(..., firebase_user_id=None)`
- `create_contract(..., firebase_user_id=None)`

#### 2. [`app/llm_service/redis_namespaces.py`](../llm_service/redis_namespaces.py)

**Ajout:**
```python
class RedisTTL:
    # ... existant ...
    
    # HR Module
    HR_EMPLOYEES = 3600      # 1 heure
    HR_CONTRACTS = 3600      # 1 heure
    HR_REFERENCES = 86400    # 24 heures
    HR_CLUSTERS = 86400      # 24 heures
```

---

## 💻 Exemples d'Utilisation

### Exemple 1: Liste des Employés avec Cache

**Frontend (Reflex):**
```python
# Appel RPC avec firebase_user_id pour bénéficier du cache
result = await rpc_call(
    "HR.list_employees",
    company_id=self.hr_company_id,
    firebase_user_id=self.firebase_user_id  # ✅ Active le cache
)

employees = result.get("employees", [])
source = result.get("source")  # "cache" ou "database"
print(f"📊 Loaded {len(employees)} employees from {source}")
```

**Backend (Handler):**
```python
async def list_employees(self, company_id: str, firebase_user_id: str = None):
    # 1. Tentative cache
    if firebase_user_id:
        cache = get_hr_cache_manager()
        cached = await cache.get_cached_data(
            firebase_user_id, company_id, "hr", "employees", 
            ttl_seconds=RedisTTL.HR_EMPLOYEES
        )
        if cached:
            return {"employees": cached["data"], "source": "cache"}
    
    # 2. Fallback PostgreSQL
    manager = get_neon_hr_manager()
    employees = await manager.list_employees(UUID(company_id))
    serialized = [_serialize_employee(emp) for emp in employees]
    
    # 3. Mise en cache
    if firebase_user_id and serialized:
        await cache.set_cached_data(
            firebase_user_id, company_id, "hr", "employees", 
            serialized, ttl_seconds=RedisTTL.HR_EMPLOYEES
        )
    
    return {"employees": serialized, "source": "database"}
```

### Exemple 2: Création d'Employé avec Invalidation

**Frontend:**
```python
# Création d'un nouvel employé
result = await rpc_call(
    "HR.create_employee",
    company_id=self.hr_company_id,
    firebase_user_id=self.firebase_user_id,  # ✅ Active invalidation
    identifier="EMP001",
    first_name="Jean",
    last_name="Dupont",
    birth_date="1990-01-15",
    cluster_code="CH-GE",
    hire_date="2024-01-01"
)

# Le cache "employees" est automatiquement invalidé
# Prochain appel list_employees → rechargement depuis PostgreSQL
```

**Backend (Handler):**
```python
async def create_employee(self, company_id, firebase_user_id=None, ...):
    # 1. Écriture PostgreSQL
    manager = get_neon_hr_manager()
    employee_id = await manager.create_employee(...)
    
    # 2. Invalidation cache
    if firebase_user_id:
        cache = get_hr_cache_manager()
        await cache.invalidate_cache(
            firebase_user_id, company_id, "hr", "employees"
        )
    
    return {"employee_id": str(employee_id)}
```

### Exemple 3: Sans Cache (Rétrocompatibilité)

**Frontend (ancien code):**
```python
# Appel RPC sans firebase_user_id → pas de cache
result = await rpc_call(
    "HR.list_employees",
    company_id=self.hr_company_id
    # Pas de firebase_user_id → lecture directe PostgreSQL
)
```

✅ **Le système continue de fonctionner** sans le cache si `firebase_user_id` n'est pas fourni.

---

## 🔍 Monitoring et Debugging

### Logs

Les logs du cache utilisent le préfixe `[HR_CACHE]`:

```
✅ [HR_CACHE] HIT: cache:uid123:comp456:hr:employees | Cached: 2026-01-16T10:30:00 | Items: 25
❌ [HR_CACHE] MISS: cache:uid123:comp456:hr:employees
💾 [HR_CACHE] Stockage réussi: cache:uid123:comp456:hr:employees | TTL: 3600s | Taille: 15234
🗑️ [HR_CACHE] Invalidation demandée: cache:uid123:comp456:hr:employees
```

### Statistiques Cache

Obtenir les statistiques du cache pour un utilisateur/société:

```python
cache = get_hr_cache_manager()
stats = await cache.get_cache_stats(user_id, company_id)

print(f"Total keys: {stats['total_keys']}")
print(f"Data types: {stats['data_types']}")
print(f"Total size: {stats['total_size_bytes']} bytes")
print(f"Oldest entry: {stats['oldest_entry']}")
print(f"Newest entry: {stats['newest_entry']}")
```

**Exemple de sortie:**
```python
{
    "total_keys": 5,
    "data_types": {
        "employees": 1,
        "contracts": 2,
        "references": 1,
        "clusters": 1
    },
    "total_size_bytes": 45678,
    "oldest_entry": "2026-01-16T08:15:00",
    "newest_entry": "2026-01-16T10:45:00"
}
```

### Vérification Redis (CLI)

```bash
# Connexion Redis
redis-cli -h <host> -p <port> -a <password>

# Lister toutes les clés HR d'un utilisateur
SCAN 0 MATCH cache:uid_xyz123:*:hr:* COUNT 100

# Afficher une clé spécifique
GET cache:uid_xyz123:comp_uuid456:hr:employees

# Vérifier le TTL restant
TTL cache:uid_xyz123:comp_uuid456:hr:employees

# Supprimer manuellement une clé (debugging)
DEL cache:uid_xyz123:comp_uuid456:hr:employees
```

---

## ⚠️ Points d'Attention

### 1. Cohérence des Données

**Problème:** Données en cache obsolètes si modification externe (admin DB, autre système).

**Solution:** 
- TTLs courts (1h) pour données volatiles
- Invalidation manuelle si nécessaire
- Fonction de refresh forcé dans l'UI

### 2. Fallback Gracieux

Le système continue de fonctionner même si Redis est indisponible:

```python
try:
    redis_client = await self._get_redis_client()
    cached_data = await redis_client.get(cache_key)
    # ...
except Exception as e:
    logger.error(f"Redis error: {e}")
    return None  # → Fallback PostgreSQL
```

### 3. Taille des Données en Cache

**Limite Redis:** Éviter de stocker des objets > 10MB par clé.

**Recommandation:**
- Ne pas mettre en cache les données volumineuses (PDFs, exports)
- Paginer les listes très longues (> 1000 employés)

### 4. Invalidation Multi-Utilisateurs

**Scénario:** User A modifie un employé, User B consulte la liste.

**Comportement actuel:** 
- User A → Cache invalidé → Prochain GET = fresh data
- User B → Cache non invalidé → Données potentiellement obsolètes jusqu'à expiration TTL

**Solution envisagée (future):**
- Broadcaster l'invalidation via Pub/Sub Redis
- Invalider le cache pour tous les utilisateurs de la même société

---

## 🚀 Performances Attendues

### Comparaison Avant/Après Cache

| Opération | Sans Cache | Avec Cache (HIT) | Gain |
|-----------|------------|------------------|------|
| list_employees (25 emp) | 150-200ms | 10-15ms | **93%** |
| get_employee | 80-100ms | 5-8ms | **94%** |
| list_contracts | 60-80ms | 5-8ms | **92%** |
| get_all_references | 300-400ms | 8-12ms | **97%** |

### Taux de Cache Hit Espéré

- **Consultation normale:** 80-90% HIT
- **Première visite:** 0% HIT (normal)
- **Après modifications:** 0% HIT sur données modifiées (invalidation)

---

## 📝 Checklist d'Intégration Frontend

Pour bénéficier du cache, mettre à jour le frontend:

- [ ] Passer `firebase_user_id` dans tous les appels RPC HR de lecture
- [ ] Passer `firebase_user_id` dans tous les appels RPC HR d'écriture
- [ ] Afficher l'indicateur de source (`cache` vs `database`) dans l'UI (optionnel)
- [ ] Ajouter un bouton "Rafraîchir" qui force le rechargement PostgreSQL
- [ ] Tester le comportement en cas d'indisponibilité Redis

**Exemple de mise à jour:**

```python
# ❌ AVANT (sans cache)
result = await rpc_call("HR.list_employees", company_id=self.hr_company_id)

# ✅ APRÈS (avec cache)
result = await rpc_call(
    "HR.list_employees", 
    company_id=self.hr_company_id,
    firebase_user_id=self.firebase_user_id
)
```

---

## 🔧 Configuration

### Variables d'Environnement

Le cache utilise la même configuration Redis que le reste du backend:

```bash
# Redis Connection
LISTENERS_REDIS_HOST=your-redis-host.amazonaws.com
LISTENERS_REDIS_PORT=6379
LISTENERS_REDIS_PASSWORD=your-redis-password
LISTENERS_REDIS_TLS=true
LISTENERS_REDIS_DB=0

# Local Development
USE_LOCAL_REDIS=true  # Force localhost:6379
```

### Désactivation du Cache (Debug)

Pour désactiver temporairement le cache sans modifier le code:

**Option 1:** Ne pas passer `firebase_user_id` dans les appels RPC.

**Option 2:** Modifier temporairement les TTLs à 0 dans `redis_namespaces.py`:

```python
class RedisTTL:
    HR_EMPLOYEES = 0  # Cache désactivé
```

---

## 📞 Références

### Fichiers Principaux

- [`app/tools/hr_cache_manager.py`](../tools/hr_cache_manager.py) - Cache manager
- [`app/hr_rpc_handlers.py`](../hr_rpc_handlers.py) - Handlers RPC avec cache
- [`app/llm_service/redis_namespaces.py`](../llm_service/redis_namespaces.py) - Constantes TTL
- [`app/tools/neon_hr_manager.py`](../tools/neon_hr_manager.py) - Manager PostgreSQL

### Documentation Connexe

- [HR Module Integration (Frontend)](../../../../pinnokio_app/docs/hr/HR_MODULE_INTEGRATION.md)
- [Redis Cache Implementation (Frontend)](../../../../pinnokio_app/docs/architecture_devops/REDIS_CACHE_IMPLEMENTATION_FINAL.md)
- Backend Integration HR (firebase_microservice)

---

## 📈 Évolutions Futures

### Prévues

1. **Invalidation Multi-Utilisateurs**: Broadcaster via Redis Pub/Sub
2. **Métriques Prometheus**: Exposer HIT/MISS rate, latences
3. **Cache Warming**: Pré-charger le cache au login
4. **Cache Partiel**: Stratégies de pagination pour grandes listes

### À Considérer

- **Cache Hiérarchique**: Redis (L1) + PostgreSQL Read Replica (L2)
- **Compression**: Compresser les données en cache si > 1MB
- **Versioning**: Invalider automatiquement en cas de changement de schéma

---

*Documentation mise à jour le 16 Janvier 2026 - Intégration Redis Cache HR Backend v1.0*
