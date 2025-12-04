# Impl�mentation Mode Interm�diation Consolid�

**Date**: 08/11/2025
**Objectif**: Unifier la gestion des CARDs et FOLLOW_MESSAGE pour le mode interm�diation

---

## =� R�sum� des Modifications Backend  TERMIN�

Toutes les modifications backend dans [llm_manager.py](app/llm_service/llm_manager.py) sont **COMPL�T�ES**.

### 1. Nouvelles M�thodes Cr��es

#### `_start_intermediation_mode()` (ligne ~3255)
**R�le**: D�marre le mode interm�diation avec message syst�me et signal RPC

**Actions**:
1. Active `session.intermediation_mode[thread_key] = True`
2. Extrait `tools_config` du message
3. Envoie message syst�me au chat (visible, NON sauvegard� RTDB)
4. Envoie signal RPC `RPC_INTERMEDIATION_STATE` avec `action: "start"`

**Signal RPC envoy�**:
```python
{
    "type": "RPC_INTERMEDIATION_STATE",
    "channel": f"chat:{user_id}:{collection_name}:{thread_key}",
    "payload": {
        "action": "start",
        "thread_key": thread_key,
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tools_config": [...]  # Liste des outils disponibles
    }
}
```

#### `_stop_intermediation_mode()` (ligne ~3354)
**R�le**: Arr�te le mode interm�diation

**Actions**:
1. D�sactive `session.intermediation_mode[thread_key] = False`
2. Envoie message syst�me de fin
3. Envoie signal RPC `RPC_INTERMEDIATION_STATE` avec `action: "stop"`

**Raisons possibles**: `user_action`, `timeout`, `card_click`, `termination_word`

---

### 2. Modifications de la Logique Existante

#### FOLLOW_MESSAGE (ligne ~3756)
```python
# P AVANT
session.intermediation_mode[thread_key] = True
await self._send_non_message_via_websocket(...)

# P APR�S
await self._send_non_message_via_websocket(...)
await self._start_intermediation_mode(...)  # Avec message syst�me + RPC
```

#### CLOSE_INTERMEDIATION (ligne ~3784)
```python
# P AVANT
session.intermediation_mode[thread_key] = False
await self._send_non_message_via_websocket(...)

# P APR�S
await self._send_non_message_via_websocket(...)
await self._stop_intermediation_mode(...)  # Avec message syst�me + RPC
```

#### CARD - NOUVELLE LOGIQUE (ligne ~3812)
```python
# P NOUVEAU: D�marrer interm�diation pour CARD
# UNIQUEMENT pour apbookeeper_chat, router_chat, banker_chat
if session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
    await self._start_intermediation_mode(...)
```

#### Gestion terminaison dans `_handle_intermediation_response()` (ligne ~3940)
```python
# P AVANT
if has_termination:
    close_message_ref.set(close_payload)
    session.intermediation_mode[thread_key] = False

# P APR�S
if has_termination:
    close_message_ref.set(close_payload)
    await self._stop_intermediation_mode(..., reason="termination_word")
```

---

### 3. Support du job_status

#### `_check_intermediation_on_load()` modifi�e (ligne ~3987)
**Nouveau param�tre**: `job_status: Optional[str] = None`

**Nouvelle logique**:
- Supporte maintenant **CARD** ET **FOLLOW_MESSAGE** (avant: seulement FOLLOW_MESSAGE)
- V�rifie `job_status in ('running', 'in queue')` avant de r�activer
- N'appelle `_start_intermediation_mode()` QUE si job actif

**Exemple**:
```python
# Si CARD ou FOLLOW_MESSAGE dans historique + pas de CLOSE_INTERMEDIATION
if not has_close_message:
    if job_status in ('running', 'in queue'):
        await self._start_intermediation_mode(...)  #  R�active
    else:
        # � Ne r�active PAS (job termin�)
```

#### Signatures modifi�es
```python
async def enter_chat(..., job_status: Optional[str] = None)
async def start_onboarding_chat(..., job_status: Optional[str] = None)
```

