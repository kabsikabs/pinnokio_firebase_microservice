# 📦 Format des Payloads LPT - Documentation Complète

## 🎯 Vue d'ensemble

Ce document décrit le format **ENGLOBEUR** (wrapper) utilisé pour TOUS les LPT (Long Process Tasks), ainsi que les variations du champ `jobs_data` spécifiques à chaque type de LPT.

---

## 🔷 Format Englobeur UNIVERSEL

**✅ CE FORMAT EST IDENTIQUE POUR TOUS LES LPT**

Ce format englobeur est cohérent et paramétrable côté application. Seul le contenu de `jobs_data` varie selon le type de LPT.

### Structure Complète

```json
{
  // ═══════════════════════════════════════════════════════
  // 1. INFORMATIONS DE BASE (Identifiants utilisateur/société)
  // ═══════════════════════════════════════════════════════
  "collection_name": "company_abc123",
  "user_id": "user_xyz789",
  "client_uuid": "client_a1b2c3d4",
  "mandates_path": "clients/user_xyz/bo_clients/client_id/mandates/mandate_id",
  "batch_id": "batch_a1b2c3d4e5",  // ID unique pour ce traitement
  
  // ═══════════════════════════════════════════════════════
  // 2. DONNÉES DE LA TÂCHE (Variable selon le type de LPT)
  // ═══════════════════════════════════════════════════════
  "jobs_data": [
    // ⭐ CONTENU VARIABLE - Voir section "Variations jobs_data" ci-dessous
  ],
  
  // ═══════════════════════════════════════════════════════
  // 3. CONFIGURATION (Settings système)
  // ═══════════════════════════════════════════════════════
  "settings": [
    {
      "communication_mode": "webhook"  // ou "pubsub", "firebase"
    },
    {
      "log_communication_mode": "firebase"  // ou "pubsub", "console"
    },
    {
      "dms_system": "google_drive"  // ou "onedrive", "dropbox"
    }
  ],
  
  // ═══════════════════════════════════════════════════════
  // 4. TRAÇABILITÉ (Pour callback et suivi)
  // ═══════════════════════════════════════════════════════
  "traceability": {
    "thread_key": "thread_abc123",
    "thread_name": "APBookkeeper_batch_xxx",
    "execution_id": "exec_def456",  // ID d'exécution de tâche (si applicable)
    "execution_plan": "NOW",  // ou "ON_DEMAND", "SCHEDULED", "ONE_TIME"
    "initiated_at": "2025-10-25T14:30:00.000Z",
    "source": "pinnokio_brain"
  },
  
  // ═══════════════════════════════════════════════════════
  // 5. IDENTIFIANTS ADDITIONNELS
  // ═══════════════════════════════════════════════════════
  "pub_sub_id": "batch_a1b2c3d4e5",  // Généralement = batch_id
  "start_instructions": "Instructions générales (optionnel)"  // ou null
}
```

---

## 📊 Comparaison des Champs Englobeurs

| Champ | Type | Description | Obligatoire | Identique pour tous LPT |
|-------|------|-------------|-------------|------------------------|
| `collection_name` | string | ID de la société | ✅ Oui | ✅ Oui |
| `user_id` | string | ID utilisateur Firebase | ✅ Oui | ✅ Oui |
| `client_uuid` | string | UUID unique du client | ✅ Oui | ✅ Oui |
| `mandates_path` | string | Chemin Firebase du mandat | ✅ Oui | ✅ Oui |
| `batch_id` | string | ID unique du batch | ✅ Oui | ✅ Oui |
| `jobs_data` | array | Données spécifiques à traiter | ✅ Oui | ❌ **Variable** |
| `settings` | array | Configuration système | ✅ Oui | ✅ Oui |
| `traceability` | object | Info de traçabilité | ✅ Oui | ✅ Oui |
| `pub_sub_id` | string | ID pour PubSub | ✅ Oui | ✅ Oui |
| `start_instructions` | string\|null | Instructions générales | ⚪ Optionnel | ✅ Oui |

---

## 🎨 Variations du champ `jobs_data`

Voici les **SEULES DIFFÉRENCES** entre les LPT : le contenu de `jobs_data`.

### 1️⃣ LPT APBookkeeper (Saisie de factures)

**URL** : `/apbookeeper-event-trigger`

