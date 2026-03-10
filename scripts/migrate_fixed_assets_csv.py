#!/usr/bin/env python3
"""
Migration script: Import fixed assets from CSV into Neon PostgreSQL.

Target company:
  - company_id (Neon): cccd0781-26d7-47a0-97c8-e192ac9ec256
  - mandate_path: /clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/kXAQYwMgsMrV60jeVcuz/mandates/BIr6edJxlNeKUBZxNhu4
  - collection_id: AAAABzwjXro

Steps:
  1. Verify company exists in core.companies
  2. Verify all required accounts exist in core.chart_of_accounts with correct account_function
  3. Insert 6 asset models into fixed_assets.asset_models
  4. Insert 80 assets into fixed_assets.assets (state=running or close)
  5. For running assets: generate depreciation lines (past = posted, future = unposted)

Usage:
  NEON_DATABASE_URL="postgresql://..." python scripts/migrate_fixed_assets_csv.py [--dry-run]
"""

import asyncio
import csv
import logging
import os
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import UUID

try:
    import asyncpg
except ImportError:
    print("ERROR: asyncpg required. Install with: pip install asyncpg")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================
COMPANY_ID = UUID("7d0f14d3-6498-44ad-bd6f-0b9810d40837")

CSV_DIR = Path(__file__).resolve().parent.parent.parent / "pinnokio_agentic_worker" / "plans" / "Fixe_asset_implementation.md"
MODELS_CSV = CSV_DIR / "model_immobilisation.csv"
ASSETS_CSV = CSV_DIR / "Comptabilisation des immobilisationsproduits (account.asset).csv"

# Manually parsed models (CSV has malformed quoting with commas in account names)
MODELS_DATA = [
    {
        "name": "Informatique",
        "account_asset_number": "1520",
        "account_depreciation_number": "1529",
        "account_expense_number": "6821",  # resolved from assets CSV
        "method": "linear",
        "method_number": 60,   # 60 months
        "method_period": 1,    # monthly
    },
    {
        "name": "Machinery",
        "account_asset_number": "1500",
        "account_depreciation_number": "1509",
        "account_expense_number": "6800",
        "method": "linear",
        "method_number": 60,
        "method_period": 1,
    },
    {
        "name": "Office Equipment (including ICT)",
        "account_asset_number": "1510",
        "account_depreciation_number": "1519",
        "account_expense_number": "6821",  # most common (71/74)
        "method": "linear",
        "method_number": 96,
        "method_period": 1,
    },
    {
        "name": "Vehicles",
        "account_asset_number": "1530",
        "account_depreciation_number": "1539",
        "account_expense_number": "6800",
        "method": "linear",
        "method_number": 60,
        "method_period": 1,
    },
    {
        "name": "Equipment",
        "account_asset_number": "1510",
        "account_depreciation_number": "1519",
        "account_expense_number": "6821",
        "method": "linear",
        "method_number": 96,
        "method_period": 1,
    },
    {
        "name": "Informatique 1521",
        "account_asset_number": "1521",
        "account_depreciation_number": "1529",
        "account_expense_number": "6821",  # same family as 1520
        "method": "linear",
        "method_number": 36,   # 3 years × 12
        "method_period": 12,   # annual
    },
]

# Status mapping
STATUS_MAP = {
    "En cours": "running",
    "Fermé": "close",
}

# Method mapping
METHOD_MAP = {
    "Linéaire": "linear",
    "Linéaire ": "linear",
    "Lineaire": "linear",
    "Dégressive": "degressive",
    "Degressive": "degressive",
}


def extract_account_number(raw: str) -> str:
    """Extract account number from 'XXXX Account name' format."""
    return raw.strip().split(" ")[0]


