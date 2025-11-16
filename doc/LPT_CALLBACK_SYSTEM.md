# 🔄 Système de Callback LPT - Documentation Complète

## 📋 Vue d'ensemble

Le système de callback LPT permet aux agents externes (APBookkeeper, Router, Banker) de renvoyer leurs résultats au brain Pinnokio pour la reprise automatique du workflow.

**⭐ NOUVEAU SYSTÈME (v2)** :
- ✅ Format de callback COMPLET (payload original + response)
- ✅ Prompt système spécial pour le mode callback
- ✅ Mise à jour prioritaire de la checklist
- ✅ Suivi/ajustement automatique du plan
- ✅ Réactivation du brain avec historique

---

## 🎯 Architecture Globale

```
┌─────────────────────────────────────────────────────────────┐
│  1. AGENT PINNOKIO (Brain)                                  │
│     Déclenche un LPT → Envoie payload complet              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  2. AGENT EXTERNE (APBookkeeper/Router/Banker)              │
│     Traite la tâche (5-30 minutes)                         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  3. CALLBACK → /lpt/callback                                │
│     Renvoie payload complet + response                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  4. RÉACTIVATION BRAIN + PROMPT SPÉCIAL                     │
│     - Recharge historique                                   │
│     - Génère prompt système callback                       │
│     - Demande mise à jour checklist                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  5. AGENT PINNOKIO (Reprise)                                │
│     - Met à jour checklist (UPDATE_STEP)                   │
│     - Continue/ajuste le plan                              │
│     - Termine ou lance prochaine étape                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 Format du Callback (Nouveau)

### Structure Complète

Le callback doit contenir **TOUTES** les données envoyées au LPT + un champ `response` :

```json
{
  // ═══════════════════════════════════════════════════════════
  // 1. IDENTIFIANTS (Données englobantes originales)
  // ═══════════════════════════════════════════════════════════
  "collection_name": "company_abc123",
  "user_id": "user_xyz789",
  "client_uuid": "client_a1b2c3d4",
  "mandates_path": "clients/user_xyz/bo_clients/.../mandates/...",
  "batch_id": "batch_a1b2c3d4e5",
  
  // ═══════════════════════════════════════════════════════════
  // 2. DONNÉES DE LA TÂCHE (jobs_data original)
  // ═══════════════════════════════════════════════════════════
  "jobs_data": [
    {
      "file_name": "facture_001.pdf",
      "job_id": "file_abc123",
      "instructions": "Vérifier montants",
      "status": "to_process",
      "approval_required": false,
      "approval_contact_creation": false
    }
  ],
  
  // ═══════════════════════════════════════════════════════════
  // 3. CONFIGURATION (settings originaux)
  // ═══════════════════════════════════════════════════════════
  "settings": [
    {"communication_mode": "webhook"},
    {"log_communication_mode": "firebase"},
    {"dms_system": "google_drive"}
  ],
  
  // ═══════════════════════════════════════════════════════════
  // 4. TRAÇABILITÉ (traceability original)
  // ═══════════════════════════════════════════════════════════
  "traceability": {
    "thread_key": "thread_abc123",
    "thread_name": "APBookkeeper_batch_xxx",
    "execution_id": "exec_def456",
    "execution_plan": "NOW",
    "initiated_at": "2025-10-25T14:30:00Z",
    "source": "pinnokio_brain"
  },
  
  // ═══════════════════════════════════════════════════════════
  // 5. IDENTIFIANTS ADDITIONNELS
  // ═══════════════════════════════════════════════════════════
  "pub_sub_id": "batch_a1b2c3d4e5",
  "start_instructions": null,
  
  // ═══════════════════════════════════════════════════════════
  // 6. RÉPONSE DU LPT (⭐ NOUVEAU - Données de sortie)
  // ═══════════════════════════════════════════════════════════
  "response": {
    "status": "completed",  // "completed" | "failed" | "partial"
    "result": {
      "summary": "50 factures saisies avec succès",
      "processed_items": 50,
      "failed_items": 0,
      "details": {
        "total_amount": 125000.50,
        "currency": "EUR",
        // ... autres données pertinentes
      }
    },
    "error": null
  },
  
  // ═══════════════════════════════════════════════════════════
  // 7. MÉTADONNÉES D'EXÉCUTION
  // ═══════════════════════════════════════════════════════════
  "execution_time": "450.5s",
  "completed_at": "2025-10-25T15:00:00Z",
  "logs_url": "https://logs.example.com/task_abc123"
}
```

---

## 🎨 Modèle TypeScript/JSON Schema

```typescript
interface LPTCallbackRequest {
  // Identifiants
  collection_name: string;
  user_id: string;
  client_uuid: string;
  mandates_path: string;
  batch_id: string;
  
