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

from app.firebase_providers import FirebaseManagement
from app.redis_client import get_redis
from app.ws_events import WS_EVENTS
from app.ws_hub import hub

logger = logging.getLogger("approval.handlers")


# ============================================
# CONSTANTS
# ============================================

TTL_APPROVALS_CACHE = 30  # 30 seconds


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
    ) -> Dict[str, Any]:
        """
        Récupère les approbations en attente par département.

        RPC: APPROVAL.get_pending_approvals

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat

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

            # Group by department
            router_items = []
            banker_items = []
            apbookeeper_items = []

            for item in pending_items:
                department = item.get("department", "").lower()
                approval_item = self._format_approval_item(item)

                if department == "router":
                    router_items.append(approval_item)
                elif department == "banker":
                    # Banker has additional fields
                    approval_item["transactionAmount"] = item.get("transaction_amount", 0)
                    approval_item["transactionAmountStr"] = self._format_amount(
                        item.get("transaction_amount", 0),
                        item.get("currency", "EUR")
                    )
                    approval_item["transactionAmountColor"] = (
                        "red" if item.get("transaction_amount", 0) < 0 else "green"
                    )
                    approval_item["transactionId"] = item.get("transaction_id", "")
                    approval_item["batchId"] = item.get("batch_id", "")
                    banker_items.append(approval_item)
                elif department in ["apbookeeper", "ap"]:
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
        confidence_score = item.get("confidence_score", 0)

        # Calculate confidence color
        if confidence_score >= 0.8:
            confidence_color = "green"
        elif confidence_score >= 0.5:
            confidence_color = "yellow"
        else:
            confidence_color = "red"

        return {
            "id": item.get("id", ""),
            "fileName": item.get("file_name", ""),
            "account": item.get("account", ""),
            "agentNote": item.get("agent_note", ""),
            "confidenceScore": confidence_score,
            "confidenceScoreStr": f"{int(confidence_score * 100)}%",
            "confidenceColor": confidence_color,
            "driveFileId": item.get("drive_file_id", ""),
            "createdAt": item.get("creation_date", ""),
            "contextPayload": item.get("context_payload", {}),
            # Router specific
            "availableServices": item.get("available_services", []),
            "availableYears": item.get("available_years", []),
            "selectedService": item.get("selected_service", ""),
            "selectedFiscalYear": item.get("selected_fiscal_year", ""),
            "suggestedService": item.get("suggested_service", ""),
            "suggestedYear": item.get("suggested_year", ""),
        }

    def _format_amount(self, amount: float, currency: str = "EUR") -> str:
        """Formate un montant avec devise."""
        symbol = {"EUR": "€", "USD": "$", "CHF": "CHF"}.get(currency, currency)
        return f"{symbol} {amount:,.2f}"

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
        mandate_path=payload.get("mandate_path", "")
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
]
