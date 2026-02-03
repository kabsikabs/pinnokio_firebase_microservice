"""
Worker Broadcast Listener - Ecoute les broadcasts du worker via Redis PubSub.

Le worker (pinnokio_agentic_worker) publie des evenements via Redis PubSub.
Ce listener les recoit et les transmet aux clients WebSocket.

Architecture:
- Worker -> Redis PubSub (ws:broadcast:{user_id}) -> Listener -> WebSocketHub -> Frontend

Channels ecoutes:
- ws:broadcast:* - Evenements generaux (llm_response, tool_execution, etc.)
- ws:stream:*    - Streaming LLM chunks
- ws:notification:* - Notifications utilisateur
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from ..redis_client import get_redis

logger = logging.getLogger("realtime.worker_broadcast")


class WorkerBroadcastListener:
    """
    Listener pour les broadcasts du worker agentique.

    Souscrit aux channels Redis PubSub et forward les messages
    vers le WebSocketHub.
    """

    # Patterns de channels a ecouter
    CHANNEL_PATTERNS = [
        "ws:broadcast:*",
        "ws:stream:*",
        "ws:notification:*",
    ]

    def __init__(self):
        self._redis = None
        self._pubsub = None
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None
        self._hub = None

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

    async def start(self):
        """
        Demarre l'ecoute des broadcasts du worker.

        Doit etre appele au demarrage de l'application.
        """
        if self._running:
            logger.warning("WorkerBroadcastListener already running")
            return

        logger.info("Starting WorkerBroadcastListener...")

        self._pubsub = self.redis.pubsub()

        # Souscrire aux patterns
        for pattern in self.CHANNEL_PATTERNS:
            self._pubsub.psubscribe(pattern)
            logger.info(f"Subscribed to pattern: {pattern}")

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
            self._pubsub.punsubscribe()
            self._pubsub.close()

        logger.info("WorkerBroadcastListener stopped")

    async def _listen_loop(self):
        """
        Boucle d'ecoute des messages PubSub.
        """
        logger.info("WorkerBroadcastListener listen loop started")

        while self._running:
            try:
                # Utiliser get_message avec timeout pour ne pas bloquer
                message = self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.1  # 100ms
                )

                if message is None:
                    await asyncio.sleep(0.01)  # Yield to other tasks
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
                - type: "pmessage" pour pattern match
                - pattern: Le pattern qui a matche
                - channel: Le channel complet (ex: "ws:broadcast:user123")
                - data: Le payload JSON
        """
        if message["type"] != "pmessage":
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
        Forward un message vers le WebSocketHub.

        Args:
            user_id: ID de l'utilisateur cible
            payload: Payload a envoyer
            channel_type: Type de channel (broadcast, stream, notification)
        """
        try:
            # Verifier si l'utilisateur est connecte
            if not self.hub.is_user_connected(user_id):
                logger.debug(
                    f"User {user_id} not connected, message will be buffered"
                )
                # Le hub.broadcast gere le buffering

            await self.hub.broadcast(user_id, payload)

            logger.debug(
                f"Forwarded to WebSocket: user={user_id} "
                f"type={payload.get('type', 'unknown')}"
            )

        except Exception as e:
            logger.error(f"Error forwarding to WebSocket: {e}")


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
