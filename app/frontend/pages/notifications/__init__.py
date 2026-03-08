"""
Notifications Page Handlers
===========================

Handlers pour les événements notification.* WebSocket.
"""

from .handlers import (
    NotificationHandlers,
    get_notification_handlers,
    handle_notification_mark_read,
    handle_notification_click,
)

__all__ = [
    "NotificationHandlers",
    "get_notification_handlers",
    "handle_notification_mark_read",
    "handle_notification_click",
]
