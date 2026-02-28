"""
Routing Page Orchestration
==========================

Handles post-authentication data loading for the Routing page.

Source de Vérité:
- Drive: Liste brute des fichiers (base "to_process")
- task_manager (Firebase): État des traitements par department "Router"
- Junction: job_id (Drive file_id == task_manager job_id)

Classification:
- Drive file sans match task_manager → to_process
- task_manager status on_process/in_queue/running → in_process
- task_manager status pending → pending
- task_manager status completed/close/closed → processed

Pattern:
    page.restore_state -> cache hit: instant data
    page.restore_state -> cache miss: routing.orchestrate_init -> full load

Flow (3-Level Architecture):
    1. Frontend sends page.restore_state with page="routing"
    2. If cache hit -> page.state_restored with data
    3. If cache miss -> page.state_not_found
    4. Frontend sends routing.orchestrate_init with company_id only
    5. Backend reads company context from Level 2 cache: company:{uid}:{cid}:context
    6. Backend fetches Drive docs + crosses with task_manager (1 bulk query)
    7. Backend saves to page state cache
    8. Backend sends routing.full_data

ARCHITECTURE (3-Level Cache):
    - Level 2: company:{uid}:{cid}:context → Full company_data (mandate_path, input_drive_doc_id, etc.)
    - Level 3: business:{uid}:{cid}:routing → { to_process, in_process, pending, processed }
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.drive_cache_handlers import get_drive_cache_handlers
from app.redis_client import get_redis

logger = logging.getLogger("routing.orchestration")

# Cache TTL for routing documents
TTL_ROUTING_DOCUMENTS = 1800  # 30 minutes


def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """
    Retrieve company context from Level 2 cache.

    Cache hierarchy (tries in order):
    1. Level 2: company:{uid}:{company_id}:context (full company_data)
    2. Fallback Firebase: fetch_all_mandates_light → repopulate Level 2

    Returns:
        Dict with: mandate_path, client_uuid, input_drive_doc_id, etc.
    """
    redis_client = get_redis()

    # 1. Try Level 2 context key (full company_data)
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            logger.info(
                f"[ROUTING] Context from Level 2: {level2_key} "
                f"mandate_path={data.get('mandate_path', '')[:30]}... "
                f"input_drive_doc_id={data.get('input_drive_doc_id', '')[:20]}..."
            )
            return data
    except Exception as e:
        logger.warning(f"[ROUTING] Level 2 context read error: {e}")

    # 2. Fallback Firebase: Level 2 expiré ou absent → récupérer depuis Firebase
    logger.warning(f"[ROUTING] Level 2 cache MISS for uid={uid}, company={company_id} — fetching from Firebase...")
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
                    f"[ROUTING] Context repopulated from Firebase: "
                    f"mandate_path={m.get('mandate_path', '')[:30]}..."
                )
                return m
    except Exception as e:
        logger.error(f"[ROUTING] Firebase fallback failed: {e}")

    logger.error(f"[ROUTING] No company context found for uid={uid}, company={company_id}")
    return {}


async def handle_routing_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.orchestrate_init WebSocket event.

    This is the main orchestration entry point when the Routing page loads
    and no cached state is available.

    CRITICAL: Reuses the SAME data sources as dashboard widgets:
    - Drive documents via drive_cache_handlers.get_documents()
    - Firebase processed documents via fetch_journal_entries_by_mandat_id()

    ARCHITECTURE (3-Level Cache):
    - Company context (input_drive_doc_id, mandate_path, client_uuid) is retrieved
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

    logger.info(f"[ROUTING] Orchestration started for company={company_id}")

    # Validate company_id
    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": "Missing company_id",
                "code": "MISSING_COMPANY_ID"
            }
        })
        return

    # ════════════════════════════════════════════════════════════
    # Get company context from Level 2 cache
    # Populated during dashboard orchestration (_run_company_phase)
    # Contains: mandate_path, client_uuid, input_drive_doc_id, etc.
    # ════════════════════════════════════════════════════════════
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")
    client_uuid = context.get("client_uuid", "")
    input_drive_id = context.get("input_drive_doc_id", "")

    # Validate we have the required context
    if not mandate_path:
        logger.error(f"[ROUTING] No mandate_path in session context - dashboard orchestration may not have run")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        # ════════════════════════════════════════════════════════════
        # STEP 1: Fetch Drive documents croisés avec task_manager
        # Uses drive_cache_handlers.get_documents() which:
        # - Checks Redis cache first
        # - On miss: fetches from Drive API + crosses with task_manager (1 bulk query)
        # - Returns: to_process, in_process, pending, processed (correctly sorted)
        # ════════════════════════════════════════════════════════════
        to_process_documents = []
        in_process_documents = []
        pending_documents = []
        processed_documents = []
        oauth_error = False

        if input_drive_id:
            logger.info(f"[ROUTING] Fetching documents via drive_cache_handlers: drive_id={input_drive_id[:20]}...")
            drive_handlers = get_drive_cache_handlers()

            try:
                drive_result = await drive_handlers.get_documents(
                    user_id=uid,
                    company_id=company_id,
                    input_drive_id=input_drive_id,
                    mandate_path=mandate_path
                )

                if drive_result.get("oauth_error"):
                    oauth_error = True
                    logger.warning(f"[ROUTING] OAuth re-auth required for Drive")
                elif drive_result.get("data"):
                    data = drive_result["data"]
                    to_process_documents = data.get("to_process", [])
                    in_process_documents = data.get("in_process", [])
                    pending_documents = data.get("pending", [])
                    processed_documents = data.get("processed", [])

                    logger.info(
                        f"[ROUTING] Documents loaded: "
                        f"to_process={len(to_process_documents)}, "
                        f"in_process={len(in_process_documents)}, "
                        f"pending={len(pending_documents)}, "
                        f"processed={len(processed_documents)} "
                        f"source={drive_result.get('source', 'unknown')}"
                    )
            except Exception as drive_error:
                logger.error(f"[ROUTING] Drive fetch error: {drive_error}")
        else:
            logger.warning(f"[ROUTING] No input_drive_doc_id in session context - skipping Drive fetch")

        # ════════════════════════════════════════════════════════════
        # STEP 1B: Fetch instruction templates
        # ════════════════════════════════════════════════════════════
        instruction_templates = []
        try:
            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()
            instruction_templates = firebase.fetch_instruction_templates(mandate_path, "routing")
            logger.info(f"[ROUTING] Loaded {len(instruction_templates)} instruction templates")
        except Exception as tpl_err:
            logger.warning(f"[ROUTING] Failed to load instruction templates: {tpl_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 2: Build combined routing data structure
        # ════════════════════════════════════════════════════════════
        routing_data = {
            "documents": {
                "to_process": to_process_documents,
                "in_process": in_process_documents,
                "pending": pending_documents,
                "processed": processed_documents,
            },
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
            "oauth": {
                "connected": not oauth_error,
                "reauth_required": oauth_error,
                "scopes": ["https://www.googleapis.com/auth/drive.readonly"] if not oauth_error else [],
            },
            "company": {
                "id": company_id,
                "mandate_path": mandate_path,
                "input_drive_id": input_drive_id,
                "client_uuid": client_uuid,
            },
            "instruction_templates": instruction_templates,
            "meta": {
                "loaded_at": datetime.utcnow().isoformat() + "Z",
                "version": "1.0",
                "source": "drive+task_manager"
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
                page="routing",
                mandate_path=mandate_path,
                data=routing_data
            )
            logger.info(f"[ROUTING] Page state saved for fast recovery")
        except Exception as cache_err:
            logger.warning(f"[ROUTING] Failed to save page state: {cache_err}")

        # ════════════════════════════════════════════════════════════
        # STEP 4: Send response via WebSocket
        # ════════════════════════════════════════════════════════════
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.FULL_DATA,
            "payload": {
                "success": True,
                "data": routing_data,
                "company_id": company_id,
            }
        })

        logger.info(
            f"[ROUTING] Orchestration completed for company={company_id}: "
            f"total={routing_data['pagination']['totalItems']} documents"
        )

    except Exception as e:
        logger.error(f"[ROUTING] Orchestration failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": str(e),
                "code": "ORCHESTRATION_ERROR"
            }
        })


async def handle_routing_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.refresh WebSocket event.

    Refreshes document data by invalidating cache and re-fetching.
    Uses the same flow as orchestrate_init but forces cache refresh.
    """
    company_id = payload.get("company_id")
    tab = payload.get("tab", "to_process")  # Optional: which tab to focus

    logger.info(f"[ROUTING] Refresh requested for company={company_id}, tab={tab}")

    # Get context from Level 2 cache
    context = _get_company_context(uid, company_id)
    input_drive_id = context.get("input_drive_doc_id", "")
    mandate_path = context.get("mandate_path", "")

    # Invalidate Drive cache first
    if input_drive_id:
        try:
            drive_handlers = get_drive_cache_handlers()
            await drive_handlers.refresh_documents(
                user_id=uid,
                company_id=company_id,
                input_drive_id=input_drive_id,
                mandate_path=mandate_path
            )
            logger.info(f"[ROUTING] Drive cache invalidated")
        except Exception as e:
            logger.warning(f"[ROUTING] Failed to invalidate Drive cache: {e}")

    # Re-run orchestration (only needs company_id now)
    await handle_routing_orchestrate_init(uid, session_id, {"company_id": company_id})


