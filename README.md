## Déploiement – pinnokio_microservice

### Aperçu
Service FastAPI exposant `/ws` et `/healthz`, packagé en conteneur, déployé sur AWS ECS Fargate dans le cluster `pinnokio_cluster`. Le service s’appuie sur Redis (AWS ElastiCache) et sur GCP Secret Manager pour certaines infos projet.

### Prérequis AWS
- ECR repo: `pinnokio_microservice`
- ECS cluster: `pinnokio_cluster`
- ECS service (Fargate): `pinnokio_microservice`
- VPC/Subnets privés (accès ElastiCache) + SG autorisant le port 8090 côté ALB interne si exposé
- ElastiCache (Valkey/Redis): endpoint interne + port 6379
- Rôles IAM:
  - Exécution tâche: `ECSExecutionRole` (pull ECR, logs)
  - Rôle tâche: `ECSTaskRole` (accès CloudWatch Logs, éventuels secrets)
  - Rôle OIDC GitHub à assumer: stocker l’ARN dans `AWS_ROLE_TO_ASSUME` (secret GitHub)

### Variables d’environnement PROD (ECS)
Définies dans `ecs-taskdef.json` (peuvent être déplacées vers SSM/Secrets si besoin):
- `AWS_REGION_NAME`: `us-east-1`
- `GOOGLE_PROJECT_ID`: ID projet GCP (pour Secret Manager)
- `GOOGLE_SERVICE_ACCOUNT_SECRET`: nom du secret GSM contenant la clé JSON (optionnel)
- `AWS_SECRET_NAME`: nom d’un secret GSM contenant des creds AWS (optionnel)
- `LISTENERS_REDIS_HOST`: hostname ElastiCache interne (ex: `pinnokio-cache-xxxx.use1.cache.amazonaws.com`)
- `LISTENERS_REDIS_PORT`: `6379`
- `LISTENERS_REDIS_PASSWORD`: vide si ElastiCache sans auth
- `LISTENERS_REDIS_TLS`: `true` si cluster TLS (par défaut oui côté ElastiCache Valkey/Redis)
- `LISTENERS_REDIS_TLS_VERIFY`: `true` en prod (désactivable localement)
- `LISTENERS_REDIS_DB`: `0`
- `LISTENERS_CHANNEL_PREFIX`: `user:`
- `USE_LOCAL_REDIS`: `false` en prod
- `PORT`: `8090`

Localement, on peut forcer un Redis local via `.env`:
```
USE_LOCAL_REDIS=true
LISTENERS_REDIS_DB=0
```

### Workflow GitHub Actions
Le fichier `.github/workflows/deploy.yml`:
- construit et pousse l’image sur ECR
- rend la task definition avec l’image
- déploie sur ECS service `pinnokio_microservice` dans `pinnokio_cluster`

Secrets requis dans GitHub:
- `AWS_ROLE_TO_ASSUME`: ARN du rôle IAM assumable par OIDC GitHub

### Exposition réseau / WebSocket
- Application écoute sur le port `8090` (voir `Dockerfile` et `ecs-taskdef.json`).
- Recommandé: placer un ALB interne dans le VPC devant le service pour gérer les connexions WebSocket et le scaling.

### Commandes utiles
Test santé:
```
GET http://<ALB-ou-service-endpoint>/healthz
```

### Notes
- Les logs sont envoyés vers CloudWatch Logs `/ecs/pinnokio_microservice`.
- Adapter les ARNs `executionRoleArn` et `taskRoleArn` dans `ecs-taskdef.json`.

