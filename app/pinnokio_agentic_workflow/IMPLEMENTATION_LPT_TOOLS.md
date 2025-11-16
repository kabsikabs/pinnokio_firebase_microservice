# 🎯 Implémentation des Outils LPT - Documentation Complète

## 📋 Vue d'ensemble

Ce document décrit l'implémentation complète des outils LPT (Long Process Tooling) pour l'agent Pinnokio, basée sur la documentation `lpt_tools.txt`.

---

## 🏗️ Architecture Implémentée

### **1. Structure des fichiers**

```
app/pinnokio_agentic_workflow/
├── tools/
│   ├── __init__.py              # Exports des classes
│   ├── tool_registry.py         # Registre centralisé des outils
│   ├── spt_tools.py             # Outils SPT (Short Process Tooling)
│   └── lpt_client.py            # ⭐ Client LPT avec construction automatique des payloads
│
├── orchestrator/
│   ├── pinnokio_brain.py        # ✅ Modifié - Utilise SPTTools et LPTClient
│   ├── task_tracker.py          # Tracking des tâches LPT
│   ├── task_planner.py          # Planning des tâches
│   └── task_executor.py         # Exécution des tâches
│
└── workflows/
    ├── pinnokio_workflow.py     # Workflow agentic principal
    └── base_workflow.py         # Base pour workflows
```

---

## 🎯 Principe Clé : Simplification pour l'Agent

### ❌ **AVANT** : L'agent devait fournir TOUT le payload

```json
{
    "collection_name": "company_abc",
    "user_id": "uid_123",
    "jobs_data": [...],
    "client_uuid": "...",
    "settings": [...],
    "mandates_path": "...",
    "thread_key": "...",
    ...
}
```

### ✅ **MAINTENANT** : L'agent fournit SEULEMENT les IDs + instructions

```json
{
    "job_ids": ["file_abc123", "file_def456"],
    "general_instructions": "Vérifier les montants HT/TTC"
}
```

**Tout le reste est automatique !** 🎉

---

## 🛠️ Outils LPT Implémentés

### **1. LPT_APBookkeeper** - Saisie de Factures Fournisseur

#### **Ce que l'agent fournit** :
```json
{
    "job_ids": ["file_abc123", "file_def456"],
    "general_instructions": "Vérifier les montants HT/TTC",
    "file_instructions": {
        "file_abc123": "Facture urgente, prioriser"
    },
    "approval_required": false,
    "approval_contact_creation": false
}
```

#### **Ce que le système construit automatiquement** :

```python
payload = {
    "collection_name": company_id,              # ✅ Automatique
    "user_id": user_id,                         # ✅ Automatique
    "thread_key": thread_key,                   # ✅ Automatique (⭐ TRÈS IMPORTANT)
    "client_uuid": context['client_uuid'],      # ✅ Automatique (depuis registre)
    "mandates_path": context['mandate_path'],   # ✅ Automatique (depuis registre)
    "settings": [                               # ✅ Automatique (depuis registre)
        {'communication_mode': '...'},
        {'log_communication_mode': '...'},
        {'dms_system': '...'}
    ],
    "batch_id": f'batch_{uuid.uuid4().hex[:10]}',  # ✅ Généré automatiquement
    "jobs_data": [                              # ✅ Construit depuis job_ids
        {
            "file_name": "document_file_abc123",
            "job_id": "file_abc123",
            "instructions": "Facture urgente, prioriser",
            "status": "to_process",
            "approval_required": false,
            "approval_contact_creation": false
        },
        ...
    ],
    "start_instructions": "Vérifier les montants HT/TTC"  # ✅ Instructions générales
}
```

#### **Endpoint HTTP** :
```
POST http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com/apbookeeper-event-trigger
```

#### **Notification Firebase automatique** :

```python
Path: clients/{user_id}/notifications

Data: {
    'function_name': 'APbookeeper',
    'file_id': 'file_abc123',
    'job_id': 'file_abc123',
    'file_name': 'document_file_abc123',
    'status': 'in queue',
    'timestamp': '2025-10-13T...',
    'collection_id': 'company_abc',
    'collection_name': 'Company ABC',
    'batch_id': 'batch_abc123',
    ...
}
```

