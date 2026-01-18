from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi import Header, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, Tuple, Callable, List
import json as _json
import base64
import requests
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
from . import runtime as runtime_state
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
async def on_startup():
    global listeners_manager
    try:
        listeners_manager = ListenersManager()
        listeners_manager.start()
        runtime_state.listeners_manager = listeners_manager
        logger.info("listeners_manager status=started")
    except Exception as e:
        listeners_manager = None
        runtime_state.listeners_manager = None
        logger.error("listeners_manager status=error error=%s", repr(e))

    # ⭐ NOUVEAU: Démarrer le scheduler CRON
    try:
        from .cron_scheduler import get_cron_scheduler
        scheduler = get_cron_scheduler()
        await scheduler.start()
        logger.info("cron_scheduler status=started")
    except Exception as e:
        logger.error("cron_scheduler status=error error=%s", repr(e))


@app.on_event("shutdown")
async def on_shutdown():
    try:
        if listeners_manager:
            listeners_manager.stop()
            logger.info("listeners_manager status=stopped")
    except Exception as e:
        logger.error("listeners_manager_stop status=error error=%s", repr(e))
    finally:
        runtime_state.listeners_manager = None

    # ⭐ NOUVEAU: Arrêter le scheduler CRON
    try:
        from .cron_scheduler import get_cron_scheduler
        scheduler = get_cron_scheduler()
        await scheduler.stop()
        logger.info("cron_scheduler status=stopped")
    except Exception as e:
        logger.error("cron_scheduler_stop status=error error=%s", repr(e))


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


@app.get("/ws-metrics")
def ws_metrics():
    """Endpoint pour consulter les métriques de déconnexion WebSocket."""
    try:
        from .ws_metrics import get_ws_metrics
        metrics = get_ws_metrics()
        return {
            "status": "ok",
            "metrics": metrics.get_summary()
        }
    except Exception as e:
        logger.error("ws_metrics_error error=%s", repr(e))
        return {
            "status": "error",
            "error": repr(e)
        }


# ═══════════════════════════════════════════════════════════════
# GOOGLE AUTH CALLBACK (BACKEND)
# ═══════════════════════════════════════════════════════════════

