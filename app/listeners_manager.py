import logging
import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from .config import get_settings
from .firebase_client import get_firestore
from .redis_client import get_redis
from .ws_hub import hub
from .registry import get_registry_listeners
import os
import firebase_admin
from firebase_admin import db as rtdb


def _is_online_and_not_expired(doc: DocumentSnapshot) -> bool:
    data = doc.to_dict() or {}
    status = data.get("status")
    if status != "online":
        return False
    ttl_seconds = int(data.get("ttl_seconds", 90))
    hb = data.get("heartbeat_at")
    if hb is None:
        return False
    try:
        if hasattr(hb, "to_datetime"):
            hb_dt = hb.to_datetime()
        elif isinstance(hb, datetime):
            hb_dt = hb
        else:
            return False
        now = datetime.now(timezone.utc)
        age = (now - hb_dt).total_seconds()
        return age <= ttl_seconds
    except Exception:
        return False


class ListenersManager:
    def __init__(self) -> None:
        self.logger = logging.getLogger("listeners.manager")
        self.settings = get_settings()
        self.db: firestore.Client = get_firestore()
        self.redis = get_redis()
        self._registry_unsub: Optional[Callable[[], None]] = None
        self._user_unsubs: Dict[str, List[Callable[[], None]]] = {}
        self._workflow_unsubs: Dict[str, List[Callable[[], None]]] = {}  # Workflow listeners
        self._workflow_cache: Dict[str, dict] = {}  # Cache des valeurs précédentes
        self._transaction_listeners: Dict[str, Dict] = {}  # Transaction status listeners {listener_key: {watch, state}}
        self._lock = threading.Lock()
        self._workflow_enabled = self._get_workflow_config()
        self._transaction_listener_enabled = self._get_transaction_listener_config()

    @property
    def listeners_count(self) -> int:
        with self._lock:
            return len(self._user_unsubs)

    @property
    def workflow_listeners_count(self) -> int:
        with self._lock:
            return len(self._workflow_unsubs)

    @property
    def transaction_listeners_count(self) -> int:
        with self._lock:
            return len(self._transaction_listeners)

    def _get_workflow_config(self) -> bool:
        """Récupère la configuration du workflow listener depuis les variables d'environnement ou secrets."""
        try:
            # Vérifier d'abord les variables d'environnement
            workflow_enabled = os.getenv("WORKFLOW_LISTENER_ENABLED", "true").lower()  # Activé par défaut
            return workflow_enabled in ("true", "1", "yes", "on")
        except Exception:
            return True  # Activé par défaut

    def _get_transaction_listener_config(self) -> bool:
        """Récupère la configuration du transaction listener depuis les variables d'environnement ou secrets."""
        try:
            # Vérifier d'abord les variables d'environnement
            transaction_enabled = os.getenv("TRANSACTION_LISTENER_ENABLED", "true").lower()
            return transaction_enabled in ("true", "1", "yes", "on")
        except Exception:
            return True  # Activé par défaut

    def start(self) -> None:
        self.logger.info("registry_watch start collection=listeners_registry")
        col = self.db.collection("listeners_registry")
        self._registry_unsub = col.on_snapshot(self._on_registry_snapshot)

    def stop(self) -> None:
        self.logger.info("registry_watch stop")
        try:
            if self._registry_unsub:
                # Le retour de on_snapshot est un objet Watch qui a une méthode unsubscribe()
                # ou close() selon les versions, mais n'est pas callable directement
                if hasattr(self._registry_unsub, "unsubscribe"):
                    self._registry_unsub.unsubscribe()
                elif hasattr(self._registry_unsub, "close"):
                    self._registry_unsub.close()
                elif callable(self._registry_unsub):
                    self._registry_unsub()
        except Exception as e:
            self.logger.error("registry_unsub error=%s", repr(e))
        with self._lock:
            for uid, unsubs in self._user_unsubs.items():
                for u in unsubs:
                    try:
                        u()
                    except Exception as e:
                        self.logger.error("user_unsub uid=%s error=%s", uid, repr(e))
            self._user_unsubs.clear()
            
            # Nettoyer les transaction listeners
            for listener_key, listener_info in self._transaction_listeners.items():
                try:
                    document_watch = listener_info.get('watch')
                    if document_watch and callable(document_watch):
                        document_watch()
                except Exception as e:
                    self.logger.error("transaction_listener_cleanup key=%s error=%s", listener_key, repr(e))
            self._transaction_listeners.clear()

    def _on_registry_snapshot(self, docs, changes, read_time) -> None:  # type: ignore[no-untyped-def]
        try:
            # Log uniquement si plusieurs changements (évite spam)
            if len(changes) > 1:
                self.logger.info("registry_snapshot triggered changes_count=%s", len(changes))
            for change in changes:
                doc: DocumentSnapshot = change.document
                uid = doc.id
                online = _is_online_and_not_expired(doc)
                # Log désactivé (trop verbeux)
                # self.logger.info("registry_change uid=%s type=%s online=%s", uid, change.type.name, online)
                if change.type.name in ("ADDED", "MODIFIED"):
                    if online:
                        # Log désactivé (trop verbeux)
                        # self.logger.info("registry_attach_trigger uid=%s", uid)
                        self._ensure_user_watchers(uid)
                    else:
                        self.logger.info("registry_detach_trigger uid=%s reason=offline_or_expired", uid)
                        self._detach_user_watchers(uid, reason="offline_or_expired")
                elif change.type.name == "REMOVED":
                    self.logger.info("registry_detach_trigger uid=%s reason=registry_removed", uid)
                    self._detach_user_watchers(uid, reason="registry_removed")
        except Exception as e:
            self.logger.error("registry_snapshot error=%s", repr(e))

    def _ensure_user_watchers(self, uid: str) -> None:
        with self._lock:
            already = uid in self._user_unsubs
        if already:
            # Log désactivé (trop verbeux)
            # self.logger.info("user_attach_skip uid=%s reason=already_attached", uid)
            return
        self.logger.info("user_attach_start uid=%s", uid)
        unsubs: List[Callable[[], None]] = []
        try:
            self.logger.info("user_attach_firebase_query uid=%s", uid)
            q = (
                self.db.collection("clients").document(uid)
                .collection("notifications")
                .where(filter=FieldFilter("read", "==", False))
            )
            self.logger.info("user_attach_firebase_listener uid=%s", uid)
            unsub_notif = q.on_snapshot(lambda docs, changes, rt: self._on_notifications(uid, docs, changes, rt))  # type: ignore[arg-type]
            unsubs.append(unsub_notif)
            self.logger.info("user_attach_notification_listener_attached uid=%s", uid)

            self.logger.info("user_attach_publish_sync_notifications uid=%s", uid)
            self._publish_notifications_sync(uid)

            # Messages directs (Firebase Realtime Database)
            self.logger.info("user_attach_rtdb_messages uid=%s", uid)
            unsub_msg = self._start_direct_messages_listener(uid)
            if unsub_msg:
                unsubs.append(unsub_msg)
                self.logger.info("user_attach_message_listener_attached uid=%s", uid)
            self._publish_messages_sync(uid)

            # ❌ WORKFLOW LISTENER RETIRÉ : Désormais démarré à la demande par job_id
            # Le listener workflow n'est plus global mais activé uniquement quand un utilisateur
            # ouvre une page EditForm pour un job spécifique (via start_workflow_listener_for_job)
            self.logger.debug("user_attach_workflow_listener_skipped uid=%s reason=on_demand_only", uid)
            
            with self._lock:
                self._user_unsubs[uid] = unsubs
            self.logger.info("user_attach_complete uid=%s listeners_count=%s", uid, len(unsubs))
            
            # 🆕 NOUVEAU : Enregistrer les listeners dans le registre centralisé
            self.logger.info("🔵 REGISTRY_START enregistrement des listeners pour uid=%s", uid)
            try:
                registry = get_registry_listeners()
                self.logger.info("🔵 REGISTRY_INSTANCE récupérée pour uid=%s", uid)
                
                # Enregistrer listener notifications
                notif_result = registry.register_listener(
                    user_id=uid,
                    listener_type="notif"
                )
                self.logger.info("🔵 REGISTRY_NOTIF uid=%s success=%s", uid, notif_result.get("success"))
                
                # Enregistrer listener messages
                msg_result = registry.register_listener(
                    user_id=uid,
                    listener_type="msg"
                )
                self.logger.info("🔵 REGISTRY_MSG uid=%s success=%s", uid, msg_result.get("success"))
                
                
                self.logger.info("🟢 REGISTRY_COMPLETE uid=%s enregistrement terminé", uid)
            except Exception as e:
                # Ne pas bloquer si l'enregistrement échoue (traçabilité optionnelle)
                self.logger.error("🔴 REGISTRY_ERROR uid=%s error=%s", uid, repr(e), exc_info=True)
            
        except Exception as e:
            self.logger.error("user_attach_error uid=%s error=%s", uid, repr(e))
            for u in unsubs:
                try:
                    u()
                except Exception:
                    pass

    def _detach_user_watchers(self, uid: str, reason: str) -> None:
        with self._lock:
            unsubs = self._user_unsubs.pop(uid, [])
            workflow_unsubs = self._workflow_unsubs.pop(uid, [])
            # Nettoyer le cache workflow pour cet utilisateur
            self._workflow_cache.pop(uid, None)

        if not unsubs and not workflow_unsubs:
            return

        self.logger.info("user_detach_scheduled uid=%s reason=%s listeners=%s workflow=%s", 
                        uid, reason, len(unsubs), len(workflow_unsubs))

        def _do_detach():
            try:
                # ⏰ DÉLAI DE 5 SECONDES pour permettre la reconnexion rapide
                # Ce délai évite les race conditions entre déconnexion et reconnexion immédiate
                delay_seconds = 5
                self.logger.info(
                    "⏰ user_detach_delay_start uid=%s reason=%s delay=%ss", 
                    uid, reason, delay_seconds
                )
                time.sleep(delay_seconds)
                
                # 🔍 VÉRIFICATION : L'utilisateur s'est-il reconnecté pendant le délai ?
                with self._lock:
                    if uid in self._user_unsubs:
                        # ✅ Reconnexion détectée : annuler le nettoyage
                        self.logger.info(
                            "✅ user_detach_cancelled uid=%s reason=reconnected_during_delay",
                            uid
                        )
                        return
                
                self.logger.info(
                    "🧹 user_detach_executing uid=%s reason=%s (no reconnection detected)",
                    uid, reason
                )
                
                # Détacher les listeners standards
                for u in unsubs:
                    try:
                        # Certains listeners renvoient un objet avec .close(), d'autres une fonction
                        if callable(u):
                            u()  # type: ignore[misc]
                        elif hasattr(u, "close"):
                            try:
                                u.close()  # type: ignore[attr-defined]
                            except Exception:
                                pass
                    except Exception as e:
                        self.logger.error("user_unsub_error uid=%s error=%s", uid, repr(e))

                # Détacher les workflow listeners
                for u in workflow_unsubs:
                    try:
                        if callable(u):
                            u()  # type: ignore[misc]
                        elif hasattr(u, "close"):
                            try:
                                u.close()  # type: ignore[attr-defined]
                            except Exception:
                                pass
                    except Exception as e:
                        self.logger.error("workflow_unsub_error uid=%s error=%s", uid, repr(e))
                
                # 🆕 NOUVEAU : Nettoyer le registre centralisé
                self.logger.info("🔵 REGISTRY_CLEANUP_START nettoyage pour uid=%s reason=%s", uid, reason)
                try:
                    registry = get_registry_listeners()
                    cleanup_result = registry.cleanup_user_listeners(uid)
                    if cleanup_result.get("success"):
                        cleaned_count = cleanup_result.get("cleaned_count", 0)
                        self.logger.info("🟢 REGISTRY_CLEANUP_SUCCESS uid=%s cleaned=%s", uid, cleaned_count)
                    else:
                        self.logger.error("🔴 REGISTRY_CLEANUP_FAILED uid=%s error=%s", uid, cleanup_result.get("error"))
                except Exception as e:
                    # Ne pas bloquer si le nettoyage échoue
                    self.logger.error("🔴 REGISTRY_CLEANUP_ERROR uid=%s error=%s", uid, repr(e), exc_info=True)
            except Exception as e:
                self.logger.error("detach_thread_error uid=%s error=%s", uid, repr(e), exc_info=True)

        # Exécuter le nettoyage dans un thread séparé pour ne pas bloquer la boucle principale (Health Check)
        threading.Thread(target=_do_detach, name=f"detach-{uid}", daemon=True).start()

    def publish(self, uid: str, payload: dict) -> None:
        """Méthode publique pour publier des messages via Redis/WS"""
        self._publish(uid, payload)

    def _publish(self, uid: str, payload: dict) -> None:
        evt_type = str(payload.get("type", ""))
        
        # ⭐ Événements workflow : UNIQUEMENT WebSocket (pas de Redis)
        # ⭐ MODE BACKEND : Vérifier si user est sur ce thread avant de broadcaster
        if evt_type.startswith("workflow"):
            job_id = payload.get("job_id")
            space_code = payload.get("space_code")
            thread_key = payload.get("payload", {}).get("thread_key")
            
            self.logger.debug("workflow_wss_publish_start uid=%s type=%s job_id=%s space_code=%s thread_key=%s",
                            uid, evt_type, job_id, space_code, thread_key)
            
            # ⭐ Vérifier si user est sur ce thread (comme pour chat*)
            should_broadcast_ws = True
            if space_code and thread_key:
                try:
                    from .llm_service.session_state_manager import SessionStateManager
                    state_manager = SessionStateManager()
                    user_on_thread = state_manager.is_user_on_thread(uid, space_code, thread_key)
                    
                    if not user_on_thread:
                        # Mode BACKEND : pas de broadcast WebSocket
                        should_broadcast_ws = False
                        self.logger.debug(
                            "workflow_backend_mode uid=%s type=%s space=%s thread=%s (no_ws_broadcast)",
                            uid, evt_type, space_code, thread_key
                        )
                    else:
                        # Mode UI : broadcast activé
                        self.logger.debug(
                            "workflow_ui_mode uid=%s type=%s space=%s thread=%s (ws_broadcast_enabled)",
                            uid, evt_type, space_code, thread_key
                        )
                except Exception as e:
                    # En cas d'erreur, broadcaster par défaut (comportement conservateur)
                    self.logger.warning(
                        "workflow_mode_check_error uid=%s error=%s (broadcasting anyway)",
                        uid, repr(e)
                    )
            
            if should_broadcast_ws:
                try:
                    hub.broadcast_threadsafe(uid, payload)
                    self.logger.info("workflow_wss_success uid=%s type=%s job_id=%s space_code=%s", 
                                   uid, evt_type, job_id, space_code)
                except Exception as e:
                    self.logger.error("workflow_wss_error uid=%s type=%s job_id=%s error=%s", 
                                    uid, evt_type, job_id, repr(e), exc_info=True)
            else:
                self.logger.debug("workflow_wss_skipped_backend_mode uid=%s type=%s", uid, evt_type)
            return  # Sortie anticipée, pas de Redis pour workflow
        
        # Pour autres types : Redis + WebSocket (comportement existant)
        channel = f"{self.settings.channel_prefix}{uid}"
        
        # Canal dédié pour chat si configuré
        should_broadcast_ws = True  # Par défaut, broadcaster
        if evt_type.startswith("chat"):
            chat_prefix = os.getenv("LISTENERS_CHAT_CHANNEL_PREFIX", "chat:")
            sc = payload.get("payload", {}).get("space_code")
            tk = payload.get("payload", {}).get("thread_key")
            if sc and tk:
                channel = f"{chat_prefix}{uid}:{sc}:{tk}"
                
                # ⭐ MODE BACKEND : Vérifier si user est sur ce thread avant de broadcaster
                # Si l'utilisateur n'est pas sur ce thread, on est en mode BACKEND
                # → Ne pas broadcaster WebSocket (économie ressources)
                try:
                    from .llm_service.session_state_manager import SessionStateManager
                    state_manager = SessionStateManager()
                    user_on_thread = state_manager.is_user_on_thread(uid, sc, tk)
                    
                    if not user_on_thread:
                        # Mode BACKEND : pas de broadcast WebSocket
                        should_broadcast_ws = False
                        self.logger.debug(
                            "chat_message_backend_mode uid=%s space=%s thread=%s (no_ws_broadcast)",
                            uid, sc, tk
                        )
                    else:
                        # Mode UI : broadcast activé
                        self.logger.debug(
                            "chat_message_ui_mode uid=%s space=%s thread=%s (ws_broadcast_enabled)",
                            uid, sc, tk
                        )
                except Exception as e:
                    # En cas d'erreur, broadcaster par défaut (comportement conservateur)
                    self.logger.warning(
                        "chat_message_mode_check_error uid=%s error=%s (broadcasting anyway)",
                        uid, repr(e)
                    )
        
        try:
            self.redis.publish(channel, json.dumps(payload))
        except Exception as e:
            self.logger.error("redis_publish_error uid=%s error=%s", uid, repr(e))
        # WS (best-effort): broadcaster UNIQUEMENT si mode UI
        if should_broadcast_ws:
            try:
                hub.broadcast_threadsafe(uid, payload)
            except Exception as e:
                self.logger.error("ws_broadcast_error uid=%s error=%s", uid, repr(e))
        else:
            self.logger.debug("ws_broadcast_skipped_backend_mode uid=%s type=%s", uid, evt_type)
        self.logger.info("publish type=%s uid=%s channel=%s", payload.get("type"), uid, channel)

    def _publish_notifications_sync(self, uid: str) -> None:
        try:
            # Récupère liste d'autorisations éventuelles depuis le registre
            allowed_companies: List[str] = []
            try:
                reg_snap = self.db.collection("listeners_registry").document(uid).get()
                reg_data = reg_snap.to_dict() or {}
                allowed_companies = list(reg_data.get("authorized_companies_ids") or [])
            except Exception:
                allowed_companies = []

            q = (
                self.db.collection("clients").document(uid)
                .collection("notifications")
                .where(filter=FieldFilter("read", "==", False))
            )
            docs = list(q.stream())
            raw_items = []
            for d in docs:
                item = d.to_dict() or {}
                item["doc_id"] = d.id
                # Filtrage optionnel par companies autorisées
                if allowed_companies:
                    if (item.get("collection_id") or "") in allowed_companies:
                        raw_items.append(item)
                else:
                    raw_items.append(item)

            # Formater pour compat historique (client NotificationListener)
            formatted = [_format_notification_item(x) for x in raw_items]
            # Trier décroissant par timestamp ISO (lexico ok)
            formatted.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

            payload = {"notifications": formatted, "count": len(formatted), "timestamp": datetime.now(timezone.utc).isoformat()}
            msg = {
                "type": "notif.sync",
                "uid": uid,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
            self._publish(uid, msg)
            try:
                sample_ids = [x.get("doc_id") for x in formatted[:5]]
                self.logger.info("notif_sync uid=%s count=%s sample_ids=%s", uid, len(formatted), sample_ids)
            except Exception:
                pass
        except Exception as e:
            self.logger.error("notif_sync_error uid=%s error=%s", uid, repr(e))

    def _on_notifications(self, uid: str, docs, changes, read_time) -> None:  # type: ignore[no-untyped-def]
        try:
            self.logger.info("notifications_change_triggered uid=%s changes_count=%s", uid, len(changes))
            for change in changes:
                self.logger.info("notification_change uid=%s doc_id=%s type=%s", uid, change.document.id, change.type.name)
            # Pour compat historique, republier un snapshot complet formaté
            # à chaque changement (add/update/remove)
            self.logger.info("notifications_resync_start uid=%s", uid)
            self._publish_notifications_sync(uid)
            self.logger.info("notifications_resync_complete uid=%s", uid)
        except Exception as e:
            self.logger.error("notif_change_error uid=%s error=%s", uid, repr(e))

    def _on_messages_event(self, uid: str) -> None:
        try:
            self._publish_messages_sync(uid)
        except Exception as e:
            self.logger.error("msg_change_error uid=%s error=%s", uid, repr(e))

    def _publish_messages_sync(self, uid: str) -> None:
        try:
            allowed_companies: List[str] = []
            try:
                reg_snap = self.db.collection("listeners_registry").document(uid).get()
                reg_data = reg_snap.to_dict() or {}
                allowed_companies = list(reg_data.get("authorized_companies_ids") or [])
            except Exception:
                allowed_companies = []

            # Lire la liste des messages directs non lus depuis RTDB
            path = f"clients/{uid}/direct_message_notif"
            ref = _get_rtdb_ref(path)
            data = ref.get() or {}
            raw_items: List[dict] = []
            if isinstance(data, dict):
                for msg_id, msg in data.items():
                    if not isinstance(msg, dict):
                        continue
                    item = dict(msg)
                    item["doc_id"] = msg_id
                    if allowed_companies:
                        if (item.get("collection_id") or "") in allowed_companies:
                            raw_items.append(item)
                    else:
                        raw_items.append(item)

            formatted = [_format_message_item(x) for x in raw_items]
            formatted.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

            payload = {"messages": formatted, "count": len(formatted), "timestamp": datetime.now(timezone.utc).isoformat()}
            msg = {
                "type": "msg.sync",
                "uid": uid,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
            self._publish(uid, msg)
            try:
                sample_ids = [x.get("doc_id") for x in formatted[:5]]
                self.logger.info("msg_sync uid=%s count=%s sample_ids=%s", uid, len(formatted), sample_ids)
            except Exception:
                pass
        except Exception as e:
            self.logger.error("msg_sync_error uid=%s error=%s", uid, repr(e))

    def _start_direct_messages_listener(self, uid: str) -> Optional[Callable[[], None]]:
        """Attache un listener RTDB sur les messages directs et renvoie une fonction de fermeture."""
        try:
            path = f"clients/{uid}/direct_message_notif"
            ref = _get_rtdb_ref(path)

            def _on_event(event):  # RTDB callback (thread)
                try:
                    # Déclenche un resync complet à chaque put
                    if getattr(event, "event_type", None) == "put":
                        self._on_messages_event(uid)
                except Exception as e:
                    self.logger.error("rtdb_msg_event_error uid=%s error=%s", uid, repr(e))

            listener = ref.listen(_on_event)

            def _close():
                try:
                    if listener and hasattr(listener, "close"):
                        listener.close()
                except Exception:
                    pass

            return _close
        except Exception as e:
            self.logger.error("rtdb_msg_listener_error uid=%s error=%s", uid, repr(e))
            return None


    def start_chat_watcher(self, uid: str, space_code: str, thread_key: str, mode: str = "job_chats") -> None:
        """Expose un attach chat pour un utilisateur (utilisé par le WS)."""
        try:
            closer = self._start_chat_listener(uid, space_code, thread_key, mode)
            if closer:
                with self._lock:
                    self._user_unsubs.setdefault(uid, []).append(closer)
                self.logger.info("chat_attach uid=%s space=%s thread=%s mode=%s", uid, space_code, thread_key, mode)
        except Exception as e:
            self.logger.error("chat_attach_error uid=%s error=%s", uid, repr(e))

    def _start_chat_listener(self, uid: str, space_code: str, thread_key: str, mode: str = "job_chats") -> Optional[Callable[[], None]]:
        """Écoute les messages d'un thread de chat dans RTDB et publie sur Redis.

        Sélection du chemin RTDB:
        - Si mode explicite vaut "chats", "active_chats" ou "job_chats", on l'utilise tel quel.
        - Si mode absent/"auto"/invalide: on tente d'abord active_chats/, puis chats/, puis job_chats/.
        """
        try:
            selected_mode = (mode or "auto").strip()
            candidate_modes: List[str]
            valid_modes = ("chats", "active_chats", "job_chats")
            if selected_mode in valid_modes:
                candidate_modes = [selected_mode]
            else:
                candidate_modes = ["active_chats", "chats", "job_chats"]

            ref = None
            chosen_mode = None
            for m in candidate_modes:
                path_try = f"{space_code}/{m}/{thread_key}/messages"
                try:
                    tmp_ref = _get_rtdb_ref(path_try)
                    # Vérifier existence légère: lecture tête (peut renvoyer None si vide/non créé)
                    _ = tmp_ref.get()
                    ref = tmp_ref
                    chosen_mode = m
                    break
                except Exception:
                    # Essayer prochain mode
                    continue

            if ref is None:
                # Dernière tentative: utiliser le dernier path pour forcer le listener (au cas où des events arrivent plus tard)
                fallback_mode = candidate_modes[-1]
                path_fallback = f"{space_code}/{fallback_mode}/{thread_key}/messages"
                ref = _get_rtdb_ref(path_fallback)
                chosen_mode = fallback_mode

            # 🔍 LOG CRUCIAL : Afficher le chemin RTDB exact
            rtdb_full_path = f"{space_code}/{chosen_mode}/{thread_key}/messages"
            self.logger.info("🔍 CHAT_RTDB_PATH uid=%s full_path=%s", uid, rtdb_full_path)
            self.logger.info("chat_path_resolved uid=%s space=%s thread=%s requested_mode=%s chosen_mode=%s", uid, space_code, thread_key, mode, chosen_mode)

            def _on_event(event):
                try:
                    # 🔍 LOG COMPLET : Voir TOUTES les données de l'événement
                    event_type = getattr(event, "event_type", None)
                    event_path = getattr(event, "path", None)
                    event_data = getattr(event, "data", None)
                    event_data_type = type(event_data).__name__
                    
                    self.logger.info("🔍 CHAT_RAW_EVENT uid=%s event_type=%s path=%s data_type=%s data_is_dict=%s data_keys=%s", 
                                   uid, event_type, event_path, event_data_type, 
                                   isinstance(event_data, dict),
                                   list(event_data.keys()) if isinstance(event_data, dict) else "N/A")
                    
                    self.logger.info("🔵 CHAT_EVENT_RECEIVED uid=%s space=%s thread=%s event_type=%s path=%s", 
                                   uid, space_code, thread_key, event_type, event_path)
                    
                    if event_type != "put":
                        self.logger.warning("🟡 CHAT_EVENT_SKIP uid=%s reason=not_put event_type=%s", 
                                          uid, getattr(event, "event_type", None))
                        return
                    
                    # Cas 1: path=/ signifie snapshot initial ou mise à jour de tout le thread
                    # Dans ce cas, event.data est un dict de {msg_id: message_data}
                    if event.path == "/":
                        # Chat vide (nouveau chat) : event.data est None
                        if event.data is None or (isinstance(event.data, dict) and len(event.data) == 0):
                            self.logger.info("🔵 CHAT_EMPTY_RECEIVED uid=%s space=%s thread=%s status=new_or_empty_chat", 
                                           uid, space_code, thread_key)
                            self.logger.info("🔍 CHAT_LISTENER_ACTIVE uid=%s space=%s thread=%s status=waiting_for_first_message", 
                                           uid, space_code, thread_key)
                            # Chat vide mais valide, on attend juste les nouveaux messages
                            return
                        
                        # Chat avec messages existants
                        if isinstance(event.data, dict):
                            self.logger.info("🔵 CHAT_SNAPSHOT_RECEIVED uid=%s space=%s thread=%s messages_count=%s", 
                                           uid, space_code, thread_key, len(event.data))
                            self.logger.info("🔍 CHAT_LISTENER_ACTIVE uid=%s space=%s thread=%s status=waiting_for_new_messages", 
                                           uid, space_code, thread_key)
                            # On ignore les snapshots initiaux pour éviter de republier tous les anciens messages
                            # Les nouveaux messages arrivent avec path=/msg_id
                            return
                    
                    # Cas 2: path=/msg_id signifie un nouveau message ou une mise à jour
                    if not (event.data and event.path != "/" and isinstance(event.data, dict)):
                        self.logger.warning("🟡 CHAT_EVENT_SKIP uid=%s reason=invalid_data path=%s data_type=%s data=%s", 
                                          uid, event.path, type(event.data).__name__, 
                                          str(event.data)[:100] if event.data else "None")
                        return
                    
                    msg_id = event.path.lstrip("/")
                    message_data = {"id": msg_id, **event.data}
                    
                    # ═══════════════════════════════════════════════════════════
                    # DÉTECTION DES MESSAGES D'APPROBATION DE CARTE
                    # ═══════════════════════════════════════════════════════════
                    # Si le message contient un champ 'action', c'est une réponse de carte
                    # qui doit être routée vers send_card_response au lieu d'être traitée
                    # comme un message normal
                    action = event.data.get('action')
                    if action:
                        self.logger.info(
                            "🃏 CARD_ACTION_DETECTED uid=%s msg_id=%s action=%s thread=%s data_keys=%s",
                            uid, msg_id, action, thread_key, list(event.data.keys())
                        )
                        
                        # Extraire les informations nécessaires pour send_card_response
                        # Essayer plusieurs structures possibles pour card_name
                        card_name = None
                        message_obj = event.data.get('message')
                        if isinstance(message_obj, dict):
                            # Structure Google Chat Card
                            card_params = message_obj.get('cardParams', {})
                            if isinstance(card_params, dict):
                                card_name = card_params.get('cardId')
                            
                            # Essayer aussi dans cardsV2
                            if not card_name:
                                cards_v2 = message_obj.get('cardsV2', [])
                                if cards_v2 and len(cards_v2) > 0:
                                    first_card = cards_v2[0] if isinstance(cards_v2[0], dict) else {}
                                    card_name = first_card.get('cardId')
                        
                        # Essayer d'extraire depuis d'autres champs possibles
                        if not card_name:
                            card_name = event.data.get('cardId') or event.data.get('card_name') or 'approval_card'
                        
                        # Extraire le message utilisateur (commentaire optionnel)
                        user_message = ""
                        if isinstance(message_obj, dict):
                            # Structure Google Chat Card avec common.formInputs
                            common = message_obj.get('common', {})
                            if isinstance(common, dict):
                                form_inputs = common.get('formInputs', {})
                                if isinstance(form_inputs, dict):
                                    user_msg_input = form_inputs.get('user_message', {})
                                    if isinstance(user_msg_input, dict):
                                        string_inputs = user_msg_input.get('stringInputs', {})
                                        if isinstance(string_inputs, dict):
                                            values = string_inputs.get('value', [])
                                            if values and len(values) > 0:
                                                user_message = str(values[0])
                        
                        # Extraire card_message_id depuis le message ou utiliser msg_id
                        # Le card_message_id peut être dans différents endroits selon la structure
                        card_message_id = (
                            event.data.get('card_message_id') or 
                            event.data.get('assistant_message_id') or
                            (message_obj.get('assistant_message_id') if isinstance(message_obj, dict) else None) or
                            msg_id
                        )
                        
                        # Appeler send_card_response de manière asynchrone
                        try:
                            import asyncio
                            from .llm_service import get_llm_manager
                            
                            async def handle_card_response():
                                try:
                                    llm_manager = get_llm_manager()
                                    result = await llm_manager.send_card_response(
                                        user_id=uid,
                                        collection_name=space_code,
                                        thread_key=thread_key,
                                        card_name=card_name,
                                        card_message_id=card_message_id,
                                        action=action,
                                        user_message=user_message
                                    )
                                    self.logger.info(
                                        "✅ CARD_RESPONSE_HANDLED uid=%s msg_id=%s action=%s result=%s",
                                        uid, msg_id, action, result.get('success', False)
                                    )
                                except Exception as e:
                                    self.logger.error(
                                        "❌ CARD_RESPONSE_ERROR uid=%s msg_id=%s error=%s",
                                        uid, msg_id, str(e), exc_info=True
                                    )
                            
                            # Exécuter de manière asynchrone dans la boucle d'événements
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    # Si la boucle est déjà en cours d'exécution, créer une tâche
                                    asyncio.create_task(handle_card_response())
                                else:
                                    # Sinon, exécuter directement
                                    loop.run_until_complete(handle_card_response())
                            except RuntimeError:
                                # Si aucune boucle n'existe, en créer une nouvelle
                                asyncio.run(handle_card_response())
                            
                        except Exception as e:
                            self.logger.error(
                                "❌ CARD_ACTION_ROUTING_ERROR uid=%s msg_id=%s error=%s",
                                uid, msg_id, str(e), exc_info=True
                            )
                        
                        # Marquer comme lu
                        try:
                            ref.child(msg_id).update({"read": True})
                        except Exception:
                            pass
                        
                        # Ne pas publier comme chat.message normal
                        return
                    
                    self.logger.info("🔵 CHAT_MESSAGE_PROCESSING uid=%s msg_id=%s content_preview=%s", 
                                   uid, msg_id, str(event.data.get('content', ''))[:50])
                    
                    # Marquer comme lu (best-effort)
                    try:
                        ref.child(msg_id).update({"read": True})
                    except Exception:
                        pass
                    
                    payload = {
                        "space_code": space_code,
                        "thread_key": thread_key,
                        "raw_messages": [message_data],
                    }
                    msg = {
                        "type": "chat.message",
                        "uid": uid,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "payload": payload,
                    }
                    self._publish(uid, msg)
                    self.logger.info("🟢 CHAT_MESSAGE_PUBLISHED uid=%s msg_id=%s channel=user:%s", uid, msg_id, uid)
                except Exception as e:
                    self.logger.error("🔴 CHAT_EVENT_ERROR uid=%s error=%s", uid, repr(e), exc_info=True)

            listener = ref.listen(_on_event)

            def _close():
                try:
                    if listener and hasattr(listener, "close"):
                        listener.close()
                    # 🆕 NOUVEAU : Désenregistrer du registre centralisé lors de la fermeture
                    self.logger.info("🔵 REGISTRY_CHAT_UNREGISTER uid=%s space=%s thread=%s", uid, space_code, thread_key)
                    try:
                        registry = get_registry_listeners()
                        unregister_result = registry.unregister_listener(
                            user_id=uid,
                            listener_type="chat",
                            space_code=space_code,
                            thread_key=thread_key
                        )
                        if unregister_result.get("success"):
                            self.logger.info("🟢 REGISTRY_CHAT_UNREGISTER_SUCCESS uid=%s space=%s thread=%s", 
                                           uid, space_code, thread_key)
                        else:
                            self.logger.warning("🟡 REGISTRY_CHAT_UNREGISTER_FAILED uid=%s error=%s", 
                                              uid, unregister_result.get("error"))
                    except Exception as e:
                        self.logger.error("🔴 REGISTRY_CHAT_UNREGISTER_ERROR uid=%s error=%s", uid, repr(e), exc_info=True)
                except Exception:
                    pass

            # 🆕 NOUVEAU : Enregistrer dans le registre centralisé
            self.logger.info("🔵 REGISTRY_CHAT_START uid=%s space=%s thread=%s mode=%s", uid, space_code, thread_key, chosen_mode)
            try:
                registry = get_registry_listeners()
                register_result = registry.register_listener(
                    user_id=uid,
                    listener_type="chat",
                    space_code=space_code,
                    thread_key=thread_key,
                    mode=chosen_mode
                )
                if register_result.get("success"):
                    self.logger.info(
                        "🟢 REGISTRY_CHAT_SUCCESS uid=%s space=%s thread=%s mode=%s listener_id=%s channel=%s",
                        uid, space_code, thread_key, chosen_mode, 
                        register_result.get("listener_id"),
                        register_result.get("channel_name")
                    )
                else:
                    # Si l'enregistrement échoue, continuer quand même (traçabilité optionnelle)
                    self.logger.warning(
                        "🟡 REGISTRY_CHAT_FAILED uid=%s space=%s thread=%s error=%s",
                        uid, space_code, thread_key, register_result.get("error")
                    )
            except Exception as e:
                # Ne pas bloquer si l'enregistrement échoue
                self.logger.error("🔴 REGISTRY_CHAT_ERROR uid=%s space=%s thread=%s error=%s", 
                                uid, space_code, thread_key, repr(e), exc_info=True)

            return _close
        except Exception as e:
            self.logger.error("chat_listener_error uid=%s error=%s", uid, repr(e))
            return None

    def _start_workflow_listener(self, uid: str) -> List[Callable[[], None]]:
        """Démarre le listener workflow pour un utilisateur donné.

        Surveille tous les documents dans clients/{uid}/task_manager/ pour:
        - Les changements dans document.initial_data (données de facture)
        - Les changements dans APBookeeper_step_status (étapes de workflow)

        Returns:
            List[Callable]: Liste des fonctions de désabonnement
        """
        unsubs: List[Callable[[], None]] = []

        try:
            self.logger.info("workflow_listener_start uid=%s enabled=%s", uid, self._workflow_enabled)
            
            if not self._workflow_enabled:
                self.logger.info("workflow_listener_disabled uid=%s reason=config", uid)
                return []

            # Initialiser le cache pour cet utilisateur
            cache_key = uid
            if cache_key not in self._workflow_cache:
                self._workflow_cache[cache_key] = {}
                self.logger.debug("workflow_cache_initialized uid=%s cache_key=%s", uid, cache_key)
            else:
                self.logger.debug("workflow_cache_exists uid=%s cache_key=%s", uid, cache_key)

            # Surveiller tous les documents dans task_manager
            task_manager_path = f"clients/{uid}/task_manager"
            self.logger.debug("workflow_listener_path uid=%s path=%s", uid, task_manager_path)
            
            task_manager_collection = (
                self.db.collection("clients")
                .document(uid)
                .collection("task_manager")
            )

            def on_task_manager_snapshot(docs, changes, read_time):
                try:
                    self.logger.debug("workflow_snapshot_received uid=%s docs_count=%s changes_count=%s",
                                    uid, len(docs), len(changes))
                    self._on_workflow_changes(uid, docs, changes, read_time)
                except Exception as e:
                    self.logger.error("workflow_snapshot_error uid=%s error=%s", uid, repr(e), exc_info=True)

            # Attacher le listener sur toute la collection task_manager
            unsub_workflow = task_manager_collection.on_snapshot(on_task_manager_snapshot)
            unsubs.append(unsub_workflow)

            self.logger.info("workflow_listener_attached uid=%s path=%s", uid, task_manager_path)

        except Exception as e:
            self.logger.error("workflow_listener_error uid=%s error=%s", uid, repr(e), exc_info=True)
            # Nettoyer les listeners en cas d'erreur
            for u in unsubs:
                try:
                    u()
                except Exception:
                    pass
            return []

        return unsubs

    def _on_workflow_changes(self, uid: str, docs, changes, read_time) -> None:
        """Traite les changements dans les documents task_manager."""
        try:
            self.logger.debug("workflow_changes_start uid=%s changes_count=%s", uid, len(changes))
            
            for change in changes:
                doc = change.document
                job_id = doc.id
                doc_data = doc.to_dict() or {}
                
                self.logger.debug("workflow_change_detected uid=%s job_id=%s change_type=%s", 
                                uid, job_id, change.type.name if hasattr(change, 'type') else 'unknown')

                # Extraire space_code si disponible dans le document (optionnel, pour information)
                space_code = doc_data.get("collection_id") or doc_data.get("space_code")
                
                self.logger.debug("workflow_doc_data uid=%s job_id=%s space_code=%s has_apbookeeper=%s has_document=%s",
                                uid, job_id, space_code,
                                "APBookeeper_step_status" in doc_data,
                                "document" in doc_data)

                # Traiter les changements de données de facture
                self._process_invoice_changes(uid, job_id, doc_data, space_code)

                # Traiter les changements d'étapes APBookeeper
                self._process_step_changes(uid, job_id, doc_data, space_code)
            
            self.logger.debug("workflow_changes_complete uid=%s", uid)

        except Exception as e:
            self.logger.error("workflow_changes_error uid=%s error=%s", uid, repr(e), exc_info=True)

    def _process_invoice_changes(self, uid: str, job_id: str, doc_data: dict, space_code: Optional[str] = None) -> None:
        """Traite les changements dans les données de facture."""
        try:
            self.logger.debug("invoice_changes_start uid=%s job_id=%s", uid, job_id)
            
            # Extraire les données de facture
            document = doc_data.get("document", {})
            initial_data = document.get("initial_data", {})

            if not initial_data:
                self.logger.debug("invoice_changes_no_data uid=%s job_id=%s reason=no_initial_data", uid, job_id)
                return

            # Champs de facture à surveiller
            invoice_fields = [
                "invoiceReference", "recipient", "invoiceDescription",
                "totalAmountDueVATExcluded", "totalAmountDueVATIncluded", "VATAmount",
                "recipientAddress", "dueDate", "sender", "invoiceDate",
                "currency", "VATPercentages", "sender_country",
                "account_number", "account_name"
            ]

            # Construire la clé de cache
            cache_key = f"{uid}_invoice_{job_id}"
            previous_data = self._workflow_cache.get(cache_key, {})
            
            self.logger.debug("invoice_changes_cache_check uid=%s job_id=%s cache_key=%s has_previous=%s",
                            uid, job_id, cache_key, bool(previous_data))

            # Détecter les changements
            invoice_changes = {}
            for field in invoice_fields:
                current_value = initial_data.get(field)
                previous_value = previous_data.get(field)

                if current_value != previous_value:
                    invoice_changes[field] = current_value
                    self.logger.debug("invoice_field_changed uid=%s job_id=%s field=%s previous=%s current=%s",
                                    uid, job_id, field, previous_value, current_value)

            # Publier seulement s'il y a des changements
            if invoice_changes:
                self.logger.info("workflow_invoice_changes uid=%s job_id=%s space_code=%s changes=%s", 
                               uid, job_id, space_code, list(invoice_changes.keys()))

                payload = {
                    "type": "workflow.invoice_update",
                    "uid": uid,
                    "job_id": job_id,
                    "space_code": space_code,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "invoice_changes": invoice_changes
                    }
                }
                
                self.logger.debug("invoice_changes_publishing uid=%s job_id=%s payload_size=%s",
                                uid, job_id, len(str(payload)))

                self._publish(uid, payload)

                # Mettre à jour le cache
                self._workflow_cache[cache_key] = initial_data.copy()
                self.logger.debug("invoice_changes_cache_updated uid=%s job_id=%s cache_key=%s",
                                uid, job_id, cache_key)
            else:
                self.logger.debug("invoice_changes_no_changes uid=%s job_id=%s", uid, job_id)

        except Exception as e:
            self.logger.error("invoice_changes_error uid=%s job_id=%s error=%s", uid, job_id, repr(e), exc_info=True)

    def _process_step_changes(self, uid: str, job_id: str, doc_data: dict, space_code: Optional[str] = None) -> None:
        """Traite les changements dans les étapes APBookeeper."""
        try:
            self.logger.debug("step_changes_start uid=%s job_id=%s", uid, job_id)
            
            # Extraire les données d'étapes
            step_status = doc_data.get("APBookeeper_step_status", {})

            # Gestion de la compatibilité: si c'est une string (ex: status final legacy), on l'encapsule
            if isinstance(step_status, str):
                self.logger.debug("step_changes_legacy_string_detected uid=%s job_id=%s val=%s", uid, job_id, step_status)
                step_status = {"status_label": step_status}

            # Vérifier que step_status est bien un dictionnaire
            if not isinstance(step_status, dict):
                # Si c'est une string (ex: statut de fin de job legacy), on l'ignore proprement
                if isinstance(step_status, str):
                    self.logger.debug("step_changes_legacy_string_status uid=%s job_id=%s value=%s", uid, job_id, step_status)
                    return

                self.logger.warning("step_changes_invalid_type uid=%s job_id=%s type=%s value=%s",
                                   uid, job_id, type(step_status).__name__, step_status)
                return

            if not step_status:
                self.logger.debug("step_changes_no_data uid=%s job_id=%s reason=no_APBookeeper_step_status", uid, job_id)
                return

            self.logger.debug("step_changes_data uid=%s job_id=%s steps_count=%s steps=%s",
                            uid, job_id, len(step_status), list(step_status.keys()))

            # Construire la clé de cache
            cache_key = f"{uid}_steps_{job_id}"
            previous_steps = self._workflow_cache.get(cache_key, {})
            
            self.logger.debug("step_changes_cache_check uid=%s job_id=%s cache_key=%s has_previous=%s previous_steps=%s",
                            uid, job_id, cache_key, bool(previous_steps), list(previous_steps.keys()) if previous_steps else [])

            # Détecter les changements d'étapes
            step_changes = {}
            for step_name, current_count in step_status.items():
                previous_count = previous_steps.get(step_name, 0)

                if current_count != previous_count:
                    step_changes[step_name] = current_count
                    self.logger.debug("step_changed uid=%s job_id=%s step=%s previous=%s current=%s",
                                    uid, job_id, step_name, previous_count, current_count)

            # Publier seulement s'il y a des changements
            if step_changes:
                self.logger.info("workflow_step_changes uid=%s job_id=%s space_code=%s changes=%s", 
                               uid, job_id, space_code, step_changes)

                payload = {
                    "type": "workflow.step_update",
                    "uid": uid,
                    "job_id": job_id,
                    "space_code": space_code,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "step_changes": {
                            "APBookeeper_step_status": step_changes
                        }
                    }
                }
                
                self.logger.debug("step_changes_publishing uid=%s job_id=%s payload_size=%s steps_changed=%s",
                                uid, job_id, len(str(payload)), len(step_changes))

                self._publish(uid, payload)

                # Mettre à jour le cache
                self._workflow_cache[cache_key] = step_status.copy()
                self.logger.debug("step_changes_cache_updated uid=%s job_id=%s cache_key=%s",
                                uid, job_id, cache_key)
            else:
                self.logger.debug("step_changes_no_changes uid=%s job_id=%s", uid, job_id)

        except Exception as e:
            self.logger.error("step_changes_error uid=%s job_id=%s error=%s", uid, job_id, repr(e), exc_info=True)

    # ========== Transaction Status Listeners ==========

    def start_transaction_status_listener(self, user_id: str, batch_id: str, initial_statuses: dict, callback=None) -> bool:
        """Démarre un listener de transaction status pour un batch spécifique.
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            batch_id (str): ID du batch de transactions
            initial_statuses (dict): Statuts initiaux des transactions {"transaction_id": "status", ...}
            callback: Ignoré côté microservice (paramètre pour compatibilité avec l'API Reflex)
            
        Returns:
            bool: True si le listener a été démarré avec succès, False sinon
        """
        if not self._transaction_listener_enabled:
            self.logger.info("transaction_listener_disabled user_id=%s batch_id=%s", user_id, batch_id)
            return False
        
        # Log informatif si un callback est passé (pour debugging/compatibilité)
        if callback is not None:
            self.logger.info("transaction_listener_callback_ignored user_id=%s batch_id=%s reason=microservice_uses_redis_pubsub", user_id, batch_id)
            
        try:
            listener_key = f"transaction_status_{user_id}_{batch_id}"
            
            # Vérifier si le listener existe déjà
            with self._lock:
                if listener_key in self._transaction_listeners:
                    self.logger.info("transaction_listener_already_exists key=%s", listener_key)
                    return True
            
            # Construire le chemin Firestore à écouter
            if user_id:
                firestore_path = f'clients/{user_id}/task_manager/{batch_id}'
            else:
                firestore_path = f"task_manager/{batch_id}"
            
            # Créer le listener Firestore
            document_ref = self.db.document(firestore_path)
            
            # Mémoriser les statuts initiaux pour détecter les changements
            listener_state = {
                'user_id': user_id,
                'batch_id': batch_id,
                'initial_statuses': initial_statuses.copy(),
                'acknowledged_statuses': {}
            }
            
            # Attacher le listener avec callback
            def on_snapshot(doc_snapshot, changes, read_time):
                self._handle_transaction_status_change(listener_state, doc_snapshot, changes, read_time)
            
            document_watch = document_ref.on_snapshot(on_snapshot)
            
            # Stocker le listener
            with self._lock:
                self._transaction_listeners[listener_key] = {
                    'watch': document_watch,
                    'state': listener_state
                }
            
            self.logger.info("transaction_listener_started path=%s key=%s", firestore_path, listener_key)
            return True
            
        except Exception as e:
            self.logger.error("transaction_listener_start_error user_id=%s batch_id=%s error=%s", user_id, batch_id, repr(e))
            return False

    def stop_transaction_status_listener(self, user_id: str, batch_id: str) -> bool:
        """Arrête le listener de statuts de transactions.
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            batch_id (str): ID du batch de transactions
            
        Returns:
            bool: True si le listener a été arrêté avec succès, False sinon
        """
        try:
            listener_key = f"transaction_status_{user_id}_{batch_id}"
            
            with self._lock:
                if listener_key not in self._transaction_listeners:
                    self.logger.info("transaction_listener_not_found key=%s", listener_key)
                    return False
                
                listener_info = self._transaction_listeners.pop(listener_key)
            
            # Arrêter le listener Firestore
            document_watch = listener_info.get('watch')
            if document_watch and callable(document_watch):
                document_watch()
            
            self.logger.info("transaction_listener_stopped key=%s", listener_key)
            return True
            
        except Exception as e:
            self.logger.error("transaction_listener_stop_error user_id=%s batch_id=%s error=%s", user_id, batch_id, repr(e))
            return False

    def _handle_transaction_status_change(self, listener_state: dict, doc_snapshot, changes, read_time):
        """Traite les changements de statuts et publie sur Redis.
        
        Args:
            listener_state (dict): État du listener contenant user_id, batch_id, etc.
            doc_snapshot: Snapshot du document Firestore
            changes: Changements détectés
            read_time: Timestamp de lecture
        """
        try:
            user_id = listener_state['user_id']
            batch_id = listener_state['batch_id']
            initial_statuses = listener_state['initial_statuses']
            acknowledged_statuses = listener_state['acknowledged_statuses']
            
            self.logger.info("transaction_status_change_triggered user_id=%s batch_id=%s", user_id, batch_id)
            
            for doc in doc_snapshot:
                current_data = doc.to_dict()
                
                # Vérifier la structure: jobs_data -> transactions
                if 'jobs_data' not in current_data:
                    self.logger.debug("transaction_status_no_jobs_data user_id=%s batch_id=%s", user_id, batch_id)
                    return
                
                jobs_data = current_data['jobs_data']
                if not jobs_data or len(jobs_data) == 0:
                    self.logger.debug("transaction_status_empty_jobs user_id=%s batch_id=%s", user_id, batch_id)
                    return
                
                # Prendre le premier job (généralement il n'y en a qu'un)
                job_data = jobs_data[0]
                current_transactions = job_data.get('transactions', [])
                
                changes_detected = False
                transaction_changes = {}
                
                # Parcourir les transactions actuelles
                for tx in current_transactions:
                    tx_id = str(tx.get('transaction_id', ''))
                    current_status = tx.get('status', '')
                    
                    if tx_id in initial_statuses:
                        old_status = initial_statuses[tx_id]
                        
                        # Vérifier si le statut a changé et n'a pas déjà été acquitté
                        if (current_status != old_status and 
                            acknowledged_statuses.get(tx_id) != current_status):
                            
                            if not changes_detected:
                                self.logger.info("transaction_status_changes_detected user_id=%s batch_id=%s", user_id, batch_id)
                                changes_detected = True
                            
                            self.logger.info("transaction_status_change user_id=%s batch_id=%s tx_id=%s old=%s new=%s", 
                                           user_id, batch_id, tx_id, old_status, current_status)
                            
                            # Mettre à jour le statut de référence
                            initial_statuses[tx_id] = current_status
                            # Enregistrer le changement
                            transaction_changes[tx_id] = current_status
                            # Marquer comme acquitté
                            acknowledged_statuses[tx_id] = current_status
                
                # Si des changements ont été détectés, publier sur Redis
                if transaction_changes:
                    self._publish_transaction_status_changes(user_id, batch_id, transaction_changes)
                    
        except Exception as e:
            self.logger.error("transaction_status_change_error user_id=%s batch_id=%s error=%s", 
                            listener_state.get('user_id', 'unknown'), 
                            listener_state.get('batch_id', 'unknown'), repr(e))
            import traceback
            traceback.print_exc()

    # ==================== WORKFLOW LISTENER PAR JOB (ON-DEMAND) ====================
    
    def start_workflow_listener_for_job(self, uid: str, job_id: str) -> bool:
        """
        Démarre un listener workflow pour un job spécifique (à la demande).
        
        Cette méthode permet d'activer la surveillance d'un seul document task_manager
        uniquement lorsque l'utilisateur ouvre la page EditForm pour ce job.
        
        Args:
            uid (str): User ID
            job_id (str): Job ID à surveiller
            
        Returns:
            bool: True si succès, False sinon
        """
        try:
            key = f"{uid}_{job_id}"
            
            # Vérifier si déjà actif
            with self._lock:
                if key in self._workflow_unsubs:
                    self.logger.info("workflow_listener_already_active uid=%s job_id=%s", uid, job_id)
                    return True
            
            self.logger.info("workflow_listener_start_for_job uid=%s job_id=%s", uid, job_id)
            
            # Surveiller UN SEUL document dans task_manager
            doc_ref = (
                self.db.collection("clients")
                .document(uid)
                .collection("task_manager")
                .document(job_id)
            )
            
            # Initialiser le cache pour ce job
            cache_key_invoice = f"{uid}_invoice_{job_id}"
            cache_key_steps = f"{uid}_steps_{job_id}"
            with self._lock:
                if cache_key_invoice not in self._workflow_cache:
                    self._workflow_cache[cache_key_invoice] = {}
                if cache_key_steps not in self._workflow_cache:
                    self._workflow_cache[cache_key_steps] = {}
            
            def on_job_snapshot(doc_snapshot, changes, read_time):
                """Callback pour un job spécifique"""
                try:
                    # google-cloud-firestore fournit souvent une LISTE de snapshots (même pour DocumentReference)
                    # signature: (doc_snapshot, changes, read_time)
                    # - doc_snapshot: List[DocumentSnapshot] ou DocumentSnapshot selon versions/contexts
                    snapshot = doc_snapshot
                    if isinstance(snapshot, list):
                        snapshot = snapshot[0] if snapshot else None

                    if not snapshot or not getattr(snapshot, "exists", False):
                        self.logger.debug("workflow_job_snapshot_empty uid=%s job_id=%s", uid, job_id)
                        return
                    
                    doc_data = snapshot.to_dict() or {}
                    space_code = doc_data.get("collection_id") or doc_data.get("space_code")
                    
                    self.logger.debug(
                        "workflow_job_change uid=%s job_id=%s has_apbookeeper=%s has_document=%s",
                        uid, job_id,
                        "APBookeeper_step_status" in doc_data,
                        "document" in doc_data
                    )
                    
                    # Traiter les changements
                    self._process_invoice_changes(uid, job_id, doc_data, space_code)
                    self._process_step_changes(uid, job_id, doc_data, space_code)
                    
                except Exception as e:
                    self.logger.error("workflow_job_snapshot_error uid=%s job_id=%s error=%s", 
                                    uid, job_id, repr(e), exc_info=True)
            
            # Attacher le listener sur le document spécifique
            unsub = doc_ref.on_snapshot(on_job_snapshot)
            
            # Sauvegarder le callback de désinscription
            with self._lock:
                self._workflow_unsubs[key] = [unsub]
            
            self.logger.info("workflow_listener_attached_for_job uid=%s job_id=%s", uid, job_id)
            return True
            
        except Exception as e:
            self.logger.error("workflow_listener_start_error uid=%s job_id=%s error=%s", 
                            uid, job_id, repr(e), exc_info=True)
            return False

    def stop_workflow_listener_for_job(self, uid: str, job_id: str) -> bool:
        """
        Arrête le listener workflow pour un job spécifique.
        
        Args:
            uid (str): User ID
            job_id (str): Job ID à arrêter
            
        Returns:
            bool: True si succès, False sinon
        """
        try:
            key = f"{uid}_{job_id}"
            
            with self._lock:
                unsubs = self._workflow_unsubs.get(key)
                if not unsubs:
                    self.logger.info("workflow_listener_not_active uid=%s job_id=%s", uid, job_id)
                    return False
                
                # Détacher le listener
                for unsub in unsubs:
                    try:
                        unsub()
                    except Exception as e:
                        self.logger.error("workflow_listener_detach_error uid=%s job_id=%s error=%s",
                                        uid, job_id, repr(e))
                
                # Supprimer de la registry
                del self._workflow_unsubs[key]
                
                # Nettoyer le cache
                cache_key_invoice = f"{uid}_invoice_{job_id}"
                cache_key_steps = f"{uid}_steps_{job_id}"
                self._workflow_cache.pop(cache_key_invoice, None)
                self._workflow_cache.pop(cache_key_steps, None)
            
            self.logger.info("workflow_listener_stopped_for_job uid=%s job_id=%s", uid, job_id)
            return True
            
        except Exception as e:
            self.logger.error("workflow_listener_stop_error uid=%s job_id=%s error=%s",
                            uid, job_id, repr(e), exc_info=True)
            return False

    # ==================== TRANSACTION STATUS LISTENER ====================

    def _publish_transaction_status_changes(self, user_id: str, batch_id: str, transaction_changes: dict):
        """Publie les changements de statuts sur Redis.
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            batch_id (str): ID du batch de transactions
            transaction_changes (dict): Changements de statuts {"transaction_id": "new_status", ...}
        """
        try:
            # Format du message conforme à la spec du microservice et compatible avec BusConsumer côté Reflex
            # Ce format est identique à celui utilisé par workflow_listener pour assurer la cohérence
            message = {
                "type": "transaction.status_change",
                "uid": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "batch_id": batch_id,
                    "transaction_changes": transaction_changes
                }
            }
            
            # Publier via la méthode existante qui gère Redis + WebSocket
            self._publish(user_id, message)
            
            self.logger.info("transaction_status_published user_id=%s batch_id=%s changes=%s", 
                           user_id, batch_id, list(transaction_changes.keys()))
            
        except Exception as e:
            self.logger.error("transaction_status_publish_error user_id=%s batch_id=%s error=%s", 
                            user_id, batch_id, repr(e))


