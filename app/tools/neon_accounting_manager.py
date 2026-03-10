"""
Gestionnaire Neon Accounting avec pattern Singleton thread-safe.

Ce module centralise toutes les operations PostgreSQL Neon pour le module Comptabilite.
Il suit le pattern existant de neon_hr_manager.py.

ARCHITECTURE:
    - Un seul pool de connexions partage (reutilise celui de neon_hr_manager)
    - Methodes pour COA upsert, journals upsert, GL sync incrementale
    - Gestion sync_metadata et period_balances
    - Hash SHA-256 pour detection des changements

CONFIGURATION:
    - NEON_DATABASE_URL: URL de connexion directe (dev local)
    - NEON_SECRET_NAME: Nom du secret dans Secret Manager (default: pinnokio_postgres_neon)

Usage:
    from app.tools.neon_accounting_manager import get_neon_accounting_manager

    manager = get_neon_accounting_manager()
    await manager.upsert_chart_of_accounts(company_id, coa_data)
"""

import asyncio
import hashlib
import json
import os
import threading
import logging
from datetime import date, datetime
from typing import Optional, Dict, Any, List, Union
from uuid import UUID

try:
    import asyncpg
except ImportError:
    asyncpg = None

from .g_cred import get_secret

logger = logging.getLogger("accounting.neon_manager")


def _compute_hash(row: Dict[str, Any]) -> str:
    """
    Calcule le hash SHA-256 d'une ligne de donnees metier.

    Exclut les champs de controle Pinnokio pour ne hasher que les donnees ERP.

    Args:
        row: Dictionnaire des champs metier

    Returns:
        str: Hash hexadecimal SHA-256 (64 caracteres)
    """
    excluded = {
        "pinnokio_checked_time", "pinnokio_hash", "pinnokio_version",
        "is_deleted", "created_at", "updated_at", "id",
    }
    business = {k: v for k, v in row.items() if k not in excluded}
    return hashlib.sha256(
        json.dumps(business, sort_keys=True, default=str).encode()
    ).hexdigest()


