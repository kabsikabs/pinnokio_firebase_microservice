"""
Consommateur de bus pour alimenter les queues par utilisateur.

Transports supportés (via env LISTENERS_TRANSPORT):
- WS (par défaut): WebSocket vers le microservice (ex: http://localhost:8090/ws)
- SSE (réservé évolutions): Server-Sent Events (non implémenté ici)
- REDIS: Pub/Sub Redis/Valkey (optionnel)

En modes LOCAL/PROD, on remplace les écoutes Firebase par ce consommateur,
qui reçoit les événements et pousse dans la queue du `ListenerManager`
des payloads compatibles avec l'existant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Callable, Dict, Any, Optional, List


class BusConsumer:
    """Consommateur Pub/Sub (Redis/Valkey) pour un utilisateur donné."""

    def __init__(
        self,
        user_id: str,
        output_queue: asyncio.Queue,
        kinds: List[str],
        filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
        # Contexte chat optionnel
        space_code: Optional[str] = None,
        thread_key: Optional[str] = None,
    ) -> None:
        self.user_id = user_id
        self.output_queue = output_queue
        self.kinds = kinds
        self.filter_fn = filter_fn
        self.logger = logging.getLogger(f"bus_consumer.{user_id}")
        self.task: Optional[asyncio.Task] = None
        self._running = False
        self._redis = None
        self._pubsub = None
        self.space_code = space_code
        self.thread_key = thread_key

    async def start(self) -> None:
        if self._running:
            self.logger.warning("BusConsumer déjà démarré")
            return
        self._running = True
        self.task = asyncio.create_task(self._run())
        self.logger.info("BusConsumer démarré")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        # Fermer pubsub/redis si ouverts
        try:
            if self._pubsub is not None:
                await self._pubsub.unsubscribe()
                await self._pubsub.close()
        except Exception:
            pass
        try:
            if self._redis is not None:
                await self._redis.close()
        except Exception:
            pass
        self.logger.info("BusConsumer arrêté")

    async def _run(self) -> None:
        try:
            transport = os.getenv("LISTENERS_TRANSPORT", "WS_REDIS").upper().strip()
            if transport in ("WS_REDIS", "COMBINED"):
                await self._run_combined()
            elif transport == "REDIS":
                await self._run_redis()
            elif transport == "WS":
                await self._run_ws()
            else:
                self.logger.error(f"Transport inconnu: {transport}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Erreur BusConsumer: {e}")

    def _map_listener_name(self, evt_type: str) -> str:
        if evt_type.startswith("notif"):
            return "notifications"
        if evt_type.startswith("msg"):
            return "messages"
        if evt_type.startswith("chat"):
            return "chat"
        return "general"

    async def _connect_redis(self):
        try:
            # Importer redis.asyncio à la volée pour éviter dépendance dure
            import redis.asyncio as redis  # type: ignore

            host = os.getenv("LISTENERS_REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("LISTENERS_REDIS_PORT", "6379"))
            password = os.getenv("LISTENERS_REDIS_PASSWORD", "") or None
            use_tls = os.getenv("LISTENERS_REDIS_TLS", "false").lower() == "true"
            db = int(os.getenv("LISTENERS_REDIS_DB", "0"))

            if use_tls:
                url = f"rediss://{host}:{port}/{db}"
            else:
                url = f"redis://{host}:{port}/{db}"
            if password:
                # redis-py accepte password via from_url param
                client = redis.from_url(url, password=password, encoding="utf-8", decode_responses=False)
            else:
                client = redis.from_url(url, encoding="utf-8", decode_responses=False)

            # ping rapide
            try:
                await client.ping()
            except Exception as e:
                self.logger.error(f"Connexion Redis échouée: {e}")
                return None
            return client
        except Exception as e:
            self.logger.error(f"redis.asyncio non disponible ou erreur d'init: {e}")
            return None

    async def _run_redis(self) -> None:
        redis = await self._connect_redis()
        if redis is None:
            self.logger.error("Redis non disponible, arrêt du consommateur")
            return
        self._redis = redis

        channel_name = self._resolve_channel_name()

        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_name)
        self._pubsub = pubsub
        self.logger.info(f"Abonné au canal {channel_name}")
        if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
            print(f"[BusConsumer] subscribe channel={channel_name}")

        while self._running:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                evt = self._parse_event_data(data)
                if evt is None:
                    continue
                await self._dispatch_event(evt)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Erreur boucle BusConsumer(REDIS): {e}")
                await asyncio.sleep(0.2)

    def _resolve_channel_name(self) -> str:
        prefix = os.getenv("LISTENERS_CHANNEL_PREFIX", "user:")
        # Canal principal par utilisateur
        base = f"{prefix}{self.user_id}"
        # Si on écoute un chat spécifique, on peut aussi consommer un canal dédié
        if "chat" in self.kinds and self.space_code and self.thread_key:
            chat_prefix = os.getenv("LISTENERS_CHAT_CHANNEL_PREFIX", "chat:")
            return f"{chat_prefix}{self.user_id}:{self.space_code}:{self.thread_key}"
        return base

    async def _run_ws(self) -> None:
        try:
            service_url = os.getenv("LISTENERS_SERVICE_URL", "http://127.0.0.1:8090")
            # Construire ws URL
            if service_url.startswith("http://"):
                ws_url = service_url.replace("http://", "ws://")
            elif service_url.startswith("https://"):
                ws_url = service_url.replace("https://", "wss://")
            else:
                ws_url = service_url
            path = os.getenv("LISTENERS_WS_PATH", "/ws")
            # Construire la query: uid obligatoire; pour chat, ajouter space_code et thread_key
            query = f"uid={self.user_id}"
            if "chat" in self.kinds and self.space_code and self.thread_key:
                query += f"&space_code={self.space_code}&thread_key={self.thread_key}"
            ws_full = f"{ws_url}{path}?{query}"

            # Import websockets
            try:
                import websockets  # type: ignore
            except Exception as e:
                self.logger.error(f"websockets non disponible: {e}")
                return

            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[BusConsumer] WS connect -> {ws_full}")

            async for websocket in websockets.connect(ws_full, ping_interval=20, ping_timeout=20):
                try:
                    if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                        print("[BusConsumer] WS connected")
                    while self._running:
                        raw = await websocket.recv()
                        evt = self._parse_event_data(raw)
                        if evt is None:
                            continue
                        await self._dispatch_event(evt)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"WS loop error: {e}")
                    await asyncio.sleep(0.5)
                finally:
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                if not self._running:
                    break
        except Exception as e:
            self.logger.error(f"Erreur _run_ws: {e}")

    async def _run_ws_heartbeat(self) -> None:
        """Connexion WS uniquement pour créer/maintenir la souscription (heartbeat)."""
        try:
            service_url = os.getenv("LISTENERS_SERVICE_URL", "http://127.0.0.1:8090")
            if service_url.startswith("http://"):
                ws_url = service_url.replace("http://", "ws://")
            elif service_url.startswith("https://"):
                ws_url = service_url.replace("https://", "wss://")
            else:
                ws_url = service_url
            path = os.getenv("LISTENERS_WS_PATH", "/ws")
            # Inclure les paramètres chat pour forcer l'attachement côté serveur
            query = f"uid={self.user_id}"
            if "chat" in self.kinds and self.space_code and self.thread_key:
                query += f"&space_code={self.space_code}&thread_key={self.thread_key}"
            ws_full = f"{ws_url}{path}?{query}"
            try:
                import websockets  # type: ignore
            except Exception as e:
                self.logger.error(f"websockets non disponible: {e}")
                return
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[BusConsumer] WS(heartbeat) connect -> {ws_full}")
            async for websocket in websockets.connect(ws_full, ping_interval=20, ping_timeout=20):
                try:
                    if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                        print("[BusConsumer] WS(heartbeat) connected")
                    while self._running:
                        try:
                            # Lire et ignorer les données; l'objectif est le keepalive
                            await asyncio.wait_for(websocket.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            # Pas de message, le ping/pong maintient la connexion
                            continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"WS(heartbeat) loop error: {e}")
                    await asyncio.sleep(0.5)
                finally:
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                if not self._running:
                    break
        except Exception as e:
            self.logger.error(f"Erreur _run_ws_heartbeat: {e}")

    async def _run_combined(self) -> None:
        """Combine WS (heartbeat/subscribe) et Redis (flux de messages)."""
        hb_task: Optional[asyncio.Task] = None
        try:
            hb_task = asyncio.create_task(self._run_ws_heartbeat())
            await self._run_redis()
        finally:
            if hb_task and not hb_task.done():
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

    def _parse_event_data(self, data: Any) -> Optional[Dict[str, Any]]:
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if isinstance(data, str):
                return json.loads(data)
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    async def _dispatch_event(self, evt: Dict[str, Any]) -> None:
        evt_type = str(evt.get("type", ""))
        if not any(evt_type.startswith(k) for k in self.kinds):
            return
        payload = evt.get("payload", {})
        if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
            try:
                keys = list(payload.keys()) if isinstance(payload, dict) else []
                count = payload.get("count") if isinstance(payload, dict) else None
                ln = len(payload.get("notifications", [])) if isinstance(payload, dict) else None
                print(f"[BusConsumer] dispatch type={evt_type} payload_keys={keys} count={count} len_notifications={ln}")
            except Exception:
                pass
        if self.filter_fn and not self.filter_fn(payload):
            return
        listener_name = self._map_listener_name(evt_type)
        if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
            print(f"[BusConsumer] recv type={evt_type} -> listener={listener_name}")
        await self.output_queue.put((listener_name, payload))



