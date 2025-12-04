# 📋 Changelog - Implémentation Scalabilité Redis
## Rapport de Modifications (Décembre 2024)

---

## 🎯 Contexte

Ce document trace les modifications apportées pour implémenter l'architecture stateless multi-instance décrite dans `REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md`.

**Objectif** : Permettre le scaling horizontal du microservice Firebase avec état externalisé dans Redis.

---

## ✅ Phase 1 : Externalisation État Session

### 1.1 Création `SessionStateManager`

**Fichier créé** : `app/llm_service/session_state_manager.py`

**Fonctionnalités implémentées** :

| Méthode | Description | Clé Redis |
|---------|-------------|-----------|
| `save_session_state()` | Sauvegarde complète état session | `session:{user_id}:{company_id}:state` |
| `load_session_state()` | Charge état depuis Redis | `session:{user_id}:{company_id}:state` |
| `update_session_state()` | Mise à jour partielle | `session:{user_id}:{company_id}:state` |
| `delete_session_state()` | Suppression session | `session:{user_id}:{company_id}:state` |
| `update_presence()` | Tracking présence utilisateur | `session:{user_id}:{company_id}:state` |
| `update_thread_activity()` | Dernière activité par thread | `session:{user_id}:{company_id}:state` |
| `update_jobs_data()` | Mise à jour jobs | `session:{user_id}:{company_id}:state` |
| `is_user_on_thread()` | Vérification présence cross-instance | `session:{user_id}:{company_id}:state` |
| `session_exists()` | Vérification existence | `session:{user_id}:{company_id}:state` |
| `extend_ttl()` | Prolongation TTL | `session:{user_id}:{company_id}:state` |
| `list_user_sessions()` | Liste sessions utilisateur | `session:{user_id}:*:state` |
| `get_session_stats()` | Statistiques globales | `session:*:state` |

**Configuration** :
- TTL par défaut : **2 heures** (7200 secondes)
- Sérialisation : JSON avec support types spéciaux (datetime, set)

**Code clé** :

```python
class SessionStateManager:
    DEFAULT_TTL = 7200  # 2 heures
    KEY_PREFIX = "session"
    
    def _build_key(self, user_id: str, company_id: str) -> str:
        return f"{self.KEY_PREFIX}:{user_id}:{company_id}:state"
```

---

### 1.2 Modification `LLMSession`

**Fichier modifié** : `app/llm_service/llm_manager.py`

**Modifications apportées** :

#### Import ajouté
```python
from .session_state_manager import SessionStateManager, get_session_state_manager
```

#### Attributs ajoutés dans `__init__`
```python
# ⭐ GESTIONNAIRE D'ÉTAT REDIS (scaling horizontal)
self._state_manager: SessionStateManager = get_session_state_manager()
self._state_loaded_from_redis: bool = False
```

#### Nouvelles méthodes ajoutées

| Méthode | Description |
|---------|-------------|
| `_try_restore_from_redis()` | Restaure état au démarrage si session existe |
| `_sync_to_redis()` | Synchronise état local vers Redis |

#### Méthodes modifiées

| Méthode | Modification |
|---------|--------------|
| `enter_chat()` | Ajout sync Redis via `_state_manager.update_presence()` |
| `switch_thread()` | Ajout sync Redis via `_state_manager.update_presence()` |
| `leave_chat()` | Ajout sync Redis via `_state_manager.update_presence()` |
| `is_user_on_specific_thread()` | Ajout paramètre `check_redis` pour lecture cross-instance |
| `initialize_session_data()` | Ajout appel `_sync_to_redis()` après chargement |

