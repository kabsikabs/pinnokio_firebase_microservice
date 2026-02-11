"""
Active Job Manager - Centralized active_jobs Queue Management
=============================================================

Manages the Firebase `active_jobs` collection as the single source of truth
for job queueing. Called by the backend BEFORE dispatching to workers.

Architecture:
    Backend receives request -> ActiveJobManager.register_job() -> active_jobs/{type}/jobs
    Backend sends HTTP/ECS dispatch -> Worker reads active_jobs -> processes

Firebase Structure:
    active_jobs/{job_type}/jobs/{encoded_mandate}_{job_key}
        - job_key, job_id, batch_id (aliases)
        - mandate_path, mandates_path (compat)
        - job_type, status ("running"|"pending")
        - payload (job_data wrapped)
        - stop_requested, stop_requested_transactions
        - created_at, started_at, last_updated, position_in_queue

Author: Migration Agent
Created: 2026-02-11
"""

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .firebase_client import get_firestore

logger = logging.getLogger("active_job_manager")


class ActiveJobManager:
    """
    Centralized management of the active_jobs Firebase queue.
    Called by the backend BEFORE dispatch to workers.
    """

    # ─────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────

    @staticmethod
    def _encode_mandate_path(mandate_path: str) -> str:
        """
        Encode mandate_path for Firestore (single segment).

        Identical logic to klk_router/tools/jobstatus_manager.py to ensure
        the same document IDs are produced.

        Example:
            "clients/user123/mandates/mandate456"
            -> "clients_user123_mandates_mandate456"
        """
        if not mandate_path:
            return "unknown"

        encoded = mandate_path.replace("/", "_").replace(".", "_dot_").replace("#", "_hash_")

        if len(encoded) > 1000:
            encoded_bytes = mandate_path.encode("utf-8")
            encoded = base64.urlsafe_b64encode(encoded_bytes).decode("utf-8").rstrip("=")
            encoded = f"b64_{encoded}"

        return encoded

    @staticmethod
    def _decode_mandate_path(encoded_path: str) -> str:
        """Decode mandate_path from Firestore."""
        if not encoded_path or encoded_path == "unknown":
            return ""

        if encoded_path.startswith("b64_"):
            try:
                b64_part = encoded_path[4:]
                padding = 4 - (len(b64_part) % 4)
                if padding != 4:
                    b64_part += "=" * padding
                return base64.urlsafe_b64decode(b64_part).decode("utf-8")
            except Exception as e:
                logger.error(f"Error decoding base64 {encoded_path}: {e}")
                return encoded_path

        return encoded_path.replace("_dot_", ".").replace("_hash_", "#").replace("_", "/")

    # ─────────────────────────────────────────────
    # REGISTRATION (backend-side, before dispatch)
    # ─────────────────────────────────────────────

    @staticmethod
    def register_job(
        mandate_path: str,
        job_data: dict,
        job_key: str,
        job_type: str,
    ) -> Dict[str, Any]:
        """
        Register a single job in active_jobs. Uses a Firestore transaction
        to determine if the job should start immediately or queue as pending.

        Args:
            mandate_path: Firebase mandate path (e.g. "clients/uid/mandates/mid")
            job_data: Full job payload
            job_key: Unique job identifier (job_id for router/AP, batch_id for banker)
            job_type: "router" | "apbookeeper" | "banker"

        Returns:
            {should_start, job_key, status, position_in_queue}
        """
        db = get_firestore()

        try:
            transaction = db.transaction()
            encoded = ActiveJobManager._encode_mandate_path(mandate_path)
            result = ActiveJobManager._register_job_transaction(
                transaction, db, mandate_path, encoded, job_key, job_data, job_type,
            )
            logger.info(
                f"[ACTIVE_JOBS] Job {job_key} registered for {mandate_path[-30:]}: "
                f"status={result['status']} should_start={result['should_start']}"
            )
            return result

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error registering job {job_key}: {e}")
            # Fail-safe: assume job can start (don't block dispatch)
            return {
                "should_start": True,
                "job_key": job_key,
                "status": "running",
                "position_in_queue": 0,
            }

    @staticmethod
    @firestore.transactional
    def _register_job_transaction(
        transaction,
        db,
        mandate_path: str,
        encoded_mandate: str,
        job_key: str,
        job_data: dict,
        job_type: str,
    ) -> Dict[str, Any]:
        """Atomic transaction: check running jobs, register as running or pending."""

        jobs_collection = db.collection(f"active_jobs/{job_type}/jobs")

        # 1. Check for running jobs on this mandate
        running_query = (
            jobs_collection
            .where(filter=FieldFilter("mandate_path", "==", mandate_path))
            .where(filter=FieldFilter("status", "==", "running"))
        )
        running_jobs = list(running_query.stream(transaction=transaction))

        # 2. Count pending jobs for queue position
        pending_query = (
            jobs_collection
            .where(filter=FieldFilter("mandate_path", "==", mandate_path))
            .where(filter=FieldFilter("status", "==", "pending"))
        )
        pending_jobs = list(pending_query.stream(transaction=transaction))
        pending_count = len(pending_jobs)

        # 3. Determine status
        if running_jobs:
            status = "pending"
            should_start = False
            position = pending_count + 1
        else:
            status = "running"
            should_start = True
            position = 0

        now = datetime.now(timezone.utc).isoformat()

        # 4. Build unified document
        doc_ref = db.document(f"active_jobs/{job_type}/jobs/{encoded_mandate}_{job_key}")
        job_document = {
            "job_key": job_key,
            "job_id": job_key,       # alias for router/AP compat
            "batch_id": job_key,     # alias for banker compat
            "mandate_path": mandate_path,
            "mandates_path": mandate_path,   # compat with router/AP plural field
            "job_type": job_type,
            "status": status,
            "payload": job_data,
            "created_at": now,
            "started_at": now if should_start else None,
            "stop_requested": False,
            "stop_requested_transactions": [],
            "last_updated": now,
            "position_in_queue": position,
        }

        transaction.set(doc_ref, job_document)

        return {
            "should_start": should_start,
            "job_key": job_key,
            "status": status,
            "position_in_queue": position,
        }

    @staticmethod
    def register_batch(
        mandate_path: str,
        jobs_data: list,
        job_type: str,
        batch_id: str,
    ) -> Dict[str, Any]:
        """
        Register a batch of jobs. For router/AP, each file is a separate job.
        For banker, the whole batch is a single job.

        Args:
            mandate_path: Firebase mandate path
            jobs_data: List of job items
            job_type: "router" | "apbookeeper" | "banker"
            batch_id: Batch identifier

        Returns:
            {should_start, job_key, status, position_in_queue, registered_count}
        """
        if job_type == "banker":
            # Banker: single job for the whole batch
            return ActiveJobManager.register_job(
                mandate_path=mandate_path,
                job_data={"jobs_data": jobs_data, "batch_id": batch_id},
                job_key=batch_id,
                job_type="banker",
            )

        # Router/AP: register each file as a separate job in the queue
        # but as a single batch entry (the worker receives the whole batch)
        full_payload = {"jobs_data": jobs_data, "batch_id": batch_id}
        return ActiveJobManager.register_job(
            mandate_path=mandate_path,
            job_data=full_payload,
            job_key=batch_id,
            job_type=job_type,
        )

    # ─────────────────────────────────────────────
    # STOP (backend-side, replaces HTTP to worker)
    # ─────────────────────────────────────────────

    @staticmethod
    def request_stop(
        mandate_path: str,
        job_type: str,
        job_key: str,
        transaction_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Request stop for a job by writing stop_requested=True in active_jobs.

        For banker jobs, can optionally stop specific transactions only.

        Args:
            mandate_path: Firebase mandate path
            job_type: "router" | "apbookeeper" | "banker"
            job_key: Job identifier (job_id or batch_id)
            transaction_ids: Optional list of specific transaction IDs to stop (banker only)

        Returns:
            {success, job_key, message}
        """
        db = get_firestore()
        encoded = ActiveJobManager._encode_mandate_path(mandate_path)

        try:
            doc_ref = db.document(f"active_jobs/{job_type}/jobs/{encoded}_{job_key}")
            doc = doc_ref.get()

            if not doc.exists:
                logger.warning(
                    f"[ACTIVE_JOBS] Job {job_key} not found in active_jobs/{job_type}/jobs "
                    f"for stop request"
                )
                return {
                    "success": False,
                    "job_key": job_key,
                    "message": f"Job {job_key} not found in active_jobs",
                }

            now = datetime.now(timezone.utc).isoformat()

            if transaction_ids:
                # Banker: stop specific transactions only
                doc_ref.update({
                    "stop_requested_transactions": firestore.ArrayUnion(transaction_ids),
                    "stop_requested_at": now,
                    "last_updated": now,
                })
                logger.info(
                    f"[ACTIVE_JOBS] Stop requested for {len(transaction_ids)} transactions "
                    f"in batch {job_key}"
                )
            else:
                # Full stop for the job/batch
                doc_ref.update({
                    "stop_requested": True,
                    "stop_requested_at": now,
                    "last_updated": now,
                })
                logger.info(f"[ACTIVE_JOBS] Full stop requested for job {job_key}")

            return {
                "success": True,
                "job_key": job_key,
                "message": f"Stop requested for {job_key}",
            }

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error requesting stop for {job_key}: {e}")
            return {
                "success": False,
                "job_key": job_key,
                "message": str(e),
            }

    # ─────────────────────────────────────────────
    # QUERY
    # ─────────────────────────────────────────────

    @staticmethod
    def get_queue_status(mandate_path: str, job_type: str) -> Dict[str, Any]:
        """
        Get queue status for a mandate: running and pending job counts.

        Args:
            mandate_path: Firebase mandate path
            job_type: "router" | "apbookeeper" | "banker"

        Returns:
            {mandate_path, running_count, pending_count, total}
        """
        db = get_firestore()

        try:
            jobs_collection = db.collection(f"active_jobs/{job_type}/jobs")

            running_query = (
                jobs_collection
                .where(filter=FieldFilter("mandate_path", "==", mandate_path))
                .where(filter=FieldFilter("status", "==", "running"))
            )
            running_docs = list(running_query.stream())

            pending_query = (
                jobs_collection
                .where(filter=FieldFilter("mandate_path", "==", mandate_path))
                .where(filter=FieldFilter("status", "==", "pending"))
            )
            pending_docs = list(pending_query.stream())

            return {
                "mandate_path": mandate_path,
                "running_count": len(running_docs),
                "pending_count": len(pending_docs),
                "total": len(running_docs) + len(pending_docs),
            }

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error querying queue status: {e}")
            return {
                "mandate_path": mandate_path,
                "error": str(e),
            }
