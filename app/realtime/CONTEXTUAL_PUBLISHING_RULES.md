# Règles de Publication Contextuelle

## Vue d'ensemble

Le système de publication contextuelle permet de publier des événements selon 3 niveaux de granularité :

1. **USER** (global) - Pour le compte utilisateur
2. **COMPANY** - Pour la société sous le compte
3. **PAGE** - Pour la page en cours

## Règles de Publication

### Niveau USER (Global)

**Utilisation**: Notifications, messages, événements globaux au compte utilisateur

**Règle**: Toujours publié si l'utilisateur est connecté (pas de vérification de page/company)

**Exemples**:
- Notifications de jobs (Router, APbookeeper, Bankbookeeper)
- Messages directs (Messenger)
- Alertes système
- Changements de balance (compte utilisateur)

**Cache**: `cache:user:{uid}`

**Code**:
```python
from app.realtime.contextual_publisher import publish_user_event

await publish_user_event(
    uid="user123",
    event_type=WS_EVENTS.NOTIFICATION.DELTA,
    payload={"action": "new", "data": {...}}
)
```

### Niveau COMPANY (Par société)

**Utilisation**: Métriques, jobs, données spécifiques à une société

**Règle**: Publié seulement si la société correspond à celle sélectionnée par l'utilisateur

**Exemples**:
- Changements de métriques dashboard (par société)
- Jobs APbookeeper/Router/Banker (par société)
- Approvals (par société)
- Tasks (par société)

**Cache**: `cache:company:{uid}:{company_id}`

**Code**:
```python
from app.realtime.contextual_publisher import publish_company_event

await publish_company_event(
    uid="user123",
    company_id="company_xyz",
    event_type=WS_EVENTS.DASHBOARD.METRICS_UPDATE,
    payload={"metrics": {...}},
    session_id=session_id  # Optionnel, pour récupérer le contexte
)
```

### Niveau PAGE (Par page)

**Utilisation**: Widgets spécifiques, mises à jour de page

**Règle**: Publié seulement si l'utilisateur est sur la page concernée ET la société correspond

**Exemples**:
- Mise à jour d'un widget dashboard (seulement si sur /dashboard)
- Changement de status d'un document routing (seulement si sur /routing)
- Nouveau message chat (seulement si sur /chat avec le thread ouvert)
- Mise à jour d'une facture (seulement si sur /invoices)

**Cache**: `cache:page:{uid}:{company_id}:{page}`

**Code**:
```python
from app.realtime.contextual_publisher import publish_page_event

await publish_page_event(
    uid="user123",
    company_id="company_xyz",
    page="dashboard",
    event_type=WS_EVENTS.DASHBOARD.STORAGE_UPDATE,
    payload={"storage": {...}},
    session_id=session_id  # Optionnel, pour récupérer le contexte
)
```

## Mise à jour du Contexte de Page

Le frontend doit envoyer un événement `page.context_change` lors d'un changement de page :

```typescript
// Frontend
wsClient.send({
    type: "page.context_change",
    payload: {
        page: "dashboard"  // ou "chat", "routing", "invoices", etc.
    }
})
```

Le backend met à jour automatiquement le contexte dans Redis.

## Règles de Cache

### Règle 1: Cache TOUJOURS mis à jour

Le cache est **TOUJOURS** mis à jour, même si l'événement n'est pas publié (utilisateur non connecté, mauvaise page, etc.).

Cela garantit que :
- Les données sont disponibles au prochain chargement
- Le cache reste cohérent même si l'utilisateur change de page
- Pas de perte de données

### Règle 2: Format du Cache

Le cache stocke les données dans un format structuré :

```json
{
    "items": [
        {"id": "...", "data": {...}},
        ...
    ],
    "last_update": "2026-01-21T10:30:00Z"
}
```

### Règle 3: TTL par défaut

- **USER**: 1 heure (3600s)
- **COMPANY**: 1 heure (3600s)
- **PAGE**: 30 minutes (1800s)

## Exemples d'Utilisation

### Exemple 1: Notification (USER)

```python
# Jobbeur (APbookeeper) après traitement d'une facture
from app.realtime.pubsub_helper import publish_notification_new

await publish_notification_new(uid, {
    "docId": "notif_abc123",
    "message": "Invoice processed successfully",
    "status": "completed",
    "functionName": "APbookeeper",
    "jobId": "job_123"
})
# → Publié si utilisateur connecté (n'importe quelle page/société)
```

### Exemple 2: Métrique Dashboard (PAGE)

```python
# Jobbeur après calcul de métriques
from app.realtime.contextual_publisher import publish_page_event

await publish_page_event(
    uid=uid,
    company_id=company_id,
    page="dashboard",
    event_type=WS_EVENTS.DASHBOARD.METRICS_UPDATE,
    payload={
        "metrics": {
            "totalInvoices": 150,
            "totalAmount": 50000.00
        }
    }
)
# → Publié seulement si utilisateur sur /dashboard ET société correspond
```

### Exemple 3: Job Router (COMPANY)

```python
# Router après traitement d'un document
from app.realtime.contextual_publisher import publish_company_event

await publish_company_event(
    uid=uid,
    company_id=company_id,
    event_type=WS_EVENTS.ROUTING.DOCUMENT_PROCESSED,
    payload={
        "document_id": "doc_xyz",
        "status": "completed"
    }
)
# → Publié seulement si société correspond (n'importe quelle page)
```

## Décision: Quel Niveau Utiliser?

| Type d'événement | Niveau | Raison |
|-----------------|--------|--------|
| Notification job | USER | Global au compte utilisateur |
| Message direct | USER | Global au compte utilisateur |
| Balance compte | USER | Global au compte utilisateur |
| Métrique dashboard | PAGE | Spécifique à la page dashboard |
| Widget storage | PAGE | Spécifique à la page dashboard |
| Job APbookeeper | COMPANY | Spécifique à la société |
| Document routing | COMPANY ou PAGE | Selon si besoin de la page |
| Task | COMPANY | Spécifique à la société |
| Approval | COMPANY | Spécifique à la société |

## Migration depuis l'Ancien Système

### Avant (pubsub_helper.py)

```python
# Ancien code
hub.broadcast_threadsafe(uid, {
    "type": WS_EVENTS.NOTIFICATION.DELTA,
    "payload": payload
})
```

### Après (contextual_publisher.py)

```python
# Nouveau code - USER level
await publish_user_event(
    uid=uid,
    event_type=WS_EVENTS.NOTIFICATION.DELTA,
    payload=payload
)

# Nouveau code - PAGE level
await publish_page_event(
    uid=uid,
    company_id=company_id,
    page="dashboard",
    event_type=WS_EVENTS.DASHBOARD.METRICS_UPDATE,
    payload=payload
)
```

## Avantages

1. **Optimisation**: Pas de publication inutile si utilisateur sur autre page
2. **Cache cohérent**: Données toujours disponibles même si non publiées
3. **Flexibilité**: 3 niveaux permettent de cibler précisément
4. **Performance**: Réduction du trafic WebSocket inutile