**Fonctionnement hybride** :
```
┌─────────────────────────────────────────────────────────────┐
│                   APPROCHE HYBRIDE                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  LECTURE:  Cache local → Redis (si local vide)             │
│  ÉCRITURE: Local + Redis (parallèle)                       │
│  REPRISE:  Redis → Local (au démarrage)                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ Phase 2 : Externalisation Chat History

### 2.1 Création `ChatHistoryManager`

**Fichier créé** : `app/llm_service/chat_history_manager.py`

**Fonctionnalités implémentées** :

| Méthode | Description | Clé Redis |
|---------|-------------|-----------|
| `save_chat_history()` | Sauvegarde historique complet | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `load_chat_history()` | Charge historique | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `get_messages()` | Récupère messages uniquement | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `append_message()` | Ajoute un message | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `append_messages_batch()` | Ajoute plusieurs messages | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `update_system_prompt()` | Met à jour system prompt | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `clear_messages()` | Vide messages (garde system prompt) | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `delete_chat_history()` | Supprime historique | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `get_message_count()` | Compte messages | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `chat_exists()` | Vérifie existence | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `update_status()` | Met à jour statut chat | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `update_metadata()` | Met à jour métadonnées | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `list_user_chats()` | Liste threads utilisateur | `chat:{user_id}:{company_id}:*:history` |
| `get_chat_stats()` | Statistiques utilisateur | `chat:{user_id}:{company_id}:*:history` |
| `estimate_token_count()` | Estimation tokens | `chat:{user_id}:{company_id}:{thread_key}:history` |

**Configuration** :
- TTL par défaut : **24 heures** (86400 secondes)
- Format stocké : JSON avec messages, system_prompt, metadata, status

**Structure stockée** :
```json
{
  "messages": [...],
  "system_prompt": "...",
  "metadata": {
    "chat_mode": "general_chat",
    "provider": "openai"
  },
  "status": "active",
  "message_count": 42,
  "updated_at": "2024-12-02T10:30:00Z",
  "version": "1.0"
}
```

---

### 2.2 Modification `PinnokioBrain`

**Fichier modifié** : `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py`

**Modifications apportées** :

#### Import ajouté
```python
from ...llm_service.chat_history_manager import get_chat_history_manager, ChatHistoryManager
```

#### Attributs ajoutés dans `__init__`
```python
# ⭐ ARCHITECTURE STATELESS (Multi-Instance Ready)
self._chat_history_manager: ChatHistoryManager = get_chat_history_manager()
self._redis_sync_enabled: bool = True
```

#### Méthodes modifiées

| Méthode | Modification |
|---------|--------------|
| `add_user_message()` | Ajout appel `_sync_history_to_redis()` |
| `add_assistant_message()` | Ajout appel `_sync_history_to_redis()` |
| `clear_chat_history()` | Ajout sync vers Redis via `_chat_history_manager.clear_messages()` |

#### Nouvelles méthodes ajoutées

| Méthode | Description |
|---------|-------------|
| `get_chat_history_from_redis()` | Lecture directe Redis (cross-instance) |
| `restore_history_from_redis()` | Restauration complète depuis Redis |
| `_sync_history_to_redis()` | Synchronisation locale → Redis |

**Synchronisation automatique** :
```
Chaque message (user/assistant) → _sync_history_to_redis() → Redis
```

---

## ✅ Phase 3 : Lock Distribué CronScheduler

### 3.1 Création `DistributedLock`

**Fichier modifié** : `app/cron_scheduler.py` (classe ajoutée en début de fichier)

**Fonctionnalités implémentées** :

| Méthode | Description | Pattern Redis |
|---------|-------------|---------------|
| `acquire()` | Acquiert lock (atomic SET NX EX) | `lock:cron:{task_id}` |
| `release()` | Libère lock (si détenu) | `lock:cron:{task_id}` |
| `extend()` | Prolonge TTL lock | `lock:cron:{task_id}` |
| `is_locked()` | Vérifie si verrouillé | `lock:cron:{task_id}` |

**Configuration** :
- TTL par défaut : **5 minutes** (300 secondes)
- Acquisition : Atomique via `SET NX EX`

**Fonctionnement** :
```
Instance 1: acquire("task_123") → ✅ Lock acquis
Instance 2: acquire("task_123") → ❌ Déjà verrouillé (skip)
Instance 1: _execute_task()
Instance 1: release("task_123") → 🔓 Lock libéré
```

---

### 3.2 Modification `CronScheduler`

**Fichier modifié** : `app/cron_scheduler.py`

**Attributs ajoutés dans `__init__`** :
```python
# ⭐ Multi-Instance: Lock distribué + ID unique par instance
self._lock = DistributedLock()
self._instance_id = f"cron_{uuid.uuid4().hex[:8]}"
```

**Méthode modifiée** : `_execute_task()`

```python
async def _execute_task(self, task_data: dict, triggered_at: datetime):
    task_id = task_data["task_id"]
    
    # ⭐ STEP 0: Acquérir le lock distribué
    if not self._lock.acquire(task_id, self._instance_id):
        logger.info(f"[CRON] ⏭️ Tâche ignorée (déjà en cours): {task_id}")
        return
    
    try:
        # ... exécution de la tâche ...
    finally:
        # ⭐ STEP 6: Libérer le lock
        self._lock.release(task_id, self._instance_id)
