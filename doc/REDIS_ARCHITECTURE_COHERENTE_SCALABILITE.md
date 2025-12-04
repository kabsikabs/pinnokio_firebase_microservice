# 🏗️ Architecture Redis Cohérente - Frontend & Backend
## Stratégie de Scalabilité Horizontale

---

## ✅ IMPLÉMENTATION RÉALISÉE (Décembre 2024)

### Modules Créés

| Module | Fichier | Description |
|--------|---------|-------------|
| **SessionStateManager** | `app/llm_service/session_state_manager.py` | Externalise l'état LLMSession dans Redis (session:*) |
| **ChatHistoryManager** | `app/llm_service/chat_history_manager.py` | Externalise l'historique chat dans Redis (chat:*:history) |
| **DistributedLock** | `app/cron_scheduler.py` | Lock Redis distribué pour CronScheduler (lock:cron:*) |
| **RedisNamespaces** | `app/llm_service/redis_namespaces.py` | Constantes et helpers pour les clés Redis |

### Modifications Effectuées

| Fichier | Modification |
|---------|--------------|
| `app/llm_service/llm_manager.py` | LLMSession utilise SessionStateManager pour état hybride (RAM + Redis) |
| `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` | PinnokioBrain utilise ChatHistoryManager pour sync historique |
| `app/cron_scheduler.py` | CronScheduler utilise DistributedLock pour éviter exécutions en double |
| `app/llm_service/__init__.py` | Export des nouveaux modules |

### Architecture Résultante

