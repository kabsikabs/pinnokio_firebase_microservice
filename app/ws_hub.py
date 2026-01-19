import asyncio
import json
import logging
from typing import Dict, Set

from starlette.websockets import WebSocket

# Event type normalization mapping (legacy underscore → standard dot notation)
# This ensures compatibility between old backend event names and new frontend expectations
EVENT_TYPE_NORMALIZATION = {
    # LLM streaming events
    "llm_stream_start": "llm.stream_start",
    "llm_stream_chunk": "llm.stream_delta",  # chunk → delta
    "llm_stream_delta": "llm.stream_delta",
    "llm_stream_complete": "llm.stream_end",  # complete → end
    "llm_stream_end": "llm.stream_end",
    "llm_stream_error": "llm.error",
    "llm_stream_interrupted": "llm.error",
    # Tool use events
    "tool_use_start": "llm.tool_use_start",
    "tool_use_progress": "llm.tool_use_progress",
    "tool_use_complete": "llm.tool_use_complete",
    "tool_use_error": "llm.tool_use_error",
}


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
        # Normalize event type for frontend compatibility
        original_type = message.get("type", "unknown")
        normalized_type = EVENT_TYPE_NORMALIZATION.get(original_type, original_type)

        # Update message with normalized type if different
        if normalized_type != original_type:
            message = {**message, "type": normalized_type}

        # Envoie le message JSON (texte) à toutes les connexions pour ce uid
        data = json.dumps(message)
        msg_type = normalized_type
        channel = message.get("channel", "")
        
        async with self._lock:
            conns = list(self._uid_to_conns.get(uid, set()))
        
        if not conns:
            # ⭐ NOUVEAU: Buffer automatique si pas de connexion active
            # Extraction du thread_key depuis le channel (format: "chat:{thread_key}")
            thread_key = None
            if channel and ":" in channel:
                try:
                    thread_key = channel.split(":", 1)[1]
                except Exception:
                    pass
            
            if thread_key:
                # Buffering du message dans Redis pour replay après reconnexion
                try:
                    from .ws_message_buffer import get_message_buffer
                    buffer = get_message_buffer()
                    buffer.store_pending_message(uid, thread_key, message)
                    self._logger.info(
                        "ws_broadcast_buffered uid=%s thread=%s type=%s (no_active_connection)", 
                        uid, thread_key, msg_type
                    )
                except Exception as buffer_error:
                    self._logger.error(
                        "ws_broadcast_buffer_failed uid=%s thread=%s error=%s",
                        uid, thread_key, repr(buffer_error)
                    )
            else:
                # Pas de thread_key identifiable → log debug uniquement
                self._logger.debug(
                    "ws_broadcast_no_connections uid=%s type=%s channel=%s (no_thread_key)", 
                    uid, msg_type, channel
                )
            return
        
        sent_count = 0
        for ws in conns:
            try:
                await ws.send_text(data)
                sent_count += 1
            except Exception as e:
                self._logger.error("ws_send_error uid=%s error=%s", uid, repr(e))
        
        # Logs de broadcast (sauf chunks streaming pour éviter verbosité)
        if msg_type == "llm.stream_delta" or original_type == "llm_stream_chunk":
            # Logs de chunks en DEBUG uniquement pour éviter verbosité
            chunk_len = len(message.get("payload", {}).get("chunk", "") or message.get("payload", {}).get("delta", ""))
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
