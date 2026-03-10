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
        Récupère les dépenses via expenses/handlers.py (source unique de vérité).

        Délègue à expenses.handlers.list_expenses() qui gère le cache Redis
        au format dict pré-catégorisé {to_process, in_process, pending, processed}.
        Extrait les items en liste plate pour compatibilité avec les appelants legacy.

        RPC: FIREBASE_CACHE.get_expenses

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            mandate_path (str, optional): Chemin du mandat pour filtre task_manager

        Returns:
            {"data": [...], "source": "cache"|"expenses_handler"}
        """
        try:
            from .frontend.pages.expenses.handlers import get_expenses_handlers

            handlers = get_expenses_handlers()
            result = await handlers.list_expenses(
                user_id=user_id,
                company_id=company_id,
                mandate_path=mandate_path or "",
                force_refresh=False,
            )

            if result.get("success") and result.get("data"):
                categorized = result["data"]
                # Extraire tous les items en liste plate pour compatibilité legacy
                all_items = []
                for cat_key in ("to_process", "in_process", "pending", "processed"):
                    all_items.extend(categorized.get(cat_key, []))

                source = "cache" if result.get("from_cache") else "expenses_handler"
                logger.info(
                    f"FIREBASE_CACHE.get_expenses company_id={company_id} "
                    f"count={len(all_items)} source={source}"
                )
                return {"data": all_items, "source": source}

            logger.warning(
                f"FIREBASE_CACHE.get_expenses company_id={company_id} "
                f"no data from expenses handler"
            )
            return {"data": [], "source": "empty"}

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
        mandate_path: str = None,  # Kept for backward compatibility
        force_refresh: bool = False,
        skip_suggestions: bool = False,
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
            force_refresh (bool): If True, bypass cache and fetch fresh from ERP + Firebase

        Returns:
            {"data": {"to_process": [...], "in_process": [...], "pending": [...], "processed": [...]}, "source": "cache"|"erp"}
        """
        import asyncio

        try:
            # 1. Tentative cache (sauf si force_refresh)
            cache = get_firebase_cache_manager()
            if not force_refresh:
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
            else:
                logger.info(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"force_refresh=True → bypassing cache"
                )

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

                # Extraire reconciliation_details et result_code depuis department_data
                recon_details = banker.get("reconciliation_details")
                result_code = banker.get("result_code", "")
                step_label = banker.get("step_label", "")

                if erp_tx:
                    amount = float(erp_tx.get("amount", 0) or 0)
                    base = {
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
                        "current_step": step_label or item.get("current_step", ""),
                        "job_id": item.get("task_id", ""),
                        "created_at": erp_tx.get("date", ""),
                        "updated_at": erp_tx.get("date", ""),
                    }
                else:
                    amount = float(banker.get("txn_amount", 0) or 0)
                    base = {
                        "id": tx_id,
                        "transaction_id": tx_id,
                        "account_id": str(banker.get("bank_account_id") or ""),
                        "account_name": banker.get("bank_account", ""),
                        "date": banker.get("transaction_date", ""),
                        "reference": banker.get("reference", ""),
                        "description": banker.get("description", "") or banker.get("label", ""),
                        "partner_name": banker.get("partner_name", ""),
                        "payment_ref": banker.get("payment_ref", ""),
                        "amount": amount,
                        "currency": banker.get("txn_currency") or "CHF",
                        "transaction_type": "credit" if amount >= 0 else "debit",
                        "status": item.get("status", status),
                        "batch_id": banker.get("batch_id", ""),
                        "current_step": step_label or item.get("current_step", ""),
                        "job_id": item.get("task_id", ""),
                        "created_at": banker.get("transaction_date", ""),
                        "updated_at": banker.get("transaction_date", ""),
                    }

                # Enrichir avec reconciliation_details et result_code si présents
                if recon_details and isinstance(recon_details, dict):
                    base["reconciliation_details"] = recon_details
                if result_code:
                    base["result_code"] = result_code
                return base

            # Indexer les transactions ERP par ID pour recherche rapide
            erp_map = {str(tx.get("move_id") or tx.get("id")): tx for tx in erp_transactions}

            # A. In Process (cross-ref ERP, flat list avec batch_id sur chaque item)
            # FILTRE CROISÉ: seules les transactions encore présentes dans l'ERP
            # (non réconciliées) sont affichées. Si la transaction a déjà été
            # réconciliée dans Odoo, elle n'apparaît plus dans erp_map et est
            # considérée comme fantôme (task_manager stale).
            in_process_list = []
            phantom_in_process = 0

            for batch_id, items in fb_jobs.get("in_process", {}).items():
                for item in items:
                    tx_id = str(item.get("transaction_id") or "")
                    if not tx_id:
                        continue  # Skip items sans transaction_id (docs batch-level fantômes)
                    erp_tx = erp_map.pop(tx_id, None)
                    if erp_tx is None:
                        phantom_in_process += 1
                        continue  # Transaction déjà réconciliée dans ERP → fantôme
                    normalized = _normalize_from_task_manager(item, erp_tx=erp_tx, status="on_process")
                    normalized["batch_id"] = batch_id
                    in_process_list.append(normalized)

            if phantom_in_process:
                logger.info(
                    f"[BANK] Filtered {phantom_in_process} phantom in_process "
                    f"(task_manager stale, already reconciled in ERP)"
                )

            # B. Pending (cross-ref ERP)
            # FILTRE CROISÉ: même logique — une transaction pending dont le move_id
            # n'est plus dans l'ERP a déjà été réconciliée → fantôme.
            pending_list = []
            phantom_pending = 0
            for item in fb_jobs.get("pending", []):
                tx_id = str(item.get("transaction_id") or "")
                if not tx_id:
                    continue  # Skip items sans transaction_id
                erp_tx = erp_map.pop(tx_id, None)
                if erp_tx is None:
                    phantom_pending += 1
                    continue  # Transaction déjà réconciliée dans ERP → fantôme
                normalized = _normalize_from_task_manager(item, erp_tx=erp_tx, status="pending")
                pending_list.append(normalized)

            if phantom_pending:
                logger.info(
                    f"[BANK] Filtered {phantom_pending} phantom pending "
                    f"(task_manager stale, already reconciled in ERP)"
                )

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

            # E. Enrichissement Bulk Matching Suggestions (non-bloquant)
            if to_process_list and not skip_suggestions:
                try:
                    from .bulk_matching_engine import BulkMatchingSuggestionEngine, BulkMatchingConfig
                    from .fx_rate_service import get_fx_rates_cached, normalize_currency

                    # Collecter devises necessaires
                    currencies = {normalize_currency(tx.get("currency", "CHF")) for tx in to_process_list}
                    # Devise majoritaire = base
                    from collections import Counter
                    currency_counts = Counter(
                        normalize_currency(tx.get("currency", "CHF")) for tx in to_process_list
                    )
                    base_currency = currency_counts.most_common(1)[0][0] if currency_counts else "CHF"
                    target_currencies = currencies - {base_currency}

                    # Date range from transactions
                    tx_dates = [tx.get("date", "") for tx in to_process_list if tx.get("date")]
                    date_from = min(tx_dates) if tx_dates else None
                    date_to = max(tx_dates) if tx_dates else None

                    # Fetch parallele: AP invoices + AR invoices + Expenses + FX rates
                    ap_data, ar_data, exp_data, fx_data = await asyncio.gather(
                        self._get_open_ap_for_matching(user_id, company_id, mandate_path),
                        self._get_open_ar_for_matching(user_id, company_id, mandate_path),
                        self._get_open_expenses_for_matching(user_id, company_id, mandate_path),
                        get_fx_rates_cached(base_currency, target_currencies, date_from, date_to),
                        return_exceptions=True,
                    )

                    ap_invoices = ap_data if not isinstance(ap_data, Exception) else []
                    ar_invoices = ar_data if not isinstance(ar_data, Exception) else []
                    expenses_list = exp_data if not isinstance(exp_data, Exception) else []
                    fx_rates = fx_data if not isinstance(fx_data, Exception) else {}

                    if isinstance(ap_data, Exception):
                        logger.warning(f"[BANK] Bulk matching: AP fetch failed: {ap_data}")
                    if isinstance(ar_data, Exception):
                        logger.warning(f"[BANK] Bulk matching: AR fetch failed: {ar_data}")
                    if isinstance(exp_data, Exception):
                        logger.warning(f"[BANK] Bulk matching: Expenses fetch failed: {exp_data}")
                    if isinstance(fx_data, Exception):
                        logger.warning(f"[BANK] Bulk matching: FX rates failed: {fx_data}")

                    # Score
                    engine = BulkMatchingSuggestionEngine(BulkMatchingConfig(), fx_rates)
                    suggestions = engine.compute_suggestions(
                        to_process_list, ap_invoices, expenses_list, ar_invoices=ar_invoices
                    )

                    # Enrich
                    enriched_count = 0
                    for tx in to_process_list:
                        s = suggestions.get(str(tx.get("id", "")))
                        if s and (s.get("top_matches") or s.get("transfer_match")):
                            tx["match_suggestions"] = s
                            enriched_count += 1
                        else:
                            tx["match_suggestions"] = {
                                "top_matches": [], "transfer_match": None, "scored_at": None
                            }

                    logger.info(
                        f"[BANK] Bulk matching enriched {enriched_count}/{len(to_process_list)} "
                        f"transactions (AP={len(ap_invoices)}, AR={len(ar_invoices)}, EXP={len(expenses_list)})"
                    )
                except Exception as e:
                    logger.warning(f"[BANK] Bulk matching enrichment failed (non-blocking): {e}")

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
    # BULK MATCHING HELPERS (used by get_bank_transactions)
    # ═══════════════════════════════════════════════════════════════

    async def _get_open_ap_for_matching(
        self, user_id: str, company_id: str, mandate_path: str
    ) -> List[Dict]:
        """
        Retourne les factures AP ouvertes depuis l'ERP (Odoo), normalisees pour le bulk matching.
        Query: account.move / move_type=in_invoice / state=posted / payment_state in [not_paid, partial]
        Ce sont les factures deja saisies et pas encore payees — les vrais candidats pour le rapprochement bancaire.
        """
        try:
            import asyncio
            from .erp_service import ERPService

            # client_uuid = None lets ERPService resolve credentials from Firebase
            client_uuid = None
            candidates = await asyncio.to_thread(
                ERPService.get_open_ap_invoices,
                user_id,
                company_id,
                client_uuid,
            )
            logger.info(f"[BANK] Bulk matching: {len(candidates)} open AP invoices from ERP")
            return candidates
        except Exception as e:
            logger.warning(f"[BANK] _get_open_ap_for_matching error: {e}")
            traceback.print_exc()
            return []

    async def _get_open_ar_for_matching(
        self, user_id: str, company_id: str, mandate_path: str
    ) -> List[Dict]:
        """
        Retourne les factures clients (AR) ouvertes depuis l'ERP (Odoo), normalisees pour le bulk matching.
        Query: account.move / move_type=out_invoice / state=posted / payment_state in [not_paid, partial]
        """
        try:
            import asyncio
            from .erp_service import ERPService

            client_uuid = None
            candidates = await asyncio.to_thread(
                ERPService.get_open_ar_invoices,
                user_id,
                company_id,
                client_uuid,
            )
            logger.info(f"[BANK] Bulk matching: {len(candidates)} open AR invoices from ERP")
            return candidates
        except Exception as e:
            logger.warning(f"[BANK] _get_open_ar_for_matching error: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def _get_open_expenses_for_matching(
        self, user_id: str, company_id: str, mandate_path: str
    ) -> List[Dict]:
        """
        Retourne les expenses ouvertes, normalisees pour le bulk matching.
        Deplie department_data.EXbookeeper vers des champs plats.
        """
        try:
            result = await self.get_expenses(
                user_id=user_id,
                company_id=company_id,
                mandate_path=mandate_path,
            )
            data = result.get("data", [])

            raw_items = []
            if isinstance(data, list):
                raw_items = [
                    item for item in data
                    if item.get("status", "").lower() not in ("completed", "processed", "closed")
                ]
            elif isinstance(data, dict):
                for key in ("to_process", "in_process", "pending"):
                    items = data.get(key, [])
                    if isinstance(items, list):
                        raw_items.extend(items)

            # Normaliser: extraire les champs metier depuis department_data
            candidates = []
            for item in raw_items:
                dept = item.get("department_data", {})
                ex = dept.get("EXbookeeper", {}) or dept.get("exbookeeper", {})

                amount = float(
                    ex.get("amount")
                    or item.get("amount")
                    or 0
                )
                if amount == 0:
                    continue  # Pas de montant = pas de candidat

                supplier = ex.get("supplier") or item.get("supplier_name") or ""
                concern = ex.get("concern") or item.get("description") or ""

                candidates.append({
                    # Champs pour le scoring
                    "amount": amount,
                    "currency": ex.get("currency") or item.get("currency") or "CHF",
                    "description": concern,
                    "label": concern,
                    "employee_name": supplier,
                    "date": ex.get("date") or item.get("date") or "",
                    "expense_date": ex.get("date") or item.get("date") or "",
                    # Champs d'affichage (pour le badge frontend)
                    "display_name": concern or supplier or item.get("file_name") or "",
                    "display_ref": supplier,
                    "display_amount": amount,
                    "display_date": ex.get("date") or item.get("date") or "",
                    # ID technique (cache pour le payload)
                    "id": item.get("id") or item.get("job_id") or item.get("expense_id") or "",
                    "job_id": item.get("job_id") or "",
                })

            return candidates
        except Exception as e:
            logger.warning(f"[BANK] _get_open_expenses_for_matching error: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════
    # SINGLE-CANDIDATE SCORING (post Router/AP completion)
    # ═══════════════════════════════════════════════════════════════

    async def trigger_single_candidate_scoring(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        candidate: Dict,
        candidate_type: str,
    ) -> Dict:
        """
        Score a single new invoice/expense against all to_process bank TX.
        Updates the cache in-place and returns auto-dispatch candidates (score >= HIGH).

        Args:
            candidate: Normalized candidate dict (amount, currency, date, etc.)
            candidate_type: "invoice" or "expense"

        Returns:
            {
                "updated_count": int,
                "auto_dispatch": [{"tx": ..., "suggestion": ...}],
            }
        """
        import asyncio
        from .bulk_matching_engine import BulkMatchingSuggestionEngine, BulkMatchingConfig
        from .fx_rate_service import get_fx_rates_cached, normalize_currency
        from .cache.unified_cache_manager import get_firebase_cache_manager
        from .llm_service.redis_namespaces import build_business_key, get_ttl_for_domain
        import json

        config = BulkMatchingConfig()
        result = {"updated_count": 0, "auto_dispatch": []}

        try:
            # 1. Read bank cache from Redis
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(user_id, company_id, "bank", "transactions")
            if not cached:
                logger.info("[SINGLE_SCORING] No bank cache found — skipping")
                return result

            to_process_list = cached.get("to_process", [])
            if not to_process_list:
                logger.info("[SINGLE_SCORING] No to_process TX — skipping")
                return result

            # 2. Get FX rates
            cand_currency = normalize_currency(candidate.get("currency", "CHF"))
            tx_currencies = {normalize_currency(tx.get("currency", "CHF")) for tx in to_process_list}
            all_currencies = tx_currencies | {cand_currency}
            from collections import Counter
            currency_counts = Counter(
                normalize_currency(tx.get("currency", "CHF")) for tx in to_process_list
            )
            base_currency = currency_counts.most_common(1)[0][0] if currency_counts else "CHF"
            target_currencies = all_currencies - {base_currency}

            tx_dates = [tx.get("date", "") for tx in to_process_list if tx.get("date")]
            date_from = min(tx_dates) if tx_dates else None
            date_to = max(tx_dates) if tx_dates else None

            try:
                fx_rates = await get_fx_rates_cached(base_currency, target_currencies, date_from, date_to)
            except Exception:
                fx_rates = {}

            # 3. Score single candidate against all TX
            engine = BulkMatchingSuggestionEngine(config, fx_rates)
            updated_txs = engine.update_suggestions_with_candidate(
                to_process_list, candidate, candidate_type
            )

            if not updated_txs:
                logger.info("[SINGLE_SCORING] No TX improved — skipping cache write")
                return result

            result["updated_count"] = len(updated_txs)

            # 4. Collect auto-dispatch candidates (score >= high_confidence)
            for tx in updated_txs:
                suggestions = tx.get("match_suggestions", {})
                top = suggestions.get("top_matches", [])
                if top and top[0].get("score", 0) >= config.high_confidence:
                    result["auto_dispatch"].append({
                        "tx": tx,
                        "suggestion": top[0],
                    })

            # 5. Write updated cache back to Redis
            cached["to_process"] = to_process_list
            await cache.set_cached_data(
                user_id, company_id, "bank", "transactions",
                cached, ttl_seconds=2400  # 40min
            )

            logger.info(
                f"[SINGLE_SCORING] Updated {len(updated_txs)} TX suggestions, "
                f"{len(result['auto_dispatch'])} auto-dispatch candidates "
                f"(candidate_type={candidate_type})"
            )

        except Exception as e:
            logger.warning(f"[SINGLE_SCORING] Failed (non-blocking): {e}")
            import traceback
            traceback.print_exc()

        return result

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
