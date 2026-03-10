"""
Handlers RPC pour le module Fixed Assets (Immobilisations).

NAMESPACE: FIXED_ASSETS

Architecture:
    Worker LLM / Frontend -> rpc_call("FIXED_ASSETS.list_assets", ...)
                          -> POST /rpc
                          -> _resolve_method("FIXED_ASSETS.list_assets")
                          -> FixedAssetRPCHandlers.list_assets()
                          -> NeonFixedAssetManager (asyncpg pool)

Endpoints disponibles:
    Models:
        - FIXED_ASSETS.list_asset_models
        - FIXED_ASSETS.get_asset_model
        - FIXED_ASSETS.create_asset_model
        - FIXED_ASSETS.update_asset_model
        - FIXED_ASSETS.delete_asset_model

    Assets:
        - FIXED_ASSETS.list_assets
        - FIXED_ASSETS.get_asset
        - FIXED_ASSETS.create_asset
        - FIXED_ASSETS.update_asset
        - FIXED_ASSETS.confirm_asset
        - FIXED_ASSETS.reset_to_draft
        - FIXED_ASSETS.dispose_asset

    Depreciation:
        - FIXED_ASSETS.get_depreciation_schedule
        - FIXED_ASSETS.run_depreciation
        - FIXED_ASSETS.generate_depreciation_pdf

    Reports:
        - FIXED_ASSETS.get_asset_report
        - FIXED_ASSETS.get_eligible_accounts
"""

import base64
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from .tools.neon_fixed_asset_manager import get_fixed_asset_manager
from .tools.depreciation_pdf import generate_depreciation_schedule_pdf

logger = logging.getLogger("fixed_assets.rpc_handlers")


# ===================================================================
# SERIALIZATION (same pattern as accounting_rpc_handlers)
# ===================================================================

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


async def _resolve(mandate_path: str):
    """Resolve mandate_path -> company_id. Returns (manager, company_id) or raises."""
    mgr = get_fixed_asset_manager()
    company_id = await mgr._resolve_company(mandate_path)
    if not company_id:
        raise ValueError(f"Company not found for mandate_path: {mandate_path}")
    return mgr, company_id


# ===================================================================
# HANDLERS CLASS
# ===================================================================

