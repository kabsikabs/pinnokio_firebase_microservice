"""
Redis Namespaces - Architecture 3 Niveaux pour les clés Redis.

Ce module centralise les préfixes de clés Redis selon une architecture
à 3 niveaux pour assurer la cohérence entre frontend et backend.

═══════════════════════════════════════════════════════════════════════
ARCHITECTURE 3 NIVEAUX
═══════════════════════════════════════════════════════════════════════

NIVEAU 1 - USER (Global)
────────────────────────
Données utilisateur globales, publiées à TOUS les onglets.
Persistance: localStorage (frontend)

    user:{uid}:profile              → { email, displayName, photoURL }
    user:{uid}:preferences          → { locale, theme }
    user:{uid}:companies            → [ { id, name } ]
    user:{uid}:selected_company_id  → "company-uuid"
    static_data:v1                  → { countries, languages, currencies, erps, dms }

    PubSub: notification:{uid}, messenger:{uid}


NIVEAU 2 - COMPANY (Context Société)
────────────────────────────────────
Données de contexte société, publiées SI company_id == selectedCompany.
Persistance: sessionStorage (frontend)

    company:{uid}:{cid}:context     → { companyId, clientUuid, mandatePath, baseCurrency }
    company:{uid}:{cid}:settings     → { workflowParams, contexts, telegram, erp }

    PubSub: company:{uid}:{cid}:updates


NIVEAU 3 - BUSINESS (Logique Métier)
────────────────────────────────────
UNE CLÉ PAR DOMAINE - Données métier, publiées SI company match ET page ouverte.
Les METRICS sont CALCULÉES depuis ces données, pas stockées séparément.

    business:{uid}:{cid}:bank       → { accounts, transactions, batches }
    business:{uid}:{cid}:routing    → { documents, oauth }
    business:{uid}:{cid}:invoices   → { items }
    business:{uid}:{cid}:expenses   → { items }
    business:{uid}:{cid}:coa        → { accounts, functions }
    business:{uid}:{cid}:dashboard  → { tasks, approvals, activity }
    business:{uid}:{cid}:chat       → { sessions, messages }
    business:{uid}:{cid}:hr         → { employees, contracts }

    PubSub: business:{uid}:{cid}:{domain}:updates


AUTRES CLÉS (Système)
─────────────────────
    session:{uid}:{cid}:state       → État LLM session (2h TTL)
    chat:{uid}:{cid}:{thread}:history → Historique chat (24h TTL)
    lock:{type}:{resource_id}       → Locks distribués (5min TTL)
    pending_ws_messages:{uid}:{thread} → Buffer WS (5min TTL)

═══════════════════════════════════════════════════════════════════════

@see docs/architecture/STORE_BACKEND_ARCHITECTURE.md
"""

from typing import Dict, Optional, List, Any
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# NIVEAUX D'ARCHITECTURE
# ═══════════════════════════════════════════════════════════════

class CacheLevel(Enum):
    """Niveaux de cache pour le routage des publications WSS."""
    USER = "user"           # Niveau 1: Global (tous onglets)
    COMPANY = "company"     # Niveau 2: Par société (si match)
    BUSINESS = "business"   # Niveau 3: Par page/domaine (si match + page active)


# ═══════════════════════════════════════════════════════════════
# DOMAINES MÉTIER (NIVEAU 3)
# ═══════════════════════════════════════════════════════════════

class BusinessDomain(Enum):
    """Domaines métier pour les clés business:{uid}:{cid}:{domain}."""
    BANK = "bank"           # Comptes, transactions, batches
    ROUTING = "routing"     # Documents à router, OAuth status
    INVOICES = "invoices"   # Factures fournisseurs (APBookkeeper)
    EXPENSES = "expenses"   # Notes de frais
    COA = "coa"             # Plan comptable (Chart of Accounts)
    DASHBOARD = "dashboard" # Tasks, approvals, activity
    CHAT = "chat"           # Sessions et messages chat
    HR = "hr"               # Employés, contrats


# ═══════════════════════════════════════════════════════════════
# PRÉFIXES DE NAMESPACES (NOUVELLE ARCHITECTURE)
# ═══════════════════════════════════════════════════════════════

