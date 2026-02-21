from __future__ import annotations
import time
import re
import json
import os
import threading
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable, Any,Dict
from concurrent.futures import TimeoutError as FutureTimeoutError
from firebase_admin import credentials, firestore, initialize_app,auth
import firebase_admin
from firebase_admin import credentials
from google.cloud.firestore_v1.base_query import FieldFilter
from .firebase_client import get_firestore, get_firebase_app
from .tools.g_cred import get_secret
import uuid

# Logger pour Firebase providers
logger = logging.getLogger(__name__)

try:
    import stripe  # type: ignore
except Exception:  # stripe facultatif et non utilisé immédiatement
    stripe = None  # type: ignore[assignment]

try:
    from firebase_admin import db as rtdb  # type: ignore
except Exception:
    rtdb = None  # type: ignore[assignment]


_FIREBASE_MANAGEMENT_SINGLETON: Optional["FirebaseManagement"] = None
_FIREBASE_REALTIME_SINGLETON: Optional["FirebaseRealtimeChat"] = None


class FirebaseManagement:
    """
    Gestionnaire Firebase avec pattern Singleton thread-safe.
    Garantit une seule instance avec une seule connexion Firebase et Stripe.

    Important: NE PAS renommer la classe ni les accesseurs ci-dessous.
    Collez vos méthodes métier dans la zone PASTE ZONE.
    Mapping RPC: "FIREBASE_MANAGEMENT.*"
    """

    _instance: Optional["FirebaseManagement"] = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._initialize_services()
                    self.__class__._initialized = True

    def _initialize_services(self):
        try:
            # Firebase app via singleton local
            self._initialize_firebase()
            # Firestore client via notre helper
            self.db = get_firestore()
            # Stripe
            self._initialize_stripe()
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation des services: {e}")
            raise

    def _initialize_firebase(self):
        try:
            # Utilise notre singleton d'app Firebase
            get_firebase_app()
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation Firebase: {e}")
            raise

    def _fallback_firebase_init(self):
        # Non utilisé ici car get_firebase_app() gère déjà l'initialisation
        if not firebase_admin._apps:
            try:
                secret_name = os.getenv("FIRESTORE_SERVICE_ACCOUNT_SECRET")
                if not secret_name:
                    raise RuntimeError("FIRESTORE_SERVICE_ACCOUNT_SECRET manquant")
                firestore_service_account = json.loads(get_secret(secret_name))
                cred = credentials.Certificate(firestore_service_account)
                firebase_admin.initialize_app(cred)
            except Exception as e:
                print(f"❌ Erreur fallback Firebase: {e}")
                raise

    def _initialize_stripe(self):
        try:
            # Initialiser les attributs par défaut
            self.stripe_api_key = None
            self.stripe_success_url = os.getenv("STRIPE_SUCCESS_URL", "http://localhost:3000/payment-success")
            self.stripe_cancel_url = os.getenv("STRIPE_CANCEL_URL", "http://localhost:3000/payment-canceled")
            
            secret_name = os.getenv("STRIPE_KEYS")
            if not secret_name:
                print("⚠️  STRIPE_KEYS non configuré - Stripe désactivé")
                return
                
            keys_json = get_secret(secret_name)
            stripe_keys = json.loads(keys_json)
            self.stripe_api_key = stripe_keys.get("stripe_prod_key")
            
            if stripe and self.stripe_api_key and not getattr(stripe, "_pinnokio_configured", False):
                stripe.api_key = self.stripe_api_key
                stripe._pinnokio_configured = True  # type: ignore[attr-defined]
                print("✅ Stripe configuré avec succès")
            elif not stripe:
                print("⚠️  Module stripe non disponible")
            elif not self.stripe_api_key:
                print("⚠️  Clé API Stripe manquante dans le secret")
                
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation Stripe: {e}")
            # Ne pas lever l'exception - continuer sans Stripe
            self.stripe_api_key = None

    def _normalize_mandate_path(self, mandate_path: Optional[str]) -> Optional[str]:
        """Corrige les chemins mandat mal formés provenant du client."""
        if not mandate_path:
            return mandate_path

        normalized = mandate_path.strip()
        # Corriger les fautes de frappe connues
        normalized = normalized.replace("bo_clientss", "bo_clients")
        # Normaliser les doubles slash
        normalized = re.sub(r"/{2,}", "/", normalized)
        return normalized

    @property
    def firestore_client(self):
        return self.db
    
    def list_collection(self, collection_path: str):
        """
        Retourne tous les documents d'une collection avec leurs IDs.
        Utilisé par l'UI (Approval Waitlist) via RPC.
        """
        if collection_path.startswith("/"):
            collection_path = collection_path[1:]
        try:
            collection_ref = self.db.collection(collection_path)
            docs = collection_ref.stream()
            return [{**doc.to_dict(), "id": doc.id} for doc in docs]
        except Exception as e:
            logger.error(f"[FB] list_collection error path=%s err=%s", collection_path, e)
            return []

    def create_telegram_user(self, user_id: str, mandate_path: str, telegram_username: str, additional_data: dict = None):
        """
        Crée ou met à jour un utilisateur Telegram dans la collection.
        Ajoute la société au mapping des mandats autorisés.
        """
        try:
            from datetime import datetime, timezone
            
            # Référence au document utilisateur
            telegram_ref = self.db.collection('telegram_users').document(telegram_username)
            existing_doc = telegram_ref.get()
            
            # Préparer les données du mandat à ajouter
            mandate_data = {
                "firebase_user_id": user_id,
                "mandate_path": mandate_path,
                "added_at": datetime.now(timezone.utc).isoformat()
            }
            
            # Ajouter les données supplémentaires si fournies
            if additional_data:
                mandate_data.update(additional_data)
            
            if existing_doc.exists:
                # L'utilisateur existe déjà - mettre à jour le mapping
                current_data = existing_doc.to_dict()
                authorized_mandates = current_data.get('authorized_mandates', {})
                
                # Vérifier si ce mandat existe déjà
                if mandate_path in authorized_mandates:
                    print(f"⚠️ Mandat {mandate_path} déjà autorisé pour {telegram_username}")
                    return False
                
                # Ajouter le nouveau mandat
                authorized_mandates[mandate_path] = mandate_data
                
                # Mettre à jour le document
                telegram_ref.update({
                    'authorized_mandates': authorized_mandates,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                })
                
                print(f"✅ Mandat ajouté pour {telegram_username}")
                return True
                
            else:
                # Créer un nouvel utilisateur
                user_data = {
                    "telegram_username": telegram_username,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "is_active": True,
                    "last_activity": None,
                    "telegram_chat_id": None,
                    "authorized_mandates": {
                        mandate_path: mandate_data
                    }
                }
                
                # Sauvegarder le nouveau document
                telegram_ref.set(user_data)
                
                print(f"✅ Utilisateur Telegram {telegram_username} créé avec succès")
                return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la création/mise à jour de l'utilisateur Telegram: {str(e)}")
            return False

    def get_telegram_users(self, user_id: str, mandate_path: str = None):
        """
        Récupère la liste des utilisateurs Telegram pour un utilisateur Firebase.
        """
        try:
            # Référence à la collection telegram_users
            users_ref = self.db.collection('telegram_users')
            query = users_ref.where(filter=FieldFilter('is_active', '==', True))
            
            docs = query.get()
            
            users = []
            for doc in docs:
                user_data = doc.to_dict()
                authorized_mandates = user_data.get('authorized_mandates', {})
                
                # Filtrer selon les critères
                user_matches = False
                
                for path, mandate_data in authorized_mandates.items():
                    if mandate_data.get('firebase_user_id') == user_id:
                        if mandate_path is None or path == mandate_path:
                            user_matches = True
                            break
                
                if user_matches:
                    user_data['doc_id'] = doc.id  # doc.id = telegram_username
                    users.append(user_data)
            
            print(f"✅ {len(users)} utilisateurs Telegram trouvés")
            return users
            
        except Exception as e:
            print(f"❌ Erreur lors de la récupération des utilisateurs Telegram: {str(e)}")
            return []

    def delete_telegram_user(self, telegram_username: str, mandate_path: str):
        """
        Supprime un mandat spécifique d'un utilisateur Telegram.
        Supprime l'utilisateur entier si plus aucun mandat autorisé.
        """
        try:
            from datetime import datetime, timezone
            
            # Référence directe au document utilisateur
            user_ref = self.db.collection('telegram_users').document(telegram_username)
            
            # Vérifier que le document existe
            doc = user_ref.get()
            if not doc.exists:
                print(f"⚠️ Utilisateur Telegram {telegram_username} non trouvé")
                return False
            
            user_data = doc.to_dict()
            authorized_mandates = user_data.get('authorized_mandates', {})
            
            # Vérifier que le mandat existe
            if mandate_path not in authorized_mandates:
                print(f"⚠️ Mandat {mandate_path} non trouvé pour {telegram_username}")
                return False
            
            # Récupérer les infos du mandat avant suppression
            mandate_data = authorized_mandates[mandate_path]
            firebase_user_id = mandate_data.get('firebase_user_id')
            mandate_doc_id = mandate_data.get('mandate_doc_id')

            # Supprimer le mandat du mapping
            del authorized_mandates[mandate_path]
            
            # Si plus aucun mandat autorisé, supprimer l'utilisateur entier
            if not authorized_mandates:
                user_ref.delete()
                print(f"✅ Document utilisateur Telegram {telegram_username} supprimé complètement (plus aucune société)")
            else:
                # Mettre à jour avec les mandats restants
                user_ref.update({
                    'authorized_mandates': authorized_mandates,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                })
                print(f"✅ Mandat {mandate_path} supprimé pour {telegram_username}")
            
            if firebase_user_id and mandate_doc_id:
                try:
                    # Construire le chemin du document mandat depuis mandate_path
                    mandate_doc_path = f"{mandate_path}"
                    mandate_doc_ref = self.db.document(mandate_doc_path)
                    
                    # Récupérer le document mandat
                    mandate_doc = mandate_doc_ref.get()
                    if mandate_doc.exists:
                        mandate_doc_data = mandate_doc.to_dict()
                        
                        # Préparer les mises à jour
                        updates = {}
                        
                        # A. Supprimer de telegram_auth_users
                        telegram_auth_users = mandate_doc_data.get('telegram_auth_users', [])
                        if telegram_username in telegram_auth_users:
                            telegram_auth_users.remove(telegram_username)
                            updates['telegram_auth_users'] = telegram_auth_users
                            print(f"✅ {telegram_username} supprimé de telegram_auth_users")
                        
                        # B. NOUVEAU : Supprimer du mapping telegram_users_mapping
                        telegram_users_mapping = mandate_doc_data.get('telegram_users_mapping', {})
                        if telegram_username in telegram_users_mapping:
                            del telegram_users_mapping[telegram_username]
                            updates['telegram_users_mapping'] = telegram_users_mapping
                            print(f"✅ {telegram_username} supprimé du mapping telegram_users_mapping")
                        
                        # Appliquer les mises à jour si nécessaire
                        if updates:
                            mandate_doc_ref.update(updates)
                            print(f"✅ Document mandat {mandate_path} nettoyé")
                        else:
                            print(f"⚠️ Rien à nettoyer dans le document mandat")
                            
                    else:
                        print(f"⚠️ Document mandat non trouvé: {mandate_doc_path}")
                        
                except Exception as e:
                    print(f"⚠️ Erreur lors de la mise à jour du document mandat: {str(e)}")
                    # Ne pas faire échouer la suppression principale pour cette erreur
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la suppression: {str(e)}")
            return False

    def update_telegram_user_activity(self, telegram_username: str, telegram_chat_id: str):
        """
        Met à jour l'activité d'un utilisateur Telegram (appelé par le bot).
        """
        try:
            from datetime import datetime, timezone
            
            # Référence directe au document utilisateur
            user_ref = self.db.collection('telegram_users').document(telegram_username)
            
            # Vérifier que le document existe et est actif
            doc = user_ref.get()
            if not doc.exists:
                print(f"⚠️ Utilisateur Telegram {telegram_username} non trouvé")
                return False
                
            user_data = doc.to_dict()
            if not user_data.get('is_active', False):
                print(f"⚠️ Utilisateur Telegram {telegram_username} inactif")
                return False
            
            # Mettre à jour l'activité
            user_ref.update({
                'telegram_chat_id': telegram_chat_id,
                'last_activity': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat()
            })
            
            print(f"✅ Activité mise à jour pour {telegram_username}")
            return True
                
        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour de l'activité: {str(e)}")
            return False

    def get_banker_jobs_from_task_manager(self, user_id: str, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère l'état des jobs bancaires depuis task_manager (Source de Vérité).
        
        Remplace l'ancienne logique basée sur les notifications et pending sheets.
        
        Args:
            user_id (str): Firebase UID
            mandate_path (str): Chemin du mandat pour filtrer les tâches
            
        Returns:
            Dict structuré:
            {
                "processed": [...],     # Tâches terminées
                "pending": [...],       # Tâches en attente (détail)
                "in_process": {...}     # Tâches en cours groupées par batch_id
            }
        """
        try:
            
            
            # Initialiser la structure de retour
            result = {
                "processed": [],
                "pending": [],
                "in_process": {}  # Dict groupé par batch_id
            }
            
            # Requête sur task_manager
            # Filtre 1: mandate_path (filtrage post-query si startswith pas dispo, mais ici on suppose égalité)
            # Filtre 2: department == 'banker' (ou 'Banker')
            
            task_ref = self.db.collection("clients").document(user_id).collection("task_manager")
            
            # On récupère tout le task_manager pour ce mandat (ou filtré plus finement si possible)
            # Note: Firestore ne supporte pas nativement "startswith" ou "contains" simple combiné
            # On va filtrer en mémoire pour être sûr, ou utiliser des filtres d'égalité si mandate_path est exact
            
            # Optimisation: Filtrer par department directement si possible, sinon tout charger
            # Comme mandate_path est stocké dans le doc, on peut filtrer dessus si indexé
            
            # Stratégie: Récupérer les tâches 'banker' et filtrer mandate_path en python
            query = task_ref.where(filter=FieldFilter("department", "in", ["Bankbookeeper", "banker", "Banker"]))
            docs = query.stream()
            
            for doc in docs:
                data = doc.to_dict()
                doc_mandate_path = data.get("mandate_path", "")
                
                # Filtrage strict sur le mandat
                if doc_mandate_path != mandate_path:
                    continue
                    
                status = data.get("status", "").lower()
                department_data = data.get("department_data", {})
                banker_data = department_data.get("Bankbookeeper", {}) or department_data.get("banker", {}) or department_data.get("Banker", {})
                
                # Cas 1: Completed -> Processed
                if status in ["completed", "close", "closed"]:
                    result["processed"].append(data)
                    
                # Cas 2: Pending -> Liste simple avec détails
                elif status == "pending":
                    # Extraire les infos clés pour le croisement
                    item = {
                        "task_id": doc.id,
                        "status": status,  # Garder le statut original
                        "batch_id": banker_data.get("batch_id"),
                        "bank_account_id": banker_data.get("bank_account_id"), # journal_id
                        "transaction_id": banker_data.get("transaction_id"),   # move_id
                        "txn_amount": banker_data.get("txn_amount"),
                        "txn_currency": banker_data.get("txn_currency"),
                        # Garder le reste au cas où
                        **banker_data
                    }
                    result["pending"].append(item)
                    
                # Cas 3: On Process -> Groupé par batch_id
                elif status in ["on_process", "processing", "in_progress", "in_queue", "running", "stopping"]:
                    batch_id = banker_data.get("batch_id") or "unknown_batch"

                    item = {
                        "task_id": doc.id,
                        "status": status,  # Garder le statut original (on_process, processing, etc.)
                        "bank_account_id": banker_data.get("bank_account_id"),
                        "transaction_id": banker_data.get("transaction_id"),
                        "txn_amount": banker_data.get("txn_amount"),
                        "txn_currency": banker_data.get("txn_currency"),
                        **banker_data
                    }
                    
                    if batch_id not in result["in_process"]:
                        result["in_process"][batch_id] = []
                    
                    result["in_process"][batch_id].append(item)
            
            return result
            
        except Exception as e:
            print(f"❌ Erreur get_banker_jobs_from_task_manager: {e}")
            import traceback
            traceback.print_exc()
            return {"processed": [], "pending": [], "in_process": {}}

    def get_apbookeeper_jobs_from_task_manager(self, user_id: str, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère l'état des jobs APBookkeeper depuis task_manager (Source de Vérité).

        Remplace l'ancienne logique basée sur fetch_journal_entries + check_job_status (notifications).

        Args:
            user_id (str): Firebase UID
            mandate_path (str): Chemin du mandat pour filtrer les tâches

        Returns:
            Dict structuré:
            {
                "to_process": [...],    # Tâches à traiter (error, to_process)
                "in_process": [...],    # Tâches en cours (on_process, in_queue)
                "pending": [...],       # Tâches en attente
                "processed": [...],     # Tâches terminées (completed, close, closed)
                "step_mapping": {...}   # Mapping current_step → translated_term
            }
        """
        try:
            result = {
                "to_process": [],
                "in_process": [],
                "pending": [],
                "processed": [],
                "step_mapping": {}
            }

            # 1. Charger le mapping ap_approval_list pour traduire current_step
            try:
                approval_ref = self.db.document(f"{mandate_path}/setup/ap_approval_list")
                approval_doc = approval_ref.get()
                if approval_doc.exists:
                    mapping_data = approval_doc.to_dict() or {}
                    # Structure: { "STEP_KEY": { "original_term": "...", "translated_term": "..." } }
                    for step_key, step_info in mapping_data.items():
                        if isinstance(step_info, dict) and "translated_term" in step_info:
                            original = step_info.get("original_term", step_key)
                            result["step_mapping"][original] = step_info["translated_term"]
            except Exception as e:
                print(f"[AP] Warning: Could not load ap_approval_list: {e}")

            # 2. Requête task_manager filtrée par department APbookeeper/Apbookeeper
            task_ref = self.db.collection("clients").document(user_id).collection("task_manager")
            query = task_ref.where(filter=FieldFilter("department", "in", ["APbookeeper", "Apbookeeper", "apbookeeper"]))
            docs = query.stream()

            for doc in docs:
                data = doc.to_dict()
                doc_mandate_path = data.get("mandate_path", "")

                # Filtrage strict sur le mandat
                if doc_mandate_path != mandate_path:
                    continue

                status = (data.get("status") or "").lower()

                # Skip purged/incomplete documents (no status = billing skeleton after delete)
                if not status:
                    continue
                department_data = data.get("department_data", {})
                ap_data = department_data.get("APbookeeper", {}) or department_data.get("Apbookeeper", {}) or department_data.get("apbookeeper", {})

                # Extraire le timestamp (format "9 janvier 2026 à 23:59:45 UTC+1") → date seule
                raw_timestamp = data.get("timestamp", "")
                date_str = ""
                if raw_timestamp:
                    # Prendre la partie avant " à " pour extraire la date
                    parts = str(raw_timestamp).split(" à ")
                    date_str = parts[0].strip() if parts else str(raw_timestamp)

                # Construire l'item standardisé
                file_id = ap_data.get("file_id", "")
                uri_link = data.get("uri_file_link", "") or data.get("drive_link", "")
                if not uri_link and file_id:
                    uri_link = f"https://drive.google.com/file/d/{file_id}/view"

                item = {
                    "task_id": doc.id,
                    "job_id": ap_data.get("job_id", doc.id),
                    "file_name": ap_data.get("file_name", data.get("file_name", "")),
                    "file_id": file_id,
                    "drive_file_id": file_id,
                    "uri_drive_link": uri_link,
                    "date": date_str,
                    "current_step": data.get("current_step", ""),
                    "status": status,
                }

                # Classement par statut (garder le statut original pour le badge frontend)
                if status in ["completed", "close", "closed"]:
                    result["processed"].append(item)
                elif status == "pending":
                    result["pending"].append(item)
                elif status in ["on_process", "processing", "in_progress", "in_queue", "running", "stopping"]:
                    result["in_process"].append(item)
                else:
                    # error, to_process, stopped, skipped ou inconnu → to_process
                    if status not in ["error", "to_process", "stopped", "skipped"]:
                        item["status"] = "to_process"
                    result["to_process"].append(item)

            return result

        except Exception as e:
            print(f"❌ Erreur get_apbookeeper_jobs_from_task_manager: {e}")
            import traceback
            traceback.print_exc()
            return {"to_process": [], "in_process": [], "pending": [], "processed": [], "step_mapping": {}}

    def get_router_jobs_from_task_manager(self, user_id: str, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère l'état des jobs Router depuis task_manager (Source de Vérité).

        Utilisé pour le croisement avec les fichiers Drive:
        - Drive = liste brute des fichiers (base "to_process")
        - task_manager = source de vérité des statuts
        - Junction: job_id (Drive file_id == task_manager job_id)

        Args:
            user_id (str): Firebase UID
            mandate_path (str): Chemin du mandat pour filtrer les tâches

        Returns:
            Dict structuré:
            {
                "in_process": [...],   # on_process, in_queue, running, stopping
                "pending": [...],      # pending
                "processed": [...],    # completed, close, closed
                "by_job_id": {...}     # job_id → item (index pour croisement Drive)
            }
        """
        try:
            result = {
                "in_process": [],
                "pending": [],
                "processed": [],
                "by_job_id": {}
            }

            task_ref = self.db.collection("clients").document(user_id).collection("task_manager")
            query = task_ref.where(filter=FieldFilter("department", "in", ["router", "Router"]))
            docs = query.stream()

            for doc in docs:
                data = doc.to_dict()
                doc_mandate_path = data.get("mandate_path", "")

                if doc_mandate_path != mandate_path:
                    continue

                status = (data.get("status") or "").lower()
                job_id = data.get("job_id", doc.id)
                file_name = data.get("file_name", "")
                raw_timestamp = data.get("timestamp", "")

                date_str = ""
                if raw_timestamp:
                    parts = str(raw_timestamp).split(" à ")
                    date_str = parts[0].strip() if parts else str(raw_timestamp)

                item = {
                    "task_id": doc.id,
                    "job_id": job_id,
                    "file_name": file_name,
                    "date": date_str,
                    "status": status,
                }

                # Index par job_id pour croisement avec Drive
                result["by_job_id"][job_id] = item

                # Classement par statut (garder le statut original pour le badge frontend)
                if status in ["completed", "close", "closed"]:
                    result["processed"].append(item)
                elif status == "pending":
                    result["pending"].append(item)
                elif status in ["on_process", "processing", "in_progress", "in_queue", "running", "stopping"]:
                    result["in_process"].append(item)
                # else: error, to_process → pas ajouté (reste dans to_process du Drive)

            return result

        except Exception as e:
            print(f"❌ Erreur get_router_jobs_from_task_manager: {e}")
            import traceback
            traceback.print_exc()
            return {"in_process": [], "pending": [], "processed": [], "by_job_id": {}}

    def get_telegram_user_by_username(self, telegram_username: str):
        """
        Trouve un utilisateur Telegram par son nom d'utilisateur (pour le bot).
        """
        try:
            # Accès direct au document via le telegram_username
            user_ref = self.db.collection('telegram_users').document(telegram_username)
            doc = user_ref.get()
            
            if doc.exists:
                user_data = doc.to_dict()
                
                # Vérifier que l'utilisateur est actif
                if user_data.get('is_active', False):
                    user_data['doc_id'] = doc.id  # doc.id = telegram_username
                    return user_data
                else:
                    print(f"⚠️ Utilisateur Telegram {telegram_username} trouvé mais inactif")
                    return None
            else:
                print(f"⚠️ Utilisateur Telegram {telegram_username} non trouvé")
                return None
            
        except Exception as e:
            print(f"❌ Erreur lors de la recherche de l'utilisateur: {str(e)}")
            return None

    def is_telegram_user_authorized(self, telegram_username: str, mandate_path: str):
        """
        Vérifie si un utilisateur Telegram est autorisé pour un mandat donné.
        """
        try:
            # Accès direct au document
            user_ref = self.db.collection('telegram_users').document(telegram_username)
            doc = user_ref.get()
            
            if doc.exists:
                user_data = doc.to_dict()
                
                # Vérifier que l'utilisateur est actif
                if user_data.get('is_active', False):
                    authorized_mandates = user_data.get('authorized_mandates', {})
                    # Vérifier si le mandat spécifique est autorisé
                    return mandate_path in authorized_mandates
            
            return False
            
        except Exception as e:
            print(f"❌ Erreur lors de la vérification d'autorisation: {str(e)}")
            return False

    def get_user_authorized_mandates(self, telegram_username: str):
        """
        Récupère tous les mandats autorisés pour un utilisateur Telegram.
        """
        try:
            user_ref = self.db.collection('telegram_users').document(telegram_username)
            doc = user_ref.get()
            
            if doc.exists:
                user_data = doc.to_dict()
                
                if user_data.get('is_active', False):
                    return user_data.get('authorized_mandates', {})
            
            return {}
            
        except Exception as e:
            print(f"❌ Erreur lors de la récupération des mandats: {str(e)}")
            return {}

    # === BETA ACCESS MANAGEMENT ===
    def get_beta_request_by_email(self, email: str) -> Optional[dict]:
        """Retourne l'entrée beta_request pour un email donné s'il existe."""
        try:
            query = self.db.collection("beta_request").where(filter=FieldFilter("email", "==", email)).limit(1)
            docs = query.get()
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                return data
            return None
        except Exception as e:
            print(f"❌ Erreur get_beta_request_by_email: {e}")
            return None

    def create_or_update_beta_request(self, payload: dict) -> bool:
        """
        Crée (ou met à jour) une demande beta dans la collection racine 'beta_request'.
        Utilise l'email comme clé unique de document pour éviter les doublons.
        """
        try:
            email = payload.get("email")
            if not email:
                raise ValueError("email manquant dans la demande beta")

            # Force authorized_access à False à la création par défaut si non fourni
            if "authorized_access" not in payload:
                payload["authorized_access"] = False

            payload.setdefault("created_at", firestore.SERVER_TIMESTAMP)
            payload["updated_at"] = firestore.SERVER_TIMESTAMP

            # Email est acceptable comme ID (Firestore interdit seulement '/')
            doc_ref = self.db.collection("beta_request").document(email)
            doc_ref.set(payload, merge=True)
            return True
        except Exception as e:
            print(f"❌ Erreur create_or_update_beta_request: {e}")
            return False

    def delete_scheduler_job_completely(self, job_id: str) -> bool:
        """Supprime complètement un job scheduler de Firebase."""
        try:
            # ✅ Correction : chercher dans la collection "scheduled_tasks" (et non "scheduler")
            job_ref = self.db.collection("scheduled_tasks").document(job_id)
            
            # Vérifier si le document existe avant de le supprimer
            if job_ref.get().exists:
                job_ref.delete()
                logger.info(f"[TASKS] ✅ Document scheduled_tasks {job_id} supprimé de Firebase")
                return True
            else:
                logger.info(f"[TASKS] ℹ️ Document scheduled_tasks {job_id} n'existe pas dans Firebase")
                return True  # Considéré comme un succès car l'objectif est atteint
            
        except Exception as e:
            logger.error(f"[TASKS] ❌ Erreur lors de la suppression complète du job {job_id}: {e}")
            return False

    def save_scheduler_job(self, mandate_path: str, job_type: str, job_data: dict) -> bool:
        """
        Sauvegarde un job dans la collection scheduler.
        
        Args:
            mandate_path: Chemin du mandat
            job_type: Type de job (apbookeeper, banker, router)
            job_data: Données du job à sauvegarder
            
        Returns:
            bool: True si succès, False sinon
        """
        try:
            # ID unique pour ce job
            job_id = f"{mandate_path.replace('/', '_')}_{job_type}"
            
            # Ajouter des métadonnées automatiques
            job_data_with_meta = {
                **job_data,
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "enabled": True
            }
            
            # Sauvegarder dans la collection scheduler
            doc_ref = self.db.collection('scheduler').document(job_id)
            doc_ref.set(job_data_with_meta)
            
            print(f"✅ Job {job_type} sauvegardé dans scheduler DB avec ID: {job_id}")
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la sauvegarde dans scheduler DB: {e}")
            return False
    
    def disable_scheduler_job(self, mandate_path: str, job_type: str) -> bool:
        """
        Désactive un job dans la collection scheduler.
        
        Args:
            mandate_path: Chemin du mandat
            job_type: Type de job
            
        Returns:
            bool: True si succès, False sinon
        """
        try:
            # ID unique pour ce job
            job_id = f"{mandate_path.replace('/', '_')}_{job_type}"
            
            # Marquer comme désactivé
            doc_ref = self.db.collection('scheduler').document(job_id)
            doc_ref.update({
                "enabled": False,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "disabled_at": firestore.SERVER_TIMESTAMP
            })
            
            print(f"✅ Job {job_type} désactivé dans scheduler DB")
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la désactivation dans scheduler DB: {e}")
            return False

    def delete_scheduler_documents_for_mandate(self, mandate_path: str) -> bool:
        """
        Supprime tous les documents scheduler associés à un mandate_path.
        
        Cette méthode supprime tous les jobs scheduler qui contiennent le 
        mandate_path dans leur ID de document (format: mandate_path_jobtype).
        
        Args:
            mandate_path: Chemin du mandat (ex: "clients/user_id/bo_clients/doc_id/mandates/mandate_id")
            
        Returns:
            bool: True si succès (même si aucun document trouvé), False en cas d'erreur
        """
        try:
            # Convertir le mandate_path au format utilisé dans les IDs scheduler
            # Format: clients_user_id_bo_clients_doc_id_mandates_mandate_id
            mandate_path_formatted = mandate_path.replace('/', '_')
            
            print(f"🔍 Recherche des documents scheduler pour le mandat: {mandate_path}")
            print(f"   Format recherché: {mandate_path_formatted}")
            
            # Récupérer tous les documents de la collection scheduler
            scheduler_ref = self.db.collection('scheduler')
            
            # Récupérer tous les documents dont l'ID commence par mandate_path_formatted
            # Note: Firestore ne supporte pas startsWith dans les queries, donc on récupère tous
            # les documents et on filtre en Python
            all_docs = scheduler_ref.stream()
            
            deleted_count = 0
            for doc in all_docs:
                # Vérifier si l'ID du document commence par notre mandate_path formaté
                if doc.id.startswith(mandate_path_formatted):
                    print(f"   🗑️ Suppression du job scheduler: {doc.id}")
                    doc.reference.delete()
                    deleted_count += 1
            
            if deleted_count > 0:
                print(f"✅ {deleted_count} document(s) scheduler supprimé(s) pour le mandat")
            else:
                print(f"ℹ️ Aucun document scheduler trouvé pour le mandat")
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la suppression des documents scheduler: {e}")
            return False
    def clean_telegram_users_for_mandate(self, mandate_path: str) -> bool:
        """
        Nettoie les références au mandat dans les documents telegram_users.
        
        Cette méthode:
        1. Récupère d'abord les utilisateurs Telegram du document mandat (telegram_auth_users)
        2. Pour chaque utilisateur trouvé, supprime le mandat de leur liste authorized_mandates
        3. Supprime l'utilisateur entier si plus aucun mandat autorisé
        
        Args:
            mandate_path: Chemin du mandat (ex: "clients/user_id/bo_clients/doc_id/mandates/mandate_id")
            
        Returns:
            bool: True si succès (même si aucun utilisateur trouvé), False en cas d'erreur
        """
        try:
            from datetime import datetime, timezone
            
            print(f"🔍 Nettoyage des utilisateurs Telegram pour le mandat: {mandate_path}")
            
            # 1. Récupérer le document du mandat pour obtenir telegram_auth_users
            mandate_doc_ref = self.db.document(mandate_path)
            mandate_doc = mandate_doc_ref.get()
            
            if not mandate_doc.exists:
                print(f"⚠️ Document mandat {mandate_path} non trouvé")
                return True  # Pas d'erreur, juste pas de document
            
            mandate_data = mandate_doc.to_dict()
            telegram_auth_users = mandate_data.get('telegram_auth_users', [])
            
            # Vérifier si le champ telegram_auth_users existe et contient des données
            if not telegram_auth_users or not isinstance(telegram_auth_users, list) or len(telegram_auth_users) == 0:
                print(f"ℹ️ Pas d'utilisateurs Telegram configurés pour ce mandat (telegram_auth_users vide ou absent)")
                return True
            
            print(f"   📋 {len(telegram_auth_users)} utilisateur(s) Telegram trouvé(s): {telegram_auth_users}")
            
            # 2. Pour chaque utilisateur Telegram, nettoyer leurs authorized_mandates
            cleaned_count = 0
            for telegram_username in telegram_auth_users:
                try:
                    # Référence au document utilisateur Telegram
                    user_ref = self.db.collection('telegram_users').document(telegram_username)
                    user_doc = user_ref.get()
                    
                    if not user_doc.exists:
                        print(f"   ⚠️ Utilisateur Telegram {telegram_username} non trouvé dans telegram_users")
                        continue
                    
                    user_data = user_doc.to_dict()
                    authorized_mandates = user_data.get('authorized_mandates', {})
                    
                    # Vérifier si ce mandat est dans la liste des mandats autorisés
                    if mandate_path not in authorized_mandates:
                        print(f"   ℹ️ Mandat déjà absent des authorized_mandates de {telegram_username}")
                        continue
                    
                    # Supprimer le mandat de authorized_mandates
                    del authorized_mandates[mandate_path]
                    
                    # Si plus aucun mandat autorisé, supprimer l'utilisateur entier
                    if not authorized_mandates:
                        user_ref.delete()
                        print(f"   🗑️ Document utilisateur Telegram {telegram_username} supprimé (plus aucun mandat)")
                    else:
                        # Mettre à jour avec les mandats restants
                        user_ref.update({
                            'authorized_mandates': authorized_mandates,
                            'updated_at': datetime.now(timezone.utc).isoformat()
                        })
                        print(f"   ✅ Mandat supprimé des authorized_mandates de {telegram_username}")
                    
                    cleaned_count += 1
                    
                except Exception as e:
                    print(f"   ⚠️ Erreur lors du nettoyage de {telegram_username}: {e}")
                    # Continuer avec les autres utilisateurs
                    continue
            
            if cleaned_count > 0:
                print(f"✅ {cleaned_count} utilisateur(s) Telegram nettoyé(s)")
            else:
                print(f"ℹ️ Aucun utilisateur Telegram n'a été nettoyé")
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors du nettoyage des utilisateurs Telegram: {e}")
            return False




    def set_firebase_function_table(self, path: str) -> bool:
        """
        Vérifie l'existence et crée/met à jour le document des fonctions dans Firebase.
        
        Args:
            path (str): Chemin de base pour le document
            
        Returns:
            bool: True si l'opération est réussie, False sinon
        """
        try:
            # Construire le chemin complet du document
            doc_path = f"{path}/setup/function_table"
            
            # Créer la référence du document
            doc_ref = self.db.document(doc_path)
            
            # Vérifier si le document existe
            doc = doc_ref.get()
            
            # Préparer les données à insérer
            functions_data = {
                    "INVOICES": {
                        "drive_service": "AP",
                        "doc_to_do_name": "bookeeper",
                        "function_description": "Flux de process pour la gestion de la saisie de factures fournisseurs",
                        "is_active": True,
                        'ask_approval':True
                    },
                    "EXPENSES": {
                        "drive_service": "EX",
                        "doc_to_do_name": "bookeeper",
                        "function_description": "Flux de process pour la gestion de la saisie des notes de frais",
                        "is_active": True,
                        'ask_approval':True
                    },
                    "BANKS_CASH": {
                        "drive_service": "Bank",
                        "doc_to_do_name": "bookeeper",
                        "function_description": "Flux de process pour la gestion de la saisie des banques (relevés bancaires, extraits , avis de débit, crédit ect..)",
                        "is_active": True,
                        'ask_approval':True
                    },
                    "HR": {
                        "drive_service": "HR",
                        "doc_to_do_name": "manager",
                        "function_description": "Flux de process pour la gestion des taches en relation avec les ressources humaines",
                        "is_active": True,
                        'ask_approval':True
                    },
                    "TAXES": {
                        "drive_service": "Ad",
                        "doc_to_do_name": "manager",
                        "function_description": "Flux de process pour la gesion des taches administratives et légales",
                        "is_active": True,
                        'ask_approval':True
                    },
                    "CONTRATS": {
                        "drive_service": "Ad",
                        "doc_to_do_name": "manager",
                        "function_description": "Flux de process pour la gesion des taches administratives et légales",
                        "is_active": True,
                        'ask_approval':True
                    },
                    "LETTERS": {
                        "drive_service": "Ad",
                        "doc_to_do_name": "manager",
                        "function_description": "Flux de process pour la gesion des taches administratives et légales",
                        "is_active": True,
                        'ask_approval':True
                    }
                }


            if not doc.exists:
                # Si le document n'existe pas, le créer
                doc_ref.set(functions_data)
                print(f"Document créé avec succès: {doc_path}")
            else:
                # Si le document existe, le mettre à jour
                doc_ref.update(functions_data)
                print(f"Document mis à jour avec succès: {doc_path}")
                
            return True
            
        except Exception as e:
            print(f"Erreur lors de la création/mise à jour du document: {str(e)}")
            return False




    def get_departments_list(self, path: str) -> list:
        """
        Récupère la liste des noms de départements depuis Firebase.
        
        Args:
            path (str): Chemin de base pour le document
            
        Returns:
            list: Liste des noms de départements formatés (première lettre majuscule)
        """
        try:
            # Construire le chemin complet du document
            doc_path = f"{path}/setup/function_table"
            
            # Créer la référence du document et le récupérer
            doc_ref = self.db.document(doc_path)
            doc = doc_ref.get()
            
            if not doc.exists:
                print(f"Le document n'existe pas, tentative de création du document")
                # Si le document n'existe pas, le créer avec le path fourni
                create = self.set_firebase_function_table(path)
                if create:
                    doc = doc_ref.get()
                    if not doc.exists:
                        print("Échec de récupération du document après création")
                        return []
                else:
                    print("Échec de création du document")
                    return []
                    
            # Extraire les noms des départements (clés principales)
            departments_list = []
            firebase_data = doc.to_dict()
            if firebase_data is None:
                print("Les données Firebase sont vides")
                return []
                
            for department_name in firebase_data.keys():
                # Transformer en format Title Case (première lettre majuscule, reste minuscule)
                formatted_name = department_name.lower().capitalize()
                departments_list.append(formatted_name)
                    
            return sorted(departments_list)  # Retourner la liste triée
            
        except Exception as e:
            print(f"Erreur lors de la récupération des départements: {str(e)}")
            return []

    def get_batch_details(self,user_id, batch_id):
        """
        Récupère les détails d'un lot bancaire à partir de son ID dans notifications.
        
        ⚠️ CHANGEMENT : Lit maintenant depuis notifications/{batch_id} au lieu de task_manager/{batch_id}
        Les transactions sont extraites directement depuis le tableau 'transactions' au niveau racine.
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            batch_id (str): ID du lot à récupérer
            
        Returns:
            dict: Dictionnaire contenant le job_id, bank_account et transactions, ou None si non trouvé
        """
        try:
            if not user_id:
                print("Erreur: L'ID utilisateur est requis pour accéder aux détails du lot")
                return None
            
            # ⚠️ CHANGEMENT : Chemin vers le document notifications au lieu de task_manager
            notifications_path = f"clients/{user_id}/notifications"
            notification_doc_ref = self.db.collection(notifications_path).document(batch_id)
            notification_doc = notification_doc_ref.get()
            
            if not notification_doc.exists:
                print(f"Aucun document notifications trouvé pour le lot {batch_id}")
                return None
            
            notification_data = notification_doc.to_dict()
            
            # Structure de base pour les détails du lot
            batch_details = {
                'job_id': notification_data.get('job_id', batch_id),  # Utiliser job_id du document ou batch_id en fallback
                'bank_account': '',
                'transactions': [],
                'start_instructions': '',  # Laissé vide car non présent dans notifications
            }
            
            # ⚠️ CHANGEMENT : Récupérer le compte bancaire directement au niveau racine
            if 'bank_account' in notification_data:
                batch_details['bank_account'] = notification_data['bank_account']
            
            if 'bank_account_id' in notification_data:
                batch_details['bank_account_id'] = notification_data['bank_account_id']

            if 'start_instructions' in notification_data:
                batch_details['start_instructions'] = notification_data['start_instructions']

            # ⚠️ CHANGEMENT : Récupérer les transactions directement au niveau racine
            if 'transactions' in notification_data and isinstance(notification_data['transactions'], list):
                batch_details['transactions'] = notification_data['transactions']
            else:
                print(f"⚠️ Aucune transaction trouvée dans le document notifications pour le lot {batch_id}")

            return batch_details
            
        except Exception as e:
            print(f"Erreur lors de la récupération des détails du lot {batch_id}: {e}")
            import traceback
            traceback.print_exc()
            return None

    def delete_task_manager_document(self, user_id: str, document_id: str):
        """
        ⚠️ PURGE (et non suppression totale) d'un document dans task_manager par son ID.
        
        Objectif: nettoyer les données de travail (payload, events, sous-collections, etc.)
        tout en PRÉSERVANT le champ `billing` si présent (facturation immuable).
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            document_id (str): ID du document à supprimer (chemin composé)
            
        Returns:
            bool: True si succès, False sinon
        """
        try:
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            
            task_manager_ref = self.db.collection(base_path).document(document_id)
            
            # Vérifier si le document existe
            doc = task_manager_ref.get()
            
            if doc.exists:
                # Préserver la facturation + champs "index UI" nécessaires au dashboard si présents
                current_data = doc.to_dict() or {}
                billing_data = current_data.get("billing", None)
                mandate_path = current_data.get("mandate_path", None)
                file_name = current_data.get("file_name", None)
                department = current_data.get("department", None)
                departement = current_data.get("departement", None)
                # timestamps possibles selon les writers
                timestamp = current_data.get("timestamp", None)
                updated_at = current_data.get("updated_at", None)

                # Supprimer les sous-collections d'abord
                subcollections = task_manager_ref.collections()
                for subcollection in subcollections:
                    sub_docs = subcollection.stream()
                    for sub_doc in sub_docs:
                        print(f"Suppression du document {sub_doc.id} dans la sous-collection {subcollection.id} de task_manager/{document_id}")
                        sub_doc.reference.delete()

                # Remplacer le document principal en gardant uniquement les champs immuables/nécessaires:
                # `billing` + contexte société (`mandate_path`) + index UI (`file_name`, département, timestamps).
                # Sinon, comportement historique: suppression totale.
                if billing_data is not None:
                    task_manager_ref.set(
                        {
                            "billing": billing_data,
                            **({"mandate_path": mandate_path} if mandate_path is not None else {}),
                            **({"file_name": file_name} if file_name is not None else {}),
                            **({"department": department} if department is not None else {}),
                            **({"departement": departement} if departement is not None else {}),
                            **({"timestamp": timestamp} if timestamp is not None else {}),
                            **({"updated_at": updated_at} if updated_at is not None else {}),
                            "purged": True,
                            "purged_at": firestore.SERVER_TIMESTAMP,
                        },
                        merge=False,
                    )
                    print(f"Document {document_id} purgé (billing préservé) dans task_manager.")
                else:
                    task_manager_ref.delete()
                    print(f"Document {document_id} supprimé de task_manager (aucun billing à préserver).")

                return True
            else:
                print(f"Document {document_id} non trouvé dans task_manager.")
                return False
                
        except Exception as e:
            print(f"Erreur lors de la suppression du document {document_id} dans task_manager: {e}")
            import traceback
            traceback.print_exc()
            return False



    
    def delete_specific_pending_transactions(self, mandate_path, transaction_ids):
        """
        Supprime les transactions en attente spécifiées par leurs IDs du document Firebase
        sans affecter les autres transactions.
        
        :param mandate_path: Chemin du mandat
        :param transaction_ids: Liste des IDs de transactions à supprimer
        :return: (bool, dict) Tuple avec un booléen indiquant le succès et le document mis à jour
        """
        try:
            print(f"[INFO] Début suppression pour: {transaction_ids}")
            
            # Chemin complet vers le document des transactions en attente
            document_path = f"{mandate_path}/working_doc/pending_item_docsheet"
            #print(f"[INFO] Chemin document: {document_path}")
            
            # Récupérer le document actuel
            doc_ref = self.db.document(document_path)
            doc_data = doc_ref.get().to_dict()
            #print(f"[DEBUG] Données brutes récupérées: {doc_data}")
            
            if not doc_data or "items" not in doc_data:
                print("[WARN] Aucune donnée de transaction en attente trouvée")
                return False, None
            
            import copy
            updated_data = copy.deepcopy(doc_data)
            items_data = updated_data.get("items", {})
            #print(f"[DEBUG] Type de items_data: {type(items_data)}")
            #print(f"[DEBUG] items_data: {items_data}")
            
            if not isinstance(items_data, dict):
                print("[ERROR] items_data n'est pas un dictionnaire")
                return False, None

            keys_to_delete = []
            for key, item in items_data.items():
                #print(f"[CHECK] item key={key}, item={item}")
                if isinstance(item, dict) and str(item.get("Id")) in transaction_ids:
                    #print(f"[MATCH] ID trouvé: {item.get('Id')}")
                    keys_to_delete.append(key)

            for key in keys_to_delete:
                if key in items_data:
                    #print(f"[DELETE] Suppression de la clé: {key}")
                    del items_data[key]
            
            doc_ref.set(updated_data, merge=False)
            print(f"[SUCCESS] Document mis à jour avec succès")
            
            return True, updated_data
        except Exception as e:
            print(f"[EXCEPTION] Erreur lors de la suppression des transactions en attente: {e}")
            return False, None



    def get_banker_batches(self,user_id, notifications_path, collection_id):
        """
        Récupère tous les lots bancaires en cours de traitement pour une collection spécifique.
        
        1. Identifie d'abord les documents de type 'banker' dans la collection notifications
        2. Filtre par collection_id pour s'assurer d'avoir les bons documents
        3. Récupère ensuite les données détaillées de ces documents dans la collection task_manager
        
        Args:
            notifications_path (str): Chemin vers la collection notifications
            collection_id (str): ID de la collection pour filtrer les documents
            
        Returns:
            list: Liste des données complètes des lots bancaires en cours de traitement
        """
        try:
            # Vérifier que l'user_id est défini
            if not user_id:
                print("Erreur: L'ID utilisateur est requis pour accéder aux notifications")
                return []
            
            # Vérifier que collection_id est fourni
            if not collection_id:
                print("Erreur: L'ID de la collection est requis pour filtrer les notifications")
                return []
            
            # 1. ÉTAPE 1: Récupérer les IDs des documents dans "notifications" avec function_name='banker' et collection_id correspondant
            notifications_ref = self.db.collection(notifications_path)
            
            # Filtrer les documents où function_name='banker' ET collection_id correspond
            query = notifications_ref.where(
                filter=firestore.FieldFilter("function_name", "==", "Bankbookeeper")
            ).where(
                filter=firestore.FieldFilter("collection_id", "==", collection_id)
            )
            
            banker_docs = query.stream()
            
            # Extraire les IDs des documents notifications et leur statut
            banker_notifications = []
            for doc in banker_docs:
                doc_data = doc.to_dict()
                if not isinstance(doc_data, dict):
                    continue
                
                # Ne conserver que les documents avec un statut de traitement en cours
                if doc_data.get('status') in ['running', 'in queue', 'stopping','stopped']:
                    banker_notifications.append({
                        'notification_id': doc.id,
                        'job_id': doc_data.get('job_id', doc.id),
                        'status': doc_data.get('status', ''),
                        'bank_account': doc_data.get('bank_account', ''),
                        'timestamp': doc_data.get('timestamp', ''),
                        'transactions': doc_data.get('transactions', []),
                        'collection_id': doc_data.get('collection_id', '')  # Inclure le collection_id pour référence
                    })
            
            print(f"Récupéré {len(banker_notifications)} notifications bancaires actives pour la collection {collection_id}")
            
            # 2. ÉTAPE 2: Récupérer les données détaillées dans "task_manager" pour chaque notification
            task_manager_path = notifications_path.replace('notifications', 'task_manager')
            task_manager_ref = self.db.collection(task_manager_path)
            
            complete_banker_batches = []
            
            # Pour chaque notification, récupérer les données détaillées du task_manager
            for notification in banker_notifications:
                job_id = notification['job_id']
                
                # Récupérer le document task_manager correspondant
                task_doc_ref = task_manager_ref.document(job_id)
                task_doc = task_doc_ref.get()
                
                if task_doc.exists:
                    task_data = task_doc.to_dict()
                    
                    # Fusionner les données de notification et task_manager
                    combined_data = {**notification}
                    
                    # Ajouter les données du payload si disponible
                    if 'payload' in task_data and isinstance(task_data['payload'], dict):
                        payload = task_data['payload']
                        
                        # Enrichir avec les données du payload qui sont utiles pour TransactionItem
                        if 'jobs_data' in payload and isinstance(payload['jobs_data'], list) and len(payload['jobs_data']) > 0:
                            job_data = payload['jobs_data'][0]
                            
                            # Mise à jour des transactions si elles sont plus complètes dans le payload
                            if 'transactions' in job_data and len(job_data['transactions']) > len(notification['transactions']):
                                combined_data['transactions'] = job_data['transactions']
                    
                    complete_banker_batches.append(combined_data)
                else:
                    # Si aucun document task_manager n'est trouvé, utiliser uniquement les données de notification
                    complete_banker_batches.append(notification)
            
            print(f"Enrichi {len(complete_banker_batches)} lots bancaires avec les données de task_manager pour la collection {collection_id}")
            return complete_banker_batches
            
        except Exception as e:
            print(f"Erreur lors de la récupération des lots bancaires pour la collection {collection_id}: {e}")
            import traceback
            traceback.print_exc()
            return []
    

    def save_banker_batch(self, mandate_path, batch_data, batch_id=None):
        """
        Sauvegarde un lot bancaire dans Firebase.
        
        Args:
            mandate_path (str): Chemin du mandat dans Firebase
            batch_data (dict): Données du lot à sauvegarder
            batch_id (str, optional): ID du lot. Si non fourni, un ID sera généré.
            
        Returns:
            str: ID du lot sauvegardé
        """
        try:
            # Vérifier si le chemin du mandat est valide
            if not mandate_path:
                print("Erreur: Le chemin du mandat est vide")
                return None
                
            # Chemin complet de la collection des lots bancaires
            batches_collection_path = f"{mandate_path}/banking_batches"
            
            # Référence à la collection
            batches_ref = self.db.collection(batches_collection_path)
            
            # Si aucun batch_id n'est fourni, générer un nouvel ID unique
            if not batch_id:
                # Générer un ID basé sur la date et l'heure pour assurer l'unicité
                current_timestamp = int(time.time())
                batch_id = f"batch_{current_timestamp}"
            
            # Ajouter des métadonnées supplémentaires au lot
            batch_data["created_at"] = firestore.SERVER_TIMESTAMP
            batch_data["updated_at"] = firestore.SERVER_TIMESTAMP
            batch_data["batch_id"] = batch_id
            
            # Si un utilisateur est associé à cette instance, enregistrer son ID
            if user_id:
                batch_data["created_by"] = user_id
            
            # Référence au document spécifique
            batch_doc_ref = batches_ref.document(batch_id)
            
            # Sauvegarder les données dans Firebase
            batch_doc_ref.set(batch_data)
            
            print(f"Lot bancaire sauvegardé avec succès: {batch_id}")
            return batch_id
            
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du lot bancaire: {e}")
            return None


    def get_onboarding_temp_mandate(self, path):
        """
        Vérifie si des données d'onboarding existent sous le chemin spécifié et retourne le nom de l'entreprise.
        
        Args:
            path (str): Chemin dans Firebase vers les données onboarding
            
        Returns:
            str or None: Nom de l'entreprise si trouvé, None sinon
        """
        try:
            # Récupérer le document depuis Firestore
            doc_ref = self.db.document(path)
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                if 'base_info' in data:
                    base_info = data['base_info']
                    # Vérifier si business_name est renseigné, sinon utiliser company_name
                    if 'business_name' in base_info and base_info['business_name']:
                        return base_info['business_name']
                    elif 'company_name' in base_info and base_info['company_name']:
                        return base_info['company_name']
            
            return None
        except Exception as e:
            print(f"Erreur lors de la récupération des données d'onboarding: {e}")
            import traceback
            traceback.print_exc()
            return None

    def download_file_from_storage(self, source_path, destination_path):
        """
        Télécharge un fichier depuis Firebase Storage.
        
        Args:
            source_path (str): Chemin du fichier dans Firebase Storage
            destination_path (str): Chemin local pour enregistrer le fichier
            
        Returns:
            bool: True si succès, False sinon
        """
        bucket_name = "pinnokio-gpt.appspot.com"
        bucket = storage.bucket(name=bucket_name)
        blob = bucket.blob(source_path)
        
        try:
            blob.download_to_filename(destination_path)
            return True
        except Exception as e:
            print(f"Erreur de téléchargement depuis Firebase Storage: {e}")
            return False


    def fetch_all_blogs(self, limit: int = 100):
        """
        Récupère tous les articles de blog depuis Firestore, triés par date décroissante.
        """
        try:
            blogs_ref = self.db.collection('blogs')
            # Trier par le champ 'date' (qui est un Timestamp Firestore) en ordre décroissant
            query = blogs_ref.order_by('date', direction=firestore.Query.DESCENDING).limit(limit)
            docs = query.stream()

            blog_list = []
            for doc in docs:
                post_data = doc.to_dict()
                post_data['slug'] = doc.id # Ajouter le slug (ID du document)

                # Convertir le Timestamp Firestore en string pour l'affichage
                if 'date' in post_data and isinstance(post_data['date'], datetime):
                    # Formater la date comme vous le souhaitez (ex: Month Day, Year)
                    post_data['date'] = post_data['date'].strftime('%B %d, %Y')
                else:
                     post_data['date'] = "Date inconnue" # Fallback

                blog_list.append(post_data)

            print(f"Récupéré {len(blog_list)} articles de blog depuis Firestore.")
            return blog_list
        except Exception as e:
            print(f"Erreur lors de la récupération des blogs depuis Firestore: {e}")
            return [] # Retourner une liste vide en cas d'erreur

    def fetch_blog_by_slug(self, slug: str):
        """
        Récupère un article de blog spécifique par son slug depuis Firestore.
        """
        try:
            doc_ref = self.db.collection('blogs').document(slug)
            doc = doc_ref.get()

            if doc.exists:
                post_data = doc.to_dict()
                post_data['slug'] = doc.id

                # Convertir le Timestamp Firestore en string
                if 'date' in post_data and isinstance(post_data['date'], datetime):
                    post_data['date'] = post_data['date'].strftime('%B %d, %Y')
                else:
                    post_data['date'] = "Date inconnue"

                print(f"Blog '{slug}' trouvé dans Firestore.")
                return post_data
            else:
                print(f"Blog '{slug}' non trouvé dans Firestore.")
                return None
        except Exception as e:
            print(f"Erreur lors de la récupération du blog '{slug}' depuis Firestore: {e}")
            return None
    def load_dms_types(self):
        """
        Charge les types de DMS dans la collection /settings_param/dms_type.
        Si les données n'existent pas déjà, elles seront créées.
        """
        # Vérifier si la collection existe déjà
        dms_ref = self.db.collection('settings_param').document('dms_type')
        dms_doc = dms_ref.get()
        
        if not dms_doc.exists or not dms_doc.to_dict().get('dms_types'):
            # Définir les types de DMS connus
            dms_data = {
                'dms_types': [
                    {'id': '1', 'dms_displayname': 'Google Drive', 'dms_name': 'google_drive'},
                    {'id': '2', 'dms_displayname': 'Microsoft OneDrive', 'dms_name': 'onedrive'},
                    {'id': '3', 'dms_displayname': 'Dropbox', 'dms_name': 'dropbox'},
                    {'id': '4', 'dms_displayname': 'SharePoint', 'dms_name': 'sharepoint'}
                ]
            }
            
            # Enregistrer les données dans Firestore
            dms_ref.set(dms_data)
            print(f"Les types de DMS ont été chargés dans la collection settings_param/dms_type.")
            return dms_data['dms_types']
        else:
            print(f"Les types de DMS existent déjà dans la collection settings_param/dms_type.")
            return dms_doc.to_dict().get('dms_types', [])

    def load_erp_types(self):
        """
        Charge les types d'ERP dans la collection /settings_param/erp_type.
        Si les données n'existent pas déjà, elles seront créées.
        """
        # Vérifier si la collection existe déjà
        erp_ref = self.db.collection('settings_param').document('erp_type')
        erp_doc = erp_ref.get()
        
        if not erp_doc.exists or not erp_doc.to_dict().get('erp_types'):
            # Définir les types d'ERP connus
            erp_data = {
                'erp_types': [
                    {'id': '1', 'erp_displayname': 'Odoo', 'erp_name': 'odoo'},
                    {'id': '2', 'erp_displayname': 'SAP', 'erp_name': 'sap'},
                    {'id': '3', 'erp_displayname': 'Oracle', 'erp_name': 'oracle'},
                    {'id': '4', 'erp_displayname': 'Microsoft Dynamics', 'erp_name': 'dynamics'}
                ]
            }
            
            # Enregistrer les données dans Firestore
            erp_ref.set(erp_data)
            print(f"Les types d'ERP ont été chargés dans la collection settings_param/erp_type.")
            return erp_data['erp_types']
        else:
            print(f"Les types d'ERP existent déjà dans la collection settings_param/erp_type.")
            return erp_doc.to_dict().get('erp_types', [])

    def load_chat_types(self):
        """
        Charge les types de chat dans la collection /settings_param/chat_type.
        Si les données n'existent pas déjà, elles seront créées.
        """
        # Vérifier si la collection existe déjà
        chat_ref = self.db.collection('settings_param').document('chat_type')
        chat_doc = chat_ref.get()
        
        if not chat_doc.exists or not chat_doc.to_dict().get('chat_types'):
            # Définir les types de chat connus
            chat_data = {
                'chat_types': [
                    {'id': '1', 'chat_displayname': 'Pinnokio', 'chat_name': 'pinnokio'},
                    {'id': '2', 'chat_displayname': 'Slack', 'chat_name': 'slack'},
                    {'id': '3', 'chat_displayname': 'Microsoft Teams', 'chat_name': 'teams'},
                    {'id': '4', 'chat_displayname': 'Discord', 'chat_name': 'discord'}
                ]
            }
            
            # Enregistrer les données dans Firestore
            chat_ref.set(chat_data)
            print(f"Les types de chat ont été chargés dans la collection settings_param/chat_type.")
            return chat_data['chat_types']
        else:
            print(f"Les types de chat existent déjà dans la collection settings_param/chat_type.")
            return chat_doc.to_dict().get('chat_types', [])

    def load_param_data(self, param_type):
        """
        Charge les données d'un type de paramètre spécifique.
        
        Args:
            param_type (str): Type de paramètre à charger ('erp', 'dms', 'chat', 'currencies')
        
        Returns:
            list: Liste des paramètres chargés
        """
        if param_type == 'communication':
            # Données par défaut pour communication
            return [
                {
                    "id": 1,
                    "communication_displayname": "Pinnokio",
                    "communication_name": "pinnokio",
                    "is_active": True
                },
                {
                    "id": 2,
                    "communication_displayname": "Telegram",
                    "communication_name": "telegram",
                    "is_active": True
                },
                {
                    "id": 3,
                    "communication_displayname": "WhatsApp",
                    "communication_name": "whatsapp",
                    "is_active": False
                }
            ]
        elif param_type == 'erp':
            return self.load_erp_types()
        elif param_type == 'dms':
            return self.load_dms_types()
        elif param_type == 'chat':
            return self.load_chat_types()
        elif param_type == 'currencies':
            return self.load_currencies()
        else:
            print(f"Type de paramètre inconnu: {param_type}")
            return []

    def get_param_data(self, param_type):
        """
        Récupère les données d'un type de paramètre spécifique.
        
        Args:
            param_type (str): Type de paramètre à récupérer ('erp', 'dms', 'chat', 'currencies')
        
        Returns:
            list: Liste des paramètres
        """
        try:
            if param_type not in ['erp', 'dms', 'chat', 'currencies','communication']:
                print(f"Type de paramètre inconnu: {param_type}")
                return []
            
            collection_name = {
                'erp': 'erp_type',
                'dms': 'dms_type',
                'chat': 'chat_type',
                'currencies': 'currencies',
                'communication': 'communication_type'
            }[param_type]
            
            field_name = {
                'erp': 'erp_types',
                'dms': 'dms_types',
                'chat': 'chat_types',
                'currencies': 'currencies',
                'communication': 'communication_types'
            }[param_type]
            
            param_ref = self.db.collection('settings_param').document(collection_name)
            param_doc = param_ref.get()
            
            if param_doc.exists:
                data = param_doc.to_dict().get(field_name, [])
                print(f"✅ Données {param_type} récupérées: {len(data)} éléments")
                return data
            else:
                print(f"⚠️ Document {collection_name} non trouvé, création avec données par défaut")
                # Créer avec les données par défaut
                default_data = self.load_param_data(param_type)
                param_ref.set({field_name: default_data})
                return default_data
        except Exception as e:
            print(f"❌ Erreur lors de la récupération des paramètres {param_type}: {str(e)}")
            return None

    def add_new_param(self, param_type, param_data):
        """
        Ajoute un nouveau paramètre à la collection.
        
        Args:
            param_type (str): Type de paramètre ('erp', 'dms', 'chat', 'currencies')
            param_data (dict): Données du paramètre à ajouter
        
        Returns:
            bool: True si l'ajout a réussi, False sinon
        """
        if param_type not in ['erp', 'dms', 'chat', 'currencies', 'communication']:
            print(f"Type de paramètre inconnu: {param_type}")
            return False
        
        collection_name = {
            'erp': 'erp_type',
            'dms': 'dms_type',
            'chat': 'chat_type',
            'currencies': 'currencies',
            'communication': 'communication_type',
            'communication': 'communication_types'
        }[param_type]
        
        field_name = {
            'erp': 'erp_types',
            'dms': 'dms_types',
            'chat': 'chat_types',
            'currencies': 'currencies',
            'communication': ['id', 'communication_displayname', 'communication_name', 'is_active']
        }[param_type]
        
        # Vérifier les champs requis
        required_fields = {
            'erp': ['id', 'erp_displayname', 'erp_name'],
            'dms': ['id', 'dms_displayname', 'dms_name'],
            'chat': ['id', 'chat_displayname', 'chat_name'],
            'currencies': ['id', 'currency_iso_code', 'region', 'name'],
            'communication': ['id', 'communication_displayname', 'communication_name', 'is_active']
        }[param_type]
        
        if not all(key in param_data for key in required_fields):
            print(f"Erreur: Les données doivent contenir {', '.join(required_fields)}")
            return False
        
        # Récupérer les paramètres existants
        param_ref = self.db.collection('settings_param').document(collection_name)
        param_doc = param_ref.get()
        
        if param_doc.exists:
            params = param_doc.to_dict().get(field_name, [])
            
            # Vérifier si le paramètre existe déjà
            if any(param['id'] == param_data['id'] for param in params):
                print(f"Le paramètre avec l'id {param_data['id']} existe déjà.")
                return False
            
            # Ajouter le nouveau paramètre
            params.append(param_data)
            
            # Mettre à jour le document
            param_ref.update({field_name: params})
            print(f"Le paramètre {param_data['id']} a été ajouté avec succès.")
            return True
        else:
            # Si aucun paramètre n'existe, charger d'abord les paramètres par défaut
            params = self.load_param_data(param_type)
            
            # Puis ajouter le nouveau paramètre
            params.append(param_data)
            
            # Mettre à jour le document
            param_ref.update({field_name: params})
            print(f"Le paramètre {param_data['id']} a été ajouté avec succès.")
            return True

    def load_currencies(self):
        """
        Charge les devises connues dans la collection /settings_param/currencies.
        Si les données n'existent pas déjà, elles seront créées.
        """
        # Vérifier si la collection de devises existe déjà
        currencies_ref = self.db.collection('settings_param').document('currencies')
        currencies_doc = currencies_ref.get()
        
        if not currencies_doc.exists or not currencies_doc.to_dict().get('currencies'):
            # Définir les devises connues par région
            currencies_data = {
                'currencies': [
                    # Europe
                    {'id': 'EUR', 'currency_iso_code': 'EUR', 'region': 'Europe', 'name': 'Euro'},
                    {'id': 'GBP', 'currency_iso_code': 'GBP', 'region': 'Europe', 'name': 'British Pound'},
                    {'id': 'CHF', 'currency_iso_code': 'CHF', 'region': 'Europe', 'name': 'Swiss Franc'},
                    {'id': 'NOK', 'currency_iso_code': 'NOK', 'region': 'Europe', 'name': 'Norwegian Krone'},
                    {'id': 'SEK', 'currency_iso_code': 'SEK', 'region': 'Europe', 'name': 'Swedish Krona'},
                    {'id': 'DKK', 'currency_iso_code': 'DKK', 'region': 'Europe', 'name': 'Danish Krone'},
                    {'id': 'PLN', 'currency_iso_code': 'PLN', 'region': 'Europe', 'name': 'Polish Zloty'},
                    
                    # Amérique
                    {'id': 'USD', 'currency_iso_code': 'USD', 'region': 'America', 'name': 'US Dollar'},
                    {'id': 'CAD', 'currency_iso_code': 'CAD', 'region': 'America', 'name': 'Canadian Dollar'},
                    {'id': 'MXN', 'currency_iso_code': 'MXN', 'region': 'America', 'name': 'Mexican Peso'},
                    {'id': 'BRL', 'currency_iso_code': 'BRL', 'region': 'America', 'name': 'Brazilian Real'},
                    {'id': 'ARS', 'currency_iso_code': 'ARS', 'region': 'America', 'name': 'Argentine Peso'},
                    {'id': 'COP', 'currency_iso_code': 'COP', 'region': 'America', 'name': 'Colombian Peso'},
                    
                    # Asie
                    {'id': 'JPY', 'currency_iso_code': 'JPY', 'region': 'Asia', 'name': 'Japanese Yen'},
                    {'id': 'CNY', 'currency_iso_code': 'CNY', 'region': 'Asia', 'name': 'Chinese Yuan'},
                    {'id': 'HKD', 'currency_iso_code': 'HKD', 'region': 'Asia', 'name': 'Hong Kong Dollar'},
                    {'id': 'SGD', 'currency_iso_code': 'SGD', 'region': 'Asia', 'name': 'Singapore Dollar'},
                    {'id': 'INR', 'currency_iso_code': 'INR', 'region': 'Asia', 'name': 'Indian Rupee'},
                    {'id': 'KRW', 'currency_iso_code': 'KRW', 'region': 'Asia', 'name': 'South Korean Won'},
                    {'id': 'THB', 'currency_iso_code': 'THB', 'region': 'Asia', 'name': 'Thai Baht'},
                    
                    # Afrique
                    {'id': 'ZAR', 'currency_iso_code': 'ZAR', 'region': 'Africa', 'name': 'South African Rand'},
                    {'id': 'NGN', 'currency_iso_code': 'NGN', 'region': 'Africa', 'name': 'Nigerian Naira'},
                    {'id': 'EGP', 'currency_iso_code': 'EGP', 'region': 'Africa', 'name': 'Egyptian Pound'},
                    {'id': 'MAD', 'currency_iso_code': 'MAD', 'region': 'Africa', 'name': 'Moroccan Dirham'},
                    {'id': 'KES', 'currency_iso_code': 'KES', 'region': 'Africa', 'name': 'Kenyan Shilling'},
                    {'id': 'XOF', 'currency_iso_code': 'XOF', 'region': 'Africa', 'name': 'CFA Franc BCEAO'},
                    {'id': 'XAF', 'currency_iso_code': 'XAF', 'region': 'Africa', 'name': 'CFA Franc BEAC'}
                ]
            }
            
            # Enregistrer les données dans Firestore
            currencies_ref.set(currencies_data)
            print(f"Les devises ont été chargées dans la collection settings_param/currencies.")
            return currencies_data['currencies']
        else:
            print(f"Les devises existent déjà dans la collection settings_param/currencies.")
            return currencies_doc.to_dict().get('currencies', [])

    def get_all_currencies(self):
        """
        Récupère toutes les devises depuis la collection /settings_param/currencies.
        Si les devises n'existent pas encore, la méthode load_currencies est appelée d'abord.
        
        Returns:
            list: Liste de toutes les devises disponibles
        """
        currencies_ref = self.db.collection('settings_param').document('currencies')
        currencies_doc = currencies_ref.get()
        
        if not currencies_doc.exists or not currencies_doc.to_dict().get('currencies'):
            # Si les devises n'existent pas, les charger d'abord
            return self.load_currencies()
        
        # Retourner la liste des devises
        return currencies_doc.to_dict().get('currencies', [])

    def add_new_currency(self, currency_data):
        """
        Ajoute une nouvelle devise à la collection.
        
        Args:
            currency_data (dict): Données de la devise à ajouter
                                (doit contenir id, currency_iso_code, region, name)
        
        Returns:
            bool: True si l'ajout a réussi, False sinon
        """
        if not all(key in currency_data for key in ['id', 'currency_iso_code', 'region', 'name']):
            print("Erreur: Les données de devise doivent contenir id, currency_iso_code, region et name")
            return False
        
        # Récupérer les devises existantes
        currencies_ref = self.db.collection('settings_param').document('currencies')
        currencies_doc = currencies_ref.get()
        
        if currencies_doc.exists:
            currencies = currencies_doc.to_dict().get('currencies', [])
            
            # Vérifier si la devise existe déjà
            if any(currency['id'] == currency_data['id'] for currency in currencies):
                print(f"La devise avec l'id {currency_data['id']} existe déjà.")
                return False
            
            # Ajouter la nouvelle devise
            currencies.append(currency_data)
            
            # Mettre à jour le document
            currencies_ref.update({'currencies': currencies})
            print(f"La devise {currency_data['id']} a été ajoutée avec succès.")
            return True
        else:
            # Si aucune devise n'existe, charger d'abord les devises par défaut
            currencies = self.load_currencies()
            
            # Puis ajouter la nouvelle devise
            currencies.append(currency_data)
            
            # Mettre à jour le document
            currencies_ref.update({'currencies': currencies})
            print(f"La devise {currency_data['id']} a été ajoutée avec succès.")
            return True

    def upload_token_usage(self, user_id: str, data: dict, entry_id: Optional[str] = None) -> bool:
        """
        Télécharge les données d'utilisation des tokens vers Firestore.

        ⭐ Conçu pour le scaling multi-instances:
        - Chaque événement est écrit dans une sous-collection `entries/` (évite un tableau qui grossit)
        - Écriture idempotente via `entry_id` (si déjà présent, on ne re-compte pas)
        - Agrégation rapide sur le document parent via increments atomiques

        Chemins:
        - Parent:  clients/{user_id}/token_usage/{job_id}
        - Entry:   clients/{user_id}/token_usage/{job_id}/entries/{entry_id}
        """
        try:
            job_id = data.get("job_id")
            if not job_id:
                print("Erreur: job_id manquant dans les données")
                return False

            # Idempotence: si pas fourni, générer un id unique (moins robuste aux retries)
            if not entry_id:
                entry_id = data.get("entry_id") or uuid.uuid4().hex

            parent_ref = self.db.document(f"clients/{user_id}/token_usage/{job_id}")
            entry_ref = parent_ref.collection("entries").document(str(entry_id))

            # Déduire une date de facturation si le job_id suit le pattern chat journalier:
            # chat:{user_id}:{collection_name}:{YYYY-MM-DD}
            billing_kind = None
            billing_date = None
            try:
                if isinstance(job_id, str) and job_id.startswith("chat:"):
                    parts = job_id.split(":")
                    if len(parts) >= 4:
                        candidate = parts[-1]
                        # format attendu YYYY-MM-DD
                        if re.match(r"^\d{4}-\d{2}-\d{2}$", candidate):
                            billing_kind = "chat_daily"
                            billing_date = candidate
            except Exception:
                billing_kind = None
                billing_date = None

            # Helpers de conversion
            def _to_int(v: Any) -> int:
                try:
                    return int(v) if v is not None else 0
                except Exception:
                    try:
                        return int(float(v))
                    except Exception:
                        return 0

            def _to_float(v: Any) -> float:
                try:
                    return float(v) if v is not None else 0.0
                except Exception:
                    return 0.0

            input_tokens = _to_int(data.get("total_input_tokens"))
            output_tokens = _to_int(data.get("total_output_tokens"))
            total_tokens = _to_int(data.get("total_tokens"))
            if total_tokens <= 0:
                total_tokens = input_tokens + output_tokens
            buy_price = _to_float(data.get("buy_price"))
            sales_price = _to_float(data.get("sales_price"))
            resolved_function = data.get("function") or data.get("department")

            transaction = self.db.transaction()

            @firestore.transactional
            def _txn(tx):
                snap = entry_ref.get(transaction=tx)
                if snap.exists:
                    # Déjà enregistré (idempotence)
                    return True

                # Écrire l'event complet
                tx.set(entry_ref, data)

                # Agrégats sur le parent
                tx.set(parent_ref, {
                    "user_id": user_id,
                    "job_id": job_id,
                    "project_id": data.get("project_id"),
                    "function": resolved_function,
                    "collection_name": data.get("collection_name"),
                    "mandate_path": data.get("mandate_path"),
                    "billing_kind": billing_kind or data.get("billing_kind"),
                    "billing_date": billing_date or data.get("billing_date"),
                    "last_entry_at": firestore.SERVER_TIMESTAMP,
                    "entries_count": firestore.Increment(1),
                    "total_input_tokens": firestore.Increment(input_tokens),
                    "total_output_tokens": firestore.Increment(output_tokens),
                    "total_tokens": firestore.Increment(total_tokens),
                    "total_buy_price": firestore.Increment(buy_price),
                    "total_sales_price": firestore.Increment(sales_price),
                }, merge=True)
                return True

            ok = bool(_txn(transaction))
            if ok:
                print(
                    f"Données token_usage enregistrées (job={job_id}, provider={data.get('provider_name')}, entry_id={entry_id})"
                )
            return ok
        except Exception as e:
            print(f"Erreur lors du téléchargement des données d'utilisation: {e}")
            return False

    def _upsert_task_manager_billing_index(
        self,
        user_id: str,
        task_doc_id: str,
        billing_data: dict,
        department: Optional[str] = None,
        file_name: Optional[str] = None,
        mandate_path: Optional[str] = None
    ) -> bool:
        """
        Indexe les données de facturation dans task_manager selon le contrat unifié.
        
        ⚠️ Best-effort: ne doit pas casser la facturation si l'écriture échoue.
        ⚠️ Appelé UNIQUEMENT lors de la finalisation journalière (finalize_daily_chat_billing)
        
        Contrat:
        - On **écrase** toujours le sous-objet `task_manager.billing` (pas d'addition)
        - On écrit avec merge=True pour ne pas toucher aux autres champs UI
        - L'indexation ne doit jamais bloquer la facturation
        
        Champs écrits au même niveau que billing:
        - timestamp: Firestore SERVER_TIMESTAMP (date de finalisation)
        - file_name: Nom du fichier (ex: "Chat usage 12/01/2026")
        - department: Valeur du champ function (ex: "chat")
        
        Args:
            user_id: Firebase user ID
            task_doc_id: Document ID dans task_manager (généralement = job_id)
            billing_data: Objet billing à indexer (totaux cumulatifs)
            department: Département (ex: "chat", "router") - sera aussi écrit comme champ racine
            file_name: Nom de fichier pour affichage UI
            mandate_path: Chemin du mandat (pour cohérence)
            
        Returns:
            bool: True si succès, False sinon (non bloquant)
        """
        try:
            task_manager_ref = self.db.document(f"clients/{user_id}/task_manager/{task_doc_id}")
            
            # Construire le patch selon le contrat
            # Champs au même niveau que billing (demandé par l'utilisateur)
            patch = {
                "job_id": task_doc_id,
                "billing": billing_data,  # Écrasement du sous-objet billing (pas d'addition)
                "timestamp": firestore.SERVER_TIMESTAMP,  # Date de finalisation (format Firestore Timestamp)
            }
            
            # Champs UI index au même niveau que billing
            if department:
                patch["department"] = department
            if file_name:
                patch["file_name"] = file_name
            if mandate_path:
                patch["mandate_path"] = mandate_path
            
            # Écriture avec merge=True (ne pas écraser les autres champs)
            task_manager_ref.set(patch, merge=True)
            
            logger.debug(
                f"[BILLING] Indexation task_manager réussie: {task_doc_id} "
                f"(tokens={billing_data.get('total_tokens', 0)}, "
                f"price={billing_data.get('total_sales_price', 0.0)})"
            )
            return True
            
        except Exception as e:
            logger.warning(
                f"[BILLING] Erreur non bloquante indexation task_manager "
                f"(task_doc_id={task_doc_id}): {e}"
            )
            return False



    def test_strip(self):
        try:
            account=stripe.Account.retrieve()
            print("✅ Connexion réussie!")
            print(f"Compte: {account.id}")
            print(f"Email: {account.email}")
            print(f"Devise par défaut: {account.default_currency}")
        except Exception as e:
            print("❌ Erreur de connexion à Stripe:")
            print(str(e))

    def download_all_languages(self):
        """Télécharge tous les documents de settings_param/languages/items."""
        collection_ref = self.db.collection('settings_param').document('languages').collection('items')
        docs = collection_ref.stream()
        return {doc.id: doc.to_dict() for doc in docs}

    def get_countries_list(self):
        """Récupère la liste des pays depuis Firebase.
        
        Returns:
            tuple: Un tuple contenant (liste des pays triés, dictionnaire des IDs de pays)
        """
        try:
            countries_ref = self.db.collection('settings_param/companies_legal_form/countries')
            countries_docs = countries_ref.stream()
            
            countries_data = []
            country_id_map = {}
            
            for doc in countries_docs:
                country_data = doc.to_dict()
                country_name = country_data.get('name', '').strip()
                country_id = int(country_data.get("id"))
                
                if country_name:
                    countries_data.append((country_id, country_name))
                    country_id_map[country_name] = country_id
            
            # Trier par nom de pays
            countries_list = sorted([name for _, name in countries_data])
            
            return countries_list, country_id_map
        except Exception as e:
            print(f"Erreur lors de la récupération des pays: {str(e)}")
            return [], {}

    def get_legal_forms_for_country(self, country_name, country_id_map):
        """Récupère les formes légales pour un pays spécifique."""
        try:
            if not country_name or country_name not in country_id_map:
                return []
            
            country_id = country_id_map[country_name]
            country_doc_ref = self.db.document(f'settings_param/companies_legal_form/countries/{country_name}')
            country_doc = country_doc_ref.get()
            
            if not country_doc.exists:
                return []
            
            country_data = country_doc.to_dict()
            legal_forms = country_data.get('legal_forms', [])
            
            combined_forms = []
            
            # Vérifier si legal_forms est un dictionnaire
            if isinstance(legal_forms, dict):
                for form_id, form_data in legal_forms.items():
                    form_name = form_data.get('name', '').strip()
                    form_description = form_data.get('description', '').strip()
                    
                    if form_name and form_description:
                        combined_form = f"{form_name} - {form_description}"
                    else:
                        combined_form = form_name or form_description
                    
                    if combined_form:
                        combined_forms.append(combined_form)
            # Si legal_forms est une liste
            elif isinstance(legal_forms, list):
                for form_data in legal_forms:
                    if isinstance(form_data, dict):
                        form_name = form_data.get('name', '').strip()
                        form_description = form_data.get('description', '').strip()
                        
                        if form_name and form_description:
                            combined_form = f"{form_name} - {form_description}"
                        else:
                            combined_form = form_name or form_description
                        
                        if combined_form:
                            combined_forms.append(combined_form)
                    elif isinstance(form_data, str):
                        # Si c'est juste une chaîne, l'utiliser directement
                        combined_forms.append(form_data.strip())
            
            return sorted(combined_forms)
        except Exception as e:
            print(f"Erreur lors de la récupération des formes légales: {str(e)}")
            return []

    def get_all_expenses(self, mandate_path):
        """
        Récupère tous les jobs de dépenses pour un mandat donné.
        
        Args:
            mandate_path (str): Chemin du mandat client
            
        Returns:
            dict: Dictionnaire des jobs avec leur ID comme clé, ou dict vide en cas d'erreur
        """
        try:
            mandate_path = self._normalize_mandate_path(mandate_path)
            # Chemin de la collection des dépenses
            expenses_collection = self.db.collection(f"{mandate_path}/billing/topping/expenses")
            
            # Récupérer tous les documents (jobs) de la collection
            expenses_docs = expenses_collection.stream()
            
            expenses_data = {}
            for doc in expenses_docs:
                job_id = doc.id
                job_data = doc.to_dict()
                expenses_data[job_id] = job_data
            
            return expenses_data
        
        except Exception as e:
            print(f"Erreur lors de la récupération des dépenses: {e}")
            return {}
    
    def fetch_task_manager_details(self, user_id: str, job_id: str, department: str, mandate_path: str = None) -> Optional[dict]:
        """
        Récupère les détails task_manager pour un job donné (APBookeeper et Router uniquement).
        
        Args:
            user_id: ID de l'utilisateur Firebase
            job_id: ID du job (correspond au job_id dans expenses - peut contenir un préfixe département)
            department: Département du job ("apbookeeper", "router", etc.)
            mandate_path: Chemin du mandat (optionnel, pour validation)
            
        Returns:
            dict avec structure:
            {
                "payload": {...},           # Données du payload
                "metrics": {...},            # Métriques de traitement (APBookeeper_step_status, etc.)
                "initial_data": {...},       # Données depuis document/initial_data
                "departement": str,          # Validation du département
                "found": bool                # Si le document existe
            }
            ou None si erreur
        """
        try:
            if not user_id or not job_id or not department:
                print(f"❌ [TASK_MANAGER] Paramètres manquants: user_id={user_id}, job_id={job_id}, department={department}")
                return None
            
            # Normaliser le département
            department_lower = department.lower()
            
            # ══════════════════════════════════════════════════════════════
            # Nettoyer le job_id : enlever les préfixes de département
            # Le job_id dans expenses peut être "Apbookeeper_klk_xxx" ou "router_xxx"
            # Mais dans task_manager, le document ID est juste "klk_xxx"
            # ══════════════════════════════════════════════════════════════
            clean_job_id = job_id
            department_prefixes = [
                "Apbookeeper_", "apbookeeper_", "APBookeeper_",
                "Router_", "router_", "ROUTER_",
                "Banker_", "banker_", "BANKER_",
                "Bank_", "bank_", "BANK_"
            ]
            
            for prefix in department_prefixes:
                if clean_job_id.startswith(prefix):
                    clean_job_id = clean_job_id[len(prefix):]
                    print(f"🔧 [TASK_MANAGER] Job ID nettoyé: {job_id} → {clean_job_id}")
                    break
            
            # Construire le chemin task_manager
            if user_id:
                task_manager_path = f'clients/{user_id}/task_manager'
            else:
                task_manager_path = 'task_manager'
            
            # Essayer d'abord avec le job_id nettoyé
            task_doc_ref = self.db.collection(task_manager_path).document(clean_job_id)
            task_doc = task_doc_ref.get()
            
            # Si pas trouvé avec le job_id nettoyé, essayer avec le job_id original
            if not task_doc.exists and clean_job_id != job_id:
                print(f"🔍 [TASK_MANAGER] Document non trouvé avec {clean_job_id}, tentative avec {job_id}")
                task_doc_ref = self.db.collection(task_manager_path).document(job_id)
                task_doc = task_doc_ref.get()
            
            if not task_doc.exists:
                print(f"⚠️ [TASK_MANAGER] Document non trouvé: essayé {task_manager_path}/{clean_job_id} et {task_manager_path}/{job_id}")
                return {
                    "found": False,
                    "payload": {},
                    "metrics": {},
                    "initial_data": {},
                    "departement": None
                }
            
            # Utiliser l'ID du document réellement trouvé
            found_doc_id = task_doc.id
            task_data = task_doc.to_dict() or {}
            
            # Validation: vérifier que le département correspond (case-insensitive)
            task_department = task_data.get('departement', '').lower()
            # Aussi chercher dans le champ 'department' (sans 'e')
            if not task_department:
                task_department = task_data.get('department', '').lower()
            
            if task_department and task_department != department_lower:
                print(f"⚠️ [TASK_MANAGER] Département mismatch: attendu={department_lower}, trouvé={task_department}")
                # On continue quand même, mais on log l'avertissement
            else:
                print(f"✓ [TASK_MANAGER] Document trouvé: {found_doc_id} (département: {task_department or department_lower})")
            
            # Extraire les données selon le département
            result = {
                "found": True,
                "job_id_clean": found_doc_id,
                "job_id_original": job_id,
                "departement": task_data.get('departement') or task_data.get('department', department),
                "status": task_data.get('status', ''),
                "uri_file_link": task_data.get('uri_file_link', ''),
                "payload": {},
                "metrics": {},
                "initial_data": {},
                "department_data": {}
            }
            
            # 1. Payload avec jobs_data
            if 'payload' in task_data and isinstance(task_data['payload'], dict):
                result["payload"] = task_data['payload']
            
            # 2. Métriques de traitement (spécifique à APBookeeper)
            if department_lower == "apbookeeper":
                if 'APBookeeper_step_status' in task_data:
                    result["metrics"]["step_status"] = task_data['APBookeeper_step_status']
            
            # 3. department_data - Données métier du département
            # Contient les données extraites spécifiques au département (APbookeeper, Router, etc.)
            if 'department_data' in task_data and isinstance(task_data['department_data'], dict):
                result["department_data"] = task_data['department_data']
                print(f"📊 [TASK_MANAGER] department_data trouvé avec clés: {list(task_data['department_data'].keys())}")
            
            # 4. Données initiales depuis document/initial_data (sous-collection)
            try:
                initial_data_path = f'{task_manager_path}/{found_doc_id}/document/initial_data'
                initial_data_ref = self.db.document(initial_data_path)
                initial_data_doc = initial_data_ref.get()
                
                if initial_data_doc.exists:
                    initial_data_dict = initial_data_doc.to_dict() or {}
                    result["initial_data"] = initial_data_dict.get('initial_data', {})
            except Exception as e:
                print(f"⚠️ [TASK_MANAGER] Erreur récupération initial_data: {e}")
                # Non bloquant, on continue
            
            print(f"✅ [TASK_MANAGER] Détails récupérés pour {job_id} (département: {department_lower})")
            return result
            
        except Exception as e:
            print(f"❌ [TASK_MANAGER] Erreur lors de la récupération des détails: {e}")
            import traceback
            traceback.print_exc()
            return None

    def list_task_manager_by_mandate_path(self, user_id: str, mandate_path: str, limit: int = 2000) -> list:
        """
        Liste les documents `clients/{user_id}/task_manager` filtrés par `mandate_path`.

        Pourquoi:
        - `task_manager` contient tous les jobs du compte utilisateur.
        - Le dashboard doit afficher uniquement la société sélectionnée (mandate_path courant).
        """
        try:
            if not user_id or not mandate_path:
                return []

            task_manager_path = f"clients/{user_id}/task_manager"
            ref = self.db.collection(task_manager_path)

            try:
                query = ref.where(filter=FieldFilter("mandate_path", "==", mandate_path)).limit(int(limit))
                docs = query.stream()
                out = []
                for doc in docs:
                    data = doc.to_dict() or {}
                    data["id"] = doc.id
                    out.append(data)
                return out
            except Exception:
                # Fallback: stream + filter en python (si FieldFilter/index non dispo)
                docs = ref.stream()
                out = []
                for doc in docs:
                    data = doc.to_dict() or {}
                    if data.get("mandate_path") == mandate_path:
                        data["id"] = doc.id
                        out.append(data)
                        if len(out) >= int(limit):
                            break
                return out
        except Exception as e:
            print(f"❌ Erreur list_task_manager_by_mandate_path: {e}")
            return []
    
    def fetch_expenses_by_mandate(self, mandate_path: str, status: Optional[str] = None) -> Dict:
        """
        Récupère les notes de frais depuis Firebase pour un mandat donné.
        
        NOUVELLE STRUCTURE (v2):
            {mandate_path}/working_doc/expenses_details (document)
            └── items (sous-collection)
                └── {job_id} (document) { ...expense_data }
        
        ANCIENNE STRUCTURE (legacy - migration automatique):
            {mandate_path}/working_doc/expenses_details (document)
            └── items: { "job_id": {...}, ... }  (champ map dans le document)
        
        Args:
            mandate_path: Chemin du mandat
            status: Filtrer par statut ("to_process", "running", "close", None=tous)
        
        Returns:
            Dict: Dictionnaire des expenses avec job_id comme clé
        """
        try:
            base_doc_path = f"{mandate_path}/working_doc/expenses_details"
            items_collection_path = f"{base_doc_path}/items"
            
            # 1. Essayer de lire la NOUVELLE structure (sous-collection items)
            collection_ref = self.db.collection(items_collection_path)
            docs = list(collection_ref.stream())
            
            if docs:
                expenses_data = {}
                for doc in docs:
                    job_id = doc.id
                    expense_data = doc.to_dict() or {}
                    if status and expense_data.get("status") != status:
                        continue
                    expenses_data[job_id] = expense_data
                print(f"✅ [FIREBASE] {len(expenses_data)} expenses récupérées depuis collection: {items_collection_path}")
                return expenses_data
            
            # 2. Fallback: ANCIENNE structure (document avec champ items map)
            doc_ref = self.db.document(base_doc_path)
            doc = doc_ref.get()
            if doc.exists:
                doc_data = doc.to_dict() or {}
                items = dict(doc_data.get("items", {}) or {})
                if items:
                    print(f"📦 [FIREBASE] Ancienne structure détectée ({len(items)} items) - à migrer")
                    if status:
                        items = {k: v for k, v in items.items() if isinstance(v, dict) and v.get("status") == status}
                    return items
            
            # Note: L'ancien fallback legacy {mandate_path}/working_doc a été supprimé
            # car ce chemin a un nombre impair de segments (collection, pas document)
            # et n'était probablement pas utilisé dans la pratique.
            
            print(f"ℹ️ [FIREBASE] Aucune expense trouvée pour: {mandate_path}")
            return {}

        except Exception as e:
            print(f"❌ [FIREBASE] Erreur lors de la récupération des expenses: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def migrate_expenses_to_collection(self, mandate_path: str) -> bool:
        """
        Migre les expenses de l'ancienne structure (document avec items map) vers 
        la nouvelle structure (collection de documents individuels).
        
        Cette méthode est appelée automatiquement lors du chargement si l'ancienne
        structure est détectée.
        
        AVANT (ancienne structure):
            {mandate_path}/working_doc/expenses_details (document)
            └── items: { "job_id_1": {...}, "job_id_2": {...} }
        
        APRÈS (nouvelle structure):
            {mandate_path}/working_doc/expenses_details (document)
            └── items (sous-collection)
                └── job_id_1 (document) { ...expense_data }
                └── job_id_2 (document) { ...expense_data }
        
        Args:
            mandate_path: Chemin du mandat
        
        Returns:
            bool: True si migration réussie ou pas nécessaire, False sinon
        """
        try:
            base_doc_path = f"{mandate_path}/working_doc/expenses_details"
            items_collection_path = f"{base_doc_path}/items"
            
            # Vérifier si l'ancienne structure existe
            doc_ref = self.db.document(base_doc_path)
            doc = doc_ref.get()
            
            if not doc.exists:
                print(f"ℹ️ [MIGRATION] Pas d'ancienne structure à migrer pour: {base_doc_path}")
                return True
            
            doc_data = doc.to_dict() or {}
            items = dict(doc_data.get("items", {}) or {})
            
            if not items:
                print(f"ℹ️ [MIGRATION] Document existe mais sans items à migrer: {base_doc_path}")
                return True
            
            print(f"🔄 [MIGRATION] Début migration de {len(items)} expenses vers sous-collection items...")
            
            # Créer chaque expense comme document individuel dans la sous-collection items
            collection_ref = self.db.collection(items_collection_path)
            batch = self.db.batch()
            batch_count = 0
            migrated_count = 0
            
            for job_id, expense_data in items.items():
                if not isinstance(expense_data, dict):
                    print(f"⚠️ [MIGRATION] Skipping invalid expense data for job_id: {job_id}")
                    continue
                
                # Créer le document dans la sous-collection items
                expense_doc_ref = collection_ref.document(job_id)
                batch.set(expense_doc_ref, expense_data)
                batch_count += 1
                migrated_count += 1
                
                # Firestore batch limit est 500 opérations
                if batch_count >= 450:
                    batch.commit()
                    print(f"✅ [MIGRATION] Batch commit: {migrated_count} expenses migrées")
                    batch = self.db.batch()
                    batch_count = 0
            
            # Commit remaining
            if batch_count > 0:
                batch.commit()
            
            print(f"✅ [MIGRATION] {migrated_count} expenses migrées vers sous-collection items")
            
            # Supprimer le champ items du document (garder le document pour la sous-collection)
            doc_ref.update({"items": firestore.DELETE_FIELD})
            print(f"🗑️ [MIGRATION] Champ 'items' supprimé du document: {base_doc_path}")
            
            print(f"✅ [MIGRATION] Migration terminée avec succès pour: {mandate_path}")
            return True
            
        except Exception as e:
            print(f"❌ [MIGRATION] Erreur lors de la migration: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def check_and_migrate_expenses(self, mandate_path: str) -> bool:
        """
        Vérifie si une migration est nécessaire et l'effectue si besoin.
        
        Args:
            mandate_path: Chemin du mandat
        
        Returns:
            bool: True si aucune migration nécessaire ou migration réussie
        """
        try:
            base_doc_path = f"{mandate_path}/working_doc/expenses_details"
            
            # Vérifier si l'ancienne structure existe (document avec champ items map)
            doc_ref = self.db.document(base_doc_path)
            doc = doc_ref.get()
            
            if doc.exists:
                doc_data = doc.to_dict() or {}
                items = dict(doc_data.get("items", {}) or {})
                if items:
                    print(f"⚠️ [FIREBASE] Ancienne structure détectée (champ items) - lancement migration...")
                    return self.migrate_expenses_to_collection(mandate_path)
            
            return True
            
        except Exception as e:
            print(f"❌ [FIREBASE] Erreur lors de la vérification de migration: {e}")
            return False
    
    def update_expense_in_firebase(
        self, 
        mandate_path: str, 
        expense_id: str, 
        update_data: Dict
            ) -> bool:
        """
        Met à jour un ou plusieurs champs d'une expense dans Firebase.
        
        NOUVELLE STRUCTURE (v2):
            {mandate_path}/working_doc/expenses_details/items/{expense_id} (document dans sous-collection)
        
        ANCIENNE STRUCTURE (legacy):
            {mandate_path}/working_doc/expenses_details (document) → items.{expense_id} (champ map)
            {mandate_path}/working_doc (document) → expense_details.{expense_id}
        
        Args:
            mandate_path: Chemin du mandat
            expense_id: ID de l'expense (job_id) à mettre à jour
            update_data: Dictionnaire des champs à mettre à jour
        
        Returns:
            bool: True si succès, False sinon
        """
        try:
            base_doc_path = f"{mandate_path}/working_doc/expenses_details"
            items_collection_path = f"{base_doc_path}/items"
            
            # 1. NOUVELLE STRUCTURE: Document individuel dans sous-collection items
            expense_doc_ref = self.db.collection(items_collection_path).document(expense_id)
            expense_doc = expense_doc_ref.get()
            if expense_doc.exists:
                print(f"✏️ [FIREBASE] Mise à jour expense {expense_id} dans sous-collection: {items_collection_path}/{expense_id}")
                expense_doc_ref.update(update_data or {})
                print(f"✅ [FIREBASE] Expense {expense_id} mise à jour avec succès (sous-collection)")
                return True
            
            # 2. ANCIENNE STRUCTURE: Document avec items map (champ)
            doc_ref = self.db.document(base_doc_path)
            doc = doc_ref.get()
            if doc.exists:
                doc_data = doc.to_dict() or {}
                items = dict(doc_data.get("items", {}) or {})
                if expense_id in items:
                    print(f"✏️ [FIREBASE] Mise à jour expense {expense_id} dans champ items: {base_doc_path}")
                    update_fields = {f"items.{expense_id}.{field}": value for field, value in (update_data or {}).items()}
                    doc_ref.update(update_fields)
                    print(f"✅ [FIREBASE] Expense {expense_id} mise à jour avec succès (champ items)")
                    return True
            
            # 3. FALLBACK LEGACY: working_doc avec expense_details
            legacy_doc_path = f"{mandate_path}/working_doc"
            legacy_doc_ref = self.db.document(legacy_doc_path)
            legacy_doc = legacy_doc_ref.get()
            if legacy_doc.exists:
                legacy_data = legacy_doc.to_dict() or {}
                if expense_id in (legacy_data.get("expense_details", {}) or {}):
                    print(f"✏️ [FIREBASE] Mise à jour expense {expense_id} dans legacy: {legacy_doc_path}")
                    update_fields = {f"expense_details.{expense_id}.{field}": value for field, value in (update_data or {}).items()}
                    legacy_doc_ref.update(update_fields)
                    print(f"✅ [FIREBASE] Expense {expense_id} mise à jour avec succès (legacy)")
                    return True
            
            print(f"⚠️ [FIREBASE] Expense {expense_id} non trouvée dans aucune structure")
            return False

        except Exception as e:
            print(f"❌ [FIREBASE] Erreur lors de la mise à jour de l'expense {expense_id}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def delete_expense_from_firebase(
        self, 
        mandate_path: str, 
        expense_id: str
        ) -> bool:
        """
        Supprime une expense de Firebase.
        
        NOUVELLE STRUCTURE (v2):
            {mandate_path}/working_doc/expenses_details/items/{expense_id} (document) → suppression du document
        
        ANCIENNE STRUCTURE (legacy):
            {mandate_path}/working_doc/expenses_details (document) → FieldValue.delete() sur items.{expense_id}
            {mandate_path}/working_doc (document) → FieldValue.delete() sur expense_details.{expense_id}
        
        Args:
            mandate_path: Chemin du mandat
            expense_id: ID de l'expense (job_id) à supprimer
        
        Returns:
            bool: True si succès, False sinon
        """
        try:
            base_doc_path = f"{mandate_path}/working_doc/expenses_details"
            items_collection_path = f"{base_doc_path}/items"
            
            # 1. NOUVELLE STRUCTURE: Document individuel dans sous-collection items
            expense_doc_ref = self.db.collection(items_collection_path).document(expense_id)
            expense_doc = expense_doc_ref.get()
            if expense_doc.exists:
                expense_data = expense_doc.to_dict() or {}
                if expense_data.get("status") == "close":
                    print("⚠️ [FIREBASE] Impossible de supprimer une expense avec status 'close'")
                    return False
                expense_doc_ref.delete()
                print(f"✅ [FIREBASE] Expense {expense_id} supprimée avec succès (sous-collection)")
                return True
            
            # 2. ANCIENNE STRUCTURE: Document avec items map (champ)
            doc_ref = self.db.document(base_doc_path)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict() or {}
                items = dict(data.get("items", {}) or {})
                if expense_id in items:
                    if isinstance(items.get(expense_id), dict) and items[expense_id].get("status") == "close":
                        print("⚠️ [FIREBASE] Impossible de supprimer une expense avec status 'close'")
                        return False
                    doc_ref.update({f"items.{expense_id}": firestore.DELETE_FIELD})
                    print(f"✅ [FIREBASE] Expense {expense_id} supprimée avec succès (champ items)")
                    return True
            
            # 3. FALLBACK LEGACY: working_doc avec expense_details
            legacy_doc_path = f"{mandate_path}/working_doc"
            legacy_doc_ref = self.db.document(legacy_doc_path)
            legacy_doc = legacy_doc_ref.get()
            if legacy_doc.exists:
                legacy_data = legacy_doc.to_dict() or {}
                expenses_data = dict(legacy_data.get("expense_details", {}) or {})
                if expense_id in expenses_data:
                    if isinstance(expenses_data.get(expense_id), dict) and expenses_data[expense_id].get("status") == "close":
                        print("⚠️ [FIREBASE] Impossible de supprimer une expense avec status 'close'")
                        return False
                    legacy_doc_ref.update({f"expense_details.{expense_id}": firestore.DELETE_FIELD})
                    print(f"✅ [FIREBASE] Expense {expense_id} supprimée avec succès (legacy)")
                    return True
            
            print(f"⚠️ [FIREBASE] Expense {expense_id} non trouvée dans aucune structure")
            return False

        except Exception as e:
            print(f"❌ [FIREBASE] Erreur lors de la suppression de l'expense {expense_id}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def create_expense_in_firebase(
        self, 
        mandate_path: str, 
        expense_id: str, 
        expense_data: Dict
        ) -> bool:
        """
        Crée une nouvelle expense dans Firebase (nouvelle structure sous-collection).
        
        STRUCTURE:
            {mandate_path}/working_doc/expenses_details/items/{expense_id} (document)
        
        Args:
            mandate_path: Chemin du mandat
            expense_id: ID de l'expense (job_id)
            expense_data: Données de l'expense
        
        Returns:
            bool: True si succès, False sinon
        """
        try:
            base_doc_path = f"{mandate_path}/working_doc/expenses_details"
            items_collection_path = f"{base_doc_path}/items"
            expense_doc_ref = self.db.collection(items_collection_path).document(expense_id)
            
            expense_doc_ref.set(expense_data)
            print(f"✅ [FIREBASE] Expense {expense_id} créée avec succès dans: {items_collection_path}/{expense_id}")
            return True
            
        except Exception as e:
            print(f"❌ [FIREBASE] Erreur lors de la création de l'expense {expense_id}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    

    def load_automated_router_context(self, mandate_path):
        """
        Charge les prompts automatisés depuis Firestore.
        Retourne (definition_prompts, instruction_prompts) si existants, sinon None.
        """
        try:
            automated_context_ref = self.db.document(f"{mandate_path}/context/automated_router_context")
            automated_snapshot = automated_context_ref.get()

            

            if automated_snapshot.exists:
                data = automated_snapshot.to_dict()
                definition_prompt = data.get("definition_prompt", {})
                instruct_prompt = data.get("instruct_prompt", {})

                '''ordered_departments = [
                    "invoices", "expenses", "banks_cash", "hr",
                    "taxes", "letters", "contrats", "financial_statement"
                ]'''

                #definition_prompts = tuple(definition_prompt.get(d, "") for d in ordered_departments)
                #instruction_prompts = tuple(instruct_prompt.get(d, "") for d in ordered_departments)

                return definition_prompt, instruct_prompt
                

            else:
                return  {}, {}
        except Exception as e:
            print(f"Erreur lors du chargement du contexte automatisé : {e}")
            return None, None



    
    def check_job_status(self, user_id: str, job_id: str = None, file_id: str = None):
        """
        Vérifie l'état d'un job ou d'un fichier spécifique en accédant directement au document.
        
        Args:
            user_id (str): L'ID de l'utilisateur
            job_id (str, optional): L'ID du job à rechercher
            file_id (str, optional): L'ID du fichier à rechercher
        
        Returns:
            dict: Les informations du job ou du fichier, y compris son statut, ou None si aucun résultat
        """
        try:
            if not job_id and not file_id:
                print("Veuillez spécifier un job_id ou un file_id.")
                return None
            
            # Construire le chemin de la collection
            collection_path = f"clients/{user_id}/notifications"
            
            # Accéder directement au document par son ID
            if file_id:
                # Convertir en string pour sécurité (Firebase attend toujours des strings)
                doc_ref = self.db.collection(collection_path).document(str(file_id))
                doc = doc_ref.get()
                if doc.exists:
                    doc_data = doc.to_dict()
                    print(f"Fichier {file_id} trouvé avec statut: {doc_data.get('status', 'inconnu')}")
                    doc_data['document_id'] = doc.id
                    return doc_data
                else:
                    print(f"Aucun document trouvé avec file_id={file_id}")
                    return None
                    
            if job_id:
                # Convertir en string pour sécurité (Firebase attend toujours des strings)
                doc_ref = self.db.collection(collection_path).document(str(job_id))
                doc = doc_ref.get()
                if doc.exists:
                    doc_data = doc.to_dict()
                    print(f"Job {job_id} trouvé avec statut: {doc_data.get('status', 'inconnu')}")
                    doc_data['document_id'] = doc.id
                    return doc_data
                else:
                    print(f"Aucun document trouvé avec job_id={job_id}")
                    return None
            
        except Exception as e:
            print(f"Erreur lors de la vérification du statut: {e}")
            return None

    def add_top_up(self, mandate_path: str, top_up_data: dict) -> Dict[str, Any]:
        """
        Ajoute une nouvelle transaction de recharge dans Firestore et crée une session de paiement Stripe.
        
        Args:
            mandate_path (str): Chemin du mandat dans Firestore
            top_up_data (dict): Dictionnaire contenant 'currency' et 'amount'
            
        Returns:
            dict: Résultat de l'opération avec URL Stripe si applicable
        """
        try:
            # Validation des données
            if not isinstance(top_up_data, dict):
                return {
                    'success': False, 
                    'error': 'top_up_data must be a dictionary'
                }
            
            required_fields = {'currency', 'amount'}
            if not all(field in top_up_data for field in required_fields):
                return {
                    'success': False, 
                    'error': 'Missing required fields: currency and amount'
                }
            
            # Extraire l'ID utilisateur du chemin du mandat
            path_parts = mandate_path.split('/')
            user_id = path_parts[1] if len(path_parts) > 1 else None
            
            if not user_id:
                return {
                    'success': False, 
                    'error': 'Could not extract user_id from mandate_path'
                }
            
            # Convertir le montant en float
            amount = float(top_up_data['amount'])
            
            if amount <= 0:
                return {
                    'success': False, 
                    'error': 'Amount must be greater than 0'
                }
            
            # Créer une référence à la transaction
            transactions_path = f"{mandate_path}/billing/topping/transactions"
            transaction_ref = self.db.collection(transactions_path).document()
            
            # Préparer les données de la transaction - état initial "pending"
            transaction_data = {
                'currency': top_up_data['currency'],
                'amount': amount,
                'created_at': firestore.SERVER_TIMESTAMP,
                'status': 'pending',  # Initialement "pending" jusqu'à confirmation de Stripe
                'transaction_id': transaction_ref.id,
                'payment_method': 'stripe'
            }
            
            # Enregistrer la transaction dans Firebase
            transaction_ref.set(transaction_data)
            
            # Vérifier si Stripe est configuré
            if not self.stripe_api_key:
                # Mode sans Stripe - traitement immédiat comme avant
                self._process_immediate_top_up(user_id, amount, transaction_ref)
                return {
                    'success': True,
                    'message': f'Successfully added {amount} to your account (without Stripe)',
                    'immediate': True
                }
            
            # Créer une session de paiement Stripe
            try:
                # Vérifier que Stripe est disponible et configuré
                if not stripe:
                    raise Exception("Module Stripe non disponible")
                if not self.stripe_api_key:
                    raise Exception("Clé API Stripe non configurée")
                
                # Conversion du montant en centimes pour Stripe
                amount_in_cents = int(amount * 100)
                
                # Création des métadonnées pour le suivi
                metadata = {
                    'user_id': user_id,
                    'mandate_path': mandate_path,
                    'transaction_id': transaction_ref.id,
                    'transaction_path': f"{transactions_path}/{transaction_ref.id}",
                    'payment_type': 'top_up'
                }
                
                # Création de la session Stripe
                checkout_session = stripe.checkout.Session.create(
                    payment_method_types=['card'],
                    line_items=[{
                        'price_data': {
                            'currency': top_up_data['currency'],
                            'product_data': {
                                'name': 'Account Top-up',
                                'description': f'Add {amount} {top_up_data["currency"]} to your account balance',
                            },
                            'unit_amount': amount_in_cents,
                        },
                        'quantity': 1,
                    }],
                    mode='payment',
                    success_url=f"{self.stripe_success_url}?session_id={{CHECKOUT_SESSION_ID}}",
                    cancel_url=self.stripe_cancel_url,
                    metadata=metadata,
                )
                
                # Mettre à jour la transaction avec les informations de la session Stripe
                transaction_ref.update({
                    'stripe_session_id': checkout_session.id,
                    'stripe_payment_intent': checkout_session.payment_intent,
                    'stripe_created_at': firestore.SERVER_TIMESTAMP
                })
                
                return {
                    'success': True,
                    'checkout_url': checkout_session.url,
                    'transaction_id': transaction_ref.id,
                    'message': 'Transaction initiée, redirection vers la page de paiement'
                }
                
            except Exception as stripe_error:
                # En cas d'erreur avec Stripe, annuler la transaction
                transaction_ref.update({
                    'status': 'error',
                    'error_message': str(stripe_error)
                })
                
                print(f"Error creating Stripe session: {str(stripe_error)}")
                return {
                    'success': False,
                    'error': f'Stripe error: {str(stripe_error)}',
                    'message': 'Erreur lors de la création de la session de paiement'
                }
            
        except Exception as e:
            print(f"Error in add_top_up: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'message': 'Une erreur est survenue lors de l\'initialisation du paiement'
            }
    
    
    def process_immediate_top_up(self, user_id: str,amount: float, transaction_ref: str=None) -> None:
        """
        Traite immédiatement un top-up sans passer par Stripe (méthode interne).
        Utilisée lorsque Stripe n'est pas configuré ou pour le mode de développement.
        
        NOTE: En mode LOCAL/PROD, cette méthode est automatiquement déléguée au microservice
        via le proxy RPC dans __getattribute__.
        """
        # Mettre à jour la transaction comme "added"
        if transaction_ref:
            transaction_ref.update({
                'status': 'added',
                'processed_at': firestore.SERVER_TIMESTAMP
            })
        
        # Mettre à jour le solde actuel
        balance_doc_ref = self.db.document(f"clients/{user_id}/billing/current_balance")
        balance_doc = balance_doc_ref.get()
        
        # Initialiser les valeurs du solde
        current_balance = 0.0
        current_topping = 0.0
        
        if balance_doc.exists:
            balance_data = balance_doc.to_dict()
            current_balance = float(balance_data.get('current_balance', 0.0))
            current_topping = float(balance_data.get('current_topping', 0.0))
        
        # Mettre à jour les valeurs
        current_topping += amount
        current_balance += amount
        
        # Enregistrer les nouvelles valeurs
        balance_doc_ref.set({
            'current_balance': current_balance,
            'current_topping': current_topping,
            'timestamp_topping': firestore.SERVER_TIMESTAMP,
            'last_updated': firestore.SERVER_TIMESTAMP
        }, merge=True)
    


    def process_stripe_webhook(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite les webhooks Stripe pour compléter les transactions de top-up.
        
        Args:
            event_data (dict): Données de l'événement Stripe
            
        Returns:
            dict: Résultat du traitement
        """
        try:
            event_type = event_data.get('type')
            
            # Traiter uniquement les événements de paiement réussi
            if event_type == 'checkout.session.completed':
                session = event_data.get('data', {}).get('object', {})
                metadata = session.get('metadata', {})
                
                # Vérifier si c'est un top-up
                if metadata.get('payment_type') == 'top_up':
                    user_id = metadata.get('user_id')
                    mandate_path = metadata.get('mandate_path')
                    transaction_id = metadata.get('transaction_id')
                    transaction_path = metadata.get('transaction_path')
                    
                    # Vérifier les données nécessaires
                    if not all([user_id, mandate_path, transaction_id, transaction_path]):
                        return {
                            'success': False,
                            'error': 'Missing required metadata',
                            'message': 'Les métadonnées requises sont manquantes'
                        }
                    
                    # Récupérer la transaction
                    transaction_ref = self.db.document(transaction_path)
                    transaction_doc = transaction_ref.get()
                    
                    if not transaction_doc.exists:
                        return {
                            'success': False,
                            'error': 'Transaction not found',
                            'message': 'La transaction n\'existe pas'
                        }
                    
                    transaction_data = transaction_doc.to_dict()
                    amount = float(transaction_data.get('amount', 0.0))
                    
                    # Mettre à jour la transaction
                    transaction_ref.update({
                        'status': 'added',
                        'processed_at': firestore.SERVER_TIMESTAMP,
                        'stripe_payment_status': 'completed',
                        'stripe_payment_intent_status': session.get('payment_intent', {}).get('status', 'succeeded')
                    })
                    
                    # Mettre à jour le solde de l'utilisateur
                    balance_doc_ref = self.db.document(f"clients/{user_id}/billing/current_balance")
                    balance_doc = balance_doc_ref.get()
                    
                    # Initialiser les valeurs du solde
                    current_balance = 0.0
                    current_topping = 0.0
                    
                    if balance_doc.exists:
                        balance_data = balance_doc.to_dict()
                        current_balance = float(balance_data.get('current_balance', 0.0))
                        current_topping = float(balance_data.get('current_topping', 0.0))
                    
                    # Mettre à jour les valeurs
                    current_topping += amount
                    current_balance += amount
                    
                    # Enregistrer les nouvelles valeurs
                    balance_doc_ref.set({
                        'current_balance': current_balance,
                        'current_topping': current_topping,
                        'timestamp_topping': firestore.SERVER_TIMESTAMP,
                        'last_updated': firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    
                    return {
                        'success': True,
                        'transaction_id': transaction_id,
                        'amount': amount,
                        'user_id': user_id,
                        'mandate_path': mandate_path,
                        'message': f'Top-up de {amount} traité avec succès'
                    }
            
            return {
                'success': True,
                'ignored': True,
                'event_type': event_type,
                'message': f'Événement {event_type} ignoré'
            }
            
        except Exception as e:
            print(f"Error processing Stripe webhook: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'message': 'Une erreur est survenue lors du traitement du webhook'
            }

    def retrieve_stripe_session(self, session_id: str) -> Dict[str, Any]:
        """
        Récupère les détails d'une session Stripe.
        
        Args:
            session_id (str): ID de la session Stripe
            
        Returns:
            dict: Détails de la session ou erreur
        """
        try:
            if not self.stripe_api_key:
                return {
                    'success': False,
                    'error': 'Stripe not configured'
                }
                
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                'success': True,
                'session': session
            }
            
        except Exception as e:
            print(f"Error retrieving Stripe session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    
    
    def delete_notifications(self, user_id: str, job_id: Union[str, List[str]] = None):
        """
        Supprime les notifications associées à un ou plusieurs job_id spécifiques.

        Args:
            user_id (str): L'ID de l'utilisateur
            job_id (Union[str, List[str]], optional): L'ID du job ou une liste d'ID de jobs à rechercher et supprimer
            file_id (str, optional): Ignoré, présent pour cohérence avec check_job_status

        Returns:
            bool: True si suppression réussie, False sinon
        """
        try:
            if not job_id:
                print("Veuillez spécifier un job_id ou une liste de job_id.")
                return False

            # Normaliser en liste de strings
            if isinstance(job_id, str):
                job_ids: List[str] = [job_id]
            else:
                job_ids = list(job_id)
            job_ids = [str(j).strip() for j in job_ids if j is not None and str(j).strip()]
            if not job_ids:
                print("Veuillez spécifier un job_id ou une liste de job_id non vide.")
                return False

            # Construire le chemin de la collection
            collection_path = f"clients/{user_id}/notifications"
            collection_ref = self.db.collection(collection_path)

            deleted = False

            # ✅ Fast-path: si le job_id correspond à l'ID du document,
            # on peut supprimer directement clients/{user_id}/notifications/{job_id}
            # (et en batch si plusieurs job_ids).
            try:
                doc_refs = [collection_ref.document(jid) for jid in job_ids]
                snapshots = list(self.db.get_all(doc_refs))

                batch = self.db.batch()
                for snap in snapshots:
                    if snap.exists:
                        batch.delete(snap.reference)
                        print(f"Notification {snap.id} supprimée (doc_id={snap.id}).")
                        deleted = True

                if deleted:
                    batch.commit()
                    return True
            except Exception as e:
                # Si l'environnement ne supporte pas get_all/batch comme attendu, on retombe en mode legacy.
                print(f"⚠️ Suppression directe échouée, fallback legacy: {e}")

            if not deleted:
                # 🔁 Fallback legacy: parcourir la collection et matcher sur le champ 'job_id'
                # (utile si l'ID du document n'est pas le job_id).
                docs = collection_ref.stream()
                for doc in docs:
                    doc_data = doc.to_dict() or {}
                    if doc_data.get('job_id') in job_ids:
                        collection_ref.document(doc.id).delete()
                        print(f"Notification {doc.id} supprimée pour job_id {doc_data.get('job_id')}.")
                        deleted = True

                if not deleted:
                    print(f"Aucune notification trouvée pour job_id={job_ids}")
                    return False
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la suppression des notifications: {e}")
            return False

    def get_invitations_by_inviter(self, invited_by: str, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Récupère les invitations où l'utilisateur courant est l'invitant.

        Args:
            invited_by (str): UID Firebase de l'invitant
            limit (int): Nombre maximum de documents

        Returns:
            List[dict]: Liste des invitations (chaque dict inclut la clé 'id')
        """
        try:
            if not invited_by:
                return []
            ref = self.db.collection("invitations")
            try:
                query = ref.where(filter=firestore.FieldFilter("invited_by", "==", invited_by)).limit(limit)
            except Exception:
                query = ref.where(filter=FieldFilter("invited_by", "==", invited_by)).limit(limit)
            docs = query.stream()
            results: List[Dict[str, Any]] = []
            for doc in docs:
                data = doc.to_dict() or {}
                data["id"] = doc.id
                results.append(data)
            return results
        except Exception as e:
            print(f"❌ Erreur get_invitations_by_inviter: {e}")
            return []

    def get_all_users(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """
        Récupère les documents de la collection 'users' (avec id).

        Args:
            limit (int): Nombre maximum de documents.

        Returns:
            List[dict]: Liste de documents utilisateur (avec clé 'id').
        """
        try:
            ref = self.db.collection("users")
            try:
                query = ref.limit(limit)
            except Exception:
                query = ref  # fallback si .limit indisponible (rare)
            docs = query.stream()
            results: List[Dict[str, Any]] = []
            for doc in docs:
                data = doc.to_dict() or {}
                data["id"] = doc.id
                results.append(data)
            return results
        except Exception as e:
            print(f"❌ Erreur get_all_users: {e}")
            return []



    def get_unread_notifications(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Récupère la liste des notifications non lues pour un utilisateur.

        Args:
            user_id (str): L'ID Firebase de l'utilisateur
            limit (int): Nombre maximum de notifications à retourner

        Returns:
            List[dict]: Liste des notifications (dict) non lues, triées par date décroissante si possible
        """
        try:
            if not user_id:
                return []

            collection_path = f"clients/{user_id}/notifications"
            ref = self.db.collection(collection_path)

            # Firestore FieldFilter API (compatible Admin SDK récent)
            try:
                query = ref.where(filter=firestore.FieldFilter("read", "==", False)).order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
            except Exception:
                # Fallback: certains environnements ne supportent pas FieldFilter/order_by combiné
                query = ref.where(filter=FieldFilter("read", "==", False)).limit(limit)

            docs = query.stream()
            results: List[Dict[str, Any]] = []
            for doc in docs:
                data = doc.to_dict() or {}
                data["id"] = doc.id
                results.append(data)
            return results
        except Exception as e:
            print(f"❌ Erreur get_unread_notifications: {e}")
            return []
    def get_notifications(self, user_id: str, read: Optional[bool] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Récupère les notifications avec filtrage optionnel sur 'read'.

        Args:
            user_id (str): L'ID Firebase de l'utilisateur
            read (Optional[bool]): True, False, ou None pour ne pas filtrer
            limit (int): Nombre max de documents

        Returns:
            List[dict]: Liste des notifications
        """
        try:
            if not user_id:
                return []
            ref = self.db.collection(f"clients/{user_id}/notifications")
            if read is None:
                query = ref.limit(limit)
            else:
                try:
                    query = ref.where(filter=firestore.FieldFilter("read", "==", bool(read))).limit(limit)
                except Exception:
                    query = ref.where(filter=FieldFilter("read", "==", bool(read))).limit(limit)
            docs = query.stream()
            out: List[Dict[str, Any]] = []
            for doc in docs:
                data = doc.to_dict() or {}
                data["id"] = doc.id
                out.append(data)
            return out
        except Exception as e:
            print(f"❌ Erreur get_notifications: {e}")
            return []

    def fetch_routing_documents(self, user_id: str, mandate_path: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Récupère les documents de routing triés par status depuis la collection notifications.

        Cette méthode implémente la logique de tri pour les onglets de la page Routing:
        - in_process: documents avec status in ['in_queue', 'on_process', 'stopping']
        - pending: documents avec status == 'pending'
        - processed: documents avec status == 'completed'

        Note: Les documents "À traiter" (to_process) viennent du Drive, pas de cette méthode.

        Args:
            user_id (str): L'ID Firebase de l'utilisateur
            mandate_path (str): Le chemin du mandat (company_id)

        Returns:
            Dict avec les clés 'in_process', 'pending', 'processed' contenant les listes de documents
        """
        result = {
            "in_process": [],
            "pending": [],
            "processed": []
        }

        try:
            if not user_id:
                print("[fetch_routing_documents] user_id manquant")
                return result

            # Collection des notifications/jobs
            collection_path = f"clients/{user_id}/notifications"
            collection_ref = self.db.collection(collection_path)

            # Récupérer tous les documents pour ce mandat (filtrer par function_name='Router' si applicable)
            # Note: On filtre par company_id/mandate car un user peut avoir plusieurs mandats
            try:
                query = collection_ref.where(filter=FieldFilter("function_name", "==", "Router"))
                docs = query.stream()
            except Exception:
                # Fallback si le champ function_name n'existe pas
                docs = collection_ref.stream()

            # Status groups selon les types définis
            in_process_statuses = ['in_queue', 'on_process', 'stopping']
            pending_statuses = ['pending']
            processed_statuses = ['completed']

            for doc in docs:
                data = doc.to_dict() or {}
                data["id"] = doc.id

                # Extraire le status du document
                status = data.get("status", "").lower()

                # Construire l'objet document avec les champs nécessaires
                doc_item = {
                    "id": doc.id,
                    "job_id": data.get("job_id", doc.id),
                    "file_name": data.get("file_name", data.get("name", "")),
                    "status": status,
                    "timestamp": data.get("timestamp", data.get("created_at", "")),
                    "source": data.get("source", ""),
                    "drive_file_id": data.get("file_id", data.get("drive_file_id", "")),
                    "uri_drive_link": data.get("uri_file_link", data.get("drive_link", "")),
                    "pinnokio_func": data.get("function_name", data.get("pinnokio_func", "Router")),
                }

                # Trier dans la bonne catégorie selon le status
                if status in in_process_statuses:
                    result["in_process"].append(doc_item)
                elif status in pending_statuses:
                    result["pending"].append(doc_item)
                elif status in processed_statuses:
                    result["processed"].append(doc_item)
                # Les autres statuts (to_process, error, stopped) sont gérés côté Drive

            print(f"[fetch_routing_documents] Retrieved: in_process={len(result['in_process'])}, "
                  f"pending={len(result['pending'])}, processed={len(result['processed'])}")

            return result

        except Exception as e:
            print(f"❌ Erreur fetch_routing_documents: {e}")
            return result



    def update_job_status(self, user_id: str, job_id: str, new_status: str, additional_info: dict = None):
        """
        Met à jour le statut d'un job spécifique.
        
        Args:
            user_id (str): L'ID de l'utilisateur
            job_id (str): L'ID du job à mettre à jour
            new_status (str): Le nouveau statut du job (ex: "running", "completed", "error", etc.)
            additional_info (dict, optional): Informations supplémentaires à ajouter au document
                                            (ex: {'error_message': 'Message d'erreur'})
        
        Returns:
            bool: True si la mise à jour a réussi, False sinon
        """
        try:
            # Construire le chemin de la collection
            collection_path = f"clients/{user_id}/notifications"
            
            # Obtenir la référence à la collection
            collection_ref = self.db.collection(collection_path)
            
            # Parcourir tous les documents pour trouver celui avec le bon job_id
            docs = collection_ref.stream()
            
            # Variable pour suivre si le document a été trouvé et mis à jour
            updated = False
            
            for doc in docs:
                doc_data = doc.to_dict()
                # Vérifier si ce document correspond au job_id recherché
                if doc_data.get('job_id') == job_id:
                    # Obtenir la référence au document spécifique
                    doc_ref = collection_ref.document(doc.id)
                    
                    # Préparer les données à mettre à jour
                    update_data = {
                        'status': new_status,
                        'timestamp': datetime.now(timezone.utc).isoformat()  # Mettre à jour aussi le timestamp
                    }
                    
                    # Ajouter les informations supplémentaires si fournies
                    if additional_info is not None:
                        update_data['additional_info'] = additional_info
                    
                    # Mettre à jour le document
                    doc_ref.update(update_data)
                    
                    print(f"Statut du job {job_id} mis à jour: {new_status}")
                    if additional_info:
                        print(f"Informations supplémentaires ajoutées: {additional_info}")
                        
                    updated = True
                    break
            
            if not updated:
                print(f"Le job {job_id} n'existe pas, impossible de mettre à jour son statut")
                return False
                
            return updated
                
        except Exception as e:
            print(f"Erreur lors de la mise à jour du statut du job {job_id}: {e}")
            return False


    def get_user_balance(self, mandate_path:str=None,user_id:str=None) -> float:
        """
        Calculates the user's current balance by processing pending top-ups and unbilled expenses.
        
        Args:
            mandate_path (str): Path to the mandate in Firestore
            
        Returns:
            float: The current account balance
        """
        try:
            # Get the user ID from the mandate path
            # mandate_path format: clients/{user_id}/bo_clients/{parent_id}/mandates/{mandate_id}
            if mandate_path:
                path_parts = mandate_path.split('/')
                user_id = path_parts[1] if len(path_parts) > 1 else None
            
            if not user_id:
                print("Could not extract user_id from mandate_path")
                return 0.0

            # ───────────────────────────────────────────────────────
            # ✅ Lock Redis (anti double facturation concurrente)
            # Sans lock, deux process peuvent voir `billed=false` et incrémenter 2x current_expenses.
            # ───────────────────────────────────────────────────────
            lock_key = f"lock:billing:balance:{user_id}"
            lock_token = uuid.uuid4().hex
            lock_acquired = False
            try:
                from .redis_client import get_redis
                r = get_redis()
                lock_acquired = bool(r.set(lock_key, lock_token, nx=True, ex=120))
            except Exception:
                # En cas de panne Redis, on laisse passer (mode dégradé)
                lock_acquired = True

            if not lock_acquired:
                # Quelqu'un d'autre est en train de recalculer → renvoyer le solde actuel
                balance_doc_ref = self.db.document(f"clients/{user_id}/billing/current_balance")
                balance_doc = balance_doc_ref.get()
                if balance_doc.exists:
                    balance_data = balance_doc.to_dict() or {}
                    return float(balance_data.get("current_balance", 0.0))
                return 0.0
                
            # Step 1: Get the current balance document
            balance_doc_ref = self.db.document(f"clients/{user_id}/billing/current_balance")
            balance_doc = balance_doc_ref.get()
            
            # Initialize balance values
            current_balance = 0.0
            current_topping = 0.0
            current_expenses = 0.0
            
            if balance_doc.exists:
                balance_data = balance_doc.to_dict()
                current_balance = float(balance_data.get('current_balance', 0.0))
                current_topping = float(balance_data.get('current_topping', 0.0))
                current_expenses = float(balance_data.get('current_expenses', 0.0))


            if mandate_path:
                # Step 2: Process pending top-ups
                transactions_path = f"{mandate_path}/billing/topping/transactions"
                # Utilisez la nouvelle syntaxe de filtre pour éviter l'avertissement
                transactions_query = self.db.collection(transactions_path)
                pending_transactions = transactions_query.where(filter=FieldFilter("status", "==", "pending")).get()
                
                new_topping_amount = 0.0
                processed_transactions = []
                
                for transaction in pending_transactions:
                    transaction_data = transaction.to_dict()
                    new_topping_amount += float(transaction_data.get('amount', 0.0))
                    processed_transactions.append(transaction.reference)
                
                # Mark processed transactions as 'added'
                batch = self.db.batch()
                for transaction_ref in processed_transactions:
                    batch.update(transaction_ref, {'status': 'added'})
                
                if processed_transactions:
                    batch.commit()
                
                # Update the topping value if there are new top-ups
                if new_topping_amount > 0:
                    current_topping += new_topping_amount
                    balance_doc_ref.set({
                        'current_topping': current_topping,
                        'timestamp_topping': firestore.SERVER_TIMESTAMP
                    }, merge=True)
                
                # Step 3: Process unbilled expenses
                expenses_data = self.get_all_expenses(mandate_path)

                new_expenses_amount = 0.0
                updated_jobs = []

                for job_id, job_data in expenses_data.items():
                    if job_data.get('billed') is False:
                        # Add the sales price to expenses
                        new_expenses_amount += float(job_data.get('total_sales_price', 0.0))
                        
                        # Mark this job as billed and prepare to update
                        job_data['billed'] = True
                        updated_jobs.append((job_id, job_data))

                # Update each job document to mark as billed
                if updated_jobs:
                    batch = self.db.batch()
                    for job_id, job_data in updated_jobs:
                        job_ref = self.db.document(f"{mandate_path}/billing/topping/expenses/{job_id}")
                        batch.update(job_ref, {'billed': True})
                    batch.commit()
                            
                # Update expenses if there are new unbilled expenses
                if new_expenses_amount > 0:
                    current_expenses += new_expenses_amount
                    balance_doc_ref.set({
                        'current_expenses': current_expenses,
                        'timestamp_expenses': firestore.SERVER_TIMESTAMP
                    }, merge=True)
                
                # Step 4: Calculate and update final balance
                updated_balance = current_topping - current_expenses
                
                # Only update if there are new transactions or expenses
                if new_topping_amount > 0 or new_expenses_amount > 0:
                    balance_doc_ref.set({
                        'current_balance': updated_balance,
                        'last_updated': firestore.SERVER_TIMESTAMP
                    }, merge=True)
                
                return updated_balance if (new_topping_amount > 0 or new_expenses_amount > 0) else current_balance
                
        except Exception as e:
            print(f"Error calculating user balance: {str(e)}")
            import traceback
            traceback.print_exc()
            return 0.0
        finally:
            # Libérer le lock si on l'a pris (best-effort)
            try:
                from .redis_client import get_redis
                r = get_redis()
                current = r.get(lock_key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current == lock_token:
                    r.delete(lock_key)
            except Exception:
                pass


    def get_balance_info(self, mandate_path: str=None,user_id:str=None) -> dict:
        """
        Récupère les informations de solde à partir du document de solde courant.
        
        Args:
            mandate_path (str): Chemin du mandat dans Firestore
            
        Returns:
            dict: Dictionnaire contenant les valeurs current_balance, current_expenses, et current_topping
                ou valeurs par défaut si non trouvées
        """
        try:
            mandate_path = self._normalize_mandate_path(mandate_path)
            # Extraire l'ID utilisateur du chemin du mandat
            if mandate_path:
                path_parts = mandate_path.split('/')
                user_id = path_parts[1] if len(path_parts) > 1 else None
            
                
            
            if not user_id:
                print("Could not extract user_id from mandate_path")
                return {
                    'current_balance': 0.0,
                    'current_expenses': 0.0, 
                    'current_topping': 0.0
                }
            
            # Récupérer le document de solde
            balance_doc_ref = self.db.document(f"clients/{user_id}/billing/current_balance")
            balance_doc = balance_doc_ref.get()
            
            if not balance_doc.exists:
                print(f"No balance document found for user {user_id}")
                return {
                    'current_balance': 0.0,
                    'current_expenses': 0.0, 
                    'current_topping': 0.0
                }
            
            # Extraire les données
            balance_data = balance_doc.to_dict()
            
            # Récupérer les valeurs avec des valeurs par défaut de 0.0
            current_balance = float(balance_data.get('current_balance', 0.0))
            current_expenses = float(balance_data.get('current_expenses', 0.0))
            current_topping = float(balance_data.get('current_topping', 0.0))
            
            return {
                'current_balance': current_balance,
                'current_expenses': current_expenses,
                'current_topping': current_topping
            }
            
        except Exception as e:
            print(f"Error retrieving balance info: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'current_balance': 0.0,
                'current_expenses': 0.0, 
                'current_topping': 0.0
            }


    def ensure_chat_daily_expenses(
        self,
        mandate_path: str,
        collection_name: str,
        user_id: Optional[str] = None,
        days_back: int = 7,
        include_today: bool = False,
    ) -> dict:
        """
        Crée (si manquants) les docs expenses journaliers "chat" à partir des agrégats token_usage.

        But: permettre un rattrapage (serveur arrêté / CRON raté) sans attendre le prochain CRON.
        Cette méthode est idempotente: elle fait des upserts sur les expenses.
        """
        try:
            mandate_path = self._normalize_mandate_path(mandate_path)
            if mandate_path:
                parts = mandate_path.split("/")
                user_id = user_id or (parts[1] if len(parts) > 1 else None)

            if not user_id or not mandate_path or not collection_name:
                return {"success": False, "error": "missing user_id/mandate_path/collection_name"}

            from datetime import datetime, timedelta, timezone

            today = datetime.now(timezone.utc).date()
            dates = []
            start = 0 if include_today else 1
            for i in range(start, max(1, days_back) + 1):
                d = today - timedelta(days=i)
                dates.append(d.strftime("%Y-%m-%d"))

            created_or_updated = 0
            missing_token_usage = 0

            for billing_date in dates:
                job_id = f"chat:{user_id}:{collection_name}:{billing_date}"
                token_usage_ref = self.db.document(f"clients/{user_id}/token_usage/{job_id}")
                token_doc = token_usage_ref.get()
                if not token_doc.exists:
                    missing_token_usage += 1
                    continue

                tu = token_doc.to_dict() or {}
                # Format "Chat usage DD/MM/YYYY"
                ddmmyyyy = billing_date
                try:
                    from datetime import datetime
                    ddmmyyyy = datetime.strptime(billing_date, "%Y-%m-%d").strftime("%d/%m/%Y")
                except Exception:
                    ddmmyyyy = billing_date

                expense_ref = self.db.document(f"{mandate_path}/billing/topping/expenses/{job_id}")
                expense_payload = {
                    "job_id": job_id,
                    "function": "chat",
                    "file_name": f"Chat usage {ddmmyyyy}",
                    "billing_kind": "chat_daily",
                    "billing_date": billing_date,
                    "collection_name": collection_name,
                    "user_id": user_id,
                    "total_input_tokens": tu.get("total_input_tokens", 0),
                    "total_output_tokens": tu.get("total_output_tokens", 0),
                    "total_tokens": tu.get("total_tokens", (tu.get("total_input_tokens", 0) or 0) + (tu.get("total_output_tokens", 0) or 0)),
                    "total_buy_price": tu.get("total_buy_price", 0.0),
                    "total_sales_price": tu.get("total_sales_price", 0.0),
                    "entries_count": tu.get("entries_count", 0),
                    # IMPORTANT: laisser billed=false → get_user_balance() fera la consommation une seule fois
                    "billed": False,
                    "billing_timestamp": datetime.now(timezone.utc).isoformat(),
                }
                expense_ref.set(expense_payload, merge=True)
                created_or_updated += 1

            return {
                "success": True,
                "days_back": days_back,
                "include_today": include_today,
                "expenses_upserted": created_or_updated,
                "missing_token_usage": missing_token_usage,
            }
        except Exception as e:
            logger.error("[BILLING] ensure_chat_daily_expenses error=%s", repr(e), exc_info=True)
            return {"success": False, "error": str(e)}


    
    def save_context(self, context_path: str, context_data: dict):
        """
        Sauvegarde les données de contexte dans Firestore.
        
        Args:
            context_path (str): Chemin complet du document dans Firestore
            context_data (dict): Données à sauvegarder au format dictionnaire
        
        Returns:
            bool: True si la sauvegarde est réussie, False sinon
        """
        try:
            # Obtenir la référence du document
            doc_ref = self.db.document(context_path)
            
            # Sauvegarder les données avec merge pour ne pas écraser d'autres champs
            doc_ref.set(context_data, merge=True)
            return True
            
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du contexte: {str(e)}")
            return False

    def upload_settings_params(self, erp: str, countries: list, doc_type: str, data: dict) -> dict:
        """
        Upload ou met à jour les paramètres de configuration dans Firestore.
        
        Args:
            erp (str): Nom de l'ERP (ex: 'sage50', 'sap', etc.)
            countries (list): Liste des pays concernés
            doc_type (str): Type de document (ex: 'account_type_definition')
            data (dict): Données à uploader (clé-valeur des paramètres)
        
        Returns:
            dict: Résultat de l'opération avec statut et message
        """
        try:
            # Création du chemin de base
            base_path = f'settings_param/coa_mapping_settings/erp/{erp}'
            
            # Pour chaque pays dans la liste
            for country in countries:
                # Définition du chemin complet
                doc_ref = self.db.document(f'{base_path}/{country}/{doc_type}')
                
                # Vérification si le document existe déjà
                doc = doc_ref.get()
                
                if doc.exists:
                    # Mise à jour du document existant
                    doc_ref.update(data)
                else:
                    # Création d'un nouveau document
                    doc_ref.set(data)

            return {
                'status': 'success',
                'message': f'Parameters successfully uploaded for {erp} in {", ".join(countries)}'
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Error uploading parameters: {str(e)}'
            }

    def download_account_type_spec(self, erp: str, country: str, account_type: str) -> tuple[bool, str]:
        """
        Télécharge la définition spécifique d'un type de compte depuis Firestore.
        
        Args:
            erp (str): Nom de l'ERP (ex: 'sage50', 'sap', etc.)
            country (str): Pays concerné
            account_type (str): Type de compte (ex: 'expense_direct_cost')
        
        Returns:
            tuple[bool, str]: (True/False, définition ou message d'erreur)
                            True si la définition est trouvée, False sinon
        """
        try:
            # Construction du chemin pour account_type_definition
            doc_path = f'settings_param/coa_mapping_settings/erp/{erp}/{country}/account_type_definition'
            doc_ref = self.db.document(doc_path)
            
            # Récupération du document
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                # Vérification si le type de compte spécifique existe
                if account_type in data:
                    return True, data[account_type]
                else:
                    return False, f"Account type '{account_type}' not found for {country}"
            else:
                return False, f"No account type definitions found for {erp}/{country}"
                
        except Exception as e:
            return False, f"Error retrieving account type definition: {str(e)}"

    def check_if_usermail_already_exists(self,email_adresse):
        
        print(f"🔍 Controle de l'existance du mail du client : {email_adresse}")

        # Vérification si un e-mail identique existe déjà dans la collection clients
        existing_docs = self.db.collection("clients").where(filter=FieldFilter("client_email", "==", email_adresse)).get()
        if existing_docs:
            print(f"⚠️ L'adresse e-mail {email_adresse} existe déjà dans la collection clients.")
            return True  # Arrête l'exécution si un utilisateur avec le même e-mail existe
        else:
            return False    

    def check_and_create_client_document(self, user_data):
        """
        Vérifie si les documents utilisateur existent et les crée si nécessaire.

        Crée INDÉPENDAMMENT:
        1. users/{uid}
        2. clients/{uid}
        3. clients/{uid}/bo_clients/{uid}

        Args:
            user_data (dict): Données utilisateur contenant `email` et `displayName`.
        """
        user_id = user_data['uid']
        user_document_data = {
            "uid": user_data["uid"],
            "email": user_data["email"],
            "displayName": user_data.get("displayName", ""),
            "photoURL": user_data.get("photoUrl", "")
        }

        # ═══════════════════════════════════════════════════════════════
        # 1. Check/Create users/{uid}
        # ═══════════════════════════════════════════════════════════════
        user_ref = self.db.collection("users").document(user_id)
        print(f"🔍 Vérification users/{user_id}")

        user_doc = user_ref.get()
        if user_doc.exists:
            print(f"✅ users/{user_id} existe déjà - mise à jour")
            user_ref.update(user_data)
        else:
            print(f"⚠️ users/{user_id} n'existe pas - création")
            user_ref.set(user_document_data)
            print(f"✅ users/{user_id} créé")

        # ═══════════════════════════════════════════════════════════════
        # 2. Check/Create clients/{uid} (INDÉPENDAMMENT de users/)
        # ═══════════════════════════════════════════════════════════════
        client_ref = self.db.collection('clients').document(user_id)
        print(f"🔍 Vérification clients/{user_id}")

        client_doc = client_ref.get()
        if client_doc.exists:
            print(f"✅ clients/{user_id} existe déjà")
        else:
            print(f"⚠️ clients/{user_id} n'existe pas - création")
            client_ref.set({
                "client_email": user_data["email"],
                "client_name": user_data.get("displayName", ""),
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            print(f"✅ clients/{user_id} créé")

        # ═══════════════════════════════════════════════════════════════
        # 3. Check/Create clients/{uid}/bo_clients/{uid} (INDÉPENDAMMENT)
        # ═══════════════════════════════════════════════════════════════
        bo_client_ref = client_ref.collection("bo_clients").document(user_id)
        print(f"🔍 Vérification clients/{user_id}/bo_clients/{user_id}")

        bo_client_doc = bo_client_ref.get()
        if bo_client_doc.exists:
            print(f"✅ clients/{user_id}/bo_clients/{user_id} existe déjà")
        else:
            print(f"⚠️ clients/{user_id}/bo_clients/{user_id} n'existe pas - création")
            client_uuid = f"klk_client_{str(uuid.uuid4())[:8]}"
            bo_client_ref.set({
                "client_name": user_data.get("displayName", ""),
                "created_at": firestore.SERVER_TIMESTAMP,
                "client_uuid": client_uuid
            })
            print(f"✅ clients/{user_id}/bo_clients/{user_id} créé avec client_uuid={client_uuid}")
           

    def get_erp_path(self, mandate_path: str, erp_name: str) -> dict:
        """
        Récupère le document ERP correspondant au type spécifié dans le chemin du mandat.
        
        Args:
            mandate_path (str): Chemin du mandat
            erp_name (str): Nom de l'ERP à rechercher
            
        Returns:
            dict: Document correspondant à l'ERP trouvé ou None si non trouvé
        """
        # Nettoyer le path en enlevant le '/' initial si présent
        clean_path = mandate_path.lstrip('/')
        
        # Construire le chemin complet avec /erp
        full_path = f"{clean_path}/erp"
        
        try:
            # Créer la référence à la collection
            erp_collection = self.db.collection(full_path)
            
            # Streamer la collection et chercher le document correspondant
            docs = erp_collection.stream()
            
            for doc in docs:
                doc_data = doc.to_dict()
                # Vérifier si le document a le bon erp_type
                if doc_data.get('erp_type') == erp_name:
                    # Ajouter l'ID du document aux données
                    doc_data['id'] = doc.id
                    return doc_data
                    
            # Si aucun document n'est trouvé
            return None
            
        except Exception as e:
            print(f"Erreur lors de la récupération de l'ERP: {str(e)}")
            return None     

    def search_files_in_firebase(self,user_id, log_entry, file_names,funcs_list):
        # S'assurer que file_names est une liste, même si une seule chaîne est fournie
        if isinstance(file_names, str):
            file_names = [file_names]

        pinnokio_funcs = funcs_list
        mandat_id = log_entry.get('mandat_id')

        # S'assurer que pinnokio_func est une liste, même si une seule chaîne est fournie
        if isinstance(pinnokio_funcs, str):
            pinnokio_funcs = [pinnokio_funcs]

        # Vérifier si au moins un pinnokio_func est fourni
        if not pinnokio_funcs:
            return {'success': False, 'message': "Aucun 'pinnokio_func' fourni pour la recherche."}

        found_files = []

        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        # Itérer sur chaque 'pinnokio_func' (= department) pour rechercher les fichiers correspondants
        for pinnokio_func in pinnokio_funcs:
            # Rechercher les fichiers dans task_manager pour chaque file_name
            for file_name in file_names:
                query = task_manager_ref.where(filter=FieldFilter('department', '==', pinnokio_func)) \
                                        .where(filter=FieldFilter('file_name', '==', file_name)) \
                                        .where(filter=FieldFilter('mandat_id', '==', mandat_id)).stream()

                for doc in query:
                    found_files.append(doc.to_dict())

        # Générer la réponse
        if found_files:
            return {
                'success': True,
                'message': f"{len(found_files)} fichier(s) trouvé(s) pour les critères donnés.",
                'data': found_files
            }
        else:
            return {'success': False, 'message': "Aucun fichier trouvé correspondant aux critères donnés."}

    def get_client_data_by_name(self,user_id, client_name):
        """Récupère les données client par nom."""
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        
        clients_ref = self.db.collection(base_path)
        query = clients_ref.where(filter=FieldFilter('client_name', '==', client_name))
        results = query.stream()

        for doc in results:
            # On suppose que 'client_name' est unique et on prend donc le premier résultat.
            client_data = doc.to_dict()
            client_uuid = client_data.get('client_uuid')
            drive_client_parent_id = client_data.get('drive_client_parent_id')
            document_path = doc.reference.path
            return client_uuid, drive_client_parent_id,document_path

        return None, None,None  # Renvoie None si aucun client correspondant n'est trouvé
    def check_business_name_existence(self,user_id, client_uuid, business_name):
        """
        Vérifie si un business_name spécifié existe déjà dans la sous-collection 'mandates'
        de la collection 'bo_clients' pour un client_uuid donné.

        Args:
            client_uuid (str): L'UUID du client à vérifier.
            business_name (str): Le nom de l'entreprise à rechercher.

        Returns:
            bool: True si le business_name existe déjà sous le client spécifié, False sinon.
        """
        # Recherche du client par client_uuid dans la collection 'bo_clients'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        client_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()

        # Si un client correspondant est trouvé
        if client_query:
            # Récupération de l'ID du document du client trouvé
            client_doc_id = client_query[0].id

            # Construction du chemin vers la sous-collection 'mandates' du client trouvé
            if user_id:
                base_path = f'clients/{user_id}/bo_clients/{client_doc_id}/mandates'
            else:
                base_path = f'bo_clients/{client_doc_id}/mandates'
            #mandates_collection_path = f'bo_clients/{client_doc_id}/mandates'

            # Requête dans la sous-collection 'mandates' pour trouver un document avec le business_name donné
            mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_name', '==', business_name)).limit(1).get()

            # Vérification si un document correspondant existe
            return len(mandates_query) > 0

        # Si aucun client correspondant n'est trouvé, ou si le business_name n'existe pas sous ce client
        return False

    def user_app_permission_token(self,user_id):
        """Récupère le jeton d'autorisation de l'utilisateur pour accéder à l'API Google Drive."""
        if user_id:
            base_path = f'clients/{user_id}/cred_tokens'
        else:
            base_path = 'klk_vision'
        
        doc_ref = self.db.collection(base_path).document('google_authcred_token')
        doc = doc_ref.get()
        if doc.exists:
            data=doc.to_dict()
            token_data = {
                        "token": data.get('token'),
                        "refresh_token": data.get('refresh_token'),
                        "token_uri": data.get('token_uri'),
                        "client_id": data.get('client_id'),
                        "client_secret": data.get('client_secret'),
                        "expiry": data.get('expiry'),
                        
                    }
            return token_data
        else:
            return None


    def check_if_users_exist(self,mail_to_invite):
        user_query=self.db.collection('users').where(filter=FieldFilter('email','==',mail_to_invite)).limit(1).get()
        for user_doc in user_query:
            existing_user = {
                "uid": user_doc.id,
                "data": user_doc.to_dict()
            }

    def create_or_get_chat_threadkey(self,user_id, department_index):
        departments = {
            "banker": "banker",
            "router": "router",
            "APbookeeper": "APbookeeper",
            "HRmanager": "HRmanager",
            "Admanager": "Admanager",
            "EXbookeeper": "EXbookeeper",
            "Bankbookeeper": "Bankbookeeper",
            "Router": "Router",
        }

        department = departments.get(department_index)

        if not department:
            raise ValueError("Invalid department index provided")

        # Lecture depuis chat_config/{department}
        if user_id:
            doc_ref = self.db.collection('clients').document(str(user_id)).collection('chat_config').document(department)
        else:
            doc_ref = self.db.collection('chat_config').document(department)

        doc = doc_ref.get()

        if doc.exists:
            doc_data = doc.to_dict()
            if 'chat_threadkey' in doc_data:
                return doc_data['chat_threadkey']

        # Génération du chat_threadkey avec UUID
        chat_threadkey = f"klk_{uuid.uuid4().hex}_{department}"

        # Création/mise à jour du document chat_config
        doc_ref.set({'chat_threadkey': chat_threadkey, 'department': department}, merge=True)

        return chat_threadkey

    def get_close_job_id(self,user_id,departement, space_id):
        """
        Recherche les job_id fermés correspondant aux critères spécifiés.

        Args:
            departement (int): Index dans departement_index.
            space_id (str): Le mandat_id à filtrer.
        Returns:
            list: Une liste des job_id correspondant aux critères.
        """
        departement_index=['Admanager','EXbookeeper','Router','Bankbookeeper','APbookeeper','HRmanager']

        filtered_documents = []
        chosen_departement = departement_index[departement]

        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        # Requête directe sur task_manager avec filtres department + status + mandat_id
        query = task_manager_ref.where(filter=FieldFilter('department', '==', chosen_departement)) \
                                .where(filter=FieldFilter('status', '==', 'close')) \
                                .where(filter=FieldFilter('mandat_id', '==', space_id))

        # Filtre source pour APbookeeper (comme dans l'ancienne version)
        if chosen_departement == 'APbookeeper':
            query = query.where(filter=FieldFilter('source', '==', 'documents/invoices/doc_booked'))

        for doc in query.stream():
            doc_data = doc.to_dict()
            doc_data['fb_doc_id'] = doc.id
            filtered_documents.append(doc_data)

        return filtered_documents
    
    def watch_transaction_status_changes(self,user_id, batch_id, initial_transaction_statuses, callback_function):
        """
        Surveille les modifications des statuts des transactions dans task_manager.
        
        Args:
            batch_id (str): L'ID du batch
            initial_transaction_statuses (dict): Statuts initiaux des transactions
            callback_function: Fonction de callback pour notifier les changements
        """
        print(f"Debut de la fonction watch_transaction_status_changes pour batch {batch_id}")
        
        if user_id:
            base_path = f'clients/{user_id}/task_manager/{batch_id}'
        else:
            base_path = f"task_manager/{batch_id}"
        
        document_ref = self.db.document(base_path)
        
        # Dictionnaire pour suivre les statuts déjà acquittés
        acknowledged_statuses = {}
        
        print(f"[DEBUG] Démarrage de l'écoute sur le chemin: {base_path}")

        def on_snapshot(doc_snapshot, changes, read_time):
            print("[DEBUG] Callback on_snapshot déclenché pour les statuts de transactions")
            try:
                for doc in doc_snapshot:
                    current_data = doc.to_dict()
                    
                    # Vérifier la structure: jobs_data -> transactions
                    if 'jobs_data' not in current_data:
                        print("[DEBUG] Pas de données 'jobs_data' trouvées")
                        return
                    
                    jobs_data = current_data['jobs_data']
                    if not jobs_data or len(jobs_data) == 0:
                        print("[DEBUG] Aucun job trouvé dans jobs_data")
                        return
                    
                    # Prendre le premier job (généralement il n'y en a qu'un)
                    job_data = jobs_data[0]
                    current_transactions = job_data.get('transactions', [])
                    
                    changes_detected = False
                    updated_statuses = {}
                    changes_to_acknowledge = {}
                    
                    # Parcourir les transactions actuelles
                    for tx in current_transactions:
                        tx_id = str(tx.get('transaction_id', ''))
                        current_status = tx.get('status', '')
                        
                        if tx_id in initial_transaction_statuses:
                            old_status = initial_transaction_statuses[tx_id]
                            
                            # Vérifier si le statut a changé et n'a pas déjà été acquitté
                            if (current_status != old_status and 
                                acknowledged_statuses.get(tx_id) != current_status):
                                
                                if not changes_detected:
                                    print(f"\nNouvelles modifications de statuts détectées dans le batch {batch_id}:")
                                    changes_detected = True
                                
                                print(f"  - Transaction {tx_id}:")
                                print(f"    Ancien statut: {old_status}")
                                print(f"    Nouveau statut: {current_status}")
                                
                                # Mettre à jour le statut de référence
                                initial_transaction_statuses[tx_id] = current_status
                                # Enregistrer pour acquittement
                                changes_to_acknowledge[tx_id] = current_status
                        
                        # Construire le dictionnaire des statuts mis à jour
                        updated_statuses[tx_id] = current_status
                    
                    # Si des changements ont été détectés, appeler le callback
                    if changes_to_acknowledge:
                        acknowledged_statuses.update(changes_to_acknowledge)
                        print("[DEBUG] Changements de statuts acquittés:", changes_to_acknowledge)
                        
                        # Appeler le callback avec tous les statuts mis à jour
                        try:
                            import asyncio
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            loop.run_until_complete(callback_function(updated_statuses))
                            loop.close()
                        except Exception as callback_error:
                            print(f"[ERREUR] Échec du callback: {callback_error}")
                    
            except Exception as e:
                print(f"[ERREUR] Échec du listener Firestore pour les statuts: {e}")
                import traceback
                traceback.print_exc()

        document_watch = document_ref.on_snapshot(on_snapshot)
        print("[DEBUG] Listener pour statuts de transactions attaché avec succès au document")
        
        return document_watch

    # ========== Transaction Status Listeners (Nouveau système) ==========

    def start_transaction_listener(self, user_id: str, batch_id: str, initial_statuses: dict, callback=None) -> bool:
        """Démarre un listener de transaction status via le système unifié de listeners.
        
        Cette méthode remplace watch_transaction_status_changes pour résoudre l'erreur
        'Object of type function is not JSON serializable' en déléguant vers ListenersManager.
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            batch_id (str): ID du batch de transactions (ex: bank_batch_7196d7f1d5)
            initial_statuses (dict): Statuts initiaux des transactions {"transaction_id": "status", ...}
            callback: Ignoré côté microservice (utilisé seulement côté Reflex pour BusConsumer)
            
        Returns:
            bool: True si le listener a été démarré avec succès, False sinon
        """
        try:
            # Log informatif si un callback est passé (pour debugging)
            if callback is not None:
                print(f"ℹ️ Callback ignoré côté microservice pour user_id={user_id}, batch_id={batch_id} (utilisation Redis pub/sub)")
            
            # Déléguer vers ListenersManager qui gère tous les listeners de manière centralisée
            from .main import listeners_manager
            if listeners_manager:
                return listeners_manager.start_transaction_status_listener(user_id, batch_id, initial_statuses, callback)
            else:
                print("❌ ListenersManager non disponible")
                return False
                
        except Exception as e:
            print(f"❌ Erreur lors du démarrage du transaction listener: {e}")
            return False

    def stop_transaction_listener(self, user_id: str, batch_id: str) -> bool:
        """Arrête un listener de transaction status.
        
        Args:
            user_id (str): ID de l'utilisateur Firebase
            batch_id (str): ID du batch de transactions
            
        Returns:
            bool: True si le listener a été arrêté avec succès, False sinon
        """
        try:
            # Déléguer vers ListenersManager
            from .main import listeners_manager
            if listeners_manager:
                return listeners_manager.stop_transaction_status_listener(user_id, batch_id)
            else:
                print("❌ ListenersManager non disponible")
                return False
                
        except Exception as e:
            print(f"❌ Erreur lors de l'arrêt du transaction listener: {e}")
            return False
    
    def get_open_job_id(self,user_id,departement, space_id):
        """
        Recherche les job_id ouverts correspondant aux critères spécifiés.

        Args:
            departement (int): Index dans departement_index.
            space_id (str): Le mandat_id à filtrer.
        Returns:
            list: Une liste des job_id correspondant aux critères.
        """
        departement_index=['Admanager','EXbookeeper','Router','Bankbookeeper','APbookeeper','HRmanager']

        filtered_documents = []
        chosen_departement = departement_index[departement]

        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        # Requête directe sur task_manager avec filtres department + status + source + mandat_id
        query = task_manager_ref.where(filter=FieldFilter('department', '==', chosen_departement)) \
                                .where(filter=FieldFilter('status', '==', 'to_process')) \
                                .where(filter=FieldFilter('source', '==', 'documents/accounting/invoices/doc_to_do')) \
                                .where(filter=FieldFilter('mandat_id', '==', space_id))

        for doc in query.stream():
            doc_data = doc.to_dict()
            doc_data['fb_doc_id'] = doc.id
            filtered_documents.append(doc_data)

        return filtered_documents
    
    def add_document_without_timestamp(self, collection_path, doc_id, data, merge=None):
        """
        Ajoute ou met à jour un document spécifique dans Firestore sans ajouter de timestamp.

        Args:
            collection_path (str): Le chemin de la collection Firestore.
            doc_id (str): L'ID du document à ajouter ou mettre à jour.
            data (dict): Les données du document à ajouter.
            merge (bool, optional): Indique si les données doivent être fusionnées avec les données existantes. Par défaut, False.

        Returns:
            str: L'ID du document mis à jour ou créé.
        """
        # Vérification que data est bien un dictionnaire
        if not isinstance(data, dict):
            raise TypeError("Les données du document (data) doivent être un dictionnaire.")

        # Ajouter ou mettre à jour le document dans Firestore
        doc_ref = self.db.collection(collection_path).document(doc_id)
        doc_ref.set(data, merge=merge if merge is not None else False)

        # Retourner l'ID du document mis à jour ou créé
        return doc_ref.id

    def add_new_document(self, collection_path, data):
        """
        Ajoute un document à une collection Firestore avec un ID généré automatiquement.

        Args:
            collection_path (str): Le chemin de la collection Firestore.
            data (dict): Les données du document à ajouter.

        Returns:
            str: L'ID du document créé.
        """
        try:
            # Ajouter un timestamp aux données
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
            
            
            # Ajouter le document avec un ID généré automatiquement
            doc_ref = self.db.collection(collection_path).add(data)
            
            # Retourner l'ID du document généré automatiquement
            return doc_ref[1].id
        except AttributeError as e:
            print("Vérifiez que les données passées à 'add' ne contiennent pas de types non valides (e.g., set).")
            print(f"Erreur : {e}")
        except Exception as e:
            print(f"Erreur pendant add_new_document sur la méthode de firebase: {e}")

    
    def add_or_update_job_by_file_id(self, collection_path, job_data):
        """
        Ajoute un nouveau job ou met à jour un job existant en utilisant file_id comme identifiant du document
        
        Args:
            collection_path (str): Chemin de la collection
            job_data (dict): Données du job avec au moins 'file_id'
            
        Returns:
            str: ID du document (qui est égal au file_id)
        """
        try:
            if 'file_id' not in job_data:
                raise ValueError("job_data doit contenir un 'file_id'")
                
            file_id = job_data['file_id']
            
            # Mettre à jour le timestamp
            job_data['timestamp'] = datetime.now(timezone.utc).isoformat()
            
            # Utiliser directement le file_id comme ID du document
            doc_ref = self.db.collection(collection_path).document(file_id)
            
            # Vérifier si le document existe déjà
            doc = doc_ref.get()
            
            if doc.exists:
                # Mettre à jour le document existant
                doc_ref.set(job_data, merge=True)
            else:
                # Créer un nouveau document avec le file_id comme ID
                doc_ref.set(job_data)
            
            # Publier notification si c'est dans le chemin notifications
            if "notifications" in collection_path:
                self._publish_notification_event(collection_path, job_data)
                
            return file_id
                
        except Exception as e:
            print(f"Erreur lors de l'ajout/mise à jour du job: {e}")
            return None

    
    def add_or_update_job_by_job_id(self, collection_path, job_data):
        """
        Ajoute un nouveau job ou met à jour un job existant en utilisant job_id comme identifiant du document

        Args:
            collection_path (str): Chemin de la collection
            job_data (dict): Données du job avec au moins 'job_id'

        Returns:
            str: ID du document (qui est égal au job_id)
        """
        print(f"[DEBUG] add_or_update_job_by_job_id called - path: {collection_path}")
        print(f"[DEBUG] job_data keys: {list(job_data.keys()) if isinstance(job_data, dict) else 'NOT_DICT'}")

        try:
            if 'job_id' not in job_data:
                print(f"[ERROR] Missing job_id in job_data. Keys available: {list(job_data.keys()) if isinstance(job_data, dict) else 'NOT_DICT'}")
                raise ValueError("job_data doit contenir un 'job_id'")

            job_id = job_data['job_id']
            print(f"[DEBUG] Processing job_id: {job_id}")

            # Mettre à jour le timestamp
            job_data['timestamp'] = datetime.now(timezone.utc).isoformat()

            # Utiliser directement le job_id comme ID du document
            doc_ref = self.db.collection(collection_path).document(job_id)

            # Vérifier si le document existe déjà
            doc = doc_ref.get()

            if doc.exists:
                print(f"[DEBUG] Updating existing document with job_id: {job_id}")
                # Mettre à jour le document existant
                doc_ref.set(job_data, merge=True)
            else:
                print(f"[DEBUG] Creating new document with job_id: {job_id}")
                # Créer un nouveau document avec le job_id comme ID
                doc_ref.set(job_data)

            print(f"[DEBUG] Successfully processed job_id: {job_id} in path: {collection_path}")

            # Publier notification si c'est dans le chemin notifications
            if "notifications" in collection_path:
                print(f"[DEBUG] Publishing notification for job_id: {job_id}")
                self._publish_notification_event(collection_path, job_data)

            return job_id

        except Exception as e:
            print(f"[ERROR] add_or_update_job_by_job_id failed: {e}")
            print(f"[ERROR] collection_path: {collection_path}")
            print(f"[ERROR] job_data: {job_data}")
            import traceback
            print(f"[ERROR] traceback: {traceback.format_exc()}")
            return None

    def _publish_notification_event(self, collection_path, job_data):
        """
        Publie une notification sur Redis PubSub pour les events temps réel.
        
        Utilise le nouveau système PubSub Redis au lieu des listeners Firebase.

        Args:
            collection_path (str): Chemin de la collection
            job_data (dict): Données du job
        """
        try:
            # Extraire user_id du chemin (format: clients/{user_id}/notifications)
            path_parts = collection_path.split('/')
            if len(path_parts) >= 2 and path_parts[0] == 'clients':
                user_id = path_parts[1]

                # Déterminer l'action (new ou update) selon si le document existe
                doc_ref = self.db.collection(collection_path).document(job_data.get('job_id') or job_data.get('file_id') or job_data.get('batch_id'))
                doc = doc_ref.get()
                action = "update" if doc.exists else "new"

                # Normalize functionName to match frontend enum: Router | APbookeeper | Bankbookeeper
                _fn_normalize = {"router": "Router", "apbookeeper": "APbookeeper", "bankbookeeper": "Bankbookeeper", "onboarding": "Onboarding"}
                raw_fn = job_data.get('function_name', 'Router')
                normalized_fn = _fn_normalize.get(raw_fn.lower() if isinstance(raw_fn, str) else "", raw_fn)

                file_name = job_data.get('file_name', '')
                status = job_data.get('status', 'pending')
                message = job_data.get('message', '')
                if not message and file_name:
                    message = f"{file_name} - {status}"

                # Transformer job_data en format notification pour le frontend
                notification_data = {
                    "docId": job_data.get('job_id') or job_data.get('file_id') or job_data.get('batch_id'),
                    "message": message,
                    "fileName": file_name,
                    "collectionId": job_data.get('collection_id', ''),
                    "collectionName": job_data.get('collection_name', ''),
                    "status": status,
                    "read": job_data.get('read', False),
                    "jobId": job_data.get('job_id', ''),
                    "fileId": job_data.get('file_id', ''),
                    "functionName": normalized_fn,
                    "timestamp": job_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                    "additionalInfo": job_data.get('additional_info', ''),
                    "driveLink": job_data.get('drive_link', ''),
                    "batchId": job_data.get('batch_id', ''),
                }

                # Utiliser le nouveau système PubSub Redis
                import asyncio
                from app.realtime.pubsub_helper import publish_notification_event
                
                # Publier de manière asynchrone
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Si la boucle est déjà en cours, créer une tâche
                        asyncio.create_task(
                            publish_notification_event(user_id, action, notification_data)
                        )
                    else:
                        # Sinon, exécuter directement
                        loop.run_until_complete(
                            publish_notification_event(user_id, action, notification_data)
                        )
                except RuntimeError:
                    # Si aucune boucle n'existe, en créer une nouvelle
                    asyncio.run(
                        publish_notification_event(user_id, action, notification_data)
                    )

                print(f"[DEBUG] Notification published via PubSub Redis for user {user_id}, action={action}")

        except Exception as e:
            print(f"[ERROR] Failed to publish notification: {e}")
            import traceback
            print(f"[ERROR] traceback: {traceback.format_exc()}")

    def add_or_update_job_by_batch_id(self, collection_path, job_data):
        """
        Ajoute un nouveau job ou met à jour un job existant en utilisant batch_id comme identifiant du document
        
        Args:
            collection_path (str): Chemin de la collection
            job_data (dict): Données du job avec au moins 'batch_id'
            
        Returns:
            str: ID du document (qui est égal au batch_id)
        """
        try:
            if 'batch_id' not in job_data:
                raise ValueError("job_data doit contenir un 'batch_id'")
                
            job_id = job_data['batch_id']
            
            # Mettre à jour le timestamp
            job_data['timestamp'] = datetime.now(timezone.utc).isoformat()
            
            # Utiliser directement le job_id comme ID du document
            doc_ref = self.db.collection(collection_path).document(job_id)
            
            # Vérifier si le document existe déjà
            doc = doc_ref.get()
            
            if doc.exists:
                # Mettre à jour le document existant
                doc_ref.set(job_data, merge=True)
            else:
                # Créer un nouveau document avec le job_id comme ID
                doc_ref.set(job_data)
            
            # Publier notification si c'est dans le chemin notifications
            if "notifications" in collection_path:
                self._publish_notification_event(collection_path, job_data)
                
            return job_id
                
        except Exception as e:
            print(f"Erreur lors de l'ajout/mise à jour du job: {e}")
            return None



    def add_document(self, collection_path, data,merge=None):
        """
        Ajoute un document à une collection Firestore avec un timestamp.

        Args:
            collection_path (str): Le chemin de la collection Firestore.
            data (dict): Les données du document à ajouter.

        Returns:
            str: L'ID du document créé.
        """
        
        try:
            # Ajouter un timestamp aux données
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
            merge = bool(merge) if merge is not None else False
            # Ajouter le document à Firestore
            doc_ref = self.db.collection(collection_path).document()
            doc_ref.set(data, merge=merge)

            # Retourner l'ID du document créé
            return doc_ref.id
        except Exception as e:
            print(f"Erreur pendant add_document sur la méthode de firebase:{e}")

    def set_document(self, document_path, data, merge=False):
        """
        Crée ou met à jour un document à l'emplacement spécifié par document_path.

        :param document_path: Chemin complet au document Firestore.
        :param data: Données à stocker dans le document, au format dictionnaire.
        :param merge: Si True, fusionne les données avec les données existantes. Sinon, remplace le document existant.
        """
        # Vérifier et ajuster le chemin s'il commence par '/'
        if document_path.startswith('/'):
            document_path = document_path[1:]

        # Référence au document Firestore
        doc_ref = self.db.document(document_path)
        doc_ref.set(data, merge=merge)
    def get_raw_document(self,document_path):
        
        """
        Récupère un document spécifique à partir de son chemin dans Firestore.

        Args:
            document_path (str): Le chemin complet du document (incluant la collection et l'ID du document).

        Returns:
            dict: Les données du document, ou None si le document n'existe pas.
        """
        # Récupérer le document
        try:
            if document_path.startswith('/'):
                document_path = document_path[1:]

            doc_ref = self.db.document(document_path)
            doc = doc_ref.get()

            # Vérifier si le document existe
            if doc.exists:
                data= doc.to_dict()
                return data
            else:
                return None
        except Exception as e:
            print(f"Erreur sur get_document pour cause:{e}")

    def get_document(self, document_path):
        """
        Récupère un document spécifique à partir de son chemin dans Firestore.

        Args:
            document_path (str): Le chemin complet du document (incluant la collection et l'ID du document).

        Returns:
            dict: Les données du document, ou None si le document n'existe pas.
        """
        # Récupérer le document
        try:
            if document_path.startswith('/'):
                document_path = document_path[1:]

            doc_ref = self.db.document(document_path)
            doc = doc_ref.get()

            # Vérifier si le document existe
            if doc.exists:
                data= doc.to_dict()
                data['id'] = doc.id  # Ajoute l'ID du document au dictionnaire
                return data
            else:
                return None
        except Exception as e:
            print(f"Erreur sur get_document pour cause:{e}")

    
    def add_timestamp_and_upload_to_firebase(self, log_entry):
        """Deprecated: alias vers add_timestamp_and_upload_to_task_manager."""
        log_entry['timestamp'] = datetime.now(timezone.utc).isoformat()
        self.upload_to_task_manager(log_entry)

    def add_timestamp_and_upload_to_task_manager(self, log_entry):
        log_entry['timestamp'] = datetime.now(timezone.utc).isoformat()
        self.upload_to_task_manager(log_entry)

    def upload_to_firebase(self, user_id, log_entry):
        """Deprecated: alias vers upload_to_task_manager."""
        self.upload_to_task_manager(log_entry, user_id=user_id)

    def upload_to_task_manager(self, log_entry, user_id=None):
        pinnokio_func = log_entry.get('pinnokio_func')
        job_id = log_entry.get('job_id')

        if not job_id:
            print(f"[WARN] upload_to_task_manager: pas de job_id dans log_entry, skip.")
            return None

        # S'assurer que department est renseigné
        data = log_entry.copy()
        if pinnokio_func and 'department' not in data:
            data['department'] = pinnokio_func

        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'

        doc_ref = self.db.collection(base_path).document(job_id)
        doc_ref.set(data, merge=True)
        print(f"Log ajouté dans task_manager pour job_id '{job_id}' (department: '{pinnokio_func}').")
        return doc_ref.path

    def find_matching_department(self, user_id, pinnokio_func):
        """Deprecated: retourne directement pinnokio_func (plus besoin de chercher le doc_id klk_vision)."""
        return pinnokio_func
    
    def fetch_documents_from_firestore(self,collection_path, mandat_id_value, max_docs=None):
       
        collection_ref = self.db.collection(collection_path)
        query = collection_ref.where(filter=FieldFilter('mandat_id', '==', mandat_id_value))

        documents = query.stream()
        documents_list = [doc.to_dict() for doc in documents]

        # Afficher le nombre total de documents récupérés
        print(f"Nombre total de documents récupérés avec mandat_id {mandat_id_value}: {len(documents_list)}")

        # Limiter les documents à traiter si max_docs est spécifié
        documents_to_process = documents_list[:max_docs] if max_docs else documents_list

        # La fonction retourne simplement les documents filtrés (ou leurs métadonnées)
        return documents_to_process
    
    def fetch_all_mandates(self,user_id):
        # Étape 1 : Extraire toutes les collections 'mandates'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        
        mandates_query=self.db.collection_group('mandates')
        results = mandates_query.stream()

        all_mandates = []
        filtered_mandates = []

        # Étape 2 : Filtrer les documents appartenant à la racine spécifique si user_id est défini
        
        
        print(f"Filtre sur le chemin: {base_path}")
        for doc in results:
            #print(f"Chemin trouvé: {doc.reference.path}")
            if doc.reference.path.startswith(base_path):
                filtered_mandates.append(doc)
                #print(f"Document ajouté au filtre: {doc.reference.path}")
        

        # Étape 3 : Appliquer les règles d'extraction et de filtrage sur les documents filtrés
        for doc in filtered_mandates:
            doc_data = doc.to_dict()

            # Filtrer les mandats actifs
            if doc_data.get('isactive', True):  # Par défaut, on suppose 'true' si non spécifié
                mandate = {
                    "id": doc.id,
                    "contact_space_name": doc_data.get('contact_space_name', ""),
                    "contact_space_id": doc_data.get('contact_space_id', ""),
                    "bank_erp": doc_data.get('bank_erp', ""),
                    "drive_space_parent_id": doc_data.get('drive_space_parent_id', ""),
                    "gl_accounting_erp": doc_data.get('gl_accounting_erp', ""),
                    "input_drive_doc_id": doc_data.get('input_drive_doc_id', ""),
                    "isactive": doc_data.get('isactive', True),
                    "legal_name": doc_data.get('legal_name', ""),
                    "main_doc_drive_id": doc_data.get('main_doc_drive_id', ""),
                    "output_drive_doc_id": doc_data.get('output_drive_doc_id', ""),
                    "ap_erp": doc_data.get('ap_erp', ""),
                    "ar_erp": doc_data.get('ar_erp', ""),
                    "base_currency": doc_data.get('base_currency', ""),
                    "dms_type": doc_data.get('dms_type', ""),
                    "chat_type": doc_data.get('chat_type', ""),
                    "communication_log_type":doc_data.get('communication_log_type',"")
                    
                }

                # Récupérer le chemin du document parent
                parent_doc_path = "/".join(doc.reference.path.split("/")[:-2])  # Retire les deux derniers segments (collection + ID)
                parent_doc_id = parent_doc_path.split("/")[-1]
                try:
                    # Récupérer les informations du document parent
                    
                    parent_doc = self.db.document(parent_doc_path).get()
                    
                    
                    if parent_doc.exists:
                        parent_data = parent_doc.to_dict()
                        mandate["parent_details"] = {
                            "parent_doc_id": parent_doc_id,  # Ajout du doc_id du parent
                            "client_mail": parent_data.get("client_mail", ""),
                            "client_name": parent_data.get("client_name", ""),
                            "client_address": parent_data.get("client_address", ""),
                            "client_phone": parent_data.get("client_phone", ""),
                            "client_uuid": parent_data.get("client_uuid", ""),
                            "drive_client_parent_id": parent_data.get("drive_client_parent_id", "")
                        }
                    else:
                        mandate["parent_details"] = {
                            "error": f"Parent document not found for path: {parent_doc_path}"
                        }
                except Exception as e:
                    mandate["parent_details"] = {
                        "error": f"Error fetching parent document: {str(e)}"
                    }

                try:
                    # Construire le chemin vers le document workflow_params
                    # Nous remplaçons la partie "mandates/{id}" par "setup/workflow_params"
                    path_parts = doc.reference.path.split("/")
                    client_id = path_parts[1]  # L'ID du client (user_id)
                    parent_doc_id = path_parts[3]  # L'ID du document parent
                    mandate_doc_id = path_parts[5]  # L'ID du document mandat

                    workflow_params_path = f"{base_path}/{parent_doc_id}/mandates/{mandate_doc_id}/setup/workflow_params"
                    #print(f"impression du chemin vers workflow:_{workflow_params_path}")
                    # Initialiser la structure pour les paramètres de workflow
                    mandate["workflow_params"] = {
                        "Apbookeeper_param": {
                            "apbookeeper_approval_contact_creation": False,
                            "apbookeeper_approval_required": False,
                            "apbookeeper_communication_method": ""
                        },
                        "Router_param": {
                            "router_approval_required": False,
                            "router_automated_workflow": False,
                            "router_communication_method": ""
                        },
                        "Banker_param":{"banker_communication_method": "",
                        "banker_approval_required":False,
                        "banker_approval_thresholdworkflow": ""}
                    }
                    
                    # Récupérer le document workflow_params
                    workflow_doc = self.db.document(workflow_params_path).get()
                    
                    if workflow_doc.exists:
                        workflow_data = workflow_doc.to_dict()
                        
                        # Extraire les paramètres pour Apbookeeper
                        if "Apbookeeper_param" in workflow_data:
                            ap_param = workflow_data.get("Apbookeeper_param", {})
                            mandate["workflow_params"]["Apbookeeper_param"] = {
                                "apbookeeper_approval_contact_creation": ap_param.get("apbookeeper_approval_contact_creation", False),
                                "apbookeeper_approval_required": ap_param.get("apbookeeper_approval_required", False),
                                "apbookeeper_communication_method": ap_param.get("apbookeeper_communication_method", ""),
                                "apbookeeper_approval_pendinglist_enabled": ap_param.get("apbookeeper_approval_pendinglist_enabled", False),
                                "apbookeeper_automated_workflow": ap_param.get("apbookeeper_automated_workflow", False),
                                "trust_threshold_required": ap_param.get("trust_threshold_required", False),
                                "trust_threshold_percent": ap_param.get("trust_threshold_percent", 95),
                            }
                        
                        # Extraire les paramètres pour Router
                        if "Router_param" in workflow_data:
                            router_param = workflow_data.get("Router_param", {})
                            mandate["workflow_params"]["Router_param"] = {
                                "router_approval_required": router_param.get("router_approval_required", False),
                                "router_automated_workflow": router_param.get("router_automated_workflow", False),
                                "router_communication_method": router_param.get("router_communication_method", ""),
                                "router_approval_pendinglist_enabled": router_param.get("router_approval_pendinglist_enabled", False),
                                "departments": router_param.get("departments", []),
                            }
                        
                        # Extraire les paramètres pour Banker
                        if "Banker_param" in workflow_data:
                            banker_param = workflow_data.get("Banker_param", {})
                            mandate["workflow_params"]["Banker_param"] = {
                                "banker_approval_required": banker_param.get("banker_approval_required", False),
                                "banker_approval_thresholdworkflow": banker_param.get("banker_approval_thresholdworkflow", 0),
                                "banker_communication_method": banker_param.get("banker_communication_method", ""),
                                "banker_approval_pendinglist_enabled": banker_param.get("banker_approval_pendinglist_enabled", False),
                                "banker_gl_approval": banker_param.get("banker_gl_approval", False),
                                "banker_voucher_approval": banker_param.get("banker_voucher_approval", False),
                            }

                        # Extraire les paramètres de date comptable depuis Accounting_param
                        if "Accounting_param" in workflow_data:
                            accounting_param = workflow_data.get("Accounting_param", {})
                            print(f"[FETCH_MANDATE] Found Accounting_param in Firestore: {accounting_param}")
                            mandate["workflow_params"]["Accounting_param"] = {
                                "accounting_date_definition": accounting_param.get("accounting_date_definition", True),
                                "accounting_date": accounting_param.get("accounting_date", ""),
                                "custom_mode": accounting_param.get("custom_mode", False),
                                "date_prompt": accounting_param.get("date_prompt", ""),
                            }
                            print(f"[FETCH_MANDATE] Built Accounting_param: {mandate['workflow_params']['Accounting_param']}")
                        else:
                            # Fallback: legacy format at root level
                            print(f"[FETCH_MANDATE] No Accounting_param key, using legacy format. workflow_data keys: {list(workflow_data.keys())}")
                            mandate["workflow_params"]["Accounting_param"] = {
                                "accounting_date_definition": workflow_data.get("automated_accounting_date_definition", True),
                                "accounting_date": workflow_data.get("accounting_date", ""),
                                "custom_mode": workflow_data.get("accounting_date_custom_mode", False),
                                "date_prompt": workflow_data.get("accounting_date_custom_prompt", ""),
                            }
                            print(f"[FETCH_MANDATE] Built legacy Accounting_param: {mandate['workflow_params']['Accounting_param']}")

                    else:
                        print(f"Workflow params not found for path: {workflow_params_path}")
                        # Nous conservons la structure vide initialisée plus haut

                    # Load Asset Management from separate document: setup/asset_model
                    try:
                        asset_model_path = f"{base_path}/{parent_doc_id}/mandates/{mandate_doc_id}/setup/asset_model"
                        print(f"[FETCH_MANDATE] Loading Asset_param from: {asset_model_path}")
                        asset_doc = self.db.document(asset_model_path).get()

                        if asset_doc.exists:
                            asset_data = asset_doc.to_dict()
                            print(f"[FETCH_MANDATE] Found Asset_param in Firestore: {asset_data}")
                            mandate["workflow_params"]["Asset_param"] = {
                                "asset_management_activated": asset_data.get("asset_management_activated", False),
                                "asset_automated_creation": asset_data.get("asset_automated_creation", True),
                                "asset_default_method": asset_data.get("asset_default_method", "linear"),
                                "asset_default_method_period": asset_data.get("asset_default_method_period", "12"),
                            }
                            print(f"[FETCH_MANDATE] Built Asset_param: {mandate['workflow_params']['Asset_param']}")
                        else:
                            # Default values if no asset_model document exists
                            print(f"[FETCH_MANDATE] No asset_model document found, using defaults")
                            mandate["workflow_params"]["Asset_param"] = {
                                "asset_management_activated": False,
                                "asset_automated_creation": True,
                                "asset_default_method": "linear",
                                "asset_default_method_period": "12",
                            }
                    except Exception as asset_err:
                        print(f"[FETCH_MANDATE] Error fetching asset_model: {str(asset_err)}")
                        mandate["workflow_params"]["Asset_param"] = {
                            "asset_management_activated": False,
                            "asset_automated_creation": True,
                            "asset_default_method": "linear",
                            "asset_default_method_period": "12",
                        }
                except Exception as e:
                    print(f"Error fetching workflow params: {str(e)}")
                    mandate["workflow_params"]["error"] = f"Error fetching workflow parameters: {str(e)}"

                try:
                    # Initialiser `erp_details` comme un dictionnaire vide
                    mandate["erp_details"] = {}

                    # Construire le chemin de la collection 'erp'
                    erp_collection_path = f"{doc.reference.path}/erp"
                    
                    erp_docs = self.db.collection(erp_collection_path).stream()
                    

                    #erp_docs = self.db.collection(erp_collection_path).stream()

                    # Parcourir les documents de la collection 'erp'
                    for erp_doc in erp_docs:
                        erp_data = erp_doc.to_dict()
                        mandate["erp_details"][erp_doc.id] = erp_data  # Ajouter chaque document avec son ID comme clé

                    # Si aucun document n'est trouvé, ajouter une information
                    if not mandate["erp_details"]:
                        mandate["erp_details"] = {"info": "No ERP documents found"}

                except Exception as e:
                    mandate["erp_details"] = {"error": f"Error fetching ERP documents: {str(e)}"}

                # Accéder à la collection 'context' et extraire 'accounting_context' et 'general_context'
                try:
                    mandate["context_details"] = {}
                    context_collection_path = f"{doc.reference.path}/context"
                    
                    context_docs = self.db.collection(context_collection_path).stream()
                    
                    
                    for context_doc in context_docs:
                        context_data = context_doc.to_dict()
                        if context_doc.id == "accounting_context":
                            accounting_data = context_data.get('data', {}).get('accounting_context_0', {})
                            mandate["context_details"]["accounting_context"] = accounting_data
                        elif context_doc.id == "general_context":
                            general_data = context_data.get('context_company_profile_report', '')
                            mandate["context_details"]["general_context"] = general_data
                        elif context_doc.id == "router_context":
                            # Vérifier si le document existe et contient router_prompt
                            router_prompt = context_data.get('router_prompt', {})
                            
                            if router_prompt:
                                # Stocker chaque contexte de département dans context_details
                                mandate["context_details"]["invoices_context"] = router_prompt.get('invoices', '')
                                mandate["context_details"]["expenses_context"] = router_prompt.get('expenses', '')
                                mandate["context_details"]["banks_cash_context"] = router_prompt.get('banks_cash', '')
                                mandate["context_details"]["hr_context"] = router_prompt.get('hr', '')
                                mandate["context_details"]["taxes_context"] = router_prompt.get('taxes', '')
                                mandate["context_details"]["letters_context"] = router_prompt.get('letters', '')
                                mandate["context_details"]["contrats_context"] = router_prompt.get('contrats', '')
                                mandate["context_details"]["financial_statement_context"] = router_prompt.get('financial_statement', '')

                except Exception as e:
                    mandate["context_details"]["error"] = f"Error fetching context documents: {str(e)}"

                all_mandates.append(mandate)

        return all_mandates

    def fetch_all_mandates_light(self, user_id):
        """
        🚀 Version OPTIMISÉE pour le selector d'entreprises.
        
        Charge les champs essentiels + les infos parent critiques (client_uuid)
        via une requête batch get_all() au lieu de requêtes séquentielles.
        
        Performance: ~200-400ms vs ~2.7s pour fetch_all_mandates (avec 18 mandats)
        - 1 requête collection_group (mandats)
        - 1 requête batch get_all (tous les parents en une fois)
        
        Les détails complets (workflow_params, erp_details, context_details)
        sont chargés via fetch_single_mandate() à la sélection.
        
        Args:
            user_id: ID de l'utilisateur Firebase
            
        Returns:
            Liste de dictionnaires avec les champs essentiels de chaque mandat
        """
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        
        print(f"🚀 [LIGHT] Chargement léger des mandats pour: {base_path}")
        
        # ═══════════════════════════════════════════════════════════════════
        # ÉTAPE 1: Récupérer tous les mandats en une seule requête
        # ═══════════════════════════════════════════════════════════════════
        mandates_query = self.db.collection_group('mandates')
        results = mandates_query.stream()
        
        # Collecter les mandats et les références des parents uniques
        mandates_data = []  # (doc, doc_data, parent_doc_path)
        parent_refs = {}    # {parent_doc_path: DocumentReference}
        
        for doc in results:
            # Filtrer par chemin utilisateur
            if not doc.reference.path.startswith(base_path):
                continue
                
            doc_data = doc.to_dict()
            
            # Filtrer les mandats inactifs
            if not doc_data.get('isactive', True):
                continue
            
            # Extraire le chemin du parent depuis le chemin du mandat
            # Le chemin est: clients/{user_id}/bo_clients/{parent_doc_id}/mandates/{mandate_id}
            path_parts = doc.reference.path.split("/")
            parent_doc_path = "/".join(path_parts[:-2])  # clients/{user_id}/bo_clients/{parent_doc_id}
            
            mandates_data.append((doc, doc_data, parent_doc_path))
            
            # Ajouter la référence du parent si pas déjà présente
            if parent_doc_path not in parent_refs:
                parent_refs[parent_doc_path] = self.db.document(parent_doc_path)
        
        print(f"📊 [LIGHT] {len(mandates_data)} mandats trouvés, {len(parent_refs)} parents uniques")
        
        # ═══════════════════════════════════════════════════════════════════
        # ÉTAPE 2: Charger tous les parents en une seule requête batch
        # ═══════════════════════════════════════════════════════════════════
        parents_data = {}  # {parent_doc_path: parent_data}
        
        if parent_refs:
            print(f"🔄 [LIGHT] Chargement batch des {len(parent_refs)} parents...")
            # get_all() fait une seule requête pour tous les documents
            parent_docs = self.db.get_all(list(parent_refs.values()))
            
            for parent_doc in parent_docs:
                if parent_doc.exists:
                    parents_data[parent_doc.reference.path] = parent_doc.to_dict()
            
            print(f"✅ [LIGHT] {len(parents_data)} parents chargés")
        
        # ═══════════════════════════════════════════════════════════════════
        # ÉTAPE 3: Construire les mandats avec les infos des parents
        # ═══════════════════════════════════════════════════════════════════
        all_mandates = []
        
        for doc, doc_data, parent_doc_path in mandates_data:
            path_parts = doc.reference.path.split("/")
            parent_doc_id = path_parts[3] if len(path_parts) >= 4 else ""
            
            # Récupérer les données du parent
            parent_data = parents_data.get(parent_doc_path, {})
            
            mandate = {
                "id": doc.id,
                "contact_space_name": doc_data.get('contact_space_name', ""),
                "contact_space_id": doc_data.get('contact_space_id', ""),
                "legal_name": doc_data.get('legal_name', ""),
                "isactive": True,
                "dms_type": doc_data.get('dms_type', ""),
                "chat_type": doc_data.get('chat_type', ""),
                "communication_log_type": doc_data.get('communication_log_type', ""),
                "base_currency": doc_data.get('base_currency', ""),
                # Stocker le path pour charger les détails plus tard
                "mandate_path": doc.reference.path,
                # ✅ CRITIQUE: parent_details avec client_uuid pour le LLM
                "parent_details": {
                    "parent_doc_id": parent_doc_id,
                    "client_mail": parent_data.get("client_mail", ""),
                    "client_name": parent_data.get("client_name", ""),
                    "client_address": parent_data.get("client_address", ""),
                    "client_phone": parent_data.get("client_phone", ""),
                    "client_uuid": parent_data.get("client_uuid", ""),  # ✅ CRITIQUE pour LLM
                    "drive_client_parent_id": parent_data.get("drive_client_parent_id", "")
                },
                # Champs utiles pour éviter un rechargement
                "bank_erp": doc_data.get('bank_erp', ""),
                "ap_erp": doc_data.get('ap_erp', ""),
                "ar_erp": doc_data.get('ar_erp', ""),
                "gl_accounting_erp": doc_data.get('gl_accounting_erp', ""),
                "input_drive_doc_id": doc_data.get('input_drive_doc_id', ""),
                "output_drive_doc_id": doc_data.get('output_drive_doc_id', ""),
                "main_doc_drive_id": doc_data.get('main_doc_drive_id', ""),
                "drive_space_parent_id": doc_data.get('drive_space_parent_id', ""),
            }
            
            # Ajouter le nom pour le tri
            mandate["name"] = doc_data.get('contact_space_name', "") or doc_data.get('legal_name', "Unknown")
            
            client_uuid = parent_data.get("client_uuid", "")[:20] + "..." if parent_data.get("client_uuid") else "VIDE"
            print(f"  📄 [LIGHT] {mandate['name']} | parent: {parent_doc_id} | client_uuid: {client_uuid}")
            
            all_mandates.append(mandate)
        
        print(f"✅ [LIGHT] {len(all_mandates)} mandats chargés avec infos parents (version optimisée)")
        return all_mandates

    def fetch_single_mandate(self, mandate_path):
        """
        Charge un mandat spécifique à partir de son chemin.
        
        Args:
            mandate_path (str): Chemin complet vers le mandat 
                            ex: 'clients/user_id/bo_clients/parent_id/mandates/mandate_id'
        
        Returns:
            dict: Données du mandat formatées selon la structure attendue
        """
        try:
            print(f"Chargement du mandat depuis: {mandate_path}")
            
            # Récupérer le document du mandat directement
            mandate_doc = self.db.document(mandate_path).get()
            
            if not mandate_doc.exists:
                raise ValueError(f"Mandat non trouvé au chemin: {mandate_path}")
            
            doc_data = mandate_doc.to_dict()

            # DEBUG: Log all keys in Firestore document
            print(f"[fetch_single_mandate] Document keys: {list(doc_data.keys())}")
            print(f"[fetch_single_mandate] Company info fields:")
            print(f"  - legal_status: '{doc_data.get('legal_status', 'NOT_FOUND')}'")
            print(f"  - country: '{doc_data.get('country', 'NOT_FOUND')}'")
            print(f"  - address: '{doc_data.get('address', 'NOT_FOUND')}'")
            print(f"  - phone_number: '{doc_data.get('phone_number', 'NOT_FOUND')}'")
            print(f"  - email: '{doc_data.get('email', 'NOT_FOUND')}'")
            print(f"  - website: '{doc_data.get('website', 'NOT_FOUND')}'")
            print(f"  - language: '{doc_data.get('language', 'NOT_FOUND')}'")
            print(f"  - has_vat: '{doc_data.get('has_vat', 'NOT_FOUND')}'")

            # Vérifier si le mandat est actif
            if not doc_data.get('isactive', True):
                raise ValueError(f"Le mandat au chemin {mandate_path} n'est pas actif")
            
            # Construire la structure de base du mandat
            mandate = {
                "id": mandate_doc.id,
                "mandate_path": mandate_path,
                "contact_space_name": doc_data.get('contact_space_name', ""),
                "contact_space_id": doc_data.get('contact_space_id', ""),
                "bank_erp": doc_data.get('bank_erp', ""),
                "drive_space_parent_id": doc_data.get('drive_space_parent_id', ""),
                "gl_accounting_erp": doc_data.get('gl_accounting_erp', ""),
                "input_drive_doc_id": doc_data.get('input_drive_doc_id', ""),
                "isactive": doc_data.get('isactive', True),
                "legal_name": doc_data.get('legal_name', ""),
                "main_doc_drive_id": doc_data.get('main_doc_drive_id', ""),
                "output_drive_doc_id": doc_data.get('output_drive_doc_id', ""),
                "ap_erp": doc_data.get('ap_erp', ""),
                "ar_erp": doc_data.get('ar_erp', ""),
                "base_currency": doc_data.get('base_currency', ""),
                "dms_type": doc_data.get('dms_type', ""),
                "chat_type": doc_data.get('chat_type', ""),
                "communication_log_type": doc_data.get('communication_log_type', ""),
                # Company info fields (added for Company Settings page)
                "legal_status": doc_data.get('legal_status', ""),
                "country": doc_data.get('country', ""),
                "address": doc_data.get('address', ""),
                "phone_number": doc_data.get('phone_number', ""),
                "email": doc_data.get('email', ""),
                "website": doc_data.get('website', ""),
                "language": doc_data.get('language', ""),
                "has_vat": doc_data.get('has_vat', False),
                "vat_number": doc_data.get('vat_number', ""),
                "ownership_type": doc_data.get('ownership_type', ""),
                # Workflow parameters
                "router_approval_required": doc_data.get('router_approval_required', False),
                "router_automated_workflow": doc_data.get('router_automated_workflow', False),
                "router_communication_method": doc_data.get('router_communication_method', "telegram"),
                "apbookeeper_approval_required": doc_data.get('apbookeeper_approval_required', False),
                "apbookeeper_approval_contact_creation": doc_data.get('apbookeeper_approval_contact_creation', False),
                "apbookeeper_communication_method": doc_data.get('apbookeeper_communication_method', "telegram"),
                "banker_approval_required": doc_data.get('banker_approval_required', False),
                "banker_approval_threshold_workflow": doc_data.get('banker_approval_threshold_workflow', 0),
                "banker_communication_method": doc_data.get('banker_communication_method', "telegram"),
                # Context details for AI
                "general_context": doc_data.get('general_context', ""),
                "accounting_context": doc_data.get('accounting_context', ""),
                "invoices_context": doc_data.get('invoices_context', ""),
                "expenses_context": doc_data.get('expenses_context', ""),
                "banks_cash_context": doc_data.get('banks_cash_context', ""),
                "hr_context": doc_data.get('hr_context', ""),
                "taxes_context": doc_data.get('taxes_context', ""),
            }

            # Récupérer les informations du document parent
            parent_doc_path = "/".join(mandate_path.split("/")[:-2])  # Retire les deux derniers segments
            parent_doc_id = parent_doc_path.split("/")[-1]
            
            try:
                parent_doc = self.db.document(parent_doc_path).get()
                
                if parent_doc.exists:
                    parent_data = parent_doc.to_dict()
                    mandate["parent_details"] = {
                        "parent_doc_id": parent_doc_id,
                        "client_mail": parent_data.get("client_mail", ""),
                        "client_name": parent_data.get("client_name", ""),
                        "client_address": parent_data.get("client_address", ""),
                        "client_phone": parent_data.get("client_phone", ""),
                        "client_uuid": parent_data.get("client_uuid", ""),
                        "drive_client_parent_id": parent_data.get("drive_client_parent_id", "")
                    }
                else:
                    mandate["parent_details"] = {
                        "error": f"Parent document not found for path: {parent_doc_path}"
                    }
            except Exception as e:
                mandate["parent_details"] = {
                    "error": f"Error fetching parent document: {str(e)}"
                }

            # Récupérer les paramètres de workflow
            try:
                workflow_params_path = f"{mandate_path}/setup/workflow_params"
                print(f"Chemin vers workflow: {workflow_params_path}")
                
                # Initialiser la structure pour les paramètres de workflow
                mandate["workflow_params"] = {
                    "Apbookeeper_param": {
                        "apbookeeper_approval_contact_creation": False,
                        "apbookeeper_approval_required": False,
                        "apbookeeper_communication_method": ""
                    },
                    "Router_param": {
                        "router_approval_required": False,
                        "router_automated_workflow": False,
                        "router_communication_method": ""
                    },
                    "Banker_param": {
                        "banker_communication_method": "",
                        "banker_approval_required": False,
                        "banker_approval_thresholdworkflow": ""
                    }
                }
                
                workflow_doc = self.db.document(workflow_params_path).get()
                
                if workflow_doc.exists:
                    workflow_data = workflow_doc.to_dict()
                    
                    # Extraire les paramètres pour Apbookeeper
                    if "Apbookeeper_param" in workflow_data:
                        ap_param = workflow_data.get("Apbookeeper_param", {})
                        mandate["workflow_params"]["Apbookeeper_param"] = {
                            "apbookeeper_approval_contact_creation": ap_param.get("apbookeeper_approval_contact_creation", False),
                            "apbookeeper_approval_required": ap_param.get("apbookeeper_approval_required", False),
                            "apbookeeper_communication_method": ap_param.get("apbookeeper_communication_method", "")
                        }
                    
                    # Extraire les paramètres pour Router
                    if "Router_param" in workflow_data:
                        router_param = workflow_data.get("Router_param", {})
                        mandate["workflow_params"]["Router_param"] = {
                            "router_approval_required": router_param.get("router_approval_required", False),
                            "router_automated_workflow": router_param.get("router_automated_workflow", False),
                            "router_communication_method": router_param.get("router_communication_method", "")
                        }
                    
                    # Extraire les paramètres pour Banker
                    if "Banker_param" in workflow_data:
                        banker_param = workflow_data.get("Banker_param", {})
                        mandate["workflow_params"]["Banker_param"] = {
                            "banker_approval_required": banker_param.get("banker_approval_required", False),
                            "banker_approval_thresholdworkflow": banker_param.get("banker_approval_thresholdworkflow", 0),
                            "banker_communication_method": banker_param.get("banker_communication_method", "")
                        }

                    # Extraire les paramètres de date comptable (Accounting_param)
                    if "Accounting_param" in workflow_data:
                        accounting_param = workflow_data.get("Accounting_param", {})
                        print(f"[fetch_single_mandate] Found Accounting_param: {accounting_param}")
                        mandate["workflow_params"]["Accounting_param"] = {
                            "accounting_date_definition": accounting_param.get("accounting_date_definition", True),
                            "accounting_date": accounting_param.get("accounting_date", ""),
                            "custom_mode": accounting_param.get("custom_mode", False),
                            "date_prompt": accounting_param.get("date_prompt", ""),
                        }
                    else:
                        # Valeurs par défaut si Accounting_param n'existe pas
                        print(f"[fetch_single_mandate] No Accounting_param found, using defaults")
                        mandate["workflow_params"]["Accounting_param"] = {
                            "accounting_date_definition": True,
                            "accounting_date": "",
                            "custom_mode": False,
                            "date_prompt": "",
                        }
                else:
                    print(f"Workflow params not found for path: {workflow_params_path}")

                # Charger Asset_param depuis le document séparé setup/asset_model
                try:
                    asset_model_path = f"{mandate_path}/setup/asset_model"
                    print(f"[fetch_single_mandate] Loading Asset_param from: {asset_model_path}")
                    asset_doc = self.db.document(asset_model_path).get()

                    if asset_doc.exists:
                        asset_data = asset_doc.to_dict()
                        print(f"[fetch_single_mandate] Found Asset_param: {asset_data}")
                        mandate["workflow_params"]["Asset_param"] = {
                            "asset_management_activated": asset_data.get("asset_management_activated", False),
                            "asset_automated_creation": asset_data.get("asset_automated_creation", True),
                            "asset_default_method": asset_data.get("asset_default_method", "linear"),
                            "asset_default_method_period": asset_data.get("asset_default_method_period", "12"),
                        }
                    else:
                        print(f"[fetch_single_mandate] No asset_model document found, using defaults")
                        mandate["workflow_params"]["Asset_param"] = {
                            "asset_management_activated": False,
                            "asset_automated_creation": True,
                            "asset_default_method": "linear",
                            "asset_default_method_period": "12",
                        }
                except Exception as asset_err:
                    print(f"[fetch_single_mandate] Error loading Asset_param: {asset_err}")
                    mandate["workflow_params"]["Asset_param"] = {
                        "asset_management_activated": False,
                        "asset_automated_creation": True,
                        "asset_default_method": "linear",
                        "asset_default_method_period": "12",
                    }
                    
            except Exception as e:
                print(f"Error fetching workflow params: {str(e)}")
                mandate["workflow_params"]["error"] = f"Error fetching workflow parameters: {str(e)}"

            # Récupérer les détails ERP
            try:
                mandate["erp_details"] = {}
                erp_collection_path = f"{mandate_path}/erp"
                
                erp_docs = self.db.collection(erp_collection_path).stream()
                
                for erp_doc in erp_docs:
                    erp_data = erp_doc.to_dict()
                    mandate["erp_details"][erp_doc.id] = erp_data
                
                if not mandate["erp_details"]:
                    mandate["erp_details"] = {"info": "No ERP documents found"}
                    
            except Exception as e:
                mandate["erp_details"] = {"error": f"Error fetching ERP documents: {str(e)}"}

            # Récupérer les détails de contexte
            try:
                mandate["context_details"] = {}
                context_collection_path = f"{mandate_path}/context"
                print(f"[fetch_single_mandate] Loading contexts from: {context_collection_path}")

                context_docs = list(self.db.collection(context_collection_path).stream())
                print(f"[fetch_single_mandate] Found {len(context_docs)} context documents")

                for context_doc in context_docs:
                    print(f"[fetch_single_mandate] Processing context doc: {context_doc.id}")
                    context_data = context_doc.to_dict()
                    if context_doc.id == "accounting_context":
                        accounting_data = context_data.get('data', {}).get('accounting_context_0', {})
                        mandate["context_details"]["accounting_context"] = accounting_data
                    elif context_doc.id == "bank_context":
                        bank_data = context_data.get('data', {}).get('bank_context_0', '')
                        mandate["context_details"]["bank_context"] = bank_data
                    elif context_doc.id == "general_context":
                        general_data = context_data.get('context_company_profile_report', '')
                        mandate["context_details"]["general_context"] = general_data
                    elif context_doc.id == "router_context":
                        router_prompt = context_data.get('router_prompt', {})
                        
                        if router_prompt:
                            mandate["context_details"]["invoices_context"] = router_prompt.get('invoices', '')
                            mandate["context_details"]["expenses_context"] = router_prompt.get('expenses', '')
                            mandate["context_details"]["banks_cash_context"] = router_prompt.get('banks_cash', '')
                            mandate["context_details"]["hr_context"] = router_prompt.get('hr', '')
                            mandate["context_details"]["taxes_context"] = router_prompt.get('taxes', '')
                            mandate["context_details"]["letters_context"] = router_prompt.get('letters', '')
                            mandate["context_details"]["contrats_context"] = router_prompt.get('contrats', '')
                            mandate["context_details"]["financial_statement_context"] = router_prompt.get('financial_statement', '')
                            
            except Exception as e:
                mandate["context_details"]["error"] = f"Error fetching context documents: {str(e)}"
                print(f"[fetch_single_mandate] Error loading contexts: {e}")

            # DEBUG: Log context_details content
            print(f"[fetch_single_mandate] context_details keys: {list(mandate.get('context_details', {}).keys())}")
            print(f"[fetch_single_mandate] general_context present: {'general_context' in mandate.get('context_details', {})}")

            print(f"Mandat chargé avec succès: {mandate.get('legal_name', 'Nom non disponible')}")
            return mandate
            
        except Exception as e:
            print(f"Erreur lors du chargement du mandat: {str(e)}")
            raise

    def _reconstruct_mandate_from_path(self, mandate_path):
        try:
            # Supprimer le '/' initial si présent
            if mandate_path.startswith('/'):
                mandate_path = mandate_path[1:]

            doc = self.db.document(mandate_path).get()
            if not doc.exists:
                return None

            doc_data = doc.to_dict()

            # Filtrer les mandats actifs
            if not doc_data.get('isactive', True):
                return None

            mandate = {
                "id": doc.id,
                "contact_space_name": doc_data.get('contact_space_name', ""),
                "contact_space_id": doc_data.get('contact_space_id', ""),
                "bank_erp": doc_data.get('bank_erp', ""),
                "drive_space_parent_id": doc_data.get('drive_space_parent_id', ""),
                "gl_accounting_erp": doc_data.get('gl_accounting_erp', ""),
                "input_drive_doc_id": doc_data.get('input_drive_doc_id', ""),
                "isactive": doc_data.get('isactive', True),
                "legal_name": doc_data.get('legal_name', ""),
                "main_doc_drive_id": doc_data.get('main_doc_drive_id', ""),
                "output_drive_doc_id": doc_data.get('output_drive_doc_id', ""),
                "ap_erp": doc_data.get('ap_erp', ""),
                "ar_erp": doc_data.get('ar_erp', ""),
                "base_currency": doc_data.get('base_currency', ""),
            }

            # Récupérer le chemin du document parent
            parent_doc_path = "/".join(mandate_path.split("/")[:-2])
            parent_doc_id = parent_doc_path.split("/")[-1]

            try:
                parent_doc = self.db.document(parent_doc_path).get()
                if parent_doc.exists:
                    parent_data = parent_doc.to_dict()
                    mandate["parent_details"] = {
                        "parent_doc_id": parent_doc_id,
                        "client_mail": parent_data.get("client_mail", ""),
                        "client_name": parent_data.get("client_name", ""),
                        "client_address": parent_data.get("client_address", ""),
                        "client_phone": parent_data.get("client_phone", ""),
                        "client_uuid": parent_data.get("client_uuid", ""),
                        "drive_client_parent_id": parent_data.get("drive_client_parent_id", ""),
                    }
                else:
                    mandate["parent_details"] = {
                        "error": f"Parent document not found for path: {parent_doc_path}"
                    }
            except Exception as e:
                mandate["parent_details"] = {
                    "error": f"Error fetching parent document: {str(e)}"
                }

            # Récupérer les détails de la collection 'erp'
            try:
                mandate["erp_details"] = {}
                erp_collection_path = f"{mandate_path}/erp"
                erp_docs = self.db.collection(erp_collection_path).stream()

                for erp_doc in erp_docs:
                    erp_data = erp_doc.to_dict()
                    mandate["erp_details"][erp_doc.id] = erp_data

                if not mandate["erp_details"]:
                    mandate["erp_details"] = {"info": "No ERP documents found"}

            except Exception as e:
                mandate["erp_details"] = {"error": f"Error fetching ERP documents: {str(e)}"}

            # Accéder à la collection 'context'
            try:
                mandate["context_details"] = {}
                context_collection_path = f"{mandate_path}/context"
                context_docs = self.db.collection(context_collection_path).stream()

                for context_doc in context_docs:
                    context_data = context_doc.to_dict()
                    if context_doc.id == "accounting_context":
                        mandate["context_details"]["accounting_context"] = context_data
                    elif context_doc.id == "general_context":
                        mandate["context_details"]["general_context"] = context_data
            except Exception as e:
                mandate["context_details"] = {"error": f"Error fetching context documents: {str(e)}"}

            return mandate

        except Exception as e:
            print(f"Error reconstructing mandate from path {mandate_path}: {str(e)}")
            return None
    def fetch_all_mandates_summary(self,user_id):
        # Étape 1 : Extraire toutes les collections 'mandates'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'

        mandates_query = self.db.collection_group('mandates')
        results = mandates_query.stream()

        mandate_summary = {}

        print(f"Filtre sur le chemin: {base_path}")
        for doc in results:
            if doc.reference.path.startswith(base_path):
                mandate_path = doc.reference.path

                # Supprimer le '/' initial si présent
                if mandate_path.startswith('/'):
                    mandate_path = mandate_path[1:]

                try:
                    # Récupérer le document de mandat
                    mandate_doc = self.db.document(mandate_path).get()
                    if not mandate_doc.exists:
                        continue

                    mandate_data = mandate_doc.to_dict()
                    company_name = mandate_data.get('legal_name', "Unknown Company")
                    contact_space = mandate_data.get('contact_space_name', "Unknown Contact Space")

                    # Récupérer les détails du document parent
                    parent_doc_path = "/".join(mandate_path.split("/")[:-2])
                    try:
                        parent_doc = self.db.document(parent_doc_path).get()
                        if parent_doc.exists:
                            parent_data = parent_doc.to_dict()
                            client_name = parent_data.get("client_name", "Unknown Client")
                        else:
                            client_name = "Unknown Client"
                    except Exception as e:
                        client_name = f"Error fetching parent document: {str(e)}"

                    mandate_summary[company_name] = {
                        "contact_space": contact_space,
                        "mandate_path": mandate_path,
                        "client_name": client_name
                    }

                except Exception as e:
                    print(f"Error processing mandate at path {mandate_path}: {str(e)}")

        return mandate_summary

    def fetch_journal_entries_by_mandat_id_and_job_ids(self,user_id, mandat_id, source, departement, job_ids):
        """
        Récupère les entrées task_manager pour un mandat_id, source et département donnés,
        filtrées par job_ids.
        Migration: remplace la requête sur klk_vision/{dept}/journal.
        """
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        query = task_manager_ref.where(filter=FieldFilter('department', '==', departement)) \
                                .where(filter=FieldFilter('mandat_id', '==', mandat_id)) \
                                .where(filter=FieldFilter('source', '==', source)).stream()

        entries_with_paths = []
        for entry in query:
            entry_data = entry.to_dict()
            if entry_data.get('status') not in ['rejection']:
                if entry_data.get('job_id') in job_ids:
                    document_path = f"{base_path}/{entry.id}"
                    entry_with_path = {
                        'data': entry_data,
                        'path': document_path,
                        'firebase_doc_id': entry.id
                    }
                    entries_with_paths.append(entry_with_path)

        return entries_with_paths

    


    def update_status_to_pending(self, firebase_path):
        """ Mettre à jour le statut à 'pending' pour le document spécifié par `firebase_path`. """
        try:
            # Référence directe au document en utilisant le chemin complet
            doc_ref = self.db.document(firebase_path)
            print(f"Accès au document avec le chemin: {firebase_path}")
            
            # Vérifier si le document existe
            doc = doc_ref.get()
            if doc.exists:
                print(f"Document trouvé. ID: {doc.id}")
                
                # Mettre à jour le champ 'status' à 'pending'
                doc_ref.update({'status': 'pending'})
                print(f"Document ID {doc.id} mis à jour avec le statut 'pending'")
            else:
                print(f"Aucun document trouvé pour le chemin: {firebase_path}")

        except Exception as e:
            print(f"Erreur lors de la mise à jour du statut: {e}")


    def fetch_journal_entries(self,user_id, mandat_id, departement, job_ids=None, status=None):
        """
        Récupère les entrées task_manager pour un mandat_id et département donnés,
        avec filtres optionnels sur job_ids et status.
        Migration: remplace la requête sur klk_vision/{dept}/journal.
        """
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        query = task_manager_ref.where(filter=FieldFilter('department', '==', departement)) \
                                .where(filter=FieldFilter('mandat_id', '==', mandat_id))
        if status is not None:
            query = query.where(filter=FieldFilter('status', '==', status))

        entries_with_paths = []
        for entry in query.stream():
            entry_data = entry.to_dict()
            job_id = entry_data.get('job_id')
            if job_ids is None or (isinstance(job_id, str) and job_id in job_ids):
                document_path = f"{base_path}/{entry.id}"
                entry_with_path = {
                    'data': entry_data,
                    'path': document_path,
                    'firebase_doc_id': entry.id
                }
                entries_with_paths.append(entry_with_path)

        return entries_with_paths



    def fetch_journal_entries_by_mandat_id_without_source(self,user_id, mandat_id, departement):
        """
        Récupère les entrées task_manager par mandat_id et département,
        en excluant les statuts 'rejection' et 'pending'.
        Migration: remplace la requête sur klk_vision/{dept}/journal.
        """
        try:
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            task_manager_ref = self.db.collection(base_path)

            query = task_manager_ref.where(filter=FieldFilter('department', '==', departement)) \
                                    .where(filter=FieldFilter('mandat_id', '==', mandat_id)).stream()

            entries_with_paths = []
            for entry in query:
                entry_data = entry.to_dict()
                if entry_data.get('status') not in ['rejection', 'pending']:
                    document_path = f"{base_path}/{entry.id}"
                    entry_with_path = {
                        'data': entry_data,
                        'path': document_path,
                        'firebase_doc_id': entry.id
                    }
                    entries_with_paths.append(entry_with_path)

            print(f"Total documents récupérés: {len(entries_with_paths)}")
            return entries_with_paths

        except Exception as e:
            print(f"erreur lors de la récupération des items traités depuis task_manager: {e}")

    def fetch_journal_entries_by_mandat_id(self,user_id, mandat_id, source, departement):
        """
        Récupère les entrées task_manager par mandat_id, source et département,
        en excluant le statut 'rejection'.
        Migration: remplace la requête sur klk_vision/{dept}/journal.
        """
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        query = task_manager_ref.where(filter=FieldFilter('department', '==', departement)) \
                                .where(filter=FieldFilter('mandat_id', '==', mandat_id)) \
                                .where(filter=FieldFilter('source', '==', source)).stream()

        entries_with_paths = []
        for entry in query:
            entry_data = entry.to_dict()
            if entry_data.get('status') not in ['rejection']:
                document_path = f"{base_path}/{entry.id}"
                entry_with_path = {
                    'data': entry_data,
                    'path': document_path,
                    'firebase_doc_id': entry.id
                }
                entries_with_paths.append(entry_with_path)

        return entries_with_paths

    def fetch_pending_journal_entries_by_mandat_id(self,user_id, mandat_id, source, departement):
        """
        Récupère uniquement les documents task_manager avec le statut 'pending'.
        Migration: remplace la requête sur klk_vision/{dept}/journal.
        """
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        query = task_manager_ref.where(filter=FieldFilter('department', '==', departement)) \
                                .where(filter=FieldFilter('mandat_id', '==', mandat_id)) \
                                .where(filter=FieldFilter('source', '==', source)) \
                                .where(filter=FieldFilter('status', '==', 'pending')).stream()

        entries_with_paths = []
        for entry in query:
            entry_data = entry.to_dict()
            document_path = f"{base_path}/{entry.id}"
            entry_with_path = {
                'data': entry_data,
                'path': document_path,
                'firebase_doc_id': entry.id
            }
            entries_with_paths.append(entry_with_path)
            print(f"Document pending trouvé: {entry_data.get('file_name', 'Unknown')} - Status: pending")

        print(f"Total documents pending récupérés: {len(entries_with_paths)}")
        return entries_with_paths


    

    def freeze_job_list(self, entries_with_paths):
        """
        Change temporairement le statut à 'in_queue' pour tous les documents de la liste.
        """
        if isinstance(entries_with_paths, dict):
            # Si une seule entrée a été passée comme dictionnaire, la convertir en liste
            entries_with_paths = [entries_with_paths]
        batch = self.db.batch()
        for entry in entries_with_paths:
            doc_ref = self.db.document(entry['path'])
            batch.update(doc_ref, {'status': 'in_queue'})
        batch.commit()

    def update_document_process_status(self, document_path):
        """
        Met à jour le statut d'un document spécifique à 'on_process'.
        """
        doc_ref = self.db.document(document_path)
        doc_ref.update({'status': 'on_process'})

    def unfreeze_job_list(self, entries_with_paths):
        """
        Remet le statut 'in_queue' à 'to_process' pour tous les documents de la liste.
        """
        batch = self.db.batch()
        for entry in entries_with_paths:
            try:
                doc_ref = self.db.document(entry['path'])
                doc_snapshot = doc_ref.get()
                if doc_snapshot.exists:
                    current_status = doc_snapshot.to_dict().get('status')
                    if current_status in ['in_queue', 'on_process']:
                        batch.update(doc_ref, {'status': 'to_process'})
                else:
                    print(f"Document inexistant : {entry['path']}")
            except Exception as e:
                print(f"Erreur lors de la mise à jour du document {entry['path']} : {e}")
        try:
            batch.commit()
            print("Mise à jour des statuts terminée avec succès.")
        except Exception as e:
            print(f"Erreur lors de la validation du batch : {e}")



    def delete_items_by_job_id(self, user_id, job_ids, mandate_path=None):
        """
        Supprime les items par job_id dans task_manager et expenses_details.

        ⚠️ IMPORTANT: côté task_manager, on fait un PURGE qui PRÉSERVE le champ `billing` si présent.
        Migration: klk_vision cleanup supprimé (migration vers task_manager terminée).

        Args:
            user_id: ID de l'utilisateur Firebase
            job_ids: Liste de job_ids à supprimer
            mandate_path: Chemin du mandat (optionnel, pour suppression expenses_details si EXbookeeper)
        """
        if not isinstance(job_ids, list):
            job_ids = [job_ids]

        for job_id in job_ids:
            exbookeeper_detected = False
            klk_job_id = None

            # Étape 1: Lire task_manager AVANT purge pour détecter EXbookeeper
            try:
                if user_id:
                    tm_base = f'clients/{user_id}/task_manager'
                else:
                    tm_base = 'task_manager'
                tm_doc_ref = self.db.collection(tm_base).document(job_id)
                tm_doc = tm_doc_ref.get()
                if tm_doc.exists:
                    tm_data = tm_doc.to_dict()
                    department = tm_data.get('department', '')
                    if department == 'EXbookeeper':
                        exbookeeper_detected = True
                        original_job_id = tm_data.get('job_id', '')
                        if original_job_id and original_job_id.startswith("klk"):
                            klk_job_id = original_job_id
            except Exception as e:
                print(f"⚠️ Erreur lecture task_manager pour {job_id} (non bloquant): {e}")

            # Étape 2: PURGE task_manager (préserver billing)
            print(f"[PURGE] task_manager pour le job_id: {job_id} (préserver billing si présent)")
            try:
                self.delete_task_manager_document(user_id=user_id, document_id=job_id)
            except Exception as e:
                print(f"⚠️ Erreur purge task_manager pour {job_id} (non bloquant): {e}")

            # Étape 3: Suppression dans expenses_details si EXbookeeper détecté et mandate_path fourni
            if mandate_path and exbookeeper_detected:
                try:
                    expenses_items_path = f"{mandate_path}/working_doc/expenses_details/items"
                    deleted = False
                    
                    # Essayer d'abord avec le job_id passé en argument
                    expense_doc_path = f"{expenses_items_path}/{job_id}"
                    expense_doc_ref = self.db.document(expense_doc_path)
                    expense_doc = expense_doc_ref.get()
                    
                    if expense_doc.exists:
                        expense_doc_ref.delete()
                        print(f"🗑️ [EXPENSES] Document supprimé: {expense_doc_path}")
                        deleted = True
                    else:
                        print(f"⚠️ [EXPENSES] Document non trouvé avec job_id: {expense_doc_path}")
                    
                    # Si pas trouvé et qu'on a un klk_job_id, essayer avec celui-ci
                    if not deleted and klk_job_id:
                        expense_doc_path_klk = f"{expenses_items_path}/{klk_job_id}"
                        expense_doc_ref_klk = self.db.document(expense_doc_path_klk)
                        expense_doc_klk = expense_doc_ref_klk.get()
                        
                        if expense_doc_klk.exists:
                            expense_doc_ref_klk.delete()
                            print(f"🗑️ [EXPENSES] Document supprimé avec klk_job_id: {expense_doc_path_klk}")
                            deleted = True
                        else:
                            print(f"⚠️ [EXPENSES] Document non trouvé avec klk_job_id: {expense_doc_path_klk}")
                    
                    if not deleted:
                        print(f"⚠️ [EXPENSES] Aucun document expense trouvé pour job_id={job_id} ou klk_job_id={klk_job_id}")
                            
                except Exception as e:
                    print(f"⚠️ [EXPENSES] Erreur suppression expenses_details (non bloquant): {e}")
        
            print(f"Suppression terminée pour le job_id: {job_id}")
        
        return True
        

    def get_client_name_by_business_name(self,user_id, business_name):
        """ 
        Recherche le client_name à partir de business_name dans la sous-collection 'mandates'.

        Args:
            business_name (str): Le nom du business à rechercher.

        Returns:
            tuple: (client_name, client_doc_ref) si trouvé, sinon (None, None)
        """
        try:
            # Itérer sur tous les documents de 'bo_clients'
            if user_id:
                base_path = f'clients/{user_id}/bo_clients'
            else:
                base_path = 'bo_clients'
            clients = self.db.collection(base_path).stream()
            for client_doc in clients:
                client_doc_ref = client_doc.reference
                client_name = client_doc.get('client_name')
                
                # Rechercher le business_name dans tous les documents de la sous-collection 'mandates'
                mandates = client_doc_ref.collection('mandates').stream()
                for mandate_doc in mandates:
                    if mandate_doc.get('contact_space_name') == business_name:
                        return client_name, client_doc_ref
                
            # Si aucun client trouvé
            return None, None
        except Exception as e:
            print(f"Erreur lors de la récupération du client_name par business_name : {str(e)}")
            return None, None

    # Alias pour la compatibilité RPC - redirige vers la version synchrone
    def async_delete_items_by_job_id(self, user_id, job_ids, mandate_path=None):
        """Alias pour delete_items_by_job_id pour compatibilité RPC."""
        return self.delete_items_by_job_id(user_id, job_ids, mandate_path=mandate_path)

    def delete_task_executions_collection(self, user_id: str, mandate_path: str, thread_key: str) -> bool:
        """
        Supprime la collection 'executions' d'une tâche dans Firestore.
        
        Args:
            user_id: ID de l'utilisateur Firebase (obligatoire)
            mandate_path: Chemin du mandat (ex: "clients/user_id/bo_clients/doc_id/mandates/mandate_id")
            thread_key: Clé du thread (ID de la tâche)
            
        Returns:
            bool: True si la suppression a réussi, False sinon
        """
        try:
            if not user_id or not mandate_path or not thread_key:
                print(f"⚠️ user_id, mandate_path ou thread_key manquant (user_id: {user_id}, mandate_path: {mandate_path}, thread_key: {thread_key})")
                return False
            
            # Construire la référence au document task
            # mandate_path est au format: clients/user_id/bo_clients/doc_id/mandates/mandate_id
            # On doit accéder à: mandate_path/tasks/thread_key/executions
            mandate_ref = self.db.document(mandate_path)
            task_ref = mandate_ref.collection('tasks').document(thread_key)
            
            # Supprimer tous les documents de la sous-collection executions
            executions_ref = task_ref.collection('executions')
            executions_docs = executions_ref.stream()
            
            deleted_count = 0
            for doc in executions_docs:
                doc.reference.delete()
                deleted_count += 1
            
            if deleted_count > 0:
                print(f"✅ {deleted_count} document(s) supprimé(s) de la collection executions")
            else:
                print(f"ℹ️ Aucun document trouvé dans la collection executions")
            
            return True
            
        except Exception as e:
            print(f"⚠️ Erreur lors de la suppression de la collection executions: {e}")
            import traceback
            traceback.print_exc()
            return False




    def delete_document_recursive(self, doc_path: str, batch_size: int = 100) -> bool:
        try:
            doc_ref = self.db.document(doc_path)

            # Supprime récursivement toutes les sous-collections
            for coll_ref in doc_ref.collections():
                self._delete_collection_recursive(coll_ref, batch_size)

            # Supprime le document lui-même
            doc_ref.delete()
            return True
        except Exception as e:
            print(f"Erreur suppression {doc_path}: {e}")
            return False

    def _delete_collection_recursive(self, coll_ref, batch_size: int = 100):
        while True:
            docs = list(coll_ref.limit(batch_size).stream())
            if not docs:
                break
            for d in docs:
                # Sous-collections du document
                for sub in d.reference.collections():
                    self._delete_collection_recursive(sub, batch_size)
                d.reference.delete()


    def delete_client_mandate(self, user_id, client_name, business_name):
        """Supprime la fiche du mandat client dans Firestore, y compris toutes les sous-collections."""
        try:
            # Recherche du document client par client_name
            if user_id:
                base_path = f'clients/{user_id}/bo_clients'
            else:
                base_path = 'bo_clients'
            print(f"impression de base_path: {base_path} et client_name: {client_name} et business_name: {business_name}")
            clients = self.db.collection(base_path).where(filter=FieldFilter('client_name', '==', client_name)).limit(1).get()
            if clients:
                client_doc_ref = clients[0].reference  # Référence au document du client trouvé
                
                # Recherche du document mandat par business_name dans la sous-collection 'mandates'
                mandates = client_doc_ref.collection('mandates').where(filter=FieldFilter('contact_space_name', '==', business_name)).limit(1).get()
                if mandates:
                    mandate_doc_ref = mandates[0].reference  # Référence au document du mandat trouvé
                    
                    # Supprimer toutes les sous-collections et leurs documents
                    self.delete_all_subcollections(mandate_doc_ref)
                    
                    # Supprimer le document principal du mandat
                    mandate_doc_ref.delete()
                    print(f"Le mandat pour {business_name} a été supprimé avec succès.")
                    return True

            else:
                print(f"Aucun client trouvé pour le client_name: {client_name}")

            return False
        except Exception as e:
            print(f"Erreur lors de la suppression de la fiche du mandat client dans Firebase : {str(e)}")
            return False

    def delete_all_subcollections(self, doc_ref):
        """Supprime toutes les sous-collections et leurs documents pour un document donné."""
        try:
            subcollections = doc_ref.collections()
            for subcollection in subcollections:
                docs = subcollection.stream()
                for doc in docs:
                    # Appel récursif pour supprimer toutes les sous-collections des sous-documents
                    self.delete_all_subcollections(doc.reference)
                    # Supprime le document de la sous-collection
                    doc.reference.delete()
                    print(f"Document {doc.id} dans la sous-collection {subcollection.id} supprimé.")
        except Exception as e:
            print(f"Erreur lors de la suppression des sous-collections : {str(e)}")

    def delete_client_if_no_mandates(self, user_id, client_name: str) -> tuple[bool, str, list]:
        """
        Supprime un client sous clients/{user_id}/bo_clients uniquement s'il n'a pas de sous-collection 'mandates'.

        Returns:
            (deleted, message, companies_list)
            - deleted True si la suppression a été effectuée, False sinon
            - message explicatif (en anglais en cas de blocage)
            - companies_list: liste des noms de sociétés attachées (vide si suppression ok)
        """
        try:
            if user_id:
                base_path = f"clients/{user_id}/bo_clients"
            else:
                base_path = "bo_clients"

            query = self.db.collection(base_path).where(filter=FieldFilter('client_name', '==', client_name)).limit(1).get()
            if not query:
                return False, "Client not found.", []

            client_ref = query[0].reference

            # Check mandates existence and get their names
            mandates_docs = client_ref.collection('mandates').stream()
            companies_list = []
            for mandate_doc in mandates_docs:
                mandate_data = mandate_doc.to_dict()
                # Get company name from contact_space_name or legal_name
                company_name = mandate_data.get('contact_space_name') or mandate_data.get('legal_name') or mandate_doc.id
                if mandate_data.get('isactive', True):  # Only include active mandates
                    companies_list.append(company_name)
            
            if companies_list:
                return (
                    False,
                    "This client has companies attached. Please delete the associated company/companies in Settings before deleting the client.",
                    companies_list
                )

            # Safe delete: remove subcollections (if any other) then the document
            for subcoll in client_ref.collections():
                # If it's mandates, we already ensured it's empty; still process generically
                self._delete_collection_recursive(subcoll)

            client_ref.delete()
            return True, "Client deleted successfully.", []
        except Exception as e:
            print(f"Erreur delete_client_if_no_mandates: {e}")
            return False, "Unexpected error while deleting client.", []

    def create_new_client(self, user_id: str, first_name: str, last_name: str, client_email: str) -> dict:
        """
        Crée un nouveau client dans Firebase sous clients/{user_id}/bo_clients.

        Args:
            user_id: ID de l'utilisateur Firebase
            first_name: Prénom du client
            last_name: Nom du client
            client_email: Email du client

        Returns:
            dict: {
                'success': bool,
                'message': str,
                'client_uuid': str (si success),
                'client_name': str (si success)
            }
        """
        try:
            if not user_id:
                return {'success': False, 'message': 'User ID is required.'}
            
            if not first_name or not last_name:
                return {'success': False, 'message': 'First name and last name are required.'}
            
            if not client_email:
                return {'success': False, 'message': 'Client email is required.'}

            # Construct client name
            client_name = f"{first_name.strip()} {last_name.strip()}"
            
            # Check if client with same name already exists
            base_path = f"clients/{user_id}/bo_clients"
            existing_query = self.db.collection(base_path).where(
                filter=FieldFilter('client_name', '==', client_name)
            ).limit(1).get()
            
            if existing_query:
                return {'success': False, 'message': f'A client named "{client_name}" already exists.'}

            # Generate unique client_uuid
            client_uuid = f"klk_client_{str(uuid.uuid4())[:8]}"
            
            # Prepare client data
            client_data = {
                'client_name': client_name,
                'client_mail': client_email.strip(),
                'client_uuid': client_uuid,
                'client_address': '',
                'client_phone': '',
                'created_at': firestore.SERVER_TIMESTAMP
            }

            # Add document to collection (auto-generated ID)
            doc_ref = self.db.collection(base_path).add(client_data)
            doc_id = doc_ref[1].id
            
            print(f"✅ Nouveau client créé: {client_name} (uuid: {client_uuid}, doc_id: {doc_id})")
            
            return {
                'success': True,
                'message': f'Client "{client_name}" created successfully.',
                'client_uuid': client_uuid,
                'client_name': client_name,
                'doc_id': doc_id
            }

        except Exception as e:
            print(f"❌ Erreur create_new_client: {e}")
            return {'success': False, 'message': f'Error creating client: {str(e)}'}

    def add_message_to_internal_message(self,user_id, job_id, message, sent_to, sent_from):
        
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        job_doc_ref = self.db.collection(base_path).document(job_id)
        
        internal_message_doc_ref = job_doc_ref.collection('internal_message').document('messages')

        # Étape 1: Récupérer le document actuel pour voir les messages existants
        doc = internal_message_doc_ref.get()
        if doc.exists:
            messages = doc.to_dict()
            next_message_number = len(messages) + 1
        else:
            messages = {}
            next_message_number = 1  # Commencer à 1 si aucun message n'existe

        # Étape 2: Ajouter le nouveau message
        message_key = str(next_message_number)  # Convertir en chaîne pour l'utilisation comme clé
        messages[message_key] = {
            'datetime': datetime.now(timezone.utc).isoformat(),
            'message': message,
            'sent_to': sent_to,
            'send_from': sent_from
        }

        # Mettre à jour le document avec les nouveaux messages
        internal_message_doc_ref.set(messages)
        print(f"Message {message_key} ajouté avec succès.")
    def get_internal_message(self,user_id, job_id):
        
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        job_doc_ref = self.db.collection(base_path).document(job_id)
        internal_message_doc_ref = job_doc_ref.collection('internal_message').document('messages')

        # Récupérer les messages actuels
        doc = internal_message_doc_ref.get()
       
        if doc.exists:
            messages = doc.to_dict()
        else:
            print("Aucun message trouvé.")
            return None, {} # Retourne un dict vide au lieu de None

        # Construire la vue formatée et le dictionnaire
        formatted_view = ""
        for k, m in (messages or {}).items():
            if not isinstance(m, dict):
                continue

            dt = m.get('datetime')
            datetime_str = dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(dt, 'strftime') else str(dt or 'N/A')

            service = m.get('sent_to', 'N/A')      # string OK
            motivation = m.get('message', '')      # tu affiches bien le contenu

            formatted_view += (
                f"Message {k} envoyé le {datetime_str}:\n"
                f"Expéditeurs: {m.get('send_from','N/A')}\n"
                f"Receveur: {service}\n"
                f"Motivation: {motivation}\n"
            )

        # Retourner la vue formatée et le dictionnaire des messages
        return formatted_view, messages

    def watch_apbookeeper_step_changes(self,user_id, job_id, initial_data, callback):
        """
        Surveille les modifications des étapes APBookeeper avec système d'acquittement.
        
        Args:
            job_id (str): L'ID du travail
            initial_data (dict): Données initiales à surveiller (peut être vide)
            callback: Fonction de callback asynchrone appelée lors des changements
        """
        print(f"Début de la fonction watch_apbookeeper_step_changes pour job_id: {job_id}")
        
        if user_id:
            base_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            base_path = f"task_manager/{job_id}"
        
        document_ref = self.db.document(base_path)
        
        # Dictionnaire pour suivre les valeurs déjà acquittées
        acknowledged_values = {}
        
        print(f"[DEBUG] Démarrage de l'écoute APBookeeper sur le chemin: {base_path}")

        def on_snapshot(doc_snapshot, changes, read_time):
            print("[DEBUG] Callback APBookeeper on_snapshot déclenché")
            try:
                for doc in doc_snapshot:
                    current_data = doc.to_dict()
                    
                    if not current_data:
                        print("[DEBUG] Aucune donnée trouvée dans le document")
                        return
                    
                    # Rechercher le champ APBookeeper_step_status
                    if 'APBookeeper_step_status' in current_data:
                        current_step_status = current_data['APBookeeper_step_status']
                        
                        changes_detected = False
                        changes_to_acknowledge = {}
                        
                        # Si c'est la première fois qu'on voit ce champ
                        if 'APBookeeper_step_status' not in acknowledged_values:
                            acknowledged_values['APBookeeper_step_status'] = {}
                        
                        previous_step_status = acknowledged_values['APBookeeper_step_status']
                        
                        # Vérifier chaque étape dans APBookeeper_step_status
                        for step_name, step_count in current_step_status.items():
                            previous_count = previous_step_status.get(step_name, 0)
                            current_count = int(step_count) if isinstance(step_count, (int, str)) else 0
                            
                            # Si le count a changé
                            if current_count != previous_count:
                                if not changes_detected:
                                    print(f"\nNouvelles modifications d'étapes APBookeeper détectées dans le document {job_id}:")
                                    changes_detected = True
                                
                                print(f"  - {step_name}:")
                                print(f"    Count précédent: {previous_count}")
                                print(f"    Nouveau count: {current_count}")
                                
                                # Préparer les changements pour le callback
                                if 'APBookeeper_step_status' not in changes_to_acknowledge:
                                    changes_to_acknowledge['APBookeeper_step_status'] = {}
                                changes_to_acknowledge['APBookeeper_step_status'][step_name] = current_count
                                
                                # Mettre à jour les valeurs acquittées
                                acknowledged_values['APBookeeper_step_status'][step_name] = current_count
                        
                        # Appeler le callback si des changements ont été détectés
                        if changes_to_acknowledge:
                            print(f"[DEBUG] Appel du callback avec les changements: {changes_to_acknowledge}")
                            # Utiliser asyncio pour appeler la fonction callback asynchrone
                            import asyncio
                            try:
                                # Créer une nouvelle boucle d'événements si nécessaire
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    # Si la boucle est déjà en cours d'exécution, créer une tâche
                                    asyncio.create_task(callback(changes_to_acknowledge))
                                else:
                                    # Sinon, exécuter directement
                                    loop.run_until_complete(callback(changes_to_acknowledge))
                            except RuntimeError:
                                # Si pas de boucle d'événements, en créer une nouvelle
                                asyncio.run(callback(changes_to_acknowledge))
                            
                            print("[DEBUG] Changements d'étapes acquittés:", changes_to_acknowledge)
                    
                    else:
                        print("[DEBUG] Pas de champ 'APBookeeper_step_status' trouvé dans les données")
                        
            except Exception as e:
                print(f"[ERREUR] Échec du listener Firestore APBookeeper : {e}")
                import traceback
                traceback.print_exc()

        document_watch = document_ref.on_snapshot(on_snapshot)
        print("[DEBUG] Listener APBookeeper attaché avec succès au document")
        
        return document_watch


    def watch_invoice_changes(self,user_id, job_id, initial_data):
        """
        Surveille les modifications spécifiques d'une facture avec système d'acquittement.
        
        Args:
            job_id (str): L'ID du travail
            initial_data (dict): Données initiales à surveiller
        """
        print(f"Debut de la fonction watch_invoice_changes")
        if user_id:
            base_path = f'clients/{user_id}/task_manager/{job_id}/document/initial_data'
        else:
            base_path =f"task_manager/{job_id}/document/initial_data"
        #document_path = f"task_manager/{job_id}/document/initial_data"
        document_ref = self.db.document(base_path)
        
        # Dictionnaire pour suivre les valeurs déjà acquittées
        acknowledged_values = {}
        
        #print(f"[DEBUG] Démarrage de l'écoute sur le chemin: {document_path}")

        def on_snapshot(doc_snapshot, changes, read_time):
            #print("[DEBUG] Callback on_snapshot déclenché")
            try:
                for doc in doc_snapshot:
                    current_data = doc.to_dict()
                    
                    if 'initial_data' not in current_data:
                        print("[DEBUG] Pas de données 'initial_data' trouvées")
                        return
                        
                    current_invoice_data = current_data['initial_data']
                    
                    changes_detected = False
                    changes_to_acknowledge = {}
                    
                    for field, old_value in initial_data.items():
                        # Récupérer la nouvelle valeur
                        if field == 'accounting_date' and isinstance(current_invoice_data.get(field), datetime):
                            new_value = current_invoice_data[field].strftime('%Y-%m-%d') if current_invoice_data[field] else None
                        else:
                            new_value = current_invoice_data.get(field)
                        
                        # Vérifier si la valeur a changé et n'a pas déjà été acquittée
                        if (field in current_invoice_data and 
                            new_value != old_value and 
                            acknowledged_values.get(field) != new_value):
                            
                            if not changes_detected:
                                print(f"\nNouvelles modifications détectées dans le document {job_id}:")
                                changes_detected = True
                            
                            print(f"  - {field}:")
                            print(f"    Ancienne valeur: {old_value}")
                            print(f"    Nouvelle valeur: {new_value}")
                            
                            # Mettre à jour la valeur de référence
                            initial_data[field] = new_value
                            # Enregistrer pour acquittement
                            changes_to_acknowledge[field] = new_value
                    
                    # Acquitter les changements une fois traités
                    if changes_to_acknowledge:
                        acknowledged_values.update(changes_to_acknowledge)
                        print("[DEBUG] Changements acquittés:", changes_to_acknowledge)
            except Exception as e:
                print(f"[ERREUR] Échec du listener Firestore : {e}")

        document_watch = document_ref.on_snapshot(on_snapshot)
        print("[DEBUG] Listener attaché avec succès au document")
        
        return document_watch

    
    def watch_unified_job_changes(self,user_id, job_id, initial_invoice_data, callback):
        """
        Listener unifié qui surveille tous les changements d'un job (facture + étapes APBookeeper).
        
        Args:
            job_id (str): L'ID du travail
            initial_invoice_data (dict): Données initiales de facture à surveiller
            callback: Fonction de callback asynchrone appelée lors des changements
        """
        print(f"🚀 Démarrage du listener unifié pour job_id: {job_id}")
        
        if user_id:
            document_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            document_path = f"task_manager/{job_id}"
        
        document_ref = self.db.document(document_path)
        
        # Dictionnaire unifié pour suivre tous les changements acquittés
        acknowledged_changes = {
            'invoice_data': {},  # Pour les données de facture
            'apbookeeper_steps': {}  # Pour les étapes APBookeeper
        }
        
        print(f"[DEBUG] Listener unifié configuré sur: {document_path}")

        def on_unified_snapshot(doc_snapshot, changes, read_time):
            print("[DEBUG] 🔔 Callback unifié déclenché")
            try:
                for doc in doc_snapshot:
                    current_data = doc.to_dict()
                    
                    if not current_data:
                        print("[DEBUG] Aucune donnée trouvée dans le document")
                        return
                    
                    # Structure pour tous les changements détectés
                    all_changes = {
                        'invoice_changes': {},
                        'step_changes': {}
                    }
                    changes_detected = False
                    
                    # ═══════════════════════════════════════════════════════════
                    # 📄 SECTION 1: Surveillance des données de facture
                    # ═══════════════════════════════════════════════════════════
                    if 'document' in current_data and 'initial_data' in current_data['document']:
                        current_invoice_data = current_data['document']['initial_data']
                        
                        for field, old_value in initial_invoice_data.items():
                            # Récupérer la nouvelle valeur
                            if field == 'accounting_date' and isinstance(current_invoice_data.get(field), datetime):
                                new_value = current_invoice_data[field].strftime('%Y-%m-%d') if current_invoice_data[field] else None
                            else:
                                new_value = current_invoice_data.get(field)
                            
                            # Vérifier si la valeur a changé et n'a pas déjà été acquittée
                            if (field in current_invoice_data and 
                                new_value != old_value and 
                                acknowledged_changes['invoice_data'].get(field) != new_value):
                                
                                if not changes_detected:
                                    print(f"\n📋 Modifications de facture détectées dans le document {job_id}:")
                                    changes_detected = True
                                
                                print(f"  💰 {field}:")
                                print(f"    Ancienne valeur: {old_value}")
                                print(f"    Nouvelle valeur: {new_value}")
                                
                                # Mettre à jour la valeur de référence
                                initial_invoice_data[field] = new_value
                                # Enregistrer pour acquittement
                                all_changes['invoice_changes'][field] = new_value
                                acknowledged_changes['invoice_data'][field] = new_value
                    
                    # ═══════════════════════════════════════════════════════════
                    # ⚙️ SECTION 2: Surveillance des étapes APBookeeper
                    # ═══════════════════════════════════════════════════════════
                    if 'APBookeeper_step_status' in current_data:
                        current_step_status = current_data['APBookeeper_step_status']
                        
                        # ✅ Vérification de type et traitement dans le même bloc
                        if isinstance(current_step_status, dict):
                            
                            # Initialiser si première fois
                            if not acknowledged_changes['apbookeeper_steps']:
                                acknowledged_changes['apbookeeper_steps'] = {}
                            
                            previous_step_status = acknowledged_changes['apbookeeper_steps']
                            
                            # UNE SEULE boucle pour vérifier chaque étape
                            for step_name, step_count in current_step_status.items():
                                previous_count = previous_step_status.get(step_name, 0)
                                current_count = int(step_count) if isinstance(step_count, (int, str)) else 0
                                
                                # Si le count a changé
                                if current_count != previous_count:
                                    if not changes_detected:
                                        print(f"\n⚙️ Modifications d'étapes APBookeeper détectées dans le document {job_id}:")
                                        changes_detected = True
                                    
                                    print(f"  🔧 {step_name}:")
                                    print(f"    Count précédent: {previous_count}")
                                    print(f"    Nouveau count: {current_count}")
                                    
                                    # Préparer les changements pour le callback
                                    if 'APBookeeper_step_status' not in all_changes['step_changes']:
                                        all_changes['step_changes']['APBookeeper_step_status'] = {}
                                    all_changes['step_changes']['APBookeeper_step_status'][step_name] = current_count
                                    
                                    # Mettre à jour les valeurs acquittées
                                    acknowledged_changes['apbookeeper_steps'][step_name] = current_count
                        
                        else:
                            # ✅ Gestion du cas où ce n'est pas un dict
                            print(f"⚠️ APBookeeper_step_status n'est pas un dictionnaire: {type(current_step_status)}")
                            print(f"   Valeur: {current_step_status}")

                    # ═══════════════════════════════════════════════════════════
                    # 🚀 SECTION 3: Appel du callback unifié
                    # ═══════════════════════════════════════════════════════════
                    if any(all_changes.values()):
                        print(f"[DEBUG] 📤 Envoi des changements unifiés: {all_changes}")
                        
                        # Appeler le callback unifié avec asyncio
                        import asyncio
                        try:
                            #import asyncio
                            #loop = asyncio.new_event_loop()
                            #asyncio.set_event_loop(loop)
                            #loop.run_until_complete(callback(all_changes))
                            #loop.close()
                            callback(all_changes)
                            print(f"[DEBUG] ✅ Changements unifiés acquittés")
                        except Exception as callback_error:
                            print(f"[ERREUR] Échec du callback unifié: {callback_error}")
                        
                        print(f"[DEBUG] ✅ Changements unifiés acquittés")
                            
            except Exception as e:
                print(f"[ERREUR] 💥 Échec du listener Firestore unifié : {e}")
                import traceback
                traceback.print_exc()

        # Attacher le listener
        document_watch = document_ref.on_snapshot(on_unified_snapshot)
        print("[DEBUG] ✅ Listener unifié attaché avec succès")
        
        return document_watch
    
    def watch_invoice_step(self,user_id, job_id):
        """
        Surveille toutes les modifications du document en temps réel.

        Args:
            job_id (str): L'ID du travail.
        """
        if user_id:
            base_path = f'clients/{user_id}/task_manager/{job_id}/document/initial_data'
        else:
            base_path =f"task_manager/{job_id}/document/initial_data"
        #document_path = f"task_manager/{job_id}/document/initial_data"
        document_ref = self.db.document(base_path)
        

        def on_snapshot(doc_snapshot, changes, read_time):
            for doc in doc_snapshot:
                # Récupère toutes les données du document
                data = doc.to_dict()
                print(f"Changement détecté pour {job_id}:")
                # Affiche toutes les données du document
                for key, value in data.items():
                    print(f"  - {key}: {value}")

        document_watch = document_ref.on_snapshot(on_snapshot)
        print(f"Écoute en temps réel activée pour le job_id {job_id}.")

    def upload_aws_instance_id(self,user_id, job_ids, aws_instance_id):
        """
        Enregistre l'ID d'une instance AWS pour une liste de jobs dans la collection 'task_manager'.

        Args:
            job_ids (list): Liste des IDs des travaux pour lesquels l'instance est associée.
            aws_instance_id (str): L'ID de l'instance AWS à enregistrer.

        Returns:
            bool: True si l'upload est réussi pour tous les jobs, False sinon.
        """
        try:
            for job_id in job_ids:
                # Référence au document correspondant à job_id dans la collection 'task_manager'
                if user_id:
                    base_path = f'clients/{user_id}/task_manager'
                else:
                    base_path = 'task_manager'
                job_doc_ref = self.db.collection(base_path).document(job_id)
                
                # Mise à jour ou création de la clé 'aws_instance_id'
                job_doc_ref.set({'aws_instance_id': aws_instance_id}, merge=True)
                print(f"L'ID de l'instance AWS '{aws_instance_id}' a été enregistré avec succès pour le job_id '{job_id}'.")
            return True
        except Exception as e:
            print(f"Erreur lors de l'upload de l'instance AWS pour un ou plusieurs jobs : {e}")
            return False


    
    def delete_doc_id(self, doc_path):
        """
        Supprime un document Firestore spécifié par son chemin.
        
        Args:
            doc_path (str): Le chemin complet du document Firestore à supprimer.
            
        Returns:
            bool: True si la suppression a réussi, False en cas d'échec.
        """
        try:
            # Obtenir une référence au document basée sur le chemin fourni
            doc_ref = self.db.document(doc_path)
            
            # Supprimer le document
            doc_ref.delete()
            
            print(f"Document à '{doc_path}' supprimé avec succès.")
            return True
        except Exception as e:
            print(f"Erreur lors de la suppression du document à '{doc_path}': {e}")
            return False

    def download_document_to_task_job_id(self,user_id, job_id):
        """
        Télécharge les données depuis une sous-collection nommée 'internal_message'
        dans un document spécifié par job_id dans la collection 'task_manager'.

        Args:
            job_id (str): L'ID du travail pour lequel les données sont téléchargées.

        Returns:
            dict: Dictionnaire des données téléchargées ou None si une erreur survient.
        """
        try:
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            
            job_doc_ref = self.db.collection(base_path).document(job_id)
            
            # Accès à la sous-collection 'document' du document trouvé
            internal_message_ref = job_doc_ref.collection('document')
            
            # Référence au document 'initial_data' dans la sous-collection 'internal_message'
            initial_data_doc_ref = internal_message_ref.document('initial_data')
            
            # Tentative de téléchargement des données du document 'initial_data'
            doc = initial_data_doc_ref.get()
            if doc.exists:
                print("Les données ont été téléchargées avec succès depuis 'internal_message'.")
                return doc.to_dict()
            else:
                print("Le document spécifié n'existe pas.")
                return False
        except Exception as e:
            print(f"Erreur lors du téléchargement des données : {e}")
            return None

    def download_invoice_step(self,user_id, job_id):
        """
        Récupère le champ 'APBookeeper_step_status' d'un document spécifié par job_id
        dans la collection 'task_manager'.

        Args:
            job_id (str): L'ID du travail pour lequel l'étape de facturation est récupérée.

        Returns:
            str: L'étape de la facturation actuelle ou un message indiquant que l'étape n'est pas disponible.
        """
        
        if user_id:
            document_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            document_path = f"task_manager/{job_id}"
        
        document_path = f"task_manager/{job_id}"
        document_ref = self.db.document(document_path)

        try:
            # Tentative de récupérer le document
            doc = document_ref.get()
            if doc.exists:
                # Récupérer la valeur du champ 'APBookeeper_step_status' si le document existe
                invoice_step = doc.to_dict().get('APBookeeper_step_status', 'Étape inconnue')
                print(f"L'étape actuelle de la facturation pour le document {document_path} est '{invoice_step}'.")
                return invoice_step
            else:
                print(f"Aucun document trouvé pour {document_path}.")
                return "Document non trouvé."
        except Exception as e:
            print(f"Erreur lors de la récupération du document {document_path}: {e}")
            return "Erreur lors de la récupération du document."


    def upload_invoice_step(self,user_id, job_id, invoice_step):
        """
        Crée ou met à jour le champ 'APBookeeper_step_status' dans un document spécifié par job_id
        dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            invoice_step (str): L'étape de la facturation à uploader dans le champ 'APBookeeper_step_status'.
        """
        if user_id:
            document_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            document_path = f"task_manager/{job_id}"
        
        
        document_ref = self.db.document(document_path)

        # Utilisation de set avec merge=True pour créer ou mettre à jour le document
        document_ref.set({'APBookeeper_step_status': invoice_step}, merge=True)
        print(f"Le champ 'APBookeeper_step_status' dans le document {document_path} a été mis à jour avec succès avec la valeur '{invoice_step}'.")

    def upload_metadatas_to_job_id(self,user_id, job_id, metadata):
        """
        Crée ou met à jour le champ 'APBookeeper_step_status' dans un document spécifié par job_id
        dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            invoice_step (str): L'étape de la facturation à uploader dans le champ 'APBookeeper_step_status'.
        """
        
        if user_id:
            document_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            document_path = f"task_manager/{job_id}"
        
        document_ref = self.db.document(document_path)

        # Utilisation de set avec merge=True pour créer ou mettre à jour le document
        document_ref.set({'Document_information':metadata}, merge=True)
        print(f"Le champ 'Document_information' dans le document {document_path} a été mis à jour avec succès avec la valeur .")

    def upload_chat_history(self,user_id, job_id, chat_history):
        """
        Ajoute ou met à jour un historique de chat dans une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel l'historique de chat est ajouté ou mis à jour.
            chat_history (list): L'historique de chat à uploader ou mettre à jour, assumé être une liste de dictionnaires.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            chat_history_data_ref = internal_message_ref.document('chat_history')
            
            # Préparer les mises à jour de l'historique de chat avec une clé unique pour chaque message
            chat_history_updates = {"messages": firestore.ArrayUnion([message for message in chat_history])}
            
            # Utiliser set avec merge=True pour fusionner les nouvelles données ou créer le document si nécessaire
            chat_history_data_ref.set(chat_history_updates, merge=True)
            
            print("L'historique de chat a été ajouté ou mis à jour avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout ou de la mise à jour de l'historique de chat : {e}")

    def upload_audit_report_posting_check(self,user_id, job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'contact_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"posting_check": audit_report_text}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")
    def upload_document_to_task_job_id(self,user_id, job_id, initial_document_data):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'contact_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'document' du document trouvé
            internal_message_ref = job_doc_ref.collection('document')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('initial_data')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"initial_data": initial_document_data}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    
    def download_auditor_review_on_step(self,user_id, job_id):
        """
        Télécharge un rapport d'audit depuis la sous-collection 'internal_message'.
        Récupère également les données APBookeeper_step_status depuis le document principal.
        
        Args:
            job_id (str): L'ID du travail
            
        Returns:
            dict: Structure de données contenant les étapes, leurs rapports et le statut APBookeeper
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
                
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Initialiser la structure de données de retour
            formatted_data = {
                'step_name': {}
            }
            
            # 1. 🆕 Récupérer APBookeeper_step_status depuis le document principal
            job_doc = job_doc_ref.get()
            if job_doc.exists:
                job_data = job_doc.to_dict()
                apbookeeper_steps = job_data.get('APBookeeper_step_status', {})
                if isinstance(apbookeeper_steps, dict) and apbookeeper_steps:
                    formatted_data['APBookeeper_step_status'] = apbookeeper_steps
                    print(f"✅ APBookeeper_step_status trouvé dans le document principal: {apbookeeper_steps}")
            
            # 2. Récupérer les données d'audit_report depuis internal_message
            internal_message_ref = job_doc_ref.collection('internal_message')
            audit_report_ref = internal_message_ref.document('audit_report')
            audit_report_doc = audit_report_ref.get()
            
            if audit_report_doc.exists:
                audit_report_data = audit_report_doc.to_dict()
                step_review = audit_report_data.get("step_review", {})
                
                if isinstance(step_review, dict):
                    for step_key, step_value in step_review.items():
                        for step_name, details in step_value.items():
                            formatted_data['step_name'][step_name] = {
                                'report': details.get('report', '')
                            }
            
            print(f"Données formatées (avec workflow): {formatted_data}")
            return formatted_data
                
        except Exception as e:
            print(f"Erreur lors du téléchargement du rapport d'audit: {e}")
            return {'step_name': {}}
    
    

    def get_uri_drive_link_and_file_name(self,user_id, job_id):
        if user_id:
            document_path = f'clients/{user_id}/task_manager'
        else:
            document_path = "task_manager"

        job_doc_ref = self.db.collection(document_path).document(job_id)
        job_doc = job_doc_ref.get()
        
        if job_doc.exists:
            audit_report_data = job_doc.to_dict()
            document_info = audit_report_data.get("Document_information", {})
            uri_drive_link = document_info.get("uri_file_link")
            file_name = document_info.get("file_name")
            
            if uri_drive_link and file_name:
                return {"link": uri_drive_link, "file_name": file_name}
            else:
                return "Missing drive link or file name"
        else:
            return "Job document does not exist"




    def transforme_audit_report_in_text(self,audit_report_text):
                # Vérifier si 'step_review' est un dictionnaire avant de formater
                def format_audit_report(data):
                    """
                    Transforme un dictionnaire en une chaîne de caractères formatée pour l'historique de log.
                    
                    Args:
                        data (dict): Le dictionnaire contenant les informations d'audit.
                    
                    Returns:
                        str: La chaîne de caractères formatée.
                    """
                    formatted_string = "Historique de log:\n"
                    
                    for step_key, step_value in data.items():
                        for step_name, details in step_value.items():
                            report = details.get('report', '')
                            formatted_string += f"Nom de l'étape: {step_name}\n"
                            formatted_string += f"Detail:\n{report}\n\n"
                    
                    return formatted_string
            
                if isinstance(audit_report_text, dict):
                    audit_report_text = format_audit_report(audit_report_text)
                else:
                    print(f"step_review n'est pas un dictionnaire: {audit_report_text}")
                
                return audit_report_text
           
                
    def restart_job(self,user_id, job_id: str) -> bool:
        """
        Redémarre un job en supprimant les données initiales, le rapport d'audit et les événements.
        Supprime les champs: APBookeeper_step_status, current_step, Document_information
        Supprime les documents: initial_data, audit_report
        Supprime la collection: events
        
        Args:
            user_id (str): L'identifiant de l'utilisateur
            job_id (str): L'identifiant du job à redémarrer
            
        Returns:
            bool: True si le redémarrage a réussi, False sinon
        """
        try:
            # Vérifier si le job_id est valide
            if not job_id:
                print("Error: job_id cannot be empty")
                return False
                
            # Construction du chemin vers le document
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            job_doc_ref = self.db.collection(base_path).document(job_id)
            
            # Vérifier si le document principal existe
            job_doc = job_doc_ref.get()
            if not job_doc.exists:
                print(f"Error: No job found with id {job_id}")
                return True
            
            # Lire les données actuelles pour déboguer
            current_data = job_doc.to_dict()
            print(f"Current document data before deletion: {list(current_data.keys())}")
            
            # Suppression des champs dynamiques ajoutés pendant le traitement
            fields_to_delete = {}
            # Note: Le champ s'appelle APBookeeper avec un seul 'k', pas APBookkeeper
            if 'APBookeeper_step_status' in current_data:
                fields_to_delete['APBookeeper_step_status'] = firestore.DELETE_FIELD
                print(f"Marking APBookeeper_step_status for deletion")
            
            if 'current_step' in current_data:
                fields_to_delete['current_step'] = firestore.DELETE_FIELD
                print(f"Marking current_step for deletion")
            
            if 'Document_information' in current_data:
                fields_to_delete['Document_information'] = firestore.DELETE_FIELD
                print(f"Marking Document_information for deletion")
            
            if 'status' in current_data:
                fields_to_delete['status'] = firestore.DELETE_FIELD
                print(f"Marking status for deletion (was: {current_data.get('status')})")
            
            # Exécuter la suppression si des champs existent
            if fields_to_delete:
                print(f"Deleting fields: {list(fields_to_delete.keys())}")
                job_doc_ref.update(fields_to_delete)
                
                # Attendre un court instant pour que Firebase traite la requête
                time.sleep(0.5)
                
                # Vérifier que les champs ont bien été supprimés
                updated_doc = job_doc_ref.get()
                if updated_doc.exists:
                    doc_data = updated_doc.to_dict()
                    print(f"Document data after deletion: {list(doc_data.keys())}")
                    
                    failed_deletions = []
                    if 'APBookeeper_step_status' in doc_data:
                        failed_deletions.append('APBookeeper_step_status')
                        print(f"WARNING: APBookeeper_step_status still exists after deletion")
                        print(f"Current value: {doc_data['APBookeeper_step_status']}")
                    
                    if 'current_step' in doc_data:
                        failed_deletions.append('current_step')
                        print(f"WARNING: current_step still exists after deletion")
                        print(f"Current value: {doc_data['current_step']}")
                    
                    if 'Document_information' in doc_data:
                        failed_deletions.append('Document_information')
                        print(f"WARNING: Document_information still exists after deletion")
                        print(f"Current value: {doc_data['Document_information']}")
                    
                    if failed_deletions:
                        print(f"ERROR: Failed to delete fields: {failed_deletions}")
                        return False
                    else:
                        print(f"Successfully deleted all requested fields for job {job_id}")
                else:
                    print(f"WARNING: Document {job_id} no longer exists")
            else:
                print(f"No fields to delete for job {job_id}")


            # Accès à la sous-collection 'document'
            document_collection_ref = job_doc_ref.collection('document')
            
            # Supprimer initial_data
            initial_data_ref = document_collection_ref.document('initial_data')
            initial_data_doc = initial_data_ref.get()
            if initial_data_doc.exists:
                print(f"Deleting initial_data document...")
                initial_data_ref.delete()
                time.sleep(0.3)  # Attendre que la suppression soit traitée
                
                # Vérifier que le document a bien été supprimé
                if initial_data_ref.get().exists:
                    print(f"WARNING: initial_data still exists after deletion for job {job_id}")
                    return False
                else:
                    print(f"Successfully deleted initial_data for job {job_id}")
            else:
                print(f"initial_data document does not exist for job {job_id}, skipping deletion")

            # Supprimer audit_report de la sous-collection internal_message
            audit_report_path = job_doc_ref.collection('internal_message')  
            audit_report_ref = audit_report_path.document('audit_report')
            audit_report_doc = audit_report_ref.get()
            if audit_report_doc.exists:
                print(f"Deleting audit_report document...")
                audit_report_ref.delete()
                time.sleep(0.3)  # Attendre que la suppression soit traitée
                
                # Vérifier que le document a bien été supprimé
                if audit_report_ref.get().exists:
                    print(f"WARNING: audit_report still exists after deletion for job {job_id}")
                    return False
                else:
                    print(f"Successfully deleted audit_report for job {job_id}")
            else:
                print(f"audit_report document does not exist for job {job_id}, skipping deletion")
            
            # Supprimer la collection 'events'
            print(f"Deleting events collection for job {job_id}...")
            events_collection_ref = job_doc_ref.collection('events')
            events_docs = events_collection_ref.get()
            
            deleted_events_count = 0
            for event_doc in events_docs:
                event_doc.reference.delete()
                deleted_events_count += 1
            
            if deleted_events_count > 0:
                print(f"Successfully deleted {deleted_events_count} events for job {job_id}")
            else:
                print(f"No events found in 'events' collection for job {job_id}")
            
            print(f"Successfully restarted job {job_id}")
            return True
                
        except Exception as e:
            print(f"Error while restarting job {job_id}: {str(e)}")
            # On pourrait également logger l'erreur dans un système de logging
            return False

    def download_auditor_apbookeeper_chat_with_user(self,user_id, job_id):
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            job_doc_ref = self.db.collection(document_path).document(job_id)
            internal_message_ref = job_doc_ref.collection('internal_message')
            chat_messages_ref = internal_message_ref.document('chat_messages')
            chat_messages_doc = chat_messages_ref.get()

            if chat_messages_doc.exists:
                chat_data = chat_messages_doc.to_dict()
                formatted_history = ""

                sessions = chat_data.get("sessions", [])
                if sessions:
                    # Récupérer l'index de la dernière session
                    last_session = sessions[-1]  # Prendre la dernière session (plus haut index)
                    
                    formatted_history += f"Session {len(sessions) - 1}:\n"  # Afficher l'index de la dernière session
                    messages = last_session.get("messages", "")
                    
                    if isinstance(messages, str):
                        try:
                            import ast
                            messages_list = ast.literal_eval(messages)
                            for msg in messages_list:
                                role = msg.get('role', 'unknown')
                                content = msg.get('content', '').replace('\\n', '\n')
                                formatted_history += f"{role}: {content}\n"
                        except:
                            formatted_history += f"{messages}\n"
                    else:
                        formatted_history += f"{messages}\n"
                    
                    formatted_history += "\n"
                
                return formatted_history
            else:
                print("Le document 'chat_messages' n'existe pas.")
                return None
        except Exception as e:
            print(f"Erreur lors du téléchargement de l'historique des chats: {str(e)}")
            return None

    def upload_auditor_apbookeeper_chat_with_user(self,user_id, job_id, chat_history):
        """
        Ajoute un historique de chat avec un horodatage à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le chat est ajouté.
            chat_history (list): Liste des messages à uploader dans le champ 'messages'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'chat_messages' dans la sous-collection 'internal_message'
            chat_messages_ref = internal_message_ref.document('chat_messages')
            
            # Ajouter un horodatage à la session de chat
            timestamp = datetime.now(timezone.utc).isoformat()
            session_data = {
                'timestamp': timestamp,
                'messages': chat_history
            }

            # Récupérer les sessions existantes s'il y en a
            chat_messages_doc = chat_messages_ref.get()
            if chat_messages_doc.exists:
                existing_chat_sessions = chat_messages_doc.to_dict().get("sessions", [])
            else:
                existing_chat_sessions = []

            # Ajouter la nouvelle session de chat
            existing_chat_sessions.append(session_data)

            # Préparer les données avec 'sessions' comme clé et les sessions combinées comme valeur
            chat_sessions_data = {"sessions": existing_chat_sessions}
            
            # Mettre à jour ou créer le document 'chat_messages' avec les données fournies
            chat_messages_ref.set(chat_sessions_data, merge=True)
            
            print("L'historique des chats a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout de l'historique des chats : {e}")

    def upload_auditor_review_on_step(self,user_id, job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'contact_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"step_review": audit_report_text}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    def upload_audit_report_is_invoice_check(self,user_id, job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'contact_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = audit_report_text
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    def upload_audit_report_archive_check(self,user_id, job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'contact_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"archive_check": audit_report_text}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    def upload_audit_report_contact_check(self, user_id,job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'contact_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"contact_check": audit_report_text}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    def upload_audit_report_booking_check(self,user_id, job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'booking_check'.
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(document_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"booking_check": audit_report_text}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    def download_general_context_review(self,user_id, client_uuid, contact_space_id, target_index):
        # Récupérer les données générales du client pour obtenir l'ID
        if user_id:
                document_path = f'clients/{user_id}/bo_clients'
        else:
            document_path = f"bo_clients"
        clients_query = self.db.collection(document_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            document_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            document_path = f'bo_clients/{client_id}/mandates'
        
        mandates_query = self.db.collection(document_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Chemin du contexte dans Firestore
        if user_id:
            document_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            document_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        document_ref = self.db.collection(document_path).document('general_context')

        # Télécharger le document et obtenir le champ spécifié
        document = document_ref.get()
        if document.exists:
            data = document.to_dict()
            review_motivation = data.get('context_topic_q_a', {}).get(str(target_index), {}).get('review_motivation')
            return review_motivation
        else:
            return None

    def upload_company_profile_report(self,user_id, client_uuid, contact_space_id, report):
        # Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Chemin du contexte dans Firestore
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        document_ref = self.db.collection(base_path).document('general_context')

        # Générer et mettre à jour le rapport de profil de l'entreprise
        document_ref.set({
            "context_company_profile_report": report
        }, merge=True)

        print(f"Profil de l'entreprise mis à jour pour le client {client_uuid}, mandat {contact_space_id}.")
    def upload_general_context_on_targets(self,user_id, client_uuid, contact_space_id, target_index, field, value):
        # Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Chemin du contexte dans Firestore
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        document_ref = self.db.collection(base_path).document('general_context')
        
        # Mettre à jour le champ spécifié
        document_ref.update({
            f"context_topic_q_a.{target_index}.{field}": value
        })

        print(f"Champ '{field}' mis à jour pour le client {client_uuid}, mandat {contact_space_id}, target {target_index}.")

    def upload_audit_report_contact_creation_check(self,user_id, job_id, audit_report_text):
        """
        Ajoute un rapport d'audit à une sous-collection nommée 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est ajouté.
            audit_report_text (str): Le texte de l'audit à uploader dans le champ 'booking_check'.
        """
        try:
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            # Construction du chemin vers le document spécifié par job_id dans la collection 'task_manager'
            job_doc_ref = self.db.collection(base_path).document(job_id)
            
            # Accès à la sous-collection 'internal_message' du document trouvé
            internal_message_ref = job_doc_ref.collection('internal_message')
            
            # Référence au document 'audit_report' dans la sous-collection 'internal_message'
            audit_report_ref = internal_message_ref.document('audit_report')
            
            # Préparer les données avec 'contact_check' comme clé et le texte de l'audit comme valeur
            audit_report_data = {"contact_creation_check": audit_report_text}
            
            # Mettre à jour ou créer le document 'audit_report' avec les données fournies
            audit_report_ref.set(audit_report_data, merge=True)
            
            print("Le rapport d'audit a été ajouté avec succès dans 'internal_message'.")
            
        except Exception as e:
            print(f"Erreur lors de l'ajout du rapport d'audit : {e}")

    def update_firebase_doc(self, doc_path, update_data):
        """
        Met à jour un document Firestore spécifié par son chemin avec les données fournies.
        
        Args:
            doc_path (str): Le chemin complet du document Firestore à mettre à jour.
            update_data (dict): Un dictionnaire contenant les champs à mettre à jour et leurs nouvelles valeurs.
            
        Returns:
            bool: True si la mise à jour a réussi, False en cas d'échec.
        """
        try:
            # Obtenir une référence au document basée sur le chemin fourni
            doc_ref = self.db.document(doc_path)
            
            # Effectuer la mise à jour avec les données fournies
            update_data['timestamp'] = datetime.now(timezone.utc).isoformat()
            doc_ref.update(update_data)
            
            print(f"Document à '{doc_path}' mis à jour avec succès.")
            return True
        except Exception as e:
            print(f"Erreur lors de la mise à jour du document à '{doc_path}': {e}")
            return False




    def download_accounting_context(self,user_id, client_uuid, contact_space_id):
        """Récupère le contexte comptable depuis Firestore sous le dossier 'erp'."""

        # Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Chemin du contexte comptable dans Firestore
        context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        context_doc_path = self.db.collection(context_path).document('coa_context')

        # Récupérer le document du contexte comptable
        context_doc = context_doc_path.get()

        if context_doc.exists:
            accounting_context = context_doc.to_dict()['data']
            print(f"Contexte comptable récupéré pour le client {client_uuid} et le mandat {contact_space_id}.")
            return accounting_context
        else:
            print(f"Aucun contexte comptable trouvé pour le client {client_uuid} et le mandat {contact_space_id}.")
            return None

    def upload_coa_context(self,user_id, client_uuid, contact_space_id, accounting_data, kill=False):
        """Initialise ou met à jour le contexte comptable dans Firestore sous le dossier 'erp'."""

        # Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Chemin du contexte comptable dans Firestore
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        context_doc_path = self.db.collection(base_path).document('coa_context')
        doc = context_doc_path.get()
        if not doc.exists:
            # Si le document n'existe pas, le créer avec les données initiales
            context_doc_path.set({'data': accounting_data})
            print(f"Document créé pour le client {client_uuid} et le mandat {contact_space_id}.")
        elif kill:
            # Écraser toutes les données si kill est True
            context_doc_path.set({'data': accounting_data})
            print("Toutes les données comptables ont été remplacées.")
        else:
        # Mise à jour incrémentielle des comptes spécifiques
            for group_name, accounts in accounting_data.items():
                for account_key, account_details in accounts.items():
                    update_path = f"data.{group_name}.{account_key}"
                    update_data = {}
                    
                    # Vérifier si les champs existent et les mettre à jour uniquement s'ils sont présents
                    if 'account_number' in account_details:
                        update_data[f"{update_path}.account_number"] = account_details['account_number']
                    if 'account_name' in account_details:
                        update_data[f"{update_path}.account_name"] = account_details['account_name']
                    if 'account_id' in account_details:
                        update_data[f"{update_path}.account_id"] = account_details['account_id']
                    if 'functions' in account_details:
                        update_data[f"{update_path}.functions"] = account_details['functions']
                    
                    if update_data:
                        context_doc_path.update(update_data)
                        print(f"Compte mis à jour: {account_key}")

        print(f"Contexte comptable mis à jour pour le client {client_uuid} et le mandat {contact_space_id}.")

    def download_general_context_ai_summary(self,user_id, client_uuid, contact_space_id):
        """Télécharge le contexte général initialisé depuis Firestore sous le dossier 'erp'."""
        
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Télécharger le document 'general_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        
        general_context_doc = self.db.collection(base_path).document('general_context').get()

        if general_context_doc.exists:
            general_context_data = general_context_doc.to_dict()
            
            # Vérifier si le nouveau chemin existe
            if 'context_company_profile_report' in general_context_data:
                return general_context_data.get('context_company_profile_report', {})
            else:
                # L'ancien chemin est encore utilisé
                old_content = general_context_data.get('content', '')
                
                # Migrer les données vers le nouveau format
                new_data = {
                    'context_company_profile_report': old_content
                }
                
                # Mettre à jour le document avec le nouveau format
                self.db.collection(base_path).document('general_context').set(new_data)
                
                # Supprimer l'ancien champ 'content'
                self.db.collection(base_path).document('general_context').update({
                    'content': firestore.DELETE_FIELD
                })
                
                return old_content
        else:
            return {}
        
    def download_general_context_init(self,user_id, client_uuid, contact_space_id):
        """Télécharge le contexte général initialisé depuis Firestore sous le dossier 'erp'."""
        
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Télécharger le document 'general_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        
        general_context_doc = self.db.collection(base_path).document('general_context').get()

        if general_context_doc.exists:
            general_context_data = general_context_doc.to_dict()
        else:
            general_context_data = {}

        return general_context_data.get('context_topic_q_a', {})

    def download_accounting_context_init(self,user_id, client_uuid, contact_space_id):
        """Télécharge le contexte général initialisé depuis Firestore sous le dossier 'erp'."""
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = next((client_doc.id for client_doc in clients_query), None)

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = next((mandate_doc.id for mandate_doc in mandates_query), None)

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Vérifier et télécharger le document 'accounting_context' sous 'context'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        context_doc_ref = self.db.collection(base_path).document('accounting_context')
        context_doc = context_doc_ref.get()

        if context_doc.exists:
            data = context_doc.to_dict().get('data', {})
            if 'accounting_context_0' in data:
                # La nouvelle structure est déjà en place
                return data['accounting_context_0']
            else:
                # Ancienne structure détectée, migration nécessaire
                new_data = {'accounting_context_0': data}
                context_doc_ref.set({'data': new_data})
                return data
        else:
            # Le document n'existe pas, vérifier l'ancienne structure
            old_context_doc = self.db.collection(base_path).document('accounting_context').get()
            if old_context_doc.exists:
                old_data = old_context_doc.to_dict()
                # Migrer les données vers la nouvelle structure
                new_data = {'accounting_context_0': old_data}
                context_doc_ref.set({'data': new_data})
                # Supprimer l'ancien document
                self.db.collection(base_path).document('accounting_context').delete()
                return old_data
            else:
                return False

    def get_last_refresh_accounting_context(self,user_id, client_uuid, contact_space_id):
        """Récupère le champ 'last_refresh' depuis Firestore pour le contexte comptable."""

        # Étape 1: Récupérer l'ID du client avec 'client_uuid'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = next((client_doc.id for client_doc in clients_query), None)
        
        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer l'ID du mandat spécifique en utilisant 'contact_space_id'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = next((mandate_doc.id for mandate_doc in mandates_query), None)
        print(f"impression de mandat_id:{mandate_id}")
        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Accéder au document 'accounting_context' pour récupérer 'last_refresh'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        context_doc_ref = self.db.collection(base_path).document('accounting_context')
        print(f"impression de context_doc:{context_doc_ref}")
        context_doc = context_doc_ref.get()
        print(f"impression de context_doc:{context_doc}")
        if context_doc.exists:
            data  = context_doc.to_dict().get('data',{})
            last_refresh=data.get('last_refresh')

            if last_refresh is not None:
                return last_refresh
            else:
                raise ValueError("'last_refresh' non trouvé dans 'data'.")
        
        else:
            print(f"context doc exite pas-----")
            raise ValueError("Document de contexte non trouvé ou champ 'last_refresh' absent.")


    def upload_accounting_context_init(self,user_id, client_uuid, contact_space_id, general_context_data):
        """Initialise ou met à jour le contexte général dans Firestore sous le dossier 'erp'."""
        
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = next((client_doc.id for client_doc in clients_query), None)

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = next((mandate_doc.id for mandate_doc in mandates_query), None)

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Mettre à jour ou remplacer le document 'accounting_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        context_doc_ref = self.db.collection(base_path).document('accounting_context')

        # Obtenir le timestamp actuel et le formater
        #current_timestamp = time.time()
        from datetime import datetime, timezone as _dt
        formatted_time = _dt.utcnow().isoformat()
        #formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_timestamp))

        # Utiliser une transaction pour garantir la cohérence des données
        @firestore.transactional
        def update_in_transaction(transaction):
            # Remplacer les données existantes par les nouvelles données avec le timestamp ajouté
            transaction.set(context_doc_ref, {
                'data': {
                    'accounting_context_0': general_context_data,
                    'last_refresh': formatted_time
                }
            }, merge=False)
            return "Contexte général et timestamp mis à jour avec succès."

        return update_in_transaction(self.db.transaction())


    def upload_general_context_init_chat_with_user(self,user_id, client_uuid, contact_space_id, chat_history, current_target_index):
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path =f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Créer ou mettre à jour le document 'general_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path =f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        
        # Récupérer les données actuelles du document 'general_context'
        general_context_doc = self.db.collection(base_path).document('general_context').get()
        if general_context_doc.exists:
            general_context_data = general_context_doc.to_dict()
        else:
            general_context_data = {}

        # Ajouter le chat à l'objectif actuel
        context_topic_q_a = general_context_data.get('context_topic_q_a', {})
        target_key = str(current_target_index)
        
        # Vérification et mise à jour du chat
        if target_key in context_topic_q_a:
            if 'chat' not in context_topic_q_a[target_key]:
                context_topic_q_a[target_key]['chat'] = []
            # Assurez-vous que chat_history est une chaîne de caractères
            if isinstance(chat_history, str):
                context_topic_q_a[target_key]['chat'].append(chat_history)
            else:
                raise ValueError("chat_history doit être une chaîne de caractères")
        else:
            context_topic_q_a[target_key] = {
                'chat': [chat_history] if isinstance(chat_history, str) else []
            }

        # Mettre à jour le document 'general_context' avec le nouveau chat
        self.db.collection(base_path).document('general_context').set({
            'context_topic_q_a': context_topic_q_a
        }, merge=True)

        print(f"Chat ajouté pour le client {client_uuid} et le mandat {contact_space_id}.")


    def download_general_context_chat_with_user(self,user_id, client_uuid, mandat_space_id, current_target_index):
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', mandat_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Récupérer le document 'general_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        general_context_doc = self.db.collection(base_path).document('general_context').get()
        
        all_chats = []

        if general_context_doc.exists:
            general_context_data = general_context_doc.to_dict()
            context_topic_q_a = general_context_data.get('context_topic_q_a', {})
            
            # Télécharger tous les chats jusqu'à l'index actuel inclusivement
            for index in range(int(current_target_index) + 1):
                index_str = str(index)
                if index_str in context_topic_q_a and 'chat' in context_topic_q_a[index_str]:
                    all_chats.extend(context_topic_q_a[index_str]['chat'])
        
        return all_chats
    def upload_general_context_init_topics_q_a(self,user_id, client_uuid, contact_space_id, get_q_and_a_topics):
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(f'bo_clients/{client_id}/mandates').where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Créer ou mettre à jour le document 'general_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'

        # Conversion des clés en chaînes de caractères et nettoyage des données
        context_topic_q_a = {}
        for key, topic in get_q_and_a_topics.items():
            print(f"Key: {key}")
            print(f"Target: {topic['target']}")
            print(f"Questions: {topic['questions']}")
            print(f"Tips: {topic['tips']}")

            clean_questions = [q for q in topic['questions'] if isinstance(q, str) and q]
            clean_tips = [t for t in topic['tips'] if isinstance(t, str) and t]

            context_topic_q_a[str(key)] = {
                'target': topic['target'],
                'questions': clean_questions,
                'tips': clean_tips,
                'is_done':False,
            }

        # Impression pour débogage
        print(f"Impression de context_topic_q_a avant le chargement dans Firebase: {context_topic_q_a}")

        # Vérification des données brutes avant le chargement
       

        # Impression pour débogage
        
        # Création du document 'general_context'
        self.db.collection(base_path).document('general_context').set({
            'context_topic_q_a': context_topic_q_a
        }, merge=True)

        print(f"Contexte général initialisé pour le client {client_uuid} et le mandat {contact_space_id}.")



    def upload_general_context_init(self,user_id, client_uuid, contact_space_id,general_context_data):
        """Initialise le contexte général dans Firestore sous le dossier 'erp'."""
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        # Étape 1: Récupérer les données générales du client pour obtenir l'ID
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_id = client_doc.id  # Obtenir l'ID du client

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        mandate_id = None
        for mandate_doc in mandates_query:
            mandate_id = mandate_doc.id  # Obtenir l'ID du mandat

        if not mandate_id:
            raise ValueError("Mandat non trouvé avec l'ID de l'espace de contact fourni")

        # Étape 3: Créer ou mettre à jour le document 'general_context' sous 'context' dans 'erp'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/context'
        else:
            base_path =f'bo_clients/{client_id}/mandates/{mandate_id}/context'
        #context_path = f'bo_clients/{client_id}/mandates/{mandate_id}/context'
       
        
        self.db.collection(base_path).document('general_context').set({
            'content': general_context_data
        })

        print(f"Contexte général initialisé pour le client {client_uuid} et le mandat {contact_space_id}.")

    


    def reconstruct_full_client_profile(self,user_id, client_uuid, contact_space_id):
        full_profile = {}  # Dictionnaire pour stocker le profil complet
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        # Étape 1: Récupérer les données générales du client
        clients_query = self.db.collection(base_path).where(filter=FieldFilter('client_uuid', '==', client_uuid)).limit(1).get()
        client_id = None
        for client_doc in clients_query:
            client_data = client_doc.to_dict()
            client_id = client_doc.id
            full_profile.update(client_data)  # Ajouter les données du client au profil complet
        
        # ⭐ Vérifier qu'un client a été trouvé
        if not client_id:
            raise ValueError(
                f"Aucun client trouvé pour client_uuid='{client_uuid}' dans {base_path}"
            )

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id'
        # ⭐ Stocker le client_id (disponible dès maintenant)
        full_profile['_client_id'] = client_id
        
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path =f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where(filter=FieldFilter('contact_space_id', '==', contact_space_id)).get()
        for mandate_doc in mandates_query:
            mandate_data = mandate_doc.to_dict()
            mandate_id = mandate_doc.id
            
            # ⭐ Stocker le mandate_id (disponible dans la boucle)
            full_profile['_mandate_id'] = mandate_id
            
            for key, value in mandate_data.items():
                full_profile[f'mandate_{key}'] = value  # Préfixer les clés pour éviter les conflits

            # Étape 3: Récupérer les données ERP spécifiques au mandat
            if user_id:
                base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/erp'
            else:
                base_path =f'bo_clients/{client_id}/mandates/{mandate_id}/erp'
            erp_query = self.db.collection(base_path).get()
            erp_data_accumulated = {}

            for erp_doc in erp_query:
                erp_data = erp_doc.to_dict()
                for key, value in erp_data.items():
                    # Si la clé existe déjà, ajouter un suffixe pour éviter les conflits
                    new_key = f'erp_{key}'
                    if new_key in erp_data_accumulated:
                        suffix = 1
                        while f'{new_key}_{suffix}' in erp_data_accumulated:
                            suffix += 1
                        new_key = f'{new_key}_{suffix}'
                    erp_data_accumulated[new_key] = value
            full_profile.update(erp_data_accumulated)
            
            # ⭐ Étape 4 : Récupérer les workflow_params (paramètres d'approbation)
            if user_id:
                workflow_params_path = f'clients/{user_id}/bo_clients/{client_id}/mandates/{mandate_id}/setup/workflow_params'
            else:
                workflow_params_path = f'bo_clients/{client_id}/mandates/{mandate_id}/setup/workflow_params'
            
            # Initialiser la structure par défaut
            workflow_params = {
                "Apbookeeper_param": {
                    "apbookeeper_approval_contact_creation": False,
                    "apbookeeper_approval_required": False,
                    "apbookeeper_communication_method": ""
                },
                "Router_param": {
                    "router_approval_required": False,
                    "router_automated_workflow": False,
                    "router_communication_method": ""
                },
                "Banker_param": {
                    "banker_approval_required": False,
                    "banker_approval_thresholdworkflow": 0,
                    "banker_communication_method": ""
                }
            }
            
            try:
                workflow_doc = self.db.document(workflow_params_path).get()
                
                if workflow_doc.exists:
                    workflow_data = workflow_doc.to_dict()
                    
                    # Extraire les paramètres pour Apbookeeper
                    if "Apbookeeper_param" in workflow_data:
                        ap_param = workflow_data.get("Apbookeeper_param", {})
                        workflow_params["Apbookeeper_param"] = {
                            "apbookeeper_approval_contact_creation": ap_param.get("apbookeeper_approval_contact_creation", False),
                            "apbookeeper_approval_required": ap_param.get("apbookeeper_approval_required", False),
                            "apbookeeper_communication_method": ap_param.get("apbookeeper_communication_method", "")
                        }
                    
                    # Extraire les paramètres pour Router
                    if "Router_param" in workflow_data:
                        router_param = workflow_data.get("Router_param", {})
                        workflow_params["Router_param"] = {
                            "router_approval_required": router_param.get("router_approval_required", False),
                            "router_automated_workflow": router_param.get("router_automated_workflow", False),
                            "router_communication_method": router_param.get("router_communication_method", "")
                        }
                    
                    # Extraire les paramètres pour Banker
                    if "Banker_param" in workflow_data:
                        banker_param = workflow_data.get("Banker_param", {})
                        workflow_params["Banker_param"] = {
                            "banker_approval_required": banker_param.get("banker_approval_required", False),
                            "banker_approval_thresholdworkflow": banker_param.get("banker_approval_thresholdworkflow", 0),
                            "banker_communication_method": banker_param.get("banker_communication_method", "")
                        }
            except Exception as e:
                print(f"⚠️ Erreur récupération workflow_params: {str(e)} - Utilisation des valeurs par défaut")
            
            # Ajouter les workflow_params au profil complet
            full_profile['workflow_params'] = workflow_params
        
        # ⭐ Vérifier qu'au moins un mandat a été trouvé
        if '_mandate_id' not in full_profile:
            raise ValueError(
                f"Aucun mandat trouvé pour contact_space_id='{contact_space_id}' "
                f"dans {base_path}. Vérifiez que le mandat existe avec ce contact_space_id."
            )
        
        return full_profile

    


    def get_folder_ids_from_fetched_details(self, fetched_details):
        # Extraire ou définir des valeurs par défaut pour les champs qui peuvent ne pas être toujours présents
        mandate_drive_space_parent_id=fetched_details.get('mandate_drive_space_parent_id')
        input_drive_doc_id = fetched_details.get('mandate_input_drive_doc_id', "")
        output_drive_doc_id = fetched_details.get('mandate_output_drive_doc_id', "")
        mandat_space_id = fetched_details.get('mandate_contact_space_id', "")
        mandat_space_name = fetched_details.get('mandate_contact_space_name', "")
        mandat_base_currency = fetched_details.get('mandate_base_currency', "")
        client_name = fetched_details.get('client_name', "")
        legal_name = fetched_details.get('mandate_legal_name', fetched_details.get('client_name', ""))
        uuid_id = fetched_details.get('client_uuid', "")
        gl_sheet_id = fetched_details.get('gl_sheet_id', "")
        root_folder_id = fetched_details.get('mandate_drive_client_parent_id', "")
        doc_folder_id = fetched_details.get('mandate_main_doc_drive_id', "")
        #ap_erp_type=fetched_details.get('mandate_ap_erp',"")
        #ar_erp_type=fetched_details.get('mandate_ar_erp',"")
        #bank_erp_type=fetched_details.get('mandate_bank_erp',"")
        #gl_accounting_erp_type=fetched_details.get('mandate_gl_accounting_erp',"")
        
        
        # Pour les champs potentiellement présents sous différents noms ou dans d'autres appels
        odoo_url = fetched_details.get('odoo_url', fetched_details.get('erp_odoo_url', ""))
        odoo_username = fetched_details.get('odoo_username', fetched_details.get('erp_odoo_username', ""))
        odoo_db = fetched_details.get('odoo_db', fetched_details.get('erp_odoo_db', ""))
        odoo_company_name=fetched_details.get('odoo_company_name',fetched_details.get('erp_odoo_company_name', ""))
        odoo_erp_type = fetched_details.get('erp_erp_type', "")
        odoo_secret_manager = fetched_details.get('erp_secret_manager', "")

        #Champs récupération des données erp
        gl_accounting_erp=fetched_details.get('mandate_gl_accounting_erp',"")
        ar_erp=fetched_details.get('mandate_ar_erp',"")
        ap_erp=fetched_details.get('mandate_ap_erp',"")
        bank_erp=fetched_details.get('mandate_bank_erp',"")

        #Champs de récupération du context
        

        return input_drive_doc_id, output_drive_doc_id, mandat_space_id, mandat_space_name, client_name, legal_name, uuid_id, gl_sheet_id, root_folder_id, doc_folder_id, odoo_erp_type, odoo_url, odoo_username, odoo_secret_manager, odoo_db,mandate_drive_space_parent_id,odoo_company_name,mandat_base_currency,gl_accounting_erp,ar_erp,ap_erp,bank_erp

    def fetch_job_journals_by_mandat_id(self,user_id, mandat_id):
        """
        Récupère tous les documents task_manager pour un mandat_id donné.
        Migration: remplace l'ancien scan de klk_vision/{dept}/journal.
        """
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        task_manager_ref = self.db.collection(base_path)

        query = task_manager_ref.where(filter=FieldFilter('mandat_id', '==', mandat_id)).stream()

        filtered_journals = []
        document_paths = []
        for doc in query:
            doc_data = doc.to_dict()
            document_path = f"{base_path}/{doc.id}"
            filtered_journals.append(doc_data)
            document_paths.append(document_path)

        return filtered_journals, document_paths

    def delete_documents_by_full_paths(self, document_paths):
        """
        Supprime les documents spécifiés par leurs chemins complets.

        :param document_paths: Liste des chemins complets des documents à supprimer.
        """
        for full_path in document_paths:
            try:
                # Supposons que full_path est de la forme "collection/docID/subcollection/docID"
                # et que nous voulons supprimer le document à ce chemin
                doc_ref = self.db.document(full_path)
                doc_ref.delete()
                print(f'Document à {full_path} supprimé avec succès.')
            except Exception as e:
                print(f'Erreur lors de la suppression du document à {full_path}: {e}')

   
    def get_client_doc(self,user_id, uuid):
        """
        Récupère l'ID du document ayant une clé 'client_uuid' correspondant à l'uuid fourni.
        
        Args:
            uuid (str): L'uuid du client à rechercher.
        
        Returns:
            str: L'ID du document correspondant, ou None si aucun document ne correspond.
        """
       
        base_path = f'clients/{user_id}/bo_clients'
        clients_ref = self.db.collection(base_path)
        docs = clients_ref.stream()

        for doc in docs:
            client_data = doc.to_dict()
            if client_data.get('client_uuid') == uuid:
                return doc.id  # Retourne l'ID du document correspondant

        return None  # Aucun document correspondant trouvé

    def resolve_client_by_contact_space(self, user_id: Optional[str], contact_space_id: str) -> Optional[dict]:
        """
        Résout le client (client_uuid + parent_doc_id) en fonction d'un contact_space_id.

        Args:
            user_id: ID Firebase de l'utilisateur (None pour comptes partagés)
            contact_space_id: Identifiant de la société / mandate (collection_name côté Reflex)

        Returns:
            dict contenant au minimum client_uuid si trouvé, sinon None.
        """
        if not contact_space_id:
            return None

        try:
            prefix = f"clients/{user_id}/bo_clients/" if user_id else "bo_clients/"
            mandates_query = (
                self.db.collection_group("mandates")
                .where(filter=FieldFilter("contact_space_id", "==", contact_space_id))
                .get()
            )

            for mandate_doc in mandates_query:
                path = mandate_doc.reference.path  # ex: clients/{uid}/bo_clients/{client_doc}/mandates/{mandate_id}

                if user_id and not path.startswith(prefix):
                    continue  # Ignorer les résultats appartenant à un autre utilisateur

                parts = path.split("/")
                try:
                    bo_clients_idx = parts.index("bo_clients")
                except ValueError:
                    continue

                client_doc_id = parts[bo_clients_idx + 1] if len(parts) > bo_clients_idx + 1 else None
                mandate_id = parts[bo_clients_idx + 3] if len(parts) > bo_clients_idx + 3 else None

                if not client_doc_id:
                    continue

                parent_doc_path = (
                    f"{prefix}{client_doc_id}"
                    if user_id
                    else f"bo_clients/{client_doc_id}"
                )

                parent_doc = self.db.document(parent_doc_path).get()
                client_uuid = parent_doc.to_dict().get("client_uuid") if parent_doc.exists else None

                return {
                    "client_uuid": client_uuid,
                    "client_doc_id": client_doc_id,
                    "mandate_id": mandate_id,
                    "mandate_path": path,
                }

        except Exception as e:
            print(f"⚠️ Erreur lors de la résolution client_uuid pour {contact_space_id}: {e}")

        return None

    def get_customer_list(self,user_id, shared_account=False):
        """
        Récupère la liste des noms de clients.
        
        Args:
            shared_account (bool): Si True, filtre uniquement les clients partagés 
                                qui correspondent aux authorized_companies_ids.
        
        Returns:
            list: Une liste des noms de clients.
        """
        
        # Si on veut uniquement les clients partagés
        if shared_account:
            return self.get_shared_customers_list(user_id)
        
        # Sinon, logique standard pour tous les clients
        base_path = f'clients/{user_id}/bo_clients'
        exclude_path = f'{base_path}/{user_id}'
        
        clients_ref = self.db.collection(base_path)
        docs = clients_ref.stream()
        
        # Exclure le client correspondant à `exclude_path`
        client_list = [
            doc.to_dict().get('client_name')
            for doc in docs
            if exclude_path is None or doc.reference.path != exclude_path
        ]
        return client_list


    def get_shared_customers_list(self,user_id):
        """
        Récupère la liste des noms de clients partagés en utilisant authorized_companies_ids.
        
        Returns:
            list: Une liste des noms de clients partagés.
        """
        
        # Récupérer d'abord la liste des authorized_companies_ids depuis users/{user_id}
        user_doc = self.db.document(f'users/{user_id}').get()
        if not user_doc.exists:
            return []
        
        user_data = user_doc.to_dict()
        
        # Vérifier si l'utilisateur est invité et extraire les authorized_companies_ids
        authorized_companies = []
        if 'share_settings' in user_data:
            share_settings = user_data.get('share_settings', {})
            
            # Structure avec des comptes
            if 'accounts' in share_settings:
                for account_id, account_data in share_settings['accounts'].items():
                    if 'companies' in account_data and isinstance(account_data['companies'], list):
                        authorized_companies.extend(account_data['companies'])
            
            # Ancienne structure pour rétrocompatibilité
            elif 'company_id' in share_settings:
                company_id = share_settings.get('company_id', '')
                if company_id:
                    authorized_companies.append(company_id)
        
        if not authorized_companies:
            return []
        
        # Maintenant, récupérer tous les mandats et filtrer par contact_space_id
        mandates_query = self.db.collection_group('mandates')
        results = mandates_query.stream()
        
        shared_clients = []
        
        for doc in results:
            doc_data = doc.to_dict()
            contact_space_id = doc_data.get('contact_space_id', '')
            
            # Vérifier si ce mandat correspond à un espace de contact autorisé
            if contact_space_id in authorized_companies:
                # Récupérer les détails du client
                parent_doc_path = "/".join(doc.reference.path.split("/")[:-2])
                try:
                    parent_doc = self.db.document(parent_doc_path).get()
                    if parent_doc.exists:
                        parent_data = parent_doc.to_dict()
                        client_name = parent_data.get('client_name', '')
                        if client_name:
                            shared_clients.append(client_name)
                except Exception as e:
                    print(f"Erreur lors de la récupération des détails du client: {e}")
        
        # Éliminer les doublons
        return list(set(shared_clients))

    
    
    
    
        
    def create_or_get_working_doc_2(self, mandate_path):
        """
        Crée ou récupère le document 'pending_item_docsheet' dans la collection 'working_doc'
        en utilisant directement le chemin du mandat.
        """
        try:
            mandate_path = self._normalize_mandate_path(mandate_path)
            # Construction du chemin complet pour working_doc
            working_doc_path = f'{mandate_path}/working_doc'
            print(f"Base path pour working_doc: {working_doc_path}")
            
            # Référence au document pending_item_docsheet
            pending_item_doc_ref = self.db.collection(working_doc_path).document('pending_item_docsheet')
            
            # Créer le document s'il n'existe pas
            if not pending_item_doc_ref.get().exists:
                pending_item_doc_ref.set({})
            else:
                print(f"Document 'pending_item_docsheet' déjà existant.")
            
            return pending_item_doc_ref
            
        except Exception as e:
            print(f"Erreur rencontrée: {str(e)}")
            raise
    
    def download_pending_item_docsheet(self, mandate_path):
        """
        Télécharge les données du document 'pending_item_docsheet'.
        """
        try:
            # Obtenir la référence du document 'pending_item_docsheet'
            print(f"impression de contact_space_id:{mandate_path}")
            pending_item_doc_ref = self.create_or_get_working_doc_2(mandate_path)

            # Télécharger le document
            doc = pending_item_doc_ref.get()

            if doc.exists:
                print(f"Document 'pending_item_docsheet' récupéré pour {mandate_path}.")
                return doc.to_dict()
            else:
                print(f"Aucun document 'pending_item_docsheet' trouvé pour {mandate_path}.")
                return {}
        except Exception as e:
            print(f"Erreur lors du téléchargement : {str(e)}")
            return {}

    def get_accounting_context(self, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère le contexte comptable depuis Firebase.
        
        Structure réelle:
        {mandate_path}/context/accounting_context
        └── data: {
            "accounting_context_0": "...",
            "last_refresh": "2025-..."
        }
        
        Args:
            mandate_path: Chemin du mandat (ex: clients/.../mandates/.../data)
        
        Returns:
            Dict contenant:
            - accounting_context_0: Contenu principal
            - last_refresh: Timestamp de la dernière modification
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/accounting_context")
            context_snapshot = context_ref.get()
            
            if context_snapshot.exists:
                data = context_snapshot.to_dict()
                # Structure réelle: le dictionnaire 'data' contient accounting_context_0 et last_refresh
                return data.get('data', {})
            else:
                return {}
        except Exception as e:
            print(f"[Firebase] Erreur get_accounting_context: {e}")
            return {}

    def get_general_context(self, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère le contexte général (profil entreprise) depuis Firebase.
        
        Structure réelle:
        {mandate_path}/context/general_context
        └── context_company_profile_report: "Profil d'entreprise..."
        └── last_refresh: "2025-..."
        
        Args:
            mandate_path: Chemin du mandat
        
        Returns:
            Dict contenant:
            - context_company_profile_report: Description de l'entreprise
            - last_refresh: Timestamp (directement sur le document)
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/general_context")
            context_snapshot = context_ref.get()
            
            if context_snapshot.exists:
                # Les champs sont directement sur le document (pas de dictionnaire 'data')
                return context_snapshot.to_dict()
            else:
                return {}
        except Exception as e:
            print(f"[Firebase] Erreur get_general_context: {e}")
            return {}

    def get_router_context(self, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère le contexte de routage depuis Firebase.
        
        Structure réelle:
        {mandate_path}/context/router_context
        └── router_prompt: {
            "banks_cash": "Prompt pour banks_cash...",
            "contrats": "Prompt pour contrats...",
            "expenses": "Prompt pour expenses...",
            "financial_statement": "...",
            "hr": "...",
            "invoices": "...",
            "letters": "...",
            "taxes": "..."
        }
        └── last_refresh: "2025-..."
        
        Args:
            mandate_path: Chemin du mandat
        
        Returns:
            Dict contenant:
            - router_prompt: Dictionnaire des prompts par service
            - last_refresh: Timestamp
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/router_context")
            context_snapshot = context_ref.get()
            
            if context_snapshot.exists:
                # Les champs sont directement sur le document
                return context_snapshot.to_dict()
            else:
                return {}
        except Exception as e:
            print(f"[Firebase] Erreur get_router_context: {e}")
            return {}

    def get_all_contexts(self, mandate_path: str) -> Dict[str, Any]:
        """
        Récupère tous les contextes (accounting, general, router, bank) en une seule requête.
        
        ⭐ Structure Firebase :
        
        accounting_context/data/accounting_context_0: "TEXTE LONG..."
        bank_context/data/bank_context_0: "TEXTE LONG..."
        general_context/context_company_profile_report: "TEXTE LONG..."
        router_context/router_prompt: {banks_cash: "...", hr: "...", ...}
        
        Args:
            mandate_path: Chemin du mandat
        
        Returns:
            Dict structuré et exploitable directement:
            {
                'accounting': {
                    'accounting_context_0': "TEXTE...",
                    'last_refresh': "..."
                },
                'bank': {
                    'bank_context_0': "TEXTE...",
                    'last_refresh': "..."
                },
                'general': {
                    'context_company_profile_report': "TEXTE...",
                    'last_refresh': "..."
                },
                'router': {
                    'router_prompt': {banks_cash: "...", ...},
                    'last_refresh': "..."
                }
            }
        """
        try:
            context_folder = self.db.collection(f"{mandate_path}/context")
            docs = context_folder.get()
            
            contexts = {}
            for doc in docs:
                doc_id = doc.id
                data = doc.to_dict()
                
                if doc_id == 'accounting_context':
                    # Pour accounting: extraire le dictionnaire 'data' qui contient accounting_context_0
                    contexts['accounting'] = data.get('data', {})
                elif doc_id == 'bank_context':
                    # Pour bank: extraire le dictionnaire 'data' qui contient bank_context_0
                    contexts['bank'] = data.get('data', {})
                elif doc_id == 'general_context':
                    # Pour general: le document entier (context_company_profile_report est direct)
                    contexts['general'] = data
                elif doc_id == 'router_context':
                    # Pour router: le document entier (router_prompt est direct)
                    contexts['router'] = data
            
            return contexts
        except Exception as e:
            print(f"[Firebase] Erreur get_all_contexts: {e}")
            return {}

    def update_bank_context(self, mandate_path: str, updated_content: Dict, additions: Dict = None) -> bool:
        """
        Met à jour le contexte bancaire avec timestamp last_refresh.
        
        ⚠️ STRUCTURE: Le contenu est stocké sous 'data' (comme accounting_context).
        
        Args:
            mandate_path: Chemin du mandat
            updated_content: Contenu mis à jour (remplace 'bank_context_0')
            additions: Champs supplémentaires à ajouter (optional)
        
        Returns:
            bool: True si succès
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/bank_context")
            
            update_data = {
                'data': {
                    'bank_context_0': updated_content,
                    'last_refresh': datetime.now(timezone.utc).isoformat(),
                }
            }
            
            if additions:
                update_data['data'].update(additions)
            
            context_ref.set(update_data, merge=True)
            return True
        except Exception as e:
            print(f"[Firebase] Erreur update_bank_context: {e}")
            return False

    def update_accounting_context(self, mandate_path: str, updated_content: Dict, additions: Dict = None) -> bool:
        """
        Met à jour le contexte comptable avec timestamp last_refresh.
        
        Args:
            mandate_path: Chemin du mandat
            updated_content: Contenu mis à jour (remplace 'accounting_context_0')
            additions: Champs supplémentaires à ajouter (optional)
        
        Returns:
            bool: True si succès
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/accounting_context")
            
            # Construire la donnée à mettre à jour
            update_data = {
                'data': {
                    'accounting_context_0': updated_content,
                    'last_refresh': datetime.now(timezone.utc).isoformat(),
                }
            }
            
            # Ajouter les champs supplémentaires si fournis
            if additions:
                update_data['data'].update(additions)
            
            context_ref.set(update_data, merge=True)
            return True
        except Exception as e:
            print(f"[Firebase] Erreur update_accounting_context: {e}")
            return False

    def update_general_context(self, mandate_path: str, updated_content: Dict, additions: Dict = None) -> bool:
        """
        Met à jour le contexte général avec timestamp last_refresh.
        
        ⚠️ STRUCTURE: Les champs sont DIRECTEMENT sur le document (pas de sous-objet 'data')
        
        Args:
            mandate_path: Chemin du mandat
            updated_content: Contenu mis à jour (remplace 'context_company_profile_report')
            additions: Champs supplémentaires à ajouter (optional)
        
        Returns:
            bool: True si succès
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/general_context")
            
            # ✅ Champs directement sur le document (pas de 'data')
            update_data = {
                'context_company_profile_report': updated_content,
                'last_refresh': datetime.now(timezone.utc).isoformat(),
            }
            
            if additions:
                update_data.update(additions)
            
            context_ref.set(update_data, merge=True)
            return True
        except Exception as e:
            print(f"[Firebase] Erreur update_general_context: {e}")
            return False

    def update_router_context(self, mandate_path: str, updated_content: Dict, additions: Dict = None) -> bool:
        """
        Met à jour le contexte de routage avec timestamp last_refresh.
        
        ⚠️ STRUCTURE: Les champs sont DIRECTEMENT sur le document (pas de sous-objet 'data')
        
        Args:
            mandate_path: Chemin du mandat
            updated_content: Contenu mis à jour (dict avec les prompts par service: {banks_cash: "...", hr: "...", ...})
            additions: Champs supplémentaires (optional)
        
        Returns:
            bool: True si succès
        """
        try:
            context_ref = self.db.document(f"{mandate_path}/context/router_context")
            
            # ✅ Champs directement sur le document (pas de 'data')
            update_data = {
                'router_prompt': updated_content,
                'last_refresh': datetime.now(timezone.utc).isoformat(),
            }
            
            if additions:
                update_data.update(additions)
            
            context_ref.set(update_data, merge=True)
            return True
        except Exception as e:
            print(f"[Firebase] Erreur update_router_context: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════
    # GESTION DES TÂCHES PLANIFIÉES (SCHEDULED TASKS)
    # ═══════════════════════════════════════════════════════════════

    def create_task(self, mandate_path: str, task_data: dict) -> dict:
        """
        Crée une nouvelle tâche planifiée.

        Args:
            mandate_path: Chemin du mandat (ex: "clients/user123/bo_clients/.../mandates/mandate789")
            task_data: Données complètes de la tâche

        Returns:
            {"success": True, "task_id": "task_abc123"}
        """
        try:
            # Générer task_id si absent
            task_id = task_data.get("task_id")
            if not task_id:
                task_id = f"task_{uuid.uuid4().hex[:12]}"
                task_data["task_id"] = task_id

            # Ajouter timestamps
            now = datetime.now(timezone.utc).isoformat()
            task_data["created_at"] = now
            task_data["updated_at"] = now
            task_data["execution_count"] = 0

            # Sauvegarder dans {mandate_path}/tasks/{task_id}
            task_ref = self.db.document(f"{mandate_path}/tasks/{task_id}")
            task_ref.set(task_data)

            logger.info(f"[TASKS] Tâche créée: {task_id}")

            # Si SCHEDULED ou ONE_TIME : sauvegarder dans /scheduled_tasks
            # ON_DEMAND et NOW : pas de scheduler (exécution manuelle ou immédiate)
            execution_plan = task_data.get("execution_plan")
            if execution_plan in ["SCHEDULED", "ONE_TIME"]:
                self._save_task_to_scheduler(mandate_path, task_data)

            return {"success": True, "task_id": task_id}

        except Exception as e:
            logger.error(f"[TASKS] Erreur create_task: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_task(self, mandate_path: str, task_id: str) -> Optional[dict]:
        """Récupère une tâche."""
        try:
            task_ref = self.db.document(f"{mandate_path}/tasks/{task_id}")
            task_doc = task_ref.get()

            if task_doc.exists:
                return task_doc.to_dict()
            return None

        except Exception as e:
            logger.error(f"[TASKS] Erreur get_task: {e}", exc_info=True)
            return None

    def update_task(self, mandate_path: str, task_id: str, updates: dict) -> bool:
        """
        Met à jour une tâche.

        Usage:
            - Mettre à jour next_execution après CRON trigger
            - Mettre à jour last_execution_report après completion
            - Changer status/enabled
        """
        try:
            task_ref = self.db.document(f"{mandate_path}/tasks/{task_id}")

            # Ajouter timestamp de mise à jour
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()

            task_ref.update(updates)
            logger.info(f"[TASKS] Tâche mise à jour: {task_id}")
            
            # ⭐ NOUVEAU : Synchroniser avec scheduled_tasks si la tâche est planifiée
            try:
                # Récupérer les données complètes de la tâche après mise à jour
                task_doc = task_ref.get()
                if task_doc.exists:
                    task_data = task_doc.to_dict()
                    execution_plan = task_data.get("execution_plan")
                    
                    # Si la tâche est planifiée (SCHEDULED ou ONE_TIME)
                    if execution_plan in ["SCHEDULED", "ONE_TIME"]:
                        self._update_scheduler(mandate_path, task_id, task_data, updates)
            except Exception as sync_error:
                # Ne pas échouer toute la mise à jour si la synchronisation échoue
                logger.error(f"[TASKS] ⚠️ Erreur synchronisation scheduler: {sync_error}")
            
            return True

        except Exception as e:
            logger.error(f"[TASKS] Erreur update_task: {e}", exc_info=True)
            return False

    def delete_task(self, mandate_path: str, task_id: str) -> bool:
        """
        Supprime une tâche complètement.

        Actions:
            1. Supprimer document {mandate_path}/tasks/{task_id}
            2. Supprimer sous-collection executions (si existante)
            3. Supprimer de /scheduled_tasks/{job_id}
        """
        try:
            # 1. Supprimer le document principal
            task_ref = self.db.document(f"{mandate_path}/tasks/{task_id}")
            task_ref.delete()

            # 2. Supprimer sous-collection executions
            executions_ref = self.db.collection(f"{mandate_path}/tasks/{task_id}/executions")
            executions = executions_ref.stream()
            for exec_doc in executions:
                exec_doc.reference.delete()

            # 3. Supprimer de /scheduled_tasks
            job_id = f"{mandate_path.replace('/', '_')}_{task_id}"
            self.delete_scheduler_job_completely(job_id)

            logger.info(f"[TASKS] Tâche supprimée: {task_id}")
            return True

        except Exception as e:
            logger.error(f"[TASKS] Erreur delete_task: {e}", exc_info=True)
            return False

    def list_tasks_for_mandate(self, mandate_path: str, status: str = None) -> list:
        """
        Liste toutes les tâches d'un mandat.

        Args:
            mandate_path: Chemin du mandat
            status: Filtrer par status ("active", "paused", "completed") ou None pour toutes

        Returns:
            Liste des tâches
        """
        try:
            mandate_path = self._normalize_mandate_path(mandate_path)
            collection_path = f"{mandate_path}/tasks"
            logger.info(f"[TASKS] list_tasks_for_mandate: Querying collection '{collection_path}'")
            tasks_ref = self.db.collection(collection_path)

            if status:
                query = tasks_ref.where(filter=FieldFilter("status", "==", status))
            else:
                query = tasks_ref

            tasks = []
            for doc in query.stream():
                task_data = doc.to_dict()
                tasks.append(task_data)

            logger.info(f"[TASKS] {len(tasks)} tâches listées pour {mandate_path}")
            return tasks

        except Exception as e:
            logger.error(f"[TASKS] Erreur list_tasks_for_mandate: {e}", exc_info=True)
            return []

    def _save_task_to_scheduler(self, mandate_path: str, task_data: dict):
        """Sauvegarde une tâche dans /scheduled_tasks pour le CRON."""
        try:
            task_id = task_data["task_id"]
            job_id = f"{mandate_path.replace('/', '_')}_{task_id}"

            schedule = task_data.get("schedule", {})
            
            # ⭐ VALIDATION DE SÉCURITÉ : Vérifier que next_execution_utc n'est pas vide
            next_execution_utc = schedule.get("next_execution_utc")
            next_execution_local = schedule.get("next_execution_local_time")
            
            if not next_execution_utc or not next_execution_local:
                logger.warning(
                    f"[TASKS] ⚠️ next_execution vide pour task {task_id} - "
                    f"next_execution_utc='{next_execution_utc}', next_execution_local='{next_execution_local}'. "
                    f"Tâche NON ajoutée au scheduler (sera ignorée par le cron job)."
                )
                return

            scheduler_data = {
                "mandate_path": mandate_path,
                "task_id": task_id,
                "job_type": "scheduled_task",
                "cron_expression": schedule.get("cron_expression"),
                "timezone": schedule.get("timezone"),
                "next_execution_utc": next_execution_utc,
                "next_execution_local_time": next_execution_local,
                "enabled": task_data.get("enabled", True),
                "user_id": task_data.get("user_id"),
                "company_id": task_data.get("company_id"),
                "mission_title": task_data.get("mission", {}).get("title"),
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP
            }

            scheduler_ref = self.db.collection("scheduled_tasks").document(job_id)
            scheduler_ref.set(scheduler_data)

            logger.info(f"[TASKS] ✅ Tâche ajoutée au scheduler: {job_id} (next_exec_utc: {next_execution_utc})")

        except Exception as e:
            logger.error(f"[TASKS] ❌ Erreur _save_task_to_scheduler: {e}", exc_info=True)

    def _update_scheduler(self, mandate_path: str, task_id: str, task_data: dict, updates: dict):
        """
        Met à jour le document scheduler après modification d'une tâche.
        
        Args:
            mandate_path: Chemin du mandat
            task_id: ID de la tâche
            task_data: Données complètes de la tâche (après mise à jour)
            updates: Champs modifiés
        """
        try:
            job_id = f"{mandate_path.replace('/', '_')}_{task_id}"
            scheduler_ref = self.db.collection("scheduled_tasks").document(job_id)
            
            # Vérifier si le document scheduler existe
            scheduler_doc = scheduler_ref.get()
            if not scheduler_doc.exists:
                logger.warning(f"[TASKS] ⚠️ Document scheduler {job_id} n'existe pas, pas de mise à jour")
                return
            
            # Construire les mises à jour pour le scheduler
            scheduler_updates = {}
            
            # Récupérer les données actuelles du scheduler pour comparaison
            current_scheduler_data = scheduler_doc.to_dict() or {}
            
            # ⭐ CORRECTION CRITIQUE : Synchroniser le schedule depuis task_data
            # Si next_execution_utc n'est pas dans task_data, utiliser la valeur actuelle du scheduler
            schedule = task_data.get("schedule", {})
            
            # 🔍 DEBUG: Afficher ce que contient schedule pour diagnostiquer
            logger.info(f"[TASKS] 🔍 DEBUG schedule keys: {list(schedule.keys()) if schedule else 'EMPTY'}")
            logger.info(f"[TASKS] 🔍 DEBUG next_execution_utc in schedule: {schedule.get('next_execution_utc', 'NOT_FOUND')}")
            
            # Synchroniser les champs de schedule
            # Priorité: updates["schedule"] > task_data["schedule"] > current_scheduler_data
            updates_schedule = updates.get("schedule", {}) if isinstance(updates.get("schedule"), dict) else {}
            
            # next_execution_utc
            if "next_execution_utc" in updates_schedule:
                scheduler_updates["next_execution_utc"] = updates_schedule["next_execution_utc"]
                logger.info(f"[TASKS] 📝 next_execution_utc mis à jour depuis updates: {updates_schedule['next_execution_utc']}")
            elif "next_execution_utc" in schedule and schedule["next_execution_utc"]:
                scheduler_updates["next_execution_utc"] = schedule["next_execution_utc"]
            # Sinon, on garde la valeur actuelle (pas dans scheduler_updates = pas de modification)
            
            # next_execution_local_time
            if "next_execution_local_time" in updates_schedule:
                scheduler_updates["next_execution_local_time"] = updates_schedule["next_execution_local_time"]
                logger.info(f"[TASKS] 📝 next_execution_local_time mis à jour depuis updates: {updates_schedule['next_execution_local_time']}")
            elif "next_execution_local_time" in schedule and schedule["next_execution_local_time"]:
                scheduler_updates["next_execution_local_time"] = schedule["next_execution_local_time"]
            
            # cron_expression
            if "cron_expression" in updates_schedule:
                scheduler_updates["cron_expression"] = updates_schedule["cron_expression"]
            elif "cron_expression" in schedule:
                scheduler_updates["cron_expression"] = schedule["cron_expression"]
            
            # timezone
            if "timezone" in updates_schedule:
                scheduler_updates["timezone"] = updates_schedule["timezone"]
            elif "timezone" in schedule:
                scheduler_updates["timezone"] = schedule["timezone"]
            
            # Si enabled a été modifié
            if "enabled" in updates:
                scheduler_updates["enabled"] = updates["enabled"]
            elif "enabled" in task_data:
                # Sinon prendre la valeur actuelle pour garantir la cohérence
                scheduler_updates["enabled"] = task_data["enabled"]
            
            # Si mission.title a été modifié
            if "mission" in updates and isinstance(updates["mission"], dict):
                if "title" in updates["mission"]:
                    scheduler_updates["mission_title"] = updates["mission"]["title"]
            elif "mission" in task_data and isinstance(task_data["mission"], dict):
                # Sinon prendre la valeur actuelle
                if "title" in task_data["mission"]:
                    scheduler_updates["mission_title"] = task_data["mission"]["title"]
            
            # Si des mises à jour sont nécessaires
            if scheduler_updates:
                scheduler_updates["updated_at"] = firestore.SERVER_TIMESTAMP
                scheduler_ref.update(scheduler_updates)
                logger.info(f"[TASKS] ✅ Scheduler mis à jour: {job_id} - champs: {list(scheduler_updates.keys())}")
            else:
                logger.info(f"[TASKS] ℹ️ Aucune mise à jour scheduler nécessaire pour {job_id}")
        
        except Exception as e:
            logger.error(f"[TASKS] ❌ Erreur _update_scheduler: {e}", exc_info=True)

    # ═══════════════════════════════════════════════════════════════
    # GESTION DES EXÉCUTIONS DE TÂCHES
    # ═══════════════════════════════════════════════════════════════

    def create_task_execution(self, mandate_path: str, task_id: str, execution_data: dict) -> str:
        """
        Crée un document d'exécution temporaire.

        Args:
            mandate_path: Chemin du mandat
            task_id: ID de la tâche
            execution_data: Données initiales

        Returns:
            execution_id
        """
        try:
            execution_id = execution_data.get("execution_id")
            if not execution_id:
                execution_id = f"exec_{uuid.uuid4().hex[:12]}"
                execution_data["execution_id"] = execution_id

            exec_ref = self.db.document(f"{mandate_path}/tasks/{task_id}/executions/{execution_id}")
            exec_ref.set(execution_data)

            logger.info(f"[TASKS] Exécution créée: {execution_id}")
            return execution_id

        except Exception as e:
            logger.error(f"[TASKS] Erreur create_task_execution: {e}", exc_info=True)
            return ""

    def update_task_execution(self, mandate_path: str, task_id: str, execution_id: str, updates: dict) -> bool:
        """
        Met à jour une exécution.

        Usage:
            - Mettre à jour workflow_checklist
            - Ajouter/mettre à jour lpt_tasks
            - Changer status
        """
        try:
            exec_ref = self.db.document(f"{mandate_path}/tasks/{task_id}/executions/{execution_id}")
            exec_ref.update(updates)

            logger.info(f"[TASKS] Exécution mise à jour: {execution_id}")
            return True

        except Exception as e:
            logger.error(f"[TASKS] Erreur update_task_execution: {e}", exc_info=True)
            return False

    def get_task_execution(self, mandate_path: str, task_id: str, execution_id: str) -> Optional[dict]:
        """Récupère les données d'une exécution."""
        try:
            exec_ref = self.db.document(f"{mandate_path}/tasks/{task_id}/executions/{execution_id}")
            exec_doc = exec_ref.get()

            if exec_doc.exists:
                return exec_doc.to_dict()
            return None

        except Exception as e:
            logger.error(f"[TASKS] Erreur get_task_execution: {e}", exc_info=True)
            return None

    def complete_task_execution(self, mandate_path: str, task_id: str, execution_id: str, final_report: dict) -> bool:
        """
        Finalise une exécution.

        Actions:
            1. Sauvegarder final_report dans task_id.last_execution_report
            2. Incrémenter execution_count
            3. Marquer l'exécution comme "completed" (sans supprimer pour permettre les callbacks LPT)
        """
        try:
            # 1. Sauvegarder rapport dans la tâche
            task_ref = self.db.document(f"{mandate_path}/tasks/{task_id}")
            task_doc = task_ref.get()

            if task_doc.exists:
                task_data = task_doc.to_dict()
                execution_count = task_data.get("execution_count", 0) + 1

                task_ref.update({
                    "last_execution_report": final_report,
                    "execution_count": execution_count,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })

            # 2. Marquer l'exécution comme "completed" (sans supprimer pour permettre les callbacks LPT)
            exec_ref = self.db.document(f"{mandate_path}/tasks/{task_id}/executions/{execution_id}")
            exec_ref.update({
                "status": final_report.get("status", "completed"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "final_report": final_report
            })

            logger.info(f"[TASKS] Exécution finalisée: {execution_id} (conservée pour callbacks LPT)")
            return True

        except Exception as e:
            logger.error(f"[TASKS] Erreur complete_task_execution: {e}", exc_info=True)
            return False

    def cleanup_completed_executions(self, mandate_path: str, task_id: str) -> int:
        """
        Supprime toutes les exécutions terminées pour une tâche.

        Returns:
            Nombre de documents supprimés
        """
        try:
            executions_ref = self.db.collection(f"{mandate_path}/tasks/{task_id}/executions")
            executions = executions_ref.where(filter=FieldFilter("status", "in", ["completed", "failed"])).stream()

            count = 0
            for exec_doc in executions:
                exec_doc.reference.delete()
                count += 1

            logger.info(f"[TASKS] {count} exécutions nettoyées pour {task_id}")
            return count

        except Exception as e:
            logger.error(f"[TASKS] Erreur cleanup_completed_executions: {e}", exc_info=True)
            return 0

    # ═══════════════════════════════════════════════════════════════
    # GESTION TIMEZONE & CRON
    # ═══════════════════════════════════════════════════════════════

    def get_timezone_from_mandate(self, mandate_path: str) -> Optional[str]:
        """
        Récupère la timezone sauvegardée dans le mandat.

        Args:
            mandate_path: Chemin du mandat

        Returns:
            Timezone (ex: "Europe/Zurich") ou None si non définie
        """
        try:
            mandate_ref = self.db.document(mandate_path)
            mandate_doc = mandate_ref.get()

            if mandate_doc.exists:
                mandate_data = mandate_doc.to_dict()
                return mandate_data.get("timezone")
            return None

        except Exception as e:
            logger.error(f"[TASKS] Erreur get_timezone_from_mandate: {e}", exc_info=True)
            return None

    def save_timezone_to_mandate(self, mandate_path: str, timezone_str: str) -> bool:
        """
        Sauvegarde la timezone dans le mandat pour réutilisation future.

        Args:
            mandate_path: Chemin du mandat
            timezone_str: Timezone (ex: "Europe/Zurich")

        Returns:
            True si succès
        """
        try:
            mandate_ref = self.db.document(mandate_path)
            mandate_ref.update({
                "timezone": timezone_str,
                "timezone_updated_at": datetime.now(timezone.utc).isoformat()
            })

            logger.info(f"[TASKS] Timezone sauvegardée: {timezone_str} pour {mandate_path}")
            return True

        except Exception as e:
            logger.error(f"[TASKS] Erreur save_timezone_to_mandate: {e}", exc_info=True)
            return False

    def build_task_cron_expression(self, frequency: str, time_str: str, day_of_week: str = None, day_of_month: int = None) -> str:
        """
        Construit une expression CRON.

        Args:
            frequency: "daily" | "weekly" | "monthly"
            time_str: "HH:MM" (ex: "03:00")
            day_of_week: "MON" | "TUE" | ... (pour weekly)
            day_of_month: 1-31 (pour monthly)

        Returns:
            Expression CRON (ex: "0 3 1 * *")
        """
        try:
            # Parser l'heure
            hour, minute = time_str.split(":")
            hour = int(hour)
            minute = int(minute)

            if frequency == "daily":
                return f"{minute} {hour} * * *"

            elif frequency == "weekly":
                day_map = {
                    "MON": 1, "TUE": 2, "WED": 3, "THU": 4,
                    "FRI": 5, "SAT": 6, "SUN": 0
                }
                day_num = day_map.get(day_of_week, 1)
                return f"{minute} {hour} * * {day_num}"

            elif frequency == "monthly":
                day = day_of_month or 1
                return f"{minute} {hour} {day} * *"

            else:
                raise ValueError(f"Fréquence non supportée: {frequency}")

        except Exception as e:
            logger.error(f"[TASKS] Erreur build_task_cron_expression: {e}", exc_info=True)
            return ""

    def calculate_task_next_execution(self, cron_expr: str, timezone_str: str, from_time: Optional[datetime] = None) -> tuple:
        """
        Calcule la prochaine exécution en local_time et UTC.

        Args:
            cron_expr: Expression CRON
            timezone_str: Timezone (ex: "Europe/Zurich")
            from_time: Point de départ (défaut: maintenant)

        Returns:
            (next_execution_local_time_iso, next_execution_utc_iso)
        """
        try:
            import pytz
            from croniter import croniter

            # Timezone
            tz = pytz.timezone(timezone_str)

            # Point de départ
            if from_time is None:
                base_time = datetime.now(tz)
            else:
                # Convertir from_time en timezone locale
                if from_time.tzinfo is None:
                    from_time = pytz.utc.localize(from_time)
                base_time = from_time.astimezone(tz)

            # Calculer prochaine exécution en heure locale
            cron = croniter(cron_expr, base_time)
            next_local = cron.get_next(datetime)

            # Convertir en UTC
            next_utc = next_local.astimezone(pytz.utc)

            return (next_local.isoformat(), next_utc.isoformat())

        except Exception as e:
            logger.error(f"[TASKS] ❌ Erreur calculate_task_next_execution - cron_expr='{cron_expr}', timezone_str='{timezone_str}': {e}", exc_info=True)
            return ("", "")

    def get_tasks_ready_for_execution_utc(self, current_time_utc: datetime) -> list:
        """
        Retourne les tâches dont next_execution_utc <= current_time_utc et enabled=True.

        Args:
            current_time_utc: Timestamp UTC actuel

        Returns:
            Liste des tâches complètes (depuis mandate_path/tasks/{task_id})
        """
        try:
            # Query /scheduled_tasks avec enabled=True
            scheduler_ref = self.db.collection("scheduled_tasks")
            query = scheduler_ref.where(filter=FieldFilter("enabled", "==", True))

            tasks_ready = []

            for doc in query.stream():
                scheduler_data = doc.to_dict()
                next_execution_utc_str = scheduler_data.get("next_execution_utc")

                if not next_execution_utc_str:
                    continue

                # Parser next_execution_utc
                try:
                    from dateutil import parser
                    next_exec_utc = parser.isoparse(next_execution_utc_str)

                    # Comparer avec current_time_utc
                    if next_exec_utc <= current_time_utc:
                        # Charger les données complètes depuis mandate_path/tasks/{task_id}
                        mandate_path = scheduler_data.get("mandate_path")
                        task_id = scheduler_data.get("task_id")

                        if mandate_path and task_id:
                            task_data = self.get_task(mandate_path, task_id)
                            if task_data:
                                tasks_ready.append(task_data)

                except Exception as parse_error:
                    logger.error(f"[TASKS] Erreur parsing next_execution_utc: {parse_error}")
                    continue

            logger.info(f"[TASKS] {len(tasks_ready)} tâches prêtes pour exécution")
            return tasks_ready

        except Exception as e:
            logger.error(f"[TASKS] Erreur get_tasks_ready_for_execution_utc: {e}", exc_info=True)
            return []

    # ========== Approval Pending List Management ==========

    def save_approval_item_changes(
        self,
        mandate_path: str,
        item_id: str,
        changes: dict,
        user_id: str = None
    ) -> bool:
        """
        Sauvegarde les modifications locales d'un item d'approbation dans Firebase.
        Utilisé pour persister les sélections utilisateur (service, année, instructions)
        sans envoyer au jobbeur.

        Args:
            mandate_path: Chemin du mandat (ex: "bo_clients/xxx/mandates/yyy")
            item_id: ID de l'item (ex: "router_abc123")
            changes: Dict des modifications à appliquer
                - selected_service: Service sélectionné
                - selected_fiscal_year: Année fiscale sélectionnée
                - instructions: Instructions additionnelles
            user_id: ID de l'utilisateur (pour audit)

        Returns:
            bool: True si sauvegarde réussie
        """
        try:
            # Construire le chemin du document
            doc_path = f"{mandate_path}/approval_pendinglist/{item_id}"
            logger.info(f"[APPROVAL] Saving changes to {doc_path}: {changes}")

            doc_ref = self.db.document(doc_path)
            doc = doc_ref.get()

            if not doc.exists:
                logger.warning(f"[APPROVAL] Item not found: {doc_path}")
                return False

            # Préparer les données à mettre à jour
            update_data = {}

            if "selected_service" in changes:
                update_data["selected_service"] = changes["selected_service"]
            if "selected_fiscal_year" in changes:
                update_data["selected_fiscal_year"] = changes["selected_fiscal_year"]
            if "instructions" in changes:
                update_data["instructions"] = changes["instructions"]

            # Ajouter métadonnées d'audit
            update_data["last_modified_by"] = user_id
            update_data["last_modified_at"] = firestore.SERVER_TIMESTAMP

            # Mettre à jour le document
            doc_ref.update(update_data)

            logger.info(f"[APPROVAL] Changes saved successfully: {doc_path}")
            return True

        except Exception as e:
            logger.error(f"[APPROVAL] Error saving changes: {e}", exc_info=True)
            return False

    def process_router_approval(
        self,
        mandate_path: str,
        item_id: str,
        selected_service: str,
        selected_fiscal_year: str,
        user_id: str,
        instructions: str = None
    ) -> bool:
        """
        Traite l'approbation d'un item Router.

        1. Charge l'item depuis approval_pendinglist
        2. Crée une notification avec approval_response_mode: true
        3. Supprime l'item de approval_pendinglist
        4. Publie sur Redis pour mise à jour temps réel

        Args:
            mandate_path: Chemin du mandat
            item_id: ID de l'item (ex: "router_abc123")
            selected_service: Service sélectionné par l'utilisateur
            selected_fiscal_year: Année fiscale sélectionnée
            user_id: ID de l'utilisateur Firebase
            instructions: Instructions additionnelles (optionnel)

        Returns:
            bool: True si traitement réussi
        """
        import uuid
        from datetime import datetime, timezone

        try:
            # 1. Charger l'item depuis approval_pendinglist
            pending_path = f"{mandate_path}/approval_pendinglist/{item_id}"
            logger.info(f"[APPROVAL] Processing router approval: {pending_path}")

            doc_ref = self.db.document(pending_path)
            doc = doc_ref.get()

            if not doc.exists:
                logger.error(f"[APPROVAL] Item not found: {pending_path}")
                return False

            item_data = doc.to_dict()
            context_payload = item_data.get("context_payload", {})

            # Extraire les informations nécessaires
            drive_file_id = context_payload.get("drive_file_id", "")
            file_name = context_payload.get("file_name", "") or item_data.get("file_name", "")

            # Extraire company_id depuis mandate_path (format: bo_clients/{client}/mandates/{company})
            path_parts = mandate_path.split("/")
            company_id = path_parts[-1] if len(path_parts) >= 4 else ""
            client_uuid = path_parts[1] if len(path_parts) >= 2 else ""

            # 2. Créer la notification avec approval_response_mode
            batch_id = f"approval_batch_{uuid.uuid4().hex[:10]}"
            notification_path = f"clients/{user_id}/notifications"

            notification_data = {
                "job_id": item_id,
                "file_id": drive_file_id,
                "file_name": file_name,
                "function_name": "Router",
                "status": "in_queue",
                "read": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "collection_id": company_id,
                "collection_name": company_id,
                "batch_id": batch_id,
                "approval_response_mode": True,  # ← Clé importante pour le jobbeur
                "approval_status": "approved",
                "selected_service": selected_service,
                "selected_fiscal_year": selected_fiscal_year,
                "instructions": instructions or "",
                "user_id": user_id,
                "client_uuid": client_uuid,
                "mandates_path": mandate_path,
            }

            # Ajouter la notification
            notifications_ref = self.db.collection(notification_path)
            notifications_ref.document(item_id).set(notification_data)
            logger.info(f"[APPROVAL] Notification created: {notification_path}/{item_id}")

            # 3. Supprimer l'item de approval_pendinglist
            doc_ref.delete()
            logger.info(f"[APPROVAL] Item deleted from pendinglist: {pending_path}")

            # 4. Supprimer aussi le contexte d'approbation s'il existe
            context_path = f"{mandate_path}/approval_context/{item_id}"
            try:
                context_ref = self.db.document(context_path)
                if context_ref.get().exists:
                    context_ref.delete()
                    logger.info(f"[APPROVAL] Context deleted: {context_path}")
            except Exception:
                pass  # Le contexte peut ne pas exister

            # 5. Publier sur Redis pour mise à jour temps réel (action: remove)
            self._publish_pending_approval_redis(
                user_id=user_id,
                company_id=company_id,
                item_id=item_id,
                action="remove"
            )

            logger.info(f"[APPROVAL] Router approval processed successfully: {item_id}")
            return True

        except Exception as e:
            logger.error(f"[APPROVAL] Error processing router approval: {e}", exc_info=True)
            return False

    def process_router_rejection(
        self,
        mandate_path: str,
        item_id: str,
        rejection_reason: str,
        instructions: str = None,
        close: bool = False,
        user_id: str = None
    ) -> bool:
        """
        Traite le rejet d'un item Router.

        Si close=True: Supprime l'item (ferme définitivement)
        Si close=False: Met à jour le statut en "rejected" (peut être réouvert)

        Args:
            mandate_path: Chemin du mandat
            item_id: ID de l'item
            rejection_reason: Raison du rejet
            instructions: Instructions additionnelles
            close: Si True, supprime l'item définitivement
            user_id: ID de l'utilisateur

        Returns:
            bool: True si traitement réussi
        """
        from datetime import datetime, timezone

        try:
            pending_path = f"{mandate_path}/approval_pendinglist/{item_id}"
            logger.info(f"[APPROVAL] Processing router rejection: {pending_path}, close={close}")

            doc_ref = self.db.document(pending_path)
            doc = doc_ref.get()

            if not doc.exists:
                logger.error(f"[APPROVAL] Item not found: {pending_path}")
                return False

            item_data = doc.to_dict()

            # Extraire company_id
            path_parts = mandate_path.split("/")
            company_id = path_parts[-1] if len(path_parts) >= 4 else ""

            if close:
                # Supprimer définitivement
                doc_ref.delete()
                logger.info(f"[APPROVAL] Item deleted (closed): {pending_path}")

                # Supprimer aussi le contexte
                context_path = f"{mandate_path}/approval_context/{item_id}"
                try:
                    context_ref = self.db.document(context_path)
                    if context_ref.get().exists:
                        context_ref.delete()
                except Exception:
                    pass

                # Publier sur Redis (action: remove)
                self._publish_pending_approval_redis(
                    user_id=user_id,
                    company_id=company_id,
                    item_id=item_id,
                    action="remove"
                )
            else:
                # Mettre à jour le statut
                update_data = {
                    "status": "rejected",
                    "rejection_reason": rejection_reason,
                    "instructions": instructions or "",
                    "rejected_by": user_id,
                    "rejected_at": datetime.now(timezone.utc).isoformat(),
                }
                doc_ref.update(update_data)
                logger.info(f"[APPROVAL] Item marked as rejected: {pending_path}")

                # Publier sur Redis (action: update)
                self._publish_pending_approval_redis(
                    user_id=user_id,
                    company_id=company_id,
                    item_id=item_id,
                    action="update",
                    data={"status": "rejected", "rejection_reason": rejection_reason}
                )

            logger.info(f"[APPROVAL] Router rejection processed successfully: {item_id}")
            return True

        except Exception as e:
            logger.error(f"[APPROVAL] Error processing router rejection: {e}", exc_info=True)
            return False

    def _publish_pending_approval_redis(
        self,
        user_id: str,
        company_id: str,
        item_id: str,
        action: str,
        data: dict = None
    ):
        """
        Publie une mise à jour pending_approval sur Redis.

        Args:
            user_id: ID utilisateur Firebase
            company_id: ID de la société
            item_id: ID de l'item
            action: "add" | "update" | "remove"
            data: Données additionnelles (optionnel)
        """
        try:
            import json
            from .redis_client import get_redis

            redis = get_redis()
            channel = f"user:{user_id}/pending_approval"

            from datetime import datetime, timezone as tz

            payload = {
                "type": f"pending_approval_{'deleted' if action == 'remove' else 'updated' if action == 'update' else 'created'}",
                "action": action,
                "department": "routing",
                "job_id": item_id,
                "company_id": company_id,
                "data": data or {"id": item_id},
                "timestamp": datetime.now(tz.utc).isoformat(),
            }

            redis.publish(channel, json.dumps(payload, default=str))
            logger.info(f"[REDIS] Published pending_approval to {channel}: action={action}")

        except Exception as e:
            logger.warning(f"[REDIS] Error publishing pending_approval: {e}")


class FirebaseRealtimeChat:
    """
    Gestionnaire Firebase Realtime basé sur un singleton thread-safe.
    Une seule instance est créée et réutilisée dans toute l'application.

    Important: NE PAS renommer la classe ni les accesseurs ci-dessous.
    Collez vos méthodes métier dans la zone PASTE ZONE.
    Mapping RPC: "FIREBASE_REALTIME.*"
    """

    _instance: Optional["FirebaseRealtimeChat"] = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self.__class__._initialized:
            return
        self.__class__._initialized = True

        self.processed_messages = set()
        self.current_listener = None
        self.should_stop = False

        database_url = os.getenv(
            "FIREBASE_REALTIME_DB_URL",
            "https://pinnokio-gpt-default-rtdb.europe-west1.firebasedatabase.app/",
        )

        try:
            app = get_firebase_app()
            if rtdb is None:
                raise RuntimeError("firebase_admin.db indisponible")
            self.db = rtdb.reference("/", url=database_url, app=app)
        except Exception as e:
            print(f"Erreur lors de l'initialisation de Firebase: {e}")
            import traceback
            traceback.print_exc()
            raise

    def delete_space(self, space_id: str) -> bool:
        """
        Supprime un espace (nœud racine) dans Firebase Realtime DB.
        
        Args:
            space_id (str): Nom de l'espace (clé racine) à supprimer
        
        Returns:
            bool: True si succès, False sinon
        """
        try:
            # référence vers /space_id
            ref = self.db.child(space_id)
            ref.delete()
            print(f"✅ Espace {space_id} supprimé de Firebase Realtime")
            return True
        except Exception as e:
            print(f"❌ Erreur lors de la suppression de l'espace {space_id}: {e}")
            return False

    
    def listen_direct_messages(self, user_id: str, callback) -> Any:
        """
        Configure un écouteur pour les messages directs d'un utilisateur.
        
        Args:
            user_id (str): ID de l'utilisateur
            callback: Fonction asynchrone à appeler lors de la réception d'un message
            
        Returns:
            Any: Référence à l'écouteur pour pouvoir l'arrêter plus tard
        """
        try:
            print(f"🚀 Démarrage de l'écoute des messages directs pour l'utilisateur {user_id}")
            
            # Chemin des messages directs en respectant votre structure
            messages_path = f"clients/{user_id}/direct_message_notif"
            
            try:
                # Créer la référence aux messages
                messages_ref = self.db.child(messages_path)
            except Exception as e:
                print(f"❌ Erreur lors de l'accès à la référence: {str(e)}")
                self._reinitialize_firebase_connection()
                messages_ref = self.db.child(messages_path)
            
            # Obtenir la boucle principale
            main_loop = asyncio.get_event_loop()
            
            # Ensemble pour suivre les messages déjà traités
            processed_messages = set()
            
            # Handler pour les événements
            def on_message(event):
                try:
                    print(f"\n📨 Événement de message direct reçu:")
                    #print(f"  Type: {event.event_type}")
                    #print(f"  Chemin: {event.path}")
                    #print(f"  Data: {event.data}")
                    
                    if event.data and event.event_type == 'put':
                        # S'assurer que c'est un nouveau message (pas une opération sur la racine)
                        if event.path != '/' and isinstance(event.data, dict):
                            message_id = event.path.lstrip('/')
                            
                            # Vérifier si le message a déjà été traité
                            if message_id in processed_messages:
                                print(f"⏭️ Message {message_id} déjà traité, ignoré")
                                return
                                
                            processed_messages.add(message_id)
                            
                            # Ajouter l'ID au message
                            message_data = {
                                'doc_id': message_id,
                                **event.data
                            }
                            
                            try:
                                # Planifier le callback asynchrone
                                future = asyncio.run_coroutine_threadsafe(
                                    callback(message_data),
                                    main_loop
                                )
                                # Attendre avec timeout pour ne pas bloquer
                                future.result(timeout=1)
                            except asyncio.TimeoutError:
                                print("⚠️ Callback timeout - continuer en arrière-plan")
                            except Exception as e:
                                print(f"❌ Erreur dans le callback: {str(e)}")
                                import traceback
                                traceback.print_exc()
                except Exception as e:
                    print(f"❌ Erreur dans le handler on_message: {str(e)}")
                    import traceback
                    traceback.print_exc()
            
            # Configurer l'écouteur
            try:
                listener = messages_ref.listen(on_message)
                print("✅ Écouteur de messages directs configuré et actif")
                
                # Créer un objet écouteur avec une méthode close pour faciliter le nettoyage
                class ListenerWrapper:
                    def __init__(self, listener_ref):
                        self.listener_ref = listener_ref
                        self.processed_ids = processed_messages
                    
                    def close(self):
                        try:
                            if hasattr(self.listener_ref, 'close'):
                                self.listener_ref.close()
                            elif callable(self.listener_ref):
                                self.listener_ref()  # Pour les écouteurs Firebase qui sont arrêtés en appelant la fonction
                            print("✅ Écouteur Firebase fermé proprement")
                        except Exception as e:
                            print(f"⚠️ Erreur lors de la fermeture de l'écouteur: {e}")
                
                return ListenerWrapper(listener)
                
            except AttributeError as e:
                print(f"❌ Erreur AttributeError lors de la configuration de l'écouteur: {str(e)}")
                # Alternative en cas d'erreur avec la session authentifiée
                try:
                    app = firebase_admin.get_app()
                    db_ref = firebase_admin.db.reference(messages_path, app=app)
                    
                    # Pour Firebase Admin SDK, l'API listen est différente
                    def admin_on_message(event):
                        try:
                            data = event.data
                            path = event.path
                            
                            if data and path != '/':
                                message_id = path.lstrip('/')
                                
                                # Vérifier si le message a déjà été traité
                                if message_id in processed_messages:
                                    return
                                    
                                processed_messages.add(message_id)
                                
                                message_data = {
                                    'doc_id': message_id,
                                    **data
                                }
                                asyncio.run_coroutine_threadsafe(
                                    callback(message_data),
                                    main_loop
                                )
                        except Exception as e:
                            print(f"❌ Erreur dans admin_on_message: {str(e)}")
                    
                    listener = db_ref.listen(admin_on_message)
                    print("✅ Écouteur de messages directs configuré et actif (méthode alternative)")
                    
                    # Utiliser le même wrapper pour la cohérence
                    return ListenerWrapper(listener)
                    
                except Exception as alt_e:
                    print(f"❌ Erreur avec méthode alternative: {str(alt_e)}")
                    raise
            except Exception as e:
                print(f"❌ Erreur lors de la configuration de l'écouteur: {str(e)}")
                raise
                
        except Exception as e:
            print(f"❌ Erreur lors de la configuration de l'écouteur de messages directs: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    
    def send_direct_message(self,user_id: str, recipient_id: str, message_data: dict):
        """
        Envoie un message direct à un utilisateur via Realtime Database.
        
        Args:
            user_id (str): ID de l'expéditeur
            recipient_id (str): ID du destinataire
            message_data (dict): Données du message
            
        Returns:
            str | None: ID du message créé si succès, None sinon
        """
        try:
            # Créer la référence au nœud des messages directs du destinataire
            direct_messages_path = f"clients/{recipient_id}/direct_message_notif"
            messages_ref = self.db.child(direct_messages_path)
            
            # Ajouter des informations par défaut si nécessaires
            if 'sender_id' not in message_data:
                message_data['sender_id'] = user_id
                
            if 'timestamp' not in message_data:
                message_data['timestamp'] = datetime.now(timezone.utc).isoformat()
            
            # Envoyer le message et récupérer la référence
            new_message_ref = messages_ref.push(message_data)
            
            # Extraire l'ID du message créé
            message_id = new_message_ref.key
            
            logger.info(f"✅ Message direct envoyé à {recipient_id} - message_id={message_id}")
            
            # Publier sur Redis PubSub pour mise à jour temps réel
            try:
                import asyncio
                from app.realtime.pubsub_helper import publish_messenger_new
                
                # Transformer message_data en format pour le frontend
                messenger_data = {
                    "docId": message_id,
                    "message": message_data.get('message', ''),
                    "fileName": message_data.get('file_name', ''),
                    "collectionId": message_data.get('collection_id', ''),
                    "collectionName": message_data.get('collection_name', ''),
                    "status": message_data.get('status', ''),
                    "jobId": message_data.get('job_id', ''),
                    "fileId": message_data.get('file_id', ''),
                    "functionName": message_data.get('function_name', 'Chat'),
                    "timestamp": message_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                    "additionalInfo": message_data.get('additional_info', ''),
                    "chatMode": message_data.get('chat_mode', ''),
                    "threadKey": message_data.get('thread_key', ''),
                    "driveLink": message_data.get('drive_link', ''),
                    "batchId": message_data.get('batch_id', ''),
                }
                
                # Publier de manière asynchrone
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(
                            publish_messenger_new(recipient_id, messenger_data)
                        )
                    else:
                        loop.run_until_complete(
                            publish_messenger_new(recipient_id, messenger_data)
                        )
                except RuntimeError:
                    asyncio.run(
                        publish_messenger_new(recipient_id, messenger_data)
                    )
                
                logger.info(f"✅ Message published via PubSub Redis for user {recipient_id}")
            except Exception as pubsub_error:
                logger.warning(f"⚠️ Failed to publish message via PubSub: {pubsub_error}")
            
            return message_id
            
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'envoi du message direct: {e}", exc_info=True)
            return None

    def delete_direct_message(self, user_id: str, message_id: str) -> bool:
        """
        Supprime un message direct après lecture.
        
        Args:
            user_id (str): ID de l'utilisateur
            message_id (str): ID du message à supprimer
            
        Returns:
            bool: True si la suppression a réussi
        """
        try:
            logger.info(f"🗑️ Suppression du message direct {message_id} pour l'utilisateur {user_id}")
            
            # Chemin du message
            message_path = f"clients/{user_id}/direct_message_notif/{message_id}"
            
            # Supprimer le message
            message_ref = self.db.child(message_path)
            message_ref.delete()
            logger.info(f"✅ Message {message_id} supprimé avec succès")
            return True
                
        except Exception as e:
            logger.error(
                f"❌ Erreur lors de la suppression du message direct: {type(e).__name__}: {str(e)}",
                exc_info=True
            )
            return False
    	
    def get_unread_direct_messages(self, user_id: str, companies: List[Dict] = None) -> List[Dict]:
        """
        Récupère les messages directs non lus pour un utilisateur,
        filtrés selon les entreprises auxquelles il a accès.
        
        Args:
            user_id (str): ID de l'utilisateur
            companies (List[Dict], optional): Liste des entreprises auxquelles l'utilisateur a accès
        
        Returns:
            List[Dict]: Liste des messages directs non lus filtrés
        """
        try:
            #print(f"📨 Récupération des messages directs non lus pour l'utilisateur: {user_id}")
            
            # Chemin vers les messages directs de l'utilisateur
            messages_path = f"clients/{user_id}/direct_message_notif"
            #print(f"📍 Chemin d'accès: {messages_path}")
            
            # Récupérer les messages avec gestion des erreurs
            try:
                messages_ref = self.db.child(messages_path)
                messages_data = messages_ref.get()
            except AttributeError as ae:
                print(f"⚠️ Erreur d'attribut lors de la récupération des messages: {ae}")
                # Réinitialiser la connexion Firebase et réessayer
                self._reinitialize_firebase_connection()
                # Utiliser firebase_admin.db.reference directement
                messages_ref = firebase_admin.db.reference(messages_path)
                messages_data = messages_ref.get()
            except Exception as e:
                print(f"⚠️ Erreur générale lors de la récupération des messages: {e}")
                # Réinitialiser la connexion Firebase et réessayer
                self._reinitialize_firebase_connection()
                messages_ref = self.db.child(messages_path)
                messages_data = messages_ref.get()
            
            # Si pas de messages ou format incorrect
            if not messages_data:
                #print("ℹ️ Aucun message trouvé")
                return []
            
            # Créer un ensemble des IDs d'entreprises auxquelles l'utilisateur a accès
            authorized_company_ids = set()
            company_names = {}
            if companies:
                for company in companies:
                    company_id = company.get("contact_space_id", "")
                    company_name = company.get("name", "")
                    if company_id:
                        authorized_company_ids.add(company_id)
                        if company_name:
                            company_names[company_id] = company_name
            
            # Formater les messages et filtrer selon les droits d'accès
            formatted_messages = []
            for msg_id, msg_data in messages_data.items():
                if isinstance(msg_data, dict):
                    collection_id = msg_data.get("collection_id", "")
                    
                    # Vérifier si l'utilisateur a accès à ce message
                    if collection_id in authorized_company_ids:
                        # Ajouter l'ID du message
                        msg_data["doc_id"] = msg_id
                        
                        # Ajouter le nom de l'entreprise si disponible
                        msg_data["collection_name"] = company_names.get(collection_id, "")
                        
                        formatted_messages.append(msg_data)
            
            #print(f"✅ {len(formatted_messages)} messages directs autorisés récupérés")
            return formatted_messages
            
        except Exception as e:
            print(f"❌ Erreur lors de la récupération des messages directs: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    
    def _reinitialize_firebase_connection(self):
        """
        Ré-initialise la connexion à Firebase en cas d'erreur.
        """
        try:
            print("🔄 Ré-initialisation de la connexion Firebase")
            database_url = "https://pinnokio-gpt-default-rtdb.europe-west1.firebasedatabase.app/"

            # Réutiliser l'app unique via le singleton
            try:
                try:
                    from .firebase_app_init import get_firebase_app
                except ImportError:
                    from pinnokio_app.code.tools.firebase_app_init import get_firebase_app
                app = get_firebase_app()
                self.db = firebase_admin.db.reference('/', url=database_url, app=app)
                print("✅ Connexion ré-établie avec le singleton Firebase")
            except Exception as inner_e:
                print(f"⚠️ Erreur lors de la récupération de l'app Firebase: {inner_e}")
                # Dernier recours: référence directe (fonctionnera si les règles le permettent)
                self.db = firebase_admin.db.reference('/', url=database_url)
                print("✅ Référence directe à la base de données créée")
            
            # Tester la connexion
            test_result = None
            try:
                test_ref = self.db.child('connection_test')
                test_result = test_ref.set({"timestamp": datetime.now(timezone.utc).isoformat()})
            except AttributeError:
                # Essayer avec db.reference
                test_ref = firebase_admin.db.reference('connection_test', url=database_url)
                test_result = test_ref.set({"timestamp": datetime.now(timezone.utc).isoformat()})
            
            print(f"✅ Test de connexion réussi: {test_result}")
            
        except Exception as e:
            print(f"❌ Erreur lors de la ré-initialisation de Firebase: {str(e)}")
            import traceback
            traceback.print_exc()
            # Ne pas relever l'exception pour permettre à l'application de continuer

    def _get_thread_path(self, space_code: str, thread_key: str, mode: str = 'job_chats'):
        """
        Construit le chemin vers le thread en fonction du mode.
        
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread
            mode (str): Mode de groupement ('job_chats', 'chats' ou 'active_chats')
            
        Returns:
            str: Chemin complet vers le thread
        """
        if mode == 'chats':
            return f'{space_code}/chats/{thread_key}'
        if mode == 'active_chats':
            return f'{space_code}/active_chats/{thread_key}'
        # Par défaut, utilisez 'job_chats'
        return f'{space_code}/job_chats/{thread_key}'
    
    def create_chat(self,user_id: str, space_code: str, thread_name: str, mode: str = 'chats', chat_mode: str = 'general_chat',thread_key:str=None) -> dict:
        """
        Crée un nouveau thread de chat dans Firebase Realtime Database.
        
        Args:
            space_code (str): Code de l'espace (typiquement le companies_search_id)
            thread_name (str): Nom du nouveau thread/chat
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            chat_mode (str): Mode de fonctionnement du chat ('general_chat', 'onboarding_chat', etc.)
            
        Returns:
            dict: Informations sur le thread créé (thread_key, success, etc.)
        """
        
        try:
            if thread_key:
                # Utiliser le thread_key existant (pour le cas "renommer" un chat vierge)
                print(f"📝 Utilisation du thread_key existant: {thread_key}")
            else:
                # Générer un thread_key unique basé sur le timestamp et le nom
                thread_key = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9]', '_', thread_name)}"
                print(f"📝 Génération d'un nouveau thread_key: {thread_key}")
            # Construire le chemin complet pour le nouveau thread
            path = f"{space_code}/{mode}/{thread_key}"
            
            # Créer la structure initiale du thread
            thread_data = {
                "thread_name": thread_name,
                "thread_key": thread_key,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": user_id,
                "chat_mode": chat_mode,  # Ajouter le chat_mode dans les données du thread
                "messages": {}  # Messages vides au départ
                }
            
            # Enregistrer le thread dans Firebase
            result = self.db.child(path).set(thread_data)
            
            # Vérifier si l'opération a réussi (Firebase renvoie None en cas de succès)
            success = result is None
            
            return {
                "success": success,
                "thread_key": thread_key,
                "mode": mode,
                "chat_mode": chat_mode,
                "name": thread_name,
                "last_activity": datetime.now(timezone.utc).isoformat(),
                "message_count": 0
            }
            
        except Exception as e:
            print(f"❌ Erreur lors de la création du chat: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}


    def delete_chat(self, space_code: str, thread_key: str, mode: str = 'chats') -> bool:
        """
        Supprime un thread de chat dans Firebase Realtime Database.
        
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread à supprimer
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            bool: True si la suppression a réussi, False sinon
        """
        try:
            print(f"🗑️ Suppression du chat: {thread_key} (mode: {mode})")
            
            # Construire le chemin complet
            path = f"{space_code}/{mode}/{thread_key}"
            
            # Supprimer le thread
            thread_ref  = self.db.child(path)
            
            # Vérifier si le thread existe
            if thread_ref.get() is None:
                print(f"⚠️ Le thread {thread_key} n'existe pas")
                return False
                
            # Supprimer le thread
            thread_ref.delete()
            print(f"✅ Chat {thread_key} supprimé avec succès")
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la suppression du chat: {e}")
            import traceback
            traceback.print_exc()
            return False

    def rename_chat(self, space_code: str, thread_key: str, new_name: str, mode: str = 'chats') -> bool:
        """
        Renomme un thread de chat dans Firebase Realtime Database.
        
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread à renommer
            new_name (str): Nouveau nom du thread
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            bool: True si le renommage a réussi, False sinon
        """
        
        try:
            print(f"✏️ Renommage du chat: {thread_key} → '{new_name}' (mode: {mode})")
            
            # Construire le chemin complet
            path = f"{space_code}/{mode}/{thread_key}"
            
            # Référence au thread
            thread_ref = self.db.child(path)
            
            # Vérifier si le thread existe
            thread_data = thread_ref.get()
            if thread_data is None:
                print(f"⚠️ Le thread {thread_key} n'existe pas")
                return False
            
            # Mettre à jour le thread_name dans les métadonnées
            thread_ref.child("thread_name").set(new_name)
            print(f"✅ Chat {thread_key} renommé avec succès en '{new_name}'")
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors du renommage du chat: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_thread_name(self, space_code: str, thread_key: str, mode: str = 'chats') -> Optional[str]:
        """
        Récupère le nom d'un thread de chat dans Firebase Realtime Database.
        
        Args:
            space_code (str): Code de l'espace (typiquement le companies_search_id)
            thread_key (str): Identifiant du thread
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            Optional[str]: Nom du thread si trouvé, None sinon
        """
        
        try:
            # Construire le chemin complet
            path = f"{space_code}/{mode}/{thread_key}"
            
            # Référence au thread
            thread_ref = self.db.child(path)
            
            # Récupérer les données du thread
            thread_data = thread_ref.get()
            
            if thread_data is None:
                logger.warning(f"⚠️ Thread {thread_key} non trouvé dans {space_code}/{mode}")
                return None
            
            # Récupérer le thread_name (avec fallback sur thread_key si absent)
            thread_name = thread_data.get('thread_name', thread_key)
            
            logger.info(f"✅ Nom du thread récupéré: {thread_name} (thread_key={thread_key})")
            return thread_name
            
        except Exception as e:
            logger.error(f"❌ Erreur lors de la récupération du nom du thread: {e}", exc_info=True)
            return None


    def get_all_threads(self, space_code: str, mode: str = 'job_chats') -> Dict[str, Dict]:
        """
        Récupère tous les threads disponibles dans un espace spécifique.
        
        Args:
            space_code (str): Code de l'espace (typiquement le companies_search_id)
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            Dict[str, Dict]: Dictionnaire des threads avec leurs métadonnées
        """
        
        try:
            print(f"📚 Récupération de tous les threads pour l'espace: {space_code}, mode: {mode}")
            
            # Référence au dossier contenant les threads selon le mode
            if mode == 'chats':
                threads_ref = self.db.child(f'{space_code}/chats')
            elif mode == 'active_chats':
                threads_ref = self.db.child(f'{space_code}/active_chats')
            else:  # job_chats par défaut
                threads_ref = self.db.child(f'{space_code}/job_chats')
            
            # Récupérer tous les nœuds enfants qui sont des threads
            threads = threads_ref.get()
            
            if not threads:
                print("ℹ️ Aucun thread trouvé dans cet espace")
                return {}
            
            # Filtrer pour ne garder que les threads (ignorer les potentiels autres nœuds)
            valid_threads = {}
            for thread_key, thread_data in threads.items():
                # Vérifier que c'est bien un thread (il devrait contenir un nœud 'messages')
                if isinstance(thread_data, dict) and 'messages' in thread_data:
                    # Ajouter des méta-informations utiles
                    thread_info = {
                        'thread_key': thread_key,
                        'thread_name': thread_data.get('thread_name', thread_key),
                        'chat_mode': thread_data.get('chat_mode', 'general_chat'),  # ✅ Récupérer le chat_mode
                        'message_count': len(thread_data.get('messages', {})),
                        'last_activity': self._get_last_activity(thread_data.get('messages', {}))
                    }
                    valid_threads[thread_key] = thread_info
            
            print(f"✅ {len(valid_threads)} threads récupérés")
            return valid_threads
        
        except Exception as e:
            print(f"❌ Erreur lors de la récupération des threads:")
            print(f"  Type: {type(e).__name__}")
            print(f"  Message: {str(e)}")
            import traceback
            print("  Traceback:")
            print(traceback.format_exc())
            return {}
    def get_thread_messages(self, space_code: str, thread_key: str, mode: str = 'chats', limit: int = 100) -> List[Dict]:
        """
        Récupère les messages d'un thread spécifique.

        Args:
            space_code (str): Code de l'espace (typiquement le companies_search_id)
            thread_key (str): Clé du thread
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            limit (int): Nombre maximum de messages à récupérer

        Returns:
            List[Dict]: Liste des messages triés par timestamp
        """
        try:
            print(f"📨 Récupération des messages pour thread: {thread_key}, mode: {mode}")

            # Construire le chemin du thread
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            messages_ref = self.db.child(f'{thread_path}/messages')

            # Récupérer les messages
            messages_data = messages_ref.get()

            if not messages_data:
                print("ℹ️ Aucun message trouvé dans ce thread")
                return []

            # Convertir en liste avec les IDs
            messages_list = []
            for msg_id, msg_data in messages_data.items():
                if isinstance(msg_data, dict):
                    msg_data['message_id'] = msg_id
                    messages_list.append(msg_data)

            # Trier par timestamp (plus ancien en premier)
            messages_list.sort(key=lambda x: x.get('timestamp', ''))

            # Limiter le nombre de messages
            if limit and len(messages_list) > limit:
                messages_list = messages_list[-limit:]  # Garder les plus récents

            print(f"✅ {len(messages_list)} messages récupérés")
            return messages_list

        except Exception as e:
            print(f"❌ Erreur lors de la récupération des messages: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _get_last_activity(self, messages: Dict) -> str:
        """
        Détermine la dernière activité d'un thread en fonction des timestamps des messages.

        Args:
            messages (Dict): Dictionnaire des messages

        Returns:
            str: Timestamp de la dernière activité au format ISO
        """
        if not messages:
            return ""
        
        # Extraire tous les timestamps
        timestamps = []
        for _, msg in messages.items():
            if isinstance(msg, dict) and 'timestamp' in msg:
                timestamps.append(msg['timestamp'])
        
        # Trier et prendre le plus récent
        if timestamps:
            return sorted(timestamps)[-1]
        
        return ""

    def test_connection(self):
        """Test la connexion à Firebase."""
        try:
            test_ref = self.db.child('test')
            test_ref.get()
            print("Test de connexion Firebase réussi")
            return True
        except Exception as e:
            print(f"Erreur de connexion Firebase: {e}")
            return False

    def send_speeddial_action(
        self, 
        user_id: str,
        space_code: str, 
        thread_key: str, 
        action: str,
        additional_data: dict = None,
        mode: str = 'job_chats'
        ) -> bool:
        """
        Envoie un message d'action speeddial via Firebase Realtime.
        Args:
            space_code (str): Identifiant de l'espace
            thread_key (str): Identifiant du thread/conversation
            action (str): Type d'action speeddial (ex: 'TERMINATE')
            additional_data (dict, optional): Données supplémentaires à inclure
            mode (str): Mode de groupement ('job_chats' ou 'chats')
        Returns:
            bool: True si l'envoi a réussi
        """
        try:
            message_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'sender_id': user_id,
                'message_type': 'SPEEDDIAL',
                'action': action,
                'read': False
            }

            # Ajouter des données supplémentaires si fournies
            if additional_data:
                message_data.update(additional_data)

            # Créer la référence au chemin des messages
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            messages_ref = self.db.child(f'{thread_path}/messages')
            
            print(f"Envoi d'action speeddial vers: {thread_path}/messages")
            
            # Envoyer le message
            messages_ref.push(message_data)
            print(f"Action speeddial {action} envoyée avec succès")
            return True

        except Exception as e:
            print(f"Erreur lors de l'envoi de l'action speeddial: {e}")
            return False

    def erase_chat(self, space_code: str, thread_key: str, mode: str = 'job_chats') -> bool:
        """
        Supprime tous les messages d'un canal spécifique.
        
        Args:
            space_code (str): Code de l'espace de discussion
            thread_key (str): Identifiant du thread/conversation
            mode (str): Mode de groupement ('job_chats' ou 'chats')
        
        Returns:
            bool: True si la suppression a réussi, False sinon
        """
        try:
            print(f"🗑️ Début de la suppression du chat")
            print(f"📍 Espace: {space_code}")
            print(f"🧵 Thread: {thread_key}")
            print(f"🔄 Mode: {mode}")
            
            # Obtenir la référence au nœud des messages
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            messages_ref = self.db.child(f'{thread_path}/messages')
            
            # Vérifier si le nœud existe
            if messages_ref.get() is None:
                print("⚠️ Aucun message trouvé dans ce canal")
                return True
            
            # Supprimer le nœud des messages
            messages_ref.delete()
            print("✅ Tous les messages ont été supprimés avec succès")
            
            # Recréer le nœud des messages vide pour maintenir la structure
            messages_ref.set({})
            print("✅ Structure du canal réinitialisée")
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de la suppression des messages:")
            print(f"  Type: {type(e).__name__}")
            print(f"  Message: {str(e)}")
            import traceback
            print("  Traceback:")
            print(traceback.format_exc())
            return False
    
    def watch_chat(self, space_code: str, chat_id: str, callback, mode: str = 'chats') -> None:
        """
        Écoute les changements dans un chat spécifique.
        Args:
            space_code (str): Code de l'espace
            chat_id (str): Identifiant du chat
            callback: Fonction à appeler lors de la réception d'un nouveau message
            mode (str): Mode de groupement ('job_chats' ou 'chats')
        """
        try:
            thread_path = self._get_thread_path(space_code, chat_id, mode)
            chat_ref = self.db.child(f'{thread_path}/messages')
            
            def on_change(event):
                if event.data:  # Vérifie si des données sont présentes
                    callback(event.data)
            
            # Mettre en place l'écouteur
            chat_ref.listen(on_change)
        except Exception as e:
            print(f"Erreur lors de l'écoute du chat: {e}")

    
    
    async def listen_realtime_channel(
        self,
        space_code: str,
        thread_key: str,
        callback,
        mode: str = 'job_chats',
        scheduler: Optional[Callable[[Awaitable[Any], Optional[float]], Any]] = None,
        scheduler_timeout: Optional[float] = 1.0
    ) -> None:
        """
        Configure un écouteur pour les messages d'un canal spécifique.
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread/conversation
            callback: Fonction asynchrone à appeler lors de la réception d'un message
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            scheduler: Fonction optionnelle pour planifier l'exécution asynchrone (ex: session.schedule_coroutine)
            scheduler_timeout: Temps d'attente max pour le scheduler (None pour ne pas attendre)
        """
        try:
            print(f"🚀 Démarrage de l'écoute Firebase Realtime")
            print(f"📍 Espace: {space_code}")
            print(f"🧵 Thread: {thread_key}")
            print(f"🔄 Mode: {mode}")
            
            # Créer la référence selon le mode
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            messages_ref = self.db.child(f'{thread_path}/messages')
            
            print(f"📡 Référence créée: {messages_ref.path}")
            # Utiliser un ensemble pour suivre les messages en cours de traitement

            def on_message(event):
                #print(f"\n🔔 Événement reçu:")
                #print(f"  Type: {event.event_type}")
                #print(f"  Chemin: {event.path}")
                #print(f"  Data: {event.data}")
                
                if event.data and event.event_type == 'put':
                    if not (event.data and event.event_type == 'put' and event.path != '/' and isinstance(event.data, dict)):
                      return
                    
                    message_id = event.path.lstrip('/')
                    message_type = event.data.get('message_type', 'N/A')
                    print(f"📨 [LISTENER_METIER] Message reçu - path={messages_ref.path} msg_id={message_id} type={message_type}")
            
                    # Vérifier si le message est déjà en cours de traitement
                    if message_id in self.processed_messages:
                        print(f"⏳ [LISTENER_METIER] Message {message_id} en cours de traitement, ignoré")
                        return
                   
                    # ⚠️ Support des deux formats: message_type (ancien) et type (nouveau)
                    message_type = event.data.get('message_type') or event.data.get('type')
                    is_unread = not event.data.get('read', True)
                    
                    # ✅ Inclure tous les types de messages gérés par le handler
                    if (message_type in ['MESSAGE', 'CARD', 'FOLLOW_CARD', 'TOOL', 'CMMD', 'FOLLOW_MESSAGE', 'WAITING_MESSAGE', 'WORKFLOW', 'CLOSE_INTERMEDIATION', 'CARD_CLICKED_PINNOKIO'] and 
                        is_unread):
                        self.processed_messages.add(message_id)
                        message_data = {
                            'id': event.path.lstrip('/'),
                            **event.data
                        }
                        print(f"✅ [LISTENER_METIER] Message valide détecté - msg_id={message_id} type={message_type} path={messages_ref.path}")
                        #print(f"📝 Message Pinnokio formaté: {message_data}")
                        # Marquer comme lu AVANT le traitement
                        messages_ref.child(message_id).update({'read': True})
                        print(f"✅ [LISTENER_METIER] Message {message_id} marqué comme lu")
                        try:
                            print(f"🔄 [LISTENER_METIER] Envoi vers callback - msg_id={message_id}")

                            if scheduler:
                                scheduler(
                                    callback(message_data),
                                    timeout=scheduler_timeout
                                )
                            else:
                                loop: Optional[asyncio.AbstractEventLoop]
                                try:
                                    loop = asyncio.get_running_loop()
                                except RuntimeError:
                                    try:
                                        loop = asyncio.get_event_loop()
                                    except RuntimeError:
                                        loop = None

                                if loop and loop.is_running():
                                    future = asyncio.run_coroutine_threadsafe(
                                        callback(message_data),
                                        loop
                                    )
                                    future.result(timeout=1)
                                else:
                                    asyncio.run(callback(message_data))

                            print(f"✅ [LISTENER_METIER] Callback exécuté avec succès - msg_id={message_id}")
                            self.processed_messages.discard(message_id)

                        except FutureTimeoutError:
                            print(f"⚠️ [LISTENER_METIER] Callback timeout - msg_id={message_id}, continuer en arrière-plan")
                            self.processed_messages.discard(message_id)
                        except Exception as e:
                            print(f"❌ [LISTENER_METIER] Erreur dans le callback - msg_id={message_id} error={e}")
                            self.processed_messages.discard(message_id)
                            if isinstance(e, RuntimeError) and 'Event loop is closed' in str(e):
                                logger.exception("[LISTENER_METIER] Event loop fermé lors du callback", exc_info=True)
                        

                    else:
                        print(f"⏭️ [LISTENER_METIER] Message ignoré - Type: {message_type}, Lu: {not is_unread}, msg_id={message_id}")
            
            print("🎯 Configuration de l'écouteur...")
            listener = messages_ref.listen(on_message)
            print("✅ Écouteur configuré et actif")
            
            return listener  # Retourner l'écouteur pour pouvoir le désactiver plus tard
            
        except Exception as e:
            print(f"❌ Erreur lors de la configuration de l'écouteur:")
            print(f"  Type: {type(e).__name__}")
            print(f"  Message: {str(e)}")
            import traceback
            print("  Traceback:")
            print(traceback.format_exc())
            raise
    
    def stop_listener(self):
        """Arrête l'écouteur actif s'il existe."""
        try:
            if self.current_listener:
                print("Arrêt de l'écouteur Firebase...")
                self.should_stop = True  # Signal d'arrêt
                self.current_listener.close()
                self.current_listener = None
                print("Écouteur Firebase arrêté avec succès")
                return True
        except Exception as e:
            print(f"Erreur lors de l'arrêt de l'écouteur: {e}")
            return False

    def send_tools_list(self,user_id: str, space_code: str, thread_key: str, tools_config: List[Dict],mode: str = 'job_chats') -> bool:
        """
        Envoie la liste des outils disponibles via Firebase Realtime.
        
        Args:
            space_code (str): Identifiant de l'espace
            thread_key (str): Identifiant du thread/conversation
            tools_config (List[Dict]): Configuration des outils
            
        Returns:
            bool: True si l'envoi a réussi
        """
        try:
            # Extraire uniquement les noms des outils
            tool_names = [tool["name"] for tool in tools_config]
            
            # Créer le message structuré pour les outils
            message_data = {
                'content': json.dumps({
                    'tool_list': tool_names
                }),
                'sender_id': user_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'message_type': 'TOOL',
                'read': False
            }
            
            # Utiliser le thread_key comme room_id
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            messages_ref = self.db.child(f'{thread_path}/messages')
            messages_ref.push(message_data)
            
            print(f"Liste d'outils envoyée: {tool_names}")
            return True
            
        except Exception as e:
            print(f"Erreur lors de l'envoi de la liste d'outils: {e}")
            return False



    def send_realtime_message_structured(
        self,
        user_id : str,
        space_code: str, 
        thread_key: str, 
        text: str=None,
        message_type: str = "MESSAGE_PINNOKIO",
        message_data: dict = None,
        mode: str = 'job_chats'
        ) -> bool:
        """
        Envoie un message structuré via Firebase Realtime.
        Args:
            space_code (str): Identifiant de l'espace
            thread_key (str): Identifiant du thread/conversation
            text (str): Contenu du message
            message_type (str): Type de message (par défaut "MESSAGE_PINNOKIO")
            message_data (dict): Données additionnelles du message (optionnel)
            mode (str): Mode de groupement ('job_chats' ou 'chats')
        Returns:
            bool: True si l'envoi a réussi
        """
        try:
            # Déterminer le chemin de base selon le mode
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            
            if message_type == "CARD_CLICKED_PINNOKIO":
                # Structure spécifique pour les cartes cliquées
                messages_ref = self.db.child(f'{thread_path}/messages')
                messages_ref.push(message_data)
                return True
            else:
                if not message_data:
                    message_data = {
                        'content': text,
                        'sender_id': user_id,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'message_type': message_type,
                        'read': False
                    }
                
                # Envoyer le message au chemin complet
                messages_ref = self.db.child(f'{thread_path}/messages')
                print(f"Envoi vers le chemin: {thread_path}/messages")
                messages_ref.push(message_data)
                return True
        except Exception as e:
            print(f"Erreur lors de l'envoi du message structuré: {e}")
            return False


    def get_channel_messages(self, space_code: str, thread_key: str, limit: int = 50, mode: str = 'job_chats') -> List[Dict]:
        """
        Récupère les messages d'un canal spécifique.
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread/conversation
            limit (int): Nombre maximum de messages à récupérer
            mode (str): Mode de groupement ('job_chats' ou 'chats')
        Returns:
            List[Dict]: Liste des messages
        """
        try:
            # Construire le chemin complet
            thread_path = self._get_thread_path(space_code, thread_key, mode)
            messages_ref = self.db.child(f'{thread_path}/messages')
            
            messages = messages_ref.get()
            
            if not messages:
                return []
            
            # Convertir en liste et trier par timestamp
            message_list = [{'id': key, **value} for key, value in messages.items()]
            message_list.sort(key=lambda x: x.get('timestamp', ''))
            
            return message_list[-limit:] if len(message_list) > limit else message_list
        except Exception as e:
            print(f"Erreur lors de la récupération des messages: {e}")
            return []

    # ════════════════════════════════════════════════════════════
    # Instruction Templates CRUD
    # Path: {mandate_path}/working_doc/instruction_templates/{page_name}/{template_id}
    # ════════════════════════════════════════════════════════════

    def fetch_instruction_templates(self, mandate_path: str, page_name: str) -> List[Dict]:
        """
        Retourne tous les instruction templates pour une page donnée.

        Args:
            mandate_path: Chemin du mandat
            page_name: "routing" | "invoices" | "banking"

        Returns:
            Liste de dicts [{id, title, content, created_at}, ...]
        """
        try:
            collection_path = f"{mandate_path}/working_doc/instruction_templates/{page_name}/items"
            docs = self.db.collection(collection_path).stream()
            templates = [{"id": doc.id, **doc.to_dict()} for doc in docs]
            print(f"✅ [FIREBASE] Fetched {len(templates)} instruction templates for {page_name}")
            return templates
        except Exception as e:
            print(f"❌ [FIREBASE] Error fetching instruction templates for {page_name}: {e}")
            return []

    def create_instruction_template(self, mandate_path: str, page_name: str, template_data: Dict) -> Dict:
        """
        Crée un nouveau instruction template. Génère l'ID côté serveur.

        Args:
            mandate_path: Chemin du mandat
            page_name: "routing" | "invoices" | "banking"
            template_data: {title, content, created_at}

        Returns:
            {id, title, content, created_at} ou {} en cas d'erreur
        """
        try:
            collection_path = f"{mandate_path}/working_doc/instruction_templates/{page_name}/items"
            doc_ref = self.db.collection(collection_path).document()
            doc_ref.set(template_data)
            result = {"id": doc_ref.id, **template_data}
            print(f"✅ [FIREBASE] Created instruction template {doc_ref.id} for {page_name}")
            return result
        except Exception as e:
            print(f"❌ [FIREBASE] Error creating instruction template for {page_name}: {e}")
            return {}

    def update_instruction_template(self, mandate_path: str, page_name: str, template_id: str, update_data: Dict) -> bool:
        """
        Met à jour un instruction template existant.

        Args:
            mandate_path: Chemin du mandat
            page_name: "routing" | "invoices" | "banking"
            template_id: ID du template
            update_data: Champs à mettre à jour {title?, content?}

        Returns:
            True si succès, False sinon
        """
        try:
            collection_path = f"{mandate_path}/working_doc/instruction_templates/{page_name}/items"
            doc_ref = self.db.collection(collection_path).document(template_id)
            doc_ref.update(update_data)
            print(f"✅ [FIREBASE] Updated instruction template {template_id} for {page_name}")
            return True
        except Exception as e:
            print(f"❌ [FIREBASE] Error updating instruction template {template_id} for {page_name}: {e}")
            return False

    def delete_instruction_template(self, mandate_path: str, page_name: str, template_id: str) -> bool:
        """
        Supprime un instruction template par ID.

        Args:
            mandate_path: Chemin du mandat
            page_name: "routing" | "invoices" | "banking"
            template_id: ID du template

        Returns:
            True si succès, False sinon
        """
        try:
            collection_path = f"{mandate_path}/working_doc/instruction_templates/{page_name}/items"
            doc_ref = self.db.collection(collection_path).document(template_id)
            doc_ref.delete()
            print(f"✅ [FIREBASE] Deleted instruction template {template_id} for {page_name}")
            return True
        except Exception as e:
            print(f"❌ [FIREBASE] Error deleting instruction template {template_id} for {page_name}: {e}")
            return False

def get_firebase_management() -> FirebaseManagement:
    global _FIREBASE_MANAGEMENT_SINGLETON
    if _FIREBASE_MANAGEMENT_SINGLETON is None:
        _FIREBASE_MANAGEMENT_SINGLETON = FirebaseManagement()
    return _FIREBASE_MANAGEMENT_SINGLETON


def get_firebase_realtime() -> FirebaseRealtimeChat:
    global _FIREBASE_REALTIME_SINGLETON
    if _FIREBASE_REALTIME_SINGLETON is None:
        _FIREBASE_REALTIME_SINGLETON = FirebaseRealtimeChat()
    return _FIREBASE_REALTIME_SINGLETON


    