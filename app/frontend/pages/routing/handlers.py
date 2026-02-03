"""
Routing Page RPC Handlers
=========================

RPC endpoints for ROUTING.* namespace.

Architecture:
    Frontend (Next.js) -> wsClient.send({ type: 'routing.*', payload })
                       -> Backend handler
                       -> Redis Cache (HIT) | Firebase/Drive (MISS)
                       -> WSS Response

Endpoints:
    - ROUTING.list              -> List documents by category
    - ROUTING.process           -> Process selected documents
    - ROUTING.restart           -> Restart a job
    - ROUTING.refresh           -> Refresh current tab data
    - ROUTING.instructions_save -> Save document instructions
    - ROUTING.toggle_selection  -> Toggle document selection
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.cache.unified_cache_manager import get_firebase_cache_manager
from app.firebase_providers import get_firebase_management
from app.ws_events import WS_EVENTS

logger = logging.getLogger("routing.handlers")

# ===============================================
# CONSTANTES TTL
# ===============================================

TTL_ROUTING_LIST = 60          # 1 minute pour liste de documents
TTL_ROUTING_FULL = 120         # 2 minutes pour donnees completes

# ===============================================
# SINGLETON
# ===============================================

_routing_handlers_instance: Optional["RoutingHandlers"] = None


def get_routing_handlers() -> "RoutingHandlers":
    """Singleton accessor pour les handlers routing."""
    global _routing_handlers_instance
    if _routing_handlers_instance is None:
        _routing_handlers_instance = RoutingHandlers()
    return _routing_handlers_instance


class RoutingHandlers:
    """
    RPC handlers pour le namespace ROUTING.

    Chaque methode correspond a un endpoint RPC:
    - ROUTING.list -> list_documents()
    - ROUTING.process -> process_documents()
    - ROUTING.restart -> restart_job()
    - ROUTING.refresh -> refresh_tab()
    """

    NAMESPACE = "ROUTING"

    # ===============================================
    # LIST DOCUMENTS
    # ===============================================

    async def list_documents(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        input_drive_id: Optional[str] = None,
        category: str = "all",
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_direction: str = "asc",
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        ROUTING.list - Fetch documents by category.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            input_drive_id: Google Drive folder ID for documents
            category: "unprocessed", "in_process", "pending", "processed", or "all"
            page: Current page number (1-indexed)
            page_size: Items per page
            search: Search filter
            sort_column: Column to sort by
            sort_direction: "asc" or "desc"
            force_refresh: Bypass cache

        Returns:
            {
                "success": True,
                "data": {
                    "unprocessed": [...],
                    "in_process": [...],
                    "pending": [...],
                    "processed": [...],
                    "counts": {
                        "unprocessed": 10,
                        "in_process": 5,
                        "pending": 3,
                        "processed": 25
                    },
                    "pagination": {
                        "page": 1,
                        "pageSize": 20,
                        "totalPages": 2,
                        "totalItems": 43
                    }
                },
                "from_cache": bool
            }
        """
        try:
            cache = get_firebase_cache_manager()
            cache_key = f"routing:list:{company_id}:{category}"

            # 1. Check cache (unless force_refresh)
            if not force_refresh:
                try:
                    cached = await cache.get_cached_data(
                        user_id,
                        company_id,
                        "routing",
                        f"list_{category}",
                        ttl_seconds=TTL_ROUTING_LIST
                    )
                    if cached and cached.get("data"):
                        logger.info(f"[ROUTING] Cache hit for list_{category}")
                        # Apply client-side pagination/filtering to cached data
                        return self._apply_filters(
                            cached["data"],
                            page, page_size, search, sort_column, sort_direction,
                            from_cache=True
                        )
                except Exception as cache_err:
                    logger.warning(f"[ROUTING] Cache read error: {cache_err}")

            # 2. Fetch from Drive (with Firebase status check) and Firebase journal
            logger.info(f"[ROUTING] Fetching documents for company={company_id}, category={category}")

            # Fetch all document categories from Drive with Firebase status sorting
            unprocessed = []
            in_process = []
            pending = []
            processed = []

            if category == "all" or category in ("unprocessed", "in_process", "pending"):
                # Use drive_cache_handlers which now does correct sorting via check_job_status
                drive_docs = await self._fetch_drive_documents_with_status(
                    user_id, company_id, input_drive_id
                )
                if drive_docs:
                    unprocessed = drive_docs.get("to_process", [])
                    in_process = drive_docs.get("in_process", [])
                    pending = drive_docs.get("pending", [])

            if category == "all" or category == "processed":
                # Fetch processed documents from Firebase journal (like Router.py)
                firebase_mgmt = get_firebase_management()
                processed_docs = await asyncio.to_thread(
                    firebase_mgmt.fetch_journal_entries_by_mandat_id_without_source,
                    user_id,
                    company_id,  # mandat_id
                    'Router'  # departement
                )
                if processed_docs and isinstance(processed_docs, list):
                    for doc in processed_docs:
                        doc_data = doc.get('data', {}) if isinstance(doc, dict) else {}
                        # Filtrer uniquement les statuts de succès: completed, routed, success, close
                        # (comme dans l'ancien code g_cred.py ligne 6615)
                        status = doc_data.get('status', '')
                        if status in ['completed', 'routed', 'success', 'close']:
                            processed.append({
                                "id": doc.get('firebase_doc_id', ''),
                                "job_id": doc_data.get('job_id', ''),
                                "file_name": doc_data.get('file_name', ''),
                                "status": status,
                                "timestamp": str(doc_data.get('timestamp', '')),
                                "source": doc_data.get('source', ''),
                            })

            # Build result
            data = {
                "unprocessed": unprocessed,
                "in_process": in_process,
                "pending": pending,
                "processed": processed,
                "counts": {
                    "unprocessed": len(unprocessed),
                    "in_process": len(in_process),
                    "pending": len(pending),
                    "processed": len(processed),
                }
            }

            # 3. Cache result
            try:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "routing",
                    f"list_{category}",
                    data,
                    ttl_seconds=TTL_ROUTING_LIST
                )
            except Exception as cache_err:
                logger.warning(f"[ROUTING] Cache write error: {cache_err}")

            # 4. Apply pagination/filtering and return
            return self._apply_filters(
                data, page, page_size, search, sort_column, sort_direction,
                from_cache=False
            )

        except Exception as e:
            logger.error(f"[ROUTING] list_documents error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {
                    "code": "ROUTING_LIST_ERROR",
                    "message": str(e)
                }
            }

    async def _fetch_drive_documents_with_status(
        self,
        user_id: str,
        company_id: str,
        input_drive_id: Optional[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch documents from Google Drive with Firebase status sorting.

        Uses drive_cache_handlers.get_documents() which:
        - Checks Redis cache first
        - On cache miss: fetches from Drive API + checks Firebase notifications
        - Returns correctly sorted: to_process, in_process, pending
        """
        if not input_drive_id:
            return {"to_process": [], "in_process": [], "pending": []}

        try:
            from app.drive_cache_handlers import get_drive_cache_handlers

            drive_handlers = get_drive_cache_handlers()
            result = await drive_handlers.get_documents(
                user_id=user_id,
                company_id=company_id,
                input_drive_id=input_drive_id
            )

            if result and result.get("data"):
                return result["data"]
            return {"to_process": [], "in_process": [], "pending": []}
        except Exception as e:
            logger.error(f"[ROUTING] Drive fetch error: {e}")
            return {"to_process": [], "in_process": [], "pending": []}

    def _apply_filters(
        self,
        data: Dict[str, Any],
        page: int,
        page_size: int,
        search: Optional[str],
        sort_column: Optional[str],
        sort_direction: str,
        from_cache: bool
    ) -> Dict[str, Any]:
        """Apply search, sort, and pagination to document data."""
        # Get the appropriate list based on current needs
        # For now, return all data with pagination info
        total_items = sum(data["counts"].values())
        total_pages = (total_items + page_size - 1) // page_size if page_size > 0 else 1

        return {
            "success": True,
            "data": {
                **data,
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalPages": total_pages,
                    "totalItems": total_items,
                }
            },
            "from_cache": from_cache
        }

    # ===============================================
    # PROCESS DOCUMENTS
    # ===============================================

    async def process_documents(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        document_ids: List[str],
        general_instructions: Optional[str] = None,
        document_instructions: Optional[Dict[str, str]] = None,
        approval_states: Optional[Dict[str, bool]] = None,
        workflow_states: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        """
        ROUTING.process - Process selected documents.

        Sends documents to the appropriate department for processing.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            document_ids: List of document IDs to process
            general_instructions: General instructions for all documents
            document_instructions: Per-document specific instructions
            approval_states: Per-document approval requirements (AAA)
            workflow_states: Per-document automated workflow flags

        Returns:
            {"success": True, "processed": [...], "failed": [...]}
        """
        try:
            if not document_ids:
                return {
                    "success": False,
                    "error": {"code": "NO_DOCUMENTS", "message": "No documents selected"}
                }

            firebase_mgmt = get_firebase_management()
            processed = []
            failed = []

            for doc_id in document_ids:
                try:
                    # Get document-specific settings
                    instructions = (document_instructions or {}).get(doc_id, general_instructions or "")
                    needs_approval = (approval_states or {}).get(doc_id, False)
                    auto_workflow = (workflow_states or {}).get(doc_id, False)

                    # Create job in Firebase
                    result = await asyncio.to_thread(
                        firebase_mgmt.create_routing_job,
                        user_id,
                        company_id,
                        mandate_path,
                        doc_id,
                        instructions=instructions,
                        approval_required=needs_approval,
                        automated_workflow=auto_workflow
                    )

                    if result.get("success"):
                        processed.append(doc_id)
                    else:
                        failed.append({"id": doc_id, "error": result.get("error", "Unknown error")})

                except Exception as doc_err:
                    failed.append({"id": doc_id, "error": str(doc_err)})

            # Invalidate cache after processing
            cache = get_firebase_cache_manager()
            await cache.delete_cached_data(user_id, company_id, "routing", "list_all")

            return {
                "success": True,
                "processed": processed,
                "failed": failed,
                "summary": {
                    "totalProcessed": len(processed),
                    "totalFailed": len(failed)
                }
            }

        except Exception as e:
            logger.error(f"[ROUTING] process_documents error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "PROCESS_ERROR", "message": str(e)}
            }

    # ===============================================
    # RESTART JOB
    # ===============================================

    async def restart_job(
        self,
        user_id: str,
        company_id: str,
        job_id: str,
        mandate_path: str
    ) -> Dict[str, Any]:
        """
        ROUTING.restart - Restart a processing job.

        Resets a job to initial state, clearing events and chat history.

        Args:
            user_id: Firebase UID
            company_id: Company ID (base_collection_id)
            job_id: Job ID to restart
            mandate_path: Firebase mandate path

        Returns:
            {"success": True, "job_id": "..."}
        """
        try:
            firebase_mgmt = get_firebase_management()

            # 1. Restart job in Firebase (clears events)
            restart_success = await asyncio.to_thread(
                firebase_mgmt.restart_job,
                user_id,
                job_id
            )

            if not restart_success:
                return {
                    "success": False,
                    "error": {"code": "RESTART_FAILED", "message": f"Failed to restart job {job_id}"}
                }

            # 2. Clear Realtime chats
            try:
                from app.firebase_realtime import get_firebase_realtime_chat
                realtime_service = get_firebase_realtime_chat()

                await asyncio.to_thread(
                    realtime_service.erase_chat,
                    space_code=company_id,
                    thread_key=job_id,
                    mode='job_chats'
                )
                await asyncio.to_thread(
                    realtime_service.erase_chat,
                    space_code=company_id,
                    thread_key=job_id,
                    mode='active_chats'
                )
            except Exception as chat_err:
                logger.warning(f"[ROUTING] Failed to clear chats: {chat_err}")

            # 3. Invalidate cache
            cache = get_firebase_cache_manager()
            await cache.delete_cached_data(user_id, company_id, "routing", "list_all")

            return {
                "success": True,
                "job_id": job_id,
                "message": f"Job {job_id} has been successfully reset"
            }

        except Exception as e:
            logger.error(f"[ROUTING] restart_job error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "RESTART_ERROR", "message": str(e)}
            }

    # ===============================================
    # SAVE INSTRUCTIONS
    # ===============================================

    async def save_instructions(
        self,
        user_id: str,
        company_id: str,
        document_id: str,
        instructions: str
    ) -> Dict[str, Any]:
        """
        ROUTING.instructions_save - Save instructions for a document.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            document_id: Document ID
            instructions: Instructions text

        Returns:
            {"success": True, "document_id": "..."}
        """
        try:
            # Instructions are typically stored in memory/Redis for session
            # and applied when processing the document
            cache = get_firebase_cache_manager()

            await cache.set_cached_data(
                user_id,
                company_id,
                "routing",
                f"instructions_{document_id}",
                {"instructions": instructions},
                ttl_seconds=3600  # 1 hour
            )

            return {
                "success": True,
                "document_id": document_id,
                "message": "Instructions saved"
            }

        except Exception as e:
            logger.error(f"[ROUTING] save_instructions error: {e}")
            return {
                "success": False,
                "error": {"code": "SAVE_ERROR", "message": str(e)}
            }

    # ===============================================
    # REFRESH DOCUMENTS
    # ===============================================

    async def refresh_documents(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        input_drive_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        ROUTING.refresh - Force refresh documents from source (bypass cache).

        This method:
        1. Invalidates Drive cache
        2. Invalidates routing cache
        3. Re-fetches all documents from Drive API + Firebase

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            input_drive_id: Google Drive folder ID

        Returns:
            Same structure as list_documents but with fresh data
        """
        try:
            logger.info(f"[ROUTING] Refresh requested for company={company_id}")

            # 1. Invalidate Drive cache and fetch fresh data
            if input_drive_id:
                from app.drive_cache_handlers import get_drive_cache_handlers
                drive_handlers = get_drive_cache_handlers()
                drive_result = await drive_handlers.refresh_documents(
                    user_id=user_id,
                    company_id=company_id,
                    input_drive_id=input_drive_id
                )
            else:
                drive_result = {"data": {"to_process": [], "in_process": [], "pending": []}}

            # 2. Invalidate routing cache
            await self.invalidate_cache(user_id, company_id)

            # 3. Fetch processed documents fresh from Firebase
            processed = []
            try:
                firebase_mgmt = get_firebase_management()
                processed_docs = await asyncio.to_thread(
                    firebase_mgmt.fetch_journal_entries_by_mandat_id_without_source,
                    user_id,
                    company_id,
                    'Router'
                )
                if processed_docs and isinstance(processed_docs, list):
                    for doc in processed_docs:
                        doc_data = doc.get('data', {}) if isinstance(doc, dict) else {}
                        # Filtrer uniquement les statuts de succès: completed, routed, success, close
                        # (comme dans l'ancien code g_cred.py ligne 6615)
                        status = doc_data.get('status', '')
                        if status in ['completed', 'routed', 'success', 'close']:
                            processed.append({
                                "id": doc.get('firebase_doc_id', ''),
                                "job_id": doc_data.get('job_id', ''),
                                "file_name": doc_data.get('file_name', ''),
                                "status": status,
                                "timestamp": str(doc_data.get('timestamp', '')),
                                "source": doc_data.get('source', ''),
                            })
            except Exception as fb_err:
                logger.warning(f"[ROUTING] Failed to fetch processed docs: {fb_err}")

            # 4. Build response with fresh data
            drive_data = drive_result.get("data", {}) if drive_result else {}
            unprocessed = drive_data.get("to_process", [])
            in_process = drive_data.get("in_process", [])
            pending = drive_data.get("pending", [])

            data = {
                "unprocessed": unprocessed,
                "in_process": in_process,
                "pending": pending,
                "processed": processed,
                "counts": {
                    "unprocessed": len(unprocessed),
                    "in_process": len(in_process),
                    "pending": len(pending),
                    "processed": len(processed),
                }
            }

            logger.info(
                f"[ROUTING] Refresh complete: "
                f"unprocessed={len(unprocessed)}, in_process={len(in_process)}, "
                f"pending={len(pending)}, processed={len(processed)}"
            )

            return {
                "success": True,
                "data": data,
                "refreshed": True
            }

        except Exception as e:
            logger.error(f"[ROUTING] refresh_documents error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "REFRESH_ERROR", "message": str(e)}
            }

    # ===============================================
    # INVALIDATE CACHE
    # ===============================================

    async def invalidate_cache(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        ROUTING.invalidate_cache - Invalidate routing cache.

        Args:
            user_id: Firebase UID
            company_id: Company ID

        Returns:
            {"success": True, "invalidated": [...]}
        """
        try:
            cache = get_firebase_cache_manager()

            keys_to_invalidate = [
                ("routing", "list_all"),
                ("routing", "list_unprocessed"),
                ("routing", "list_in_process"),
                ("routing", "list_pending"),
                ("routing", "list_processed"),
            ]

            invalidated = []
            for category, sub_key in keys_to_invalidate:
                try:
                    await cache.delete_cached_data(user_id, company_id, category, sub_key)
                    invalidated.append(f"{category}:{sub_key}")
                except Exception:
                    pass

            logger.info(f"[ROUTING] Cache invalidated: {invalidated}")
            return {"success": True, "invalidated": invalidated}

        except Exception as e:
            logger.error(f"[ROUTING] invalidate_cache error: {e}")
            return {"success": False, "error": {"message": str(e)}}
