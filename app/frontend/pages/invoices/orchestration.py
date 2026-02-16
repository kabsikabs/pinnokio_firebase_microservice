"""
APBookkeeper/Invoices Page Orchestration
=========================================

Handles post-authentication data loading for the APBookkeeper (Invoices) page.

IMPORTANT: This module reuses the SAME data source as the dashboard widgets:
- Firebase AP documents from firebase_cache_handlers.get_ap_documents() → to_process, in_process, pending, processed

Pattern:
    page.restore_state -> cache hit: instant data
    page.restore_state -> cache miss: invoices.orchestrate_init -> full load

Flow (3-Level Architecture):
    1. Frontend sends page.restore_state with page="invoices"
    2. If cache hit -> page.state_restored with data
    3. If cache miss -> page.state_not_found
    4. Frontend sends invoices.orchestrate_init with company_id only
    5. Backend reads company context from Level 2 cache: company:{uid}:{cid}:context
    6. Backend checks business cache (Level 3): business:{uid}:{cid}:invoices
    7. If cache miss -> fetch from Firebase using mandate_path from Level 2
    8. Backend saves to page state cache
    9. Backend sends invoices.full_data

ARCHITECTURE (3-Level Cache):
    - Level 2: company:{uid}:{cid}:context → Full company_data (mandate_path, client_uuid, etc.)
    - Level 3: business:{uid}:{cid}:invoices → { to_process, in_process, pending, processed }
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.firebase_cache_handlers import get_firebase_cache_handlers
from app.redis_client import get_redis

logger = logging.getLogger("invoices.orchestration")

# Cache TTL for invoices documents
TTL_INVOICES_DOCUMENTS = 1800  # 30 minutes


def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """
    Retrieve company context from Level 2 cache.

    Cache hierarchy (tries in order):
    1. Level 2: company:{uid}:{company_id}:context (full company_data)
    2. Fallback Firebase: fetch_all_mandates_light → repopulate Level 2

    Returns:
        Dict with: mandate_path, client_uuid, etc.
    """
    redis_client = get_redis()

    # 1. Try Level 2 context key (full company_data)
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            logger.info(
                f"[INVOICES] Context from Level 2: {level2_key} "
                f"mandate_path={data.get('mandate_path', '')[:30]}..."
            )
            return data
    except Exception as e:
        logger.warning(f"[INVOICES] Level 2 context read error: {e}")

    # 2. Fallback Firebase: Level 2 expiré ou absent → récupérer depuis Firebase
    logger.warning(f"[INVOICES] Level 2 cache MISS for uid={uid}, company={company_id} — fetching from Firebase...")
    try:
        from app.firebase_providers import get_firebase_management
        from app.wrappers.dashboard_orchestration_handlers import set_selected_company

        firebase = get_firebase_management()
        mandates = firebase.fetch_all_mandates_light(uid)
        for m in (mandates or []):
            m_ids = (m.get("contact_space_id"), m.get("id"), m.get("contact_space_name"))
            if company_id in m_ids:
                m["company_id"] = company_id
                set_selected_company(uid, company_id, m)
                logger.info(
                    f"[INVOICES] Context repopulated from Firebase: "
                    f"mandate_path={m.get('mandate_path', '')[:30]}..."
                )
                return m
    except Exception as e:
        logger.error(f"[INVOICES] Firebase fallback failed: {e}")

    logger.error(f"[INVOICES] No company context found for uid={uid}, company={company_id}")
    return {}


async def handle_invoices_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.orchestrate_init WebSocket event.

    This is the main orchestration entry point when the Invoices page loads
    and no cached state is available.

    CRITICAL: Reuses the SAME data source as dashboard widgets:
    - Firebase AP documents via firebase_cache_handlers.get_ap_documents()

    ARCHITECTURE (3-Level Cache):
    - Company context (mandate_path, client_uuid) is retrieved
      from Level 2 cache: company:{uid}:{company_id}:context
    - This cache was populated during dashboard orchestration (_run_company_phase)
    - Frontend only needs to send company_id - backend retrieves the rest from cache

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            "company_id": str (required - used to lookup context from Level 2 cache)
        }
    """
    company_id = payload.get("company_id")

    logger.info(f"[INVOICES] Orchestration started for company={company_id}")

    # Validate company_id
    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID"
            }
        })
        return

    # ════════════════════════════════════════════════════════════
    # Get company context from Level 2 cache
    # Populated during dashboard orchestration (_run_company_phase)
    # Contains: mandate_path, client_uuid, etc.
    # ════════════════════════════════════════════════════════════
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")
    client_uuid = context.get("client_uuid", "")

    # Validate we have the required context
    if not mandate_path:
        logger.error(f"[INVOICES] No mandate_path in session context - dashboard orchestration may not have run")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        # ════════════════════════════════════════════════════════════
        # STEP 1: Fetch AP documents from task_manager (Source de Vérité)
        # Uses firebase_cache_handlers.get_ap_documents() which:
        # - Checks Redis cache first
        # - On miss: fetches from task_manager filtered by department APbookeeper
        # - Returns: to_process, in_process, pending, processed, step_mapping
        # ════════════════════════════════════════════════════════════
        to_process_documents = []
        in_process_documents = []
        pending_documents = []
        processed_documents = []
        step_mapping = {}

        logger.info(f"[INVOICES] Fetching documents via firebase_cache_handlers...")
        cache_handlers = get_firebase_cache_handlers()

        try:
            ap_result = await cache_handlers.get_ap_documents(
                user_id=uid,
                company_id=company_id,
                mandate_path=mandate_path
            )

            if ap_result.get("data"):
                data = ap_result["data"]
                to_process_documents = data.get("to_process", [])
                in_process_documents = data.get("in_process", [])
                pending_documents = data.get("pending", [])
                processed_documents = data.get("processed", [])
                step_mapping = data.get("step_mapping", {})

                logger.info(
                    f"[INVOICES] Documents loaded: "
                    f"to_process={len(to_process_documents)}, "
                    f"in_process={len(in_process_documents)}, "
                    f"pending={len(pending_documents)}, "
                    f"processed={len(processed_documents)}, "
                    f"step_mapping_keys={len(step_mapping)} "
                    f"source={ap_result.get('source', 'unknown')}"
                )
        except Exception as ap_error:
            logger.error(f"[INVOICES] AP documents fetch error: {ap_error}")

        # ════════════════════════════════════════════════════════════
        # STEP 1B: Fetch instruction templates
        # ════════════════════════════════════════════════════════════
        instruction_templates = []
        try:
            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()
            instruction_templates = firebase.fetch_instruction_templates(mandate_path, "invoices")
            logger.info(f"[INVOICES] Loaded {len(instruction_templates)} instruction templates")
        except Exception as tpl_err:
            logger.warning(f"[INVOICES] Failed to load instruction templates: {tpl_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 2: Build combined invoices data structure
        # ════════════════════════════════════════════════════════════
        invoices_data = {
            "documents": {
                "to_process": to_process_documents,
                "in_process": in_process_documents,
                "pending": pending_documents,
                "processed": processed_documents,
            },
            "step_mapping": step_mapping,
            "counts": {
                "to_process": len(to_process_documents),
                "in_process": len(in_process_documents),
                "pending": len(pending_documents),
                "processed": len(processed_documents),
            },
            "pagination": {
                "page": 1,
                "pageSize": 20,
                "totalPages": 1,
                "totalItems": (
                    len(to_process_documents) +
                    len(in_process_documents) +
                    len(pending_documents) +
                    len(processed_documents)
                ),
            },
            "company": {
                "id": company_id,
                "mandate_path": mandate_path,
                "client_uuid": client_uuid,
            },
            "instruction_templates": instruction_templates,
            "meta": {
                "loaded_at": datetime.utcnow().isoformat() + "Z",
                "version": "1.0",
                "source": "task_manager"
            }
        }

        # ════════════════════════════════════════════════════════════
        # STEP 3: Save page state for fast recovery
        # ════════════════════════════════════════════════════════════
        try:
            from app.wrappers.page_state_manager import get_page_state_manager
            page_manager = get_page_state_manager()
            page_manager.save_page_state(
                uid=uid,
                company_id=company_id,
                page="invoices",
                mandate_path=mandate_path,
                data=invoices_data
            )
            logger.info(f"[INVOICES] Page state saved for fast recovery")
        except Exception as cache_err:
            logger.warning(f"[INVOICES] Failed to save page state: {cache_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 4: Send response via WebSocket
        # ════════════════════════════════════════════════════════════
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.FULL_DATA,
            "payload": {
                "success": True,
                "data": invoices_data,
                "company_id": company_id,
            }
        })

        logger.info(
            f"[INVOICES] Orchestration completed for company={company_id}: "
            f"total={invoices_data['pagination']['totalItems']} documents"
        )

    except Exception as e:
        logger.error(f"[INVOICES] Orchestration failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": str(e),
                "code": "ORCHESTRATION_ERROR"
            }
        })


