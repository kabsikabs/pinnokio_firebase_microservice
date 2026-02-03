"""
Metrics Calculator - Calcul des métriques depuis les données business.

PRINCIPE FONDAMENTAL:
    Les métriques NE SONT PAS stockées séparément dans Redis.
    Elles sont CALCULÉES à la demande depuis les données métier.

    Avantage: Quand une transaction change de statut, les métriques
    sont automatiquement cohérentes sans besoin de synchronisation.

USAGE:
    from app.cache.metrics_calculator import MetricsCalculator

    calculator = MetricsCalculator(redis_client)
    metrics = await calculator.get_all_metrics(uid, company_id)

    # Résultat:
    {
        "router": {"toProcess": 5, "inProcess": 2, "pending": 1, "processed": 10},
        "ap": {"toProcess": 3, "inProcess": 1, "pending": 0, "processed": 8},
        "bank": {"toProcess": 12, "inProcess": 4, "pending": 2},
        "expenses": {"open": 7, "closed": 15, "pendingApproval": 3},
        "summary": {
            "totalDocumentsToProcess": 20,
            "totalInProgress": 7,
            "totalCompleted": 18,  # Router + AP only (bank has no completed status)
            "completionRate": 0.45
        }
    }

@see docs/architecture/METRICS_STORES_ARCHITECTURE.md
"""

import json
from typing import Dict, Any, Optional, List
from redis import Redis
import redis.asyncio as aioredis

from app.llm_service.redis_namespaces import (
    build_business_key,
    build_routing_key,
    build_invoices_key,
    build_bank_key,
    build_expenses_key,
    build_dashboard_key,
    BusinessDomain,
)


# ═══════════════════════════════════════════════════════════════
# TYPES
# ═══════════════════════════════════════════════════════════════

class ModuleMetrics:
    """Métriques pour un module (router, ap, bank)."""

    def __init__(
        self,
        to_process: int = 0,
        in_process: int = 0,
        pending: int = 0,
        processed: int = 0,  # Used for router and ap only (not for bank)
    ):
        self.to_process = to_process
        self.in_process = in_process
        self.pending = pending
        self.processed = processed

    def to_dict(self) -> Dict[str, int]:
        return {
            "toProcess": self.to_process,
            "inProcess": self.in_process,
            "pending": self.pending,
            "processed": self.processed,
        }


class BankMetrics(ModuleMetrics):
    """Métriques spécifiques pour le module bancaire - seulement 3 statuts."""

    def to_dict(self) -> Dict[str, int]:
        return {
            "toProcess": self.to_process,
            "inProcess": self.in_process,
            "pending": self.pending,
        }


class ExpenseMetrics:
    """Métriques pour les dépenses."""

    def __init__(
        self,
        open_count: int = 0,
        closed_count: int = 0,
        pending_approval: int = 0,
    ):
        self.open_count = open_count
        self.closed_count = closed_count
        self.pending_approval = pending_approval

    def to_dict(self) -> Dict[str, int]:
        return {
            "open": self.open_count,
            "closed": self.closed_count,
            "pendingApproval": self.pending_approval,
        }


class SummaryMetrics:
    """Métriques agrégées."""

    def __init__(
        self,
        total_to_process: int = 0,
        total_in_progress: int = 0,
        total_completed: int = 0,
    ):
        self.total_to_process = total_to_process
        self.total_in_progress = total_in_progress
        self.total_completed = total_completed

    @property
    def completion_rate(self) -> float:
        total = self.total_to_process + self.total_in_progress + self.total_completed
        if total == 0:
            return 0.0
        return round(self.total_completed / total, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totalDocumentsToProcess": self.total_to_process,
            "totalInProgress": self.total_in_progress,
            "totalCompleted": self.total_completed,
            "completionRate": self.completion_rate,
        }


# ═══════════════════════════════════════════════════════════════
# STATUS MAPPINGS
# ═══════════════════════════════════════════════════════════════

# Statuts qui correspondent à "toProcess"
TO_PROCESS_STATUSES = {"to_process", "to_do", "new", "pending_process", "unprocessed"}

# Statuts qui correspondent à "inProcess"
IN_PROCESS_STATUSES = {"in_process", "processing", "running", "in_progress"}

