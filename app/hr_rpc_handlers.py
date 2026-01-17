"""
Handlers RPC pour le module HR.

Ces handlers sont appelés par le serveur RPC (main.py) quand l'UI
fait des appels via rpc_call("HR.method", ...).

NAMESPACE: HR

Architecture:
    Frontend (Reflex) → rpc_call("HR.list_employees", ...) 
                     → POST /rpc
                     → _resolve_method("HR.list_employees")
                     → hr_rpc_handlers.list_employees()
                     → Redis Cache (HIT) | PostgreSQL Neon (MISS)

Cache Strategy:
    - Lecture: Cache-first avec fallback PostgreSQL
    - Écriture: Write-through avec invalidation cache
    - TTL: 1h (employees/contracts), 24h (references/clusters)

Endpoints disponibles:
    - HR.check_connection     → Vérifier la connexion Neon
    - HR.get_company_id       → mandate_path → company_id
    - HR.ensure_company       → Créer company si inexistante
    - HR.list_employees       → Liste employés (avec cache)
    - HR.get_employee         → Détail employé (avec cache)
    - HR.create_employee      → Créer employé (invalidation cache)
    - HR.update_employee      → Modifier employé (invalidation cache)
    - HR.delete_employee      → Supprimer employé (invalidation cache)
    - HR.list_contracts       → Liste contrats (avec cache)
    - HR.get_active_contract  → Contrat actif (avec cache)
    - HR.create_contract      → Créer contrat (invalidation cache)
    - HR.list_clusters        → Liste clusters (avec cache)
    - HR.get_payroll_result   → Résultat paie
    - HR.list_payroll_results → Historique paie
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID
from decimal import Decimal
from datetime import date, datetime

from .tools.neon_hr_manager import get_neon_hr_manager
from .tools.hr_cache_manager import get_hr_cache_manager
from .llm_service.redis_namespaces import RedisTTL

logger = logging.getLogger("hr.rpc_handlers")


def _serialize_value(value: Any) -> Any:
    """
    Sérialise les valeurs pour JSON (UUID, date, Decimal, etc.).
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def _serialize_employee(emp: Dict[str, Any]) -> Dict[str, Any]:
    """Sérialise un employé pour JSON."""
    return {
        **{k: _serialize_value(v) for k, v in emp.items()},
        "id": str(emp["id"]) if emp.get("id") else None,
    }


def _serialize_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    """Sérialise un contrat pour JSON."""
    return {
        **{k: _serialize_value(v) for k, v in contract.items()},
        "id": str(contract["id"]) if contract.get("id") else None,
        "employee_id": str(contract["employee_id"]) if contract.get("employee_id") else None,
    }


def _serialize_payroll(payroll: Dict[str, Any]) -> Dict[str, Any]:
    """Sérialise un résultat de paie pour JSON."""
    return {
        **{k: _serialize_value(v) for k, v in payroll.items()},
        "id": str(payroll["id"]) if payroll.get("id") else None,
        "employee_id": str(payroll["employee_id"]) if payroll.get("employee_id") else None,
        "contract_id": str(payroll["contract_id"]) if payroll.get("contract_id") else None,
    }


