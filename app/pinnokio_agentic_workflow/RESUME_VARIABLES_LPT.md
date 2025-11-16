# 📊 Résumé : Constitution des Variables pour les LPTs

## 🎯 Tableau récapitulatif

| Variable | Source | Méthode | Chemin Firebase | Quand |
|----------|--------|---------|----------------|-------|
| **user_id** | Reflex | `self.firebase_user_id` | N/A | ÉTAPE 1 (RPC) |
| **collection_name** | Reflex | `self.base_collection_id` | N/A | ÉTAPE 1 (RPC) |
| **thread_key** | Reflex | `self.current_chat` | N/A | ÉTAPE 1 (RPC) |
| **client_uuid** | Firebase | Accès direct | `clients/{uid}/bo_clients/{uid}/client_uuid` | ÉTAPE 8.1 |
| **mandate_path** | Firebase | `reconstruct_full_client_profile` | `bo_clients/.../mandates/.../contact_space_id` | ÉTAPE 8.2 |
| **communication_mode** | Firebase | `reconstruct_full_client_profile` | `.../mandates/.../erp/.../communication_mode` | ÉTAPE 8.2 |
| **log_communication_mode** | Firebase | `reconstruct_full_client_profile` | `.../mandates/.../erp/.../log_communication_mode` | ÉTAPE 8.2 |
| **dms_system** | Firebase | `reconstruct_full_client_profile` | `.../mandates/.../erp/.../dms_system` | ÉTAPE 8.2 |
| **drive_space_parent_id** | Firebase | `reconstruct_full_client_profile` | `.../mandates/.../drive_space_parent_id` | ÉTAPE 8.2 |
| **bank_erp** | Firebase | `reconstruct_full_client_profile` | `.../mandates/.../bank_erp` | ÉTAPE 8.2 |

---

## 🔍 Références dans le code

### **1️⃣ Création initiale du `client_uuid`**

**Fichier** : `app/firebase_providers.py:3045-3058`

```python
def check_and_create_client_document(self, user_data):
    # Ligne 3045
    client_uuid = f"client_{str(uuid.uuid4())[:8]}"
    
    # Ligne 3053-3058
    sub_doc_ref = user_ref.collection("bo_clients").document(user_id)
    sub_doc_ref.set({
        "client_name": user_data["displayName"],
        "client_uuid": client_uuid  # ← Stocké ici
    })
```

**Chemin créé** : `clients/{user_id}/bo_clients/{user_id}/`

---

### **2️⃣ Récupération du `client_uuid`**

**Fichier** : `app/pinnokio_agentic_workflow/tools/lpt_client.py:284-293`

```python
async def _get_user_context_data(self, user_id: str, company_id: str):
    # Ligne 286-287
    doc_ref = firebase_service.db.collection(
        f'clients/{user_id}/bo_clients'
    ).document(user_id)  # ← Document ID == user_id
    
    doc = await asyncio.to_thread(doc_ref.get)
    
    # Ligne 290-293
    if doc.exists:
        client_data = doc.to_dict()
        client_uuid = client_data.get('client_uuid')  # ✅ Récupéré !
```

**Clé importante** : Le `document_id` est égal au `user_id`, donc accès direct possible.

---

### **3️⃣ Récupération du contexte complet**

**Fichier** : `app/firebase_providers.py:7043-7088`

```python
def reconstruct_full_client_profile(self, user_id, client_uuid, contact_space_id):
    # Ligne 7050 : Récupérer le client
    clients_query = self.db.collection(f'clients/{user_id}/bo_clients') \
        .where('client_uuid', '==', client_uuid).limit(1).get()
    
    # Ligne 7061 : Récupérer le mandat (via contact_space_id)
    mandates_query = self.db.collection(f'bo_clients/{client_id}/mandates') \
        .where('contact_space_id', '==', contact_space_id).get()
    
    # Ligne 7073 : Récupérer l'ERP
    erp_query = self.db.collection(
        f'bo_clients/{client_id}/mandates/{mandate_id}/erp'
    ).get()
    
    return full_profile  # Contient tous les champs
```

**Utilisé dans** : `lpt_client.py:304-309`

---

### **4️⃣ Extraction et formatage**

**Fichier** : `app/pinnokio_agentic_workflow/tools/lpt_client.py:316-335`

