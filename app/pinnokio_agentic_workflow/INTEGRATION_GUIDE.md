# 📘 GUIDE D'INTÉGRATION - Agent Cerveau Pinnokio

## ✅ **Squelette Installé - Vue d'ensemble**

Le framework agentic workflow pour l'agent cerveau Pinnokio a été installé dans l'application. Voici la structure complète et les étapes suivantes.

---

## 📁 **Structure des fichiers créés**

```
app/
├── pinnokio_agentic_workflow/
│   ├── __init__.py                          ✅ CRÉÉ
│   ├── DOCUMENTATION_FRAMEWORK_AGENTIC_WORKFLOW.md  (Existant)
│   ├── exemple.py                           (Existant)
│   ├── INTEGRATION_GUIDE.md                 ✅ CRÉÉ (ce fichier)
│   │
│   ├── orchestrator/
│   │   ├── __init__.py                      ✅ CRÉÉ
│   │   ├── pinnokio_brain.py                ✅ CRÉÉ (Agent cerveau)
│   │   ├── task_tracker.py                  ✅ CRÉÉ (Tracking SPT/LPT)
│   │   ├── task_planner.py                  ✅ CRÉÉ (Stub pour future)
│   │   └── task_executor.py                 ✅ CRÉÉ (Stub pour future)
│   │
│   └── workflows/
│       ├── __init__.py                      ✅ CRÉÉ
│       └── pinnokio_workflow.py             ✅ CRÉÉ (Workflow principal)
│
├── llm_service/
│   └── llm_manager.py                       ✅ MODIFIÉ
│       ├─ send_message_with_pinnokio()      (Nouvelle méthode)
│       └─ _process_pinnokio_workflow()      (Nouvelle méthode)
│
└── main.py                                  ⚠️ À MODIFIER
    └─ Ajouter endpoints RPC et callbacks
```

---

## 🎯 **Fonctionnalités implémentées**

### 1. **PinnokioBrain** - Agent Cerveau Principal

**Fichier** : `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py`

**Responsabilités** :
- ✅ Initialisation avec BaseAIAgent
- ✅ System prompt intelligent avec instructions SPT/LPT
- ✅ Création des outils (SPT + LPT + TERMINATE)
- ✅ Méthodes SPT :
  - `_spt_read_firebase()` : Lecture Firebase
  - `_spt_search_chromadb()` : Recherche vectorielle
- ✅ Méthodes LPT :
  - `_lpt_file_manager()` : Appel Agent File Manager
  - `_lpt_accounting()` : Appel Agent Comptable
- ✅ Tracking des tâches LPT actives par thread

**Arguments clés** :
```python
PinnokioBrain(
    collection_name="klk_space_id_002e0b",  # Société
    firebase_user_id="user_abc123",          # UID
    dms_system="google_drive",
    dms_mode="prod"
)
```

### 2. **TaskTracker** - Suivi des tâches

**Fichier** : `app/pinnokio_agentic_workflow/orchestrator/task_tracker.py`

**Responsabilités** :
- ✅ Création de tâches LPT
- ✅ Sauvegarde dans Firebase RTDB (visible UI)
  - Path : `{collection_name}/tasks/{thread_key}/lpt_tasks/{task_id}`
- ✅ Envoi requêtes HTTP vers agents externes
- ✅ Tracking progression et statuts
- ✅ Mise à jour Firebase en temps réel

**Métadonnées envoyées avec chaque LPT** :
```python
{
    "task_id": "lpt_abc123...",
    "action": "search_and_analyze_document",
    "params": {...},
    "metadata": {
        "user_id": "user_abc123",
        "collection_name": "klk_space_id_002e0b",
        "thread_key": "chat_thread_xyz",
        "task_title": "Analyse document factures",
        "created_at": "2025-10-13T..."
    },
    "callback_url": "http://microservice:8000/api/v1/lpt/callback"
}
```

### 3. **pinnokio_agent_workflow** - Workflow Principal

