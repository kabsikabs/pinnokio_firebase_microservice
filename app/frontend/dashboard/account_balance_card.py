"""
Account Balance Card - Data Provider
=====================================

Frontend Component: AccountBalanceCard
Location: src/components/dashboard/account-balance-card.tsx

Data Structure:
    {
        "currentBalance": float,      # current_topping - current_expenses
        "currentMonthExpenses": float, # Sum of expenses for current month
        "lastMonthExpenses": float,    # Sum of expenses for previous month
        "totalCost": float,            # Total of all expenses
        "totalTopping": float          # Total top-ups
    }

Sources (from Reflex AuthState/JobHistory):
    - Balance: FirebaseManagement.get_balance_info(mandate_path)
        -> {'current_topping': X, 'current_expenses': Y}

    - Month costs: Calculated from task_manager documents
        -> billing.total_sales_price field with timestamp

Firebase Paths:
    - Balance: clients/{user_id}/billing/current_balance
    - Expenses: {mandate_path}/task_manager/{task_id} -> billing.total_sales_price
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


async def get_account_balance_data(
    user_id: str,
    company_id: str,
    mandate_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch billing data for the AccountBalanceCard component.

    Args:
        user_id: Firebase user ID
        company_id: Company/mandate ID
        mandate_path: Full Firestore path to mandate (REQUIRED for monthly costs)

    Returns:
        Dict with currentBalance, currentMonthExpenses, lastMonthExpenses, totalCost, totalTopping
    """
    try:
        from ...firebase_client import get_firestore
        from ...firebase_providers import FirebaseManagement

        db = get_firestore()

        # ═══════════════════════════════════════════════════════════════════
        # STEP 1: Resolve mandate_path if not provided
        # ═══════════════════════════════════════════════════════════════════
        if not mandate_path:
            mandate_path = await _resolve_mandate_path(db, user_id, company_id)

        if not mandate_path:
            logger.warning(f"[ACCOUNT_BALANCE] No mandate_path for company_id={company_id}")
            return _default_billing_data()

        # ═══════════════════════════════════════════════════════════════════
        # STEP 2: Get balance info from FirebaseManagement
        # ═══════════════════════════════════════════════════════════════════
        firebase_mgmt = FirebaseManagement()
        balance_info = await asyncio.to_thread(
            firebase_mgmt.get_balance_info,
            mandate_path,
            user_id
        )

        current_balance = balance_info.get('current_balance', 0.0)
        current_topping = balance_info.get('current_topping', 0.0)
        total_expenses = balance_info.get('current_expenses', 0.0)

        # ═══════════════════════════════════════════════════════════════════
        # STEP 3: Calculate monthly expenses from task_manager documents
        # ═══════════════════════════════════════════════════════════════════
        current_month_cost, last_month_cost = await _calculate_monthly_costs(
            db, mandate_path
        )

        logger.info(
            f"[ACCOUNT_BALANCE] company_id={company_id} "
            f"balance={current_balance:.2f} "
            f"current_month={current_month_cost:.2f} "
            f"last_month={last_month_cost:.2f} "
            f"total={total_expenses:.2f}"
        )

        return {
            "currentBalance": current_balance,
            "currentMonthExpenses": current_month_cost,
            "lastMonthExpenses": last_month_cost,
            "totalCost": total_expenses,
            "totalTopping": current_topping
        }

    except Exception as e:
        logger.error(f"[ACCOUNT_BALANCE] Error: {e}", exc_info=True)
        return _default_billing_data()


async def _resolve_mandate_path(db, user_id: str, company_id: str) -> Optional[str]:
    """
    Resolve mandate_path from company_id via Firestore.

    Searches in order:
    1. Firestore mandates/{company_id}
    2. Firestore clients/{user_id}/companies/{company_id}
    """
    try:
        # 1. Try mandates collection
        mandate_ref = db.collection("mandates").document(company_id)
        mandate_doc = mandate_ref.get()

        if mandate_doc.exists:
            mandate_data = mandate_doc.to_dict() or {}
            path = mandate_data.get("mandate_path", "")
            if path:
                logger.info(f"[ACCOUNT_BALANCE] mandate_path from mandates: {path[:50]}...")
                return path

        # 2. Fallback: clients/{user_id}/companies
        company_ref = db.collection("clients").document(user_id).collection("companies").document(company_id)
        company_doc = company_ref.get()

        if company_doc.exists:
            company_data = company_doc.to_dict() or {}
            path = company_data.get("mandate_path", "")
            if path:
                logger.info(f"[ACCOUNT_BALANCE] mandate_path from companies: {path[:50]}...")
                return path

        return None

    except Exception as e:
        logger.error(f"[ACCOUNT_BALANCE] Error resolving mandate_path: {e}")
        return None


async def _calculate_monthly_costs(db, mandate_path: str) -> tuple[float, float]:
    """
    Calculate current month and last month costs from task_manager documents.

    Based on Reflex JobHistory.get_current_month_cost / get_last_month_cost:
    - Iterates through expenses (task_manager docs)
    - Filters by timestamp.year and timestamp.month
    - Sums billing.total_sales_price

    Args:
        db: Firestore client
        mandate_path: Full path to mandate

    Returns:
        Tuple of (current_month_cost, last_month_cost)
    """
    try:
        now = datetime.now()
        current_year = now.year
        current_month = now.month

        # Calculate previous month
        if current_month == 1:
            prev_month = 12
            prev_year = current_year - 1
        else:
            prev_month = current_month - 1
            prev_year = current_year

        current_month_total = 0.0
        last_month_total = 0.0

        # Fetch task_manager documents
        # Path: {mandate_path}/task_manager/{task_id}
        tasks_ref = db.collection(f"{mandate_path}/task_manager")

        # Get all tasks (filter by date client-side)
        task_docs = tasks_ref.stream()

        for doc in task_docs:
            task_data = doc.to_dict()
            if not task_data:
                continue

            # Get billing info
            billing = task_data.get("billing", {})
            cost = float(billing.get("total_sales_price", 0.0) or 0.0)

            if cost <= 0:
                continue

            # Get timestamp
            timestamp = task_data.get("timestamp") or task_data.get("created_at")

            if timestamp:
                # Handle different timestamp formats
                if hasattr(timestamp, 'year'):  # datetime object
                    ts_year = timestamp.year
                    ts_month = timestamp.month
                elif isinstance(timestamp, str):
                    try:
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        ts_year = dt.year
                        ts_month = dt.month
                    except Exception:
                        continue
                else:
                    continue

                # Check if current month
                if ts_year == current_year and ts_month == current_month:
                    current_month_total += cost
                # Check if last month
                elif ts_year == prev_year and ts_month == prev_month:
                    last_month_total += cost

        return current_month_total, last_month_total

    except Exception as e:
        logger.error(f"[ACCOUNT_BALANCE] Error calculating monthly costs: {e}", exc_info=True)
        return 0.0, 0.0


def _default_billing_data() -> Dict[str, Any]:
    """Return default billing data structure."""
    return {
        "currentBalance": 0.0,
        "currentMonthExpenses": 0.0,
        "lastMonthExpenses": 0.0,
        "totalCost": 0.0,
        "totalTopping": 0.0
    }
