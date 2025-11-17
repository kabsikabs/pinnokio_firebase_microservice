"""
Test de l'intégration OpenAI Agent via BaseAIAgent
Ce fichier teste l'utilisation d'OpenAI via BaseAIAgent (wrapper utilisé dans l'application)
"""

import json
import asyncio
from app.llm.klk_agents import (
    NEW_OpenAiAgent, 
    BaseAIAgent, 
    ModelProvider, 
    ModelSize
)

# ============================================================================
# CONFIGURATION GLOBALE
# ============================================================================

def setup_base_agent():
    """
    Configure un BaseAIAgent avec OpenAI (comme dans l'application réelle)
    """
    base_agent = BaseAIAgent()
    openai_instance = NEW_OpenAiAgent()
    
    # Enregistrer le provider OpenAI
    base_agent.register_provider(
        provider=ModelProvider.OPENAI,
        instance=openai_instance,
        default_model_size=ModelSize.MEDIUM
    )
    
    # Configurer comme provider par défaut
    base_agent.default_provider = ModelProvider.OPENAI
    base_agent.default_model_size = ModelSize.MEDIUM
    
    return base_agent, openai_instance


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
# DÉFINITION DES OUTILS (FORMAT ANTHROPIC - attendu par BaseAIAgent)
# ============================================================================

