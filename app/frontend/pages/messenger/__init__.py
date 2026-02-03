"""
Messenger Page Handlers
=======================

Handlers pour les événements messenger.* WebSocket.
"""

from .handlers import (
    MessengerHandlers,
    get_messenger_handlers,
    handle_messenger_mark_read,
    handle_messenger_click,
)

__all__ = [
    "MessengerHandlers",
    "get_messenger_handlers",
    "handle_messenger_mark_read",
    "handle_messenger_click",
]
