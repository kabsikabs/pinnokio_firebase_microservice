"""
Notification Handlers
=====================

RPC handlers for notification.* WebSocket events.

Events handled:
- notification.mark_read: Mark a notification as read
- notification.click: Handle notification click (prepare redirect)

Migrated from Reflex NotificationState:
- mark_as_read() -> handle_notification_mark_read
- click_notification() -> handle_notification_click
"""

import json
import logging
from typing import Any, Dict, Optional

from app.firebase_providers import get_firebase_management, get_firebase_realtime
from app.firebase_client import get_firestore
from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.realtime.pubsub_helper import publish_notification_remove

logger = logging.getLogger(__name__)

# ============================================
# Singleton
# ============================================

_instance: Optional["NotificationHandlers"] = None


def get_notification_handlers() -> "NotificationHandlers":
    """Get singleton instance of NotificationHandlers."""
    global _instance
    if _instance is None:
        _instance = NotificationHandlers()
    return _instance


# ============================================
# Handlers Class
# ============================================

class NotificationHandlers:
    """RPC handlers for notification namespace."""

    def __init__(self):
        self._firebase = get_firebase_management()  # For Firestore via self._firebase.db
        self._rtdb = get_firebase_realtime()        # For RTDB via self._rtdb.db
        self._logger = logging.getLogger("notification.handlers")

    # ─────────────────────────────────────────
    # notification.mark_read
    # ─────────────────────────────────────────

    async def handle_mark_read(
        self,
        uid: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle notification.mark_read event.

        Business rules (migrated from NotificationState.mark_as_read):
        1. If status == 'completed' -> DELETE notification permanently
        2. Otherwise -> Mark as read=True and remove from display

        Side effects for Bankbookeeper:
        - Delete active_chats RTDB
        - Delete job_chats RTDB
        - Delete task_manager Firestore

        Args:
            uid: Firebase user ID
            payload: { docId: string }
        """
        doc_id = payload.get("docId")
        if not doc_id:
            await self._send_error(uid, "Missing docId in payload")
            return

        try:
            # Access Firestore via get_firestore() singleton
            db = get_firestore()
            notif_ref = (
                db.collection("clients")
                .document(uid)
                .collection("notifications")
                .document(doc_id)
            )
            notif_doc = notif_ref.get()

            if not notif_doc.exists:
                await self._send_error(uid, f"Notification {doc_id} not found")
                return

            notif_data = notif_doc.to_dict()
            status = (notif_data.get("status") or "").lower()
            function_name = notif_data.get("function_name", notif_data.get("functionName", ""))

            # Determine action based on status
            if status == "completed":
                # Delete permanently
                action = "deleted"

                # Side effects for Bankbookeeper
                if function_name == "Bankbookeeper":
                    await self._cleanup_bankbookeeper_data(uid, notif_data)

                # Delete the notification
                notif_ref.delete()
                self._logger.info(
                    f"[NOTIFICATION] Deleted notification {doc_id} for uid={uid}"
                )
            else:
                # Mark as read
                action = "marked_read"
                notif_ref.update({"read": True})
                self._logger.info(
                    f"[NOTIFICATION] Marked notification {doc_id} as read for uid={uid}"
                )

            # Send success result
            await hub.broadcast(uid, {
                "type": WS_EVENTS.NOTIFICATION.MARK_READ_RESULT,
                "payload": {
                    "success": True,
                    "docId": doc_id,
                    "action": action,
                },
            })

            # Publish delta to remove from frontend list
            await publish_notification_remove(uid, doc_id)

        except Exception as e:
            self._logger.error(
                f"[NOTIFICATION] Failed to mark_read {doc_id} for uid={uid}: {e}"
            )
            await self._send_error(uid, str(e))

    async def _cleanup_bankbookeeper_data(
        self,
        uid: str,
        notif_data: Dict[str, Any],
    ) -> None:
        """
        Clean up Bankbookeeper-related data when marking notification as read.

        Migrated from NotificationState.mark_as_read side effects.
        """
        try:
            job_id = notif_data.get("job_id", notif_data.get("jobId", ""))
            collection_id = notif_data.get("collection_id", notif_data.get("collectionId", ""))

            if not job_id or not collection_id:
                return

            # Delete from RTDB: active_chats/{uid}/{job_id}
            try:
                self._rtdb.db.child(f"active_chats/{uid}/{job_id}").delete()
            except Exception:
                pass

            # Delete from RTDB: job_chats/{collection_id}/{job_id}
            try:
                self._rtdb.db.child(f"job_chats/{collection_id}/{job_id}").delete()
            except Exception:
                pass

            # Delete from Firestore: task_manager/{uid}/tasks/{job_id}
            try:
                db = get_firestore()
                task_ref = (
                    db.collection("task_manager")
                    .document(uid)
                    .collection("tasks")
                    .document(job_id)
                )
                task_ref.delete()
            except Exception:
                pass

            self._logger.debug(
                f"[NOTIFICATION] Cleaned up Bankbookeeper data for job={job_id}"
            )

        except Exception as e:
            self._logger.warning(
                f"[NOTIFICATION] Failed to cleanup Bankbookeeper data: {e}"
            )

    # ─────────────────────────────────────────
    # notification.click
    # ─────────────────────────────────────────

    async def handle_click(
        self,
        uid: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Handle notification.click event.

        Determines redirect path based on functionName.

        Redirect rules (migrated from NotificationState.click_notification):
        - Router -> /routing/{drive_id}
        - APbookeeper -> /invoices/{job_id}
        - Bankbookeeper -> /banking/{batch_id}
        - Default -> /dashboard

        Args:
            uid: Firebase user ID
            payload: {
                docId, jobId, fileId, functionName, collectionId, batchId?
            }
        """
        function_name = payload.get("functionName", "")
        job_id = payload.get("jobId", "")
        file_id = payload.get("fileId", "")
        collection_id = payload.get("collectionId", "")
        batch_id = payload.get("batchId", "")

        try:
            # Determine redirect path
            redirect_path = "/dashboard"  # Default

            if function_name == "Router":
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
            # (The frontend will handle this based on current company vs collectionId)
            needs_company_change = False
            target_company_id = None

            if collection_id:
                # Let frontend decide if company change is needed
                target_company_id = collection_id

            # Send result
            await hub.broadcast(uid, {
                "type": WS_EVENTS.NOTIFICATION.CLICK_RESULT,
                "payload": {
                    "success": True,
                    "redirect": {
                        "path": redirect_path,
                    },
                    "needsCompanyChange": needs_company_change,
                    "targetCompanyId": target_company_id,
                },
            })

            self._logger.info(
                f"[NOTIFICATION] Click handled for uid={uid}, redirect={redirect_path}"
            )

        except Exception as e:
            self._logger.error(
                f"[NOTIFICATION] Failed to handle click for uid={uid}: {e}"
            )
            await self._send_error(uid, str(e))

    # ─────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────

    async def _send_error(self, uid: str, error: str) -> None:
        """Send error event to frontend."""
        await hub.broadcast(uid, {
            "type": WS_EVENTS.NOTIFICATION.ERROR,
            "payload": {"error": error},
        })


# ============================================
# Handler Functions (for WebSocket router)
# ============================================

async def handle_notification_mark_read(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle notification.mark_read WebSocket event."""
    handlers = get_notification_handlers()
    await handlers.handle_mark_read(uid, payload)


async def handle_notification_click(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle notification.click WebSocket event."""
    handlers = get_notification_handlers()
    await handlers.handle_click(uid, payload)
