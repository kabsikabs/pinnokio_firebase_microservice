"""
Handlers RPC pour le module Accounting.

Ces handlers sont appeles par le serveur RPC (main.py) quand le worker LLM
ou l'UI fait des appels via rpc_call("ACCOUNTING.method", ...).

NAMESPACE: ACCOUNTING

Architecture:
    Worker LLM / Frontend -> rpc_call("ACCOUNTING.search_gl_entries", ...)
                          -> POST /rpc
                          -> _resolve_method("ACCOUNTING.search_gl_entries")
                          -> accounting_rpc_handlers.search_gl_entries()
                          -> NeonAccountingManager (asyncpg pool)

Objectif:
    Centraliser TOUS les acces Neon PostgreSQL accounting ici.
    Le worker LLM ne doit JAMAIS acceder directement a Neon.
    Pattern identique a hr_rpc_handlers.py.

Endpoints disponibles:
    - ACCOUNTING.resolve_company_id   -> mandate_path -> company_id UUID
    - ACCOUNTING.search_gl_entries    -> Recherche GL (stored proc)
    - ACCOUNTING.get_account_balance  -> Soldes par compte/nature/function/month
    - ACCOUNTING.get_trial_balance    -> Balance de verification
    - ACCOUNTING.get_journal_summary  -> Synthese journaux
    - ACCOUNTING.compare_periods      -> Comparaison N vs N-1
    - ACCOUNTING.get_coa_info         -> Plan comptable enrichi
    - ACCOUNTING.get_gl_readiness     -> Etat GL/COA pour pre-check agent
    - ACCOUNTING.lookup_accounts      -> Lookup comptes par numeros
    - ACCOUNTING.get_sync_metadata    -> Metadata de synchronisation
    - ACCOUNTING.list_journals        -> Liste des journaux comptables
    - ACCOUNTING.resolve_journal      -> Resolution journal par code/nom
    - ACCOUNTING.resolve_erp_ids      -> Resolution batch KLK->ERP IDs
    - ACCOUNTING.validate_accounts    -> Validation batch comptes (existence, actif, mapping ERP)
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from .tools.neon_accounting_manager import get_neon_accounting_manager

logger = logging.getLogger("accounting.rpc_handlers")


# ===================================================================
# SERIALIZATION
# ===================================================================

def _serialize(value: Any) -> Any:
    """Serialise les valeurs pour JSON (UUID, date, Decimal, asyncpg.Record)."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if hasattr(value, "keys") and callable(value.keys):
        return {k: _serialize(value[k]) for k in value.keys()}
    return value


# ===================================================================
# HANDLERS CLASS
# ===================================================================

