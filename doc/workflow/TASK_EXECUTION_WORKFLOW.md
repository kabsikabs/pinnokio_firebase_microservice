# 📋 Workflow d'exécution des tâches automatisées

## 📌 Vue d'ensemble

Ce document décrit le flux complet d'exécution des tâches planifiées (SCHEDULED/ONE_TIME) et à la demande (ON_DEMAND), depuis le déclenchement jusqu'à l'affichage des résultats dans l'interface utilisateur.

---

## 🎯 Contexte et objectifs

### Problématique
Les tâches planifiées doivent :
1. **S'exécuter automatiquement** selon un planning (CRON)
2. **Être déclenchées manuellement** depuis le dashboard
3. **Conserver l'historique** des exécutions (chat persistant)
4. **Afficher les résultats** dans l'interface en temps réel

### Architecture mise en place
```
┌─────────────────┐
│   CRON Scheduler│ ──┐
│   (Backend)     │   │
└─────────────────┘   │
                      │
┌─────────────────┐   │    ┌──────────────────┐
│   Dashboard UI  │ ──┼───►│  LLM Manager     │
│   (Frontend)    │   │    │  (_execute_      │
└─────────────────┘   │    │   scheduled_task)│
                      │    └──────────────────┘
                      │            │
                      │            ▼
                      │    ┌──────────────────┐
                      └───►│  Firebase RTDB   │
                           │  (Chat persistant)│
                           └──────────────────┘
```

---

## 🔄 Flux 1 : Exécution automatique (CRON)

### 📍 Point d'entrée : `app/cron_scheduler.py`

#### Étape 1 : Détection des tâches dues
```python
# Fichier: app/cron_scheduler.py
# Méthode: check_and_execute_tasks()

# Récupération des tâches prêtes depuis /scheduled_tasks
tasks = fbm.get_tasks_ready_for_execution_utc(now_utc)

# Pour chaque tâche due
for task_data in tasks:
    await self._execute_task(task_data, now)
```

**Firebase path consulté** : `/scheduled_tasks/{job_id}`
- Champs critiques : `next_execution_utc`, `enabled`, `mandate_path`

---

#### Étape 2 : Préparation de l'exécution
```python
# Fichier: app/cron_scheduler.py
# Méthode: _execute_task()

# 1. Génération des identifiants
execution_id = f"exec_{uuid.uuid4().hex[:12]}"
thread_key = task_id  # ⭐ Chat persistant : thread_key = task_id

# 2. Création du document d'exécution dans Firebase
execution_data = {
    "execution_id": execution_id,
    "task_id": task_id,
    "thread_key": thread_key,
    "status": "running",
    "started_at": triggered_at.isoformat(),
    "workflow_checklist": None,
    "lpt_tasks": {}
}
fbm.create_task_execution(mandate_path, task_id, execution_data)
```

**Firebase path créé** : `{mandate_path}/tasks/{task_id}/executions/{execution_id}`

---

#### Étape 3 : Création/vérification du chat RTDB
```python
# Vérifier si le chat existe déjà (historique)
chat_path = f"{company_id}/chats/{thread_key}"
existing_chat = rtdb.db.child(chat_path).get()

if existing_chat:
    # ✅ Chat existant : continuité de l'historique
    logger.info(f"Chat existant trouvé: {thread_key}")
else:
    # 🆕 Création nouveau chat
    chat_result = rtdb.create_chat(
        user_id=user_id,
        space_code=company_id,
        thread_name=mission_title,
        mode="chats",
        chat_mode="task_execution",  # ✅ Mode reconnu dans le frontend
        thread_key=thread_key
    )
```

**Firebase RTDB path** : `{company_id}/chats/{thread_key}/`

**Structure du chat créé** :
```json
{
  "thread_name": "Titre de la mission",
  "thread_key": "task_abc123",
  "created_at": "2025-12-02T10:30:00Z",
  "created_by": "user_xyz",
  "chat_mode": "task_execution",
  "messages": {}
}
```

