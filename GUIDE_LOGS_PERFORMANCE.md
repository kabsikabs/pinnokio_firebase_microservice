# 📊 Guide de Téléchargement des Logs de Performance

Guide complet pour télécharger et analyser les logs de vos services ECS sur AWS.

---

## 📋 Table des matières

1. [Variables à connaître](#variables-à-connaître)
2. [Configuration IAM requise](#configuration-iam-requise)
3. [Commandes de base](#commandes-de-base)
4. [Téléchargement des logs](#téléchargement-des-logs)
5. [Analyse des logs](#analyse-des-logs)
6. [Résolution de problèmes](#résolution-de-problèmes)

---

## 🔑 Variables à connaître

### Informations générales

```bash
REGION="us-east-1"
CLUSTER_NAME="pinnokio_cluster"
AWS_ACCOUNT_ID="654654322636"
```

### Services ECS disponibles

| Service | Nom complet | Log Group |
|---------|-------------|-----------|
| **Microservice** | `pinnokio_microservice` | `/ecs/pinnokio_microservice` |
| **Backend Task** | `pinnokio_backend_task-service-wijv4h6y` | `/ecs/pinnokio_backend_task` |
| **Router** | `klk_router_service` | `/ecs/klk_router` |
| **APBookkeeper** | `klk_apbookeeper_service` | `/ecs/klk_apbookeeper` |
| **Task Bank** | `klk_task_bank-service-afec4qu1` | `/ecs/klk_task_bank` |

### Format des Log Streams

```
ecs/{service_name}/{task_id}
```

Exemple :
```
ecs/pinnokio_microservice/dbb324385d1a42408348de195496d41d
```

---

## 🔐 Configuration IAM requise

### Politique IAM minimale

Créez une politique managée avec ce JSON :

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
                "logs:GetLogEvents",
                "logs:FilterLogEvents",
                "logs:StartQuery",
                "logs:StopQuery",
                "logs:GetQueryResults"
            ],
            "Resource": "arn:aws:logs:us-east-1:654654322636:log-group:/ecs/*:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "ecs:ListTasks",
                "ecs:DescribeTasks",
                "ecs:DescribeServices",
                "ecs:ListServices"
            ],
            "Resource": "*"
        }
    ]
}
```

**Nom suggéré** : `PinnokioLogsAndECSReadOnly`

### Attacher la politique

```bash
aws iam attach-user-policy \
    --user-name pinnokio_aws \
    --policy-arn arn:aws:iam::654654322636:policy/PinnokioLogsAndECSReadOnly \
    --region us-east-1
```

---

## 🎯 Commandes de base

### 1. Lister tous les services

```bash
aws ecs list-services \
    --cluster pinnokio_cluster \
    --region us-east-1
```

### 2. Obtenir les tâches en cours d'un service

```bash
SERVICE_NAME="pinnokio_microservice"

aws ecs list-tasks \
    --cluster pinnokio_cluster \
    --service-name $SERVICE_NAME \
    --desired-status RUNNING \
    --region us-east-1
```

### 3. Obtenir les détails d'une tâche

```bash
TASK_ID="dbb324385d1a42408348de195496d41d"

aws ecs describe-tasks \
    --cluster pinnokio_cluster \
    --tasks $TASK_ID \
    --region us-east-1
```

### 4. Lister les log groups disponibles

```bash
aws logs describe-log-groups \
    --log-group-name-prefix /ecs/ \
    --region us-east-1
```

### 5. Lister les log streams d'un service

```bash
LOG_GROUP="/ecs/pinnokio_microservice"

aws logs describe-log-streams \
    --log-group-name $LOG_GROUP \
    --order-by LastEventTime \
    --descending \
    --max-items 10 \
    --region us-east-1
```

---

## 📥 Téléchargement des logs

### Méthode 1 : Par période de temps (recommandé)

#### Bash/Linux

```bash
# Variables
LOG_GROUP="/ecs/pinnokio_microservice"
TASK_ID="dbb324385d1a42408348de195496d41d"
LOG_STREAM="ecs/pinnokio_microservice/${TASK_ID}"

# Calculer les timestamps (10 dernières minutes)
END_TIME=$(date +%s)000
START_TIME=$((END_TIME - 600000))  # 10 minutes = 600000 ms

# Télécharger les logs
aws logs get-log-events \
    --log-group-name $LOG_GROUP \
    --log-stream-name $LOG_STREAM \
    --start-time $START_TIME \
    --end-time $END_TIME \
    --region us-east-1 \
    --output json > logs_${TASK_ID}_10min.json
```

#### PowerShell

```powershell
# Variables
$LOG_GROUP = "/ecs/pinnokio_microservice"
$TASK_ID = "dbb324385d1a42408348de195496d41d"
$LOG_STREAM = "ecs/pinnokio_microservice/$TASK_ID"

# Calculer les timestamps (10 dernières minutes)
$tenMinutesAgo = [DateTimeOffset]::UtcNow.AddMinutes(-10).ToUnixTimeMilliseconds()
$now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()

# Télécharger les logs
aws logs get-log-events `
    --log-group-name $LOG_GROUP `
    --log-stream-name $LOG_STREAM `
    --start-time $tenMinutesAgo `
    --end-time $now `
    --region us-east-1 `
    --output json | Out-File -Encoding utf8 "logs_${TASK_ID}_10min.json"
```

### Méthode 2 : Utiliser tail (temps réel)

```bash
# Suivre les logs en temps réel
aws logs tail /ecs/pinnokio_microservice --follow --region us-east-1

# Logs des 30 dernières minutes
aws logs tail /ecs/pinnokio_microservice --since 30m --region us-east-1

# Logs avec filtre
aws logs tail /ecs/pinnokio_microservice \
    --since 10m \
    --filter-pattern "ERROR" \
    --region us-east-1
```

### Méthode 3 : Script Python (meilleure gestion encodage)

Créez `download_logs.py` :

```python
import boto3
import json
from datetime import datetime, timedelta
import sys

def download_logs(log_group, task_id, minutes=10):
    """
    Télécharge les logs d'une tâche ECS
    
    Args:
        log_group: Nom du log group (ex: /ecs/pinnokio_microservice)
        task_id: ID de la tâche
        minutes: Nombre de minutes à récupérer (défaut: 10)
    """
    # Extraire le nom du service du log group
    service_name = log_group.split('/')[-1]
    log_stream = f"ecs/{service_name}/{task_id}"
    
    # Client CloudWatch Logs
    logs_client = boto3.client('logs', region_name='us-east-1')
    
    print(f"📥 Téléchargement des logs...")
    print(f"  Service: {service_name}")
    print(f"  Task ID: {task_id}")
    print(f"  Période: {minutes} dernières minutes\n")
    
    # Calculer les timestamps
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=minutes)
    
    start_timestamp = int(start_time.timestamp() * 1000)
    end_timestamp = int(end_time.timestamp() * 1000)
    
    try:
        # Récupérer les logs
        response = logs_client.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            startTime=start_timestamp,
            endTime=end_timestamp,
            startFromHead=True
        )
        
        events = response['events']
        
        # Nom du fichier de sortie
        output_file = f"logs_{service_name}_{task_id}_{minutes}min.txt"
        
        # Sauvegarder les logs
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"=== Logs de {service_name} ===\n")
            f.write(f"=== Task: {task_id} ===\n")
            f.write(f"=== Période: {start_time} à {end_time} ===\n")
            f.write(f"=== Total événements: {len(events)} ===\n\n")
            
            for event in events:
                timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
                message = event['message']
                f.write(f"[{timestamp}] {message}\n")
        
        print(f"✅ {len(events)} événements sauvegardés dans: {output_file}")
        
        return events
        
    except Exception as e:
        print(f"❌ Erreur: {str(e)}")
        return None

