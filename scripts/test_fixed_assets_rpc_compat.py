#!/usr/bin/env python3
"""
Test de compatibilite RPC Fixed Assets : klk_accountant → backend.

Simule exactement les 3 appels RPC du fixed_asset_rpc_adapter.py
avec le meme format de payload et verifie que les retours sont
compatibles avec le contrat I/O attendu par klk_accountant.

Cible: Dulce Sport Ecole SARL (mandate migree avec 6 modeles, 80 assets)

Usage:
    NEON_DATABASE_URL="postgresql://..." python scripts/test_fixed_assets_rpc_compat.py
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Add firebase_microservice to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Note: firebase_mandate_path in Neon is stored WITHOUT leading slash
MANDATE_PATH = "clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/kXAQYwMgsMrV60jeVcuz/mandates/BIr6edJxlNeKUBZxNhu4"

# ============================================================
# Direct handler invocation (simulates /rpc dispatch)
# ============================================================

async def rpc_call_direct(rpc_method: str, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Call RPC handler directly (same as POST /rpc would do).
    This bypasses HTTP but tests the exact same handler code.
    """
    from app.fixed_asset_rpc_handlers import get_fixed_asset_handlers

    handlers = get_fixed_asset_handlers()

    # Parse method: "FIXED_ASSETS.list_asset_models" -> "list_asset_models"
    _, func_name = rpc_method.split(".", 1)
    func = getattr(handlers, func_name, None)
    if not func:
        return {"success": False, "error": f"Method {func_name} not found"}

    result = await func(**kwargs)
    return result


# ============================================================
# Adapter simulation (same logic as klk_accountant adapter)
# ============================================================

def adapt_list_asset_models(rpc_result: Dict) -> List[Dict]:
    """
    Same adaptation as fixed_asset_rpc_adapter.py:list_asset_models().
    Converts RPC output to pyodoo-compatible format.
    """
    if not rpc_result or not rpc_result.get("success"):
        return []

    adapted = []
    for m in rpc_result.get("models", []):
        adapted.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "account_asset_id": m.get("account_asset_number"),
            "account_asset_number": m.get("account_asset_number"),
            "account_asset_name": m.get("account_asset_name"),
            "account_depreciation_id": m.get("account_depreciation_number"),
            "account_depreciation_number": m.get("account_depreciation_number"),
            "account_depreciation_name": m.get("account_depreciation_name"),
            "account_depreciation_expense_id": m.get("account_expense_number"),
            "account_depreciation_expense_number": m.get("account_expense_number"),
            "account_depreciation_expense_name": m.get("account_expense_name"),
            "method": m.get("method", "linear"),
            "method_number": m.get("method_number", 60),
            "method_period": m.get("method_period", 1),
            "journal_id": None,
            "journal_name": None,
        })
    return adapted


def adapt_create_asset_model(rpc_result: Dict) -> Dict:
    """Same adaptation as adapter create_asset_model_with_journal()."""
    if not rpc_result or not rpc_result.get("success"):
        error = (rpc_result or {}).get("error", "Unknown")
        return {"error": error}

    model = rpc_result.get("model", {})
    return {
        "journal": {"id": None, "name": model.get("name"), "code": "N/A"},
        "asset_model": {"id": model.get("id"), "name": model.get("name")},
    }


def adapt_create_asset(rpc_result: Dict) -> Dict:
    """Same adaptation as adapter create_asset()."""
    if not rpc_result or not rpc_result.get("success"):
        error = (rpc_result or {}).get("error", "Unknown")
        return {"error": error}

    asset = rpc_result.get("asset", {})
    return {
        "id": asset.get("id"),
        "name": asset.get("name"),
        "model_id": asset.get("model_id"),
        "acquisition_date": asset.get("acquisition_date"),
        "original_value": asset.get("original_value"),
        "state": asset.get("state", "draft"),
    }


# ============================================================
# TESTS
# ============================================================