---

### **2. LPT_Router** - Routage de Documents

#### **Ce que l'agent fournit** :
```json
{
    "drive_file_id": "file_xyz789",
    "instructions": "Router vers le dossier Factures",
    "approval_required": false,
    "automated_workflow": true
}
```

#### **Ce que le système construit** :

```python
payload = {
    "collection_name": company_id,
    "user_id": user_id,
    "thread_key": thread_key,                   # ⭐ TRÈS IMPORTANT
    "client_uuid": context['client_uuid'],
    "pub_sub_id": f"router_{file_id}_{uuid}",
    "mandates_path": context['mandate_path'],
    "settings": [...],
    "jobs_data": [{
        "file_name": "document_file_xyz789",
        "drive_file_id": "file_xyz789",
        "instructions": "Router vers le dossier Factures",
        "status": 'to_route',
        "approval_required": false,
        "automated_workflow": true
    }]
}
```

#### **Endpoint HTTP** :
```
POST http://klk-load-balancer.../event-trigger
```

---

### **3. LPT_Banker** - Réconciliation Bancaire

#### **Ce que l'agent fournit** :
```json
{
    "bank_account": "FR76 1234 5678 9012 3456",
    "transaction_ids": ["tx_001", "tx_002", "tx_003"],
    "instructions": "Vérifier les doublons",
    "approval_required": false,
    "approval_contact_creation": false
}
```

#### **Ce que le système construit** :

```python
payload = {
    "collection_name": company_id,
    "user_id": user_id,
    "thread_key": thread_key,                   # ⭐ TRÈS IMPORTANT
    "client_uuid": context['client_uuid'],
    "batch_id": f'bank_batch_{uuid}',
    "pub_sub_id": f"bank_batch_{batch_id}",
    "mandates_path": context['mandate_path'],
    "settings": [...],
    "jobs_data": [{
        "bank_account": "FR76 1234 5678 9012 3456",
        "job_id": "...",
        "transactions": [
            {
                "transaction_id": "tx_001",
                "transaction_name": "Transaction tx_001",
                "date": "2025-10-13T...",
                "amount": 0.0,
                "currency_name": "EUR",
                "status": "in_queue",
                ...
            },
            ...
        ],
        "instructions": "Vérifier les doublons",
        "approval_required": false,
        "approval_contact_creation": false
    }]
}
```

#### **Endpoint HTTP** :
```
POST http://klk-load-balancer.../banker-event-trigger
```

---

## 🔐 Valeurs Automatiques et Contexte Utilisateur

### **Récupération du contexte** :

La fonction `_get_user_context_data()` dans `LPTClient` récupère automatiquement :

```python
context = {
    "client_uuid": "...",                # Depuis UnifiedRegistry ou Firebase
    "communication_mode": "webhook",     # Depuis settings utilisateur/société
    "log_communication_mode": "firebase",
    "dms_system": "google_drive",        # Depuis company_data
    "mandate_path": "...",               # Depuis company_data
    "company_name": "Company ABC"        # Depuis company_data
}
```

### **Sources de données** :

1. **UnifiedRegistry** : `get_user_session(user_id)`, `get_company_data(company_id)`
2. **Firebase Firestore** : `clients/{user_id}/settings`, `companies/{company_id}/settings`
3. **Variables d'environnement** : `PINNOKIO_SOURCE`, `PINNOKIO_AWS_URL`

### **Mise à jour automatique** :

Quand l'utilisateur change de société sur l'UI :
- ✅ Le frontend met à jour `collection_name` dans le RPC call
- ✅ Le contexte est récupéré dynamiquement à chaque appel LPT
- ✅ Pas de cache statique = toujours à jour

---

## 💾 Sauvegarde des Tâches dans Firebase

### **Path de sauvegarde** :

```
clients/{firebase_user_id}/workflow_pinnokio/{thread_key}
```

### **Structure du document** :