  // Données tâche
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
    initiated_at: string;
    source: string;
  };
  
  // Identifiants additionnels
  pub_sub_id: string;
  start_instructions?: string;
  
  // ⭐ RÉPONSE DU LPT
  response: {
    status: "completed" | "failed" | "partial";
    result?: {
      summary: string;
      processed_items: number;
      failed_items?: number;
      details?: Record<string, any>;
    };
    error?: string;
  };
  
  // Métadonnées exécution
  execution_time?: string;
  completed_at?: string;
  logs_url?: string;
}
```

---

## 🔄 Workflow de Reprise

### Étape 1 : Réception du Callback

**Endpoint** : `POST /lpt/callback`

**Actions** :
1. ✅ Validation du payload complet
2. ✅ Sauvegarde dans Firebase (tasks + original_payload + response)
3. ✅ Vérification session LLM active
4. ✅ Détection mode (UI ou Backend)
5. ✅ Lancement reprise workflow en background

### Étape 2 : Réactivation du Brain

**Méthode** : `_resume_workflow_after_lpt()`

**Actions** :
1. ✅ Garantir session initialisée (`_ensure_session_initialized`)
2. ✅ Charger/créer brain pour le thread
   - Si brain n'existe pas → charger historique depuis RTDB
   - Si brain existe → utiliser brain existant
3. ✅ Construire prompt système spécial

### Étape 3 : Génération Prompt Système Callback

**Fonction** : `build_lpt_callback_prompt()`

**Paramètres** :
- `user_context` : Contexte utilisateur
- `lpt_response` : Réponse du LPT
- `original_payload` : Payload original complet

**Contenu du prompt** :
```
# 🔄 MODE CALLBACK LPT - Reprise de Workflow

## CONTEXTE
Vous venez de recevoir une RÉPONSE d'un outil LPT que vous aviez 
VOUS-MÊME DÉCLENCHÉ précédemment.

## MISSION PRIORITAIRE : MISE À JOUR CHECKLIST

⚠️ WORKFLOW OBLIGATOIRE :

1. METTRE À JOUR LA CHECKLIST (🔴 OBLIGATOIRE EN PREMIER)
   - Utiliser UPDATE_STEP avec l'étape concernée
   - Statut : "completed" | "error"
   - Message concret avec résultats

2. ANALYSER LE RÉSULTAT
   - Consulter le plan initial
   - Déterminer la suite

3. DÉCIDER :
   ├─→ Continuer (prochaine étape)
   ├─→ Ajuster le plan (si nécessaire)
   └─→ Terminer (si tout est fini)
```

### Étape 4 : Message de Continuation

**Format selon status** :

#### Status : `completed` ✅
```markdown
🔄 **RÉPONSE DE L'OUTIL {task_type}**

**Status** : ✅ Succès
**Résumé** : {summary}
**Items traités** : {processed_items}

**Données complètes** :
```json
{result_details}
```

⚠️ **ACTIONS REQUISES** :
1. METTRE À JOUR LA CHECKLIST (🔴 PRIORITÉ ABSOLUE)
2. ANALYSER ET CONTINUER
```

#### Status : `failed` ❌
```markdown
🔄 **RÉPONSE DE L'OUTIL {task_type}**

**Status** : ❌ Échec
**Erreur** : {error}

⚠️ **ACTIONS REQUISES** :
1. METTRE À JOUR LA CHECKLIST → "error"
2. PROPOSER ACTIONS CORRECTIVES
```

#### Status : `partial` ⚠️
```markdown
🔄 **RÉPONSE DE L'OUTIL {task_type}**

**Status** : ⚠️ Partiel
**Résumé** : {summary}

⚠️ **ACTIONS REQUISES** :
1. METTRE À JOUR LA CHECKLIST
2. EXPLIQUER POURQUOI PARTIEL
3. PROPOSER ACTIONS POUR COMPLÉTER
```

### Étape 5 : Exécution de l'Agent

**L'agent doit** :

#### 1️⃣ Mettre à jour la checklist (🔴 PRIORITÉ ABSOLUE)

```json
// Appel UPDATE_STEP obligatoire
{
  "step_id": "STEP_2_SAISIE_FACTURES",
  "status": "completed",
  "message": "✅ 50 factures saisies - 125,000 EUR"
}
```

#### 2️⃣ Analyser et continuer

**Option A : Continuer selon le plan**
```
Checklist actuelle :
- STEP_1_ANALYSE_DOCUMENTS ✅
- STEP_2_SAISIE_FACTURES ✅ (vient de terminer)
- STEP_3_RECONCILIATION_BANCAIRE → prochaine étape