if __name__ == "__main__":
    # Exemple d'utilisation
    if len(sys.argv) < 3:
        print("Usage: python download_logs.py <log_group> <task_id> [minutes]")
        print("\nExemple:")
        print("  python download_logs.py /ecs/pinnokio_microservice dbb324385d1a42408348de195496d41d 10")
        sys.exit(1)
    
    log_group = sys.argv[1]
    task_id = sys.argv[2]
    minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    
    download_logs(log_group, task_id, minutes)
```

**Utilisation** :

```bash
python download_logs.py /ecs/pinnokio_microservice dbb324385d1a42408348de195496d41d 10
```

---

## 🔍 Analyse des logs

### Rechercher des erreurs

```bash
# Filtrer les erreurs dans un fichier de logs
grep -i "error\|exception\|failed" logs_task_xxx.txt

# Compter les erreurs
grep -i "error" logs_task_xxx.txt | wc -l

# Filtrer par timestamp
grep "2025-11-27 23:" logs_task_xxx.txt
```

### Analyser les health checks

```bash
# Compter les health checks réussis
grep "healthz.*200 OK" logs_task_xxx.txt | wc -l

# Voir les health checks échoués
grep "healthz" logs_task_xxx.txt | grep -v "200 OK"
```

### Script d'analyse Python

Créez `analyze_logs.py` :

```python
import json
import sys
from collections import Counter
from datetime import datetime