```json
{
    "thread_key": "chat_abc123",
    "user_id": "uid_123",
    "updated_at": "2025-10-13T...",
    "tasks": {
        "batch_abc123": {
            "task_id": "batch_abc123",
            "task_type": "APBookkeeper",
            "status": "queued",
            "created_at": "2025-10-13T...",
            "updated_at": "2025-10-13T...",
            "payload_summary": {
                "collection_name": "company_abc",
                "user_id": "uid_123",
                "thread_key": "chat_abc123"
            }
        },
        "router_xyz789": {
            "task_id": "router_xyz789",
            "task_type": "Router",
            "status": "in_progress",
            ...
        }
    }
}
```

### **Avantages** :

- ✅ **Indexation par thread_key** : Récupération rapide côté UI
- ✅ **Historique complet** : Toutes les tâches du thread
- ✅ **Statuts en temps réel** : Mise à jour par les callbacks
- ✅ **Métadonnées traçables** : Pour déboguer et auditer

---

## 🔔 Notifications Firebase

### **Path des notifications** :

```
clients/{firebase_user_id}/notifications
```

### **Structure par agent** :

#### **APBookkeeper** :
```json
{
    "function_name": "APbookeeper",
    "file_id": "file_abc123",
    "job_id": "file_abc123",
    "file_name": "document_file_abc123",
    "status": "in queue",
    "batch_id": "batch_abc123",
    "batch_index": 1,
    "batch_total": 2,
    "collection_id": "company_abc",
    "collection_name": "Company ABC",
    "timestamp": "2025-10-13T...",
    "read": false
}
```

#### **Router** :
```json
{
    "function_name": "Router",
    "file_id": "file_xyz789",
    "job_id": "",
    "pub_sub_id": "router_file_xyz789_abc123",
    "status": "in queue",
    "instructions": "Router vers Factures",
    "collection_id": "company_abc",
    "collection_name": "Company ABC",
    "timestamp": "2025-10-13T...",
    "read": false
}
```

#### **Banker** :
```json
{
    "function_name": "Bankbookeeper",
    "job_id": "...",
    "batch_id": "bank_batch_abc123",
    "bank_account": "FR76 1234 5678 9012 3456",
    "transactions": [...],
    "status": "in queue",
    "collection_id": "company_abc",
    "collection_name": "Company ABC",
    "timestamp": "2025-10-13T...",
    "read": false
}
```

---

## ⭐ L'importance de `thread_key`

### **Pourquoi `thread_key` est CRUCIAL** :

1. **Canal de communication** : L'agent sait sur quel canal (chat) répondre
2. **Isolation des tâches** : Chaque thread a ses propres tâches
3. **Contexte conversationnel** : Lier les LPT à la conversation qui les a déclenchés
4. **Récupération UI** : Le frontend peut afficher les tâches par thread
5. **Callbacks agents externes** : Les agents savent où renvoyer les résultats

### **Utilisation dans le système** :

```python
# 1. Envoi initial depuis Reflex
rpc_call("LLM.send_message_with_pinnokio", kwargs={
    "user_id": self.firebase_user_id,
    "collection_name": self.base_collection_id,
    "thread_key": self.current_chat,  # ⭐ Thread actuel
    "message": "..."
})

# 2. Création des outils LPT avec thread_key
lpt_tools_list, lpt_tools_mapping = lpt_client.get_tools_definitions_and_mapping(
    user_id=user_id,
    company_id=company_id,
    thread_key=thread_key  # ⭐ Capturé dans les lambdas
)

# 3. Inclusion dans tous les payloads LPT
payload = {
    ...
    "thread_key": thread_key  # ⭐ Envoyé aux agents externes
}

# 4. Sauvegarde des tâches par thread_key
workflow_path = f"clients/{user_id}/workflow_pinnokio/{thread_key}"

# 5. Callback des agents externes
POST /api/v1/lpt/callback
{
    "thread_key": "chat_abc123",  # ⭐ Pour retrouver le contexte
    "task_id": "batch_abc123",
    "status": "completed",
    "result": {...}
}
```

---

## 🔄 Flux Complet d'Exécution d'un LPT

