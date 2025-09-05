import firebase_admin
from firebase_admin import db
from typing import Dict, List, Optional
from datetime import datetime,timezone
import json
import asyncio
from typing import Dict, List, Optional, Callable,Any
import json
from google.cloud import secretmanager
import os
from dotenv import load_dotenv
import time
import re
# AJOUTER ces imports
import queue
import threading

def get_secret(secret_name):
    try:
        client = secretmanager.SecretManagerServiceClient()
        project_id=os.getenv('GOOGLE_PROJECT_ID')
        secret_version = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": secret_version})
        #print(f"Clé secrete récupérer avec succes {secret_name}")
        return response.payload.data.decode('UTF-8')
    except Exception as e:
        print(f"Erreur lors de la récupération du secret: {e}")
        raise

load_dotenv()

class FirebaseRealtimeChat:
    def __init__(self, user_id: str = None):
        """
        Initialise la connexion à Firebase Realtime Database.
        Args:
            user_id (str): Identifiant de l'utilisateur
        """
        self.user_id = user_id
        self.processed_messages = set()
        # URL de la base de données Realtime
        database_url = "https://pinnokio-gpt-default-rtdb.europe-west1.firebasedatabase.app/"
        
        # Initialisation via singleton centralisé
        try:
            try:
                from .firebase_app_init import get_firebase_app
            except ImportError:
                from pinnokio_app.code.tools.firebase_app_init import get_firebase_app

            app = get_firebase_app()
            self.db = firebase_admin.db.reference('/', url=database_url, app=app)
        
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

    
    def send_direct_message(self, recipient_id: str, message_data: dict):
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
                message_data['sender_id'] = self.user_id
                
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

    def x_get_unread_direct_messages(self, user_id: str, companies: List[Dict] = None) -> List[Dict]:
        """
        Récupère les messages directs non lus pour un utilisateur.
        
        Args:
            user_id (str): ID de l'utilisateur
            companies (List[Dict], optional): Liste des entreprises pour récupérer les noms
        
        Returns:
            List[Dict]: Liste des messages directs non lus
        """
        try:
            #print(f"📨 Récupération des messages directs non lus pour l'utilisateur: {user_id}")
            
            # Chemin vers les messages directs de l'utilisateur
            messages_path = f"clients/{user_id}/direct_message_notif"
            print(f"📍 Chemin d'accès: {messages_path}")
            
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
                print("ℹ️ Aucun message trouvé")
                return []
            
            # Créer un dictionnaire pour mapper les ID d'entreprises à leurs noms
            company_names = {}
            if companies:
                for company in companies:
                    company_id = company.get("contact_space_id", "")
                    company_name = company.get("name", "")
                    if company_id and company_name:
                        company_names[company_id] = company_name
            
            # Formater les messages
            formatted_messages = []
            for msg_id, msg_data in messages_data.items():
                if isinstance(msg_data, dict):
                    # Ajouter l'ID du message
                    msg_data["doc_id"] = msg_id
                    
                    # Ajouter le nom de l'entreprise si disponible
                    collection_id = msg_data.get("collection_id", "")
                    msg_data["collection_name"] = company_names.get(collection_id, "")
                    
                    formatted_messages.append(msg_data)
            
            print(f"✅ {len(formatted_messages)} messages directs récupérés")
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
    
    def create_chat(self, space_code: str, thread_name: str, mode: str = 'chats') -> dict:
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
                "created_by": self.user_id,
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
                'sender_id': self.user_id,
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

    def send_tools_list(self, space_code: str, thread_key: str, tools_config: List[Dict],mode: str = 'job_chats') -> bool:
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
                'sender_id': self.user_id,
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
                        'sender_id': self.user_id,
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


