# 🔧 Guide d'Intégration des SPT Agents

## Vue d'Ensemble

Les **SPT Agents** (Short Process Tooling) sont des agents autonomes et isolés conçus pour des tâches rapides (< 30 secondes). Ils héritent de `BaseSPTAgent` pour garantir une structure standard, une isolation complète, et une gestion robuste des ressources.

---

## 📦 Architecture SPT Agent

```
BaseSPTAgent (Classe Abstraite)
├─ Boucle synchrone standard (execute())
├─ Budget tokens : 15K
├─ Max tours : 7
├─ Chat history isolé
├─ Self-healing (résumé auto si dépassement tokens)
├─ Clarification cache (TTL 1h)
└─ Méthodes abstraites :
   ├─ initialize_system_prompt()
   ├─ initialize_tools()
   └─ validate_instructions()

        ↓ Hérite

SPT_AGENT_CONCRET (ex: SPTContextManager)
├─ Implémente les 3 méthodes abstraites
├─ Son propre BaseAIAgent (créé à l'exécution)
├─ Outils spécialisés
└─ Wrapper synchrone pour intégration brain
```

---

## 🚀 Comment Créer un SPT Agent

### **Étape 1 : Créer la classe**

```python
# Fichier: app/pinnokio_agentic_workflow/tools/spt_my_agent.py

from typing import Dict, List, Any, Optional, Tuple
from .base_spt_agent import BaseSPTAgent, SPTStatus

class SPTMyAgent(BaseSPTAgent):
    """
    Agent SPT spécialisé pour [votre cas d'usage].
    
    ⭐ ISOLATION GARANTIE:
    - Son propre BaseAIAgent (créé à execute())
    - Chat history isolé du brain
    - Budget tokens: 15K, Max tours: 7
    """
    
    def __init__(self, 
                 firebase_user_id: str,
                 collection_name: str,
                 brain_context: Optional[Dict[str, Any]] = None):
        """
        Initialise l'agent SPT.
        
        Args:
            firebase_user_id: ID utilisateur Firebase
            collection_name: Nom de la collection (société)
            brain_context: Contexte du brain (mandate_path, dms_system, etc.)
        """
        # Récupérer les params DMS du contexte
        dms_system = brain_context.get('dms_system', 'google_drive') if brain_context else 'google_drive'
        dms_mode = brain_context.get('dms_mode', 'prod') if brain_context else 'prod'
        
        # ⭐ Appeler le parent (IMPORTANT)
        super().__init__(
            firebase_user_id=firebase_user_id,
            collection_name=collection_name,
            dms_system=dms_system,
            dms_mode=dms_mode,
            max_turns=7,           # Customizable
            token_budget=15000      # Customizable
        )
        
        self.brain_context = brain_context or {}
        logger.info(f"[SPTMyAgent] Initialisé")
    
    def validate_instructions(self, instructions: str) -> Tuple[bool, Optional[str]]:
        """
        ⭐ À IMPLÉMENTER: Valide les instructions d'entrée.
        
        Returns:
            Tuple[bool, Optional[str]]: (is_valid, error_message)
        """
        if not instructions or len(instructions.strip()) < 3:
            return False, "Instructions trop courtes"
        
        if len(instructions) > 5000:
            return False, "Instructions trop longues"
        
        return True, None
    
    def initialize_system_prompt(self) -> None:
        """
        ⭐ À IMPLÉMENTER: Initialise le prompt système spécialisé.
        """
        self.system_prompt = f"""Vous êtes un agent SPT spécialisé dans [votre fonction].

RÔLE:
- [Expliquer le rôle]

CONTEXTE:
- Utilisateur: {self.firebase_user_id}
- Société: {self.collection_name}

OUTILS DISPONIBLES:
- [Lister les outils]

STRATÉGIE:
- [Expliquer la stratégie]

TERMINAISON (⚠️ CRITIQUE):
🎯 APPELEZ L'OUTIL TERMINATE_TASK quand:
- Vous avez complété la mission
- Le résultat est structuré

⚠️ IMPORTANT: 
- **APPELEZ l'outil** TERMINATE_TASK, ne l'écrivez PAS dans votre texte
- Ne jamais mentionner "TERMINATE_TASK" dans une réponse textuelle
- L'appel de l'outil termine immédiatement l'exécution
- Ne pas mettre "**TERMINATE_TASK**" ou "TERMINATE_TASK" comme texte markdown
"""
    
    def initialize_tools(self) -> None:
        """
        ⭐ À IMPLÉMENTER: Initialise les outils disponibles.
        """
        self.tools = [
            {
                "name": "MY_TOOL",
                "description": "Description de l'outil",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "param1": {"type": "string", "description": "..."}
                    },
                    "required": ["param1"]
                }
            },
            {
                "name": "TERMINATE_TASK",
                "description": "🎯 Terminer la mission",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "result": {"type": "object"},
                        "conclusion": {"type": "string"}
                    },
                    "required": ["reason", "result", "conclusion"]
                }
            }
        ]
        
        self.tool_mapping = {
            "MY_TOOL": self._my_tool_impl,
            # TERMINATE_TASK géré par la boucle parent
        }
    
    # ═══ OUTILS IMPLÉMENTATION ═══
    
    def _my_tool_impl(self, param1: str) -> Dict[str, Any]:
        """Implémentation de MY_TOOL"""
        try:
            logger.info(f"[SPTMyAgent] MY_TOOL appelé: {param1}")
            
            # ... votre logique ici ...
            
            return {
                "success": True,
                "result": "..."
            }
        except Exception as e:
            logger.error(f"[SPTMyAgent] Erreur MY_TOOL: {e}")
            return {"success": False, "error": str(e)}
```