```json
"jobs_data": [
  {
    "file_name": "document_file_abc123",
    "job_id": "file_abc123",  // drive_file_id du document
    "instructions": "Vérifier les montants HT/TTC",
    "status": "to_process",
    "approval_required": false,
    "approval_contact_creation": false
  },
  {
    "file_name": "document_file_def456",
    "job_id": "file_def456",
    "instructions": "Facture urgente",
    "status": "to_process",
    "approval_required": true,
    "approval_contact_creation": true
  }
  // ... autres factures
]
```

**Champs spécifiques** :
- `file_name` : Nom du fichier
- `job_id` : drive_file_id du document
- `instructions` : Instructions spécifiques pour cette facture
- `status` : "to_process"
- `approval_required` : Nécessite approbation avant saisie
- `approval_contact_creation` : Approuver création de contact

---

### 2️⃣ LPT Router (Analyse et classification)

**URL** : `/event-trigger`

```json
"jobs_data": [
  {
    "file_name": "document_file_xyz789",
    "drive_file_id": "file_xyz789",
    "instructions": "Analyser et classifier tous les documents du dossier",
    "status": "to_route",
    "approval_required": false,
    "automated_workflow": true
  }
]
```

**Champs spécifiques** :
- `file_name` : Nom du fichier/dossier
- `drive_file_id` : ID Drive du fichier/dossier à analyser
- `instructions` : Instructions d'analyse
- `status` : "to_route"
- `approval_required` : Nécessite approbation
- `automated_workflow` : Workflow automatisé après analyse

**Note** : Router traite généralement UN SEUL élément à la fois (fichier ou dossier).

---

### 3️⃣ LPT Banker (Réconciliation bancaire)

**URL** : `/banker-event-trigger`

```json
"jobs_data": [
  {
    "bank_account": "account_bnp_principal",
    "job_id": "bank_job_a1b2c3",
    "transactions": [
      "tx_001",
      "tx_002",
      "tx_003"
    ],
    "instructions": "Rapprocher automatiquement avec les factures correspondantes",
    "approval_required": false,
    "approval_contact_creation": false
  }
]
```

**Champs spécifiques** :
- `bank_account` : Identifiant du compte bancaire
- `job_id` : ID du job bancaire
- `transactions` : Liste des IDs de transactions à traiter
- `instructions` : Instructions de réconciliation
- `approval_required` : Nécessite approbation
- `approval_contact_creation` : Approuver création de contact

**Note** : Banker traite généralement UN SEUL job à la fois mais peut contenir PLUSIEURS transactions.

---

## 📐 Schéma de Validation

### Format Englobeur (Commun à TOUS les LPT)

```typescript
interface LPTPayload {
  // Identifiants
  collection_name: string;
  user_id: string;
  client_uuid: string;
  mandates_path: string;
  batch_id: string;
  
  // Données variables
  jobs_data: Array<APBookkeeperJob | RouterJob | BankerJob>;
  
  // Configuration
  settings: Array<{
    communication_mode?: string;
    log_communication_mode?: string;
    dms_system?: string;
  }>;
  
  // Traçabilité
  traceability: {
    thread_key: string;
    thread_name: string;
    execution_id?: string;
    execution_plan?: string;
    initiated_at: string;  // ISO timestamp
    source: string;
  };
  
  // Identifiants additionnels
  pub_sub_id: string;
  start_instructions?: string | null;
}
```

### Types de jobs_data

```typescript
// APBookkeeper
interface APBookkeeperJob {
  file_name: string;
  job_id: string;  // drive_file_id
  instructions: string;
  status: "to_process";
  approval_required: boolean;
  approval_contact_creation: boolean;
}

// Router
interface RouterJob {
  file_name: string;
  drive_file_id: string;
  instructions: string;
  status: "to_route";
  approval_required: boolean;
  automated_workflow: boolean;
}

// Banker
interface BankerJob {
  bank_account: string;
  job_id: string;
  transactions: string[];  // Liste d'IDs
  instructions: string;
  approval_required: boolean;
  approval_contact_creation: boolean;
}
```

---

## 🔍 Détails Section `traceability`

Cette section permet le suivi complet de la tâche pour le callback :

