"""
Gestionnaire Neon Fixed Assets — Module Immobilisations internalisé.

Reutilise le pool de connexions de NeonAccountingManager (singleton partage).
Engine d'amortissement lineaire + degressif avec prorata temporis.
Generation d'ecritures GL (journal OD) pour dotations et cessions.

Usage:
    from app.tools.neon_fixed_asset_manager import get_fixed_asset_manager

    manager = get_fixed_asset_manager()
    models = await manager.list_asset_models(company_id)
"""

import calendar
import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

try:
    import asyncpg
except ImportError:
    asyncpg = None

from .neon_accounting_manager import get_neon_accounting_manager

logger = logging.getLogger("fixed_assets.neon_manager")


def _dec(value) -> Decimal:
    """Convert to Decimal, safe."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round2(d: Decimal) -> Decimal:
    """Round to 2 decimal places."""
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _end_of_month(d: date) -> date:
    """Return last day of the month for a given date."""
    _, last_day = calendar.monthrange(d.year, d.month)
    return date(d.year, d.month, last_day)


def _add_months(d: date, months: int) -> date:
    """Add months to a date, clamping to end of month."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    _, last_day = calendar.monthrange(year, month)
    day = min(d.day, last_day)
    return date(year, month, day)


class NeonFixedAssetManager:
    """
    Manager pour le schema fixed_assets dans Neon PostgreSQL.
    Singleton thread-safe, reutilise le pool du NeonAccountingManager.
    """

    _instance: Optional["NeonFixedAssetManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    async def _get_pool(self) -> "asyncpg.Pool":
        """Reutilise le pool du NeonAccountingManager."""
        mgr = get_neon_accounting_manager()
        return await mgr.get_pool()

    async def _resolve_company(self, mandate_path: str) -> Optional[UUID]:
        """Resolve mandate_path -> company_id."""
        mgr = get_neon_accounting_manager()
        return await mgr.get_company_id_from_mandate_path(mandate_path)

    # ===================================================================
    # ELIGIBLE ACCOUNTS (for pickers)
    # ===================================================================

    async def get_eligible_accounts(
        self, company_id: UUID, include_all_active: bool = True
    ) -> List[Dict[str, Any]]:
        """Return COA accounts eligible for fixed asset mapping.

        Args:
            include_all_active: If True, also returns all active accounts
                with role='all' (for disposal account picker).
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT account_number, account_name, account_function, role
                FROM fixed_assets.eligible_accounts
                WHERE company_id = $1
                ORDER BY role, account_number
                """,
                company_id,
            )
            result = [dict(r) for r in rows]

            if include_all_active:
                all_rows = await conn.fetch(
                    """
                    SELECT account_number, account_name, account_function,
                           'all' AS role
                    FROM core.chart_of_accounts
                    WHERE company_id = $1 AND is_active = TRUE
                    ORDER BY account_number
                    """,
                    company_id,
                )
                result.extend(dict(r) for r in all_rows)

        return result

    # ===================================================================
    # ASSET MODELS (CRUD)
    # ===================================================================

    async def list_asset_models(
        self, company_id: UUID
    ) -> List[Dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.*,
                       a1.account_name AS account_asset_name,
                       a2.account_name AS account_depreciation_name,
                       a3.account_name AS account_expense_name
                FROM fixed_assets.asset_models m
                LEFT JOIN core.chart_of_accounts a1
                    ON a1.company_id = m.company_id AND a1.account_number = m.account_asset_number
                LEFT JOIN core.chart_of_accounts a2
                    ON a2.company_id = m.company_id AND a2.account_number = m.account_depreciation_number
                LEFT JOIN core.chart_of_accounts a3
                    ON a3.company_id = m.company_id AND a3.account_number = m.account_expense_number
                WHERE m.company_id = $1 AND m.is_active = TRUE
                ORDER BY m.name
                """,
                company_id,
            )
        return [dict(r) for r in rows]

    async def get_asset_model(
        self, company_id: UUID, model_id: UUID
    ) -> Optional[Dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT m.*,
                       a1.account_name AS account_asset_name,
                       a2.account_name AS account_depreciation_name,
                       a3.account_name AS account_expense_name
                FROM fixed_assets.asset_models m
                LEFT JOIN core.chart_of_accounts a1
                    ON a1.company_id = m.company_id AND a1.account_number = m.account_asset_number
                LEFT JOIN core.chart_of_accounts a2
                    ON a2.company_id = m.company_id AND a2.account_number = m.account_depreciation_number
                LEFT JOIN core.chart_of_accounts a3
                    ON a3.company_id = m.company_id AND a3.account_number = m.account_expense_number
                WHERE m.company_id = $1 AND m.id = $2
                """,
                company_id,
                model_id,
            )
        return dict(row) if row else None

    async def create_asset_model(
        self, company_id: UUID, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO fixed_assets.asset_models
                    (company_id, name, account_asset_number, account_depreciation_number,
                     account_expense_number, account_disposal_number,
                     method, method_number, method_period, prorata, auto_post_enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING *
                """,
                company_id,
                data["name"],
                data["account_asset_number"],
                data["account_depreciation_number"],
                data["account_expense_number"],
                data.get("account_disposal_number"),
                data.get("method", "linear"),
                data.get("method_number", 60),
                data.get("method_period", 1),
                data.get("prorata", True),
                data.get("auto_post_enabled", False),
            )
        logger.info("Asset model created: %s (%s)", data["name"], row["id"])
        return dict(row)

    async def update_asset_model(
        self, company_id: UUID, model_id: UUID, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        allowed = {
            "name", "account_asset_number", "account_depreciation_number",
            "account_expense_number", "account_disposal_number",
            "method", "method_number", "method_period", "prorata",
            "auto_post_enabled",
        }
        filtered = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not filtered:
            return {"success": False, "error": "No valid fields to update"}

        sets = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(filtered))
        params = [company_id, model_id] + list(filtered.values())

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE fixed_assets.asset_models
                SET {sets}
                WHERE company_id = $1 AND id = $2
                RETURNING *
                """,
                *params,
            )
        if not row:
            return {"success": False, "error": "Model not found"}
        logger.info("Asset model updated: %s", model_id)
        return {"success": True, **dict(row)}

    async def delete_asset_model(
        self, company_id: UUID, model_id: UUID
    ) -> Dict[str, Any]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Check no active assets use this model
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM fixed_assets.assets
                WHERE model_id = $1 AND state NOT IN ('disposed')
                """,
                model_id,
            )
            if count and count > 0:
                return {
                    "success": False,
                    "error": f"Cannot delete: {count} active asset(s) use this model",
                }
            row = await conn.fetchrow(
                """
                UPDATE fixed_assets.asset_models
                SET is_active = FALSE
                WHERE company_id = $1 AND id = $2
                RETURNING name
                """,
                company_id,
                model_id,
            )
        if not row:
            return {"success": False, "error": "Model not found"}
        logger.info("Asset model soft-deleted: %s (%s)", row["name"], model_id)
        return {"success": True, "deleted_model": row["name"]}

    # ===================================================================
    # ASSETS (CRUD)
    # ===================================================================

    async def list_assets(
        self,
        company_id: UUID,
        state: Optional[List[str]] = None,
        search: Optional[str] = None,
        model_id: Optional[UUID] = None,
        account_number: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """List assets with filters. Computes book_value inline."""
        conditions = ["a.company_id = $1"]
        params: list = [company_id]
        idx = 2

        if state:
            conditions.append(f"a.state = ANY(${idx}::text[])")
            params.append(state)
            idx += 1

        if search:
            conditions.append(f"(a.name ILIKE ${idx} OR a.reference ILIKE ${idx})")
            params.append(f"%{search}%")
            idx += 1

        if model_id:
            conditions.append(f"a.model_id = ${idx}")
            params.append(model_id)
            idx += 1

        if account_number:
            conditions.append(f"a.account_asset_number = ${idx}")
            params.append(account_number)
            idx += 1

        if date_from:
            conditions.append(f"a.acquisition_date >= ${idx}")
            params.append(date_from)
            idx += 1

        if date_to:
            conditions.append(f"a.acquisition_date <= ${idx}")
            params.append(date_to)
            idx += 1

        where = " AND ".join(conditions)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT a.*,
                       m.name AS model_name,
                       COALESCE(
                           a.original_value - a.salvage_value
                           - COALESCE((
                               SELECT SUM(dl.amount)
                               FROM fixed_assets.depreciation_lines dl
                               WHERE dl.asset_id = a.id AND dl.is_posted = TRUE
                           ), 0),
                           a.original_value
                       ) AS book_value,
                       COALESCE((
                           SELECT SUM(dl.amount)
                           FROM fixed_assets.depreciation_lines dl
                           WHERE dl.asset_id = a.id AND dl.is_posted = TRUE
                       ), 0) AS cumulated_depreciation,
                       COALESCE((
                           SELECT COUNT(*)
                           FROM fixed_assets.depreciation_lines dl
                           WHERE dl.asset_id = a.id AND dl.is_posted = FALSE
                       ), 0) AS pending_lines_count,
                       (
                           SELECT MIN(dl.depreciation_date)
                           FROM fixed_assets.depreciation_lines dl
                           WHERE dl.asset_id = a.id AND dl.is_posted = FALSE
                       ) AS next_unposted_date,
                       (a.source_invoice_ref IS NOT NULL) AS has_source_link
                FROM fixed_assets.assets a
                LEFT JOIN fixed_assets.asset_models m ON m.id = a.model_id
                WHERE {where}
                ORDER BY a.acquisition_date DESC, a.name
                """,
                *params,
            )
        return [dict(r) for r in rows]

    async def get_asset(
        self, company_id: UUID, asset_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Get single asset with full details + depreciation lines."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            asset = await conn.fetchrow(
                """
                SELECT a.*,
                       m.name AS model_name,
                       ca1.account_name AS account_asset_name,
                       ca2.account_name AS account_depreciation_name,
                       ca3.account_name AS account_expense_name
                FROM fixed_assets.assets a
                LEFT JOIN fixed_assets.asset_models m ON m.id = a.model_id
                LEFT JOIN core.chart_of_accounts ca1
                    ON ca1.company_id = a.company_id AND ca1.account_number = a.account_asset_number
                LEFT JOIN core.chart_of_accounts ca2
                    ON ca2.company_id = a.company_id AND ca2.account_number = a.account_depreciation_number
                LEFT JOIN core.chart_of_accounts ca3
                    ON ca3.company_id = a.company_id AND ca3.account_number = a.account_expense_number
                WHERE a.company_id = $1 AND a.id = $2
                """,
                company_id,
                asset_id,
            )
            if not asset:
                return None

            lines = await conn.fetch(
                """
                SELECT * FROM fixed_assets.depreciation_lines
                WHERE asset_id = $1
                ORDER BY line_number
                """,
                asset_id,
            )

        result = dict(asset)
        result["depreciation_lines"] = [dict(l) for l in lines]

        # Computed values
        posted_sum = sum((_dec(l["amount"]) for l in lines if l["is_posted"]), Decimal("0"))
        result["cumulated_depreciation"] = float(_round2(posted_sum))
        result["book_value"] = float(
            _round2(_dec(asset["original_value"]) - _dec(asset["salvage_value"]) - posted_sum)
        )
        return result

    async def create_asset(
        self, company_id: UUID, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new asset. If model_id provided, copy defaults from model."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # If model_id, load defaults
                model_defaults = {}
                if data.get("model_id"):
                    model = await conn.fetchrow(
                        "SELECT * FROM fixed_assets.asset_models WHERE id = $1 AND company_id = $2",
                        UUID(str(data["model_id"])),
                        company_id,
                    )
                    if model:
                        model_defaults = {
                            "account_asset_number": model["account_asset_number"],
                            "account_depreciation_number": model["account_depreciation_number"],
                            "account_expense_number": model["account_expense_number"],
                            "account_disposal_number": model["account_disposal_number"],
                            "method": model["method"],
                            "method_number": model["method_number"],
                            "method_period": model["method_period"],
                            "prorata": model["prorata"],
                        }

                # Merge: explicit data overrides model defaults
                merged = {**model_defaults, **{k: v for k, v in data.items() if v is not None}}

                acq_date = data.get("acquisition_date")
                if isinstance(acq_date, str):
                    acq_date = date.fromisoformat(acq_date[:10])

                first_depr = data.get("first_depreciation_date")
                if isinstance(first_depr, str):
                    first_depr = date.fromisoformat(first_depr[:10])

                row = await conn.fetchrow(
                    """
                    INSERT INTO fixed_assets.assets
                        (company_id, name, reference, model_id,
                         acquisition_date, original_value, salvage_value, currency,
                         account_asset_number, account_depreciation_number, account_expense_number,
                         account_disposal_number,
                         method, method_number, method_period, prorata, first_depreciation_date,
                         source_invoice_ref, source_document_id)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
                    RETURNING *
                    """,
                    company_id,
                    data["name"],
                    data.get("reference"),
                    UUID(str(data["model_id"])) if data.get("model_id") else None,
                    acq_date,
                    Decimal(str(data["original_value"])),
                    Decimal(str(data.get("salvage_value", 0))),
                    data.get("currency", "CHF"),
                    merged["account_asset_number"],
                    merged["account_depreciation_number"],
                    merged["account_expense_number"],
                    merged.get("account_disposal_number"),
                    merged.get("method", "linear"),
                    merged.get("method_number", 60),
                    merged.get("method_period", 1),
                    merged.get("prorata", True),
                    first_depr,
                    data.get("source_invoice_ref"),
                    data.get("source_document_id"),
                )

        logger.info("Asset created: %s (%s)", data["name"], row["id"])
        return dict(row)

    async def update_asset(
        self, company_id: UUID, asset_id: UUID, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a draft or running asset. If method changes, regenerate schedule."""
        allowed_draft = {
            "name", "reference", "model_id", "acquisition_date",
            "original_value", "salvage_value", "currency",
            "account_asset_number", "account_depreciation_number",
            "account_expense_number", "method", "method_number",
            "method_period", "prorata", "first_depreciation_date",
        }
        allowed_running = {"name", "reference"}

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            current = await conn.fetchrow(
                "SELECT state FROM fixed_assets.assets WHERE company_id = $1 AND id = $2",
                company_id, asset_id,
            )
            if not current:
                return {"success": False, "error": "Asset not found"}

            allowed = allowed_draft if current["state"] == "draft" else allowed_running
            filtered = {k: v for k, v in data.items() if k in allowed and v is not None}
            if not filtered:
                return {"success": False, "error": "No valid fields to update"}

            # Convert date strings
            for dk in ("acquisition_date", "first_depreciation_date"):
                if dk in filtered and isinstance(filtered[dk], str):
                    filtered[dk] = date.fromisoformat(filtered[dk][:10])
            for nk in ("original_value", "salvage_value"):
                if nk in filtered:
                    filtered[nk] = Decimal(str(filtered[nk]))
            if "model_id" in filtered and filtered["model_id"]:
                filtered["model_id"] = UUID(str(filtered["model_id"]))

            sets = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(filtered))
            params = [company_id, asset_id] + list(filtered.values())

            row = await conn.fetchrow(
                f"""
                UPDATE fixed_assets.assets
                SET {sets}
                WHERE company_id = $1 AND id = $2
                RETURNING *
                """,
                *params,
            )

        if not row:
            return {"success": False, "error": "Update failed"}
        return {"success": True, **dict(row)}

    async def confirm_asset(
        self, company_id: UUID, asset_id: UUID
    ) -> Dict[str, Any]:
        """Confirm asset (draft -> running) and generate depreciation schedule."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            asset = await conn.fetchrow(
                """
                SELECT * FROM fixed_assets.assets
                WHERE company_id = $1 AND id = $2 AND state = 'draft'
                """,
                company_id,
                asset_id,
            )
            if not asset:
                return {"success": False, "error": "Asset not found or not in draft state"}

            # Generate depreciation schedule
            lines = compute_depreciation_schedule(dict(asset))

            async with conn.transaction():
                # Update state
                await conn.execute(
                    "UPDATE fixed_assets.assets SET state = 'running' WHERE id = $1",
                    asset_id,
                )
                # Insert depreciation lines
                for line in lines:
                    await conn.execute(
                        """
                        INSERT INTO fixed_assets.depreciation_lines
                            (asset_id, company_id, line_number, depreciation_date,
                             amount, cumulated_amount, remaining_value)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        asset_id,
                        company_id,
                        line["line_number"],
                        line["depreciation_date"],
                        Decimal(str(line["amount"])),
                        Decimal(str(line["cumulated_amount"])),
                        Decimal(str(line["remaining_value"])),
                    )

        logger.info("Asset confirmed: %s (%d depreciation lines)", asset["name"], len(lines))
        return {"success": True, "lines_created": len(lines)}

    async def reset_to_draft(
        self, company_id: UUID, asset_id: UUID
    ) -> Dict[str, Any]:
        """Reset asset back to draft — only if NO depreciation lines are posted."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            asset = await conn.fetchrow(
                """
                SELECT id, name, state FROM fixed_assets.assets
                WHERE company_id = $1 AND id = $2 AND state = 'running'
                """,
                company_id,
                asset_id,
            )
            if not asset:
                return {"success": False, "error": "Asset not found or not in running state"}

            # Check for posted lines
            posted = await conn.fetchval(
                """
                SELECT COUNT(*) FROM fixed_assets.depreciation_lines
                WHERE asset_id = $1 AND is_posted = TRUE
                """,
                asset_id,
            )
            if posted and posted > 0:
                return {
                    "success": False,
                    "error": f"Cannot reset: {posted} depreciation line(s) already posted to ERP",
                }

            async with conn.transaction():
                # Delete all unposted depreciation lines
                await conn.execute(
                    "DELETE FROM fixed_assets.depreciation_lines WHERE asset_id = $1",
                    asset_id,
                )
                # Reset state to draft
                await conn.execute(
                    "UPDATE fixed_assets.assets SET state = 'draft' WHERE id = $1",
                    asset_id,
                )

        logger.info("Asset reset to draft: %s", asset["name"])
        return {"success": True}

    # ===================================================================
    # DEPRECIATION SCHEDULE
    # ===================================================================

    async def get_depreciation_schedule(
        self, company_id: UUID, asset_id: UUID
    ) -> List[Dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT dl.*, a.name AS asset_name, a.currency
                FROM fixed_assets.depreciation_lines dl
                JOIN fixed_assets.assets a ON a.id = dl.asset_id
                WHERE dl.asset_id = $1 AND a.company_id = $2
                ORDER BY dl.line_number
                """,
                asset_id,
                company_id,
            )
        return [dict(r) for r in rows]

    # ===================================================================
    # RUN DEPRECIATION (post entries up to a date)
    # ===================================================================

    async def run_depreciation(
        self, company_id: UUID, date_up_to: date, asset_id: UUID | None = None
    ) -> Dict[str, Any]:
        """Collect unposted depreciation lines up to date_up_to.

        IMPORTANT: This method does NOT write to accounting.gl_entries.
        Neon GL is a read-only ERP mirror. Actual posting goes through
        erp_provider.post_journal_entry() (see depreciation_cron.py).

        Args:
            asset_id: Optional — filter to a single asset.

        Returns the lines to post so the caller (cron) can push them to ERP.
        """
        pool = await self._get_pool()

        asset_filter = "AND dl.asset_id = $3" if asset_id else ""
        params: list = [company_id, date_up_to]
        if asset_id:
            params.append(asset_id)

        async with pool.acquire() as conn:
            lines = await conn.fetch(
                f"""
                SELECT dl.*, a.name AS asset_name, a.reference AS asset_reference,
                       a.account_expense_number, a.account_depreciation_number,
                       a.currency
                FROM fixed_assets.depreciation_lines dl
                JOIN fixed_assets.assets a ON a.id = dl.asset_id
                WHERE dl.company_id = $1
                  AND dl.is_posted = FALSE
                  AND dl.depreciation_date <= $2
                  AND a.state = 'running'
                  {asset_filter}
                ORDER BY dl.depreciation_date, a.name
                """,
                *params,
            )

            if not lines:
                return {"success": True, "posted_count": 0, "total_amount": 0, "lines_to_post": []}

        # Build structured list for ERP posting
        lines_to_post = []
        total_amount = Decimal("0")
        for line in lines:
            asset_ref = line["asset_reference"] or str(line["asset_id"])[:8]
            dep_date = line["depreciation_date"]
            entry_ref = f"AMOR-{asset_ref}-{dep_date.strftime('%Y%m')}"
            period_label = dep_date.strftime("%m/%Y")
            description = f"Amortissement {line['asset_name']} - {period_label}"

            total_amount += _dec(line["amount"])
            lines_to_post.append({
                "depreciation_line_id": str(line["id"]),
                "asset_id": str(line["asset_id"]),
                "asset_name": line["asset_name"],
                "entry_ref": entry_ref,
                "description": description,
                "depreciation_date": dep_date.isoformat(),
                "amount": float(line["amount"]),
                "currency": line["currency"],
                "account_expense_number": line["account_expense_number"],
                "account_depreciation_number": line["account_depreciation_number"],
            })

        logger.info(
            "Depreciation run: %d lines to post, total %s, up to %s",
            len(lines_to_post), _round2(total_amount), date_up_to,
        )
        return {
            "success": True,
            "posted_count": len(lines_to_post),
            "total_amount": float(_round2(total_amount)),
            "lines_to_post": lines_to_post,
        }

    async def mark_depreciation_posted(
        self,
        company_id: UUID,
        depreciation_line_id: UUID,
        gl_entry_ref: str,
    ) -> None:
        """Mark a single depreciation line as posted after successful ERP posting."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE fixed_assets.depreciation_lines
                SET is_posted = TRUE, posted_date = $2, gl_entry_ref = $3
                WHERE id = $1
                """,
                depreciation_line_id,
                datetime.now(timezone.utc),
                gl_entry_ref,
            )

    async def get_lines_for_reversal(
        self,
        company_id: UUID,
        line_ids: list[UUID] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        asset_id: UUID | None = None,
    ) -> list[dict]:
        """Get posted, non-reversed depreciation lines eligible for reversal."""
        pool = await self._get_pool()
        conditions = [
            "dl.company_id = $1",
            "dl.is_posted = TRUE",
            "dl.is_reversed = FALSE",
            "a.state IN ('running', 'close')",
        ]
        params: list = [company_id]
        idx = 2

        if line_ids:
            conditions.append(f"dl.id = ANY(${idx})")
            params.append(line_ids)
            idx += 1
        if date_from:
            conditions.append(f"dl.depreciation_date >= ${idx}")
            params.append(date_from)
            idx += 1
        if date_to:
            conditions.append(f"dl.depreciation_date <= ${idx}")
            params.append(date_to)
            idx += 1
        if asset_id:
            conditions.append(f"dl.asset_id = ${idx}")
            params.append(asset_id)
            idx += 1

        where = " AND ".join(conditions)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT dl.*, a.name AS asset_name, a.reference AS asset_reference,
                       a.account_expense_number, a.account_depreciation_number,
                       a.currency
                FROM fixed_assets.depreciation_lines dl
                JOIN fixed_assets.assets a ON a.id = dl.asset_id
                WHERE {where}
                ORDER BY dl.depreciation_date, a.name
                """,
                *params,
            )
        return [dict(r) for r in rows]

    async def mark_depreciation_reversed(
        self,
        depreciation_line_id: UUID,
        reversal_gl_entry_ref: str,
        reversal_entry_ref: str,
    ) -> None:
        """Mark a depreciation line as reversed."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE fixed_assets.depreciation_lines
                SET is_reversed = TRUE,
                    reversed_at = $2,
                    reversal_gl_entry_ref = $3,
                    reversal_entry_ref = $4
                WHERE id = $1
                """,
                depreciation_line_id,
                datetime.now(timezone.utc),
                reversal_gl_entry_ref,
                reversal_entry_ref,
            )

    async def close_fully_depreciated(self, company_id: UUID) -> int:
        """Close assets that have no unposted lines remaining. Returns count."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE fixed_assets.assets a
                SET state = 'close'
                WHERE a.company_id = $1
                  AND a.state = 'running'
                  AND NOT EXISTS (
                      SELECT 1 FROM fixed_assets.depreciation_lines dl
                      WHERE dl.asset_id = a.id AND dl.is_posted = FALSE
                  )
                """,
                company_id,
            )
            # result is like "UPDATE N"
            count = int(result.split()[-1]) if result else 0
            if count:
                logger.info("Closed %d fully depreciated assets for company %s", count, company_id)
            return count

    # ===================================================================
    # DISPOSE ASSET
    # ===================================================================

    async def dispose_asset(
        self,
        company_id: UUID,
        asset_id: UUID,
        disposal_type: str,
        disposal_date: date,
        disposal_value: Decimal = Decimal("0"),
    ) -> Dict[str, Any]:
        """Dispose of an asset (sale or scrap).

        IMPORTANT: Does NOT write to accounting.gl_entries (read-only ERP mirror).
        Returns the journal entry lines so the caller can post via ERP provider.
        """
        pool = await self._get_pool()

        async with pool.acquire() as conn:
            asset = await conn.fetchrow(
                "SELECT * FROM fixed_assets.assets WHERE company_id = $1 AND id = $2 AND state IN ('running', 'close')",
                company_id, asset_id,
            )
            if not asset:
                return {"success": False, "error": "Asset not found or not in running/close state"}

            # Compute cumulated depreciation (posted lines)
            cum_dep = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM fixed_assets.depreciation_lines WHERE asset_id = $1 AND is_posted = TRUE",
                asset_id,
            )
            cum_dep = _dec(cum_dep)
            original = _dec(asset["original_value"])
            vnc = original - cum_dep  # Net book value

            ref_base = f"DISP-{asset['reference'] or str(asset_id)[:8]}-{disposal_date.strftime('%Y%m')}"
            description_base = f"Cession {asset['name']}"

            # Build journal entry lines for ERP posting
            journal_lines = [
                # Debit accumulated depreciation (remove it)
                {
                    "account_number": asset["account_depreciation_number"],
                    "description": description_base,
                    "debit": float(_round2(cum_dep)),
                    "credit": 0,
                },
                # Credit asset account (remove asset)
                {
                    "account_number": asset["account_asset_number"],
                    "description": description_base,
                    "debit": 0,
                    "credit": float(_round2(original)),
                },
            ]

            gain_loss = _dec(disposal_value) - vnc
            # Use disposal account if set, fallback to expense account
            disposal_account = asset["account_disposal_number"] or asset["account_expense_number"]
            if gain_loss != Decimal("0"):
                is_gain = gain_loss > 0
                journal_lines.append({
                    "account_number": disposal_account,
                    "description": f"{'Plus' if is_gain else 'Moins'}-value cession {asset['name']}",
                    "debit": 0 if is_gain else float(_round2(abs(gain_loss))),
                    "credit": float(_round2(abs(gain_loss))) if is_gain else 0,
                })

            async with conn.transaction():
                # Delete unposted depreciation lines
                await conn.execute(
                    "DELETE FROM fixed_assets.depreciation_lines WHERE asset_id = $1 AND is_posted = FALSE",
                    asset_id,
                )

                # Update asset state
                await conn.execute(
                    """
                    UPDATE fixed_assets.assets
                    SET state = 'disposed', disposal_date = $2,
                        disposal_value = $3, disposal_type = $4
                    WHERE id = $1
                    """,
                    asset_id, disposal_date, disposal_value, disposal_type,
                )

        logger.info("Asset disposed: %s (type=%s, VNC=%s, sale=%s)", asset["name"], disposal_type, vnc, disposal_value)
        return {
            "success": True,
            "asset_name": asset["name"],
            "net_book_value": float(_round2(vnc)),
            "gain_loss": float(_round2(gain_loss)),
            "gl_ref": ref_base,
            "journal_entry": {
                "journal_code": "OD",
                "entry_date": disposal_date.isoformat(),
                "document_ref": ref_base,
                "description": description_base,
                "currency": asset["currency"],
                "lines": journal_lines,
            },
        }

    # ===================================================================
    # ASSET REPORT (aggregated by account)
    # ===================================================================

    async def get_asset_report(
        self, company_id: UUID, period_date: date
    ) -> Dict[str, Any]:
        """Aggregated depreciation report by asset account for a given month."""
        period_start = date(period_date.year, period_date.month, 1)
        period_end = _end_of_month(period_date)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH asset_data AS (
                    SELECT
                        a.account_asset_number,
                        ca.account_name AS account_asset_name,
                        a.original_value,
                        a.state,
                        a.acquisition_date,
                        a.disposal_date,
                        -- Depreciation posted before period start
                        COALESCE((
                            SELECT SUM(dl.amount)
                            FROM fixed_assets.depreciation_lines dl
                            WHERE dl.asset_id = a.id AND dl.is_posted = TRUE
                              AND dl.depreciation_date < $2
                        ), 0) AS dep_before,
                        -- Depreciation posted during period
                        COALESCE((
                            SELECT SUM(dl.amount)
                            FROM fixed_assets.depreciation_lines dl
                            WHERE dl.asset_id = a.id AND dl.is_posted = TRUE
                              AND dl.depreciation_date BETWEEN $2 AND $3
                        ), 0) AS dep_in_period
                    FROM fixed_assets.assets a
                    LEFT JOIN core.chart_of_accounts ca
                        ON ca.company_id = a.company_id AND ca.account_number = a.account_asset_number
                    WHERE a.company_id = $1 AND a.state != 'draft'
                )
                SELECT
                    account_asset_number,
                    MAX(account_asset_name) AS account_asset_name,
                    -- Immobilisations
                    SUM(CASE WHEN acquisition_date < $2 THEN original_value ELSE 0 END) AS immo_start,
                    SUM(CASE WHEN acquisition_date BETWEEN $2 AND $3 THEN original_value ELSE 0 END) AS immo_additions,
                    SUM(CASE WHEN disposal_date BETWEEN $2 AND $3 THEN original_value ELSE 0 END) AS immo_disposals,
                    SUM(CASE WHEN state != 'disposed' OR disposal_date > $3 THEN original_value ELSE 0 END) AS immo_end,
                    -- Amortissement
                    SUM(dep_before) AS dep_start,
                    SUM(dep_in_period) AS dep_in_period,
                    SUM(dep_before + dep_in_period) AS dep_end,
                    -- VNC
                    SUM(
                        CASE WHEN state != 'disposed' OR disposal_date > $3
                        THEN original_value - dep_before - dep_in_period
                        ELSE 0 END
                    ) AS net_book_value,
                    COUNT(*) AS asset_count
                FROM asset_data
                GROUP BY account_asset_number
                ORDER BY account_asset_number
                """,
                company_id,
                period_start,
                period_end,
            )

        accounts = [dict(r) for r in rows]

        # Totals
        totals = {}
        for key in ("immo_start", "immo_additions", "immo_disposals", "immo_end",
                     "dep_start", "dep_in_period", "dep_end", "net_book_value"):
            totals[key] = float(sum(_dec(a.get(key, 0)) for a in accounts))

        return {
            "success": True,
            "period": period_date.isoformat()[:7],
            "accounts": accounts,
            "totals": totals,
        }


# ===================================================================
# DEPRECIATION ENGINE (pure function, no DB)
# ===================================================================

def compute_depreciation_schedule(asset: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Compute full depreciation schedule for an asset.

    Supports:
    - Linear (straight-line) with prorata temporis
    - Degressive (declining balance) with switch to linear

    Returns list of {line_number, depreciation_date, amount, cumulated_amount, remaining_value}
    """
    original = _dec(asset["original_value"])
    salvage = _dec(asset.get("salvage_value", 0))
    base = original - salvage  # Depreciable base

    if base <= 0:
        return []

    method = asset.get("method", "linear")
    method_number = int(asset.get("method_number", 60))
    method_period = int(asset.get("method_period", 1))
    prorata = asset.get("prorata", True)

    acq_date = asset["acquisition_date"]
    if isinstance(acq_date, str):
        acq_date = date.fromisoformat(acq_date[:10])

    first_depr = asset.get("first_depreciation_date")
    if first_depr and isinstance(first_depr, str):
        first_depr = date.fromisoformat(first_depr[:10])
    if not first_depr:
        first_depr = acq_date

    # Number of periods
    num_periods = method_number  # total periods
    period_months = method_period

    if method == "linear":
        return _compute_linear(base, num_periods, period_months, first_depr, prorata)
    elif method == "degressive":
        return _compute_degressive(base, num_periods, period_months, first_depr, prorata)
    else:
        raise ValueError(f"Unknown depreciation method: {method}")


