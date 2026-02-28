"""
File Upload Endpoints
=====================

Pipeline 1: POST /upload/chat-file   → GCS (chat_files/{thread_key}/)
Pipeline 2: POST /upload/routing-file → Google Drive (input_drive_doc_id) + WS broadcast

Both endpoints require Firebase ID token authentication (Authorization: Bearer <token>).
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from app.file_upload_utils import (
    CHAT_ALLOWED_EXTENSIONS,
    GCS_BUCKET_NAME,
    MAX_CHAT_FILE_SIZE,
    MAX_ROUTING_FILE_SIZE,
    ROUTING_ALLOWED_EXTENSIONS,
    validate_file,
    verify_firebase_id_token,
)

logger = logging.getLogger("file_upload")

router = APIRouter(tags=["file-upload"])


# ──────────────────────────────────────────────
# Pipeline 1 — Chat File Upload (GCS)
# ──────────────────────────────────────────────

@router.post("/upload/chat-file")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    thread_key: str = Form(...),
    company_id: str = Form(...),
):
    """
    Upload a file attached to a chat conversation.

    Stored in GCS at `chat_files/{thread_key}/{uuid8}_{filename}`.
    Returns a 7-day signed download URL.
    Auto-deleted when the chat session is deleted (+ GCS lifecycle 30d).
    """
    # 1. Auth
    authorization = request.headers.get("Authorization", "")
    try:
        uid = verify_firebase_id_token(authorization)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    # 2. Validate extension
    try:
        ext = validate_file(file.filename, CHAT_ALLOWED_EXTENSIONS, MAX_CHAT_FILE_SIZE)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. Read bytes + check size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_CHAT_FILE_SIZE:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "error": f"File too large ({len(file_bytes)} bytes). Max {MAX_CHAT_FILE_SIZE // (1024*1024)} MB.",
            },
        )

    # 4. Build GCS path
    short_id = uuid.uuid4().hex[:8]
    safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in file.filename)
    blob_name = f"chat_files/{thread_key}/{short_id}_{safe_name}"

    # 5. Upload to GCS
    from app.storage_client import get_storage_client

    storage = get_storage_client()
    result = storage.upload_blob(GCS_BUCKET_NAME, blob_name, file_bytes, file.content_type)

    if not result.get("success"):
        return JSONResponse(
            status_code=200,
            content={"success": False, "error": result.get("error", "GCS upload failed")},
        )

    # 6. Generate signed URL (7 days)
    try:
        download_url = storage.generate_signed_url(blob_name, expiration_hours=168)
    except Exception as url_err:
        logger.warning(f"[CHAT_UPLOAD] Signed URL generation failed: {url_err}")
        download_url = None

    logger.info(
        f"[CHAT_UPLOAD] success uid={uid} thread_key={thread_key} "
        f"blob={blob_name} size={len(file_bytes)}"
    )

    return {
        "success": True,
        "file": {
            "gcs_path": blob_name,
            "download_url": download_url,
            "filename": file.filename,
            "size": len(file_bytes),
            "content_type": file.content_type,
            "thread_key": thread_key,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ──────────────────────────────────────────────
# Pipeline 2 — Routing Upload to Drive
# ──────────────────────────────────────────────

@router.post("/upload/routing-file", status_code=202)
async def upload_routing_file(
    request: Request,
    file: UploadFile = File(...),
    company_id: str = Form(...),
):
    """
    Upload a file to the company's Drive input folder.

    Returns 202 immediately; the actual Drive upload runs in background.
    On success → WS event `routing.uploaded`.
    On failure → WS event `routing.error`.
    """
    # 1. Auth
    authorization = request.headers.get("Authorization", "")
    try:
        uid = verify_firebase_id_token(authorization)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    # 2. Validate extension
    try:
        ext = validate_file(file.filename, ROUTING_ALLOWED_EXTENSIONS, MAX_ROUTING_FILE_SIZE)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. Read bytes + check size (before 202 response)
    file_bytes = await file.read()
    if len(file_bytes) > MAX_ROUTING_FILE_SIZE:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "error": f"File too large ({len(file_bytes)} bytes). Max {MAX_ROUTING_FILE_SIZE // (1024*1024)} MB.",
            },
        )

    # 4. Lookup input_drive_doc_id from Redis L2 cache
    from app.redis_client import get_redis

    r = get_redis()
    context_key = f"company:{uid}:{company_id}:context"
    raw_context = r.get(context_key)

    if not raw_context:
        return JSONResponse(
            status_code=200,
            content={"success": False, "code": "CONTEXT_NOT_FOUND", "error": "Company context not found in cache"},
        )

    try:
        context = json.loads(raw_context) if isinstance(raw_context, str) else raw_context
    except (json.JSONDecodeError, TypeError):
        return JSONResponse(
            status_code=200,
            content={"success": False, "code": "CONTEXT_NOT_FOUND", "error": "Invalid company context data"},
        )

    input_drive_id = context.get("input_drive_doc_id") or context.get("inputDriveDocId")
    mandate_path = context.get("mandatePath", context.get("mandate_path", ""))

    if not input_drive_id:
        return JSONResponse(
            status_code=200,
            content={"success": False, "code": "CONTEXT_NOT_FOUND", "error": "input_drive_doc_id not found in context"},
        )

    # 5. Fire background task and return 202
    asyncio.create_task(
        _background_drive_upload(
            uid=uid,
            company_id=company_id,
            file_bytes=file_bytes,
            file_name=file.filename,
            content_type=file.content_type or "application/octet-stream",
            input_drive_id=input_drive_id,
            mandate_path=mandate_path,
        )
    )

    logger.info(
        f"[ROUTING_UPLOAD] accepted uid={uid} company={company_id} "
        f"file={file.filename} size={len(file_bytes)} drive_folder={input_drive_id}"
    )

    return {"success": True, "message": "Upload accepted, processing in background"}


async def _background_drive_upload(
    uid: str,
    company_id: str,
    file_bytes: bytes,
    file_name: str,
    content_type: str,
    input_drive_id: str,
    mandate_path: str,
):
    """Background task: upload to Drive → refresh cache → broadcast WS event."""
    from app.driveClientService import DriveClientServiceSingleton
    from app.ws_hub import hub

    try:
        drive = DriveClientServiceSingleton()
        result = await drive.upload_file_to_drive(
            user_id=uid,
            file_bytes=file_bytes,
            file_name=file_name,
            folder_id=input_drive_id,
            mime_type=content_type,
        )

        if not result.get("success"):
            error_msg = result.get("error", "Drive upload failed")
            logger.error(f"[ROUTING_UPLOAD] Drive upload failed uid={uid} error={error_msg}")
            await hub.broadcast(uid, {
                "type": "routing.error",
                "payload": {
                    "error": error_msg,
                    "file_name": file_name,
                    "oauth_reauth_required": result.get("oauth_reauth_required", False),
                },
            })
            return

        logger.info(
            f"[ROUTING_UPLOAD] Drive upload success uid={uid} "
            f"file_id={result.get('file_id')} file={file_name}"
        )

        # Refresh Drive cache
        try:
            from app.drive_cache_handlers import drive_cache_handlers

            await drive_cache_handlers.refresh_documents(
                user_id=uid,
                company_id=company_id,
                input_drive_id=input_drive_id,
                mandate_path=mandate_path,
            )
            logger.info(f"[ROUTING_UPLOAD] Cache refreshed uid={uid} company={company_id}")
        except Exception as cache_err:
            logger.warning(f"[ROUTING_UPLOAD] Cache refresh failed (non-blocking): {cache_err}")

        # Broadcast success event
        await hub.broadcast(uid, {
            "type": "routing.uploaded",
            "payload": {
                "file_id": result.get("file_id"),
                "file_name": result.get("file_name", file_name),
                "web_view_link": result.get("web_view_link"),
                "company_id": company_id,
            },
        })

    except Exception as exc:
        logger.error(f"[ROUTING_UPLOAD] Background task failed uid={uid}: {exc}", exc_info=True)
        try:
            await hub.broadcast(uid, {
                "type": "routing.error",
                "payload": {
                    "error": str(exc),
                    "file_name": file_name,
                    "oauth_reauth_required": "credentials" in str(exc).lower(),
                },
            })
        except Exception:
            pass
