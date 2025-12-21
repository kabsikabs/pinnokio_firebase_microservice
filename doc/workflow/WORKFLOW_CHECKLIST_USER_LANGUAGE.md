# 🌍 Gestion de la langue utilisateur dans les Workflow Checklists

## 📋 Vue d'ensemble

La langue utilisateur (`user_language`) est désormais récupérée dynamiquement depuis le profil de la société au lieu d'être hard-codée à `"fr"`. Cela permet d'adapter automatiquement la langue de l'interface selon les préférences de chaque client.

---

## 🔑 Champ Firebase

### Localisation

Le champ `user_language` est stocké dans Firebase sous la clé :

```
mandate_user_language
```

### Emplacement dans la structure Firebase

```
clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/
└── mandate_user_language: "fr" | "en" | "es" | ...
```

---

## 🏗️ Architecture de récupération

### 1. Chargement dans `user_context`

Le champ est récupéré lors de l'initialisation de la session et ajouté au `user_context` :

#### Dans `llm_manager.py` (ligne 460)

```python
self.user_context = {
    # ... autres champs ...
    "country": full_profile.get("mandate_country"),
    "timezone": full_profile.get("mandate_timezone", "no timezone found"),
    "user_language": full_profile.get("mandate_user_language", "fr"),  # ← NOUVEAU
    # ... suite ...
}
```

#### Dans `lpt_client.py` (ligne 398)

```python
context = {
    # ... autres champs ...
    "legal_name": full_profile.get("mandate_legal_name"),
    "user_language": full_profile.get("mandate_user_language", "fr"),  # ← NOUVEAU
    # ... suite ...
}
```

### 2. Utilisation dans PinnokioBrain

#### Récupération dynamique (ligne 544)

```python
# Récupérer user_language depuis le contexte utilisateur
user_language = self.user_context.get("user_language", "fr") if self.user_context else "fr"

checklist_command = {
    "action": "SET_WORKFLOW_CHECKLIST",
    "params": {
        "checklist": checklist_data,
        "user_language": user_language  # ← Langue dynamique
    }
}
```

#### Contexte minimal par défaut (ligne 1428)

En cas d'erreur de chargement du contexte, `user_language` est défini à `"fr"` par défaut :

```python
self.user_context = {
    "mandate_path": self.collection_name,
    "dms_system": "google_drive",
    "communication_mode": "webhook",
    "log_communication_mode": "firebase",
    "user_language": "fr",  # ← Valeur par défaut
    "mode": mode
}
```

---

## 🔄 Flux complet

```
┌─────────────────────────────────────────────────────────────┐
│  1. FIREBASE (Source de vérité)                             │
│     mandate_user_language: "fr" | "en" | "es" | ...        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  2. CHARGEMENT dans user_context                            │
│     • llm_manager.py → LLMSession.initialize_session_data() │
│     • lpt_client.py → _get_user_context_data()             │
│     • brain.py → load_user_context()                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  3. BRAIN : PinnokioBrain                                   │
│     self.user_context["user_language"]                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  4. CRÉATION DE CHECKLIST                                   │
│     handle_create_checklist()                              │
│     → user_language = self.user_context.get(...)           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ├─────────────────┐
                       │                 │
                       ▼                 ▼
┌──────────────────────────┐  ┌────────────────────────────┐
│  5a. WEBSOCKET           │  │  5b. RTDB                  │
│  Envoi immédiat          │  │  Sauvegarde persistence    │
│  via hub.broadcast()     │  │  via messages_ref.push()   │
└──────────────────────────┘  └────────────────────────────┘
                       │                 │
                       └────────┬────────┘
                                ▼
┌─────────────────────────────────────────────────────────────┐
│  6. CLIENT FRONTEND (Reflex)                                │
│     Reçoit user_language dans le message                   │
│     → Adaptation de l'interface                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 📝 Format des messages

### Message WebSocket

```json
{
  "type": "WORKFLOW_CHECKLIST",
  "channel": "chat:{user_id}:{company_id}:{thread_key}",
  "payload": {
    "type": "WORKFLOW_CHECKLIST",
    "thread_key": "thread_abc123",
    "timestamp": "2025-10-25T14:30:00Z",
    "message_id": "uuid-xxx",
    "content": {
      "message": {
        "cmmd": {
          "action": "SET_WORKFLOW_CHECKLIST",
          "params": {
            "checklist": { /* ... */ },
            "user_language": "fr"  // ← Langue dynamique
          }
        }
      }
    }
  }
}
```

### Message RTDB

```json
{
  "content": {
    "message": {
      "cmmd": {
        "action": "SET_WORKFLOW_CHECKLIST",
        "params": {
          "checklist": { /* ... */ },
          "user_language": "fr"  // ← Langue dynamique
        }
      }
    }
  },
  "sender_id": "user_xxx",
  "timestamp": "2025-10-25T14:30:00Z",
  "message_type": "CMMD",
  "read": false,
  "role": "assistant"
}
```

---

## 🎯 Valeurs supportées

| Code | Langue |
|------|--------|
| `fr` | Français (défaut) |
| `en` | Anglais |
| `es` | Espagnol |
| `de` | Allemand |
| `it` | Italien |
| `pt` | Portugais |

**Note** : La valeur par défaut est `"fr"` si le champ n'existe pas dans Firebase.

---

## 🔍 Points de modification

| Fichier | Ligne | Modification |
|---------|-------|--------------|
| `app/llm_service/llm_manager.py` | 460 | Ajout de `user_language` dans `user_context` |
| `app/pinnokio_agentic_workflow/tools/lpt_client.py` | 398 | Ajout de `user_language` dans `context` |
| `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` | 544 | Récupération dynamique de `user_language` |
| `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` | 1428 | Ajout dans contexte minimal par défaut |

---

## ✅ Avantages

### 1. Internationalisation native
- ✅ Support multilingue automatique
- ✅ Adaptation selon le client
- ✅ Pas de hard-coding de la langue

### 2. Cohérence système
- ✅ Même source de vérité (Firebase)
- ✅ Chargé une seule fois au setup
- ✅ Disponible partout via `user_context`

### 3. Maintenance facilitée
- ✅ Un seul endroit à modifier (Firebase)
- ✅ Propagation automatique
- ✅ Fallback sur `"fr"` en cas d'erreur

---

## 🧪 Tests recommandés

### 1. Test de récupération
```python
# Vérifier que user_language est bien chargé
assert session.user_context.get("user_language") == "fr"
```

### 2. Test avec différentes langues
```python
# Tester avec en, es, de, etc.
full_profile["mandate_user_language"] = "en"
# Vérifier que la checklist utilise "en"
```

### 3. Test fallback
```python
# Si mandate_user_language n'existe pas
# Vérifier que le fallback est "fr"
assert user_language == "fr"
```

---

## 📚 Références

- **Architecture WebSocket** : `doc/REFLEX_WEBSOCKET_STREAMING.md`
- **Workflow Checklist** : `doc/WORKFLOW_CHECK_LIST.MD`
- **User Context** : `app/pinnokio_agentic_workflow/RESUME_VARIABLES_LPT.md`

---

## 🔄 Historique

| Date | Version | Description |
|------|---------|-------------|
| 2025-10-25 | 1.0 | Implémentation initiale de `user_language` dynamique |