class FixedAssetRPCHandlers:
    """Handlers RPC pour le namespace FIXED_ASSETS."""

    NAMESPACE = "FIXED_ASSETS"

    # ------------------------------------------------------------------
    # ELIGIBLE ACCOUNTS
    # ------------------------------------------------------------------
    async def get_eligible_accounts(
        self, mandate_path: str, **kwargs
    ) -> Dict[str, Any]:
        """Return COA accounts eligible for fixed asset mapping."""
        try:
            mgr, company_id = await _resolve(mandate_path)
            accounts = await mgr.get_eligible_accounts(company_id)
            return {"success": True, "accounts": _serialize(accounts)}
        except Exception as e:
            logger.error("get_eligible_accounts error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # ASSET MODELS
    # ------------------------------------------------------------------
    async def list_asset_models(
        self, mandate_path: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            models = await mgr.list_asset_models(company_id)
            return {"success": True, "models": _serialize(models), "count": len(models)}
        except Exception as e:
            logger.error("list_asset_models error: %s", e)
            return {"success": False, "error": str(e)}

    async def get_asset_model(
        self, mandate_path: str, model_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            model = await mgr.get_asset_model(company_id, UUID(model_id))
            if not model:
                return {"success": False, "error": "Model not found"}
            return {"success": True, "model": _serialize(model)}
        except Exception as e:
            logger.error("get_asset_model error: %s", e)
            return {"success": False, "error": str(e)}

    async def create_asset_model(
        self, mandate_path: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.create_asset_model(company_id, kwargs)
            return {"success": True, "model": _serialize(result)}
        except Exception as e:
            logger.error("create_asset_model error: %s", e)
            return {"success": False, "error": str(e)}

    async def update_asset_model(
        self, mandate_path: str, model_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.update_asset_model(company_id, UUID(model_id), kwargs)
            return _serialize(result)
        except Exception as e:
            logger.error("update_asset_model error: %s", e)
            return {"success": False, "error": str(e)}

    async def delete_asset_model(
        self, mandate_path: str, model_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.delete_asset_model(company_id, UUID(model_id))
            return _serialize(result)
        except Exception as e:
            logger.error("delete_asset_model error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # ASSETS
    # ------------------------------------------------------------------
    async def list_assets(
        self,
        mandate_path: str,
        state: Optional[List[str]] = None,
        search: Optional[str] = None,
        model_id: Optional[str] = None,
        account_number: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            assets = await mgr.list_assets(
                company_id,
                state=state,
                search=search,
                model_id=UUID(model_id) if model_id else None,
                account_number=account_number,
                date_from=date.fromisoformat(date_from) if date_from else None,
                date_to=date.fromisoformat(date_to) if date_to else None,
            )
            return {"success": True, "assets": _serialize(assets), "count": len(assets)}
        except Exception as e:
            logger.error("list_assets error: %s", e)
            return {"success": False, "error": str(e)}

    async def get_asset(
        self, mandate_path: str, asset_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            asset = await mgr.get_asset(company_id, UUID(asset_id))
            if not asset:
                return {"success": False, "error": "Asset not found"}
            return {"success": True, "asset": _serialize(asset)}
        except Exception as e:
            logger.error("get_asset error: %s", e)
            return {"success": False, "error": str(e)}

    async def create_asset(
        self, mandate_path: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.create_asset(company_id, kwargs)
            return {"success": True, "asset": _serialize(result)}
        except Exception as e:
            logger.error("create_asset error: %s", e)
            return {"success": False, "error": str(e)}

    async def update_asset(
        self, mandate_path: str, asset_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.update_asset(company_id, UUID(asset_id), kwargs)
            return _serialize(result)
        except Exception as e:
            logger.error("update_asset error: %s", e)
            return {"success": False, "error": str(e)}

    async def confirm_asset(
        self, mandate_path: str, asset_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.confirm_asset(company_id, UUID(asset_id))
            return _serialize(result)
        except Exception as e:
            logger.error("confirm_asset error: %s", e)
            return {"success": False, "error": str(e)}

    async def reset_to_draft(
        self, mandate_path: str, asset_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Reset running asset back to draft (only if no posted lines)."""
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.reset_to_draft(company_id, UUID(asset_id))
            return _serialize(result)
        except Exception as e:
            logger.error("reset_to_draft error: %s", e)
            return {"success": False, "error": str(e)}

    async def dispose_asset(
        self,
        mandate_path: str,
        asset_id: str,
        disposal_type: str,
        disposal_date: str,
        disposal_value: float = 0,
        uid: str = "",
        collection_id: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """Dispose asset and post closing entry to ERP."""
        try:
            mgr, company_id = await _resolve(mandate_path)
            result = await mgr.dispose_asset(
                company_id,
                UUID(asset_id),
                disposal_type,
                date.fromisoformat(disposal_date),
                Decimal(str(disposal_value)),
            )

            if not result.get("success"):
                return _serialize(result)

            # Post disposal journal entry to ERP via centralized poster
            journal_entry = result.pop("journal_entry", None)
            if journal_entry:
                try:
                    from .erp.journal_entry_poster import get_journal_entry_poster

                    poster = get_journal_entry_poster()
                    erp_result = await poster.post(
                        entry=journal_entry,
                        mandate_path=mandate_path,
                        uid=uid,
                        collection_id=collection_id,
                        trigger_gl_sync=True,
                        source="disposal",
                    )

                    if erp_result.get("success"):
                        result["erp_entry_name"] = erp_result.get("erp_entry_name")
                        logger.info("Disposal posted to ERP: %s", result["erp_entry_name"])
                    else:
                        result["erp_warning"] = f"Disposal entry not posted to ERP: {erp_result.get('error')}"
                        logger.warning("Disposal ERP post failed: %s", erp_result.get("error"))
                except Exception as erp_err:
                    result["erp_warning"] = f"ERP posting error: {erp_err}"
                    logger.error("Disposal ERP post error: %s", erp_err, exc_info=True)

            return _serialize(result)
        except Exception as e:
            logger.error("dispose_asset error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # DEPRECIATION
    # ------------------------------------------------------------------
    async def get_depreciation_schedule(
        self, mandate_path: str, asset_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            lines = await mgr.get_depreciation_schedule(company_id, UUID(asset_id))
            return {"success": True, "lines": _serialize(lines), "count": len(lines)}
        except Exception as e:
            logger.error("get_depreciation_schedule error: %s", e)
            return {"success": False, "error": str(e)}

    async def run_depreciation(
        self, mandate_path: str, date_up_to: str, asset_id: str | None = None,
        uid: str | None = None, collection_id: str | None = None, **kwargs
    ) -> Dict[str, Any]:
        """Run depreciation: collect unposted lines, post each to ERP, mark posted."""
        try:
            mgr, company_id = await _resolve(mandate_path)
            target = date.fromisoformat(date_up_to)
            target_asset = UUID(asset_id) if asset_id else None

            # 1. Collect unposted lines
            run_result = await mgr.run_depreciation(company_id, target, asset_id=target_asset)
            lines_to_post = run_result.get("lines_to_post", [])

            if not lines_to_post:
                return _serialize(run_result)

            # 2. Post each line to ERP via centralized poster
            from .erp.journal_entry_poster import get_journal_entry_poster

            poster = get_journal_entry_poster()
            posted = 0
            errors = 0
            entries = []

            for line_info in lines_to_post:
                entry = {
                    "journal_code": "OD",
                    "entry_date": line_info["depreciation_date"],
                    "document_ref": line_info["entry_ref"],
                    "description": line_info["description"],
                    "currency": line_info["currency"],
                    "lines": [
                        {
                            "account_number": line_info["account_expense_number"],
                            "description": line_info["description"],
                            "debit": line_info["amount"],
                            "credit": 0,
                        },
                        {
                            "account_number": line_info["account_depreciation_number"],
                            "description": line_info["description"],
                            "debit": 0,
                            "credit": line_info["amount"],
                        },
                    ],
                }

                erp_result = await poster.post(
                    entry=entry,
                    mandate_path=mandate_path,
                    uid=uid,
                    collection_id=collection_id,
                    trigger_gl_sync=False,  # once at end
                    source="depreciation_manual",
                )

                if erp_result.get("success"):
                    erp_entry_name = erp_result.get(
                        "erp_entry_name", str(erp_result.get("erp_entry_id", ""))
                    )
                    await mgr.mark_depreciation_posted(
                        company_id, UUID(line_info["depreciation_line_id"]), erp_entry_name
                    )
                    posted += 1
                    entries.append({
                        "asset_name": line_info["asset_name"],
                        "date": line_info["depreciation_date"],
                        "amount": line_info["amount"],
                        "erp_ref": erp_entry_name,
                    })
                else:
                    errors += 1
                    logger.warning(
                        "Depreciation ERP post failed for %s: %s",
                        line_info["entry_ref"], erp_result.get("error"),
                    )

            # 3. Trigger GL sync once for the whole batch
            if posted > 0:
                from .erp.journal_entry_poster import _extract_uid

                await poster._trigger_gl_sync(
                    uid or _extract_uid(mandate_path) or "",
                    collection_id or "",
                    mandate_path,
                    "depreciation_manual",
                )

            # 4. Close fully depreciated assets
            closed = await mgr.close_fully_depreciated(company_id)

            return _serialize({
                "success": errors == 0,
                "posted_count": posted,
                "total_amount": run_result.get("total_amount", 0),
                "errors": errors,
                "assets_closed": closed,
                "entries": entries,
            })
        except Exception as e:
            logger.error("run_depreciation error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # DEPRECIATION REVERSAL
    # ------------------------------------------------------------------
    async def reverse_depreciation(
        self, mandate_path: str, uid: str | None = None, collection_id: str | None = None,
        line_ids: list[str] | None = None,
        date_from: str | None = None, date_to: str | None = None,
        asset_id: str | None = None, **kwargs
    ) -> dict:
        """Reverse posted depreciation lines by creating counter-entries in ERP."""
        try:
            mgr, company_id = await _resolve(mandate_path)

            target_line_ids = [UUID(lid) for lid in line_ids] if line_ids else None
            target_asset = UUID(asset_id) if asset_id else None
            d_from = date.fromisoformat(date_from) if date_from else None
            d_to = date.fromisoformat(date_to) if date_to else None

            lines = await mgr.get_lines_for_reversal(
                company_id,
                line_ids=target_line_ids,
                date_from=d_from,
                date_to=d_to,
                asset_id=target_asset,
            )

            if not lines:
                return {"success": True, "reversed_count": 0, "message": "No lines to reverse"}

            from .erp.journal_entry_poster import get_journal_entry_poster
            poster = get_journal_entry_poster()

            reversed_count = 0
            errors = 0
            entries = []

            for line in lines:
                asset_ref = line["asset_reference"] or str(line["asset_id"])[:8]
                dep_date = line["depreciation_date"]
                original_ref = line["gl_entry_ref"] or ""
                reversal_ref = f"REV-{asset_ref}-{dep_date.strftime('%Y%m')}"
                period_label = dep_date.strftime("%m/%Y")
                description = f"Extourne amortissement {line['asset_name']} - {period_label}"
                if original_ref:
                    description += f" (réf. {original_ref})"

                entry = {
                    "journal_code": "OD",
                    "entry_date": dep_date.isoformat(),
                    "document_ref": reversal_ref,
                    "description": description,
                    "currency": line["currency"],
                    "lines": [
                        {
                            "account_number": line["account_expense_number"],
                            "description": description,
                            "debit": 0,
                            "credit": float(line["amount"]),
                        },
                        {
                            "account_number": line["account_depreciation_number"],
                            "description": description,
                            "debit": float(line["amount"]),
                            "credit": 0,
                        },
                    ],
                }

                erp_result = await poster.post(
                    entry=entry,
                    mandate_path=mandate_path,
                    uid=uid,
                    collection_id=collection_id,
                    trigger_gl_sync=False,
                    source="depreciation_reversal",
                )

                if erp_result.get("success"):
                    erp_entry_name = erp_result.get(
                        "erp_entry_name", str(erp_result.get("erp_entry_id", ""))
                    )
                    await mgr.mark_depreciation_reversed(
                        UUID(str(line["id"])), erp_entry_name, reversal_ref
                    )
                    reversed_count += 1
                    entries.append({
                        "asset_name": line["asset_name"],
                        "date": dep_date.isoformat(),
                        "amount": float(line["amount"]),
                        "original_ref": original_ref,
                        "reversal_ref": erp_entry_name,
                    })
                else:
                    errors += 1
                    logger.warning(
                        "Reversal ERP post failed for %s: %s",
                        reversal_ref, erp_result.get("error"),
                    )

            # GL sync once at end
            if reversed_count > 0:
                from .erp.journal_entry_poster import _extract_uid
                await poster._trigger_gl_sync(
                    uid or _extract_uid(mandate_path) or "",
                    collection_id or "",
                    mandate_path,
                    "depreciation_reversal",
                )

            return _serialize({
                "success": errors == 0,
                "reversed_count": reversed_count,
                "errors": errors,
                "entries": entries,
            })
        except Exception as e:
            logger.error("reverse_depreciation error: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # DEPRECIATION PDF
    # ------------------------------------------------------------------
    async def generate_depreciation_pdf(
        self,
        mandate_path: str,
        asset_id: str,
        language: str = "fr",
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate a depreciation schedule PDF for a given asset.

        Returns base64-encoded PDF content and a suggested filename.
        """
        try:
            mgr, company_id = await _resolve(mandate_path)

            # 1. Get asset with full details
            asset = await mgr.get_asset(company_id, UUID(asset_id))
            if not asset:
                return {"success": False, "error": "Asset not found"}

            # 2. Get depreciation lines
            lines = await mgr.get_depreciation_schedule(company_id, UUID(asset_id))

            # 3. Get company name from core.companies
            pool = await mgr._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT company_name, address FROM core.companies WHERE id = $1",
                    company_id,
                )
            company_name = row["company_name"] if row else "Company"
            company_address = (row["address"] or "") if row else ""

            # 4. Serialize asset & lines for the PDF generator (Decimal/date -> float/str)
            asset_data = _serialize(asset)
            lines_data = _serialize(lines)

            # 5. Generate PDF
            pdf_buffer = generate_depreciation_schedule_pdf(
                asset=asset_data,
                lines=lines_data,
                company_name=company_name,
                company_address=company_address,
                language=language,
            )

            # 6. Encode to base64
            pdf_base64 = base64.b64encode(pdf_buffer.read()).decode("utf-8")

            # 7. Build filename: depreciation_{sanitized_name}_{date}.pdf
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", asset_data.get("name", "asset"))
            today_str = date.today().strftime("%Y%m%d")
            filename = f"depreciation_{safe_name}_{today_str}.pdf"

            return {
                "success": True,
                "pdf_base64": pdf_base64,
                "filename": filename,
                "asset_name": asset_data.get("name", ""),
            }
        except Exception as e:
            logger.error("generate_depreciation_pdf error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # REPORT
    # ------------------------------------------------------------------
    async def get_asset_report(
        self, mandate_path: str, period_date: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            mgr, company_id = await _resolve(mandate_path)
            # Accept both "YYYY-MM" and "YYYY-MM-DD"
            pd = period_date.strip()
            if len(pd) == 7:  # "YYYY-MM"
                pd = pd + "-01"
            result = await mgr.get_asset_report(
                company_id, date.fromisoformat(pd)
            )
            return _serialize(result)
        except Exception as e:
            logger.error("get_asset_report error: %s", e)
            return {"success": False, "error": str(e)}


# ===================================================================
# SINGLETON ACCESSOR
# ===================================================================

_handlers: Optional[FixedAssetRPCHandlers] = None


def get_fixed_asset_handlers() -> FixedAssetRPCHandlers:
    global _handlers
    if _handlers is None:
        _handlers = FixedAssetRPCHandlers()
    return _handlers
