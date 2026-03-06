"""
Client pour soumettre des jobs au Worker HR (pinnokio_hr) via le dispatch centralisé.

ARCHITECTURE (Phase 3 — pattern identique router/apbookeeper/bankbookeeper):
    Frontend → WebSocket → orchestration.py → HRJobberClient
        → handle_job_process(job_type="hr")
        → ActiveJobManager.register_job() (Firebase active_jobs/hr/)
        → HTTP dispatch /hr-event-trigger (si worker up)
        → Worker poll active_jobs (si worker cold-starting)
        → Worker calcule → Redis task_manager → Backend → WebSocket → Frontend

    Plus de HTTP direct vers pinnokio_hr:8001.
    Plus de callback URL.
    Le retour arrive via Redis PubSub (task_manager notifications).
"""

import logging
import uuid
from typing import Optional, Dict, Any, List

logger = logging.getLogger("hr.jobber_client")


class HRJobberClient:
    """
    Client centralisé pour le Worker HR.

    Soumet les jobs via handle_job_process (dispatch centralisé)
    au lieu d'appels HTTP directs.

    Usage:
        client = get_hr_jobber_client()
        result = await client.submit_payroll_calculation(
            uid="firebase_uid",
            company_data={...},
            employees=[{"id": "...", ...}],
            period={"year": 2026, "month": 1},
        )
    """

    @staticmethod
    def _generate_batch_id(prefix: str = "hr") -> str:
        """Génère un batch_id unique."""
        return f"batch_{prefix}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _generate_job_id(prefix: str = "payroll") -> str:
        """Génère un job_id unique."""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    # ═══════════════════════════════════════════════════════════════
    # CALCUL DE PAIE — Individuel (liste d'employés)
    # ═══════════════════════════════════════════════════════════════

    async def submit_payroll_calculation(
        self,
        uid: str,
        company_data: Dict[str, Any],
        employees: List[Dict[str, Any]],
        period: Dict[str, int],
        variables: Optional[Dict[str, Any]] = None,
        force_recalculate: bool = False,
        traceability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Soumet un calcul de paie pour une liste d'employés.

        Construit les jobs_data et appelle handle_job_process(job_type="hr").

        Args:
            uid: Firebase UID
            company_data: Contexte société (mandate_path, company_id, client_uuid, etc.)
            employees: Liste de dicts avec au minimum {"id": "uuid"}
            period: {"year": 2026, "month": 1}
            variables: Variables additionnelles (heures sup, primes)
            force_recalculate: Recalculer même si déjà existant
            traceability: Infos traçabilité (thread_key, execution_id)

        Returns:
            Résultat de handle_job_process (success, batch_id, etc.)
        """
        from app.wrappers.job_actions_handler import handle_job_process

        year = period["year"]
        month = period["month"]

        jobs_data = [
            {
                "job_id": f"payroll_{year}_{month:02d}_{emp['id'][:8]}",
                "employee_id": str(emp["id"]),
                "action": "calculate",
                "period_year": year,
                "period_month": month,
                "variables": variables or {},
                "force_recalculate": force_recalculate,
            }
            for emp in employees
        ]

        payload = {
            "jobs_data": jobs_data,
            "document_ids": [j["job_id"] for j in jobs_data],
        }

        result = await handle_job_process(
            uid=uid,
            job_type="hr",
            payload=payload,
            company_data=company_data,
            source="ui",
            traceability=traceability,
        )

        logger.info(
            "submit_payroll_calculation batch_id=%s employees=%d period=%d-%02d success=%s",
            result.get("batch_id"), len(employees), year, month, result.get("success"),
        )
        return result

    # ═══════════════════════════════════════════════════════════════
    # CALCUL DE PAIE — Batch (tous employés ou par cluster)
    # ═══════════════════════════════════════════════════════════════

    async def submit_batch_payroll(
        self,
        uid: str,
        company_data: Dict[str, Any],
        period: Dict[str, int],
        employee_ids: Optional[List[str]] = None,
        cluster_code: Optional[str] = None,
        traceability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Soumet un batch de calculs de paie.

        Si employee_ids est None, le worker itère sur tous les employés actifs.
        Si employee_ids est fourni, on construit un job par employé.

        Args:
            uid: Firebase UID
            company_data: Contexte société
            period: {"year": 2026, "month": 1}
            employee_ids: Liste d'IDs (None = tous — le worker itère)
            cluster_code: Filtrer par cluster
            traceability: Infos traçabilité

        Returns:
            Résultat de handle_job_process
        """
        from app.wrappers.job_actions_handler import handle_job_process

        year = period["year"]
        month = period["month"]

        if employee_ids:
            # Mode explicite: un job par employé
            jobs_data = [
                {
                    "job_id": f"payroll_{year}_{month:02d}_{eid[:8]}",
                    "employee_id": eid,
                    "action": "calculate",
                    "period_year": year,
                    "period_month": month,
                }
                for eid in employee_ids
            ]
        else:
            # Mode batch: un seul job "batch_calculate" — le worker itère
            batch_job_id = f"batch_payroll_{year}_{month:02d}"
            jobs_data = [
                {
                    "job_id": batch_job_id,
                    "action": "batch_calculate",
                    "period_year": year,
                    "period_month": month,
                    "cluster_code": cluster_code,
                }
            ]

        payload = {
            "jobs_data": jobs_data,
            "document_ids": [j["job_id"] for j in jobs_data],
        }

        result = await handle_job_process(
            uid=uid,
            job_type="hr",
            payload=payload,
            company_data=company_data,
            source="ui",
            traceability=traceability,
        )

        logger.info(
            "submit_batch_payroll batch_id=%s period=%d-%02d employee_ids=%s success=%s",
            result.get("batch_id"), year, month,
            len(employee_ids) if employee_ids else "all",
            result.get("success"),
        )
        return result

    # ═══════════════════════════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════════════════════════

    async def submit_validate(
        self,
        uid: str,
        company_data: Dict[str, Any],
        payroll_result_ids: List[str],
        traceability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Soumet une validation de bulletins (DRAFT → VALIDATED).

        Args:
            uid: Firebase UID
            company_data: Contexte société
            payroll_result_ids: UUIDs des résultats à valider
            traceability: Infos traçabilité
        """
        from app.wrappers.job_actions_handler import handle_job_process

        jobs_data = [
            {
                "job_id": f"validate_{rid[:8]}",
                "action": "validate",
                "payroll_result_id": rid,
            }
            for rid in payroll_result_ids
        ]

        payload = {
            "jobs_data": jobs_data,
            "document_ids": [j["job_id"] for j in jobs_data],
        }

        return await handle_job_process(
            uid=uid,
            job_type="hr",
            payload=payload,
            company_data=company_data,
            source="ui",
            traceability=traceability,
        )

    # ═══════════════════════════════════════════════════════════════
    # EXPORT COMPTABLE
    # ═══════════════════════════════════════════════════════════════

    async def submit_export_accounting(
        self,
        uid: str,
        company_data: Dict[str, Any],
        period: Dict[str, int],
        traceability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Soumet une génération d'écritures comptables.

        Args:
            uid: Firebase UID
            company_data: Contexte société
            period: {"year": 2026, "month": 1}
            traceability: Infos traçabilité
        """
        from app.wrappers.job_actions_handler import handle_job_process

        year = period["year"]
        month = period["month"]
        job_id = f"export_accounting_{year}_{month:02d}"

        payload = {
            "jobs_data": [
                {
                    "job_id": job_id,
                    "action": "export_accounting",
                    "period_year": year,
                    "period_month": month,
                }
            ],
            "document_ids": [job_id],
        }

        return await handle_job_process(
            uid=uid,
            job_type="hr",
            payload=payload,
            company_data=company_data,
            source="ui",
            traceability=traceability,
        )

    # ═══════════════════════════════════════════════════════════════
    # GÉNÉRATION PDF
    # ═══════════════════════════════════════════════════════════════

    async def submit_pdf_generate(
        self,
        uid: str,
        company_data: Dict[str, Any],
        payroll_result_ids: List[str],
        traceability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Soumet une génération de fiches de paie PDF.

        Args:
            uid: Firebase UID
            company_data: Contexte société
            payroll_result_ids: UUIDs des résultats pour lesquels générer un PDF
            traceability: Infos traçabilité
        """
        from app.wrappers.job_actions_handler import handle_job_process

        jobs_data = [
            {
                "job_id": f"pdf_{rid[:8]}",
                "action": "generate_pdf",
                "payroll_result_id": rid,
            }
            for rid in payroll_result_ids
        ]

        payload = {
            "jobs_data": jobs_data,
            "document_ids": [j["job_id"] for j in jobs_data],
        }

        return await handle_job_process(
            uid=uid,
            job_type="hr",
            payload=payload,
            company_data=company_data,
            source="ui",
            traceability=traceability,
        )


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_hr_jobber_client: Optional[HRJobberClient] = None


def get_hr_jobber_client() -> HRJobberClient:
    """
    Retourne l'instance singleton du client Jobber HR.

    Usage:
        client = get_hr_jobber_client()
        result = await client.submit_payroll_calculation(...)
    """
    global _hr_jobber_client
    if _hr_jobber_client is None:
        _hr_jobber_client = HRJobberClient()
    return _hr_jobber_client