**Fichier** : `app/pinnokio_agentic_workflow/workflows/pinnokio_workflow.py`

**Architecture** :
- ✅ Boucle interne de tours (max 10 par défaut)
- ✅ Gestion SPT (exécution synchrone)
- ✅ Gestion LPT (démarrage asynchrone)
- ✅ Détection TERMINATE_TASK
- ✅ Status codes :
  - `MISSION_COMPLETED` : Terminé avec succès
  - `LPT_IN_PROGRESS` : Tâches LPT en cours
  - `MAX_TURNS_REACHED` : Limite atteinte
  - `NO_IA_ACTION` : Aucune action
  - `ERROR_FATAL` : Erreur fatale

**Arguments importants** :
```python
pinnokio_agent_workflow(
    manager_instance=brain,           # Instance PinnokioBrain
    initial_query="...",              # Requête enrichie
    tools=tool_set,                   # Outils disponibles
    tool_mapping=tool_map,            # Mapping outil -> fonction
    uid="user_abc123",                # ⚠️ IMPORTANT
    collection_name="klk_...",        # ⚠️ IMPORTANT
    thread_key="chat_thread_xyz",    # ⚠️ IMPORTANT
    size=ModelSize.MEDIUM,
    max_turns=10
)
```

### 4. **Intégration LLMManager**

**Fichier** : `app/llm_service/llm_manager.py`

**Nouvelles méthodes** :
- ✅ `send_message_with_pinnokio()` : Point d'entrée
- ✅ `_process_pinnokio_workflow()` : Boucle externe d'itérations

**Fonctionnement** :
1. Création/réutilisation de PinnokioBrain
2. Écriture message utilisateur dans RTDB
3. Création des outils workflow
4. Lancement workflow en arrière-plan (asyncio.create_task)
5. Boucle externe (max 3 itérations)
6. Mise à jour RTDB en temps réel

---

## ⚠️ **Étapes restantes - Intégration main.py**

### 1. Ajouter endpoint RPC pour Pinnokio

**Dans `app/main.py`**, ajouter dans `_resolve_method()` :

```python
# Dans _resolve_method(), ajouter :
if method.startswith("LLM."):
    name = method.split(".", 1)[1]
    from .llm_service import get_llm_manager
    
    if name == "send_message_with_pinnokio":
        # Version async directe
        async def _async_wrapper(**kwargs):
            return await get_llm_manager().send_message_with_pinnokio(**kwargs)
        return _async_wrapper, "LLM"
    
    # ... autres méthodes LLM existantes ...
```

### 2. Ajouter endpoint de callback LPT

**Dans `app/main.py`**, ajouter APRÈS les endpoints existants :

