import logging
import json
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import DocumentSnapshot

from .config import get_settings
from .firebase_client import get_firestore
from .redis_client import get_redis
from .ws_hub import hub
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
        self._lock = threading.Lock()
        self._workflow_enabled = self._get_workflow_config()

    @property
    def listeners_count(self) -> int:
        with self._lock:
            return len(self._user_unsubs)

    @property
    def workflow_listeners_count(self) -> int:
        with self._lock:
            return len(self._workflow_unsubs)

    def _get_workflow_config(self) -> bool:
        """Récupère la configuration du workflow listener depuis les variables d'environnement ou secrets."""
        try:
            # Vérifier d'abord les variables d'environnement
            workflow_enabled = os.getenv("WORKFLOW_LISTENER_ENABLED", "true").lower()
            return workflow_enabled in ("true", "1", "yes", "on")
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

    def _on_registry_snapshot(self, docs, changes, read_time) -> None:  # type: ignore[no-untyped-def]
        try:
            self.logger.info("registry_snapshot triggered changes_count=%s", len(changes))
            for change in changes:
                doc: DocumentSnapshot = change.document
                uid = doc.id
                online = _is_online_and_not_expired(doc)
                self.logger.info("registry_change uid=%s type=%s online=%s", uid, change.type.name, online)
                if change.type.name in ("ADDED", "MODIFIED"):
                    if online:
                        self.logger.info("registry_attach_trigger uid=%s", uid)
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
            self.logger.info("user_attach_skip uid=%s reason=already_attached", uid)
            return
        self.logger.info("user_attach_start uid=%s", uid)
        unsubs: List[Callable[[], None]] = []
        try:
            self.logger.info("user_attach_firebase_query uid=%s", uid)
            q = (
                self.db.collection("clients").document(uid)
                .collection("notifications")
                .where("read", "==", False)
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

            # Workflow Listener (surveillance des documents task_manager)
            if self._workflow_enabled:
                self.logger.info("user_attach_workflow_listener uid=%s", uid)
                workflow_unsubs = self._start_workflow_listener(uid)
                if workflow_unsubs:
                    unsubs.extend(workflow_unsubs)
                    with self._lock:
                        self._workflow_unsubs[uid] = workflow_unsubs
                    self.logger.info("user_attach_workflow_listener_attached uid=%s listeners_count=%s", uid, len(workflow_unsubs))

            with self._lock:
                self._user_unsubs[uid] = unsubs
            self.logger.info("user_attach_complete uid=%s listeners_count=%s", uid, len(unsubs))
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

        if unsubs or workflow_unsubs:
            self.logger.info("user_detach uid=%s reason=%s listeners=%s workflow=%s", uid, reason, len(unsubs), len(workflow_unsubs))

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

    def publish(self, uid: str, payload: dict) -> None:
        """Méthode publique pour publier des messages via Redis/WS"""
        self._publish(uid, payload)

    def _publish(self, uid: str, payload: dict) -> None:
        evt_type = str(payload.get("type", ""))
        # Canal par défaut (événements généraux par utilisateur)
        channel = f"{self.settings.channel_prefix}{uid}"
        # Canal dédié pour chat si configuré
        if evt_type.startswith("chat"):
            chat_prefix = os.getenv("LISTENERS_CHAT_CHANNEL_PREFIX", "chat:")
            sc = payload.get("payload", {}).get("space_code")
            tk = payload.get("payload", {}).get("thread_key")
            if sc and tk:
                channel = f"{chat_prefix}{uid}:{sc}:{tk}"
        try:
            self.redis.publish(channel, json.dumps(payload))
        except Exception as e:
            self.logger.error("redis_publish_error uid=%s error=%s", uid, repr(e))
        # WS (best-effort): utiliser une diffusion thread-safe vers la loop serveur
        try:
            hub.broadcast_threadsafe(uid, payload)
        except Exception as e:
            self.logger.error("ws_broadcast_error uid=%s error=%s", uid, repr(e))
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
                .where("read", "==", False)
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
        - Si mode explicite vaut "chats" ou "job_chats", on l'utilise tel quel.
        - Si mode absent/"auto"/invalide: on tente d'abord chats/, puis fallback job_chats/.
        """
        try:
            selected_mode = (mode or "auto").strip()
            candidate_modes: List[str]
            if selected_mode in ("chats", "job_chats"):
                candidate_modes = [selected_mode]
            else:
                candidate_modes = ["chats", "job_chats"]

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

            self.logger.info("chat_path_resolved uid=%s space=%s thread=%s requested_mode=%s chosen_mode=%s", uid, space_code, thread_key, mode, chosen_mode)

            def _on_event(event):
                try:
                    if getattr(event, "event_type", None) != "put":
                        return
                    if not (event.data and event.path != "/" and isinstance(event.data, dict)):
                        return
                    msg_id = event.path.lstrip("/")
                    message_data = {"id": msg_id, **event.data}
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
                except Exception as e:
                    self.logger.error("chat_event_error uid=%s error=%s", uid, repr(e))

            listener = ref.listen(_on_event)

            def _close():
                try:
                    if listener and hasattr(listener, "close"):
                        listener.close()
                except Exception:
                    pass

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
            self.logger.info("workflow_listener_start uid=%s", uid)

            # Initialiser le cache pour cet utilisateur
            cache_key = uid
            if cache_key not in self._workflow_cache:
                self._workflow_cache[cache_key] = {}

            # Surveiller tous les documents dans task_manager
            task_manager_collection = (
                self.db.collection("clients")
                .document(uid)
                .collection("task_manager")
            )

            def on_task_manager_snapshot(docs, changes, read_time):
                try:
                    self._on_workflow_changes(uid, docs, changes, read_time)
                except Exception as e:
                    self.logger.error("workflow_snapshot_error uid=%s error=%s", uid, repr(e))

            # Attacher le listener sur toute la collection task_manager
            unsub_workflow = task_manager_collection.on_snapshot(on_task_manager_snapshot)
            unsubs.append(unsub_workflow)

            self.logger.info("workflow_listener_attached uid=%s", uid)

        except Exception as e:
            self.logger.error("workflow_listener_error uid=%s error=%s", uid, repr(e))
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
            for change in changes:
                doc = change.document
                job_id = doc.id
                doc_data = doc.to_dict() or {}

                # Traiter les changements de données de facture
                self._process_invoice_changes(uid, job_id, doc_data)

                # Traiter les changements d'étapes APBookeeper
                self._process_step_changes(uid, job_id, doc_data)

        except Exception as e:
            self.logger.error("workflow_changes_error uid=%s error=%s", uid, repr(e))

    def _process_invoice_changes(self, uid: str, job_id: str, doc_data: dict) -> None:
        """Traite les changements dans les données de facture."""
        try:
            # Extraire les données de facture
            document = doc_data.get("document", {})
            initial_data = document.get("initial_data", {})

            if not initial_data:
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

            # Détecter les changements
            invoice_changes = {}
            for field in invoice_fields:
                current_value = initial_data.get(field)
                previous_value = previous_data.get(field)

                if current_value != previous_value:
                    invoice_changes[field] = current_value

            # Publier seulement s'il y a des changements
            if invoice_changes:
                self.logger.info("workflow_invoice_changes uid=%s job_id=%s changes=%s", uid, job_id, list(invoice_changes.keys()))

                payload = {
                    "type": "workflow.invoice_update",
                    "uid": uid,
                    "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "invoice_changes": invoice_changes
                    }
                }

                self._publish(uid, payload)

                # Mettre à jour le cache
                self._workflow_cache[cache_key] = initial_data.copy()

        except Exception as e:
            self.logger.error("invoice_changes_error uid=%s job_id=%s error=%s", uid, job_id, repr(e))

    def _process_step_changes(self, uid: str, job_id: str, doc_data: dict) -> None:
        """Traite les changements dans les étapes APBookeeper."""
        try:
            # Extraire les données d'étapes
            step_status = doc_data.get("APBookeeper_step_status", {})

            if not step_status:
                return

            # Construire la clé de cache
            cache_key = f"{uid}_steps_{job_id}"
            previous_steps = self._workflow_cache.get(cache_key, {})

            # Détecter les changements d'étapes
            step_changes = {}
            for step_name, current_count in step_status.items():
                previous_count = previous_steps.get(step_name, 0)

                if current_count != previous_count:
                    step_changes[step_name] = current_count

            # Publier seulement s'il y a des changements
            if step_changes:
                self.logger.info("workflow_step_changes uid=%s job_id=%s changes=%s", uid, job_id, step_changes)

                payload = {
                    "type": "workflow.step_update",
                    "uid": uid,
                    "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "step_changes": {
                            "APBookeeper_step_status": step_changes
                        }
                    }
                }

                self._publish(uid, payload)

                # Mettre à jour le cache
                self._workflow_cache[cache_key] = step_status.copy()

        except Exception as e:
            self.logger.error("step_changes_error uid=%s job_id=%s error=%s", uid, job_id, repr(e))


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


