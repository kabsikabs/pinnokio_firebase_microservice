"""
Job Dispatch Listener - BRPOP consumer for agentic dispatch queue.

The pinnokio_agentic_worker (lpt_client.py) pushes job dispatch requests
to Redis queue 'queue:agentic_dispatch' via LPUSH. This listener picks
them up via BRPOP and routes to handle_job_process() for centralized
processing (HTTP dispatch, notifications, cache, WSS).

Architecture:
    lpt_client.py --LPUSH--> queue:agentic_dispatch --BRPOP--> this listener
                                                                    |
                                                                    v
                                                          handle_job_process()
                                                          (HTTP + notifs + cache + WSS)
"""

import asyncio
import json
import logging
from typing import Optional

from ..redis_client import get_redis

logger = logging.getLogger("job_dispatch_listener")

AGENTIC_DISPATCH_QUEUE = "queue:agentic_dispatch"

_listener_task: Optional[asyncio.Task] = None
_running = False


async def _listen_loop():
    """BRPOP loop on the agentic dispatch queue."""
    global _running
    redis = get_redis()

    logger.info(f"[DISPATCH_LISTENER] Listening on {AGENTIC_DISPATCH_QUEUE}")

    while _running:
        try:
            # BRPOP with timeout (blocking pop from right side of list)
            result = await asyncio.to_thread(
                redis.brpop, AGENTIC_DISPATCH_QUEUE, timeout=5
            )

            if result is None:
                continue

            _, raw = result
            data = json.loads(raw if isinstance(raw, str) else raw.decode())

            uid = data.get("uid", "")
            job_type = data.get("job_type", "")
            source = data.get("source", "agentic")

            logger.info(
                f"[DISPATCH_LISTENER] Job received: uid={uid} "
                f"job_type={job_type} source={source}"
            )

            # Import here to avoid circular imports at module load time
            from .job_actions_handler import (
                handle_job_process,
                handle_reverse_reconciliation_dispatch,
            )

            # Route reverse_reconciliation to dedicated handler (no notifs, no list changes)
            if "reverse_reconciliation" in source:
                result = await handle_reverse_reconciliation_dispatch(
                    uid=uid,
                    payload=data.get("payload", {}),
                    company_data=data.get("company_data", {}),
                    source=source,
                )
            else:
                result = await handle_job_process(
                    uid=uid,
                    job_type=job_type,
                    payload=data.get("payload", {}),
                    company_data=data.get("company_data", {}),
                    source=source,
                    traceability=data.get("traceability"),
                )

            if result.get("success"):
                logger.info(
                    f"[DISPATCH_LISTENER] Job dispatched successfully: "
                    f"batch_id={result.get('batch_id')} "
                    f"dispatch_method={result.get('dispatch_method')}"
                )
            else:
                logger.error(
                    f"[DISPATCH_LISTENER] Job dispatch failed: "
                    f"{result.get('error', 'unknown error')}"
                )

        except json.JSONDecodeError as e:
            logger.error(f"[DISPATCH_LISTENER] Invalid JSON in queue: {e}")
        except Exception as e:
            logger.error(f"[DISPATCH_LISTENER] Error: {e}", exc_info=True)
            await asyncio.sleep(2)

    logger.info("[DISPATCH_LISTENER] Listen loop ended")


async def start_agentic_dispatch_listener():
    """Start the agentic dispatch listener (call at app startup)."""
    global _listener_task, _running

    if _running:
        logger.warning("[DISPATCH_LISTENER] Already running")
        return

    _running = True
    _listener_task = asyncio.create_task(_listen_loop())
    logger.info("[DISPATCH_LISTENER] Started")


async def stop_agentic_dispatch_listener():
    """Stop the agentic dispatch listener (call at app shutdown)."""
    global _listener_task, _running

    _running = False

    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None

    logger.info("[DISPATCH_LISTENER] Stopped")
