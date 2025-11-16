# 📊 Rapport détaillé : Gestion de la Workflow Checklist

## 1. 🔄 Mise à jour des données

### 1.1 Flux de données complet

```
📡 RTDB Firebase
    ↓
    [Message type='CMMD']
    ↓
📨 handle_realtime_message() (ligne 20840)
    ↓
    [Détection message_type == 'CMMD']
    ↓
📋 Parsing du message (ligne 21004-21060)
    ↓
    [Extraction de action et params]
    ↓
🎯 Dispatch selon action:
    ├─ "SET_WORKFLOW_CHECKLIST" → Initialisation complète
    └─ "UPDATE_STEP_STATUS" → Mise à jour d'une étape
    ↓
💾 Mise à jour de ChatState.workflow_checklist
    ↓
🎨 Re-render automatique du composant
```

### 1.2 Code de réception des mises à jour

**Localisation** : `base_state.py`, ligne 21004-21060

```python
elif message_type == 'CMMD':
    try:
        content_str = message_data.get('content', '{}')
        content = json.loads(content_str)
        
        # Extract command data
        cmmd = content.get("message", {}).get("cmmd", {})
        action = cmmd.get("action", "")
        params = cmmd.get("params", {})
        
        print(f"Processing command: action={action}, params={params}")
        
        # Handle different command actions
        if action == "SET_PROCESS_STATE":
            # Update router_process_running state
            new_state = params.get("state", False)
            self.onboarding_job_active = new_state
            print(f"Updated chatsate command to: False\nvaleur onborading job: True")

        elif action == "SET_WORKFLOW_CHECKLIST":
            # 🔧 INITIALISATION COMPLÈTE DE LA CHECKLIST
            checklist_data = params.get("checklist", {})
            self.workflow_checklist = checklist_data
            self.checklist_visible = True
            print(f"✅ Checklist du workflow mise à jour: {checklist_data.get('total_steps', 0)} étapes")

        elif action == "UPDATE_STEP_STATUS":
            # 🔄 MISE À JOUR D'UNE ÉTAPE SPÉCIFIQUE
            if self.workflow_checklist:
                step_id = params.get("step_id")
                new_status = params.get("status")
                timestamp = params.get("timestamp")
                message = params.get("message")

                # Trouver et mettre à jour l'étape correspondante
                for step in self.workflow_checklist.get("steps", []):
                    if step.get("id") == step_id:
                        step["status"] = new_status
                        step["timestamp"] = timestamp
                        if message:
                            step["message"] = message
                        break

                print(f"📊 Étape {step_id} mise à jour: {new_status}")
        
        # ✅ CORRECTION : Forcer un yield pour déclencher la réactivité UI
        yield
        
    except json.JSONDecodeError as e:
        print(f"Error parsing command JSON: {e}")
```

## 2. 📦 Structure des données

### 2.1 Format du message RTDB entrant

```json
{
  "type": "CMMD",
  "content": "{\"message\": {\"cmmd\": {\"action\": \"SET_WORKFLOW_CHECKLIST\", \"params\": {...}}}}",
  "timestamp": "2025-01-21T10:30:00Z",
  "sender_id": "system",
  "message_type": "CMMD"
}
```

### 2.2 Structure de `SET_WORKFLOW_CHECKLIST`

```python
{
    "action": "SET_WORKFLOW_CHECKLIST",
    "params": {
        "checklist": {
            "total_steps": 8,
            "steps": [
                {
                    "id": "DOCUMENT_EXTRACTION",
                    "name": "Document Analysis",
                    "status": "completed",  # "pending", "in_progress", "completed", "error"
                    "timestamp": "2025-01-21T10:30:00Z",
                    "message": "Document extracted successfully"
                },
                {
                    "id": "ROUTE_DEFINITION",
                    "name": "Document Routing Validation",
                    "status": "in_progress",
                    "timestamp": "2025-01-21T10:31:00Z",
                    "message": ""
                },
                # ... autres étapes
            ]
        }
    }
}
```

### 2.3 Structure de `UPDATE_STEP_STATUS`

```python
{
    "action": "UPDATE_STEP_STATUS",
    "params": {
        "step_id": "ROUTE_DEFINITION",  # ID de l'étape à mettre à jour
        "status": "completed",           # Nouveau statut
        "timestamp": "2025-01-21T10:32:00Z",
        "message": "Route validated"     # Message optionnel
    }
}
```

## 3. 🎧 Configuration de l'écoute RTDB/Firebase

### 3.1 Initialisation du listener

**Localisation** : `base_state.py`, ligne 20356-20445

