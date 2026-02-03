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
- user:{uid}/{collection}/job_chats/{job_id}/messages → Job Chat (RTDB) - Routé vers llm_manager
- user:{uid}/{space_code}/*/messages → Chat - DÉJÀ GÉRÉ PAR llm_manager (IGNORÉ)

RÈGLES DE PUBLICATION:
- USER (notifications, direct_message_notif): Publier si utilisateur connecté uniquement
- BUSINESS (task_manager): Publier si utilisateur connecté ET sur la page concernée
- Job Chat (job_chats): Routé vers llm_manager._handle_onboarding_log_event() pour traitement métier
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
from typing import Any, Dict, Optional, Callable
from redis.client import PubSub

from app.redis_client import get_redis
from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.realtime.contextual_publisher import (
    publish_user_event,
    publish_business_event,
    CacheLevel,
    _get_user_context,
    _update_cache,
    _get_cache_key,
    _get_ttl_for_cache,
)

logger = logging.getLogger(__name__)

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
        "banking": "bank",
        "routing": "routing",
        "hr": "hr",
        "expenses": "expenses",
        "coa": "coa",
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

    async def _handle_notification_message(self, uid: str, channel: str, message_data: Dict[str, Any]) -> None:
        """
        Traite un message du canal notifications.

        Canal: user:{uid}/notifications
        Niveau: USER (global)
        Règle: Publier si utilisateur connecté uniquement

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

            # Vérification connexion
            is_connected = hub.is_user_connected(uid)
            logger.debug("[REDIS_SUBSCRIBER] → user_connected=%s", is_connected)

            # Étape 1: Mise à jour du cache USER (TOUJOURS)
            logger.info("[REDIS_SUBSCRIBER] → Step 1: Updating USER cache...")
            try:
                cache_key = _get_cache_key(CacheLevel.USER, uid, cache_subkey="notifications")
                ttl = _get_ttl_for_cache(CacheLevel.USER)

                # Préparer le payload pour le cache
                cache_payload = {
                    "action": "new" if msg_type in ["notification_update", "job_created"] else "update",
                    "data": message_data
                }
                _update_cache(CacheLevel.USER, cache_key, cache_payload, ttl)
                logger.debug("[REDIS_SUBSCRIBER] → cache_key=%s", cache_key)

            except Exception as cache_error:
                logger.error("[REDIS_SUBSCRIBER] cache_update_failed uid=%s error=%s", uid, str(cache_error), exc_info=True)

            # Étape 2: Publication WebSocket si connecté
            if is_connected:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Publishing via WebSocket...")
                try:
                    event_type = WS_EVENTS.NOTIFICATION.DELTA
                    payload = {"action": "new", "data": message_data}

                    # Publier via contextual_publisher (niveau USER)
                    published = await publish_user_event(
                        uid=uid,
                        event_type=event_type,
                        payload=payload,
                        cache_subkey="notifications"
                    )

                    if published:
                        logger.info("[REDIS_SUBSCRIBER] → published event_type=%s", event_type)
                        logger.info("[REDIS_SUBSCRIBER] handle_notification SUCCESS - published to connected user")
                    else:
                        logger.warning("[REDIS_SUBSCRIBER] → publish_failed (user disconnected during processing)")

                except Exception as publish_error:
                    logger.error("[REDIS_SUBSCRIBER] publish_failed uid=%s error=%s", uid, str(publish_error), exc_info=True)

            else:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Skipping WebSocket publish (user not connected)")
                logger.info("[REDIS_SUBSCRIBER] handle_notification SUCCESS - cache updated, no WS publish")

            duration_ms = (time.time() - start_time) * 1000
            logger.debug("[REDIS_SUBSCRIBER] → duration_ms=%.2f", duration_ms)
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except KeyError as e:
            logger.error("[REDIS_SUBSCRIBER] missing_field channel=%s uid=%s field=%s", channel, uid, str(e), exc_info=True)
        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] unexpected_error channel=%s uid=%s error=%s", channel, uid, str(e), exc_info=True)

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

            # Étape 1: Mise à jour du cache USER (TOUJOURS)
            logger.info("[REDIS_SUBSCRIBER] → Step 1: Updating USER cache (messages)...")
            try:
                cache_key = _get_cache_key(CacheLevel.USER, uid, cache_subkey="messages")
                ttl = _get_ttl_for_cache(CacheLevel.USER)

                # Préparer le payload pour le cache
                cache_payload = {
                    "action": "new",
                    "data": message_data
                }
                _update_cache(CacheLevel.USER, cache_key, cache_payload, ttl)
                logger.debug("[REDIS_SUBSCRIBER] → cache_key=%s", cache_key)

            except Exception as cache_error:
                logger.error("[REDIS_SUBSCRIBER] cache_update_failed uid=%s error=%s", uid, str(cache_error), exc_info=True)

            # Étape 2: Publication WebSocket si connecté
            if is_connected:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Publishing HIGH PRIORITY message via WebSocket...")
                try:
                    event_type = WS_EVENTS.MESSENGER.DELTA
                    payload = {"action": "new", "data": message_data}

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

    async def _handle_task_manager_message(self, uid: str, channel: str, message_data: Dict[str, Any]) -> None:
        """
        Traite un message du canal task_manager.

        Canal: user:{uid}/task_manager
        Niveau: BUSINESS (page-specific)
        Règle: Publier si utilisateur connecté ET sur la page concernée

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

            # Extraction contexte
            company_id = message_data.get("collection_id") or message_data.get("company_id")
            domain = _extract_department_to_domain(department)
            logger.debug("[REDIS_SUBSCRIBER] → extracted company_id=%s domain=%s", company_id, domain)

            if not company_id or not domain:
                logger.warning("[REDIS_SUBSCRIBER] → missing company_id or domain, skipping")
                return

            # Vérification connexion
            is_connected = hub.is_user_connected(uid)
            logger.debug("[REDIS_SUBSCRIBER] → user_connected=%s", is_connected)

            # Étape 1: Mise à jour du cache BUSINESS (TOUJOURS)
            logger.info("[REDIS_SUBSCRIBER] → Step 1: Updating BUSINESS cache...")
            try:
                cache_key = _get_cache_key(CacheLevel.BUSINESS, uid, company_id, domain)
                ttl = _get_ttl_for_cache(CacheLevel.BUSINESS, domain)

                # Préparer le payload pour le cache
                cache_payload = {
                    "action": "new" if task_type in ["task_manager_created"] else "update",
                    "data": message_data
                }
                _update_cache(CacheLevel.BUSINESS, cache_key, cache_payload, ttl)
                logger.debug("[REDIS_SUBSCRIBER] → cache_key=%s", cache_key)

            except Exception as cache_error:
                logger.error("[REDIS_SUBSCRIBER] cache_update_failed uid=%s error=%s", uid, str(cache_error), exc_info=True)

            # Étape 2: Publication WebSocket si connecté ET sur la bonne page
            if is_connected:
                # Vérification page active
                context = _get_user_context(uid)
                current_company = context.get("company_id")
                current_domain = context.get("current_domain")
                logger.debug("[REDIS_SUBSCRIBER] → user_context: company=%s domain=%s", current_company, current_domain)

                if current_company == company_id and current_domain == domain:
                    logger.info("[REDIS_SUBSCRIBER] → Step 2: Publishing via WebSocket (user on page)...")
                    try:
                        # Déterminer le type d'événement selon le domaine
                        event_type = f"{domain}.task_manager_update"

                        payload = {"action": "update", "data": message_data}

                        # Publier via contextual_publisher (niveau BUSINESS)
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

                else:
                    logger.info("[REDIS_SUBSCRIBER] → Step 2: Skipping WebSocket publish (user on different page)")
                    logger.debug("[REDIS_SUBSCRIBER] → context mismatch: company (current=%s target=%s) domain (current=%s target=%s)",
                                 current_company, company_id, current_domain, domain)
                    logger.info("[REDIS_SUBSCRIBER] handle_task_manager SUCCESS - cache updated, no WS publish")

            else:
                logger.info("[REDIS_SUBSCRIBER] → Step 2: Skipping WebSocket publish (user not connected)")
                logger.info("[REDIS_SUBSCRIBER] handle_task_manager SUCCESS - cache updated, no WS publish")

            duration_ms = (time.time() - start_time) * 1000
            logger.debug("[REDIS_SUBSCRIBER] → duration_ms=%.2f", duration_ms)
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except KeyError as e:
            logger.error("[REDIS_SUBSCRIBER] missing_field channel=%s uid=%s field=%s", channel, uid, str(e), exc_info=True)
        except Exception as e:
            logger.error("[REDIS_SUBSCRIBER] unexpected_error channel=%s uid=%s error=%s", channel, uid, str(e), exc_info=True)


    async def _handle_job_chat_message(
        self,
        uid: str,
        channel: str,
        message_data: Dict[str, Any]
    ) -> None:
        """
        Traite un message du canal job_chats.

        Canal: user:{uid}/{collection}/job_chats/{job_id}/messages
        Route vers llm_manager pour traitement métier (injection LLM, mode intermédiation).

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

            # Appeler llm_manager pour traitement métier
            from app.llm_service.llm_manager import get_llm_manager

            llm_manager = get_llm_manager()

            # Récupérer ou créer la session
            session_key = f"{uid}:{collection_name}"
            
            # Vérifier si la session existe
            with llm_manager._lock:
                session = llm_manager.sessions.get(session_key)

            if not session:
                logger.warning(
                    "[REDIS_SUBSCRIBER] session_not_found uid=%s collection=%s thread=%s "
                    "message will be processed when session is created",
                    uid, collection_name, thread_key
                )
                # On peut quand même essayer de créer une session minimale si nécessaire
                # Pour l'instant, on log juste l'avertissement
                logger.info("[REDIS_SUBSCRIBER] handle_job_chat SKIPPED - session not found")
                logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
                return

            # Vérifier si le thread a un listener onboarding actif
            listener_info = session.onboarding_listeners.get(thread_key)
            if not listener_info:
                logger.debug(
                    "[REDIS_SUBSCRIBER] onboarding_listener_not_found uid=%s collection=%s thread=%s "
                    "message will be ignored (listener not started)",
                    uid, collection_name, thread_key
                )
                logger.info("[REDIS_SUBSCRIBER] handle_job_chat SKIPPED - listener not active")
                logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
                return

            # Récupérer le brain depuis la session
            brain = getattr(session, 'brain', None)
            if not brain:
                logger.warning(
                    "[REDIS_SUBSCRIBER] brain_not_found uid=%s collection=%s thread=%s",
                    uid, collection_name, thread_key
                )
                logger.info("[REDIS_SUBSCRIBER] handle_job_chat SKIPPED - brain not found")
                logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")
                return

            # Appeler le handler existant (logique métier inchangée)
            follow_thread_key = f"follow_{job_id}"
            
            logger.info(
                "[REDIS_SUBSCRIBER] → routing to llm_manager._handle_onboarding_log_event()"
            )
            
            await llm_manager._handle_onboarding_log_event(
                session=session,
                brain=brain,
                collection_name=collection_name,
                thread_key=thread_key,
                follow_thread_key=follow_thread_key,
                message=message
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "[REDIS_SUBSCRIBER] handle_job_chat SUCCESS - processed uid=%s collection=%s job_id=%s duration_ms=%.2f",
                uid, collection_name, job_id, duration_ms
            )
            logger.info("[REDIS_SUBSCRIBER] ═══════════════════════════════════════════════════════")

        except Exception as e:
            self._error_count += 1
            logger.error(
                "[REDIS_SUBSCRIBER] job_chat_handler_error channel=%s uid=%s error=%s",
                channel, uid, str(e), exc_info=True
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