class X_FirebaseRealtimeChat:
    def __init__(self, user_id: str = None):
        """
        Initialise la connexion à Firebase Realtime Database.
        Args:
            user_id (str): Identifiant de l'utilisateur
        """
        self.user_id = user_id
        self.processed_messages = set()
        # URL de la base de données Realtime
        database_url = "https://pinnokio-gpt-default-rtdb.europe-west1.firebasedatabase.app/"
        
        # Vérifier si Firebase est déjà initialisé
        try:
            if not firebase_admin._apps:
                try:
                    # Initialiser Firebase Admin SDK avec l'URL de la base de données Realtime
                    cred = firebase_admin.credentials.Certificate(json.loads(get_secret(os.getenv('FIRESTORE_SERVICE_ACCOUNT_SECRET'))))
                    firebase_admin.initialize_app(
                        cred,
                        {
                            'databaseURL': database_url
                        }
                    )
            
                except Exception as e:
                    print(f"Erreur lors de l'initialisation de Firebase: {e}")
                    raise

            elif 'default' not in firebase_admin._apps:
                # Si une autre app existe mais pas la default, initialiser avec un nom spécifique
                cred = firebase_admin.credentials.Certificate(json.loads(get_secret(os.getenv('FIRESTORE_SERVICE_ACCOUNT_SECRET'))))
                firebase_admin.initialize_app(
                    cred,
                    {
                        'databaseURL': database_url
                    },
                    name='default'
                )
            
            # Initialiser la référence à la base de données Realtime
            self.db = firebase_admin.db.reference('/', url=database_url)
        
        except Exception as e:
            print(f"Erreur lors de l'initialisation de Firebase: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def get_all_threads(self, space_code: str) -> Dict[str, Dict]:
        """
        Récupère tous les threads disponibles dans un espace spécifique.
        
        Args:
            space_code (str): Code de l'espace (typiquement le companies_search_id)
            
        Returns:
            Dict[str, Dict]: Dictionnaire des threads avec leurs métadonnées
        """
        try:
            print(f"📚 Récupération de tous les threads pour l'espace: {space_code}")
            
            # Référence à l'espace spécifié
            space_ref = self.db.child(f'{space_code}')
            
            # Récupérer tous les nœuds enfants qui sont des threads
            threads = space_ref.get()
            
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
        space_code: str, 
        thread_key: str, 
        action: str,
        additional_data: dict = None
    ) -> bool:
        """
        Envoie un message d'action speeddial via Firebase Realtime.
        Args:
            space_code (str): Identifiant de l'espace
            thread_key (str): Identifiant du thread/conversation
            action (str): Type d'action speeddial (ex: 'TERMINATE')
            additional_data (dict, optional): Données supplémentaires à inclure
        Returns:
            bool: True si l'envoi a réussi
        """
        try:
            message_data = {
                'timestamp': str(datetime.now()),
                'sender_id': self.user_id,
                'message_type': 'SPEEDDIAL',
                'action': action,
                'read': False
            }

            # Ajouter des données supplémentaires si fournies
            if additional_data:
                message_data.update(additional_data)

            # Créer la référence au chemin des messages
            messages_ref = self.db.child(f'{space_code}').child(f'{thread_key}').child('messages')
            print(f"Envoi d'action speeddial vers: {space_code}/{thread_key}/messages")
            
            # Envoyer le message
            messages_ref.push(message_data)
            print(f"Action speeddial {action} envoyée avec succès")
            return True

        except Exception as e:
            print(f"Erreur lors de l'envoi de l'action speeddial: {e}")
            return False

    def erase_chat(self, space_code: str, thread_key: str) -> bool:
        """
        Supprime tous les messages d'un canal spécifique.
        
        Args:
            space_code (str): Code de l'espace de discussion
            thread_key (str): Identifiant du thread/conversation
        
        Returns:
            bool: True si la suppression a réussi, False sinon
        """
        try:
            print(f"🗑️ Début de la suppression du chat")
            print(f"📍 Espace: {space_code}")
            print(f"🧵 Thread: {thread_key}")
            
            # Obtenir la référence au nœud des messages
            messages_ref = self.db.child(f'{space_code}').child(f'{thread_key}').child('messages')
            
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
    
    
    def watch_chat(self, chat_id: str, callback) -> None:
        """
        Écoute les changements dans un chat spécifique.
        Args:
            chat_id (str): Identifiant du chat
            callback: Fonction à appeler lors de la réception d'un nouveau message
        """
        try:
            chat_ref = self.db.child(f'chats/{chat_id}/messages')
            
            def on_change(event):
                if event.data:  # Vérifie si des données sont présentes
                    callback(event.data)
            
            # Mettre en place l'écouteur
            chat_ref.listen(on_change)
        except Exception as e:
            print(f"Erreur lors de l'écoute du chat: {e}")


    async def listen_realtime_channel(self, space_code: str, thread_key: str, callback) -> None:
        """
        Configure un écouteur pour les messages d'un canal spécifique.
        Args:
            space_code (str): Code de l'espace
            thread_key (str): Identifiant du thread/conversation
            callback: Fonction asynchrone à appeler lors de la réception d'un message
        """
        try:
            print(f"🚀 Démarrage de l'écoute Firebase Realtime")
            print(f"📍 Espace: {space_code}")
            print(f"🧵 Thread: {thread_key}")
            
            messages_ref = self.db.child(f'{space_code}').child(f'{thread_key}').child('messages')
            print(f"📡 Référence créée: {messages_ref.path}")
            main_loop = asyncio.get_event_loop()
            # Utiliser un ensemble pour suivre les messages en cours de traitement

            def on_message(event):
                print(f"\n🔔 Événement reçu:")
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

    def send_realtime_message_structured(
        self, 
        space_code: str, 
        thread_key: str, 
        text: str=None,
        message_type: str = "MESSAGE_PINNOKIO",
        message_data: dict = None
    ) -> bool:
        """
        Envoie un message structuré via Firebase Realtime.
        Args:
            space_code (str): Identifiant de l'espace
            thread_key (str): Identifiant du thread/conversation
            text (str): Contenu du message
            message_type (str): Type de message (par défaut "MESSAGE_PINNOKIO")
            message_data (dict): Données additionnelles du message (optionnel)
        Returns:
            bool: True si l'envoi a réussi
        """
        try:
            if message_type == "CARD_CLICKED_PINNOKIO":
                # Structure spécifique pour les cartes cliquées
                
                messages_ref = self.db.child(f'{space_code}').child(f'{thread_key}').child('messages')
                messages_ref.push(message_data)
                return True
            else:
                if not message_data:
                    message_data = {
                        'content': text,
                        'sender_id': self.user_id,
                        'timestamp': str(datetime.now()),
                        'message_type': message_type,
                        'read': False
                    }
                
                # Utiliser thread_key comme room_id
                messages_ref = self.db.child(f'{space_code}').child(f'{thread_key}').child('messages')
                print(f"Envoi vers le chemin: {space_code}/{thread_key}/messages")
                messages_ref.push(message_data)
                return True
        except Exception as e:
            print(f"Erreur lors de l'envoi du message structuré: {e}")
            return False

    
    def get_channel_messages(self,space_code: str, thread_key: str, limit: int = 50) -> List[Dict]:
        """
        Récupère les messages d'un canal spécifique.
        Args:
            thread_key (str): Identifiant du thread/conversation
            limit (int): Nombre maximum de messages à récupérer
        Returns:
            List[Dict]: Liste des messages
        """
        try:
            messages_ref = self.db.child(f'{space_code}').child(f'{thread_key}').child('messages')
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
    

'''# D'abord tester la connexion
fi_real = FirebaseRealtimeChat('test_user_123')
if fi_real.test_connection():
    # Tester l'envoi d'un message
    test_result = fi_real.send_realtime_message_structured(
        space_code="test_space_001",
        thread_key="test_thread_001",
        text="Ceci est un message de test",
        message_type="MESSAGE_PINNOKIO"
    )
    print(f"Résultat de l'envoi: {'Succès' if test_result else 'Échec'}")
else:
    print("Test de connexion échoué, impossible d'envoyer le message")'''