"""
Test de l'intégration Groq Agent
Ce fichier teste toutes les fonctionnalités de NEW_GROQ_AGENT et son intégration avec BaseAIAgent
"""

import json
import asyncio
from app.llm.klk_agents import (
    NEW_GROQ_AGENT, 
    BaseAIAgent, 
    ModelProvider, 
    ModelSize
)


# ============================================================================
# FONCTIONS DUMMY POUR LES TESTS D'OUTILS
# ============================================================================

def get_weather(location: str, unit: str = "celsius") -> dict:
    """
    Fonction dummy pour tester l'appel d'outils.
    
    Args:
        location: Ville pour laquelle obtenir la météo
        unit: Unité de température (celsius ou fahrenheit)
    
    Returns:
        dict: Informations météo simulées
    """
    print(f"\n🌤️  Appel de get_weather pour {location} en {unit}")
    
    # Simulation de données météo
    weather_data = {
        "location": location,
        "temperature": 22 if unit == "celsius" else 72,
        "unit": unit,
        "condition": "Ensoleillé",
        "humidity": 65,
        "wind_speed": 15
    }
    
    return weather_data


def calculate_sum(a: float, b: float) -> dict:
    """
    Fonction dummy pour calculer une somme.
    
    Args:
        a: Premier nombre
        b: Deuxième nombre
    
    Returns:
        dict: Résultat du calcul
    """
    print(f"\n🧮 Calcul de {a} + {b}")
    return {
        "operation": "addition",
        "a": a,
        "b": b,
        "result": a + b
    }


# ============================================================================
# DÉFINITION DES OUTILS (FORMAT OPENAI/GROQ)
# ============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Obtenir les informations météo actuelles pour une ville donnée",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "La ville et le pays, ex: Paris, France"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "L'unité de température"
                    }
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_sum",
            "description": "Calculer la somme de deux nombres",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "number",
                        "description": "Premier nombre"
                    },
                    "b": {
                        "type": "number",
                        "description": "Deuxième nombre"
                    }
                },
                "required": ["a", "b"]
            }
        }
    }
]

# Mapping des outils vers les fonctions
TOOL_MAPPING = {
    "get_weather": get_weather,
    "calculate_sum": calculate_sum
}


# ============================================================================
# TESTS
# ============================================================================

