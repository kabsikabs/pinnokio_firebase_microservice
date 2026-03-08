# Job Actions Centralized Handler

## Vue d'ensemble

Ce document décrit l'architecture centralisée pour la gestion des actions de jobs (process, stop, restart, delete) dans le système Pinnokio. Cette architecture unifie le traitement des jobs pour tous les départements (Router, APbookeeper, Bankbookeeper) tout en maintenant une communication cohérente avec le frontend via WebSocket.

---

## Table des matières

1. [Architecture générale](#architecture-générale)
2. [Fichiers créés/modifiés](#fichiers-créésmodifiés)
3. [Flux de données](#flux-de-données)
4. [API des handlers centralisés](#api-des-handlers-centralisés)
5. [Pattern pour applications externes (Jobbeurs)](#pattern-pour-applications-externes-jobbeurs)
6. [Événements WebSocket](#événements-websocket)
7. [Logs et traçabilité](#logs-et-traçabilité)
8. [Guide d'intégration](#guide-dintégration)

---

## Architecture générale

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (Next.js)                              │
│                                                                              │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────┐  │
│  │ use-routing-        │    │ use-invoices-       │    │ use-banking-    │  │
│  │ orchestration.ts    │    │ orchestration.ts    │    │ orchestration.ts│  │
│  └─────────┬───────────┘    └─────────┬───────────┘    └────────┬────────┘  │
│            │                          │                          │           │
│            └──────────────────────────┼──────────────────────────┘           │
│                                       │                                      │
│                              WebSocket Events                                │
│                     (routing.process, routing.stop, etc.)                    │
└───────────────────────────────────────┼──────────────────────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                           BACKEND (FastAPI)                                   │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                            main.py (WebSocket Router)                    │  │
│  │                                                                          │  │
│  │  routing.process  ──► handle_routing_process()                          │  │
│  │  routing.stop     ──► handle_routing_stop()                             │  │
│  │  routing.restart  ──► handle_routing_restart()                          │  │
│  │  routing.delete   ──► handle_routing_delete()                           │  │
│  └─────────────────────────────────────┬───────────────────────────────────┘  │
│                                        │                                      │
│  ┌─────────────────────────────────────▼───────────────────────────────────┐  │
│  │              routing/orchestration.py (Page Handlers)                    │  │
│  │                                                                          │  │
│  │  - Valide les payloads                                                  │  │
│  │  - Récupère le contexte company                                         │  │
│  │  - Appelle job_actions_handler                                          │  │
│  │  - Broadcast les résultats via WebSocket                                │  │
│  └─────────────────────────────────────┬───────────────────────────────────┘  │
│                                        │                                      │
│  ┌─────────────────────────────────────▼───────────────────────────────────┐  │
│  │              wrappers/job_actions_handler.py (Centralized)               │  │
│  │                                                                          │  │
│  │  handle_job_process()  ──► HTTP call to Jobbeur                         │  │
│  │  handle_job_stop()     ──► HTTP call to Jobbeur                         │  │
│  │  handle_job_restart()  ──► Cleanup Chroma + Firebase                    │  │
│  │  handle_job_delete()   ──► Full workflow (notifications, cache, Drive)  │  │
│  │                                                                          │  │
│  │  create_and_publish_notification()  ──► Firebase + WebSocket            │  │
│  └─────────────────────────────────────┬───────────────────────────────────┘  │
│                                        │                                      │
└────────────────────────────────────────┼──────────────────────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
           ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
           │    ROUTER     │    │  APBOOKEEPER  │    │ BANKBOOKEEPER │
           │  (Port 8080)  │    │  (Port 8081)  │    │  (Port 8082)  │
           │               │    │               │    │               │
           │ /event-trigger│    │ /apbookeeper- │    │ /banker-      │
           │ /stop_router  │    │  event-trigger│    │  event-trigger│
           │               │    │ /stop_apbook- │    │ /stop_banker  │
           │               │    │  keeper       │    │               │
           └───────┬───────┘    └───────┬───────┘    └───────┬───────┘
                   │                    │                    │
                   └────────────────────┼────────────────────┘
                                        │
                                        ▼
                              ┌───────────────────┐
                              │  LPT CALLBACK     │
                              │  POST /lpt/callback│
                              │                   │
                              │  Retour des       │
                              │  résultats de     │
                              │  traitement       │
                              └───────────────────┘
```

---

## Fichiers créés/modifiés

### Nouveaux fichiers

| Fichier | Description |
|---------|-------------|
| `app/wrappers/job_actions_handler.py` | Handler centralisé pour toutes les actions de jobs |

### Fichiers modifiés

| Fichier | Modifications |
|---------|---------------|
| `app/ws_events.py` | Ajout des événements STOP, STOPPED, DELETE, DELETED, UPLOAD, UPLOADED, PROCESSING_STARTED pour RoutingEvents |
| `app/frontend/pages/routing/orchestration.py` | Ajout de handle_routing_stop(), handle_routing_delete(), mise à jour de handle_routing_process() et handle_routing_restart() pour utiliser le handler centralisé |
| `app/frontend/pages/routing/__init__.py` | Export des nouveaux handlers |
| `app/frontend/pages/invoices/orchestration.py` | Mise à jour de handle_invoices_stop(), handle_invoices_delete(), handle_invoices_restart() pour utiliser le handler centralisé |
| `app/main.py` | Ajout des routes WebSocket pour routing.stop et routing.delete |

---

## Flux de données

### 1. Flux PROCESS (Optimiste)

```
Frontend                    Backend                         Jobbeur
   │                           │                               │
   │  routing.process          │                               │
   │  {document_ids, ...}      │                               │
   │ ─────────────────────────►│                               │
   │                           │                               │
   │                           │  POST /event-trigger          │
   │                           │  {collection_name, ...}       │
   │                           │ ─────────────────────────────►│
   │                           │                               │
   │                           │  HTTP 202 {job_id}            │
   │                           │◄───────────────────────────── │
   │                           │                               │
   │                           │  Create Firebase notification │
   │                           │  Publish via WebSocket        │
   │                           │                               │
   │  routing.processing_started                               │
   │  {job_id, document_ids}   │                               │
   │◄───────────────────────── │                               │
   │                           │                               │
   │  routing.processed        │                               │
   │  {success: true, job_id}  │                               │
   │◄───────────────────────── │                               │
   │                           │                               │
   │                           │         ... processing ...    │
   │                           │                               │
   │                           │  POST /lpt/callback           │
   │                           │  {status: completed, ...}     │
   │                           │◄───────────────────────────── │
   │                           │                               │
   │  notification.new         │                               │
   │  {type: completed, ...}   │                               │
   │◄───────────────────────── │                               │
```

### 2. Flux STOP (Pessimiste)

```
Frontend                    Backend                         Jobbeur
   │                           │                               │
   │  routing.stop             │                               │
   │  {job_ids}                │                               │
   │ ─────────────────────────►│                               │
   │                           │                               │
   │                           │  POST /stop_router            │
   │                           │  {user_id, job_ids}           │
   │                           │ ─────────────────────────────►│
   │                           │                               │
   │                           │  HTTP 200                     │
   │                           │◄───────────────────────────── │
   │                           │                               │
   │  routing.stopped          │                               │
   │  {success: true, job_ids} │                               │
   │◄───────────────────────── │                               │
```

### 3. Flux DELETE (Avec workflow complet)

```
Frontend                    Backend                         Firebase/Drive
   │                           │                                   │
   │  routing.delete           │                                   │
   │  {job_ids}                │                                   │
   │ ─────────────────────────►│                                   │
   │                           │                                   │
   │                           │  1. Delete notifications          │
   │                           │ ─────────────────────────────────►│
   │                           │                                   │
   │                           │  2. Delete chat threads (RTDB)    │
   │                           │ ─────────────────────────────────►│
   │                           │                                   │
   │                           │  3. Delete approval_pendinglist   │
   │                           │ ─────────────────────────────────►│
   │                           │                                   │
   │                           │  4. Move files to Drive (Router)  │
   │                           │ ─────────────────────────────────►│
   │                           │                                   │
   │                           │  5. Update Redis cache            │
   │                           │                                   │
   │  routing.deleted          │                                   │
   │  {deleted_jobs, moved}    │                                   │
   │◄───────────────────────── │                                   │
```

---

## API des handlers centralisés

### `handle_job_process()`

```python
async def handle_job_process(
    uid: str,
    job_type: str,          # 'router', 'apbookeeper', 'bankbookeeper'
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Déclenche le traitement des documents via le jobbeur approprié.

    Args:
        uid: Firebase user ID
        job_type: Type de job ('router', 'apbookeeper', 'bankbookeeper')
        payload: {
            "document_ids": ["doc1", "doc2", ...],
            "general_instructions": "...",
            "document_instructions": {"doc1": "...", ...},
            "approval_states": {"doc1": True, ...},
            "workflow_states": {"doc1": True, ...},
        }
        company_data: {
            "company_id": "...",
            "mandate_path": "mandates/uid/companies/cid",
        }

    Returns:
        {
            "success": True/False,
            "job_id": "router_batch_xxx",
            "processed_count": 5,
            "message": "Processing started for 5 documents",
            "error": "..." (si échec),
            "code": "PROCESS_ERROR" (si échec),
        }
    """
```

### `handle_job_stop()`

```python
async def handle_job_stop(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Arrête un ou plusieurs jobs en cours.

    Args:
        payload: {
            "job_ids": ["job1", "job2", ...],
            # ou
            "job_id": "job1",  # sera normalisé en liste
        }

    Returns:
        {
            "success": True/False,
            "stopped_jobs": ["job1", "job2"],
            "message": "Stop signal sent for 2 jobs",
        }
    """
```

### `handle_job_restart()`

```python
async def handle_job_restart(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Redémarre un job bloqué (nettoyage sans déplacement).

    Actions:
        1. Supprime les embeddings Chroma associés
        2. Reset le statut Firebase du job
        3. Supprime les notifications associées

    Args:
        payload: {
            "job_id": "job1",
        }

    Returns:
        {
            "success": True/False,
            "job_id": "job1",
            "message": "Job job1 has been reset for reprocessing",
        }
    """
```

### `handle_job_delete()`

```python
async def handle_job_delete(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Supprime des jobs avec workflow complet.

    Actions:
        1. Supprime les notifications Firebase
        2. Supprime les threads de chat RTDB
        3. Supprime les entrées approval_pendinglist
        4. (Router uniquement) Déplace les fichiers vers Drive input
        5. Met à jour le cache Redis
        6. (Router uniquement) Ajoute les items dans to_do

    Args:
        payload: {
            "job_ids": ["job1", "job2", ...],
            # ou format détaillé:
            "items": [
                {"job_id": "job1", "file_name": "doc1.pdf"},
                ...
            ]
        }

    Returns:
        {
            "success": True/False,
            "deleted_jobs": ["job1", "job2"],
            "moved_to_todo": ["doc1.pdf", "doc2.pdf"],  # Router only
            "message": "Deleted 2 jobs",
        }
    """
```

---

## Pattern pour applications externes (Jobbeurs)

Les applications externes (Router, APbookeeper, Bankbookeeper) doivent respecter un pattern harmonisé pour communiquer les étapes de traitement au backend, qui les publiera ensuite via WebSocket au frontend.

### Endpoints HTTP requis

Chaque jobbeur doit exposer les endpoints suivants:

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/{job}-event-trigger` | POST | Déclenche le traitement |
| `/stop_{job}` | POST | Arrête le traitement |
| `/lpt/callback` | POST | Callback de fin de traitement |

### Format de requête PROCESS

```json
POST /event-trigger (Router)
POST /apbookeeper-event-trigger (APbookeeper)
POST /banker-event-trigger (Bankbookeeper)

{
    "collection_name": "company_id",
    "user_id": "firebase_uid",
    "mandates_path": "clients/uid/bo_clients/client_id/mandates/mandate_id",
    "document_ids": ["doc1", "doc2", "doc3"],
    "instructions": "Instructions générales...",
    "document_instructions": {
        "doc1": "Instructions spécifiques pour doc1..."
    },
    "approval_states": {
        "doc1": true,
        "doc2": false
    },
    "workflow_states": {
        "doc1": true,
        "doc2": true
    }
}
```

### Format de réponse PROCESS

```json
HTTP 202 Accepted

{
    "job_id": "router_batch_1706234567.123",
    "status": "accepted",
    "message": "Processing started",
    "document_count": 3
}
```

### Format de requête STOP

```json
POST /stop_router
POST /stop_apbookeeper
POST /stop_banker

{
    "user_id": "firebase_uid",
    "job_ids": ["job1", "job2"],
    "collection_name": "company_id"
}
```

### Format de réponse STOP

```json
HTTP 200 OK

{
    "success": true,
    "stopped_jobs": ["job1", "job2"],
    "message": "Jobs stopped successfully"
}
```

### Pattern de publication des étapes (LPT Callback)

Les jobbeurs doivent utiliser le callback LPT pour notifier le backend des étapes de traitement. Cela permet une publication harmonisée vers le frontend.

#### Callback de progression

```json
POST /lpt/callback

{
    "task_id": "router_batch_1706234567.123",
    "thread_key": "thread_abc123",
    "status": "in_progress",
    "user_id": "firebase_uid",
    "company_id": "company_id",
    "mandate_path": "mandates/uid/companies/cid",
    "result": {
        "current_step": "extraction",
        "progress_percent": 45,
        "processed_items": 2,
        "total_items": 5,
        "current_document": "doc2.pdf"
    },
    "jobs_data": [
        {
            "job_id": "job_doc1",
            "file_name": "doc1.pdf",
            "status": "completed"
        },
        {
            "job_id": "job_doc2",
            "file_name": "doc2.pdf",
            "status": "in_progress"
        }
    ]
}
```

#### Callback de complétion

```json
POST /lpt/callback

{
    "task_id": "router_batch_1706234567.123",
    "thread_key": "thread_abc123",
    "status": "completed",
    "user_id": "firebase_uid",
    "company_id": "company_id",
    "mandate_path": "mandates/uid/companies/cid",
    "result": {
        "summary": {
            "total_processed": 5,
            "successful": 4,
            "failed": 1,
            "duration_seconds": 123.45
        },
        "processed_items": [
            {"id": "doc1", "status": "success", "result": {...}},
            {"id": "doc2", "status": "success", "result": {...}},
            {"id": "doc3", "status": "failed", "error": "Invalid format"}
        ]
    },
    "jobs_data": [
        {
            "job_id": "job_doc1",
            "file_name": "doc1.pdf",
            "status": "completed",
            "destination": "accounting"
        }
    ]
}
```

#### Callback d'erreur

```json
POST /lpt/callback

{
    "task_id": "router_batch_1706234567.123",
    "thread_key": "thread_abc123",
    "status": "failed",
    "user_id": "firebase_uid",
    "company_id": "company_id",
    "mandate_path": "mandates/uid/companies/cid",
    "error": "Connection timeout to ERP",
    "result": {
        "failed_at_step": "erp_sync",
        "partial_results": [...]
    }
}
```

### Création de notifications depuis le jobbeur

Les jobbeurs peuvent créer des notifications directement dans Firebase, qui seront automatiquement publiées via WebSocket si l'utilisateur est connecté.

#### Pattern recommandé

```python
# Dans le jobbeur (Router, APbookeeper, Bankbookeeper)

from app.realtime.pubsub_helper import publish_notification_new

async def notify_processing_step(uid: str, job_id: str, step: str, progress: int):
    """Notifie le frontend d'une étape de traitement."""

    notification = {
        "type": "processing_step",
        "job_id": job_id,
        "step": step,
        "progress": progress,
        "status": "in_progress",
        "functionName": "Router",  # ou "APbookeeper", "Bankbookeeper"
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "read": False,
    }

    # Publie via Redis PubSub -> WebSocket
    await publish_notification_new(uid, notification)


async def notify_completion(uid: str, job_id: str, success: bool, summary: dict):
    """Notifie le frontend de la fin du traitement."""

    notification = {
        "type": "processing_completed" if success else "processing_failed",
        "job_id": job_id,
        "status": "completed" if success else "failed",
        "summary": summary,
        "functionName": "Router",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "read": False,
    }

    await publish_notification_new(uid, notification)
```

### Mise à jour du cache métier

Les jobbeurs peuvent également déclencher des mises à jour du cache métier via le contextual_publisher.

```python
from app.realtime.contextual_publisher import publish_routing_event

async def update_business_cache_after_processing(
    uid: str,
    company_id: str,
    processed_items: list,
    new_status: str
):
    """Met à jour le cache métier et notifie le frontend."""

    await publish_routing_event(
        uid=uid,
        company_id=company_id,
        event_type="job.batch_update",
        payload={
            "action": "status_change",
            "items": processed_items,
            "new_status": new_status,  # "in_process" -> "processed"
        }
    )
```

---

## Événements WebSocket

### Événements de requête (Frontend → Backend)

| Événement | Payload | Description |
|-----------|---------|-------------|
| `routing.process` | `{document_ids, company_id, instructions, ...}` | Lancer le traitement |
| `routing.stop` | `{job_ids, company_id}` | Arrêter des jobs |
| `routing.restart` | `{job_id, company_id}` | Redémarrer un job |
| `routing.delete` | `{job_ids, company_id}` | Supprimer des jobs |

### Événements de réponse (Backend → Frontend)

| Événement | Payload | Description |
|-----------|---------|-------------|
| `routing.processing_started` | `{job_id, document_ids, count}` | Traitement démarré |
| `routing.processed` | `{success, processed, failed, summary}` | Traitement terminé |
| `routing.stopped` | `{success, job_ids, message}` | Jobs arrêtés |
| `routing.restarted` | `{success, job_id, message}` | Job redémarré |
| `routing.deleted` | `{success, deleted_jobs, moved_to_todo}` | Jobs supprimés |
| `routing.error` | `{error, code}` | Erreur |

### Événements de notification (Jobbeur → Backend → Frontend)

| Événement | Payload | Description |
|-----------|---------|-------------|
| `notification.new` | `{docId, type, job_id, status, message}` | Nouvelle notification |
| `notification.updated` | `{docId, ...}` | Notification mise à jour |

---

## Logs et traçabilité

### Format des logs

Les logs suivent un format structuré pour faciliter le debugging:

```
[MODULE] ═══════════════════════════════════════════════════════════
[MODULE] handler_name START - uid=xxx job_type=yyy company_id=zzz
[MODULE] → paramètre1=value1
[MODULE] → paramètre2=value2
[MODULE] → Step 1: Description de l'étape...
[MODULE] → Step 2: Description de l'étape...
[MODULE] handler_name SUCCESS/FAILED - résumé
[MODULE] ═══════════════════════════════════════════════════════════
```

### Préfixes de modules

| Préfixe | Module |
|---------|--------|
| `[JOB_ACTIONS]` | job_actions_handler.py |
| `[ROUTING]` | routing/orchestration.py |
| `[INVOICES]` | invoices/orchestration.py |
| `[BANKING]` | banking/orchestration.py |

### Niveaux de log

- `logger.info()` : Flux normal, étapes principales
- `logger.debug()` : Détails techniques (payloads complets, etc.)
- `logger.warning()` : Situations anormales non-bloquantes
- `logger.error()` : Erreurs avec `exc_info=True`

### Exemple de trace complète

```
[ROUTING] ──────────────────────────────────────────────────────
[ROUTING] handle_routing_process - uid=user123 session=sess456
[ROUTING] → document_ids count=3 company_id=company789
[JOB_ACTIONS] ═══════════════════════════════════════════════════
[JOB_ACTIONS] handle_job_process START - uid=user123 job_type=router company_id=company789
[JOB_ACTIONS] → document_ids count=3 first_ids=['doc1', 'doc2', 'doc3']
[JOB_ACTIONS] → company_data: mandate_path=mandates/user123/companies/company789...
[JOB_ACTIONS] → config: domain=routing endpoint=/event-trigger
[JOB_ACTIONS] → Step 1: Building jobbeur payload...
[JOB_ACTIONS] → Step 2: Calling HTTP endpoint: http://localhost:8080/event-trigger
[JOB_ACTIONS] → Step 3: HTTP response status=202
[JOB_ACTIONS] → Step 4: HTTP success - job_id=router_batch_1706234567
[JOB_ACTIONS] → Step 5: Creating Firebase notification...
[JOB_ACTIONS] → Step 5: Notification created - notif_id=notif_abc123
[JOB_ACTIONS] handle_job_process SUCCESS - job_id=router_batch_1706234567 count=3
[JOB_ACTIONS] ═══════════════════════════════════════════════════
[ROUTING] → Process SUCCESS - job_id=router_batch_1706234567
[ROUTING] → Broadcasting ROUTING.PROCESSING_STARTED
[ROUTING] → Broadcasting ROUTING.PROCESSED (request accepted)
[ROUTING] ──────────────────────────────────────────────────────
```

---

## Guide d'intégration

### Pour ajouter un nouveau job type

1. **Ajouter la configuration** dans `job_actions_handler.py`:

```python
JOB_TYPE_CONFIG = {
    # ... existing configs ...
    "new_job_type": {
        "process_endpoint": "/new-job-event-trigger",
        "stop_endpoint": "/stop_new_job",
        "local_port": 8083,
        "department": "NewDepartment",
        "domain": "new_domain",
        "approval_prefix": "new_",
    },
}
```

2. **Ajouter les événements WebSocket** dans `ws_events.py`:

```python
class NewDomainEvents:
    ORCHESTRATE_INIT = "new_domain.orchestrate_init"
    PROCESS = "new_domain.process"
    PROCESSED = "new_domain.processed"
    # ... etc
```

3. **Créer les handlers d'orchestration** dans `app/frontend/pages/new_domain/orchestration.py`

4. **Enregistrer les handlers** dans `main.py`

### Pour les développeurs de jobbeurs

1. **Implémenter les endpoints requis** (process, stop)
2. **Utiliser le callback LPT** pour les retours asynchrones
3. **Créer des notifications** via `publish_notification_new()`
4. **Respecter les formats de payload** documentés ci-dessus

---

## Références

- [Architecture 3 niveaux de cache](./CACHE_PATTERN_FRONTEND_BACKEND.md)
- [Contextual Publisher](./CONTEXTUAL_PUBLISHING_RULE.md)
- [WebSocket Events](../frontend/WS_EVENTS.md)
