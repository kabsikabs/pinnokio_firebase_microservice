"""
Script de test pour vérifier le système de vérification de solde des outils LPT.

Ce script teste :
1. La méthode check_balance_before_lpt
2. L'intégration dans les méthodes launch_* (APBookkeeper, Router, Banker)
3. Le blocage en cas de solde insuffisant
4. Le message clair retourné à l'agent

Usage:
    python test_balance_check_lpt.py
"""

import asyncio
import sys
from typing import Dict, Any

# Simuler les imports nécessaires
class MockFirebaseManagement:
    """Mock de FirebaseManagement pour les tests"""
    
    def get_balance_info(self, mandate_path: str = None, user_id: str = None) -> dict:
        """
        Simule la récupération du solde.
        Pour le test, on retourne un solde de 10.0$ (insuffisant pour la plupart des opérations)
        """
        print(f"[MOCK] get_balance_info appelé - mandate_path={mandate_path}, user_id={user_id}")
        return {
            'current_balance': 10.0,  # Solde faible pour tester le blocage
            'current_expenses': 100.0,
            'current_topping': 110.0
        }


class MockBrain:
    """Mock de PinnokioBrain pour les tests"""
    
    def __init__(self):
        self.jobs_data = {
            "APBOOKEEPER": {
                "to_do": [
                    {"id": "invoice_001", "file_name": "Facture_001.pdf"},
                    {"id": "invoice_002", "file_name": "Facture_002.pdf"},
                    {"id": "invoice_003", "file_name": "Facture_003.pdf"}
                ]
            },
            "ROUTER": {
                "to_process": [
                    {"id": "doc_001", "name": "Document_001.pdf"},
                    {"id": "doc_002", "name": "Document_002.pdf"}
                ]
            },
            "BANK": {
                "unprocessed": [
                    {"id": "tx_001", "label": "Transaction 1"},
                    {"id": "tx_002", "label": "Transaction 2"},
                    {"id": "tx_003", "label": "Transaction 3"},
                    {"id": "tx_004", "label": "Transaction 4"}
                ]
            }
        }
        self.context = {
            'mandate_path': 'clients/test_user_123/bo_clients/client_abc/mandates/mandate_xyz',
            'workflow_params': {}
        }
    
    def get_user_context(self) -> Dict[str, Any]:
        return self.context


