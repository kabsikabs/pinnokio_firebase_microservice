# Extracteur de Logs CloudWatch

Ce document décrit l'utilisation du module `cloudwatch_logs.py` pour extraire les logs depuis AWS CloudWatch Logs.

## Vue d'ensemble

Le module `CloudWatchLogsExtractor` permet de:
- Lister les streams de logs avec leurs dates
- Télécharger un log complet (format texte ou JSON)
- Filtrer par dates
- Obtenir des informations sur le groupe de journaux

Le groupe de journaux utilisé est: `/ecs/pinnokio_microservice`

## Prérequis

### Dépendances

Le module utilise `boto3` qui est présent dans `requirements.txt`.

### Configuration AWS

**Boto3 détecte automatiquement les credentials AWS** dans cet ordre de priorité:

1. **Variables d'environnement** (recommandé pour les scripts locaux):
   ```bash
   # Linux/Mac
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-east-1
   
   # Windows PowerShell
   $env:AWS_ACCESS_KEY_ID="your_access_key"
   $env:AWS_SECRET_ACCESS_KEY="your_secret_key"
   $env:AWS_DEFAULT_REGION="us-east-1"
   
   # Windows CMD
   set AWS_ACCESS_KEY_ID=your_access_key
   set AWS_SECRET_ACCESS_KEY=your_secret_key
   set AWS_DEFAULT_REGION=us-east-1
   ```

2. **Fichier de credentials** (`~/.aws/credentials` ou `%USERPROFILE%\.aws\credentials` sur Windows):
   ```ini
   [default]
   aws_access_key_id = your_access_key
   aws_secret_access_key = your_secret_key
   region = us-east-1
   ```

3. **IAM Role** (automatique si exécuté depuis une instance EC2 ou ECS)

4. **Fichier de configuration** (`~/.aws/config`):
   ```ini
   [default]
   region = us-east-1
   ```

**Important**: Vous devez fournir au moins `AWS_ACCESS_KEY_ID` et `AWS_SECRET_ACCESS_KEY` via l'une de ces méthodes. Boto3 ne fonctionnera pas sans credentials valides.

### Vérification des credentials

**Recommandé**: Vérifiez d'abord que vos credentials sont correctement configurés avant d'utiliser le module.

Via le script (méthode la plus simple):
```bash
python scripts/download_cloudwatch_logs.py check-credentials
```

En Python:
```python
from app.tools.cloudwatch_logs import CloudWatchLogsExtractor

extractor = CloudWatchLogsExtractor()
if extractor.check_credentials():
    print("✓ Credentials AWS valides")
else:
    print("✗ Erreur: Vérifiez vos credentials AWS")
```

**Note importante**: Boto3 cherche automatiquement les credentials dans l'ordre suivant:
1. Paramètres explicites passés au client (si fournis)
2. Variables d'environnement (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
3. Fichier `~/.aws/credentials` (ou `%USERPROFILE%\.aws\credentials` sur Windows)
4. IAM Role (si exécuté sur EC2/ECS/Lambda)
5. Fichier de configuration `~/.aws/config`

Vous n'avez **pas besoin** de passer les credentials explicitement si vous utilisez les méthodes 2, 3 ou 4 ci-dessus.

### Permissions requises

Le rôle IAM ou les credentials doivent avoir les permissions suivantes:
- `logs:DescribeLogGroups`
- `logs:DescribeLogStreams`
- `logs:GetLogEvents`

#### Politique IAM recommandée (pour tous les logs)

Si vous souhaitez accéder à **tous les logs CloudWatch**, utilisez cette politique IAM:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
                "logs:GetLogEvents"
            ],
            "Resource": "*"
        }
    ]
}
```

**Alternative**: Utilisez la politique AWS gérée `CloudWatchLogsReadOnlyAccess` qui donne un accès en lecture seule à tous les logs CloudWatch.

#### Politique IAM restrictive (pour un groupe spécifique uniquement)

Si vous souhaitez limiter l'accès à un seul groupe de journaux (ex: `/ecs/pinnokio_microservice`):

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
                "logs:GetLogEvents"
            ],
            "Resource": "arn:aws:logs:us-east-1:*:log-group:/ecs/pinnokio_microservice:*"
        }
    ]
}
```

## Utilisation en Python

### Importation

```python
from app.tools.cloudwatch_logs import CloudWatchLogsExtractor
```

