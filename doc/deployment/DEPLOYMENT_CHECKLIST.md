# ✅ Checklist de Déploiement - Correctifs WebSocket

## 📦 Fichiers Modifiés

### Backend (firebase_microservice)
- ✅ `app/main.py` - Logs améliorés + endpoint `/ws-metrics`
- ✅ `app/ws_metrics.py` - **NOUVEAU** Module de métriques WebSocket
- ✅ `app/listeners_manager.py` - Délai 5s avec annulation de cleanup
- ✅ `WEBSOCKET_FIXES.md` - Documentation complète
- ✅ `DEPLOYMENT_CHECKLIST.md` - Cette checklist

### Frontend (pinnokio_app)
- ✅ `pinnokio_app/listeners/bus_consumer.py` - ping_timeout 20→60s

## 🚀 Étapes de Déploiement

### Phase 1 : Backend (Automatique via CI/CD)

```bash
# 1. Commit et push vers master
cd C:\Users\Cedri\Coding\firebase_microservice
git add .
git commit -m "fix: WebSocket déconnexions + race conditions + métriques"
git push origin master

# 2. GitHub Actions déploiera automatiquement sur ECS
# Surveiller : https://github.com/YOUR_REPO/actions
```

**Durée estimée :** 5-10 minutes

### Phase 2 : Frontend (Manuel)

```bash
# 1. Sur le serveur frontend
cd /path/to/pinnokio_app

# 2. Pull les changements
git pull origin master

# 3. Redémarrer l'application
# (Ajuster selon votre méthode de déploiement)
pm2 restart pinnokio_app
# OU
systemctl restart pinnokio_app
# OU
reflex run --reload
```

**Durée estimée :** 2-3 minutes

## 🔍 Vérifications Post-Déploiement

### ✅ Backend

1. **Service démarré correctement**
```bash
curl https://your-backend.com/healthz
# Attendu : {"status": "ok", ...}
```

2. **Endpoint métriques disponible**
```bash
curl https://your-backend.com/ws-metrics
# Attendu : {"status": "ok", "metrics": {...}}
```

3. **Logs structurés**
```bash
# CloudWatch Logs
# Chercher : "🔴 ws_disconnect", "⏰ user_detach_delay_start"
```

4. **ECS Tasks stables**
```bash
# AWS Console → ECS → Cluster → Service
# Vérifier : Aucun "ServiceSchedulerInitiated" dans les dernières heures
```

### ✅ Frontend

1. **Application accessible**
```bash
curl https://your-frontend.com
# Attendu : 200 OK
```

2. **WebSocket se connecte**
```bash
# Console navigateur → Network → WS
# Vérifier : Connexion établie et maintenue
```

3. **Pas d'erreurs console**
```bash
# Console navigateur
# Vérifier : Pas d'erreurs WebSocket
```

## 📊 Monitoring (Premières 24h)

### CloudWatch Logs - Requêtes Utiles

**1. Déconnexions par type**
```
fields @timestamp, uid, code, reason, type
| filter @message like /ws_disconnect/
| stats count() by type
```

**2. Cleanup annulés (reconnexions rapides)**
```
fields @timestamp, uid
| filter @message like /user_detach_cancelled/
| count
```

**3. Cleanup exécutés**
```
fields @timestamp, uid
| filter @message like /user_detach_executing/
| count
```

**4. Erreurs de cleanup**
```
fields @timestamp, uid, error
| filter @message like /REGISTRY_CLEANUP_ERROR/
```

### Métriques Clés à Surveiller

| Métrique | Valeur Attendue | Action si Dépassé |
|----------|-----------------|-------------------|
| Tasks stoppés (ServiceSchedulerInitiated) | 0 | Rollback immédiat |
| Health check failures | < 1% | Investiguer logs |
| Déconnexions 1006 | < 10% des connexions | Analyser réseau |
| Ratio annulation/exécution | > 30% | Ajuster délai si nécessaire |

## 🚨 Plan de Rollback

### Si Problème Détecté

**Backend (Urgent)**
```bash
cd C:\Users\Cedri\Coding\firebase_microservice
git revert HEAD
git push origin master --force
# CI/CD redéploiera automatiquement
```

**Frontend**
```python
# bus_consumer.py lignes 274 et 347
# Remplacer :
ping_timeout=60
# Par :
ping_timeout=20
```

## 📞 Contacts d'Urgence

- **DevOps Lead :** [Nom] - [Email/Téléphone]
- **Tech Lead :** [Nom] - [Email/Téléphone]
- **On-Call :** [Système de paging]

## 🎯 Critères de Succès

### Après 1 Heure
- ✅ Aucun task ECS redémarré
- ✅ Health checks à 100%
- ✅ WebSockets stables

### Après 6 Heures
- ✅ < 5% de déconnexions 1006
- ✅ > 20% de cleanup annulés (reconnexions rapides)
- ✅ Latence des listeners < 100ms

### Après 24 Heures
- ✅ Aucun incident lié aux WebSocket
- ✅ Métriques `/ws-metrics` montrent des patterns normaux
- ✅ Feedback utilisateurs positif

## 📝 Notes de Déploiement

### Heure de Déploiement Recommandée
- **Préféré :** Heures creuses (2h-6h du matin, heure locale)
- **Éviter :** Vendredi après-midi, veilles de jours fériés
- **Durée maintenance :** Aucune (déploiement sans interruption)

### Équipe Requise
- 1 développeur backend (monitoring logs)
- 1 DevOps (surveillance infrastructure)
- Durée : 2-3 heures de surveillance active

### Risques Identifiés
| Risque | Probabilité | Impact | Mitigation |
|--------|-------------|--------|------------|
| Délai 5s trop long | Faible | Faible | Ajustable via variable env |
| Métriques consomment mémoire | Faible | Faible | Auto-cleanup à implémenter |
| ping_timeout trop long | Faible | Moyen | Valeur temporaire, ajustable |

---

## ✍️ Signature de Déploiement

**Déployé par :** ___________________  
**Date :** ___________________  
**Heure :** ___________________  
**Version :** ___________________  

**Validation :**
- [ ] Tests locaux réussis
- [ ] Revue de code effectuée
- [ ] Documentation à jour
- [ ] Plan de rollback prêt
- [ ] Équipe on-call notifiée

**Post-Déploiement (à remplir après 24h) :**
- [ ] Aucun incident majeur
- [ ] Métriques dans les normes
- [ ] Logs analysés
- [ ] Rapport post-mortem créé (si incidents)

