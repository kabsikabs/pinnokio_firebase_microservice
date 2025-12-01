# 🧾 Architecture Agent Comptable `accounting_chat`

> **Version** : 1.0  
> **Date** : 29 Novembre 2025  
> **Auteur** : Pinnokio AI  
> **Statut** : En attente de validation

---

## 📋 Table des Matières

1. [Vue d'ensemble](#vue-densemble)
2. [Architecture Technique](#architecture-technique)
3. [Schéma du Journal Pinnokio](#schéma-du-journal-pinnokio)
4. [Module d'Extraction GL](#module-dextraction-gl)
5. [Gestionnaire DuckDB](#gestionnaire-duckdb)
6. [Flux de Transfert d'Agent](#flux-de-transfert-dagent)
7. [Outils de l'Agent](#outils-de-lagent)
8. [Synchronisation Incrémentale](#synchronisation-incrémentale)
9. [Points de Conformité](#points-de-conformité)
10. [Planning d'Implémentation](#planning-dimplémentation)
11. [Références Code Existant](#références-code-existant)

---

## 1. Vue d'ensemble

### 1.1 Objectif

L'agent `accounting_chat` est un agent spécialisé dans la gestion comptable, accessible depuis l'agent principal `general_chat`. Il permet de :

- **Consulter** les journaux comptables normalisés au format Pinnokio
- **Passer des écritures** comptables avec système d'approbation
- **Analyser** les mouvements via requêtes DuckDB
- **Accéder** aux livres de tiers (clients, fournisseurs)

### 1.2 Positionnement dans l'Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PINNOKIO BRAIN                              │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                      general_chat                              │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐│ │
│  │  │ SPT Tools   │  │ LPT Client  │  │ TRANSFER_TO_ACCOUNTING  ││ │
│  │  └─────────────┘  └─────────────┘  └───────────┬─────────────┘│ │
│  └───────────────────────────────────────────────│───────────────┘ │
│                                                   │                 │
│                                                   ▼                 │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                    accounting_chat                             │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │ │
│  │  │ Accounting SPT  │  │ GL Extractor    │  │ DuckDB Manager│  │ │
│  │  └─────────────────┘  └─────────────────┘  └───────────────┘  │ │
│  │                                                                │ │
│  │  ┌─────────────────────────────────────────────────────────┐  │ │
│  │  │ Outils: QUERY | CREATE_ENTRY | PARTNER_LEDGER | CLOSE   │  │ │
│  │  └─────────────────────────────────────────────────────────┘  │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 Principe de Fonctionnement

| Aspect | Description |
|--------|-------------|
| **Mode d'accès** | Outil `TRANSFER_TO_ACCOUNTING` depuis `general_chat` |
| **Détection** | Sémantique : écritures, journal, comptabilité, grand livre, etc. |
| **Notification** | WebSocket vers frontend lors du changement de mode |
| **Terminaison** | Mot-clé `TERMINATE` ou outil `CLOSE_ACCOUNTING_SESSION` |
| **Mémoire** | Effacée au retour vers `general_chat` |
| **Synthèse** | Output obligatoire des actions effectuées |

---

## 2. Architecture Technique

### 2.1 Structure des Fichiers

```
firebase_microservice/app/pinnokio_agentic_workflow/
├── orchestrator/
│   ├── agent_modes.py                          # ✏️ MODIFIER
│   ├── system_prompt_accounting_agent.py       # 🆕 CRÉER
│   └── pinnokio_brain.py                       # ✏️ MODIFIER
│
├── tools/
│   ├── accounting_tools.py                     # 🆕 CRÉER
│   └── gl_extractor/                           # 🆕 CRÉER (module)
│       ├── __init__.py
│       ├── base_extractor.py                   # Interface abstraite
│       ├── odoo_extractor.py                   # Implémentation Odoo
│       ├── pinnokio_normalizer.py              # Normalisation
│       └── duckdb_manager.py                   # Stockage DuckDB
│
└── ARCHITECTURE_ACCOUNTING_AGENT.md            # 📄 Ce document
```

### 2.2 Dépendances Requises

```python
# requirements.txt - Ajouts nécessaires
duckdb>=0.9.0              # Base de données analytique
pandas>=2.0.0              # Manipulation DataFrame (déjà présent)
xmlrpc.client              # Connexion Odoo (stdlib)
```

### 2.3 Configuration Environnement

```python
# Variables d'environnement optionnelles
ACCOUNTING_DUCKDB_PATH = "/tmp/accounting_{collection_name}.duckdb"
ACCOUNTING_SYNC_TTL_MINUTES = 15  # Durée de validité du cache
ACCOUNTING_MAX_ENTRIES_DISPLAY = 100  # Limite affichage
```

---

## 3. Schéma du Journal Pinnokio

### 3.1 Définition du Schéma Normalisé

Le format **Pinnokio** est le schéma normalisé vers lequel tous les ERP sont convertis.

```python
PINNOKIO_JOURNAL_SCHEMA = {
    # ═══════════════════════════════════════════════════════════════
    # IDENTIFIANTS
    # ═══════════════════════════════════════════════════════════════
    "id": {
        "type": "INTEGER",
        "description": "ID interne de la ligne dans l'ERP source",
        "source_odoo": "id"
    },
    "move_id": {
        "type": "INTEGER",
        "description": "ID de l'écriture comptable parente",
        "source_odoo": "move_id[0]"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # COMPTE COMPTABLE
    # ═══════════════════════════════════════════════════════════════
    "account_number": {
        "type": "VARCHAR",
        "description": "Numéro de compte (ex: '60100000', '41100000')",
        "source_odoo": "account_id → code via fetch_chart_of_account()"
    },
    "account_name": {
        "type": "VARCHAR",
        "description": "Libellé du compte",
        "source_odoo": "account_id[1]"
    },
    "account_id": {
        "type": "INTEGER",
        "description": "ID du compte dans l'ERP",
        "source_odoo": "account_id[0]"
    },
    "account_type": {
        "type": "VARCHAR",
        "description": "Type normalisé du compte",
        "enum": [
            "asset_current",           # Actif circulant
            "asset_non_current",       # Actif immobilisé
            "asset_prepayments",       # Charges constatées d'avance
            "liability_current",       # Passif court terme
            "liability_non_current",   # Passif long terme
            "equity",                  # Capitaux propres
            "income",                  # Produits
            "income_other",            # Autres produits
            "expense",                 # Charges
            "expense_depreciation",    # Amortissements
            "expense_direct_cost",     # Coûts directs
            "off_balance"              # Hors bilan
        ],
        "source_odoo": "account_type (transformé via ModelManager)"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # JOURNAL COMPTABLE
    # ═══════════════════════════════════════════════════════════════
    "journal_code": {
        "type": "VARCHAR",
        "description": "Code du journal (ex: 'VT', 'AC', 'BNQ')",
        "source_odoo": "journal_id → code"
    },
    "journal_name": {
        "type": "VARCHAR",
        "description": "Nom complet du journal",
        "source_odoo": "journal_id[1]"
    },
    "journal_id": {
        "type": "INTEGER",
        "description": "ID du journal",
        "source_odoo": "journal_id[0]"
    },
    "journal_type": {
        "type": "VARCHAR",
        "description": "Type de journal",
        "enum": ["sale", "purchase", "bank", "cash", "general"],
        "source_odoo": "journal_id → type via fetch_account_journal()"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # MONTANTS
    # ═══════════════════════════════════════════════════════════════
    "debit": {
        "type": "DECIMAL(15,2)",
        "description": "Montant au débit",
        "source_odoo": "debit"
    },
    "credit": {
        "type": "DECIMAL(15,2)",
        "description": "Montant au crédit",
        "source_odoo": "credit"
    },
    "balance": {
        "type": "DECIMAL(15,2)",
        "description": "Solde calculé (debit - credit)",
        "computed": True
    },
    "amount_currency": {
        "type": "DECIMAL(15,2)",
        "description": "Montant en devise étrangère (si applicable)",
        "source_odoo": "amount_currency"
    },
    "currency": {
        "type": "VARCHAR(3)",
        "description": "Code devise ISO (CHF, EUR, USD)",
        "source_odoo": "currency_id[1] ou company_currency"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # DATES
    # ═══════════════════════════════════════════════════════════════
    "date": {
        "type": "DATE",
        "description": "Date comptable de l'écriture",
        "source_odoo": "date"
    },
    "create_date": {
        "type": "TIMESTAMP",
        "description": "Date de création dans l'ERP",
        "source_odoo": "create_date"
    },
    "write_date": {
        "type": "TIMESTAMP",
        "description": "⭐ Date dernière modification (clé pour sync incrémentale)",
        "source_odoo": "write_date",
        "critical": True
    },
    
    # ═══════════════════════════════════════════════════════════════
    # RÉFÉRENCES ET LIBELLÉS
    # ═══════════════════════════════════════════════════════════════
    "name": {
        "type": "VARCHAR",
        "description": "Libellé de la ligne d'écriture",
        "source_odoo": "name"
    },
    "ref": {
        "type": "VARCHAR",
        "description": "Référence externe (n° facture, etc.)",
        "source_odoo": "ref"
    },
    "move_name": {
        "type": "VARCHAR",
        "description": "Numéro de pièce comptable",
        "source_odoo": "move_name"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # TIERS (PARTNER)
    # ═══════════════════════════════════════════════════════════════
    "partner_id": {
        "type": "INTEGER",
        "description": "ID du tiers (client/fournisseur)",
        "source_odoo": "partner_id[0]"
    },
    "partner_name": {
        "type": "VARCHAR",
        "description": "Nom du tiers",
        "source_odoo": "partner_id[1]"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # MÉTADONNÉES SYNC PINNOKIO
    # ═══════════════════════════════════════════════════════════════
    "erp_source": {
        "type": "VARCHAR",
        "description": "ERP d'origine",
        "enum": ["odoo", "sage", "quickbooks", "csv"],
        "pinnokio_field": True
    },
    "last_sync_at": {
        "type": "TIMESTAMP",
        "description": "⭐ Timestamp dernière synchronisation agent",
        "pinnokio_field": True,
        "critical": True
    },
    "sync_hash": {
        "type": "VARCHAR(64)",
        "description": "⭐ Hash SHA256 pour détection des changements",
        "pinnokio_field": True,
        "critical": True
    },
    "company_id": {
        "type": "INTEGER",
        "description": "ID de la société dans l'ERP",
        "source_odoo": "company_id[0]"
    }
}
```

### 3.2 Groupes de Comptes et Fonctions

```python
ACCOUNT_GROUPS = {
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 1 - CAPITAUX
    # ═══════════════════════════════════════════════════════════════
    "1": {
        "name": "Capitaux",
        "description": "Capital social, réserves, résultats",
        "account_types": ["equity", "liability_non_current"],
        "function": "Financement de l'entreprise et résultats accumulés"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 2 - IMMOBILISATIONS
    # ═══════════════════════════════════════════════════════════════
    "2": {
        "name": "Immobilisations",
        "description": "Actifs à long terme (bâtiments, machines, brevets)",
        "account_types": ["asset_non_current"],
        "function": "Investissements durables de l'entreprise"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 3 - STOCKS
    # ═══════════════════════════════════════════════════════════════
    "3": {
        "name": "Stocks et en-cours",
        "description": "Marchandises, matières premières, produits finis",
        "account_types": ["asset_current"],
        "function": "Actifs destinés à être vendus ou transformés"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 4 - TIERS
    # ═══════════════════════════════════════════════════════════════
    "4": {
        "name": "Tiers",
        "description": "Créances clients, dettes fournisseurs, État",
        "account_types": ["asset_current", "liability_current"],
        "subgroups": {
            "40": {"name": "Fournisseurs", "type": "liability_current"},
            "41": {"name": "Clients", "type": "asset_current"},
            "42": {"name": "Personnel", "type": "liability_current"},
            "43": {"name": "Sécurité sociale", "type": "liability_current"},
            "44": {"name": "État (TVA, impôts)", "type": "liability_current"},
            "45": {"name": "Groupe et associés", "type": "asset_current"},
            "46": {"name": "Débiteurs/Créditeurs divers", "type": "mixed"}
        },
        "function": "Relations financières avec les partenaires externes"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 5 - TRÉSORERIE
    # ═══════════════════════════════════════════════════════════════
    "5": {
        "name": "Trésorerie",
        "description": "Banques, caisse, valeurs mobilières",
        "account_types": ["asset_current"],
        "function": "Disponibilités et placements à court terme"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 6 - CHARGES
    # ═══════════════════════════════════════════════════════════════
    "6": {
        "name": "Charges",
        "description": "Achats, services, salaires, amortissements",
        "account_types": ["expense", "expense_depreciation", "expense_direct_cost"],
        "subgroups": {
            "60": {"name": "Achats", "type": "expense_direct_cost"},
            "61": {"name": "Services extérieurs", "type": "expense"},
            "62": {"name": "Autres services", "type": "expense"},
            "63": {"name": "Impôts et taxes", "type": "expense"},
            "64": {"name": "Charges de personnel", "type": "expense"},
            "65": {"name": "Autres charges", "type": "expense"},
            "66": {"name": "Charges financières", "type": "expense"},
            "67": {"name": "Charges exceptionnelles", "type": "expense"},
            "68": {"name": "Dotations amortissements", "type": "expense_depreciation"}
        },
        "function": "Consommations et dépenses de l'exercice"
    },
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSE 7 - PRODUITS
    # ═══════════════════════════════════════════════════════════════
    "7": {
        "name": "Produits",
        "description": "Ventes, prestations, produits financiers",
        "account_types": ["income", "income_other"],
        "subgroups": {
            "70": {"name": "Ventes de produits/services", "type": "income"},
            "71": {"name": "Production stockée", "type": "income"},
            "72": {"name": "Production immobilisée", "type": "income"},
            "74": {"name": "Subventions", "type": "income_other"},
            "75": {"name": "Autres produits", "type": "income_other"},
            "76": {"name": "Produits financiers", "type": "income_other"},
            "77": {"name": "Produits exceptionnels", "type": "income_other"}
        },
        "function": "Revenus et produits de l'exercice"
    }
}
```

---

## 4. Module d'Extraction GL

### 4.1 Interface Abstraite (`base_extractor.py`)

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd


class BaseGLExtractor(ABC):
    """
    Interface d'extraction du Grand Livre pour différents ERP.
    
    Chaque ERP (Odoo, Sage, QuickBooks, etc.) doit implémenter cette interface
    pour garantir une normalisation cohérente vers le format Pinnokio.
    """
    
    def __init__(self, connection_params: Dict[str, Any]):
        """
        Args:
            connection_params: Paramètres de connexion spécifiques à l'ERP
        """
        self.connection_params = connection_params
        self.connected = False
    
    @abstractmethod
    def connect(self) -> bool:
        """Établit la connexion à l'ERP."""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Ferme la connexion à l'ERP."""
        pass
    
    @abstractmethod
    def fetch_journal_entries(
        self, 
        last_sync_date: Optional[datetime] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Récupère les écritures comptables.
        
        Args:
            last_sync_date: Si fourni, récupère uniquement les modifications depuis cette date
            date_from: Date de début de la période
            date_to: Date de fin de la période
            
        Returns:
            DataFrame avec les colonnes du schéma Pinnokio (avant normalisation finale)
        """
        pass
    
    @abstractmethod
    def fetch_chart_of_accounts(self) -> pd.DataFrame:
        """
        Récupère le plan comptable complet.
        
        Returns:
            DataFrame avec: account_id, account_number, account_name, account_type
        """
        pass
    
    @abstractmethod
    def fetch_account_journals(self) -> List[Dict[str, Any]]:
        """
        Récupère les types de journaux comptables.
        
        Returns:
            Liste de dictionnaires: {id, code, name, type}
        """
        pass
    
    @abstractmethod
    def fetch_partners(self, partner_type: str = "all") -> pd.DataFrame:
        """
        Récupère la liste des tiers.
        
        Args:
            partner_type: "customer", "supplier", "all"
            
        Returns:
            DataFrame avec: partner_id, name, ref, vat, etc.
        """
        pass
    
    @abstractmethod
    def get_company_info(self) -> Dict[str, Any]:
        """
        Récupère les informations de la société.
        
        Returns:
            Dict avec: country, currency, vat, address, etc.
        """
        pass
    
    @abstractmethod
    def get_oldest_entry_date(self) -> Optional[datetime]:
        """Retourne la date de la plus ancienne écriture."""
        pass
    
    @abstractmethod
    def get_latest_modification_date(self) -> Optional[datetime]:
        """Retourne la date de la dernière modification d'écriture."""
        pass
```

### 4.2 Implémentation Odoo (`odoo_extractor.py`)

> **Source de référence** : `klk_router/tools/pyodoo.py` (classe `ODOO_KLK_VISION`)

```python
import xmlrpc.client
from datetime import datetime
from typing import Optional, List, Dict, Any
import pandas as pd

from .base_extractor import BaseGLExtractor


class OdooGLExtractor(BaseGLExtractor):
    """
    Extracteur GL pour Odoo.
    
    Basé sur la logique existante dans:
    - klk_router/tools/pyodoo.py (ODOO_KLK_VISION)
    - klk_router/tools/onboarding_manager.py (DF_ANALYSER)
    """
    
    def __init__(self, connection_params: Dict[str, Any]):
        """
        Args:
            connection_params: {
                "url": "https://odoo.example.com",
                "db": "database_name",
                "username": "user@example.com",
                "password": "api_key",
                "company_name": "Ma Société SA"
            }
        """
        super().__init__(connection_params)
        self.url = connection_params["url"]
        self.db = connection_params["db"]
        self.username = connection_params["username"]
        self.password = connection_params["password"]
        self.company_name = connection_params.get("company_name")
        
        self.uid = None
        self.models = None
        self.company_id = None
        
    def connect(self) -> bool:
        """Établit la connexion XML-RPC à Odoo."""
        try:
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            self.uid = common.authenticate(self.db, self.username, self.password, {})
            
            if not self.uid:
                raise ConnectionError("Authentification Odoo échouée")
            
            self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
            self.connected = True
            
            # Récupérer company_id
            self._resolve_company_id()
            
            return True
        except Exception as e:
            self.connected = False
            raise ConnectionError(f"Erreur connexion Odoo: {e}")
    
    def _execute_kw(self, model: str, method: str, args: list, kwargs: dict = None) -> Any:
        """Wrapper pour les appels XML-RPC."""
        return self.models.execute_kw(
            self.db, self.uid, self.password,
            model, method, args, kwargs or {}
        )
    
    def _resolve_company_id(self):
        """Résout l'ID de la société depuis son nom."""
        if self.company_name:
            companies = self._execute_kw(
                'res.company', 'search_read',
                [[['name', '=', self.company_name]]],
                {'fields': ['id', 'name']}
            )
            if companies:
                self.company_id = companies[0]['id']
    
    def fetch_journal_entries(
        self, 
        last_sync_date: Optional[datetime] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Récupère les écritures depuis account.move.line.
        
        ⭐ POINT CRITIQUE: Inclure write_date pour sync incrémentale
        """
        domain = []
        
        # Filtre société
        if self.company_id:
            domain.append(['company_id', '=', self.company_id])
        
        # Filtre sync incrémentale (basé sur write_date)
        if last_sync_date:
            domain.append(['write_date', '>', last_sync_date.isoformat()])
        
        # Filtres de période
        if date_from:
            domain.append(['date', '>=', date_from.strftime('%Y-%m-%d')])
        if date_to:
            domain.append(['date', '<=', date_to.strftime('%Y-%m-%d')])
        
        # Champs à récupérer (incluant write_date pour sync)
        fields = [
            'id', 'move_id', 'account_id', 'journal_id', 'partner_id',
            'name', 'ref', 'date', 'debit', 'credit', 'balance',
            'amount_currency', 'currency_id', 'company_id',
            'create_date', 'write_date',  # ⭐ Critique pour sync
            'move_name', 'account_type'
        ]
        
        records = self._execute_kw(
            'account.move.line', 'search_read',
            [domain],
            {'fields': fields}
        )
        
        return pd.DataFrame(records) if records else pd.DataFrame()
    
    def fetch_chart_of_accounts(self) -> pd.DataFrame:
        """Récupère le plan comptable."""
        domain = []
        if self.company_id:
            domain.append(['company_id', '=', self.company_id])
        
        records = self._execute_kw(
            'account.account', 'search_read',
            [domain],
            {'fields': ['id', 'code', 'name', 'account_type', 'reconcile']}
        )
        
        df = pd.DataFrame(records) if records else pd.DataFrame()
        
        # Renommage au format Pinnokio
        if not df.empty:
            df = df.rename(columns={
                'code': 'account_number',
                'name': 'account_name',
                'id': 'account_id'
            })
        
        return df
    
    def fetch_account_journals(self) -> List[Dict[str, Any]]:
        """Récupère les journaux comptables."""
        domain = []
        if self.company_id:
            domain.append(['company_id', '=', self.company_id])
        
        return self._execute_kw(
            'account.journal', 'search_read',
            [domain],
            {'fields': ['id', 'code', 'name', 'type']}
        ) or []
    
    # ... autres méthodes selon interface
```

### 4.3 Normaliseur Pinnokio (`pinnokio_normalizer.py`)

> **Source de référence** : `klk_router/tools/onboarding_manager.py` (méthode `expand_list_columns`)

```python
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd


class PinnokioNormalizer:
    """
    Normalise les données extraites au format Pinnokio.
    
    Transformations appliquées:
    1. Expansion des colonnes liste (Odoo [id, name] → colonnes séparées)
    2. Renommage des colonnes selon schéma Pinnokio
    3. Calcul des champs dérivés (balance, sync_hash)
    4. Ajout des métadonnées de sync
    """
    
    def __init__(self, erp_source: str = "odoo"):
        self.erp_source = erp_source
    
    def normalize(
        self, 
        df: pd.DataFrame, 
        chart_of_accounts: pd.DataFrame = None,
        journals: List[Dict] = None
    ) -> pd.DataFrame:
        """
        Normalise un DataFrame brut vers le format Pinnokio.
        
        Args:
            df: DataFrame brut depuis l'ERP
            chart_of_accounts: Plan comptable pour enrichissement
            journals: Liste des journaux pour enrichissement
        """
        if df.empty:
            return df
        
        # Étape 1: Expansion des colonnes liste
        df = self._expand_list_columns(df)
        
        # Étape 2: Enrichissement depuis plan comptable
        if chart_of_accounts is not None:
            df = self._enrich_from_coa(df, chart_of_accounts)
        
        # Étape 3: Enrichissement depuis journaux
        if journals:
            df = self._enrich_from_journals(df, journals)
        
        # Étape 4: Calcul des champs dérivés
        df = self._compute_derived_fields(df)
        
        # Étape 5: Ajout métadonnées sync
        df = self._add_sync_metadata(df)
        
        return df
    
    def _expand_list_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Transforme les colonnes Odoo [id, name] en colonnes séparées.
        
        Exemple:
            account_id: [42, "Clients"] 
            → account_id: 42, account_name: "Clients"
        """
        for col in df.columns:
            if df[col].apply(lambda x: isinstance(x, list)).any():
                # Créer colonne _name
                name_col = col.replace('_id', '') + '_name'
                
                # Extraire id et name
                df[col + "_temp"] = df[col].apply(
                    lambda x: x[0] if isinstance(x, list) and len(x) > 0 else None
                )
                df[name_col] = df[col].apply(
                    lambda x: x[1] if isinstance(x, list) and len(x) > 1 else None
                )
                
                # Remplacer colonne originale par id seul
                df[col] = df[col + "_temp"]
                df = df.drop(columns=[col + "_temp"])
        
        return df
    
    def _compute_derived_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcule les champs dérivés."""
        # Balance = debit - credit
        if 'debit' in df.columns and 'credit' in df.columns:
            df['balance'] = df['debit'] - df['credit']
        
        return df
    
    def _add_sync_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ajoute les métadonnées de synchronisation Pinnokio."""
        now = datetime.utcnow()
        
        df['erp_source'] = self.erp_source
        df['last_sync_at'] = now
        
        # Calcul hash pour détection changements
        df['sync_hash'] = df.apply(
            lambda row: self._compute_row_hash(row), axis=1
        )
        
        return df
    
    def _compute_row_hash(self, row: pd.Series) -> str:
        """Calcule un hash SHA256 pour détecter les changements."""
        # Colonnes clés pour le hash (exclure les métadonnées sync)
        key_cols = ['id', 'debit', 'credit', 'name', 'ref', 'write_date']
        
        hash_input = "|".join([
            str(row.get(col, "")) for col in key_cols if col in row.index
        ])
        
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]
```

---

## 5. Gestionnaire DuckDB

### 5.1 Structure (`duckdb_manager.py`)

```python
import duckdb
import pandas as pd
from typing import Optional, List, Dict, Any
from datetime import datetime
import os
import logging

logger = logging.getLogger("pinnokio.duckdb_manager")


class AccountingDuckDB:
    """
    Gestionnaire DuckDB pour stockage et requêtes analytiques.
    
    Avantages DuckDB:
    - Requêtes SQL analytiques ultra-rapides
    - Pas de serveur (fichier local)
    - Intégration native pandas
    - Support OLAP (agrégations, window functions)
    """
    
    def __init__(self, collection_name: str, base_path: str = "/tmp"):
        """
        Args:
            collection_name: Identifiant de la société (pour isolation)
            base_path: Répertoire de stockage des fichiers .duckdb
        """
        self.collection_name = collection_name
        self.db_path = os.path.join(base_path, f"accounting_{collection_name}.duckdb")
        self.conn = None
        
        self._connect()
        self._init_schema()
    
    def _connect(self):
        """Établit la connexion DuckDB."""
        self.conn = duckdb.connect(self.db_path)
        logger.info(f"DuckDB connecté: {self.db_path}")
    
    def _init_schema(self):
        """Crée les tables si inexistantes."""
        
        # Table principale: journal_entries
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY,
                move_id INTEGER,
                
                -- Compte
                account_id INTEGER,
                account_number VARCHAR,
                account_name VARCHAR,
                account_type VARCHAR,
                
                -- Journal
                journal_id INTEGER,
                journal_code VARCHAR,
                journal_name VARCHAR,
                journal_type VARCHAR,
                
                -- Montants
                debit DECIMAL(15,2),
                credit DECIMAL(15,2),
                balance DECIMAL(15,2),
                amount_currency DECIMAL(15,2),
                currency VARCHAR(3),
                
                -- Dates
                date DATE,
                create_date TIMESTAMP,
                write_date TIMESTAMP,
                
                -- Références
                name VARCHAR,
                ref VARCHAR,
                move_name VARCHAR,
                
                -- Tiers
                partner_id INTEGER,
                partner_name VARCHAR,
                
                -- Métadonnées sync
                erp_source VARCHAR,
                last_sync_at TIMESTAMP,
                sync_hash VARCHAR(64),
                company_id INTEGER
            )
        """)
        
        # Table: chart_of_accounts
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chart_of_accounts (
                account_id INTEGER PRIMARY KEY,
                account_number VARCHAR,
                account_name VARCHAR,
                account_type VARCHAR,
                reconcile BOOLEAN,
                last_sync_at TIMESTAMP
            )
        """)
        
        # Table: account_journals
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS account_journals (
                id INTEGER PRIMARY KEY,
                code VARCHAR,
                name VARCHAR,
                type VARCHAR,
                last_sync_at TIMESTAMP
            )
        """)
        
        # Table: sync_metadata (tracking des syncs)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_metadata (
                id INTEGER PRIMARY KEY,
                table_name VARCHAR,
                last_sync_at TIMESTAMP,
                entries_synced INTEGER,
                sync_type VARCHAR  -- 'full' ou 'incremental'
            )
        """)
        
        # Index pour performances
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_date ON journal_entries(date)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_account ON journal_entries(account_number)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_write_date ON journal_entries(write_date)
        """)
        
        logger.info("Schéma DuckDB initialisé")
    
    def upsert_entries(self, df: pd.DataFrame) -> int:
        """
        Insert ou update les écritures (basé sur id).
        
        Returns:
            Nombre d'entrées traitées
        """
        if df.empty:
            return 0
        
        # Supprimer les entrées existantes avec les mêmes IDs
        ids = df['id'].tolist()
        self.conn.execute(f"""
            DELETE FROM journal_entries WHERE id IN ({','.join(map(str, ids))})
        """)
        
        # Insérer les nouvelles/mises à jour
        self.conn.execute("""
            INSERT INTO journal_entries SELECT * FROM df
        """)
        
        return len(df)
    
    def get_last_sync_date(self) -> Optional[datetime]:
        """Retourne la date de dernière sync."""
        result = self.conn.execute("""
            SELECT MAX(last_sync_at) FROM journal_entries
        """).fetchone()
        
        return result[0] if result and result[0] else None
    
    def query(self, sql: str) -> pd.DataFrame:
        """
        Exécute une requête SQL et retourne un DataFrame.
        
        ⚠️ Sécurité: Cette méthode sera utilisée par l'agent.
        Validation du SQL nécessaire côté outil.
        """
        return self.conn.execute(sql).df()
    
    def get_balance_by_account(
        self, 
        date_from: str = None, 
        date_to: str = None,
        account_type: str = None
    ) -> pd.DataFrame:
        """Requête pré-construite: Balance par compte."""
        
        where_clauses = []
        if date_from:
            where_clauses.append(f"date >= '{date_from}'")
        if date_to:
            where_clauses.append(f"date <= '{date_to}'")
        if account_type:
            where_clauses.append(f"account_type = '{account_type}'")
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        return self.query(f"""
            SELECT 
                account_number,
                account_name,
                account_type,
                SUM(debit) as total_debit,
                SUM(credit) as total_credit,
                SUM(balance) as solde
            FROM journal_entries
            WHERE {where_sql}
            GROUP BY account_number, account_name, account_type
            ORDER BY account_number
        """)
    
    def get_partner_ledger(
        self, 
        partner_type: str = "all",
        date_from: str = None,
        date_to: str = None
    ) -> pd.DataFrame:
        """Requête pré-construite: Grand livre des tiers."""
        
        where_clauses = ["partner_id IS NOT NULL"]
        
        if partner_type == "customer":
            where_clauses.append("account_number LIKE '41%'")
        elif partner_type == "supplier":
            where_clauses.append("account_number LIKE '40%'")
        
        if date_from:
            where_clauses.append(f"date >= '{date_from}'")
        if date_to:
            where_clauses.append(f"date <= '{date_to}'")
        
        where_sql = " AND ".join(where_clauses)
        
        return self.query(f"""
            SELECT 
                partner_name,
                account_number,
                date,
                ref,
                name,
                debit,
                credit,
                balance
            FROM journal_entries
            WHERE {where_sql}
            ORDER BY partner_name, date
        """)
    
    def close(self):
        """Ferme la connexion."""
        if self.conn:
            self.conn.close()
            logger.info(f"DuckDB fermé: {self.db_path}")
```

---

## 6. Flux de Transfert d'Agent

### 6.1 Diagramme de Séquence

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │     │ general_chat │     │accounting_chat│     │   DuckDB     │
└──────┬───────┘     └──────┬───────┘     └──────┬────────┘     └──────┬───────┘
       │                    │                     │                     │
       │ "Montre-moi les    │                     │                     │
       │  écritures"        │                     │                     │
       │───────────────────>│                     │                     │
       │                    │                     │                     │
       │                    │ Détection intention │                     │
       │                    │ comptable           │                     │
       │                    │                     │                     │
       │                    │ TRANSFER_TO_ACCOUNTING                    │
       │                    │────────────────────>│                     │
       │                    │                     │                     │
       │<─ ─ ─ ─ ─ ─ ─ ─ ─ ─│ Notification WS     │                     │
       │  mode: accounting  │ (changement agent)  │                     │
       │                    │                     │                     │
       │                    │                     │ SYNC_JOURNAL        │
       │                    │                     │────────────────────>│
       │                    │                     │                     │
       │                    │                     │<────────────────────│
       │                    │                     │ DataFrame normalisé │
       │                    │                     │                     │
       │                    │                     │ QUERY_ACCOUNTING    │
       │                    │                     │────────────────────>│
       │                    │                     │                     │
       │<─────────────────────────────────────────│ Résultats           │
       │  Affichage écritures                     │                     │
       │                    │                     │                     │
       │ "TERMINATE" ou     │                     │                     │
       │ "C'est tout merci" │                     │                     │
       │───────────────────────────────────────-->│                     │
       │                    │                     │                     │
       │                    │ CLOSE_ACCOUNTING_SESSION                  │
       │                    │<────────────────────│                     │
       │                    │  + Synthèse actions │                     │
       │                    │  + Effacement mémoire                     │
       │                    │                     │                     │
       │<─ ─ ─ ─ ─ ─ ─ ─ ─ ─│ Notification WS     │                     │
       │  mode: general     │                     │                     │
       │                    │                     │                     │
```

### 6.2 Implémentation Outil de Transfert

```python
# Dans tools/accounting_tools.py

TRANSFER_TO_ACCOUNTING_TOOL = {
    "name": "TRANSFER_TO_ACCOUNTING",
    "description": """🔄 Transfère la conversation vers l'agent comptable spécialisé.

**Utilisez cet outil quand l'utilisateur demande** :
- Consultation du journal/grand livre
- Passation d'écritures comptables
- Analyse des mouvements par compte
- Livre des tiers (clients/fournisseurs)
- Questions sur les soldes comptables

**Ce qui se passe** :
1. Notification envoyée au frontend (changement de mode)
2. L'agent comptable prend le relais
3. Mémoire du chat général préservée
4. Retour automatique avec synthèse via TERMINATE ou CLOSE_ACCOUNTING_SESSION

**Exemple d'utilisation** :
- Utilisateur: "Je veux voir mes écritures de décembre"
- Agent: Appel TRANSFER_TO_ACCOUNTING avec context résumé""",
    "input_schema": {
        "type": "object",
        "properties": {
            "initial_request": {
                "type": "string",
                "description": "Résumé de la demande utilisateur à transmettre"
            },
            "context": {
                "type": "object",
                "description": "Contexte additionnel (période, comptes spécifiques, etc.)"
            }
        },
        "required": ["initial_request"]
    }
}
```

### 6.3 Outil de Clôture avec Synthèse

```python
CLOSE_ACCOUNTING_SESSION_TOOL = {
    "name": "CLOSE_ACCOUNTING_SESSION",
    "description": """🔚 Termine la session comptable et retourne vers l'agent général.

**Utilisez cet outil quand** :
- L'utilisateur dit "TERMINATE", "c'est tout", "merci", etc.
- Toutes les demandes comptables sont traitées
- L'utilisateur demande explicitement de revenir au mode général

**Output obligatoire** :
- Synthèse des actions effectuées
- Liste des écritures consultées/créées
- Statut des approbations en attente
- Recommandations éventuelles

**Comportement** :
1. Génère la synthèse
2. Efface la mémoire de l'agent comptable
3. Notifie le frontend du retour au mode général
4. Transmet la synthèse à l'agent général""",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Synthèse détaillée des actions effectuées"
            },
            "actions_performed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Liste des actions effectuées"
            },
            "pending_approvals": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Écritures en attente d'approbation"
            }
        },
        "required": ["summary"]
    }
}
```

---

## 7. Outils de l'Agent

### 7.1 Tableau Récapitulatif

| Outil | Type | Temps | Description |
|-------|------|-------|-------------|
| `QUERY_ACCOUNTING_DATA` | SPT | <5s | Requête SQL sur DuckDB |
| `GET_ACCOUNT_GROUPS` | SPT | <1s | Liste groupes/fonctions comptes |
| `SYNC_JOURNAL` | SPT/LPT | Variable | Sync incrémentale depuis ERP |
| `GET_PARTNER_LEDGER` | SPT | <5s | Grand livre des tiers |
| `GET_TRIAL_BALANCE` | SPT | <5s | Balance générale |
| `CREATE_JOURNAL_ENTRY` | LPT | Variable | Création écriture (approbation) |
| `CLOSE_ACCOUNTING_SESSION` | SPT | <1s | Retour mode général |

### 7.2 Définitions Détaillées

```python
ACCOUNTING_TOOLS = [
    {
        "name": "QUERY_ACCOUNTING_DATA",
        "description": """📊 Exécute une requête SQL sur le journal comptable.

**Base de données** : DuckDB avec le schéma Pinnokio normalisé

**Tables disponibles** :
- `journal_entries` : Écritures comptables
- `chart_of_accounts` : Plan comptable
- `account_journals` : Types de journaux

**Colonnes journal_entries** :
- id, move_id, date, account_number, account_name, account_type
- journal_code, journal_name, journal_type
- debit, credit, balance, currency
- partner_name, partner_id, name, ref

**Exemples de requêtes** :
1. Total par compte:
   SELECT account_number, account_name, SUM(balance) 
   FROM journal_entries GROUP BY 1,2

2. Écritures décembre:
   SELECT * FROM journal_entries 
   WHERE date >= '2024-12-01' AND date <= '2024-12-31'

3. Solde client:
   SELECT SUM(balance) FROM journal_entries 
   WHERE partner_name = 'Client XYZ'

⚠️ Limite 100 lignes par défaut. Utilisez LIMIT pour plus.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Requête SQL (SELECT uniquement)"
                },
                "limit": {
                    "type": "integer",
                    "default": 100,
                    "description": "Nombre max de résultats"
                }
            },
            "required": ["sql"]
        }
    },
    
    {
        "name": "GET_ACCOUNT_GROUPS",
        "description": """📚 Récupère les groupes de comptes avec leurs fonctions.

**Retourne** :
- Classe (1-7)
- Nom du groupe
- Types de comptes associés
- Fonction/rôle dans la comptabilité
- Sous-groupes éventuels

**Utilisation** :
- Expliquer la structure du plan comptable
- Identifier où classer une opération
- Comprendre les flux financiers""",
        "input_schema": {
            "type": "object",
            "properties": {
                "class_filter": {
                    "type": "string",
                    "enum": ["1", "2", "3", "4", "5", "6", "7", "all"],
                    "description": "Filtrer par classe (défaut: all)"
                }
            }
        }
    },
    
    {
        "name": "SYNC_JOURNAL",
        "description": """🔄 Synchronise le journal depuis l'ERP source.

**Modes** :
- `incremental` (défaut) : Uniquement les modifications depuis dernière sync
- `full` : Resynchronisation complète

**Basé sur** : Champ `write_date` de l'ERP pour détection des changements

**Retourne** :
- Nombre d'entrées synchronisées
- Date de dernière modification
- Statut de la sync""",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["incremental", "full"],
                    "default": "incremental"
                },
                "date_from": {
                    "type": "string",
                    "description": "Date début période (YYYY-MM-DD)"
                },
                "date_to": {
                    "type": "string",
                    "description": "Date fin période (YYYY-MM-DD)"
                }
            }
        }
    },
    
    {
        "name": "GET_PARTNER_LEDGER",
        "description": """👥 Récupère le grand livre des tiers.

**Types de tiers** :
- `customer` : Clients (comptes 41x)
- `supplier` : Fournisseurs (comptes 40x)
- `all` : Tous les tiers

**Retourne** :
- Nom du tiers
- Mouvements (date, libellé, débit, crédit)
- Solde par tiers""",
        "input_schema": {
            "type": "object",
            "properties": {
                "partner_type": {
                    "type": "string",
                    "enum": ["customer", "supplier", "all"],
                    "default": "all"
                },
                "partner_name": {
                    "type": "string",
                    "description": "Filtrer par nom de tiers (recherche partielle)"
                },
                "date_from": {"type": "string"},
                "date_to": {"type": "string"}
            }
        }
    },
    
    {
        "name": "CREATE_JOURNAL_ENTRY",
        "description": """✏️ Crée une écriture comptable dans l'ERP.

⚠️ **SYSTÈME D'APPROBATION** :
- L'écriture est créée en statut "brouillon"
- Notification envoyée pour approbation
- Comptabilisation effective après validation

**Paramètres requis** :
- journal_code : Code du journal (ex: "VT", "AC")
- date : Date de l'écriture
- lines : Liste des lignes (compte, débit/crédit, libellé)

**Validation automatique** :
- Total débits = Total crédits
- Comptes existants dans le plan comptable
- Date dans exercice ouvert""",
        "input_schema": {
            "type": "object",
            "properties": {
                "journal_code": {
                    "type": "string",
                    "description": "Code du journal"
                },
                "date": {
                    "type": "string",
                    "description": "Date (YYYY-MM-DD)"
                },
                "ref": {
                    "type": "string",
                    "description": "Référence externe"
                },
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_number": {"type": "string"},
                            "name": {"type": "string"},
                            "debit": {"type": "number"},
                            "credit": {"type": "number"},
                            "partner_id": {"type": "integer"}
                        },
                        "required": ["account_number", "name"]
                    }
                }
            },
            "required": ["journal_code", "date", "lines"]
        }
    }
]
```

---

## 8. Synchronisation Incrémentale

### 8.1 Stratégie de Sync

```python
class JournalSyncManager:
    """
    Gestionnaire de synchronisation incrémentale.
    
    Stratégie:
    1. Récupérer last_sync_at depuis DuckDB
    2. Requêter l'ERP avec filtre write_date > last_sync_at
    3. Comparer sync_hash pour détecter les vrais changements
    4. Upsert uniquement les modifications
    5. Mettre à jour sync_metadata
    """
    
    def sync(self, force_full: bool = False) -> Dict[str, Any]:
        """
        Exécute la synchronisation.
        
        Args:
            force_full: Si True, ignore last_sync_at et resync tout
            
        Returns:
            {
                "status": "success" | "error",
                "entries_synced": int,
                "sync_type": "full" | "incremental",
                "last_entry_date": datetime,
                "duration_seconds": float
            }
        """
        start_time = datetime.now()
        
        # 1. Déterminer mode de sync
        last_sync = None if force_full else self.duckdb.get_last_sync_date()
        sync_type = "full" if last_sync is None else "incremental"
        
        # 2. Extraire depuis ERP
        df = self.extractor.fetch_journal_entries(last_sync_date=last_sync)
        
        if df.empty:
            return {
                "status": "success",
                "entries_synced": 0,
                "sync_type": sync_type,
                "message": "Aucune nouvelle entrée"
            }
        
        # 3. Normaliser au format Pinnokio
        df_normalized = self.normalizer.normalize(df)
        
        # 4. Filtrer par hash si incrémental (éviter faux positifs)
        if sync_type == "incremental":
            df_normalized = self._filter_real_changes(df_normalized)
        
        # 5. Upsert dans DuckDB
        count = self.duckdb.upsert_entries(df_normalized)
        
        # 6. Mettre à jour metadata
        self._update_sync_metadata(sync_type, count)
        
        duration = (datetime.now() - start_time).total_seconds()
        
        return {
            "status": "success",
            "entries_synced": count,
            "sync_type": sync_type,
            "last_entry_date": df_normalized['write_date'].max(),
            "duration_seconds": duration
        }
    
    def _filter_real_changes(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compare les hash pour identifier les vrais changements.
        Évite de réinsérer des entrées identiques.
        """
        existing_hashes = self.duckdb.query("""
            SELECT id, sync_hash FROM journal_entries
        """)
        
        hash_map = dict(zip(existing_hashes['id'], existing_hashes['sync_hash']))
        
        # Garder uniquement les lignes avec hash différent
        mask = df.apply(
            lambda row: hash_map.get(row['id']) != row['sync_hash'],
            axis=1
        )
        
        return df[mask]
```

### 8.2 Gestion du Champ `write_date`

> ⭐ **Point critique** : Le champ `write_date` d'Odoo doit être inclus dans toutes les requêtes `account.move.line`.

```python
# Dans odoo_extractor.py - fetch_journal_entries()

fields = [
    'id', 'move_id', 'account_id', 'journal_id', 'partner_id',
    'name', 'ref', 'date', 'debit', 'credit', 'balance',
    'amount_currency', 'currency_id', 'company_id',
    'create_date', 
    'write_date',  # ⭐ CRITIQUE: Date modification pour sync incrémentale
    'move_name', 'account_type'
]

# Filtre incrémental
if last_sync_date:
    domain.append(['write_date', '>', last_sync_date.isoformat()])
```

---

## 9. Points de Conformité

### 9.1 Règles d'Intégration Obligatoires

| # | Règle | Fichier concerné | Priorité |
|---|-------|------------------|----------|
| 1 | L'agent `accounting_chat` **DOIT** être enregistré dans `_AGENT_MODE_REGISTRY` | `agent_modes.py` | 🔴 Haute |
| 2 | Le transfert **DOIT** envoyer une notification WebSocket au frontend | `pinnokio_brain.py` | 🔴 Haute |
| 3 | La mémoire **DOIT** être effacée au retour vers `general_chat` | `pinnokio_brain.py` | 🔴 Haute |
| 4 | Le champ `write_date` **DOIT** être extrait pour sync incrémentale | `odoo_extractor.py` | 🔴 Haute |
| 5 | Le schéma Pinnokio **DOIT** être respecté pour normalisation | `pinnokio_normalizer.py` | 🔴 Haute |
| 6 | Les requêtes SQL **DOIVENT** être validées (SELECT only) | `accounting_tools.py` | 🟡 Moyenne |
| 7 | Le système d'approbation **DOIT** être utilisé pour CREATE_JOURNAL_ENTRY | `accounting_lpt_client.py` | 🟡 Moyenne |
| 8 | La synthèse de clôture **DOIT** inclure toutes les actions effectuées | `CLOSE_ACCOUNTING_SESSION` | 🟡 Moyenne |

### 9.2 Compatibilité avec l'Existant

| Composant existant | Impact | Action requise |
|--------------------|--------|----------------|
| `pinnokio_brain.py` | Modification | Ajouter `accounting_data`, `accounting_duckdb`, `clear_accounting_session()` |
| `agent_modes.py` | Modification | Ajouter entrée `accounting_chat` dans registry |
| `lpt_client.py` | Extension | Ajouter endpoint pour `CREATE_JOURNAL_ENTRY` |
| `spt_tools.py` | Aucun | Les outils accounting sont séparés |
| `erp_service.py` | Réutilisation | Utiliser `ERPConnectionManager` pour credentials Odoo |
| `firebase_providers.py` | Réutilisation | Utiliser pour notifications et stockage |

### 9.3 Variables de Contexte Requises

Le `user_context` du `PinnokioBrain` **DOIT** contenir :

```python
user_context = {
    # Existants (déjà disponibles)
    "mandate_path": str,           # Chemin Firebase du mandat
    "collection_name": str,        # ID de la société
    "firebase_user_id": str,       # ID utilisateur
    "client_uuid": str,            # UUID client
    
    # Requis pour accounting_chat
    "gl_accounting_erp": str,      # ⭐ "odoo", "sage", etc.
    "erp_credentials": {           # ⭐ Credentials ERP (via erp_service.py)
        "url": str,
        "db": str,
        "username": str,
        "password": str,           # Récupéré via Secret Manager
        "company_name": str
    },
    "timezone": str,               # Pour affichage dates
    "currency": str                # Devise principale
}
```

---

## 10. Planning d'Implémentation

### Phase 1 : Infrastructure (Priorité Haute)

| Tâche | Fichier | Estimation |
|-------|---------|------------|
| Créer structure `gl_extractor/` | Nouveau module | 1h |
| Implémenter `base_extractor.py` | Interface | 1h |
| Implémenter `odoo_extractor.py` | Copier/adapter pyodoo.py | 3h |
| Implémenter `pinnokio_normalizer.py` | Copier/adapter onboarding_manager.py | 2h |
| Implémenter `duckdb_manager.py` | Nouveau | 3h |

### Phase 2 : Agent et Outils (Priorité Moyenne)

| Tâche | Fichier | Estimation |
|-------|---------|------------|
| Créer `system_prompt_accounting_agent.py` | Nouveau | 2h |
| Créer `accounting_tools.py` (SPT) | Nouveau | 3h |
| Modifier `agent_modes.py` | Existant | 1h |
| Modifier `pinnokio_brain.py` | Existant | 2h |

### Phase 3 : Intégration (Priorité Basse)

| Tâche | Fichier | Estimation |
|-------|---------|------------|
| Ajouter `TRANSFER_TO_ACCOUNTING` dans general_chat | `spt_tools.py` ou nouveau | 2h |
| Implémenter `CREATE_JOURNAL_ENTRY` (LPT) | `lpt_client.py` | 4h |
| Tests d'intégration | Nouveau | 4h |
| Documentation utilisateur | Nouveau | 2h |

**Total estimé : ~30 heures de développement**

---

## 11. Références Code Existant

### 11.1 Fichiers Sources à Copier/Adapter

| Source | Destination | Éléments à récupérer |
|--------|-------------|----------------------|
| `klk_router/tools/pyodoo.py` | `gl_extractor/odoo_extractor.py` | `ODOO_KLK_VISION`, `fetch_financial_records`, `fetch_account_journal`, `get_account_chart` |
| `klk_router/tools/onboarding_manager.py` | `gl_extractor/pinnokio_normalizer.py` | `expand_list_columns`, renommages colonnes |
| `klk_router/tools/pinnokio_dep.py` | Référence | Structure `PINNOKIO_DEPARTEMENTS` |

### 11.2 Patterns à Suivre

| Pattern | Exemple existant | À reproduire pour |
|---------|------------------|-------------------|
| Définition outil | `job_tools.py` → `APBookkeeperJobTools` | `AccountingSPTTools` |
| LPT Client | `lpt_client.py` → `LPT_APBookkeeper` | `CREATE_JOURNAL_ENTRY` |
| Mode agent | `agent_modes.py` → `_build_apbookeeper_prompt` | `_build_accounting_prompt` |
| Notification WS | `pinnokio_brain.py` → WebSocket events | Notification changement mode |

### 11.3 Configuration ERP

Récupération des credentials Odoo via `erp_service.py` :

```python
# Utiliser le pattern existant
from app.erp_service import ERPConnectionManager

erp_manager = ERPConnectionManager()
credentials = erp_manager._get_erp_credentials(
    user_id=firebase_user_id,
    company_id=collection_name,
    client_uuid=client_uuid
)

# credentials contient: odoo_url, odoo_db, odoo_username, secret_manager (clé)
```

---

## ✅ Prochaine Étape

Ce document sert de **spécification technique** pour l'implémentation de l'agent `accounting_chat`.

**Actions requises** :
1. ✅ Validation de cette architecture
2. ⏳ Création des fichiers selon planning Phase 1
3. ⏳ Tests unitaires pour chaque module
4. ⏳ Intégration et tests end-to-end

---

> **Document généré le** : 29 Novembre 2025  
> **Statut** : En attente de validation avant implémentation