def _doc_payload(doc: DocumentSnapshot) -> dict:
    data = doc.to_dict() or {}
    data["doc_id"] = doc.id
    return data


def _safe_iso(ts_value) -> str:
    """Convertit timestamp Firestore/str/datetime vers ISO8601 (UTC) pour JSON."""
    try:
        if ts_value is None:
            return ""
        if hasattr(ts_value, "to_datetime"):
            dt = ts_value.to_datetime()
        elif isinstance(ts_value, datetime):
            dt = ts_value
        elif isinstance(ts_value, str):
            try:
                # Accepte déjà ISO
                return ts_value
            except Exception:
                return ""
        else:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return ""


def _extract_additional_info_message(additional_info) -> str:
    try:
        if isinstance(additional_info, str) and additional_info:
            import json as _json

            try:
                info = _json.loads(additional_info)
            except Exception:
                return ""
        elif isinstance(additional_info, dict):
            info = additional_info
        else:
            return ""
        if info.get("error_message"):
            return str(info["error_message"]) or ""
        if info.get("message"):
            return str(info["message"]) or ""
        return ""
    except Exception:
        return ""


def _format_notification_item(notif: dict) -> dict:
    file_name = notif.get("file_name") or "Document"
    status = notif.get("status") or "info"
    job_id = notif.get("job_id") or ""
    additional_info = notif.get("additional_info", "{}")

    return {
      "message": f"{file_name} - {status}",
      "file_name": file_name,
      "collection_id": notif.get("collection_id", ""),
      "collection_name": notif.get("collection_name", ""),
      "url": f"/edit_form/{job_id}",
      "status": status,
      "read": bool(notif.get("read", False)),
      "doc_id": notif.get("doc_id", ""),
      "job_id": job_id,
      "file_id": notif.get("file_id", ""),
      "function_name": notif.get("function_name", ""),
      "timestamp": _safe_iso(notif.get("timestamp")),
      "additional_info": additional_info if isinstance(additional_info, str) else str(additional_info),
      "info_message": _extract_additional_info_message(additional_info),
    }


