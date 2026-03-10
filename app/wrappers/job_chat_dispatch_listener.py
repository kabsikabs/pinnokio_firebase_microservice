"""
Job Chat Dispatch Listener - BRPOP consumer for job_chat_dispatch queue.

Workers (klk_router, klk_bank, klk_accountant) push job chat messages
(CARD, FOLLOW_CARD, CMMD, MESSAGE) to Redis queue 'queue:job_chat_dispatch'
via LPUSH. This listener picks them up via BRPOP and routes to the existing
_handle_job_chat_message() handler in redis_subscriber.

Why queue instead of Pub/Sub:
  - Exactly-once delivery: only 1 backend instance processes each message
    (no duplicate cards when multiple instances are running)
  - No message loss: messages persist in queue until consumed
    (Pub/Sub drops messages if 0 subscribers are listening)

Architecture:
    worker firebase_realtime.py --LPUSH--> queue:job_chat_dispatch
                                                    |
                                              BRPOP (this listener)
                                                    |
                                                    v
                                    redis_subscriber._handle_job_chat_message()
                                          (WS broadcast + Telegram dispatch + LLM)
"""

import asyncio
import json
import logging
from typing import Optional

from ..redis_client import get_redis

logger = logging.getLogger("job_chat_dispatch_listener")

JOB_CHAT_DISPATCH_QUEUE = "queue:job_chat_dispatch"

_listener_task: Optional[asyncio.Task] = None
_running = False


async def _listen_loop():
    """BRPOP loop on the job chat dispatch queue."""
    global _running
    redis = get_redis()

    logger.info(f"[JOB_CHAT_LISTENER] Listening on {JOB_CHAT_DISPATCH_QUEUE}")

    while _running:
        try:
            result = await asyncio.to_thread(
                redis.brpop, JOB_CHAT_DISPATCH_QUEUE, timeout=5
            )

            if result is None:
                continue

            _, raw = result
            data = json.loads(raw if isinstance(raw, str) else raw.decode())

            uid = data.get("uid", "")
            job_id = data.get("job_id", "")
            msg = data.get("message", {})
            message_type = msg.get("message_type", "UNKNOWN") if isinstance(msg, dict) else "UNKNOWN"

            logger.info(
                "[JOB_CHAT_LISTENER] Received: uid=%s job_id=%s message_type=%s",
                uid, job_id[:12], message_type
            )

            if not uid:
                logger.warning("[JOB_CHAT_LISTENER] Missing uid, skipping")
                continue

            # Route to the existing handler in redis_subscriber
            from ..realtime.redis_subscriber import get_redis_subscriber
            subscriber = get_redis_subscriber()
            channel = f"user:{uid}/job_chats"  # synthetic channel for handler compatibility
            await subscriber._handle_job_chat_message(uid, channel, data)

            logger.info(
                "[JOB_CHAT_LISTENER] Processed: uid=%s job_id=%s message_type=%s",
                uid, job_id[:12], message_type
            )

        except json.JSONDecodeError as e:
            logger.error(f"[JOB_CHAT_LISTENER] Invalid JSON in queue: {e}")
        except Exception as e:
            logger.error(f"[JOB_CHAT_LISTENER] Error: {e}", exc_info=True)
            await asyncio.sleep(2)

    logger.info("[JOB_CHAT_LISTENER] Listen loop ended")


async def start_job_chat_dispatch_listener():
    """Start the job chat dispatch listener (call at app startup)."""
    global _listener_task, _running

    if _running:
        logger.warning("[JOB_CHAT_LISTENER] Already running")
        return

    _running = True
    _listener_task = asyncio.create_task(_listen_loop())
    logger.info("[JOB_CHAT_LISTENER] Started")


async def stop_job_chat_dispatch_listener():
    """Stop the job chat dispatch listener (call at app shutdown)."""
    global _listener_task, _running

    _running = False

    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None

    logger.info("[JOB_CHAT_LISTENER] Stopped")
