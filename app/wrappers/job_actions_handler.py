"""
Job Actions Handler - Centralized Job Action Management
========================================================

Central handler for managing job actions (process, stop, restart, delete) across
different job types (Router, APbookeeper, Bankbookeeper, Onboarding).

This module provides:
- Unified interface for job actions across all departments
- Firebase notification creation and WebSocket publishing
- Business cache updates via contextual_publisher
- Integration with external jobbeur HTTP endpoints

Architecture:
    Frontend (Next.js) -> WebSocket -> routing_orchestration/invoices_orchestration
                       -> job_actions_handler.py -> External Jobbeur HTTP APIs
                                                 -> Firebase notifications
                                                 -> Redis business cache

Job Types:
    - router: Document routing from Drive to departments
    - apbookeeper: Invoice processing (AP)
    - bankbookeeper: Bank transaction matching
    - onboarding: Client onboarding (COA setup)

Author: Migration Agent
Created: 2026-01-25
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import aiohttp

from ..firebase_providers import get_firebase_management, get_firebase_realtime, FirebaseManagement
from ..realtime.contextual_publisher import (
    publish_routing_event,
    publish_invoices_event,
    publish_bank_event,
    publish_dashboard_event,
)
from ..realtime.pubsub_helper import publish_notification_new
from ..active_job_manager import ActiveJobManager
from ..redis_client import get_redis
from ..ws_events import WS_EVENTS
from ..ws_hub import hub
from ..domain_config import ListManager, get_domain_config

logger = logging.getLogger("job_actions_handler")


# ============================================
# CONSTANTS
# ============================================

# Job type to HTTP endpoint mapping
JOB_TYPE_CONFIG = {
    "router": {
        "process_endpoint": "/event-trigger",
        "stop_endpoint": "/stop_router",
        "local_port": 8080,
        "department": "Router",
        "domain": "routing",
        "approval_prefix": "router_",
    },
    "apbookeeper": {
        "process_endpoint": "/apbookeeper-event-trigger",
        "stop_endpoint": "/stop_apbookeeper",
        "local_port": 8081,
        "department": "APbookeeper",
        "domain": "invoices",
        "approval_prefix": "ap_",
    },
    "bankbookeeper": {
        "process_endpoint": "/banker-event-trigger",
        "stop_endpoint": "/stop_banker",
        "local_port": 8082,
        "department": "Bankbookeeper",
        "domain": "bank",
        "approval_prefix": "bank_",
    },
    "onboarding": {
        "process_endpoint": "/onboarding_manager_agent",
        "stop_endpoint": "/stop-onboarding",
        "local_port": 8080,           # Same service as router (klk_router)
        "department": "Onboarding",
        "domain": "onboarding",       # No business cache for onboarding
        "approval_prefix": "onboarding_",
    },
}

# Mapping from job_type to active_jobs collection name
ACTIVE_JOB_TYPE_MAP = {
    "router": "router",
    "apbookeeper": "apbookeeper",
    "bankbookeeper": "banker",
    "onboarding": "onboarding",
}

# Environment URLs
DOCKER_URL = "http://localhost:8080"
AWS_URL = "http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com"
LOCAL_URL_BASE = "http://127.0.0.1"


def _get_base_url(source: Optional[str] = None, job_type: str = "router") -> str:
    """Get the base URL for HTTP requests based on source environment."""
    if source is None:
        source = os.environ.get("PINNOKIO_SOURCE", "aws")

    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])

    if source == "docker":
        return DOCKER_URL
    elif source == "aws" or source == "ecs":
        return AWS_URL
    elif source == "local":
        return f"{LOCAL_URL_BASE}:{config['local_port']}"
    else:
        return AWS_URL


# ============================================
# LIST CHANGE HELPERS (ListManager Integration)
# ============================================


async def _apply_optimistic_list_change(
    uid: str,
    job_type: str,
    document_ids: List[str],
    company_data: Dict[str, Any],
    action: str,
    extra_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Apply optimistic list change using the centralized ListManager.

    This function:
    1. Gets the business cache for the domain
    2. Uses ListManager to move items between lists based on status
    3. Saves updated cache back to Redis
    4. Broadcasts item_update event via WebSocket

    Args:
        uid: Firebase user ID
        job_type: Type of job ('router', 'apbookeeper', 'bankbookeeper')
        document_ids: List of document IDs being processed
        company_data: Company context with company_id
        action: Action being performed ('process', 'stop', etc.)
        extra_data: Optional extra fields to set on moved items (e.g. {"batch_id": "..."})

    Returns:
        Dict with change result or None if failed
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    domain = config["domain"]
    company_id = company_data.get("company_id", "")

    logger.info(
        f"[JOB_ACTIONS] _apply_optimistic_list_change START - "
        f"domain={domain} action={action} items={len(document_ids)}"
    )

    try:
        # Get domain configuration
        domain_config = get_domain_config(domain)
        if not domain_config:
            logger.warning(f"[JOB_ACTIONS] No domain config for: {domain}")
            return None

        # Check if action is optimistic
        if not domain_config.is_optimistic(action):
            logger.debug(f"[JOB_ACTIONS] Action '{action}' is pessimistic, skipping optimistic update")
            return None

        # Get initial status for this action
        initial_status = domain_config.get_initial_status(action)
        if not initial_status:
            logger.warning(f"[JOB_ACTIONS] No initial status for action: {action}")
            return None

        # Invalidate page_state FIRST to prevent race condition:
        # A concurrent page.restore_state between our optimistic broadcast
        # and cache save would serve stale data.
        try:
            from app.wrappers.page_state_manager import get_page_state_manager
            page_manager = get_page_state_manager()
            page_manager.invalidate_page_state(
                uid=uid,
                company_id=company_id,
                page=domain,
            )
            logger.debug(f"[JOB_ACTIONS] page_state cache PRE-invalidated for page={domain}")
        except Exception as ps_err:
            logger.warning(f"[JOB_ACTIONS] Failed to pre-invalidate page_state: {ps_err}")

        # Get business cache
        redis = get_redis()
        cache_key = f"business:{uid}:{company_id}:{domain}"
        cached = redis.get(cache_key)

        if not cached:
            logger.warning(f"[JOB_ACTIONS] No business cache found: {cache_key}")
            return None

        cache_data = json.loads(cached if isinstance(cached, str) else cached.decode())

        # Get the documents section (handle both flat and nested structures)
        # Note: drive_cache_handlers uses "data" key, not "documents"
        documents_data = cache_data.get("data", cache_data.get("documents", cache_data))

        # Apply list change using ListManager
        result = ListManager.apply_status_change(
            domain=domain,
            cache_data=documents_data,
            item_ids=document_ids,
            new_status=initial_status,
            action=action,
            extra_data=extra_data,
        )

        if not result.success:
            logger.error(f"[JOB_ACTIONS] ListManager failed: {result.error}")
            return None

        logger.info(
            f"[JOB_ACTIONS] ListManager success: {len(result.items_moved)} items moved "
            f"from '{result.from_list}' to '{result.to_list}' (status={result.new_status})"
        )

        # Update the documents section in cache
        # Note: drive_cache_handlers uses "data" key
        if "data" in cache_data:
            cache_data["data"] = documents_data
            # Also update top-level counts if exists
            if "counts" in cache_data["data"]:
                cache_data["data"]["counts"] = result.counts
        elif "documents" in cache_data:
            cache_data["documents"] = documents_data
            cache_data["counts"] = result.counts
        else:
            cache_data = documents_data
            cache_data["counts"] = result.counts

        # Save updated cache back to Redis
        redis.setex(cache_key, 1800, json.dumps(cache_data))
        logger.info(f"[JOB_ACTIONS] Business cache updated: {cache_key}")

        # Broadcast item_update event
        ws_event_type = f"{domain}.item_update"
        await hub.broadcast(uid, {
            "type": ws_event_type,
            "payload": result.ws_payload.get("payload", {}) if result.ws_payload else {
                "action": "status_change",
                "trigger_action": action,
                "items": result.items_moved,
                "item_ids": document_ids,
                "from_list": result.from_list,
                "to_list": result.to_list,
                "new_status": result.new_status,
                "counts": result.counts,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        })
        logger.info(f"[JOB_ACTIONS] Broadcasted {ws_event_type} event")

        return {
            "success": True,
            "items_moved": len(result.items_moved),
            "from_list": result.from_list,
            "to_list": result.to_list,
            "new_status": result.new_status,
            "counts": result.counts,
        }

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] _apply_optimistic_list_change FAILED: {e}", exc_info=True)
        return None


# ============================================
# MAIN HANDLERS
# ============================================


async def handle_job_process(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
    source: str = "ui",
    traceability: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Process jobs - Optimistic approach.

    Triggers processing of selected documents via the appropriate jobbeur service.
    Frontend applies optimistic updates immediately, backend confirms/rejects.

    Args:
        uid: Firebase user ID
        job_type: Type of job ('router', 'apbookeeper', 'bankbookeeper')
        payload: Request payload with document_ids, instructions, etc.
        company_data: Company context (mandate_path, company_id, etc.)
        source: Origin of the request ('ui' or 'agentic')
        traceability: Optional traceability info (thread_key, execution_id, etc.)

    Returns:
        {
            "success": bool,
            "job_id": str,
            "processed_count": int,
            "message": str
        }
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    document_ids = payload.get("document_ids", [])
    company_id = company_data.get("company_id") or payload.get("company_id")
    mandate_path = company_data.get("mandate_path", "")
    company_name = company_data.get("company_name", company_id)

    # Fallback: si mandate_path est vide, le récupérer depuis Firebase
    if not mandate_path and uid and company_id:
        logger.warning(
            f"[JOB_ACTIONS] mandate_path EMPTY — fallback Firebase lookup uid={uid} company={company_id}"
        )
        try:
            firebase = get_firebase_management()
            mandates = await asyncio.to_thread(firebase.fetch_all_mandates_light, uid)
            for m in (mandates or []):
                m_ids = (m.get("contact_space_id"), m.get("id"), m.get("contact_space_name"))
                if company_id in m_ids:
                    mandate_path = m.get("mandate_path", "")
                    if mandate_path:
                        company_data["mandate_path"] = mandate_path
                        logger.info(f"[JOB_ACTIONS] → mandate_path recovered from Firebase: {mandate_path[:50]}...")
                        break
            if not mandate_path:
                logger.error(f"[JOB_ACTIONS] mandate_path STILL EMPTY after fallback — company_id={company_id} not found in mandates")
        except Exception as fb_err:
            logger.error(f"[JOB_ACTIONS] mandate_path fallback FAILED: {fb_err}")

    logger.info(
        f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
    )
    logger.info(
        f"[JOB_ACTIONS] handle_job_process START - "
        f"uid={uid} job_type={job_type} company_id={company_id}"
    )
    logger.info(
        f"[JOB_ACTIONS] → document_ids count={len(document_ids)} "
        f"first_ids={document_ids[:3] if document_ids else []}"
    )
    logger.info(
        f"[JOB_ACTIONS] → company_data: mandate_path={mandate_path[:50] if mandate_path else 'EMPTY'}..."
    )
    logger.info(
        f"[JOB_ACTIONS] → config: domain={config['domain']} endpoint={config['process_endpoint']}"
    )

    if not document_ids:
        return {
            "success": False,
            "error": "No documents selected",
            "code": "NO_DOCUMENTS",
        }

    try:
        # Generate batch identifiers
        batch_id = f"batch_{uuid.uuid4().hex[:10]}"
        aws_instance_id = f"aws_instance_id_{uuid.uuid4().hex[:8]}"
        pub_sub_id = f"klk_google_pubsub_id_{uuid.uuid4().hex[:8]}"

        # Check if jobs_data is already prebuilt (agentic source)
        if payload.get("jobs_data_prebuilt"):
            # Agentic source: jobs_data already resolved by the caller
            jobs_data = payload["jobs_data_prebuilt"]
            logger.info(f"[JOB_ACTIONS] → Using prebuilt jobs_data ({len(jobs_data)} items, source={source})")
        elif job_type == "bankbookeeper":
            # Banker-specific: build from transactions_data
            jobs_data = _build_banker_jobs_data(
                payload=payload, company_data=company_data,
            )
            logger.info(f"[JOB_ACTIONS] → Built banker jobs_data ({len(jobs_data)} jobs)")
        else:
            # Generic builder for Router/AP (from cache)
            documents_info = await _get_documents_from_cache(
                uid=uid,
                company_id=company_id,
                domain=config["domain"],
                document_ids=document_ids,
            )

            jobs_data = []
            document_instructions = payload.get("document_instructions", {})
            approval_states = payload.get("approval_states", {})
            workflow_states = payload.get("workflow_states", {})

            # Get defaults from company settings (fallback to safe defaults)
            default_approval = company_data.get("router_approval_required", False)
            default_workflow = company_data.get("router_automated_workflow", True)

            for doc_id in document_ids:
                doc_info = documents_info.get(doc_id, {})
                file_name = doc_info.get("file_name", doc_id)

                job_item = {
                    "file_name": str(file_name),
                    "drive_file_id": str(doc_id),
                    "job_id": str(doc_id),
                    "instructions": str(document_instructions.get(doc_id, "")),
                    "status": "to_route" if job_type == "router" else "to_process",
                    "approval_required": approval_states.get(doc_id, default_approval),
                    "automated_workflow": workflow_states.get(doc_id, default_workflow),
                }
                jobs_data.append(job_item)

        # log_communication_mode must be a valid GMS mode (google_chat, pinnokio, telegram)
        # If not explicitly set or invalid, inherit from communication_mode (or default to pinnokio)
        _valid_log_modes = ("google_chat", "pinnokio", "telegram")
        _comm_mode = company_data.get("communication_mode", "pinnokio")
        _log_mode = company_data.get("log_communication_mode", "")
        if _log_mode not in _valid_log_modes:
            _log_mode = _comm_mode if _comm_mode in _valid_log_modes else "pinnokio"

        # Build the jobbeur payload
        jobbeur_payload = {
            "collection_name": str(company_id),
            "jobs_data": jobs_data,
            "start_instructions": payload.get("general_instructions", payload.get("start_instructions", "")),
            "client_uuid": company_data.get("client_uuid", ""),
            "user_id": uid,
            "pub_sub_id": pub_sub_id,
            "mandates_path": mandate_path,
            "batch_id": batch_id,
            "settings": [
                {"communication_mode": _comm_mode},
                {"log_communication_mode": _log_mode},
                {"dms_system": company_data.get("dms_type", "odoo")},
            ],
        }

        # Override for onboarding: flat payload format expected by DF_ANALYSER
        if job_type == "onboarding":
            first_job = jobs_data[0] if jobs_data else {}
            jobbeur_payload = {
                "firebase_user_id": uid,
                "job_id": first_job.get("job_id", batch_id),
                "mandate_path": mandate_path,
                "mode": "onboarding",
                "setup_coa_type": first_job.get("setup_coa_type"),
                "erp_system": first_job.get("erp_system"),
                "context": first_job.get("initial_context_data", ""),
                "batch_id": batch_id,
                "collection_name": str(company_id),
            }
            if traceability:
                jobbeur_payload["traceability"] = traceability

        # Banker-specific extra fields
        if job_type == "bankbookeeper":
            jobbeur_payload["journal_name"] = payload.get("journal_name", "")
            jobbeur_payload["proxy"] = payload.get("proxy", False)

        # Inject traceability if present (agentic source, non-onboarding)
        if traceability and job_type != "onboarding":
            jobbeur_payload["traceability"] = traceability

        logger.info(f"[JOB_ACTIONS] → Step 1: Building jobbeur payload...")
        logger.info(f"[JOB_ACTIONS] → jobbeur_payload: batch_id={batch_id} jobs_count={len(jobs_data)}")

        # Call the external jobbeur HTTP endpoint
        base_url = _get_base_url(job_type=job_type)
        process_url = f"{base_url}{config['process_endpoint']}"

        # ═══════════════════════════════════════════════════════════════════
        # DEBUG: Print full payload before sending to jobbeur
        # ═══════════════════════════════════════════════════════════════════
        logger.info(f"[JOB_ACTIONS] ┌─────────────────────────────────────────────────────────────")
        logger.info(f"[JOB_ACTIONS] │ PAYLOAD TO JOBBEUR - {process_url}")
        logger.info(f"[JOB_ACTIONS] ├─────────────────────────────────────────────────────────────")
        logger.info(f"[JOB_ACTIONS] │ collection_name: {jobbeur_payload.get('collection_name')}")
        logger.info(f"[JOB_ACTIONS] │ user_id: {jobbeur_payload.get('user_id')}")
        logger.info(f"[JOB_ACTIONS] │ client_uuid: {jobbeur_payload.get('client_uuid')}")
        logger.info(f"[JOB_ACTIONS] │ mandates_path: {jobbeur_payload.get('mandates_path')}")
        logger.info(f"[JOB_ACTIONS] │ batch_id: {jobbeur_payload.get('batch_id')}")
        logger.info(f"[JOB_ACTIONS] │ pub_sub_id: {jobbeur_payload.get('pub_sub_id')}")
        logger.info(f"[JOB_ACTIONS] │ start_instructions: {jobbeur_payload.get('start_instructions', '')[:100]}...")
        logger.info(f"[JOB_ACTIONS] │ settings: {jobbeur_payload.get('settings')}")
        logger.info(f"[JOB_ACTIONS] │ jobs_data count: {len(jobbeur_payload.get('jobs_data', []))}")
        for idx, job in enumerate(jobbeur_payload.get('jobs_data', [])[:5]):  # Limit to first 5
            logger.info(f"[JOB_ACTIONS] │   [{idx}] file_name={job.get('file_name')}")
            logger.info(f"[JOB_ACTIONS] │       drive_file_id={job.get('drive_file_id')}")
            logger.info(f"[JOB_ACTIONS] │       job_id={job.get('job_id')}")
            logger.info(f"[JOB_ACTIONS] │       status={job.get('status')}")
            logger.info(f"[JOB_ACTIONS] │       approval_required={job.get('approval_required')}")
            logger.info(f"[JOB_ACTIONS] │       automated_workflow={job.get('automated_workflow')}")
            logger.info(f"[JOB_ACTIONS] │       instructions={job.get('instructions', '')[:50]}...")
        if len(jobbeur_payload.get('jobs_data', [])) > 5:
            logger.info(f"[JOB_ACTIONS] │   ... and {len(jobbeur_payload.get('jobs_data', [])) - 5} more jobs")
        logger.info(f"[JOB_ACTIONS] └─────────────────────────────────────────────────────────────")

        # Also print full JSON for debugging
        import json as json_module
        logger.info(f"[JOB_ACTIONS] FULL PAYLOAD JSON:\n{json_module.dumps(jobbeur_payload, indent=2, default=str)}")

        # ═══════════════════════════════════════════════════════════════════
        # Step 1.5: Register in active_jobs (before dispatch)
        # This ensures the job is queued even if the worker is down.
        # ═══════════════════════════════════════════════════════════════════
        active_job_type = ACTIVE_JOB_TYPE_MAP.get(job_type, job_type)
        try:
            # Store full jobbeur_payload so workers polling active_jobs
            # get all required fields (mandates_path, collection_name, user_id, settings, etc.)
            reg_result = ActiveJobManager.register_job(
                mandate_path=mandate_path,
                job_data=jobbeur_payload,
                job_key=batch_id,
                job_type=active_job_type,
            )
            logger.info(
                f"[JOB_ACTIONS] → Step 1.5: active_jobs registered: "
                f"status={reg_result.get('status')} should_start={reg_result.get('should_start')} "
                f"position={reg_result.get('position_in_queue')}"
            )
        except Exception as reg_err:
            logger.warning(f"[JOB_ACTIONS] → Step 1.5: active_jobs registration failed: {reg_err}")
            # Non-blocking: continue with HTTP dispatch

        # ═══════════════════════════════════════════════════════════════════
        # Step 1.6: Ensure worker is running (ECS in PROD, subprocess in LOCAL)
        # ═══════════════════════════════════════════════════════════════════
        ecs_starting = False
        try:
            environment = os.environ.get("PINNOKIO_ENVIRONMENT", "PROD").upper()
            if environment == "LOCAL":
                from ..local_worker_manager import LocalWorkerManager
                worker_status = LocalWorkerManager.ensure_worker_running(job_type)
            else:
                from ..ecs_manager import ECSManager
                worker_status = ECSManager.ensure_worker_running(job_type)

            logger.info(f"[JOB_ACTIONS] → Step 1.6: Worker status={worker_status.get('status')} (env={environment})")

            if worker_status.get("status") in ("starting", "provisioning"):
                ecs_starting = True
        except Exception as ecs_err:
            logger.warning(f"[JOB_ACTIONS] → Step 1.6: Worker check failed: {ecs_err}")
            # Non-blocking: continue with HTTP dispatch attempt

        # ═══════════════════════════════════════════════════════════════════
        # Step 1.7: Check worker heartbeat (fast check before HTTP)
        # ═══════════════════════════════════════════════════════════════════
        skip_http = False
        if ecs_starting:
            logger.info("[JOB_ACTIONS] → Step 1.7: Worker starting up, skipping HTTP dispatch")
            skip_http = True
        else:
            try:
                redis = get_redis()
                hb_key = f"worker:{ACTIVE_JOB_TYPE_MAP.get(job_type, job_type)}:heartbeat"
                heartbeat = redis.get(hb_key)
                if not heartbeat:
                    logger.info(f"[JOB_ACTIONS] → Step 1.7: No heartbeat ({hb_key}), skipping HTTP dispatch")
                    skip_http = True
            except Exception as hb_err:
                logger.warning(f"[JOB_ACTIONS] → Step 1.7: Heartbeat check failed: {hb_err}")

        logger.info(f"[JOB_ACTIONS] → Step 2: Calling HTTP endpoint: {process_url}")

        # ═══════════════════════════════════════════════════════════════════
        # HTTP dispatch (active_jobs is the fallback if HTTP fails)
        # ═══════════════════════════════════════════════════════════════════
        dispatch_method = "http"
        job_id = batch_id

        if skip_http:
            dispatch_method = "ecs_starting" if ecs_starting else "active_jobs_pending"
            logger.info(f"[JOB_ACTIONS] → Step 2: HTTP dispatch SKIPPED (dispatch_method={dispatch_method})")
        else:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        process_url, json=jobbeur_payload, timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        status = response.status
                        try:
                            result = await response.json()
                        except Exception:
                            result = {"message": await response.text()}

                logger.info(f"[JOB_ACTIONS] → Step 3: HTTP response status={status}")

                if status in [200, 202]:
                    job_id = result.get("job_id", batch_id)
                    logger.info(f"[JOB_ACTIONS] → Step 3: HTTP success - job_id={job_id}")
                else:
                    # Non-2xx response: job is already in active_jobs, worker will pick up
                    logger.warning(
                        f"[JOB_ACTIONS] HTTP dispatch returned {status}. "
                        f"Job is in active_jobs, worker will pick up on next poll."
                    )
                    dispatch_method = "active_jobs_pending"

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    f"[JOB_ACTIONS] HTTP dispatch failed: {e}. "
                    f"Job is in active_jobs, worker will pick up on next poll."
                )
                dispatch_method = "active_jobs_pending"

        # ═══════════════════════════════════════════════════════════════════
        # Post-dispatch: notifications + cache (always, regardless of dispatch method)
        # The job WILL be processed (HTTP immediate or BRPOP on worker restart).
        # ═══════════════════════════════════════════════════════════════════
        logger.info(f"[JOB_ACTIONS] → Step 5: Creating Firebase notifications... (dispatch_method={dispatch_method})")
        notification_ids = await _create_batch_notifications(
            uid=uid,
            job_type=job_type,
            jobs_data=jobs_data,
            batch_id=batch_id,
            aws_instance_id=aws_instance_id,
            pub_sub_id=pub_sub_id,
            company_id=company_id,
            company_name=company_name,
        )
        logger.info(f"[JOB_ACTIONS] → Step 5: {len(notification_ids)} notifications created")

        # Step 5b: Persist to task_manager (source of truth for cache reload)
        logger.info(f"[JOB_ACTIONS] → Step 5b: Persisting to task_manager...")
        try:
            await _persist_jobs_to_task_manager(
                uid=uid,
                job_type=job_type,
                document_ids=document_ids,
                company_data=company_data,
                batch_id=batch_id,
                payload=payload,
                jobs_data=jobs_data,
            )
            logger.info(f"[JOB_ACTIONS] → Step 5b: task_manager persisted for {len(document_ids)} items")
        except Exception as tm_err:
            logger.warning(f"[JOB_ACTIONS] → Step 5b: task_manager persist FAILED: {tm_err}")
            # Non-bloquant: le worker ecrira aussi dans task_manager

        # Step 6: Apply optimistic list change (move items to in_process)
        logger.info(f"[JOB_ACTIONS] → Step 6: Applying optimistic list change...")
        extra_data_for_move = None
        if job_type == "bankbookeeper":
            extra_data_for_move = {"batch_id": batch_id}

        list_change_result = await _apply_optimistic_list_change(
            uid=uid,
            job_type=job_type,
            document_ids=document_ids,
            company_data=company_data,
            action="process",
            extra_data=extra_data_for_move,
        )
        if list_change_result:
            logger.info(
                f"[JOB_ACTIONS] → Step 6: List change applied - "
                f"moved {list_change_result.get('items_moved', 0)} items "
                f"from '{list_change_result.get('from_list')}' to '{list_change_result.get('to_list')}'"
            )
        else:
            logger.warning(f"[JOB_ACTIONS] → Step 6: List change skipped or failed")

        logger.info(
            f"[JOB_ACTIONS] handle_job_process SUCCESS - "
            f"batch_id={batch_id} count={len(document_ids)} dispatch={dispatch_method}"
        )
        logger.info(
            f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
        )

        return {
            "success": True,
            "job_id": job_id,
            "batch_id": batch_id,
            "processed_count": len(document_ids),
            "dispatch_method": dispatch_method,
            "message": f"Processing started for {len(document_ids)} documents",
            "list_change": list_change_result,
        }

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Unexpected error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "code": "INTERNAL_ERROR",
        }


async def handle_job_stop(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Stop jobs - Pessimistic approach.

    Sends stop signal to the jobbeur service. Items remain in current state
    until external confirmation is received.

    Args:
        uid: Firebase user ID
        job_type: Type of job ('router', 'apbookeeper', 'bankbookeeper')
        payload: Request payload with job_id(s)
        company_data: Company context

    Returns:
        {
            "success": bool,
            "stopped_jobs": list,
            "message": str
        }
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    job_ids = payload.get("job_ids", [])
    single_job_id = payload.get("job_id")
    company_id = company_data.get("company_id") or payload.get("company_id")
    mandate_path = company_data.get("mandate_path", "")

    # Normalize to list
    if single_job_id and not job_ids:
        job_ids = [single_job_id]

    logger.info(
        f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
    )
    logger.info(
        f"[JOB_ACTIONS] handle_job_stop START - "
        f"uid={uid} job_type={job_type} company_id={company_id}"
    )
    logger.info(
        f"[JOB_ACTIONS] → job_ids={job_ids}"
    )
    logger.info(
        f"[JOB_ACTIONS] → mandate_path={mandate_path[:50] if mandate_path else 'None'}..."
    )

    if not job_ids:
        logger.warning(f"[JOB_ACTIONS] handle_job_stop ABORTED - No jobs specified")
        return {
            "success": False,
            "error": "No jobs specified",
            "code": "NO_JOBS",
        }

    try:
        # Write stop_requested directly in active_jobs (no HTTP to worker needed)
        active_job_type = ACTIVE_JOB_TYPE_MAP.get(job_type, job_type)
        transaction_ids = payload.get("transaction_ids")
        job_ids_to_stop = payload.get("job_ids_to_stop")

        logger.info(
            f"[JOB_ACTIONS] → Step 1: Writing stop_requested to active_jobs "
            f"(type={active_job_type}, {len(job_ids)} jobs)"
        )

        stop_results = []
        for jid in job_ids:
            stop_result = ActiveJobManager.request_stop(
                mandate_path=mandate_path,
                job_type=active_job_type,
                job_key=jid,
                job_ids_to_stop=job_ids_to_stop,
                transaction_ids=transaction_ids,
            )
            stop_results.append(stop_result)

        success_count = sum(1 for r in stop_results if r.get("success"))

        # Step 2: Send synthetic stopped notifications for pending jobs removed directly
        synthetic_count = 0
        for stop_result in stop_results:
            for stopped_job_id in stop_result.get("synthetic_stops", []):
                try:
                    await _send_synthetic_stopped_notification(
                        uid=uid,
                        job_type=job_type,
                        job_id=stopped_job_id,
                        mandate_path=mandate_path,
                        company_data=company_data,
                    )
                    synthetic_count += 1
                except Exception as synth_err:
                    logger.warning(
                        f"[JOB_ACTIONS] Synthetic stop notification failed for {stopped_job_id}: {synth_err}"
                    )

        if synthetic_count > 0:
            logger.info(
                f"[JOB_ACTIONS] → Step 2: Sent {synthetic_count} synthetic stopped notifications"
            )

        logger.info(
            f"[JOB_ACTIONS] handle_job_stop SUCCESS - "
            f"stopped={success_count}/{len(job_ids)} synthetic={synthetic_count}"
        )
        logger.info(
            f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
        )

        return {
            "success": True,
            "stopped_jobs": job_ids,
            "synthetic_stops": synthetic_count,
            "message": f"Stop requested for {success_count}/{len(job_ids)} jobs via active_jobs",
        }

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Stop failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "code": "STOP_ERROR",
        }


async def handle_job_restart(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Restart a job - Clean state and move back to to_process.

    Cleans up the failed/stuck job state and moves the item back to to_process.

    Complete restart workflow:
    1. Delete Chroma embeddings for this job
    2. Delete approval_pendinglist entry (before task_manager reset)
    3. Reset task_manager fields + delete events subcollection
    4. Update notification status to to_process
    5. Erase RTDB job_chats
    6. Delete associated notifications
    7. Move item to to_process in business cache

    Args:
        uid: Firebase user ID
        job_type: Type of job
        payload: Request payload with job_id
        company_data: Company context

    Returns:
        {
            "success": bool,
            "job_id": str,
            "message": str
        }
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    job_id = payload.get("job_id")
    company_id = company_data.get("company_id") or payload.get("company_id")
    mandate_path = company_data.get("mandate_path", "")

    logger.info(
        f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
    )
    logger.info(
        f"[JOB_ACTIONS] handle_job_restart START - "
        f"uid={uid} job_type={job_type} company_id={company_id}"
    )
    logger.info(f"[JOB_ACTIONS] → job_id={job_id}")
    logger.info(f"[JOB_ACTIONS] → mandate_path={mandate_path[:50] if mandate_path else 'None'}...")

    if not job_id:
        logger.warning(f"[JOB_ACTIONS] handle_job_restart ABORTED - No job_id provided")
        return {
            "success": False,
            "error": "No job_id provided",
            "code": "MISSING_JOB_ID",
        }

    try:
        firebase = get_firebase_management()

        # 1. Delete Chroma embeddings for this job (if applicable)
        logger.info(f"[JOB_ACTIONS] → Step 1: Deleting Chroma embeddings...")
        try:
            from ..chroma_vector_service import get_chroma_vector_service
            chroma = get_chroma_vector_service()
            if chroma:
                result = await asyncio.to_thread(
                    chroma.delete_documents,
                    company_id,
                    {"job_id": job_id},
                )
                if result.get("success"):
                    logger.info(f"[JOB_ACTIONS] → Step 1: Chroma embeddings DELETED for job_id={job_id}")
                else:
                    logger.warning(f"[JOB_ACTIONS] → Step 1: Chroma deletion FAILED: {result.get('error', 'unknown error')}")
            else:
                logger.info(f"[JOB_ACTIONS] → Step 1: Chroma service not available, skipping")
        except Exception as chroma_err:
            logger.warning(f"[JOB_ACTIONS] → Step 1: Chroma deletion SKIPPED: {chroma_err}")

        # 2. Delete approval_pendinglist entry (before task_manager reset)
        logger.info(f"[JOB_ACTIONS] → Step 2: Deleting approval_pendinglist entry...")
        try:
            file_name = payload.get("file_name", "")
            await _delete_approval_pendinglist(
                uid, job_type, [(job_id, file_name)], company_data
            )
            # Invalidate approvals cache so frontend picks up the deletion
            try:
                redis = get_redis()
                redis.delete(f"approvals:{company_id}")
                logger.debug(f"[JOB_ACTIONS] → Step 2: approvals cache invalidated for company_id={company_id}")
            except Exception:
                pass  # Non-blocking
            logger.info(f"[JOB_ACTIONS] → Step 2: approval_pendinglist entry DELETED for job_id={job_id}")
        except Exception as approval_err:
            logger.warning(f"[JOB_ACTIONS] → Step 2: approval_pendinglist deletion SKIPPED: {approval_err}")

        # 3. Reset task_manager fields + delete events subcollection
        logger.info(f"[JOB_ACTIONS] → Step 3: Resetting task_manager job data...")
        try:
            await asyncio.to_thread(firebase.restart_job, uid, job_id)
            logger.info(f"[JOB_ACTIONS] → Step 3: task_manager fields RESET for job_id={job_id}")
        except Exception as fb_err:
            logger.warning(f"[JOB_ACTIONS] → Step 3: task_manager reset SKIPPED: {fb_err}")

        # 4. Update notification status to to_process
        logger.info(f"[JOB_ACTIONS] → Step 4: Resetting notification status...")
        try:
            await asyncio.to_thread(
                firebase.update_job_status,
                uid,
                job_id,
                "to_process",
                {"reset_at": datetime.now(timezone.utc).isoformat(), "reset_by": "user"},
            )
            logger.info(f"[JOB_ACTIONS] → Step 4: Notification status RESET for job_id={job_id}")
        except Exception as fb_err:
            logger.warning(f"[JOB_ACTIONS] → Step 4: Notification status update SKIPPED: {fb_err}")

        # 5. Erase RTDB job_chats for this job
        logger.info(f"[JOB_ACTIONS] → Step 5: Erasing RTDB job_chats...")
        try:
            await _delete_job_chat_threads(uid, company_id, job_id)
            logger.info(f"[JOB_ACTIONS] → Step 5: RTDB job_chats erased")
        except Exception as rtdb_err:
            logger.warning(f"[JOB_ACTIONS] → Step 5: RTDB erase SKIPPED: {rtdb_err}")

        # 6. Delete associated notifications for this job
        logger.info(f"[JOB_ACTIONS] → Step 6: Deleting job notifications...")
        await _delete_job_notifications(uid, mandate_path, job_id)
        logger.info(f"[JOB_ACTIONS] → Step 6: Notifications cleanup completed")

        # 7. Move item to to_process in business cache
        logger.info(f"[JOB_ACTIONS] → Step 7: Moving item to to_process in cache...")
        try:
            await _move_to_list_in_cache(
                uid, job_type, company_data, job_id, "to_process"
            )
            logger.info(f"[JOB_ACTIONS] → Step 7: Item moved to to_process in cache ✓")
        except Exception as cache_err:
            logger.warning(f"[JOB_ACTIONS] → Step 7: Cache update SKIPPED: {cache_err}")

        logger.info(
            f"[JOB_ACTIONS] handle_job_restart SUCCESS - job_id={job_id}"
        )
        logger.info(
            f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
        )

        return {
            "success": True,
            "job_id": job_id,
            "message": f"Job {job_id} has been reset for reprocessing",
        }

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Restart failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "code": "RESTART_ERROR",
        }


async def handle_job_delete(
    uid: str,
    job_type: str,
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Delete jobs - Full workflow with Drive move (for Router).

    Complete deletion workflow:
    1. Delete Firebase notifications related to jobs
    2. Delete RTDB chat threads
    3. Purge task_manager document (preserves billing)
    4. Delete approval_pendinglist entries
    5. For Router: Move files back to Drive input folder
    6. Update business cache
    7. For Router: Add files back to routing to_do
    8. For Invoice/AP: Cross-update routing cache (doc returns to Router to_do)

    Args:
        uid: Firebase user ID
        job_type: Type of job
        payload: Request payload with job_ids and file info
        company_data: Company context

    Returns:
        {
            "success": bool,
            "deleted_jobs": list,
            "moved_to_todo": list (Router only),
            "message": str
        }
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    job_ids = payload.get("job_ids", [])
    company_id = company_data.get("company_id") or payload.get("company_id")
    mandate_path = company_data.get("mandate_path", "")

    logger.info(
        f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
    )
    logger.info(
        f"[JOB_ACTIONS] handle_job_delete START - "
        f"uid={uid} job_type={job_type} company_id={company_id}"
    )
    logger.info(f"[JOB_ACTIONS] → job_ids count={len(job_ids)} first_ids={job_ids[:3] if job_ids else []}")
    logger.info(f"[JOB_ACTIONS] → mandate_path={mandate_path[:50] if mandate_path else 'None'}...")
    logger.info(f"[JOB_ACTIONS] → domain={config['domain']} (Router will move to Drive)")

    if not job_ids:
        logger.warning(f"[JOB_ACTIONS] handle_job_delete ABORTED - No jobs specified")
        return {
            "success": False,
            "error": "No jobs specified",
            "code": "NO_JOBS",
        }

    try:
        firebase = get_firebase_management()
        deleted_jobs = []
        failed_jobs = []
        moved_to_todo = []

        # Normalize payload to get job_id and file_name pairs
        job_file_pairs = _normalize_delete_payload(payload, job_type)
        logger.info(f"[JOB_ACTIONS] → Normalized to {len(job_file_pairs)} job/file pairs")

        for idx, (job_id, file_name) in enumerate(job_file_pairs, 1):
            logger.info(f"[JOB_ACTIONS] → Processing job {idx}/{len(job_file_pairs)}: job_id={job_id}")
            try:
                # 0. For AP: resolve drive_file_id BEFORE purge (Step 3 will erase the data)
                drive_file_id = ""
                resolved_name = ""
                if job_type == "apbookeeper":
                    drive_file_id, resolved_name = await _resolve_ap_drive_file_id(uid, job_id)
                    logger.info(f"[JOB_ACTIONS] →   Step 0: Resolved drive_file_id={drive_file_id or '(empty)'} for {job_id}")

                # 1. Delete notifications for this job
                logger.debug(f"[JOB_ACTIONS] →   Step 1: Deleting notifications...")
                await _delete_job_notifications(uid, mandate_path, job_id)

                # 2. Delete RTDB chat threads
                logger.debug(f"[JOB_ACTIONS] →   Step 2: Deleting chat threads...")
                await _delete_job_chat_threads(uid, company_id, job_id)

                # 3. Purge task_manager document (preserves billing if present)
                logger.debug(f"[JOB_ACTIONS] →   Step 3: Purging task_manager document...")
                await asyncio.to_thread(
                    firebase.delete_items_by_job_id,
                    uid, [job_id], mandate_path
                )

                # 4. Delete approval_pendinglist entries
                logger.debug(f"[JOB_ACTIONS] →   Step 4: Deleting approval_pendinglist...")
                await _delete_approval_pendinglist(
                    uid, job_type, [(job_id, file_name)], company_data
                )

                # 5. For Router: Move file back to Drive input folder
                if job_type == "router":
                    logger.info(f"[JOB_ACTIONS] →   Step 5: Moving file to Drive input: {file_name or job_id}")
                    move_success = await _move_file_to_drive_input(
                        uid, company_data, job_id, file_name
                    )
                    if move_success:
                        moved_to_todo.append({"job_id": job_id, "file_name": file_name})
                        logger.info(f"[JOB_ACTIONS] →   Step 5: File moved to Drive ✓")
                    else:
                        logger.warning(f"[JOB_ACTIONS] →   Step 5: Drive move FAILED for {job_id}")

                # 5. For AP: Move file to Drive input, purge Router doc
                elif job_type == "apbookeeper":
                    if drive_file_id:
                        # 5b: Move file back to Drive input folder
                        logger.info(f"[JOB_ACTIONS] →   Step 5b: Moving file to Drive input: {resolved_name or drive_file_id}")
                        move_success = await _move_file_to_drive_input(
                            uid, company_data, drive_file_id, resolved_name
                        )
                        if move_success:
                            moved_to_todo.append({
                                "job_id": drive_file_id,
                                "file_name": resolved_name,
                                "drive_file_id": drive_file_id,
                            })
                            logger.info(f"[JOB_ACTIONS] →   Step 5b: File moved to Drive ✓")
                        else:
                            logger.warning(f"[JOB_ACTIONS] →   Step 5b: Drive move FAILED for {drive_file_id}")

                        # 5c: Reset Router task_manager/{drive_file_id} to status=to_process
                        #     NE PAS purger : le klk_router vérifie l'existence de klk_job_id
                        #     dans ce document pour éviter la duplication.
                        try:
                            router_doc_ref = firebase.db.collection(
                                f"clients/{uid}/task_manager"
                            ).document(drive_file_id)
                            router_doc = await asyncio.to_thread(router_doc_ref.get)
                            if router_doc.exists:
                                await asyncio.to_thread(
                                    router_doc_ref.update,
                                    {"status": "to_process"}
                                )
                                # Enrichir moved_to_todo avec les champs du doc Router
                                router_data = router_doc.to_dict() or {}
                                moved_to_todo[-1]["klk_job_id"] = router_data.get("klk_job_id", "")
                                moved_to_todo[-1]["collection_id"] = router_data.get("collection_id", "")
                                moved_to_todo[-1]["mandate_path"] = router_data.get("mandate_path", "")
                                logger.info(
                                    f"[JOB_ACTIONS] →   Step 5c: Router task_manager/{drive_file_id} "
                                    f"reset to to_process (klk_job_id={router_data.get('klk_job_id', 'N/A')}) ✓"
                                )
                            else:
                                logger.info(f"[JOB_ACTIONS] →   Step 5c: Router doc {drive_file_id} not found, skip")
                        except Exception as reset_err:
                            logger.warning(f"[JOB_ACTIONS] →   Step 5c: Router reset skipped: {reset_err}")
                    else:
                        logger.warning(f"[JOB_ACTIONS] →   Step 5: No drive_file_id found for AP job {job_id}, skipping Drive move")

                deleted_jobs.append(job_id)
                logger.info(f"[JOB_ACTIONS] →   Job {job_id} DELETE completed ✓")

            except Exception as job_err:
                logger.error(
                    f"[JOB_ACTIONS] →   Job {job_id} DELETE FAILED: {job_err}"
                )
                failed_jobs.append({"job_id": job_id, "error": str(job_err)})

        # 6. Update business cache
        logger.info(f"[JOB_ACTIONS] → Step 6: Updating business cache...")
        if deleted_jobs:
            await _update_cache_after_delete(
                uid, job_type, company_data, job_file_pairs
            )
            logger.info(f"[JOB_ACTIONS] → Step 6: Cache updated for {len(deleted_jobs)} deleted jobs")

        # 6b. Remove deleted jobs from billing_history cache + notify dashboard
        if deleted_jobs:
            logger.info(f"[JOB_ACTIONS] → Step 6b: Updating billing_history cache...")
            removed_billing = await _remove_from_billing_history_cache(
                uid, company_id, deleted_jobs
            )
            if removed_billing > 0:
                try:
                    await hub.broadcast(uid, {
                        "type": WS_EVENTS.DASHBOARD.BILLING_ITEM_UPDATE,
                        "payload": {
                            "action": "delete",
                            "item_ids": deleted_jobs,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    })
                    logger.info(f"[JOB_ACTIONS] → Step 6b: Removed {removed_billing} items from billing_history, WSS broadcasted")
                except Exception as billing_broadcast_err:
                    logger.warning(f"[JOB_ACTIONS] → Step 6b: Billing WSS broadcast failed: {billing_broadcast_err}")
            else:
                logger.info(f"[JOB_ACTIONS] → Step 6b: No billing_history items to remove")

        # 7. If Router and files were moved, add to routing to_do
        if job_type == "router" and moved_to_todo:
            logger.info(f"[JOB_ACTIONS] → Step 7: Adding {len(moved_to_todo)} items to routing to_do...")
            await _add_to_routing_todo(uid, company_data, moved_to_todo)
            logger.info(f"[JOB_ACTIONS] → Step 7: Items added to routing to_do ✓")

        # 8. If Invoice/AP deleted, also add files back to routing to_do
        #    AND broadcast routing.item_update so the Router frontend store is updated
        if job_type == "apbookeeper" and moved_to_todo:
            logger.info(f"[JOB_ACTIONS] → Step 8: Cross-updating routing cache for AP delete...")
            await _add_to_routing_todo(uid, company_data, moved_to_todo)
            logger.info(f"[JOB_ACTIONS] → Step 8: {len(moved_to_todo)} items added to routing to_do ✓")

            # 8b: Broadcast routing.item_update (cross-domain WSS notification)
            try:
                redis_r = get_redis()
                routing_cache_key = f"business:{uid}:{company_id}:routing"
                routing_cached = redis_r.get(routing_cache_key)
                routing_counts = {}
                if routing_cached:
                    rc_data = json.loads(routing_cached if isinstance(routing_cached, str) else routing_cached.decode())
                    rc_inner = rc_data.get("data", rc_data) if "cache_version" in rc_data else rc_data
                    routing_counts = rc_inner.get("counts", {})

                await hub.broadcast(uid, {
                    "type": "routing.item_update",
                    "payload": {
                        "action": "add",
                        "trigger_action": "delete_cross_domain",
                        "items": moved_to_todo,
                        "item_ids": [item.get("drive_file_id") or item.get("job_id") for item in moved_to_todo],
                        "to_list": "to_process",
                        "new_status": "to_process",
                        "counts": routing_counts,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                })
                logger.info(f"[JOB_ACTIONS] → Step 8b: Broadcasted routing.item_update (add {len(moved_to_todo)} items)")
            except Exception as routing_broadcast_err:
                logger.warning(f"[JOB_ACTIONS] → Step 8b: Routing broadcast failed: {routing_broadcast_err}")

        # 9. Broadcast consolidated item_update (pessimistic: after all confirmations)
        if deleted_jobs:
            logger.info(f"[JOB_ACTIONS] → Step 9: Broadcasting item_update for delete...")
            try:
                redis = get_redis()
                cache_key = f"business:{uid}:{company_id}:{config['domain']}"
                cached = redis.get(cache_key)
                counts = {}
                if cached:
                    cache_data = json.loads(cached if isinstance(cached, str) else cached.decode())
                    counts = cache_data.get("counts", {})

                # Determine source and target lists
                # Router delete: items from processed/pending → to_process (Drive confirmed)
                # Other domains: items removed from their list
                to_list = "to_process" if moved_to_todo else None
                new_status = "to_process" if moved_to_todo else None

                ws_event_type = f"{config['domain']}.item_update"
                await hub.broadcast(uid, {
                    "type": ws_event_type,
                    "payload": {
                        "action": "delete",
                        "trigger_action": "delete",
                        "items": moved_to_todo if moved_to_todo else [],
                        "item_ids": deleted_jobs,
                        "from_list": "processed",
                        "to_list": to_list,
                        "new_status": new_status,
                        "counts": counts,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                })
                logger.info(
                    f"[JOB_ACTIONS] → Step 9: Broadcasted {ws_event_type} "
                    f"(deleted={len(deleted_jobs)}, moved_to_todo={len(moved_to_todo)})"
                )
            except Exception as broadcast_err:
                logger.warning(f"[JOB_ACTIONS] → Step 9: Broadcast failed: {broadcast_err}")

        logger.info(
            f"[JOB_ACTIONS] handle_job_delete {'SUCCESS' if not failed_jobs else 'PARTIAL'} - "
            f"deleted={len(deleted_jobs)} failed={len(failed_jobs)} moved_to_todo={len(moved_to_todo)}"
        )
        logger.info(
            f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
        )

        result = {
            "success": len(deleted_jobs) > 0,
            "deleted_jobs": deleted_jobs,
            "moved_to_todo": [item["file_name"] for item in moved_to_todo],
            "message": f"Deleted {len(deleted_jobs)} jobs",
        }

        # Report partial failures so frontend can rollback selectively
        if failed_jobs:
            result["failed_jobs"] = failed_jobs
            result["message"] = (
                f"Deleted {len(deleted_jobs)} jobs, "
                f"{len(failed_jobs)} failed: {', '.join(f['job_id'] for f in failed_jobs)}"
            )

        return result

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Delete failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "code": "DELETE_ERROR",
        }


# ============================================
# DOCUMENT & BATCH HELPERS
# ============================================


def _build_banker_jobs_data(
    payload: Dict[str, Any],
    company_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Build banker-specific jobs_data from payload.

    Banker payload uses transactions (not files). Each job groups transactions
    by bank account with approval settings.

    Args:
        payload: Request payload with transactions_data or jobs_data
        company_data: Company context with banker workflow params

    Returns:
        List of banker job dicts with bank_account, transactions, etc.
    """
    # If jobs_data already provided in correct banker format, use it directly
    if payload.get("jobs_data"):
        return payload["jobs_data"]

    # Build from transactions_data
    transactions_data = payload.get("transactions_data", [])
    bank_account = payload.get("bank_account", "")
    bank_account_id = payload.get("bank_account_id", "")

    # Get approval settings from company_data
    banker_params = company_data.get("workflow_params", {}).get("Banker_param", {})
    approval_required = banker_params.get("banker_approval_required", False)
    approval_threshold = banker_params.get("banker_approval_thresholdworkflow", "95")

    jobs_data = [{
        "bank_account": bank_account,
        "bank_account_id": bank_account_id,
        "transactions": transactions_data,
        "instructions": payload.get("instructions", ""),
        "banker_approval_required": approval_required,
        "banker_approval_thresholdworkflow": str(approval_threshold),
    }]

    return jobs_data


