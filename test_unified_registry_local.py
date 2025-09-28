#!/usr/bin/env python3
"""
Script de test local pour valider le fonctionnement du registre unifié.
Permet de vérifier que le système fonctionne correctement en mode local.
"""

import os
import sys
import time
import json
import requests
import redis
from datetime import datetime

# Configuration pour test local
os.environ["USE_LOCAL_REDIS"] = "true"
os.environ["UNIFIED_REGISTRY_ENABLED"] = "true"
os.environ["REGISTRY_DEBUG"] = "true"
os.environ["LISTENERS_MODE"] = "LOCAL"

# URL du microservice local
BASE_URL = "http://localhost:8080"

def print_section(title):
    """Affiche une section de test."""
    print("\n" + "="*60)
    print(f"🧪 {title}")
    print("="*60)

def print_success(message):
    """Affiche un message de succès."""
    print(f"✅ {message}")

def print_error(message):
    """Affiche un message d'erreur."""
    print(f"❌ {message}")

def print_info(message):
    """Affiche un message d'information."""
    print(f"ℹ️  {message}")

def test_health_check():
    """Test 1: Vérification de la santé du microservice."""
    print_section("Test de santé du microservice")
    
    try:
        response = requests.get(f"{BASE_URL}/healthz", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print_success(f"Microservice en ligne - Status: {data.get('status')}")
            print_info(f"Version: {data.get('version')}")
            print_info(f"Redis: {data.get('redis')}")
            print_info(f"Listeners: {data.get('listeners_count')}")
            return True
        else:
            print_error(f"Microservice non accessible - Status: {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Erreur connexion microservice: {e}")
        return False

def test_debug_endpoint():
    """Test 2: Vérification de l'endpoint debug."""
    print_section("Test de l'endpoint debug")
    
    try:
        response = requests.get(f"{BASE_URL}/debug", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print_success("Endpoint debug accessible")
            print_info(f"Redis: {data.get('redis', {}).get('status')}")
            print_info(f"Firestore: {data.get('firestore', {}).get('status')}")
            print_info(f"Workflow listeners: {data.get('workflow_listeners', {}).get('status')}")
            return True
        else:
            print_error(f"Endpoint debug non accessible - Status: {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Erreur endpoint debug: {e}")
        return False

def test_redis_connection():
    """Test 3: Vérification de la connexion Redis locale."""
    print_section("Test de connexion Redis")
    
    try:
        r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
        r.ping()
        print_success("Connexion Redis locale OK")
        
        # Test d'écriture/lecture
        test_key = "test:unified_registry"
        test_value = {"timestamp": datetime.now().isoformat(), "test": True}
        r.set(test_key, json.dumps(test_value), ex=60)
        
        stored_value = r.get(test_key)
        if stored_value:
            print_success("Écriture/lecture Redis OK")
            print_info(f"Valeur stockée: {stored_value}")
        else:
            print_error("Impossible de lire la valeur Redis")
            return False
            
        r.delete(test_key)
        return True
        
    except Exception as e:
        print_error(f"Erreur connexion Redis: {e}")
        return False

def test_rpc_registry_legacy():
    """Test 4: Test des APIs de registre (mode legacy)."""
    print_section("Test APIs registre (mode legacy)")
    
    # Test d'enregistrement utilisateur
    test_user_id = f"test_user_{int(time.time())}"
    test_session_id = f"session_{int(time.time())}"
    
    rpc_payload = {
        "api_version": "v1",
        "method": "REGISTRY.register_user",
        "args": [test_user_id, test_session_id, "/test/route"],
        "kwargs": {},
        "user_id": test_user_id,
        "session_id": test_session_id,
        "idempotency_key": f"test_register_{int(time.time())}",
        "timeout_ms": 10000,
        "trace_id": f"trace_{int(time.time())}"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/rpc",
            json=rpc_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                print_success("Enregistrement utilisateur OK")
                print_info(f"Données retournées: {data.get('data')}")
                
                # Test de désenregistrement
                unreg_payload = {
                    "api_version": "v1",
                    "method": "REGISTRY.unregister_session",
                    "args": [test_session_id],
                    "kwargs": {},
                    "idempotency_key": f"test_unregister_{int(time.time())}",
                    "timeout_ms": 10000,
                    "trace_id": f"trace_unreg_{int(time.time())}"
                }
                
                unreg_response = requests.post(
                    f"{BASE_URL}/rpc",
                    json=unreg_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                if unreg_response.status_code == 200:
                    unreg_data = unreg_response.json()
                    if unreg_data.get("ok"):
                        print_success("Désenregistrement utilisateur OK")
                        return True
                    else:
                        print_error(f"Erreur désenregistrement: {unreg_data.get('error')}")
                        return False
                        
            else:
                print_error(f"Erreur enregistrement: {data.get('error')}")
                return False
        else:
            print_error(f"Erreur RPC - Status: {response.status_code}")
            return False
            
    except Exception as e:
        print_error(f"Erreur test RPC: {e}")
        return False

def test_unified_registry_direct():
    """Test 5: Test direct du registre unifié."""
    print_section("Test direct du registre unifié")
    
    # Activer temporairement le registre unifié
    os.environ["UNIFIED_REGISTRY_ENABLED"] = "true"
    
    try:
        # Import dynamique après configuration
        sys.path.append('.')
        from app.unified_registry import get_unified_registry
        from app.registry_wrapper import get_registry_wrapper
        
        registry = get_unified_registry()
        wrapper = get_registry_wrapper()
        
        print_info(f"Registre unifié activé: {wrapper.unified_enabled}")
        
        # Test d'enregistrement
        test_user_id = f"test_unified_{int(time.time())}"
        test_session_id = f"session_unified_{int(time.time())}"
        test_company_id = "test_company"
        
        result = registry.register_user_session(
            user_id=test_user_id,
            session_id=test_session_id,
            company_id=test_company_id,
            authorized_companies=[test_company_id],
            backend_route="/test/unified"
        )
        
        print_success("Enregistrement dans le registre unifié OK")
        print_info(f"Utilisateur: {result['user_info']['user_id']}")
        print_info(f"Société: {result['companies']['current_company_id']}")
        
        # Test de récupération
        user_registry = registry.get_user_registry(test_user_id)
        if user_registry:
            print_success("Récupération du registre utilisateur OK")
            print_info(f"Status: {user_registry['user_info']['status']}")
            print_info(f"Services: {list(user_registry['services'].keys())}")
        else:
            print_error("Impossible de récupérer le registre utilisateur")
            return False
        
        # Test de heartbeat
        heartbeat_ok = registry.update_user_heartbeat(test_user_id)
        if heartbeat_ok:
            print_success("Heartbeat utilisateur OK")
        else:
            print_error("Erreur heartbeat utilisateur")
        
        # Test de tâche
        task_data = registry.register_task(
            task_id=f"test_task_{int(time.time())}",
            task_type="test_task",
            user_id=test_user_id,
            company_id=test_company_id
        )
        
        print_success("Enregistrement de tâche OK")
        print_info(f"Tâche: {task_data['task_info']['task_id']}")
        print_info(f"Namespace: {task_data['isolation']['namespace']}")
        
        return True
        
    except Exception as e:
        print_error(f"Erreur test registre unifié: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_redis_keys_inspection():
    """Test 6: Inspection des clés Redis créées."""
    print_section("Inspection des clés Redis")
    
    try:
        r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
        
        # Clés de registre legacy
        legacy_keys = r.keys("registry:user:*")
        print_info(f"Clés registre legacy: {len(legacy_keys)}")
        for key in legacy_keys[:5]:  # Afficher les 5 premières
            print_info(f"  - {key}")
        
        # Clés de registre unifié
        unified_keys = r.keys("registry:unified:*")
        print_info(f"Clés registre unifié: {len(unified_keys)}")
        for key in unified_keys[:5]:  # Afficher les 5 premières
            print_info(f"  - {key}")
            # Afficher le contenu
            data = r.hget(key, "data")
            if data:
                try:
                    parsed = json.loads(data)
                    print_info(f"    Status: {parsed.get('user_info', {}).get('status')}")
                    print_info(f"    Société: {parsed.get('companies', {}).get('current_company_id')}")
                except:
                    pass
        
        # Clés de tâches
        task_keys = r.keys("registry:task:*")
        print_info(f"Clés de tâches: {len(task_keys)}")
        for key in task_keys[:3]:  # Afficher les 3 premières
            print_info(f"  - {key}")
        
        # Clés de sociétés
        company_keys = r.keys("registry:company:*")
        print_info(f"Clés de sociétés: {len(company_keys)}")
        for key in company_keys[:3]:  # Afficher les 3 premières
            print_info(f"  - {key}")
        
        return True
        
    except Exception as e:
        print_error(f"Erreur inspection Redis: {e}")
        return False

def test_wrapper_fallback():
    """Test 7: Test du fallback du wrapper."""
    print_section("Test du fallback du wrapper")
    
    try:
        # Désactiver temporairement le registre unifié
        os.environ["UNIFIED_REGISTRY_ENABLED"] = "false"
        
        # Import dynamique après configuration
        sys.path.append('.')
        from app.registry_wrapper import get_registry_wrapper
        
        wrapper = get_registry_wrapper()
        print_info(f"Registre unifié activé: {wrapper.unified_enabled}")
        
        if not wrapper.unified_enabled:
            print_success("Wrapper en mode legacy comme attendu")
            
            # Test d'enregistrement en mode legacy
            result = wrapper.register_user(
                f"test_legacy_{int(time.time())}", 
                f"session_legacy_{int(time.time())}", 
                "/test/legacy"
            )
            
            if result and "user_id" in result:
                print_success("Enregistrement legacy via wrapper OK")
                print_info(f"Résultat: {result}")
                return True
            else:
                print_error("Erreur enregistrement legacy")
                return False
        else:
            print_error("Wrapper devrait être en mode legacy")
            return False
            
    except Exception as e:
        print_error(f"Erreur test fallback: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Fonction principale de test."""
    print("🚀 Test du registre unifié en local")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    # Vérifier que Redis local est démarré
    print_info("Pré-requis: Redis local doit être démarré sur le port 6379")
    print_info("Commande: docker run -d --name redis-local -p 6379:6379 redis:alpine")
    print_info("Microservice doit être démarré: uvicorn app.main:app --host 0.0.0.0 --port 8080")
    
    tests = [
        ("Santé du microservice", test_health_check),
        ("Endpoint debug", test_debug_endpoint), 
        ("Connexion Redis", test_redis_connection),
        ("APIs registre legacy", test_rpc_registry_legacy),
        ("Registre unifié direct", test_unified_registry_direct),
        ("Inspection clés Redis", test_redis_keys_inspection),
        ("Test fallback wrapper", test_wrapper_fallback),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
            if success:
                print_success(f"Test '{test_name}' RÉUSSI")
            else:
                print_error(f"Test '{test_name}' ÉCHOUÉ")
        except Exception as e:
            print_error(f"Test '{test_name}' ERREUR: {e}")
            results.append((test_name, False))
        
        time.sleep(1)  # Pause entre les tests
    
    # Résumé final
    print_section("Résumé des tests")
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    print(f"Tests réussis: {passed}/{total}")
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
    
    if passed == total:
        print_success("🎉 Tous les tests sont passés ! Le registre unifié fonctionne correctement.")
    else:
        print_error(f"⚠️  {total - passed} test(s) ont échoué. Vérifiez la configuration.")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

