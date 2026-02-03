# Migration des Listeners Firebase vers PubSub Redis

**Date**: 2026-01-21  
**Auteur**: Migration Architecture  
**Statut**: ✅ Complété

## Vue d'ensemble

Cette migration remplace les listeners directs Firestore/RTDB par un système de publication PubSub Redis. Comme nous sommes les auteurs de toutes les écritures dans Firebase/RTDB, nous pouvons publier directement sur Redis PubSub après chaque écriture, éliminant le besoin d'écouter les changements Firebase.

## Contexte et Justification

### Problèmes de l'ancien système

1. **Listeners Firebase coûteux** : Les listeners `on_snapshot` Firestore et RTDB consomment des ressources et ont des limites de connexions
2. **Complexité** : Double système (listeners + publication) créant de la redondance
3. **Performance** : Les listeners Firebase peuvent être lents et créer des latences
4. **Scalabilité** : Difficulté à gérer de nombreux listeners simultanés

### Avantages du nouveau système

1. **Contrôle total** : Nous contrôlons exactement quand publier
2. **Performance** : Redis PubSub est plus rapide et scalable
3. **Simplicité** : Un seul point de publication (les jobbeurs)
4. **Cohérence** : Le cache Redis est toujours mis à jour, même si l'utilisateur n'est pas connecté

## Architecture

### Avant (Ancien Système)

```
Jobbeur écrit dans Firebase/RTDB
    ↓
ListenersManager écoute Firestore/RTDB (on_snapshot/listen)
    ↓
Détection de changement
    ↓
Publication Redis PubSub (via listeners_manager.publish)
    ↓
Broadcast WebSocket
```

**Problèmes** :
- Double écriture (Firebase + Redis)
- Listeners actifs en permanence
- Dépendance aux listeners Firebase

### Après (Nouveau Système)

```
Jobbeur écrit dans Firebase/RTDB
    ↓
Jobbeur appelle publish_notification_event() / publish_messenger_event()
    ↓
Publication directe sur Redis PubSub (canal: notification:{uid} ou messenger:{uid})
    ↓
subscription_manager écoute Redis PubSub
    ↓
Mise à jour du cache Redis
    ↓
Broadcast WebSocket au frontend
```

**Avantages** :
- Publication directe et contrôlée
- Pas de listeners Firebase
- Cache toujours cohérent

## Modifications Apportées

### 1. `app/firebase_providers.py`

#### `_publish_notification_event()`

**Avant** : Utilisait `listeners_manager.publish()` (ancien système)

**Après** : Utilise `publish_notification_event()` de `pubsub_helper` (nouveau système PubSub)

```python
# Nouveau code
from app.realtime.pubsub_helper import publish_notification_event

# Détermine l'action (new/update)
action = "update" if doc.exists else "new"

# Transforme job_data en format notification
notification_data = {
    "docId": job_data.get('job_id') or job_data.get('file_id'),
    "message": job_data.get('message', ''),
    # ... autres champs
}

# Publie sur Redis PubSub
await publish_notification_event(user_id, action, notification_data)
```

#### Méthodes modifiées

- ✅ `add_or_update_job_by_file_id()` : Ajout de la publication PubSub après écriture
- ✅ `add_or_update_job_by_job_id()` : Publication PubSub déjà présente (conservée)
- ✅ `add_or_update_job_by_batch_id()` : Ajout de la publication PubSub après écriture
- ✅ `send_direct_message()` : Ajout de la publication PubSub après écriture dans RTDB

**Exemple pour `send_direct_message()`** :

```python
# Après écriture dans RTDB
new_message_ref = messages_ref.push(message_data)
message_id = new_message_ref.key

# Publication sur Redis PubSub
from app.realtime.pubsub_helper import publish_messenger_new

messenger_data = {
    "docId": message_id,
    "message": message_data.get('message', ''),
    # ... autres champs transformés
}

await publish_messenger_new(recipient_id, messenger_data)
```

### 2. `app/realtime/subscription_manager.py`

#### Nouvelles méthodes ajoutées

##### `_subscribe_to_pubsub_channels(uid: str)`

S'abonne aux canaux Redis PubSub pour un utilisateur.

**Canaux** :
- `notification:{uid}` - Pour les événements de notification
- `messenger:{uid}` - Pour les événements de message

##### `_pubsub_listener_loop(uid: str)`

Boucle d'écoute en arrière-plan qui écoute les messages Redis PubSub.

**Fonctionnement** :
1. Crée un client PubSub Redis
2. S'abonne aux canaux `notification:{uid}` et `messenger:{uid}`
3. Écoute les messages en continu
4. Route les messages vers les handlers appropriés

**Gestion des erreurs** :
- Timeout de 1 seconde pour permettre l'annulation
- Retry automatique en cas d'erreur
- Nettoyage propre lors de l'annulation

##### `_handle_pubsub_message(uid: str, message: Dict)`

