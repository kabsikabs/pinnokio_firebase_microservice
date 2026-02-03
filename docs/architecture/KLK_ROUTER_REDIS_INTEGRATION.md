# Intégration Redis PubSub - KLK Router

**Date**: 2026-02-02  
**Statut**: ✅ Intégration Complétée  
**Applications concernées**: `klk_router` (Router, APbookeeper, Bankbookeeper)

---

## Vue d'ensemble

Ce document décrit l'intégration complète de la publication Redis PubSub dans `klk_router`, alignée avec l'architecture backend du microservice Firebase.

---

## Architecture Implémentée

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           KLK_ROUTER (Jobbeur)                              │
│                                                                             │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────┐  │
│  │  new_router.py      │    │  onboarding_        │    │  Autres apps    │  │
│  │  (Router)           │    │  manager.py         │    │                 │  │
│  └─────────┬───────────┘    └─────────┬───────────┘    └────────┬────────┘  │
│            │                          │                          │           │
│            └──────────────────────────┼──────────────────────────┘           │
│                                       │                                      │
│                                       ▼                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Services Firebase Centralisés                       │  │
│  │                                                                        │  │
│  │  ┌─────────────────────────┐    ┌─────────────────────────────────┐   │  │
│  │  │  FireBaseManagement     │    │  FirebaseRealtimeChat           │   │  │
│  │  │  (g_cred.py)            │    │  (firebase_realtime.py)         │   │  │
│  │  │                         │    │                                  │   │  │
│  │  │  • notifications        │    │  • direct_message_notif         │   │  │
│  │  │  • task_manager         │    │  • job_chats                    │   │  │
│  │  │  • pending_approval     │    │                                  │   │  │
│  │  └────────────┬────────────┘    └────────────────┬────────────────┘   │  │
│  │               │                                   │                    │  │
│  └───────────────┼───────────────────────────────────┼────────────────────┘  │
│                  │                                   │                       │
└──────────────────┼───────────────────────────────────┼───────────────────────┘
                   │                                   │
                   ▼                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PERSISTANCE                                      │
│                                                                               │
│  ┌─────────────────────────┐              ┌─────────────────────────────┐    │
│  │      Firestore          │              │      Firebase RTDB          │    │
│  │                         │              │                             │    │
│  │  • notifications        │              │  • job_chats/messages       │    │
│  │  • task_manager         │              │  • direct_message_notif     │    │
│  │  • approval_pendinglist │              │                             │    │
│  └─────────────────────────┘              └─────────────────────────────┘    │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                   │                                   │
                   │     Si REDIS_PUBLISH_ENABLED      │
                   ▼                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           REDIS PUBSUB                                        │
