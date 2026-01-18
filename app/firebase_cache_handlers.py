"""
Handlers RPC pour les données Firebase avec cache Redis intégré.

Ces handlers implémentent la stratégie cache-first pour les données Firebase:
    - APBookkeeper documents
    - Expenses details
    - Bank transactions
    - Approval pending list
    - Company/Mandate snapshot

NAMESPACE: FIREBASE_CACHE

Architecture:
    Frontend (Reflex) → rpc_call("FIREBASE_CACHE.get_expenses", ...)
                     → POST /rpc
                     → firebase_cache_handlers.get_expenses()
                     → Redis Cache (HIT) | Firebase (MISS)

Endpoints disponibles:
    - FIREBASE_CACHE.get_mandate_snapshot     → Snapshot mandat (TTL 1h)
    - FIREBASE_CACHE.get_expenses             → Liste des dépenses (TTL 40min)
    - FIREBASE_CACHE.get_ap_documents         → Documents APBookkeeper (TTL 40min)
    - FIREBASE_CACHE.get_bank_transactions    → Transactions bancaires (TTL 40min)
    - FIREBASE_CACHE.get_approval_pendinglist → Liste approbations (TTL 40min)
    - FIREBASE_CACHE.invalidate_cache         → Invalidation manuelle

Note: user_id est injecté automatiquement par main.py si non fourni.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .cache.unified_cache_manager import get_firebase_cache_manager
from .llm_service.redis_namespaces import RedisTTL
from .firebase_client import get_firestore

logger = logging.getLogger("firebase.cache_handlers")


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _convert_timestamps(data: Any) -> Any:
    """
    Recursively convert Firebase timestamps (DatetimeWithNanoseconds) to ISO strings.
    This ensures data can be JSON serialized for Redis cache.
    """
    if data is None:
        return None

    # Handle DatetimeWithNanoseconds and datetime objects
    if hasattr(data, 'isoformat'):
        return data.isoformat()

    # Handle Firestore Timestamp objects
    if hasattr(data, 'seconds') and hasattr(data, 'nanoseconds'):
        try:
            return datetime.fromtimestamp(data.seconds, tz=timezone.utc).isoformat()
        except Exception:
            return str(data)

    # Handle dictionaries recursively
    if isinstance(data, dict):
        return {key: _convert_timestamps(value) for key, value in data.items()}

    # Handle lists recursively
    if isinstance(data, list):
        return [_convert_timestamps(item) for item in data]

    return data


# ═══════════════════════════════════════════════════════════════
# CONSTANTES TTL
# ═══════════════════════════════════════════════════════════════

TTL_MANDATE_SNAPSHOT = 3600      # 1 heure
TTL_EXPENSES = 2400              # 40 minutes
TTL_AP_DOCUMENTS = 2400          # 40 minutes
TTL_BANK_TRANSACTIONS = 2400     # 40 minutes
TTL_APPROVAL_PENDINGLIST = 2400  # 40 minutes


class FirebaseCacheHandlers:
    """
    Handlers RPC pour le namespace FIREBASE_CACHE.

    Chaque méthode correspond à un endpoint RPC:
    - FIREBASE_CACHE.get_mandate_snapshot → get_mandate_snapshot()
    - FIREBASE_CACHE.get_expenses → get_expenses()
    - etc.

    Toutes les méthodes sont asynchrones.
    """

    NAMESPACE = "FIREBASE_CACHE"

    # ═══════════════════════════════════════════════════════════════
    # MANDATE SNAPSHOT
    # ═══════════════════════════════════════════════════════════════

    async def get_mandate_snapshot(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Récupère le snapshot mandat depuis Firebase avec cache.

        RPC: FIREBASE_CACHE.get_mandate_snapshot

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID

        Returns:
            {"data": {...}, "source": "cache"|"firebase"}
        """
        try:
            # 1. Tentative cache
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "mandate",
                "snapshot",
                ttl_seconds=TTL_MANDATE_SNAPSHOT
            )

            if cached and cached.get("data"):
                logger.info(
                    f"FIREBASE_CACHE.get_mandate_snapshot company_id={company_id} "
                    f"source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase
            db = get_firestore()
            mandate_ref = db.collection("mandates").document(company_id)
            mandate_doc = mandate_ref.get()

            if not mandate_doc.exists:
                logger.warning(
                    f"FIREBASE_CACHE.get_mandate_snapshot company_id={company_id} "
                    f"not_found"
                )
                return {"data": None, "source": "firebase"}

            mandate_data = mandate_doc.to_dict()
            logger.info(
                f"FIREBASE_CACHE.get_mandate_snapshot company_id={company_id} "
                f"source=firebase"
            )

            # 3. Sync vers Redis
            await cache.set_cached_data(
                user_id,
                company_id,
                "mandate",
                "snapshot",
                mandate_data,
                ttl_seconds=TTL_MANDATE_SNAPSHOT
            )

            return {
                "data": mandate_data,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_mandate_snapshot error={e}")
            return {"data": None, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # EXPENSES
    # ═══════════════════════════════════════════════════════════════

    async def get_expenses(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Récupère les dépenses depuis Firebase avec cache.

        RPC: FIREBASE_CACHE.get_expenses

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID

        Returns:
            {"data": [...], "source": "cache"|"firebase"}
        """
        try:
            # 1. Tentative cache
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "expenses",
                "details",
                ttl_seconds=TTL_EXPENSES
            )

            if cached and cached.get("data"):
                logger.info(
                    f"FIREBASE_CACHE.get_expenses company_id={company_id} "
                    f"count={len(cached['data']) if isinstance(cached['data'], list) else 0} "
                    f"source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase
            db = get_firestore()
            expenses_ref = db.collection("mandates").document(company_id).collection("expenses")
            expenses_docs = expenses_ref.stream()

            expenses = []
            for doc in expenses_docs:
                expense_data = doc.to_dict()
                expense_data["id"] = doc.id
                expenses.append(expense_data)

            logger.info(
                f"FIREBASE_CACHE.get_expenses company_id={company_id} "
                f"count={len(expenses)} source=firebase"
            )

            # 3. Sync vers Redis
            if expenses:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "expenses",
                    "details",
                    expenses,
                    ttl_seconds=TTL_EXPENSES
                )

            return {
                "data": expenses,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_expenses error={e}")
            return {"data": [], "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # AP BOOKKEEPER DOCUMENTS
    # ═══════════════════════════════════════════════════════════════

    async def get_ap_documents(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str = None
    ) -> Dict[str, Any]:
        """
        Récupère les documents APBookkeeper depuis Firebase avec cache.

        RPC: FIREBASE_CACHE.get_ap_documents

        Uses FireBaseManagement.fetch_journal_entries_by_mandat_id() which queries:
        - clients/{user_id}/klk_vision/{departement_doc}/journal
        - Filtered by mandat_id (company_id) and source

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company/Mandate ID (used as mandat_id filter)
            mandate_path (str): Unused - kept for API compatibility

        Returns:
            {"data": {"to_do": [...], "in_process": [...], "pending": [...], "processed": [...]}, "source": "cache"|"firebase"}
        """
        import asyncio

        try:
            # 1. Tentative cache
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "apbookeeper",
                "documents",
                ttl_seconds=TTL_AP_DOCUMENTS
            )

            if cached and cached.get("data"):
                data = cached["data"]
                if isinstance(data, dict):
                    total = sum(len(v) for v in data.values() if isinstance(v, list))
                else:
                    total = len(data) if isinstance(data, list) else 0
                logger.info(
                    f"FIREBASE_CACHE.get_ap_documents company_id={company_id} "
                    f"count={total} source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase - use FireBaseManagement like Reflex does
            from .firebase_providers import get_firebase_management
            firebase_mgmt = get_firebase_management()
            departement = 'APbookeeper'

            # Fetch TO_DO documents
            todo_docs = await asyncio.to_thread(
                firebase_mgmt.fetch_journal_entries_by_mandat_id,
                user_id,
                company_id,  # mandat_id
                'documents/accounting/invoices/doc_to_do',  # source
                departement
            )

            # Fetch PROCESSED/BOOKED documents
            booked_docs = await asyncio.to_thread(
                firebase_mgmt.fetch_journal_entries_by_mandat_id,
                user_id,
                company_id,  # mandat_id
                'documents/invoices/doc_booked',  # source
                departement
            )

            # Fetch PENDING documents
            pending_docs = []
            try:
                pending_docs = await asyncio.to_thread(
                    firebase_mgmt.fetch_pending_journal_entries_by_mandat_id,
                    user_id,
                    company_id,  # mandat_id
                    'documents/accounting/invoices/doc_to_do',  # source
                    departement
                )
            except Exception as e:
                logger.debug(f"FIREBASE_CACHE.get_ap_documents pending_fetch_error={e}")

            # Process documents and check job status
            items_to_do = []
            items_in_process = []
            items_pending = []
            items_processed = []

            # Process TO_DO documents
            for doc in todo_docs:
                doc_data = doc.get('data', {}) if isinstance(doc, dict) else {}
                doc_data['id'] = doc.get('id', '') if isinstance(doc, dict) else ''

                job_id = doc_data.get('job_id', '')
                if job_id:
                    notification = firebase_mgmt.check_job_status(user_id, job_id)
                    if notification and notification.get('function_name') == 'APbookeeper':
                        status = notification.get('status', '')
                        if status in ['running', 'in queue', 'stopping']:
                            doc_data['status'] = status
                            items_in_process.append(doc_data)
                            continue
                        elif status == 'pending':
                            doc_data['status'] = 'pending'
                            items_pending.append(doc_data)
                            continue

                items_to_do.append(doc_data)

            # Process PENDING documents
            for doc in pending_docs:
                doc_data = doc.get('data', {}) if isinstance(doc, dict) else {}
                doc_data['id'] = doc.get('id', '') if isinstance(doc, dict) else ''

                job_id = doc_data.get('job_id', '')
                if job_id:
                    notification = firebase_mgmt.check_job_status(user_id, job_id)
                    if notification and notification.get('function_name') == 'APbookeeper':
                        if notification.get('status') == 'pending':
                            doc_data['status'] = 'pending'
                            items_pending.append(doc_data)

            # Process BOOKED documents
            for doc in booked_docs:
                doc_data = doc.get('data', {}) if isinstance(doc, dict) else {}
                doc_data['id'] = doc.get('id', '') if isinstance(doc, dict) else ''
                doc_data['status'] = 'completed'
                items_processed.append(doc_data)

            organized = {
                "to_do": items_to_do,
                "in_process": items_in_process,
                "pending": items_pending,
                "processed": items_processed
            }

            total_count = len(items_to_do) + len(items_in_process) + len(items_pending) + len(items_processed)
            logger.info(
                f"FIREBASE_CACHE.get_ap_documents company_id={company_id} "
                f"count={total_count} (to_do={len(items_to_do)}, in_process={len(items_in_process)}, "
                f"pending={len(items_pending)}, processed={len(items_processed)}) source=firebase"
            )

            # Convert timestamps to JSON-serializable format
            organized = _convert_timestamps(organized)

            # 3. Sync vers Redis
            if total_count > 0:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "apbookeeper",
                    "documents",
                    organized,
                    ttl_seconds=TTL_AP_DOCUMENTS
                )

            return {
                "data": organized,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_ap_documents error={e}")
            import traceback
            traceback.print_exc()
            return {"data": {"to_do": [], "in_process": [], "pending": [], "processed": []}, "error": str(e)}

    def _organize_ap_documents_by_status(self, documents: List[Dict]) -> Dict[str, List]:
        """Organize AP documents by status for metrics widget."""
        organized = {
            "to_do": [],
            "in_process": [],
            "pending": [],
            "processed": []
        }

        for doc in documents:
            status = doc.get("status", "").lower()
            if status in ["to_do", "todo", "new", "unprocessed"]:
                organized["to_do"].append(doc)
            elif status in ["in_process", "processing", "in_progress"]:
                organized["in_process"].append(doc)
            elif status in ["pending", "waiting", "approval"]:
                organized["pending"].append(doc)
            elif status in ["processed", "done", "completed", "booked"]:
                organized["processed"].append(doc)
            else:
                # Default to to_do for unknown status
                organized["to_do"].append(doc)

        return organized

    # ═══════════════════════════════════════════════════════════════
    # BANK TRANSACTIONS
    # ═══════════════════════════════════════════════════════════════

    async def get_bank_transactions(
        self,
        user_id: str,
        company_id: str,
        client_uuid: str = None,
        bank_erp: str = None,
        mandate_path: str = None  # Kept for backward compatibility
    ) -> Dict[str, Any]:
        """
        Récupère les transactions bancaires depuis ERP (Odoo) avec cache.

        RPC: FIREBASE_CACHE.get_bank_transactions

        Bank transactions come from ERP (Odoo), NOT from Firebase.
        Uses ERPService.get_odoo_bank_statement_move_line_not_rec()

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company/Mandate ID
            client_uuid (str): Client UUID for ERP connection
            bank_erp (str): ERP type (e.g., "odoo")
            mandate_path (str): Unused - kept for API compatibility

        Returns:
            {"data": {"to_reconcile": [...], "in_process": [...], "pending": [...], "matched": [...]}, "source": "cache"|"erp"}
        """
        import asyncio

        try:
            # 1. Tentative cache
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "bank",
                "transactions",
                ttl_seconds=TTL_BANK_TRANSACTIONS
            )

            if cached and cached.get("data"):
                data = cached["data"]
                if isinstance(data, dict):
                    total = sum(len(v) for v in data.values() if isinstance(v, list))
                else:
                    total = len(data) if isinstance(data, list) else 0
                logger.info(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"count={total} source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback ERP - bank transactions come from Odoo, not Firebase
            bank_erp_type = (bank_erp or "").lower()

            # Check if we have ERP configuration
            if not bank_erp_type:
                logger.warning(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"no_bank_erp_configured"
                )
                return {
                    "data": {"to_reconcile": [], "in_process": [], "pending": [], "matched": []},
                    "source": "none",
                    "warning": "No bank ERP configured"
                }

            # Only Odoo is supported for now
            if bank_erp_type != "odoo":
                logger.info(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"unsupported_erp={bank_erp_type}"
                )
                return {
                    "data": {"to_reconcile": [], "in_process": [], "pending": [], "matched": []},
                    "source": "none",
                    "warning": f"ERP type '{bank_erp_type}' not yet supported. Only Odoo is available."
                }

            if not client_uuid:
                logger.warning(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"no_client_uuid"
                )
                return {
                    "data": {"to_reconcile": [], "in_process": [], "pending": [], "matched": []},
                    "source": "none",
                    "warning": "No client UUID for ERP connection"
                }

            # Fetch from Odoo ERP using singleton
            try:
                from .erp_service import ERPService

                # Call ERP service to get unreconciled bank transactions
                # This uses cached ERP connections (30 min TTL)
                bank_transactions = await asyncio.to_thread(
                    ERPService.get_odoo_bank_statement_move_line_not_rec,
                    user_id,
                    company_id,
                    client_uuid,
                    None,  # journal_id - None = all bank accounts
                    False  # reconciled - False = unreconciled only
                )

                logger.info(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"count={len(bank_transactions)} source=erp"
                )

            except Exception as erp_error:
                logger.error(
                    f"FIREBASE_CACHE.get_bank_transactions erp_error={erp_error}"
                )
                return {
                    "data": {"to_reconcile": [], "in_process": [], "pending": [], "matched": []},
                    "source": "none",
                    "error": str(erp_error)
                }

            # Organize transactions by reconciliation status
            organized = self._organize_bank_transactions_by_status(bank_transactions)

            total_count = sum(len(v) for v in organized.values() if isinstance(v, list))
            logger.info(
                f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                f"total={total_count} (to_reconcile={len(organized.get('to_reconcile', []))}, "
                f"matched={len(organized.get('matched', []))}) source=erp"
            )

            # 3. Sync vers Redis
            if total_count > 0:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "bank",
                    "transactions",
                    organized,
                    ttl_seconds=TTL_BANK_TRANSACTIONS
                )

            return {
                "data": organized,
                "source": "erp"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_bank_transactions error={e}")
            import traceback
            traceback.print_exc()
            return {"data": {"to_reconcile": [], "in_process": [], "pending": [], "matched": []}, "error": str(e)}

    def _organize_bank_transactions_by_status(self, transactions: List[Dict]) -> Dict[str, List]:
        """Organize bank transactions by reconciliation status for metrics widget."""
        organized = {
            "to_reconcile": [],
            "in_process": [],
            "pending": [],
            "matched": []
        }

        for tx in transactions:
            # Check reconciliation status
            reconciled = tx.get("reconciled", False)
            status = tx.get("status", "").lower()
            reconciliation_status = tx.get("reconciliation_status", "").lower()

            if reconciled or status in ["matched", "reconciled", "done"]:
                organized["matched"].append(tx)
            elif status in ["in_process", "processing"] or reconciliation_status in ["in_process", "processing"]:
                organized["in_process"].append(tx)
            elif status in ["pending", "waiting"] or reconciliation_status in ["pending", "waiting"]:
                organized["pending"].append(tx)
            else:
                # Default to to_reconcile
                organized["to_reconcile"].append(tx)

        return organized

    # ═══════════════════════════════════════════════════════════════
    # APPROVAL PENDING LIST
    # ═══════════════════════════════════════════════════════════════

    async def get_approval_pendinglist(
        self,
        user_id: str,
        company_id: str,
        department: str
    ) -> Dict[str, Any]:
        """
        Récupère la liste d'approbation en attente depuis Firebase avec cache.

        RPC: FIREBASE_CACHE.get_approval_pendinglist

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            department (str): Department code

        Returns:
            {"data": [...], "source": "cache"|"firebase"}
        """
        try:
            # 1. Tentative cache
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "approval_pendinglist",
                department,
                ttl_seconds=TTL_APPROVAL_PENDINGLIST
            )

            if cached and cached.get("data"):
                logger.info(
                    f"FIREBASE_CACHE.get_approval_pendinglist company_id={company_id} "
                    f"department={department} "
                    f"count={len(cached['data']) if isinstance(cached['data'], list) else 0} "
                    f"source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase
            db = get_firestore()
            approval_ref = (
                db.collection("mandates")
                .document(company_id)
                .collection("approval_waitlist")
                .where("department", "==", department)
                .where("status", "==", "pending")
            )
            approval_docs = approval_ref.stream()

            pending_items = []
            for doc in approval_docs:
                item_data = doc.to_dict()
                item_data["id"] = doc.id
                pending_items.append(item_data)

            logger.info(
                f"FIREBASE_CACHE.get_approval_pendinglist company_id={company_id} "
                f"department={department} count={len(pending_items)} source=firebase"
            )

            # 3. Sync vers Redis
            if pending_items:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "approval_pendinglist",
                    department,
                    pending_items,
                    ttl_seconds=TTL_APPROVAL_PENDINGLIST
                )

            return {
                "data": pending_items,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_approval_pendinglist error={e}")
            return {"data": [], "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # CACHE INVALIDATION
    # ═══════════════════════════════════════════════════════════════

    async def invalidate_cache(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None
    ) -> Dict[str, Any]:
        """
        Invalide une entrée de cache spécifique.

        RPC: FIREBASE_CACHE.invalidate_cache

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            data_type (str): Type de données (expenses, apbookeeper, bank, etc.)
            sub_type (str, optional): Sous-type (details, documents, transactions, etc.)

        Returns:
            {"success": bool}
        """
        try:
            cache = get_firebase_cache_manager()
            success = await cache.invalidate_cache(
                user_id,
                company_id,
                data_type,
                sub_type
            )

            logger.info(
                f"FIREBASE_CACHE.invalidate_cache user_id={user_id} "
                f"company_id={company_id} data_type={data_type} "
                f"sub_type={sub_type} success={success}"
            )

            return {"success": success}

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.invalidate_cache error={e}")
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

firebase_cache_handlers = FirebaseCacheHandlers()


def get_firebase_cache_handlers() -> FirebaseCacheHandlers:
    """Retourne l'instance singleton des handlers Firebase cache."""
    return firebase_cache_handlers
