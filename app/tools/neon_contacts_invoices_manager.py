"""
Gestionnaire Neon Contacts & Invoices — Données ERP internalisées.

Réutilise le pool de connexions de NeonAccountingManager (singleton partagé).
Pattern identique à neon_fixed_asset_manager.py.

Tables: erp_data.contacts, erp_data.invoices, erp_data.invoice_lines, erp_data.aging_snapshots
Views:  erp_data.open_invoices, erp_data.contact_balances

Usage:
    from app.tools.neon_contacts_invoices_manager import get_contacts_invoices_manager

    manager = get_contacts_invoices_manager()
    contacts = await manager.list_contacts(company_id)
"""

import hashlib
import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

try:
    import asyncpg
except ImportError:
    asyncpg = None

from .neon_accounting_manager import get_neon_accounting_manager, _to_date, _to_datetime

logger = logging.getLogger("erp_data.neon_manager")

# ─── Hash helpers ────────────────────────────────────────────────
HASH_EXCLUDED_CONTACTS = {
    "id", "sync_hash", "sync_version", "created_at", "updated_at",
    "is_deleted", "contact_type",  # GENERATED column
}
HASH_EXCLUDED_INVOICES = {
    "id", "contact_id", "sync_hash", "sync_version", "created_at", "updated_at",
    "is_deleted", "invoice_direction",  # GENERATED column
    "amount_total_company", "amount_residual_company",
}
HASH_EXCLUDED_LINES = {
    "id", "invoice_id", "sync_hash", "created_at",
}

SYNC_OVERLAP_MINUTES = 5


def _compute_hash(row: Dict[str, Any], excluded: set) -> str:
    """SHA-256 hash of business fields, excluding control/generated fields."""
    business = {k: v for k, v in row.items() if k not in excluded}
    return hashlib.sha256(
        json.dumps(business, sort_keys=True, default=str).encode()
    ).hexdigest()


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value is False:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value is False:
            return default
        return int(value)
    except (ValueError, TypeError):
        return default


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert asyncpg Record to dict, handling Decimal/UUID/date serialization."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, Decimal):
            d[k] = float(v)
        elif isinstance(v, UUID):
            d[k] = str(v)
        elif isinstance(v, (date, datetime)):
            d[k] = v.isoformat()
    return d


# ═════════════════════════════════════════════════════════════════
# MANAGER
# ═════════════════════════════════════════════════════════════════

