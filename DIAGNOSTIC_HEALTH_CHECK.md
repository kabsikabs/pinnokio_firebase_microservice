# 🔍 Diagnostic des échecs de Health Check ECS

## 📋 Résumé du problème

Vos tâches ECS sont tuées après **2-6 minutes** avec le message d'erreur :
```
Task failed ELB health checks (code 137 - SIGKILL)
```

## ✅ Analyse des logs

### Ce qui fonctionne correctement :
- ✅ L'application démarre sans erreur
- ✅ Le endpoint `/healthz` répond avec `200 OK`
- ✅ Les health checks internes réussissent
- ✅ Aucune erreur critique dans l'application

### Le problème identifié :
Les logs montrent que les health checks **réussissent**, mais ECS tue quand même les tâches !

## 🎯 Cause racine

La configuration du **Target Group** et du **Service ECS** est trop stricte :

| Paramètre | Valeur Actuelle | Problème |
|-----------|----------------|----------|
| **Grace Period ECS** | 60 secondes | ⚠️ Trop court pour initialiser l'app |
| **Unhealthy Threshold** | 2 échecs | ⚠️ Pas assez tolérant |
| **Timeout** | 5 secondes | ⚠️ Peut être trop court sous charge |
| **Healthy Threshold** | 5 succès | ⏰ Trop long pour démarrer |

### Timeline du problème :
```
0s      : Conteneur démarre
0-6s    : Pull de l'image Docker
6-30s   : Application s'initialise (Redis, Firebase, ChromaDB, etc.)
60s     : FIN du grace period ⚠️
60-90s  : Premier health check après grace period
90-120s : Deuxième health check - SI 2 ÉCHECS → TÂCHE TUÉE 💀
```

**Résultat** : La tâche est tuée après ~120 secondes (2 minutes) si 2 health checks échouent !

## 🛠️ Solution recommandée

### Nouvelle configuration optimale :

```powershell
# 1. Augmenter le grace period à 5 minutes
aws ecs update-service \
    --cluster pinnokio_cluster \
    --service pinnokio_microservice \
    --health-check-grace-period-seconds 300 \
    --region us-east-1

# 2. Rendre le Target Group plus tolérant
aws elbv2 modify-target-group \
    --target-group-arn arn:aws:elasticloadbalancing:us-east-1:654654322636:targetgroup/new-pinnokio-firebase-backend/6c7046f6f3969fee \
    --health-check-interval-seconds 30 \
    --health-check-timeout-seconds 10 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 5 \
    --region us-east-1
```

### Paramètres optimisés :

| Paramètre | Avant | Après | Bénéfice |
|-----------|-------|-------|----------|
| **Grace Period** | 60s | **300s** | 5 minutes pour initialiser |
| **Timeout** | 5s | **10s** | Plus de temps pour répondre |
| **Healthy Threshold** | 5 | **2** | Démarre plus vite (60s au lieu de 150s) |
| **Unhealthy Threshold** | 2 | **5** | Tolère les pics temporaires |

### Nouvelle timeline :
```
0s      : Conteneur démarre
0-30s   : Application s'initialise
300s    : FIN du grace period (5 minutes) ✅
300s+   : Health checks commencent
→ Besoin de 5 échecs consécutifs (2.5 minutes) pour tuer la tâche
→ Total: Minimum 7.5 minutes avant qu'une tâche soit tuée
```

## 🚀 Appliquer la solution

### Option 1 : Utiliser le script PowerShell
```powershell
.\fix_health_check.ps1
```

### Option 2 : Commandes manuelles
Exécutez les deux commandes ci-dessus dans votre terminal.

## 📊 Vérification

Après avoir appliqué les corrections, vérifiez :

```bash
# 1. Vérifier le grace period
aws ecs describe-services \
    --cluster pinnokio_cluster \
    --services pinnokio_microservice \
    --region us-east-1 \
    --query "services[0].healthCheckGracePeriodSeconds"

# 2. Vérifier le Target Group
aws elbv2 describe-target-groups \
    --target-group-arns arn:aws:elasticloadbalancing:us-east-1:654654322636:targetgroup/new-pinnokio-firebase-backend/6c7046f6f3969fee \
    --region us-east-1 \
    --query "TargetGroups[0].[HealthCheckTimeoutSeconds,HealthyThresholdCount,UnhealthyThresholdCount]"

# 3. Surveiller les tâches
aws ecs list-tasks \
    --cluster pinnokio_cluster \
    --service-name pinnokio_microservice \
    --desired-status RUNNING \
    --region us-east-1
```

## 📈 Résultats attendus

Après avoir appliqué ces corrections :
- ✅ Les tâches ne seront plus tuées prématurément
- ✅ Plus de tolérance aux pics de charge temporaires
- ✅ Déploiements plus stables
- ✅ Moins de redémarrages inutiles

## 📝 Notes additionnelles

### Pourquoi ces valeurs ?

1. **Grace Period (300s)** : Permet à l'application de :
   - Initialiser les connexions Firebase
   - Se connecter à Redis
   - Charger ChromaDB
   - Démarrer tous les listeners

2. **Unhealthy Threshold (5)** : 
   - Tolère les pics temporaires de charge
   - Évite les faux positifs
   - 5 échecs × 30s = 2.5 minutes avant de tuer une tâche saine

3. **Healthy Threshold (2)** :
   - Nouvelle tâche devient "healthy" après 2 succès (60s)
   - Accélère les déploiements

4. **Timeout (10s)** :
   - Laisse le temps à l'application de répondre même sous charge
   - Compatible avec des temps de réponse variables

## 🔄 Prochaines étapes

1. Appliquer les corrections
2. Surveiller les tâches pendant 15-20 minutes
3. Vérifier qu'aucune tâche n'est tuée prématurément
4. Si tout fonctionne, documenter la configuration

## 📞 Commandes utiles pour le monitoring

```bash
# Surveiller les événements du service
aws ecs describe-services \
    --cluster pinnokio_cluster \
    --services pinnokio_microservice \
    --region us-east-1 \
    --query 'services[0].events[:10]'

# Voir les tâches arrêtées récemment
aws ecs list-tasks \
    --cluster pinnokio_cluster \
    --service-name pinnokio_microservice \
    --desired-status STOPPED \
    --region us-east-1

# Télécharger les logs d'une tâche
python download_logs.py
```

---

**Date du diagnostic** : 27 novembre 2025  
**Fichiers de logs analysés** :
- `logs_task_6ac9ae34675d448b9a904c4d8f538524.txt`
- `logs_task_46e2329eaa0c4352affa79f697746163.txt`
- `logs_task_35effd67684940bfaf39cf48dd2830af.txt`