def analyze_logs(log_file):
    """Analyse les logs et génère des statistiques"""
    
    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    # Statistiques
    total_lines = len(lines)
    errors = [l for l in lines if 'ERROR' in l.upper() or 'EXCEPTION' in l.upper()]
    warnings = [l for l in lines if 'WARNING' in l.upper() or 'WARN' in l.upper()]
    health_checks = [l for l in lines if 'healthz' in l and '200 OK' in l]
    
    print("📊 ANALYSE DES LOGS")
    print("=" * 50)
    print(f"\n📈 Statistiques générales:")
    print(f"  • Total de lignes: {total_lines}")
    print(f"  • Erreurs: {len(errors)}")
    print(f"  • Warnings: {len(warnings)}")
    print(f"  • Health checks OK: {len(health_checks)}")
    
    if errors:
        print(f"\n❌ Premières erreurs trouvées:")
        for error in errors[:5]:
            print(f"  {error[:100]}")
    
    if warnings:
        print(f"\n⚠️  Premiers warnings trouvés:")
        for warning in warnings[:5]:
            print(f"  {warning[:100]}")
    
    # Mots-clés fréquents
    keywords = ['disconnect', 'timeout', 'failed', 'success', 'complete']
    keyword_counts = {}
    for keyword in keywords:
        count = sum(1 for l in lines if keyword.lower() in l.lower())
        if count > 0:
            keyword_counts[keyword] = count
    
    if keyword_counts:
        print(f"\n🔍 Mots-clés trouvés:")
        for keyword, count in sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  • {keyword}: {count}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_logs.py <log_file>")
        sys.exit(1)
    
    analyze_logs(sys.argv[1])
```

**Utilisation** :

```bash
python analyze_logs.py logs_task_xxx.txt
```

---

## 🚨 Résolution de problèmes

### Problème 1 : Erreur de permissions

**Erreur** :
```
AccessDeniedException: User is not authorized to perform: logs:GetLogEvents
```

**Solution** :
- Vérifier que la politique IAM est bien attachée
- Utiliser un utilisateur avec des droits admin temporairement
- Voir la section [Configuration IAM requise](#configuration-iam-requise)

### Problème 2 : Encodage des caractères (Windows)

**Erreur** :
```
'charmap' codec can't encode character
```

**Solutions** :

**Option 1** : Utiliser le script Python (recommandé)
```bash
python download_logs.py /ecs/pinnokio_microservice <task_id> 10
```

**Option 2** : PowerShell avec UTF-8
```powershell
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001
```

**Option 3** : Rediriger vers un fichier JSON
```powershell
aws logs get-log-events ... --output json | Out-File -Encoding utf8 logs.json
```

### Problème 3 : Log stream introuvable

**Erreur** :
```
ResourceNotFoundException: The specified log stream does not exist
```

**Solution** :
```bash
# Lister les streams disponibles
aws logs describe-log-streams \
    --log-group-name /ecs/pinnokio_microservice \
    --order-by LastEventTime \
    --descending \
    --max-items 5 \
    --region us-east-1
