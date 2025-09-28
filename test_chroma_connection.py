#!/usr/bin/env python3
"""
Script de test pour vérifier la connexion ChromaDB dans le microservice.
Usage: python test_chroma_connection.py
"""

import os
import sys
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

def test_chroma_basic():
    """Test basique de connexion ChromaDB"""
    print("=== Test ChromaDB Basic ===")

    try:
        import chromadb
        import chromadb.utils.embedding_functions as embedding_functions

        print(f"✅ ChromaDB importé, version: {chromadb.__version__}")

        # Configuration depuis .env
        def safe_env(key, default=None):
            value = os.getenv(key, default)
            return None if value == "None" else value

        host = safe_env("CHROMA_HOST") or '35.180.247.70'
        port = safe_env("CHROMA_PORT") or '8000'
        ssl = safe_env("CHROMA_SSL") == "True"

        print(f"Configuration: {host}:{port}, ssl={ssl}")

        # Configuration minimale qui fonctionne
        client = chromadb.HttpClient(
            host=host,
            port=port,
            ssl=ssl
        )

        # Test heartbeat
        heartbeat = client.heartbeat()
        print(f"✅ Heartbeat: {heartbeat}")

        # Test liste collections
        collections = client.list_collections()
        print(f"✅ Collections disponibles ({len(collections)}): {[c.name for c in collections[:5]]}...")

        return True

    except Exception as e:
        print(f"❌ Erreur: {e}")
        print(f"Type: {type(e).__name__}")
        return False

def test_chroma_with_embeddings():
    """Test ChromaDB avec embeddings OpenAI"""
    print("\n=== Test ChromaDB avec Embeddings ===")

    try:
        import chromadb
        import chromadb.utils.embedding_functions as embedding_functions
        from app.tools.g_cred import get_secret

        # Configuration minimale qui fonctionne
        client = chromadb.HttpClient(
            host='35.180.247.70',
            port='8000',
            ssl=False
        )

        # Configuration embeddings
        api_key = get_secret('openai_pinnokio')
        embeddings = embedding_functions.OpenAIEmbeddingFunction(
            api_key=api_key,
            model_name='text-embedding-ada-002'
        )
        print("✅ Embeddings OpenAI configurés")

        # Test collection avec embeddings
        collection = client.get_or_create_collection(
            name="test_microservice_embeddings",
            embedding_function=embeddings
        )
        print(f"✅ Collection créée: {collection.name}")

        # Test ajout document
        collection.add(
            documents=["Test document pour microservice ChromaDB"],
            metadatas=[{"source": "test", "app": "microservice"}],
            ids=["test_microservice_doc"]
        )
        print("✅ Document ajouté avec embeddings")

        # Test query
        results = collection.query(
            query_texts=["test microservice"],
            n_results=1
        )
        print(f"✅ Query réussie: {len(results['documents'][0])} résultats")

        # Nettoyage
        collection.delete(ids=["test_microservice_doc"])
        print("✅ Document test supprimé")

        return True

    except Exception as e:
        print(f"❌ Erreur embeddings: {e}")
        print(f"Type: {type(e).__name__}")
        return False

def test_chroma_service_class():
    """Test de la classe ChromaVectorService"""
    print("\n=== Test ChromaVectorService ===")

    try:
        from app.chroma_vector_service import ChromaVectorService

        # Créer instance
        service = ChromaVectorService()
        print("✅ ChromaVectorService initialisé")

        # Test méthodes RPC
        result = service.get_collection_info("test_microservice_class")
        print(f"✅ get_collection_info: {result}")

        # Test ajout documents
        add_result = service.add_documents(
            collection_name="test_microservice_class",
            documents=["Document test pour classe service"],
            metadatas=[{"source": "service_test", "type": "rpc_test"}]
        )
        print(f"✅ add_documents: {add_result}")

        # Test query
        query_result = service.query_documents(
            collection_name="test_microservice_class",
            query_texts=["document test"],
            n_results=1
        )
        print(f"✅ query_documents: {query_result['success']}")

        # Test analyse
        analysis = service.analyze_collection("test_microservice_class")
        print(f"✅ analyze_collection: {analysis['success']}")

        return True

    except Exception as e:
        print(f"❌ Erreur service: {e}")
        print(f"Type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Test de connexion ChromaDB pour le microservice")
    print("=" * 60)

    # Ajout du chemin du projet
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    tests = [
        ("Test basique", test_chroma_basic),
        ("Test avec embeddings", test_chroma_with_embeddings),
        ("Test service class", test_chroma_service_class)
    ]

    results = []
    for name, test_func in tests:
        print(f"\n📋 {name}...")
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"❌ Erreur critique dans {name}: {e}")
            results.append((name, False))

    # Résumé
    print("\n" + "=" * 60)
    print("📊 RÉSUMÉ DES TESTS")
    print("=" * 60)

    passed = 0
    for name, success in results:
        status = "✅ PASSÉ" if success else "❌ ÉCHOUÉ"
        print(f"{status} - {name}")
        if success:
            passed += 1

    print(f"\n🎯 Résultat: {passed}/{len(results)} tests réussis")

    if passed == len(results):
        print("🎉 Tous les tests ChromaDB ont réussi!")
    else:
        print("⚠️ Certains tests ont échoué. Vérifiez la configuration.")