```python
context = {
    # Depuis full_profile
    "client_uuid": full_profile.get("client_uuid", client_uuid),
    "company_name": full_profile.get("contact_space_name"),
    "mandate_path": full_profile.get("mandate_contact_space_id"),
    "drive_space_parent_id": full_profile.get("mandate_drive_space_parent_id"),
    "dms_system": full_profile.get("erp_dms_system", "google_drive"),
    "communication_mode": full_profile.get("erp_communication_mode", "webhook"),
    "log_communication_mode": full_profile.get("erp_log_communication_mode", "firebase"),
    "bank_erp": full_profile.get("mandate_bank_erp") or full_profile.get("erp_bank_erp"),
}
```

---

## 🚀 Flux simplifié (3 appels)

```
1. Reflex → RPC("LLM.send_message")
   ↓ user_id, collection_name, thread_key
   
2. LPTClient._get_user_context_data()
   ↓
   2.1. Firestore: clients/{uid}/bo_clients/{uid} → client_uuid
   ↓
   2.2. reconstruct_full_client_profile(uid, client_uuid, collection_name)
        ↓
        ├─ Firestore: clients/{uid}/bo_clients WHERE client_uuid
        ├─ Firestore: bo_clients/.../mandates WHERE contact_space_id
        └─ Firestore: .../mandates/.../erp
   ↓
   2.3. Retour: context{} avec TOUTES les variables
   
3. Payload LPT complet envoyé via HTTP
```

---

## ✅ Garanties

| Variable | Garantie | Fallback |
|----------|----------|----------|
| `user_id`, `collection_name`, `thread_key` | ✅ Toujours présentes (Reflex) | N/A |
| `client_uuid` | ✅ Créé à la première connexion | `fallback_{user_id[:8]}` |
| Autres (`mandate_path`, etc.) | ⚠️ Dépend de la configuration mandat | Valeurs par défaut |

---

## 🔧 Méthodes utilisées

| Méthode | Fichier | Ligne | Rôle |
|---------|---------|-------|------|
| `check_and_create_client_document` | `firebase_providers.py` | 3012 | Crée le `client_uuid` initial |
| `reconstruct_full_client_profile` | `firebase_providers.py` | 7043 | Récupère tout le contexte |
| `_get_user_context_data` | `lpt_client.py` | 260 | Orchestre la récupération |
| `_get_default_context` | `lpt_client.py` | 350 | Fallback si erreur |
| `get_tools_definitions_and_mapping` | `lpt_client.py` | 33 | Emprisonne les variables dans les lambdas |

---

## 🎯 Ce que l'agent NE fournit PAS

L'agent LLM **ne fournit jamais** ces variables :
- ❌ `user_id`
- ❌ `collection_name`
- ❌ `thread_key`
- ❌ `client_uuid`
- ❌ `mandate_path`
- ❌ `communication_mode`
- ❌ Toutes les variables de configuration

**L'agent fournit SEULEMENT** :
- ✅ IDs des pièces (`invoice_ids`, `file_ids`, `transaction_ids`, etc.)
- ✅ Instructions optionnelles (`instructions`)
- ✅ Paramètres spécifiques (`approval_required`, etc.)

---

## 📝 Exemple concret

### Entrée de l'agent :
```json
{
  "tool_use": "LPT_APBookkeeper",
  "input": {
    "invoice_ids": ["inv_001", "inv_002", "inv_003"]
  }
}
```

### Payload complet envoyé au LPT :
```json
{
  "uid": "user_abc123",                    // ← Reflex
  "collection_name": "company_xyz789",      // ← Reflex
  "thread_key": "chat_001",                 // ← Reflex
  "client_uuid": "client_def456",           // ← Firebase (direct)
  "mandates_path": "company_xyz789",        // ← Firebase (reconstruct_full_client_profile)
  "communication_mode": "webhook",          // ← Firebase (reconstruct_full_client_profile)
  "log_communication_mode": "firebase",     // ← Firebase (reconstruct_full_client_profile)
  "dms_system": "google_drive",             // ← Firebase (reconstruct_full_client_profile)
  "invoice_ids": ["inv_001", "inv_002", "inv_003"]  // ← Agent
}
```

---

**Document complet** : `FLUX_VARIABLES_CONTEXTUELLES.md`


