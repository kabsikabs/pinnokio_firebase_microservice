"""
Migration Script: active_jobs/{dept}/jobs/ → pending/ + on_process/
===================================================================

One-time migration from the legacy single-subcollection structure
(active_jobs/{dept}/jobs/ with status field) to the new dual-subcollection
structure (active_jobs/{dept}/pending/ and active_jobs/{dept}/on_process/).

Usage:
    python scripts_utils/migrate_active_jobs.py [--dry-run]

Idempotent: safe to re-run. Checks if doc already exists in target before creating.
"""

import argparse
import logging
import sys
import os

# Add parent directory to path for app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_active_jobs")

JOB_TYPES = ["router", "apbookeeper", "banker", "onboarding"]


def _extract_job_ids_from_payload(job_data: dict, job_type: str) -> dict:
    """Build jobs_status dict from legacy payload."""
    jobs_status = {}

    if job_type == "onboarding":
        jid = job_data.get("job_id")
        if jid:
            jobs_status[str(jid)] = "in_queue"
        return jobs_status

    # Payload may be the full doc (legacy) or nested under "payload"
    payload = job_data.get("payload", job_data)
    jobs_data = payload.get("jobs_data", [])

    if job_type in ("router", "apbookeeper"):
        for item in jobs_data:
            jid = item.get("job_id") or item.get("drive_file_id")
            if jid:
                jobs_status[str(jid)] = "in_queue"

    elif job_type == "banker":
        for item in jobs_data:
            transactions = item.get("transactions", [])
            for tx in transactions:
                tid = tx.get("transaction_id") or tx.get("id")
                if tid:
                    jobs_status[str(tid)] = "in_queue"
            if not transactions:
                jid = item.get("job_id") or item.get("id")
                if jid:
                    jobs_status[str(jid)] = "in_queue"

    return jobs_status


def migrate(dry_run: bool = False):
    """Run the migration."""
    from app.firebase_client import get_firestore

    db = get_firestore()
    total_migrated = 0
    total_skipped = 0
    total_deleted = 0

    for job_type in JOB_TYPES:
        legacy_col = db.collection(f"active_jobs/{job_type}/jobs")
        docs = list(legacy_col.stream())

        if not docs:
            logger.info(f"[{job_type}] No legacy docs found in jobs/")
            continue

        logger.info(f"[{job_type}] Found {len(docs)} legacy docs in jobs/")

        for doc in docs:
            data = doc.to_dict()
            status = data.get("status", "unknown")
            doc_id = doc.id

            # Determine target subcollection
            if status == "running":
                target = "on_process"
            elif status == "pending":
                target = "pending"
            else:
                logger.warning(
                    f"[{job_type}] Skipping doc {doc_id} with unknown status: {status}"
                )
                total_skipped += 1
                continue

            # Build jobs_status if not present
            if "jobs_status" not in data:
                jobs_status = _extract_job_ids_from_payload(data, job_type)
                # For running jobs, mark them as on_process
                if target == "on_process":
                    jobs_status = {k: "on_process" for k in jobs_status}
                data["jobs_status"] = jobs_status

            # Remove legacy "status" and "position_in_queue" fields
            data.pop("status", None)
            data.pop("position_in_queue", None)

            # Check if target doc already exists
            target_ref = db.document(f"active_jobs/{job_type}/{target}/{doc_id}")
            if target_ref.get().exists:
                logger.info(f"[{job_type}] Doc {doc_id} already exists in {target}/, skipping")
                total_skipped += 1
                continue

            if dry_run:
                logger.info(
                    f"[{job_type}] [DRY RUN] Would migrate {doc_id} -> {target}/ "
                    f"(jobs_status: {len(data.get('jobs_status', {}))} jobs)"
                )
                total_migrated += 1
            else:
                # Create in target
                target_ref.set(data)
                # Delete from legacy
                doc.reference.delete()
                logger.info(
                    f"[{job_type}] Migrated {doc_id} -> {target}/ "
                    f"(jobs_status: {len(data.get('jobs_status', {}))} jobs)"
                )
                total_migrated += 1
                total_deleted += 1

    logger.info("=" * 60)
    logger.info(f"Migration complete: migrated={total_migrated} skipped={total_skipped} deleted={total_deleted}")
    if dry_run:
        logger.info("(DRY RUN - no actual changes made)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate active_jobs to new structure")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    migrate(dry_run=args.dry_run)
