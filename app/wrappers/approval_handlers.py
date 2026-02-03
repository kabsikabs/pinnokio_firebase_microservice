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
        """
        # Serialize to handle Firestore DatetimeWithNanoseconds
        item = _serialize_value(item)
        base = self._format_approval_item(item)
        
        # Mode detection
        approval_type = item.get("approval_type", "invoice")  # invoice | supplier | asset
        
        # APBookkeeper common fields
        base.update({
            "approvalType": approval_type,
            "jobId": item.get("job_id", ""),
            "batchId": item.get("batch_id", ""),
        })
        
        # Invoice mode - Saisie de facture complète
        if approval_type == "invoice":
            base.update({
                # Invoice header (partner, ref, currency, dates)
                "invoiceDetails": item.get("invoice_details", {}),
                # Accounting lines with accounts, taxes, amounts
                "accountingLines": item.get("accounting_lines", []),
                # Calculated totals (total_ht, total_vat, total_ttc, expected_ttc, is_balanced)
                "invoiceTotals": item.get("invoice_totals", {}),
                # Metadata for dropdowns (suppliers, accounts, taxes, currencies)
                "invoiceDetailsMeta": item.get("invoice_details_meta", {}),
                "invoiceLinesMeta": item.get("invoice_lines_meta", {}),
                # Options for dropdowns
                "availableSuppliers": item.get("available_suppliers", []),
                "availableAccounts": item.get("available_accounts", []),
                "availableTaxes": item.get("available_taxes", []),
                "availableCurrencies": item.get("available_currencies", []),
            })
        
        # Supplier mode - Création de contact
        elif approval_type == "supplier":
            base.update({
                # Dynamic editable fields (name, email, address, country_id, vat, etc.)
                "editableFields": item.get("editable_fields", {}),
                "supplierData": item.get("supplier_data", {}),
                # Fields UI with types, labels, options
                "supplierFieldsUI": item.get("supplier_fields_ui", []),
                # Country dropdown options
                "availableCountries": item.get("available_countries", []),
            })
        
        # Asset mode - Création d'immobilisation
        elif approval_type == "asset":
            base.update({
                # List of assets to create (asset_name, model_id, acquisition_date, original_value, quantity, justification)
                "assetsToCreate": item.get("assets_to_create", []),
                # Asset models dropdown
                "availableAssetModels": item.get("available_asset_models", []),
                # Metadata
                "assetsMeta": item.get("assets_meta", {}),
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
                            user_id=user_id
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
