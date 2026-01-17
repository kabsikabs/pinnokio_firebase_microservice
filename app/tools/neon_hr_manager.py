"""
Gestionnaire Neon HR avec pattern Singleton thread-safe.

Ce module centralise toutes les connexions PostgreSQL Neon pour le module HR.
Il suit le pattern existant de l'application (similaire à firebase_providers).

ARCHITECTURE:
    - Un seul pool de connexions partagé
    - Méthodes CRUD pour employees, contracts, clusters, payroll
    - Cache de mapping mandate_path → company_id
    - Thread-safe pour les appels concurrents

CONFIGURATION:
    - NEON_DATABASE_URL: URL de connexion directe (dev local)
    - NEON_SECRET_NAME: Nom du secret dans Secret Manager (default: pinnokio_postgres_neon)

Usage:
    from app.tools.neon_hr_manager import get_neon_hr_manager
    
    manager = get_neon_hr_manager()
    employees = await manager.list_employees(company_id)
"""

import asyncio
import os
import threading
import logging
from datetime import date
from typing import Optional, Dict, Any, List, Union
from uuid import UUID

try:
    import asyncpg
except ImportError:
    asyncpg = None
    print("⚠️ asyncpg non installé. Exécuter: pip install asyncpg")

from .g_cred import get_secret

logger = logging.getLogger("hr.neon_manager")