Route les messages PubSub vers les handlers appropriés selon le canal.

##### `_handle_notification_event(uid: str, event_data: Dict)`

Gère les événements de notification :
1. Extrait l'action (`new`, `update`, `remove`)
2. Met à jour le cache Redis
3. Broadcast WebSocket au frontend

**Actions supportées** :
- `new` : Ajoute à la liste (en tête, limitée à 50)
- `update` : Met à jour un élément existant
- `remove` : Supprime de la liste

##### `_handle_messenger_event(uid: str, event_data: Dict)`

Gère les événements de message (même logique que notifications).

#### Modifications des méthodes existantes

##### `start_user_subscriptions(uid: str)`

**Ajout** : Appel à `_subscribe_to_pubsub_channels()` après le chargement initial

```python
# 1. Load notifications from Firestore (initial load)
notifications = await self._load_notifications(uid)

# 2. Load messages from RTDB (initial load)
messages = await self._load_messages(uid)

# 3. Cache in Redis
await self._cache_data(uid, notifications, messages)

# 4. Send FULL_DATA to frontend
await self._send_full_data(uid, notifications, messages)

# 5. ✨ NOUVEAU : Subscribe to Redis PubSub channels
await self._subscribe_to_pubsub_channels(uid)

# 6. Track as active
self._active_users.add(uid)
```

##### `stop_user_subscriptions(uid: str)`

**Ajout** : Arrêt des tâches PubSub

```python
# Stop PubSub subscription task
if uid in self._pubsub_tasks:
    task = self._pubsub_tasks.pop(uid)
    task.cancel()
    # ... nettoyage
```

### 3. `app/listeners_manager.py`

#### `_ensure_user_watchers(uid: str)`

**Modification** : Listeners Firestore/RTDB désactivés

**Avant** :
```python
# Attach Firestore listener
unsub_notif = q.on_snapshot(lambda docs, changes, rt: self._on_notifications(uid, docs, changes, rt))
unsubs.append(unsub_notif)

# Attach RTDB listener
unsub_msg = self._start_direct_messages_listener(uid)
unsubs.append(unsub_msg)
```

**Après** :
```python
# ❌ LISTENERS FIRESTORE/RTDB DÉSACTIVÉS - Migration vers PubSub Redis
# Les notifications et messages sont maintenant publiés directement sur Redis PubSub
# par les jobbeurs après écriture dans Firebase/RTDB
# Le subscription_manager écoute les canaux Redis PubSub au lieu des listeners Firebase

self.logger.info("user_attach_skip_firestore_listeners uid=%s reason=migrated_to_redis_pubsub", uid)

# Charger les données initiales une fois (pour compatibilité)
# Le subscription_manager s'occupe déjà de cela dans la Phase 4 de l'orchestration
self._publish_notifications_sync(uid)
self._publish_messages_sync(uid)
```

## Canaux Redis PubSub

### Format des canaux

- **Notifications** : `notification:{uid}`
- **Messages** : `messenger:{uid}`

### Format des messages

```json
{
  "type": "notification.delta",  // ou "messenger.delta"
  "payload": {
    "action": "new",  // "new", "update", ou "remove"
    "data": {
      "docId": "notif_abc123",
      "message": "Invoice processed successfully",
      "status": "completed",
      "functionName": "APbookeeper",
      // ... autres champs
    }
  }
}
```

## Flux Complet

### 1. Initialisation (Phase 4 de l'orchestration)

```
Utilisateur s'authentifie
    ↓
dashboard.orchestrate_init
    ↓
Phase 4: Realtime Subscriptions
    ↓
subscription_manager.start_user_subscriptions(uid)
    ↓
1. Charge données initiales depuis Firebase/RTDB
2. Met en cache Redis
3. Envoie FULL_DATA au frontend
4. ✨ S'abonne aux canaux Redis PubSub
```

### 2. Écriture et Publication

```
Jobbeur (Router/APbookeeper/Bankbookeeper)
    ↓
Écrit dans Firebase/RTDB
    ↓
Appelle publish_notification_event() ou publish_messenger_event()
    ↓
Publication sur Redis PubSub (canal: notification:{uid} ou messenger:{uid})
    ↓
subscription_manager reçoit le message
    ↓
Met à jour le cache Redis
    ↓
Broadcast WebSocket au frontend
```

### 3. Réception Frontend

```
Frontend reçoit événement WebSocket
    ↓
Type: notification.delta ou messenger.delta
    ↓
Payload: { action: "new", data: {...} }
    ↓
Mise à jour de l'UI
```

## Utilisation pour les Développeurs

### Pour les Jobbeurs (Publication)

#### Notifications

