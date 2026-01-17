"""
Client HTTP pour communiquer avec le Jobber HR (pinnokio_hr).

Ce module gère tous les appels HTTP vers le Jobber pour :
- Calcul de paie (unitaire et batch)
- Génération de PDF
- Export comptable
- Consultation des rubriques et clusters

ARCHITECTURE:
    Frontend → Backend RPC (HR.submit_*) → Ce client → Jobber HR
    Jobber HR → /hr/callback → WebSocket → Frontend

Configuration:
    - HR_JOBBER_URL: URL du Jobber (ex: http://localhost:8001)
    - HR_JOBBER_API_KEY: Clé API pour authentification
    - LISTENERS_URL: URL de callback pour le Jobber (ce service)
"""

import os
import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime

try:
    import httpx
except ImportError:
    httpx = None
    print("⚠️ httpx non installé. Exécuter: pip install httpx")

logger = logging.getLogger("hr.jobber_client")


class HRJobberClient:
    """
    Client HTTP pour le Jobber HR.
    
    Usage:
        client = HRJobberClient()
        result = await client.submit_payroll_calculate(
            user_id="xxx",
            company_id="uuid",
            employee_id="uuid",
            year=2026,
            month=1
        )
    """
    
    def __init__(self):
        """Initialise le client avec la configuration depuis l'environnement."""
        self.jobber_url = os.getenv("HR_JOBBER_URL", "http://localhost:8001")
        self.api_key = os.getenv("HR_JOBBER_API_KEY", "")
        self.callback_base_url = os.getenv("LISTENERS_URL", "http://localhost:8000")
        self.timeout = float(os.getenv("HR_JOBBER_TIMEOUT", "30"))
        
        logger.info(
            "HRJobberClient initialized: jobber_url=%s callback_url=%s",
            self.jobber_url, self.callback_base_url
        )
    
    def _get_headers(self) -> Dict[str, str]:
        """Retourne les headers d'authentification."""
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def _generate_job_id(self, prefix: str = "hr_job") -> str:
        """Génère un ID unique pour le job."""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
    
    def _get_callback_url(self) -> str:
        """Retourne l'URL de callback pour le Jobber."""
        return f"{self.callback_base_url}/hr/callback"
    
    # ═══════════════════════════════════════════════════════════════
    # CALCUL DE PAIE
    # ═══════════════════════════════════════════════════════════════
    
    async def submit_payroll_calculate(
        self,
        user_id: str,
        company_id: str,
        employee_id: str,
        year: int,
        month: int,
        variables: Optional[Dict[str, Any]] = None,
        force_recalculate: bool = False,
        session_id: Optional[str] = None,
        mandate_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Soumet un calcul de paie au Jobber.
        
        Le Jobber effectuera le calcul et appellera /hr/callback à la fin.
        
        Args:
            user_id: Firebase UID pour le callback
            company_id: UUID de la company PostgreSQL
            employee_id: UUID de l'employé
            year: Année de la période
            month: Mois de la période
            variables: Variables additionnelles (heures sup, primes, etc.)
            force_recalculate: Recalculer même si existe déjà
            session_id: Session pour routage WebSocket
            mandate_path: Chemin Firebase pour traçabilité
        
        Returns:
            {"job_id": "...", "status": "pending", "estimated_time_seconds": 30}
        """
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        job_id = self._generate_job_id("payroll")
        
        payload = {
            "company_id": company_id,
            "employee_id": employee_id,
            "year": year,
            "month": month,
            "variables": variables or {},
            "force_recalculate": force_recalculate,
            # Callback info
            "callback_url": self._get_callback_url(),
            "callback_data": {
                "job_id": job_id,
                "job_type": "payroll_calculate",
                "user_id": user_id,
                "session_id": session_id,
                "mandate_path": mandate_path,
                "employee_id": employee_id,
                "period_year": year,
                "period_month": month,
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.jobber_url}/api/payroll/calculate",
                    json=payload,
                    headers=self._get_headers()
                )
                
                if response.status_code == 202:
                    # Job accepté en async
                    logger.info(
                        "payroll_calculate_submitted job_id=%s employee=%s period=%d-%02d",
                        job_id, employee_id, year, month
                    )
                    return {
                        "job_id": job_id,
                        "status": "pending",
                        "estimated_time_seconds": 30,
                    }
                elif response.status_code == 200:
                    # Calcul synchrone terminé (fallback si Jobber ne supporte pas async)
                    result = response.json()
                    logger.info(
                        "payroll_calculate_sync job_id=%s employee=%s",
                        job_id, employee_id
                    )
                    return {
                        "job_id": job_id,
                        "status": "completed",
                        "result": result,
                    }
                else:
                    error_detail = response.text
                    logger.error(
                        "payroll_calculate_failed job_id=%s status=%d error=%s",
                        job_id, response.status_code, error_detail
                    )
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "error": f"HTTP {response.status_code}: {error_detail}",
                    }
        
        except httpx.TimeoutException:
            logger.error("payroll_calculate_timeout job_id=%s", job_id)
            return {
                "job_id": job_id,
                "status": "failed",
                "error": "Timeout lors de la soumission au Jobber",
            }
        except Exception as e:
            logger.error("payroll_calculate_error job_id=%s error=%s", job_id, repr(e))
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e),
            }
    
    async def submit_payroll_batch(
        self,
        user_id: str,
        company_id: str,
        year: int,
        month: int,
        employee_ids: Optional[List[str]] = None,
        cluster_code: Optional[str] = None,
        session_id: Optional[str] = None,
        mandate_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Soumet un batch de calculs de paie au Jobber.
        
        Le Jobber calculera toutes les paies et mettra à jour la progression
        via le callback.
        
        Args:
            user_id: Firebase UID pour le callback
            company_id: UUID de la company
            year: Année de la période
            month: Mois de la période
            employee_ids: Liste d'employés (None = tous)
            cluster_code: Filtrer par cluster
            session_id: Session pour routage WebSocket
            mandate_path: Chemin Firebase pour traçabilité
        
        Returns:
            {"job_id": "...", "status": "pending", "estimated_count": N}
        """
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        job_id = self._generate_job_id("batch")
        
        payload = {
            "company_id": company_id,
            "year": year,
            "month": month,
            "employee_ids": employee_ids,
            "cluster_code": cluster_code,
            # Callback info
            "callback_url": self._get_callback_url(),
            "callback_data": {
                "job_id": job_id,
                "job_type": "payroll_batch",
                "user_id": user_id,
                "session_id": session_id,
                "mandate_path": mandate_path,
                "company_id": company_id,
                "period_year": year,
                "period_month": month,
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.jobber_url}/api/payroll/batch",
                    json=payload,
                    headers=self._get_headers()
                )
                
                if response.status_code in (200, 202):
                    result = response.json()
                    logger.info(
                        "payroll_batch_submitted job_id=%s company=%s period=%d-%02d",
                        job_id, company_id, year, month
                    )
                    return {
                        "job_id": job_id,
                        "status": "pending",
                        "estimated_count": result.get("estimated_count", 0),
                        "estimated_time_seconds": result.get("estimated_duration_seconds", 300),
                    }
                else:
                    error_detail = response.text
                    logger.error(
                        "payroll_batch_failed job_id=%s status=%d error=%s",
                        job_id, response.status_code, error_detail
                    )
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "error": f"HTTP {response.status_code}: {error_detail}",
                    }
        
        except Exception as e:
            logger.error("payroll_batch_error job_id=%s error=%s", job_id, repr(e))
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e),
            }
    
    # ═══════════════════════════════════════════════════════════════
    # GÉNÉRATION PDF
    # ═══════════════════════════════════════════════════════════════
    
    async def submit_pdf_generate(
        self,
        user_id: str,
        payroll_id: str,
        session_id: Optional[str] = None,
        mandate_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Soumet une génération de PDF au Jobber.
        
        Args:
            user_id: Firebase UID pour le callback
            payroll_id: UUID du résultat de paie
            session_id: Session pour routage WebSocket
            mandate_path: Chemin Firebase pour traçabilité
        
        Returns:
            {"job_id": "...", "status": "pending"} ou {"pdf_url": "..."}
        """
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        job_id = self._generate_job_id("pdf")
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # D'abord essayer de récupérer le PDF directement (si déjà généré)
                response = await client.get(
                    f"{self.jobber_url}/api/payroll/pdf/{payroll_id}",
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    # PDF existe déjà
                    content_type = response.headers.get("content-type", "")
                    if "application/pdf" in content_type:
                        # Retourner l'URL directe
                        logger.info("pdf_exists payroll_id=%s", payroll_id)
                        return {
                            "job_id": job_id,
                            "status": "completed",
                            "pdf_url": f"{self.jobber_url}/api/payroll/pdf/{payroll_id}",
                        }
                
                # Sinon, soumettre une génération async
                payload = {
                    "payroll_id": payroll_id,
                    "callback_url": self._get_callback_url(),
                    "callback_data": {
                        "job_id": job_id,
                        "job_type": "pdf_generate",
                        "user_id": user_id,
                        "session_id": session_id,
                        "mandate_path": mandate_path,
                    }
                }
                
                response = await client.post(
                    f"{self.jobber_url}/api/payroll/pdf/generate",
                    json=payload,
                    headers=self._get_headers()
                )
                
                if response.status_code in (200, 202):
                    logger.info("pdf_generate_submitted job_id=%s payroll=%s", job_id, payroll_id)
                    return {
                        "job_id": job_id,
                        "status": "pending",
                    }
                else:
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "error": f"HTTP {response.status_code}: {response.text}",
                    }
        
        except Exception as e:
            logger.error("pdf_generate_error job_id=%s error=%s", job_id, repr(e))
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e),
            }
    
    # ═══════════════════════════════════════════════════════════════
    # STATUT DES JOBS
    # ═══════════════════════════════════════════════════════════════
    
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Récupère le statut d'un job auprès du Jobber.
        
        Args:
            job_id: ID du job
        
        Returns:
            {"job_id": "...", "status": "...", "progress": {...}}
        """
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.jobber_url}/api/jobs/{job_id}",
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return {"job_id": job_id, "status": "not_found"}
                else:
                    return {
                        "job_id": job_id,
                        "status": "error",
                        "error": f"HTTP {response.status_code}",
                    }
        
        except Exception as e:
            logger.error("get_job_status_error job_id=%s error=%s", job_id, repr(e))
            return {
                "job_id": job_id,
                "status": "error",
                "error": str(e),
            }
    
    # ═══════════════════════════════════════════════════════════════
    # DONNÉES DE RÉFÉRENCE
    # ═══════════════════════════════════════════════════════════════
    
    async def get_all_references(
        self,
        country_code: str = "CH",
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère toutes les données de référence en un seul appel.
        
        Args:
            country_code: Code pays (CH, FR, etc.)
            lang: Langue (fr, de, en, it)
        
        Returns:
            {
                "contract_types": [...],
                "remuneration_types": [...],
                "family_status": [...],
                "tax_status": [...],
                "permit_types": [...],
                "payroll_status": [...],
                "item_nature": [...],
                "charge_bearer": [...],
            }
        """
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.jobber_url}/references/all",
                    params={"country_code": country_code, "lang": lang},
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        "get_all_references_failed status=%d error=%s",
                        response.status_code, response.text
                    )
                    return {"error": f"HTTP {response.status_code}: {response.text}"}
        
        except Exception as e:
            logger.error("get_all_references_error error=%s", repr(e))
            return {"error": str(e)}
    
    async def get_contract_types(
        self,
        country_code: Optional[str] = None,
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """Récupère les types de contrat."""
        return await self._get_reference("contract-types", country_code, lang)
    
    async def get_remuneration_types(
        self,
        country_code: Optional[str] = None,
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """Récupère les types de rémunération."""
        return await self._get_reference("remuneration-types", country_code, lang)
    
    async def get_family_status(
        self,
        country_code: Optional[str] = None,
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """Récupère les statuts familiaux."""
        return await self._get_reference("family-status", country_code, lang)
    
    async def get_tax_status(
        self,
        country_code: str = "CH",
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """Récupère les statuts fiscaux (spécifiques au pays)."""
        return await self._get_reference("tax-status", country_code, lang)
    
    async def get_permit_types(
        self,
        country_code: str = "CH",
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """Récupère les types de permis (spécifiques au pays)."""
        return await self._get_reference("permit-types", country_code, lang)
    
    async def get_payroll_status(
        self,
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """Récupère les statuts de paie."""
        return await self._get_reference("payroll-status", None, lang)
    
    async def get_payroll_items(
        self,
        country_code: str = "CH",
        cluster_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Récupère les rubriques de paie."""
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        try:
            params = {"country_code": country_code}
            if cluster_code:
                params["cluster_code"] = cluster_code
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.jobber_url}/references/payroll-items",
                    params=params,
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return []
        
        except Exception as e:
            logger.error("get_payroll_items_error error=%s", repr(e))
            return []
    
    async def _get_reference(
        self,
        endpoint: str,
        country_code: Optional[str],
        lang: str,
    ) -> List[Dict[str, Any]]:
        """Helper pour récupérer une table de référence."""
        if httpx is None:
            raise ImportError("httpx n'est pas installé")
        
        try:
            params = {"lang": lang}
            if country_code:
                params["country_code"] = country_code
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.jobber_url}/references/{endpoint}",
                    params=params,
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(
                        "get_reference_%s_failed status=%d",
                        endpoint, response.status_code
                    )
                    return []
        
        except Exception as e:
            logger.error("get_reference_%s_error error=%s", endpoint, repr(e))
            return []
    
    # ═══════════════════════════════════════════════════════════════
    # HEALTH CHECK
    # ═══════════════════════════════════════════════════════════════
    
    async def check_health(self) -> Dict[str, Any]:
        """
        Vérifie la disponibilité du Jobber.
        
        Returns:
            {"status": "ok"|"error", "jobber_url": "...", ...}
        """
        if httpx is None:
            return {
                "status": "error",
                "error": "httpx not installed",
                "jobber_url": self.jobber_url,
            }
        
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.jobber_url}/health",
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "status": "ok",
                        "jobber_url": self.jobber_url,
                        "jobber_status": result.get("status"),
                        "jobber_version": result.get("version"),
                    }
                else:
                    return {
                        "status": "error",
                        "jobber_url": self.jobber_url,
                        "http_status": response.status_code,
                    }
        
        except Exception as e:
            return {
                "status": "error",
                "jobber_url": self.jobber_url,
                "error": str(e),
            }


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_hr_jobber_client: Optional[HRJobberClient] = None


def get_hr_jobber_client() -> HRJobberClient:
    """
    Retourne l'instance singleton du client Jobber HR.
    
    Usage:
        client = get_hr_jobber_client()
        result = await client.submit_payroll_calculate(...)
    """
    global _hr_jobber_client
    if _hr_jobber_client is None:
        _hr_jobber_client = HRJobberClient()
    return _hr_jobber_client
