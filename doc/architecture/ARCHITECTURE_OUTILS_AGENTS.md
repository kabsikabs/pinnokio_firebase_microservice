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

### 1.3 Contextes métier Firestore (mandate_path/context)

En plus de `user_context` (profil mandat) et `jobs_data` (jobs), le système maintient des **contextes métier persistants** dans Firestore sous :

- `{mandate_path}/context/*`

**Documents actuellement supportés par les outils de contexte (ContextTools)** :

- **`router_context`** : règles de routage / classification
  - Champ: `router_prompt` (dict par service: `hr`, `invoices`, `banks_cash`, etc.)
- **`accounting_context`** : règles comptables globales
  - Champ: `data.accounting_context_0` (texte)
- **`bank_context`** : règles & conventions de rapprochement bancaire
  - Champ: `data.bank_context_0` (texte)
- **`general_context`** : profil entreprise
  - Champ: `context_company_profile_report` (texte)

⚠️ **RÈGLE CRITIQUE (anti-confusion)** :

- `router_context/router_prompt` = **règles de routage** (choix du département/service)
- `bank_context` = **contexte bancaire** (règles de rapprochement)
- `{mandate_path}/setup/function_table` = **règles d’approbation** par département (lecture seule), **ce n’est PAS un contexte métier**.

### 1.4 Exemple de workflow_params

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
| `job_tools.py` | GET_APBOOKEEPER_JOBS, GET_ROUTER_JOBS, GET_BANK_TRANSACTIONS, GET_EXPENSES_INFO, **ContextTools** (`ROUTER_PROMPT`, `APBOOKEEPER_CONTEXT`, `BANK_CONTEXT`, `COMPANY_CONTEXT`, `UPDATE_CONTEXT`) |
| `task_manager_tools.py` | **Lecture contractuelle “Solution A”** : index des travaux + timeline d’audit (`GET_TASK_MANAGER_INDEX`, `GET_TASK_MANAGER_DETAILS`) |
| `lpt_client.py` | LPT_APBookkeeper, LPT_Router, LPT_Banker, versions ALL et STOP |
| `spt_tools.py` | GET_FIREBASE_DATA, SEARCH_CHROMADB |
| `task_tools.py` | CREATE_TASK, CREATE_CHECKLIST, UPDATE_STEP, WAIT_ON_LPT |
| `pinnokio_brain.py` | Assemblage final + VIEW_DRIVE_DOCUMENT, TERMINATE_TASK |

### 6.6 Outils de contexte (ContextTools)

Les **ContextTools** sont des outils “courts” (accès direct Firestore) intégrés au `tool_set` des modes qui utilisent `_build_general_tools` (ex: `general_chat`, `accounting_chat`, `onboarding_chat`, `task_execution`).

**Outils disponibles** :

- `ROUTER_PROMPT(service)` : lire les règles de routage d’un service (source: `{mandate_path}/context/router_context`)
- `APBOOKEEPER_CONTEXT()` : lire le contexte comptable (source: `{mandate_path}/context/accounting_context`, champ `data.accounting_context_0`)
- `BANK_CONTEXT()` : lire le contexte bancaire (source: `{mandate_path}/context/bank_context`, champ `data.bank_context_0`)
- `COMPANY_CONTEXT()` : lire le profil entreprise (source: `{mandate_path}/context/general_context`)
- `UPDATE_CONTEXT(context_type, ...)` : modifier un contexte avec opérations `add/replace/delete` + approbation + sauvegarde Firestore
  - `context_type` supporte : `router`, `accounting`, `bank`, `company`
  - ⚠️ `service_name` requis uniquement pour `router`

### 6.7 Outils Task Manager (Index + Audit) — Contrat “Solution A”

Ces outils donnent à `general_chat` une **vision “travaux”** basée sur le contrat inter-départements (index + timeline append-only).

#### 6.7.1 Source de vérité (Firestore)

Les outils lisent **uniquement** dans les chemins contractuels suivants :

- **Index job** : `clients/{userId}/task_manager/{job_id}`
- **Audit events** : `clients/{userId}/task_manager/{job_id}/events/{event_id}`

#### 6.7.2 Outils disponibles

- **`GET_TASK_MANAGER_INDEX`**
  - Rôle : lister les travaux (dashboard / filtres / pagination).
  - Filtres : `department`, `status_final`, `status`, `last_outcome`, période (`started_from`, `started_to`), `file_name_contains`, pagination `start_after_job_id`.

- **`GET_TASK_MANAGER_DETAILS`**
  - Rôle : ouvrir un travail (`job_id`) et retourner **index + timeline**.
  - Paramètres : `job_id` + `events_limit` + `events_order`.

#### 6.7.3 Garantie de respect du contrat général (sécurité & segmentation)

**Règle critique** : `mandate_path` est **imposé** côté serveur et ne peut pas être fourni par l’agent.

Concrètement :

- **Base path imposé** : `userId` est récupéré depuis le contexte du brain (`brain.firebase_user_id`).
- **Filtre imposé** : `mandate_path` est récupéré depuis `brain.user_context["mandate_path"]` et appliqué via `where("mandate_path", "==", mandate_path)` sur l’index.
- **Accès refusé** : `GET_TASK_MANAGER_DETAILS(job_id=...)` refuse si le doc n’a pas le même `mandate_path`.

➡️ Résultat : l’agent ne peut **pas** “explorer” d’autres mandats ni d’autres users, même par erreur ou prompt injection.

#### 6.7.4 Pattern d’intégration (conforme au framework outils)

**Code**

- Implémentation : `app/pinnokio_agentic_workflow/tools/task_manager_tools.py`
  - Définitions courtes : `get_task_manager_index_definition()` + `get_task_manager_details_definition()`
  - Exécution : `get_index(...)` + `get_details(job_id, ...)`

- Wiring : `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py`
  - Ajout des définitions dans `tool_set`
  - Ajout des handlers dans `tool_mapping`

**Documentation**

- Doc détaillée via `GET_TOOL_HELP` :
  - Entrées ajoutées dans `app/pinnokio_agentic_workflow/tools/tool_help_registry.py` (`DETAILED_HELP["GET_TASK_MANAGER_INDEX"]`, `DETAILED_HELP["GET_TASK_MANAGER_DETAILS"]`)
  - Le registre `ToolHelpRegistry` expose `GET_TOOL_HELP` dynamiquement (uniquement pour les outils réellement chargés).

#### 6.7.5 Notes de compatibilité “départements”

Le contrat autorise des extensions sous `department_data.<DEPARTMENT>`. Selon les départements, la clé `<DEPARTMENT>` peut varier en casse (ex: `router`, `banker`, `APbookeeper`).  
Ces outils renvoient `department_data` **tel quel** (pas de normalisation), pour éviter toute perte d’information.

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

- `doc/architecture/ARCHITECTURE_AGENTIQUE_COMPLETE.md` - Documentation complète
- `doc/infrastructure/REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md` - Architecture Redis
- `doc/architecture/ARCHITECTURE_REDIS_JOBS_METRICS.md` - Jobs et métriques

