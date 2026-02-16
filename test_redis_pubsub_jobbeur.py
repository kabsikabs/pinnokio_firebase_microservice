"""
Script de Test - Simuler un Jobbeur qui Publie sur Redis PubSub
================================================================

Ce script simule un jobbeur (Router, APbookeeper, Bankbookeeper) qui publie
des messages sur les canaux Redis PubSub.

Usage:
    python test_redis_pubsub_jobbeur.py

Prérequis:
    - Redis doit être en cours d'exécution
    - Le backend (firebase_microservice) doit être démarré
    - Un utilisateur doit être connecté via WebSocket

Tests effectués:
1. Publication sur user:{uid}/notifications
2. Publication sur user:{uid}/direct_message_notif
3. Publication sur user:{uid}/task_manager
4. Publication sur user:{uid}/{space_code}/chats/{thread_key}/messages (ignoré)
"""

import redis
import json
import time
import sys
from typing import Dict, Any

# Configuration Redis (ajuster selon votre environnement)
REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "password": None,
    "db": 0,
    "decode_responses": True,
}

# UID de test (à remplacer par un UID réel d'un utilisateur connecté)
TEST_UID = "test_user_123"

# Company ID de test
TEST_COMPANY_ID = "test_company_xyz"


def get_redis_client() -> redis.Redis:
    """Crée une connexion Redis."""
    return redis.Redis(**REDIS_CONFIG)


def publish_notification(client: redis.Redis, uid: str) -> None:
    """
    Simule une notification publiée par un jobbeur.

    Canal: user:{uid}/notifications
    """
    channel = f"user:{uid}/notifications"
    
    message = {
        "type": "notification_update",
        "job_id": "klk_test_001",
        "collection_path": f"clients/{uid}/notifications",
        "update_data": {
            "docId": f"notif_{int(time.time())}",
            "message": "Test notification from jobbeur",
            "status": "completed",
            "functionName": "Router",
            "timestamp": time.time(),
        },
        "status": "completed",
        "timestamp": time.time(),
    }
    
    print(f"\n[TEST] 📤 Publishing NOTIFICATION to {channel}")
    print(f"[TEST] → Payload: {json.dumps(message, indent=2)}")
    
    result = client.publish(channel, json.dumps(message))
    print(f"[TEST] ✅ Published to {result} subscriber(s)")


def publish_direct_message(client: redis.Redis, uid: str) -> None:
    """
    Simule un message direct (Messenger) publié par un jobbeur.

    Canal: user:{uid}/direct_message_notif
    """
    channel = f"user:{uid}/direct_message_notif"
    
    message = {
        "type": "direct_message",
        "message_id": f"msg_{int(time.time())}",
        "recipient_id": uid,
        "sender_id": "system",
        "collection_path": f"clients/{uid}/direct_message_notif",
        "data": {
            "action_type": "approval_required",
            "job_id": "klk_test_002",
            "priority": "high",
            "message": "Urgent: Please review and approve the invoice",
            "timestamp": time.time(),
        },
        "action_type": "approval_required",
        "job_id": "klk_test_002",
        "priority": "high",
        "timestamp": time.time(),
    }
    
    print(f"\n[TEST] 📤 Publishing DIRECT MESSAGE to {channel}")
    print(f"[TEST] → Payload: {json.dumps(message, indent=2)}")
    
    result = client.publish(channel, json.dumps(message))
    print(f"[TEST] ✅ Published to {result} subscriber(s)")


def publish_task_manager(client: redis.Redis, uid: str, company_id: str) -> None:
    """
    Simule une mise à jour task_manager publiée par un jobbeur.

    Canal: user:{uid}/task_manager
    """
    channel = f"user:{uid}/task_manager"
    
    message = {
        "type": "task_manager_update",
        "job_id": "klk_test_003",
        "mandate_path": f"mandates/{company_id}",
        "collection_path": f"mandates/{company_id}/task_manager",
        "collection_id": company_id,
        "company_id": company_id,
        "data": {
            "activity_type": "invoice_processing",
            "billing_amount": 25.50,
            "status": "completed",
            "timestamp": time.time(),
        },
        "status": "completed",
        "activity_type": "invoice_processing",
        "billing_amount": 25.50,
        "department": "accounting",  # accounting → invoices domain
        "timestamp": time.time(),
    }
    
    print(f"\n[TEST] 📤 Publishing TASK MANAGER to {channel}")
    print(f"[TEST] → Payload: {json.dumps(message, indent=2)}")
    
    result = client.publish(channel, json.dumps(message))
    print(f"[TEST] ✅ Published to {result} subscriber(s)")