│                                                                               │
│  Canaux:                                                                      │
│  • user:{uid}/notifications                                                   │
│  • user:{uid}/task_manager                                                    │
│  • user:{uid}/pending_approval                                                │
│  • user:{uid}/direct_message_notif                                            │
│  • user:{uid}/{collection}/{mode}/{job_id}/messages                           │
│                                                                               │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       BACKEND MICROSERVICE (FastAPI)                          │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                         RedisSubscriber                                  │ │
│  │                                                                          │ │
│  │  Pattern d'écoute: user:*                                               │ │
│  │                                                                          │ │
│  │  Routes:                                                                 │ │
│  │  • /notifications      → _handle_notification()                         │ │
│  │  • /task_manager       → _handle_task_manager()                         │ │
│  │  • /pending_approval   → _handle_pending_approval()                     │ │
│  │  • /direct_message_notif → _handle_direct_message()                     │ │
│  │  • /job_chats/*/messages → _handle_job_chat_message()                   │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                          │
│                                    ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  • Cache Redis (business:{uid}:{cid}:*)                                 │ │
│  │  • WebSocket Broadcast                                                   │ │
│  │  • llm_manager (pour job_chats)                                         │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Canaux Redis Implémentés

| Canal | Pattern | Classe Source | Description |
|-------|---------|---------------|-------------|
| Notifications | `user:{uid}/notifications` | `FireBaseManagement` | Mises à jour de notifications (statut jobs) |
| Task Manager | `user:{uid}/task_manager` | `FireBaseManagement` | Suivi activité/facturation |
| Pending Approval | `user:{uid}/pending_approval` | `FireBaseManagement` | Liste d'approbations en attente |
| Direct Message | `user:{uid}/direct_message_notif` | `FirebaseRealtimeChat` | Notifications instantanées |
| Job Chats | `user:{uid}/{collection}/{mode}/{job_id}/messages` | `FirebaseRealtimeChat` | Messages de chat workflow |

---

## Fichiers Modifiés

### 1. `tools/redis_pubsub.py`

**Ajout**: Fonction `is_redis_publish_enabled()`

```python
def is_redis_publish_enabled() -> bool:
    """
    Verifie si la publication Redis est activee via variable d'environnement.
    
    Returns:
        bool: True si REDIS_PUBLISH_ENABLED est defini a true (defaut: true)
    """
    return _str_to_bool(os.getenv("REDIS_PUBLISH_ENABLED", "true"))
```

---

### 2. `tools/g_cred.py` (FireBaseManagement)

#### Nouvelles méthodes

**`save_pending_approval()`** - Sauvegarde Firestore + Publication Redis

```python
def save_pending_approval(
    self,
    mandate_path: str,
    doc_id: str,
    approval_payload: dict,
    company_id: str = None,
    department: str = "routing",
    action: str = "add"  # "add" | "update" | "remove"
) -> bool:
```

**`_publish_pending_approval_to_redis()`** - Publication sur canal `pending_approval`

```python
def _publish_pending_approval_to_redis(
    self,
    job_id: str,
    company_id: str,
    collection_path: str,
    mandate_path: str,
    approval_payload: dict,
    department: str = "routing",
    action: str = "add"
):
```

#### Méthodes modifiées (ajout vérification `is_redis_publish_enabled()`)

- `_publish_task_manager_to_redis()`
- `_publish_notification_to_redis()`

---

### 3. `tools/firebase_realtime.py` (FirebaseRealtimeChat)

#### Nouvelle méthode

**`_publish_job_chat_to_redis()`** - Publication sur canal `job_chats`

```python
def _publish_job_chat_to_redis(
    self,
    space_code: str,
    thread_key: str,
    message_data: dict,
    message_id: str = None,
    mode: str = 'job_chats'
):
```

#### Méthodes modifiées

**`send_realtime_message_structured()`** - Ajout publication Redis après écriture RTDB

**`_publish_direct_message_to_redis()`** - Ajout vérification `is_redis_publish_enabled()`

---

### 4. `tools/new_router.py`

#### Méthodes modifiées

**`_save_to_pending_list()`** - Utilise maintenant `firebase_service.save_pending_approval()`

**Suppression**: `_publish_pending_approval_to_redis()` (logique déplacée vers `FireBaseManagement`)

**Suppression**: Import inutilisé `get_redis_client, run_async`

---

## Format des Payloads Redis

### Notifications

```json
{
    "type": "notification_update",
    "drive_id": "doc_abc123",
    "job_id": "klk_xxx",
    "collection_path": "clients/{uid}/notifications",
    "update_data": {
        "status": "completed"
    },
    "status": "completed",
    "timestamp": "2026-02-02T10:30:00Z"
}
```

### Task Manager

```json
{
    "type": "task_manager_update",
    "job_id": "klk_xxx",
    "mandate_path": "clients/{uid}/companies/{cid}/mandates/{mid}",
    "collection_path": "clients/{uid}/task_manager/{job_id}",
    "data": {
        "status": "running",
        "department": "router"
    },
    "status": "running",
    "department": "router",
    "timestamp": "2026-02-02T10:30:00Z"
}
```

### Pending Approval

```json
{
    "type": "pending_approval_created",
    "action": "add",
    "department": "routing",
    "job_id": "router_{drive_file_id}",
    "company_id": "company_12345",
    "collection_path": "{mandate_path}/approval_pendinglist/{doc_id}",
    "mandate_path": "clients/{uid}/companies/{cid}/mandates/{mid}",
    "data": {
        "id": "router_{drive_file_id}",
        "file_name": "document.pdf",
        "drive_file_id": "abc123",
        "status": "pending_approval",
        "step": "routing",
        "service_list": ["invoices", "expenses"],
        "available_years": ["2024", "2025"],
        "report": "...",
        "selected_motivation": "...",
        "timestamp": "2026-02-02T10:30:00Z"
    },
    "timestamp": "2026-02-02T10:30:00Z"
}
```

### Direct Message

```json
{
    "type": "direct_message",
    "message_id": "msg_abc123",
    "recipient_id": "{uid}",
    "sender_id": "{sender_uid}",
    "collection_path": "clients/{uid}/direct_message_notif",
    "data": {
        "file_name": "document.pdf",
        "job_id": "klk_xxx",
        "status": "Action required"
    },
    "job_id": "klk_xxx",
    "timestamp": "2026-02-02T10:30:00Z"
}
```

### Job Chats

```json
{
    "type": "job_chat_message",
    "job_id": "{thread_key}",
    "collection_name": "{space_code}",
    "thread_key": "{thread_key}",
    "message": {
        "id": "msg_abc123",
        "message_type": "MESSAGE",
        "content": "...",
        "sender_id": "{uid}",
        "timestamp": "2026-02-02T10:30:00Z",
        "read": false
    },
    "timestamp": "2026-02-02T10:30:00Z"
}
```

---

## Variable d'Environnement

```bash
# Active/désactive la publication Redis (défaut: true)
REDIS_PUBLISH_ENABLED=true
```

Quand `REDIS_PUBLISH_ENABLED=false`:
- Les données sont toujours persistées (Firestore/RTDB)
- Aucune publication Redis n'est effectuée
- Utile pour le debug ou les environnements sans Redis

---

## Utilisation dans les Applications

### Exemple: Router (new_router.py)

```python
# Sauvegarde pending approval avec publication Redis automatique
self.firebase_service.save_pending_approval(
    mandate_path=self.mandate_path,
    doc_id=f"router_{drive_file_id}",
    approval_payload=approval_payload,
    company_id=self.collection_name,
    department="routing",
    action="add"
)
```

### Exemple: Onboarding Manager

```python
from tools.g_cred import FireBaseManagement

firebase_instance = FireBaseManagement(user_id)
firebase_instance.save_pending_approval(
    mandate_path=mandate_path,
    doc_id=f"onboarding_{job_id}",
    approval_payload=approval_payload,
    company_id=company_id,
    department="onboarding",
    action="add"
)
```

### Exemple: Messages Job Chats

```python
# Via GoogleSpaceManager.send_message() mode pinnokio
# La publication Redis est automatique via send_realtime_message_structured()
self.space_manager.send_message(
    space_code=self.collection_name,
    thread_key=self.job_id,
    text=log_message,
    message_mode='job_chats'
)
```

---

## Compatibilité Backend

Cette intégration est **100% compatible** avec:

- `RedisSubscriber` dans `app/realtime/redis_subscriber.py`
- `_handle_job_chat_message()` pour les messages de chat
- `_handle_notification()` pour les notifications
- `_handle_task_manager()` pour le suivi d'activité
- `_handle_pending_approval()` pour les approbations en attente

---

## Checklist de Validation

### KLK Router (Jobbeur)

- [x] `is_redis_publish_enabled()` ajouté dans `redis_pubsub.py`
- [x] `save_pending_approval()` ajouté dans `FireBaseManagement`
- [x] `_publish_pending_approval_to_redis()` ajouté dans `FireBaseManagement`
- [x] `_publish_job_chat_to_redis()` ajouté dans `FirebaseRealtimeChat`
- [x] `send_realtime_message_structured()` publie sur Redis
- [x] Vérification `is_redis_publish_enabled()` dans toutes les méthodes de publication
- [x] `new_router.py` utilise `save_pending_approval()` centralisé
- [x] Import inutilisé supprimé de `new_router.py`

### Tests à effectuer

- [ ] Test notifications: Vérifier publication sur `user:{uid}/notifications`
- [ ] Test task_manager: Vérifier publication sur `user:{uid}/task_manager`
- [ ] Test pending_approval: Vérifier publication sur `user:{uid}/pending_approval`
- [ ] Test direct_message: Vérifier publication sur `user:{uid}/direct_message_notif`
- [ ] Test job_chats: Vérifier publication sur `user:{uid}/{collection}/job_chats/{job_id}/messages`
- [ ] Test désactivation: Vérifier que `REDIS_PUBLISH_ENABLED=false` bloque les publications

---

## Références

- [JOB_ACTIONS_CENTRALIZED_HANDLER.md](./JOB_ACTIONS_CENTRALIZED_HANDLER.md)
- [JOBBEUR_INTEGRATION_PATTERN.md](./JOBBEUR_INTEGRATION_PATTERN.md)
- [ONBOARDING_MANAGER_PUBSUB_MIGRATION.md](./ONBOARDING_MANAGER_PUBSUB_MIGRATION.md)
