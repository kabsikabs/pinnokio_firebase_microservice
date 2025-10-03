# Documentation du Registre Centralisé des Listeners

## 🎯 Objectif

Le registre centralisé des listeners permet de tracer tous les listeners actifs dans le microservice Firebase, détecter les écoutes zombies et faciliter le debugging.

---

## 📊 Architecture

### Collection Firestore

```
listeners_active/
  {user_id}/
    listeners/
      {listener_id}/
        - listener_type: "chat" | "notif" | "msg" | "workflow"
        - space_code: string (pour chat)
        - thread_key: string (pour chat)
        - mode: string (pour chat: "job_chats", "chats")
        - created_at: timestamp ISO 8601
        - last_heartbeat: timestamp ISO 8601
        - status: "active" | "expired" | "zombie"
        - channel_name: string (canal Redis)
        - ttl_seconds: 90
```

### Exemples d'IDs de Listeners

- **Chat** : `chat_user123_space1_thread1`
- **Notifications** : `notif_user123`
- **Messages** : `msg_user123`
- **Workflow** : `workflow_user123`

---

## 🔌 Endpoints RPC Disponibles

**⚠️ Note :** Les endpoints sont disponibles sous **DEUX préfixes** :
- `REGISTRY.*` : Pour compatibilité avec l'application Reflex (recommandé)
- `REGISTRY_LISTENERS.*` : Pour usage interne ou debugging

### 1. `REGISTRY.check_listener_status` (ou `REGISTRY_LISTENERS.check_listener_status`)

Vérifie si un listener est actif pour un utilisateur.

**Paramètres :**
```python
{
    "user_id": "user123",
    "listener_type": "chat",
    "space_code": "space1",  # Requis pour chat
    "thread_key": "thread1"  # Requis pour chat
}
```

**Retour :**
```json
{
    "success": true,
    "active": true,
    "listener_id": "chat_user123_space1_thread1",
    "status": "active",
    "created_at": "2025-10-03T10:30:00Z",
    "last_heartbeat": "2025-10-03T10:35:00Z",
    "channel_name": "chat:user123:space1:thread1",
    "details": {
        "listener_type": "chat",
        "space_code": "space1",
        "thread_key": "thread1",
        "mode": "job_chats"
    }
}
```

**Exemple d'utilisation (Reflex) :**
```python
# Utiliser le préfixe REGISTRY.* (recommandé pour Reflex)
result = rpc_call(
    "REGISTRY.check_listener_status",
    args=["user123", "chat", "space1", "thread1"]
)

# OU (équivalent)
result = rpc_call(
    "REGISTRY_LISTENERS.check_listener_status",
    args=["user123", "chat", "space1", "thread1"]
)
```

---

### 2. `REGISTRY.register_listener` (ou `REGISTRY_LISTENERS.register_listener`)

Enregistre un listener dans le registre (traçabilité uniquement).

**⚠️ Note :** Cette méthode N'IMPACTE PAS le démarrage réel du listener. Elle sert uniquement à l'enregistrer pour la traçabilité.

**Paramètres :**
```python
{
    "user_id": "user123",
    "listener_type": "chat",
    "space_code": "space1",
    "thread_key": "thread1",
    "mode": "job_chats"
}
```

**Retour :**
```json
{
    "success": true,
    "listener_id": "chat_user123_space1_thread1",
    "channel_name": "chat:user123:space1:thread1",
    "created_at": "2025-10-03T10:30:00Z",
    "message": "Listener enregistré avec succès"
}
```

**Erreurs possibles :**
- `LISTENER_ALREADY_EXISTS` : Un listener actif existe déjà
- `MISSING_REQUIRED_PARAM` : Paramètres manquants
- `INVALID_LISTENER_TYPE` : Type invalide
- `INTERNAL_ERROR` : Erreur interne

---

### 3. `REGISTRY.unregister_listener` (ou `REGISTRY_LISTENERS.unregister_listener`)

Désenregistre un listener du registre.

**Paramètres :**
```python
{
    "user_id": "user123",
    "listener_type": "chat",
    "space_code": "space1",
    "thread_key": "thread1"
}
```