def _to_date(value: Union[str, date, None]) -> Optional[date]:
    """
    Convertit une valeur en objet date Python.
    
    asyncpg nécessite des objets date natifs, pas des strings.
    
    Args:
        value: String ISO "YYYY-MM-DD", objet date, ou None
    
    Returns:
        Objet date ou None
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"Cannot convert {type(value)} to date")


class NeonHRManager:
    """
    Gestionnaire Neon HR avec pattern Singleton thread-safe.
    
    Centralise:
    - Pool de connexions PostgreSQL Neon
    - Méthodes CRUD pour employees, contracts, clusters, payroll
    - Cache de mapping mandate_path → company_id
    
    Usage:
        manager = NeonHRManager()
        employees = await manager.list_employees(company_id)
    """
    
    _instance: Optional['NeonHRManager'] = None
    _lock = threading.Lock()
    _initialized = False
    
    # Pool de connexions
    _pool: Optional["asyncpg.Pool"] = None
    _pool_lock: Optional[asyncio.Lock] = None
    
    # Cache mandate_path → company_id
    _company_cache: Dict[str, UUID] = {}
    
    def __new__(cls):
        """Implémentation thread-safe du pattern Singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialisation (une seule fois grâce au flag _initialized)."""
        with self._lock:
            if not self.__class__._initialized:
                self._database_url = self._get_database_url()
                self.__class__._initialized = True
                logger.info("✅ NeonHRManager initialisé (singleton)")
    
    def _get_database_url(self) -> str:
        """
        Récupère l'URL de connexion Neon.
        
        Priorité:
        1. Variable d'environnement NEON_DATABASE_URL
        2. Google Secret Manager (pinnokio_postgres_neon)
        """
        # Priorité 1: Variable d'environnement
        if url := os.getenv("NEON_DATABASE_URL"):
            logger.info("✅ Utilisation de NEON_DATABASE_URL depuis l'environnement")
            return url
        
        # Priorité 2: Secret Manager (utilise g_cred existant)
        secret_name = os.getenv("NEON_SECRET_NAME", "pinnokio_postgres_neon")
        try:
            url = get_secret(secret_name)
            logger.info(f"✅ URL Neon récupérée depuis Secret Manager ({secret_name})")
            return url
        except Exception as e:
            logger.error(f"⚠️ Impossible de récupérer le secret Neon: {e}")
            raise RuntimeError("NEON_DATABASE_URL non configuré")
    
    async def get_pool(self) -> "asyncpg.Pool":
        """
        Retourne le pool de connexions (création lazy).
        
        Thread-safe grâce à asyncio.Lock.
        """
        if asyncpg is None:
            raise ImportError(
                "asyncpg n'est pas installé. "
                "Exécutez: pip install asyncpg"
            )
        
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self._database_url,
                        min_size=2,
                        max_size=10,
                        command_timeout=60,
                        # SSL requis pour Neon
                        ssl='require' if 'neon.tech' in self._database_url else 'prefer',
                    )
                    logger.info("✅ Pool PostgreSQL Neon créé (min=2, max=10)")
        return self._pool
    
    async def close_pool(self):
        """Ferme le pool de connexions."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("✅ Pool PostgreSQL Neon fermé")
    
    async def check_connection(self) -> Dict[str, Any]:
        """
        Vérifie la connexion à la base de données.
        
        Returns:
            Dictionnaire avec le statut de connexion
        """
        try:
            pool = await self.get_pool()
            async with pool.acquire() as conn:
                version = await conn.fetchval("SELECT version()")
                
                # Vérifier les schémas
                schemas = await conn.fetch("""
                    SELECT schema_name 
                    FROM information_schema.schemata 
                    WHERE schema_name IN ('core', 'hr')
                """)
                
                return {
                    "status": "connected",
                    "database": "PostgreSQL Neon",
                    "version": version,
                    "schemas": [s["schema_name"] for s in schemas],
                    "pool_size": self._pool.get_size() if self._pool else 0,
                }
        except Exception as e:
            logger.error(f"❌ Erreur de connexion Neon: {e}")
            return {
                "status": "error",
                "error": str(e),
            }
    
    # ═══════════════════════════════════════════════════════════════
    # MAPPING FIREBASE → POSTGRESQL
    # ═══════════════════════════════════════════════════════════════
    
    async def get_company_id_from_mandate_path(
        self, 
        mandate_path: str
    ) -> Optional[UUID]:
        """
        Récupère le company_id PostgreSQL depuis un mandate_path Firebase.
        
        Args:
            mandate_path: Chemin Firebase (ex: "comptes/xxx/mandats/yyy")
        
        Returns:
            UUID de l'entreprise ou None si non trouvée
        """
        # Vérifier le cache
        if mandate_path in self._company_cache:
            return self._company_cache[mandate_path]
        
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM core.companies WHERE firebase_mandate_path = $1",
                mandate_path
            )
            if row:
                company_id = row["id"]
                self._company_cache[mandate_path] = company_id
                return company_id
            return None
    
    async def get_or_create_company(
        self,
        account_firebase_uid: str,
        mandate_path: str,
        company_name: str,
        country: str,
        country_code: str = None,
        region: str = None,
        region_code: str = None,
    ) -> UUID:
        """
        Récupère ou crée une entreprise dans PostgreSQL.
        
        Appelé lors de la première synchronisation Firebase → PostgreSQL.
        """
        # Vérifier si existe
        existing = await self.get_company_id_from_mandate_path(mandate_path)
        if existing:
            return existing
        
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # Récupérer ou créer le compte
            account = await conn.fetchrow(
                "SELECT id FROM core.accounts WHERE firebase_uid = $1",
                account_firebase_uid
            )
            if not account:
                account = await conn.fetchrow(
                    """
                    INSERT INTO core.accounts (firebase_uid, display_name, email)
                    VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    account_firebase_uid,
                    "Imported Account",
                    f"{account_firebase_uid}@imported.local"
                )
            
            # Parser le mandate_path
            parts = mandate_path.split("/")
            firebase_parent_id = parts[1] if len(parts) >= 2 else None
            firebase_mandate_id = parts[3] if len(parts) >= 4 else None
            
            # Créer l'entreprise
            result = await conn.fetchrow(
                """
                INSERT INTO core.companies (
                    account_id, firebase_mandate_path, firebase_mandate_id,
                    firebase_parent_id, name, country, country_code, region, region_code
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                account["id"], mandate_path, firebase_mandate_id,
                firebase_parent_id, company_name, country, country_code, region, region_code
            )
            
            company_id = result["id"]
            self._company_cache[mandate_path] = company_id
            logger.info(f"✅ Entreprise créée: {company_name} ({company_id})")
            return company_id
    
    # ═══════════════════════════════════════════════════════════════
    # ENDPOINTS EMPLOYEES
    # ═══════════════════════════════════════════════════════════════
    
    async def list_employees(self, company_id: UUID) -> List[Dict[str, Any]]:
        """Liste tous les employés d'une entreprise."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, identifier, first_name, last_name, birth_date,
                       cluster_code, hire_date, is_active, created_at
                FROM hr.employees 
                WHERE company_id = $1 AND is_active = TRUE
                ORDER BY last_name, first_name
                """,
                company_id
            )
            return [dict(row) for row in rows]
    
    async def get_employee(
        self, 
        company_id: UUID, 
        employee_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Récupère un employé par son ID."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM hr.employees 
                WHERE id = $1 AND company_id = $2
                """,
                employee_id, company_id
            )
            return dict(row) if row else None
    
    async def create_employee(
        self,
        company_id: UUID,
        identifier: str,
        first_name: str,
        last_name: str,
        birth_date: Union[str, date],
        cluster_code: str,
        hire_date: Union[str, date],
        **kwargs
    ) -> UUID:
        """Crée un nouvel employé."""
        # Convertir les dates strings en objets date
        birth_date_obj = _to_date(birth_date)
        hire_date_obj = _to_date(hire_date)
        
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO hr.employees (
                    company_id, identifier, first_name, last_name, 
                    birth_date, cluster_code, hire_date
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                company_id, identifier, first_name, last_name,
                birth_date_obj, cluster_code, hire_date_obj
            )
            logger.info(f"✅ Employé créé: {first_name} {last_name} ({result['id']})")
            return result["id"]
    
    async def update_employee(
        self,
        company_id: UUID,
        employee_id: UUID,
        **fields
    ) -> bool:
        """Met à jour un employé."""
        if not fields:
            return False
        
        # Construire la requête dynamiquement
        set_clauses = []
        values = []
        idx = 1
        
        allowed_fields = [
            "identifier", "first_name", "last_name", "birth_date",
            "cluster_code", "hire_date", "is_active"
        ]
        
        # Champs qui nécessitent une conversion date
        date_fields = ["birth_date", "hire_date"]
        
        for field, value in fields.items():
            if field in allowed_fields:
                # Convertir les dates strings en objets date
                if field in date_fields and value is not None:
                    value = _to_date(value)
                set_clauses.append(f"{field} = ${idx}")
                values.append(value)
                idx += 1
        
        if not set_clauses:
            return False
        
        values.extend([employee_id, company_id])
        
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE hr.employees 
                SET {", ".join(set_clauses)}, updated_at = NOW()
                WHERE id = ${idx} AND company_id = ${idx + 1}
                """,
                *values
            )
            return "UPDATE 1" in result
    
    async def delete_employee(
        self, 
        company_id: UUID, 
        employee_id: UUID
    ) -> bool:
        """Supprime un employé (soft delete)."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE hr.employees 
                SET is_active = FALSE, updated_at = NOW()
                WHERE id = $1 AND company_id = $2
                """,
                employee_id, company_id
            )
            return "UPDATE 1" in result
    
    # ═══════════════════════════════════════════════════════════════
    # ENDPOINTS CONTRACTS
    # ═══════════════════════════════════════════════════════════════
    
    async def list_contracts(
        self, 
        company_id: UUID, 
        employee_id: UUID
    ) -> List[Dict[str, Any]]:
        """Liste les contrats d'un employé."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.employee_id, c.contract_type, c.start_date, c.end_date,
                       c.base_salary, c.currency, c.work_rate, c.weekly_hours,
                       c.provisions, c.is_active
                FROM hr.contracts c
                JOIN hr.employees e ON c.employee_id = e.id
                WHERE c.employee_id = $1 AND e.company_id = $2
                ORDER BY c.start_date DESC
                """,
                employee_id, company_id
            )
            return [dict(row) for row in rows]
    
    async def get_active_contract(
        self, 
        company_id: UUID, 
        employee_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Récupère le contrat actif d'un employé."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT c.* FROM hr.contracts c
                JOIN hr.employees e ON c.employee_id = e.id
                WHERE c.employee_id = $1 
                  AND e.company_id = $2
                  AND c.is_active = TRUE
                  AND c.start_date <= CURRENT_DATE
                  AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
                ORDER BY c.start_date DESC
                LIMIT 1
                """,
                employee_id, company_id
            )
            return dict(row) if row else None
    
    async def create_contract(
        self,
        company_id: UUID,
        employee_id: UUID,
        contract_type: str,
        start_date: Union[str, date],
        base_salary: float,
        **kwargs
    ) -> UUID:
        """Crée un nouveau contrat."""
        # Convertir les dates strings en objets date
        start_date_obj = _to_date(start_date)
        end_date_obj = _to_date(kwargs.get("end_date"))
        
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # Vérifier que l'employé appartient à la company
            emp = await conn.fetchrow(
                "SELECT id FROM hr.employees WHERE id = $1 AND company_id = $2",
                employee_id, company_id
            )
            if not emp:
                raise ValueError("Employee not found in company")
            
            result = await conn.fetchrow(
                """
                INSERT INTO hr.contracts (
                    employee_id, contract_type, start_date, end_date,
                    base_salary, currency, work_rate, weekly_hours
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                employee_id,
                contract_type,
                start_date_obj,
                end_date_obj,
                base_salary,
                kwargs.get("currency", "CHF"),
                kwargs.get("work_rate", 1.0),
                kwargs.get("weekly_hours", 42.0)
            )
            logger.info(f"✅ Contrat créé: {contract_type} pour employé {employee_id}")
            return result["id"]
    
    # ═══════════════════════════════════════════════════════════════
    # ENDPOINTS CLUSTERS
    # ═══════════════════════════════════════════════════════════════
    
    async def list_clusters(
        self, 
        country_code: str = None
    ) -> List[Dict[str, Any]]:
        """Liste les clusters disponibles."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            if country_code:
                rows = await conn.fetch(
                    """
                    SELECT c.* FROM hr.clusters c
                    JOIN hr.country_clusters cc ON c.code = cc.cluster_code
                    WHERE cc.country_code = $1 AND c.is_active = TRUE
                    ORDER BY c.code
                    """,
                    country_code
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM hr.clusters WHERE is_active = TRUE ORDER BY code"
                )
            return [dict(row) for row in rows]
    
    # ═══════════════════════════════════════════════════════════════
    # ENDPOINTS PAYROLL (lecture seule - le calcul est fait par le Jobber)
    # ═══════════════════════════════════════════════════════════════
    
    async def get_payroll_result(
        self,
        company_id: UUID,
        employee_id: UUID,
        year: int,
        month: int
    ) -> Optional[Dict[str, Any]]:
        """Récupère un résultat de paie existant."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.* FROM hr.payroll_results r
                JOIN hr.employees e ON r.employee_id = e.id
                WHERE r.employee_id = $1 
                  AND r.period_year = $2 
                  AND r.period_month = $3
                  AND e.company_id = $4
                """,
                employee_id, year, month, company_id
            )
            return dict(row) if row else None
    
    async def list_payroll_results(
        self,
        company_id: UUID,
        employee_id: UUID = None,
        year: int = None
    ) -> List[Dict[str, Any]]:
        """Liste les résultats de paie."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            query = """
                SELECT r.* FROM hr.payroll_results r
                JOIN hr.employees e ON r.employee_id = e.id
                WHERE e.company_id = $1
            """
            params = [company_id]
            idx = 2
            
            if employee_id:
                query += f" AND r.employee_id = ${idx}"
                params.append(employee_id)
                idx += 1
            
            if year:
                query += f" AND r.period_year = ${idx}"
                params.append(year)
            
            query += " ORDER BY r.period_year DESC, r.period_month DESC"
            
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE SINGLETON & HELPER
# ═══════════════════════════════════════════════════════════════════════════

_neon_hr_manager: Optional[NeonHRManager] = None


def get_neon_hr_manager() -> NeonHRManager:
    """
    Retourne l'instance singleton du NeonHRManager.
    
    Usage:
        manager = get_neon_hr_manager()
        employees = await manager.list_employees(company_id)
    """
    global _neon_hr_manager
    if _neon_hr_manager is None:
        _neon_hr_manager = NeonHRManager()
    return _neon_hr_manager
