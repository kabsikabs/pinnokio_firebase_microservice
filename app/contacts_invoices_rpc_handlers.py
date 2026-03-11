"""
Handlers RPC pour les modules Contacts + Invoices + Aging.

NAMESPACES: CONTACTS, INVOICES

Architecture:
    Frontend / Worker -> rpc_call("CONTACTS.list_contacts", ...)
                      -> POST /rpc
                      -> _resolve_method("CONTACTS.list_contacts")
                      -> contacts_invoices_rpc_handlers.list_contacts()
                      -> NeonContactsInvoicesManager (asyncpg pool)

IMPORTANT: Les handlers retournent directement les données (pas de wrapper {"ok", "data"}).
           Le wrapping est fait par RpcResponse dans main.py (ligne 1328).
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from .tools.neon_accounting_manager import get_neon_accounting_manager
from .tools.neon_contacts_invoices_manager import get_contacts_invoices_manager

logger = logging.getLogger("contacts_invoices.rpc_handlers")


def _serialize(value: Any) -> Any:
    """Serialise les valeurs pour JSON."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if hasattr(value, "keys") and callable(value.keys):
        return {k: _serialize(value[k]) for k in value.keys()}
    return value


class ContactsInvoicesRPCHandlers:
    """Handlers RPC pour CONTACTS + INVOICES."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def _resolve_company(self, mandate_path: str) -> UUID:
        mgr = get_neon_accounting_manager()
        cid = await mgr.get_company_id_from_mandate_path(mandate_path)
        if not cid:
            raise ValueError(f"Company not found for mandate_path={mandate_path}")
        return cid

    # ═══════════════════════════════════════════════════════════
    # CONTACTS
    # ═══════════════════════════════════════════════════════════

    async def list_contacts(self, mandate_path: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.list_contacts(
            cid,
            contact_type=kwargs.get("contact_type"),
            search_text=kwargs.get("search_text"),
            country_code=kwargs.get("country_code"),
            is_active=kwargs.get("is_active", True),
            has_open_balance=kwargs.get("has_open_balance", False),
            page=kwargs.get("page", 1),
            page_size=kwargs.get("page_size", 25),
            sort_by=kwargs.get("sort_by", "name"),
        )
        return _serialize(result)

    async def get_contact(self, mandate_path: str, contact_id: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.get_contact(cid, UUID(contact_id))
        if not result:
            raise ValueError("Contact not found")
        return _serialize(result)

    async def search_contacts(self, mandate_path: str, search_term: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.search_contacts(cid, search_term, limit=kwargs.get("limit", 50))
        return _serialize(result)

    # ═══════════════════════════════════════════════════════════
    # INVOICES
    # ═══════════════════════════════════════════════════════════

    async def list_invoices(self, mandate_path: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.list_invoices(
            cid,
            direction=kwargs.get("direction"),
            state=kwargs.get("state"),
            payment_state=kwargs.get("payment_state"),
            contact_id=UUID(kwargs["contact_id"]) if kwargs.get("contact_id") else None,
            date_from=kwargs.get("date_from"),
            date_to=kwargs.get("date_to"),
            search_text=kwargs.get("search_text"),
            page=kwargs.get("page", 1),
            page_size=kwargs.get("page_size", 25),
            sort_by=kwargs.get("sort_by", "invoice_date"),
            sort_dir=kwargs.get("sort_dir", "desc"),
        )
        return _serialize(result)

    async def get_invoice(self, mandate_path: str, invoice_id: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.get_invoice(cid, UUID(invoice_id))
        if not result:
            raise ValueError("Invoice not found")
        return _serialize(result)

    async def by_contact(self, mandate_path: str, contact_id: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.get_invoices_by_contact(
            cid, UUID(contact_id),
            direction=kwargs.get("direction"),
            payment_state=kwargs.get("payment_state"),
        )
        return _serialize(result)

    async def search_invoices(self, mandate_path: str, search_term: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.list_invoices(
            cid, search_text=search_term,
            page=1, page_size=kwargs.get("limit", 50),
        )
        return _serialize(result)

    # ═══════════════════════════════════════════════════════════
    # AGING
    # ═══════════════════════════════════════════════════════════

    async def get_aging(self, mandate_path: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        direction = kwargs.get("direction", "payable")
        as_of = kwargs.get("as_of_date")
        as_of_date = date.fromisoformat(as_of) if as_of else None
        result = await mgr.compute_aging_snapshot(cid, as_of_date=as_of_date, direction=direction)
        return _serialize(result)

    async def get_aging_latest(self, mandate_path: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.get_latest_aging(cid, direction=kwargs.get("direction", "payable"))
        if not result:
            raise ValueError("No aging snapshot available")
        return _serialize(result)

    async def get_aging_trend(self, mandate_path: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.get_aging_trend(
            cid,
            direction=kwargs.get("direction", "payable"),
            months=kwargs.get("months", 6),
        )
        return _serialize(result)

    # ═══════════════════════════════════════════════════════════
    # DASHBOARD
    # ═══════════════════════════════════════════════════════════

    async def dashboard_summary(self, mandate_path: str, **kwargs) -> dict:
        cid = await self._resolve_company(mandate_path)
        mgr = get_contacts_invoices_manager()
        result = await mgr.get_dashboard_summary(cid)
        return _serialize(result)

    # ═══════════════════════════════════════════════════════════
    # SYNC
    # ═══════════════════════════════════════════════════════════

    async def trigger_sync(self, mandate_path: str, **kwargs) -> dict:
        """Trigger a manual sync of contacts + invoices from ERP."""
        from .tools.contacts_invoices_sync_service import sync_contacts_invoices

        uid = kwargs.get("uid", "")
        collection_id = kwargs.get("collection_id", "")
        force_full = kwargs.get("full_sync", False)

        result = await sync_contacts_invoices(uid, mandate_path, collection_id, force_full)
        return _serialize(result)

    async def sync_status(self, mandate_path: str, **kwargs) -> dict:
        """Get sync status for contacts + invoices."""
        cid = await self._resolve_company(mandate_path)
        mgr = get_neon_accounting_manager()
        contacts_meta = await mgr.get_sync_metadata(cid, "contacts")
        invoices_meta = await mgr.get_sync_metadata(cid, "invoices")
        return {
            "contacts": _serialize(contacts_meta),
            "invoices": _serialize(invoices_meta),
        }


# Singleton accessor
_handlers = None

def get_contacts_invoices_handlers() -> ContactsInvoicesRPCHandlers:
    global _handlers
    if _handlers is None:
        _handlers = ContactsInvoicesRPCHandlers()
    return _handlers
