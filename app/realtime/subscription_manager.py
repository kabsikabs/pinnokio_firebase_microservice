"""
Realtime Subscription Manager
=============================

Singleton qui gère les subscriptions temps réel pour les utilisateurs connectés.

RESPONSABILITÉS:
1. Charger les données initiales depuis Firebase/RTDB à la connexion
2. Mettre en cache dans Redis
3. Envoyer FULL_DATA au frontend
4. Planifier la synchronisation périodique

ARCHITECTURE:
- Les notifications proviennent de Firestore: clients/{uid}/notifications
- Les messages proviennent de RTDB: direct_messages/{uid}
- Le cache Redis utilise les clés: realtime:{uid}:notifications et realtime:{uid}:messages
- La synchronisation périodique se fait toutes les 2h pour les utilisateurs connectés
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.redis_client import get_redis
from app.firebase_providers import get_firebase_management, get_firebase_realtime
from app.ws_hub import hub
from app.ws_events import WS_EVENTS

logger = logging.getLogger(__name__)

# ============================================
# Configuration
# ============================================

# Cache TTL (2 hours - matches periodic sync interval)
CACHE_TTL_SECONDS = 7200

# Redis key prefixes
CACHE_KEY_PREFIX = "realtime"
NOTIFICATIONS_SUFFIX = "notifications"
MESSAGES_SUFFIX = "messages"


def _get_notifications_cache_key(uid: str) -> str:
    """Get Redis cache key for notifications."""
    return f"{CACHE_KEY_PREFIX}:{uid}:{NOTIFICATIONS_SUFFIX}"


def _get_messages_cache_key(uid: str) -> str:
    """Get Redis cache key for messages."""
    return f"{CACHE_KEY_PREFIX}:{uid}:{MESSAGES_SUFFIX}"


# ============================================
# Subscription Manager Singleton
# ============================================

_instance: Optional["RealtimeSubscriptionManager"] = None


def get_subscription_manager() -> "RealtimeSubscriptionManager":
    """Get singleton instance of RealtimeSubscriptionManager."""
    global _instance
    if _instance is None:
        _instance = RealtimeSubscriptionManager()
    return _instance


class RealtimeSubscriptionManager:
    """
    Manages realtime subscriptions for connected users.

    This class handles:
    - Loading initial data from Firebase/RTDB
    - Caching in Redis
    - Sending FULL_DATA events to frontend
    - Periodic synchronization

    Usage:
        manager = get_subscription_manager()
        await manager.start_user_subscriptions(uid)
    """

    def __init__(self):
        self._redis = get_redis()
        self._firebase = get_firebase_management()  # Firestore
        self._rtdb = get_firebase_realtime()        # RTDB
        self._active_users: Set[str] = set()
        self._sync_task: Optional[asyncio.Task] = None
        self._pubsub_tasks: Dict[str, asyncio.Task] = {}  # PubSub tasks par uid
        self._logger = logging.getLogger("realtime.manager")

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    async def start_user_subscriptions(self, uid: str) -> Dict[str, Any]:
        """
        Start realtime subscriptions for a user.

        This should be called after successful authentication,
        typically in the dashboard orchestration Phase 4.

        Flow:
        1. Load notifications from Firestore
        2. Load messages from RTDB
        3. Cache both in Redis
        4. Send FULL_DATA events to frontend
        5. Track user as active

        Args:
            uid: Firebase user ID

        Returns:
            Dict with success status and counts
        """
        self._logger.info(f"[REALTIME] Starting subscriptions for uid={uid}")

        try:
            # 1. Load notifications from Firestore
            notifications = await self._load_notifications(uid)

            # 2. Load messages from RTDB
            messages = await self._load_messages(uid)

            # 3. Cache in Redis
            await self._cache_data(uid, notifications, messages)

            # 4. Send FULL_DATA to frontend
            await self._send_full_data(uid, notifications, messages)

            # 5. Subscribe to Redis PubSub channels for real-time updates
            await self._subscribe_to_pubsub_channels(uid)

            # 6. Track as active
            self._active_users.add(uid)

            self._logger.info(
                f"[REALTIME] Subscriptions started for uid={uid}, "
                f"notifications={len(notifications)}, messages={len(messages)}"
            )

            return {
                "success": True,
                "notification_count": len(notifications),
                "message_count": len(messages),
            }

        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to start subscriptions for uid={uid}: {e}"
            )
            return {
                "success": False,
                "error": str(e),
            }

    async def stop_user_subscriptions(self, uid: str) -> None:
        """
        Stop realtime subscriptions for a user.

        Called when user disconnects or logs out.

        Args:
            uid: Firebase user ID
        """
        self._logger.info(f"[REALTIME] Stopping subscriptions for uid={uid}")
        self._active_users.discard(uid)
        
        # Stop PubSub subscription task
        if uid in self._pubsub_tasks:
            task = self._pubsub_tasks.pop(uid)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._logger.info(f"[REALTIME] Stopped PubSub subscription for uid={uid}")

    async def refresh_user_data(self, uid: str) -> Dict[str, Any]:
        """
        Refresh data for a connected user.

        Used for manual refresh or periodic sync.

        Args:
            uid: Firebase user ID

        Returns:
            Dict with success status and counts
        """
        if not hub.is_user_connected(uid):
            return {"success": False, "error": "User not connected"}

        return await self.start_user_subscriptions(uid)

    # ─────────────────────────────────────────
    # Data Loading
    # ─────────────────────────────────────────

    async def _load_notifications(self, uid: str) -> List[Dict[str, Any]]:
        """
        Load notifications from Firestore using FirebaseManagement.get_notifications().

        Path: clients/{uid}/notifications
        Filter: read == False (unread only for display)

        Returns:
            List of notification documents
        """
        try:
            # Use existing FirebaseManagement method
            raw_notifications = await asyncio.to_thread(
                self._firebase.get_notifications,
                uid,
                False,  # read=False (unread only)
                50      # limit
            )

            if not raw_notifications:
                return []

            notifications = []
            for data in raw_notifications:
                if data:
                    doc_id = data.get("id", "")
                    notification = self._transform_notification(doc_id, data)
                    notifications.append(notification)

            self._logger.debug(
                f"[REALTIME] Loaded {len(notifications)} notifications for uid={uid}"
            )
            return notifications

        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to load notifications for uid={uid}: {e}"
            )
            return []

    async def _load_messages(self, uid: str) -> List[Dict[str, Any]]:
        """
        Load messages from Firebase RTDB using FirebaseRealtimeChat singleton.

        Path: clients/{uid}/direct_message_notif

        Returns:
            List of message documents
        """
        try:
            # Access RTDB via FirebaseRealtimeChat singleton
            # Path is clients/{uid}/direct_message_notif based on existing get_unread_direct_messages
            messages_path = f"clients/{uid}/direct_message_notif"

            def _fetch_rtdb():
                try:
                    messages_ref = self._rtdb.db.child(messages_path)
                    return messages_ref.get()
                except Exception as e:
                    self._logger.warning(f"[REALTIME] RTDB fetch error: {e}")
                    return None

            messages_data = await asyncio.to_thread(_fetch_rtdb)

            if not messages_data:
                return []

            messages = []
            if isinstance(messages_data, dict):
                for doc_id, data in messages_data.items():
                    if data and isinstance(data, dict):
                        message = self._transform_message(doc_id, data)
                        messages.append(message)

            # Sort by timestamp descending
            messages.sort(
                key=lambda m: m.get("timestamp", ""),
                reverse=True
            )

            # Limit to 50 most recent
            messages = messages[:50]

            self._logger.debug(
                f"[REALTIME] Loaded {len(messages)} messages for uid={uid}"
            )
            return messages

        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to load messages for uid={uid}: {e}"
            )
            return []

    # ─────────────────────────────────────────
    # Data Transformation
    # ─────────────────────────────────────────

    def _serialize_timestamp(self, value: Any) -> str:
        """Convert Firestore timestamp to ISO string."""
        if value is None:
            return ""
        # Handle Firestore DatetimeWithNanoseconds
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        # Handle datetime objects
        if isinstance(value, datetime):
            return value.isoformat()
        # Already a string
        if isinstance(value, str):
            return value
        # Fallback
        return str(value)

    def _transform_notification(
        self, doc_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Transform Firestore notification document to frontend format.

        Matches the Notification interface in types/notification.ts
        """
        # Parse additional_info if it's a string
        additional_info = data.get("additional_info", "")
        has_additional_info = False
        additional_info_formatted = ""

        if additional_info:
            has_additional_info = True
            if isinstance(additional_info, str):
                try:
                    parsed = json.loads(additional_info)
                    additional_info_formatted = json.dumps(parsed, indent=2)
                except json.JSONDecodeError:
                    additional_info_formatted = additional_info
            else:
                additional_info_formatted = json.dumps(additional_info, indent=2)

        return {
            "docId": doc_id,
            "message": data.get("message", ""),
            "fileName": data.get("file_name", data.get("fileName", "")),
            "collectionId": data.get("collection_id", data.get("collectionId", "")),
            "collectionName": data.get("collection_name", data.get("collectionName", "")),
            "status": data.get("status", "pending"),
            "read": data.get("read", False),
            "jobId": data.get("job_id", data.get("jobId", "")),
            "fileId": data.get("file_id", data.get("fileId", "")),
            "functionName": data.get("function_name", data.get("functionName", "Router")),
            "timestamp": self._serialize_timestamp(data.get("timestamp")),
            "additionalInfo": additional_info,
            "hasAdditionalInfo": has_additional_info,
            "additionalInfoFormatted": additional_info_formatted,
            "driveLink": data.get("drive_link", data.get("driveLink", "")),
            "batchId": data.get("batch_id", data.get("batchId", "")),
        }

    def _transform_message(
        self, doc_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Transform RTDB message document to frontend format.

        Matches the DirectMessage interface in types/messenger.ts
        """
        return {
            "docId": doc_id,
            "message": data.get("message", ""),
            "fileName": data.get("file_name", data.get("fileName", "")),
            "collectionId": data.get("collection_id", data.get("collectionId", "")),
            "collectionName": data.get("collection_name", data.get("collectionName", "")),
            "status": data.get("status", ""),
            "jobId": data.get("job_id", data.get("jobId", "")),
            "fileId": data.get("file_id", data.get("fileId", "")),
            "functionName": data.get("function_name", data.get("functionName", "Chat")),
            "timestamp": self._serialize_timestamp(data.get("timestamp")),
            "additionalInfo": data.get("additional_info", data.get("additionalInfo", "")),
            "chatMode": data.get("chat_mode", data.get("chatMode", "")),
            "threadKey": data.get("thread_key", data.get("threadKey", "")),
            "driveLink": data.get("drive_link", data.get("driveLink", "")),
            "batchId": data.get("batch_id", data.get("batchId", "")),
        }

    # ─────────────────────────────────────────
    # Caching
    # ─────────────────────────────────────────

    async def _cache_data(
        self,
        uid: str,
        notifications: List[Dict],
        messages: List[Dict],
    ) -> None:
        """Cache notifications and messages in Redis."""
        try:
            # Cache notifications
            notif_key = _get_notifications_cache_key(uid)
            self._redis.setex(
                notif_key,
                CACHE_TTL_SECONDS,
                json.dumps(notifications),
            )

            # Cache messages
            msg_key = _get_messages_cache_key(uid)
            self._redis.setex(
                msg_key,
                CACHE_TTL_SECONDS,
                json.dumps(messages),
            )

            self._logger.debug(
                f"[REALTIME] Cached data for uid={uid}, TTL={CACHE_TTL_SECONDS}s"
            )

        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to cache data for uid={uid}: {e}"
            )

    async def get_cached_notifications(self, uid: str) -> Optional[List[Dict]]:
        """Get cached notifications from Redis."""
        try:
            key = _get_notifications_cache_key(uid)
            data = self._redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to get cached notifications for uid={uid}: {e}"
            )
            return None

    async def get_cached_messages(self, uid: str) -> Optional[List[Dict]]:
        """Get cached messages from Redis."""
        try:
            key = _get_messages_cache_key(uid)
            data = self._redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to get cached messages for uid={uid}: {e}"
            )
            return None

    # ─────────────────────────────────────────
    # Broadcasting
    # ─────────────────────────────────────────

    async def _send_full_data(
        self,
        uid: str,
        notifications: List[Dict],
        messages: List[Dict],
    ) -> None:
        """Send FULL_DATA events to frontend."""
        try:
            # Send notifications
            await hub.broadcast(uid, {
                "type": WS_EVENTS.NOTIFICATION.FULL_DATA,
                "payload": {
                    "notifications": notifications,
                    "count": len(notifications),
                },
            })

            # Send messages
            await hub.broadcast(uid, {
                "type": WS_EVENTS.MESSENGER.FULL_DATA,
                "payload": {
                    "messages": messages,
                    "count": len(messages),
                },
            })

            self._logger.debug(
                f"[REALTIME] Sent full_data to uid={uid}"
            )

        except Exception as e:
            self._logger.error(
                f"[REALTIME] Failed to send full_data to uid={uid}: {e}"
            )

    # ─────────────────────────────────────────
    # Redis PubSub Subscriptions
    # ─────────────────────────────────────────

    async def _subscribe_to_pubsub_channels(self, uid: str) -> None:
        """
        Subscribe to Redis PubSub channels for real-time updates.
        
        Channels:
        - notification:{uid} - For notification events
        - messenger:{uid} - For message events
        
        Args:
            uid: Firebase user ID
        """
        # Stop existing subscription if any
        if uid in self._pubsub_tasks:
            task = self._pubsub_tasks[uid]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Start new subscription task
        task = asyncio.create_task(self._pubsub_listener_loop(uid))
        self._pubsub_tasks[uid] = task
        self._logger.info(f"[REALTIME] Started PubSub subscription for uid={uid}")

    async def _pubsub_listener_loop(self, uid: str) -> None:
        """
        Background loop that listens to Redis PubSub channels.
        
        Args:
            uid: Firebase user ID
        """
        pubsub = None
        notification_channel = f"notification:{uid}"
        messenger_channel = f"messenger:{uid}"
        
        try:
            # Create PubSub client (synchronous Redis client)
            pubsub = self._redis.pubsub()
            
            # Subscribe to channels (synchronous operation)
            await asyncio.to_thread(
                pubsub.subscribe,
                notification_channel,
                messenger_channel
            )
            self._logger.info(
                f"[REALTIME] Subscribed to channels: {notification_channel}, {messenger_channel}"
            )
            
            # Listen for messages (synchronous operation in thread)
            while True:
                # Get message with timeout to allow cancellation
                try:
                    message = await asyncio.to_thread(
                        pubsub.get_message,
                        timeout=1.0,
                        ignore_subscribe_messages=False
                    )
                    
                    if message is None:
                        # Timeout - continue loop to check for cancellation
                        continue
                    
                    if message['type'] == 'message':
                        await self._handle_pubsub_message(uid, message)
                    elif message['type'] == 'subscribe':
                        self._logger.debug(
                            f"[REALTIME] Subscribed to channel: {message['channel']}"
                        )
                    elif message['type'] == 'unsubscribe':
                        self._logger.debug(
                            f"[REALTIME] Unsubscribed from channel: {message['channel']}"
                        )
                        
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._logger.warning(
                        f"[REALTIME] Error getting PubSub message for uid={uid}: {e}"
                    )
                    await asyncio.sleep(0.1)  # Brief pause before retry
                    
        except asyncio.CancelledError:
            self._logger.info(f"[REALTIME] PubSub listener cancelled for uid={uid}")
            if pubsub:
                try:
                    await asyncio.to_thread(
                        pubsub.unsubscribe,
                        notification_channel,
                        messenger_channel
                    )
                    await asyncio.to_thread(pubsub.close)
                except Exception:
                    pass
        except Exception as e:
            self._logger.error(
                f"[REALTIME] PubSub listener error for uid={uid}: {e}",
                exc_info=True
            )
            if pubsub:
                try:
                    await asyncio.to_thread(pubsub.close)
                except Exception:
                    pass

    async def _handle_pubsub_message(self, uid: str, message: Dict[str, Any]) -> None:
        """
        Handle a message from Redis PubSub.
        
        Args:
            uid: Firebase user ID
            message: Redis PubSub message
        """
        try:
            channel = message.get('channel', b'').decode('utf-8') if isinstance(message.get('channel'), bytes) else message.get('channel', '')
            data_str = message.get('data', b'').decode('utf-8') if isinstance(message.get('data'), bytes) else message.get('data', '')
            
            if not data_str:
                return
            
            # Parse message data
            try:
                event_data = json.loads(data_str)
            except json.JSONDecodeError:
                self._logger.warning(f"[REALTIME] Invalid JSON in PubSub message: {data_str[:100]}")
                return
            
            # Determine event type based on channel
            if channel == f"notification:{uid}":
                await self._handle_notification_event(uid, event_data)
            elif channel == f"messenger:{uid}":
                await self._handle_messenger_event(uid, event_data)
            else:
                self._logger.debug(f"[REALTIME] Unknown channel: {channel}")
                
        except Exception as e:
            self._logger.error(
                f"[REALTIME] Error handling PubSub message for uid={uid}: {e}",
                exc_info=True
            )

    async def _handle_notification_event(self, uid: str, event_data: Dict[str, Any]) -> None:
        """
        Handle a notification event from PubSub.
        
        Args:
            uid: Firebase user ID
            event_data: Event data from PubSub
        """
        try:
            # Extract payload from event
            payload = event_data.get('payload', {})
            action = payload.get('action', 'new')
            data = payload.get('data', {})
            
            # Update cache
            notifications = await self.get_cached_notifications(uid) or []
            
            if action == 'new':
                # Add to cache
                notifications.insert(0, data)
                notifications = notifications[:50]  # Limit to 50
            elif action == 'update':
                # Update in cache
                doc_id = data.get('docId')
                for i, notif in enumerate(notifications):
                    if notif.get('docId') == doc_id:
                        notifications[i] = {**notif, **data}
                        break
            elif action == 'remove':
                # Remove from cache
                doc_id = data.get('docId')
                notifications = [n for n in notifications if n.get('docId') != doc_id]
            
            # Save updated cache
            notif_key = _get_notifications_cache_key(uid)
            self._redis.setex(
                notif_key,
                CACHE_TTL_SECONDS,
                json.dumps(notifications)
            )
            
            # Broadcast to frontend
            await hub.broadcast(uid, {
                "type": WS_EVENTS.NOTIFICATION.DELTA,
                "payload": {
                    "action": action,
                    "data": data
                }
            })
            
            self._logger.debug(
                f"[REALTIME] Handled notification event: action={action} docId={data.get('docId')}"
            )
            
        except Exception as e:
            self._logger.error(
                f"[REALTIME] Error handling notification event for uid={uid}: {e}",
                exc_info=True
            )

    async def _handle_messenger_event(self, uid: str, event_data: Dict[str, Any]) -> None:
        """
        Handle a messenger event from PubSub.
        
        Args:
            uid: Firebase user ID
            event_data: Event data from PubSub
        """
        try:
            # Extract payload from event
            payload = event_data.get('payload', {})
            action = payload.get('action', 'new')
            data = payload.get('data', {})
            
            # Update cache
            messages = await self.get_cached_messages(uid) or []
            
            if action == 'new':
                # Add to cache
                messages.insert(0, data)
                messages = messages[:50]  # Limit to 50
            elif action == 'update':
                # Update in cache
                doc_id = data.get('docId')
                for i, msg in enumerate(messages):
                    if msg.get('docId') == doc_id:
                        messages[i] = {**msg, **data}
                        break
            elif action == 'remove':
                # Remove from cache
                doc_id = data.get('docId')
                messages = [m for m in messages if m.get('docId') != doc_id]
            
            # Save updated cache
            msg_key = _get_messages_cache_key(uid)
            self._redis.setex(
                msg_key,
                CACHE_TTL_SECONDS,
                json.dumps(messages)
            )
            
            # Broadcast to frontend
            await hub.broadcast(uid, {
                "type": WS_EVENTS.MESSENGER.DELTA,
                "payload": {
                    "action": action,
                    "data": data
                }
            })
            
            self._logger.debug(
                f"[REALTIME] Handled messenger event: action={action} docId={data.get('docId')}"
            )
            
        except Exception as e:
            self._logger.error(
                f"[REALTIME] Error handling messenger event for uid={uid}: {e}",
                exc_info=True
            )

    # ─────────────────────────────────────────
    # Periodic Sync
    # ─────────────────────────────────────────

    async def start_periodic_sync(self, interval_seconds: int = CACHE_TTL_SECONDS):
        """
        Start periodic synchronization for all connected users.

        This runs in the background and refreshes data every interval.

        Args:
            interval_seconds: Sync interval (default: 2 hours)
        """
        if self._sync_task and not self._sync_task.done():
            self._logger.warning("[REALTIME] Periodic sync already running")
            return

        self._sync_task = asyncio.create_task(
            self._periodic_sync_loop(interval_seconds)
        )
        self._logger.info(
            f"[REALTIME] Started periodic sync with interval={interval_seconds}s"
        )

    async def stop_periodic_sync(self):
        """Stop periodic synchronization."""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
            self._logger.info("[REALTIME] Stopped periodic sync")

    async def _periodic_sync_loop(self, interval_seconds: int):
        """Background loop for periodic synchronization."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)

                # Get all connected users
                connected_users = hub.get_connected_users()

                if not connected_users:
                    self._logger.debug("[REALTIME] No connected users for periodic sync")
                    continue

                self._logger.info(
                    f"[REALTIME] Running periodic sync for {len(connected_users)} users"
                )

                # Refresh data for each connected user
                for uid in connected_users:
                    try:
                        await self.refresh_user_data(uid)
                    except Exception as e:
                        self._logger.error(
                            f"[REALTIME] Periodic sync failed for uid={uid}: {e}"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[REALTIME] Periodic sync error: {e}")
                # Continue running despite errors
                await asyncio.sleep(60)  # Wait 1 minute before retry
