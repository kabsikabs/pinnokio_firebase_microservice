"""
Tâches de maintenance périodiques pour le système de registre unifié et listeners.
Exécutées par Celery Beat pour maintenir la santé du système.
"""

import logging
from .task_service import celery_app
from .registry import get_unified_registry, get_registry_listeners
from .firebase_client import get_firestore

logger = logging.getLogger("maintenance_tasks")

@celery_app.task(name='app.maintenance_tasks.cleanup_expired_registries')
def cleanup_expired_registries():
    """
    Nettoie les entrées expirées dans les registres.
    Exécutée toutes les 5 minutes.
    """
    try:
        registry = get_unified_registry()
        registry.cleanup_expired_entries()
        return {"status": "success", "message": "Cleanup completed"}
    except Exception as e:
        logger.error("cleanup_expired_registries_error error=%s", repr(e))
        return {"status": "error", "error": str(e)}

@celery_app.task(name='app.maintenance_tasks.health_check_services')
def health_check_services():
    """
    Vérifie la santé des services connectés.
    Exécutée toutes les minutes.
    """
    try:
        health_status = {
            "redis": _check_redis_health(),
            "firestore": _check_firestore_health(),
            "chroma": _check_chroma_health()
        }
        
        overall_health = all(status["status"] == "ok" for status in health_status.values())
        
        return {
            "status": "healthy" if overall_health else "degraded",
            "services": health_status
        }
    except Exception as e:
        logger.error("health_check_services_error error=%s", repr(e))
        return {"status": "error", "error": str(e)}

def _check_redis_health() -> dict:
    """Vérifie la santé de Redis."""
    try:
        from .redis_client import get_redis
        r = get_redis()
        r.ping()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _check_firestore_health() -> dict:
    """Vérifie la santé de Firestore."""
    try:
        from .firebase_client import get_firestore
        db = get_firestore()
        # Test simple de lecture
        list(db.collections())
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _check_chroma_health() -> dict:
    """Vérifie la santé de ChromaDB."""
    try:
        from .chroma_vector_service import get_chroma_vector_service
        chroma_service = get_chroma_vector_service()
        # Test de heartbeat
        chroma_service.chroma.heartbeat()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task(name='app.maintenance_tasks.cleanup_expired_listeners')
def cleanup_expired_listeners():
    """
    Nettoie automatiquement les listeners expirés dans le registre centralisé.
    
    Cette tâche parcourt tous les utilisateurs ayant des listeners enregistrés,
    vérifie leur heartbeat et supprime ceux dont le TTL est dépassé.
    
    Exécutée toutes les minutes par Celery Beat.
    
    Returns:
        dict: Statut de l'exécution avec le nombre de listeners nettoyés
    """
    try:
        logger.info("cleanup_expired_listeners_start")
        
        db = get_firestore()
        registry = get_registry_listeners()
        
        # Parcourir tous les utilisateurs dans listeners_active
        users_ref = db.collection("listeners_active")
        users_docs = users_ref.stream()
        
        total_cleaned = 0
        total_users_checked = 0
        users_with_expired = []
        
        for user_doc in users_docs:
            uid = user_doc.id
            total_users_checked += 1
            
            try:
                # Lister les listeners de cet utilisateur (include_expired=True)
                result = registry.list_user_listeners(uid, include_expired=True)
                
                if not result.get("success"):
                    logger.error(
                        "cleanup_list_error uid=%s error=%s", 
                        uid, result.get("error")
                    )
                    continue
                
                # Identifier et nettoyer les listeners expirés
                expired_count = 0
                for listener in result.get("listeners", []):
                    if listener.get("status") in ["expired", "zombie"]:
                        # Nettoyer ce listener
                        unregister_result = registry.unregister_listener(
                            user_id=uid,
                            listener_type=listener.get("listener_type"),
                            space_code=listener.get("space_code"),
                            thread_key=listener.get("thread_key")
                        )
                        
                        if unregister_result.get("success"):
                            total_cleaned += 1
                            expired_count += 1
                            logger.info(
                                "listener_expired_cleanup uid=%s listener_id=%s type=%s status=%s",
                                uid, 
                                listener.get("listener_id"),
                                listener.get("listener_type"),
                                listener.get("status")
                            )
                        else:
                            logger.error(
                                "listener_cleanup_error uid=%s listener_id=%s error=%s",
                                uid,
                                listener.get("listener_id"),
                                unregister_result.get("error")
                            )
                
                if expired_count > 0:
                    users_with_expired.append({
                        "uid": uid,
                        "cleaned_count": expired_count
                    })
                    
            except Exception as e:
                logger.error("cleanup_user_error uid=%s error=%s", uid, repr(e))
                continue
        
        logger.info(
            "cleanup_expired_listeners_complete users_checked=%s total_cleaned=%s users_with_expired=%s",
            total_users_checked,
            total_cleaned,
            len(users_with_expired)
        )
        
        return {
            "status": "success",
            "total_cleaned": total_cleaned,
            "users_checked": total_users_checked,
            "users_with_expired": len(users_with_expired),
            "details": users_with_expired[:10]  # Limiter à 10 pour éviter logs trop longs
        }
        
    except Exception as e:
        logger.error("cleanup_expired_listeners_error error=%s", repr(e), exc_info=True)
        return {
            "status": "error",
            "error": str(e)
        }