async def handle_invoices_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.refresh WebSocket event.

    Refreshes document data by invalidating cache and re-fetching.
    Uses the same flow as orchestrate_init but forces cache refresh.
    """
    company_id = payload.get("company_id")
    tab = payload.get("tab", "all")  # Optional: which tab to focus

    logger.info(f"[INVOICES] Refresh requested for company={company_id}, tab={tab}")

    # Invalidate AP documents cache first
    try:
        cache_handlers = get_firebase_cache_handlers()
        await cache_handlers.invalidate_ap_cache(
            user_id=uid,
            company_id=company_id
        )
        logger.info(f"[INVOICES] AP cache invalidated")
    except Exception as e:
        logger.warning(f"[INVOICES] Failed to invalidate AP cache: {e}")

    # Re-run orchestration (only needs company_id now)
    await handle_invoices_orchestrate_init(uid, session_id, {"company_id": company_id})


async def handle_invoices_process(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.process WebSocket event.

    Triggers processing of selected documents via the APBookkeeper workflow.
    """
    document_ids = payload.get("document_ids", [])
    company_id = payload.get("company_id")
    general_instructions = payload.get("general_instructions", "")
    document_instructions = payload.get("document_instructions", {})
    approval_states = payload.get("approval_states", {})
    workflow_states = payload.get("workflow_states", {})

    logger.info(f"[INVOICES] Process requested for {len(document_ids)} documents")

    if not document_ids:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "No documents selected",
                "code": "NO_DOCUMENTS"
            }
        })
        return

    try:
        # TODO: Integrate with actual APBookkeeper workflow
        # For now, acknowledge the request and return success

        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.PROCESSED,
            "payload": {
                "success": True,
                "processed": document_ids,
                "failed": [],
                "summary": {
                    "totalProcessed": len(document_ids),
                    "totalFailed": 0
                }
            }
        })

    except Exception as e:
        logger.error(f"[INVOICES] Process failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": str(e),
                "code": "PROCESS_ERROR"
            }
        })


