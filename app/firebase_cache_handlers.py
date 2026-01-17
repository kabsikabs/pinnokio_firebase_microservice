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
from typing import Any, Dict, List, Optional

from .cache.unified_cache_manager import get_firebase_cache_manager
from .llm_service.redis_namespaces import RedisTTL
from .firebase_client import get_firestore

logger = logging.getLogger("firebase.cache_handlers")


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
        company_id: str
    ) -> Dict[str, Any]:
        """
        Récupère les documents APBookkeeper depuis Firebase avec cache.

        RPC: FIREBASE_CACHE.get_ap_documents

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
                "apbookeeper",
                "documents",
                ttl_seconds=TTL_AP_DOCUMENTS
            )

            if cached and cached.get("data"):
                logger.info(
                    f"FIREBASE_CACHE.get_ap_documents company_id={company_id} "
                    f"count={len(cached['data']) if isinstance(cached['data'], list) else 0} "
                    f"source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase
            db = get_firestore()
            ap_ref = db.collection("mandates").document(company_id).collection("apbookeeper")
            ap_docs = ap_ref.stream()

            documents = []
            for doc in ap_docs:
                doc_data = doc.to_dict()
                doc_data["id"] = doc.id
                documents.append(doc_data)

            logger.info(
                f"FIREBASE_CACHE.get_ap_documents company_id={company_id} "
                f"count={len(documents)} source=firebase"
            )

            # 3. Sync vers Redis
            if documents:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "apbookeeper",
                    "documents",
                    documents,
                    ttl_seconds=TTL_AP_DOCUMENTS
                )

            return {
                "data": documents,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_ap_documents error={e}")
            return {"data": [], "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # BANK TRANSACTIONS
    # ═══════════════════════════════════════════════════════════════

    async def get_bank_transactions(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Récupère les transactions bancaires depuis Firebase avec cache.

        RPC: FIREBASE_CACHE.get_bank_transactions

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
                "bank",
                "transactions",
                ttl_seconds=TTL_BANK_TRANSACTIONS
            )

            if cached and cached.get("data"):
                logger.info(
                    f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                    f"count={len(cached['data']) if isinstance(cached['data'], list) else 0} "
                    f"source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache"
                }

            # 2. Fallback Firebase
            db = get_firestore()
            bank_ref = db.collection("mandates").document(company_id).collection("bank_transactions")
            bank_docs = bank_ref.stream()

            transactions = []
            for doc in bank_docs:
                tx_data = doc.to_dict()
                tx_data["id"] = doc.id
                transactions.append(tx_data)

            logger.info(
                f"FIREBASE_CACHE.get_bank_transactions company_id={company_id} "
                f"count={len(transactions)} source=firebase"
            )

            # 3. Sync vers Redis
            if transactions:
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "bank",
                    "transactions",
                    transactions,
                    ttl_seconds=TTL_BANK_TRANSACTIONS
                )

            return {
                "data": transactions,
                "source": "firebase"
            }

        except Exception as e:
            logger.error(f"FIREBASE_CACHE.get_bank_transactions error={e}")
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
