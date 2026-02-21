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
TTL_TASKS = 2400                 # 40 minutes


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
        company_id: str,
        mandate_path: str = None
    ) -> Dict[str, Any]:
        """
        Récupère les dépenses depuis task_manager (source unique de vérité) avec cache.
        Fallback vers l'ancienne collection mandates/{company_id}/expenses si task_manager vide.

        RPC: FIREBASE_CACHE.get_expenses

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            mandate_path (str, optional): Chemin du mandat pour filtre task_manager

        Returns:
            {"data": [...], "source": "cache"|"firebase"|"task_manager"}
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

            # 2. Source: task_manager (EXbookeeper)
            db = get_firestore()
            expenses = []

            try:
                task_mgr_ref = db.collection(f"clients/{user_id}/task_manager")
                query = task_mgr_ref.where("department", "in", ["EXbookeeper", "exbookeeper"])
                if mandate_path:
                    clean_path = mandate_path[1:] if mandate_path.startswith("/") else mandate_path
                    query = query.where("mandate_path", "==", clean_path)

                for doc in query.stream():
                    data = doc.to_dict() or {}
                    dept_data = data.get("department_data", {})
                    expense_entry = dept_data.get("EXbookeeper", {}) or dept_data.get("exbookeeper", {})
                    expense_entry["id"] = doc.id
                    expense_entry["job_id"] = data.get("job_id", doc.id)
                    expense_entry["status"] = data.get("status", "")
                    expense_entry["file_name"] = data.get("file_name", expense_entry.get("file_name", ""))
                    expense_entry["mandate_path"] = data.get("mandate_path", "")
                    expenses.append(_convert_timestamps(expense_entry))

                if expenses:
                    logger.info(
                        f"FIREBASE_CACHE.get_expenses company_id={company_id} "
                        f"count={len(expenses)} source=task_manager"
                    )
                    await cache.set_cached_data(
                        user_id, company_id, "expenses", "details",
                        expenses, ttl_seconds=TTL_EXPENSES
                    )
                    return {"data": expenses, "source": "task_manager"}
            except Exception as e:
                logger.warning(f"FIREBASE_CACHE.get_expenses task_manager fallback: {e}")

            # 3. Fallback: ancienne collection mandates/{company_id}/expenses
            expenses_ref = db.collection("mandates").document(company_id).collection("expenses")
            expenses_docs = expenses_ref.stream()

            for doc in expenses_docs:
                expense_data = doc.to_dict()
                expense_data["id"] = doc.id
                expenses.append(_convert_timestamps(expense_data))

            logger.info(
                f"FIREBASE_CACHE.get_expenses company_id={company_id} "
                f"count={len(expenses)} source=firebase"
            )

            # 4. Sync vers Redis
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
        Récupère les documents APBookkeeper depuis task_manager (Source de Vérité) avec cache.

        RPC: FIREBASE_CACHE.get_ap_documents

        Source de Vérité:
        - task_manager (Firebase): clients/{user_id}/task_manager filtré par department APbookeeper
        - Mapping étapes: {mandate_path}/setup/ap_approval_list (original_term → translated_term)

        Classification par statut:
        - completed/close/closed → processed
        - pending → pending
        - on_process/in_queue/running/stopping → in_process
        - error/to_process/autre → to_do

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company/Mandate ID
            mandate_path (str): Chemin Firestore du mandat

        Returns:
            {"data": {"to_process": [...], "in_process": [...], "pending": [...], "processed": [...], "step_mapping": {...}}, "source": "cache"|"task_manager"}
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

            # 2. Fetch depuis task_manager (Source de Vérité)
            from .firebase_providers import get_firebase_management
            firebase_mgmt = get_firebase_management()

            fb_jobs = await asyncio.to_thread(
                firebase_mgmt.get_apbookeeper_jobs_from_task_manager,
                user_id,
                mandate_path or ""
            )

            organized = {
                "to_process": fb_jobs.get("to_process", []),
                "in_process": fb_jobs.get("in_process", []),
                "pending": fb_jobs.get("pending", []),
                "processed": fb_jobs.get("processed", []),
                "step_mapping": fb_jobs.get("step_mapping", {})
            }

            total_count = (
                len(organized["to_process"]) +
                len(organized["in_process"]) +
                len(organized["pending"]) +
                len(organized["processed"])
            )
            logger.info(
                f"FIREBASE_CACHE.get_ap_documents company_id={company_id} "
                f"count={total_count} (to_process={len(organized['to_process'])}, "
                f"in_process={len(organized['in_process'])}, "
                f"pending={len(organized['pending'])}, "
                f"processed={len(organized['processed'])}) source=task_manager"
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
                "source": "task_manager"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_ap_documents error={e}")
            import traceback
            traceback.print_exc()
            return {"data": {"to_process": [], "in_process": [], "pending": [], "processed": [], "step_mapping": {}}, "error": str(e)}

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
        Récupère les transactions bancaires avec réconciliation (ERP + Task Manager).

        RPC: FIREBASE_CACHE.get_bank_transactions

        Source de Vérité:
        1. ERP (Odoo): Transactions brutes
        2. Task Manager (Firebase): État des traitements (In Process, Pending)

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company/Mandate ID
            client_uuid (str): Client UUID for ERP connection
            bank_erp (str): ERP type (e.g., "odoo")
            mandate_path (str): Full Firestore path to mandate

        Returns:
            {"data": {"to_process": [...], "in_process": [...], "pending": [...], "processed": [...]}, "source": "cache"|"erp"}
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

            # 2. Validation des paramètres
            bank_erp_type = (bank_erp or "").lower()
            if not bank_erp_type or bank_erp_type != "odoo":
                logger.warning(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"unsupported_erp={bank_erp_type}"
                )
                return {
                    "data": {"to_process": [], "in_process": [], "pending": [], "processed": []},
                    "source": "none",
                    "warning": f"ERP type '{bank_erp_type}' not supported"
                }

            if not client_uuid:
                return {
                    "data": {"to_process": [], "in_process": [], "pending": [], "processed": []},
                    "source": "none",
                    "warning": "No client UUID"
                }

            # 3. Chargement parallèle des sources (ERP + Task Manager)
            from .erp_service import ERPService
            from .firebase_providers import get_firebase_management
            firebase_mgmt = get_firebase_management()

            # Task 1: ERP Transactions (to_reconcile raw)
            task_erp = asyncio.to_thread(
                ERPService.get_odoo_bank_statement_move_line_not_rec,
                user_id,
                company_id,
                client_uuid,
                None,  # All journals
                False  # Unreconciled only
            )

            # Task 2: Firebase Task Manager (Status)
            task_firebase = asyncio.to_thread(
                firebase_mgmt.get_banker_jobs_from_task_manager,
                user_id,
                mandate_path or ""
            )

            results = await asyncio.gather(task_erp, task_firebase, return_exceptions=True)
            
            erp_transactions = results[0] if not isinstance(results[0], Exception) else []
            fb_jobs = results[1] if not isinstance(results[1], Exception) else {"processed": [], "pending": [], "in_process": {}}

            if isinstance(results[0], Exception):
                logger.error(f"[BANK] ERP Error: {results[0]}")
            if isinstance(results[1], Exception):
                logger.error(f"[BANK] Firebase Error: {results[1]}")

            # 4. Réconciliation / Croisement + Normalisation BankTransaction

            # Helper: normaliser un item vers le format BankTransaction attendu par le frontend
            def _normalize_from_erp(erp_tx, status="to_process"):
                """Normalise une transaction ERP brute vers BankTransaction."""
                move_id = str(erp_tx.get("move_id") or erp_tx.get("id") or "")
                amount = float(erp_tx.get("amount", 0) or 0)
                return {
                    "id": move_id,
                    "transaction_id": move_id,
                    "account_id": str(erp_tx.get("journal_id") or ""),
                    "account_name": erp_tx.get("journal_name", ""),
                    "date": erp_tx.get("date", ""),
                    "reference": erp_tx.get("ref") or erp_tx.get("name") or "",
                    "description": erp_tx.get("payment_ref") or erp_tx.get("display_name") or "",
                    "partner_name": erp_tx.get("partner_name", ""),
                    "payment_ref": erp_tx.get("payment_ref", ""),
                    "amount": amount,
                    "currency": erp_tx.get("currency_name") or "CHF",
                    "transaction_type": "credit" if amount >= 0 else "debit",
                    "status": status,
                    "created_at": erp_tx.get("date", ""),
                    "updated_at": erp_tx.get("date", ""),
                }

            def _normalize_from_task_manager(item, erp_tx=None, status="pending"):
                """Normalise un item task_manager vers BankTransaction, enrichi par ERP si dispo."""
                banker = item if "transaction_id" in item else item.get("department_data", {}).get("Bankbookeeper", {}) or item.get("department_data", {}).get("banker", {}) or item.get("department_data", {}).get("Banker", {})
                tx_id = str(banker.get("transaction_id") or item.get("task_id", ""))

                if erp_tx:
                    amount = float(erp_tx.get("amount", 0) or 0)
                    return {
                        "id": tx_id,
                        "transaction_id": tx_id,
                        "account_id": str(banker.get("bank_account_id") or erp_tx.get("journal_id") or ""),
                        "account_name": erp_tx.get("journal_name", "") or banker.get("bank_account", ""),
                        "date": erp_tx.get("date", ""),
                        "reference": erp_tx.get("ref") or erp_tx.get("name") or "",
                        "description": erp_tx.get("payment_ref") or erp_tx.get("display_name") or "",
                        "partner_name": erp_tx.get("partner_name", ""),
                        "payment_ref": erp_tx.get("payment_ref", ""),
                        "amount": amount,
                        "currency": erp_tx.get("currency_name") or banker.get("txn_currency") or "CHF",
                        "transaction_type": "credit" if amount >= 0 else "debit",
                        "status": item.get("status", status),
                        "batch_id": banker.get("batch_id", ""),
                        "current_step": item.get("current_step", ""),
                        "job_id": item.get("task_id", ""),
                        "created_at": erp_tx.get("date", ""),
                        "updated_at": erp_tx.get("date", ""),
                    }
                else:
                    amount = float(banker.get("txn_amount", 0) or 0)
                    return {
                        "id": tx_id,
                        "transaction_id": tx_id,
                        "account_id": str(banker.get("bank_account_id") or ""),
                        "account_name": banker.get("bank_account", ""),
                        "date": banker.get("transaction_date", ""),
                        "reference": banker.get("reference", ""),
                        "description": "",
                        "partner_name": "",
                        "payment_ref": "",
                        "amount": amount,
                        "currency": banker.get("txn_currency") or "CHF",
                        "transaction_type": "credit" if amount >= 0 else "debit",
                        "status": item.get("status", status),
                        "batch_id": banker.get("batch_id", ""),
                        "current_step": item.get("current_step", ""),
                        "job_id": item.get("task_id", ""),
                        "created_at": banker.get("transaction_date", ""),
                        "updated_at": banker.get("transaction_date", ""),
                    }

            # Indexer les transactions ERP par ID pour recherche rapide
            erp_map = {str(tx.get("move_id") or tx.get("id")): tx for tx in erp_transactions}

            # A. In Process (cross-ref ERP, flat list avec batch_id sur chaque item)
            # Note: si absent de l'ERP, on garde quand même en in_process.
            # Le process en cours détectera que la transaction est déjà réconciliée.
            # Les batches sont calculés à la volée par l'orchestration (pas stockés dans le cache).
            in_process_list = []

            for batch_id, items in fb_jobs.get("in_process", {}).items():
                for item in items:
                    tx_id = str(item.get("transaction_id") or "")
                    erp_tx = erp_map.pop(tx_id, None)
                    normalized = _normalize_from_task_manager(item, erp_tx=erp_tx, status="on_process")
                    normalized["batch_id"] = batch_id
                    in_process_list.append(normalized)

            # B. Pending (cross-ref ERP)
            # Si une transaction pending n'existe plus dans l'ERP non-réconcilié,
            # elle a été réconciliée manuellement en dehors du flow →
            # supprimer le doc task_manager (nettoyage) et ne pas l'afficher.
            pending_list = []
            pending_to_delete = []  # task_ids à supprimer de task_manager
            for item in fb_jobs.get("pending", []):
                tx_id = str(item.get("transaction_id") or "")
                erp_tx = erp_map.pop(tx_id, None)
                if erp_tx is None:
                    # Transaction réconciliée manuellement → marquer pour suppression
                    task_id = item.get("task_id", "")
                    if task_id:
                        pending_to_delete.append(task_id)
                    logger.info(
                        f"[BANK] Pending tx_id={tx_id} task_id={task_id} "
                        f"no longer in ERP → delete from task_manager"
                    )
                else:
                    normalized = _normalize_from_task_manager(item, erp_tx=erp_tx, status="pending")
                    pending_list.append(normalized)

            # Suppression asynchrone des docs pending obsolètes
            if pending_to_delete:
                try:
                    db = get_firestore()
                    task_mgr_ref = db.collection("clients").document(user_id).collection("task_manager")
                    for task_id in pending_to_delete:
                        await asyncio.to_thread(
                            task_mgr_ref.document(task_id).delete
                        )
                    logger.info(
                        f"[BANK] Deleted {len(pending_to_delete)} obsolete pending docs "
                        f"from task_manager for company_id={company_id}"
                    )
                except Exception as del_err:
                    logger.warning(f"[BANK] Failed to delete pending docs: {del_err}")

            # C. Processed (full Firestore docs, extract department_data.banker)
            processed_list = []
            for item in fb_jobs.get("processed", []):
                tx_id_check = ""
                dept_data = item.get("department_data", {})
                banker = dept_data.get("banker", {}) or dept_data.get("Banker", {})
                if banker:
                    tx_id_check = str(banker.get("transaction_id") or "")
                erp_tx = erp_map.pop(tx_id_check, None) if tx_id_check else None
                normalized = _normalize_from_task_manager(item, erp_tx=erp_tx, status="processed")
                processed_list.append(normalized)

            # D. Ce qui reste dans erp_map = vraiment "À traiter"
            to_process_list = [_normalize_from_erp(tx) for tx in erp_map.values()]

            # 5. Construction du résultat final (flat lists uniquement, pas de dict de batches)
            organized = {
                "to_process": to_process_list,
                "in_process": in_process_list,
                "pending": pending_list,
                "processed": processed_list
            }

            total_count = len(to_process_list) + len(in_process_list) + len(pending_list)
            logger.info(
                f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                f"total={total_count} (to_process={len(to_process_list)}, "
                f"in_process={len(in_process_list)}, "
                f"pending={len(pending_list)}) source=erp+firebase"
            )

            # 6. Sync vers Redis
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
            return {"data": {"to_process": [], "in_process": [], "pending": [], "processed": []}, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # TASKS
    # ═══════════════════════════════════════════════════════════════

    async def get_tasks(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str = None
    ) -> Dict[str, Any]:
        """
        Récupère les tâches planifiées depuis {mandate_path}/tasks avec cache.

        RPC: FIREBASE_CACHE.get_tasks

        Uses FirebaseManagement.list_tasks_for_mandate() which queries:
        - {mandate_path}/tasks

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company/Mandate ID
            mandate_path (str): Full Firestore path to mandate (required)

        Returns:
            {"data": [...], "source": "cache"|"firebase"}
        """
        import asyncio

        try:
            # 1. Tentative cache
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "tasks",
                "list",
                ttl_seconds=TTL_TASKS
            )

            if cached and cached.get("data"):
                data = cached["data"]
                count = len(data) if isinstance(data, list) else 0
                logger.info(
                    f"FIREBASE_CACHE.get_tasks company_id={company_id} "
                    f"count={count} source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase - requires mandate_path
            if not mandate_path:
                logger.warning(
                    f"FIREBASE_CACHE.get_tasks company_id={company_id} "
                    f"no_mandate_path"
                )
                return {
                    "data": [],
                    "source": "none",
                    "warning": "No mandate_path provided"
                }

            from .firebase_providers import get_firebase_management
            firebase_mgmt = get_firebase_management()

            # Fetch tasks from {mandate_path}/tasks
            raw_tasks = await asyncio.to_thread(
                firebase_mgmt.list_tasks_for_mandate,
                mandate_path
            )

            if not raw_tasks:
                logger.info(
                    f"FIREBASE_CACHE.get_tasks company_id={company_id} "
                    f"no_tasks_found"
                )
                # Cache empty result to avoid repeated Firebase calls
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "tasks",
                    "list",
                    [],
                    ttl_seconds=TTL_TASKS
                )
                return {
                    "data": [],
                    "source": "firebase"
                }

            # Transform tasks to match dashboard format
            tasks = []
            for task in raw_tasks:
                mission = task.get("mission", {})
                schedule = task.get("schedule", {})

                task_data = {
                    "id": task.get("task_id", task.get("id", "")),
                    "title": mission.get("title", ""),
                    "description": mission.get("description"),
                    "status": task.get("status", "inactive"),
                    "priority": task.get("priority", "medium"),
                    "executionPlan": task.get("execution_plan", "ON_DEMAND"),
                    "enabled": task.get("enabled", False),
                    "nextExecution": schedule.get("next_execution_utc", ""),
                    "frequency": schedule.get("frequency", ""),
                    "createdAt": task.get("created_at", ""),
                    "updatedAt": task.get("updated_at", ""),
                    "category": "accounting",
                }
                tasks.append(task_data)

            # Convert timestamps to JSON-serializable format
            tasks = _convert_timestamps(tasks)

            logger.info(
                f"FIREBASE_CACHE.get_tasks company_id={company_id} "
                f"count={len(tasks)} source=firebase"
            )

            # 3. Sync vers Redis
            await cache.set_cached_data(
                user_id,
                company_id,
                "tasks",
                "list",
                tasks,
                ttl_seconds=TTL_TASKS
            )

            return {
                "data": tasks,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_tasks error={e}")
            import traceback
            traceback.print_exc()
            return {"data": [], "error": str(e)}

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
