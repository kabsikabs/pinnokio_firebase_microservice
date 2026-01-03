"""
Task Manager Tools - Lecture index & audit events (Solution A)

Ces outils permettent à l'agent `general_chat` (et tous les modes qui utilisent `_build_general_tools`)
de consulter l'index des travaux et la timeline d'audit stockés dans Firestore selon le contrat :

- Index job : `clients/{userId}/task_manager/{job_id}`
- Events    : `clients/{userId}/task_manager/{job_id}/events/{event_id}`

⚠️ Sécurité / Contrat :
- `mandate_path` est FIXÉ côté outil : il est lu depuis `brain.user_context["mandate_path"]`
  et appliqué comme filtre Firestore obligatoire.
- Le `userId` est FIXÉ côté outil : il est lu depuis `brain.firebase_user_id`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from firebase_admin import firestore

logger = logging.getLogger("pinnokio.task_manager_tools")


def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("Date/heure attendue au format string ISO 8601")
    s = value.strip()
    if not s:
        return None
    # Supporte "Z"
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _serialize(value: Any) -> Any:
    """Convertit les valeurs Firestore en JSON-friendly (datetime -> ISO, etc.)."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


class TaskManagerTools:
    """
    Outils SPT de consultation de l'index de travaux (task_manager) et des events (audit).

    Contexte imposé :
    - user_id = brain.firebase_user_id
    - mandate_path = brain.user_context["mandate_path"]
    """

    def __init__(self, firebase_management, brain):
        self.firebase = firebase_management
        self.brain = brain

    # ─────────────────────────────────────────────────────────────────────────────
    # Tool definitions (courtes)
    # ─────────────────────────────────────────────────────────────────────────────
    def get_task_manager_index_definition(self) -> Dict[str, Any]:
        return {
            "name": "GET_TASK_MANAGER_INDEX",
            "description": (
                "📌 Index des travaux (task_manager) filtrable (département, statut, période). "
                "Sécurité: filtrage mandate_path imposé. GET_TOOL_HELP pour détails."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "department": {"type": "string", "description": "Filtre exact sur le champ `department` (optionnel)"},
                    "status_final": {
                        "type": "string",
                        "description": "Filtre exact sur `status_final` (ex: booked_and_archived|archived|pending|error|completed) (optionnel)",
                    },
                    "status": {"type": "string", "description": "Filtre exact sur `status` runtime (optionnel)"},
                    "last_outcome": {
                        "type": "string",
                        "description": "Filtre exact sur `last_outcome` (info|success|failure|pending) (optionnel)",
                    },
                    "file_name_contains": {
                        "type": "string",
                        "description": "Filtre côté backend (contains, case-insensitive) sur `file_name` (optionnel)",
                    },
                    "started_from": {"type": "string", "description": "ISO 8601. Filtre `started_at >=` (optionnel)"},
                    "started_to": {"type": "string", "description": "ISO 8601. Filtre `started_at <=` (optionnel)"},
                    "limit": {"type": "integer", "description": "Nombre max (défaut: 50, max: 200)", "default": 50},
                    "start_after_job_id": {
                        "type": "string",
                        "description": "Pagination: reprendre après ce job_id (optionnel)",
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": "Si true, inclut aussi `raw` (doc complet) dans chaque item (défaut: false)",
                        "default": False,
                    },
                },
                "required": [],
            },
        }

    def get_task_manager_details_definition(self) -> Dict[str, Any]:
        return {
            "name": "GET_TASK_MANAGER_DETAILS",
            "description": (
                "🧾 Détails d’un travail (index + timeline events) via job_id. "
                "Sécurité: mandate_path imposé. GET_TOOL_HELP pour détails."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "ID du job à ouvrir (requis)"},
                    "events_limit": {"type": "integer", "description": "Nombre max d'events (défaut: 100, max: 500)", "default": 100},
                    "events_order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Ordre de la timeline (défaut: asc)",
                        "default": "asc",
                    },
                },
                "required": ["job_id"],
            },
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────────
    def _get_fixed_context(self) -> Dict[str, str]:
        uid = getattr(self.brain, "firebase_user_id", None)
        user_context = getattr(self.brain, "user_context", None) or {}
        mandate_path = user_context.get("mandate_path")

        if not uid or not isinstance(uid, str):
            raise RuntimeError("Contexte utilisateur invalide: firebase_user_id manquant")
        if not mandate_path or not isinstance(mandate_path, str):
            raise RuntimeError("Contexte utilisateur invalide: mandate_path manquant (obligatoire)")

        return {"user_id": uid, "mandate_path": mandate_path}

    def _task_manager_collection_ref(self, user_id: str):
        # Contrat : clients/{userId}/task_manager/{job_id}
        return self.firebase.db.collection("clients").document(user_id).collection("task_manager")

    # ─────────────────────────────────────────────────────────────────────────────
    # Tool execution
    # ─────────────────────────────────────────────────────────────────────────────
    async def get_index(
        self,
        department: Optional[str] = None,
        status_final: Optional[str] = None,
        status: Optional[str] = None,
        last_outcome: Optional[str] = None,
        file_name_contains: Optional[str] = None,
        started_from: Optional[str] = None,
        started_to: Optional[str] = None,
        limit: int = 50,
        start_after_job_id: Optional[str] = None,
        include_raw: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            ctx = self._get_fixed_context()
            user_id = ctx["user_id"]
            mandate_path = ctx["mandate_path"]

            limit = int(limit or 50)
            limit = max(1, min(limit, 200))

            started_from_dt = _parse_iso_dt(started_from)
            started_to_dt = _parse_iso_dt(started_to)

            col = self._task_manager_collection_ref(user_id)

            # Filtre de sécurité (obligatoire)
            query = col.where(filter=firestore.FieldFilter("mandate_path", "==", mandate_path))

            # Log des filtres appliqués
            logger.info(
                "[GET_TASK_MANAGER_INDEX] Filtres appliqués - "
                f"mandate_path={mandate_path}, "
                f"department={department}, "
                f"status_final={status_final}, "
                f"status={status}, "
                f"last_outcome={last_outcome}, "
                f"started_from={started_from}, "
                f"started_to={started_to}, "
                f"file_name_contains={file_name_contains}, "
                f"limit={limit}"
            )

            # Filtres optionnels
            if department:
                query = query.where(filter=firestore.FieldFilter("department", "==", department))
            if status_final:
                query = query.where(filter=firestore.FieldFilter("status_final", "==", status_final))
            if status:
                query = query.where(filter=firestore.FieldFilter("status", "==", status))
            if last_outcome:
                query = query.where(filter=firestore.FieldFilter("last_outcome", "==", last_outcome))
            if started_from_dt:
                query = query.where(filter=firestore.FieldFilter("started_at", ">=", started_from_dt))
            if started_to_dt:
                query = query.where(filter=firestore.FieldFilter("started_at", "<=", started_to_dt))

            # Ordre (contrat recommande started_at timestamp)
            query = query.order_by("started_at", direction=firestore.Query.DESCENDING)

            # Pagination
            if start_after_job_id:
                snap = await asyncio.to_thread(lambda: col.document(str(start_after_job_id)).get())
                if snap and snap.exists:
                    data = snap.to_dict() or {}
                    if data.get("mandate_path") == mandate_path:
                        query = query.start_after(snap)
                    else:
                        return {
                            "success": False,
                            "error": "start_after_job_id ne correspond pas au mandate_path courant (refusé)",
                        }
                else:
                    return {
                        "success": False,
                        "error": "start_after_job_id introuvable",
                    }

            # Si on fait un filtre `contains` côté backend, on prefetch plus puis on tronque
            fetch_limit = limit
            if file_name_contains:
                fetch_limit = min(200, max(limit, 50) * 4)

            docs = await asyncio.to_thread(lambda: list(query.limit(fetch_limit).stream()))

            items: List[Dict[str, Any]] = []
            needle = (file_name_contains or "").strip().lower()
            
            # Collecter les départements uniques pour diagnostic
            departments_found = set()
            
            for d in docs:
                dd = d.to_dict() or {}
                dd_job_id = dd.get("job_id") or d.id
                
                # Collecter le département pour diagnostic
                dept = dd.get("department")
                if dept:
                    departments_found.add(str(dept))

                file_name = str(dd.get("file_name") or "")
                if needle and needle not in file_name.lower():
                    continue

                item = {
                    "job_id": dd_job_id,
                    "department": dd.get("department"),
                    "file_name": dd.get("file_name"),
                    "status": dd.get("status"),
                    "status_final": dd.get("status_final"),
                    "started_at": _serialize(dd.get("started_at")),
                    "ended_at": _serialize(dd.get("ended_at")),
                    "duration_ms": dd.get("duration_ms"),
                    "last_event_time": dd.get("last_event_time"),
                    "last_outcome": dd.get("last_outcome"),
                    "last_message": dd.get("last_message"),
                    "department_data": dd.get("department_data", {}),
                }
                if include_raw:
                    item["raw"] = _serialize(dd)

                items.append(item)
                if len(items) >= limit:
                    break

            next_cursor = items[-1]["job_id"] if len(items) == limit else None

            # Diagnostic : si aucun résultat et filtre department appliqué, suggérer les départements disponibles
            diagnostic_info = {}
            if len(items) == 0 and department:
                # Récupérer tous les départements disponibles pour ce mandate_path (sans filtre department)
                try:
                    base_query = col.where(filter=firestore.FieldFilter("mandate_path", "==", mandate_path))
                    all_docs_sample = await asyncio.to_thread(lambda: list(base_query.limit(100).stream()))
                    all_departments = set()
                    for doc in all_docs_sample:
                        data = doc.to_dict() or {}
                        dept = data.get("department")
                        if dept:
                            all_departments.add(str(dept))
                    if all_departments:
                        diagnostic_info["available_departments"] = sorted(list(all_departments))
                        diagnostic_info["suggestion"] = (
                            f"Aucun résultat avec department='{department}'. "
                            f"Départements disponibles: {', '.join(sorted(all_departments))}"
                        )
                        logger.warning(
                            f"[GET_TASK_MANAGER_INDEX] 🔍 DIAGNOSTIC - "
                            f"Recherche department='{department}' → 0 résultats. "
                            f"Départements trouvés dans la base: {', '.join(sorted(all_departments))}"
                        )
                    else:
                        logger.warning(
                            f"[GET_TASK_MANAGER_INDEX] 🔍 DIAGNOSTIC - "
                            f"Aucun document trouvé pour mandate_path='{mandate_path}' "
                            f"(même sans filtre department)"
                        )
                except Exception as e:
                    logger.warning(f"[GET_TASK_MANAGER_INDEX] Erreur lors du diagnostic: {e}", exc_info=True)

            logger.info(
                f"[GET_TASK_MANAGER_INDEX] Résultats - count={len(items)}, "
                f"departments_trouvés={sorted(list(departments_found)) if departments_found else 'aucun'}"
            )

            return {
                "success": True,
                "fixed_context": {"user_id": user_id, "mandate_path": mandate_path},
                "filters_applied": {
                    "mandate_path": mandate_path,  # ⭐ Ajouté pour visibilité
                    "department": department,
                    "status_final": status_final,
                    "status": status,
                    "last_outcome": last_outcome,
                    "file_name_contains": file_name_contains,
                    "started_from": started_from,
                    "started_to": started_to,
                },
                "count": len(items),
                "results": _serialize(items),
                "next_start_after_job_id": next_cursor,
                "diagnostic": diagnostic_info if diagnostic_info else None,
            }

        except Exception as e:
            logger.error("[GET_TASK_MANAGER_INDEX] error=%s", repr(e), exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_details(
        self,
        job_id: str,
        events_limit: int = 100,
        events_order: str = "asc",
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            ctx = self._get_fixed_context()
            user_id = ctx["user_id"]
            mandate_path = ctx["mandate_path"]

            if not job_id or not isinstance(job_id, str):
                return {"success": False, "error": "job_id requis"}

            events_limit = int(events_limit or 100)
            events_limit = max(1, min(events_limit, 500))
            order = "asc" if str(events_order).lower() != "desc" else "desc"

            col = self._task_manager_collection_ref(user_id)
            doc_ref = col.document(job_id)

            snap = await asyncio.to_thread(doc_ref.get)
            if not snap.exists:
                return {"success": False, "error": f"Aucun job trouvé pour job_id={job_id}"}

            index_data = snap.to_dict() or {}
            if index_data.get("mandate_path") != mandate_path:
                return {"success": False, "error": "Accès refusé: job_id hors mandate_path courant"}

            # Lire events
            events_ref = doc_ref.collection("events")
            direction = firestore.Query.ASCENDING if order == "asc" else firestore.Query.DESCENDING

            # Priorité: eventTimeServer (timestamp) ; fallback: eventTime (string)
            try:
                q = events_ref.order_by("eventTimeServer", direction=direction).limit(events_limit)
                events_snaps = await asyncio.to_thread(lambda: list(q.stream()))
            except Exception:
                q = events_ref.order_by("eventTime", direction=direction).limit(events_limit)
                events_snaps = await asyncio.to_thread(lambda: list(q.stream()))

            events: List[Dict[str, Any]] = []
            for es in events_snaps:
                ed = es.to_dict() or {}
                ed["event_id"] = es.id
                events.append(_serialize(ed))

            return {
                "success": True,
                "fixed_context": {"user_id": user_id, "mandate_path": mandate_path},
                "job": _serialize({**index_data, "job_id": index_data.get("job_id") or snap.id}),
                "events": events,
                "events_count": len(events),
            }

        except Exception as e:
            logger.error("[GET_TASK_MANAGER_DETAILS] error=%s", repr(e), exc_info=True)
            return {"success": False, "error": str(e)}


