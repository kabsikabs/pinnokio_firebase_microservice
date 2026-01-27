"""
Dashboard Orchestration Handlers - Wrapper Layer
=================================================

This module provides wrapper handlers for WebSocket dashboard orchestration.
It manages the post-authentication data loading sequence, replicating the
logic from AuthState.process_post_authentication() on the backend.

CRITICAL: This is ADDITIVE code - it wraps existing services without modifying them.

Architecture:
    Frontend -> WebSocket -> dashboard_orchestration_handlers.py -> FirebaseManagement

Events Handled:
    - dashboard.orchestrate_init: Start full orchestration after auth
    - dashboard.company_change: Handle company switch with cancellation
    - dashboard.refresh: Force refresh all data

Dependencies (Existing Services - DO NOT MODIFY):
    - firebase_providers.py: FirebaseManagement singleton
    - dashboard_handlers.py: DashboardHandlers.full_data()
    - redis_client: Session/cache storage
    - ws_hub: WebSocket broadcasting
    - llm_service/session_state_manager: LLM session state management

Flow (mirrors AuthState.process_post_authentication):
    Phase 0: User Setup
        - check_and_create_client_document()
        - Check first_connect -> process_immediate_top_up(50)
        - process_share_settings()
    Phase 1: Company Selection
        - fetch_all_mandates_light()
        - Auto-select first company
        - fetch_single_mandate() for details
    Phase 2: Data Loading
        - Load all dashboard widgets in parallel
    Phase 3: LLM Session
        - Initialize LLM with mandate_path and client_uuid

Author: Lead Migration Architect
Created: 2026-01-18
Updated: 2026-01-18 - Added full process_post_authentication flow
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..firebase_providers import FirebaseManagement
from ..dashboard_handlers import get_dashboard_handlers
from ..firebase_client import get_firestore
from ..llm_service.session_state_manager import SessionStateManager
from ..llm_service.redis_namespaces import (
    build_user_selected_company_key,
    build_company_context_key,
    build_company_coa_key,
    RedisTTL
)
from ..redis_client import get_redis
from ..ws_events import WS_EVENTS
from ..ws_hub import hub

logger = logging.getLogger("dashboard.orchestration")


# ============================================
# CONSTANTS
# ============================================

ORCHESTRATION_TTL = 3600  # 1 hour
COMPANY_SELECTION_TTL = 86400  # 24 hours
USER_SESSION_TTL = 7200  # 2 hours
PHASE_TIMEOUT = 30  # seconds per phase
FIRST_CONNECT_CREDIT = 50  # $50 credit for new users


# ============================================
# HELPERS: Company Selection (Niveau 1 + Niveau 2)
# ============================================

def get_selected_company_id(uid: str) -> Optional[str]:
    """
    Récupère le company_id sélectionné depuis le cache Niveau 1 (USER).
    
    Returns:
        company_id ou None si aucune société sélectionnée
    """
    try:
        redis_client = get_redis()
        selected_key = build_user_selected_company_key(uid)
        cached = redis_client.get(selected_key)
        if cached:
            company_id = cached.decode() if isinstance(cached, bytes) else cached
            return company_id if company_id else None
    except Exception as e:
        logger.debug(f"[ORCHESTRATION] Failed to get selected company_id: {e}")
    return None


def get_company_context(uid: str, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Récupère le contexte complet d'une société depuis le cache Niveau 2 (COMPANY).
    
    Si company_id n'est pas fourni, récupère d'abord depuis Niveau 1.
    
    Returns:
        Dict avec company_data complet ou None
    """
    try:
        # Si company_id non fourni, le récupérer depuis Niveau 1
        if not company_id:
            company_id = get_selected_company_id(uid)
            if not company_id:
                return None
        
        # Récupérer le contexte depuis Niveau 2
        redis_client = get_redis()
        context_key = build_company_context_key(uid, company_id)
        cached = redis_client.get(context_key)
        if cached:
            context_data = json.loads(cached if isinstance(cached, str) else cached.decode())
            return context_data
    except Exception as e:
        logger.debug(f"[ORCHESTRATION] Failed to get company context: {e}")
    return None


def set_selected_company(uid: str, company_id: str, company_data: Dict[str, Any]) -> None:
    """
    Met à jour le cache Niveau 1 (selected_company_id) et Niveau 2 (context).
    
    Args:
        uid: User ID
        company_id: Company ID
        company_data: Données complètes de la société
    """
    try:
        redis_client = get_redis()
        
        # Niveau 1: Stocker le company_id sélectionné
        selected_key = build_user_selected_company_key(uid)
        redis_client.setex(selected_key, COMPANY_SELECTION_TTL, company_id)
        
        # Niveau 2: Stocker le contexte complet
        context_key = build_company_context_key(uid, company_id)
        context_payload = {
            **company_data,
            "company_id": company_id,
            "selected_at": datetime.now(timezone.utc).isoformat()
        }
        redis_client.setex(
            context_key,
            COMPANY_SELECTION_TTL,
            json.dumps(context_payload)
        )
        logger.info(
            f"[ORCHESTRATION] Company selection cached: "
            f"selected={company_id} context_keys={list(context_payload.keys())}"
        )
    except Exception as e:
        logger.warning(f"[ORCHESTRATION] Failed to cache company selection: {e}")


# ============================================
# ORCHESTRATION STATE MANAGER
# ============================================