→ Marquer STEP_3 en "in_progress"
→ Appeler GET_BANK_TRANSACTIONS
→ Appeler LPT_Banker
```

**Option B : Ajuster le plan**
```
Résultat inattendu détecté
→ Expliquer pourquoi ajuster
→ Proposer nouveau plan
→ Créer/ajuster étapes checklist
→ Continuer selon nouveau plan
```

**Option C : Terminer**
```
Toutes les étapes terminées ✅
→ Vérifier checklist complète
→ Appeler TERMINATE_TASK avec résumé structuré
```

---

## 📝 Exemple Complet de Callback

### Payload envoyé au LPT (APBookkeeper)

```json
{
  "collection_name": "company_123",
  "user_id": "user_456",
  "client_uuid": "client_789",
  "mandates_path": "clients/user_456/bo_clients/client_789/mandates/mandate_001",
  "batch_id": "batch_abc123def456",
  
  "jobs_data": [
    {
      "file_name": "facture_orange_01.pdf",
      "job_id": "file_001",
      "instructions": "Vérifier montant HT/TTC",
      "status": "to_process",
      "approval_required": false,
      "approval_contact_creation": false
    },
    {
      "file_name": "facture_sfr_02.pdf",
      "job_id": "file_002",
      "instructions": "",
      "status": "to_process",
      "approval_required": false,
      "approval_contact_creation": false
    }
  ],
  
  "settings": [
    {"communication_mode": "webhook"},
    {"log_communication_mode": "firebase"},
    {"dms_system": "google_drive"}
  ],
  
  "traceability": {
    "thread_key": "thread_xyz789",
    "thread_name": "APBookkeeper_batch_abc123def456",
    "execution_id": "exec_task_001",
    "execution_plan": "NOW",
    "initiated_at": "2025-10-25T14:30:00Z",
    "source": "pinnokio_brain"
  },
  
  "pub_sub_id": "batch_abc123def456",
  "start_instructions": "Traiter toutes les factures > 1000 EUR"
}
```

### Callback reçu (Même payload + response)

```json
{
  // ⭐ TOUTES LES DONNÉES CI-DESSUS +
  
  "response": {
    "status": "completed",
    "result": {
      "summary": "2 factures traitées avec succès",
      "processed_items": 2,
      "failed_items": 0,
      "details": {
        "total_amount_ht": 1850.00,
        "total_amount_ttc": 2220.00,
        "currency": "EUR",
        "invoices": [
          {
            "job_id": "file_001",
            "supplier": "Orange",
            "amount_ht": 850.00,
            "amount_ttc": 1020.00,
            "accounting_entry_id": "entry_001"
          },
          {
            "job_id": "file_002",
            "supplier": "SFR",
            "amount_ht": 1000.00,
            "amount_ttc": 1200.00,
            "accounting_entry_id": "entry_002"
          }
        ]
      }
    },
    "error": null
  },
  
  "execution_time": "125.8s",
  "completed_at": "2025-10-25T14:32:05Z",
  "logs_url": "https://logs.aws.com/batch_abc123def456"
}
```

### Réponse de l'Agent (après reprise)

L'agent reçoit le prompt système callback et exécute :

```
1. UPDATE_STEP :
{
  "step_id": "STEP_2_SAISIE_FACTURES",
  "status": "completed",
  "message": "✅ 2 factures saisies - Orange (1020 EUR) + SFR (1200 EUR) = 2220 EUR TTC"
}

2. Analyse :
- Plan initial : STEP_2_SAISIE_FACTURES → STEP_3_RECONCILIATION_BANCAIRE
- Résultat : Succès complet
- Décision : Continuer selon le plan

3. Prochaine étape :
UPDATE_STEP :
{
  "step_id": "STEP_3_RECONCILIATION_BANCAIRE",
  "status": "in_progress",
  "message": "🔄 Récupération des transactions bancaires..."
}

GET_BANK_TRANSACTIONS(...)
→ Résultat : 35 transactions à réconcilier

