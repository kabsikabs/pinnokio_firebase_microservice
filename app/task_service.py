"""
Service de gestion des tâches avec Celery pour l'exécution de tâches parallèles.
Intégré avec le système de registre unifié pour l'isolation par utilisateur/société.
"""

import os
from celery import Celery
from celery.schedules import crontab
from .config import get_settings

# Configuration Celery utilisant votre Redis existant
settings = get_settings()

# Construction de l'URL Redis pour Celery
if settings.use_local_redis:
    redis_url = f"redis://127.0.0.1:6379/1"  # DB 1 pour les tâches (séparer de l'event bus)
else:
    # Production avec TLS
    if settings.redis_tls:
        redis_url = f"rediss://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/1"
    else:
        redis_url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/1"

# Création de l'instance Celery
celery_app = Celery(
    'firebase_microservice_tasks',
    broker=redis_url,
    backend=redis_url,
    include=['app.computation_tasks']  # Module contenant les tâches
)

# Configuration Celery
celery_app.conf.update(
    # Sérialisation
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    
    # Timezone
    timezone='UTC',
    enable_utc=True,
    
    # Tâches
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes max par tâche
    task_soft_time_limit=25 * 60,  # Warning à 25 minutes
    
    # Worker
    worker_prefetch_multiplier=1,  # Une tâche à la fois par worker
    worker_max_tasks_per_child=1000,  # Redémarrer worker après 1000 tâches
    worker_disable_rate_limits=False,
    
    # Résultats
    result_expires=3600,  # Garder les résultats 1 heure
    
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
    
    # Routage des tâches
    task_routes={
        'app.computation_tasks.compute_document_analysis': {'queue': 'document_processing'},
        'app.computation_tasks.compute_vector_embeddings': {'queue': 'vector_processing'},
        'app.computation_tasks.process_llm_conversation': {'queue': 'llm_processing'},
    },
    
    # Queues par défaut
    task_default_queue='default',
    task_default_exchange='default',
    task_default_exchange_type='direct',
    task_default_routing_key='default',
)

# Configuration des queues
celery_app.conf.task_routes = {
    'app.computation_tasks.*': {'queue': 'computation'},
    'app.llm_tasks.*': {'queue': 'llm'},
    'app.maintenance_tasks.*': {'queue': 'maintenance'},
}

# Configuration du beat scheduler (tâches périodiques)
celery_app.conf.beat_schedule = {
    'cleanup-expired-registries': {
        'task': 'app.maintenance_tasks.cleanup_expired_registries',
        'schedule': 300.0,  # Toutes les 5 minutes
    },
    'health-check-services': {
        'task': 'app.maintenance_tasks.health_check_services',
        'schedule': 60.0,  # Toutes les minutes
    },
    'cleanup-expired-listeners': {
        'task': 'app.maintenance_tasks.cleanup_expired_listeners',
        'schedule': 60.0,  # Toutes les minutes - Nettoie les listeners expirés
    },
    # 💳 Facturation chat journalière (veille UTC)
    'billing-finalize-daily-chat': {
        'task': 'app.maintenance_tasks.finalize_daily_chat_billing',
        # Toutes les heures à :20 UTC (rattrape automatiquement les jours manqués)
        'schedule': crontab(minute=20, hour='*/1'),
        'kwargs': {'days_back': 7},
    },
}

# Fonction utilitaire pour publier les événements de progression
def publish_task_progress(user_id: str, task_id: str, status: str, progress: int, data: dict = None):
    """Publie la progression d'une tâche via le système de messaging existant."""
    try:
        from datetime import datetime, timezone
        from .main import listeners_manager
        import logging
        
        payload = {
            "type": "task.progress_update",
            "uid": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "task_id": task_id,
                "status": status,
                "progress": progress,
                "data": data or {}
            }
        }
        
        if listeners_manager:
            listeners_manager.publish(user_id, payload)
            
    except Exception as e:
        logger = logging.getLogger("task_service")
        logger.error("publish_task_progress_error task_id=%s error=%s", task_id, repr(e))

# Export de l'instance Celery
__all__ = ['celery_app', 'publish_task_progress']

