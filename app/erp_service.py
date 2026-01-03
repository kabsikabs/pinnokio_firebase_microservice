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

    # ═══════════════════════════════════════════════════════════════
    # ASSET MANAGEMENT METHODS
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def list_asset_models(cls, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        """
        Liste les modèles d'actifs (state='model') depuis Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société

        Returns:
            Liste des modèles d'actifs avec leurs détails
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            logger.error(f"❌ [ERP] list_asset_models: No connection for user={user_id} company={company_id}")
            return []

        try:
            models = connection.list_asset_models()
            logger.info(f"✅ [ERP] list_asset_models: Found {len(models)} models")
            return models
        except Exception as e:
            logger.error(f"❌ [ERP] list_asset_models error: {e}")
            return []

    @classmethod
    def create_asset_model_with_journal(
        cls,
        user_id: str,
        company_id: str,
        name: str,
        account_asset_id: int,
        account_depreciation_id: int,
        account_depreciation_expense_id: int,
        depreciation_method: str,
        method_number: int,
        method_period: int,
        is_model: bool = True
    ) -> Dict[str, Any]:
        """
        Crée un journal et un modèle d'actif dans Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            name: Nom du modèle d'actif et du journal
            account_asset_id: ID du compte d'immobilisation
            account_depreciation_id: ID du compte de dépréciation
            account_depreciation_expense_id: ID du compte de charges
            depreciation_method: Méthode d'amortissement ('linear', 'degressive')
            method_number: Nombre de périodes (durée totale)
            method_period: Durée de chaque période en mois (1, 3, 6, 12)
            is_model: Indique si un modèle d'actif doit être créé

        Returns:
            Dict avec les détails du journal et du modèle créés
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            logger.error(f"❌ [ERP] create_asset_model_with_journal: No connection for user={user_id} company={company_id}")
            return {"success": False, "error": "No ERP connection"}

        try:
            # Call the erp_manager method with 'method' parameter (Odoo expects 'method')
            result = connection.create_asset_model_with_journal(
                name=name,
                account_asset_id=account_asset_id,
                account_depreciation_id=account_depreciation_id,
                account_depreciation_expense_id=account_depreciation_expense_id,
                method=depreciation_method,  # Map depreciation_method to method for Odoo
                method_number=method_number,
                method_period=method_period,
                is_model=is_model
            )
            
            if result and not result.get("error"):
                logger.info(f"✅ [ERP] create_asset_model_with_journal: Created model '{name}'")
                return {"success": True, **result}
            else:
                logger.error(f"❌ [ERP] create_asset_model_with_journal: Failed - {result}")
                return {"success": False, "error": result.get("error", "Unknown error")}
                
        except Exception as e:
            logger.error(f"❌ [ERP] create_asset_model_with_journal error: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def update_asset_model(
        cls,
        user_id: str,
        company_id: str,
        model_id: int,
        values: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Met à jour un modèle d'actif existant dans Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            model_id: ID du modèle d'actif à mettre à jour
            values: Dict des champs à mettre à jour:
                - name (str): Nom du modèle
                - method (str): Méthode d'amortissement ('linear', 'degressive')
                - method_number (int): Nombre de périodes
                - method_period (int): Durée de chaque période (1, 3, 6, 12)

        Returns:
            Dict avec le résultat de la mise à jour
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            logger.error(f"❌ [ERP] update_asset_model: No connection for user={user_id} company={company_id}")
            return {"success": False, "error": "No ERP connection"}

        try:
            result = connection.update_asset_model(model_id=model_id, values=values)
            
            if result and result.get("success"):
                logger.info(f"✅ [ERP] update_asset_model: Updated model ID {model_id}")
                return {"success": True, **result}
            else:
                logger.error(f"❌ [ERP] update_asset_model: Failed - {result}")
                return {"success": False, "error": result.get("error", "Unknown error")}
                
        except Exception as e:
            logger.error(f"❌ [ERP] update_asset_model error: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def delete_asset_model(
        cls,
        user_id: str,
        company_id: str,
        model_id: int
    ) -> Dict[str, Any]:
        """
        Supprime un modèle d'actif dans Odoo.

        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            model_id: ID du modèle d'actif à supprimer

        Returns:
            Dict avec le résultat de la suppression
        """
        manager = cls._get_manager()
        connection = manager.get_connection(user_id, company_id)

        if not connection:
            logger.error(f"❌ [ERP] delete_asset_model: No connection for user={user_id} company={company_id}")
            return {"success": False, "error": "No ERP connection"}

        try:
            result = connection.delete_asset_model(model_id=model_id)
            
            if result and result.get("success"):
                logger.info(f"✅ [ERP] delete_asset_model: Deleted model ID {model_id}")
                return {"success": True, **result}
            else:
                logger.error(f"❌ [ERP] delete_asset_model: Failed - {result}")
                return {"success": False, "error": result.get("error", "Unknown error")}
                
        except Exception as e:
            logger.error(f"❌ [ERP] delete_asset_model error: {e}")
            return {"success": False, "error": str(e)}

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

    @classmethod
    async def sync_coa_from_erp(
        cls,
        user_id: str,
        company_id: str,
        client_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronise le plan comptable depuis l'ERP vers Firebase (PULL FROM ERP) - v2.

        Flux v2 (agentic):
        1) Extraire COA depuis ERP
        2) Comparer avec COA existant Firebase (ajout/modif/désactivation/suppression)
        3) Mapper les nouveaux/changements de `account_type` → `klk_account_nature` via agent (valeurs fixes)
        4) Mapper les comptes concernés → `klk_account_function` via agent, par nature, en utilisant
           uniquement les fonctions **actives** disponibles dans `mandate_path/setup/klk_function_name_definition`
        5) Persister les deltas dans Firebase
        6) Émettre les signaux WSS de progression
        """
        from .ws_hub import hub
        from google.cloud import firestore
        from datetime import datetime
        import asyncio
        import json
        import re
        
        # Helper pour envoyer les signaux de progression
        async def send_progress(stage: str, progress: int, message: str):
            try:
                await hub.broadcast(user_id, {
                    "type": "coa_sync_progress",
                    "stage": stage,
                    "progress": progress,
                    "message": message
                })
            except Exception as e:
                logger.warning(f"[ERP] WSS broadcast failed: {e}")
        
        if not user_id or not company_id:
            raise ValueError("user_id et company_id requis")

        manager = cls._get_manager()
        
        try:
            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 0: Signal de démarrage
            # ═══════════════════════════════════════════════════════════════
            await send_progress("starting", 0, "Démarrage de la synchronisation...")
            
            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 1: Récupérer le contexte et déterminer le type d'ERP
            # ═══════════════════════════════════════════════════════════════
            await send_progress("connecting", 5, "Connexion à l'ERP...")
            
            # Récupérer les credentials pour déterminer le type d'ERP
            credentials = manager._get_erp_credentials(user_id, company_id, client_uuid=client_uuid)
            if not credentials:
                raise Exception("Failed to get ERP credentials")
            
            erp_type = credentials.get("erp_type", "odoo").lower()
            logger.info(f"🔄 [ERP] sync_coa_from_erp: ERP type={erp_type}")
            
            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 2: Connexion et récupération du COA selon le type d'ERP
            # ═══════════════════════════════════════════════════════════════
            await send_progress("fetching_erp", 10, f"Récupération du plan comptable depuis {erp_type.upper()}...")
            
            connection = manager.get_connection(user_id, company_id, client_uuid=client_uuid)
            if not connection:
                raise Exception("Failed to connect to ERP")
            
            # Récupération conditionnelle selon le type d'ERP
            if erp_type == "odoo":
                # Appel potentiellement long + bloquant → thread
                erp_accounts = await asyncio.to_thread(connection.fetch_chart_of_account)
            # Future: elif erp_type == "sage": erp_accounts = connection.fetch_chart_of_account_sage()
            else:
                raise ValueError(f"ERP type '{erp_type}' non supporté pour sync_coa_from_erp")

            if not erp_accounts:
                await send_progress("error", 100, "Aucun compte trouvé dans l'ERP")
                return {
                    "success": False,
                    "message": "Aucun compte trouvé dans l'ERP",
                    "accounts_synced": 0,
                    "accounts_added": 0,
                    "accounts_updated": 0
                }

            logger.info(f"✅ [ERP] sync_coa_from_erp: Found {len(erp_accounts)} accounts")
            await send_progress("fetching_erp", 20, f"{len(erp_accounts)} comptes récupérés")

            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 3: Charger les définitions KLK (natures + fonctions actives) depuis Firestore
            # ═══════════════════════════════════════════════════════════════
            await send_progress("loading_definitions", 25, "Chargement des définitions de natures/fonctions...")

            mandate_path = manager.get_mandate_path(user_id, company_id, client_uuid=client_uuid)
            if not mandate_path:
                raise Exception("Failed to resolve mandate_path for Firebase update")

            db = get_firestore()
            functions_doc_path = f"{mandate_path}/setup/klk_function_name_definition"
            coa_doc_path = f"{mandate_path}/setup/coa"

            FIXED_NATURES = ["ASSET", "LIABILITY", "PROFIT_AND_LOSS", "OFF_BALANCE_SHEET"]

            def _extract_active_functions_by_nature(doc: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, str]]]:
                """Retourne {nature: {function_name: {display_name, definition}}} pour active=True (par défaut True si absent)."""
                out: Dict[str, Dict[str, Dict[str, str]]] = {k: {} for k in FIXED_NATURES}
                if not doc or not isinstance(doc, dict):
                    return out
                natures = doc.get("natures")
                if not isinstance(natures, dict):
                    return out
                for nature_key, nature_data in natures.items():
                    if nature_key not in out or not isinstance(nature_data, dict):
                        continue
                    funcs = nature_data.get("functions")
                    # Format "mandate": list[{name, display_name, definition, active, ...}]
                    if isinstance(funcs, list):
                        for fn in funcs:
                            if not isinstance(fn, dict):
                                continue
                            name = (fn.get("name") or "").strip()
                            if not name:
                                continue
                            active = fn.get("active", True)
                            if active is None:
                                active = True
                            if not bool(active):
                                continue
                            out[nature_key][name] = {
                                "display_name": fn.get("display_name", name) or name,
                                "definition": fn.get("definition", "") or "",
                            }
                    # Format "seed": dict{name: {display_name, definition, active?, ...}}
                    elif isinstance(funcs, dict):
                        for name, meta in funcs.items():
                            if not isinstance(meta, dict):
                                continue
                            name = (name or "").strip()
                            if not name:
                                continue
                            active = meta.get("active", True)
                            if active is None:
                                active = True
                            if not bool(active):
                                continue
                            out[nature_key][name] = {
                                "display_name": meta.get("display_name", name) or name,
                                "definition": meta.get("definition", "") or "",
                            }
                return out

            async def _ensure_functions_doc_exists() -> Dict[str, Any]:
                """Assure que le doc mandat des fonctions existe; fallback seed english (copie minimale) si absent."""
                def _worker():
                    fn_doc = db.document(functions_doc_path).get()
                    if fn_doc.exists and isinstance(fn_doc.to_dict(), dict) and (fn_doc.to_dict() or {}).get("natures"):
                        return fn_doc.to_dict()

                    seed_path = "settings_param/coa_mapping_settings/coa_model/english"
                    seed_doc = db.document(seed_path).get()
                    if not seed_doc.exists:
                        raise ValueError(f"Seed document not found: {seed_path}")
                    seed_data = seed_doc.to_dict() or {}

                    # Construire une version "mandat" minimale: seulement natures + functions (avec active=True par défaut)
                    natures = seed_data.get("natures") if isinstance(seed_data.get("natures"), dict) else {}
                    minimal_natures: Dict[str, Any] = {}
                    for nature_key in FIXED_NATURES:
                        nature_data = natures.get(nature_key) if isinstance(natures, dict) else None
                        if not isinstance(nature_data, dict):
                            nature_data = {}
                        nature_display_name = nature_data.get("nature_display_name", nature_key)
                        funcs = nature_data.get("functions", {})
                        functions_list: List[Dict[str, Any]] = []
                        if isinstance(funcs, dict):
                            for fn_name, meta in funcs.items():
                                if not isinstance(meta, dict):
                                    continue
                                active_val = meta.get("active")
                                if active_val is None:
                                    active_val = True
                                functions_list.append({
                                    "name": fn_name,
                                    "display_name": meta.get("display_name", fn_name),
                                    "definition": meta.get("definition", ""),
                                    "mandatory": meta.get("mandatory", False),
                                    "active": bool(active_val),
                                })
                        elif isinstance(funcs, list):
                            for fn in funcs:
                                if not isinstance(fn, dict):
                                    continue
                                fn_name = fn.get("name")
                                if not fn_name:
                                    continue
                                active_val = fn.get("active")
                                if active_val is None:
                                    active_val = True
                                functions_list.append({
                                    "name": fn_name,
                                    "display_name": fn.get("display_name", fn_name),
                                    "definition": fn.get("definition", ""),
                                    "mandatory": fn.get("mandatory", False),
                                    "active": bool(active_val),
                                })

                        minimal_natures[nature_key] = {
                            "nature_name": nature_key,
                            "nature_display_name": nature_display_name,
                            "functions": functions_list,
                        }

                    payload = {
                        "schema_version": "v2",
                        "language": seed_data.get("language", "English"),
                        "updated_at": datetime.utcnow().isoformat(),
                        "natures": minimal_natures,
                    }
                    db.document(functions_doc_path).set(payload, merge=False)
                    return payload

                return await asyncio.to_thread(_worker)

            functions_doc = await _ensure_functions_doc_exists()
            active_functions_by_nature = _extract_active_functions_by_nature(functions_doc)

            total_active = sum(len(v) for v in active_functions_by_nature.values())
            await send_progress("loading_definitions", 30, f"Définitions chargées ({total_active} fonctions actives)")

            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 4: Lire COA existant dans Firebase + calcul deltas
            # ═══════════════════════════════════════════════════════════════
            await send_progress("diffing", 35, "Comparaison ERP ↔ Firebase (deltas)...")

            def _safe_bool(v: Any) -> bool:
                if isinstance(v, bool):
                    return v
                if isinstance(v, int):
                    return bool(v)
                if isinstance(v, str):
                    return v.strip().lower() in ("true", "1", "yes", "oui", "y")
                return bool(v)

            def _worker_load_existing():
                doc = db.document(coa_doc_path).get()
                return doc.to_dict() if doc.exists else {}

            existing_data = await asyncio.to_thread(_worker_load_existing)
            if not isinstance(existing_data, dict):
                existing_data = {}

            # Filtrer les entrées "comptes" (évite d'embarquer des clés non-compte dans les suppressions)
            existing_account_ids = set()
            for k, v in existing_data.items():
                if not isinstance(v, dict):
                    continue
                key = str(k).strip()
                acc_id = str((v or {}).get("account_id") or "").strip()
                # Convention: la clé Firestore du compte = account_id
                if key and acc_id and key == acc_id:
                    existing_account_ids.add(key)

            erp_by_id: Dict[str, Dict[str, Any]] = {}
            for acc in erp_accounts:
                acc_id = str((acc or {}).get("id") or "").strip()
                if not acc_id:
                    continue
                erp_by_id[acc_id] = acc

            erp_account_ids = set(erp_by_id.keys())
            deleted_ids = sorted(list(existing_account_ids - erp_account_ids))

            # Comptes à upsert + comptes nécessitant (re)mapping
            to_upsert: Dict[str, Dict[str, Any]] = {}
            mapping_nature_needed: List[Dict[str, Any]] = []
            mapping_function_needed: List[Dict[str, Any]] = []

            # Static mapping rapide (réduit les tokens). Pour les types inconnus, agent.
            STATIC_TYPE_TO_NATURE = {
                # ASSET
                "asset_cash": "ASSET",
                "asset_current": "ASSET",
                "asset_prepayments": "ASSET",
                "asset_fixed": "ASSET",
                "asset_non_current": "ASSET",
                "asset_receivable": "ASSET",
                # LIABILITY
                "liability_payable": "LIABILITY",
                "liability_credit_card": "LIABILITY",
                "liability_current": "LIABILITY",
                "liability_non_current": "LIABILITY",
                "equity": "LIABILITY",
                "equity_unaffected": "LIABILITY",
                # PROFIT_AND_LOSS
                "income": "PROFIT_AND_LOSS",
                "income_other": "PROFIT_AND_LOSS",
                "expense": "PROFIT_AND_LOSS",
                "expense_depreciation": "PROFIT_AND_LOSS",
                "expense_direct_cost": "PROFIT_AND_LOSS",
                # OFF_BALANCE_SHEET (si ERP le supporte)
                "off_balance": "OFF_BALANCE_SHEET",
            }

            # Index: active functions for validation
            active_function_names = set()
            active_function_names_by_nature = {k: set(v.keys()) for k, v in active_functions_by_nature.items()}
            for nature_key, fns in active_functions_by_nature.items():
                for fn_name in fns.keys():
                    active_function_names.add(fn_name)

            for acc_id, acc in erp_by_id.items():
                account_type = (acc or {}).get("account_type", "") or ""
                account_number = str((acc or {}).get("code", "") or "")
                account_name = (acc or {}).get("display_name", "") or ""
                deprecated = _safe_bool((acc or {}).get("deprecated", False))
                isactive = not deprecated

                existing = existing_data.get(acc_id) if isinstance(existing_data.get(acc_id), dict) else None
                is_new = existing is None

                existing_type = (existing or {}).get("account_type", "")
                existing_name = (existing or {}).get("account_name", "")
                existing_number = str((existing or {}).get("account_number", "") or "")
                existing_active = _safe_bool((existing or {}).get("isactive", True))

                # Comparaison
                type_changed = (existing_type != account_type) if not is_new else True
                label_changed = (existing_name != account_name) if not is_new else True
                number_changed = (existing_number != account_number) if not is_new else True
                active_changed = (existing_active != isactive) if not is_new else True

                # Champs KLK existants
                existing_nature = (existing or {}).get("klk_account_nature") or ""
                existing_function = (existing or {}).get("klk_account_function") or ""

                # Validité fonction: doit être active et appartenir à la nature
                existing_function_active = existing_function in active_function_names
                existing_function_allowed = (
                    (existing_nature in active_function_names_by_nature)
                    and (existing_function in active_function_names_by_nature.get(existing_nature, set()))
                )

                nature_needed = is_new or type_changed or (not existing_nature)
                function_needed = (
                    is_new
                    or type_changed
                    or (not existing_function)
                    or (not existing_function_active)
                    or (not existing_function_allowed)
                )

                # Si aucun delta métier et aucun remapping → on ne touche pas
                if (not is_new) and (not type_changed) and (not label_changed) and (not number_changed) and (not active_changed) and (not function_needed) and (not nature_needed):
                    continue

                # Préparer payload de base (merge) - on conserve klk_* existants, puis on écrasera si remappé
                payload = dict(existing) if isinstance(existing, dict) else {}
                payload.update({
                    "account_id": acc_id,
                    "account_number": account_number,
                    "account_name": account_name,
                    "account_type": account_type,
                    "isactive": isactive,
                })

                to_upsert[acc_id] = payload

                if nature_needed:
                    mapping_nature_needed.append({
                        "account_id": acc_id,
                        "account_type": account_type,
                        "account_number": account_number,
                        "account_name": account_name,
                    })
                if function_needed:
                    mapping_function_needed.append({
                        "account_id": acc_id,
                        "account_type": account_type,
                        "account_number": account_number,
                        "account_name": account_name,
                    })

            await send_progress(
                "diffing",
                45,
                f"Deltas: {len(to_upsert)} upserts, {len(deleted_ids)} suppressions (ERP→Firebase)",
            )

            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 5: Mapping agentic des account_type inconnus → klk_account_nature
            # ═══════════════════════════════════════════════════════════════
            await send_progress("mapping_nature", 50, "Mapping des natures (account_type → klk_account_nature)...")

            # Construire le mapping final type → nature
            types_to_map = sorted({(x.get("account_type") or "").strip() for x in mapping_nature_needed if (x.get("account_type") or "").strip()})
            type_to_nature: Dict[str, str] = {}
            unknown_types: List[str] = []
            for t in types_to_map:
                if t in STATIC_TYPE_TO_NATURE:
                    type_to_nature[t] = STATIC_TYPE_TO_NATURE[t]
                else:
                    unknown_types.append(t)

            if unknown_types:
                await send_progress("mapping_nature", 55, f"Agent: mapping de {len(unknown_types)} nouveaux account_type...")

                from .llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize, NEW_Anthropic_Agent

                def _extract_text(obj: Any) -> str:
                    if isinstance(obj, str):
                        return obj
                    if isinstance(obj, list) and obj:
                        return _extract_text(obj[0])
                    if isinstance(obj, dict):
                        for k in ("answer_text", "text", "content", "text_output"):
                            if k in obj:
                                return _extract_text(obj[k])
                    return str(obj)

                def _parse_json_from_text(txt: str) -> Dict[str, Any]:
                    if not isinstance(txt, str):
                        return {}
                    txt = txt.strip()
                    # tenter extraction du premier objet JSON
                    first = txt.find("{")
                    last = txt.rfind("}")
                    if first >= 0 and last > first:
                        chunk = txt[first:last + 1]
                        try:
                            return json.loads(chunk)
                        except Exception:
                            pass
                    # fallback regex simple
                    m = re.search(r"\{[\s\S]*\}", txt)
                    if m:
                        try:
                            return json.loads(m.group(0))
                        except Exception:
                            return {}
                    return {}

                def _nature_mapping_agent_call(types_batch: List[str]) -> Dict[str, str]:
                    system_prompt = (
                        "Tu es un expert comptable et ERP. "
                        "Tu dois mapper des valeurs ERP `account_type` vers une nature KLK.\n\n"
                        "NATURES POSSIBLES (valeurs EXACTES): ASSET, LIABILITY, PROFIT_AND_LOSS, OFF_BALANCE_SHEET.\n"
                        "RÈGLES:\n"
                        "- Retourne UNIQUEMENT un JSON strict {\"account_type\": \"NATURE\", ...}\n"
                        "- N'invente aucune autre nature.\n"
                        "- Si incertain, choisis la nature la plus plausible (souvent PROFIT_AND_LOSS).\n"
                    )
                    user_prompt = (
                        "Mappe ces account_type ERP vers une nature KLK:\n"
                        + "\n".join([f"- {t}" for t in types_batch])
                        + "\n\nRéponds en JSON strict."
                    )

                    agent = BaseAIAgent(
                        collection_name=company_id,
                        firebase_user_id=user_id,
                        job_id="coa_nature_mapping",
                    )
                    anthropic_instance = NEW_Anthropic_Agent(collection_name=company_id, job_id="coa_nature_mapping")
                    anthropic_instance.update_system_prompt(system_prompt)
                    agent.register_provider(ModelProvider.ANTHROPIC, anthropic_instance, ModelSize.SMALL)
                    agent.default_provider = ModelProvider.ANTHROPIC
                    resp = agent.process_tool_use(
                        content=user_prompt,
                        tools=[],
                        tool_mapping={},
                        size=ModelSize.SMALL,
                        provider=ModelProvider.ANTHROPIC,
                        max_tokens=2048,
                        raw_output=True,
                    )
                    try:
                        agent.flush_chat_history()
                    except Exception:
                        pass
                    txt = _extract_text(resp)
                    data = _parse_json_from_text(txt)
                    out_map: Dict[str, str] = {}
                    if isinstance(data, dict):
                        for k, v in data.items():
                            k = str(k).strip()
                            v = str(v).strip().upper()
                            if k and v in FIXED_NATURES:
                                out_map[k] = v
                    return out_map

                # Batch pour éviter prompts trop longs
                BATCH = 20
                for i in range(0, len(unknown_types), BATCH):
                    batch = unknown_types[i:i + BATCH]
                    mapped = await asyncio.to_thread(_nature_mapping_agent_call, batch)
                    type_to_nature.update(mapped)

                # Fallback: tout type inconnu non mappé → PROFIT_AND_LOSS
                for t in unknown_types:
                    if t not in type_to_nature:
                        type_to_nature[t] = "PROFIT_AND_LOSS"

            # Appliquer klk_account_nature aux comptes concernés
            for item in mapping_nature_needed:
                acc_id = item["account_id"]
                t = (item.get("account_type") or "").strip()
                nature = type_to_nature.get(t) or STATIC_TYPE_TO_NATURE.get(t) or "PROFIT_AND_LOSS"
                if acc_id in to_upsert:
                    to_upsert[acc_id]["klk_account_nature"] = nature

            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 6: Mapping agentic des comptes → klk_account_function (par nature)
            # ═══════════════════════════════════════════════════════════════
            await send_progress("mapping_function", 65, "Mapping des fonctions (comptes → klk_account_function)...")

            # Regrouper les comptes à mapper par nature (la nature doit être déterminée à ce stade)
            accounts_by_nature: Dict[str, List[Dict[str, Any]]] = {k: [] for k in FIXED_NATURES}
            for item in mapping_function_needed:
                acc_id = item["account_id"]
                payload = to_upsert.get(acc_id) or {}
                nature = (payload.get("klk_account_nature") or "").strip() or "PROFIT_AND_LOSS"
                if nature not in accounts_by_nature:
                    nature = "PROFIT_AND_LOSS"
                accounts_by_nature[nature].append(item)

            from .llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize, NEW_Anthropic_Agent

            def _extract_text(obj: Any) -> str:
                if isinstance(obj, str):
                    return obj
                if isinstance(obj, list) and obj:
                    return _extract_text(obj[0])
                if isinstance(obj, dict):
                    for k in ("answer_text", "text", "content", "text_output"):
                        if k in obj:
                            return _extract_text(obj[k])
                return str(obj)

            def _parse_json_from_text(txt: str) -> Dict[str, Any]:
                if not isinstance(txt, str):
                    return {}
                txt = txt.strip()
                first = txt.find("{")
                last = txt.rfind("}")
                if first >= 0 and last > first:
                    chunk = txt[first:last + 1]
                    try:
                        return json.loads(chunk)
                    except Exception:
                        pass
                m = re.search(r"\{[\s\S]*\}", txt)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except Exception:
                        return {}
                return {}

            def _map_batch_accounts_to_functions(nature: str, allowed: Dict[str, Dict[str, str]], batch_items: List[Dict[str, Any]]) -> Dict[str, str]:
                allowed_names = list(allowed.keys())
                if not allowed_names:
                    return {}

                # Contexte fonctions
                functions_context = "\n".join([
                    f"- {fn_name}: {meta.get('display_name','')} — {meta.get('definition','')}"
                    for fn_name, meta in allowed.items()
                ])

                accounts_context = "\n".join([
                    f"- account_id={x['account_id']} | account_number={x.get('account_number','')} | account_name={x.get('account_name','')} | account_type={x.get('account_type','')}"
                    for x in batch_items
                ])

                system_prompt = (
                    "Tu es un expert comptable. Tu dois mapper des comptes à une klk_account_function.\n"
                    f"NATURE: {nature}\n"
                    "RÈGLES:\n"
                    "- Choisis UNIQUEMENT parmi les fonctions autorisées fournies.\n"
                    "- Retourne UNIQUEMENT un JSON strict {\"account_id\": \"klk_account_function\", ...}\n"
                    "- N'inclus aucun texte hors JSON.\n"
                )
                user_prompt = (
                    "Fonctions autorisées (name, display_name, definition):\n"
                    f"{functions_context}\n\n"
                    "Comptes à mapper:\n"
                    f"{accounts_context}\n\n"
                    "Réponds en JSON strict avec account_id comme clés."
                )

                agent = BaseAIAgent(
                    collection_name=company_id,
                    firebase_user_id=user_id,
                    job_id=f"coa_function_mapping_{nature.lower()}",
                )
                anthropic_instance = NEW_Anthropic_Agent(collection_name=company_id, job_id=f"coa_function_mapping_{nature.lower()}")
                anthropic_instance.update_system_prompt(system_prompt)
                agent.register_provider(ModelProvider.ANTHROPIC, anthropic_instance, ModelSize.SMALL)
                agent.default_provider = ModelProvider.ANTHROPIC

                resp = agent.process_tool_use(
                    content=user_prompt,
                    tools=[],
                    tool_mapping={},
                    size=ModelSize.SMALL,
                    provider=ModelProvider.ANTHROPIC,
                    max_tokens=4096,
                    raw_output=True,
                )
                try:
                    agent.flush_chat_history()
                except Exception:
                    pass

                txt = _extract_text(resp)
                data = _parse_json_from_text(txt)
                out_map: Dict[str, str] = {}

                batch_ids = {x["account_id"] for x in batch_items if x.get("account_id")}
                for k, v in (data.items() if isinstance(data, dict) else []):
                    acc_key = str(k).strip()
                    fn = str(v).strip()
                    if acc_key in batch_ids and fn in allowed:
                        out_map[acc_key] = fn

                return out_map

            # Exécuter par nature + batch
            BATCH_SIZE = 25
            total_to_map = sum(len(v) for v in accounts_by_nature.values())
            mapped_count = 0

            for nature, items in accounts_by_nature.items():
                if not items:
                    continue
                allowed = active_functions_by_nature.get(nature, {}) or {}
                if not allowed:
                    # Pas de fonctions actives dans cette nature → fallback (ne bloque pas)
                    logger.warning("[ERP] No active functions for nature=%s, skipping agent mapping for %s accounts", nature, len(items))
                    continue

                for i in range(0, len(items), BATCH_SIZE):
                    batch = items[i:i + BATCH_SIZE]
                    mapped = await asyncio.to_thread(_map_batch_accounts_to_functions, nature, allowed, batch)
                    mapped_count += len(mapped)
                    # Appliquer le mapping aux payloads
                    for acc_id, fn_name in mapped.items():
                        if acc_id in to_upsert:
                            to_upsert[acc_id]["klk_account_function"] = fn_name
                    # Progress
                    if total_to_map:
                        pct = 65 + int((mapped_count / max(total_to_map, 1)) * 15)
                        await send_progress("mapping_function", min(pct, 80), f"Fonctions mappées: {mapped_count}/{total_to_map}")

            # Fallback final: si certains comptes n'ont toujours pas de klk_account_function
            for acc_id, payload in list(to_upsert.items()):
                if not payload.get("klk_account_function"):
                    nature = (payload.get("klk_account_nature") or "PROFIT_AND_LOSS").strip()
                    allowed = active_functions_by_nature.get(nature, {}) or {}
                    if allowed:
                        payload["klk_account_function"] = next(iter(allowed.keys()))
                    else:
                        payload["klk_account_function"] = payload.get("account_type") or "expense"

            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 7: Persister deltas dans Firebase (upsert + deletions)
            # ═══════════════════════════════════════════════════════════════
            await send_progress("saving", 85, "Sauvegarde dans Firebase...")

            accounts_added = 0
            accounts_updated = 0
            accounts_deactivated = 0
            for acc_id, payload in to_upsert.items():
                if acc_id not in existing_data:
                    accounts_added += 1
                else:
                    accounts_updated += 1
                if (acc_id in existing_data) and _safe_bool((existing_data.get(acc_id) or {}).get("isactive", True)) and (not _safe_bool(payload.get("isactive", True))):
                    accounts_deactivated += 1

            def _worker_write():
                if to_upsert:
                    db.document(coa_doc_path).set({k: v for k, v in to_upsert.items()}, merge=True)
                # suppressions
                if deleted_ids:
                    doc_ref = db.document(coa_doc_path)
                    # update échoue si doc absent → ignorer si inexistant
                    if doc_ref.get().exists:
                        # Firestore limite la taille d'un update → on chunk
                        chunk_size = 200
                        for i in range(0, len(deleted_ids), chunk_size):
                            chunk = deleted_ids[i:i + chunk_size]
                            deletions = {acc_id: firestore.DELETE_FIELD for acc_id in chunk}
                            doc_ref.update(deletions)

            await asyncio.to_thread(_worker_write)

            # ═══════════════════════════════════════════════════════════════
            # ÉTAPE 8: Signal de complétion
            # ═══════════════════════════════════════════════════════════════
            await send_progress("complete", 100, "Synchronisation terminée avec succès!")

            try:
                await hub.broadcast(user_id, {
                    "type": "coa_sync_complete",
                    "success": True,
                    "accounts_synced": len(erp_account_ids),
                    "accounts_added": accounts_added,
                    "accounts_updated": accounts_updated,
                    "accounts_deleted": len(deleted_ids),
                    "accounts_deactivated": accounts_deactivated,
                })
            except Exception:
                pass

            return {
                "success": True,
                "message": "Plan comptable synchronisé avec succès",
                "accounts_synced": len(erp_account_ids),
                "accounts_added": accounts_added,
                "accounts_updated": accounts_updated,
                "accounts_deleted": len(deleted_ids),
                "accounts_deactivated": accounts_deactivated,
                "doc_path": coa_doc_path,
                "functions_doc_path": functions_doc_path,
            }

        except Exception as e:
            logger.error(f"❌ [ERP] sync_coa_from_erp error: {e}", exc_info=True)
            
            # Signal d'erreur
            try:
                await hub.broadcast(user_id, {
                    "type": "coa_sync_complete",
                    "success": False,
                    "error": str(e)
                })
            except Exception:
                pass
            
            return {
                "success": False,
                "message": f"Erreur lors de la synchronisation: {str(e)}",
                "accounts_synced": 0,
                "accounts_added": 0,
                "accounts_updated": 0
            }

    @classmethod
    async def _enrich_expense_accounts_with_ai(
        cls,
        user_id: str,
        company_id: str,
        expense_accounts: List[Dict[str, str]],
        send_progress: Any,
    ) -> Dict[str, str]:
        """
        Enrichit les comptes de charges via l'Agent IA (BaseAIAgent).
        
        Utilise l'architecture standard BaseAIAgent avec:
        - process_tool_use() pour les appels LLM
        - size=ModelSize.SMALL pour rapidité et coût réduit
        - Capture des tokens via get_token_usage_by_provider()
        
        Mode BACKEND isolé: Ne pollue pas les sessions de chat.
        
        Args:
            user_id: ID Firebase de l'utilisateur
            company_id: ID de la société
            expense_accounts: Liste des comptes à enrichir
            send_progress: Callback pour envoyer les signaux de progression
            
        Returns:
            Dict {account_id: klk_account_function}
        """
        import asyncio
        import json
        import re
        from .llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize, NEW_Anthropic_Agent
        
        # Catégories disponibles pour l'enrichissement
        expense_function_list = [
            'hr_expenses',
            'general_administration_expenses', 
            'corporate_tax_expenses',
            'expense'  # Fallback
        ]
        
        # Préparer le texte des comptes pour l'IA
        accounts_text = "\n".join([
            f"- {acc['account_number']}: {acc['account_name']}"
            for acc in expense_accounts
        ])
        
        # Prompt système pour l'agent
        system_prompt = """Tu es un assistant comptable expert. Tu dois classifier des comptes de charges en catégories.