class AccountingRPCHandlers:
    """
    Handlers RPC pour le namespace ACCOUNTING.

    Chaque methode correspond a un endpoint RPC:
    - ACCOUNTING.search_gl_entries -> search_gl_entries()
    - ACCOUNTING.get_account_balance -> get_account_balance()
    - etc.
    """

    NAMESPACE = "ACCOUNTING"

    # ------------------------------------------------------------------
    # resolve_company_id
    # ------------------------------------------------------------------
    async def resolve_company_id(
        self,
        mandate_path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Resout company_id UUID depuis mandate_path via Neon."""
        manager = get_neon_accounting_manager()
        company_id = await manager.get_company_id_from_mandate_path(mandate_path)
        if not company_id:
            return {"success": False, "error": f"Company not found for mandate_path: {mandate_path}"}
        return {"success": True, "company_id": str(company_id)}

    # ------------------------------------------------------------------
    # search_gl_entries
    # ------------------------------------------------------------------
    async def search_gl_entries(
        self,
        mandate_path: str,
        search_term: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        journal_code: Optional[str] = None,
        account_number: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        limit: int = 50,
        **kwargs,
    ) -> Dict[str, Any]:
        """Recherche des ecritures dans le grand livre."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM accounting.search_gl_entries($1, $2, $3, $4, $5, $6, $7, $8, $9) LIMIT $10",
                    company_id,
                    search_term,
                    date.fromisoformat(date_from) if date_from else None,
                    date.fromisoformat(date_to) if date_to else None,
                    journal_code,
                    account_number,
                    min_amount,
                    max_amount,
                    "posted",
                    min(limit, 200),
                )

            entries = _serialize(rows)
            return {
                "success": True,
                "entries": entries,
                "count": len(entries),
                "filters_applied": {
                    k: v for k, v in {
                        "search_term": search_term, "date_from": date_from,
                        "date_to": date_to, "journal_code": journal_code,
                        "account_number": account_number, "min_amount": min_amount,
                        "max_amount": max_amount,
                    }.items() if v is not None
                },
            }
        except Exception as e:
            logger.error("search_gl_entries error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # get_account_balance
    # ------------------------------------------------------------------
    async def get_account_balance(
        self,
        mandate_path: str,
        account_number: str,
        as_of: Optional[str] = None,
        group_by: str = "account",
        **kwargs,
    ) -> Dict[str, Any]:
        """Obtient le solde d'un ou plusieurs comptes."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
            pool = await manager.get_pool()

            if group_by == "nature":
                group_col = "coa.account_nature"
            elif group_by == "function":
                group_col = "coa.account_function"
            elif group_by == "month":
                group_col = "TO_CHAR(gl.entry_date, 'YYYY-MM')"
            else:
                group_col = None  # account-level

            async with pool.acquire() as conn:
                if group_col and group_by != "account":
                    need_coa_join = group_by in ("nature", "function")
                    coa_join = (
                        "LEFT JOIN core.chart_of_accounts coa "
                        "ON coa.company_id = gl.company_id AND coa.account_number = gl.account_number"
                    ) if need_coa_join else ""

                    rows = await conn.fetch(f"""
                        SELECT {group_col} AS group_key,
                               SUM(gl.debit) AS total_debit, SUM(gl.credit) AS total_credit,
                               SUM(gl.debit) - SUM(gl.credit) AS balance, COUNT(*) AS entry_count
                        FROM accounting.gl_entries gl
                        {coa_join}
                        WHERE gl.company_id = $1 AND gl.account_number LIKE $2 || '%'
                          AND gl.entry_date <= $3 AND NOT gl.is_deleted AND gl.entry_state = 'posted'
                        GROUP BY {group_col}
                        ORDER BY {group_col}
                    """, company_id, account_number, as_of_date)
                else:
                    rows = await conn.fetch("""
                        SELECT gl.account_number AS group_key,
                               COALESCE(coa.account_name, gl.account_name) AS label,
                               SUM(gl.debit) AS total_debit, SUM(gl.credit) AS total_credit,
                               SUM(gl.debit) - SUM(gl.credit) AS balance, COUNT(*) AS entry_count
                        FROM accounting.gl_entries gl
                        LEFT JOIN core.chart_of_accounts coa
                            ON coa.company_id = gl.company_id AND coa.account_number = gl.account_number
                        WHERE gl.company_id = $1 AND gl.account_number LIKE $2 || '%'
                          AND gl.entry_date <= $3 AND NOT gl.is_deleted AND gl.entry_state = 'posted'
                        GROUP BY gl.account_number, coa.account_name, gl.account_name
                        ORDER BY gl.account_number
                    """, company_id, account_number, as_of_date)

            return {
                "success": True,
                "account_number_prefix": account_number,
                "as_of": as_of_date.isoformat(),
                "group_by": group_by,
                "balances": _serialize(rows),
            }
        except Exception as e:
            logger.error("get_account_balance error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # get_trial_balance
    # ------------------------------------------------------------------
    async def get_trial_balance(
        self,
        mandate_path: str,
        date_from: str,
        date_to: str,
        journal_code: Optional[str] = None,
        group_by: str = "account",
        **kwargs,
    ) -> Dict[str, Any]:
        """Balance de verification complete."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            d_from = date.fromisoformat(date_from)
            d_to = date.fromisoformat(date_to)
            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                if group_by == "account":
                    rows = await conn.fetch(
                        "SELECT * FROM accounting.get_trial_balance($1, $2, $3, $4)",
                        company_id, d_from, d_to, journal_code,
                    )
                else:
                    col_map = {
                        "nature": "account_nature",
                        "function": "account_function",
                        "class": "account_class",
                    }
                    col = col_map.get(group_by, "account_nature")
                    rows = await conn.fetch(f"""
                        SELECT {col} AS group_key,
                               SUM(total_debit)::NUMERIC(15,2) AS total_debit,
                               SUM(total_credit)::NUMERIC(15,2) AS total_credit,
                               SUM(balance)::NUMERIC(15,2) AS balance,
                               SUM(entry_count) AS entry_count
                        FROM accounting.get_trial_balance($1, $2, $3, $4)
                        GROUP BY {col}
                        ORDER BY {col}
                    """, company_id, d_from, d_to, journal_code)

            rows_serialized = _serialize(rows)
            total_debit = sum(float(r.get("total_debit", 0) or 0) for r in rows_serialized)
            total_credit = sum(float(r.get("total_credit", 0) or 0) for r in rows_serialized)

            return {
                "success": True,
                "period": f"{date_from} - {date_to}",
                "group_by": group_by,
                "rows": rows_serialized,
                "totals": {
                    "total_debit": round(total_debit, 2),
                    "total_credit": round(total_credit, 2),
                    "difference": round(total_debit - total_credit, 2),
                },
                "is_balanced": abs(total_debit - total_credit) < 0.01,
            }
        except Exception as e:
            logger.error("get_trial_balance error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # get_journal_summary
    # ------------------------------------------------------------------
    async def get_journal_summary(
        self,
        mandate_path: str,
        date_from: str,
        date_to: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Synthese des journaux comptables par periode."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            d_from = date.fromisoformat(date_from)
            d_to = date.fromisoformat(date_to)
            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT gl.journal_code,
                           j.journal_name,
                           j.journal_type,
                           COUNT(*) AS entry_count,
                           SUM(gl.debit)::NUMERIC(15,2) AS total_debit,
                           SUM(gl.credit)::NUMERIC(15,2) AS total_credit,
                           MIN(gl.entry_date) AS first_entry,
                           MAX(gl.entry_date) AS last_entry
                    FROM accounting.gl_entries gl
                    LEFT JOIN accounting.journals j
                        ON j.company_id = gl.company_id AND j.journal_code = gl.journal_code
                    WHERE gl.company_id = $1
                      AND gl.entry_date BETWEEN $2 AND $3
                      AND NOT gl.is_deleted
                      AND gl.entry_state = 'posted'
                    GROUP BY gl.journal_code, j.journal_name, j.journal_type
                    ORDER BY gl.journal_code
                """, company_id, d_from, d_to)

            return {
                "success": True,
                "period": f"{date_from} - {date_to}",
                "journals": _serialize(rows),
                "total_journals": len(rows),
            }
        except Exception as e:
            logger.error("get_journal_summary error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # compare_periods
    # ------------------------------------------------------------------
    async def compare_periods(
        self,
        mandate_path: str,
        period1_from: str,
        period1_to: str,
        period2_from: str,
        period2_to: str,
        metric: str = "custom",
        account_number: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Compare deux periodes (N vs N-1)."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            if metric == "revenue":
                where_clause = "AND coa.account_function IN ('income', 'income_other')"
                extra_params = []
            elif metric == "expenses":
                where_clause = "AND coa.account_nature = 'PROFIT_AND_LOSS' AND coa.account_function NOT IN ('income', 'income_other')"
                extra_params = []
            elif metric == "net_income":
                where_clause = "AND coa.account_nature = 'PROFIT_AND_LOSS'"
                extra_params = []
            else:
                if account_number:
                    where_clause = "AND gl.account_number LIKE $4 || '%'"
                    extra_params = [account_number]
                else:
                    where_clause = ""
                    extra_params = []

            async def _get_period_total(conn, from_str: str, to_str: str) -> Dict:
                rows = await conn.fetch(f"""
                    SELECT SUM(gl.debit)::NUMERIC(15,2) AS total_debit,
                           SUM(gl.credit)::NUMERIC(15,2) AS total_credit,
                           (SUM(gl.debit) - SUM(gl.credit))::NUMERIC(15,2) AS balance,
                           COUNT(*) AS entry_count
                    FROM accounting.gl_entries gl
                    LEFT JOIN core.chart_of_accounts coa
                        ON coa.company_id = gl.company_id AND coa.account_number = gl.account_number
                    WHERE gl.company_id = $1
                      AND gl.entry_date BETWEEN $2 AND $3
                      AND NOT gl.is_deleted AND gl.entry_state = 'posted'
                      {where_clause}
                """, company_id, date.fromisoformat(from_str), date.fromisoformat(to_str), *extra_params)
                r = rows[0] if rows else {}
                return _serialize(r) if r else {"total_debit": 0, "total_credit": 0, "balance": 0, "entry_count": 0}

            async with pool.acquire() as conn:
                p1 = await _get_period_total(conn, period1_from, period1_to)
                p2 = await _get_period_total(conn, period2_from, period2_to)

            p1_val = float(p1.get("balance") or 0)
            p2_val = float(p2.get("balance") or 0)
            p1_count = int(p1.get("entry_count") or 0)
            p2_count = int(p2.get("entry_count") or 0)
            variation = p1_val - p2_val
            pct = (variation / abs(p2_val) * 100) if p2_val != 0 else None

            result = {
                "success": True,
                "metric": metric,
                "period_1": {"from": period1_from, "to": period1_to, **p1},
                "period_2": {"from": period2_from, "to": period2_to, **p2},
                "variation": {
                    "absolute": round(variation, 2),
                    "percentage": round(pct, 2) if pct is not None else None,
                    "direction": "up" if variation > 0 else ("down" if variation < 0 else "stable"),
                },
            }

            if metric in ("revenue", "expenses", "net_income") and p1_count == 0 and p2_count == 0:
                result["warning"] = (
                    f"Aucune ecriture trouvee pour metric='{metric}' sur les deux periodes. "
                    "Cause probable: le plan comptable (COA) n'est pas synchronise."
                )

            return result
        except Exception as e:
            logger.error("compare_periods error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # get_coa_info
    # ------------------------------------------------------------------
    async def get_coa_info(
        self,
        mandate_path: str,
        function: Optional[str] = None,
        search: Optional[str] = None,
        nature: Optional[str] = None,
        account_class: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Consulte le plan comptable enrichi."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()
            conditions = ["company_id = $1", "is_active = TRUE", "erp_account_id IS NOT NULL"]
            params: list = [company_id]
            idx = 2

            if search:
                conditions.append(f"(account_number ILIKE ${idx} OR account_name ILIKE ${idx})")
                params.append(f"%{search}%")
                idx += 1

            if nature:
                conditions.append(f"account_nature = ${idx}")
                params.append(nature)
                idx += 1

            if function:
                conditions.append(f"account_function = ${idx}")
                params.append(function)
                idx += 1

            if account_class:
                conditions.append(f"account_class = ${idx}")
                params.append(account_class)
                idx += 1

            where = " AND ".join(conditions)

            async with pool.acquire() as conn:
                rows = await conn.fetch(f"""
                    SELECT account_number, account_name, erp_account_type,
                           account_nature, account_function, account_class,
                           erp_account_id, erp_source
                    FROM core.chart_of_accounts
                    WHERE {where}
                    ORDER BY account_number
                    LIMIT 100
                """, *params)

            return {
                "success": True,
                "accounts": _serialize(rows),
                "count": len(rows),
                "filters": {k: v for k, v in {
                    "function": function, "search": search,
                    "nature": nature, "class": account_class,
                }.items() if v is not None},
            }
        except Exception as e:
            logger.error("get_coa_info error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # get_gl_readiness
    # ------------------------------------------------------------------
    async def get_gl_readiness(
        self,
        mandate_path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Etat de readiness GL/COA pour le pre-check de l'agent accounting."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {
                    "success": True,
                    "company_found": False,
                    "gl_count": 0,
                    "coa_count": 0,
                    "coa_enriched": 0,
                }

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                gl_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM accounting.gl_entries WHERE company_id = $1 AND NOT is_deleted",
                    company_id,
                )
                coa_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM core.chart_of_accounts WHERE company_id = $1 AND is_active = TRUE",
                    company_id,
                )
                coa_enriched = await conn.fetchval(
                    "SELECT COUNT(*) FROM core.chart_of_accounts WHERE company_id = $1 AND is_active = TRUE AND account_nature IS NOT NULL",
                    company_id,
                )
                tables_exist = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = 'accounting' AND table_name = 'gl_entries')"
                )

                sync_meta = await conn.fetchrow(
                    "SELECT * FROM accounting.sync_metadata WHERE company_id = $1 AND sync_type = 'gl'",
                    company_id,
                ) if tables_exist else None

            return {
                "success": True,
                "company_found": True,
                "company_id": str(company_id),
                "gl_count": gl_count or 0,
                "coa_count": coa_count or 0,
                "coa_enriched": coa_enriched or 0,
                "tables_exist": tables_exist or False,
                "sync_metadata": _serialize(dict(sync_meta)) if sync_meta else None,
            }
        except Exception as e:
            logger.error("get_gl_readiness error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # lookup_accounts
    # ------------------------------------------------------------------
    async def lookup_accounts(
        self,
        mandate_path: str,
        account_numbers: Optional[List[str]] = None,
        account_ids: Optional[List[int]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Lookup comptes par numeros ou ERP IDs."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                if account_numbers:
                    rows = await conn.fetch("""
                        SELECT account_number, account_name, erp_account_type,
                               account_nature, account_function, account_class,
                               erp_account_id, is_active
                        FROM core.chart_of_accounts
                        WHERE company_id = $1 AND account_number = ANY($2)
                        ORDER BY account_number
                    """, company_id, account_numbers)
                elif account_ids:
                    rows = await conn.fetch("""
                        SELECT account_number, account_name, erp_account_type,
                               account_nature, account_function, account_class,
                               erp_account_id, is_active
                        FROM core.chart_of_accounts
                        WHERE company_id = $1 AND erp_account_id = ANY($2)
                        ORDER BY account_number
                    """, company_id, account_ids)
                else:
                    return {"success": False, "error": "account_numbers or account_ids required"}

            return {
                "success": True,
                "accounts": _serialize(rows),
                "count": len(rows),
            }
        except Exception as e:
            logger.error("lookup_accounts error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # get_sync_metadata
    # ------------------------------------------------------------------
    async def get_sync_metadata(
        self,
        mandate_path: str,
        sync_type: str = "gl",
        **kwargs,
    ) -> Dict[str, Any]:
        """Recupere les metadata de synchronisation."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            meta = await manager.get_sync_metadata(company_id, sync_type)
            return {
                "success": True,
                "metadata": _serialize(meta) if meta else None,
            }
        except Exception as e:
            logger.error("get_sync_metadata error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # ensure_gl_columns
    # ------------------------------------------------------------------
    async def ensure_gl_columns(
        self,
        mandate_path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Verifie et ajoute les colonnes manquantes dans gl_entries (migration auto)."""
        try:
            manager = get_neon_accounting_manager()
            pool = await manager.get_pool()

            cols_missing = []
            async with pool.acquire() as conn:
                for col in ("amount_currency_value", "currency_erp_id"):
                    exists = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'accounting' AND table_name = 'gl_entries' AND column_name = $1)",
                        col,
                    )
                    if not exists:
                        cols_missing.append(col)

                if cols_missing:
                    await conn.execute("""
                        ALTER TABLE accounting.gl_entries
                            ADD COLUMN IF NOT EXISTS amount_currency_value  NUMERIC(15,2),
                            ADD COLUMN IF NOT EXISTS currency_erp_id        INTEGER
                    """)

            return {
                "success": True,
                "cols_added": cols_missing,
                "force_full_needed": len(cols_missing) > 0,
            }
        except Exception as e:
            logger.error("ensure_gl_columns error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # upsert_coa_from_firebase
    # ------------------------------------------------------------------
    async def upsert_coa_from_firebase(
        self,
        mandate_path: str,
        coa_data: Dict[str, Any],
        deleted_erp_ids: Optional[List[str]] = None,
        erp_source: str = "firebase",
        **kwargs,
    ) -> Dict[str, Any]:
        """Upsert COA dans Neon. Supporte source 'firebase' et 'erp'."""
        try:
            import hashlib as _hashlib

            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()
            upserted = 0
            active_count = 0
            enriched_count = 0
            deactivated = 0

            async with pool.acquire() as conn:
                for acc_id, acc in coa_data.items():
                    if not isinstance(acc, dict):
                        continue

                    account_number = str(acc.get("account_number") or acc.get("code") or acc_id)
                    account_name = acc.get("account_name") or acc.get("display_name") or ""
                    erp_account_type = acc.get("account_type") or ""
                    account_nature = acc.get("klk_account_nature") or None
                    account_function = acc.get("klk_account_function") or None
                    is_active = not acc.get("deprecated", False)

                    hash_input = f"{account_number}|{account_name}|{erp_account_type}|{account_nature}|{account_function}|{is_active}"
                    sync_hash = _hashlib.sha256(hash_input.encode()).hexdigest()

                    await conn.execute(f"""
                        INSERT INTO core.chart_of_accounts
                            (company_id, account_number, account_name, erp_account_type,
                             account_nature, account_function, erp_account_id, is_active, sync_hash, erp_source)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, '{erp_source}')
                        ON CONFLICT (company_id, account_number)
                        DO UPDATE SET
                            account_name = EXCLUDED.account_name,
                            erp_account_type = EXCLUDED.erp_account_type,
                            account_nature = COALESCE(EXCLUDED.account_nature, core.chart_of_accounts.account_nature),
                            account_function = COALESCE(EXCLUDED.account_function, core.chart_of_accounts.account_function),
                            erp_account_id = EXCLUDED.erp_account_id,
                            erp_source = EXCLUDED.erp_source,
                            is_active = EXCLUDED.is_active,
                            sync_hash = EXCLUDED.sync_hash,
                            updated_at = NOW()
                        WHERE core.chart_of_accounts.sync_hash IS DISTINCT FROM EXCLUDED.sync_hash
                    """,
                        company_id, account_number, account_name, erp_account_type,
                        account_nature, account_function, str(acc_id), is_active, sync_hash,
                    )
                    upserted += 1

                    if is_active:
                        active_count += 1
                        if account_nature:
                            enriched_count += 1

                # Deactivate deleted accounts
                if deleted_erp_ids:
                    for del_acc_id in deleted_erp_ids:
                        await conn.execute(
                            "UPDATE core.chart_of_accounts "
                            "SET is_active = FALSE, updated_at = NOW() "
                            "WHERE company_id = $1 AND erp_account_id = $2",
                            company_id, str(del_acc_id),
                        )
                        deactivated += 1

                # Update sync_metadata
                if upserted > 0 or deactivated > 0:
                    import json as _json
                    await conn.execute("""
                        INSERT INTO accounting.sync_metadata
                            (company_id, sync_type, last_sync_time, sync_status,
                             total_entries, active_entries, dataset_version, last_changes)
                        VALUES ($1, 'coa', NOW(), 'idle', $2, $3, 1, $4::jsonb)
                        ON CONFLICT (company_id, sync_type) DO UPDATE SET
                            last_sync_time = EXCLUDED.last_sync_time,
                            sync_status = 'idle',
                            total_entries = EXCLUDED.total_entries,
                            active_entries = EXCLUDED.active_entries,
                            dataset_version = accounting.sync_metadata.dataset_version + 1,
                            last_changes = EXCLUDED.last_changes
                    """,
                        company_id, active_count, enriched_count,
                        _json.dumps({"source": erp_source, "upserted": upserted, "deactivated": deactivated}),
                    )

            return {
                "success": True,
                "upserted": upserted,
                "deactivated": deactivated,
                "active_count": active_count,
                "enriched_count": enriched_count,
            }
        except Exception as e:
            logger.error("upsert_coa_from_firebase error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # remap_gl_account_names
    # ------------------------------------------------------------------
    async def remap_gl_account_names(
        self,
        mandate_path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Remap account_name dans gl_entries depuis COA si COA plus recent."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                coa_meta = await conn.fetchrow(
                    "SELECT last_sync_time FROM accounting.sync_metadata WHERE company_id = $1 AND sync_type = 'coa'",
                    company_id,
                )
                gl_meta = await conn.fetchrow(
                    "SELECT last_sync_time FROM accounting.sync_metadata WHERE company_id = $1 AND sync_type = 'gl'",
                    company_id,
                )

                if not coa_meta or not coa_meta.get("last_sync_time"):
                    return {"success": True, "remapped": False, "reason": "no coa sync_metadata"}

                from datetime import datetime as _dt, timezone as _tz
                coa_time = coa_meta["last_sync_time"]
                gl_time = gl_meta["last_sync_time"] if gl_meta and gl_meta.get("last_sync_time") else _dt.min.replace(tzinfo=_tz.utc)

                if coa_time <= gl_time:
                    return {"success": True, "remapped": False, "reason": "gl fresher than coa"}

                result = await conn.execute("""
                    UPDATE accounting.gl_entries gl
                    SET account_name = coa.account_name
                    FROM core.chart_of_accounts coa
                    WHERE coa.company_id = gl.company_id
                      AND coa.account_number = gl.account_number
                      AND gl.company_id = $1
                      AND coa.account_name IS DISTINCT FROM gl.account_name
                """, company_id)

            return {"success": True, "remapped": True, "result": result}
        except Exception as e:
            logger.error("remap_gl_account_names error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # list_journals
    # ------------------------------------------------------------------
    async def list_journals(
        self,
        mandate_path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Liste tous les journaux comptables d'une societe."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT journal_code, journal_name, journal_type "
                    "FROM accounting.journals WHERE company_id = $1 ORDER BY journal_code",
                    company_id,
                )

            return {
                "success": True,
                "journals": _serialize(rows),
                "count": len(rows),
            }
        except Exception as e:
            logger.error("list_journals error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # resolve_journal
    # ------------------------------------------------------------------
    async def resolve_journal(
        self,
        mandate_path: str,
        journal_input: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Resout un journal par code OU par nom.

        Essaie: code exact -> nom exact (ILIKE) -> nom partiel (ILIKE %...%).
        """
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                # 1. Code exact
                row = await conn.fetchrow(
                    "SELECT journal_code, journal_name, journal_type, erp_journal_id "
                    "FROM accounting.journals WHERE company_id = $1 AND journal_code = $2",
                    company_id, journal_input,
                )
                if not row:
                    # 2. Nom exact ILIKE
                    row = await conn.fetchrow(
                        "SELECT journal_code, journal_name, journal_type, erp_journal_id "
                        "FROM accounting.journals WHERE company_id = $1 AND journal_name ILIKE $2",
                        company_id, journal_input,
                    )
                if not row:
                    # 3. Nom partiel ILIKE
                    row = await conn.fetchrow(
                        "SELECT journal_code, journal_name, journal_type, erp_journal_id "
                        "FROM accounting.journals WHERE company_id = $1 AND journal_name ILIKE $2 LIMIT 1",
                        company_id, f"%{journal_input}%",
                    )

            if not row:
                return {
                    "success": False,
                    "error": f"Journal '{journal_input}' introuvable (ni par code, ni par nom).",
                }

            return {
                "success": True,
                "journal": _serialize(dict(row)),
            }
        except Exception as e:
            logger.error("resolve_journal error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # resolve_erp_ids
    # ------------------------------------------------------------------
    async def resolve_erp_ids(
        self,
        mandate_path: str,
        journal_code: str,
        account_numbers: List[str],
        currency: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Resout les IDs KLK en IDs ERP pour une ecriture.

        - journal_code -> erp_journal_id
        - account_numbers -> {account_number: erp_account_id}
        - currency (optionnel) -> currency_erp_id
        """
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()
            result: Dict[str, Any] = {"success": True}

            async with pool.acquire() as conn:
                # 1. Journal -> erp_journal_id
                row = await conn.fetchrow(
                    "SELECT erp_journal_id, journal_name FROM accounting.journals "
                    "WHERE company_id = $1 AND journal_code = $2",
                    company_id, journal_code,
                )
                if row and row["erp_journal_id"]:
                    result["erp_journal_id"] = int(row["erp_journal_id"])
                else:
                    return {
                        "success": False,
                        "error": f"Journal '{journal_code}' introuvable ou sans erp_journal_id",
                    }

                # 2. Accounts -> erp_account_id (batch)
                acc_rows = await conn.fetch(
                    "SELECT account_number, erp_account_id, is_active "
                    "FROM core.chart_of_accounts "
                    "WHERE company_id = $1 AND account_number = ANY($2)",
                    company_id, account_numbers,
                )
                accounts_map = {}
                errors = []
                found = {r["account_number"]: r for r in acc_rows}
                for acc_num in account_numbers:
                    r = found.get(acc_num)
                    if not r:
                        errors.append(f"Compte '{acc_num}' introuvable")
                    elif not r["is_active"]:
                        errors.append(f"Compte '{acc_num}' desactive")
                    elif not r["erp_account_id"]:
                        errors.append(f"Compte '{acc_num}' sans erp_account_id")
                    else:
                        accounts_map[acc_num] = int(r["erp_account_id"])

                if errors:
                    return {"success": False, "error": "; ".join(errors)}
                result["accounts_map"] = accounts_map

                # 3. Currency -> currency_erp_id (optionnel)
                if currency:
                    currency_erp_id = await conn.fetchval(
                        "SELECT erp_currency_id FROM core.erp_currencies "
                        "WHERE company_id = $1 AND currency_code = $2",
                        company_id, currency.upper(),
                    )
                    result["currency_erp_id"] = int(currency_erp_id) if currency_erp_id else None

            return result
        except Exception as e:
            logger.error("resolve_erp_ids error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # validate_accounts
    # ------------------------------------------------------------------
    async def validate_accounts(
        self,
        mandate_path: str,
        account_numbers: List[str],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Valide une liste de comptes: existence, is_active, erp_account_id.

        Retourne un dict par account_number avec status et details.
        """
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT account_number, account_name, is_active, erp_account_id "
                    "FROM core.chart_of_accounts "
                    "WHERE company_id = $1 AND account_number = ANY($2)",
                    company_id, account_numbers,
                )

            found = {r["account_number"]: dict(r) for r in rows}
            results = {}
            errors = []

            for acc_num in account_numbers:
                r = found.get(acc_num)
                if not r:
                    results[acc_num] = {"valid": False, "error": "non trouve dans le plan comptable"}
                    errors.append(f"Compte {acc_num} non trouve dans le plan comptable")
                elif not r["is_active"]:
                    results[acc_num] = {"valid": False, "error": "desactive"}
                    errors.append(f"Compte {acc_num} est desactive")
                elif not r["erp_account_id"]:
                    results[acc_num] = {"valid": False, "error": "sans mapping ERP (erp_account_id manquant)"}
                    errors.append(f"Compte {acc_num} sans mapping ERP (erp_account_id manquant)")
                else:
                    results[acc_num] = {"valid": True, "account_name": r["account_name"], "erp_account_id": int(r["erp_account_id"])}

            return {
                "success": True,
                "accounts": _serialize(results),
                "errors": errors,
                "all_valid": len(errors) == 0,
            }
        except Exception as e:
            logger.error("validate_accounts error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # check_journals_freshness
    # ------------------------------------------------------------------
    async def check_journals_freshness(
        self,
        mandate_path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Compare sync_metadata timestamps pour COA vs Journals."""
        try:
            manager = get_neon_accounting_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            if not company_id:
                return {"success": False, "error": "Company not found"}

            pool = await manager.get_pool()

            async with pool.acquire() as conn:
                coa_meta = await conn.fetchrow(
                    "SELECT last_sync_time FROM accounting.sync_metadata WHERE company_id = $1 AND sync_type = 'coa'",
                    company_id,
                )
                journals_meta = await conn.fetchrow(
                    "SELECT last_sync_time FROM accounting.sync_metadata WHERE company_id = $1 AND sync_type = 'journals'",
                    company_id,
                )

            return {
                "success": True,
                "coa_last_sync": _serialize(coa_meta["last_sync_time"]) if coa_meta and coa_meta.get("last_sync_time") else None,
                "journals_last_sync": _serialize(journals_meta["last_sync_time"]) if journals_meta and journals_meta.get("last_sync_time") else None,
                "needs_journal_sync": (
                    coa_meta and coa_meta.get("last_sync_time") and
                    (not journals_meta or not journals_meta.get("last_sync_time") or
                     coa_meta["last_sync_time"] > journals_meta["last_sync_time"])
                ),
            }
        except Exception as e:
            logger.error("check_journals_freshness error: %s", e)
            return {"success": False, "error": str(e)}


# ===================================================================
# SINGLETON
# ===================================================================

_handlers: Optional[AccountingRPCHandlers] = None


def get_accounting_handlers() -> AccountingRPCHandlers:
    global _handlers
    if _handlers is None:
        _handlers = AccountingRPCHandlers()
    return _handlers