TOOLS = [
    {
        "name": "get_weather",
        "description": "Obtenir les informations météo actuelles pour une ville donnée",
        "input_schema": {
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
    },
    {
        "name": "calculate_sum",
        "description": "Calculer la somme de deux nombres",
        "input_schema": {
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
]

# Mapping des outils vers les fonctions
# ⭐ IMPORTANT: BaseAIAgent attend une LISTE de dicts (pas un dict simple)
TOOL_MAPPING = [
    {
        "get_weather": get_weather,
        "calculate_sum": calculate_sum
    }
]


# ============================================================================
# TESTS
# ============================================================================

def test_1_generation_texte_simple():
    """
    Test 1: Génération de texte simple via BaseAIAgent.process_text()
    Utilise GPT-4o-mini
    """
    print("\n" + "="*80)
    print("TEST 1: GÉNÉRATION DE TEXTE VIA BaseAIAgent (GPT-4o-mini)")
    print("="*80)
    
    try:
        # Setup comme dans l'application réelle
        base_agent, openai_instance = setup_base_agent()
        
        # Test avec génération simple
        prompt = "Explique en 2 phrases ce qu'est l'intelligence artificielle."
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Provider: {base_agent.default_provider.value}")
        print(f"🧠 Size: {base_agent.default_model_size.value}")
        
        response = base_agent.process_text(
            content=prompt,
            provider=ModelProvider.OPENAI,
            size=ModelSize.MEDIUM,
            max_tokens=200
        )
        
        # process_text() peut retourner une string ou un dict
        if isinstance(response, str):
            print(f"\n✅ Réponse: {response}")
        else:
            print(f"\n✅ Réponse: {response.get('text_output', response)}")
        
        print(f"\n📊 Utilisation des tokens:")
        print(json.dumps(openai_instance.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_2_streaming_simple():
    """
    Test 2: Streaming de texte via BaseAIAgent.process_text_streaming()
    """
    print("\n" + "="*80)
    print("TEST 2: STREAMING DE TEXTE VIA BaseAIAgent")
    print("="*80)
    
    try:
        base_agent, openai_instance = setup_base_agent()
        
        prompt = "Raconte une très courte histoire sur un robot en 3 phrases."
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Provider: OpenAI - Size: MEDIUM")
        print("\n🔄 Streaming en cours...\n")
        
        full_text = ""
        async for chunk in base_agent.process_text_streaming(
            content=prompt,
            provider=ModelProvider.OPENAI,
            size=ModelSize.MEDIUM,
            max_tokens=200
        ):
            # OpenAI retourne {"content": str, "is_final": bool, "model": str}
            if isinstance(chunk, dict):
                content = chunk.get("content", "")
                if content:  # Afficher seulement si non-vide
                    print(content, end='', flush=True)
                    full_text += content
            elif isinstance(chunk, str):
                print(chunk, end='', flush=True)
                full_text += chunk
        
        print(f"\n\n✅ Streaming terminé. Texte complet reçu: {len(full_text)} caractères")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_3_streaming_tools():
    """
    Test 3: Streaming avec outils via BaseAIAgent.process_text_tool_streaming()
    ⭐ MÉTHODE CLÉ UTILISÉE DANS L'APPLICATION
    """
    print("\n" + "="*80)
    print("TEST 3: STREAMING AVEC OUTILS VIA BaseAIAgent")
    print("⭐ Méthode utilisée dans pinnokio_brain.py")
    print("="*80)
    
    try:
        base_agent, openai_instance = setup_base_agent()
        
        prompt = "Quelle est la météo à Paris en celsius?"
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Provider: OpenAI - Size: MEDIUM")
        print("\n🔄 Streaming en cours...\n")
        
        accumulated_text = ""
        tool_calls_detected = []
        tool_results_detected = []
        
        # ⭐ MÉTHODE UTILISÉE DANS L'APPLICATION RÉELLE
        async for event in base_agent.process_text_tool_streaming(
            content=prompt,
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            provider=ModelProvider.OPENAI,
            size=ModelSize.MEDIUM,
            max_tokens=500
        ):
            event_type = event.get("type")
            
            if event_type == "text":
                text = event.get("content", "")
                print(text, end='', flush=True)
                accumulated_text += text
            
            elif event_type == "tool_use":
                tool_name = event.get("tool_name")
                tool_input = event.get("tool_input")
                tool_calls_detected.append({
                    "name": tool_name,
                    "input": tool_input
                })
                print(f"\n\n🔧 Outil appelé: {tool_name}")
                print(f"   Arguments: {json.dumps(tool_input, indent=2)}")
            
            elif event_type == "tool_result":
                tool_name = event.get("tool_name")
                result = event.get("tool_output")
                tool_results_detected.append({
                    "name": tool_name,
                    "result": result
                })
                print(f"\n✅ Résultat de {tool_name}:")
                print(f"   {json.dumps(result, indent=2)}")
            
            elif event_type == "final":
                print(f"\n\n📋 Événement final reçu")
        
        print(f"\n\n✅ Streaming terminé")
        print(f"   - Texte généré: {len(accumulated_text)} caractères")
        print(f"   - Outils appelés: {len(tool_calls_detected)}")
        print(f"   - Résultats reçus: {len(tool_results_detected)}")
        
        print(f"\n📊 Utilisation des tokens:")
        print(json.dumps(openai_instance.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_4_process_tool_use():
    """
    Test 4: process_tool_use (non-streaming) via BaseAIAgent
    """
    print("\n" + "="*80)
    print("TEST 4: process_tool_use (non-streaming)")
    print("="*80)
    
    try:
        base_agent, openai_instance = setup_base_agent()
        
        # Test utilisation d'outils sans streaming
        print("\n🔧 Test utilisation d'outils via BaseAIAgent (non-streaming):")
        response_tool = base_agent.process_tool_use(
            content="Calcule la somme de 42 et 58",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            provider=ModelProvider.OPENAI,
            size=ModelSize.MEDIUM,
            max_tokens=500
        )
        
        print(f"\nRéponse outil:")
        if 'tool_calls' in response_tool:
            print(f"  - Outils appelés: {len(response_tool['tool_calls'])}")
            if 'tool_results' in response_tool:
                for result in response_tool['tool_results']:
                    print(f"  - Résultat de {result['name']}: {result['content']}")
        
        if 'text_output' in response_tool:
            print(f"  - Texte: {response_tool['text_output']}")
        
        print(f"\n📊 Utilisation totale des tokens:")
        print(json.dumps(openai_instance.get_total_tokens(), indent=2))
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_5_multiple_tools_same_request():
    """
    Test 5: Appels multiples d'outils via BaseAIAgent
    """
    print("\n" + "="*80)
    print("TEST 5: APPELS MULTIPLES D'OUTILS")
    print("="*80)
    
    try:
        base_agent, openai_instance = setup_base_agent()
        
        prompt = "Quelle est la température à Lyon et calcule 15 + 27?"
        print(f"\n📝 Prompt: {prompt}")
        print(f"🧠 Provider: OpenAI - Size: MEDIUM")
        print("\n🔄 Streaming en cours...\n")
        
        accumulated_text = ""
        tools_executed = []
        
        async for event in base_agent.process_text_tool_streaming(
            content=prompt,
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            provider=ModelProvider.OPENAI,
            size=ModelSize.MEDIUM,
            max_tokens=1000
        ):
            event_type = event.get("type")
            
            if event_type == "text":
                text = event.get("content", "")
                print(text, end='', flush=True)
                accumulated_text += text
            
            elif event_type == "tool_use":
                tool_name = event.get("tool_name")
                tools_executed.append(tool_name)
                print(f"\n\n🔧 Outil #{len(tools_executed)}: {tool_name}")
            
            elif event_type == "tool_result":
                tool_name = event.get("tool_name")
                result = event.get("tool_output")
                print(f"✅ Résultat {tool_name}: {result}")
        
        print(f"\n\n✅ Streaming terminé")
        print(f"   - Outils exécutés: {len(tools_executed)}")
        print(f"   - Texte: {len(accumulated_text)} caractères")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_6_message_cleaning():
    """
    Test 6: Vérifier que le nettoyage des messages fonctionne
    ⭐ TESTE LE FIX PRINCIPAL (nettoyage format Anthropic)
    """
    print("\n" + "="*80)
    print("TEST 6: NETTOYAGE DES MESSAGES (FIX PRINCIPAL)")
    print("⭐ Teste que les messages Anthropic sont bien nettoyés")
    print("="*80)
    
    try:
        base_agent, openai_instance = setup_base_agent()
        
        # Simuler un historique avec format Anthropic
        print("\n🧪 Ajout de messages au format Anthropic dans l'historique...")
        openai_instance.chat_history.append({
            "role": "user",
            "content": "Test message"
        })
        openai_instance.chat_history.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Voici ma réponse"},
                {"type": "tool_use", "id": "tool_123", "name": "test_tool", "input": {}}
            ]
        })
        
        print(f"✅ Historique simulé avec {len(openai_instance.chat_history)} messages")
        print(f"   - Message 2 a un format Anthropic (liste avec type)")
        
        # Maintenant faire un appel - le nettoyage devrait empêcher l'erreur 400
        prompt = "Continue la conversation"
        print(f"\n📝 Prompt: {prompt}")
        print("🔄 Test avec nettoyage des messages...\n")
        
        response_received = False
        async for event in base_agent.process_text_tool_streaming(
            content=prompt,
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            provider=ModelProvider.OPENAI,
            size=ModelSize.MEDIUM,
            max_tokens=300
        ):
            event_type = event.get("type")
            
            if event_type == "text":
                response_received = True
                text = event.get("content", "")
                print(text, end='', flush=True)
        
        print(f"\n\n✅ Test terminé")
        if response_received:
            print("✅ SUCCÈS: Aucune erreur 400 - Le nettoyage fonctionne!")
        else:
            print("⚠️  Pas de texte reçu, mais pas d'erreur 400")
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        if "400" in error_msg and "type" in error_msg.lower():
            print(f"\n❌ ÉCHEC: Erreur 400 détectée - Le nettoyage ne fonctionne pas")
            print(f"   Erreur: {error_msg}")
            return False
        else:
            print(f"\n⚠️  Autre erreur: {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_7_vision_pdf():
    """
    Test 7: Analyse d'image/PDF avec BaseAIAgent.process_vision()
    Utilise GPT-4o (Vision) via OpenAI
    """
    print("\n" + "="*80)
    print("TEST 7: ANALYSE D'IMAGE/PDF avec process_vision()")
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
        
        # Initialiser BaseAIAgent
        print("\n🔄 Initialisation de BaseAIAgent avec OpenAI...")
        base_agent, openai_instance = setup_base_agent()
        
        # Test avec process_vision
        print("\n🖼️  Analyse du PDF avec GPT-4o (Vision)...")
        prompt = "Décris le contenu de ce document. Quelles sont les principales informations visibles?"
        print(f"🤖 Modèle: GPT-4o (MEDIUM - vision)")
        
        try:
            response = base_agent.process_vision(
                text=prompt,
                provider=ModelProvider.OPENAI,
                size=ModelSize.MEDIUM,  # GPT-4o pour vision
                local_files=[pdf_path],
                method='batch',
                max_tokens=1000,
                final_resume=True
            )
            
            print(f"\n✅ Réponse du modèle vision:")
            if isinstance(response, str):
                print(response[:500] + "..." if len(response) > 500 else response)
            elif isinstance(response, dict):
                if 'text_output' in response:
                    print(response['text_output'][:500] + "...")
                else:
                    print(json.dumps({k: v for k, v in response.items() if k != 'raw_response'}, 
                                   indent=2, ensure_ascii=False)[:500])
            else:
                print(response)
            
            print(f"\n📊 Utilisation des tokens:")
            print(json.dumps(openai_instance.get_total_tokens(), indent=2))
            
            return True
            
        except ImportError as import_err:
            if "pdf2image" in str(import_err):
                print(f"\n⚠️  Module manquant: {import_err}")
                print("💡 Pour installer: pip install pdf2image")
                print("   (Nécessite aussi poppler: https://github.com/oschwartz10612/poppler-windows/releases/)")
                print("\n✅ Test ignoré (dépendance manquante)")
                return True
            else:
                raise
        
        except Exception as vision_error:
            print(f"\n❌ Erreur lors de l'analyse vision: {vision_error}")
            import traceback
            traceback.print_exc()
            return False
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_8_default_provider():
    """
    Test 8: Vérifier que le provider par défaut est bien utilisé
    ⭐ SIMULE L'UTILISATION DANS PINNOKIO_BRAIN
    """
    print("\n" + "="*80)
    print("TEST 8: PROVIDER PAR DÉFAUT (comme pinnokio_brain)")
    print("⭐ Simule l'initialisation dans pinnokio_brain.py")
    print("="*80)
    
    try:
        # Simuler l'initialisation de pinnokio_brain
        print("\n🧠 Simulation de l'initialisation dans pinnokio_brain...")
        base_agent, openai_instance = setup_base_agent()
        
        print(f"✅ Provider par défaut: {base_agent.default_provider.value}")
        print(f"✅ Size par défaut: {base_agent.default_model_size.value}")
        
        # Test sans spécifier le provider (utilise le défaut)
        print("\n📝 Test sans spécifier le provider (utilise défaut):")
        
        accumulated_text = ""
        async for event in base_agent.process_text_tool_streaming(
            content="Calcule 10 + 20",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            # PAS de provider/size spécifié - utilise les défauts
            max_tokens=300
        ):
            if event.get("type") == "text":
                text = event.get("content", "")
                print(text, end='', flush=True)
                accumulated_text += text
            elif event.get("type") == "tool_use":
                print(f"\n🔧 Outil: {event.get('tool_name')}")
            elif event.get("type") == "tool_result":
                print(f"\n✅ Résultat: {event.get('tool_output')}")
        
        print(f"\n\n✅ Test terminé - Provider par défaut fonctionne!")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

async def run_all_tests():
    """
    Exécute tous les tests
    """
    print("\n" + "🚀"*40)
    print("TESTS OPENAI via BaseAIAgent (comme dans l'application)")
    print("🚀"*40)
    
    tests = [
        ("Génération texte via BaseAIAgent", test_1_generation_texte_simple, False),
        ("Streaming texte via BaseAIAgent", test_2_streaming_simple, True),
        ("⭐ Streaming + outils (méthode clé)", test_3_streaming_tools, True),
        ("process_tool_use (non-streaming)", test_4_process_tool_use, True),
        ("Appels multiples d'outils", test_5_multiple_tools_same_request, True),
        ("⭐ Nettoyage messages (FIX principal)", test_6_message_cleaning, True),
        ("Analyse d'image/PDF avec process_vision (GPT-4o)", test_7_vision_pdf, True),
        ("⭐ Provider par défaut (pinnokio_brain)", test_8_default_provider, True),
    ]
    
    results = {}
    
    for test_name, test_func, is_async in tests:
        try:
            if is_async:
                result = await test_func()
            else:
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


if __name__ == "__main__":
    # Vérifier que la clé API est configurée
    try:
        from app.tools.g_cred import get_secret
        api_key = get_secret('openai_pinnokio')
        if not api_key:
            print("❌ ERREUR: La clé API OpenAI n'est pas configurée!")
            print("💡 Configurez 'openai_pinnokio' dans votre système de secrets")
            exit(1)
    except Exception as e:
        print(f"❌ ERREUR lors de la récupération de la clé API: {e}")
        exit(1)
    
    # Lancer tous les tests
    asyncio.run(run_all_tests())