| Champ | Type | Description | Exemple |
|-------|------|-------------|---------|
| `thread_key` | string | ID du thread de conversation | "thread_abc123" |
| `thread_name` | string | Nom descriptif du thread | "APBookkeeper_batch_xxx" |
| `execution_id` | string\|null | ID d'exécution (si tâche planifiée) | "exec_def456" |
| `execution_plan` | string\|null | Mode d'exécution | "NOW", "ON_DEMAND", "SCHEDULED" |
| `initiated_at` | string | Timestamp ISO d'initiation | "2025-10-25T14:30:00Z" |
| `source` | string | Source de l'appel | "pinnokio_brain" |

**Usage** : Ces informations sont renvoyées dans le callback pour permettre au brain de retrouver le contexte d'exécution.

---

## 🔍 Détails Section `settings`

Format tableau avec objets individuels :

```json
"settings": [
  {
    "communication_mode": "webhook"
  },
  {
    "log_communication_mode": "firebase"
  },
  {
    "dms_system": "google_drive"
  }
]
```

**Valeurs possibles** :

| Setting | Valeurs possibles | Description |
|---------|-------------------|-------------|
| `communication_mode` | "webhook", "pubsub", "firebase" | Mode de callback |
| `log_communication_mode` | "firebase", "pubsub", "console" | Mode de logs |
| `dms_system` | "google_drive", "onedrive", "dropbox" | Système DMS |

---

## 📡 Endpoints LPT

| LPT | Méthode | URL (PROD) | Timeout |
|-----|---------|-----------|---------|
| **APBookkeeper** | POST | `{ALB}/apbookeeper-event-trigger` | 30s |
| **Router** | POST | `{ALB}/event-trigger` | 30s |
| **Banker** | POST | `{ALB}/banker-event-trigger` | 30s |

**ALB** : `http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com`

---

## ✅ Validation du Format Englobeur

### ✅ Le format englobeur est COHÉRENT pour tous les LPT

| Critère | APBookkeeper | Router | Banker | Cohérent ? |
|---------|--------------|--------|--------|------------|
| `collection_name` | ✅ | ✅ | ✅ | ✅ Oui |
| `user_id` | ✅ | ✅ | ✅ | ✅ Oui |
| `client_uuid` | ✅ | ✅ | ✅ | ✅ Oui |
| `mandates_path` | ✅ | ✅ | ✅ | ✅ Oui |
| `batch_id` | ✅ | ✅ | ✅ | ✅ Oui |
| `jobs_data` (array) | ✅ | ✅ | ✅ | ✅ Oui (contenu variable) |
| `settings` (array) | ✅ | ✅ | ✅ | ✅ Oui |
| `traceability` (object) | ✅ | ✅ | ✅ | ✅ Oui |
| `pub_sub_id` | ✅ | ✅ | ✅ | ✅ Oui |
| `start_instructions` | ✅ | ✅ | ✅ | ✅ Oui |

**✅ CONCLUSION : Le format englobeur est IDENTIQUE pour tous les LPT.**

**❗ SEULE DIFFÉRENCE : Le contenu du tableau `jobs_data`.**

---

## 🎯 Paramétrage côté Application

### Structure à paramétrer :

```python
class LPTPayloadBuilder:
    """Builder pour construire les payloads LPT."""
    
    @staticmethod
    def build_base_payload(
        collection_name: str,
        user_id: str,
        context: Dict[str, Any],
        batch_id: str,
        thread_key: str,
        execution_id: Optional[str] = None,
        execution_plan: Optional[str] = None,
        start_instructions: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Construit la partie ENGLOBEUR du payload (commune à tous les LPT).
        """
        return {
            # Identifiants
            "collection_name": collection_name,
            "user_id": user_id,
            "client_uuid": context["client_uuid"],
            "mandates_path": context["mandate_path"],
            "batch_id": batch_id,
            
            # jobs_data sera ajouté par le caller
            
            # Configuration
            "settings": [
                {"communication_mode": context["communication_mode"]},
                {"log_communication_mode": context["log_communication_mode"]},
                {"dms_system": context["dms_system"]}
            ],
            
            # Traçabilité
            "traceability": {
                "thread_key": thread_key,
                "thread_name": f"LPT_{batch_id}",
                "execution_id": execution_id,
                "execution_plan": execution_plan,
                "initiated_at": datetime.now(timezone.utc).isoformat(),
                "source": "pinnokio_brain"
            },
            
            # Identifiants additionnels
            "pub_sub_id": batch_id,
            "start_instructions": start_instructions
        }
    
    @staticmethod
    def add_apbookeeper_jobs(
        base_payload: Dict[str, Any],
        job_ids: List[str],
        file_instructions: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """Ajoute jobs_data spécifique à APBookkeeper."""
        jobs_data = []
        for job_id in job_ids:
            jobs_data.append({
                "file_name": f"document_{job_id}",
                "job_id": job_id,
                "instructions": file_instructions.get(job_id, "") if file_instructions else "",
                "status": "to_process",
                "approval_required": False,
                "approval_contact_creation": False
            })
        
        base_payload["jobs_data"] = jobs_data
        return base_payload
    
    # Méthodes similaires pour Router et Banker...
```

