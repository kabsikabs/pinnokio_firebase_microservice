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

from .cache.unified_cache_manager import get_firebase_cache_manager
from .firebase_client import get_firestore
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
        include_activity: bool = True,
        activity_limit: int = 10
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
                            f"source=cache request_id={request_id}"
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
                self._get_metrics(user_id, company_id),
                self._get_jobs_by_category(user_id, company_id),
                self._get_pending_approvals(user_id, company_id),
                self._get_tasks(user_id, company_id),
                self._get_activity(user_id, company_id, activity_limit) if include_activity else asyncio.coroutine(lambda: [])(),
                self._get_alerts(user_id, company_id),
                return_exceptions=True
            )

            # Extraire les résultats (avec gestion des erreurs)
            company = self._safe_result(results[0], {})
            storage = self._safe_result(results[1], {"used": 0, "total": 0, "percentage": 0})
            metrics = self._safe_result(results[2], self._default_metrics())
            jobs = self._safe_result(results[3], {"apbookeeper": [], "router": [], "banker": []})
            approvals = self._safe_result(results[4], [])
            tasks = self._safe_result(results[5], [])
            activity = self._safe_result(results[6], []) if include_activity else []
            alerts = self._safe_result(results[7], [])

            # Construire la réponse
            dashboard_data = {
                "company": company,
                "storage": storage,
                "metrics": metrics,
                "jobs": jobs,
                "approvals": approvals,
                "tasks": tasks,
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

    async def _get_storage_info(self, user_id: str, company_id: str) -> Dict[str, Any]:
        """Récupère les infos de stockage."""
        try:
            db = get_firestore()

            # Chercher les métadonnées de stockage
            storage_ref = db.collection("clients").document(user_id).collection("storage").document(company_id)
            storage_doc = storage_ref.get()

            if storage_doc.exists:
                data = storage_doc.to_dict() or {}
                used = data.get("used_bytes", 0)
                total = data.get("total_bytes", 10 * 1024 * 1024 * 1024)  # 10GB default
                return {
                    "used": used,
                    "total": total,
                    "percentage": round((used / total) * 100, 1) if total > 0 else 0,
                    "documentsCount": data.get("documents_count", 0),
                    "lastUpdated": data.get("updated_at", datetime.utcnow().isoformat() + "Z")
                }

            return {
                "used": 0,
                "total": 10 * 1024 * 1024 * 1024,
                "percentage": 0,
                "documentsCount": 0,
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            }
        except Exception as e:
            logger.error(f"_get_storage_info error: {e}")
            return {"used": 0, "total": 0, "percentage": 0, "documentsCount": 0}

    async def _get_metrics(self, user_id: str, company_id: str) -> Dict[str, Any]:
        """Récupère les métriques du dashboard."""
        try:
            db = get_firestore()

            # Récupérer les compteurs depuis différentes collections
            metrics = self._default_metrics()

            # Router documents (documents to_do, in_process, processed)
            router_ref = db.collection("clients").document(user_id).collection("router_jobs")
            router_docs = router_ref.where("company_id", "==", company_id).stream()

            for doc in router_docs:
                data = doc.to_dict() or {}
                status = data.get("status", "")
                if status == "to_do":
                    metrics["router"]["toProcess"] += 1
                elif status == "in_process":
                    metrics["router"]["inProcess"] += 1
                elif status == "processed":
                    metrics["router"]["processed"] += 1

            # APBookkeeper documents
            ap_ref = db.collection("clients").document(user_id).collection("ap_documents")
            ap_docs = ap_ref.where("company_id", "==", company_id).stream()

            for doc in ap_docs:
                data = doc.to_dict() or {}
                status = data.get("status", "")
                if status == "to_do":
                    metrics["ap"]["toProcess"] += 1
                elif status == "in_process":
                    metrics["ap"]["inProcess"] += 1
                elif status == "pending":
                    metrics["ap"]["pending"] += 1
                elif status == "processed":
                    metrics["ap"]["processed"] += 1

            # Bank transactions
            bank_ref = db.collection("clients").document(user_id).collection("bank_transactions")
            bank_docs = bank_ref.where("company_id", "==", company_id).stream()

            for doc in bank_docs:
                data = doc.to_dict() or {}
                status = data.get("status", "")
                if status == "to_process":
                    metrics["bank"]["toProcess"] += 1
                elif status == "in_process":
                    metrics["bank"]["inProcess"] += 1
                elif status == "pending":
                    metrics["bank"]["pending"] += 1
                elif status == "matched":
                    metrics["bank"]["matched"] += 1

            # Expenses
            expenses_ref = db.collection("clients").document(user_id).collection("expenses")
            expenses_docs = expenses_ref.where("company_id", "==", company_id).stream()

            for doc in expenses_docs:
                data = doc.to_dict() or {}
                status = data.get("status", "")
                if status == "open":
                    metrics["expenses"]["open"] += 1
                elif status == "closed":
                    metrics["expenses"]["closed"] += 1
                elif status == "pending_approval":
                    metrics["expenses"]["pendingApproval"] += 1

            # Calculer le résumé
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
            ap_docs = ap_ref.where("company_id", "==", company_id).where("status", "in", ["pending", "processing"]).limit(20).stream()

            for doc in ap_docs:
                data = doc.to_dict() or {}
                jobs["apbookeeper"].append(self._format_job(doc.id, data, "invoice_processing"))

            # Router jobs
            router_ref = db.collection("clients").document(user_id).collection("router_jobs")
            router_docs = router_ref.where("company_id", "==", company_id).where("status", "in", ["pending", "processing"]).limit(20).stream()

            for doc in router_docs:
                data = doc.to_dict() or {}
                jobs["router"].append(self._format_job(doc.id, data, "document_scan"))

            # Banker jobs
            banker_ref = db.collection("clients").document(user_id).collection("banker_jobs")
            banker_docs = banker_ref.where("company_id", "==", company_id).where("status", "in", ["pending", "processing"]).limit(20).stream()

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
            approval_docs = approvals_ref.where("company_id", "==", company_id).where("status", "==", "pending").limit(50).stream()

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

    async def _get_tasks(self, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        """Récupère les tâches."""
        try:
            db = get_firestore()

            tasks = []
            tasks_ref = db.collection("clients").document(user_id).collection("tasks")
            task_docs = tasks_ref.where("company_id", "==", company_id).where("status", "in", ["pending", "in_progress"]).limit(50).stream()

            for doc in task_docs:
                data = doc.to_dict() or {}
                tasks.append({
                    "id": doc.id,
                    "title": data.get("title", ""),
                    "description": data.get("description"),
                    "status": data.get("status", "pending"),
                    "priority": data.get("priority", "medium"),
                    "assignedTo": data.get("assigned_to"),
                    "assignedToName": data.get("assigned_to_name"),
                    "dueDate": data.get("due_date"),
                    "createdAt": data.get("created_at", datetime.utcnow().isoformat() + "Z"),
                    "updatedAt": data.get("updated_at", datetime.utcnow().isoformat() + "Z"),
                    "category": data.get("category", "general"),
                    "relatedEntityId": data.get("related_entity_id"),
                    "relatedEntityType": data.get("related_entity_type")
                })

            return tasks
        except Exception as e:
            logger.error(f"_get_tasks error: {e}")
            return []

    async def _get_activity(self, user_id: str, company_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Récupère l'activité récente."""
        try:
            db = get_firestore()

            activity = []
            activity_ref = db.collection("clients").document(user_id).collection("activity")
            activity_docs = activity_ref.where("company_id", "==", company_id).order_by("timestamp", direction="DESCENDING").limit(limit).stream()

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
            alert_docs = alerts_ref.where("company_id", "==", company_id).where("dismissed", "==", False).limit(10).stream()

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
