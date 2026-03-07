"""
BulkMatchingSuggestionEngine - Moteur de scoring bulk deterministe.

Port lightweight du ReverseScoringEngine de klk_bank/tools/reverse_reconciliation.py,
adapte pour tourner cote backend sans agent IA.

Differences vs reverse_reconciliation.py:
- Pas d'agent IA (scoring deterministe uniquement)
- Pas de Redis/Firestore direct (donnees pre-chargees)
- Output = suggestions attachees aux TX (pas de decisions)
- Optimise pour batch (amount bucket index)
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .fx_rate_service import get_fx_rate_for_date, normalize_currency

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BulkMatchingConfig:
    # Weights for invoice scoring
    invoice_weights: Dict[str, float] = field(default_factory=lambda: {
        "amount": 0.25,
        "date": 0.25,
        "text": 0.25,
        "reference": 0.15,
        "currency": 0.10,
    })
    # Weights for expense scoring
    expense_weights: Dict[str, float] = field(default_factory=lambda: {
        "amount": 0.30,
        "date": 0.30,
        "text": 0.25,
        "reference": 0.00,
        "currency": 0.15,
    })
    # Thresholds
    min_display_threshold: float = 0.35
    high_confidence: float = 0.85
    medium_confidence: float = 0.50
    max_suggestions: int = 3
    # Amount tolerances
    amount_tolerance_native: float = 0.05   # 5%
    amount_tolerance_fx: float = 0.10       # 10%
    # Amount bucket width (for O(1) candidate lookup)
    bucket_pct: float = 0.20               # 20% bucket range
    # Transfer matching
    transfer_date_tolerance_days: int = 3
    transfer_amount_tolerance: float = 0.02  # 2%


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class BulkMatchingSuggestionEngine:

    def __init__(self, config: BulkMatchingConfig, fx_rates: Dict[str, Dict[str, float]]):
        self.config = config
        self.fx_rates = fx_rates

    def compute_suggestions(
        self,
        transactions: List[Dict],
        ap_invoices: List[Dict],
        expenses: List[Dict],
        ar_invoices: Optional[List[Dict]] = None,
    ) -> Dict[str, Dict]:
        """
        Score toutes les transactions to_process contre les factures AP, AR et expenses.

        - TX débit (sortie d'argent) → matchées contre AP invoices + expenses
        - TX crédit (entrée d'argent) → matchées contre AR invoices (factures clients)

        Returns: {
            tx_id: {
                "top_matches": [...max 3, sorted by score desc],
                "transfer_match": {...} or None,
                "scored_at": ISO timestamp
            }
        }
        """
        # Build indexes
        inv_index = self._build_amount_index(ap_invoices, "amount")
        exp_index = self._build_amount_index(expenses, "amount")
        ar_index = self._build_amount_index(ar_invoices or [], "amount")
        transfer_index = self._build_transfer_index(transactions)

        # ── Phase 1: Score all TX↔candidate pairs ──
        # Collect ALL (tx_id, candidate_key, score, suggestion) tuples
        all_pairs: List[Tuple[str, str, float, Dict]] = []

        for tx in transactions:
            tx_id_str = str(tx.get("id", ""))
            tx_amount_raw = float(tx.get("amount", 0) or 0)
            tx_amount = abs(tx_amount_raw)
            tx_currency = normalize_currency(tx.get("currency", "CHF"))
            tx_is_debit = tx_amount_raw < 0

            # Skip scoring if transfer detected (mutually exclusive)
            if transfer_index.get(tx_id_str):
                continue

            if tx_is_debit:
                for inv in self._get_bucket_candidates(tx_amount, inv_index):
                    score, details = self._score_pair(tx, inv, "invoice")
                    if score >= self.config.min_display_threshold and details["amount"] >= 0.40:
                        suggestion = self._build_suggestion(inv, score, details, "invoice", tx_currency)
                        # Unique key: type + internal_id (or id)
                        cand_key = f"invoice:{suggestion.get('_internal_id') or suggestion['id']}"
                        all_pairs.append((tx_id_str, cand_key, score, suggestion))

                for exp in self._get_bucket_candidates(tx_amount, exp_index):
                    score, details = self._score_pair(tx, exp, "expense")
                    if score >= self.config.min_display_threshold and details["amount"] >= 0.40:
                        suggestion = self._build_suggestion(exp, score, details, "expense", tx_currency)
                        cand_key = f"expense:{suggestion.get('_internal_id') or suggestion['id']}"
                        all_pairs.append((tx_id_str, cand_key, score, suggestion))
            else:
                # Credit TX (inflow) → match against AR invoices (customer payments)
                for ar in self._get_bucket_candidates(tx_amount, ar_index):
                    score, details = self._score_pair(tx, ar, "invoice")
                    if score >= self.config.min_display_threshold and details["amount"] >= 0.40:
                        suggestion = self._build_suggestion(ar, score, details, "ar_invoice", tx_currency)
                        cand_key = f"ar_invoice:{suggestion.get('_internal_id') or suggestion['id']}"
                        all_pairs.append((tx_id_str, cand_key, score, suggestion))

        # ── Phase 2: Greedy exclusive assignment ──
        # Sort by score descending → best match wins the candidate
        all_pairs.sort(key=lambda p: p[2], reverse=True)

        # tx_id → list of assigned suggestions
        tx_matches: Dict[str, List[Dict]] = {}
        # candidate keys already claimed by a transaction
        claimed: set = set()

        for tx_id, cand_key, score, suggestion in all_pairs:
            # Skip if candidate already assigned to a higher-scoring TX
            if cand_key in claimed:
                continue
            # Skip if this TX already has max suggestions
            current = tx_matches.get(tx_id, [])
            if len(current) >= self.config.max_suggestions:
                continue

            tx_matches.setdefault(tx_id, []).append(suggestion)
            claimed.add(cand_key)

        # ── Phase 3: Build results ──
        results = {}
        scored_at = datetime.utcnow().isoformat() + "Z"
        for tx in transactions:
            tx_id_str = str(tx.get("id", ""))
            transfer = transfer_index.get(tx_id_str)
            matches = tx_matches.get(tx_id_str, [])

            results[tx_id_str] = {
                "top_matches": [] if transfer else matches,
                "transfer_match": transfer,
                "scored_at": scored_at,
            }

        return results

    # ------------------------------------------------------------------
    # Amount bucket index
    # ------------------------------------------------------------------

    def _build_amount_index(self, items: List[Dict], amount_key: str) -> Dict[int, List[Dict]]:
        """
        Index items by amount bucket for O(1) candidate lookup.
        Bucket key = int(abs(amount) / bucket_width).
        """
        index: Dict[int, List[Dict]] = {}
        for item in items:
            amt = abs(float(item.get(amount_key, 0) or 0))
            if amt == 0:
                continue
            bucket = self._amount_bucket(amt)
            for b in (bucket - 1, bucket, bucket + 1):  # check adjacent buckets
                index.setdefault(b, [])
            index.setdefault(bucket, []).append(item)
        return index

    def _amount_bucket(self, amount: float) -> int:
        bucket_width = max(amount * self.config.bucket_pct, 1.0)
        return int(amount / bucket_width)

    def _get_bucket_candidates(self, tx_amount: float, index: Dict[int, List[Dict]]) -> List[Dict]:
        if tx_amount == 0:
            return []
        bucket = self._amount_bucket(tx_amount)
        seen_ids = set()
        candidates = []
        for b in (bucket - 1, bucket, bucket + 1):
            for item in index.get(b, []):
                item_id = id(item)
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    candidates.append(item)
        return candidates

    # ------------------------------------------------------------------
    # Transfer index (tx-to-tx matching)
    # ------------------------------------------------------------------

    def _build_transfer_index(self, transactions: List[Dict]) -> Dict[str, Dict]:
        """
        Detecte les transferts inter-comptes avec scoring multi-criteres:

        Criteres obligatoires (gates):
        - Signes opposes
        - Journaux differents
        - Date +-3 jours

        Scoring (pondere):
        - 40% Montant (apres conversion FX si needed)
        - 30% Date (meme jour=1.0, +-1j=0.85, +-2j=0.60, +-3j=0.40)
        - 30% Texte (similarite description — filtre les faux positifs)

        Seuil minimum: 0.70 (strict pour eviter les faux positifs)
        """
        index: Dict[str, Dict] = {}
        n = len(transactions)
        if n > 500:
            return index  # skip for very large sets (perf)

        TRANSFER_MIN_SCORE = 0.70

        for i in range(n):
            tx_a = transactions[i]
            amt_a = float(tx_a.get("amount", 0) or 0)
            if amt_a == 0:
                continue
            cur_a = normalize_currency(tx_a.get("currency", "CHF"))

            for j in range(i + 1, n):
                tx_b = transactions[j]
                amt_b = float(tx_b.get("amount", 0) or 0)
                if amt_b == 0:
                    continue

                # Gate 1: Opposite signs
                if amt_a * amt_b >= 0:
                    continue

                # Gate 2: Different journals
                if tx_a.get("account_id") == tx_b.get("account_id"):
                    continue

                # Gate 3: Date proximity (hard limit)
                date_a = _parse_date(tx_a.get("date"))
                date_b = _parse_date(tx_b.get("date"))
                if date_a and date_b:
                    day_diff = abs((date_a - date_b).days)
                    if day_diff > self.config.transfer_date_tolerance_days:
                        continue
                else:
                    day_diff = 99  # unknown dates → low date score

                cur_b = normalize_currency(tx_b.get("currency", "CHF"))
                abs_a = abs(amt_a)
                abs_b = abs(amt_b)
                is_fx = cur_a != cur_b

                # --- Amount score (40%) ---
                compare_a = abs_a
                compare_b = abs_b
                if is_fx and self.fx_rates:
                    ref_date = tx_a.get("date", "")
                    rate = get_fx_rate_for_date(self.fx_rates, cur_b, ref_date)
                    if rate and rate > 0:
                        compare_b = abs_b / rate
                    else:
                        rate_rev = get_fx_rate_for_date(self.fx_rates, cur_a, ref_date)
                        if rate_rev and rate_rev > 0:
                            compare_a = abs_a / rate_rev
                        else:
                            continue  # No FX rate → skip

                diff = abs(compare_a - compare_b) / max(compare_a, compare_b)
                tolerance = 0.05 if is_fx else self.config.transfer_amount_tolerance
                if diff > tolerance:
                    continue

                amount_score = 1.0 - diff
                if is_fx:
                    amount_score *= 0.90  # small FX penalty

                # --- Date score (30%) ---
                if day_diff == 0:
                    date_score = 1.0
                elif day_diff == 1:
                    date_score = 0.85
                elif day_diff == 2:
                    date_score = 0.60
                elif day_diff <= 3:
                    date_score = 0.40
                else:
                    date_score = 0.10

                # --- Text score (30%) ---
                text_a = _build_tx_text(tx_a)
                text_b = _build_tx_text(tx_b)
                text_score = _text_similarity(text_a, text_b)

                # Composite score
                score = (
                    0.40 * amount_score +
                    0.30 * date_score +
                    0.30 * text_score
                )

                if score < TRANSFER_MIN_SCORE:
                    continue

                id_a = str(tx_a.get("id", ""))
                id_b = str(tx_b.get("id", ""))
                if id_a and id_b:
                    if id_a not in index or index[id_a]["score"] < score:
                        index[id_a] = {
                            "counterpart_tx_id": id_b,
                            "counterpart_journal": tx_b.get("account_name", ""),
                            "counterpart_amount": amt_b,
                            "counterpart_currency": cur_b,
                            "counterpart_date": tx_b.get("date", ""),
                            "counterpart_description": tx_b.get("description", ""),
                            "is_fx": is_fx,
                            "score": round(score, 3),
                        }
                    if id_b not in index or index[id_b]["score"] < score:
                        index[id_b] = {
                            "counterpart_tx_id": id_a,
                            "counterpart_journal": tx_a.get("account_name", ""),
                            "counterpart_amount": amt_a,
                            "counterpart_currency": cur_a,
                            "counterpart_date": tx_a.get("date", ""),
                            "counterpart_description": tx_a.get("description", ""),
                            "is_fx": is_fx,
                            "score": round(score, 3),
                        }

        return index

    # ------------------------------------------------------------------
    # Single-candidate scoring (triggered when Router/AP completes a job)
    # ------------------------------------------------------------------

    def score_single_candidate(
        self,
        candidate: Dict,
        candidate_type: str,
        transactions: List[Dict],
    ) -> Dict[str, Dict]:
        """
        Score ONE new invoice/expense against all to_process transactions.

        Returns: {
            tx_id: {
                "suggestion": {...},   # MatchSuggestion or None
                "score": float,
            }
        }
        """
        results: Dict[str, Dict] = {}
        # Determine which TX sign this candidate type matches
        # AR invoices (customer) → credit TX (positive), everything else → debit TX (negative)
        match_credit = candidate_type == "ar_invoice"

        for tx in transactions:
            tx_id_str = str(tx.get("id", ""))
            tx_amount_raw = float(tx.get("amount", 0) or 0)
            tx_amount = abs(tx_amount_raw)
            tx_currency = normalize_currency(tx.get("currency", "CHF"))
            tx_is_debit = tx_amount_raw < 0

            # Filter TX by sign: debit for AP/expenses, credit for AR
            if match_credit and tx_is_debit:
                continue
            if not match_credit and not tx_is_debit:
                continue

            # AR invoices use invoice scoring weights
            scoring_type = "invoice" if candidate_type in ("invoice", "ar_invoice") else candidate_type
            score, details = self._score_pair(tx, candidate, scoring_type)
            if score >= self.config.min_display_threshold and details["amount"] >= 0.40:
                suggestion = self._build_suggestion(candidate, score, details, candidate_type, tx_currency)
                results[tx_id_str] = {"suggestion": suggestion, "score": score}

        return results

    def update_suggestions_with_candidate(
        self,
        transactions: List[Dict],
        candidate: Dict,
        candidate_type: str,
    ) -> List[Dict]:
        """
        Score a single candidate against all TX and update their match_suggestions
        in-place. Replaces existing suggestion if the new score is higher.

        Returns list of tx dicts that were updated (for WSS push).
        """
        scores = self.score_single_candidate(candidate, candidate_type, transactions)
        updated_txs: List[Dict] = []

        for tx in transactions:
            tx_id_str = str(tx.get("id", ""))
            result = scores.get(tx_id_str)
            if not result:
                continue

            new_suggestion = result["suggestion"]
            new_score = result["score"]
            cand_key = f"{candidate_type}:{new_suggestion.get('_internal_id') or new_suggestion['id']}"

            # Get or init existing suggestions
            existing = tx.get("match_suggestions") or {
                "top_matches": [], "transfer_match": None, "scored_at": None
            }
            top_matches: List[Dict] = list(existing.get("top_matches") or [])

            # Check if this candidate already exists in top_matches
            replaced = False
            for i, m in enumerate(top_matches):
                existing_key = f"{m.get('type', '')}:{m.get('_internal_id') or m.get('id', '')}"
                if existing_key == cand_key:
                    if new_score > m.get("score", 0):
                        top_matches[i] = new_suggestion
                        replaced = True
                    break
            else:
                # Candidate not in list — add if better than worst or list not full
                if len(top_matches) < self.config.max_suggestions:
                    top_matches.append(new_suggestion)
                    replaced = True
                elif top_matches and new_score > min(m.get("score", 0) for m in top_matches):
                    # Replace the weakest
                    worst_idx = min(range(len(top_matches)), key=lambda i: top_matches[i].get("score", 0))
                    top_matches[worst_idx] = new_suggestion
                    replaced = True

            if replaced:
                # Re-sort by score desc
                top_matches.sort(key=lambda m: m.get("score", 0), reverse=True)
                existing["top_matches"] = top_matches
                existing["scored_at"] = datetime.utcnow().isoformat() + "Z"
                tx["match_suggestions"] = existing
                updated_txs.append(tx)

        return updated_txs

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_pair(
        self, tx: Dict, candidate: Dict, candidate_type: str
    ) -> Tuple[float, Dict[str, float]]:
        """Score a transaction against a candidate (invoice or expense)."""
        weights = (
            self.config.invoice_weights
            if candidate_type == "invoice"
            else self.config.expense_weights
        )

        tx_amount = abs(float(tx.get("amount", 0) or 0))
        tx_currency = normalize_currency(tx.get("currency", "CHF"))
        tx_date = _parse_date(tx.get("date"))

        # Extract candidate fields (already normalized by helpers)
        cand_amount = abs(float(candidate.get("amount", 0) or 0))
        cand_currency = normalize_currency(candidate.get("currency", "CHF"))

        if candidate_type == "invoice":
            cand_date = _parse_date(
                candidate.get("invoice_date") or candidate.get("date")
            )
            cand_text = candidate.get("partner_name", "")
            cand_ref = candidate.get("ref", "") or candidate.get("payment_reference", "") or candidate.get("name", "")
        else:  # expense
            cand_date = _parse_date(
                candidate.get("expense_date") or candidate.get("date")
            )
            cand_text = candidate.get("description", "") or candidate.get("label", "")
            cand_ref = ""

        # Skip if candidate has no amount (bad data)
        if cand_amount == 0:
            return 0.0, {"amount": 0, "date": 0, "text": 0, "reference": 0, "currency": 0}

        # Currency score
        is_fx = False
        if tx_currency == cand_currency:
            currency_score = 1.0
        elif tx_currency in ("CHF", "EUR", "USD", "GBP") and cand_currency in ("CHF", "EUR", "USD", "GBP"):
            currency_score = 0.7
            is_fx = True
        else:
            currency_score = 0.5
            is_fx = True

        # FX conversion if needed
        compare_tx_amount = tx_amount
        compare_cand_amount = cand_amount
        fx_rate = None
        if is_fx and self.fx_rates:
            tx_date_str = tx.get("date", "")
            rate = get_fx_rate_for_date(self.fx_rates, cand_currency, tx_date_str)
            if rate and rate > 0:
                fx_rate = rate
                # Convert candidate to tx currency
                compare_cand_amount = cand_amount / rate
                currency_score = 0.7

        # Amount score
        tolerance = self.config.amount_tolerance_fx if is_fx else self.config.amount_tolerance_native
        amount_score = _score_amount(compare_tx_amount, compare_cand_amount, tolerance, is_fx)

        # Date score
        is_expense = candidate_type == "expense"
        date_score = _score_date(tx_date, cand_date, is_expense=is_expense)

        # Text score
        tx_text = _build_tx_text(tx)
        text_score = _text_similarity(tx_text, cand_text)

        # Reference score
        ref_score = 0.0
        if cand_ref:
            ref_score = _text_similarity(
                tx.get("reference", "") + " " + tx.get("payment_ref", ""),
                cand_ref,
            )

        # Adjust weights if FX
        w = dict(weights)
        if is_fx:
            reduction = w["amount"] * 0.15
            w["amount"] -= reduction
            w["date"] += reduction / 2
            w["text"] += reduction / 2

        # Weighted total
        total = (
            w["amount"] * amount_score
            + w["date"] * date_score
            + w["text"] * text_score
            + w["reference"] * ref_score
            + w["currency"] * currency_score
        )

        details = {
            "amount": round(amount_score, 3),
            "date": round(date_score, 3),
            "text": round(text_score, 3),
            "reference": round(ref_score, 3),
            "currency": round(currency_score, 3),
        }

        return round(total, 3), details

    # ------------------------------------------------------------------
    # Build suggestion output
    # ------------------------------------------------------------------

    def _build_suggestion(
        self,
        candidate: Dict,
        score: float,
        score_details: Dict[str, float],
        candidate_type: str,
        tx_currency: str,
    ) -> Dict[str, Any]:
        # Use display_* fields (set by cache helpers) for human-readable info
        # Fall back to raw fields for backward compat
        amount = float(candidate.get("display_amount") or candidate.get("amount", 0) or 0)
        currency = normalize_currency(candidate.get("currency", "CHF"))
        date = candidate.get("display_date") or candidate.get("date") or ""

        if candidate_type == "invoice":
            # Display: supplier name + invoice ref
            display_label = candidate.get("display_name") or candidate.get("partner_name") or ""
            display_ref = candidate.get("display_ref") or candidate.get("name") or ""
        else:
            # Display: expense title/concern + supplier
            display_label = candidate.get("display_name") or candidate.get("description") or ""
            display_ref = candidate.get("display_ref") or candidate.get("employee_name") or ""

        # Confidence zone
        if score >= self.config.high_confidence:
            confidence = "HIGH"
        elif score >= self.config.medium_confidence:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # FX conversion info
        fx_rate = None
        fx_converted = None
        if currency != tx_currency and self.fx_rates:
            rate = get_fx_rate_for_date(self.fx_rates, currency, str(date))
            if rate and rate > 0:
                fx_rate = round(rate, 6)
                fx_converted = round(amount / rate, 2)

        return {
            "type": candidate_type,
            # id = human-readable label (supplier name or expense title)
            "id": str(display_ref) if display_ref else str(display_label),
            "partner_name": str(display_label),
            "amount": amount,
            "currency": currency,
            "date": str(date),
            "score": score,
            "confidence": confidence,
            "score_details": score_details,
            "fx_rate": fx_rate,
            "fx_converted_amount": fx_converted,
            # Technical IDs (hidden in UI, used for reconciliation payload)
            "_internal_id": candidate.get("id") or candidate.get("job_id") or "",
            "_job_id": candidate.get("job_id") or "",
            "_file_id": candidate.get("file_id") or "",
        }


# ---------------------------------------------------------------------------
# Reconciliation data builder (for klk_bank contrat)
# ---------------------------------------------------------------------------

def build_reconciliation_data(suggestion: Dict, deterministic_threshold: int = 85) -> Optional[Dict]:
    """
    Build `reconciliation_data` dict from a BulkMatchingSuggestionEngine suggestion,
    conforming to the klk_bank CONTRAT_RECONCILIATION_DATA.

    Returns None if the suggestion doesn't have enough data.

    Rules:
    - invoice (AP): bank_case=counterpart_exists, odoo_move_id from ERP, contact_type=supplier
    - ar_invoice (AR): bank_case=counterpart_exists, odoo_move_id from ERP, contact_type=customer
    - expense: bank_case=no_counterpart, entry_type=expense_entry, selected_expense_job_id
    """
    stype = suggestion.get("type", "")
    score_float = suggestion.get("score", 0)
    score_int = int(round(score_float * 100))
    internal_id = suggestion.get("_internal_id", "")
    partner_name = suggestion.get("partner_name", "")
    score_details = suggestion.get("score_details", {})

    reasoning = (
        f"Auto-scoring backend: amount={score_details.get('amount', 0):.0%}, "
        f"date={score_details.get('date', 0):.0%}, "
        f"text={score_details.get('text', 0):.0%}, "
        f"ref={score_details.get('reference', 0):.0%}, "
        f"currency={score_details.get('currency', 0):.0%}"
    )

    if stype in ("invoice", "ar_invoice"):
        # AP or AR invoice — counterpart_exists — IDs are Odoo move_id (from ERP query)
        odoo_move_id = None
        if internal_id:
            try:
                odoo_move_id = int(internal_id)
            except (ValueError, TypeError):
                pass

        if not odoo_move_id:
            return None  # Can't build deterministic data without Odoo move_id

        contact_type = "customer" if stype == "ar_invoice" else "supplier"

        return {
            "score": score_int,
            "bank_case": "counterpart_exists",
            "odoo_partner_name": partner_name,
            "odoo_move_id": odoo_move_id,
            "odoo_contact_type": contact_type,
            "reconcile": "full_reconcile",
            "reasoning": reasoning,
        }

    elif stype == "expense":
        # Expense — no_counterpart — IDs are internal job_id (NOT Odoo)
        job_id = suggestion.get("_job_id", "") or str(internal_id)
        return {
            "score": score_int,
            "bank_case": "no_counterpart",
            "entry_type": "expense_entry",
            "selected_expense_job_id": job_id,
            "odoo_partner_name": partner_name,
            "reasoning": reasoning,
        }

    return None


# ---------------------------------------------------------------------------
# Scoring helper functions (standalone, no state)
# ---------------------------------------------------------------------------

def _score_amount(expected: float, actual: float, tolerance: float = 0.05, is_fx: bool = False) -> float:
    if expected == 0 or actual == 0:
        return 0.0
    diff_ratio = abs(expected - actual) / max(abs(expected), abs(actual))
    fx_tolerance = 0.10 if is_fx else tolerance
    if diff_ratio <= 0.02:
        return 0.95
    elif diff_ratio <= tolerance:
        return 0.85
    elif diff_ratio <= fx_tolerance:
        return 0.70 if is_fx else 0.60
    elif diff_ratio <= 0.15 and is_fx:
        return 0.55
    elif diff_ratio <= 0.20:
        return 0.40
    return 0.0


def _score_date(
    date1: Optional[datetime],
    date2: Optional[datetime],
    is_expense: bool = False,
) -> float:
    if not date1 or not date2:
        return 0.25
    delta_days = abs((date1 - date2).days)
    if is_expense:
        if delta_days <= 1:
            return 1.0
        elif delta_days <= 3:
            return 0.90
        elif delta_days <= 7:
            return 0.75
        return 0.0
    else:
        if delta_days == 0:
            return 1.0
        elif delta_days <= 3:
            return 0.85
        elif delta_days <= 7:
            return 0.70
        elif delta_days <= 30:
            return 0.50
        return 0.25


def _text_similarity(text1: str, text2: str) -> float:
    if not text1 or not text2:
        return 0.0
    t1 = text1.lower().strip()
    t2 = text2.lower().strip()
    if t1 in t2 or t2 in t1:
        return 1.0
    return SequenceMatcher(None, t1, t2).ratio()


def _build_tx_text(tx: Dict) -> str:
    parts = [
        tx.get("reference", ""),
        tx.get("payment_ref", ""),
        tx.get("partner_name", ""),
        tx.get("description", ""),
    ]
    return " ".join(str(p) for p in parts if p)


def _parse_date(date_str) -> Optional[datetime]:
    if not date_str:
        return None
    if isinstance(date_str, datetime):
        return date_str
    s = str(date_str).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
