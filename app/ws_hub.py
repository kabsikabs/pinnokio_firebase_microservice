import asyncio
import json
import logging
from typing import Dict, Set

from starlette.websockets import WebSocket


class WebSocketHub:
    def __init__(self) -> None:
        self._logger = logging.getLogger("listeners.ws")
        self._uid_to_conns: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def register(self, uid: str, ws: WebSocket) -> None:
        async with self._lock:
            # Mémoriser la loop courante pour exécutions thread-safe
            try:
                self._loop = asyncio.get_running_loop()
            except Exception:
                pass
            self._uid_to_conns.setdefault(uid, set()).add(ws)
            self._logger.info("ws_connect uid=%s total=%s", uid, len(self._uid_to_conns[uid]))

    async def unregister(self, uid: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._uid_to_conns.get(uid)
            if conns and ws in conns:
                conns.remove(ws)
                self._logger.info("ws_disconnect uid=%s total=%s", uid, len(conns))
                if not conns:
                    self._uid_to_conns.pop(uid, None)

    async def broadcast(self, uid: str, message: dict) -> None:
        # Envoie le message JSON (texte) à toutes les connexions pour ce uid
        data = json.dumps(message)
        msg_type = message.get("type", "unknown")
        channel = message.get("channel", "")
        
        async with self._lock:
            conns = list(self._uid_to_conns.get(uid, set()))
        
        if not conns:
            # DEBUG au lieu de WARNING : c'est normal si le client n'est pas connecté
            # (ex: page fermée, déconnexion, ou pas encore connecté)
            self._logger.debug("ws_broadcast_no_connections uid=%s type=%s channel=%s", uid, msg_type, channel)
            return
        
        sent_count = 0
        for ws in conns:
            try:
                await ws.send_text(data)
                sent_count += 1
            except Exception as e:
                self._logger.error("ws_send_error uid=%s error=%s", uid, repr(e))
        
        # Logs de broadcast (sauf chunks streaming pour éviter verbosité)
        if msg_type == "llm_stream_chunk":
            # Logs de chunks en DEBUG uniquement pour éviter verbosité
            chunk_len = len(message.get("payload", {}).get("chunk", ""))
            self._logger.debug("ws_broadcast_chunk uid=%s chunk_len=%s connections=%s sent=%s", uid, chunk_len, len(conns), sent_count)
        else:
            self._logger.info("ws_broadcast uid=%s type=%s channel=%s connections=%s", uid, msg_type, channel, sent_count)

    def broadcast_threadsafe(self, uid: str, message: dict) -> None:
        """Déclenche un broadcast depuis un thread quelconque via la loop serveur."""
        loop = self._loop
        if loop is None:
            # Pas de loop connue; on ne peut pas diffuser côté WS
            self._logger.error("ws_broadcast_threadsafe_no_loop uid=%s", uid)
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self.broadcast(uid, message), loop)
            # Optionnel: ignorer résultat, mais capture des exceptions éventuelles
            fut.add_done_callback(lambda f: f.exception())
        except Exception as e:
            self._logger.error("ws_broadcast_threadsafe_error uid=%s error=%s", uid, repr(e))


hub = WebSocketHub()
