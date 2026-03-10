"""
JournalEntryPoster — Point d'entree UNIQUE pour poster des ecritures vers l'ERP.

TOUT module qui doit poster une ecriture comptable passe par ici:
  - Journal entries manuels (Worker LLM approval card)
  - Depreciation entries (cron automatique ou bouton manuel)
  - Disposal entries (cession d'immobilisation)
  - Future: HR entries, invoice entries, etc.

Pipeline:
  1. resolve_erp_ids()  — journal_code → erp_journal_id, account_number → erp_account_id
  2. get_erp_provider()  — obtient l'adapter ERP (Odoo, Sage, etc.)
  3. provider.post_journal_entry() — poste via XMLRPC / API
  4. trigger_gl_sync()   — (optionnel) sync incrementale GL → Neon
  5. return result

Usage:
    from app.erp.journal_entry_poster import get_journal_entry_poster

    poster = get_journal_entry_poster()
    result = await poster.post(
        entry={"journal_code": "OD", "entry_date": "2026-03-10", "lines": [...]},
        mandate_path="clients/uid/bo_clients/cid/mandates/mid",
    )
    # result = {success: True, erp_entry_id: 123, erp_entry_name: "OD/2026/03/0001"}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("erp.journal_entry_poster")


class JournalEntryPoster:
    """Point d'entree centralise pour poster des ecritures vers l'ERP."""

    async def post(
        self,
        entry: Dict[str, Any],
        mandate_path: str,
        uid: Optional[str] = None,
        collection_id: Optional[str] = None,
        trigger_gl_sync: bool = True,
        source: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Poste une ecriture comptable vers l'ERP.

        Args:
            entry: Ecriture au format KLK standard:
                {journal_code, entry_date, description, document_ref, currency,
                 lines: [{account_number, description, debit, credit}, ...]}
                OU deja enrichie avec _erp_journal_id / _erp_account_id
            mandate_path: Chemin mandat Firebase (pour resolution company + ERP connexion)
            uid: Firebase user ID (auto-extrait du mandate_path si absent)
            collection_id: Company/collection ID (auto-extrait si absent)
            trigger_gl_sync: Declencher la sync GL incrementale apres posting (defaut: True)
            source: Identifiant du module appelant (pour logging)

        Returns:
            {success: bool, erp_entry_id: int, erp_entry_name: str, message: str}
        """
        # 0. Resolve uid / collection_id from mandate_path if not provided
        effective_uid = uid or _extract_uid(mandate_path)
        effective_cid = collection_id or _extract_collection_id(mandate_path) or effective_uid

        if not effective_uid:
            return _fail(f"Cannot extract uid from mandate_path: {mandate_path}")

        # 1. Resolve ERP IDs (skip if already resolved — e.g. approval cards)
        if not entry.get("_erp_journal_id"):
            try:
                entry = await self._resolve_ids(entry, mandate_path)
            except Exception as e:
                logger.error("[POSTER:%s] ERP ID resolution failed: %s", source, e)
                return _fail(f"ERP ID resolution failed: {e}")

        # 2. Get ERP provider
        try:
            from .erp_provider import get_erp_provider

            provider = await get_erp_provider(effective_uid, effective_cid, mandate_path)
        except Exception as e:
            logger.error("[POSTER:%s] ERP connection failed: %s", source, e)
            return _fail(f"ERP connection failed: {e}")

        # 3. Post to ERP
        try:
            result = await provider.post_journal_entry(entry)
        except Exception as e:
            logger.error("[POSTER:%s] ERP post error: %s", source, e, exc_info=True)
            return _fail(f"ERP post error: {e}")

        if not result.get("success"):
            logger.warning("[POSTER:%s] ERP post failed: %s", source, result.get("error"))
            return result

        erp_entry_id = result.get("erp_entry_id", "")
        erp_entry_name = result.get("erp_entry_name", str(erp_entry_id))

        logger.info(
            "[POSTER:%s] Posted: %s → ERP %s (uid=%s)",
            source,
            entry.get("document_ref") or entry.get("description", ""),
            erp_entry_name,
            effective_uid,
        )

        # 4. Trigger GL sync (non-blocking)
        if trigger_gl_sync and effective_uid:
            await self._trigger_gl_sync(effective_uid, effective_cid, mandate_path, source)

        return result

    # ─── Internal helpers ──────────────────────────────────────────

    @staticmethod
    async def _resolve_ids(entry: Dict[str, Any], mandate_path: str) -> Dict[str, Any]:
        """Resolve KLK IDs → ERP IDs (journal_code, account_numbers)."""
        from .erp_provider import resolve_erp_ids

        return await resolve_erp_ids(entry, mandate_path)

    @staticmethod
    async def _trigger_gl_sync(
        uid: str, company_id: str, mandate_path: str, source: str
    ):
        """Trigger incremental GL sync via Redis PubSub (non-blocking)."""
        try:
            import json as _json
            from app.redis_client import get_redis

            redis = get_redis()
            channel = f"user:{uid}/task_manager"
            redis.publish(
                channel,
                _json.dumps({
                    "type": "gl_sync_requested",
                    "department": "coa",
                    "collection_id": company_id,
                    "mandate_path": mandate_path,
                    "data": {
                        "force_full": False,
                        "overlap_months": 1,
                        "requested_by": f"journal_entry_poster:{source}",
                    },
                }),
            )
            logger.info("[POSTER:%s] GL sync triggered for %s", source, mandate_path)
        except Exception as e:
            logger.warning("[POSTER:%s] GL sync trigger failed (non-blocking): %s", source, e)


# ─── Module-level singleton ────────────────────────────────────────

_poster: Optional[JournalEntryPoster] = None


def get_journal_entry_poster() -> JournalEntryPoster:
    """Return singleton JournalEntryPoster instance."""
    global _poster
    if _poster is None:
        _poster = JournalEntryPoster()
    return _poster


# ─── Utility functions ─────────────────────────────────────────────

def _extract_uid(mandate_path: str) -> Optional[str]:
    """Extract Firebase UID: clients/{uid}/bo_clients/...."""
    parts = (mandate_path or "").strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "clients":
        return parts[1]
    return None


def _extract_collection_id(mandate_path: str) -> Optional[str]:
    """Extract collection_id (bo_client UUID): clients/{uid}/bo_clients/{cid}/...."""
    parts = (mandate_path or "").strip("/").split("/")
    if len(parts) >= 4 and parts[2] == "bo_clients":
        return parts[3]
    return None


def _fail(error: str) -> Dict[str, Any]:
    return {"success": False, "error": error}