**Retour :**
```json
{
    "success": true,
    "listener_id": "chat_user123_space1_thread1",
    "message": "Listener désenregistré avec succès"
}
```

---

### 4. `REGISTRY.list_user_listeners` (ou `REGISTRY_LISTENERS.list_user_listeners`)

Liste tous les listeners d'un utilisateur.

**Paramètres :**
```python
{
    "user_id": "user123",
    "include_expired": false  # Inclure les listeners expirés
}
```

**Retour :**
```json
{
    "success": true,
    "user_id": "user123",
    "listeners": [
        {
            "listener_id": "chat_user123_space1_thread1",
            "listener_type": "chat",
            "space_code": "space1",
            "thread_key": "thread1",
            "mode": "job_chats",
            "status": "active",
            "created_at": "2025-10-03T10:30:00Z",
            "last_heartbeat": "2025-10-03T10:35:00Z",
            "channel_name": "chat:user123:space1:thread1"
        },
        {
            "listener_id": "notif_user123",
            "listener_type": "notif",
            "status": "active",
            "created_at": "2025-10-03T09:00:00Z",
            "last_heartbeat": "2025-10-03T10:35:00Z",
            "channel_name": "user:user123"
        }
    ],
    "total_count": 2,
    "active_count": 2,
    "expired_count": 0
}
```

---

### 5. `REGISTRY.cleanup_user_listeners` (ou `REGISTRY_LISTENERS.cleanup_user_listeners`)

Nettoie tous les listeners d'un utilisateur.

**Paramètres :**
```python
{
    "user_id": "user123",
    "listener_types": ["chat", "notif"]  # Optionnel, None = tous
}
```

**Retour :**
```json
{
    "success": true,
    "cleaned_count": 3,
    "cleaned_listeners": [
        {
            "listener_id": "chat_user123_space1_thread1",
            "listener_type": "chat",
            "status": "active",
            "space_code": "space1",
            "thread_key": "thread1"
        }
    ],
    "message": "3 listener(s) nettoyé(s) pour user123"
}
```

---

## 🔄 Enregistrement Automatique

Le microservice enregistre **automatiquement** les listeners lors de leur démarrage :

### Listeners Automatiques (via `listeners_registry`)

```python
# Quand un utilisateur se connecte
_ensure_user_watchers(uid)
  └─ Démarre listeners notifications, messages, workflow
  └─ Enregistre chaque listener dans le registre centralisé
```

### Listeners Chat (via WebSocket)

```python
# Quand Reflex ouvre un WebSocket pour un chat
listeners_manager.start_chat_watcher(uid, space_code, thread_key, mode)
  └─ Démarre listener Firebase RTDB
  └─ Enregistre dans le registre centralisé
  └─ Publie sur Redis: chat:{uid}:{space}:{thread}
```

### Nettoyage Automatique

```python
# Lors de la déconnexion
_detach_user_watchers(uid, reason)
  └─ Arrête tous les listeners
  └─ Nettoie le registre centralisé (cleanup_user_listeners)
```

---

## ⏱️ Heartbeat et TTL

### Paramètres par Défaut

- **TTL** : 90 secondes
- **Heartbeat** : Automatique lors de la création
- **Vérification** : Lors de `check_listener_status()` et `list_user_listeners()`

### Statuts Possibles

- **`active`** : Listener actif, heartbeat récent (< TTL)
- **`expired`** : Listener dont le heartbeat a dépassé le TTL
- **`zombie`** : Listener sans heartbeat ou avec heartbeat invalide
- **`not_found`** : Listener inexistant dans le registre

---

## 🧹 Nettoyage Automatique

### Tâche Celery Beat

Une tâche périodique nettoie automatiquement les listeners expirés :

```python
# Exécutée toutes les minutes
@celery_app.task(name='app.maintenance_tasks.cleanup_expired_listeners')
def cleanup_expired_listeners():
    # Parcourt tous les utilisateurs
    # Identifie les listeners expirés
    # Les supprime du registre
```

