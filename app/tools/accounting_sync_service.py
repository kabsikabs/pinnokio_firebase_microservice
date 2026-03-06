"""
AccountingSyncService - Synchronisation GL depuis ERP vers Neon PostgreSQL.

Orchestre le flux complet:
1. Résolution des credentials ERP via mandate_path
2. Connexion Odoo (XML-RPC)
3. Fetch account.move.line (GL) + account.journal
4. Transformation + hash SHA-256
5. Persistence via NeonAccountingManager (incremental upsert)
6. Refresh period_balances

Déclenché par:
- Event Redis gl_sync_requested (depuis SYNC_GL_FROM_ERP tool Worker)
- Futur cron nocturne
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Champs GL renvoyés par Odoo account.move.line
_GL_FIELDS = [
    "id", "date", "account_type", "currency_id", "parent_state",
    "amount_currency", "currency_rate", "name", "debit", "credit", "balance",
    "account_id", "journal_id", "move_id", "company_id", "write_date",
    "full_reconcile_id", "partner_id",
]

# Champs journal renvoyés par Odoo account.journal
_JOURNAL_FIELDS = ["id", "code", "name", "type", "default_account_id", "company_id"]

# Mapping Odoo journal type → KLK journal_category
_JOURNAL_TYPE_MAP = {
    "sale": "sale",
    "purchase": "purchase",
    "bank": "bank",
    "cash": "cash",
    "general": "general",
}


def _compute_gl_hash(entry: Dict[str, Any]) -> str:
    """Compute SHA-256 hash of business fields (exclude pinnokio control fields)."""
    excluded = {
        "pinnokio_checked_time", "pinnokio_hash", "pinnokio_version",
        "is_deleted", "created_at", "updated_at", "id",
    }
    business = {k: v for k, v in entry.items() if k not in excluded}
    return hashlib.sha256(
        json.dumps(business, sort_keys=True, default=str).encode()
    ).hexdigest()


def _extract_tuple_field(value, index: int = 0):
    """Extract value from Odoo tuple field (id, name). Returns None if empty."""
    if isinstance(value, (list, tuple)) and len(value) > index:
        return value[index]
    return value


def _safe_float(value, default: float = 0.0) -> float:
    """Convert to float safely."""
    try:
        if value is None or value is False:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _odoo_gl_to_neon_entry(record: Dict[str, Any], journal_id_to_code: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """
    Transform an Odoo account.move.line record into the format
    expected by NeonAccountingManager.incremental_sync_gl_entries().

    Uses English column names (post-migration 018).

    Args:
        record: Odoo record dict
        journal_id_to_code: mapping {journal_erp_id: journal_code} from account.journal
    """
    # Extract tuple fields (Odoo returns (id, name) tuples)
    account_id_tuple = record.get("account_id")
    journal_id_tuple = record.get("journal_id")
    move_id_tuple = record.get("move_id")
    currency_tuple = record.get("currency_id")
    partner_tuple = record.get("partner_id")

    # Resolve journal_code via mapping (preferred) or fallback to tuple name truncated
    journal_code = ""
    if journal_id_to_code and isinstance(journal_id_tuple, (list, tuple)) and journal_id_tuple:
        jid = int(journal_id_tuple[0])
        journal_code = journal_id_to_code.get(jid, "")
    if not journal_code:
        # Fallback: use tuple display name, truncated to 10 chars
        raw = _extract_tuple_field(journal_id_tuple, 1) if isinstance(journal_id_tuple, (list, tuple)) else str(journal_id_tuple or "")
        journal_code = str(raw)[:10]

    # Devise étrangère
    currency_name = _extract_tuple_field(currency_tuple, 1) if isinstance(currency_tuple, (list, tuple)) else "CHF"
    currency_erp_id = int(currency_tuple[0]) if isinstance(currency_tuple, (list, tuple)) and currency_tuple else None
    amount_currency = _safe_float(record.get("amount_currency"))
    # currency_rate natif Odoo (ex: 1.0956 pour EUR quand base=CHF)
    # Fallback 1.0 si absent/0/False (devise de base ou champ non exposé)
    raw_rate = _safe_float(record.get("currency_rate"), default=0.0)
    exchange_rate = raw_rate if raw_rate > 0 else 1.0

    entry = {
        "entry_id": str(record.get("id", "")),
        "entry_date": record.get("date"),
        "last_update_date": record.get("write_date"),
        "journal_code": journal_code,
        "document_ref": _extract_tuple_field(move_id_tuple, 1) if isinstance(move_id_tuple, (list, tuple)) else record.get("move_ref"),
        "account_number": _extract_tuple_field(account_id_tuple, 1).split(" ")[0] if isinstance(account_id_tuple, (list, tuple)) and account_id_tuple else "",
        "account_name": _extract_tuple_field(account_id_tuple, 1) if isinstance(account_id_tuple, (list, tuple)) else "",
        "description": record.get("name") or "",
        "partner_name": _extract_tuple_field(partner_tuple, 1) if isinstance(partner_tuple, (list, tuple)) else None,
        "debit": _safe_float(record.get("debit")),
        "credit": _safe_float(record.get("credit")),
        "reconciliation_ref": (lambda v: str(v)[:20] if v else None)(_extract_tuple_field(record.get("full_reconcile_id"), 1) if isinstance(record.get("full_reconcile_id"), (list, tuple)) else None),
        "entry_state": _map_parent_state(record.get("parent_state")),
        "currency": currency_name,
        "currency_erp_id": currency_erp_id,
        "amount_currency_value": amount_currency if amount_currency != 0.0 else None,
        "exchange_rate": exchange_rate,
    }

    return entry


def _map_parent_state(state) -> str:
    """Map Odoo parent_state to our entry_state."""
    mapping = {
        "posted": "posted",
        "draft": "draft",
        "cancel": "cancel",
    }
    return mapping.get(str(state or ""), "posted")


def _odoo_journal_to_neon(record: Dict[str, Any]) -> Dict[str, Any]:
    """Transform an Odoo account.journal record for NeonAccountingManager.upsert_journals()."""
    return {
        "journal_code": record.get("code", ""),
        "journal_name": _extract_tuple_field(record.get("name"), 1) if isinstance(record.get("name"), (list, tuple)) else str(record.get("name", "")),
        "journal_type": _JOURNAL_TYPE_MAP.get(record.get("type", ""), "general"),
        "erp_journal_id": str(record.get("id", "")),
        "erp_source": "odoo",
        "is_active": True,
    }


async def sync_gl_from_erp(
    uid: str,
    mandate_path: str,
    collection_id: str,
    force_full: bool = False,
) -> Dict[str, Any]:
    """
    Synchronise le grand livre depuis l'ERP Odoo vers Neon PostgreSQL.

    Args:
        uid: Firebase user ID
        mandate_path: Chemin du mandat Firebase
        collection_id: collection_name / space_id
        force_full: True pour réconciliation complète (vs incrémentale)

    Returns:
        Dict avec statistiques de sync ou erreur
    """
    from .neon_accounting_manager import get_neon_accounting_manager

    logger.info(
        "[GL_SYNC] START uid=%s mandate=%s force_full=%s",
        uid, mandate_path, force_full,
    )

    manager = get_neon_accounting_manager()

    # 1. Résoudre company_id Neon
    company_id = await manager.get_company_id_from_mandate_path(mandate_path)
    if not company_id:
        msg = f"Company not found for mandate_path={mandate_path}"
        logger.error("[GL_SYNC] %s", msg)
        return {"success": False, "error": msg}

    # 2. Obtenir la connexion ERP
    erp_connection = _get_erp_connection(uid, collection_id)
    if not erp_connection:
        msg = f"ERP connection failed for uid={uid} collection={collection_id}"
        logger.error("[GL_SYNC] %s", msg)
        return {"success": False, "error": msg}

    # 3. Mettre à jour sync_metadata → running
    await manager.update_sync_metadata(
        company_id, "gl",
        sync_status="running",
    )

    try:
        # 3b. Sync journals FIRST to build journal_id → code mapping
        journal_id_to_code = {}
        journals_result = await sync_gl_from_erp_async_journals(
            manager, erp_connection, company_id
        )
        if journals_result:
            journal_id_to_code = journals_result.get("id_to_code", {})

        result = await _do_gl_sync(manager, erp_connection, company_id, force_full, journal_id_to_code)
        if journals_result:
            result["journals"] = journals_result

        # 5. Mettre à jour sync_metadata → idle
        gl_stats = result.get("gl", {})
        total = gl_stats.get("added", 0) + gl_stats.get("modified", 0) + gl_stats.get("unchanged", 0)
        await manager.update_sync_metadata(
            company_id, "gl",
            sync_status="idle",
            last_sync_time=datetime.now(timezone.utc),
            total_entries=total,
            active_entries=total,
            last_changes=gl_stats,
        )

        result["success"] = True
        logger.info("[GL_SYNC] DONE company=%s result=%s", company_id, result)
        return result

    except Exception as e:
        logger.error("[GL_SYNC] ERROR company=%s: %s", company_id, e, exc_info=True)
        await manager.update_sync_metadata(
            company_id, "gl",
            sync_status="error",
            last_error=str(e),
        )
        return {"success": False, "error": str(e)}


def _get_erp_connection(uid: str, collection_id: str):
    """Get an Odoo connection via ERPConnectionManager."""
    try:
        from ..erp_service import ERPConnectionManager

        manager = ERPConnectionManager()
        connection = manager.get_connection(uid, collection_id)
        if connection:
            logger.info("[GL_SYNC] ERP connection OK for uid=%s collection=%s", uid, collection_id)
        return connection
    except Exception as e:
        logger.error("[GL_SYNC] ERP connection error: %s", e, exc_info=True)
        return None


async def _do_gl_sync(manager, erp_connection, company_id: UUID, force_full: bool, journal_id_to_code: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """
    Fetch GL entries from Odoo and persist via NeonAccountingManager.
    """
    result = {}

    # Build domain filter
    domain = []

    if not force_full:
        # Incremental: fetch since last sync
        sync_meta = await manager.get_sync_metadata(company_id, "gl")
        if sync_meta and sync_meta.get("last_sync_time"):
            # Marge de sécurité: 1 jour avant
            from datetime import timedelta
            last_sync = sync_meta["last_sync_time"]
            if isinstance(last_sync, str):
                last_sync = datetime.fromisoformat(last_sync)
            safe_date = (last_sync - timedelta(days=1)).strftime("%Y-%m-%d")
            domain.append(("write_date", ">=", safe_date))
            logger.info("[GL_SYNC] Incremental since %s (safe_date=%s)", last_sync, safe_date)

    # Fetch GL entries from Odoo
    logger.info("[GL_SYNC] Fetching account.move.line with domain=%s", domain)
    gl_df, _coa_df = erp_connection.fetch_financial_records(domain=domain)

    if gl_df is None or gl_df.empty:
        logger.info("[GL_SYNC] No GL entries returned from ERP")
        result["gl"] = {"added": 0, "modified": 0, "unchanged": 0, "total_fetched": 0}
        return result

    logger.info("[GL_SYNC] Fetched %d GL entries from ERP", len(gl_df))

    # Transform Odoo records → Neon format (with journal_id→code mapping)
    entries = []
    for _, row in gl_df.iterrows():
        entry = _odoo_gl_to_neon_entry(row.to_dict(), journal_id_to_code)
        entries.append(entry)

    # Persist via incremental sync
    gl_stats = await manager.incremental_sync_gl_entries(company_id, entries)
    gl_stats["total_fetched"] = len(entries)

    # Reconciliation des suppressions: lors d'un full sync, marquer comme
    # is_deleted=True les ecritures Neon absentes de l'ERP.
    if force_full and entries:
        active_ids = [e["entry_id"] for e in entries if e.get("entry_id")]
        deleted_count = await manager.mark_deleted_entries(company_id, active_ids)
        gl_stats["deleted"] = deleted_count
        if deleted_count:
            logger.info("[GL_SYNC] Full sync: %d entries marked as deleted (absent from ERP)", deleted_count)

    result["gl"] = gl_stats

    # Refresh period_balances for impacted years
    if gl_stats.get("added", 0) > 0 or gl_stats.get("modified", 0) > 0:
        impacted_years = set()
        for entry in entries:
            date_val = entry.get("entry_date") or entry.get("date_ecriture")
            if date_val:
                try:
                    if isinstance(date_val, str):
                        year = int(date_val[:4])
                    else:
                        year = date_val.year
                    impacted_years.add(year)
                except (ValueError, AttributeError):
                    pass

        for year in impacted_years:
            rows = await manager.refresh_period_balances(company_id, year)
            logger.info("[GL_SYNC] Refreshed period_balances year=%d rows=%d", year, rows)

    return result


async def sync_gl_from_erp_async_journals(
    manager, erp_connection, company_id: UUID
) -> Optional[Dict[str, Any]]:
    """Async version of journal sync for use from async context.

    Returns dict with upsert stats + id_to_code mapping {erp_journal_id: journal_code}.
    """
    try:
        # 1. Always fetch ALL journals for id→code mapping (used by GL transform)
        full_domain = [["company_id", "=", erp_connection.company_id]]
        all_journal_records = erp_connection.execute_kw(
            "account.journal", "search_read",
            [full_domain],
            {"fields": _JOURNAL_FIELDS},
        )

        if not all_journal_records:
            return None

        # Build COMPLETE id→code mapping for GL transform
        id_to_code: Dict[int, str] = {}
        for rec in all_journal_records:
            jid = rec.get("id")
            code = rec.get("code", "")
            if jid and code:
                id_to_code[int(jid)] = str(code)

        # 2. Determine which journals to upsert (incremental if possible)
        journals_to_upsert = all_journal_records
        journals_meta = await manager.get_sync_metadata(company_id, "journals")
        if journals_meta and journals_meta.get("last_sync_time"):
            from datetime import timedelta
            last_sync = journals_meta["last_sync_time"]
            if isinstance(last_sync, str):
                last_sync = datetime.fromisoformat(last_sync)
            safe_date = (last_sync - timedelta(days=1)).strftime("%Y-%m-%d")
            # Filter locally: only upsert journals modified since safe_date
            journals_to_upsert = [
                rec for rec in all_journal_records
                if str(rec.get("write_date", "")) >= safe_date
            ]
            logger.info(
                "[GL_SYNC] Journals incremental: %d/%d modified since %s",
                len(journals_to_upsert), len(all_journal_records), safe_date,
            )

        journals = [_odoo_journal_to_neon(rec) for rec in journals_to_upsert]
        logger.info("[GL_SYNC] Fetched %d journals from ERP (id_to_code: %d mappings)", len(all_journal_records), len(id_to_code))
        upsert_result = await manager.upsert_journals(company_id, journals) if journals else {"added": 0, "modified": 0, "unchanged": 0}

        # Update sync_metadata for journals
        if upsert_result is None:
            upsert_result = {}
        total_journals = len(journals)
        await manager.update_sync_metadata(
            company_id, "journals",
            sync_status="idle",
            last_sync_time=datetime.now(timezone.utc),
            total_entries=total_journals,
            active_entries=total_journals,
            last_changes=upsert_result,
        )

        upsert_result["id_to_code"] = id_to_code
        return upsert_result

    except Exception as e:
        logger.error("[GL_SYNC] Journals async sync error: %s", e, exc_info=True)
        return None
