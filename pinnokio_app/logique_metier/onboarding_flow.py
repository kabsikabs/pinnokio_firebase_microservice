from .g_cred import DriveClientService,get_secret,create_secret,FireBaseManagement,SPACE_MANAGER,StorageClient,GoogleSpaceManager
from .g_cred import FirebaseRealtimeChat
import json

import datetime
import os
from dotenv import load_dotenv
import uuid
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.cloud import secretmanager
load_dotenv()

class DMS_CREATION:
    def __init__(self,dms_type,
    command,mandates_path,user_mail=None,
    command_args=None,firebase_user_id=None,
    client_uuid=None,client_mandat_doc_id=None,space_id=None):
           
        self.dms_type=dms_type
        print(f"impression de dms_type:{dms_type}")
        self.firebase_service=FireBaseManagement()
        self.communication_mode=command_args.get('communication_mode')
        self.mandates_path=mandates_path
        self.space_id=space_id
        print(f"impression de communication_mode:{self.communication_mode}")
        if dms_type=='google_drive':
            #Creation des dossier Dans l'univers Drive
            self.firebase_user_id=firebase_user_id
            self.dms=DriveClientService()  # 🆕 Plus de user_id dans le constructeur
            self.firebase_realtime_chat=FirebaseRealtimeChat()
            if self.communication_mode=='google_chat':
                self.space_manager=GoogleSpaceManager(
                communication_mode=self.communication_mode,
                auth_user_mail=user_mail,mode='prod',
                firebase_user_id=firebase_user_id,
                mandates_path=self.mandates_path)

            self.storage_client=StorageClient()
            self.gcs_bucket_name = "pinnokio_app"

            if command_args:
                client_name = command_args.get("client_name","")
                space_name = command_args.get("space_name","")
                share_email = command_args.get("share_email","")
                specific_year = command_args.get("specific_year","")
                ownership_type = command_args.get("ownership_type",""),
                client_uuid=client_uuid
                client_mandat_doc_id=client_mandat_doc_id
                if command=='create_mandate':
                    self.create_space(space_name,share_email)   
                    self.firebase_create_mandate_template=self.ensure_folder_structure(client_name, space_name,client_uuid,client_mandat_doc_id, specific_year,share_email)
                elif command=='create_folders':
                        self.firebase_create_mandate_template=self.ensure_folder_structure(client_name,
                        space_name,client_uuid,client_mandat_doc_id, 
                        specific_year)
                elif command=='delete_company':
                    # Initialiser le rapport de suppression
                    self.deletion_report = {
                        "success": False,
                        "steps": []
                    }
                    
                    # Gestion des secrets ERP
                    try:
                        self.manage_deletion_secret()
                        self.deletion_report["steps"].append({
                            "name": "ERP Secrets Deletion",
                            "status": "success",
                            "reason": "ERP secrets deleted successfully"
                        })
                    except Exception as e:
                        print(f"⚠️ Warning: Error deleting ERP secrets: {e}")
                        self.deletion_report["steps"].append({
                            "name": "ERP Secrets Deletion",
                            "status": "failed",
                            "reason": f"Exception: {str(e)}"
                        })
                    
                    # Suppression du mandat avec rapport détaillé
                    mandate_result = self.delete_mandate(client_name, space_name)
                    if isinstance(mandate_result, dict) and "steps" in mandate_result:
                        self.deletion_report["steps"].extend(mandate_result["steps"])
                        self.deletion_report["success"] = mandate_result.get("success", False)
                    
                    # Suppression de l'espace Realtime Database
                    try:
                        if self.firebase_realtime_chat.delete_space(self.space_id):
                            self.deletion_report["steps"].append({
                                "name": "Firebase Realtime Database",
                                "status": "success",
                                "reason": f"Space {self.space_id} deleted successfully"
                            })
                        else:
                            self.deletion_report["steps"].append({
                                "name": "Firebase Realtime Database",
                                "status": "failed",
                                "reason": "Delete operation returned False"
                            })
                    except Exception as e:
                        self.deletion_report["steps"].append({
                            "name": "Firebase Realtime Database",
                            "status": "failed",
                            "reason": f"Exception: {str(e)}"
                        })

                
        else:
            pass
        #Disponiblité pour ONE_DRIVE ET DROPBOX

    def delete_mandate(self, client_name, space_name):
        """
        Supprime un mandat complet incluant toutes les ressources associées.
        
        Args:
            client_name (str): Nom du client
            space_name (str): Nom de l'espace/mandat à supprimer
            
        Returns:
            dict: Rapport détaillé de la suppression avec le statut de chaque étape
        """
        # Initialiser le rapport de suppression
        deletion_report = {
            "success": False,
            "steps": []
        }
        
        try:
            print(f"Debut de suppression de compte client avec le chemin suivant:{self.mandates_path}")
            
            # 1. Récupérer les données du mandat
            data = self.firebase_service.get_document(self.mandates_path)
            main_drive_id = data.get('drive_space_parent_id')
            contact_space_id = data.get('contact_space_id', "")
            
            # SEULE CONDITION BLOQUANTE: contact_space_id
            if not contact_space_id or contact_space_id == 'unknown_folder':
                print("ID du mandata contact_space_ID non trouvé ou invalide")
                deletion_report["error"] = "Contact space ID not found or invalid"
                return deletion_report
            
            # 2. Archiver le dossier Drive (NON-BLOQUANT)
            if not main_drive_id or main_drive_id == 'unknown_folder':
                deletion_report["steps"].append({
                    "name": "Google Drive Archive",
                    "status": "skipped",
                    "reason": "Drive folder ID not found or invalid"
                })
                print("⚠️ Warning: Drive folder ID not found, skipping Drive archiving")
            else:
                try:
                    if self.dms.Archived_Pinnokio_folder(self.firebase_user_id, main_drive_id):
                        deletion_report["steps"].append({
                            "name": "Google Drive Archive",
                            "status": "success",
                            "reason": f"Folder {main_drive_id} archived successfully"
                        })
                        print(f"Le dossier Drive a bien été renommé dans la procédure de suppression du mandat")
                    else:
                        deletion_report["steps"].append({
                            "name": "Google Drive Archive",
                            "status": "failed",
                            "reason": "Archive operation returned False"
                        })
                        print(f"⚠️ Warning: Failed to archive Drive folder {main_drive_id}, continuing...")
                except Exception as e:
                    deletion_report["steps"].append({
                        "name": "Google Drive Archive",
                        "status": "failed",
                        "reason": f"Exception: {str(e)}"
                    })
                    print(f"⚠️ Warning: Exception during Drive archiving: {e}, continuing...")
            
            # 3. Supprimer la collection dans ChromaDB (NON-BLOQUANT)
            try:
                from .chroma_klk import CHROMA_KLK
                chroma_klk = CHROMA_KLK(contact_space_id)
                
                if chroma_klk.delete_collection():
                    deletion_report["steps"].append({
                        "name": "ChromaDB Collection",
                        "status": "success",
                        "reason": f"Collection {contact_space_id} deleted successfully"
                    })
                    print(f"Suppression de la collection dans ChromaDB réussie")
                else:
                    deletion_report["steps"].append({
                        "name": "ChromaDB Collection",
                        "status": "failed",
                        "reason": "Delete operation returned False"
                    })
                    print(f"⚠️ Warning: Failed to delete ChromaDB collection for {contact_space_id}, continuing...")
            except Exception as e:
                deletion_report["steps"].append({
                    "name": "ChromaDB Collection",
                    "status": "failed",
                    "reason": f"Exception: {str(e)}"
                })
                print(f"⚠️ Warning: Exception during ChromaDB deletion: {e}, continuing...")
            
            # 4. Supprimer les documents scheduler associés au mandat (NON-BLOQUANT)
            try:
                print(f"Suppression des documents scheduler pour le mandat...")
                if self.firebase_service.delete_scheduler_documents_for_mandate(self.mandates_path):
                    deletion_report["steps"].append({
                        "name": "Scheduler Documents",
                        "status": "success",
                        "reason": "Scheduler documents deleted successfully"
                    })
                else:
                    deletion_report["steps"].append({
                        "name": "Scheduler Documents",
                        "status": "failed",
                        "reason": "No scheduler documents found or delete operation failed"
                    })
                    print(f"⚠️ Warning: Failed to delete scheduler documents, continuing...")
            except Exception as e:
                deletion_report["steps"].append({
                    "name": "Scheduler Documents",
                    "status": "failed",
                    "reason": f"Exception: {str(e)}"
                })
                print(f"⚠️ Warning: Exception during scheduler deletion: {e}, continuing...")
            
            # 5. Nettoyer les utilisateurs Telegram pour ce mandat (NON-BLOQUANT)
            try:
                print(f"Nettoyage des utilisateurs Telegram pour le mandat...")
                if self.firebase_service.clean_telegram_users_for_mandate(self.mandates_path):
                    deletion_report["steps"].append({
                        "name": "Telegram Users Cleanup",
                        "status": "success",
                        "reason": "Telegram users cleaned successfully"
                    })
                else:
                    deletion_report["steps"].append({
                        "name": "Telegram Users Cleanup",
                        "status": "failed",
                        "reason": "No Telegram users found or cleanup operation failed"
                    })
                    print(f"⚠️ Warning: Failed to clean Telegram users, continuing...")
            except Exception as e:
                deletion_report["steps"].append({
                    "name": "Telegram Users Cleanup",
                    "status": "failed",
                    "reason": f"Exception: {str(e)}"
                })
                print(f"⚠️ Warning: Exception during Telegram cleanup: {e}, continuing...")
            
            # 6. Supprimer les fichiers dans Google Storage (NON-BLOQUANT)
            paths_to_try = [
                f"pinnokio_app/pinnokio_app/clients/{client_name}/{contact_space_id}/",
                f"klk_gcp_data/clients/{client_name}/{contact_space_id}/"
            ]
            
            storage_deletion_success = False
            storage_files_deleted = 0
            
            for path in paths_to_try:
                try:
                    formatted_path = path.format(client_name=client_name, contact_space_id=contact_space_id)
                    
                    parts = formatted_path.split('/', 1)
                    bucket_name = parts[0]
                    relative_path = parts[1] if len(parts) > 1 else ""
                    
                    bucket = self.storage_client.storage_client.bucket(bucket_name)
                    blobs = list(bucket.list_blobs(prefix=relative_path, max_results=1))
                    
                    if blobs:
                        print(f"Fichiers trouvés dans: {formatted_path}")
                        result = self.storage_client.delete_path(formatted_path, recursive=True)
                    
                        if result["success"]:
                            storage_deletion_success = True
                            storage_files_deleted += result.get('files_deleted', 0)
                            print(f"Suppression réussie dans {formatted_path}: {result['files_deleted']} fichiers supprimés")
                        else:
                            print(f"Erreurs lors de la suppression dans {formatted_path}: {result['errors']}")
                    else:
                        print(f"Aucun fichier trouvé dans: {formatted_path}")
                
                except Exception as e:
                    print(f"Erreur lors de la vérification/suppression de '{formatted_path}': {str(e)}")
            
            if storage_deletion_success:
                deletion_report["steps"].append({
                    "name": "Google Cloud Storage",
                    "status": "success",
                    "reason": f"{storage_files_deleted} files deleted successfully"
                })
            else:
                deletion_report["steps"].append({
                    "name": "Google Cloud Storage",
                    "status": "skipped",
                    "reason": "No files found in storage paths"
                })
                print(f"Aucun fichier trouvé dans les chemins de stockage pour {client_name}/{contact_space_id}")
            
            # 7. Supprimer les données dans Firebase (CRITIQUE - TOUJOURS EXECUTÉ)
            try:
                self.firebase_service.delete_document_recursive(self.mandates_path)
                deletion_report["steps"].append({
                    "name": "Firestore Mandate Document",
                    "status": "success",
                    "reason": f"Document {self.mandates_path} deleted recursively"
                })
                print(f"✅ Document Firestore {self.mandates_path} supprimé avec succès")
            except Exception as e:
                deletion_report["steps"].append({
                    "name": "Firestore Mandate Document",
                    "status": "failed",
                    "reason": f"Exception: {str(e)}"
                })
                print(f"⚠️ Warning: Exception during Firestore mandate deletion: {e}")
            
            # 8. Supprimer les documents dans les journaux des départements
            try:
                departements = ['APbookeeper', 'Bankbookeeper', 'Router', 'Admanager', 'EXbookeeper', 'HRmanager']
                self.firebase_service.delete_document_in_journal_by_mandat_id(contact_space_id, departements)
                deletion_report["steps"].append({
                    "name": "Department Journal Documents",
                    "status": "success",
                    "reason": f"Journal documents deleted for {len(departements)} departments"
                })
                print(f"✅ Documents journal supprimés pour {len(departements)} départements")
            except Exception as e:
                deletion_report["steps"].append({
                    "name": "Department Journal Documents",
                    "status": "failed",
                    "reason": f"Exception: {str(e)}"
                })
                print(f"⚠️ Warning: Exception during journal deletion: {e}")
            
            # 9. Supprimer les documents par space_name
            try:
                self.firebase_service.delete_documents_by_space_name(space_name)
                deletion_report["steps"].append({
                    "name": "Space Name Documents",
                    "status": "success",
                    "reason": f"Documents with space_name '{space_name}' deleted"
                })
                print(f"✅ Documents avec space_name '{space_name}' supprimés")
            except Exception as e:
                deletion_report["steps"].append({
                    "name": "Space Name Documents",
                    "status": "failed",
                    "reason": f"Exception: {str(e)}"
                })
                print(f"⚠️ Warning: Exception during space_name deletion: {e}")
            
            # Marquer la suppression comme réussie
            deletion_report["success"] = True
            print(f"Suppression complète du mandat {space_name} pour le client {client_name} réussie")
            return deletion_report
                
        except Exception as e:
            error_msg = f"Critical error during mandate deletion: {str(e)}"
            print(error_msg)
            deletion_report["success"] = False
            deletion_report["error"] = error_msg
            return deletion_report

    def delete_secret(self,secret_name):
        """
        Supprime un secret dans Google Secret Manager.
        
        Args:
            secret_name (str): Le nom du secret à supprimer
            
        Returns:
            bool: True si la suppression a réussi, False sinon
            
        Raises:
            Exception: Si une erreur survient lors de la suppression
        """
        try:
            client = secretmanager.SecretManagerServiceClient()
            project_id = os.getenv('GOOGLE_PROJECT_ID')
            secret_path = f"projects/{project_id}/secrets/{secret_name}"
            
            # Suppression du secret
            client.delete_secret(request={"name": secret_path})
            
            print(f"Secret '{secret_name}' supprimé avec succès")
            return True
        except Exception as e:
            print(f"Erreur lors de la suppression du secret '{secret_name}': {e}")
            raise
    
    def manage_deletion_secret(self):
        """
        Gère la suppression des secrets associés aux ERPs d'un mandat.
        
        Cette méthode:
        1. Récupère les types d'ERP associés au mandat
        2. Obtient la liste des noms d'ERP uniques
        3. Récupère les données complètes de chaque ERP
        4. Extrait les noms des secrets associés
        5. Supprime tous les secrets trouvés
        
        Returns:
            list: Liste des noms de secrets supprimés avec succès
        """
        try:
            # Récupérer les données du mandat
            data = self.firebase_service.get_document(self.mandates_path)
            
            # Extraire les noms des ERPs
            erp_names = []
            erp_fields = ['gl_accounting_erp', 'ap_erp', 'ar_erp', 'bank_erp']
            
            for field in erp_fields:
                erp_name = data.get(field, "")
                if erp_name:  # Si le champ n'est pas vide
                    erp_names.append(erp_name)
            
            # Créer une liste des noms d'ERP uniques
            unique_erp_names = list(set(erp_names))
            print(f"ERPs uniques trouvés: {unique_erp_names}")
            
            # Récupérer les documents ERP correspondants
            erp_documents = []
            for erp_name in unique_erp_names:
                erp_doc = self.firebase_service.get_erp_path(self.mandates_path, erp_name)
                if erp_doc:
                    erp_documents.append(erp_doc)
            
            # Extraire les noms des secrets à supprimer
            secrets_to_delete = []
            for erp_doc in erp_documents:
                secret_name = erp_doc.get('secret_manager')
                if secret_name:
                    secrets_to_delete.append(secret_name)
            
            # Supprimer les secrets
            deleted_secrets = []
            for secret_name in secrets_to_delete:
                try:
                    # Appel à la méthode de suppression des secrets
                    if self.delete_secret(secret_name):
                        deleted_secrets.append(secret_name)
                        print(f"Secret {secret_name} supprimé avec succès")
                except Exception as e:
                    print(f"Échec de la suppression du secret {secret_name}: {e}")
            
            return deleted_secrets
        
        except Exception as e:
            print(f"Erreur lors de la gestion de la suppression des secrets: {e}")
            return []



    def add_descriptions_to_folders(self, folder_info, folder_description_schema):
        """
        Ajoute des descriptions aux dossiers basées sur un schéma de description contenu dans un fichier JSON.

        Args:
            folder_info (dict): Dictionnaire contenant les ID et noms des dossiers.
            folder_description_schema (str): Chemin vers le fichier JSON contenant les descriptions des dossiers.
        """
        if self.dms_type=='google_drive':
            # Lecture et conversion du fichier JSON
            folder_description_schema = {item['folder_name']: item['folder_function'] for item in folder_description_schema}

            # Mise à jour des descriptions des dossiers
            for folder_name, folder_data in folder_info.items():
                description = folder_description_schema.get(folder_name)

                if description:
                    # Mise à jour de la description pour chaque ID de dossier ayant ce nom
                    folder_id = folder_data[0]  # Premier élément du tuple est l'ID du dossier
                    self.dms.update_folder_description(self.firebase_user_id,folder_id,description)
                    print(f"Description mise à jour pour {folder_name} (ID: {folder_id})")
                else:
                    # Message d'erreur si aucune correspondance n'est trouvée dans le mapping JSON
                    print(f"Aucune description trouvée pour le dossier nommé '{folder_name}'")
        else:
            print("DMS non paramétré.....")
    
    def create_folder_structure_recursive(self, folder_schema, parent_id):
        """
        Crée récursivement la structure des dossiers basée sur le schéma JSON
        Args:
            folder_schema: Liste des dossiers à créer avec leurs sous-dossiers
            parent_id: ID du dossier parent où créer la structure
        Returns:
            dict: Dictionnaire contenant les IDs des dossiers créés
        """
        folder_info = {}
        
        for folder in folder_schema:
            folder_name = folder['folder_name']
            folder_function = folder['folder_function']
            
            # Gestion spéciale pour le dossier "Year"
            if folder_name == "Year":
                folder_name = str(self.current_year)
                
            # Créer ou trouver le dossier
            current_folder_id, current_folder_name = self.dms.create_or_find_folder(
                self.firebase_user_id,
                folder_name, 
                parent_id
            )
            
            # Stocker les informations du dossier
            folder_info[folder_name] = {
                'id': current_folder_id,
                'name': current_folder_name,
                'function': folder_function
            }
            
            # Traiter les sous-dossiers s'ils existent
            if 'subfolders' in folder and folder['subfolders']:
                subfolder_info = self.create_folder_structure_recursive(
                    folder['subfolders'], 
                    current_folder_id
                )
                folder_info[folder_name]['subfolders'] = subfolder_info
                
        return folder_info

    def create_space(self,space_name,share_email):
        # Créer l'espace de Chat
        if self.communication_mode=='google_chat':
            print("Creation de l'espace de Chat au nom du mandat")
            description=""" Chat to interact with Pinnokio Agents."""

            _, space_id = self.space_manager.create_space(space_name,description)
            self.space_manager.share_space(space_id,share_email)
            #first_message=f"""Welcome to your new chat space {space_name}! This is where you can communicate with the Pinnokio agent to manage your tasks, ask questions, and track your processes in real time. Feel free to ask any questions you may have."""
            #self.space_manager.send_message(space_code=space_id,text=first_message)
            self.unique_space_id = space_id.split('/')[-1]
            print(f"impression de l'unique space id : {self.unique_space_id}")
        else:
            self.unique_space_id = f"klk_space_id_{str(uuid.uuid4())[:6]}"
            print(f"impression de l'unique space id Pinnokio (fallback) : {self.unique_space_id}")
    
    
    def ensure_folder_structure(self, client_name, space_name,client_uuid,client_mandat_doc_id, specific_year=None,share_email=None):
        """
        Crée la structure complète des dossiers pour un client
        """
        try:
            
            self.current_year = specific_year if specific_year else datetime.datetime.now().year
            
            print(f"ensure_folder_structure appelée avec : parent={space_name}, specific_year={specific_year}")

            # Recherche dans Firestore par le nom
            if self.dms_type=='google_drive':
                print(f"Creation de la structure des dossiers dans Google drive pour {client_name} ({space_name}, pour le client {client_uuid})")
                client_doc_id=self.firebase_service.get_client_doc(self.firebase_user_id,client_uuid)
                print(f"impression de doc_id:{client_doc_id}")
                client_path=f'clients/{self.firebase_user_id}/bo_clients/{client_doc_id}'
                document_path = f'clients/{self.firebase_user_id}/bo_clients/{client_doc_id}/mandates/{client_mandat_doc_id}'
                print(f"impression de document path:{document_path} {client_uuid} ")
                #firebase_instance=FireBaseManagement()
                client_data=self.firebase_service.get_document(client_path)
                print(f"impression de client_data:{client_data}")
                data=self.firebase_service.get_document(document_path)
                print(f"impression de data:{data}")
                drive_client_parent_id= client_data.get('drive_client_parent_id')
                unique_space_id=data.get('contact_space_id',"")
                print(f"valeur de unique space_id extrait:{unique_space_id}")
                if not unique_space_id:
                    unique_space_id=self.unique_space_id
                    print(f"Valeur du space_id n'etait pas existante elle a été crée.... {unique_space_id}.....")
                
                else:
                    self.unique_space_id=unique_space_id
                    print(f"La valeur du space_id est deja existante, {self.unique_space_id}")
                    
                

                print(f"impression de drive_client_parent_id:{drive_client_parent_id}")
                # Vérifier si le dossier existe réellement dans Google Drive
                if not drive_client_parent_id:
                    # Si aucun ID n'existe dans Firebase, créer le dossier
                    drive_client_parent_id, folder_name=self.dms.create_or_find_folder(self.firebase_user_id,client_name,'root')
                    data={'drive_client_parent_id':drive_client_parent_id}
                    client_document_path = f'clients/{self.firebase_user_id}/bo_clients/{client_doc_id}'
                    self.firebase_service.set_document(document_path=client_document_path,data=data,merge=True)
                    print(f"le dossier parent drive a été créé avec succès:{drive_client_parent_id}")
                else:
                    # Si un ID existe dans Firebase, vérifier qu'il existe réellement dans Drive
                    if not self.dms.folder_exists_by_id(self.firebase_user_id, drive_client_parent_id):
                        print(f"⚠️ Le dossier parent Drive (ID: {drive_client_parent_id}) n'existe pas ou a été supprimé. Recréation du dossier...")
                        # Recréer le dossier et mettre à jour Firebase
                        drive_client_parent_id, folder_name=self.dms.create_or_find_folder(self.firebase_user_id,client_name,'root')
                        data={'drive_client_parent_id':drive_client_parent_id}
                        client_document_path = f'clients/{self.firebase_user_id}/bo_clients/{client_doc_id}'
                        self.firebase_service.set_document(document_path=client_document_path,data=data,merge=True)
                        print(f"✅ Le dossier parent drive a été recréé avec succès:{drive_client_parent_id}")
                    else:
                        print(f"✅ Le dossier parent Drive (ID: {drive_client_parent_id}) existe et est valide.")
                if not client_uuid and drive_client_parent_id:
                    print("Numéro de client non identifié. Veuillez d'abord paramétrer le client.")
                    return
                    
                    
                # Créer le dossier principal de l'espace
                space_folder_id, _ = self.dms.create_or_find_folder(self.firebase_user_id,space_name,drive_client_parent_id)
                
                
                
                # Charger le schéma des dossiers depuis Google Cloud Storage
                '''file_in_memory = self.storage_client.download_blob(
                    'klk_gcp_data', 
                    'gdrive_folder_schema/drive-manager.json'
                )
                folder_description_schema = json.load(file_in_memory)'''
                
                bucket_name = 'pinnokio_app'
                destination_blob_name = 'setting_params/router_gdrive_structure/drive_manager.json'

                # Initialiser le service de stockage
                storage_service = StorageClient()

                # Télécharger le fichier depuis GCS
                file_in_memory = storage_service.download_blob(bucket_name, destination_blob_name)

                # Charger le contenu JSON dans un dictionnaire Python
                folder_description_schema = json.load(file_in_memory)

                # Créer la structure de dossiers de manière récursive
                folder_info = self.create_folder_structure_recursive(folder_description_schema, space_folder_id)
                
                # Créer les dossiers dans Google Cloud Storage
                storage_folders = [
                    "setup/coa",
                    "setup/gl_journals",
                    "doc_data/",
                    "schema/",
                    
                ]
                
                base_path = f"pinnokio_app/clients/{client_name}/{self.unique_space_id}/"
                for folder in storage_folders:
                    full_folder_path = base_path + folder
                    self.storage_client.folder_exists_or_create(self.gcs_bucket_name, full_folder_path)
                
                # Partager le dossier si une adresse email est fournie
                if share_email:
                    self.dms.share_folder_with_email(self.firebase_user_id,space_folder_id,share_email)
                
                # Préparer le template pour Firebase
                firebase_create_mandate_template = {
                    "contact_space_id": self.unique_space_id,
                    "contact_space_name": space_name,
                    "drive_space_parent_id": space_folder_id,
                    "input_drive_doc_id": folder_info.get("doc_to_do", {}).get('id'),
                    "main_doc_drive_id": folder_info.get("doc", {}).get('id'),
                    "output_drive_doc_id": folder_info.get("doc_done", {}).get('id'),
                }
                print(f"impression de firebase_create_mandate_template : {firebase_create_mandate_template}")
                #firebase_instance=FireBaseManagement()
                self.firebase_service.set_document(document_path,firebase_create_mandate_template,merge=True)
                return firebase_create_mandate_template
        except Exception as e:
            raise Exception(f"Erreur lors de la création de la structure des dossiers: {str(e)}")


