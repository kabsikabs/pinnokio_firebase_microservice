# Architecture de communication Firebase Microservice ↔ Reflex

## Vue d'ensemble de l'architecture

Cette documentation décrit l'architecture complète de communication entre l'application Reflex et le microservice Firebase, incluant les patterns de communication, les protocoles utilisés et les mécanismes d'extensibilité pour de nouveaux services.

### Architecture globale

```
┌─────────────────┐    RPC HTTP     ┌─────────────────┐    Firebase API    ┌─────────────────┐
│                 │◄───────────────►│                 │◄──────────────────►│                 │
│  Application    │                 │  Microservice   │                    │   Firebase      │
│     Reflex      │                 │   Firebase      │                    │  (Firestore +   │
│                 │                 │                 │                    │   Realtime DB)  │
└─────────────────┘                 └─────────────────┘                    └─────────────────┘
         ▲                                   │
         │ WebSocket/Redis                   │ Redis Pub/Sub
         │ (événements temps réel)           ▼
         │                          ┌─────────────────┐
         └─────────────────────────►│     Redis       │
                                    │  (Event Bus)    │
                                    └─────────────────┘
```

## Modes de fonctionnement

Le système supporte trois modes de déploiement pour permettre une transition progressive et un développement sécurisé.

### 1) Mode ACTUEL (ne rien casser)
- Source: mécanisme en place aujourd’hui (queue process interne / listeners intégrés au State Reflex).
- Action: ne changez rien. Ce mode reste par défaut tant que les tests local et prod ne sont pas validés.

### 2) Mode LOCAL (tests développeur)
- Source: Redis local (Docker) publié par `listeners-service` en local.
- Pré-requis côté dev:
  - Démarrer Redis local: `docker run -d --name redis-local -p 6379:6379 redis:alpine`
  - Démarrer le microservice listeners: `USE_LOCAL_REDIS=true uvicorn app.main:app --host 0.0.0.0 --port 8080`
  - Vérifier `GET http://localhost:8080/debug` → `redis: ok`
- Paramétrage côté backend Reflex (variables d’environnement):
  - `LISTENERS_REDIS_HOST=127.0.0.1`
  - `LISTENERS_REDIS_PORT=6379`
  - `LISTENERS_REDIS_PASSWORD=` (vide)
  - `LISTENERS_REDIS_TLS=false`
  - `LISTENERS_REDIS_DB=0`
  - `LISTENERS_CHANNEL_PREFIX=user:` (assurez-vous qu’il corresponde à celui du microservice)
- Résultat attendu: le backend Reflex s’abonne à `user:{uid}` sur le Redis local et reçoit les messages `notif.*`.

### 3) Mode PROD (ECS Fargate + ALB + ElastiCache Valkey)
- Source: Valkey Serverless (compatible Redis) dans AWS.
- Paramétrage côté backend Reflex (env prod):
  - `LISTENERS_REDIS_HOST=pinnokio-cache-7uum2j.serverless.use1.cache.amazonaws.com`
  - `LISTENERS_REDIS_PORT=6379`
  - `LISTENERS_REDIS_PASSWORD=` (vide, sécurité réseau via SG/VPC)
  - `LISTENERS_REDIS_TLS=true`
  - `LISTENERS_REDIS_DB=0`
  - `LISTENERS_CHANNEL_PREFIX=user:`
- Réseau: Le backend Reflex doit être dans le même VPC/subnets et Security Group autorisant le port 6379 vers Valkey.
- Vérification: une fois déployé, `GET https://<ALB>/healthz` du microservice doit être `ok`, et le backend Reflex doit recevoir les événements sur `user:{uid}`.

### 4) Commutation progressive des modes
- Étapes recommandées:
  1) Conserver le mode ACTUEL en production (aucun changement).
  2) Tester le mode LOCAL côté dev (Redis Docker + microservice local). Valider que l’UI reçoit `notif.*` via le backend Reflex.
  3) Déployer le microservice en PROD (ECS/ALB) et configurer le backend Reflex en mode PROD (Valkey). Tester sur un sous-ensemble d’utilisateurs.
  4) Après validation, retirer le mode ACTUEL et basculer définitivement sur Redis/Valkey.