def publish_chat_message(client: redis.Redis, uid: str) -> None:
    """
    Simule un message chat publié par un jobbeur.

    Canal: user:{uid}/{space_code}/chats/{thread_key}/messages
    NOTE: Ce message doit être IGNORÉ par le RedisSubscriber (géré par llm_manager)
    """
    space_code = "test_company"
    thread_key = "test_thread_001"
    channel = f"user:{uid}/{space_code}/chats/{thread_key}/messages"
    
    message = {
        "type": "chat_message",
        "thread_key": thread_key,
        "message": "This chat message should be IGNORED by RedisSubscriber",
        "role": "assistant",
        "timestamp": time.time(),
    }
    
    print(f"\n[TEST] 📤 Publishing CHAT MESSAGE to {channel}")
    print(f"[TEST] → Payload: {json.dumps(message, indent=2)}")
    print(f"[TEST] ⚠️  This message should be IGNORED by RedisSubscriber (handled by llm_manager)")
    
    result = client.publish(channel, json.dumps(message))
    print(f"[TEST] ✅ Published to {result} subscriber(s)")


def run_tests():
    """Exécute tous les tests de publication Redis PubSub."""
    print("=" * 80)
    print("TEST REDIS PUBSUB - SIMULATION JOBBEUR")
    print("=" * 80)
    print(f"\n[TEST] Configuration Redis: {REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}")
    print(f"[TEST] Test UID: {TEST_UID}")
    print(f"[TEST] Test Company ID: {TEST_COMPANY_ID}")
    
    try:
        # Connexion Redis
        print(f"\n[TEST] 🔌 Connecting to Redis...")
        client = get_redis_client()
        client.ping()
        print(f"[TEST] ✅ Connected to Redis successfully")
        
        # Attendre un peu pour que le subscriber soit prêt
        print(f"\n[TEST] ⏳ Waiting 2 seconds for subscriber to be ready...")
        time.sleep(2)
        
        # Test 1: Notification
        print("\n" + "=" * 80)
        print("TEST 1: NOTIFICATION (Niveau USER - Global)")
        print("=" * 80)
        publish_notification(client, TEST_UID)
        time.sleep(1)
        
        # Test 2: Direct Message
        print("\n" + "=" * 80)
        print("TEST 2: DIRECT MESSAGE (Messenger - Niveau USER - Global)")
        print("=" * 80)
        publish_direct_message(client, TEST_UID)
        time.sleep(1)
        
        # Test 3: Task Manager
        print("\n" + "=" * 80)
        print("TEST 3: TASK MANAGER (Niveau BUSINESS - Page-Specific)")
        print("=" * 80)
        publish_task_manager(client, TEST_UID, TEST_COMPANY_ID)
        time.sleep(1)
        
        # Test 4: Chat (doit être ignoré)
        print("\n" + "=" * 80)
        print("TEST 4: CHAT MESSAGE (IGNORÉ - Géré par llm_manager)")
        print("=" * 80)
        publish_chat_message(client, TEST_UID)
        time.sleep(1)
        
        print("\n" + "=" * 80)
        print("TESTS TERMINÉS")
        print("=" * 80)
        print("\n[TEST] ✅ Tous les messages ont été publiés sur Redis PubSub")
        print("[TEST] 📋 Vérifiez les logs du backend pour voir les messages traités")
        print("[TEST] 🔍 Logs attendus:")
        print("       - [REDIS_SUBSCRIBER] message_received channel=user:{uid}/notifications")
        print("       - [REDIS_SUBSCRIBER] handle_notification START")
        print("       - [REDIS_SUBSCRIBER] message_received channel=user:{uid}/direct_message_notif")
        print("       - [REDIS_SUBSCRIBER] handle_direct_message START")
        print("       - [REDIS_SUBSCRIBER] message_received channel=user:{uid}/task_manager")
        print("       - [REDIS_SUBSCRIBER] handle_task_manager START")
        print("       - [REDIS_SUBSCRIBER] routing to: IGNORE (chat handled by llm_manager)")
        
    except redis.ConnectionError as e:
        print(f"\n[TEST] ❌ Erreur de connexion Redis: {e}")
        print("[TEST] 💡 Assurez-vous que Redis est en cours d'exécution")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n[TEST] ❌ Erreur inattendue: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    print("\n⚠️  IMPORTANT:")
    print("1. Assurez-vous que Redis est en cours d'exécution")
    print("2. Assurez-vous que le backend (firebase_microservice) est démarré")
    print("3. Remplacez TEST_UID par un UID réel d'un utilisateur connecté")
    print("4. Remplacez TEST_COMPANY_ID par un company_id réel")
    print("\nPress Enter to continue or Ctrl+C to cancel...")
    
    try:
        input()
    except KeyboardInterrupt:
        print("\n\n[TEST] Annulé par l'utilisateur")
        sys.exit(0)
    
    run_tests()
