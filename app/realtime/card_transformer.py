"""
CardTransformer — Source unique de verite pour l'extraction et la reconstruction
des cartes interactives cardsV2 (Google Chat API format).

Consolide la logique dupliquee de:
- redis_subscriber._extract_card_data_from_message()
- handlers._extract_widgets_from_cardsv2()
- orchestration._EXTERNAL_WORKER_CARD_IDS / _DEFAULT_INVOKED_FUNCTION / _FORM_FIELDS_BY_CARD
- card_format_adapter.py (worker LLM)
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ===================================================================
# CONSTANTES — source unique de verite
# ===================================================================

EXTERNAL_WORKER_CARD_IDS = {
    "klk_router_card",
    "klk_router_approval_card",
    "four_eyes_approval_card",
    "approval_card",
    "job_menu_card",
    "bank_list_file_card",
    "bank_file_list_card",
}

# Mapping cardId -> invokedFunction par defaut (bouton principal)
DEFAULT_INVOKED_FUNCTION = {
    "klk_router_card": "answer_pinnokio",
    "klk_router_approval_card": "answer_pinnokio",
    "four_eyes_approval_card": "approve_four_eyes",
    "approval_card": "approve_four_eyes",
    "job_menu_card": "navigate",
    "bank_list_file_card": "start_router_job",
    "bank_file_list_card": "start_router_job",
}

# Mapping cardId -> champs formInputs attendus par les workers
FORM_FIELDS_BY_CARD = {
    "klk_router_card": ["pinnokio_func", "instructions"],
    "klk_router_approval_card": ["pinnokio_func", "second_dropdown", "instructions"],
    "four_eyes_approval_card": ["user_message"],
    "approval_card": ["user_message"],
    "job_menu_card": ["next_step"],
    "bank_list_file_card": ["selected_files", "methode"],
    "bank_file_list_card": ["selected_files", "methode"],
}


class CardTransformer:
    """Extraction et reconstruction des cartes interactives cardsV2."""

    # ─────────────────────────────────────────────────
    # Verification
    # ─────────────────────────────────────────────────

    @staticmethod
    def is_external_worker_card(card_id: str) -> bool:
        """Verifie si un cardId provient d'un worker externe."""
        return card_id in EXTERNAL_WORKER_CARD_IDS

    # ─────────────────────────────────────────────────
    # Direction 1 : cardsV2 -> InteractiveCard (pour le frontend)
    # ─────────────────────────────────────────────────

    @staticmethod
    def cardsv2_to_interactive_card(
        message: Dict[str, Any],
        thread_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Convertit un message RTDB contenant une carte cardsV2 en format InteractiveCard.

        Args:
            message: Message RTDB complet (avec 'content' JSON stringifie ou dict)
            thread_key: Cle du thread de chat

        Returns:
            Dict InteractiveCard-compatible ou None si pas une carte externe
        """
        try:
            raw_content = message.get("content")
            if not raw_content:
                return None

            if isinstance(raw_content, str):
                try:
                    content = json.loads(raw_content)
                except (json.JSONDecodeError, TypeError):
                    return None
            else:
                content = raw_content

            if not isinstance(content, dict):
                return None

            cards_v2 = content.get("cardsV2", [])
            if not cards_v2:
                return None

            first_card = cards_v2[0]
            card_id = first_card.get("cardId", "")

            if not CardTransformer.is_external_worker_card(card_id):
                return None

            card_body = first_card.get("card", {})
            header = card_body.get("header", {})

            # Extraire les widgets
            widgets = []
            text_paragraphs = []

            for section in card_body.get("sections", []):
                for widget in section.get("widgets", []):
                    parsed = CardTransformer.parse_widget(widget)
                    if parsed:
                        if parsed["type"] == "text_paragraph":
                            text_paragraphs.append(parsed.get("text", ""))
                        widgets.append(parsed)

            message_id = (
                message.get("id")
                or message.get("message_id")
                or uuid.uuid4().hex[:12]
            )

            return {
                "cardId": card_id,
                "cardType": card_id,
                "title": header.get("title", "Carte Interactive"),
                "subtitle": header.get("subtitle"),
                "text": "\n".join(text_paragraphs) if text_paragraphs else None,
                "params": {},
                "isVisible": True,
                "threadKey": thread_key,
                "messageId": message_id,
                "widgets": widgets,
            }

        except Exception as e:
            logger.warning("[CARD_TRANSFORMER] extraction failed: %s", e)
            return None

    @staticmethod
    def extract_widgets_from_card_body(card_body: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrait les widgets normalises depuis un card body cardsV2.

        Remplace handlers._extract_widgets_from_cardsv2().
        """
        widgets = []
        for section in card_body.get("sections", []):
            for widget in section.get("widgets", []):
                parsed = CardTransformer.parse_widget(widget)
                if parsed:
                    widgets.append(parsed)
        return widgets

    @staticmethod
    def parse_widget(widget: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse un widget Google Chat API en widget InteractiveCard normalise."""

        # selectionInput (dropdown / multi-select)
        if "selectionInput" in widget:
            sel = widget["selectionInput"]
            sel_type = sel.get("type", "DROPDOWN")
            items = [
                {
                    "text": str(item.get("text", "")),
                    "value": str(item.get("value", item.get("text", ""))),
                }
                for item in sel.get("items", [])
            ]
            raw_default = sel.get("value")
            default_val = str(raw_default) if raw_default is not None else (items[0]["value"] if items else None)
            return {
                "type": "multi_select" if sel_type == "MULTI_SELECT" else "dropdown",
                "name": sel.get("name", ""),
                "label": sel.get("label", ""),
                "items": items,
                "defaultValue": default_val,
            }

        # textInput
        if "textInput" in widget:
            ti = widget["textInput"]
            return {
                "type": "text_input",
                "name": ti.get("name", ""),
                "label": ti.get("label", ""),
                "hintText": ti.get("hintText", ""),
            }

        # buttonList
        if "buttonList" in widget:
            bl = widget["buttonList"]
            buttons = []
            for btn in bl.get("buttons", []):
                action_data = btn.get("onClick", {}).get("action", {})
                btn_params = {}
                for param in action_data.get("parameters", []):
                    btn_params[param.get("key", "")] = param.get("value", "")

                color = btn.get("color", {})
                color_str = None
                if color:
                    r = color.get("red", 0)
                    g = color.get("green", 0)
                    b = color.get("blue", 0)
                    if g > r and g > b:
                        color_str = "green"
                    elif r > g and r > b:
                        color_str = "red"

                buttons.append({
                    "text": btn.get("text", ""),
                    "action": action_data.get("function", ""),
                    "params": btn_params if btn_params else None,
                    "color": color_str,
                    "disabled": btn.get("disabled", False),
                })
            return {"type": "button_list", "buttons": buttons}

        # textParagraph
        if "textParagraph" in widget:
            tp = widget["textParagraph"]
            return {"type": "text_paragraph", "text": tp.get("text", "")}

        # decoratedText
        if "decoratedText" in widget:
            dt = widget["decoratedText"]
            label = dt.get("topLabel", "")
            text = dt.get("text", "")
            return {
                "type": "text_paragraph",
                "text": f"{label}: {text}" if label else text,
            }

        return None

    # ─────────────────────────────────────────────────
    # Direction 2 : InteractiveCard response -> CARD_CLICKED_PINNOKIO
    # ─────────────────────────────────────────────────

    @staticmethod
    def build_card_clicked_payload(
        card_id: str,
        widget_values: Dict[str, Any],
        thread_key: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Reconstruit un payload CARD_CLICKED_PINNOKIO compatible avec les workers externes.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        action = widget_values.get("action", "approve")

        invoked_function = CardTransformer._determine_invoked_function(card_id, action)
        form_inputs = CardTransformer._build_form_inputs(card_id, widget_values)

        return {
            "type": "CARD_CLICKED",
            "threadKey": thread_key,
            "message": {
                "cardsV2": [{
                    "cardId": card_id,
                    "card": {
                        "header": {
                            "title": "Reponse utilisateur",
                            "subtitle": f"Action: {action}",
                        }
                    }
                }]
            },
            "common": {
                "invokedFunction": invoked_function,
                "formInputs": form_inputs,
            },
            "message_type": "CARD_CLICKED_PINNOKIO",
            "timestamp": timestamp,
            "sender_id": user_id,
            "read": False,
        }

    @staticmethod
    def _determine_invoked_function(card_id: str, action: str) -> str:
        """Determine l'invokedFunction selon le card_id et l'action utilisateur."""
        if card_id in ("four_eyes_approval_card", "approval_card"):
            if action in ("reject", "rejected", "reject_four_eyes"):
                return "reject_four_eyes"
            return "approve_four_eyes"
        if card_id == "job_menu_card":
            return "navigate"
        return DEFAULT_INVOKED_FUNCTION.get(card_id, "answer_pinnokio")

    @staticmethod
    def _build_form_inputs(
        card_id: str, widget_values: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Construit les formInputs au format Google Chat API attendu par les workers.

        Format: {"field": {"stringInputs": {"value": ["val"]}}}
        """
        form_inputs = {}
        values = widget_values.get("widget_values", widget_values)
        expected_fields = FORM_FIELDS_BY_CARD.get(card_id, [])

        for field_name in expected_fields:
            raw_value = values.get(field_name)
            if raw_value is None and field_name in ("instructions", "user_message"):
                raw_value = values.get("comment", "") or values.get("user_message", "")

            if isinstance(raw_value, list):
                value_list = raw_value
            elif raw_value is not None:
                value_list = [str(raw_value)]
            else:
                value_list = [""]

            form_inputs[field_name] = {"stringInputs": {"value": value_list}}

        return form_inputs

    # ─────────────────────────────────────────────────
    # Utilitaires
    # ─────────────────────────────────────────────────

    @staticmethod
    def extract_card_id_from_message(message: Dict[str, Any]) -> Optional[str]:
        """Extrait le cardId d'un message RTDB (content JSON ou dict)."""
        try:
            raw_content = message.get("content")
            if not raw_content:
                return None
            if isinstance(raw_content, str):
                content = json.loads(raw_content)
            else:
                content = raw_content
            cards_v2 = content.get("cardsV2", [])
            if cards_v2:
                return cards_v2[0].get("cardId")
            return content.get("message", {}).get("cardType")
        except Exception:
            return None