---

## 📝 Exemple Complet : APBookkeeper

### Payload complet envoyé :

```json
{
  "collection_name": "company_abc123",
  "user_id": "user_xyz789",
  "client_uuid": "client_a1b2c3d4",
  "mandates_path": "clients/user_xyz789/bo_clients/client_id/mandates/mandate_id",
  "batch_id": "batch_f1e2d3c4b5",
  
  "jobs_data": [
    {
      "file_name": "document_file_001",
      "job_id": "file_001",
      "instructions": "Vérifier les montants HT/TTC",
      "status": "to_process",
      "approval_required": false,
      "approval_contact_creation": false
    },
    {
      "file_name": "document_file_002",
      "job_id": "file_002",
      "instructions": "",
      "status": "to_process",
      "approval_required": true,
      "approval_contact_creation": false
    }
  ],
  
  "settings": [
    {"communication_mode": "webhook"},
    {"log_communication_mode": "firebase"},
    {"dms_system": "google_drive"}
  ],
  
  "traceability": {
    "thread_key": "thread_abc123",
    "thread_name": "APBookkeeper_batch_f1e2d3c4b5",
    "execution_id": "exec_def456",
    "execution_plan": "NOW",
    "initiated_at": "2025-10-25T14:30:00.000Z",
    "source": "pinnokio_brain"
  },
  
  "pub_sub_id": "batch_f1e2d3c4b5",
  "start_instructions": "Traiter toutes les factures > 1000 EUR"
}
```

---

## 🔄 Réponse des LPT

### Format de réponse HTTP (succès)

**Status** : `202 Accepted`

```json
{
  "status": "queued",
  "task_id": "batch_f1e2d3c4b5",
  "batch_id": "batch_f1e2d3c4b5",
  "message": "Tâche lancée avec succès"
}
```

### Format de réponse (erreur)

**Status** : `400` ou `500`

```json
{
  "status": "error",
  "error": "Message d'erreur détaillé",
  "error_type": "missing_user_data | configuration_error | technical_error"
}
```

---

## 📚 Références

- **Code source** : `app/pinnokio_agentic_workflow/tools/lpt_client.py`
- **Méthodes principales** :
  - `launch_apbookeeper()` - Lignes 459-600
  - `launch_router()` - Lignes 602-744
  - `launch_banker()` - Lignes 758-920

---

## 🎯 Résumé Exécutif

### ✅ Format Englobeur COHÉRENT

Le format englobeur est **identique pour tous les LPT** et contient :
1. **Identifiants** : collection_name, user_id, client_uuid, mandates_path, batch_id
2. **jobs_data** : Array de jobs (contenu variable selon LPT)
3. **settings** : Configuration système (communication_mode, log_mode, dms_system)
4. **traceability** : Info de traçabilité (thread_key, execution_id, etc.)
5. **pub_sub_id** : Identifiant PubSub
6. **start_instructions** : Instructions générales (optionnel)

### ❗ SEULE DIFFÉRENCE : jobs_data

Chaque LPT a son propre format de `jobs_data` :
- **APBookkeeper** : Factures (file_name, job_id, instructions, status, approval_*)
- **Router** : Documents (file_name, drive_file_id, instructions, status, approval_*, automated_workflow)
- **Banker** : Transactions bancaires (bank_account, job_id, transactions[], instructions, approval_*)

### ✅ Paramétrable côté Application

Le format englobeur peut être facilement paramétré côté application car :
- ✅ Structure cohérente
- ✅ Champs obligatoires bien définis
- ✅ Seul jobs_data varie selon le type de LPT
- ✅ Validation TypeScript/JSON Schema possible

---

**Version** : 1.0  
**Date** : 2025-10-25  
**Auteur** : Pinnokio Brain Team