class NeonContactsInvoicesManager:
    """
    Manager pour le schema erp_data dans Neon PostgreSQL.
    Singleton thread-safe, réutilise le pool du NeonAccountingManager.
    """

    _instance: Optional["NeonContactsInvoicesManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    async def _get_pool(self) -> "asyncpg.Pool":
        """Réutilise le pool du NeonAccountingManager."""
        acct_mgr = get_neon_accounting_manager()
        return await acct_mgr.get_pool()

    # ===================================================================
    # SYNC: CONTACTS
    # ===================================================================

    async def upsert_contacts(
        self, company_id: UUID, contacts: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Upsert contacts via staging table + hash comparison.
        Pattern identique à incremental_sync_gl_entries.
        """
        if not contacts:
            return {"added": 0, "modified": 0, "unchanged": 0}

        pool = await self._get_pool()
        stats = {"added": 0, "modified": 0, "unchanged": 0}

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    CREATE TEMP TABLE _contacts_staging (
                        erp_partner_id INTEGER,
                        erp_type VARCHAR(20),
                        name VARCHAR(255),
                        display_name VARCHAR(255),
                        is_company BOOLEAN,
                        parent_id INTEGER,
                        supplier_rank INTEGER,
                        customer_rank INTEGER,
                        email VARCHAR(255),
                        phone VARCHAR(50),
                        mobile VARCHAR(50),
                        website VARCHAR(500),
                        street VARCHAR(255),
                        street2 VARCHAR(255),
                        city VARCHAR(100),
                        zip VARCHAR(20),
                        state_code VARCHAR(10),
                        state_name VARCHAR(100),
                        country_code CHAR(2),
                        country_name VARCHAR(100),
                        vat VARCHAR(50),
                        property_account_payable VARCHAR(20),
                        property_account_receivable VARCHAR(20),
                        payment_term_supplier VARCHAR(100),
                        payment_term_customer VARCHAR(100),
                        is_active BOOLEAN,
                        last_erp_write_date TIMESTAMPTZ,
                        sync_hash VARCHAR(64)
                    ) ON COMMIT DROP
                """)

                records = []
                for c in contacts:
                    h = _compute_hash(c, HASH_EXCLUDED_CONTACTS)
                    records.append((
                        _safe_int(c.get("erp_partner_id")),
                        c.get("erp_type", "odoo"),
                        c.get("name", ""),
                        c.get("display_name"),
                        c.get("is_company", True),
                        _safe_int(c.get("parent_id")) or None,
                        _safe_int(c.get("supplier_rank")),
                        _safe_int(c.get("customer_rank")),
                        c.get("email"),
                        c.get("phone"),
                        c.get("mobile"),
                        c.get("website"),
                        c.get("street"),
                        c.get("street2"),
                        c.get("city"),
                        c.get("zip"),
                        c.get("state_code"),
                        c.get("state_name"),
                        c.get("country_code"),
                        c.get("country_name"),
                        c.get("vat"),
                        c.get("property_account_payable"),
                        c.get("property_account_receivable"),
                        c.get("payment_term_supplier"),
                        c.get("payment_term_customer"),
                        c.get("is_active", True),
                        _to_datetime(c.get("last_erp_write_date")),
                        h,
                    ))

                await conn.copy_records_to_table(
                    "_contacts_staging", records=records,
                    columns=[
                        "erp_partner_id", "erp_type", "name", "display_name",
                        "is_company", "parent_id", "supplier_rank", "customer_rank",
                        "email", "phone", "mobile", "website",
                        "street", "street2", "city", "zip",
                        "state_code", "state_name", "country_code", "country_name",
                        "vat", "property_account_payable", "property_account_receivable",
                        "payment_term_supplier", "payment_term_customer",
                        "is_active", "last_erp_write_date", "sync_hash",
                    ],
                )

                result = await conn.fetch("""
                    INSERT INTO erp_data.contacts (
                        company_id, erp_partner_id, erp_type,
                        name, display_name, is_company, parent_id,
                        supplier_rank, customer_rank,
                        email, phone, mobile, website,
                        street, street2, city, zip,
                        state_code, state_name, country_code, country_name,
                        vat, property_account_payable, property_account_receivable,
                        payment_term_supplier, payment_term_customer,
                        is_active, last_erp_write_date,
                        sync_hash, sync_version, is_deleted
                    )
                    SELECT
                        $1, s.erp_partner_id, s.erp_type,
                        s.name, s.display_name, s.is_company, s.parent_id,
                        s.supplier_rank, s.customer_rank,
                        s.email, s.phone, s.mobile, s.website,
                        s.street, s.street2, s.city, s.zip,
                        s.state_code, s.state_name, s.country_code, s.country_name,
                        s.vat, s.property_account_payable, s.property_account_receivable,
                        s.payment_term_supplier, s.payment_term_customer,
                        s.is_active, s.last_erp_write_date,
                        s.sync_hash, 1, FALSE
                    FROM _contacts_staging s
                    ON CONFLICT (company_id, erp_partner_id) DO UPDATE
                    SET name = EXCLUDED.name,
                        display_name = EXCLUDED.display_name,
                        is_company = EXCLUDED.is_company,
                        parent_id = EXCLUDED.parent_id,
                        supplier_rank = EXCLUDED.supplier_rank,
                        customer_rank = EXCLUDED.customer_rank,
                        email = EXCLUDED.email,
                        phone = EXCLUDED.phone,
                        mobile = EXCLUDED.mobile,
                        website = EXCLUDED.website,
                        street = EXCLUDED.street,
                        street2 = EXCLUDED.street2,
                        city = EXCLUDED.city,
                        zip = EXCLUDED.zip,
                        state_code = EXCLUDED.state_code,
                        state_name = EXCLUDED.state_name,
                        country_code = EXCLUDED.country_code,
                        country_name = EXCLUDED.country_name,
                        vat = EXCLUDED.vat,
                        property_account_payable = EXCLUDED.property_account_payable,
                        property_account_receivable = EXCLUDED.property_account_receivable,
                        payment_term_supplier = EXCLUDED.payment_term_supplier,
                        payment_term_customer = EXCLUDED.payment_term_customer,
                        is_active = EXCLUDED.is_active,
                        last_erp_write_date = EXCLUDED.last_erp_write_date,
                        sync_hash = EXCLUDED.sync_hash,
                        sync_version = erp_data.contacts.sync_version + 1,
                        is_deleted = FALSE
                    WHERE erp_data.contacts.sync_hash IS DISTINCT FROM EXCLUDED.sync_hash
                    RETURNING (xmax = 0) AS is_insert
                """, company_id)

                for row in result:
                    if row["is_insert"]:
                        stats["added"] += 1
                    else:
                        stats["modified"] += 1
                stats["unchanged"] = len(contacts) - stats["added"] - stats["modified"]

        logger.info(
            "Contacts sync company=%s: added=%d modified=%d unchanged=%d",
            company_id, stats["added"], stats["modified"], stats["unchanged"],
        )
        return stats

    # ===================================================================
    # SYNC: INVOICES
    # ===================================================================

    async def upsert_invoices(
        self, company_id: UUID, invoices: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Upsert invoices via staging table + hash comparison.
        Returns stats + list of modified erp_move_ids (for line sync).
        """
        if not invoices:
            return {"added": 0, "modified": 0, "unchanged": 0, "modified_move_ids": []}

        pool = await self._get_pool()
        stats = {"added": 0, "modified": 0, "unchanged": 0, "modified_move_ids": []}

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    CREATE TEMP TABLE _invoices_staging (
                        erp_move_id INTEGER,
                        erp_type VARCHAR(20),
                        move_type VARCHAR(20),
                        invoice_name VARCHAR(100),
                        invoice_ref VARCHAR(255),
                        invoice_origin VARCHAR(255),
                        erp_partner_id INTEGER,
                        partner_name VARCHAR(255),
                        invoice_date DATE,
                        invoice_date_due DATE,
                        accounting_date DATE,
                        amount_untaxed NUMERIC(15,2),
                        amount_tax NUMERIC(15,2),
                        amount_total NUMERIC(15,2),
                        amount_residual NUMERIC(15,2),
                        amount_total_signed NUMERIC(15,2),
                        amount_residual_signed NUMERIC(15,2),
                        currency CHAR(3),
                        state VARCHAR(20),
                        payment_state VARCHAR(20),
                        journal_code VARCHAR(10),
                        journal_name VARCHAR(255),
                        last_erp_write_date TIMESTAMPTZ,
                        sync_hash VARCHAR(64)
                    ) ON COMMIT DROP
                """)

                records = []
                for inv in invoices:
                    h = _compute_hash(inv, HASH_EXCLUDED_INVOICES)
                    records.append((
                        _safe_int(inv.get("erp_move_id")),
                        inv.get("erp_type", "odoo"),
                        inv.get("move_type", "in_invoice"),
                        inv.get("invoice_name", ""),
                        inv.get("invoice_ref"),
                        inv.get("invoice_origin"),
                        _safe_int(inv.get("erp_partner_id")) or None,
                        inv.get("partner_name"),
                        _to_date(inv.get("invoice_date")),
                        _to_date(inv.get("invoice_date_due")),
                        _to_date(inv.get("accounting_date")),
                        _safe_float(inv.get("amount_untaxed")),
                        _safe_float(inv.get("amount_tax")),
                        _safe_float(inv.get("amount_total")),
                        _safe_float(inv.get("amount_residual")),
                        _safe_float(inv.get("amount_total_signed")),
                        _safe_float(inv.get("amount_residual_signed")),
                        inv.get("currency", "CHF"),
                        inv.get("state", "draft"),
                        inv.get("payment_state", "not_paid"),
                        inv.get("journal_code"),
                        inv.get("journal_name"),
                        _to_datetime(inv.get("last_erp_write_date")),
                        h,
                    ))

                await conn.copy_records_to_table(
                    "_invoices_staging", records=records,
                    columns=[
                        "erp_move_id", "erp_type", "move_type",
                        "invoice_name", "invoice_ref", "invoice_origin",
                        "erp_partner_id", "partner_name",
                        "invoice_date", "invoice_date_due", "accounting_date",
                        "amount_untaxed", "amount_tax", "amount_total",
                        "amount_residual", "amount_total_signed", "amount_residual_signed",
                        "currency", "state", "payment_state",
                        "journal_code", "journal_name",
                        "last_erp_write_date", "sync_hash",
                    ],
                )

                # Resolve contact_id from erp_partner_id
                await conn.execute("""
                    ALTER TABLE _invoices_staging ADD COLUMN contact_id UUID
                """)
                await conn.execute("""
                    UPDATE _invoices_staging s
                    SET contact_id = c.id
                    FROM erp_data.contacts c
                    WHERE c.company_id = $1
                      AND c.erp_partner_id = s.erp_partner_id
                """, company_id)

                result = await conn.fetch("""
                    INSERT INTO erp_data.invoices (
                        company_id, erp_move_id, erp_type, move_type,
                        invoice_name, invoice_ref, invoice_origin,
                        contact_id, erp_partner_id, partner_name,
                        invoice_date, invoice_date_due, accounting_date,
                        amount_untaxed, amount_tax, amount_total,
                        amount_residual, amount_total_signed, amount_residual_signed,
                        currency, state, payment_state,
                        journal_code, journal_name,
                        last_erp_write_date, sync_hash, sync_version, is_deleted
                    )
                    SELECT
                        $1, s.erp_move_id, s.erp_type, s.move_type,
                        s.invoice_name, s.invoice_ref, s.invoice_origin,
                        s.contact_id, s.erp_partner_id, s.partner_name,
                        s.invoice_date, s.invoice_date_due, s.accounting_date,
                        s.amount_untaxed, s.amount_tax, s.amount_total,
                        s.amount_residual, s.amount_total_signed, s.amount_residual_signed,
                        s.currency, s.state, s.payment_state,
                        s.journal_code, s.journal_name,
                        s.last_erp_write_date, s.sync_hash, 1, FALSE
                    FROM _invoices_staging s
                    ON CONFLICT (company_id, erp_move_id) DO UPDATE
                    SET move_type = EXCLUDED.move_type,
                        invoice_name = EXCLUDED.invoice_name,
                        invoice_ref = EXCLUDED.invoice_ref,
                        invoice_origin = EXCLUDED.invoice_origin,
                        contact_id = EXCLUDED.contact_id,
                        erp_partner_id = EXCLUDED.erp_partner_id,
                        partner_name = EXCLUDED.partner_name,
                        invoice_date = EXCLUDED.invoice_date,
                        invoice_date_due = EXCLUDED.invoice_date_due,
                        accounting_date = EXCLUDED.accounting_date,
                        amount_untaxed = EXCLUDED.amount_untaxed,
                        amount_tax = EXCLUDED.amount_tax,
                        amount_total = EXCLUDED.amount_total,
                        amount_residual = EXCLUDED.amount_residual,
                        amount_total_signed = EXCLUDED.amount_total_signed,
                        amount_residual_signed = EXCLUDED.amount_residual_signed,
                        currency = EXCLUDED.currency,
                        state = EXCLUDED.state,
                        payment_state = EXCLUDED.payment_state,
                        journal_code = EXCLUDED.journal_code,
                        journal_name = EXCLUDED.journal_name,
                        last_erp_write_date = EXCLUDED.last_erp_write_date,
                        sync_hash = EXCLUDED.sync_hash,
                        sync_version = erp_data.invoices.sync_version + 1,
                        is_deleted = FALSE
                    WHERE erp_data.invoices.sync_hash IS DISTINCT FROM EXCLUDED.sync_hash
                    RETURNING erp_move_id, (xmax = 0) AS is_insert
                """, company_id)

                for row in result:
                    if row["is_insert"]:
                        stats["added"] += 1
                    else:
                        stats["modified"] += 1
                    stats["modified_move_ids"].append(row["erp_move_id"])

                stats["unchanged"] = len(invoices) - stats["added"] - stats["modified"]

        logger.info(
            "Invoices sync company=%s: added=%d modified=%d unchanged=%d",
            company_id, stats["added"], stats["modified"], stats["unchanged"],
        )
        return stats

    # ===================================================================
    # SYNC: INVOICE LINES
    # ===================================================================

    async def sync_invoice_lines(
        self, company_id: UUID, lines: List[Dict[str, Any]], erp_move_ids: List[int]
    ) -> Dict[str, int]:
        """
        Sync invoice lines for modified invoices.
        Strategy: delete-then-insert for changed invoices (lines change often).
        """
        if not erp_move_ids:
            return {"deleted": 0, "inserted": 0}

        pool = await self._get_pool()
        stats = {"deleted": 0, "inserted": 0}

        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1. Delete existing lines for modified invoices
                del_result = await conn.execute("""
                    DELETE FROM erp_data.invoice_lines
                    WHERE company_id = $1 AND erp_move_id = ANY($2::int[])
                """, company_id, erp_move_ids)
                stats["deleted"] = int(del_result.split()[-1]) if "DELETE" in del_result else 0

                if not lines:
                    return stats

                # 2. Resolve invoice_id from erp_move_id
                invoice_map = {}
                rows = await conn.fetch("""
                    SELECT id, erp_move_id FROM erp_data.invoices
                    WHERE company_id = $1 AND erp_move_id = ANY($2::int[])
                """, company_id, erp_move_ids)
                for r in rows:
                    invoice_map[r["erp_move_id"]] = r["id"]

                # 3. Insert new lines
                records = []
                for ln in lines:
                    move_id = _safe_int(ln.get("erp_move_id"))
                    invoice_id = invoice_map.get(move_id)
                    if not invoice_id:
                        continue

                    h = _compute_hash(ln, HASH_EXCLUDED_LINES)
                    tax_ids = ln.get("tax_ids") or []
                    if not isinstance(tax_ids, (list, str)):
                        tax_ids = []

                    records.append((
                        invoice_id,
                        company_id,
                        _safe_int(ln.get("erp_line_id")),
                        move_id,
                        ln.get("line_name"),
                        _safe_float(ln.get("quantity"), 1.0),
                        _safe_float(ln.get("price_unit")),
                        _safe_float(ln.get("discount")),
                        ln.get("account_number"),
                        ln.get("account_name"),
                        _safe_float(ln.get("price_subtotal")),
                        _safe_float(ln.get("price_total")),
                        _safe_float(ln.get("debit")),
                        _safe_float(ln.get("credit")),
                        _safe_float(ln.get("balance")),
                        json.dumps(tax_ids) if isinstance(tax_ids, list) else str(tax_ids),
                        ln.get("tax_rate"),
                        ln.get("product_name"),
                        _safe_int(ln.get("product_id")) or None,
                        h,
                    ))

                if records:
                    await conn.executemany("""
                        INSERT INTO erp_data.invoice_lines (
                            invoice_id, company_id, erp_line_id, erp_move_id,
                            line_name, quantity, price_unit, discount,
                            account_number, account_name,
                            price_subtotal, price_total, debit, credit, balance,
                            tax_ids, tax_rate, product_name, product_id,
                            sync_hash
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15, $16::jsonb, $17, $18, $19, $20
                        )
                        ON CONFLICT (company_id, erp_line_id) DO UPDATE
                        SET invoice_id = EXCLUDED.invoice_id,
                            erp_move_id = EXCLUDED.erp_move_id,
                            line_name = EXCLUDED.line_name,
                            quantity = EXCLUDED.quantity,
                            price_unit = EXCLUDED.price_unit,
                            discount = EXCLUDED.discount,
                            account_number = EXCLUDED.account_number,
                            account_name = EXCLUDED.account_name,
                            price_subtotal = EXCLUDED.price_subtotal,
                            price_total = EXCLUDED.price_total,
                            debit = EXCLUDED.debit,
                            credit = EXCLUDED.credit,
                            balance = EXCLUDED.balance,
                            tax_ids = EXCLUDED.tax_ids,
                            tax_rate = EXCLUDED.tax_rate,
                            product_name = EXCLUDED.product_name,
                            product_id = EXCLUDED.product_id,
                            sync_hash = EXCLUDED.sync_hash
                    """, records)
                    stats["inserted"] = len(records)

        logger.info(
            "Invoice lines sync company=%s: deleted=%d inserted=%d",
            company_id, stats["deleted"], stats["inserted"],
        )
        return stats

    # ===================================================================
    # AGING ENGINE
    # ===================================================================

    async def compute_aging_snapshot(
        self,
        company_id: UUID,
        as_of_date: Optional[date] = None,
        direction: str = "payable",
    ) -> Dict[str, Any]:
        """
        Compute aging snapshot from open invoices and persist to aging_snapshots.

        Args:
            company_id: UUID
            as_of_date: Date de référence (default: today)
            direction: 'payable' (AP) or 'receivable' (AR)

        Returns:
            Dict with summary totals + details per contact
        """
        if as_of_date is None:
            as_of_date = date.today()

        pool = await self._get_pool()

        async with pool.acquire() as conn:
            # 1. Fetch open invoices
            rows = await conn.fetch("""
                SELECT
                    i.contact_id, i.erp_partner_id, i.partner_name,
                    i.invoice_date_due, i.currency,
                    COALESCE(i.amount_residual_company,
                             ABS(i.amount_residual_signed)) AS residual
                FROM erp_data.invoices i
                WHERE i.company_id = $1
                  AND i.state = 'posted'
                  AND i.payment_state IN ('not_paid', 'partial')
                  AND i.invoice_direction = $2
                  AND i.is_deleted = FALSE
            """, company_id, direction)

            # 2. Get company currency
            company_row = await conn.fetchrow(
                "SELECT base_currency FROM core.companies WHERE id = $1", company_id
            )
            currency = company_row["base_currency"] if company_row else "CHF"

            # 3. Bucket computation
            buckets_by_contact: Dict[Optional[UUID], Dict] = {}

            for r in rows:
                contact_id = r["contact_id"]
                due = r["invoice_date_due"] or as_of_date
                days = (as_of_date - due).days
                amount = float(r["residual"] or 0)

                if contact_id not in buckets_by_contact:
                    buckets_by_contact[contact_id] = {
                        "contact_id": contact_id,
                        "erp_partner_id": r["erp_partner_id"],
                        "partner_name": r["partner_name"] or "Unknown",
                        "not_due": 0.0, "bucket_1_30": 0.0, "bucket_31_60": 0.0,
                        "bucket_61_90": 0.0, "bucket_91_120": 0.0, "bucket_120_plus": 0.0,
                        "total": 0.0,
                        "count_not_due": 0, "count_1_30": 0, "count_31_60": 0,
                        "count_61_90": 0, "count_91_120": 0, "count_120_plus": 0,
                        "count_total": 0,
                    }

                b = buckets_by_contact[contact_id]
                b["total"] += amount
                b["count_total"] += 1

                if days <= 0:
                    b["not_due"] += amount
                    b["count_not_due"] += 1
                elif days <= 30:
                    b["bucket_1_30"] += amount
                    b["count_1_30"] += 1
                elif days <= 60:
                    b["bucket_31_60"] += amount
                    b["count_31_60"] += 1
                elif days <= 90:
                    b["bucket_61_90"] += amount
                    b["count_61_90"] += 1
                elif days <= 120:
                    b["bucket_91_120"] += amount
                    b["count_91_120"] += 1
                else:
                    b["bucket_120_plus"] += amount
                    b["count_120_plus"] += 1

            # 4. Persist to aging_snapshots
            async with conn.transaction():
                # Clear existing snapshot for this date/direction
                await conn.execute("""
                    DELETE FROM erp_data.aging_snapshots
                    WHERE company_id = $1 AND snapshot_date = $2 AND direction = $3
                """, company_id, as_of_date, direction)

                # Insert per-contact rows
                for b in buckets_by_contact.values():
                    await conn.execute("""
                        INSERT INTO erp_data.aging_snapshots (
                            company_id, snapshot_date, direction,
                            contact_id, erp_partner_id, partner_name, currency,
                            not_due, bucket_1_30, bucket_31_60,
                            bucket_61_90, bucket_91_120, bucket_120_plus, total,
                            count_not_due, count_1_30, count_31_60,
                            count_61_90, count_91_120, count_120_plus, count_total
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7,
                            $8, $9, $10, $11, $12, $13, $14,
                            $15, $16, $17, $18, $19, $20, $21
                        )
                    """,
                        company_id, as_of_date, direction,
                        b["contact_id"], b["erp_partner_id"], b["partner_name"], currency,
                        b["not_due"], b["bucket_1_30"], b["bucket_31_60"],
                        b["bucket_61_90"], b["bucket_91_120"], b["bucket_120_plus"], b["total"],
                        b["count_not_due"], b["count_1_30"], b["count_31_60"],
                        b["count_61_90"], b["count_91_120"], b["count_120_plus"], b["count_total"],
                    )

            # 5. Build summary
            summary = {
                "not_due": 0.0, "bucket_1_30": 0.0, "bucket_31_60": 0.0,
                "bucket_61_90": 0.0, "bucket_91_120": 0.0, "bucket_120_plus": 0.0,
                "total": 0.0, "count_total": 0,
            }
            for b in buckets_by_contact.values():
                for k in summary:
                    summary[k] += b[k]

            details = sorted(buckets_by_contact.values(), key=lambda x: -x["total"])

            logger.info(
                "Aging snapshot company=%s date=%s dir=%s contacts=%d total=%.2f",
                company_id, as_of_date, direction, len(details), summary["total"],
            )

            return {
                "snapshot_date": as_of_date.isoformat(),
                "direction": direction,
                "currency": currency,
                "summary": summary,
                "details": [
                    {k: (str(v) if isinstance(v, UUID) else v) for k, v in d.items()}
                    for d in details
                ],
            }

    async def get_latest_aging(
        self, company_id: UUID, direction: str = "payable"
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent aging snapshot without recomputing."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM erp_data.aging_snapshots
                WHERE company_id = $1 AND direction = $2
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM erp_data.aging_snapshots
                      WHERE company_id = $1 AND direction = $2
                  )
                ORDER BY total DESC
            """, company_id, direction)

            if not rows:
                return None

            details = [_row_to_dict(r) for r in rows]
            summary = {
                "not_due": 0.0, "bucket_1_30": 0.0, "bucket_31_60": 0.0,
                "bucket_61_90": 0.0, "bucket_91_120": 0.0, "bucket_120_plus": 0.0,
                "total": 0.0, "count_total": 0,
            }
            for d in details:
                for k in summary:
                    summary[k] += d.get(k, 0)

            return {
                "snapshot_date": details[0].get("snapshot_date"),
                "direction": direction,
                "currency": details[0].get("currency", "CHF"),
                "summary": summary,
                "details": details,
            }

    async def get_aging_trend(
        self, company_id: UUID, direction: str = "payable", months: int = 6
    ) -> List[Dict[str, Any]]:
        """Return aging totals over time (for trend chart)."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    snapshot_date,
                    SUM(not_due) AS not_due,
                    SUM(bucket_1_30 + bucket_31_60 + bucket_61_90 +
                        bucket_91_120 + bucket_120_plus) AS overdue,
                    SUM(total) AS total,
                    SUM(count_total) AS count_total
                FROM erp_data.aging_snapshots
                WHERE company_id = $1 AND direction = $2
                  AND snapshot_date >= CURRENT_DATE - ($3 || ' months')::interval
                GROUP BY snapshot_date
                ORDER BY snapshot_date
            """, company_id, direction, str(months))
            return [_row_to_dict(r) for r in rows]

    # ===================================================================
    # READ: CONTACTS
    # ===================================================================

    async def list_contacts(
        self, company_id: UUID,
        contact_type: Optional[str] = None,
        search_text: Optional[str] = None,
        country_code: Optional[str] = None,
        is_active: bool = True,
        has_open_balance: bool = False,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "name",
    ) -> Dict[str, Any]:
        """List contacts with filtering, pagination, and open balance."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            conditions = ["c.company_id = $1", "c.is_deleted = FALSE"]
            params: list = [company_id]
            idx = 2

            if is_active:
                conditions.append("c.is_active = TRUE")

            if contact_type and contact_type != "all":
                conditions.append(f"c.contact_type = ${idx}")
                params.append(contact_type)
                idx += 1

            if search_text:
                conditions.append(f"(c.name ILIKE ${idx} OR c.email ILIKE ${idx} OR c.vat ILIKE ${idx})")
                params.append(f"%{search_text}%")
                idx += 1

            if country_code:
                conditions.append(f"c.country_code = ${idx}")
                params.append(country_code)
                idx += 1

            where = " AND ".join(conditions)
            sort_col = {"name": "c.name", "created_at": "c.created_at"}.get(sort_by, "c.name")

            # Count
            count_row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM erp_data.contacts c WHERE {where}", *params
            )
            total = count_row["cnt"]

            # Fetch with balance info
            offset = (page - 1) * page_size
            rows = await conn.fetch(f"""
                SELECT c.*,
                    COALESCE(cb.ap_open_balance, 0) AS ap_open_balance,
                    COALESCE(cb.ar_open_balance, 0) AS ar_open_balance,
                    COALESCE(cb.ap_open_count, 0) AS ap_open_count,
                    COALESCE(cb.ar_open_count, 0) AS ar_open_count
                FROM erp_data.contacts c
                LEFT JOIN erp_data.contact_balances cb ON cb.contact_id = c.id
                WHERE {where}
                {"AND (COALESCE(cb.ap_open_balance, 0) != 0 OR COALESCE(cb.ar_open_balance, 0) != 0)" if has_open_balance else ""}
                ORDER BY {sort_col}
                LIMIT {page_size} OFFSET {offset}
            """, *params)

            return {
                "items": [_row_to_dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
            }

    async def get_contact(self, company_id: UUID, contact_id: UUID) -> Optional[Dict[str, Any]]:
        """Get a single contact with invoice summary."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*,
                    COALESCE(cb.ap_open_balance, 0) AS ap_open_balance,
                    COALESCE(cb.ar_open_balance, 0) AS ar_open_balance,
                    COALESCE(cb.ap_open_count, 0) AS ap_open_count,
                    COALESCE(cb.ar_open_count, 0) AS ar_open_count
                FROM erp_data.contacts c
                LEFT JOIN erp_data.contact_balances cb ON cb.contact_id = c.id
                WHERE c.company_id = $1 AND c.id = $2
            """, company_id, contact_id)
            return _row_to_dict(row) if row else None

    async def search_contacts(
        self, company_id: UUID, search_term: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Quick search contacts by name/email/vat."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, display_name, contact_type, email, phone,
                       country_code, vat, is_company
                FROM erp_data.contacts
                WHERE company_id = $1
                  AND is_deleted = FALSE
                  AND (name ILIKE $2 OR email ILIKE $2 OR vat ILIKE $2 OR display_name ILIKE $2)
                ORDER BY name
                LIMIT $3
            """, company_id, f"%{search_term}%", limit)
            return [_row_to_dict(r) for r in rows]

    # ===================================================================
    # READ: INVOICES
    # ===================================================================

    async def list_invoices(
        self, company_id: UUID,
        direction: Optional[str] = None,
        state: Optional[str] = None,
        payment_state: Optional[str] = None,
        contact_id: Optional[UUID] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search_text: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "invoice_date",
        sort_dir: str = "desc",
    ) -> Dict[str, Any]:
        """List invoices with filtering and pagination."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            conditions = ["i.company_id = $1", "i.is_deleted = FALSE"]
            params: list = [company_id]
            idx = 2

            if direction:
                conditions.append(f"i.invoice_direction = ${idx}")
                params.append(direction)
                idx += 1

            if state:
                conditions.append(f"i.state = ${idx}")
                params.append(state)
                idx += 1

            if payment_state:
                conditions.append(f"i.payment_state = ${idx}")
                params.append(payment_state)
                idx += 1

            if contact_id:
                conditions.append(f"i.contact_id = ${idx}")
                params.append(contact_id)
                idx += 1

            if date_from:
                conditions.append(f"i.invoice_date >= ${idx}")
                params.append(_to_date(date_from))
                idx += 1

            if date_to:
                conditions.append(f"i.invoice_date <= ${idx}")
                params.append(_to_date(date_to))
                idx += 1

            if search_text:
                conditions.append(f"(i.invoice_name ILIKE ${idx} OR i.invoice_ref ILIKE ${idx} OR i.partner_name ILIKE ${idx})")
                params.append(f"%{search_text}%")
                idx += 1

            where = " AND ".join(conditions)
            sort_col = {
                "invoice_date": "i.invoice_date",
                "invoice_date_due": "i.invoice_date_due",
                "amount_total": "i.amount_total",
                "partner_name": "i.partner_name",
            }.get(sort_by, "i.invoice_date")
            order = "DESC" if sort_dir == "desc" else "ASC"

            count_row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM erp_data.invoices i WHERE {where}", *params
            )
            total = count_row["cnt"]

            offset = (page - 1) * page_size
            rows = await conn.fetch(f"""
                SELECT i.* FROM erp_data.invoices i
                WHERE {where}
                ORDER BY {sort_col} {order}
                LIMIT {page_size} OFFSET {offset}
            """, *params)

            # Totals for filtered set
            totals_row = await conn.fetchrow(f"""
                SELECT
                    COUNT(*) AS count,
                    COALESCE(SUM(ABS(i.amount_total_signed)), 0) AS sum_total,
                    COALESCE(SUM(ABS(i.amount_residual_signed)), 0) AS sum_residual
                FROM erp_data.invoices i
                WHERE {where}
            """, *params)

            return {
                "items": [_row_to_dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
                "totals": {
                    "count": totals_row["count"],
                    "sum_total": float(totals_row["sum_total"]),
                    "sum_residual": float(totals_row["sum_residual"]),
                },
            }

    async def get_invoice(self, company_id: UUID, invoice_id: UUID) -> Optional[Dict[str, Any]]:
        """Get invoice with lines."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            inv = await conn.fetchrow("""
                SELECT * FROM erp_data.invoices
                WHERE company_id = $1 AND id = $2
            """, company_id, invoice_id)
            if not inv:
                return None

            lines = await conn.fetch("""
                SELECT * FROM erp_data.invoice_lines
                WHERE invoice_id = $1
                ORDER BY erp_line_id
            """, invoice_id)

            result = _row_to_dict(inv)
            result["lines"] = [_row_to_dict(ln) for ln in lines]
            return result

    async def get_invoices_by_contact(
        self, company_id: UUID, contact_id: UUID,
        direction: Optional[str] = None,
        payment_state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all invoices for a contact."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            conditions = ["company_id = $1", "contact_id = $2", "is_deleted = FALSE"]
            params: list = [company_id, contact_id]
            idx = 3

            if direction:
                conditions.append(f"invoice_direction = ${idx}")
                params.append(direction)
                idx += 1

            if payment_state:
                conditions.append(f"payment_state = ${idx}")
                params.append(payment_state)
                idx += 1

            where = " AND ".join(conditions)
            rows = await conn.fetch(f"""
                SELECT * FROM erp_data.invoices
                WHERE {where}
                ORDER BY invoice_date DESC
            """, *params)
            return [_row_to_dict(r) for r in rows]

    # ===================================================================
    # DASHBOARD
    # ===================================================================

    async def get_dashboard_summary(self, company_id: UUID) -> Dict[str, Any]:
        """Dashboard summary: contact counts + open invoice totals."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            contacts = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE is_active AND NOT is_deleted) AS total,
                    COUNT(*) FILTER (WHERE contact_type = 'supplier' AND is_active AND NOT is_deleted) AS suppliers,
                    COUNT(*) FILTER (WHERE contact_type = 'customer' AND is_active AND NOT is_deleted) AS customers,
                    COUNT(*) FILTER (WHERE contact_type = 'both' AND is_active AND NOT is_deleted) AS both
                FROM erp_data.contacts WHERE company_id = $1
            """, company_id)

            invoices = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(ABS(amount_residual_signed)) FILTER (WHERE invoice_direction = 'payable'), 0) AS open_ap,
                    COALESCE(SUM(amount_residual_signed) FILTER (WHERE invoice_direction = 'receivable'), 0) AS open_ar,
                    COALESCE(SUM(ABS(amount_residual_signed)) FILTER (
                        WHERE invoice_direction = 'payable' AND invoice_date_due < CURRENT_DATE
                    ), 0) AS overdue_ap,
                    COALESCE(SUM(amount_residual_signed) FILTER (
                        WHERE invoice_direction = 'receivable' AND invoice_date_due < CURRENT_DATE
                    ), 0) AS overdue_ar
                FROM erp_data.invoices
                WHERE company_id = $1
                  AND state = 'posted'
                  AND payment_state IN ('not_paid', 'partial')
                  AND is_deleted = FALSE
            """, company_id)

            return {
                "contacts": {
                    "total": contacts["total"],
                    "suppliers": contacts["suppliers"],
                    "customers": contacts["customers"],
                    "both": contacts["both"],
                },
                "invoices": {
                    "open_ap": float(invoices["open_ap"]),
                    "open_ar": float(invoices["open_ar"]),
                    "overdue_ap": float(invoices["overdue_ap"]),
                    "overdue_ar": float(invoices["overdue_ar"]),
                },
            }


# ═════════════════════════════════════════════════════════════════
# SINGLETON ACCESSOR
# ═════════════════════════════════════════════════════════════════

def get_contacts_invoices_manager() -> NeonContactsInvoicesManager:
    """Get the singleton NeonContactsInvoicesManager instance."""
    return NeonContactsInvoicesManager()
