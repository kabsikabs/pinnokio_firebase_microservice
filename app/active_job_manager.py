"""
Active Job Manager - Centralized active_jobs Queue Management
=============================================================

Manages the Firebase `active_jobs` collection as the single source of truth
for job queueing. Called by the backend BEFORE dispatching to workers.

Architecture:
    Backend receives request -> ActiveJobManager.register_job() -> active_jobs/{type}/{pending|on_process}
    Backend sends HTTP/ECS dispatch -> Worker reads active_jobs -> processes

Firebase Structure:
    active_jobs/{job_type}/pending/{encoded_mandate}_{batch_id}
    active_jobs/{job_type}/on_process/{encoded_mandate}_{batch_id}
        - job_key, job_id, batch_id (aliases)
        - mandate_path, mandates_path (compat)
        - job_type
        - payload (job_data wrapped)
        - jobs_status: { "file_id_1": "in_queue", ... }
        - stop_requested, stop_requested_transactions
        - created_at, started_at, last_updated

Terminal statuses: completed, error, stopped, skipped, pending, routed

Author: Migration Agent
Created: 2026-02-11
Updated: 2026-02-12 — Restructured with pending/on_process subcollections + jobs_status
"""

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .firebase_client import get_firestore

logger = logging.getLogger("active_job_manager")

# Terminal statuses — when a job reaches one of these, it's removed from jobs_status
TERMINAL_STATUSES = {"completed", "error", "stopped", "skipped", "pending", "routed"}


