# Guide de déploiement - Microservice Firebase Unifié

## 🚀 **Déploiement en mode sécurisé (registre unifié désactivé)**

### **Phase 1 : Déploiement initial (0 risque)**

Le système est configuré pour fonctionner **exactement comme avant** par défaut :

```bash
# Variables d'environnement par défaut (mode sécurisé)
UNIFIED_REGISTRY_ENABLED=false
REGISTRY_DEBUG=false
```

### **Commandes de déploiement**

```bash
# 1. Build et push de l'image
docker build -t pinnokio_microservice_unified .
docker tag pinnokio_microservice_unified:latest 654654322636.dkr.ecr.us-east-1.amazonaws.com/pinnokio_microservice:unified
docker push 654654322636.dkr.ecr.us-east-1.amazonaws.com/pinnokio_microservice:unified

# 2. Mise à jour de la task definition
aws ecs register-task-definition --cli-input-json file://ecs-taskdef-unified.json

# 3. Mise à jour du service
aws ecs update-service \
    --cluster pinnokio_cluster \
    --service pinnokio_microservice \
    --task-definition pinnokio_microservice_unified:LATEST
```

### **Vérification du déploiement**

```bash
# Vérifier que le service fonctionne
curl https://<ALB_URL>/healthz

# Réponse attendue (identique à avant)
{
  "status": "ok",
  "version": "1.0.0",
  "listeners_count": 5,
  "workflow_listeners_count": 3,
  "redis": "ok",
  "uptime_s": 3600
}
```

## ⚡ **Activation progressive du registre unifié**

### **Phase 2 : Test avec un utilisateur**

```bash
# Mettre à jour SEULEMENT la variable d'environnement
aws ecs update-service \
    --cluster pinnokio_cluster \
    --service pinnokio_microservice \
    --task-definition pinnokio_microservice_unified:LATEST \
    --force-new-deployment

# Dans la task definition, changer :
"UNIFIED_REGISTRY_ENABLED": "true"
"REGISTRY_DEBUG": "true"  # Pour logs détaillés
```

### **Phase 3 : Monitoring**

```bash
# Vérifier les logs
aws logs filter-log-events \
    --log-group-name /ecs/pinnokio_microservice_unified \
    --filter-pattern "unified_registry"

# Vérifier Redis pour le nouveau registre
redis-cli -h pinnokio-cache-7uum2j.serverless.use1.cache.amazonaws.com \
    --tls \
    KEYS "registry:unified:*"
```

## 🔧 **Configuration des tâches parallèles**

### **Activation des workers Celery**

Les workers sont **déjà déployés** mais **inactifs** tant que le registre unifié est désactivé.

```bash
# Vérifier les workers
aws logs filter-log-events \
    --log-group-name /ecs/pinnokio_microservice_unified \
    --log-stream-name-prefix worker

# Vérifier le scheduler
aws logs filter-log-events \
    --log-group-name /ecs/pinnokio_microservice_unified \
    --log-stream-name-prefix beat
```

## 🧪 **Tests de validation**

### **Test 1 : Compatibilité RPC (côté Reflex inchangé)**

```python
# Dans l'application Reflex - AUCUN CHANGEMENT
result = rpc_call("REGISTRY.register_user", args=[user_id, session_id, route])
# Doit fonctionner exactement comme avant
```

### **Test 2 : Test des nouvelles tâches (quand activé)**

```python
# Test d'une tâche de calcul
result = rpc_call("TASK.start_document_analysis", 
                 args=[user_id, {"content": "test doc"}, "job123"])

# Résultat attendu
{
    "success": True,
    "task_id": "doc_analysis_job123",
    "celery_task_id": "uuid-task-id",
    "status": "queued"
}

# L'UI recevra automatiquement les mises à jour via WebSocket
# Type d'événement: "task.progress_update"
```

### **Test 3 : Registre unifié (quand activé)**

```python
# Test du registre unifié
result = rpc_call("UNIFIED_REGISTRY.get_user_registry", args=[user_id])

# Résultat attendu
{
    "user_info": {"user_id": "user123", "status": "online"},
    "companies": {"current_company_id": "company_abc"},
    "services": {"chroma": {"collections": ["collection1"]}}
}
```

## 🔄 **Rollback d'urgence**

En cas de problème, rollback **instantané** :

```bash
# Option 1 : Désactiver le registre unifié
aws ecs update-service \
    --cluster pinnokio_cluster \
    --service pinnokio_microservice \
    --task-definition pinnokio_microservice_unified:LATEST \
    --force-new-deployment

# Dans la task definition, changer :
"UNIFIED_REGISTRY_ENABLED": "false"

# Option 2 : Revenir à l'ancienne task definition
aws ecs update-service \
    --cluster pinnokio_cluster \
    --service pinnokio_microservice \
    --task-definition pinnokio_microservice:PREVIOUS_VERSION
```

## 📊 **Monitoring et métriques**

### **Métriques clés à surveiller**

1. **Santé API** : `GET /healthz` doit rester `"status": "ok"`
2. **Latence RPC** : Les appels RPC ne doivent pas être plus lents
3. **Erreurs** : Pas d'augmentation des erreurs 5xx
4. **Redis** : Utilisation mémoire stable

### **Logs importants**

```bash
# Logs de migration
aws logs filter-log-events --filter-pattern "registry_wrapper"
aws logs filter-log-events --filter-pattern "unified_registry"
aws logs filter-log-events --filter-pattern "sync.*ChromaDB"

# Logs d'erreur
aws logs filter-log-events --filter-pattern "ERROR"
aws logs filter-log-events --filter-pattern "fallback"
```

## 🎯 **Plan de migration complet**

### **Semaine 1 : Déploiement sécurisé**
- [ ] Déployer avec `UNIFIED_REGISTRY_ENABLED=false`
- [ ] Vérifier que tout fonctionne comme avant
- [ ] Monitoring pendant 2-3 jours

### **Semaine 2 : Tests progressifs**
- [ ] Activer sur 1 utilisateur test : `UNIFIED_REGISTRY_ENABLED=true`
- [ ] Vérifier les logs et métriques
- [ ] Tester les nouvelles APIs de tâches

### **Semaine 3 : Activation production**
- [ ] Activer pour tous : `UNIFIED_REGISTRY_ENABLED=true`
- [ ] Monitoring intensif
- [ ] Tests de charge

### **Semaine 4 : Optimisation**
- [ ] Désactiver les logs debug : `REGISTRY_DEBUG=false`
- [ ] Optimiser les performances
- [ ] Documentation finale

## ⚠️ **Points de vigilance**

1. **Aucun changement côté Reflex** : L'application Reflex n'a **AUCUN** changement à faire
2. **Compatibilité totale** : Tous les appels RPC existants fonctionnent identiquement
3. **Rollback instantané** : Une variable d'environnement suffit pour revenir en arrière
4. **Monitoring** : Surveiller les métriques de performance et d'erreur
5. **Redis** : Vérifier l'utilisation mémoire (nouveau registre + ancien en parallèle)

## 📞 **Support et dépannage**

### **Problèmes courants**

1. **API lente** → Vérifier `UNIFIED_REGISTRY_ENABLED=false`
2. **Erreurs Redis** → Vérifier les credentials et TLS
3. **Workers inactifs** → Normal si registre unifié désactivé
4. **Logs manquants** → Activer `REGISTRY_DEBUG=true`

### **Commandes de diagnostic**

```bash
# Santé globale
curl https://<ALB_URL>/debug

# État des tâches Celery
docker exec -it <container> celery -A app.task_service inspect active

# État Redis
redis-cli -h <redis_host> --tls INFO memory
```