**Configuration dans `task_service.py` :**
```python
celery_app.conf.beat_schedule = {
    'cleanup-expired-listeners': {
        'task': 'app.maintenance_tasks.cleanup_expired_listeners',
        'schedule': 60.0,  # Toutes les minutes
    },
}
```

---

## 📊 Monitoring et Logs

### Logs Principaux

```python
# Enregistrement
logger.info("listener_register uid=%s type=%s listener_id=%s channel=%s")

# Désenregistrement
logger.info("listener_unregister uid=%s listener_id=%s")

# Nettoyage automatique
logger.info("listener_expired_cleanup uid=%s listener_id=%s type=%s status=%s")

# Erreurs
logger.error("register_listener_error uid=%s type=%s error=%s")
```

### Métriques Recommandées

1. **Compteurs :**
   - `registry.listeners.active` (par type)
   - `registry.listeners.registered_total`
   - `registry.listeners.unregistered_total`
   - `registry.listeners.expired_cleaned_total`

2. **Gauges :**
   - `registry.listeners.zombie_count`
   - `registry.users.with_active_listeners`

---

## 🔍 Debugging

### Vérifier les Listeners d'un Utilisateur

```python
# Via RPC
result = rpc_call(
    "REGISTRY.list_user_listeners",
    args=["user123", True]  # include_expired=True
)
print(result)
```

### Vérifier un Listener Spécifique

```python
# Chat
result = rpc_call(
    "REGISTRY.check_listener_status",
    args=["user123", "chat", "space1", "thread1"]
)

# Notifications
result = rpc_call(
    "REGISTRY.check_listener_status",
    args=["user123", "notif"]
)
```

### Nettoyer Manuellement

```python
# Nettoyer tous les listeners d'un user
result = rpc_call(
    "REGISTRY.cleanup_user_listeners",
    args=["user123"]
)

# Nettoyer seulement les listeners chat
result = rpc_call(
    "REGISTRY.cleanup_user_listeners",
    args=["user123", ["chat"]]
)
```

---

## 🎯 Cas d'Usage

### 1. Détecter les Listeners Zombies

```python
# Lister tous les listeners incluant les expirés
result = rpc_call("REGISTRY.list_user_listeners", args=["user123", True])

# Filtrer les zombies
zombies = [
    l for l in result["listeners"] 
    if l["status"] in ["expired", "zombie"]
]

print(f"Listeners zombies détectés : {len(zombies)}")
for zombie in zombies:
    print(f"  - {zombie['listener_id']} ({zombie['status']})")
```

### 2. Vérifier Avant de Démarrer un Listener

```python
# Vérifier si un listener chat existe déjà
status = rpc_call(
    "REGISTRY.check_listener_status",
    args=["user123", "chat", "space1", "thread1"]
)

if status["active"]:
    print("Listener déjà actif, pas besoin d'en créer un nouveau")
else:
    print("Listener absent ou expiré, on peut en créer un nouveau")
```

### 3. Audit des Listeners Actifs

```python
# Pour tous les utilisateurs (à faire côté microservice)
from app.firebase_client import get_firestore
from app.registry_listeners import get_registry_listeners

db = get_firestore()
registry = get_registry_listeners()

users_ref = db.collection("listeners_active")
users_docs = users_ref.stream()

for user_doc in users_docs:
    uid = user_doc.id
    result = registry.list_user_listeners(uid, include_expired=False)
    print(f"User {uid}: {result['active_count']} listeners actifs")
```

---

## ⚠️ Points d'Attention

### 1. Traçabilité Uniquement

Le registre centralisé est **uniquement pour la traçabilité**. Il n'impacte pas le démarrage ou l'arrêt réel des listeners.

### 2. Erreurs Non-Bloquantes

Les erreurs d'enregistrement/désenregistrement ne bloquent **jamais** le fonctionnement normal des listeners. Si le registre échoue, le listener continue de fonctionner.

### 3. TTL Configurable

Le TTL par défaut est de 90 secondes. Il peut être modifié dans `app/registry_listeners.py` :