---

#### Étape 4 : Lancement de l'exécution asynchrone
```python
# Délégation à LLMManager pour l'exécution réelle
from .llm_service.llm_manager import get_llm_manager
llm_manager = get_llm_manager()

asyncio.create_task(
    llm_manager._execute_scheduled_task(
        user_id=user_id,
        company_id=company_id,
        task_data=task_data,
        thread_key=thread_key,
        execution_id=execution_id
    )
)
```

---

#### Étape 5 : Mise à jour de `next_execution`
```python
# Pour SCHEDULED : calcul de la prochaine occurrence
if execution_plan == "SCHEDULED":
    next_local, next_utc = fbm.calculate_task_next_execution(
        cron_expr, timezone_str, from_time=triggered_at
    )
    
    # Mise à jour dans /tasks/{task_id}
    fbm.update_task(mandate_path, task_id, {
        "schedule.next_execution_local_time": next_local,
        "schedule.next_execution_utc": next_utc,
        "execution_count": task_data.get("execution_count", 0) + 1
    })
    
    # Mise à jour dans /scheduled_tasks/{job_id}
    scheduler_ref.update({
        "next_execution_local_time": next_local,
        "next_execution_utc": next_utc,
        "updated_at": firestore.SERVER_TIMESTAMP
    })

# Pour ONE_TIME : désactivation
elif execution_plan == "ONE_TIME":
    fbm.update_task(mandate_path, task_id, {
        "enabled": False,
        "status": "completed"
    })
    fbm.delete_scheduler_job_completely(job_id)
```

---

## 🔄 Flux 2 : Exécution manuelle (Dashboard)

### 📍 Point d'entrée : `DashboardTasksState.execute_task_now()`

#### Étape 1 : Appel RPC depuis le frontend
```python
# Fichier: pinnokio_app/state/DashboardTasksState.py
# Méthode: execute_task_now()

result = rpc_call(
    "LLM.execute_task_now",
    kwargs={
        "mandate_path": mandate_path,
        "task_id": task_id,
        "user_id": user_id,
        "company_id": company_id
    },
    user_id=user_id,
    timeout_ms=30000
)
```

**RPC endpoint** : `POST /rpc` → `LLM.execute_task_now`

---

#### Étape 2 : Réception dans le microservice
```python
# Fichier: app/llm_service/llm_manager.py
# Méthode: execute_task_now()

# 1. Récupération des données complètes de la tâche
task_data = fbm.get_task(mandate_path, task_id)

# 2. Génération execution_id et thread_key
execution_id = f"exec_{uuid.uuid4().hex[:12]}"
thread_key = task_id  # ⭐ Chat persistant

# 3. Création document d'exécution (identique au CRON)
fbm.create_task_execution(mandate_path, task_id, execution_data)

# 4. Vérification/création chat RTDB (identique au CRON)
# 5. Lancement _execute_scheduled_task() en background
```

**Le reste du flux est identique au CRON** ✅

---

## 🧠 Flux 3 : Exécution LLM (Core)

### 📍 Point d'entrée : `llm_manager._execute_scheduled_task()`

#### Étape 1 : Initialisation de la session LLM
```python
# Fichier: app/llm_service/llm_manager.py
# Méthode: _execute_scheduled_task()

session = await self._ensure_session_initialized(
    user_id=user_id,
    collection_name=company_id,
    chat_mode="task_execution"  # ✅ Mode reconnu dans le frontend
)
```

**Session LLM** :
- Cache Redis : `llm_session:{user_id}:{company_id}:task_execution`
- Charge : `user_context`, `dms_system`, `available_tools`

---

#### Étape 2 : Chargement de l'historique du chat
```python
# ⭐ CLEF DE LA CONTINUITÉ : Charger l'historique des exécutions précédentes
history = await self._load_history_from_rtdb(
    collection_name=company_id,
    thread_key=thread_key,  # thread_key = task_id
    chat_mode="task_execution"
)

logger.info(f"Historique chargé: {len(history)} message(s)")
```

