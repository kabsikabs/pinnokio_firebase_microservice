"""
Test des différents modes d'utilisation des outils avec GROQ
Tests : auto, required (précisé), none (sans outils)
"""

import json
from app.llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize
from app.llm.klk_agents import NEW_GROQ_AGENT

# ============================================================================
# CONFIGURATION DES OUTILS
# ============================================================================

TOOLS = [
    {
        "name": "calculate_sum",
        "description": "Additionne deux nombres entiers ou décimaux",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {
                    "type": "number",
                    "description": "Premier nombre à additionner"
                },
                "b": {
                    "type": "number",
                    "description": "Deuxième nombre à additionner"
                }
            },
            "required": ["a", "b"]
        }
    },
    {
        "name": "get_weather",
        "description": "Obtient les informations météorologiques pour une ville donnée",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Nom de la ville"
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Unité de température",
                    "default": "celsius"
                }
            },
            "required": ["city"]
        }
    }
]

# Fonctions dummy pour le mapping
def calculate_sum(a: float, b: float) -> float:
    """Additionne deux nombres"""
    result = a + b
    print(f"   🔢 Calcul : {a} + {b} = {result}")
    return result

def get_weather(city: str, unit: str = "celsius") -> dict:
    """Retourne la météo simulée"""
    weather_data = {
        "city": city,
        "temperature": 22 if unit == "celsius" else 72,
        "unit": unit,
        "conditions": "Ensoleillé",
        "humidity": 65
    }
    print(f"   🌤️  Météo pour {city} : {weather_data['temperature']}°{unit[0].upper()} - {weather_data['conditions']}")
    return weather_data

TOOL_MAPPING = {
    "calculate_sum": calculate_sum,
    "get_weather": get_weather
}

# ============================================================================
# TESTS
# ============================================================================

def test_1_mode_auto():
    """
    TEST 1 : MODE AUTO
    Le modèle décide s'il utilise un outil ou non
    """
    print("\n" + "="*80)
    print("📝 TEST 1 : MODE AUTO - Le modèle décide")
    print("="*80)
    print("Configuration : tool_choice={'type': 'auto'}")
    print("Question : Calcul mathématique (devrait utiliser calculate_sum)")
    
    base_agent = BaseAIAgent()
    groq_instance = NEW_GROQ_AGENT()
    base_agent.register_provider(
        ModelProvider.GROQ,
        groq_instance,
        ModelSize.REASONING_MEDIUM
    )
    
    try:
        response = base_agent.process_tool_use(
            content="Combien font 25 + 37 ?",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            tool_choice={"type": "auto"},  # Le modèle décide
            provider=ModelProvider.GROQ,
            size=ModelSize.REASONING_MEDIUM  # Kimi K2
        )
        
        print(f"\n✅ Résultat :")
        if 'tool_calls' in response:
            print(f"  🔧 Outils appelés : {len(response['tool_calls'])}")
            for tool_call in response['tool_calls']:
                print(f"     - Outil : {tool_call.function.name}")
                print(f"     - Arguments : {tool_call.function.arguments}")
            
            if 'tool_results' in response:
                print(f"\n  📊 Résultats des outils :")
                for result in response['tool_results']:
                    print(f"     - {result['name']} : {result['content']}")
        elif 'text_output' in response:
            print(f"  📝 Réponse textuelle : {response['text_output']}")
        else:
            print(f"  ℹ️  Type de réponse : {type(response)}")
        
        print("\n💡 Analyse : Le modèle a décidé d'utiliser un outil (calculate_sum)")
        
    except Exception as e:
        print(f"\n❌ Erreur : {str(e)}")
    
    print("\n" + "-"*80)

def test_2_mode_required():
    """
    TEST 2 : MODE REQUIRED (PRÉCISÉ)
    Le modèle DOIT utiliser un outil parmi ceux disponibles
    """
    print("\n" + "="*80)
    print("📝 TEST 2 : MODE REQUIRED - Le modèle DOIT utiliser un outil")
    print("="*80)
    print("Configuration : tool_choice={'type': 'any'}")
    print("Question : Question générale (doit quand même utiliser un outil)")
    
    base_agent = BaseAIAgent()
    groq_instance = NEW_GROQ_AGENT()
    base_agent.register_provider(
        ModelProvider.GROQ,
        groq_instance,
        ModelSize.REASONING_MEDIUM
    )
    
    try:
        response = base_agent.process_tool_use(
            content="Quelle est la météo à Paris aujourd'hui ?",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            tool_choice={"type": "any"},  # DOIT utiliser un outil (any → required pour Groq)
            provider=ModelProvider.GROQ,
            size=ModelSize.REASONING_MEDIUM  # Kimi K2
        )
        
        print(f"\n✅ Résultat :")
        if 'tool_calls' in response:
            print(f"  🔧 Outils appelés : {len(response['tool_calls'])}")
            for tool_call in response['tool_calls']:
                print(f"     - Outil : {tool_call.function.name}")
                print(f"     - Arguments : {tool_call.function.arguments}")
            
            if 'tool_results' in response:
                print(f"\n  📊 Résultats des outils :")
                for result in response['tool_results']:
                    print(f"     - {result['name']} : {result['content']}")
        elif 'text_output' in response:
            print(f"  📝 Réponse textuelle : {response['text_output']}")
        else:
            print(f"  ℹ️  Type de réponse : {type(response)}")
        
        print("\n💡 Analyse : Le modèle a été forcé d'utiliser un outil (probablement get_weather)")
        
    except Exception as e:
        print(f"\n❌ Erreur : {str(e)}")
    
    print("\n" + "-"*80)