class ActiveJobManager:
    """
    Centralized management of the active_jobs Firebase queue.
    Called by the backend BEFORE dispatch to workers.
    """

    # ─────────────────────────────────────────────
    # UTILITIES (unchanged)
    # ─────────────────────────────────────────────

    @staticmethod
    def _encode_mandate_path(mandate_path: str) -> str:
        """
        Encode mandate_path for Firestore (single segment).

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
    # JOB ID EXTRACTION
    # ─────────────────────────────────────────────

    @staticmethod
    def _extract_job_ids_from_payload(job_data: dict, job_type: str) -> Dict[str, str]:
        """
        Build the initial jobs_status dict from the payload.

        Returns:
            Dict mapping job_id -> "in_queue" for each individual job in the payload.
        """
        jobs_status = {}

        if job_type == "onboarding":
            jid = job_data.get("job_id")
            if jid:
                jobs_status[str(jid)] = "in_queue"
            return jobs_status

        jobs_data = job_data.get("jobs_data", [])

        if job_type in ("router", "apbookeeper"):
            for item in jobs_data:
                jid = item.get("job_id") or item.get("drive_file_id")
                if jid:
                    jobs_status[str(jid)] = "in_queue"

        elif job_type == "banker":
            for item in jobs_data:
                # Each item may have a transactions list
                transactions = item.get("transactions", [])
                for tx in transactions:
                    tid = tx.get("transaction_id") or tx.get("id")
                    if tid:
                        jobs_status[str(tid)] = "in_queue"
                # If no transactions, use item-level id
                if not transactions:
                    jid = item.get("job_id") or item.get("id")
                    if jid:
                        jobs_status[str(jid)] = "in_queue"

        return jobs_status

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
        to determine if the job should start immediately (on_process) or queue (pending).

        Args:
            mandate_path: Firebase mandate path (e.g. "clients/uid/mandates/mid")
            job_data: Full job payload
            job_key: Unique job identifier (batch_id)
            job_type: "router" | "apbookeeper" | "banker" | "onboarding"

        Returns:
            {should_start, job_key, location, position_in_queue}
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
                f"location={result['location']} should_start={result['should_start']}"
            )
            return result

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error registering job {job_key}: {e}")
            return {
                "should_start": True,
                "job_key": job_key,
                "status": "on_process",
                "location": "on_process",
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
        """Atomic transaction: check running jobs, register in on_process or pending."""

        on_process_col = db.collection(f"active_jobs/{job_type}/on_process")
        pending_col = db.collection(f"active_jobs/{job_type}/pending")

        # 1. Check for running jobs on this mandate (in on_process/)
        running_query = (
            on_process_col
            .where(filter=FieldFilter("mandate_path", "==", mandate_path))
        )
        running_jobs = list(running_query.stream(transaction=transaction))

        # 2. Count pending jobs for queue position
        pending_query = (
            pending_col
            .where(filter=FieldFilter("mandate_path", "==", mandate_path))
        )
        pending_jobs = list(pending_query.stream(transaction=transaction))
        pending_count = len(pending_jobs)

        # 3. Determine location
        if running_jobs:
            location = "pending"
            should_start = False
            position = pending_count + 1
        else:
            location = "on_process"
            should_start = True
            position = 0

        now = datetime.now(timezone.utc).isoformat()

        # 4. Build jobs_status from payload
        jobs_status = ActiveJobManager._extract_job_ids_from_payload(job_data, job_type)

        # 5. Build document
        doc_id = f"{encoded_mandate}_{job_key}"
        doc_ref = db.document(f"active_jobs/{job_type}/{location}/{doc_id}")

        job_document = {
            "job_key": job_key,
            "job_id": job_key,
            "batch_id": job_key,
            "mandate_path": mandate_path,
            "mandates_path": mandate_path,
            "job_type": job_type,
            "payload": job_data,
            "jobs_status": jobs_status,
            "created_at": now,
            "started_at": now if should_start else None,
            "stop_requested": False,
            "stop_requested_transactions": [],
            "last_updated": now,
        }

        transaction.set(doc_ref, job_document)

        return {
            "should_start": should_start,
            "job_key": job_key,
            "status": "running" if should_start else "pending",
            "location": location,
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
        Register a batch of jobs. Delegates to register_job with wrapped payload.

        Args:
            mandate_path: Firebase mandate path
            jobs_data: List of job items
            job_type: "router" | "apbookeeper" | "banker" | "onboarding"
            batch_id: Batch identifier

        Returns:
            {should_start, job_key, location, position_in_queue}
        """
        if job_type == "banker":
            return ActiveJobManager.register_job(
                mandate_path=mandate_path,
                job_data={"jobs_data": jobs_data, "batch_id": batch_id},
                job_key=batch_id,
                job_type="banker",
            )

        full_payload = {"jobs_data": jobs_data, "batch_id": batch_id}
        return ActiveJobManager.register_job(
            mandate_path=mandate_path,
            job_data=full_payload,
            job_key=batch_id,
            job_type=job_type,
        )

    # ─────────────────────────────────────────────
    # STOP (differentiated: on_process vs pending)
    # ─────────────────────────────────────────────

    @staticmethod
    def request_stop(
        mandate_path: str,
        job_type: str,
        job_key: str,
        job_ids_to_stop: Optional[List[str]] = None,
        transaction_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Request stop for a job. Differentiates between on_process and pending:
        - on_process: set stop_requested or mark individual jobs as "stopping"
        - pending: remove jobs directly (synthetic stop) or delete the document

        Args:
            mandate_path: Firebase mandate path
            job_type: "router" | "apbookeeper" | "banker" | "onboarding"
            job_key: Job/batch identifier
            job_ids_to_stop: Optional list of specific job IDs to stop
            transaction_ids: Optional list of specific transaction IDs to stop (banker)

        Returns:
            {success, job_key, location, synthetic_stops}
        """
        db = get_firestore()
        encoded = ActiveJobManager._encode_mandate_path(mandate_path)
        doc_id = f"{encoded}_{job_key}"
        now = datetime.now(timezone.utc).isoformat()

        try:
            # 1. Try on_process first
            on_process_ref = db.document(f"active_jobs/{job_type}/on_process/{doc_id}")
            on_process_doc = on_process_ref.get()

            if on_process_doc.exists:
                return ActiveJobManager._stop_on_process(
                    on_process_ref, on_process_doc, job_key, job_ids_to_stop, transaction_ids, now
                )

            # 2. Try pending
            pending_ref = db.document(f"active_jobs/{job_type}/pending/{doc_id}")
            pending_doc = pending_ref.get()

            if pending_doc.exists:
                return ActiveJobManager._stop_pending(
                    pending_ref, pending_doc, job_key, job_ids_to_stop, now
                )

            # 3. job_key not found as batch_id — scan for it as an individual job_id
            result = ActiveJobManager._stop_by_scanning(
                db, job_type, mandate_path, job_key, job_ids_to_stop, transaction_ids, now
            )
            if result:
                return result

            logger.warning(
                f"[ACTIVE_JOBS] Job {job_key} not found in on_process or pending "
                f"for {job_type}"
            )
            return {
                "success": False,
                "job_key": job_key,
                "location": "not_found",
                "synthetic_stops": [],
                "message": f"Job {job_key} not found in active_jobs",
            }

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error requesting stop for {job_key}: {e}")
            return {
                "success": False,
                "job_key": job_key,
                "location": "error",
                "synthetic_stops": [],
                "message": str(e),
            }

    @staticmethod
    def _stop_on_process(doc_ref, doc_snapshot, job_key, job_ids_to_stop, transaction_ids, now):
        """Handle stop for a job currently in on_process."""
        data = doc_snapshot.to_dict()
        jobs_status = data.get("jobs_status", {})

        if job_ids_to_stop:
            # Mark specific jobs as "stopping"
            updates = {"last_updated": now}
            for jid in job_ids_to_stop:
                if jid in jobs_status:
                    updates[f"jobs_status.{jid}"] = "stopping"
            doc_ref.update(updates)
            logger.info(f"[ACTIVE_JOBS] Marked {len(job_ids_to_stop)} jobs as stopping in {job_key}")
        elif transaction_ids:
            # Banker: stop specific transactions
            doc_ref.update({
                "stop_requested_transactions": firestore.ArrayUnion(transaction_ids),
                "stop_requested_at": now,
                "last_updated": now,
            })
            logger.info(f"[ACTIVE_JOBS] Stop requested for {len(transaction_ids)} transactions in {job_key}")
        else:
            # Full batch stop
            doc_ref.update({
                "stop_requested": True,
                "stop_requested_at": now,
                "last_updated": now,
            })
            logger.info(f"[ACTIVE_JOBS] Full stop requested for job {job_key}")

        return {
            "success": True,
            "job_key": job_key,
            "location": "on_process",
            "synthetic_stops": [],
            "message": f"Stop requested for {job_key} (on_process)",
        }

    @staticmethod
    def _stop_pending(doc_ref, doc_snapshot, job_key, job_ids_to_stop, now):
        """Handle stop for a job in pending — direct removal (synthetic stop)."""
        data = doc_snapshot.to_dict()
        jobs_status = data.get("jobs_status", {})
        synthetic_stops = []

        if job_ids_to_stop:
            # Remove specific jobs from jobs_status
            for jid in job_ids_to_stop:
                if jid in jobs_status:
                    synthetic_stops.append(jid)
                    del jobs_status[jid]

            if not jobs_status:
                # All jobs removed — delete the document
                doc_ref.delete()
                logger.info(f"[ACTIVE_JOBS] Pending doc {job_key} deleted (all jobs stopped)")
            else:
                # Update with remaining jobs
                doc_ref.update({
                    "jobs_status": jobs_status,
                    "last_updated": now,
                })
                logger.info(
                    f"[ACTIVE_JOBS] Removed {len(synthetic_stops)} jobs from pending {job_key}, "
                    f"{len(jobs_status)} remaining"
                )
        else:
            # Full batch stop — collect all job IDs and delete
            synthetic_stops = list(jobs_status.keys())
            doc_ref.delete()
            logger.info(f"[ACTIVE_JOBS] Pending doc {job_key} deleted (full batch stop)")

        return {
            "success": True,
            "job_key": job_key,
            "location": "pending",
            "synthetic_stops": synthetic_stops,
            "message": f"Stopped {len(synthetic_stops)} jobs from pending",
        }

    @staticmethod
    def _stop_by_scanning(db, job_type, mandate_path, job_id, job_ids_to_stop, transaction_ids, now):
        """
        Scan on_process/ and pending/ to find a document containing job_id in its jobs_status.
        Used when the frontend sends an individual job_id rather than a batch_id.
        """
        for subcollection in ("on_process", "pending"):
            col = db.collection(f"active_jobs/{job_type}/{subcollection}")
            query = col.where(filter=FieldFilter("mandate_path", "==", mandate_path))
            docs = list(query.stream())

            for doc in docs:
                data = doc.to_dict()
                jobs_status = data.get("jobs_status", {})
                if job_id in jobs_status:
                    actual_job_key = data.get("job_key", doc.id)
                    ids_to_stop = job_ids_to_stop or [job_id]

                    if subcollection == "on_process":
                        return ActiveJobManager._stop_on_process(
                            doc.reference, doc, actual_job_key, ids_to_stop, transaction_ids, now
                        )
                    else:
                        return ActiveJobManager._stop_pending(
                            doc.reference, doc, actual_job_key, ids_to_stop, now
                        )

        return None

    # ─────────────────────────────────────────────
    # JOB STATUS UPDATE (called from notification cascade)
    # ─────────────────────────────────────────────

    @staticmethod
    def update_job_status_in_active(
        job_type: str,
        mandate_path: str,
        job_id: str,
        new_status: str,
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update a single job's status within the active_jobs document.
        Called from the notification cascade (redis_subscriber.py).

        If the status is terminal, removes the job from jobs_status.
        If jobs_status becomes empty, deletes the document and promotes next pending.

        Args:
            job_type: "router" | "apbookeeper" | "banker" | "onboarding"
            mandate_path: Firebase mandate path
            job_id: Individual job ID within the batch
            new_status: New status for this job
            batch_id: Optional batch_id for direct lookup

        Returns:
            {updated, all_done, promoted_next}
        """
        db = get_firestore()

        try:
            doc_ref, doc_data = ActiveJobManager._find_active_doc(
                db, job_type, mandate_path, job_id, batch_id
            )

            if not doc_ref or not doc_data:
                logger.debug(
                    f"[ACTIVE_JOBS] Job {job_id} not found in on_process for update "
                    f"(may already be cleaned up)"
                )
                return {"updated": False, "all_done": False, "promoted_next": False}

            jobs_status = doc_data.get("jobs_status", {})
            is_terminal = new_status in TERMINAL_STATUSES
            now = datetime.now(timezone.utc).isoformat()

            if is_terminal:
                # Remove from jobs_status (terminal = done)
                if job_id in jobs_status:
                    del jobs_status[job_id]
            else:
                # Update in-place
                jobs_status[job_id] = new_status

            if not jobs_status:
                # All jobs done — delete document and promote next pending
                doc_ref.delete()
                logger.info(
                    f"[ACTIVE_JOBS] All jobs done in {doc_data.get('job_key')}, "
                    f"document deleted from on_process"
                )
                promoted = ActiveJobManager._promote_next_pending(
                    db, job_type, mandate_path
                )
                return {
                    "updated": True,
                    "all_done": True,
                    "promoted_next": promoted is not None,
                }
            else:
                # Update jobs_status in the document
                doc_ref.update({
                    "jobs_status": jobs_status,
                    "last_updated": now,
                })
                return {"updated": True, "all_done": False, "promoted_next": False}

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error updating job status for {job_id}: {e}")
            return {"updated": False, "all_done": False, "promoted_next": False}

    @staticmethod
    def _find_active_doc(db, job_type, mandate_path, job_id, batch_id=None):
        """
        Find the on_process document containing job_id.

        Returns:
            (doc_ref, doc_data) or (None, None) if not found
        """
        encoded = ActiveJobManager._encode_mandate_path(mandate_path)

        # Fast path: direct lookup by batch_id
        if batch_id:
            doc_id = f"{encoded}_{batch_id}"
            doc_ref = db.document(f"active_jobs/{job_type}/on_process/{doc_id}")
            doc = doc_ref.get()
            if doc.exists:
                return doc_ref, doc.to_dict()

        # Slow path: scan on_process by mandate_path
        col = db.collection(f"active_jobs/{job_type}/on_process")
        query = col.where(filter=FieldFilter("mandate_path", "==", mandate_path))
        docs = list(query.stream())

        for doc in docs:
            data = doc.to_dict()
            if job_id in data.get("jobs_status", {}):
                return doc.reference, data

        return None, None

    # ─────────────────────────────────────────────
    # PROMOTE NEXT PENDING
    # ─────────────────────────────────────────────

    @staticmethod
    def _promote_next_pending(db, job_type: str, mandate_path: str) -> Optional[Dict]:
        """
        Promote the oldest pending job to on_process.
        Called when an on_process document is fully done.

        Returns:
            Dict with promoted job info, or None if no pending jobs
        """
        try:
            pending_col = db.collection(f"active_jobs/{job_type}/pending")
            query = (
                pending_col
                .where(filter=FieldFilter("mandate_path", "==", mandate_path))
                .order_by("created_at")
                .limit(1)
            )
            pending_docs = list(query.stream())

            if not pending_docs:
                logger.info(f"[ACTIVE_JOBS] No pending jobs to promote for {mandate_path[-30:]}")
                return None

            next_doc = pending_docs[0]
            next_data = next_doc.to_dict()
            next_key = next_data.get("job_key") or next_data.get("batch_id")
            encoded = ActiveJobManager._encode_mandate_path(mandate_path)
            now = datetime.now(timezone.utc).isoformat()

            # Create in on_process
            new_doc_id = f"{encoded}_{next_key}"
            new_ref = db.document(f"active_jobs/{job_type}/on_process/{new_doc_id}")

            promoted_data = dict(next_data)
            promoted_data["started_at"] = now
            promoted_data["last_updated"] = now

            new_ref.set(promoted_data)

            # Delete from pending
            next_doc.reference.delete()

            logger.info(
                f"[ACTIVE_JOBS] Promoted {next_key} from pending to on_process "
                f"for {mandate_path[-30:]}"
            )

            return {
                "job_key": next_key,
                "payload": next_data.get("payload", next_data),
                "mandate_path": mandate_path,
            }

        except Exception as e:
            logger.error(f"[ACTIVE_JOBS] Error promoting next pending: {e}")
            return None

    # ─────────────────────────────────────────────
    # QUERY
    # ─────────────────────────────────────────────

    @staticmethod
    def get_queue_status(mandate_path: str, job_type: str) -> Dict[str, Any]:
        """
        Get queue status for a mandate: running and pending job counts.

        Args:
            mandate_path: Firebase mandate path
            job_type: "router" | "apbookeeper" | "banker" | "onboarding"

        Returns:
            {mandate_path, running_count, pending_count, total}
        """
        db = get_firestore()

        try:
            on_process_col = db.collection(f"active_jobs/{job_type}/on_process")
            running_query = (
                on_process_col
                .where(filter=FieldFilter("mandate_path", "==", mandate_path))
            )
            running_docs = list(running_query.stream())

            pending_col = db.collection(f"active_jobs/{job_type}/pending")
            pending_query = (
                pending_col
                .where(filter=FieldFilter("mandate_path", "==", mandate_path))
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