```

**Garanties** :
- ✅ Une seule instance exécute chaque tâche
- ✅ Lock libéré même en cas d'erreur (finally)
- ✅ TTL évite locks orphelins si crash

---

## ✅ Phase 4 : Documentation Namespaces

### 4.1 Création `redis_namespaces.py`

**Fichier créé** : `app/llm_service/redis_namespaces.py`

**Contenu** :

#### Classes de constantes

```python
class RedisNamespace:
    SESSION = "session"
    CHAT = "chat"
    CONTEXT = "context"
    CACHE = "cache"
    JOBS = "jobs"
    WS_BUFFER = "pending_ws_messages"
    LOCK = "lock"

class RedisTTL:
    SESSION = 7200       # 2h
    CHAT_HISTORY = 86400 # 24h
    CONTEXT = 3600       # 1h
    CACHE = 3600         # 1h
    JOBS = 3600          # 1h
    WS_BUFFER = 300      # 5min
    LOCK = 300           # 5min
```

#### Fonctions helpers

| Fonction | Format clé généré |
|----------|-------------------|
| `build_session_key()` | `session:{user_id}:{company_id}:state` |
| `build_chat_history_key()` | `chat:{user_id}:{company_id}:{thread_key}:history` |
| `build_ws_channel()` | `chat:{user_id}:{company_id}:{thread_key}` |
| `build_context_key()` | `context:{user_id}:{company_id}` |
| `build_cache_key()` | `cache:{user_id}:{company_id}:{data_type}:{sub_type}` |
| `build_jobs_key()` | `jobs:{user_id}:{company_id}:{department}` |
| `build_ws_buffer_key()` | `pending_ws_messages:{user_id}:{thread_key}` |
| `build_lock_key()` | `lock:{lock_type}:{resource_id}` |

---

### 4.2 Mise à jour `__init__.py`

**Fichier modifié** : `app/llm_service/__init__.py`

**Exports ajoutés** :
```python
from .session_state_manager import get_session_state_manager, SessionStateManager
from .chat_history_manager import get_chat_history_manager, ChatHistoryManager