```
┌───────────────────────────────────────────────────────────────────────────┐
│                     ARCHITECTURE MULTI-INSTANCE READY                      │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     │
│  │   Instance 1    │     │   Instance 2    │     │   Instance N    │     │
│  │  (ECS Fargate)  │     │  (ECS Fargate)  │     │  (ECS Fargate)  │     │
│  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘     │
│           │                       │                       │               │
│           └───────────────────────┼───────────────────────┘               │
│                                   │                                       │
│                         ┌─────────▼─────────┐                             │
│                         │   REDIS CLOUD     │                             │
│                         │                   │                             │
│                         │ session:* (2h)    │  ← État session             │
│                         │ chat:*:history    │  ← Historique chat          │
│                         │ lock:cron:* (5m)  │  ← Locks distribués         │
│                         │ context:* (1h)    │  ← Contexte utilisateur     │
│                         │ cache:* (1h)      │  ← Cache données            │
│                         └───────────────────┘                             │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### Fonctionnement Hybride (Performance + Durabilité)

1. **Lecture**: Cache local d'abord, puis Redis
2. **Écriture**: Local + Redis en parallèle
3. **Reprise**: Restauration automatique depuis Redis si session existe
4. **Lock**: Acquisition atomique avec SET NX EX

---

## 📋 Table des Matières

1. [État Actuel de l'Architecture](#état-actuel-de-larchitecture)
2. [Problèmes de Scalabilité Identifiés](#problèmes-de-scalabilité-identifiés)
3. [Architecture Redis Cohérente Proposée](#architecture-redis-cohérente-proposée)
4. [Stratégie de Migration](#stratégie-de-migration)
5. [Plan de Scalabilité Horizontale](#plan-de-scalabilité-horizontale)
6. [Recommandations Opérationnelles](#recommandations-opérationnelles)

---

## État Actuel de l'Architecture

### 🗂️ Namespace Redis Actuels (Partagés Frontend/Backend)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        REDIS CLOUD (Valkey Serverless)                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ 📦 NAMESPACE 1: cache:* (Données métiers - Frontend)                   │
│    cache:{user_id}:{company_id}:bank:transactions         [TTL: 60min]│
│    cache:{user_id}:{company_id}:drive:documents           [TTL: 30min]│
│    cache:{user_id}:{company_id}:apbookeeper:documents     [TTL: 40min]│
│    cache:{user_id}:{company_id}:expenses:details          [TTL: 40min]│
│                                                                         │
│ 🧠 NAMESPACE 2: context:* (Contexte LLM - Backend)                     │
│    context:{user_id}:{collection_name}                    [TTL: 24h] │
│    └─ Contient: mandate_path, client_uuid, dms_system, etc.          │
│                                                                         │
│ 📊 NAMESPACE 3: jobs:* (Métriques jobs - Backend)                      │
│    jobs:{user_id}:{collection_name}:APBOOKEEPER          [TTL: 30min]│
│    jobs:{user_id}:{collection_name}:ROUTER               [TTL: 30min]│
│    jobs:{user_id}:{collection_name}:BANK                 [TTL: 30min]│
│                                                                         │
│ 🔐 NAMESPACE 4: registry:* (Registre unifié - Backend)                 │
│    registry:unified:{user_id}                            [TTL: 24h] │
│    registry:task:{task_id}                               [TTL: var] │
│    registry:company:{company_id}                         [TTL: 24h] │
│                                                                         │
│ 🎯 NAMESPACE 5: idemp:* (Idempotence RPC - Backend)                    │
│    idemp:{idempotency_key}                               [TTL: 15min]│
│                                                                         │
│ 📡 NAMESPACE 6: user:{uid} (Pub/Sub Listeners - Backend)               │
│    user:{user_id}                                        [Pub/Sub]   │
│    └─ Utilisé pour: notifications, messages, chat temps réel         │
│                                                                         │
│ 🔄 NAMESPACE 7: llm_init:* (Initialisation sessions LLM - Backend)     │
│    llm_init:{user_id}:{collection_name}                  [TTL: 5min] │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 📊 Mapping Usage par Application

| Namespace | Utilisé par | Accès | Nature | Scalabilité |
|-----------|-------------|-------|--------|-------------|
| `cache:*` | **Frontend Reflex** | R/W | Données métiers | ✅ Stateless |
| `context:*` | **Backend LLM** | R/W | Contexte utilisateur | ⚠️ Partiel |
| `jobs:*` | **Backend JobLoader** | R/W | Métriques jobs | ✅ Stateless |
| `registry:*` | **Backend Registre** | R/W | État sessions | ⚠️ Critique |
| `idemp:*` | **Backend RPC** | R/W | Déduplication | ✅ Stateless |
| `user:{uid}` | **Backend Pub/Sub** | Pub/Sub | Événements temps réel | ❌ **Bloquant** |
| `llm_init:*` | **Backend LLMManager** | R/W | Locks initialisation | ⚠️ Critique |

---

## Problèmes de Scalabilité Identifiés

### 🔴 **Problème #1 : État en Mémoire (LLMSession + PinnokioBrain)**

**Fichier** : `app/llm_service/llm_manager.py`

```python
class LLMSession:
    def __init__(self, session_key: str, context: LLMContext):
        self.session_key = session_key
        
        # ⚠️ PROBLÈME : État en mémoire RAM
        self.user_context: Optional[Dict] = None
        self.jobs_data: Optional[Dict] = None
        self.jobs_metrics: Optional[Dict] = None
        
        # ⚠️ PROBLÈME : Brains actifs en RAM (1 brain = 1 chat)
        self.active_brains: Dict[str, Any] = {}  # {thread_key: PinnokioBrain}
        
        # ⚠️ PROBLÈME : Historique chat en RAM
        # Chaque PinnokioBrain a son propre chat_history
```

**Impact Scalabilité** :
- ❌ **Impossible de distribuer sur plusieurs instances**
- ❌ **Perte de l'état si instance tombe**
- ❌ **Affinité session obligatoire (Sticky Sessions)**

### 🔴 **Problème #2 : Pub/Sub Redis Blocking Architecture**

**Fichier** : `app/listeners_manager.py`

```python
class ListenersManager:
    def start(self):
        # ⚠️ PROBLÈME : Chaque instance écoute TOUS les channels user:{uid}
        # Si 10 instances → 10x listeners pour le même user
        
        # ⚠️ PROBLÈME : Pas de partitionnement par user
        # Une instance ne peut pas déléguer à une autre
