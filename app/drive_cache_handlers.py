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
        input_drive_id: str,
        mandate_path: str = ""
    ) -> Dict[str, Any]:
        """
        Récupère les documents Google Drive croisés avec task_manager (Source de Vérité).

        RPC: DRIVE_CACHE.get_documents

        Source de Vérité:
        1. Drive: Liste brute des fichiers (base "to_process")
        2. task_manager (Firebase): État des traitements par department "Router"
        - Junction: job_id (Drive file_id == task_manager job_id)

        Classification:
        - Drive file sans match task_manager → to_process
        - task_manager status on_process/in_queue/running → in_process
        - task_manager status pending → pending
        - task_manager status completed/close/closed → processed

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            input_drive_id (str): Drive folder ID
            mandate_path (str): Chemin Firestore du mandat (pour requête task_manager)

        Returns:
            {
                "data": {
                    "to_process": [...],
                    "in_process": [...],
                    "pending": [...],
                    "processed": [...]
                },
                "source": "cache"|"drive+task_manager",
                "oauth_error": bool
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

            drive_data = await self._fetch_from_drive(user_id, input_drive_id, mandate_path)

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
                "source": "drive+task_manager",
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
        input_drive_id: str,
        mandate_path: str = ""
    ) -> Dict[str, Any]:
        """
        Force le rafraîchissement des documents depuis Drive + task_manager.

        Invalide le cache puis récupère les données fraîches.

        RPC: DRIVE_CACHE.refresh_documents

        Args:
            user_id (str): Firebase UID (injecté auto)
            company_id (str): Company ID
            input_drive_id (str): Drive folder ID
            mandate_path (str): Chemin Firestore du mandat

        Returns:
            {"data": {...}, "source": "drive+task_manager", "oauth_error": bool}
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

            # 2. Récupérer depuis Drive + task_manager
            return await self.get_documents(user_id, company_id, input_drive_id, mandate_path)

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
        input_drive_id: str,
        mandate_path: str = ""
    ) -> Dict[str, Any]:
        """
        Récupère les documents depuis Google Drive API et croise avec task_manager.

        IMPORTANT: Cette méthode utilise DriveClientService qui nécessite
        des credentials OAuth valides. En cas d'erreur OAuth, retourne
        oauth_error=True.

        Args:
            user_id: Firebase UID
            input_drive_id: Drive folder ID
            mandate_path: Chemin Firestore du mandat (pour requête task_manager)

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
                organized_docs = await self._organize_documents_by_status_with_firebase(user_id, data, mandate_path)
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
        drive_files: List[Dict],
        mandate_path: str = ""
    ) -> Dict[str, List]:
        """
        Croise les fichiers Drive avec task_manager (Source de Vérité) en 1 requête bulk.

        Logique de tri:
        1. Charger TOUS les jobs Router depuis task_manager (1 seule requête)
        2. Pour chaque fichier Drive, vérifier si job_id existe dans task_manager:
           - status on_process/in_queue/running/stopping → in_process
           - status pending → pending
           - status completed/close/closed → processed
           - Pas de match ou status error/to_process → to_process
        3. Jobs task_manager sans match Drive → ajoutés à leur catégorie

        Args:
            user_id: Firebase user ID
            drive_files: Liste brute de fichiers depuis Drive API
            mandate_path: Chemin Firestore du mandat

        Returns:
            {
                "to_process": [...],
                "in_process": [...],
                "pending": [...],
                "processed": [...]
            }
        """
        organized = {
            "to_process": [],
            "in_process": [],
            "pending": [],
            "processed": []
        }

        # 1. Charger les jobs Router depuis task_manager (1 requête bulk)
        task_manager_index = {}  # job_id → task_manager data
        if mandate_path:
            try:
                firebase_mgmt = get_firebase_management()
                tm_result = await asyncio.to_thread(
                    firebase_mgmt.get_router_jobs_from_task_manager,
                    user_id,
                    mandate_path
                )
                task_manager_index = tm_result.get("by_job_id", {})
                logger.info(
                    f"DRIVE_CACHE._organize task_manager loaded: "
                    f"{len(task_manager_index)} router jobs found"
                )
            except Exception as e:
                logger.warning(f"DRIVE_CACHE._organize task_manager error: {e}")

        # 2. Croiser chaque fichier Drive avec task_manager
        matched_job_ids = set()

        for doc in drive_files:
            file_id = doc.get('id', '')
            file_name = doc.get('name', '')

            # Formater le timestamp
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
                "job_id": file_id,
                "file_name": file_name,
                "name": file_name,
                "created_time": formatted_time,
                "timestamp": formatted_time,
                "status": "to_process",
                "source": "drive",
                "drive_file_id": file_id,
                "uri_drive_link": doc.get('webViewLink', ''),
                "web_view_link": doc.get('webViewLink', ''),
            }

            # Vérifier si ce fichier a un job dans task_manager
            tm_data = task_manager_index.get(file_id)
            if tm_data:
                matched_job_ids.add(file_id)
                status = tm_data.get("status", "").lower()
                # Garder le statut original pour le badge frontend
                doc_item["status"] = status

                if status in ["completed", "close", "closed"]:
                    organized["processed"].append(doc_item)
                elif status == "pending":
                    organized["pending"].append(doc_item)
                elif status in ["on_process", "processing", "in_progress", "in_queue", "running", "stopping"]:
                    organized["in_process"].append(doc_item)
                else:
                    # error, to_process, stopped ou autre → reste dans to_process
                    if status not in ["error", "to_process", "stopped"]:
                        doc_item["status"] = "to_process"
                    organized["to_process"].append(doc_item)
            else:
                # Pas de match task_manager → À traiter
                organized["to_process"].append(doc_item)

        # 3. Jobs task_manager sans match Drive (fichiers déjà déplacés du Drive)
        for job_id, tm_data in task_manager_index.items():
            if job_id in matched_job_ids:
                continue
            status = tm_data.get("status", "").lower()
            item = {
                "id": job_id,
                "job_id": job_id,
                "file_name": tm_data.get("file_name", ""),
                "name": tm_data.get("file_name", ""),
                "timestamp": tm_data.get("date", ""),
                "status": status,  # Garder le statut original
                "source": "task_manager",
            }
            if status in ["completed", "close", "closed"]:
                organized["processed"].append(item)
            elif status == "pending":
                organized["pending"].append(item)
            elif status in ["on_process", "processing", "in_progress", "in_queue", "running", "stopping"]:
                organized["in_process"].append(item)

        logger.info(
            f"DRIVE_CACHE._organize_documents "
            f"to_process={len(organized['to_process'])} "
            f"in_process={len(organized['in_process'])} "
            f"pending={len(organized['pending'])} "
            f"processed={len(organized['processed'])}"
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
