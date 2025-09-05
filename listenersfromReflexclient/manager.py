"""
Gestionnaire central des listeners temps-réel.
Coordonne le démarrage, l'arrêt et la gestion des listeners.
"""

import asyncio
import logging
from typing import Dict, Optional, List
from .base_listener import BaseListener, get_global_queue
from .realtime_listener import RealtimeListener
from .notification_listener import NotificationListener
from .message_listener import MessageListener
from .chat_listener import ChatListener
from .bus_consumer import BusConsumer

import asyncio
import os


class ListenerManager:
    """Gestionnaire central des listeners temps-réel (polling + temps réel)."""
    
    def __init__(self):
        self.listeners: Dict[str, BaseListener | RealtimeListener] = {}
        self.is_running = False
        self.logger = logging.getLogger("listener.manager")
        # Files par utilisateur et par type
        self.user_notif_queues: Dict[str, asyncio.Queue] = {}
        self.user_msg_queues: Dict[str, asyncio.Queue] = {}
        # Compat héritée (ancienne API)
        self.user_queues: Dict[str, asyncio.Queue] = {}
        # Dernier user id initialisé (compatibilité get_queue())
        self._last_user_id: Optional[str] = None

    def _get_mode(self) -> str:
        """Retourne le mode de fonctionnement des listeners.

        Valeurs possibles:
        - "ACTUEL" (ou "ACTUAL"): comportement historique (queue globale, listener unique)
        - "LOCAL" / "PROD": nouvelles queues par utilisateur + registre
        """
        mode = os.getenv("LISTENERS_MODE", "ACTUEL").strip().upper()
        if mode == "ACTUAL":
            mode = "ACTUEL"
        if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
            print(f"[ListenerManager] Mode sélectionné: {mode}")
        return mode
        
    async def start_notification_listener(self, firebase_user_id: str, authorized_companies_ids: List[str]):
        """Démarre le listener de notifications."""
        try:
            mode = self._get_mode()

            if mode == "ACTUEL":
                # Comportement historique: un seul listener global "notifications"
                await self.stop_listener("notifications")
                listener = NotificationListener(firebase_user_id, authorized_companies_ids, output_queue=None)
                self.listeners["notifications"] = listener
                await listener.start()
                # Compat: la queue utilisée restera la queue globale via get_queue()
                self._last_user_id = None
                self.logger.info(f"Listener notifications (mode ACTUEL) démarré pour {firebase_user_id}")
                if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                    print(f"[ListenerManager] Démarré (ACTUEL) notifications pour user={firebase_user_id}")
                return

            # Nouvelles implémentations: queues par utilisateur (LOCAL/PROD)
            if firebase_user_id not in self.user_notif_queues:
                self.user_notif_queues[firebase_user_id] = asyncio.Queue()
            user_queue = self.user_notif_queues[firebase_user_id]

            listener_key = f"notifications::{firebase_user_id}"
            if listener_key in self.listeners:
                await self.stop_listener(listener_key)

            # En modes LOCAL/PROD, consommer depuis le bus (Redis/Valkey)
            bus = BusConsumer(
                user_id=firebase_user_id,
                output_queue=user_queue,
                kinds=["notif"],
            )
            self.listeners[listener_key] = bus  # type: ignore
            await bus.start()
            self._last_user_id = firebase_user_id
            self.logger.info(f"Listener notifications (mode {mode}) démarré pour {firebase_user_id}")
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[ListenerManager] Démarré ({mode}) BusConsumer notifications pour user={firebase_user_id}")
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du listener de notifications: {e}")
    
    async def start_message_listener(self, firebase_user_id: str, companies: List[Dict]):
        """Démarre le listener de messages."""
        try:
            mode = self._get_mode()

            if mode == "ACTUEL":
                # Arrêter l'ancien listener s'il existe
                await self.stop_listener("messages")
                
                # Créer et démarrer le nouveau listener (historique Firebase)
                listener = MessageListener(firebase_user_id, companies)
                self.listeners["messages"] = listener
                await listener.start()
                self.logger.info(f"Listener de messages (mode ACTUEL) démarré pour {firebase_user_id}")
                if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                    print(f"[ListenerManager] Démarré (ACTUEL) messages pour user={firebase_user_id}")
                return

            # Nouvelles implémentations: queues par utilisateur (LOCAL/PROD) via BusConsumer
            if firebase_user_id not in self.user_notif_queues:
                self.user_notif_queues[firebase_user_id] = asyncio.Queue()
            user_queue = self.user_notif_queues[firebase_user_id]

            listener_key = f"messages::{firebase_user_id}"
            if listener_key in self.listeners:
                await self.stop_listener(listener_key)

            bus = BusConsumer(
                user_id=firebase_user_id,
                output_queue=user_queue,
                kinds=["msg"],
            )
            self.listeners[listener_key] = bus  # type: ignore
            await bus.start()
            self._last_user_id = firebase_user_id
            self.logger.info(f"Listener messages (mode {mode}) démarré pour {firebase_user_id}")
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[ListenerManager] Démarré ({mode}) BusConsumer messages pour user={firebase_user_id}")
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du listener de messages: {e}")

    
    
    async def stop_listener(self, listener_name: str):
        """Arrête un listener spécifique (polling ou temps réel)."""
        if listener_name in self.listeners:
            listener = self.listeners[listener_name]
            await listener.stop()
            del self.listeners[listener_name]
            self.logger.info(f"Listener {listener_name} arrêté")
    
    
    
    async def stop_all_listeners(self):
        """Arrête tous les listeners."""
        self.logger.info("Arrêt de tous les listeners")
        
        for listener_name in list(self.listeners.keys()):
            await self.stop_listener(listener_name)
        
        self.is_running = False
        self.logger.info("Tous les listeners arrêtés")
    
    async def update_user_context(self, firebase_user_id: str, authorized_companies_ids: List[str], companies: List[Dict]):
        """Met à jour le contexte utilisateur pour tous les listeners."""
        if "notifications" in self.listeners:
            listener = self.listeners["notifications"]
            if hasattr(listener, 'update_user_context'):
                listener.update_user_context(firebase_user_id, authorized_companies_ids)
        
        if "messages" in self.listeners:
            listener = self.listeners["messages"]
            if hasattr(listener, 'update_user_context'):
                listener.update_user_context(firebase_user_id, companies)
    
    def get_queue(self) -> asyncio.Queue:
        """Retourne une queue compatible avec l'existant.

        - Mode ACTUEL: retourne la queue globale historique
        - Autres modes: retourne la queue du dernier utilisateur initialisé si disponible, sinon la queue globale
        """
        mode = self._get_mode()
        if mode == "ACTUEL":
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print("[ListenerManager] get_queue() -> queue globale (ACTUEL)")
            return get_global_queue()
        # Fallback compat: chercher une file notif si dispo
        if self._last_user_id and self._last_user_id in self.user_notif_queues:
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[ListenerManager] get_queue() -> queue NOTIF user={self._last_user_id}")
            return self.user_notif_queues[self._last_user_id]
        if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
            print("[ListenerManager] get_queue() -> fallback queue globale")
        return get_global_queue()

    def get_queue_for_user(self, firebase_user_id: str) -> asyncio.Queue:
        """Retourne la queue spécifique d'un utilisateur, la crée si nécessaire."""
        if firebase_user_id not in self.user_notif_queues:
            self.user_notif_queues[firebase_user_id] = asyncio.Queue()
        return self.user_notif_queues[firebase_user_id]

    def get_notifications_queue_for_user(self, firebase_user_id: str) -> asyncio.Queue:
        if firebase_user_id not in self.user_notif_queues:
            self.user_notif_queues[firebase_user_id] = asyncio.Queue()
        return self.user_notif_queues[firebase_user_id]

    def get_messages_queue_for_user(self, firebase_user_id: str) -> asyncio.Queue:
        if firebase_user_id not in self.user_msg_queues:
            self.user_msg_queues[firebase_user_id] = asyncio.Queue()
        return self.user_msg_queues[firebase_user_id]
    
    def is_listener_running(self, listener_name: str) -> bool:
        """Vérifie si un listener spécifique est en cours d'exécution."""
        return listener_name in self.listeners and self.listeners[listener_name].is_running

    # ===== Chat listeners (Firebase Realtime) =====
    def _chat_key(self, space_code: str, thread_key: str) -> str:
        return f"chat_{space_code}_{thread_key}"

    async def start_chat_listener(self, space_code: str, thread_key: str, user_id: str, main_loop: asyncio.AbstractEventLoop | None = None, handler: Optional[callable] = None, mode: str = 'job_chats'):
        """Démarre un listener de chat.

        - Mode ACTUEL: Firebase Realtime direct via ChatListener (callback ou queue globale)
        - Modes LOCAL/PROD: BusConsumer (WS heartbeat + Redis) sur canal chat:{uid}:{space_code}:{thread_key}
        """
        listener_name = self._chat_key(space_code, thread_key)
        await self.stop_listener(listener_name) if listener_name in self.listeners else None

        mode_sel = self._get_mode()
        if mode_sel == "ACTUEL":
            chat_listener = ChatListener(space_code=space_code, thread_key=thread_key, user_id=user_id, mode=mode)

            if main_loop is not None and handler is not None:
                async def _direct_dispatch(message_data: dict):
                    try:
                        try:
                            current_loop = asyncio.get_running_loop()
                        except RuntimeError:
                            current_loop = None
                        if current_loop is main_loop:
                            await handler(message_data)
                            return True
                        future = asyncio.run_coroutine_threadsafe(
                            handler(message_data),
                            main_loop,
                        )
                        try:
                            future.result(timeout=1.0)
                        except Exception:
                            pass
                        return True
                    except Exception as e:
                        self.logger.error(f"Erreur dispatch direct chat {listener_name}: {e}")
                        return False
                callback = _direct_dispatch
            else:
                async def _to_queue(message_data: dict):
                    payload = {
                        "space_code": space_code,
                        "thread_key": thread_key,
                        "raw_messages": [message_data or {}],
                    }
                    queue = get_global_queue()
                    await queue.put((listener_name, payload))
                    return True
                callback = _to_queue

            await chat_listener.start(callback)
            self.listeners[listener_name] = chat_listener
            self.logger.info(f"Listener de chat (ACTUEL) démarré: {listener_name}")
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[ListenerManager] Chat ACTUEL démarré {space_code}/{thread_key} pour user={user_id}")
            return

        # Modes LOCAL/PROD: BusConsumer pour le chat
        # Préparer file dédiée par thread
        user_queue = asyncio.Queue()
        bus = BusConsumer(
            user_id=user_id,
            output_queue=user_queue,
            kinds=["chat"],
            space_code=space_code,
            thread_key=thread_key,
        )

        # Enregistrer un task de drainage vers le handler si fourni, sinon via queue globale
        if main_loop is not None and handler is not None:
            # Helper: extraire des messages unitaires depuis le payload bus
            def _iter_messages_from_payload(payload: dict) -> List[dict]:
                try:
                    if not isinstance(payload, dict):
                        # Cas rare: liste brute
                        return list(payload) if isinstance(payload, list) else []
                    # Priorité aux formes batchées connues
                    if isinstance(payload.get("raw_messages"), list):
                        return [m for m in payload.get("raw_messages", []) if isinstance(m, dict)]
                    if isinstance(payload.get("messages"), list):
                        return [m for m in payload.get("messages", []) if isinstance(m, dict)]
                    # Si on reçoit déjà un message "firebase-like"
                    if any(k in payload for k in ("message_type", "content", "id", "client_id")):
                        return [payload]
                    # Dernier recours: payload unique inconnu → tenter tel quel si dict
                    return [payload]
                except Exception:
                    return []

            async def _drain_and_dispatch():
                try:
                    while True:
                        try:
                            _, payload = await asyncio.wait_for(user_queue.get(), timeout=1.0)
                            # Dé-wrapper: dispatcher unitairement au handler
                            for msg in _iter_messages_from_payload(payload):
                                try:
                                    await handler(msg)
                                except Exception as e:
                                    self.logger.error(f"Erreur dispatch chat message→handler: {e}")
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            self.logger.error(f"Erreur drain chat bus→handler: {e}")
                            await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    pass

            drain_task = asyncio.create_task(_drain_and_dispatch())
            # Attacher pour pouvoir stop
            self.listeners[listener_name] = bus  # type: ignore
            await bus.start()
            self.logger.info(f"BusConsumer chat (mode {mode_sel}) démarré: {listener_name}")
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[ListenerManager] Chat {mode_sel} démarré {space_code}/{thread_key} pour user={user_id}")
            return
        else:
            # Fallback: pousser en queue globale avec clé listener_name, en re-wrap par message (compat ACTUEL)
            def _iter_messages_from_payload(payload: dict) -> List[dict]:
                try:
                    if not isinstance(payload, dict):
                        return list(payload) if isinstance(payload, list) else []
                    if isinstance(payload.get("raw_messages"), list):
                        return [m for m in payload.get("raw_messages", []) if isinstance(m, dict)]
                    if isinstance(payload.get("messages"), list):
                        return [m for m in payload.get("messages", []) if isinstance(m, dict)]
                    if any(k in payload for k in ("message_type", "content", "id", "client_id")):
                        return [payload]
                    return [payload]
                except Exception:
                    return []

            async def _drain_to_global():
                try:
                    global_q = get_global_queue()
                    while True:
                        try:
                            _, payload = await asyncio.wait_for(user_queue.get(), timeout=1.0)
                            # Re-wrapper par message pour simuler la forme ACTUEL
                            msgs = _iter_messages_from_payload(payload)
                            if not msgs:
                                continue
                            for msg in msgs:
                                wrapper = {
                                    "space_code": space_code,
                                    "thread_key": thread_key,
                                    "raw_messages": [msg or {}],
                                }
                                await global_q.put((listener_name, wrapper))
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            self.logger.error(f"Erreur drain chat bus→global: {e}")
                            await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    pass

            drain_task = asyncio.create_task(_drain_to_global())
            self.listeners[listener_name] = bus  # type: ignore
            await bus.start()
            self.logger.info(f"BusConsumer chat (mode {mode_sel}) démarré (fallback queue globale): {listener_name}")
            if os.getenv("LISTENERS_DEBUG", "false").lower() == "true":
                print(f"[ListenerManager] Chat {mode_sel} démarré {space_code}/{thread_key} pour user={user_id} (fallback)")
            return

    async def stop_chat_listener(self, space_code: str, thread_key: str):
        """Arrête le listener de chat identifié par space_code/thread_key."""
        listener_name = self._chat_key(space_code, thread_key)
        await self.stop_listener(listener_name)

# Instance globale du gestionnaire
listener_manager = ListenerManager() 