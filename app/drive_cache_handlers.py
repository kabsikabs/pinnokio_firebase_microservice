"""
Handlers RPC pour Google Drive avec cache Redis intégré.

Ces handlers implémentent la stratégie cache-first pour les documents Drive:
    - Documents to_do (à traiter)
    - Documents in_process (en cours)
    - Documents processed (traités)

NAMESPACE: DRIVE_CACHE

Architecture:
    Frontend (Reflex) → rpc_call("DRIVE_CACHE.get_documents", ...)
                     → POST /rpc
                     → drive_cache_handlers.get_documents()
                     → Redis Cache (HIT) | Google Drive API (MISS)

Endpoints disponibles:
    - DRIVE_CACHE.get_documents      → Documents Drive (TTL 30min)
    - DRIVE_CACHE.refresh_documents  → Force refresh depuis Drive
    - DRIVE_CACHE.invalidate_cache   → Invalidation manuelle

Note:
    - user_id est injecté automatiquement par main.py si non fourni
    - Nécessite OAuth Drive credentials valides pour l'utilisateur
"""

import logging
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

from .cache.unified_cache_manager import get_drive_cache_manager
from .llm_service.redis_namespaces import RedisTTL
from .firebase_providers import get_firebase_management
from .status_normalization import StatusNormalizer

logger = logging.getLogger("drive.cache_handlers")


# ═══════════════════════════════════════════════════════════════
# CONSTANTES TTL
# ═══════════════════════════════════════════════════════════════

TTL_DRIVE_DOCUMENTS = 1800  # 30 minutes


