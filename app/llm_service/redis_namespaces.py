"""
Redis Namespaces - Conventions et constantes pour les clés Redis.

Ce module centralise les préfixes de clés Redis pour assurer la cohérence
et faciliter la maintenance.

Architecture Multi-Instance Ready:
    - Toutes les clés incluent user_id et company_id pour isolation
    - TTLs appropriés pour éviter les données orphelines
    - Préfixes explicites pour debug et monitoring

NAMESPACES ACTUELS:
    ─────────────────────────────────────────────────────────────────
    | Namespace              | Description                      | TTL    |
    ─────────────────────────────────────────────────────────────────
    | session:*              | État session LLM (stateless)     | 2h     |
    | chat:*:history         | Historique de chat               | 24h    |
    | chat:*                  | Canaux WebSocket (Pub/Sub)       | N/A    |
    | context:*              | Contexte utilisateur (metadata)  | 1h     |
    | cache:*                | Cache données métier (frontend)  | 1h     |
    | jobs:*                 | Données de jobs                  | 1h     |
    | pending_ws_messages:*  | Buffer messages WebSocket        | 5min   |
    | lock:cron:*            | Locks distribués CRON            | 5min   |
    ─────────────────────────────────────────────────────────────────

FORMATS DE CLÉS:
    - session:{user_id}:{company_id}:state
    - chat:{user_id}:{company_id}:{thread_key}:history
    - context:{user_id}:{company_id}
    - cache:{user_id}:{company_id}:{data_type}:{sub_type}
    - jobs:{user_id}:{company_id}:{department}
    - pending_ws_messages:{user_id}:{thread_key}
    - lock:cron:{task_id}
"""

from typing import Dict


# ═══════════════════════════════════════════════════════════════
# PRÉFIXES DE NAMESPACES
# ═══════════════════════════════════════════════════════════════

class RedisNamespace:
    """Constantes pour les préfixes de namespaces Redis."""
    
    # État session LLM (stateless architecture)
    SESSION = "session"
    
    # Historique de chat (multi-instance)
    CHAT = "chat"
    
    # Contexte utilisateur (metadata société)
    CONTEXT = "context"
    
    # Cache données métier (partagé avec frontend)
    CACHE = "cache"
    
    # Données de jobs (APBookkeeper, Router, Bank)
    JOBS = "jobs"
    
    # Buffer messages WebSocket
    WS_BUFFER = "pending_ws_messages"
    
    # Locks distribués
    LOCK = "lock"


# ═══════════════════════════════════════════════════════════════
# TTLs PAR DÉFAUT (en secondes)
# ═══════════════════════════════════════════════════════════════

class RedisTTL:
    """TTLs par défaut pour chaque type de donnée."""
    
    # Session: 2 heures (prolongé à chaque activité)
    SESSION = 7200
    
    # Chat history: 24 heures (conversations actives)
    CHAT_HISTORY = 86400
    
    # Contexte: 1 heure
    CONTEXT = 3600
    
    # Cache: 1 heure (données métier)
    CACHE = 3600
    
    # Jobs: 1 heure
    JOBS = 3600
    
    # Buffer WS: 5 minutes
    WS_BUFFER = 300
    
    # Lock CRON: 5 minutes (évite locks orphelins)
    LOCK = 300
    
    # HR Module: Données RH et paie
    HR_EMPLOYEES = 3600      # 1 heure (données modifiées occasionnellement)
    HR_CONTRACTS = 3600      # 1 heure (données stables)
    HR_REFERENCES = 86400    # 24 heures (données statiques: types contrat, etc.)
    HR_CLUSTERS = 86400      # 24 heures (configuration rarement modifiée)

    # Firebase Cache: Données Firebase centralisées dans le backend
    MANDATE_SNAPSHOT = 3600      # 1 heure (données société)
    EXPENSES_DETAILS = 2400      # 40 minutes (liste dépenses)
    AP_DOCUMENTS = 2400          # 40 minutes (documents APBookkeeper)
    BANK_TRANSACTIONS = 2400     # 40 minutes (transactions bancaires)
    APPROVAL_PENDINGLIST = 2400  # 40 minutes (liste approbations)

    # Drive Cache: Documents Google Drive
    DRIVE_DOCUMENTS = 1800       # 30 minutes (documents to_do/in_process/processed)


# ═══════════════════════════════════════════════════════════════
# HELPERS POUR CONSTRUIRE LES CLÉS
# ═══════════════════════════════════════════════════════════════

def build_session_key(user_id: str, company_id: str) -> str:
    """Construit la clé pour l'état de session."""
    return f"{RedisNamespace.SESSION}:{user_id}:{company_id}:state"


def build_chat_history_key(user_id: str, company_id: str, thread_key: str) -> str:
    """Construit la clé pour l'historique de chat."""
    return f"{RedisNamespace.CHAT}:{user_id}:{company_id}:{thread_key}:history"


def build_ws_channel(user_id: str, company_id: str, thread_key: str) -> str:
    """Construit le nom du canal WebSocket."""
    return f"{RedisNamespace.CHAT}:{user_id}:{company_id}:{thread_key}"


def build_context_key(user_id: str, company_id: str) -> str:
    """Construit la clé pour le contexte utilisateur."""
    return f"{RedisNamespace.CONTEXT}:{user_id}:{company_id}"


def build_cache_key(user_id: str, company_id: str, data_type: str, sub_type: str = None) -> str:
    """Construit la clé pour le cache de données métier."""
    if sub_type:
        return f"{RedisNamespace.CACHE}:{user_id}:{company_id}:{data_type}:{sub_type}"
    return f"{RedisNamespace.CACHE}:{user_id}:{company_id}:{data_type}"


def build_jobs_key(user_id: str, company_id: str, department: str) -> str:
    """Construit la clé pour les données de jobs."""
    return f"{RedisNamespace.JOBS}:{user_id}:{company_id}:{department}"


def build_ws_buffer_key(user_id: str, thread_key: str) -> str:
    """Construit la clé pour le buffer WebSocket."""
    return f"{RedisNamespace.WS_BUFFER}:{user_id}:{thread_key}"


def build_lock_key(lock_type: str, resource_id: str) -> str:
    """Construit la clé pour un lock distribué."""
    return f"{RedisNamespace.LOCK}:{lock_type}:{resource_id}"


# ═══════════════════════════════════════════════════════════════
# DOCUMENTATION DES MIGRATIONS FUTURES
# ═══════════════════════════════════════════════════════════════

MIGRATION_NOTES = """
MIGRATION FUTURE SUGGÉRÉE: Unification des namespaces
═══════════════════════════════════════════════════════════════

Objectif: Regrouper cache:* et jobs:* sous data:* pour simplifier.

Plan de migration:
1. Créer nouveau namespace data:*
2. Écrire en dual (ancien + nouveau) pendant 24h
3. Basculer les lectures vers data:*
4. Arrêter les écritures vers ancien namespace
5. Supprimer les clés orphelines

Nouveau format proposé:
    - data:{user_id}:{company_id}:context      (ex: context:*)
    - data:{user_id}:{company_id}:cache:{type} (ex: cache:*)
    - data:{user_id}:{company_id}:jobs:{dept}  (ex: jobs:*)

Avantages:
    - Pattern unique pour invalidation: data:{user_id}:{company_id}:*
    - Monitoring simplifié
    - Cohérence avec session:* et chat:*

⚠️ Attention: Nécessite coordination avec pinnokio_app (frontend)
"""

