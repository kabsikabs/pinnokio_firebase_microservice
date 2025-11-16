# 🔄 Flux des Variables Contextuelles pour les LPTs

## 📊 Vue d'ensemble

Ce document décrit **exactement** où et comment les variables contextuelles sont constituées, récupérées et utilisées dans les payloads LPT.

---

## 🎯 Variables requises pour les LPTs

| Variable | Source | Étape de constitution |
|----------|--------|----------------------|
| `user_id` (uid) | **Reflex** | ÉTAPE 1 : RPC call |
| `collection_name` | **Reflex** | ÉTAPE 1 : RPC call |
| `thread_key` | **Reflex** | ÉTAPE 1 : RPC call |
| `client_uuid` | **Firebase** | ÉTAPE 8 : `_get_user_context_data()` |
| `mandate_path` | **Firebase** | ÉTAPE 8 : `reconstruct_full_client_profile()` |
| `communication_mode` | **Firebase** | ÉTAPE 8 : `reconstruct_full_client_profile()` |
| `log_communication_mode` | **Firebase** | ÉTAPE 8 : `reconstruct_full_client_profile()` |
| `dms_system` | **Firebase** | ÉTAPE 8 : `reconstruct_full_client_profile()` |
| `drive_space_parent_id` | **Firebase** | ÉTAPE 8 : `reconstruct_full_client_profile()` |
| `bank_erp` | **Firebase** | ÉTAPE 8 : `reconstruct_full_client_profile()` |

---

## 🗺️ Flux complet (10 étapes)

### **ÉTAPE 1 : Reflex envoie la requête**

**Fichier** : Frontend Reflex (État utilisateur)

```python
# Dans Reflex State
await self.rpc_call(
    "LLM.send_message",
    user_id=self.firebase_user_id,          # ✅ Variable 1
    collection_name=self.base_collection_id, # ✅ Variable 2
    thread_key=self.current_chat,           # ✅ Variable 3
    message="Analyse mes 15 factures fournisseurs"
)
```

**Garantie** : Ces 3 variables sont **déjà présentes** dans l'état Reflex après l'authentification Firebase.

---

### **ÉTAPE 2 : RPC Endpoint reçoit la requête**

**Fichier** : `app/main.py:449`

```python
@app.post("/rpc", response_model=RpcResponse)
async def rpc_endpoint(req: RpcRequest, ...):
    # La requête contient : user_id, collection_name, thread_key
    func, _ns = _resolve_method(req.method)  # → "LLM.send_message"
    result = await func(*(req.args or []), **(req.kwargs or {}))
```

---

### **ÉTAPE 3 : LLMManager.send_message**

**Fichier** : `app/llm_service/llm_manager.py:412`

```python
async def send_message(
    self,
    user_id: str,        # ← ÉTAPE 1
    collection_name: str, # ← ÉTAPE 1
    thread_key: str,     # ← ÉTAPE 1
    message: str,
    ...
):
    # Lance le traitement agentic en arrière-plan
    task = asyncio.create_task(
        self._process_message_with_agentic_streaming(
            user_id=user_id,
            collection_name=collection_name,
            thread_key=thread_key,
            message=message,
            ...
        )
    )
```

---

### **ÉTAPE 4 : Création du PinnokioBrain**

**Fichier** : `app/llm_service/llm_manager.py` (dans `_process_message_with_agentic_streaming`)

```python
from ..pinnokio_agentic_workflow.orchestrator.pinnokio_brain import PinnokioBrain

brain = PinnokioBrain(
    firebase_user_id=user_id,       # ← ÉTAPE 1
    collection_name=collection_name # ← ÉTAPE 1
)

# Créer les outils (SPT + LPT)
tools_definitions, tools_mapping = brain.create_workflow_tools(thread_key)
```

---

### **ÉTAPE 5 : PinnokioBrain crée les outils LPT**

**Fichier** : `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py:449`

```python
def create_workflow_tools(self, thread_key: str) -> Tuple[List[Dict], Dict]:
    # Créer le client LPT
    lpt_client = LPTClient()
    
    # ⭐ EMPRISONNEMENT DES VARIABLES DANS LES LAMBDAS ⭐
    lpt_defs, lpt_mapping = lpt_client.get_tools_definitions_and_mapping(
        user_id=self.firebase_user_id,    # ← Capturé dans les lambdas
        company_id=self.collection_name,  # ← Capturé dans les lambdas
        thread_key=thread_key             # ← Capturé dans les lambdas
    )
```

