"""
COA (Chart of Accounts) Page RPC Handlers
==========================================

NAMESPACE: COA

Architecture:
    Frontend (Next.js) -> WSS -> coa.* events
                       -> Backend handlers
                       -> Redis Cache Niveau 2 (HIT) | Firebase/ERP (MISS)

CACHE NIVEAU 2:
    Le COA est traité comme donnée critique de niveau entreprise.
    Clé: company:{uid}:{company_id}:coa
    TTL: 1 heure (comme company:context)

    Le cache niveau 2 est:
    - Pré-chargé pendant dashboard orchestration
    - Lu en priorité lors de l'accès à la page COA
    - Invalidé lors des modifications (save, toggle, create, update, delete)

Endpoints disponibles:
    - COA.load_accounts          -> Charge les comptes COA
    - COA.load_functions         -> Charge les fonctions KLK
    - COA.save_changes           -> Sauvegarde modifications vers ERP
    - COA.sync_from_erp          -> Synchronise depuis ERP
    - COA.toggle_function        -> Active/desactive fonction
    - COA.create_function        -> Cree fonction custom
    - COA.update_function        -> Met a jour fonction custom
    - COA.delete_function        -> Supprime fonction custom

IMPORTANT: Ce module utilise les singletons existants:
    - get_firebase_management() pour Firebase
    - get_firebase_cache_manager() pour Redis
    - ERP RPC pour operations ERP
"""

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.cache.unified_cache_manager import get_firebase_cache_manager
from app.firebase_providers import get_firebase_management
from app.redis_client import get_redis
from app.llm_service.redis_namespaces import build_company_coa_key, RedisTTL

logger = logging.getLogger("coa.handlers")


# ===================================================================
# CONSTANTES TTL
# ===================================================================

TTL_COA_ACCOUNTS = 60       # 1 minute pour comptes COA
TTL_COA_FUNCTIONS = 300     # 5 minutes pour fonctions KLK
TTL_PAGE_STATE = 1800       # 30 minutes pour page state


# ===================================================================
# CONSTANTES NATURES
# ===================================================================

NATURE_DISPLAY_NAMES_FALLBACK = {
    "ASSET": "Assets",
    "LIABILITY": "Liabilities",
    "PROFIT_AND_LOSS": "Profit and Loss",
    "OFF_BALANCE_SHEET": "Off-Balance Sheet",
}

VALID_NATURES = ["ASSET", "LIABILITY", "PROFIT_AND_LOSS"]


# ===================================================================
# SINGLETON
# ===================================================================

_coa_handlers_instance: Optional["COAHandlers"] = None


def get_coa_handlers() -> "COAHandlers":
    """Singleton accessor pour les handlers COA."""
    global _coa_handlers_instance
    if _coa_handlers_instance is None:
        _coa_handlers_instance = COAHandlers()
    return _coa_handlers_instance


