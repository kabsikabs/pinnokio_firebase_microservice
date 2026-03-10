"""
Cockpit RPC Executor
====================

Executes accounting RPC methods directly against Neon PostgreSQL
for widget refresh (no LLM involved, zero token cost).

Supported RPC methods:
- ACCOUNTING.search_gl_entries
- ACCOUNTING.get_account_balance
- ACCOUNTING.get_trial_balance
- ACCOUNTING.get_journal_summary
- ACCOUNTING.compare_periods
- ACCOUNTING.get_coa_info

All queries are SELECT-only (read-only access).
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger("cockpit.rpc_executor")

# Mapping of RPC method names to executor functions
_RPC_EXECUTORS: Dict[str, Any] = {}


def _serialize_row(row: dict) -> dict:
    """Convert asyncpg row types to JSON-serializable types."""
    result = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, UUID):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def _parse_date(value: Any) -> Optional[date]:
    """Convert string date to date object for asyncpg compatibility."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


async def _get_company_id(mandate_path: str) -> Optional[str]:
    """Resolve company_id from mandate_path via NeonHRManager."""
    from app.tools.neon_hr_manager import get_neon_hr_manager
    manager = get_neon_hr_manager()
    company_id = await manager.get_company_id_from_mandate_path(mandate_path)
    return str(company_id) if company_id else None


async def _execute_query(query: str, params: list) -> List[Dict]:
    """Execute a read-only query against Neon PostgreSQL."""
    from app.tools.neon_hr_manager import get_neon_hr_manager
    manager = get_neon_hr_manager()
    pool = await manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [_serialize_row(dict(row)) for row in rows]


# =============================================================================
# RPC METHOD IMPLEMENTATIONS
# =============================================================================

