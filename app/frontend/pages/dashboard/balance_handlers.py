"""
Balance Handlers - Top-up and Refresh Balance
==============================================

Handles account balance operations for the Dashboard AccountBalanceCard component.

WebSocket Events:
    - balance.top_up: Initiate top-up (creates Stripe checkout session)
    - balance.top_up_result: Response with checkout URL or error
    - balance.refresh: Refresh balance data
    - balance.refreshed: Balance data updated

Flow for Top-Up with Stripe:
    1. Frontend sends balance.top_up with amount
    2. Backend saves pending_action for state preservation
    3. Backend creates Stripe checkout session
    4. Backend returns checkout_url via balance.top_up_result
    5. Frontend redirects to Stripe
    6. User completes payment on Stripe
    7. Stripe webhook calls backend to complete transaction
    8. Backend completes pending_action and updates balance
    9. User returns to app, pending_action restores state

Based on Reflex JobHistory.handle_top_up and refresh_balance methods.
"""

import logging
from typing import Any, Dict, Optional

from app.firebase_providers import get_firebase_management
from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.frontend.core.pending_action_manager import get_pending_action_manager

logger = logging.getLogger("balance.handlers")


# =============================================================================
# SINGLETON
# =============================================================================

_balance_handlers_instance: Optional["BalanceHandlers"] = None


def get_balance_handlers() -> "BalanceHandlers":
    """Get singleton instance of BalanceHandlers."""
    global _balance_handlers_instance
    if _balance_handlers_instance is None:
        _balance_handlers_instance = BalanceHandlers()
    return _balance_handlers_instance


# =============================================================================
# HANDLER CLASS
# =============================================================================