# Statuts qui correspondent à "pending" (en attente d'approbation)
PENDING_STATUSES = {"pending", "pending_approval", "awaiting_approval", "waiting"}

# Statuts qui correspondent à "processed/matched"
PROCESSED_STATUSES = {"processed", "completed", "done", "matched", "reconciled", "approved"}


def classify_status(status: str) -> str:
    """
    Classifie un statut dans une des 4 catégories.

    Returns:
        "toProcess", "inProcess", "pending", ou "processed"
    """
    status_lower = status.lower() if status else ""

    if status_lower in TO_PROCESS_STATUSES:
        return "toProcess"
    elif status_lower in IN_PROCESS_STATUSES:
        return "inProcess"
    elif status_lower in PENDING_STATUSES:
        return "pending"
    elif status_lower in PROCESSED_STATUSES:
        return "processed"
    else:
        # Par défaut, considérer comme "toProcess" si inconnu
        return "toProcess"


# ═══════════════════════════════════════════════════════════════
# METRICS CALCULATOR
# ═══════════════════════════════════════════════════════════════

class MetricsCalculator:
    """
    Calculateur de métriques depuis les données business Redis.

    Les métriques sont calculées à la demande, pas stockées séparément.
    Cela garantit la cohérence automatique quand les données changent.
    """

    def __init__(self, redis_client: Redis):
        """
        Args:
            redis_client: Client Redis synchrone
        """
        self.redis = redis_client

    def _get_business_data(self, uid: str, company_id: str, domain: str) -> Optional[Dict]:
        """Récupère les données business depuis Redis."""
        key = build_business_key(uid, company_id, domain)
        data = self.redis.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
        return None

    def _count_by_status(self, items: List[Dict], status_field: str = "status") -> Dict[str, int]:
        """
        Compte les items par catégorie de statut.

        Args:
            items: Liste d'items avec un champ status
            status_field: Nom du champ contenant le statut

        Returns:
            {"toProcess": N, "inProcess": N, "pending": N, "processed": N}
        """
        counts = {"toProcess": 0, "inProcess": 0, "pending": 0, "processed": 0}

        for item in items:
            status = item.get(status_field, "")
            category = classify_status(status)
            counts[category] += 1

        return counts

    # ─────────────────────────────────────────────────────────────
    # CALCUL PAR MODULE
    # ─────────────────────────────────────────────────────────────

    def get_routing_metrics(self, uid: str, company_id: str) -> ModuleMetrics:
        """
        Calcule les métriques Router depuis business:{uid}:{cid}:routing.

        Structure attendue:
            {"documents": [{"id": "...", "status": "to_do|in_process|pending|processed", ...}]}
        """
        data = self._get_business_data(uid, company_id, BusinessDomain.ROUTING.value)

        if not data:
            return ModuleMetrics()

        documents = data.get("documents", [])
        counts = self._count_by_status(documents)

        return ModuleMetrics(
            to_process=counts["toProcess"],
            in_process=counts["inProcess"],
            pending=counts["pending"],
            processed=counts["processed"],
        )

    def get_ap_metrics(self, uid: str, company_id: str) -> ModuleMetrics:
        """
        Calcule les métriques APBookkeeper depuis business:{uid}:{cid}:invoices.

        Structure attendue:
            {"items": [{"id": "...", "status": "to_process|in_process|pending|processed", ...}]}
        """
        data = self._get_business_data(uid, company_id, BusinessDomain.INVOICES.value)

        if not data:
            return ModuleMetrics()

        items = data.get("items", [])
        counts = self._count_by_status(items)

        return ModuleMetrics(
            to_process=counts["toProcess"],
            in_process=counts["inProcess"],
            pending=counts["pending"],
            processed=counts["processed"],
        )

    def get_bank_metrics(self, uid: str, company_id: str) -> BankMetrics:
        """
        Calcule les métriques Bank depuis business:{uid}:{cid}:bank.

        Structure attendue:
            {"to_reconcile": [...], "in_process": [...], "pending": [...]}
        """
        data = self._get_business_data(uid, company_id, BusinessDomain.BANK.value)

        if not data:
            return BankMetrics()

        # The data is already organized by status in the cache
        to_reconcile = data.get("to_reconcile", [])
        in_process = data.get("in_process", [])
        pending = data.get("pending", [])

        return BankMetrics(
            to_process=len(to_reconcile),
            in_process=len(in_process),
            pending=len(pending),
            processed=0,  # Not used for bank
        )

    def get_expenses_metrics(self, uid: str, company_id: str) -> ExpenseMetrics:
        """
        Calcule les métriques Expenses depuis business:{uid}:{cid}:expenses.

        Structure attendue:
            {"items": [{"id": "...", "status": "open|closed|pending_approval", ...}]}
        """
        data = self._get_business_data(uid, company_id, BusinessDomain.EXPENSES.value)

        if not data:
            return ExpenseMetrics()

        items = data.get("items", [])

        open_count = 0
        closed_count = 0
        pending_approval = 0

        for item in items:
            status = (item.get("status") or "").lower()
            if status in {"open", "to_process", "in_process"}:
                open_count += 1
            elif status in {"closed", "processed", "completed"}:
                closed_count += 1
            elif status in {"pending", "pending_approval", "awaiting_approval"}:
                pending_approval += 1

        return ExpenseMetrics(
            open_count=open_count,
            closed_count=closed_count,
            pending_approval=pending_approval,
        )

    # ─────────────────────────────────────────────────────────────
    # CALCUL GLOBAL
    # ─────────────────────────────────────────────────────────────

    def get_all_metrics(self, uid: str, company_id: str) -> Dict[str, Any]:
        """
        Calcule toutes les métriques pour un utilisateur et une société.

        Returns:
            {
                "router": {"toProcess": N, "inProcess": N, "pending": N, "processed": N},
                "ap": {"toProcess": N, "inProcess": N, "pending": N, "processed": N},
                "bank": {"toProcess": N, "inProcess": N, "pending": N},
                "expenses": {"open": N, "closed": N, "pendingApproval": N},
                "summary": {
                    "totalDocumentsToProcess": N,
                    "totalInProgress": N,
                    "totalCompleted": N,  # Router + AP only
                    "completionRate": 0.XX
                }
            }
        """
        router = self.get_routing_metrics(uid, company_id)
        ap = self.get_ap_metrics(uid, company_id)
        bank = self.get_bank_metrics(uid, company_id)
        expenses = self.get_expenses_metrics(uid, company_id)

        # Calcul du résumé
        summary = SummaryMetrics(
            total_to_process=router.to_process + ap.to_process + bank.to_process,
            total_in_progress=router.in_process + ap.in_process + bank.in_process,
            total_completed=router.processed + ap.processed + bank.processed,
        )

        return {
            "router": router.to_dict(),
            "ap": ap.to_dict(),
            "bank": bank.to_dict(),
            "expenses": expenses.to_dict(),
            "summary": summary.to_dict(),
        }