**Firebase RTDB path** : `{company_id}/chats/{thread_key}/messages/`

**Format de l'historique** :
```python
[
    {
        "role": "user",
        "content": "Effectue la tâche planifiée...",
        "timestamp": "2025-12-01T09:00:00Z",
        "message_id": "msg_123"
    },
    {
        "role": "assistant",
        "content": "J'ai terminé la tâche...",
        "timestamp": "2025-12-01T09:05:00Z",
        "message_id": "msg_124"
    }
]
```

---

#### Étape 3 : Création du brain avec historique
```python
# Créer brain pour ce thread avec l'historique chargé
load_result = await self.load_chat_history(
    user_id=user_id,
    collection_name=company_id,
    thread_key=thread_key,
    history=history
)

brain_id = load_result["brain_id"]
session.active_brains[thread_key] = brain_id
```

**Brain state** :
- `chat_history` : Historique complet des exécutions
- `tools` : Outils disponibles pour l'agent
- `system_prompt` : Contexte de la mission

---

#### Étape 4 : Construction du message initial
```python
# Message système avec la mission à accomplir
mission_description = task_data.get("mission", {}).get("description", "")
mission_plan = task_data.get("mission", {}).get("plan", "")

initial_message = f"""🎯 **Tâche planifiée : {mission_title}**

**Description :**
{mission_description}

**Plan d'action :**
{mission_plan}

📊 Exécution ID : {execution_id}
⏰ Déclenchée à : {triggered_at}
"""
```

---

#### Étape 5 : Sauvegarde du message utilisateur dans RTDB
```python
user_message_id = f"{int(time.time() * 1000)}"
user_timestamp = datetime.now(timezone.utc).isoformat()

rtdb = get_firebase_realtime()
rtdb.save_message(
    space_code=company_id,
    thread_key=thread_key,
    message_id=user_message_id,
    role="user",
    content=initial_message,
    timestamp=user_timestamp
)
```

**Firebase RTDB path** : `{company_id}/chats/{thread_key}/messages/{user_message_id}`

---

#### Étape 6 : Création du message assistant (placeholder)
```python
assistant_message_id = f"{int(time.time() * 1000) + 1}"
assistant_timestamp = datetime.now(timezone.utc).isoformat()

rtdb.save_message(
    space_code=company_id,
    thread_key=thread_key,
    message_id=assistant_message_id,
    role="assistant",
    content="",  # Vide au départ, sera streamé
    timestamp=assistant_timestamp
)
```

---

#### Étape 7 : Détection de connexion utilisateur
```python
# Vérifier si l'utilisateur est connecté (pour le streaming)
user_connected = await self._is_user_connected(
    user_id=user_id,
    company_id=company_id,
    thread_key=thread_key
)

enable_streaming = user_connected
logger.info(f"Utilisateur connecté : {user_connected} → Streaming: {enable_streaming}")
```

**Registre Redis** : `listeners:{user_id}:*`

---

#### Étape 8 : Exécution du LLM avec outils
```python
result = await self._process_message_with_brain(
    session=session,
    user_id=user_id,
    collection_name=company_id,
    thread_key=thread_key,
    message=initial_message,
    assistant_message_id=assistant_message_id,
    assistant_timestamp=assistant_timestamp,
    enable_streaming=user_connected,
    chat_mode="task_execution",
    system_prompt=task_specific_prompt
)
```

**Outils disponibles** :
- `CREATE_TASK` : Créer des sous-tâches
- `SEARCH_DOCUMENTS` : Rechercher dans Google Drive
- `READ_DOCUMENT` : Lire un fichier
- `WRITE_DOCUMENT` : Créer/modifier un fichier
- `LONG_RUNNING_TASK` : Déléguer à un LPT
- Etc.

---

## 🔧 Flux 4 : Utilisation des outils (LPT)

### 📍 Cas d'usage : `LONG_RUNNING_TASK`