async def handle_routing_process(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.process WebSocket event.

    Triggers processing of selected documents via the centralized job_actions_handler.

    Supports optimistic updates:
    - Frontend sends _optimistic_update_id with the request
    - On success: confirms the optimistic update via metrics.update_confirmed
    - On failure: rejects the update via metrics.update_failed (triggers rollback)
    """
    document_ids = payload.get("document_ids", [])
    company_id = payload.get("company_id")
    optimistic_update_id = payload.get("_optimistic_update_id")

    logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
    logger.info(f"[ROUTING] handle_routing_process - uid={uid} session={session_id}")
    logger.info(
        f"[ROUTING] → document_ids count={len(document_ids)} company_id={company_id}"
        f"{f' optimistic_update_id={optimistic_update_id}' if optimistic_update_id else ''}"
    )

    if not document_ids:
        # Rollback optimistic update if present
        if optimistic_update_id:
            await _reject_optimistic_update(uid, optimistic_update_id, "routing", "No documents selected")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": "No documents selected",
                "code": "NO_DOCUMENTS"
            }
        })
        return

    try:
        # Get company context
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path", "")

        # Use centralized job actions handler
        from app.wrappers.job_actions_handler import handle_job_process

        result = await handle_job_process(
            uid=uid,
            job_type="router",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": mandate_path,
                "company_name": context.get("name", context.get("legal_name", company_id)),
                "client_uuid": context.get("client_uuid", ""),
                # Communication settings (pour jobbeur payload)
                "dms_type": context.get("dms_type", "odoo"),
                "communication_mode": context.get("communication_chat_type", context.get("chat_type", "pinnokio")),
                "log_communication_mode": context.get("communication_log_type", "pinnokio"),
                # Workflow defaults (from company settings)
                "router_approval_required": context.get("router_approval_required", False),
                "router_automated_workflow": context.get("router_automated_workflow", True),
            }
        )

        if result.get("success"):
            job_id = result.get("job_id")
            logger.info(f"[ROUTING] → Process SUCCESS - job_id={job_id}")

            # Notify processing started
            logger.info(f"[ROUTING] → Broadcasting ROUTING.PROCESSING_STARTED")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.PROCESSING_STARTED,
                "payload": {
                    "document_ids": document_ids,
                    "count": len(document_ids),
                    "status": "started",
                    "job_id": job_id,
                    "_optimistic_update_id": optimistic_update_id,
                }
            })

            # Note: PROCESSED event will be triggered by the jobbeur callback when complete
            # For now, also send a processed event to acknowledge the request was submitted
            logger.info(f"[ROUTING] → Broadcasting ROUTING.PROCESSED (request accepted)")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.PROCESSED,
                "payload": {
                    "success": True,
                    "processed": document_ids,
                    "failed": [],
                    "job_id": job_id,
                    "summary": {
                        "totalProcessed": len(document_ids),
                        "totalFailed": 0
                    },
                    "_optimistic_update_id": optimistic_update_id,
                }
            })

            # Confirm optimistic update if present
            if optimistic_update_id:
                logger.info(f"[ROUTING] → Confirming optimistic update: {optimistic_update_id}")
                await _confirm_optimistic_update(uid, optimistic_update_id, "routing", company_id, mandate_path)

            logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
        else:
            logger.warning(f"[ROUTING] → Process FAILED - {result.get('error')}")

            # Rollback optimistic update if present
            if optimistic_update_id:
                logger.info(f"[ROUTING] → Rejecting optimistic update: {optimistic_update_id}")
                await _reject_optimistic_update(uid, optimistic_update_id, "routing", result.get("error", "Process failed"))

            error_payload = {
                "error": result.get("error", "Process failed"),
                "code": result.get("code", "PROCESS_ERROR"),
            }
            # Propagate extra fields (balance_info, message) for specific error codes
            if result.get("balance_info"):
                error_payload["balance_info"] = result["balance_info"]
            if result.get("message"):
                error_payload["message"] = result["message"]

            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.ERROR,
                "payload": error_payload,
            })

    except Exception as e:
        logger.error(f"[ROUTING] Process EXCEPTION: {e}", exc_info=True)

        # Rollback optimistic update if present
        if optimistic_update_id:
            await _reject_optimistic_update(uid, optimistic_update_id, "routing", str(e))

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": str(e),
                "code": "PROCESS_ERROR"
            }
        })


async def handle_routing_restart(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.restart WebSocket event.

    Restarts a job that is stuck or needs to be re-processed using centralized job_actions_handler.
    """
    job_id = payload.get("job_id")
    company_id = payload.get("company_id")

    logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
    logger.info(f"[ROUTING] handle_routing_restart - uid={uid} session={session_id}")
    logger.info(f"[ROUTING] → job_id={job_id} company_id={company_id}")

    if not job_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": "No job_id provided",
                "code": "MISSING_JOB_ID"
            }
        })
        return

    try:
        # Get company context
        context = _get_company_context(uid, company_id)
        logger.info(f"[ROUTING] → Got company context - mandate_path exists={bool(context.get('mandate_path'))}")

        # Use centralized job actions handler
        from app.wrappers.job_actions_handler import handle_job_restart
        logger.info(f"[ROUTING] → Calling job_actions_handler.handle_job_restart...")

        result = await handle_job_restart(
            uid=uid,
            job_type="router",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": context.get("mandate_path", ""),
            }
        )

        if result.get("success"):
            logger.info(f"[ROUTING] → Restart SUCCESS - broadcasting ROUTING.RESTARTED")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.RESTARTED,
                "payload": {
                    "success": True,
                    "job_id": job_id,
                    "message": result.get("message", f"Job {job_id} has been successfully reset"),
                    "_optimistic_update_id": payload.get("_optimistic_update_id"),
                }
            })
            logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
        else:
            logger.warning(f"[ROUTING] → Restart FAILED - {result.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.ERROR,
                "payload": {
                    "error": result.get("error", "Restart failed"),
                    "code": result.get("code", "RESTART_ERROR")
                }
            })

    except Exception as e:
        logger.error(f"[ROUTING] Restart EXCEPTION: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": str(e),
                "code": "RESTART_ERROR"
            }
        })