async def _get_documents_from_cache(
    uid: str,
    company_id: str,
    domain: str,
    document_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Get document information from business cache.

    Retrieves file_name and other details for each document ID
    to build the jobs_data payload.

    Args:
        uid: Firebase user ID
        company_id: Company ID
        domain: Domain name (routing, invoices, bank)
        document_ids: List of document IDs to look up

    Returns:
        Dict mapping document_id -> document info
    """
    result = {}

    try:
        redis = get_redis()
        cache_key = f"business:{uid}:{company_id}:{domain}"
        cached = redis.get(cache_key)

        if not cached:
            logger.warning(f"[JOB_ACTIONS] No cache found for document lookup: {cache_key}")
            return result

        cache_data = json.loads(cached if isinstance(cached, str) else cached.decode())

        # Get documents data (handle nested structure)
        documents_data = cache_data.get("data", cache_data.get("documents", cache_data))

        # Search all lists for the documents
        list_names = ["to_process", "in_process", "pending", "processed"]

        for list_name in list_names:
            items = documents_data.get(list_name, [])
            for item in items:
                item_id = item.get("id") or item.get("job_id")
                if item_id and item_id in document_ids:
                    result[item_id] = {
                        "file_name": item.get("file_name", item_id),
                        "status": item.get("status", ""),
                        "client": item.get("client", ""),
                        "created_time": item.get("created_time", ""),
                        "uri_file_link": item.get("uri_file_link", ""),
                    }

        logger.info(f"[JOB_ACTIONS] Retrieved {len(result)}/{len(document_ids)} documents from cache")

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Error getting documents from cache: {e}")

    return result


async def _create_batch_notifications(
    uid: str,
    job_type: str,
    jobs_data: List[Dict[str, Any]],
    batch_id: str,
    aws_instance_id: str,
    pub_sub_id: str,
    company_id: str,
    company_name: str,
) -> List[str]:
    """
    Create Firebase notifications for each document in a batch.

    Creates one notification per document with the correct format
    expected by the frontend notification store.

    Args:
        uid: Firebase user ID
        job_type: Type of job ('router', 'apbookeeper', 'bankbookeeper')
        jobs_data: List of job items with file_name, job_id, etc.
        batch_id: Batch ID for grouping notifications
        aws_instance_id: AWS instance ID for tracking
        pub_sub_id: Google Pub/Sub ID (for router)
        company_id: Company ID
        company_name: Company name

    Returns:
        List of created notification IDs
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    notification_ids = []
    batch_total = len(jobs_data)

    try:
        firebase = get_firebase_management()
        notifications_path = f"clients/{uid}/notifications"

        for index, job_item in enumerate(jobs_data, start=1):
            # Build notification depending on job_type
            if job_type == "onboarding":
                notification_firebase = {
                    "function_name": config["department"],  # "Onboarding"
                    "aws_instance_id": aws_instance_id,
                    "job_id": batch_id,
                    "batch_id": batch_id,
                    "status": "in queue",
                    "read": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "collection_id": company_id,
                    "collection_name": company_name,
                    "batch_index": 1,
                    "batch_total": 1,
                }
                upsert_key = "job_id"
                display_name = "Onboarding"
            elif job_type == "bankbookeeper":
                # Banker: notification references the batch + transactions
                notification_firebase = {
                    "function_name": config["department"],
                    "aws_instance_id": aws_instance_id,
                    "job_id": batch_id,
                    "batch_id": batch_id,
                    "bank_account": job_item.get("bank_account", ""),
                    "bank_account_id": job_item.get("bank_account_id", ""),
                    "transactions": job_item.get("transactions", []),
                    "status": "in queue",
                    "read": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "collection_id": company_id,
                    "collection_name": company_name,
                    "batch_index": index,
                    "batch_total": batch_total,
                }
                upsert_key = "job_id"  # Banker uses job_id for upsert
                display_name = job_item.get("bank_account", batch_id)
            else:
                # Router/AP: notification per file
                file_id = job_item.get("drive_file_id") or job_item.get("job_id", "")
                file_name = job_item.get("file_name", file_id)
                notification_firebase = {
                    "function_name": config["department"],
                    "aws_instance_id": aws_instance_id,
                    "file_id": file_id,
                    "job_id": file_id,
                    "file_name": file_name,
                    "journal_entries": "",
                    "status": "in queue",
                    "read": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "collection_id": company_id,
                    "collection_name": company_name,
                    "total_files": 1,
                    "batch_index": index,
                    "batch_total": batch_total,
                    "batch_id": batch_id,
                }
                upsert_key = "file_id"  # Router/AP uses file_id for upsert
                display_name = file_name

                # Add pub_sub_id for Router
                if job_type == "router":
                    notification_firebase["pub_sub_id"] = pub_sub_id

            # Write to Firebase using upsert (prevents duplicates on re-dispatch)
            try:
                if upsert_key == "job_id":
                    notification_id = await asyncio.to_thread(
                        firebase.add_or_update_job_by_job_id, notifications_path, notification_firebase
                    )
                else:
                    notification_id = await asyncio.to_thread(
                        firebase.add_or_update_job_by_file_id, notifications_path, notification_firebase
                    )
                notification_id = str(notification_id) if notification_id else None

                if notification_id:
                    notification_ids.append(notification_id)

                    # Build camelCase notification for WebSocket
                    if job_type == "onboarding":
                        notification_ws = {
                            "docId": notification_id,
                            "functionName": config["department"],
                            "awsInstanceId": aws_instance_id,
                            "jobId": batch_id,
                            "batchId": batch_id,
                            "status": "in_queue",
                            "read": False,
                            "timestamp": notification_firebase["timestamp"],
                            "collectionId": company_id,
                            "collectionName": company_name,
                            "batchIndex": 1,
                            "batchTotal": 1,
                            "message": "Onboarding - in queue",
                            "hasAdditionalInfo": False,
                        }
                    elif job_type == "bankbookeeper":
                        notification_ws = {
                            "docId": notification_id,
                            "functionName": config["department"],
                            "awsInstanceId": aws_instance_id,
                            "jobId": batch_id,
                            "batchId": batch_id,
                            "bankAccount": job_item.get("bank_account", ""),
                            "bankAccountId": job_item.get("bank_account_id", ""),
                            "transactions": job_item.get("transactions", []),
                            "status": "in_queue",
                            "read": False,
                            "timestamp": notification_firebase["timestamp"],
                            "collectionId": company_id,
                            "collectionName": company_name,
                            "batchIndex": index,
                            "batchTotal": batch_total,
                            "message": f"Bank reconciliation - in queue",
                            "hasAdditionalInfo": False,
                        }
                    else:
                        file_id = job_item.get("drive_file_id") or job_item.get("job_id", "")
                        file_name = job_item.get("file_name", file_id)
                        notification_ws = {
                            "docId": notification_id,
                            "functionName": config["department"],
                            "awsInstanceId": aws_instance_id,
                            "fileId": file_id,
                            "jobId": file_id,
                            "fileName": file_name,
                            "journalEntries": "",
                            "status": "in_queue",
                            "read": False,
                            "timestamp": notification_firebase["timestamp"],
                            "collectionId": company_id,
                            "collectionName": company_name,
                            "totalFiles": 1,
                            "batchIndex": index,
                            "batchTotal": batch_total,
                            "batchId": batch_id,
                            "message": f"{file_name} - in queue",
                            "hasAdditionalInfo": False,
                        }
                        if job_type == "router":
                            notification_ws["pubSubId"] = pub_sub_id

                    # Publish via WebSocket
                    await publish_notification_new(uid, notification_ws)

                    logger.debug(
                        f"[JOB_ACTIONS] Notification created - "
                        f"id={notification_id} item={display_name} batch_index={index}/{batch_total}"
                    )

            except Exception as notif_err:
                logger.error(f"[JOB_ACTIONS] Failed to create notification for {display_name}: {notif_err}")

        logger.info(
            f"[JOB_ACTIONS] Created {len(notification_ids)} notifications "
            f"for batch {batch_id}"
        )

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Error creating batch notifications: {e}", exc_info=True)

    return notification_ids


# ============================================
# NOTIFICATION HELPERS
# ============================================


async def create_and_publish_notification(
    uid: str,
    job_type: str,
    notification_data: Dict[str, Any],
) -> Optional[str]:
    """
    Create a Firebase notification and publish via WebSocket.

    Args:
        uid: Firebase user ID
        job_type: Type of job for department categorization
        notification_data: Notification content

    Returns:
        notification_id if created, None otherwise
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])

    try:
        firebase = get_firebase_management()

        # Build notification document
        notification = {
            "type": notification_data.get("type", "job_update"),
            "department": config["department"],
            "job_id": notification_data.get("job_id", ""),
            "status": notification_data.get("status", "info"),
            "message": notification_data.get("message", ""),
            "company_id": notification_data.get("company_id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "read": False,
            "functionName": config["department"],
        }

        # Write to Firebase (add_document returns the document ID as string)
        notification_path = f"clients/{uid}/notifications"
        notification_id = await asyncio.to_thread(
            firebase.add_document, notification_path, notification
        )
        # Ensure notification_id is a string
        notification_id = str(notification_id) if notification_id else None

        # Add docId to notification data
        notification["docId"] = notification_id

        # Publish via WebSocket (using pubsub_helper pattern)
        await publish_notification_new(uid, notification)

        logger.info(
            f"[JOB_ACTIONS] Notification created and published - "
            f"uid={uid} notification_id={notification_id}"
        )

        return notification_id

    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Failed to create notification: {e}")
        return None


async def _delete_job_notifications(
    uid: str,
    mandate_path: str,
    job_id: str,
) -> None:
    """Delete all notifications related to a specific job."""
    try:
        firebase = get_firebase_management()
        notifications_path = f"clients/{uid}/notifications"

        # List all notifications and filter by job_id
        all_notifications = await asyncio.to_thread(
            firebase.list_collection,
            notifications_path,
        )

        if all_notifications:
            for notif in all_notifications:
                notif_data = notif.get("data", notif)
                notif_job_id = notif_data.get("job_id", "")
                if notif_job_id == job_id:
                    doc_id = notif.get("docId") or notif.get("id") or notif.get("firebase_doc_id")
                    if doc_id:
                        try:
                            await asyncio.to_thread(
                                firebase.delete_document_recursive,
                                f"{notifications_path}/{doc_id}",
                            )
                            logger.debug(f"[JOB_ACTIONS] Deleted notification {doc_id}")
                        except Exception:
                            pass  # Continue even if single delete fails

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to delete notifications: {e}")


async def _delete_job_chat_threads(
    uid: str,
    company_id: str,
    job_id: str,
) -> None:
    """Delete RTDB chat threads associated with a job.

    Erases both job_chats and active_chats messages in RTDB:
      - Path: {company_id}/{job_id}/job_chats/messages
      - Path: {company_id}/{job_id}/active_chats/messages
    """
    try:
        rtdb = get_firebase_realtime()

        # Erase job_chats for this job
        await asyncio.to_thread(
            rtdb.erase_chat,
            company_id,  # space_code
            job_id,       # thread_key
            'job_chats',  # mode
        )
        logger.debug(f"[JOB_ACTIONS] RTDB job_chats erased for job_id={job_id}")

        # Erase active_chats for this job
        await asyncio.to_thread(
            rtdb.erase_chat,
            company_id,  # space_code
            job_id,       # thread_key
            'active_chats',  # mode
        )
        logger.debug(f"[JOB_ACTIONS] RTDB active_chats erased for job_id={job_id}")
    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to delete chat threads: {e}")


# ============================================
# SYNTHETIC STOP NOTIFICATION
# ============================================


async def _send_synthetic_stopped_notification(
    uid: str,
    job_type: str,
    job_id: str,
    mandate_path: str,
    company_data: Dict[str, Any],
) -> None:
    """
    Send a synthetic "stopped" notification for jobs removed directly from pending.

    These jobs never reached the worker, so no worker notification will come.
    We simulate the notification cascade:
    1. Update task_manager → status: "stopped"
    2. Update notifications → status: "stopped"
    3. Publish Redis → triggers normal cascade (cache update, WS push)
    """
    firebase = get_firebase_management()
    company_id = company_data.get("company_id", "")

    # 1. Update task_manager
    task_mgr_path = f"clients/{uid}/task_manager"
    try:
        await asyncio.to_thread(
            firebase.add_or_update_job_by_job_id,
            task_mgr_path,
            {
                "job_id": job_id,
                "status": "stopped",
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "stopped_by": "user_pending_cancel",
            },
        )
    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Synthetic stop: task_manager update failed for {job_id}: {e}")

    # 2. Update notification status
    notifications_path = f"clients/{uid}/notifications"
    try:
        await asyncio.to_thread(
            firebase.add_or_update_job_by_job_id,
            notifications_path,
            {
                "job_id": job_id,
                "status": "stopped",
            },
        )
    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Synthetic stop: notification update failed for {job_id}: {e}")

    # 3. Publish to Redis → triggers normal cascade (cache + WS)
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    try:
        redis = get_redis()
        redis_payload = json.dumps({
            "type": "task_manager_update",
            "job_id": job_id,
            "status": "stopped",
            "department": config["department"],
            "collection_id": company_id,
            "mandate_path": mandate_path,
            "data": {
                "status": "stopped",
                "stopped_by": "user_pending_cancel",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        redis.publish(f"user:{uid}/task_manager", redis_payload)
        logger.info(f"[JOB_ACTIONS] Synthetic stop published for {job_id}")
    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Synthetic stop: Redis publish failed for {job_id}: {e}")


# ============================================
# CACHE UPDATE HELPERS
# ============================================


def _normalize_delete_payload(
    payload: Dict[str, Any],
    job_type: str,
) -> List[Tuple[str, str]]:
    """
    Normalize payload to list of (job_id, file_name) tuples.

    Handles different payload formats:
    - job_ids: ["id1", "id2"] (simple list)
    - items: [{"job_id": "id1", "file_name": "f1"}, ...] (detailed list)
    """
    result = []

    # Format 1: Simple job_ids list
    job_ids = payload.get("job_ids", [])
    items = payload.get("items", [])

    if items:
        # Format 2: Detailed items with file names
        for item in items:
            job_id = item.get("job_id") or item.get("id", "")
            file_name = item.get("file_name", "")
            if job_id:
                result.append((job_id, file_name))
    elif job_ids:
        # Simple list - no file names
        for job_id in job_ids:
            result.append((job_id, ""))

    return result


async def _delete_approval_pendinglist(
    uid: str,
    job_type: str,
    job_file_pairs: List[Tuple[str, str]],
    company_data: Dict[str, Any],
) -> None:
    """
    Delete approval_pendinglist entries for deleted jobs.

    Each job type has a different prefix for approval entries.
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    mandate_path = company_data.get("mandate_path", "")

    if not mandate_path:
        return

    try:
        firebase = get_firebase_management()
        pending_path = f"{mandate_path}/approval_pendinglist"

        for job_id, file_name in job_file_pairs:
            # Build the approval entry ID based on job type prefix
            approval_id = f"{config['approval_prefix']}{job_id}"
            full_path = f"{pending_path}/{approval_id}"

            try:
                await asyncio.to_thread(
                    firebase.delete_documents_by_full_paths,
                    [full_path],
                )
                logger.debug(f"[JOB_ACTIONS] Deleted approval entry {approval_id}")
            except Exception:
                # Entry might not exist, which is fine
                pass

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to delete approval entries: {e}")


async def _update_cache_after_delete(
    uid: str,
    job_type: str,
    company_data: Dict[str, Any],
    job_file_pairs: List[Tuple[str, str]],
) -> None:
    """
    Update business cache to remove deleted items.

    Uses contextual_publisher to update the business cache and notify
    connected clients if they're on the relevant page.
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    company_id = company_data.get("company_id", "")
    domain = config["domain"]

    try:
        redis = get_redis()
        cache_key = f"business:{uid}:{company_id}:{domain}"

        # Get current cached data
        cached = redis.get(cache_key)
        if not cached:
            return

        raw_data = json.loads(cached if isinstance(cached, str) else cached.decode())
        deleted_ids = {job_id for job_id, _ in job_file_pairs}

        # Unwrap unified cache manager envelope if present
        is_wrapped = isinstance(raw_data, dict) and "cache_version" in raw_data and "data" in raw_data
        data = raw_data["data"] if is_wrapped else raw_data

        # Cache structure: lists under "documents" (routing) OR flat at root (invoices/bank)
        docs_container = data.get("documents", data)

        # Remove deleted items from each category
        actually_removed = 0
        for category in ["in_process", "pending", "processed", "to_process"]:
            if category in docs_container:
                before = len(docs_container[category])
                docs_container[category] = [
                    item
                    for item in docs_container[category]
                    if item.get("job_id") not in deleted_ids
                    and item.get("id") not in deleted_ids
                ]
                actually_removed += before - len(docs_container[category])

        # Update counts
        if "counts" in data:
            for category in ["in_process", "pending", "processed", "to_process"]:
                if category in docs_container:
                    data["counts"][category] = len(docs_container[category])

        # Save back (preserve wrapper if present)
        if is_wrapped:
            raw_data["data"] = data
            redis.setex(cache_key, 1800, json.dumps(raw_data))
        else:
            redis.setex(cache_key, 1800, json.dumps(data))
        logger.info(f"[JOB_ACTIONS] Cache updated - removed {actually_removed} items from {domain} (wrapped={is_wrapped})")

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to update cache: {e}")


async def _remove_from_billing_history_cache(
    uid: str,
    company_id: str,
    deleted_job_ids: List[str],
) -> int:
    """
    Remove deleted jobs from the billing_history cache.

    Returns the number of items actually removed.
    Handles unified cache wrapper (cache_version/data envelope).
    """
    try:
        redis = get_redis()
        cache_key = f"business:{uid}:{company_id}:billing_history"

        raw = redis.get(cache_key)
        if not raw:
            return 0

        cache_data = json.loads(raw if isinstance(raw, str) else raw.decode())

        # Unwrap unified cache manager envelope if present
        is_wrapped = isinstance(cache_data, dict) and "cache_version" in cache_data and "data" in cache_data
        inner_data = cache_data.get("data", cache_data) if is_wrapped else cache_data

        items_list = inner_data.get("items", [])
        deleted_set = set(deleted_job_ids)
        original_count = len(items_list)

        # Filter out deleted items (match by jobId or id)
        items_list = [
            item for item in items_list
            if item.get("jobId") not in deleted_set
            and item.get("id") not in deleted_set
        ]

        removed = original_count - len(items_list)
        if removed > 0:
            inner_data["items"] = items_list
            if is_wrapped:
                cache_data["data"] = inner_data
                redis.setex(cache_key, 1800, json.dumps(cache_data))
            else:
                redis.setex(cache_key, 1800, json.dumps(inner_data))
            logger.info(
                f"[JOB_ACTIONS] billing_history cache: removed {removed} items "
                f"(remaining={len(items_list)}, wrapped={is_wrapped})"
            )
        return removed

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to update billing_history cache: {e}")
        return 0


async def _resolve_ap_drive_file_id(uid: str, job_id: str) -> Tuple[str, str]:
    """
    For an AP job, read task_manager/{job_id} and extract:
    - drive_file_id from department_data.APbookeeper.file_id
    - file_name from department_data.APbookeeper.file_name (or top-level)

    Returns: (drive_file_id, file_name) — ("", "") if not found.
    Non-blocking: returns ("", "") on error.
    """
    try:
        firebase = get_firebase_management()
        doc = await asyncio.to_thread(
            lambda: firebase.db.collection(f"clients/{uid}/task_manager").document(job_id).get()
        )
        if not doc.exists:
            logger.warning(f"[JOB_ACTIONS] _resolve_ap_drive_file_id: doc {job_id} not found")
            return ("", "")

        data = doc.to_dict() or {}

        # file_id can be at root level (written by update_job_status)
        # or in department_data.APbookeeper (denormalized)
        drive_file_id = data.get("file_id", "")

        if not drive_file_id:
            dept_data = data.get("department_data", {})
            # Try casing variants: APbookeeper, Apbookeeper, apbookeeper
            ap_data = dept_data.get("APbookeeper") or dept_data.get("Apbookeeper") or dept_data.get("apbookeeper") or {}
            drive_file_id = ap_data.get("file_id", "")

        file_name = data.get("file_name", "")

        return (drive_file_id, file_name)

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] _resolve_ap_drive_file_id FAILED for {job_id}: {e}")
        return ("", "")


async def _move_file_to_drive_input(
    uid: str,
    company_data: Dict[str, Any],
    job_id: str,
    file_name: str = "",
) -> bool:
    """
    Move a file back to Drive input folder (Router delete workflow).

    Uses DriveClientServiceSingleton.move_file() to move the file
    (identified by job_id = Drive file ID) to the input_drive_doc_id folder
    (from Level 2 cache).

    Args:
        uid: Firebase user ID (needed for Drive credentials)
        company_data: Company context containing input_drive_doc_id
        job_id: The Drive file ID (= job_id in router context)
        file_name: Human-readable file name (for logging only)

    Returns:
        True if move successful, False otherwise
    """
    try:
        input_drive_doc_id = company_data.get("input_drive_doc_id", "")
        if not input_drive_doc_id:
            logger.warning(f"[JOB_ACTIONS] No input_drive_doc_id in company_data, cannot move file")
            return False

        from ..driveClientService import get_drive_client_service
        drive = get_drive_client_service()

        result = await asyncio.to_thread(
            drive.move_file,
            uid,
            job_id,               # Drive file ID
            input_drive_doc_id,   # Destination folder ID from Level 2 cache
        )

        if result:
            logger.info(f"[JOB_ACTIONS] Drive move confirmed for {file_name or job_id}: {result}")
            return True
        else:
            logger.warning(f"[JOB_ACTIONS] Drive move returned None for {file_name or job_id}")
            return False
    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Failed to move file to Drive: {e}")
        return False


async def _add_to_routing_todo(
    uid: str,
    company_data: Dict[str, Any],
    moved_items: List[Dict[str, str]],
) -> None:
    """
    Move items back to routing to_process after Drive move confirmation.

    If the item already exists in another list (e.g. processed), it is MOVED
    to to_process with status reset. Otherwise a new item is added.
    Handles unified cache wrapper (cache_version/data envelope).
    """
    if not moved_items:
        return

    company_id = company_data.get("company_id", "")

    try:
        redis = get_redis()
        cache_key = f"business:{uid}:{company_id}:routing"

        cached = redis.get(cache_key)
        if not cached:
            return

        raw_data = json.loads(cached if isinstance(cached, str) else cached.decode())

        # Unwrap unified cache manager envelope if present
        is_wrapped = isinstance(raw_data, dict) and "cache_version" in raw_data and "data" in raw_data
        data = raw_data["data"] if is_wrapped else raw_data

        # Cache routing: listes à la racine OU sous "documents" (selon le domaine)
        docs_container = data.get("documents", data)
        to_process = docs_container.get("to_process", [])

        moved = 0
        for item in moved_items:
            drive_id = item.get("drive_file_id") or item.get("job_id", "")
            if not drive_id:
                continue

            # 1. Remove from ALL other lists (processed, in_process, pending)
            found_existing = None
            for list_name in ["processed", "in_process", "pending"]:
                lst = docs_container.get(list_name, [])
                for existing in lst:
                    if existing.get("drive_file_id") == drive_id or existing.get("id") == drive_id or existing.get("job_id") == drive_id:
                        found_existing = existing
                        break
                if found_existing:
                    docs_container[list_name] = [
                        e for e in lst
                        if e.get("drive_file_id") != drive_id and e.get("id") != drive_id and e.get("job_id") != drive_id
                    ]
                    break

            # 2. Check if already in to_process
            already_in_to_process = None
            for existing in to_process:
                if existing.get("drive_file_id") == drive_id or existing.get("id") == drive_id or existing.get("job_id") == drive_id:
                    already_in_to_process = existing
                    break

            if already_in_to_process:
                # Update status in-place
                already_in_to_process["status"] = "to_process"
                already_in_to_process["timestamp"] = datetime.now(timezone.utc).isoformat()
                moved += 1
            elif found_existing:
                # Move from other list: update status and append to to_process
                found_existing["status"] = "to_process"
                found_existing["timestamp"] = datetime.now(timezone.utc).isoformat()
                to_process.append(found_existing)
                moved += 1
            else:
                # Not found anywhere: create new item
                new_item = {
                    "id": drive_id,
                    "job_id": drive_id,
                    "file_name": item.get("file_name", ""),
                    "drive_file_id": drive_id,
                    "status": "to_process",
                    "source": "drive",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if item.get("klk_job_id"):
                    new_item["klk_job_id"] = item["klk_job_id"]
                if item.get("collection_id"):
                    new_item["collection_id"] = item["collection_id"]
                if item.get("mandate_path"):
                    new_item["mandate_path"] = item["mandate_path"]
                to_process.append(new_item)
                moved += 1

        docs_container["to_process"] = to_process
        # Recalculate ALL counts
        if "counts" in data:
            for cat in ["to_process", "in_process", "pending", "processed"]:
                if cat in docs_container:
                    data["counts"][cat] = len(docs_container[cat])

        # Save back (preserve wrapper if present)
        if is_wrapped:
            raw_data["data"] = data
            redis.setex(cache_key, 1800, json.dumps(raw_data))
        else:
            redis.setex(cache_key, 1800, json.dumps(data))
        logger.info(f"[JOB_ACTIONS] Moved {moved} items to routing to_process (wrapped={is_wrapped})")

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to move items to routing to_process: {e}")


async def _move_to_list_in_cache(
    uid: str,
    job_type: str,
    company_data: Dict[str, Any],
    job_id: str,
    target_status: str,
) -> None:
    """
    Move a single item to a different list in the business cache.

    Used by restart to move items from current list to to_process.
    Finds the item in any list, removes it, updates status, adds to target list.
    """
    config = JOB_TYPE_CONFIG.get(job_type, JOB_TYPE_CONFIG["router"])
    company_id = company_data.get("company_id", "")
    domain = config["domain"]

    try:
        redis = get_redis()
        cache_key = f"business:{uid}:{company_id}:{domain}"
        cached = redis.get(cache_key)
        if not cached:
            return

        raw_data = json.loads(cached if isinstance(cached, str) else cached.decode())

        # Unwrap unified cache manager envelope if present
        is_wrapped = isinstance(raw_data, dict) and "cache_version" in raw_data and "data" in raw_data
        data = raw_data["data"] if is_wrapped else raw_data
        documents = data.get("documents", data)

        # Determine target list name from domain config
        domain_config = get_domain_config(domain)
        target_list = domain_config.get_list_for_status(target_status) if domain_config else "to_process"

        # Search all lists for the item and remove it
        all_lists = ["to_process", "in_process", "pending", "processed"]
        found_item = None
        source_list = None

        for list_name in all_lists:
            items = documents.get(list_name, [])
            for i, item in enumerate(items):
                item_id = item.get("id") or item.get("job_id")
                if item_id == job_id:
                    found_item = items.pop(i)
                    source_list = list_name
                    break
            if found_item:
                break

        if not found_item:
            logger.debug(f"[JOB_ACTIONS] Item {job_id} not found in any cache list")
            return

        # Update status and add to target list
        found_item["status"] = target_status
        found_item["updated_at"] = datetime.now(timezone.utc).isoformat()

        if target_list not in documents:
            documents[target_list] = []
        documents[target_list].insert(0, found_item)

        # Recalculate counts
        counts = {}
        if "counts" in data:
            for list_name in all_lists:
                if list_name in documents:
                    data["counts"][list_name] = len(documents[list_name])
            counts = data["counts"]

        # Determine TTL based on domain
        ttl = 1800 if domain == "routing" else 2400
        if is_wrapped:
            raw_data["data"] = data
            redis.setex(cache_key, ttl, json.dumps(raw_data))
        else:
            redis.setex(cache_key, ttl, json.dumps(data))
        logger.info(f"[JOB_ACTIONS] Moved {job_id} from {source_list} to {target_list} (wrapped={is_wrapped})")

        # Invalidate page_state cache so that page.restore_state
        # won't serve stale data (item still in old list)
        try:
            from app.wrappers.page_state_manager import get_page_state_manager
            page_manager = get_page_state_manager()
            page_manager.invalidate_page_state(
                uid=uid,
                company_id=company_id,
                page=domain,  # "routing", "apbookeeper", etc.
            )
            logger.debug(f"[JOB_ACTIONS] page_state cache invalidated for page={domain}")
        except Exception as ps_err:
            logger.warning(f"[JOB_ACTIONS] Failed to invalidate page_state: {ps_err}")

        # Broadcast item_update event so the frontend can sync its store
        ws_event_type = f"{domain}.item_update"
        await hub.broadcast(uid, {
            "type": ws_event_type,
            "payload": {
                "action": "status_change",
                "trigger_action": "restart",
                "items": [found_item],
                "item_ids": [job_id],
                "from_list": source_list,
                "to_list": target_list,
                "new_status": target_status,
                "counts": counts,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        })
        logger.info(f"[JOB_ACTIONS] Broadcasted {ws_event_type} for restart: {source_list} → {target_list}")

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to move item in cache: {e}")


# ============================================
# TASK MANAGER PERSISTENCE
# ============================================

async def _persist_jobs_to_task_manager(
    uid: str,
    job_type: str,
    document_ids: List[str],
    company_data: Dict[str, Any],
    batch_id: str,
    payload: Dict[str, Any],
    jobs_data: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Persist initial job state to task_manager for cache-reload coherence.

    When a job is launched, we write an initial record to task_manager with
    status "in_queue". This ensures that if the page reloads before the worker
    writes its own record, the cache builder will still classify the item as
    "in_process" (not "to_process").

    The worker will overwrite this record when it starts processing.
    """
    firebase = get_firebase_management()
    config = JOB_TYPE_CONFIG[job_type]
    mandate_path = company_data.get("mandate_path", "")
    company_id = company_data.get("company_id", "")
    task_mgr_path = f"clients/{uid}/task_manager"

    # Build file_name lookup from jobs_data
    file_name_map = {}
    if jobs_data:
        for item in jobs_data:
            jid = item.get("job_id") or item.get("drive_file_id") or ""
            if jid:
                file_name_map[str(jid)] = item.get("file_name", "")

    if job_type == "router":
        # Router: 1 doc par fichier, job_id = drive_file_id
        for file_id in document_ids:
            await asyncio.to_thread(
                firebase.add_or_update_job_by_job_id,
                task_mgr_path,
                {
                    "job_id": file_id,
                    "status": "in_queue",
                    "department": "Router",
                    "mandate_path": mandate_path,
                    "collection_id": company_id,
                    "batch_id": batch_id,
                    "file_name": file_name_map.get(file_id, ""),
                }
            )

    elif job_type == "apbookeeper":
        # AP: doc existe deja (cree par Router), juste update status + enrichir champs critiques
        for job_id in document_ids:
            await asyncio.to_thread(
                firebase.add_or_update_job_by_job_id,
                task_mgr_path,
                {
                    "job_id": job_id,
                    "status": "in_queue",
                    "department": "APbookeeper",
                    "collection_id": company_id,
                    "batch_id": batch_id,
                }
            )

    elif job_type == "bankbookeeper":
        # Bank: 1 doc par transaction, job_id = {company_id}_{account_id}_{move_id}
        transactions_data = payload.get("transactions_data", [])
        bank_account_id = payload.get("bank_account_id", "")
        for tx in transactions_data:
            move_id = str(tx.get("id") or tx.get("move_id") or tx.get("transaction_id", ""))
            acct_id = str(tx.get("account_id") or tx.get("journal_id") or bank_account_id)
            composite_key = f"{company_id}_{acct_id}_{move_id}"
            await asyncio.to_thread(
                firebase.add_or_update_job_by_job_id,
                task_mgr_path,
                {
                    "job_id": composite_key,
                    "status": "in_queue",
                    "department": "Bankbookeeper",
                    "mandate_path": mandate_path,
                    "collection_id": company_id,
                    "batch_id": batch_id,
                    "department_data": {
                        "Bankbookeeper": {
                            "batch_id": batch_id,
                            "bank_account_id": acct_id,
                            "transaction_id": move_id,
                        }
                    }
                }
            )

    elif job_type == "onboarding":
        for job_id_item in document_ids:
            await asyncio.to_thread(
                firebase.add_or_update_job_by_job_id,
                task_mgr_path,
                {
                    "job_id": job_id_item,
                    "status": "in_queue",
                    "department": "Onboarding",
                    "mandate_path": mandate_path,
                    "collection_id": company_id,
                    "batch_id": batch_id,
                }
            )


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "handle_job_process",
    "handle_job_stop",
    "handle_job_restart",
    "handle_job_delete",
    "create_and_publish_notification",
    "JOB_TYPE_CONFIG",
]
