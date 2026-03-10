#!/usr/bin/env python3
"""
E2E Test Script for Fixed Assets Module (Neon PostgreSQL).

Tests the complete lifecycle:
1. Migration check (schema exists)
2. Asset Model CRUD
3. Asset CRUD + confirm
4. Depreciation schedule generation
5. Run depreciation (post lines)
6. Disposal
7. PDF generation
8. Eligible accounts view
9. Cleanup

Usage:
    # Requires NEON_DATABASE_URL in env
    python scripts/test_fixed_assets_e2e.py

    # Or with explicit URL
    NEON_DATABASE_URL="postgresql://..." python scripts/test_fixed_assets_e2e.py
"""

import asyncio
import logging
import os
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("test_fixed_assets")

# Test company ID — must exist in core.companies
TEST_COMPANY_ID = os.getenv("TEST_COMPANY_ID", "")

# Counters
_passed = 0
_failed = 0


def ok(msg: str):
    global _passed
    _passed += 1
    logger.info(f"  ✅ {msg}")


def fail(msg: str, e: Exception = None):
    global _failed
    _failed += 1
    logger.error(f"  ❌ {msg}: {e}" if e else f"  ❌ {msg}")


async def get_pool():
    """Create a raw asyncpg pool for direct testing."""
    import asyncpg
    url = os.getenv("NEON_DATABASE_URL")
    if not url:
        raise RuntimeError("NEON_DATABASE_URL not set")
    return await asyncpg.create_pool(url, min_size=1, max_size=3, command_timeout=30)


async def test_schema_exists(pool):
    """Test 1: Verify fixed_assets schema and tables exist."""
    logger.info("Test 1: Schema existence check")
    async with pool.acquire() as conn:
        # Check schema
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = 'fixed_assets')"
        )
        if exists:
            ok("Schema fixed_assets exists")
        else:
            fail("Schema fixed_assets does not exist — run migration 020 first")
            return False

        # Check tables
        for table in ("asset_models", "assets", "depreciation_lines"):
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = 'fixed_assets' AND table_name = $1)",
                table,
            )
            if exists:
                ok(f"Table fixed_assets.{table} exists")
            else:
                fail(f"Table fixed_assets.{table} missing")

        # Check view
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.views WHERE table_schema = 'fixed_assets' AND table_name = 'eligible_accounts')"
        )
        if exists:
            ok("View fixed_assets.eligible_accounts exists")
        else:
            fail("View fixed_assets.eligible_accounts missing")

    return True


async def resolve_test_company(pool) -> UUID:
    """Find a test company or create a note about it."""
    global TEST_COMPANY_ID
    async with pool.acquire() as conn:
        if TEST_COMPANY_ID:
            row = await conn.fetchrow(
                "SELECT id FROM core.companies WHERE id = $1",
                UUID(TEST_COMPANY_ID),
            )
            if row:
                ok(f"Test company found: {TEST_COMPANY_ID}")
                return row["id"]
            else:
                fail(f"Test company {TEST_COMPANY_ID} not found in core.companies")
                return None

        # Pick first available company
        row = await conn.fetchrow("SELECT id, name FROM core.companies LIMIT 1")
        if row:
            TEST_COMPANY_ID = str(row["id"])
            ok(f"Using company: {row['name']} ({TEST_COMPANY_ID})")
            return row["id"]
        else:
            fail("No companies found in core.companies")
            return None


async def test_eligible_accounts(pool, company_id: UUID):
    """Test 2: Eligible accounts view."""
    logger.info("Test 2: Eligible accounts")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fixed_assets.eligible_accounts WHERE company_id = $1 ORDER BY role, account_number",
            company_id,
        )
        logger.info(f"  Found {len(rows)} eligible accounts")
        roles = {r["role"] for r in rows}
        for role in ("asset", "depreciation", "expense"):
            count = sum(1 for r in rows if r["role"] == role)
            if count > 0:
                ok(f"Role '{role}': {count} accounts")
            else:
                logger.warning(f"  ⚠️  No accounts with role '{role}' — model creation may fail")
        return rows


