"""
Handlers RPC pour le module Company Settings (Next.js).

NAMESPACE: COMPANY_SETTINGS

Architecture:
    Frontend (Next.js) -> wsClient.send({type: "company_settings.*", ...})
                       -> WebSocket Hub
                       -> company_settings_handlers.*()
                       -> Redis Cache (HIT) | Firebase/Services (MISS)

Endpoints disponibles:
    - COMPANY_SETTINGS.full_data          -> Donnees completes page (TTL 300s)
    - COMPANY_SETTINGS.save_company_info  -> Sauvegarde infos entreprise
    - COMPANY_SETTINGS.save_settings      -> Sauvegarde DMS/Communication/Accounting
    - COMPANY_SETTINGS.save_workflow      -> Sauvegarde params workflow
    - COMPANY_SETTINGS.save_context       -> Sauvegarde contextes
    - COMPANY_SETTINGS.get_dropdowns      -> Donnees statiques (countries, etc.)

Note: user_id et company_id sont injectes automatiquement par le WebSocket context.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.cache.unified_cache_manager import get_firebase_cache_manager
from app.firebase_providers import get_firebase_management
from app.ws_events import WS_EVENTS

logger = logging.getLogger("company_settings.handlers")


# ============================================
# CONSTANTES TTL
# ============================================

TTL_FULL_DATA = 300         # 5 minutes pour donnees completes
TTL_DROPDOWNS = 86400       # 24 heures pour donnees statiques (countries, etc.)
TTL_COMPANY_INFO = 300      # 5 minutes pour infos entreprise


# ============================================
# SINGLETON
# ============================================

_company_settings_handlers_instance: Optional["CompanySettingsHandlers"] = None


def get_company_settings_handlers() -> "CompanySettingsHandlers":
    """Singleton accessor pour les handlers company settings."""
    global _company_settings_handlers_instance
    if _company_settings_handlers_instance is None:
        _company_settings_handlers_instance = CompanySettingsHandlers()
    return _company_settings_handlers_instance


class CompanySettingsHandlers:
    """
    Handlers RPC pour le namespace COMPANY_SETTINGS.

    Chaque methode correspond a un endpoint RPC:
    - COMPANY_SETTINGS.full_data -> full_data()
    - COMPANY_SETTINGS.save_company_info -> save_company_info()
    - etc.

    Toutes les methodes sont asynchrones.
    """

    NAMESPACE = "COMPANY_SETTINGS"

    def __init__(self):
        self._cache = get_firebase_cache_manager()
        self._firebase = get_firebase_management()

    # ============================================
    # HELPER METHODS
    # ============================================

    def _elapsed_ms(self, start: datetime) -> int:
        """Calculate elapsed time in milliseconds."""
        return int((datetime.utcnow() - start).total_seconds() * 1000)

    def _build_mandate_path(self, user_id: str, parent_doc_id: str, mandate_doc_id: str) -> str:
        """Build the Firebase mandate path."""
        return f"clients/{user_id}/bo_clients/{parent_doc_id}/mandates/{mandate_doc_id}"

    # ============================================
    # FULL DATA (Donnees completes)
    # ============================================

    async def full_data(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Recupere TOUTES les donnees de la page Company Settings.

        RPC: COMPANY_SETTINGS.full_data

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate
            force_refresh: Bypass cache

        Returns:
            {
                "success": True,
                "data": {
                    "companyInfo": {...},
                    "workflowParams": {...},
                    "contexts": {...},
                    "telegramUsers": [...],
                    "communicationRoomsConfig": {...},
                    "dropdowns": {...},
                    "meta": {...}
                }
            }
        """
        start_time = datetime.utcnow()
        cache_key = f"company_settings:full:{company_id}"

        try:
            # 1. Tentative cache (sauf force_refresh)
            if not force_refresh:
                try:
                    cached = await self._cache.get_cached_data(
                        user_id,
                        company_id,
                        "company_settings",
                        "full_data",
                        ttl_seconds=TTL_FULL_DATA
                    )
                    if cached and cached.get("data"):
                        cached_data = cached["data"]
                        cached_data["meta"] = {
                            **cached_data.get("meta", {}),
                            "cacheHit": True,
                            "durationMs": self._elapsed_ms(start_time),
                        }
                        logger.info(f"COMPANY_SETTINGS.full_data company_id={company_id} source=cache")
                        return {"success": True, "data": cached_data}
                except Exception as cache_err:
                    logger.warning(f"Cache read error: {cache_err}")

            # 2. Fetch toutes les donnees en parallele
            logger.info(f"COMPANY_SETTINGS.full_data company_id={company_id} source=firebase")

            results = await asyncio.gather(
                self._get_company_info(mandate_path),
                self._get_workflow_params(mandate_path),
                self._get_asset_config(mandate_path),
                self._get_contexts(mandate_path),
                self._get_telegram_users(user_id, mandate_path),
                self._get_communication_rooms_config(mandate_path),
                self._get_dropdowns(),
                self._get_erp_connections(mandate_path),
                return_exceptions=True
            )

            # 3. Extraire les resultats
            company_info = results[0] if not isinstance(results[0], Exception) else {}
            workflow_params = results[1] if not isinstance(results[1], Exception) else {}
            asset_config = results[2] if not isinstance(results[2], Exception) else {}
            contexts = results[3] if not isinstance(results[3], Exception) else {}
            telegram_users = results[4] if not isinstance(results[4], Exception) else []
            rooms_config = results[5] if not isinstance(results[5], Exception) else {}
            dropdowns = results[6] if not isinstance(results[6], Exception) else {}
            erp_connections = results[7] if not isinstance(results[7], Exception) else {}

            # Log errors
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"Error in parallel fetch {i}: {r}")

            # Merge asset config into workflow params for frontend convenience
            workflow_params.update(asset_config)

            # 4. Construire la reponse
            data = {
                "companyInfo": company_info,
                "workflowParams": workflow_params,
                "contexts": contexts,
                "telegramUsers": telegram_users,
                "communicationRoomsConfig": rooms_config,
                "dropdowns": dropdowns,
                "erpConnections": erp_connections,
                "meta": {
                    "cacheHit": False,
                    "durationMs": self._elapsed_ms(start_time),
                    "timestamp": datetime.utcnow().isoformat(),
                }
            }

            # 5. Sauvegarder dans le cache
            try:
                await self._cache.set_cached_data(
                    user_id,
                    company_id,
                    "company_settings",
                    "full_data",
                    data,
                    ttl_seconds=TTL_FULL_DATA
                )
            except Exception as cache_err:
                logger.warning(f"Cache write error: {cache_err}")

            return {"success": True, "data": data}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.full_data error: {e}")
            return {"success": False, "error": str(e)}

    # ============================================
    # ADDITIONAL DATA (Optimized - Telegram/ERP only)
    # ============================================

    async def additional_data(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
    ) -> Dict[str, Any]:
        """
        Fetch ONLY data not included in COMPANY.DETAILS broadcast.

        This is the NEW optimized method that loads only:
        - telegramUsers: List of authorized Telegram users
        - communicationRoomsConfig: Telegram room configuration
        - erpConnections: ERP connection configurations

        All other data (companyInfo, workflowParams, contexts, dropdowns)
        is now included in COMPANY.DETAILS from dashboard orchestration.

        RPC: COMPANY_SETTINGS.additional_data

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate

        Returns:
            {
                "success": True,
                "data": {
                    "telegramUsers": [...],
                    "communicationRoomsConfig": {...},
                    "erpConnections": {...},
                    "meta": {...}
                }
            }
        """
        start_time = datetime.utcnow()
        cache_key = f"company_settings:additional:{company_id}"

        try:
            # Check cache first
            try:
                cached = await self._cache.get_cached_data(
                    user_id,
                    company_id,
                    "company_settings",
                    "additional_data",
                    ttl_seconds=TTL_FULL_DATA
                )
                if cached and cached.get("data"):
                    cached_data = cached["data"]
                    cached_data["meta"] = {
                        **cached_data.get("meta", {}),
                        "cacheHit": True,
                        "durationMs": self._elapsed_ms(start_time),
                    }
                    logger.info(f"COMPANY_SETTINGS.additional_data company_id={company_id} source=cache")
                    return {"success": True, "data": cached_data}
            except Exception as cache_err:
                logger.warning(f"Cache read error: {cache_err}")

            # Fetch only unique data in parallel
            logger.info(f"COMPANY_SETTINGS.additional_data company_id={company_id} source=firebase")

            results = await asyncio.gather(
                self._get_telegram_users(user_id, mandate_path),
                self._get_communication_rooms_config(mandate_path),
                self._get_erp_connections(mandate_path),
                self._get_email_settings(mandate_path),
                return_exceptions=True
            )

            # Extract results
            telegram_users = results[0] if not isinstance(results[0], Exception) else []
            rooms_config = results[1] if not isinstance(results[1], Exception) else {}
            erp_connections = results[2] if not isinstance(results[2], Exception) else {}
            email_settings = results[3] if not isinstance(results[3], Exception) else None

            # Read email_type from mandate root (needed because company-store
            # does not persist settings to sessionStorage — on page refresh the
            # ADDITIONAL_DATA response is the only reliable source)
            email_type = None
            try:
                mandate_doc = self._firebase.get_raw_document(mandate_path)
                if mandate_doc:
                    email_type = mandate_doc.get("email_type")
            except Exception as mt_err:
                logger.warning(f"Error reading email_type from mandate: {mt_err}")

            # Log errors
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"Error in additional_data fetch {i}: {r}")

            # Build response
            data = {
                "telegramUsers": telegram_users,
                "communicationRoomsConfig": rooms_config,
                "erpConnections": erp_connections,
                "emailSettings": email_settings,
                "emailType": email_type,
                "meta": {
                    "cacheHit": False,
                    "durationMs": self._elapsed_ms(start_time),
                    "timestamp": datetime.utcnow().isoformat(),
                }
            }

            # Cache the result
            try:
                await self._cache.set_cached_data(
                    user_id,
                    company_id,
                    "company_settings",
                    "additional_data",
                    data,
                    ttl_seconds=TTL_FULL_DATA
                )
            except Exception as cache_err:
                logger.warning(f"Cache write error: {cache_err}")

            return {"success": True, "data": data}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.additional_data error: {e}")
            return {"success": False, "error": str(e)}

    # ============================================
    # DATA FETCHERS (Internal)
    # ============================================

    async def _get_company_info(self, mandate_path: str) -> Dict[str, Any]:
        """Fetch company information from Firebase."""
        try:
            data = self._firebase.get_document(mandate_path)
            if not data:
                return {}

            return {
                "legalName": data.get("legal_name", ""),
                "legalStatus": data.get("legal_status", ""),
                "phoneNumber": data.get("phone_number", ""),
                "website": data.get("website", ""),
                "address": data.get("address", ""),
                "country": data.get("country", ""),
                "baseCurrency": data.get("base_currency", ""),
                "email": data.get("email", ""),
                "hasVat": data.get("has_vat", False),
                "language": data.get("language", "English"),
                "dmsType": data.get("dms_type", ""),
                "chatType": data.get("chat_type", ""),
                "communicationMode": data.get("log_type", ""),
                "glType": data.get("gl_type", ""),
                "apType": data.get("ap_type", ""),
                "arType": data.get("ar_type", ""),
            }
        except Exception as e:
            logger.error(f"Error fetching company info: {e}")
            raise

    async def _get_workflow_params(self, mandate_path: str) -> Dict[str, Any]:
        """
        Fetch workflow parameters from Firebase.

        Path: {mandate_path}/setup/workflow_params

        Returns flat structure for direct frontend binding.
        """
        try:
            workflow_path = f"{mandate_path}/setup/workflow_params"
            data = self._firebase.get_raw_document(workflow_path) or {}

            # Extract sub-sections
            accounting_param = data.get("Accounting_param", {})
            router_param = data.get("Router_param", {})
            banker_param = data.get("Banker_param", {})
            apbookeeper_param = data.get("Apbookeeper_param", {})

            # Return FLAT structure for direct frontend binding
            return {
                # ─────────────────────────────────────────────────
                # Accounting Date Rules
                # ─────────────────────────────────────────────────
                "accountingDateAutomatedDefinition": accounting_param.get("accounting_date_definition", True),
                "accountingDateDefaultDate": accounting_param.get("accounting_date", ""),
                "accountingDateCustomMode": accounting_param.get("custom_mode", False),
                "accountingDateCustomPrompt": accounting_param.get("date_prompt", ""),

                # ─────────────────────────────────────────────────
                # Router Approval
                # ─────────────────────────────────────────────────
                "routerCommunicationMethod": router_param.get("router_communication_method", "telegram"),
                "routerApprovalRequired": router_param.get("router_approval_required", False),
                "routerAutomatedWorkflow": router_param.get("router_automated_workflow", False),
                "routerApprovalPendinglistEnabled": router_param.get("router_approval_pendinglist_enabled", False),
                "routerDepartments": router_param.get("departments", []),

                # ─────────────────────────────────────────────────
                # Banker Approval
                # ─────────────────────────────────────────────────
                "bankerCommunicationMethod": banker_param.get("banker_communication_method", "telegram"),
                "bankerApprovalRequired": banker_param.get("banker_approval_required", False),
                "bankerApprovalThresholdWorkflow": banker_param.get("banker_approval_thresholdworkflow", 0),
                "bankerGlApproval": banker_param.get("banker_gl_approval", False),
                "bankerVoucherApproval": banker_param.get("banker_voucher_approval", False),
                "bankerApprovalPendinglistEnabled": banker_param.get("banker_approval_pendinglist_enabled", False),

                # ─────────────────────────────────────────────────
                # APbookeeper Approval
                # ─────────────────────────────────────────────────
                "apbookeeperCommunicationMethod": apbookeeper_param.get("apbookeeper_communication_method", "telegram"),
                "apbookeeperApprovalRequired": apbookeeper_param.get("apbookeeper_approval_required", False),
                "apbookeeperApprovalContactCreation": apbookeeper_param.get("apbookeeper_approval_contact_creation", False),
                "apbookeeperTrustThresholdRequired": apbookeeper_param.get("trust_threshold_required", False),
                "apbookeeperTrustThresholdPercent": apbookeeper_param.get("trust_threshold_percent", 95),
                "apbookeeperApprovalPendinglistEnabled": apbookeeper_param.get("apbookeeper_approval_pendinglist_enabled", False),
                "apbookeeperAutomatedWorkflow": apbookeeper_param.get("apbookeeper_automated_workflow", False),
            }
        except Exception as e:
            logger.error(f"Error fetching workflow params: {e}")
            raise

    async def _get_asset_config(self, mandate_path: str) -> Dict[str, Any]:
        """
        Fetch asset management configuration from Firebase.

        Path: {mandate_path}/setup/asset_model
        """
        try:
            asset_path = f"{mandate_path}/setup/asset_model"
            data = self._firebase.get_raw_document(asset_path) or {}

            return {
                "assetManagementActivated": data.get("asset_management_activated", False),
                "assetAutomatedCreation": data.get("asset_automated_creation", True),
                "assetDefaultMethod": data.get("asset_default_method", "linear"),
                "assetDefaultMethodPeriod": data.get("asset_default_method_period", "12"),
            }
        except Exception as e:
            logger.error(f"Error fetching asset config: {e}")
            return {
                "assetManagementActivated": False,
                "assetAutomatedCreation": True,
                "assetDefaultMethod": "linear",
                "assetDefaultMethodPeriod": "12",
            }

    async def _get_contexts(self, mandate_path: str) -> Dict[str, Any]:
        """Fetch all business contexts from Firebase."""
        try:
            context_path = f"{mandate_path}/context"

            # Fetch general context
            general_doc = self._firebase.get_document(f"{context_path}/general_context")
            general_context = general_doc.get("context_company_profile_report", "") if general_doc else ""

            # Fetch accounting context
            accounting_doc = self._firebase.get_document(f"{context_path}/accounting_context")
            accounting_context = accounting_doc.get("accounting_context_report", "") if accounting_doc else ""

            # Fetch bank context
            bank_doc = self._firebase.get_document(f"{context_path}/bank_context")
            bank_context = bank_doc.get("bank_context_report", "") if bank_doc else ""

            # Fetch router contexts
            router_doc = self._firebase.get_document(f"{context_path}/router_context")
            router_prompt = router_doc.get("router_prompt", {}) if router_doc else {}

            return {
                "general": general_context,
                "accounting": accounting_context,
                "bank": bank_context,
                "routerInvoices": router_prompt.get("invoices", ""),
                "routerExpenses": router_prompt.get("expenses", ""),
                "routerBankCash": router_prompt.get("banks_cash", ""),
                "routerHr": router_prompt.get("hr", ""),
                "routerTaxes": router_prompt.get("taxes", ""),
                "routerLetters": router_prompt.get("letters", ""),
                "routerContrats": router_prompt.get("contrats", ""),
                "routerFinancialStatement": router_prompt.get("financial_statement", ""),
            }
        except Exception as e:
            logger.error(f"Error fetching contexts: {e}")
            raise

    async def _get_telegram_users(self, user_id: str, mandate_path: str) -> List[str]:
        """Fetch telegram authorized users."""
        try:
            users = self._firebase.get_telegram_users(user_id, mandate_path)
            return users if users else []
        except Exception as e:
            logger.error(f"Error fetching telegram users: {e}")
            return []

    async def _get_communication_rooms_config(self, mandate_path: str) -> Dict[str, Any]:
        """Fetch communication rooms configuration."""
        try:
            data = self._firebase.get_document(mandate_path)
            if not data:
                return {}

            rooms_mapping = data.get("telegram_users_mapping", {})
            room_assignments = data.get("telegram_room_assignments", {})
            telegram_auth_users = data.get("telegram_auth_users", [])

            # Build config for each room
            rooms = ["accountbookeeper_room", "router_room", "banker_room", "general_administration_room"]
            config = {}

            for room in rooms:
                # Normalize authorizedUsers to list (legacy string → list migration)
                raw_users = room_assignments.get(room, [])
                if isinstance(raw_users, str):
                    authorized = [raw_users] if raw_users else []
                else:
                    authorized = raw_users if raw_users else []

                config[room] = {
                    "roomId": rooms_mapping.get(room, ""),
                    "userIdentifier": authorized[0] if authorized else "",
                    "isConfigured": bool(rooms_mapping.get(room)),
                    "authorizedUsers": authorized,
                }

            config["telegramAuthUsers"] = telegram_auth_users
            return config
        except Exception as e:
            logger.error(f"Error fetching rooms config: {e}")
            return {}

    async def _get_dropdowns(self) -> Dict[str, Any]:
        """Fetch all dropdown data (countries, currencies, etc.)."""
        try:
            # Check cache first for dropdowns (long TTL)
            cached = await self._cache.get_cached_data(
                "system",
                "global",
                "company_settings",
                "dropdowns",
                ttl_seconds=TTL_DROPDOWNS
            )
            if cached and cached.get("data"):
                return cached["data"]

            # Fetch from Firebase in parallel
            results = await asyncio.gather(
                asyncio.to_thread(self._firebase.get_countries_list),
                asyncio.to_thread(self._firebase.get_all_currencies),
                asyncio.to_thread(self._firebase.download_all_languages),
                asyncio.to_thread(self._firebase.get_param_data, "dms"),
                asyncio.to_thread(self._firebase.get_param_data, "erp"),
                asyncio.to_thread(self._firebase.get_param_data, "chat"),
                asyncio.to_thread(self._firebase.get_param_data, "communication"),
                return_exceptions=True
            )

            # Process results
            countries_result = results[0] if not isinstance(results[0], Exception) else ([], {})
            currencies_result = results[1] if not isinstance(results[1], Exception) else []
            languages_result = results[2] if not isinstance(results[2], Exception) else {}
            dms_result = results[3] if not isinstance(results[3], Exception) else []
            erp_result = results[4] if not isinstance(results[4], Exception) else []
            chat_result = results[5] if not isinstance(results[5], Exception) else []
            communication_result = results[6] if not isinstance(results[6], Exception) else []

            # Extract countries
            countries, country_id_map = countries_result if isinstance(countries_result, tuple) else ([], {})

            # Extract currencies
            currencies = []
            currency_id_map = {}
            if currencies_result:
                for c in currencies_result:
                    code = c.get("currency_iso_code", "")
                    if code:
                        currencies.append(code)
                        currency_id_map[code] = c.get("id", 0)

            # Extract languages
            languages = sorted(languages_result.keys()) if languages_result else ["English", "French", "German"]

            # Build dropdowns
            dropdowns = {
                "countries": sorted(countries),
                "countryIdMap": country_id_map,
                "currencies": sorted(currencies),
                "currencyIdMap": currency_id_map,
                "languages": languages,
                "dmsTypes": dms_result or [],
                "erpTypes": erp_result or [],
                "chatTypes": chat_result or [],
                "communicationTypes": communication_result or [],
            }

            # Cache for 24h
            await self._cache.set_cached_data(
                "system",
                "global",
                "company_settings",
                "dropdowns",
                dropdowns,
                ttl_seconds=TTL_DROPDOWNS
            )

            return dropdowns
        except Exception as e:
            logger.error(f"Error fetching dropdowns: {e}")
            return {}

    async def _get_erp_connections(self, mandate_path: str) -> Dict[str, Any]:
        """
        Fetch ERP connection configurations.

        Loads from: {mandate_path}/erp/{erp_type}

        Returns dict keyed by erp_type (e.g., "odoo") with connection fields:
        - For Odoo: odoo_company_name, odoo_db, odoo_url, odoo_username, secret_manager
        """
        try:
            # 1. Load mandate to get active ERP types
            mandate_data = self._firebase.get_raw_document(mandate_path)
            if not mandate_data:
                logger.warning(f"No mandate found at {mandate_path}")
                return {}

            # 2. Get active ERP types (deduplicated)
            active_erps = set()
            for field in ["gl_accounting_erp", "ap_erp", "ar_erp"]:
                erp_type = mandate_data.get(field, "")
                if erp_type:
                    # Normalize to internal name (lowercase)
                    internal_name = erp_type.lower().strip()
                    active_erps.add(internal_name)

            if not active_erps:
                logger.info(f"No active ERPs configured for {mandate_path}")
                return {}

            logger.info(f"Loading ERP connections for types: {active_erps}")

            # 3. Load connection for each active ERP
            connections: Dict[str, Any] = {}
            for erp_type in active_erps:
                erp_doc_path = f"{mandate_path}/erp/{erp_type}"
                doc = self._firebase.get_raw_document(erp_doc_path)

                if doc:
                    # Build connection payload based on ERP type
                    connection = {
                        "erp_type": erp_type,
                        "secret_manager": doc.get("secret_manager", ""),
                    }

                    # Add Odoo-specific fields
                    if erp_type == "odoo":
                        connection.update({
                            "company_name": doc.get("odoo_company_name", ""),
                            "database": doc.get("odoo_db", ""),
                            "url": doc.get("odoo_url", ""),
                            "username": doc.get("odoo_username", ""),
                        })

                    connections[erp_type] = connection
                    logger.debug(f"Loaded ERP connection for {erp_type}")
                else:
                    # ERP configured but no connection document yet
                    connections[erp_type] = {
                        "erp_type": erp_type,
                        "secret_manager": "",
                        "company_name": "",
                        "database": "",
                        "url": "",
                        "username": "",
                    }
                    logger.debug(f"No connection document for {erp_type}, using empty defaults")

            return connections

        except Exception as e:
            logger.error(f"Error fetching ERP connections: {e}")
            return {}

    async def _get_email_settings(self, mandate_path: str) -> Optional[Dict[str, Any]]:
        """Fetch email governance settings from Firebase."""
        try:
            doc = self._firebase.get_raw_document(f"{mandate_path}/setup/email_settings")
            if not doc:
                return None
            return {
                "contact_groups": doc.get("contact_groups", []),
                "default_policy": doc.get("default_policy", {
                    "approval_required": False,
                    "semantic_tone": 5,
                    "custom_prompt": "",
                    "forbidden_terms": [],
                    "required_terms": [],
                }),
            }
        except Exception as e:
            logger.error(f"Error fetching email settings: {e}")
            return None

    # ============================================
    # SAVE METHODS
    # ============================================

    async def save_company_info(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save company basic information.

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate
            data: Company info to save

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            # Map frontend keys to Firebase keys
            firebase_data = {
                "legal_name": data.get("legalName"),
                "legal_status": data.get("legalStatus"),
                "phone_number": data.get("phoneNumber"),
                "website": data.get("website"),
                "address": data.get("address"),
                "country": data.get("country"),
                "base_currency": data.get("baseCurrency"),
                "email": data.get("email"),
                "has_vat": data.get("hasVat", False),
                "language": data.get("language"),
            }

            # Remove None values
            firebase_data = {k: v for k, v in firebase_data.items() if v is not None}

            # Save to Firebase
            self._firebase.set_document(mandate_path, firebase_data, merge=True)

            # Invalidate caches
            await self._invalidate_page_cache(user_id, company_id)

            logger.info(f"Company info saved for company_id={company_id}")
            return {"success": True}

        except Exception as e:
            logger.error(f"Error saving company info: {e}")
            return {"success": False, "error": str(e)}

    async def save_settings(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        section: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save company settings (DMS, Communication, or Accounting).

        Args:
            section: "dms", "communication", or "accounting"
            data: Settings to save
        """
        try:
            firebase_data = {}

            if section == "dms":
                firebase_data["dms_type"] = data.get("dmsType")

            elif section == "communication":
                firebase_data["log_type"] = data.get("communicationMode")
                firebase_data["chat_type"] = data.get("chatType")
                # Keep communication_chat_type in sync (used by Worker LLM pipeline + communication_dispatcher)
                firebase_data["communication_chat_type"] = data.get("chatType")

            elif section == "accounting":
                firebase_data["gl_type"] = data.get("glType")
                firebase_data["ap_type"] = data.get("apType")
                firebase_data["ar_type"] = data.get("arType")

            # Remove None values
            firebase_data = {k: v for k, v in firebase_data.items() if v is not None}

            # Save to Firebase
            self._firebase.set_document(mandate_path, firebase_data, merge=True)

            # Invalidate caches
            await self._invalidate_page_cache(user_id, company_id)

            logger.info(f"Settings ({section}) saved for company_id={company_id}")
            return {"success": True}

        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            return {"success": False, "error": str(e)}

    async def save_workflow(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        section: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save workflow parameters.

        Args:
            section: "router", "banker", "apbookeeper", or "accountingDate"
            data: Workflow params to save
        """
        try:
            workflow_path = f"{mandate_path}/setup/workflow_params"

            # Fetch existing params
            existing = self._firebase.get_raw_document(workflow_path) or {}

            if section == "router":
                router_param = existing.get("Router_param", {})
                router_param.update({
                    "router_communication_method": data.get("communicationMethod"),
                    "router_approval_required": data.get("approvalRequired"),
                    "router_automated_workflow": data.get("automatedWorkflow"),
                    "router_approval_pendinglist_enabled": data.get("approvalPendinglistEnabled"),
                    "departments": data.get("departments", []),
                    "trust_threshold_required": data.get("trustThresholdRequired"),
                    "trust_threshold_percent": data.get("trustThresholdPercent"),
                })
                self._firebase.set_document(workflow_path, {"Router_param": router_param}, merge=True)

                # Also save to mandate root
                self._firebase.set_document(mandate_path, {
                    "router_approval_pendinglist": data.get("approvalPendinglistEnabled", False)
                }, merge=True)

            elif section == "banker":
                banker_param = existing.get("Banker_param", {})
                banker_param.update({
                    "banker_communication_method": data.get("communicationMethod"),
                    "banker_approval_required": data.get("approvalRequired"),
                    "banker_approval_thresholdworkflow": data.get("approvalThreshold"),
                    "banker_gl_approval": data.get("glApproval"),
                    "banker_voucher_approval": data.get("voucherApproval"),
                    "banker_approval_pendinglist_enabled": data.get("approvalPendinglistEnabled"),
                })
                self._firebase.set_document(workflow_path, {"Banker_param": banker_param}, merge=True)

                # Also save to mandate root
                self._firebase.set_document(mandate_path, {
                    "banker_approval_pendinglist": data.get("approvalPendinglistEnabled", False)
                }, merge=True)

            elif section == "apbookeeper":
                apbookeeper_param = existing.get("Apbookeeper_param", {})
                apbookeeper_param.update({
                    "apbookeeper_communication_method": data.get("communicationMethod"),
                    "apbookeeper_approval_required": data.get("approvalRequired"),
                    "apbookeeper_approval_contact_creation": data.get("approvalContactCreation"),
                    "trust_threshold_required": data.get("trustThresholdRequired"),
                    "trust_threshold_percent": data.get("trustThresholdPercent"),
                    "apbookeeper_approval_pendinglist_enabled": data.get("approvalPendinglistEnabled"),
                    "apbookeeper_automated_workflow": data.get("automatedWorkflow"),
                })
                self._firebase.set_document(workflow_path, {"Apbookeeper_param": apbookeeper_param}, merge=True)

                # Also save to mandate root
                self._firebase.set_document(mandate_path, {
                    "apbookeeper_approval_pendinglist": data.get("approvalPendinglistEnabled", False)
                }, merge=True)

            elif section == "accountingDate":
                # Build Accounting_param with correct Firestore field names
                accounting_param = existing.get("Accounting_param", {})
                accounting_param.update({
                    "accounting_date_definition": data.get("automatedDefinition", True),
                    "accounting_date": data.get("defaultDate", ""),
                    "custom_mode": data.get("customMode", False),
                    "date_prompt": data.get("customPrompt", ""),
                })
                self._firebase.set_document(workflow_path, {"Accounting_param": accounting_param}, merge=True)

            # Invalidate caches
            await self._invalidate_page_cache(user_id, company_id)

            logger.info(f"Workflow ({section}) saved for company_id={company_id}")
            return {"success": True}

        except Exception as e:
            logger.error(f"Error saving workflow: {e}")
            return {"success": False, "error": str(e)}

    async def save_context(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        context_type: str,
        content: str,
    ) -> Dict[str, Any]:
        """
        Save a business context.

        Args:
            context_type: "general", "accounting", "bank", or "router_*"
            content: Context text content
        """
        try:
            context_path = f"{mandate_path}/context"

            if context_type == "general":
                self._firebase.save_context(
                    f"{context_path}/general_context",
                    {"context_company_profile_report": content}
                )

            elif context_type == "accounting":
                self._firebase.save_context(
                    f"{context_path}/accounting_context",
                    {"accounting_context_report": content}
                )

            elif context_type == "bank":
                self._firebase.save_context(
                    f"{context_path}/bank_context",
                    {"bank_context_report": content}
                )

            elif context_type.startswith("router_"):
                # Get existing router_prompt
                router_doc = self._firebase.get_document(f"{context_path}/router_context") or {}
                router_prompt = router_doc.get("router_prompt", {})

                # Map context_type to key
                key_map = {
                    "router_invoices": "invoices",
                    "router_expenses": "expenses",
                    "router_bank_cash": "banks_cash",
                    "router_hr": "hr",
                    "router_taxes": "taxes",
                    "router_letters": "letters",
                    "router_contrats": "contrats",
                    "router_financial_statement": "financial_statement",
                }

                key = key_map.get(context_type)
                if key:
                    router_prompt[key] = content
                    self._firebase.set_document(
                        f"{context_path}/router_context",
                        {"router_prompt": router_prompt},
                        merge=True
                    )

            # Invalidate caches
            await self._invalidate_page_cache(user_id, company_id)

            logger.info(f"Context ({context_type}) saved for company_id={company_id}")
            return {"success": True}

        except Exception as e:
            logger.error(f"Error saving context: {e}")
            return {"success": False, "error": str(e)}

    async def _invalidate_page_cache(self, user_id: str, company_id: str) -> None:
        """Invalidate the company settings page cache."""
        try:
            await self._cache.invalidate_cache(
                user_id,
                company_id,
                "company_settings",
                "full_data"
            )
            logger.info(f"Page cache invalidated for company_id={company_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate page cache: {e}")

    # ============================================
    # DMS OPERATIONS
    # ============================================

    def create_fiscal_folders(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        fiscal_year: int,
    ) -> Dict[str, Any]:
        """
        Create fiscal year folder structure in the DMS (Google Drive).

        Uses DMS_CREATION(command='create_folders') from onboarding_flow.py.
        This is a SYNCHRONOUS method (Drive API calls are blocking).
        The orchestration layer runs it via asyncio.to_thread().

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate
            fiscal_year: Year to create folders for (e.g., 2025)

        Returns:
            {"success": True, "message": "...", "folders_created": int}
        """
        start_time = datetime.utcnow()

        try:
            logger.info(
                f"COMPANY_SETTINGS.create_fiscal_folders "
                f"company_id={company_id} fiscal_year={fiscal_year}"
            )

            # Validate fiscal_year
            current_year = datetime.utcnow().year
            if not (2000 <= fiscal_year <= current_year + 5):
                return {
                    "success": False,
                    "error": f"Invalid fiscal year: {fiscal_year}. Must be between 2000 and {current_year + 5}."
                }

            # Get company data from Redis Level 2 cache (populated during auth/dashboard)
            from app.wrappers.dashboard_orchestration_handlers import get_company_context
            company_data = get_company_context(user_id, company_id)

            if not company_data:
                # Fallback: read raw mandate from Firebase
                logger.warning(
                    f"COMPANY_SETTINGS.create_fiscal_folders "
                    f"Level 2 cache MISS for uid={user_id} company={company_id}, falling back to Firebase"
                )
                company_data = self._firebase.get_document(mandate_path) or {}

            dms_type = company_data.get("dms_type", "odoo")
            if dms_type != "google_drive":
                return {
                    "success": False,
                    "error": f"Fiscal folder creation only supported for Google Drive DMS (current: {dms_type})"
                }

            # Extract required fields from Level 2 cache
            client_name = company_data.get("client_name", "")
            space_name = company_data.get("contact_space_name", "")
            client_uuid = company_data.get("client_uuid", "")
            communication_mode = company_data.get("communication_log_type", "pinnokio")

            if not client_name or not space_name:
                return {
                    "success": False,
                    "error": "Missing client_name or contact_space_name in mandate. Please configure company info first."
                }

            # Extract client_mandat_doc_id from mandate_path
            # Format: clients/{uid}/bo_clients/{client_doc_id}/mandates/{mandat_doc_id}
            path_parts = mandate_path.split("/")
            if len(path_parts) >= 6:
                client_mandat_doc_id = path_parts[5]
            else:
                return {
                    "success": False,
                    "error": f"Invalid mandate_path format: {mandate_path}"
                }

            # Call DMS_CREATION with command='create_folders'
            from pinnokio_app.logique_metier.onboarding_flow import DMS_CREATION

            logger.info(
                f"COMPANY_SETTINGS.create_fiscal_folders launching DMS_CREATION "
                f"client={client_name} space={space_name} year={fiscal_year}"
            )

            dms_creator = DMS_CREATION(
                dms_type=dms_type,
                command="create_folders",
                mandates_path=mandate_path,
                user_mail=None,
                command_args={
                    "client_name": client_name,
                    "space_name": space_name,
                    "specific_year": str(fiscal_year),
                    "communication_mode": communication_mode,
                },
                firebase_user_id=user_id,
                client_uuid=client_uuid,
                client_mandat_doc_id=client_mandat_doc_id,
            )

            # DMS_CREATION constructor executes create_folders synchronously
            # Result is stored in dms_creator.firebase_create_mandate_template
            template = getattr(dms_creator, "firebase_create_mandate_template", None)

            if template:
                logger.info(
                    f"COMPANY_SETTINGS.create_fiscal_folders SUCCESS "
                    f"company_id={company_id} fiscal_year={fiscal_year} "
                    f"template_keys={list(template.keys()) if isinstance(template, dict) else 'N/A'}"
                )
                return {
                    "success": True,
                    "folders_created": len(template) if isinstance(template, dict) else 0,
                    "message": f"Dossiers fiscaux {fiscal_year} créés avec succès.",
                    "durationMs": self._elapsed_ms(start_time),
                }
            else:
                return {
                    "success": False,
                    "error": "DMS_CREATION completed but no folder template was returned.",
                    "durationMs": self._elapsed_ms(start_time),
                }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.create_fiscal_folders error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    # ============================================
    # ERP OPERATIONS
    # ============================================

    async def save_erp_connection(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        erp_type: str,
        connection_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save ERP connection configuration.

        RPC: COMPANY_SETTINGS.save_erp_connections

        Path: {mandate_path}/erp/{erp_type}

        Handles API key storage via Google Secret Manager:
        - If new API key provided: delete old secret, create new one
        - Stores secret NAME in Firestore, never the actual key

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate
            erp_type: ERP type (odoo, banana, etc.)
            connection_data: Connection configuration data
                - companyName: Company name in ERP
                - database: Database name
                - url: ERP URL
                - username: Username
                - apiKey: New API key (optional, only if changing)

        Returns:
            {"success": True, "has_secret": bool} or {"success": False, "error": "..."}
        """
        try:
            from app.tools.g_cred import create_secret, delete_secret

            logger.info(
                f"COMPANY_SETTINGS.save_erp_connection "
                f"company_id={company_id} erp_type={erp_type}"
            )

            # Normalize ERP type
            erp_type = erp_type.lower().strip()

            # Path for this specific ERP connection
            erp_doc_path = f"{mandate_path}/erp/{erp_type}"

            # Get existing connection document
            existing_doc = self._firebase.get_raw_document(erp_doc_path) or {}
            old_secret_name = existing_doc.get("secret_manager", "")

            # Prepare data to save (Odoo-specific field mapping)
            data_to_save = {
                "erp_type": erp_type,
                "updated_at": datetime.utcnow().isoformat(),
            }

            if erp_type == "odoo":
                data_to_save.update({
                    "odoo_company_name": connection_data.get("companyName", ""),
                    "odoo_db": connection_data.get("database", ""),
                    "odoo_url": connection_data.get("url", ""),
                    "odoo_username": connection_data.get("username", ""),
                })

            # Handle API key - only process if a new key is provided
            new_api_key = (connection_data.get("apiKey") or "").strip()
            new_secret_name = old_secret_name  # Keep existing by default

            if new_api_key:
                logger.info(f"New API key provided for {erp_type}, managing secrets...")

                # 1. Delete old secret if exists
                if old_secret_name:
                    logger.info(f"Deleting old secret: {old_secret_name}")
                    try:
                        delete_secret(old_secret_name)
                        logger.info(f"Old secret deleted successfully")
                    except Exception as e:
                        # Don't fail if old secret doesn't exist
                        logger.warning(f"Could not delete old secret: {e}")

                # 2. Create new secret with the API key
                logger.info(f"Creating new secret for {erp_type}...")
                try:
                    new_secret_name = create_secret(new_api_key)
                    logger.info(f"New secret created: {new_secret_name}")
                except Exception as e:
                    logger.error(f"Failed to create secret: {e}")
                    return {"success": False, "error": f"Failed to store API key securely: {e}"}

            # Store the secret NAME (not the key itself!)
            data_to_save["secret_manager"] = new_secret_name

            # Save to Firebase
            self._firebase.set_document(erp_doc_path, data_to_save, merge=True)

            # Invalidate cache
            await self._invalidate_page_cache(user_id, company_id)

            logger.info(f"ERP connection ({erp_type}) saved for company_id={company_id}")
            return {
                "success": True,
                "has_secret": bool(new_secret_name),
            }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.save_erp_connection error: {e}")
            return {"success": False, "error": str(e)}

    async def test_erp_connection(
        self,
        user_id: str,
        company_id: str,
        erp_type: str,
        connection_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Test ERP connection.

        RPC: COMPANY_SETTINGS.test_erp_connection

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            erp_type: ERP type (odoo, banana, etc.)
            connection_data: Connection configuration data

        Returns:
            {
                "success": True,
                "connected": True/False,
                "message": "...",
                "details": {...}  # Optional additional info
            }
        """
        start_time = datetime.utcnow()

        try:
            logger.info(
                f"COMPANY_SETTINGS.test_erp_connection "
                f"company_id={company_id} erp_type={erp_type}"
            )

            if erp_type == "odoo":
                return await self._test_odoo_connection(connection_data, start_time)
            elif erp_type == "banana":
                return await self._test_banana_connection(connection_data, start_time)
            else:
                # Placeholder for other ERP types
                return {
                    "success": True,
                    "connected": False,
                    "message": f"Connection test not implemented for {erp_type}",
                    "durationMs": self._elapsed_ms(start_time)
                }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.test_erp_connection error: {e}")
            return {
                "success": False,
                "connected": False,
                "error": str(e)
            }

    async def _test_odoo_connection(
        self,
        connection_data: Dict[str, Any],
        start_time: datetime
    ) -> Dict[str, Any]:
        """Test Odoo connection using XML-RPC."""
        url = connection_data.get("url", "")
        database = connection_data.get("database", "")
        username = connection_data.get("username", "")
        api_key = connection_data.get("apiKey", "")

        if not all([url, database, username, api_key]):
            return {
                "success": True,
                "connected": False,
                "message": "Missing required connection parameters",
                "durationMs": self._elapsed_ms(start_time)
            }

        # TODO: Implement actual Odoo XML-RPC connection test
        # When ERP service singleton is configured, use:
        #
        # from app.erp_manager import ERPManager
        #
        # erp_manager = ERPManager()
        # result = await erp_manager.test_odoo_connection(
        #     url=url,
        #     database=database,
        #     username=username,
        #     api_key=api_key
        # )
        #
        # return {
        #     "success": True,
        #     "connected": result.get("connected", False),
        #     "message": result.get("message", ""),
        #     "details": result.get("details", {}),
        #     "durationMs": self._elapsed_ms(start_time)
        # }

        # PLACEHOLDER: Simulate connection test
        logger.warning(
            f"COMPANY_SETTINGS.test_erp_connection PLACEHOLDER - "
            f"ERP service singleton not yet configured"
        )

        return {
            "success": True,
            "connected": True,
            "message": "[PLACEHOLDER] Odoo connection test - Service integration pending",
            "details": {
                "url": url,
                "database": database,
                "username": username,
            },
            "durationMs": self._elapsed_ms(start_time)
        }

    async def _test_banana_connection(
        self,
        connection_data: Dict[str, Any],
        start_time: datetime
    ) -> Dict[str, Any]:
        """Test Banana connection."""
        # PLACEHOLDER: Implement Banana connection test when service is ready
        return {
            "success": True,
            "connected": False,
            "message": "[PLACEHOLDER] Banana connection test not implemented",
            "durationMs": self._elapsed_ms(start_time)
        }

    # ============================================
    # ASSET MANAGEMENT
    # ============================================

    async def list_asset_models(
        self,
        user_id: str,
        company_id: str,
        erp_type: str,
    ) -> Dict[str, Any]:
        """
        Fetch asset models from ERP.

        RPC: COMPANY_SETTINGS.list_asset_models

        Uses erp_type argument to allow future integration of multiple ERPs.
        Currently supports: odoo

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            erp_type: ERP type (odoo, banana, etc.)

        Returns:
            {
                "success": True,
                "models": [...],  # List of asset models
                "erp_type": "odoo"
            }
        """
        try:
            logger.info(
                f"COMPANY_SETTINGS.list_asset_models "
                f"company_id={company_id} erp_type={erp_type}"
            )

            erp_type = erp_type.lower().strip()

            if erp_type == "odoo":
                return await self._list_asset_models_odoo(user_id, company_id)
            else:
                return {
                    "success": False,
                    "error": f"Asset models not supported for ERP type: {erp_type}",
                    "models": [],
                    "erp_type": erp_type,
                }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.list_asset_models error: {e}")
            return {"success": False, "error": str(e), "models": []}

    async def _list_asset_models_odoo(
        self,
        user_id: str,
        company_id: str,
    ) -> Dict[str, Any]:
        """
        Fetch asset models from Odoo ERP.

        Returns models with structure:
        - id: int (Odoo model ID)
        - name: str (Model name)
        - method: str (linear/degressive)
        - method_period: int (1, 3, 6, 12)
        - method_number: int (number of periods)
        - account_asset_id: int
        - account_depreciation_id: int
        - account_depreciation_expense_id: int
        """
        try:
            from app.erp_service import ERPService

            models = ERPService.list_asset_models(
                user_id=user_id,
                company_id=company_id
            )

            logger.info(f"Fetched {len(models)} asset models from Odoo")

            return {
                "success": True,
                "models": models,
                "erp_type": "odoo",
            }

        except Exception as e:
            logger.error(f"Error fetching Odoo asset models: {e}")
            return {
                "success": False,
                "error": str(e),
                "models": [],
                "erp_type": "odoo",
            }

    async def save_asset_config(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save asset management configuration.

        RPC: COMPANY_SETTINGS.save_asset_config

        Path: {mandate_path}/setup/asset_model

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate
            data: Asset config data
                - activated: bool
                - automatedCreation: bool
                - defaultMethod: str (linear/degressive)
                - defaultPeriod: str (1, 3, 6, 12)

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            logger.info(f"COMPANY_SETTINGS.save_asset_config company_id={company_id}")

            asset_path = f"{mandate_path}/setup/asset_model"

            # Map frontend keys to Firebase keys
            firebase_data = {
                "asset_management_activated": data.get("activated", False),
                "asset_automated_creation": data.get("automatedCreation", True),
                "asset_default_method": data.get("defaultMethod", "linear"),
                "asset_default_method_period": data.get("defaultPeriod", "12"),
                "updated_at": datetime.utcnow().isoformat(),
            }

            # Save to Firebase
            self._firebase.set_document(asset_path, firebase_data, merge=True)

            # Invalidate cache
            await self._invalidate_page_cache(user_id, company_id)

            logger.info(f"Asset config saved for company_id={company_id}")
            return {"success": True}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.save_asset_config error: {e}")
            return {"success": False, "error": str(e)}

    async def create_asset_model(
        self,
        user_id: str,
        company_id: str,
        name: str,
        account_asset_id: int,
        account_depreciation_id: int,
        account_depreciation_expense_id: int,
        method: str,
        method_period: int,
        duration_years: int,
    ) -> Dict[str, Any]:
        """
        Create a new asset model in the ERP.

        RPC: COMPANY_SETTINGS.create_asset_model

        Creates both an asset journal and asset model in Odoo.

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            name: Model name
            account_asset_id: Asset account ID from COA
            account_depreciation_id: Depreciation account ID from COA
            account_depreciation_expense_id: Expense account ID from COA
            method: Depreciation method ('linear' or 'degressive')
            method_period: Period in months (1, 3, 6, 12)
            duration_years: Total duration in years

        Returns:
            {"success": True, "model": {...}} or {"success": False, "error": "..."}
        """
        try:
            logger.info(
                f"COMPANY_SETTINGS.create_asset_model "
                f"company_id={company_id} name={name}"
            )

            from app.erp_service import ERPService

            # Calculate method_number: (duration_years * 12) / method_period
            method_number = (duration_years * 12) // method_period

            result = ERPService.create_asset_model_with_journal(
                user_id=user_id,
                company_id=company_id,
                name=name,
                account_asset_id=account_asset_id,
                account_depreciation_id=account_depreciation_id,
                account_depreciation_expense_id=account_depreciation_expense_id,
                depreciation_method=method,
                method_number=method_number,
                method_period=method_period,
                is_model=True
            )

            if result.get("success"):
                logger.info(f"Asset model '{name}' created successfully")
                return {
                    "success": True,
                    "model": {
                        "id": result.get("model_id"),
                        "name": name,
                        "method": method,
                        "methodPeriod": method_period,
                        "methodNumber": method_number,
                        "accountAssetId": account_asset_id,
                        "accountDepreciationId": account_depreciation_id,
                        "accountDepreciationExpenseId": account_depreciation_expense_id,
                    },
                    "journal_id": result.get("journal_id"),
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Failed to create asset model")
                }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.create_asset_model error: {e}")
            return {"success": False, "error": str(e)}

    async def update_asset_model(
        self,
        user_id: str,
        company_id: str,
        model_id: int,
        name: Optional[str] = None,
        method: Optional[str] = None,
        method_period: Optional[int] = None,
        duration_years: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing asset model in the ERP.

        RPC: COMPANY_SETTINGS.update_asset_model

        Note: Account mappings cannot be changed after creation.

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            model_id: ERP model ID to update
            name: New model name (optional)
            method: New depreciation method (optional)
            method_period: New period in months (optional)
            duration_years: New duration in years (optional)

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            logger.info(
                f"COMPANY_SETTINGS.update_asset_model "
                f"company_id={company_id} model_id={model_id}"
            )

            from app.erp_service import ERPService

            # Build values dict with only provided fields
            values: Dict[str, Any] = {}

            if name is not None:
                values["name"] = name

            if method is not None:
                values["method"] = method

            if method_period is not None:
                values["method_period"] = method_period

            if duration_years is not None and method_period is not None:
                # Calculate method_number
                values["method_number"] = (duration_years * 12) // method_period
            elif duration_years is not None:
                # Need to get current method_period to calculate
                # Default to 12 if not provided
                values["method_number"] = duration_years

            if not values:
                return {"success": True, "message": "No changes to update"}

            result = ERPService.update_asset_model(
                user_id=user_id,
                company_id=company_id,
                model_id=model_id,
                values=values
            )

            if result.get("success"):
                logger.info(f"Asset model {model_id} updated successfully")
                return {"success": True}
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Failed to update asset model")
                }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.update_asset_model error: {e}")
            return {"success": False, "error": str(e)}

    async def delete_asset_model(
        self,
        user_id: str,
        company_id: str,
        model_id: int,
    ) -> Dict[str, Any]:
        """
        Delete an asset model from the ERP.

        RPC: COMPANY_SETTINGS.delete_asset_model

        Warning: This operation cannot be undone. The model must not
        have any assets linked to it.

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            model_id: ERP model ID to delete

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            logger.info(
                f"COMPANY_SETTINGS.delete_asset_model "
                f"company_id={company_id} model_id={model_id}"
            )

            from app.erp_service import ERPService

            result = ERPService.delete_asset_model(
                user_id=user_id,
                company_id=company_id,
                model_id=model_id
            )

            if result.get("success"):
                logger.info(f"Asset model {model_id} deleted successfully")
                return {"success": True}
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Failed to delete asset model")
                }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.delete_asset_model error: {e}")
            return {"success": False, "error": str(e)}

    async def load_asset_accounts(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
    ) -> Dict[str, Any]:
        """
        Load COA accounts filtered for asset model account mapping.

        RPC: COMPANY_SETTINGS.load_asset_accounts

        Returns accounts grouped by function type:
        - asset_fixed: Fixed asset accounts
        - cumulated_depreciation: Accumulated depreciation accounts
        - expense_depreciation: Depreciation expense accounts

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate

        Returns:
            {
                "success": True,
                "data": {
                    "assetAccounts": [...],
                    "depreciationAccounts": [...],
                    "expenseAccounts": [...]
                }
            }
        """
        try:
            logger.info(
                f"COMPANY_SETTINGS.load_asset_accounts "
                f"company_id={company_id}"
            )

            # Use COA handlers to load accounts
            from app.frontend.pages.coa.handlers import get_coa_handlers

            coa_handlers = get_coa_handlers()
            result = await coa_handlers.load_accounts(
                uid=user_id,
                company_id=company_id,
                mandate_path=mandate_path,
                force_refresh=False
            )

            if not result.get("success"):
                return {
                    "success": False,
                    "error": result.get("error", {}).get("message", "Failed to load accounts")
                }

            accounts = result.get("data", {}).get("accounts", [])

            # Filter accounts by function type
            # Asset accounts: function contains 'asset_fixed' or 'fixed_asset'
            asset_accounts = []
            depreciation_accounts = []
            expense_accounts = []

            for acc in accounts:
                if not acc.get("isactive", True):
                    continue

                func = (acc.get("account_function") or "").lower()
                account_data = {
                    "id": acc.get("account_id"),
                    "number": acc.get("account_number", ""),
                    "name": acc.get("account_name", ""),
                    "function": acc.get("account_function", ""),
                }

                # Categorize by function
                if "asset_fixed" in func or "fixed_asset" in func or "immobilisation" in func:
                    asset_accounts.append(account_data)
                elif "cumulated_depreciation" in func or "accumulated_depreciation" in func or "amortissement_cumul" in func:
                    depreciation_accounts.append(account_data)
                elif "expense_depreciation" in func or "depreciation_expense" in func or "charge_amortissement" in func or "dotation" in func:
                    expense_accounts.append(account_data)

            logger.info(
                f"Loaded asset accounts: {len(asset_accounts)} asset, "
                f"{len(depreciation_accounts)} depreciation, {len(expense_accounts)} expense"
            )

            return {
                "success": True,
                "data": {
                    "assetAccounts": asset_accounts,
                    "depreciationAccounts": depreciation_accounts,
                    "expenseAccounts": expense_accounts,
                }
            }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.load_asset_accounts error: {e}")
            return {"success": False, "error": str(e)}

    # ============================================
    # COMPANY DELETION
    # ============================================

    def delete_company(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        confirmation_name: str,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Delete a company and all its associated data.

        RPC: COMPANY_SETTINGS.delete_company

        DANGER: This is a destructive operation that cannot be undone.
        Deletes: Firestore documents, Drive folders, ChromaDB collections,
        RTDB nodes, GCS files, scheduler jobs, ERP secrets, Telegram users.

        Args:
            user_id: Firebase UID
            company_id: Company/Mandate ID
            mandate_path: Full Firebase path to mandate
            confirmation_name: Company name typed by user for confirmation
            progress_callback: Optional async callback(step_name, step_index, total_steps)

        Returns:
            {"success": True/False, "message": str, "report": list}
        """
        import asyncio

        report: List[Dict[str, str]] = []
        total_steps = 13

        def _report(name: str, status: str, reason: str = ""):
            report.append({"name": name, "status": status, "reason": reason})

        def _notify(step_name: str, step_index: int):
            """Fire progress callback if available (non-blocking)."""
            if progress_callback:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(progress_callback(step_name, step_index, total_steps))
                    else:
                        loop.run_until_complete(progress_callback(step_name, step_index, total_steps))
                except Exception:
                    pass  # Progress notification is best-effort

        try:
            logger.info(
                f"COMPANY_SETTINGS.delete_company START "
                f"company_id={company_id} user_id={user_id}"
            )

            # ── Step 1: Validation ──────────────────────────
            _notify("Validating confirmation", 1)

            mandate_data = self._firebase.get_document(mandate_path)
            if not mandate_data:
                return {"success": False, "error": "Company not found", "report": report}

            company_name = mandate_data.get("legal_name") or mandate_data.get("name", "")

            if confirmation_name != company_name:
                return {
                    "success": False,
                    "error": "Confirmation name does not match company name",
                    "report": report,
                }

            _report("Validation", "success", "Confirmation name verified")

            # ── Step 2: Read mandate data ───────────────────
            _notify("Reading company data", 2)

            drive_space_parent_id = mandate_data.get("drive_space_parent_id", "")
            contact_space_id = mandate_data.get("contact_space_id", "")
            client_name = mandate_data.get("client_name", company_name)

            _report("Read Company Data", "success", f"Client: {client_name}")

            # ── Step 3: Verify contact_space_id ─────────────
            _notify("Verifying identifiers", 3)

            if not contact_space_id:
                logger.warning(
                    f"delete_company: contact_space_id missing for {company_id}, "
                    "RTDB and ChromaDB steps will be skipped"
                )
                _report("Verify Identifiers", "skipped", "No contact_space_id found")
            else:
                _report("Verify Identifiers", "success", f"contact_space_id={contact_space_id}")

            # ── Step 4: Delete Scheduler Jobs ───────────────
            _notify("Removing scheduled jobs", 4)
            try:
                job_types = ["apbookeeper", "banker", "router"]
                deleted_jobs = 0
                for jt in job_types:
                    # Job IDs use mandate_path with slashes replaced by underscores
                    job_id = mandate_path.replace("/", "_") + f"_{jt}"
                    if self._firebase.delete_scheduler_job_completely(job_id):
                        deleted_jobs += 1
                _report("Scheduled Jobs", "success", f"Deleted {deleted_jobs}/{len(job_types)} jobs")
            except Exception as e:
                logger.warning(f"delete_company: scheduler jobs cleanup failed: {e}")
                _report("Scheduled Jobs", "failed", str(e))

            # ── Step 5: Delete ERP Secrets ──────────────────
            _notify("Removing security credentials", 5)
            try:
                from app.tools.g_cred import delete_secret

                erp_types = ["gl_accounting_erp", "ap_erp", "ar_erp", "bank_erp"]
                deleted_secrets = 0
                for erp_type in erp_types:
                    erp_data = self._firebase.get_erp_path(mandate_path, erp_type)
                    if erp_data and erp_data.get("secret_name"):
                        try:
                            delete_secret(erp_data["secret_name"])
                            deleted_secrets += 1
                        except Exception as se:
                            logger.warning(f"delete_company: failed to delete secret for {erp_type}: {se}")
                _report("Security Credentials", "success", f"Cleaned {deleted_secrets} ERP secrets")
            except Exception as e:
                logger.warning(f"delete_company: ERP secrets cleanup failed: {e}")
                _report("Security Credentials", "failed", str(e))

            # ── Step 6: Archive Drive Folder ────────────────
            _notify("Archiving document management system", 6)
            try:
                if drive_space_parent_id:
                    from app.driveClientService import get_drive_client_service

                    drive_service = get_drive_client_service()
                    archived = drive_service.Archived_Pinnokio_folder(user_id, drive_space_parent_id)
                    if archived:
                        _report("Document Management System", "success", "Drive folder archived")
                    else:
                        _report("Document Management System", "failed", "Archive operation returned False")
                else:
                    _report("Document Management System", "skipped", "No drive_space_parent_id")
            except Exception as e:
                logger.warning(f"delete_company: Drive archive failed: {e}")
                _report("Document Management System", "failed", str(e))

            # ── Step 7: Delete ChromaDB Collection ──────────
            _notify("Cleaning vector database", 7)
            try:
                if contact_space_id:
                    from app.chroma_vector_service import get_chroma_vector_service

                    chroma = get_chroma_vector_service()
                    result = chroma.delete_collection(contact_space_id)
                    if result.get("success"):
                        _report("Vector Database", "success", f"Collection '{contact_space_id}' deleted")
                    else:
                        _report("Vector Database", "failed", result.get("error", "Unknown error"))
                else:
                    _report("Vector Database", "skipped", "No contact_space_id")
            except Exception as e:
                logger.warning(f"delete_company: ChromaDB cleanup failed: {e}")
                _report("Vector Database", "failed", str(e))

            # ── Step 8: Delete Scheduler Documents ──────────
            _notify("Cleaning scheduled tasks", 8)
            try:
                result = self._firebase.delete_scheduler_documents_for_mandate(mandate_path)
                if result:
                    _report("Scheduled Tasks", "success", "Scheduler documents cleaned")
                else:
                    _report("Scheduled Tasks", "failed", "delete_scheduler_documents_for_mandate returned False")
            except Exception as e:
                logger.warning(f"delete_company: scheduler documents cleanup failed: {e}")
                _report("Scheduled Tasks", "failed", str(e))

            # ── Step 9: Clean Telegram Users ────────────────
            _notify("Cleaning communication channels", 9)
            try:
                result = self._firebase.clean_telegram_users_for_mandate(mandate_path)
                if result:
                    _report("Communication Channels", "success", "Telegram users cleaned")
                else:
                    _report("Communication Channels", "failed", "clean_telegram_users_for_mandate returned False")
            except Exception as e:
                logger.warning(f"delete_company: Telegram cleanup failed: {e}")
                _report("Communication Channels", "failed", str(e))

            # ── Step 10: Delete PostgreSQL Neon HR Data ────
            # NOTE: Cette étape est maintenant gérée de manière async dans orchestration.py
            # pour utiliser correctement le pool de connexions asyncpg et garantir la scalabilité.
            # Voir handle_delete_company() dans orchestration.py

            # ── Step 11: Delete GCS Storage ─────────────────
            _notify("Removing file storage", 11)
            try:
                from app.storage_client import get_storage_client

                storage_client = get_storage_client()
                # Two GCS paths: company files and processed outputs
                paths_to_delete = [
                    f"companies/{company_id}/",
                    f"mandates/{mandate_path.replace('/', '_')}/",
                ]
                total_deleted = 0
                for gcs_path in paths_to_delete:
                    result = storage_client.delete_path(gcs_path, recursive=True)
                    total_deleted += result.get("deleted_count", 0)
                _report("File Storage", "success", f"Deleted {total_deleted} files from GCS")
            except Exception as e:
                logger.warning(f"delete_company: GCS cleanup failed: {e}")
                _report("File Storage", "failed", str(e))

            # ── Step 12: Delete Firestore (CRITICAL) ────────
            _notify("Removing company database", 12)
            try:
                result = self._firebase.delete_document_recursive(mandate_path)
                if result:
                    _report("Company Database", "success", "Firestore documents deleted recursively")
                else:
                    _report("Company Database", "failed", "delete_document_recursive returned False")
            except Exception as e:
                logger.error(f"delete_company: CRITICAL - Firestore deletion failed: {e}")
                _report("Company Database", "failed", str(e))
                # Firestore deletion failure is critical
                return {
                    "success": False,
                    "message": f"Critical failure: Firestore deletion failed for '{company_name}'",
                    "report": report,
                }

            # ── Step 13: Delete RTDB Space ──────────────────
            _notify("Cleaning real-time services", 13)
            try:
                if contact_space_id:
                    from app.firebase_providers import get_firebase_realtime

                    rtdb = get_firebase_realtime()
                    result = rtdb.delete_space(contact_space_id)
                    if result:
                        _report("Real-time Services", "success", f"RTDB space '{contact_space_id}' deleted")
                    else:
                        _report("Real-time Services", "failed", "delete_space returned False")
                else:
                    _report("Real-time Services", "skipped", "No contact_space_id")
            except Exception as e:
                logger.warning(f"delete_company: RTDB cleanup failed: {e}")
                _report("Real-time Services", "failed", str(e))

            # ── Final Result ────────────────────────────────
            failed_steps = [r for r in report if r["status"] == "failed"]
            has_critical_failure = any(
                r["name"] == "Company Database" and r["status"] == "failed" for r in report
            )

            logger.info(
                f"COMPANY_SETTINGS.delete_company COMPLETE "
                f"company_id={company_id} "
                f"total={len(report)} failed={len(failed_steps)}"
            )

            return {
                "success": not has_critical_failure,
                "message": (
                    f"Company '{company_name}' successfully deleted"
                    if not has_critical_failure
                    else f"Company '{company_name}' deletion completed with errors"
                ),
                "report": report,
            }

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.delete_company error: {e}")
            return {
                "success": False,
                "error": str(e),
                "report": report,
            }

    # ============================================
    # EMAIL SETTINGS
    # ============================================

    async def save_email_settings(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save email governance settings.

        Writes to Firebase: {mandate_path}/setup/email_settings

        Args:
            data: {
                "contact_groups": [...],
                "default_policy": {...}
            }

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            email_settings = {
                "contact_groups": data.get("contact_groups", []),
                "default_policy": data.get("default_policy", {}),
                "updated_at": datetime.utcnow().isoformat(),
                "updated_by": user_id,
            }

            doc_path = f"{mandate_path}/setup/email_settings"
            self._firebase.set_document(doc_path, email_settings, merge=True)

            # Invalidate cache
            try:
                await self._cache.invalidate_cached_data(
                    user_id, company_id, "company_settings", "additional_data"
                )
            except Exception as cache_err:
                logger.warning(f"Cache invalidation error: {cache_err}")

            logger.info(
                f"COMPANY_SETTINGS.save_email_settings OK "
                f"company_id={company_id} groups={len(email_settings.get('contact_groups', []))}"
            )
            return {"success": True}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.save_email_settings error: {e}")
            return {"success": False, "error": str(e)}

    async def handle_email_approval(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        draft_id: str,
        decision: str,
        modified_body: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle email draft approval/rejection/modification.

        Args:
            draft_id: ID of the pending email draft
            decision: "approve" | "reject" | "modify"
            modified_body: Updated body text (for modify)

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            draft_path = f"{mandate_path}/pending_email_approvals/{draft_id}"
            draft_doc = self._firebase.get_raw_document(draft_path)

            if not draft_doc:
                return {"success": False, "error": f"Draft {draft_id} not found"}

            if decision == "approve":
                # Dispatch email sending via Redis queue
                import json
                redis = None
                try:
                    from app.redis_client import get_redis
                    redis = get_redis()
                except Exception:
                    pass

                if redis:
                    job_payload = {
                        "action": "send_email",
                        "draft_id": draft_id,
                        "draft": draft_doc,
                        "company_id": company_id,
                        "mandate_path": mandate_path,
                        "approved_by": user_id,
                    }
                    redis.lpush("queue:agentic_dispatch", json.dumps(job_payload))

                # Mark as approved
                self._firebase.set_document(draft_path, {
                    "status": "approved",
                    "approved_by": user_id,
                    "approved_at": datetime.utcnow().isoformat(),
                }, merge=True)

            elif decision == "reject":
                # Delete the draft
                self._firebase.delete_document(draft_path)

            elif decision == "modify":
                # Update the draft body
                update_data = {
                    "status": "modified",
                    "modified_by": user_id,
                    "modified_at": datetime.utcnow().isoformat(),
                }
                if modified_body is not None:
                    update_data["body"] = modified_body
                self._firebase.set_document(draft_path, update_data, merge=True)

            else:
                return {"success": False, "error": f"Unknown decision: {decision}"}

            logger.info(
                f"COMPANY_SETTINGS.handle_email_approval OK "
                f"draft_id={draft_id} decision={decision}"
            )
            return {"success": True}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.handle_email_approval error: {e}")
            return {"success": False, "error": str(e)}

    # ────────────────────────────────────────────────────────────
    # Email Provider Type
    # ────────────────────────────────────────────────────────────

    async def save_email_type(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        email_type: str,
    ) -> Dict[str, Any]:
        """
        Save the chosen email provider type to the mandate document.

        Writes field ``email_type`` at the mandate root (e.g. "gmail").
        """
        try:
            self._firebase.set_document(mandate_path, {
                "email_type": email_type,
            }, merge=True)

            try:
                await self._cache.invalidate_cached_data(
                    user_id, company_id, "company_settings", "additional_data"
                )
            except Exception as cache_err:
                logger.warning(f"Cache invalidation error: {cache_err}")

            logger.info(
                f"COMPANY_SETTINGS.save_email_type OK "
                f"company_id={company_id} email_type={email_type}"
            )
            return {"success": True}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.save_email_type error: {e}")
            return {"success": False, "error": str(e)}

    async def initiate_email_auth(
        self,
        user_id: str,
        provider: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Initiate OAuth flow for the chosen email provider.

        Gmail  → reuses GoogleAuthManager (same as onboarding).
        Outlook → returns coming_soon placeholder.
        """
        try:
            if provider == "outlook":
                return {"success": True, "coming_soon": True}

            # --- Gmail OAuth via GoogleAuthManager ---
            import os
            import base64
            import json as _json

            from pinnokio_app.logique_metier.onboarding_flow import GoogleAuthManager

            auth_manager = GoogleAuthManager(user_id=user_id)
            # Email-specific scopes (Gmail send + read)
            auth_manager.SCOPES = [
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
            ]

            redirect_uri = os.getenv(
                "GOOGLE_AUTH_REDIRECT_LOCAL",
                "http://localhost:8000/google_auth_callback/",
            )

            state = {
                "user_id": user_id,
                "source": "email_settings",
                "communication_mode": "pinnokio",
                "redirect_uri": redirect_uri,
                "session_id": session_id,
            }
            state_encoded = base64.b64encode(_json.dumps(state).encode()).decode()

            auth_url = auth_manager.get_authorization_url(state=state_encoded)

            logger.info(f"COMPANY_SETTINGS.initiate_email_auth OK provider=gmail uid={user_id}")
            return {"success": True, "auth_url": auth_url}

        except Exception as e:
            logger.error(f"COMPANY_SETTINGS.initiate_email_auth error: {e}")
            return {"success": False, "error": str(e)}
