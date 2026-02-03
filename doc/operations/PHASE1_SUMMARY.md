# ✅ Phase 1 COMPLÉTÉE: MICROSERVICE - Structure Agentic

## 📁 Fichiers créés

### 1. `app/llm_service/agent_config.py`
**Gestionnaire de configuration des agents par chat_mode**

✅ **Créé avec succès**

**Contenu**:
- Class `AgentConfigManager`
- Prompt system `ONBOARDING_SYSTEM_PROMPT` (détaillé pour l'onboarding)
- Config dict `AGENT_CONFIGS` pour 5 modes:
  - `general_chat`
  - `onboarding_chat`
  - `edit_router_form`
  - `edit_form`
  - `bank_detail`

**Méthodes principales**:
```python
AgentConfigManager.get_config(chat_mode)  # Récupère config
AgentConfigManager.inject_context_data(prompt, context)  # Injecte données entreprise
AgentConfigManager.is_rtdb_listening_enabled(mode)  # Vérifie écoute RTDB
AgentConfigManager.get_message_log_container_id(mode)  # ID container logs
```

---

### 2. `app/llm/agent_log_container.py`
**Mixin pour gestion du message_log_container dans BaseAIAgent**

✅ **Créé avec succès**

**Contenu**:
- Class `LogContainerMixin`

**Méthodes principales**:
```python
# À intégrer dans BaseAIAgent
init_log_container_attributes()  # Initialiser attributs
set_message_log_container_id(id)  # Définir ID container
inject_or_update_log_message(content, timestamp)  # Inject/update logs
get_log_container_content()  # Récupérer contenu actuel
clear_log_container()  # Effacer logs
```

**⚠️ INTÉGRATION MANUELLE REQUISE** dans `app/llm/klk_agents.py`:
```python
# 1. Import
from .agent_log_container import LogContainerMixin

# 2. Hériter
class BaseAIAgent(LogContainerMixin):

# 3. Initialiser dans __init__
self.job_id = job_id
self.init_log_container_attributes()  # ✅ Ajouter
```

---

### 3. `app/listeners/onboarding_rtdb_listener.py` ⚠️ DÉPRÉCIÉ

**Note** : L'écoute RTDB a été migrée vers PubSub Redis. Voir `docs/architecture/ONBOARDING_MANAGER_PUBSUB_MIGRATION.md` pour les détails.
**Listener RTDB pour mode onboarding_chat**

✅ **Créé avec succès**

**Contenu**:
- Class `OnboardingRTDBListener`
- Function `get_onboarding_rtdb_listener()` (singleton)

**Méthodes principales**:
```python
# Démarrer écoute RTDB
await listener.start_listening(
    mandate_path, job_id, collection_name, thread_key,
    on_event_callback
)

# Arrêter écoute
await listener.stop_listening(mandate_path, job_id)

# Récupérer listeners actifs
active = await listener.get_active_listeners()
```

**Écoute sur**:
- `mandate_path/onboarding_activity/{job_id}`

**Détecte 4 types d'événements**:
1. **LOG** (`/logs/*`) → Logs de progression
2. **TEXT_QUESTION** (`/questions/*`) → Questions agent métier
3. **INTERACTIVE_CARD** (`/cards/*`) → Cartes interactives
4. **CONTROL** (`/control/*`) → Commandes (TERMINATE, NEXT, PENDING)

---

### 4. `app/llm_service/rtdb_to_wss_formatter.py`
**Formateur d'événements RTDB → WSS**

✅ **Créé avec succès**

**Contenu**:
- Class `RTDBToWSSFormatter`
- Helper functions (`aggregate_logs`, `validate_card_data`)

**Méthodes principales**:
```python
# Formater log pour agent (pas de WSS)
format_log_for_agent(log_content, timestamp)

# Formater question pour WSS
format_text_question_for_wss(question, question_id, sender, timestamp)

# Formater carte pour WSS
format_interactive_card_for_wss(card_data, card_id, timestamp)

# Formater confirmation commande contrôle
format_control_response_for_wss(command, timestamp)

# Vérifier si mot-clé contrôle
is_control_keyword(user_message)

# Extraire mot-clé contrôle
extract_control_keyword(user_message)

# Formater réponse carte pour RTDB
format_card_response_for_rtdb(card_id, user_choice, user_id, timestamp)

# Formater commande contrôle pour RTDB
format_control_command_for_rtdb(command, timestamp)
```

---

### 5. `app/listeners/__init__.py`
**Module d'export pour listeners**

✅ **Créé avec succès**

Exporte:
- `OnboardingRTDBListener`
- `get_onboarding_rtdb_listener`

---

## 🔧 Intégrations manuelles requises

### Intégration 1: BaseAIAgent
**Fichier**: `app/llm/klk_agents.py`

```python
# 1. Ajouter import en haut
from .agent_log_container import LogContainerMixin

# 2. Modifier déclaration classe
class BaseAIAgent(LogContainerMixin):

# 3. Dans __init__, après self.job_id
self.job_id = job_id
self.init_log_container_attributes()  # ✅ AJOUTER
```

### Intégration 2: LLMManager
**Fichier**: `app/llm_service/llm_manager.py`

```python
# 1. Ajouter import en haut
from .agent_config import AgentConfigManager
from ..listeners import get_onboarding_rtdb_listener

# 2. Modifier méthode initialize_agent pour accepter:
async def initialize_agent(
    self,
    user_id: str,
    collection_name: str,
    thread_key: str,
    chat_mode: str = 'general_chat',  # ✅ NOUVEAU
    context_data: dict = None,  # ✅ NOUVEAU
    tools: list = None,
    system_prompt: str = None,
    provider: ModelProvider = ModelProvider.ANTHROPIC,
    size: ModelSize = ModelSize.MEDIUM,
    mode: str = 'chats'
) -> str:
    # ✅ Récupérer config
    agent_config = AgentConfigManager.get_config(chat_mode)

    # ✅ Utiliser system_prompt de config si non fourni
    if not system_prompt:
        system_prompt = agent_config.get('system_prompt')

    # ✅ Injecter contexte
    if agent_config.get('context_injection') and context_data:
        system_prompt = AgentConfigManager.inject_context_data(
            system_prompt, context_data
        )

    # ✅ Utiliser tools de config
    if tools is None:
        tools = agent_config.get('tools', [])

    # Création session (code existant)
    session = await self._create_session(...)

    # ✅ Définir message_log_container_id
    log_container_id = AgentConfigManager.get_message_log_container_id(chat_mode)
    if log_container_id:
        session.agent.set_message_log_container_id(log_container_id)

    # ✅ Démarrer listener RTDB si nécessaire
    if AgentConfigManager.is_rtdb_listening_enabled(chat_mode):
        await self._start_rtdb_listener_for_mode(
            user_id, collection_name, thread_key, chat_mode
        )

    return session_id
```

**Nouvelle méthode à ajouter**:
```python
async def _start_rtdb_listener_for_mode(
    self,
    user_id: str,
    collection_name: str,
    thread_key: str,
    chat_mode: str
):
    """
    Démarre le listener RTDB approprié selon le chat_mode.
    """
    if chat_mode == 'onboarding_chat':
        await self._start_onboarding_rtdb_listener(
            user_id, collection_name, thread_key
        )
    elif chat_mode in ['edit_router_form', 'edit_form', 'bank_detail']:
        # À implémenter plus tard pour autres modes
        pass

async def _start_onboarding_rtdb_listener(
    self,
    user_id: str,
    collection_name: str,
    thread_key: str
):
    """
    Démarre le listener RTDB pour onboarding_chat.
    """
    from ..firebase_client import get_firestore

    # Extraire mandate_path et job_id de thread_key
    # Supposons que thread_key = job_id pour onboarding
    job_id = thread_key

    # Récupérer mandate_path depuis Firestore ou autre source
    # TODO: Adapter selon votre structure de données
    db = get_firestore()
    # ... logique pour récupérer mandate_path ...

    # Callback pour traiter les événements RTDB
    def on_rtdb_event(event_type: str, event_data: dict):
        """
        Callback appelé par OnboardingRTDBListener.
        """
        if event_type == 'LOG':
            # Injecter dans log container de l'agent
            session = self.sessions.get(...)  # Récupérer session
            if session and session.agent:
                session.agent.inject_or_update_log_message(
                    event_data['content'],
                    event_data['timestamp']
                )

        elif event_type == 'TEXT_QUESTION':
            # Ajouter au chat_history + envoi WSS
            # TODO: Implémenter traitement question
            pass

        elif event_type == 'INTERACTIVE_CARD':
            # Reformater et envoyer via WSS
            # TODO: Implémenter traitement carte
            pass

        elif event_type == 'CONTROL':
            # Traiter commande contrôle
            # TODO: Implémenter traitement contrôle
            pass

    # Démarrer listener
    listener = get_onboarding_rtdb_listener()
    await listener.start_listening(
        mandate_path=mandate_path,
        job_id=job_id,
        collection_name=collection_name,
        thread_key=thread_key,
        on_event_callback=on_rtdb_event
    )

    logger.info(f"🎧 Listener RTDB démarré pour onboarding: {job_id}")
```

---

## 📝 Notes importantes

1. **Phase 1 est 100% complétée côté fichiers créés**
2. **2 intégrations manuelles requises** (BaseAIAgent + LLMManager)
3. **Architecture extensible** pour edit_router_form, edit_form, bank_detail
4. **Pas de modifications dans les fichiers existants** (évite conflits)

---

## 🧪 Tests recommandés (après intégrations manuelles)

### Test 1: AgentConfigManager
```python
from app.llm_service.agent_config import AgentConfigManager

config = AgentConfigManager.get_config('onboarding_chat')
assert config['rtdb_listening'] == True
assert config['message_log_container_id'] == 'onboarding_logs_container'

prompt = AgentConfigManager.inject_context_data(
    config['system_prompt'],
    {'company_name': 'Test Corp', 'erp_system': 'odoo'}
)
assert 'Test Corp' in prompt
```

### Test 2: LogContainerMixin
```python
from app.llm.klk_agents import BaseAIAgent

agent = BaseAIAgent()
agent.set_message_log_container_id('test_container')
agent.inject_or_update_log_message('Premier log', '2025-01-01T10:00:00')
content = agent.get_log_container_content()
assert 'Premier log' in content

agent.inject_or_update_log_message('Mise à jour log', '2025-01-01T10:05:00')
content2 = agent.get_log_container_content()
assert 'Mise à jour log' in content2
assert 'Premier log' not in content2  # Remplacé
```

### Test 3: OnboardingRTDBListener
```python
from app.listeners import get_onboarding_rtdb_listener

listener = get_onboarding_rtdb_listener()

events_received = []

def callback(event_type, event_data):
    events_received.append((event_type, event_data))

await listener.start_listening(
    mandate_path='clients/test/mandates/test123',
    job_id='job_test',
    collection_name='test_collection',
    thread_key='thread_test',
    on_event_callback=callback
)

# Écrire dans RTDB pour tester
# ...

await listener.stop_listening('clients/test/mandates/test123', 'job_test')
```

### Test 4: RTDBToWSSFormatter
```python
from app.llm_service.rtdb_to_wss_formatter import RTDBToWSSFormatter

# Test question
question_wss = RTDBToWSSFormatter.format_text_question_for_wss(
    'Quelle est la clôture fiscale?',
    'q123'
)
assert question_wss['type'] == 'llm_stream_complete'
assert 'Quelle est la clôture fiscale' in question_wss['content']

# Test mot-clé contrôle
assert RTDBToWSSFormatter.is_control_keyword('NEXT') == True
assert RTDBToWSSFormatter.is_control_keyword('bonjour') == False

keyword = RTDBToWSSFormatter.extract_control_keyword('  next  ')
assert keyword == 'NEXT'
```

---

## 🚀 Prochaines étapes

1. ✅ **Effectuer les 2 intégrations manuelles** (BaseAIAgent + LLMManager)
2. ✅ **Tester Phase 1** avec les tests ci-dessus
3. ➡️ **Passer à Phase 2: REFLEX** (modifications ChatState)