```

**Impact Scalabilité** :
- ⚠️ **Broadcasting redondant** (chaque instance reçoit tous les messages)
- ⚠️ **Pas de load balancing** intelligent par user
- ⚠️ **WebSocket tied à une instance** (pas de failover)

### 🟡 **Problème #3 : Duplication des Données (Frontend/Backend)**

**Situation Actuelle** :

```
FRONTEND (Reflex)                    BACKEND (Microservice)
┌─────────────────────┐              ┌─────────────────────┐
│ cache:{uid}:{cid}:  │              │ jobs:{uid}:{cid}:   │
│   apbookeeper       │◄─────────────┤   APBOOKEEPER       │
│   documents         │   DOUBLON?   │                     │
└─────────────────────┘              └─────────────────────┘
```

**Questions** :
- ❓ Les données `cache:*` (frontend) et `jobs:*` (backend) sont-elles redondantes ?
- ❓ Le backend devrait-il lire `cache:*` ou maintenir `jobs:*` ?
- ❓ Qui est la source de vérité ?

### 🟡 **Problème #4 : TTL Incohérents**

| Namespace | TTL | Justification |
|-----------|-----|---------------|
| `cache:*:bank:transactions` | 60 min | Frontend - Données ERP stables |
| `cache:*:drive:documents` | 30 min | Frontend - Drive volatile |
| `context:*` | 24h | Backend - Métadonnées company |
| `jobs:*` | 30 min | Backend - Métriques jobs |

**Problème** : Pas de stratégie cohérente de rafraîchissement entre frontend/backend.

---

## Architecture Redis Cohérente Proposée

### 🎯 Objectifs

1. **Scalabilité Horizontale** : Permettre 2-10 instances backend sans affinité
2. **État Externalisé** : Aucun état critique en RAM
3. **Réduction Duplication** : Unifier les namespaces frontend/backend
4. **Stratégie TTL Cohérente** : Définir des règles claires de rafraîchissement

### 📐 Nouvelle Structure Redis

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    REDIS CLOUD (Source de Vérité Unique)                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ 🗂️ TIER 1: SESSION STATE (État utilisateur externalisé)                │
│    session:{user_id}:{company_id}:state                  [TTL: 2h]    │
│    ├─ user_context (mandate_path, client_uuid, dms_system)            │
│    ├─ jobs_data (factures, documents, transactions)                   │
│    ├─ jobs_metrics (compteurs pour system prompt)                     │
│    └─ active_threads (threads de chat actifs)                         │
│                                                                         │
│ 💬 TIER 2: CHAT HISTORY (Conversations LLM externalisées)              │
│    chat:{user_id}:{company_id}:{thread_key}:history      [TTL: 24h]  │
│    ├─ messages (utilisateur + assistant + tool_results)               │
│    ├─ system_prompt (avec résumés éventuels)                          │
│    ├─ metadata (created_at, last_activity, mode)                      │
│    └─ status (active, idle, terminated)                               │
│                                                                         │
│ 📦 TIER 3: BUSINESS DATA CACHE (Données métiers - Partagé)            │
│    data:{user_id}:{company_id}:bank:transactions        [TTL: 60min] │
│    data:{user_id}:{company_id}:drive:documents          [TTL: 30min] │
│    data:{user_id}:{company_id}:apbookeeper:documents    [TTL: 40min] │
│    data:{user_id}:{company_id}:expenses:details         [TTL: 40min] │
│    └─ ✅ Utilisé par Frontend ET Backend (source unique)              │
│                                                                         │
│ 🔐 TIER 4: INFRASTRUCTURE (Registres et coordination)                  │
│    registry:user:{user_id}                               [TTL: 24h]  │
│    registry:task:{task_id}                               [TTL: var]  │
│    idemp:{key}                                          [TTL: 15min] │
│    llm_init:{user_id}:{company_id}                      [TTL: 5min]  │
│                                                                         │
│ 📡 TIER 5: REAL-TIME EVENTS (Pub/Sub avec routing)                     │
│    events:{user_id}                                     [Pub/Sub]    │
│    ├─ notifications (jobs terminés, erreurs)                          │
│    ├─ chat_updates (nouveaux messages)                                │
│    └─ state_sync (synchronisation multi-onglets)                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 🔄 Mapping Migration

| Ancien Namespace | Nouveau Namespace | Changement |
|------------------|-------------------|------------|
| `cache:{uid}:{cid}:*` | `data:{uid}:{cid}:*` | Renommage (+ partagé backend) |
| `context:{uid}:{cid}` | `session:{uid}:{cid}:state` | Intégration session |
| `jobs:{uid}:{cid}:*` | **SUPPRIMÉ** | Utilise `data:*` directement |
| `user:{uid}` (Pub/Sub) | `events:{uid}` | Renommage (routing amélioré) |
| **NOUVEAU** | `chat:{uid}:{cid}:{thread}:history` | Externalisation chat history |

---

## Stratégie de Migration

### 📅 Phase 1 : Externalisation État Session (Semaine 1-2)

**Objectif** : Permettre scaling horizontal basique avec sticky sessions

#### 1.1 Créer `SessionStateManager`

**Nouveau fichier** : `app/state_manager.py`

```python
class SessionStateManager:
    """Gestionnaire d'état session externalisé dans Redis."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def save_session_state(
        self, 
        user_id: str, 
        company_id: str,
        state: Dict
    ):
        """Sauvegarde l'état complet d'une session."""
        key = f"session:{user_id}:{company_id}:state"
        
        payload = {
            "user_context": state.get("user_context"),
            "jobs_data": state.get("jobs_data"),
            "jobs_metrics": state.get("jobs_metrics"),
            "active_threads": state.get("active_threads", []),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        await self.redis.setex(
            key,
            7200,  # TTL 2h
            json.dumps(payload)
        )
    
    async def load_session_state(
        self, 
        user_id: str, 
        company_id: str
    ) -> Optional[Dict]:
        """Charge l'état session depuis Redis."""
        key = f"session:{user_id}:{company_id}:state"
        data = await self.redis.get(key)
        
        if data:
            return json.loads(data)
        return None
    
    async def update_heartbeat(
        self, 
        user_id: str, 
        company_id: str
    ):
        """Met à jour le heartbeat session (prolonge TTL)."""
        key = f"session:{user_id}:{company_id}:state"
        await self.redis.expire(key, 7200)
