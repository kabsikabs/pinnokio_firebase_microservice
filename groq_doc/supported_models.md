---
description: Explore all available models on GroqCloud.
title: Supported Models - GroqDocs
image: https://console.groq.com/og_cloudv5.jpg
---

# Supported Models

Explore all available models on GroqCloud.

## [Featured Models and Systems](#featured-models-and-systems)

[![Groq Compound icon](https://console.groq.com/_next/image?url=%2Fgroq-circle.png&w=96&q=75)Groq CompoundGroq Compound is an AI system powered by openly available models that intelligently and selectively uses built-in tools to answer user queries, including web search and code execution.Token Speed\~450 tpsModalitiesCapabilities](/docs/compound/systems/compound)[![OpenAI GPT-OSS 120B icon](https://console.groq.com/_next/static/media/openailogo.523c87a0.svg)OpenAI GPT-OSS 120BGPT-OSS 120B is OpenAI's flagship open-weight language model with 120 billion parameters, built in browser search and code execution, and reasoning capabilities.Token Speed\~500 tpsModalitiesCapabilities](/docs/model/openai/gpt-oss-120b)

## [Production Models](#production-models)

**Note:** Production models are intended for use in your production environments. They meet or exceed our high standards for speed, quality, and reliability. Read more [here](https://console.groq.com/docs/deprecations).

| MODEL ID                                                                                                                                      | SPEED (T/SEC) | PRICE PER 1M TOKENS      | RATE LIMITS (DEVELOPER PLAN) | CONTEXT WINDOW (TOKENS) | MAX COMPLETION TOKENS | MAX FILE SIZE |
| --------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | ------------------------ | ---------------------------- | ----------------------- | --------------------- | ------------- |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Llama 3.1 8B](/docs/model/llama-3.1-8b-instant)llama-3.1-8b-instant                      | 560           | $0.05 input$0.08 output  | 250K TPM1K RPM               | 131,072                 | 131,072               | \-            |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Llama 3.3 70B](/docs/model/llama-3.3-70b-versatile)llama-3.3-70b-versatile               | 280           | $0.59 input$0.79 output  | 300K TPM1K RPM               | 131,072                 | 32,768                | \-            |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Llama Guard 4 12B](/docs/model/meta-llama/llama-guard-4-12b)meta-llama/llama-guard-4-12b | 1200          | $0.20 input$0.20 output  | 30K TPM100 RPM               | 131,072                 | 1,024                 | 20 MB         |
| [![OpenAI](https://console.groq.com/_next/static/media/openailogo.523c87a0.svg)GPT OSS 120B](/docs/model/openai/gpt-oss-120b)openai/gpt-oss-120b                      | 500           | $0.15 input$0.60 output  | 250K TPM1K RPM               | 131,072                 | 65,536                | \-            |
| [![OpenAI](https://console.groq.com/_next/static/media/openailogo.523c87a0.svg)GPT OSS 20B](/docs/model/openai/gpt-oss-20b)openai/gpt-oss-20b                         | 1000          | $0.075 input$0.30 output | 250K TPM1K RPM               | 131,072                 | 65,536                | \-            |
| [![OpenAI](https://console.groq.com/_next/static/media/openailogo.523c87a0.svg)Whisper](/docs/model/whisper-large-v3)whisper-large-v3                                 | \-            | $0.111 per hour          | 200K ASH300 RPM              | \-                      | \-                    | 100 MB        |
| [![OpenAI](https://console.groq.com/_next/static/media/openailogo.523c87a0.svg)Whisper Large V3 Turbo](/docs/model/whisper-large-v3-turbo)whisper-large-v3-turbo      | \-            | $0.04 per hour           | 400K ASH400 RPM              | \-                      | \-                    | 100 MB        |

## [Production Systems](#production-systems)

Systems are a collection of models and tools that work together to answer a user query.

  
| MODEL ID                                                                                                                      | SPEED (T/SEC) | PRICE PER 1M TOKENS | RATE LIMITS (DEVELOPER PLAN) | CONTEXT WINDOW (TOKENS) | MAX COMPLETION TOKENS | MAX FILE SIZE |
| ----------------------------------------------------------------------------------------------------------------------------- | ------------- | ------------------- | ---------------------------- | ----------------------- | --------------------- | ------------- |
| [![Groq](https://console.groq.com/_next/image?url=%2Fgroq-circle.png&w=48&q=75)Compound](/docs/compound/systems/compound)groq/compound                | 450           | \-                  | 200K TPM200 RPM              | 131,072                 | 8,192                 | \-            |
| [![Groq](https://console.groq.com/_next/image?url=%2Fgroq-circle.png&w=48&q=75)Compound Mini](/docs/compound/systems/compound-mini)groq/compound-mini | 450           | \-                  | 200K TPM200 RPM              | 131,072                 | 8,192                 | \-            |

  
[Learn More About Agentic ToolingDiscover how to build powerful applications with real-time web search and code execution](https://console.groq.com/docs/agentic-tooling) 

## [Preview Models](#preview-models)

**Note:** Preview models are intended for evaluation purposes only and should not be used in production environments as they may be discontinued at short notice. Read more about deprecations [here](https://console.groq.com/docs/deprecations).

| MODEL ID                                                                                                                                                                                | SPEED (T/SEC) | PRICE PER 1M TOKENS      | RATE LIMITS (DEVELOPER PLAN) | CONTEXT WINDOW (TOKENS) | MAX COMPLETION TOKENS | MAX FILE SIZE |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | ------------------------ | ---------------------------- | ----------------------- | --------------------- | ------------- |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Llama 4 Maverick 17B 128E](/docs/model/meta-llama/llama-4-maverick-17b-128e-instruct)meta-llama/llama-4-maverick-17b-128e-instruct | 600           | $0.20 input$0.60 output  | 300K TPM1K RPM               | 131,072                 | 8,192                 | 20 MB         |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Llama 4 Scout 17B 16E](/docs/model/meta-llama/llama-4-scout-17b-16e-instruct)meta-llama/llama-4-scout-17b-16e-instruct             | 750           | $0.11 input$0.34 output  | 300K TPM1K RPM               | 131,072                 | 8,192                 | 20 MB         |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Llama Prompt Guard 2 22M](/docs/model/meta-llama/llama-prompt-guard-2-22m)meta-llama/llama-prompt-guard-2-22m                      | \-            | $0.03 input$0.03 output  | 30K TPM100 RPM               | 512                     | 512                   | \-            |
| [![Meta](https://console.groq.com/_next/image?url=%2FMeta_logo.png&w=48&q=75)Prompt Guard 2 86M](/docs/model/meta-llama/llama-prompt-guard-2-86m)meta-llama/llama-prompt-guard-2-86m                            | \-            | $0.04 input$0.04 output  | 30K TPM100 RPM               | 512                     | 512                   | \-            |
| [![Moonshot AI](https://console.groq.com/_next/image?url=%2Fmoonshot_logo.png&w=48&q=75)Kimi K2 0905](/docs/model/moonshotai/kimi-k2-instruct-0905)moonshotai/kimi-k2-instruct-0905                             | 200           | $1.00 input$3.00 output  | 250K TPM1K RPM               | 262,144                 | 16,384                | \-            |
| [![OpenAI](https://console.groq.com/_next/static/media/openailogo.523c87a0.svg)Safety GPT OSS 20B](/docs/model/openai/gpt-oss-safeguard-20b)openai/gpt-oss-safeguard-20b                                        | 1000          | $0.075 input$0.30 output | 150K TPM1K RPM               | 131,072                 | 65,536                | \-            |
| [![PlayAI](https://console.groq.com/_next/static/media/playailogo.bf59d168.svg)PlayAI TTS](/docs/model/playai-tts)playai-tts                                                                                    | \-            | $50.00 per 1M characters | 50K TPM250 RPM               | 8,192                   | 8,192                 | \-            |
| [![PlayAI](https://console.groq.com/_next/static/media/playailogo.bf59d168.svg)PlayAI TTS Arabic](/docs/model/playai-tts-arabic)playai-tts-arabic                                                               | \-            | $50.00 per 1M characters | 50K TPM250 RPM               | 8,192                   | 8,192                 | \-            |
| [![Alibaba Cloud](https://console.groq.com/_next/image?url=%2Fqwen_logo.png&w=48&q=75)Qwen3-32B](/docs/model/qwen/qwen3-32b)qwen/qwen3-32b                                                                      | 400           | $0.29 input$0.59 output  | 300K TPM1K RPM               | 131,072                 | 40,960                | \-            |

## [Deprecated Models](#deprecated-models)

Deprecated models are models that are no longer supported or will no longer be supported in the future. See our deprecation guidelines and deprecated models [here](https://console.groq.com/docs/deprecations).

## [Get All Available Models](#get-all-available-models)

Hosted models are directly accessible through the GroqCloud Models API endpoint using the model IDs mentioned above. You can use the `https://api.groq.com/openai/v1/models` endpoint to return a JSON list of all active models:

shell

```
curl -X GET "https://api.groq.com/openai/v1/models" \
     -H "Authorization: Bearer $GROQ_API_KEY" \
     -H "Content-Type: application/json"
```

```
import Groq from "groq-sdk";

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

const getModels = async () => {
  return await groq.models.list();
};

getModels().then((models) => {
  // console.log(models);
});
```

```
import requests
import os

api_key = os.environ.get("GROQ_API_KEY")
url = "https://api.groq.com/openai/v1/models"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

response = requests.get(url, headers=headers)

print(response.json())
```

================================================================================
LISTE DES MODÈLES GROQ DISPONIBLES
================================================================================

🔄 Initialisation de l'agent Groq...
✅ Clé API récupérée avec succès!

🔄 Récupération de la liste des modèles...

📋 RÉPONSE BRUTE (JSON complet):
================================================================================
{
  "object": "list",
  "data": [
    {
      "id": "whisper-large-v3",
      "object": "model",
      "created": 1693721698,
      "owned_by": "OpenAI",
      "active": true,
      "context_window": 448,
      "public_apps": null,
      "max_completion_tokens": 448
    },
    {
      "id": "meta-llama/llama-4-scout-17b-16e-instruct",
      "object": "model",
      "created": 1743874824,
      "owned_by": "Meta",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 8192
    },
    {
      "id": "openai/gpt-oss-20b",
      "object": "model",
      "created": 1754407957,
      "owned_by": "OpenAI",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 65536
    },
    {
      "id": "qwen/qwen3-32b",
      "object": "model",
      "created": 1748396646,
      "owned_by": "Alibaba Cloud",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 40960
    },
    {
      "id": "playai-tts",
      "object": "model",
      "created": 1740682771,
      "owned_by": "PlayAI",
      "active": true,
      "context_window": 8192,
      "public_apps": null,
      "max_completion_tokens": 8192
    },
    {
      "id": "openai/gpt-oss-120b",
      "object": "model",
      "created": 1754408224,
      "owned_by": "OpenAI",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 65536
    },
    {
      "id": "meta-llama/llama-guard-4-12b",
      "object": "model",
      "created": 1746743847,
      "owned_by": "Meta",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 1024
    },
    {
      "id": "moonshotai/kimi-k2-instruct-0905",
      "object": "model",
      "created": 1757046093,
      "owned_by": "Moonshot AI",
      "active": true,
      "context_window": 262144,
      "public_apps": null,
      "max_completion_tokens": 16384
    },
    {
      "id": "whisper-large-v3-turbo",
      "object": "model",
      "created": 1728413088,
      "owned_by": "OpenAI",
      "active": true,
      "context_window": 448,
      "public_apps": null,
      "max_completion_tokens": 448
    },
    {
      "id": "moonshotai/kimi-k2-instruct",
      "object": "model",
      "created": 1752435491,
      "owned_by": "Moonshot AI",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 16384
    },
    {
      "id": "groq/compound",
      "object": "model",
      "created": 1756949530,
      "owned_by": "Groq",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 8192
    },
    {
      "id": "meta-llama/llama-4-maverick-17b-128e-instruct",
      "object": "model",
      "created": 1743877158,
      "owned_by": "Meta",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 8192
    },
    {
      "id": "llama-3.3-70b-versatile",
      "object": "model",
      "created": 1733447754,
      "owned_by": "Meta",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 32768
    },
    {
      "id": "groq/compound-mini",
      "object": "model",
      "created": 1756949707,
      "owned_by": "Groq",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 8192
    },
    {
      "id": "playai-tts-arabic",
      "object": "model",
      "created": 1740682783,
      "owned_by": "PlayAI",
      "active": true,
      "context_window": 8192,
      "public_apps": null,
      "max_completion_tokens": 8192
    },
    {
      "id": "openai/gpt-oss-safeguard-20b",
      "object": "model",
      "created": 1761708789,
      "owned_by": "OpenAI",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 65536
    },
    {
      "id": "allam-2-7b",
      "object": "model",
      "created": 1737672203,
      "owned_by": "SDAIA",
      "active": true,
      "context_window": 4096,
      "public_apps": null,
      "max_completion_tokens": 4096
    },
    {
      "id": "meta-llama/llama-prompt-guard-2-22m",
      "object": "model",
      "created": 1748632101,
      "owned_by": "Meta",
      "active": true,
      "context_window": 512,
      "public_apps": null,
      "max_completion_tokens": 512
    },
    {
      "id": "meta-llama/llama-prompt-guard-2-86m",
      "object": "model",
      "created": 1748632165,
      "owned_by": "Meta",
      "active": true,
      "context_window": 512,
      "public_apps": null,
      "max_completion_tokens": 512
    },
    {
      "id": "llama-3.1-8b-instant",
      "object": "model",
      "created": 1693721698,
      "owned_by": "Meta",
      "active": true,
      "context_window": 131072,
      "public_apps": null,
      "max_completion_tokens": 131072
    }
  ]
}
================================================================================

📊 NOMBRE TOTAL DE MODÈLES: 20

================================================================================
DÉTAILS DES MODÈLES
================================================================================

1. 🤖 allam-2-7b
   └─ Propriétaire: SDAIA
   └─ Créé: 1737672203
   └─ active: True
   └─ context_window: 4096
   └─ public_apps: None
   └─ max_completion_tokens: 4096

2. 🤖 groq/compound
   └─ Propriétaire: Groq
   └─ Créé: 1756949530
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 8192

3. 🤖 groq/compound-mini
   └─ Propriétaire: Groq
   └─ Créé: 1756949707
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 8192

4. 🤖 llama-3.1-8b-instant
   └─ Propriétaire: Meta
   └─ Créé: 1693721698
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 131072

5. 🤖 llama-3.3-70b-versatile
   └─ Propriétaire: Meta
   └─ Créé: 1733447754
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 32768

6. 🤖 meta-llama/llama-4-maverick-17b-128e-instruct
   └─ Propriétaire: Meta
   └─ Créé: 1743877158
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 8192

7. 🤖 meta-llama/llama-4-scout-17b-16e-instruct
   └─ Propriétaire: Meta
   └─ Créé: 1743874824
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 8192

8. 🤖 meta-llama/llama-guard-4-12b
   └─ Propriétaire: Meta
   └─ Créé: 1746743847
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 1024

9. 🤖 meta-llama/llama-prompt-guard-2-22m
   └─ Propriétaire: Meta
   └─ Créé: 1748632101
   └─ active: True
   └─ context_window: 512
   └─ public_apps: None
   └─ max_completion_tokens: 512

10. 🤖 meta-llama/llama-prompt-guard-2-86m
   └─ Propriétaire: Meta
   └─ Créé: 1748632165
   └─ active: True
   └─ context_window: 512
   └─ public_apps: None
   └─ max_completion_tokens: 512

11. 🤖 moonshotai/kimi-k2-instruct
   └─ Propriétaire: Moonshot AI
   └─ Créé: 1752435491
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 16384

12. 🤖 moonshotai/kimi-k2-instruct-0905
   └─ Propriétaire: Moonshot AI
   └─ Créé: 1757046093
   └─ active: True
   └─ context_window: 262144
   └─ public_apps: None
   └─ max_completion_tokens: 16384

13. 🤖 openai/gpt-oss-120b
   └─ Propriétaire: OpenAI
   └─ Créé: 1754408224
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 65536

14. 🤖 openai/gpt-oss-20b
   └─ Propriétaire: OpenAI
   └─ Créé: 1754407957
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 65536

15. 🤖 openai/gpt-oss-safeguard-20b
   └─ Propriétaire: OpenAI
   └─ Créé: 1761708789
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 65536

16. 🤖 playai-tts
   └─ Propriétaire: PlayAI
   └─ Créé: 1740682771
   └─ active: True
   └─ context_window: 8192
   └─ public_apps: None
   └─ max_completion_tokens: 8192

17. 🤖 playai-tts-arabic
   └─ Propriétaire: PlayAI
   └─ Créé: 1740682783
   └─ active: True
   └─ context_window: 8192
   └─ public_apps: None
   └─ max_completion_tokens: 8192

18. 🤖 qwen/qwen3-32b
   └─ Propriétaire: Alibaba Cloud
   └─ Créé: 1748396646
   └─ active: True
   └─ context_window: 131072
   └─ public_apps: None
   └─ max_completion_tokens: 40960

19. 🤖 whisper-large-v3
   └─ Propriétaire: OpenAI
   └─ Créé: 1693721698
   └─ active: True
   └─ context_window: 448
   └─ public_apps: None
   └─ max_completion_tokens: 448

20. 🤖 whisper-large-v3-turbo
   └─ Propriétaire: OpenAI
   └─ Créé: 1728413088
   └─ active: True
   └─ context_window: 448
   └─ public_apps: None
   └─ max_completion_tokens: 448


================================================================================
CATÉGORISATION PAR FOURNISSEUR
================================================================================


📦 Alibaba Qwen (1 modèles):
   • qwen/qwen3-32b

📦 Autres (3 modèles):
   • allam-2-7b
   • playai-tts
   • playai-tts-arabic

📦 Groq Compound (2 modèles):
   • groq/compound
   • groq/compound-mini

📦 Meta Llama (7 modèles):
   • llama-3.1-8b-instant
   • llama-3.3-70b-versatile
   • meta-llama/llama-4-maverick-17b-128e-instruct
   • meta-llama/llama-4-scout-17b-16e-instruct
   • meta-llama/llama-guard-4-12b
   • meta-llama/llama-prompt-guard-2-22m
   • meta-llama/llama-prompt-guard-2-86m

📦 Moonshot AI (Kimi) (2 modèles):
   • moonshotai/kimi-k2-instruct
   • moonshotai/kimi-k2-instruct-0905

📦 OpenAI (3 modèles):
   • openai/gpt-oss-120b
   • openai/gpt-oss-20b
   • openai/gpt-oss-safeguard-20b

📦 Whisper (Audio) (2 modèles):
   • whisper-large-v3
   • whisper-large-v3-turbo

================================================================================
RECHERCHE DE CAPACITÉS SPÉCIFIQUES
================================================================================

🖼️  MODÈLES POTENTIELS AVEC VISION (3):
   • meta-llama/llama-4-maverick-17b-128e-instruct
   • meta-llama/llama-4-scout-17b-16e-instruct
   • meta-llama/llama-guard-4-12b

🧠 MODÈLES POTENTIELS AVEC RAISONNEMENT (2):
   • moonshotai/kimi-k2-instruct
   • moonshotai/kimi-k2-instruct-0905

================================================================================
✅ Liste complète exportée dans: groq_models_list.json
================================================================================