def _format_message_item(msg: dict) -> dict:
    """Formate un message direct au même schéma que MessageListener historique."""
    file_name = msg.get("file_name") or "Document"
    status = msg.get("status") or "info"
    job_id = msg.get("job_id") or ""
    additional_info = msg.get("additional_info", "{}")

    return {
      "message": f"{file_name} - {status}",
      "file_name": file_name,
      "collection_id": msg.get("collection_id", ""),
      "collection_name": msg.get("collection_name", ""),
      "url": f"/edit_form/{job_id}",
      "status": status,
      "doc_id": msg.get("doc_id", ""),
      "job_id": job_id,
      "file_id": msg.get("file_id", ""),
      "function_name": msg.get("function_name", ""),
      "timestamp": _safe_iso(msg.get("timestamp")),
      "additional_info": additional_info if isinstance(additional_info, str) else str(additional_info),
    }



def _get_rtdb_ref(path: str):
    try:
        from .firebase_client import get_firebase_app
    except Exception:
        # fallback import local
        from .firebase_client import get_firebase_app  # type: ignore[no-redef]
    app = get_firebase_app()
    url = os.getenv("FIREBASE_REALTIME_DB_URL", "https://pinnokio-gpt-default-rtdb.europe-west1.firebasedatabase.app/")
    return rtdb.reference(path, url=url, app=app)


