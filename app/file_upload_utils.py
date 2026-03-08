"""
File Upload Utilities
=====================

Shared auth, validation, and constants for file upload endpoints.

Usage:
    from app.file_upload_utils import verify_firebase_id_token, validate_file
"""

import os
import logging
import mimetypes

from firebase_admin import auth as firebase_auth
from .firebase_client import get_firebase_app

logger = logging.getLogger("file_upload")

# ─── Constants ───

MAX_CHAT_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_ROUTING_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

CHAT_ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
}

ROUTING_ALLOWED_EXTENSIONS = {
    ".pdf", ".xls", ".xlsx", ".png", ".jpeg", ".jpg",
    ".doc", ".docx", ".csv", ".txt",
}

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "pinnokio-gpt.appspot.com")


# ─── Auth ───

def verify_firebase_id_token(authorization: str) -> str:
    """
    Verify a Firebase ID token from the Authorization header.

    Same pattern as auth_handlers.py:104.

    Args:
        authorization: "Bearer <token>" header value.

    Returns:
        uid (str) on success.

    Raises:
        ValueError: Missing/malformed header.
        firebase_admin.auth.InvalidIdTokenError: Invalid token.
        firebase_admin.auth.ExpiredIdTokenError: Expired token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise ValueError("Missing or malformed Authorization header")

    token = authorization[len("Bearer "):]
    decoded = firebase_auth.verify_id_token(
        token,
        app=get_firebase_app(),
        clock_skew_seconds=5,
    )
    return decoded["uid"]


# ─── Validation ───

def validate_file(filename: str, allowed_extensions: set, max_size: int) -> str:
    """
    Validate file extension against an allow-list.

    Args:
        filename: Original filename.
        allowed_extensions: Set of allowed extensions (e.g. {".pdf", ".png"}).
        max_size: Max allowed size in bytes (used for error message only;
                  caller checks actual bytes).

    Returns:
        Lowercase extension (e.g. ".pdf").

    Raises:
        ValueError: If extension is not allowed.
    """
    if not filename:
        raise ValueError("Filename is required")

    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    if ext not in allowed_extensions:
        raise ValueError(
            f"Extension '{ext}' not allowed. "
            f"Accepted: {', '.join(sorted(allowed_extensions))}"
        )

    # Advisory MIME check — warn but don't block
    guessed_type, _ = mimetypes.guess_type(filename)
    if guessed_type and ext in {".pdf"} and "pdf" not in (guessed_type or ""):
        logger.warning(
            f"[FILE_UPLOAD] MIME mismatch: filename={filename} ext={ext} guessed={guessed_type}"
        )

    return ext