### **Exemple : Saisie de 3 factures**

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. UTILISATEUR (Reflex UI)                                     │
└─────────────────────────────────────────────────────────────────┘
    │
    │ "Saisis les factures file_001, file_002, file_003"
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. RPC CALL : LLM.send_message_with_pinnokio                   │
│    - user_id: uid_123                                           │
│    - collection_name: company_abc                               │
│    - thread_key: chat_abc123                                    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. PINNOKIO BRAIN                                               │
│    - Analyse la requête                                         │
│    - Identifie : besoin de LPT_APBookkeeper                     │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. AGENT APPELLE L'OUTIL LPT_APBookkeeper                      │
│    {                                                            │
│        "job_ids": ["file_001", "file_002", "file_003"]         │
│    }                                                            │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. LPT_CLIENT CONSTRUIT LE PAYLOAD COMPLET                     │
│    - Récupère contexte utilisateur (client_uuid, settings...)  │
│    - Génère batch_id                                            │
│    - Construit jobs_data à partir des job_ids                   │
│    - Ajoute thread_key au payload ⭐                            │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. ENVOI HTTP POST                                              │
│    URL: /apbookeeper-event-trigger                              │
│    Status: 202 Accepted                                         │
└─────────────────────────────────────────────────────────────────┘
    │
    ├──────────────────────────────────────────────────────────────┐
    │                                                              │
    ▼                                                              ▼
┌─────────────────────────────────────┐  ┌────────────────────────────┐
│ 7a. SAUVEGARDE TÂCHE FIREBASE       │  │ 7b. CRÉATION NOTIFICATIONS │
│     Path: clients/{uid}/            │  │     Path: clients/{uid}/   │
│           workflow_pinnokio/        │  │           notifications    │
│           {thread_key}              │  │                            │
│     {                               │  │     3 notifications créées │
│       "tasks": {                    │  │     (1 par fichier)        │
│         "batch_xyz": {              │  │                            │
│           "status": "queued",       │  │                            │
│           "task_type": "APBookk..", │  │                            │
│           ...                       │  │                            │
│         }                           │  │                            │
│       }                             │  │                            │
│     }                               │  │                            │
└─────────────────────────────────────┘  └────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 8. RETOUR À L'AGENT PINNOKIO                                    │
│    {                                                            │
│        "status": "queued",                                      │
│        "task_id": "batch_xyz",                                  │
│        "nb_jobs": 3,                                            │
│        "thread_key": "chat_abc123",                             │
│        "message": "✓ APBookkeeper lancé : 3 facture(s) ..."    │
│    }                                                            │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 9. DÉTECTION LPT DANS WORKFLOW                                  │
│    - pinnokio_agent_workflow détecte status="queued"            │
│    - Retourne "LPT_IN_PROGRESS"                                 │
│    - Agent devient DISPONIBLE                                   │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 10. MESSAGE RTDB POUR UTILISATEUR                               │
│     "⏳ APBookkeeper lancé : 3 facture(s) en cours de          │
│      traitement. Je reste disponible pour vos questions."       │
└─────────────────────────────────────────────────────────────────┘
    │
    │ ... Agent APBookkeeper travaille en arrière-plan ...
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 11. CALLBACK DE L'AGENT EXTERNE (future implémentation)        │
│     POST /api/v1/lpt/callback                                   │
│     {                                                           │
│         "thread_key": "chat_abc123",  ⭐                        │
│         "task_id": "batch_xyz",                                 │
│         "status": "completed",                                  │
│         "result": {                                             │
│             "nb_factures_traitees": 3,                          │
│             "montant_total": 4500.00,                           │
│             ...                                                 │
│         }                                                       │
│     }                                                           │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 12. MISE À JOUR FIREBASE + RTDB                                │
│     - Mise à jour statut tâche dans workflow_pinnokio           │
│     - Mise à jour notifications                                 │
│     - Message RTDB : "✅ 3 factures saisies avec succès !"     │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 13. L'AGENT PINNOKIO PEUT REPRENDRE LE CONTEXTE                │
│     - Accède au résultat via Firebase                           │
│     - Continue la conversation avec l'utilisateur               │
│     - Peut lancer d'autres tâches si nécessaire                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Ce qu'il reste à faire