---

### **ÉTAPE 6 : LPTClient génère les définitions d'outils**

**Fichier** : `app/pinnokio_agentic_workflow/tools/lpt_client.py:33`

```python
def get_tools_definitions_and_mapping(
    self, 
    user_id: str,      # ← ÉTAPE 5
    company_id: str,   # ← ÉTAPE 5
    thread_key: str    # ← ÉTAPE 5
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    
    # Définitions simplifiées pour l'agent (seulement les IDs et instructions)
    tools_list = [
        {
            "name": "LPT_APBookkeeper",
            "input_schema": {
                "properties": {
                    "invoice_ids": {"type": "array"},  # ← Agent fournit SEULEMENT ça
                    "instructions": {"type": "string"}  # ← Optionnel
                }
            }
        },
        # ... autres LPTs
    ]
    
    # ⭐ MAPPING AVEC LAMBDAS (emprisonnement des variables) ⭐
    tools_mapping = {
        "LPT_APBookkeeper": lambda **kwargs: self.launch_apbookeeper(
            user_id=user_id,        # ← Capturé (ÉTAPE 1 via ÉTAPE 5)
            company_id=company_id,  # ← Capturé (ÉTAPE 1 via ÉTAPE 5)
            thread_key=thread_key,  # ← Capturé (ÉTAPE 1 via ÉTAPE 5)
            **kwargs                # ← invoice_ids, instructions (fournis par l'agent)
        ),
        # ... autres mappings
    }
    
    return tools_list, tools_mapping
```

**Important** : Les variables `user_id`, `company_id`, `thread_key` sont **automatiquement ajoutées** grâce aux lambdas. L'agent n'a **jamais besoin** de les fournir.

---

### **ÉTAPE 7 : Agent choisit un outil LPT**

**Fichier** : Streaming LLM (Anthropic/OpenAI)

```json
{
  "tool_use": {
    "name": "LPT_APBookkeeper",
    "input": {
      "invoice_ids": ["inv_001", "inv_002", "inv_003", "..."],
      "instructions": "Priorité aux factures > 5000€"
    }
  }
}
```

**Note** : L'agent fournit **UNIQUEMENT** les `invoice_ids` et les `instructions`. Les autres variables sont gérées automatiquement.

---

### **ÉTAPE 8 : LPTClient.launch_apbookeeper**

**Fichier** : `app/pinnokio_agentic_workflow/tools/lpt_client.py:376`

```python
async def launch_apbookeeper(
    self,
    user_id: str,         # ← Fourni par le lambda (ÉTAPE 6)
    company_id: str,      # ← Fourni par le lambda (ÉTAPE 6)
    thread_key: str,      # ← Fourni par le lambda (ÉTAPE 6)
    invoice_ids: List[str],  # ← Fourni par l'agent (ÉTAPE 7)
    instructions: str = None # ← Fourni par l'agent (ÉTAPE 7)
) -> Dict[str, Any]:
    
    # ⭐ RÉCUPÉRATION DU CONTEXTE COMPLET ⭐
    context = await self._get_user_context_data(user_id, company_id)
    # context contient maintenant :
    # - client_uuid ✅
    # - mandate_path ✅
    # - communication_mode ✅
    # - log_communication_mode ✅
    # - dms_system ✅
    # - drive_space_parent_id ✅
    # - bank_erp ✅
    
    # Construction du payload complet
    payload = {
        "uid": user_id,                                      # ✅ ÉTAPE 1
        "collection_name": company_id,                        # ✅ ÉTAPE 1
        "thread_key": thread_key,                             # ✅ ÉTAPE 1
        "client_uuid": context['client_uuid'],                # ✅ ÉTAPE 8
        "mandates_path": context['mandate_path'],             # ✅ ÉTAPE 8
        "communication_mode": context['communication_mode'],  # ✅ ÉTAPE 8
        "log_communication_mode": context['log_communication_mode'], # ✅ ÉTAPE 8
        "dms_system": context['dms_system'],                  # ✅ ÉTAPE 8
        "invoice_ids": invoice_ids,                           # ✅ AGENT (ÉTAPE 7)
        "instructions": instructions                          # ✅ AGENT (ÉTAPE 7)
    }
    
    # Envoyer la requête HTTP à l'agent APBookkeeper externe
    async with aiohttp.ClientSession() as session:
        response = await session.post(
            f"{self.aws_url}/apbookeeper",
            json=payload
        )
```