#### Étape 1 : Appel de l'outil par l'agent
```python
# L'agent décide de déléguer une tâche longue
tool_call = {
    "name": "LONG_RUNNING_TASK",
    "arguments": {
        "task_description": "Analyser 1000 factures",
        "subtasks": [
            {"description": "Extraire données", "estimated_time": "10 min"},
            {"description": "Valider montants", "estimated_time": "5 min"}
        ]
    }
}
```

---

#### Étape 2 : Création du LPT
```python
# Fichier: app/pinnokio_agentic_workflow/tools/lpt_client.py
# Méthode: submit_lpt()

lpt_id = f"lpt_{uuid.uuid4().hex[:12]}"
lpt_data = {
    "lpt_id": lpt_id,
    "task_description": task_description,
    "status": "pending",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "callback_url": f"{BASE_URL}/lpt_callback",
    "callback_token": generated_token,
    "metadata": {
        "user_id": user_id,
        "company_id": company_id,
        "thread_key": thread_key,
        "execution_id": execution_id
    }
}
```

**Firebase path** : `{mandate_path}/tasks/{task_id}/executions/{execution_id}/lpt_tasks/{lpt_id}`

---

#### Étape 3 : Exécution du LPT (worker externe)
```python
# Fichier externe : worker process

# 1. Récupération du LPT depuis Firebase
lpt_data = fbm.get_lpt_task(mandate_path, task_id, execution_id, lpt_id)

# 2. Exécution de la tâche longue
for subtask in subtasks:
    result = execute_subtask(subtask)
    update_progress(lpt_id, progress)

# 3. Appel du callback avec le résultat
callback_url = lpt_data["callback_url"]
callback_token = lpt_data["callback_token"]

response = requests.post(
    callback_url,
    json={
        "lpt_id": lpt_id,
        "status": "completed",
        "result": final_result,
        "metadata": lpt_data["metadata"]
    },
    headers={"Authorization": f"Bearer {callback_token}"}
)
```

---

#### Étape 4 : Réception du callback LPT
```python
# Fichier: app/main.py
# Endpoint: POST /lpt_callback

@app.post("/lpt_callback")
async def lpt_callback(req: LPTCallbackRequest, authorization: str | None = Header(...)):
    # 1. Validation du token
    validate_callback_token(authorization)
    
    # 2. Récupération du contexte
    metadata = req.metadata
    user_id = metadata["user_id"]
    company_id = metadata["company_id"]
    thread_key = metadata["thread_key"]
    execution_id = metadata["execution_id"]
    
    # 3. Mise à jour du statut LPT dans Firebase
    fbm.update_lpt_status(
        mandate_path=metadata["mandate_path"],
        task_id=metadata["task_id"],
        execution_id=execution_id,
        lpt_id=req.lpt_id,
        status=req.status,
        result=req.result
    )
    
    # 4. Notification à l'agent (via RTDB ou message)
    rtdb = get_firebase_realtime()
    rtdb.save_message(
        space_code=company_id,
        thread_key=thread_key,
        message_id=f"lpt_result_{req.lpt_id}",
        role="system",
        content=f"✅ LPT terminé : {req.result['summary']}",
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    
    return {"success": True}
```

---

## 💬 Flux 5 : Streaming vers le frontend

### Condition : Utilisateur connecté sur le chat

#### Étape 1 : Détection de la connexion
```python
# Vérifier si l'utilisateur écoute ce thread
listener_id = f"chat_{user_id}_{company_id}_{thread_key}"
listener_exists = redis_client.exists(f"listeners:{user_id}:{listener_id}")

if listener_exists:
    enable_streaming = True
```

---

#### Étape 2 : Streaming des tokens LLM
```python
# Durant _process_message_with_brain()
if enable_streaming:
    async for chunk in llm_stream:
        token = chunk.get("content", "")
        
        # Mise à jour RTDB en temps réel
        rtdb.update_message_content(
            space_code=company_id,
            thread_key=thread_key,
            message_id=assistant_message_id,
            content=accumulated_content + token
        )
        
        # Notification via Redis PubSub
        redis_client.publish(
            f"stream:{company_id}:{thread_key}",
            json.dumps({
                "type": "token",
                "message_id": assistant_message_id,
                "token": token
            })
        )
```

