FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installation des dépendances système (ajout de curl pour health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip

# Installation des dépendances Python
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code de l'application
COPY app ./app
COPY start.sh ./start.sh

# Permissions d'exécution pour le script
RUN chmod +x start.sh

EXPOSE 8090
ENV PORT=8090
ENV CONTAINER_TYPE=api

# Utilisation du script de démarrage flexible
CMD ["./start.sh"]
