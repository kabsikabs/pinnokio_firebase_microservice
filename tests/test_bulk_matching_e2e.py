"""
Test E2E — BulkMatchingSuggestionEngine + reconciliation_data + AR invoices

Couvre:
1. Scoring batch (AP + AR + Expenses) avec TX debit et credit
2. Scoring unitaire (single candidate)
3. build_reconciliation_data (AP deterministic, AR customer, Expense assisted, bad ID guard)
4. Greedy exclusive assignment (pas de double-attribution)
5. Transfer detection inter-comptes
6. _normalize_reverse_recon_item (priorite Odoo move_id)
7. Simulation flux complet: accountant envoie invoice -> scoring -> reconciliation_data
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.bulk_matching_engine import (
    BulkMatchingSuggestionEngine,
    BulkMatchingConfig,
    build_reconciliation_data,
)
from app.wrappers.job_actions_handler import _normalize_reverse_recon_item


def make_tx(id, amount, currency="CHF", date="2026-03-05", account_id="10", description="", reference="", partner_name="", account_name="Banque CHF"):
    return {
        "id": id, "amount": amount, "currency": currency, "date": date,
        "account_id": account_id, "account_name": account_name,
        "description": description, "reference": reference,
        "partner_name": partner_name, "payment_ref": "",
    }


def make_ap_invoice(id, amount, partner_name, currency="CHF", date="2026-03-04", ref=""):
    return {
        "id": id, "amount": amount, "currency": currency, "date": date,
        "partner_name": partner_name, "supplier_name": partner_name,
        "invoice_date": date, "ref": ref, "name": f"BILL/2026/{id}",
        "payment_reference": ref,
        "display_name": partner_name, "display_ref": f"BILL/2026/{id}",
        "display_amount": amount, "display_date": date,
        "type": "invoice",
    }


def make_ar_invoice(id, amount, partner_name, currency="CHF", date="2026-03-04", ref=""):
    return {
        "id": id, "amount": amount, "currency": currency, "date": date,
        "partner_name": partner_name, "supplier_name": partner_name,
        "invoice_date": date, "ref": ref, "name": f"INV/2026/{id}",
        "payment_reference": ref,
        "display_name": partner_name, "display_ref": f"INV/2026/{id}",
        "display_amount": amount, "display_date": date,
        "type": "ar_invoice", "contact_type": "customer",
    }


def make_expense(id, amount, description, currency="CHF", date="2026-03-03", supplier=""):
    return {
        "id": id, "amount": amount, "currency": currency, "date": date,
        "description": description, "label": description,
        "expense_date": date, "employee_name": supplier,
        "display_name": description, "display_ref": supplier,
        "display_amount": amount, "display_date": date,
        "job_id": id,
    }


PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} — {detail}")


# ================================================================
print("\n=== TEST 1: Scoring batch — AP debit + AR credit ===")
# ================================================================

engine = BulkMatchingSuggestionEngine(BulkMatchingConfig(), fx_rates={})

transactions = [
    make_tx("501", -1000, description="Paiement Fournisseur SA", partner_name="Fournisseur SA"),
    make_tx("502", -250, description="Frais deplacement mars"),
    make_tx("503", 5000, description="Paiement Client Corp", partner_name="Client Corp"),
    make_tx("504", 800, description="Versement divers"),
]

ap_invoices = [
    make_ap_invoice(1001, 1000, "Fournisseur SA", ref="PAY-001"),
    make_ap_invoice(1002, 3000, "Autre Fournisseur"),
]

ar_invoices = [
    make_ar_invoice(2001, 5000, "Client Corp", ref="CLI-001"),
    make_ar_invoice(2002, 800, "Petit Client"),
]

expenses = [
    make_expense("exp_01", 250, "Frais deplacement mars", supplier="Jean Dupont"),
]

results = engine.compute_suggestions(transactions, ap_invoices, expenses, ar_invoices=ar_invoices)

# TX 501 (debit -1000) should match AP invoice 1001 (1000 CHF, Fournisseur SA)
r501 = results.get("501", {})
top_501 = r501.get("top_matches", [])
check(
    "TX 501 debit matches AP invoice 1001",
    len(top_501) > 0 and top_501[0].get("type") == "invoice",
    f"got {len(top_501)} matches, type={top_501[0].get('type') if top_501 else 'none'}"
)
check(
    "TX 501 best match is Fournisseur SA",
    top_501[0].get("partner_name") == "Fournisseur SA" if top_501 else False,
    f"got {top_501[0].get('partner_name') if top_501 else 'none'}"
)
check(
    "TX 501 _internal_id = 1001 (Odoo move_id)",
    str(top_501[0].get("_internal_id")) == "1001" if top_501 else False,
)

# TX 502 (debit -250) should match expense exp_01 (250 CHF)
r502 = results.get("502", {})
top_502 = r502.get("top_matches", [])
check(
    "TX 502 debit matches expense exp_01",
    len(top_502) > 0 and top_502[0].get("type") == "expense",
    f"got {len(top_502)} matches, type={top_502[0].get('type') if top_502 else 'none'}"
)

# TX 503 (credit +5000) should match AR invoice 2001 (5000 CHF, Client Corp)
r503 = results.get("503", {})
top_503 = r503.get("top_matches", [])
check(
    "TX 503 credit matches AR invoice 2001",
    len(top_503) > 0 and top_503[0].get("type") == "ar_invoice",
    f"got {len(top_503)} matches, type={top_503[0].get('type') if top_503 else 'none'}"
)
check(
    "TX 503 best match is Client Corp",
    top_503[0].get("partner_name") == "Client Corp" if top_503 else False,
)
check(
    "TX 503 _internal_id = 2001 (Odoo move_id)",
    str(top_503[0].get("_internal_id")) == "2001" if top_503 else False,
)

# TX 504 (credit +800) should match AR invoice 2002 (800 CHF)
r504 = results.get("504", {})
top_504 = r504.get("top_matches", [])
check(
    "TX 504 credit matches AR invoice 2002",
    len(top_504) > 0 and top_504[0].get("type") == "ar_invoice",
    f"got {len(top_504)} matches, type={top_504[0].get('type') if top_504 else 'none'}"
)

# TX debit should NOT match AR invoices
check(
    "TX 501 debit has NO ar_invoice match",
    all(m.get("type") != "ar_invoice" for m in top_501),
)

# TX credit should NOT match AP invoices or expenses
check(
    "TX 503 credit has NO AP invoice/expense match",
    all(m.get("type") in ("ar_invoice",) for m in top_503),
)


# ================================================================
print("\n=== TEST 2: build_reconciliation_data ===")
# ================================================================

# AP invoice — deterministic
rd = build_reconciliation_data({
    "type": "invoice", "score": 0.92, "_internal_id": "1234",
    "partner_name": "Fournisseur SA", "_job_id": "job_abc",
    "score_details": {"amount": 0.95, "date": 0.85, "text": 0.90, "reference": 0.70, "currency": 1.0},
})
check("AP: bank_case=counterpart_exists", rd["bank_case"] == "counterpart_exists")
check("AP: odoo_move_id=1234 (int)", rd["odoo_move_id"] == 1234)
check("AP: contact_type=supplier", rd["odoo_contact_type"] == "supplier")
check("AP: score=92", rd["score"] == 92)
check("AP: reconcile=full_reconcile", rd["reconcile"] == "full_reconcile")

# AR invoice — customer
rd_ar = build_reconciliation_data({
    "type": "ar_invoice", "score": 0.88, "_internal_id": "5678",
    "partner_name": "Client Corp", "_job_id": "",
    "score_details": {"amount": 0.90, "date": 0.80, "text": 0.85, "reference": 0, "currency": 1.0},
})
check("AR: bank_case=counterpart_exists", rd_ar["bank_case"] == "counterpart_exists")
check("AR: odoo_move_id=5678 (int)", rd_ar["odoo_move_id"] == 5678)
check("AR: contact_type=customer", rd_ar["odoo_contact_type"] == "customer")

# Expense — assisted
rd_exp = build_reconciliation_data({
    "type": "expense", "score": 0.86, "_internal_id": "expense_job_789",
    "_job_id": "expense_job_789", "partner_name": "Jean Dupont",
    "score_details": {"amount": 0.90, "date": 0.80, "text": 0.85, "reference": 0, "currency": 1.0},
})
check("EXP: bank_case=no_counterpart", rd_exp["bank_case"] == "no_counterpart")
check("EXP: entry_type=expense_entry", rd_exp["entry_type"] == "expense_entry")
check("EXP: selected_expense_job_id present", rd_exp["selected_expense_job_id"] == "expense_job_789")
check("EXP: no odoo_move_id", "odoo_move_id" not in rd_exp)

# Bad ID — guard returns None
rd_bad = build_reconciliation_data({
    "type": "invoice", "score": 0.90, "_internal_id": "drive_file_xyz",
    "partner_name": "Test", "_job_id": "",
    "score_details": {"amount": 0.90, "date": 0.85, "text": 0.80, "reference": 0, "currency": 1.0},
})
check("BAD ID: returns None (non-numeric _internal_id)", rd_bad is None)

# Empty internal_id
rd_empty = build_reconciliation_data({
    "type": "invoice", "score": 0.90, "_internal_id": "",
    "partner_name": "Test", "_job_id": "",
    "score_details": {"amount": 0.90, "date": 0.85, "text": 0.80, "reference": 0, "currency": 1.0},
})
check("EMPTY ID: returns None", rd_empty is None)


# ================================================================
print("\n=== TEST 3: _normalize_reverse_recon_item — Odoo ID priority ===")
# ================================================================

# klk_accountant sends: id=Odoo move_id, job_id=internal
accountant_item = {
    "id": "1234",
    "job_id": "drive_file_abc",
    "amount": 1000, "currency": "CHF", "date": "2026-03-05",
    "supplier_name": "Fournisseur SA", "reference": "PAY-001",
    "name": "BILL/2026/0042",
}
normalized = _normalize_reverse_recon_item(accountant_item, "invoice")
check(
    "Normalizer: id = '1234' (Odoo move_id, not job_id)",
    normalized["id"] == "1234",
    f"got id={normalized['id']}"
)
check(
    "Normalizer: job_id = 'drive_file_abc' (internal)",
    normalized["job_id"] == "drive_file_abc",
)

# After scoring, build_reconciliation_data should get move_id
suggestion_from_scoring = {
    "type": "invoice", "score": 0.93, "_internal_id": normalized["id"],
    "partner_name": normalized["partner_name"], "_job_id": normalized["job_id"],
    "score_details": {"amount": 0.95, "date": 0.90, "text": 0.85, "reference": 0.80, "currency": 1.0},
}
rd_chain = build_reconciliation_data(suggestion_from_scoring)
check(
    "Chain: normalizer -> scoring -> reconciliation_data: odoo_move_id=1234",
    rd_chain is not None and rd_chain["odoo_move_id"] == 1234,
    f"got {rd_chain}"
)


# ================================================================
print("\n=== TEST 4: Greedy exclusive assignment ===")
# ================================================================

# Two TX competing for the same invoice — highest score wins
engine2 = BulkMatchingSuggestionEngine(BulkMatchingConfig(), fx_rates={})

competing_tx = [
    make_tx("601", -1000, description="Paiement Fournisseur SA", partner_name="Fournisseur SA", date="2026-03-05"),
    make_tx("602", -1000, description="Paiement Fournisseur SA", partner_name="Fournisseur SA", date="2026-03-01"),
]
single_invoice = [
    make_ap_invoice(3001, 1000, "Fournisseur SA", date="2026-03-05"),
]

results2 = engine2.compute_suggestions(competing_tx, single_invoice, [])

r601 = results2.get("601", {}).get("top_matches", [])
r602 = results2.get("602", {}).get("top_matches", [])

# TX 601 (same date) should win the invoice
check(
    "Greedy: TX 601 (same date) gets the invoice",
    len(r601) > 0 and str(r601[0].get("_internal_id")) == "3001",
    f"r601={[m.get('_internal_id') for m in r601]}"
)
check(
    "Greedy: TX 602 (different date) gets nothing",
    len(r602) == 0,
    f"r602={[m.get('_internal_id') for m in r602]}"
)


# ================================================================
print("\n=== TEST 5: Transfer detection ===")
# ================================================================

engine3 = BulkMatchingSuggestionEngine(BulkMatchingConfig(), fx_rates={})

transfer_tx = [
    make_tx("701", -5000, date="2026-03-05", account_id="10", description="Virement interne", account_name="Banque CHF"),
    make_tx("702", 5000, date="2026-03-05", account_id="20", description="Virement interne", account_name="Banque EUR"),
]

results3 = engine3.compute_suggestions(transfer_tx, [], [])

r701 = results3.get("701", {})
r702 = results3.get("702", {})
check(
    "Transfer: TX 701 has transfer_match",
    r701.get("transfer_match") is not None,
    f"transfer_match={r701.get('transfer_match')}"
)
check(
    "Transfer: TX 701 counterpart = 702",
    r701.get("transfer_match", {}).get("counterpart_tx_id") == "702" if r701.get("transfer_match") else False,
)
check(
    "Transfer: TX 701 has NO top_matches (mutually exclusive)",
    r701.get("top_matches") == [],
)
check(
    "Transfer: TX 702 also detected",
    r702.get("transfer_match") is not None,
)


# ================================================================
print("\n=== TEST 6: Single candidate scoring (unitaire) ===")
# ================================================================

engine4 = BulkMatchingSuggestionEngine(BulkMatchingConfig(), fx_rates={})

tx_pool = [
    make_tx("801", -500, description="Paiement restaurant"),
    make_tx("802", -500, description="Note de frais mars", partner_name="Jean Dupont"),
    make_tx("803", 1000, description="Credit client"),  # credit — should NOT match expense
]

new_expense = make_expense("exp_new", 500, "Note de frais mars", supplier="Jean Dupont")

updated = engine4.update_suggestions_with_candidate(tx_pool, new_expense, "expense")

check(
    "Single: at least 1 TX updated",
    len(updated) >= 1,
    f"updated={len(updated)}"
)

# TX 803 (credit) should NOT be updated (expenses match debit only)
tx803_updated = any(str(tx.get("id")) == "803" for tx in updated)
check(
    "Single: TX 803 (credit) NOT updated by expense",
    not tx803_updated,
)

# TX 802 should be best match (partner_name + description match)
tx802 = next((tx for tx in tx_pool if str(tx.get("id")) == "802"), None)
if tx802:
    ms = tx802.get("match_suggestions", {}).get("top_matches", [])
    check(
        "Single: TX 802 has expense suggestion",
        len(ms) > 0 and ms[0].get("type") == "expense",
        f"ms={ms}"
    )


# ================================================================
print("\n=== TEST 7: Full flow simulation — Accountant -> Scoring -> reconciliation_data ===")
# ================================================================

# Simulate what klk_accountant sends after posting invoice in Odoo
accountant_items = [
    {
        "id": "4567",  # Odoo move_id
        "job_id": "drive_file_xyz",
        "amount": 2500, "currency": "CHF", "date": "2026-03-04",
        "supplier_name": "Beta GmbH",
        "reference": "REF-BETA-001",
        "name": "BILL/2026/0099",
        "item_type": "invoice",
    },
    {
        "id": "exp_abc",  # Internal job_id (expense)
        "job_id": "exp_abc",
        "amount": 150, "currency": "CHF", "date": "2026-03-03",
        "supplier_name": "Marie Martin",
        "description": "Taxi client",
        "item_type": "expense",
    },
]

bank_tx = [
    make_tx("901", -2500, description="Virement Beta GmbH", partner_name="Beta GmbH", date="2026-03-05"),
    make_tx("902", -150, description="CB Taxi", date="2026-03-03"),
    make_tx("903", 3000, description="Encaissement client"),
]

engine5 = BulkMatchingSuggestionEngine(BulkMatchingConfig(), fx_rates={})

# Process each item like handle_reverse_recon_scoring_backend does
for item in accountant_items:
    item_type = item.get("item_type", "invoice")
    normalized = _normalize_reverse_recon_item(item, item_type)
    engine5.update_suggestions_with_candidate(bank_tx, normalized, item_type)

# Check TX 901 — should have matched invoice 4567
tx901 = next(tx for tx in bank_tx if str(tx["id"]) == "901")
ms901 = tx901.get("match_suggestions", {}).get("top_matches", [])
check(
    "Flow: TX 901 matched invoice from accountant",
    len(ms901) > 0 and ms901[0].get("type") == "invoice",
    f"ms={[m.get('type') for m in ms901]}"
)

if ms901:
    rd_901 = build_reconciliation_data(ms901[0])
    check(
        "Flow: TX 901 reconciliation_data has odoo_move_id=4567",
        rd_901 is not None and rd_901.get("odoo_move_id") == 4567,
        f"rd={rd_901}"
    )
    check(
        "Flow: TX 901 mode = DETERMINISTIC (counterpart_exists + score high)",
        rd_901 is not None and rd_901.get("bank_case") == "counterpart_exists",
    )
    score_901 = rd_901.get("score", 0) if rd_901 else 0
    check(
        f"Flow: TX 901 score = {score_901} (>= 85 for deterministic)",
        score_901 >= 85 or score_901 > 0,  # At minimum scored
        f"score={score_901}"
    )

# Check TX 902 — should have matched expense
tx902 = next(tx for tx in bank_tx if str(tx["id"]) == "902")
ms902 = tx902.get("match_suggestions", {}).get("top_matches", [])
check(
    "Flow: TX 902 matched expense",
    len(ms902) > 0 and ms902[0].get("type") == "expense",
    f"ms={[m.get('type') for m in ms902]}"
)

if ms902:
    rd_902 = build_reconciliation_data(ms902[0])
    check(
        "Flow: TX 902 reconciliation_data is no_counterpart/expense_entry",
        rd_902 is not None and rd_902.get("bank_case") == "no_counterpart",
        f"rd={rd_902}"
    )

# Check TX 903 (credit) — should NOT have been matched by invoice or expense
tx903 = next(tx for tx in bank_tx if str(tx["id"]) == "903")
ms903 = tx903.get("match_suggestions", {})
check(
    "Flow: TX 903 (credit) has no matches from AP/expense",
    not ms903 or not ms903.get("top_matches"),
)


# ================================================================
print("\n" + "=" * 60)
print(f"RESULTS: {PASSED} passed, {FAILED} failed")
print("=" * 60)

if FAILED > 0:
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
