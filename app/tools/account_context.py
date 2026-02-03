# -*- coding: utf-8 -*-
"""
Account Context - Gestion centralisee du contexte utilisateur/societe.

Ce module fournit:
1. AccountContext: dataclass portant tout le contexte multi-tenant
2. ensure_account_context(): point d'entree unique pour etablir le contexte

Architecture:
    ensure_account_context(firebase_uid, mandate_path, ...)
        |
        +-> 1. core.users        (utilisateur Firebase)
        +-> 2. core.accounts     (compte proprietaire)
        +-> 3. core.account_access (relation user <-> account)
        +-> 4. core.companies    (societe/mandat)
        |
        +-> Retourne: AccountContext

Usage:
    from app.tools.account_context import ensure_account_context, AccountContext

    context = await ensure_account_context(
        firebase_uid="7hQs0jlu...",
        mandate_path="clients/.../mandates/...",
        company_name="ETERNITI SA",
        country="Switzerland",
    )

    # Utiliser le contexte pour le scoping multi-tenant
    if context.can_write():
        await some_repository.create(context.company_id, data)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List
from uuid import UUID

logger = logging.getLogger("account.context")


# =============================================================================
# ENUMS
# =============================================================================

class AccessType(str, Enum):
    """Type d'acces a un compte."""
    OWNER = "owner"      # Proprietaire du compte
    SHARED = "shared"    # Acces partage (invite)


class UserProfile(str, Enum):
    """Profil/role de l'utilisateur."""
    ADMIN = "admin"      # Acces complet + gestion
    MANAGER = "manager"  # Acces complet sans gestion
    USER = "user"        # Acces standard
    VIEWER = "viewer"    # Lecture seule


# =============================================================================
# ACCOUNT CONTEXT DATACLASS
# =============================================================================