CATÉGORIES DISPONIBLES:
- hr_expenses: Charges liées aux ressources humaines (salaires, charges sociales, formation, etc.)
- general_administration_expenses: Charges administratives générales (loyer, fournitures, honoraires, etc.)
- corporate_tax_expenses: Charges fiscales de l'entreprise (impôts, taxes, etc.)
- expense: Autres charges non classifiables

RÈGLES:
1. Analyse le numéro et le nom de chaque compte
2. Attribue la catégorie la plus appropriée
3. En cas de doute, utilise "expense" comme valeur par défaut
4. Réponds UNIQUEMENT avec le JSON demandé, sans texte supplémentaire"""

        user_prompt = f"""Voici {len(expense_accounts)} comptes de charges à classifier:

{accounts_text}

Pour chaque compte, indique la catégorie appropriée parmi: {expense_function_list}

Réponds au format JSON strict:
{{
  "numéro_compte_1": "catégorie",
  "numéro_compte_2": "catégorie"
}}"""

        try:
            await send_progress("enriching_expenses", 45, "Agent IA en cours d'analyse...")
            
            # ═══════════════════════════════════════════════════════════════
            # CRÉER UN BaseAIAgent ISOLÉ (mode BACKEND, non lié aux sessions de chat)
            # ═══════════════════════════════════════════════════════════════
            agent = BaseAIAgent(
                collection_name=company_id,
                firebase_user_id=user_id,
                job_id=f"coa_enrichment"
            )
            
            # ⭐ ENREGISTRER L'INSTANCE DU PROVIDER ANTHROPIC
            anthropic_instance = NEW_Anthropic_Agent(
                collection_name=company_id,
                job_id="coa_enrichment"
            )
            anthropic_instance.update_system_prompt(system_prompt)
            agent.register_provider(ModelProvider.ANTHROPIC, anthropic_instance, ModelSize.SMALL)
            agent.default_provider = ModelProvider.ANTHROPIC
            
            await send_progress("enriching_expenses", 55, "Traitement par l'IA...")
            
            # ═══════════════════════════════════════════════════════════════
            # APPEL LLM VIA process_tool_use (méthode standard)
            # Utilise size=ModelSize.SMALL (Claude Haiku) pour rapidité et coût
            # ═══════════════════════════════════════════════════════════════
            def _sync_call():
                return agent.process_tool_use(
                    content=user_prompt,
                    tools=[],  # Pas d'outils, juste génération de texte
                    tool_mapping={},
                    size=ModelSize.SMALL,  # Claude Haiku pour rapidité
                    provider=ModelProvider.ANTHROPIC,
                    max_tokens=4096,
                    raw_output=True
                )
            
            response = await asyncio.to_thread(_sync_call)
            
            if not response:
                raise ValueError("Réponse vide de l'agent IA")
            
            # ═══════════════════════════════════════════════════════════════
            # CAPTURER LES TOKENS UTILISÉS
            # ═══════════════════════════════════════════════════════════════
            try:
                token_usage = agent.get_token_usage_by_provider()
                if token_usage:
                    logger.info(f"📊 [ERP] Token usage: {token_usage}")
            except Exception as token_err:
                logger.warning(f"[ERP] Failed to capture token usage: {token_err}")
            
            await send_progress("enriching_expenses", 70, "Analyse des résultats...")
            
            # ═══════════════════════════════════════════════════════════════
            # EXTRAIRE LE TEXTE DE LA RÉPONSE (structure complexe possible)
            # Format possible: [{'text_output': {'content': {'answer_text': '...'}}}]
            # ═══════════════════════════════════════════════════════════════
            response_text = ""
            
            def extract_json_text(obj):
                """Extrait récursivement le texte JSON de la réponse."""
                if isinstance(obj, str):
                    return obj
                if isinstance(obj, list) and len(obj) > 0:
                    return extract_json_text(obj[0])
                if isinstance(obj, dict):
                    # Priorité: answer_text > text > content > text_output
                    if 'answer_text' in obj:
                        return extract_json_text(obj['answer_text'])
                    if 'text' in obj:
                        return extract_json_text(obj['text'])
                    if 'content' in obj:
                        return extract_json_text(obj['content'])
                    if 'text_output' in obj:
                        return extract_json_text(obj['text_output'])
                return str(obj)
            
            response_text = extract_json_text(response)
            
            logger.info(f"[ERP] AI response preview: {response_text[:200]}...")
            
            # ═══════════════════════════════════════════════════════════════
            # PARSER LA RÉPONSE JSON
            # ═══════════════════════════════════════════════════════════════
            # Extraire le JSON de la réponse (peut être entouré de texte)
            json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
            if json_match:
                result_map = json.loads(json_match.group())
            else:
                # Essayer de parser la réponse complète
                try:
                    result_map = json.loads(response_text.strip())
                except json.JSONDecodeError:
                    raise ValueError(f"Impossible de parser la réponse de l'IA: {response_text[:200]}")
            
            # Mapper account_number → account_id
            number_to_id = {acc["account_number"]: acc["account_id"] for acc in expense_accounts}
            enriched = {}
            
            for acc_number, function in result_map.items():
                acc_id = number_to_id.get(str(acc_number))
                if acc_id and function in expense_function_list:
                    enriched[acc_id] = function
                elif acc_id:
                    enriched[acc_id] = "expense"  # Fallback si catégorie invalide
            
            # Pour les comptes non enrichis, utiliser le fallback
            for acc in expense_accounts:
                if acc["account_id"] not in enriched:
                    enriched[acc["account_id"]] = "expense"
            
            logger.info(f"✅ [ERP] AI enrichment completed: {len(enriched)} accounts mapped")
            
            # ═══════════════════════════════════════════════════════════════
            # FLUSH de l'historique de l'agent après le travail
            # ═══════════════════════════════════════════════════════════════
            try:
                agent.flush_chat_history()
                logger.info(f"🧹 [ERP] Agent chat history flushed")
            except Exception as flush_error:
                logger.warning(f"[ERP] Failed to flush agent chat history: {flush_error}")
            
            return enriched
            
        except Exception as e:
            logger.error(f"❌ [ERP] AI enrichment error: {e}", exc_info=True)
            raise


def get_erp_service() -> ERPService:
    """Helper pour récupérer le service ERP (utilisé dans main.py)."""
    return ERPService
