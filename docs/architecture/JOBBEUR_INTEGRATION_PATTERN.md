# Pattern d'Intégration Jobbeurs

## Introduction

Ce document décrit le pattern d'intégration que doivent suivre les applications externes (jobbeurs) pour communiquer avec le microservice Firebase et, par extension, avec le frontend Next.js.

Les jobbeurs concernés sont:
- **Router** (Port 8080) - Routage de documents depuis Drive
- **APbookeeper** (Port 8081) - Traitement des factures
- **Bankbookeeper** (Port 8082) - Rapprochement bancaire

---

## Vue d'ensemble du flux

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│    FRONTEND     │────►│   MICROSERVICE   │────►│    JOBBEUR      │
│    (Next.js)    │     │    (FastAPI)     │     │  (Router/AP/BK) │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        ▲                       │                        │
        │                       │                        │
        │    WebSocket          │    HTTP Callback       │
        │    Events             │    (LPT)               │
        │                       │                        │
        └───────────────────────┴────────────────────────┘
```

---

## 1. Endpoints HTTP à implémenter

### 1.1 Endpoint de traitement (PROCESS)

Chaque jobbeur doit exposer un endpoint pour déclencher le traitement.

| Jobbeur | Endpoint |
|---------|----------|
| Router | `POST /event-trigger` |
| APbookeeper | `POST /apbookeeper-event-trigger` |
| Bankbookeeper | `POST /banker-event-trigger` |

#### Requête attendue

```json
{
    "collection_name": "company_12345",
    "user_id": "firebase_user_uid",
    "mandate_path": "mandates/firebase_user_uid/companies/company_12345",
    "document_ids": [
        "doc_abc123",
        "doc_def456",
        "doc_ghi789"
    ],
    "instructions": "Instructions générales pour tous les documents",
    "document_instructions": {
        "doc_abc123": "Instructions spécifiques pour ce document",
        "doc_def456": "Traiter en priorité"
    },
    "approval_states": {
        "doc_abc123": true,
        "doc_def456": false,
        "doc_ghi789": true
    },
    "workflow_states": {
        "doc_abc123": true,
        "doc_def456": true,
        "doc_ghi789": false
    }
}
```

#### Réponse attendue

```json
// HTTP 202 Accepted
{
    "job_id": "router_batch_1706234567.123456",
    "status": "accepted",
    "message": "Processing started for 3 documents",
    "document_count": 3
}
```

#### Codes de réponse

| Code | Signification |
|------|---------------|
| 202 | Traitement accepté et démarré |
| 400 | Payload invalide |
| 401 | Non autorisé |
| 500 | Erreur serveur |

---

### 1.2 Endpoint d'arrêt (STOP)

| Jobbeur | Endpoint |
|---------|----------|
| Router | `POST /stop_router` |
| APbookeeper | `POST /stop_apbookeeper` |
| Bankbookeeper | `POST /stop_banker` |

#### Requête attendue

```json
{
    "user_id": "firebase_user_uid",
    "job_ids": [
        "router_batch_1706234567.123456",
        "router_batch_1706234890.654321"
    ],
    "collection_name": "company_12345"
}
```

#### Réponse attendue

```json
// HTTP 200 OK
{
    "success": true,
    "stopped_jobs": [
        "router_batch_1706234567.123456",
        "router_batch_1706234890.654321"
    ],
    "message": "2 jobs stopped successfully"
}
```

---

## 2. Callback LPT (Long Processing Task)

Le callback LPT est le mécanisme principal pour notifier le microservice des étapes et résultats de traitement.

### Endpoint

```
POST {MICROSERVICE_URL}/lpt/callback
```

### 2.1 Notification de progression

Pendant le traitement, envoyez des callbacks de progression pour informer l'utilisateur.

```json
{
    "task_id": "router_batch_1706234567.123456",
    "thread_key": "thread_abc123",
    "status": "in_progress",
    "user_id": "firebase_user_uid",
    "company_id": "company_12345",
    "collection_name": "company_12345",
    "mandate_path": "mandates/firebase_user_uid/companies/company_12345",
    "result": {
        "current_step": "extraction",
        "progress_percent": 45,
        "processed_items": 2,
        "total_items": 5,
        "current_document": {
            "id": "doc_def456",
            "file_name": "facture_002.pdf"
        },
        "steps_completed": [
            "validation",
            "ocr"
        ],
        "steps_remaining": [
            "extraction",
            "classification",
            "export"
        ]
    },
    "jobs_data": [
        {
            "job_id": "job_doc_abc123",
            "file_name": "facture_001.pdf",
            "status": "completed",
            "result": {
                "vendor": "Fournisseur ABC",
                "amount": 1234.56
            }
        },
        {
            "job_id": "job_doc_def456",
            "file_name": "facture_002.pdf",
            "status": "in_progress",
            "current_step": "extraction"
        }
    ]
}
```

### 2.2 Notification de complétion (succès)

```json
{
    "task_id": "router_batch_1706234567.123456",
    "thread_key": "thread_abc123",
    "status": "completed",
    "user_id": "firebase_user_uid",
    "company_id": "company_12345",
    "collection_name": "company_12345",
    "mandate_path": "mandates/firebase_user_uid/companies/company_12345",
    "result": {
        "summary": {
            "total_documents": 5,
            "successful": 4,
            "failed": 1,
            "skipped": 0,
            "duration_seconds": 145.67,
            "start_time": "2026-01-25T10:00:00Z",
            "end_time": "2026-01-25T10:02:25Z"
        },
        "processed_items": [
            {
                "id": "doc_abc123",
                "file_name": "facture_001.pdf",
                "status": "success",
                "destination": "accounting/invoices/supplier_abc",
                "extracted_data": {
                    "vendor": "Fournisseur ABC",
                    "invoice_number": "INV-2026-001",
                    "amount": 1234.56,
                    "date": "2026-01-20"
                }
            },
            {
                "id": "doc_def456",
                "file_name": "facture_002.pdf",
                "status": "success",
                "destination": "accounting/invoices/supplier_xyz",
                "extracted_data": {
                    "vendor": "Entreprise XYZ",
                    "invoice_number": "F-2026-0042",
                    "amount": 567.89,
                    "date": "2026-01-22"
                }
            },
            {
                "id": "doc_ghi789",
                "file_name": "document_inconnu.pdf",
                "status": "failed",
                "error": "Unable to classify document type",
                "error_code": "CLASSIFICATION_FAILED"
            }
        ]
    },
    "jobs_data": [
        {
            "job_id": "job_doc_abc123",
            "file_name": "facture_001.pdf",
            "status": "completed",
            "firebase_doc_id": "fb_doc_123"
        },
        {
            "job_id": "job_doc_def456",
            "file_name": "facture_002.pdf",
            "status": "completed",
            "firebase_doc_id": "fb_doc_456"
        },
        {
            "job_id": "job_doc_ghi789",
            "file_name": "document_inconnu.pdf",
            "status": "failed",
            "error": "CLASSIFICATION_FAILED"
        }
    ]
}
```

### 2.3 Notification d'échec global

```json
{
    "task_id": "router_batch_1706234567.123456",
    "thread_key": "thread_abc123",
    "status": "failed",
    "user_id": "firebase_user_uid",
    "company_id": "company_12345",
    "collection_name": "company_12345",
    "mandate_path": "mandates/firebase_user_uid/companies/company_12345",
    "error": "Connection timeout to ERP system after 3 retries",
    "error_code": "ERP_CONNECTION_TIMEOUT",
    "result": {
        "failed_at_step": "erp_export",
        "documents_processed_before_failure": 2,
        "partial_results": [
            {
                "id": "doc_abc123",
                "status": "success",
                "exported": true
            },
            {
                "id": "doc_def456",
                "status": "success",
                "exported": true
            },
            {
                "id": "doc_ghi789",
                "status": "pending",
                "exported": false,
                "reason": "Processing interrupted"
            }
        ]
    }
}
```

---

## 3. Publication de notifications en temps réel

### 3.1 Via le module pubsub_helper

Pour une intégration directe avec le microservice (si le jobbeur est dans le même environnement):

```python
from app.realtime.pubsub_helper import publish_notification_new

