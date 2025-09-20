from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Header, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, Tuple, Callable
import json as _json
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
from .redis_client import get_redis
from .firebase_providers import get_firebase_management, get_firebase_realtime

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
    wcount = listeners_manager.workflow_listeners_count if listeners_manager else 0
    return {
        "status": "ok",
        "version": VERSION,
        "listeners_count": lcount,
        "workflow_listeners_count": wcount,
        "redis": redis_status,
        "uptime_s": uptime_s,
        "region": settings.aws_region_name,
    }


@app.get("/version")
def version():
    return {"version": VERSION}


@app.get("/readyz")
def readyz():
    try:
        r = get_redis()
        r.ping()
        return {"ok": True}
    except Exception:
        raise HTTPException(status_code=503, detail={"ok": False, "error": "redis_unavailable"})


@app.get("/debug")
def debug():
    report = {"redis": None, "gsm": None, "firestore": None, "workflow_listeners": None}

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

    # Information sur les workflow listeners
    try:
        if listeners_manager:
            with listeners_manager._lock:
                active_users = list(listeners_manager._workflow_unsubs.keys())
                cache_keys = list(listeners_manager._workflow_cache.keys())

            report["workflow_listeners"] = {
                "status": "ok",
                "enabled": listeners_manager._workflow_enabled,
                "active_count": len(active_users),
                "users": active_users[:10],  # Limiter à 10 pour éviter logs trop longs
                "cache_entries": len(cache_keys)
            }
        else:
            report["workflow_listeners"] = {"status": "manager_not_available"}
    except Exception as e:
        logger.error("debug_workflow_listeners status=error error=%s", repr(e))
        report["workflow_listeners"] = {"status": "error", "error": repr(e)}

    return report


# ===================== RPC (contrat applicatif) =====================

class RpcRequest(BaseModel):
    api_version: str = Field(..., examples=["v1"]) 
    method: str
    args: list[Any] = []
    kwargs: Dict[str, Any] = {}
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    reply_to: Optional[str] = None
    idempotency_key: str
    timeout_ms: Optional[int] = 15000
    trace_id: Optional[str] = None


class RpcResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: Dict[str, Any] | None = None


def _require_auth(authorization: str | None) -> None:
    expected = os.getenv("LISTENERS_SERVICE_TOKEN")
    if not expected:
        return  # Auth désactivée si non configurée (dev/local)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    token = authorization.split(" ", 1)[1]
    if token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_token")


def _idempotency_mark_if_new(key: str, ttl_s: int = 900) -> bool:
    r = get_redis()
    try:
        return bool(r.set(f"idemp:{key}", "1", nx=True, ex=ttl_s))
    except Exception:
        return True  # en cas d'erreur Redis, on laisse passer la requête


def _registry_register_user(user_id: str, session_id: str, backend_route: str | None) -> dict:
    r = get_redis()
    key = f"registry:user:{user_id}"
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "backend_route": backend_route or "",
        "last_seen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    r.hset(key, mapping=payload)
    r.expire(key, 24 * 3600)
    return payload


def _registry_unregister_session(session_id: str) -> bool:
    r = get_redis()
    cursor = 0
    removed = False
    pattern = "registry:user:*"
    while True:
        cursor, keys = r.scan(cursor=cursor, match=pattern, count=200)
        for k in keys:
            try:
                sid = r.hget(k, "session_id")
                if sid and sid.decode() == session_id:
                    r.delete(k)
                    removed = True
            except Exception:
                continue
        if cursor == 0:
            break
    return removed


def _resolve_method(method: str) -> Tuple[Callable[..., Any], str]:
    if method.startswith("FIREBASE_MANAGEMENT."):
        name = method.split(".", 1)[1]
        target = getattr(get_firebase_management(), name, None)
        if callable(target):
            return target, "FIREBASE_MANAGEMENT"
    if method.startswith("FIREBASE_REALTIME."):
        name = method.split(".", 1)[1]
        target = getattr(get_firebase_realtime(), name, None)
        if callable(target):
            return target, "FIREBASE_REALTIME"
    if method.startswith("REGISTRY."):
        name = method.split(".", 1)[1]
        if name == "register_user":
            return _registry_register_user, "REGISTRY"
        if name == "unregister_session":
            return _registry_unregister_session, "REGISTRY"
    raise KeyError(method)


