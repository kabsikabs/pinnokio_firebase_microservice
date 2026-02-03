"""
LLM Gateway - Interface pour la delegation vers le worker agentique.

Au lieu de traiter les jobs LLM localement, ce gateway les enqueue
dans Redis pour traitement par le worker externe (pinnokio_agentic_worker).

Architecture:
- Frontend -> WebSocket -> API -> LLMGateway -> Redis Queue
- Worker <- Redis Queue <- Traitement LLM
- Worker -> Redis PubSub -> API -> WebSocket -> Frontend
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..redis_client import get_redis
from ..config import get_settings

logger = logging.getLogger("llm_service.gateway")


class LLMGateway:
    """
    Gateway qui enqueue les jobs LLM dans Redis.

    Remplace les appels directs a llm_manager.send_message() pour
    deleguer le traitement au worker externe.
    """

    # Nom de la queue Redis pour les jobs LLM
    QUEUE_NAME = "queue:llm_jobs"

    def __init__(self):
        self._redis = None

    @property
    def redis(self):
        """Lazy load du client Redis."""
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    async def enqueue_message(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        message: str,
        chat_mode: str = "general_chat",
        system_prompt: Optional[str] = None,
        selected_tool: Optional[str] = None,
        **kwargs
    ) -> dict[str, Any]:
        """
        Enqueue un message pour traitement par le worker.

        Au lieu de traiter le message localement via llm_manager.send_message(),
        cette methode enqueue un job dans Redis.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company
            thread_key: Cle du thread de chat
            message: Message de l'utilisateur
            chat_mode: Mode de chat ("general_chat", "task_execution", etc.)
            system_prompt: System prompt personnalise (optionnel)
            selected_tool: Outil pre-selectionne (optionnel)
            **kwargs: Parametres additionnels

        Returns:
            Dict avec:
                - status: "queued"
                - job_id: ID unique du job
                - message: Message de confirmation
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "send_message",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                "message": message,
                "chat_mode": chat_mode,
                "system_prompt": system_prompt,
                "selected_tool": selected_tool,
                **kwargs
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # Enqueue le job (LPUSH pour FIFO avec BRPOP)
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {job_id[:8]}... "
                f"type=send_message user={user_id} thread={thread_key}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Message enqueued for processing",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue job: {e}")
            raise

    async def enqueue_lpt_callback(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Enqueue un callback LPT (Long Processing Task).

        Quand une LPT externe (ex: klk_router) termine, elle envoie
        un callback. Ce gateway enqueue le callback pour que le worker
        puisse reprendre le workflow.

        Args:
            user_id: ID utilisateur
            collection_name: ID company
            thread_key: Thread du workflow
            payload: Payload du callback LPT

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "resume_workflow_after_lpt",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                **payload,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] LPT callback enqueued: {job_id[:8]}... "
                f"user={user_id} thread={thread_key}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "LPT callback enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue LPT callback: {e}")
            raise

    async def enqueue_scheduled_task(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        task_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Enqueue une tache planifiee pour execution.

        Args:
            user_id: ID utilisateur
            collection_name: ID company
            thread_key: Thread pour la tache
            task_data: Donnees de la tache a executer

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "execute_scheduled_task",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                "task_data": task_data,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Scheduled task enqueued: {job_id[:8]}... "
                f"user={user_id}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Scheduled task enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue scheduled task: {e}")
            raise

    def get_queue_length(self) -> int:
        """Retourne le nombre de jobs dans la queue."""
        return self.redis.llen(self.QUEUE_NAME)

    def get_queue_stats(self) -> dict[str, Any]:
        """Retourne des statistiques sur la queue."""
        return {
            "queue_name": self.QUEUE_NAME,
            "length": self.get_queue_length(),
        }


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_gateway: Optional[LLMGateway] = None


def get_llm_gateway() -> LLMGateway:
    """
    Retourne l'instance singleton du LLMGateway.

    Usage:
        gateway = get_llm_gateway()
        await gateway.enqueue_message(...)
    """
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
