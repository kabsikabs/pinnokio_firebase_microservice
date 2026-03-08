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
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Feature flags — desactives par defaut, activer quand la migration est prete
DISPATCH_GOOGLE_CHAT_ENABLED = os.getenv("DISPATCH_GOOGLE_CHAT_ENABLED", "false").lower() == "true"
DISPATCH_TELEGRAM_ENABLED = os.getenv("DISPATCH_TELEGRAM_ENABLED", "false").lower() == "true"

# Wizard multi-step pour cartes avec dropdowns sur Telegram
WIZARD_PREFIX = "card_wizard"
WIZARD_TTL = 1800  # 30 minutes


class CommunicationDispatcher:
    """Dispatch multi-canal des messages workers vers les bons canaux."""

    def __init__(self):
        pass

    def get_telegram_mapping(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Resout telegram_users_mapping depuis le cache L2 ou fallback Firestore.

        Le mapping est dans le document mandate:
            telegram_users_mapping = {
                "router_room": "-4910212603",
                "accountbookeeper_room": "-4987387695",
                "banker_room": "-4837782794",
                "general_accountant_room": "",
            }

        Strategie: cache L2 (company:{uid}:{cid}:context) → fallback Firestore.
        """
        # 1. Cache L2 — get_company_context (synchrone, Redis direct)
        try:
            from app.wrappers.dashboard_orchestration_handlers import get_company_context
            context = get_company_context(uid, company_id)
            if context:
                mapping = context.get("telegram_users_mapping")
                if mapping:
                    logger.debug(
                        "[COMM_DISPATCHER] telegram_mapping from L2 cache uid=%s cid=%s",
                        uid, company_id,
                    )
                    return mapping
        except Exception as e:
            logger.debug("[COMM_DISPATCHER] L2 cache miss: %s", e)

        # 2. Fallback Firestore — document mandate direct
        try:
            from app.firebase_providers import get_firebase_management
            fm = get_firebase_management()

            mandate_doc = fm.db.document(mandate_path).get()
            if not mandate_doc.exists:
                logger.warning("[COMM_DISPATCHER] mandate doc not found: %s", mandate_path)
                return None

            data = mandate_doc.to_dict()
            telegram_mapping = data.get("telegram_users_mapping")
            logger.info(
                "[COMM_DISPATCHER] telegram_mapping from Firestore mandate=%s found=%s",
                mandate_path, telegram_mapping is not None,
            )
            return telegram_mapping

        except Exception as e:
            logger.error("[COMM_DISPATCHER] telegram_mapping resolution failed: %s", e)
            return None

    async def dispatch_message(
        self,
        uid: str,
        mandate_path: str,
        space_code: str,
        thread_key: str,
        message_data: Dict[str, Any],
        department: str = "Router",
        message_mode: str = "job_chats",
        communication_chat_type: str = "pinnokio",
    ) -> None:
        """
        Point d'entree principal — dispatch vers le bon canal.

        communication_chat_type provient du payload du worker externe.
        Pour pinnokio: NOP (le broadcast WSS est deja gere par redis_subscriber).
        Pour google_chat/telegram: dispatch si feature flag actif.
        """
        comm_mode = communication_chat_type

        if comm_mode == "pinnokio":
            # NOP — redis_subscriber._handle_job_chat_message() gere deja le WSS
            return

        logger.info(
            "[COMM_DISPATCHER] dispatch_message comm_mode=%s uid=%s thread=%s dept=%s",
            comm_mode, uid, thread_key, department,
        )

        if comm_mode == "google_chat":
            if not DISPATCH_GOOGLE_CHAT_ENABLED:
                logger.debug(
                    "[COMM_DISPATCHER] google_chat dispatch disabled (feature flag off) thread=%s",
                    thread_key,
                )
                return
            # TODO: resolve google_space_id from mandate
            # await self._send_google_chat(google_space_id, thread_key, message_data)

        elif comm_mode == "telegram":
            if not DISPATCH_TELEGRAM_ENABLED:
                logger.debug(
                    "[COMM_DISPATCHER] telegram dispatch disabled (feature flag off) thread=%s",
                    thread_key,
                )
                return
            telegram_mapping = self.get_telegram_mapping(uid, space_code, mandate_path)
            if telegram_mapping:
                text = self._extract_text(message_data)
                cards_v2 = self._extract_cardsv2(message_data)
                await self._send_telegram(
                    telegram_mapping, thread_key, text, cards_v2, mandate_path,
                    department=department,
                )
            else:
                logger.warning(
                    "[COMM_DISPATCHER] no telegram_users_mapping in mandate=%s",
                    mandate_path,
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

    # Mapping thread_key prefix ou department → room field dans telegram_mapping
    _DEPARTMENT_TO_ROOM = {
        "Router": "router_room",
        "router": "router_room",
        "APbookeeper": "accountbookeeper_room",
        "apbookeeper": "accountbookeeper_room",
        "Bankbookeeper": "banker_room",
        "banker": "banker_room",
        "bankbookeeper": "banker_room",
        "general": "general_accountant_room",
    }

    async def _send_telegram(
        self,
        telegram_mapping: Dict[str, Any],
        thread_key: str,
        text: str,
        cards_v2: list,
        mandate_path: str,
        department: str = "Router",
    ) -> None:
        """
        Convertit cardsV2 en inline keyboard Telegram et envoie via PubSubTransport.

        Resout le chat_id depuis telegram_mapping + department, puis dispatch
        vers le topic telegram-outbound.

        Si la carte contient des dropdowns, active le mode wizard multi-step
        (un message par choix, puis text input, puis agregation finale).
        """
        # Resoudre le chat_id depuis telegram_mapping + department
        room_field = self._DEPARTMENT_TO_ROOM.get(department, "router_room")
        chat_id = telegram_mapping.get(room_field)

        if not chat_id:
            logger.warning(
                "[COMM_DISPATCHER] telegram send — no chat_id for room=%s in mapping, thread=%s",
                room_field, thread_key,
            )
            return

        # Convertir chat_id string → int
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            logger.warning("[COMM_DISPATCHER] telegram send — invalid chat_id=%s", chat_id)
            return

        if cards_v2:
            from app.realtime.card_transformer import CardTransformer

            first_card = cards_v2[0] if cards_v2 else {}
            card_body = first_card.get("card", {})
            card_id = first_card.get("cardId", "unknown")

            card_data = {
                "cardId": card_id,
                "header": card_body.get("header", {}),
                "sections": card_body.get("sections", []),
            }

            # Detecter si la carte a des dropdowns → wizard multi-step
            widgets = CardTransformer.extract_widgets_from_card_body(card_data)
            has_dropdowns = any(
                w["type"] in ("dropdown", "multi_select") for w in widgets
            )

            if has_dropdowns:
                # Wizard mode: envoyer etape par etape
                await self._send_telegram_wizard(
                    chat_id_int, card_data, widgets, text,
                    mandate_path, thread_key, department,
                )
            else:
                # Carte simple (pas de dropdowns) — boutons inline classiques
                telegram_msg = self._cardsv2_to_telegram_keyboard(card_data)
                if text and telegram_msg.get("text"):
                    telegram_msg["text"] = f"{text}\n\n{telegram_msg['text']}"
                elif text:
                    telegram_msg["text"] = text

                await self._publish_outbound("telegram", {
                    "action": "send_message_with_buttons",
                    "chat_id": chat_id_int,
                    "text": telegram_msg.get("text", ""),
                    "buttons": telegram_msg.get("buttons", []),
                    "mandate_path": mandate_path,
                })
        elif text:
            await self._publish_outbound("telegram", {
                "action": "send_message",
                "chat_id": chat_id_int,
                "text": text,
                "mandate_path": mandate_path,
            })

        logger.info(
            "[COMM_DISPATCHER] telegram sent — chat_id=%s cards=%d text_len=%d thread=%s",
            chat_id_int, len(cards_v2), len(text), thread_key,
        )

    # ═══════════════════════════════════════════════════════════════
    # WIZARD — cartes multi-step pour Telegram (dropdowns → boutons)
    # ═══════════════════════════════════════════════════════════════

    async def _send_telegram_wizard(
        self,
        chat_id: int,
        card_data: Dict[str, Any],
        widgets: List[Dict[str, Any]],
        context_text: str,
        mandate_path: str,
        thread_key: str,
        department: str,
    ) -> None:
        """
        Cree un wizard multi-step et envoie la premiere etape.

        Chaque dropdown devient une serie de boutons inline (un message par etape).
        Les text_input deviennent un prompt texte avec bouton 'Passer'.
        L'etat est stocke dans Redis avec TTL.
        """
        wizard_id = uuid.uuid4().hex[:6]

        # Construire les etapes depuis les widgets
        steps: List[Dict[str, Any]] = []
        text_parts = [context_text] if context_text else []

        for w in widgets:
            wtype = w.get("type")
            if wtype in ("dropdown", "multi_select"):
                steps.append({
                    "field": w["name"],
                    "type": "choice",
                    "label": w.get("label", w["name"]),
                    "items": w.get("items", []),
                    "default": w.get("defaultValue"),
                    "selected": None,
                })
            elif wtype == "text_input":
                steps.append({
                    "field": w["name"],
                    "type": "text_input",
                    "label": w.get("label", w["name"]),
                    "hint": w.get("hintText", ""),
                    "selected": None,
                })
            elif wtype == "text_paragraph":
                text_parts.append(w.get("text", ""))

        if not steps:
            logger.warning("[COMM_DISPATCHER] wizard: no interactive steps in card")
            return

        context = "\n".join(filter(None, text_parts))

        # Stocker l'etat wizard dans Redis
        state = {
            "card_id": card_data.get("cardId", "unknown"),
            "mandate_path": mandate_path,
            "chat_id": chat_id,
            "thread_key": thread_key,
            "department": department,
            "context_text": context,
            "steps": steps,
            "current_step": 0,
            "wizard_id": wizard_id,
        }

        from app.redis_client import get_redis
        redis = get_redis()
        key = f"{WIZARD_PREFIX}:{chat_id}:{wizard_id}"
        redis.setex(key, WIZARD_TTL, json.dumps(state))

        # Pointeur actif pour detecter les messages texte (text_input steps)
        redis.setex(f"{WIZARD_PREFIX}_active:{chat_id}", WIZARD_TTL, wizard_id)

        logger.info(
            "[COMM_DISPATCHER] wizard created id=%s card=%s steps=%d chat_id=%s",
            wizard_id, card_data.get("cardId"), len(steps), chat_id,
        )

        # Envoyer la premiere etape
        await self.send_wizard_step(state)

    async def send_wizard_step(self, state: Dict[str, Any]) -> None:
        """
        Envoie le message Telegram pour l'etape courante du wizard.

        - choice: message avec boutons inline (un par option)
        - text_input: message avec bouton 'Passer' + attente texte libre
        """
        step_idx = state["current_step"]
        steps = state["steps"]
        wizard_id = state["wizard_id"]
        chat_id = state["chat_id"]
        total_steps = len(steps)

        if step_idx >= total_steps:
            return

        step = steps[step_idx]

        # Contexte (rapport) inclus uniquement au premier step
        header = ""
        if step_idx == 0 and state.get("context_text"):
            header = f"{state['context_text']}\n\n"

        progress = f"[{step_idx + 1}/{total_steps}]"

        if step["type"] == "choice":
            text = f"{header}{progress} {step['label']} :"
            buttons = []
            for item in step["items"]:
                cb = f"w:{wizard_id}:{step_idx}:{item['value']}"
                label = item["text"]
                # Marquer le defaut
                if item["value"] == step.get("default"):
                    label = f">> {label}"
                buttons.append([{"text": label, "callback_data": cb[:64]}])

            await self._publish_outbound("telegram", {
                "action": "send_message_with_buttons",
                "chat_id": chat_id,
                "text": text,
                "buttons": buttons,
                "mandate_path": state["mandate_path"],
            })

        elif step["type"] == "text_input":
            text = f"{progress} {step['label']} :"
            if step.get("hint"):
                text += f"\n{step['hint']}"
            text += "\n\nTapez votre texte ou appuyez sur 'Passer'"

            cb_skip = f"w:{wizard_id}:{step_idx}:__skip__"
            buttons = [[{"text": "Passer", "callback_data": cb_skip}]]

            await self._publish_outbound("telegram", {
                "action": "send_message_with_buttons",
                "chat_id": chat_id,
                "text": text,
                "buttons": buttons,
                "mandate_path": state["mandate_path"],
            })

        logger.info(
            "[COMM_DISPATCHER] wizard step sent id=%s step=%d/%d field=%s chat_id=%s",
            wizard_id, step_idx + 1, total_steps, step["field"], chat_id,
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
