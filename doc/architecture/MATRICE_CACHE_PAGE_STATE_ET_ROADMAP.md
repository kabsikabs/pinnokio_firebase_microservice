# Matrice Cache / Page State et Roadmap de Simplification

## Contexte

Le frontend utilise actuellement un pattern cache-first:

1. `page.restore_state` (lecture `page_state:{uid}:{company_id}:{page}`)
2. si cache miss: `*.orchestrate_init`
3. synchro temps réel via événements WebSocket (`full_data`, `task_manager_update`, `item_update`, etc.)

Ce pattern fonctionne, mais crée un risque de divergence quand les données métier sont aussi maintenues dans un cache business centralisé (Redis `business:{uid}:{cid}:{domain}`).

---

## Matrice par page

| Page | Source de vérité métier (cible) | Usage `page_state` actuel | Événements incrémentaux | Risque de drift | Priorité |
|---|---|---|---|---|---|
| `routing` | `business:{uid}:{cid}:routing` | Oui (restore + hydration store complet) | `routing.item_update` + `routing.task_manager_update` | Élevé | P1 |
| `invoices` | `business:{uid}:{cid}:invoices` | Oui | `invoices.task_manager_update` (pas de `item_update` dédié) | Élevé | P1 |
| `banking` | `business:{uid}:{cid}:banking` | Oui | `banking.task_manager_update` (pas de `item_update` dédié) | Élevé | P1 |
| `expenses` | cache métier expenses | Oui | events métier (`closed/reopened/updated/deleted`) | Moyen | P2 |
| `hr` | cache/store HR + backend HR | Oui (mix local cache + restore) | events CRUD HR | Moyen/Élevé | P2 |
| `chat` | état session/chat backend | Oui | events chat (sessions/history/mode) | Moyen | P3 |
| `chat_tasks` | cache tasks/dashboard | Oui | `dashboard.tasks_update` + events task | Moyen | P3 |
| `coa` | cache COA + orchestration COA | Oui (moins central) | events COA | Faible/Moyen | P3 |
| `dashboard` | orchestration dashboard + caches modules | Oui | events dashboard multiples | Moyen | P2 |

---

## Décision cible (recommandée)

### Principe d’architecture

- **Données métier dynamiques**: source de vérité unique = cache business centralisé.
- **Page state**: uniquement état UI (onglet, filtres, tri, pagination, scroll, préférences locales).
- **Refresh/recovery**: en cas de miss `page_state`, relancer orchestration qui lit le cache business (ou reconstruit si absent).

### Pourquoi

- Réduit le risque de rollback visuel (ex: item repassant de `to_process` vers `pending`).
- Réduit le coût d’invalidation inter-caches.
- Clarifie la responsabilité de chaque couche.

---

## Roadmap de migration (safe, en 3 étapes)

## Étape 1 — Stabilisation immédiate (P1: Routing)

Objectif: supprimer les divergences visibles sans casser le flow existant.

- Invalider `page_state:routing` après chaque mouvement de liste backend.
- Garantir la diffusion `routing.item_update` pour chaque action de déplacement.
- Conserver `page.restore_state` mais forcer fallback orchestration propre sur miss.

Critères de succès:

- Après `routing.restart`, l’item reste dans `to_process`.
- Aucun retour spontané en `pending` après `routing.restarted`.

---

## Étape 2 — Convergence modèle de données (P1: Routing, Invoices, Banking)

Objectif: uniformiser les pages à forte dynamique métier.

- `page_state` ne stocke plus les listes complètes (documents/items/transactions).
- `page_state` conserve uniquement:
  - tab active
  - filtres/sort
  - pagination
  - context UI
- L’hydratation des listes provient:
  - d’un event `*.full_data` basé business cache
  - puis des deltas (`task_manager_update` + `item_update`)
- Ajouter `item_update` dédié pour `invoices` et `banking` (parité avec `routing`).

Critères de succès:

- Plus de recopie de snapshot métier via `page.state_restored`.
- Réduction nette des invalidations de `page_state`.

---

## Étape 3 — Standardisation transverse (P2/P3)

Objectif: simplifier l’ensemble des pages et réduire la dette technique.

- Introduire un contrat commun de synchro:
  - `full_data` = reconstruction base vue
  - `item_update` = mouvement de listes atomique
  - `task_manager_update` = statut worker temps réel
- Centraliser la logique `page_state recovery` dans un hook commun (`usePageStateRecovery`) pour éviter la duplication.
- Définir une politique d’invalidation unique par domaine.

Critères de succès:

- Flux homogène entre modules.
- Moins de code spécifique page pour la récupération cache-first.

---

## Plan de validation (checklist)

- [ ] Restart `routing`: `pending -> to_process` reste stable après `restarted`.
- [ ] Refresh navigateur sur page `routing`: état cohérent avec cache business.
- [ ] Scénario équivalent `invoices` et `banking` (stop/restart/delete).
- [ ] Aucun event “vieux snapshot” n’écrase un delta récent.
- [ ] Dashboard continue à refléter les métriques sans régression.

---

## Risques et mitigation

- Risque: perte de rapidité au restore si on retire des données de `page_state`.
  - Mitigation: conserver cache business chaud + `full_data` rapide.
- Risque: régression pendant migration incrémentale.
  - Mitigation: feature flag par domaine (`routing` puis `invoices/banking`).
- Risque: incohérence temporaire frontend/backend.
  - Mitigation: contrat WS explicite et tests E2E sur transitions de listes.

