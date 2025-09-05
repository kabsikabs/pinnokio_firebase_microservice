import json
import os
from typing import Optional

from google.cloud import secretmanager
from google.oauth2 import service_account

_client_cache: Optional[secretmanager.SecretManagerServiceClient] = None


def _resolve_version_path(secret_name: str) -> str:
    if secret_name.startswith("projects/"):
        return secret_name if "/versions/" in secret_name else f"{secret_name}/versions/latest"
    project_id = os.getenv("GOOGLE_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GOOGLE_PROJECT_ID manquant pour accéder aux secrets")
    return f"projects/{project_id}/secrets/{secret_name}/versions/latest"


def _access_secret(client: secretmanager.SecretManagerServiceClient, secret_name: str) -> str:
    name = _resolve_version_path(secret_name)
    resp = client.access_secret_version(request={"name": name})
    return resp.payload.data.decode("utf-8")


def _build_client_with_optional_sa() -> secretmanager.SecretManagerServiceClient:
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    sa_secret_name = os.getenv("GOOGLE_SERVICE_ACCOUNT_SECRET")
    if sa_secret_name:
        # Bootstrap avec ADC pour récupérer la clé du compte de service
        bootstrap_client = secretmanager.SecretManagerServiceClient()
        sa_json = _access_secret(bootstrap_client, sa_secret_name)
        credentials = service_account.Credentials.from_service_account_info(json.loads(sa_json))
        _client_cache = secretmanager.SecretManagerServiceClient(credentials=credentials)
        return _client_cache

    _client_cache = secretmanager.SecretManagerServiceClient()
    return _client_cache


def get_secret(secret_name: str) -> str:
    client = _build_client_with_optional_sa()
    return _access_secret(client, secret_name)


def create_secret(secret_data: str) -> str:
    project_id = os.getenv("GOOGLE_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GOOGLE_PROJECT_ID requis")
    client = _build_client_with_optional_sa()

    import uuid

    secret_id = f"created-{uuid.uuid4().hex[:8]}"
    parent = f"projects/{project_id}"
    secret = client.create_secret(
        request={
            "parent": parent,
            "secret_id": secret_id,
            "secret": {"replication": {"automatic": {}}},
        }
    )
    client.add_secret_version(
        request={"parent": secret.name, "payload": {"data": secret_data.encode("utf-8")}}
    )
    return secret.name


def get_aws_credentials_from_gsm() -> dict:
    secret_name = os.getenv("AWS_SECRET_NAME")
    if not secret_name:
        return {}
    data = get_secret(secret_name)
    return json.loads(data)
