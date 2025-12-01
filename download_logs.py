import boto3
import json
from datetime import datetime

# Configuration
LOG_GROUP = "/ecs/pinnokio_microservice"
REGION = "us-east-1"

# Tâches à analyser
TASK_IDS = [
    "6ac9ae34675d448b9a904c4d8f538524",
    "46e2329eaa0c4352affa79f697746163", 
    "35effd67684940bfaf39cf48dd2830af"
]

# Client CloudWatch Logs
logs_client = boto3.client('logs', region_name=REGION)

print("🔍 Téléchargement des logs des tâches qui ont échoué au health check...\n")

for task_id in TASK_IDS:
    log_stream_name = f"ecs/pinnokio_microservice/{task_id}"
    print(f"📋 Tâche: {task_id}")
    print(f"📁 Log stream: {log_stream_name}\n")
    
    try:
        # Récupérer les logs
        response = logs_client.get_log_events(
            logGroupName=LOG_GROUP,
            logStreamName=log_stream_name,
            startFromHead=True
        )
        
        events = response['events']
        
        if not events:
            print(f"  ⚠️  Aucun log trouvé pour cette tâche\n")
            continue
            
        # Sauvegarder dans un fichier
        filename = f"logs_task_{task_id}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"=== Logs de la tâche {task_id} ===\n")
            f.write(f"=== Nombre d'événements: {len(events)} ===\n\n")
            
            for event in events:
                timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
                message = event['message']
                f.write(f"[{timestamp}] {message}\n")
        
        print(f"  ✅ {len(events)} événements sauvegardés dans {filename}")
        
        # Afficher les dernières lignes
        print(f"  📝 Derniers messages:")
        for event in events[-5:]:
            timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
            message = event['message'].strip()
            print(f"     [{timestamp.strftime('%H:%M:%S')}] {message[:100]}")
        
        print()
        
    except Exception as e:
        print(f"  ❌ Erreur: {str(e)}\n")

print("✨ Téléchargement terminé!")

