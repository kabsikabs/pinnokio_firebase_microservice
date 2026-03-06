import asyncio
import json
import logging
import math
from typing import Any, Dict, Set

from starlette.websockets import WebSocket


class SafeJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles non-JSON-compliant values.

    - Converts NaN and Infinity to None (valid JSON null)
    - Handles datetime objects
    - Prevents JSON serialization errors from crashing WebSocket broadcast
    """
    def default(self, obj: Any) -> Any:
        # Handle numpy types if present
        try:
            import numpy as np
            if isinstance(obj, (np.integer, np.floating)):
                if np.isnan(obj) or np.isinf(obj):
                    return None
                return obj.item()
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass

        # Handle datetime
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()

        return super().default(obj)

    def encode(self, obj: Any) -> str:
        """Override encode to handle NaN/Infinity in nested structures."""
        return super().encode(self._sanitize(obj))

    def _sanitize(self, obj: Any) -> Any:
        """Recursively sanitize values, converting NaN/Infinity to None."""
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        elif isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._sanitize(item) for item in obj]
        return obj


def safe_json_dumps(obj: Any) -> str:
    """
    Serialize object to JSON, safely handling NaN and Infinity values.

    Python's float('nan') and float('inf') are not valid JSON.
    This function converts them to null to prevent parse errors on the frontend.
    """
    return json.dumps(obj, cls=SafeJSONEncoder)

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
    # Thinking events (reasoning phase)
    "thinking_start": "llm.thinking_start",
    "thinking_delta": "llm.thinking_delta",
    "thinking_chunk": "llm.thinking_delta",  # chunk → delta
    "thinking_end": "llm.thinking_end",
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

    def is_user_connected(self, uid: str) -> bool:
        """
        Check if user has at least one active WebSocket connection.

        Used by PubSub helpers to determine if notifications/messages
        should be broadcast to the user.

        Args:
            uid: Firebase user ID

        Returns:
            True if user has at least one active connection
        """
        conns = self._uid_to_conns.get(uid)
        return bool(conns and len(conns) > 0)

    def get_connected_users(self) -> Set[str]:
        """
        Get set of all currently connected user IDs.

        Used for periodic sync to determine which users need updates.

        Returns:
            Set of connected user UIDs
        """
        return set(self._uid_to_conns.keys())

    async def broadcast(self, uid: str, message: dict) -> None:
        # Normalize event type for frontend compatibility
        original_type = message.get("type", "unknown")
        normalized_type = EVENT_TYPE_NORMALIZATION.get(original_type, original_type)

        # Update message with normalized type if different
        if normalized_type != original_type:
            message = {**message, "type": normalized_type}

        # Envoie le message JSON (texte) à toutes les connexions pour ce uid
        # Use safe_json_dumps to handle NaN/Infinity values from ERP data
        data = safe_json_dumps(message)
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
        dead_conns = []
        for ws in conns:
            try:
                await ws.send_text(data)
                sent_count += 1
            except Exception as e:
                self._logger.error("ws_send_error uid=%s error=%s", uid, repr(e))
                dead_conns.append(ws)

        # Nettoyer les connexions mortes et bufferiser si plus aucune connexion active
        if dead_conns:
            async with self._lock:
                uid_conns = self._uid_to_conns.get(uid)
                if uid_conns:
                    for dead_ws in dead_conns:
                        uid_conns.discard(dead_ws)
                    if not uid_conns:
                        self._uid_to_conns.pop(uid, None)
            self._logger.warning(
                "ws_dead_connections_cleaned uid=%s removed=%s remaining=%s",
                uid, len(dead_conns), sent_count
            )
            # Si aucun envoi n'a réussi, bufferiser les messages critiques
            if sent_count == 0:
                thread_key = None
                if channel and ":" in channel:
                    try:
                        thread_key = channel.split(":", 1)[1]
                    except Exception:
                        pass
                if thread_key:
                    try:
                        from .ws_message_buffer import get_message_buffer
                        buffer = get_message_buffer()
                        buffer.store_pending_message(uid, thread_key, message)
                        self._logger.info(
                            "ws_broadcast_buffered_after_failure uid=%s thread=%s type=%s",
                            uid, thread_key, msg_type
                        )
                    except Exception as buffer_error:
                        self._logger.error(
                            "ws_broadcast_buffer_failed uid=%s error=%s",
                            uid, repr(buffer_error)
                        )

        # Logs de broadcast (sauf chunks streaming pour éviter verbosité)
        # ⭐ MIGRATION 2026-02-04: Ajout thinking_delta au filtre
        streaming_types = ("llm.stream_delta", "llm.thinking_delta", "llm_stream_chunk")
        if msg_type in streaming_types or original_type in streaming_types:
            # Logs de streaming en DEBUG uniquement pour éviter verbosité
            self._logger.debug("ws_broadcast_streaming uid=%s type=%s connections=%s", uid, msg_type, len(conns))
        else:
            self._logger.info("ws_broadcast uid=%s type=%s channel=%s connections=%s", uid, msg_type, channel, sent_count)

    async def send_to_user(self, uid: str, message: dict) -> None:
        """Alias for broadcast — sends message to all WS connections for a user."""
        await self.broadcast(uid, message)

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