class GoogleAuthManager:
    def __init__(self,user_id=None):
        self.user_id=user_id
        self.oauth_state =None
        # Définition des scopes par défaut (pour la rétrocompatibilité)
        self.SCOPES = [
            'https://www.googleapis.com/auth/chat.messages',
            'https://www.googleapis.com/auth/chat.spaces',
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/documents',
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/chat.memberships',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile',
            'openid',
            'https://www.googleapis.com/auth/script.scriptapp',
            'https://www.googleapis.com/auth/tasks.readonly',
            'https://www.googleapis.com/auth/tasks',
            'https://mail.google.com/',
            'https://www.googleapis.com/auth/gmail.settings.basic',
            'https://www.googleapis.com/auth/gmail.settings.sharing',
            'https://www.googleapis.com/auth/script.external_request',
            'https://www.googleapis.com/auth/contacts.readonly'
        ]
        
        # Définition des scopes par service
        self.GOOGLE_CHAT_SCOPES = [
            'https://www.googleapis.com/auth/chat.messages',
            'https://www.googleapis.com/auth/chat.spaces',
            'https://www.googleapis.com/auth/chat.memberships'
        ]
        
        self.GOOGLE_DRIVE_SCOPES = [
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/documents',
            'https://www.googleapis.com/auth/spreadsheets'
        ]
        
        # Scopes toujours requis
        self.BASE_SCOPES = [
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile',
            'openid'
        ]

    def set_drive_only_scopes(self):
        """Force l'utilisation exclusive des scopes nécessaires au DMS Google Drive.

        Conserve uniquement Drive / Docs / Sheets, sans inclure les BASE_SCOPES
        afin que le contexte Router demande exactement les mêmes scopes attendus.
        """
        try:
            self.SCOPES = list(self.GOOGLE_DRIVE_SCOPES)
        except Exception:
            # Fallback défensif si les constantes ne sont pas définies
            self.SCOPES = [
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/spreadsheets',
            ]

    def update_scopes_for_choices(self, dms_type=None, chat_type=None):
        """
        Met à jour la liste des scopes (self.SCOPES) en fonction des choix de l'utilisateur.
        
        Args:
            dms_type: Type de système de gestion documentaire choisi
            chat_type: Type de système de chat choisi
        """
        # Commencer avec les scopes de base (toujours nécessaires)
        selected_scopes = self.BASE_SCOPES.copy()
        
        # Ajouter les scopes en fonction des choix
        if dms_type and 'google' in dms_type.lower():
            selected_scopes.extend(self.GOOGLE_DRIVE_SCOPES)
            print(f"Ajout des scopes Google Drive pour DMS: {dms_type}")
        
        if chat_type and 'google_chat' in chat_type.lower():
            selected_scopes.extend(self.GOOGLE_CHAT_SCOPES)
            print(f"Ajout des scopes Google Chat pour: {chat_type}")
        
        # Éliminer les doublons
        self.SCOPES = list(set(selected_scopes))
        print(f"Scopes configurés: {self.SCOPES}")
        
        return self.SCOPES

    def get_authorization_url(self,state) -> str:
        """Génère l'URL d'autorisation OAuth2."""
        
        
        service_account_info = json.loads(get_secret(os.getenv('GOOGLE_AUTH2_KEY')))
        print(f"impression du fichier auth:{service_account_info}")
        
        print(f"Redirect URI from env: {os.getenv('GOOGLE_AUTH_REDIRECT_LOCAL')}")
        print(f"Redirect URIs in config: {service_account_info['web']['redirect_uris']}")
        flow = Flow.from_client_config(
            service_account_info,
            scopes=self.SCOPES,
            redirect_uri=os.getenv('GOOGLE_AUTH_REDIRECT_LOCAL')
        )

        auth_url, _ = flow.authorization_url(
            access_type='offline',
            #include_granted_scopes='true',
            prompt='consent',
            state=state,
        )

        return auth_url

    async def process_authorization_code(self, authorization_response: str):
        """Traite le code d'autorisation et retourne les tokens."""
        
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        service_account_info = json.loads(get_secret(os.getenv('GOOGLE_AUTH2_KEY')))
        print(f"impression du fichier auth:{service_account_info}")
        flow = Flow.from_client_config(
            service_account_info,
            scopes=self.SCOPES,
            redirect_uri=os.getenv('GOOGLE_AUTH_REDIRECT_LOCAL')
        )

        print("Échange du code contre les credentials...")
        # Échange le code contre des credentials
        flow.fetch_token(
            authorization_response=authorization_response,
           
            
        )
        credentials = flow.credentials
        print(f"Credentials obtenus : {credentials}")
        # Prépare les données des tokens pour le stockage
        token_data = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes,
            'expiry': credentials.expiry.isoformat() if credentials.expiry else None
        }
        print(f"Credentials obtenus : {token_data}")
        print("Sauvegarde des tokens dans Firebase...")
        # Sauvegarde les tokens dans Firebase
        self._save_tokens_to_firebase(token_data)
        print("Traitement du code d'autorisation terminé avec succès.")

        return token_data

    
    
    async def refresh_token(self, token_data):
        """Rafraîchit un token expiré"""
        try:
            credentials = Credentials(
                token=token_data['token'],
                refresh_token=token_data['refresh_token'],
                token_uri=token_data['token_uri'],
                client_id=token_data['client_id'],
                client_secret=token_data['client_secret'],
                scopes=token_data['scopes']
            )

            if credentials.expired:
                credentials.refresh(Request())
                token_data['token'] = credentials.token
                token_data['expiry'] = credentials.expiry.isoformat()

            return token_data

        except Exception as e:
            raise Exception(f"Erreur lors du rafraîchissement du token: {str(e)}")    
    
    def _save_tokens_to_firebase(self, token_data):
        """Sauvegarde les tokens dans Firebase"""
        try:

            tokens_path = f'clients/{self.user_id}/cred_tokens/google_authcred_token'
            firebase=FireBaseManagement()
            firebase.set_document(tokens_path, token_data)
        except Exception as e:
            raise Exception(f"Erreur lors de la sauvegarde des tokens: {str(e)}")