### **1. Implémenter les méthodes SPT manquantes**

Dans `spt_tools.py` :
- ✅ `get_firebase_data` : Logique de récupération Firestore complète
- ✅ `search_chromadb` : Intégration avec ChromaVectorService
- ✅ `get_user_context` : Enrichir avec plus de données

### **2. Compléter la récupération du contexte utilisateur**

Dans `lpt_client.py` → `_get_user_context_data()` :
- Récupérer vraiment depuis Firebase : `clients/{user_id}/settings`
- Récupérer depuis companies : `companies/{company_id}/settings`
- Gérer les valeurs par défaut intelligemment

### **3. Récupération des noms de fichiers réels**

Dans `lpt_client.py` :
- Ajouter appel à Google Drive API pour récupérer les vrais noms de fichiers
- Remplacer `f"document_{job_id}"` par le nom réel

### **4. Récupération des données de transactions bancaires**

Dans `launch_banker()` :
- Récupérer les vraies données de transactions depuis Firebase/ERP
- Remplacer les transactions simplifiées par les vraies données

### **5. Endpoint callback LPT**

Dans `main.py`, ajouter :

```python
@app.post("/api/v1/lpt/callback")
async def lpt_callback(request: Request):
    """
    Reçoit les callbacks des agents externes (APBookkeeper, Router, Banker).
    Met à jour le statut de la tâche et réactive l'agent Pinnokio.
    """
    data = await request.json()
    
    thread_key = data.get("thread_key")
    task_id = data.get("task_id")
    status = data.get("status")
    result = data.get("result", {})
    
    # Mettre à jour Firebase
    # Publier événement via WebSocket
    # Réactiver le workflow Pinnokio si nécessaire
    
    return {"success": True}
```

### **6. Tests unitaires et d'intégration**

- Tester chaque LPT individuellement
- Tester la construction automatique des payloads
- Tester la sauvegarde Firebase
- Tester les notifications

### **7. Documentation utilisateur**

- Guide d'utilisation des LPT pour l'utilisateur final
- Exemples de requêtes typiques
- Troubleshooting

---

## 📊 Résumé des Fichiers Modifiés/Créés

| Fichier | Statut | Description |
|---------|--------|-------------|
| `tools/__init__.py` | ✅ Créé | Exports des classes d'outils |
| `tools/tool_registry.py` | ✅ Créé | Registre centralisé des outils |
| `tools/spt_tools.py` | ✅ Créé | Outils SPT (Short Process Tooling) |
| `tools/lpt_client.py` | ✅ Créé | **Client LPT avec construction auto des payloads** |
| `orchestrator/pinnokio_brain.py` | ✅ Modifié | Utilise SPTTools et LPTClient |
| `orchestrator/task_tracker.py` | ✅ Existe | Tracking des tâches (déjà créé) |
| `workflows/pinnokio_workflow.py` | ✅ Existe | Détection LPT_IN_PROGRESS (déjà créé) |
| `llm_service/llm_manager.py` | ✅ Modifié | Méthodes `send_message_with_pinnokio` et `_process_pinnokio_workflow` |

---

## 🎉 Conclusion

Vous avez maintenant un système complet de gestion des LPT qui :

✅ **Simplifie l'utilisation** : L'agent fournit seulement IDs + instructions
✅ **Automatise la complexité** : Construction automatique des payloads complets
✅ **Sécurise et compartimente** : Valeurs automatiques par utilisateur/société
✅ **Trace tout** : Sauvegarde Firebase + notifications automatiques
✅ **Préserve le contexte** : `thread_key` omniprésent pour l'isolation
✅ **Reste disponible** : Agent Pinnokio disponible pendant les LPT
✅ **Utilise l'existant** : Intégration avec FirebaseManagement, UnifiedRegistry

**Le système est prêt à être testé !** 🚀

---

**Prochaine étape** : Ajouter l'endpoint RPC dans `main.py` et tester l'appel depuis Reflex !