---

### **Étape 2 : Créer le wrapper d'intégration**

```python
# À la fin de votre fichier spt_my_agent.py

def create_spt_my_agent_wrapper(brain) -> Tuple[Dict[str, Any], callable]:
    """
    Crée l'outil SPT_MY_AGENT et son handler synchrone pour le brain.
    
    ⭐ Patterns clés:
    - SPT créera son PROPRE BaseAIAgent
    - Pas de partage avec brain.pinnokio_agent
    - Chat history complètement isolé
    """
    
    # Instance persistante du SPT Agent
    spt_agent = SPTMyAgent(
        firebase_user_id=brain.firebase_user_id,
        collection_name=brain.collection_name,
        brain_context=brain.get_user_context()
    )
    
    # Définition de l'outil
    tool_definition = {
        "name": "SPT_MY_AGENT",
        "description": "🔧 Agent SPT pour [votre fonction].",
        "input_schema": {
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": "Instruction pour le SPT (question, demande, etc.)"
                }
            },
            "required": ["instructions"]
        }
    }
    
    # Handler synchrone
    def handle_spt_my_agent(instructions: str, **kwargs) -> Dict[str, Any]:
        """
        ⭐ HANDLER SYNCHRONE - Appelé depuis le brain async via executor
        """
        try:
            logger.info(f"[BRAIN] 🔧 SPTMyAgent appelé: {instructions[:100]}...")
            
            # Exécuter l'agent SPT (crée son propre BaseAIAgent)
            result = spt_agent.execute(instructions)
            
            if result["status"] == SPTStatus.MISSION_COMPLETED:
                return {
                    "success": True,
                    "response_type": "completed",
                    "result": result.get("result"),
                    "turn_count": result.get("turn_count")
                }
            elif result["status"] == SPTStatus.CLARIFICATION_NEEDED:
                return {
                    "success": True,
                    "response_type": "clarification_needed",
                    "clarification": result.get("result"),
                    "clarification_id": result.get("clarification_id")
                }
            else:
                return {
                    "success": False,
                    "response_type": result.get("status"),
                    "error": result.get("result")
                }
        
        except Exception as e:
            logger.error(f"[BRAIN] Erreur SPTMyAgent: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    return tool_definition, handle_spt_my_agent
```

---

## 🧠 Intégration au Brain

### **Dans `pinnokio_brain.py` méthode `create_workflow_tools()`**

```python
def create_workflow_tools(self, thread_key: str, session=None):
    """Crée les outils du workflow"""
    
    # ... autres outils ...
    
    # ⭐ AJOUTER LE NOUVEAU SPT AGENT
    from ..tools.spt_my_agent import create_spt_my_agent_wrapper
    
    tool_def, handler = create_spt_my_agent_wrapper(self)
    spt_tools_list.append(tool_def)
    spt_tools_mapping["SPT_MY_AGENT"] = handler
    
    # ... suite du code ...
```

**C'est tout !** L'intégration est automatique. ✅

---

## 🔄 Flux d'Exécution Complet