**Appels mis � jour**:
- Ligne 1370: `start_onboarding_chat` � passe job_status
- Ligne 2055: `load_chat_history` � passe None
- Ligne 2158: `load_chat_history` � passe None

---

## =� Modifications Frontend Requises � EN ATTENTE

### 1. EditFormState.py - Variables d'�tat

**Ajouter** dans la classe `EditFormState`:
```python
# �tat du mode interm�diation par thread
intermediation_active: Dict[str, bool] = {}

# Outils disponibles pendant l'interm�diation
intermediation_tools: Dict[str, List[Dict]] = {}
```

### 2. EditFormState.py - Handler RPC

**Cr�er** le handler pour recevoir les signaux RPC:
```python
@rx.event(background=True)
async def handle_rpc_intermediation_state(self, payload: dict):
    """
    Re�oit RPC_INTERMEDIATION_STATE depuis le microservice.

    Payload:
    {
        "action": "start" | "stop",
        "thread_key": str,
        "job_id": str,
        "timestamp": str,
        "tools_config": [...],  # Pour "start"
        "reason": str  # Pour "stop"
    }
    """
    async with self:
        action = payload.get("action")
        thread_key = payload.get("thread_key")

        if action == "start":
            self.intermediation_active[thread_key] = True
            self.intermediation_tools[thread_key] = payload.get("tools_config", [])
            print(f"= Mode interm�diation ACTIV� - {thread_key}")

        elif action == "stop":
            self.intermediation_active[thread_key] = False
            if thread_key in self.intermediation_tools:
                del self.intermediation_tools[thread_key]
            print(f" Mode interm�diation D�SACTIV� - {thread_key}")
```

### 3. EditFormState.py - Int�gration WebSocket

**Chercher** o� les messages WebSocket sont trait�s et **ajouter**:
```python
# Dans le handler WebSocket principal
if message_type == "RPC_INTERMEDIATION_STATE":
    await self.handle_rpc_intermediation_state(payload)
```

### 4. EditFormState.py - Appel enter_chat avec job_status

**Modifier** l'appel RPC `enter_chat`:
```python
# Chercher o� enter_chat est appel� (ligne ~1031)
enter_chat_result = rpc_call(
    "llm_manager.enter_chat",
    user_id=self.firebase_user_id,
    collection_name=self.collection_name,
    thread_key=thread_key,
    chat_mode=self.chat_mode,
    job_status=self.job_status  # P AJOUTER
)
```

**Note**: V�rifier que `self.job_status` existe d�j� dans EditFormState (semble �tre d�fini quelque part)

### 5. EditFormState.py - Support messages SYSTEM_MESSAGE_INTERMEDIATION

**Ajouter** dans le handler de messages WebSocket:
```python
if message_type == "SYSTEM_MESSAGE_INTERMEDIATION":
    # Cr�er QA de type syst�me
    system_qa = QA(
        type="system",
        system_type=payload.get("system_type", "status"),
        title=payload.get("title", ""),
        message=payload.get("content", ""),
        timestamp=payload.get("timestamp"),
    )

    # Ajouter au chat (visible mais pas sauvegard� RTDB)
    if thread_key not in self.chats:
        self.chats[thread_key] = []
    self.chats[thread_key].append(system_qa)
```

---

### 6. chat_apbookeeper.py - Style messages interm�diation

**Ajouter** dans `editformstate_message()`:

```python
def intermediation_message(qa: QA) -> rx.Component:
    """Message de l'application m�tier en mode interm�diation."""
    return rx.box(
        rx.vstack(
            # Badge d'origine
            rx.badge(
                "Application M�tier",
                size="1",
                variant="soft",
                color_scheme="orange",
                margin_bottom="1",
            ),

            # Contenu du message
            rx.markdown(
                qa.answer,
                **assistant_message_style,
                style=markdown_in_message_style,
            ),

            width="100%",
            align_items="start",
            spacing="1",
        ),
        **message_container_assistant_style,
        # Style sp�cial
        border_left=f"4px solid {rx.color('orange', 6)}",
        background=rx.color("orange", 1),
    )
```

