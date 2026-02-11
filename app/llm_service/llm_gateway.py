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

    async def enqueue_enter_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        chat_mode: str = "general_chat",
        initial_message: Optional[str] = None,
        tab_session_id: str = "legacy"
    ) -> dict[str, Any]:
        """
        Enqueue une initialisation de chat.

        Quand l'utilisateur selectionne un thread, ce gateway enqueue
        le job pour que le worker initialise le contexte LLM.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company
            thread_key: Cle du thread de chat
            chat_mode: Mode de chat ("general_chat", "onboarding_chat", etc.)
            initial_message: Message initial optionnel
            tab_session_id: ID de session onglet

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "enter_chat",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                "chat_mode": chat_mode,
                "initial_message": initial_message,
                "tab_session_id": tab_session_id,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {job_id[:8]}... "
                f"type=enter_chat user={user_id} thread={thread_key}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Chat initialization enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue enter_chat: {e}")
            raise

    async def enqueue_card_response(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        card_name: str,
        card_message_id: str,
        action: str,
        user_message: str = "",
        message_data: Optional[dict] = None,
        tab_session_id: str = "legacy"
    ) -> dict[str, Any]:
        """
        Enqueue une reponse a une carte interactive.

        Quand l'utilisateur clique sur approve/reject d'une carte,
        ce gateway enqueue le traitement au worker.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company
            thread_key: Cle du thread de chat
            card_name: Nom/ID de la carte
            card_message_id: ID du message contenant la carte
            action: Action de l'utilisateur ("approve", "reject", etc.)
            user_message: Message optionnel de l'utilisateur
            message_data: Donnees additionnelles
            tab_session_id: ID de session onglet

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "send_card_response",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                "card_name": card_name,
                "card_message_id": card_message_id,
                "action": action,
                "user_message": user_message,
                "message_data": message_data or {},
                "tab_session_id": tab_session_id,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {job_id[:8]}... "
                f"type=send_card_response user={user_id} card={card_name} action={action}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Card response enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue card_response: {e}")
            raise

    async def enqueue_onboarding_chat(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        chat_mode: str = "onboarding_chat"
    ) -> dict[str, Any]:
        """
        Enqueue le demarrage d'un chat d'onboarding.

        Declenche apres la creation d'une company quand l'utilisateur
        arrive sur la page de chat avec action=create.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company
            thread_key: Cle du thread (job_id de l'onboarding)
            chat_mode: Mode de chat (par defaut "onboarding_chat")

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "start_onboarding_chat",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                "chat_mode": chat_mode,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {job_id[:8]}... "
                f"type=start_onboarding_chat user={user_id} thread={thread_key}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Onboarding chat enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue onboarding_chat: {e}")
            raise

    async def enqueue_invalidate_context(
        self,
        user_id: str,
        collection_name: str,
    ) -> dict[str, Any]:
        """
        Enqueue une invalidation de contexte utilisateur.

        Force le rechargement du contexte depuis Firebase.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "invalidate_context",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {job_id[:8]}... "
                f"type=invalidate_context user={user_id} company={collection_name}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Context invalidation enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue invalidate_context: {e}")
            raise

    async def enqueue_stop_streaming(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
    ) -> dict[str, Any]:
        """
        Enqueue un arret de streaming LLM.

        Interrompt la generation en cours pour un thread.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company
            thread_key: Thread du streaming a interrompre

        Returns:
            Dict avec status et job_id
        """
        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "type": "stop_streaming",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {job_id[:8]}... "
                f"type=stop_streaming user={user_id} thread={thread_key}"
            )

            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Stop streaming enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue stop_streaming: {e}")
            raise

    async def enqueue_job_chat_message(
        self,
        user_id: str,
        collection_name: str,
        thread_key: str,
        job_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Enqueue un message de job chat (onboarding) pour traitement.

        Route vers _handle_onboarding_log_event dans le worker.

        Args:
            user_id: ID Firebase de l'utilisateur
            collection_name: ID de la company
            thread_key: Thread du chat
            job_id: ID du job onboarding
            message: Message a traiter

        Returns:
            Dict avec status et job_id
        """
        queue_job_id = str(uuid.uuid4())

        job = {
            "job_id": queue_job_id,
            "type": "handle_job_chat_message",
            "params": {
                "user_id": user_id,
                "collection_name": collection_name,
                "thread_key": thread_key,
                "job_id": job_id,
                "message": message,
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.lpush(self.QUEUE_NAME, json.dumps(job))

            logger.info(
                f"[LLM_GATEWAY] Job enqueued: {queue_job_id[:8]}... "
                f"type=handle_job_chat_message user={user_id} job={job_id}"
            )

            return {
                "status": "queued",
                "job_id": queue_job_id,
                "message": "Job chat message enqueued",
            }

        except Exception as e:
            logger.error(f"[LLM_GATEWAY] Failed to enqueue job_chat_message: {e}")
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
