"""
Contextual Publisher - Publication contextuelle par niveau (Architecture 3 Niveaux)
===================================================================================

Système de publication avec 3 niveaux de granularité aligné avec redis_namespaces.py:

1. USER (global) - Pour le compte utilisateur (notifications, messages)
2. COMPANY - Pour la société sous le compte (métriques, jobs)
3. BUSINESS - Pour le domaine métier en cours (widgets spécifiques par page)

RÈGLES:
- USER: Toujours publié si utilisateur connecté (pas de vérification de page/company)
- COMPANY: Publié seulement si la société correspond à celle sélectionnée
- BUSINESS: Publié seulement si l'utilisateur est sur la page du domaine concerné

CACHE:
- Chaque niveau a son propre cache Redis selon redis_namespaces.py
- Le cache est TOUJOURS mis à jour, même si l'événement n'est pas publié
- Format:
  - USER: user:{uid}:{subkey}
  - COMPANY: company:{uid}:{cid}:{subkey}
  - BUSINESS: business:{uid}:{cid}:{domain}

PRINCIPE CLÉ:
- Les METRICS sont CALCULÉES depuis les données business, pas stockées séparément
- Utiliser MetricsCalculator pour calculer les metrics à la demande

@see app/llm_service/redis_namespaces.py - Architecture et helpers
@see app/cache/metrics_calculator.py - Calcul des metrics depuis business data
"""

import json
import logging
from typing import Any, Dict, Optional

from app.redis_client import get_redis
from app.ws_hub import hub
from app.ws_events import WS_EVENTS

# Import architecture 3 niveaux depuis redis_namespaces
from app.llm_service.redis_namespaces import (
    CacheLevel,
    BusinessDomain,
    RedisTTL,
    # Helpers Niveau 1 - USER
    build_user_profile_key,
    build_user_preferences_key,
    # Helpers Niveau 2 - COMPANY
    build_company_context_key,
    build_company_settings_key,
    # Helpers Niveau 3 - BUSINESS
    build_business_key,
    build_bank_key,
    build_routing_key,
    build_invoices_key,
    build_expenses_key,
    build_coa_key,
    build_dashboard_key,
    build_chat_business_key,
    build_hr_key,
    # TTL helpers
    get_ttl_for_level,
    get_ttl_for_domain,
)

# NOTE: get_user_session_manager est importé de manière lazy dans _get_user_context()
# pour éviter les imports circulaires avec dashboard_orchestration_handlers

logger = logging.getLogger(__name__)


# ============================================
# Mapping Page → Domaine Métier
# ============================================

PAGE_TO_DOMAIN_MAP: Dict[str, str] = {
    "dashboard": BusinessDomain.DASHBOARD.value,
    "banking": BusinessDomain.BANK.value,
    "bank": BusinessDomain.BANK.value,
    "routing": BusinessDomain.ROUTING.value,
    "router": BusinessDomain.ROUTING.value,
    "invoices": BusinessDomain.INVOICES.value,
    "apbookeeper": BusinessDomain.INVOICES.value,
    "expenses": BusinessDomain.EXPENSES.value,
    "coa": BusinessDomain.COA.value,
    "chart-of-accounts": BusinessDomain.COA.value,
    "chat": BusinessDomain.CHAT.value,
    "hr": BusinessDomain.HR.value,
}


def page_to_domain(page: str) -> Optional[str]:
    """Convertit un nom de page en domaine métier."""
    return PAGE_TO_DOMAIN_MAP.get(page.lower())


def domain_to_pages(domain: str) -> list[str]:
    """Retourne les pages associées à un domaine métier."""
    return [page for page, dom in PAGE_TO_DOMAIN_MAP.items() if dom == domain]


# ============================================
# Cache Keys (Architecture 3 Niveaux)
# ============================================

def _get_cache_key(
    level: CacheLevel,
    uid: str,
    company_id: Optional[str] = None,
    domain: Optional[str] = None,
    subkey: Optional[str] = None
) -> str:
    """
    Génère la clé de cache Redis selon le niveau.

    Format (aligné avec redis_namespaces.py):
    - USER: user:{uid}:{subkey} (ex: user:abc:notifications)
    - COMPANY: company:{uid}:{company_id}:{subkey} (ex: company:abc:xyz:context)
    - BUSINESS: business:{uid}:{company_id}:{domain} (ex: business:abc:xyz:bank)
    """
    if level == CacheLevel.USER:
        if subkey:
            return f"user:{uid}:{subkey}"
        return build_user_profile_key(uid)

    elif level == CacheLevel.COMPANY:
        if not company_id:
            raise ValueError("company_id required for COMPANY level")
        if subkey == "settings":
            return build_company_settings_key(uid, company_id)
        return build_company_context_key(uid, company_id)

    elif level == CacheLevel.BUSINESS:
        if not company_id or not domain:
            raise ValueError("company_id and domain required for BUSINESS level")
        return build_business_key(uid, company_id, domain)

    else:
        raise ValueError(f"Unknown level: {level}")


