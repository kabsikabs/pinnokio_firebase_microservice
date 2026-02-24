"""
PubSubTransport — Abstraction dual-mode Google Pub/Sub / Redis.
Permet de developper et tester localement avec Redis, et de basculer
vers Google Pub/Sub en production.

Mode selectionne par env var PUBSUB_TRANSPORT:
- "pubsub" (defaut prod): Google Cloud Pub/Sub via google.cloud.pubsub_v1
- "redis" (dev/fallback): Redis PubSub via app.redis_client.get_redis()

Auto-fallback vers redis si GOOGLE_PROJECT_ID n'est pas defini.
"""

import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("realtime.pubsub_transport")

TRANSPORT_MODE = os.getenv("PUBSUB_TRANSPORT", "pubsub")  # "pubsub" ou "redis"
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID", "")

# Topic/subscription names (configurables)
TELEGRAM_OUTBOUND_TOPIC = os.getenv("PUBSUB_OUTBOUND_TOPIC", "telegram-outbound")
TELEGRAM_INBOUND_SUBSCRIPTION = os.getenv("PUBSUB_INBOUND_SUBSCRIPTION", "telegram-inbound-backend-sub")


class PubSubTransport:
    """Transport dual-mode: Google Pub/Sub en prod, Redis PubSub en dev."""

    def __init__(self):
        self._publisher = None
        self._subscriber = None
        self._mode = TRANSPORT_MODE

        # Auto-fallback to redis if no GCP project
        if self._mode == "pubsub" and not GOOGLE_PROJECT_ID:
            logger.warning("[TRANSPORT] No GOOGLE_PROJECT_ID, falling back to redis")
            self._mode = "redis"

        logger.info("[TRANSPORT] Initialized mode=%s", self._mode)

    def publish(self, topic: str, message: Dict[str, Any]) -> None:
        """Publie un message JSON sur un topic."""
        if self._mode == "pubsub":
            self._publish_pubsub(topic, message)
        else:
            self._publish_redis(topic, message)

    def _publish_pubsub(self, topic: str, message: dict):
        from google.cloud import pubsub_v1

        if not self._publisher:
            self._publisher = pubsub_v1.PublisherClient()

        topic_path = self._publisher.topic_path(GOOGLE_PROJECT_ID, topic)
        data = json.dumps(message).encode("utf-8")

        # Attributs Pub/Sub pour filtrage cote subscription
        attrs = {}
        if "action" in message:
            attrs["action"] = message["action"]
        if "module" in message:
            attrs["module"] = message["module"]

        future = self._publisher.publish(topic_path, data, **attrs)
        future.result(timeout=10)
        logger.info("[TRANSPORT] pubsub published topic=%s action=%s", topic, message.get("action"))

    def _publish_redis(self, topic: str, message: dict):
        from app.redis_client import get_redis

        r = get_redis()
        # Mapping topic name -> Redis channel
        channel = f"outbound:{topic.replace('telegram-', '')}"
        r.publish(channel, json.dumps(message))
        logger.info("[TRANSPORT] redis published channel=%s action=%s", channel, message.get("action"))

    async def subscribe(self, subscription: str, callback: Callable) -> None:
        """Souscrit a un topic et appelle callback(message_dict) pour chaque message."""
        if self._mode == "pubsub":
            await self._subscribe_pubsub(subscription, callback)
        else:
            await self._subscribe_redis(subscription, callback)

    async def _subscribe_pubsub(self, subscription: str, callback: Callable):
        """Streaming pull Google Pub/Sub (tourne en boucle async)."""
        from google.cloud import pubsub_v1

        if not self._subscriber:
            self._subscriber = pubsub_v1.SubscriberClient()

        sub_path = self._subscriber.subscription_path(GOOGLE_PROJECT_ID, subscription)
        loop = asyncio.get_event_loop()

        def _sync_callback(message):
            try:
                data = json.loads(message.data.decode("utf-8"))
                loop.call_soon_threadsafe(asyncio.ensure_future, callback(data))
                message.ack()
            except Exception as e:
                logger.error("[TRANSPORT] pubsub callback error: %s", e)
                message.nack()

        streaming_pull = self._subscriber.subscribe(sub_path, callback=_sync_callback)
        logger.info("[TRANSPORT] pubsub subscribed to %s", subscription)

        # Block to keep the subscription alive
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            streaming_pull.cancel()
            try:
                streaming_pull.result(timeout=5)
            except Exception:
                pass

    async def _subscribe_redis(self, subscription: str, callback: Callable):
        """Polling Redis PubSub (equivalent dev)."""
        from app.redis_client import get_redis

        r = get_redis()
        ps = r.pubsub()
        # Mapping subscription -> Redis channel
        # "telegram-inbound-backend-sub" -> "inbound:telegram"
        base = subscription.replace("-backend-sub", "").replace("-inbound", "")
        channel = f"inbound:{base}"
        ps.subscribe(channel)
        logger.info("[TRANSPORT] redis subscribed to channel=%s", channel)

        while True:
            msg = ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg.get("data"):
                try:
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await callback(json.loads(data))
                except Exception as e:
                    logger.error("[TRANSPORT] redis callback error: %s", e)
            await asyncio.sleep(0.01)


# Singleton
_transport: Optional[PubSubTransport] = None


def get_pubsub_transport() -> PubSubTransport:
    """Retourne l'instance singleton du PubSubTransport."""
    global _transport
    if _transport is None:
        _transport = PubSubTransport()
    return _transport