async def notify_user_step_completed(
    uid: str,
    job_id: str,
    step_name: str,
    document_name: str
):
    """Notifie l'utilisateur qu'une étape est terminée."""

    notification = {
        "type": "step_completed",
        "functionName": "Router",  # ou "APbookeeper", "Bankbookeeper"
        "job_id": job_id,
        "step": step_name,
        "document": document_name,
        "status": "info",
        "message": f"Step '{step_name}' completed for {document_name}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "read": False,
    }

    success = await publish_notification_new(uid, notification)
    return success
```

### 3.2 Via écriture Firebase directe

Si le jobbeur écrit directement dans Firebase, la notification sera automatiquement publiée:

```python
# Chemin Firebase pour les notifications
notification_path = f"clients/{user_id}/notifications"

# Document de notification
notification_doc = {
    "type": "processing_completed",
    "functionName": "Router",
    "job_id": job_id,
    "status": "completed",
    "message": "Processing completed successfully",
    "timestamp": firestore.SERVER_TIMESTAMP,
    "read": False,
    "data": {
        "processed_count": 5,
        "failed_count": 1
    }
}

# Écriture dans Firebase
db.collection(notification_path).add(notification_doc)
```

### 3.3 Types de notifications standards

| Type | Description | Quand l'envoyer |
|------|-------------|-----------------|
| `processing_started` | Traitement démarré | Au début du batch |
| `step_completed` | Étape terminée | Après chaque étape majeure |
| `document_processed` | Document traité | Après chaque document |
| `approval_required` | Approbation requise | Quand une décision est nécessaire |
| `processing_completed` | Traitement terminé (succès) | À la fin du batch |
| `processing_failed` | Traitement échoué | En cas d'erreur fatale |
| `processing_partial` | Traitement partiel | Si certains docs ont échoué |

### 3.4 Structure de notification standard

```json
{
    "type": "processing_completed",
    "functionName": "Router",
    "job_id": "router_batch_1706234567.123456",
    "status": "completed",
    "message": "5 documents processed successfully",
    "timestamp": "2026-01-25T10:02:25.123Z",
    "read": false,
    "data": {
        "batch_id": "router_batch_1706234567.123456",
        "company_id": "company_12345",
        "processed_count": 5,
        "failed_count": 0,
        "duration_seconds": 145
    },
    "actions": [
        {
            "type": "view_results",
            "label": "Voir les résultats",
            "route": "/routing?tab=processed"
        }
    ]
}
```

---

## 4. Mise à jour du cache métier

### 4.1 Via contextual_publisher

Pour mettre à jour le cache métier et notifier le frontend en temps réel:

```python
from app.realtime.contextual_publisher import (
    publish_routing_event,
    publish_invoices_event,
    publish_bank_event
)

