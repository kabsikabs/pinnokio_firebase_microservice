from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import os
import time
import logging
import asyncio

from .config import get_settings
from .logging_setup import configure_logging
from .tools.g_cred import get_secret
from .firebase_client import get_firestore
from .listeners_manager import ListenersManager
from .ws_hub import hub
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

try:
    import redis  # type: ignore
except Exception:
    redis = None

configure_logging()
logger = logging.getLogger("listeners.app")

app = FastAPI(title="listeners-service")

START_TIME = time.time()
VERSION = os.getenv("SERVICE_VERSION", "0.1.0")

settings = get_settings()
logger.info(f"service_start version={VERSION} region={settings.aws_region_name}")
logger.info("redis_mode mode=%s host=%s port=%s tls=%s", "local" if settings.use_local_redis else "cloud", settings.redis_host, settings.redis_port, settings.redis_tls)

redis_client = None
if redis and settings.redis_host:
    try:
        redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            ssl=settings.redis_tls,
            db=settings.redis_db,
            socket_connect_timeout=2,
        )
        redis_client.ping()
        logger.info("redis_connect status=ok host=%s port=%s tls=%s", settings.redis_host, settings.redis_port, settings.redis_tls)
    except Exception as e:
        logger.error("redis_connect status=error host=%s port=%s error=%s", settings.redis_host, settings.redis_port, repr(e))
        redis_client = None
else:
    logger.info("redis_connect status=disabled reason=missing_config")

listeners_manager: ListenersManager | None = None


@app.on_event("startup")
def on_startup():
    global listeners_manager
    try:
        listeners_manager = ListenersManager()
        listeners_manager.start()
        logger.info("listeners_manager status=started")
    except Exception as e:
        listeners_manager = None
        logger.error("listeners_manager status=error error=%s", repr(e))


@app.on_event("shutdown")
def on_shutdown():
    try:
        if listeners_manager:
            listeners_manager.stop()
            logger.info("listeners_manager status=stopped")
    except Exception as e:
        logger.error("listeners_manager_stop status=error error=%s", repr(e))


@app.get("/healthz")
def healthz():
    redis_status = "disabled"
    if redis_client:
        try:
            redis_client.ping()
            redis_status = "ok"
        except Exception as e:
            logger.error("redis_ping status=error error=%s", repr(e))
            redis_status = "error"
    uptime_s = int(time.time() - START_TIME)
    lcount = listeners_manager.listeners_count if listeners_manager else 0
    return {
        "status": "ok",
        "version": VERSION,
        "listeners_count": lcount,
        "redis": redis_status,
        "uptime_s": uptime_s,
        "region": settings.aws_region_name,
    }


@app.get("/version")
def version():
    return {"version": VERSION}


@app.get("/debug")
def debug():
    report = {"redis": None, "gsm": None, "firestore": None}

    try:
        if redis_client:
            redis_client.ping()
            report["redis"] = {"status": "ok"}
        else:
            report["redis"] = {"status": "disabled"}
    except Exception as e:
        logger.error("debug_redis status=error error=%s", repr(e))
        report["redis"] = {"status": "error", "error": repr(e)}

    try:
        name = os.getenv("AWS_SECRET_NAME")
        if name:
            _ = get_secret(name)
            report["gsm"] = {"status": "ok", "secret": name}
        else:
            report["gsm"] = {"status": "skipped"}
    except Exception as e:
        logger.error("debug_gsm status=error error=%s", repr(e))
        report["gsm"] = {"status": "error", "error": repr(e)}

    try:
        db = get_firestore()
        _ = [c.id for c in db.collections()]
        report["firestore"] = {"status": "ok"}
    except Exception as e:
        logger.error("debug_firestore status=error error=%s", repr(e))
        report["firestore"] = {"status": "error", "error": repr(e)}

    return report


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # Backend Reflex passera le uid via query string ?uid=...
        uid = ws.query_params.get("uid")
        space_code = ws.query_params.get("space_code")
        thread_key = ws.query_params.get("thread_key")
        chat_mode = ws.query_params.get("mode") or "job_chats"
        if not uid:
            await ws.close()
            return
        await hub.register(uid, ws)
        # Démarre une tâche de heartbeat Firestore liée à cette connexion
        heartbeat_task = asyncio.create_task(_presence_heartbeat(uid))
        # Optionnel: attacher un watcher de chat si demandé
        try:
            if listeners_manager and space_code and thread_key:
                listeners_manager.start_chat_watcher(uid, space_code, thread_key, chat_mode)
        except Exception as e:
            logger.error("chat_watcher_attach_error uid=%s error=%s", uid, repr(e))
        while True:
            # Lectures éventuellement inutilisées (backend peut ne rien envoyer)
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("ws_error error=%s", repr(e))
    finally:
        try:
            uid = ws.query_params.get("uid")
            if uid:
                await hub.unregister(uid, ws)
                # Arrête le heartbeat et marque l'utilisateur offline
                try:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                except Exception:
                    pass
                await _set_presence(uid, status="offline")
        except Exception:
            pass


# ===== Présence / Heartbeat Firestore =====
async def _set_presence(uid: str, status: str = "online", ttl_seconds: int | None = None) -> None:
    """Met à jour le document listeners_registry/{uid}.

    Écrit status, heartbeat_at (SERVER_TIMESTAMP) et ttl_seconds si fourni.
    """
    try:
        if ttl_seconds is None:
            try:
                ttl_seconds = int(os.getenv("LISTENERS_TTL_SECONDS", "90"))
            except Exception:
                ttl_seconds = 90

        db = get_firestore()
        doc = db.collection("listeners_registry").document(uid)

        def _write():
            payload = {
                "status": status,
                "heartbeat_at": SERVER_TIMESTAMP,
            }
            if ttl_seconds is not None:
                payload["ttl_seconds"] = int(ttl_seconds)
            doc.set(payload, merge=True)

        await asyncio.to_thread(_write)
        logger.info("presence_update uid=%s status=%s ttl=%s", uid, status, ttl_seconds)
    except Exception as e:
        logger.error("presence_update_error uid=%s error=%s", uid, repr(e))


async def _presence_heartbeat(uid: str) -> None:
    """Boucle de heartbeat tant que la connexion WebSocket est ouverte."""
    try:
        try:
            interval = int(os.getenv("LISTENERS_HEARTBEAT_INTERVAL", "45"))
        except Exception:
            interval = 45
        try:
            ttl_seconds = int(os.getenv("LISTENERS_TTL_SECONDS", "90"))
        except Exception:
            ttl_seconds = 90

        # Premier battement immédiat
        await _set_presence(uid, status="online", ttl_seconds=ttl_seconds)

        while True:
            await asyncio.sleep(interval)
            await _set_presence(uid, status="online", ttl_seconds=ttl_seconds)
    except asyncio.CancelledError:
        # Sortie silencieuse sur annulation; le finally du WS marque offline
        pass
    except Exception as e:
        logger.error("presence_heartbeat_error uid=%s error=%s", uid, repr(e))