```

#### 1.2 Modifier `LLMSession` pour Utiliser Redis

**Fichier** : `app/llm_service/llm_manager.py`

```python
class LLMSession:
    def __init__(self, session_key: str, context: LLMContext):
        self.session_key = session_key
        self.context = context
        
        # ✅ NOUVEAU : Gestionnaire d'état externalisé
        self.state_manager = SessionStateManager(get_redis())
        
        # ❌ SUPPRIMÉ : État en mémoire
        # self.user_context: Optional[Dict] = None
        # self.jobs_data: Optional[Dict] = None
        # self.jobs_metrics: Optional[Dict] = None
        
        # ⚠️ CONSERVÉ TEMPORAIREMENT : Brains (Phase 2)
        self.active_brains: Dict[str, Any] = {}
    
    async def get_user_context(self) -> Dict:
        """Charge user_context depuis Redis."""
        state = await self.state_manager.load_session_state(
            self.context.user_id,
            self.context.collection_name
        )
        return state.get("user_context") if state else {}
    
    async def update_jobs_data(self, jobs_data: Dict):
        """Met à jour jobs_data dans Redis."""
        state = await self.state_manager.load_session_state(
            self.context.user_id,
            self.context.collection_name
        ) or {}
        
        state["jobs_data"] = jobs_data
        
        await self.state_manager.save_session_state(
            self.context.user_id,
            self.context.collection_name,
            state
        )
