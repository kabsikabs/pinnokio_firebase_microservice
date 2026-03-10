"""
Static Data Handlers - Dropdown/Configuration Data
===================================================

This module provides handlers for loading static/configuration data
used throughout the application (dropdowns, lists, etc.).

These data are loaded ONCE during Phase 0 (user_setup) of orchestration
and cached in the frontend store permanently (until app reload).

Data includes:
- Languages: Available languages for the app
- Countries: List of countries
- Legal forms: Legal forms per country
- ERPs: ERP systems (Odoo, Banana, etc.)
- DMS: Document management systems (Drive, etc.)
- Currencies: Available currencies
- Communication: Communication types (Pinnokio, Telegram)

Architecture:
    Frontend -> WebSocket -> static_data_handlers.py -> FirebaseManagement

Events Handled:
    - static_data.load: Load all static data
    - static_data.refresh: Force refresh cached data

Cache Strategy:
    - Backend: Redis cache with 24h TTL (static data rarely changes)
    - Frontend: Zustand store (permanent until app reload)

Author: Lead Migration Architect
Created: 2026-01-22
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from ..firebase_providers import FirebaseManagement
from ..redis_client import get_redis
from ..ws_events import WS_EVENTS
from ..ws_hub import hub

logger = logging.getLogger("static_data.handlers")


# ============================================
# CONSTANTS
# ============================================

# Redis cache TTL: 24 hours (static data rarely changes)
STATIC_DATA_CACHE_TTL = 86400
STATIC_DATA_CACHE_KEY = "static_data:global"


# ============================================
# SINGLETON
# ============================================

_instance: Optional["StaticDataHandlers"] = None


def get_static_data_handlers() -> "StaticDataHandlers":
    """Get singleton instance of StaticDataHandlers."""
    global _instance
    if _instance is None:
        _instance = StaticDataHandlers()
    return _instance


# ============================================
# STATIC DATA HANDLERS
# ============================================

class StaticDataHandlers:
    """
    Handlers for loading static/configuration data.

    All data is loaded from Firebase via FirebaseManagement singleton
    and cached in Redis for performance.
    """

    def __init__(self):
        self._firebase: Optional[FirebaseManagement] = None
        self._redis = None

    @property
    def firebase(self) -> FirebaseManagement:
        """Lazy-load FirebaseManagement singleton."""
        if self._firebase is None:
            self._firebase = FirebaseManagement()
        return self._firebase

    @property
    def redis(self):
        """Lazy-load Redis client."""
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    async def load_all_static_data(
        self,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Load all static data for dropdowns.

        This is called during Phase 0 (user_setup) of orchestration.
        Data is cached in Redis for 24 hours.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Dict containing all static data:
            {
                "success": True,
                "data": {
                    "languages": [...],
                    "countries": [...],
                    "legalForms": {...},  # Map by country
                    "erps": [...],
                    "dms": [...],
                    "currencies": [...],
                    "communicationTypes": [...],
                    "emailTypes": [...]
                }
            }
        """
        try:
            # Check cache first (unless force refresh)
            if not force_refresh:
                cached = self.redis.get(STATIC_DATA_CACHE_KEY)
                if cached:
                    data = json.loads(cached if isinstance(cached, str) else cached.decode())
                    logger.info("[STATIC_DATA] Returning cached data")
                    return {
                        "success": True,
                        "data": data,
                        "from_cache": True
                    }

            logger.info("[STATIC_DATA] Fetching fresh data from Firebase...")

            # Fetch all data in parallel using asyncio.to_thread (non-blocking)
            languages_task = asyncio.to_thread(self._load_languages)
            countries_task = asyncio.to_thread(self._load_countries)
            erps_task = asyncio.to_thread(self._load_erps)
            dms_task = asyncio.to_thread(self._load_dms)
            currencies_task = asyncio.to_thread(self._load_currencies)
            communication_task = asyncio.to_thread(self._load_communication_types)
            email_task = asyncio.to_thread(self._load_email_types)

            # Wait for all tasks
            results = await asyncio.gather(
                languages_task,
                countries_task,
                erps_task,
                dms_task,
                currencies_task,
                communication_task,
                email_task,
                return_exceptions=True
            )

            # Unpack results
            languages = results[0] if not isinstance(results[0], Exception) else []
            countries_data = results[1] if not isinstance(results[1], Exception) else ({}, [])
            erps = results[2] if not isinstance(results[2], Exception) else []
            dms = results[3] if not isinstance(results[3], Exception) else []
            currencies = results[4] if not isinstance(results[4], Exception) else []
            communication_types = results[5] if not isinstance(results[5], Exception) else []
            email_types = results[6] if not isinstance(results[6], Exception) else []

            # countries_data is a tuple (country_id_map, countries_list)
            country_id_map, countries_list = countries_data

            # Load legal forms for all countries
            legal_forms = await asyncio.to_thread(
                self._load_all_legal_forms,
                countries_list,
                country_id_map
            )

            # Build response data
            data = {
                "languages": languages,
                "countries": countries_list,
                "countryIdMap": country_id_map,  # Needed for legal forms lookup
                "legalForms": legal_forms,  # Map: country_name -> [forms]
                "erps": erps,
                "dms": dms,
                "currencies": currencies,
                "communicationTypes": communication_types,
                "emailTypes": email_types
            }

            # Cache in Redis
            self.redis.setex(
                STATIC_DATA_CACHE_KEY,
                STATIC_DATA_CACHE_TTL,
                json.dumps(data)
            )

            logger.info(
                f"[STATIC_DATA] Loaded: "
                f"languages={len(languages)}, "
                f"countries={len(countries_list)}, "
                f"erps={len(erps)}, "
                f"dms={len(dms)}, "
                f"currencies={len(currencies)}, "
                f"communicationTypes={len(communication_types)}, "
                f"emailTypes={len(email_types)}"
            )

            return {
                "success": True,
                "data": data,
                "from_cache": False
            }

        except Exception as e:
            logger.error(f"[STATIC_DATA] Error loading data: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    def _load_languages(self) -> List[Dict[str, Any]]:
        """Load languages from Firebase."""
        try:
            languages_dict = self.firebase.download_all_languages()
            # Convert dict to list format
            languages = []
            for lang_code, lang_data in languages_dict.items():
                languages.append({
                    "code": lang_code,
                    "name": lang_data.get("name", lang_code),
                    "nativeName": lang_data.get("native_name", lang_data.get("name", lang_code))
                })
            # Sort by name
            languages.sort(key=lambda x: x.get("name", ""))
            return languages
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading languages: {e}")
            # Return fallback
            return [
                {"code": "en", "name": "English", "nativeName": "English"},
                {"code": "fr", "name": "French", "nativeName": "Francais"},
                {"code": "de", "name": "German", "nativeName": "Deutsch"}
            ]

    def _load_countries(self) -> tuple:
        """
        Load countries from Firebase.

        Returns:
            Tuple of (country_id_map, countries_list)
        """
        try:
            countries_list, country_id_map = self.firebase.get_countries_list()
            return (country_id_map, countries_list)
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading countries: {e}")
            # Return fallback
            return (
                {"Switzerland": 1, "France": 2, "Germany": 3, "Belgium": 4, "Luxembourg": 5},
                ["Belgium", "France", "Germany", "Luxembourg", "Switzerland"]
            )

    def _load_all_legal_forms(
        self,
        countries_list: List[str],
        country_id_map: Dict[str, int]
    ) -> Dict[str, List[str]]:
        """
        Load legal forms for all countries.

        Args:
            countries_list: List of country names
            country_id_map: Map of country name to ID

        Returns:
            Dict mapping country name to list of legal forms
        """
        legal_forms = {}
        for country_name in countries_list:
            try:
                forms = self.firebase.get_legal_forms_for_country(
                    country_name,
                    country_id_map
                )
                if forms:
                    legal_forms[country_name] = forms
            except Exception as e:
                logger.warning(f"[STATIC_DATA] Error loading legal forms for {country_name}: {e}")
        return legal_forms

    def _load_erps(self) -> List[Dict[str, Any]]:
        """Load ERP systems from Firebase."""
        try:
            erps = self.firebase.get_param_data('erp')
            if not isinstance(erps, list):
                return []
            # Transform Firebase format to frontend format
            # Firebase: {'id': '1', 'erp_displayname': 'Odoo', 'erp_name': 'odoo'}
            # Frontend: {'id': 'odoo', 'name': 'Odoo'}
            return [
                {
                    "id": erp.get("erp_name", erp.get("id", "")),
                    "name": erp.get("erp_displayname", erp.get("name", erp.get("erp_name", "")))
                }
                for erp in erps
                if isinstance(erp, dict) and erp.get("active", True)
            ]
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading ERPs: {e}")
            # Return fallback
            return [
                {"id": "odoo", "name": "Odoo"},
                {"id": "banana", "name": "Banana"},
                {"id": "none", "name": "None"}
            ]

    def _load_dms(self) -> List[Dict[str, Any]]:
        """Load DMS systems from Firebase."""
        try:
            dms = self.firebase.get_param_data('dms')
            if not isinstance(dms, list):
                return []
            # Transform Firebase format to frontend format
            # Firebase: {'id': '1', 'dms_displayname': 'Google Drive', 'dms_name': 'google_drive'}
            # Frontend: {'id': 'google_drive', 'name': 'Google Drive'}
            return [
                {
                    "id": item.get("dms_name", item.get("id", "")),
                    "name": item.get("dms_displayname", item.get("name", item.get("dms_name", "")))
                }
                for item in dms
                if isinstance(item, dict) and item.get("active", True)
            ]
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading DMS: {e}")
            # Return fallback
            return [
                {"id": "drive", "name": "Google Drive"},
                {"id": "none", "name": "None"}
            ]

    def _load_currencies(self) -> List[Dict[str, Any]]:
        """Load currencies from Firebase."""
        try:
            currencies = self.firebase.get_all_currencies()
            # Normalize format
            if isinstance(currencies, list):
                return [
                    {
                        "code": c.get("currency_iso_code", c.get("code", c.get("id", ""))),
                        "name": c.get("name", c.get("currency_iso_code", "")),
                        "region": c.get("region", "")
                    }
                    for c in currencies
                    if isinstance(c, dict)
                ]
            return []
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading currencies: {e}")
            # Return fallback
            return [
                {"code": "CHF", "name": "Swiss Franc", "region": "Switzerland"},
                {"code": "EUR", "name": "Euro", "region": "Europe"},
                {"code": "USD", "name": "US Dollar", "region": "United States"},
                {"code": "GBP", "name": "British Pound", "region": "United Kingdom"}
            ]

    def _load_email_types(self) -> List[Dict[str, Any]]:
        """Load email provider types from Firebase."""
        try:
            email_types = self.firebase.get_param_data('email')
            if not isinstance(email_types, list):
                return []
            return [
                {
                    "id": item.get("email_name", str(item.get("id", ""))),
                    "name": item.get("email_displayname", item.get("name", item.get("email_name", "")))
                }
                for item in email_types
                if isinstance(item, dict) and item.get("active", True)
            ]
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading email types: {e}")
            return [
                {"id": "gmail", "name": "Gmail"},
                {"id": "outlook", "name": "Outlook"}
            ]

    def _load_communication_types(self) -> List[Dict[str, Any]]:
        """Load communication types from Firebase."""
        try:
            comm_types = self.firebase.get_param_data('communication')
            if not isinstance(comm_types, list):
                return []
            # Transform Firebase format to frontend format
            # Firebase: {'id': 1, 'communication_displayname': 'Pinnokio', 'communication_name': 'pinnokio', 'is_active': True}
            # Frontend: {'id': 'pinnokio', 'name': 'Pinnokio'}
            return [
                {
                    "id": item.get("communication_name", str(item.get("id", ""))),
                    "name": item.get("communication_displayname", item.get("name", item.get("communication_name", "")))
                }
                for item in comm_types
                if isinstance(item, dict) and item.get("is_active", True)
            ]
        except Exception as e:
            logger.warning(f"[STATIC_DATA] Error loading communication types: {e}")
            # Return fallback
            return [
                {"id": "pinnokio", "name": "Pinnokio Chat"},
                {"id": "telegram", "name": "Telegram"}
            ]


# ============================================
# WEBSOCKET HANDLERS
# ============================================

async def handle_static_data_load(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle static_data.load WebSocket event.

    Called during Phase 0 (user_setup) of orchestration.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Optional payload (can include force_refresh flag)

    Returns:
        Response dict with static data
    """
    logger.info(f"[STATIC_DATA] Load requested: uid={uid}")

    handlers = get_static_data_handlers()
    force_refresh = payload.get("force_refresh", False)

    result = await handlers.load_all_static_data(force_refresh=force_refresh)

    # Broadcast to user
    await hub.broadcast(uid, {
        "type": WS_EVENTS.STATIC_DATA.LOADED,
        "payload": result
    })

    return {
        "type": WS_EVENTS.STATIC_DATA.LOADED,
        "payload": result
    }


async def handle_static_data_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle static_data.refresh WebSocket event.

    Forces a refresh of cached static data.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Payload (ignored)

    Returns:
        Response dict with fresh static data
    """
    logger.info(f"[STATIC_DATA] Refresh requested: uid={uid}")

    handlers = get_static_data_handlers()
    result = await handlers.load_all_static_data(force_refresh=True)

    # Broadcast to user
    await hub.broadcast(uid, {
        "type": WS_EVENTS.STATIC_DATA.LOADED,
        "payload": result
    })

    return {
        "type": WS_EVENTS.STATIC_DATA.LOADED,
        "payload": result
    }


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "StaticDataHandlers",
    "get_static_data_handlers",
    "handle_static_data_load",
    "handle_static_data_refresh",
]