class DriveCacheHandlers:
    """
    Handlers RPC pour le namespace DRIVE_CACHE.

    Chaque méthode correspond à un endpoint RPC:
    - DRIVE_CACHE.get_documents → get_documents()
    - DRIVE_CACHE.refresh_documents → refresh_documents()
    - etc.

    Toutes les méthodes sont asynchrones.

    IMPORTANT: Ces handlers nécessitent que l'utilisateur ait des credentials
    OAuth valides pour accéder à Google Drive. En cas d'erreur OAuth,
    un message approprié est retourné pour déclencher le re-consent.
    """

    NAMESPACE = "DRIVE_CACHE"

    # ═══════════════════════════════════════════════════════════════
    # DOCUMENTS DRIVE
    # ═══════════════════════════════════════════════════════════════

    async def get_documents(
        self,
        user_id: str,
        company_id: str,
        input_drive_id: str
    ) -> Dict[str, Any]:
        """
        Récupère les documents Google Drive avec cache.

        RPC: DRIVE_CACHE.get_documents

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            input_drive_id (str): Drive folder ID

        Returns:
            {
                "data": {
                    "to_process": [...],
                    "in_process": [...],
                    "processed": [...]
                },
                "source": "cache"|"drive",
                "oauth_error": bool  # True si erreur OAuth
            }
        """
        try:
            # 1. Tentative cache
            cache = get_drive_cache_manager()
            cached = await cache.get_cached_data(
                user_id,
                company_id,
                "drive",
                "documents",
                ttl_seconds=TTL_DRIVE_DOCUMENTS
            )

            if cached and cached.get("data"):
                logger.info(
                    f"DRIVE_CACHE.get_documents company_id={company_id} "
                    f"source=cache"
                )
                return {
                    "data": cached["data"],
                    "source": "cache",
                    "oauth_error": False
                }

            # 2. Fallback Google Drive API
            logger.info(
                f"DRIVE_CACHE.get_documents company_id={company_id} "
                f"fetching_from_drive"
            )

            drive_data = await self._fetch_from_drive(user_id, input_drive_id)

            # 3. Vérifier erreur OAuth
            if drive_data.get("oauth_error"):
                logger.warning(
                    f"DRIVE_CACHE.get_documents company_id={company_id} "
                    f"oauth_error={drive_data.get('error_message')}"
                )
                return {
                    "data": None,
                    "source": "drive",
                    "oauth_error": True,
                    "error_message": drive_data.get("error_message", "OAuth authentication required")
                }

            # 4. Sync vers Redis si succès
            if drive_data.get("data"):
                await cache.set_cached_data(
                    user_id,
                    company_id,
                    "drive",
                    "documents",
                    drive_data["data"],
                    ttl_seconds=TTL_DRIVE_DOCUMENTS
                )
                logger.info(
                    f"DRIVE_CACHE.get_documents company_id={company_id} "
                    f"source=drive cached=true"
                )

            return {
                "data": drive_data.get("data"),
                "source": "drive",
                "oauth_error": False
            }

        except Exception as e:
            logger.error(f"DRIVE_CACHE.get_documents error={e}")
            return {
                "data": None,
                "error": str(e),
                "oauth_error": False
            }

    async def refresh_documents(
        self,
        user_id: str,
        company_id: str,
        input_drive_id: str
    ) -> Dict[str, Any]:
        """
        Force le rafraîchissement des documents depuis Drive.

        Invalide le cache puis récupère les données fraîches.

        RPC: DRIVE_CACHE.refresh_documents

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            input_drive_id (str): Drive folder ID

        Returns:
            {"data": {...}, "source": "drive", "oauth_error": bool}
        """
        try:
            # 1. Invalider le cache
            cache = get_drive_cache_manager()
            await cache.invalidate_cache(
                user_id,
                company_id,
                "drive",
                "documents"
            )
            logger.info(
                f"DRIVE_CACHE.refresh_documents company_id={company_id} "
                f"cache_invalidated"
            )

            # 2. Récupérer depuis Drive
            return await self.get_documents(user_id, company_id, input_drive_id)

        except Exception as e:
            logger.error(f"DRIVE_CACHE.refresh_documents error={e}")
            return {
                "data": None,
                "error": str(e),
                "oauth_error": False
            }

    async def _fetch_from_drive(
        self,
        user_id: str,
        input_drive_id: str
    ) -> Dict[str, Any]:
        """
        Récupère les documents depuis Google Drive API.

        IMPORTANT: Cette méthode utilise DriveClientService qui nécessite
        des credentials OAuth valides. En cas d'erreur OAuth, retourne
        oauth_error=True.

        Args:
            user_id: Firebase UID
            input_drive_id: Drive folder ID

        Returns:
            {
                "data": {...} ou None,
                "oauth_error": bool,
                "error_message": str (si erreur OAuth)
            }
        """
        try:
            # Import local pour éviter les dépendances circulaires
            from .driveClientService import DriveClientService

            # DriveClientService est un singleton - ne pas passer user_id au constructeur
            # user_id est passé aux méthodes individuelles
            drive_service = DriveClientService(mode='prod')

            # list_files_in_doc_to_do est async - l'appeler directement
            data = await drive_service.list_files_in_doc_to_do(
                user_id,
                input_drive_id
            )

            # Cas 1: Data None = erreur OAuth silencieuse
            if data is None:
                logger.warning(
                    f"DRIVE_CACHE._fetch_from_drive user_id={user_id} "
                    f"data_none oauth_required"
                )
                return {
                    "data": None,
                    "oauth_error": True,
                    "error_message": "OAuth authentication required"
                }

            # Cas 2: Erreur explicite retournée par le service
            if isinstance(data, dict) and "erreur" in data:
                error_text = str(data.get("erreur", "")).lower()
                logger.warning(
                    f"DRIVE_CACHE._fetch_from_drive user_id={user_id} "
                    f"error={error_text}"
                )

                # Détecter erreurs OAuth
                if "invalid_grant" in error_text or "unauthorized" in error_text:
                    return {
                        "data": None,
                        "oauth_error": True,
                        "error_message": data.get("erreur", "OAuth re-authentication required")
                    }

                # Autres erreurs Drive
                return {
                    "data": None,
                    "oauth_error": False,
                    "error_message": data.get("erreur", "Drive API error")
                }

            # Cas 3: Succès - organiser les documents par statut avec check Firebase
            if isinstance(data, list):
                organized_docs = await self._organize_documents_by_status_with_firebase(user_id, data)
                logger.info(
                    f"DRIVE_CACHE._fetch_from_drive user_id={user_id} "
                    f"success count={len(data)}"
                )
                return {
                    "data": organized_docs,
                    "oauth_error": False
                }

            # Cas 4: Format inattendu
            logger.warning(
                f"DRIVE_CACHE._fetch_from_drive user_id={user_id} "
                f"unexpected_format type={type(data)}"
            )
            return {
                "data": None,
                "oauth_error": False,
                "error_message": "Unexpected data format from Drive API"
            }

        except Exception as e:
            error_str = str(e).lower()
            logger.error(f"DRIVE_CACHE._fetch_from_drive error={e}")

            # Détecter erreurs OAuth dans les exceptions
            if "invalid_grant" in error_str or "oauth" in error_str:
                return {
                    "data": None,
                    "oauth_error": True,
                    "error_message": str(e)
                }

            return {
                "data": None,
                "oauth_error": False,
                "error_message": str(e)
            }

    async def _organize_documents_by_status_with_firebase(
        self,
        user_id: str,
        drive_files: List[Dict]
    ) -> Dict[str, List]:
        """
        Organise les documents Drive par statut en vérifiant les notifications Firebase.

        Logique de tri (conforme à Router.py):
        1. Pour chaque fichier Drive, vérifier check_job_status(user_id, file_id)
        2. Si notification existe avec function_name='Router':
           - status='running'|'in queue'|'stopping' → in_process (En cours)
           - status='pending' → pending (En attente)
           - status='error'|'completed'|'success' ou autre → to_process (À traiter)
        3. Pas de notification → to_process (À traiter)

        Args:
            user_id: Firebase user ID
            drive_files: Liste brute de fichiers depuis Drive API

        Returns:
            {
                "to_process": [...],
                "in_process": [...],
                "pending": [...]
            }
        """
        organized = {
            "to_process": [],
            "in_process": [],
            "pending": []
        }

        firebase_mgmt = get_firebase_management()

        for doc in drive_files:
            file_id = doc.get('id', '')
            file_name = doc.get('name', '')

            # Créer l'objet document enrichi
            created_time = doc.get('createdTime', '')
            try:
                if created_time:
                    formatted_time = datetime.strptime(
                        created_time, "%Y-%m-%dT%H:%M:%S.%fZ"
                    ).strftime("%d/%m/%Y %H:%M")
                else:
                    formatted_time = datetime.now().strftime("%d/%m/%Y %H:%M")
            except Exception:
                formatted_time = datetime.now().strftime("%d/%m/%Y %H:%M")

            doc_item = {
                "id": file_id,
                "job_id": file_id,  # job_id = file_id pour les documents Drive
                "file_name": file_name,
                "name": file_name,
                "created_time": formatted_time,
                "timestamp": formatted_time,
                "status": "to_process",  # Statut par défaut
                "source": "drive",
                "drive_file_id": file_id,
                "uri_drive_link": doc.get('webViewLink', ''),
                "web_view_link": doc.get('webViewLink', ''),
            }

            # Vérifier le status dans Firebase notifications
            try:
                notification = await asyncio.to_thread(
                    firebase_mgmt.check_job_status,
                    user_id,
                    None,  # job_id
                    file_id  # file_id
                )

                # Si notification existe et correspond à la fonction Router
                if notification and notification.get('function_name') == 'Router':
                    firebase_status = notification.get('status', '')
                    function_name = notification.get('function_name', '')

                    # Utiliser le normalizer centralisé
                    normalized_status = StatusNormalizer.normalize_for_function(
                        function_name,
                        firebase_status,
                        default="to_process"
                    )
                    doc_item['status'] = normalized_status

                    # Catégoriser selon le statut normalisé
                    category = StatusNormalizer.get_category(normalized_status)
                    if category == "in_process":
                        organized["in_process"].append(doc_item)
                    elif category == "pending":
                        organized["pending"].append(doc_item)
                    else:
                        # to_process ou processed → dans to_process pour Router
                        organized["to_process"].append(doc_item)
                else:
                    # Pas de notification Router → À traiter
                    organized["to_process"].append(doc_item)

            except Exception as e:
                logger.warning(f"Error checking job status for {file_id}: {e}")
                # En cas d'erreur, mettre dans À traiter
                organized["to_process"].append(doc_item)

            logger.debug(f"Document {file_name} (ID: {file_id}): status={doc_item['status']}")

        logger.info(
            f"DRIVE_CACHE._organize_documents_with_firebase "
            f"to_process={len(organized['to_process'])} "
            f"in_process={len(organized['in_process'])} "
            f"pending={len(organized['pending'])}"
        )

        return organized

    def _organize_documents_by_status(self, drive_files: List[Dict]) -> Dict[str, List]:
        """
        DEPRECATED: Ancienne méthode sans vérification Firebase.
        Utilisez _organize_documents_by_status_with_firebase à la place.

        Organise les documents Drive par statut (sans vérification Firebase).
        """
        organized = {
            "to_process": [],
            "in_process": [],
            "pending": []
        }

        for doc in drive_files:
            status = doc.get("status", "to_process")

            if status in ("in_process", "on_process", "in_queue", "stopping", "running"):
                organized["in_process"].append(doc)
            elif status == "pending":
                organized["pending"].append(doc)
            else:
                organized["to_process"].append(doc)

        logger.debug(
            f"DRIVE_CACHE._organize_documents "
            f"to_process={len(organized['to_process'])} "
            f"in_process={len(organized['in_process'])} "
            f"pending={len(organized['pending'])}"
        )

        return organized

    # ═══════════════════════════════════════════════════════════════
    # CACHE INVALIDATION
    # ═══════════════════════════════════════════════════════════════

    async def invalidate_cache(
        self,
        user_id: str,
        company_id: str
    ) -> Dict[str, Any]:
        """
        Invalide le cache Drive pour un utilisateur/société.

        RPC: DRIVE_CACHE.invalidate_cache

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID

        Returns:
            {"success": bool}
        """
        try:
            cache = get_drive_cache_manager()
            success = await cache.invalidate_cache(
                user_id,
                company_id,
                "drive",
                "documents"
            )

            logger.info(
                f"DRIVE_CACHE.invalidate_cache user_id={user_id} "
                f"company_id={company_id} success={success}"
            )

            return {"success": success}

        except Exception as e:
            logger.error(f"DRIVE_CACHE.invalidate_cache error={e}")
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

drive_cache_handlers = DriveCacheHandlers()


def get_drive_cache_handlers() -> DriveCacheHandlers:
    """Retourne l'instance singleton des handlers Drive cache."""
    return drive_cache_handlers
