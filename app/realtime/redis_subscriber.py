"""
RedisSubscriber - Écoute des Messages Redis PubSub
=================================================

Ce module implémente l'écoute des messages Redis PubSub publiés par les jobbeurs
(Router, APbookeeper, Bankbookeeper) et les traite selon le niveau contextuel.

ARCHITECTURE:
- Écoute les canaux Redis patterns: user:*
- Route les messages selon le type de canal
- Met à jour le cache métier (USER, BUSINESS)
- Publie via WebSocket selon les règles contextuelles

CANAUX REDIS:
- user:{uid}/notifications         → Notifications (Firestore) - Niveau USER (global)
- user:{uid}/direct_message_notif  → Direct Messages (RTDB) - Niveau USER (global)
- user:{uid}/task_manager          → Task Manager (Firestore) - Niveau BUSINESS (page-specific)
- user:{uid}/{collection}/job_chats/{job_id}/messages → Job Chat (RTDB) - CMMD direct WS / MESSAGE → llm_manager
- user:{uid}/{space_code}/*/messages → Chat - DÉJÀ GÉRÉ PAR llm_manager (IGNORÉ)

RÈGLES DE PUBLICATION:
- USER (notifications, direct_message_notif): Publier si utilisateur connecté uniquement
- BUSINESS (task_manager): Publier si utilisateur connecté ET sur la page concernée
- Job Chat (job_chats): CMMD → broadcast direct WebSocket / MESSAGE → llm_manager pour traitement métier
- Chat: Ignoré (déjà géré par llm_manager)

FLUX:
1. Jobbeur écrit dans Firebase/RTDB (persistance)
2. Jobbeur publie sur Redis PubSub (channel pattern)
3. RedisSubscriber reçoit le message
4. Routage selon le type de canal
5. Mise à jour du cache métier
6. Publication WebSocket si règles respectées

@see app/realtime/contextual_publisher.py - Publication contextuelle
@see app/ws_hub.py - Gestion des connexions WebSocket
@see app/llm_service/llm_manager.py - Gestion du chat (NE PAS DUPLIQUER)
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Callable, Tuple
from redis.client import PubSub

from app.redis_client import get_redis
from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.realtime.contextual_publisher import (
    publish_user_event,
    publish_business_event,
    publish_dashboard_event,
    publish_metrics_update,
    CacheLevel,
    _get_user_context,
    _update_cache,
    _get_cache_key,
    _get_ttl_for_cache,
)
from app.status_normalization import StatusNormalizer
from app.active_job_manager import ActiveJobManager
from app.firebase_providers import FirebaseManagement
from app.llm_service.redis_namespaces import (
    build_business_key,
    get_ttl_for_domain,
)

logger = logging.getLogger(__name__)

# Mapping card_id → department for messenger notifications
CARD_ID_TO_DEPARTMENT = {
    "klk_router_card": "Router",
    "klk_router_approval_card": "Router",
    "four_eyes_approval_card": "Router",
    "approval_card": "APbookeeper",
    "job_menu_card": "APbookeeper",
    "bank_list_file_card": "Bankbookeeper",
    "bank_file_list_card": "Bankbookeeper",
    "email_draft_approval_card": "Router",
}

# ============================================
# Extraction des Informations du Canal
# ============================================


def _extract_uid_from_channel(channel: str) -> Optional[str]:
    """
    Extrait l'UID depuis le nom du canal Redis.

    Patterns supportés:
    - user:{uid}/notifications
    - user:{uid}/direct_message_notif
    - user:{uid}/task_manager
    - user:{uid}/{space_code}/{message_mode}/{thread_key}/messages

    Args:
        channel: Nom du canal Redis

    Returns:
        UID extrait ou None si pattern non reconnu
    """
    # Pattern: user:{uid}/...
    match = re.match(r"^user:([^/]+)/", channel)
    if match:
        return match.group(1)
    return None


def _extract_active_job_type(department: str) -> Optional[str]:
    """
    Map department name to active_jobs job_type.
    Returns None if the department doesn't map to an active_jobs type.
    """
    mapping = {
        "router": "router",
        "routing": "router",
        "apbookeeper": "apbookeeper",
        "accounting": "apbookeeper",
        "banker": "banker",
        "bankbookeeper": "banker",
        "banking": "banker",
        "exbookeeper": "exbookeeper",
        "onboarding": "onboarding",
    }
    return mapping.get(department.lower()) if department else None


def _extract_department_to_domain(department: str) -> str:
    """
    Convertit un department en domaine métier.

    Mapping:
    - accounting → invoices
    - banking → bank
    - routing → routing
    - hr → hr

    Args:
        department: Nom du département

    Returns:
        Domaine métier correspondant
    """
    department_map = {
        "accounting": "invoices",
        "apbookeeper": "invoices",  # Workers envoient "APbookeeper"
        "banking": "bank",
        "banker": "bank",           # Workers envoient "Banker"
        "bankbookeeper": "bank",    # Backend _persist_jobs écrit "Bankbookeeper"
        "exbookeeper": "expenses",  # Workers envoient "EXbookeeper"
        "routing": "routing",
        "router": "routing",
        "hr": "hr",
        "expenses": "expenses",
        "coa": "coa",
        "onboarding": "onboarding",
    }
    return department_map.get(department.lower(), department.lower())


# ============================================
# RedisSubscriber - Classe Principale
# ============================================


class RedisSubscriber:
    """
    Subscriber Redis PubSub pour les messages des jobbeurs.

    Écoute les canaux Redis patterns et route les messages vers les handlers appropriés.
    Met à jour le cache métier et publie via WebSocket selon les règles contextuelles.

    IMPORTANT:
    - Ne traite PAS les messages chat (déjà géré par llm_manager)
    - S'abonne avec pattern matching: user:*
    - Gère la reconnexion automatique en cas de perte de connexion

    Usage:
        subscriber = RedisSubscriber()
        await subscriber.start()  # Démarre l'écoute
        await subscriber.stop()   # Arrête l'écoute
    """

    # Department → Telegram module (for thread_key resolution in multi-canal mode)
    _DEPARTMENT_TO_MODULE = {
        "Router": "router",
        "router": "router",
        "APbookeeper": "apbookeeper",
        "apbookeeper": "apbookeeper",
        "Bankbookeeper": "banker",
        "banker": "banker",
        "bankbookeeper": "banker",
        "general": "general",
    }

    def __init__(self):
        """Initialise le RedisSubscriber."""
        self.redis = get_redis()
        self.pubsub: Optional[PubSub] = None
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 5  # Secondes
        self._max_reconnect_attempts = 10
        self._message_count = 0
        self._error_count = 0
        self._start_time = 0.0

    async def start(self) -> None:
        """
        Démarre le subscriber Redis PubSub.

        S'abonne au pattern: user:*
        Lance la boucle d'écoute en arrière-plan.
        """
        if self._running:
            logger.warning("[REDIS_SUBSCRIBER] Already running, skipping start")
            return

        self._running = True
        self._start_time = time.time()

        logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
        logger.info("[REDIS_SUBSCRIBER] RedisSubscriber START - initializing PubSub listener")
        logger.info("[REDIS_SUBSCRIBER] → Redis host=%s port=%s", self.redis.connection_pool.connection_kwargs.get("host"), self.redis.connection_pool.connection_kwargs.get("port"))
        logger.info("[REDIS_SUBSCRIBER] → Pattern subscribed: user:*")
        logger.info("[REDIS_SUBSCRIBER] → Channels to handle: notifications, direct_message_notif, task_manager, job_chats")
        logger.info("[REDIS_SUBSCRIBER] → Chat channels: IGNORED (handled by llm_manager)")

        try:
            self.pubsub = self.redis.pubsub()
            # S'abonner au pattern user:*
            await asyncio.to_thread(self.pubsub.psubscribe, "user:*")

            # Démarrer la boucle d'écoute
            self._listener_task = asyncio.create_task(self._listener_loop())

            logger.info("[REDIS_SUBSCRIBER] RedisSubscriber SUCCESS - listener started")
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except Exception as e:
            self._running = False
            logger.error("[REDIS_SUBSCRIBER] RedisSubscriber FAILED - startup error error=%s", str(e), exc_info=True)
            raise

    async def stop(self) -> None:
        """
        Arrête le subscriber Redis PubSub.

        Désabonne du pattern, ferme la connexion PubSub et arrête la boucle d'écoute.
        """
        if not self._running:
            logger.warning("[REDIS_SUBSCRIBER] Not running, skipping stop")
            return

        logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
        logger.info("[REDIS_SUBSCRIBER] RedisSubscriber STOP - shutting down...")
        logger.info("[REDIS_SUBSCRIBER] → unsubscribing from patterns...")

        self._running = False

        try:
            # Arrêter la tâche d'écoute
            if self._listener_task and not self._listener_task.done():
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except asyncio.CancelledError:
                    pass

            # Fermer la connexion PubSub
            if self.pubsub:
                await asyncio.to_thread(self.pubsub.punsubscribe)
                await asyncio.to_thread(self.pubsub.close)
                self.pubsub = None

            uptime = time.time() - self._start_time
            logger.info("[REDIS_SUBSCRIBER] → closing PubSub connection...")
            logger.info("[REDIS_SUBSCRIBER] → Stats: messages=%s errors=%s uptime=%.2fs", self._message_count, self._error_count, uptime)
            logger.info("[REDIS_SUBSCRIBER] RedisSubscriber SUCCESS - stopped cleanly")
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] RedisSubscriber STOP ERROR - error=%s", str(e), exc_info=True)

    async def _listener_loop(self) -> None:
        """
        Boucle d'écoute principale pour les messages Redis PubSub.

        Écoute en continu les messages publiés sur les canaux souscrits.
        Gère la reconnexion automatique en cas d'erreur.
        """
        reconnect_attempts = 0

        while self._running:
            try:
                if not self.pubsub:
                    logger.error("[REDIS_SUBSCRIBER] PubSub connection lost, reconnecting...")
                    await self._reconnect()
                    reconnect_attempts = 0

                # Écouter les messages (bloquant, mais dans un thread séparé)
                message = await asyncio.to_thread(self.pubsub.get_message, timeout=1.0)

                if message and message["type"] == "pmessage":
                    self._message_count += 1
                    pattern = message.get("pattern")
                    channel = message.get("channel")
                    data = message.get("data")

                    # Ignorer les messages de subscription
                    if isinstance(data, (int, bytes)) and data == 1:
                        continue

                    # Router le message
                    await self._route_message(channel, data)

            except asyncio.CancelledError:
                logger.info("[REDIS_SUBSCRIBER] Listener loop cancelled")
                break

            except Exception as e:
                self._error_count += 1
                logger.error("[REDIS_SUBSCRIBER] listener_loop_error error=%s", str(e), exc_info=True)

                # Gérer la reconnexion
                reconnect_attempts += 1
                if reconnect_attempts >= self._max_reconnect_attempts:
                    logger.error("[REDIS_SUBSCRIBER] Max reconnect attempts reached, stopping listener")
                    self._running = False
                    break

                logger.warning("[REDIS_SUBSCRIBER] redis_connection_lost - attempting reconnect attempt=%s/%s", reconnect_attempts, self._max_reconnect_attempts)
                await asyncio.sleep(self._reconnect_delay)

    async def _reconnect(self) -> None:
        """Reconnecte au Redis PubSub après une perte de connexion."""
        try:
            if self.pubsub:
                await asyncio.to_thread(self.pubsub.close)

            self.pubsub = self.redis.pubsub()
            await asyncio.to_thread(self.pubsub.psubscribe, "user:*")

            logger.info("[REDIS_SUBSCRIBER] redis_reconnected - resubscribing to patterns...")

        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] reconnect_failed error=%s", str(e), exc_info=True)
            raise

    async def _route_message(self, channel: str, data: Any) -> None:
        """
        Route un message Redis vers le handler approprié.

        Patterns supportés:
        - user:{uid}/notifications → _handle_notification_message()
        - user:{uid}/direct_message_notif → _handle_direct_message_message()
        - user:{uid}/task_manager → _handle_task_manager_message()
        - user:{uid}/*/messages → IGNORÉ (déjà géré par llm_manager)

        Args:
            channel: Nom du canal Redis
            data: Données du message (JSON string ou bytes)
        """
        start_time = time.time()

        try:
            # Décoder le channel si bytes
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8")

            # Extraire l'UID
            uid = _extract_uid_from_channel(channel)
            if not uid:
                logger.warning("[REDIS_SUBSCRIBER] routing_message FAILED - invalid channel format channel=%s", channel)
                return

            # Décoder les données
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            try:
                message_data = json.loads(data) if isinstance(data, str) else data
            except json.JSONDecodeError as e:
                logger.error("[REDIS_SUBSCRIBER] json_decode_error channel=%s uid=%s error=%s", channel, uid, str(e), exc_info=True)
                logger.error("[REDIS_SUBSCRIBER] → raw_data=%s", str(data)[:500])
                return

            logger.info("[REDIS_SUBSCRIBER] message_received channel=%s uid=%s", channel, uid)
            logger.debug("[REDIS_SUBSCRIBER] → raw_message=%s", json.dumps(message_data)[:200])
            logger.debug("[REDIS_SUBSCRIBER] → message_type=%s", message_data.get("type"))

            # Routage par type de canal
            logger.debug("[REDIS_SUBSCRIBER] routing_message channel=%s", channel)

            if channel.endswith("/notifications"):
                logger.debug("[REDIS_SUBSCRIBER] → routing to: notifications handler")
                await self._handle_notification_message(uid, channel, message_data)

            elif channel.endswith("/direct_message_notif"):
                logger.debug("[REDIS_SUBSCRIBER] → routing to: direct_message handler")
                await self._handle_direct_message_message(uid, channel, message_data)

            elif channel.endswith("/task_manager"):
                logger.debug("[REDIS_SUBSCRIBER] → routing to: task_manager handler")
                await self._handle_task_manager_message(uid, channel, message_data)

            elif "/job_chats/" in channel and "/messages" in channel:
                logger.debug("[REDIS_SUBSCRIBER] → routing to: job_chat handler")
                await self._handle_job_chat_message(uid, channel, message_data)

            elif channel.endswith("/pending_approval"):
                logger.debug("[REDIS_SUBSCRIBER] → routing to: pending_approval handler")
                await self._handle_pending_approval_message(uid, channel, message_data)

            elif "/messages" in channel:
                logger.debug("[REDIS_SUBSCRIBER] → routing to: IGNORE (chat handled by llm_manager)")
                # Chat déjà géré par llm_manager, ne rien faire

            else:
                logger.warning("[REDIS_SUBSCRIBER] → routing to: UNKNOWN CHANNEL TYPE")

            # Log de performance
            duration_ms = (time.time() - start_time) * 1000
            logger.debug("[REDIS_SUBSCRIBER] message_handled channel=%s uid=%s duration_ms=%.2f", channel, uid, duration_ms)

        except Exception as e:
            self._error_count += 1
            logger.error("[REDIS_SUBSCRIBER] unexpected_error channel=%s error=%s", channel, str(e), exc_info=True)

    # ── Shared sanitization ──────────────────────────────────────────

    @staticmethod
    def _sanitize_value(v):
        """
        Sanitize a value that may contain Firestore sentinel strings.
        Workers use json.dumps(default=str) which converts:
          - firestore.SERVER_TIMESTAMP → "Sentinel: Value used to set a document field to ..."
        """
        if isinstance(v, str) and v.startswith("Sentinel:"):
            from datetime import datetime, timezone
            return datetime.now(timezone.utc).isoformat()
        return v

    def _resolve_notification_doc_id(self, message_data: Dict[str, Any]) -> str:
        """
        Resolve the Firestore document ID from a raw Redis notification payload.

        Convention:
          - Router stores notifications at /notifications/{drive_id}
          - AP/Bank store notifications at /notifications/{job_id}
        """
        # Flatten nested to find drive_id if present
        nested = message_data.get("update_data") or message_data.get("data")
        if isinstance(nested, dict):
            drive_id = nested.get("drive_id") or message_data.get("drive_id")
        else:
            drive_id = message_data.get("drive_id")
        return drive_id or message_data.get("job_id", "")

    # Mapping lowercase function_name → frontend NotificationFunctionName
    # Frontend expects: 'Router' | 'APbookeeper' | 'Bankbookeeper'
    FUNCTION_NAME_NORMALIZE = {
        "router": "Router",
        "apbookeeper": "APbookeeper",
        "bankbookeeper": "Bankbookeeper",
        "onboarding": "Onboarding",
    }

    @staticmethod
    def _transform_notification(doc_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform a Firestore notification document to the frontend Notification
        interface (types/notification.ts).

        Same logic as subscription_manager._transform_notification but static
        so it can be used from RedisSubscriber without SubscriptionManager instance.
        """
        additional_info = data.get("additional_info", "")
        has_additional_info = False
        additional_info_formatted = ""

        if additional_info:
            has_additional_info = True
            if isinstance(additional_info, str):
                try:
                    parsed = json.loads(additional_info)
                    additional_info_formatted = json.dumps(parsed, indent=2)
                except json.JSONDecodeError:
                    additional_info_formatted = additional_info
            else:
                additional_info_formatted = json.dumps(additional_info, indent=2)

        # Serialize timestamp (Firestore DatetimeWithNanoseconds / datetime / string)
        raw_ts = data.get("timestamp")
        if raw_ts is None:
            timestamp = ""
        elif hasattr(raw_ts, "isoformat"):
            timestamp = raw_ts.isoformat()
        elif isinstance(raw_ts, str):
            # Sanitize Sentinel strings that slipped through json.dumps(default=str)
            timestamp = raw_ts if not raw_ts.startswith("Sentinel:") else ""
        else:
            timestamp = str(raw_ts)

        # Normalize functionName to match frontend enum: Router | APbookeeper | Bankbookeeper
        raw_fn = data.get("function_name", data.get("functionName", "Router"))
        function_name = RedisSubscriber.FUNCTION_NAME_NORMALIZE.get(
            raw_fn.lower() if isinstance(raw_fn, str) else "", raw_fn
        )

        # Build file name and status
        file_name = data.get("file_name", data.get("fileName", ""))
        status = data.get("status", "pending")

        # Generate message if missing (notification docs often don't have a 'message' field)
        message = data.get("message", "")
        if not message and file_name:
            message = f"{file_name} - {status}"

        return {
            "docId": doc_id,
            "message": message,
            "fileName": file_name,
            "collectionId": data.get("collection_id", data.get("collectionId", "")),
            "collectionName": data.get("collection_name", data.get("collectionName", "")),
            "status": status,
            "read": data.get("read", False),
            "jobId": data.get("job_id", data.get("jobId", "")),
            "fileId": data.get("file_id", data.get("fileId", "")),
            "functionName": function_name,
            "timestamp": timestamp,
            "additionalInfo": additional_info,
            "hasAdditionalInfo": has_additional_info,
            "additionalInfoFormatted": additional_info_formatted,
            "driveLink": data.get("drive_link", data.get("driveLink", "")),
            "batchId": data.get("batch_id", data.get("batchId", "")),
        }

    async def _handle_notification_message(self, uid: str, channel: str, message_data: Dict[str, Any]) -> None:
        """
        Traite un message du canal notifications.

        Canal: user:{uid}/notifications
        Niveau: USER (global)
        Règle: Publier si utilisateur connecté uniquement

        Architecture:
        - Le PubSub delta est un SIGNAL (seuls les champs modifiés).
        - On re-lit la notification COMPLÈTE depuis Firebase (source de vérité).
        - On transforme vers le format frontend via _transform_notification().
        - On cache et publie le document complet au frontend.

        Args:
            uid: User ID
            channel: Nom du canal Redis
            message_data: Données du message
        """
        start_time = time.time()

        try:
            msg_type = message_data.get("type", "unknown")
            job_id = message_data.get("job_id", "unknown")

            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
            logger.info("[REDIS_SUBSCRIBER] handle_notification START - uid=%s channel=%s", uid, channel)
            logger.info("[REDIS_SUBSCRIBER] → notification_type=%s job_id=%s", msg_type, job_id)

            # ── Step 0: Resolve the Firestore document ID from the PubSub signal ──
            doc_id = self._resolve_notification_doc_id(message_data)
            if not doc_id:
                logger.warning("[REDIS_SUBSCRIBER] → cannot resolve docId from payload, skipping")
                return

            logger.info("[REDIS_SUBSCRIBER] → resolved docId=%s", doc_id)

            # ── Step 1: Re-read the FULL notification from Firebase ──
            # The PubSub message only has the changed fields;
            # Firebase has the complete document (source of truth).
            notification_data = None
            doc_path = f"clients/{uid}/notifications/{doc_id}"
            try:
                firebase = FirebaseManagement()
                raw_doc = await asyncio.to_thread(firebase.get_document, doc_path)
                if raw_doc:
                    notification_data = self._transform_notification(doc_id, raw_doc)
                    logger.info(
                        "[REDIS_SUBSCRIBER] → fetched from Firebase: fileName=%s status=%s functionName=%s",
                        notification_data.get("fileName", "?"),
                        notification_data.get("status", "?"),
                        notification_data.get("functionName", "?"),
                    )
                else:
                    logger.warning("[REDIS_SUBSCRIBER] → doc not found in Firebase: %s", doc_path)
            except Exception as fb_err:
                logger.error("[REDIS_SUBSCRIBER] → Firebase fetch failed: %s", fb_err)

            # Fallback: if Firebase read failed, build minimal notification from PubSub delta
            if notification_data is None:
                logger.warning("[REDIS_SUBSCRIBER] → using PubSub delta as fallback (Firebase unavailable)")
                nested = message_data.get("update_data") or message_data.get("data")
                flat = dict(message_data)
                if isinstance(nested, dict):
                    for k, v in nested.items():
                        if k not in flat:
                            flat[k] = v
                notification_data = self._transform_notification(doc_id, flat)

            # Vérification connexion
            is_connected = hub.is_user_connected(uid)

            # ── Step 2: Update notification cache (upsert by docId) ──
            logger.info("[REDIS_SUBSCRIBER] → Step 2: Updating USER cache (upsert by docId)...")
            ws_action = "new"
            try:
                cache_key = _get_cache_key(CacheLevel.USER, uid, subkey="notifications")
                ttl = _get_ttl_for_cache(CacheLevel.USER)

                redis = get_redis()
                raw_notif = redis.get(cache_key)
                notif_cache = {}
                if raw_notif:
                    try:
                        notif_cache = json.loads(raw_notif) if isinstance(raw_notif, str) else json.loads(raw_notif.decode())
                    except json.JSONDecodeError:
                        notif_cache = {}
                if isinstance(notif_cache, list):
                    notif_cache = {"items": notif_cache}
                notif_items = notif_cache.get("items", [])

                # Remove existing entry with same docId (dedup)
                if doc_id:
                    original_len = len(notif_items)
                    notif_items = [
                        item for item in notif_items
                        if item.get("docId") != doc_id
                    ]
                    if len(notif_items) < original_len:
                        ws_action = "update"

                # Insert the FULL transformed notification at head
                notif_items.insert(0, notification_data)
                notif_cache["items"] = notif_items[:100]
                redis.setex(cache_key, ttl, json.dumps(notif_cache))

                logger.debug("[REDIS_SUBSCRIBER] → notification upserted: docId=%s ws_action=%s total=%d",
                             doc_id, ws_action, len(notif_items))

            except Exception as cache_error:
                logger.error("[REDIS_SUBSCRIBER] cache_update_failed uid=%s error=%s", uid, str(cache_error), exc_info=True)

            # ── Step 3: Publish to frontend via WebSocket ──
            # Use hub.broadcast directly (not publish_user_event) to avoid
            # double cache writes — we already manage our own notification cache above.
            if is_connected:
                logger.info("[REDIS_SUBSCRIBER] → Step 3: Publishing via WebSocket...")
                try:
                    event_type = WS_EVENTS.NOTIFICATION.DELTA
                    payload = {"action": ws_action, "data": notification_data}

                    # Log the exact payload for debugging
                    logger.info(
                        "[REDIS_SUBSCRIBER] → WS payload: action=%s docId=%s functionName=%s fileName=%s status=%s message=%s",
                        ws_action,
                        notification_data.get("docId", "?"),
                        notification_data.get("functionName", "?"),
                        notification_data.get("fileName", "?")[:40],
                        notification_data.get("status", "?"),
                        notification_data.get("message", "?")[:40],
                    )

                    # Broadcast directly to WebSocket (skip publish_user_event cache)
                    await hub.broadcast(uid, {
                        "type": event_type,
                        "payload": payload
                    })

                    logger.info("[REDIS_SUBSCRIBER] → published event_type=%s ws_action=%s docId=%s", event_type, ws_action, doc_id)
                    logger.info("[REDIS_SUBSCRIBER] handle_notification SUCCESS - published to connected user")

                except Exception as publish_error:
                    logger.error("[REDIS_SUBSCRIBER] publish_failed uid=%s error=%s", uid, str(publish_error), exc_info=True)

            else:
                logger.info("[REDIS_SUBSCRIBER] → Step 3: Skipping WebSocket publish (user not connected)")
                logger.info("[REDIS_SUBSCRIBER] handle_notification SUCCESS - cache updated, no WS publish")

            duration_ms = (time.time() - start_time) * 1000
            logger.debug("[REDIS_SUBSCRIBER] → duration_ms=%.2f", duration_ms)
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except KeyError as e:
            logger.error("[REDIS_SUBSCRIBER] missing_field channel=%s uid=%s field=%s", channel, uid, str(e), exc_info=True)
        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] unexpected_error channel=%s uid=%s error=%s", channel, uid, str(e), exc_info=True)

    @staticmethod
    def _transform_direct_message(message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transforme un payload direct_message brut (snake_case worker) en format
        frontend camelCase (DirectMessage interface).

        Pour action "new": extrait les données du worker et ajoute docId.
        Pour action "remove": retourne juste {docId}.
        """
        action = message_data.get("action_type", "new")

        if action == "remove":
            return {
                "docId": message_data.get("message_id", message_data.get("docId", "")),
            }

        # Pour "new": les données worker sont soit dans "data", soit au top-level
        raw = message_data.get("data", message_data)
        message_id = message_data.get("message_id", "")

        return {
            "docId": message_id,
            "message": raw.get("message", raw.get("status", "")),
            "fileName": raw.get("file_name", raw.get("fileName", "")),
            "collectionId": raw.get("collection_id", raw.get("collectionId", "")),
            "collectionName": raw.get("collection_name", raw.get("collectionName", "")),
            "status": raw.get("status", ""),
            "jobId": raw.get("job_id", raw.get("jobId", "")),
            "fileId": raw.get("file_id", raw.get("fileId", "")),
            "functionName": raw.get("function_name", raw.get("functionName", "Chat")),
            "timestamp": raw.get("timestamp", message_data.get("timestamp", "")),
            "chatMode": raw.get("chat_mode", raw.get("chatMode", "")),
            "threadKey": raw.get("thread_key", raw.get("threadKey", "")),
            "driveLink": raw.get("drive_link", raw.get("driveLink", "")),
            "batchId": raw.get("batch_id", raw.get("batchId", "")),
        }

    async def _handle_direct_message_message(self, uid: str, channel: str, message_data: Dict[str, Any]) -> None:
        """
        Traite un message du canal direct_message_notif (Messenger).

        Canal: user:{uid}/direct_message_notif
        Niveau: USER (global)
        Règle: Publier si utilisateur connecté uniquement
        Priorité: HIGH (message nécessitant action immédiate)

        Args:
            uid: User ID
            channel: Nom du canal Redis
            message_data: Données du message
        """
        start_time = time.time()

        try:
            message_id = message_data.get("message_id", "unknown")
            action_type = message_data.get("action_type", "unknown")
            priority = message_data.get("priority", "normal")

            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
            logger.info("[REDIS_SUBSCRIBER] handle_direct_message START - uid=%s channel=%s", uid, channel)
            logger.info("[REDIS_SUBSCRIBER] → message_id=%s action_type=%s priority=%s", message_id, action_type, priority)

            # Vérification connexion
            is_connected = hub.is_user_connected(uid)
            logger.debug("[REDIS_SUBSCRIBER] → user_connected=%s", is_connected)

            # Transformer le payload brut snake_case → camelCase (format DirectMessage)
            action = message_data.get("action_type", "new")
            transformed_data = self._transform_direct_message(message_data)

            logger.debug("[REDIS_SUBSCRIBER] → transformed_data=%s", transformed_data)

            # Étape 1: Mise à jour du cache USER (TOUJOURS)
            logger.info("[REDIS_SUBSCRIBER] → Step 1: Updating USER cache (messages)...")
            try:
                cache_key = _get_cache_key(CacheLevel.USER, uid, subkey="messages")
                ttl = _get_ttl_for_cache(CacheLevel.USER)

                cache_payload = {
                    "action": action,
                    "data": transformed_data
                }
                _update_cache(CacheLevel.USER, cache_key, cache_payload, ttl)
                logger.debug("[REDIS_SUBSCRIBER] → cache_key=%s action=%s", cache_key, action)

            except Exception as cache_error:
                logger.error("[REDIS_SUBSCRIBER] cache_update_failed uid=%s error=%s", uid, str(cache_error), exc_info=True)

            # Étape 2: Publication WebSocket si connecté
            if is_connected:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Publishing HIGH PRIORITY message via WebSocket...")
                try:
                    event_type = WS_EVENTS.MESSENGER.DELTA
                    payload = {"action": action, "data": transformed_data}

                    # Publier via contextual_publisher (niveau USER)
                    published = await publish_user_event(
                        uid=uid,
                        event_type=event_type,
                        payload=payload,
                        cache_subkey="messages"
                    )

                    if published:
                        logger.info("[REDIS_SUBSCRIBER] → published event_type=%s priority=HIGH", event_type)
                        logger.info("[REDIS_SUBSCRIBER] handle_direct_message SUCCESS - published to connected user")
                    else:
                        logger.warning("[REDIS_SUBSCRIBER] → publish_failed (user disconnected during processing)")

                except Exception as publish_error:
                    logger.error("[REDIS_SUBSCRIBER] publish_failed uid=%s error=%s", uid, str(publish_error), exc_info=True)

            else:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Skipping WebSocket publish (user not connected)")
                logger.warning("[REDIS_SUBSCRIBER] → HIGH PRIORITY message cached but user offline")
                logger.info("[REDIS_SUBSCRIBER] handle_direct_message SUCCESS - cache updated, no WS publish")

            duration_ms = (time.time() - start_time) * 1000
            logger.debug("[REDIS_SUBSCRIBER] → duration_ms=%.2f", duration_ms)
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except KeyError as e:
            logger.error("[REDIS_SUBSCRIBER] missing_field channel=%s uid=%s field=%s", channel, uid, str(e), exc_info=True)
        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] unexpected_error channel=%s uid=%s error=%s", channel, uid, str(e), exc_info=True)

    # Clés de listes universelles (identiques pour tous les domaines)
    DOMAIN_CATEGORY_KEYS = {
        "routing": ["to_process", "in_process", "pending", "processed"],
        "invoices": ["to_process", "in_process", "pending", "processed"],
        "bank": ["to_process", "in_process", "pending", "processed"],
        "expenses": ["to_process", "in_process", "pending", "processed"],
    }

    # Mapping status → catégorie universelle (to_process, in_process, pending, processed)
    STATUS_TO_ABSTRACT_CATEGORY = {
        # → to_process
        "to_process": "to_process", "to_do": "to_process", "new": "to_process",
        "error": "to_process", "stopped": "to_process", "unprocessed": "to_process",
        "to_reconcile": "to_process", "skipped": "to_process",
        # → in_process
        "in_process": "in_process", "on_process": "in_process", "processing": "in_process",
        "in_progress": "in_process", "in_queue": "in_process", "running": "in_process",
        "stopping": "in_process",
        # → pending
        "pending": "pending", "pending_approval": "pending", "awaiting_approval": "pending",
        # → processed
        "processed": "processed", "completed": "processed", "done": "processed",
        "close": "processed", "closed": "processed", "matched": "processed",
        "reconciled": "processed", "approved": "processed", "routed": "processed",
    }

    # Mapping catégorie abstraite → clé concrète (identique pour tous les domaines)
    _UNIVERSAL_KEY = {"to_process": "to_process", "in_process": "in_process", "pending": "pending", "processed": "processed"}
    ABSTRACT_TO_DOMAIN_KEY = {
        "routing": _UNIVERSAL_KEY,
        "invoices": _UNIVERSAL_KEY,
        "bank": _UNIVERSAL_KEY,
        "expenses": _UNIVERSAL_KEY,
    }

    def _get_target_category_key(self, domain: str, status: str) -> str:
        """Convertit un status en clé de catégorie pour le domaine donné."""
        abstract = self.STATUS_TO_ABSTRACT_CATEGORY.get(status, "to_process")
        return self._UNIVERSAL_KEY.get(abstract, "to_process")

    def _update_business_cache_item(
        self,
        uid: str,
        company_id: str,
        domain: str,
        job_id: str,
        item_data: Dict[str, Any],
        is_new: bool = False
    ) -> None:
        """
        Met à jour un item dans le cache business, en respectant le format réel du cache.

        Chaque domaine a ses propres clés de catégorie:
        - routing: to_process, in_process, pending, processed
        - invoices (AP): to_do, in_process, pending, processed
        - bank: to_reconcile, in_process, pending, matched

        Quand un status change (ex: in_queue → pending):
        1. Trouver l'item dans toutes les listes du domaine (par job_id)
        2. Le retirer de l'ancienne liste
        3. MERGER le delta avec l'item original (préserver les champs existants)
        4. L'ajouter dans la nouvelle liste (basée sur le nouveau status)
        """
        try:
            redis = get_redis()
            cache_key = build_business_key(uid, company_id, domain)

            # Lire le cache existant
            raw = redis.get(cache_key)
            if raw:
                try:
                    cache_data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                except json.JSONDecodeError:
                    cache_data = {}
            else:
                cache_data = {}

            # Détecter le format wrapper du unified_cache_manager
            is_wrapped = "cache_version" in cache_data and "data" in cache_data
            inner_data = cache_data.get("data", cache_data) if is_wrapped else cache_data

            # Récupérer les clés de catégorie pour ce domaine
            category_keys = self.DOMAIN_CATEGORY_KEYS.get(
                domain, self.DOMAIN_CATEGORY_KEYS.get("routing", [])
            )

            # Déterminer si le cache utilise des listes pré-catégorisées
            uses_categorized_lists = any(k in inner_data for k in category_keys)

            if uses_categorized_lists:
                # Format pré-catégorisé (routing, invoices, bank)
                # Toutes les catégories sont des flat lists (bank inclus, batches calculés à la volée)

                # Extraire transaction_id depuis department_data pour matching bank
                # Les items to_process ont "id": "577" (move_id ERP)
                # Le delta a job_id: "company_18_577" (composite)
                # department_data peut être:
                #   - un dict imbriqué: {"Bankbookeeper": {"transaction_id": "577"}}
                #   - OU des clés dot-path: {"department_data.Bankbookeeper.transaction_id": "577"}
                tx_id_for_match = None
                if domain in ("bank", "banking"):
                    # Tentative 1: dict imbriqué classique
                    dept_data = item_data.get("department_data", {})
                    if isinstance(dept_data, dict) and dept_data:
                        bk_data = dept_data.get("Bankbookeeper", dept_data.get("banker", dept_data.get("Banker", {})))
                        tx_id_for_match = str(bk_data.get("transaction_id", "")) if bk_data else None
                    # Tentative 2: clés dot-path (venant de update_task_manager_transaction_direct)
                    if not tx_id_for_match:
                        for dot_key in ("department_data.Bankbookeeper.transaction_id", "department_data.banker.transaction_id"):
                            val = item_data.get(dot_key)
                            if val:
                                tx_id_for_match = str(val)
                                break
                    # Tentative 3: extraire le dernier segment du composite job_id
                    # job_id = "klk_space_id_xxx_18_577" → transaction_id = "577"
                    if not tx_id_for_match and job_id and "_" in job_id:
                        last_segment = job_id.rsplit("_", 1)[-1]
                        if last_segment.isdigit():
                            tx_id_for_match = last_segment

                # Étape 1: Trouver l'item existant dans toutes les listes (conserver ses champs)
                existing_item = None
                for cat_key in category_keys:
                    cat_list = inner_data.get(cat_key, [])
                    if not isinstance(cat_list, list):
                        continue
                    for item in cat_list:
                        if (item.get("job_id") == job_id or item.get("id") == job_id or item.get("task_id") == job_id or
                                (tx_id_for_match and (str(item.get("id", "")) == tx_id_for_match or str(item.get("transaction_id", "")) == tx_id_for_match))):
                            existing_item = item
                            break
                    if existing_item:
                        break

                # Déterminer le nouveau status: si le delta n'a pas de status,
                # conserver le status de l'item existant pour la catégorisation
                new_status = (item_data.get("status") or "").lower()
                if not new_status and existing_item:
                    new_status = (existing_item.get("status") or "").lower()
                target_category = self._get_target_category_key(domain, new_status)

                # Étape 2: Retirer l'item de toutes les listes
                for cat_key in category_keys:
                    cat_list = inner_data.get(cat_key, [])
                    if not isinstance(cat_list, list):
                        continue
                    inner_data[cat_key] = [
                        item for item in cat_list
                        if not (item.get("job_id") == job_id or item.get("id") == job_id or item.get("task_id") == job_id or
                                (tx_id_for_match and (str(item.get("id", "")) == tx_id_for_match or str(item.get("transaction_id", "")) == tx_id_for_match)))
                    ]

                # Étape 3: MERGER le delta PubSub avec l'item original
                if existing_item:
                    merged_item = {**existing_item, **item_data}
                else:
                    merged_item = item_data

                # Étape 4: Insérer l'item mergé dans la bonne catégorie
                if target_category not in inner_data:
                    inner_data[target_category] = []
                if not isinstance(inner_data[target_category], list):
                    inner_data[target_category] = []
                inner_data[target_category].insert(0, merged_item)

                total = sum(
                    len(v) if isinstance(v, list) else (
                        sum(len(items) for items in v.values() if isinstance(items, list))
                        if isinstance(v, dict) else 0
                    )
                    for k, v in inner_data.items() if k in category_keys
                )
                logger.info(
                    "[REDIS_SUBSCRIBER] business_cache_updated domain=%s status=%s → category=%s "
                    "merged=%s total_items=%s job_id=%s",
                    domain, new_status, target_category,
                    "existing+delta" if existing_item else "new_item_only",
                    total, job_id
                )
            else:
                # Format liste plate (cache vide ou format inconnu)
                list_key = "items"
                items = inner_data.get(list_key, [])

                found = False
                for i, item in enumerate(items):
                    if item.get("job_id") == job_id or item.get("id") == job_id:
                        items[i] = {**item, **item_data}
                        found = True
                        break
                if not found:
                    items.insert(0, item_data)

                inner_data[list_key] = items
                logger.info(
                    "[REDIS_SUBSCRIBER] business_cache_updated domain=%s list_key=%s items_count=%s job_id=%s",
                    domain, list_key, len(items), job_id
                )

            # Reconstruire avec wrapper si nécessaire
            if is_wrapped:
                cache_data["data"] = inner_data
            else:
                cache_data = inner_data

            # Sauvegarder avec TTL
            ttl = get_ttl_for_domain(domain)
            redis.setex(cache_key, ttl, json.dumps(cache_data))

        except Exception as e:
            logger.error(
                "[REDIS_SUBSCRIBER] business_cache_update_failed uid=%s domain=%s error=%s",
                uid, domain, str(e), exc_info=True
            )

    # ── Cross-domain: Router → target department cache ADD ──────────────
    _TARGET_DEPT_KEYS = ["EXbookeeper", "exbookeeper", "APbookeeper", "Apbookeeper", "apbookeeper"]
    _DEPT_KEY_TO_DOMAIN = {
        "exbookeeper": "expenses",
        "apbookeeper": "invoices",
    }

    async def _cross_domain_add_after_routing(
        self,
        uid: str,
        company_id: str,
        job_id: str,
        collection_path: str,
    ) -> List[Tuple[str, dict]]:
        """
        Après qu'un item Router passe à completed/routed, lire le document
        Firebase task_manager pour récupérer department_data.{target} et
        ajouter l'item dans le cache du domaine cible (expenses / invoices).

        Le Redis notification du Router ne contient PAS department_data —
        seul le document Firebase l'a (source de vérité).

        Returns:
            Liste de (target_domain, flat_item) pour chaque domaine cible ajouté.
            Permet à l'appelant de publier les WSS events correspondants.
        """
        added_items: List[Tuple[str, dict]] = []
        try:
            # 1. Lire le document Firebase task_manager (sync I/O → thread)
            doc_path = f"clients/{uid}/task_manager/{job_id}"
            firebase = FirebaseManagement()
            task_doc = await asyncio.to_thread(firebase.get_document, doc_path)

            if not task_doc:
                logger.warning(
                    "[CROSS_DOMAIN] task_manager doc not found: %s — skipping",
                    doc_path,
                )
                return added_items

            dept_data = task_doc.get("department_data", {})
            if not dept_data:
                logger.info(
                    "[CROSS_DOMAIN] no department_data in %s — nothing to cross-add",
                    doc_path,
                )
                return added_items

            # 2. Pour chaque département cible présent, bâtir un item et l'ajouter
            processed_domains = set()
            for dept_key in self._TARGET_DEPT_KEYS:
                target_entry = dept_data.get(dept_key)
                if not target_entry or not isinstance(target_entry, dict):
                    continue

                target_domain = self._DEPT_KEY_TO_DOMAIN.get(dept_key.lower())
                if not target_domain or target_domain in processed_domains:
                    continue
                processed_domains.add(target_domain)

                # 3. Construire l'item au format attendu par le cache cible
                item = self._build_cross_domain_item(
                    target_domain=target_domain,
                    dept_entry=target_entry,
                    task_doc=task_doc,
                    job_id=job_id,
                )

                # 4. Injecter dans le cache cible comme nouvel item (to_process)
                logger.info(
                    "[CROSS_DOMAIN] ADD to %s cache — job_id=%s status=%s",
                    target_domain, job_id, item.get("status", "to_process"),
                )
                self._update_business_cache_item(
                    uid=uid,
                    company_id=company_id,
                    domain=target_domain,
                    job_id=job_id,
                    item_data=item,
                    is_new=True,
                )
                added_items.append((target_domain, item))

        except Exception as e:
            logger.error(
                "[CROSS_DOMAIN] failed uid=%s job_id=%s error=%s",
                uid, job_id, str(e),
                exc_info=True,
            )
        return added_items

    @staticmethod
    def _build_cross_domain_item(
        target_domain: str,
        dept_entry: dict,
        task_doc: dict,
        job_id: str,
    ) -> dict:
        """
        Construit un item formaté pour le cache du domaine cible.

        expenses → format ExpensesHandlers._transform_expenses
        invoices → format standard AP cache
        """
        # Champs communs provenant du document racine task_manager
        base = {
            "job_id": task_doc.get("job_id", job_id),
            "file_name": task_doc.get("file_name", dept_entry.get("file_name", "")),
            "file_id": task_doc.get("file_id", dept_entry.get("file_id", "")),
            "mandate_path": task_doc.get("mandate_path", ""),
            "status": dept_entry.get("status", "to_process"),
        }

        if target_domain == "expenses":
            # Format identique à ExpensesHandlers._transform_expenses
            base["expense_id"] = task_doc.get("id", job_id)
            base["supplier"] = dept_entry.get("supplier", "")
            base["amount"] = dept_entry.get("amount", 0)
            base["date"] = dept_entry.get("date", "")
            base["currency"] = dept_entry.get("currency", "")
            base["category"] = dept_entry.get("category", "")
            base["description"] = dept_entry.get("description", "")

        elif target_domain == "invoices":
            # Format standard AP cache
            base["supplier"] = dept_entry.get("supplier", "")
            base["amount"] = dept_entry.get("amount", 0)
            base["date"] = dept_entry.get("date", "")
            base["currency"] = dept_entry.get("currency", "")
            base["current_step"] = dept_entry.get("current_step", "")

        return base

    @staticmethod
    def _normalize_department(dept: str) -> str:
        """Normalise le nom du département pour cohérence avec _get_expenses() dans handlers.py."""
        dept_lower = (dept or "").lower().strip()
        if dept_lower in ("apbookeeper", "ap_bookeeper"):
            return "APBookkeeper"
        elif dept_lower == "router":
            return "Router"
        elif dept_lower in ("banker", "bank"):
            return "Banker"
        elif dept_lower in ("chat", "chat_usage", "chat_daily"):
            return "Chat"
        elif dept_lower in ("exbookeeper", "ex_bookeeper"):
            return "EXbookeeper"
        elif dept_lower in ("hr", "payroll"):
            return "HR"
        elif dept_lower == "onboarding":
            return "Onboarding"
        elif dept_lower in ("reverse_reconciliation", "reversereconciliation"):
            return "Reverse_reconciliation"
        return dept or "Other"

    def _update_billing_history_cache(
        self,
        uid: str,
        company_id: str,
        job_id: str,
        message_data: dict
    ) -> Optional[dict]:
        """
        Met à jour un ExpenseItem dans le cache billing_history (Historique des dépenses).

        Le billing_history est une collection d'ExpenseItems issus de task_manager,
        affichés dans le tableau du dashboard. Pas de logique open/closed ni de metrics.
        On cherche l'item par job_id, on merge les champs du payload PubSub, on sauvegarde.
        """
        try:
            redis = get_redis()
            cache_key = build_business_key(uid, company_id, "billing_history")

            raw = redis.get(cache_key)
            if not raw:
                return None

            try:
                cache_data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            except json.JSONDecodeError:
                return None

            # Unwrap unified_cache_manager format
            is_wrapped = "cache_version" in cache_data and "data" in cache_data
            inner_data = cache_data.get("data", cache_data) if is_wrapped else cache_data

            # Chercher l'item par jobId/id dans la liste plate items[]
            items_list = inner_data.get("items", [])

            existing_item = None
            for item in items_list:
                if item.get("jobId") == job_id or item.get("id") == job_id:
                    existing_item = item
                    break

            if existing_item is None:
                # Item pas encore dans le cache — le créer depuis les données PubSub
                logger.info(
                    "[REDIS_SUBSCRIBER] billing_history item NOT FOUND job_id=%s in %d items, creating from PubSub",
                    job_id, len(items_list)
                )
                item_data = message_data.get("data", message_data)
                raw_dept = message_data.get("department") or item_data.get("department") or ""
                existing_item = {
                    "jobId": job_id,
                    "id": job_id,
                    "status": message_data.get("status", item_data.get("status", "unknown")),
                    "department": self._normalize_department(raw_dept),
                    "fileName": item_data.get("file_name", ""),
                    "mandatePath": message_data.get("mandate_path", item_data.get("mandate_path", "")),
                    "createdAt": item_data.get("started_at", item_data.get("last_event_time", "")),
                    "timestamp": item_data.get("started_at") or item_data.get("last_event_time") or message_data.get("timestamp", ""),
                    "totalTokens": 0,
                    "cost": 0.0,
                    "currency": "CHF",
                    "currentStep": "",
                    "lastMessage": "",
                    "lastOutcome": "",
                }
                items_list.insert(0, existing_item)

            # Merger les champs du payload PubSub → ExpenseItem (camelCase)
            # Les champs peuvent être au top-level OU dans message_data["data"]
            item_data = message_data.get("data", {})

            # Status (top-level ou nested)
            raw_status = message_data.get("status") or item_data.get("status")
            if raw_status:
                old_status = existing_item.get("status", "N/A")
                existing_item["status"] = raw_status
                logger.info(
                    "[REDIS_SUBSCRIBER] billing_history STATUS CHANGE job_id=%s: '%s' → '%s'",
                    job_id, old_status, raw_status
                )
            for src_key, dst_key in (
                ("current_step", "currentStep"),
                ("last_message", "lastMessage"),
                ("last_outcome", "lastOutcome"),
                ("file_name", "fileName"),
                ("uri_file_link", "uriFileLink"),
            ):
                val = message_data.get(src_key) or item_data.get(src_key)
                if val:
                    existing_item[dst_key] = val
            # Department (normaliser pour cohérence avec _get_expenses() dans handlers.py)
            dept = message_data.get("department") or item_data.get("department")
            if dept:
                existing_item["department"] = self._normalize_department(dept)

            # Billing data (top-level OU imbriqué dans data)
            billing = message_data.get("billing") or item_data.get("billing")
            if isinstance(billing, dict):
                if "total_tokens" in billing:
                    existing_item["totalTokens"] = int(billing["total_tokens"] or 0)
                if "total_sales_price" in billing:
                    existing_item["cost"] = float(billing["total_sales_price"] or 0)
                if "currency" in billing:
                    existing_item["currency"] = billing["currency"]
                if "billed" in billing:
                    existing_item["billed"] = bool(billing["billed"])

            # Department-specific data
            dept_data = message_data.get("department_data")
            if isinstance(dept_data, dict):
                # APBookkeeper
                ap_data = dept_data.get("APbookeeper") or dept_data.get("Apbookeeper") or {}
                if ap_data:
                    for src, dst in [
                        ("supplier_name", "supplierName"),
                        ("invoice_ref", "invoiceRef"),
                        ("invoice_date", "invoiceDate"),
                        ("due_date", "dueDate"),
                        ("accounting_date", "accountingDate"),
                        ("invoice_description", "invoiceDescription"),
                    ]:
                        if src in ap_data:
                            existing_item[dst] = ap_data[src]
                    for src, dst in [
                        ("amount_vat_excluded", "amountVatExcluded"),
                        ("amount_vat_included", "amountVatIncluded"),
                        ("amount_vat", "amountVat"),
                    ]:
                        if src in ap_data:
                            existing_item[dst] = float(ap_data[src] or 0)
                    if "currency" in ap_data:
                        existing_item["currency"] = ap_data["currency"]

                # Router
                router_data = dept_data.get("Router") or dept_data.get("router") or {}
                if router_data:
                    if "destination" in router_data:
                        existing_item["routeDestination"] = router_data["destination"]
                    if "confidence" in router_data:
                        existing_item["routeConfidence"] = float(router_data["confidence"] or 0)

                # Banker
                banker_data = dept_data.get("Banker") or dept_data.get("banker") or {}
                if banker_data:
                    if "bank_account" in banker_data:
                        existing_item["bankAccount"] = banker_data["bank_account"]
                    if "transaction_type" in banker_data:
                        existing_item["transactionType"] = banker_data["transaction_type"]

            # Reconstruire avec wrapper
            if is_wrapped:
                cache_data["data"] = inner_data
            else:
                cache_data = inner_data

            # Sauvegarder (TTL 1800s = même durée que le page_state dashboard)
            redis.setex(cache_key, 1800, json.dumps(cache_data))

            logger.info(
                "[REDIS_SUBSCRIBER] billing_history_updated job_id=%s fields_merged=%s",
                job_id, [k for k in message_data.keys() if k not in ("channel", "uid", "company_id")]
            )
            return existing_item

        except Exception as e:
            logger.error(
                "[REDIS_SUBSCRIBER] billing_history_update_failed uid=%s job_id=%s error=%s",
                uid, job_id, str(e), exc_info=True
            )
            return None

    async def _handle_pending_approval_message(self, uid: str, channel: str, message_data: Dict[str, Any]) -> None:
        """
        Traite un message du canal pending_approval.

        Canal: user:{uid}/pending_approval
        Niveau: BUSINESS (dashboard)
        Règle: Toujours invalider le cache approval + publier WS si utilisateur connecté sur la même company
        """
        try:
            action = message_data.get("action", "add")
            job_id = message_data.get("job_id", "unknown")
            company_id = message_data.get("company_id", "")
            raw_department = message_data.get("department", "")

            # Normaliser le département pour le frontend (attend "router"|"banker"|"apbookeeper")
            _PENDING_DEPT_MAP = {
                "routing": "router",
                "banking": "banker",
                "bankbookeeper": "banker",
                "accounting": "apbookeeper",
            }
            department = _PENDING_DEPT_MAP.get(raw_department.lower(), raw_department.lower()) if raw_department else ""

            logger.info("[REDIS_SUBSCRIBER] handle_pending_approval START - uid=%s action=%s job_id=%s dept=%s(raw=%s)", uid, action, job_id, department, raw_department)

            if not company_id:
                logger.warning("[REDIS_SUBSCRIBER] → pending_approval: missing company_id, skipping")
                return

            # Toujours invalider le cache approval pour que le prochain chargement dashboard
            # récupère les données fraîches depuis Firebase
            try:
                redis = get_redis()
                cache_key = f"approvals:{company_id}"
                redis.delete(cache_key)
                logger.info("[REDIS_SUBSCRIBER] → pending_approval: cache invalidated key=%s", cache_key)
            except Exception as cache_err:
                logger.warning("[REDIS_SUBSCRIBER] → pending_approval: cache invalidation failed: %s", cache_err)

            # Publier via WebSocket uniquement si l'utilisateur est sur le dashboard
            # (les données approval sont chargées au mount du dashboard, les deltas ne servent
            # qu'à mettre à jour le state pendant qu'on y est — même logique que billing_history)
            is_connected = hub.is_user_connected(uid)
            if is_connected:
                context = _get_user_context(uid)
                current_company = context.get("company_id")
                current_domain = context.get("current_domain")

                if current_company == company_id and current_domain == "dashboard":
                    try:
                        # Construire le payload au format attendu par le frontend
                        # Transformer snake_case PubSub → camelCase matching _format_approval_item()
                        # Référence: approval_handlers.py L250-277
                        item_data = message_data.get("data", {})
                        raw_years = item_data.get("available_years", [])
                        available_years = [str(y) for y in raw_years] if raw_years else []
                        confidence_score = item_data.get("confidence_score", 0)

                        formatted_item = {
                            "id": item_data.get("id", job_id),
                            "fileName": item_data.get("file_name", ""),
                            "account": item_data.get("account", ""),
                            "agentNote": item_data.get("selected_motivation", "") or item_data.get("agent_note", ""),
                            "confidenceScore": confidence_score,
                            "confidenceScoreStr": f"{int(confidence_score * 100)}%" if confidence_score else "0%",
                            "confidenceColor": "green" if confidence_score >= 0.8 else "yellow" if confidence_score >= 0.5 else "red",
                            "driveFileId": item_data.get("drive_file_id", ""),
                            "createdAt": item_data.get("timestamp", ""),
                            "contextPayload": item_data.get("context_payload", {}),
                            "availableServices": item_data.get("service_list", []),
                            "availableYears": available_years,
                            "selectedService": item_data.get("selected_service", "") or item_data.get("service", ""),
                            "selectedFiscalYear": str(item_data.get("selected_fiscal_year", "")) if item_data.get("selected_fiscal_year") else "",
                            "suggestedService": item_data.get("selected_service", "") or item_data.get("service", ""),
                            "suggestedYear": str(item_data.get("selected_fiscal_year", "")) if item_data.get("selected_fiscal_year") else "",
                            "instructions": item_data.get("instructions", ""),
                            "jobId": item_data.get("job_id", job_id),
                            "fileId": item_data.get("file_id", ""),
                            "driveLink": item_data.get("drive_link", ""),
                        }

                        ws_payload = {
                            "action": action,
                            "item": formatted_item,
                            "job_id": job_id,
                            "department": department,
                        }
                        published = await publish_dashboard_event(
                            uid=uid,
                            company_id=company_id,
                            event_type="dashboard.pending_approval_update",
                            payload=ws_payload,
                        )
                        if published:
                            logger.info("[REDIS_SUBSCRIBER] → pending_approval published to dashboard: action=%s dept=%s", action, department)
                    except Exception as publish_error:
                        logger.error("[REDIS_SUBSCRIBER] pending_approval publish_failed: %s", publish_error)
                else:
                    logger.debug("[REDIS_SUBSCRIBER] → pending_approval: user not on dashboard (domain=%s), skipping WS (cache already invalidated)", current_domain)

            logger.info("[REDIS_SUBSCRIBER] handle_pending_approval DONE")

        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] pending_approval error: %s", e, exc_info=True)

    async def _handle_task_manager_message(self, uid: str, channel: str, message_data: Dict[str, Any]) -> None:
        """
        Traite un message du canal task_manager.

        Canal: user:{uid}/task_manager
        Niveau: BUSINESS (page-specific)
        Règle: Publier si utilisateur connecté ET sur la page concernée
               + Notifier le dashboard si l'utilisateur y est (cross-domain)

        Args:
            uid: User ID
            channel: Nom du canal Redis
            message_data: Données du message
        """
        start_time = time.time()

        try:
            task_type = message_data.get("type", "unknown")
            job_id = message_data.get("job_id", "unknown")
            department = message_data.get("department", "unknown")

            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
            logger.info("[REDIS_SUBSCRIBER] handle_task_manager START - uid=%s channel=%s", uid, channel)
            logger.info("[REDIS_SUBSCRIBER] → task_type=%s job_id=%s department=%s", task_type, job_id, department)

            # ── Sanitize Sentinel values from all levels ──
            for k in list(message_data.keys()):
                message_data[k] = self._sanitize_value(message_data[k])
            nested_data = message_data.get("data")
            if isinstance(nested_data, dict):
                for k in list(nested_data.keys()):
                    nested_data[k] = self._sanitize_value(nested_data[k])

            # Extraction contexte
            company_id = message_data.get("collection_id") or message_data.get("company_id")
            domain = _extract_department_to_domain(department)
            logger.debug("[REDIS_SUBSCRIBER] → extracted company_id=%s domain=%s", company_id, domain)

            if not company_id or not domain:
                logger.warning("[REDIS_SUBSCRIBER] → missing company_id or domain, skipping")
                return

            # Normalisation du status (si présent)
            raw_status = message_data.get("status")
            if raw_status:
                # Utiliser le department comme function_name pour les overrides spécifiques
                function_name = department.lower() if department != "unknown" else ""
                normalized_status = StatusNormalizer.normalize_for_function(function_name, raw_status)
                category = StatusNormalizer.get_category(normalized_status)
                message_data["status"] = normalized_status
                message_data["status_category"] = category
                logger.debug("[REDIS_SUBSCRIBER] → status normalized: %s → %s (category=%s)", raw_status, normalized_status, category)

            # Vérification connexion
            is_connected = hub.is_user_connected(uid)
            logger.debug("[REDIS_SUBSCRIBER] → user_connected=%s", is_connected)

            # Étape 1: Mise à jour du cache BUSINESS domain-aware (TOUJOURS)
            # Utilise la structure correcte par domaine (documents/items/transactions)
            # pour que le MetricsCalculator puisse lire les données
            logger.info("[REDIS_SUBSCRIBER] → Step 1: Updating BUSINESS cache (domain-aware)...")
            is_new = task_type in ["task_manager_created"]
            item_data = message_data.get("data", message_data)
            # S'assurer que le status normalisé est dans item_data
            if raw_status and "status" not in item_data:
                item_data["status"] = message_data.get("status", raw_status)

            # Aplatissement domain-specific: le cache initial stocke les champs
            # métier au top-level, mais le delta Redis les a imbriqués dans
            # department_data.{Department}. On les extrait pour cohérence.
            dept_data = item_data.get("department_data", {})
            if isinstance(dept_data, dict) and dept_data:
                if domain == "routing":
                    router_sub = dept_data.get("Router", {}) or dept_data.get("router", {})
                    if isinstance(router_sub, dict) and router_sub.get("selected_service"):
                        item_data["routed_to"] = router_sub["selected_service"]

                elif domain == "expenses":
                    # Aplatir department_data.EXbookeeper → top-level
                    # (cohérent avec _fetch_from_task_manager / _build_cross_domain_item)
                    ex_sub = dept_data.get("EXbookeeper", {}) or dept_data.get("exbookeeper", {})
                    if isinstance(ex_sub, dict) and ex_sub:
                        _EXPENSE_FLAT_KEYS = (
                            "supplier", "amount", "date", "currency",
                            "category", "description", "payment_method",
                        )
                        for k in _EXPENSE_FLAT_KEYS:
                            if k in ex_sub and k not in item_data:
                                item_data[k] = ex_sub[k]
                        # expense_id doit correspondre au job_id pour le matching frontend
                        if "expense_id" not in item_data:
                            item_data["expense_id"] = job_id
                        logger.debug(
                            "[REDIS_SUBSCRIBER] → EXbookeeper flattened keys: %s",
                            [k for k in _EXPENSE_FLAT_KEYS if k in ex_sub],
                        )

                elif domain == "invoices":
                    # Aplatir department_data.APbookeeper → top-level
                    ap_sub = (
                        dept_data.get("APbookeeper", {})
                        or dept_data.get("Apbookeeper", {})
                        or dept_data.get("apbookeeper", {})
                    )
                    if isinstance(ap_sub, dict) and ap_sub:
                        _AP_FLAT_KEYS = (
                            "supplier", "amount", "date", "currency",
                            "current_step", "current_step_technical",
                        )
                        for k in _AP_FLAT_KEYS:
                            if k in ap_sub and k not in item_data:
                                item_data[k] = ap_sub[k]

                elif domain in ("bank", "banking"):
                    # Aplatir department_data.Bankbookeeper → top-level
                    bk_sub = (
                        dept_data.get("Bankbookeeper", {})
                        or dept_data.get("banker", {})
                        or dept_data.get("Banker", {})
                    )
                    if isinstance(bk_sub, dict) and bk_sub:
                        _BANK_FLAT_KEYS = (
                            "step_id", "step_label", "result_code",
                            "reconciliation_details",
                        )
                        for k in _BANK_FLAT_KEYS:
                            if k in bk_sub and k not in item_data:
                                item_data[k] = bk_sub[k]
                        logger.debug(
                            "[REDIS_SUBSCRIBER] → Bankbookeeper flattened keys: %s",
                            [k for k in _BANK_FLAT_KEYS if k in bk_sub],
                        )

            # Aplatir les clés dot-path department_data.Bankbookeeper.* → top-level
            # (venant de update_task_manager_transaction_direct qui utilise des dot-paths)
            if domain in ("bank", "banking"):
                _DOT_PREFIX = "department_data.Bankbookeeper."
                dot_keys_to_flatten = [k for k in item_data if isinstance(k, str) and k.startswith(_DOT_PREFIX)]
                for dk in dot_keys_to_flatten:
                    short_key = dk[len(_DOT_PREFIX):]
                    if short_key and short_key not in item_data:
                        item_data[short_key] = item_data[dk]

            self._update_business_cache_item(
                uid=uid,
                company_id=company_id,
                domain=domain,
                job_id=job_id,
                item_data=item_data,
                is_new=is_new
            )

            # Étape 1-cross: Cross-domain ADD when Router completes a routed item
            # The Router writes department_data.{APbookeeper|EXbookeeper} in Firebase
            # but the Redis notification only says department="Router".
            # We read the Firebase doc to get the target department and ADD to its cache.
            logger.info(
                "[REDIS_SUBSCRIBER] → Step 1-cross CHECK: domain=%s raw_status=%r job_id=%s",
                domain, raw_status, job_id,
            )
            cross_domain_items: List[Tuple[str, dict]] = []
            if domain == "routing" and raw_status and raw_status.lower() in ("completed", "routed"):
                logger.info(
                    "[REDIS_SUBSCRIBER] → Step 1-cross TRIGGERED: cross-domain ADD for job_id=%s",
                    job_id,
                )
                cross_domain_items = await self._cross_domain_add_after_routing(
                    uid=uid,
                    company_id=company_id,
                    job_id=job_id,
                    collection_path=message_data.get("collection_path", ""),
                )

            # Étape 1a: Mise à jour active_jobs (suivi par-job)
            # Non-bloquant: met à jour jobs_status dans le document on_process
            active_job_type = _extract_active_job_type(department)
            if active_job_type and raw_status:
                try:
                    batch_id = (
                        item_data.get("batch_id")
                        or message_data.get("data", {}).get("batch_id")
                    )
                    ActiveJobManager.update_job_status_in_active(
                        job_type=active_job_type,
                        mandate_path=message_data.get("mandate_path", ""),
                        job_id=job_id,
                        new_status=raw_status,
                        batch_id=batch_id,
                    )
                except Exception as active_err:
                    logger.warning(
                        "[REDIS_SUBSCRIBER] → Step 1a: active_jobs update failed: %s",
                        active_err
                    )

            # Étape 1b: Mise à jour du billing_history cache (Historique des dépenses)
            # Merge les champs du payload PubSub dans l'ExpenseItem correspondant
            # Returns the updated item (for delta WS push) or None if not found
            updated_billing_item = self._update_billing_history_cache(
                uid=uid,
                company_id=company_id,
                job_id=job_id,
                message_data=message_data
            )

            # Étape 2: Publication WebSocket si connecté
            if is_connected:
                # Vérification page active
                context = _get_user_context(uid)
                current_company = context.get("company_id")
                current_domain = context.get("current_domain")
                logger.debug("[REDIS_SUBSCRIBER] → user_context: company=%s domain=%s", current_company, current_domain)

                if current_company == company_id and current_domain == domain:
                    # Utilisateur sur la page du domaine → envoyer l'event spécifique
                    logger.info("[REDIS_SUBSCRIBER] → Step 2: Publishing via WebSocket (user on page)...")
                    try:
                        event_type = f"{domain}.task_manager_update"
                        payload = {"action": "update", "data": message_data}

                        published = await publish_business_event(
                            uid=uid,
                            company_id=company_id,
                            domain=domain,
                            event_type=event_type,
                            payload=payload
                        )

                        if published:
                            logger.info("[REDIS_SUBSCRIBER] → published event_type=%s", event_type)
                            logger.info("[REDIS_SUBSCRIBER] handle_task_manager SUCCESS - published to user on page")
                        else:
                            logger.warning("[REDIS_SUBSCRIBER] → publish_failed (context changed during processing)")

                    except Exception as publish_error:
                        logger.error("[REDIS_SUBSCRIBER] publish_failed uid=%s error=%s", uid, str(publish_error), exc_info=True)

                elif current_company == company_id and domain == "onboarding" and current_domain == "chat":
                    # Forwarding spécial: onboarding task_manager → chat page (carte onboarding)
                    logger.info("[REDIS_SUBSCRIBER] → Forwarding onboarding update to chat page...")
                    try:
                        tm_status = (message_data.get("status") or "").lower()
                        STATUS_MAP = {
                            "in_queue": "queued", "processing": "running", "on_process": "running",
                            "running": "running", "completed": "completed", "failed": "error",
                            "stopped": "idle", "stopping": "stopping", "error": "error",
                        }
                        mapped = STATUS_MAP.get(tm_status, tm_status)
                        await hub.broadcast(uid, {
                            "type": WS_EVENTS.CHAT.ONBOARDING_JOB_STATUS,
                            "payload": {"job_id": job_id, "status": mapped}
                        })
                        logger.info("[REDIS_SUBSCRIBER] → published chat.onboarding_job_status status=%s", mapped)
                    except Exception as fwd_err:
                        logger.error("[REDIS_SUBSCRIBER] onboarding_forward error: %s", fwd_err)

                elif current_company == company_id and current_domain == "dashboard":
                    # Utilisateur sur le DASHBOARD → envoyer:
                    # 1. BILLING_ITEM_UPDATE delta (juste l'item changé, ~1KB vs ~500KB)
                    # 2. METRICS_UPDATE (compteurs seulement, pas de billing_history)
                    logger.info("[REDIS_SUBSCRIBER] → Step 2: User on DASHBOARD - publishing delta + metrics...")
                    try:
                        # 2a. Delta billing item update (si l'item existait dans le cache)
                        if updated_billing_item:
                            delta_published = await publish_dashboard_event(
                                uid=uid,
                                company_id=company_id,
                                event_type=WS_EVENTS.DASHBOARD.BILLING_ITEM_UPDATE,
                                payload={
                                    "action": "update",
                                    "item": updated_billing_item,
                                    "job_id": job_id,
                                    "domain": domain,
                                }
                            )
                            if delta_published:
                                logger.info("[REDIS_SUBSCRIBER] → published BILLING_ITEM_UPDATE delta for job_id=%s", job_id)
                            else:
                                logger.warning("[REDIS_SUBSCRIBER] → BILLING_ITEM_UPDATE publish_failed")

                        # 2b. Metrics update (compteurs seulement, sans billing_history)
                        published = await publish_metrics_update(
                            uid=uid,
                            company_id=company_id
                        )

                        if published:
                            logger.info("[REDIS_SUBSCRIBER] → published dashboard.metrics_update for domain=%s", domain)
                            logger.info("[REDIS_SUBSCRIBER] handle_task_manager SUCCESS - delta+metrics published to dashboard")
                        else:
                            logger.warning("[REDIS_SUBSCRIBER] → metrics publish_failed (user may have left dashboard)")

                    except Exception as publish_error:
                        logger.error("[REDIS_SUBSCRIBER] metrics_publish_failed uid=%s error=%s", uid, str(publish_error), exc_info=True)

                else:
                    logger.info("[REDIS_SUBSCRIBER] → Step 2: Skipping WebSocket publish (user on different page)")
                    logger.debug("[REDIS_SUBSCRIBER] → context mismatch: company (current=%s target=%s) domain (current=%s target=%s)",
                                 current_company, company_id, current_domain, domain)
                    logger.info("[REDIS_SUBSCRIBER] handle_task_manager SUCCESS - cache updated, no WS publish")

            else:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Skipping WebSocket publish (user not connected)")
                logger.info("[REDIS_SUBSCRIBER] handle_task_manager SUCCESS - cache updated, no WS publish")

            # Étape 2-cross: Publier WSS events pour les domaines cibles du cross-domain
            # (ex: Router→Expenses, Router→Invoices).  Le cache a été mis à jour en Step 1-cross;
            # il faut aussi pousser le delta WSS si l'utilisateur est sur la page cible.
            if cross_domain_items and is_connected:
                context = _get_user_context(uid)
                current_company = context.get("company_id")
                current_domain = context.get("current_domain")
                for target_domain, flat_item in cross_domain_items:
                    if current_company == company_id and current_domain == target_domain:
                        try:
                            item_status = (flat_item.get("status") or "to_process").lower()
                            cross_payload = {
                                "action": "update",
                                "data": {
                                    "type": "task_manager_created",
                                    "job_id": job_id,
                                    "department": department,
                                    "collection_id": company_id,
                                    "status": item_status,
                                    "status_category": StatusNormalizer.get_category(item_status),
                                    **flat_item,
                                },
                            }
                            cross_published = await publish_business_event(
                                uid=uid,
                                company_id=company_id,
                                domain=target_domain,
                                event_type=f"{target_domain}.task_manager_update",
                                payload=cross_payload,
                            )
                            if cross_published:
                                logger.info(
                                    "[REDIS_SUBSCRIBER] → Step 2-cross: published %s.task_manager_update for job_id=%s",
                                    target_domain, job_id,
                                )
                        except Exception as cross_err:
                            logger.warning(
                                "[REDIS_SUBSCRIBER] → Step 2-cross: publish failed domain=%s error=%s",
                                target_domain, cross_err,
                            )

            duration_ms = (time.time() - start_time) * 1000
            logger.debug("[REDIS_SUBSCRIBER] → duration_ms=%.2f", duration_ms)
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except KeyError as e:
            logger.error("[REDIS_SUBSCRIBER] missing_field channel=%s uid=%s field=%s", channel, uid, str(e), exc_info=True)
        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] unexpected_error channel=%s uid=%s error=%s", channel, uid, str(e), exc_info=True)


    def _resolve_llm_thread_key(
        self,
        uid: str,
        thread_key: str,
        department: str,
        communication_chat_type: str = "pinnokio",
    ) -> str:
        """
        Resolve the correct Worker LLM thread_key based on active communication mode.

        In Pinnokio mode: thread_key = job_id (unchanged).
        In Telegram mode: thread_key = tg_{abs(chat_id)}_{module}, stored
        by CommunicationResponseCollector when a Telegram session is created.

        The mapping Redis key: comm_thread:{uid}:{module} → tg_xxx_module
        """
        # En mode pinnokio, le thread_key est toujours le job_id — pas de résolution
        if communication_chat_type == "pinnokio":
            return thread_key

        module = self._DEPARTMENT_TO_MODULE.get(department)
        if not module:
            return thread_key

        try:
            mapping_key = f"comm_thread:{uid}:{module}"
            resolved = self.redis.get(mapping_key)
            if resolved:
                logger.info(
                    "[REDIS_SUBSCRIBER] llm_thread_key resolved: %s → %s (dept=%s module=%s)",
                    thread_key, resolved, department, module,
                )
                return resolved
        except Exception as e:
            logger.debug("[REDIS_SUBSCRIBER] llm_thread_key resolution failed: %s", e)

        return thread_key

    async def _handle_job_chat_message(
        self,
        uid: str,
        channel: str,
        message_data: Dict[str, Any]
    ) -> None:
        """
        Traite un message du canal job_chats.

        Canal: user:{uid}/{collection}/job_chats/{job_id}/messages

        Raccourci CMMD: Les messages de type CMMD (SET_WORKFLOW_CHECKLIST, UPDATE_STEP_STATUS)
        sont broadcastés directement au frontend via WebSocket sans passer par le Worker LLM.
        La persistance RTDB est déjà assurée côté klk_router.

        Messages MESSAGE: Routés vers llm_manager pour traitement métier (injection LLM).

        Args:
            uid: User ID
            channel: Nom du canal Redis
            message_data: Données du message (format: {type, job_id, collection_name, thread_key, message})
        """
        start_time = time.time()

        try:
            # Extraire les informations depuis le payload
            job_id = message_data.get("job_id")
            collection_name = message_data.get("collection_name")
            thread_key = message_data.get("thread_key")
            message = message_data.get("message", message_data)

            if not job_id or not collection_name:
                logger.warning(
                    "[REDIS_SUBSCRIBER] job_chat_message_invalid channel=%s uid=%s "
                    "missing=job_id_or_collection",
                    channel, uid
                )
                return

            if not thread_key:
                thread_key = job_id

            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
            logger.info(
                "[REDIS_SUBSCRIBER] handle_job_chat START - uid=%s collection=%s job_id=%s thread=%s",
                uid, collection_name, job_id, thread_key
            )

            # ── Raccourci CMMD : broadcast direct au frontend ──
            message_type = message.get("message_type") if isinstance(message, dict) else None

            if message_type == "CMMD":
                logger.info(
                    "[JOB_CHAT] CMMD detected - bypassing Worker LLM, broadcasting direct to WS"
                )

                # Vérifier si le user a ce thread ouvert
                from app.llm_service.session_state_manager import get_session_state_manager
                state_manager = get_session_state_manager()
                user_on_thread = state_manager.is_user_on_thread_multi_tab(
                    user_id=uid,
                    company_id=collection_name,
                    thread_key=thread_key
                )

                if user_on_thread:
                    await hub.broadcast(uid, {
                        "type": "CMMD",
                        "payload": {
                            "content": message.get("content"),
                            "message_type": "CMMD",
                            "thread_key": thread_key,
                            "job_id": job_id,
                            "collection_name": collection_name,
                        }
                    })
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(
                        "[JOB_CHAT] CMMD broadcast direct → user on thread=True "
                        "uid=%s job_id=%s duration_ms=%.2f",
                        uid, job_id, duration_ms
                    )
                else:
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(
                        "[JOB_CHAT] CMMD skipped (user not on thread) uid=%s thread=%s duration_ms=%.2f",
                        uid, thread_key, duration_ms
                    )

                logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
                return

            # ── Raccourci FOLLOW_CARD / CARD : broadcast direct au frontend ──
            # Même pattern que CMMD : les cartes interactives des workers externes
            # (klk_router, klk_bank, klk_accountant) n'ont pas besoin du Worker LLM
            # pour être affichées. On enrichit avec card_data et on broadcast direct.
            if message_type in ("FOLLOW_CARD", "CARD"):
                logger.info(
                    "[JOB_CHAT] %s detected - bypassing Worker LLM, broadcasting direct to WS",
                    message_type
                )

                # Extraire card_data depuis le contenu cardsV2
                from app.realtime.card_transformer import CardTransformer
                card_data = CardTransformer.cardsv2_to_interactive_card(message, thread_key)

                from app.llm_service.session_state_manager import get_session_state_manager
                state_manager = get_session_state_manager()
                user_on_thread = state_manager.is_user_on_thread_multi_tab(
                    user_id=uid,
                    company_id=collection_name,
                    thread_key=thread_key
                )

                if user_on_thread:
                    widget_count = len(card_data.get("widgets", [])) if card_data else 0
                    logger.info(
                        "[JOB_CHAT] card_data debug: cardId=%s widgets=%d keys=%s",
                        card_data.get("cardId") if card_data else None,
                        widget_count,
                        list(card_data.keys()) if card_data else []
                    )
                    await hub.broadcast(uid, {
                        "type": "CARD",
                        "payload": {
                            "content": message.get("content"),
                            "message_type": "CARD",
                            "thread_key": thread_key,
                            "job_id": job_id,
                            "collection_name": collection_name,
                            "card_data": card_data,
                            "message_id": message.get("id") or message.get("message_id"),
                            "timestamp": message.get("timestamp"),
                        }
                    })
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(
                        "[JOB_CHAT] %s broadcast direct → user on thread=True "
                        "uid=%s job_id=%s card=%s widgets=%d duration_ms=%.2f",
                        message_type, uid, job_id,
                        card_data.get("cardId", "unknown") if card_data else "no_card_data",
                        widget_count,
                        duration_ms
                    )
                else:
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(
                        "[JOB_CHAT] %s — user not on thread, card stored in RTDB for reload "
                        "uid=%s thread=%s duration_ms=%.2f",
                        message_type, uid, thread_key, duration_ms
                    )

                # Dispatch vers canaux externes (google_chat, telegram) si mandate_path present
                # communication_chat_type et department viennent du payload du worker externe
                mandate_path = message_data.get("mandate_path")
                comm_chat_type = message_data.get("communication_chat_type", "pinnokio")
                dept = message_data.get("department", "Router")
                if mandate_path and comm_chat_type != "pinnokio":
                    from app.realtime.communication_dispatcher import get_communication_dispatcher
                    dispatcher = get_communication_dispatcher()
                    asyncio.create_task(
                        dispatcher.dispatch_message(
                            uid, mandate_path, collection_name,
                            thread_key, message,
                            department=dept,
                            message_mode="job_chats",
                            communication_chat_type=comm_chat_type,
                        )
                    )

                # --- Messenger notification (only for pinnokio channel) ---
                if comm_chat_type == "pinnokio" and card_data:
                    asyncio.create_task(
                        self._create_messenger_notif(
                            uid, collection_name, thread_key, card_data, message
                        )
                    )

                # Forward to Worker LLM so the agent knows a card arrived
                # Resolve thread_key for multi-canal (Telegram uses tg_ prefix)
                llm_thread_key = self._resolve_llm_thread_key(uid, thread_key, dept, comm_chat_type)
                try:
                    from app.llm_service.llm_gateway import get_llm_gateway
                    gateway = get_llm_gateway()
                    await gateway.enqueue_job_chat_message(
                        user_id=uid,
                        collection_name=collection_name,
                        thread_key=llm_thread_key,
                        job_id=job_id,
                        message=message
                    )
                    logger.info(
                        "[JOB_CHAT] %s forwarded to Worker LLM - uid=%s job_id=%s llm_thread=%s",
                        message_type, uid, job_id, llm_thread_key
                    )
                except Exception as e:
                    logger.warning(
                        "[JOB_CHAT] Failed to enqueue %s to Worker LLM: %s",
                        message_type, e
                    )

                logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
                return

            # ── MESSAGE ou autre : déléguer au Worker LLM via LLMGateway ──
            # Resolve thread_key for multi-canal (Telegram uses tg_ prefix)
            msg_dept = (message.get("department") or message_data.get("department", "Router"))
            msg_comm_type = (message.get("communication_chat_type")
                             or message_data.get("communication_chat_type", "pinnokio"))
            llm_thread_key = self._resolve_llm_thread_key(uid, thread_key, msg_dept, msg_comm_type)

            from app.llm_service.llm_gateway import get_llm_gateway

            gateway = get_llm_gateway()
            result = await gateway.enqueue_job_chat_message(
                user_id=uid,
                collection_name=collection_name,
                thread_key=llm_thread_key,
                job_id=job_id,
                message=message
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "[REDIS_SUBSCRIBER] handle_job_chat ENQUEUED - uid=%s collection=%s job_id=%s "
                "queue_job_id=%s duration_ms=%.2f",
                uid, collection_name, job_id, result.get("job_id", "unknown")[:8], duration_ms
            )
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except Exception as e:
            self._error_count += 1
            logger.error(
                "[REDIS_SUBSCRIBER] job_chat_handler_error channel=%s uid=%s error=%s",
                channel, uid, str(e), exc_info=True
            )

    async def _create_messenger_notif(
        self,
        uid: str,
        collection_name: str,
        thread_key: str,
        card_data: Dict[str, Any],
        message: Dict[str, Any],
    ) -> None:
        """
        Create a messenger notification (direct_message_notif) when a CARD/FOLLOW_CARD
        is received from an external worker. Writes to RTDB and publishes via Redis PubSub.

        Args:
            uid: Firebase user ID
            collection_name: Company/collection ID
            thread_key: Job chat thread key
            card_data: Extracted interactive card data (from CardTransformer)
            message: Original message dict from the worker
        """
        try:
            card_id = card_data.get("cardId", "")
            function_name = CARD_ID_TO_DEPARTMENT.get(card_id, "Chat")

            # Build human-readable message (never raw card JSON)
            DEPARTMENT_MESSAGES = {
                "Router": "Approval required for document routing",
                "APbookeeper": "Approval required for invoice processing",
                "Bankbookeeper": "Action required on bank transaction",
            }
            human_message = DEPARTMENT_MESSAGES.get(function_name, "Action required")

            notif_data = {
                "job_id": thread_key,
                "function_name": function_name,
                "collection_id": collection_name,
                "collection_name": collection_name,
                "status": "Action required",
                "message": human_message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            }

            # Enrich from task_manager cache if available
            try:
                from app.llm_service.redis_namespaces import build_business_key
                cache_key = build_business_key(uid, collection_name, "task_manager")
                cached = self.redis.get(cache_key)
                if cached:
                    import json as _json
                    cache_items = _json.loads(cached) if isinstance(cached, (str, bytes)) else cached
                    if isinstance(cache_items, list):
                        for item in cache_items:
                            if isinstance(item, dict) and item.get("job_id") == thread_key:
                                notif_data["file_name"] = item.get("file_name", "")
                                notif_data["file_id"] = item.get("file_id", "")
                                notif_data["mandate_path"] = item.get("mandate_path", "")
                                # Enrich message with file name if available
                                file_name = item.get("file_name", "")
                                if file_name:
                                    notif_data["message"] = f"{human_message}: {file_name}"
                                break
            except Exception:
                pass  # Cache enrichment is best-effort

            # Write to RTDB via FirebaseRealtimeChat (send_direct_message is on this class)
            from app.firebase_providers import get_firebase_realtime
            realtime = get_firebase_realtime()
            notif_message_id = realtime.send_direct_message(uid, uid, notif_data)

            if notif_message_id:
                # Store tracking in Redis with 2h TTL
                tracking_key = f"messenger_notif:{uid}:{thread_key}"
                self.redis.set(tracking_key, notif_message_id, ex=7200)

                logger.info(
                    "[MESSENGER_NOTIF] Created notif uid=%s thread=%s card=%s dept=%s notif_id=%s",
                    uid, thread_key, card_id, function_name, notif_message_id
                )
            else:
                logger.warning(
                    "[MESSENGER_NOTIF] Failed to create notif uid=%s thread=%s",
                    uid, thread_key
                )

        except Exception as e:
            logger.error(
                "[MESSENGER_NOTIF] Error creating notif uid=%s thread=%s error=%s",
                uid, thread_key, str(e), exc_info=True
            )


# ============================================
# Singleton Instance
# ============================================

_redis_subscriber_instance: Optional[RedisSubscriber] = None


def get_redis_subscriber() -> RedisSubscriber:
    """
    Retourne l'instance singleton du RedisSubscriber.

    Returns:
        Instance du RedisSubscriber
    """
    global _redis_subscriber_instance
    if _redis_subscriber_instance is None:
        _redis_subscriber_instance = RedisSubscriber()
    return _redis_subscriber_instance
