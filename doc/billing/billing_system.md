## Facturation (microservice) — Règles & architecture (précis)

Cette documentation décrit **l’implémentation effective** dans ce repo `firebase_microservice`.

> L’ancien modèle “SQLite local + finalize_job_billing destructif” n’est **pas** utilisé ici.

---

## Objectif global
- **Mesurer** l’usage LLM (tokens input/output) par provider et modèle.
- **Valoriser** cet usage via un barème (buy/sales).
- **Persister** de façon **centralisée** et **multi-instance safe** (Firestore).
- **Facturer** le chat sous forme d’**expenses journalières** consommées par le wallet existant.

---

## Glossaire & identifiants (contrats)
- **`user_id`**: Firebase user id.
- **`collection_name`**: identifiant société / space_code.
- **`mandate_path`**: chemin Firestore complet du mandat:
  - `clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}`
- **Tour LLM**: itération de `_process_unified_workflow` (peut être texte, tool_use, tool_result, etc.)
- **Bucket chat journalier** = “job virtuel”:
  - `job_id = chat:{user_id}:{collection_name}:{YYYY-MM-DD}` (UTC)
  - `project_id = chat_{user_id}_{collection_name}_{YYYY-MM-DD}`

---

## Architecture (vue d’ensemble)
### Sources de données
- **Tokens**: récupérés depuis les réponses providers (OpenAI/Groq/Anthropic).
- **Prix**: calculés via `ModelPricing.PRICE_STRUCTURE` + `ModelPricing.calculate_token_cost`.
- **Mandate**: `mandate_path` provient de `session.user_context` (chargé lors de l’initialisation de session).

### Pipeline Chat (résumé)
1) **À la fin de chaque tour LLM**:
- calcul tokens + prix côté providers
- enregistrement Firestore: 1 event idempotent + agrégats sur parent

2) **Finalisation**:
- création / MAJ des `expenses` journalières
- application au solde via `get_user_balance` (wallet)

---

## Composants (fichiers & responsabilités)
- **Pricing**
  - `app/llm/klk_agents.py`
    - `ModelPricing.PRICE_STRUCTURE`
    - `ModelPricing.calculate_token_cost(...)`
- **Capture tokens + persistence Firestore**
  - `app/llm/klk_agents.py`
    - `BaseAIAgent.get_token_usage_by_provider()`
    - `BaseAIAgent.load_token_usage_to_db(...)`
  - `app/firebase_providers.py`
    - `FirebaseManagement.upload_token_usage(...)` (transaction + increments)
- **Hook conversation**
  - `app/llm_service/llm_manager.py`
    - `_process_unified_workflow` (appel billing “par tour”)
- **Wallet**
  - `app/firebase_providers.py`
    - `FirebaseManagement.get_user_balance(...)` (consomme expenses/topups)
- **CRON (Celery Beat)**
  - `app/maintenance_tasks.py`
    - `finalize_daily_chat_billing(target_date=None, days_back=7)`
  - `app/task_service.py`
    - `celery_app.conf.beat_schedule` (horaire + days_back)
- **Catch-up (rattrapage) non-bloquant au niveau session**
  - `app/llm_service/llm_manager.py`
    - `_ensure_session_initialized` (fire-and-forget + garde-fou Redis)

---

## Modèle de données Firestore (source de vérité)
### 1) Token usage — agrégats (parent)
Chemin:
- `clients/{user_id}/token_usage/{job_id}`

Rôle:
- doc **agrégé** (recherche rapide, totalisation, création d’expense).

Champs (règle):
- **Identité**
  - `user_id` (string)
  - `job_id` (string)
  - `project_id` (string)
  - `collection_name` (string)
  - `mandate_path` (string)
- **Classification**
  - `function` (string, ex: `chat`)
  - `billing_kind` (string, ex: `chat_daily`)
  - `billing_date` (string `YYYY-MM-DD`)
    - dérivée automatiquement de `job_id` si pattern `chat:{user}:{collection}:{YYYY-MM-DD}`
- **Agrégats**
  - `entries_count` (int, increment)
  - `total_input_tokens` (int, increment)
  - `total_output_tokens` (int, increment)
  - `total_tokens` (int, increment) = `total_input_tokens + total_output_tokens`
  - `total_buy_price` (float, increment)
  - `total_sales_price` (float, increment)
- **Timestamps**
  - `last_entry_at` (SERVER_TIMESTAMP)

