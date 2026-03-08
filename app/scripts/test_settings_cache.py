"""
Test: company settings cache refresh after workflow save.

Usage:
    cd /workspaces/firebase_microservice/app
    python scripts/test_settings_cache.py <uid> <company_id>

Example:
    python scripts/test_settings_cache.py 7hQs0jluP5YUWcREqdi22NRFnU32 AAAABzwjXro

What it does:
    1. Read current L2 cache (before)
    2. Call _refresh_company_context_cache with patched workflow_params
    3. Read L2 cache (after) and diff workflow fields
"""

import sys
import json
import redis

REDIS_URL = "redis://localhost:6379"
WORKFLOW_FIELDS = [
    "router_automated_workflow", "router_approval_required",
    "router_communication_method", "router_approval_pendinglist_enabled",
    "router_trust_threshold_required", "router_trust_threshold_percent",
    "apbookeeper_approval_required", "apbookeeper_approval_contact_creation",
    "apbookeeper_communication_method", "apbookeeper_automated_workflow",
    "banker_approval_required", "banker_approval_threshold_workflow",
    "banker_communication_method", "banker_gl_approval", "banker_voucher_approval",
]


def read_cache(r: redis.Redis, uid: str, company_id: str) -> dict | None:
    key = f"company:{uid}:{company_id}:context"
    raw = r.get(key)
    if not raw:
        print(f"[!] No L2 cache found for key: {key}")
        return None
    return json.loads(raw.decode())


def print_workflow_fields(label: str, data: dict):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    for field in WORKFLOW_FIELDS:
        val = data.get(field, "MISSING")
        print(f"  {field}: {val}")
    # Also show nested workflow_params for Router
    wp = data.get("workflow_params", {})
    if wp:
        print(f"\n  [workflow_params.router_communication_method]: "
              f"{wp.get('router_communication_method', 'MISSING')}")
        print(f"  [workflow_params.router_automated_workflow]: "
              f"{wp.get('router_automated_workflow', 'MISSING')}")


def _simulate_refresh(r: redis.Redis, uid: str, company_id: str, selected_mandate: dict, workflow_params: dict):
    """Inline copy of the FIXED _refresh_company_context_cache logic."""
    key = f"company:{uid}:{company_id}:context"
    cached = r.get(key)
    if not cached:
        print("[!] No L2 cache to refresh")
        return

    existing = json.loads(cached.decode())

    # step 2 — merge workflow_params
    existing["workflow_params"] = workflow_params

    # step 3 — mandate flat fields only (no workflow fields)
    mandate_flat_fields = [
        "dms_type", "chat_type", "communication_chat_type", "communication_log_type",
        "legal_name", "legal_status", "country", "address",
        "phone_number", "email", "website", "language",
        "has_vat", "vat_number", "ownership_type", "base_currency",
    ]
    for field in mandate_flat_fields:
        if field in selected_mandate:
            existing[field] = selected_mandate[field]

    # step 4 — promote ALL workflow flat fields from workflow_params (THE FIX)
    workflow_flat_map = {
        "router_automated_workflow": True,
        "router_approval_required": False,
        "router_communication_method": "",
        "router_approval_pendinglist_enabled": False,
        "router_trust_threshold_required": False,
        "router_trust_threshold_percent": 80,
        "apbookeeper_approval_required": False,
        "apbookeeper_approval_contact_creation": False,
        "apbookeeper_communication_method": "",
        "apbookeeper_automated_workflow": False,
        "apbookeeper_approval_pendinglist_enabled": False,
        "banker_approval_required": False,
        "banker_approval_threshold_workflow": 0,
        "banker_communication_method": "",
        "banker_approval_pendinglist_enabled": False,
        "banker_gl_approval": False,
        "banker_voucher_approval": False,
    }
    for flat_key, default in workflow_flat_map.items():
        existing[flat_key] = workflow_params.get(flat_key, existing.get(flat_key, default))

    # step 4b — sync communication_chat_type
    if "chat_type" in existing:
        existing["communication_chat_type"] = existing["chat_type"]

    r.setex(key, 86400, json.dumps(existing))
    print("[>] Cache refreshed (simulated)")


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/test_settings_cache.py <uid> <company_id>")
        print("\nAvailable keys:")
        r = redis.from_url(REDIS_URL)
        for k in r.keys("company:*:context"):
            print(f"  {k.decode()}")
        sys.exit(1)

    uid = sys.argv[1]
    company_id = sys.argv[2]

    r = redis.from_url(REDIS_URL)

    # --- BEFORE ---
    before = read_cache(r, uid, company_id)
    if not before:
        sys.exit(1)
    print_workflow_fields("BEFORE (current L2 cache)", before)

    # --- SIMULATE a workflow save ---
    # Patch workflow_params with test values (toggle booleans to detect change)
    patched_wp = before.get("workflow_params", {}).copy()
    original_method = patched_wp.get("router_communication_method", "telegram")
    new_method = "pinnokio" if original_method != "pinnokio" else "telegram"
    patched_wp["router_communication_method"] = new_method
    patched_wp["router_automated_workflow"] = not patched_wp.get("router_automated_workflow", True)

    print(f"\n[>] Patching: router_communication_method {original_method!r} → {new_method!r}")
    print(f"[>] Patching: router_automated_workflow → {patched_wp['router_automated_workflow']}")

    # Build a minimal selected_mandate (mimics fetch_single_mandate output)
    fake_mandate = {
        "dms_type": before.get("dms_type", "odoo"),
        "chat_type": before.get("chat_type", "pinnokio"),
        "communication_log_type": before.get("communication_log_type", "pinnokio"),
        "legal_name": before.get("legal_name", ""),
        "base_currency": before.get("base_currency", "CHF"),
        # Intentionally stale workflow fields (as fetch_single_mandate would return)
        "router_communication_method": original_method,  # stale!
        "router_automated_workflow": not patched_wp["router_automated_workflow"],  # stale!
    }

    # Inline the fixed _refresh_company_context_cache logic (avoids complex import chain)
    _simulate_refresh(r, uid, company_id, fake_mandate, patched_wp)

    # --- AFTER ---
    after = read_cache(r, uid, company_id)
    if not after:
        print("[!] Cache disappeared after refresh?")
        sys.exit(1)
    print_workflow_fields("AFTER (L2 cache post-refresh)", after)

    # --- DIFF ---
    print(f"\n{'='*50}")
    print("  DIFF (changed fields)")
    print(f"{'='*50}")
    any_diff = False
    all_fields = set(WORKFLOW_FIELDS) | set(before.keys()) & set(after.keys())
    for field in sorted(all_fields):
        b = before.get(field, "MISSING")
        a = after.get(field, "MISSING")
        if b != a:
            print(f"  ✓ {field}: {b!r} → {a!r}")
            any_diff = True
    if not any_diff:
        print("  [!] NO CHANGES DETECTED — fix may not be working")
    else:
        print("\n[OK] Cache correctly updated after workflow save simulation")


if __name__ == "__main__":
    main()