### Initialisation

```python
# Utilisation par défaut (région us-east-1, groupe /ecs/pinnokio_microservice)
# Utilise automatiquement les credentials AWS configurés (env vars, fichier, IAM role)
extractor = CloudWatchLogsExtractor()

# Personnalisation
extractor = CloudWatchLogsExtractor(
    region_name='us-east-1',
    log_group_name='/ecs/pinnokio_microservice'
)

# Avec credentials explicites (optionnel, généralement non nécessaire)
extractor = CloudWatchLogsExtractor(
    region_name='us-east-1',
    log_group_name='/ecs/pinnokio_microservice',
    aws_access_key_id='your_key',
    aws_secret_access_key='your_secret'
)
```

**Note**: Il est généralement **non nécessaire** de passer les credentials explicitement. Boto3 les détecte automatiquement depuis les variables d'environnement, le fichier de credentials, ou l'IAM role.

### Lister les streams de logs

```python
# Lister tous les streams (triés par date décroissante)
streams = extractor.list_log_streams()

# Limiter le nombre de résultats
streams = extractor.list_log_streams(limit=10)

# Filtrer par dates
from datetime import datetime, timedelta

# Streams des 7 derniers jours
start_time = datetime.now() - timedelta(days=7)
streams = extractor.list_log_streams(start_time=start_time)

# Période spécifique
start_time = datetime(2025, 1, 1)
end_time = datetime(2025, 1, 31)
streams = extractor.list_log_streams(start_time=start_time, end_time=end_time)

# Trier par nom de stream
streams = extractor.list_log_streams(order_by='LogStreamName', descending=False)
```

Chaque stream retourné contient:
- `logStreamName`: nom du stream
- `creationTime`: timestamp de création (ms)
- `creationTimeFormatted`: date de création formatée
- `firstEventTimestamp`: timestamp du premier événement (ms)
- `firstEventTimeFormatted`: date du premier événement formatée
- `lastEventTimestamp`: timestamp du dernier événement (ms)
- `lastEventTimeFormatted`: date du dernier événement formatée
- `storedBytes`: taille en bytes

### Télécharger un log (format texte)

```python
# Télécharger un log complet
output_file = extractor.download_log('ecs/pinnokio_microservice/abc123')

# Spécifier le fichier de sortie
output_file = extractor.download_log(
    'ecs/pinnokio_microservice/abc123',
    output_file='logs/mon_log.log'
)

# Filtrer par dates
from datetime import datetime

start_time = datetime(2025, 1, 1, 10, 0, 0)
end_time = datetime(2025, 1, 1, 12, 0, 0)
output_file = extractor.download_log(
    'ecs/pinnokio_microservice/abc123',
    start_time=start_time,
    end_time=end_time
)
```

Le fichier généré contient les logs au format:
```
[2025-01-01 10:00:00] Message du log
[2025-01-01 10:00:01] Autre message
...
```

### Télécharger un log (format JSON)

```python
# Télécharger au format JSON
output_file = extractor.download_log_json('ecs/pinnokio_microservice/abc123')

# Spécifier le fichier de sortie
output_file = extractor.download_log_json(
    'ecs/pinnokio_microservice/abc123',
    output_file='logs/mon_log.json'
)
```

Le fichier JSON contient:
```json
{
  "logGroupName": "/ecs/pinnokio_microservice",
  "logStreamName": "ecs/pinnokio_microservice/abc123",
  "exportedAt": "2025-01-01T12:00:00",
  "totalEvents": 150,
  "events": [
    {
      "timestamp": 1704110400000,
      "timestampFormatted": "2025-01-01 10:00:00",
      "message": "Message du log"
    },
    ...
  ]
}
```

### Obtenir les informations du groupe de journaux

```python
info = extractor.get_log_group_info()
print(f"Groupe: {info['logGroupName']}")
print(f"Créé le: {info['creationTimeFormatted']}")
print(f"Taille: {info['storedBytes']} bytes")
print(f"Rétention: {info['retentionInDays']} jours")
```

## Utilisation via script

Un script d'exécution est fourni dans `scripts/download_cloudwatch_logs.py`.

### Lister les streams