@app.get("/google_auth_callback/", response_class=HTMLResponse)
async def google_auth_callback(request: Request):
    """
    Callback pour l'authentification Google OAuth2 initiée par le backend/agents.
    
    Ce endpoint :
    1. Reçoit le code d'autorisation et le state
    2. Décode le state pour identifier l'utilisateur et le contexte
    3. Échange le code contre des tokens (Access + Refresh)
    4. Met à jour les credentials dans Firebase
    5. Notifie l'agent en attente via le système de chat
    """
    try:
        params = request.query_params
        code = params.get('code')
        state_str = params.get('state')
        error = params.get('error')
        
        if error:
            logger.error(f"google_auth_callback_error error={error}")
            return HTMLResponse(content=f"<h1>Erreur d'authentification</h1><p>{error}</p>", status_code=400)
            
        if not code or not state_str:
            return HTMLResponse(content="<h1>Paramètres manquants</h1><p>Code ou State manquant.</p>", status_code=400)
            
        # 1. Décoder le state
        try:
            try:
                decoded_state = base64.b64decode(state_str).decode('utf-8')
                state = _json.loads(decoded_state)
            except:
                state = _json.loads(state_str)
                
            logger.info(f"google_auth_callback state={state}")
            
            user_id = state.get('user_id')
            job_id = state.get('job_id')
            source = state.get('source')
            communication_mode = state.get('communication_mode', 'google_chat')
            redirect_uri = state.get('redirect_uri')
            chat_id = state.get('chat_id')  # ✅ RÉCUPÉRATION DU CHAT_ID du state OAuth
            
            if not user_id:
                raise ValueError("user_id manquant dans le state")
                
        except Exception as e:
            logger.error(f"google_auth_callback_state_error error={e}")
            return HTMLResponse(content=f"<h1>Erreur de contexte</h1><p>State invalide: {e}</p>", status_code=400)
            
        # 2. Récupérer la configuration client depuis Firebase
        # On récupère le token actuel pour extraire client_id/secret
        fb_user = get_firebase_management()
        fb_user.user_id = user_id
        
        try:
            creds_info = fb_user.user_app_permission_token()
        except:
            creds_info = {}
        
        client_id = creds_info.get('client_id')
        client_secret = creds_info.get('client_secret')
        token_uri = creds_info.get('token_uri', 'https://oauth2.googleapis.com/token')
        
        if not client_id or not client_secret:
             # Fallback: Essayer de récupérer depuis les secrets globaux
             try:
                app_creds = _json.loads(get_secret("pinnokio_google_client_secret"))
                client_id = app_creds.get('web', {}).get('client_id')
                client_secret = app_creds.get('web', {}).get('client_secret')
             except:
                pass
                
        if not client_id or not client_secret:
            return HTMLResponse(content="<h1>Erreur Configuration</h1><p>Client ID ou Secret introuvable.</p>", status_code=500)

        # 3. Échanger le code contre les tokens
        token_data = {
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code'
        }
        
        logger.info(f"google_auth_exchange uri={redirect_uri}")
        
        response = requests.post(token_uri, data=token_data)
        
        if response.status_code != 200:
            logger.error(f"google_auth_exchange_failed status={response.status_code} body={response.text}")
            return HTMLResponse(content=f"<h1>Erreur Échange Token</h1><p>{response.text}</p>", status_code=400)
            
        tokens = response.json()
        
        # 4. Mettre à jour Firebase
        from datetime import datetime, timedelta
        
        update_payload = {
            'token': tokens.get('access_token'),
            'expiry': (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat(),
        }
        
        if 'refresh_token' in tokens:
            update_payload['refresh_token'] = tokens['refresh_token']
            
        # Sauvegarder via Firestore
        db = get_firestore()
        try:
            # Essayer d'abord de mettre à jour via user_param (structure probable)
            user_param_ref = db.collection('users_param').document(user_id)
            user_param_ref.set({'token_data': update_payload}, merge=True)
            logger.info(f"google_auth_firebase_update success user={user_id}")
        except Exception as e:
            logger.error(f"google_auth_firebase_update_error {e}")
            return HTMLResponse(content=f"<h1>Erreur Sauvegarde</h1><p>{e}</p>", status_code=500)
            
        # 5. Notifier l'agent en attente via le canal approprié
        if communication_mode:
            try:
                context_params = state.get('context_params', {})
                message_text = "✅ Authentification Google Drive réussie ! Les accès sont à jour. TERMINATE"
                
                logger.info(f"google_auth_notify mode={communication_mode} params={context_params.keys()}")
                
                # --- TELEGRAM (ROUTAGE INTERNE) ---
                if communication_mode == 'telegram':
                    # 1. Déterminer l'environnement (LOCAL vs PROD)
                    env = os.getenv('ENVIRONMENT', 'LOCAL')
                    
                    # 2. Définir Base URL
                    if env == 'LOCAL':
                        base_url = 'http://127.0.0.1'
                    else:
                        base_url = os.getenv('PINNOKIO_AWS_URL', 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com')
                    
                    # 3. Définir Target URL en fonction de la source
                    target_url = ""
                    
                    if source in ['filemanager', 'router', 'onboarding', 'filemanager_agent']:
                        port = ":8080" if env == 'LOCAL' else ""
                        target_url = f"{base_url}{port}/router_webhook/telegram-response"
                        
                    elif source == 'apbookeeper':
                        port = ":8081" if env == 'LOCAL' else ""
                        target_url = f"{base_url}{port}/apbookeeper_webhook/telegram-response"
                        
                    elif source == 'banker':
                        port = ":8082" if env == 'LOCAL' else ""
                        target_url = f"{base_url}{port}/banker_webhook/telegram-response"
                    
                    else:
                        # Fallback sur Router par défaut
                        port = ":8080" if env == 'LOCAL' else ""
                        target_url = f"{base_url}{port}/router_webhook/telegram-response"

                    logger.info(f"google_auth_routing_telegram source={source} target={target_url}")

                    # 4. Construire le payload attendu par le webhook
                    # ✅ UTILISATION DU CHAT_ID du state en priorité
                    effective_chat_id = chat_id or context_params.get('chat_id') or context_params.get('subscription_id')
                    
                    webhook_payload = {
                        "mandate_path": context_params.get('mandate_path'),
                        "response": {
                            "type": "message",
                            "chat_id": effective_chat_id,
                            "text": message_text
                        }
                    }
                    
                    logger.info(f"google_auth_webhook_payload mandate={webhook_payload.get('mandate_path')} chat_id={effective_chat_id}")
                    
                    # 5. Envoyer la requête au service interne
                    if target_url and webhook_payload.get("mandate_path"):
                        try:
                            resp = requests.post(target_url, json=webhook_payload, timeout=5)
                            logger.info(f"google_auth_notify_telegram_routed status={resp.status_code}")
                        except Exception as e:
                            logger.error(f"google_auth_notify_telegram_routed_error {e}")
                    else:
                        logger.warning(f"google_auth_notify_telegram_skip missing_url_or_mandate path={webhook_payload.get('mandate_path')}")

                # --- PINNOKIO (WEB) ---
                elif communication_mode == 'pinnokio':
                    # Notification via WebSocket Hub si l'utilisateur est connecté
                    if user_id:
                        # Message système simulé
                        payload = {
                            "type": "chat_message",
                            "content": message_text,
                            "role": "system",
                            "timestamp": datetime.now().isoformat()
                        }
                        await hub.broadcast(user_id, payload)
                        logger.info(f"google_auth_notify_pinnokio broadcast to {user_id}")

                # --- GOOGLE CHAT ---
                elif communication_mode == 'google_chat':
                    # Nécessite un webhook ou thread_key + API
                    # Si on a un webhook stocké dans le state
                    webhook_url = context_params.get('webhook_url')
                    if webhook_url:
                        requests.post(webhook_url, json={"text": message_text})
                        logger.info("google_auth_notify_gchat webhook sent")
                    else:
                        # TODO: Implémenter envoi via API si nécessaire
                        pass

            except Exception as notify_err:
                logger.error(f"google_auth_notify_global_error {notify_err}")

        # 6. Réponse UI (HTML)
        return HTMLResponse(content="""
        <html>
            <head>
                <title>Authentification Réussie</title>
                <style>
                    body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background-color: #f0f2f5; }
                    .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; }
                    .success { color: #10b981; font-size: 4rem; margin-bottom: 1rem; }
                    h1 { color: #1f2937; }
                    p { color: #4b5563; }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="success">✅</div>
                    <h1>Connexion Réussie !</h1>
                    <p>Les accès Google Drive ont été mis à jour.</p>
                    <p>Vous pouvez fermer cette fenêtre.</p>
                    <script>setTimeout(function() { window.close(); }, 3000);</script>
                </div>
            </body>
        </html>
        """, status_code=200)
        
    except Exception as e:
        logger.error(f"google_auth_callback_fatal_error {e}", exc_info=True)
        return HTMLResponse(content=f"<h1>Erreur Serveur</h1><p>{str(e)}</p>", status_code=500)


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
            from .registry import get_registry_wrapper
            return get_registry_wrapper().register_user, "REGISTRY"
        if name == "unregister_session":
            # NOUVEAU: Utiliser le wrapper transparent (API identique)
            from .registry import get_registry_wrapper
            return get_registry_wrapper().unregister_session, "REGISTRY"
        # 🆕 NOUVEAU: Méthodes du registre des listeners (sous REGISTRY.*)
        if name in ["check_listener_status", "register_listener", "unregister_listener", 
                    "list_user_listeners", "cleanup_user_listeners", "update_listener_heartbeat"]:
            from .registry import get_registry_listeners
            target = getattr(get_registry_listeners(), name, None)
            if callable(target):
                return target, "REGISTRY"
    
    # === LISTENERS MANAGER (Workflow Listener à la demande) ===
    if method.startswith("LISTENERS."):
        name = method.split(".", 1)[1]
        # ⭐ Workflow listener par job (on-demand)
        if name in ["start_workflow_listener_for_job", "stop_workflow_listener_for_job"]:
            target = getattr(listeners_manager, name, None)
            if callable(target):
                return target, "LISTENERS"
    
    if method.startswith("CHROMA_VECTOR."):
        name = method.split(".", 1)[1]
        
        # ⭐ OPTIMISATION: register_collection_user en mode fire-and-forget (gain 13s)
        if name == "register_collection_user":
            async def _async_wrapper(user_id, collection_name, session_id):
                # Lancer le traitement réel dans un thread pour ne pas bloquer la boucle
                import asyncio
                import time
                
                loop = asyncio.get_event_loop()
                # On utilise run_in_executor pour ne pas bloquer l'event loop avec des appels sync
                loop.run_in_executor(
                    None, 
                    lambda: getattr(get_chroma_vector_service(), "register_collection_user")(user_id, collection_name, session_id)
                )
                
                # Retourner immédiatement une réponse simulée pour débloquer le client
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                return {
                    "user_id": user_id,
                    "collection_name": collection_name,
                    "session_id": session_id,
                    "registered_at": timestamp,
                    "last_heartbeat": timestamp,
                    "status": "initializing_background"
                }
            return _async_wrapper, "CHROMA_VECTOR"

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
    # ❌ SUPPRIMÉ: UNIFIED_REGISTRY déjà utilisé par REGISTRY via registry_wrapper
    # if method.startswith("UNIFIED_REGISTRY."):
    #     name = method.split(".", 1)[1]
    #     from .unified_registry import get_unified_registry
    #     target = getattr(get_unified_registry(), name, None)
    #     if callable(target):
    #         return target, "UNIFIED_REGISTRY"
    if method.startswith("LLM."):
        name = method.split(".", 1)[1]
        from .llm_service import get_llm_manager
        
        # ═══════════════════════════════════════════════════════════════
        # MÉTHODES LLM - ARCHITECTURE UNIFIÉE UI/BACKEND
        # ═══════════════════════════════════════════════════════════════
        # ⭐ FLUX UNIFIÉ :
        #   - MODE UI : send_message() → _process_unified_workflow(streaming=True)
        #   - MODE BACKEND : _resume_workflow_after_lpt() → _process_unified_workflow(streaming=False/True)
        #
        # ✅ Isolation garantie par _ensure_session_initialized() :
        #   - Charge user_context, jobs_data, jobs_metrics
        #   - Utilisé automatiquement par send_message() et _resume_workflow_after_lpt()
        # ═══════════════════════════════════════════════════════════════
        
        if name == "initialize_session":
            # ⭐ FIX: Utiliser un wrapper ASYNCHRONE pour permettre à create_task de fonctionner sur l'event loop principal
            # L'ancien wrapper synchrone + ThreadPoolExecutor tuait les tâches d'arrière-plan
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().initialize_session(**kwargs)
            return _async_wrapper, "LLM"
        if name == "start_onboarding_chat":
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().start_onboarding_chat(**kwargs)
            return _async_wrapper, "LLM"
        if name == "stop_onboarding_chat":
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().stop_onboarding_chat(**kwargs)
            return _async_wrapper, "LLM"
        if name == "send_message":
            # ⭐ MODE UI - Point d'entrée principal pour messages utilisateur
            # Architecture unifiée :
            #   1. _ensure_session_initialized() → Garantit données permanentes
            #   2. Vérifie/crée brain pour le thread
            #   3. _process_unified_workflow(enable_streaming=True) → Streaming WebSocket
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
            # ⭐ CORRIGÉ: Wrapper asynchrone non-bloquant (était sync et causait des timeouts)
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().flush_chat_history(**kwargs)
            return _async_wrapper, "LLM"
        if name == "enter_chat":
            # ⭐ NOUVEAU: Signal d'entrée sur un thread de chat (pour tracking présence)
            # Appelé par Reflex quand user ouvre/entre sur un thread
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().enter_chat(**kwargs)
            return _async_wrapper, "LLM"
        if name == "leave_chat":
            # ⭐ NOUVEAU: Signal de départ de la page chat (pour tracking présence)
            # Appelé par Reflex quand user ferme l'onglet ou change de module
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().leave_chat(**kwargs)
            return _async_wrapper, "LLM"
        if name == "approve_plan":
            # ⭐ NOUVEAU: Gérer les approbations de plans LPT
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().handle_approval_response(**kwargs)
            return _async_wrapper, "LLM"
        if name == "send_card_response":
            # ⭐ NOUVEAU: Réception réponse carte interactive
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().send_card_response(**kwargs)
            return _async_wrapper, "LLM"
        if name == "invalidate_user_context":
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().invalidate_user_context(**kwargs)
            return _async_wrapper, "LLM"
        if name == "execute_task_now":
            # ⭐ NOUVEAU: Exécution immédiate d'une tâche (déclenchée depuis le frontend)
            # Réplique la logique du CRON mais appelée manuellement
            async def _async_wrapper(**kwargs):
                return await get_llm_manager().execute_task_now(**kwargs)
            return _async_wrapper, "LLM"
    # ❌ SUPPRIMÉ: DOUBLON - Les méthodes listeners sont déjà exposées sous REGISTRY.*
    # if method.startswith("REGISTRY_LISTENERS."):
    #     name = method.split(".", 1)[1]
    #     from .registry_listeners import get_registry_listeners
    #     target = getattr(get_registry_listeners(), name, None)
    #     if callable(target):
    #         return target, "REGISTRY_LISTENERS"

    # === DMS (Document Management Systems) ===
    if method.startswith("DMS."):
        name = method.split(".", 1)[1]
        from .driveClientService import get_drive_client_service
        target = getattr(get_drive_client_service(mode='prod'), name, None)
        if callable(target):
            return target, "DMS"

    # === HR (Human Resources - Neon PostgreSQL) ===
    if method.startswith("HR."):
        name = method.split(".", 1)[1]
        from .hr_rpc_handlers import hr_rpc_handlers
        target = getattr(hr_rpc_handlers, name, None)
        if callable(target):
            return target, "HR"

    # === FIREBASE_CACHE (Firebase data with Redis cache) ===
    if method.startswith("FIREBASE_CACHE."):
        name = method.split(".", 1)[1]
        from .firebase_cache_handlers import firebase_cache_handlers
        target = getattr(firebase_cache_handlers, name, None)
        if callable(target):
            return target, "FIREBASE_CACHE"

    # === DRIVE_CACHE (Google Drive with Redis cache) ===
    if method.startswith("DRIVE_CACHE."):
        name = method.split(".", 1)[1]
        from .drive_cache_handlers import drive_cache_handlers
        target = getattr(drive_cache_handlers, name, None)
        if callable(target):
            return target, "DRIVE_CACHE"

    # === ERP (Enterprise Resource Planning) ===
    if method.startswith("ERP."):
        name = method.split(".", 1)[1]
        from .erp_service import get_erp_service
        
        # ⭐ OPTIMISATION: invalidate_connection en mode fire-and-forget (gain 11s)
        if name == "invalidate_connection":
            async def _async_wrapper(user_id, company_id, **kwargs):
                # Lancer le traitement réel dans un thread pour ne pas bloquer la boucle
                import asyncio
                loop = asyncio.get_event_loop()
                loop.run_in_executor(
                    None, 
                    lambda: get_erp_service().invalidate_connection(user_id, company_id)
                )
                return {"success": True, "message": "Invalidation launched in background"}
            return _async_wrapper, "ERP"

        target = getattr(get_erp_service(), name, None)
        if callable(target):
            return target, "ERP"

    # === DASHBOARD (Next.js Dashboard - NEW) ===
    # Ce namespace est NOUVEAU et ne modifie pas les méthodes existantes
    # Endpoints: DASHBOARD.full_data, DASHBOARD.get_metrics, DASHBOARD.invalidate_cache
    if method.startswith("DASHBOARD."):
        name = method.split(".", 1)[1]
        from .dashboard_handlers import get_dashboard_handlers
        target = getattr(get_dashboard_handlers(), name, None)
        if callable(target):
            return target, "DASHBOARD"

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

        # ⭐ Injecter user_id et company_id pour DMS et ERP
        args = list(req.args or [])
        kwargs = dict(req.kwargs or {})

        if _ns == "DMS" and req.user_id:
            # DMS: Injecter user_id comme PREMIER argument (positionnel)
            args = [req.user_id] + args

        elif _ns == "ERP":
            # ERP: Injecter user_id et company_id depuis kwargs (extraits du contexte)
            # Les méthodes ERP ont la signature: method(user_id, company_id, **kwargs)
            if "user_id" not in kwargs and req.user_id:
                kwargs["user_id"] = req.user_id

            # Exception pour test_connection en mode direct (credentials fournis)
            # Dans ce cas, company_id n'est pas requis
            is_test_connection_direct = (
                req.method == "ERP.test_connection" and
                any(kwargs.get(key) for key in ["url", "db", "username", "password"])
            )

            # company_id doit être fourni explicitement par le client
            # SAUF pour test_connection en mode direct
            if "company_id" not in kwargs and not is_test_connection_direct:
                raise ValueError("company_id is required for ERP methods")

        elif _ns == "HR":
            # HR: Injecter user_id automatiquement depuis le contexte RPC
            # Les méthodes HR utilisent firebase_user_id pour le cache Redis
            if "firebase_user_id" not in kwargs and req.user_id:
                kwargs["firebase_user_id"] = req.user_id

        elif _ns == "FIREBASE_CACHE":
            # FIREBASE_CACHE: Injecter user_id automatiquement depuis le contexte RPC
            # Les méthodes Firebase cache utilisent user_id pour le cache Redis
            if "user_id" not in kwargs and req.user_id:
                kwargs["user_id"] = req.user_id

        elif _ns == "DRIVE_CACHE":
            # DRIVE_CACHE: Injecter user_id automatiquement depuis le contexte RPC
            # Les méthodes Drive cache utilisent user_id pour le cache Redis
            if "user_id" not in kwargs and req.user_id:
                kwargs["user_id"] = req.user_id

        elif _ns == "DASHBOARD":
            # DASHBOARD (Next.js): Injecter user_id automatiquement
            # Les méthodes Dashboard utilisent user_id et company_id pour le cache
            if "user_id" not in kwargs and req.user_id:
                kwargs["user_id"] = req.user_id
            # company_id doit être fourni par le client (dans kwargs)
            # car il n'y a pas de company_id dans le contexte RPC par défaut

        if inspect.iscoroutinefunction(func):
            result = await func(*args, **kwargs)
        else:
            result = func(*args, **kwargs)
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


# ═══════════════════════════════════════════════════════════════
# ENDPOINT DE CALLBACK POUR LES AGENTS LPT
# ═══════════════════════════════════════════════════════════════

class LPTCallbackRequest(BaseModel):
    """
    Modèle pour les réponses des agents LPT.
    
    ⭐ NOUVEAU FORMAT : Inclut toutes les données englobantes du payload original
    + la réponse du LPT
    """
    # ═══════════════════════════════════════════════════════════
    # 1. IDENTIFIANTS (Données englobantes originales)
    # ═══════════════════════════════════════════════════════════
    collection_name: str
    user_id: str
    client_uuid: str
    mandates_path: str
    batch_id: str  # Utilisé comme task_id
    
    # ═══════════════════════════════════════════════════════════
    # 2. DONNÉES DE LA TÂCHE (jobs_data original)
    # ═══════════════════════════════════════════════════════════
    jobs_data: List[Dict[str, Any]]
    
    # ═══════════════════════════════════════════════════════════
    # 3. CONFIGURATION (settings originaux)
    # ═══════════════════════════════════════════════════════════
    settings: List[Dict[str, Any]]
    
    # ═══════════════════════════════════════════════════════════
    # 4. TRAÇABILITÉ (traceability original)
    # ═══════════════════════════════════════════════════════════
    traceability: Dict[str, Any]
    
    # ═══════════════════════════════════════════════════════════
    # 5. IDENTIFIANTS ADDITIONNELS
    # ═══════════════════════════════════════════════════════════
    pub_sub_id: str
    start_instructions: Optional[str] = None
    
    # ═══════════════════════════════════════════════════════════
    # 6. RÉPONSE DU LPT (NOUVEAU - Données de sortie)
    # ═══════════════════════════════════════════════════════════
    response: Dict[str, Any] = Field(
        ...,
        description="Réponse du LPT contenant status, result, error, etc."
    )
    
    # ═══════════════════════════════════════════════════════════
    # 7. MÉTADONNÉES D'EXÉCUTION
    # ═══════════════════════════════════════════════════════════
    execution_time: Optional[str] = None
    completed_at: Optional[str] = None
    logs_url: Optional[str] = None
    
    @property
    def task_id(self) -> str:
        """Alias pour batch_id (rétrocompatibilité)."""
        return self.batch_id
    
    @property
    def thread_key(self) -> str:
        """Extrait thread_key depuis traceability."""
        return self.traceability.get("thread_key", "")
    
    @property
    def status(self) -> str:
        """Extrait status depuis response."""
        return self.response.get("status", "unknown")
    
    @property
    def result(self) -> Optional[Dict[str, Any]]:
        """Extrait result depuis response."""
        return self.response.get("result")
    
    @property
    def error(self) -> Optional[str]:
        """Extrait error depuis response."""
        return self.response.get("error")
    
    @property
    def task_type(self) -> str:
        """Détermine le type de LPT depuis les données."""
        # Essayer de déduire depuis traceability ou jobs_data
        thread_name = self.traceability.get("thread_name", "")
        if "APBookkeeper" in thread_name:
            return "APBookkeeper"
        elif "Router" in thread_name:
            return "Router"
        elif "Banker" in thread_name:
            return "Banker"
        # Fallback: regarder dans jobs_data
        if self.jobs_data and len(self.jobs_data) > 0:
            first_job = self.jobs_data[0]
            if "file_name" in first_job and "job_id" in first_job and "status" in first_job:
                if first_job.get("status") == "to_process":
                    return "APBookkeeper"
                elif first_job.get("status") == "to_route":
                    return "Router"
            elif "bank_account" in first_job:
                return "Banker"
        return "LPT"


class InvalidateCacheRequest(BaseModel):
    """Requête pour invalider le cache Redis (dev/debug seulement)."""
    user_id: str = Field(..., description="ID Firebase de l'utilisateur")
    collection_name: str = Field(..., description="Nom de la collection (company)")
    cache_types: list[str] = Field(default=["context", "jobs"], description="Types de cache à invalider: context, jobs, all")


# ═══════════════════════════════════════════════════════════════
# MODÈLE DE CALLBACK POUR LE JOBBER HR
# ═══════════════════════════════════════════════════════════════

class HRCallbackRequest(BaseModel):
    """
    Modèle pour les callbacks du Jobber HR après traitement d'un job.
    
    Le Jobber appelle ce endpoint quand un calcul de paie, génération PDF,
    ou batch est terminé.
    """
    # Identifiants pour routage
    user_id: str = Field(..., description="Firebase UID de l'utilisateur")
    session_id: Optional[str] = Field(None, description="Session ID pour routage WebSocket")
    mandate_path: Optional[str] = Field(None, description="Chemin Firebase pour traçabilité")
    company_id: Optional[str] = Field(None, description="UUID de la company PostgreSQL")
    
    # Identifiant du job
    job_id: str = Field(..., description="ID unique du job")
    job_type: str = Field(..., description="Type: payroll_calculate, payroll_batch, pdf_generate, etc.")
    
    # Résultat
    status: str = Field(..., description="Status: completed, failed, partial")
    result: Optional[Dict[str, Any]] = Field(None, description="Données de résultat")
    error: Optional[str] = Field(None, description="Message d'erreur si échec")
    
    # Métadonnées d'exécution
    started_at: Optional[str] = Field(None, description="ISO timestamp début")
    completed_at: Optional[str] = Field(None, description="ISO timestamp fin")
    execution_time_ms: Optional[int] = Field(None, description="Durée en millisecondes")
    
    # Données additionnelles pour certains types de jobs
    employee_id: Optional[str] = Field(None, description="Employee concerné")
    period_year: Optional[int] = Field(None, description="Année de la période")
    period_month: Optional[int] = Field(None, description="Mois de la période")
    batch_progress: Optional[Dict[str, Any]] = Field(None, description="Progression batch: {total, completed, failed}")


class InvalidateContextRequest(BaseModel):
    """Requête pour invalider le contexte LLM (force rechargement Firebase)."""
    user_id: str = Field(..., description="ID Firebase de l'utilisateur")
    collection_name: str = Field(..., description="Chemin de collecte (mandate_path)")


class CloudWatchListRequest(BaseModel):
    """Requête pour lister les streams de logs CloudWatch."""
    limit: Optional[int] = None
    order_by: str = 'LastEventTime'
    descending: bool = True
    days: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class CloudWatchDownloadRequest(BaseModel):
    """Requête pour télécharger un log CloudWatch."""
    log_stream_name: str = Field(..., description="Nom du stream de logs à télécharger")
    output_file: Optional[str] = None
    json_format: bool = False
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@app.post("/invalidate-context")
async def invalidate_context(req: InvalidateContextRequest):
    """
    Invalide le contexte utilisateur pour forcer un rechargement depuis Firebase.
    """
    from .llm_service import get_llm_manager

    llm_manager = get_llm_manager()
    result = await llm_manager.invalidate_user_context(
        user_id=req.user_id,
        collection_name=req.collection_name,
    )
    return result


@app.post("/admin/invalidate_cache")
async def invalidate_cache(req: InvalidateCacheRequest):
    """
    🔧 **ENDPOINT DE DÉVELOPPEMENT** - Invalide le cache Redis pour un utilisateur
    
    ⚠️ Cet endpoint est destiné à un usage manuel pendant le développement.
    
    Args:
        req: Requête contenant user_id, collection_name et types de cache
    
    Returns:
        Détails des clés supprimées
    """
    try:
        from .redis_client import get_redis
        
        redis_client = get_redis()
        deleted_keys = []
        
        # Préparer les clés à supprimer
        keys_to_delete = []
        
        if "all" in req.cache_types or "context" in req.cache_types:
            context_key = f"context:{req.user_id}:{req.collection_name}"
            keys_to_delete.append(context_key)
        
        if "all" in req.cache_types or "jobs" in req.cache_types:
            # ⭐ Jobs par département - Utiliser cache:* (source de vérité unique)
            # Format: cache:{user_id}:{company_id}:{data_type}:{sub_type}
            dept_mapping = {
                "APBOOKEEPER": "apbookeeper:documents",
                "ROUTER": "drive:documents",
                "BANK": "bank:transactions"
            }
            for dept in ["APBOOKEEPER", "ROUTER", "BANK"]:
                data_type_sub = dept_mapping.get(dept, f"{dept.lower()}:data")
                cache_key = f"cache:{req.user_id}:{req.collection_name}:{data_type_sub}"
                keys_to_delete.append(cache_key)
        
        # Supprimer chaque clé
        for key in keys_to_delete:
            try:
                result = redis_client.delete(key)
                if result > 0:
                    deleted_keys.append(key)
                    logger.info(f"[CACHE_INVALIDATE] ✅ Clé supprimée: {key}")
                else:
                    logger.info(f"[CACHE_INVALIDATE] ℹ️ Clé absente: {key}")
            except Exception as e:
                logger.warning(f"[CACHE_INVALIDATE] ⚠️ Erreur suppression {key}: {e}")
        
        return {
            "status": "success",
            "message": f"Cache invalidé pour {req.user_id}:{req.collection_name}",
            "deleted_keys": deleted_keys,
            "requested_keys": keys_to_delete,
            "cache_types": req.cache_types
        }
    
    except Exception as e:
        logger.error(f"[CACHE_INVALIDATE] ❌ Erreur: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erreur invalidation cache: {str(e)}")


@app.post("/lpt/callback")
async def lpt_callback(req: LPTCallbackRequest, authorization: str | None = Header(default=None, alias="Authorization")):
    """
    ⭐ MODE BACKEND - Point d'entrée pour reprise workflow après LPT
    
    Les agents externes (APBookkeeper, Router, Banker, etc.) appellent cet endpoint
    quand leur traitement est terminé.
    
    Flux unifié :
    1. Récupérer la tâche sauvegardée dans Firebase (pour le contexte)
    2. Mettre à jour le statut de la tâche
    3. Appeler _resume_workflow_after_lpt() qui :
       a. _ensure_session_initialized() → Garantit données permanentes (⭐ CRITIQUE)
       b. Vérifie/crée brain pour le thread
       c. _process_unified_workflow(enable_streaming=user_connected) → Flux unifié
    4. Envoyer une notification à l'utilisateur via WebSocket (si connecté)
    
    Support Dual-Mode :
    - User connecté → Streaming WebSocket actif (MODE UI)
    - User déconnecté → RTDB uniquement (MODE BACKEND pur)
    """
    t0 = time.time()
    try:
        # 🔍 DEBUG: Logger la structure RÉELLE du payload parsé par Pydantic
        logger.info(
            "🔍 [LPT_CALLBACK] Structure réelle du payload parsé: batch_id=%s, has_jobs_data=%s, has_response=%s",
            req.batch_id if hasattr(req, 'batch_id') else "N/A",
            bool(req.jobs_data) if hasattr(req, 'jobs_data') else False,
            bool(req.response) if hasattr(req, 'response') else False
        )
        
        logger.info(
            "lpt_callback_in task_id=%s thread=%s status=%s user=%s",
            req.task_id,
            req.thread_key,
            req.status,
            req.user_id
        )
        
        # 🔍 DEBUG: Afficher les données brutes du callback
        import json
        try:
            raw_payload = {
                "task_id": req.task_id,
                "task_type": req.task_type,
                "thread_key": req.thread_key,
                "user_id": req.user_id,
                "collection_name": req.collection_name,
                "status": req.status,
                "result": req.result,
                "error": req.error,
                "mandates_path": req.mandates_path,
                "traceability": req.traceability,
                "pub_sub_id": req.pub_sub_id
            }
            logger.info(f"🔍 [LPT_CALLBACK] Données brutes reçues: {json.dumps(raw_payload, indent=2, ensure_ascii=False)}")
        except Exception as log_err:
            logger.warning(f"[LPT_CALLBACK] Erreur lors du log des données brutes: {log_err}")
        
        # 🔐 Vérifier l'authentification
        _require_auth(authorization)
        
        # ⭐ ÉTAPE 1 : Vérifier si c'est une tâche planifiée (avec task_id dans mandate_path/tasks)
        # Utiliser mandate_path du payload (renvoyé dans le callback)
        mandate_path = req.mandates_path
        tasks_path = f"{mandate_path}/tasks"
        
        # ⭐ CORRECTION : Utiliser thread_key pour détecter la tâche planifiée
        # Car thread_key = task_id de la tâche planifiée (voir cron_scheduler.py ligne 295)
        # req.task_id est le batch_id du LPT (router_batch_xxx, apbookeeper_batch_xxx, etc.)
        task_ref = get_firestore().document(f"{tasks_path}/{req.thread_key}")
        task_doc_snap = task_ref.get()
        
        # Déterminer si c'est une tâche planifiée ou un LPT simple
        is_planned_task = task_doc_snap.exists
        
        if is_planned_task:
            # ⭐ CAS 1 : Tâche planifiée (task_id existe dans mandate_path/tasks)
            task_data = task_doc_snap.to_dict()
            logger.info(
                "lpt_callback_planned_task task_id=%s thread=%s mandate_path=%s",
                req.task_id,
                req.thread_key,
                mandate_path
            )
        else:
            # ⭐ CAS 2 : LPT simple (sans task_id planifié / ordre direct)
            # → Pas de document task dans {mandate_path}/tasks/{task_id}
            logger.info(
                "lpt_callback_simple_lpt task_id=%s thread=%s mandate_path=%s "
                "(pas de task dans %s - ordre direct)",
                req.task_id,
                req.thread_key,
                mandate_path,
                tasks_path
            )
            
            # Pour LPT simple, construire task_data minimal
            from datetime import datetime, timezone
            task_data = {
                "task_type": req.task_type,
                "task_id": req.task_id,
                "thread_key": req.thread_key,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        
        # ⭐ ÉTAPE 2 : Mettre à jour la tâche dans Firebase (seulement si tâche planifiée)
        from datetime import datetime, timezone
        
        now_iso = datetime.now(timezone.utc).isoformat()
        
        if is_planned_task:
            # Mise à jour uniquement pour tâches planifiées
            update_data = {
                "status": req.status,
                "updated_at": now_iso,
                "completed_at": req.completed_at or now_iso,
                "result": req.result,
                "error": req.error,
                "execution_time": req.execution_time,
                "logs_url": req.logs_url,
                # ⭐ NOUVEAU : Sauvegarder le payload complet pour reprise
                "original_payload": {
                    "collection_name": req.collection_name,
                    "user_id": req.user_id,
                    "client_uuid": req.client_uuid,
                    "mandates_path": req.mandates_path,
                    "batch_id": req.batch_id,
                    "jobs_data": req.jobs_data,
                    "settings": req.settings,
                    "traceability": req.traceability,
                    "pub_sub_id": req.pub_sub_id,
                    "start_instructions": req.start_instructions
                },
                "response": req.response
            }
            
            # Mise à jour dans {mandate_path}/tasks/{thread_key} (thread_key = task_id de la tâche planifiée)
            task_ref.update(update_data)
            logger.info("lpt_callback_firebase_updated task_id=%s (thread_key) path=%s with_full_payload=True", req.thread_key, tasks_path)
        else:
            # Pour LPT simple, pas de mise à jour Firebase (pas de document task)
            logger.info("lpt_callback_skip_firebase_update task_id=%s (LPT simple, pas de document task)", req.task_id)
        
        # ⭐ ÉTAPE 3 : Construire le message pour l'utilisateur
        task_type = task_data.get("task_type", "LPT")
        
        if req.status == "completed":
            agent_message = f"✅ Tâche {task_type} terminée avec succès."
            if req.result:
                summary = req.result.get("summary", "Traitement terminé")
                agent_message += f"\n\n**Résumé** : {summary}"
                if "processed_items" in req.result:
                    agent_message += f"\n**Items traités** : {req.result['processed_items']}"
        elif req.status == "failed":
            agent_message = f"❌ Tâche {task_type} échouée."
            if req.error:
                agent_message += f"\n\n**Erreur** : {req.error}"
        else:  # partial
            agent_message = f"⚠️ Tâche {task_type} terminée partiellement."
            if req.result:
                agent_message += f"\n\n**Résumé** : {req.result.get('summary', 'Traitement partiel')}"
        
        
        # ⭐ ÉTAPE 4 : Reprendre le workflow (quelque soit le statut - l'agent décide)
        # Récupérer le company_id depuis task_data ou fallback sur collection_name du payload
        company_id = task_data.get("company_id") or req.collection_name
        
        if not company_id:
            logger.error(
                "lpt_callback_skip_resume company_id_missing task_id=%s",
                req.task_id
            )
            dt_ms = int((time.time() - t0) * 1000)
            return {
                "ok": False,
                "task_id": req.task_id,
                "error": "company_id manquant",
                "dt_ms": dt_ms
            }
        
        # ⭐ ÉTAPE 4.1 : Déterminer le mode (UI ou Backend) avec check robuste
        from .llm_service import get_llm_manager
        
        llm_manager = get_llm_manager()
        
        # Garantir que la session existe (nécessaire pour vérifier is_user_on_specific_thread)
        session = await llm_manager._ensure_session_initialized(
            user_id=req.user_id,
            collection_name=company_id,
            chat_mode="general_chat"
        )
        
        # ⭐ CHECK ROBUSTE: User est-il ACTUELLEMENT sur ce thread précis?
        # Logique:
        # - is_on_chat_page = False → Mode BACKEND (user pas sur la page)
        # - is_on_chat_page = True + current_active_thread = thread_key → Mode UI
        # - is_on_chat_page = True + current_active_thread ≠ thread_key → Mode BACKEND
        user_on_active_chat = session.is_user_on_specific_thread(req.thread_key)
        
        mode = "UI" if user_on_active_chat else "BACKEND"
        
        logger.info(
            "lpt_callback_resume_workflow mode=%s task_id=%s thread=%s "
            "user_on_active_chat=%s is_on_chat_page=%s current_active_thread=%s is_planned=%s",
            mode,
            req.task_id,
            req.thread_key,
            user_on_active_chat,
            session.is_on_chat_page,
            session.current_active_thread,
            is_planned_task
        )
        
        # Lancer la reprise du workflow en arrière-plan
        # ⭐ _resume_workflow_after_lpt gère automatiquement :
        # - Création de session si nécessaire (_ensure_session_initialized)
        # - Création du brain si nécessaire (charge historique RTDB)
        # - Streaming conditionnel (enable_streaming=user_on_active_chat)
        asyncio.create_task(
            llm_manager._resume_workflow_after_lpt(
                user_id=req.user_id,
                company_id=company_id,
                thread_key=req.thread_key,
                task_id=req.task_id,
                task_data=task_data,
                lpt_response=req.response,
                original_payload={
                    "collection_name": req.collection_name,
                    "user_id": req.user_id,
                    "client_uuid": req.client_uuid,
                    "mandates_path": req.mandates_path,
                    "batch_id": req.batch_id,
                    "jobs_data": req.jobs_data,
                    "settings": req.settings,
                    "traceability": req.traceability,
                    "pub_sub_id": req.pub_sub_id,
                    "start_instructions": req.start_instructions,
                    "task_type": req.task_type
                },
                user_connected=user_on_active_chat,  # ⭐ Check robuste du thread actif
                is_planned_task=is_planned_task  # ⭐ NOUVEAU: Distinguer tâche planifiée vs LPT simple
            )
        )
        
        logger.info(
            "lpt_callback_resume_task_created mode=%s task_id=%s is_planned=%s",
            mode,
            req.task_id,
            is_planned_task
        )
        
        dt_ms = int((time.time() - t0) * 1000)
        
        # ⭐ Pour les tâches planifiées, utiliser thread_key comme task_id (car thread_key = task_id de la tâche planifiée)
        # Pour les LPT simples, utiliser req.task_id (batch_id)
        returned_task_id = req.thread_key if is_planned_task else req.task_id
        
        logger.info(
            "lpt_callback_ok task_id=%s (returned=%s) status=%s dt_ms=%s is_planned=%s",
            req.task_id,
            returned_task_id,
            req.status,
            dt_ms,
            is_planned_task
        )
        
        return {
            "ok": True,
            "task_id": returned_task_id,
            "message": "Callback traité avec succès"
        }
    
    except Exception as e:
        dt_ms = int((time.time() - t0) * 1000)
        logger.error("lpt_callback_error code=INTERNAL task_id=%s dt_ms=%s error=%s", req.task_id, dt_ms, repr(e))
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# ENDPOINT DE CALLBACK POUR LE JOBBER HR
# ═══════════════════════════════════════════════════════════════

@app.post("/hr/callback")
async def hr_callback(
    req: HRCallbackRequest,
    authorization: str | None = Header(default=None, alias="Authorization")
):
    """
    ⭐ Callback du Jobber HR après traitement d'un job asynchrone.
    
    Le Jobber (pinnokio_hr) appelle ce endpoint quand :
    - Un calcul de paie est terminé
    - Un batch de paies est terminé
    - Un PDF a été généré
    - Un export comptable est prêt
    
    Responsabilités :
    1. Authentifier l'appel (API Key ou Service Token)
    2. Logger pour traçabilité
    3. Broadcaster au client via WebSocket Hub
    4. Optionnel: Mettre à jour métriques/quotas compte
    5. Optionnel: Buffer si user déconnecté
    """
    t0 = time.time()
    
    try:
        logger.info(
            "hr_callback_in job_id=%s user=%s status=%s type=%s company=%s",
            req.job_id, req.user_id, req.status, req.job_type, req.company_id
        )
        
        # 1. Authentification
        _require_auth(authorization)
        
        # 2. Construire le payload WebSocket selon le type de job
        ws_payload = {
            "type": "hr_job_completed",
            "job_id": req.job_id,
            "job_type": req.job_type,
            "status": req.status,
            "timestamp": req.completed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        
        # Ajouter les données spécifiques selon le type
        if req.job_type == "payroll_calculate":
            ws_payload["data"] = {
                "employee_id": req.employee_id,
                "period": f"{req.period_year}-{req.period_month:02d}" if req.period_year and req.period_month else None,
                "result": req.result,
                "error": req.error,
            }
        elif req.job_type == "payroll_batch":
            ws_payload["data"] = {
                "progress": req.batch_progress,
                "result": req.result,
                "error": req.error,
            }
        elif req.job_type == "pdf_generate":
            ws_payload["data"] = {
                "employee_id": req.employee_id,
                "pdf_url": req.result.get("pdf_url") if req.result else None,
                "error": req.error,
            }
        else:
            # Générique
            ws_payload["data"] = {
                "result": req.result,
                "error": req.error,
            }
        
        # Ajouter les métadonnées d'exécution
        if req.execution_time_ms:
            ws_payload["execution_time_ms"] = req.execution_time_ms
        
        # 3. Broadcast via WebSocket Hub
        ws_sent = False
        try:
            await hub.broadcast(req.user_id, ws_payload)
            ws_sent = True
            logger.info(
                "hr_callback_ws_sent user=%s job=%s type=%s",
                req.user_id, req.job_id, req.job_type
            )
        except Exception as ws_err:
            # User probablement déconnecté
            logger.warning(
                "hr_callback_ws_failed user=%s job=%s error=%s",
                req.user_id, req.job_id, repr(ws_err)
            )
            
            # 4. Buffer le message pour envoi ultérieur si WebSocket échoue
            try:
                from .ws_message_buffer import get_message_buffer
                buffer = get_message_buffer()
                buffer.add_message(
                    user_id=req.user_id,
                    thread_key=f"hr_job_{req.job_id}",
                    message=ws_payload
                )
                logger.info(
                    "hr_callback_buffered user=%s job=%s",
                    req.user_id, req.job_id
                )
            except Exception as buf_err:
                logger.warning(
                    "hr_callback_buffer_failed user=%s error=%s",
                    req.user_id, repr(buf_err)
                )
        
        # 5. Optionnel: Mettre à jour Firestore pour progression
        if req.job_type == "payroll_batch" and req.mandate_path:
            try:
                db = get_firestore()
                progress_ref = db.document(f"{req.mandate_path}/hr_jobs/{req.job_id}")
                progress_ref.set({
                    "status": req.status,
                    "progress": req.batch_progress,
                    "completed_at": req.completed_at,
                    "result_summary": {
                        "total": req.batch_progress.get("total") if req.batch_progress else 0,
                        "completed": req.batch_progress.get("completed") if req.batch_progress else 0,
                        "failed": req.batch_progress.get("failed") if req.batch_progress else 0,
                    } if req.batch_progress else None,
                    "error": req.error,
                }, merge=True)
                logger.info(
                    "hr_callback_firestore_updated job=%s path=%s",
                    req.job_id, req.mandate_path
                )
            except Exception as fs_err:
                logger.warning(
                    "hr_callback_firestore_failed job=%s error=%s",
                    req.job_id, repr(fs_err)
                )
        
        dt_ms = int((time.time() - t0) * 1000)
        
        logger.info(
            "hr_callback_ok job_id=%s status=%s ws_sent=%s dt_ms=%s",
            req.job_id, req.status, ws_sent, dt_ms
        )
        
        return {
            "ok": True,
            "job_id": req.job_id,
            "ws_sent": ws_sent,
            "dt_ms": dt_ms
        }
    
    except Exception as e:
        dt_ms = int((time.time() - t0) * 1000)
        logger.error(
            "hr_callback_error job_id=%s dt_ms=%s error=%s",
            req.job_id, dt_ms, repr(e),
            exc_info=True
        )
        return {"ok": False, "job_id": req.job_id, "error": str(e)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # Backend Reflex passera le uid via query string ?uid=...
        uid = ws.query_params.get("uid")
        session_id = ws.query_params.get("session_id", "")
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
        # ⭐ NOUVEAU: Démarre une tâche de keepalive WebSocket (ping/pong)
        keepalive_task = asyncio.create_task(_websocket_keepalive(ws, uid))
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
        
        # ⭐ NOUVEAU: Envoyer les messages bufferisés si le WebSocket du chat est connecté
        if space_code and thread_key:
            try:
                from .ws_message_buffer import get_message_buffer
                buffer = get_message_buffer()
                
                # Récupérer les messages en attente (et les supprimer du buffer)
                pending_messages = buffer.get_pending_messages(
                    user_id=uid,
                    thread_key=thread_key,
                    delete_after=True
                )
                
                if pending_messages:
                    logger.info(
                        f"[WS_BUFFER] 📬 Envoi des messages bufferisés - "
                        f"uid={uid} thread={thread_key} count={len(pending_messages)}"
                    )
                    
                    # Envoyer chaque message bufferisé
                    for message in pending_messages:
                        await hub.broadcast(uid, message)
                        message_type = message.get("type", "unknown")
                        logger.info(
                            f"[WS_BUFFER] 📡 Message bufferisé envoyé - "
                            f"uid={uid} thread={thread_key} type={message_type}"
                        )
                    
                    logger.info(
                        f"[WS_BUFFER] ✅ Tous les messages bufferisés envoyés - "
                        f"uid={uid} thread={thread_key} count={len(pending_messages)}"
                    )
            except Exception as e:
                logger.error(
                    f"[WS_BUFFER] ❌ Erreur envoi messages bufferisés - "
                    f"uid={uid} thread={thread_key} error={e}",
                    exc_info=True
                )
        
        while True:
            # Reception et traitement des messages WebSocket du client
            try:
                raw_message = await ws.receive_text()

                # Parse le message JSON
                try:
                    message = _json.loads(raw_message)
                    msg_type = message.get("type")
                    msg_payload = message.get("payload", {})

                    logger.info(f"[WS] Message reçu - uid={uid} type={msg_type}")

                    # Routage des messages vers les handlers appropriés
                    if msg_type == "auth.firebase_token":
                        # Handler d'authentification Firebase
                        from .wrappers.auth_handlers import handle_firebase_token
                        response = await handle_firebase_token(msg_payload)

                        # Envoyer la réponse au client
                        await ws.send_text(_json.dumps(response))
                        logger.info(
                            f"[WS] Auth response sent - uid={uid} "
                            f"type={response.get('type')} "
                            f"success={response.get('payload', {}).get('success')}"
                        )

                    elif msg_type == "dashboard.orchestrate_init":
                        # Handler d'orchestration du dashboard (après auth)
                        from .wrappers.dashboard_orchestration_handlers import handle_orchestrate_init
                        response = await handle_orchestrate_init(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Orchestration init response sent - uid={uid}")

                    elif msg_type == "dashboard.company_change":
                        # Handler de changement de société
                        from .wrappers.dashboard_orchestration_handlers import handle_company_change
                        response = await handle_company_change(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Company change response sent - uid={uid}")

                    elif msg_type == "dashboard.refresh":
                        # Handler de rafraîchissement forcé
                        from .wrappers.dashboard_orchestration_handlers import handle_refresh
                        response = await handle_refresh(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Dashboard refresh response sent - uid={uid}")

                    # ============================================
                    # TASK EVENTS
                    # ============================================
                    elif msg_type == "task.list":
                        from .wrappers.task_handlers import handle_task_list
                        response = await handle_task_list(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Task list response sent - uid={uid}")

                    elif msg_type == "task.execute":
                        from .wrappers.task_handlers import handle_task_execute
                        response = await handle_task_execute(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Task execute response sent - uid={uid}")

                    elif msg_type == "task.toggle_enabled":
                        from .wrappers.task_handlers import handle_task_toggle
                        response = await handle_task_toggle(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Task toggle response sent - uid={uid}")

                    elif msg_type == "task.update":
                        from .wrappers.task_handlers import handle_task_update
                        response = await handle_task_update(
                            uid=uid,
                            session_id=session_id,
                            payload=msg_payload
                        )
                        await ws.send_text(_json.dumps(response))
                        logger.info(f"[WS] Task update response sent - uid={uid}")

                    else:
                        # Messages non gérés (pour future extension)
                        logger.debug(
                            f"[WS] Unhandled message type - uid={uid} type={msg_type}"
                        )

                except _json.JSONDecodeError as parse_err:
                    logger.error(
                        f"[WS] Invalid JSON received - uid={uid} error={parse_err}"
                    )
                    # Envoyer une erreur au client
                    error_response = {
                        "type": "error",
                        "payload": {
                            "error": "Invalid JSON format",
                            "code": "PARSE_ERROR"
                        }
                    }
                    await ws.send_text(_json.dumps(error_response))

            except WebSocketDisconnect:
                # Client déconnecté, sortir de la boucle proprement
                logger.info(f"[WS] Client disconnected during receive - uid={uid}")
                break
            except Exception as msg_err:
                logger.error(
                    f"[WS] Message processing error - uid={uid} error={msg_err}",
                    exc_info=True
                )
                # Vérifier si c'est une erreur de connexion fermée
                if "disconnect" in str(msg_err).lower() or "closed" in str(msg_err).lower():
                    logger.info(f"[WS] Connection closed, exiting loop - uid={uid}")
                    break
    except WebSocketDisconnect as e:
        disconnect_reason = "unknown"
        try:
            code = getattr(e, "code", None)
            reason = getattr(e, "reason", None)
            
            # 🔍 Identifier le type de déconnexion
            if code == 1000:
                disconnect_reason = "normal_closure"
            elif code == 1001:
                disconnect_reason = "going_away"
            elif code == 1006:
                disconnect_reason = "abnormal_closure"
            elif code == 1011:
                disconnect_reason = "server_error"
            else:
                disconnect_reason = f"code_{code}"
            
            logger.warning(
                "🔴 ws_disconnect uid=%s code=%s reason=%s type=%s", 
                ws.query_params.get("uid"), code, reason, disconnect_reason
            )
        except Exception:
            logger.warning("🔴 ws_disconnect uid=%s type=exception", ws.query_params.get("uid"))
    except Exception as e:
        logger.error("🔴 ws_error uid=%s error=%s", ws.query_params.get("uid"), repr(e), exc_info=True)
    finally:
        try:
            uid = ws.query_params.get("uid")
            if uid:
                # 📊 Enregistrer la métrique de déconnexion
                try:
                    from .ws_metrics import record_ws_disconnect
                    record_ws_disconnect(uid, disconnect_reason if 'disconnect_reason' in locals() else "unknown")
                except Exception:
                    pass
                
                await hub.unregister(uid, ws)
                # Arrête le heartbeat et le keepalive, puis marque l'utilisateur offline
                try:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                except Exception:
                    pass
                try:
                    keepalive_task.cancel()
                    try:
                        await keepalive_task
                    except asyncio.CancelledError:
                        pass
                except Exception:
                    pass
                await _set_presence(uid, status="offline")
                
                logger.info("🟡 ws_cleanup_complete uid=%s", uid)
        except Exception as e:
            logger.error("🔴 ws_cleanup_error error=%s", repr(e), exc_info=True)


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
            from .registry import get_registry_wrapper
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


async def _websocket_keepalive(ws: WebSocket, uid: str) -> None:
    """
    ⭐ NOUVEAU: Envoie des pings périodiques pour maintenir la connexion active.
    
    Prévient le timeout ALB en envoyant un message toutes les 30 secondes.
    Particulièrement important pour les traitements longs (onboarding, LLM).
    """
    try:
        try:
            interval = int(os.getenv("WEBSOCKET_KEEPALIVE_INTERVAL", "30"))
        except Exception:
            interval = 30
        
        # Log activation keepalive
        if _debug_enabled():
            logger.info("ws_keepalive_started uid=%s interval=%ss", uid, interval)
        
        while True:
            await asyncio.sleep(interval)
            try:
                # Envoyer ping au client
                await ws.send_json({
                    "type": "ping",
                    "timestamp": time.time()
                })
                
                # Log uniquement en mode debug pour éviter spam
                if _debug_enabled():
                    logger.debug("ws_keepalive_ping uid=%s", uid)
                    
            except Exception as send_error:
                # Si l'envoi échoue, la connexion est probablement morte
                logger.warning("ws_keepalive_send_failed uid=%s error=%s", uid, repr(send_error))
                break
                
    except asyncio.CancelledError:
        # Sortie silencieuse sur annulation
        if _debug_enabled():
            logger.info("ws_keepalive_stopped uid=%s", uid)
        pass
    except Exception as e:
        logger.error("ws_keepalive_error uid=%s error=%s", uid, repr(e))


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


# ═══════════════════════════════════════════════════════════════
# CLOUDWATCH LOGS ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.post("/cloudwatch/logs/list")
async def cloudwatch_list_logs(req: CloudWatchListRequest, authorization: str | None = Header(default=None, alias="Authorization")):
    """
    Liste les streams de logs CloudWatch pour le groupe /ecs/pinnokio_microservice.
    
    Peut être appelé depuis l'extérieur avec authentification.
    """
    try:
        from .tools.cloudwatch_logs import CloudWatchLogsExtractor
        from datetime import datetime, timedelta
        
        _require_auth(authorization)
        
        extractor = CloudWatchLogsExtractor(
            region_name=settings.aws_region_name,
            log_group_name='/ecs/pinnokio_microservice'
        )
        
        # Calculer les dates si spécifiées
        start_time = None
        end_time = None
        
        if req.days:
            start_time = datetime.now() - timedelta(days=req.days)
        
        if req.start_date:
            start_time = datetime.fromisoformat(req.start_date)
        
        if req.end_date:
            end_time = datetime.fromisoformat(req.end_date)
        
        streams = extractor.list_log_streams(
            limit=req.limit,
            order_by=req.order_by,
            descending=req.descending,
            start_time=start_time,
            end_time=end_time
        )
        
        return {
            "status": "success",
            "count": len(streams),
            "streams": streams
        }
    except Exception as e:
        logger.error("cloudwatch_list_logs_error error=%s", repr(e))
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des logs: {str(e)}")


@app.post("/cloudwatch/logs/download")
async def cloudwatch_download_log(req: CloudWatchDownloadRequest, authorization: str | None = Header(default=None, alias="Authorization")):
    """
    Télécharge un log CloudWatch depuis un stream spécifique.
    
    Peut être appelé depuis l'extérieur avec authentification.
    Retourne le contenu du log au format texte ou JSON selon le paramètre json_format.
    """
    try:
        from .tools.cloudwatch_logs import CloudWatchLogsExtractor
        from datetime import datetime
        import tempfile
        import os
        
        _require_auth(authorization)
        
        extractor = CloudWatchLogsExtractor(
            region_name=settings.aws_region_name,
            log_group_name='/ecs/pinnokio_microservice'
        )
        
        # Calculer les dates si spécifiées
        start_time = None
        end_time = None
        
        if req.start_date:
            start_time = datetime.fromisoformat(req.start_date)
        
        if req.end_date:
            end_time = datetime.fromisoformat(req.end_date)
        
        # Créer un fichier temporaire si aucun fichier de sortie n'est spécifié
        temp_file_created = False
        if not req.output_file:
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json' if req.json_format else '.log') as tmp_file:
                req.output_file = tmp_file.name
                temp_file_created = True
        
        # Télécharger le log
        if req.json_format:
            output_file = extractor.download_log_json(
                log_stream_name=req.log_stream_name,
                output_file=req.output_file,
                start_time=start_time,
                end_time=end_time
            )
        else:
            output_file = extractor.download_log(
                log_stream_name=req.log_stream_name,
                output_file=req.output_file,
                start_time=start_time,
                end_time=end_time
            )
        
        # Lire le contenu du fichier
        with open(output_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Nettoyer le fichier temporaire si créé
        if temp_file_created:
            try:
                os.unlink(output_file)
            except:
                pass
        
        return {
            "status": "success",
            "log_stream_name": req.log_stream_name,
            "format": "json" if req.json_format else "text",
            "content": content if not req.json_format else _json.loads(content),
            "file_path": output_file
        }
    except Exception as e:
        logger.error("cloudwatch_download_log_error error=%s", repr(e))
        raise HTTPException(status_code=500, detail=f"Erreur lors du téléchargement du log: {str(e)}")


@app.get("/cloudwatch/logs/info")
async def cloudwatch_logs_info(authorization: str | None = Header(default=None, alias="Authorization")):
    """
    Récupère les informations sur le groupe de journaux CloudWatch.
    
    Peut être appelé depuis l'extérieur avec authentification.
    """
    try:
        from .tools.cloudwatch_logs import CloudWatchLogsExtractor
        
        _require_auth(authorization)
        
        extractor = CloudWatchLogsExtractor(
            region_name=settings.aws_region_name,
            log_group_name='/ecs/pinnokio_microservice'
        )
        
        info = extractor.get_log_group_info()
        
        return {
            "status": "success",
            "info": info
        }
    except Exception as e:
        logger.error("cloudwatch_logs_info_error error=%s", repr(e))
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des informations: {str(e)}")
