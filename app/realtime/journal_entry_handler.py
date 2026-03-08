"""
JournalEntryHandler — Traite les reponses d'approbation journal_entry_approval_card.

Flux:
1. Utilisateur clique "Confirmer" sur la carte d'approbation
2. orchestration.py route vers ce handler (car card_id = journal_entry_approval_card)
3. Handler charge le payload depuis Redis (je_approval:{id})
4. Si approuve: post DIRECTEMENT vers ERP (IDs deja resolus) → notifie succes
5. Si rejete: supprime Redis → notifie rejet
6. gl_entries se met a jour automatiquement lors de la prochaine sync GL

Architecture:
- Ce handler est DIRECT (pas de Worker LLM) — meme pattern que CMMD cards
- Le payload dans Redis est DEJA RESOLU (_erp_journal_id, _erp_account_id)
  → la resolution se fait dans SUBMIT_FOR_APPROVAL (worker) AVANT l'approbation
- L'appel ERP utilise le nouveau module app/erp/ (ERPProvider abstrait)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("realtime.journal_entry_handler")


class JournalEntryHandler:
    """Traite les reponses d'approbation pour les ecritures comptables."""

    def __init__(self):
        from app.redis_client import get_redis
        self.redis = get_redis()

    async def handle_card_response(
        self,
        uid: str,
        company_id: str,
        thread_key: str,
        action: str,
        params: Dict[str, Any],
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Point d'entree principal — appele par orchestration.py.

        Args:
            uid: Firebase user ID
            company_id: Collection/space ID
            thread_key: Job chat thread key
            action: Action du bouton (ex: "journal_entry_decision")
            params: Parametres de la carte (approval_id, decision, rejection_reason)
            message_id: ID du message de la carte

        Returns:
            Dict avec le resultat pour le frontend
        """
        logger.debug("[JE_HANDLER] params reçus: %s", params)

        # Extraire les parametres
        approval_id = params.get("approval_id", "")
        # decision peut etre dans params directement OU dans widget_values imbrique
        widget_values = params.get("widget_values", {}) or {}
        decision = params.get("decision", "") or widget_values.get("decision", "")
        rejection_reason = (
            params.get("rejection_reason", "")
            or widget_values.get("rejection_reason", "")
        )

        if not approval_id:
            return self._error_response("approval_id manquant dans les parametres")

        if not decision:
            return self._error_response("decision manquante (approve/reject)")

        logger.info(
            "[JE_HANDLER] Card response: decision=%s approval_id=%s uid=%s",
            decision, approval_id, uid,
        )

        # Charger le payload depuis Redis
        redis_key = f"je_approval:{approval_id}"
        payload_json = self.redis.get(redis_key)

        if not payload_json:
            logger.warning("[JE_HANDLER] Payload expire ou introuvable: %s", redis_key)
            await self._notify_user(uid, thread_key, {
                "type": "journal_entry.expired",
                "message": "Approbation expiree (delai de 24h depasse). Veuillez recreer l'ecriture.",
            })
            return self._error_response("Approbation expiree (TTL 24h)")

        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("[JE_HANDLER] Invalid payload JSON: %s", e)
            return self._error_response(f"Payload invalide: {e}")

        # Router selon la decision
        if decision == "reject":
            return await self._handle_rejection(
                uid, company_id, thread_key, approval_id, payload, rejection_reason
            )
        elif decision == "approve":
            return await self._handle_approval(
                uid, company_id, thread_key, approval_id, payload
            )
        else:
            return self._error_response(f"Decision inconnue: {decision}")

    # ═══════════════════════════════════════════════════════════════
    # APPROVAL → ERP POST
    # ═══════════════════════════════════════════════════════════════

    async def _handle_approval(
        self,
        uid: str,
        company_id: str,
        thread_key: str,
        approval_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Approuve l'ecriture: post DIRECTEMENT vers ERP (IDs deja resolus)."""
        entry = payload.get("entry", {})
        mandate_path = payload.get("mandate_path", "")

        if not mandate_path:
            return self._error_response("mandate_path manquant dans le payload")

        # Verifier que les IDs ERP sont bien resolus (pre-condition)
        if not entry.get("_erp_journal_id"):
            return self._error_response(
                "_erp_journal_id manquant — le payload n'a pas ete resolu correctement"
            )

        try:
            # 1. Get ERP provider et poster directement
            # Les IDs ERP (_erp_journal_id, _erp_account_id) sont deja dans le payload
            # (resolus par SUBMIT_FOR_APPROVAL dans le worker AVANT l'approbation)
            from app.erp.erp_provider import get_erp_provider

            provider = await get_erp_provider(uid, company_id, mandate_path)
            result = await provider.post_journal_entry(entry)

            if not result.get("success"):
                error_msg = result.get("error", "Erreur inconnue")
                logger.error("[JE_HANDLER] ERP post failed: %s", error_msg)
                # Cleanup draft entry left in ERP if posting failed
                draft_id = result.get("draft_erp_id")
                if draft_id:
                    try:
                        await provider.delete_draft_entry(draft_id)
                        logger.info("[JE_HANDLER] Draft entry %s cleaned up", draft_id)
                    except Exception as cleanup_err:
                        logger.warning("[JE_HANDLER] Draft cleanup failed: %s", cleanup_err)
                await self._notify_user(uid, thread_key, {
                    "type": "journal_entry.error",
                    "message": f"Erreur lors du posting ERP: {error_msg}",
                    "approval_id": approval_id,
                }, company_id=company_id)
                return self._error_response(f"Erreur posting ERP: {error_msg}")

            # 3. Nettoyer Redis
            self.redis.delete(f"je_approval:{approval_id}")

            # 4. Notifier succes
            erp_entry_id = result.get("erp_entry_id", "")
            erp_entry_name = result.get("erp_entry_name", str(erp_entry_id))

            await self._notify_user(uid, thread_key, {
                "type": "journal_entry.posted",
                "message": f"Ecriture postee avec succes: {erp_entry_name}",
                "erp_entry_id": erp_entry_id,
                "erp_entry_name": erp_entry_name,
                "approval_id": approval_id,
            }, company_id=company_id)

            # 5. Optionnel: declencher sync GL incrementale
            await self._trigger_gl_sync(uid, company_id, mandate_path)

            logger.info(
                "[JE_HANDLER] Ecriture postee: erp_id=%s name=%s uid=%s",
                erp_entry_id, erp_entry_name, uid,
            )

            return {
                "type": "chat.card_clicked",
                "payload": {
                    "success": True,
                    "action": "approve",
                    "erp_entry_id": erp_entry_id,
                    "erp_entry_name": erp_entry_name,
                    "message": f"Ecriture postee: {erp_entry_name}",
                },
            }

        except Exception as e:
            logger.error("[JE_HANDLER] Approval error: %s", e, exc_info=True)
            await self._notify_user(uid, thread_key, {
                "type": "journal_entry.error",
                "message": f"Erreur: {e}",
                "approval_id": approval_id,
            }, company_id=company_id)
            return self._error_response(str(e))

    # ═══════════════════════════════════════════════════════════════
    # REJECTION
    # ═══════════════════════════════════════════════════════════════

    async def _handle_rejection(
        self,
        uid: str,
        company_id: str,
        thread_key: str,
        approval_id: str,
        payload: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """Rejette l'ecriture: supprime Redis, notifie."""
        # Nettoyer Redis
        self.redis.delete(f"je_approval:{approval_id}")

        entry = payload.get("entry", {})
        description = entry.get("description", "")

        await self._notify_user(uid, thread_key, {
            "type": "journal_entry.rejected",
            "message": f"Ecriture rejetee: {description}",
            "reason": reason or "Aucun motif fourni",
            "approval_id": approval_id,
        }, company_id=company_id)

        logger.info(
            "[JE_HANDLER] Ecriture rejetee: approval_id=%s reason=%s",
            approval_id, reason,
        )

        return {
            "type": "chat.card_clicked",
            "payload": {
                "success": True,
                "action": "reject",
                "message": f"Ecriture rejetee: {reason or 'sans motif'}",
            },
        }

    # ═══════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ═══════════════════════════════════════════════════════════════

    async def _notify_user(self, uid: str, thread_key: str, data: Dict[str, Any], company_id: str = ""):
        """Envoie une notification WSS a l'utilisateur."""
        try:
            # Broadcast via Redis PubSub → WorkerBroadcastListener → WSS
            import json as _json

            channel = f"ws:broadcast:{uid}"
            self.redis.publish(channel, _json.dumps({
                "event": data.get("type", "journal_entry.update"),
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

            # Ecrire dans RTDB chats pour persistance
            try:
                from app.firebase_providers import get_firebase_realtime
                import uuid as _uuid

                rtdb = get_firebase_realtime()
                if rtdb and thread_key:
                    msg_id = f"je_notif_{_uuid.uuid4().hex[:8]}"
                    # thread_key format: "collection_id/chats/xxx" or just the key
                    # We need to find the collection_id from context — use thread_key directly
                    # as it may already include the full path prefix
                    msg_data = {
                        "type": "system_log",
                        "sender": "system",
                        "content": data.get("message", ""),
                        "metadata": data,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    rtdb.db.child(company_id).child("chats").child(thread_key).child("messages").child(msg_id).set(msg_data)
            except Exception as e:
                logger.warning("[JE_HANDLER] RTDB write failed (non-blocking): %s", e)

        except Exception as e:
            logger.warning("[JE_HANDLER] Notification failed: %s", e)

    async def _trigger_gl_sync(self, uid: str, company_id: str, mandate_path: str):
        """Declenche une sync GL incrementale en arriere-plan (optionnel)."""
        try:
            import json as _json

            channel = f"user:{uid}/task_manager"
            self.redis.publish(channel, _json.dumps({
                "type": "gl_sync_requested",
                "department": "coa",
                "collection_id": company_id,
                "mandate_path": mandate_path,
                "data": {
                    "force_full": False,
                    "overlap_months": 1,
                    "requested_by": "journal_entry_handler",
                },
            }))
            logger.info("[JE_HANDLER] GL sync triggered for mandate=%s", mandate_path)
        except Exception as e:
            logger.warning("[JE_HANDLER] GL sync trigger failed (non-blocking): %s", e)

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _error_response(error: str) -> Dict[str, Any]:
        return {
            "type": "chat.card_clicked",
            "payload": {"success": False, "error": error},
        }
