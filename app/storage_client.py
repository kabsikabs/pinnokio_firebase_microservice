"""
StorageClientSingleton - Google Cloud Storage Singleton
========================================================

Thread-safe singleton for Google Cloud Storage operations.
Follows the same pattern as ChromaVectorService and DriveClientServiceSingleton.

Usage:
    from app.storage_client import get_storage_client

    client = get_storage_client()
    client.delete_path("path/to/folder", recursive=True)
"""

from __future__ import annotations

import os
import threading
import logging
from typing import Optional

from google.cloud import storage
from .tools.g_cred import get_secret

logger = logging.getLogger("storage_client")

_STORAGE_CLIENT_SINGLETON: Optional["StorageClientSingleton"] = None


class StorageClientSingleton:
    """
    Singleton thread-safe pour Google Cloud Storage.

    Garantit une seule instance avec une seule connexion GCS.
    """

    _instance: Optional["StorageClientSingleton"] = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._initialize()
                    self.__class__._initialized = True

    def _initialize(self):
        """Initialize the GCS client."""
        try:
            bucket_name = os.getenv("GCS_BUCKET_NAME", "pinnokio-gpt.appspot.com")

            # Use default credentials (from service account / GOOGLE_APPLICATION_CREDENTIALS)
            self._client = storage.Client()
            self._bucket = self._client.bucket(bucket_name)

            logger.info(f"StorageClientSingleton initialized - bucket={bucket_name}")
        except Exception as e:
            logger.error(f"StorageClientSingleton initialization failed: {e}")
            raise

    def delete_path(self, path: str, recursive: bool = True, bucket_name: str = None) -> dict:
        """
        Delete a path (file or folder) from GCS.

        Args:
            path: The GCS path (prefix) to delete.
            recursive: If True, deletes all blobs under this prefix.
            bucket_name: Optional bucket name (uses default if not specified).

        Returns:
            {"success": True/False, "deleted_count": int, "error": str (if failed)}
        """
        try:
            if not path or not path.strip():
                return {"success": False, "deleted_count": 0, "error": "Empty path"}

            # Normalize path - remove leading slash
            clean_path = path.lstrip("/")
            
            # Use specified bucket or default
            if bucket_name and bucket_name != self._bucket.name:
                bucket = self._client.bucket(bucket_name)
            else:
                bucket = self._bucket

            if recursive:
                blobs = list(bucket.list_blobs(prefix=clean_path))
                if not blobs:
                    logger.info(f"[GCS] No blobs found at prefix: {bucket_name or self._bucket.name}/{clean_path}")
                    return {"success": True, "deleted_count": 0}

                # Delete in batches
                deleted = 0
                for blob in blobs:
                    try:
                        blob.delete()
                        deleted += 1
                    except Exception as e:
                        logger.warning(f"[GCS] Failed to delete blob {blob.name}: {e}")

                logger.info(f"[GCS] Deleted {deleted}/{len(blobs)} blobs at prefix: {bucket_name or self._bucket.name}/{clean_path}")
                return {"success": True, "deleted_count": deleted}
            else:
                # Delete single blob
                blob = bucket.blob(clean_path)
                if blob.exists():
                    blob.delete()
                    logger.info(f"[GCS] Deleted blob: {bucket_name or self._bucket.name}/{clean_path}")
                    return {"success": True, "deleted_count": 1}
                else:
                    logger.info(f"[GCS] Blob not found: {bucket_name or self._bucket.name}/{clean_path}")
                    return {"success": True, "deleted_count": 0}

        except Exception as e:
            logger.error(f"[GCS] delete_path failed for '{path}': {e}")
            return {"success": False, "deleted_count": 0, "error": str(e)}

    def download_blob(self, bucket_name: str, blob_name: str):
        """
        Download a blob from GCS and return it as a file-like object.

        Args:
            bucket_name: The GCS bucket name (can be different from default bucket).
            blob_name: The path/name of the blob to download.

        Returns:
            BytesIO file-like object containing the blob content.
        """
        import io

        try:
            # Use specified bucket or default
            if bucket_name and bucket_name != self._bucket.name:
                bucket = self._client.bucket(bucket_name)
            else:
                bucket = self._bucket

            blob = bucket.blob(blob_name)

            if not blob.exists():
                logger.warning(f"[GCS] Blob not found: {bucket_name}/{blob_name}")
                return None

            # Download to memory
            content = blob.download_as_bytes()
            file_in_memory = io.BytesIO(content)
            file_in_memory.seek(0)

            logger.info(f"[GCS] Downloaded blob: {bucket_name}/{blob_name} ({len(content)} bytes)")
            return file_in_memory

        except Exception as e:
            logger.error(f"[GCS] download_blob failed for '{bucket_name}/{blob_name}': {e}")
            raise

    def upload_blob(self, bucket_name: str, blob_name: str, data, content_type: str = None):
        """
        Upload data to a blob in GCS.

        Args:
            bucket_name: The GCS bucket name.
            blob_name: The path/name for the blob.
            data: The data to upload (bytes, string, or file-like object).
            content_type: Optional content type (e.g., 'application/json').

        Returns:
            {"success": True/False, "blob_path": str, "error": str (if failed)}
        """
        try:
            # Use specified bucket or default
            if bucket_name and bucket_name != self._bucket.name:
                bucket = self._client.bucket(bucket_name)
            else:
                bucket = self._bucket

            blob = bucket.blob(blob_name)

            # Handle different data types
            if isinstance(data, bytes):
                blob.upload_from_string(data, content_type=content_type)
            elif isinstance(data, str):
                blob.upload_from_string(data.encode('utf-8'), content_type=content_type)
            elif hasattr(data, 'read'):
                # File-like object
                blob.upload_from_file(data, content_type=content_type)
            else:
                raise ValueError(f"Unsupported data type: {type(data)}")

            logger.info(f"[GCS] Uploaded blob: {bucket_name}/{blob_name}")
            return {"success": True, "blob_path": f"gs://{bucket_name}/{blob_name}"}

        except Exception as e:
            logger.error(f"[GCS] upload_blob failed for '{bucket_name}/{blob_name}': {e}")
            return {"success": False, "error": str(e)}

    def list_blobs(self, prefix: str = None, bucket_name: str = None) -> list:
        """
        List blobs in the bucket with optional prefix filter.

        Args:
            prefix: Optional prefix to filter blobs.
            bucket_name: Optional bucket name (uses default if not specified).

        Returns:
            List of blob names.
        """
        try:
            if bucket_name and bucket_name != self._bucket.name:
                bucket = self._client.bucket(bucket_name)
            else:
                bucket = self._bucket

            blobs = bucket.list_blobs(prefix=prefix)
            return [blob.name for blob in blobs]

        except Exception as e:
            logger.error(f"[GCS] list_blobs failed: {e}")
            return []

    def folder_exists_or_create(self, bucket_name: str, folder_path: str):
        """
        Vérifie si un dossier existe dans le bucket, sinon le crée.
        
        Note: GCS n'a pas de vrais dossiers - on crée un blob vide avec un trailing '/'
        pour simuler un dossier.
        
        Args:
            bucket_name: Nom du bucket GCS.
            folder_path: Chemin du dossier (ex: "clients/123/documents/").
        """
        try:
            # Assurer que le chemin se termine par '/' pour indiquer un dossier
            if not folder_path.endswith('/'):
                folder_path = folder_path + '/'
            
            # Utiliser le bucket spécifié ou le bucket par défaut
            if bucket_name and bucket_name != self._bucket.name:
                bucket = self._client.bucket(bucket_name)
            else:
                bucket = self._bucket
            
            blob = bucket.blob(folder_path)

            if not blob.exists():
                # Créer un blob vide pour simuler un dossier
                # b"" est suffisant, pas besoin de io.BytesIO()
                blob.upload_from_string(b"", content_type="application/x-directory")
                logger.info(f"[GCS] Folder created: {bucket_name}/{folder_path}")
            else:
                logger.debug(f"[GCS] Folder already exists: {bucket_name}/{folder_path}")
                
        except Exception as e:
            logger.error(f"[GCS] folder_exists_or_create failed for '{bucket_name}/{folder_path}': {e}")
            raise


def get_storage_client() -> StorageClientSingleton:
    """
    Returns the singleton instance of StorageClientSingleton.
    Thread-safe, initializes on first call.
    """
    global _STORAGE_CLIENT_SINGLETON
    if _STORAGE_CLIENT_SINGLETON is None:
        _STORAGE_CLIENT_SINGLETON = StorageClientSingleton()
    return _STORAGE_CLIENT_SINGLETON
