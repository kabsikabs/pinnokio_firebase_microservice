# -*- coding: utf-8 -*-
"""
Module Registry - Systeme d'initialisation extensible des modules.

Ce module fournit:
1. ModuleInitializer: Interface pour les modules
2. ModuleRegistry: Registre central et orchestration

Architecture:
    ModuleRegistry.on_company_created(conn, context)
        |
        +-> Resolve dependencies order
        |
        +-> For each module:
            +-> AccountingModule.initialize()  (si enregistre)
            +-> HRModule.initialize()          (si enregistre)
            +-> CRMModule.initialize()         (si enregistre)
            +-> ...

Usage:
    # Enregistrer un module
    from app.tools.module_registry import ModuleRegistry, ModuleInitializer

    class MyModuleInitializer(ModuleInitializer):
        @property
        def module_name(self) -> str:
            return "my_module"

        async def initialize_for_company(self, conn, context):
            # Init logic...
            return {"created": 10}

    ModuleRegistry.register(MyModuleInitializer())

    # L'initialisation est automatique lors de ensure_account_context()
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Any, Type
from uuid import UUID

logger = logging.getLogger("module.registry")


# =============================================================================
# MODULE INITIALIZER INTERFACE
# =============================================================================

class ModuleInitializer(ABC):
    """
    Interface pour l'initialisation des modules.

    Chaque module (HR, Accounting, CRM, etc.) implemente cette interface
    pour definir comment il s'initialise pour une nouvelle societe.

    Example:
        class HRModuleInitializer(ModuleInitializer):
            @property
            def module_name(self) -> str:
                return "hr"

            @property
            def dependencies(self) -> List[str]:
                return ["accounting"]  # HR depend du plan comptable

            async def initialize_for_company(self, conn, context):
                # Creer les settings HR par defaut
                # Copier les rubriques de paie
                return {"settings_created": 5, "items_created": 20}
    """

    @property
    @abstractmethod
    def module_name(self) -> str:
        """Nom unique du module (hr, accounting, crm, etc.)"""
        pass

    @property
    def dependencies(self) -> List[str]:
        """Liste des modules qui doivent etre initialises avant celui-ci."""
        return []

    @property
    def is_enabled_by_default(self) -> bool:
        """Le module est-il active par defaut pour les nouvelles societes?"""
        return True

    @abstractmethod
    async def initialize_for_company(
        self,
        conn,  # asyncpg.Connection
        context,  # AccountContext
    ) -> Dict[str, Any]:
        """
        Initialise le module pour une nouvelle societe.

        Args:
            conn: Connection PostgreSQL (dans une transaction)
            context: Contexte complet de l'utilisateur/societe

        Returns:
            Dict avec les informations creees (pour logging/audit)
        """
        pass

    async def on_company_deleted(
        self,
        conn,
        company_id: UUID,
    ) -> None:
        """Hook appele lors de la suppression d'une societe (optionnel)."""
        pass


# =============================================================================
# MODULE REGISTRY
# =============================================================================