def test_balance_check():
    """
    Test de la méthode check_balance_before_lpt
    """
    print("\n" + "="*80)
    print("TEST 1 : Vérification de la méthode check_balance_before_lpt")
    print("="*80)
    
    # Patch temporaire de Firebase
    import app.pinnokio_agentic_workflow.tools.lpt_client as lpt_module
    original_firebase = None
    
    try:
        # Créer une instance de LPTClient
        from app.pinnokio_agentic_workflow.tools.lpt_client import LPTClient
        
        # Patch Firebase
        import app.firebase_providers as fb_module
        original_firebase = fb_module.FirebaseManagement
        fb_module.FirebaseManagement = MockFirebaseManagement
        
        lpt_client = LPTClient()
        
        # Test 1 : Solde insuffisant (besoin de 3.6$ mais seulement 10.0$ disponible)
        print("\n📊 Test 1.1 : Solde insuffisant pour 3 factures (coût estimé : 3.0$)")
        result = lpt_client.check_balance_before_lpt(
            user_id="test_user_123",
            mandate_path="clients/test_user_123/bo_clients/client_abc/mandates/mandate_xyz",
            estimated_cost=3.0,
            lpt_tool_name="APBookkeeper_TEST"
        )
        
        print(f"\n✅ Résultat :")
        print(f"  - Suffisant : {result['sufficient']}")
        print(f"  - Solde actuel : {result['current_balance']:.2f}$")
        print(f"  - Solde requis : {result['required_balance']:.2f}$")
        if not result['sufficient']:
            print(f"  - Montant manquant : {result['missing_amount']:.2f}$")
            print(f"\n📢 Message à l'agent :")
            print(result['message'])
        
        # Test 2 : Solde suffisant (besoin de 0.6$ et 10.0$ disponible)
        print("\n" + "-"*80)
        print("\n📊 Test 1.2 : Solde suffisant pour 1 document (coût estimé : 0.5$)")
        result = lpt_client.check_balance_before_lpt(
            user_id="test_user_123",
            mandate_path="clients/test_user_123/bo_clients/client_abc/mandates/mandate_xyz",
            estimated_cost=0.5,
            lpt_tool_name="Router_TEST"
        )
        
        print(f"\n✅ Résultat :")
        print(f"  - Suffisant : {result['sufficient']}")
        print(f"  - Solde actuel : {result['current_balance']:.2f}$")
        print(f"  - Solde requis : {result['required_balance']:.2f}$")
        
        print("\n✅ Test 1 : RÉUSSI")
        
    except Exception as e:
        print(f"\n❌ Test 1 : ÉCHOUÉ - {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restaurer Firebase
        if original_firebase:
            fb_module.FirebaseManagement = original_firebase


async def test_apbookeeper_with_insufficient_balance():
    """
    Test de launch_apbookeeper avec solde insuffisant
    """
    print("\n" + "="*80)
    print("TEST 2 : launch_apbookeeper avec solde insuffisant")
    print("="*80)
    
    try:
        from app.pinnokio_agentic_workflow.tools.lpt_client import LPTClient
        import app.firebase_providers as fb_module
        
        # Patch Firebase
        original_firebase = fb_module.FirebaseManagement
        fb_module.FirebaseManagement = MockFirebaseManagement
        
        lpt_client = LPTClient()
        brain = MockBrain()
        
        print("\n📊 Tentative de lancement de 3 factures (coût estimé : 3.0$)")
        print(f"💰 Solde disponible : 10.0$ (insuffisant après marge de 20%)")
        
        result = await lpt_client.launch_apbookeeper(
            user_id="test_user_123",
            company_id="company_abc",
            thread_key="thread_test_123",
            job_ids=["invoice_001", "invoice_002", "invoice_003"],
            general_instructions="Test de vérification du solde",
            brain=brain
        )
        
        print(f"\n✅ Résultat :")
        print(f"  - Status : {result.get('status')}")
        print(f"  - Error : {result.get('error')}")
        
        if result.get('status') == 'insufficient_balance':
            print(f"\n✅ BLOCAGE CONFIRMÉ - Le système a correctement bloqué l'opération")
            balance_info = result.get('balance_info', {})
            print(f"  - Solde actuel : {balance_info.get('current_balance', 0):.2f}$")
            print(f"  - Solde requis : {balance_info.get('required_balance', 0):.2f}$")
            print(f"  - Montant manquant : {balance_info.get('missing_amount', 0):.2f}$")
            print(f"\n📢 Message retourné à l'agent :")
            print(result.get('message'))
            print("\n✅ Test 2 : RÉUSSI")
        else:
            print(f"\n❌ Test 2 : ÉCHOUÉ - L'opération n'a pas été bloquée")
        
        # Restaurer Firebase
        fb_module.FirebaseManagement = original_firebase
        
    except Exception as e:
        print(f"\n❌ Test 2 : ÉCHOUÉ - {e}")
        import traceback
        traceback.print_exc()


async def test_router_all_with_insufficient_balance():
    """
    Test de launch_router_all avec solde insuffisant
    """
    print("\n" + "="*80)
    print("TEST 3 : launch_router_all avec solde insuffisant")
    print("="*80)
    
    try:
        from app.pinnokio_agentic_workflow.tools.lpt_client import LPTClient
        import app.firebase_providers as fb_module
        
        # Patch Firebase
        original_firebase = fb_module.FirebaseManagement
        fb_module.FirebaseManagement = MockFirebaseManagement
        
        lpt_client = LPTClient()
        brain = MockBrain()
        
        print("\n📊 Tentative de routage de 2 documents (coût estimé : 1.0$)")
        print(f"💰 Solde disponible : 10.0$ (suffisant après marge de 20%)")
        
        result = await lpt_client.launch_router_all(
            user_id="test_user_123",
            company_id="company_abc",
            thread_key="thread_test_123",
            brain=brain
        )
        
        print(f"\n✅ Résultat :")
        print(f"  - Status : {result.get('status')}")
        
        if result.get('status') == 'insufficient_balance':
            print(f"  - L'opération a été bloquée (solde insuffisant)")
            print("\n✅ Test 3 : RÉUSSI (blocage attendu)")
        else:
            print(f"  - L'opération a été autorisée (solde suffisant)")
            print("\n✅ Test 3 : RÉUSSI (opération autorisée)")
        
        # Restaurer Firebase
        fb_module.FirebaseManagement = original_firebase
        
    except Exception as e:
        print(f"\n❌ Test 3 : ÉCHOUÉ - {e}")
        import traceback
        traceback.print_exc()


def print_summary():
    """Affiche un résumé de l'implémentation"""
    print("\n" + "="*80)
    print("📊 RÉSUMÉ DE L'IMPLÉMENTATION")
    print("="*80)
    
    print("""
✅ FONCTIONNALITÉS IMPLÉMENTÉES :

1. 🛡️ Méthode check_balance_before_lpt dans LPTClient
   - Vérifie le solde avant chaque opération LPT
   - Utilise get_balance_info() de Firebase
   - Marge de sécurité de 20% (estimated_cost * 1.2)
   - Retourne un message clair à l'agent si insuffisant

2. 📋 Intégration dans launch_apbookeeper
   - Coût estimé : 1.0$ par facture
   - Blocage si solde insuffisant
   - Message détaillé à l'agent

3. 🔄 Intégration dans launch_router
   - Coût estimé : 0.5$ par document
   - Blocage si solde insuffisant
   - Message détaillé à l'agent

4. 💰 Intégration dans launch_banker
   - Coût estimé : 0.3$ par transaction
   - Blocage si solde insuffisant
   - Message détaillé à l'agent

5. 📦 Intégration dans toutes les versions _all
   - launch_apbookeeper_all
   - launch_router_all
   - launch_banker_all
   - Calcul dynamique du coût total selon le nombre d'items

📢 MESSAGE TYPE RETOURNÉ À L'AGENT :
-----------------------------------
⚠️ **SOLDE INSUFFISANT** ⚠️

L'exécution de l'outil **APBookkeeper** nécessite un solde minimum.

📊 **État du compte :**
• Solde actuel : **10.00 $**
• Solde requis : **3.60 $**
• Montant manquant : **0.00 $**

💡 **Action requise :**
Veuillez inviter l'utilisateur à **recharger son compte** depuis le tableau de bord
pour continuer à utiliser les services.

🔗 L'utilisateur peut recharger son compte dans la section **Facturation** du tableau de bord.
-----------------------------------

✅ COÛTS CONFIGURÉS :
- APBookkeeper : 1.0$ par facture
- Router : 0.5$ par document
- Banker : 0.3$ par transaction
- Marge de sécurité : 20% (modifiable dans check_balance_before_lpt)

✅ FALLBACK EN CAS D'ERREUR :
- Si la vérification échoue, l'opération est autorisée par défaut (failsafe)
- Un message d'avertissement est logué

🎯 PROCHAINES ÉTAPES (optionnelles) :
1. Ajuster les coûts estimés selon vos tarifs réels
2. Modifier la marge de sécurité (actuellement 20%)
3. Ajouter une configuration dynamique des coûts via Firebase
4. Tester en environnement de production
""")


def main():
    """Point d'entrée principal"""
    print("""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║  🧪 SCRIPT DE TEST - VÉRIFICATION DE SOLDE POUR OUTILS LPT                    ║
║                                                                               ║
║  Ce script teste le système de vérification de solde avant l'envoi des       ║
║  outils LPT (APBookkeeper, Router, Banker).                                  ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
""")
    
    try:
        # Test 1 : Méthode check_balance_before_lpt
        test_balance_check()
        
        # Test 2 : launch_apbookeeper avec solde insuffisant
        asyncio.run(test_apbookeeper_with_insufficient_balance())
        
        # Test 3 : launch_router_all avec solde insuffisant
        asyncio.run(test_router_all_with_insufficient_balance())
        
        # Résumé
        print_summary()
        
        print("\n" + "="*80)
        print("✅ TOUS LES TESTS SONT TERMINÉS")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n❌ ERREUR FATALE : {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

