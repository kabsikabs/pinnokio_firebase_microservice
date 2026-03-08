"""
Worker Broadcast Listener - Ecoute les broadcasts du worker via Redis PubSub.

Le worker (pinnokio_agentic_worker) publie des evenements via Redis PubSub.
Ce listener les recoit et les transmet aux clients WebSocket.

Architecture:
- Worker -> Redis PubSub (ws:broadcast:{user_id}) -> Listener -> WebSocketHub -> Frontend

Channels ecoutes (per-user dynamic subscribe):
- ws:broadcast:{uid} - Evenements generaux (llm_response, tool_execution, etc.)
- ws:stream:{uid}    - Streaming LLM chunks
- ws:notification:{uid} - Notifications utilisateur

Compatible ElastiCache Serverless (no psubscribe).
"""

import asyncio
import json
import logging
from typing import Any, Optional, Set

from ..redis_client import get_redis

logger = logging.getLogger("realtime.worker_broadcast")

# Types de messages dispatchables vers canaux externes
DISPATCHABLE_TYPES = {
    "llm_stream_complete", "llm.stream_end",  # Message final LLM
    "CARD", "FOLLOW_CARD", "CMMD",            # Cartes interactives
    "notification",                            # Notifications
    "llm_stream_error",                        # Erreurs
}

# Channel prefixes per user
CHANNEL_PREFIXES = ["ws:broadcast", "ws:stream", "ws:notification"]


