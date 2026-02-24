"""
CommunicationResponseCollector — Collecte les reponses de tous les canaux
et les republie vers les workers via Redis PubSub.

Canaux supportes:
- pinnokio (frontend): Deja gere par orchestration._handle_external_card_response()
  + le fix Phase 2.1 (Redis publish apres RTDB write)
- google_chat: Ecoute Google PubSub subscription (feature-flagged)
- telegram: Ecoute Telegram callback queries (feature-flagged)

Toutes les reponses sont normalisees en format CARD_CLICKED_PINNOKIO
avant publication sur Redis.

Phase 2.2 du plan de centralisation communication workers.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Feature flags
COLLECT_GOOGLE_CHAT_ENABLED = os.getenv("COLLECT_GOOGLE_CHAT_ENABLED", "false").lower() == "true"
COLLECT_TELEGRAM_ENABLED = os.getenv("COLLECT_TELEGRAM_ENABLED", "false").lower() == "true"


class CommunicationResponseCollector:
    """
    Collecte les reponses de tous les canaux, convertit en format standard
    CARD_CLICKED_PINNOKIO, publie sur Redis PubSub pour les workers.
    """

    def __init__(self):
        self._running = False
        self._tasks = []

    async def start(self) -> None:
        """Demarre les listeners (Google PubSub, Telegram callbacks)."""
        if self._running:
            logger.warning("[RESPONSE_COLLECTOR] Already running, skipping start")
            return

        self._running = True
        logger.info("[RESPONSE_COLLECTOR] Starting response collector...")

        if COLLECT_GOOGLE_CHAT_ENABLED:
            task = asyncio.create_task(self._start_google_pubsub_listener())
            self._tasks.append(task)
            logger.info("[RESPONSE_COLLECTOR] Google PubSub listener started")

        if COLLECT_TELEGRAM_ENABLED:
            task = asyncio.create_task(self._start_telegram_callback_listener())
            self._tasks.append(task)
            logger.info("[RESPONSE_COLLECTOR] Telegram callback listener started")

        if not self._tasks:
            logger.info(
                "[RESPONSE_COLLECTOR] No external channel listeners enabled "
                "(pinnokio responses handled by orchestration.py)"
            )

    async def stop(self) -> None:
        """Arrete tous les listeners."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("[RESPONSE_COLLECTOR] Stopped")

    # ── Google Chat ──

    async def _start_google_pubsub_listener(self) -> None:
        """
        Ecoute Google PubSub (subscription dediee backend).

        Migrera: g_cred.listen_to_subscription() L6804-6894
        La subscription backend coexiste avec celle des workers pendant la transition.

        TODO: Implementer quand Google Chat sera un canal actif.
        """
        logger.info("[RESPONSE_COLLECTOR] Google PubSub listener stub — not implemented yet")

    def _google_chat_to_standard(self, json_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Google Chat CARD_CLICKED -> CARD_CLICKED_PINNOKIO standard.

        Migrera: g_cred.callback_card_clicked() L7189-7308
        """
        try:
            from app.realtime.card_transformer import (
                CardTransformer,
                DEFAULT_INVOKED_FUNCTION,
                FORM_FIELDS_BY_CARD,
            )

            card_id = json_data.get("common", {}).get("parameters", {}).get("cardId", "")
            invoked_function = json_data.get("common", {}).get("invokedFunction", "")
            form_inputs = json_data.get("common", {}).get("formInputs", {})
            thread_key = (
                json_data.get("message", {}).get("thread", {}).get("name", "")
                or json_data.get("message", {}).get("threadKey", "")
            )

            return {
                "type": "CARD_CLICKED",
                "message_type": "CARD_CLICKED_PINNOKIO",
                "threadKey": thread_key,
                "message": json_data.get("message", {}),
                "common": {
                    "invokedFunction": invoked_function,
                    "formInputs": form_inputs,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sender_id": json_data.get("user", {}).get("name", ""),
                "read": False,
            }
        except Exception as e:
            logger.error("[RESPONSE_COLLECTOR] google_chat_to_standard failed: %s", e)
            return None

    # ── Telegram ──

    # Module -> chat_mode Worker LLM
    _MODULE_TO_CHAT_MODE = {
        "router": "router_chat",
        "apbookeeper": "ap_chat",
        "banker": "banker_chat",
        "general": "general_chat",
        "onboarding": "onboarding_chat",
    }

    async def _start_telegram_callback_listener(self) -> None:
        """Ecoute les messages Telegram entrants via PubSubTransport."""
        from app.realtime.pubsub_transport import get_pubsub_transport, TELEGRAM_INBOUND_SUBSCRIPTION

        transport = get_pubsub_transport()
        await transport.subscribe(TELEGRAM_INBOUND_SUBSCRIPTION, self._handle_telegram_inbound)

    async def _handle_telegram_inbound(self, data: Dict[str, Any]) -> None:
        """
        Route un message Telegram entrant vers le Worker LLM ou le worker externe.

        Format attendu (spec microservice telegram):
        {
            "mandate_path": "companies/xxx",
            "module": "router",
            "response": {
                "type": "message" | "callback",
                "chat_id": -123456,
                "text": "...",           # si type=message
                "data": "card:action",   # si type=callback
                "username": "john"
            }
        }
        """
        mandate_path = data.get("mandate_path")
        module = data.get("module", "router")
        response = data.get("response", {})
        msg_type = response.get("type")  # "message" ou "callback"
        chat_id = response.get("chat_id")
        username = response.get("username")

        if not mandate_path or not chat_id:
            logger.warning("[RESPONSE_COLLECTOR] Invalid telegram inbound: missing mandate_path or chat_id")
            return

        # Resoudre uid + company_id depuis Firestore telegram_users/{username}
        uid, company_id = await self._resolve_telegram_user(username, mandate_path)
        if not uid:
            logger.warning(
                "[RESPONSE_COLLECTOR] Cannot resolve uid for username=%s mandate=%s",
                username, mandate_path,
            )
            return

        # Thread key deterministe pour ce canal Telegram + module
        thread_key = f"tg_{abs(chat_id)}_{module}"

        if msg_type == "message":
            # Message texte -> enqueue vers Worker LLM
            text = response.get("text", "")
            from app.llm_service.llm_gateway import get_llm_gateway

            gateway = get_llm_gateway()
            await gateway.enqueue_message(
                user_id=uid,
                collection_name=company_id,
                thread_key=thread_key,
                message=text,
                chat_mode=self._MODULE_TO_CHAT_MODE.get(module, "general_chat"),
                communication_chat_type="telegram",
                external_thread_id=str(chat_id),
            )
            logger.info(
                "[RESPONSE_COLLECTOR] Telegram message enqueued uid=%s thread=%s module=%s",
                uid, thread_key, module,
            )

        elif msg_type == "callback":
            # Callback (clic bouton) -> convertir en CARD_CLICKED standard
            callback_data = response.get("data", "")
            # Parse callback_data: "card_id:action_name:value" ou "card_id:action_name"
            parts = callback_data.split(":", 2)
            card_id = parts[0] if parts else "unknown"

            standard = self._telegram_to_standard(callback_data, card_id, thread_key)
            if standard:
                # Enrichir avec source_channel
                standard["communication_chat_type"] = "telegram"
                standard["external_thread_id"] = str(chat_id)

                # Router: card Worker LLM ou card Worker Externe?
                from app.realtime.card_transformer import EXTERNAL_WORKER_CARD_IDS

                if card_id in EXTERNAL_WORKER_CARD_IDS:
                    # Card d'un worker externe -> RTDB write
                    space_code = company_id
                    await self._publish_response_to_worker(uid, space_code, thread_key, standard)
                else:
                    # Card du Worker LLM -> enqueue card response
                    from app.llm_service.llm_gateway import get_llm_gateway

                    gateway = get_llm_gateway()
                    await gateway.enqueue_card_response(
                        user_id=uid,
                        collection_name=company_id,
                        thread_key=thread_key,
                        card_name=card_id,
                        card_message_id="",
                        action=callback_data,
                        communication_chat_type="telegram",
                        external_thread_id=str(chat_id),
                    )
                logger.info(
                    "[RESPONSE_COLLECTOR] Telegram callback routed uid=%s card=%s",
                    uid, card_id,
                )

    async def _resolve_telegram_user(self, username: str, mandate_path: str) -> tuple:
        """
        Resout uid + contact_space_id depuis telegram_users/{username} + mandate Firestore.

        Le contact_space_id est le vrai identifiant company utilise partout dans le systeme
        (session cache Level 2, resolve_client_by_contact_space, etc.).
        On le lit directement depuis le document mandate Firestore.
        """
        if not username:
            return None, None
        try:
            from app.firebase_providers import get_firebase_management

            fm = get_firebase_management()

            # 1. Resoudre uid depuis telegram_users/{username}
            doc_ref = fm.db.collection("telegram_users").document(username)
            doc = doc_ref.get()
            if not doc.exists:
                return None, None

            user_data = doc.to_dict()
            authorized = user_data.get("authorized_mandates", {})
            mandate_data = authorized.get(mandate_path, {})
            uid = mandate_data.get("firebase_user_id")
            if not uid:
                return None, None

            # 2. Resoudre contact_space_id depuis le document mandate Firestore
            #    mandate_path ex: "clients/{uid}/bo_clients/{client_doc}/mandates/{mandate_id}"
            #    Le champ contact_space_id est la cle utilisee par le cache Level 2
            contact_space_id = None
            try:
                mandate_doc = fm.db.document(mandate_path).get()
                if mandate_doc.exists:
                    contact_space_id = mandate_doc.to_dict().get("contact_space_id")
            except Exception as e:
                logger.warning("[RESPONSE_COLLECTOR] mandate doc read failed: %s", e)

            if not contact_space_id:
                logger.warning(
                    "[RESPONSE_COLLECTOR] contact_space_id not found in mandate doc, "
                    "falling back to mandate_doc_id for username=%s",
                    username,
                )
                # Fallback: mandate_doc_id ou dernier segment du path
                contact_space_id = mandate_data.get("mandate_doc_id")
                if not contact_space_id:
                    parts = mandate_path.strip("/").split("/")
                    contact_space_id = parts[-1] if parts else None

            return uid, contact_space_id
        except Exception as e:
            logger.error("[RESPONSE_COLLECTOR] resolve_telegram_user error: %s", e)
            return None, None

    def _telegram_to_standard(
        self,
        callback_data: str,
        card_id: str,
        thread_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Telegram callback -> CARD_CLICKED_PINNOKIO standard.

        Migrera: telegram.py.convert_telegram_message_to_pinnokio_format() L1148-1400
        """
        try:
            from app.realtime.card_transformer import DEFAULT_INVOKED_FUNCTION

            invoked_function = DEFAULT_INVOKED_FUNCTION.get(card_id, "answer_pinnokio")

            return {
                "type": "CARD_CLICKED",
                "message_type": "CARD_CLICKED_PINNOKIO",
                "threadKey": thread_key,
                "common": {
                    "invokedFunction": invoked_function,
                    "formInputs": {
                        "pinnokio_func": {
                            "stringInputs": {"value": [callback_data]}
                        }
                    },
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sender_id": "telegram_user",
                "read": False,
            }
        except Exception as e:
            logger.error("[RESPONSE_COLLECTOR] telegram_to_standard failed: %s", e)
            return None

    # ── Commun ──

    async def _publish_response_to_worker(
        self,
        uid: str,
        space_code: str,
        thread_key: str,
        response_data: Dict[str, Any],
        message_mode: str = "job_chats",
    ) -> None:
        """
        Publie CARD_CLICKED_PINNOKIO sur Redis channel du worker.

        Channel: user:{uid}/{space_code}/{message_mode}/{thread_key}/messages
        """
        try:
            from app.redis_client import get_redis
            redis = get_redis()
            channel = f"user:{uid}/{space_code}/{message_mode}/{thread_key}/messages"
            payload = {
                "type": "job_chat_message",
                "job_id": thread_key,
                "collection_name": space_code,
                "thread_key": thread_key,
                "message": response_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            redis.publish(channel, json.dumps(payload, default=str))
            logger.info(
                "[RESPONSE_COLLECTOR] Published response to worker channel=%s thread=%s",
                channel, thread_key,
            )
        except Exception as e:
            logger.error("[RESPONSE_COLLECTOR] Redis publish failed: %s", e)


# Singleton
_collector: Optional[CommunicationResponseCollector] = None


def get_response_collector() -> CommunicationResponseCollector:
    """Retourne l'instance singleton du CommunicationResponseCollector."""
    global _collector
    if _collector is None:
        _collector = CommunicationResponseCollector()
    return _collector