```bash
# Lister tous les streams
python scripts/download_cloudwatch_logs.py list

# Lister les 10 derniers streams
python scripts/download_cloudwatch_logs.py list --limit 10

# Lister les streams des 7 derniers jours
python scripts/download_cloudwatch_logs.py list --days 7

# Lister avec filtre de dates
python scripts/download_cloudwatch_logs.py list --start-date 2025-01-01 --end-date 2025-01-31

# Trier par nom (croissant)
python scripts/download_cloudwatch_logs.py list --order-by LogStreamName --ascending
```

### Télécharger un log

```bash
# Télécharger un log (format texte)
python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123

# Spécifier le fichier de sortie
python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123 --output logs/mon_log.log

# Télécharger au format JSON
python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123 --json

# Filtrer par dates
python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123 \
    --start-date 2025-01-01T10:00:00 \
    --end-date 2025-01-01T12:00:00
```

### Vérifier les credentials

```bash
# Vérifier que les credentials AWS sont valides
python scripts/download_cloudwatch_logs.py check-credentials
```

### Afficher les informations du groupe

```bash
python scripts/download_cloudwatch_logs.py info
```

### Options globales

```bash
# Spécifier une autre région
python scripts/download_cloudwatch_logs.py list --region eu-west-1

# Spécifier un autre groupe de journaux
python scripts/download_cloudwatch_logs.py list --log-group /ecs/autre_groupe
```

## Utilisation via API REST

Le service expose des endpoints REST pour accéder aux logs CloudWatch depuis l'extérieur.

### Authentification

Tous les endpoints nécessitent un header d'authentification:
```
Authorization: Bearer <token>
```

### Lister les streams

**Endpoint**: `POST /cloudwatch/logs/list`

**Corps de la requête**:
```json
{
  "limit": 10,
  "order_by": "LastEventTime",
  "descending": true,
  "days": 7,
  "start_date": "2025-01-01",
  "end_date": "2025-01-31"
}
```

**Réponse**:
```json
{
  "status": "success",
  "count": 10,
  "streams": [
    {
      "logStreamName": "ecs/pinnokio_microservice/abc123",
      "creationTime": 1704110400000,
      "creationTimeFormatted": "2025-01-01 10:00:00",
      "firstEventTimestamp": 1704110400000,
      "firstEventTimeFormatted": "2025-01-01 10:00:00",
      "lastEventTimestamp": 1704110500000,
      "lastEventTimeFormatted": "2025-01-01 10:01:40",
      "storedBytes": 1024
    }
  ]
}
```

### Télécharger un log

**Endpoint**: `POST /cloudwatch/logs/download`

**Corps de la requête**:
```json
{
  "log_stream_name": "ecs/pinnokio_microservice/abc123",
  "json_format": false,
  "start_date": "2025-01-01T10:00:00",
  "end_date": "2025-01-01T12:00:00"
}
```

**Réponse** (format texte):
```json
{
  "status": "success",
  "log_stream_name": "ecs/pinnokio_microservice/abc123",
  "format": "text",
  "content": "[2025-01-01 10:00:00] Message du log\n[2025-01-01 10:00:01] Autre message\n...",
  "file_path": "/tmp/tmp_abc123.log"
}
```

**Réponse** (format JSON):
```json
{
  "status": "success",
  "log_stream_name": "ecs/pinnokio_microservice/abc123",
  "format": "json",
  "content": {
    "logGroupName": "/ecs/pinnokio_microservice",
    "logStreamName": "ecs/pinnokio_microservice/abc123",
    "exportedAt": "2025-01-01T12:00:00",
    "totalEvents": 150,
    "events": [...]
  },
  "file_path": "/tmp/tmp_abc123.json"
}
```

### Obtenir les informations du groupe

**Endpoint**: `GET /cloudwatch/logs/info`

**Réponse**:
```json
{
  "status": "success",
  "info": {
    "logGroupName": "/ecs/pinnokio_microservice",
    "creationTime": 1704110400000,
    "creationTimeFormatted": "2025-01-01 10:00:00",
    "storedBytes": 1048576,
    "retentionInDays": 30,
    "metricFilterCount": 0
  }
}
```

### Exemple avec curl

```bash
# Lister les 10 derniers streams
curl -X POST http://localhost:8000/cloudwatch/logs/list \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"limit": 10}'

# Télécharger un log au format JSON
curl -X POST http://localhost:8000/cloudwatch/logs/download \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"log_stream_name": "ecs/pinnokio_microservice/abc123", "json_format": true}'

# Obtenir les informations du groupe
curl -X GET http://localhost:8000/cloudwatch/logs/info \
  -H "Authorization: Bearer <token>"
```