```python
@rx.event(background=True)
async def start_realtime_listener(self):
    """
    🎧 Démarre l'écouteur pour les messages Firebase Realtime via le gestionnaire unifié.
    """
    async with self:
        try:
            # Vérification des prérequis
            if not (self.companies_search_id and self.current_chat):
                print("[CHAT_LISTENERS] ❌ Impossible de démarrer l'écouteur")
                return

            # 📋 Récupérer le mode Firebase depuis personal_chats
            mode = 'chats'  # Valeur par défaut
            for chat_info in self.personal_chats:
                if chat_info.get("thread_key") == self.current_chat:
                    mode = chat_info.get("mode", "chats")
                    print(f"[CHAT_LISTENERS] 🔍 Mode Firebase trouvé: {mode}")
                    break
            
            print(f"[CHAT_LISTENERS] 🚀 Démarrage écouteur pour {self.companies_search_id}/{self.current_chat}")
            print(f"[CHAT_LISTENERS]    - chat_mode: {self.chat_mode}")
            print(f"[CHAT_LISTENERS]    - listener_mode: {mode}")

            # Démarrer via le gestionnaire unifié
            from ..listeners.manager import listener_manager
            main_loop = asyncio.get_running_loop()
            
            async def handler(message_data: dict):
                # handle_realtime_message retourne un async generator
                async for _ in self.handle_realtime_message(message_data):
                    pass  # Consommer le generator
            
            await listener_manager.start_chat_listener(
                space_code=self.companies_search_id,  # ← ID de l'entreprise
                thread_key=self.current_chat,         # ← ID du chat/thread
                user_id=self.firebase_user_id,        # ← ID de l'utilisateur
                main_loop=main_loop,
                handler=handler,
                mode=mode,                             # ← Mode Firebase ('chats' ou 'job_chats')
                chat_mode=self.chat_mode,              # ← Mode de chat ('onboarding_chat', etc.)
            )

            # Mémoriser les informations
            self.chat_space_code = self.companies_search_id
            self.chat_thread_key = self.current_chat
            self.Chat_realtime_listener_active = True
            
            print(f"[CHAT_LISTENERS] ✅ Écouteur Realtime démarré avec succès")
            
        except Exception as e:
            print(f"[CHAT_LISTENERS] ❌ Erreur: {e}")
            self.Chat_realtime_listener_active = False
```

### 3.2 Chemin d'écoute Firebase Realtime Database

**Structure du chemin** :
```
/clients/{firebase_user_id}/{mode}/{space_code}/threads/{thread_key}/messages
```

**Exemple concret** :
```
/clients/user123/chats/katalog_demo_12345/threads/onboarding_1758614588/messages
```

**Paramètres** :
- `firebase_user_id` : ID de l'utilisateur Firebase
- `mode` : `'chats'` (chats utilisateur) ou `'job_chats'` (chats de jobs)
- `space_code` : ID de l'entreprise (= `companies_search_id`)
- `thread_key` : ID du thread de chat (= `current_chat`)

### 3.3 Filtrage des messages

**Localisation** : `base_state.py`, ligne 20869-20884

```python
# 🛡️ GUARD DÉFENSIF : Filtrer les messages RTDB pour general_chat avec microservice
if self.chat_mode == 'general_chat' and self._is_microservice_mode_active():
    # Accepter : llm_stream_* + événements agentiques + événements de cartes
    agentic_events = ['tool_use_start', 'tool_use_complete']
    card_events = ['CARD', 'CARD_CLICKED_PINNOKIO']
    allowed_events = agentic_events + card_events
    
    if message_type and not message_type.startswith('llm_stream_') and message_type not in allowed_events:
        return  # Ignorer les messages RTDB

# ✅ Pour onboarding_chat, TOUS les messages sont acceptés, y compris 'CMMD'
```

**💡 Important** : Les messages de type `'CMMD'` sont **toujours acceptés** en mode `onboarding_chat`.

## 4. ⚡ Règles pour la réactivité

### 4.1 Mécanisme de réactivité Reflex

```python
# ✅ Variables réactives dans ChatState
workflow_checklist: Optional[Dict[str, List[Dict[str, Any]]]] = None  # Mutable
checklist_visible: bool = False                                         # Mutable

# ✅ Variable calculée (se met à jour automatiquement)
@rx.var
def workflow_checklist_steps(self) -> List[Dict[str, Any]]:
    if not self.workflow_checklist or not self.workflow_checklist.get("steps"):
        return []
    return self.workflow_checklist["steps"]

# ✅ Variable calculée pour la progression
@rx.var
def workflow_progress_percentage(self) -> int:
    if not self.workflow_checklist or not self.workflow_checklist.get("steps"):
        return 0
    
    steps = self.workflow_checklist["steps"]
    completed_steps = sum(1 for step in steps if step.get("status") == "completed")
    total_steps = len(steps)
    
    if total_steps == 0:
        return 0
    
    return int((completed_steps / total_steps) * 100)
```