class OrchestrationStateManager:
    """Manages orchestration state in Redis."""

    KEY_PREFIX = "orchestration"

    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def _build_key(self, uid: str, session_id: str) -> str:
        return f"{self.KEY_PREFIX}:{uid}:{session_id}:state"

    def create_orchestration(
        self,
        uid: str,
        session_id: str,
        company_id: Optional[str] = None
    ) -> str:
        """Create new orchestration state, returns orchestration_id."""
        orchestration_id = str(uuid.uuid4())
        key = self._build_key(uid, session_id)

        state = {
            "orchestration_id": orchestration_id,
            "phase": "user_setup",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cancellation_requested": False,
            "selected_company_id": company_id,
            "is_first_connect": False,
            "is_invited_user": False,
            "authorized_companies_ids": [],  # Will be populated during user_setup phase
            "widgets_status": {
                "balance": "pending",
                "metrics": "pending",
                "storage": "pending",
                "expenses": "pending",
                "tasks": "pending",
                "apbookeeper_jobs": "pending",
                "router_jobs": "pending",
                "banker_jobs": "pending",
                "approval_waitlist": "pending"
            },
            "errors": []
        }

        self.redis.setex(key, ORCHESTRATION_TTL, json.dumps(state))
        logger.info(f"[ORCHESTRATION] Created: uid={uid} id={orchestration_id}")

        return orchestration_id

    def get_orchestration(self, uid: str, session_id: str) -> Optional[Dict]:
        """Get current orchestration state."""
        key = self._build_key(uid, session_id)
        data = self.redis.get(key)

        if data:
            return json.loads(data if isinstance(data, str) else data.decode())
        return None

    def update_orchestration(
        self,
        uid: str,
        session_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update orchestration state."""
        key = self._build_key(uid, session_id)
        state = self.get_orchestration(uid, session_id)

        if not state:
            return False

        state.update(updates)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        self.redis.setex(key, ORCHESTRATION_TTL, json.dumps(state))
        return True

    def request_cancellation(self, uid: str, session_id: str) -> bool:
        """Request cancellation of current orchestration."""
        return self.update_orchestration(uid, session_id, {
            "cancellation_requested": True
        })

    def is_cancelled(self, uid: str, session_id: str, orchestration_id: str) -> bool:
        """Check if orchestration should be cancelled."""
        state = self.get_orchestration(uid, session_id)

        if not state:
            return True

        return (
            state.get("cancellation_requested", False) or
            state.get("orchestration_id") != orchestration_id
        )


# ============================================
# SINGLETONS
# ============================================

_state_manager: Optional[OrchestrationStateManager] = None


def get_state_manager() -> OrchestrationStateManager:
    global _state_manager
    if _state_manager is None:
        _state_manager = OrchestrationStateManager()
    return _state_manager


# ============================================
# MAIN ORCHESTRATION HANDLERS
# ============================================

async def handle_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle dashboard.orchestrate_init event.

    Triggers the full dashboard orchestration sequence:
    0. Phase 0 (user_setup): Setup user document, check first connect, share settings
    1. Phase 1 (company): Load companies, select first
    2. Phase 2 (data): Load all dashboard widgets in parallel
    3. Phase 3 (llm): Initialize LLM session in background

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Optional payload with user_data from auth

    Returns:
        Response dict with orchestration_id
    """
    logger.info(f"[ORCHESTRATION] Init requested: uid={uid} session={session_id}")

    state_manager = get_state_manager()

    # Cancel any existing orchestration
    existing = state_manager.get_orchestration(uid, session_id)
    if existing:
        state_manager.request_cancellation(uid, session_id)
        await asyncio.sleep(0.1)  # Brief pause for cleanup

    # Create new orchestration
    orchestration_id = state_manager.create_orchestration(uid, session_id)

    # Extract user_data from payload (passed from auth.session_confirmed)
    user_data = payload.get("user_data", {})
    if not user_data:
        # Fallback: construct minimal user_data
        user_data = {"uid": uid}

    # Extract target_company_id if provided (preserves selected company after cache miss)
    target_company_id = payload.get("target_company_id")
    if target_company_id:
        logger.info(f"[ORCHESTRATION] Init with target_company_id={target_company_id}")

    # Start orchestration in background
    asyncio.create_task(
        _run_orchestration(
            uid, session_id, orchestration_id, user_data,
            target_company_id=target_company_id
        )
    )

    return {
        "type": "dashboard.orchestrate_init",
        "payload": {
            "success": True,
            "orchestration_id": orchestration_id,
            "message": "Orchestration started"
        }
    }


async def handle_company_change(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle dashboard.company_change event.

    Proper company switching flow:
    1. Validate company_id is authorized for user
    2. Invalidate old LLM session (company-specific context)
    3. Cancel existing orchestration
    4. Fetch fresh company data (mandate_path, client_uuid)
    5. Start data loading phase with new company

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain 'company_id'

    Returns:
        Response dict
    """
    company_id = payload.get("company_id")

    if not company_id:
        return {
            "type": "error",
            "payload": {
                "success": False,
                "error": "company_id required",
                "code": "MISSING_COMPANY_ID"
            }
        }

    logger.info(
        f"[ORCHESTRATION] Company change requested: uid={uid} "
        f"new_company={company_id}"
    )

    state_manager = get_state_manager()

    # 1. Get old company to invalidate LLM session (from Niveau 1 + Niveau 2)
    old_company_id = get_selected_company_id(uid)
    old_company_data = get_company_context(uid, old_company_id) if old_company_id else None

    # 2. Validate company_id is authorized (optional but recommended)
    orchestration_state = state_manager.get_orchestration(uid, session_id)
    if orchestration_state:
        authorized_ids = orchestration_state.get("authorized_companies_ids", [])
        # If authorized list exists and company not in it, reject
        if authorized_ids and company_id not in authorized_ids:
            logger.warning(
                f"[ORCHESTRATION] Unauthorized company change: uid={uid} "
                f"company={company_id} authorized={authorized_ids}"
            )
            return {
                "type": "error",
                "payload": {
                    "success": False,
                    "error": "Company not authorized for this user",
                    "code": "UNAUTHORIZED_COMPANY"
                }
            }

    # 3. Invalidate old LLM session if switching to different company
    if old_company_id and old_company_id != company_id:
        try:
            from ..llm_service.session_state_manager import SessionStateManager
            llm_session_manager = SessionStateManager()
            llm_session_manager.delete_session_state(uid, old_company_id)
            logger.info(
                f"[ORCHESTRATION] Invalidated LLM session for old company: "
                f"uid={uid} old_company={old_company_id}"
            )
        except Exception as e:
            logger.warning(f"[ORCHESTRATION] Failed to invalidate LLM session: {e}")

    # 3b. Invalidate page_state for old company (all pages)
    if old_company_id:
        try:
            from .page_state_manager import get_page_state_manager
            page_state_manager = get_page_state_manager()
            # Invalidate all pages for the OLD company
            pages_to_invalidate = ["dashboard", "invoices", "expenses", "chat", "hr", "settings"]
            for page in pages_to_invalidate:
                page_state_manager.invalidate_page_state(uid, old_company_id, page)
            logger.info(
                f"[ORCHESTRATION] Invalidated page_state for company change: "
                f"uid={uid} old_company={old_company_id} pages={pages_to_invalidate}"
            )
        except Exception as ps_error:
            logger.warning(f"[ORCHESTRATION] Failed to invalidate page_state: {ps_error}")

    # 4. Cancel existing orchestration
    state_manager.request_cancellation(uid, session_id)
    await asyncio.sleep(0.1)

    # 5. Create new orchestration with company pre-selected
    orchestration_id = state_manager.create_orchestration(
        uid, session_id, company_id
    )

    # 6. Start orchestration with company phase to load FULL mandate data
    # IMPORTANT: Always run company phase to ensure complete company_data
    # (input_drive_doc_id, bank_erp, etc.) needed for widget caches.
    # The target_company_id ensures the correct company is selected.
    logger.info(
        f"[ORCHESTRATION] Company change: loading full mandate data for "
        f"company_id={company_id}"
    )
    asyncio.create_task(
        _run_orchestration(
            uid, session_id, orchestration_id,
            user_data={},
            skip_user_setup=True,
            skip_company_phase=False,  # Always load full company data
            target_company_id=company_id
        )
    )

    return {
        "type": "dashboard.company_change",
        "payload": {
            "success": True,
            "orchestration_id": orchestration_id,
            "company_id": company_id,
            "previous_company_id": old_company_id
        }
    }


async def handle_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle dashboard.refresh event.

    Forces refresh of all dashboard data from source.
    """
    logger.info(f"[ORCHESTRATION] Refresh requested: uid={uid}")

    # Get current company from Niveau 1 + Niveau 2
    company_id = get_selected_company_id(uid)
    company_data = get_company_context(uid, company_id) if company_id else None

    if not company_id:
        return {
            "type": "error",
            "payload": {
                "success": False,
                "error": "No company selected",
                "code": "NO_COMPANY"
            }
        }

    # Invalidate cache and fetch fresh data
    dashboard_handlers = get_dashboard_handlers()

    # Get mandate_path from company_data if available
    mandate_path = company_data.get("mandate_path", "") if company_data else ""

    result = await dashboard_handlers.full_data(
        user_id=uid,
        company_id=company_id,
        force_refresh=True,
        mandate_path=mandate_path
    )

    # Fix company data if empty or missing critical fields (use company_data from session)
    if result.get("success") and result.get("data"):
        existing_company = result["data"].get("company", {})
        # Check for essential fields like id, name, currency
        needs_fix = (
            not existing_company
            or not existing_company.get("id")
            or not existing_company.get("name")
            or not existing_company.get("currency")
        )
        if needs_fix and company_data:
            result["data"]["company"] = transform_company_data_to_info(company_data)
            logger.info(
                f"[ORCHESTRATION] Refresh: Fixed company data using transform_company_data_to_info"
            )

    # Broadcast to user
    await hub.broadcast(uid, {
        "type": WS_EVENTS.DASHBOARD.FULL_DATA,
        "payload": result
    })

    return {
        "type": "dashboard.refresh",
        "payload": {
            "success": True,
            "company_id": company_id
        }
    }


# ============================================
# ORCHESTRATION RUNNER
# ============================================

async def _run_orchestration(
    uid: str,
    session_id: str,
    orchestration_id: str,
    user_data: Dict[str, Any],
    skip_user_setup: bool = False,
    skip_company_phase: bool = False,
    target_company_id: Optional[str] = None
):
    """
    Run the full orchestration sequence.

    This is the main orchestration coroutine that manages
    all phases of dashboard initialization.

    Mirrors AuthState.process_post_authentication() flow.
    """
    state_manager = get_state_manager()

    try:
        # ========================================
        # PHASE 0: USER SETUP (Critical)
        # ========================================
        if not skip_user_setup:
            await _notify_phase_start(uid, "user_setup")

            user_setup_result = await _run_user_setup_phase(
                uid, session_id, orchestration_id, user_data
            )

            if state_manager.is_cancelled(uid, session_id, orchestration_id):
                logger.info(f"[ORCHESTRATION] Cancelled during user_setup phase")
                return

            if not user_setup_result.get("success"):
                await _notify_phase_complete(
                    uid, "user_setup",
                    success=False,
                    error=user_setup_result.get("error", "User setup failed")
                )
                return

            # Update orchestration with user setup results including authorized_companies_ids
            state_manager.update_orchestration(uid, session_id, {
                "is_first_connect": user_setup_result.get("is_first_connect", False),
                "is_invited_user": user_setup_result.get("is_invited_user", False),
                "authorized_companies_ids": user_setup_result.get("authorized_companies_ids", []),
                "phase": "company"
            })

            await _notify_phase_complete(uid, "user_setup", success=True)

        # ========================================
        # PHASE 1: COMPANY SELECTION (Critical)
        # ========================================
        if not skip_company_phase:
            await _notify_phase_start(uid, "company")

            # Get authorized companies from orchestration state
            orchestration_state = state_manager.get_orchestration(uid, session_id)
            authorized_companies = orchestration_state.get("authorized_companies_ids", []) if orchestration_state else []

            company_id, company_data = await _run_company_phase(
                uid, session_id, orchestration_id, authorized_companies,
                target_company_id=target_company_id
            )

            if state_manager.is_cancelled(uid, session_id, orchestration_id):
                logger.info(f"[ORCHESTRATION] Cancelled during company phase")
                return

            if not company_id:
                await _notify_phase_complete(uid, "company", success=False, error="No companies found")
                return

            # Company data is already saved to Redis legacy key in _run_company_phase()

            # Save selected company
            state_manager.update_orchestration(uid, session_id, {
                "selected_company_id": company_id,
                "phase": "data"
            })

            await _notify_phase_complete(uid, "company", success=True)
        else:
            # Get company_id from target_company_id (passed from handle_company_change)
            # or fallback to state or Niveau 1 cache
            state = state_manager.get_orchestration(uid, session_id)
            company_id = target_company_id or (state.get("selected_company_id") if state else None) or get_selected_company_id(uid)

            # Get company_data from Niveau 2 cache
            company_data = get_company_context(uid, company_id) if company_id else None

            if not company_id:
                logger.error("[ORCHESTRATION] No company_id for skip_company_phase")
                return

            # Update state with selected_company_id
            state_manager.update_orchestration(uid, session_id, {
                "selected_company_id": company_id,
                "phase": "data"
            })

        # ========================================
        # PHASE 2: DATA LOADING (Background)
        # ========================================
        await _notify_phase_start(uid, "data")

        # Get mandate_path from company_data for billing widget
        mandate_path = ""
        if company_data:
            mandate_path = company_data.get("mandate_path", "")

        await _run_data_phase(
            uid, session_id, orchestration_id, company_id,
            mandate_path=mandate_path,
            company_data=company_data  # Pass full company_data for cache population
        )

        if state_manager.is_cancelled(uid, session_id, orchestration_id):
            logger.info(f"[ORCHESTRATION] Cancelled during data phase")
            return

        state_manager.update_orchestration(uid, session_id, {"phase": "llm"})
        await _notify_phase_complete(uid, "data", success=True)

        # ========================================
        # PHASE 3: LLM SESSION (Background)
        # ========================================
        await _notify_phase_start(uid, "llm")

        # Get company metadata for LLM from Niveau 2 cache
        company_data = get_company_context(uid, company_id) or {}

        await _run_llm_phase(
            uid,
            company_id,
            mandate_path=company_data.get("mandate_path", ""),
            client_uuid=company_data.get("client_uuid", "")
        )

        state_manager.update_orchestration(uid, session_id, {"phase": "completed"})
        await _notify_phase_complete(uid, "llm", success=True)

        logger.info(
            f"[ORCHESTRATION] Completed successfully: uid={uid} "
            f"orchestration_id={orchestration_id}"
        )

    except Exception as e:
        logger.error(
            f"[ORCHESTRATION] Failed: uid={uid} error={e}",
            exc_info=True
        )
        state_manager.update_orchestration(uid, session_id, {
            "phase": "error",
            "errors": [str(e)]
        })


# ============================================
# PHASE 0: USER SETUP
# ============================================

async def _run_user_setup_phase(
    uid: str,
    session_id: str,
    orchestration_id: str,
    user_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Phase 0: User setup - mirrors AuthState.process_post_authentication phases 1-3.

    1. check_and_create_client_document()
    2. Check first_connect -> process_immediate_top_up(50)
    3. process_share_settings()

    Returns: Dict with success status and user flags
    """
    state_manager = get_state_manager()

    try:
        # Use FirebaseManagement singleton
        firebase_mgmt = FirebaseManagement()

        # Prepare user_data with uid
        if "uid" not in user_data:
            user_data["uid"] = uid

        logger.info(f"[ORCHESTRATION] Phase 0: User setup for uid={uid}")

        # ─────────────────────────────────────────────────
        # Step 1: Check and create client document
        # ─────────────────────────────────────────────────
        logger.info(f"[ORCHESTRATION] Step 1: check_and_create_client_document")

        # Run in thread to avoid blocking
        await asyncio.to_thread(
            firebase_mgmt.check_and_create_client_document,
            user_data
        )

        if state_manager.is_cancelled(uid, session_id, orchestration_id):
            return {"success": False, "error": "Cancelled"}

        # ─────────────────────────────────────────────────
        # Step 2: Check first_connect and add credit
        # ─────────────────────────────────────────────────
        logger.info(f"[ORCHESTRATION] Step 2: Check first_connect")

        user_doc = await asyncio.to_thread(
            firebase_mgmt.get_document,
            f"users/{uid}"
        )

        is_first_connect = False
        if user_doc and 'first_connect' not in user_doc:
            is_first_connect = True
            logger.info(f"[ORCHESTRATION] First connection for user {uid} - adding ${FIRST_CONNECT_CREDIT} credit")

            # Add initial credit
            await asyncio.to_thread(
                firebase_mgmt.process_immediate_top_up,
                uid,
                FIRST_CONNECT_CREDIT
            )

            # Update user_data with first_connect flag
            user_data['first_connect'] = False
            await asyncio.to_thread(
                firebase_mgmt.check_and_create_client_document,
                user_data
            )

            # Notify frontend about the credit
            await hub.broadcast(uid, {
                "type": "user.first_connect",
                "payload": {
                    "credit_added": FIRST_CONNECT_CREDIT,
                    "message": f"Welcome! ${FIRST_CONNECT_CREDIT} in credit has been added to your account."
                }
            })

        if state_manager.is_cancelled(uid, session_id, orchestration_id):
            return {"success": False, "error": "Cancelled"}

        # ─────────────────────────────────────────────────
        # Step 3: Process share settings
        # ─────────────────────────────────────────────────
        logger.info(f"[ORCHESTRATION] Step 3: Process share settings")

        is_invited_user = False
        user_profile = "admin"
        authorized_companies_ids = []
        share_settings = {}

        # List to send to frontend for account switcher
        shared_accounts_for_frontend = []

        if user_doc and 'share_settings' in user_doc:
            share_settings = user_doc.get('share_settings', {})

            # Check share_settings for shared accounts (DO NOT BLOCK)
            # Only include ACTIVE shared accounts in authorized companies
            accounts = share_settings.get('accounts', {})
            if accounts:
                for account_id, account_data in accounts.items():
                    companies = account_data.get('companies', [])
                    is_active = account_data.get('is_active', True)

                    # Build shared account info for frontend
                    shared_accounts_for_frontend.append({
                        "id": account_id,
                        "name": account_data.get('name', account_id),
                        "companies": companies if isinstance(companies, list) else [],
                        "is_active": is_active
                    })

                    # Only include ACTIVE shared accounts in authorized companies
                    if is_active:
                        if isinstance(companies, list) and companies:
                            is_invited_user = True
                            authorized_companies_ids.extend(companies)
                            logger.info(
                                f"[ORCHESTRATION] User {uid} has access to shared account "
                                f"{account_id} with {len(companies)} companies"
                            )
                    else:
                        # Log but DO NOT BLOCK - just skip this shared account
                        logger.info(
                            f"[ORCHESTRATION] Shared account {account_id} is disabled - "
                            f"skipping (user can still access their own companies)"
                        )

            # Set user profile based on whether they have shared accounts
            if is_invited_user:
                user_profile = "user"
                logger.info(
                    f"[ORCHESTRATION] User {uid} has {len(authorized_companies_ids)} "
                    f"authorized shared companies"
                )

        # Broadcast user profile to frontend
        # is_invited_user flag tells UI to show account switcher if shared accounts exist
        await hub.broadcast(uid, {
            "type": WS_EVENTS.USER.PROFILE,
            "payload": {
                "uid": uid,
                "email": user_data.get("email", ""),
                "display_name": user_data.get("displayName", ""),
                "is_invited_user": is_invited_user,
                "user_profile": user_profile,
                "is_first_connect": is_first_connect,
                "authorized_companies_count": len(authorized_companies_ids),
                # Include shared accounts for account switcher UI
                "shared_accounts": shared_accounts_for_frontend if is_invited_user else []
            }
        })

        # ─────────────────────────────────────────────────
        # Step 4: Load and broadcast static data for dropdowns
        # ─────────────────────────────────────────────────
        logger.info(f"[ORCHESTRATION] Step 4: Load static data for dropdowns")

        try:
            from app.wrappers.static_data_handlers import get_static_data_handlers

            static_handlers = get_static_data_handlers()
            static_result = await static_handlers.load_all_static_data()

            if static_result.get("success"):
                # Broadcast static data to frontend
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.STATIC_DATA.LOADED,
                    "payload": static_result.get("data", {})
                })
                logger.info(
                    f"[ORCHESTRATION] Static data broadcasted: "
                    f"countries={len(static_result.get('data', {}).get('countries', []))}, "
                    f"currencies={len(static_result.get('data', {}).get('currencies', []))}"
                )
            else:
                logger.warning(f"[ORCHESTRATION] Failed to load static data: {static_result.get('error')}")
        except Exception as static_err:
            # Non-blocking - static data is optional
            logger.warning(f"[ORCHESTRATION] Static data load error (non-blocking): {static_err}")

        return {
            "success": True,
            "is_first_connect": is_first_connect,
            "is_invited_user": is_invited_user,
            "user_profile": user_profile,
            "authorized_companies_ids": authorized_companies_ids
        }

    except Exception as e:
        logger.error(f"[ORCHESTRATION] User setup phase error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# ============================================
# HELPER: BUILD WORKFLOW PARAMS FOR FRONTEND
# ============================================

def _build_workflow_params(selected_mandate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build workflow_params dict for frontend from fetch_single_mandate data.

    The data is in nested structure from workflow_params document:
    - workflow_params.Router_param
    - workflow_params.Banker_param
    - workflow_params.Apbookeeper_param
    - workflow_params.Accounting_param
    - workflow_params.Asset_param

    Returns flat structure with camelCase keys for frontend TypeScript types.
    """
    wp = selected_mandate.get("workflow_params", {})

    # DEBUG: Log the raw workflow_params structure
    logger.info(f"[WORKFLOW_PARAMS] Raw wp keys: {list(wp.keys())}")
    logger.info(f"[WORKFLOW_PARAMS] Accounting_param: {wp.get('Accounting_param', 'NOT FOUND')}")
    logger.info(f"[WORKFLOW_PARAMS] Asset_param: {wp.get('Asset_param', 'NOT FOUND')}")

    # Extract nested params with fallback to empty dict
    router_p = wp.get("Router_param", {})
    banker_p = wp.get("Banker_param", {})
    apbookeeper_p = wp.get("Apbookeeper_param", {})
    accounting_p = wp.get("Accounting_param", {})
    asset_p = wp.get("Asset_param", {})

    return {
        # ─────────────────────────────────────
        # Router params
        # ─────────────────────────────────────
        "router_approval_required": router_p.get("router_approval_required", False),
        "router_automated_workflow": router_p.get("router_automated_workflow", True),
        "router_communication_method": router_p.get("router_communication_method", ""),
        "router_approval_pendinglist_enabled": router_p.get("router_approval_pendinglist_enabled", False),
        "router_departments": router_p.get("departments", []),
        # ─────────────────────────────────────
        # APbookeeper params
        # ─────────────────────────────────────
        "apbookeeper_approval_required": apbookeeper_p.get("apbookeeper_approval_required", False),
        "apbookeeper_approval_contact_creation": apbookeeper_p.get("apbookeeper_approval_contact_creation", False),
        "apbookeeper_communication_method": apbookeeper_p.get("apbookeeper_communication_method", ""),
        "apbookeeper_approval_pendinglist_enabled": apbookeeper_p.get("apbookeeper_approval_pendinglist_enabled", False),
        "apbookeeper_trust_threshold_required": apbookeeper_p.get("trust_threshold_required", False),
        "apbookeeper_trust_threshold_percent": apbookeeper_p.get("trust_threshold_percent", 95),
        "apbookeeper_automated_workflow": apbookeeper_p.get("apbookeeper_automated_workflow", False),
        # ─────────────────────────────────────
        # Banker params
        # ─────────────────────────────────────
        "banker_approval_required": banker_p.get("banker_approval_required", False),
        "banker_approval_threshold_workflow": banker_p.get("banker_approval_thresholdworkflow", 0),
        "banker_communication_method": banker_p.get("banker_communication_method", ""),
        "banker_approval_pendinglist_enabled": banker_p.get("banker_approval_pendinglist_enabled", False),
        "banker_gl_approval": banker_p.get("banker_gl_approval", False),
        "banker_voucher_approval": banker_p.get("banker_voucher_approval", False),
        # ─────────────────────────────────────
        # Accounting Date Rules
        # Firestore fields: accounting_date_definition, accounting_date, custom_mode, date_prompt
        # ─────────────────────────────────────
        "accounting_date_automated_definition": accounting_p.get("accounting_date_definition", True),
        "accounting_date_default_date": accounting_p.get("accounting_date", ""),
        "accounting_date_custom_mode": accounting_p.get("custom_mode", False),
        "accounting_date_custom_prompt": accounting_p.get("date_prompt", ""),
        # ─────────────────────────────────────
        # Asset Management
        # ─────────────────────────────────────
        "asset_management_activated": asset_p.get("asset_management_activated", False),
        "asset_automated_creation": asset_p.get("asset_automated_creation", True),
        "asset_default_method": asset_p.get("asset_default_method", "linear"),
        "asset_default_method_period": asset_p.get("asset_default_method_period", "12"),
    }


# ============================================
# PHASE 1: COMPANY SELECTION
# ============================================

async def _run_company_phase(
    uid: str,
    session_id: str,
    orchestration_id: str,
    authorized_companies_ids: Optional[List[str]] = None,
    target_company_id: Optional[str] = None
) -> tuple[Optional[str], Optional[Dict]]:
    """
    Phase 1: Load companies and select one.

    Uses FirebaseManagement.fetch_all_mandates_light() for optimized loading.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        orchestration_id: Current orchestration ID
        authorized_companies_ids: List of companies user is authorized to access
        target_company_id: If provided, select this company instead of the first one

    Returns: Tuple of (company_id, company_data) or (None, None)
    """
    state_manager = get_state_manager()

    try:
        firebase_mgmt = FirebaseManagement()

        logger.info(f"[ORCHESTRATION] Phase 1: Loading companies for uid={uid}")

        # ─────────────────────────────────────────────────
        # Step 1: Fetch companies (light version)
        # This loads the user's OWN mandates from clients/{uid}/companies
        # or mandates collection - NOT the shared companies
        # ─────────────────────────────────────────────────
        mandates = await asyncio.to_thread(
            firebase_mgmt.fetch_all_mandates_light,
            uid
        )

        if not isinstance(mandates, list):
            logger.warning(f"[ORCHESTRATION] Invalid mandates response for uid={uid}")
            return None, None

        logger.info(f"[ORCHESTRATION] {len(mandates)} mandates found for uid={uid}")

        # ─────────────────────────────────────────────────
        # Step 2: Filter by authorized companies if shared account
        # ─────────────────────────────────────────────────
        if authorized_companies_ids and len(authorized_companies_ids) > 0:
            logger.info(f"[ORCHESTRATION] Filtering by authorized companies: {authorized_companies_ids}")
            mandates = [
                m for m in mandates
                if m.get("contact_space_id") in authorized_companies_ids
            ]
            logger.info(f"[ORCHESTRATION] {len(mandates)} mandates after filtering")

        if not mandates:
            logger.warning(f"[ORCHESTRATION] No companies found for uid={uid}")
            return None, None

        # ─────────────────────────────────────────────────
        # Step 3: Build company list for frontend
        # ─────────────────────────────────────────────────
        companies = []
        for mandate in mandates:
            # Extract parent_details for client_uuid
            parent_details = mandate.get("parent_details", {})
            companies.append({
                "id": mandate.get("id", ""),
                "contact_space_id": mandate.get("contact_space_id", ""),
                "name": mandate.get("legal_name", "") or mandate.get("name", ""),
                "legal_name": mandate.get("legal_name", ""),
                "contact_space_name": mandate.get("contact_space_name", ""),
                "is_active": mandate.get("isactive", True),
                # Critical: include mandate_path for company context
                "mandate_path": mandate.get("mandate_path", ""),
                "client_uuid": parent_details.get("client_uuid", ""),
                "parent_doc_id": parent_details.get("parent_doc_id", ""),
            })

        # Broadcast company list
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY.LIST,
            "payload": {
                "companies": companies,
                "total": len(companies)
            }
        })

        if state_manager.is_cancelled(uid, session_id, orchestration_id):
            return None, None

        # ─────────────────────────────────────────────────
        # Step 4: Select company (target or first)
        # ─────────────────────────────────────────────────
        if target_company_id:
            # Find the target company in the mandates list
            selected_mandate = next(
                (m for m in mandates
                 if m.get("contact_space_id") == target_company_id
                 or m.get("id") == target_company_id),
                mandates[0]  # Fallback to first if target not found
            )
            logger.info(f"[ORCHESTRATION] Pre-selected target company: {target_company_id}")
        else:
            selected_mandate = mandates[0]  # Auto-select first

        company_id = selected_mandate.get("contact_space_id", selected_mandate.get("id", ""))

        logger.info(f"[ORCHESTRATION] Selected company: {company_id}")

        # ─────────────────────────────────────────────────
        # Step 4.5: Load FULL mandate data (not just light version)
        # fetch_all_mandates_light() only loads basic fields
        # fetch_single_mandate() loads ALL fields including company info
        # ─────────────────────────────────────────────────
        mandate_path = selected_mandate.get("mandate_path", "")
        if mandate_path:
            try:
                full_mandate = await asyncio.to_thread(
                    firebase_mgmt.fetch_single_mandate,
                    mandate_path
                )
                if full_mandate:
                    # Merge full data into selected_mandate
                    selected_mandate.update(full_mandate)
                    logger.info(f"[ORCHESTRATION] Full mandate loaded: keys={list(full_mandate.keys())[:10]}...")
            except Exception as e:
                logger.warning(f"[ORCHESTRATION] Failed to load full mandate: {e}")
                # Continue with light data

        # ─────────────────────────────────────────────────
        # Step 5: Build full company data from mandate
        # ─────────────────────────────────────────────────
        # Extract parent_details which may contain nested fields
        parent_details = selected_mandate.get("parent_details", {}) or {}

        company_data = {
            "id": selected_mandate.get("id", ""),
            "contact_space_id": company_id,
            "name": selected_mandate.get("legal_name", "") or selected_mandate.get("name", ""),
            "legal_name": selected_mandate.get("legal_name", ""),
            "contact_space_name": selected_mandate.get("contact_space_name", ""),
            "mandate_path": selected_mandate.get("mandate_path", ""),
            "client_uuid": (
                selected_mandate.get("client_uuid") or
                parent_details.get("client_uuid", "")
            ),
            "bank_erp": selected_mandate.get("bank_erp", ""),
            "gl_accounting_erp": selected_mandate.get("gl_accounting_erp", ""),
            "ap_erp": selected_mandate.get("ap_erp", ""),
            "ar_erp": selected_mandate.get("ar_erp", ""),
            "dms_type": selected_mandate.get("dms_type", ""),
            "base_currency": selected_mandate.get("base_currency", "EUR"),
            # Drive IDs - check both top-level and parent_details
            "input_drive_doc_id": selected_mandate.get("input_drive_doc_id", ""),
            "drive_client_parent_id": (
                selected_mandate.get("drive_client_parent_id") or
                parent_details.get("drive_client_parent_id", "")
            ),
            "parent_doc_id": (
                selected_mandate.get("parent_doc_id") or
                parent_details.get("parent_doc_id", "")
            ),
            # Parent details (also keep nested for compatibility)
            "parent_details": parent_details,
            # ─────────────────────────────────────────────────
            # Company Info Fields (pour Settings page)
            # Priority: fetch_single_mandate fields > client_* fields > parent_details
            # ─────────────────────────────────────────────────
            "address": (
                selected_mandate.get("address") or
                selected_mandate.get("client_address") or
                parent_details.get("client_address", "")
            ),
            "phone_number": (
                selected_mandate.get("phone_number") or
                selected_mandate.get("client_phone") or
                parent_details.get("client_phone", "")
            ),
            "email": (
                selected_mandate.get("email") or
                selected_mandate.get("client_mail") or
                parent_details.get("client_mail", "")
            ),
            "country": selected_mandate.get("country", ""),
            "language": selected_mandate.get("language", ""),
            "legal_status": selected_mandate.get("legal_status", ""),
            "has_vat": selected_mandate.get("has_vat", False),
            "website": selected_mandate.get("website", ""),
            "vat_number": selected_mandate.get("vat_number", ""),
            # Legacy fields (for backward compatibility)
            "client_mail": (
                selected_mandate.get("email") or
                selected_mandate.get("client_mail") or
                parent_details.get("client_mail", "")
            ),
            "client_name": (
                selected_mandate.get("client_name") or
                parent_details.get("client_name", "")
            ),
            "client_address": (
                selected_mandate.get("address") or
                selected_mandate.get("client_address") or
                parent_details.get("client_address", "")
            ),
            "client_phone": (
                selected_mandate.get("phone_number") or
                selected_mandate.get("client_phone") or
                parent_details.get("client_phone", "")
            ),
            # ─────────────────────────────────────────────────
            # Workflow Params (structure complète pour frontend)
            # Loaded from subcollection setup/workflow_params by fetch_single_mandate()
            # ─────────────────────────────────────────────────
            "workflow_params": _build_workflow_params(selected_mandate),
            # ─────────────────────────────────────────────────
            # Communication Settings (pour frontend et jobbeur)
            # ─────────────────────────────────────────────────
            "communication_settings": {
                "dms_type": selected_mandate.get("dms_type", "odoo"),
                "chat_type": selected_mandate.get("chat_type", "rag"),
                "communication_log_type": selected_mandate.get("communication_log_type", "rag"),
            },
            # Champs plats conservés pour compatibilité backend
            "apbookeeper_approval_required": selected_mandate.get("apbookeeper_approval_required", False),
            "router_approval_required": selected_mandate.get("router_approval_required", False),
            "banker_approval_required": selected_mandate.get("banker_approval_required", False),
            "chat_type": selected_mandate.get("chat_type", "rag"),
            "communication_log_type": selected_mandate.get("communication_log_type", "rag"),
            "router_automated_workflow": selected_mandate.get("router_automated_workflow", True),
            # ─────────────────────────────────────────────────
            # Context Details (pour LLM et Settings page)
            # Loaded from subcollection {mandate_path}/context/ by fetch_single_mandate()
            # ─────────────────────────────────────────────────
            "context_details": selected_mandate.get("context_details", {
                "general_context": "",
                "accounting_context": "",
                "bank_context": "",
                "invoices_context": "",
                "expenses_context": "",
                "banks_cash_context": "",
                "hr_context": "",
                "taxes_context": "",
                "letters_context": "",
                "contrats_context": "",
                "financial_statement_context": "",
            }),
        }

        # Broadcast company details
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY.DETAILS,
            "payload": company_data
        })

        # ─────────────────────────────────────────────────
        # Step 6: Cache company selection (Niveau 1 + Niveau 2)
        # Store selected_company_id (Niveau 1) and full context (Niveau 2)
        # ─────────────────────────────────────────────────
        set_selected_company(uid, company_id, company_data)

        return company_id, company_data

    except Exception as e:
        logger.error(f"[ORCHESTRATION] Company phase error: {e}", exc_info=True)
        return None, None