# ═══════════════════════════════════════════════════════════════
# ASYNC VERSION
# ═══════════════════════════════════════════════════════════════

class AsyncMetricsCalculator:
    """
    Version asynchrone du calculateur de métriques.

    Usage:
        async_redis = aioredis.from_url("redis://localhost")
        calculator = AsyncMetricsCalculator(async_redis)
        metrics = await calculator.get_all_metrics(uid, company_id)
    """

    def __init__(self, redis_client: aioredis.Redis):
        """
        Args:
            redis_client: Client Redis asynchrone
        """
        self.redis = redis_client

    async def _get_business_data(self, uid: str, company_id: str, domain: str) -> Optional[Dict]:
        """Récupère les données business depuis Redis (async)."""
        key = build_business_key(uid, company_id, domain)
        data = await self.redis.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
        return None

    def _count_by_status(self, items: List[Dict], status_field: str = "status") -> Dict[str, int]:
        """Compte les items par catégorie de statut."""
        counts = {"toProcess": 0, "inProcess": 0, "pending": 0, "processed": 0}

        for item in items:
            status = item.get(status_field, "")
            category = classify_status(status)
            counts[category] += 1

        return counts

    async def get_routing_metrics(self, uid: str, company_id: str) -> ModuleMetrics:
        """Calcule les métriques Router (async)."""
        data = await self._get_business_data(uid, company_id, BusinessDomain.ROUTING.value)

        if not data:
            return ModuleMetrics()

        documents = data.get("documents", [])
        counts = self._count_by_status(documents)

        return ModuleMetrics(
            to_process=counts["toProcess"],
            in_process=counts["inProcess"],
            pending=counts["pending"],
            processed=counts["processed"],
        )

    async def get_ap_metrics(self, uid: str, company_id: str) -> ModuleMetrics:
        """Calcule les métriques APBookkeeper (async)."""
        data = await self._get_business_data(uid, company_id, BusinessDomain.INVOICES.value)

        if not data:
            return ModuleMetrics()

        items = data.get("items", [])
        counts = self._count_by_status(items)

        return ModuleMetrics(
            to_process=counts["toProcess"],
            in_process=counts["inProcess"],
            pending=counts["pending"],
            processed=counts["processed"],
        )

    async def get_bank_metrics(self, uid: str, company_id: str) -> BankMetrics:
        """Calcule les métriques Bank (async)."""
        data = await self._get_business_data(uid, company_id, BusinessDomain.BANK.value)

        if not data:
            return BankMetrics()

        transactions = data.get("transactions", [])
        counts = self._count_by_status(transactions)

        return BankMetrics(
            to_process=counts["toProcess"],
            in_process=counts["inProcess"],
            pending=counts["pending"],
            processed=counts["processed"],
        )

    async def get_expenses_metrics(self, uid: str, company_id: str) -> ExpenseMetrics:
        """Calcule les métriques Expenses (async)."""
        data = await self._get_business_data(uid, company_id, BusinessDomain.EXPENSES.value)

        if not data:
            return ExpenseMetrics()

        items = data.get("items", [])

        open_count = 0
        closed_count = 0
        pending_approval = 0

        for item in items:
            status = (item.get("status") or "").lower()
            if status in {"open", "to_process", "in_process"}:
                open_count += 1
            elif status in {"closed", "processed", "completed"}:
                closed_count += 1
            elif status in {"pending", "pending_approval", "awaiting_approval"}:
                pending_approval += 1

        return ExpenseMetrics(
            open_count=open_count,
            closed_count=closed_count,
            pending_approval=pending_approval,
        )

    async def get_all_metrics(self, uid: str, company_id: str) -> Dict[str, Any]:
        """
        Calcule toutes les métriques (async).

        Utilise asyncio.gather pour paralléliser les requêtes Redis.
        """
        import asyncio

        # Récupération parallèle des données
        router, ap, bank, expenses = await asyncio.gather(
            self.get_routing_metrics(uid, company_id),
            self.get_ap_metrics(uid, company_id),
            self.get_bank_metrics(uid, company_id),
            self.get_expenses_metrics(uid, company_id),
        )

        # Calcul du résumé
        summary = SummaryMetrics(
            total_to_process=router.to_process + ap.to_process + bank.to_process,
            total_in_progress=router.in_process + ap.in_process + bank.in_process,
            total_completed=router.processed + ap.processed + bank.processed,
        )

        return {
            "router": router.to_dict(),
            "ap": ap.to_dict(),
            "bank": bank.to_dict(),
            "expenses": expenses.to_dict(),
            "summary": summary.to_dict(),
        }


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_metrics(redis_client: Redis, uid: str, company_id: str) -> Dict[str, Any]:
    """
    Fonction utilitaire pour calculer les métriques.

    Args:
        redis_client: Client Redis synchrone
        uid: User ID
        company_id: Company ID

    Returns:
        Dict avec les métriques de tous les modules
    """
    calculator = MetricsCalculator(redis_client)
    return calculator.get_all_metrics(uid, company_id)


async def get_metrics_async(
    redis_client: aioredis.Redis,
    uid: str,
    company_id: str,
) -> Dict[str, Any]:
    """
    Fonction utilitaire asynchrone pour calculer les métriques.

    Args:
        redis_client: Client Redis asynchrone
        uid: User ID
        company_id: Company ID

    Returns:
        Dict avec les métriques de tous les modules
    """
    calculator = AsyncMetricsCalculator(redis_client)
    return await calculator.get_all_metrics(uid, company_id)