---

#### Étape 3 : Réception dans le frontend
```python
# Fichier: pinnokio_app/state/ChatState.py
# Listener RTDB actif

def on_message_update(event):
    message_id = event.path.split("/")[-1]
    new_content = event.data.get("content", "")
    
    # Mise à jour en temps réel dans l'UI
    for msg in self.chats[thread_key]:
        if msg["id"] == message_id:
            msg["content"] = new_content
            break
    
    # Forcer le re-render
    yield
```

---

## 📊 Flux 6 : Finalisation de l'exécution

### Étape 1 : Sauvegarde du résultat final
```python
# Fichier: app/llm_service/llm_manager.py
# Fin de _execute_scheduled_task()

# Mise à jour du document d'exécution
fbm.update_task_execution(
    mandate_path=mandate_path,
    task_id=task_id,
    execution_id=execution_id,
    updates={
        "status": "completed",
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration,
        "final_message": result["response"]
    }
)

# Mise à jour de last_execution_report dans la tâche
fbm.update_task(
    mandate_path=mandate_path,
    task_id=task_id,
    updates={
        "last_execution_report": {
            "execution_id": execution_id,
            "status": "completed",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration,
            "summary": result["response"][:500]
        }
    }
)
```

---

### Étape 2 : Notification utilisateur (si connecté)
```python
# Envoi d'une notification push
if user_connected:
    notification_manager.send_notification(
        user_id=user_id,
        title=f"Tâche terminée : {mission_title}",
        body=f"Exécution {execution_id} complétée avec succès",
        data={
            "type": "task_completed",
            "task_id": task_id,
            "thread_key": thread_key,
            "execution_id": execution_id
        }
    )
```

---

## 🎨 Intégration Frontend du mode `task_execution`

### Configuration dans `ChatState.py`

Le mode `task_execution` est intégré comme un mode de chat à part entière :

```python
# Modes de chat reconnus
CHAT_MODES = [
    "router_chat", 
    "apbookeeper_chat", 
    "general_chat", 
    "onboarding_chat", 
    "task_execution"  # ✅ Mode pour les tâches planifiées
]

# Modes avec streaming WebSocket
streaming_chat_modes = (
    'general_chat', 
    'onboarding_chat', 
    'apbookeeper_chat', 
    'router_chat', 
    'banker_chat', 
    'task_execution'  # ✅ Streaming activé
)

# Affichage dans l'UI
chat_mode_display = {
    "general_chat": "Chat Général",
    "apbookeeper_chat": "APBookkeeper",
    "router_chat": "Router",
    "onboarding_chat": "Onboarding",
    "task_execution": "⚙️ Tâche Auto"  # ✅ Label distinctif
}

# Couleurs par mode
chat_mode_color = {
    "general_chat": "blue",
    "apbookeeper_chat": "green",
    "router_chat": "orange",
    "onboarding_chat": "purple",
    "task_execution": "amber"  # ✅ Couleur distinctive
}
```

### Distinction visuelle dans la liste des chats

Les chats de type `task_execution` sont facilement identifiables :

```
┌────────────────────────────────────────┐
│ 📋 Liste des chats                      │
├────────────────────────────────────────┤
│ 💬 Discussion générale         • blue   │
│ 🚀 Onboarding client XYZ       • purple │
│ ⚙️ Tâche Auto: Analyse mensuelle • amber│  ← task_execution
│ ⚙️ Tâche Auto: Rapport hebdo    • amber │
│ 📁 Routage facture 12345       • orange │
└────────────────────────────────────────┘
```

### Backend : Configuration dans `agent_modes.py`