__all__ = [
    'get_llm_manager', 
    'LLMManager', 
    'LLMContext',
    'get_session_state_manager',
    'SessionStateManager',
    'get_chat_history_manager',
    'ChatHistoryManager'
]
```

---

## 📊 Récapitulatif des Fichiers

### Fichiers Créés

| Fichier | Lignes | Description |
|---------|--------|-------------|
| `app/llm_service/session_state_manager.py` | ~450 | Gestionnaire état session Redis |
| `app/llm_service/chat_history_manager.py` | ~500 | Gestionnaire historique chat Redis |
| `app/llm_service/redis_namespaces.py` | ~150 | Constantes et helpers clés Redis |

### Fichiers Modifiés

| Fichier | Modifications | Impact |
|---------|---------------|--------|
| `app/llm_service/llm_manager.py` | +~120 lignes | LLMSession stateless |
| `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` | +~80 lignes | PinnokioBrain sync Redis |
| `app/cron_scheduler.py` | +~100 lignes | Lock distribué |
| `app/llm_service/__init__.py` | +15 lignes | Exports nouveaux modules |
| `REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md` | +50 lignes | Documentation implémentation |

---

## 🏗️ Architecture Redis Résultante

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         NAMESPACES REDIS                                   │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  session:{user_id}:{company_id}:state                    [TTL: 2h]       │
│  ├─ user_context                                                          │
│  ├─ jobs_data                                                             │
│  ├─ jobs_metrics                                                          │
│  ├─ is_on_chat_page                                                       │
│  ├─ current_active_thread                                                 │
│  ├─ thread_states                                                         │
│  ├─ active_tasks                                                          │
│  └─ intermediation_mode                                                   │
│                                                                           │
│  chat:{user_id}:{company_id}:{thread_key}:history        [TTL: 24h]      │
│  ├─ messages                                                              │
│  ├─ system_prompt                                                         │
│  ├─ metadata                                                              │
│  └─ status                                                                │
│                                                                           │
│  lock:cron:{task_id}                                     [TTL: 5min]     │
│  └─ instance_id (holder)                                                  │
│                                                                           │
│  context:{user_id}:{company_id}                          [TTL: 1h]       │
│  cache:{user_id}:{company_id}:{type}:{subtype}           [TTL: var]      │
│  jobs:{user_id}:{company_id}:{department}                [TTL: 30min]    │
│  pending_ws_messages:{user_id}:{thread_key}              [TTL: 5min]     │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Prochaines Étapes

### À Faire (Non Implémenté)

| Phase | Tâche | Priorité |
|-------|-------|----------|
| Phase 3 | Unifier `cache:*` et `jobs:*` → `data:*` | Moyenne |
| Phase 4 | Renommer `user:{uid}` → `events:{uid}` (Pub/Sub) | Basse |
| Phase 5 | Configuration ALB round-robin | Haute |
| Phase 5 | Tests de charge multi-instance | Haute |
| Phase 5 | Monitoring CloudWatch Redis | Moyenne |

### Tests Recommandés

1. **Test reprise session** : Arrêter une instance, vérifier reprise sur autre instance
2. **Test chat cross-instance** : Envoyer messages alternativement via 2 instances
3. **Test CRON multi-instance** : Vérifier qu'une tâche n'est exécutée qu'une fois
4. **Test performance** : Mesurer latence ajoutée par Redis (~10-30ms attendu)

---

## 📝 Notes Techniques

### Singletons

Tous les managers utilisent le pattern singleton :

```python
_session_state_manager: Optional[SessionStateManager] = None

def get_session_state_manager() -> SessionStateManager:
    global _session_state_manager
    if _session_state_manager is None:
        _session_state_manager = SessionStateManager()
    return _session_state_manager
```

### Lazy Loading Redis

Tous les managers utilisent le lazy loading pour le client Redis :

```python
@property
def redis(self):
    if self._redis is None:
        from ..redis_client import get_redis
        self._redis = get_redis()
    return self._redis
```

### Gestion des Erreurs

Toutes les opérations Redis sont encapsulées avec try/except pour éviter de bloquer le flux principal :

```python
try:
    self._state_manager.save_session_state(...)
except Exception as e:
    logger.warning(f"[SESSION_SYNC] ⚠️ Erreur sync Redis: {e}")
    # Continue sans bloquer
```

---

**Document généré le** : 2 Décembre 2024  
**Version** : 1.0  
**Auteur** : Migration automatique selon `REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md`