**Modifier** la condition principale:
```python
return rx.vstack(
    rx.cond(
        qa.type == "system",
        system_message(qa),
        rx.cond(
            qa.from_intermediation == True,  # P NOUVEAU
            intermediation_message(qa),
            qa_message_container(qa),
        ),
    ),
    spacing="1",
    width="100%",
)
```

**Note**: Ajouter `from_intermediation: bool = False` dans la classe `QA` si n�cessaire

### 7. chat_apbookeeper.py - Bouton outils conditionnel

**Modifier** `editformstate_action_bar_job_id_view()`:

**Chercher** le bouton wrench et **entourer** avec une condition:
```python
# Bouton outils (wrench)
rx.cond(
    EditFormState.intermediation_active.get(EditFormState.current_job_id, False),  # P CONDITION
    rx.box(
        # Speed dial des outils
        rx.cond(
            EditFormState.show_tools,
            editformstate_tool_speed_dial_enhanced(),
            rx.box()
        ),

        # Bouton principal wrench
        rx.tooltip(
            rx.icon_button(...),
            content="Outils disponibles (Mode Interm�diation)",
        ),
        position="relative",
    ),
    rx.box()  # Masquer si pas en mode interm�diation
),
```

---

## >� Plan de Tests

### Test 1: CARD � Interm�diation (apbookeeper_chat)
1. Ouvrir job en mode `apbookeeper_chat` (status="running")
2. Application m�tier envoie CARD
3. **V�rifier**:
   -  Message "Mode Interm�diation Activ�" appara�t
   -  Bouton outils devient visible
   -  Liste des outils affich�e dans message syst�me

### Test 2: Messages utilisateur � Application m�tier
1. En mode interm�diation, envoyer message
2. **V�rifier**:
   -  Message envoy� � RTDB de l'app m�tier (pas LLM)
   -  R�ponse affich�e avec badge "Application M�tier"
   -  Bordure orange + fond orange clair

### Test 3: Cloture par mot de terminaison
1. En mode interm�diation, taper "TERMINATE"
2. **V�rifier**:
   -  CLOSE_INTERMEDIATION �crit en RTDB
   -  Message "Mode Interm�diation Termin�" appara�t
   -  Bouton outils dispara�t
   -  Messages suivants vont au LLM

### Test 4: Chargement historique - Job actif
1. Fermer/rouvrir chat (job status="running")
2. Historique contient CARD sans CLOSE_INTERMEDIATION
3. **V�rifier**:
   -  Mode interm�diation r�activ�
   -  Message syst�me affich�
   -  Bouton outils visible

### Test 5: Chargement historique - Job termin�
1. Ouvrir chat (job status="completed")
2. Historique contient CARD sans CLOSE_INTERMEDIATION
3. **V�rifier**:
   -  Mode interm�diation NON r�activ�
   -  Pas de message syst�me
   -  Bouton outils cach�

### Test 6: Mode non concern� (onboarding_chat)
1. Ouvrir job en mode `onboarding_chat`
2. Application m�tier envoie CARD
3. **V�rifier**:
   -  CARD affich� normalement
   -  Pas de mode interm�diation
   -  Messages vont au LLM

---

## =� Notes Importantes

### � Messages syst�me
- **Visible** dans le chat frontend
- **NON sauvegard�** en RTDB
- **Format**: Suit normes industrie (ic�nes, couleurs, hi�rarchie)

### � Modes concern�s
- **Avec interm�diation**: `apbookeeper_chat`, `router_chat`, `banker_chat`
- **Sans interm�diation**: `onboarding_chat`, `general_chat`

### � Job status critique
- **Fourni par frontend** lors de `enter_chat`
- **Job actif**: `status in ['running', 'in queue']`
- **R�activation conditionnelle** au chargement selon job_status

### � Outils disponibles
- **Fournis dans message** CARD/FOLLOW_MESSAGE
- **Champ**: `tools_config` ou `tools`
- **Format**: `[{"name": "...", "description": "..."}]`