async def test_1_list_asset_models():
    """Test 1: FIXED_ASSETS.list_asset_models — same call as adapter line 76-78."""
    logger.info("=" * 60)
    logger.info("TEST 1: FIXED_ASSETS.list_asset_models")
    logger.info("=" * 60)

    # RPC call (same as adapter)
    result = await rpc_call_direct(
        "FIXED_ASSETS.list_asset_models",
        mandate_path=MANDATE_PATH,
    )

    # Verify RPC response structure
    assert result is not None, "RPC returned None"
    assert result.get("success") is True, f"RPC failed: {result.get('error')}"
    assert "models" in result, "Missing 'models' key in response"
    assert isinstance(result["models"], list), "models should be a list"
    assert len(result["models"]) == 6, f"Expected 6 models, got {len(result['models'])}"

    logger.info("  RPC response OK: %d models", len(result["models"]))

    # Verify each model has required fields (as consumed by adapter)
    required_fields = [
        "id", "name", "account_asset_number", "account_depreciation_number",
        "account_expense_number", "method", "method_number", "method_period",
    ]
    for m in result["models"]:
        for field in required_fields:
            assert field in m, f"Model '{m.get('name')}' missing field: {field}"

    # Also check the optional name fields used by adapter
    optional_name_fields = ["account_asset_name", "account_depreciation_name", "account_expense_name"]
    first_model = result["models"][0]
    for field in optional_name_fields:
        if field not in first_model:
            logger.warning("  WARNING: Field '%s' missing (adapter uses it for display)", field)

    # Now test the adapter transformation
    adapted = adapt_list_asset_models(result)
    assert len(adapted) == 6

    # Verify pyodoo-compatible keys exist
    pyodoo_keys = [
        "id", "name", "account_asset_id", "account_asset_number",
        "account_depreciation_id", "account_depreciation_number",
        "account_depreciation_expense_id", "account_depreciation_expense_number",
        "method", "method_number", "method_period", "journal_id",
    ]
    for a in adapted:
        for key in pyodoo_keys:
            assert key in a, f"Adapted model missing pyodoo key: {key}"

    logger.info("  Adapter transformation OK")
    logger.info("  Sample model: %s", json.dumps(adapted[0], indent=2, default=str))

    # Return first model_id for test 3
    return adapted[0]["id"]


async def test_2_create_asset_model():
    """Test 2: FIXED_ASSETS.create_asset_model — same call as adapter line 117-127."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: FIXED_ASSETS.create_asset_model")
    logger.info("=" * 60)

    # RPC call (same args as adapter create_asset_model_with_journal)
    result = await rpc_call_direct(
        "FIXED_ASSETS.create_asset_model",
        mandate_path=MANDATE_PATH,
        name="__TEST_MODEL_RPC__",
        account_asset_number="1500",
        account_depreciation_number="1509",
        account_expense_number="6800",
        method="linear",
        method_number=48,
        method_period=1,
    )

    assert result is not None, "RPC returned None"
    assert result.get("success") is True, f"RPC failed: {result.get('error')}"
    assert "model" in result, "Missing 'model' key in response"

    model = result["model"]
    assert model.get("name") == "__TEST_MODEL_RPC__"
    assert model.get("id") is not None

    logger.info("  RPC response OK: model id=%s", model["id"])

    # Test adapter transformation
    adapted = adapt_create_asset_model(result)
    assert "asset_model" in adapted, "Adapter output missing 'asset_model'"
    assert "journal" in adapted, "Adapter output missing 'journal'"
    assert adapted["asset_model"]["id"] == model["id"]
    assert adapted["journal"]["id"] is None  # No journal in Neon

    logger.info("  Adapter transformation OK")
    logger.info("  Adapted: %s", json.dumps(adapted, indent=2, default=str))

    return model["id"]


async def test_3_create_asset(model_id: str):
    """Test 3: FIXED_ASSETS.create_asset — same call as adapter line 145-151."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: FIXED_ASSETS.create_asset")
    logger.info("=" * 60)

    # RPC call (same args as adapter create_asset)
    result = await rpc_call_direct(
        "FIXED_ASSETS.create_asset",
        mandate_path=MANDATE_PATH,
        name="__TEST_ASSET_RPC__",
        model_id=str(model_id),
        acquisition_date="2025-01-15",
        original_value=10000.0,
    )

    assert result is not None, "RPC returned None"
    assert result.get("success") is True, f"RPC failed: {result.get('error')}"
    assert "asset" in result, "Missing 'asset' key in response"

    asset = result["asset"]
    assert asset.get("name") == "__TEST_ASSET_RPC__"
    assert asset.get("id") is not None
    assert asset.get("state") == "draft"
    assert asset.get("model_id") is not None

    logger.info("  RPC response OK: asset id=%s, state=%s", asset["id"], asset["state"])

    # Test adapter transformation
    adapted = adapt_create_asset(result)
    assert adapted.get("id") == asset["id"]
    assert adapted.get("name") == "__TEST_ASSET_RPC__"
    assert adapted.get("state") == "draft"
    assert adapted.get("original_value") is not None

    logger.info("  Adapter transformation OK")
    logger.info("  Adapted: %s", json.dumps(adapted, indent=2, default=str))

    return asset["id"]


