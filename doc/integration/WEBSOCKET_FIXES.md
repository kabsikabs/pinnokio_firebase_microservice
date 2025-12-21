# 🔧 Correctifs WebSocket - Déconnexions et Race Conditions

## 📋 Résumé du Problème

Le service ECS tombait en panne avec `ServiceSchedulerInitiated` à cause de health checks ELB échoués. L'analyse a révélé :

1. **Déconnexions brutales** (code 1006 - ABNORMAL_CLOSURE)
2. **Race conditions** entre déconnexion et reconnexion rapide
3. **Blocage du backend** pendant le cleanup des listeners
4. **Health checks échoués** → Task ECS tué par AWS

## ✅ Solutions Implémentées

### 1. 🔍 Logs Améliorés (`app/main.py`)

**Avant :**
```python
logger.info("ws_disconnect uid=%s", uid)
```

**Après :**
```python
logger.warning("🔴 ws_disconnect uid=%s code=%s reason=%s type=%s", uid, code, reason, disconnect_reason)
```

**Bénéfices :**
- Identification du type de déconnexion (normal, abnormal, timeout, etc.)
- Logs visuels avec emojis pour repérage rapide
- Logs structurés pour analyse

### 2. 📊 Métriques WebSocket (`app/ws_metrics.py`)

Nouveau module pour tracer :
- Nombre de déconnexions par utilisateur
- Raisons de déconnexion (1000, 1006, timeout, etc.)
- Horodatage de la dernière déconnexion
- Détection des déconnexions fréquentes

**Endpoint :** `GET /ws-metrics`

**Exemple de réponse :**
```json
{
  "status": "ok",
  "metrics": {
    "total_users_tracked": 5,
    "top_disconnects": [
      ["user123", 3],
      ["user456", 2]
    ],
    "all_reasons": {
      "user123": {
        "abnormal_closure": 2,
        "normal_closure": 1
      }
    }
  }
}
```

### 3. ⏰ Délai avec Annulation (`app/listeners_manager.py`)

**Problème :**
```
T+0s  : Déconnexion
T+0s  : Backend lance cleanup
T+0.5s: Frontend reconnecte
T+0.5s: Backend attache de nouveaux listeners
→ CONFLIT : Cleanup et attachment simultanés
→ DEADLOCK → Health checks échouent
```

**Solution :**
```python
def _do_detach():
    # ⏰ Attendre 5 secondes avant le cleanup
    time.sleep(5)
    
    # 🔍 Vérifier si reconnexion pendant le délai
    with self._lock:
        if uid in self._user_unsubs:
            logger.info("✅ Cleanup annulé (reconnexion)")
            return
    
    # 🧹 Procéder au cleanup seulement si pas de reconnexion
    # ... cleanup code ...
```

**Bénéfices :**
- Évite le cleanup inutile en cas de reconnexion rapide
- Élimine la race condition
- Réduit la charge CPU/réseau

### 4. 🔬 Diagnostic Timeout (`pinnokio_app/listeners/bus_consumer.py`)

**Avant :**
```python
websockets.connect(ws_full, ping_interval=20, ping_timeout=20)
```

**Après :**
```python
websockets.connect(ws_full, ping_interval=20, ping_timeout=60)
```

**Objectif :**
- **ping_interval=20** : Envoie un PING toutes les 20 secondes
- **ping_timeout=60** : Attend jusqu'à 60 secondes pour le PONG (au lieu de 20s)

**Diagnostic :**
- Si déconnexions **persistent** avec 60s → Problème réseau ou fermeture explicite
- Si déconnexions **disparaissent** → C'était un timeout dû au blocage backend

## 📊 Monitoring et Diagnostic

### Consulter les métriques

```bash
curl https://your-service.com/ws-metrics
```

### Logs à surveiller

| Emoji | Message | Signification |
|-------|---------|---------------|
| 🔴 | `ws_disconnect` | Déconnexion WebSocket |
| ⏰ | `user_detach_delay_start` | Début du délai de 5s |
| ✅ | `user_detach_cancelled` | Cleanup annulé (reconnexion) |
| 🧹 | `user_detach_executing` | Cleanup en cours |
| 🔵 | `REGISTRY_CLEANUP_START` | Nettoyage registre Firestore |
| 🟢 | `REGISTRY_CLEANUP_SUCCESS` | Nettoyage réussi |
| 🔴 | `REGISTRY_CLEANUP_ERROR` | Erreur nettoyage |
| 🟡 | `ws_cleanup_complete` | Nettoyage WebSocket terminé |

