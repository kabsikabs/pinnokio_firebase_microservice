"""
Test script pour vérifier les données Router dans Redis et comprendre pourquoi l'agent ne les voit pas.

Ce script :
1. Charge les données Router depuis Redis (comme le fait l'agent)
2. Affiche la structure complète des données
3. Compare avec ce que RouterJobTools attend
"""

import asyncio
import json
import sys
import os

# Ajouter le répertoire parent au path pour importer les modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.redis_client import get_redis
from app.pinnokio_agentic_workflow.tools.job_loader import JobLoader


async def test_router_redis_data():
    """Test principal pour diagnostiquer les données Router."""
    
    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION (À ADAPTER SELON VOTRE ENVIRONNEMENT)
    # ═══════════════════════════════════════════════════════════════
    
    # TODO: Remplacer par vos valeurs réelles
    user_id = "BHlZ7YMYMXicWIYRYsqEkXcnzL2"  # Ex: "Xs1PSmFz4AXt0..."
    company_id = "klk_space_id_8b2dce"  # Ex: "klk_space_id_8b2dce"
    
    print("=" * 80)
    print("🔍 DIAGNOSTIC ROUTER - REDIS vs AGENT")
    print("=" * 80)
    print(f"User ID: {user_id}")
    print(f"Company ID: {company_id}")
    print()
    
    # ═══════════════════════════════════════════════════════════════
    # ÉTAPE 1 : LECTURE DIRECTE REDIS
    # ═══════════════════════════════════════════════════════════════
    
    print("─" * 80)
    print("📥 ÉTAPE 1 : Lecture directe depuis Redis")
    print("─" * 80)
    
    redis_client = get_redis()
    cache_key = f"jobs:{user_id}:{company_id}:ROUTER"
    
    print(f"Clé Redis: {cache_key}")
    
    try:
        cached_data = redis_client.get(cache_key)
        
        if cached_data:
            print("✅ Données trouvées dans Redis")
            
            # Décoder les données
            router_data = json.loads(cached_data)
            
            print(f"\n🔑 Clés principales : {list(router_data.keys())}")
            
            # Afficher la structure complète
            print("\n📊 Structure complète des données :")
            print(json.dumps(router_data, indent=2, default=str)[:2000])  # Limité à 2000 chars
            
            # Compter les documents par statut
            print("\n📈 Statistiques :")
            for status_key in ["to_process", "in_process", "processed"]:
                if status_key in router_data:
                    data = router_data[status_key]
                    if isinstance(data, list):
                        print(f"  • {status_key}: {len(data)} documents")
                    else:
                        print(f"  • {status_key}: Type invalide ({type(data).__name__})")
                else:
                    print(f"  • {status_key}: Clé manquante ❌")
            
            # Afficher un exemple de document (s'il existe)
            print("\n📄 Exemple de document (premier to_process) :")
            if "to_process" in router_data and isinstance(router_data["to_process"], list):
                if len(router_data["to_process"]) > 0:
                    first_doc = router_data["to_process"][0]
                    print(json.dumps(first_doc, indent=2, default=str))
                else:
                    print("  ⚠️ Liste 'to_process' vide")
            else:
                print("  ❌ Pas de clé 'to_process' ou type invalide")
        
        else:
            print("❌ Aucune donnée trouvée dans Redis")
            print("   → Les données doivent être chargées d'abord depuis Drive/Firebase")
    
    except Exception as e:
        print(f"❌ Erreur lecture Redis : {e}")
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # ÉTAPE 2 : CHARGEMENT VIA JOBLOADER (COMME L'AGENT)
    # ═══════════════════════════════════════════════════════════════
    
    print("\n" + "─" * 80)
    print("🤖 ÉTAPE 2 : Chargement via JobLoader (comme l'agent)")
    print("─" * 80)
    
    try:
        loader = JobLoader(
            user_id=user_id,
            company_id=company_id
        )
        
        # Simuler un user_context minimal (pour le fallback Drive)
        user_context = {
            "input_drive_doc_id": "15S5X9pi3wfUnhSx2Z7bcJxUZwXk93DTt",  # Dossier Drive Input
            "mandate_input_drive_doc_id": "15S5X9pi3wfUnhSx2Z7bcJxUZwXk93DTt"  # Dossier Drive Input
        }
        
        # Charger en mode UI (avec cache Redis)
        print("\nMode UI (cache Redis prioritaire) :")
        router_data_ui = await loader.load_router_jobs(mode="UI", user_context=user_context)
        
        print(f"✅ Données chargées (mode UI)")
        print(f"   Clés : {list(router_data_ui.keys())}")
        
        for status_key in ["to_process", "in_process", "processed"]:
            if status_key in router_data_ui:
                data = router_data_ui[status_key]
                if isinstance(data, list):
                    print(f"   • {status_key}: {len(data)} documents")
                else:
                    print(f"   • {status_key}: Type invalide ({type(data).__name__})")
    
    except Exception as e:
        print(f"❌ Erreur JobLoader : {e}")
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # ÉTAPE 3 : SIMULATION ROUTERJOBTOOL S (CE QUE L'AGENT UTILISE)
    # ═══════════════════════════════════════════════════════════════
    
    print("\n" + "─" * 80)
    print("🛠️  ÉTAPE 3 : Simulation RouterJobTools (outil utilisé par l'agent)")
    print("─" * 80)
    
    try:
        from app.pinnokio_agentic_workflow.tools.job_tools import RouterJobTools
        
        # Simuler les jobs_data comme dans la session
        jobs_data = {
            "ROUTER": router_data_ui  # Utiliser les données chargées par JobLoader
        }
        
        # Créer l'outil
        router_tools = RouterJobTools(jobs_data=jobs_data)
        
        print("\n📋 Test de recherche avec RouterJobTools :")
        
        # Test 1 : Recherche par défaut (to_process)
        print("\n  🔍 Test 1 : Recherche par défaut (status='to_process')")
        result = await router_tools.search(status="to_process")
        
        print(f"     Success: {result.get('success')}")
        print(f"     Total trouvé: {result.get('total_found')}")
        print(f"     Résumé: {result.get('summary')}")
        
        if result.get('results'):
            print(f"\n     Premier résultat :")
            first_result = result['results'][0]
            print(f"       - drive_file_id: {first_result.get('drive_file_id')}")
            print(f"       - file_name: {first_result.get('file_name')}")
            print(f"       - status: {first_result.get('status')}")
        
        # Test 2 : Recherche tous statuts
        print("\n  🔍 Test 2 : Recherche tous statuts (status='all')")
        result_all = await router_tools.search(status="all")
        
        print(f"     Success: {result_all.get('success')}")
        print(f"     Total trouvé: {result_all.get('total_found')}")
        print(f"     Résumé: {result_all.get('summary')}")
    
    except Exception as e:
        print(f"❌ Erreur RouterJobTools : {e}")
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # ÉTAPE 4 : DIAGNOSTIC ET RECOMMANDATIONS
    # ═══════════════════════════════════════════════════════════════
    
    print("\n" + "=" * 80)
    print("🔬 DIAGNOSTIC")
    print("=" * 80)
    
    print("\n✅ Ce qui devrait fonctionner :")
    print("   1. Redis contient des données avec clés 'to_process', 'in_process', 'processed'")
    print("   2. JobLoader charge ces données depuis Redis (mode UI)")
    print("   3. RouterJobTools reçoit jobs_data avec ROUTER.to_process[]")
    print("   4. L'agent peut chercher avec status='to_process' (même clé)")
    
    print("\n❌ Problèmes potentiels identifiés :")
    
    # Vérifier les problèmes courants
    issues = []
    
    if cached_data:
        router_data = json.loads(cached_data)
        
        # Problème 1 : Clés manquantes
        if "to_process" not in router_data:
            issues.append("⚠️  Clé 'to_process' manquante dans Redis (devrait exister)")
        
        # Problème 2 : Liste vide
        if "to_process" in router_data and isinstance(router_data["to_process"], list):
            if len(router_data["to_process"]) == 0:
                issues.append("⚠️  Liste 'to_process' vide (pas de documents à router)")
        
        # Problème 3 : Type incorrect
        if "to_process" in router_data and not isinstance(router_data["to_process"], list):
            issues.append(f"❌ Type invalide pour 'to_process': {type(router_data['to_process']).__name__} (devrait être list)")
        
        # Problème 4 : Format de document incorrect
        if "to_process" in router_data and isinstance(router_data["to_process"], list):
            if len(router_data["to_process"]) > 0:
                first_doc = router_data["to_process"][0]
                if "id" not in first_doc:
                    issues.append("❌ Champ 'id' manquant dans document (requis pour drive_file_id)")
                if "name" not in first_doc:
                    issues.append("❌ Champ 'name' manquant dans document (requis pour file_name)")
    else:
        issues.append("❌ Aucune donnée dans Redis - Données non chargées")
    
    if issues:
        for issue in issues:
            print(f"   {issue}")
    else:
        print("   ✅ Aucun problème détecté !")
    
    print("\n💡 Recommandations :")
    print("   1. Vérifiez que les données sont chargées dans Redis au démarrage de session")
    print("   2. Vérifiez que input_drive_doc_id est configuré dans user_context")
    print("   3. Assurez-vous que les documents Drive existent réellement")
    print("   4. Vérifiez les logs lors de l'initialisation de la session LLM")
    print("   5. Testez avec un refresh manuel des jobs (via endpoint /refresh_jobs)")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    print("\n🚀 Démarrage du diagnostic Router Redis...\n")
    
    # Exécuter le test
    asyncio.run(test_router_redis_data())

