"""
ERPProvider — Interface abstraite et resolution du provider ERP.

Pattern Strategy : chaque ERP implemente la meme interface.
Le resolver determine quel adapter utiliser via mandate_path → erp_type.

Usage:
    provider = await get_erp_provider(uid, collection_id, mandate_path)
    result = await provider.post_journal_entry(entry)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger("erp.provider")


class ERPProvider(ABC):
    """Interface abstraite pour tous les ERP (Odoo, Sage, Abacus, ...)."""

    @property
    @abstractmethod
    def erp_type(self) -> str:
        """Retourne le type d'ERP ('odoo', 'sage', 'abacus')."""
        ...

    @abstractmethod
    async def post_journal_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Poste une ecriture comptable vers l'ERP.

        Args:
            entry: Ecriture au format standard KLK, enrichie avec les IDs ERP resolus:
                - journal_code, entry_date, description, document_ref, currency
                - lines[]: account_number, description, debit, credit
                - _erp_journal_id (int): ID journal resolu
                - lines[]._erp_account_id (int): ID compte resolu

        Returns:
            Dict: {success: bool, erp_entry_id: str, message: str}
        """
        ...

    # ─── Methodes futures (a ajouter progressivement) ───
    # async def list_tax_id_all(self, ...) -> ...: ...
    # async def check_account_existence(self, ...) -> ...: ...
    # async def generate_ap_invoice(self, ...) -> ...: ...


async def get_erp_provider(
    uid: str,
    collection_id: str,
    mandate_path: Optional[str] = None,
) -> ERPProvider:
    """
    Resout et retourne le bon provider ERP pour un utilisateur/societe.

    Resolution:
    1. Obtient une connexion ERP via ERPConnectionManager (existant)
    2. Determine le type d'ERP (pour l'instant: toujours Odoo)
    3. Retourne l'adapter correspondant

    Args:
        uid: Firebase user ID
        collection_id: Company/collection ID
        mandate_path: Chemin du mandat Firebase (optionnel, pour resolution Neon)

    Returns:
        ERPProvider instance
    """
    from ..erp_service import ERPConnectionManager

    manager = ERPConnectionManager()
    connection = manager.get_connection(uid, collection_id)

    if not connection:
        raise ConnectionError(
            f"ERP connection failed for uid={uid} collection={collection_id}"
        )

    # Pour l'instant, seul Odoo est supporte
    # Futur: lire erp_type depuis mandate_path/erp/ ou company settings
    from .adapters.odoo_adapter import OdooAdapter

    return OdooAdapter(connection)


async def resolve_erp_ids(
    entry: Dict[str, Any],
    mandate_path: str,
) -> Dict[str, Any]:
    """
    Resout les IDs standard KLK en IDs ERP.

    Transformation:
    - account_number → erp_account_id (via core.chart_of_accounts.erp_account_id)
    - journal_code → erp_journal_id (via accounting.journals.erp_journal_id)

    Modifie entry in-place et retourne l'entry enrichie.

    Args:
        entry: Ecriture au format KLK
        mandate_path: Pour resolution company_id Neon

    Returns:
        entry enrichie avec _erp_journal_id et lines[]._erp_account_id
    """
    from ..tools.neon_accounting_manager import get_neon_accounting_manager

    manager = get_neon_accounting_manager()
    company_id = await manager.get_company_id_from_mandate_path(mandate_path)

    if not company_id:
        raise ValueError(f"Company not found for mandate_path: {mandate_path}")

    pool = await manager.get_pool()

    # 1. Resolve journal_code → erp_journal_id
    journal_code = entry.get("journal_code", "")
    row = await pool.fetchrow(
        "SELECT erp_journal_id FROM accounting.journals "
        "WHERE company_id = $1 AND journal_code = $2",
        company_id, journal_code,
    )
    if row and row["erp_journal_id"]:
        entry["_erp_journal_id"] = int(row["erp_journal_id"])
    else:
        raise ValueError(
            f"Journal '{journal_code}' not found or missing erp_journal_id "
            f"for company_id={company_id}"
        )

    # 2. Resolve account_number → erp_account_id for each line
    for line in entry.get("lines", []):
        account_number = line.get("account_number", "")
        acc_row = await pool.fetchrow(
            "SELECT erp_account_id FROM core.chart_of_accounts "
            "WHERE company_id = $1 AND account_number = $2 AND is_active",
            company_id, account_number,
        )
        if acc_row and acc_row["erp_account_id"]:
            line["_erp_account_id"] = int(acc_row["erp_account_id"])
        else:
            raise ValueError(
                f"Account '{account_number}' not found or missing erp_account_id "
                f"for company_id={company_id}"
            )

    logger.info(
        "[ERP_RESOLVE] IDs resolus: journal=%s→%s, %d comptes resolus",
        journal_code, entry.get("_erp_journal_id"),
        len(entry.get("lines", [])),
    )

    return entry