---

### **ÉTAPE 8 DÉTAILLÉE : `_get_user_context_data`**

**Fichier** : `app/pinnokio_agentic_workflow/tools/lpt_client.py:260`

#### **Sous-étape 8.1 : Récupération du `client_uuid`**

```python
async def _get_user_context_data(self, user_id: str, company_id: str):
    firebase_service = FirebaseManagement()
    
    # ⭐ ACCÈS DIRECT AU client_uuid ⭐
    # Chemin : clients/{user_id}/bo_clients/{user_id}
    # Note : Le document_id est égal au user_id (voir check_and_create_client_document)
    doc_ref = firebase_service.db.collection(
        f'clients/{user_id}/bo_clients'
    ).document(user_id)
    
    doc = await asyncio.to_thread(doc_ref.get)
    
    client_uuid = None
    if doc.exists:
        client_data = doc.to_dict()
        client_uuid = client_data.get('client_uuid')  # ✅ client_uuid récupéré !
```

**Structure Firebase** :
```
clients/{user_id}/
└── bo_clients/{user_id}/       ← Document ID == user_id
    ├── client_name: "Jean Dupont"
    ├── created_at: Timestamp
    └── client_uuid: "client_abc12345"  ← ⭐ LA VALEUR CHERCHÉE ⭐
```

**Référence de création** : `firebase_providers.py:3045-3058` (méthode `check_and_create_client_document`)

#### **Sous-étape 8.2 : Récupération du contexte complet**

```python
    # ⭐ UTILISER reconstruct_full_client_profile ⭐
    full_profile = await asyncio.to_thread(
        firebase_service.reconstruct_full_client_profile,
        user_id,
        client_uuid,         # ← Récupéré en 8.1
        company_id           # ← contact_space_id (collection_name)
    )
    
    # full_profile contient maintenant :
    # - client_uuid
    # - client_name
    # - contact_space_name
    # - mandate_* (tous les champs du mandat)
    # - erp_* (tous les champs ERP)
```

**Référence** : `firebase_providers.py:7043-7088` (méthode `reconstruct_full_client_profile`)

**Flux interne de `reconstruct_full_client_profile`** :

1. **Requête clients** :
   ```python
   # Ligne 7050
   clients_query = self.db.collection(f'clients/{user_id}/bo_clients') \
       .where('client_uuid', '==', client_uuid) \
       .limit(1).get()
   ```

2. **Requête mandats** :
   ```python
   # Ligne 7061
   mandates_query = self.db.collection(f'bo_clients/{client_id}/mandates') \
       .where('contact_space_id', '==', contact_space_id) \
       .get()
   # contact_space_id correspond à collection_name (ÉTAPE 1)
   ```

3. **Requête ERP** :
   ```python
   # Ligne 7073
   erp_query = self.db.collection(
       f'bo_clients/{client_id}/mandates/{mandate_id}/erp'
   ).get()
   ```

#### **Sous-étape 8.3 : Extraction et formatage**

```python
    # ⭐ EXTRACTION DES VALEURS POUR LES LPTs ⭐
    context = {
        # Identifiants
        "client_uuid": full_profile.get("client_uuid", client_uuid),
        "company_name": full_profile.get("contact_space_name") or full_profile.get("client_name", company_id),
        
        # Chemins et systèmes
        "mandate_path": full_profile.get("mandate_contact_space_id", company_id),
        "drive_space_parent_id": full_profile.get("mandate_drive_space_parent_id"),
        "dms_system": full_profile.get("erp_dms_system", "google_drive"),
        
        # Modes de communication
        "communication_mode": full_profile.get("erp_communication_mode", "webhook"),
        "log_communication_mode": full_profile.get("erp_log_communication_mode", "firebase"),
        
        # ERP
        "bank_erp": full_profile.get("mandate_bank_erp") or full_profile.get("erp_bank_erp"),
    }
    
    return context
```

**Garantie** : Toutes les variables sont maintenant disponibles !

---

### **ÉTAPE 9 : Sauvegarde de la tâche dans Firebase**

**Fichier** : `app/pinnokio_agentic_workflow/tools/lpt_client.py` (dans `launch_apbookeeper`)

```python
# Après l'envoi HTTP, sauvegarder la tâche pour suivi UI
await self._save_lpt_task_to_firebase(
    user_id=user_id,
    thread_key=thread_key,
    task_data={
        "task_id": job_id,
        "tool_name": "LPT_APBookkeeper",
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "invoice_ids": invoice_ids,
        "company_name": context['company_name']
    }
)
```