---

##  Checklist

### Backend  TERMIN�
- [x] `_start_intermediation_mode()` cr��e
- [x] `_stop_intermediation_mode()` cr��e
- [x] FOLLOW_MESSAGE utilise nouvelle m�thode
- [x] CLOSE_INTERMEDIATION utilise nouvelle m�thode
- [x] CARD d�marre interm�diation (modes concern�s)
- [x] `_handle_intermediation_response()` modifi�e
- [x] `_check_intermediation_on_load()` supporte job_status + CARD
- [x] Signatures `enter_chat` et `start_onboarding_chat` acceptent job_status
- [x] Tous les appels � `_check_intermediation_on_load` passent job_status

### Frontend ✅ TERMINÉ
- [x] Variables `intermediation_active` et `intermediation_tools` ajoutées
- [x] Handler `handle_rpc_intermediation_state()` créé
- [x] Intégration WebSocket pour RPC_INTERMEDIATION_STATE
- [x] Appel `enter_chat` passe job_status
- [x] Support messages SYSTEM_MESSAGE_INTERMEDIATION
- [x] Style spécial messages intermédiation (badge orange)
- [x] Bouton outils conditionnel
- [x] **FIX CRITIQUE**: Ajout des types `RPC_` et `SYSTEM_MESSAGE_` dans `kinds` (listener_manager.py:375)
- [ ] Tests complets

---

## 🐛 FIX CRITIQUE - Messages Intermédiation Non Reçus

**Date**: 08/11/2025 - 16:00
**Problème**: Messages `RPC_INTERMEDIATION_STATE` et `SYSTEM_MESSAGE_INTERMEDIATION` n'apparaissaient pas dans le frontend

### Diagnostic

**Symptômes**:
- Backend loguait l'envoi des messages avec succès
- Frontend ne recevait AUCUN message (pas de logs de réception)
- System messages et outils invisibles

**Root Cause Identifiée**:
Le `ListenerManager` filtrait les messages selon une liste de types autorisés (`kinds`).

**Fichier**: `pinnokio_app/listeners/manager.py`, ligne 374

**AVANT** (ligne 374):
```python
kinds = ["llm_stream", "tool_use", "plan_", "lpt_", "chat"]
```

**APRÈS** (ligne 375):
```python
kinds = ["llm_stream", "tool_use", "plan_", "lpt_", "chat", "RPC_", "SYSTEM_MESSAGE_"]
```

### Explication

Le `BusConsumer` filtre les messages Redis/WebSocket par **préfixe de type**:
- `llm_stream` → Accepte `llm_stream_start`, `llm_stream_chunk`, etc.
- `chat` → Accepte `CARD`, `FOLLOW_MESSAGE`, etc.
- **Bloque** tout ce qui ne match pas ces préfixes

Les messages `RPC_INTERMEDIATION_STATE` et `SYSTEM_MESSAGE_INTERMEDIATION` ne correspondaient à AUCUN préfixe autorisé → **Messages jetés avant même d'atteindre `editformstate_handle_realtime_message`**.

### Solution

Ajout de 2 nouveaux préfixes dans la liste `kinds`:
- `RPC_` → Accepte tous les signaux RPC (extensible pour futurs types)
- `SYSTEM_MESSAGE_` → Accepte tous les messages système

✅ Les messages passent maintenant le filtre du `BusConsumer` et arrivent correctement au handler frontend.

---

## 🔧 CORRECTIONS POST-TESTS - 08/11/2025 18:00

### Problèmes Détectés Lors des Tests Utilisateur

Après les premiers tests, 3 problèmes ont été identifiés :

1. ❌ **Carte interactive n'apparaît pas au chargement** - La carte déjà envoyée ne se réaffiche pas
2. ❌ **Carte ne disparaît pas lors des mots de terminaison** - TERMINATE/NEXT/PENDING ne masquent pas la carte
3. ❌ **Bouton outils et liste des outils invisibles** - Le wrench apparaît mais le speed dial est vide

