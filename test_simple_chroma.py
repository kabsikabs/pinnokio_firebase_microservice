#!/usr/bin/env python3
"""
Test minimal ChromaDB - configuration progressive
"""

import chromadb

print("=== Test ChromaDB Minimal ===")
print(f"Version ChromaDB: {chromadb.__version__}")

# Test 1: Configuration minimale
print("\n1. Test configuration minimale...")
try:
    client = chromadb.HttpClient(
        host='35.180.247.70',
        port='8000',
        ssl=False
    )
    heartbeat = client.heartbeat()
    print(f"✅ Config minimale OK, heartbeat: {heartbeat}")
except Exception as e:
    print(f"❌ Config minimale échoue: {e}")

# Test 2: Avec headers et settings à None
print("\n2. Test avec headers/settings None...")
try:
    client = chromadb.HttpClient(
        host='35.180.247.70',
        port='8000',
        ssl=False,
        headers=None,
        settings=None
    )
    heartbeat = client.heartbeat()
    print(f"✅ Avec None OK, heartbeat: {heartbeat}")
except Exception as e:
    print(f"❌ Avec None échoue: {e}")

# Test 3: Essayer d'ajouter tenant/database
print("\n3. Test avec tenant/database...")
try:
    client = chromadb.HttpClient(
        host='35.180.247.70',
        port='8000',
        ssl=False,
        headers=None,
        settings=None,
        tenant='default_tenant',
        database='default_database'
    )
    heartbeat = client.heartbeat()
    print(f"✅ Avec tenant/database OK, heartbeat: {heartbeat}")
except Exception as e:
    print(f"❌ Avec tenant/database échoue: {e}")

# Test 4: Vérifier les paramètres acceptés
print("\n4. Inspection des paramètres HttpClient...")
import inspect
sig = inspect.signature(chromadb.HttpClient)
print(f"Paramètres acceptés: {list(sig.parameters.keys())}")

print("\n=== FIN DES TESTS ===")