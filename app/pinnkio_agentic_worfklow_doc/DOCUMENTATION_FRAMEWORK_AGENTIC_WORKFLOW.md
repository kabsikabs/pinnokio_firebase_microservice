# 📚 DOCUMENTATION COMPLÈTE - FRAMEWORK AGENTIC WORKFLOW

## 🎯 Vue d'ensemble

Ce document décrit en détail le framework de workflow agentic implémenté dans l'application, conçu pour permettre à des agents IA d'exécuter des tâches complexes de manière autonome et itérative.

### Caractéristiques principales

- **Architecture à deux niveaux** : Boucle globale (itérations) et boucle interne (tours)
- **Système d'agents intelligents** : Agent exécutant avec contexte maintenu
- **Gestion d'outils** : Définition, mapping et exécution d'outils spécialisés
- **Terminaison contrôlée** : Outil dédié pour signaler la fin de mission
- **Résumés automatiques** : Génération et réinjection en cas de dépassement
- **Tracking complet** : Suivi des tokens, performances et états

---

## 📐 ARCHITECTURE GLOBALE

### Schéma conceptuel

```
┌─────────────────────────────────────────────────────────────────┐
│                     APPLICATION PRINCIPALE                       │
│                    (OPEN_EXPENSES_CHECK)                         │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                ┌──────────────▼───────────────┐
                │   BOUCLE EXTERNE (Itérations) │
                │   Max: 3 itérations           │
                └──────────────┬───────────────┘
                               │
            ┌──────────────────▼──────────────────┐
            │     EXPENSES_AGENT_WORKFLOW         │
            │  (Workflow intelligent interne)     │
            └──────────────┬─────────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │   BOUCLE INTERNE (Tours)            │
        │   Max: 7 tours par itération        │
        └──────────────┬─────────────────────┘
                       │
    ┌──────────────────▼──────────────────────┐
    │     PROCESS_TOOL_USE                    │
    │  (Appel agent + exécution outils)       │
    └──────────────┬─────────────────────────┘
                   │
    ┌──────────────▼──────────────────────────┐
    │    GESTION DES OUTPUTS                  │
    │  - Tool outputs → Traitement            │
    │  - Text outputs → Contexte              │
    │  - TERMINATE_SEARCH → Sortie            │
    └─────────────────────────────────────────┘
```

---

## 🏗️ COMPOSANTS DU FRAMEWORK

### 1. CLASSE DE BASE : BaseAIAgent

**Fichier** : `tools/langchain_tools.py` (ligne 313)

**Rôle** : Classe de base pour tous les agents IA avec support multi-providers et DMS

#### Initialisation

```python
class BaseAIAgent:
    def __init__(self, 
                 collection_name: Optional[str] = None,
                 dms_system: Optional[str] = None,
                 dms_mode: Optional[str] = None,
                 firebase_user_id: Optional[str] = None,
                 chat_instance: Optional[Any] = None,
                 job_id: Optional[str] = None) -> None:
```

#### Attributs clés

- `self.chat_history` : Historique des conversations pour maintenir le contexte
- `self.token_usage` : Suivi des tokens consommés
- `self.provider_instances` : Instances des différents providers AI
- `self.system_prompt` : Prompt système de l'agent
- `self.token_manager` : Gestionnaire de tokens avec SQLite

#### Providers supportés

```python
self.provider_models = {
    ModelProvider.ANTHROPIC: {
        ModelSize.SMALL: ["claude-3-5-haiku-20241022"],
        ModelSize.MEDIUM: ["claude-3-7-sonnet-20250219"],
        ModelSize.LARGE: ["claude-3-opus-latest"]
    },
    ModelProvider.OPENAI: {
        ModelSize.SMALL: ["gpt-4.1-mini-2025-04-14"],
        ModelSize.MEDIUM: ["gpt-4.1-2025-04-14"],
        ModelSize.LARGE: ["o1"]
    },
    ModelProvider.GEMINI: {
        ModelSize.SMALL: ["gemini-2.0-flash"],
        ModelSize.MEDIUM: ["gemini-2.5-pro-preview-05-06"],
        ModelSize.LARGE: ["gemini-1.5-pro"]
    }
}
```

---

### 2. AGENT EXÉCUTANT : Expenses Manager

**Fichier** : `tools/pybank.py` (ligne 5103)

**Rôle** : Agent spécialisé dans la recherche et l'analyse de notes de frais

#### Initialisation de l'agent

```python
def EXPENSES_MANAGER_INIT(self):
    """
    Initialise l'agent Expenses Manager avec le prompt système approprié
    """
    prompt = f"""Vous êtes un agent IA spécialisé dans la recherche et l'analyse 
    d'informations sur les notes de frais...
    """
    
    # Ajout d'instructions spécifiques si disponibles
    if hasattr(self, 'expenses_manager_adapted_instructions'):
        prompt += f"\n\n{'='*80}\nINSTRUCTIONS SPÉCIFIQUES:\n{self.expenses_manager_adapted_instructions}\n"
    
    self.expenses_manager.update_system_prompt(prompt)
```

#### Structure du System Prompt

Le prompt système est structuré en plusieurs sections :

1. **Rôle et contexte général**
   - Description du rôle de l'agent
   - Contexte métier (gestion de notes de frais)

2. **Paramètres disponibles**
   - Liste des paramètres d'outils
   - Format et type de chaque paramètre

3. **Stratégies de recherche**
   - Recherche partielle par fournisseur
   - Recherche par plages (montant, dates)
   - Filtrage par méthode de paiement
   - Recherche itérative

4. **Critères de matching**
   - Priorité : Montant et devise (±0.01€)
   - Dates avec tolérance (±3 jours)
   - Fournisseurs (attention aux variations)

5. **Workflow recommandé**
   ```
   a) Filtrer par montant avec tolérance
   b) Affiner par date
   c) Vérifier le fournisseur (recherche partielle)
   d) EN CAS DE DOUTE : utiliser VIEW_EXPENSE_DOCUMENT
   e) Demander le compte comptable
   ```

6. **Rapport de sortie obligatoire**
   - Informations essentielles (job_id, nature, date, montant)
   - Paramètres techniques (bank_case, entry_type, etc.)

7. **Instructions de terminaison**
   - Utilisation de TERMINATE_SEARCH
   - Format de conclusion

---

### 3. DÉFINITION DES OUTILS

**Fichier** : `tools/pybank.py` (ligne 4898)

#### Structure d'un outil

Chaque outil est défini au format JSON avec le schéma suivant :

```python
{
    "name": "NOM_OUTIL",
    "description": "Description détaillée du rôle et quand l'utiliser",
    "input_schema": {
        "type": "object",
        "properties": {
            "param1": {
                "type": "type_param",
                "description": "Description du paramètre"
            },
            # Autres paramètres...
        },
        "required": ["param1"]  # Paramètres obligatoires
    }
}
```

#### Exemple complet : GET_EXPENSES_INFO

```python
{
    "name": "GET_EXPENSES_INFO",
    "description": "Filtre et retourne les notes de frais selon différents critères. Supporte la recherche partielle par fournisseur et les plages de montants/dates.",
    "input_schema": {
        "type": "object",
        "properties": {
            "supplier_name": {
                "type": "string", 
                "description": "Nom du fournisseur (recherche partielle supportée - insensible à la casse)."
            },
            "job_id": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Liste des identifiants de notes de frais à rechercher."
            },
            "payment_method": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Méthode de paiement. Exemples : ['CARD', 'CASH', 'TRANSFER']."
            },
            "status": {
                "type": "array", 
                "items": {"type": "string"},
                "description": "État des notes. Exemples : ['to_process', 'processed']."
            },
            "date_range": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Format YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "Format YYYY-MM-DD"}
                },
                "description": "Plage de dates pour filtrer."
            },
            "amount_range": {
                "type": "object", 
                "properties": {
                    "min_amount": {"type": "number"},
                    "max_amount": {"type": "number"}
                },
                "description": "Plage de montants. Utilisez pour montants approximatifs."
            }
        },
        "required": []  # Tous les paramètres sont optionnels
    }
}
```

#### Exemple : VIEW_EXPENSE_DOCUMENT (Outil de vision)

```python
{
    "name": "VIEW_EXPENSE_DOCUMENT",
    "description": "🔍 Visualiser le document justificatif d'une note de frais pour vérifier les détails. À utiliser EN CAS DE DOUTE uniquement.",
    "input_schema": {
        "type": "object",
        "properties": {
            "expense_job_id": {
                "type": "string",
                "description": "L'identifiant unique (job_id) de la note de frais"
            },
            "question": {
                "type": "string",
                "description": "La question spécifique sur le document (ex: 'Quel est le montant exact et la devise?')"
            }
        },
        "required": ["expense_job_id", "question"]
    }
}
```

