# 🤖 Architecture Agentique Pinnokio - Guide de Développement

## 📋 Table des matières

1. [Structure du Contexte](#1-structure-du-contexte)
2. [Agents et Modes](#2-agents-et-modes)
3. [Boucle d'Exécution (Loop)](#3-boucle-dexécution-loop)
4. [Modes de Workflow](#4-modes-de-workflow)
5. [Missions des Agents](#5-missions-des-agents)
6. [Intégration des Outils](#6-intégration-des-outils)

---

## 1. Structure du Contexte

### 1.1 Hiérarchie des Sessions

```
LLMSessionManager (Singleton Global)
    └── LLMSession (Par user_id + company_id)
            └── PinnokioBrain (Orchestrateur Principal)
                    ├── user_context      → Profil utilisateur/entreprise
                    ├── workflow_params   → Paramètres d'approbation
                    ├── jobs_data         → Données brutes des jobs
                    └── jobs_metrics      → Compteurs par département
```

### 1.2 Variables de Contexte

| Variable | Source | Contenu |
|----------|--------|---------|
| `user_context` | Firebase Firestore | company_name, mandate_path, client_uuid, timezone, ERPs |
| `workflow_params` | Firebase Firestore | Apbookeeper_param, Router_param, Banker_param |
| `jobs_data` | Redis (UI) / Firebase (BACKEND) | APBOOKEEPER, ROUTER, BANK avec listes de jobs |
| `jobs_metrics` | Calculé depuis jobs_data | Compteurs to_do, in_process, pending, processed |

### 1.3 Exemple de workflow_params

```python
workflow_params = {
    "Apbookeeper_param": {
        "apbookeeper_approval_required": True,
        "apbookeeper_approval_contact_creation": False
    },
    "Router_param": {
        "router_approval_required": False,
        "router_automated_workflow": True
    },
    "Banker_param": {
        "banker_approval_required": True,
        "banker_approval_thresholdworkflow": "95"
    }
}
```

---

## 2. Agents et Modes

### 2.1 Registry des Modes (`agent_modes.py`)

```python
_AGENT_MODE_REGISTRY = {
    "general_chat":     → _build_general_tools    → TOUS les outils
    "accounting_chat":  → _build_general_tools    → TOUS les outils
    "onboarding_chat":  → _build_general_tools    → TOUS les outils
    "task_execution":   → _build_general_tools    → TOUS les outils
    "apbookeeper_chat": → _build_specialized_tools → Aucun outil
    "router_chat":      → _build_specialized_tools → Aucun outil
    "banker_chat":      → _build_specialized_tools → Aucun outil
}
```

### 2.2 Configuration d'un Mode

```python
AgentModeConfig = NamedTuple(
    name: str,           # Nom du mode
    prompt_builder: Fn,  # Fonction qui construit le system_prompt
    tool_builder: Fn     # Fonction qui retourne (tool_set, tool_mapping)
)
```

### 2.3 Sélection du Mode

```python
# Dans create_workflow_tools()
config = get_agent_mode_config(chat_mode)
tool_set, tool_mapping = config.tool_builder(brain, thread_key, session, chat_mode, mode)
```

---

## 3. Boucle d'Exécution (Loop)

### 3.1 Workflow Unifié (`_process_unified_workflow`)

```
┌─────────────────────────────────────────────────────────────┐
│                   BOUCLE PRINCIPALE                         │
│                   (max_turns = 10)                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. VÉRIFICATION BUDGET TOKENS                              │
│     if tokens >= max_budget:                                │
│         → generate_summary()                                │
│         → reset_context_with_summary()                      │
│                                                             │
│  2. APPEL LLM                                               │
│     response = brain.process_single_turn(message, tools)    │
│                                                             │
│  3. ANALYSE RÉPONSE                                         │
│     ├── stop_reason == "tool_use"                           │
│     │   └── EXÉCUTER OUTILS → message = tool_results        │
│     │                                                       │
│     ├── stop_reason == "end_turn"                           │
│     │   └── BREAK (réponse finale)                          │
│     │                                                       │
│     └── WAIT_ON_LPT appelé                                  │
│         └── SAUVEGARDER ÉTAT → BREAK (pause workflow)       │
│                                                             │
│  4. STREAMING (si mode UI)                                  │
│     → WebSocket + RTDB                                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Calcul des Tokens

```python
# Dans BaseAIAgent.get_total_context_tokens()
total_tokens = history_tokens + system_prompt_tokens

# Budget par défaut
max_tokens_budget = 80_000
```

---

## 4. Modes de Workflow

### 4.1 Mode UI vs BACKEND

| Aspect | Mode UI | Mode BACKEND |
|--------|---------|--------------|
| **Déclencheur** | WebSocket utilisateur | Callback LPT / Tâche planifiée |
| **Source jobs** | Redis (cache) | Firebase (direct) |
| **Streaming** | ✅ WebSocket + RTDB | ❌ Pas de streaming |
| **Détection** | `_detect_connection_mode()` | Paramètre explicite |

### 4.2 Détection Automatique

```python
async def _detect_connection_mode(user_id, collection_name, thread_key):
    # Vérifier si utilisateur connecté via WebSocket
    is_connected = await hub.is_user_connected(user_id)
    
    # Vérifier cache Redis récent
    has_recent_cache = await check_redis_cache(collection_name)
    
    return "UI" if (is_connected and has_recent_cache) else "BACKEND"
```

### 4.3 Workflow State Manager (Redis)

```python
# Clé Redis pour état workflow
key = f"workflow_state:{collection_name}:{thread_key}"

# Structure
{
    "status": "waiting_lpt",
    "expected_lpt": "LPT_APBookkeeper",
    "paused_at": "2025-12-04T10:00:00Z",
    "execution_context": {...}
}
```

---

## 5. Missions des Agents

| Agent | Mission |
|-------|---------|
| **PinnokioBrain** | Orchestrateur principal. Gère les outils, le contexte, et coordonne les workflows. |
| **APBookkeeper** | Saisie automatique des factures fournisseur dans l'ERP. |
| **Router** | Classification et routage des documents vers les départements. |
| **Banker** | Réconciliation des transactions bancaires avec l'ERP. |
| **FileManager** | Gestion des fichiers Google Drive (lecture, écriture, organisation). |

---

## 6. Intégration des Outils

### 6.1 Architecture Optimisée (Tokens)

```
┌───────────────────────────────────────────────────────────────┐
│                    DÉFINITIONS D'OUTILS                       │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  Définition COURTE (envoyée à chaque appel API)               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ "description": "📋 Recherche factures par statut/nom.   │ │
│  │                 GET_TOOL_HELP pour détails."            │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ~100 tokens                          │
│                                                               │
│  Documentation DÉTAILLÉE (via GET_TOOL_HELP)                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ ## Rôle                                                 │ │
│  │ ## Paramètres (tableau)                                 │ │
│  │ ## Exemples d'utilisation                               │ │
│  │ ## Workflow typique                                     │ │
│  └─────────────────────────────────────────────────────────┘ │
│                     (seulement si demandé)                    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### 6.2 Fichiers Clés

| Fichier | Rôle |
|---------|------|
| `tool_help_registry.py` | Registre centralisé + DETAILED_HELP |
| `job_tools.py` | GET_APBOOKEEPER_JOBS, GET_ROUTER_JOBS, GET_BANK_TRANSACTIONS, Context tools |
| `lpt_client.py` | LPT_APBookkeeper, LPT_Router, LPT_Banker, versions ALL et STOP |
| `spt_tools.py` | GET_FIREBASE_DATA, SEARCH_CHROMADB |
| `task_tools.py` | CREATE_TASK, CREATE_CHECKLIST, UPDATE_STEP, WAIT_ON_LPT |
| `pinnokio_brain.py` | Assemblage final + VIEW_DRIVE_DOCUMENT, TERMINATE_TASK |

### 6.3 Comment Ajouter un Nouvel Outil

#### Étape 1 : Créer la définition courte

```python
# Dans votre fichier d'outils (ex: my_tools.py)
class MyNewTool:
    def get_tool_definition(self) -> Dict:
        """Définition COURTE pour l'API."""
        return {
            "name": "MY_NEW_TOOL",
            "description": "🔧 Description courte (1-2 lignes). GET_TOOL_HELP pour détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string", "description": "..."},
                    "param2": {"type": "integer", "description": "..."}
                },
                "required": ["param1"]
            }
        }
    
    async def execute(self, param1: str, param2: int = 10) -> Dict:
        """Logique d'exécution."""
        # ...
        return {"success": True, "result": ...}
```

#### Étape 2 : Ajouter la documentation détaillée

```python
# Dans tool_help_registry.py → DETAILED_HELP
DETAILED_HELP = {
    # ... outils existants ...
    
    "MY_NEW_TOOL": """
🔧 **MY_NEW_TOOL** - Titre descriptif

## Rôle
Description complète de ce que fait l'outil.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `param1` | string | Description détaillée |
| `param2` | integer | Description détaillée (défaut: 10) |

## Exemples d'utilisation

**Cas 1 :**
```json
{"param1": "valeur", "param2": 20}
```

## Notes importantes
⚠️ Points d'attention...
""",
}
```

#### Étape 3 : Intégrer dans pinnokio_brain.py

```python
# Dans _build_general_chat_tools()

# 1. Créer l'instance
from ..tools.my_tools import MyNewTool
my_tool = MyNewTool(...)
my_tool_def = my_tool.get_tool_definition()

# 2. Créer le handler
async def handle_my_new_tool(**kwargs):
    return await my_tool.execute(**kwargs)

# 3. Ajouter au tool_set
tool_set = [
    # ... autres outils ...
    my_tool_def,
]

# 4. Ajouter au tool_mapping
tool_mapping = {
    # ... autres mappings ...
    "MY_NEW_TOOL": handle_my_new_tool,
}
```

### 6.4 Types d'Outils

| Type | Durée | Communication | Exemple |
|------|-------|---------------|---------|
| **SPT** (Short Process) | < 30s | Synchrone | GET_FIREBASE_DATA |
| **LPT** (Long Process) | > 30s | HTTP + Callback | LPT_APBookkeeper |
| **Core** | Instantané | Interne | TERMINATE_TASK |

### 6.5 Pattern LPT (Long Process Tooling)

```
Agent                    LPT Client                   Agent Externe
  │                          │                              │
  │  LPT_APBookkeeper(...)   │                              │
  ├─────────────────────────>│                              │
  │                          │   HTTP POST /event-trigger   │
  │                          ├─────────────────────────────>│
  │                          │                              │
  │   {"status": "launched"} │                              │
  │<─────────────────────────┤                              │
  │                          │                              │
  │  WAIT_ON_LPT(...)        │                              │
  ├──────────────────────────┤                              │
  │  (workflow en pause)     │                              │
  │                          │                              │
  │                          │   CALLBACK (résultat)        │
  │                          │<─────────────────────────────┤
  │                          │                              │
  │  (reprise workflow)      │                              │
  │<─────────────────────────┤                              │
```

---

## 📁 Arborescence des Fichiers

```
app/pinnokio_agentic_workflow/
├── orchestrator/
│   ├── pinnokio_brain.py           # Orchestrateur principal
│   ├── agent_modes.py              # Registry des modes
│   ├── system_prompt_principal_agent.py
│   └── system_prompt_*.py          # Prompts par mode
│
├── tools/
│   ├── tool_help_registry.py       # 📚 Registre + DETAILED_HELP
│   ├── job_tools.py                # GET_JOBS + Context tools
│   ├── lpt_client.py               # Outils LPT
│   ├── spt_tools.py                # Outils SPT
│   ├── task_tools.py               # Gestion tâches planifiées
│   ├── wait_on_lpt.py              # WAIT_ON_LPT
│   └── job_loader.py               # Chargement jobs Redis/Firebase
│
└── file_manager_agent/
    ├── file_manager.py
    └── file_manager_tools.py
```

---

## ⚡ Résumé des Économies de Tokens

| Composant | Avant | Après | Gain |
|-----------|-------|-------|------|
| Définitions outils | ~9500 | ~2500 | **-74%** |
| System prompt | ~40000 | ~16000 | **-60%** |
| **Total contexte initial** | ~50000 | ~18500 | **~63%** |

---

## 🔗 Références

- `doc/ARCHITECTURE_AGENTIQUE_COMPLETE.md` - Documentation complète
- `doc/REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md` - Architecture Redis
- `doc/ARCHITECTURE_REDIS_JOBS_METRICS.md` - Jobs et métriques

