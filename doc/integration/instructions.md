## Cahier des charges — Microservice "listeners" temps réel (Fargate + ALB + Redis Pub/Sub)

### 1) Contexte et objectifs
- Application Reflex (frontend export), backend conteneurisé, données sur Firebase (Firestore/Realtime DB) pour notifications, messages, chat.
- Aujourd’hui, les listeners sont démarrés par le State et poussent dans une queue locale process; nous passons à un microservice autonome pour la fiabilité/évolutivité.
- Objectif: latence faible, résilience, scalabilité horizontale, en s’intégrant nativement avec la logique Reflex (States inchangés côté métier).

### 2) Décisions d’architecture (validées)
- Bus temps réel: Redis Pub/Sub (ElastiCache) par utilisateur.
  - Canal: `user:{uid}` (préfixe configurable via secret `channel_prefix`).
  - Le microservice publie les événements (notifications/messages/chat) par utilisateur.
- Accès externe: ALB (HTTPS + WebSocket) devant Fargate (latence plus faible qu’API Gateway pour ce cas d’usage).
- Registre online/TTL: Firestore `listeners_registry` (doc par `user_id`) avec `heartbeat` et TTL logique (90s par défaut).
- Source de vérité: Firestore. Redis ne stocke pas, il diffuse (pas de replay; en cas de reconnexion on relit Firestore).

### 3) Secrets & configuration (Google Secret Manager)
Utiliser Google Secret Manager (GSM) via vos utilitaires `g_cred.create_secret(secret_data)` et `g_cred.get_secret(secret_name)`.

Variables d’environnement (fournies par vous):
- `AWS_REGION_NAME` (obligatoire) — région AWS du déploiement Fargate/ECR/ECS.
- `GOOGLE_PROJECT_ID` (obligatoire) — ID du projet GCP hébergeant les secrets.
- `GOOGLE_SERVICE_ACCOUNT_SECRET` (optionnel) — nom du secret GSM contenant le JSON du compte de service (si vous devez injecter des identifiants dynamiquement).
- `AWS_SECRET_NAME` (optionnel) — nom du secret GSM contenant des identifiants/paramètres AWS si nécessaire.

Chargement d’un secret dans le code (exemples):
```python
from pinnokio_app.code.tools.g_cred import get_secret

redis_cfg_json = get_secret("pinnokio/listeners/redis")
firebase_admin_json = get_secret("pinnokio/listeners/firebase_admin")
service_cfg_json = get_secret("pinnokio/listeners/service_config")  # optionnel

# Si vous devez lire un secret nommé dans une variable:
import os, json
aws_secret_name = os.getenv("AWS_SECRET_NAME")
if aws_secret_name:
    aws_cfg_json = get_secret(aws_secret_name)
    aws_cfg = json.loads(aws_cfg_json)
```

Création d’un secret (si besoin) avec `create_secret` (les données sont une chaîne JSON):
```python
from pinnokio_app.code.tools.g_cred import create_secret

secret_name = create_secret("{\n  \"host\": \"...\",\n  \"port\": 6379\n}")
print("Secret créé:", secret_name)
```

- Secret Redis: `pinnokio/listeners/redis`
```json
{
  "host": "redis-xxxxx.abcdef.ng.0001.use1.cache.amazonaws.com",
  "port": 6379,
  "password": "REPLACE_WITH_STRONG_PASSWORD",
  "tls": true,
  "db": 0,
  "channel_prefix": "user:"
}
```

- Secret Firebase Admin: `pinnokio/listeners/firebase_admin`
```json
{
  "type": "service_account",
  "project_id": "your-firebase-project",
  "private_key_id": "xxxx",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-xxxx@your-firebase-project.iam.gserviceaccount.com",
  "client_id": "1234567890",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-xxxx%40your-firebase-project.iam.gserviceaccount.com"
}
```

- Secret Service (optionnel): `pinnokio/listeners/service_config`
```json
{
  "allowed_origins": ["https://app.pinnokioagent.com"],
  "alb_ws_path": "/ws",
  "heartbeat_ttl_seconds": 90
}
```

Fallback (variables d’environnement autorisées si besoin):
- Redis: `LISTENERS_REDIS_HOST`, `LISTENERS_REDIS_PORT`, `LISTENERS_REDIS_PASSWORD`, `LISTENERS_REDIS_TLS`, `LISTENERS_REDIS_DB`, `LISTENERS_CHANNEL_PREFIX`.
- Service: `ALB_WS_URL` (ex: `wss://listeners.pinnokioagent.com/ws`).
- Firebase: `FIREBASE_ADMIN_JSON` (JSON complet, à éviter en prod si Secrets Manager est disponible).