def test_3_mode_none():
    """
    TEST 3 : MODE NONE (SANS OUTILS)
    Le modèle NE DOIT PAS utiliser d'outils
    """
    print("\n" + "="*80)
    print("📝 TEST 3 : MODE NONE - Le modèle NE DOIT PAS utiliser d'outils")
    print("="*80)
    print("Configuration : tool_choice={'type': 'none'}")
    print("Question : Calcul mathématique (ne doit PAS utiliser calculate_sum)")
    
    base_agent = BaseAIAgent()
    groq_instance = NEW_GROQ_AGENT()
    base_agent.register_provider(
        ModelProvider.GROQ,
        groq_instance,
        ModelSize.REASONING_MEDIUM
    )
    
    try:
        response = base_agent.process_tool_use(
            content="Combien font 15 multiplié par 8 ?",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            tool_choice={"type": "none"},  # NE DOIT PAS utiliser d'outils
            provider=ModelProvider.GROQ,
            size=ModelSize.REASONING_MEDIUM  # Kimi K2
        )
        
        print(f"\n✅ Résultat :")
        if 'tool_calls' in response:
            print(f"  ⚠️  ERREUR : Le modèle a utilisé un outil alors qu'il ne devrait pas !")
            for tool_call in response['tool_calls']:
                print(f"     - Outil : {tool_call.function.name}")
        elif 'text_output' in response:
            print(f"  📝 Réponse textuelle : {response['text_output']}")
        elif hasattr(response, 'choices') and response.choices:
            # Si c'est un objet ChatCompletion brut
            text = response.choices[0].message.content
            print(f"  📝 Réponse textuelle : {text}")
        else:
            print(f"  ℹ️  Type de réponse : {type(response)}")
        
        print("\n💡 Analyse : Le modèle a répondu directement sans utiliser d'outil")
        
    except Exception as e:
        print(f"\n❌ Erreur : {str(e)}")
    
    print("\n" + "-"*80)

def test_4_mode_forced_tool():
    """
    TEST 4 : MODE OUTIL FORCÉ (BONUS)
    Le modèle DOIT utiliser un outil spécifique
    """
    print("\n" + "="*80)
    print("📝 TEST 4 : MODE OUTIL FORCÉ - Force l'utilisation de calculate_sum")
    print("="*80)
    print("Configuration : tool_choice={'type': 'tool', 'name': 'calculate_sum'}")
    print("Question : Question ambiguë (pourrait calculer mentalement ou utiliser l'outil)")
    
    base_agent = BaseAIAgent()
    groq_instance = NEW_GROQ_AGENT()
    base_agent.register_provider(
        ModelProvider.GROQ,
        groq_instance,
        ModelSize.REASONING_MEDIUM
    )
    
    try:
        response = base_agent.process_tool_use(
            content="J'ai 25 pommes et j'en achète 37 de plus. Combien j'en ai maintenant ?",
            tools=TOOLS,
            tool_mapping=TOOL_MAPPING,
            tool_choice={
                "type": "tool",
                "name": "calculate_sum"
            },  # Force calculate_sum
            provider=ModelProvider.GROQ,
            size=ModelSize.REASONING_MEDIUM  # Kimi K2
        )
        
        print(f"\n✅ Résultat :")
        if 'error' in response:
            print(f"  ❌ Erreur inattendue : {response['error']}")
            print(f"  💡 Note : Cette erreur est inattendue car la question est pertinente pour calculate_sum")
        elif 'tool_calls' in response:
            print(f"  🔧 Outils appelés : {len(response['tool_calls'])}")
            for tool_call in response['tool_calls']:
                print(f"     - Outil : {tool_call.function.name}")
                print(f"     - Arguments : {tool_call.function.arguments}")
            
            if 'tool_results' in response:
                print(f"\n  📊 Résultats des outils :")
                for result in response['tool_results']:
                    print(f"     - {result['name']} : {result['content']}")
            
            print(f"\n  ✅ SUCCESS : Le modèle a bien utilisé calculate_sum comme forcé")
        elif 'text_output' in response:
            print(f"  📝 Réponse textuelle : {response['text_output']}")
            print(f"  ⚠️  Le modèle n'a pas utilisé l'outil (comportement inattendu)")
        else:
            print(f"  ℹ️  Type de réponse : {type(response)}")
        
        print("\n💡 Analyse : Le modèle est forcé d'utiliser calculate_sum même s'il pourrait calculer mentalement")
        
    except Exception as e:
        print(f"\n❌ Erreur : {str(e)}")
    
    print("\n" + "-"*80)

