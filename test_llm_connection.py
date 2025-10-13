"""
Test de connexion LLM entre le microservice et Reflex.
Teste les appels RPC LLM.initialize_session et LLM.send_message.
"""

import asyncio
import json
import uuid
from datetime import datetime


async def test_llm_rpc_connection():
    """
    Teste la connexion LLM via RPC.
    Ce test simule les appels que Reflex ferait au microservice.
    """
    
    print("\n🔍 Test de connexion LLM microservice\n")
    print("=" * 60)
    
    # 1. Importer le gestionnaire LLM
    try:
        from app.llm_service import get_llm_manager
        print("✅ Import du LLM Manager réussi")
    except Exception as e:
        print(f"❌ Erreur import LLM Manager: {e}")
        return
    
    # 2. Paramètres de test
    test_user_id = "test_user_123"
    test_collection = "test_company_456"
    test_space_code = "test_space"
    test_thread = f"thread_{uuid.uuid4().hex[:8]}"
    
    print(f"\n📝 Paramètres de test:")
    print(f"   - User ID: {test_user_id}")
    print(f"   - Collection: {test_collection}")
    print(f"   - Space Code: {test_space_code}")
    print(f"   - Thread: {test_thread}")
    
    # 3. Test d'initialisation de session
    print("\n🚀 Test 1: Initialisation de session LLM")
    print("-" * 60)
    
    try:
        llm_manager = get_llm_manager()
        result = await llm_manager.initialize_session(
            user_id=test_user_id,
            collection_name=test_collection,
            dms_system="google_drive",
            dms_mode="prod",
            chat_mode="general_chat"
        )
        
        if result.get("success"):
            print(f"✅ Session initialisée: {result.get('session_id')}")
            print(f"   Status: {result.get('status')}")
            print(f"   Message: {result.get('message')}")
            
            # Ajouter un system prompt pour le test
            session_key = f"{test_user_id}:{test_collection}"
            if session_key in llm_manager.sessions:
                session = llm_manager.sessions[session_key]
                if session.agent:
                    system_prompt = "Tu es un assistant de test pour le microservice LLM. Réponds de manière concise et professionnelle."
                    session.agent.update_system_prompt(system_prompt)
                    print(f"✅ System prompt configuré pour le test")
        else:
            print(f"❌ Échec initialisation: {result.get('error')}")
            return
    except Exception as e:
        print(f"❌ Exception lors de l'initialisation: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 4. Test d'envoi de message (sans Firebase RTDB réel - mode dry-run)
    print("\n💬 Test 2: Envoi de message LLM (simulation)")
    print("-" * 60)
    
    try:
        # Note: Ce test échouera probablement si Firebase RTDB n'est pas configuré
        # Mais il valide la structure RPC
        test_message = "Bonjour, c'est un test de connexion!"
        
        print(f"   Message: '{test_message}'")
        
        # Simulation sans Firebase
        result = await llm_manager.send_message(
            user_id=test_user_id,
            collection_name=test_collection,
            space_code=test_space_code,
            chat_thread=test_thread,
            message=test_message,
            chat_mode="general_chat"
        )
        
        if result.get("success"):
            print(f"✅ Message envoyé avec succès")
            print(f"   User Message ID: {result.get('user_message_id')}")
            print(f"   Assistant Message ID: {result.get('assistant_message_id')}")
            print(f"   Message: {result.get('message')}")
        else:
            print(f"⚠️  Résultat: {result.get('error')}")
            print("   (Normal si Firebase RTDB n'est pas configuré)")
    except Exception as e:
        print(f"⚠️  Exception lors de l'envoi: {e}")
        print("   (Normal si Firebase RTDB n'est pas configuré)")
    
    # 5. Vérifier la session en cache
    print("\n📊 Test 3: Vérification du cache de session")
    print("-" * 60)
    
    try:
        session_key = f"{test_user_id}:{test_collection}"
        if session_key in llm_manager.sessions:
            session = llm_manager.sessions[session_key]
            print(f"✅ Session trouvée en cache: {session_key}")
            print(f"   Créée à: {session.created_at.isoformat()}")
            print(f"   Agent initialisé: {session.agent is not None}")
            if session.agent:
                print(f"   Provider par défaut: {session.agent.default_provider}")
                print(f"   Collection: {session.agent.collection_name}")
        else:
            print(f"❌ Session non trouvée en cache")
    except Exception as e:
        print(f"❌ Erreur vérification cache: {e}")
    
    # 6. Test de réutilisation de session
    print("\n🔄 Test 4: Réutilisation de session existante")
    print("-" * 60)
    
    try:
        result = await llm_manager.initialize_session(
            user_id=test_user_id,
            collection_name=test_collection,
            dms_system="google_drive",
            dms_mode="dev",
            chat_mode="general_chat"
        )
        
        if result.get("status") == "existing":
            print(f"✅ Session existante réutilisée correctement")
        else:
            print(f"⚠️  Nouvelle session créée (attendu: existante)")
    except Exception as e:
        print(f"❌ Erreur réutilisation: {e}")
    
    # Résumé
    print("\n" + "=" * 60)
    print("✅ Tests de connexion LLM terminés")
    print("\n📌 Prochaines étapes:")
    print("   1. Configurer Firebase RTDB pour le streaming")
    print("   2. Tester depuis l'application Reflex")
    print("   3. Vérifier les listeners RTDB dans Reflex")
    print("=" * 60 + "\n")


async def test_rpc_format():
    """
    Teste le format RPC attendu par Reflex.
    Simule exactement ce que Reflex enverrait.
    """
    
    print("\n🔍 Test du format RPC (simulation Reflex)\n")
    print("=" * 60)
    
    # Format RPC attendu
    test_rpc_request = {
        "api_version": "v1",
        "method": "LLM.initialize_session",
        "args": {
            "user_id": "test_user_123",
            "collection_name": "test_company_456",
            "dms_system": "google_drive",
            "dms_mode": "prod",
            "chat_mode": "general_chat"
        },
        "trace_id": f"trace_{uuid.uuid4().hex[:12]}"
    }
    
    print("📤 Format RPC envoyé par Reflex:")
    print(json.dumps(test_rpc_request, indent=2))
    
    # Simuler le traitement RPC côté microservice
    try:
        from app.main import _resolve_method
        
        method_name = test_rpc_request["method"]
        print(f"\n🔧 Résolution de la méthode: {method_name}")
        
        func, namespace = _resolve_method(method_name)
        print(f"✅ Méthode résolue dans namespace: {namespace}")
        
        # Appeler la fonction
        print(f"\n⚙️  Exécution de la méthode...")
        result = await func(**test_rpc_request["args"])
        
        print(f"\n📥 Réponse du microservice:")
        print(json.dumps(result, indent=2, default=str))
        
        if result.get("success"):
            print(f"\n✅ Format RPC valide et fonctionnel!")
        else:
            print(f"\n⚠️  La méthode a répondu mais avec une erreur")
    except Exception as e:
        print(f"\n❌ Erreur dans le traitement RPC: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🚀 TESTS DE CONNEXION LLM MICROSERVICE")
    print("=" * 60)
    
    # Test 1: Connexion directe
    asyncio.run(test_llm_rpc_connection())
    
    # Test 2: Format RPC
    asyncio.run(test_rpc_format())
    
    print("\n✅ Tous les tests sont terminés!\n")


