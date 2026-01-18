"""
Handlers RPC pour le module Dashboard (Next.js).

NAMESPACE: DASHBOARD

Architecture:
    Frontend (Next.js) → apiClient.call("DASHBOARD.full_data", ...)
                      → POST /rpc
                      → dashboard_handlers.full_data()
                      → Redis Cache (HIT) | Firebase/Services (MISS)

Endpoints disponibles:
    - DASHBOARD.full_data           → Données complètes dashboard (TTL 60s)
    - DASHBOARD.get_metrics         → Métriques uniquement (TTL 30s)
    - DASHBOARD.invalidate_cache    → Invalidation manuelle

IMPORTANT: Ce module est NOUVEAU et NE MODIFIE PAS les méthodes existantes.
           Les anciennes méthodes Reflex restent intactes pour rétrocompatibilité.

Note: user_id et company_id sont injectés automatiquement par main.py
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud.firestore_v1.base_query import FieldFilter
from .cache.unified_cache_manager import get_firebase_cache_manager
from .firebase_client import get_firestore
from .firebase_providers import get_firebase_management
from .ws_events import WS_EVENTS

logger = logging.getLogger("dashboard.handlers")


# ═══════════════════════════════════════════════════════════════
# CONSTANTES TTL
# ═══════════════════════════════════════════════════════════════

TTL_DASHBOARD_FULL = 60          # 1 minute pour données complètes
TTL_DASHBOARD_METRICS = 30       # 30 secondes pour métriques seules


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_dashboard_handlers_instance: Optional["DashboardHandlers"] = None


def get_dashboard_handlers() -> "DashboardHandlers":
    """Singleton accessor pour les handlers dashboard."""
    global _dashboard_handlers_instance
    if _dashboard_handlers_instance is None:
        _dashboard_handlers_instance = DashboardHandlers()
    return _dashboard_handlers_instance


class DashboardHandlers:
    """
    Handlers RPC pour le namespace DASHBOARD.

    Chaque méthode correspond à un endpoint RPC:
    - DASHBOARD.full_data → full_data()
    - DASHBOARD.get_metrics → get_metrics()
    - DASHBOARD.invalidate_cache → invalidate_cache()

    Toutes les méthodes sont asynchrones.
    """

    NAMESPACE = "DASHBOARD"

    # ═══════════════════════════════════════════════════════════════
    # FULL DATA (Données complètes)
    # ═══════════════════════════════════════════════════════════════

    async def full_data(
        self,
        user_id: str,
        company_id: str,
        force_refresh: bool = False,
        include_activity: bool = False,  # Disabled - collection not used in Reflex app
        activity_limit: int = 10,
        mandate_path: Optional[str] = None  # Added for billing widget
    ) -> Dict[str, Any]:
        """
        Récupère TOUTES les données du dashboard en une seule requête.

        RPC: DASHBOARD.full_data

        Cette méthode remplace l'orchestration côté frontend.
        Le backend agrège toutes les données et les retourne en une fois.

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            force_refresh (bool): Forcer le rechargement (bypass cache)
            include_activity (bool): Inclure l'activité récente
            activity_limit (int): Nombre max d'items d'activité

        Returns:
            {
                "success": True,
                "data": {
                    "company": {...},
                    "storage": {...},
                    "metrics": {...},
                    "jobs": {...},
                    "approvals": [...],
                    "tasks": [...],
                    "expenses": {
                        "open": [...],
                        "closed": [...],
                        "metrics": {
                            "totalOpen": 5,
                            "totalClosed": 12,
                            "totalAmount": 15000.00
                        }
                    },
                    "activity": [...],
                    "alerts": [...],
                    "meta": {...}
                }
            }
        """
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        start_time = datetime.utcnow()

        try:
            cache = get_firebase_cache_manager()
            cache_key = f"dashboard:full:{company_id}"

            # 1. Tentative cache (sauf force_refresh)
            if not force_refresh:
                try:
                    cached = await cache.get_cached_data(
                        user_id,
                        company_id,
                        "dashboard",
                        "full_data",
                        ttl_seconds=TTL_DASHBOARD_FULL
                    )

                    if cached and cached.get("data"):
                        cached_data = cached["data"]
                        cached_data["meta"] = {
                            **cached_data.get("meta", {}),
                            "requestId": request_id,
                            "cacheHit": True,
                            "durationMs": self._elapsed_ms(start_time),
                        }
                        logger.info(
                            f"DASHBOARD.full_data company_id={company_id} "
                            f"source=cache request_id={request_id} "
                            f"cached_tasks_count={len(cached_data.get('tasks', []))}"
                        )
                        return {"success": True, "data": cached_data}
                except Exception as cache_err:
                    logger.warning(f"Cache read error: {cache_err}")

            # 2. Fetch toutes les données en parallèle
            logger.info(
                f"DASHBOARD.full_data company_id={company_id} "
                f"source=aggregation request_id={request_id}"
            )

            # Récupérer les données en parallèle
            results = await asyncio.gather(
                self._get_company_info(user_id, company_id),
                self._get_storage_info(user_id, company_id),
                self._get_metrics(user_id, company_id, mandate_path),  # Pass mandate_path for expenses metrics
                self._get_jobs_by_category(user_id, company_id),
                self._get_pending_approvals(user_id, company_id),
                self._get_tasks(user_id, company_id, mandate_path),  # Pass mandate_path for tasks from {mandate_path}/tasks
                self._get_expenses(user_id, company_id, mandate_path),  # Pass mandate_path for expenses
                self._get_activity(user_id, company_id, activity_limit) if include_activity else asyncio.coroutine(lambda: [])(),
                self._get_alerts(user_id, company_id),
                self._get_balance_info(user_id, company_id, mandate_path),  # Billing with mandate_path
                return_exceptions=True
            )

            # Extraire les résultats (avec gestion des erreurs)
            company = self._safe_result(results[0], {})
            storage = self._safe_result(results[1], {"used": 0, "total": 0, "percentage": 0})
            metrics = self._safe_result(results[2], self._default_metrics())
            jobs = self._safe_result(results[3], {"apbookeeper": [], "router": [], "banker": []})
            approvals = self._safe_result(results[4], [])
            tasks = self._safe_result(results[5], [])
            logger.info(f"[FULL_DATA] Tasks after _safe_result: count={len(tasks)}, raw_result_type={type(results[5])}, is_exception={isinstance(results[5], Exception)}")
            expenses = self._safe_result(results[6], {"open": [], "closed": [], "metrics": {"totalOpen": 0, "totalClosed": 0, "totalAmount": 0}})
            activity = self._safe_result(results[7], []) if include_activity else []
            alerts = self._safe_result(results[8], [])
            balance_info = self._safe_result(results[9], {
                "currentBalance": 0.0,
                "currentMonthExpenses": 0.0,
                "lastMonthExpenses": 0.0,
                "totalCost": 0.0,
                "totalTopping": 0.0
            })

            # Ensure mandate_path is included in company data
            # (may be missing if not stored in document)
            if mandate_path and (not company.get("mandatePath")):
                company["mandatePath"] = mandate_path

            # Construire la réponse
            dashboard_data = {
                "company": company,
                "balance": balance_info,  # Informations de solde (billing)
                "storage": storage,
                "metrics": metrics,
                "jobs": jobs,
                "approvals": approvals,
                "tasks": tasks,
                "expenses": expenses,
                "activity": activity,
                "alerts": alerts,
                "meta": {
                    "requestId": request_id,
                    "cachedAt": datetime.utcnow().isoformat() + "Z",
                    "cacheHit": False,
                    "cacheTTL": TTL_DASHBOARD_FULL,
                    "durationMs": self._elapsed_ms(start_time),
                    "dataFreshness": {
                        "company": datetime.utcnow().isoformat() + "Z",
                        "metrics": datetime.utcnow().isoformat() + "Z",
                        "jobs": datetime.utcnow().isoformat() + "Z",
                        "approvals": datetime.utcnow().isoformat() + "Z",
                        "tasks": datetime.utcnow().isoformat() + "Z",
                        "expenses": datetime.utcnow().isoformat() + "Z",
                    }
                }
            }

            # 3. Sauvegarder en cache
            try:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "dashboard",
                    "full_data",
                    dashboard_data,
                    ttl_seconds=TTL_DASHBOARD_FULL
                )
            except Exception as cache_err:
                logger.warning(f"Cache write error: {cache_err}")

            logger.info(f"[FULL_DATA] Returning dashboard_data: tasks_count={len(dashboard_data.get('tasks', []))}")
            return {"success": True, "data": dashboard_data}

        except Exception as e:
            logger.error(f"DASHBOARD.full_data error={e} company_id={company_id}")
            return {
                "success": False,
                "error": {
                    "code": "DASHBOARD_FETCH_ERROR",
                    "message": str(e),
                    "details": {"company_id": company_id}
                }
            }

    # ═══════════════════════════════════════════════════════════════
    # GET METRICS (Métriques seules)
    # ═══════════════════════════════════════════════════════════════

    async def get_metrics(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Récupère uniquement les métriques du dashboard.

        RPC: DASHBOARD.get_metrics

        Args:
            user_id (str): Firebase UID
            company_id (str): Company ID

        Returns:
            {"success": True, "data": {...}}
        """
        try:
            metrics = await self._get_metrics(user_id, company_id)
            return {"success": True, "data": metrics}
        except Exception as e:
            logger.error(f"DASHBOARD.get_metrics error={e}")
            return {"success": False, "error": {"message": str(e)}}

    # ═══════════════════════════════════════════════════════════════
    # INVALIDATE CACHE
    # ═══════════════════════════════════════════════════════════════

    async def invalidate_cache(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Invalide le cache dashboard pour une company.

        RPC: DASHBOARD.invalidate_cache

        Args:
            user_id (str): Firebase UID
            company_id (str): Company ID

        Returns:
            {"success": True, "invalidated": [...]}
        """
        try:
            cache = get_firebase_cache_manager()

            # Invalider les clés dashboard
            keys_to_invalidate = [
                ("dashboard", "full_data"),
                ("dashboard", "metrics"),
            ]

            invalidated = []
            for category, sub_key in keys_to_invalidate:
                try:
                    await cache.delete_cached_data(user_id, company_id, category, sub_key)
                    invalidated.append(f"{category}:{sub_key}")
                except Exception:
                    pass

            logger.info(
                f"DASHBOARD.invalidate_cache company_id={company_id} "
                f"invalidated={invalidated}"
            )
            return {"success": True, "invalidated": invalidated}

        except Exception as e:
            logger.error(f"DASHBOARD.invalidate_cache error={e}")
            return {"success": False, "error": {"message": str(e)}}

    # ═══════════════════════════════════════════════════════════════
    # HELPERS PRIVÉS
    # ═══════════════════════════════════════════════════════════════

    def _elapsed_ms(self, start: datetime) -> int:
        """Calcule le temps écoulé en millisecondes."""
        return int((datetime.utcnow() - start).total_seconds() * 1000)

    def _safe_result(self, result: Any, default: Any) -> Any:
        """Retourne le résultat ou la valeur par défaut si exception."""
        if isinstance(result, Exception):
            logger.warning(f"Partial fetch error: {result}")
            return default
        return result if result is not None else default

    def _default_metrics(self) -> Dict[str, Any]:
        """Retourne les métriques par défaut."""
        return {
            "router": {"toProcess": 0, "inProcess": 0, "processed": 0},
            "ap": {"toProcess": 0, "inProcess": 0, "pending": 0, "processed": 0},
            "bank": {"toProcess": 0, "inProcess": 0, "pending": 0, "matched": 0},
            "expenses": {"open": 0, "closed": 0, "pendingApproval": 0},
            "summary": {
                "totalDocumentsToProcess": 0,
                "totalInProgress": 0,
                "totalCompleted": 0,
                "completionRate": 0
            }
        }

    async def _get_company_info(self, user_id: str, company_id: str) -> Dict[str, Any]:
        """Récupère les infos de la company depuis Firebase."""
        try:
            db = get_firestore()

            # Chercher dans mandates ou companies
            mandate_ref = db.collection("mandates").document(company_id)
            mandate_doc = mandate_ref.get()

            if mandate_doc.exists:
                data = mandate_doc.to_dict() or {}
                return {
                    "id": company_id,
                    "name": data.get("name", ""),
                    "legalName": data.get("legal_name", data.get("name", "")),
                    "balance": data.get("balance", 0),
                    "currency": data.get("currency", "EUR"),
                    "mandatePath": data.get("mandate_path", ""),
                    "clientUuid": data.get("client_uuid", ""),
                    "integrations": {
                        "banking": data.get("banking_connected", False),
                        "erp": data.get("erp_connected", False),
                    }
                }

            # Fallback: chercher dans clients/{user_id}/companies
            company_ref = db.collection("clients").document(user_id).collection("companies").document(company_id)
            company_doc = company_ref.get()

            if company_doc.exists:
                data = company_doc.to_dict() or {}
                return {
                    "id": company_id,
                    "name": data.get("name", ""),
                    "legalName": data.get("legal_name", data.get("name", "")),
                    "balance": data.get("balance", 0),
                    "currency": data.get("currency", "EUR"),
                    "mandatePath": data.get("mandate_path", ""),
                    "clientUuid": user_id,
                    "integrations": {
                        "banking": data.get("banking_connected", False),
                        "erp": data.get("erp_connected", False),
                    }
                }

            return {}
        except Exception as e:
            logger.error(f"_get_company_info error: {e}")
            return {}

    async def _get_balance_info(
        self,
        user_id: str,
        company_id: str,
        mandate_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Récupère les informations de solde depuis Firebase.

        Délègue au module account_balance_card pour une meilleure organisation du code.
        Voir: app/frontend/dashboard/account_balance_card.py

        Args:
            user_id: Firebase user ID
            company_id: Company/mandate ID
            mandate_path: Full Firestore path to mandate (from orchestration)

        Returns:
            {
                "currentBalance": float,     # Solde actuel (topping - expenses)
                "currentMonthExpenses": float,  # Dépenses du mois en cours
                "lastMonthExpenses": float,  # Dépenses du mois dernier
                "totalCost": float,          # Coût total (toutes dépenses)
                "totalTopping": float        # Total des rechargements
            }
        """
        try:
            from .frontend.dashboard import get_account_balance_data

            return await get_account_balance_data(user_id, company_id, mandate_path)

        except Exception as e:
            logger.error(f"_get_balance_info error: {e}", exc_info=True)
            return {
                "currentBalance": 0.0,
                "currentMonthExpenses": 0.0,
                "lastMonthExpenses": 0.0,
                "totalCost": 0.0,
                "totalTopping": 0.0
            }

    async def _get_storage_info(self, user_id: str, company_id: str) -> Dict[str, Any]:
        """Récupère les infos de stockage depuis ChromaDB.

        Utilise ChromaVectorService.analyze_storage_full() pour récupérer
        les données de stockage de la base vectorielle.
        """
        try:
            from .chroma_vector_service import get_chroma_vector_service

            # Récupérer le service ChromaDB
            chroma_service = get_chroma_vector_service()

            # Analyser le stockage pour cette company (collection = company_id)
            max_storage_gb = 10.0  # 10GB limite par défaut
            result = chroma_service.analyze_storage_full(company_id, max_storage_gb)

            if result.get("success"):
                storage_data = result["data"]
                # Convertir GB en bytes pour cohérence avec le frontend
                used_bytes = int(storage_data["storage_size_gb"] * (1024 ** 3))
                total_bytes = int(max_storage_gb * (1024 ** 3))

                # Extraire le nombre de documents depuis storage_details
                details = storage_data.get("storage_details", "")
                documents_count = 0
                if "Total amount of documents:" in details:
                    try:
                        doc_line = details.split("\n")[0]
                        documents_count = int(doc_line.split(":")[1].strip())
                    except (IndexError, ValueError):
                        pass

                return {
                    "used": used_bytes,
                    "total": total_bytes,
                    "percentage": storage_data["storage_percentage"],
                    "documentsCount": documents_count,
                    "details": storage_data["storage_details"],
                    "lastUpdated": datetime.utcnow().isoformat() + "Z"
                }
            else:
                logger.warning(f"ChromaDB storage analysis failed: {result.get('error')}")
                return {
                    "used": 0,
                    "total": int(10 * 1024 * 1024 * 1024),
                    "percentage": 0,
                    "documentsCount": 0,
                    "details": "No data available",
                    "lastUpdated": datetime.utcnow().isoformat() + "Z"
                }

        except Exception as e:
            logger.error(f"_get_storage_info error: {e}")
            return {
                "used": 0,
                "total": int(10 * 1024 * 1024 * 1024),
                "percentage": 0,
                "documentsCount": 0,
                "details": f"Error: {str(e)}",
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            }

    async def _get_metrics(self, user_id: str, company_id: str, mandate_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Récupère les métriques du dashboard depuis le cache Redis.

        Utilise les mêmes clés de cache que l'app Reflex:
        - drive/documents: Router metrics
        - apbookeeper/documents: AP metrics
        - bank/transactions: Bank metrics
        - expenses/details: Expenses metrics (from {mandate_path}/working_doc/expenses_details/items)
        """
        try:
            from .cache.unified_cache_manager import get_firebase_cache_manager

            cache = get_firebase_cache_manager()
            metrics = self._default_metrics()

            # ═══════════════════════════════════════════════════════════════
            # Router metrics from drive/documents cache
            # ═══════════════════════════════════════════════════════════════
            try:
                router_data = await cache.get_cached_data(
                    user_id, company_id, "drive", "documents",
                    ttl_seconds=300
                )
                if router_data and "data" in router_data:
                    data = router_data["data"]
                    metrics["router"]["toProcess"] = len(data.get("to_process", []))
                    metrics["router"]["inProcess"] = len(data.get("in_process", []))
                    metrics["router"]["processed"] = len(data.get("processed", []))
                    logger.info(f"_get_metrics: Router from cache - toProcess={metrics['router']['toProcess']}")
            except Exception as e:
                logger.warning(f"_get_metrics: Router cache error: {e}")

            # ═══════════════════════════════════════════════════════════════
            # APBookkeeper metrics from apbookeeper/documents cache
            # ═══════════════════════════════════════════════════════════════
            try:
                ap_data = await cache.get_cached_data(
                    user_id, company_id, "apbookeeper", "documents",
                    ttl_seconds=300
                )
                if ap_data and "data" in ap_data:
                    data = ap_data["data"]
                    metrics["ap"]["toProcess"] = len(data.get("to_do", []))
                    metrics["ap"]["inProcess"] = len(data.get("in_process", []))
                    metrics["ap"]["pending"] = len(data.get("pending", []))
                    metrics["ap"]["processed"] = len(data.get("processed", []))
                    logger.info(f"_get_metrics: AP from cache - toProcess={metrics['ap']['toProcess']}")
            except Exception as e:
                logger.warning(f"_get_metrics: AP cache error: {e}")

            # ═══════════════════════════════════════════════════════════════
            # Bank metrics from bank/transactions cache
            # ═══════════════════════════════════════════════════════════════
            try:
                bank_data = await cache.get_cached_data(
                    user_id, company_id, "bank", "transactions",
                    ttl_seconds=300
                )
                if bank_data and "data" in bank_data:
                    data = bank_data["data"]
                    metrics["bank"]["toProcess"] = len(data.get("to_reconcile", []))
                    metrics["bank"]["inProcess"] = len(data.get("in_process", []))
                    metrics["bank"]["pending"] = len(data.get("pending", []))
                    metrics["bank"]["matched"] = len(data.get("matched", []))
                    logger.info(f"_get_metrics: Bank from cache - toProcess={metrics['bank']['toProcess']}")
            except Exception as e:
                logger.warning(f"_get_metrics: Bank cache error: {e}")

            # ═══════════════════════════════════════════════════════════════
            # Expenses metrics from working_doc/expenses_details/items
            # (Notes de Frais - real expense reports, not job history)
            # ═══════════════════════════════════════════════════════════════
            try:
                if mandate_path:
                    firebase_mgmt = get_firebase_management()

                    # Fetch from {mandate_path}/working_doc/expenses_details/items
                    expense_reports = await asyncio.to_thread(
                        firebase_mgmt.fetch_expenses_by_mandate,
                        mandate_path
                    )

                    if expense_reports:
                        open_count = 0
                        closed_count = 0

                        for expense_id, expense_data in expense_reports.items():
                            status = expense_data.get("status", "").lower()
                            if status in ("close", "closed", "completed"):
                                closed_count += 1
                            else:
                                open_count += 1

                        metrics["expenses"]["open"] = open_count
                        metrics["expenses"]["closed"] = closed_count
                        metrics["expenses"]["pendingApproval"] = 0
                        logger.info(f"_get_metrics: Expenses from working_doc - open={open_count}, closed={closed_count}")
                else:
                    logger.warning(f"_get_metrics: No mandate_path for expenses metrics")
            except Exception as e:
                logger.warning(f"_get_metrics: Expenses metrics error: {e}")

            # ═══════════════════════════════════════════════════════════════
            # Calculate summary
            # ═══════════════════════════════════════════════════════════════
            total_to_process = (
                metrics["router"]["toProcess"] +
                metrics["ap"]["toProcess"] +
                metrics["bank"]["toProcess"]
            )
            total_in_progress = (
                metrics["router"]["inProcess"] +
                metrics["ap"]["inProcess"] +
                metrics["bank"]["inProcess"]
            )
            total_completed = (
                metrics["router"]["processed"] +
                metrics["ap"]["processed"] +
                metrics["bank"]["matched"]
            )
            total_all = total_to_process + total_in_progress + total_completed

            metrics["summary"] = {
                "totalDocumentsToProcess": total_to_process,
                "totalInProgress": total_in_progress,
                "totalCompleted": total_completed,
                "completionRate": round((total_completed / total_all) * 100, 1) if total_all > 0 else 0
            }

            return metrics
        except Exception as e:
            logger.error(f"_get_metrics error: {e}")
            return self._default_metrics()

    async def _get_jobs_by_category(self, user_id: str, company_id: str) -> Dict[str, List[Dict]]:
        """Récupère les jobs groupés par catégorie."""
        try:
            db = get_firestore()

            jobs = {
                "apbookeeper": [],
                "router": [],
                "banker": []
            }

            # APBookkeeper jobs (pending)
            ap_ref = db.collection("clients").document(user_id).collection("ap_jobs")
            ap_docs = (
                ap_ref
                .where(filter=FieldFilter("company_id", "==", company_id))
                .where(filter=FieldFilter("status", "in", ["pending", "processing"]))
                .limit(20)
                .stream()
            )

            for doc in ap_docs:
                data = doc.to_dict() or {}
                jobs["apbookeeper"].append(self._format_job(doc.id, data, "invoice_processing"))

            # Router jobs
            router_ref = db.collection("clients").document(user_id).collection("router_jobs")
            router_docs = (
                router_ref
                .where(filter=FieldFilter("company_id", "==", company_id))
                .where(filter=FieldFilter("status", "in", ["pending", "processing"]))
                .limit(20)
                .stream()
            )

            for doc in router_docs:
                data = doc.to_dict() or {}
                jobs["router"].append(self._format_job(doc.id, data, "document_scan"))

            # Banker jobs
            banker_ref = db.collection("clients").document(user_id).collection("banker_jobs")
            banker_docs = (
                banker_ref
                .where(filter=FieldFilter("company_id", "==", company_id))
                .where(filter=FieldFilter("status", "in", ["pending", "processing"]))
                .limit(20)
                .stream()
            )

            for doc in banker_docs:
                data = doc.to_dict() or {}
                jobs["banker"].append(self._format_job(doc.id, data, "bank_reconciliation"))

            return jobs
        except Exception as e:
            logger.error(f"_get_jobs_by_category error: {e}")
            return {"apbookeeper": [], "router": [], "banker": []}

    def _format_job(self, job_id: str, data: Dict, job_type: str) -> Dict[str, Any]:
        """Formate un job pour la réponse."""
        return {
            "id": job_id,
            "type": job_type,
            "status": data.get("status", "pending"),
            "companyId": data.get("company_id", ""),
            "title": data.get("title", f"Job {job_id[:8]}"),
            "description": data.get("description"),
            "createdBy": data.get("created_by", ""),
            "createdByName": data.get("created_by_name"),
            "createdAt": data.get("created_at", datetime.utcnow().isoformat() + "Z"),
            "updatedAt": data.get("updated_at", datetime.utcnow().isoformat() + "Z"),
            "progress": data.get("progress", 0),
            "priority": data.get("priority", "medium"),
            "estimatedCompletion": data.get("estimated_completion"),
            "metadata": data.get("metadata", {})
        }

    async def _get_pending_approvals(self, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        """Récupère les approbations en attente."""
        try:
            db = get_firestore()

            approvals = []
            approvals_ref = db.collection("clients").document(user_id).collection("approvals")
            approval_docs = (
                approvals_ref
                .where(filter=FieldFilter("company_id", "==", company_id))
                .where(filter=FieldFilter("status", "==", "pending"))
                .limit(50)
                .stream()
            )

            for doc in approval_docs:
                data = doc.to_dict() or {}
                approvals.append({
                    "id": doc.id,
                    "type": data.get("type", "document"),
                    "title": data.get("title", ""),
                    "description": data.get("description"),
                    "amount": data.get("amount"),
                    "currency": data.get("currency"),
                    "companyId": company_id,
                    "requestedBy": data.get("requested_by", ""),
                    "requestedByName": data.get("requested_by_name"),
                    "requestedByAvatar": data.get("requested_by_avatar"),
                    "requestedAt": data.get("created_at", datetime.utcnow().isoformat() + "Z"),
                    "status": "pending",
                    "priority": data.get("priority", "medium"),
                    "dueDate": data.get("due_date"),
                    "relatedEntityId": data.get("related_entity_id"),
                    "metadata": data.get("metadata", {})
                })

            return approvals
        except Exception as e:
            logger.error(f"_get_pending_approvals error: {e}")
            return []

    async def _get_tasks(self, user_id: str, company_id: str, mandate_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Récupère les tâches planifiées depuis {mandate_path}/tasks.

        Uses FirebaseManagement.list_tasks_for_mandate() which queries the correct collection.
        """
        try:
            logger.info(f"_get_tasks called with: user_id={user_id}, company_id={company_id}, mandate_path={mandate_path}")

            if not mandate_path:
                logger.warning("_get_tasks: No mandate_path provided, returning empty list")
                return []

            # Use the correct method that queries {mandate_path}/tasks
            firebase_mgmt = get_firebase_management()
            raw_tasks = await asyncio.to_thread(
                firebase_mgmt.list_tasks_for_mandate,
                mandate_path
            )

            if not raw_tasks:
                return []

            tasks = []
            for task in raw_tasks:
                mission = task.get("mission", {})
                schedule = task.get("schedule", {})

                tasks.append({
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
                })

            logger.info(f"_get_tasks: Found {len(tasks)} tasks for mandate_path={mandate_path}")
            return tasks
        except Exception as e:
            logger.error(f"_get_tasks error: {e}", exc_info=True)
            return []

    async def _get_expenses(
        self,
        user_id: str,
        company_id: str,
        mandate_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Récupère les expenses depuis task_manager (comme Reflex JobHistory.fetch_expenses).

        Source: clients/{user_id}/task_manager filtrés par mandate_path
        Chaque doc task_manager contient billing info + department_data

        Args:
            user_id: Firebase user ID
            company_id: Company/mandate ID
            mandate_path: Full Firestore path to mandate (from orchestration)

        Returns:
            Dict avec structure:
            {
                "open": [...],      # Liste des expenses ouvertes (status != completed)
                "closed": [...],    # Liste des expenses fermées (status == completed)
                "metrics": {
                    "totalOpen": 5,
                    "totalClosed": 12,
                    "totalAmount": 15000.00,
                    "totalTokens": 125000
                }
            }
        """
        from datetime import datetime, timezone

        def convert_timestamp(raw_ts):
            """Convert various timestamp formats to ISO string."""
            if raw_ts is None:
                return None
            try:
                # Firestore Timestamp-like (objet avec .timestamp())
                if hasattr(raw_ts, 'timestamp'):
                    return datetime.fromtimestamp(raw_ts.timestamp(), tz=timezone.utc).isoformat()
                # Dict Firestore export {seconds, nanoseconds}
                if isinstance(raw_ts, dict) and ('seconds' in raw_ts or '_seconds' in raw_ts):
                    seconds = raw_ts.get('seconds', raw_ts.get('_seconds')) or 0
                    nanos = raw_ts.get('nanoseconds', raw_ts.get('_nanoseconds')) or 0
                    return datetime.fromtimestamp(float(seconds) + float(nanos)/1e9, tz=timezone.utc).isoformat()
                # Nombre epoch (s ou ms)
                if isinstance(raw_ts, (int, float)):
                    ts_val = float(raw_ts)
                    if ts_val > 1e12:  # ms
                        return datetime.fromtimestamp(ts_val / 1000.0, tz=timezone.utc).isoformat()
                    return datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat()
                # String
                if isinstance(raw_ts, str):
                    return raw_ts  # Already string
            except Exception:
                pass
            return None

        try:
            from .cache.unified_cache_manager import get_firebase_cache_manager
            from .firebase_providers import get_firebase_management

            # 1. Tentative de récupération depuis le cache Redis
            cache = get_firebase_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "expenses",
                "details",
                ttl_seconds=300  # 5 minutes TTL
            )

            if cached and cached.get("data"):
                logger.info(f"_get_expenses company_id={company_id} source=cache")
                return cached["data"]

            # 2. Resolve mandate_path if not provided
            db = get_firestore()

            if not mandate_path:
                # Chercher dans mandates
                mandate_ref = db.collection("mandates").document(company_id)
                mandate_doc = mandate_ref.get()

                if mandate_doc.exists:
                    mandate_data = mandate_doc.to_dict() or {}
                    mandate_path = mandate_data.get("mandate_path", "")

                # Fallback: chercher dans clients/{user_id}/companies
                if not mandate_path:
                    company_ref = db.collection("clients").document(user_id).collection("companies").document(company_id)
                    company_doc = company_ref.get()

                    if company_doc.exists:
                        company_data = company_doc.to_dict() or {}
                        mandate_path = company_data.get("mandate_path", "")

            if not mandate_path:
                logger.warning(f"_get_expenses: No mandate_path found for company_id={company_id}")
                return {
                    "open": [],
                    "closed": [],
                    "metrics": {"totalOpen": 0, "totalClosed": 0, "totalAmount": 0, "totalTokens": 0}
                }

            # 3. Utiliser list_task_manager_by_mandate_path (comme Reflex JobHistory)
            firebase_mgmt = get_firebase_management()

            task_docs = await asyncio.to_thread(
                firebase_mgmt.list_task_manager_by_mandate_path,
                user_id,
                mandate_path,
                2000  # limit
            )

            # 4. Transformer chaque doc task_manager en ExpenseItem
            open_expenses = []
            closed_expenses = []
            total_amount = 0.0
            total_tokens = 0

            for task_data in (task_docs or []):
                job_id = task_data.get("id") or task_data.get("job_id") or ""
                if not job_id:
                    continue

                billing = task_data.get("billing") or {}

                # Timestamp: priorité billing.billing_timestamp, sinon timestamp du doc
                raw_ts = (
                    billing.get("billing_timestamp")
                    or task_data.get("timestamp")
                    or task_data.get("updated_at")
                )
                timestamp = convert_timestamp(raw_ts)

                # Department normalization
                department_raw = (task_data.get("department") or task_data.get("departement") or "").strip()
                dept_lower = department_raw.lower()
                if dept_lower in ("apbookeeper", "ap_bookeeper"):
                    department = "APBookkeeper"
                elif dept_lower == "router":
                    department = "Router"
                elif dept_lower in ("banker", "bank"):
                    department = "Banker"
                elif dept_lower in ("chat", "chat_usage", "chat_daily"):
                    department = "Chat"
                else:
                    department = department_raw or "Other"

                file_name = task_data.get("file_name", "")

                # Task manager fields
                status = task_data.get("status", "")
                uri_file_link = task_data.get("uri_file_link", "")
                current_step = task_data.get("current_step", "")
                last_message = task_data.get("last_message", "")
                last_outcome = task_data.get("last_outcome", "")
                mandat_name = task_data.get("mandat_name", "")

                # Billing data
                tokens = int(billing.get("total_tokens", 0) or 0)
                cost = float(billing.get("total_sales_price", 0.0) or 0.0)
                currency = billing.get("currency", "CHF")
                billed = bool(billing.get("billed", False))

                total_tokens += tokens
                total_amount += cost

                # Department-specific data
                department_data = task_data.get("department_data", {})
                ap_data = department_data.get("APbookeeper", {}) or department_data.get("Apbookeeper", {}) or {}
                router_data = department_data.get("Router", {}) or department_data.get("router", {}) or {}
                banker_data = department_data.get("Banker", {}) or department_data.get("banker", {}) or {}

                # Build expense item (matching Reflex ExpenseItem structure)
                expense_item = {
                    "id": job_id,
                    "jobId": job_id,
                    "fileName": file_name or job_id,
                    "department": department,
                    "timestamp": timestamp,
                    "totalTokens": tokens,
                    "cost": cost,
                    "currency": currency,
                    "billed": billed,
                    "status": status,
                    "uriFileLink": uri_file_link,
                    "currentStep": current_step,
                    "lastMessage": last_message,
                    "lastOutcome": last_outcome,
                    "mandatName": mandat_name,
                    # APBookkeeper specific
                    "supplierName": ap_data.get("supplier_name", ""),
                    "invoiceRef": ap_data.get("invoice_ref", ""),
                    "invoiceDate": ap_data.get("invoice_date", ""),
                    "dueDate": ap_data.get("due_date", ""),
                    "invoiceDescription": ap_data.get("invoice_description", ""),
                    "amountVatExcluded": float(ap_data.get("amount_vat_excluded", 0) or 0),
                    "amountVatIncluded": float(ap_data.get("amount_vat_included", 0) or 0),
                    "amountVat": float(ap_data.get("amount_vat", 0) or 0),
                    "accountingLines": ap_data.get("accounting_lines", []),
                    # Router specific
                    "routeDestination": router_data.get("destination", ""),
                    "routeConfidence": float(router_data.get("confidence", 0) or 0),
                    # Banker specific
                    "bankAccount": banker_data.get("bank_account", ""),
                    "transactionType": banker_data.get("transaction_type", ""),
                }

                # Grouper par statut
                if status.lower() in ("completed", "close", "closed"):
                    closed_expenses.append(expense_item)
                else:
                    open_expenses.append(expense_item)

            expenses_result = {
                "open": open_expenses,
                "closed": closed_expenses,
                "metrics": {
                    "totalOpen": len(open_expenses),
                    "totalClosed": len(closed_expenses),
                    "totalAmount": round(total_amount, 2),
                    "totalTokens": total_tokens
                }
            }

            logger.info(
                f"_get_expenses company_id={company_id} "
                f"open={len(open_expenses)} closed={len(closed_expenses)} "
                f"source=firebase"
            )

            # 5. Sauvegarder dans le cache
            try:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "expenses",
                    "details",
                    expenses_result,
                    ttl_seconds=300  # 5 minutes TTL
                )
            except Exception as cache_err:
                logger.warning(f"_get_expenses: Cache write error: {cache_err}")

            return expenses_result

        except Exception as e:
            logger.error(f"_get_expenses error: {e}", exc_info=True)
            return {
                "open": [],
                "closed": [],
                "metrics": {"totalOpen": 0, "totalClosed": 0, "totalAmount": 0, "totalTokens": 0}
            }

    async def _get_activity(self, user_id: str, company_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Récupère l'activité récente."""
        try:
            db = get_firestore()

            activity = []
            activity_ref = db.collection("clients").document(user_id).collection("activity")
            activity_docs = (
                activity_ref
                .where(filter=FieldFilter("company_id", "==", company_id))
                .order_by("timestamp", direction="DESCENDING")
                .limit(limit)
                .stream()
            )

            for doc in activity_docs:
                data = doc.to_dict() or {}
                activity.append({
                    "id": doc.id,
                    "type": data.get("type", ""),
                    "title": data.get("title", ""),
                    "description": data.get("description"),
                    "userId": data.get("user_id", ""),
                    "userName": data.get("user_name"),
                    "userAvatar": data.get("user_avatar"),
                    "timestamp": data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
                    "metadata": data.get("metadata", {}),
                    "relatedEntityId": data.get("related_entity_id"),
                    "relatedEntityType": data.get("related_entity_type")
                })

            return activity
        except Exception as e:
            logger.error(f"_get_activity error: {e}")
            return []

    async def _get_alerts(self, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        """Récupère les alertes actives."""
        try:
            db = get_firestore()

            alerts = []
            alerts_ref = db.collection("clients").document(user_id).collection("alerts")
            alert_docs = (
                alerts_ref
                .where(filter=FieldFilter("company_id", "==", company_id))
                .where(filter=FieldFilter("dismissed", "==", False))
                .limit(10)
                .stream()
            )

            for doc in alert_docs:
                data = doc.to_dict() or {}
                alerts.append({
                    "id": doc.id,
                    "type": data.get("type", "info"),
                    "title": data.get("title", ""),
                    "message": data.get("message", ""),
                    "timestamp": data.get("created_at", datetime.utcnow().isoformat() + "Z"),
                    "dismissed": False,
                    "actionUrl": data.get("action_url"),
                    "actionLabel": data.get("action_label"),
                    "expiresAt": data.get("expires_at")
                })

            return alerts
        except Exception as e:
            logger.error(f"_get_alerts error: {e}")
            return []