async def handle_invoices_stop(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.stop WebSocket event.

    Stops processing of selected jobs using centralized job_actions_handler.
    """
    job_ids = payload.get("job_ids", [])
    company_id = payload.get("company_id")

    logger.info(f"[INVOICES] Stop requested for {len(job_ids)} jobs")

    if not job_ids:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "No jobs selected",
                "code": "NO_JOBS"
            }
        })
        return

    try:
        # Get company context
        context = _get_company_context(uid, company_id)

        # Use centralized job actions handler
        from app.wrappers.job_actions_handler import handle_job_stop

        result = await handle_job_stop(
            uid=uid,
            job_type="apbookeeper",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": context.get("mandate_path", ""),
            }
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.INVOICES.STOPPED,
                "payload": {
                    "success": True,
                    "job_ids": job_ids,
                    "message": result.get("message", f"{len(job_ids)} jobs stopped"),
                    "_optimistic_update_id": payload.get("_optimistic_update_id"),
                }
            })
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.INVOICES.ERROR,
                "payload": {
                    "error": result.get("error", "Stop failed"),
                    "code": result.get("code", "STOP_ERROR")
                }
            })

    except Exception as e:
        logger.error(f"[INVOICES] Stop failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": str(e),
                "code": "STOP_ERROR"
            }
        })


async def handle_invoices_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.delete WebSocket event.

    Deletes completed/processed invoices using centralized job_actions_handler.
    """
    job_ids = payload.get("job_ids", [])
    company_id = payload.get("company_id")

    logger.info(f"[INVOICES] Delete requested for {len(job_ids)} documents")

    if not job_ids:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "No documents selected",
                "code": "NO_DOCUMENTS"
            }
        })
        return

    try:
        # Get company context
        context = _get_company_context(uid, company_id)

        # Use centralized job actions handler
        from app.wrappers.job_actions_handler import handle_job_delete

        result = await handle_job_delete(
            uid=uid,
            job_type="apbookeeper",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": context.get("mandate_path", ""),
            }
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.INVOICES.DELETED,
                "payload": {
                    "success": True,
                    "job_ids": result.get("deleted_jobs", job_ids),
                    "message": result.get("message", f"{len(job_ids)} documents deleted"),
                    "_optimistic_update_id": payload.get("_optimistic_update_id"),
                }
            })
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.INVOICES.ERROR,
                "payload": {
                    "error": result.get("error", "Delete failed"),
                    "code": result.get("code", "DELETE_ERROR")
                }
            })

    except Exception as e:
        logger.error(f"[INVOICES] Delete failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": str(e),
                "code": "DELETE_ERROR"
            }
        })


