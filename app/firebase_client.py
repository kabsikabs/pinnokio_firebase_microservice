import json
import os
from typing import Optional

import firebase_admin
from firebase_admin import credentials, initialize_app
from google.cloud import firestore
from google.oauth2 import service_account

from .tools.g_cred import get_secret

_FIREBASE_APP: Optional[firebase_admin.App] = None
_FIRESTORE_CLIENT: Optional[firestore.Client] = None
_SA_INFO: Optional[dict] = None


def _load_service_account_info() -> dict:
    global _SA_INFO
    if _SA_INFO is not None:
        return _SA_INFO

    # 1) JSON direct via env (dev/local)
    env_json = os.getenv("FIREBASE_ADMIN_JSON")
    if env_json:
        _SA_INFO = json.loads(env_json)
        return _SA_INFO

    # 2) Secret GSM configurable (secret-id sans slash OU ressource complète projects/*/secrets/*/versions/*)
    secret_name = os.getenv("FIREBASE_ADMIN_SECRET_NAME", "pinnokio-listeners-firebase-admin")
    secret_json = get_secret(secret_name)
    _SA_INFO = json.loads(secret_json)
    return _SA_INFO


def get_firebase_app() -> firebase_admin.App:
    global _FIREBASE_APP
    if _FIREBASE_APP is not None:
        return _FIREBASE_APP
    sa_info = _load_service_account_info()
    cred = credentials.Certificate(sa_info)
    _FIREBASE_APP = initialize_app(cred)
    return _FIREBASE_APP


def get_firestore() -> firestore.Client:
    global _FIRESTORE_CLIENT
    if _FIRESTORE_CLIENT is not None:
        return _FIRESTORE_CLIENT

    sa_info = _load_service_account_info()
    creds = service_account.Credentials.from_service_account_info(sa_info)
    project = os.getenv("GOOGLE_PROJECT_ID") or sa_info.get("project_id")
    _FIRESTORE_CLIENT = firestore.Client(project=project, credentials=creds)
    return _FIRESTORE_CLIENT