class ONBOARDING_MANAGEMENT:
    def __init__(self,fb_data=None,text_data=None):
        self.fb_data = fb_data
        self.text_data = text_data
        self.user_id = fb_data.get('metadata', {}).get('user_id')
        self.firebase = FireBaseManagement()  # Initialisation de Firebase
        self.workflow_status = {
            'database': 'pending',
            'google_auth': 'pending',
            'dms_creation': 'pending',
            'account_analysis': 'pending',
            'prompt_generation': 'pending'
        }
        # Déterminer le chemin de base en fonction de l'ownership_type
        self.base_path = self._get_base_path()
        self.mandates_path = None
        self.erp_path = None

    def create_asset_management_toggle(self):
        """
        Crée / met à jour le paramètre d'activation des immobilisations (fixed assets)
        dans le mandat Firebase.

        Cible: {mandate_path}/setup/asset_model/asset_management_activated
        """
        if not self.mandates_path:
            raise Exception("mandates_path non initialisé (create_mandates_document doit être appelé avant).")

        assets = (self.fb_data.get("business_details", {}) or {}).get("assets", {}) or {}
        asset_management_activated = bool(assets.get("asset_management_activated", False))
        details = str(assets.get("details", "") or "").strip()

        asset_model_path = f"{self.mandates_path}/setup/asset_model"
        self.firebase.set_document(
            asset_model_path,
            {
                "asset_management_activated": asset_management_activated,
                "details": details,
            },
            merge=True,
        )
        print(f"✅ Document asset_model mis à jour: {asset_model_path} (asset_management_activated={asset_management_activated})")

    async def _save_tokens_to_firebase(self, credentials):
        """Sauvegarde les tokens dans Firebase"""
        tokens_path = f'clients/{self.user_id}/cred_tokens'
        tokens_data = {
            'access_token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes,
            'expiry': credentials.expiry.isoformat()
        }
        
        await self.firebase.set_document(tokens_path, tokens_data)


    def _get_base_path(self):
        # Extraire ownership_type de fb_data['base_info']
        self.ownership_type = self.fb_data['base_info'].get('ownership_type', 'I own this company')
        print(f'situtation :{self.ownership_type}')
        # Déterminer le chemin de base en fonction de ownership_type
        if self.ownership_type == 'I own this company':
            return f'clients/{self.user_id}/bo_clients'
        else:
            return f'clients/{self.user_id}/bo_clients'
    
    def create_client_document(self):
        if self.ownership_type == 'I own this company':
            print(f"Creation sous le nom de l'utilisateur.....")

            # Path to the collection
            client_collection_path = self.base_path
            
            # Create a document with user_id as the document ID
            self.client_path = f'{client_collection_path}/{self.user_id}'
            print("impression de client path:", self.client_path)
            # Check if the document already exists
            existing_client = self.firebase.get_document(self.client_path)
            print(f"impression de existing_client:{existing_client}")
            if existing_client and existing_client.get('client_uuid'):
                print("Document client existe déjà avec client_uuid. Étape sautée.")
                self.client_uuid=existing_client.get('client_uuid')
                self.client_doc_id=existing_client.get('id')
                # Prepare client data
                client_data = {
                    'client_name': f"{self.fb_data['base_info']['client_name'].strip()}",
                    'client_address': self.fb_data['base_info'].get('address', '').strip(),
                    'client_mail': self.fb_data['base_info']['email'].strip(),
                    'client_phone': self.fb_data['base_info']['phone_number'].strip(),
                    
                }
                
                # Add the document with the specified document ID
                self.firebase.set_document(self.client_path, client_data,merge=True)
                
                print("Document client créé avec succès.")
                return
            
            # 🆕 AJOUT : Si le client n'existe pas, le créer
            else:
                print("Client n'existe pas encore, création d'un nouveau client...")
                self.client_uuid = f"klk_client_{str(uuid.uuid4())[:8]}"
                self.client_doc_id = self.user_id
                
                client_data = {
                    'client_name': f"{self.fb_data['base_info']['client_name'].strip()}",
                    'client_address': self.fb_data['base_info'].get('address', '').strip(),
                    'client_mail': self.fb_data['base_info']['email'].strip(),
                    'client_phone': self.fb_data['base_info']['phone_number'].strip(),
                    'client_uuid': self.client_uuid,
                    'id': self.client_doc_id
                }
                
                # Create the document with the specified document ID
                self.firebase.set_document(self.client_path, client_data, merge=True)
                
                print(f"Document client créé avec succès. client_uuid: {self.client_uuid}, client_doc_id: {self.client_doc_id}")
                return
        
        else:
            #get customer uuid if exist othersie create a new one
            client_uuid = self.fb_data['base_info'].get('client_uuid','')
            print(f"impression de client_uuid:{client_uuid}")
            if client_uuid:
                print("Client UUID trouvé. Utilisation de l'UUID existant.")
                #firebase=FireBaseManagement()
                self.client_doc_id=self.firebase.get_client_doc(self.user_id,client_uuid)
                self.client_uuid = client_uuid
                print(f"impression de client_doc_id:{self.client_doc_id}")
                
                # ✅ Vérifier que le client_doc_id a bien été trouvé
                if not self.client_doc_id:
                    raise Exception(f"Client UUID '{client_uuid}' trouvé dans les données mais le document client n'existe pas dans Firebase. Impossible de continuer.")
                
                return 
            # Path to the collection
            client_collection_path = self.base_path
            print(f"impression de client_collection_path:{client_collection_path}")
            # Prepare client data
            client_data = {
                'client_name': (
                    f"{self.fb_data['base_info'].get('first_name', '').strip()} {self.fb_data['base_info'].get('last_name', '').strip()}"
                    if self.fb_data['base_info'].get('first_name', '') and self.fb_data['base_info'].get('last_name', '') 
                    else self.fb_data['base_info'].get('client_name', '').strip()
                ),
                'client_address': self.fb_data['base_info'].get('address', '').strip(),
                'client_mail': self.fb_data['base_info'].get('email', '').strip(),
                'client_phone': self.fb_data['base_info'].get('phone_number', '').strip(),
                'client_uuid': f"klk_client_{str(uuid.uuid4())[:8]}"
                }
            print(f"impression de client_data:{client_data}")
            # Add a new document to the collection
            self.client_uuid=client_data['client_uuid']
            self.client_doc_id = self.firebase.add_new_document(client_collection_path, client_data)
            print(f"Document client créé avec succès. slef.client_doc_id:{self.client_doc_id}")

    def create_mandates_document(self):
        # Construire le chemin de la collection mandates
        
        self.mandates_path = f'clients/{self.user_id}/bo_clients/{self.client_doc_id}/mandates'
    
        
        # Données du document mandates
        mandates_data = {
            'base_currency': self.fb_data['business_details'].get('base_currency', ''),
            'contact_space_name': (self.fb_data['base_info'].get('business_name', '') or '').strip() or (self.fb_data['base_info'].get('company_name', '') or '').strip(),
            'isactive': True,
            'email': self.fb_data['base_info'].get('email', '').strip(),
            'legal_name': (self.fb_data['base_info'].get('company_name') or 
              self.fb_data['base_info'].get('business_name') or 
              '').strip(),
            'legal_status': self.fb_data['base_info'].get('legal_status', ''),
            'country': self.fb_data['base_info'].get('country', ''),
            'has_vat': self.fb_data['business_details'].get('vat_info', {}).get('has_vat', False),
            'gl_accounting_erp': (self.fb_data.get('accounting_systems', {}).get('accounting_system', '') or '').lower(),
            'ap_erp': (self.fb_data.get('accounting_systems', {}).get('accounting_system', '') or '').lower(),
            'ar_erp': (self.fb_data.get('accounting_systems', {}).get('accounting_system', '') or '').lower(),
            'bank_erp': (self.fb_data.get('accounting_systems', {}).get('accounting_system', '') or '').lower(),
            'dms_type': self.fb_data.get('system_details', {}).get('dms', {}).get('type', ''),
            'chat_type': self.fb_data.get('system_details', {}).get('chat', {}).get('type', ''),
            'communication_chat_type': self.fb_data.get('system_details', {}).get('chat', {}).get('type', ''),
            'communication_log_type': self.fb_data.get('system_details', {}).get('chat', {}).get('type', ''),
            'website': self.fb_data['base_info'].get('website', ''),
            'phone_number': self.fb_data['base_info'].get('phone_number', '').strip(),
            'address': self.fb_data['base_info'].get('address', '').strip(),
            # Suppression du double 'country'
            'ownership_type': self.fb_data['base_info'].get('ownership_type', ''),
            'language': self.fb_data['base_info'].get('language', '')
        }
        if mandates_data['chat_type']:
            self.chat_type=mandates_data['chat_type']
        else:
            self.chat_type=None
        print(f"impression de mandate_data durant la creation du mandat avant l'import:{mandates_data}")
        # Ajouter le document mandates dans Firebase
        self.client_mandat_id=self.firebase.add_document(self.mandates_path, mandates_data,merge=True)
        self.mandates_path = f'clients/{self.user_id}/bo_clients/{self.client_doc_id}/mandates/{self.client_mandat_id}'
        print("Document mandates créé avec succès.")

        # ✅ Asset management toggle (immobilisations) - dépend de mandates_path
        self.create_asset_management_toggle()

    def create_erp_document(self):
        # Données du document erp
        accounting_systems = self.fb_data['accounting_systems']
        erp_type = accounting_systems['accounting_system'].lower()
        
        # Construire le chemin complet du document avec l'ID basé sur le nom de l'ERP
        # Format: mandate_path/erp/{erp_type} (ex: .../mandates/{id}/erp/odoo)
        self.erp_path = f'clients/{self.user_id}/bo_clients/{self.client_doc_id}/mandates/{self.client_mandat_id}/erp/{erp_type}'
        
        print(f"🔄 [create_erp_document] Création du document ERP avec chemin: {self.erp_path}")
        print(f"   - Type ERP: {erp_type}")
        
        erp_data = {}

        if erp_type == 'odoo':
            secret_name=create_secret(accounting_systems['accounting_api_key'])
            print(f"   - Secret créé: {secret_name}")
            erp_data = {
                'erp_type': 'odoo',
                'odoo_company_name': accounting_systems['odoo_details']['company_name'],
                'odoo_db': accounting_systems['odoo_details']['database_name'],
                'odoo_url': accounting_systems['odoo_details']['url'],
                'odoo_username': accounting_systems['odoo_details']['username'],
                'secret_manager': secret_name,
            }
        else:
            # Pour les autres ERPs (bexio, quickbooks, etc.), on peut ajouter leur logique ici
            # Pour l'instant, on crée juste le document avec le type
            erp_data = {
                'erp_type': erp_type,
            }
            print(f"   ⚠️  Type ERP '{erp_type}' non entièrement configuré, document créé avec type uniquement")

        # Utiliser set_document avec le chemin complet incluant l'ID du document
        # Cela permet de créer le document avec un ID spécifique basé sur le nom de l'ERP
        self.firebase.set_document(self.erp_path, erp_data, merge=True)
        print(f"✅ Document erp créé avec succès au chemin: {self.erp_path}")

    def create_workflow_params_document(self):
        """
        Crée le document workflow_params avec les valeurs par défaut.
        Ce document contient les paramètres de configuration pour Apbookeeper, Router et Banker.
        """
        # Construire le chemin complet du document workflow_params
        workflow_params_path = f'clients/{self.user_id}/bo_clients/{self.client_doc_id}/mandates/{self.client_mandat_id}/setup/workflow_params'
        
        print(f"🔄 [create_workflow_params_document] Création du document avec chemin: {workflow_params_path}")
        
        # Structure par défaut des paramètres de workflow
        workflow_params_data = {
            "Accounting_param": {
                "accounting_date_definition": True,  # Règle automatique par défaut
                "accounting_date": ""  # Pas de date par défaut
            },
            "Apbookeeper_param": {
                "apbookeeper_approval_contact_creation": True,
                "apbookeeper_approval_required": True,
                "apbookeeper_communication_method": "pinnokio",
                "trust_threshold_required": False,
                "trust_threshold_percent": 80,
                "scheduler_enabled": False,
                "scheduler_frequency": "daily",
                "scheduler_time": "03:00",
                "scheduler_day_of_week": "MON",
                "scheduler_day_of_month": 1,
                "scheduler_timezone": "Europe/Zurich"
            },
            "Router_param": {
                "router_approval_required": True,
                "router_automated_workflow": True,
                "router_communication_method": "pinnokio",
                "scheduler_enabled": False,
                "scheduler_frequency": "daily",
                "scheduler_time": "03:00",
                "scheduler_day_of_week": "MON",
                "scheduler_day_of_month": 1,
                "scheduler_timezone": "Europe/Zurich"
            },
            "Banker_param": {
                "banker_communication_method": "pinnokio",
                "banker_approval_required": True,
                "banker_approval_thresholdworkflow": 95,
                "banker_gl_approval": True,
                "banker_voucher_approval": True,
                "scheduler_enabled": False,
                "scheduler_frequency": "daily",
                "scheduler_time": "03:00",
                "scheduler_day_of_week": "MON",
                "scheduler_day_of_month": 1,
                "scheduler_timezone": "Europe/Zurich"
            }
        }
        
        # Créer le document dans Firebase
        self.firebase.set_document(workflow_params_path, workflow_params_data, merge=False)
        print(f"✅ Document workflow_params créé avec succès au chemin: {workflow_params_path}")

    def process_onboarding(self):
        try:
            self.create_client_document()
            self.create_mandates_document()
            self.create_erp_document()
            self.create_workflow_params_document()  # ✅ NOUVEAU : Créer workflow_params
            
            self.workflow_status['database'] = 'completed'
            print("Database in NoSql created successfully...")
            return self.client_uuid,self.client_mandat_id,self.mandates_path,self.erp_path
        except Exception as e:
            self.workflow_status['database'] = 'error'
            raise Exception(f"Erreur lors du process d'onboarding: {str(e)}")
    
    def get_workflow_status(self):
        """Retourne l'état actuel du workflow"""
        return self.workflow_status
    
    def create_dms(self,dms_type,user_mail,command,command_args=None,firebase_user_id=None,client_uuid=None,client_mandat_doc_id=None,mandates_path=None):
        try:
            
            print(f"impression de client_uuid:{client_uuid}, impression de client_mandat_doc_id:{client_mandat_doc_id}, impression de mandates_path:{mandates_path}")
            command_args['communication_mode']=self.chat_type
            
            dms_folder = DMS_CREATION(
                user_mail=user_mail,
                dms_type=dms_type,
                command=command,
                command_args=command_args,
                firebase_user_id=firebase_user_id,
                mandates_path=mandates_path,
                client_uuid=client_uuid,
                client_mandat_doc_id=client_mandat_doc_id)
            
            return dms_folder.firebase_create_mandate_template
            #Mettre self.mandates_path a la place de temp_path a la fin du test
           
        except Exception as e:
            raise Exception(f"Erreur lors de la création du DMS: {str(e)}") 


