import boto3
import json
from datetime import datetime, timedelta

# Configuration
LOG_GROUP = "/ecs/pinnokio_microservice"
LOG_STREAM = "ecs/pinnokio_microservice/dbb324385d1a42408348de195496d41d"
REGION = "us-east-1"

# Client CloudWatch Logs
logs_client = boto3.client('logs', region_name=REGION)

print("🔍 Recherche de problèmes de session/déconnexion...\n")

# Récupérer les logs des 10 dernières minutes
end_time = datetime.utcnow()
start_time = end_time - timedelta(minutes=10)

start_timestamp = int(start_time.timestamp() * 1000)
end_timestamp = int(end_time.timestamp() * 1000)

try:
    response = logs_client.get_log_events(
        logGroupName=LOG_GROUP,
        logStreamName=LOG_STREAM,
        startTime=start_timestamp,
        endTime=end_timestamp,
        startFromHead=True
    )
    
    events = response['events']
    print(f"📊 {len(events)} événements trouvés\n")
    
    # Rechercher des patterns de problèmes
    keywords = [
        'disconnect', 'disconnection', 'déconnexion',
        'session', 'timeout', 'error', 'ERROR',
        'exception', 'Exception', 'failed', 'Failed',
        'ws_disconnect', 'connection closed', 'ABNORMAL_CLOSURE',
        'balance', 'storage'
    ]
    
    issues_found = []
    
    for event in events:
        message = event['message'].lower()
        timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
        
        for keyword in keywords:
            if keyword.lower() in message:
                issues_found.append({
                    'timestamp': timestamp,
                    'keyword': keyword,
                    'message': event['message']
                })
                break
    
    if issues_found:
        print(f"⚠️  {len(issues_found)} problèmes potentiels trouvés:\n")
        for issue in issues_found[-15:]:  # Derniers 15
            print(f"[{issue['timestamp'].strftime('%H:%M:%S')}] {issue['keyword'].upper()}")
            print(f"  {issue['message'][:200]}")
            print()
    else:
        print("✅ Aucun problème de session/déconnexion détecté")
    
    # Sauvegarder tous les logs
    with open('logs_session_analysis.json', 'w', encoding='utf-8') as f:
        json.dump({'events': events, 'issues': issues_found}, f, indent=2, default=str)
    
    print(f"\n📁 Tous les logs sauvegardés dans: logs_session_analysis.json")
    
    # Statistiques
    print(f"\n📈 Statistiques:")
    print(f"  • Total événements: {len(events)}")
    print(f"  • Problèmes trouvés: {len(issues_found)}")
    
    # Compter les types de problèmes
    keyword_counts = {}
    for issue in issues_found:
        key = issue['keyword']
        keyword_counts[key] = keyword_counts.get(key, 0) + 1
    
    if keyword_counts:
        print(f"\n  Répartition:")
        for keyword, count in sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"    • {keyword}: {count}")
    
except Exception as e:
    print(f"❌ Erreur: {str(e)}")

