"""
PubSub Helper for Realtime Events
=================================

Ce module fournit des helpers pour les jobbeurs (Router, APbookeeper, Bankbookeeper)
qui doivent publier des événements de notification et de message.

FLUX:
1. Le jobbeur écrit la donnée dans Firebase/RTDB (persistance)
2. Le jobbeur appelle publish_notification_event ou publish_messenger_event
3. Si l'utilisateur est connecté, l'événement est publié sur Redis PubSub
4. Le subscription_manager reçoit l'événement et le broadcast via WebSocket

IMPORTANT:
- Ne publie QUE si l'utilisateur est connecté (optimisation)
- Les données doivent d'abord être écrites dans Firebase/RTDB avant publication
- Les notifications/messages sont au niveau USER (global) - pas de filtrage par société/page

ARCHITECTURE:
- Utilise le système de publication contextuelle (contextual_publisher.py)
- Niveau USER pour notifications/messages (globaux)
- Cache mis à jour même si non publié
"""

import json
import logging
from typing import Any, Dict, Literal, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.realtime.contextual_publisher import publish_user_event, PublicationLevel

logger = logging.getLogger(__name__)

# ============================================
# Channel Names
# ============================================

NOTIFICATION_CHANNEL_PREFIX = "notification"
MESSENGER_CHANNEL_PREFIX = "messenger"


def _get_notification_channel(uid: str) -> str:
    """Get Redis PubSub channel name for notifications."""
    return f"{NOTIFICATION_CHANNEL_PREFIX}:{uid}"


def _get_messenger_channel(uid: str) -> str:
    """Get Redis PubSub channel name for messages."""
    return f"{MESSENGER_CHANNEL_PREFIX}:{uid}"


# ============================================
# Connection Check
# ============================================

def is_user_connected(uid: str) -> bool:
    """
    Check if a user has an active WebSocket connection.

    Args:
        uid: Firebase user ID

    Returns:
        True if user is connected, False otherwise
    """
    return hub.is_user_connected(uid)


# ============================================
# Notification Events
# ============================================

async def publish_notification_event(
    uid: str,
    action: Literal["new", "update", "remove"],
    data: Dict[str, Any],
    skip_connection_check: bool = False,
) -> bool:
    """
    Publish a notification event to Redis PubSub.

    This function should be called by jobbeurs AFTER writing the notification
    to Firebase Firestore. It will broadcast the event to the frontend if
    the user is connected.

    Args:
        uid: Firebase user ID
        action: Type of action ('new', 'update', 'remove')
        data: Notification data (for 'new'/'update') or {'docId': ...} (for 'remove')
        skip_connection_check: If True, publish even if user is not connected

    Returns:
        True if event was published, False if user was not connected

    Example:
        # After writing notification to Firebase
        await publish_notification_event(
            uid="user123",
            action="new",
            data={
                "docId": "notif_abc123",
                "message": "Invoice processed successfully",
                "status": "completed",
                "functionName": "APbookeeper",
                # ... other fields
            }
        )
    """
    if not skip_connection_check and not is_user_connected(uid):
        logger.debug(
            f"[NOTIFICATION] User {uid} not connected, skipping publish for action={action}"
        )
        return False

    try:
        # Utiliser le système de publication contextuelle (niveau USER)
        event_type = WS_EVENTS.NOTIFICATION.DELTA
        payload = {"action": action, "data": data}
        
        # Publication au niveau USER (global - pas de filtrage par société/page)
        published = await publish_user_event(
            uid=uid,
            event_type=event_type,
            payload=payload
        )
        
        if published:
            logger.info(
                f"[NOTIFICATION] Published {action} event for uid={uid}, docId={data.get('docId', 'unknown')}"
            )
        else:
            logger.debug(
                f"[NOTIFICATION] Event not published (user not connected): uid={uid}, action={action}"
            )
        
        return published

    except Exception as e:
        logger.error(
            f"[NOTIFICATION] Failed to publish event for uid={uid}: {e}"
        )
        return False


async def publish_notification_new(uid: str, notification: Dict[str, Any]) -> bool:
    """Shortcut for publishing a new notification."""
    return await publish_notification_event(uid, "new", notification)


async def publish_notification_update(uid: str, notification: Dict[str, Any]) -> bool:
    """Shortcut for publishing a notification update."""
    return await publish_notification_event(uid, "update", notification)


async def publish_notification_remove(uid: str, doc_id: str) -> bool:
    """Shortcut for publishing a notification removal."""
    return await publish_notification_event(uid, "remove", {"docId": doc_id})


# ============================================
# Messenger Events
# ============================================

async def publish_messenger_event(
    uid: str,
    action: Literal["new", "update", "remove"],
    data: Dict[str, Any],
    skip_connection_check: bool = False,
) -> bool:
    """
    Publish a messenger event to Redis PubSub.

    This function should be called by jobbeurs AFTER writing the message
    to Firebase RTDB. It will broadcast the event to the frontend if
    the user is connected.

    Args:
        uid: Firebase user ID
        action: Type of action ('new', 'update', 'remove')
        data: Message data (for 'new'/'update') or {'docId': ...} (for 'remove')
        skip_connection_check: If True, publish even if user is not connected

    Returns:
        True if event was published, False if user was not connected

    Example:
        # After writing message to RTDB
        await publish_messenger_event(
            uid="user123",
            action="new",
            data={
                "docId": "msg_abc123",
                "message": "New chat message",
                "functionName": "Chat",
                # ... other fields
            }
        )
    """
    if not skip_connection_check and not is_user_connected(uid):
        logger.debug(
            f"[MESSENGER] User {uid} not connected, skipping publish for action={action}"
        )
        return False

    try:
        # Utiliser le système de publication contextuelle (niveau USER)
        event_type = WS_EVENTS.MESSENGER.DELTA
        payload = {"action": action, "data": data}
        
        # Publication au niveau USER (global - pas de filtrage par société/page)
        published = await publish_user_event(
            uid=uid,
            event_type=event_type,
            payload=payload
        )
        
        if published:
            logger.info(
                f"[MESSENGER] Published {action} event for uid={uid}, docId={data.get('docId', 'unknown')}"
            )
        else:
            logger.debug(
                f"[MESSENGER] Event not published (user not connected): uid={uid}, action={action}"
            )
        
        return published

    except Exception as e:
        logger.error(
            f"[MESSENGER] Failed to publish event for uid={uid}: {e}"
        )
        return False