def parse_assets_csv() -> list[dict]:
    """Parse the assets CSV file."""
    assets = []
    with open(ASSETS_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["Nom de l'immobilisation"].strip()
            if not name:
                continue

            original_value = row["Valeur d'origine"].strip()
            if not original_value:
                logger.warning("Skipping asset '%s': no original_value", name)
                continue

            book_value_raw = row["Valeur amortissable"].strip()
            status_raw = row["Statut"].strip()
            state = STATUS_MAP.get(status_raw, "draft")

            # For closed assets, book_value is empty → 0
            if book_value_raw:
                book_value = Decimal(book_value_raw)
            else:
                book_value = Decimal("0")

            assets.append({
                "name": name,
                "account_asset_number": extract_account_number(row["Compte d'immobilisations"]),
                "account_depreciation_number": extract_account_number(row["Compte de dépréciation"]),
                "account_expense_number": extract_account_number(row["Compte de charges"]),
                "acquisition_date": date.fromisoformat(row["Date d'acquisition"].strip()),
                "currency": row["Devise"].strip() or "CHF",
                "method": METHOD_MAP.get(row["Mode"].strip(), "linear"),
                "original_value": Decimal(original_value),
                "book_value": book_value,
                "state": state,
            })

    return assets


def _add_months(base: date, months: int) -> date:
    """Add N months to a date (day=1 for monthly periods)."""
    total = base.month + months
    year = base.year + (total - 1) // 12
    month = ((total - 1) % 12) + 1
    return date(year, month, base.day if base.day <= 28 else min(base.day, 28))


def compute_depreciation_schedule(
    original_value: Decimal,
    salvage_value: Decimal,
    method_number: int,
    method_period: int,
    acquisition_date: date,
    prorata: bool = True,
    book_value_csv: Decimal = Decimal("0"),
    is_closed: bool = False,
) -> list[dict]:
    """
    Compute the full depreciation schedule (linear method).

    For migration from CSV:
    - book_value_csv: the remaining book value from CSV export (= original - cumulated_depreciation)
    - is_closed: if True, all lines are marked as posted
    - For running assets: lines are posted based on cumulated <= (original - book_value_csv)

    Returns list of {line_number, depreciation_date, amount, cumulated_amount, remaining_value, is_posted}.
    """
    depreciable = original_value - salvage_value
    if depreciable <= 0 or method_number <= 0:
        return []

    period_amount = (depreciable / Decimal(method_number)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ─── First depreciation date ───
    if method_period == 1:
        # Monthly: first day of next month after acquisition
        if acquisition_date.day == 1:
            first_date = acquisition_date
        else:
            first_date = _add_months(date(acquisition_date.year, acquisition_date.month, 1), 1)
    elif method_period == 12:
        first_date = date(acquisition_date.year, 12, 31)
    elif method_period == 3:
        q_month = ((acquisition_date.month - 1) // 3 + 1) * 3
        if q_month > 12:
            first_date = date(acquisition_date.year + 1, 3, 31)
        else:
            last_day = 31 if q_month in (3, 12) else 30
            first_date = date(acquisition_date.year, q_month, last_day)
    elif method_period == 6:
        if acquisition_date.month <= 6:
            first_date = date(acquisition_date.year, 6, 30)
        else:
            first_date = date(acquisition_date.year, 12, 31)
    else:
        first_date = _add_months(date(acquisition_date.year, acquisition_date.month, 1), 1)

    # ─── Prorata: first period may be partial ───
    first_amount = period_amount
    if prorata and method_period == 1:
        days_in_month = 30
        days_used = days_in_month - acquisition_date.day + 1
        if days_used < days_in_month:
            first_amount = (period_amount * Decimal(days_used) / Decimal(days_in_month)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

    # ─── Target cumulated depreciation from CSV ───
    # This is what the source ERP had actually depreciated
    target_cumulated = original_value - book_value_csv

    # ─── Generate lines ───
    lines = []
    cumulated = Decimal("0")

    for i in range(1, method_number + 2):  # +2 for prorata overflow
        if method_period == 1:
            dep_date = first_date if i == 1 else _add_months(first_date, i - 1)
        elif method_period == 12:
            dep_date = date(first_date.year + (i - 1), first_date.month, first_date.day)
        elif method_period == 3:
            dep_date = _add_months(first_date, (i - 1) * 3)
        elif method_period == 6:
            dep_date = _add_months(first_date, (i - 1) * 6)
        else:
            break

        amt = first_amount if i == 1 else period_amount

        # Last line: adjust to not exceed depreciable
        if cumulated + amt >= depreciable:
            amt = depreciable - cumulated
            if amt <= 0:
                break

        cumulated += amt
        remaining = original_value - cumulated

        # Posted: closed → all posted; running → posted if cumulated <= what ERP had depreciated
        if is_closed:
            is_posted = True
        else:
            is_posted = cumulated <= target_cumulated + Decimal("0.01")  # small tolerance for rounding

        lines.append({
            "line_number": i,
            "depreciation_date": dep_date,
            "amount": amt,
            "cumulated_amount": cumulated,
            "remaining_value": remaining,
            "is_posted": is_posted,
        })

        if remaining <= salvage_value:
            break

    return lines


async def run_migration(dry_run: bool = False):
    url = os.getenv("NEON_DATABASE_URL")
    if not url:
        logger.error("NEON_DATABASE_URL environment variable not set")
        sys.exit(1)

    conn = await asyncpg.connect(url)
    logger.info("Connected to Neon PostgreSQL")

    try:
        # ─── Step 1: Verify company exists ───
        company = await conn.fetchrow(
            "SELECT id, name FROM core.companies WHERE id = $1", COMPANY_ID
        )
        if not company:
            logger.error("Company %s NOT FOUND in core.companies", COMPANY_ID)
            sys.exit(1)
        logger.info("Company found: %s (%s)", company["name"], COMPANY_ID)

        # ─── Step 2: Verify accounts exist in COA ───
        required_accounts = set()
        for m in MODELS_DATA:
            required_accounts.add(m["account_asset_number"])
            required_accounts.add(m["account_depreciation_number"])
            required_accounts.add(m["account_expense_number"])

        # Also from assets CSV
        assets_data = parse_assets_csv()
        for a in assets_data:
            required_accounts.add(a["account_asset_number"])
            required_accounts.add(a["account_depreciation_number"])
            required_accounts.add(a["account_expense_number"])

        logger.info("Checking %d unique accounts in COA...", len(required_accounts))

        existing_accounts = await conn.fetch(
            "SELECT account_number, account_name, account_function FROM core.chart_of_accounts WHERE company_id = $1 AND account_number = ANY($2)",
            COMPANY_ID,
            list(required_accounts),
        )
        existing_map = {r["account_number"]: r for r in existing_accounts}

        missing = required_accounts - set(existing_map.keys())
        if missing:
            logger.error("MISSING accounts in COA: %s", sorted(missing))
            logger.error("These accounts must exist in core.chart_of_accounts before migration.")
            # Show what we have
            for acc_num, acc in sorted(existing_map.items()):
                logger.info("  OK: %s - %s (function=%s)", acc_num, acc["account_name"], acc["account_function"])
            sys.exit(1)

        # Verify account_function mapping
        expected_functions = {
            "asset_fixed": [],
            "cumulated_depreciation": [],
            "expense_depreciation": [],
        }
        for acc_num, acc in existing_map.items():
            func = acc["account_function"]
            if acc_num.startswith("1") and acc_num[-1] != "9":
                if func != "asset_fixed":
                    logger.warning("Account %s has function '%s', expected 'asset_fixed'", acc_num, func)
            elif acc_num.startswith("1") and acc_num[-1] == "9":
                if func != "cumulated_depreciation":
                    logger.warning("Account %s has function '%s', expected 'cumulated_depreciation'", acc_num, func)
            elif acc_num.startswith("6"):
                if func != "expense_depreciation":
                    logger.warning("Account %s has function '%s', expected 'expense_depreciation'", acc_num, func)

        for acc_num, acc in sorted(existing_map.items()):
            logger.info("  COA: %s - %s (function=%s)", acc_num, acc["account_name"], acc["account_function"])

        # ─── Step 3: Check for existing data ───
        existing_models = await conn.fetchval(
            "SELECT COUNT(*) FROM fixed_assets.asset_models WHERE company_id = $1",
            COMPANY_ID,
        )
        existing_assets = await conn.fetchval(
            "SELECT COUNT(*) FROM fixed_assets.assets WHERE company_id = $1",
            COMPANY_ID,
        )
        if existing_models > 0 or existing_assets > 0:
            logger.warning(
                "Company already has %d models and %d assets. Migration will ADD to existing data.",
                existing_models, existing_assets,
            )

        if dry_run:
            logger.info("=== DRY RUN MODE — no data will be written ===")

        # ─── Step 4: Insert asset models ───
        logger.info("\n--- Inserting %d asset models ---", len(MODELS_DATA))
        model_id_map = {}  # (asset_acc, dep_acc) -> model_id (first match)

        for m in MODELS_DATA:
            logger.info("  Model: %s (asset=%s, dep=%s, exp=%s, periods=%d, period=%d)",
                        m["name"], m["account_asset_number"], m["account_depreciation_number"],
                        m["account_expense_number"], m["method_number"], m["method_period"])

            if not dry_run:
                row = await conn.fetchrow(
                    """
                    INSERT INTO fixed_assets.asset_models
                        (company_id, name, account_asset_number, account_depreciation_number,
                         account_expense_number, method, method_number, method_period, prorata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (company_id, name) DO UPDATE SET
                        account_asset_number = EXCLUDED.account_asset_number,
                        account_depreciation_number = EXCLUDED.account_depreciation_number,
                        account_expense_number = EXCLUDED.account_expense_number,
                        method = EXCLUDED.method,
                        method_number = EXCLUDED.method_number,
                        method_period = EXCLUDED.method_period
                    RETURNING id
                    """,
                    COMPANY_ID,
                    m["name"],
                    m["account_asset_number"],
                    m["account_depreciation_number"],
                    m["account_expense_number"],
                    m["method"],
                    m["method_number"],
                    m["method_period"],
                    True,
                )
                model_id = row["id"]
            else:
                model_id = None

            key = (m["account_asset_number"], m["account_depreciation_number"])
            if key not in model_id_map:
                model_id_map[key] = (model_id, m)

        logger.info("Models inserted: %d", len(MODELS_DATA))

        # ─── Step 5: Insert assets ───
        logger.info("\n--- Inserting %d assets ---", len(assets_data))

        stats = {"running": 0, "close": 0, "lines_total": 0}

        for idx, a in enumerate(assets_data, 1):
            # Resolve model_id
            key = (a["account_asset_number"], a["account_depreciation_number"])
            model_id, model_data = model_id_map.get(key, (None, None))

            # Get method_number from matched model (assets CSV doesn't have it)
            method_number = model_data["method_number"] if model_data else 60
            method_period = model_data["method_period"] if model_data else 1

            salvage_value = Decimal("0")

            logger.info(
                "  [%d/%d] %s | state=%s | original=%.2f | book=%.2f | model=%s",
                idx, len(assets_data), a["name"], a["state"],
                a["original_value"], a["book_value"],
                model_data["name"] if model_data else "NONE",
            )

            if not dry_run:
                asset_row = await conn.fetchrow(
                    """
                    INSERT INTO fixed_assets.assets
                        (company_id, name, model_id,
                         acquisition_date, original_value, salvage_value, currency,
                         account_asset_number, account_depreciation_number, account_expense_number,
                         method, method_number, method_period, prorata,
                         state)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    RETURNING id
                    """,
                    COMPANY_ID,
                    a["name"],
                    model_id,
                    a["acquisition_date"],
                    a["original_value"],
                    salvage_value,
                    a["currency"],
                    a["account_asset_number"],
                    a["account_depreciation_number"],
                    a["account_expense_number"],
                    a["method"],
                    method_number,
                    method_period,
                    True,  # prorata
                    a["state"],
                )
                asset_id = asset_row["id"]
            else:
                asset_id = None

            stats[a["state"]] = stats.get(a["state"], 0) + 1

            # ─── Step 6: Generate depreciation lines ───
            if a["original_value"] > 0:
                lines = compute_depreciation_schedule(
                    original_value=a["original_value"],
                    salvage_value=salvage_value,
                    method_number=method_number,
                    method_period=method_period,
                    acquisition_date=a["acquisition_date"],
                    prorata=True,
                    book_value_csv=a["book_value"],
                    is_closed=(a["state"] == "close"),
                )

                posted_count = sum(1 for l in lines if l["is_posted"])
                label = "all posted - closed" if a["state"] == "close" else f"{posted_count} posted, {len(lines) - posted_count} future"
                logger.info("    → %d depreciation lines (%s)", len(lines), label)

                if not dry_run and lines:
                    await conn.executemany(
                        """
                        INSERT INTO fixed_assets.depreciation_lines
                            (asset_id, company_id, line_number, depreciation_date,
                             amount, cumulated_amount, remaining_value, is_posted)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        [
                            (
                                asset_id,
                                COMPANY_ID,
                                l["line_number"],
                                l["depreciation_date"],
                                l["amount"],
                                l["cumulated_amount"],
                                l["remaining_value"],
                                l["is_posted"],
                            )
                            for l in lines
                        ],
                    )

                stats["lines_total"] += len(lines)

        # ─── Summary ───
        logger.info("\n" + "=" * 60)
        logger.info("MIGRATION SUMMARY%s", " (DRY RUN)" if dry_run else "")
        logger.info("=" * 60)
        logger.info("Company: %s (%s)", company["name"], COMPANY_ID)
        logger.info("Models inserted: %d", len(MODELS_DATA))
        logger.info("Assets inserted: %d (running=%d, close=%d)",
                     len(assets_data), stats["running"], stats["close"])
        logger.info("Depreciation lines: %d", stats["lines_total"])
        logger.info("=" * 60)

        if dry_run:
            logger.info("Re-run WITHOUT --dry-run to apply changes.")

    finally:
        await conn.close()
        logger.info("Connection closed.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(run_migration(dry_run=dry_run))
