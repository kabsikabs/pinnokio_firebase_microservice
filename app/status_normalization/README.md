# Status Normalization Module

Module centralisé pour la normalisation des statuts dans l'application Pinnokio.

## Installation

Le module est automatiquement disponible dans le projet backend:

```python
from status_normalization import StatusNormalizer, NormalizedStatus
```

## Quick Start

```python
from status_normalization import StatusNormalizer

# Normaliser un statut brut
status = StatusNormalizer.normalize("running")  # → "on_process"

# Avec contexte de fonction (Router a des règles spéciales)
status = StatusNormalizer.normalize_for_function("Router", "success")  # → "routed"

# Obtenir la catégorie pour l'onglet UI
category = StatusNormalizer.get_category("on_process")  # → "in_process"
```

## API Reference

### StatusNormalizer

Classe utilitaire avec méthodes statiques.

#### `normalize(raw_status, default="to_process") -> str`

Normalise un statut brut vers sa valeur standardisée.

```python
StatusNormalizer.normalize("running")      # → "on_process"
StatusNormalizer.normalize("in queue")     # → "in_queue"
StatusNormalizer.normalize("success")      # → "completed"
StatusNormalizer.normalize(None)           # → "to_process"
StatusNormalizer.normalize("unknown")      # → "unknown" (passthrough)
```

#### `normalize_for_function(function_name, raw_status, default="to_process") -> str`

Normalise avec prise en compte du contexte de fonction.

```python
# Router: success → routed (override spécifique)
StatusNormalizer.normalize_for_function("Router", "success")   # → "routed"

# Banker: success → completed (standard)
StatusNormalizer.normalize_for_function("Banker", "success")   # → "completed"
```

#### `get_category(normalized_status) -> str`

Retourne la catégorie (onglet UI) pour un statut normalisé.

```python
StatusNormalizer.get_category("on_process")  # → "in_process"
StatusNormalizer.get_category("completed")   # → "processed"
StatusNormalizer.get_category("error")       # → "to_process"
StatusNormalizer.get_category("pending")     # → "pending"
```

#### Helpers

```python
StatusNormalizer.is_in_progress("running")     # → True
StatusNormalizer.is_completed("success")       # → True
StatusNormalizer.is_error("error")             # → True
StatusNormalizer.is_valid_status("on_process") # → True
```

## Statuts Normalisés

| Statut | Description | Catégorie |
|--------|-------------|-----------|
| `to_process` | À traiter (défaut) | to_process |
| `in_queue` | En file d'attente | in_process |
| `on_process` | En cours | in_process |
| `stopping` | Arrêt en cours | in_process |
| `pending` | En attente | pending |
| `completed` | Terminé | processed |
| `error` | Erreur | to_process |
| `stopped` | Arrêté | to_process |
| `routed` | Routé (Router) | to_process |

## Mapping Statuts Bruts

| Brut | Normalisé |
|------|-----------|
| `running`, `processing` | `on_process` |
| `in queue`, `in_queue`, `queued` | `in_queue` |
| `success`, `close`, `done`, `finished` | `completed` |
| `error` | `error` |
| `pending` | `pending` |
| `stopping` | `stopping` |
| `stopped` | `stopped` |

## Overrides par Fonction

| Fonction | Brut | Normalisé |
|----------|------|-----------|
| Router | `success` | `routed` |
| Router | `completed` | `routed` |

## Exemple d'intégration

```python
from status_normalization import StatusNormalizer

async def process_notification(notification: dict) -> dict:
    """Traite une notification et normalise son statut."""
    raw_status = notification.get('status', '')
    function_name = notification.get('function_name', '')

    # Normalisation centralisée
    normalized = StatusNormalizer.normalize_for_function(
        function_name,
        raw_status,
        default="to_process"
    )

    # Catégorisation
    category = StatusNormalizer.get_category(normalized)

    return {
        **notification,
        'status': normalized,
        'category': category,
    }
```

## Fichiers

- `__init__.py` - Exports publics
- `constants.py` - Enums et mappings
- `normalizer.py` - Classe StatusNormalizer
- `README.md` - Cette documentation
