## Structure Agent Onboarding

### Flux général
- Le mode `onboarding_chat` repose sur la configuration déclarée dans `agent_modes.py`.
- `PinnokioBrain.load_onboarding_data()` charge une seule fois les métadonnées depuis Firebase (`clients/{user_id}/temp_data/onboarding`).
- À chaque `enter_chat` ou `load_chat_history`, le brain est alimenté avec ces données et le mode sélectionné.

### Logs métier
- Chaque job d’onboarding publie ses logs dans RTDB (`collection/chats/{job_id}`).
- Le backend écoute ces logs via `listen_realtime_channel` et les recopie dans un message unique `collection/chats/follow_{job_id}/messages/{job_id}` avec `message_type = LOG_FOLLOW_UP`.
- Ce message contient l’ensemble des entrées (liste `log_entries`) et peut être écrasé/reconstitué.

### Injection dans l’historique LLM
- Lors du chargement d’un brain (`load_chat_history`, `_resume_workflow_after_lpt`, `enter_chat`), `_load_onboarding_log_history` lit le message `LOG_FOLLOW_UP`, agrège les entrées et appelle `BaseAIAgent.append_system_log(job_id, timestamp, contenu)`.
- `append_system_log` remplace ou ajoute une entrée unique `[LOG] job_id|timestamp …` dans le `chat_history` du provider (Anthropic) et dans le cache du wrapper.
- Ainsi, les logs font partie intégrante du contexte lors des requêtes LLM (budget tokens, résumés, etc.).

### Écoute temps réel
- `_ensure_onboarding_listener` démarre une écoute asynchrone uniquement si nécessaire et synchronise les entrées initiales (chargées depuis RTDB).
- `_handle_onboarding_log_event` se contente de mettre à jour le message RTDB suivi (`follow_{job_id}`) et de stocker les entrées en mémoire pour la session courante.
- `_stop_onboarding_listener` ferme automatiquement les écouteurs lors d’un `flush_chat_history` ou à l’arrêt du thread.

### Points restants / TODO
- Définir la logique fine du handler côté frontend (affichage des logs, purge éventuelle).
- Ajouter des tests unitaires/end-to-end pour vérifier le rechargement du `LOG_FOLLOW_UP` et l’impact sur le contexte LLM.
- Éventuellement introduire des outils spécifiques au mode onboarding (actuellement aucun outil n’est activé).  
- Documenter l’usage côté application métier (comment formater les logs, conventions, etc.).

