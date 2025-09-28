"""
Tâches de maintenance périodiques pour le système de registre unifié.
Exécutées par Celery Beat pour maintenir la santé du système.
"""

from .task_service import celery_app
from .unified_registry import get_unified_registry

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
        print(f"❌ Erreur cleanup registres: {e}")
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
        print(f"❌ Erreur health check: {e}")
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