async def test_4_get_depreciation_schedule(asset_id: str):
    """Test 4: Verify depreciation lines were generated for the new asset."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 4: FIXED_ASSETS.get_depreciation_schedule")
    logger.info("=" * 60)

    result = await rpc_call_direct(
        "FIXED_ASSETS.get_depreciation_schedule",
        mandate_path=MANDATE_PATH,
        asset_id=str(asset_id),
    )

    assert result is not None, "RPC returned None"
    assert result.get("success") is True, f"RPC failed: {result.get('error')}"
    assert "lines" in result, "Missing 'lines' key"

    lines = result["lines"]
    logger.info("  Depreciation lines: %d", len(lines))

    if lines:
        first = lines[0]
        last = lines[-1]
        logger.info("  First line: date=%s, amount=%s, remaining=%s",
                     first.get("depreciation_date"), first.get("amount"), first.get("remaining_value"))
        logger.info("  Last line:  date=%s, amount=%s, remaining=%s",
                     last.get("depreciation_date"), last.get("amount"), last.get("remaining_value"))

        # Verify structure
        for field in ["line_number", "depreciation_date", "amount", "cumulated_amount", "remaining_value", "is_posted"]:
            assert field in first, f"Missing field: {field}"

    return len(lines)


async def test_5_list_assets_with_migration_data():
    """Test 5: Verify migrated assets are queryable."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 5: FIXED_ASSETS.list_assets (migrated data)")
    logger.info("=" * 60)

    # List running assets
    result = await rpc_call_direct(
        "FIXED_ASSETS.list_assets",
        mandate_path=MANDATE_PATH,
        state=["running"],
    )

    assert result is not None
    assert result.get("success") is True, f"RPC failed: {result.get('error')}"
    assert "assets" in result

    running = result["assets"]
    logger.info("  Running assets: %d", len(running))

    # Check one asset has all expected fields
    if running:
        sample = running[0]
        expected = ["id", "name", "acquisition_date", "original_value", "state",
                    "account_asset_number", "account_depreciation_number", "account_expense_number",
                    "method", "method_number", "method_period", "currency"]
        for field in expected:
            assert field in sample, f"Asset missing field: {field}"
        logger.info("  Sample: %s | original=%s | state=%s",
                     sample["name"], sample["original_value"], sample["state"])

    # List close assets
    result_close = await rpc_call_direct(
        "FIXED_ASSETS.list_assets",
        mandate_path=MANDATE_PATH,
        state=["close"],
    )
    close_count = len(result_close.get("assets", []))
    logger.info("  Close assets: %d", close_count)

    return len(running), close_count


