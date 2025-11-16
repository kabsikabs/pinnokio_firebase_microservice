"""
Test HTTP du callback LPT avec le FORMAT EXACT envoyé par les agents
"""
import asyncio
import httpx
import json

# ✅ FORMAT EXACT que les agents externes (Router/APBookkeeper/Banker) renvoient
# Ce format contient TOUT le payload original + un champ "response"
callback_data = {
    # 1. IDENTIFIANTS (du payload original)
    "collection_name": "klk_space_id_8b2dce",
    "user_id": "4BHlZ7YMYMXicWIYRYsqEkXcnzL2",
    "client_uuid": "client_test_12345",
    "mandates_path": "clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/bo_clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/mandates/9wWAKnxEMsOFe4annCXo",
    "batch_id": "ap_batch_test_callback_streaming",
    
    # 2. DONNÉES DE LA TÂCHE (du payload original)
    "jobs_data": [{
        "file_name": "test_fiche_salaire.pdf",
        "drive_file_id": "1test_streaming_debug",
        "instructions": "Document test pour debug streaming",
        "status": "to_route",
        "approval_required": False,
        "automated_workflow": True
    }],
    
    # 3. CONFIGURATION (du payload original)
    "settings": [
        {"communication_mode": "telegram"},
        {"log_communication_mode": "pinnokio"},
        {"dms_system": "google_drive"}
    ],
    
    # 4. TRAÇABILITÉ (du payload original)
    "traceability": {
        "thread_key": "virgin_chat_6333d6e8",  # Utiliser un thread existant
        "thread_name": "Test Debug Streaming Callback",
        "execution_id": None,
        "execution_plan": None,
        "initiated_at": "2025-10-27T10:00:00.000000+00:00",
        "source": "pinnokio_brain"
    },
    
    # 5. IDENTIFIANTS ADDITIONNELS
    "pub_sub_id": "router_test_streaming_callback",
    "start_instructions": None,
    
    # 6. ⭐ RÉPONSE DU LPT (C'EST ICI que status/result/error sont maintenant)
    "response": {
        "status": "completed",
        "result": {
            "summary": """Résumé du traitement de test

Le document test_fiche_salaire.pdf a été analysé avec succès. Il s'agit d'un document de type fiche de salaire qui a été correctement routé vers le département des Ressources Humaines.

Ce test permet de vérifier que le streaming fonctionne correctement lors du retour d'un callback LPT. Le message devrait s'afficher progressivement en temps réel côté frontend.

Si vous voyez ce message apparaître d'un coup (sans streaming), c'est qu'il y a un problème dans la diffusion des chunks WebSocket."""
        },
        "error": None
    },
    
    # 7. MÉTADONNÉES D'EXÉCUTION
    "execution_time": "45.2s",
    "completed_at": "2025-10-27T10:01:00Z",
    "logs_url": None
}


async def test_lpt_callback_http():
    """
    Envoie un POST HTTP vers /lpt/callback avec le FORMAT EXACT
    """
    url = "http://127.0.0.1:8090/lpt/callback"
    
    print("=" * 80)
    print("🧪 TEST CALLBACK LPT - FORMAT EXACT DES AGENTS")
    print("=" * 80)
    print(f"\n📍 URL: {url}")
    print(f"\n📦 Payload (FORMAT COMPLET avec response):")
    print(json.dumps(callback_data, indent=2, ensure_ascii=False))
    print("\n" + "=" * 80)
    print("🚀 Envoi de la requête...\n")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=callback_data
            )
            
            print(f"📊 Réponse HTTP:")
            print(f"  Status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"\n✅ Callback accepté avec succès !")
                print(f"\n💡 MAINTENANT:")
                print(f"  1. Ouvrez le chat thread_key=virgin_chat_6333d6e8")
                print(f"  2. Observez si le message apparaît en STREAMING")
                print(f"  3. Ou si il apparaît d'un coup")
                print(f"\n📋 Dans les logs serveur, cherchez:")
                print(f"  - llm_stream_start ✓")
                print(f"  - llm_stream_chunk (DEVRAIT apparaître plusieurs fois) ⚠️")
                print(f"  - llm_stream_complete ✓")
            else:
                print(f"\n❌ Erreur HTTP {response.status_code}")
                print(f"📄 Réponse: {response.text}")
            
    except httpx.ConnectError:
        print("❌ Impossible de se connecter au serveur")
        print("⚠️  Assurez-vous que le serveur FastAPI tourne sur le port 8090")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()


async def main():
    print("""
╔════════════════════════════════════════════════════════════════╗
║        TEST CALLBACK LPT - DEBUG STREAMING                     ║
╚════════════════════════════════════════════════════════════════╝

Ce test envoie un callback avec le FORMAT EXACT des agents externes.

⚠️  IMPORTANT: Ouvrez le chat virgin_chat_6333d6e8 AVANT de lancer !

Appuyez sur Entrée pour continuer...
""")
    
    input()
    await test_lpt_callback_http()
    
    print("\n" + "=" * 80)
    print("❓ AVEZ-VOUS VU LE MESSAGE EN STREAMING ?")
    print("=" * 80)
    print("""
OUI → Le problème est ailleurs (frontend?)
NON → Le problème est dans le backend (broadcast des chunks)

Cherchez dans les logs entre:
  - HTTP Request: POST https://api.anthropic.com/v1/messages
  - "Texte simple sans outils → Mission complétée"

Devrait contenir des lignes:
  ws_broadcast type=llm_stream_chunk ← SI ABSENT = BUG ICI !
""")


if __name__ == "__main__":
    asyncio.run(main())
