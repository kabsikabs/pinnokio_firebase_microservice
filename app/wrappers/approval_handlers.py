"""
Approval Handlers - Wrapper Layer
==================================

Handlers WebSocket pour la gestion des approbations Router/Banker/APbookeeper.
Permet l'envoi des décisions d'approbation depuis le dashboard Next.js.

NAMESPACE: APPROVAL

Architecture:
    Frontend (Next.js) → WebSocket → approval_handlers.py → FirebaseManagement/RPC

Events gérés:
    - approval.list: Liste des approbations en attente
    - approval.send_router: Envoi approbations Router
    - approval.send_banker: Envoi approbations Banker
    - approval.send_apbookeeper: Envoi approbations APbookeeper
    - approval.result: Résultat d'envoi (broadcast)

Author: Migration Agent
Created: 2026-01-18
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..firebase_providers import FirebaseManagement
from ..redis_client import get_redis
from ..ws_events import WS_EVENTS
from ..ws_hub import hub

logger = logging.getLogger("approval.handlers")


# ============================================
# CONSTANTS
# ============================================

TTL_APPROVALS_CACHE = 30  # 30 seconds


# ============================================
# HELPERS
# ============================================

def _serialize_value(value: Any) -> Any:
    """
    Serialize a value for JSON, handling Firestore DatetimeWithNanoseconds.

    Recursively processes dicts and lists.
    """
    if value is None:
        return None

    # Handle Firestore DatetimeWithNanoseconds and standard datetime
    if hasattr(value, 'isoformat'):
        return value.isoformat()

    # Handle dicts recursively
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}

    # Handle lists recursively
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]

    # Return primitive types as-is
    return value


# ============================================
# SINGLETON
# ============================================

_approval_handlers_instance: Optional["ApprovalHandlers"] = None


def get_approval_handlers() -> "ApprovalHandlers":
    """Singleton accessor pour les handlers approval."""
    global _approval_handlers_instance
    if _approval_handlers_instance is None:
        _approval_handlers_instance = ApprovalHandlers()
    return _approval_handlers_instance


class ApprovalHandlers:
    """
    Handlers pour le namespace APPROVAL.

    Méthodes:
    - get_pending_approvals: Liste les approbations en attente par département
    - send_router_approvals: Envoie les approbations Router
    - send_banker_approvals: Envoie les approbations Banker
    - send_apbookeeper_approvals: Envoie les approbations APbookeeper
    """

    NAMESPACE = "APPROVAL"

    # ============================================
    # GET PENDING APPROVALS
    # ============================================

    async def get_pending_approvals(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Récupère les approbations en attente par département.

        RPC: APPROVAL.get_pending_approvals

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            force_refresh: Si True, invalide le cache et recharge depuis Firestore

        Returns:
            {
                "success": True,
                "data": {
                    "router": {"items": [...], "count": N, "enabled": True},
                    "banker": {"items": [...], "count": N, "enabled": True},
                    "apbookeeper": {"items": [...], "count": N, "enabled": True}
                }
            }
        """
        try:
            redis = get_redis()
            cache_key = f"approvals:{company_id}"

            # Invalidate cache if force_refresh
            if force_refresh:
                redis.delete(cache_key)
                logger.info(f"APPROVAL.get_pending_approvals force_refresh - cache invalidated for {company_id}")

            # Check cache
            cached = redis.get(cache_key)
            if cached:
                import json
                data = json.loads(cached if isinstance(cached, str) else cached.decode())
                logger.info(f"APPROVAL.get_pending_approvals company_id={company_id} source=cache")
                return {"success": True, "data": data}

            # Fetch from Firebase
            firebase = FirebaseManagement()
            pending_path = f"{mandate_path}/approval_pendinglist"

            pending_items = await asyncio.to_thread(
                firebase.list_collection,
                pending_path
            )

            if not pending_items:
                pending_items = []

            # Group by department based on document ID prefix
            # Document IDs follow pattern: router_{id}, apbookeeper_{id}, banker_{id}
            router_items = []
            banker_items = []
            apbookeeper_items = []

            for item in pending_items:
                # Get document ID - department is determined by ID prefix, not a field
                doc_id = item.get("id", "")

                if doc_id.startswith("router_"):
                    # Use base formatter for Router (already has all fields)
                    approval_item = self._format_approval_item(item)
                    router_items.append(approval_item)
                elif doc_id.startswith("banker_") or doc_id.startswith("bank_"):
                    # Use specialized Banker formatter with mode-specific fields
                    approval_item = self._format_banker_item(item)
                    banker_items.append(approval_item)
                elif doc_id.startswith("apbookeeper_") or doc_id.startswith("ap_"):
                    # Use specialized APBookkeeper formatter with mode-specific fields
                    approval_item = self._format_apbookeeper_item(item)
                    apbookeeper_items.append(approval_item)

            result = {
                "router": {
                    "items": router_items,
                    "count": len(router_items),
                    "enabled": True
                },
                "banker": {
                    "items": banker_items,
                    "count": len(banker_items),
                    "enabled": True
                },
                "apbookeeper": {
                    "items": apbookeeper_items,
                    "count": len(apbookeeper_items),
                    "enabled": True
                }
            }

            # Cache result
            import json
            redis.setex(cache_key, TTL_APPROVALS_CACHE, json.dumps(result))

            logger.info(
                f"APPROVAL.get_pending_approvals company_id={company_id} "
                f"router={len(router_items)} banker={len(banker_items)} "
                f"ap={len(apbookeeper_items)}"
            )

            return {"success": True, "data": result}

        except Exception as e:
            logger.error(f"APPROVAL.get_pending_approvals error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "APPROVAL_LIST_ERROR", "message": str(e)}
            }

    def _format_approval_item(self, item: Dict) -> Dict[str, Any]:
        """Formate un item d'approbation."""
        # Serialize all values to handle Firestore DatetimeWithNanoseconds
        item = _serialize_value(item)
        confidence_score = item.get("confidence_score", 0)

        # Calculate confidence color
        if confidence_score >= 0.8:
            confidence_color = "green"
        elif confidence_score >= 0.5:
            confidence_color = "yellow"
        else:
            confidence_color = "red"

        # Extract context_payload for Router-specific fields
        context_payload = item.get("context_payload", {})

        # Get drive_file_id for document viewer modal - from context_payload
        drive_file_id = context_payload.get("drive_file_id", "") or item.get("drive_file_id", "")

        # Get available years and normalize to strings for consistent comparison
        raw_available_years = context_payload.get("available_years", []) or item.get("available_years", [])
        available_years = [str(y) for y in raw_available_years] if raw_available_years else []

        # Get selected year and normalize to string
        selected_year = context_payload.get("year", "") or context_payload.get("selected_fiscal_year", "") or item.get("selected_fiscal_year", "")
        selected_year = str(selected_year) if selected_year else ""

        return {
            "id": item.get("id", ""),
            "fileName": item.get("file_name", ""),
            "account": item.get("account", ""),
            # Agent note comes from context_payload.selected_motivation
            "agentNote": context_payload.get("selected_motivation", "") or item.get("agent_note", ""),
            "confidenceScore": confidence_score,
            "confidenceScoreStr": f"{int(confidence_score * 100)}%",
            "confidenceColor": confidence_color,
            # Drive file ID for document viewer modal URL
            "driveFileId": drive_file_id,
            "createdAt": item.get("creation_date", ""),
            "contextPayload": context_payload,
            # Router specific fields - from context_payload
            # Available options for dropdowns (years normalized to strings)
            "availableServices": context_payload.get("service_list", []) or item.get("available_services", []),
            "availableYears": available_years,
            # Selected/Suggested values - agent's choice (context_payload.service and context_payload.year)
            "selectedService": context_payload.get("service", "") or context_payload.get("selected_service", "") or item.get("selected_service", ""),
            "selectedFiscalYear": selected_year,
            # Suggested values - same as selected (what agent suggested)
            "suggestedService": context_payload.get("service", "") or context_payload.get("selected_service", "") or item.get("suggested_service", ""),
            "suggestedYear": selected_year,
            "instructions": item.get("instructions", ""),
            "jobId": item.get("job_id", ""),
            "fileId": item.get("file_id", ""),
            "driveLink": item.get("drive_link", ""),
        }

    def _format_amount(self, amount: float, currency: str = "EUR") -> str:
        """Formate un montant avec devise."""
        symbol = {"EUR": "€", "USD": "$", "CHF": "CHF"}.get(currency, currency)
        return f"{symbol} {amount:,.2f}"

    def _format_banker_item(self, item: Dict) -> Dict[str, Any]:
        """
        Formate un item d'approbation Banker avec tous les champs selon le mode.

        Modes:
        - gl_entry: Écritures comptables manuelles
        - expense_entry: Réconciliation avec note de frais
        - counterpart_exists (invoice): Réconciliation avec facture existante
        """
        # Serialize to handle Firestore DatetimeWithNanoseconds
        item = _serialize_value(item)
        base = self._format_approval_item(item)
        
        # Mode detection
        entry_type = item.get("entry_type", "expense_entry")  # gl_entry | expense_entry
        bank_case = item.get("bank_case", "")  # counterpart_exists = invoice mode
        
        # Banker specific fields (tous modes)
        base.update({
            "entryType": entry_type,
            "bankCase": bank_case,
            "transactionId": item.get("transaction_id", ""),
            "batchId": item.get("batch_id", ""),
            "transactionAmount": item.get("transaction_amount", 0),
            "transactionAmountStr": self._format_amount(
                item.get("transaction_amount", 0),
                item.get("currency", "EUR")
            ),
            "transactionAmountColor": (
                "red" if item.get("transaction_amount", 0) < 0 else "green"
            ),
            "currency": item.get("currency", "EUR"),
        })
        
        # GL Entry mode - Écritures comptables manuelles
        if entry_type == "gl_entry":
            base.update({
                "accountingLines": item.get("accounting_lines", []),
                "glTotals": item.get("gl_totals", {}),  # debit, credit, matches
            })
        
        # Expense mode - Réconciliation avec note de frais
        if entry_type == "expense_entry":
            base.update({
                "expenseReportId": item.get("expense_report_id", ""),
                "selectedExpenseAccount": item.get("selected_expense_account", ""),
                "selectedTaxIds": item.get("selected_tax_ids", []),
                "expenseDetails": item.get("expense_details", {}),
            })
        
        # Invoice reconcile mode - Réconciliation avec facture
        if bank_case == "counterpart_exists":
            base.update({
                "selectedInvoiceId": item.get("selected_invoice_id", ""),
                "selectedInvoiceIds": item.get("selected_invoice_ids", []),  # Multi-select
                "fullReconcile": item.get("full_reconcile", True),
                "invoiceCandidates": item.get("invoice_candidates", []),
                "sortNewestFirst": item.get("sort_newest_first", True),
            })
        
        # Fields UI metadata (dropdowns, options)
        base.update({
            "fieldsUI": item.get("fields_ui", []),  # Dynamic form fields
            "availableAccounts": item.get("available_accounts", []),
            "availableTaxes": item.get("available_taxes", []),
        })
        
        return base

    def _format_apbookeeper_item(self, item: Dict) -> Dict[str, Any]:
        """
        Formate un item d'approbation APBookkeeper avec tous les champs selon le mode.

        Modes:
        - invoice: Saisie de facture (invoice_details + accounting_lines)
        - supplier: Création de contact fournisseur (editable_fields)
        - asset: Création d'immobilisation (assets_to_create)

        Note: Workers store data nested under context_payload, dropdown_options,
        editable_fields. We read from nested first, with flat fallback.
        """
        # Serialize to handle Firestore DatetimeWithNanoseconds
        item = _serialize_value(item)
        base = self._format_approval_item(item)

        # Extract nested containers (worker stores data here)
        ctx = item.get("context_payload", {})
        dropdown = item.get("dropdown_options", {})
        editable = item.get("editable_fields", {})

        # Mode detection — check context_payload first, then flat
        approval_type = (
            item.get("approval_type")
            or ctx.get("approval_type")
            or "invoice"
        )

        # APBookkeeper common fields
        base.update({
            "approvalType": approval_type,
            "jobId": item.get("job_id", "") or ctx.get("job_id", ""),
            "batchId": item.get("batch_id", "") or ctx.get("batch_id", ""),
        })

        # Invoice mode - Saisie de facture complète
        if approval_type == "invoice":
            # Read from context_payload first (worker format), then flat (legacy)
            invoice_details_raw = ctx.get("invoice_details") or item.get("invoice_details", {})
            accounting_lines = ctx.get("accounting_lines") or item.get("accounting_lines", [])
            invoice_totals = ctx.get("invoice_totals") or item.get("invoice_totals", {})

            # Dropdowns — from dropdown_options first, then flat
            available_suppliers = dropdown.get("suppliers") or item.get("available_suppliers", [])
            available_accounts = dropdown.get("accounts") or item.get("available_accounts", [])
            available_taxes = dropdown.get("taxes") or item.get("available_taxes", [])
            available_currencies = dropdown.get("currencies") or item.get("available_currencies", [])

            # --- Normalize Odoo field names → frontend field names ---
            invoice_details = dict(invoice_details_raw)  # shallow copy

            # ref → invoice_ref
            if "ref" in invoice_details and "invoice_ref" not in invoice_details:
                invoice_details["invoice_ref"] = invoice_details.pop("ref")
            # date → accounting_date
            if "date" in invoice_details and "accounting_date" not in invoice_details:
                invoice_details["accounting_date"] = invoice_details.get("date")
            # invoice_date_due → due_date
            if "invoice_date_due" in invoice_details and "due_date" not in invoice_details:
                invoice_details["due_date"] = invoice_details.pop("invoice_date_due")

            # Resolve partner_name from dropdowns if missing
            partner_id = invoice_details.get("partner_id")
            if partner_id and not invoice_details.get("partner_name"):
                for s in available_suppliers:
                    if s.get("id") == partner_id or str(s.get("id")) == str(partner_id):
                        invoice_details["partner_name"] = s.get("name", "")
                        break

            # Resolve currency_name from dropdowns if missing
            currency_id = invoice_details.get("currency_id")
            if currency_id and not invoice_details.get("currency_name"):
                for c in available_currencies:
                    if c.get("id") == currency_id or str(c.get("id")) == str(currency_id):
                        invoice_details["currency_name"] = c.get("name", "")
                        break

            # --- Normalize accounting lines: resolve names from dropdowns ---
            for line in accounting_lines:
                # name → description
                if "name" in line and "description" not in line:
                    line["description"] = line.get("name")
                # Resolve account_name/account_code from dropdowns if missing
                acc_id = line.get("account_id")
                if acc_id and not line.get("account_name"):
                    for a in available_accounts:
                        if a.get("id") == acc_id or str(a.get("id")) == str(acc_id):
                            line["account_name"] = a.get("name", "")
                            line["account_code"] = a.get("code", "")
                            break
                # Resolve tax_names from dropdowns if missing
                tax_ids = line.get("tax_ids") or []
                if tax_ids and not line.get("tax_names"):
                    tax_names = []
                    for tid in tax_ids:
                        for t in available_taxes:
                            if t.get("id") == tid or str(t.get("id")) == str(tid):
                                tax_names.append(t.get("name", ""))
                                break
                    if tax_names:
                        line["tax_names"] = tax_names

            # Compute totals if not provided
            if not invoice_totals and accounting_lines:
                total_ht = sum(
                    (line.get("quantity", 1) or 1) * (line.get("price_unit", 0) or 0)
                    for line in accounting_lines
                )
                total_vat = sum(line.get("tax_amount", 0) or 0 for line in accounting_lines)
                total_ttc = total_ht + total_vat
                expected_ttc = (
                    invoice_details.get("amount_man")
                    or invoice_details.get("amount_total")
                    or total_ttc
                )
                invoice_totals = {
                    "total_ht": round(total_ht, 2),
                    "total_vat": round(total_vat, 2),
                    "total_ttc": round(total_ttc, 2),
                    "expected_ttc": round(float(expected_ttc), 2) if expected_ttc else round(total_ttc, 2),
                    "is_balanced": abs(total_ttc - float(expected_ttc or total_ttc)) < 0.01,
                }

            base.update({
                "invoiceDetails": invoice_details,
                "accountingLines": accounting_lines,
                "invoiceTotals": invoice_totals,
                "invoiceDetailsMeta": ctx.get("invoice_details_meta") or item.get("invoice_details_meta", {}),
                "invoiceLinesMeta": ctx.get("invoice_lines_meta") or item.get("invoice_lines_meta", {}),
                "availableSuppliers": available_suppliers,
                "availableAccounts": available_accounts,
                "availableTaxes": available_taxes,
                "availableCurrencies": available_currencies,
            })

        # Supplier mode - Création de contact
        elif approval_type == "supplier":
            base.update({
                "editableFields": editable.get("supplier_data") or item.get("editable_fields", {}),
                "supplierData": ctx.get("supplier_data") or item.get("supplier_data", {}),
                "supplierFieldsUI": item.get("supplier_fields_ui", []),
                "availableCountries": dropdown.get("countries") or item.get("available_countries", []),
            })

        # Asset mode - Création d'immobilisation
        elif approval_type == "asset":
            # Read from immobilisation_data container if present
            immo = ctx.get("immobilisation_data", {})
            base.update({
                "assetsToCreate": immo.get("assets_to_create") or ctx.get("assets_to_create") or item.get("assets_to_create", []),
                "expensesToPost": immo.get("expenses_to_post") or ctx.get("expenses_to_post") or item.get("expenses_to_post", []),
                "assetInvoiceSummary": immo.get("invoice_summary") or ctx.get("invoice_summary") or item.get("asset_invoice_summary", {}),
                "availableAssetModels": dropdown.get("asset_models") or item.get("available_asset_models", []),
                "availableAssetAccounts": dropdown.get("asset_accounts") or dropdown.get("accounts") or item.get("available_asset_accounts", []),
                "availableTaxes": dropdown.get("taxes") or item.get("available_taxes", []),
                "assetsMeta": ctx.get("assets_meta") or item.get("assets_meta", {}),
                # Pass invoice details for asset context (supplier info, dates)
                "invoiceDetails": immo.get("invoice_details") or ctx.get("invoice_details") or item.get("invoice_details", {}),
                "availableSuppliers": dropdown.get("suppliers") or item.get("available_suppliers", []),
            })

        return base

    # ============================================
    # SEND ROUTER APPROVALS
    # ============================================

    async def send_router_approvals(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        approvals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Envoie les décisions d'approbation Router.

        RPC: APPROVAL.send_router_approvals

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            approvals: Liste des décisions [{
                "itemId": "...",
                "approved": True/False,
                "selectedService": "...",
                "selectedFiscalYear": "...",
                "rejectionReason": "...",
                "instructions": "...",
                "close": True/False
            }]

        Returns:
            {"success": True, "data": {"processed": N, "failed": N}}
        """
        try:
            logger.info(
                f"APPROVAL.send_router_approvals user_id={user_id} "
                f"count={len(approvals)}"
            )

            firebase = FirebaseManagement()
            processed = 0
            failed = 0
            errors = []

            for decision in approvals:
                item_id = decision.get("itemId", "")
                approved = decision.get("approved", False)

                try:
                    if approved:
                        # Process approval - move to appropriate service folder
                        result = await asyncio.to_thread(
                            firebase.process_router_approval,
                            mandate_path=mandate_path,
                            item_id=item_id,
                            selected_service=decision.get("selectedService", ""),
                            selected_fiscal_year=decision.get("selectedFiscalYear", ""),
                            user_id=user_id
                        )
                    else:
                        # Process rejection
                        result = await asyncio.to_thread(
                            firebase.process_router_rejection,
                            mandate_path=mandate_path,
                            item_id=item_id,
                            rejection_reason=decision.get("rejectionReason", ""),
                            instructions=decision.get("instructions", ""),
                            close=decision.get("close", False),
                            user_id=user_id
                        )

                    if result:
                        processed += 1
                    else:
                        failed += 1
                        errors.append({"itemId": item_id, "error": "Processing failed"})

                except Exception as item_err:
                    failed += 1
                    errors.append({"itemId": item_id, "error": str(item_err)})
                    logger.error(f"Router approval error item={item_id}: {item_err}")

            # Invalidate approvals cache
            redis = get_redis()
            redis.delete(f"approvals:{company_id}")

            # Broadcast result
            await hub.broadcast(user_id, {
                "type": "approval.result",
                "payload": {
                    "department": "router",
                    "processed": processed,
                    "failed": failed,
                    "errors": errors if failed > 0 else []
                }
            })

            logger.info(
                f"APPROVAL.send_router_approvals complete "
                f"processed={processed} failed={failed}"
            )

            return {
                "success": True,
                "data": {"processed": processed, "failed": failed, "errors": errors}
            }

        except Exception as e:
            logger.error(f"APPROVAL.send_router_approvals error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "APPROVAL_SEND_ERROR", "message": str(e)}
            }

    # ============================================
    # SEND BANKER APPROVALS
    # ============================================

    async def send_banker_approvals(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        approvals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Envoie les décisions d'approbation Banker.

        RPC: APPROVAL.send_banker_approvals

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            approvals: Liste des décisions

        Returns:
            {"success": True, "data": {"processed": N, "failed": N}}
        """
        try:
            logger.info(
                f"APPROVAL.send_banker_approvals user_id={user_id} "
                f"count={len(approvals)}"
            )

            firebase = FirebaseManagement()
            processed = 0
            failed = 0
            errors = []

            for decision in approvals:
                item_id = decision.get("itemId", "")
                approved = decision.get("approved", False)

                try:
                    if approved:
                        result = await asyncio.to_thread(
                            firebase.process_banker_approval,
                            mandate_path=mandate_path,
                            item_id=item_id,
                            batch_id=decision.get("batchId", ""),
                            user_id=user_id
                        )
                    else:
                        result = await asyncio.to_thread(
                            firebase.process_banker_rejection,
                            mandate_path=mandate_path,
                            item_id=item_id,
                            rejection_reason=decision.get("rejectionReason", ""),
                            instructions=decision.get("instructions", ""),
                            close=decision.get("close", False),
                            user_id=user_id
                        )

                    if result:
                        processed += 1
                    else:
                        failed += 1
                        errors.append({"itemId": item_id, "error": "Processing failed"})

                except Exception as item_err:
                    failed += 1
                    errors.append({"itemId": item_id, "error": str(item_err)})
                    logger.error(f"Banker approval error item={item_id}: {item_err}")

            # Invalidate cache
            redis = get_redis()
            redis.delete(f"approvals:{company_id}")

            # Broadcast result
            await hub.broadcast(user_id, {
                "type": "approval.result",
                "payload": {
                    "department": "banker",
                    "processed": processed,
                    "failed": failed,
                    "errors": errors if failed > 0 else []
                }
            })

            logger.info(
                f"APPROVAL.send_banker_approvals complete "
                f"processed={processed} failed={failed}"
            )

            return {
                "success": True,
                "data": {"processed": processed, "failed": failed, "errors": errors}
            }

        except Exception as e:
            logger.error(f"APPROVAL.send_banker_approvals error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "APPROVAL_SEND_ERROR", "message": str(e)}
            }

    # ============================================
    # SAVE APPROVAL CHANGES (LOCAL)
    # ============================================

    async def save_approval_changes(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        item_id: str,
        changes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Sauvegarde les modifications locales d'un item sans l'envoyer au jobbeur.

        RPC: APPROVAL.save_changes

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            item_id: ID de l'item
            changes: Modifications à sauvegarder
                - selected_service
                - selected_fiscal_year
                - instructions

        Returns:
            {"success": True} ou {"success": False, "error": {...}}
        """
        try:
            logger.info(f"APPROVAL.save_changes user_id={user_id} item_id={item_id}")

            firebase = FirebaseManagement()
            result = await asyncio.to_thread(
                firebase.save_approval_item_changes,
                mandate_path=mandate_path,
                item_id=item_id,
                changes=changes,
                user_id=user_id
            )

            if result:
                # Invalider le cache
                redis = get_redis()
                redis.delete(f"approvals:{company_id}")

                logger.info(f"APPROVAL.save_changes success item_id={item_id}")
                return {"success": True, "item_id": item_id}
            else:
                return {
                    "success": False,
                    "error": {"code": "SAVE_FAILED", "message": "Failed to save changes"}
                }

        except Exception as e:
            logger.error(f"APPROVAL.save_changes error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "APPROVAL_SAVE_ERROR", "message": str(e)}
            }

    # ============================================
    # SEND APBOOKEEPER APPROVALS
    # ============================================

    async def send_apbookeeper_approvals(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        approvals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Envoie les décisions d'approbation APbookeeper.

        RPC: APPROVAL.send_apbookeeper_approvals

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            approvals: Liste des décisions

        Returns:
            {"success": True, "data": {"processed": N, "failed": N}}
        """
        try:
            logger.info(
                f"APPROVAL.send_apbookeeper_approvals user_id={user_id} "
                f"count={len(approvals)}"
            )

            firebase = FirebaseManagement()
            processed = 0
            failed = 0
            errors = []

            for decision in approvals:
                item_id = decision.get("itemId", "")
                approved = decision.get("approved", False)

                try:
                    if approved:
                        result = await asyncio.to_thread(
                            firebase.process_apbookeeper_approval,
                            mandate_path=mandate_path,
                            item_id=item_id,
                            user_id=user_id,
                            instructions=decision.get("instructions", ""),
                            updated_data=decision.get("updatedData", {}),
                            approval_type=decision.get("selectedMode", "invoice"),
                        )
                    else:
                        result = await asyncio.to_thread(
                            firebase.process_apbookeeper_rejection,
                            mandate_path=mandate_path,
                            item_id=item_id,
                            rejection_reason=decision.get("rejectionReason", ""),
                            instructions=decision.get("instructions", ""),
                            close=decision.get("close", False),
                            user_id=user_id
                        )

                    if result:
                        processed += 1
                    else:
                        failed += 1
                        errors.append({"itemId": item_id, "error": "Processing failed"})

                except Exception as item_err:
                    failed += 1
                    errors.append({"itemId": item_id, "error": str(item_err)})
                    logger.error(f"APbookeeper approval error item={item_id}: {item_err}")

            # Invalidate cache
            redis = get_redis()
            redis.delete(f"approvals:{company_id}")

            # Broadcast result
            await hub.broadcast(user_id, {
                "type": "approval.result",
                "payload": {
                    "department": "apbookeeper",
                    "processed": processed,
                    "failed": failed,
                    "errors": errors if failed > 0 else []
                }
            })

            logger.info(
                f"APPROVAL.send_apbookeeper_approvals complete "
                f"processed={processed} failed={failed}"
            )

            return {
                "success": True,
                "data": {"processed": processed, "failed": failed, "errors": errors}
            }

        except Exception as e:
            logger.error(f"APPROVAL.send_apbookeeper_approvals error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "APPROVAL_SEND_ERROR", "message": str(e)}
            }


# ============================================
# WEBSOCKET EVENT HANDLERS
# ============================================

async def handle_approval_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle approval.list WebSocket event."""
    handlers = get_approval_handlers()
    result = await handlers.get_pending_approvals(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        force_refresh=payload.get("force_refresh", False)
    )

    if result.get("success"):
        await hub.broadcast(uid, {
            "type": "dashboard.approvals_update",
            "payload": result
        })

    return {"type": "approval.list", "payload": result}


async def handle_send_router(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle approval.send_router WebSocket event."""
    handlers = get_approval_handlers()
    result = await handlers.send_router_approvals(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        approvals=payload.get("approvals", [])
    )
    return {"type": "approval.send_router", "payload": result}


async def handle_send_banker(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle approval.send_banker WebSocket event."""
    handlers = get_approval_handlers()
    result = await handlers.send_banker_approvals(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        approvals=payload.get("approvals", [])
    )
    return {"type": "approval.send_banker", "payload": result}


async def handle_send_apbookeeper(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle approval.send_apbookeeper WebSocket event."""
    handlers = get_approval_handlers()
    result = await handlers.send_apbookeeper_approvals(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        approvals=payload.get("approvals", [])
    )
    return {"type": "approval.send_apbookeeper", "payload": result}


async def handle_save_approval_changes(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle approval.save_changes WebSocket event.

    Sauvegarde les modifications locales d'un item d'approbation sans l'envoyer au jobbeur.
    Utilisé pour persister les sélections utilisateur (service, année, instructions).

    Payload:
        - company_id: ID de la société
        - mandate_path: Chemin du mandat
        - item_id: ID de l'item (ex: "router_abc123")
        - changes: Dict des modifications
            - selected_service: Service sélectionné
            - selected_fiscal_year: Année fiscale sélectionnée
            - instructions: Instructions additionnelles
    """
    try:
        mandate_path = payload.get("mandate_path", "")
        item_id = payload.get("item_id", "")
        changes = payload.get("changes", {})
        company_id = payload.get("company_id", "")

        if not mandate_path or not item_id:
            return {
                "type": "approval.save_changes",
                "payload": {
                    "success": False,
                    "error": {"code": "MISSING_PARAMS", "message": "mandate_path and item_id are required"}
                }
            }

        firebase = FirebaseManagement()
        result = await asyncio.to_thread(
            firebase.save_approval_item_changes,
            mandate_path=mandate_path,
            item_id=item_id,
            changes=changes,
            user_id=uid
        )

        if result:
            # Invalider le cache des approbations
            redis = get_redis()
            redis.delete(f"approvals:{company_id}")

            logger.info(f"APPROVAL.save_changes success item_id={item_id}")

            # Broadcast la mise à jour pour synchroniser les autres onglets
            await hub.broadcast(uid, {
                "type": "dashboard.pending_approval_update",
                "payload": {
                    "action": "update",
                    "item": {
                        "id": item_id,
                        **changes
                    },
                    "job_id": item_id,
                    "department": "router" if item_id.startswith("router_") else "banker" if item_id.startswith("banker_") else "apbookeeper"
                }
            })

            return {
                "type": "approval.save_changes",
                "payload": {"success": True, "item_id": item_id}
            }
        else:
            return {
                "type": "approval.save_changes",
                "payload": {
                    "success": False,
                    "error": {"code": "SAVE_FAILED", "message": "Failed to save changes"}
                }
            }

    except Exception as e:
        logger.error(f"APPROVAL.save_changes error: {e}", exc_info=True)
        return {
            "type": "approval.save_changes",
            "payload": {
                "success": False,
                "error": {"code": "APPROVAL_SAVE_ERROR", "message": str(e)}
            }
        }


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "ApprovalHandlers",
    "get_approval_handlers",
    "handle_approval_list",
    "handle_send_router",
    "handle_send_banker",
    "handle_send_apbookeeper",
    "handle_save_approval_changes",
]