async def _search_gl_entries(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Search GL entries with filters."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"entries": [], "count": 0, "error": "company_id not found"}

    conditions = ["ge.company_id = $1", "ge.is_deleted = false"]
    params: list = [company_id]
    idx = 2

    if kwargs.get("date_from"):
        conditions.append(f"ge.entry_date >= ${idx}")
        params.append(_parse_date(kwargs["date_from"]))
        idx += 1
    if kwargs.get("date_to"):
        conditions.append(f"ge.entry_date <= ${idx}")
        params.append(_parse_date(kwargs["date_to"]))
        idx += 1
    if kwargs.get("account_number"):
        conditions.append(f"ge.account_number LIKE ${idx}")
        params.append(f"{kwargs['account_number']}%")
        idx += 1
    if kwargs.get("journal_code"):
        conditions.append(f"ge.journal_code = ${idx}")
        params.append(kwargs["journal_code"])
        idx += 1
    if kwargs.get("search_term"):
        conditions.append(f"(ge.description ILIKE ${idx} OR ge.partner_name ILIKE ${idx} OR ge.document_ref ILIKE ${idx})")
        params.append(f"%{kwargs['search_term']}%")
        idx += 1
    if kwargs.get("min_amount") is not None:
        conditions.append(f"GREATEST(ge.debit, ge.credit) >= ${idx}")
        params.append(float(kwargs["min_amount"]))
        idx += 1
    if kwargs.get("max_amount") is not None:
        conditions.append(f"GREATEST(ge.debit, ge.credit) <= ${idx}")
        params.append(float(kwargs["max_amount"]))
        idx += 1

    limit = min(int(kwargs.get("limit", 50)), 1000)
    where = " AND ".join(conditions)

    query = f"""
        SELECT ge.entry_date, ge.journal_code, ge.document_ref, ge.account_number,
               ge.account_name, ge.description, ge.partner_name,
               ge.debit, ge.credit, ge.currency, ge.entry_state
        FROM accounting.gl_entries ge
        WHERE {where}
        ORDER BY ge.entry_date DESC, ge.entry_id
        LIMIT {limit}
    """
    entries = await _execute_query(query, params)
    return {"entries": entries, "count": len(entries)}


async def _get_account_balance(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Get balance for one or more accounts."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"error": "company_id not found"}

    account_number = kwargs.get("account_number")
    conditions = ["ge.company_id = $1", "ge.is_deleted = false", "ge.entry_state = 'posted'"]
    params: list = [company_id]
    idx = 2

    if account_number:
        conditions.append(f"ge.account_number LIKE ${idx}")
        params.append(f"{account_number}%")
        idx += 1
    if kwargs.get("as_of"):
        conditions.append(f"ge.entry_date <= ${idx}")
        params.append(_parse_date(kwargs["as_of"]))
        idx += 1

    where = " AND ".join(conditions)
    group_by = kwargs.get("group_by", "account")

    if group_by == "month":
        query = f"""
            SELECT DATE_TRUNC('month', ge.entry_date)::date AS month,
                   SUM(ge.debit) AS debit_total, SUM(ge.credit) AS credit_total,
                   SUM(ge.debit) - SUM(ge.credit) AS balance
            FROM accounting.gl_entries ge
            WHERE {where}
            GROUP BY month ORDER BY month
        """
    else:
        query = f"""
            SELECT ge.account_number, ge.account_name,
                   SUM(ge.debit) AS debit_total, SUM(ge.credit) AS credit_total,
                   SUM(ge.debit) - SUM(ge.credit) AS balance
            FROM accounting.gl_entries ge
            WHERE {where}
            GROUP BY ge.account_number, ge.account_name
            ORDER BY ge.account_number
        """
    rows = await _execute_query(query, params)
    return {"accounts": rows, "count": len(rows)}


async def _get_trial_balance(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Get trial balance for a period."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"error": "company_id not found"}

    conditions = ["ge.company_id = $1", "ge.is_deleted = false", "ge.entry_state = 'posted'"]
    params: list = [company_id]
    idx = 2

    if kwargs.get("date_from"):
        conditions.append(f"ge.entry_date >= ${idx}")
        params.append(_parse_date(kwargs["date_from"]))
        idx += 1
    if kwargs.get("date_to"):
        conditions.append(f"ge.entry_date <= ${idx}")
        params.append(_parse_date(kwargs["date_to"]))
        idx += 1
    if kwargs.get("journal_code"):
        conditions.append(f"ge.journal_code = ${idx}")
        params.append(kwargs["journal_code"])
        idx += 1

    where = " AND ".join(conditions)
    group_by = kwargs.get("group_by", "account")

    if group_by == "month":
        query = f"""
            SELECT DATE_TRUNC('month', ge.entry_date)::date AS month,
                   SUM(ge.debit) AS total_debit, SUM(ge.credit) AS total_credit,
                   SUM(ge.debit) - SUM(ge.credit) AS balance
            FROM accounting.gl_entries ge
            WHERE {where}
            GROUP BY month ORDER BY month
        """
    else:
        query = f"""
            SELECT ge.account_number, ge.account_name,
                   coa.account_nature, coa.account_function,
                   SUM(ge.debit) AS debit, SUM(ge.credit) AS credit,
                   SUM(ge.debit) - SUM(ge.credit) AS balance
            FROM accounting.gl_entries ge
            LEFT JOIN core.chart_of_accounts coa
                ON coa.company_id = ge.company_id AND coa.account_number = ge.account_number
            WHERE {where}
            GROUP BY ge.account_number, ge.account_name, coa.account_nature, coa.account_function
            ORDER BY ge.account_number
        """
    rows = await _execute_query(query, params)
    return {"accounts": rows, "count": len(rows)}


async def _get_journal_summary(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Get journal summary for a period."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"error": "company_id not found"}

    conditions = ["ge.company_id = $1", "ge.is_deleted = false", "ge.entry_state = 'posted'"]
    params: list = [company_id]
    idx = 2

    if kwargs.get("date_from"):
        conditions.append(f"ge.entry_date >= ${idx}")
        params.append(_parse_date(kwargs["date_from"]))
        idx += 1
    if kwargs.get("date_to"):
        conditions.append(f"ge.entry_date <= ${idx}")
        params.append(_parse_date(kwargs["date_to"]))
        idx += 1

    where = " AND ".join(conditions)
    query = f"""
        SELECT ge.journal_code,
               j.journal_name, j.journal_type,
               SUM(ge.debit) AS total_debit, SUM(ge.credit) AS total_credit,
               COUNT(*) AS entry_count
        FROM accounting.gl_entries ge
        LEFT JOIN accounting.journals j
            ON j.company_id = ge.company_id AND j.journal_code = ge.journal_code
        WHERE {where}
        GROUP BY ge.journal_code, j.journal_name, j.journal_type
        ORDER BY ge.journal_code
    """
    rows = await _execute_query(query, params)
    return {"journals": rows, "count": len(rows)}


async def _compare_periods(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Compare two periods."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"error": "company_id not found"}

    p1_from = kwargs.get("period1_from")
    p1_to = kwargs.get("period1_to")
    p2_from = kwargs.get("period2_from")
    p2_to = kwargs.get("period2_to")

    if not all([p1_from, p1_to, p2_from, p2_to]):
        return {"error": "Les 4 bornes de periodes sont requises"}

    base_conditions = "ge.company_id = $1 AND ge.is_deleted = false AND ge.entry_state = 'posted'"
    account_filter = ""
    params_extra = []
    if kwargs.get("account_number"):
        account_filter = " AND ge.account_number = $6"
        params_extra = [kwargs["account_number"]]

    query = f"""
        WITH p1 AS (
            SELECT SUM(ge.debit) AS debit, SUM(ge.credit) AS credit,
                   SUM(ge.debit) - SUM(ge.credit) AS balance
            FROM accounting.gl_entries ge
            WHERE {base_conditions} AND ge.entry_date >= $2 AND ge.entry_date <= $3{account_filter}
        ), p2 AS (
            SELECT SUM(ge.debit) AS debit, SUM(ge.credit) AS credit,
                   SUM(ge.debit) - SUM(ge.credit) AS balance
            FROM accounting.gl_entries ge
            WHERE {base_conditions} AND ge.entry_date >= $4 AND ge.entry_date <= $5{account_filter}
        )
        SELECT
            p1.balance AS period1_balance, p1.debit AS period1_debit, p1.credit AS period1_credit,
            p2.balance AS period2_balance, p2.debit AS period2_debit, p2.credit AS period2_credit,
            COALESCE(p1.balance, 0) - COALESCE(p2.balance, 0) AS variation_absolute,
            CASE WHEN COALESCE(p2.balance, 0) != 0
                 THEN ROUND(((COALESCE(p1.balance, 0) - COALESCE(p2.balance, 0)) / ABS(p2.balance)) * 100, 2)
                 ELSE NULL END AS variation_percent
        FROM p1, p2
    """
    params = [company_id, _parse_date(p1_from), _parse_date(p1_to), _parse_date(p2_from), _parse_date(p2_to)] + params_extra
    rows = await _execute_query(query, params)
    return rows[0] if rows else {}


async def _get_coa_info(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Get chart of accounts info."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"error": "company_id not found"}

    conditions = ["coa.company_id = $1", "coa.is_active = true"]
    params: list = [company_id]
    idx = 2

    if kwargs.get("nature"):
        conditions.append(f"coa.account_nature = ${idx}")
        params.append(kwargs["nature"])
        idx += 1
    if kwargs.get("function"):
        conditions.append(f"coa.account_function = ${idx}")
        params.append(kwargs["function"])
        idx += 1
    if kwargs.get("search"):
        conditions.append(f"(coa.account_number ILIKE ${idx} OR coa.account_name ILIKE ${idx})")
        params.append(f"%{kwargs['search']}%")
        idx += 1
    if kwargs.get("account_class"):
        conditions.append(f"coa.account_number LIKE ${idx}")
        params.append(f"{kwargs['account_class']}%")
        idx += 1

    where = " AND ".join(conditions)
    query = f"""
        SELECT coa.account_number, coa.account_name,
               coa.account_nature, coa.account_function,
               coa.erp_account_type, coa.parent_account_number
        FROM core.chart_of_accounts coa
        WHERE {where}
        ORDER BY coa.account_number
        LIMIT 200
    """
    rows = await _execute_query(query, params)
    return {"accounts": rows, "count": len(rows)}


# =============================================================================
# RPC DISPATCHER
# =============================================================================

async def _execute_query_rpc(mandate_path: str, **kwargs) -> Dict[str, Any]:
    """Execute a raw SQL query (read-only) — used by CockpitAgent widgets."""
    company_id = await _get_company_id(mandate_path)
    if not company_id:
        return {"rows": [], "error": "company_id not found"}

    sql = kwargs.get("sql", "")
    if not sql.strip():
        return {"rows": [], "error": "SQL vide"}

    row_limit = min(int(kwargs.get("row_limit", 500)), 1000)

    # Security: only SELECT/WITH
    sql_upper = sql.strip().upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return {"rows": [], "error": "Seules les requetes SELECT/WITH sont autorisees"}

    from app.tools.neon_hr_manager import get_neon_hr_manager
    manager = get_neon_hr_manager()
    pool = await manager.get_pool()

    import asyncio
    async with pool.acquire() as conn:
        try:
            rows = await asyncio.wait_for(
                conn.fetch(f"{sql} LIMIT {row_limit}", company_id),
                timeout=10.0,
            )
            return {"rows": [_serialize_row(dict(r)) for r in rows], "row_count": len(rows), "success": True}
        except asyncio.TimeoutError:
            return {"rows": [], "error": "Timeout (10s)"}
        except Exception as e:
            return {"rows": [], "error": str(e)}


_RPC_EXECUTORS = {
    "ACCOUNTING.search_gl_entries": _search_gl_entries,
    "ACCOUNTING.get_account_balance": _get_account_balance,
    "ACCOUNTING.get_trial_balance": _get_trial_balance,
    "ACCOUNTING.get_journal_summary": _get_journal_summary,
    "ACCOUNTING.compare_periods": _compare_periods,
    "ACCOUNTING.get_coa_info": _get_coa_info,
    "ACCOUNTING.execute_query": _execute_query_rpc,
}


async def execute_accounting_rpc(
    mandate_path: str,
    rpc_method: str,
    rpc_params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute an accounting RPC method directly (no LLM).

    Used by cockpit widget refresh to get fresh data
    without incurring any LLM token cost.
    """
    executor = _RPC_EXECUTORS.get(rpc_method)
    if not executor:
        raise ValueError(f"Methode RPC inconnue: {rpc_method}")

    logger.info(f"[COCKPIT_RPC] Executing {rpc_method} for {mandate_path}")
    return await executor(mandate_path, **rpc_params)
