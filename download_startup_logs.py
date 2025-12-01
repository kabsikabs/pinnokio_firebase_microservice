import boto3
from datetime import datetime

# Configuration
LOG_GROUP = "/ecs/pinnokio_microservice"
TASK_ID = "dbb324385d1a42408348de195496d41d"
SERVICE_NAME = "pinnokio_microservice"
LOG_STREAM = f"ecs/{SERVICE_NAME}/{TASK_ID}"

# Client CloudWatch Logs
logs_client = boto3.client('logs', region_name='us-east-1')

print(f"Telechargement des PREMIERS logs (depuis le debut)...")
print(f"  Service: {SERVICE_NAME}")
print(f"  Task ID: {TASK_ID}\n")

try:
    # Récupérer depuis le début sans limite de temps
    response = logs_client.get_log_events(
        logGroupName=LOG_GROUP,
        logStreamName=LOG_STREAM,
        startFromHead=True,  # Depuis le début
        limit=1000  # Les 1000 premiers événements
    )
    
    events = response['events']
    
    print(f"Total d'evenements: {len(events)}")
    
    if events:
        first_ts = datetime.fromtimestamp(events[0]['timestamp'] / 1000)
        last_ts = datetime.fromtimestamp(events[-1]['timestamp'] / 1000)
        print(f"Premier log: {first_ts.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Dernier log: {last_ts.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Sauvegarder en format texte lisible
    txt_file = f"logs_{TASK_ID}_startup.txt"
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(f"=== LOGS DEMARRAGE - {SERVICE_NAME} ===\n")
        f.write(f"=== Task: {TASK_ID} ===\n")
        f.write(f"=== Total evenements: {len(events)} ===\n\n")
        
        for i, event in enumerate(events, 1):
            timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
            message = event['message']
            f.write(f"[{i:4d}] [{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    
    print(f"\nTexte lisible sauvegarde dans: {txt_file}")
    
    # Rechercher les logs importants
    print(f"\n--- LOGS IMPORTANTS ---")
    important_keywords = ["JOB_LOADER", "ROUTER", "session", "jobs_data", "initialize", "ERROR", "WARNING"]
    important_logs = []
    
    for evt in events:
        msg = evt['message']
        if any(keyword in msg.upper() for keyword in [k.upper() for k in important_keywords]):
            important_logs.append(evt)
    
    print(f"Trouve {len(important_logs)} logs importants")
    
    if important_logs:
        print("\nPremiers logs importants:")
        for evt in important_logs[:10]:
            ts = datetime.fromtimestamp(evt['timestamp'] / 1000)
            msg = evt['message'].replace('\n', ' ')[:150]
            print(f"  [{ts.strftime('%H:%M:%S')}] {msg}")
    
    print(f"\nTermine avec succes!")
    
except Exception as e:
    print(f"Erreur: {str(e)}")
    import traceback
    traceback.print_exc()