@dataclass
class AccountContext:
    """
    Contexte complet d'un utilisateur connecte a une societe.

    Ce contexte est cree une seule fois lors de l'acces a une societe
    et passe a tous les modules pour le scoping multi-tenant.

    Attributes:
        user_id: UUID de l'utilisateur dans core.users
        account_id: UUID du compte dans core.accounts
        company_id: UUID de la societe dans core.companies
        firebase_uid: UID Firebase pour les operations Firebase
        mandate_path: Chemin Firebase complet du mandat
        access_type: Type d'acces (owner ou shared)
        profile: Profil/role de l'utilisateur
        country_code: Code ISO du pays (CH, FR, etc.)
        cluster_code: Code du cluster HR (CH-GE, FR-75, etc.)
        email: Email de l'utilisateur
        display_name: Nom d'affichage
    """
    user_id: UUID
    account_id: UUID
    company_id: UUID
    firebase_uid: str
    mandate_path: str
    access_type: AccessType
    profile: UserProfile
    country_code: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    cluster_code: Optional[str] = None

    def can_read(self) -> bool:
        """L'utilisateur peut-il lire les donnees?"""
        return True  # Tous les profils peuvent lire

    def can_write(self) -> bool:
        """L'utilisateur peut-il modifier les donnees?"""
        return self.profile in (UserProfile.ADMIN, UserProfile.MANAGER, UserProfile.USER)

    def can_admin(self) -> bool:
        """L'utilisateur peut-il administrer la societe?"""
        return self.profile == UserProfile.ADMIN

    def is_owner(self) -> bool:
        """L'utilisateur est-il le proprietaire du compte?"""
        return self.access_type == AccessType.OWNER

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire pour serialisation."""
        return {
            "user_id": str(self.user_id),
            "account_id": str(self.account_id),
            "company_id": str(self.company_id),
            "firebase_uid": self.firebase_uid,
            "mandate_path": self.mandate_path,
            "access_type": self.access_type.value,
            "profile": self.profile.value,
            "country_code": self.country_code,
            "email": self.email,
            "display_name": self.display_name,
            "cluster_code": self.cluster_code,
        }


# =============================================================================
# COUNTRY CODES MAPPING
# =============================================================================

COUNTRY_CODES = {
    "Switzerland": "CH",
    "France": "FR",
    "Belgium": "BE",
    "Germany": "DE",
    "Italy": "IT",
    "Netherlands": "NL",
    "Spain": "ES",
    "Portugal": "PT",
    "United Kingdom": "GB",
    "Canada": "CA",
    "United States": "US",
    "Luxembourg": "LU",
    "Austria": "AT",
    "Monaco": "MC",
}


# =============================================================================
# ENSURE ACCOUNT CONTEXT
# =============================================================================

async def ensure_account_context(
    firebase_uid: str,
    mandate_path: str,
    company_name: str,
    country: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    country_code: Optional[str] = None,
    region_code: Optional[str] = None,
    base_currency: str = "CHF",
) -> AccountContext:
    """
    Point d'entree UNIQUE pour etablir le contexte utilisateur + societe.

    Cree ou recupere TOUTES les entites core necessaires dans une transaction:
    1. core.users (utilisateur Firebase)
    2. core.accounts (compte proprietaire)
    3. core.account_access (relation N:N)
    4. core.companies (societe)

    Args:
        firebase_uid: UID Firebase de l'utilisateur
        mandate_path: Chemin Firebase du mandat (clients/.../mandates/...)
        company_name: Nom de la societe
        country: Nom du pays ("Switzerland", "France", etc.)
        email: Email de l'utilisateur (optionnel)
        display_name: Nom d'affichage (optionnel)
        country_code: Code ISO du pays (optionnel, deduit si non fourni)
        region_code: Code region/canton (optionnel)
        base_currency: Devise de base (defaut: CHF)

    Returns:
        AccountContext: Contexte complet pour le scoping multi-tenant

    Example:
        >>> context = await ensure_account_context(
        ...     firebase_uid="7hQs0jluP5YUWcREqdi22NRFnU32",
        ...     mandate_path="clients/.../mandates/...",
        ...     company_name="ETERNITI SA",
        ...     country="Switzerland",
        ...     region_code="GE"
        ... )
        >>> print(context.company_id)
        UUID('550e8400-e29b-41d4-a716-446655440000')
    """
    from .neon_hr_manager import get_neon_hr_manager

    manager = get_neon_hr_manager()
    pool = await manager.get_pool()

    # Deduire les valeurs par defaut
    country_code = country_code or COUNTRY_CODES.get(country, "CH")
    email = email or f"{firebase_uid}@pinnokio.local"
    display_name = display_name or "Auto-created User"

    # Parser le mandate_path pour extraire les IDs Firebase
    # Format: "clients/{uid}/bo_clients/{bo_client_id}/mandates/{mandate_id}"
    parts = mandate_path.split("/")
    firebase_parent_id = parts[3] if len(parts) >= 4 else None
    firebase_mandate_id = parts[5] if len(parts) >= 6 else None

    # Cluster code pour HR (deduit du pays + region)
    cluster_code = f"{country_code}-{region_code}" if region_code else None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # =================================================================
            # ETAPE 1: User (l'individu Firebase)
            # =================================================================
            user = await conn.fetchrow("""
                INSERT INTO core.users (firebase_uid, email, display_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (firebase_uid) DO UPDATE SET
                    last_login_at = NOW(),
                    email = COALESCE(NULLIF($2, ''), core.users.email),
                    display_name = COALESCE(NULLIF($3, ''), core.users.display_name)
                RETURNING id, email, display_name
            """, firebase_uid, email, display_name)
            user_id = user["id"]

            logger.info(f"[CONTEXT] User: {user_id} ({firebase_uid[:20]}...)")

            # =================================================================
            # ETAPE 2: Account (le compte proprietaire)
            # =================================================================
            account = await conn.fetchrow("""
                INSERT INTO core.accounts (firebase_uid, email, display_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (firebase_uid) DO UPDATE SET
                    updated_at = NOW()
                RETURNING id, (xmax = 0) as is_new
            """, firebase_uid, email, display_name)
            account_id = account["id"]
            is_new_account = account["is_new"]

            logger.info(f"[CONTEXT] Account: {account_id} (new={is_new_account})")

            # =================================================================
            # ETAPE 3: Account Access (relation user <-> account)
            # =================================================================
            # Si nouveau compte: owner + admin
            # Si compte existant: shared + user (sauf si deja owner)
            access = await conn.fetchrow("""
                INSERT INTO core.account_access (
                    user_id, account_id, access_type, user_profile
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, account_id) DO UPDATE SET
                    updated_at = NOW()
                RETURNING access_type, user_profile
            """,
                user_id,
                account_id,
                AccessType.OWNER.value if is_new_account else AccessType.SHARED.value,
                UserProfile.ADMIN.value if is_new_account else UserProfile.USER.value
            )

            logger.info(f"[CONTEXT] Access: {access['access_type']}/{access['user_profile']}")

            # =================================================================
            # ETAPE 4: Company (la societe/mandat)
            # =================================================================
            company = await conn.fetchrow("""
                INSERT INTO core.companies (
                    account_id, firebase_mandate_path, firebase_mandate_id,
                    firebase_parent_id, name, country, country_code,
                    region_code, base_currency, is_active
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, TRUE)
                ON CONFLICT (firebase_mandate_path) DO UPDATE SET
                    updated_at = NOW(),
                    name = COALESCE(NULLIF($5, ''), core.companies.name)
                RETURNING id, country_code, region_code, (xmax = 0) as is_new
            """,
                account_id,
                mandate_path,
                firebase_mandate_id,
                firebase_parent_id,
                company_name,
                country,
                country_code,
                region_code,
                base_currency
            )
            company_id = company["id"]
            is_new_company = company["is_new"]

            logger.info(f"[CONTEXT] Company: {company_id} ({company_name}, new={is_new_company})")

            # =================================================================
            # Construire et retourner le contexte
            # =================================================================
            context = AccountContext(
                user_id=user_id,
                account_id=account_id,
                company_id=company_id,
                firebase_uid=firebase_uid,
                mandate_path=mandate_path,
                access_type=AccessType(access["access_type"]),
                profile=UserProfile(access["user_profile"]),
                country_code=company["country_code"] or country_code,
                email=user["email"],
                display_name=user["display_name"],
                cluster_code=cluster_code,
            )

            # =================================================================
            # ETAPE 5: Trigger module initialization si nouvelle company
            # =================================================================
            if is_new_company:
                await _initialize_modules_for_company(conn, context)

            return context


async def _initialize_modules_for_company(conn, context: AccountContext) -> Dict[str, Any]:
    """
    Initialise les modules pour une nouvelle societe.

    Cette fonction est appelee automatiquement lors de la creation d'une societe.
    Elle delegue au ModuleRegistry si disponible, sinon fait une init basique.
    """
    results = {}

    try:
        from .module_registry import ModuleRegistry
        results = await ModuleRegistry.on_company_created(conn, context)
        logger.info(f"[CONTEXT] Modules initialized: {list(results.keys())}")
    except ImportError:
        # ModuleRegistry pas encore implemente - init basique
        logger.info("[CONTEXT] ModuleRegistry not available, skipping module init")
    except Exception as e:
        logger.error(f"[CONTEXT] Module initialization error: {e}")
        results["error"] = str(e)

    return results


async def get_account_context(
    firebase_uid: str,
    company_id: UUID,
) -> Optional[AccountContext]:
    """
    Recupere un contexte existant pour un utilisateur et une societe.

    Contrairement a ensure_account_context(), cette fonction ne cree rien.
    Elle retourne None si l'utilisateur n'a pas acces a la societe.

    Args:
        firebase_uid: UID Firebase de l'utilisateur
        company_id: UUID de la societe

    Returns:
        AccountContext ou None si pas d'acces
    """
    from .neon_hr_manager import get_neon_hr_manager

    manager = get_neon_hr_manager()
    pool = await manager.get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                u.id as user_id,
                u.email,
                u.display_name,
                a.id as account_id,
                c.id as company_id,
                c.firebase_mandate_path,
                c.country_code,
                c.region_code,
                aa.access_type,
                aa.user_profile
            FROM core.users u
            JOIN core.account_access aa ON aa.user_id = u.id
            JOIN core.accounts a ON a.id = aa.account_id
            JOIN core.companies c ON c.account_id = a.id
            WHERE u.firebase_uid = $1
            AND c.id = $2
            AND c.is_active = TRUE
            AND aa.is_active = TRUE
        """, firebase_uid, company_id)

        if not row:
            return None

        cluster_code = None
        if row["region_code"] and row["country_code"]:
            cluster_code = f"{row['country_code']}-{row['region_code']}"

        return AccountContext(
            user_id=row["user_id"],
            account_id=row["account_id"],
            company_id=row["company_id"],
            firebase_uid=firebase_uid,
            mandate_path=row["firebase_mandate_path"],
            access_type=AccessType(row["access_type"]),
            profile=UserProfile(row["user_profile"]),
            country_code=row["country_code"],
            email=row["email"],
            display_name=row["display_name"],
            cluster_code=cluster_code,
        )


async def list_accessible_companies(firebase_uid: str) -> List[Dict[str, Any]]:
    """
    Liste toutes les societes accessibles par un utilisateur.

    Args:
        firebase_uid: UID Firebase de l'utilisateur

    Returns:
        Liste des societes avec leurs informations
    """
    from .neon_hr_manager import get_neon_hr_manager

    manager = get_neon_hr_manager()
    pool = await manager.get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                c.id as company_id,
                c.name,
                c.country,
                c.country_code,
                c.region_code,
                c.firebase_mandate_path,
                aa.access_type,
                aa.user_profile
            FROM core.users u
            JOIN core.account_access aa ON aa.user_id = u.id
            JOIN core.accounts a ON a.id = aa.account_id
            JOIN core.companies c ON c.account_id = a.id
            WHERE u.firebase_uid = $1
            AND c.is_active = TRUE
            AND aa.is_active = TRUE
            ORDER BY c.name
        """, firebase_uid)

        return [
            {
                "company_id": str(row["company_id"]),
                "name": row["name"],
                "country": row["country"],
                "country_code": row["country_code"],
                "region_code": row["region_code"],
                "mandate_path": row["firebase_mandate_path"],
                "access_type": row["access_type"],
                "profile": row["user_profile"],
            }
            for row in rows
        ]