class RedisNamespace:
    """Constantes pour les préfixes de namespaces Redis."""

    # ─── NIVEAU 1: USER ───
    USER = "user"               # user:{uid}:profile, user:{uid}:preferences
    STATIC_DATA = "static_data" # static_data:v1

    # ─── NIVEAU 2: COMPANY ───
    COMPANY = "company"         # company:{uid}:list, company:{uid}:{cid}:context

    # ─── NIVEAU 3: BUSINESS ───
    BUSINESS = "business"       # business:{uid}:{cid}:{domain}

    # ─── SYSTÈME ───
    SESSION = "session"         # État session LLM (stateless architecture)
    CHAT = "chat"               # Historique chat (multi-instance)
    LOCK = "lock"               # Locks distribués
    WS_BUFFER = "pending_ws_messages"  # Buffer messages WebSocket

    # ─── LEGACY (pour rétro-compatibilité) ───
    # Ces namespaces sont dépréciés, utiliser les nouveaux ci-dessus
    CONTEXT = "context"         # → company:{uid}:{cid}:context
    CACHE = "cache"             # → business:{uid}:{cid}:{domain}
    JOBS = "jobs"               # → business:{uid}:{cid}:dashboard


# ═══════════════════════════════════════════════════════════════
# TTLs PAR NIVEAU (en secondes)
# ═══════════════════════════════════════════════════════════════

class RedisTTL:
    """TTLs par défaut pour chaque type de donnée."""

    # ─── NIVEAU 1: USER ───
    USER_PROFILE = 86400        # 24 heures (données stables)
    USER_PREFERENCES = 86400    # 24 heures (rarement modifiées)
    USER_COMPANIES = 3600       # 1 heure (liste sociétés)
    USER_SELECTED_COMPANY = 3600 # 1 heure (société sélectionnée)
    STATIC_DATA = 86400         # 24 heures (données référentielles)
    NOTIFICATIONS = 7200        # 2 heures (cache notifications)
    MESSAGES = 7200             # 2 heures (cache messages)

    # ─── NIVEAU 2: COMPANY ───
    COMPANY_CONTEXT = 3600      # 1 heure (contexte société)
    COMPANY_SETTINGS = 3600     # 1 heure (paramètres société)

    # ─── NIVEAU 3: BUSINESS ───
    BUSINESS_DEFAULT = 2400     # 40 minutes (données métier actives)
    BUSINESS_BANK = 2400        # 40 minutes (transactions bancaires)
    BUSINESS_ROUTING = 1800     # 30 minutes (documents Drive)
    BUSINESS_INVOICES = 2400    # 40 minutes (factures)
    BUSINESS_EXPENSES = 2400    # 40 minutes (dépenses)
    BUSINESS_COA = 3600         # 1 heure (plan comptable - stable)
    BUSINESS_DASHBOARD = 1800   # 30 minutes (tasks, approvals)
    BUSINESS_CHAT = 86400       # 24 heures (sessions chat)
    BUSINESS_HR = 3600          # 1 heure (données RH)

    # ─── SYSTÈME ───
    SESSION = 7200              # 2 heures (prolongé à chaque activité)
    CHAT_HISTORY = 86400        # 24 heures (conversations actives)
    LOCK = 300                  # 5 minutes (évite locks orphelins)
    WS_BUFFER = 300             # 5 minutes

    # ─── LEGACY TTLs ───
    CONTEXT = 3600
    CACHE = 3600
    JOBS = 3600
    MANDATE_SNAPSHOT = 3600
    EXPENSES_DETAILS = 2400
    AP_DOCUMENTS = 2400
    BANK_TRANSACTIONS = 2400
    APPROVAL_PENDINGLIST = 2400
    DRIVE_DOCUMENTS = 1800
    HR_EMPLOYEES = 3600
    HR_CONTRACTS = 3600
    HR_REFERENCES = 86400
    HR_CLUSTERS = 86400


# ═══════════════════════════════════════════════════════════════
# HELPERS NIVEAU 1 - USER
# ═══════════════════════════════════════════════════════════════

def build_user_profile_key(uid: str) -> str:
    """Clé pour le profil utilisateur."""
    return f"{RedisNamespace.USER}:{uid}:profile"


def build_user_preferences_key(uid: str) -> str:
    """Clé pour les préférences utilisateur."""
    return f"{RedisNamespace.USER}:{uid}:preferences"


def build_static_data_key(version: str = "v1") -> str:
    """Clé pour les données statiques (pays, langues, devises, etc.)."""
    return f"{RedisNamespace.STATIC_DATA}:{version}"


def build_user_companies_key(uid: str) -> str:
    """Clé pour la liste des sociétés de l'utilisateur (Niveau 1 - USER)."""
    return f"{RedisNamespace.USER}:{uid}:companies"