async def test_6_eligible_accounts():
    """Test 6: Verify eligible accounts for pickers."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 6: FIXED_ASSETS.get_eligible_accounts")
    logger.info("=" * 60)

    result = await rpc_call_direct(
        "FIXED_ASSETS.get_eligible_accounts",
        mandate_path=MANDATE_PATH,
    )

    assert result is not None
    assert result.get("success") is True
    accounts = result.get("accounts", [])
    logger.info("  Eligible accounts: %d", len(accounts))

    roles = {}
    for a in accounts:
        role = a.get("role", "unknown")
        roles[role] = roles.get(role, 0) + 1
    logger.info("  By role: %s", roles)

    assert "asset" in roles, "No 'asset' role accounts found"
    assert "depreciation" in roles, "No 'depreciation' role accounts found"
    assert "expense" in roles, "No 'expense' role accounts found"

    return len(accounts)


async def cleanup(model_id: str, asset_id: str):
    """Cleanup test data."""
    logger.info("\n" + "=" * 60)
    logger.info("CLEANUP: Deleting test model and asset")
    logger.info("=" * 60)

    # Delete test asset first (it references the model)
    if asset_id:
        result = await rpc_call_direct(
            "FIXED_ASSETS.delete_asset_model",  # soft delete
            mandate_path=MANDATE_PATH,
            model_id=str(model_id),
        )
        # Direct SQL cleanup for the test asset
        from app.tools.neon_fixed_asset_manager import get_fixed_asset_manager
        mgr = get_fixed_asset_manager()
        pool = await mgr._get_pool()
        async with pool.acquire() as conn:
            # Delete depreciation lines first
            await conn.execute(
                "DELETE FROM fixed_assets.depreciation_lines WHERE asset_id = $1",
                __import__("uuid").UUID(asset_id),
            )
            # Delete asset
            await conn.execute(
                "DELETE FROM fixed_assets.assets WHERE id = $1",
                __import__("uuid").UUID(asset_id),
            )
            logger.info("  Deleted test asset %s", asset_id)

    # Hard delete test model
    if model_id:
        from app.tools.neon_fixed_asset_manager import get_fixed_asset_manager
        mgr = get_fixed_asset_manager()
        pool = await mgr._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM fixed_assets.asset_models WHERE id = $1",
                __import__("uuid").UUID(model_id),
            )
            logger.info("  Deleted test model %s", model_id)


async def main():
    test_model_id = None
    test_asset_id = None

    try:
        # Test 1: List models (read migrated data)
        first_model_id = await test_1_list_asset_models()

        # Test 2: Create model (write)
        test_model_id = await test_2_create_asset_model()

        # Test 3: Create asset (write + auto-generate depreciation)
        test_asset_id = await test_3_create_asset(test_model_id)

        # Test 4: Get depreciation schedule
        line_count = await test_4_get_depreciation_schedule(test_asset_id)

        # Test 5: List migrated assets
        running_count, close_count = await test_5_list_assets_with_migration_data()

        # Test 6: Eligible accounts
        account_count = await test_6_eligible_accounts()

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("ALL TESTS PASSED")
        logger.info("=" * 60)
        logger.info("  list_asset_models: 6 models OK")
        logger.info("  create_asset_model: model created OK")
        logger.info("  create_asset: asset created + %d depreciation lines", line_count)
        logger.info("  list_assets: %d running, %d close", running_count, close_count)
        logger.info("  eligible_accounts: %d accounts", account_count)
        logger.info("  Adapter transformations: all compatible with pyodoo I/O")
        logger.info("  RPC channel: direct handler invocation (same as POST /rpc)")
        logger.info("=" * 60)

    except AssertionError as e:
        logger.error("TEST FAILED: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("UNEXPECTED ERROR: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        await cleanup(test_model_id, test_asset_id)


if __name__ == "__main__":
    asyncio.run(main())