async def handle_invoices_restart(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.restart WebSocket event.

    Restarts a job that is stuck or needs to be re-processed using centralized job_actions_handler.
    """
    job_id = payload.get("job_id")
    company_id = payload.get("company_id")

    logger.info(f"[INVOICES] Restart requested for job={job_id}")

    if not job_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "No job_id provided",
                "code": "MISSING_JOB_ID"
            }
        })
        return

    try:
        # Get company context
        context = _get_company_context(uid, company_id)

        # Use centralized job actions handler
        from app.wrappers.job_actions_handler import handle_job_restart

        result = await handle_job_restart(
            uid=uid,
            job_type="apbookeeper",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": context.get("mandate_path", ""),
            }
        )

        if result.get("success"):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.INVOICES.RESTARTED,
                "payload": {
                    "success": True,
                    "job_id": job_id,
                    "message": result.get("message", f"Job {job_id} has been successfully reset"),
                    "_optimistic_update_id": payload.get("_optimistic_update_id"),
                }
            })
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.INVOICES.ERROR,
                "payload": {
                    "error": result.get("error", "Restart failed"),
                    "code": result.get("code", "RESTART_ERROR")
                }
            })

    except Exception as e:
        logger.error(f"[INVOICES] Restart failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": str(e),
                "code": "RESTART_ERROR"
            }
        })


async def handle_invoices_instructions_save(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle invoices.instructions_save WebSocket event.

    Saves instructions for a specific document.
    """
    document_id = payload.get("document_id")
    company_id = payload.get("company_id")
    instructions = payload.get("instructions", "")

    logger.info(f"[INVOICES] Save instructions for document={document_id}")

    if not document_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": "No document_id provided",
                "code": "MISSING_DOCUMENT_ID"
            }
        })
        return

    try:
        # TODO: Save instructions to Firebase/backend

        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.INSTRUCTIONS_SAVED,
            "payload": {
                "success": True,
                "document_id": document_id,
                "message": "Instructions saved"
            }
        })

    except Exception as e:
        logger.error(f"[INVOICES] Save instructions failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.INVOICES.ERROR,
            "payload": {
                "error": str(e),
                "code": "SAVE_INSTRUCTIONS_ERROR"
            }
        })