class WorkerBroadcastListener:
    """
    Listener pour les broadcasts du worker agentique.

    Souscrit aux channels Redis PubSub par utilisateur connecte
    et forward les messages vers le WebSocketHub.
    """

    def __init__(self):
        self._redis = None
        self._pubsub = None
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None
        self._hub = None
        self._subscribed_uids: Set[str] = set()
        self._sub_lock = asyncio.Lock()

    @property
    def redis(self):
        """Lazy load du client Redis."""
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    @property
    def hub(self):
        """Lazy load du WebSocketHub."""
        if self._hub is None:
            from ..ws_hub import hub
            self._hub = hub
        return self._hub

    def _channels_for_uid(self, uid: str) -> list[str]:
        """Return the 3 channels for a given user."""
        return [f"{prefix}:{uid}" for prefix in CHANNEL_PREFIXES]

    async def subscribe_user(self, uid: str) -> None:
        """Subscribe to all broadcast channels for a user."""
        async with self._sub_lock:
            if uid in self._subscribed_uids:
                return
            if not self._pubsub:
                return
            channels = self._channels_for_uid(uid)
            try:
                await asyncio.to_thread(self._pubsub.subscribe, *channels)
                self._subscribed_uids.add(uid)
                logger.info("worker_broadcast_subscribe uid=%s channels=%s", uid, channels)
            except Exception as e:
                logger.error("worker_broadcast_subscribe_error uid=%s error=%s", uid, repr(e))

    async def unsubscribe_user(self, uid: str) -> None:
        """Unsubscribe from all broadcast channels for a user."""
        async with self._sub_lock:
            if uid not in self._subscribed_uids:
                return
            if not self._pubsub:
                return
            channels = self._channels_for_uid(uid)
            try:
                await asyncio.to_thread(self._pubsub.unsubscribe, *channels)
                self._subscribed_uids.discard(uid)
                logger.info("worker_broadcast_unsubscribe uid=%s", uid)
            except Exception as e:
                logger.error("worker_broadcast_unsubscribe_error uid=%s error=%s", uid, repr(e))

    async def start(self):
        """
        Demarre l'ecoute des broadcasts du worker.

        Doit etre appele au demarrage de l'application.
        """
        if self._running:
            logger.warning("WorkerBroadcastListener already running")
            return

        logger.info("Starting WorkerBroadcastListener (dynamic subscribe mode)...")

        self._pubsub = self.redis.pubsub()

        # Register hub callbacks for dynamic subscribe/unsubscribe
        self.hub.on_first_connect(self.subscribe_user)
        self.hub.on_last_disconnect(self.unsubscribe_user)

        # Subscribe for already-connected users (in case of restart)
        for uid in self.hub.get_connected_users():
            await self.subscribe_user(uid)

        self._running = True
        self._listener_task = asyncio.create_task(self._listen_loop())

        logger.info("WorkerBroadcastListener started successfully")

    async def stop(self):
        """
        Arrete l'ecoute des broadcasts.
        """
        logger.info("Stopping WorkerBroadcastListener...")

        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            try:
                await asyncio.to_thread(self._pubsub.unsubscribe)
                await asyncio.to_thread(self._pubsub.close)
            except Exception:
                pass

        self._subscribed_uids.clear()
        logger.info("WorkerBroadcastListener stopped")

    async def _listen_loop(self):
        """
        Boucle d'ecoute des messages PubSub.
        """
        logger.info("WorkerBroadcastListener listen loop started")

        while self._running:
            try:
                message = await asyncio.to_thread(
                    self._pubsub.get_message,
                    ignore_subscribe_messages=True,
                    timeout=0.1,
                )

                if message is None:
                    await asyncio.sleep(0.01)
                    continue

                await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in listen loop: {e}")
                await asyncio.sleep(1)

        logger.info("WorkerBroadcastListener listen loop ended")

    async def _handle_message(self, message: dict[str, Any]):
        """
        Traite un message recu du PubSub.

        Args:
            message: Message Redis PubSub avec:
                - type: "message" pour subscribe direct
                - channel: Le channel complet (ex: "ws:broadcast:user123")
                - data: Le payload JSON
        """
        if message["type"] != "message":
            return

        channel = message["channel"]
        if isinstance(channel, bytes):
            channel = channel.decode("utf-8")

        # Extraire le user_id du channel
        # Format: ws:{type}:{user_id}
        parts = channel.split(":")
        if len(parts) < 3:
            logger.warning(f"Invalid channel format: {channel}")
            return

        channel_type = parts[1]  # broadcast, stream, notification
        user_id = parts[2]

        # Decoder le payload
        data = message["data"]
        if isinstance(data, bytes):
            data = data.decode("utf-8")

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message: {e}")
            return

        logger.debug(
            f"Worker broadcast received: channel={channel} "
            f"type={payload.get('type', 'unknown')}"
        )

        # Forward vers le WebSocketHub
        await self._forward_to_websocket(user_id, payload, channel_type)

    async def _forward_to_websocket(
        self,
        user_id: str,
        payload: dict[str, Any],
        channel_type: str
    ):
        """
        Forward un message vers le WebSocketHub ou vers un canal externe.

        Si communication_chat_type != "pinnokio", route vers
        CommunicationDispatcher.dispatch_outbound() pour les types dispatchables.
        """
        communication_chat_type = payload.get("communication_chat_type", "pinnokio")

        if communication_chat_type == "pinnokio":
            try:
                if not self.hub.is_user_connected(user_id):
                    logger.debug(
                        f"User {user_id} not connected, message will be buffered"
                    )

                await self.hub.broadcast(user_id, payload)

                logger.debug(
                    f"Forwarded to WebSocket: user={user_id} "
                    f"type={payload.get('type', 'unknown')}"
                )
            except Exception as e:
                logger.error(f"Error forwarding to WebSocket: {e}")
        else:
            msg_type = payload.get("type", "")
            if msg_type in DISPATCHABLE_TYPES:
                try:
                    from app.realtime.communication_dispatcher import get_communication_dispatcher
                    dispatcher = get_communication_dispatcher()
                    await dispatcher.dispatch_outbound(
                        channel=communication_chat_type,
                        uid=user_id,
                        external_thread_id=payload.get("external_thread_id"),
                        message_type=msg_type,
                        payload=payload,
                    )
                    logger.info(
                        f"Dispatched to external channel: user={user_id} "
                        f"channel={communication_chat_type} type={msg_type}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error dispatching to external channel: "
                        f"user={user_id} channel={communication_chat_type} error={e}"
                    )
            else:
                logger.debug(
                    f"Skipped non-dispatchable type for external: "
                    f"user={user_id} channel={communication_chat_type} type={msg_type}"
                )


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_listener: Optional[WorkerBroadcastListener] = None


def get_worker_broadcast_listener() -> WorkerBroadcastListener:
    """
    Retourne l'instance singleton du WorkerBroadcastListener.
    """
    global _listener
    if _listener is None:
        _listener = WorkerBroadcastListener()
    return _listener


async def start_worker_broadcast_listener():
    """
    Demarre le listener (a appeler au startup de l'app).
    """
    listener = get_worker_broadcast_listener()
    await listener.start()


async def stop_worker_broadcast_listener():
    """
    Arrete le listener (a appeler au shutdown de l'app).
    """
    global _listener
    if _listener:
        await _listener.stop()
        _listener = None
