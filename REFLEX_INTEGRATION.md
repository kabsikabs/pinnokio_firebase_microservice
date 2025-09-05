## Intégration backend Reflex — Sources d’événements (actuel / local / prod)

Objectif: expliquer quoi changer côté backend Reflex pour consommer les événements temps réel depuis la bonne source selon le mode.

### 1) Mode ACTUEL (ne rien casser)
- Source: mécanisme en place aujourd’hui (queue process interne / listeners intégrés au State Reflex).
- Action: ne changez rien. Ce mode reste par défaut tant que les tests local et prod ne sont pas validés.

### 2) Mode LOCAL (tests développeur)
- Source: Redis local (Docker) publié par `listeners-service` en local.
- Pré-requis côté dev:
  - Démarrer Redis local: `docker run -d --name redis-local -p 6379:6379 redis:alpine`
  - Démarrer le microservice listeners: `USE_LOCAL_REDIS=true uvicorn app.main:app --host 0.0.0.0 --port 8080`
  - Vérifier `GET http://localhost:8080/debug` → `redis: ok`
- Paramétrage côté backend Reflex (variables d’environnement):
  - `LISTENERS_REDIS_HOST=127.0.0.1`
  - `LISTENERS_REDIS_PORT=6379`
  - `LISTENERS_REDIS_PASSWORD=` (vide)
  - `LISTENERS_REDIS_TLS=false`
  - `LISTENERS_REDIS_DB=0`
  - `LISTENERS_CHANNEL_PREFIX=user:` (assurez-vous qu’il corresponde à celui du microservice)
- Résultat attendu: le backend Reflex s’abonne à `user:{uid}` sur le Redis local et reçoit les messages `notif.*`.

### 3) Mode PROD (ECS Fargate + ALB + ElastiCache Valkey)
- Source: Valkey Serverless (compatible Redis) dans AWS.
- Paramétrage côté backend Reflex (env prod):
  - `LISTENERS_REDIS_HOST=pinnokio-cache-7uum2j.serverless.use1.cache.amazonaws.com`
  - `LISTENERS_REDIS_PORT=6379`
  - `LISTENERS_REDIS_PASSWORD=` (vide, sécurité réseau via SG/VPC)
  - `LISTENERS_REDIS_TLS=true`
  - `LISTENERS_REDIS_DB=0`
  - `LISTENERS_CHANNEL_PREFIX=user:`
- Réseau: Le backend Reflex doit être dans le même VPC/subnets et Security Group autorisant le port 6379 vers Valkey.
- Vérification: une fois déployé, `GET https://<ALB>/healthz` du microservice doit être `ok`, et le backend Reflex doit recevoir les événements sur `user:{uid}`.

### 4) Commutation progressive des modes
- Étapes recommandées:
  1) Conserver le mode ACTUEL en production (aucun changement).
  2) Tester le mode LOCAL côté dev (Redis Docker + microservice local). Valider que l’UI reçoit `notif.*` via le backend Reflex.
  3) Déployer le microservice en PROD (ECS/ALB) et configurer le backend Reflex en mode PROD (Valkey). Tester sur un sous-ensemble d’utilisateurs.
  4) Après validation, retirer le mode ACTUEL et basculer définitivement sur Redis/Valkey.

### 5) Références et points de contrôle
- Préfixe de canal: `LISTENERS_CHANNEL_PREFIX` doit être identique côté microservice et backend Reflex.
- Aucun replay: Redis/Valkey diffuse seulement. Sur reconnexion, relire Firestore si besoin.
- Santé du microservice: `GET /healthz` et `GET /debug` (ALB en prod ou localhost en local).
- Sécurité (prod): accès Valkey par SG/VPC, TLS activé, pas de mot de passe.
