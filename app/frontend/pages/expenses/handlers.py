"""
Expenses Page RPC Handlers
==========================

Handlers for expenses.* namespace.
Pattern: Cache -> Fetch -> Transform -> Cache -> Return

Endpoints:
    - expenses.list      -> List expenses by category
    - expenses.refresh   -> Refresh expenses data
    - expenses.close     -> Close an expense
    - expenses.reopen    -> Reopen a closed expense
    - expenses.update    -> Update expense fields
    - expenses.delete    -> Delete an expense

Status Flow:
    to_process (open) -> running -> close (closed)
                      -> close (manual close)
    close -> to_process (reopen)

Integration with Centralized Cache:
    All status-changing actions (close, reopen, delete) use ListManager
    to update the business cache optimistically instead of invalidating it.
    This ensures metrics are always up-to-date without refetching from Firebase.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.redis_client import get_redis
from app.domain_config import ListManager, get_domain_config

logger = logging.getLogger("expenses.handlers")

# Cache TTLs
TTL_EXPENSES = 2400  # 40 minutes (match firebase_cache_handlers.py)

# ===============================================
# SINGLETON INSTANCE
# ===============================================

_expenses_handlers_instance: Optional["ExpensesHandlers"] = None


def get_expenses_handlers() -> "ExpensesHandlers":
    """Get singleton instance of ExpensesHandlers."""
    global _expenses_handlers_instance
    if _expenses_handlers_instance is None:
        _expenses_handlers_instance = ExpensesHandlers()
    return _expenses_handlers_instance


class ExpensesHandlers:
    """
    RPC handlers for the EXPENSES namespace.

    Each method corresponds to an RPC endpoint:
    - EXPENSES.list -> list_expenses()
    - EXPENSES.refresh -> refresh_expenses()
    - EXPENSES.close -> close_expense()
    - EXPENSES.reopen -> reopen_expense()
    - EXPENSES.update -> update_expense()
    - EXPENSES.delete -> delete_expense()
    """

    NAMESPACE = "EXPENSES"

    def __init__(self):
        self._redis = get_redis()

    # ===============================================
    # LIST EXPENSES
    # ===============================================

    async def list_expenses(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        EXPENSES.list - Fetch expenses with cache.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            force_refresh: Bypass cache if True

        Returns:
            {
                "success": True,
                "data": {
                    "to_process": [...],
                    "in_process": [...],
                    "pending": [...],
                    "processed": [...],
                    "metrics": {
                        "totalToProcess": int,
                        "totalInProcess": int,
                        "totalPending": int,
                        "totalProcessed": int,
                        "totalAmount": float
                    }
                },
                "from_cache": bool
            }
        """
        cache_key = f"business:{user_id}:{company_id}:expenses"

        # 1. Check cache (unless force_refresh)
        if not force_refresh:
            try:
                cached = self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached if isinstance(cached, str) else cached.decode())
                    logger.info(f"[EXPENSES] Cache hit for {cache_key}")
                    return {"success": True, "data": data, "from_cache": True}
            except Exception as e:
                logger.warning(f"[EXPENSES] Cache read error: {e}")

        # 2. Fetch from task_manager (primary source) with legacy fallback
        raw_data = await self._fetch_from_task_manager(user_id, mandate_path)

        # 3. Transform (normalize status, group by status)
        transformed = self._transform_expenses(raw_data)

        # 4. Cache
        try:
            self._redis.setex(cache_key, TTL_EXPENSES, json.dumps(transformed))
            logger.info(f"[EXPENSES] Data cached: {cache_key} TTL={TTL_EXPENSES}s")
        except Exception as e:
            logger.warning(f"[EXPENSES] Cache write error: {e}")

        return {"success": True, "data": transformed, "from_cache": False}

    async def refresh_expenses(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
    ) -> Dict[str, Any]:
        """
        EXPENSES.refresh - Invalidate cache and reload from source.
        Used by: widget refresh button, page refresh button.
        """
        cache_key = f"business:{user_id}:{company_id}:expenses"

        # 1. Invalidate cache
        try:
            self._redis.delete(cache_key)
            logger.info(f"[EXPENSES] Cache invalidated: {cache_key}")
        except Exception as e:
            logger.warning(f"[EXPENSES] Cache delete error: {e}")

        # 2. Fetch fresh data (force_refresh=True bypasses cache check)
        return await self.list_expenses(user_id, company_id, mandate_path, force_refresh=True)

    # ===============================================
    # CLOSE EXPENSE
    # ===============================================

    async def close_expense(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        expense_id: str,
    ) -> Dict[str, Any]:
        """
        EXPENSES.close - Update status to 'close'.

        Uses ListManager for optimistic cache update:
        1. Update Firebase (source of truth)
        2. Apply status change to business cache using ListManager
        3. Return updated metrics for dashboard sync

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            expense_id: ID of expense to close

        Returns:
            {
                "success": True,
                "expense_id": str,
                "cache_update": {...},  # ListManager result
                "metrics": {...}        # Updated metrics for dashboard
            }
        """
        try:
            # 1. Best-effort: update legacy Firebase (may not exist for migrated expenses)
            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()

            legacy_ok = firebase.update_expense_in_firebase(
                mandate_path, expense_id, {"status": "close"}
            )
            if not legacy_ok:
                logger.warning(f"[EXPENSES] Legacy Firebase update skipped for {expense_id} (migrated?)")

            # 1b. Source de vérité: task_manager (TOUJOURS exécuté)
            self._update_task_manager(user_id, expense_id, {"status": "processed"})

            # 2. Apply optimistic update to business cache
            cache_result = self._apply_list_change(
                user_id=user_id,
                company_id=company_id,
                expense_id=expense_id,
                new_status="close",
                action="close",
            )

            logger.info(
                f"[EXPENSES] Closed expense: {expense_id} "
                f"cache_update={cache_result is not None}"
            )

            return {
                "success": True,
                "expense_id": expense_id,
                "cache_update": cache_result,
                "metrics": cache_result.get("metrics") if cache_result else None,
            }

        except Exception as e:
            logger.error(f"[EXPENSES] Close expense failed: {e}")
            return {"success": False, "error": str(e)}

    # ===============================================
    # REOPEN EXPENSE
    # ===============================================

    async def reopen_expense(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        expense_id: str,
    ) -> Dict[str, Any]:
        """
        EXPENSES.reopen - Update status to 'to_process'.

        Uses ListManager for optimistic cache update:
        1. Update Firebase (source of truth)
        2. Apply status change to business cache using ListManager
        3. Return updated metrics for dashboard sync

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            expense_id: ID of expense to reopen

        Returns:
            {
                "success": True,
                "expense_id": str,
                "cache_update": {...},
                "metrics": {...}
            }
        """
        try:
            logger.info(f"[EXPENSES][FLOW] ────────────────────────────────────────────")
            logger.info(f"[EXPENSES][FLOW] reopen_expense handler called")
            logger.info(f"[EXPENSES][FLOW] expense_id={expense_id}")

            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()

            # Best-effort: update legacy Firebase (may not exist for migrated expenses)
            logger.info(f"[EXPENSES][FLOW] STEP 2a: Updating legacy Firebase (status -> to_process)")
            legacy_ok = firebase.update_expense_in_firebase(
                mandate_path, expense_id, {"status": "to_process"}
            )
            logger.info(f"[EXPENSES][FLOW] Legacy Firebase update result: {legacy_ok}")
            if not legacy_ok:
                logger.warning(f"[EXPENSES][FLOW] Legacy Firebase update skipped for {expense_id} (migrated?)")

            # Source de vérité: task_manager (TOUJOURS exécuté)
            self._update_task_manager(user_id, expense_id, {"status": "to_process"})

            # Apply optimistic update to business cache
            logger.info(f"[EXPENSES][FLOW] STEP 2b: Applying list change via ListManager")
            cache_result = self._apply_list_change(
                user_id=user_id,
                company_id=company_id,
                expense_id=expense_id,
                new_status="to_process",
                action="reopen",
            )

            logger.info(
                f"[EXPENSES][FLOW] ListManager result: "
                f"success={cache_result is not None}, "
                f"from_list={cache_result.get('from_list') if cache_result else 'N/A'}, "
                f"to_list={cache_result.get('to_list') if cache_result else 'N/A'}"
            )
            if cache_result:
                logger.info(f"[EXPENSES][FLOW] Updated metrics: {cache_result.get('metrics')}")
            else:
                logger.error(f"[EXPENSES][FLOW] ❌ ListManager returned None - item not found in cache!")
            logger.info(f"[EXPENSES][FLOW] ────────────────────────────────────────────")

            return {
                "success": True,
                "expense_id": expense_id,
                "cache_update": cache_result,
                "metrics": cache_result.get("metrics") if cache_result else None,
            }

        except Exception as e:
            logger.error(f"[EXPENSES][FLOW] ❌ Exception in reopen_expense: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ===============================================
    # UPDATE EXPENSE
    # ===============================================

    async def update_expense(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        expense_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        EXPENSES.update - Update expense fields.

        Uses ListManager for optimistic cache update when status changes.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            expense_id: ID of expense to update
            update_data: Dict of fields to update

        Returns:
            {
                "success": True,
                "expense_id": str,
                "cache_update": {...},
                "metrics": {...}
            }
        """
        try:
            # Remove non-updatable fields
            safe_data = {
                k: v for k, v in update_data.items()
                if k not in ['expense_id', 'job_id', 'file_name', 'drive_file_id']
            }

            if not safe_data:
                return {"success": False, "error": "No valid fields to update"}

            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()

            # Best-effort: update legacy Firebase (may not exist for migrated expenses)
            legacy_ok = firebase.update_expense_in_firebase(
                mandate_path, expense_id, safe_data
            )
            if not legacy_ok:
                logger.warning(f"[EXPENSES] Legacy Firebase update skipped for {expense_id} (migrated?)")

            # Source de vérité: task_manager (TOUJOURS exécuté)
            tm_updates = {}
            new_status = safe_data.get("status")
            if new_status:
                tm_updates["status"] = self._normalize_status(new_status)
            # Also cascade non-status fields into department_data.EXbookeeper
            dept_fields = {k: v for k, v in safe_data.items() if k != "status"}
            if dept_fields:
                for k, v in dept_fields.items():
                    tm_updates[f"department_data.EXbookeeper.{k}"] = v
            if tm_updates:
                self._update_task_manager(user_id, expense_id, tm_updates)

            # Apply optimistic update to business cache
            # If status changed, use ListManager to move between lists
            if new_status:
                cache_result = self._apply_list_change(
                    user_id=user_id,
                    company_id=company_id,
                    expense_id=expense_id,
                    new_status=new_status,
                    action="update",
                    extra_data=safe_data,
                )
            else:
                # No status change - just update the item in place
                cache_result = self._update_item_in_cache(
                    user_id=user_id,
                    company_id=company_id,
                    expense_id=expense_id,
                    update_data=safe_data,
                )

            logger.info(
                f"[EXPENSES] Updated expense: {expense_id} "
                f"fields={list(safe_data.keys())} cache_update={cache_result is not None}"
            )

            return {
                "success": True,
                "expense_id": expense_id,
                "cache_update": cache_result,
                "metrics": cache_result.get("metrics") if cache_result else None,
            }

        except Exception as e:
            logger.error(f"[EXPENSES] Update expense failed: {e}")
            return {"success": False, "error": str(e)}

    # ===============================================
    # DELETE EXPENSE
    # ===============================================

    async def delete_expense(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        expense_id: str,
        job_id: Optional[str] = None,
        drive_file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        EXPENSES.delete - Delete expense (multi-service).

        Uses ListManager to remove item from cache optimistically.

        Full deletion includes:
        - Transfer Drive file to input folder
        - Delete from ChromaDB
        - Delete from task_manager
        - Delete from expenses_details

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            expense_id: ID of expense to delete
            job_id: Optional job ID for deletion
            drive_file_id: Optional Drive file ID for cleanup

        Returns:
            {
                "success": True,
                "expense_id": str,
                "cache_update": {...},
                "metrics": {...}
            }
        """
        try:
            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()

            # BEFORE remove: retrieve item info for cross-update
            item_info = self._find_item_in_cache(user_id, company_id, expense_id)
            file_name = (item_info or {}).get("file_name", "")
            effective_drive_id = drive_file_id or (item_info or {}).get("drive_file_id")

            # Best-effort: delete from legacy Firebase (may not exist for migrated expenses)
            legacy_ok = firebase.delete_expense_from_firebase(mandate_path, expense_id)
            if not legacy_ok:
                logger.warning(f"[EXPENSES] Legacy Firebase delete skipped for {expense_id} (migrated?)")

            # Delete from task_manager (source of truth)
            self._delete_from_task_manager(user_id, expense_id)

            # Apply optimistic update to business cache (remove item)
            cache_result = self._remove_item_from_cache(
                user_id=user_id,
                company_id=company_id,
                expense_id=expense_id,
            )

            # Cross-update routing cache if doc has a real Drive file ID
            if effective_drive_id and not effective_drive_id.startswith("klk_"):
                self._cross_update_routing_cache(
                    user_id, company_id, file_name, effective_drive_id
                )

            logger.info(
                f"[EXPENSES] Deleted expense: {expense_id} "
                f"cache_update={cache_result is not None} "
                f"routing_cross_update={bool(effective_drive_id and not (effective_drive_id or '').startswith('klk_'))}"
            )

            return {
                "success": True,
                "expense_id": expense_id,
                "cache_update": cache_result,
                "metrics": cache_result.get("metrics") if cache_result else None,
            }

        except Exception as e:
            logger.error(f"[EXPENSES] Delete expense failed: {e}")
            return {"success": False, "error": str(e)}

    # ===============================================
    # PRIVATE HELPERS
    # ===============================================

    async def _fetch_from_task_manager(self, user_id: str, mandate_path: str) -> Dict[str, Any]:
        """
        Fetch expenses from task_manager (source unique de verite).

        Queries clients/{uid}/task_manager where department=EXbookeeper
        and mandate_path matches.

        Falls back to legacy expenses_details if task_manager returns nothing.

        Returns:
            Dict {expense_id: expense_data} (same format as legacy for compatibility
            with _transform_expenses).
        """
        try:
            from app.firebase_client import get_firestore
            from google.cloud.firestore_v1.base_query import FieldFilter

            db = get_firestore()
            task_mgr_ref = db.collection(f"clients/{user_id}/task_manager")
            query = task_mgr_ref.where(
                filter=FieldFilter("department", "in", ["EXbookeeper", "exbookeeper"])
            )

            expenses_data = {}
            for doc in query.stream():
                data = doc.to_dict() or {}
                doc_mandate = data.get("mandate_path", "")

                # Filter by mandate_path (exact match)
                if doc_mandate != mandate_path:
                    continue

                # Extract department_data.EXbookeeper as the expense content
                dept_data = data.get("department_data", {})
                expense_entry = (
                    dept_data.get("EXbookeeper", {})
                    or dept_data.get("exbookeeper", {})
                    or {}
                )

                # Merge root-level fields into the expense entry
                expense_entry["status"] = data.get("status", expense_entry.get("status", ""))
                expense_entry["file_name"] = data.get("file_name", expense_entry.get("file_name", ""))
                expense_entry["file_id"] = data.get("file_id", expense_entry.get("file_id", ""))
                expense_entry["mandate_path"] = doc_mandate

                expenses_data[doc.id] = expense_entry

            if expenses_data:
                logger.info(
                    f"[EXPENSES] Fetched {len(expenses_data)} expenses from task_manager "
                    f"(user={user_id}, mandate={mandate_path[-30:]}...)"
                )
                return expenses_data

            # Fallback: legacy expenses_details collection
            logger.warning(
                f"[EXPENSES] task_manager returned 0 expenses, trying legacy collection"
            )
            from app.firebase_providers import get_firebase_management
            firebase = get_firebase_management()
            legacy_data = firebase.fetch_expenses_by_mandate(mandate_path) or {}
            if legacy_data:
                logger.info(f"[EXPENSES] Legacy fallback returned {len(legacy_data)} expenses")
            return legacy_data

        except Exception as e:
            logger.error(f"[EXPENSES] _fetch_from_task_manager error: {e}", exc_info=True)
            return {}

    def _transform_expenses(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform and normalize expenses data.

        Groups expenses by normalized status:
        - to_process: to_process, open, draft
        - in_process: running, in_process, processing
        - pending: pending
        - processed: close, closed, completed
        """
        to_process_list: List[Dict] = []
        in_process_list: List[Dict] = []
        pending_list: List[Dict] = []
        processed_list: List[Dict] = []
        total_amount = 0.0

        for expense_id, expense in raw_data.items():
            if not isinstance(expense, dict):
                continue

            status = self._normalize_status(expense.get("status", "to_process"))
            item = {
                "expense_id": expense_id,
                **expense,
                "status": status,
            }

            if status == "processed":
                processed_list.append(item)
            elif status == "in_process":
                in_process_list.append(item)
            elif status == "pending":
                pending_list.append(item)
            else:
                to_process_list.append(item)
                # Sum amounts for to_process expenses
                try:
                    amount = float(expense.get("amount", 0) or 0)
                    total_amount += amount
                except (ValueError, TypeError):
                    pass

        # Sort by date (most recent first)
        for lst in [to_process_list, in_process_list, pending_list, processed_list]:
            lst.sort(
                key=lambda x: x.get("date") or x.get("created_at") or "",
                reverse=True
            )

        return {
            "to_process": to_process_list,
            "in_process": in_process_list,
            "pending": pending_list,
            "processed": processed_list,
            "metrics": {
                "totalToProcess": len(to_process_list),
                "totalInProcess": len(in_process_list),
                "totalPending": len(pending_list),
                "totalProcessed": len(processed_list),
                "totalAmount": round(total_amount, 2),
            }
        }

    def _normalize_status(self, status: str) -> str:
        """
        Normalize status to canonical values.

        Maps:
        - to_process, open, draft -> to_process
        - running, in_process, processing -> in_process
        - pending -> pending
        - close, closed, completed -> processed
        """
        if not status:
            return "to_process"

        status_lower = status.lower().strip()

        if status_lower in ("close", "closed", "completed", "processed"):
            return "processed"
        elif status_lower in ("running", "in_process", "processing"):
            return "in_process"
        elif status_lower == "pending":
            return "pending"
        else:
            return "to_process"

    # ===============================================
    # CACHE MANIPULATION HELPERS (ListManager Integration)
    # ===============================================

    def _apply_list_change(
        self,
        user_id: str,
        company_id: str,
        expense_id: str,
        new_status: str,
        action: str,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Apply status change to business cache using ListManager.

        This method:
        1. Reads the current business cache
        2. Uses ListManager to move the item between lists
        3. Saves the updated cache back to Redis
        4. Returns the result with updated metrics

        Args:
            user_id: Firebase UID
            company_id: Company ID
            expense_id: ID of the expense to update
            new_status: New status to assign
            action: Action name (close, reopen, etc.)
            extra_data: Optional extra data to merge into the item

        Returns:
            Dict with:
            - success: bool
            - from_list: str
            - to_list: str
            - metrics: Dict with updated counts
        """
        cache_key = f"business:{user_id}:{company_id}:expenses"

        try:
            logger.info(f"[EXPENSES][FLOW] _apply_list_change START")
            logger.info(f"[EXPENSES][FLOW] cache_key: {cache_key}")
            logger.info(f"[EXPENSES][FLOW] expense_id: {expense_id}, new_status: {new_status}, action: {action}")

            # 1. Read current cache
            cached = self._redis.get(cache_key)
            if not cached:
                logger.error(f"[EXPENSES][FLOW] ❌ No business cache found: {cache_key}")
                return None

            cache_data = json.loads(cached if isinstance(cached, str) else cached.decode())

            # Log current state BEFORE change
            logger.info(f"[EXPENSES][FLOW] Cache BEFORE ListManager:")
            logger.info(f"[EXPENSES][FLOW]   to_process: {len(cache_data.get('to_process', []))} items")
            logger.info(f"[EXPENSES][FLOW]   in_process: {len(cache_data.get('in_process', []))} items")
            logger.info(f"[EXPENSES][FLOW]   pending: {len(cache_data.get('pending', []))} items")
            logger.info(f"[EXPENSES][FLOW]   processed: {len(cache_data.get('processed', []))} items")
            logger.info(f"[EXPENSES][FLOW]   metrics: {cache_data.get('metrics', {})}")

            # Log expense_ids in each list for debugging
            for list_name in ['to_process', 'in_process', 'pending', 'processed']:
                ids = [item.get('expense_id') for item in cache_data.get(list_name, [])]
                logger.info(f"[EXPENSES][FLOW]   {list_name} expense_ids: {ids[:5]}{'...' if len(ids) > 5 else ''}")

            # 2. Use ListManager to apply status change
            logger.info(f"[EXPENSES][FLOW] Calling ListManager.apply_status_change()")
            result = ListManager.apply_status_change(
                domain="expenses",
                cache_data=cache_data,
                item_ids=[expense_id],
                new_status=new_status,
                action=action,
                extra_data=extra_data,
            )

            logger.info(f"[EXPENSES][FLOW] ListManager result.success: {result.success}")
            logger.info(f"[EXPENSES][FLOW] ListManager result.from_list: {result.from_list}")
            logger.info(f"[EXPENSES][FLOW] ListManager result.to_list: {result.to_list}")
            logger.info(f"[EXPENSES][FLOW] ListManager result.items_moved: {len(result.items_moved)}")
            if result.error:
                logger.error(f"[EXPENSES][FLOW] ListManager result.error: {result.error}")

            if not result.success:
                logger.error(f"[EXPENSES][FLOW] ❌ ListManager failed: {result.error}")
                return None

            # 3. Recalculate metrics from updated lists
            metrics = self._calculate_metrics(cache_data)
            cache_data["metrics"] = metrics

            # Log state AFTER change
            logger.info(f"[EXPENSES][FLOW] Cache AFTER ListManager:")
            logger.info(f"[EXPENSES][FLOW]   to_process: {len(cache_data.get('to_process', []))} items")
            logger.info(f"[EXPENSES][FLOW]   in_process: {len(cache_data.get('in_process', []))} items")
            logger.info(f"[EXPENSES][FLOW]   pending: {len(cache_data.get('pending', []))} items")
            logger.info(f"[EXPENSES][FLOW]   processed: {len(cache_data.get('processed', []))} items")
            logger.info(f"[EXPENSES][FLOW]   metrics: {metrics}")

            # 4. Save updated cache back to Redis
            self._redis.setex(cache_key, TTL_EXPENSES, json.dumps(cache_data))
            logger.info(f"[EXPENSES][FLOW] ✓ Cache saved to Redis: {cache_key}")

            logger.info(f"[EXPENSES][FLOW] _apply_list_change END")

            return {
                "success": True,
                "from_list": result.from_list,
                "to_list": result.to_list,
                "new_status": result.new_status,
                "items_moved": len(result.items_moved),
                "metrics": metrics,
            }

        except Exception as e:
            logger.error(f"[EXPENSES][FLOW] ❌ _apply_list_change exception: {e}", exc_info=True)
            return None

    def _update_item_in_cache(
        self,
        user_id: str,
        company_id: str,
        expense_id: str,
        update_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Update an item in the cache without changing its list position.

        Used when updating fields that don't affect status.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            expense_id: ID of the expense to update
            update_data: Fields to update

        Returns:
            Dict with success and metrics
        """
        cache_key = f"business:{user_id}:{company_id}:expenses"

        try:
            cached = self._redis.get(cache_key)
            if not cached:
                logger.warning(f"[EXPENSES] No business cache found: {cache_key}")
                return None

            cache_data = json.loads(cached if isinstance(cached, str) else cached.decode())

            # Find and update item in all lists
            found = False
            for list_name in ["to_process", "in_process", "pending", "processed"]:
                items = cache_data.get(list_name, [])
                for item in items:
                    if item.get("expense_id") == expense_id:
                        item.update(update_data)
                        item["updated_at"] = datetime.now(timezone.utc).isoformat()
                        found = True
                        break
                if found:
                    break

            if not found:
                logger.warning(f"[EXPENSES] Item not found in cache: {expense_id}")
                return None

            # Recalculate metrics (amount may have changed)
            metrics = self._calculate_metrics(cache_data)
            cache_data["metrics"] = metrics

            # Save updated cache
            self._redis.setex(cache_key, TTL_EXPENSES, json.dumps(cache_data))

            logger.info(f"[EXPENSES] Item updated in cache: {expense_id}")

            return {
                "success": True,
                "metrics": metrics,
            }

        except Exception as e:
            logger.error(f"[EXPENSES] _update_item_in_cache failed: {e}", exc_info=True)
            return None

    def _remove_item_from_cache(
        self,
        user_id: str,
        company_id: str,
        expense_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Remove an item from the cache (for delete operations).

        Args:
            user_id: Firebase UID
            company_id: Company ID
            expense_id: ID of the expense to remove

        Returns:
            Dict with success and updated metrics
        """
        cache_key = f"business:{user_id}:{company_id}:expenses"

        try:
            cached = self._redis.get(cache_key)
            if not cached:
                logger.warning(f"[EXPENSES] No business cache found: {cache_key}")
                return None

            cache_data = json.loads(cached if isinstance(cached, str) else cached.decode())

            # Remove item from all lists
            found = False
            for list_name in ["to_process", "in_process", "pending", "processed"]:
                items = cache_data.get(list_name, [])
                original_len = len(items)
                cache_data[list_name] = [
                    item for item in items
                    if item.get("expense_id") != expense_id
                ]
                if len(cache_data[list_name]) < original_len:
                    found = True
                    break

            if not found:
                logger.warning(f"[EXPENSES] Item not found for deletion: {expense_id}")
                # Still continue - item may have been deleted elsewhere

            # Recalculate metrics
            metrics = self._calculate_metrics(cache_data)
            cache_data["metrics"] = metrics

            # Save updated cache
            self._redis.setex(cache_key, TTL_EXPENSES, json.dumps(cache_data))

            logger.info(f"[EXPENSES] Item removed from cache: {expense_id}")

            return {
                "success": True,
                "metrics": metrics,
            }

        except Exception as e:
            logger.error(f"[EXPENSES] _remove_item_from_cache failed: {e}", exc_info=True)
            return None

    # ===============================================
    # TASK_MANAGER WRITE HELPERS
    # ===============================================

    def _update_task_manager(self, user_id: str, expense_id: str, updates: Dict[str, Any]) -> None:
        """
        Update a document in task_manager (source of truth for expenses).

        Args:
            user_id: Firebase UID
            expense_id: Document ID in task_manager
            updates: Dict of fields to update (supports dot notation for nested fields)
        """
        try:
            from app.firebase_client import get_firestore
            db = get_firestore()
            doc_ref = db.collection(f"clients/{user_id}/task_manager").document(expense_id)
            doc = doc_ref.get()
            if doc.exists:
                doc_ref.update(updates)
                logger.info(f"[EXPENSES] task_manager updated: {expense_id} fields={list(updates.keys())}")
            else:
                logger.warning(f"[EXPENSES] task_manager doc not found: {expense_id} (skip update)")
        except Exception as e:
            logger.error(f"[EXPENSES] task_manager update failed for {expense_id}: {e}")

    def _delete_from_task_manager(self, user_id: str, expense_id: str) -> None:
        """
        Delete a document from task_manager.

        Args:
            user_id: Firebase UID
            expense_id: Document ID in task_manager
        """
        try:
            from app.firebase_client import get_firestore
            db = get_firestore()
            doc_ref = db.collection(f"clients/{user_id}/task_manager").document(expense_id)
            doc = doc_ref.get()
            if doc.exists:
                doc_ref.delete()
                logger.info(f"[EXPENSES] task_manager deleted: {expense_id}")
            else:
                logger.warning(f"[EXPENSES] task_manager doc not found for delete: {expense_id}")
        except Exception as e:
            logger.error(f"[EXPENSES] task_manager delete failed for {expense_id}: {e}")

    def _find_item_in_cache(
        self,
        user_id: str,
        company_id: str,
        expense_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find an item across all lists in the expenses cache."""
        cache_key = f"business:{user_id}:{company_id}:expenses"
        try:
            cached = self._redis.get(cache_key)
            if not cached:
                return None
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            for list_name in ["to_process", "in_process", "pending", "processed"]:
                for item in data.get(list_name, []):
                    if item.get("expense_id") == expense_id or item.get("id") == expense_id:
                        return item
            return None
        except Exception as e:
            logger.warning(f"[EXPENSES] _find_item_in_cache error: {e}")
            return None

    def _cross_update_routing_cache(
        self,
        user_id: str,
        company_id: str,
        file_name: str,
        drive_file_id: str,
    ) -> None:
        """Add a deleted Drive doc back to the routing cache to_process list."""
        cache_key = f"business:{user_id}:{company_id}:routing"
        try:
            cached = self._redis.get(cache_key)
            if not cached:
                logger.info(f"[EXPENSES] No routing cache to cross-update: {cache_key}")
                return

            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            documents = data.get("documents", data)
            to_process = documents.get("to_process", [])

            # Avoid duplicates
            if any(item.get("drive_file_id") == drive_file_id for item in to_process):
                logger.info(f"[EXPENSES] Drive file {drive_file_id} already in routing to_process")
                return

            new_item = {
                "id": drive_file_id,
                "file_name": file_name,
                "drive_file_id": drive_file_id,
                "status": "to_process",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            to_process.insert(0, new_item)
            documents["to_process"] = to_process

            # Update counts
            counts = data.get("counts", data.get("metrics", {}))
            if "to_process" in counts:
                counts["to_process"] = len(to_process)
            elif "totalToProcess" in counts:
                counts["totalToProcess"] = len(to_process)

            self._redis.setex(cache_key, 1800, json.dumps(data))
            logger.info(
                f"[EXPENSES] Cross-updated routing cache: added {drive_file_id} "
                f"to to_process ({len(to_process)} items)"
            )
        except Exception as e:
            logger.error(f"[EXPENSES] _cross_update_routing_cache error: {e}", exc_info=True)

    def _calculate_metrics(self, cache_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate metrics from cache data.

        Args:
            cache_data: Cache data with to_process, in_process, pending, processed lists

        Returns:
            Metrics dict with totalToProcess, totalInProcess, totalPending, totalProcessed, totalAmount
        """
        to_process_list = cache_data.get("to_process", [])
        in_process_list = cache_data.get("in_process", [])
        pending_list = cache_data.get("pending", [])
        processed_list = cache_data.get("processed", [])

        # Calculate total amount from to_process expenses
        total_amount = 0.0
        for item in to_process_list:
            try:
                amount = float(item.get("amount", 0) or 0)
                total_amount += amount
            except (ValueError, TypeError):
                pass

        return {
            "totalToProcess": len(to_process_list),
            "totalInProcess": len(in_process_list),
            "totalPending": len(pending_list),
            "totalProcessed": len(processed_list),
            "totalAmount": round(total_amount, 2),
        }