def _compute_linear(
    base: Decimal,
    num_periods: int,
    period_months: int,
    first_depr_date: date,
    prorata: bool,
) -> List[Dict[str, Any]]:
    """Straight-line depreciation schedule."""
    lines = []
    period_amount = _round2(base / num_periods)
    cumulated = Decimal("0")
    remaining = base

    for i in range(1, num_periods + 1):
        dep_date = _end_of_month(_add_months(first_depr_date, (i - 1) * period_months))

        if i == 1 and prorata and period_months == 1:
            # Prorata: proportion of first month
            _, days_in_month = calendar.monthrange(first_depr_date.year, first_depr_date.month)
            days_remaining = days_in_month - first_depr_date.day + 1
            amount = _round2(period_amount * Decimal(str(days_remaining)) / Decimal(str(days_in_month)))
        elif i == num_periods:
            # Last period: take the remainder to ensure exact total
            amount = _round2(remaining)
        else:
            amount = period_amount

        # Clamp: don't exceed remaining
        if amount > remaining:
            amount = _round2(remaining)

        cumulated += amount
        remaining = _round2(base - cumulated)

        lines.append({
            "line_number": i,
            "depreciation_date": dep_date,
            "amount": float(amount),
            "cumulated_amount": float(cumulated),
            "remaining_value": float(remaining),
        })

        if remaining <= 0:
            break

    return lines