async def update_documents_status(
    uid: str,
    company_id: str,
    documents: list,
    new_status: str
):
    """Met à jour le statut des documents dans le cache."""

    # Pour Router
    await publish_routing_event(
        uid=uid,
        company_id=company_id,
        event_type="job.batch_update",
        payload={
            "action": "status_change",
            "items": [
                {"id": doc["id"], "status": new_status}
                for doc in documents
            ],
            "new_status": new_status
        }
    )

    # Pour APbookeeper (similaire)
    # await publish_invoices_event(...)

    # Pour Bankbookeeper (similaire)
    # await publish_bank_event(...)
```

### 4.2 Événements de mise à jour supportés

| Action | Description |
|--------|-------------|
| `status_change` | Changement de statut (to_process → in_process → processed) |
| `add_items` | Ajout de nouveaux items |
| `remove_items` | Suppression d'items |
| `update_metadata` | Mise à jour des métadonnées |

---

## 5. Gestion des erreurs

### 5.1 Codes d'erreur standards

| Code | Description |
|------|-------------|
| `VALIDATION_FAILED` | Payload invalide |
| `DOCUMENT_NOT_FOUND` | Document introuvable |
| `CLASSIFICATION_FAILED` | Impossible de classifier |
| `EXTRACTION_FAILED` | Échec de l'extraction |
| `ERP_CONNECTION_ERROR` | Erreur connexion ERP |
| `DRIVE_ACCESS_DENIED` | Accès Drive refusé |
| `TIMEOUT` | Dépassement du délai |
| `INTERNAL_ERROR` | Erreur interne |

### 5.2 Format d'erreur dans les réponses

```json
{
    "success": false,
    "error": "Human-readable error message",
    "error_code": "CLASSIFICATION_FAILED",
    "details": {
        "document_id": "doc_abc123",
        "attempted_classifiers": ["invoice", "receipt", "contract"],
        "confidence_scores": {
            "invoice": 0.35,
            "receipt": 0.28,
            "contract": 0.15
        }
    }
}
```

---

## 6. Bonnes pratiques

### 6.1 Idempotence

Les endpoints doivent être idempotents. Si le même job_id est soumis deux fois, le comportement doit être prévisible:
- Soit retourner le statut actuel du job existant
- Soit rejeter avec un code d'erreur explicite

### 6.2 Timeouts

- Les callbacks LPT doivent être envoyés avec un timeout raisonnable (30s max)
- Si le microservice ne répond pas, réessayer avec backoff exponentiel

### 6.3 Logging

Utiliser un format de log cohérent:

```
[JOBBEUR_NAME] ═══════════════════════════════════════════════
[JOBBEUR_NAME] Processing START - job_id=xxx user_id=yyy
[JOBBEUR_NAME] → document_count=5
[JOBBEUR_NAME] → Step 1: Validation...
[JOBBEUR_NAME] → Step 2: OCR...
[JOBBEUR_NAME] Processing SUCCESS - duration=145s
[JOBBEUR_NAME] ═══════════════════════════════════════════════
```

### 6.4 Healthcheck

Implémenter un endpoint `/health` qui retourne:

```json
{
    "status": "healthy",
    "version": "1.2.3",
    "uptime_seconds": 86400,
    "active_jobs": 3
}
```

---

## 7. Exemple d'implémentation complète

### Router (FastAPI)

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import httpx
import asyncio

app = FastAPI()

MICROSERVICE_URL = "http://microservice:8000"

class ProcessRequest(BaseModel):
    collection_name: str
    user_id: str
    mandate_path: str
    document_ids: List[str]
    instructions: Optional[str] = ""
    document_instructions: Optional[Dict[str, str]] = {}
    approval_states: Optional[Dict[str, bool]] = {}
    workflow_states: Optional[Dict[str, bool]] = {}

class StopRequest(BaseModel):
    user_id: str
    job_ids: List[str]
    collection_name: str


@app.post("/event-trigger")
async def process_documents(request: ProcessRequest):
    """Démarre le traitement des documents."""

    job_id = f"router_batch_{time.time()}"

    # Lancer le traitement en background
    asyncio.create_task(
        process_documents_async(job_id, request)
    )

    return {
        "job_id": job_id,
        "status": "accepted",
        "message": f"Processing started for {len(request.document_ids)} documents",
        "document_count": len(request.document_ids)
    }


async def process_documents_async(job_id: str, request: ProcessRequest):
    """Traitement asynchrone des documents."""

    try:
        # Étape 1: Validation
        await send_progress_callback(
            job_id=job_id,
            request=request,
            status="in_progress",
            step="validation",
            progress=10
        )

        # ... validation logic ...

        # Étape 2: OCR
        await send_progress_callback(
            job_id=job_id,
            request=request,
            status="in_progress",
            step="ocr",
            progress=30
        )

        # ... OCR logic ...

        # Étape 3: Extraction
        await send_progress_callback(
            job_id=job_id,
            request=request,
            status="in_progress",
            step="extraction",
            progress=60
        )

        # ... extraction logic ...

        # Étape 4: Classification
        await send_progress_callback(
            job_id=job_id,
            request=request,
            status="in_progress",
            step="classification",
            progress=80
        )

        # ... classification logic ...

        # Terminé
        await send_completion_callback(
            job_id=job_id,
            request=request,
            success=True,
            results=[...]
        )

    except Exception as e:
        await send_completion_callback(
            job_id=job_id,
            request=request,
            success=False,
            error=str(e)
        )


async def send_progress_callback(
    job_id: str,
    request: ProcessRequest,
    status: str,
    step: str,
    progress: int
):
    """Envoie un callback de progression."""

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{MICROSERVICE_URL}/lpt/callback",
            json={
                "task_id": job_id,
                "thread_key": f"thread_{job_id}",
                "status": status,
                "user_id": request.user_id,
                "company_id": request.collection_name,
                "mandate_path": request.mandate_path,
                "result": {
                    "current_step": step,
                    "progress_percent": progress
                }
            },
            timeout=30.0
        )


async def send_completion_callback(
    job_id: str,
    request: ProcessRequest,
    success: bool,
    results: list = None,
    error: str = None
):
    """Envoie un callback de fin de traitement."""

    payload = {
        "task_id": job_id,
        "thread_key": f"thread_{job_id}",
        "status": "completed" if success else "failed",
        "user_id": request.user_id,
        "company_id": request.collection_name,
        "mandate_path": request.mandate_path,
    }

    if success:
        payload["result"] = {
            "summary": {
                "total_documents": len(request.document_ids),
                "successful": len(results),
                "failed": 0
            },
            "processed_items": results
        }
    else:
        payload["error"] = error

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{MICROSERVICE_URL}/lpt/callback",
            json=payload,
            timeout=30.0
        )


@app.post("/stop_router")
async def stop_processing(request: StopRequest):
    """Arrête les jobs en cours."""

    stopped = []
    for job_id in request.job_ids:
        # Logic to stop job
        stopped.append(job_id)

    return {
        "success": True,
        "stopped_jobs": stopped,
        "message": f"{len(stopped)} jobs stopped successfully"
    }


@app.get("/health")
async def health_check():
    """Vérification de santé."""

    return {
        "status": "healthy",
        "version": "1.0.0",
        "active_jobs": len(active_jobs)
    }
```