### 2) Token usage — events (détail idempotent)
Chemin:
- `clients/{user_id}/token_usage/{job_id}/entries/{entry_id}`

Rôle:
- audit (preuve), debugging, traçabilité.

Champs (minimum):
- `provider_name`
- `provider_model_name`
- `workflow_step`
- `total_input_tokens`, `total_output_tokens`
- `total_tokens` (= input + output)
- `buy_price`, `sales_price`, `output_mode`
- `thread_key`, `message_id`, `timestamp`
- `collection_name`, `mandate_path`, `function`

### 3) Expenses (consommées par le wallet)
Chemin:
- `{mandate_path}/billing/topping/expenses/{job_id}`

Rôle:
- source de vérité “facturable” pour le wallet.

Champs minimum requis par `get_user_balance`:
- `total_sales_price` (float)
- `billed` (bool)

Champs ajoutés par la facturation chat:
- `function = "chat"`
- `file_name = "Chat usage DD/MM/YYYY"`
- `total_input_tokens`, `total_output_tokens`, `total_tokens`

### 4) Wallet
Chemin:
- `clients/{user_id}/billing/current_balance`

Champs:
- `current_topping`
- `current_expenses`
- `current_balance = current_topping - current_expenses`

---

## Règles d’idempotence (anti double comptage)
### A) Idempotence côté token_usage
But:
- éviter l’**addition multiple** des tokens/prix si un tour est rejoué (retries, scaling).

Mécanisme:
- `upload_token_usage` utilise une transaction Firestore:
  - si `entries/{entry_id}` existe → **ne pas** incrémenter les agrégats.

Convention d’ID (implémentation actuelle):
- `entry_id_base = "{message_id}:{turn_count}"`
- `entry_id_final = "{entry_id_base}:{provider_name}"`

Point d’attention (important):
- L’idempotence dépend de la **stabilité** de `message_id` en cas de retry.
  - Si un retry génère un nouveau `message_id`, un doublon est possible.
  - Recommandation produit: préférer un `user_message_id` stable côté UI si disponible (amélioration future).

### B) Idempotence côté wallet
Risque:
- deux process exécutent `get_user_balance()` en parallèle → double incrément `current_expenses`.

Protection en place:
- lock Redis dans `get_user_balance`:
  - clé: `lock:billing:balance:{user_id}`
  - TTL ~120s

Mode dégradé:
- si Redis down → lock “fail-open” (continuité de service, risque accru de concurrence).

---

## Capture tokens — règles streaming (source des tokens)
Problème classique:
- en streaming, le champ `usage` peut arriver **uniquement sur le dernier chunk**.
- ce dernier chunk peut avoir `choices=[]`.

Règle:
- on lit l’`usage` **même si `choices=[]`**, puis on appelle `update_token_usage`.

Compatibilité:
- `update_token_usage` accepte `prompt_tokens/completion_tokens` **ou** `input_tokens/output_tokens`.

---

## Finalisation (CRON) — règles exactes
### Tâche
- `app.maintenance_tasks.finalize_daily_chat_billing(target_date=None, days_back=7)`

Planification effective (repo):
- Celery Beat: **toutes les heures** (ex: `:20 UTC`) avec `days_back=7`.

Règles:
- par défaut (sans `target_date`), traiter les **N derniers jours hors aujourd’hui** (rattrapage).
- pour chaque date:
  - query collection group `token_usage` où:
    - `billing_kind == "chat_daily"`
    - `billing_date == YYYY-MM-DD`
  - upsert `{mandate_path}/billing/topping/expenses/{job_id}` avec `billed=false`
  - appeler `get_user_balance(mandate_path)` (lock Redis)

---

## Catch-up (rattrapage) au niveau session (non-bloquant)
Déclenchement:
- `_ensure_session_initialized` (quand on charge/crée la session user+collection).

Comportement:
- fire-and-forget:
  - `ensure_chat_daily_expenses(mandate_path, collection_name, days_back=7)`
  - puis `get_user_balance(mandate_path)`

Garde-fou:
- Redis key: `billing:catchup:{user_id}:{collection_name}` avec TTL 1h (évite spam / multi-instance).

---

## Rétention (suppression des buckets) — règle actuelle
Actuel:
- on **ne supprime pas** les `token_usage` ni `entries/` (audit).
- on évite la double facturation via:
  - `billed=true` sur `expenses`
  - idempotence `entries/{entry_id}`
  - lock Redis wallet

Option future (non implémentée):
- purge des `entries/` après X jours (conserver uniquement l’agrégé parent).