class HRRPCHandlers:
    """
    Handlers RPC pour le namespace HR.
    
    Chaque méthode correspond à un endpoint RPC:
    - HR.list_employees → list_employees()
    - HR.get_employee → get_employee()
    - etc.
    
    Toutes les méthodes sont asynchrones pour supporter asyncpg.
    """
    
    NAMESPACE = "HR"
    
    # ═══════════════════════════════════════════════════════════════
    # CONNECTION & HEALTH CHECK
    # ═══════════════════════════════════════════════════════════════
    
    async def check_connection(self) -> Dict[str, Any]:
        """
        Vérifie la connexion à PostgreSQL Neon.
        
        RPC: HR.check_connection
        Returns: { "status": "connected"|"error", "version": "...", ... }
        """
        try:
            manager = get_neon_hr_manager()
            result = await manager.check_connection()
            logger.info(f"HR.check_connection status={result.get('status')}")
            return result
        except Exception as e:
            logger.error(f"HR.check_connection error={e}")
            return {"status": "error", "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # MAPPING & CONTEXT
    # ═══════════════════════════════════════════════════════════════
    
    async def get_company_id(
        self, 
        mandate_path: str
    ) -> Dict[str, Any]:
        """
        Récupère le company_id depuis un mandate_path Firebase.
        
        RPC: HR.get_company_id
        Args: mandate_path (str)
        Returns: { "company_id": "uuid" | null }
        """
        try:
            manager = get_neon_hr_manager()
            company_id = await manager.get_company_id_from_mandate_path(mandate_path)
            logger.info(f"HR.get_company_id mandate_path={mandate_path} → {company_id}")
            return {
                "company_id": str(company_id) if company_id else None
            }
        except Exception as e:
            logger.error(f"HR.get_company_id error={e}")
            return {"company_id": None, "error": str(e)}
    
    async def ensure_company(
        self,
        account_firebase_uid: str,
        mandate_path: str,
        company_name: str,
        country: str,
        country_code: str = None,
        region: str = None,
        region_code: str = None,
    ) -> Dict[str, Any]:
        """
        S'assure qu'une entreprise existe dans PostgreSQL.
        Crée l'entreprise si elle n'existe pas.
        
        RPC: HR.ensure_company
        Returns: { "company_id": "uuid" }
        """
        try:
            manager = get_neon_hr_manager()
            company_id = await manager.get_or_create_company(
                account_firebase_uid=account_firebase_uid,
                mandate_path=mandate_path,
                company_name=company_name,
                country=country,
                country_code=country_code,
                region=region,
                region_code=region_code,
            )
            logger.info(f"HR.ensure_company mandate_path={mandate_path} → {company_id}")
            return {"company_id": str(company_id)}
        except Exception as e:
            logger.error(f"HR.ensure_company error={e}")
            return {"company_id": None, "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # EMPLOYEES
    # ═══════════════════════════════════════════════════════════════
    
    async def list_employees(
        self, 
        company_id: str,
        firebase_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Liste les employés d'une entreprise.
        
        RPC: HR.list_employees
        Args: 
            company_id (str UUID)
            firebase_user_id (str, optional): Firebase UID pour le cache
        Returns: { "employees": [...], "source": "cache"|"database" }
        """
        try:
            # 1. Tentative cache si user_id fourni
            if firebase_user_id:
                cache = get_hr_cache_manager()
                cached = await cache.get_cached_data(
                    firebase_user_id, 
                    company_id, 
                    "hr", 
                    "employees",
                    ttl_seconds=RedisTTL.HR_EMPLOYEES
                )
                if cached and cached.get("data"):
                    logger.info(
                        f"HR.list_employees company_id={company_id} "
                        f"count={len(cached['data'])} source=cache"
                    )
                    return {
                        "employees": cached["data"],
                        "source": "cache"
                    }
            
            # 2. Fallback PostgreSQL
            manager = get_neon_hr_manager()
            employees = await manager.list_employees(UUID(company_id))
            serialized = [_serialize_employee(emp) for emp in employees]
            
            logger.info(
                f"HR.list_employees company_id={company_id} "
                f"count={len(serialized)} source=database"
            )
            
            # 3. Sync vers Redis si user_id fourni
            if firebase_user_id and serialized:
                cache = get_hr_cache_manager()
                await cache.set_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    "employees",
                    serialized,
                    ttl_seconds=RedisTTL.HR_EMPLOYEES
                )
            
            return {
                "employees": serialized,
                "source": "database"
            }
        except Exception as e:
            logger.error(f"HR.list_employees error={e}")
            return {"employees": [], "error": str(e)}
    
    async def get_employee(
        self, 
        company_id: str, 
        employee_id: str,
        firebase_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Récupère un employé.
        
        RPC: HR.get_employee
        Args:
            company_id (str UUID)
            employee_id (str UUID)
            firebase_user_id (str, optional): Firebase UID pour le cache
        Returns: { "employee": {...}, "source": "cache"|"database" }
        """
        try:
            # 1. Tentative cache si user_id fourni
            if firebase_user_id:
                cache = get_hr_cache_manager()
                cached = await cache.get_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    f"employee:{employee_id}",
                    ttl_seconds=RedisTTL.HR_EMPLOYEES
                )
                if cached and cached.get("data"):
                    logger.info(
                        f"HR.get_employee employee_id={employee_id} source=cache"
                    )
                    return {
                        "employee": cached["data"],
                        "source": "cache"
                    }
            
            # 2. Fallback PostgreSQL
            manager = get_neon_hr_manager()
            employee = await manager.get_employee(
                UUID(company_id), 
                UUID(employee_id)
            )
            
            if not employee:
                return {"employee": None}
            
            serialized = _serialize_employee(employee)
            logger.info(f"HR.get_employee employee_id={employee_id} source=database")
            
            # 3. Sync vers Redis si user_id fourni
            if firebase_user_id:
                cache = get_hr_cache_manager()
                await cache.set_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    f"employee:{employee_id}",
                    serialized,
                    ttl_seconds=RedisTTL.HR_EMPLOYEES
                )
            
            return {
                "employee": serialized,
                "source": "database"
            }
        except Exception as e:
            logger.error(f"HR.get_employee error={e}")
            return {"employee": None, "error": str(e)}
    
    async def create_employee(
        self,
        company_id: str,
        identifier: str,
        first_name: str,
        last_name: str,
        birth_date: str,
        cluster_code: str,
        hire_date: str,
        firebase_user_id: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Crée un employé.
        
        RPC: HR.create_employee
        Args:
            company_id (str UUID)
            ... autres champs employé ...
            firebase_user_id (str, optional): Firebase UID pour invalidation cache
        Returns: { "employee_id": "uuid" }
        """
        try:
            # 1. Écrire dans PostgreSQL
            manager = get_neon_hr_manager()
            employee_id = await manager.create_employee(
                company_id=UUID(company_id),
                identifier=identifier,
                first_name=first_name,
                last_name=last_name,
                birth_date=birth_date,
                cluster_code=cluster_code,
                hire_date=hire_date,
                **kwargs
            )
            logger.info(f"HR.create_employee employee_id={employee_id}")
            
            # 2. Invalider le cache employees pour forcer le rechargement
            if firebase_user_id:
                cache = get_hr_cache_manager()
                await cache.invalidate_cache(firebase_user_id, company_id, "hr", "employees")
                logger.info(
                    f"HR.create_employee invalidated cache for user={firebase_user_id} "
                    f"company={company_id}"
                )
            
            return {"employee_id": str(employee_id)}
        except Exception as e:
            logger.error(f"HR.create_employee error={e}")
            return {"employee_id": None, "error": str(e)}
    
    async def update_employee(
        self,
        company_id: str,
        employee_id: str,
        firebase_user_id: str = None,
        **fields
    ) -> Dict[str, Any]:
        """
        Met à jour un employé.
        
        RPC: HR.update_employee
        Args:
            company_id (str UUID)
            employee_id (str UUID)
            firebase_user_id (str, optional): Firebase UID pour invalidation cache
            **fields: Champs à mettre à jour
        Returns: { "success": bool }
        """
        try:
            # 1. Mettre à jour dans PostgreSQL
            manager = get_neon_hr_manager()
            success = await manager.update_employee(
                UUID(company_id),
                UUID(employee_id),
                **fields
            )
            logger.info(f"HR.update_employee employee_id={employee_id} success={success}")
            
            # 2. Invalider les caches concernés
            if firebase_user_id and success:
                cache = get_hr_cache_manager()
                # Invalider la liste des employés
                await cache.invalidate_cache(firebase_user_id, company_id, "hr", "employees")
                # Invalider l'employé spécifique
                await cache.invalidate_cache(
                    firebase_user_id, company_id, "hr", f"employee:{employee_id}"
                )
                logger.info(
                    f"HR.update_employee invalidated cache for employee={employee_id} "
                    f"user={firebase_user_id}"
                )
            
            return {"success": success}
        except Exception as e:
            logger.error(f"HR.update_employee error={e}")
            return {"success": False, "error": str(e)}
    
    async def delete_employee(
        self, 
        company_id: str, 
        employee_id: str,
        firebase_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Supprime un employé (soft delete).
        
        RPC: HR.delete_employee
        Args:
            company_id (str UUID)
            employee_id (str UUID)
            firebase_user_id (str, optional): Firebase UID pour invalidation cache
        Returns: { "success": bool }
        """
        try:
            # 1. Soft delete dans PostgreSQL
            manager = get_neon_hr_manager()
            success = await manager.delete_employee(
                UUID(company_id),
                UUID(employee_id)
            )
            logger.info(f"HR.delete_employee employee_id={employee_id} success={success}")
            
            # 2. Invalider les caches concernés
            if firebase_user_id and success:
                cache = get_hr_cache_manager()
                # Invalider la liste des employés
                await cache.invalidate_cache(firebase_user_id, company_id, "hr", "employees")
                # Invalider l'employé spécifique
                await cache.invalidate_cache(
                    firebase_user_id, company_id, "hr", f"employee:{employee_id}"
                )
                # Invalider les contrats de l'employé
                await cache.invalidate_cache(
                    firebase_user_id, company_id, "hr", f"contracts:{employee_id}"
                )
                await cache.invalidate_cache(
                    firebase_user_id, company_id, "hr", f"active_contract:{employee_id}"
                )
                logger.info(
                    f"HR.delete_employee invalidated cache for employee={employee_id} "
                    f"user={firebase_user_id}"
                )
            
            return {"success": success}
        except Exception as e:
            logger.error(f"HR.delete_employee error={e}")
            return {"success": False, "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # CONTRACTS
    # ═══════════════════════════════════════════════════════════════
    
    async def list_contracts(
        self, 
        company_id: str, 
        employee_id: str,
        firebase_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Liste les contrats d'un employé.
        
        RPC: HR.list_contracts
        Args:
            company_id (str UUID)
            employee_id (str UUID)
            firebase_user_id (str, optional): Firebase UID pour le cache
        Returns: { "contracts": [...], "source": "cache"|"database" }
        """
        try:
            # 1. Tentative cache si user_id fourni
            if firebase_user_id:
                cache = get_hr_cache_manager()
                cached = await cache.get_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    f"contracts:{employee_id}",
                    ttl_seconds=RedisTTL.HR_CONTRACTS
                )
                if cached and cached.get("data"):
                    logger.info(
                        f"HR.list_contracts employee_id={employee_id} "
                        f"count={len(cached['data'])} source=cache"
                    )
                    return {
                        "contracts": cached["data"],
                        "source": "cache"
                    }
            
            # 2. Fallback PostgreSQL
            manager = get_neon_hr_manager()
            contracts = await manager.list_contracts(
                UUID(company_id),
                UUID(employee_id)
            )
            serialized = [_serialize_contract(c) for c in contracts]
            
            logger.info(
                f"HR.list_contracts employee_id={employee_id} "
                f"count={len(serialized)} source=database"
            )
            
            # 3. Sync vers Redis si user_id fourni
            if firebase_user_id and serialized:
                cache = get_hr_cache_manager()
                await cache.set_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    f"contracts:{employee_id}",
                    serialized,
                    ttl_seconds=RedisTTL.HR_CONTRACTS
                )
            
            return {
                "contracts": serialized,
                "source": "database"
            }
        except Exception as e:
            logger.error(f"HR.list_contracts error={e}")
            return {"contracts": [], "error": str(e)}
    
    async def get_active_contract(
        self, 
        company_id: str, 
        employee_id: str,
        firebase_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Récupère le contrat actif.
        
        RPC: HR.get_active_contract
        Args:
            company_id (str UUID)
            employee_id (str UUID)
            firebase_user_id (str, optional): Firebase UID pour le cache
        Returns: { "contract": {...}, "source": "cache"|"database" }
        """
        try:
            # 1. Tentative cache si user_id fourni
            if firebase_user_id:
                cache = get_hr_cache_manager()
                cached = await cache.get_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    f"active_contract:{employee_id}",
                    ttl_seconds=RedisTTL.HR_CONTRACTS
                )
                if cached and cached.get("data"):
                    logger.info(
                        f"HR.get_active_contract employee_id={employee_id} source=cache"
                    )
                    return {
                        "contract": cached["data"],
                        "source": "cache"
                    }
            
            # 2. Fallback PostgreSQL
            manager = get_neon_hr_manager()
            contract = await manager.get_active_contract(
                UUID(company_id),
                UUID(employee_id)
            )
            
            if not contract:
                return {"contract": None}
            
            serialized = _serialize_contract(contract)
            logger.info(
                f"HR.get_active_contract employee_id={employee_id} source=database"
            )
            
            # 3. Sync vers Redis si user_id fourni
            if firebase_user_id:
                cache = get_hr_cache_manager()
                await cache.set_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    f"active_contract:{employee_id}",
                    serialized,
                    ttl_seconds=RedisTTL.HR_CONTRACTS
                )
            
            return {
                "contract": serialized,
                "source": "database"
            }
        except Exception as e:
            logger.error(f"HR.get_active_contract error={e}")
            return {"contract": None, "error": str(e)}
    
    async def create_contract(
        self,
        company_id: str,
        employee_id: str,
        contract_type: str,
        start_date: str,
        base_salary: float,
        firebase_user_id: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Crée un contrat.
        
        RPC: HR.create_contract
        Args:
            company_id (str UUID)
            employee_id (str UUID)
            ... autres champs contrat ...
            firebase_user_id (str, optional): Firebase UID pour invalidation cache
        Returns: { "contract_id": "uuid" }
        """
        try:
            # 1. Créer le contrat dans PostgreSQL
            manager = get_neon_hr_manager()
            contract_id = await manager.create_contract(
                company_id=UUID(company_id),
                employee_id=UUID(employee_id),
                contract_type=contract_type,
                start_date=start_date,
                base_salary=base_salary,
                **kwargs
            )
            logger.info(f"HR.create_contract contract_id={contract_id}")
            
            # 2. Invalider les caches de contrats concernés
            if firebase_user_id:
                cache = get_hr_cache_manager()
                # Invalider la liste des contrats de l'employé
                await cache.invalidate_cache(
                    firebase_user_id, company_id, "hr", f"contracts:{employee_id}"
                )
                # Invalider le contrat actif (pourrait avoir changé)
                await cache.invalidate_cache(
                    firebase_user_id, company_id, "hr", f"active_contract:{employee_id}"
                )
                logger.info(
                    f"HR.create_contract invalidated cache for employee={employee_id} "
                    f"user={firebase_user_id}"
                )
            
            return {"contract_id": str(contract_id)}
        except Exception as e:
            logger.error(f"HR.create_contract error={e}")
            return {"contract_id": None, "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # CLUSTERS
    # ═══════════════════════════════════════════════════════════════
    
    async def list_clusters(
        self, 
        country_code: str = None,
        firebase_user_id: str = None,
        company_id: str = None
    ) -> Dict[str, Any]:
        """
        Liste les clusters disponibles.
        
        RPC: HR.list_clusters
        Args:
            country_code (str, optional): Code pays (CH, FR, etc.)
            firebase_user_id (str, optional): Firebase UID pour le cache
            company_id (str, optional): Company ID pour la clé de cache
        Returns: { "clusters": [...], "source": "cache"|"database" }
        """
        try:
            # 1. Tentative cache si user_id et company_id fournis
            if firebase_user_id and company_id:
                cache = get_hr_cache_manager()
                cache_sub_type = f"clusters:{country_code}" if country_code else "clusters"
                cached = await cache.get_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    cache_sub_type,
                    ttl_seconds=RedisTTL.HR_CLUSTERS
                )
                if cached and cached.get("data"):
                    logger.info(
                        f"HR.list_clusters country_code={country_code} "
                        f"count={len(cached['data'])} source=cache"
                    )
                    return {
                        "clusters": cached["data"],
                        "source": "cache"
                    }
            
            # 2. Fallback PostgreSQL
            manager = get_neon_hr_manager()
            clusters = await manager.list_clusters(country_code)
            serialized = [_serialize_value(c) for c in clusters]
            
            logger.info(
                f"HR.list_clusters country_code={country_code} "
                f"count={len(serialized)} source=database"
            )
            
            # 3. Sync vers Redis si user_id et company_id fournis
            if firebase_user_id and company_id and serialized:
                cache = get_hr_cache_manager()
                cache_sub_type = f"clusters:{country_code}" if country_code else "clusters"
                await cache.set_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    cache_sub_type,
                    serialized,
                    ttl_seconds=RedisTTL.HR_CLUSTERS
                )
            
            return {
                "clusters": serialized,
                "source": "database"
            }
        except Exception as e:
            logger.error(f"HR.list_clusters error={e}")
            return {"clusters": [], "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # PAYROLL (lecture seule - le calcul est fait par le Jobber)
    # ═══════════════════════════════════════════════════════════════
    
    async def get_payroll_result(
        self,
        company_id: str,
        employee_id: str,
        year: int,
        month: int
    ) -> Dict[str, Any]:
        """
        Récupère un résultat de paie.
        
        RPC: HR.get_payroll_result
        """
        try:
            manager = get_neon_hr_manager()
            result = await manager.get_payroll_result(
                UUID(company_id),
                UUID(employee_id),
                year,
                month
            )
            
            if not result:
                return {"payroll": None}
            
            logger.info(f"HR.get_payroll_result employee_id={employee_id} period={year}-{month}")
            return {"payroll": _serialize_payroll(result)}
        except Exception as e:
            logger.error(f"HR.get_payroll_result error={e}")
            return {"payroll": None, "error": str(e)}
    
    async def list_payroll_results(
        self,
        company_id: str,
        employee_id: str = None,
        year: int = None
    ) -> Dict[str, Any]:
        """
        Liste les résultats de paie.
        
        RPC: HR.list_payroll_results
        """
        try:
            manager = get_neon_hr_manager()
            results = await manager.list_payroll_results(
                UUID(company_id),
                UUID(employee_id) if employee_id else None,
                year
            )
            logger.info(f"HR.list_payroll_results company_id={company_id} count={len(results)}")
            return {
                "payroll_results": [_serialize_payroll(r) for r in results]
            }
        except Exception as e:
            logger.error(f"HR.list_payroll_results error={e}")
            return {"payroll_results": [], "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # JOBS ASYNCHRONES (via Jobber HR)
    # Ces méthodes soumettent des jobs au Jobber et retournent immédiatement.
    # Le Jobber appellera /hr/callback quand le job est terminé.
    # ═══════════════════════════════════════════════════════════════
    
    async def submit_payroll_calculate(
        self,
        user_id: str,
        company_id: str,
        employee_id: str,
        year: int,
        month: int,
        variables: Dict[str, Any] = None,
        force_recalculate: bool = False,
        session_id: str = None,
        mandate_path: str = None,
    ) -> Dict[str, Any]:
        """
        Soumet un calcul de paie au Jobber (asynchrone).
        
        RPC: HR.submit_payroll_calculate
        
        Le résultat sera envoyé via WebSocket quand le calcul est terminé.
        
        Args:
            user_id: Firebase UID pour le callback
            company_id: UUID de la company
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
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.submit_payroll_calculate(
                user_id=user_id,
                company_id=company_id,
                employee_id=employee_id,
                year=year,
                month=month,
                variables=variables,
                force_recalculate=force_recalculate,
                session_id=session_id,
                mandate_path=mandate_path,
            )
            
            logger.info(
                f"HR.submit_payroll_calculate job_id={result.get('job_id')} "
                f"employee={employee_id} period={year}-{month}"
            )
            return result
        except Exception as e:
            logger.error(f"HR.submit_payroll_calculate error={e}")
            return {"status": "failed", "error": str(e)}
    
    async def submit_payroll_batch(
        self,
        user_id: str,
        company_id: str,
        year: int,
        month: int,
        employee_ids: list = None,
        cluster_code: str = None,
        session_id: str = None,
        mandate_path: str = None,
    ) -> Dict[str, Any]:
        """
        Soumet un batch de calculs de paie au Jobber (asynchrone).
        
        RPC: HR.submit_payroll_batch
        
        Le Jobber enverra des mises à jour de progression via WebSocket.
        
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
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.submit_payroll_batch(
                user_id=user_id,
                company_id=company_id,
                year=year,
                month=month,
                employee_ids=employee_ids,
                cluster_code=cluster_code,
                session_id=session_id,
                mandate_path=mandate_path,
            )
            
            logger.info(
                f"HR.submit_payroll_batch job_id={result.get('job_id')} "
                f"company={company_id} period={year}-{month}"
            )
            return result
        except Exception as e:
            logger.error(f"HR.submit_payroll_batch error={e}")
            return {"status": "failed", "error": str(e)}
    
    async def submit_pdf_generate(
        self,
        user_id: str,
        payroll_id: str,
        session_id: str = None,
        mandate_path: str = None,
    ) -> Dict[str, Any]:
        """
        Soumet une génération de PDF au Jobber.
        
        RPC: HR.submit_pdf_generate
        
        Args:
            user_id: Firebase UID pour le callback
            payroll_id: UUID du résultat de paie
            session_id: Session pour routage WebSocket
            mandate_path: Chemin Firebase pour traçabilité
        
        Returns:
            {"job_id": "...", "status": "pending"} ou {"pdf_url": "..."}
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.submit_pdf_generate(
                user_id=user_id,
                payroll_id=payroll_id,
                session_id=session_id,
                mandate_path=mandate_path,
            )
            
            logger.info(
                f"HR.submit_pdf_generate job_id={result.get('job_id')} payroll={payroll_id}"
            )
            return result
        except Exception as e:
            logger.error(f"HR.submit_pdf_generate error={e}")
            return {"status": "failed", "error": str(e)}
    
    async def get_job_status(
        self,
        job_id: str,
    ) -> Dict[str, Any]:
        """
        Récupère le statut d'un job auprès du Jobber.
        
        RPC: HR.get_job_status
        
        Args:
            job_id: ID du job
        
        Returns:
            {"job_id": "...", "status": "...", "progress": {...}}
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_job_status(job_id)
            
            logger.info(f"HR.get_job_status job_id={job_id} status={result.get('status')}")
            return result
        except Exception as e:
            logger.error(f"HR.get_job_status error={e}")
            return {"job_id": job_id, "status": "error", "error": str(e)}
    
    async def check_jobber_health(self) -> Dict[str, Any]:
        """
        Vérifie la disponibilité du Jobber HR.
        
        RPC: HR.check_jobber_health
        
        Returns:
            {"status": "ok"|"error", "jobber_url": "...", ...}
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.check_health()
            
            logger.info(f"HR.check_jobber_health status={result.get('status')}")
            return result
        except Exception as e:
            logger.error(f"HR.check_jobber_health error={e}")
            return {"status": "error", "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════
    # DONNÉES DE RÉFÉRENCE (via Jobber HR)
    # Ces méthodes récupèrent les tables de référence dynamiques.
    # ═══════════════════════════════════════════════════════════════
    
    async def get_all_references(
        self,
        country_code: str = "CH",
        lang: str = "fr",
        firebase_user_id: str = None,
        company_id: str = None
    ) -> Dict[str, Any]:
        """
        Récupère toutes les données de référence en un seul appel.
        
        RPC: HR.get_all_references
        
        Optimal pour le chargement initial du module HR.
        
        Args:
            country_code: Code pays (CH, FR, etc.)
            lang: Langue (fr, de, en, it)
            firebase_user_id (str, optional): Firebase UID pour le cache
            company_id (str, optional): Company ID pour la clé de cache
        
        Returns:
            {
                "contract_types": [...],
                "remuneration_types": [...],
                "family_status": [...],
                "tax_status": [...],
                "permit_types": [...],
                "payroll_status": [...],
                "source": "cache"|"database"
            }
        """
        try:
            # 1. Tentative cache si user_id et company_id fournis
            if firebase_user_id and company_id:
                cache = get_hr_cache_manager()
                cache_sub_type = f"references:{country_code}:{lang}"
                cached = await cache.get_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    cache_sub_type,
                    ttl_seconds=RedisTTL.HR_REFERENCES
                )
                if cached and cached.get("data"):
                    logger.info(
                        f"HR.get_all_references country={country_code} lang={lang} source=cache"
                    )
                    return {
                        **cached["data"],
                        "source": "cache"
                    }
            
            # 2. Fallback via Jobber client
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_all_references(
                country_code=country_code,
                lang=lang,
            )
            
            logger.info(
                f"HR.get_all_references country={country_code} lang={lang} "
                f"keys={list(result.keys()) if isinstance(result, dict) else 'error'} source=database"
            )
            
            # 3. Sync vers Redis si user_id et company_id fournis
            if firebase_user_id and company_id and isinstance(result, dict):
                cache = get_hr_cache_manager()
                cache_sub_type = f"references:{country_code}:{lang}"
                await cache.set_cached_data(
                    firebase_user_id,
                    company_id,
                    "hr",
                    cache_sub_type,
                    result,
                    ttl_seconds=RedisTTL.HR_REFERENCES
                )
            
            return {
                **result,
                "source": "database"
            } if isinstance(result, dict) else result
            
        except Exception as e:
            logger.error(f"HR.get_all_references error={e}")
            return {"error": str(e)}
    
    async def get_contract_types(
        self,
        country_code: str = None,
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère les types de contrat.
        
        RPC: HR.get_contract_types
        
        Returns:
            {"contract_types": [{"code": "CDI", "label": "..."}]}
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_contract_types(country_code, lang)
            
            logger.info(f"HR.get_contract_types count={len(result)}")
            return {"contract_types": result}
        except Exception as e:
            logger.error(f"HR.get_contract_types error={e}")
            return {"contract_types": [], "error": str(e)}
    
    async def get_remuneration_types(
        self,
        country_code: str = None,
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère les types de rémunération.
        
        RPC: HR.get_remuneration_types
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_remuneration_types(country_code, lang)
            
            logger.info(f"HR.get_remuneration_types count={len(result)}")
            return {"remuneration_types": result}
        except Exception as e:
            logger.error(f"HR.get_remuneration_types error={e}")
            return {"remuneration_types": [], "error": str(e)}
    
    async def get_family_status(
        self,
        country_code: str = None,
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère les statuts familiaux.
        
        RPC: HR.get_family_status
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_family_status(country_code, lang)
            
            logger.info(f"HR.get_family_status count={len(result)}")
            return {"family_status": result}
        except Exception as e:
            logger.error(f"HR.get_family_status error={e}")
            return {"family_status": [], "error": str(e)}
    
    async def get_tax_status(
        self,
        country_code: str = "CH",
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère les statuts fiscaux (spécifiques au pays).
        
        RPC: HR.get_tax_status
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_tax_status(country_code, lang)
            
            logger.info(f"HR.get_tax_status country={country_code} count={len(result)}")
            return {"tax_status": result}
        except Exception as e:
            logger.error(f"HR.get_tax_status error={e}")
            return {"tax_status": [], "error": str(e)}
    
    async def get_permit_types(
        self,
        country_code: str = "CH",
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère les types de permis (spécifiques au pays).
        
        RPC: HR.get_permit_types
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_permit_types(country_code, lang)
            
            logger.info(f"HR.get_permit_types country={country_code} count={len(result)}")
            return {"permit_types": result}
        except Exception as e:
            logger.error(f"HR.get_permit_types error={e}")
            return {"permit_types": [], "error": str(e)}
    
    async def get_payroll_status(
        self,
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Récupère les statuts de paie (workflow).
        
        RPC: HR.get_payroll_status
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_payroll_status(lang)
            
            logger.info(f"HR.get_payroll_status count={len(result)}")
            return {"payroll_status": result}
        except Exception as e:
            logger.error(f"HR.get_payroll_status error={e}")
            return {"payroll_status": [], "error": str(e)}
    
    async def get_payroll_items(
        self,
        country_code: str = "CH",
        cluster_code: str = None,
    ) -> Dict[str, Any]:
        """
        Récupère les rubriques de paie disponibles.
        
        RPC: HR.get_payroll_items
        
        Args:
            country_code: Code pays
            cluster_code: Optionnel, filtre par cluster/CCT
        """
        try:
            from .tools.hr_jobber_client import get_hr_jobber_client
            
            client = get_hr_jobber_client()
            result = await client.get_payroll_items(country_code, cluster_code)
            
            logger.info(
                f"HR.get_payroll_items country={country_code} "
                f"cluster={cluster_code} count={len(result)}"
            )
            return {"payroll_items": result}
        except Exception as e:
            logger.error(f"HR.get_payroll_items error={e}")
            return {"payroll_items": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

# Instance pour enregistrement dans le router RPC (main.py)
hr_rpc_handlers = HRRPCHandlers()


def get_hr_rpc_handlers() -> HRRPCHandlers:
    """Retourne l'instance singleton des handlers HR."""
    return hr_rpc_handlers