### 4.2 Déclenchement du re-render

**🔧 CRITIQUE** : Le `yield` à la fin du traitement de `CMMD` (ligne 21057)

```python
elif message_type == 'CMMD':
    try:
        # ... traitement des actions SET_WORKFLOW_CHECKLIST et UPDATE_STEP_STATUS
        
        # ✅ CORRECTION : Forcer un yield pour déclencher la réactivité UI
        yield  # ← INDISPENSABLE pour le re-render
        
    except json.JSONDecodeError as e:
        print(f"Error parsing command JSON: {e}")
```

**Sans ce `yield`** :
- ❌ Les modifications de `workflow_checklist` ne déclenchent pas de re-render
- ❌ L'UI ne se met pas à jour automatiquement
- ❌ L'utilisateur ne voit pas les changements

**Avec ce `yield`** :
- ✅ Reflex détecte que l'état a changé
- ✅ Les variables calculées (`workflow_checklist_steps`, `workflow_progress_percentage`) sont recalculées
- ✅ Le composant `workflow_checklist_component()` se re-render automatiquement
- ✅ L'UI reflète instantanément les changements

### 4.3 Utilisation de `rx.foreach` pour la réactivité

```python
# Ligne 75-116 dans index.py
rx.foreach(
    ChatState.workflow_checklist_steps,  # ← Variable calculée réactive
    lambda step: rx.hstack(
        # ... affichage de chaque étape
    )
)
```

**Avantages de `rx.foreach`** :
- ✅ Re-render automatique quand `workflow_checklist_steps` change
- ✅ Optimisation : seules les étapes modifiées sont re-render
- ✅ Type-safe avec la variable calculée

## 5. 🎯 Conditions d'affichage du composant

### 5.1 Condition principale de visibilité

**Localisation** : `index.py`, ligne 61-145

```python
def workflow_checklist_component() -> rx.Component:
    """Composant pour afficher la checklist du workflow d'onboarding."""
    return rx.cond(
        ChatState.checklist_visible,  # ← Condition principale
        # ... contenu si visible
        rx.box()  # Placeholder vide si non visible
    )
```

### 5.2 Règles de visibilité de `checklist_visible`

**📍 Activation de la checklist** :

#### A. Via `check_onboarding_job_status()` (ligne 18777-18843)

```python
@rx.event(background=True)
async def check_onboarding_job_status(self):
    """Vérifie l'état du job d'onboarding et met à jour les variables d'état."""
    try:
        # Lecture du document onboarding dans Firebase
        firebase_c = FireBaseManagement()
        data = firebase_c.get_document(document_path=onboarding_path) or {}
        job_active_remote = bool(data.get('job_active', False))
        
        if company_name and company_name == self.companies_search_term:
            # 📋 GESTION DE LA CHECKLIST selon le statut du job
            if job_active_remote:
                # Job actif → rendre la checklist visible
                if not self.checklist_visible:
                    self.checklist_visible = True  # ✅ ACTIVATION
                    print("📋 Checklist rendue visible (job actif)")
            else:
                # Job inactif → masquer la checklist
                self.checklist_visible = False  # ❌ DÉSACTIVATION
                self.workflow_checklist = None
                print("📋 Checklist masquée (job inactif)")
        else:
            # Pas de données d'onboarding
            self.checklist_visible = False  # ❌ DÉSACTIVATION
            self.workflow_checklist = None
```

#### B. Via `SET_WORKFLOW_CHECKLIST` (ligne 21027-21032)

```python
elif action == "SET_WORKFLOW_CHECKLIST":
    # Initialisation de la checklist
    checklist_data = params.get("checklist", {})
    self.workflow_checklist = checklist_data
    self.checklist_visible = True  # ✅ ACTIVATION AUTOMATIQUE
    print(f"✅ Checklist du workflow mise à jour")
```

#### C. Via `toggle_onboarding_job()` - Arrêt du job (ligne 18897-18909)

```python
if result.get('success', False):
    # Arrêt réussi du job
    self.onboarding_job_active = False
    
    # 🧹 SUPPRIMER LA CHECKLIST quand le job est arrêté
    self.workflow_checklist = None
    self.checklist_visible = False  # ❌ DÉSACTIVATION
    print("🧹 Checklist du workflow supprimée (job arrêté)")
```

### 5.3 Table de décision pour l'affichage

| Condition | `checklist_visible` | Affichage |
|-----------|---------------------|-----------|
| `onboarding_job_active == True` | `True` | ✅ Visible |
| `onboarding_job_active == False` | `False` | ❌ Masqué |
| Message `SET_WORKFLOW_CHECKLIST` reçu | `True` | ✅ Visible |
| Job arrêté manuellement | `False` | ❌ Masqué |
| Pas de données d'onboarding | `False` | ❌ Masqué |
| `workflow_checklist == None` | N/A | ❌ Masqué |