async def publish_messenger_new(uid: str, message: Dict[str, Any]) -> bool:
    """Shortcut for publishing a new message."""
    return await publish_messenger_event(uid, "new", message)


async def publish_messenger_remove(uid: str, doc_id: str) -> bool:
    """Shortcut for publishing a message removal."""
    return await publish_messenger_event(uid, "remove", {"docId": doc_id})


# ============================================
# Batch Publishing (for periodic sync)
# ============================================

async def publish_notifications_full_data(
    uid: str,
    notifications: list,
    skip_connection_check: bool = False,
) -> bool:
    """
    Publish full notifications data to a user.

    Used during initial load and periodic sync.

    Args:
        uid: Firebase user ID
        notifications: List of notification objects
        skip_connection_check: If True, publish even if user is not connected

    Returns:
        True if published, False otherwise
    """
    if not skip_connection_check and not is_user_connected(uid):
        return False

    try:
        hub.broadcast_threadsafe(uid, {
            "type": WS_EVENTS.NOTIFICATION.FULL_DATA,
            "payload": {
                "notifications": notifications,
                "count": len(notifications),
            },
        })
        logger.info(
            f"[NOTIFICATION] Published full_data for uid={uid}, count={len(notifications)}"
        )
        return True
    except Exception as e:
        logger.error(f"[NOTIFICATION] Failed to publish full_data for uid={uid}: {e}")
        return False


async def publish_messages_full_data(
    uid: str,
    messages: list,
    skip_connection_check: bool = False,
) -> bool:
    """
    Publish full messages data to a user.

    Used during initial load and periodic sync.

    Args:
        uid: Firebase user ID
        messages: List of message objects
        skip_connection_check: If True, publish even if user is not connected

    Returns:
        True if published, False otherwise
    """
    if not skip_connection_check and not is_user_connected(uid):
        return False

    try:
        hub.broadcast_threadsafe(uid, {
            "type": WS_EVENTS.MESSENGER.FULL_DATA,
            "payload": {
                "messages": messages,
                "count": len(messages),
            },
        })
        logger.info(
            f"[MESSENGER] Published full_data for uid={uid}, count={len(messages)}"
        )
        return True
    except Exception as e:
        logger.error(f"[MESSENGER] Failed to publish full_data for uid={uid}: {e}")
        return False


# ============================================
# Job Chat Events (Onboarding Manager)
# ============================================

async def publish_job_chat_message(
    uid: str,
    collection_name: str,
    job_id: str,
    message_data: Dict[str, Any],
    thread_key: Optional[str] = None,
    skip_connection_check: bool = False,
) -> bool:
    """
    Publie un message job_chat sur Redis PubSub.

    Cette fonction doit être appelée par les jobbeurs APRÈS avoir écrit le message
    dans RTDB ({collection}/job_chats/{job_id}/messages).

    Canal Redis: user:{uid}/{collection}/job_chats/{job_id}/messages

    Args:
        uid: Firebase user ID
        collection_name: Collection/company ID
        job_id: Job ID
        message_data: Données du message (format RTDB)
        thread_key: Thread key (optionnel, par défaut job_id)
        skip_connection_check: Si True, publie même si utilisateur non connecté

    Returns:
        True si publié avec succès, False sinon

    Example:
        # Après écriture dans RTDB
        await rtdb.send_message(...)
        
        # Publication Redis
        await publish_job_chat_message(
            uid="user123",
            collection_name="company_12345",
            job_id="router_batch_1706234567",
            message_data={
                "id": "msg_abc123",
                "message_type": "MESSAGE",
                "content": "Document processed successfully",
                "timestamp": "2026-01-26T10:30:00Z"
            },
            thread_key="klk_router_batch_1706234567"
        )
    """
    try:
        from app.redis_client import get_redis
        
        redis = get_redis()
        channel = f"user:{uid}/{collection_name}/job_chats/{job_id}/messages"
        
        payload = {
            "type": "job_chat_message",
            "job_id": job_id,
            "collection_name": collection_name,
            "thread_key": thread_key or job_id,
            "message": message_data
        }
        
        # Publier sur Redis PubSub (toujours, même si utilisateur non connecté)
        # Le RedisSubscriber gérera la logique de routage
        await redis.publish(channel, json.dumps(payload))
        
        logger.info(
            f"[JOB_CHAT] Published message uid={uid} collection={collection_name} "
            f"job_id={job_id} channel={channel}"
        )
        
        return True
        
    except Exception as e:
        logger.error(
            f"[JOB_CHAT] Failed to publish message uid={uid} collection={collection_name} "
            f"job_id={job_id} error={e}",
            exc_info=True
        )
        return False
