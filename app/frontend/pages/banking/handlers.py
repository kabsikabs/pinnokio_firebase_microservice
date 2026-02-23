"""
Banking Page RPC Handlers
=========================

RPC endpoints for BANKING.* namespace.

Architecture:
    Frontend (Next.js) -> wsClient.send({ type: 'banking.*', payload })
                       -> Backend handler
                       -> Redis Cache CENTRAL (business:{uid}:{cid}:bank)
                       -> WSS Response

IMPORTANT: This handler uses the CENTRAL BUSINESS CACHE (firebase_cache_handlers)
           to ensure consistency with Dashboard metrics.

Cache Strategy:
    - Transactions (to_process, in_process, pending, processed): from firebase_cache_handlers.get_bank_transactions()
      → business:{uid}:{cid}:bank
    - After any action (process/stop/delete): invalidate central cache

Endpoints:
    - BANKING.list                 -> List transactions by category
    - BANKING.process              -> (handled by orchestration.py → job_actions_handler)
    - BANKING.stop                 -> Stop processing
    - BANKING.delete               -> Delete transactions
    - BANKING.instructions_save    -> Save transaction instructions
    - BANKING.refresh              -> Refresh current tab data
    - BANKING.accounts_list        -> List bank accounts

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
from app.firebase_cache_handlers import get_firebase_cache_handlers
from app.ws_events import WS_EVENTS

logger = logging.getLogger("banking.handlers")

# ===============================================
# CONSTANTES TTL
# ===============================================

TTL_BANKING_LIST = 60           # 1 minute pour liste de transactions
TTL_BANKING_FULL = 120          # 2 minutes pour donnees completes
TTL_BANKING_ACCOUNTS = 300      # 5 minutes pour comptes bancaires

# ===============================================
# SINGLETON
# ===============================================

_banking_handlers_instance: Optional["BankingHandlers"] = None


def get_banking_handlers() -> "BankingHandlers":
    """Singleton accessor pour les handlers banking."""
    global _banking_handlers_instance
    if _banking_handlers_instance is None:
        _banking_handlers_instance = BankingHandlers()
    return _banking_handlers_instance


class BankingHandlers:
    """
    RPC handlers pour le namespace BANKING.

    IMPORTANT: This handler uses the CENTRAL BUSINESS CACHE to ensure
    consistency between Banking page and Dashboard metrics.

    Chaque methode correspond a un endpoint RPC:
    - BANKING.list -> list_transactions()
    - BANKING.stop -> stop_processing()
    - BANKING.delete -> delete_transactions()
    - BANKING.accounts_list -> list_accounts()

    NOTE: BANKING.process is handled by orchestration.py → job_actions_handler
    """

    NAMESPACE = "BANKING"

    # ===============================================
    # LIST BANK ACCOUNTS
    # ===============================================

    async def list_accounts(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        BANKING.accounts_list - Fetch bank accounts for the company.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            force_refresh: Bypass cache

        Returns:
            {
                "success": True,
                "data": {
                    "accounts": [
                        {
                            "id": "acc_123",
                            "name": "Main Account",
                            "iban": "CH93 0076 2011 6238 5295 7",
                            "currency": "CHF",
                            "balance": 15000.00,
                            "last_sync": "2024-01-20T10:30:00Z"
                        }
                    ],
                    "total_balance": 45000.00
                },
                "from_cache": bool
            }
        """
        try:
            cache = get_firebase_cache_manager()

            # 1. Check cache (unless force_refresh)
            if not force_refresh:
                try:
                    cached = await cache.get_cached_data(
                        user_id,
                        company_id,
                        "banking",
                        "accounts",
                        ttl_seconds=TTL_BANKING_ACCOUNTS
                    )
                    if cached and cached.get("data"):
                        logger.info(f"[BANKING] Cache hit for accounts")
                        return {
                            "success": True,
                            "data": cached["data"],
                            "from_cache": True
                        }
                except Exception as cache_err:
                    logger.warning(f"[BANKING] Cache read error: {cache_err}")

            # 2. Fetch from ERP/Firebase
            logger.info(f"[BANKING] Fetching accounts for company={company_id}")
            firebase_mgmt = get_firebase_management()

            # Fetch bank accounts from company settings
            accounts_raw = await asyncio.to_thread(
                firebase_mgmt.get_company_bank_accounts,
                user_id,
                company_id,
                mandate_path
            )

            accounts = []
            total_balance = 0.0

            if accounts_raw and isinstance(accounts_raw, list):
                for acc in accounts_raw:
                    balance = float(acc.get('balance', 0) or 0)
                    total_balance += balance
                    accounts.append({
                        "id": acc.get('id', ''),
                        "name": acc.get('name', ''),
                        "iban": acc.get('iban', ''),
                        "currency": acc.get('currency', 'CHF'),
                        "balance": balance,
                        "last_sync": acc.get('last_sync', ''),
                    })

            data = {
                "accounts": accounts,
                "total_balance": total_balance
            }

            # 3. Cache result
            try:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "banking",
                    "accounts",
                    data,
                    ttl_seconds=TTL_BANKING_ACCOUNTS
                )
            except Exception as cache_err:
                logger.warning(f"[BANKING] Cache write error: {cache_err}")

            return {"success": True, "data": data, "from_cache": False}

        except Exception as e:
            logger.error(f"[BANKING] list_accounts error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "ACCOUNTS_LIST_ERROR", "message": str(e)}
            }

    # ===============================================
    # LIST TRANSACTIONS
    # ===============================================

    async def list_transactions(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        client_uuid: str = "",
        bank_erp: str = "",
        account_id: Optional[str] = None,
        category: str = "all",
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        sort_column: Optional[str] = None,
        sort_direction: str = "desc",
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        BANKING.list - Fetch transactions by category.

        IMPORTANT: Uses CENTRAL BUSINESS CACHE (firebase_cache_handlers.get_bank_transactions())
        to ensure consistency with Dashboard metrics.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            client_uuid: Client UUID for ERP connection
            bank_erp: Bank ERP type (e.g., "odoo")
            account_id: Optional filter by bank account
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
                    "batches": [...],
                    "counts": {
                        "to_process": 10,
                        "in_process": 5,
                        "pending": 3,
                        "processed": 25
                    },
                    "pagination": {...}
                },
                "from_cache": bool
            }
        """
        try:
            # ═══════════════════════════════════════════════════════════════
            # STEP 1: Fetch transactions from CENTRAL BUSINESS CACHE
            # Uses firebase_cache_handlers which stores in business:{uid}:{cid}:bank
            # ═══════════════════════════════════════════════════════════════
            cache_handlers = get_firebase_cache_handlers()

            # Force refresh invalidates central cache first
            if force_refresh:
                await cache_handlers.invalidate_cache(user_id, company_id, "bank", "transactions")

            # Fetch from central cache (or source if miss)
            bank_result = await cache_handlers.get_bank_transactions(
                user_id=user_id,
                company_id=company_id,
                client_uuid=client_uuid,
                bank_erp=bank_erp,
                mandate_path=mandate_path,
                force_refresh=force_refresh,
            )

            from_cache = bank_result.get("source") == "cache"
            bank_data = bank_result.get("data", {})

            # All categories now use universal names
            to_process = bank_data.get("to_process", [])
            in_process = bank_data.get("in_process", [])
            pending = bank_data.get("pending", [])

            # Format transactions for UI
            to_process = [self._format_transaction(tx) for tx in to_process]
            in_process = [self._format_transaction(tx) for tx in in_process]
            pending = [self._format_transaction(tx) for tx in pending]

            # Apply account filter if specified
            if account_id:
                to_process = [tx for tx in to_process if tx.get('account_id') == account_id]
                in_process = [tx for tx in in_process if tx.get('account_id') == account_id]
                pending = [tx for tx in pending if tx.get('account_id') == account_id]

            logger.info(
                f"[BANKING] Transactions from central cache: "
                f"to_process={len(to_process)}, in_process={len(in_process)}, "
                f"pending={len(pending)}, source={bank_result.get('source')}"
            )

            # ═══════════════════════════════════════════════════════════════
            # STEP 2: Fetch PROCESSED transactions from journal (separate source)
            # Processed transactions are reconciled and stored in journal, not in
            # the central bank cache. This is intentional - they are a different
            # data source.
            # ═══════════════════════════════════════════════════════════════
            processed = []
            batches = []

            if category == "all" or category == "processed":
                firebase_mgmt = get_firebase_management()
                matched_docs = await asyncio.to_thread(
                    firebase_mgmt.fetch_journal_entries_by_mandat_id_without_source,
                    user_id,
                    company_id,
                    'Bankbookeeper'
                )

                if matched_docs and isinstance(matched_docs, list):
                    for doc in matched_docs:
                        doc_data = doc.get('data', {}) if isinstance(doc, dict) else {}
                        status = doc_data.get('status', '')
                        if status in ['matched', 'completed', 'success', 'close']:
                            # Filter by account if specified
                            if account_id and doc_data.get('account_id') != account_id:
                                continue
                            processed.append({
                                "id": doc.get('firebase_doc_id', ''),
                                "transaction_id": doc_data.get('transaction_id', ''),
                                "job_id": doc_data.get('job_id', ''),
                                "account_id": doc_data.get('account_id', ''),
                                "account_name": doc_data.get('account_name', ''),
                                "date": doc_data.get('date', ''),
                                "description": doc_data.get('description', ''),
                                "reference": doc_data.get('reference', ''),
                                "partner_name": doc_data.get('partner_name', ''),
                                "amount": float(doc_data.get('amount', 0) or 0),
                                "currency": doc_data.get('currency', 'CHF'),
                                "status": 'processed',
                                "matched_invoice": doc_data.get('matched_invoice', ''),
                                "timestamp": str(doc_data.get('timestamp', '')),
                            })

            # ═══════════════════════════════════════════════════════════════
            # STEP 3: Compute active batches on-the-fly from in_process
            # Group by batch_id field present on each item.
            # Batches disappear naturally when all their items leave in_process.
            # ═══════════════════════════════════════════════════════════════
            if category == "all" or category == "in_process":
                batches_map = {}
                for tx in in_process:
                    bid = tx.get("batch_id", "")
                    if bid:
                        batches_map.setdefault(bid, []).append(tx)
                for batch_id, items in batches_map.items():
                    first_item = items[0] if items else {}
                    batches.append({
                        "batch_id": batch_id,
                        "account_id": first_item.get("account_id", ""),
                        "transaction_count": len(items),
                        "status": "running",
                    })

            # ═══════════════════════════════════════════════════════════════
            # STEP 4: Build result
            # ═══════════════════════════════════════════════════════════════
            data = {
                "to_process": to_process,
                "in_process": in_process,
                "pending": pending,
                "processed": processed,
                "batches": batches,
                "counts": {
                    "to_process": len(to_process),
                    "in_process": len(in_process),
                    "pending": len(pending),
                    "processed": len(processed),
                }
            }

            # Apply pagination/filtering and return
            return self._apply_filters(
                data, page, page_size, search, sort_column, sort_direction,
                from_cache=from_cache
            )

        except Exception as e:
            logger.error(f"[BANKING] list_transactions error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "BANKING_LIST_ERROR", "message": str(e)}
            }

    def _format_transaction(self, tx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format a transaction from central cache for UI.

        The central cache (firebase_cache_handlers) may return transactions
        in ERP format. This method normalizes them for the Banking UI.
        """
        # Handle both ERP format and Firebase format
        if isinstance(tx, dict):
            # ERP format fields (from Odoo)
            return {
                "id": str(tx.get('id', tx.get('move_id', ''))),
                "move_id": tx.get('move_id', ''),
                "transaction_id": str(tx.get('id', tx.get('transaction_id', ''))),
                "account_id": str(tx.get('journal_id', tx.get('account_id', ''))),
                "account_name": tx.get('journal_name', tx.get('account_name', '')),
                "date": tx.get('date', ''),
                "description": tx.get('name', tx.get('description', '')),
                "reference": tx.get('ref', tx.get('reference', '')),
                "partner_name": tx.get('partner_name', ''),
                "transaction_type": 'credit' if float(tx.get('amount', 0) or 0) > 0 else 'debit',
                "payment_ref": tx.get('payment_ref', ''),
                "amount": float(tx.get('amount', 0) or 0),
                "currency": tx.get('currency_id', tx.get('currency', 'CHF')),
                "status": tx.get('status', 'to_process'),
                "job_id": tx.get('job_id', ''),
                "created_at": str(tx.get('create_date', tx.get('created_at', ''))),
                "updated_at": str(tx.get('write_date', tx.get('updated_at', ''))),
            }
        return {}

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
        """Apply search, sort, and pagination to transaction data."""
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
    # STOP PROCESSING
    # ===============================================

    async def stop_processing(
        self,
        user_id: str,
        company_id: str,
        transaction_ids: Optional[List[str]] = None,
        batch_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        BANKING.stop - Stop processing transactions or batches.

        After stopping, invalidates the CENTRAL BUSINESS CACHE.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            transaction_ids: Optional list of transaction IDs to stop
            batch_ids: Optional list of batch IDs to stop

        Returns:
            {"success": True, "stopped": [...]}
        """
        try:
            if not transaction_ids and not batch_ids:
                return {
                    "success": False,
                    "error": {"code": "NO_ITEMS", "message": "No transactions or batches to stop"}
                }

            firebase_mgmt = get_firebase_management()
            stopped = []

            # Stop individual transactions
            if transaction_ids:
                for tx_id in transaction_ids:
                    try:
                        result = await asyncio.to_thread(
                            firebase_mgmt.stop_job,
                            user_id,
                            tx_id,
                            'banking'
                        )
                        if result:
                            stopped.append({"type": "transaction", "id": tx_id})
                    except Exception as e:
                        logger.warning(f"[BANKING] Failed to stop transaction {tx_id}: {e}")

            # Stop batches
            if batch_ids:
                for batch_id in batch_ids:
                    try:
                        result = await asyncio.to_thread(
                            firebase_mgmt.stop_batch,
                            user_id,
                            batch_id,
                            'banking'
                        )
                        if result:
                            stopped.append({"type": "batch", "id": batch_id})
                    except Exception as e:
                        logger.warning(f"[BANKING] Failed to stop batch {batch_id}: {e}")

            # ═══════════════════════════════════════════════════════════════
            # CRITICAL: Invalidate CENTRAL BUSINESS CACHE
            # ═══════════════════════════════════════════════════════════════
            await self._invalidate_central_cache(user_id, company_id)

            return {
                "success": True,
                "stopped": stopped,
                "summary": {"totalStopped": len(stopped)}
            }

        except Exception as e:
            logger.error(f"[BANKING] stop_processing error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "STOP_ERROR", "message": str(e)}
            }

    # ===============================================
    # DELETE TRANSACTIONS
    # ===============================================

    async def delete_transactions(
        self,
        user_id: str,
        company_id: str,
        transaction_ids: List[str],
    ) -> Dict[str, Any]:
        """
        BANKING.delete - Delete banking transactions.

        After deleting, invalidates the CENTRAL BUSINESS CACHE.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            transaction_ids: List of transaction IDs to delete

        Returns:
            {"success": True, "deleted": [...], "failed": [...]}
        """
        try:
            if not transaction_ids:
                return {
                    "success": False,
                    "error": {"code": "NO_TRANSACTIONS", "message": "No transactions to delete"}
                }

            firebase_mgmt = get_firebase_management()
            deleted = []
            failed = []

            for tx_id in transaction_ids:
                try:
                    result = await asyncio.to_thread(
                        firebase_mgmt.delete_journal_entry,
                        user_id,
                        tx_id,
                        'Bankbookeeper'
                    )
                    if result:
                        deleted.append(tx_id)
                    else:
                        failed.append({"id": tx_id, "error": "Delete failed"})
                except Exception as e:
                    failed.append({"id": tx_id, "error": str(e)})

            # ═══════════════════════════════════════════════════════════════
            # CRITICAL: Invalidate CENTRAL BUSINESS CACHE
            # ═══════════════════════════════════════════════════════════════
            await self._invalidate_central_cache(user_id, company_id)

            return {
                "success": True,
                "deleted": deleted,
                "failed": failed,
                "summary": {
                    "totalDeleted": len(deleted),
                    "totalFailed": len(failed)
                }
            }

        except Exception as e:
            logger.error(f"[BANKING] delete_transactions error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "DELETE_ERROR", "message": str(e)}
            }

    # ===============================================
    # SAVE INSTRUCTIONS
    # ===============================================

    async def save_instructions(
        self,
        user_id: str,
        company_id: str,
        transaction_id: str,
        instructions: str
    ) -> Dict[str, Any]:
        """
        BANKING.instructions_save - Save instructions for a transaction.

        Note: Instructions are stored separately, not in central cache.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            transaction_id: Transaction ID
            instructions: Instructions text

        Returns:
            {"success": True, "transaction_id": "..."}
        """
        try:
            cache = get_firebase_cache_manager()

            await cache.set_cached_data(
                user_id,
                company_id,
                "banking",
                f"instructions_{transaction_id}",
                {"instructions": instructions},
                ttl_seconds=3600  # 1 hour
            )

            return {
                "success": True,
                "transaction_id": transaction_id,
                "message": "Instructions saved"
            }

        except Exception as e:
            logger.error(f"[BANKING] save_instructions error: {e}")
            return {
                "success": False,
                "error": {"code": "SAVE_ERROR", "message": str(e)}
            }

    # ===============================================
    # REFRESH TRANSACTIONS
    # ===============================================

    async def refresh_transactions(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        client_uuid: str = "",
        bank_erp: str = "",
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        BANKING.refresh - Force refresh transactions from source (bypass cache).

        Invalidates CENTRAL BUSINESS CACHE and re-fetches from source.

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Firebase mandate path
            client_uuid: Client UUID for ERP connection
            bank_erp: Bank ERP type
            account_id: Optional filter by bank account

        Returns:
            Same structure as list_transactions but with fresh data
        """
        try:
            logger.info(f"[BANKING] Refresh requested for company={company_id}, account={account_id}")

            # Invalidate central cache
            await self._invalidate_central_cache(user_id, company_id)

            # Re-fetch with force_refresh
            return await self.list_transactions(
                user_id,
                company_id,
                mandate_path,
                client_uuid=client_uuid,
                bank_erp=bank_erp,
                account_id=account_id,
                category="all",
                force_refresh=True
            )

        except Exception as e:
            logger.error(f"[BANKING] refresh_transactions error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "REFRESH_ERROR", "message": str(e)}
            }

    # ===============================================
    # INVALIDATE CACHE (CENTRAL)
    # ===============================================

    async def _invalidate_central_cache(
        self,
        user_id: str,
        company_id: str
    ) -> None:
        """
        Invalidate the CENTRAL BUSINESS CACHE for banking.

        This is called after any action (process/stop/delete) to ensure
        Dashboard metrics are recalculated from fresh data.

        Cache key: business:{uid}:{cid}:bank (via firebase_cache_handlers)
        """
        try:
            cache_handlers = get_firebase_cache_handlers()
            await cache_handlers.invalidate_cache(
                user_id=user_id,
                company_id=company_id,
                data_type="bank",
                sub_type="transactions"
            )
            logger.info(f"[BANKING] Central cache invalidated: bank/transactions for {company_id}")
        except Exception as e:
            logger.warning(f"[BANKING] Failed to invalidate central cache: {e}")

    async def invalidate_cache(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        BANKING.invalidate_cache - Invalidate banking cache (public endpoint).

        Invalidates both central cache and local caches.

        Args:
            user_id: Firebase UID
            company_id: Company ID

        Returns:
            {"success": True, "invalidated": [...]}
        """
        try:
            cache = get_firebase_cache_manager()
            invalidated = []

            # 1. Invalidate CENTRAL BUSINESS CACHE (most important!)
            await self._invalidate_central_cache(user_id, company_id)
            invalidated.append("bank:transactions (central)")

            # 2. Invalidate local caches (accounts, instructions)
            local_keys = [
                ("banking", "accounts"),
            ]

            for category, sub_key in local_keys:
                try:
                    await cache.invalidate_cache(user_id, company_id, category, sub_key)
                    invalidated.append(f"{category}:{sub_key}")
                except Exception:
                    pass

            logger.info(f"[BANKING] Cache invalidated: {invalidated}")
            return {"success": True, "invalidated": invalidated}

        except Exception as e:
            logger.error(f"[BANKING] invalidate_cache error: {e}")
            return {"success": False, "error": {"message": str(e)}}