### 3.1) CI/CD et déploiement (GitHub Actions → ECS Fargate)
- Repository ECR: `listeners-service` (créé automatiquement si absent par le workflow).
- Cluster ECS: `pinnokio_cluster` (fourni par l’infra existante).
- Service ECS: fourni via secret `ECS_SERVICE_NAME` dans GitHub (ex: `listeners-service`).
- Rôle OIDC GitHub: `AWS_ROLE_TO_ASSUME` (secret GitHub) pointant vers un rôle IAM autorisant ECR/ECS.
- Workflow: `.github/workflows/deploy.yml` build/push l’image, rend la taskdef et déploie le service.

Secrets GitHub requis:
- `AWS_ROLE_TO_ASSUME`
- `AWS_REGION` (ex: `us-east-1`)
- `ECS_SERVICE_NAME` (ex: `listeners-service`)

Paramètres runtime (ECS Task Definition → variables d’environnement conteneur):
- `AWS_REGION_NAME`, `GOOGLE_PROJECT_ID`, `GOOGLE_SERVICE_ACCOUNT_SECRET`, `AWS_SECRET_NAME`
- `LISTENERS_REDIS_HOST`, `LISTENERS_REDIS_PORT`, `LISTENERS_REDIS_PASSWORD`, `LISTENERS_REDIS_TLS`, `LISTENERS_REDIS_DB`, `LISTENERS_CHANNEL_PREFIX`

Commande de démarrage (conteneur):
`uvicorn app.main:app --host 0.0.0.0 --port 8080`

### 4) Registre online Firestore (partagé)
- Collection: `listeners_registry`, document id = `user_id`.
- Champs:
  - `status`: "online" | "offline"
  - `heartbeat_at`: `serverTimestamp`
  - `ttl_seconds`: int (défaut 90)
  - `authorized_companies_ids`: array<string>
  - `instance_id`: string (identifiant logique d’instance backend; utile quand on scalera à plusieurs)
- Règles:
  - À la connexion/init: upsert `{status: "online", heartbeat_at: now, ttl_seconds, instance_id}`.
  - Toutes les 30 s: mise à jour `heartbeat_at`.
  - À la déconnexion: set `{status: "offline"}` (best-effort). Expiration logique si `now - heartbeat_at > ttl_seconds`.
- Côté microservice: on_snapshot sur `listeners_registry`; pour chaque `user_id` online/non expiré → attacher (ou maintenir) les on_snapshot Firestore.

### 5) OnSnapshot Firestore et normalisation des événements
- Notifications: `clients/{uid}/notifications` where `read == false`.
  - Sur callback: construire et publier un snapshot normalisé (trié par `timestamp`), plus des deltas.
  - Événements:
    - `notif.sync` → `{ notifications: [...], count: n, timestamp }`
    - `notif.add | notif.update | notif.remove` → `{ doc_id, ... }`
- Messages/Chat: on_snapshot par canal/ressource (existant), mêmes conventions (`msg.*`, `chat.*`).
- Idempotence: inclure `doc_id` (ou `event_id`) pour permettre la déduplication côté consommateur si nécessaire.

### 6) Microservice "listeners" (Fargate)
- Rôle: ouvrir/maintenir les on_snapshot par `user_id` online, publier vers Redis Pub/Sub, exposer WebSocket (optionnel) et healthcheck.
- Démarrage:
  - Charger secrets via `aws_service.get_secret`.
  - Initialiser Firebase Admin SDK et client Redis (TLS si demandé).
  - Lancer on_snapshot sur `listeners_registry` et gérer attache/détache des streams utilisateurs.
- Publication Redis:
  - Canal: `"{channel_prefix}{uid}"` (ex: `user:abc123`).
  - Message JSON UTF-8; payload minimal et cohérent avec l’UI Reflex.
- Endpoints HTTP:
  - `GET /healthz` → `{status: "ok", version, listeners_count, redis: "ok"}`
  - `GET /version` (optionnel)
- WebSocket (optionnel):
  - `GET /ws?token=<ID_TOKEN>` (token Firebase côté client) ou mTLS interne côté backend.
  - Diffuser sur WS les mêmes messages que Pub/Sub (si vous optez pour mode direct navigateur).