### Codes de déconnexion WebSocket

| Code | Nom | Cause |
|------|-----|-------|
| 1000 | Normal Closure | Fermeture propre (logout, navigation) |
| 1001 | Going Away | Fermeture page/onglet |
| 1006 | Abnormal Closure | **Timeout ping/pong, crash backend, coupure réseau** |
| 1011 | Server Error | Exception non gérée côté serveur |

## 🧪 Tests Recommandés

### Test 1 : Reconnexion Rapide
1. Se connecter à l'application
2. Fermer/rouvrir l'onglet rapidement (< 5s)
3. **Attendu :** Log `user_detach_cancelled`

### Test 2 : Déconnexion Longue
1. Se connecter à l'application
2. Fermer l'onglet et attendre 10 secondes
3. **Attendu :** Logs `user_detach_executing` → `REGISTRY_CLEANUP_SUCCESS`

### Test 3 : Stabilité ECS
1. Déployer les changements
2. Surveiller les health checks ELB pendant 30 minutes
3. **Attendu :** Aucun échec de health check

### Test 4 : Timeout Diagnostic
1. Analyser les logs avec `ping_timeout=60`
2. Si `code=1006` persiste → Problème réseau/frontend
3. Si `code=1006` disparaît → C'était un blocage backend (résolu)

## 🚀 Déploiement

### Backend (firebase_microservice)

```bash
# Les fichiers modifiés :
app/main.py                  # Logs améliorés + endpoint /ws-metrics
app/ws_metrics.py            # Nouveau module de métriques
app/listeners_manager.py     # Délai 5s avec annulation
```

**Déploiement automatique** via GitHub Actions sur push vers `master`.

### Frontend (pinnokio_app)

```bash
# Les fichiers modifiés :
pinnokio_app/listeners/bus_consumer.py  # ping_timeout: 20→60s
```

**Redémarrage nécessaire** de l'application Reflex.

## 📈 Métriques à Surveiller Post-Déploiement

1. **ECS Task Stability**
   - Tasks arrêtés (ServiceSchedulerInitiated) → Devrait être 0
   - Health check failures → Devrait être 0

2. **CloudWatch Logs**
   - Fréquence de `ws_disconnect code=1006`
   - Ratio `user_detach_cancelled` / `user_detach_executing`
   - Temps de cleanup (`REGISTRY_CLEANUP_SUCCESS`)

3. **Application Metrics**
   - Latence des listeners
   - Nombre de reconnexions
   - Taux d'erreur des RPC calls

## 🔄 Rollback Rapide

Si les changements causent des problèmes :

### Backend
```bash
git revert HEAD
git push origin master
# Attendre le redéploiement automatique
```

### Frontend
```python
# Remettre ping_timeout=20 dans bus_consumer.py
websockets.connect(ws_full, ping_interval=20, ping_timeout=20)
```

## 📝 Notes Importantes

1. **Le délai de 5 secondes** peut légèrement retarder le cleanup en production, mais c'est un compromis acceptable pour éviter les race conditions.

2. **Le ping_timeout de 60 secondes** est temporaire pour le diagnostic. Une fois la cause identifiée, on peut le réduire à 30-40 secondes.

3. **Les métriques WebSocket** consomment de la mémoire. Le nettoyage automatique sera ajouté si nécessaire.

4. **Logs avec emojis** : Assurez-vous que CloudWatch affiche correctement les caractères UTF-8.

## 🎯 Prochaines Étapes

1. ✅ Déployer les changements
2. 🔍 Surveiller les logs pendant 24h
3. 📊 Analyser les métriques `/ws-metrics`
4. 🔧 Ajuster `ping_timeout` selon les résultats
5. 🧹 Implémenter un nettoyage automatique des métriques si nécessaire
6. 📈 Créer un dashboard CloudWatch pour visualiser les métriques

---

**Date :** 20 novembre 2025  
**Auteur :** Assistant IA + Cedric  
**Référence :** ServiceSchedulerInitiated / ELB Health Check Failures