def build_user_selected_company_key(uid: str) -> str:
    """Clé pour la société sélectionnée par l'utilisateur (Niveau 1 - USER)."""
    return f"{RedisNamespace.USER}:{uid}:selected_company_id"


# ═══════════════════════════════════════════════════════════════
# HELPERS NIVEAU 2 - COMPANY
# ═══════════════════════════════════════════════════════════════


def build_company_context_key(uid: str, company_id: str) -> str:
    """Clé pour le contexte d'une société (mandatePath, clientUuid, etc.)."""
    return f"{RedisNamespace.COMPANY}:{uid}:{company_id}:context"


def build_company_settings_key(uid: str, company_id: str) -> str:
    """Clé pour les paramètres d'une société (workflow, telegram, erp)."""
    return f"{RedisNamespace.COMPANY}:{uid}:{company_id}:settings"


def build_company_coa_key(uid: str, company_id: str) -> str:
    """
    Clé cache niveau 2 pour le Plan Comptable (COA).

    Le COA est traité comme donnée critique de niveau entreprise,
    similaire au contexte société. Cache niveau 2 pour:
    - Chargement rapide (pré-chargé pendant dashboard orchestration)
    - Cohérence avec le pattern company context
    - TTL 1 heure (comme company:context)

    Structure cachée:
    {
        "accounts": [...],
        "total_accounts": int,
        "functions": [...],
        "nature_display_names": {...},
        "cached_at": "ISO datetime",
        "source": "dashboard_orchestration" | "page_load"
    }
    """
    return f"{RedisNamespace.COMPANY}:{uid}:{company_id}:coa"


# ═══════════════════════════════════════════════════════════════
# HELPERS NIVEAU 3 - BUSINESS
# ═══════════════════════════════════════════════════════════════

def build_business_key(uid: str, company_id: str, domain: str) -> str:
    """
    Clé pour les données métier d'un domaine.

    Args:
        uid: User ID
        company_id: Company ID
        domain: Un des BusinessDomain (bank, routing, invoices, etc.)

    Returns:
        Clé Redis: business:{uid}:{company_id}:{domain}
    """
    return f"{RedisNamespace.BUSINESS}:{uid}:{company_id}:{domain}"


def build_bank_key(uid: str, company_id: str) -> str:
    """Clé pour les données bancaires (comptes, transactions, batches)."""
    return build_business_key(uid, company_id, BusinessDomain.BANK.value)


def build_routing_key(uid: str, company_id: str) -> str:
    """Clé pour les documents à router."""
    return build_business_key(uid, company_id, BusinessDomain.ROUTING.value)


def build_invoices_key(uid: str, company_id: str) -> str:
    """Clé pour les factures fournisseurs (APBookkeeper)."""
    return build_business_key(uid, company_id, BusinessDomain.INVOICES.value)


def build_expenses_key(uid: str, company_id: str) -> str:
    """Clé pour les notes de frais."""
    return build_business_key(uid, company_id, BusinessDomain.EXPENSES.value)


def build_coa_key(uid: str, company_id: str) -> str:
    """Clé pour le plan comptable (Chart of Accounts)."""
    return build_business_key(uid, company_id, BusinessDomain.COA.value)


def build_dashboard_key(uid: str, company_id: str) -> str:
    """Clé pour les données dashboard (tasks, approvals, activity)."""
    return build_business_key(uid, company_id, BusinessDomain.DASHBOARD.value)


def build_chat_business_key(uid: str, company_id: str) -> str:
    """Clé pour les sessions et messages chat."""
    return build_business_key(uid, company_id, BusinessDomain.CHAT.value)


def build_hr_key(uid: str, company_id: str) -> str:
    """Clé pour les données RH (employés, contrats)."""
    return build_business_key(uid, company_id, BusinessDomain.HR.value)


# ═══════════════════════════════════════════════════════════════
# HELPERS SYSTÈME
# ═══════════════════════════════════════════════════════════════

def build_session_key(user_id: str, company_id: str) -> str:
    """Construit la clé pour l'état de session LLM."""
    return f"{RedisNamespace.SESSION}:{user_id}:{company_id}:state"


def build_chat_history_key(user_id: str, company_id: str, thread_key: str) -> str:
    """Construit la clé pour l'historique de chat."""
    return f"{RedisNamespace.CHAT}:{user_id}:{company_id}:{thread_key}:history"


def build_ws_channel(user_id: str, company_id: str, thread_key: str) -> str:
    """Construit le nom du canal WebSocket."""
    return f"{RedisNamespace.CHAT}:{user_id}:{company_id}:{thread_key}"


