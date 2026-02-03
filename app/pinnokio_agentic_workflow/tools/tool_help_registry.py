"""
ToolHelpRegistry - Registre centralisé pour la documentation détaillée des outils.

Ce module permet de :
1. Séparer les définitions courtes (envoyées à l'API) de la documentation détaillée
2. Fournir un outil GET_TOOL_HELP dynamique qui ne documente que les outils chargés
3. Réduire significativement les tokens consommés par les définitions d'outils (~74%)

Usage:
    registry = ToolHelpRegistry()
    registry.register("GET_APBOOKEEPER_JOBS", detailed_help_text)
    
    # L'outil GET_TOOL_HELP est créé dynamiquement avec la liste des outils disponibles
    tool_def, handler = registry.create_get_tool_help()
"""

import logging
from typing import Dict, List, Any, Tuple, Callable, Set

logger = logging.getLogger("pinnokio.tool_help_registry")


class ToolHelpRegistry:
    """
    Registre centralisé pour la documentation détaillée des outils.
    
    Permet de :
    - Enregistrer la documentation détaillée de chaque outil
    - Créer dynamiquement l'outil GET_TOOL_HELP
    - Limiter l'accès à la doc des seuls outils chargés (sécurité)
    """
    
    def __init__(self):
        self._detailed_help: Dict[str, str] = {}
        self._available_tools: Set[str] = set()
        logger.debug("[TOOL_HELP_REGISTRY] Registre initialisé")
    
    def register(self, tool_name: str, detailed_help: str) -> None:
        """
        Enregistre la documentation détaillée d'un outil.
        
        Args:
            tool_name: Nom de l'outil (ex: "GET_APBOOKEEPER_JOBS")
            detailed_help: Documentation complète de l'outil
        """
        self._detailed_help[tool_name] = detailed_help
        self._available_tools.add(tool_name)
        logger.debug(f"[TOOL_HELP_REGISTRY] Enregistré: {tool_name}")
    
    def register_multiple(self, tools_help: Dict[str, str]) -> None:
        """
        Enregistre la documentation de plusieurs outils à la fois.
        
        Args:
            tools_help: Dict {tool_name: detailed_help}
        """
        for tool_name, detailed_help in tools_help.items():
            self.register(tool_name, detailed_help)
    
    def get_help(self, tool_names: List[str]) -> Dict[str, Any]:
        """
        Retourne la documentation détaillée des outils demandés.
        
        Sécurité : Ne retourne que les docs des outils disponibles.
        
        Args:
            tool_names: Liste des noms d'outils
            
        Returns:
            Dict avec la doc de chaque outil ou message d'erreur
        """
        result = {}
        valid_tools = []
        invalid_tools = []
        
        for name in tool_names:
            if name in self._available_tools:
                result[name] = self._detailed_help.get(name, "Documentation non disponible")
                valid_tools.append(name)
            else:
                result[name] = f"❌ Outil '{name}' non disponible. Utilisez un des outils listés."
                invalid_tools.append(name)
        
        if invalid_tools:
            result["_available_tools"] = sorted(list(self._available_tools))
            logger.warning(f"[TOOL_HELP] Outils non trouvés: {invalid_tools}")
        
        logger.info(f"[TOOL_HELP] Documentation fournie pour: {valid_tools}")
        return result
    
    def get_available_tools(self) -> List[str]:
        """Retourne la liste des outils disponibles (triée)."""
        return sorted(list(self._available_tools))
    
    def create_get_tool_help(self) -> Tuple[Dict[str, Any], Callable]:
        """
        Crée l'outil GET_TOOL_HELP avec sa définition et son handler.
        
        L'outil est créé dynamiquement avec la liste des outils disponibles
        dans sa description, garantissant que l'agent ne peut demander
        l'aide que des outils réellement chargés.
        
        Returns:
            Tuple (tool_definition, async_handler)
        """
        available_tools_list = self.get_available_tools()
        tools_list_str = ", ".join(available_tools_list) if available_tools_list else "Aucun outil enregistré"
        
        tool_definition = {
            "name": "GET_TOOL_HELP",
            "description": f"""📚 Documentation détaillée des outils disponibles.

Utilisez cet outil pour obtenir des exemples d'utilisation, les paramètres détaillés, 
et les cas d'usage spécifiques d'un ou plusieurs outils.

**Outils disponibles** : {tools_list_str}

⚠️ Vous ne pouvez demander l'aide que des outils listés ci-dessus.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des noms d'outils pour lesquels obtenir la documentation détaillée"
                    }
                },
                "required": ["tool_names"]
            }
        }
        
        # Créer le handler async
        async def handle_get_tool_help(tool_names: List[str] = None, **kwargs) -> Dict[str, Any]:
            """Handler pour l'outil GET_TOOL_HELP."""
            if not tool_names:
                tool_names = kwargs.get("tool_names", [])
            
            if not tool_names:
                return {
                    "error": "Paramètre 'tool_names' requis",
                    "available_tools": self.get_available_tools()
                }
            
            return self.get_help(tool_names)
        
        logger.info(f"[TOOL_HELP_REGISTRY] GET_TOOL_HELP créé avec {len(available_tools_list)} outils disponibles")
        
        return tool_definition, handle_get_tool_help


# ═══════════════════════════════════════════════════════════════════════════════
# DOCUMENTATION DÉTAILLÉE DES OUTILS
# ═══════════════════════════════════════════════════════════════════════════════
# Ces constantes contiennent la documentation complète de chaque outil.
# Elles sont séparées des définitions courtes pour économiser des tokens.

DETAILED_HELP = {
    # ─────────────────────────────────────────────────────────────────────────────
    # OUTILS GET_JOBS (job_tools.py)
    # ─────────────────────────────────────────────────────────────────────────────
    
    "GET_APBOOKEEPER_JOBS": """
📋 **GET_APBOOKEEPER_JOBS** - Recherche des factures fournisseur (APBookkeeper)

## Rôle
Recherche et filtre les factures fournisseur par statut ou nom de fichier.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `status` | string | `to_do`, `in_process`, `pending`, `processed`, `all` (défaut: `to_do`) |
| `file_name_contains` | string | Recherche partielle dans le nom du fichier (case insensitive) |
| `limit` | integer | Nombre max de résultats (défaut: 50, max: 200) |

## Output
Chaque job retourné contient :
- `job_id` : ID unique du job (requis pour LPT_APBookkeeper)
- `drive_file_id` : ID Google Drive (pour VIEW_DRIVE_DOCUMENT) 🔍
- `uri_drive_link` : Lien direct vers le document
- `file_name` : Nom du fichier
- `status` : Statut actuel
- `timestamp` : Date de création

## Exemples d'utilisation

**1. Lister les factures à traiter :**
```json
{"status": "to_do"}
```

**2. Rechercher une facture spécifique :**
```json
{"file_name_contains": "facture_orange", "status": "all"}
```

**3. Voir les factures en cours :**
```json
{"status": "in_process"}
```

## Workflow typique
1. `GET_APBOOKEEPER_JOBS(status="to_do")` → Obtenir la liste
2. `VIEW_DRIVE_DOCUMENT(file_id=drive_file_id)` → Voir un document
3. `LPT_APBookkeeper(job_ids=[...])` → Lancer le traitement

⚠️ **Important** : Utilisez `drive_file_id` avec VIEW_DRIVE_DOCUMENT pour visualiser.
""",

    "GET_ROUTER_JOBS": """
🗂️ **GET_ROUTER_JOBS** - Recherche des documents à router

## Rôle
Recherche et filtre les documents en attente de routage vers les départements.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `status` | string | `to_process`, `in_process`, `all` (défaut: `to_process`) |
| `file_name_contains` | string | Recherche partielle dans le nom du fichier |
| `limit` | integer | Nombre max de résultats (défaut: 50, max: 200) |

## Output
Chaque job retourné contient :
- `job_id` : ID unique du job (requis pour LPT_Router)
- `drive_file_id` : ID Google Drive (pour VIEW_DRIVE_DOCUMENT) 🔍
- `uri_drive_link` : Lien direct vers le document
- `file_name` : Nom du fichier
- `status` : Statut actuel (`to_process`, `in_process`)

## Exemples d'utilisation

**1. Lister les documents à router :**
```json
{"status": "to_process"}
```

**2. Rechercher un document spécifique :**
```json
{"file_name_contains": "contrat", "status": "all"}
```

## Workflow typique
1. `GET_ROUTER_JOBS(status="to_process")` → Obtenir la liste
2. `VIEW_DRIVE_DOCUMENT(file_id=drive_file_id)` → Analyser un document
3. `LPT_Router(job_ids=[...])` → Lancer le routage automatique
""",

    "GET_BANK_TRANSACTIONS": """
🏦 **GET_BANK_TRANSACTIONS** - Recherche des transactions bancaires

## Rôle
Recherche et filtre les transactions bancaires par compte, statut, montant ou date.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `status` | string | `to_reconcile`, `in_process`, `pending`, `reconciled`, `all` |
| `journal_id` | string | ID du compte bancaire (journal) pour filtrer |
| `min_amount` | number | Montant minimum |
| `max_amount` | number | Montant maximum |
| `date_from` | string | Date début (YYYY-MM-DD) |
| `date_to` | string | Date fin (YYYY-MM-DD) |
| `partner_contains` | string | Recherche dans le nom du partenaire |
| `limit` | integer | Nombre max de résultats (défaut: 50) |

## Output
Chaque transaction retournée contient :
- `transaction_id` : ID unique
- `journal_id` : ID du compte bancaire
- `amount` : Montant de la transaction
- `date` : Date de la transaction
- `partner_name` : Nom du partenaire
- `reference` : Référence/libellé
- `status` : Statut de réconciliation

## Exemples d'utilisation

**1. Transactions à réconcilier :**
```json
{"status": "to_reconcile"}
```

**2. Transactions d'un compte spécifique :**
```json
{"journal_id": "bank_001", "status": "all"}
```

**3. Grosses transactions récentes :**
```json
{"min_amount": 1000, "date_from": "2024-01-01"}
```

## Workflow typique
1. `GET_BANK_TRANSACTIONS(status="to_reconcile")` → Obtenir la liste
2. `LPT_Banker(transaction_ids=[...])` → Lancer la réconciliation
""",

    "GET_EXPENSES_INFO": """
💰 **GET_EXPENSES_INFO** - Recherche des notes de frais

## Rôle
Recherche et filtre les notes de frais par statut, date, montant, fournisseur ou méthode de paiement.

## Statuts des notes de frais
- **`open`** (statut "to_process") : Notes de frais **non saisies en comptabilité**. Elles doivent généralement être réconciliées avec une transaction bancaire correspondante. Ce sont les notes de frais en attente de traitement comptable.
- **`closed`** (statut "close") : Notes de frais **déjà comptabilisées en comptabilité**. Elles ont été traitées et enregistrées dans les écritures comptables.

## ⚠️ IMPORTANT - Notes de frais à rembourser
Si une note de frais représente un remboursement à un employé ou à une personne (frais professionnels remboursables), elle doit être traitée comme une **facture fournisseur** et passer par le processus des factures fournisseurs (APBookkeeper) plutôt que comme une simple note de frais.

**Workflow recommandé pour les notes de frais à rembourser** :
1. Identifier que la note de frais est un remboursement (via GET_EXPENSES_INFO et VIEW_DRIVE_DOCUMENT si nécessaire)
2. Expliquer à l'utilisateur que ce type de note doit être saisie comme facture fournisseur
3. Recommander la mise à jour du contexte expenses pour clarifier cette règle
4. Guider l'utilisateur vers le processus APBookkeeper si nécessaire

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `status` | string | `open` (non saisies), `closed` (comptabilisées), `all` |
| `date_from` | string | Date début (YYYY-MM-DD) |
| `date_to` | string | Date fin (YYYY-MM-DD) |
| `amount_min` | number | Montant minimum |
| `amount_max` | number | Montant maximum |
| `supplier_contains` | string | Recherche dans le nom du fournisseur (case insensitive) |
| `payment_method` | string | Filtrer par méthode de paiement |
| `limit` | integer | Nombre max de résultats (défaut: 50, max: 200) |

## Output
Chaque expense retournée contient :
- `expense_id` : ID unique de la note de frais
- `drive_file_id` : ID du document Google Drive (pour visualisation via VIEW_DRIVE_DOCUMENT)
- `date` : Date de la note de frais
- `amount` : Montant
- `currency` : Devise (ex: CHF, EUR)
- `supplier` : Nom du fournisseur
- `status` : Statut ("to_process" ou "close")
- `concern` : Description/objet de la note de frais
- `payment_method` : Méthode de paiement
- `job_id` : ID du job associé (si applicable)
- `file_name` : Nom du fichier

## Exemples d'utilisation

**1. Notes de frais ouvertes (non saisies) :**
```json
{"status": "open"}
```

**2. Notes de frais d'un fournisseur spécifique :**
```json
{"supplier_contains": "Restaurant", "status": "all"}
```

**3. Notes de frais récentes de montant élevé :**
```json
{"date_from": "2025-01-01", "amount_min": 500, "status": "open"}
```

**4. Notes de frais fermées (comptabilisées) :**
```json
{"status": "closed", "date_from": "2024-01-01"}
```

## Accès aux documents
- Chaque expense contient un `drive_file_id` qui permet de visualiser le document via `VIEW_DRIVE_DOCUMENT`
- Utilisez `VIEW_DRIVE_DOCUMENT` avec le `drive_file_id` pour analyser le contenu de la note de frais en cas de doute

## Workflow typique
1. `GET_EXPENSES_INFO(status="open")` → Obtenir les notes de frais non saisies
2. `VIEW_DRIVE_DOCUMENT(file_id=drive_file_id)` → Analyser le document si nécessaire
3. Identifier si c'est un remboursement → Guider vers APBookkeeper si nécessaire
4. Réconcilier avec transaction bancaire si applicable
""",

    # ─────────────────────────────────────────────────────────────────────────────
    # OUTILS TASK_MANAGER (index + audit) — Solution A
    # ─────────────────────────────────────────────────────────────────────────────

    "GET_TASK_MANAGER_INDEX": """
📌 **GET_TASK_MANAGER_INDEX** - Index des travaux (task_manager) filtrable

## Rôle
Permet d’obtenir une **vue générale** des travaux exécutés (jobs) stockés dans Firestore selon le contrat Solution A :
- Index : `clients/{userId}/task_manager/{job_id}`

⚠️ **Sécurité/Contrat** :
- `mandate_path` est **imposé** côté outil (lu depuis le contexte utilisateur) et appliqué comme filtre obligatoire.
- `userId` est **imposé** (ID Firebase utilisateur courant).

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `department` | string | Filtre exact sur `department` (optionnel). ⚠️ **Casse sensible** : valeurs valides : `"banker"`, `"router"`, `"APbookeeper"` |
| `status_final` | string | Filtre exact sur `status_final` (optionnel) |
| `status` | string | Filtre exact sur `status` runtime (optionnel) |
| `last_outcome` | string | Filtre exact sur `last_outcome` (info\\|success\\|failure\\|pending) (optionnel) |
| `file_name_contains` | string | Filtre contains (case-insensitive) sur `file_name` (optionnel, filtré côté backend) |
| `started_from` | string | ISO 8601, filtre `started_at >=` (optionnel) |
| `started_to` | string | ISO 8601, filtre `started_at <=` (optionnel) |
| `limit` | integer | Nombre max (défaut: 50, max: 200) |
| `start_after_job_id` | string | Pagination: reprendre après ce job_id (optionnel) |
| `include_raw` | boolean | Si true, inclut le doc complet en plus des champs utiles (défaut: false) |

## Valeurs valides pour `department`
⚠️ **Important** : Le filtre est **exact** (==), la casse compte !
- `"banker"` (tout en minuscule)
- `"router"` (tout en minuscule)
- `"APbookeeper"` (A et P majuscules, reste en minuscule)

## Exemples d'utilisation

**1) Derniers travaux en erreur :**
```json
{"status_final":"error","limit":25}
```

**2) Travaux d'un département sur une période :**
```json
{"department":"router","started_from":"2025-12-01T00:00:00Z","started_to":"2025-12-20T23:59:59Z","limit":50}
```

**3) Travaux du département banker :**
```json
{"department":"banker","status_final":"completed","limit":50}
```

**4) Travaux APbookeeper en attente :**
```json
{"department":"APbookeeper","status":"pending","limit":100}
```

## Output
Retourne une liste d’items incluant `job_id`, `department`, `file_name`, `status`, `status_final`, `started_at`, `last_*`, et `department_data`.
""",

    "GET_TASK_MANAGER_DETAILS": """
🧾 **GET_TASK_MANAGER_DETAILS** - Détails d’un travail (index + timeline events)

## Rôle
Ouvre un job via `job_id` et retourne :
- l’index `clients/{userId}/task_manager/{job_id}`
- la timeline append-only `clients/{userId}/task_manager/{job_id}/events/*`

⚠️ **Sécurité/Contrat** :
- Accès refusé si le document ne porte pas le **même** `mandate_path` que celui du contexte utilisateur.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `job_id` | string | ID du job (requis) |
| `events_limit` | integer | Nombre max d’events (défaut: 100, max: 500) |
| `events_order` | string | `asc` ou `desc` (défaut: `asc`) |

## Exemples d'utilisation

**1) Ouvrir un job et lire les 100 derniers events :**
```json
{"job_id":"klk_...","events_limit":100,"events_order":"desc"}
```

## Output
- `job`: document d’index
- `events`: liste d’events (avec `event_id`) triés selon `events_order`
""",

    # ─────────────────────────────────────────────────────────────────────────────
    # OUTILS LPT (lpt_client.py)
    # ─────────────────────────────────────────────────────────────────────────────
    
    "LPT_APBookkeeper": """
📋 **LPT_APBookkeeper** - Traitement des factures fournisseur

## Rôle
Lance le traitement automatique des factures fournisseur sélectionnées.
C'est un outil LPT (Long Process Tooling) : l'exécution est asynchrone.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `job_ids` | array | Liste des IDs de jobs à traiter (obtenus via GET_APBOOKEEPER_JOBS) |
| `file_instructions` | object | Instructions spécifiques par fichier (optionnel) |

## Comportement
- **Approbation** : Configurée automatiquement selon `workflow_params`
- **Création contact** : Configurée automatiquement selon `workflow_params`
- **Callback** : Vous recevrez le résultat via WAIT_ON_LPT

## Exemple d'utilisation

```json
{
    "job_ids": ["job_abc123", "job_def456"],
    "file_instructions": {
        "job_abc123": "Attention : TVA à 5.5%"
    }
}
```

## Workflow complet
1. `GET_APBOOKEEPER_JOBS(status="to_do")` → Obtenir les job_ids
2. `LPT_APBookkeeper(job_ids=[...])` → Lancer le traitement
3. `WAIT_ON_LPT(...)` → Attendre le callback si en mode task_execution

⚠️ **Vérification solde** : Le système vérifie automatiquement le solde avant exécution.
""",

    "LPT_APBookkeeper_ALL": """
📋 **LPT_APBookkeeper_ALL** - Traitement de TOUTES les factures

## Rôle
Lance le traitement automatique de TOUTES les factures fournisseur en statut `to_do`.
Équivalent à GET_APBOOKEEPER_JOBS + LPT_APBookkeeper sur tous les résultats.

## Paramètres
Aucun paramètre requis - traite automatiquement toutes les factures disponibles.

## Exemple d'utilisation
```json
{}
```

## Comportement
- Récupère automatiquement toutes les factures `to_do`
- Vérifie le solde pour le nombre total de factures
- Lance le traitement en batch

⚠️ **Attention** : Peut traiter un grand nombre de factures. Vérifiez le solde.
""",

    "LPT_Router": """
🗂️ **LPT_Router** - Routage automatique des documents

## Rôle
Lance le routage automatique des documents vers les départements appropriés.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `job_ids` | array | Liste des IDs de jobs à router (obtenus via GET_ROUTER_JOBS) |
| `file_instructions` | object | Instructions spécifiques par fichier (optionnel) |

## Comportement
- **Approbation** : Configurée selon `workflow_params.Router_param`
- **Workflow automatisé** : Si activé, enchaîne vers APBookkeeper/Banker après routage
- **Callback** : Résultat via WAIT_ON_LPT

## Départements de destination
- `hr` : Ressources humaines
- `invoices` : Factures fournisseur → APBookkeeper
- `expenses` : Notes de frais
- `banks_cash` : Documents bancaires → Banker
- `taxes` : Documents fiscaux
- `contrats` : Contrats
- `letters` : Courriers
- `financial_statement` : États financiers

## Exemple d'utilisation
```json
{
    "job_ids": ["job_router_001", "job_router_002"]
}
```
""",

    "LPT_Router_ALL": """
🗂️ **LPT_Router_ALL** - Routage de TOUS les documents

## Rôle
Lance le routage automatique de TOUS les documents en statut `to_process`.

## Paramètres
Aucun paramètre requis.

## Exemple
```json
{}
```
""",

    "LPT_Banker": """
🏦 **LPT_Banker** - Réconciliation bancaire

## Rôle
Lance la réconciliation automatique des transactions bancaires.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `transaction_ids` | array | Liste des IDs de transactions (obtenus via GET_BANK_TRANSACTIONS) |
| `journal_id` | string | ID du compte bancaire (optionnel, pour filtrer) |

## Comportement
- **Approbation** : Configurée selon `workflow_params.Banker_param`
- **Seuil d'approbation** : Transactions au-dessus du seuil nécessitent approbation
- **Callback** : Résultat via WAIT_ON_LPT

## Exemple d'utilisation
```json
{
    "transaction_ids": ["tx_001", "tx_002", "tx_003"]
}
```
""",

    "LPT_Banker_ALL": """
🏦 **LPT_Banker_ALL** - Réconciliation de TOUTES les transactions

## Rôle
Lance la réconciliation de TOUTES les transactions en statut `to_reconcile`.

## Paramètres
Aucun paramètre requis.

## Exemple
```json
{}
```
""",

    "LPT_STOP_APBookkeeper": """
🛑 **LPT_STOP_APBookkeeper** - Arrêter le traitement APBookkeeper

## Rôle
Arrête le traitement en cours des factures fournisseur.

## Paramètres
Aucun paramètre requis.

## Utilisation
```json
{}
```

⚠️ **Note** : Les factures déjà traitées ne sont pas annulées.
""",

    "LPT_STOP_Router": """
🛑 **LPT_STOP_Router** - Arrêter le routage

## Rôle
Arrête le routage en cours des documents.

## Paramètres
Aucun paramètre requis.
""",

    "LPT_STOP_Banker": """
🛑 **LPT_STOP_Banker** - Arrêter la réconciliation

## Rôle
Arrête la réconciliation bancaire en cours.

## Paramètres
Aucun paramètre requis.
""",

    # ─────────────────────────────────────────────────────────────────────────────
    # OUTILS SPT (spt_tools.py)
    # ─────────────────────────────────────────────────────────────────────────────
    
    "GET_FIREBASE_DATA": """
🔍 **GET_FIREBASE_DATA** - Accès aux données Firebase

## Rôle
Récupère des données depuis Firebase Firestore.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `path` | string | Chemin Firebase (ex: `clients/{uid}/notifications`) |
| `query_filters` | object | Filtres optionnels pour la requête |

## Placeholders disponibles
- `{uid}` : Remplacé par l'ID utilisateur courant
- `{collection}` : Remplacé par l'ID de la société courante

## Exemple d'utilisation
```json
{
    "path": "clients/{uid}/notifications",
    "query_filters": {"read": false}
}
```
""",

    "SEARCH_CHROMADB": """
🔎 **SEARCH_CHROMADB** - Recherche sémantique

## Rôle
Effectue une recherche sémantique dans la base de connaissances ChromaDB.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `query` | string | Requête de recherche en langage naturel |
| `n_results` | integer | Nombre de résultats (défaut: 5) |

## Exemple d'utilisation
```json
{
    "query": "Comment comptabiliser une facture d'électricité ?",
    "n_results": 3
}
```
""",

    # ─────────────────────────────────────────────────────────────────────────────
    # OUTILS VISION & CONTEXTE
    # ─────────────────────────────────────────────────────────────────────────────
    
    "VIEW_DRIVE_DOCUMENT": """
🖼️ **VIEW_DRIVE_DOCUMENT** - Visualiser un document Google Drive

## Rôle
Permet de voir et analyser le contenu d'un document (PDF, image, facture).

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `file_id` | string | ID Google Drive du document (⚠️ REQUIS) |
| `question` | string | Question spécifique sur le document (optionnel) |

## ⚠️ WORKFLOW OBLIGATOIRE

**ÉTAPE 1** : Récupérer le `drive_file_id` avec :
- `GET_APBOOKEEPER_JOBS` → pour les factures
- `GET_ROUTER_JOBS` → pour les documents à router

**ÉTAPE 2** : Utiliser VIEW_DRIVE_DOCUMENT avec ce `drive_file_id`

## Exemple d'utilisation
```json
{
    "file_id": "1A2B3C4D5E6F7G8H9I0J",
    "question": "Quel est le montant total de cette facture ?"
}
```

❌ **NE PAS** inventer ou deviner un file_id !
❌ **NE PAS** utiliser un nom de fichier comme file_id !
""",

    "ROUTER_PROMPT": """
📋 **ROUTER_PROMPT** - Règles de classification des documents

## Rôle
Récupère les règles de classification par département configurées pour la société.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `service` | string | Département spécifique (optionnel) |

## Services disponibles
- `hr` : Ressources humaines
- `invoices` : Factures fournisseur
- `expenses` : Notes de frais
- `banks_cash` : Documents bancaires
- `taxes` : Documents fiscaux
- `contrats` : Contrats
- `letters` : Courriers
- `financial_statement` : États financiers

## Exemple
```json
{"service": "invoices"}
```
""",

    "APBOOKEEPER_CONTEXT": """
📒 **APBOOKEEPER_CONTEXT** - Contexte comptable

## Rôle
Récupère les règles comptables et le plan comptable de la société.

## Paramètres
Aucun paramètre requis.

## Contenu retourné
- Plan comptable personnalisé
- Règles de TVA
- Règles de rapprochement
- Configurations ERP
""",

    "COMPANY_CONTEXT": """
🏢 **COMPANY_CONTEXT** - Profil complet de la société

## Rôle
Récupère toutes les informations de la société cliente.

## Paramètres
Aucun paramètre requis.

## Contenu retourné
- Informations légales (nom, SIRET, adresse)
- Configuration ERP
- Paramètres de workflow
- Contacts
""",

    "UPDATE_CONTEXT": """
✏️ **UPDATE_CONTEXT** - Modifier un contexte avec précision chirurgicale

## Rôle
Permet de modifier un contexte (Router, APBookkeeper, Bank, Company) en utilisant des **ANCRES** pour localiser précisément la zone à modifier.

## 🎯 CONCEPT DES ANCRES

Les ancres fonctionnent comme des coordonnées GPS dans le texte :
- `anchor_before` : 12+ caractères QUI PRÉCÈDENT la zone à modifier
- `anchor_after` : 12+ caractères QUI SUIVENT la zone à modifier
- La zone cible est **ENTRE** les deux ancres

```
Texte: "...On lui passe toujours context..."
                    ↑        ↑
              anchor_before  anchor_after
              "On lui passe" "context..."
              
Zone cible = " toujours " (entre les ancres)
```

## Paramètres principaux
| Paramètre | Type | Description |
|-----------|------|-------------|
| `context_type` | string | `router`, `accounting`, `bank`, ou `company` |
| `service_name` | string | Nom du service (OBLIGATOIRE si `context_type=router`) |
| `operations` | array | Liste d'opérations avec ancres |
| `preview_only` | boolean | Si `true`, prévisualisation uniquement |

## Structure d'une opération

| Champ | Type | Description |
|-------|------|-------------|
| `operation` | string | `"add"`, `"replace"`, ou `"delete"` |
| `anchor_before` | string | 12+ caractères AVANT la zone (null = début du texte) |
| `anchor_after` | string | 12+ caractères APRÈS la zone (null = fin du texte) |
| `new_content` | string | Nouveau contenu (pour add/replace) |

## 📋 MATRICE DES CAS

| anchor_before | anchor_after | Zone ciblée |
|---------------|--------------|-------------|
| `null` | présent | Du DÉBUT jusqu'à anchor_after |
| présent | `null` | De anchor_before jusqu'à la FIN |
| présent | présent | ENTRE les deux ancres |
| `null` | `null` | Ajoute au début (pour add) |

## Exemples

### Exemple 1 : Remplacer une phrase précise
Texte original : "Convention de nommage recommandée : [TYPE]_[DATE]. Règles de classification..."

```json
{
    "context_type": "router",
    "service_name": "letters",
    "operations": [
        {
            "operation": "replace",
            "anchor_before": "recommandée : ",
            "anchor_after": ". Règles de",
            "new_content": "[DATE]_[OBJET]_[SOURCE]"
        }
    ]
}
```
Résultat : "Convention de nommage recommandée : [DATE]_[OBJET]_[SOURCE]. Règles de classification..."

### Exemple 2 : Ajouter au début du texte
```json
{
    "context_type": "router",
    "service_name": "hr",
    "operations": [
        {
            "operation": "add",
            "anchor_before": null,
            "anchor_after": null,
            "new_content": "⚠️ MISE À JOUR 2026 : "
        }
    ]
}
```

### Exemple 3 : Ajouter après une section
```json
{
    "context_type": "router",
    "service_name": "invoices",
    "operations": [
        {
            "operation": "add",
            "anchor_before": "fin de la section.",
            "anchor_after": null,
            "new_content": "\\n\\nNouvelle section ajoutée ici."
        }
    ]
}
```

### Exemple 4 : Supprimer une portion
```json
{
    "context_type": "accounting",
    "operations": [
        {
            "operation": "delete",
            "anchor_before": "Texte avant ",
            "anchor_after": " texte après"
        }
    ]
}
```

## ⚠️ BONNES PRATIQUES

1. **Ancres de 12+ caractères** : Plus c'est long, plus c'est unique
2. **Utilisez ROUTER_PROMPT** d'abord pour voir le texte complet
3. **Copiez les ancres exactement** depuis le texte affiché
4. **Testez avec preview_only=true** avant de valider

⚠️ **Attention** : Les modifications sont persistantes après approbation.
""",

    # ─────────────────────────────────────────────────────────────────────────────
    # OUTILS TASK EXECUTION (task_tools.py)
    # ─────────────────────────────────────────────────────────────────────────────
    
    "CREATE_CHECKLIST": """
📝 **CREATE_CHECKLIST** - Créer une checklist de workflow

## Rôle
Crée une checklist pour suivre l'avancement d'une mission complexe.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `steps` | array | Liste des étapes avec `id`, `description`, `status` |

## Exemple
```json
{
    "steps": [
        {"id": "STEP_1", "description": "Récupérer les factures", "status": "pending"},
        {"id": "STEP_2", "description": "Lancer le traitement", "status": "pending"},
        {"id": "STEP_3", "description": "Vérifier les résultats", "status": "pending"}
    ]
}
```

## Statuts possibles
- `pending` : En attente
- `in_progress` : En cours
- `completed` : Terminé
- `failed` : Échoué
""",

    "UPDATE_STEP": """
✅ **UPDATE_STEP** - Mettre à jour une étape

## Rôle
Met à jour le statut ou la description d'une étape de la checklist.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `step_id` | string | ID de l'étape (ex: "STEP_1") |
| `status` | string | Nouveau statut |
| `note` | string | Note optionnelle |

## Exemple
```json
{
    "step_id": "STEP_2",
    "status": "completed",
    "note": "5 factures traitées avec succès"
}
```
""",

    "WAIT_ON_LPT": """
⏳ **WAIT_ON_LPT** - Attendre un callback LPT

## Rôle
Met le workflow en pause en attendant le retour d'un outil LPT.
Utilisé en mode `task_execution` pour les processus longs.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `reason` | string | Raison de l'attente |
| `expected_lpt` | string | Nom de l'outil LPT attendu |
| `step_waiting` | string | ID de l'étape en attente |
| `task_ids` | array | Liste des IDs de tâches en cours |

## Exemple
```json
{
    "reason": "Attente du retour de LPT_APBookkeeper pour 5 factures",
    "expected_lpt": "LPT_APBookkeeper",
    "step_waiting": "STEP_2_SAISIE",
    "task_ids": ["job_abc123", "job_def456"]
}
```

## Comportement
- Le workflow se met en pause proprement
- À la réception du callback, vous serez réactivé
- Vous recevrez le résultat et pourrez continuer
""",

    "TERMINATE_TASK": """
🏁 **TERMINATE_TASK** - Terminer la mission

## Rôle
Termine la mission en cours avec un résumé des actions effectuées.

## ⚠️ CONDITIONS D'UTILISATION

Utilisez cet outil **UNIQUEMENT** quand :
1. ✅ TOUTES les étapes de la checklist sont `completed`
2. ✅ Aucun LPT n'est en attente de callback
3. ✅ L'objectif de la mission est atteint

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `summary` | string | Résumé structuré des actions effectuées |
| `status` | string | `success`, `partial`, ou `failed` |

## Exemple
```json
{
    "summary": "Mission terminée : 10 factures traitées, 2 en erreur",
    "status": "partial"
}
```

❌ **Si des étapes ne sont pas completed** : L'appel sera REFUSÉ.
""",

    "DETERMINE_TIMEZONE": """
🕐 **DETERMINE_TIMEZONE** - Configurer le fuseau horaire

## Rôle
Détermine et configure le fuseau horaire de la société pour les tâches planifiées.

## Paramètres
| Paramètre | Type | Description |
|-----------|------|-------------|
| `country` | string | Code pays (ex: "FR", "US", "CH") |

## Exemple
```json
{"country": "FR"}
```

## Note
Nécessaire pour les tâches SCHEDULED, ONE_TIME et ON_DEMAND.
""",
}

