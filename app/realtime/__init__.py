"""
Realtime Subscriptions Module
============================

Ce module gère les subscriptions temps réel pour les notifications et messages.

Architecture:
- Les notifications et messages sont GLOBAUX (par uid, pas par société)
- À la connexion, les données initiales sont chargées depuis Firebase/RTDB
- Ensuite, les mises à jour passent par Redis PubSub
- Les jobbeurs publient sur PubSub après avoir écrit dans Firebase/RTDB

Composants:
- pubsub_helper: Helpers pour les jobbeurs qui publient des événements
- subscription_manager: Singleton qui gère les subscriptions utilisateur

Usage (Jobbeurs):
    from app.realtime import publish_notification_event, publish_messenger_event

    # Après avoir écrit une notification dans Firebase
    await publish_notification_event(uid, 'new', notification_data)

    # Après avoir écrit un message dans RTDB
    await publish_messenger_event(uid, 'new', message_data)

Usage (Backend Orchestration):
    from app.realtime import get_subscription_manager

    manager = get_subscription_manager()
    await manager.start_user_subscriptions(uid)
"""

from .pubsub_helper import (
    publish_notification_event,
    publish_notification_new,
    publish_notification_update,
    publish_notification_remove,
    publish_notifications_full_data,
    publish_messenger_event,
    publish_messenger_new,
    publish_messenger_remove,
    publish_messages_full_data,
    is_user_connected,
)
from .subscription_manager import (
    RealtimeSubscriptionManager,
    get_subscription_manager,
)
from .contextual_publisher import (
    PublicationLevel,
    publish_contextual_event,
    publish_user_event,
    publish_company_event,
    publish_page_event,
    update_page_context,
)
from .worker_broadcast_listener import (
    WorkerBroadcastListener,
    get_worker_broadcast_listener,
    start_worker_broadcast_listener,
    stop_worker_broadcast_listener,
)

__all__ = [
    # PubSub helpers (pour les jobbeurs - niveau USER)
    "publish_notification_event",
    "publish_notification_new",
    "publish_notification_update",
    "publish_notification_remove",
    "publish_notifications_full_data",
    "publish_messenger_event",
    "publish_messenger_new",
    "publish_messenger_remove",
    "publish_messages_full_data",
    "is_user_connected",
    # Subscription manager (pour l'orchestration)
    "RealtimeSubscriptionManager",
    "get_subscription_manager",
    # Contextual Publisher (USER/COMPANY/PAGE)
    "PublicationLevel",
    "publish_contextual_event",
    "publish_user_event",
    "publish_company_event",
    "publish_page_event",
    "update_page_context",
    # Worker Broadcast Listener (for worker -> API -> WebSocket)
    "WorkerBroadcastListener",
    "get_worker_broadcast_listener",
    "start_worker_broadcast_listener",
    "stop_worker_broadcast_listener",
]
