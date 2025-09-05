from typing import Optional

import redis

from .config import get_settings

_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    settings = get_settings()

    redis_kwargs = {
        "host": settings.redis_host,
        "port": settings.redis_port,
        "password": settings.redis_password or None,
        "db": settings.redis_db,
        "socket_connect_timeout": 5,
        "health_check_interval": 30,
    }

    if settings.redis_tls:
        redis_kwargs["ssl"] = True
        # Désactive la vérification TLS si demandé (utile pour tests hors VPC)
        if not settings.redis_tls_verify:
            redis_kwargs["ssl_cert_reqs"] = None  # type: ignore[assignment]

    _redis_client = redis.Redis(**redis_kwargs)
    return _redis_client
