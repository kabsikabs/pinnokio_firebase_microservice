"""
Script de test simple pour les nouveaux endpoints cache.
Lance le serveur en arrière-plan et teste les endpoints RPC.
"""

import asyncio
import aiohttp
import json
from typing import Dict, Any

# Configuration
BASE_URL = "http://localhost:8000"
RPC_ENDPOINT = f"{BASE_URL}/rpc"

# Test data
TEST_USER_ID = "test-user-123"
TEST_COMPANY_ID = "test-company-456"
TEST_DRIVE_ID = "test-drive-folder-789"


async def rpc_call(
    session: aiohttp.ClientSession,
    method: str,
    kwargs: Dict[str, Any] = None,
    user_id: str = TEST_USER_ID
) -> Dict[str, Any]:
    """Effectue un appel RPC vers le backend."""
    payload = {
        "method": method,
        "kwargs": kwargs or {},
        "user_id": user_id,
        "args": []
    }

    try:
        async with session.post(RPC_ENDPOINT, json=payload) as response:
            result = await response.json()
            return result
    except Exception as e:
        return {"error": str(e)}


async def test_firebase_cache_endpoints():
    """Test des endpoints FIREBASE_CACHE."""
    print("\n" + "="*70)
    print("🧪 TEST: FIREBASE_CACHE Endpoints")
    print("="*70)

    async with aiohttp.ClientSession() as session:

        # Test 1: get_mandate_snapshot
        print("\n📋 Test: FIREBASE_CACHE.get_mandate_snapshot")
        result = await rpc_call(
            session,
            "FIREBASE_CACHE.get_mandate_snapshot",
            kwargs={"company_id": TEST_COMPANY_ID}
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Source: {data.get('source', 'unknown')}")
            print(f"   Data exists: {data.get('data') is not None}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")

        # Test 2: get_expenses
        print("\n💰 Test: FIREBASE_CACHE.get_expenses")
        result = await rpc_call(
            session,
            "FIREBASE_CACHE.get_expenses",
            kwargs={"company_id": TEST_COMPANY_ID}
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Source: {data.get('source', 'unknown')}")
            expenses = data.get('data', [])
            print(f"   Expenses count: {len(expenses) if isinstance(expenses, list) else 0}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")

        # Test 3: get_ap_documents
        print("\n📄 Test: FIREBASE_CACHE.get_ap_documents")
        result = await rpc_call(
            session,
            "FIREBASE_CACHE.get_ap_documents",
            kwargs={"company_id": TEST_COMPANY_ID}
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Source: {data.get('source', 'unknown')}")
            docs = data.get('data', [])
            print(f"   Documents count: {len(docs) if isinstance(docs, list) else 0}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")

        # Test 4: get_bank_transactions
        print("\n🏦 Test: FIREBASE_CACHE.get_bank_transactions")
        result = await rpc_call(
            session,
            "FIREBASE_CACHE.get_bank_transactions",
            kwargs={"company_id": TEST_COMPANY_ID}
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Source: {data.get('source', 'unknown')}")
            txs = data.get('data', [])
            print(f"   Transactions count: {len(txs) if isinstance(txs, list) else 0}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")

        # Test 5: get_approval_pendinglist
        print("\n✅ Test: FIREBASE_CACHE.get_approval_pendinglist")
        result = await rpc_call(
            session,
            "FIREBASE_CACHE.get_approval_pendinglist",
            kwargs={
                "company_id": TEST_COMPANY_ID,
                "department": "expenses"
            }
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Source: {data.get('source', 'unknown')}")
            items = data.get('data', [])
            print(f"   Pending items count: {len(items) if isinstance(items, list) else 0}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")


async def test_drive_cache_endpoints():
    """Test des endpoints DRIVE_CACHE."""
    print("\n" + "="*70)
    print("🧪 TEST: DRIVE_CACHE Endpoints")
    print("="*70)

    async with aiohttp.ClientSession() as session:

        # Test 1: get_documents
        print("\n📁 Test: DRIVE_CACHE.get_documents")
        result = await rpc_call(
            session,
            "DRIVE_CACHE.get_documents",
            kwargs={
                "company_id": TEST_COMPANY_ID,
                "input_drive_id": TEST_DRIVE_ID
            }
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Source: {data.get('source', 'unknown')}")
            print(f"   OAuth Error: {data.get('oauth_error', False)}")
            if data.get('oauth_error'):
                print(f"   Message: {data.get('error_message', 'No message')}")
            else:
                drive_data = data.get('data', {})
                if isinstance(drive_data, dict):
                    print(f"   To process: {len(drive_data.get('to_process', []))}")
                    print(f"   In process: {len(drive_data.get('in_process', []))}")
                    print(f"   Processed: {len(drive_data.get('processed', []))}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")

        # Test 2: invalidate_cache
        print("\n🗑️  Test: DRIVE_CACHE.invalidate_cache")
        result = await rpc_call(
            session,
            "DRIVE_CACHE.invalidate_cache",
            kwargs={"company_id": TEST_COMPANY_ID}
        )
        print(f"   Status: {'✅' if result.get('ok') else '❌'}")
        if result.get('ok'):
            data = result.get('data', {})
            print(f"   Success: {data.get('success', False)}")
        else:
            print(f"   Error: {result.get('error', 'Unknown error')}")


async def test_health_check():
    """Test de santé du serveur."""
    print("\n" + "="*70)
    print("🏥 TEST: Health Check")
    print("="*70)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/") as response:
                status = response.status
                print(f"\n   Server status: {'✅ Running' if status == 200 else f'❌ Error {status}'}")
                if status == 200:
                    text = await response.text()
                    print(f"   Response: {text[:100]}")
                return status == 200
    except Exception as e:
        print(f"\n   ❌ Server not reachable: {e}")
        return False


async def main():
    """Fonction principale de test."""
    print("\n" + "="*70)
    print("🚀 TESTS DES NOUVEAUX ENDPOINTS CACHE")
    print("="*70)
    print(f"\n📍 Backend URL: {BASE_URL}")
    print(f"👤 Test User ID: {TEST_USER_ID}")
    print(f"🏢 Test Company ID: {TEST_COMPANY_ID}")

    # Vérifier que le serveur est accessible
    server_ok = await test_health_check()

    if not server_ok:
        print("\n" + "="*70)
        print("⚠️  SERVEUR NON ACCESSIBLE")
        print("="*70)
        print("\nPour démarrer le serveur:")
        print("   cd firebase_microservice")
        print("   python -m uvicorn app.main:app --reload --port 8000")
        print("\nPuis relancez ce script:")
        print("   python test_cache_endpoints.py")
        return

    # Tests FIREBASE_CACHE
    await test_firebase_cache_endpoints()

    # Tests DRIVE_CACHE
    await test_drive_cache_endpoints()

    # Résumé
    print("\n" + "="*70)
    print("📊 RÉSUMÉ DES TESTS")
    print("="*70)
    print("\n✅ Tests terminés!")
    print("\nNotes importantes:")
    print("   - Les endpoints retournent des données même si Firebase est vide")
    print("   - Source 'cache' = données depuis Redis")
    print("   - Source 'firebase' = données depuis Firestore")
    print("   - OAuth errors sont normales si pas de credentials Drive valides")
    print("\n" + "="*70)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrompus par l'utilisateur")
    except Exception as e:
        print(f"\n\n❌ Erreur lors des tests: {e}")
        import traceback
        traceback.print_exc()
