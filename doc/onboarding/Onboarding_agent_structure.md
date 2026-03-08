## Structure Agent Onboarding

### Flux général
- Le mode `onboarding_chat` repose sur la configuration déclarée dans `agent_modes.py`.
- `PinnokioBrain.load_onboarding_data()` charge une seule fois les métadonnées depuis Firebase (`clients/{user_id}/temp_data/onboarding`).
- À chaque `enter_chat` ou `load_chat_history`, le brain est alimenté avec ces données et le mode sélectionné.

### Logs métier
- Chaque job d'onboarding publie ses logs dans RTDB (`collection/job_chats/{job_id}/messages`) pour la persistance.
- Le jobbeur publie ensuite sur Redis PubSub (`user:{uid}/{collection}/job_chats/{job_id}/messages`) pour la communication temps réel.
- Le backend écoute ces logs via `RedisSubscriber` (pattern `user:*`) et route vers `llm_manager._handle_onboarding_log_event()`.
- Les logs sont stockés en mémoire dans la session et injectés dans l'historique LLM.

### Injection dans l’historique LLM
- Lors du chargement d’un brain (`load_chat_history`, `_resume_workflow_after_lpt`, `enter_chat`), `_load_onboarding_log_history` lit le message `LOG_FOLLOW_UP`, agrège les entrées et appelle `BaseAIAgent.append_system_log(job_id, timestamp, contenu)`.
- `append_system_log` remplace ou ajoute une entrée unique `[LOG] job_id|timestamp …` dans le `chat_history` du provider (Anthropic) et dans le cache du wrapper.
- Ainsi, les logs font partie intégrante du contexte lors des requêtes LLM (budget tokens, résumés, etc.).

### Écoute temps réel
- `_ensure_onboarding_listener` configure l'écoute PubSub en marquant la session comme active (plus de listener RTDB).
- Le `RedisSubscriber` centralisé écoute le pattern `user:*` et route les messages job_chats vers `_handle_job_chat_message()`.
- `_handle_onboarding_log_event` traite les messages reçus via PubSub et stocke les entrées en mémoire pour la session courante.
- `_stop_onboarding_listener` supprime simplement l'entrée du registre (plus de fermeture de listener RTDB nécessaire).
- **Note** : L'écoute RTDB a été complètement supprimée. Seule la persistance RTDB (lecture historique et écriture) est conservée.

### Points restants / TODO
- Définir la logique fine du handler côté frontend (affichage des logs, purge éventuelle).
- Ajouter des tests unitaires/end-to-end pour vérifier le rechargement du `LOG_FOLLOW_UP` et l’impact sur le contexte LLM.
- Éventuellement introduire des outils spécifiques au mode onboarding (actuellement aucun outil n’est activé).  
- Documenter l’usage côté application métier (comment formater les logs, conventions, etc.).