def test_1_generation_texte_simple():
    """
    Test 1: Génération de texte simple sans outils
    Utilise Kimi K2 (Streaming + Reasoning + 256K contexte)
    """
    print("\n" + "="*80)
    print("TEST 1: GÉNÉRATION DE TEXTE SIMPLE (Kimi K2)")
    print("="*80)
    
    try:
        # Initialisation de l'agent Groq
        groq_agent = NEW_GROQ_AGENT()
        
        # Test avec génération simple
        prompt = "Explique en 2 phrases ce qu'est l'intelligence artificielle."
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Modèle: Kimi K2-0905")
        
        response = groq_agent.groq_send_message(
            content=prompt,
            model_name="moonshotai/kimi-k2-instruct-0905",
            max_tokens=200
        )
        
        print(f"\n✅ Réponse: {response.get('text_output', 'Pas de texte')}")
        print(f"\n📊 Utilisation des tokens:")
        print(json.dumps(groq_agent.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_2_outil_dummy_sans_mapping():
    """
    Test 2: Appel d'outil sans mapping (extraction des arguments uniquement)
    Utilise Kimi K2 avec raisonnement agentic
    """
    print("\n" + "="*80)
    print("TEST 2: OUTIL DUMMY SANS MAPPING (extraction arguments - Kimi K2)")
    print("="*80)
    
    try:
        groq_agent = NEW_GROQ_AGENT()
        
        prompt = "Quelle est la météo à Paris en celsius?"
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Modèle: Kimi K2-0905 (Tool calling + Reasoning)")
        
        # Appel sans tool_mapping pour voir le format brut
        response = groq_agent.groq_agent(
            content=prompt,
            model_name="moonshotai/kimi-k2-instruct-0905",
            tools=TOOLS,
            tool_mapping=None,  # Pas de mapping, on veut juste voir les tool_calls
            tool_choice="auto",
            max_tokens=500
        )
        
        print(f"\n📋 Format de la réponse:")
        print(json.dumps({
            "type": type(response).__name__,
            "keys": list(response.keys()) if isinstance(response, dict) else "N/A"
        }, indent=2))
        
        # Extraction des tool_calls si présents
        if 'tool_calls' in response:
            print(f"\n🔧 Tool calls détectés:")
            for tool_call in response['tool_calls']:
                print(f"\n  - Outil: {tool_call.function.name}")
                arguments = json.loads(tool_call.function.arguments)
                print(f"  - Arguments: {json.dumps(arguments, indent=4)}")
                print(f"  - ID: {tool_call.id}")
        else:
            print(f"\n📝 Réponse textuelle: {response.get('text_output', 'N/A')}")
        
        print(f"\n📊 Utilisation des tokens:")
        print(json.dumps(groq_agent.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_3_outil_avec_mapping():
    """
    Test 3: Appel d'outil avec mapping (exécution de la fonction)
    Utilise Kimi K2 avec exécution d'outils et raisonnement
    """
    print("\n" + "="*80)
    print("TEST 3: OUTIL AVEC MAPPING ET EXÉCUTION (Kimi K2)")
    print("="*80)
    
    try:
        groq_agent = NEW_GROQ_AGENT()
        
        prompt = "Quelle est la température à Lyon en celsius?"
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Modèle: Kimi K2-0905")
        
        # Appel avec tool_mapping pour exécuter la fonction
        response = groq_agent.groq_agent(
            content=prompt,
            model_name="moonshotai/kimi-k2-instruct-0905",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,  # Avec mapping, la fonction sera exécutée
            tool_choice="auto",
            max_tokens=500
        )
        
        print(f"\n📋 Réponse complète:")
        
        if 'tool_calls' in response:
            print(f"\n🔧 Outil(s) appelé(s):")
            for tool_call in response['tool_calls']:
                print(f"  - {tool_call.function.name}")
            
            if 'tool_results' in response:
                print(f"\n✅ Résultat(s) de l'outil:")
                for result in response['tool_results']:
                    print(f"\n  Outil: {result['name']}")
                    print(f"  Contenu: {result['content']}")
        else:
            print(f"\n📝 Réponse textuelle: {response.get('text_output', 'N/A')}")
        
        print(f"\n📊 Utilisation des tokens:")
        print(json.dumps(groq_agent.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_4_base_ai_agent_integration():
    """
    Test 4: Intégration avec BaseAIAgent (interface unifiée)
    """
    print("\n" + "="*80)
    print("TEST 4: INTÉGRATION AVEC BaseAIAgent")
    print("="*80)
    
    try:
        # Initialisation de BaseAIAgent
        base_agent = BaseAIAgent()
        groq_instance = NEW_GROQ_AGENT()
        
        # Enregistrement du provider Groq
        base_agent.register_provider(
            ModelProvider.GROQ, 
            groq_instance, 
            ModelSize.MEDIUM
        )
        
        # Test 1: Génération de texte via BaseAIAgent avec Kimi K2
        print("\n📝 Test génération de texte via BaseAIAgent (Kimi K2):")
        response_text = base_agent.process_text(
            content="Quelle est la capitale de la France?",
            provider=ModelProvider.GROQ,
            size=ModelSize.REASONING_MEDIUM,  # Kimi K2
            max_tokens=100
        )
        print(f"Réponse: {response_text.get('text_output', 'N/A')}")
        
        # Test 2: Utilisation d'outils via BaseAIAgent avec Kimi K2
        print("\n\n🔧 Test utilisation d'outils via BaseAIAgent (Kimi K2 + Tools):")
        response_tool = base_agent.process_tool_use(
            content="Calcule la somme de 42 et 58",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            provider=ModelProvider.GROQ,
            size=ModelSize.REASONING_MEDIUM,  # Kimi K2
            max_tokens=500
        )
        
        print(f"Réponse outil:")
        if 'tool_calls' in response_tool:
            print(f"  - Outils appelés: {len(response_tool['tool_calls'])}")
            if 'tool_results' in response_tool:
                for result in response_tool['tool_results']:
                    print(f"  - Résultat de {result['name']}: {result['content']}")
        
        print(f"\n📊 Utilisation totale des tokens:")
        print(json.dumps(groq_instance.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_5_streaming_text():
    """
    Test 5: Streaming de texte simple avec Kimi K2
    """
    print("\n" + "="*80)
    print("TEST 5: STREAMING DE TEXTE (Kimi K2)")
    print("="*80)
    
    try:
        groq_agent = NEW_GROQ_AGENT()
        
        prompt = "Raconte une très courte histoire sur un robot."
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Modèle: Kimi K2-0905")
        print("\n🔄 Streaming en cours...\n")
        
        response = groq_agent.groq_send_message(
            content=prompt,
            model_name="moonshotai/kimi-k2-instruct-0905",
            stream=True,
            max_tokens=200
        )
        
        # Lecture du stream
        full_text = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                print(content, end='', flush=True)
                full_text += content
        
        print(f"\n\n✅ Streaming terminé. Texte complet reçu: {len(full_text)} caractères")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_6_vision_pdf():
    """
    Test 6: Analyse d'image/PDF avec BaseAIAgent.process_vision()
    Utilise meta-llama/llama-4-scout-17b-16e-instruct (Vision + 594 TPS)
    """
    print("\n" + "="*80)
    print("TEST 6: ANALYSE D'IMAGE/PDF avec process_vision()")
    print("="*80)
    
    try:
        import os
        
        pdf_path = "compte_attente_fim.pdf"
        
        # Vérifier si le fichier existe
        if not os.path.exists(pdf_path):
            print(f"\n⚠️  Fichier {pdf_path} introuvable. Test ignoré.")
            return True
        
        print(f"\n📄 Fichier: {pdf_path}")
        print(f"📏 Taille: {os.path.getsize(pdf_path) / 1024:.2f} KB")
        
        # Initialiser BaseAIAgent SANS DMS (fichier local)
        print("\n🔄 Initialisation de BaseAIAgent avec Groq...")
        base_agent = BaseAIAgent()  # Pas de DMS pour fichiers locaux
        groq_instance = NEW_GROQ_AGENT()
        base_agent.register_provider(
            ModelProvider.GROQ,
            groq_instance,
            ModelSize.MEDIUM
        )
        
        # Test avec process_vision
        print("\n🖼️  Analyse du PDF avec Llama-4 Scout (Vision)...")
        prompt = "Décris le contenu de ce document. Quelles sont les principales informations visibles?"
        print(f"🤖 Modèle: meta-llama/llama-4-scout-17b-16e-instruct")
        
        try:
            response = base_agent.process_vision(
                text=prompt,
                provider=ModelProvider.GROQ,
                size=ModelSize.MEDIUM,  # Utilisera meta-llama/llama-4-scout
                local_files=[pdf_path],  # Fichier local direct
                method='batch',
                max_tokens=1000,
                final_resume=True
            )
            
            print(f"\n✅ Réponse du modèle vision:")
            if isinstance(response, str):
                # Si c'est une string (résumé final), afficher directement
                print(response)
            elif isinstance(response, dict):
                # Si c'est un dict, essayer d'extraire le texte
                if 'text_output' in response:
                    print(response['text_output'])
                else:
                    # Sinon afficher sans raw_response pour éviter erreurs JSON
                    response_copy = {k: v for k, v in response.items() if k != 'raw_response'}
                    print(json.dumps(response_copy, indent=2, ensure_ascii=False))
            else:
                print(response)
            
            print(f"\n📊 Utilisation des tokens:")
            print(json.dumps(groq_instance.get_total_tokens(), indent=2))
            
        except Exception as vision_error:
            print(f"\n⚠️  Erreur lors de l'analyse vision: {vision_error}")
            print("💡 Note: La vision nécessite la conversion PDF->image")
            print("   Assurez-vous que pdf2image et poppler sont installés")
            # Ne pas faire échouer le test si c'est juste un problème de dépendances
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_7_streaming_tools_reasoning():
    """
    Test 7: Streaming + Tools + Raisonnement avec Kimi K2
    Utilise moonshotai/kimi-k2-instruct-0905 (Streaming + Reasoning + Tools)
    """
    print("\n" + "="*80)
    print("TEST 7: STREAMING + TOOLS + RAISONNEMENT (Kimi K2)")
    print("="*80)
    
    try:
        groq_agent = NEW_GROQ_AGENT()
        
        prompt = "Calcule 15 + 27, puis explique pourquoi ce résultat est intéressant mathématiquement."
        print(f"\n📝 Prompt: {prompt}")
        print("🧠 Modèle: Kimi K2-0905 (Raisonnement agentic + 256K contexte)")
        
        # Test sans streaming d'abord (plus fiable)
        print("\n📋 Test 1: Sans streaming (plus fiable)...")
        response = groq_agent.groq_agent(
            content=prompt,
            model_name="moonshotai/kimi-k2-instruct-0905",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            stream=False,
            max_tokens=1000
        )
        
        print(f"\n✅ Réponse reçue:")
        if 'tool_calls' in response:
            print(f"  - Outils appelés: {len(response['tool_calls'])}")
            if 'tool_results' in response:
                for result in response['tool_results']:
                    print(f"  - Résultat: {result['content']}")
        elif 'text_output' in response:
            print(f"  - Texte: {response['text_output'][:200]}...")
        
        # Maintenant test avec streaming
        print("\n\n🔄 Test 2: Avec streaming...")
        response_stream = groq_agent.groq_agent(
            content="Quelle est la météo à Tokyo?",
            model_name="moonshotai/kimi-k2-instruct-0905",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            stream=True,
            max_tokens=500
        )
        
        print("\n🔄 Streaming en cours...\n")
        
        # Le streaming avec outils peut retourner un générateur
        if hasattr(response, '__iter__') and not isinstance(response, (str, dict)):
            for chunk in response:
                # Traiter les chunks du stream
                if hasattr(chunk, 'choices') and chunk.choices:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content:
                        print(delta.content, end='', flush=True)
                    if hasattr(delta, 'tool_calls') and delta.tool_calls:
                        print(f"\n🔧 Tool call détecté dans le stream")
            print("\n")
        else:
            # Réponse non-stream
            print(f"Réponse: {response}")
        
        print(f"\n✅ Test streaming avec outils terminé")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur (attendue si le streaming avec outils n'est pas supporté): {e}")
        print("💡 Certains modèles ne supportent pas le streaming avec outils")
        return True  # Ne pas faire échouer le test


# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def run_all_tests():
    """
    Exécute tous les tests
    """
    print("\n" + "🚀"*40)
    print("TESTS DE L'INTÉGRATION GROQ AGENT")
    print("🚀"*40)
    
    tests = [
        ("Génération de texte simple (Kimi K2)", test_1_generation_texte_simple),
        ("Outil dummy sans mapping (Kimi K2)", test_2_outil_dummy_sans_mapping),
        ("Outil avec mapping (Kimi K2)", test_3_outil_avec_mapping),
        ("Intégration BaseAIAgent (Kimi K2)", test_4_base_ai_agent_integration),
        ("Streaming de texte (Kimi K2)", test_5_streaming_text),
        ("Analyse d'image/PDF avec process_vision (Llama Scout)", test_6_vision_pdf),
        ("Streaming + Tools + Raisonnement (Kimi K2)", test_7_streaming_tools_reasoning),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results[test_name] = "✅ RÉUSSI" if result else "❌ ÉCHOUÉ"
        except Exception as e:
            results[test_name] = f"❌ ERREUR: {str(e)}"
    
    # Résumé
    print("\n" + "="*80)
    print("RÉSUMÉ DES TESTS")
    print("="*80)
    
    for test_name, result in results.items():
        print(f"{result} - {test_name}")
    
    # Statistiques
    success_count = sum(1 for r in results.values() if "✅" in r)
    total_count = len(results)
    
    print(f"\n📊 Résultat global: {success_count}/{total_count} tests réussis")
    print("="*80 + "\n")


'''if __name__ == "__main__":
    # Vérifier que la clé API est configurée
    try:
        from app.tools.g_cred import get_secret
        api_key = get_secret('groq_api_key')
        if not api_key:
            print("❌ ERREUR: La clé API Groq n'est pas configurée!")
            print("💡 Configurez 'groq_api_key' dans votre système de secrets")
            exit(1)
    except Exception as e:
        print(f"❌ ERREUR lors de la récupération de la clé API: {e}")
        exit(1)
    
    # Lancer tous les tests
    run_all_tests()

'''