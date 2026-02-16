"""
Script de diagnostic : inspecte le cache billing_history dans Redis.
Montre le format du cache, le status d'un job specifique, et les statistiques.

Usage:
    python debug_billing_cache.py
    python debug_billing_cache.py --job-id klk_0a5ebc16-5cc5-4138-8a07-42fe1baeb8ce
"""

import json
import sys
import redis
from collections import Counter

# ============================================================
# CONFIG - Redis local (USE_LOCAL_REDIS=true)
# ============================================================
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0

# IDs du test (modifier si necessaire)
UID = "7hQs0jluP5YUWcREqdi22NRFnU32"
COMPANY_ID = "AAAAgaDzK_I"
TARGET_JOB_ID = "klk_0a5ebc16-5cc5-4138-8a07-42fe1baeb8ce"

# ============================================================
# Parse args
# ============================================================
if "--job-id" in sys.argv:
    idx = sys.argv.index("--job-id")
    if idx + 1 < len(sys.argv):
        TARGET_JOB_ID = sys.argv[idx + 1]


def main():
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

    billing_key = f"business:{UID}:{COMPANY_ID}:billing_history"
    page_state_key = f"page_state:{UID}:{COMPANY_ID}:dashboard"

    print("=" * 70)
    print("DIAGNOSTIC BILLING_HISTORY CACHE")
    print("=" * 70)

    # -- 1. Verifier la cle billing_history --
    print(f"\n[BILLING] Cle Redis: {billing_key}")
    ttl = r.ttl(billing_key)
    raw = r.get(billing_key)

    target_item = None
    if not raw:
        print("   [X] CACHE VIDE - la cle n'existe pas dans Redis")
        print(f"   TTL: {ttl} (-2 = cle inexistante)")
    else:
        print(f"   [OK] CACHE EXISTE - TTL restant: {ttl}s ({ttl // 60}min {ttl % 60}s)")
        data = json.loads(raw)

        # Detecter le format
        if "cache_version" in data and "data" in data:
            print(f"   Format: ENVELOPPE (unified_cache_manager)")
            print(f"      cache_version: {data.get('cache_version')}")
            print(f"      cached_at: {data.get('cached_at')}")
            print(f"      source: {data.get('source')}")
            inner = data["data"]
        elif "items" in data:
            print(f"   Format: BRUT (main.py / redis_subscriber)")
            inner = data
        else:
            print(f"   [!] Format: INCONNU - cles: {list(data.keys())[:10]}")
            inner = data

        items = inner.get("items", [])
        print(f"\n   Total items: {len(items)}")

        # Statistiques par status
        status_counts = Counter(item.get("status", "(vide)") for item in items)
        print(f"\n   Repartition des statuts:")
        for status, count in status_counts.most_common():
            marker = "[OK]" if status in ("completed", "close", "closed") else "[!!]"
            print(f"      {marker} {status or '(vide)'}: {count}")

        # Statistiques par department
        dept_counts = Counter(item.get("department", "(vide)") for item in items)
        print(f"\n   Repartition par departement:")
        for dept, count in dept_counts.most_common():
            print(f"      {dept}: {count}")

        # Chercher le job cible
        print(f"\n   Recherche du job: {TARGET_JOB_ID}")
        for i, item in enumerate(items):
            if item.get("jobId") == TARGET_JOB_ID or item.get("id") == TARGET_JOB_ID:
                target_item = item
                print(f"      [OK] TROUVE a l'index {i}")
                break

        if target_item:
            print(f"\n      === DETAIL DU JOB ===")
            print(f"      jobId:        {target_item.get('jobId')}")
            print(f"      fileName:     {target_item.get('fileName')}")
            print(f"      department:   {target_item.get('department')}")
            print(f"      status:       {target_item.get('status')} <-- VALEUR ACTUELLE")
            print(f"      currentStep:  {target_item.get('currentStep')}")
            print(f"      lastMessage:  {str(target_item.get('lastMessage', ''))[:80]}")
            print(f"      lastOutcome:  {str(target_item.get('lastOutcome', ''))[:80]}")
            print(f"      cost:         {target_item.get('cost')} {target_item.get('currency')}")
            print(f"      totalTokens:  {target_item.get('totalTokens')}")
            print(f"      timestamp:    {target_item.get('timestamp')}")
        else:
            print(f"      [X] JOB NON TROUVE dans les {len(items)} items")
            # Montrer les items "apex" pour aider
            apex_items = [it for it in items if "apex" in (it.get("fileName") or "").lower()]
            if apex_items:
                print(f"\n      Items contenant 'apex' ({len(apex_items)}):")
                for it in apex_items[:10]:
                    print(f"         {it.get('jobId', '?')[:40]}... | {it.get('department')} | status={it.get('status')}")

    # -- 2. Verifier le page_state dashboard --
    print(f"\n{'=' * 70}")
    print(f"[PAGE_STATE] Cle: {page_state_key}")
    ps_ttl = r.ttl(page_state_key)
    ps_raw = r.get(page_state_key)
    ps_target = None

    if not ps_raw:
        print(f"   [X] PAGE_STATE VIDE - cle inexistante (TTL: {ps_ttl})")
    else:
        print(f"   [OK] PAGE_STATE EXISTE - TTL restant: {ps_ttl}s ({ps_ttl // 60}min {ps_ttl % 60}s)")
        ps_data = json.loads(ps_raw)
        expenses = ps_data.get("data", {}).get("expenses", {})
        ps_items = expenses.get("items", [])
        print(f"   expenses.items dans page_state: {len(ps_items)}")

        if ps_items:
            ps_status_counts = Counter(item.get("status", "(vide)") for item in ps_items)
            print(f"   Statuts dans page_state:")
            for status, count in ps_status_counts.most_common():
                marker = "[OK]" if status in ("completed", "close", "closed") else "[!!]"
                print(f"      {marker} {status or '(vide)'}: {count}")

            # Chercher le job cible dans page_state
            ps_target = next((it for it in ps_items if it.get("jobId") == TARGET_JOB_ID or it.get("id") == TARGET_JOB_ID), None)
            if ps_target:
                print(f"\n   Job dans page_state: status = '{ps_target.get('status')}' <-- COMPARER AVEC BILLING_HISTORY")
            else:
                print(f"\n   Job {TARGET_JOB_ID[:30]}... NON TROUVE dans page_state")

    # -- 3. Verifier le business cache invoices --
    print(f"\n{'=' * 70}")
    invoices_key = f"business:{UID}:{COMPANY_ID}:invoices"
    inv_raw = r.get(invoices_key)
    if inv_raw:
        inv_data = json.loads(inv_raw)
        # Check if wrapped
        if "data" in inv_data and isinstance(inv_data["data"], dict):
            inv_inner = inv_data["data"]
        else:
            inv_inner = inv_data
        print(f"[INVOICES] Business cache: {invoices_key}")
        for category in ["pending", "processing", "processed", "error"]:
            cat_items = inv_inner.get(category, [])
            target_in_cat = any(
                it.get("job_id") == TARGET_JOB_ID or it.get("id") == TARGET_JOB_ID or it.get("jobId") == TARGET_JOB_ID
                for it in cat_items
            )
            marker = " <-- JOB ICI" if target_in_cat else ""
            print(f"   {category}: {len(cat_items)} items{marker}")
    else:
        print(f"[INVOICES] Business cache: VIDE")

    # -- 4. Resume --
    print(f"\n{'=' * 70}")
    print("RESUME")
    print("=" * 70)
    if raw and target_item:
        billing_status = target_item.get("status")
        ps_status = ps_target.get("status") if ps_raw and ps_target else "N/A"
        print(f"   billing_history cache: status = '{billing_status}'")
        print(f"   page_state snapshot:   status = '{ps_status}'")
        if billing_status != ps_status:
            print(f"   [!] DESYNCHRONISE ! Le cache et le snapshot different.")
        else:
            print(f"   [OK] Synchronise (meme status)")
    elif not raw:
        print(f"   billing_history cache: VIDE (pas de donnees)")
        if ps_raw and ps_target:
            print(f"   page_state snapshot:   status = '{ps_target.get('status')}'")
    print()


if __name__ == "__main__":
    main()