# ============================================
# HELPER: Transform company_data to CompanyInfo format
# ============================================

def transform_company_data_to_info(company_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform company_data (snake_case from orchestration) to CompanyInfo format (camelCase).

    This helper ensures consistent transformation of company data for frontend consumption.
    Use this whenever you have company_data from:
    - UserSessionStateManager.get_selected_company()
    - _run_company_phase() return value
    - Redis session storage

    Args:
        company_data: Company data in snake_case format from orchestration/session
            {
                "id": "...",
                "contact_space_id": "...",
                "name": "...",
                "legal_name": "...",
                "mandate_path": "...",
                "client_uuid": "...",
                ...
            }

    Returns:
        CompanyInfo format (camelCase) for frontend:
            {
                "id": "...",
                "name": "...",
                "legalName": "...",
                "mandatePath": "...",
                "clientUuid": "...",
                "balance": 0,
                "currency": "EUR",
                "integrations": {"banking": False, "erp": False}
            }
    """
    if not company_data:
        return {}

    # Extract parent_details if present (may contain nested fields)
    parent_details = company_data.get("parent_details", {}) or {}

    return {
        # Core identifiers
        "id": (
            company_data.get("contact_space_id") or
            company_data.get("company_id") or
            company_data.get("id", "")
        ),
        # Names
        "name": (
            company_data.get("legal_name") or
            company_data.get("name") or
            company_data.get("company_name", "")
        ),
        "legalName": (
            company_data.get("legal_name") or
            company_data.get("name", "")
        ),
        "contactSpaceName": company_data.get("contact_space_name", ""),
        # Critical paths
        "mandatePath": company_data.get("mandate_path", ""),
        "clientUuid": (
            company_data.get("client_uuid") or
            parent_details.get("client_uuid", "")
        ),
        # Financial defaults (actual balance fetched separately by _get_balance_info)
        "balance": company_data.get("balance", 0),
        "currency": company_data.get("base_currency") or company_data.get("currency", "EUR"),
        # Integrations
        "integrations": {
            "banking": bool(company_data.get("banking_connected") or company_data.get("bank_erp")),
            "erp": bool(
                company_data.get("erp_connected") or
                company_data.get("gl_accounting_erp") or
                company_data.get("ap_erp")
            ),
        },
        # ERP configuration (useful for backend operations)
        "bankErp": company_data.get("bank_erp", ""),
        "glAccountingErp": company_data.get("gl_accounting_erp", ""),
        "apErp": company_data.get("ap_erp", ""),
        "arErp": company_data.get("ar_erp", ""),
        "dmsType": company_data.get("dms_type", ""),
        # Drive IDs
        "inputDriveDocId": company_data.get("input_drive_doc_id", ""),
        "driveClientParentId": (
            company_data.get("drive_client_parent_id") or
            parent_details.get("drive_client_parent_id", "")
        ),
        "parentDocId": (
            company_data.get("parent_doc_id") or
            parent_details.get("parent_doc_id", "")
        ),
        # Contact info
        "clientMail": (
            company_data.get("client_mail") or
            parent_details.get("client_mail", "")
        ),
        "clientName": (
            company_data.get("client_name") or
            parent_details.get("client_name", "")
        ),
        "clientAddress": (
            company_data.get("client_address") or
            parent_details.get("client_address", "")
        ),
        "clientPhone": (
            company_data.get("client_phone") or
            parent_details.get("client_phone", "")
        ),
        # Workflow settings
        "apbookeeperApprovalRequired": company_data.get("apbookeeper_approval_required", False),
        "routerApprovalRequired": company_data.get("router_approval_required", False),
        "bankerApprovalRequired": company_data.get("banker_approval_required", False),
    }


# ============================================
# CACHE POPULATION (Before Data Loading)
# ============================================

async def _populate_widget_caches(
    uid: str,
    company_id: str,
    company_data: Dict[str, Any]
):
    """
    Populate Redis cache with data from external sources.

    This function calls the cache handlers to fetch and cache data
    from Drive, Firebase, and ERP. This ensures the metrics widgets
    have data to display.

    Data sources:
        - Drive (Router): drive_cache_handlers.get_documents()
        - Firebase (AP): firebase_cache_handlers.get_ap_documents()
        - Firebase (Bank): firebase_cache_handlers.get_bank_transactions()
        - Note: Expenses are already cached by _get_expenses() in full_data()

    Args:
        uid: Firebase user ID
        company_id: Selected company ID
        company_data: Company metadata containing Drive folder ID, mandate_path, etc.
    """
    import asyncio
    from ..drive_cache_handlers import get_drive_cache_handlers
    from ..firebase_cache_handlers import get_firebase_cache_handlers

    drive_handlers = get_drive_cache_handlers()
    firebase_handlers = get_firebase_cache_handlers()

    # Extract parameters from company_data
    # Drive folder ID can be in different fields depending on mandate structure
    # Check both top-level and nested in parent_details
    parent_details = company_data.get("parent_details", {}) or {}

    input_drive_id = (
        company_data.get("input_drive_doc_id") or  # Direct mandate field
        company_data.get("drive_client_parent_id") or
        parent_details.get("drive_client_parent_id") or  # Nested in parent_details
        company_data.get("parent_doc_id") or
        company_data.get("input_drive_id") or
        ""
    )

    # mandate_path is CRITICAL for correct Firebase queries
    mandate_path = company_data.get("mandate_path", "")

    logger.info(
        f"[ORCHESTRATION] Populating widget caches: uid={uid} "
        f"company_id={company_id} "
        f"drive_id={input_drive_id[:20] if input_drive_id else 'none'} "
        f"mandate_path={mandate_path[:50] if mandate_path else 'none'}..."
    )

    # Fetch all data sources in parallel (non-blocking)
    tasks = []

    # 1. Router documents from Drive (if Drive ID available)
    if input_drive_id:
        tasks.append(
            _safe_cache_fetch(
                "Router/Drive",
                drive_handlers.get_documents(uid, company_id, input_drive_id)
            )
        )
    else:
        logger.warning(f"[ORCHESTRATION] No Drive folder ID for Router cache - check mandate config")

    # 2. AP documents from Firebase (requires mandate_path for correct path)
    if mandate_path:
        tasks.append(
            _safe_cache_fetch(
                "APBookkeeper",
                firebase_handlers.get_ap_documents(uid, company_id, mandate_path=mandate_path)
            )
        )
    else:
        logger.warning(f"[ORCHESTRATION] No mandate_path for AP cache")

    # 3. Bank transactions (requires ERP connection)
    # Bank transactions come from ERP (Odoo), NOT from Firebase
    client_uuid = company_data.get("client_uuid", "")
    bank_erp = company_data.get("bank_erp", "")

    if client_uuid and bank_erp:
        tasks.append(
            _safe_cache_fetch(
                "Bank",
                firebase_handlers.get_bank_transactions(
                    uid, company_id,
                    client_uuid=client_uuid,
                    bank_erp=bank_erp
                )
            )
        )
    else:
        logger.warning(
            f"[ORCHESTRATION] No ERP config for Bank cache - "
            f"client_uuid={bool(client_uuid)} bank_erp={bank_erp}"
        )

    # 4. COA (accounts + functions) - Cache Niveau 2
    # Le COA est traité comme donnée critique de niveau entreprise
    # Il est pré-chargé pour que la page COA s'affiche immédiatement
    if mandate_path:
        tasks.append(
            _safe_cache_fetch(
                "COA",
                _populate_coa_cache(uid, company_id, mandate_path)
            )
        )
    else:
        logger.warning(f"[ORCHESTRATION] No mandate_path for COA cache")

    # Execute all cache fetches in parallel
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    logger.info(f"[ORCHESTRATION] Widget caches populated for company={company_id}")


async def _safe_cache_fetch(name: str, coro):
    """
    Safely execute a cache fetch operation with error handling.

    Args:
        name: Human-readable name for logging
        coro: Coroutine to execute
    """
    try:
        result = await coro
        if result and result.get("data"):
            source = result.get("source", "unknown")
            data = result.get("data", {})
            if isinstance(data, dict):
                item_count = sum(len(v) for v in data.values() if isinstance(v, list))
            elif isinstance(data, list):
                item_count = len(data)
            else:
                item_count = 0
            logger.info(f"[CACHE] {name}: {item_count} items (source={source})")
        else:
            logger.warning(f"[CACHE] {name}: No data or error")
            if result and result.get("oauth_error"):
                logger.warning(f"[CACHE] {name}: OAuth re-authentication required")
    except Exception as e:
        logger.error(f"[CACHE] {name} fetch error: {e}")


async def _populate_coa_cache(
    uid: str,
    company_id: str,
    mandate_path: str
) -> Dict[str, Any]:
    """
    Charge et cache les données COA en niveau 2.

    Le COA est traité comme donnée critique de niveau entreprise.
    Il est pré-chargé pendant l'orchestration dashboard pour que
    la page COA puisse l'afficher immédiatement depuis le cache.

    Args:
        uid: Firebase user ID
        company_id: Company ID
        mandate_path: Chemin mandat Firebase

    Returns:
        Dict avec success et data
    """
    try:
        from ..frontend.pages.coa.handlers import get_coa_handlers

        handlers = get_coa_handlers()

        # Charger les données COA (accounts + functions)
        result = await handlers.full_data(
            uid=uid,
            company_id=company_id,
            mandate_path=mandate_path,
            force_refresh=False  # Utilise cache existant si disponible
        )

        if result.get("success") and result.get("data"):
            # Sauvegarder en cache niveau 2 (company:{uid}:{cid}:coa)
            redis_client = get_redis()
            cache_key = build_company_coa_key(uid, company_id)
            cache_data = {
                **result["data"],
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "source": "dashboard_orchestration"
            }
            redis_client.setex(
                cache_key,
                RedisTTL.COMPANY_CONTEXT,  # 1 heure (comme company:context)
                json.dumps(cache_data)
            )
            logger.info(
                f"[ORCHESTRATION] COA cached niveau 2: "
                f"{len(result['data'].get('accounts', []))} accounts, "
                f"{len(result['data'].get('functions', []))} functions"
            )

        return result

    except Exception as e:
        logger.error(f"[ORCHESTRATION] COA cache population error: {e}")
        return {"success": False, "error": str(e)}


# ============================================
# PHASE 2: DATA LOADING
# ============================================

async def _run_data_phase(
    uid: str,
    session_id: str,
    orchestration_id: str,
    company_id: str,
    mandate_path: str = "",
    company_data: Dict[str, Any] = None
):
    """
    Phase 2: Load all dashboard data in parallel.

    First populates Redis cache with data from Drive/ERP/Firebase,
    then uses DashboardHandlers.full_data() for aggregated fetch.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        orchestration_id: Unique orchestration ID
        company_id: Selected company ID
        mandate_path: Full Firestore path to mandate (for billing widget)
        company_data: Full company metadata (for Drive folder ID, etc.)
    """
    state_manager = get_state_manager()
    dashboard_handlers = get_dashboard_handlers()

    try:
        # Update all widget statuses to loading
        state_manager.update_orchestration(uid, session_id, {
            "widgets_status": {
                "balance": "loading",
                "metrics": "loading",
                "storage": "loading",
                "expenses": "loading",
                "tasks": "loading",
                "apbookeeper_jobs": "loading",
                "router_jobs": "loading",
                "banker_jobs": "loading",
                "approval_waitlist": "loading"
            }
        })

        # Notify loading progress
        await _notify_loading_progress(uid, "all", "loading")

        # ════════════════════════════════════════════════════════════
        # STEP 1: Populate Redis cache with data from external sources
        # This ensures metrics widgets have data to display
        # ════════════════════════════════════════════════════════════
        await _populate_widget_caches(uid, company_id, company_data or {})

        if state_manager.is_cancelled(uid, session_id, orchestration_id):
            return

        # ════════════════════════════════════════════════════════════
        # STEP 2: Fetch all data using existing handler (reads from cache)
        # ════════════════════════════════════════════════════════════
        result = await dashboard_handlers.full_data(
            user_id=uid,
            company_id=company_id,
            force_refresh=False,
            include_activity=True,
            mandate_path=mandate_path
        )

        if state_manager.is_cancelled(uid, session_id, orchestration_id):
            return

        # ════════════════════════════════════════════════════════════
        # STEP 3: Fix company data if empty (use company_data from orchestration)
        # _get_company_info() may return {} if Firestore paths don't exist,
        # but we have the full company_data from company phase
        # ════════════════════════════════════════════════════════════
        logger.info(
            f"[ORCHESTRATION] STEP 3 - Checking company data: "
            f"result.success={result.get('success')} "
            f"has_result_data={bool(result.get('data'))} "
            f"has_company_data_param={bool(company_data)} "
            f"company_data_keys={list(company_data.keys()) if company_data else []}"
        )

        if result.get("success") and result.get("data"):
            existing_company = result["data"].get("company", {})

            logger.info(
                f"[ORCHESTRATION] STEP 3 - existing_company from full_data: "
                f"empty={not existing_company} "
                f"keys={list(existing_company.keys()) if existing_company else []} "
                f"mandatePath={existing_company.get('mandatePath', 'MISSING')}"
            )

            # Check if company data is empty or missing critical fields
            # Note: _get_company_info() may return only {"mandatePath": "..."} if Firestore paths don't exist
            # We need to check for essential fields like id, name, currency
            needs_fix = (
                not existing_company
                or not existing_company.get("id")
                or not existing_company.get("name")
                or not existing_company.get("currency")
            )
            if needs_fix:
                logger.info(
                    f"[ORCHESTRATION] STEP 3 - Company needs fix: "
                    f"existing_company_empty={not existing_company} "
                    f"missing_id={not existing_company.get('id') if existing_company else True} "
                    f"missing_name={not existing_company.get('name') if existing_company else True} "
                    f"company_data_available={bool(company_data)}"
                )
                if company_data:
                    # Transform snake_case company_data to camelCase CompanyInfo format
                    transformed_company = transform_company_data_to_info(company_data)
                    result["data"]["company"] = transformed_company
                    logger.info(
                        f"[ORCHESTRATION] STEP 3 - Fixed company data: "
                        f"id={transformed_company.get('id')} mandatePath={transformed_company.get('mandatePath')}"
                    )
                else:
                    logger.warning(
                        f"[ORCHESTRATION] STEP 3 - Cannot fix company: company_data is None/empty"
                    )

        # Update all widget statuses to completed
        state_manager.update_orchestration(uid, session_id, {
            "widgets_status": {
                "balance": "completed",
                "metrics": "completed",
                "storage": "completed",
                "expenses": "completed",
                "tasks": "completed",
                "apbookeeper_jobs": "completed",
                "router_jobs": "completed",
                "banker_jobs": "completed",
                "approval_waitlist": "completed"
            }
        })

        # Broadcast full dashboard data
        tasks_in_result = result.get("data", {}).get("tasks", [])
        logger.info(f"[ORCHESTRATION] Broadcasting FULL_DATA: tasks_count={len(tasks_in_result)}, sample_task={tasks_in_result[0] if tasks_in_result else 'NONE'}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.DASHBOARD.FULL_DATA,
            "payload": result
        })

        # ════════════════════════════════════════════════════════════
        # STEP 4: Save page_state for fast recovery on page refresh
        # NOTE: Only save dashboard page_state here.
        # Other pages (expenses/notes de frais, invoices, etc.) save their
        # own page_state via their dedicated orchestration handlers.
        # The "expenses" data in dashboard is billing/usage history (task_manager),
        # NOT the Notes de Frais module (expenses_details) - different data sources!
        # ════════════════════════════════════════════════════════════
        if result.get("success") and result.get("data"):
            try:
                from .page_state_manager import get_page_state_manager
                page_state_manager = get_page_state_manager()

                # Save dashboard page_state only
                page_state_manager.save_page_state(
                    uid=uid,
                    company_id=company_id,
                    page="dashboard",
                    mandate_path=mandate_path,
                    data=result["data"]
                )
                logger.info(f"[ORCHESTRATION] Page state saved for dashboard - uid={uid} company={company_id}")

            except Exception as ps_error:
                # Non-critical - log but don't fail orchestration
                logger.warning(f"[ORCHESTRATION] Failed to save page_state: {ps_error}")

        # Notify loading complete
        await _notify_loading_progress(uid, "all", "completed")

    except Exception as e:
        logger.error(f"[ORCHESTRATION] Data phase error: {e}", exc_info=True)

        # Update widget statuses to error
        state_manager.update_orchestration(uid, session_id, {
            "widgets_status": {
                "balance": "error",
                "metrics": "error",
                "storage": "error",
                "expenses": "error",
                "tasks": "error",
                "apbookeeper_jobs": "error",
                "router_jobs": "error",
                "banker_jobs": "error",
                "approval_waitlist": "error"
            }
        })

        await _notify_loading_progress(uid, "all", "error", str(e))


# ============================================
# PHASE 3: LLM SESSION
# ============================================

async def _run_llm_phase(
    uid: str,
    company_id: str,
    mandate_path: str = "",
    client_uuid: str = ""
):
    """
    Phase 3: Initialize LLM session in background.

    Uses existing SessionStateManager for state persistence.
    Uses mandate_path and client_uuid from company data.
    """
    try:
        # Use existing SessionStateManager
        session_state_manager = SessionStateManager()

        # Check if session already exists
        if session_state_manager.session_exists(uid, company_id):
            logger.info(
                f"[ORCHESTRATION] LLM session already exists: "
                f"uid={uid} company={company_id}"
            )

            # Extend TTL
            session_state_manager.extend_ttl(uid, company_id)

            # Notify ready
            await hub.broadcast(uid, {
                "type": WS_EVENTS.LLM.SESSION_READY,
                "payload": {
                    "ready": True,
                    "company_id": company_id,
                    "mandate_path": mandate_path,
                    "source": "cache"
                }
            })
            return

        # Create new session state with full context
        session_state_manager.save_session_state(
            user_id=uid,
            company_id=company_id,
            user_context={
                "company_id": company_id,
                "mandate_path": mandate_path,
                "client_uuid": client_uuid,
                "collection_name": mandate_path  # Used by LLM
            },
            jobs_data={},
            jobs_metrics={},
            is_on_chat_page=False,
            current_active_thread=None
        )

        # Notify ready
        await hub.broadcast(uid, {
            "type": WS_EVENTS.LLM.SESSION_READY,
            "payload": {
                "ready": True,
                "company_id": company_id,
                "mandate_path": mandate_path,
                "source": "new"
            }
        })

        logger.info(
            f"[ORCHESTRATION] LLM session initialized: "
            f"uid={uid} company={company_id} mandate_path={mandate_path}"
        )

    except Exception as e:
        logger.error(f"[ORCHESTRATION] LLM phase error: {e}", exc_info=True)


# ============================================
# ACCOUNT SWITCHING (FUTURE)
# ============================================

async def handle_switch_account(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle dashboard.switch_account event.

    Allows users with shared accounts to switch between:
    - Their own account (loads their own companies from clients/{uid}/companies)
    - Shared accounts (loads companies from share_settings.accounts.{id}.companies)

    Flow:
    1. Validate target_account_id from payload
    2. If switching to own account: Clear authorized_companies filter
    3. If switching to shared account: Load authorized_companies from share_settings
    4. Invalidate old LLM session (context change)
    5. Cancel current orchestration
    6. Start new orchestration with updated company filter

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Must contain 'target_account_id' ('own' for user's own account)

    Returns:
        Response dict with new orchestration_id
    """
    target_account_id = payload.get("target_account_id")

    if not target_account_id:
        return {
            "type": "error",
            "payload": {
                "success": False,
                "error": "target_account_id required",
                "code": "MISSING_ACCOUNT_ID"
            }
        }

    logger.info(
        f"[ORCHESTRATION] Account switch requested: uid={uid} "
        f"target={target_account_id}"
    )

    state_manager = get_state_manager()

    # 1. Get orchestration state for authorized_companies_ids
    orchestration_state = state_manager.get_orchestration(uid, session_id)
    if not orchestration_state:
        return {
            "type": "error",
            "payload": {
                "success": False,
                "error": "Orchestration state not found",
                "code": "SESSION_NOT_FOUND"
            }
        }

    old_authorized_companies = orchestration_state.get("authorized_companies_ids", [])
    current_account_id = orchestration_state.get("current_account_id", "own")

    # Get share_settings from Firebase (needed for account switching)
    share_settings = {}
    try:
        firebase_mgmt = FirebaseManagement()
        user_doc = await asyncio.to_thread(
            firebase_mgmt.get_document,
            f"users/{uid}"
        )
        if user_doc and 'share_settings' in user_doc:
            share_settings = user_doc.get('share_settings', {})
    except Exception as e:
        logger.warning(f"[ORCHESTRATION] Failed to fetch share_settings: {e}")

    # Check if already on this account
    if target_account_id == current_account_id:
        return {
            "type": "dashboard.switch_account",
            "payload": {
                "success": True,
                "message": "Already on this account",
                "account_id": target_account_id
            }
        }

    # 2. Determine new authorized_companies based on target
    new_authorized_companies = []
    is_own_account = target_account_id == "own" or target_account_id == uid

    if is_own_account:
        # Switching to own account - no filter, load all user's companies
        new_authorized_companies = []
        logger.info(f"[ORCHESTRATION] Switching to own account for uid={uid}")
    else:
        # Switching to a shared account - validate and get companies
        accounts = share_settings.get("accounts", {})

        if target_account_id not in accounts:
            return {
                "type": "error",
                "payload": {
                    "success": False,
                    "error": f"Shared account '{target_account_id}' not found",
                    "code": "ACCOUNT_NOT_FOUND"
                }
            }

        account_data = accounts[target_account_id]

        if not account_data.get("is_active", True):
            return {
                "type": "error",
                "payload": {
                    "success": False,
                    "error": f"Shared account '{target_account_id}' is not active",
                    "code": "ACCOUNT_INACTIVE"
                }
            }

        companies = account_data.get("companies", [])
        if not companies:
            return {
                "type": "error",
                "payload": {
                    "success": False,
                    "error": f"No companies available in shared account",
                    "code": "NO_COMPANIES"
                }
            }

        new_authorized_companies = companies
        logger.info(
            f"[ORCHESTRATION] Switching to shared account {target_account_id} "
            f"with {len(companies)} companies"
        )

    # 3. Invalidate old LLM session (context is changing)
    # Get old company from Niveau 1 + Niveau 2
    old_company_id = get_selected_company_id(uid)
    if old_company_id:
            try:
                from ..llm_service.session_state_manager import SessionStateManager
                llm_session_manager = SessionStateManager()
                llm_session_manager.delete_session_state(uid, old_company_id)
                logger.info(
                    f"[ORCHESTRATION] Invalidated LLM session for account switch: "
                    f"uid={uid} old_company={old_company_id}"
                )
            except Exception as e:
                logger.warning(f"[ORCHESTRATION] Failed to invalidate LLM session: {e}")

    # 4. Update orchestration state with new authorized_companies
    state_manager.update_orchestration(
        uid, session_id,
        {
            "authorized_companies_ids": new_authorized_companies,
            "current_account_id": "own" if is_own_account else target_account_id,
            "is_invited_user": not is_own_account
        }
    )

    # 5. Cancel existing orchestration
    state_manager.request_cancellation(uid, session_id)
    await asyncio.sleep(0.1)

    # 6. Create and start new orchestration
    orchestration_id = state_manager.create_orchestration(uid, session_id, None)

    # Run full orchestration from company phase (skip user_setup)
    asyncio.create_task(
        _run_orchestration(
            uid, session_id, orchestration_id,
            user_data={},
            skip_user_setup=True,
            skip_company_phase=False,  # Must run to load companies with new filter
            target_company_id=None  # Let it auto-select first company
        )
    )

    # Broadcast account change to frontend
    await hub.broadcast(uid, {
        "type": WS_EVENTS.DASHBOARD.SWITCH_ACCOUNT,
        "payload": {
            "success": True,
            "account_id": "own" if is_own_account else target_account_id,
            "is_own_account": is_own_account,
            "companies_count": len(new_authorized_companies) if not is_own_account else None,
            "orchestration_id": orchestration_id
        }
    })

    return {
        "type": "dashboard.switch_account",
        "payload": {
            "success": True,
            "orchestration_id": orchestration_id,
            "account_id": "own" if is_own_account else target_account_id,
            "previous_account_id": current_account_id
        }
    }


# ============================================
# NOTIFICATION HELPERS
# ============================================

async def _notify_phase_start(uid: str, phase: str):
    """Notify frontend that a phase is starting."""
    await hub.broadcast(uid, {
        "type": WS_EVENTS.DASHBOARD.PHASE_START,
        "payload": {
            "phase": phase,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    })


async def _notify_phase_complete(
    uid: str,
    phase: str,
    success: bool = True,
    error: Optional[str] = None
):
    """Notify frontend that a phase completed."""
    await hub.broadcast(uid, {
        "type": WS_EVENTS.DASHBOARD.PHASE_COMPLETE,
        "payload": {
            "phase": phase,
            "success": success,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    })


async def _notify_loading_progress(
    uid: str,
    widget: str,
    status: str,
    error: Optional[str] = None
):
    """Notify frontend of widget loading progress."""
    await hub.broadcast(uid, {
        "type": WS_EVENTS.DASHBOARD.DATA_LOADING_PROGRESS,
        "payload": {
            "widget": widget,
            "status": status,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    })


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "handle_orchestrate_init",
    "handle_company_change",
    "handle_refresh",
    "handle_switch_account",
    "get_state_manager",
    "OrchestrationStateManager",
    # Helper functions (reusable)
    "transform_company_data_to_info",
]