```python
class RegistryListeners:
    def __init__(self):
        self.ttl_seconds = 90  # Modifier ici
```

### 4. Performance

Le nettoyage automatique toutes les minutes est adapté pour des volumes modérés. Si vous avez des milliers d'utilisateurs, envisagez d'augmenter l'intervalle à 5 minutes.

---

## 🚀 Déploiement

### Variables d'Environnement

Aucune nouvelle variable nécessaire. Le système utilise la configuration existante (Firestore + Redis).

### Démarrage des Services

```bash
# API Server (comme d'habitude)
CONTAINER_TYPE=api uvicorn app.main:app --host 0.0.0.0 --port 8080

# Worker Celery (avec la nouvelle tâche de nettoyage)
CONTAINER_TYPE=worker celery -A app.task_service worker --loglevel=info

# Beat Scheduler (avec cleanup_expired_listeners)
CONTAINER_TYPE=beat celery -A app.task_service beat --loglevel=info
```

### Vérification

```bash
# Vérifier que la tâche de nettoyage est enregistrée
curl http://localhost:8080/healthz

# Logs du nettoyage automatique
docker logs <container_id> | grep "cleanup_expired_listeners"
```

---

## 🐛 Problèmes Connus et Solutions

### 🔧 Messages Chat Non Publiés (Résolu)

**Symptôme :**  
Les messages de chat n'apparaissaient pas dans l'UI Reflex malgré un listener actif et enregistré.

**Cause :**  
Firebase RTDB envoie deux types d'événements lors de l'initialisation d'un listener :
1. **Snapshot initial** : `path=/`, `data={msg1: {...}, msg2: {...}}` → Tous les messages existants
2. **Nouveau message** : `path=/msg_id`, `data={...}` → Un seul message

Le code filtrait incorrectement le snapshot initial avec `path != "/"`, ce qui ignorait aussi les snapshots mais ne distinguait pas correctement les cas.

**Logs du problème :**
```
🔵 CHAT_EVENT_RECEIVED event_type=put path=/
🟡 CHAT_EVENT_SKIP reason=invalid_data path=/ data_type=NoneType
```

**Solution :**  
Ajout d'une détection explicite du snapshot initial pour l'ignorer proprement (éviter de republier tous les anciens messages), tout en traitant correctement les nouveaux messages :

```python
# Cas 1: path=/ signifie snapshot initial ou mise à jour de tout le thread
if event.path == "/" and isinstance(event.data, dict):
    self.logger.info("🔵 CHAT_SNAPSHOT_RECEIVED messages_count=%s", len(event.data))
    # On ignore les snapshots initiaux pour éviter de republier tous les anciens messages
    return

# Cas 2: path=/msg_id signifie un nouveau message
if not (event.data and event.path != "/" and isinstance(event.data, dict)):
    return
```

**Logs après correction :**
```
🔵 CHAT_SNAPSHOT_RECEIVED messages_count=2  (ignoré)
🔵 CHAT_EVENT_RECEIVED event_type=put path=/-OaeNxY3K...
🔵 CHAT_MESSAGE_PROCESSING msg_id=-OaeNxY3K...
🟢 CHAT_MESSAGE_PUBLISHED msg_id=-OaeNxY3K...
```

**Date de résolution :** 2025-10-03

---

## 📈 Évolutions Futures

### Phase 1 (Actuelle)
✅ Registre centralisé en traçabilité seule  
✅ Enregistrement automatique des listeners  
✅ Nettoyage automatique des listeners expirés  
✅ APIs RPC pour debugging

### Phase 2 (Future)
- Heartbeat actif depuis le microservice (mise à jour périodique)
- Alertes si listeners zombies détectés
- Dashboard de monitoring des listeners
- Métriques Prometheus/Grafana

### Phase 3 (Future)
- Migration vers gestion active (le registre devient source de contrôle)
- Démarrage/arrêt de listeners via RPC uniquement
- Isolation complète entre utilisateurs

---

**Date de création :** 2025-10-03  
**Version :** 1.0  
**Auteur :** Équipe Microservice Firebase