def _compute_degressive(
    base: Decimal,
    num_periods: int,
    period_months: int,
    first_depr_date: date,
    prorata: bool,
) -> List[Dict[str, Any]]:
    """Declining balance depreciation schedule with switch to linear."""
    # Coefficient based on duration
    years = num_periods * period_months / 12
    if years <= 4:
        coeff = Decimal("1.5")
    elif years <= 6:
        coeff = Decimal("2.0")
    else:
        coeff = Decimal("2.5")

    linear_rate = Decimal("1") / Decimal(str(num_periods))
    degressive_rate = linear_rate * coeff

    lines = []
    remaining = base
    cumulated = Decimal("0")

    for i in range(1, num_periods + 1):
        dep_date = _end_of_month(_add_months(first_depr_date, (i - 1) * period_months))
        periods_left = num_periods - i + 1

        # Degressive amount
        deg_amount = _round2(remaining * degressive_rate)
        # Linear amount on remaining
        lin_amount = _round2(remaining / Decimal(str(periods_left)))

        # Switch to linear when it becomes more advantageous
        amount = max(deg_amount, lin_amount)

        if i == 1 and prorata and period_months == 1:
            _, days_in_month = calendar.monthrange(first_depr_date.year, first_depr_date.month)
            days_remaining = days_in_month - first_depr_date.day + 1
            amount = _round2(amount * Decimal(str(days_remaining)) / Decimal(str(days_in_month)))

        # Clamp
        if amount > remaining:
            amount = _round2(remaining)

        cumulated += amount
        remaining = _round2(base - cumulated)

        lines.append({
            "line_number": i,
            "depreciation_date": dep_date,
            "amount": float(amount),
            "cumulated_amount": float(cumulated),
            "remaining_value": float(remaining),
        })

        if remaining <= 0:
            break

    return lines


# ===================================================================
# SINGLETON ACCESSOR
# ===================================================================

_manager_instance: Optional[NeonFixedAssetManager] = None
_manager_lock = threading.Lock()


def get_fixed_asset_manager() -> NeonFixedAssetManager:
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = NeonFixedAssetManager()
    return _manager_instance
