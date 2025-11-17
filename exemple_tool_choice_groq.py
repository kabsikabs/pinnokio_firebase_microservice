"""
Exemple d'utilisation de tool_choice avec GROQ
Démontre comment forcer l'utilisation d'un outil spécifique
"""

import asyncio
import json
from app.llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize
from app.llm.klk_agents import NEW_GROQ_AGENT

# Définir les outils de test
TOOLS = [
    {
        "name": "calculate_sum",
        "description": "Additionne deux nombres",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "Premier nombre"},
                "b": {"type": "number", "description": "Deuxième nombre"}
            },
            "required": ["a", "b"]
        }
    },
    {
        "name": "get_weather",
        "description": "Obtient la météo pour une ville",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Nom de la ville"}
            },
            "required": ["city"]
        }
    }
]

# Mapping des fonctions
def calculate_sum(a: float, b: float) -> float:
    """Additionne deux nombres"""
    return a + b

def get_weather(city: str) -> dict:
    """Retourne la météo (simulée)"""
    return {
        "city": city,
        "temperature": 22,
        "conditions": "Ensoleillé"
    }

TOOL_MAPPING = {
    "calculate_sum": calculate_sum,
    "get_weather": get_weather
}

async def test_tool_choice_options():
    """Teste toutes les options de tool_choice"""
    
    print("="*80)
    print("🧪 TEST DES OPTIONS DE TOOL_CHOICE AVEC GROQ")
    print("="*80)
    
    # Initialiser BaseAIAgent
    base_agent = BaseAIAgent()
    groq_instance = NEW_GROQ_AGENT()
    base_agent.register_provider(
        ModelProvider.GROQ,
        groq_instance,
        ModelSize.REASONING_MEDIUM
    )
    
    # =========================================================================
    # TEST 1 : tool_choice="auto" (le modèle décide)
    # =========================================================================
    print("\n" + "="*80)
    print("📝 TEST 1 : tool_choice='auto' (le modèle décide)")
    print("="*80)
    print("Format envoyé à Groq : 'auto'")
    
    response1 = await base_agent.process_tool_use(
        content="Quel est le résultat de 15 + 27 ?",
        tools=TOOLS,
        tool_mapping=TOOL_MAPPING,
        tool_choice={"type": "auto"},  # Format Anthropic
        provider=ModelProvider.GROQ,
        model_name="moonshotai/kimi-k2-instruct-0905",
        return_full_object=False
    )
    
    print(f"\n✅ Résultat : {json.dumps(response1, indent=2, ensure_ascii=False)}")
    
    # =========================================================================
    # TEST 2 : tool_choice="required" (DOIT utiliser un outil)
    # =========================================================================
    print("\n" + "="*80)
    print("📝 TEST 2 : tool_choice='required' (DOIT utiliser un outil)")
    print("="*80)
    print("Format envoyé à Groq : 'required'")
    
    response2 = await base_agent.process_tool_use(
        content="Quelle est la météo à Paris ?",
        tools=TOOLS,
        tool_mapping=TOOL_MAPPING,
        tool_choice={"type": "any"},  # Format Anthropic → "required" pour Groq
        provider=ModelProvider.GROQ,
        model_name="moonshotai/kimi-k2-instruct-0905",
        return_full_object=False
    )
    
    print(f"\n✅ Résultat : {json.dumps(response2, indent=2, ensure_ascii=False)}")
    
    # =========================================================================
    # TEST 3 : tool_choice="none" (NE DOIT PAS utiliser d'outils)
    # =========================================================================
    print("\n" + "="*80)
    print("📝 TEST 3 : tool_choice='none' (NE DOIT PAS utiliser d'outils)")
    print("="*80)
    print("Format envoyé à Groq : 'none'")
    
    response3 = await base_agent.process_tool_use(
        content="Quel est le résultat de 10 + 5 ?",
        tools=TOOLS,
        tool_mapping=TOOL_MAPPING,
        tool_choice={"type": "none"},  # Format Anthropic
        provider=ModelProvider.GROQ,
        model_name="moonshotai/kimi-k2-instruct-0905",
        return_full_object=False
    )
    
    print(f"\n✅ Résultat : {json.dumps(response3, indent=2, ensure_ascii=False)}")
    
    # =========================================================================
    # TEST 4 : OUTIL FORCÉ - calculate_sum (L'OPTION PRINCIPALE !)
    # =========================================================================
    print("\n" + "="*80)
    print("📝 TEST 4 : OUTIL FORCÉ - calculate_sum")
    print("="*80)
    print("Format envoyé à Groq :")
    print(json.dumps({
        "type": "function",
        "function": {"name": "calculate_sum"}
    }, indent=2))
    
    response4 = await base_agent.process_tool_use(
        content="J'ai besoin d'aide avec des calculs.",
        tools=TOOLS,
        tool_mapping=TOOL_MAPPING,
        tool_choice={
            "type": "tool",       # Type = "tool" pour forcer un outil
            "name": "calculate_sum"  # Nom de l'outil à forcer
        },
        provider=ModelProvider.GROQ,
        model_name="moonshotai/kimi-k2-instruct-0905",
        return_full_object=False
    )
    
    print(f"\n✅ Résultat : {json.dumps(response4, indent=2, ensure_ascii=False)}")
    print("\n⚠️ Note : Le modèle DOIT utiliser 'calculate_sum', même si ce n'est pas pertinent !")
    
    # =========================================================================
    # TEST 5 : OUTIL FORCÉ - get_weather
    # =========================================================================
    print("\n" + "="*80)
    print("📝 TEST 5 : OUTIL FORCÉ - get_weather")
    print("="*80)
    print("Format envoyé à Groq :")
    print(json.dumps({
        "type": "function",
        "function": {"name": "get_weather"}
    }, indent=2))
    
    response5 = await base_agent.process_tool_use(
        content="Dis-moi quelque chose.",
        tools=TOOLS,
        tool_mapping=TOOL_MAPPING,
        tool_choice={
            "type": "tool",
            "name": "get_weather"
        },
        provider=ModelProvider.GROQ,
        model_name="moonshotai/kimi-k2-instruct-0905",
        return_full_object=False
    )
    
    print(f"\n✅ Résultat : {json.dumps(response5, indent=2, ensure_ascii=False)}")
    print("\n⚠️ Note : Le modèle DOIT utiliser 'get_weather', peu importe la question !")
    
    # =========================================================================
    # RÉSUMÉ
    # =========================================================================
    print("\n" + "="*80)
    print("📊 RÉSUMÉ DES TRANSFORMATIONS")
    print("="*80)
    
    transformations = [
        {
            "Option": "auto",
            "Format Anthropic": '{"type": "auto"}',
            "Format Groq": '"auto"',
            "Comportement": "Le modèle décide"
        },
        {
            "Option": "required",
            "Format Anthropic": '{"type": "any"}',
            "Format Groq": '"required"',
            "Comportement": "DOIT utiliser un outil"
        },
        {
            "Option": "none",
            "Format Anthropic": '{"type": "none"}',
            "Format Groq": '"none"',
            "Comportement": "NE DOIT PAS utiliser d'outils"
        },
        {
            "Option": "forced tool",
            "Format Anthropic": '{"type": "tool", "name": "X"}',
            "Format Groq": '{"type": "function", "function": {"name": "X"}}',
            "Comportement": "Force l'outil X"
        }
    ]
    
    for t in transformations:
        print(f"\n🔹 {t['Option'].upper()}")
        print(f"   📥 Input (Anthropic)  : {t['Format Anthropic']}")
        print(f"   📤 Output (Groq)      : {t['Format Groq']}")
        print(f"   ℹ️  Comportement       : {t['Comportement']}")
    
    print("\n" + "="*80)
    print("✅ TESTS TERMINÉS")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(test_tool_choice_options())

