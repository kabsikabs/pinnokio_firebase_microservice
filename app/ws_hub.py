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

    async def register(self, uid: str, ws: WebSocket) -> None:
        async with self._lock:
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
        async with self._lock:
            conns = list(self._uid_to_conns.get(uid, set()))
        for ws in conns:
            try:
                await ws.send_text(data)
            except Exception as e:
                self._logger.error("ws_send_error uid=%s error=%s", uid, repr(e))


hub = WebSocketHub()
