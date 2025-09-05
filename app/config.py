import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _str_to_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Settings:
    aws_region_name: str
    google_project_id: str | None
    google_service_account_secret: str | None
    aws_secret_name: str | None
    redis_host: str | None
    redis_port: int
    redis_password: str | None
    redis_tls: bool
    redis_db: int
    channel_prefix: str
    redis_tls_verify: bool
    use_local_redis: bool


def get_settings() -> Settings:
    use_local = _str_to_bool(os.getenv("USE_LOCAL_REDIS"))

    # Lire bruts pour pouvoir surcharger proprement
    raw_host = os.getenv("LISTENERS_REDIS_HOST")
    raw_port = os.getenv("LISTENERS_REDIS_PORT")
    raw_pwd = os.getenv("LISTENERS_REDIS_PASSWORD")
    raw_tls = os.getenv("LISTENERS_REDIS_TLS")
    raw_db = os.getenv("LISTENERS_REDIS_DB")
    raw_tls_verify = os.getenv("LISTENERS_REDIS_TLS_VERIFY", "true")

    if use_local:
        # FORÇAGE LOCAL, on ignore les valeurs cloud
        host = "127.0.0.1"
        port = 6379
        password = None
        tls = False
        db = int(raw_db or "0")
        tls_verify = False
    else:
        host = raw_host
        port = int(raw_port or "6379")
        password = raw_pwd
        tls = _str_to_bool(raw_tls)
        db = int(raw_db or "0")
        tls_verify = _str_to_bool(raw_tls_verify)

    return Settings(
        aws_region_name=os.getenv("AWS_REGION_NAME", "us-east-1"),
        google_project_id=os.getenv("GOOGLE_PROJECT_ID"),
        google_service_account_secret=os.getenv("GOOGLE_SERVICE_ACCOUNT_SECRET"),
        aws_secret_name=os.getenv("AWS_SECRET_NAME"),
        redis_host=host,
        redis_port=port,
        redis_password=password,
        redis_tls=tls,
        redis_db=db,
        channel_prefix=os.getenv("LISTENERS_CHANNEL_PREFIX", "user:"),
        redis_tls_verify=tls_verify,
        use_local_redis=use_local,
    )