### 5) Références et points de contrôle
- Préfixe de canal: `LISTENERS_CHANNEL_PREFIX` doit être identique côté microservice et backend Reflex.
- Aucun replay: Redis/Valkey diffuse seulement. Sur reconnexion, relire Firestore si besoin.
- Santé du microservice: `GET /healthz` et `GET /debug` (ALB en prod ou localhost en local).
- Sécurité (prod): accès Valkey par SG/VPC, TLS activé, pas de mot de passe.

---

## Protocoles de communication détaillés

### 1. Communication RPC HTTP (Reflex → Microservice)

#### Architecture RPC
- **Endpoint**: `POST /rpc`
- **Authentification**: Bearer token via `Authorization: Bearer <LISTENERS_SERVICE_TOKEN>`
- **Idempotence**: Clé SHA256 basée sur méthode, arguments et paramètres
- **Timeout**: Configurable par requête (défaut: 120s)

#### Format des requêtes RPC
```json
{
  "method": "FIREBASE_MANAGEMENT.add_or_update_job_by_job_id",
  "args": ["clients/user123/notifications", {"job_id": "abc123", ...}],
  "kwargs": {},
  "user_id": "user123",
  "session_id": "session456",
  "idempotency_key": "sha256_hash",
  "timeout_ms": 30000,
  "trace_id": "uuid4",
  "reply_to": "redis:reply:session456"
}
```

#### Format des réponses RPC
```json
// Succès
{
  "ok": true,
  "data": "result_or_object"
}

// Erreur
{
  "ok": false,
  "error": {
    "code": "INVALID_ARGS|INTERNAL|METHOD_NOT_FOUND",
    "message": "Description de l'erreur",
    "retry_after_ms": 5000  // optionnel
  }
}
```

#### Mapping des méthodes
Le microservice route automatiquement les appels RPC vers les bonnes implémentations :

- `FIREBASE_MANAGEMENT.*` → Classe `FirebaseManagement` (Firestore + Stripe)
- `FIREBASE_REALTIME.*` → Classe `FirebaseRealtimeChat` (Realtime Database)
- `REGISTRY.*` → Système de gestion des sessions utilisateur

#### Proxy côté Reflex
L'application Reflex utilise un système de proxy transparent :

```python
# En mode LOCAL, tous les appels Firebase sont automatiquement routés via RPC
firebase_service = FireBaseManagement()  # Proxy RPC automatique
firebase_service.add_or_update_job_by_job_id(path, data)  # → RPC call
```

### 2. Communication temps réel (Microservice → Reflex)

#### Architecture Event Bus
Le microservice publie les événements sur Redis, l'application Reflex les consomme via WebSocket et/ou Redis.

#### Canaux Redis
- **Notifications**: `user:{user_id}` (ex: `user:123abc`)
- **Messages directs**: `msg:{user_id}`
- **Chat**: `chat:{user_id}:{space_code}:{thread_key}`

#### Format des événements
```json
{
  "type": "notif.job_updated|notif.sync|msg.new|chat.message",
  "uid": "user_id",
  "timestamp": "2025-09-20T11:44:00.000Z",
  "payload": {
    "job_id": "abc123",
    "status": "completed",
    "collection_path": "clients/user123/notifications",
    // ... données spécifiques à l'événement
  }
}
```

#### Types d'événements
- `notif.job_updated`: Notification de job mise à jour
- `notif.sync`: Synchronisation complète des notifications
- `msg.new`: Nouveau message direct
- `msg.sync`: Synchronisation des messages
- `chat.message`: Message de chat
- `chat.sync`: Synchronisation du chat
- `workflow.invoice_update`: Mise à jour des données de facture
- `workflow.step_update`: Mise à jour des étapes de workflow APBookeeper