---

### Correction 1 : Renvoyer la Carte au Chargement (Backend)

**Fichier** : [llm_manager.py:4069-4145](app/llm_service/llm_manager.py#L4069-L4145)

**Modifications dans `_check_intermediation_on_load()`** :

1. **Ajout détection carte cliquée** (ligne 4073-4074) :
   ```python
   has_card_clicked = False  # Nouveau flag
   card_message = None  # Stocker le message CARD pour renvoi
   ```

2. **Sauvegarder message CARD** (ligne 4076-4078) :
   ```python
   if last_msg_type == 'CARD':
       card_message = last_msg
   ```

3. **Détecter CARD_CLICKED_PINNOKIO** (ligne 4091-4097) :
   ```python
   elif msg_type == 'CARD_CLICKED_PINNOKIO':
       has_card_clicked = True
       logger.debug(...)
       break
   ```

4. **Renvoyer carte via WebSocket** (ligne 4130-4145) :
   ```python
   if card_message and not has_card_clicked:
       from ..ws_hub import hub
       ws_channel = f"chat:{session.context.user_id}:{collection_name}:{thread_key}"

       await hub.broadcast(session.context.user_id, {
           "type": "CARD",
           "channel": ws_channel,
           "payload": card_message
       })

       logger.info(f"[INTERMEDIATION_LOAD] 🃏 Carte renvoyée au chargement - ...")
   ```

**Logique** :
- ✅ Détecte si la dernière carte a été cliquée
- ✅ Si carte non cliquée + job actif → Renvoie la carte via WebSocket
- ✅ Format identique à l'envoi initial

---

### Correction 2 : Masquer Carte lors des Mots de Terminaison (Frontend)

**Fichier** : [EditFormState.py:656-679](C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\EditFormState.py#L656-L679)

**Modifications dans `handle_rpc_intermediation_state()` action "stop"** :

**AVANT** :
```python
elif action == "stop":
    self.intermediation_active[thread_key] = False
    if thread_key in self.intermediation_tools:
        del self.intermediation_tools[thread_key]
    # ❌ Carte restait affichée
```

**APRÈS** (ligne 656-679) :
```python
elif action == "stop":
    self.intermediation_active[thread_key] = False

    # Nettoyer les outils
    if thread_key in self.intermediation_tools:
        del self.intermediation_tools[thread_key]

    # ⭐ MASQUER LA CARTE INTERACTIVE (comme lors du clic sur carte)
    self.show_interactive_card = False
    self.show_tools = False
    self.show_actions = False
    self.chat_input_enabled = False

    # Réinitialiser l'état des outils
    self.available_tools = []
    self.tool_is_chosen = False
    self.selected_tool = ""
    self.chosen_icon = "wrench"

    reason = payload.get("reason", "unknown")
    print(f"✅ [FRONTEND] Mode intermédiation DÉSACTIVÉ pour {thread_key}")
    print(f"   → Raison: {reason}")
    print(f"   → Carte et outils masqués")
```

**Comportement** :
- ✅ Masque la carte (`show_interactive_card = False`)
- ✅ Désactive le speed dial (`show_tools = False`)
- ✅ Réinitialise tous les états visuels
- ✅ Identique au comportement lors du clic sur carte

---

### Correction 3 : Charger Outils dans le Speed Dial (Frontend)

**Fichier** : [EditFormState.py:636-654](C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\EditFormState.py#L636-L654)

**Modifications dans `handle_rpc_intermediation_state()` action "start"** :

**AVANT** :
```python
if action == "start":
    self.intermediation_active[thread_key] = True
    self.intermediation_tools[thread_key] = payload.get("tools_config", [])
    # ❌ Outils stockés mais jamais copiés dans available_tools
    # ❌ Speed dial restait vide
```

**APRÈS** (ligne 636-654) :
```python
if action == "start":
    self.intermediation_active[thread_key] = True
    self.intermediation_tools[thread_key] = payload.get("tools_config", [])

    # ⭐ CHARGER LES OUTILS DANS LE SPEED DIAL
    tools_config = payload.get("tools_config", [])
    if tools_config:
        # Les outils sont déjà formatés côté backend avec name, value, icon_key, placeholder
        # On les charge directement dans available_tools pour le speed dial
        self.available_tools = tools_config
        self.show_tools = True  # Active l'affichage du speed dial au hover
        self.show_actions = True  # Active la zone d'actions
        self.chat_input_enabled = True  # Permet la saisie
        print(f"🔄 [FRONTEND] Mode intermédiation ACTIVÉ pour {thread_key}")
        print(f"   → {len(tools_config)} outils chargés dans le speed dial")
    else:
        print(f"🔄 [FRONTEND] Mode intermédiation ACTIVÉ pour {thread_key}")
        print(f"   → Aucun outil disponible")
```

**Format attendu des outils** (fourni par le backend dans `tools_config`) :
```python
[
    {
        "name": "Outil X",
        "value": "TOOL_X",
        "icon_key": "wrench",
        "placeholder": "Entrez les paramètres..."
    },
    ...
]
```

**Comportement** :
- ✅ Copie `tools_config` dans `available_tools`
- ✅ Active le speed dial (`show_tools = True`)
- ✅ Permet l'affichage des outils au hover du wrench
- ✅ Les outils apparaissent directement sans filtrage YAML

---

### Résumé des Fichiers Modifiés

| Fichier | Ligne(s) | Type | Description |
|---------|----------|------|-------------|
| [llm_manager.py](app/llm_service/llm_manager.py) | 4069-4145 | Backend | Renvoie carte au chargement si non cliquée |
| [EditFormState.py](C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\EditFormState.py) | 636-654 | Frontend | Charge outils dans speed dial au START |
| [EditFormState.py](C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\EditFormState.py) | 656-679 | Frontend | Masque carte et outils au STOP |

---

### Checklist Mise à Jour

- [x] **Correction 1** : Carte renvoyée au chargement (backend)
- [x] **Correction 2** : Carte masquée lors terminaison (frontend)
- [x] **Correction 3** : Outils chargés dans speed dial (frontend)
- [x] **Correction 4** : Format des outils (backend + frontend)
- [ ] **Tests** : Valider les 4 corrections en conditions réelles

---

## 🔧 CORRECTION 4 : Format des Outils (08/11/2025 19:00)

### Problème Identifié

**Incompatibilité de format** entre le backend et le frontend pour les outils.

**Flux actuel dans l'application métier (klk_accountant)** :
1. Envoie message `TOOL` avec `tool_list: ["TOOL_1", "TOOL_2"]` (juste les noms)
2. Frontend reçoit et appelle `load_tools(tool_list)` qui filtre depuis `config_tools.json`
3. Les outils complets sont chargés avec `{name, value, icon_key, placeholder}`

**Notre implémentation (incorrecte)** :
1. Backend extrait `tools_config` du message CARD (format Anthropic avec `{name, description, input_schema}`)
2. Backend envoie ce format Anthropic directement au frontend via RPC
3. Frontend s'attend au format de `config_tools.json` → **INCOMPATIBILITÉ**

### Solution Appliquée

**Principe** : Préserver la logique existante en envoyant juste les **noms** des outils.

#### Backend (llm_manager.py:3294-3365)

**AVANT** :
```python
tools_config = message.get("tools_config") or message.get("tools") or []
# Envoie le format Anthropic complet au frontend
```

**APRÈS** :
```python
# Extraire les outils au format Anthropic
tools_config_anthropic = message.get("tools_config") or message.get("tools") or []

# ⭐ EXTRAIRE UNIQUEMENT LES NOMS (comme send_tools_list le fait)
tool_names = [tool.get("name") for tool in tools_config_anthropic if isinstance(tool, dict) and "name" in tool]

# Envoyer au frontend via RPC
"tool_names": tool_names  # ⭐ Liste de strings ["TOOL_1", "TOOL_2"]
```

**Résultat** :
- Message système affiche la liste avec descriptions (depuis Anthropic)
- RPC envoie juste les noms au frontend

#### Frontend (EditFormState.py:636-655)

**AVANT** :
```python
tools_config = payload.get("tools_config", [])
self.available_tools = tools_config  # Attendait format config_tools.json
```

**APRÈS** :
```python
# Récupérer les noms des outils
tool_names = payload.get("tool_names", [])
self.intermediation_tools[thread_key] = tool_names

# ⭐ Charger depuis config_tools.json (logique existante)
self.load_tools(tool_names)  # Filtre et charge les outils complets
```

**Flux complet** :
1. Application métier envoie outils Anthropic : `[{name, description, input_schema}, ...]`
2. Backend extrait les noms : `["TOOL_1", "TOOL_2"]`
3. Backend envoie au frontend : `{"tool_names": ["TOOL_1", "TOOL_2"]}`
4. Frontend charge depuis `config_tools.json` : `[{name, value, icon_key, placeholder}, ...]`
5. Speed dial affiche les outils avec icônes et placeholders

### Fichiers Modifiés

| Fichier | Lignes | Description |
|---------|--------|-------------|
| [llm_manager.py](app/llm_service/llm_manager.py) | 3294-3305 | Extraction des noms depuis format Anthropic |
| [llm_manager.py](app/llm_service/llm_manager.py) | 3357 | RPC envoie `tool_names` au lieu de `tools_config` |
| [EditFormState.py](C:\Users\Cedri\Coding\pinnokio_app\pinnokio_app\state\EditFormState.py) | 642-647 | Utilise `load_tools()` pour charger depuis config |

### Avantages

✅ **Préserve la logique existante** : `load_tools()` est déjà testée et fonctionne
✅ **Séparation des responsabilités** : Backend envoie les noms, frontend gère l'affichage
✅ **Cohérence** : Même logique que les messages `TOOL` classiques
✅ **Extensibilité** : Facile d'ajouter de nouveaux outils dans `config_tools.json`

---

## 🔧 CORRECTION 5 : Support Message TOOL - 08/11/2025 17:00

### Problèmes Détectés

Après tests utilisateur, 2 nouveaux problèmes identifiés :

1. ❌ **Message TOOL ne déclenche pas le mode intermédiation** - Les outils arrivent mais le bouton wrench n'apparaît pas
2. ❌ **Mode non reconnu au rechargement** - Quand l'utilisateur revient sur la page après réception d'un TOOL

### Analyse

**Format du message TOOL** (depuis l'application métier) :
```json
{
  "message_type": "TOOL",
  "content": {
    "tool_list": ["send_file_to_user", "GET_CONTACT_INFO_IN_ODOO", "VIEW_DOCUMENT_WITH_VISION", "SEARCH_IN_CHART_OF_ACCOUNT"]
  }
}
```

**Différence avec CARD** :
- CARD : `tools_config` au format Anthropic `[{name, description, input_schema}, ...]`
- TOOL : `content.tool_list` au format simple `["TOOL_1", "TOOL_2"]`

### Solution Appliquée

#### Modification 1 : Support format `tool_list` dans `_start_intermediation_mode()`

**Fichier** : `app/llm_service/llm_manager.py` (lignes 3294-3340)

**Ajout logique** :
```python
# Vérifier si c'est un message TOOL avec format simple
message_content = message.get("content", {})
if isinstance(message_content, dict):
    tool_list_simple = message_content.get("tool_list")
    if tool_list_simple:
        # Format simple : liste de strings ["TOOL_1", "TOOL_2"]
        tool_names = tool_list_simple if isinstance(tool_list_simple, list) else []
```

Supporte maintenant les 2 formats :
- ✅ Format Anthropic (CARD/FOLLOW_MESSAGE)
- ✅ Format simple (TOOL)

#### Modification 2 : Activer intermédiation pour message TOOL

**Fichier** : `app/llm_service/llm_manager.py` (lignes 3896-3927)

**Nouveau bloc** ajouté dans `_handle_onboarding_log_event()` :
```python
elif message_type == "TOOL":
    # Envoyer via WebSocket
    await self._send_non_message_via_websocket(...)
    
    # ⭐ NOUVEAU : Activer mode intermédiation
    if session.context.chat_mode in ("apbookeeper_chat", "router_chat", "banker_chat"):
        await self._start_intermediation_mode(
            session=session,
            user_id=user_id,
            collection_name=collection_name,
            thread_key=thread_key,
            message=message,
            job_id=job_id
        )
```

#### Modification 3 : Détecter TOOL au chargement

**Fichier** : `app/llm_service/llm_manager.py` (ligne 4139)

**Avant** :
```python
if last_msg_type in ('FOLLOW_MESSAGE', 'CARD'):
```

**Après** :
```python
if last_msg_type in ('FOLLOW_MESSAGE', 'CARD', 'TOOL'):
```

#### Modification 4 : Appeler vérification dans `enter_chat()`

**Fichier** : `app/llm_service/llm_manager.py` (lignes 2526-2533)

**Ajout** après `_ensure_onboarding_listener()` :
```python
# ⭐ NOUVEAU : Vérifier mode intermédiation au chargement
await self._check_intermediation_on_load(
    session=session,
    collection_name=collection_name,
    thread_key=thread_key,
    job_status=job_status
)
```

**Raison** : `enter_chat()` est appelé quand l'utilisateur revient sur la page, mais ne vérifiait pas le mode intermédiation (contrairement à `start_onboarding_chat()` qui l'appelait déjà).

### Fichiers Modifiés

| Fichier | Lignes | Description |
|---------|--------|-------------|
| `app/llm_service/llm_manager.py` | 3294-3340 | Support format `tool_list` simple |
| `app/llm_service/llm_manager.py` | 3896-3927 | Activation intermédiation pour TOOL |
| `app/llm_service/llm_manager.py` | 4139 | Détection TOOL au chargement |
| `app/llm_service/llm_manager.py` | 2526-2533 | Appel vérification dans `enter_chat()` |

### Flux Corrigé

#### Réception en direct (écoute active)
1. Application métier envoie message TOOL avec `content.tool_list`
2. `_handle_onboarding_log_event()` reçoit le message
3. Message routé via WebSocket (existant)
4. **✅ NOUVEAU** : `_start_intermediation_mode()` appelée
5. Extraction des outils depuis `content.tool_list`
6. Signal RPC + message système envoyés au frontend
7. ✅ Bouton wrench activé avec outils

#### Rechargement de session
1. User revient sur la page → `enter_chat()` appelé
2. Brain chargé/récupéré
3. Listener métier démarré
4. **✅ NOUVEAU** : `_check_intermediation_on_load()` appelée
5. Détecte dernier message = TOOL (sans CLOSE après)
6. Vérifie job_status (running/in queue)
7. ✅ Réactive mode intermédiation si job actif
8. ✅ Renvoie outils via RPC au frontend
9. ✅ Bouton wrench réapparaît

### Checklist Mise à Jour

- [x] **Modification 1** : Support format `tool_list` dans `_start_intermediation_mode()`
- [x] **Modification 2** : Activer intermédiation pour message TOOL
- [x] **Modification 3** : Détecter TOOL au chargement
- [x] **Modification 4** : Appeler vérification dans `enter_chat()`
- [ ] **Tests** : Valider les 4 modifications en conditions réelles

---

## =☑ Prochaines Étapes

1. ✅ **Appliquer modifications frontend** dans EditFormState.py
2. ✅ **Appliquer modifications visuelles** dans chat_apbookeeper.py
3. ✅ **FIX CRITIQUE**: Débloquer la réception des messages d'intermédiation
4. ✅ **CORRECTIONS POST-TESTS**: Résoudre les 3 problèmes détectés
5. ✅ **CORRECTION 5**: Support message TOOL avec activation intermédiation
6. ➡️ **Tester chaque flux** selon plan de tests avec les corrections
7. **Ajuster design** messages système si nécessaire
8. **Valider cohérence** états frontend/backend
