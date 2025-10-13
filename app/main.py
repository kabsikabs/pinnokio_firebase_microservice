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
from .chroma_vector_service import get_chroma_vector_service

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
            # NOUVEAU: Utiliser le wrapper transparent (API identique)
            from .registry_wrapper import get_registry_wrapper
            return get_registry_wrapper().register_user, "REGISTRY"
        if name == "unregister_session":
            # NOUVEAU: Utiliser le wrapper transparent (API identique)
            from .registry_wrapper import get_registry_wrapper
            return get_registry_wrapper().unregister_session, "REGISTRY"
        # 🆕 NOUVEAU: Méthodes du registre des listeners (sous REGISTRY.*)
        if name in ["check_listener_status", "register_listener", "unregister_listener", 
                    "list_user_listeners", "cleanup_user_listeners", "update_listener_heartbeat"]:
            from .registry_listeners import get_registry_listeners
            target = getattr(get_registry_listeners(), name, None)
            if callable(target):
                return target, "REGISTRY"
    if method.startswith("CHROMA_VECTOR."):
        name = method.split(".", 1)[1]
        target = getattr(get_chroma_vector_service(), name, None)
        if callable(target):
            return target, "CHROMA_VECTOR"
    if method.startswith("TASK."):
        name = method.split(".", 1)[1]
        if name == "start_document_analysis":
            return _start_document_analysis_task, "TASK"
        if name == "start_vector_computation":
            return _start_vector_computation_task, "TASK"
        if name == "start_llm_conversation":
            return _start_llm_conversation_task, "TASK"
        if name == "get_task_status":
            return _get_task_status, "TASK"
    if method.startswith("UNIFIED_REGISTRY."):
        name = method.split(".", 1)[1]
        from .unified_registry import get_unified_registry
        target = getattr(get_unified_registry(), name, None)
        if callable(target):
            return target, "UNIFIED_REGISTRY"
    if method.startswith("LLM."):
        name = method.split(".", 1)[1]
        from .llm_service import get_llm_manager
        if name == "initialize_session":
            def _sync_wrapper(**kwargs):
                # Exécuter la coroutine dans l'event loop
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Si on est déjà dans un event loop, créer une nouvelle tâche
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, get_llm_manager().initialize_session(**kwargs))
                            return future.result()
                    else:
                        return asyncio.run(get_llm_manager().initialize_session(**kwargs))
                except RuntimeError:
                    # Fallback si pas d'event loop
                    return asyncio.run(get_llm_manager().initialize_session(**kwargs))
            return _sync_wrapper, "LLM"
        if name == "send_message":
            # Version async directe - pas de wrapper synchrone pour éviter l'annulation des tâches
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().send_message(**kwargs)
            return _async_wrapper, "LLM"
        if name == "update_context":
            def _sync_wrapper(**kwargs):
                # Exécuter la coroutine dans l'event loop
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Si on est déjà dans un event loop, créer une nouvelle tâche
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, get_llm_manager().update_context(**kwargs))
                            return future.result()
                    else:
                        return asyncio.run(get_llm_manager().update_context(**kwargs))
                except RuntimeError:
                    # Fallback si pas d'event loop
                    return asyncio.run(get_llm_manager().update_context(**kwargs))
            return _sync_wrapper, "LLM"
        if name == "load_chat_history":
            def _sync_wrapper(**kwargs):
                # Exécuter la coroutine dans l'event loop
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Si on est déjà dans un event loop, créer une nouvelle tâche
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, get_llm_manager().load_chat_history(**kwargs))
                            return future.result()
                    else:
                        return asyncio.run(get_llm_manager().load_chat_history(**kwargs))
                except RuntimeError:
                    # Fallback si pas d'event loop
                    return asyncio.run(get_llm_manager().load_chat_history(**kwargs))
            return _sync_wrapper, "LLM"
        if name == "stop_streaming":
            def _sync_wrapper(**kwargs):
                # Exécuter la coroutine dans l'event loop
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Si on est déjà dans un event loop, créer une nouvelle tâche
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, get_llm_manager().stop_streaming(**kwargs))
                            return future.result()
                    else:
                        return asyncio.run(get_llm_manager().stop_streaming(**kwargs))
                except RuntimeError:
                    # Fallback si pas d'event loop
                    return asyncio.run(get_llm_manager().stop_streaming(**kwargs))
            return _sync_wrapper, "LLM"
        if name == "flush_chat_history":
            def _sync_wrapper(**kwargs):
                # Exécuter la coroutine dans l'event loop
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Si on est déjà dans un event loop, créer une nouvelle tâche
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, get_llm_manager().flush_chat_history(**kwargs))
                            return future.result()
                    else:
                        return asyncio.run(get_llm_manager().flush_chat_history(**kwargs))
                except RuntimeError:
                    # Fallback si pas d'event loop
                    return asyncio.run(get_llm_manager().flush_chat_history(**kwargs))
            return _sync_wrapper, "LLM"
    if method.startswith("REGISTRY_LISTENERS."):
        name = method.split(".", 1)[1]
        from .registry_listeners import get_registry_listeners
        target = getattr(get_registry_listeners(), name, None)
        if callable(target):
            return target, "REGISTRY_LISTENERS"
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
async def rpc_endpoint(req: RpcRequest, authorization: str | None = Header(default=None, alias="Authorization")):
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
        # Log idempotence uniquement en mode debug
        if _debug_enabled():
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
            # Log duplicate uniquement si > 100ms (évite spam)
            if dt_ms > 100:
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
    # Pas de log pour rpc_idemp_skip (trop verbeux)

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

        # Exécuter la fonction (sync ou async)
        import inspect
        if inspect.iscoroutinefunction(func):
            result = await func(*(req.args or []), **(req.kwargs or {}))
        else:
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
        # Log uniquement en mode debug
        if _debug_enabled():
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

        # ANCIEN système (maintenu pour compatibilité)
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
        
        # NOUVEAU système (si activé)
        try:
            from .registry_wrapper import get_registry_wrapper
            wrapper = get_registry_wrapper()
            if wrapper.unified_enabled:
                wrapper.update_heartbeat(uid)
        except Exception as e:
            # Erreur silencieuse pour ne pas impacter l'ancien système
            logger.debug("unified_heartbeat_error uid=%s error=%s", uid, repr(e))
        
        # Log uniquement en mode debug (évite spam)
        if _debug_enabled():
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