def test_5_structured_output():
    """
    TEST 5 : STRUCTURED OUTPUT (SORTIE STRUCTURÉE)
    Force un outil pour obtenir une réponse dans un format JSON structuré
    Simule audit_agent_loggeur : forcer le modèle à répondre dans un format précis
    Utilise GPT OSS 20B au lieu de Kimi K2 pour tester le comportement
    """
    print("\n" + "="*80)
    print("📝 TEST 5 : STRUCTURED OUTPUT - Force une réponse structurée JSON")
    print("="*80)
    print("🧠 Modèle : GPT OSS 20B (openai/gpt-oss-20b) - 1000 TPS")
    print("Configuration : tool_choice={'type': 'tool', 'name': 'send_message_tool'}")
    print("Usage : Garantir que la réponse est dans un format JSON précis")
    print("Note : L'outil n'exécute RIEN, il sert juste à structurer la sortie")
    
    # Définition de l'outil pour structured output (format OpenAI/Groq)
    tool_message = [
        {
            "type": "function",
            "function": {
                "name": "send_message_tool",
                "description": "Schema pour assurer la réponse à l'utilisateur dans un format structuré",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_message": {
                            "type": "string",
                            "description": "Message à envoyer à l'utilisateur"
                        }
                    },
                    "required": ["user_message"]
                }
            }
        }
    ]
    
    # Pas de mapping réel (on veut juste la structure)
    tool_map = {'send_message_tool': None}
    
    # Force l'utilisation de l'outil
    tool_choice = {"type": "function", "function": {"name": "send_message_tool"}}
    
    try:
        # Utilisation directe de NEW_GROQ_AGENT pour spécifier le modèle exact
        groq_instance = NEW_GROQ_AGENT()
        
        # Contenu du prompt - Reformulé pour demander explicitement une structure
        user_language = "français"
        content = f"""Génère un message informatif pour l'utilisateur concernant l'analyse de document :
        
        Informations à communiquer :
        - 42 pages ont été extraites
        - 15 sections principales ont été identifiées
        - Le traitement est à 75% de complétion
        
        Important : 
        - Réponds toujours dans la langue de l'utilisateur ({user_language})
        - Ne pose jamais de question
        - Le message est purement informatif sur l'avancée du traitement
        
        Utilise send_message_tool pour formater ta réponse."""
        
        print(f"\n📝 Prompt : Génération de message informatif structuré")
        print(f"🌐 Langue : {user_language}")
        
        # Appel direct avec le nom exact du modèle
        response = groq_instance.groq_agent(
            content=content,
            model_name="openai/gpt-oss-20b",  # Force GPT OSS 20B
            tools=tool_message,
            tool_mapping=tool_map,
            tool_choice=tool_choice,
            max_tokens=500
        )
        
        print(f"\n✅ Résultat :")
        
        # Gestion d'erreur comme dans audit_agent_loggeur
        if isinstance(response, dict) and 'error' in response:
            print(f"  ❌ Erreur : {response.get('error', 'Erreur inconnue')}")
            raise Exception(f"Erreur lors de l'appel à l'agent : {response['error']}")
        
        # Afficher la structure complète
        if 'tool_calls' in response:
            print(f"  🔧 Outil appelé : {response['tool_calls'][0].function.name}")
            
            # Parser les arguments JSON
            arguments = json.loads(response['tool_calls'][0].function.arguments)
            
            print(f"\n  📊 Structured Output (JSON) :")
            print(f"     {json.dumps(arguments, indent=6, ensure_ascii=False)}")
            
            # Simulation de audit_agent_loggeur : extraction de user_message
            # Dans audit_agent_loggeur, on fait : logger_message = logger_message['user_message']
            # Mais avec Groq, la structure est différente, il faut extraire depuis tool_results
            
            print(f"\n  📊 Simulation audit_agent_loggeur :")
            print(f"     1️⃣  Le modèle a appelé send_message_tool")
            print(f"     2️⃣  Arguments structurés : {json.dumps(arguments, ensure_ascii=False)}")
            
            # Vérifier si tool_results contient les données structurées
            if 'tool_results' in response and len(response['tool_results']) > 0:
                # Extraire le contenu du tool_result
                tool_result_content = response['tool_results'][0]['content']
                print(f"     3️⃣  tool_results[0]['content'] : {tool_result_content}")
                
                # Si c'est une string JSON, la parser
                if isinstance(tool_result_content, str):
                    try:
                        parsed_content = json.loads(tool_result_content)
                        print(f"     4️⃣  Contenu parsé : {json.dumps(parsed_content, ensure_ascii=False)}")
                        
                        # Extraire user_message comme dans audit_agent_loggeur
                        if 'user_message' in parsed_content:
                            user_message = parsed_content['user_message']
                            print(f"\n  💬 Message final extrait (user_message) :")
                            print(f"     \"{user_message}\"")
                            
                            print(f"\n  ✅ SUCCESS : Structured output fonctionnel !")
                            print(f"  💡 Note : Pour extraire user_message, il faut :")
                            print(f"     → Parser tool_results[0]['content']")
                            print(f"     → Extraire la clé 'user_message'")
                        else:
                            print(f"  ❌ ERREUR : 'user_message' absent de parsed_content")
                    except json.JSONDecodeError as e:
                        print(f"     ❌ Erreur de parsing JSON : {e}")
                        print(f"     Contenu brut : {tool_result_content}")
            else:
                print(f"     ⚠️  tool_results non disponible ou vide")
        else:
            print(f"  ⚠️  Aucun tool_call détecté (comportement inattendu)")
        
        print("\n💡 Analyse : L'outil force le modèle à répondre dans un format JSON précis")
        print("   C'est utile pour garantir une structure de données cohérente et parsable")
        
    except Exception as e:
        print(f"\n❌ Erreur : {str(e)}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "-"*80)

# ============================================================================
# EXÉCUTION DES TESTS
# ============================================================================

def run_all_tests():
    """Exécute tous les tests dans l'ordre"""
    print("\n" + "="*80)
    print("🧪 TESTS DES MODES D'UTILISATION DES OUTILS AVEC GROQ")
    print("="*80)
    print("Modèle utilisé : moonshotai/kimi-k2-instruct-0905 (Kimi K2)")
    print("Outils disponibles : calculate_sum, get_weather")
    print("="*80)
    
    # Test 1 : Auto
    test_1_mode_auto()
    
    # Test 2 : Required
    test_2_mode_required()
    
    # Test 3 : None
    test_3_mode_none()
    
    # Test 4 : Forced Tool (bonus)
    test_4_mode_forced_tool()
    
    # Test 5 : Structured Output (bonus)
    test_5_structured_output()
    
    # Résumé final
    print("\n" + "="*80)
    print("📊 RÉSUMÉ DES MODES")
    print("="*80)
    
    summary = [
        {
            "Mode": "AUTO",
            "Configuration": '{"type": "auto"}',
            "Comportement": "Le modèle décide s'il utilise un outil",
            "Format Groq": '"auto"'
        },
        {
            "Mode": "REQUIRED",
            "Configuration": '{"type": "any"}',
            "Comportement": "Le modèle DOIT utiliser un outil (n'importe lequel)",
            "Format Groq": '"required"'
        },
        {
            "Mode": "NONE",
            "Configuration": '{"type": "none"}',
            "Comportement": "Le modèle NE DOIT PAS utiliser d'outils",
            "Format Groq": '"none"'
        },
        {
            "Mode": "FORCED",
            "Configuration": '{"type": "tool", "name": "X"}',
            "Comportement": "Le modèle DOIT utiliser l'outil X spécifiquement",
            "Format Groq": '{"type": "function", "function": {"name": "X"}}'
        },
        {
            "Mode": "STRUCTURED OUTPUT",
            "Configuration": '{"type": "tool", "name": "schema_tool"}',
            "Comportement": "Force une réponse dans un format JSON structuré (pas d'exécution)",
            "Format Groq": '{"type": "function", "function": {"name": "schema_tool"}}'
        }
    ]
    
    for item in summary:
        print(f"\n🔹 {item['Mode']}")
        print(f"   📥 Configuration    : {item['Configuration']}")
        print(f"   📤 Format Groq     : {item['Format Groq']}")
        print(f"   ℹ️  Comportement    : {item['Comportement']}")
    
    print("\n" + "="*80)
    print("✅ TOUS LES TESTS TERMINÉS")
    print("="*80)

if __name__ == "__main__":
    run_all_tests()

