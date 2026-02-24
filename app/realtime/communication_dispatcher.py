"""
CommunicationDispatcher — Dispatch multi-canal des messages workers.

Recoit les messages au format standard (cardsV2) via Redis,
resout le communication_mode du mandat, et dispatch vers le bon canal:
- pinnokio: NOP (redis_subscriber gere deja le broadcast WSS)
- google_chat: envoi via Google Chat API (feature-flagged)
- telegram: envoi via Telegram API (feature-flagged)

Phase 1 du plan de centralisation communication workers.
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Feature flags — desactives par defaut, activer quand la migration est prete
DISPATCH_GOOGLE_CHAT_ENABLED = os.getenv("DISPATCH_GOOGLE_CHAT_ENABLED", "false").lower() == "true"
DISPATCH_TELEGRAM_ENABLED = os.getenv("DISPATCH_TELEGRAM_ENABLED", "false").lower() == "true"

# Mapping department -> champ communication_method dans workflow_params
_DEPARTMENT_COMM_FIELD = {
    "Router": "router_communication_method",
    "APbookeeper": "apbookeeper_communication_method",
    "Bankbookeeper": "banker_communication_method",
    # Aliases
    "router": "router_communication_method",
    "apbookeeper": "apbookeeper_communication_method",
    "banker": "banker_communication_method",
    "bankbookeeper": "banker_communication_method",
}


class CommunicationDispatcher:
    """Dispatch multi-canal des messages workers vers les bons canaux."""

    def __init__(self):
        self._config_cache: Dict[str, Tuple[dict, float]] = {}
        self._cache_ttl = 300  # 5 minutes

    async def get_communication_config(
        self,
        uid: str,
        mandate_path: str,
        department: str = "Router",
    ) -> Dict[str, Any]:
        """
        Resout le communication_mode depuis la config du mandat Firebase.

        Returns:
            {
                "communication_mode": "pinnokio" | "telegram" | "google_chat",
                "telegram_mapping": {...} | None,
                "google_space_id": str | None,
            }
        """
        cache_key = f"{mandate_path}:{department}"
        now = datetime.now(timezone.utc).timestamp()

        # Cache check
        if cache_key in self._config_cache:
            cached, cached_at = self._config_cache[cache_key]
            if now - cached_at < self._cache_ttl:
                return cached

        try:
            from app.firebase_providers import get_firebase_management
            fm = get_firebase_management()

            # Charger workflow_params du mandat
            mandate_doc = fm.db.document(f"{mandate_path}/setup/workflow_params").get()
            if not mandate_doc.exists:
                config = {"communication_mode": "pinnokio"}
                self._config_cache[cache_key] = (config, now)
                return config

            params = mandate_doc.to_dict()

            # Resoudre le champ communication_method pour ce department
            comm_field = _DEPARTMENT_COMM_FIELD.get(department, "router_communication_method")
            communication_mode = params.get(comm_field, "telegram")

            # Charger mapping telegram si necessaire
            telegram_mapping = None
            if communication_mode == "telegram":
                tg_doc = fm.db.document(f"{mandate_path}/setup/telegram_config").get()
                if tg_doc.exists:
                    telegram_mapping = tg_doc.to_dict()

            config = {
                "communication_mode": communication_mode,
                "telegram_mapping": telegram_mapping,
                "google_space_id": params.get("google_space_id"),
            }

            self._config_cache[cache_key] = (config, now)
            return config

        except Exception as e:
            logger.error("[COMM_DISPATCHER] config resolution failed: %s", e)
            return {"communication_mode": "pinnokio"}

    async def dispatch_message(
        self,
        uid: str,
        mandate_path: str,
        space_code: str,
        thread_key: str,
        message_data: Dict[str, Any],
        department: str = "Router",
        message_mode: str = "job_chats",
    ) -> None:
        """
        Point d'entree principal — dispatch vers le bon canal.

        Pour pinnokio: NOP (le broadcast WSS est deja gere par redis_subscriber).
        Pour google_chat/telegram: dispatch si feature flag actif.
        """
        config = await self.get_communication_config(uid, mandate_path, department)
        comm_mode = config.get("communication_mode", "pinnokio")

        if comm_mode == "pinnokio":
            # NOP — redis_subscriber._handle_job_chat_message() gere deja le WSS
            return

        if comm_mode == "google_chat":
            if not DISPATCH_GOOGLE_CHAT_ENABLED:
                logger.debug(
                    "[COMM_DISPATCHER] google_chat dispatch disabled (feature flag off) thread=%s",
                    thread_key,
                )
                return
            google_space_id = config.get("google_space_id")
            if google_space_id:
                await self._send_google_chat(google_space_id, thread_key, message_data)

        elif comm_mode == "telegram":
            if not DISPATCH_TELEGRAM_ENABLED:
                logger.debug(
                    "[COMM_DISPATCHER] telegram dispatch disabled (feature flag off) thread=%s",
                    thread_key,
                )
                return
            telegram_mapping = config.get("telegram_mapping")
            if telegram_mapping:
                text = self._extract_text(message_data)
                cards_v2 = self._extract_cardsv2(message_data)
                await self._send_telegram(
                    telegram_mapping, thread_key, text, cards_v2, mandate_path
                )

    async def _send_google_chat(
        self,
        google_space_id: str,
        thread_key: str,
        message_data: Dict[str, Any],
    ) -> None:
        """
        Envoie via Google Chat API. cardsV2 est deja le format natif Google Chat.
        TODO: Implementer quand Google Chat sera un canal actif.
        """
        logger.info(
            "[COMM_DISPATCHER] google_chat send — space=%s thread=%s (stub)",
            google_space_id, thread_key,
        )

    async def _send_telegram(
        self,
        telegram_mapping: Dict[str, Any],
        thread_key: str,
        text: str,
        cards_v2: list,
        mandate_path: str,
    ) -> None:
        """
        Convertit cardsV2 en inline keyboard Telegram et envoie via API.
        TODO: Migrer la logique depuis klk_router/tools/telegram.py
        """
        logger.info(
            "[COMM_DISPATCHER] telegram send — thread=%s text_len=%d cards=%d (stub)",
            thread_key, len(text), len(cards_v2),
        )

    # ═══════════════════════════════════════════════════════════════
    # OUTBOUND — dispatch messages sortants Worker LLM → canal externe
    # ═══════════════════════════════════════════════════════════════

    async def dispatch_outbound(
        self,
        channel: str,
        uid: str,
        external_thread_id: Optional[str],
        message_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """
        Dispatch un message sortant du Worker LLM vers un canal externe.

        Appele par worker_broadcast_listener quand communication_chat_type != 'pinnokio'.
        """
        logger.info(
            "[COMM_DISPATCHER] dispatch_outbound channel=%s uid=%s type=%s",
            channel, uid, message_type,
        )

        if channel == "telegram":
            if not DISPATCH_TELEGRAM_ENABLED:
                logger.debug("[COMM_DISPATCHER] telegram outbound disabled (flag off)")
                return
            await self._send_telegram_outbound(external_thread_id, message_type, payload)

        elif channel == "google_chat":
            if not DISPATCH_GOOGLE_CHAT_ENABLED:
                logger.debug("[COMM_DISPATCHER] google_chat outbound disabled (flag off)")
                return
            await self._send_google_chat_outbound(external_thread_id, message_type, payload)

    async def _send_telegram_outbound(
        self,
        chat_id: Optional[str],
        message_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Envoie vers Telegram via PubSubTransport (format spec microservice)."""
        inner = payload.get("payload", {})

        if message_type in ("CARD", "FOLLOW_CARD"):
            card_data = inner.get("card_data", {})
            telegram_msg = self._cardsv2_to_telegram_keyboard(card_data)
            await self._publish_outbound("telegram", {
                "action": "send_message_with_buttons",
                "chat_id": chat_id,
                "text": telegram_msg.get("text", ""),
                "buttons": telegram_msg.get("buttons", []),
            })

        elif message_type in ("llm_stream_complete", "llm.stream_end", "CMMD"):
            text = inner.get("full_content", "") or inner.get("content", "")
            await self._publish_outbound("telegram", {
                "action": "send_message",
                "chat_id": chat_id,
                "text": text,
            })

        elif message_type == "llm_stream_error":
            error = inner.get("error", "Unknown error")
            await self._publish_outbound("telegram", {
                "action": "send_message",
                "chat_id": chat_id,
                "text": f"Erreur: {error}",
            })

        elif message_type == "notification":
            text = inner.get("message", "") or inner.get("content", "")
            await self._publish_outbound("telegram", {
                "action": "send_message",
                "chat_id": chat_id,
                "text": text,
            })

    async def _send_google_chat_outbound(
        self,
        space_id: Optional[str],
        message_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Stub Google Chat outbound — a implementer quand le canal sera actif."""
        logger.info(
            "[COMM_DISPATCHER] google_chat outbound (stub) type=%s space=%s",
            message_type, space_id,
        )

    async def _publish_outbound(self, channel: str, message: Dict[str, Any]) -> None:
        """Publie vers le microservice du canal via PubSubTransport."""
        from app.realtime.pubsub_transport import get_pubsub_transport, TELEGRAM_OUTBOUND_TOPIC

        transport = get_pubsub_transport()
        topic = TELEGRAM_OUTBOUND_TOPIC if channel == "telegram" else f"{channel}-outbound"
        transport.publish(topic, message)
        logger.info(
            "[COMM_DISPATCHER] outbound published topic=%s action=%s chat_id=%s",
            topic, message.get("action"), message.get("chat_id"),
        )

    def _cardsv2_to_telegram_keyboard(self, card_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convertit cardsV2 → Telegram inline keyboard.

        Utilise CardTransformer.extract_widgets_from_card_body() pour extraire
        les widgets normalises, puis les convertit en boutons Telegram.
        """
        from app.realtime.card_transformer import CardTransformer

        buttons = []
        text_parts = []
        card_id = card_data.get("cardId", "unknown")

        widgets = CardTransformer.extract_widgets_from_card_body(card_data)
        for parsed in widgets:
            wtype = parsed.get("type")

            if wtype in ("dropdown", "multi_select"):
                for item in parsed.get("items", []):
                    cb = f"{card_id}:{parsed.get('name', '')}:{item['value']}"
                    buttons.append({"text": item["text"], "callback_data": cb[:64]})

            elif wtype == "button_list":
                for btn in parsed.get("buttons", []):
                    if btn.get("disabled"):
                        continue
                    cb = f"{card_id}:{btn.get('action', '')}"
                    buttons.append({"text": btn["text"], "callback_data": cb[:64]})

            elif wtype == "text_paragraph":
                text_parts.append(parsed.get("text", ""))

            elif wtype == "text_input":
                text_parts.append(f"[Input: {parsed.get('label', '')}]")

        return {
            "text": "\n".join(text_parts) or card_data.get("header", {}).get("title", ""),
            "buttons": [buttons[i:i + 2] for i in range(0, len(buttons), 2)] if buttons else [],
        }

    def _extract_text(self, message_data: Dict[str, Any]) -> str:
        """Extrait le texte d'un message standard."""
        content = message_data.get("content", "")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return parsed.get("message", {}).get("argumentText", "")
            except (json.JSONDecodeError, TypeError):
                return content
        elif isinstance(content, dict):
            return content.get("message", {}).get("argumentText", "")
        return str(content)

    def _extract_cardsv2(self, message_data: Dict[str, Any]) -> list:
        """Extrait cardsV2 d'un message standard."""
        content = message_data.get("content", "")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return parsed.get("cardsV2", [])
            except (json.JSONDecodeError, TypeError):
                return []
        elif isinstance(content, dict):
            return content.get("cardsV2", [])
        return []


# Singleton
_dispatcher: Optional[CommunicationDispatcher] = None


def get_communication_dispatcher() -> CommunicationDispatcher:
    """Retourne l'instance singleton du CommunicationDispatcher."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = CommunicationDispatcher()
    return _dispatcher