#### Événements Workflow détaillés

**Événement `workflow.invoice_update`** :
```json
{
  "type": "workflow.invoice_update",
  "uid": "user123",
  "job_id": "job456",
  "timestamp": "2025-09-20T12:30:00.000Z",
  "payload": {
    "invoice_changes": {
      "invoiceReference": "INV-2025-001",
      "totalAmountDueVATExcluded": 1250.00,
      "currency": "EUR"
    }
  }
}
```

**Événement `workflow.step_update`** :
```json
{
  "type": "workflow.step_update",
  "uid": "user123",
  "job_id": "job456",
  "timestamp": "2025-09-20T12:30:00.000Z",
  "payload": {
    "step_changes": {
      "APBookeeper_step_status": {
        "step_extract_data": 3,
        "step_validate_data": 1
      }
    }
  }
}
```

### 3. Surveillance des workflows (WorkflowListener)

#### Architecture du WorkflowListener
Le microservice surveille automatiquement les documents dans `clients/{user_id}/task_manager/` pour détecter :
- **Changements de données de facture** : Modifications dans `document.initial_data`
- **Progression des étapes** : Évolution des compteurs dans `APBookeeper_step_status`

#### Champs de facture surveillés
- Informations principales : `invoiceReference`, `recipient`, `invoiceDescription`
- Montants : `totalAmountDueVATExcluded`, `totalAmountDueVATIncluded`, `VATAmount`
- Détails : `recipientAddress`, `dueDate`, `sender`, `invoiceDate`, `currency`
- Métadonnées : `VATPercentages`, `sender_country`, `account_number`, `account_name`

#### Logique de détection des étapes
- Chaque étape APBookeeper a un compteur numérique qui s'incrémente
- Le microservice compare les valeurs actuelles avec le cache précédent
- Seules les étapes modifiées sont publiées dans l'événement

#### Configuration
```bash
# Variable d'environnement pour activer/désactiver
WORKFLOW_LISTENER_ENABLED=true
```

### 4. Gestion des sessions et registre utilisateur

#### Enregistrement de session
```python
# Au login Reflex
registry_register_user(user_id, session_id, backend_route)
```

#### Désenregistrement
```python
# Au logout Reflex
registry_unregister_session(session_id)
```

#### Heartbeat et présence
- Heartbeat automatique via WebSocket
- Mise à jour du statut utilisateur (`online`/`offline`)
- TTL de 90 secondes pour la présence

---

## Extensibilité et intégration de nouveaux services

### Pattern d'extension pour services externes

#### 1. Services synchrones (bases de données vectorielles, API externes)

Pour intégrer un nouveau service synchrone (ex: Pinecone, Weaviate, API REST) :

```python
# 1. Créer une nouvelle classe de service
class VectorDatabaseService:
    def search_vectors(self, query_vector, top_k=10):
        # Implémentation de recherche vectorielle
        pass

    def upsert_vectors(self, vectors):
        # Implémentation d'insertion
        pass

# 2. Enregistrer dans le dispatcher RPC (main.py)
def _resolve_method(method: str) -> Tuple[Callable[..., Any], str]:
    if method.startswith("VECTOR_DB."):
        name = method.split(".", 1)[1]
        target = getattr(get_vector_db_service(), name, None)
        if callable(target):
            return target, "VECTOR_DB"
    # ... autres services
```

#### 2. Services asynchrones (événements temps réel)

Pour un service générant des événements :

```python
# 1. Publier des événements via le système existant
def _publish_vector_event(self, user_id, event_data):
    payload = {
        "type": "vector.search_complete",
        "uid": user_id,
        "timestamp": datetime.now().isoformat(),
        "payload": event_data
    }

    # Utiliser le listeners_manager existant
    from app.main import listeners_manager
    if listeners_manager:
        listeners_manager.publish(user_id, payload)

# 2. Ajouter de nouveaux canaux si nécessaire
# Format: service:{user_id}:{specific_channel}
```