Le mode `task_execution` utilise :
- **Prompt** : `_build_task_execution_prompt` (étend `general_chat` + instructions d'exécution autonome)
- **Outils** : `_build_general_tools` (identiques à `general_chat` + `CREATE_CHECKLIST`, `UPDATE_STEP`)

```python
_AGENT_MODE_REGISTRY = {
    # ...
    "task_execution": AgentModeConfig(
        name="task_execution",
        prompt_builder=_build_task_execution_prompt,
        tool_builder=_build_general_tools,  # Mêmes outils que general_chat
    ),
}
```

---

## 🔗 Coordination Frontend-Backend

### Structure de données partagée

#### 1️⃣ Firebase Firestore : `/tasks/{task_id}`
```json
{
  "task_id": "task_abc123",
  "user_id": "user_xyz",
  "company_id": "company_789",
  "mandate_path": "clients/.../mandates/mandate_456",
  "execution_plan": "SCHEDULED",
  "mission": {
    "title": "Analyse mensuelle",
    "description": "Analyser les données du mois",
    "plan": "1. Extraire données\n2. Analyser\n3. Créer rapport"
  },
  "schedule": {
    "cron_expression": "0 9 1 * *",
    "frequency": "monthly",
    "timezone": "Europe/Paris",
    "next_execution_local_time": "2025-01-01T09:00:00+01:00",
    "next_execution_utc": "2025-01-01T08:00:00Z"
  },
  "status": "active",
  "enabled": true,
  "execution_count": 5,
  "last_execution_report": {
    "execution_id": "exec_xyz789",
    "status": "completed",
    "executed_at": "2024-12-01T08:00:00Z",
    "duration_seconds": 300,
    "summary": "Analyse terminée avec succès"
  },
  "created_at": "2024-11-01T10:00:00Z",
  "updated_at": "2024-12-01T08:05:00Z"
}
```

---

#### 2️⃣ Firebase RTDB : `{company_id}/chats/{thread_key}`
```json
{
  "thread_name": "Analyse mensuelle",
  "thread_key": "task_abc123",
  "created_at": "2024-11-01T10:00:00Z",
  "created_by": "user_xyz",
  "chat_mode": "task_execution",
  "messages": {
    "1733140800000": {
      "role": "user",
      "content": "🎯 Effectue l'analyse mensuelle...",
      "timestamp": "2024-12-01T08:00:00Z",
      "message_id": "1733140800000"
    },
    "1733140850000": {
      "role": "assistant",
      "content": "J'ai terminé l'analyse. Voici le rapport...",
      "timestamp": "2024-12-01T08:00:50Z",
      "message_id": "1733140850000"
    }
  }
}
```

---

### Endpoints RPC utilisés par le frontend

#### 1. Charger les tâches
```python
# RPC: FIREBASE_MANAGEMENT.list_tasks_for_mandate
result = rpc_call(
    "FIREBASE_MANAGEMENT.list_tasks_for_mandate",
    kwargs={"mandate_path": mandate_path}
)
# Retourne: List[TaskData]
```

---

#### 2. Exécuter une tâche maintenant
```python
# RPC: LLM.execute_task_now
result = rpc_call(
    "LLM.execute_task_now",
    kwargs={
        "mandate_path": mandate_path,
        "task_id": task_id,
        "user_id": user_id,
        "company_id": company_id
    }
)
# Retourne: {"success": True, "task_id": "...", "thread_key": "..."}
```

---

#### 3. Activer/désactiver une tâche
```python
# RPC: FIREBASE_MANAGEMENT.update_task
result = rpc_call(
    "FIREBASE_MANAGEMENT.update_task",
    kwargs={
        "mandate_path": mandate_path,
        "task_id": task_id,
        "updates": {"enabled": False}
    }
)
# Retourne: True/False
```

---

#### 4. Écouter les messages du chat
```python
# Firebase RTDB Listener
realtime_service = FirebaseRealtimeChat()
realtime_service.attach_listener(
    space_code=company_id,
    thread_key=thread_key,
    callback=on_message_update
)
```

---

## ⚠️ Points d'attention actuels

### ✅ ~~Problème 1 : `chat_mode="task_execution"`~~ (RÉSOLU)
**Statut** : ✅ **RÉSOLU** le 2025-12-02

Le mode `task_execution` est maintenant **pleinement reconnu** dans le frontend :
- Ajouté dans `CHAT_MODES` de `ChatState.py`
- Ajouté dans toutes les listes `streaming_chat_modes`
- Label d'affichage : "⚙️ Tâche Auto"
- Couleur : amber

**Fichiers modifiés** :
- `pinnokio_app/state/ChatState.py` : Reconnaissance du mode et streaming WebSocket

---

### 🟡 Problème 2 : Format de timestamp
**État** : Les timestamps utilisent parfois `str(datetime.now())` au lieu de `.isoformat()`

**Impact** : Incohérence de format entre frontend et backend.

**Solution** : Uniformiser avec `datetime.now(timezone.utc).isoformat()`

---

### 🟡 Amélioration 1 : Retry logic
**Actuellement** : Si une tâche échoue, elle n'est pas réessayée.

**Proposition** :
- Ajouter un champ `retry_count` dans les exécutions
- Configurer `max_retries` par tâche
- Re-scheduler automatiquement en cas d'échec

---

### 🟡 Amélioration 2 : Notifications
**Actuellement** : Notifications uniquement si utilisateur connecté.

**Proposition** :
- Ajouter des notifications email/push pour les tâches critiques
- Configurer des alertes sur échec répété

---

## 📝 Checklist d'implémentation frontend

### Dashboard des tâches
- [x] Afficher la liste des tâches planifiées
- [x] Trier par `next_execution_utc`
- [x] Afficher le statut (`enabled`, `last_execution_report`)
- [x] Bouton "Execute now" → `LLM.execute_task_now`
- [x] Toggle `enabled` → `FIREBASE_MANAGEMENT.update_task`

### Page de détail d'une tâche
- [ ] Afficher l'historique des exécutions
- [ ] Graphique de succès/échecs
- [ ] Durée moyenne d'exécution
- [ ] Logs détaillés par exécution

### Chat de la tâche
- [x] Redirection vers `/general_chat?thread_key={task_id}`
- [x] Affichage de l'historique complet
- [x] Streaming en temps réel si utilisateur connecté
- [x] Mode `task_execution` reconnu avec label "⚙️ Tâche Auto" et couleur amber
- [ ] Indicateur "Tâche en cours d'exécution"
- [ ] Bouton "Arrêter l'exécution"

---

## 🚀 Prochaines étapes

1. ~~**Corriger `chat_mode`**~~ : ✅ Mode `task_execution` maintenant reconnu dans le frontend
2. **Tester le flux complet** : CRON → Exécution → Affichage dans l'UI
3. **Implémenter les pages manquantes** : Détails tâche, historique d'exécution
4. **Ajouter retry logic** : Réessayer automatiquement en cas d'échec
5. **Améliorer les notifications** : Email/push pour les tâches critiques

---

## 📚 Références

### Fichiers clés Backend
- `app/cron_scheduler.py` : Scheduler CRON
- `app/llm_service/llm_manager.py` : Gestionnaire LLM
- `app/firebase_providers.py` : Accès Firebase (Firestore + RTDB)
- `app/main.py` : Endpoints RPC et callbacks

### Fichiers clés Frontend
- `pinnokio_app/state/DashboardTasksState.py` : État dashboard
- `pinnokio_app/state/ChatState.py` : État chat et listeners
- `pinnokio_app/code/tools/firebase_realtime.py` : Client RTDB
- `pinnokio_app/code/tools/rpc_client.py` : Client RPC

---

## 📋 Historique des modifications

| Date | Version | Description |
|------|---------|-------------|
| 2025-12-02 | 1.0 | Création initiale du document |
| 2025-12-02 | 1.1 | ✅ Intégration du mode `task_execution` dans le frontend |

---

**Document généré le** : 2025-12-02  
**Dernière mise à jour** : 2025-12-02  
**Version** : 1.1