async def handle_routing_stop(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.stop WebSocket event.

    Stops one or more running routing jobs. Uses the centralized job_actions_handler.

    Pessimistic approach: Items remain in current state until external confirmation.
    """
    job_ids = payload.get("job_ids", [])
    single_job_id = payload.get("job_id")
    company_id = payload.get("company_id")

    # Normalize to list
    if single_job_id and not job_ids:
        job_ids = [single_job_id]

    logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
    logger.info(f"[ROUTING] handle_routing_stop - uid={uid} session={session_id}")
    logger.info(f"[ROUTING] → job_ids={job_ids} company_id={company_id}")

    if not job_ids:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": "No jobs specified",
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
            job_type="router",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": context.get("mandate_path", ""),
            }
        )

        if result.get("success"):
            logger.info(f"[ROUTING] → Stop SUCCESS - broadcasting ROUTING.STOPPED")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.STOPPED,
                "payload": {
                    "success": True,
                    "job_ids": job_ids,
                    "message": result.get("message", f"{len(job_ids)} jobs stopped")
                }
            })
            logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
        else:
            logger.warning(f"[ROUTING] → Stop FAILED - {result.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.ERROR,
                "payload": {
                    "error": result.get("error", "Stop failed"),
                    "code": result.get("code", "STOP_ERROR")
                }
            })

    except Exception as e:
        logger.error(f"[ROUTING] Stop EXCEPTION: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": str(e),
                "code": "STOP_ERROR"
            }
        })


async def handle_routing_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.delete WebSocket event.

    Deletes processed/completed routing jobs with full workflow:
    - Delete Firebase notifications
    - Delete approval_pendinglist entries
    - Move files back to Drive input folder
    - Update business cache

    Uses the centralized job_actions_handler.
    """
    job_ids = payload.get("job_ids", [])
    company_id = payload.get("company_id")

    logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
    logger.info(f"[ROUTING] handle_routing_delete - uid={uid} session={session_id}")
    logger.info(f"[ROUTING] → job_ids count={len(job_ids)} company_id={company_id}")

    if not job_ids:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": "No jobs specified",
                "code": "NO_JOBS"
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
            job_type="router",
            payload=payload,
            company_data={
                "company_id": company_id,
                "mandate_path": context.get("mandate_path", ""),
                "input_drive_doc_id": context.get("input_drive_doc_id", ""),
            }
        )

        if result.get("success"):
            logger.info(
                f"[ROUTING] → Delete SUCCESS - deleted={len(result.get('deleted_jobs', []))} "
                f"moved_to_todo={len(result.get('moved_to_todo', []))}"
            )
            logger.info(f"[ROUTING] → Broadcasting ROUTING.DELETED")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.DELETED,
                "payload": {
                    "success": True,
                    "job_ids": result.get("deleted_jobs", job_ids),
                    "moved_to_todo": result.get("moved_to_todo", []),
                    "message": result.get("message", f"{len(job_ids)} documents deleted"),
                    "_optimistic_update_id": payload.get("_optimistic_update_id"),
                }
            })
            logger.info(f"[ROUTING] ──────────────────────────────────────────────────────")
        else:
            logger.warning(f"[ROUTING] → Delete FAILED - {result.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ROUTING.ERROR,
                "payload": {
                    "error": result.get("error", "Delete failed"),
                    "code": result.get("code", "DELETE_ERROR")
                }
            })

    except Exception as e:
        logger.error(f"[ROUTING] Delete EXCEPTION: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": str(e),
                "code": "DELETE_ERROR"
            }
        })


