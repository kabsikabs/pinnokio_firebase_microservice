"""
Depreciation Cron — Posts unposted depreciation entries to ERP.

ARCHITECTURE:
    1. Query Neon for companies with unposted depreciation lines due today
    2. For each company:
       a. Collect unposted lines via NeonFixedAssetManager.run_depreciation()
       b. Build KLK journal entries (one per depreciation line)
       c. Resolve ERP IDs via resolve_erp_ids()
       d. Post to ERP via erp_provider.post_journal_entry()
       e. On success: mark line posted + store gl_entry_ref (ERP ref)
       f. Close fully depreciated assets

CRITICAL: Neon accounting.gl_entries is a READ-ONLY ERP mirror.
All journal entries MUST go through erp_provider → ERP XMLRPC.
The existing GL sync flow brings data back to Neon automatically.

Integrated into CronScheduler as a builtin task (runs daily).
Uses DistributedLock to prevent multi-instance double-posting.
"""

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

logger = logging.getLogger("depreciation_cron")


async def run_depreciation_cron(target_date: date | None = None) -> Dict[str, Any]:
    """
    Main entry point: post all due depreciation entries to ERP.

    Args:
        target_date: Post lines up to this date (default: today).

    Returns:
        Summary dict with per-company results.
    """
    if target_date is None:
        target_date = date.today()

    logger.info("[DEPR_CRON] Starting depreciation posting up to %s", target_date)

    # 1. Find companies with unposted depreciation lines due
    companies = await _get_companies_with_due_lines(target_date)

    if not companies:
        logger.info("[DEPR_CRON] No companies with due depreciation lines")
        return {"success": True, "companies_processed": 0, "total_posted": 0}

    logger.info("[DEPR_CRON] %d companies with due lines", len(companies))

    results = []
    total_posted = 0
    total_errors = 0

    for company in companies:
        company_id = company["company_id"]
        mandate_path = company["firebase_mandate_path"]

        try:
            result = await _process_company_depreciation(
                company_id, mandate_path, target_date
            )
            results.append(result)
            total_posted += result.get("posted", 0)
            total_errors += result.get("errors", 0)
        except Exception as e:
            logger.error(
                "[DEPR_CRON] Error processing company %s: %s",
                company_id, e, exc_info=True,
            )
            results.append({
                "company_id": str(company_id),
                "error": str(e),
                "posted": 0,
                "errors": 1,
            })
            total_errors += 1

    logger.info(
        "[DEPR_CRON] Done: %d companies, %d posted, %d errors",
        len(companies), total_posted, total_errors,
    )
    return {
        "success": total_errors == 0,
        "companies_processed": len(companies),
        "total_posted": total_posted,
        "total_errors": total_errors,
        "details": results,
    }


async def _get_companies_with_due_lines(target_date: date) -> List[Dict[str, Any]]:
    """Query Neon for companies that have unposted depreciation lines due."""
    from .tools.neon_accounting_manager import get_neon_accounting_manager

    mgr = get_neon_accounting_manager()
    pool = await mgr.get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT c.id AS company_id, c.firebase_mandate_path
            FROM fixed_assets.depreciation_lines dl
            JOIN fixed_assets.assets a ON a.id = dl.asset_id
            JOIN fixed_assets.asset_models m ON m.id = a.model_id
            JOIN core.companies c ON c.id = dl.company_id
            WHERE dl.is_posted = FALSE
              AND dl.depreciation_date <= $1
              AND a.state = 'running'
              AND m.auto_post_enabled = TRUE
              AND c.firebase_mandate_path IS NOT NULL
            """,
            target_date,
        )
    return [dict(r) for r in rows]


async def _process_company_depreciation(
    company_id: UUID,
    mandate_path: str,
    target_date: date,
) -> Dict[str, Any]:
    """Process all due depreciation lines for one company."""
    from .tools.neon_fixed_asset_manager import get_fixed_asset_manager

    fa_mgr = get_fixed_asset_manager()

    # 1. Collect unposted lines
    run_result = await fa_mgr.run_depreciation(company_id, target_date)

    lines_to_post = run_result.get("lines_to_post", [])
    if not lines_to_post:
        return {
            "company_id": str(company_id),
            "posted": 0,
            "errors": 0,
            "message": "No lines to post",
        }

    logger.info(
        "[DEPR_CRON] Company %s: %d lines to post",
        company_id, len(lines_to_post),
    )

    # 2. Get centralized poster
    from .erp.journal_entry_poster import get_journal_entry_poster

    poster = get_journal_entry_poster()

    # 3. Post each line to ERP
    posted = 0
    errors = 0
    entries = []

    for line_info in lines_to_post:
        try:
            erp_ref = await _post_single_depreciation_to_erp(
                poster, fa_mgr, company_id, mandate_path, line_info
            )
            posted += 1
            entries.append({
                "asset_name": line_info["asset_name"],
                "date": line_info["depreciation_date"],
                "amount": line_info["amount"],
                "erp_ref": erp_ref,
            })
        except Exception as e:
            errors += 1
            logger.error(
                "[DEPR_CRON] Failed to post line %s for asset %s: %s",
                line_info["depreciation_line_id"],
                line_info["asset_name"],
                e,
            )

    # 4. Trigger GL sync once for the whole batch (not per line)
    if posted > 0:
        from .erp.journal_entry_poster import _extract_uid, _extract_collection_id

        await poster._trigger_gl_sync(
            _extract_uid(mandate_path) or "",
            _extract_collection_id(mandate_path) or "",
            mandate_path,
            "depreciation_cron",
        )

    # 5. Close fully depreciated assets
    closed = await fa_mgr.close_fully_depreciated(company_id)

    logger.info(
        "[DEPR_CRON] Company %s: %d posted, %d errors, %d assets closed",
        company_id, posted, errors, closed,
    )
    return {
        "company_id": str(company_id),
        "posted": posted,
        "errors": errors,
        "assets_closed": closed,
        "entries": entries,
    }


async def _post_single_depreciation_to_erp(
    poster,
    fa_mgr,
    company_id: UUID,
    mandate_path: str,
    line_info: Dict[str, Any],
) -> str:
    """Build KLK entry, post via JournalEntryPoster, mark as posted.

    Returns the ERP entry name/ref on success.
    """
    # Build KLK-format journal entry (2 lines: debit expense, credit depreciation)
    entry = {
        "journal_code": "OD",
        "entry_date": line_info["depreciation_date"],
        "document_ref": line_info["entry_ref"],
        "description": line_info["description"],
        "currency": line_info["currency"],
        "lines": [
            {
                "account_number": line_info["account_expense_number"],
                "description": line_info["description"],
                "debit": line_info["amount"],
                "credit": 0,
            },
            {
                "account_number": line_info["account_depreciation_number"],
                "description": line_info["description"],
                "debit": 0,
                "credit": line_info["amount"],
            },
        ],
    }

    # Post via centralized poster (resolve + post + gl_sync)
    result = await poster.post(
        entry=entry,
        mandate_path=mandate_path,
        trigger_gl_sync=False,  # GL sync once at end, not per line
        source="depreciation_cron",
    )

    if not result.get("success"):
        raise RuntimeError(
            f"ERP post failed for {line_info['entry_ref']}: {result.get('error')}"
        )

    erp_entry_name = result.get("erp_entry_name", str(result.get("erp_entry_id", "")))

    # Mark depreciation line as posted with ERP reference
    await fa_mgr.mark_depreciation_posted(
        company_id,
        UUID(line_info["depreciation_line_id"]),
        erp_entry_name,
    )

    return erp_entry_name