def build_ws_buffer_key(user_id: str, thread_key: str) -> str:
    """Construit la clé pour le buffer WebSocket."""
    return f"{RedisNamespace.WS_BUFFER}:{user_id}:{thread_key}"


def build_lock_key(lock_type: str, resource_id: str) -> str:
    """Construit la clé pour un lock distribué."""
    return f"{RedisNamespace.LOCK}:{lock_type}:{resource_id}"


# ═══════════════════════════════════════════════════════════════
# HELPERS LEGACY (RÉTRO-COMPATIBILITÉ)
# ═══════════════════════════════════════════════════════════════

def build_context_key(user_id: str, company_id: str) -> str:
    """
    [DEPRECATED] Utiliser build_company_context_key() à la place.
    Maintenu pour rétro-compatibilité.
    """
    return f"{RedisNamespace.CONTEXT}:{user_id}:{company_id}"


def build_cache_key(user_id: str, company_id: str, data_type: str, sub_type: str = None) -> str:
    """
    [DEPRECATED] Utiliser build_business_key() à la place.
    Maintenu pour rétro-compatibilité.
    """
    if sub_type:
        return f"{RedisNamespace.CACHE}:{user_id}:{company_id}:{data_type}:{sub_type}"
    return f"{RedisNamespace.CACHE}:{user_id}:{company_id}:{data_type}"


def build_jobs_key(user_id: str, company_id: str, department: str) -> str:
    """
    [DEPRECATED] Utiliser build_dashboard_key() à la place.
    Maintenu pour rétro-compatibilité.
    """
    return f"{RedisNamespace.JOBS}:{user_id}:{company_id}:{department}"


# ═══════════════════════════════════════════════════════════════
# PUBSUB CHANNELS
# ═══════════════════════════════════════════════════════════════

def build_notification_channel(uid: str) -> str:
    """Canal PubSub pour les notifications (Niveau 1 - USER)."""
    return f"notification:{uid}"


def build_messenger_channel(uid: str) -> str:
    """Canal PubSub pour les messages directs (Niveau 1 - USER)."""
    return f"messenger:{uid}"


def build_company_updates_channel(uid: str, company_id: str) -> str:
    """Canal PubSub pour les mises à jour société (Niveau 2 - COMPANY)."""
    return f"company:{uid}:{company_id}:updates"


def build_business_updates_channel(uid: str, company_id: str, domain: str) -> str:
    """Canal PubSub pour les mises à jour métier (Niveau 3 - BUSINESS)."""
    return f"business:{uid}:{company_id}:{domain}:updates"


# ═══════════════════════════════════════════════════════════════
# TTL HELPERS
# ═══════════════════════════════════════════════════════════════

def get_ttl_for_level(level: CacheLevel) -> int:
    """Retourne le TTL par défaut pour un niveau de cache."""
    ttl_map = {
        CacheLevel.USER: RedisTTL.USER_PROFILE,
        CacheLevel.COMPANY: RedisTTL.COMPANY_CONTEXT,
        CacheLevel.BUSINESS: RedisTTL.BUSINESS_DEFAULT,
    }
    return ttl_map.get(level, RedisTTL.CACHE)


def get_ttl_for_domain(domain: str) -> int:
    """Retourne le TTL pour un domaine métier spécifique."""
    ttl_map = {
        BusinessDomain.BANK.value: RedisTTL.BUSINESS_BANK,
        BusinessDomain.ROUTING.value: RedisTTL.BUSINESS_ROUTING,
        BusinessDomain.INVOICES.value: RedisTTL.BUSINESS_INVOICES,
        BusinessDomain.EXPENSES.value: RedisTTL.BUSINESS_EXPENSES,
        BusinessDomain.COA.value: RedisTTL.BUSINESS_COA,
        BusinessDomain.DASHBOARD.value: RedisTTL.BUSINESS_DASHBOARD,
        BusinessDomain.CHAT.value: RedisTTL.BUSINESS_CHAT,
        BusinessDomain.HR.value: RedisTTL.BUSINESS_HR,
    }
    return ttl_map.get(domain, RedisTTL.BUSINESS_DEFAULT)


# ═══════════════════════════════════════════════════════════════
# MAPPING LEGACY → NEW
# ═══════════════════════════════════════════════════════════════