def _to_date(value: Union[str, date, None]) -> Optional[date]:
    """Convertit une valeur en objet date Python (asyncpg requiert des objets natifs)."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise ValueError(f"Cannot convert {type(value)} to date")


def _to_datetime(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Convertit une valeur en objet datetime Python (asyncpg requiert des objets natifs pour TIMESTAMPTZ)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        # Handle various Odoo datetime formats: "2025-12-01 14:03:09", "2025-12-01T14:03:09"
        value = value.strip()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            # Fallback: replace space separator with T for fromisoformat
            return datetime.fromisoformat(value.replace(" ", "T"))
    raise ValueError(f"Cannot convert {type(value)} to datetime")


class NeonAccountingManager:
    """
    Gestionnaire Neon Accounting avec pattern Singleton thread-safe.

    Centralise:
    - Pool de connexions PostgreSQL Neon
    - Methodes upsert pour COA, journals, GL entries
    - Sync incrementale avec hash SHA-256
    - Cache de soldes periodiques
    """

    _instance: Optional["NeonAccountingManager"] = None
    _lock = threading.Lock()
    _initialized = False

    # Pool de connexions
    _pool: Optional["asyncpg.Pool"] = None
    _pool_lock: Optional[asyncio.Lock] = None

    # Cache mandate_path -> company_id
    _company_cache: Dict[str, UUID] = {}

    def __new__(cls):
        """Implementation thread-safe du pattern Singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialisation (une seule fois grace au flag _initialized)."""
        with self._lock:
            if not self.__class__._initialized:
                self._database_url = self._get_database_url()
                self.__class__._initialized = True
                logger.info("NeonAccountingManager initialise (singleton)")

    def _get_database_url(self) -> str:
        """Recupere l'URL de connexion Neon (env > Secret Manager)."""
        if url := os.getenv("NEON_DATABASE_URL"):
            logger.info("Utilisation de NEON_DATABASE_URL depuis l'environnement")
            return url

        secret_name = os.getenv("NEON_SECRET_NAME", "pinnokio_postgres_neon")
        try:
            url = get_secret(secret_name)
            logger.info(f"URL Neon recuperee depuis Secret Manager ({secret_name})")
            return url
        except Exception as e:
            logger.error(f"Impossible de recuperer le secret Neon: {e}")
            raise RuntimeError("NEON_DATABASE_URL non configure")

    async def get_pool(self) -> "asyncpg.Pool":
        """Retourne le pool de connexions (creation lazy, thread-safe)."""
        if asyncpg is None:
            raise ImportError("asyncpg n'est pas installe. Executez: pip install asyncpg")

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
                        ssl="require" if "neon.tech" in self._database_url else "prefer",
                    )
                    logger.info("Pool PostgreSQL Neon cree (min=2, max=10)")
        return self._pool

    async def close_pool(self):
        """Ferme le pool de connexions."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Pool PostgreSQL Neon ferme")

    # ===================================================================
    # MAPPING FIREBASE -> POSTGRESQL
    # ===================================================================

    async def get_company_id_from_mandate_path(
        self, mandate_path: str
    ) -> Optional[UUID]:
        """Recupere le company_id PostgreSQL depuis un mandate_path Firebase."""
        # Normalize: strip leading/trailing slashes for consistent matching
        normalized = mandate_path.strip("/")

        if normalized in self._company_cache:
            return self._company_cache[normalized]

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # Try exact match first, then normalized (handles /clients/... vs clients/...)
            row = await conn.fetchrow(
                "SELECT id FROM core.companies WHERE firebase_mandate_path = $1 OR firebase_mandate_path = $2",
                normalized,
                mandate_path,
            )
            if row:
                company_id = row["id"]
                self._company_cache[normalized] = company_id
                return company_id
            return None

    # ===================================================================
    # CHART OF ACCOUNTS (COA)
    # ===================================================================

    async def upsert_chart_of_accounts(
        self, company_id: UUID, coa_data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Insere ou met a jour le plan comptable enrichi.

        Args:
            company_id: UUID de la societe
            coa_data: Liste de dicts avec les champs COA enrichis par DF_ANALYSER

        Returns:
            Dict avec compteurs: added, modified, unchanged
        """
        if not coa_data:
            return {"added": 0, "modified": 0, "unchanged": 0}

        pool = await self.get_pool()
        stats = {"added": 0, "modified": 0, "unchanged": 0}

        async with pool.acquire() as conn:
            async with conn.transaction():
                for item in coa_data:
                    account_number = str(item.get("account_number") or item.get("code", ""))
                    if not account_number:
                        continue

                    raw_is_active = item.get("isactive", item.get("is_active", True))

                    sync_hash = _compute_hash({
                        "account_number": account_number,
                        "account_name": item.get("account_name") or item.get("name", ""),
                        "erp_account_type": item.get("erp_account_type") or item.get("account_type") or item.get("type", ""),
                        "account_nature": item.get("account_nature") or item.get("klk_account_nature", ""),
                        "account_function": item.get("account_function") or item.get("klk_account_function", ""),
                        "is_active": bool(raw_is_active) if raw_is_active is not None else True,
                    })

                    # Determine account_class from first digit(s)
                    account_class = account_number[0] if account_number else None

                    # erp_source from transformer or fallback
                    erp_source = item.get("erp_source")

                    is_active = bool(raw_is_active) if raw_is_active is not None else True

                    result = await conn.execute(
                        """
                        INSERT INTO core.chart_of_accounts (
                            company_id, account_number, account_name,
                            erp_account_type, account_nature, account_function,
                            parent_account_number, firebase_account_id,
                            erp_account_id, account_class, sync_hash,
                            erp_source, is_active
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                        ON CONFLICT (company_id, account_number) DO UPDATE
                        SET account_name = EXCLUDED.account_name,
                            erp_account_type = EXCLUDED.erp_account_type,
                            account_nature = EXCLUDED.account_nature,
                            account_function = EXCLUDED.account_function,
                            parent_account_number = EXCLUDED.parent_account_number,
                            firebase_account_id = EXCLUDED.firebase_account_id,
                            erp_account_id = EXCLUDED.erp_account_id,
                            account_class = EXCLUDED.account_class,
                            sync_hash = EXCLUDED.sync_hash,
                            erp_source = EXCLUDED.erp_source,
                            is_active = EXCLUDED.is_active
                        WHERE core.chart_of_accounts.sync_hash IS DISTINCT FROM EXCLUDED.sync_hash
                           OR (core.chart_of_accounts.erp_account_id IS NULL OR core.chart_of_accounts.erp_account_id = '')
                              AND EXCLUDED.erp_account_id != ''
                        """,
                        company_id,
                        account_number,
                        item.get("account_name") or item.get("name", "Unknown"),
                        item.get("erp_account_type") or item.get("account_type") or item.get("type"),
                        item.get("account_nature") or item.get("klk_account_nature"),
                        item.get("account_function") or item.get("klk_account_function"),
                        item.get("parent_account_number") or item.get("parent_code"),
                        str(item.get("firebase_account_id", "")) or None,
                        str(item.get("erp_account_id") or item.get("erp_id") or item.get("account_id") or ""),
                        account_class,
                        sync_hash,
                        erp_source,
                        is_active,
                    )

                    tag = result  # "INSERT 0 1" or "INSERT 0 0" or "UPDATE 1"
                    if "INSERT 0 1" in tag:
                        stats["added"] += 1
                    elif "UPDATE 1" in tag:
                        stats["modified"] += 1
                    else:
                        stats["unchanged"] += 1

        logger.info(
            "COA upsert company=%s: added=%d modified=%d unchanged=%d",
            company_id, stats["added"], stats["modified"], stats["unchanged"],
        )
        return stats

    # ===================================================================
    # JOURNALS
    # ===================================================================

    async def upsert_journals(
        self, company_id: UUID, journals: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Insere ou met a jour les journaux comptables.

        Args:
            company_id: UUID de la societe
            journals: Liste de dicts avec journal_code, journal_name, etc.

        Returns:
            Dict avec compteurs: added, modified, unchanged
        """
        if not journals:
            return {"added": 0, "modified": 0, "unchanged": 0}

        pool = await self.get_pool()
        stats = {"added": 0, "modified": 0, "unchanged": 0}

        async with pool.acquire() as conn:
            async with conn.transaction():
                for j in journals:
                    journal_code = str(j.get("journal_code") or j.get("code", ""))
                    if not journal_code:
                        continue

                    result = await conn.execute(
                        """
                        INSERT INTO accounting.journals (
                            company_id, journal_code, journal_name,
                            journal_type, erp_journal_id, erp_source,
                            sync_hash, is_active
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                        ON CONFLICT (company_id, journal_code) DO UPDATE
                        SET journal_name = EXCLUDED.journal_name,
                            journal_type = EXCLUDED.journal_type,
                            erp_journal_id = EXCLUDED.erp_journal_id,
                            erp_source = EXCLUDED.erp_source,
                            sync_hash = EXCLUDED.sync_hash,
                            is_active = TRUE
                        """,
                        company_id,
                        journal_code,
                        j.get("journal_name") or j.get("name", journal_code),
                        j.get("journal_type") or j.get("journal_category") or j.get("type"),
                        str(j.get("erp_journal_id") or j.get("erp_id") or ""),
                        j.get("erp_source"),
                        j.get("sync_hash"),
                    )

                    if "INSERT 0 1" in result:
                        stats["added"] += 1
                    elif "UPDATE 1" in result:
                        stats["modified"] += 1
                    else:
                        stats["unchanged"] += 1

        logger.info(
            "Journals upsert company=%s: added=%d modified=%d unchanged=%d",
            company_id, stats["added"], stats["modified"], stats["unchanged"],
        )
        return stats

    # ===================================================================
    # GL ENTRIES (INCREMENTAL SYNC)
    # ===================================================================

    async def incremental_sync_gl_entries(
        self, company_id: UUID, entries: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Sync incrementale des ecritures du grand livre.

        Utilise une table temporaire de staging + INSERT ... ON CONFLICT
        avec comparaison de hash SHA-256 pour ne mettre a jour que les
        ecritures modifiees.

        Args:
            company_id: UUID de la societe
            entries: Liste de dicts avec les champs GL

        Returns:
            Dict avec compteurs: added, modified, unchanged
        """
        if not entries:
            return {"added": 0, "modified": 0, "unchanged": 0}

        pool = await self.get_pool()
        stats = {"added": 0, "modified": 0, "unchanged": 0}

        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1. Creer table temporaire (English column names)
                await conn.execute("""
                    CREATE TEMP TABLE _gl_staging (
                        entry_id VARCHAR(100),
                        entry_date DATE,
                        last_update_date TIMESTAMPTZ,
                        journal_code VARCHAR(10),
                        document_ref VARCHAR(100),
                        account_number VARCHAR(20),
                        account_name VARCHAR(255),
                        description TEXT,
                        partner_name VARCHAR(255),
                        debit NUMERIC(15,2),
                        credit NUMERIC(15,2),
                        reconciliation_ref VARCHAR(20),
                        entry_state VARCHAR(20),
                        currency CHAR(3),
                        exchange_rate NUMERIC(12,6),
                        amount_currency_value NUMERIC(15,2),
                        currency_erp_id INTEGER,
                        pinnokio_hash VARCHAR(64)
                    ) ON COMMIT DROP
                """)

                # 2. Inserer dans staging via COPY (bulk — beaucoup plus rapide que INSERT 1-par-1)
                # Support both old French and new English field names
                records = []
                for e in entries:
                    entry_hash = _compute_hash({
                        "entry_id": e.get("entry_id"),
                        "entry_date": str(e.get("entry_date") or e.get("date_ecriture", "")),
                        "journal_code": e.get("journal_code", ""),
                        "document_ref": e.get("document_ref") or e.get("piece_number"),
                        "account_number": e.get("account_number") or e.get("compte", ""),
                        "account_name": e.get("account_name") or e.get("compte_label"),
                        "description": e.get("description") or e.get("libelle"),
                        "partner_name": e.get("partner_name"),
                        "debit": str(e.get("debit", 0)),
                        "credit": str(e.get("credit", 0)),
                        "reconciliation_ref": e.get("reconciliation_ref") or e.get("lettrage"),
                        "entry_state": e.get("entry_state", "posted"),
                        "currency": e.get("currency") or e.get("devise", "CHF"),
                        "exchange_rate": str(e.get("exchange_rate") or e.get("taux_change", 1.0)),
                        "amount_currency_value": str(e.get("amount_currency_value")),
                        "currency_erp_id": str(e.get("currency_erp_id")),
                    })

                    records.append((
                        str(e.get("entry_id", "")),
                        _to_date(e.get("entry_date") or e.get("date_ecriture")),
                        _to_datetime(e.get("last_update_date")),
                        str(e.get("journal_code", "")),
                        e.get("document_ref") or e.get("piece_number"),
                        str(e.get("account_number") or e.get("compte", "")),
                        e.get("account_name") or e.get("compte_label"),
                        e.get("description") or e.get("libelle"),
                        e.get("partner_name"),
                        float(e.get("debit", 0)),
                        float(e.get("credit", 0)),
                        e.get("reconciliation_ref") or e.get("lettrage"),
                        e.get("entry_state", "posted"),
                        e.get("currency") or e.get("devise", "CHF"),
                        float(e.get("exchange_rate") or e.get("taux_change", 1.0)),
                        e.get("amount_currency_value"),  # None si devise de base
                        e.get("currency_erp_id"),        # None si inconnu
                        entry_hash,
                    ))

                await conn.copy_records_to_table(
                    "_gl_staging",
                    records=records,
                    columns=[
                        "entry_id", "entry_date", "last_update_date",
                        "journal_code", "document_ref", "account_number",
                        "account_name", "description", "partner_name",
                        "debit", "credit", "reconciliation_ref",
                        "entry_state", "currency", "exchange_rate",
                        "amount_currency_value", "currency_erp_id",
                        "pinnokio_hash",
                    ],
                )

                # 3. Upsert depuis staging (English column names)
                result = await conn.fetch("""
                    INSERT INTO accounting.gl_entries (
                        company_id, entry_id, entry_date, last_update_date,
                        journal_code, document_ref, account_number, account_name,
                        description, partner_name, debit, credit,
                        reconciliation_ref, entry_state, currency, exchange_rate,
                        amount_currency_value, currency_erp_id,
                        pinnokio_hash, pinnokio_version, pinnokio_checked_time,
                        is_deleted
                    )
                    SELECT
                        $1, s.entry_id, s.entry_date, s.last_update_date,
                        s.journal_code, s.document_ref, s.account_number, s.account_name,
                        s.description, s.partner_name, s.debit, s.credit,
                        s.reconciliation_ref, s.entry_state, s.currency, s.exchange_rate,
                        s.amount_currency_value, s.currency_erp_id,
                        s.pinnokio_hash, 0, NOW(), FALSE
                    FROM _gl_staging s
                    ON CONFLICT (company_id, entry_id) DO UPDATE
                    SET entry_date = EXCLUDED.entry_date,
                        last_update_date = EXCLUDED.last_update_date,
                        journal_code = EXCLUDED.journal_code,
                        document_ref = EXCLUDED.document_ref,
                        account_number = EXCLUDED.account_number,
                        account_name = EXCLUDED.account_name,
                        description = EXCLUDED.description,
                        partner_name = EXCLUDED.partner_name,
                        debit = EXCLUDED.debit,
                        credit = EXCLUDED.credit,
                        reconciliation_ref = EXCLUDED.reconciliation_ref,
                        entry_state = EXCLUDED.entry_state,
                        currency = EXCLUDED.currency,
                        exchange_rate = EXCLUDED.exchange_rate,
                        amount_currency_value = EXCLUDED.amount_currency_value,
                        currency_erp_id = EXCLUDED.currency_erp_id,
                        pinnokio_hash = EXCLUDED.pinnokio_hash,
                        pinnokio_version = accounting.gl_entries.pinnokio_version + 1,
                        pinnokio_checked_time = NOW(),
                        is_deleted = FALSE
                    WHERE accounting.gl_entries.pinnokio_hash IS DISTINCT FROM EXCLUDED.pinnokio_hash
                    RETURNING (xmax = 0) AS is_insert
                """, company_id)

                for row in result:
                    if row["is_insert"]:
                        stats["added"] += 1
                    else:
                        stats["modified"] += 1

                stats["unchanged"] = len(entries) - stats["added"] - stats["modified"]

        logger.info(
            "GL sync company=%s: added=%d modified=%d unchanged=%d (total=%d)",
            company_id, stats["added"], stats["modified"],
            stats["unchanged"], len(entries),
        )
        return stats

    async def mark_deleted_entries(
        self, company_id: UUID, active_entry_ids: List[str]
    ) -> int:
        """
        Marque comme supprimees les ecritures absentes de la liste active.

        Utilisee lors de la reconciliation hebdomadaire.

        Args:
            company_id: UUID de la societe
            active_entry_ids: Liste des entry_id encore actifs dans l'ERP

        Returns:
            Nombre d'ecritures marquees comme supprimees
        """
        if not active_entry_ids:
            return 0

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE accounting.gl_entries
                SET is_deleted = TRUE, updated_at = NOW()
                WHERE company_id = $1
                  AND NOT is_deleted
                  AND entry_id != ALL($2::varchar[])
                """,
                company_id,
                active_entry_ids,
            )
            count = int(result.split()[-1]) if "UPDATE" in result else 0
            if count:
                logger.info("Marked %d entries as deleted for company=%s", count, company_id)
            return count

    # ===================================================================
    # SYNC METADATA
    # ===================================================================

    async def get_sync_metadata(
        self, company_id: UUID, sync_type: str
    ) -> Optional[Dict[str, Any]]:
        """Recupere les metadata de sync pour un type donne."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM accounting.sync_metadata
                WHERE company_id = $1 AND sync_type = $2
                """,
                company_id, sync_type,
            )
            return dict(row) if row else None

    async def update_sync_metadata(
        self, company_id: UUID, sync_type: str, **fields
    ) -> None:
        """
        Cree ou met a jour les metadata de synchronisation.

        Args:
            company_id: UUID de la societe
            sync_type: "coa", "gl", ou "journals"
            **fields: Champs a mettre a jour (dataset_version, last_sync_time, etc.)
        """
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # Build SET clause dynamically
            set_parts = []
            values = [company_id, sync_type]
            idx = 3

            for field, value in fields.items():
                set_parts.append(f"{field} = ${idx}")
                # Serialize dicts/lists to JSON string for TEXT columns
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, default=str)
                values.append(value)
                idx += 1

            if set_parts:
                set_clause = ", ".join(set_parts)
            else:
                set_clause = "updated_at = NOW()"

            await conn.execute(
                f"""
                INSERT INTO accounting.sync_metadata (company_id, sync_type)
                VALUES ($1, $2)
                ON CONFLICT (company_id, sync_type) DO UPDATE
                SET {set_clause}
                """,
                *values,
            )

    # ===================================================================
    # PERIOD BALANCES (CACHE D'AGREGATS)
    # ===================================================================

    async def refresh_period_balances(
        self, company_id: UUID, fiscal_year: int
    ) -> int:
        """
        Recompute les soldes periodiques pour une annee fiscale.

        Supprime et recalcule tous les soldes mensuels depuis gl_entries.

        Args:
            company_id: UUID de la societe
            fiscal_year: Annee fiscale

        Returns:
            Nombre de lignes inserees
        """
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Supprimer les soldes existants
                await conn.execute(
                    """
                    DELETE FROM accounting.period_balances
                    WHERE company_id = $1 AND fiscal_year = $2
                    """,
                    company_id, fiscal_year,
                )

                # Recalculer depuis gl_entries (English column names)
                result = await conn.execute(
                    """
                    INSERT INTO accounting.period_balances (
                        company_id, fiscal_year, fiscal_month,
                        account_number, account_name,
                        total_debit, total_credit, balance,
                        entry_count, currency, computed_at,
                        source_version
                    )
                    SELECT
                        gl.company_id,
                        $2,
                        EXTRACT(MONTH FROM gl.entry_date)::INTEGER,
                        gl.account_number,
                        MAX(COALESCE(coa.account_name, gl.account_name)),
                        SUM(gl.debit),
                        SUM(gl.credit),
                        SUM(gl.debit) - SUM(gl.credit),
                        COUNT(*),
                        MODE() WITHIN GROUP (ORDER BY gl.currency),
                        NOW(),
                        COALESCE(
                            (SELECT dataset_version FROM accounting.sync_metadata
                             WHERE company_id = $1 AND sync_type = 'gl'),
                            0
                        )
                    FROM accounting.gl_entries gl
                    LEFT JOIN core.chart_of_accounts coa
                        ON coa.company_id = gl.company_id
                        AND coa.account_number = gl.account_number
                    WHERE gl.company_id = $1
                      AND EXTRACT(YEAR FROM gl.entry_date)::INTEGER = $2
                      AND NOT gl.is_deleted
                      AND gl.entry_state = 'posted'
                    GROUP BY gl.company_id, EXTRACT(MONTH FROM gl.entry_date),
                             gl.account_number
                    """,
                    company_id, fiscal_year,
                )

                count = int(result.split()[-1]) if "INSERT" in result else 0
                logger.info(
                    "Period balances refreshed company=%s year=%d: %d rows",
                    company_id, fiscal_year, count,
                )
                return count

    # ===================================================================
    # QUERY HELPERS (pour le cron et les outils agent)
    # ===================================================================

    async def get_active_companies_with_gl(self) -> List[Dict[str, Any]]:
        """Retourne les societes ayant des metadata GL (pour le cron)."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.id AS company_id, c.firebase_mandate_path,
                       sm.last_sync_time, sm.last_source_update_date,
                       sm.timestamp_granularity, sm.dataset_version
                FROM core.companies c
                JOIN accounting.sync_metadata sm
                    ON sm.company_id = c.id AND sm.sync_type = 'gl'
                WHERE sm.sync_status != 'running'
            """)
            return [dict(r) for r in rows]


# ===================================================================
# INSTANCE SINGLETON & HELPER
# ===================================================================

_neon_accounting_manager: Optional[NeonAccountingManager] = None


def get_neon_accounting_manager() -> NeonAccountingManager:
    """
    Retourne l'instance singleton du NeonAccountingManager.

    Usage:
        manager = get_neon_accounting_manager()
        await manager.upsert_chart_of_accounts(company_id, coa_data)
    """
    global _neon_accounting_manager
    if _neon_accounting_manager is None:
        _neon_accounting_manager = NeonAccountingManager()
    return _neon_accounting_manager