class COAHandlers:
    """
    Handlers RPC pour le namespace COA.

    Chaque methode correspond a un endpoint RPC:
    - COA.load_accounts -> load_accounts()
    - COA.load_functions -> load_functions()
    - COA.save_changes -> save_changes()
    - etc.

    Toutes les methodes sont asynchrones.
    """

    NAMESPACE = "COA"

    def __init__(self):
        self._cache = get_firebase_cache_manager()
        self._firebase = get_firebase_management()

    # ===================================================================
    # LOAD ACCOUNTS (Donnees COA)
    # ===================================================================

    async def load_accounts(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Charge les comptes du plan comptable depuis Firebase.

        RPC: COA.load_accounts

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            force_refresh: Forcer rechargement (bypass cache)

        Returns:
            {"success": True, "data": {"accounts": [...], "total": int}}
        """
        try:
            # 1. Tentative cache
            if not force_refresh:
                cached = await self._cache.get_cached_data(
                    uid, company_id, "coa", "accounts", ttl_seconds=TTL_COA_ACCOUNTS
                )
                if cached and "data" in cached:
                    logger.info(f"COA.load_accounts company_id={company_id} source=cache")
                    return {"success": True, "data": cached["data"], "from_cache": True}

            # 2. Fetch depuis Firebase
            logger.info(f"COA.load_accounts company_id={company_id} source=firebase")

            coa_path = f"{mandate_path}/setup/coa"
            raw_data = await asyncio.to_thread(
                self._firebase.get_document,
                coa_path
            )

            if not raw_data:
                return {"success": True, "data": {"accounts": [], "total": 0}}

            # 3. Transform data
            accounts = []
            for account_id, data in raw_data.items():
                if not isinstance(data, dict):
                    continue

                # Conversion isactive
                raw_is_active = data.get("isactive", True)
                if isinstance(raw_is_active, str):
                    is_active = raw_is_active.lower() in ["true", "1", "yes", "oui"]
                elif isinstance(raw_is_active, (bool, int)):
                    is_active = bool(raw_is_active)
                else:
                    is_active = True

                account = {
                    "account_id": account_id,
                    "account_number": data.get("account_number", ""),
                    "account_name": data.get("account_name", ""),
                    "account_nature": data.get("klk_account_nature", ""),
                    "account_function": data.get("klk_account_function", data.get("account_type", "")),
                    "isactive": is_active,
                }
                accounts.append(account)

            # 4. Sort accounts
            accounts = self._sort_accounts(accounts)

            result = {
                "accounts": accounts,
                "total": len(accounts),
            }

            # 5. Cache result
            await self._cache.set_cached_data(
                uid, company_id, "coa", "accounts", {"data": result}, ttl_seconds=TTL_COA_ACCOUNTS
            )

            return {"success": True, "data": result}

        except Exception as e:
            logger.error(f"COA.load_accounts error: {e}")
            return {
                "success": False,
                "error": {"code": "COA_LOAD_ERROR", "message": str(e)}
            }

    def _sort_accounts(self, accounts: List[Dict]) -> List[Dict]:
        """Trie les comptes par numero (numerique si possible)."""
        def sort_key(acc):
            try:
                return (0, int(acc.get("account_number", "0")), not acc.get("isactive", True))
            except (ValueError, TypeError):
                return (1, acc.get("account_number", ""), not acc.get("isactive", True))

        return sorted(accounts, key=sort_key)

    # ===================================================================
    # LOAD FUNCTIONS (KLK Functions)
    # ===================================================================

    async def load_functions(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Charge les fonctions KLK depuis le mandat ou seed.

        RPC: COA.load_functions

        Si le document mandat n'existe pas, copie depuis seed.

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            force_refresh: Forcer rechargement

        Returns:
            {"success": True, "data": {"functions": [...], "nature_display_names": {...}}}
        """
        try:
            # 1. Tentative cache
            if not force_refresh:
                cached = await self._cache.get_cached_data(
                    uid, company_id, "coa", "functions", ttl_seconds=TTL_COA_FUNCTIONS
                )
                if cached and "data" in cached:
                    logger.info(f"COA.load_functions company_id={company_id} source=cache")
                    return {"success": True, "data": cached["data"], "from_cache": True}

            # 2. Fetch depuis mandat
            logger.info(f"COA.load_functions company_id={company_id} source=firebase")

            doc_path = f"{mandate_path}/setup/klk_function_name_definition"
            doc = await asyncio.to_thread(
                self._firebase.get_document,
                doc_path
            )

            # 3. Si document existe et valide
            if doc and doc.get("natures"):
                functions, nature_names = self._extract_functions_and_natures(doc)
                if functions:
                    result = {
                        "functions": functions,
                        "nature_display_names": nature_names,
                    }
                    await self._cache.set_cached_data(
                        uid, company_id, "coa", "functions", {"data": result}, ttl_seconds=TTL_COA_FUNCTIONS
                    )
                    return {"success": True, "data": result}

            # 4. Sinon, copier depuis seed
            seed_path = "settings_param/coa_mapping_settings/coa_model/english"
            seed_doc = await asyncio.to_thread(
                self._firebase.get_document,
                seed_path
            )

            if not seed_doc:
                return {
                    "success": False,
                    "error": {"code": "SEED_NOT_FOUND", "message": f"Seed document not found: {seed_path}"}
                }

            seed_functions, seed_nature_names = self._extract_functions_and_natures(seed_doc)
            if not seed_functions:
                return {
                    "success": False,
                    "error": {"code": "NO_FUNCTIONS", "message": "No functions found in seed"}
                }

            # 5. Copier structure minimale vers mandat
            minimal = self._build_minimal_natures_structure(seed_functions, seed_nature_names)
            await asyncio.to_thread(
                self._firebase.set_document,
                doc_path,
                minimal,
                False  # merge=False
            )

            result = {
                "functions": seed_functions,
                "nature_display_names": seed_nature_names,
            }
            await self._cache.set_cached_data(
                uid, company_id, "coa", "functions", {"data": result}, ttl_seconds=TTL_COA_FUNCTIONS
            )

            return {"success": True, "data": result, "from_seed": True}

        except Exception as e:
            logger.error(f"COA.load_functions error: {e}")
            return {
                "success": False,
                "error": {"code": "FUNCTIONS_LOAD_ERROR", "message": str(e)}
            }

    def _extract_functions_and_natures(self, doc: Dict) -> tuple:
        """
        Extrait les fonctions et nature_display_names d'un document.

        Format: {"natures": {"ASSET": {"nature_display_name": "...", "functions": [...]}, ...}}

        Returns:
            (functions_list, nature_display_names_dict)
        """
        if not doc:
            return [], {}

        nature_display_names = {}
        functions_list = []

        if "natures" not in doc or not isinstance(doc["natures"], dict):
            return [], {}

        for nature_key, nature_data in doc["natures"].items():
            if not isinstance(nature_data, dict):
                continue

            # Extraire nature_display_name
            nature_display = nature_data.get("nature_display_name", nature_key)
            nature_display_names[nature_key] = nature_display

            funcs = nature_data.get("functions", [])

            # Functions peuvent etre une liste ou un dict
            if isinstance(funcs, list):
                for func in funcs:
                    if not isinstance(func, dict):
                        continue
                    func_copy = dict(func)
                    func_copy["nature"] = nature_key
                    if "active" not in func_copy or func_copy.get("active") is None:
                        func_copy["active"] = True
                    # Ensure display_name is always set (fallback to name)
                    if "display_name" not in func_copy or not func_copy.get("display_name"):
                        func_copy["display_name"] = func_copy.get("name", "Unknown")
                    functions_list.append(func_copy)
            elif isinstance(funcs, dict):
                for func_name, func_data in funcs.items():
                    if not isinstance(func_data, dict):
                        continue
                    active_val = func_data.get("active")
                    if active_val is None:
                        active_val = True
                    functions_list.append({
                        "name": func_name,
                        "display_name": func_data.get("display_name", func_name),
                        "definition": func_data.get("definition", ""),
                        "nature": nature_key,
                        "mandatory": func_data.get("mandatory", False),
                        "active": active_val,
                    })

        return functions_list, nature_display_names

    def _build_minimal_natures_structure(
        self,
        functions: List[Dict],
        nature_names: Dict[str, str]
    ) -> Dict:
        """Reconstruit la structure natures depuis une liste plate."""
        natures_data = {}
        for nk in ["ASSET", "LIABILITY", "PROFIT_AND_LOSS", "OFF_BALANCE_SHEET"]:
            natures_data[nk] = {
                "nature_name": nk,
                "nature_display_name": nature_names.get(nk, NATURE_DISPLAY_NAMES_FALLBACK.get(nk, nk)),
                "functions": [],
            }

        for fn in (functions or []):
            if not isinstance(fn, dict):
                continue
            nk = (fn.get("nature") or "PROFIT_AND_LOSS").strip() or "PROFIT_AND_LOSS"
            if nk not in natures_data:
                nk = "PROFIT_AND_LOSS"
            fn_copy = {k: v for k, v in fn.items() if k != "nature"}
            if "active" not in fn_copy or fn_copy.get("active") is None:
                fn_copy["active"] = True
            natures_data[nk]["functions"].append(fn_copy)

        return {"natures": natures_data}

    # ===================================================================
    # SAVE CHANGES (Push to ERP)
    # ===================================================================

    async def save_changes(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        modified_rows: Dict[str, Dict],
        client_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sauvegarde les modifications vers l'ERP et Firebase.

        RPC: COA.save_changes

        Utilise la methode RPC ERP.update_coa_structure existante.

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            modified_rows: {account_id: {account_id, account_nature, new_function, ...}}
            client_uuid: Client UUID (optionnel)

        Returns:
            {"success": True, "message": "..."}
        """
        try:
            if not modified_rows:
                return {"success": True, "message": "No changes to save"}

            logger.info(f"COA.save_changes company_id={company_id} modified={len(modified_rows)}")

            # Appel RPC vers ERP.update_coa_structure
            from app.wrappers.erp_handlers import handle_update_coa_structure

            result = await handle_update_coa_structure(
                uid=uid,
                company_id=company_id,
                modified_rows=modified_rows,
                client_uuid=client_uuid,
            )

            if result.get("success"):
                # Invalider cache niveau 3 (legacy)
                cache_key = f"{uid}:{company_id}:coa:accounts"
                await self._cache.delete(cache_key)

                # Invalider cache niveau 2 (nouveau pattern)
                self._invalidate_level2_cache(uid, company_id)

                return {
                    "success": True,
                    "message": f"Saved {len(modified_rows)} modifications",
                    "modified_count": len(modified_rows),
                }
            else:
                return {
                    "success": False,
                    "error": {"code": "ERP_SAVE_ERROR", "message": result.get("message", "Unknown error")}
                }

        except Exception as e:
            logger.error(f"COA.save_changes error: {e}")
            return {
                "success": False,
                "error": {"code": "SAVE_ERROR", "message": str(e)}
            }

    # ===================================================================
    # SYNC FROM ERP (Pull from ERP)
    # ===================================================================

    async def sync_from_erp(
        self,
        uid: str,
        company_id: str,
        client_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Synchronise le plan comptable depuis l'ERP.

        RPC: COA.sync_from_erp

        Utilise la methode RPC ERP.sync_coa_from_erp existante.
        Cette methode:
        1. Recupere le plan comptable depuis ERP
        2. Applique le mapping de nature
        3. Enrichit les comptes de charges via Agent IA
        4. Sauvegarde dans Firebase

        Args:
            uid: Firebase user ID
            company_id: Company ID
            client_uuid: Client UUID (optionnel)

        Returns:
            {"success": True, "accounts_synced": int, ...}
        """
        try:
            logger.info(f"COA.sync_from_erp company_id={company_id}")

            # Appel RPC vers ERP.sync_coa_from_erp
            from app.wrappers.erp_handlers import handle_sync_coa_from_erp

            result = await handle_sync_coa_from_erp(
                uid=uid,
                company_id=company_id,
                client_uuid=client_uuid,
            )

            if result.get("success"):
                # Invalider cache niveau 3 (legacy)
                cache_key = f"{uid}:{company_id}:coa:accounts"
                await self._cache.delete(cache_key)

                # Invalider cache niveau 2 (nouveau pattern)
                self._invalidate_level2_cache(uid, company_id)

                return {
                    "success": True,
                    "accounts_synced": result.get("accounts_synced", 0),
                    "accounts_added": result.get("accounts_added", 0),
                    "accounts_updated": result.get("accounts_updated", 0),
                }
            else:
                return {
                    "success": False,
                    "error": {"code": "ERP_SYNC_ERROR", "message": result.get("message", "Unknown error")}
                }

        except Exception as e:
            logger.error(f"COA.sync_from_erp error: {e}")
            return {
                "success": False,
                "error": {"code": "SYNC_ERROR", "message": str(e)}
            }

    # ===================================================================
    # FUNCTION MANAGEMENT (KLK Functions CRUD)
    # ===================================================================

    async def toggle_function(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        function_name: str,
        active: bool,
    ) -> Dict[str, Any]:
        """
        Active/desactive une fonction KLK.

        RPC: COA.toggle_function

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            function_name: Nom technique de la fonction
            active: Nouveau statut actif

        Returns:
            {"success": True, "function": {...}}
        """
        try:
            if not function_name:
                return {"success": False, "error": {"code": "INVALID_NAME", "message": "Function name required"}}

            # Load current functions
            doc_path = f"{mandate_path}/setup/klk_function_name_definition"
            doc = await asyncio.to_thread(
                self._firebase.get_document,
                doc_path
            )

            if not doc or not doc.get("natures"):
                return {"success": False, "error": {"code": "NO_FUNCTIONS", "message": "Functions document not found"}}

            # Find and update function
            found = False
            for nature_key, nature_data in doc["natures"].items():
                if not isinstance(nature_data, dict):
                    continue
                funcs = nature_data.get("functions", [])
                if isinstance(funcs, list):
                    for func in funcs:
                        if isinstance(func, dict) and func.get("name") == function_name:
                            func["active"] = bool(active)
                            found = True
                            break
                if found:
                    break

            if not found:
                return {"success": False, "error": {"code": "NOT_FOUND", "message": f"Function {function_name} not found"}}

            # Save updated document
            await asyncio.to_thread(
                self._firebase.set_document,
                doc_path,
                doc,
                False  # merge=False
            )

            # Invalidate cache niveau 3 (legacy)
            cache_key = f"{uid}:{company_id}:coa:functions"
            await self._cache.delete(cache_key)

            # Invalider cache niveau 2 (nouveau pattern)
            self._invalidate_level2_cache(uid, company_id)

            logger.info(f"COA.toggle_function {function_name} -> {active}")

            return {
                "success": True,
                "function_name": function_name,
                "active": active,
            }

        except Exception as e:
            logger.error(f"COA.toggle_function error: {e}")
            return {
                "success": False,
                "error": {"code": "TOGGLE_ERROR", "message": str(e)}
            }

    async def create_function(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        display_name: str,
        nature: str,
        definition: str = "",
        active: bool = True,
    ) -> Dict[str, Any]:
        """
        Cree une nouvelle fonction custom.

        RPC: COA.create_function

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            display_name: Nom d'affichage
            nature: Nature (ASSET, LIABILITY, PROFIT_AND_LOSS)
            definition: Description (optionnel)
            active: Statut actif

        Returns:
            {"success": True, "function": {...}}
        """
        try:
            display_name = (display_name or "").strip()
            nature = (nature or "PROFIT_AND_LOSS").strip()

            if not display_name:
                return {"success": False, "error": {"code": "INVALID_NAME", "message": "Display name required"}}

            if nature not in VALID_NATURES:
                return {"success": False, "error": {"code": "INVALID_NATURE", "message": f"Invalid nature: {nature}"}}

            # Load current functions
            doc_path = f"{mandate_path}/setup/klk_function_name_definition"
            doc = await asyncio.to_thread(
                self._firebase.get_document,
                doc_path
            )

            if not doc or not doc.get("natures"):
                return {"success": False, "error": {"code": "NO_FUNCTIONS", "message": "Functions document not found"}}

            # Collect existing names
            existing_names = set()
            for nature_data in doc["natures"].values():
                if isinstance(nature_data, dict):
                    for func in nature_data.get("functions", []):
                        if isinstance(func, dict) and func.get("name"):
                            existing_names.add(func["name"])

            # Generate custom name
            new_name = self._slugify_to_custom_name(display_name, existing_names)

            # Create function
            new_function = {
                "name": new_name,
                "display_name": display_name,
                "definition": definition,
                "active": bool(active),
            }

            # Add to appropriate nature
            if nature not in doc["natures"]:
                doc["natures"][nature] = {
                    "nature_name": nature,
                    "nature_display_name": NATURE_DISPLAY_NAMES_FALLBACK.get(nature, nature),
                    "functions": [],
                }

            doc["natures"][nature]["functions"].append(new_function)

            # Save
            await asyncio.to_thread(
                self._firebase.set_document,
                doc_path,
                doc,
                False
            )

            # Invalidate cache niveau 3 (legacy)
            cache_key = f"{uid}:{company_id}:coa:functions"
            await self._cache.delete(cache_key)

            # Invalider cache niveau 2 (nouveau pattern)
            self._invalidate_level2_cache(uid, company_id)

            logger.info(f"COA.create_function {new_name} created")

            return {
                "success": True,
                "function": {**new_function, "nature": nature},
            }

        except Exception as e:
            logger.error(f"COA.create_function error: {e}")
            return {
                "success": False,
                "error": {"code": "CREATE_ERROR", "message": str(e)}
            }

    async def update_function(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        function_name: str,
        display_name: Optional[str] = None,
        definition: Optional[str] = None,
        nature: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Met a jour une fonction custom.

        RPC: COA.update_function

        Seules les fonctions custom (custom_*) peuvent etre editees.

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            function_name: Nom technique (doit commencer par custom_)
            display_name: Nouveau nom d'affichage (optionnel)
            definition: Nouvelle definition (optionnel)
            nature: Nouvelle nature (optionnel)
            active: Nouveau statut (optionnel)

        Returns:
            {"success": True, "function": {...}}
        """
        try:
            function_name = (function_name or "").strip()

            if not function_name:
                return {"success": False, "error": {"code": "INVALID_NAME", "message": "Function name required"}}

            if not function_name.startswith("custom_"):
                return {"success": False, "error": {"code": "NOT_CUSTOM", "message": "Only custom functions can be edited"}}

            if nature and nature not in VALID_NATURES:
                return {"success": False, "error": {"code": "INVALID_NATURE", "message": f"Invalid nature: {nature}"}}

            # Load current functions
            doc_path = f"{mandate_path}/setup/klk_function_name_definition"
            doc = await asyncio.to_thread(
                self._firebase.get_document,
                doc_path
            )

            if not doc or not doc.get("natures"):
                return {"success": False, "error": {"code": "NO_FUNCTIONS", "message": "Functions document not found"}}

            # Find and update function
            found = False
            old_nature = None
            updated_func = None

            for nature_key, nature_data in doc["natures"].items():
                if not isinstance(nature_data, dict):
                    continue
                funcs = nature_data.get("functions", [])
                if isinstance(funcs, list):
                    for i, func in enumerate(funcs):
                        if isinstance(func, dict) and func.get("name") == function_name:
                            if display_name is not None:
                                func["display_name"] = display_name
                            if definition is not None:
                                func["definition"] = definition
                            if active is not None:
                                func["active"] = bool(active)
                            old_nature = nature_key
                            updated_func = func
                            found = True

                            # If nature changed, move function
                            if nature and nature != nature_key:
                                funcs.pop(i)
                                if nature not in doc["natures"]:
                                    doc["natures"][nature] = {
                                        "nature_name": nature,
                                        "nature_display_name": NATURE_DISPLAY_NAMES_FALLBACK.get(nature, nature),
                                        "functions": [],
                                    }
                                doc["natures"][nature]["functions"].append(func)
                            break
                if found:
                    break

            if not found:
                return {"success": False, "error": {"code": "NOT_FOUND", "message": f"Function {function_name} not found"}}

            # Save
            await asyncio.to_thread(
                self._firebase.set_document,
                doc_path,
                doc,
                False
            )

            # Invalidate cache niveau 3 (legacy)
            cache_key = f"{uid}:{company_id}:coa:functions"
            await self._cache.delete(cache_key)

            # Invalider cache niveau 2 (nouveau pattern)
            self._invalidate_level2_cache(uid, company_id)

            logger.info(f"COA.update_function {function_name} updated")

            return {
                "success": True,
                "function": {**updated_func, "nature": nature or old_nature},
            }

        except Exception as e:
            logger.error(f"COA.update_function error: {e}")
            return {
                "success": False,
                "error": {"code": "UPDATE_ERROR", "message": str(e)}
            }

    async def delete_function(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        function_name: str,
    ) -> Dict[str, Any]:
        """
        Supprime une fonction custom.

        RPC: COA.delete_function

        Seules les fonctions custom (custom_*) peuvent etre supprimees.

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            function_name: Nom technique (doit commencer par custom_)

        Returns:
            {"success": True, "deleted": "function_name"}
        """
        try:
            function_name = (function_name or "").strip()

            if not function_name:
                return {"success": False, "error": {"code": "INVALID_NAME", "message": "Function name required"}}

            if not function_name.startswith("custom_"):
                return {"success": False, "error": {"code": "NOT_CUSTOM", "message": "Only custom functions can be deleted"}}

            # Load current functions
            doc_path = f"{mandate_path}/setup/klk_function_name_definition"
            doc = await asyncio.to_thread(
                self._firebase.get_document,
                doc_path
            )

            if not doc or not doc.get("natures"):
                return {"success": False, "error": {"code": "NO_FUNCTIONS", "message": "Functions document not found"}}

            # Find and delete function
            found = False
            for nature_key, nature_data in doc["natures"].items():
                if not isinstance(nature_data, dict):
                    continue
                funcs = nature_data.get("functions", [])
                if isinstance(funcs, list):
                    for i, func in enumerate(funcs):
                        if isinstance(func, dict) and func.get("name") == function_name:
                            funcs.pop(i)
                            found = True
                            break
                if found:
                    break

            if not found:
                return {"success": False, "error": {"code": "NOT_FOUND", "message": f"Function {function_name} not found"}}

            # Save
            await asyncio.to_thread(
                self._firebase.set_document,
                doc_path,
                doc,
                False
            )

            # Invalidate cache niveau 3 (legacy)
            cache_key = f"{uid}:{company_id}:coa:functions"
            await self._cache.delete(cache_key)

            # Invalider cache niveau 2 (nouveau pattern)
            self._invalidate_level2_cache(uid, company_id)

            logger.info(f"COA.delete_function {function_name} deleted")

            return {
                "success": True,
                "deleted": function_name,
            }

        except Exception as e:
            logger.error(f"COA.delete_function error: {e}")
            return {
                "success": False,
                "error": {"code": "DELETE_ERROR", "message": str(e)}
            }

    # ===================================================================
    # HELPERS
    # ===================================================================

    def _slugify_to_custom_name(self, display_name: str, existing_names: set) -> str:
        """Convertit un display_name en nom technique custom_*."""
        raw = (display_name or "").strip()
        if not raw:
            base = "custom_function"
        else:
            # Strip accents
            norm = unicodedata.normalize("NFKD", raw)
            norm = norm.encode("ascii", "ignore").decode("ascii")
            norm = norm.lower()
            norm = re.sub(r"[^a-z0-9]+", "_", norm)
            norm = re.sub(r"_+", "_", norm).strip("_")
            base = norm if norm else "custom_function"

        if not base.startswith("custom_"):
            base = f"custom_{base}"

        candidate = base
        i = 2
        while candidate in existing_names:
            candidate = f"{base}_{i}"
            i += 1

        return candidate

    # ===================================================================
    # CACHE NIVEAU 2 HELPERS
    # ===================================================================

    def _invalidate_level2_cache(self, uid: str, company_id: str) -> None:
        """
        Invalide le cache niveau 2 COA.

        Appelé après toute modification (save, toggle, create, update, delete).
        """
        try:
            redis_client = get_redis()
            cache_key = build_company_coa_key(uid, company_id)
            redis_client.delete(cache_key)
            logger.info(f"COA cache niveau 2 invalidated: {cache_key}")
        except Exception as e:
            logger.warning(f"COA cache niveau 2 invalidation failed: {e}")

    # ===================================================================
    # FULL DATA (Pour orchestration)
    # ===================================================================

    async def full_data(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Charge toutes les donnees COA en une seule requete.

        RPC: COA.full_data

        Architecture Cache Niveau 2:
        1. Si force_refresh=False, essayer cache niveau 2 (company:{uid}:{cid}:coa)
        2. Si cache HIT, retourner les données immédiatement
        3. Si cache MISS, charger depuis Firebase et cacher

        Args:
            uid: Firebase user ID
            company_id: Company ID
            mandate_path: Chemin mandat Firebase
            force_refresh: Forcer rechargement (bypass cache niveau 2)

        Returns:
            {"success": True, "data": {"accounts": [...], "functions": [...], ...}}
        """
        try:
            # 1. Essayer cache niveau 2 (sauf si force_refresh)
            if not force_refresh:
                try:
                    redis_client = get_redis()
                    cache_key = build_company_coa_key(uid, company_id)
                    cached = redis_client.get(cache_key)

                    if cached:
                        data = json.loads(cached if isinstance(cached, str) else cached.decode())
                        logger.info(
                            f"COA.full_data HIT cache niveau 2: "
                            f"{len(data.get('accounts', []))} accounts, "
                            f"{len(data.get('functions', []))} functions"
                        )
                        return {
                            "success": True,
                            "data": data,
                            "from_cache": True,
                            "cache_level": 2
                        }
                except Exception as cache_err:
                    logger.warning(f"COA.full_data cache niveau 2 read error: {cache_err}")

            # 2. Cache MISS - charger depuis Firebase
            logger.info(f"COA.full_data MISS cache niveau 2, loading from Firebase")

            # Fetch both in parallel
            accounts_result, functions_result = await asyncio.gather(
                self.load_accounts(uid, company_id, mandate_path, force_refresh),
                self.load_functions(uid, company_id, mandate_path, force_refresh),
                return_exceptions=True
            )

            # Handle exceptions
            if isinstance(accounts_result, Exception):
                logger.error(f"COA.full_data accounts error: {accounts_result}")
                accounts_data = {"accounts": [], "total": 0}
            else:
                accounts_data = accounts_result.get("data", {"accounts": [], "total": 0})

            if isinstance(functions_result, Exception):
                logger.error(f"COA.full_data functions error: {functions_result}")
                functions_data = {"functions": [], "nature_display_names": NATURE_DISPLAY_NAMES_FALLBACK}
            else:
                functions_data = functions_result.get("data", {"functions": [], "nature_display_names": NATURE_DISPLAY_NAMES_FALLBACK})

            result_data = {
                "accounts": accounts_data.get("accounts", []),
                "total_accounts": accounts_data.get("total", 0),
                "functions": functions_data.get("functions", []),
                "nature_display_names": functions_data.get("nature_display_names", NATURE_DISPLAY_NAMES_FALLBACK),
                "company_id": company_id,
                "mandate_path": mandate_path,
            }

            # 3. Cacher en niveau 2 pour les prochains accès
            try:
                redis_client = get_redis()
                cache_key = build_company_coa_key(uid, company_id)
                cache_data = {
                    **result_data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "source": "page_load"
                }
                redis_client.setex(
                    cache_key,
                    RedisTTL.COMPANY_CONTEXT,  # 1 heure
                    json.dumps(cache_data)
                )
                logger.info(f"COA.full_data cached to niveau 2: {cache_key}")
            except Exception as cache_err:
                logger.warning(f"COA.full_data cache niveau 2 write error: {cache_err}")

            return {
                "success": True,
                "data": result_data
            }

        except Exception as e:
            logger.error(f"COA.full_data error: {e}")
            return {
                "success": False,
                "error": {"code": "FULL_DATA_ERROR", "message": str(e)}
            }
