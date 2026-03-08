#!/bin/bash

# Script de démarrage pour le microservice Firebase
# Supporte différents modes : api, worker, beat

set -e

echo "🚀 Démarrage du microservice Firebase..."
echo "Mode: ${CONTAINER_TYPE:-api}"
echo "Registre unifié: ${UNIFIED_REGISTRY_ENABLED:-false}"

# Configuration par défaut
export PYTHONPATH="/app:$PYTHONPATH"

# Decode GCP service account from B64 env var (ECS injection)
if [ -n "${GOOGLE_SERVICE_ACCOUNT_JSON_B64}" ]; then
    echo "🔑 Decoding GCP service account from B64..."
    echo "${GOOGLE_SERVICE_ACCOUNT_JSON_B64}" | base64 -d > /tmp/service_account.json
    export GOOGLE_APPLICATION_CREDENTIALS="/tmp/service_account.json"
    echo "✅ GOOGLE_APPLICATION_CREDENTIALS set to /tmp/service_account.json"
fi

case "${CONTAINER_TYPE:-api}" in
    "worker")
        echo "🔧 Démarrage Celery Worker..."
        echo "Queues: ${CELERY_QUEUES:-default,computation,llm,maintenance}"
        exec celery -A app.task_service worker \
            --loglevel=info \
            --concurrency=${CELERY_CONCURRENCY:-4} \
            --queues=${CELERY_QUEUES:-default,computation,llm,maintenance} \
            --hostname=worker@%h
        ;;
    
    "beat")
        echo "📅 Démarrage Celery Beat (Scheduler)..."
        exec celery -A app.task_service beat \
            --loglevel=info \
            --schedule=/tmp/celerybeat-schedule \
            --pidfile=/tmp/celerybeat.pid
        ;;
    
    "flower")
        echo "🌸 Démarrage Celery Flower (Monitoring)..."
        exec celery -A app.task_service flower \
            --port=5555 \
            --broker_api=http://guest:guest@localhost:15672/api/
        ;;
    
    "api"|*)
        echo "🌐 Démarrage FastAPI Server..."
        echo "Port: ${PORT:-8090}"
        echo "Workers: ${UVICORN_WORKERS:-1}"
        
        # Vérification des services avant démarrage
        if [ "${UNIFIED_REGISTRY_ENABLED:-false}" = "true" ]; then
            echo "✅ Mode registre unifié activé"
        else
            echo "📝 Mode legacy (registre unifié désactivé)"
        fi
        
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port ${PORT:-8090} \
            --workers ${UVICORN_WORKERS:-1} \
            --log-level ${LOG_LEVEL:-info}
        ;;
esac