LPT_Banker(...)
→ Lance réconciliation
```

---

## 🎯 Règles Importantes

### ⭐ Règle 1 : TOUJOURS Mettre à Jour la Checklist EN PREMIER
- ❌ **NE JAMAIS** continuer sans `UPDATE_STEP`
- ✅ **TOUJOURS** appeler `UPDATE_STEP` avant toute autre action

### ⭐ Règle 2 : Suivre le Plan OU Justifier les Changements
- ✅ Le plan initial est dans l'historique de conversation
- ✅ Si changement nécessaire, expliquer clairement pourquoi
- ✅ Mettre à jour la checklist en conséquence

### ⭐ Règle 3 : Terminer UNIQUEMENT Quand TOUT est Fini
- ❌ **NE PAS** utiliser `TERMINATE_TASK` si des étapes restent
- ❌ **NE PAS** terminer si LPT échoué sans action corrective
- ✅ Terminer SEULEMENT quand objectif global atteint

### ⭐ Règle 4 : Être Précis et Factuel
- ✅ Utiliser chiffres exacts (items traités, montants)
- ✅ Citer IDs et références concrètes
- ❌ Éviter formulations vagues

### ⭐ Règle 5 : Gérer les Erreurs Proactivement
- Si échec → Proposer actions correctives
- Si partiel → Expliquer et proposer relance/ajustement
- Si inattendu → Analyser et ajuster le plan

---

## 🔧 Implémentation Technique

### Fichiers Modifiés

| Fichier | Modifications |
|---------|---------------|
| `app/main.py` | Nouveau modèle `LPTCallbackRequest` + endpoint callback |
| `app/llm_service/llm_manager.py` | Méthode `_resume_workflow_after_lpt` refactorisée |
| `app/pinnokio_agentic_workflow/orchestrator/system_prompt_lpt_callback.py` | **NOUVEAU** : Prompt système callback |

### Classes/Méthodes Principales

#### 1. `LPTCallbackRequest` (BaseModel)
```python
class LPTCallbackRequest(BaseModel):
    # Données englobantes originales
    collection_name: str
    user_id: str
    client_uuid: str
    mandates_path: str
    batch_id: str
    jobs_data: List[Dict[str, Any]]
    settings: List[Dict[str, Any]]
    traceability: Dict[str, Any]
    pub_sub_id: str
    start_instructions: Optional[str]
    
    # ⭐ NOUVEAU
    response: Dict[str, Any]
    
    # Properties pour rétrocompatibilité
    @property
    def task_id(self) -> str
    @property
    def thread_key(self) -> str
    @property
    def status(self) -> str
```

#### 2. `build_lpt_callback_prompt()`
```python
def build_lpt_callback_prompt(
    user_context: dict,
    lpt_response: dict,
    original_payload: dict
) -> str:
    """Génère prompt système spécial callback."""
```

#### 3. `_resume_workflow_after_lpt()`
```python
async def _resume_workflow_after_lpt(
    self,
    user_id: str,
    company_id: str,
    thread_key: str,
    task_id: str,
    task_data: dict,
    lpt_response: dict,        # ⭐ NOUVEAU
    original_payload: dict,    # ⭐ NOUVEAU
    user_connected: bool
):
    """Reprend workflow avec prompt callback."""
```

---

## 📊 Avantages du Nouveau Système

### ✅ Cohérence des Données
- Même format englobeur pour envoi et callback
- Toutes les données disponibles pour reprise
- Traçabilité complète

### ✅ Gestion Intelligente de la Checklist
- Mise à jour prioritaire obligatoire
- Messages concrets avec résultats
- Suivi précis de l'avancement

### ✅ Flexibilité du Workflow
- Continuation automatique selon plan
- Ajustement dynamique si nécessaire
- Gestion proactive des erreurs

### ✅ Contexte Complet pour l'Agent
- Historique conservé
- Prompt système adapté au callback
- Accès à tous les outils (SPT/LPT)

### ✅ Dual-Mode Support
- Mode UI : Streaming WebSocket actif
- Mode Backend : RTDB uniquement
- Détection automatique

---

## 🚀 Migration depuis l'Ancien Système

### Ancien Format (v1)
```json
{
  "task_id": "batch_xxx",
  "thread_key": "thread_yyy",
  "user_id": "user_zzz",
  "collection_name": "company_aaa",
  "status": "completed",
  "result": {...},
  "error": null
}
```

### Nouveau Format (v2)
```json
{
  // Toutes les données englobantes (voir format complet ci-dessus)
  "response": {
    "status": "completed",
    "result": {...},
    "error": null
  }
}
```

**Rétrocompatibilité** : Les properties (`task_id`, `thread_key`, `status`, etc.) assurent la compatibilité avec l'ancien code.

---

## 📚 Références

- **Format Payload LPT** : `doc/LPT_PAYLOAD_FORMAT.md`
- **Workflow Checklist** : `doc/WORKFLOW_CHECK_LIST.MD`
- **System Prompt Principal** : `app/pinnokio_agentic_workflow/orchestrator/system_prompt_principal_agent.py`
- **System Prompt Callback** : `app/pinnokio_agentic_workflow/orchestrator/system_prompt_lpt_callback.py`

---

**Version** : 2.0  
**Date** : 2025-10-25  
**Auteur** : Pinnokio Brain Team

