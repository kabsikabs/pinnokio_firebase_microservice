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
        """Arrete tous les listeners et le transport PubSub."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        # Attendre que les tasks se terminent proprement
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("[RESPONSE_COLLECTOR] task cleanup error: %s", e)
        self._tasks.clear()

        # Arrêter les streaming pulls PubSub
        try:
            from app.realtime.pubsub_transport import get_pubsub_transport
            transport = get_pubsub_transport()
            transport.stop()
        except Exception as e:
            logger.warning("[RESPONSE_COLLECTOR] transport stop error: %s", e)

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
            # Verifier si un wizard attend un text_input
            text = response.get("text", "")
            wizard_handled = await self._check_wizard_text_input(
                chat_id, text, uid, company_id, mandate_path,
            )
            if wizard_handled:
                return

            # Message texte standard -> enqueue vers Worker LLM
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
            callback_data = response.get("data", "")

            # Wizard callback (prefix "w:")
            if callback_data.startswith("w:"):
                await self._handle_wizard_callback(
                    callback_data, chat_id, uid, company_id, mandate_path,
                )
                return

            # Callback standard (clic bouton) -> convertir en CARD_CLICKED
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

    # ── Wizard multi-step ──

    async def _handle_wizard_callback(
        self,
        callback_data: str,
        chat_id: int,
        uid: str,
        company_id: str,
        mandate_path: str,
    ) -> None:
        """
        Traite un callback wizard (clic bouton sur une etape).

        callback_data format: "w:{wizard_id}:{step_idx}:{value}"
        Charge l'etat wizard, stocke la selection, avance ou complete.
        """
        parts = callback_data.split(":", 3)
        if len(parts) < 4:
            logger.warning("[RESPONSE_COLLECTOR] Invalid wizard callback: %s", callback_data)
            return

        _, wizard_id, step_idx_str, value = parts
        try:
            step_idx = int(step_idx_str)
        except ValueError:
            logger.warning("[RESPONSE_COLLECTOR] Invalid wizard step index: %s", step_idx_str)
            return

        # Charger l'etat wizard depuis Redis
        from app.redis_client import get_redis
        from app.realtime.communication_dispatcher import WIZARD_PREFIX, WIZARD_TTL

        redis = get_redis()
        key = f"{WIZARD_PREFIX}:{chat_id}:{wizard_id}"
        raw = redis.get(key)
        if not raw:
            logger.warning("[RESPONSE_COLLECTOR] Wizard expired or not found: %s", key)
            return

        if isinstance(raw, bytes):
            raw = raw.decode()
        state = json.loads(raw)
        steps = state["steps"]

        # Verifier que le step correspond
        if step_idx != state["current_step"]:
            logger.warning(
                "[RESPONSE_COLLECTOR] Wizard step mismatch: expected=%d got=%d wiz=%s",
                state["current_step"], step_idx, wizard_id,
            )
            return

        # Stocker la selection
        if value == "__skip__":
            steps[step_idx]["selected"] = ""
        else:
            steps[step_idx]["selected"] = value

        logger.info(
            "[RESPONSE_COLLECTOR] Wizard step %d/%d selected: %s=%s wiz=%s",
            step_idx + 1, len(steps), steps[step_idx]["field"], value, wizard_id,
        )

        # Avancer
        state["current_step"] = step_idx + 1

        if state["current_step"] >= len(steps):
            # Toutes les etapes sont completes → finaliser
            redis.delete(key)
            redis.delete(f"{WIZARD_PREFIX}_active:{chat_id}")
            await self._complete_wizard(state, uid, company_id, mandate_path)
        else:
            # Sauvegarder l'etat mis a jour et envoyer l'etape suivante
            redis.setex(key, WIZARD_TTL, json.dumps(state))

            # Si la prochaine etape est text_input, mettre a jour le pointeur actif
            next_step = steps[state["current_step"]]
            if next_step["type"] == "text_input":
                redis.setex(f"{WIZARD_PREFIX}_active:{chat_id}", WIZARD_TTL, wizard_id)

            from app.realtime.communication_dispatcher import get_communication_dispatcher
            dispatcher = get_communication_dispatcher()
            await dispatcher.send_wizard_step(state)

    async def _check_wizard_text_input(
        self,
        chat_id: int,
        text: str,
        uid: str,
        company_id: str,
        mandate_path: str,
    ) -> bool:
        """
        Verifie si un wizard attend un text_input pour ce chat_id.

        Si oui, capture le texte, avance le wizard, et retourne True.
        Si non, retourne False (le message sera traite normalement).
        """
        from app.redis_client import get_redis
        from app.realtime.communication_dispatcher import WIZARD_PREFIX, WIZARD_TTL

        redis = get_redis()
        active_key = f"{WIZARD_PREFIX}_active:{chat_id}"
        wizard_id = redis.get(active_key)
        if not wizard_id:
            return False

        if isinstance(wizard_id, bytes):
            wizard_id = wizard_id.decode()

        key = f"{WIZARD_PREFIX}:{chat_id}:{wizard_id}"
        raw = redis.get(key)
        if not raw:
            redis.delete(active_key)
            return False

        if isinstance(raw, bytes):
            raw = raw.decode()
        state = json.loads(raw)
        steps = state["steps"]
        current = state["current_step"]

        if current >= len(steps):
            return False

        # Verifier que l'etape courante attend du texte
        if steps[current]["type"] != "text_input":
            return False

        # Capturer le texte comme valeur du champ
        steps[current]["selected"] = text
        state["current_step"] = current + 1

        logger.info(
            "[RESPONSE_COLLECTOR] Wizard text_input captured step=%d field=%s len=%d wiz=%s",
            current + 1, steps[current]["field"], len(text), wizard_id,
        )

        if state["current_step"] >= len(steps):
            # Toutes les etapes completes → finaliser
            redis.delete(key)
            redis.delete(active_key)
            await self._complete_wizard(state, uid, company_id, mandate_path)
        else:
            # Sauvegarder et avancer
            redis.setex(key, WIZARD_TTL, json.dumps(state))
            from app.realtime.communication_dispatcher import get_communication_dispatcher
            dispatcher = get_communication_dispatcher()
            await dispatcher.send_wizard_step(state)

        return True

    async def _complete_wizard(
        self,
        state: Dict[str, Any],
        uid: str,
        company_id: str,
        mandate_path: str,
    ) -> None:
        """
        Agrege toutes les selections du wizard et publie CARD_CLICKED_PINNOKIO.

        Construit le payload au format exact attendu par les workers externes
        (ex: process_klk_router_approval_card qui extrait pinnokio_func,
        second_dropdown, instructions).
        """
        from app.realtime.card_transformer import (
            CardTransformer,
            EXTERNAL_WORKER_CARD_IDS,
        )

        card_id = state["card_id"]
        thread_key = state["thread_key"]
        chat_id = state["chat_id"]

        # Construire widget_values depuis les selections
        widget_values = {"action": "approve"}
        for step in state["steps"]:
            widget_values[step["field"]] = step.get("selected", "")

        # Construire le payload CARD_CLICKED_PINNOKIO standard
        standard = CardTransformer.build_card_clicked_payload(
            card_id=card_id,
            widget_values=widget_values,
            thread_key=thread_key,
            user_id=uid,
        )
        standard["communication_chat_type"] = "telegram"
        standard["external_thread_id"] = str(chat_id)

        # Envoyer confirmation a l'utilisateur sur Telegram
        summary_parts = []
        for step in state["steps"]:
            val = step.get("selected", "")
            summary_parts.append(f"- {step['label']}: {val if val else '(vide)'}")
        summary = "\n".join(summary_parts)

        from app.realtime.communication_dispatcher import get_communication_dispatcher
        dispatcher = get_communication_dispatcher()
        await dispatcher._publish_outbound("telegram", {
            "action": "send_message",
            "chat_id": chat_id,
            "text": f"Reponse enregistree:\n{summary}",
            "mandate_path": mandate_path,
        })

        # Router vers le worker
        if card_id in EXTERNAL_WORKER_CARD_IDS:
            await self._publish_response_to_worker(uid, company_id, thread_key, standard)
        else:
            from app.llm_service.llm_gateway import get_llm_gateway
            gateway = get_llm_gateway()
            await gateway.enqueue_card_response(
                user_id=uid,
                collection_name=company_id,
                thread_key=thread_key,
                card_name=card_id,
                card_message_id="",
                action=json.dumps(widget_values),
                communication_chat_type="telegram",
                external_thread_id=str(chat_id),
            )

        logger.info(
            "[RESPONSE_COLLECTOR] Wizard completed card=%s thread=%s uid=%s values=%s",
            card_id, thread_key, uid,
            {s["field"]: s.get("selected", "") for s in state["steps"]},
        )

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
