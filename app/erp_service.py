"""
Service ERP Singleton pour le microservice
Architecture: Singleton thread-safe avec paramètres de connexion dynamiques

Ce service remplace ERPService/ERPInstance côté Reflex pour centraliser
les connexions ERP dans le microservice.

Flux:
1. Client (Reflex) → RPC → ERP.method(user_id, company_id, ...)
2. Service récupère les credentials depuis Firebase/Secret Manager
3. Crée/réutilise une connexion ERP pour ce (user_id, company_id)
4. Exécute la méthode et retourne le résultat

Avantages:
- Connexions ERP centralisées et cachées
- Pas de connexion ERP côté Reflex
- Support multi-utilisateurs/multi-sociétés
- Gestion automatique du cache de connexions
"""

import threading
import logging
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timedelta
from .erp_manager import ODOO_KLK_VISION
from .tools.g_cred import get_secret
from .firebase_client import get_firestore

logger = logging.getLogger(__name__)


class ERPConnectionManager:
    """
    Gestionnaire de connexions ERP avec cache et gestion du lifecycle.

    Cache Key Format: {user_id}:{company_id}:{erp_type}
    """

    def __init__(self):
        self._connections: Dict[str, Tuple[ODOO_KLK_VISION, datetime]] = {}
        self._lock = threading.RLock()
        self._ttl_minutes = 30  # TTL par défaut des connexions

    def _build_cache_key(self, user_id: str, company_id: str, erp_type: str = "odoo") -> str:
        """Construit la clé de cache pour une connexion ERP."""
        return f"{user_id}:{company_id}:{erp_type}"

    def _cleanup_expired_connections(self):
        """Nettoie les connexions expirées (appelé périodiquement)."""
        now = datetime.now()
        expired_keys = []

        with self._lock:
            for key, (_, created_at) in self._connections.items():
                if now - created_at > timedelta(minutes=self._ttl_minutes):
                    expired_keys.append(key)

            for key in expired_keys:
                logger.info(f"🧹 [ERP] Nettoyage connexion expirée: {key}")
                del self._connections[key]

    def _get_erp_credentials(self, user_id: str, company_id: str, client_uuid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Récupère les credentials ERP depuis Firestore.
        
        ⭐ NOUVELLE ARCHITECTURE: Utilise reconstruct_full_client_profile() comme LLM Manager
        pour récupérer les credentials depuis le mandate (chemin réel des données).

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société/space (collection_name)
            client_uuid: Identifiant client explicite (optionnel, priorité sur Firestore)

        Returns:
            Dict contenant les credentials ou None si non trouvé
        """
        try:
            from .firebase_providers import FirebaseManagement
            
            db = get_firestore()
            firebase_service = FirebaseManagement()

            resolved_client_uuid = client_uuid
            lookup_source = "caller"

            if not resolved_client_uuid:
                # 1. Essayer de déduire client_uuid via contact_space_id (company_id)
                lookup = firebase_service.resolve_client_by_contact_space(user_id, company_id)
                if lookup and lookup.get("client_uuid"):
                    resolved_client_uuid = lookup["client_uuid"]
                    lookup_source = "contact_space"
                    logger.info(
                        "✅ [ERP] client_uuid résolu via contact_space_id=%s → %s",
                        company_id,
                        resolved_client_uuid,
                    )

            if not resolved_client_uuid:
                # 2. Fallback historique: document bo_clients/{user_id}
                doc_ref = db.collection(f'clients/{user_id}/bo_clients').document(user_id)
                doc = doc_ref.get()

                if doc.exists:
                    client_data = doc.to_dict()
                    resolved_client_uuid = client_data.get('client_uuid')

                if not resolved_client_uuid:
                    logger.error(f"❌ [ERP] client_uuid not found for user={user_id} company={company_id}")
                    return None

                lookup_source = "user_root"
                logger.info(f"✅ [ERP] client_uuid found via fallback document: {resolved_client_uuid}")
            else:
                logger.info(f"✅ [ERP] client_uuid provided by {lookup_source}: {resolved_client_uuid}")

            # 2. Récupérer le mandate_path via reconstruct_full_client_profile
            full_profile = firebase_service.reconstruct_full_client_profile(
                user_id,
                resolved_client_uuid,
                company_id  # collection_name / space_id
            )

            if not full_profile:
                logger.error(f"❌ [ERP] Full profile not found for user={user_id}, company={company_id}")
                return None

            # 3. Construire le chemin vers le document ERP
            # Structure : mandate_path/erp/{bank_erp}
            # Ex: clients/{uid}/bo_clients/{client_id}/mandates/{mandate_id}/erp/odoo
            mandate_id = full_profile.get("_mandate_id")
            client_id = full_profile.get("_client_id")
            bank_erp = full_profile.get("mandate_bank_erp", "").lower()  # Type d'ERP (odoo, sage, etc.)
            
            if not mandate_id or not client_id:
                logger.error(f"❌ [ERP] Missing mandate_id or client_id in full_profile")
                return None
            
            if not bank_erp:
                logger.error(f"❌ [ERP] Missing mandate_bank_erp in full_profile")
                return None
            
            # Construire le chemin complet du document ERP
            if user_id:
                erp_doc_path = f"clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/erp/{bank_erp}"
            else:
                erp_doc_path = f"bo_clients/{client_id}/mandates/{mandate_id}/erp/{bank_erp}"
            
            # 4. Lire le document ERP directement
            erp_doc = db.document(erp_doc_path).get()
            
            if not erp_doc.exists:
                logger.error(f"❌ [ERP] Document ERP not found at path: {erp_doc_path}")
                return None
            
            erp_data = erp_doc.to_dict()
            
            # 5. Extraire les credentials depuis le document ERP
            # Les champs sont SANS préfixe "erp_" dans le document
            odoo_url = erp_data.get("odoo_url")
            odoo_db_name = erp_data.get("odoo_db")
            odoo_username = erp_data.get("odoo_username")
            odoo_company_name = erp_data.get("odoo_company_name")
            secret_manager_name = erp_data.get("secret_manager")

            # 6. Vérifier que tous les paramètres sont présents
            if not all([odoo_url, odoo_db_name, odoo_username, odoo_company_name, secret_manager_name]):
                missing = []
                if not odoo_url: missing.append("odoo_url")
                if not odoo_db_name: missing.append("odoo_db")
                if not odoo_username: missing.append("odoo_username")
                if not odoo_company_name: missing.append("odoo_company_name")
                if not secret_manager_name: missing.append("secret_manager")

                logger.error(f"❌ [ERP] Missing credentials in document {erp_doc_path}: {', '.join(missing)}")
                return None

            # 7. Récupérer le mot de passe depuis Secret Manager
            try:
                erp_api_key = get_secret(secret_manager_name)
            except Exception as e:
                logger.error(f"❌ [ERP] Failed to get secret {secret_manager_name}: {e}")
                return None

            logger.info(
                f"✅ [ERP] Credentials loaded from mandate - "
                f"company={odoo_company_name}, url={odoo_url}, "
                f"db={odoo_db_name}, username={odoo_username}, "
                f"secret_name={secret_manager_name}"
            )

            return {
                "erp_type": "odoo",
                "url": odoo_url,
                "db_name": odoo_db_name,
                "username": odoo_username,
                "password": erp_api_key,
                "odoo_company_name": odoo_company_name
            }

        except Exception as e:
            logger.error(f"❌ [ERP] Error getting credentials: {e}", exc_info=True)
            return None

    def get_mandate_path(self, user_id: str, company_id: str, client_uuid: Optional[str] = None) -> Optional[str]:
        """Construit le mandate_path Firestore (chemin réel) pour (user_id, company_id).

        Utilise la même logique de résolution que _get_erp_credentials (client_uuid -> reconstruct_full_client_profile).
        """
        try:
            from .firebase_providers import FirebaseManagement

            db = get_firestore()
            firebase_service = FirebaseManagement()

            resolved_client_uuid = client_uuid

            if not resolved_client_uuid:
                lookup = firebase_service.resolve_client_by_contact_space(user_id, company_id)
                if lookup and lookup.get("client_uuid"):
                    resolved_client_uuid = lookup["client_uuid"]

            if not resolved_client_uuid:
                doc_ref = db.collection(f"clients/{user_id}/bo_clients").document(user_id)
                doc = doc_ref.get()
                if doc.exists:
                    resolved_client_uuid = (doc.to_dict() or {}).get("client_uuid")

            if not resolved_client_uuid:
                logger.error("❌ [ERP] client_uuid not found for mandate_path user=%s company=%s", user_id, company_id)
                return None

            full_profile = firebase_service.reconstruct_full_client_profile(
                user_id,
                resolved_client_uuid,
                company_id,
            )

            if not full_profile:
                logger.error("❌ [ERP] Full profile not found for user=%s company=%s", user_id, company_id)
                return None

            mandate_id = full_profile.get("_mandate_id")
            client_id = full_profile.get("_client_id")

            if not mandate_id or not client_id:
                logger.error("❌ [ERP] Missing mandate_id/client_id in full_profile for user=%s company=%s", user_id, company_id)
                return None

            return f"clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}"

        except Exception as e:
            logger.error("❌ [ERP] Error building mandate_path: %s", e, exc_info=True)
            return None

    def get_connection(self, user_id: str, company_id: str, client_uuid: Optional[str] = None) -> Optional[ODOO_KLK_VISION]:


        """
        Récupère ou crée une connexion ERP pour un utilisateur/société.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            client_uuid: Identifiant client explicite (optionnel)

        Returns:
            Instance ODOO_KLK_VISION ou None si échec
        """
        cache_key = self._build_cache_key(user_id, company_id)

        # 1. Vérifier le cache
        with self._lock:
            if cache_key in self._connections:
                connection, created_at = self._connections[cache_key]

                # Vérifier si la connexion n'est pas expirée
                if datetime.now() - created_at <= timedelta(minutes=self._ttl_minutes):
                    logger.info(f"✅ [ERP] Cache hit: {cache_key}")
                    return connection
                else:
                    logger.info(f"⏰ [ERP] Cache expired: {cache_key}")
                    del self._connections[cache_key]

        # 2. Récupérer les credentials
        logger.info(f"🔍 [ERP] Cache miss, fetching credentials: {cache_key}")
        credentials = self._get_erp_credentials(user_id, company_id, client_uuid=client_uuid)

        if not credentials:
            return None

        # 3. Créer la connexion
        try:
            logger.info(f"🔄 [ERP] Creating new connection: {cache_key}")
            connection = ODOO_KLK_VISION(
                url=credentials["url"],
                db=credentials["db_name"],
                username=credentials["username"],
                password=credentials["password"],
                odoo_company_name=credentials["odoo_company_name"]
            )

            # 4. Tester la connexion
            test_result = connection.test_connection()

            if not test_result.get("success"):
                logger.error(f"❌ [ERP] Connection test failed: {test_result.get('message')}")
                return None

            # 5. Mettre en cache
            with self._lock:
                self._connections[cache_key] = (connection, datetime.now())
                logger.info(f"✅ [ERP] Connection cached: {cache_key}")

            # 6. Nettoyer les connexions expirées
            self._cleanup_expired_connections()

            return connection

        except Exception as e:
            logger.error(f"❌ [ERP] Error creating connection: {e}", exc_info=True)
            return None

    def invalidate_connection(self, user_id: str, company_id: str, client_uuid: Optional[str] = None):
        """Invalide une connexion du cache (changement de société, déconnexion, etc.)."""
        cache_key = self._build_cache_key(user_id, company_id)

        with self._lock:
            if cache_key in self._connections:
                del self._connections[cache_key]
                logger.info(f"🗑️ [ERP] Connection invalidated: {cache_key}")

    def clear_all(self):
        """Vide tout le cache de connexions."""
        with self._lock:
            count = len(self._connections)
            self._connections.clear()
            logger.info(f"🧹 [ERP] Cleared {count} connections from cache")


# ═══════════════════════════════════════════════════════════════
# SERVICE ERP PRINCIPAL
# ═══════════════════════════════════════════════════════════════

class ERPService:
    """
    Service ERP principal exposé via RPC.

    Toutes les méthodes suivent le pattern:
    method(user_id: str, company_id: str, **kwargs) -> Any

    Le user_id et company_id sont utilisés pour récupérer la connexion appropriée.
    """

    _manager: Optional[ERPConnectionManager] = None
    _lock = threading.Lock()

    @classmethod
    def _get_manager(cls) -> ERPConnectionManager:
        """Récupère le manager de connexions (singleton)."""
        if cls._manager is None:
            with cls._lock:
                if cls._manager is None:
                    cls._manager = ERPConnectionManager()
        return cls._manager

    @classmethod
    def get_odoo_bank_statement_move_line_not_rec(
        cls,
        user_id: str,
        company_id: str,
        client_uuid: Optional[str] = None,
        journal_id: Optional[int] = None,
        reconciled: Optional[bool] = None
    ) -> list:
        """
        Récupère les mouvements bancaires non réconciliés depuis Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            client_uuid: Identifiant client explicite (optionnel)
            journal_id: ID du journal bancaire (optionnel)
            reconciled: Filtre sur le statut de réconciliation (optionnel)

        Returns:
            Liste des mouvements bancaires (sans DataFrame pour compatibilité JSON)
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id, client_uuid=client_uuid)

        if not connection:
            raise Exception("Failed to connect to ERP")

        # Récupérer les données et ignorer le DataFrame (non-sérialisable)
        lines, _ = connection.get_odoo_bank_statement_move_line_not_rec(
            journal_id=journal_id,
            reconciled=reconciled
        )
        
        return lines

    @classmethod
    def test_connection(
        cls,
        user_id: Optional[str] = None,
        company_id: Optional[str] = None,
        client_uuid: Optional[str] = None,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        company_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Teste la connexion à l'ERP.

        Args:
            user_id: ID Firebase de l'utilisateur (optionnel si credentials fournis)
            company_id: ID de la société (optionnel si credentials fournis)
            client_uuid: Identifiant client explicite (optionnel)
            url: URL du serveur Odoo (mode direct)
            db: Nom de la base de données (mode direct)
            username: Nom d'utilisateur (mode direct)
            password: Mot de passe / API key (mode direct)
            company_name: Nom de la société (mode direct, optionnel)

        Returns:
            Dict avec success (bool) et message (str)
        """
        # Mode direct : credentials fournis explicitement (onboarding / tests)
        if any([url, db, username, password, company_name]):
            missing = [
                name for name, value in (
                    ("url", url),
                    ("db", db),
                    ("username", username),
                    ("password", password)
                ) if not value
            ]

            if missing:
                message = (
                    "Credentials incomplets pour test_connection direct: "
                    + ", ".join(missing)
                )
                logger.error(f"❌ [ERP] {message}")
                return {"success": False, "message": message}

            try:
                logger.info("🔌 [ERP] Test connexion direct avec credentials fournis")
                temp_connection = ODOO_KLK_VISION(
                    url=url,
                    db=db,
                    username=username,
                    password=password,
                    odoo_company_name=company_name
                )
                return temp_connection.test_connection()
            except Exception as e:
                logger.error(f"❌ [ERP] Direct connection test failed: {e}", exc_info=True)
                return {"success": False, "message": str(e)}

        # Mode standard : récupérer credentials et tester sans créer de cache
        if not user_id or not company_id:
            message = "user_id et company_id requis si les credentials ne sont pas fournis"
            logger.error(f"❌ [ERP] {message}")
            return {"success": False, "message": message}

        # Récupérer les credentials sans utiliser le cache de connexions
        manager = cls._get_manager()
        credentials = manager._get_erp_credentials(user_id, company_id, client_uuid=client_uuid)

        if not credentials:
            return {"success": False, "message": "Failed to get ERP credentials"}

        # Créer une connexion temporaire uniquement pour le test (pas de cache)
        try:
            logger.info("🔌 [ERP] Test connexion temporaire (sans cache)")
            temp_connection = ODOO_KLK_VISION(
                url=credentials["url"],
                db=credentials["db_name"],
                username=credentials["username"],
                password=credentials["password"],
                odoo_company_name=credentials["odoo_company_name"]
            )
            return temp_connection.test_connection()
        except Exception as e:
            logger.error(f"❌ [ERP] Test connection failed: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    @classmethod
    def get_pl_metrics(
        cls,
        user_id: str,
        company_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Récupère les métriques P&L (Profit & Loss).

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            start_date: Date de début (format 'YYYY-MM-DD')
            end_date: Date de fin (format 'YYYY-MM-DD')

        Returns:
            Dict contenant les métriques P&L
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            raise Exception("Failed to connect to ERP")

        return connection.get_pl_metrics(start_date=start_date, end_date=end_date)

    @classmethod
    def get_account_types(cls, user_id: str, company_id: str) -> list:
        """
        Récupère les types de comptes disponibles dans Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société

        Returns:
            Liste des types de comptes
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            raise Exception("Failed to connect to ERP")

        return connection.get_account_types()

    @classmethod
    def get_account_chart(cls, user_id: str, company_id: str, **kwargs) -> Any:
        """
        Récupère le plan comptable.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            **kwargs: Arguments additionnels (account_types, etc.)

        Returns:
            DataFrame contenant le plan comptable
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            raise Exception("Failed to connect to ERP")

        return connection.get_account_chart(**kwargs)

    @classmethod
    def update_accounts(
        cls,
        user_id: str,
        company_id: str,
        accounts_data: list
    ) -> Dict[str, Any]:
        """
        Met à jour des comptes dans Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            accounts_data: Liste des données de comptes à mettre à jour

        Returns:
            Dict avec le résultat de la mise à jour
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            raise Exception("Failed to connect to ERP")

        return connection.update_accounts(accounts_data)

    @classmethod


    @classmethod
    def update_coa_structure(
        cls,
        user_id: str,
        company_id: str,
        modified_rows: dict,
        client_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Met à jour la structure du plan comptable (COA) : Odoo + Firestore.

        - Odoo : via connection.update_accounts([...])
        - Firestore : écrit dans {mandate_path}/setup/coa (merge=True)
        """
        if not user_id or not company_id:
            raise ValueError("user_id et company_id requis")

        if not isinstance(modified_rows, dict) or not modified_rows:
            return {
                "success": True,
                "message": "No changes",
                "odoo": {"requested": 0, "result": None},
                "firebase": {"requested": 0, "doc_path": None},
                "skipped": 0,
            }

        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id, client_uuid=client_uuid)
        if not connection:
            raise Exception("Failed to connect to ERP")

        # Types Odoo supportés pour account_type
        odoo_account_types = {
            "asset_cash", "asset_current", "asset_prepayments",
            "asset_fixed", "asset_non_current", "asset_receivable",
            "liability_payable", "liability_credit_card",
            "liability_current", "liability_non_current",
            "equity_unaffected", "equity", "expense_depreciation",
            "expense", "expense_direct_cost", "income", "income_other",
        }

        # Fonctions gérées uniquement dans Firebase
        firebase_only_functions = {
            "hr_expenses",
            "general_administration_expenses",
            "corporate_tax_expenses",
        }

        def _coerce_bool(v: Any) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, int):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "yes", "oui", "y")
            return bool(v)

        odoo_updates: List[Dict[str, Any]] = []
        firebase_updates: Dict[str, Dict[str, Any]] = {}
        skipped = 0

        for account_id, account_data in modified_rows.items():
            if not isinstance(account_data, dict):
                skipped += 1
                continue

            str_id = str(account_id)
            new_function = account_data.get("new_function")
            if new_function is None:
                new_function = account_data.get("account_function")
            isactive = account_data.get("isactive")

            # Firebase update
            fb_update: Dict[str, Any] = {
                "account_id": str_id,
                "klk_account_nature": account_data.get("account_nature"),
            }
            if new_function is not None:
                fb_update["klk_account_function"] = new_function
                if new_function in odoo_account_types:
                    fb_update["account_type"] = new_function
            if isactive is not None:
                fb_update["isactive"] = _coerce_bool(isactive)
            firebase_updates[str_id] = fb_update

            # Odoo update (si nécessaire)
            try:
                acc_int = int(str_id)
            except Exception:
                skipped += 1
                continue

            needs_odoo = False
            odoo_update: Dict[str, Any] = {"account_id": acc_int}

            if isactive is not None:
                odoo_update["deprecated"] = not _coerce_bool(isactive)
                needs_odoo = True

            if new_function and new_function in odoo_account_types:
                odoo_update["account_type"] = new_function
                needs_odoo = True

            # Si uniquement une fonction administrative et pas de changement isactive → pas d'update Odoo
            if (new_function in firebase_only_functions) and (isactive is None):
                needs_odoo = False

            if needs_odoo:
                odoo_updates.append(odoo_update)

        odoo_result = None
        if odoo_updates:
            logger.info("🔄 [ERP] update_coa_structure: updating %s accounts in Odoo", len(odoo_updates))
            odoo_result = connection.update_accounts(odoo_updates)

        mandate_path = manager.get_mandate_path(user_id, company_id, client_uuid=client_uuid)
        if not mandate_path:
            raise Exception("Failed to resolve mandate_path for Firestore update")

        doc_path = f"{mandate_path}/setup/coa"
        if firebase_updates:
            db = get_firestore()
            payload = {str(k): v for k, v in firebase_updates.items()}
            db.document(doc_path).set(payload, merge=True)

        return {
            "success": True,
            "message": "COA structure updated",
            "odoo": {"requested": len(odoo_updates), "result": odoo_result},
            "firebase": {"requested": len(firebase_updates), "doc_path": doc_path},
            "skipped": skipped,
        }

    def invalidate_connection(cls, user_id: str, company_id: str):
        """
        Invalide la connexion ERP pour un utilisateur/société.
        Utile lors d'un changement de société ou de déconnexion.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
        """
        manager = cls._get_manager()
        manager.invalidate_connection(user_id, company_id)

    @classmethod
    def clear_all_connections(cls):
        """Vide toutes les connexions ERP du cache."""
        manager = cls._get_manager()
        manager.clear_all()


def get_erp_service() -> ERPService:
    """Helper pour récupérer le service ERP (utilisé dans main.py)."""
    return ERPService
