from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Optional
from firebase_admin import credentials, firestore, initialize_app,auth
import firebase_admin
from firebase_admin import credentials
from google.cloud.firestore_v1.base_query import FieldFilter
from .firebase_client import get_firestore, get_firebase_app
from .tools.g_cred import get_secret

try:
    import stripe  # type: ignore
except Exception:  # stripe facultatif si non utilisé immédiatement
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
            secret_name = os.getenv("STRIPE_KEYS")
            if not secret_name:
                return
            keys_json = get_secret(secret_name)
            stripe_keys = json.loads(keys_json)
            self.stripe_api_key = stripe_keys.get("stripe_prod_key")
            if stripe and self.stripe_api_key and not getattr(stripe, "_pinnokio_configured", False):
                stripe.api_key = self.stripe_api_key
                stripe._pinnokio_configured = True  # type: ignore[attr-defined]
            self.stripe_success_url = os.getenv("STRIPE_SUCCESS_URL", "http://localhost:3000/payment-success")
            self.stripe_cancel_url = os.getenv("STRIPE_CANCEL_URL", "http://localhost:3000/payment-canceled")
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation Stripe: {e}")
            raise

    @property
    def firestore_client(self):
        return self.db

    def create_telegram_user(self, user_id: str, mandate_path: str, telegram_username: str, additional_data: dict = None):
        """
        Crée ou met à jour un utilisateur Telegram dans la collection.
        Ajoute la société au mapping des mandats autorisés.
        """
        try:
            from datetime import datetime
            
            # Référence au document utilisateur
            telegram_ref = self.db.collection('telegram_users').document(telegram_username)
            existing_doc = telegram_ref.get()
            
            # Préparer les données du mandat à ajouter
            mandate_data = {
                "firebase_user_id": user_id,
                "mandate_path": mandate_path,
                "added_at": datetime.now()
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
                    'updated_at': datetime.now()
                })
                
                print(f"✅ Mandat ajouté pour {telegram_username}")
                return True
                
            else:
                # Créer un nouvel utilisateur
                user_data = {
                    "telegram_username": telegram_username,
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
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
            query = users_ref.where('is_active', '==', True)
            
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
            from datetime import datetime
            
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
                    'updated_at': datetime.now()
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
            from datetime import datetime
            
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
                'last_activity': datetime.now(),
                'updated_at': datetime.now()
            })
            
            print(f"✅ Activité mise à jour pour {telegram_username}")
            return True
                
        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour de l'activité: {str(e)}")
            return False

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
            query = self.db.collection("beta_request").where("email", "==", email).limit(1)
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
            # Supposant que vos jobs sont stockés dans une collection "jobs" ou similaire
            # Ajustez le chemin selon votre structure Firebase
            job_ref = self.db.collection("scheduler").document(job_id)
            
            # Vérifier si le document existe avant de le supprimer
            if job_ref.get().exists:
                job_ref.delete()
                print(f"✅ Document scheduler {job_id} supprimé de Firebase")
                return True
            else:
                print(f"ℹ️ Document scheduler {job_id} n'existe pas dans Firebase")
                return True  # Considéré comme un succès car l'objectif est atteint
            
        except Exception as e:
            print(f"❌ Erreur lors de la suppression complète du job {job_id}: {e}")
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
        Récupère les détails d'un lot bancaire à partir de son ID dans task_manager.
        
        Args:
            batch_id (str): ID du lot à récupérer
            
        Returns:
            dict: Dictionnaire contenant le job_id, bank_account et transactions, ou None si non trouvé
        """
        try:
            if not user_id:
                print("Erreur: L'ID utilisateur est requis pour accéder aux détails du lot")
                return None
            
            # Chemin direct vers le document task_manager
            task_manager_path = f"clients/{user_id}/task_manager"
            task_doc_ref = self.db.collection(task_manager_path).document(batch_id)
            task_doc = task_doc_ref.get()
            
            if not task_doc.exists:
                print(f"Aucun document trouvé pour le lot {batch_id}")
                return None
            
            task_data = task_doc.to_dict()
            
            # Structure de base pour les détails du lot
            batch_details = {
                'job_id': batch_id,
                'bank_account': '',
                'transactions': [],
                'start_instructions':'',
            }
            
            # Récupérer les données bancaires
            if 'jobs_data' in task_data and isinstance(task_data['jobs_data'], list) and len(task_data['jobs_data']) > 0:
                # Prendre le premier élément de jobs_data
                jobs_data = task_data['jobs_data'][0]
                
                # Récupérer le compte bancaire
                if 'bank_account' in jobs_data:
                    batch_details['bank_account'] = jobs_data['bank_account']
                
                # Récupérer les transactions
                if 'transactions' in jobs_data and isinstance(jobs_data['transactions'], list):
                    batch_details['transactions'] = jobs_data['transactions']
            
            if 'start_instructions' in task_data:
                batch_details['start_instructions']=task_data['start_instructions']

            # Si bank_account n'a pas été trouvé dans jobs_data, essayer au niveau principal
            if not batch_details['bank_account'] and 'journal_name' in task_data:
                batch_details['bank_account'] = task_data['journal_name']
            

            return batch_details
            
        except Exception as e:
            print(f"Erreur lors de la récupération des détails du lot {batch_id}: {e}")
            import traceback
            traceback.print_exc()
            return None

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
                filter=firestore.FieldFilter("function_name", "==", "banker")
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

    def upload_token_usage(self,user_id, data):
        """
        Télécharge les données d'utilisation des tokens vers Firebase.
        Stocke les données dans un document identifié par job_id.
        Si le document existe déjà, ajoute les nouvelles données à une liste.
        
        Args:
            data (dict): Données d'utilisation des tokens
                
        Returns:
            bool: True si le téléchargement est réussi, False sinon
        """
        try:
            # Extraire le job_id des données
            job_id = data.get('job_id')
            if not job_id:
                print("Erreur: job_id manquant dans les données")
                return False
            
            # Structure: clients (collection) / user_id (document) / token_usage (collection) / job_id (document)
            doc_ref = self.db.document(f'clients/{user_id}/token_usage/{job_id}')
            
            # Vérifier si le document existe déjà
            doc = doc_ref.get()
            
            if doc.exists:
                # Le document existe, récupérer les données actuelles
                current_data = doc.to_dict()
                
                # Si 'entries' n'existe pas, l'initialiser comme une liste
                if 'entries' not in current_data:
                    current_data['entries'] = []
                
                # Ajouter les nouvelles données à la liste
                current_data['entries'].append(data)
                
                # Mettre à jour le document
                doc_ref.set(current_data)
            else:
                # Le document n'existe pas, le créer avec les données initiales
                doc_ref.set({
                    'entries': [data]
                })
            
            print(f"Données d'utilisation téléchargées avec succès pour job {job_id}, provider {data.get('provider_name')}")
            return True
            
        except Exception as e:
            print(f"Erreur lors du téléchargement des données d'utilisation: {e}")
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
    
    def x_check_job_status(self, user_id: str, job_id: str = None, file_id: str = None):
        """
        Vérifie l'état d'un job ou d'un fichier spécifique en parcourant la collection de notifications.
        
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
            collection_ref = self.db.collection(collection_path)
            docs = collection_ref.stream()
            
            # Chercher le document correspondant à file_id ou job_id
            for doc in docs:
                doc_data = doc.to_dict()
                
                if file_id and doc_data.get('file_id') == file_id:
                    print(f"Fichier {file_id} trouvé avec statut: {doc_data.get('status', 'inconnu')}")
                    doc_data['document_id'] = doc.id
                    return doc_data
                
                if job_id and doc_data.get('job_id') == job_id:
                    print(f"Job {job_id} trouvé avec statut: {doc_data.get('status', 'inconnu')}")
                    doc_data['document_id'] = doc.id
                    return doc_data
            
            # Si aucun document correspondant n'est trouvé
            print(f"Aucun résultat trouvé pour job_id={job_id} ou file_id={file_id}")
            return None
            
        except Exception as e:
            print(f"Erreur lors de la vérification du statut: {e}")
            return None

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
                doc_ref = self.db.collection(collection_path).document(file_id)
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
                doc_ref = self.db.collection(collection_path).document(job_id)
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
    
    def _process_immediate_top_up(self, user_id: str,amount: float, transaction_ref: str=None) -> None:
        """
        Traite immédiatement un top-up sans passer par Stripe (méthode interne).
        Utilisée lorsque Stripe n'est pas configuré ou pour le mode de développement.
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

            if isinstance(job_id, str):
                job_id = [job_id]

            # Construire le chemin de la collection
            collection_path = f"clients/{user_id}/notifications"
            collection_ref = self.db.collection(collection_path)
            docs = collection_ref.stream()

            deleted = False

            # Chercher et supprimer les documents correspondant aux job_id
            for doc in docs:
                doc_data = doc.to_dict()
                if doc_data.get('job_id') in job_id:
                    self.db.collection(collection_path).document(doc.id).delete()
                    print(f"Notification {doc.id} supprimée pour job_id {doc_data.get('job_id')}.")
                    deleted = True

            if not deleted:
                print(f"Aucune notification trouvée pour job_id={job_id}")
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
                query = ref.where("invited_by", "==", invited_by).limit(limit)
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
                query = ref.where("read", "==", False).limit(limit)

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
                    query = ref.where("read", "==", bool(read)).limit(limit)
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
                        'timestamp': datetime.now().isoformat()  # Mettre à jour aussi le timestamp
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


    def x_add_top_up(self, mandate_path: str, top_up_data: dict) -> bool:
        """
        Ajoute une nouvelle transaction de recharge dans Firestore et met à jour le solde courant.
        
        Args:
            mandate_path (str): Chemin du mandat dans Firestore
            top_up_data (dict): Dictionnaire contenant 'currency' et 'amount'
            
        Returns:
            bool: True si l'ajout est réussi, False sinon
        """
        try:
            # Validation des données
            if not isinstance(top_up_data, dict):
                raise ValueError("top_up_data must be a dictionary")
            
            required_fields = {'currency', 'amount'}
            if not all(field in top_up_data for field in required_fields):
                raise ValueError("Missing required fields: currency and amount")
            
            # Extraire l'ID utilisateur du chemin du mandat
            path_parts = mandate_path.split('/')
            user_id = path_parts[1] if len(path_parts) > 1 else None
            
            if not user_id:
                raise ValueError("Could not extract user_id from mandate_path")
            
            # Convertir le montant en float
            amount = float(top_up_data['amount'])
            
            # 1. Enregistrer la transaction avec le statut 'added'
            top_up_doc = {
                'currency': top_up_data['currency'],
                'amount': amount,
                'created_at': firestore.SERVER_TIMESTAMP,
                'status': 'added'  # Définir directement comme 'added'
            }
            
            # Chemin de la collection de transactions
            transactions_path = f"{mandate_path}/billing/topping/transactions"
            
            # Ajouter la transaction
            self.db.collection(transactions_path).add(top_up_doc)
            
            # 2. Mettre à jour le solde actuel
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
            
            return True
            
        except Exception as e:
            print(f"Error adding top-up record: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

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
        existing_docs = self.db.collection("clients").where("client_email", "==", email_adresse).get()
        if existing_docs:
            print(f"⚠️ L'adresse e-mail {email_adresse} existe déjà dans la collection clients.")
            return True  # Arrête l'exécution si un utilisateur avec le même e-mail existe
        else:
            return False    

    def check_and_create_client_document(self, user_data):
        """
        Vérifie si le document utilisateur existe et effectue les actions nécessaires.
        
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
        
        user_ref = self.db.collection("users").document(user_id)
        print(f"🔍 Référence du document : {user_ref.path}")

        doc = user_ref.get()
        # Vérification de l'existence du document
        if doc.exists:
            print(f"✅ Le document pour l'utilisateur {user_id} existe déjà.")
            user_ref.update(user_data)
        else:
            print(f"⚠️ Le document pour l'utilisateur {user_id} n'existe pas. Création...")
            # Création du document principal dans 'users'
            user_ref.set(user_document_data)
            print(f"✅ Document 'users' créé pour l'utilisateur {user_id}.")

            # Création du document dans 'clients'
            print(f"⚠️ Le document pour l'utilisateur {user_id} n'existe pas. Création...")
            # Création du document principal
            user_ref=self.db.collection('clients').document(user_id)
            client_uuid=f"client_{str(uuid.uuid4())[:8]}"
            user_ref.set({
                "client_email": user_data["email"],
                "client_name": user_data["displayName"],
                "created_at": firestore.SERVER_TIMESTAMP,
               
            })
            # Création du sous-document
            sub_doc_ref = user_ref.collection("bo_clients").document(user_id)
            sub_doc_ref.set({
               
                "client_name": user_data["displayName"],
                "created_at": firestore.SERVER_TIMESTAMP,
                "client_uuid":client_uuid
            })

            print(f"✅ Documents créés pour l'utilisateur {user_id}.")
            # Règle de sécurité ou rôle dans PostgreSQL
            
            print(f"Assigning admin role to user in postgres {user_id}")
           

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

        # Itérer sur chaque 'pinnokio_func' pour rechercher les fichiers correspondants
        for pinnokio_func in pinnokio_funcs:
            # Trouver le document correspondant dans 'klk_vision' pour chaque 'pinnokio_func'
            matching_department_doc_id = self.find_matching_department(user_id,pinnokio_func)

            if not matching_department_doc_id:
                print(f"Aucun département correspondant à '{pinnokio_func}' trouvé dans 'klk_vision'.")
                continue  # Passer à l'itération suivante si le département n'existe pas

            # Référence à la sous-collection 'journal' du département correspondant
            if user_id:
                base_path = f'clients/{user_id}/klk_vision'
            else:
                base_path = 'klk_vision'
            journal_ref = self.db.collection(base_path).document(matching_department_doc_id).collection('journal')

            # Rechercher les fichiers dans la base de données pour chaque file_name
            for file_name in file_names:
                query = journal_ref.where('file_name', '==', file_name).where('mandat_id', '==', mandat_id).get()
                
                if query:
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
        query = clients_ref.where('client_name', '==', client_name)
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
        client_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()

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
            mandates_query = self.db.collection(base_path).where('contact_space_name', '==', business_name).limit(1).get()

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
        user_query=self.db.collection('users').where('email','==',mail_to_invite).limit(1).get()
        for user_doc in user_query:
            existing_user = {
                "uid": user_doc.id,
                "data": user_doc.to_dict()
            }

    def create_or_get_chat_threadkey(self,user_id, department_index):
        departments = {
            "Bankbookeeper": "Bankbookeeper",
            "Router": "Router",
            "APbookeeper": "APbookeeper",
            "HRmanager": "HRmanager",
            "Admanager": "Admanager",
            "EXbookeeper": "EXbookeeper"
        }
        
        department = departments.get(department_index)
        
        if not department:
            raise ValueError("Invalid department index provided")
        
        # Recherche du document correspondant dans la collection /klk_vision/
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        doc_ref = self.db.collection(base_path).where('departement', '==', department).limit(1).get()

        if not doc_ref:
            raise ValueError(f"No document found for department {department}")
        
        # Le document est le premier (et normalement unique) résultat
        doc = doc_ref[0]
        doc_data = doc.to_dict()

        # Vérification du champ 'chat_threadkey'
        if 'chat_threadkey' in doc_data:
            return doc_data['chat_threadkey']
        else:
            
            # Génération du chat_threadkey avec UUID
            chat_threadkey = f"klk_{uuid.uuid4().hex}_{department}"
            
            # Mise à jour du document avec le nouveau chat_threadkey
            doc_ref[0].reference.update({'chat_threadkey': chat_threadkey})
            
            return chat_threadkey

    def get_close_job_id(self,user_id,departement, space_id):
        """
        Recherche les job_id fermés correspondant aux critères spécifiés.

        Args:
            space_id (str): L'ID de l'espace à rechercher dans le champ 'departement'.
             departement_index=['Admanager','EXbookeeper','Router','Bankbookeeper','APbookeeper','HRmanager']
        Returns:
            list: Une liste des job_id correspondant aux critères.
        """
        departement_index=['Admanager','EXbookeeper','Router','Bankbookeeper','APbookeeper','HRmanager']
        
        filtered_documents = [] 
        
        # Itération sur tous les documents de la collection 'klk_vision'
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        klk_vision_docs = self.db.collection(base_path).stream()
        
        for doc in klk_vision_docs:
            data = doc.to_dict()
            
            # Vérification si le document a le champ 'departement' égal à space_id
            chosen_departement=departement_index[departement]
            #print(chosen_departement)
            if data.get('departement') == chosen_departement:
                if chosen_departement=='APbookeeper':
                    # Si c'est le cas, on cherche dans la collection 'journal' de ce document
                    journal_docs = doc.reference.collection('journal').stream()
                    #print(journal_docs)
                    for journal_doc in journal_docs:
                        journal_data = journal_doc.to_dict()
                        #print(f"impression de journal data:{journal_data}\n\n")
                        # Vérification des critères
                        if (journal_data.get('status') == 'close' and
                            journal_data.get('source') == 'documents/invoices/doc_booked' and
                            journal_data.get('mandat_id')==space_id):
                            # Ajout de l'ID du document à ses données
                            journal_data['fb_doc_id'] = journal_doc.id
                            
                            # Ajout du document complet à la liste
                            filtered_documents.append(journal_data)
        
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
    
    def get_open_job_id(self,user_id,departement, space_id):
        """
        Recherche les job_id ouverts correspondant aux critères spécifiés.

        Args:
            space_id (str): L'ID de l'espace à rechercher dans le champ 'departement'.

        Returns:
            list: Une liste des job_id correspondant aux critères.
        """
        departement_index=['Admanager','EXbookeeper','Router','Bankbookeeper','APbookeeper','HRmanager']
        
        filtered_documents = [] 
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        
        # Itération sur tous les documents de la collection 'klk_vision'
        klk_vision_docs = self.db.collection(base_path).stream()
        
        for doc in klk_vision_docs:
            data = doc.to_dict()
            
            # Vérification si le document a le champ 'departement' égal à space_id
            chosen_departement=departement_index[departement]
            #print(chosen_departement)
            if data.get('departement') == chosen_departement:
                # Si c'est le cas, on cherche dans la collection 'journal' de ce document
                journal_docs = doc.reference.collection('journal').stream()
                #print(journal_docs)
                for journal_doc in journal_docs:
                    journal_data = journal_doc.to_dict()
                    #print(f"impression de journal data:{journal_data}\n\n")
                    # Vérification des critères
                    if (journal_data.get('status') == 'to_process' and
                        journal_data.get('source') == 'documents/accounting/invoices/doc_to_do' and
                        journal_data.get('mandat_id')==space_id):
                        # Ajout de l'ID du document à ses données
                        journal_data['fb_doc_id'] = journal_doc.id
                        
                        # Ajout du document complet à la liste
                        filtered_documents.append(journal_data)
        
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
            data['timestamp'] = datetime.now()
            
            
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
            job_data['timestamp'] = datetime.now()
            
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
            job_data['timestamp'] = datetime.now()

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
        Publie une notification sur Redis pour les events temps réel

        Args:
            collection_path (str): Chemin de la collection
            job_data (dict): Données du job
        """
        try:
            # Extraire user_id du chemin (format: clients/{user_id}/notifications)
            path_parts = collection_path.split('/')
            if len(path_parts) >= 2 and path_parts[0] == 'clients':
                user_id = path_parts[1]

                # Créer le payload pour la notification
                notification_payload = {
                    "type": "notif.job_updated",
                    "uid": user_id,
                    "timestamp": datetime.now().isoformat(),
                    "payload": {
                        "job_id": job_data.get("job_id"),
                        "batch_id": job_data.get("batch_id"),
                        "function_name": job_data.get("function_name"),
                        "status": job_data.get("status"),
                        "collection_path": collection_path
                    }
                }

                print(f"[DEBUG] Publishing notification for user {user_id}: {notification_payload.get('type')}")

                # Utiliser l'instance globale listeners_manager pour publier
                from app.main import listeners_manager
                if listeners_manager:
                    listeners_manager.publish(user_id, notification_payload)
                    print(f"[DEBUG] Notification published successfully for user {user_id}")
                else:
                    print(f"[WARNING] listeners_manager not available, cannot publish notification")

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
            job_data['timestamp'] = datetime.now()
            
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
            data['timestamp'] = datetime.now()
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
        # Générer un objet datetime pour le timestamp actuel


        log_entry['timestamp'] = datetime.now()
   
        # Appeler la fonction d'upload
        self.upload_to_firebase(log_entry)

    def upload_to_firebase(self,user_id, log_entry):
        #print(f"État de firebase_admin._apps avant l'accès à Firestore: {firebase_admin._apps}")
        pinnokio_func = log_entry['pinnokio_func']
        #print(f"impression de pinnokio_func:{pinnokio_func}")

        # Trouver le document correspondant dans 'klk_vision'
        matching_department_doc_id = self.find_matching_department(user_id,pinnokio_func)

        if matching_department_doc_id:
            # Accéder à la sous-collection 'journal' du document trouvé
            if user_id:
                base_path = f'clients/{user_id}/klk_vision'
            else:
                base_path = 'klk_vision'
            journal_ref = self.db.collection(base_path).document(matching_department_doc_id).collection('journal')
            # Ajouter le log_entry comme nouveau document dans 'journal'
            journal_ref.add(log_entry)
            print(f"Log ajouté dans 'journal' pour le département '{pinnokio_func}' dans 'klk_vision'.")
        else:
            print(f"Aucun département correspondant à '{pinnokio_func}' trouvé dans 'klk_vision'.")

    
    
    def find_matching_department(self,user_id, pinnokio_func):
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        klk_vision_ref = self.db.collection(base_path)
        departements = klk_vision_ref.stream()

        for departement in departements:
            doc_data = departement.to_dict()
            if doc_data.get('departement') == pinnokio_func:
                return departement.id  # Retourner l'ID du document correspondant

        return None  # Retourner None si aucune correspondance n'est trouvée
    
    def fetch_documents_from_firestore(self,collection_path, mandat_id_value, max_docs=None):
       
        collection_ref = self.db.collection(collection_path)
        query = collection_ref.where('mandat_id', '==', mandat_id_value)

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
                        
                        # Extraire les paramètres pour Router
                        if "Banker_param" in workflow_data:
                            router_param = workflow_data.get("Banker_param", {})
                            mandate["workflow_params"]["Banker_param"] = {
                                "banker_approval_required": router_param.get("banker_approval_required", False),
                                "banker_approval_thresholdworkflow": router_param.get("banker_approval_thresholdworkflow", 0),
                                "banker_communication_method": router_param.get("banker_communication_method", "")
                            }

                    else:
                        print(f"Workflow params not found for path: {workflow_params_path}")
                        # Nous conservons la structure vide initialisée plus haut
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
            
            # Vérifier si le mandat est actif
            if not doc_data.get('isactive', True):
                raise ValueError(f"Le mandat au chemin {mandate_path} n'est pas actif")
            
            # Construire la structure de base du mandat
            mandate = {
                "id": mandate_doc.id,
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
                "communication_log_type": doc_data.get('communication_log_type', "")
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
                else:
                    print(f"Workflow params not found for path: {workflow_params_path}")
                    
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
        Récupère les entrées du journal pour un mandat_id, une source et un département donnés,
        puis filtre les résultats pour ne garder que ceux correspondant aux job_id demandés,
        en produisant une liste principale avec des sous-listes.
        """
        # Initialiser la requête de base pour obtenir le document associé au département
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        router_query = self.db.collection(base_path).where('departement', '==', departement).limit(1).get()
        entries_with_paths = []  # Liste principale pour stocker les sous-listes des entrées

        for doc in router_query:
            router_doc = doc.to_dict()
            router_doc_id = doc.id  # ID du document trouvé pour 'departement'

            # Accéder à la sous-collection 'journal' du document trouvé
            journal_query = self.db.collection(base_path).document(router_doc_id).collection('journal')


            # Appliquer les filtres sur la sous-collection 'journal'
            journal_query = journal_query.where('mandat_id', '==', mandat_id).where('source', '==', source)

            # Exécuter la requête et itérer sur les résultats
            journal_entries = journal_query.stream()

            for entry in journal_entries:
                entry_data = entry.to_dict()  # Convertir l'entrée en dictionnaire

                # Vérifier si le statut de l'entrée est valide
                if entry_data.get('status') not in ['rejection']:
                    # Vérifier si le job_id de l'entrée est dans la liste des job_ids demandés
                    if entry_data.get('job_id') in job_ids:
                        document_path = f"{base_path}/{router_doc_id}/journal/{entry.id}"  # Construire le chemin d'accès
                        firebase_doc_id = entry.id  # Extrait l'ID du document Firestore

                        # Construire un dictionnaire pour l'entrée correspondante
                        entry_with_path = {
                            'data': entry_data,
                            'path': document_path,
                            'firebase_doc_id': firebase_doc_id
                        }

                        # Ajouter l'entrée sous forme de sous-liste dans la liste principale
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
        Récupère les entrées de la collection 'journal' correspondant au mandat_id, au département donné,
        et filtre les résultats selon job_ids et status (optionnels).
        """
        entries_with_paths = []  # Liste principale pour stocker les résultats

        # Itérer au travers des documents dans la collection 'klk_vision'
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        router_query = self.db.collection(base_path).stream()

        for doc in router_query:
            router_doc = doc.to_dict()

            # Vérifier si le champ 'departement' correspond à l'argument
            if router_doc.get('departement') == departement:
                router_doc_id = doc.id  # ID du document trouvé pour le département

                # Accéder à la collection 'journal' sous le document correspondant
                if user_id:
                    base_path = f'clients/{user_id}/klk_vision'
                else:
                    base_path = 'klk_vision'
                journal_query = self.db.collection(base_path).document(router_doc_id).collection('journal').stream()

                for entry in journal_query:
                    entry_data = entry.to_dict()  # Convertir l'entrée en dictionnaire

                    # Filtrer selon mandat_id
                    if entry_data.get('mandat_id') == mandat_id:
                        job_id = entry_data.get('job_id')
                        # Appliquer les filtres supplémentaires : job_ids et status
                        if (job_ids is None or (isinstance(job_id, str) and job_id in job_ids)) and (status is None or entry_data.get('status') == status):
                            document_path = f"klk_vision/{router_doc_id}/journal/{entry.id}"  # Construire le chemin d'accès
                            firebase_doc_id = entry.id  # ID du document Firestore

                            # Construire un dictionnaire pour l'entrée correspondante
                            entry_with_path = {
                                'data': entry_data,
                                'path': document_path,
                                'firebase_doc_id': firebase_doc_id
                            }

                            # Ajouter l'entrée dans la liste principale
                            entries_with_paths.append(entry_with_path)

        return entries_with_paths



    def fetch_journal_entries_by_mandat_id_without_source(self,user_id, mandat_id, departement):
        """
        Traitement de tout les id du département concerné a l'exclusion des id avec un 
        statut 'rejection'
        """
        # Initialiser la requête de base pour obtenir le document associé au département
        try:
            if user_id:
                base_path = f'clients/{user_id}/klk_vision'
            else:
                base_path = 'klk_vision'
            router_query = self.db.collection(base_path).where('departement', '==', departement).limit(1).get()
            entries_with_paths = []  # Liste pour stocker les données des entrées et leurs chemins

            for doc in router_query:
                router_doc = doc.to_dict()
                #print(f"Document trouvé: {router_doc}")
                router_doc_id = doc.id  # ID du document trouvé pour 'departement'

                # Accéder à la sous-collection 'journal' du document trouvé
                if user_id:
                    base_path = f'clients/{user_id}/klk_vision'
                else:
                    base_path = 'klk_vision'
                journal_query = self.db.collection(base_path).document(router_doc_id).collection('journal')

                # Appliquer les filtres sur la sous-collection 'journal'
                journal_query = journal_query.where('mandat_id', '==', mandat_id)

                # Exécuter la requête et itérer sur les résultats
                journal_entries = journal_query.stream()

                for entry in journal_entries:
                    entry_data = entry.to_dict()  # Convertir l'entrée en dictionnaire

                    # Vérifier si le status de l'entrée est 'rejection'
                    if entry_data.get('status')  not in ['rejection', 'pending']:
                        if user_id:
                            document_path=f'clients/{user_id}/klk_vision//{router_doc_id}/journal/{entry.id}'
                        else:
                            document_path = f"klk_vision/{router_doc_id}/journal/{entry.id}"  # Construire le chemin d'accès
                        
                        firebase_doc_id = entry.id  # Extrait l'ID du document Firestore

                        # Construire un dictionnaire pour chaque entrée, sans ceux ayant un status 'rejection'
                        entry_with_path = {
                            'data': entry_data,
                            'path': document_path,
                            'firebase_doc_id': firebase_doc_id
                        }

                        entries_with_paths.append(entry_with_path)

            print(f"Total documents récupérés: {len(entries_with_paths)}")
            return entries_with_paths

        except Exception as e:
            print(f"erreur lors de la récupraation des items traité par le router depuis firebase:{e}")

    def fetch_journal_entries_by_mandat_id(self,user_id, mandat_id, source, departement):
        """
        Traitement de tout les id du département concerné a l'exclusion des id avec un 
        statut 'rejection'
        """
        # Initialiser la requête de base pour obtenir le document associé au département
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        router_query = self.db.collection(base_path).where('departement', '==', departement).limit(1).get()
        entries_with_paths = []  # Liste pour stocker les données des entrées et leurs chemins

        for doc in router_query:
            router_doc = doc.to_dict()
            #print(f"Document trouvé: {router_doc}")
            router_doc_id = doc.id  # ID du document trouvé pour 'departement'

            # Accéder à la sous-collection 'journal' du document trouvé
            journal_query = self.db.collection(base_path).document(router_doc_id).collection('journal')

            # Appliquer les filtres sur la sous-collection 'journal'
            journal_query = journal_query.where('mandat_id', '==', mandat_id).where('source', '==', source)

            # Exécuter la requête et itérer sur les résultats
            journal_entries = journal_query.stream()

            for entry in journal_entries:
                entry_data = entry.to_dict()  # Convertir l'entrée en dictionnaire

                # Vérifier si le status de l'entrée est 'rejection'
                if entry_data.get('status')  not in ['rejection']:
                    document_path = f"klk_vision/{router_doc_id}/journal/{entry.id}"  # Construire le chemin d'accès
                    firebase_doc_id = entry.id  # Extrait l'ID du document Firestore

                    # Construire un dictionnaire pour chaque entrée, sans ceux ayant un status 'rejection'
                    entry_with_path = {
                        'data': entry_data,
                        'path': document_path,
                        'firebase_doc_id': firebase_doc_id
                    }

                    entries_with_paths.append(entry_with_path)

        return entries_with_paths

    def fetch_pending_journal_entries_by_mandat_id(self,user_id, mandat_id, source, departement):
        """
        Récupère uniquement les documents avec le statut 'pending' pour un département donné.
        Cette méthode est spécifiquement créée pour l'onglet Pending.
        """
        # Initialiser la requête de base pour obtenir le document associé au département
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        
        router_query = self.db.collection(base_path).where('departement', '==', departement).limit(1).get()
        entries_with_paths = []  # Liste pour stocker les données des entrées et leurs chemins

        for doc in router_query:
            router_doc = doc.to_dict()
            print(f"Document trouvé pour pending: {router_doc}")
            router_doc_id = doc.id  # ID du document trouvé pour 'departement'

            # Accéder à la sous-collection 'journal' du document trouvé
            journal_query = self.db.collection(base_path).document(router_doc_id).collection('journal')

            # Appliquer les filtres sur la sous-collection 'journal'
            # Filtrer par mandat_id, source ET status 'pending'
            journal_query = journal_query.where('mandat_id', '==', mandat_id)\
                                    .where('source', '==', source)\
                                    .where('status', '==', 'pending')

            # Exécuter la requête et itérer sur les résultats
            journal_entries = journal_query.stream()

            for entry in journal_entries:
                entry_data = entry.to_dict()  # Convertir l'entrée en dictionnaire
                
                # Double vérification que le status est bien 'pending'
                if entry_data.get('status') == 'pending':
                    document_path = f"klk_vision/{router_doc_id}/journal/{entry.id}"  # Construire le chemin d'accès
                    firebase_doc_id = entry.id  # Extrait l'ID du document Firestore

                    # Construire un dictionnaire pour chaque entrée pending
                    entry_with_path = {
                        'data': entry_data,
                        'path': document_path,
                        'firebase_doc_id': firebase_doc_id
                    }

                    entries_with_paths.append(entry_with_path)
                    print(f"Document pending trouvé: {entry_data.get('file_name', 'Unknown')} - Status: {entry_data.get('status')}")

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



    def delete_items_by_job_id(self,user_id, job_ids):
        if not isinstance(job_ids, list):
            job_ids = [job_ids]
        
        for job_id in job_ids:
            print(f"Suppression de la collection sous task_manager pour le job_id: {job_id}")
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            task_manager_ref = self.db.collection(base_path).document(job_id)
            
            # Étape 1: Parcours et suppression des sous-collections de task_manager
            subcollections = task_manager_ref.collections()
            for subcollection in subcollections:
                docs = subcollection.stream()
                for doc in docs:
                    print(f"Suppression du document {doc.id} dans la sous-collection {subcollection.id} de task_manager/{job_id}")
                    doc.reference.delete()

            # Suppression du document principal de task_manager
            task_manager_ref.delete()
            print(f"Document {job_id} supprimé de task_manager.")
            
            # Étape 2: Parcours et suppression dans klk_vision/journal
            if user_id:
                base_path = f'clients/{user_id}/klk_vision'
            else:
                base_path = 'klk_vision'
            klk_vision_ref = self.db.collection(base_path)
            docs = klk_vision_ref.stream()
            
            for doc in docs:
                journal_ref = doc.reference.collection('journal')
                journal_docs = journal_ref.where('job_id', '==', job_id).stream()
                
                for jdoc in journal_docs:
                    print(f"Suppression du document avec job_id: {job_id} dans klk_vision/{doc.id}/journal")
                    jdoc.reference.delete()
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
    def async_delete_items_by_job_id(self, user_id, job_ids):
        """Alias pour delete_items_by_job_id pour compatibilité RPC."""
        return self.delete_items_by_job_id(user_id, job_ids)

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
            clients = self.db.collection(base_path).where('client_name', '==', client_name).limit(1).get()
            if clients:
                client_doc_ref = clients[0].reference  # Référence au document du client trouvé
                
                # Recherche du document mandat par business_name dans la sous-collection 'mandates'
                mandates = client_doc_ref.collection('mandates').where('contact_space_name', '==', business_name).limit(1).get()
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

    def delete_client_if_no_mandates(self, user_id, client_name: str) -> tuple[bool, str]:
        """
        Supprime un client sous clients/{user_id}/bo_clients uniquement s'il n'a pas de sous-collection 'mandates'.

        Returns:
            (deleted, message)
            - deleted True si la suppression a été effectuée, False sinon
            - message explicatif (en anglais en cas de blocage)
        """
        try:
            if user_id:
                base_path = f"clients/{user_id}/bo_clients"
            else:
                base_path = "bo_clients"

            query = self.db.collection(base_path).where('client_name', '==', client_name).limit(1).get()
            if not query:
                return False, "Client not found."

            client_ref = query[0].reference

            # Check mandates existence
            mandates_cursor = client_ref.collection('mandates').limit(1).get()
            if mandates_cursor:
                return (
                    False,
                    "This client has mandates. Please delete the associated mandate(s) first before deleting the client."
                )

            # Safe delete: remove subcollections (if any other) then the document
            for subcoll in client_ref.collections():
                # If it's mandates, we already ensured it's empty; still process generically
                self._delete_collection_recursive(subcoll)

            client_ref.delete()
            return True, "Client deleted successfully."
        except Exception as e:
            print(f"Erreur delete_client_if_no_mandates: {e}")
            return False, "Unexpected error while deleting client."

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
            'datetime': datetime.now(),
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
        for message_key, message_data in messages.items():
            datetime_str = message_data['datetime'].strftime('%Y-%m-%d %H:%M:%S')

            # Extraction des détails du 'sent_to' pour afficher proprement le service et la motivation
            service = message_data['sent_to'].get('service', 'N/A')
            motivation = message_data['sent_to'].get('motivation', '')

            # Construire la vue formatée
            formatted_view += (f"Message {message_key} envoyé le {datetime_str}:\n"
                            f"Expéditeurs: {message_data['send_from']}\n"
                            f"Receveur: {service}\n"
                            f"Motivation: {motivation}\n")
                           

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

    def x_download_auditor_review_on_step(self,user_id, job_id):
        """
        Télécharge un rapport d'audit depuis la sous-collection 'internal_message' 
        dans un document spécifié par job_id dans la collection 'task_manager'.
        
        Args:
            job_id (str): L'ID du travail pour lequel le rapport d'audit est récupéré.
            
        Returns:
            str: Le texte du rapport d'audit dans le champ 'step_review'.
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
            
            # Récupérer le document 'audit_report'
            audit_report_doc = audit_report_ref.get()
            
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
            
            if audit_report_doc.exists:
                # Extraire et retourner le texte de l'audit
                audit_report_data = audit_report_doc.to_dict()
                audit_report_text = audit_report_data.get("step_review", "")
                
                # Vérifier si 'step_review' est un dictionnaire avant de formater
                if isinstance(audit_report_text, dict):
                    audit_report_text = format_audit_report(audit_report_text)
                else:
                    print(f"step_review n'est pas un dictionnaire: {audit_report_text}")
                
                return audit_report_text
            else:
                print("Le document 'audit_report' n'existe pas.")
                return None
                
        except Exception as e:
            print(f"Erreur lors du téléchargement du rapport d'audit: {e}")
            return None

    def download_auditor_review_on_step(self,user_id, job_id):
        """
        Télécharge un rapport d'audit depuis la sous-collection 'internal_message'.
        
        Args:
            job_id (str): L'ID du travail
            
        Returns:
            dict: Structure de données contenant les étapes et leurs rapports
        """
        try:
            if user_id:
                document_path = f'clients/{user_id}/task_manager'
            else:
                document_path = f"task_manager"
                
            job_doc_ref = self.db.collection(document_path).document(job_id)
            internal_message_ref = job_doc_ref.collection('internal_message')
            audit_report_ref = internal_message_ref.document('audit_report')
            audit_report_doc = audit_report_ref.get()
            
            if audit_report_doc.exists:
                audit_report_data = audit_report_doc.to_dict()
                step_review = audit_report_data.get("step_review", {})
                
                if isinstance(step_review, dict):
                    # Transformation au format attendu
                    formatted_data = {
                        'step_name': {}
                    }
                    
                    for step_key, step_value in step_review.items():
                        for step_name, details in step_value.items():
                            formatted_data['step_name'][step_name] = {
                                'report': details.get('report', '')
                            }
                    
                    print(f"Données formatées: {formatted_data}")
                    return formatted_data
                else:
                    print(f"step_review n'est pas un dictionnaire: {step_review}")
                    return {'step_name': {}}
                    
            else:
                print("Le document 'audit_report' n'existe pas.")
                return {'step_name': {}}
                
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
        Redémarre un job en supprimant les données initiales et le rapport d'audit.
        
        Args:
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
            if not job_doc_ref.get().exists:
                print(f"Error: No job found with id {job_id}")
                return False
            
            job_doc_ref.set({
            'APBookkeeper_step_status': 'DOCUMENT_EXTRACTION'
            })
            print(f"Successfully updated step status for job {job_id}")

            # Accès à la sous-collection 'document'
            document_collection_ref = job_doc_ref.collection('document')
            
            # Supprimer initial_data
            initial_data_ref = document_collection_ref.document('initial_data')
            if initial_data_ref.get().exists:
                initial_data_ref.delete()
                print(f"Successfully deleted initial_data for job {job_id}")

            audit_report_path=job_doc_ref.collection('internal_message')  
            # Supprimer audit_report
            audit_report_ref = audit_report_path.document('audit_report')
            if audit_report_ref.get().exists:
                audit_report_ref.delete()
                print(f"Successfully deleted audit_report for job {job_id}")
            
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
            timestamp = datetime.now().isoformat()
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
        clients_query = self.db.collection(document_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        
        mandates_query = self.db.collection(document_path).where('contact_space_id', '==', contact_space_id).get()
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
        
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
            update_data['timestamp'] = datetime.now()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
        
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
        client_id = next((client_doc.id for client_doc in clients_query), None)

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
        client_id = next((client_doc.id for client_doc in clients_query), None)
        
        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer l'ID du mandat spécifique en utilisant 'contact_space_id'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
        client_id = next((client_doc.id for client_doc in clients_query), None)

        if not client_id:
            raise ValueError("Client non trouvé avec l'UUID fourni")

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id' pour obtenir l'ID du mandat
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path = f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        from datetime import datetime as _dt
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', mandat_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(f'bo_clients/{client_id}/mandates').where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
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
        mandates_query = self.db.collection(base_path).where('contact_space_id', '==', contact_space_id).get()
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
        clients_query = self.db.collection(base_path).where('client_uuid', '==', client_uuid).limit(1).get()
        for client_doc in clients_query:
            client_data = client_doc.to_dict()
            client_id = client_doc.id
            full_profile.update(client_data)  # Ajouter les données du client au profil complet

        # Étape 2: Récupérer le mandat spécifique en utilisant 'contact_space_id'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients/{client_id}/mandates'
        else:
            base_path =f'bo_clients/{client_id}/mandates'
        mandates_query = self.db.collection(f'bo_clients/{client_id}/mandates').where('contact_space_id', '==', contact_space_id).get()
        for mandate_doc in mandates_query:
            mandate_data = mandate_doc.to_dict()
            mandate_id = mandate_doc.id
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
        # Collection principale
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        main_collection = self.db.collection(base_path)
        
        # Résultats à retourner
        filtered_journals = []
        document_paths = []  # Liste pour stocker les chemins complets des documents

        # Itérer dans les collections sous 'departement'
        departments = main_collection.list_documents()  # Cela liste les références de documents dans 'klk_vision'
        for department_ref in departments:
            # Accéder à la sous-collection 'journal' de chaque 'departement'
            journals = department_ref.collection('journal').where('mandat_id', '==', mandat_id).stream()

            # Itérer sur les documents filtrés et les ajouter à la liste des résultats
            for journal in journals:
                journal_data = journal.to_dict()
                # Construire le chemin complet du document
                document_path = f"{department_ref.path}/journal/{journal.id}"
                filtered_journals.append(journal_data)
                document_paths.append(document_path)  # Ajouter le chemin complet du document à la liste
        
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

    def x_get_departement_job_path(self,user_id, departement_name):
        # Rechercher le document avec le champ 'departement' correspondant à departement_name
        if user_id:
            base_path = f'clients/{user_id}/klk_vision'
        else:
            base_path = 'klk_vision'
        departement_query = self.db.collection(base_path).where('departement', '==', departement_name).limit(1).stream()

        # Obtenir le document correspondant
        for doc in departement_query:
            departement_doc = doc
            
            # Vérifier si le document existe
            if departement_doc.exists:
                # Construire le chemin vers la sous-collection 'journal'
                journal_path = f"klk_vision/{departement_doc.id}/journal"
                # Vérifier si la sous-collection 'journal' existe
                if not self.db.collection(journal_path).get():
                    # Si la sous-collection 'journal' n'existe pas, la créer
                    self.db.collection(journal_path).document()  # Cela crée un document vide dans la sous-collection
                return journal_path
            else:
                print(f"Aucun document avec le département '{departement_name}' n'a été trouvé.")
                return None

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

    def x_get_client_list(self,user_id):
        """Récupère la liste des noms de clients."""
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        clients_ref = self.db.collection(base_path)
        docs = clients_ref.stream()
        client_list = [doc.to_dict().get('client_name') for doc in docs]
        return client_list

    def x_create_form_data(self):
        """Crée le form_data pour la carte Google Chat sans arguments."""
        client_list = self.get_client_list()
        mandate_data = self.get_mandate_data()
        form_data = {
            'client_list': client_list,
            'legal_name': mandate_data.get('legal_name', ''),
            'business_country': mandate_data.get('business_country', ''),
            'business_name': mandate_data.get('business_name', ''),
            'business_address': mandate_data.get('business_address', ''),
            'isactive': mandate_data.get('isactive', False)
        }
        return form_data

    def x_get_mandate_data_by_mandat_space_id(self,user_id, space_id):
        """Récupère les données de mandat par l'ID de l'espace de contact.

        Args:
            space_id (str): L'identifiant de l'espace de contact à rechercher.

        Returns:
            dict: Les données du mandat correspondant à l'ID de l'espace de contact, ou None si non trouvé.
        """
        # Recherche dans la collection 'klk_vision'
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        klk_vision_ref = self.db.collection(base_path)
        # Itérer sur chaque document dans 'klk_vision'
        for client_doc in klk_vision_ref.stream():
            # Accès à la sous-collection 'mandates' de ce document
            mandates_ref = client_doc.reference.collection('mandates')
            # Recherche des documents dans 'mandates' où 'contact_space_id' correspond à 'space_id'
            matches = mandates_ref.where('contact_space_id', '==', space_id).stream()

            # Si un document correspondant est trouvé, construit les données de retour avec le chemin complet
            for match in matches:
                mandate_data = match.to_dict()
                # Ajouter le chemin complet du document trouvé à 'mandate_data'
                mandate_data['full_path'] = match.reference.path
                return mandate_data

        # Si aucun document correspondant n'est trouvé, retourne None
        return None

    def x_get_combined_mandate_and_client_data(self, space_id):
        mandate_data = self.get_mandate_data_by_mandat_space_id(space_id)
        if mandate_data:
            # Découper le 'full_path' pour obtenir le chemin du document client
            client_path = '/'.join(mandate_data['full_path'].split('/')[:2])
            
            # Récupérer le document client en utilisant le chemin
            client_doc_ref = self.db.document(client_path)
            client_doc = client_doc_ref.get()
            if client_doc.exists:
                client_data = client_doc.to_dict()
                
                # Combiner les données du mandat avec celles du client
                combined_data = {**client_data, **mandate_data}
                # Optionnellement, supprimer 'full_path' si vous ne voulez pas l'inclure dans le résultat final
                combined_data.pop('full_path', None)
                
                return combined_data

        # Retourner None si les données du mandat ne sont pas trouvées ou si le document client n'existe pas
        return None

    def x_delete_document_in_journal_by_name(self,user_id, file_name, collection_name='ApBookeeper'):
        """Supprime les documents par nom dans une collection spécifiée."""
        # Accéder à la collection klk_vision et à la sous-collection spécifiée
        if user_id:
            base_path = f'clients/{user_id}/bo_clients'
        else:
            base_path = 'bo_clients'
        collection_ref = self.db.collection(base_path).document(collection_name).collections()
        
        for sub_collection in collection_ref:
            # Itérer sur chaque document de la sous-collection
            docs = sub_collection.where('file_name', '==', file_name).stream()
            for doc in docs:
                # Supprimer le document trouvé
                doc.reference.delete()
                print(f"Document {doc.id} supprimé avec succès.")

    def delete_document_and_subcollections(self, document_ref):
        """Supprime un document et toutes ses sous-collections."""
        # Supprimer les documents dans les sous-collections
        for sub_collection in document_ref.collections():
            sub_docs = sub_collection.stream()
            for sub_doc in sub_docs:
                sub_doc.reference.delete()
        
        # Supprimer le document lui-même
        document_ref.delete()
        print(f"Document et ses sous-collections supprimés avec succès : {document_ref.id}")

    
    def x_delete_document_in_journal_by_mandat_id(self,user_id, mandat_id, departements):
        """Supprime les documents par mandat_id dans la sous-collection 'journal' pour un ou plusieurs départements spécifiés."""
        # Convertir le nom du département en liste s'il ne s'agit pas déjà d'une liste
        if not isinstance(departements, list):
            departements = [departements]

        if not departements:
            print("Liste des noms de département non spécifiée.")
            return

        for departement in departements:
            if departement is None:
                print("Nom de département non spécifié dans la liste.")
                continue

            # Itérer sur chaque document de la collection klk_vision correspondant au département actuel
            if user_id:
                base_path = f'clients/{user_id}/klk_vision'
            else:
                base_path = 'klk_vision'
            docs = self.db.collection(base_path).where('departement', '==', departement).stream()

            document_found = False
            for doc in docs:
                document_found = True
                document_ref = doc.reference
                journal_collection_ref = document_ref.collection('journal')

                try:
                    # Itérer sur chaque document de la sous-collection 'journal'
                    journal_docs = journal_collection_ref.where('mandat_id', '==', mandat_id).stream()
                    for journal_doc in journal_docs:
                        # Supprimer le document trouvé
                        self.delete_document_and_subcollections(journal_doc.reference)
                        
                except Exception as e:
                    print(f"Erreur lors de la suppression des documents dans le département {departement}: {e}")

            if not document_found:
                print(f"Le département '{departement}' n'existe pas ou la collection 'journal' n'existe pas sous ce département.")

    def x_delete_documents_by_space_name(self,user_id, space_name):
        """
        Supprime tous les documents dans la collection task_manager qui ont 
        le champ Document_information.mandat_name correspondant au space_name spécifié.
        
        Args:
            space_name (str): Le nom de l'espace à rechercher dans le champ Document_information.mandat_name
            
        Returns:
            dict: Un dictionnaire contenant le nombre de documents supprimés et les erreurs éventuelles
        """
        result = {
            "success": True,
            "documents_deleted": 0,
            "errors": []
        }
        
        try:
            # Détermination du chemin de la collection en fonction de user_id
            if user_id:
                base_path = f'clients/{user_id}/task_manager'
            else:
                base_path = 'task_manager'
            
            print(f"Recherche des documents avec mandat_name='{space_name}' dans la collection {base_path}")
            
            # Récupération des documents qui contiennent Document_information
            # Note: Firestore ne permet pas de requêter directement sur des sous-champs imbriqués 
            # avec des opérateurs de condition, donc nous devons récupérer tous les documents
            # et filtrer manuellement
            docs = self.db.collection(base_path).stream()
            
            document_count = 0
            for doc in docs:
                document_count += 1
                doc_data = doc.to_dict()
                
                # Vérification de l'existence des champs nécessaires
                if 'Document_information' in doc_data and isinstance(doc_data['Document_information'], dict):
                    doc_info = doc_data['Document_information']
                    
                    # Vérification si mandat_name correspond au space_name
                    if 'mandat_name' in doc_info and doc_info['mandat_name'] == space_name:
                        try:
                            # Suppression du document et de ses sous-collections éventuelles
                            if hasattr(self, 'delete_document_and_subcollections'):
                                self.delete_document_and_subcollections(doc.reference)
                            else:
                                # Suppression simple si la méthode héritée n'est pas disponible
                                doc.reference.delete()
                                
                            result["documents_deleted"] += 1
                            print(f"Document {doc.id} supprimé avec succès")
                        except Exception as e:
                            error_msg = f"Erreur lors de la suppression du document {doc.id}: {str(e)}"
                            result["errors"].append(error_msg)
                            print(error_msg)
                            result["success"] = False
            
            if document_count == 0:
                print(f"Aucun document trouvé dans la collection {base_path}")
            elif result["documents_deleted"] == 0:
                print(f"Aucun document avec mandat_name='{space_name}' trouvé dans {document_count} documents")
            else:
                print(f"{result['documents_deleted']} document(s) supprimé(s) sur {document_count} document(s) examiné(s)")
            
            return result
            
        except Exception as e:
            error_msg = f"Erreur lors de la recherche/suppression des documents: {str(e)}"
            print(error_msg)
            result["success"] = False
            result["errors"].append(error_msg)
            return result

    def x_delete_messages_in_internal_message_by_id(self,user_id, document_id):
        """Supprime tous les documents dans la sous-collection 'messages' de 'internal_message' pour un document spécifié."""
        
        # Construire le chemin de référence au document spécifié dans task_manager
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        document_ref = self.db.collection(base_path).document(document_id)
        
        # Accéder à la sous-collection 'internal_message', puis à 'messages'
        messages_ref = document_ref.collection('internal_message').document('messages')
        
        try:
            # Itérer sur chaque document dans la sous-collection 'messages' et les supprimer
            messages_docs = messages_ref.collection('messages').stream()
            for message_doc in messages_docs:
                message_doc.reference.delete()
            print(f"Tous les messages ont été supprimés pour le document ID: {document_id}")
        except Exception as e:
            print(f"Erreur lors de la suppression des messages pour le document ID {document_id}: {e}")

    def x_update_document_names(self, parent_collection, document_id):
        journal_ref = self.db.collection(parent_collection).document(document_id).collection('journal')
        docs = journal_ref.stream()

        for doc in docs:
            doc_dict = doc.to_dict()
            if 'document_name' in doc_dict:
                document_name_value = doc_dict['document_name']
                # Supprimer 'document_name'
                journal_ref.document(doc.id).update({'document_name': firestore.DELETE_FIELD})
                # Ajouter 'file_name' avec la valeur de 'document_name'
                journal_ref.document(doc.id).update({'file_name': document_name_value})

    def x_update_status_in_collection(self, parent_collection, document_id):
        """ status_Index=0 routed, 1 to_process , 2 rejection  3 pending"""
        status_index=['routed','to_process','rejection','pending']
        journal_ref = self.db.collection(parent_collection).document(document_id).collection('journal')
        docs = journal_ref.stream()

        for doc in docs:
            doc_dict = doc.to_dict()
            if doc_dict.get('status') == 'routed':
                # Mettre à jour le champ 'status' avec la nouvelle valeur 'routed'
                journal_ref.document(doc.id).update({'status': 'to_process'})

    def x_fetch_internal_messages_by_job_id(self,user_id, job_id):
        # Accéder à la collection 'task_manager' et au document spécifié par 'job_id'
        if user_id:
            base_path = f'clients/{user_id}/task_manager'
        else:
            base_path = 'task_manager'
        document_reference = self.db.collection(base_path).document(job_id)
        
        # Accéder au document '/messages' dans le chemin 'internal_message'
        messages_document_reference = document_reference.collection('internal_message').document('messages')
        
        # Récupérer le document '/messages'
        messages_document = messages_document_reference.get()  # Cela renvoie un document
        
        # Vérifier si le document existe avant de tenter d'extraire ses données
        if messages_document.exists:
            # Extraire les données du document Firestore
            messages_data = messages_document.to_dict()  # Convertir les données du document Firestore en dictionnaire Python
            
            # Vous pourriez avoir besoin d'ajuster cette partie en fonction de la structure de vos données dans le document 'messages'
            # Par exemple, si 'messages' contient un champ 'messages_list' qui est une liste de messages
            messages_list = messages_data.get('messages_list', [])
            
            return messages_data
        else:
            # Retourner une liste vide si le document '/messages' n'existe pas
            return []

    def x_get_thread_id(self,user_id, job_id, space_id):
        # Construit le chemin du document en utilisant job_id
        if user_id:
            base_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            base_path = f'task_manager/{job_id}'
        #document_path = f"task_manager/{job_id}"
        document_ref = self.db.document(base_path)
        document = document_ref.get()
        
        if document.exists:
            # Vérifie si le champ 'gchat_thread_id' existe dans le document
            doc_data = document.to_dict()
            if 'gchat_thread_id' in doc_data:
                return space_id +"/threads/"+ doc_data['gchat_thread_id']
            else:
                print("Le champ 'gchat_thread_id' n'existe pas dans le document spécifié.")
                return space_id
        else:
            print(f"Le document spécifié avec job_id {job_id} n'existe pas.")
            return space_id

    def x_update_thread_id_by_job_id(self,user_id,job_id, thread_id):
        if user_id:
            base_path = f'clients/{user_id}/task_manager/{job_id}'
        else:
            base_path = f'task_manager/{job_id}'
        #document_path = f"task_manager/{job_id}"
        document_ref = self.db.document(base_path)
        document = document_ref.get()

        # Vérifie si le document existe avant d'essayer de le mettre à jour
        if document.exists:
            if thread_id:
                document_ref.update({'gchat_thread_id': thread_id})
                print(f"Le champ 'gchat_thread_id' dans le document {base_path} a été mis à jour avec succès avec la valeur {thread_id}.")
            else:
                print("Erreur : 'thread_id' n'a pas de valeur valide.")
        else:
            print(f"Erreur : Le document avec job_id {job_id} n'existe pas. Aucune mise à jour effectuée.")
            #Ici, vous pourriez choisir de créer le document s'il n'existe pas, par exemple :
            if thread_id:
                document_ref.set({'gchat_thread_id': thread_id})
                print(f"Document créé avec 'gchat_thread_id' = {thread_id}.")

    
    def create_or_get_working_doc_2(self, mandate_path):
        """
        Crée ou récupère le document 'pending_item_docsheet' dans la collection 'working_doc'
        en utilisant directement le chemin du mandat.
        """
        try:
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
            recipient_id (str): ID du destinataire
            message_data (dict): Données du message
            
        Returns:
            bool: True si l'envoi a réussi
        """
        try:
            # Créer la référence au nœud des messages directs du destinataire
            direct_messages_path = f"clients/{recipient_id}/direct_message_notif"
            messages_ref = self.db.child(direct_messages_path)
            
            # Ajouter des informations par défaut si nécessaires
            if 'sender_id' not in message_data:
                message_data['sender_id'] = user_id
                
            if 'timestamp' not in message_data:
                message_data['timestamp'] = str(datetime.now())
            
            # Envoyer le message
            messages_ref.push(message_data)
            print(f"✅ Message direct envoyé à l'utilisateur {recipient_id}")
            return True
            
        except Exception as e:
            print(f"❌ Erreur lors de l'envoi du message direct: {e}")
            return False

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
            print(f"🗑️ Suppression du message direct {message_id} pour l'utilisateur {user_id}")
            
            # Chemin du message
            message_path = f"clients/{user_id}/direct_message_notif/{message_id}"
            
            # Supprimer le message
            message_ref = self.db.child(message_path)
            message_ref.delete()
            print(f"✅ Message {message_id} supprimé avec succès")
            return True
                
        except Exception as e:
            print(f"❌ Erreur lors de la suppression du message direct: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
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
                test_result = test_ref.set({"timestamp": str(datetime.now())})
            except AttributeError:
                # Essayer avec db.reference
                test_ref = firebase_admin.db.reference('connection_test', url=database_url)
                test_result = test_ref.set({"timestamp": str(datetime.now())})
            
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
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            str: Chemin complet vers le thread
        """
        if mode == 'chats':
            return f'{space_code}/chats/{thread_key}'
        else:  # Par défaut, utilisez 'job_chats'
            return f'{space_code}/job_chats/{thread_key}'
    
    def create_chat(self,user_id: str, space_code: str, thread_name: str, mode: str = 'chats') -> dict:
        """
        Crée un nouveau thread de chat dans Firebase Realtime Database.
        
        Args:
            space_code (str): Code de l'espace (typiquement le companies_search_id)
            thread_name (str): Nom du nouveau thread/chat
            mode (str): Mode de groupement ('job_chats' ou 'chats')
            
        Returns:
            dict: Informations sur le thread créé (thread_key, success, etc.)
        """
        try:
            
            
            # Générer un thread_key unique basé sur le timestamp et le nom
            thread_key = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9]', '_', thread_name)}"
            
            # Construire le chemin complet pour le nouveau thread
            path = f"{space_code}/{mode}/{thread_key}"
            
            # Créer la structure initiale du thread
            thread_data = {
                "thread_name": thread_name,
                "created_at": str(datetime.now()),
                "created_by": user_id,
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
                "name": thread_name,
                "last_activity": str(datetime.now()),
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
                'timestamp': str(datetime.now()),
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

    
    
    async def listen_realtime_channel(self, space_code: str, thread_key: str, callback, mode: str = 'job_chats') -> None:
        """
        Configure un écouteur pour les messages d'un canal spécifique.
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread/conversation
            callback: Fonction asynchrone à appeler lors de la réception d'un message
            mode (str): Mode de groupement ('job_chats' ou 'chats')
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
            main_loop = asyncio.get_event_loop()
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
            
                    # Vérifier si le message est déjà en cours de traitement
                    if message_id in self.processed_messages:
                        print(f"⏳ Message {message_id} en cours de traitement, ignoré")
                        return
                   
                    message_type = event.data.get('message_type')
                    is_unread = not event.data.get('read', True)
                    
                    if (message_type in ['MESSAGE', 'CARD','TOOL','CMMD'] and 
                        is_unread):
                        self.processed_messages.add(message_id)
                        message_data = {
                            'id': event.path.lstrip('/'),
                            **event.data
                        }
                        #print(f"📝 Message Pinnokio formaté: {message_data}")
                        # Marquer comme lu AVANT le traitement
                        messages_ref.child(message_id).update({'read': True})
                        print(f"✅ Message {message_id} marqué comme lu")
                        try:
                            # Utiliser la boucle principale pour planifier le callback
                            future = asyncio.run_coroutine_threadsafe(
                                callback(message_data), 
                                main_loop
                            )
                            future.result(timeout=1)
                            # Marquer comme lu après le traitement réussi
                            
                            
                        except asyncio.TimeoutError:
                            print("⚠️ Callback timeout - continuer en arrière-plan")    
                        except Exception as e:
                            print(f"❌ Erreur dans le callback: {e}")
                            self.processed_messages.add(message_id)
                        

                    else:
                        print(f"⏭️ Message ignoré - Type: {message_type}, Lu: {not is_unread}")
            
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
                'timestamp': str(datetime.now()),
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
                        'timestamp': str(datetime.now()),
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


