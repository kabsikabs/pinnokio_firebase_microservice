"""
Messenger Handlers
==================

RPC handlers for messenger.* WebSocket events.

Events handled:
- messenger.mark_read: Mark a message as read (delete from RTDB)
- messenger.click: Handle message click (prepare redirect to chat)

Migrated from Reflex MessengerState:
- mark_as_read() -> handle_messenger_mark_read
- click_message() -> handle_messenger_click
"""

import logging
from typing import Any, Dict, Optional

from app.firebase_providers import get_firebase_realtime
from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.realtime.pubsub_helper import publish_messenger_remove

logger = logging.getLogger(__name__)

# ============================================
# Singleton
# ============================================

_instance: Optional["MessengerHandlers"] = None


def get_messenger_handlers() -> "MessengerHandlers":
    """Get singleton instance of MessengerHandlers."""
    global _instance
    if _instance is None:
        _instance = MessengerHandlers()
    return _instance


# ============================================
# Handlers Class
# ============================================

class MessengerHandlers:
    """RPC handlers for messenger namespace."""

    def __init__(self):
        self._rtdb = get_firebase_realtime()  # For RTDB via self._rtdb.db
        self._logger = logging.getLogger("messenger.handlers")

    # ─────────────────────────────────────────
    # messenger.mark_read
    # ─────────────────────────────────────────

    async def handle_mark_read(
        self,
        uid: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle messenger.mark_read event.

        In RTDB, marking as read means deleting the message.

        Args:
            uid: Firebase user ID
            payload: { docId: string }
        """
        doc_id = payload.get("docId")
        if not doc_id:
            await self._send_error(uid, "Missing docId in payload")
            return

        try:
            # Access RTDB via FirebaseRealtimeChat singleton
            # Path: clients/{uid}/direct_message_notif/{doc_id}
            message_path = f"clients/{uid}/direct_message_notif/{doc_id}"
            message_ref = self._rtdb.db.child(message_path)
            message_ref.delete()

            self._logger.info(
                f"[MESSENGER] Deleted message {doc_id} for uid={uid}"
            )

            # Send success result
            await hub.broadcast(uid, {
                "type": WS_EVENTS.MESSENGER.MARK_READ_RESULT,
                "payload": {
                    "success": True,
                    "docId": doc_id,
                },
            })

            # Publish delta to remove from frontend list
            await publish_messenger_remove(uid, doc_id)

        except Exception as e:
            self._logger.error(
                f"[MESSENGER] Failed to mark_read {doc_id} for uid={uid}: {e}"
            )
            await self._send_error(uid, str(e))

    # ─────────────────────────────────────────
    # messenger.click
    # ─────────────────────────────────────────

    async def handle_click(
        self,
        uid: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle messenger.click event.

        Determines redirect path based on functionName and chatMode.

        Redirect rules (migrated from MessengerState.click_message):
        - Chat with onboarding_chat -> /chat?mode=onboarding&thread={threadKey}
        - Chat with general_chat -> /chat?thread={threadKey}
        - Router -> /routing/{drive_id}
        - APbookeeper -> /invoices/{job_id}
        - Bankbookeeper -> /banking/{batch_id}
        - Default -> /chat

        Args:
            uid: Firebase user ID
            payload: {
                docId, jobId, fileId, functionName, collectionId,
                chatMode?, threadKey?, batchId?
            }
        """
        function_name = payload.get("functionName", "")
        job_id = payload.get("jobId", "")
        file_id = payload.get("fileId", "")
        collection_id = payload.get("collectionId", "")
        chat_mode = payload.get("chatMode", "")
        thread_key = payload.get("threadKey", "")
        batch_id = payload.get("batchId", "")

        try:
            # Determine redirect path
            redirect_path = "/chat"  # Default

            if function_name == "Chat":
                # Chat messages redirect to chat page with thread context
                if chat_mode == "onboarding_chat" and thread_key:
                    redirect_path = f"/chat?mode=onboarding&thread={thread_key}"
                elif thread_key:
                    redirect_path = f"/chat?thread={thread_key}"
                else:
                    redirect_path = "/chat"

            elif function_name == "Router":
                if file_id:
                    redirect_path = f"/routing/{file_id}"
                else:
                    redirect_path = "/routing"

            elif function_name == "APbookeeper":
                if job_id:
                    redirect_path = f"/invoices/{job_id}"
                else:
                    redirect_path = "/invoices"

            elif function_name == "Bankbookeeper":
                if batch_id:
                    redirect_path = f"/banking/{batch_id}"
                elif job_id:
                    redirect_path = f"/banking/{job_id}"
                else:
                    redirect_path = "/banking"

            # Check if company change is needed
            needs_company_change = False
            target_company_id = None

            if collection_id:
                target_company_id = collection_id

            # Send result
            await hub.broadcast(uid, {
                "type": WS_EVENTS.MESSENGER.CLICK_RESULT,
                "payload": {
                    "success": True,
                    "redirect": {
                        "path": redirect_path,
                    },
                    "needsCompanyChange": needs_company_change,
                    "targetCompanyId": target_company_id,
                    "chatMode": chat_mode,
                    "threadKey": thread_key,
                },
            })

            self._logger.info(
                f"[MESSENGER] Click handled for uid={uid}, redirect={redirect_path}"
            )

        except Exception as e:
            self._logger.error(
                f"[MESSENGER] Failed to handle click for uid={uid}: {e}"
            )
            await self._send_error(uid, str(e))

    # ─────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────

    async def _send_error(self, uid: str, error: str) -> None:
        """Send error event to frontend."""
        await hub.broadcast(uid, {
            "type": WS_EVENTS.MESSENGER.ERROR,
            "payload": {"error": error},
        })


# ============================================
# Handler Functions (for WebSocket router)
# ============================================

async def handle_messenger_mark_read(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle messenger.mark_read WebSocket event."""
    handlers = get_messenger_handlers()
    await handlers.handle_mark_read(uid, payload)


async def handle_messenger_click(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle messenger.click WebSocket event."""
    handlers = get_messenger_handlers()
    await handlers.handle_click(uid, payload)