class ModuleRegistry:
    """
    Registre central des modules.

    Gere l'enregistrement et l'initialisation ordonnee des modules.
    Pattern Singleton implicite via class methods.
    """

    _modules: Dict[str, ModuleInitializer] = {}

    @classmethod
    def register(cls, module: ModuleInitializer) -> None:
        """
        Enregistre un module dans le registre.

        Args:
            module: Instance de ModuleInitializer
        """
        cls._modules[module.module_name] = module
        logger.info(f"[REGISTRY] Module registered: {module.module_name}")

    @classmethod
    def unregister(cls, module_name: str) -> None:
        """Retire un module du registre."""
        if module_name in cls._modules:
            del cls._modules[module_name]
            logger.info(f"[REGISTRY] Module unregistered: {module_name}")

    @classmethod
    def get(cls, name: str) -> Optional[ModuleInitializer]:
        """Recupere un module par son nom."""
        return cls._modules.get(name)

    @classmethod
    def list_modules(cls) -> List[str]:
        """Liste tous les modules enregistres."""
        return list(cls._modules.keys())

    @classmethod
    def _resolve_order(cls) -> List[ModuleInitializer]:
        """
        Resout l'ordre d'initialisation selon les dependances.

        Utilise un tri topologique pour respecter les dependances.
        """
        resolved = []
        seen = set()

        def visit(module: ModuleInitializer):
            if module.module_name in seen:
                return
            seen.add(module.module_name)

            # D'abord visiter les dependances
            for dep_name in module.dependencies:
                if dep := cls._modules.get(dep_name):
                    visit(dep)
                else:
                    logger.warning(
                        f"[REGISTRY] Module {module.module_name} depends on "
                        f"{dep_name} which is not registered"
                    )

            resolved.append(module)

        for module in cls._modules.values():
            visit(module)

        return resolved

    @classmethod
    async def on_company_created(
        cls,
        conn,  # asyncpg.Connection
        context,  # AccountContext
    ) -> Dict[str, Any]:
        """
        Appele automatiquement lors de la creation d'une societe.
        Initialise tous les modules enregistres dans l'ordre des dependances.

        Args:
            conn: Connection PostgreSQL (dans une transaction)
            context: Contexte complet de l'utilisateur/societe

        Returns:
            Dict avec les resultats de chaque module
        """
        results = {}

        if not cls._modules:
            logger.info("[REGISTRY] No modules registered, skipping initialization")
            return results

        for module in cls._resolve_order():
            if not module.is_enabled_by_default:
                logger.info(f"[REGISTRY] Skipping {module.module_name} (not enabled by default)")
                continue

            try:
                logger.info(f"[REGISTRY] Initializing module: {module.module_name}")
                result = await module.initialize_for_company(conn, context)

                # Marquer comme initialise dans company_settings
                await cls._mark_initialized(conn, context.company_id, module.module_name)

                results[module.module_name] = {
                    "status": "success",
                    "data": result
                }
                logger.info(f"[REGISTRY] Module {module.module_name} initialized: {result}")

            except Exception as e:
                results[module.module_name] = {
                    "status": "error",
                    "error": str(e)
                }
                logger.error(f"[REGISTRY] Module {module.module_name} failed: {e}")
                # Continue avec les autres modules

        return results

    @classmethod
    async def initialize_modules(
        cls,
        context,  # AccountContext
        modules: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Initialise des modules specifiques (pour init manuelle/lazy).

        Utile pour activer des modules apres la creation initiale.

        Args:
            context: Contexte utilisateur/societe
            modules: Liste des modules a initialiser (tous si None)

        Returns:
            Dict avec les resultats de chaque module
        """
        from .neon_hr_manager import get_neon_hr_manager

        manager = get_neon_hr_manager()
        pool = await manager.get_pool()
        results = {}

        async with pool.acquire() as conn:
            async with conn.transaction():
                ordered = cls._resolve_order()

                for module in ordered:
                    if modules and module.module_name not in modules:
                        continue

                    # Verifier si deja initialise
                    is_init = await cls._is_initialized(
                        conn, context.company_id, module.module_name
                    )

                    if is_init:
                        results[module.module_name] = {"status": "already_initialized"}
                        continue

                    try:
                        result = await module.initialize_for_company(conn, context)
                        await cls._mark_initialized(conn, context.company_id, module.module_name)
                        results[module.module_name] = {"status": "success", "data": result}
                    except Exception as e:
                        results[module.module_name] = {"status": "error", "error": str(e)}

        return results

    @classmethod
    async def _mark_initialized(cls, conn, company_id: UUID, module_name: str) -> None:
        """Marque un module comme initialise pour une societe."""
        await conn.execute("""
            INSERT INTO core.company_settings (company_id, setting_key, setting_value)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (company_id, setting_key) DO UPDATE SET
                setting_value = $3::jsonb,
                updated_at = NOW()
        """,
            company_id,
            f"{module_name}.initialized",
            json.dumps({
                "initialized": True,
                "timestamp": datetime.now().isoformat()
            })
        )

    @classmethod
    async def _is_initialized(cls, conn, company_id: UUID, module_name: str) -> bool:
        """Verifie si un module est deja initialise pour une societe."""
        result = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM core.company_settings
                WHERE company_id = $1
                AND setting_key = $2
            )
        """, company_id, f"{module_name}.initialized")
        return result


# =============================================================================
# BUILT-IN MODULE INITIALIZERS
# =============================================================================

class HRModuleInitializer(ModuleInitializer):
    """
    Initialiseur du module HR.

    Cree:
    - Settings HR par defaut
    - Copie les rubriques de paie du catalogue
    - Copie les types de contrat pour le pays
    """

    @property
    def module_name(self) -> str:
        return "hr"

    @property
    def dependencies(self) -> List[str]:
        return []  # Pas de dependances pour l'instant

    async def initialize_for_company(self, conn, context) -> Dict[str, Any]:
        """Initialise le module HR pour une nouvelle societe."""
        results = {}

        # 1. Settings HR par defaut
        country_code = context.country_code or "CH"
        default_settings = {
            "hr.enabled": True,
            "hr.cluster_code": context.cluster_code,
            "hr.default_weekly_hours": 42.0 if country_code == "CH" else 35.0,
            "hr.default_annual_leave": 25 if country_code == "CH" else 30,
            "hr.thirteenth_month": country_code == "CH",
        }

        settings_created = 0
        for key, value in default_settings.items():
            await conn.execute("""
                INSERT INTO core.company_settings (company_id, setting_key, setting_value)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (company_id, setting_key) DO NOTHING
            """, context.company_id, key, json.dumps(value))
            settings_created += 1

        results["settings_created"] = settings_created

        # 2. Copier les rubriques de paie depuis le catalogue
        try:
            if context.cluster_code:
                items = await conn.fetch("""
                    INSERT INTO hr.company_payroll_items (company_id, catalog_item_id, is_enabled)
                    SELECT $1, id, is_mandatory
                    FROM hr.payroll_items_catalog
                    WHERE country_code = $2 AND is_active = TRUE
                      AND (cluster_code IS NULL OR cluster_code = $3)
                      AND effective_from <= CURRENT_DATE
                      AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
                    ON CONFLICT (company_id, catalog_item_id) DO NOTHING
                    RETURNING catalog_item_id
                """, context.company_id, country_code, context.cluster_code)
                results["payroll_items_created"] = len(items)
        except Exception as e:
            logger.warning(f"[HR] Could not copy payroll items: {e}")
            results["payroll_items_created"] = 0

        # 3. Contract types: no company-level copy needed.
        # ref_contract_types is read directly (scoped by country_code).
        # company_contract_types table has been dropped (migration 017).

        return results


# =============================================================================
# AUTO-REGISTRATION
# =============================================================================

# Enregistrer les modules built-in
ModuleRegistry.register(HRModuleInitializer())

logger.info("[REGISTRY] Module registry initialized with built-in modules")