**Chemin Firebase** : `clients/{user_id}/workflow_pinnokio/{thread_key}/tasks/{task_id}`

---

### **ÉTAPE 10 : Réponse de l'agent externe (LPT)**

**Fichier** : Agent externe (APBookkeeper, Router, Banker, etc.)

L'agent externe traite la requête et renvoie une réponse via HTTP :

```python
# Réponse HTTP de l'agent APBookkeeper
{
    "status": "success",
    "task_id": "task_abc123",
    "message": "15 factures traitées avec succès",
    "thread_key": "chat_xyz789"  # ← Permet de router la réponse
}
```

Cette réponse est capturée par un webhook ou un endpoint de callback dans le microservice, qui met à jour la tâche dans Firebase et notifie l'utilisateur via WebSocket.

---

## 📍 Mapping des chemins Firebase

| Variable | Chemin Firebase | Exemple de valeur |
|----------|----------------|-------------------|
| `client_uuid` | `clients/{uid}/bo_clients/{uid}/client_uuid` | `"client_abc12345"` |
| `mandate_path` | `bo_clients/{client_id}/mandates/{mandate_id}/contact_space_id` | `"company_xyz789"` |
| `communication_mode` | `bo_clients/{client_id}/mandates/{mandate_id}/erp/{erp_doc}/communication_mode` | `"webhook"` |
| `log_communication_mode` | `bo_clients/{client_id}/mandates/{mandate_id}/erp/{erp_doc}/log_communication_mode` | `"firebase"` |
| `dms_system` | `bo_clients/{client_id}/mandates/{mandate_id}/erp/{erp_doc}/dms_system` | `"google_drive"` |
| `drive_space_parent_id` | `bo_clients/{client_id}/mandates/{mandate_id}/drive_space_parent_id` | `"1A2B3C4D5E"` |
| `bank_erp` | `bo_clients/{client_id}/mandates/{mandate_id}/bank_erp` | `"qonto"` |

---

## 🎯 Points clés à retenir

1. **Variables ÉTAPE 1 (Reflex)** : `user_id`, `collection_name`, `thread_key`
   - ✅ Garanties dès la connexion utilisateur
   - ✅ Transmises via RPC à chaque appel

2. **Variables ÉTAPE 8 (Firebase)** : Toutes les autres
   - ✅ Récupérées automatiquement via `_get_user_context_data()`
   - ✅ Utilisent `reconstruct_full_client_profile()` pour tout obtenir en une fois
   - ✅ Valeurs par défaut si données manquantes

3. **Emprisonnement des variables** : Lambdas dans `get_tools_definitions_and_mapping`
   - ✅ L'agent n'a **JAMAIS** besoin de fournir `uid`, `collection_name`, `thread_key`
   - ✅ L'agent fournit **SEULEMENT** les IDs de pièces et les instructions

4. **Fallback** : Méthode `_get_default_context()`
   - ✅ Génère un `client_uuid` de secours : `f"fallback_{user_id[:8]}"`
   - ✅ Utilise des valeurs par défaut cohérentes

---

## ✅ Checklist d'implémentation

- [x] Modifier `_get_user_context_data()` pour utiliser accès direct + `reconstruct_full_client_profile`
- [x] Ajouter `_get_default_context()` pour les valeurs de secours
- [x] Tester le flux avec un utilisateur réel
- [ ] Vérifier que `reconstruct_full_client_profile` retourne bien tous les champs attendus
- [ ] Ajouter des logs de traçabilité pour le debugging

---

## 🐛 Debugging

### Vérifier la présence du `client_uuid` :

```python
# Dans Firebase Console ou via script
db.collection('clients/{user_id}/bo_clients').document(user_id).get()
```

### Vérifier le profil complet :

```python
firebase_service = FirebaseManagement()
profile = firebase_service.reconstruct_full_client_profile(
    user_id="user_abc123",
    client_uuid="client_xyz789",
    contact_space_id="company_123"
)
print(json.dumps(profile, indent=2))
```

### Vérifier les logs :

```bash
# Rechercher les logs de contexte
grep "Contexte complet récupéré" logs/app.log
grep "client_uuid non trouvé" logs/app.log
```

---

**Date de création** : 14 octobre 2025
**Version** : 1.0
**Auteur** : Intégration LPT Brain Agent