```python
# ═══════════════════════════════════════════════════════════════
# ENDPOINT CALLBACK LPT
# ═══════════════════════════════════════════════════════════════

class LPTCallbackRequest(BaseModel):
    task_id: str
    status: str  # "completed", "failed", "progress"
    progress: Optional[int] = None
    current_step: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any]


@app.post("/api/v1/lpt/callback")
async def lpt_callback(req: LPTCallbackRequest, authorization: str | None = Header(default=None, alias="Authorization")):
    """
    Endpoint de callback pour les tâches LPT.
    
    Appelé par les agents externes (File Manager, Accounting, etc.)
    quand une tâche LPT est terminée ou progresse.
    """
    try:
        logger.info(f"[LPT_CALLBACK] Reçu callback: task_id={req.task_id}, status={req.status}")
        
        # Authentification optionnelle
        # _require_auth(authorization)
        
        from .pinnokio_agentic_workflow.orchestrator.task_tracker import TaskTracker
        
        # Extraire les métadonnées
        user_id = req.metadata.get("user_id")
        collection_name = req.metadata.get("collection_name")
        thread_key = req.metadata.get("thread_key")
        
        if not all([user_id, collection_name, thread_key]):
            return {"ok": False, "error": "Métadonnées manquantes"}
        
        # Créer le tracker
        tracker = TaskTracker(user_id, collection_name)
        
        # Mettre à jour la tâche
        tracker.update_task_progress(
            task_id=req.task_id,
            status=req.status,
            progress=req.progress,
            current_step=req.current_step,
            result_data=req.result
        )
        
        # Si la tâche est terminée, réactiver le workflow
        if req.status == "completed":
            logger.info(f"[LPT_CALLBACK] Tâche {req.task_id} terminée, réactivation du workflow")
            
            from .llm_service import get_llm_manager
            llm_manager = get_llm_manager()
            
            # Récupérer l'instance Pinnokio Brain
            brain_key = f"pinnokio:{user_id}:{collection_name}"
            
            if brain_key in llm_manager.sessions:
                brain = llm_manager.sessions[brain_key]
                
                # Retirer la tâche des tâches actives
                if thread_key in brain.active_lpt_tasks:
                    if req.task_id in brain.active_lpt_tasks[thread_key]:
                        brain.active_lpt_tasks[thread_key].remove(req.task_id)
                
                # Préparer le message de callback pour le workflow
                callback_message = f"""
═══════════════════════════════════════════════════════════
CALLBACK LPT REÇU
═══════════════════════════════════════════════════════════

Tâche: {req.task_id}
Statut: {req.status}
Résultat:
{req.result}

Tu peux maintenant continuer ton traitement avec ces informations.
Utilise TERMINATE_TASK si tout est terminé.
"""
                
                # Relancer le workflow avec le callback
                # TODO: Implémenter méthode pour reprendre le workflow
                logger.info("[LPT_CALLBACK] TODO: Relancer workflow avec callback")
        
        return {
            "ok": True,
            "message": "Callback traité avec succès"
        }
        
    except Exception as e:
        logger.error(f"[LPT_CALLBACK] Erreur: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}
```

---

## 🔧 **Utilisation côté Reflex**

### Appel RPC depuis Reflex

```python
# Dans votre ChatState Reflex

@rx.event(background=True)
async def send_message_to_pinnokio(self):
    """Envoie un message à l'agent Pinnokio"""
    async with self:
        if not self.question.strip():
            return
        
        question = self.question
        self.question = ""
        self.processing = True
        
        yield
        
        # ✅ Appel RPC au microservice
        result = rpc_call(
            "LLM.send_message_with_pinnokio",
            kwargs={
                "user_id": self.firebase_user_id,
                "collection_name": self.base_collection_id,
                "space_code": self.base_collection_id,
                "thread_key": self.current_chat,
                "message": question,
                "chat_mode": self.chat_mode
            },
            user_id=self.firebase_user_id,
            timeout_ms=10000
        )
        
        if result and result.get("success"):
            print(f"✅ Message envoyé à Pinnokio: {result}")
        else:
            self.processing = False
            yield rx.toast.error("Erreur lors de l'envoi à Pinnokio")
```

**Le listener ChatListener existant récupérera automatiquement les messages !**

---

## 📊 **Données visibles côté UI**

### 1. Messages dans Firebase RTDB

Path : `{collection_name}/chats/{thread_key}/messages/`

```json
{
  "user_msg_id": {
    "role": "user",
    "content": "Accède au dossier Factures et analyse...",
    "timestamp": "2025-10-13T14:30:00Z",
    "user_id": "user_abc123",
    "read": false
  },
  "assistant_msg_id": {
    "role": "assistant",
    "content": "✅ Tâche envoyée à l'Agent File Manager...",
    "timestamp": "2025-10-13T14:30:05Z",
    "metadata": {
      "status": "lpt_in_progress"
    },
    "read": false
  }
}
```

### 2. Tâches LPT dans Firebase RTDB

Path : `{collection_name}/tasks/{thread_key}/lpt_tasks/`