### 7) Réseau et AWS (Fargate + ALB + ElastiCache)
- Fargate Service (ECS) dans VPC privé, SG autorisant sorties vers ElastiCache et entrées depuis ALB.
- ALB: Listener 443 (TLS ACM), target group HTTP/1.1 ou HTTP/2, healthcheck `/healthz`.
- ElastiCache Redis: AUTH + TLS activés; SG n’autorisant que les sous-réseaux Fargate et backend Reflex si bridge.
- Accès à Google Secret Manager depuis Fargate: le conteneur doit pouvoir s’authentifier auprès des APIs Google (par exemple via une variable `GOOGLE_APPLICATION_CREDENTIALS` pointant vers un fichier de clés monté ou via un mécanisme d’identités fédérées). Dans votre contexte, vous fournissez `GOOGLE_PROJECT_ID` et (si nécessaire) `GOOGLE_SERVICE_ACCOUNT_SECRET`.
- Logs: CloudWatch pour le microservice.

### 8) Intégration avec Reflex
Deux modes (on retiendra Bridge comme par défaut Reflex-natif):

- Mode Bridge (recommandé):
  - Backend Reflex s’abonne à Redis sur `user:{uid}`.
  - À chaque message `notif.*` → traduction en mutations de State comme aujourd’hui (aucun changement métier côté `NotificationState`).
  - Paramètres côté code (exemples):
    - `LISTENERS_REDIS_*` (ou `get_secret("pinnokio/listeners/redis")`).
    - Préfixe `channel_prefix` identique à celui du microservice.

- Mode Direct (optionnel):
  - Le frontend ouvre un WebSocket vers ALB `/ws` avec `ID_TOKEN` Firebase.
  - Le microservice vérifie le token et envoie les messages temps réel; l’UI met à jour localement.

### 9) Contrats des messages (Pub/Sub & WS)
- Enveloppe générique:
```json
{
  "type": "notif.sync | notif.add | notif.update | notif.remove | msg.* | chat.*",
  "uid": "abc123",
  "timestamp": "2025-01-01T12:00:00Z",
  "payload": { /* voir ci-dessous */ }
}
```

- `notif.sync.payload`:
```json
{
  "notifications": [
    {
      "doc_id": "...",
      "job_id": "...",
      "file_id": "...",
      "file_name": "...",
      "status": "...",
      "collection_id": "...",
      "collection_name": "...",
      "function_name": "...",
      "timestamp": "2025-01-01T12:00:00Z",
      "read": false,
      "additional_info": "{...}",
      "info_message": "..."
    }
  ],
  "count": 1
}
```

### 10) Exigences non-fonctionnelles
- Latence cible: < 300 ms intra-région pour la diffusion (Firestore → Pub/Sub → Reflex).
- Robustesse: backoff/retry sur on_snapshot, reconnexion Redis, idempotence par `doc_id`.
- Observabilité: logs structurés, métriques de listeners (attachés, reconnects), healthcheck.
- Sécurité: IAM restreint, Secrets Manager obligatoire, TLS bout-à-bout, vérification ID Token si WS direct.

### 11) Checklist de livraison (ingénieur plateforme)
1. Créer les secrets dans Google Secret Manager:
   - `pinnokio/listeners/redis` (schéma ci-dessus)
   - `pinnokio/listeners/firebase_admin`
   - `pinnokio/listeners/service_config` (optionnel)
   - Optionnel: un secret pour les paramètres AWS (`AWS_SECRET_NAME`) si requis.
2. Déployer ElastiCache Redis (AUTH + TLS) et SG réseau.
3. Déployer Fargate Service (ECS): accès sortant vers Google APIs pour GSM; logs CloudWatch; autoscaling min 1.
4. Déployer ALB (TLS ACM), target group → Fargate, healthcheck `/healthz`.
5. Configurer les variables d’environnement du service:
   - `GOOGLE_PROJECT_ID`
   - (si nécessaire) `GOOGLE_SERVICE_ACCOUNT_SECRET` ou un montage de clés Google
   - `AWS_SECRET_NAME` (si un secret AWS spécifique est stocké dans GSM)
6. Ouvrir les flux nécessaires (VPC, SG) et fournir:
   - Noms des secrets GSM
   - Endpoint ALB (wss/https)
   - Host/port Redis
   - `channel_prefix` utilisé

### 12) Paramétrage côté code (résumé)
- Récupération des secrets: `aws_service.get_secret("pinnokio/listeners/redis")`, `get_secret("pinnokio/listeners/firebase_admin")`, `get_secret("pinnokio/listeners/service_config")`.
- Registre Firestore: collection `listeners_registry` (déjà couverte côté code), TTL logique 90s par défaut.
- Bridge Reflex (par défaut): abonnement Redis sur `user:{uid}`, mapping 1:1 vers la logique existante de `NotificationState`/`MessengerState`.

Notes:
- Le branchement au microservice n’exige pas de modifier la logique métier des States; seule la source des événements change (de la queue process à Redis/WS).
- Les listeners existants (notifications/messages/chat) conservent leur format de payload connu par l’UI.