```

**Impact** :
- ✅ État session persiste entre instances
- ✅ Perte d'instance = récupération automatique
- ⚠️ Latence additionnelle : ~10-20ms par accès Redis

---

### 📅 Phase 2 : Externalisation Chat History (Semaine 3-4)

**Objectif** : Permettre rotation d'instance pendant conversation active

#### 2.1 Créer `ChatHistoryManager`

**Nouveau fichier** : `app/chat_history_manager.py`

```python
class ChatHistoryManager:
    """Gestionnaire d'historique chat externalisé dans Redis."""
    
    async def save_chat_history(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        messages: List[Dict],
        system_prompt: str,
        metadata: Dict
    ):
        """Sauvegarde l'historique complet d'un thread."""
        key = f"chat:{user_id}:{company_id}:{thread_key}:history"
        
        payload = {
            "messages": messages,
            "system_prompt": system_prompt,
            "metadata": metadata,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        await self.redis.setex(
            key,
            86400,  # TTL 24h
            json.dumps(payload)
        )
    
    async def load_chat_history(
        self,
        user_id: str,
        company_id: str,
        thread_key: str
    ) -> Optional[Dict]:
        """Charge l'historique d'un thread depuis Redis."""
        key = f"chat:{user_id}:{company_id}:{thread_key}:history"
        data = await self.redis.get(key)
        
        if data:
            return json.loads(data)
        return None
    
    async def append_message(
        self,
        user_id: str,
        company_id: str,
        thread_key: str,
        message: Dict
    ):
        """Ajoute un message à l'historique (atomique)."""
        history = await self.load_chat_history(user_id, company_id, thread_key) or {
            "messages": [],
            "metadata": {}
        }
        
        history["messages"].append(message)
        history["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        await self.save_chat_history(
            user_id, company_id, thread_key,
            history["messages"],
            history.get("system_prompt", ""),
            history.get("metadata", {})
        )
```

#### 2.2 Modifier `PinnokioBrain` pour Utiliser Redis

**Fichier** : `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py`

```python
class PinnokioBrain:
    def __init__(self, collection_name: str, firebase_user_id: str, ...):
        self.collection_name = collection_name
        self.firebase_user_id = firebase_user_id
        
        # ✅ NOUVEAU : Gestionnaire d'historique externalisé
        self.chat_history_manager = ChatHistoryManager(get_redis())
        
        # ❌ SUPPRIMÉ : Historique en mémoire
        # self.pinnokio_agent.chat_history = {...}
    
    async def add_user_message(self, content: str, thread_key: str):
        """Ajoute un message utilisateur (sauvegarde Redis)."""
        message = {
            "role": "user",
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await self.chat_history_manager.append_message(
            self.firebase_user_id,
            self.collection_name,
            thread_key,
            message
        )
    
    async def get_chat_history(self, thread_key: str) -> List[Dict]:
        """Récupère l'historique depuis Redis."""
        history = await self.chat_history_manager.load_chat_history(
            self.firebase_user_id,
            self.collection_name,
            thread_key
        )
        return history.get("messages", []) if history else []
```

**Impact** :
- ✅ Conversation persiste entre instances
- ✅ Scaling horizontal complet (sans sticky sessions)
- ⚠️ Latence additionnelle : ~15-30ms par message

---

### 📅 Phase 3 : Unification Namespace Data (Semaine 5)

**Objectif** : Éliminer la duplication `cache:*` vs `jobs:*`

#### 3.1 Renommer `cache:*` → `data:*`

**Frontend** : `pinnokio_app/code/tools/redis_cache_manager.py`

```python
class PinnokioCacheManager:
    def _build_cache_key(self, user_id, company_id, data_type, sub_type=None):
        # ✅ NOUVEAU : Namespace unifié
        key = f"data:{user_id}:{company_id}:{data_type}"
        if sub_type:
            key += f":{sub_type}"
        return key
```

#### 3.2 Backend Lit `data:*` Directement

**Fichier** : `app/pinnokio_agentic_workflow/tools/job_loader.py`

```python
class JobLoader:
    async def _get_from_cache(self, department):
        """Lit depuis le cache UNIFIÉ (data:*)."""
        mapping = {
            "APBOOKEEPER": "apbookeeper:documents",
            "ROUTER": "drive:documents",
            "BANK": "bank:transactions"
        }
        
        data_type = mapping.get(department)
        # ✅ Utilise le même namespace que le frontend
        cache_key = f"data:{self.user_id}:{self.company_id}:{data_type}"
        
        cached_data = await self.redis.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
```

**Impact** :
- ✅ Élimination duplication `cache:*` / `jobs:*`
- ✅ Frontend et Backend partagent la même source
- ✅ Réduction mémoire Redis (~30-40%)

---

### 📅 Phase 4 : Amélioration Pub/Sub (Semaine 6)

**Objectif** : Router intelligent pour événements temps réel

#### 4.1 Nouveau Pattern : `events:{user_id}` avec Routing

**Backend** : `app/listeners_manager.py`

```python
class ListenersManager:
    async def publish_event(
        self, 
        user_id: str, 
        event_type: str, 
        payload: Dict
    ):
        """Publie un événement avec routing intelligent."""
        channel = f"events:{user_id}"
        
        message = {
            "type": event_type,  # "notification", "chat_update", "state_sync"
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await self.redis.publish(channel, json.dumps(message))
    
    async def subscribe_user_events(self, user_id: str):
        """Écoute UNIQUEMENT les événements d'un user."""
        channel = f"events:{user_id}"
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                event = json.loads(message["data"])
                await self._handle_event(user_id, event)
```

**Impact** :
- ✅ Routing événements par type
- ✅ Pas de broadcasting redondant
- ✅ Préparation multi-instance (chaque instance écoute ses users)

---

## Plan de Scalabilité Horizontale

### 🎯 Architecture Cible

```
┌─────────────────────────────────────────────────────────────────┐
│                     ALB (Round Robin)                           │
│               Session Affinity: OPTIONNEL                       │
└───────────────┬─────────────────────┬───────────────────────────┘
                │                     │
    ┌───────────▼───────────┐  ┌──────▼─────────────────┐
    │ firebase_microservice │  │ firebase_microservice  │
    │      Instance 1       │  │      Instance 2        │
    │      (Stateless)      │  │      (Stateless)       │
    └───────────┬───────────┘  └───────────┬────────────┘
                │                          │
                └──────────┬───────────────┘
                           ▼
              ┌────────────────────────────────┐
              │      Redis (ElastiCache)       │
              │  - session:* (état session)    │
              │  - chat:* (historique)         │
              │  - data:* (business cache)     │
              │  - events:* (pub/sub routing)  │
              └────────────────────────────────┘
```

### 📊 Modes de Déploiement

#### **Mode 1 : Sticky Sessions (Phase 1-2)**

**Configuration ALB** :
```yaml
stickiness:
  enabled: true
  type: lb_cookie
  duration: 7200  # 2 heures
```

**Avantages** :
- ✅ Migration progressive
- ✅ Performances (moins d'accès Redis)
- ✅ Compatibilité avec code existant

**Inconvénients** :
- ⚠️ Distribution non optimale
- ⚠️ Perte de session si instance tombe

#### **Mode 2 : Round Robin Complet (Phase 3-4)**

**Configuration ALB** :
```yaml
stickiness:
  enabled: false
routing:
  algorithm: round_robin
```

**Avantages** :
- ✅ Distribution parfaite
- ✅ Résilience complète
- ✅ Scaling horizontal illimité

**Inconvénients** :
- ⚠️ Latence Redis à chaque requête
- ⚠️ Coût Redis plus élevé

---

## Recommandations Opérationnelles

### 🔧 Configuration Redis Optimale

**Fichier** : `.env` / AWS Parameter Store

```bash
# Redis Configuration
REDIS_HOST=your-elasticache-endpoint.use1.cache.amazonaws.com
REDIS_PORT=6379
REDIS_PASSWORD=your-strong-password
REDIS_TLS=true
REDIS_DB=0

# Performance Tuning
REDIS_POOL_SIZE=50              # Pool de connexions
REDIS_SOCKET_TIMEOUT=5          # Timeout socket
REDIS_SOCKET_CONNECT_TIMEOUT=5  # Timeout connexion
REDIS_RETRY_ON_TIMEOUT=true     # Retry automatique

# Cache Strategy
SESSION_STATE_TTL=7200          # 2h pour état session
CHAT_HISTORY_TTL=86400          # 24h pour historique chat
DATA_CACHE_TTL_BANK=3600        # 1h pour transactions bancaires
DATA_CACHE_TTL_DRIVE=1800       # 30min pour documents Drive
DATA_CACHE_TTL_AP=2400          # 40min pour factures AP
```

### 📊 Monitoring Redis

**Métriques Critiques** :

```python
# app/monitoring/redis_metrics.py

class RedisMetrics:
    """Système de monitoring Redis."""
    
    async def get_key_distribution(self) -> Dict:
        """Distribution des clés par namespace."""
        patterns = [
            "session:*",
            "chat:*",
            "data:*",
            "events:*",
            "registry:*"
        ]
        
        distribution = {}
        for pattern in patterns:
            cursor = 0
            count = 0
            
            while True:
                cursor, keys = await self.redis.scan(
                    cursor=cursor,
                    match=pattern,
                    count=1000
                )
                count += len(keys)
                if cursor == 0:
                    break
            
            namespace = pattern.split(":")[0]
            distribution[namespace] = count
        
        return distribution
    
    async def get_memory_usage(self) -> Dict:
        """Utilisation mémoire par namespace."""
        info = await self.redis.info("memory")
        
        return {
            "used_memory_human": info["used_memory_human"],
            "used_memory_peak_human": info["used_memory_peak_human"],
            "maxmemory_human": info.get("maxmemory_human", "unlimited"),
            "mem_fragmentation_ratio": info["mem_fragmentation_ratio"]
        }
    
    async def get_hit_ratio(self) -> float:
        """Ratio cache hit/miss."""
        info = await self.redis.info("stats")
        
        hits = int(info.get("keyspace_hits", 0))
        misses = int(info.get("keyspace_misses", 0))
        
        if hits + misses == 0:
            return 0.0
        
        return hits / (hits + misses)
```

### 🚨 Alertes CloudWatch

**Métriques à Surveiller** :

| Métrique | Seuil | Action |
|----------|-------|--------|
| `CPUUtilization` | > 70% | Scale up |
| `DatabaseMemoryUsagePercentage` | > 80% | Augmenter cache size |
| `CacheHitRate` | < 70% | Revoir stratégie TTL |
| `EngineCPUUtilization` | > 90% | Critique - intervention |
| `NetworkBytesIn/Out` | Anomalie | Vérifier pub/sub |

### 🧪 Tests de Charge

**Scénarios à Tester** :

```python
# tests/load_testing/redis_load_test.py

async def test_concurrent_sessions():
    """Test 100 sessions utilisateur concurrentes."""
    tasks = []
    
    for i in range(100):
        user_id = f"user_{i}"
        company_id = f"company_{i % 10}"  # 10 sociétés
        
        task = simulate_user_session(user_id, company_id)
        tasks.append(task)
    
    results = await asyncio.gather(*tasks)
    
    # Vérifier :
    # - Temps de réponse < 100ms
    # - Aucune perte de données
    # - Hit ratio > 70%

async def test_failover():
    """Test basculement entre instances."""
    # 1. Créer session sur instance 1
    session = await create_session_instance_1()
    
    # 2. Simuler crash instance 1
    await shutdown_instance_1()
    
    # 3. Reprendre session sur instance 2
    session_recovered = await resume_session_instance_2()
    
    # Vérifier :
    # - État session identique
    # - Historique chat préservé
    # - Aucune perte de données
```

---

## 📚 Checklist de Migration

### ✅ Phase 1 : Externalisation Session (Semaines 1-2)

- [ ] Créer `SessionStateManager` (`app/state_manager.py`)
- [ ] Modifier `LLMSession` pour utiliser Redis
- [ ] Tests unitaires état session
- [ ] Tests intégration frontend/backend
- [ ] Déploiement staging avec sticky sessions
- [ ] Validation performance (< 20ms overhead)
- [ ] Monitoring métriques Redis

### ✅ Phase 2 : Externalisation Chat History (Semaines 3-4)

- [ ] Créer `ChatHistoryManager` (`app/chat_history_manager.py`)
- [ ] Modifier `PinnokioBrain` pour utiliser Redis
- [ ] Tests conversation multi-instance
- [ ] Migration données RTDB → Redis (historique)
- [ ] Tests failover instance
- [ ] Validation latence messages (< 30ms)

### ✅ Phase 3 : Unification Namespace (Semaine 5)

- [ ] Renommer `cache:*` → `data:*` (frontend)
- [ ] Modifier backend pour lire `data:*`
- [ ] Supprimer namespace `jobs:*` (legacy)
- [ ] Tests compatibilité frontend/backend
- [ ] Migration données Redis
- [ ] Validation réduction mémoire

### ✅ Phase 4 : Amélioration Pub/Sub (Semaine 6)

- [ ] Créer routing `events:{user_id}`
- [ ] Migrer `user:{uid}` → `events:{uid}`
- [ ] Tests multi-instance pub/sub
- [ ] Monitoring événements temps réel
- [ ] Validation latence notifications

### ✅ Phase 5 : Production (Semaine 7)

- [ ] Configuration ALB round-robin
- [ ] Auto-scaling backend (2-10 instances)
- [ ] Monitoring complet Redis
- [ ] Tests de charge 100+ users
- [ ] Documentation opérationnelle
- [ ] Runbook incidents

---

## 🎯 Résumé

### État Actuel

```
┌───────────────────────────────────────────────────────┐
│ ❌ État en mémoire (LLMSession, PinnokioBrain)       │
│ ❌ Scaling horizontal impossible                      │
│ ⚠️ Duplication données (cache:* vs jobs:*)          │
│ ⚠️ Pub/Sub broadcasting redondant                   │
└───────────────────────────────────────────────────────┘
```

### Architecture Cible

```
┌───────────────────────────────────────────────────────┐
│ ✅ État externalisé dans Redis                       │
│ ✅ Scaling horizontal 2-10 instances                 │
│ ✅ Namespace unifié (data:*)                         │
│ ✅ Pub/Sub avec routing intelligent                  │
│ ✅ Résilience complète (failover automatique)       │
└───────────────────────────────────────────────────────┘
```

### ROI Attendu

| Métrique | Avant | Après | Amélioration |
|----------|-------|-------|--------------|
| **Scaling** | Vertical uniquement | Horizontal 2-10x | **10x capacité** |
| **Résilience** | ❌ Perte état si crash | ✅ Récupération auto | **100%** |
| **Latence** | ~50ms (local) | ~80ms (Redis) | **+60% acceptable** |
| **Coût Redis** | $50/mois | $150/mois | **+200% justifié** |
| **Coût ECS** | 1 instance (2 vCPU, 4GB) | 2-10 instances (auto-scale) | **Variable selon charge** |

---

**🎯 Conclusion : L'architecture Redis actuelle nécessite une refonte significative pour permettre un vrai scaling horizontal. La migration proposée en 5 phases permet une transition progressive tout en préservant la stabilité du service.**

