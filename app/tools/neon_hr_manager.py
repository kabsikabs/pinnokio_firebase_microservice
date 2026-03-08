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
from datetime import date, datetime
from decimal import Decimal
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
            # asyncpg ne supporte pas channel_binding dans l'URL — le retirer
            if 'channel_binding=' in url:
                import re
                url = re.sub(r'[&?]channel_binding=[^&]*', '', url)
                url = url.replace('?&', '?').rstrip('?')
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
                    # Ne PAS passer ssl= si l'URL contient déjà sslmode=
                    # (conflit asyncpg: double négociation SSL → timeout)
                    pool_kwargs = dict(
                        min_size=1,
                        max_size=10,
                        command_timeout=60,
                    )
                    if 'sslmode=' not in self._database_url:
                        pool_kwargs['ssl'] = 'require' if 'neon.tech' in self._database_url else 'prefer'
                    self._pool = await asyncpg.create_pool(
                        self._database_url,
                        **pool_kwargs,
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

    async def delete_company(
        self,
        mandate_path: str,
        cascade: bool = True
    ) -> Dict[str, Any]:
        """
        Supprime une société et cascade vers les données HR.

        Args:
            mandate_path: Chemin Firebase pour identifier la société
            cascade: Si True, supprime aussi employés, contrats, paies

        Returns:
            Dict avec rapport de suppression
        """
        try:
            company_id = await self.get_company_id_from_mandate_path(mandate_path)

            if not company_id:
                return {
                    "success": True,
                    "company_id": None,
                    "deleted_counts": {},
                    "message": "Company not found in Neon"
                }

            pool = await self.get_pool()
            deleted_counts = {
                "employees": 0, "contracts": 0, "payroll_results": 0,
                "company_payroll_items": 0,
            }

            async with pool.acquire() as conn:
                async with conn.transaction():
                    if cascade:
                        # 1. Supprimer payroll_results
                        result = await conn.execute("""
                            DELETE FROM hr.payroll_results
                            WHERE employee_id IN (
                                SELECT id FROM hr.employees WHERE company_id = $1
                            )
                        """, company_id)
                        deleted_counts["payroll_results"] = int(result.split()[-1]) if "DELETE" in result else 0

                        # 2. Supprimer contracts
                        result = await conn.execute("""
                            DELETE FROM hr.contracts
                            WHERE employee_id IN (
                                SELECT id FROM hr.employees WHERE company_id = $1
                            )
                        """, company_id)
                        deleted_counts["contracts"] = int(result.split()[-1]) if "DELETE" in result else 0

                        # 3. Supprimer employees
                        result = await conn.execute(
                            "DELETE FROM hr.employees WHERE company_id = $1",
                            company_id
                        )
                        deleted_counts["employees"] = int(result.split()[-1]) if "DELETE" in result else 0

                        # 4. Supprimer company_payroll_items (overrides rubriques)
                        result = await conn.execute(
                            "DELETE FROM hr.company_payroll_items WHERE company_id = $1",
                            company_id
                        )
                        deleted_counts["company_payroll_items"] = int(result.split()[-1]) if "DELETE" in result else 0

                    # 5. Supprimer la societe (cascade auto: accounting.*, core.company_settings, etc.)
                    await conn.execute("DELETE FROM core.companies WHERE id = $1", company_id)

            # Nettoyer le cache
            if mandate_path in self._company_cache:
                del self._company_cache[mandate_path]

            logger.info(f"✅ Company deleted from Neon: {company_id} {deleted_counts}")
            return {"success": True, "company_id": str(company_id), "deleted_counts": deleted_counts}

        except Exception as e:
            logger.error(f"❌ Neon delete failed: {e}")
            return {"success": False, "error": str(e)}

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
        """Crée un nouvel employé.

        Required columns: company_id, identifier, first_name, last_name,
                          birth_date, cluster_code, hire_date
        Optional columns via kwargs: email, phone, position, department, status
        """
        birth_date_obj = _to_date(birth_date)
        hire_date_obj = _to_date(hire_date)

        # Build optional columns dynamically
        optional_cols = []
        optional_vals = []
        _ALLOWED_OPTIONAL = {
            "email": str, "phone": str, "gender": str,
            "nationality": str, "tax_status": str,
            "family_status": str, "dependents": int,
            "address": str, "city": str, "postal_code": str,
            "country_code": str, "permit_type": str,
        }
        param_idx = 8  # $1-$7 are the required columns
        for col, _ in _ALLOWED_OPTIONAL.items():
            val = kwargs.get(col)
            if val is not None:
                optional_cols.append(col)
                optional_vals.append(val)
                param_idx += 1

        cols = "company_id, identifier, first_name, last_name, birth_date, cluster_code, hire_date"
        placeholders = "$1, $2, $3, $4, $5, $6, $7"
        if optional_cols:
            cols += ", " + ", ".join(optional_cols)
            placeholders += ", " + ", ".join(
                f"${i}" for i in range(8, 8 + len(optional_cols))
            )

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                f"""
                INSERT INTO hr.employees ({cols})
                VALUES ({placeholders})
                RETURNING id
                """,
                company_id, identifier, first_name, last_name,
                birth_date_obj, cluster_code, hire_date_obj,
                *optional_vals,
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
            "gender", "nationality", "cluster_code", "tax_status",
            "family_status", "dependents", "address", "city",
            "postal_code", "email", "phone", "hire_date",
            "termination_date", "is_active", "country_code",
            "permit_type",
        ]

        # Champs qui nécessitent une conversion date
        date_fields = ["birth_date", "hire_date", "termination_date"]
        
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
                       c.remuneration_type, c.annual_leave_days,
                       c.job_title, c.department, c.country_code,
                       c.provisions, c.is_active, c.created_at, c.updated_at
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
        """Crée un nouveau contrat avec tous les champs étendus."""
        import json as _json

        # Convertir les dates strings en objets date
        start_date_obj = _to_date(start_date)
        end_date_obj = _to_date(kwargs.get("end_date"))

        # Pack provisions JSONB from individual kwargs
        provisions = {}
        for prov_key in ("thirteenth_month", "thirteenth_month_rate",
                         "bonus_target", "bonus_type"):
            val = kwargs.pop(prov_key, None)
            if val is not None:
                provisions[prov_key] = val

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # Vérifier que l'employé appartient à la company
            emp = await conn.fetchrow(
                "SELECT id, country_code FROM hr.employees WHERE id = $1 AND company_id = $2",
                employee_id, company_id
            )
            if not emp:
                raise ValueError("Employee not found in company")

            country_code = kwargs.get("country_code") or (dict(emp).get("country_code"))

            result = await conn.fetchrow(
                """
                INSERT INTO hr.contracts (
                    employee_id, contract_type, start_date, end_date,
                    base_salary, currency, work_rate, weekly_hours,
                    remuneration_type, annual_leave_days,
                    job_title, department, country_code, provisions
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                RETURNING id
                """,
                employee_id,
                contract_type,
                start_date_obj,
                end_date_obj,
                base_salary,
                kwargs.get("currency", "CHF"),
                kwargs.get("work_rate", 1.0),
                kwargs.get("weekly_hours", 42.0),
                kwargs.get("remuneration_type", "MONTHLY"),
                kwargs.get("annual_leave_days", 25),
                kwargs.get("job_title"),
                kwargs.get("department"),
                country_code,
                _json.dumps(provisions) if provisions else None,
            )
            logger.info(f"✅ Contrat créé: {contract_type} pour employé {employee_id}")
            return result["id"]
    
    async def update_contract(
        self,
        company_id: UUID,
        contract_id: UUID,
        **fields
    ) -> bool:
        """Met à jour un contrat existant (pattern identique à update_employee)."""
        import json as _json

        allowed = {
            "contract_type", "start_date", "end_date", "base_salary",
            "currency", "work_rate", "weekly_hours", "remuneration_type",
            "annual_leave_days", "job_title", "department",
            "provisions", "is_active", "country_code",
        }
        filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not filtered:
            return True  # Nothing to update

        # Convert date strings
        for date_key in ("start_date", "end_date"):
            if date_key in filtered:
                filtered[date_key] = _to_date(filtered[date_key])

        # Serialize provisions if present
        if "provisions" in filtered and isinstance(filtered["provisions"], dict):
            filtered["provisions"] = _json.dumps(filtered["provisions"])

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # Verify contract belongs to company via JOIN
            check = await conn.fetchrow(
                """
                SELECT c.id FROM hr.contracts c
                JOIN hr.employees e ON c.employee_id = e.id
                WHERE c.id = $1 AND e.company_id = $2
                """,
                contract_id, company_id
            )
            if not check:
                raise ValueError("Contract not found in company")

            # Build dynamic SET clause
            set_parts = []
            params = []
            idx = 1
            for col, val in filtered.items():
                set_parts.append(f"{col} = ${idx}")
                params.append(val)
                idx += 1

            set_parts.append(f"updated_at = NOW()")
            params.append(contract_id)

            query = f"""
                UPDATE hr.contracts
                SET {', '.join(set_parts)}
                WHERE id = ${idx}
            """
            await conn.execute(query, *params)
            logger.info(f"✅ Contrat mis à jour: {contract_id} ({list(filtered.keys())})")
            return True

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

    # ═══════════════════════════════════════════════════════════════
    # ENDPOINTS RULES ENGINE (cascade résolution)
    # ═══════════════════════════════════════════════════════════════

    async def resolve_rules_cascade(
        self,
        company_id: UUID,
        cluster_code: str,
        as_of_date: Union[str, date, None] = None,
    ) -> List[Dict[str, Any]]:
        """
        Résout les règles de paie avec héritage cascade.

        Appelle la fonction PL/pgSQL hr.resolve_cascade_rules qui remonte
        la hiérarchie des clusters (canton → pays) et applique les overrides
        société (company_payroll_items).

        Args:
            company_id: UUID de la société
            cluster_code: Code du cluster (ex: 'CH-GE')
            as_of_date: Date de référence (None = aujourd'hui)

        Returns:
            Liste des règles résolues avec source_level indiquant
            l'origine ('cluster' ou 'country').
        """
        as_of = _to_date(as_of_date) or date.today()
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM hr.resolve_cascade_rules($1, $2, $3)",
                company_id, cluster_code, as_of
            )
            return [dict(row) for row in rows]

    async def get_calculation_rules(
        self,
        company_id: UUID,
        cluster_code: str,
        item_codes: List[str] = None,
        as_of_date: Union[str, date, None] = None,
    ) -> List[Dict[str, Any]]:
        """
        Récupère les règles de calcul filtrées par codes.

        Wrapper autour de resolve_rules_cascade avec filtre optionnel
        sur les codes de rubriques.

        Args:
            company_id: UUID de la société
            cluster_code: Code du cluster (ex: 'CH-GE')
            item_codes: Liste de codes à filtrer (None = tous)
            as_of_date: Date de référence (None = aujourd'hui)

        Returns:
            Liste des règles filtrées
        """
        all_rules = await self.resolve_rules_cascade(
            company_id, cluster_code, as_of_date
        )
        if item_codes:
            codes_set = set(item_codes)
            return [r for r in all_rules if r.get("code") in codes_set]
        return all_rules

    async def get_country_profile(
        self,
        country_code: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère le profil pays (paramètres par défaut).

        Args:
            country_code: Code pays ISO 2 lettres (ex: 'CH')

        Returns:
            Dictionnaire avec weekly_hours_default, annual_leave_days_default,
            has_thirteenth_month, social_security_system, settings, etc.
            None si le pays n'est pas configuré.
        """
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM hr.country_profiles WHERE country_code = $1",
                country_code
            )
            return dict(row) if row else None

    async def get_payroll_items(
        self,
        country_code: str = "CH",
        cluster_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Récupère les rubriques de paie depuis le catalogue.

        Lecture directe Neon (pas un job worker).

        Args:
            country_code: Code pays ISO 2 lettres (ex: 'CH')
            cluster_code: Optionnel, filtre par cluster/CCT

        Returns:
            Liste des rubriques actives triées par sort_order
        """
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            if cluster_code:
                rows = await conn.fetch(
                    """
                    SELECT id, code, version, label_fr, label_en, label_de, label_it,
                           nature, category, country_code, cluster_code,
                           rate_employee, rate_employer,
                           ceiling_type, ceiling_amount, calculation_base,
                           is_mandatory, is_taxable, applies_to_13th,
                           sort_order, effective_from, effective_to,
                           legal_reference, legal_article
                    FROM hr.payroll_items_catalog
                    WHERE country_code = $1
                      AND (cluster_code = $2 OR cluster_code IS NULL)
                      AND is_active = true
                    ORDER BY sort_order, code
                    """,
                    country_code, cluster_code,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, code, version, label_fr, label_en, label_de, label_it,
                           nature, category, country_code, cluster_code,
                           rate_employee, rate_employer,
                           ceiling_type, ceiling_amount, calculation_base,
                           is_mandatory, is_taxable, applies_to_13th,
                           sort_order, effective_from, effective_to,
                           legal_reference, legal_article
                    FROM hr.payroll_items_catalog
                    WHERE country_code = $1
                      AND is_active = true
                    ORDER BY sort_order, code
                    """,
                    country_code,
                )
            return [_row_to_dict(row) for row in rows]


    # ═══════════════════════════════════════════════════════════════════════
    # REFERENCE DATA (ref_* tables — direct Neon reads)
    # ═══════════════════════════════════════════════════════════════════════

    # Allowed ref tables and their label column for lang selection
    _REF_TABLES = {
        "ref_contract_types":     {"label_cols": ["label_fr", "label_en", "label_de", "label_it"]},
        "ref_remuneration_types": {"label_cols": ["label_fr", "label_en"]},
        "ref_family_status":      {"label_cols": ["label_fr", "label_en"]},
        "ref_tax_status":         {"label_cols": ["label_fr", "label_en"]},
        "ref_permit_types":       {"label_cols": ["label_fr", "label_en"]},
    }

    async def get_ref_data(
        self,
        table_name: str,
        country_code: Optional[str] = None,
        lang: str = "fr",
    ) -> List[Dict[str, Any]]:
        """
        Generic reader for hr.ref_* tables.

        Args:
            table_name: One of ref_contract_types, ref_remuneration_types, etc.
            country_code: Optional country filter (e.g. 'CH')
            lang: Language for label selection ('fr', 'en', 'de', 'it')

        Returns:
            List of dicts with code, label, and table-specific columns.
        """
        if table_name not in self._REF_TABLES:
            raise ValueError(f"Unknown ref table: {table_name}")

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            if country_code:
                rows = await conn.fetch(
                    f"SELECT * FROM hr.{table_name} WHERE country_code = $1 ORDER BY sort_order, code",
                    country_code,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT * FROM hr.{table_name} ORDER BY sort_order, code"
                )

        results = []
        for row in rows:
            d = _row_to_dict(row)
            # Add a computed 'label' field based on requested language
            label_key = f"label_{lang}" if f"label_{lang}" in d else "label_fr"
            d["label"] = d.get(label_key) or d.get("label_fr", d.get("code", ""))
            results.append(d)
        return results

    async def get_all_references(
        self,
        country_code: str = "CH",
        lang: str = "fr",
    ) -> Dict[str, Any]:
        """
        Fetch all reference data in a single call (optimal for page init).

        Returns:
            {
                "contract_types": [...],
                "remuneration_types": [...],
                "family_status": [...],
                "tax_status": [...],
                "permit_types": [...],
            }
        """
        result = {}
        for table_name in self._REF_TABLES:
            # table_name like "ref_contract_types" → key "contract_types"
            key = table_name.replace("ref_", "")
            result[key] = await self.get_ref_data(table_name, country_code, lang)
        return result


def _row_to_dict(row) -> Dict[str, Any]:
    """Convertit un asyncpg Record en dict JSON-safe."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, UUID):
            d[k] = str(v)
        elif isinstance(v, (date, datetime)):
            d[k] = v.isoformat()
        elif isinstance(v, Decimal):
            d[k] = float(v)
    return d


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