def _debug_enabled() -> bool:
    try:
        return os.getenv("LISTENERS_DEBUG", "false").lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _preview(obj: Any, limit: int = 600) -> str:
    try:
        s = _json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        try:
            s = str(obj)
        except Exception:
            s = "<unprintable>"
    if len(s) > limit:
        return s[:limit] + "…"
    return s


def _idemp_disabled(method: str) -> bool:
    """Retourne True si l'idempotence est désactivée (globalement ou pour la méthode)."""
    try:
        if os.getenv("RPC_IDEMP_DISABLE", "false").lower() in ("1", "true", "yes", "on"):
            return True
        methods = os.getenv("RPC_IDEMP_DISABLE_METHODS", "")
        if methods:
            for m in methods.split(","):
                m = (m or "").strip()
                if m and m == method:
                    return True
        return False
    except Exception:
        return False


@app.post("/rpc", response_model=RpcResponse)
def rpc_endpoint(req: RpcRequest, authorization: str | None = Header(default=None, alias="Authorization")):
    t0 = time.time()
    if _debug_enabled():
        try:
            logger.info(
                "rpc_in method=%s args_n=%s kwargs_keys=%s uid=%s sid=%s trace_id=%s",
                req.method,
                len(req.args or []),
                list((req.kwargs or {}).keys()),
                req.user_id,
                req.session_id,
                req.trace_id,
            )
        except Exception:
            pass
    _require_auth(authorization)

    expected_api = os.getenv("RPC_API_VERSION", "v1")
    if req.api_version != expected_api:
        return RpcResponse(ok=False, error={"code": "INVALID_API_VERSION", "message": f"expected {expected_api}"})

    if not req.idempotency_key:
        return RpcResponse(ok=False, error={"code": "INVALID_ARGS", "message": "idempotency_key required"})

    idemp_disabled = _idemp_disabled(req.method)
    if not idemp_disabled:
        try:
            ttl = int(os.getenv("RPC_IDEMP_TTL", "900"))
        except Exception:
            ttl = 900
        try:
            logger.info(
                "rpc_idemp_check method=%s idemp_key=%s trace_id=%s uid=%s sid=%s",
                req.method,
                req.idempotency_key,
                req.trace_id,
                req.user_id,
                req.session_id,
            )
        except Exception:
            pass
        is_new = _idempotency_mark_if_new(req.idempotency_key, ttl)
        if not is_new:
            dt_ms = int((time.time() - t0) * 1000)
            logger.info(
                "rpc_duplicate method=%s idemp_key=%s ttl_s=%s dt_ms=%s trace_id=%s uid=%s sid=%s",
                req.method,
                req.idempotency_key,
                ttl,
                dt_ms,
                req.trace_id,
                req.user_id,
                req.session_id,
            )
            return RpcResponse(ok=True, data={"duplicate": True})
    else:
        try:
            logger.info(
                "rpc_idemp_skip method=%s trace_id=%s uid=%s sid=%s",
                req.method,
                req.trace_id,
                req.user_id,
                req.session_id,
            )
        except Exception:
            pass

    try:
        func, _ns = _resolve_method(req.method)
        try:
            if req.method == "FIREBASE_MANAGEMENT.get_beta_request_by_email":
                logger.info(
                    "rpc_beta_check_call trace_id=%s idemp_key=%s email=%s",
                    req.trace_id,
                    req.idempotency_key,
                    (req.kwargs or {}).get("email"),
                )
        except Exception:
            pass

        # Log spécialisé pour add_or_update_job_by_job_id
        try:
            if req.method == "FIREBASE_MANAGEMENT.add_or_update_job_by_job_id":
                logger.info(
                    "rpc_job_notification_call trace_id=%s uid=%s args_count=%s",
                    req.trace_id,
                    req.user_id,
                    len(req.args or [])
                )
        except Exception:
            pass

        result = func(*(req.args or []), **(req.kwargs or {}))
        dt_ms = int((time.time() - t0) * 1000)
        if _debug_enabled():
            try:
                logger.info(
                    "rpc_ok method=%s dt_ms=%s trace_id=%s uid=%s sid=%s data_preview=%s",
                    req.method,
                    dt_ms,
                    req.trace_id,
                    req.user_id,
                    req.session_id,
                    _preview(result),
                )
            except Exception:
                pass
        # Log spécialisé pour le flux Beta (sans modifier la logique)
        try:
            if req.method == "FIREBASE_MANAGEMENT.get_beta_request_by_email":
                if isinstance(result, dict):
                    shape = list(result.keys())
                    auth = result.get("authorized_access")
                else:
                    shape = type(result).__name__
                    auth = None
                logger.info(
                    "rpc_beta_check_result trace_id=%s email=%s shape=%s authorized_access=%s",
                    req.trace_id,
                    (req.kwargs or {}).get("email"),
                    shape,
                    auth,
                )
        except Exception:
            pass

        # Log spécialisé pour add_or_update_job_by_job_id
        try:
            if req.method == "FIREBASE_MANAGEMENT.add_or_update_job_by_job_id":
                logger.info(
                    "rpc_job_notification_result trace_id=%s uid=%s result=%s",
                    req.trace_id,
                    req.user_id,
                    result
                )
        except Exception:
            pass
        if req.reply_to:
            try:
                r = get_redis()
                r.publish(req.reply_to, _json.dumps({"ok": True, "data": result, "trace_id": req.trace_id}))
            except Exception:
                pass
        return RpcResponse(ok=True, data=result)
    except KeyError:
        dt_ms = int((time.time() - t0) * 1000)
        logger.error("rpc_error code=METHOD_NOT_FOUND method=%s dt_ms=%s trace_id=%s", req.method, dt_ms, req.trace_id)
        return RpcResponse(ok=False, error={"code": "METHOD_NOT_FOUND", "message": req.method})
    except TypeError as e:
        dt_ms = int((time.time() - t0) * 1000)
        logger.error("rpc_error code=INVALID_ARGS method=%s dt_ms=%s trace_id=%s msg=%s", req.method, dt_ms, req.trace_id, str(e))
        return RpcResponse(ok=False, error={"code": "INVALID_ARGS", "message": str(e)})
    except Exception as e:
        dt_ms = int((time.time() - t0) * 1000)
        logger.error("rpc_error code=INTERNAL method=%s dt_ms=%s trace_id=%s error=%s", req.method, dt_ms, req.trace_id, repr(e))
        return RpcResponse(ok=False, error={"code": "INTERNAL", "message": str(e)})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # Backend Reflex passera le uid via query string ?uid=...
        uid = ws.query_params.get("uid")
        space_code = ws.query_params.get("space_code")
        thread_key = ws.query_params.get("thread_key")
        chat_mode = ws.query_params.get("mode") or "auto"
        if not uid:
            await ws.close()
            return
        await hub.register(uid, ws)
        logger.info("ws_register_complete uid=%s", uid)
        # Démarre une tâche de heartbeat Firestore liée à cette connexion
        heartbeat_task = asyncio.create_task(_presence_heartbeat(uid))
        logger.info("heartbeat_task_started uid=%s", uid)

        # DEBUG: Forcer l'attachement des listeners pour test
        if listeners_manager:
            logger.info("force_user_watchers_test uid=%s", uid)
            try:
                listeners_manager._ensure_user_watchers(uid)
                logger.info("force_user_watchers_complete uid=%s", uid)
            except Exception as e:
                logger.error("force_user_watchers_error uid=%s error=%s", uid, repr(e))
        # Optionnel: attacher un watcher de chat si demandé
        try:
            if listeners_manager and space_code and thread_key:
                listeners_manager.start_chat_watcher(uid, space_code, thread_key, chat_mode)
        except Exception as e:
            logger.error("chat_watcher_attach_error uid=%s error=%s", uid, repr(e))
        while True:
            # Lectures éventuellement inutilisées (backend peut ne rien envoyer)
            await ws.receive_text()
    except WebSocketDisconnect as e:
        try:
            code = getattr(e, "code", None)
            reason = getattr(e, "reason", None)
            logger.info("ws_disconnect uid=%s code=%s reason=%s", ws.query_params.get("uid"), code, reason)
        except Exception:
            logger.info("ws_disconnect uid=%s", ws.query_params.get("uid"))
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
