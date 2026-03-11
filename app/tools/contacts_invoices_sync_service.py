"""
ContactsInvoicesSyncService — Synchronisation Contacts + Factures depuis ERP vers Neon.

Pattern identique à accounting_sync_service.py :
1. Résolution credentials ERP via mandate_path
2. Connexion Odoo (XML-RPC)
3. Fetch res.partner + account.move + account.move.line
4. Transformation + hash SHA-256
5. Persistence via NeonContactsInvoicesManager (incremental upsert)
6. Refresh aging snapshot

Déclenché par:
- RPC INVOICES.trigger_sync (frontend ou admin)
- Cron toutes les 15 minutes (incrémental)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# ─── Champs Odoo à fetcher ───────────────────────────────────────
# Les champs sont désormais gérés via OdooModelManager (erp_manager.py)
# avec adaptation automatique selon la version Odoo (ex: 'origin' n'existe plus en v18+).
# Modèles utilisés: res.partner, account.move, account.move.line.sync


def _get_contact_fields(erp_connection) -> list:
    """Get res.partner fields adapted to Odoo version."""
    if hasattr(erp_connection, 'model_manager'):
        return erp_connection.model_manager.get_fields_for_model('res.partner')
    # Fallback hardcodé si pas de model_manager
    return [
        "id", "name", "display_name", "is_company", "parent_id",
        "supplier_rank", "customer_rank",
        "email", "phone", "mobile", "website",
        "street", "street2", "city", "zip",
        "state_id", "country_id", "vat",
        "property_account_payable_id", "property_account_receivable_id",
        "property_payment_term_id", "property_supplier_payment_term_id",
        "write_date", "active",
    ]


def _get_invoice_fields(erp_connection) -> list:
    """Get account.move fields adapted to Odoo version."""
    if hasattr(erp_connection, 'model_manager'):
        return erp_connection.model_manager.get_fields_for_model('account.move')
    # Fallback hardcodé (v18+ safe — sans 'origin')
    return [
        "id", "name", "ref", "move_type", "partner_id",
        "invoice_date", "invoice_date_due", "date",
        "amount_untaxed", "amount_tax", "amount_total",
        "amount_residual", "amount_total_signed", "amount_residual_signed",
        "currency_id", "state", "payment_state", "journal_id", "write_date",
    ]


def _get_invoice_line_fields(erp_connection) -> list:
    """Get account.move.line fields for sync (version-adapted)."""
    if hasattr(erp_connection, 'model_manager'):
        return erp_connection.model_manager.get_fields_for_model('account.move.line.sync')
    # Fallback hardcodé
    return [
        "id", "move_id", "name", "quantity", "price_unit", "discount",
        "account_id", "price_subtotal", "price_total",
        "debit", "credit", "balance", "tax_ids",
        "product_id", "display_type",
    ]

SYNC_OVERLAP_MINUTES = 5

# Max retries for removing invalid fields one-by-one
_MAX_FIELD_RETRIES = 5


def _resilient_search_read(erp_connection, model: str, domain: list, fields: list, limit: int = 50000) -> list:
    """Execute search_read with automatic retry on 'Invalid field' errors.

    Some Odoo instances lack optional fields (e.g. 'mobile' without phone module,
    'origin' without sale module). This helper retries up to _MAX_FIELD_RETRIES times,
    removing the offending field each time.
    """
    import re
    remaining_fields = fields.copy()
    for attempt in range(_MAX_FIELD_RETRIES + 1):
        try:
            return erp_connection.execute_kw(
                model, "search_read",
                [domain],
                {"fields": remaining_fields, "limit": limit},
            ) or []
        except Exception as exc:
            err_msg = str(exc)
            if "Invalid field" not in err_msg or attempt >= _MAX_FIELD_RETRIES:
                raise
            # Extract field name from: Invalid field 'xxx' on model 'yyy'
            match = re.search(r"Invalid field \\?['\"](\w+)\\?['\"]", err_msg)
            if match:
                bad_field = match.group(1)
                if bad_field in remaining_fields:
                    remaining_fields.remove(bad_field)
                    logger.warning(
                        "[CONTACTS_INV_SYNC] %s: field '%s' not available, retrying without it",
                        model, bad_field,
                    )
                    continue
            raise
                    continue
            raise
    return []


# ─── Tuple extraction helpers ────────────────────────────────────

def _extract(value, index: int = 0):
    """Extract from Odoo tuple (id, name). Returns None if empty/False."""
    if isinstance(value, (list, tuple)) and len(value) > index:
        return value[index]
    if value is False:
        return None
    return value


def _safe_str(value, default: Optional[str] = None) -> Optional[str]:
    """Convert Odoo value to str, treating False as None."""
    if value is None or value is False:
        return default
    return str(value)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value is False:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _extract_account_number(account_tuple) -> Optional[str]:
    """Extract account number from Odoo (id, 'NNNN Label') tuple."""
    name = _extract(account_tuple, 1)
    if not name or not isinstance(name, str):
        return None
    # "2000 Payables" → "2000"
    parts = name.strip().split(" ", 1)
    return parts[0] if parts else None


# ─── Transform: Odoo → Neon format ──────────────────────────────

def _odoo_contact_to_neon(record: Dict[str, Any]) -> Dict[str, Any]:
    """Transform an Odoo res.partner record to erp_data.contacts format."""
    state_tuple = record.get("state_id")
    country_tuple = record.get("country_id")

    parent_raw = record.get("parent_id")
    parent_id = _extract(parent_raw, 0) if isinstance(parent_raw, (list, tuple)) else (parent_raw if parent_raw else None)

    return {
        "erp_partner_id": record.get("id"),
        "erp_type": "odoo",
        "name": _safe_str(record.get("name"), ""),
        "display_name": _safe_str(record.get("display_name")),
        "is_company": bool(record.get("is_company", True)),
        "parent_id": parent_id,
        "supplier_rank": record.get("supplier_rank", 0) or 0,
        "customer_rank": record.get("customer_rank", 0) or 0,
        "email": _safe_str(record.get("email")),
        "phone": _safe_str(record.get("phone")),
        "mobile": _safe_str(record.get("mobile")),
        "website": _safe_str(record.get("website")),
        "street": _safe_str(record.get("street")),
        "street2": _safe_str(record.get("street2")),
        "city": _safe_str(record.get("city")),
        "zip": _safe_str(record.get("zip")),
        "state_code": None,  # Resolved below
        "state_name": _safe_str(_extract(state_tuple, 1) if isinstance(state_tuple, (list, tuple)) else None),
        "country_code": None,  # Resolved below
        "country_name": _safe_str(_extract(country_tuple, 1) if isinstance(country_tuple, (list, tuple)) else None),
        "vat": _safe_str(record.get("vat")),
        "property_account_payable": _extract_account_number(record.get("property_account_payable_id")),
        "property_account_receivable": _extract_account_number(record.get("property_account_receivable_id")),
        "payment_term_supplier": _safe_str(_extract(record.get("property_supplier_payment_term_id"), 1) if isinstance(record.get("property_supplier_payment_term_id"), (list, tuple)) else None),
        "payment_term_customer": _safe_str(_extract(record.get("property_payment_term_id"), 1) if isinstance(record.get("property_payment_term_id"), (list, tuple)) else None),
        "is_active": bool(record.get("active", True)),
        "last_erp_write_date": _safe_str(record.get("write_date")),
    }


def _odoo_invoice_to_neon(record: Dict[str, Any], journal_id_to_code: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """Transform an Odoo account.move record to erp_data.invoices format."""
    currency_tuple = record.get("currency_id")
    journal_tuple = record.get("journal_id")
    partner_tuple = record.get("partner_id")

    journal_code = None
    if journal_id_to_code and isinstance(journal_tuple, (list, tuple)) and journal_tuple:
        journal_code = journal_id_to_code.get(int(journal_tuple[0]))
    journal_name = _extract(journal_tuple, 1) if isinstance(journal_tuple, (list, tuple)) else None

    return {
        "erp_move_id": record.get("id"),
        "erp_type": "odoo",
        "move_type": _safe_str(record.get("move_type"), "in_invoice"),
        "invoice_name": _safe_str(record.get("name"), ""),
        "invoice_ref": _safe_str(record.get("ref")),
        "invoice_origin": _safe_str(record.get("origin")),
        "erp_partner_id": _extract(partner_tuple, 0) if isinstance(partner_tuple, (list, tuple)) else partner_tuple,
        "partner_name": _safe_str(_extract(partner_tuple, 1) if isinstance(partner_tuple, (list, tuple)) else None),
        "invoice_date": record.get("invoice_date") or None,
        "invoice_date_due": record.get("invoice_date_due") or None,
        "accounting_date": record.get("date") or None,
        "amount_untaxed": _safe_float(record.get("amount_untaxed")),
        "amount_tax": _safe_float(record.get("amount_tax")),
        "amount_total": _safe_float(record.get("amount_total")),
        "amount_residual": _safe_float(record.get("amount_residual")),
        "amount_total_signed": _safe_float(record.get("amount_total_signed")),
        "amount_residual_signed": _safe_float(record.get("amount_residual_signed")),
        "currency": _safe_str(_extract(currency_tuple, 1) if isinstance(currency_tuple, (list, tuple)) else None, "CHF"),
        "state": _safe_str(record.get("state"), "draft"),
        "payment_state": _safe_str(record.get("payment_state"), "not_paid"),
        "journal_code": _safe_str(journal_code),
        "journal_name": _safe_str(journal_name),
        "last_erp_write_date": _safe_str(record.get("write_date")),
    }


def _odoo_invoice_line_to_neon(record: Dict[str, Any]) -> Dict[str, Any]:
    """Transform an Odoo account.move.line record to erp_data.invoice_lines format."""
    account_tuple = record.get("account_id")
    product_tuple = record.get("product_id")

    return {
        "erp_line_id": record.get("id"),
        "erp_move_id": _extract(record.get("move_id"), 0) if isinstance(record.get("move_id"), (list, tuple)) else record.get("move_id"),
        "line_name": record.get("name") or None,
        "quantity": _safe_float(record.get("quantity"), 1.0),
        "price_unit": _safe_float(record.get("price_unit")),
        "discount": _safe_float(record.get("discount")),
        "account_number": _extract_account_number(account_tuple),
        "account_name": _extract(account_tuple, 1) if isinstance(account_tuple, (list, tuple)) else None,
        "price_subtotal": _safe_float(record.get("price_subtotal")),
        "price_total": _safe_float(record.get("price_total")),
        "debit": _safe_float(record.get("debit")),
        "credit": _safe_float(record.get("credit")),
        "balance": _safe_float(record.get("balance")),
        "tax_ids": record.get("tax_ids") or [],
        "tax_rate": None,  # Computed from tax_ids if needed
        "product_name": _extract(product_tuple, 1) if isinstance(product_tuple, (list, tuple)) else None,
        "product_id": _extract(product_tuple, 0) if isinstance(product_tuple, (list, tuple)) else None,
    }


# ─── Country code resolution ────────────────────────────────────

def _resolve_country_codes(
    erp_connection, contacts: List[Dict[str, Any]]
) -> None:
    """Resolve country_code and state_code from Odoo IDs in-place."""
    # Collect unique country IDs
    country_ids = set()
    state_ids = set()
    for c in contacts:
        cid = _extract(c.get("country_id") if isinstance(c, dict) and "country_id" in c else None, 0)
        # We already transformed, so use raw records for resolution
        pass

    # For simplicity, use country_name → code mapping for common countries
    # Full resolution via execute_kw is done at sync time
    pass


# ═════════════════════════════════════════════════════════════════
# MAIN SYNC ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════

async def sync_contacts_invoices(
    uid: str,
    mandate_path: str,
    collection_id: str,
    force_full: bool = False,
) -> Dict[str, Any]:
    """
    Synchronise contacts + factures depuis Odoo vers Neon.

    Args:
        uid: Firebase user ID
        mandate_path: Chemin du mandat Firebase
        collection_id: collection_name / space_id
        force_full: True pour sync complète (vs incrémentale)

    Returns:
        Dict avec statistiques de sync
    """
    from .neon_accounting_manager import get_neon_accounting_manager
    from .neon_contacts_invoices_manager import get_contacts_invoices_manager

    logger.info(
        "[CONTACTS_INV_SYNC] START uid=%s mandate=%s force_full=%s",
        uid, mandate_path, force_full,
    )

    acct_mgr = get_neon_accounting_manager()
    ci_mgr = get_contacts_invoices_manager()

    # 1. Resolve company_id
    company_id = await acct_mgr.get_company_id_from_mandate_path(mandate_path)
    if not company_id:
        msg = f"Company not found for mandate_path={mandate_path}"
        logger.error("[CONTACTS_INV_SYNC] %s", msg)
        return {"success": False, "error": msg}

    # 2. Get ERP connection
    erp_connection = _get_erp_connection(uid, collection_id)
    if not erp_connection:
        msg = f"ERP connection failed for uid={uid} collection={collection_id}"
        logger.error("[CONTACTS_INV_SYNC] %s", msg)
        return {"success": False, "error": msg}

    result = {"success": True}

    try:
        # ─── CONTACTS ────────────────────────────────────────
        await acct_mgr.update_sync_metadata(company_id, "contacts", sync_status="running")

        contacts_domain = [
            "|",
            ["supplier_rank", ">", 0],
            ["customer_rank", ">", 0],
        ]

        if not force_full:
            sync_meta = await acct_mgr.get_sync_metadata(company_id, "contacts")
            if sync_meta and sync_meta.get("last_sync_time"):
                last_sync = sync_meta["last_sync_time"]
                if isinstance(last_sync, str):
                    last_sync = datetime.fromisoformat(last_sync)
                safe_date = (last_sync - timedelta(minutes=SYNC_OVERLAP_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                contacts_domain = [
                    "&",
                    "|",
                    ["supplier_rank", ">", 0],
                    ["customer_rank", ">", 0],
                    ["write_date", ">=", safe_date],
                ]
                logger.info("[CONTACTS_INV_SYNC] Contacts incremental since %s", safe_date)

        contact_fields = _get_contact_fields(erp_connection)
        contact_records = _resilient_search_read(
            erp_connection, "res.partner", contacts_domain, contact_fields, limit=10000,
        )
        logger.info("[CONTACTS_INV_SYNC] Fetched %d contacts from ERP", len(contact_records or []))

        if contact_records:
            # Resolve country codes via batch lookup
            country_ids = set()
            for rec in contact_records:
                cid = rec.get("country_id")
                if isinstance(cid, (list, tuple)) and cid:
                    country_ids.add(int(cid[0]))

            country_code_map = {}
            if country_ids:
                try:
                    countries = erp_connection.execute_kw(
                        "res.country", "read",
                        [list(country_ids)],
                        {"fields": ["id", "code"]},
                    )
                    country_code_map = {c["id"]: c["code"] for c in (countries or [])}
                except Exception as e:
                    logger.warning("[CONTACTS_INV_SYNC] Country code resolution failed: %s", e)

            # Transform
            contacts_neon = []
            for rec in contact_records:
                c = _odoo_contact_to_neon(rec)
                # Resolve country_code
                cid = rec.get("country_id")
                if isinstance(cid, (list, tuple)) and cid:
                    c["country_code"] = country_code_map.get(int(cid[0]))
                contacts_neon.append(c)

            contacts_stats = await ci_mgr.upsert_contacts(company_id, contacts_neon)
            result["contacts"] = contacts_stats
        else:
            result["contacts"] = {"added": 0, "modified": 0, "unchanged": 0}

        await acct_mgr.update_sync_metadata(
            company_id, "contacts",
            sync_status="idle",
            last_sync_time=datetime.now(timezone.utc),
            last_changes=result["contacts"],
        )

        # ─── INVOICES ────────────────────────────────────────
        await acct_mgr.update_sync_metadata(company_id, "invoices", sync_status="running")

        invoices_domain = [
            ["move_type", "in", ["in_invoice", "out_invoice", "in_refund", "out_refund"]],
        ]

        if not force_full:
            sync_meta = await acct_mgr.get_sync_metadata(company_id, "invoices")
            if sync_meta and sync_meta.get("last_sync_time"):
                last_sync = sync_meta["last_sync_time"]
                if isinstance(last_sync, str):
                    last_sync = datetime.fromisoformat(last_sync)
                safe_date = (last_sync - timedelta(minutes=SYNC_OVERLAP_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                invoices_domain.append(["write_date", ">=", safe_date])
                logger.info("[CONTACTS_INV_SYNC] Invoices incremental since %s", safe_date)

        # Build journal_id → code mapping
        journal_id_to_code = {}
        try:
            journals = erp_connection.execute_kw(
                "account.journal", "search_read",
                [[["company_id", "=", erp_connection.company_id]]],
                {"fields": ["id", "code"]},
            )
            journal_id_to_code = {j["id"]: j["code"] for j in (journals or [])}
        except Exception as e:
            logger.warning("[CONTACTS_INV_SYNC] Journal lookup failed: %s", e)

        invoice_fields = _get_invoice_fields(erp_connection)
        invoice_records = _resilient_search_read(
            erp_connection, "account.move", invoices_domain, invoice_fields, limit=50000,
        )
        logger.info("[CONTACTS_INV_SYNC] Fetched %d invoices from ERP", len(invoice_records or []))

        if invoice_records:
            invoices_neon = [_odoo_invoice_to_neon(rec, journal_id_to_code) for rec in invoice_records]
            invoices_stats = await ci_mgr.upsert_invoices(company_id, invoices_neon)
            result["invoices"] = {k: v for k, v in invoices_stats.items() if k != "modified_move_ids"}

            # ─── INVOICE LINES (for new/modified invoices) ───
            modified_move_ids = invoices_stats.get("modified_move_ids", [])
            if modified_move_ids:
                # Fetch lines for modified invoices
                lines_domain = [
                    ["move_id", "in", modified_move_ids],
                    ["display_type", "not in", ["line_section", "line_note"]],
                ]
                line_fields = _get_invoice_line_fields(erp_connection)
                line_records = _resilient_search_read(
                    erp_connection, "account.move.line", lines_domain, line_fields, limit=100000,
                )
                logger.info(
                    "[CONTACTS_INV_SYNC] Fetched %d invoice lines for %d modified invoices",
                    len(line_records or []), len(modified_move_ids),
                )

                if line_records:
                    lines_neon = [_odoo_invoice_line_to_neon(rec) for rec in line_records]
                    lines_stats = await ci_mgr.sync_invoice_lines(company_id, lines_neon, modified_move_ids)
                    result["invoice_lines"] = lines_stats
                else:
                    result["invoice_lines"] = {"deleted": 0, "inserted": 0}
            else:
                result["invoice_lines"] = {"deleted": 0, "inserted": 0}
        else:
            result["invoices"] = {"added": 0, "modified": 0, "unchanged": 0}
            result["invoice_lines"] = {"deleted": 0, "inserted": 0}

        await acct_mgr.update_sync_metadata(
            company_id, "invoices",
            sync_status="idle",
            last_sync_time=datetime.now(timezone.utc),
            last_changes=result.get("invoices", {}),
        )

        # ─── AGING SNAPSHOT ──────────────────────────────────
        inv_stats = result.get("invoices", {})
        if inv_stats.get("added", 0) > 0 or inv_stats.get("modified", 0) > 0:
            try:
                aging_ap = await ci_mgr.compute_aging_snapshot(company_id, direction="payable")
                aging_ar = await ci_mgr.compute_aging_snapshot(company_id, direction="receivable")
                result["aging"] = {
                    "payable_total": aging_ap["summary"]["total"],
                    "receivable_total": aging_ar["summary"]["total"],
                }
            except Exception as e:
                logger.warning("[CONTACTS_INV_SYNC] Aging computation failed: %s", e)

        logger.info("[CONTACTS_INV_SYNC] DONE company=%s result=%s", company_id, result)
        return result

    except Exception as e:
        logger.error("[CONTACTS_INV_SYNC] ERROR company=%s: %s", company_id, e, exc_info=True)
        await acct_mgr.update_sync_metadata(
            company_id, "contacts", sync_status="error", last_error=str(e),
        )
        await acct_mgr.update_sync_metadata(
            company_id, "invoices", sync_status="error", last_error=str(e),
        )
        return {"success": False, "error": str(e)}


def _get_erp_connection(uid: str, collection_id: str):
    """Get an Odoo connection via ERPConnectionManager."""
    try:
        from ..erp_service import ERPConnectionManager

        manager = ERPConnectionManager()
        connection = manager.get_connection(uid, collection_id)
        if connection:
            logger.info("[CONTACTS_INV_SYNC] ERP connection OK for uid=%s", uid)
        return connection
    except Exception as e:
        logger.error("[CONTACTS_INV_SYNC] ERP connection error: %s", e, exc_info=True)
        return None