```python
from app.realtime.pubsub_helper import publish_notification_new, publish_notification_update, publish_notification_remove

# Après écriture dans Firestore
await firebase_mgmt.add_or_update_job_by_job_id(
    f"clients/{uid}/notifications",
    job_data
)

# Publication automatique via _publish_notification_event()
# Ou manuelle si nécessaire :
await publish_notification_new(uid, {
    "docId": "notif_abc123",
    "message": "Invoice processed",
    "status": "completed",
    "functionName": "APbookeeper",
    # ... autres champs
})
```

#### Messages

```python
from app.realtime.pubsub_helper import publish_messenger_new

# Après écriture dans RTDB
message_id = await rtdb.send_direct_message(user_id, recipient_id, message_data)

# Publication automatique via send_direct_message()
# Ou manuelle si nécessaire :
await publish_messenger_new(recipient_id, {
    "docId": message_id,
    "message": "New chat message",
    "functionName": "Chat",
    # ... autres champs
})
```

### Pour le Backend (Abonnement)

Le `subscription_manager` gère automatiquement les abonnements lors de la Phase 4 de l'orchestration. Aucune action manuelle requise.

## Migration et Compatibilité

### Compatibilité Ascendante

- ✅ Les données initiales sont toujours chargées depuis Firebase/RTDB
- ✅ Le format des événements WebSocket reste identique
- ✅ Le cache Redis est toujours utilisé
- ✅ La synchronisation périodique (2h) est conservée

### Points d'Attention

1. **Tous les jobbeurs doivent publier** : Assurez-vous que tous les points d'écriture appellent les fonctions de publication
2. **Format des données** : Les données doivent être transformées au format frontend avant publication
3. **Gestion des erreurs** : Les erreurs de publication ne doivent pas bloquer l'écriture dans Firebase

## Tests

### Scénarios de Test

1. **Notification nouvelle** :
   - Créer une notification via `add_or_update_job_by_job_id()`
   - Vérifier qu'elle arrive via WebSocket
   - Vérifier que le cache Redis est mis à jour

2. **Notification mise à jour** :
   - Mettre à jour une notification existante
   - Vérifier que l'événement `update` arrive
   - Vérifier que le cache est mis à jour

3. **Message nouveau** :
   - Envoyer un message via `send_direct_message()`
   - Vérifier qu'il arrive via WebSocket
   - Vérifier que le cache Redis est mis à jour

4. **Déconnexion/Reconnexion** :
   - Déconnecter un utilisateur
   - Vérifier que les tâches PubSub sont arrêtées
   - Reconnecter
   - Vérifier que les abonnements sont recréés

## Monitoring et Logs

### Logs Importants

- `[REALTIME] Starting subscriptions for uid={uid}` : Démarrage des subscriptions
- `[REALTIME] Subscribed to channels: notification:{uid}, messenger:{uid}` : Abonnement PubSub réussi
- `[REALTIME] Handled notification event: action={action} docId={docId}` : Événement traité
- `[NOTIFICATION] Published {action} event for uid={uid}` : Publication réussie
- `[MESSENGER] Published {action} event for uid={uid}` : Publication réussie

### Métriques à Surveiller

1. **Nombre d'abonnements actifs** : `len(subscription_manager._pubsub_tasks)`
2. **Taux de publication** : Nombre de publications par seconde
3. **Erreurs PubSub** : Erreurs dans `_pubsub_listener_loop`
4. **Latence** : Temps entre écriture Firebase et réception WebSocket

## Avantages de la Migration

### Performance

- ✅ **Réduction de la latence** : Pas d'attente des listeners Firebase
- ✅ **Moins de connexions** : Pas de listeners Firestore/RTDB actifs
- ✅ **Scalabilité** : Redis PubSub gère mieux la charge

### Simplicité

- ✅ **Un seul point de publication** : Les jobbeurs publient directement
- ✅ **Pas de double système** : Plus de listeners + publication
- ✅ **Code plus clair** : Logique centralisée dans `subscription_manager`

### Fiabilité

- ✅ **Contrôle total** : Nous décidons quand publier
- ✅ **Cache cohérent** : Toujours mis à jour, même si non publié
- ✅ **Gestion d'erreurs** : Meilleure gestion des erreurs de publication

## Prochaines Étapes (Optionnel)

1. **Nettoyage** : Supprimer complètement le code des listeners Firestore/RTDB si tout fonctionne
2. **Optimisation** : Ajouter des métriques et monitoring
3. **Documentation** : Mettre à jour la documentation des jobbeurs

## Références

- `app/realtime/pubsub_helper.py` : Helpers pour la publication
- `app/realtime/subscription_manager.py` : Gestionnaire des subscriptions
- `app/realtime/contextual_publisher.py` : Système de publication contextuelle
- `app/firebase_providers.py` : Méthodes d'écriture Firebase
- `app/listeners_manager.py` : Ancien système (listeners désactivés)

## Support

Pour toute question ou problème lié à cette migration, consulter :
- Les logs `[REALTIME]` pour les subscriptions
- Les logs `[NOTIFICATION]` et `[MESSENGER]` pour les publications
- Le code source dans `app/realtime/`