def _get_ttl_for_cache(level: CacheLevel, domain: Optional[str] = None) -> int:
    """Retourne le TTL approprié selon le niveau et le domaine."""
    if level == CacheLevel.BUSINESS and domain:
        return get_ttl_for_domain(domain)
    return get_ttl_for_level(level)


# ============================================
# Contexte Utilisateur
# ============================================

def _get_user_context(uid: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Récupère le contexte utilisateur actuel.

    Retourne:
        {
            "current_page": str | None,  # Page actuelle (dashboard, chat, routing, etc.)
            "current_domain": str | None, # Domaine métier correspondant (bank, routing, etc.)
            "company_id": str | None,     # Société sélectionnée
            "is_connected": bool          # Si l'utilisateur est connecté
        }
    """
    context = {
        "current_page": None,
        "current_domain": None,
        "company_id": None,
        "is_connected": hub.is_user_connected(uid)
    }

    # Récupérer la page actuelle depuis Redis (mis à jour par le frontend)
    try:
        redis = get_redis()
        page_key = f"session:context:{uid}:page"
        page_data = redis.get(page_key)
        if page_data:
            page = json.loads(page_data) if isinstance(page_data, str) else page_data.decode()
            context["current_page"] = page
            # Convertir la page en domaine métier
            context["current_domain"] = page_to_domain(page)
    except Exception as e:
        logger.debug(f"[CONTEXT] Failed to get current_page for uid={uid}: {e}")

    # Récupérer la société sélectionnée depuis Niveau 1 + Niveau 2
    try:
        from app.llm_service.redis_namespaces import (
            build_user_selected_company_key,
            build_company_context_key
        )
        redis = get_redis()
        
        # Niveau 1: Récupérer le company_id sélectionné
        selected_key = build_user_selected_company_key(uid)
        cached_company_id = redis.get(selected_key)
        if cached_company_id:
            company_id = cached_company_id.decode() if isinstance(cached_company_id, bytes) else cached_company_id
            if company_id:
                context["company_id"] = company_id
    except Exception as e:
        logger.debug(f"[CONTEXT] Failed to get company_id for uid={uid}: {e}")

    return context


# ============================================
# Mise à jour du Cache
# ============================================

def _update_cache(
    level: CacheLevel,
    cache_key: str,
    data: Dict[str, Any],
    ttl: int
) -> None:
    """
    Met à jour le cache Redis pour un niveau donné.

    RÈGLE: Le cache est TOUJOURS mis à jour, même si l'événement n'est pas publié.

    Modes de mise à jour:
    - action="new" ou "add": Ajouter en tête de liste
    - action="remove" ou "delete": Supprimer de la liste
    - action="update": Mettre à jour un item existant
    - action="full" ou autre: Remplacement complet
    """
    try:
        redis = get_redis()

        # Récupérer le cache existant
        existing_data = redis.get(cache_key)
        if existing_data:
            try:
                cache_data = json.loads(existing_data) if isinstance(existing_data, str) else json.loads(existing_data.decode())
            except json.JSONDecodeError:
                cache_data = {}
        else:
            cache_data = {}

        # Mettre à jour selon le type d'action
        action = data.get("action", "full")

        if action == "new" or action == "add":
            # Ajouter à la liste
            items = cache_data.get("items", [])
            items.insert(0, data.get("item", data))  # Ajouter en tête
            # Limiter à 100 items max
            cache_data["items"] = items[:100]

        elif action == "remove" or action == "delete":
            # Supprimer de la liste
            items = cache_data.get("items", [])
            item_id = data.get("id") or data.get("docId")
            cache_data["items"] = [item for item in items if item.get("id") != item_id and item.get("docId") != item_id]

        elif action == "update":
            # Mettre à jour un item existant
            items = cache_data.get("items", [])
            item_id = data.get("id") or data.get("docId")
            changes = data.get("changes", {})
            for i, item in enumerate(items):
                if item.get("id") == item_id or item.get("docId") == item_id:
                    items[i] = {**item, **changes}
                    break
            cache_data["items"] = items

        else:
            # Mise à jour complète (full state)
            cache_data = data

        # Sauvegarder avec TTL
        redis.setex(cache_key, ttl, json.dumps(cache_data))

        logger.debug(f"[CACHE] Updated {level.value} cache: {cache_key}")

    except Exception as e:
        logger.error(f"[CACHE] Failed to update cache {cache_key}: {e}")


# ============================================
# Vérification de Contexte
# ============================================

def _should_publish(
    level: CacheLevel,
    uid: str,
    context: Dict[str, Any],
    target_company_id: Optional[str] = None,
    target_domain: Optional[str] = None
) -> bool:
    """
    Détermine si un événement doit être publié selon le niveau et le contexte.

    RÈGLES:
    - USER: Toujours si connecté
    - COMPANY: Si company_id correspond
    - BUSINESS: Si company_id correspond ET domaine correspond à la page active
    """
    # Vérifier connexion
    if not context.get("is_connected"):
        return False

    if level == CacheLevel.USER:
        # USER: Toujours publié si connecté
        return True

    elif level == CacheLevel.COMPANY:
        # COMPANY: Vérifier que la société correspond
        if not target_company_id:
            logger.warning("[PUBLISH] COMPANY level requires target_company_id")
            return False

        current_company_id = context.get("company_id")
        if current_company_id != target_company_id:
            logger.debug(
                f"[PUBLISH] COMPANY level: company mismatch "
                f"(current={current_company_id}, target={target_company_id})"
            )
            return False

        return True

    elif level == CacheLevel.BUSINESS:
        # BUSINESS: Vérifier société ET domaine
        if not target_company_id or not target_domain:
            logger.warning("[PUBLISH] BUSINESS level requires target_company_id and target_domain")
            return False

        current_company_id = context.get("company_id")
        current_domain = context.get("current_domain")

        if current_company_id != target_company_id:
            logger.debug(
                f"[PUBLISH] BUSINESS level: company mismatch "
                f"(current={current_company_id}, target={target_company_id})"
            )
            return False

        if current_domain != target_domain:
            logger.debug(
                f"[PUBLISH] BUSINESS level: domain mismatch "
                f"(current={current_domain}, target={target_domain})"
            )
            return False

        return True

    return False


# ============================================
# Publication Contextuelle (API Principale)
# ============================================

async def publish_contextual_event(
    level: CacheLevel,
    uid: str,
    event_type: str,
    payload: Dict[str, Any],
    target_company_id: Optional[str] = None,
    target_domain: Optional[str] = None,
    session_id: Optional[str] = None,
    cache_subkey: Optional[str] = None,
    cache_ttl: Optional[int] = None,
    skip_connection_check: bool = False,
    skip_cache_update: bool = False
) -> bool:
    """
    Publie un événement selon le niveau de granularité.

    Args:
        level: Niveau de publication (USER, COMPANY, BUSINESS)
        uid: Firebase user ID
        event_type: Type d'événement WebSocket (ex: "dashboard.metrics_update")
        payload: Données de l'événement
        target_company_id: ID de la société concernée (requis pour COMPANY/BUSINESS)
        target_domain: Domaine métier (requis pour BUSINESS, ex: "bank", "routing")
        session_id: Session ID pour récupérer le contexte
        cache_subkey: Sous-clé optionnelle pour le cache (ex: "notifications")
        cache_ttl: TTL du cache en secondes (auto-déterminé si non fourni)
        skip_connection_check: Si True, publie même si non connecté
        skip_cache_update: Si True, ne met pas à jour le cache

    Returns:
        True si publié, False sinon

    Example:
        # Notification (USER level - global)
        await publish_contextual_event(
            level=CacheLevel.USER,
            uid="user123",
            event_type=WS_EVENTS.NOTIFICATION.DELTA,
            payload={"action": "new", "data": {...}},
            cache_subkey="notifications"
        )

        # Métrique dashboard (BUSINESS level - seulement si sur dashboard)
        await publish_contextual_event(
            level=CacheLevel.BUSINESS,
            uid="user123",
            event_type=WS_EVENTS.DASHBOARD.METRICS_UPDATE,
            payload={"metrics": {...}},
            target_company_id="company_xyz",
            target_domain="dashboard"
        )

        # Transaction bancaire (BUSINESS level)
        await publish_contextual_event(
            level=CacheLevel.BUSINESS,
            uid="user123",
            event_type="bank.transaction_update",
            payload={"action": "update", "id": "tx123", "changes": {...}},
            target_company_id="company_xyz",
            target_domain="bank"
        )
    """
    try:
        # 1. Récupérer le contexte utilisateur
        context = _get_user_context(uid, session_id)

        # 2. Mettre à jour le cache (TOUJOURS, même si pas publié)
        if not skip_cache_update:
            try:
                cache_key = _get_cache_key(level, uid, target_company_id, target_domain, cache_subkey)
                ttl = cache_ttl or _get_ttl_for_cache(level, target_domain)
                _update_cache(level, cache_key, payload, ttl)
            except ValueError as e:
                logger.warning(f"[PUBLISH] Cache update skipped: {e}")

        # 3. Vérifier si on doit publier
        if skip_connection_check:
            should_publish = True
        else:
            should_publish = _should_publish(level, uid, context, target_company_id, target_domain)

        if not should_publish:
            logger.debug(
                f"[PUBLISH] Event not published: level={level.value} uid={uid} "
                f"company={target_company_id} domain={target_domain}"
            )
            return False

        # 4. Publier via WebSocket
        hub.broadcast_threadsafe(uid, {
            "type": event_type,
            "payload": payload
        })

        logger.info(
            f"[PUBLISH] Event published: level={level.value} uid={uid} "
            f"type={event_type} company={target_company_id} domain={target_domain}"
        )

        return True

    except Exception as e:
        logger.error(f"[PUBLISH] Failed to publish event: {e}", exc_info=True)
        return False


# ============================================
# Helpers par Niveau
# ============================================

async def publish_user_event(
    uid: str,
    event_type: str,
    payload: Dict[str, Any],
    cache_subkey: Optional[str] = None,
    cache_ttl: int = RedisTTL.USER_PROFILE
) -> bool:
    """
    Helper pour publier un événement USER (global).

    Exemples:
        # Notification
        await publish_user_event(uid, WS_EVENTS.NOTIFICATION.DELTA, {...}, "notifications")

        # Message
        await publish_user_event(uid, WS_EVENTS.MESSENGER.NEW_MESSAGE, {...}, "messages")
    """
    return await publish_contextual_event(
        level=CacheLevel.USER,
        uid=uid,
        event_type=event_type,
        payload=payload,
        cache_subkey=cache_subkey,
        cache_ttl=cache_ttl
    )


async def publish_company_event(
    uid: str,
    company_id: str,
    event_type: str,
    payload: Dict[str, Any],
    session_id: Optional[str] = None,
    cache_subkey: Optional[str] = None,
    cache_ttl: int = RedisTTL.COMPANY_CONTEXT
) -> bool:
    """
    Helper pour publier un événement COMPANY (par société).

    Exemples:
        # Settings mis à jour
        await publish_company_event(uid, cid, "company.settings_update", {...}, subkey="settings")

        # Context mis à jour
        await publish_company_event(uid, cid, "company.context_update", {...})
    """
    return await publish_contextual_event(
        level=CacheLevel.COMPANY,
        uid=uid,
        event_type=event_type,
        payload=payload,
        target_company_id=company_id,
        session_id=session_id,
        cache_subkey=cache_subkey,
        cache_ttl=cache_ttl
    )


async def publish_business_event(
    uid: str,
    company_id: str,
    domain: str,
    event_type: str,
    payload: Dict[str, Any],
    session_id: Optional[str] = None,
    cache_ttl: Optional[int] = None
) -> bool:
    """
    Helper pour publier un événement BUSINESS (par domaine métier).

    Args:
        domain: Domaine métier (bank, routing, invoices, expenses, coa, dashboard, chat, hr)

    Exemples:
        # Transaction bancaire
        await publish_business_event(uid, cid, "bank", "bank.transaction_update", {...})

        # Document routing
        await publish_business_event(uid, cid, "routing", "routing.document_new", {...})

        # Facture
        await publish_business_event(uid, cid, "invoices", "invoices.status_change", {...})
    """
    return await publish_contextual_event(
        level=CacheLevel.BUSINESS,
        uid=uid,
        event_type=event_type,
        payload=payload,
        target_company_id=company_id,
        target_domain=domain,
        session_id=session_id,
        cache_ttl=cache_ttl or get_ttl_for_domain(domain)
    )


# ============================================
# Helpers par Domaine Métier (Niveau 3)
# ============================================

async def publish_bank_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine bancaire."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.BANK.value,
        event_type, payload, session_id
    )


async def publish_routing_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine routing."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.ROUTING.value,
        event_type, payload, session_id
    )


async def publish_invoices_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine factures (APBookkeeper)."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.INVOICES.value,
        event_type, payload, session_id
    )


async def publish_expenses_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine dépenses."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.EXPENSES.value,
        event_type, payload, session_id
    )


async def publish_coa_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine plan comptable."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.COA.value,
        event_type, payload, session_id
    )


async def publish_dashboard_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine dashboard (tasks, approvals, activity)."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.DASHBOARD.value,
        event_type, payload, session_id
    )


async def publish_chat_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine chat."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.CHAT.value,
        event_type, payload, session_id
    )


async def publish_hr_event(
    uid: str, company_id: str, event_type: str, payload: Dict[str, Any],
    session_id: Optional[str] = None
) -> bool:
    """Publie un événement du domaine RH."""
    return await publish_business_event(
        uid, company_id, BusinessDomain.HR.value,
        event_type, payload, session_id
    )


# ============================================
# Mise à jour du Contexte de Page
# ============================================

def update_page_context(uid: str, page: str) -> None:
    """
    Met à jour le contexte de page de l'utilisateur.

    Appelé par le frontend lors d'un changement de page.
    Stocke aussi le domaine métier correspondant.
    """
    try:
        redis = get_redis()
        page_key = f"session:context:{uid}:page"
        redis.setex(page_key, RedisTTL.SESSION, json.dumps(page))

        # Stocker aussi le domaine pour faciliter les lookups
        domain = page_to_domain(page)
        if domain:
            domain_key = f"session:context:{uid}:domain"
            redis.setex(domain_key, RedisTTL.SESSION, json.dumps(domain))

        logger.debug(f"[CONTEXT] Updated page context: uid={uid} page={page} domain={domain}")
    except Exception as e:
        logger.error(f"[CONTEXT] Failed to update page context: {e}")


# ============================================
# Intégration Metrics Calculator
# ============================================

async def publish_metrics_update(
    uid: str,
    company_id: str,
    session_id: Optional[str] = None,
    domains: Optional[list[str]] = None
) -> bool:
    """
    Calcule et publie les métriques depuis les données business.

    Cette fonction:
    1. Utilise MetricsCalculator pour calculer les metrics depuis le cache business
    2. Publie vers le dashboard si l'utilisateur est sur cette page

    Args:
        uid: User ID
        company_id: Company ID
        session_id: Session ID optionnel
        domains: Domaines à inclure (tous si None)

    Returns:
        True si publié avec succès
    """
    try:
        from app.cache.metrics_calculator import AsyncMetricsCalculator

        calculator = AsyncMetricsCalculator()

        # Calculer les métriques selon les domaines demandés
        if domains:
            metrics = {}
            for domain in domains:
                if domain == BusinessDomain.ROUTING.value:
                    metrics["routing"] = await calculator.get_routing_metrics(uid, company_id)
                elif domain == BusinessDomain.INVOICES.value:
                    metrics["ap"] = await calculator.get_ap_metrics(uid, company_id)
                elif domain == BusinessDomain.BANK.value:
                    metrics["bank"] = await calculator.get_bank_metrics(uid, company_id)
                elif domain == BusinessDomain.EXPENSES.value:
                    metrics["expenses"] = await calculator.get_expenses_metrics(uid, company_id)
        else:
            metrics = await calculator.get_all_metrics(uid, company_id)

        # Publier vers le dashboard
        return await publish_dashboard_event(
            uid=uid,
            company_id=company_id,
            event_type=WS_EVENTS.DASHBOARD.METRICS_UPDATE if hasattr(WS_EVENTS, 'DASHBOARD') else "dashboard.metrics_update",
            payload={"metrics": metrics, "action": "full"},
            session_id=session_id
        )

    except ImportError:
        logger.warning("[METRICS] MetricsCalculator not available")
        return False
    except Exception as e:
        logger.error(f"[METRICS] Failed to publish metrics update: {e}", exc_info=True)
        return False


# ============================================
# Legacy Compatibility (Deprecated)
# ============================================

# Alias pour rétro-compatibilité - À SUPPRIMER après migration complète
PublicationLevel = CacheLevel  # Deprecated: use CacheLevel

async def publish_page_event(
    uid: str,
    company_id: str,
    page: str,
    event_type: str,
    payload: Dict[str, Any],
    session_id: Optional[str] = None,
    cache_ttl: int = 3600
) -> bool:
    """
    [DEPRECATED] Utiliser publish_business_event() avec le domaine métier.

    Maintenu pour rétro-compatibilité. Convertit automatiquement page → domain.
    """
    domain = page_to_domain(page)
    if not domain:
        logger.warning(f"[LEGACY] Unknown page '{page}', using as domain")
        domain = page

    return await publish_business_event(
        uid=uid,
        company_id=company_id,
        domain=domain,
        event_type=event_type,
        payload=payload,
        session_id=session_id,
        cache_ttl=cache_ttl
    )