#### Exemple : TERMINATE_SEARCH (Outil de terminaison)

```python
{
    "name": "TERMINATE_SEARCH",
    "description": "🎯 Terminer la recherche quand la mission est accomplie. Utilisez dès que vous avez identifié la note de frais ET obtenu toutes les informations nécessaires.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Raison : 'Note de frais trouvée et identifiée' / 'Aucune correspondance trouvée' / 'Mission accomplie'"
            },
            "expense_job_id": {
                "type": "string",
                "description": "ID de la note identifiée (si applicable). Vide si aucune note trouvée."
            },
            "conclusion": {
                "type": "string",
                "description": "Réponse finale COMPLÈTE pour l'utilisateur. DOIT inclure: job_id, nature, date, montant, devise, libellé comptable, bank_case, entry_type, odoo_final_account_number, odoo_vat_type, odoo_vat_percentages."
            }
        },
        "required": ["reason", "conclusion"]
    }
}
```

---

### 4. TOOL MAPPING

**Fichier** : `tools/pybank.py` (ligne 5007)

Le tool mapping lie chaque nom d'outil à sa fonction d'implémentation :

```python
tool_map = {
    "GET_EXPENSES_INFO": self.filter_expenses_by_criteria,
    "VIEW_EXPENSE_DOCUMENT": self.VIEW_EXPENSE_DOCUMENT,
    "SEARCH_IN_CHART_OF_ACCOUNT": self.FETCH_ACCOUNT
}
```

**⚠️ IMPORTANT** : 
- Les clés doivent correspondre EXACTEMENT aux noms dans `tool_set`
- `TERMINATE_SEARCH` n'est PAS dans le mapping car géré directement par le workflow
- Les fonctions mappées doivent accepter les paramètres définis dans `input_schema`

#### Exemple d'implémentation d'une fonction mappée

```python
def filter_expenses_by_criteria(self, supplier_name=None, job_id=None, 
                                payment_method=None, status=None, 
                                date_range=None, amount_range=None):
    """
    Filtre les notes de frais selon les critères fournis
    """
    try:
        filtered_expenses = []
        
        for expense_job_id, expense_data in self.available_expenses.items():
            # Appliquer les filtres
            if supplier_name and supplier_name.lower() not in expense_data.get('supplier', '').lower():
                continue
            
            if job_id and expense_job_id not in job_id:
                continue
            
            # ... autres filtres
            
            filtered_expenses.append({
                'job_id': expense_job_id,
                'supplier': expense_data.get('supplier'),
                'amount': expense_data.get('amount'),
                'date': expense_data.get('date'),
                # ... autres champs
            })
        
        # Retourner résultats structurés
        return {
            'type': 'expense_list',
            'count': len(filtered_expenses),
            'expenses': filtered_expenses
        }
    
    except Exception as e:
        return {
            'type': 'error',
            'message': str(e)
        }
```

---

### 5. CONSTRUCTION DU PREMIER MESSAGE

**Fichier** : `tools/pybank.py` (ligne 5016)

Le premier message est crucial : il contient la requête utilisateur + tout le contexte nécessaire.

#### Structure du premier message

```python
query_enriched = f"""{query}

*************************************************************************
CONTEXTE DES NOTES DE FRAIS DISPONIBLES :
{available_expenses_summary}

*************************************************************************

ASTUCES IMPORTANTES POUR LE MATCHING :
- ⚠️ PRIORITÉ ABSOLUE : Montant et devise (tolérance ±0.01)
- Les noms de fournisseurs peuvent TRÈS DIFFÉRENTS du libellé bancaire
- Exemples : "PAYPAL *AMAZON" vs "Amazon", "SQ *CAFE" vs nom complet du café
- Sur les paiements POS/carte, le nom peut être cryptique ou abrégé
- Utilisez amount_range pour montants approximatifs (±2 unités)
- Utilisez date_range pour dates avec tolérance (±3 jours)
- EN CAS DE DOUTE sur le fournisseur : utilisez VIEW_EXPENSE_DOCUMENT
- Une fois la note identifiée : utilisez SEARCH_IN_CHART_OF_ACCOUNT pour le compte
- Itérez autant que nécessaire pour trouver LA note de frais correspondante
"""
```

#### Composants du premier message

1. **Requête utilisateur brute** (`query`)
   - La question ou tâche initiale
   - Peut contenir des détails sur une transaction bancaire

2. **Contexte métier** (`available_expenses_summary`)
   - Résumé des données disponibles
   - Exemple : "15 notes de frais non traitées, 42 notes traitées"
   - Peut inclure des statistiques utiles

3. **Instructions tactiques**
   - Conseils pratiques pour cette mission spécifique
   - Rappels des priorités et pièges à éviter
   - Références aux outils à utiliser

---

### 6. BOUCLE EXTERNE : Gestion des itérations

**Fichier** : `tools/pybank.py` (ligne 5044)

La boucle externe gère les itérations majeures en cas de dépassement du workflow interne.

#### Implémentation

```python
max_iterations = 3
iteration = 0
current_input = query_enriched  # Premier message complet

while iteration < max_iterations:
    iteration += 1
    print(f"[OPEN_EXPENSES_CHECK] Itération {iteration}/{max_iterations}")
    
    # APPEL DU WORKFLOW INTELLIGENT (boucle interne de tours)
    success, status_code, report = self.expenses_manager.expenses_agent_workflow(
        manager_instance=self.expenses_manager,
        initial_query=current_input,
        tools=tool_set,
        tool_mapping=tool_map,
        size=ModelSize.SMALL,
        project_id=self.collection_id,
        job_id=self.sp_k,
        workflow_step=f'open_expenses_check_iter_{iteration}',
        max_turns=7,
        raw_output=True
    )
    
    print(f"[OPEN_EXPENSES_CHECK] Itération {iteration} - Status: {status_code}")
    print(f"[OPEN_EXPENSES_CHECK] Report: {report[:300]}...")
    
    # Vérifier si la mission est accomplie
    if status_code == "MISSION_COMPLETED":
        print(f"✓ Mission accomplie à l'itération {iteration}")
        self.audit.add_messages_ai_hu(f"Réponse du département Expenses: {report}")
        self.expenses_manager.flush_chat_history()
        return report
    
    # Si pas terminé, préparer le prochain input avec le rapport
    if iteration < max_iterations:
        current_input = f"""╔═══════════════════════════════════════════════════════════╗
║              RAPPORT DE L'ITÉRATION PRÉCÉDENTE            ║
╚═══════════════════════════════════════════════════════════╝

{report}

╔═══════════════════════════════════════════════════════════╗
║              RAPPEL DE LA MISSION INITIALE                ║
╚═══════════════════════════════════════════════════════════╝

{query_enriched}

╔═══════════════════════════════════════════════════════════╗
║                      INSTRUCTIONS                         ║
╚═══════════════════════════════════════════════════════════╝

Tu as {max_iterations - iteration} itération(s) restante(s).
Continue ta recherche OU utilise TERMINATE_SEARCH si tu as trouvé la note de frais.
⚠️ RAPPEL : Si le montant et la date correspondent mais le nom diffère, utilise VIEW_EXPENSE_DOCUMENT !
"""

# Maximum d'itérations atteint
print(f"[OPEN_EXPENSES_CHECK] Maximum d'itérations atteint ({max_iterations})")
self.audit.add_messages_ai_hu(f"Réponse du département Expenses (max itérations): {report}")
self.expenses_manager.flush_chat_history()

return report
```

#### Logique de la boucle externe

1. **Itération 1** : 
   - Input = `query_enriched` (message original avec contexte)
   - Appel du workflow interne (max 7 tours)

2. **Si MISSION_COMPLETED** :
   - Sortie immédiate avec le rapport
   - Nettoyage de l'historique

3. **Si MAX_TURNS_REACHED** :
   - Récupération du rapport de résumé
   - Construction du nouveau message avec :
     * Rapport de l'itération précédente
     * Rappel de la mission initiale
     * Compteur d'itérations restantes
   - Relance du workflow interne

4. **Si max_iterations atteint** :
   - Retour du dernier rapport disponible
   - Logging et audit

#### Avantages de cette approche

- **Persistance** : L'historique de conversation est maintenu entre tours (dans le workflow)
- **Résilience** : En cas de blocage, l'agent peut repartir avec un nouveau contexte
- **Traçabilité** : Chaque itération est trackée séparément
- **Optimisation tokens** : L'historique est flush entre itérations pour éviter l'explosion

---

### 7. WORKFLOW INTERNE : expenses_agent_workflow

**Fichier** : `tools/langchain_tools.py` (ligne 3029)

Le workflow interne gère la boucle de tours pour une itération donnée.

#### Signature

