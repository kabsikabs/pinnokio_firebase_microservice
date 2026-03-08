# Documentation Architecture - Firebase Microservice

Ce dossier contient la documentation de l'architecture du microservice Firebase et ses patterns d'intégration.

---

## Documents disponibles

### Handlers centralisés

| Document | Description |
|----------|-------------|
| [JOB_ACTIONS_CENTRALIZED_HANDLER.md](./JOB_ACTIONS_CENTRALIZED_HANDLER.md) | Architecture du handler centralisé pour les actions de jobs (process, stop, restart, delete) |

### Patterns d'intégration

| Document | Description |
|----------|-------------|
| [JOBBEUR_INTEGRATION_PATTERN.md](./JOBBEUR_INTEGRATION_PATTERN.md) | Guide d'intégration pour les applications externes (Router, APbookeeper, Bankbookeeper) |
| [ONBOARDING_MANAGER_PUBSUB_MIGRATION.md](./ONBOARDING_MANAGER_PUBSUB_MIGRATION.md) | Migration Onboarding Manager vers PubSub Redis |

### Intégration Jobbeurs

| Document | Description |
|----------|-------------|
| [KLK_ROUTER_REDIS_INTEGRATION.md](./KLK_ROUTER_REDIS_INTEGRATION.md) | Intégration Redis PubSub dans klk_router (canaux, payloads, méthodes) |

---

## Résumé de l'architecture

### Flux de données principal

```
Frontend (Next.js)
       │
       │ WebSocket Events
       ▼
Microservice (FastAPI)
       │
       ├─────────────────────────────────────────┐
       │                                         │
       ▼                                         ▼
job_actions_handler.py                    Jobbeurs externes
(Centralized Handler)                     (Router/AP/Bank)
       │                                         │
       ├── Firebase Notifications                │
       ├── Redis Cache                           │
       └── WebSocket Broadcast                   │
                                                 │
                    LPT Callback ◄───────────────┘
```

### Composants clés

1. **job_actions_handler.py** - Point d'entrée centralisé pour toutes les actions de jobs
2. **Page orchestration handlers** - Gestion des événements WebSocket par page
3. **Contextual Publisher** - Publication des événements avec gestion du contexte
4. **PubSub Helper** - Publication des notifications
5. **LPT Callback** - Réception des résultats de traitement asynchrone

### Job types supportés

| Type | Port | Description |
|------|------|-------------|
| `router` | 8080 | Routage de documents depuis Drive |
| `apbookeeper` | 8081 | Traitement des factures |
| `bankbookeeper` | 8082 | Rapprochement bancaire |

---

## Mise à jour: 2026-02-02

### Changements récents

- **[2026-02-02]** Intégration Redis PubSub complète dans klk_router
  - Centralisation publication Redis dans `FireBaseManagement` et `FirebaseRealtimeChat`
  - Ajout canal `pending_approval` pour approbations en attente
  - Ajout canal `job_chats` pour messages de workflow
  - Variable d'environnement `REDIS_PUBLISH_ENABLED` pour contrôle
- **[2026-01-26]** Migration Onboarding Manager vers PubSub Redis
- **[2026-01-25]** Création du handler centralisé `job_actions_handler.py`
- Ajout des handlers `routing.stop` et `routing.delete`
- Intégration des handlers invoices avec le handler centralisé
- Documentation complète du pattern d'intégration jobbeurs
- Amélioration des logs pour traçabilité complète

---

## Voir aussi

- [Frontend Documentation](../../pinnokio_app_v2/docs/) - Documentation frontend Next.js
- [API Documentation](../api/) - Documentation API REST
