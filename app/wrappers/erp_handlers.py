"""
ERP Handlers - Wrappers for ERPService methods.

Called by COA handlers (save_changes, sync_from_erp) to interact with ERP.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def handle_update_coa_structure(
    uid: str,
    company_id: str,
    modified_rows: dict,
    client_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrapper async pour ERPService.update_coa_structure (methode sync)."""
    from app.erp_service import ERPService

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: ERPService.update_coa_structure(
            user_id=uid,
            company_id=company_id,
            modified_rows=modified_rows,
            client_uuid=client_uuid,
        ),
    )
    return result


async def handle_sync_coa_from_erp(
    uid: str,
    company_id: str,
    client_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrapper pour ERPService.sync_coa_from_erp (deja async)."""
    from app.erp_service import ERPService

    result = await ERPService.sync_coa_from_erp(
        user_id=uid,
        company_id=company_id,
        client_uuid=client_uuid,
    )
    return result