```python
def expenses_agent_workflow(self,
                            manager_instance: Any,
                            initial_query: str,
                            tools: List[Dict[str, Any]],
                            tool_mapping: Dict[str, Any],
                            size: ModelSize = ModelSize.SMALL,
                            provider: Optional[ModelProvider] = None,
                            max_tokens: int = 2048,
                            project_id: str = None,
                            job_id: str = None,
                            workflow_step: str = 'expenses_workflow',
                            max_turns: int = 7,
                            raw_output: bool = True) -> Tuple[bool, str, str]:
```

#### Paramètres

- `manager_instance` : Instance de l'agent (pour maintenir le contexte)
- `initial_query` : Message d'entrée (peut contenir rapport si itération > 1)
- `tools` : Liste des outils disponibles (format JSON)
- `tool_mapping` : Mapping outil → fonction
- `size` : Taille du modèle (SMALL, MEDIUM, LARGE)
- `provider` : Provider AI (optionnel, par défaut celui de l'instance)
- `max_tokens` : Limite de tokens pour la réponse
- `project_id` / `job_id` : Pour tracking
- `workflow_step` : Nom de l'étape (pour logs)
- `max_turns` : Nombre maximum de tours
- `raw_output` : Format de sortie (liste ou autre)

#### Valeurs de retour

```python
(success: bool, status_code: str, final_response_text: str)
```

**Status codes possibles** :
- `"MISSION_COMPLETED"` : Mission accomplie (TERMINATE_SEARCH appelé)
- `"MAX_TURNS_REACHED"` : Limite de tours atteinte
- `"NO_IA_ACTION"` : Aucune action de l'IA
- `"ERROR_FATAL"` : Erreur fatale

#### Implémentation

```python
def expenses_agent_workflow(self, ...):
    try:
        print(f"[EXPENSES_WORKFLOW] Démarrage - Tours max: {max_turns}")
        
        turn_count = 0
        user_input = initial_query
        next_user_input_parts = []
        
        while turn_count < max_turns:
            turn_count += 1
            print(f"[EXPENSES_WORKFLOW] Tour {turn_count}/{max_turns}")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 1 : APPEL DE L'AGENT
            # ═══════════════════════════════════════════════════
            ia_responses = manager_instance.process_tool_use(
                content=user_input,
                tools=tools,
                tool_mapping=tool_mapping,
                size=size,
                provider=provider,
                max_tokens=max_tokens,
                raw_output=raw_output
            )
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 2 : TRACKING DES TOKENS
            # ═══════════════════════════════════════════════════
            if project_id and job_id:
                manager_instance.load_token_usage_to_db(
                    project_id=project_id,
                    job_id=job_id,
                    workflow_step=f"{workflow_step}_turn_{turn_count}"
                )
            
            print(f"[EXPENSES_WORKFLOW] Réponse tour {turn_count}: {str(ia_responses)[:300]}...")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 3 : NORMALISATION DES RÉPONSES
            # ═══════════════════════════════════════════════════
            if not isinstance(ia_responses, list):
                ia_responses = [ia_responses] if ia_responses else []
            
            next_user_input_parts = []
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 4 : TRAITEMENT DES RÉPONSES
            # ═══════════════════════════════════════════════════
            for response_block in ia_responses:
                if not isinstance(response_block, dict):
                    next_user_input_parts.append(f"Réponse inattendue: {str(response_block)[:200]}")
                    continue
                
                # ───────────────────────────────────────────────
                # CAS 1 : TOOL_OUTPUT
                # ───────────────────────────────────────────────
                if "tool_output" in response_block:
                    tool_block = response_block["tool_output"]
                    tool_name = tool_block.get('tool_name', 'UnknownTool')
                    tool_content = tool_block.get('content', '')
                    
                    print(f"  [EXPENSES_WORKFLOW] Outil appelé: {tool_name}")
                    
                    # ▼▼▼ DÉTECTION TERMINATE_SEARCH ▼▼▼
                    if tool_name == 'TERMINATE_SEARCH':
                        if isinstance(tool_content, dict):
                            reason = tool_content.get('reason', 'Non spécifié')
                            conclusion = tool_content.get('conclusion', '')
                            expense_job_id = tool_content.get('expense_job_id', '')
                        else:
                            reason = "Terminaison demandée"
                            conclusion = str(tool_content)
                            expense_job_id = ""
                        
                        print(f"[EXPENSES_WORKFLOW] ✓ TERMINATE_SEARCH - Raison: {reason}")
                        print(f"[EXPENSES_WORKFLOW] Expense Job ID: {expense_job_id}")
                        
                        # 🚪 SORTIE IMMÉDIATE
                        return True, "MISSION_COMPLETED", conclusion
                    
                    # GET_EXPENSES_INFO
                    elif tool_name == 'GET_EXPENSES_INFO':
                        if isinstance(tool_content, dict):
                            if tool_content.get('type') == 'too_many_results':
                                next_user_input_parts.append(
                                    f"Trop de résultats ({tool_content.get('count')} notes). "
                                    f"Affine avec des filtres supplémentaires."
                                )
                            elif tool_content.get('type') == 'expense_list':
                                expenses = tool_content.get('expenses', [])
                                next_user_input_parts.append(
                                    f"Liste de notes de frais trouvées: {expenses}. "
                                    f"Sélectionne la plus pertinente."
                                )
                            else:
                                next_user_input_parts.append(f"Résultat: {str(tool_content)[:500]}")
                        else:
                            next_user_input_parts.append(f"Résultat GET_EXPENSES_INFO: {str(tool_content)[:500]}")
                    
                    # VIEW_EXPENSE_DOCUMENT
                    elif tool_name == 'VIEW_EXPENSE_DOCUMENT':
                        print(f"  [EXPENSES_WORKFLOW] Résultat vision: {str(tool_content)[:200]}")
                        next_user_input_parts.append(f"Résultat de la vision: {str(tool_content)[:500]}")
                    
                    # SEARCH_IN_CHART_OF_ACCOUNT
                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"  [EXPENSES_WORKFLOW] Compte trouvé: {str(tool_content)[:200]}")
                        next_user_input_parts.append(f"Compte comptable: {str(tool_content)[:500]}")
                    
                    # Autres outils
                    else:
                        next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                
                # ───────────────────────────────────────────────
                # CAS 2 : TEXT_OUTPUT
                # ───────────────────────────────────────────────
                elif "text_output" in response_block:
                    text_block = response_block["text_output"]
                    extracted_text = "Pas de texte"
                    
                    if isinstance(text_block, dict) and "content" in text_block:
                        content = text_block["content"]
                        if isinstance(content, dict):
                            extracted_text = content.get('answer_text', str(content))
                        else:
                            extracted_text = str(content)
                    elif isinstance(text_block, str):
                        extracted_text = text_block
                    
                    print(f"  [EXPENSES_WORKFLOW] Texte: {extracted_text[:200]}...")
                    next_user_input_parts.append(f"Texte précédent: {extracted_text[:300]}")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 5 : PRÉPARER INPUT POUR PROCHAIN TOUR
            # ═══════════════════════════════════════════════════
            if next_user_input_parts:
                user_input = "\n".join(next_user_input_parts)
            else:
                print("[EXPENSES_WORKFLOW] Aucune réponse utilisable de l'IA")
                return False, "NO_IA_ACTION", "L'IA n'a pas fourni de réponse claire."
        
        # ═══════════════════════════════════════════════════
        # MAX TOURS ATTEINT
        # ═══════════════════════════════════════════════════
        print(f"[EXPENSES_WORKFLOW] Maximum de {max_turns} tours atteint")
        
        # Générer un rapport de ce qui s'est passé
        summary = f"Maximum de {max_turns} tours atteint. Dernier état: {user_input[:500]}"
        
        return False, "MAX_TURNS_REACHED", summary
        
    except Exception as e:
        import traceback
        print(f"[EXPENSES_WORKFLOW] ERREUR FATALE: {e}")
        traceback.print_exc()
        error_msg = f"Erreur dans expenses_agent_workflow: {str(e)}"
        return False, "ERROR_FATAL", error_msg
```

#### Détails du workflow

##### ÉTAPE 1 : Appel de l'agent

```python
ia_responses = manager_instance.process_tool_use(
    content=user_input,
    tools=tools,
    tool_mapping=tool_mapping,
    size=size,
    provider=provider,
    max_tokens=max_tokens,
    raw_output=raw_output
)
```

**Rôle de `process_tool_use`** :
- Envoie le message à l'API du provider (Anthropic, OpenAI, etc.)
- L'agent reçoit l'historique complet (contexte maintenu)
- L'agent décide d'utiliser un outil ou de répondre en texte
- Exécute les outils via le `tool_mapping`
- Retourne les résultats structurés

##### ÉTAPE 2 : Tracking des tokens

```python
if project_id and job_id:
    manager_instance.load_token_usage_to_db(
        project_id=project_id,
        job_id=job_id,
        workflow_step=f"{workflow_step}_turn_{turn_count}"
    )
```

**Informations trackées** :
- Nombre de tokens d'entrée
- Nombre de tokens de sortie
- Provider utilisé
- Modèle utilisé
- Timestamp
- Coûts associés

##### ÉTAPE 3 : Normalisation des réponses

```python
if not isinstance(ia_responses, list):
    ia_responses = [ia_responses] if ia_responses else []
```

**Formats possibles** :
- Liste de blocs : `[{}, {}, ...]`
- Bloc unique : `{}`
- Vide : `None` ou `""`

##### ÉTAPE 4 : Traitement des réponses

**Format des réponses** :

```python
ia_responses = [
    {
        "tool_output": {
            "tool_name": "GET_EXPENSES_INFO",
            "content": {
                "type": "expense_list",
                "count": 3,
                "expenses": [...]
            }
        }
    },
    {
        "text_output": {
            "content": {
                "answer_text": "J'ai trouvé 3 notes de frais correspondantes...",
                "thinking_text": "Je vais analyser chaque note..."
            }
        }
    }
]
```

**Traitement selon le type** :

1. **tool_output** :
   - Extraire `tool_name` et `content`
   - Si `TERMINATE_SEARCH` : sortie immédiate
   - Sinon : formater le résultat pour le prochain tour

2. **text_output** :
   - Extraire le texte de réponse
   - Ajouter au contexte pour le prochain tour

##### ÉTAPE 5 : Préparer le prochain tour

```python
user_input = "\n".join(next_user_input_parts)
```

**Contenu de `user_input` au tour N+1** :
```
Résultat outil GET_EXPENSES_INFO: {...}
Texte précédent: J'ai trouvé 3 notes de frais...
```

**Avantage** : L'agent reçoit uniquement les informations pertinentes, pas tout l'historique brut.

---

### 8. GESTION DES OUTPUTS D'OUTILS

#### Types de outputs

1. **Output structuré (dict)** :
   ```python
   {
       'type': 'expense_list',
       'count': 5,
       'expenses': [...]
   }
   ```
   - Facile à parser
   - Permet une gestion conditionnelle

2. **Output texte (str)** :
   ```python
   "5 notes de frais trouvées correspondant à vos critères."
   ```
   - Plus simple
   - Moins de contrôle

3. **Output erreur** :
   ```python
   {
       'type': 'error',
       'message': 'Critères trop larges, veuillez affiner'
   }
   ```
   - Signale un problème
   - Permet à l'agent de corriger

#### Conditions de sortie spéciales

##### TERMINATE_SEARCH

**Détection** (ligne 3119) :
```python
if tool_name == 'TERMINATE_SEARCH':
    if isinstance(tool_content, dict):
        reason = tool_content.get('reason', 'Non spécifié')
        conclusion = tool_content.get('conclusion', '')
        expense_job_id = tool_content.get('expense_job_id', '')
    else:
        reason = "Terminaison demandée"
        conclusion = str(tool_content)
        expense_job_id = ""
    
    print(f"✓ TERMINATE_SEARCH - Raison: {reason}")
    
    # SORTIE IMMÉDIATE
    return True, "MISSION_COMPLETED", conclusion
```

**Pourquoi c'est important** :
- Permet à l'agent de signaler explicitement la fin
- Évite de consommer des tours inutilement
- Fournit un rapport structuré et complet

##### Trop de résultats

```python
if tool_content.get('type') == 'too_many_results':
    next_user_input_parts.append(
        f"Trop de résultats ({tool_content.get('count')} notes). "
        f"Affine avec des filtres supplémentaires (montant, date, fournisseur)."
    )
```

**Objectif** : Guider l'agent vers des critères plus précis

---

### 9. GESTION DES TEXT OUTPUTS

**Code** (ligne 3172) :
```python
elif "text_output" in response_block:
    text_block = response_block["text_output"]
    extracted_text = "Pas de texte"
    
    if isinstance(text_block, dict) and "content" in text_block:
        content = text_block["content"]
        if isinstance(content, dict):
            extracted_text = content.get('answer_text', str(content))
        else:
            extracted_text = str(content)
    elif isinstance(text_block, str):
        extracted_text = text_block
    
    print(f"  [EXPENSES_WORKFLOW] Texte: {extracted_text[:200]}...")
    next_user_input_parts.append(f"Texte précédent: {extracted_text[:300]}")
```

#### Formats possibles de text_output

1. **Format structuré avec thinking** :
   ```python
   {
       "content": {
           "answer_text": "Je vais rechercher les notes de frais...",
           "thinking_text": "L'utilisateur demande une note pour 50€..."
       }
   }
   ```

2. **Format simple** :
   ```python
   {
       "content": "Je vais rechercher les notes de frais..."
   }
   ```

3. **Format direct** :
   ```python
   "Je vais rechercher les notes de frais..."
   ```

#### Rôle du text output

- **Réflexion de l'agent** : L'agent explique son raisonnement
- **Questions à l'utilisateur** : Demande de clarifications
- **Conclusions intermédiaires** : Résumés avant appel d'outils

**⚠️ Attention** : Un text_output sans tool_output peut indiquer :
- L'agent est bloqué
- L'agent a besoin d'infos supplémentaires
- L'agent n'a pas compris les outils disponibles

---

### 10. GÉNÉRATION DE RÉSUMÉ EN CAS DE DÉPASSEMENT

#### Dans le workflow interne (ligne 3200)

```python
# Max tours atteint
print(f"[EXPENSES_WORKFLOW] Maximum de {max_turns} tours atteint")

# Générer un rapport de ce qui s'est passé
summary = f"Maximum de {max_turns} tours atteint. Dernier état: {user_input[:500]}"

return False, "MAX_TURNS_REACHED", summary
```

**Contenu du résumé** :
- Status : `MAX_TURNS_REACHED`
- Dernier état de `user_input` (résultats du dernier tour)
- Limité à 500 caractères pour éviter l'explosion

#### Insertion dans la boucle externe (ligne 5074)

```python
if iteration < max_iterations:
    current_input = f"""╔═══════════════════════════════════════════════════════════╗
║              RAPPORT DE L'ITÉRATION PRÉCÉDENTE            ║
╚═══════════════════════════════════════════════════════════╝

{report}

╔═══════════════════════════════════════════════════════════╗
║              RAPPEL DE LA MISSION INITIALE                ║
╚═══════════════════════════════════════════════════════════╝

{query_enriched}

╔═══════════════════════════════════════════════════════════╗
║                      INSTRUCTIONS                         ║
╚═══════════════════════════════════════════════════════════╝

Tu as {max_iterations - iteration} itération(s) restante(s).
Continue ta recherche OU utilise TERMINATE_SEARCH si tu as trouvé la note de frais.
⚠️ RAPPEL : Si le montant et la date correspondent mais le nom diffère, utilise VIEW_EXPENSE_DOCUMENT !
"""
```

**Structure du message de reprise** :
1. Rapport de l'itération précédente (ce qui a été fait)
2. Rappel de la mission initiale (pour recontextualiser)
3. Instructions pour la suite (guidage)
4. Compteur d'itérations restantes (urgence)

**Avantages** :
- L'agent comprend où il en est
- L'agent ne répète pas les mêmes erreurs
- L'agent sait qu'il doit conclure rapidement

---

## 🔧 GUIDE D'IMPLÉMENTATION PRATIQUE

### Étape 1 : Créer votre agent de base

```python
# Dans votre classe principale (ex: BankReconciliationInstance)

def MY_AGENT_INIT(self):
    """
    Initialise votre agent spécialisé avec le prompt système
    """
    prompt = f"""Vous êtes un agent IA spécialisé dans [VOTRE DOMAINE].
    
    RÔLE :
    Votre tâche principale est de [DÉCRIRE LA TÂCHE].
    
    CONTEXTE :
    [EXPLIQUER LE CONTEXTE MÉTIER]
    
    OUTILS DISPONIBLES :
    [LISTER LES OUTILS ET LEUR UTILITÉ]
    
    STRATÉGIE RECOMMANDÉE :
    1. [ÉTAPE 1]
    2. [ÉTAPE 2]
    3. ...
    
    CRITÈRES DE SUCCÈS :
    - [CRITÈRE 1]
    - [CRITÈRE 2]
    
    RAPPORT DE SORTIE OBLIGATOIRE :
    Vous devez retourner les informations suivantes :
    - [CHAMP 1]
    - [CHAMP 2]
    - ...
    
    TERMINAISON :
    Utilisez TERMINATE_TASK dès que [CONDITION DE TERMINAISON].
    """
    
    # Ajouter instructions spécifiques si disponibles
    if hasattr(self, 'my_agent_adapted_instructions') and self.my_agent_adapted_instructions:
        prompt += f"\n\n{'='*80}\nINSTRUCTIONS SPÉCIFIQUES:\n{self.my_agent_adapted_instructions}\n{'='*80}\n"
    
    self.my_agent.update_system_prompt(prompt)
```

### Étape 2 : Définir vos outils

```python
def MY_WORKFLOW_FUNCTION(self, query):
    """
    Point d'entrée de votre workflow
    """
    print(f"DÉMARRAGE DE MON WORKFLOW.....")
    self.MY_AGENT_INIT()
    
    # Définir vos outils
    tool_set = [
        {
            "name": "TOOL_1",
            "description": "Description de l'outil 1. Quand l'utiliser...",
            "input_schema": {
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "Description du paramètre 1"
                    },
                    "param2": {
                        "type": "number",
                        "description": "Description du paramètre 2"
                    }
                },
                "required": ["param1"]
            }
        },
        {
            "name": "TOOL_2",
            "description": "Description de l'outil 2...",
            "input_schema": {
                # ... schéma ...
            }
        },
        {
            "name": "TERMINATE_TASK",
            "description": "🎯 Terminer la tâche quand la mission est accomplie.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Raison de la terminaison"
                    },
                    "result_id": {
                        "type": "string",
                        "description": "ID du résultat trouvé (si applicable)"
                    },
                    "conclusion": {
                        "type": "string",
                        "description": "Votre réponse finale COMPLÈTE"
                    }
                },
                "required": ["reason", "conclusion"]
            }
        }
    ]
    
    # Définir le mapping
    tool_map = {
        "TOOL_1": self.my_tool_1_function,
        "TOOL_2": self.my_tool_2_function
        # TERMINATE_TASK n'est pas dans le mapping
    }
    
    # ... suite du workflow ...
```

### Étape 3 : Construire le premier message

```python
    # Obtenir le contexte
    available_data_summary = self.get_my_data_summary()
    
    # Enrichir la requête
    query_enriched = f"""{query}

*************************************************************************
CONTEXTE DES DONNÉES DISPONIBLES :
{available_data_summary}

*************************************************************************

INSTRUCTIONS TACTIQUES :
- [CONSEIL 1]
- [CONSEIL 2]
- [CONSEIL 3]
- Une fois [CONDITION] : utilisez TOOL_X
- Une fois la tâche terminée : utilisez TERMINATE_TASK
"""
```

### Étape 4 : Implémenter la boucle externe

```python
    # Boucle externe : ITÉRATIONS
    max_iterations = 3
    iteration = 0
    current_input = query_enriched
    
    while iteration < max_iterations:
        iteration += 1
        print(f"[MY_WORKFLOW] Itération {iteration}/{max_iterations}")
        
        # Appel du workflow interne
        success, status_code, report = self.my_agent.my_agent_workflow(
            manager_instance=self.my_agent,
            initial_query=current_input,
            tools=tool_set,
            tool_mapping=tool_map,
            size=ModelSize.SMALL,
            project_id=self.collection_id,
            job_id=self.sp_k,
            workflow_step=f'my_workflow_iter_{iteration}',
            max_turns=7,
            raw_output=True
        )
        
        print(f"[MY_WORKFLOW] Itération {iteration} - Status: {status_code}")
        
        # Vérifier si mission accomplie
        if status_code == "MISSION_COMPLETED":
            print(f"✓ Mission accomplie à l'itération {iteration}")
            self.audit.add_messages_ai_hu(f"Réponse : {report}")
            self.my_agent.flush_chat_history()
            return report
        
        # Préparer prochain input avec rapport
        if iteration < max_iterations:
            current_input = f"""╔═══════════════════════════════════════╗
║    RAPPORT DE L'ITÉRATION PRÉCÉDENTE  ║
╚═══════════════════════════════════════╝

{report}

╔═══════════════════════════════════════╗
║    RAPPEL DE LA MISSION INITIALE      ║
╚═══════════════════════════════════════╝

{query_enriched}

╔═══════════════════════════════════════╗
║           INSTRUCTIONS                ║
╚═══════════════════════════════════════╝

Il te reste {max_iterations - iteration} itération(s).
Continue ta recherche OU utilise TERMINATE_TASK si tu as terminé.
"""
    
    # Maximum d'itérations atteint
    print(f"[MY_WORKFLOW] Maximum d'itérations atteint ({max_iterations})")
    self.audit.add_messages_ai_hu(f"Réponse (max itérations): {report}")
    self.my_agent.flush_chat_history()
    
    return report
```

### Étape 5 : Créer le workflow interne

```python
# Dans langchain_tools.py, classe BaseAIAgent

def my_agent_workflow(self,
                     manager_instance: Any,
                     initial_query: str,
                     tools: List[Dict[str, Any]],
                     tool_mapping: Dict[str, Any],
                     size: ModelSize = ModelSize.SMALL,
                     provider: Optional[ModelProvider] = None,
                     max_tokens: int = 2048,
                     project_id: str = None,
                     job_id: str = None,
                     workflow_step: str = 'my_workflow',
                     max_turns: int = 7,
                     raw_output: bool = True) -> Tuple[bool, str, str]:
    """
    Workflow intelligent pour [VOTRE TÂCHE]
    """
    try:
        print(f"[MY_WORKFLOW] Démarrage - Tours max: {max_turns}")
        
        turn_count = 0
        user_input = initial_query
        next_user_input_parts = []
        
        while turn_count < max_turns:
            turn_count += 1
            print(f"[MY_WORKFLOW] Tour {turn_count}/{max_turns}")
            
            # Appeler l'agent
            ia_responses = manager_instance.process_tool_use(
                content=user_input,
                tools=tools,
                tool_mapping=tool_mapping,
                size=size,
                provider=provider,
                max_tokens=max_tokens,
                raw_output=raw_output
            )
            
            # Tracking
            if project_id and job_id:
                manager_instance.load_token_usage_to_db(
                    project_id=project_id,
                    job_id=job_id,
                    workflow_step=f"{workflow_step}_turn_{turn_count}"
                )
            
            # Normaliser
            if not isinstance(ia_responses, list):
                ia_responses = [ia_responses] if ia_responses else []
            
            next_user_input_parts = []
            
            # Traiter les réponses
            for response_block in ia_responses:
                if not isinstance(response_block, dict):
                    next_user_input_parts.append(f"Réponse inattendue: {str(response_block)[:200]}")
                    continue
                
                # TOOL_OUTPUT
                if "tool_output" in response_block:
                    tool_block = response_block["tool_output"]
                    tool_name = tool_block.get('tool_name', 'UnknownTool')
                    tool_content = tool_block.get('content', '')
                    
                    print(f"  [MY_WORKFLOW] Outil appelé: {tool_name}")
                    
                    # DÉTECTION TERMINATE_TASK
                    if tool_name == 'TERMINATE_TASK':
                        reason = tool_content.get('reason', 'Non spécifié') if isinstance(tool_content, dict) else "Terminaison demandée"
                        conclusion = tool_content.get('conclusion', str(tool_content)) if isinstance(tool_content, dict) else str(tool_content)
                        
                        print(f"[MY_WORKFLOW] ✓ TERMINATE_TASK - Raison: {reason}")
                        
                        # SORTIE IMMÉDIATE
                        return True, "MISSION_COMPLETED", conclusion
                    
                    # TOOL_1
                    elif tool_name == 'TOOL_1':
                        # Traiter le résultat de TOOL_1
                        next_user_input_parts.append(f"Résultat TOOL_1: {str(tool_content)[:500]}")
                    
                    # TOOL_2
                    elif tool_name == 'TOOL_2':
                        # Traiter le résultat de TOOL_2
                        next_user_input_parts.append(f"Résultat TOOL_2: {str(tool_content)[:500]}")
                    
                    # Autres outils
                    else:
                        next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                
                # TEXT_OUTPUT
                elif "text_output" in response_block:
                    text_block = response_block["text_output"]
                    extracted_text = "Pas de texte"
                    
                    if isinstance(text_block, dict) and "content" in text_block:
                        content = text_block["content"]
                        extracted_text = content.get('answer_text', str(content)) if isinstance(content, dict) else str(content)
                    elif isinstance(text_block, str):
                        extracted_text = text_block
                    
                    print(f"  [MY_WORKFLOW] Texte: {extracted_text[:200]}...")
                    next_user_input_parts.append(f"Texte précédent: {extracted_text[:300]}")
            
            # Préparer input pour prochain tour
            if next_user_input_parts:
                user_input = "\n".join(next_user_input_parts)
            else:
                print("[MY_WORKFLOW] Aucune réponse utilisable")
                return False, "NO_IA_ACTION", "L'IA n'a pas fourni de réponse claire."
        
        # Max tours atteint
        print(f"[MY_WORKFLOW] Maximum de {max_turns} tours atteint")
        summary = f"Maximum de {max_turns} tours atteint. Dernier état: {user_input[:500]}"
        return False, "MAX_TURNS_REACHED", summary
        
    except Exception as e:
        import traceback
        print(f"[MY_WORKFLOW] ERREUR FATALE: {e}")
        traceback.print_exc()
        return False, "ERROR_FATAL", f"Erreur: {str(e)}"
```

### Étape 6 : Implémenter les fonctions d'outils

```python
# Dans votre classe principale

def my_tool_1_function(self, param1, param2=None):
    """
    Implémentation de TOOL_1
    """
    try:
        # Logique de votre outil
        result = self.process_tool_1(param1, param2)
        
        # Retourner résultat structuré
        return {
            'type': 'success',
            'data': result,
            'message': 'Traitement réussi'
        }
    
    except Exception as e:
        return {
            'type': 'error',
            'message': str(e)
        }

def my_tool_2_function(self, param_a):
    """
    Implémentation de TOOL_2
    """
    try:
        # Logique de votre outil
        result = self.process_tool_2(param_a)
        
        return {
            'type': 'success',
            'data': result
        }
    
    except Exception as e:
        return {
            'type': 'error',
            'message': str(e)
        }
```

---

## 📊 CONFIGURATION ET PARAMÉTRAGE

### Paramètres configurables

#### Dans la boucle externe

```python
# Nombre d'itérations majeures
max_iterations = 3  # Ajustez selon la complexité de la tâche

# Message de reprise personnalisé
current_input = f"""╔{'═'*60}╗
║ RAPPORT DE L'ITÉRATION PRÉCÉDENTE
╚{'═'*60}╝

{report}

╔{'═'*60}╗
║ RAPPEL DE LA MISSION
╚{'═'*60}╝

{query_enriched}

╔{'═'*60}╗
║ INSTRUCTIONS
╚{'═'*60}╝

[VOS INSTRUCTIONS PERSONNALISÉES]
Il te reste {max_iterations - iteration} itération(s).
"""
```

#### Dans le workflow interne

```python
# Appel du workflow
success, status_code, report = self.my_agent.my_agent_workflow(
    manager_instance=self.my_agent,
    initial_query=current_input,
    tools=tool_set,
    tool_mapping=tool_map,
    
    # PARAMÈTRES AJUSTABLES :
    size=ModelSize.SMALL,       # SMALL / MEDIUM / LARGE
    provider=ModelProvider.ANTHROPIC,  # Optionnel
    max_tokens=2048,            # Limite de tokens
    max_turns=7,                # Nombre de tours par itération
    raw_output=True             # Format de sortie
)
```

#### Taille des modèles

```python
ModelSize.SMALL       # Tâches simples, rapides, peu coûteuses
ModelSize.MEDIUM      # Tâches moyennes, équilibre coût/performance
ModelSize.LARGE       # Tâches complexes, haute qualité
```

**Recommandations** :
- Workflow de filtrage simple → SMALL
- Analyse de documents → MEDIUM
- Raisonnement complexe → LARGE

#### Nombre de tours

```python
max_turns = 7  # Par défaut

# Ajustez selon :
# - Complexité de la tâche
# - Nombre d'outils disponibles
# - Budget de tokens
```

**Exemples** :
- Recherche simple (1-2 outils) → 5 tours
- Recherche avec vision (3-4 outils) → 7 tours
- Workflow complexe (5+ outils) → 10 tours

---

## 🎨 PERSONNALISATION DU WORKFLOW

### Ajouter un nouvel outil

1. **Définir le schéma** :
   ```python
   {
       "name": "MON_NOUVEL_OUTIL",
       "description": "Description claire de l'outil et quand l'utiliser",
       "input_schema": {
           "type": "object",
           "properties": {
               "param": {
                   "type": "string",
                   "description": "Description du paramètre"
               }
           },
           "required": ["param"]
       }
   }
   ```

2. **Implémenter la fonction** :
   ```python
   def mon_nouvel_outil(self, param):
       try:
           # Logique
           result = self.traitement(param)
           return {'type': 'success', 'data': result}
       except Exception as e:
           return {'type': 'error', 'message': str(e)}
   ```

3. **Ajouter au mapping** :
   ```python
   tool_map = {
       # ... autres outils ...
       "MON_NOUVEL_OUTIL": self.mon_nouvel_outil
   }
   ```

4. **Gérer dans le workflow** :
   ```python
   elif tool_name == 'MON_NOUVEL_OUTIL':
       # Traitement spécifique si nécessaire
       next_user_input_parts.append(f"Résultat: {str(tool_content)[:500]}")
   ```

### Créer une condition de sortie personnalisée

```python
# Dans le workflow interne
if tool_name == 'MON_OUTIL_SPECIAL':
    # Vérifier une condition
    if tool_content.get('special_flag') == True:
        print("[MY_WORKFLOW] Condition spéciale détectée")
        return True, "SPECIAL_EXIT", tool_content.get('message')
```

### Ajouter du logging avancé

```python
import logging

# Configurer le logger
logger = logging.getLogger('my_workflow')
logger.setLevel(logging.DEBUG)

# Dans le workflow
logger.debug(f"Tour {turn_count}: user_input = {user_input[:100]}")
logger.info(f"Outil appelé: {tool_name}")
logger.warning(f"Aucune réponse utilisable au tour {turn_count}")
logger.error(f"Erreur: {e}")
```

### Sauvegarder l'état du workflow

```python
# Dans le workflow interne
workflow_state = {
    'turn': turn_count,
    'status': 'in_progress',
    'last_tool': tool_name,
    'timestamp': datetime.now().isoformat()
}

# Sauvegarder dans Firebase ou fichier
self.firebase_instance.update_workflow_state(
    project_id=project_id,
    job_id=job_id,
    state=workflow_state
)
```

---

## 🐛 DEBUGGING ET TROUBLESHOOTING

### Problèmes courants

#### 1. L'agent n'utilise pas les outils

**Symptômes** :
- Uniquement des `text_output`
- Aucun `tool_output` dans les réponses

**Causes possibles** :
- Descriptions d'outils peu claires
- System prompt manque d'exemples
- Nom d'outil non mentionné dans le prompt

**Solutions** :
```python
# Améliorer les descriptions d'outils
"description": "🔍 UTILISEZ CET OUTIL POUR [ACTION PRÉCISE]. Exemple : [EXEMPLE D'UTILISATION]"

# Ajouter des exemples dans le prompt système
prompt += """
EXEMPLES D'UTILISATION DES OUTILS :

Situation : Rechercher une note de frais de 50€
Action : Utiliser GET_EXPENSES_INFO avec amount_range: {min: 48, max: 52}

Situation : Doute sur le fournisseur d'une note
Action : Utiliser VIEW_EXPENSE_DOCUMENT avec expense_job_id et question appropriée
"""
```

#### 2. Boucle infinie sans terminaison

**Symptômes** :
- Max tours toujours atteint
- L'agent répète les mêmes actions

**Causes possibles** :
- Outil TERMINATE non décrit clairement
- Critères de terminaison ambigus
- L'agent ne comprend pas quand s'arrêter

**Solutions** :
```python
# Renforcer les instructions de terminaison dans le prompt
prompt += """
⚠️ IMPORTANT - TERMINAISON :
Vous DEVEZ utiliser TERMINATE_TASK dans les cas suivants :
1. Vous avez trouvé le résultat demandé ET récupéré toutes les infos nécessaires
2. Vous avez épuisé toutes les options de recherche sans succès
3. Vous avez détecté une impossibilité de résoudre la tâche

NE CONTINUEZ PAS à itérer si vous avez déjà la réponse !
"""

# Ajouter un rappel à chaque tour
if turn_count >= max_turns - 2:
    user_input += f"\n\n⚠️ ATTENTION : Plus que {max_turns - turn_count} tour(s) restant(s). Si tu as la réponse, utilise TERMINATE_TASK MAINTENANT !"
```

#### 3. Erreurs d'exécution d'outils

**Symptômes** :
- Exceptions lors de l'appel des fonctions mappées
- Outputs avec `type: 'error'`

**Causes possibles** :
- Paramètres incorrects passés par l'agent
- Fonction mappée n'accepte pas les paramètres
- Erreur dans la logique de la fonction

**Solutions** :
```python
# Ajouter validation des paramètres
def my_tool_function(self, param1, param2=None):
    try:
        # Validation
        if not param1:
            return {'type': 'error', 'message': 'param1 est requis'}
        
        if param2 and not isinstance(param2, int):
            return {'type': 'error', 'message': 'param2 doit être un entier'}
        
        # Logique
        result = self.process(param1, param2)
        return {'type': 'success', 'data': result}
    
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] {error_trace}")
        return {
            'type': 'error',
            'message': f"Erreur dans my_tool: {str(e)}"
        }
```

#### 4. Consommation excessive de tokens

**Symptômes** :
- Coûts élevés
- Réponses très longues

**Causes possibles** :
- Historique de conversation trop long
- Outputs d'outils verbeux
- Prompt système trop long

**Solutions** :
```python
# Limiter la taille des outputs réinjectés
next_user_input_parts.append(f"Résultat: {str(tool_content)[:300]}")  # Limiter à 300 caractères

# Flush l'historique entre itérations
if iteration < max_iterations:
    self.my_agent.flush_chat_history()

# Réduire max_tokens
max_tokens=1024  # Au lieu de 2048

# Résumer les résultats volumineux
if len(tool_content) > 1000:
    tool_content = self.summarize(tool_content)
```

#### 5. Réponses incohérentes

**Symptômes** :
- L'agent oublie des informations précédentes
- Contradictions entre tours

**Causes possibles** :
- Historique mal géré
- Context switching du modèle
- Flush prématuré de l'historique

**Solutions** :
```python
# Vérifier que l'historique est maintenu DANS le workflow
# NE PAS flush pendant les tours, SEULEMENT entre itérations

# Ajouter un résumé de contexte
if turn_count > 1:
    user_input = f"""RAPPEL DU CONTEXTE :
- Tu as déjà appelé : {', '.join(tools_called)}
- Dernière info importante : {last_important_info}

NOUVELLE INFORMATION :
{user_input}
"""
```

### Outils de debugging

#### 1. Logging détaillé

```python
import json

# Dans le workflow
print(f"\n{'='*80}")
print(f"[DEBUG] Tour {turn_count}/{max_turns}")
print(f"[DEBUG] Input:")
print(json.dumps(user_input, indent=2, ensure_ascii=False)[:500])
print(f"[DEBUG] Réponse:")
print(json.dumps(ia_responses, indent=2, ensure_ascii=False)[:500])
print(f"{'='*80}\n")
```

#### 2. Sauvegarder les traces

```python
# Sauvegarder chaque tour dans un fichier
debug_log = {
    'iteration': iteration,
    'turn': turn_count,
    'input': user_input,
    'output': ia_responses,
    'tools_called': tools_called,
    'timestamp': datetime.now().isoformat()
}

with open(f'debug_trace_{project_id}_{job_id}.jsonl', 'a') as f:
    f.write(json.dumps(debug_log) + '\n')
```

#### 3. Mode dry-run

```python
# Ajouter un paramètre dry_run
def my_agent_workflow(self, ..., dry_run=False):
    if dry_run:
        print("[DRY RUN] Simulation sans appel réel aux modèles")
        return True, "DRY_RUN_SUCCESS", "Simulation réussie"
    
    # Workflow normal
    ...
```

---

## 📈 MÉTRIQUES ET SUIVI DE PERFORMANCE

### Tracking des tokens

```python
# Après chaque tour
if project_id and job_id:
    manager_instance.load_token_usage_to_db(
        project_id=project_id,
        job_id=job_id,
        workflow_step=f"{workflow_step}_turn_{turn_count}"
    )

# Récupérer les statistiques
token_stats = self.token_manager.get_usage_stats(project_id, job_id)
print(f"Tokens totaux : {token_stats['total_tokens']}")
print(f"Coût estimé : {token_stats['total_cost']}")
```

### Métriques de performance

```python
import time

# Au début du workflow
start_time = time.time()
start_iteration_time = time.time()

# Après chaque tour
turn_duration = time.time() - start_iteration_time
print(f"[PERF] Tour {turn_count} : {turn_duration:.2f}s")

# À la fin
total_duration = time.time() - start_time
print(f"[PERF] Durée totale : {total_duration:.2f}s")
print(f"[PERF] Moyenne par tour : {total_duration / turn_count:.2f}s")
```

### Taux de réussite

```python
# Tracker les succès
workflow_stats = {
    'total_runs': 0,
    'mission_completed': 0,
    'max_turns_reached': 0,
    'errors': 0
}

# Après chaque run
if status_code == "MISSION_COMPLETED":
    workflow_stats['mission_completed'] += 1
elif status_code == "MAX_TURNS_REACHED":
    workflow_stats['max_turns_reached'] += 1
else:
    workflow_stats['errors'] += 1

workflow_stats['total_runs'] += 1

# Calculer le taux
success_rate = workflow_stats['mission_completed'] / workflow_stats['total_runs'] * 100
print(f"[STATS] Taux de réussite : {success_rate:.1f}%")
```

---

## 🎓 BONNES PRATIQUES

### 1. Design du System Prompt

✅ **À FAIRE** :
- Structurer clairement (sections, bullet points)
- Donner des exemples concrets
- Expliquer le "pourquoi" pas seulement le "comment"
- Inclure des avertissements pour les pièges courants
- Utiliser des emojis pour attirer l'attention (🎯, ⚠️, ✅)

❌ **À ÉVITER** :
- Prompts trop longs (>4000 mots)
- Instructions contradictoires
- Jargon technique sans explication
- Trop de détails inutiles

### 2. Conception des outils

✅ **À FAIRE** :
- Noms d'outils descriptifs (GET_X, SEARCH_Y, UPDATE_Z)
- Descriptions détaillées avec cas d'usage
- Paramètres optionnels avec valeurs par défaut
- Retours structurés cohérents

❌ **À ÉVITER** :
- Outils trop génériques (PROCESS_DATA)
- Descriptions vagues ("Traite les données")
- Trop de paramètres obligatoires
- Retours inconsistants

### 3. Gestion du contexte

✅ **À FAIRE** :
- Maintenir l'historique pendant les tours
- Flush entre itérations pour optimiser tokens
- Résumer les outputs volumineux
- Injecter uniquement les infos pertinentes

❌ **À ÉVITER** :
- Garder l'historique complet indéfiniment
- Réinjecter des infos déjà traitées
- Perdre le contexte en cours de workflow

### 4. Conditions de terminaison

✅ **À FAIRE** :
- Outil de terminaison explicite (TERMINATE_X)
- Multiples conditions de sortie possibles
- Instructions claires sur quand terminer
- Rapport final structuré et complet

❌ **À ÉVITER** :
- Compter uniquement sur max_turns
- Conditions de terminaison ambiguës
- Sortie sans rapport final

### 5. Gestion d'erreurs

✅ **À FAIRE** :
- Try/except sur tous les outils
- Messages d'erreur informatifs
- Logging des exceptions
- Retours structurés même en cas d'erreur

❌ **À ÉVITER** :
- Laisser les exceptions remonter
- Messages d'erreur génériques
- Pas de trace des erreurs

---

## 📚 EXEMPLES D'UTILISATION

### Exemple 1 : Workflow de validation de factures

```python
def INVOICE_VALIDATION_INIT(self):
    prompt = """Vous êtes un agent de validation de factures.
    
    MISSION : Vérifier la conformité des factures fournisseurs.
    
    OUTILS :
    - GET_INVOICE_DATA : Récupérer les données d'une facture
    - CHECK_SUPPLIER : Vérifier l'existence du fournisseur
    - VERIFY_AMOUNTS : Vérifier la cohérence des montants
    - VALIDATE_TAX : Valider les calculs de TVA
    - TERMINATE_VALIDATION : Conclure la validation
    
    WORKFLOW :
    1. Récupérer les données de la facture
    2. Vérifier le fournisseur
    3. Vérifier les montants et TVA
    4. Conclure avec TERMINATE_VALIDATION
    """
    self.invoice_validator.update_system_prompt(prompt)

def VALIDATE_INVOICE(self, invoice_id):
    self.INVOICE_VALIDATION_INIT()
    
    tool_set = [
        {
            "name": "GET_INVOICE_DATA",
            "description": "Récupère les données complètes d'une facture",
            "input_schema": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"}
                },
                "required": ["invoice_id"]
            }
        },
        # ... autres outils ...
        {
            "name": "TERMINATE_VALIDATION",
            "description": "Terminer avec le verdict de validation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "valid": {"type": "boolean"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                    "conclusion": {"type": "string"}
                },
                "required": ["valid", "conclusion"]
            }
        }
    ]
    
    tool_map = {
        "GET_INVOICE_DATA": self.get_invoice_data,
        "CHECK_SUPPLIER": self.check_supplier,
        "VERIFY_AMOUNTS": self.verify_amounts,
        "VALIDATE_TAX": self.validate_tax
    }
    
    query = f"Valide la facture {invoice_id}. Vérifie tous les aspects."
    
    # Boucle externe (1 seule itération suffit généralement)
    success, status, report = self.invoice_validator.invoice_validation_workflow(
        manager_instance=self.invoice_validator,
        initial_query=query,
        tools=tool_set,
        tool_mapping=tool_map,
        max_turns=5
    )
    
    return report
```

### Exemple 2 : Workflow de recherche documentaire

```python
def DOCUMENT_SEARCH_INIT(self):
    prompt = """Vous êtes un agent de recherche documentaire.
    
    MISSION : Trouver des documents pertinents selon des critères.
    
    STRATÉGIE :
    1. Commencer large avec SEARCH_BY_KEYWORD
    2. Affiner avec FILTER_BY_DATE si trop de résultats
    3. Si doute, utiliser READ_DOCUMENT_PREVIEW
    4. Terminer avec TERMINATE_SEARCH quand trouvé
    """
    self.doc_searcher.update_system_prompt(prompt)

def SEARCH_DOCUMENTS(self, query):
    self.DOCUMENT_SEARCH_INIT()
    
    tool_set = [
        {
            "name": "SEARCH_BY_KEYWORD",
            "description": "Recherche par mots-clés",
            "input_schema": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["keywords"]
            }
        },
        {
            "name": "FILTER_BY_DATE",
            "description": "Filtre par plage de dates",
            "input_schema": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"}
                },
                "required": []
            }
        },
        {
            "name": "READ_DOCUMENT_PREVIEW",
            "description": "Lit un aperçu d'un document",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"}
                },
                "required": ["doc_id"]
            }
        },
        {
            "name": "TERMINATE_SEARCH",
            "description": "Termine avec la liste des documents trouvés",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_ids": {"type": "array", "items": {"type": "string"}},
                    "conclusion": {"type": "string"}
                },
                "required": ["doc_ids", "conclusion"]
            }
        }
    ]
    
    tool_map = {
        "SEARCH_BY_KEYWORD": self.search_by_keyword,
        "FILTER_BY_DATE": self.filter_by_date,
        "READ_DOCUMENT_PREVIEW": self.read_preview
    }
    
    # Boucle avec 2 itérations max
    max_iterations = 2
    iteration = 0
    current_input = query
    
    while iteration < max_iterations:
        iteration += 1
        
        success, status, report = self.doc_searcher.document_search_workflow(
            manager_instance=self.doc_searcher,
            initial_query=current_input,
            tools=tool_set,
            tool_mapping=tool_map,
            max_turns=6
        )
        
        if status == "MISSION_COMPLETED":
            return report
        
        if iteration < max_iterations:
            current_input = f"""Itération précédente : {report}
            
Rappel : {query}

Affine ta recherche ou termine si tu as trouvé."""
    
    return report
```

---

## 🔐 SÉCURITÉ ET VALIDATION

### Validation des inputs

```python
def my_tool_function(self, user_input):
    # Validation des inputs
    if not user_input:
        return {'type': 'error', 'message': 'Input vide'}
    
    if len(user_input) > 10000:
        return {'type': 'error', 'message': 'Input trop long'}
    
    # Nettoyer les inputs dangereux
    cleaned_input = self.sanitize_input(user_input)
    
    # Continuer le traitement
    ...
```

### Limite de ressources

```python
# Limiter la durée d'exécution
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Temps d'exécution dépassé")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(300)  # 5 minutes max

try:
    result = self.long_running_process()
finally:
    signal.alarm(0)  # Annuler l'alarme
```

### Gestion des secrets

```python
# Ne jamais inclure de secrets dans les prompts
prompt = f"""...
Utilisez l'API avec la clé fournie dans la configuration.
NE PAS afficher la clé dans vos réponses.
"""

# Charger depuis variables d'environnement
import os
api_key = os.getenv('MY_API_KEY')
```

---

## 🚀 OPTIMISATIONS AVANCÉES

### Cache des résultats

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_expensive_data(data_id):
    # Calcul coûteux
    return expensive_computation(data_id)
```

### Parallélisation des outils

```python
import asyncio

async def call_tools_parallel(self, tools_to_call):
    tasks = [
        self.call_tool_async(tool_name, params)
        for tool_name, params in tools_to_call
    ]
    results = await asyncio.gather(*tasks)
    return results
```

### Compression de l'historique

```python
def compress_history(self, history):
    """
    Compresse l'historique en gardant seulement les infos clés
    """
    compressed = []
    for message in history[-10:]:  # Garder 10 derniers messages
        if message['role'] == 'tool':
            # Résumer les outputs d'outils
            compressed.append({
                'role': 'tool',
                'name': message['name'],
                'summary': message['content'][:200]  # Tronquer
            })
        else:
            compressed.append(message)
    return compressed
```

---

## 📖 GLOSSAIRE

- **Agent exécutant** : Instance d'IA qui effectue les actions et appelle les outils
- **Agent planificateur** : (Optionnel) Agent qui supervise et guide l'exécutant
- **Boucle externe** : Boucle d'itérations majeures avec gestion de résumés
- **Boucle interne** : Boucle de tours au sein d'une itération
- **Tour (turn)** : Un échange question/réponse avec l'agent dans le workflow
- **Itération** : Une exécution complète du workflow interne (plusieurs tours)
- **Tool mapping** : Dictionnaire liant les noms d'outils aux fonctions
- **Tool output** : Résultat de l'exécution d'un outil
- **Text output** : Réponse textuelle de l'agent sans appel d'outil
- **TERMINATE_SEARCH** : Outil spécial de terminaison de mission
- **Flush** : Vidage de l'historique de conversation
- **raw_output** : Format brut de sortie (liste de dictionnaires)
- **status_code** : Code indiquant l'état de fin du workflow

---

## 📝 CHECKLIST D'IMPLÉMENTATION

Avant de déployer votre workflow agentic, vérifiez :

### Agents
- [ ] System prompt clair et structuré
- [ ] Exemples d'utilisation inclus
- [ ] Critères de succès définis
- [ ] Instructions de terminaison explicites

### Outils
- [ ] Tous les outils ont des descriptions détaillées
- [ ] Schémas JSON complets et valides
- [ ] Fonctions implémentées et testées
- [ ] Tool mapping correct
- [ ] Gestion d'erreurs dans chaque outil
- [ ] Outil TERMINATE_TASK présent

### Workflow
- [ ] Boucle externe implémentée
- [ ] Boucle interne implémentée
- [ ] Premier message enrichi avec contexte
- [ ] Gestion des tool_output
- [ ] Gestion des text_output
- [ ] Détection de TERMINATE_TASK
- [ ] Génération de résumé si MAX_TURNS_REACHED
- [ ] Insertion du résumé dans itération suivante

### Tracking
- [ ] Tracking des tokens configuré
- [ ] Logging des étapes principales
- [ ] Métriques de performance
- [ ] Audit des décisions

### Tests
- [ ] Test de cas nominal (succès au premier tour)
- [ ] Test de cas avec plusieurs tours
- [ ] Test de dépassement MAX_TURNS
- [ ] Test de dépassement MAX_ITERATIONS
- [ ] Test de gestion d'erreurs
- [ ] Test avec différents providers
- [ ] Test avec différentes tailles de modèles

### Documentation
- [ ] README du workflow
- [ ] Exemples d'utilisation
- [ ] Guide de troubleshooting
- [ ] Métriques de référence

---

## 🎉 CONCLUSION

Ce framework de workflow agentic offre une architecture robuste et flexible pour implémenter des agents IA autonomes capables d'exécuter des tâches complexes de manière itérative.

### Points clés à retenir

1. **Architecture à deux niveaux** : Boucle externe (itérations) + Boucle interne (tours)
2. **Contexte maintenu** : L'historique de conversation persiste entre tours
3. **Terminaison contrôlée** : Outil dédié pour signaler la fin de mission
4. **Résilience** : En cas de dépassement, résumé généré et réinjection
5. **Tracking complet** : Tokens, performances, audit

### Avantages de cette approche

- ✅ **Autonomie** : L'agent décide quels outils utiliser
- ✅ **Flexibilité** : Ajout facile de nouveaux outils
- ✅ **Traçabilité** : Chaque action est loggée et trackée
- ✅ **Optimisation** : Flush de l'historique entre itérations
- ✅ **Réutilisabilité** : Framework applicable à de nombreux cas d'usage

### Prochaines étapes

1. Adapter ce template à votre cas d'usage
2. Définir vos outils spécifiques
3. Écrire un system prompt de qualité
4. Tester avec différents scénarios
5. Optimiser les paramètres (max_turns, max_iterations)
6. Monitorer les performances en production

---

**Version** : 1.0  
**Date** : 2025  
**Auteur** : Framework Agentic Team  
**Licence** : Interne

---

## 📞 SUPPORT

Pour toute question ou amélioration de ce framework :
- Consultez les exemples dans `tools/pybank.py` et `tools/langchain_tools.py`
- Référez-vous aux logs pour le debugging
- Testez avec le mode `dry_run` avant déploiement

**Bonne implémentation ! 🚀**