data={'company_name': 'Fiduciaire Matteo', 'country': 'Switzerland', 'legal_status': 'SA - Société Anonyme', 'phone_number': '+41793514808', 'email': 'matteolardi8@gmail.com', 'website': '', 'ownership_type': 'I own this company', 'country_id': None, 'selling_type': 'Both', 'recurring_invoices': False, 'order_invoices': False, 'has_vat': False, 'vat_number': '', 'has_employees': False, 'has_rent': False, 'has_stock': False, 'stock_management': '', 'has_specific_taxes': False, 'specific_taxes_details': '', 'has_personal_expenses': False, 'personal_expenses_details': '', 'activity_type': '', 'suppliers_list': 'none', 'accounting_system': 'pinnokio', 'odoo_company_name': '', 'odoo_db_name': '', 'odoo_url': '', 'odoo_username': '', 'accounting_api_key': '', 'ar_system': 'Same as accounting', 'ar_api_key': '', 'base_currency': 'CHF', 'currency_id': 5, 'dms_type': 'google_drive', 'chat_type': 'google_spaces', 'employees_details': '', 'user_id': '7hQs0jluP5YUWcREqdi22NRFnU32'}
commands_args={'client_name': 'cedric gacond', 'space_name': 'Fiduciaire Matteo', 'specific_year': 2025, 'share_email': 'matteolardi8@gmail.com', 'ownership_type': 'I own this company'}
#mandates_path='clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/7hQs0jluP5YUWcREqdi22NRFnU32/mandates/kPRNe1zzaORmUmz9dpcI'
#onb_b=ONBOARDING_MANAGEMENT(fb_data=data)
#client_uuid,client_mandat_id,mandates_path=onb_b.process_onboarding()
#test=onb_b.create_dms(dms_type='google_drive',user_mail='cgacond@gmail.com',command='create_mandate',command_args=commands_args,firebase_user_id='g3E0jtfBz9ZR1GFBC1EySO5jfTg2',client_uuid=client_uuid,client_mandat_doc_id=client_mandat_id,mandates_path=mandates_path)



gcp_project_id = "pinnokio-gpt"
gcp_topic_id = "pinnokio_topic"

'''command_args = {
    "client_name": "Matteo Lardi",          # Nom du client
    "space_name": "Cabinet Rossignol Demo 2",         # Nom de l'espace ou mandat
    "specific_year": 2025,               # Année spécifique (optionnel)
    "share_email": "cedric.gacond@klkvision.tech"    # Email avec lequel partager (optionnel)
}'''

command_args={'client_name': 'lucas Dikkers', 
'space_name': 'Professional Skin Solutions Lab Sàrl',
'communication_mode':'pinnokio'
 }

'''dms_instance = DMS_CREATION(

    
    dms_type="google_drive",        
    command="delete_company",
    
    mandates_path='clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/YgsX2MpkrrOMIpMCYR0d/mandates/HoS6Y4YK4dYaTPLXiKr3',
    client_uuid='cb76abf6-60b7-4060-892a-954b3b4f8ba0',
    client_mandat_doc_id='HoS6Y4YK4dYaTPLXiKr3',
    firebase_user_id='7hQs0jluP5YUWcREqdi22NRFnU32',
    command_args=command_args)
'''



            
           