async def test_asset_model_crud(pool, company_id: UUID, eligible_accounts):
    """Test 3: Asset Model CRUD."""
    logger.info("Test 3: Asset Model CRUD")

    # Find accounts by role
    asset_acc = next((a for a in eligible_accounts if a["role"] == "asset"), None)
    depr_acc = next((a for a in eligible_accounts if a["role"] == "depreciation"), None)
    exp_acc = next((a for a in eligible_accounts if a["role"] == "expense"), None)

    if not all([asset_acc, depr_acc, exp_acc]):
        fail("Missing eligible accounts for all 3 roles — cannot test model CRUD")
        return None

    model_id = None
    async with pool.acquire() as conn:
        # CREATE
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO fixed_assets.asset_models
                    (company_id, name, account_asset_number, account_depreciation_number,
                     account_expense_number, method, method_number, method_period, prorata)
                VALUES ($1, $2, $3, $4, $5, 'linear', 60, 1, TRUE)
                RETURNING id, name
                """,
                company_id,
                "__test_model_e2e__",
                asset_acc["account_number"],
                depr_acc["account_number"],
                exp_acc["account_number"],
            )
            model_id = row["id"]
            ok(f"CREATE model: {model_id}")
        except Exception as e:
            fail("CREATE model", e)
            return None

        # READ
        try:
            row = await conn.fetchrow(
                "SELECT * FROM fixed_assets.asset_models WHERE id = $1",
                model_id,
            )
            assert row is not None
            assert row["name"] == "__test_model_e2e__"
            assert row["method"] == "linear"
            ok("READ model")
        except Exception as e:
            fail("READ model", e)

        # UPDATE
        try:
            await conn.execute(
                "UPDATE fixed_assets.asset_models SET method_number = 36 WHERE id = $1",
                model_id,
            )
            row = await conn.fetchrow(
                "SELECT method_number FROM fixed_assets.asset_models WHERE id = $1",
                model_id,
            )
            assert row["method_number"] == 36
            ok("UPDATE model (method_number → 36)")
        except Exception as e:
            fail("UPDATE model", e)

    return model_id


async def test_asset_lifecycle(pool, company_id: UUID, model_id: UUID):
    """Test 4: Asset full lifecycle (create → confirm → depreciate → dispose)."""
    logger.info("Test 4: Asset Lifecycle")

    # Get model details for account numbers
    async with pool.acquire() as conn:
        model = await conn.fetchrow(
            "SELECT * FROM fixed_assets.asset_models WHERE id = $1", model_id
        )

    from app.tools.neon_fixed_asset_manager import NeonFixedAssetManager

    mgr = NeonFixedAssetManager()

    # CREATE asset
    asset_id = None
    try:
        result = await mgr.create_asset(
            company_id,
            {
                "name": "__test_asset_e2e__",
                "model_id": str(model_id),
                "acquisition_date": "2025-01-01",
                "original_value": 12000.00,
                "salvage_value": 0,
                "currency": "CHF",
            },
        )
        asset_id = result["id"] if isinstance(result.get("id"), UUID) else UUID(str(result["id"]))
        ok(f"CREATE asset: {asset_id}")
    except Exception as e:
        fail("CREATE asset", e)
        return None

    # CONFIRM
    try:
        result = await mgr.confirm_asset(company_id, asset_id)
        assert result.get("success"), f"confirm failed: {result}"
        ok("CONFIRM asset → running")
    except Exception as e:
        fail("CONFIRM asset", e)
        return asset_id

    # CHECK depreciation lines generated
    try:
        lines = await mgr.get_depreciation_schedule(company_id, asset_id)
        assert len(lines) > 0, "No depreciation lines generated"
        ok(f"Depreciation schedule: {len(lines)} lines")

        # Verify first line
        first = lines[0]
        assert first["line_number"] == 1
        assert Decimal(str(first["remaining_value"])) < Decimal("12000")
        ok(f"First line: amount={first['amount']}, remaining={first['remaining_value']}")
    except Exception as e:
        fail("Depreciation schedule", e)

    # RUN DEPRECIATION (post first 3 lines)
    try:
        cutoff = date(2025, 4, 1)
        result = await mgr.run_depreciation(company_id, cutoff)
        posted = result.get("posted_count", 0)
        ok(f"Run depreciation (cutoff {cutoff}): {posted} lines posted")
    except Exception as e:
        fail("Run depreciation", e)

    # DISPOSE
    try:
        result = await mgr.dispose_asset(
            company_id,
            asset_id,
            disposal_type="sale",
            disposal_date=date(2025, 6, 15),
            disposal_value=Decimal("5000"),
        )
        assert result.get("success"), f"dispose failed: {result}"
        ok("DISPOSE asset (sale @ 5000 CHF)")
    except Exception as e:
        fail("DISPOSE asset", e)

    return asset_id


async def test_pdf_generation(company_id: UUID, asset_id: UUID):
    """Test 5: PDF generation."""
    logger.info("Test 5: PDF Generation")

    from app.tools.neon_fixed_asset_manager import get_fixed_asset_manager

    mgr = get_fixed_asset_manager()

    for lang in ("fr", "en", "de"):
        try:
            asset = await mgr.get_asset(company_id, asset_id)
            lines = await mgr.get_depreciation_schedule(company_id, asset_id)

            from app.tools.depreciation_pdf import generate_depreciation_schedule_pdf

            buf = generate_depreciation_schedule_pdf(
                asset=asset,
                lines=lines,
                company_name="Test Company E2E",
                language=lang,
            )
            size = len(buf.getvalue())
            assert size > 100, f"PDF too small: {size} bytes"
            ok(f"PDF ({lang}): {size} bytes")
        except Exception as e:
            fail(f"PDF ({lang})", e)


async def cleanup(pool, company_id: UUID, model_id: UUID, asset_id: UUID):
    """Cleanup test data."""
    logger.info("Cleanup")
    async with pool.acquire() as conn:
        if asset_id:
            await conn.execute(
                "DELETE FROM fixed_assets.depreciation_lines WHERE asset_id = $1",
                asset_id,
            )
            await conn.execute(
                "DELETE FROM fixed_assets.assets WHERE id = $1",
                asset_id,
            )
            ok(f"Deleted asset {asset_id}")

        if model_id:
            await conn.execute(
                "DELETE FROM fixed_assets.asset_models WHERE id = $1",
                model_id,
            )
            ok(f"Deleted model {model_id}")


async def main():
    logger.info("=" * 60)
    logger.info("Fixed Assets E2E Test Suite")
    logger.info("=" * 60)

    pool = await get_pool()

    try:
        # Test 1: Schema
        schema_ok = await test_schema_exists(pool)
        if not schema_ok:
            logger.error("Schema not found. Run migration 020 first:")
            logger.error("  psql $NEON_DATABASE_URL < migrations/020_create_fixed_assets_schema.sql")
            return

        # Resolve company
        company_id = await resolve_test_company(pool)
        if not company_id:
            logger.error("No test company available. Set TEST_COMPANY_ID env var.")
            return

        # Test 2: Eligible accounts
        eligible = await test_eligible_accounts(pool, company_id)

        # Test 3: Model CRUD
        model_id = await test_asset_model_crud(pool, company_id, eligible)

        # Test 4: Asset lifecycle
        asset_id = None
        if model_id:
            asset_id = await test_asset_lifecycle(pool, company_id, model_id)

        # Test 5: PDF
        if asset_id:
            await test_pdf_generation(company_id, asset_id)

        # Cleanup
        await cleanup(pool, company_id, model_id, asset_id)

    finally:
        await pool.close()

    logger.info("=" * 60)
    logger.info(f"Results: {_passed} passed, {_failed} failed")
    logger.info("=" * 60)
    sys.exit(1 if _failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
