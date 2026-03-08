"""
APBookkeeper/Invoices Page Handlers
====================================

RPC handlers for the INVOICES namespace.

Pattern: Frontend → WS Request → Handler → Redis/Firebase → WS Response

Endpoints:
    - INVOICES.list                -> List invoices by category
    - INVOICES.process             -> Process selected invoices
    - INVOICES.stop                -> Stop processing
    - INVOICES.delete              -> Delete processed invoices
    - INVOICES.refresh             -> Refresh current tab data
    - INVOICES.invalidate_cache    -> Invalidate cache

Status Flow:
    to_process -> in_queue -> on_process -> processed
                                         -> error -> to_process (restart)
                                         -> pending -> in_queue (re-process)
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.cache.unified_cache_manager import get_firebase_cache_manager
from app.firebase_providers import get_firebase_management
from app.ws_events import WS_EVENTS

logger = logging.getLogger("invoices.handlers")

# ===============================================
# SINGLETON INSTANCE
# ===============================================

_invoices_handlers_instance: Optional["InvoicesHandlers"] = None


def get_invoices_handlers() -> "InvoicesHandlers":
    """Get singleton instance of InvoicesHandlers."""
    global _invoices_handlers_instance
    if _invoices_handlers_instance is None:
        _invoices_handlers_instance = InvoicesHandlers()
    return _invoices_handlers_instance


class InvoicesHandlers:
    """
    RPC handlers pour le namespace INVOICES.

    Chaque methode correspond a un endpoint RPC:
    - INVOICES.list -> list_documents()
    - INVOICES.process -> process_documents()
    - INVOICES.delete -> delete_documents()
    - INVOICES.refresh -> refresh_tab()
    - INVOICES.invalidate_cache -> invalidate_cache()
    """

    NAMESPACE = "INVOICES"

    # ===============================================
    # LIST DOCUMENTS
    # ===============================================

    async def list_documents(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        category: str = "all",
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_direction: str = "asc",
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        INVOICES.list - Fetch invoices by category.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            category: "to_process", "in_process", "pending", "processed", or "all"
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
                    "to_process": [...],
                    "in_process": [...],
                    "pending": [...],
                    "processed": [...],
                    "counts": {
                        "to_process": 10,
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

            # Use firebase_cache_handlers which does correct sorting via check_job_status
            from app.firebase_cache_handlers import get_firebase_cache_handlers
            cache_handlers = get_firebase_cache_handlers()

            # Force refresh invalidates cache first
            if force_refresh:
                await cache.delete_cached_data(user_id, company_id, "apbookeeper", "documents")

            ap_result = await cache_handlers.get_ap_documents(
                user_id=user_id,
                company_id=company_id,
                mandate_path=mandate_path
            )

            if not ap_result.get("data"):
                return {
                    "success": True,
                    "data": {
                        "to_process": [],
                        "in_process": [],
                        "pending": [],
                        "processed": [],
                        "counts": {"to_process": 0, "in_process": 0, "pending": 0, "processed": 0},
                        "pagination": {"page": 1, "pageSize": page_size, "totalPages": 0, "totalItems": 0}
                    },
                    "from_cache": False
                }

            data = ap_result["data"]
            to_process = data.get("to_process", [])
            in_process = data.get("in_process", [])
            pending = data.get("pending", [])
            processed = data.get("processed", [])

            # Apply search filter if provided
            if search:
                search_lower = search.lower()
                to_process = [d for d in to_process if self._matches_search(d, search_lower)]
                in_process = [d for d in in_process if self._matches_search(d, search_lower)]
                pending = [d for d in pending if self._matches_search(d, search_lower)]
                processed = [d for d in processed if self._matches_search(d, search_lower)]

            # Build result
            result_data = {
                "to_process": to_process,
                "in_process": in_process,
                "pending": pending,
                "processed": processed,
                "counts": {
                    "to_process": len(to_process),
                    "in_process": len(in_process),
                    "pending": len(pending),
                    "processed": len(processed),
                },
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalPages": max(1, (len(to_process) + len(in_process) + len(pending) + len(processed)) // page_size),
                    "totalItems": len(to_process) + len(in_process) + len(pending) + len(processed),
                }
            }

            logger.info(f"[INVOICES] list_documents: category={category}, total={result_data['pagination']['totalItems']}")

            return {
                "success": True,
                "data": result_data,
                "from_cache": ap_result.get("source") == "cache"
            }

        except Exception as e:
            logger.error(f"[INVOICES] list_documents error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {
                    "message": str(e),
                    "code": "LIST_ERROR"
                }
            }

    def _matches_search(self, doc: Dict, search_term: str) -> bool:
        """Check if document matches search term."""
        searchable_fields = ["file_name", "name", "job_id", "id", "status"]
        for field in searchable_fields:
            value = doc.get(field, "")
            if value and search_term in str(value).lower():
                return True
        return False

    # ===============================================
    # REFRESH TAB
    # ===============================================

    async def refresh_tab(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        category: str = "all",
    ) -> Dict[str, Any]:
        """
        INVOICES.refresh - Force refresh documents from source (bypass cache).

        This method:
        1. Invalidates AP documents cache
        2. Re-fetches all documents from Firebase
        3. Returns fresh data

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            category: Tab category

        Returns:
            {"success": True, "data": {...}}
        """
        try:
            logger.info(f"[INVOICES] Refresh requested for company={company_id}, category={category}")

            # Invalidate cache and fetch fresh data
            return await self.list_documents(
                user_id=user_id,
                company_id=company_id,
                mandate_path=mandate_path,
                category=category,
                force_refresh=True
            )

        except Exception as e:
            logger.error(f"[INVOICES] refresh_tab error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {
                    "message": str(e),
                    "code": "REFRESH_ERROR"
                }
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
        INVOICES.invalidate_cache - Invalidate invoices cache.

        Args:
            user_id: Firebase UID
            company_id: Company ID

        Returns:
            {"success": True, "invalidated": [...]}
        """
        try:
            cache = get_firebase_cache_manager()

            keys_to_invalidate = [
                ("apbookeeper", "documents"),
                ("invoices", "list_all_all"),
                ("invoices", "list_all_to_process"),
                ("invoices", "list_all_in_process"),
                ("invoices", "list_all_pending"),
                ("invoices", "list_all_processed"),
            ]

            invalidated = []
            for category, sub_key in keys_to_invalidate:
                try:
                    await cache.delete_cached_data(user_id, company_id, category, sub_key)
                    invalidated.append(f"{category}:{sub_key}")
                except Exception:
                    pass

            logger.info(f"[INVOICES] Cache invalidated: {invalidated}")
            return {"success": True, "invalidated": invalidated}

        except Exception as e:
            logger.error(f"[INVOICES] invalidate_cache error: {e}")
            return {"success": False, "error": {"message": str(e)}}