## Exemples d'utilisation

### Exemple 1: Lister et télécharger les logs récents

```python
from app.tools.cloudwatch_logs import CloudWatchLogsExtractor
from datetime import datetime, timedelta

extractor = CloudWatchLogsExtractor()

# Lister les 5 derniers streams
streams = extractor.list_log_streams(limit=5)

for stream in streams:
    print(f"Stream: {stream['logStreamName']}")
    print(f"Dernier événement: {stream['lastEventTimeFormatted']}")
    
    # Télécharger le log
    output_file = extractor.download_log(stream['logStreamName'])
    print(f"Téléchargé: {output_file}\n")
```

### Exemple 2: Télécharger les logs d'une période spécifique

```python
from app.tools.cloudwatch_logs import CloudWatchLogsExtractor
from datetime import datetime

extractor = CloudWatchLogsExtractor()

# Télécharger les logs du 1er janvier 2025 entre 10h et 12h
start_time = datetime(2025, 1, 1, 10, 0, 0)
end_time = datetime(2025, 1, 1, 12, 0, 0)

output_file = extractor.download_log(
    'ecs/pinnokio_microservice/abc123',
    start_time=start_time,
    end_time=end_time,
    output_file='logs/janvier_1_10h_12h.log'
)
```

### Exemple 3: Script de sauvegarde automatique

```python
#!/usr/bin/env python3
"""Script pour sauvegarder automatiquement les logs récents."""

from app.tools.cloudwatch_logs import CloudWatchLogsExtractor
from datetime import datetime, timedelta
import os

extractor = CloudWatchLogsExtractor()

# Récupérer les streams des dernières 24 heures
start_time = datetime.now() - timedelta(days=1)
streams = extractor.list_log_streams(start_time=start_time)

# Créer un répertoire de sauvegarde
backup_dir = f"backups/logs_{datetime.now().strftime('%Y%m%d')}"
os.makedirs(backup_dir, exist_ok=True)

# Télécharger chaque stream
for stream in streams:
    stream_name = stream['logStreamName']
    safe_name = stream_name.replace('/', '_')
    output_file = os.path.join(backup_dir, f"{safe_name}.log")
    
    try:
        extractor.download_log(stream_name, output_file=output_file)
        print(f"✓ {stream_name} sauvegardé")
    except Exception as e:
        print(f"✗ Erreur pour {stream_name}: {e}")

print(f"\nSauvegarde terminée: {len(streams)} stream(s) traité(s)")
```

## Gestion des erreurs

Le module gère plusieurs types d'erreurs:

- **ResourceNotFoundException**: Le groupe ou le stream n'existe pas
- **ClientError**: Erreur d'authentification ou de permissions
- **Exception générique**: Autres erreurs (réseau, format, etc.)

Exemple de gestion d'erreurs:

```python
from app.tools.cloudwatch_logs import CloudWatchLogsExtractor
import boto3

extractor = CloudWatchLogsExtractor()

try:
    streams = extractor.list_log_streams()
except extractor.client.exceptions.ResourceNotFoundException:
    print("Le groupe de journaux n'existe pas")
except boto3.exceptions.ClientError as e:
    error_code = e.response['Error']['Code']
    if error_code == 'AccessDeniedException':
        print("Permissions insuffisantes")
    else:
        print(f"Erreur AWS: {e}")
except Exception as e:
    print(f"Erreur inattendue: {e}")
```

## Limitations

1. **Pagination**: Les méthodes gèrent automatiquement la pagination, mais pour de très gros volumes, cela peut prendre du temps.

2. **Limites AWS**: AWS CloudWatch Logs a des limites de taux:
   - 5 requêtes par seconde pour `GetLogEvents`
   - 5 requêtes par seconde pour `DescribeLogStreams`

3. **Taille des logs**: Les logs très volumineux peuvent nécessiter beaucoup de mémoire et de temps de traitement.

## Notes techniques

- Les timestamps sont en millisecondes (format AWS CloudWatch)
- Les fichiers de sortie sont créés dans le répertoire `logs/` par défaut
- Le format de date utilisé est ISO 8601 pour les timestamps formatés
- La pagination est gérée automatiquement pour récupérer tous les événements