LEGACY_TO_NEW_MAPPING = {
    # cache:{uid}:{cid}:mandate:snapshot → company:{uid}:{cid}:context
    "mandate:snapshot": ("company", "context"),

    # cache:{uid}:{cid}:bank:* → business:{uid}:{cid}:bank
    "bank:transactions": ("business", "bank"),
    "bank:accounts": ("business", "bank"),
    "bank:batches": ("business", "bank"),

    # cache:{uid}:{cid}:apbookeeper:* → business:{uid}:{cid}:invoices
    "apbookeeper:documents": ("business", "invoices"),
    "apbookeeper:pendinglist": ("business", "invoices"),

    # cache:{uid}:{cid}:expenses:* → business:{uid}:{cid}:expenses
    "expenses:details": ("business", "expenses"),
    "expenses:open": ("business", "expenses"),
    "expenses:closed": ("business", "expenses"),

    # cache:{uid}:{cid}:drive:* → business:{uid}:{cid}:routing
    "drive:documents": ("business", "routing"),

    # cache:{uid}:{cid}:coa:* → business:{uid}:{cid}:coa
    "coa:accounts": ("business", "coa"),
    "coa:functions": ("business", "coa"),

    # cache:{uid}:{cid}:hr:* → business:{uid}:{cid}:hr
    "hr:employees": ("business", "hr"),
    "hr:contracts": ("business", "hr"),
    "hr:references": ("business", "hr"),

    # jobs:{uid}:{cid}:* → business:{uid}:{cid}:dashboard
    "jobs:router": ("business", "dashboard"),
    "jobs:apbookeeper": ("business", "dashboard"),
    "jobs:banker": ("business", "dashboard"),
}


def migrate_legacy_key(legacy_key: str) -> Optional[str]:
    """
    Convertit une clé legacy vers le nouveau format.

    Args:
        legacy_key: Clé au format legacy (cache:uid:cid:type:subtype ou jobs:uid:cid:dept)

    Returns:
        Nouvelle clé au format 3 niveaux, ou None si pas de mapping
    """
    parts = legacy_key.split(":")
    if len(parts) < 4:
        return None

    namespace = parts[0]
    uid = parts[1]
    cid = parts[2]

    if namespace == "cache" and len(parts) >= 5:
        data_type = f"{parts[3]}:{parts[4]}"
        mapping = LEGACY_TO_NEW_MAPPING.get(data_type)
        if mapping:
            new_namespace, new_suffix = mapping
            if new_namespace == "company":
                return build_company_context_key(uid, cid)
            elif new_namespace == "business":
                return build_business_key(uid, cid, new_suffix)

    elif namespace == "jobs" and len(parts) >= 4:
        dept = parts[3]
        mapping = LEGACY_TO_NEW_MAPPING.get(f"jobs:{dept}")
        if mapping:
            return build_business_key(uid, cid, BusinessDomain.DASHBOARD.value)

    return None


# ═══════════════════════════════════════════════════════════════
# DOCUMENTATION
# ═══════════════════════════════════════════════════════════════

ARCHITECTURE_DOC = """
═══════════════════════════════════════════════════════════════════════
ARCHITECTURE 3 NIVEAUX - REDIS CACHE & WSS PUBLISHING
═══════════════════════════════════════════════════════════════════════

PRINCIPE CLÉ: Les METRICS sont CALCULÉES depuis les données métier,
pas stockées séparément. Quand une transaction change de statut,
les metrics sont automatiquement cohérentes.

Exemple de calcul metrics:
    def get_metrics(uid, cid):
        bank = redis.get(f"business:{uid}:{cid}:bank")
        routing = redis.get(f"business:{uid}:{cid}:routing")
        invoices = redis.get(f"business:{uid}:{cid}:invoices")
        expenses = redis.get(f"business:{uid}:{cid}:expenses")

        return {
            "bank": {
                "toProcess": count(bank.transactions, status="to_process"),
                "inProcess": count(bank.transactions, status="in_process"),
                "pending": count(bank.transactions, status="pending"),
            },
            "routing": { ... },
            "ap": { ... },
            "expenses": { ... }
        }

RÈGLES DE PUBLICATION WSS:
─────────────────────────────────────────────────────────────────
| Niveau   | Condition de publication                           |
─────────────────────────────────────────────────────────────────
| USER     | Toujours (si connecté)                             |
| COMPANY  | Si company_id == selectedCompany                   |
| BUSINESS | Si company_id match ET page du domaine est active  |
─────────────────────────────────────────────────────────────────

RÈGLES DE CACHE:
- Le cache est TOUJOURS mis à jour, même si non publié
- Quand l'utilisateur ouvre une page, il lit depuis le cache
- Les données sont fraîches car mises à jour par les events précédents

═══════════════════════════════════════════════════════════════════════
"""