async def handle_routing_oauth_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle routing.oauth_init WebSocket event.

    Initiates OAuth flow for Google Drive re-authentication.
    Generates the real Google OAuth URL with Drive-only scopes
    and sends it to the frontend for popup redirect.
    """
    import base64
    import json as _json
    company_id = payload.get("company_id")
    logger.info(f"[ROUTING] OAuth init requested for uid={uid} company_id={company_id}")

    # Get context from SessionStateManager
    context = _get_company_context(uid, company_id)
    mandate_path = context.get("mandate_path", "")

    try:
        # 1. Generate OAuth URL with Drive-only scopes
        from pinnokio_app.logique_metier.onboarding_flow import GoogleAuthManager

        auth_manager = GoogleAuthManager(user_id=uid)
        auth_manager.set_drive_only_scopes()

        # Build state as base64-encoded JSON (expected by google_auth_callback)
        state_data = {
            "user_id": uid,
            "source": "routing",
            "communication_mode": "pinnokio",
            "redirect_uri": os.getenv("GOOGLE_AUTH_REDIRECT_LOCAL", ""),
            "context_params": {
                "mandate_path": mandate_path,
                "company_id": company_id,
                "session_id": session_id,
            },
        }
        state_str = base64.b64encode(
            _json.dumps(state_data).encode("utf-8")
        ).decode("utf-8")

        auth_url = auth_manager.get_authorization_url(state_str)
        logger.info(f"[ROUTING] OAuth URL generated for uid={uid}")

        # 2. Send OAuth URL to frontend for popup redirect
        await hub.broadcast(uid, {
            "type": WS_EVENTS.AUTH.OAUTH_REDIRECT,
            "payload": {
                "provider": "google_drive",
                "auth_url": auth_url,
                "scopes": list(auth_manager.SCOPES),
            }
        })

    except Exception as e:
        logger.error(f"[ROUTING] OAuth init failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ROUTING.ERROR,
            "payload": {
                "error": str(e),
                "code": "OAUTH_INIT_ERROR"
            }
        })


# ============================================
# OPTIMISTIC UPDATE HELPERS
# ============================================

async def _confirm_optimistic_update(
    uid: str,
    update_id: str,
    module: str,
    company_id: str,
    mandate_path: str,
) -> None:
    """
    Confirm an optimistic update after successful backend operation.

    This fetches fresh metrics and sends metrics.update_confirmed event.
    The frontend can then update its store with the actual counts.
    """
    try:
        from ..metrics.orchestration import confirm_optimistic_update
        from ..metrics.handlers import get_metrics_handlers

        # Get fresh metrics after the operation
        handlers = get_metrics_handlers()
        result = await handlers.module_data(
            user_id=uid,
            company_id=company_id,
            module=module,
            mandate_path=mandate_path,
            force_refresh=True,  # Force fresh data
        )

        new_counts = result.get("data", {}) if result.get("success") else {}

        await confirm_optimistic_update(uid, update_id, module, new_counts)
        logger.info(f"[ROUTING] Optimistic update confirmed: {update_id}")

    except Exception as e:
        logger.error(f"[ROUTING] Failed to confirm optimistic update: {e}")


async def _reject_optimistic_update(
    uid: str,
    update_id: str,
    module: str,
    error: str,
) -> None:
    """
    Reject an optimistic update after failed backend operation.

    This triggers rollback on the frontend - the UI reverts to previous state.
    """
    try:
        from ..metrics.orchestration import reject_optimistic_update

        await reject_optimistic_update(uid, update_id, module, error)
        logger.info(f"[ROUTING] Optimistic update rejected: {update_id}")

    except Exception as e:
        logger.error(f"[ROUTING] Failed to reject optimistic update: {e}")