# ===== Gestion des tâches parallèles =====

def _start_document_analysis_task(user_id: str, document_data: dict, job_id: str) -> dict:
    """Démarre une tâche d'analyse de document."""
    try:
        from .computation_tasks import compute_document_analysis
        
        task = compute_document_analysis.delay(user_id, document_data, job_id)
        return {
            "success": True,
            "task_id": f"doc_analysis_{job_id}",
            "celery_task_id": task.id,
            "status": "queued",
            "job_id": job_id
        }
    except Exception as e:
        logger.error("start_document_analysis_error user_id=%s job_id=%s error=%s", user_id, job_id, repr(e))
        return {
            "success": False,
            "error": str(e),
            "job_id": job_id
        }

def _start_vector_computation_task(user_id: str, documents: list, collection_name: str) -> dict:
    """Démarre une tâche de calcul vectoriel."""
    try:
        from .computation_tasks import compute_vector_embeddings
        
        task = compute_vector_embeddings.delay(user_id, documents, collection_name)
        return {
            "success": True,
            "task_id": f"embeddings_{collection_name}",
            "celery_task_id": task.id,
            "status": "queued",
            "collection_name": collection_name
        }
    except Exception as e:
        logger.error("start_vector_computation_error user_id=%s collection=%s error=%s", user_id, collection_name, repr(e))
        return {
            "success": False,
            "error": str(e),
            "collection_name": collection_name
        }

def _start_llm_conversation_task(
    user_id: str, 
    company_id: str, 
    prompt: str, 
    conversation_id: str = None,
    model: str = "gpt-4",
    temperature: float = 0.7
) -> dict:
    """Démarre une tâche de conversation LLM."""
    try:
        from .computation_tasks import process_llm_conversation
        import uuid
        
        if not conversation_id:
            conversation_id = f"conv_{uuid.uuid4().hex[:12]}"
        
        task = process_llm_conversation.delay(
            conversation_id=conversation_id,
            user_id=user_id,
            company_id=company_id,
            prompt=prompt,
            model=model,
            temperature=temperature
        )
        
        return {
            "success": True,
            "conversation_id": conversation_id,
            "task_id": f"llm_{conversation_id}",
            "celery_task_id": task.id,
            "status": "queued"
        }
    except Exception as e:
        logger.error("start_llm_conversation_error user_id=%s company_id=%s error=%s", user_id, company_id, repr(e))
        return {
            "success": False,
            "error": str(e)
        }

def _get_task_status(task_id: str) -> dict:
    """Récupère le statut d'une tâche."""
    try:
        from .unified_registry import get_unified_registry
        
        registry = get_unified_registry()
        task_registry = registry.get_task_registry(task_id)
        
        if not task_registry:
            return {"success": False, "error": "Tâche non trouvée"}
        
        return {
            "success": True,
            "task_id": task_id,
            "status": task_registry["task_info"]["status"],
            "progress": task_registry["progress"],
            "created_at": task_registry["task_info"]["created_at"],
            "isolation": task_registry["isolation"]
        }
    except Exception as e:
        logger.error("get_task_status_error task_id=%s error=%s", task_id, repr(e))
        return {
            "success": False,
            "error": str(e),
            "task_id": task_id
        }