#### 3. Services de stockage alternatifs

Pour intégrer une base de données alternative :

```python
# 1. Créer l'interface de service
class PostgreSQLService:
    def execute_query(self, query, params=None):
        # Implémentation PostgreSQL
        pass

# 2. Ajouter au mapping RPC
# POSTGRESQL.execute_query → PostgreSQLService.execute_query

# 3. Configurer côté Reflex
# Le proxy RPC fonctionnera automatiquement
```

### Configuration pour nouveaux services

#### Variables d'environnement
```bash
# Service vectoriel
VECTOR_DB_URL=https://api.pinecone.io
VECTOR_DB_API_KEY=xxx

# Service PostgreSQL
POSTGRES_URL=postgresql://user:pass@host:5432/db

# Redis pour nouveaux canaux
VECTOR_CHANNEL_PREFIX=vector:
POSTGRES_CHANNEL_PREFIX=postgres:
```

#### Authentification et sécurité
- Réutiliser le système Bearer token existant
- Ajouter des clés d'API spécifiques dans les variables d'environnement
- Utiliser le même système d'idempotence pour éviter les doublons

### Patterns de communication recommandés

#### 1. Pour opérations CRUD simples
```python
# Utiliser le pattern RPC synchrone
result = rpc_call("NEW_SERVICE.create_record", args=[data])
```

#### 2. Pour opérations longues/asynchrones
```python
# 1. Déclencher via RPC
job_id = rpc_call("NEW_SERVICE.start_long_operation", args=[params])

# 2. Publier progression via événements
def publish_progress(user_id, job_id, progress):
    payload = {
        "type": "service.progress_update",
        "uid": user_id,
        "payload": {"job_id": job_id, "progress": progress}
    }
    listeners_manager.publish(user_id, payload)
```

#### 3. Pour intégrations temps réel
```python
# Stream d'événements continu
def stream_realtime_data(user_id):
    for event in realtime_source:
        payload = {
            "type": "stream.data_update",
            "uid": user_id,
            "payload": event
        }
        listeners_manager.publish(user_id, payload)
```

---

## Monitoring et observabilité

### Logs structurés
- `rpc_call`: Appels RPC entrants
- `rpc_ok/rpc_error`: Résultats des appels
- `publish`: Événements publiés sur Redis
- `ws_connect/ws_disconnect`: Connexions WebSocket

### Métriques recommandées
- Latence des appels RPC par méthode
- Taux d'erreur par service
- Nombre d'événements publiés par canal
- Connexions WebSocket actives

### Endpoints de santé
- `GET /healthz`: Santé globale du microservice + compteurs workflow listeners
- `GET /debug`: État détaillé (Redis, Firebase, workflow listeners, services externes)

### Monitoring spécifique aux workflow listeners

#### Endpoint /healthz
```json
{
  "status": "ok",
  "version": "1.0.0",
  "listeners_count": 5,
  "workflow_listeners_count": 3,
  "redis": "ok",
  "uptime_s": 3600
}
```

#### Endpoint /debug
```json
{
  "redis": {"status": "ok"},
  "firestore": {"status": "ok"},
  "workflow_listeners": {
    "status": "ok",
    "enabled": true,
    "active_count": 3,
    "users": ["user123", "user456", "user789"],
    "cache_entries": 12
  }
}
```

#### Logs de workflow
- `workflow_listener_start`: Démarrage du listener pour un utilisateur
- `workflow_listener_attached`: Listener attaché avec succès
- `workflow_invoice_changes`: Changements détectés dans les données de facture
- `workflow_step_changes`: Changements détectés dans les étapes APBookeeper
- `workflow_listener_error`: Erreurs du workflow listener

Cette architecture permet une extension facile vers de nouveaux services tout en maintenant la cohérence des patterns de communication et la fiabilité du système existant.