---

## 8. Checklist d'intégration

### Avant le déploiement

- [ ] Endpoint `/event-trigger` (ou équivalent) implémenté
- [ ] Endpoint `/stop_{job}` implémenté
- [ ] Endpoint `/health` implémenté
- [ ] Callbacks LPT envoyés pendant le traitement
- [ ] Callback LPT envoyé à la fin (succès ou échec)
- [ ] Codes d'erreur standards utilisés
- [ ] Logs formatés correctement
- [ ] Tests d'intégration passés

### Tests à effectuer

1. **Test PROCESS**:
   - Envoyer une requête de traitement
   - Vérifier le callback de progression
   - Vérifier le callback de complétion
   - Vérifier les notifications dans le frontend

2. **Test STOP**:
   - Démarrer un traitement
   - Envoyer un signal d'arrêt
   - Vérifier que le traitement s'arrête
   - Vérifier la réponse

3. **Test ERROR**:
   - Simuler une erreur
   - Vérifier le callback d'échec
   - Vérifier la notification d'erreur

---

## Contacts

Pour toute question sur l'intégration:
- Architecture: Voir `/docs/architecture/`
- WebSocket Events: Voir `/docs/frontend/WS_EVENTS.md`
- Cache Pattern: Voir `/docs/architecture/CACHE_PATTERN_FRONTEND_BACKEND.md`