### 5.4 Intégration dans l'onglet Jobs

**Localisation** : `index.py`, ligne 147-434

```python
def job_tab() -> rx.Component:
    """Composant pour l'onglet Jobs qui affiche les jobs disponibles."""
    return rx.vstack(
        rx.heading("Jobs", size="3", margin_bottom="4"),
        
        # Section Onboarding
        rx.cond(
            ChatState.job_tab_active,  # ← Job tab doit être actif
            rx.card(
                rx.vstack(
                    # ... En-tête avec bouton Start/Stop
                    
                    # 📋 CHECKLIST DU WORKFLOW
                    workflow_checklist_component(),  # ← Insertion du composant
                    
                    # ... Autres sections
                )
            )
        )
    )
```

## 6. 🔍 Conditions complètes pour l'affichage

### 6.1 Chaîne de conditions

```
Pour que la checklist soit visible, il faut :

1. job_tab_active == True
   └─ Activé via check_onboarding_job_status()
   └─ Condition : données d'onboarding trouvées pour l'entreprise

2. checklist_visible == True
   └─ Activé via :
      ├─ check_onboarding_job_status() si job_active == True
      ├─ SET_WORKFLOW_CHECKLIST reçu via RTDB
      └─ initialize_onboarding_chat() lors du démarrage

3. workflow_checklist != None
   └─ Initialisé via SET_WORKFLOW_CHECKLIST

4. Chat_realtime_listener_active == True
   └─ Listener RTDB actif pour recevoir les mises à jour
```

### 6.2 Code de vérification

**Dans `ChatState`** :

```python
# Vérification rapide de l'état
print(f"job_tab_active: {self.job_tab_active}")
print(f"checklist_visible: {self.checklist_visible}")
print(f"workflow_checklist: {self.workflow_checklist is not None}")
print(f"Chat_realtime_listener_active: {self.Chat_realtime_listener_active}")
print(f"onboarding_job_active: {self.onboarding_job_active}")
```

## 7. 📝 Résumé de la configuration

### 7.1 Variables d'état critiques

| Variable | Type | Rôle |
|----------|------|------|
| `workflow_checklist` | `Dict` | Données de la checklist |
| `checklist_visible` | `bool` | Visibilité du composant |
| `workflow_checklist_steps` | `List` | Variable calculée pour l'affichage |
| `workflow_progress_percentage` | `int` | Pourcentage calculé |
| `onboarding_job_active` | `bool` | Statut du job |
| `job_tab_active` | `bool` | Visibilité de l'onglet Jobs |

### 7.2 Actions RTDB supportées

| Action | Effet | Paramètres requis |
|--------|-------|-------------------|
| `SET_WORKFLOW_CHECKLIST` | Initialisation complète | `checklist` (dict) |
| `UPDATE_STEP_STATUS` | Mise à jour d'une étape | `step_id`, `status`, `timestamp`, `message` |
| `SET_PROCESS_STATE` | Activation/désactivation du job | `state` (bool) |

### 7.3 Chemins d'écoute Firebase

```
Format général :
/clients/{user_id}/{mode}/{space_code}/threads/{thread_key}/messages

Pour onboarding :
/clients/{firebase_user_id}/chats/{companies_search_id}/threads/{current_chat}/messages
```

## 8. 🐛 Points de débogage

### 8.1 Checklist de dépannage

✅ **Vérifier l'écoute RTDB** :
```python
print(f"Listener actif: {ChatState.Chat_realtime_listener_active}")
print(f"Space code: {ChatState.chat_space_code}")
print(f"Thread key: {ChatState.chat_thread_key}")
```

✅ **Vérifier les données** :
```python
print(f"Checklist visible: {ChatState.checklist_visible}")
print(f"Checklist data: {ChatState.workflow_checklist}")
print(f"Steps count: {len(ChatState.workflow_checklist_steps)}")
```

✅ **Vérifier le job** :
```python
print(f"Job tab actif: {ChatState.job_tab_active}")
print(f"Job actif: {ChatState.onboarding_job_active}")
print(f"Mode chat: {ChatState.chat_mode}")
```

### 8.2 Problèmes courants

| Problème | Cause | Solution |
|----------|-------|----------|
| Checklist ne s'affiche pas | `checklist_visible == False` | Vérifier `check_onboarding_job_status()` |
| Pas de mises à jour | Listener inactif | Vérifier `start_realtime_listener()` |
| Données non mises à jour | Pas de `yield` après modification | Ajouter `yield` après les modifications |
| Mauvais chemin RTDB | `mode` incorrect | Vérifier `mode` dans `personal_chats` |

---

**✅ Ce rapport couvre tous les aspects de la gestion de la workflow checklist dans l'application Pinnokio.**