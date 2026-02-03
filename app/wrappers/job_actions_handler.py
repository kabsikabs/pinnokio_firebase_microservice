"""
Job Actions Handler - Centralized Job Action Management
========================================================

Central handler for managing job actions (process, stop, restart, delete) across
different job types (Router, APbookeeper, Bankbookeeper).

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

from ..firebase_providers import get_firebase_management, FirebaseManagement
from ..realtime.contextual_publisher import (
    publish_routing_event,
    publish_invoices_event,
    publish_bank_event,
    publish_dashboard_event,
)
from ..realtime.pubsub_helper import publish_notification_new
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
        f"[JOB_ACTIONS] → company_data: mandate_path={mandate_path[:50] if mandate_path else 'None'}..."
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

        # Get document details from cache to build jobs_data
        documents_info = await _get_documents_from_cache(
            uid=uid,
            company_id=company_id,
            domain=config["domain"],
            document_ids=document_ids,
        )

        # Build jobs_data array (required format for jobbeur)
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
                # Use per-document override if exists, otherwise use company default
                "approval_required": approval_states.get(doc_id, default_approval),
                "automated_workflow": workflow_states.get(doc_id, default_workflow),
            }
            jobs_data.append(job_item)

        # Build the jobbeur payload (correct format with jobs_data)
        jobbeur_payload = {
            "collection_name": str(company_id),
            "jobs_data": jobs_data,
            "start_instructions": payload.get("general_instructions", ""),
            "client_uuid": company_data.get("client_uuid", ""),
            "user_id": uid,
            "pub_sub_id": pub_sub_id,
            "mandates_path": mandate_path,
            "batch_id": batch_id,
            "settings": [
                {"communication_mode": company_data.get("communication_mode", "rag")},
                {"log_communication_mode": company_data.get("log_communication_mode", "rag")},
                {"dms_system": company_data.get("dms_type", "odoo")},
            ],
        }

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

        logger.info(f"[JOB_ACTIONS] → Step 2: Calling HTTP endpoint: {process_url}")

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
            logger.info(f"[JOB_ACTIONS] → Step 4: HTTP success - job_id={job_id}")

            # Create notifications for each document (correct format)
            logger.info(f"[JOB_ACTIONS] → Step 5: Creating Firebase notifications...")
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

            # Step 6: Apply optimistic list change (move items to in_process)
            logger.info(f"[JOB_ACTIONS] → Step 6: Applying optimistic list change...")
            list_change_result = await _apply_optimistic_list_change(
                uid=uid,
                job_type=job_type,
                document_ids=document_ids,
                company_data=company_data,
                action="process",
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
                f"batch_id={batch_id} count={len(document_ids)}"
            )
            logger.info(
                f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
            )

            return {
                "success": True,
                "job_id": job_id,
                "batch_id": batch_id,
                "processed_count": len(document_ids),
                "message": f"Processing started for {len(document_ids)} documents",
                "list_change": list_change_result,
            }
        else:
            logger.error(
                f"[JOB_ACTIONS] handle_job_process FAILED - "
                f"status={status} response={result}"
            )
            return {
                "success": False,
                "error": result.get("message", f"HTTP error {status}"),
                "code": "PROCESS_ERROR",
            }

    except aiohttp.ClientError as e:
        logger.error(f"[JOB_ACTIONS] HTTP client error: {e}")
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
            "code": "CONNECTION_ERROR",
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
        # Build stop payload
        stop_payload = {
            "user_id": uid,
            "job_ids": job_ids,
            "collection_name": company_id,
            "mandates_path": mandate_path,
        }

        # Call stop endpoint
        base_url = _get_base_url(job_type=job_type)
        stop_url = f"{base_url}{config['stop_endpoint']}"

        logger.info(f"[JOB_ACTIONS] → Step 1: Calling stop endpoint: {stop_url}")
        logger.debug(f"[JOB_ACTIONS] → stop_payload: {stop_payload}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                stop_url, json=stop_payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                status = response.status
                try:
                    result = await response.json()
                except Exception:
                    result = {"message": await response.text()}

        logger.info(f"[JOB_ACTIONS] → Step 2: HTTP response status={status}")

        if status == 200:
            logger.info(
                f"[JOB_ACTIONS] handle_job_stop SUCCESS - "
                f"job_ids={job_ids} count={len(job_ids)}"
            )
            logger.info(
                f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
            )

            return {
                "success": True,
                "stopped_jobs": job_ids,
                "message": f"Stop signal sent for {len(job_ids)} jobs",
            }
        else:
            logger.warning(
                f"[JOB_ACTIONS] handle_job_stop FAILED - "
                f"status={status} response={result}"
            )
            return {
                "success": False,
                "error": result.get("message", f"HTTP error {status}"),
                "code": "STOP_ERROR",
            }

    except aiohttp.ClientError as e:
        logger.error(f"[JOB_ACTIONS] HTTP client error: {e}")
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
            "code": "CONNECTION_ERROR",
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
    Restart a job - Precise deletion without list movement.

    Cleans up the failed/stuck job state (Chroma embeddings, Firebase metadata)
    but does NOT move items between lists. User must re-select and process.

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
                # Delete documents where job_id matches
                await asyncio.to_thread(
                    chroma.delete_documents,
                    company_id,  # collection_name
                    {"job_id": {"$eq": job_id}},  # where filter
                )
                logger.info(f"[JOB_ACTIONS] → Step 1: Chroma embeddings DELETED for job_id={job_id}")
            else:
                logger.info(f"[JOB_ACTIONS] → Step 1: Chroma service not available, skipping")
        except Exception as chroma_err:
            logger.warning(f"[JOB_ACTIONS] → Step 1: Chroma deletion SKIPPED: {chroma_err}")

        # 2. Update Firebase job status to allow reprocessing
        logger.info(f"[JOB_ACTIONS] → Step 2: Resetting Firebase job status...")
        try:
            await asyncio.to_thread(
                firebase.update_job_status,
                uid,
                job_id,
                "reset",
                {"reset_at": datetime.now(timezone.utc).isoformat(), "reset_by": "user"},
            )
            logger.info(f"[JOB_ACTIONS] → Step 2: Firebase job status RESET for job_id={job_id}")
        except Exception as fb_err:
            logger.warning(f"[JOB_ACTIONS] → Step 2: Firebase status update SKIPPED: {fb_err}")

        # 3. Delete associated notifications for this job
        logger.info(f"[JOB_ACTIONS] → Step 3: Deleting job notifications...")
        await _delete_job_notifications(uid, mandate_path, job_id)
        logger.info(f"[JOB_ACTIONS] → Step 3: Notifications cleanup completed")

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
    2. Delete RTDB chat threads (if any)
    3. Delete approval_pendinglist entries
    4. For Router: Move files back to Drive input folder
    5. Update business cache

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
        moved_to_todo = []

        # Normalize payload to get job_id and file_name pairs
        job_file_pairs = _normalize_delete_payload(payload, job_type)
        logger.info(f"[JOB_ACTIONS] → Normalized to {len(job_file_pairs)} job/file pairs")

        for idx, (job_id, file_name) in enumerate(job_file_pairs, 1):
            logger.info(f"[JOB_ACTIONS] → Processing job {idx}/{len(job_file_pairs)}: job_id={job_id}")
            try:
                # 1. Delete notifications for this job
                logger.debug(f"[JOB_ACTIONS] →   Step 1: Deleting notifications...")
                await _delete_job_notifications(uid, mandate_path, job_id)

                # 2. Delete RTDB chat threads (if exists)
                logger.debug(f"[JOB_ACTIONS] →   Step 2: Deleting chat threads...")
                await _delete_job_chat_threads(uid, mandate_path, job_id)

                # 3. Delete approval_pendinglist entries
                logger.debug(f"[JOB_ACTIONS] →   Step 3: Deleting approval_pendinglist...")
                await _delete_approval_pendinglist(
                    uid, job_type, [(job_id, file_name)], company_data
                )

                # 4. For Router: Move file back to Drive (to_do)
                if job_type == "router" and file_name:
                    logger.info(f"[JOB_ACTIONS] →   Step 4: Moving file to Drive input: {file_name}")
                    move_success = await _move_file_to_drive_input(
                        uid, company_data, file_name
                    )
                    if move_success:
                        moved_to_todo.append({"job_id": job_id, "file_name": file_name})
                        logger.info(f"[JOB_ACTIONS] →   Step 4: File moved to Drive ✓")

                deleted_jobs.append(job_id)
                logger.info(f"[JOB_ACTIONS] →   Job {job_id} DELETE completed ✓")

            except Exception as job_err:
                logger.error(
                    f"[JOB_ACTIONS] →   Job {job_id} DELETE FAILED: {job_err}"
                )

        # 5. Update business cache
        logger.info(f"[JOB_ACTIONS] → Step 5: Updating business cache...")
        if deleted_jobs:
            await _update_cache_after_delete(
                uid, job_type, company_data, job_file_pairs
            )
            logger.info(f"[JOB_ACTIONS] → Step 5: Cache updated for {len(deleted_jobs)} deleted jobs")

        # 6. If Router and files were moved, add to to_do
        if job_type == "router" and moved_to_todo:
            logger.info(f"[JOB_ACTIONS] → Step 6: Adding {len(moved_to_todo)} items to routing to_do...")
            await _add_to_routing_todo(uid, company_data, moved_to_todo)
            logger.info(f"[JOB_ACTIONS] → Step 6: Items added to routing to_do ✓")

        logger.info(
            f"[JOB_ACTIONS] handle_job_delete SUCCESS - "
            f"deleted={len(deleted_jobs)} moved_to_todo={len(moved_to_todo)}"
        )
        logger.info(
            f"[JOB_ACTIONS] ═══════════════════════════════════════════════════════════"
        )

        return {
            "success": True,
            "deleted_jobs": deleted_jobs,
            "moved_to_todo": [item["file_name"] for item in moved_to_todo],
            "message": f"Deleted {len(deleted_jobs)} jobs",
        }

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
        list_names = ["to_process", "in_process", "pending", "processed", "unprocessed"]

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
            file_id = job_item.get("drive_file_id") or job_item.get("job_id", "")
            file_name = job_item.get("file_name", file_id)

            # Build notification with correct format (camelCase for frontend)
            # Note: Firebase uses snake_case, but we convert for WS payload
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

            # Add pub_sub_id for Router
            if job_type == "router":
                notification_firebase["pub_sub_id"] = pub_sub_id

            # Write to Firebase (snake_case for Firestore)
            try:
                notification_id = await asyncio.to_thread(
                    firebase.add_document, notifications_path, notification_firebase
                )
                notification_id = str(notification_id) if notification_id else None

                if notification_id:
                    notification_ids.append(notification_id)

                    # Build camelCase notification for WebSocket (frontend expects camelCase)
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

                    # Add pubSubId for Router
                    if job_type == "router":
                        notification_ws["pubSubId"] = pub_sub_id

                    # Publish via WebSocket
                    await publish_notification_new(uid, notification_ws)

                    logger.debug(
                        f"[JOB_ACTIONS] Notification created - "
                        f"id={notification_id} file={file_name} batch_index={index}/{batch_total}"
                    )

            except Exception as notif_err:
                logger.error(f"[JOB_ACTIONS] Failed to create notification for {file_name}: {notif_err}")

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
    mandate_path: str,
    job_id: str,
) -> None:
    """Delete RTDB chat threads associated with a job."""
    try:
        # Chat threads are in RTDB, not Firestore
        # Path pattern: chats/{thread_key} where thread_key contains job_id
        # This is a cleanup operation, failure is non-critical
        logger.debug(f"[JOB_ACTIONS] Chat thread cleanup for job_id={job_id}")
    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to delete chat threads: {e}")


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

            try:
                await asyncio.to_thread(
                    firebase.delete_document,
                    f"{pending_path}/{approval_id}",
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

        data = json.loads(cached if isinstance(cached, str) else cached.decode())
        deleted_ids = {job_id for job_id, _ in job_file_pairs}

        # Remove deleted items from each category
        for category in ["in_process", "pending", "processed", "to_process", "to_do"]:
            if category in data.get("documents", {}):
                data["documents"][category] = [
                    item
                    for item in data["documents"][category]
                    if item.get("job_id") not in deleted_ids
                    and item.get("id") not in deleted_ids
                ]

        # Update counts
        if "counts" in data:
            for category in data["counts"]:
                if category in data.get("documents", {}):
                    data["counts"][category] = len(data["documents"][category])

        # Save updated cache
        redis.setex(cache_key, 1800, json.dumps(data))
        logger.info(f"[JOB_ACTIONS] Cache updated - removed {len(deleted_ids)} items")

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to update cache: {e}")


async def _move_file_to_drive_input(
    uid: str,
    company_data: Dict[str, Any],
    file_name: str,
) -> bool:
    """
    Move a file back to Drive input folder (Router delete workflow).

    This is called after a Router job is deleted to return the file
    to the to_do state for potential reprocessing.

    Returns:
        True if move successful, False otherwise
    """
    try:
        # This would integrate with the Drive service
        # For now, log and return success (actual implementation depends on Drive API)
        logger.info(f"[JOB_ACTIONS] Would move file back to Drive input: {file_name}")
        return True
    except Exception as e:
        logger.error(f"[JOB_ACTIONS] Failed to move file to Drive: {e}")
        return False


async def _add_to_routing_todo(
    uid: str,
    company_data: Dict[str, Any],
    moved_items: List[Dict[str, str]],
) -> None:
    """
    Add items back to routing to_do after Drive move confirmation.

    This updates the cache to show the files are available for reprocessing.
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

        data = json.loads(cached if isinstance(cached, str) else cached.decode())

        # Add moved items to to_process/unprocessed
        to_process = data.get("documents", {}).get("unprocessed", [])

        for item in moved_items:
            new_item = {
                "id": item.get("file_name", ""),
                "file_name": item.get("file_name", ""),
                "status": "to_process",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            to_process.append(new_item)

        data["documents"]["unprocessed"] = to_process
        if "counts" in data:
            data["counts"]["unprocessed"] = len(to_process)

        redis.setex(cache_key, 1800, json.dumps(data))
        logger.info(f"[JOB_ACTIONS] Added {len(moved_items)} items to routing to_do")

    except Exception as e:
        logger.warning(f"[JOB_ACTIONS] Failed to add items to routing todo: {e}")


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
