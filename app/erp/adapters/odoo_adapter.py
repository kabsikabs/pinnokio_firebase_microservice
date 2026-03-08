"""
OdooAdapter — Implementation Odoo de l'interface ERPProvider.

Utilise XMLRPC via la connexion ODOO_KLK_VISION existante.
Premier methode implementee: post_journal_entry (account.move create + action_post).

Progressivement, les 37 methodes des workers migreront ici.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from ..erp_provider import ERPProvider

logger = logging.getLogger("erp.adapters.odoo")


class OdooAdapter(ERPProvider):
    """Adapter ERP pour Odoo via XMLRPC."""

    def __init__(self, connection):
        """
        Args:
            connection: Instance ODOO_KLK_VISION (depuis ERPConnectionManager)
        """
        self._conn = connection

    @property
    def erp_type(self) -> str:
        return "odoo"

    async def post_journal_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Poste une ecriture comptable vers Odoo.

        Flux:
        1. Construit le payload account.move (header + line_ids)
        2. Appelle account.move.create() via XMLRPC
        3. Appelle account.move.action_post() pour valider le brouillon

        Args:
            entry: Ecriture KLK enrichie avec _erp_journal_id et lines[]._erp_account_id

        Returns:
            {success: bool, erp_entry_id: str/int, erp_entry_name: str, message: str}
        """
        try:
            payload = self._build_account_move_payload(entry)

            logger.info(
                "[ODOO] Creating account.move: journal_id=%s, date=%s, %d lines",
                payload.get("journal_id"),
                payload.get("date"),
                len(payload.get("line_ids", [])),
            )

            # XMLRPC calls are synchronous — run in thread
            move_id = await asyncio.to_thread(
                self._conn.execute_kw,
                "account.move",
                "create",
                [payload],
            )

            if not move_id:
                return {
                    "success": False,
                    "error": "account.move.create() returned empty result",
                }

            logger.info("[ODOO] account.move created: id=%s", move_id)

            # Validate (post) the draft entry
            try:
                await asyncio.to_thread(
                    self._conn.execute_kw,
                    "account.move",
                    "action_post",
                    [[move_id]],
                )
            except Exception as post_err:
                # action_post failed — cancel and delete the draft to avoid orphans
                logger.warning("[ODOO] action_post failed for %s: %s — cancelling draft", move_id, post_err)
                try:
                    await asyncio.to_thread(
                        self._conn.execute_kw,
                        "account.move",
                        "button_cancel",
                        [[move_id]],
                    )
                    await asyncio.to_thread(
                        self._conn.execute_kw,
                        "account.move",
                        "unlink",
                        [[move_id]],
                    )
                    logger.info("[ODOO] Draft entry %s cancelled and deleted", move_id)
                except Exception as cleanup_err:
                    logger.warning("[ODOO] Draft cleanup failed for %s: %s", move_id, cleanup_err)
                return {
                    "success": False,
                    "error": str(post_err),
                }

            logger.info("[ODOO] account.move posted: id=%s", move_id)

            # Fetch the entry name for confirmation
            entry_name = await self._get_move_name(move_id)

            return {
                "success": True,
                "erp_entry_id": move_id,
                "erp_entry_name": entry_name or str(move_id),
                "message": f"Ecriture postee dans Odoo: {entry_name or move_id}",
            }

        except Exception as e:
            logger.error("[ODOO] post_journal_entry error: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"Erreur Odoo: {e}",
            }

    def _build_account_move_payload(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transforme une ecriture KLK en payload Odoo account.move.

        Format KLK (in):
            journal_code, entry_date, description, document_ref, currency,
            _erp_journal_id, lines[{account_number, description, debit, credit, _erp_account_id}]

        Format Odoo (out):
            journal_id, date, ref, line_ids[(0, 0, {account_id, name, debit, credit})]
        """
        line_ids = []
        for line in entry.get("lines", []):
            erp_account_id = line.get("_erp_account_id")
            if not erp_account_id:
                raise ValueError(
                    f"Missing _erp_account_id for account {line.get('account_number')}"
                )

            line_ids.append((0, 0, {
                "account_id": int(erp_account_id),
                "name": line.get("description", "") or entry.get("description", ""),
                "debit": float(line.get("debit", 0)),
                "credit": float(line.get("credit", 0)),
            }))

        erp_journal_id = entry.get("_erp_journal_id")
        if not erp_journal_id:
            raise ValueError("Missing _erp_journal_id")

        payload = {
            "journal_id": int(erp_journal_id),
            "date": entry.get("entry_date"),
            "ref": entry.get("document_ref") or entry.get("description", ""),
            "line_ids": line_ids,
        }

        # Ajouter le move_type pour Odoo (entry = ecriture diverse)
        payload["move_type"] = "entry"

        return payload

    async def delete_draft_entry(self, move_id: int) -> bool:
        """Cancel and delete a draft account.move (cleanup on failed post)."""
        try:
            await asyncio.to_thread(
                self._conn.execute_kw, "account.move", "button_cancel", [[int(move_id)]]
            )
            await asyncio.to_thread(
                self._conn.execute_kw, "account.move", "unlink", [[int(move_id)]]
            )
            return True
        except Exception as e:
            logger.warning("[ODOO] delete_draft_entry(%s) failed: %s", move_id, e)
            return False

    async def _get_move_name(self, move_id: int) -> str:
        """Recupere le nom/numero de l'ecriture Odoo (ex: OD/2026/03/0001)."""
        try:
            result = await asyncio.to_thread(
                self._conn.execute_kw,
                "account.move",
                "read",
                [[move_id]],
                {"fields": ["name"]},
            )
            if result and isinstance(result, list) and result[0]:
                return result[0].get("name", str(move_id))
        except Exception as e:
            logger.warning("[ODOO] Failed to read move name: %s", e)
        return str(move_id)