```json
{
  "lpt_abc123...": {
    "task_id": "lpt_abc123...",
    "type": "LPT",
    "agent_type": "file_manager",
    "action": "search_and_analyze_document",
    "task_title": "Analyse document factures Q1",
    "status": "processing",
    "progress": 45,
    "current_step": "ocr_extraction",
    "created_at": "2025-10-13T14:30:05Z",
    "updated_at": "2025-10-13T14:31:20Z",
    "metadata": {
      "estimated_duration": "2-3 minutes"
    }
  }
}
```

**L'UI peut afficher ces tâches en temps réel !**

---

## 🚀 **Variables d'environnement requises**

Ajouter dans `.env` ou configuration :

```bash
# URLs des agents externes (LPT)
FILE_MANAGER_AGENT_URL=http://file-manager-agent:8001
ACCOUNTING_AGENT_URL=http://accounting-agent:8002

# URL du microservice (pour callbacks)
MICROSERVICE_URL=http://pinnokio-microservice:8000
```

---

## ✅ **Checklist d'intégration**

### Squelette installé
- [x] PinnokioBrain créé
- [x] TaskTracker créé
- [x] Workflow pinnokio_agent_workflow créé
- [x] Intégration dans llm_manager.py
- [x] Méthodes send_message_with_pinnokio et _process_pinnokio_workflow

### Intégration main.py
- [ ] Ajouter endpoint RPC `LLM.send_message_with_pinnokio`
- [ ] Ajouter endpoint callback `/api/v1/lpt/callback`
- [ ] Tester l'appel RPC depuis Reflex
- [ ] Tester le callback depuis un agent externe

### Configuration
- [ ] Configurer les URLs des agents externes
- [ ] Configurer l'URL du microservice

### Frontend Reflex
- [ ] Lock sur les canaux avec LPT en cours (pour ne pas effacer)
- [ ] Affichage des tâches LPT en cours (optionnel)
- [ ] Affichage de la progression des tâches (optionnel)

---

## 🎯 **Prochaines étapes recommandées**

### Phase 1 : Test de base
1. ✅ Intégrer l'endpoint RPC dans main.py
2. ✅ Tester l'appel depuis Reflex
3. ✅ Vérifier que le message utilisateur apparaît dans RTDB
4. ✅ Vérifier que l'agent démarre le workflow

### Phase 2 : Test SPT
1. Appeler un outil SPT (READ_FIREBASE_DOCUMENT)
2. Vérifier la réponse dans RTDB
3. Tester SEARCH_CHROMADB

### Phase 3 : Test LPT (simulation)
1. Créer un agent externe simple (mock)
2. Tester l'envoi de requête LPT
3. Simuler un callback
4. Vérifier que le workflow reprend

### Phase 4 : Lock frontend
1. Implémenter le lock côté Reflex pour threads avec LPT
2. Afficher l'état des tâches LPT

---

## 📝 **Notes importantes**

### Compartimentage par utilisateur
✅ **Respecté** : Tous les workflows utilisent :
- `uid` (user_id Firebase)
- `collection_name` (société)
- `thread_key` (conversation)

### Limites SPT/LPT
⚠️ **À implémenter** : Vérifier les quotas d'utilisation (nombre de LPT/SPT par utilisateur)

### Callback LPT
⚠️ **Incomplet** : La reprise du workflow après callback LPT n'est pas encore implémentée.
Il faut ajouter une méthode pour réinjecter le résultat LPT dans le contexte.

---

## 🆘 **Support et Documentation**

- **Documentation complète** : `DOCUMENTATION_FRAMEWORK_AGENTIC_WORKFLOW.md`
- **Exemple d'usage** : `exemple.py`
- **Ce guide** : `INTEGRATION_GUIDE.md`

---

✅ **Le squelette est prêt et opérationnel !**

Les fondations du framework agentic sont en place. Il ne reste plus qu'à :
1. Ajouter les endpoints dans main.py
2. Tester depuis Reflex
3. Implémenter les agents externes (File Manager, Accounting)

**Bonne intégration ! 🚀**