```

### Problème 4 : Timestamps invalides

**Solution** : Utiliser des timestamps en millisecondes

```bash
# Bon format (millisecondes depuis epoch)
START_TIME=1764284258008

# Convertir une date
date -d "2025-11-27 23:00:00" +%s000  # Linux
```

---

## 📝 Scénarios d'utilisation courants

### Scénario 1 : Diagnostic d'un problème de démarrage

```bash
# 1. Identifier la tâche qui a échoué
aws ecs list-tasks \
    --cluster pinnokio_cluster \
    --service-name pinnokio_microservice \
    --desired-status STOPPED \
    --region us-east-1 \
    --max-items 1

# 2. Obtenir les détails
TASK_ID="xxx"
aws ecs describe-tasks \
    --cluster pinnokio_cluster \
    --tasks $TASK_ID \
    --region us-east-1 \
    --query "tasks[0].[stoppedReason,containers[0].exitCode]"

# 3. Télécharger les logs
python download_logs.py /ecs/pinnokio_microservice $TASK_ID 30

# 4. Analyser
python analyze_logs.py logs_pinnokio_microservice_${TASK_ID}_30min.txt
```

### Scénario 2 : Monitoring des performances

```bash
# Télécharger les logs de la tâche active
TASK_ID=$(aws ecs list-tasks \
    --cluster pinnokio_cluster \
    --service-name pinnokio_microservice \
    --desired-status RUNNING \
    --region us-east-1 \
    --query 'taskArns[0]' \
    --output text | awk -F'/' '{print $NF}')

# Logs des dernières 5 minutes
python download_logs.py /ecs/pinnokio_microservice $TASK_ID 5

# Compter les health checks
grep "healthz.*200 OK" logs_*.txt | wc -l
```

### Scénario 3 : Recherche de déconnexions

```bash
# Télécharger les logs
python download_logs.py /ecs/pinnokio_microservice $TASK_ID 15

# Rechercher les déconnexions
grep -i "disconnect\|session.*close\|abnormal_closure" logs_*.txt
```

---

## 🎯 Commandes rapides (cheat sheet)

```bash
# Obtenir le Task ID actuel d'un service
aws ecs list-tasks --cluster pinnokio_cluster --service-name pinnokio_microservice --desired-status RUNNING --region us-east-1 --query 'taskArns[0]' --output text | awk -F'/' '{print $NF}'

# Télécharger les logs des 10 dernières minutes
python download_logs.py /ecs/pinnokio_microservice <TASK_ID> 10

# Suivre les logs en temps réel
aws logs tail /ecs/pinnokio_microservice --follow --region us-east-1

# Compter les erreurs
grep -i "error" logs_*.txt | wc -l

# Voir les derniers événements du service
aws ecs describe-services --cluster pinnokio_cluster --services pinnokio_microservice --region us-east-1 --query 'services[0].events[:5]'
```

---

## 📚 Ressources supplémentaires

### Documentation AWS

- [CloudWatch Logs CLI Reference](https://docs.aws.amazon.com/cli/latest/reference/logs/)
- [ECS CLI Reference](https://docs.aws.amazon.com/cli/latest/reference/ecs/)
- [IAM Policies for CloudWatch Logs](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/permissions-reference-cwl.html)

### Fichiers de ce projet

- `download_logs.py` - Script Python pour télécharger les logs
- `analyze_logs.py` - Script Python pour analyser les logs
- `fix_health_check.ps1` - Script pour corriger la configuration des health checks
- `DIAGNOSTIC_HEALTH_CHECK.md` - Documentation sur le diagnostic des health checks

---

## ✅ Checklist de dépannage

Avant de télécharger les logs, vérifiez :

- [ ] Vous avez les permissions IAM nécessaires
- [ ] Le service existe et est en cours d'exécution
- [ ] Le Task ID est valide
- [ ] Le log group existe (`aws logs describe-log-groups`)
- [ ] La région AWS est correcte (`us-east-1`)
- [ ] Vous utilisez Python 3.7+ si vous utilisez les scripts

---

**Dernière mise à jour** : 27 novembre 2025  
**Auteur** : Documentation générée suite au diagnostic des problèmes de health check ECS