class BalanceHandlers:
    """
    Handlers for balance operations.

    WebSocket Events Handled:
        - balance.top_up: Create Stripe checkout for top-up
        - balance.refresh: Refresh balance data
    """

    def __init__(self):
        self._firebase = get_firebase_management()
        self._pending_action_manager = get_pending_action_manager()

    # =========================================================================
    # TOP-UP HANDLER
    # =========================================================================

    async def handle_top_up(
        self,
        uid: str,
        session_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle balance.top_up WebSocket event.

        Creates a Stripe checkout session and returns the checkout URL.
        Uses pending_action_manager to preserve state during redirect.

        Args:
            uid: Firebase user ID
            session_id: WebSocket session ID
            payload: {
                "amount": float,
                "currency": str (optional, default "USD"),
                "company_id": str,
                "mandate_path": str
            }
        """
        amount = payload.get("amount")
        currency = payload.get("currency", "USD")
        company_id = payload.get("company_id")
        mandate_path = payload.get("mandate_path")

        # Validate required fields
        if not amount:
            await self._send_error(uid, "Invalid amount", "Please enter an amount")
            return

        try:
            amount = float(amount)
        except (ValueError, TypeError):
            await self._send_error(uid, "Invalid amount", "Please enter a valid number")
            return

        if amount <= 0:
            await self._send_error(
                uid, "Invalid amount", "Please enter an amount greater than 0"
            )
            return

        if not mandate_path:
            await self._send_error(
                uid, "Missing context", "No company selected"
            )
            return

        try:
            # 1. Save pending action for state recovery after Stripe redirect
            state_token = self._pending_action_manager.save_pending_action(
                uid=uid,
                session_id=session_id,
                action_type="payment",
                provider="stripe",
                return_page="dashboard",
                return_path="/dashboard",
                context={
                    "company_id": company_id,
                    "mandate_path": mandate_path,
                    "amount": amount,
                    "currency": currency,
                    "action": "top_up",
                }
            )

            logger.info(
                f"[BALANCE] Top-up initiated: uid={uid} amount={amount} {currency} "
                f"state_token={state_token[:20]}..."
            )

            # 2. Create Stripe checkout session via FirebaseManagement
            top_up_data = {
                "currency": currency,
                "amount": amount,
            }

            result = self._firebase.add_top_up(mandate_path, top_up_data)

            if not result.get("success", False):
                # Cancel pending action on error
                self._pending_action_manager.cancel_pending_action(uid, session_id)

                error_msg = result.get("error", "Unknown error")
                await self._send_error(
                    uid, "Transaction failed", error_msg
                )
                return

            checkout_url = result.get("checkout_url")

            if checkout_url:
                # 3. Return checkout URL to frontend
                await hub.send_to_user(uid, {
                    "type": WS_EVENTS.BALANCE.TOP_UP_RESULT,
                    "payload": {
                        "success": True,
                        "checkout_url": checkout_url,
                        "transaction_id": result.get("transaction_id"),
                        "state_token": state_token,
                        "message": "Redirecting to secure payment",
                    }
                })
                logger.info(f"[BALANCE] Checkout URL sent to uid={uid}")
            else:
                # Immediate mode (no Stripe) - payment processed directly
                self._pending_action_manager.cancel_pending_action(uid, session_id)

                await hub.send_to_user(uid, {
                    "type": WS_EVENTS.BALANCE.TOP_UP_RESULT,
                    "payload": {
                        "success": True,
                        "immediate": True,
                        "message": f"Successfully added {amount} to your account",
                    }
                })

                # Refresh balance data after immediate top-up
                await self.handle_refresh(uid, session_id, {
                    "company_id": company_id,
                    "mandate_path": mandate_path,
                })

        except Exception as e:
            logger.error(f"[BALANCE] Top-up error: {e}", exc_info=True)
            self._pending_action_manager.cancel_pending_action(uid, session_id)
            await self._send_error(
                uid, "Error", "An unexpected error occurred. Please try again."
            )

    # =========================================================================
    # REFRESH HANDLER
    # =========================================================================

    async def handle_refresh(
        self,
        uid: str,
        session_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle balance.refresh WebSocket event.

        Fetches fresh balance data and sends it to the frontend.

        Args:
            uid: Firebase user ID
            session_id: WebSocket session ID
            payload: {
                "company_id": str,
                "mandate_path": str
            }
        """
        company_id = payload.get("company_id")
        mandate_path = payload.get("mandate_path")

        if not mandate_path:
            await self._send_error(
                uid, "Missing context", "No company selected"
            )
            return

        try:
            # Get fresh balance data
            from .providers.account_balance_card import get_account_balance_data

            balance_data = await get_account_balance_data(
                user_id=uid,
                company_id=company_id,
                mandate_path=mandate_path
            )

            # Send refreshed balance data
            await hub.send_to_user(uid, {
                "type": WS_EVENTS.BALANCE.REFRESHED,
                "payload": {
                    "success": True,
                    "data": balance_data,
                    "company_id": company_id,
                }
            })

            logger.info(
                f"[BALANCE] Refreshed: uid={uid} company_id={company_id} "
                f"balance={balance_data.get('currentBalance', 0):.2f}"
            )

        except Exception as e:
            logger.error(f"[BALANCE] Refresh error: {e}", exc_info=True)
            await self._send_error(
                uid, "Refresh failed", "Failed to refresh your balance"
            )

    # =========================================================================
    # STRIPE CALLBACK HANDLER
    # =========================================================================

    async def handle_stripe_callback(
        self,
        uid: str,
        session_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle return from Stripe checkout.

        Called when user returns to the app after Stripe payment.
        Completes the pending action and refreshes the dashboard.

        Args:
            uid: Firebase user ID
            session_id: WebSocket session ID
            payload: {
                "stripe_session_id": str (from URL query param),
                "status": str ("success" | "cancelled")
            }
        """
        stripe_session_id = payload.get("stripe_session_id")
        status = payload.get("status", "success")

        try:
            # Complete pending action
            pending_action = self._pending_action_manager.complete_pending_action(
                uid, session_id
            )

            if pending_action:
                context = pending_action.get("context", {})
                company_id = context.get("company_id")
                mandate_path = context.get("mandate_path")

                if status == "success":
                    logger.info(
                        f"[BALANCE] Stripe callback success: uid={uid} "
                        f"session_id={stripe_session_id}"
                    )

                    # Refresh balance after successful payment
                    await self.handle_refresh(uid, session_id, {
                        "company_id": company_id,
                        "mandate_path": mandate_path,
                    })

                    await hub.send_to_user(uid, {
                        "type": WS_EVENTS.BALANCE.TOP_UP_COMPLETE,
                        "payload": {
                            "success": True,
                            "message": "Payment completed successfully",
                            "company_id": company_id,
                        }
                    })
                else:
                    # Payment cancelled
                    await hub.send_to_user(uid, {
                        "type": WS_EVENTS.BALANCE.TOP_UP_COMPLETE,
                        "payload": {
                            "success": False,
                            "cancelled": True,
                            "message": "Payment was cancelled",
                            "company_id": company_id,
                        }
                    })
            else:
                logger.warning(
                    f"[BALANCE] No pending action found for uid={uid}"
                )

        except Exception as e:
            logger.error(f"[BALANCE] Stripe callback error: {e}", exc_info=True)

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _send_error(
        self,
        uid: str,
        title: str,
        message: str,
    ) -> None:
        """Send error response to frontend."""
        await hub.send_to_user(uid, {
            "type": WS_EVENTS.BALANCE.ERROR,
            "payload": {
                "success": False,
                "error": {
                    "title": title,
                    "message": message,
                }
            }
        })


# =============================================================================
# HANDLER FUNCTIONS (for direct import)
# =============================================================================

async def handle_top_up(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Handle balance.top_up event."""
    handlers = get_balance_handlers()
    await handlers.handle_top_up(uid, session_id, payload)


async def handle_refresh_balance(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Handle balance.refresh event."""
    handlers = get_balance_handlers()
    await handlers.handle_refresh(uid, session_id, payload)


async def handle_stripe_callback(uid: str, session_id: str, payload: Dict[str, Any]) -> None:
    """Handle Stripe callback (return from checkout)."""
    handlers = get_balance_handlers()
    await handlers.handle_stripe_callback(uid, session_id, payload)