```
Agent Principal (PinnokioBrain)
    ↓
Appelle SPT_MY_AGENT avec instructions
    ↓
handler_spt_my_agent() exécuté (synchrone)
    ↓
SPTMyAgent.execute(instructions)
    ↓
_initialize_own_ai_agent()  ← ⭐ PROPRE AGENT CRÉÉ ICI
    ↓
Boucle synchrone (max 7 tours, 15K tokens)
    ├─ Tour 1: AppelLLM + traitementReponse
    ├─ Tour 2: ...
    └─ Sortie: TERMINATE_TASK ou CLARIFICATION
    ↓
Chat history nettoyé
    ↓
Résultat retourné au brain (isolation garantie)
    ↓
Agent Principal continue
```

---

## ✅ Checklist d'Implémentation

- [ ] Créer classe héritant de `BaseSPTAgent`
- [ ] Implémenter `validate_instructions()`
- [ ] Implémenter `initialize_system_prompt()`
- [ ] Implémenter `initialize_tools()`
- [ ] Implémenter les fonctions des outils
- [ ] Créer `create_spt_xxx_wrapper()`
- [ ] Ajouter wrapper dans `create_workflow_tools()` du brain
- [ ] Tester avec agent principal

---

## 📊 Propriétés Héritées de BaseSPTAgent

Vous avez accès automatiquement à:

```python
self.ai_agent              # BaseAIAgent propre (créé à execute())
self.chat_history          # Liste isolée des messages
self.tools                 # Liste des outils disponibles
self.tool_mapping          # Mapping outil → fonction
self.system_prompt         # Prompt système spécialisé

# Utilitaires
self.execute(instructions)                    # Boucle principale
self._cache_clarification(text)              # Cache TTL 1h
self.get_cached_clarification(clarif_id)     # Récupère du cache
```

---

## 🎯 Bonnes Pratiques

1. **Nommage** : `SPT` + domaine (ex: `SPTContextManager`, `SPTTaskFinder`)
2. **Budget tokens** : Gardez 15K (peut être customisé si besoin)
3. **Max tours** : 7 tours suffisent pour la plupart des cas
4. **Isolation** : Ne jamais accéder à `brain.pinnokio_agent`
5. **Erreurs** : Retourner `{"success": False}` avec message clair
6. **Nettoyage** : Automatique via `_cleanup()` du parent

---

## 🚨 Points Critiques

| ⚠️ À FAIRE | ❌ À ÉVITER |
|-----------|-----------|
| Créer propre BaseAIAgent | Partager `brain.pinnokio_agent` |
| Implémenter les 3 méthodes abstraites | Ignorer les méthodes abstraites |
| Utiliser wrapper dans brain | Créer instance directement dans brain |
| Synchrone dans handler | Async dans handler |
| Nettoyer chat_history | Laisser historique traîner |

---

## 📝 Exemple Concret: SPTTaskFinder

```python
# Fichier: spt_task_finder.py

class SPTTaskFinder(BaseSPTAgent):
    """Agent pour trouver des tâches selon critères"""
    
    def validate_instructions(self, instructions):
        # Valider la syntaxe des critères
        return instructions.startswith(("find", "search")), "..."
    
    def initialize_system_prompt(self):
        self.system_prompt = f"""Vous trouvez des tâches...
        
OUTILS: SEARCH_TASKS, FILTER_TASKS, TERMINATE_TASK
"""
    
    def initialize_tools(self):
        self.tools = [
            {"name": "SEARCH_TASKS", ...},
            {"name": "FILTER_TASKS", ...},
            {"name": "TERMINATE_TASK", ...}
        ]
        self.tool_mapping = {
            "SEARCH_TASKS": self._search,
            "FILTER_TASKS": self._filter
        }
    
    def _search(self, query: str):
        # Implémentation
        return {"success": True, "tasks": [...]}
    
    def _filter(self, tasks, criteria):
        # Implémentation
        return {"success": True, "filtered": [...]}

# Dans brain.create_workflow_tools():
from ..tools.spt_task_finder import create_spt_task_finder_wrapper
tool_def, handler = create_spt_task_finder_wrapper(self)
spt_tools_list.append(tool_def)
spt_tools_mapping["SPT_TASK_FINDER"] = handler
```

---

## 🎓 Conclusion

Les SPT Agents offrent une façon standardisée, isolée et efficace de créer des outils autonomes. Le pattern est :

1. **Hériter** de `BaseSPTAgent`
2. **Implémenter** 3 méthodes abstraites
3. **Créer** un wrapper `create_spt_xxx_wrapper()`
4. **Intégrer** dans le brain avec 3 lignes de code

C'est tout ! Le reste (isolation, gestion tokens, nettoyage) est géré automatiquement. 🚀
