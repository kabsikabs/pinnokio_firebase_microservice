"""
Metrics RPC Handlers
====================

Provides metrics data fetching for all modules.
Leverages the existing dashboard_handlers._get_metrics() method.

NAMESPACE: METRICS

Endpoints:
- METRICS.full_data: Get all metrics (routing, ap, bank, expenses)
- METRICS.module_data: Get metrics for a specific module
"""

import logging
from typing import Any, Dict, Optional

from app.dashboard_handlers import get_dashboard_handlers

logger = logging.getLogger("metrics.handlers")


# ============================================
# Singleton
# ============================================

_metrics_handlers_instance: Optional["MetricsHandlers"] = None


def get_metrics_handlers() -> "MetricsHandlers":
    """Singleton accessor for metrics handlers."""
    global _metrics_handlers_instance
    if _metrics_handlers_instance is None:
        _metrics_handlers_instance = MetricsHandlers()
    return _metrics_handlers_instance


class MetricsHandlers:
    """
    RPC handlers for METRICS namespace.

    Delegates to DashboardHandlers._get_metrics() for actual data fetching,
    ensuring consistency between dashboard and metrics stores.
    """

    NAMESPACE = "METRICS"

    async def full_data(
        self,
        user_id: str,
        company_id: str,
        mandate_path: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Get all metrics for the 4 modules.

        RPC: METRICS.full_data

        Args:
            user_id: Firebase UID
            company_id: Company/mandate ID
            mandate_path: Full Firestore path to mandate
            force_refresh: Force cache invalidation

        Returns:
            {
                "success": True,
                "data": {
                    "routing": {"toProcess": 0, "inProcess": 0, "pending": 0, "processed": 0},
                    "ap": {"toProcess": 0, "inProcess": 0, "pending": 0, "processed": 0},
                    "bank": {"toProcess": 0, "inProcess": 0, "pending": 0},
                    "expenses": {"open": 0, "closed": 0, "pendingApproval": 0},
                    "summary": {...},
                    "company_id": "xxx",
                    "timestamp": "2024-01-20T12:00:00Z"
                }
            }
        """
        try:
            from datetime import datetime

            # If force_refresh, invalidate cache first
            if force_refresh:
                await self._invalidate_metrics_cache(user_id, company_id)

            # Delegate to dashboard handlers for actual metrics fetching
            dashboard_handlers = get_dashboard_handlers()
            metrics = await dashboard_handlers._get_metrics(user_id, company_id, mandate_path)

            # Add metadata
            metrics["company_id"] = company_id
            metrics["timestamp"] = datetime.utcnow().isoformat() + "Z"

            logger.info(
                f"METRICS.full_data company_id={company_id} "
                f"routing={metrics.get('router', {}).get('toProcess', 0)} "
                f"ap={metrics.get('ap', {}).get('toProcess', 0)} "
                f"bank={metrics.get('bank', {}).get('toProcess', 0)}"
            )

            return {"success": True, "data": metrics}

        except Exception as e:
            logger.error(f"METRICS.full_data error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "METRICS_FETCH_ERROR", "message": str(e)},
            }

    async def module_data(
        self,
        user_id: str,
        company_id: str,
        module: str,
        mandate_path: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Get metrics for a specific module.

        RPC: METRICS.module_data

        Args:
            user_id: Firebase UID
            company_id: Company/mandate ID
            module: Module name (routing, ap, bank, expenses)
            mandate_path: Full Firestore path to mandate
            force_refresh: Force cache invalidation

        Returns:
            {"success": True, "data": {"toProcess": 0, ...}}
        """
        try:
            valid_modules = ["routing", "ap", "bank", "expenses"]
            if module not in valid_modules:
                return {
                    "success": False,
                    "error": {
                        "code": "INVALID_MODULE",
                        "message": f"Module must be one of: {valid_modules}",
                    },
                }

            # Get full metrics and extract the requested module
            result = await self.full_data(user_id, company_id, mandate_path, force_refresh)

            if not result.get("success"):
                return result

            metrics = result["data"]

            # Map module names (router vs routing)
            module_key = "router" if module == "routing" else module
            module_data = metrics.get(module_key, {})

            logger.info(f"METRICS.module_data module={module} company_id={company_id}")

            return {"success": True, "data": module_data, "module": module}

        except Exception as e:
            logger.error(f"METRICS.module_data error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "METRICS_MODULE_ERROR", "message": str(e)},
            }

    async def _invalidate_metrics_cache(self, user_id: str, company_id: str) -> None:
        """Invalidate all metrics-related caches."""
        try:
            from app.cache.unified_cache_manager import (
                get_firebase_cache_manager,
                get_drive_cache_manager,
            )

            firebase_cache = get_firebase_cache_manager()
            drive_cache = get_drive_cache_manager()

            # Invalidate drive/documents (router metrics)
            try:
                await drive_cache.delete_cached_data(user_id, company_id, "drive", "documents")
            except Exception:
                pass

            # Invalidate apbookeeper/documents
            try:
                await firebase_cache.delete_cached_data(
                    user_id, company_id, "apbookeeper", "documents"
                )
            except Exception:
                pass

            # Invalidate bank/transactions
            try:
                await firebase_cache.delete_cached_data(
                    user_id, company_id, "bank", "transactions"
                )
            except Exception:
                pass

            # Invalidate expenses/details
            try:
                await firebase_cache.delete_cached_data(
                    user_id, company_id, "expenses", "details"
                )
            except Exception:
                pass

            # Invalidate dashboard cache (includes metrics)
            try:
                await firebase_cache.delete_cached_data(
                    user_id, company_id, "dashboard", "full_data"
                )
            except Exception:
                pass

            logger.info(f"METRICS cache invalidated for company_id={company_id}")

        except Exception as e:
            logger.warning(f"METRICS cache invalidation error: {e}")
