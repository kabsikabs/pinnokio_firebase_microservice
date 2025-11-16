import asyncio
import json
import os
import threading
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from .tools.g_cred import get_secret
from .firebase_providers import FirebaseManagement


class DriveClientServiceSingleton:
    """
    Singleton DriveClientService - une seule instance pour toute l'application.
    Les tokens sont récupérés par utilisateur via FirebaseManagement singleton.
    """

    _instance: Optional["DriveClientServiceSingleton"] = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls, mode: str = 'prod'):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, mode: str = 'prod'):
        """ Initialise un client Drive et Sheets authentifié. Mode par défaut = 'prod' """
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.mode = mode
                    self._initialize_services()
                    DriveClientServiceSingleton._initialized = True

    def _initialize_services(self):
        """Initialise les services selon le mode"""
        try:
            if self.mode == 'dev':
                self._initialize_dev_mode()
            elif self.mode == 'prod':
                # Mode prod : tokens récupérés dynamiquement par méthode avec user_id
                print("✅ DriveClientServiceSingleton initialisé en mode PROD")
                # Pas d'initialisation Firebase ici - fait par méthode
            else:
                raise ValueError(f"Mode '{self.mode}' non supporté")

            # Configuration commune
            self.spreadsheet_mapping_id = '1hZ2iicbqlP7Bid42l4yXgZFB93SnQoRUDaOGaXE1N70'
            self.sheet_mapping_name = 'contact'
            print("✅ DriveClientServiceSingleton initialisé avec succès")

        except Exception as e:
            print(f"❌ Erreur initialisation DriveClientServiceSingleton: {e}")
            raise

    def _initialize_dev_mode(self):
        """Initialisation en mode développement"""
        try:
            # Récupération des credentials dev
            service_account_info = json.loads(get_secret(os.getenv('GOOGLE_SERVICE_ACCOUNT_SECRET')))
            scopes = os.getenv("GOOGLE_DRIVE_SCOPES", "https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/documents").split(",")

            # Création des credentials
            self.credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=scopes)
            self.drive_service = build('drive', 'v3', credentials=self.credentials)

            # Service Sheets avec délégation
            user_email = os.getenv('ADMIN_EMAIL')
            self.spreadsheet_service = self.create_sheet_service(user_email)
            self.spreadsheet_no_delegation = self.create_sheet_service_no_delegated_mail()

            # Service Docs
            self.docs_service = build('docs', 'v1', credentials=self.credentials)

            print("✅ Mode DEV initialisé")

        except Exception as e:
            print(f"❌ Erreur initialisation mode DEV: {e}")
            raise

    def _get_user_tokens(self, user_id: str) -> dict:
        """Récupère les tokens Firebase pour l'utilisateur"""
        firebase_instance = FirebaseManagement()
        service_account_info = firebase_instance.user_app_permission_token(user_id=user_id)

        if not isinstance(service_account_info, dict):
            raise ValueError(f"Tokens invalides pour user_id={user_id}")

        return service_account_info

    def _initialize_prod_credentials(self, user_id: str):
        """Initialise les credentials en mode production pour un utilisateur"""
        try:
            service_account_info = self._get_user_tokens(user_id)

            # Définition des scopes
            scopes = [
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/documents'
            ]

            # Création des credentials
            creds = Credentials(
                token=service_account_info.get('token'),
                refresh_token=service_account_info.get('refresh_token'),
                token_uri=service_account_info.get('token_uri'),
                client_id=service_account_info.get('client_id'),
                client_secret=service_account_info.get('client_secret'),
                scopes=scopes
            )

            # Gestion du rafraîchissement des tokens
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Mettre à jour les tokens dans Firebase si nécessaire
                service_account_info['token'] = creds.token
                service_account_info['expiry'] = creds.expiry.isoformat()

            return creds

        except Exception as e:
            raise RuntimeError(f"Erreur initialisation credentials prod pour {user_id}: {e}")

    # === MÉTHODES EXISTANTES (toutes avec user_id quand nécessaire) ===

    def router_metadata_dict_from_drive(self,user_id, uuid_id, client_name, source, mandat_space_id, mandat_space_name, pinnokio_func, legal_name, input_drive_doc_id):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            collected_data = []
            treated_files_info = []
        
            results = drive_service.files().list(q=f"'{input_drive_doc_id}' in parents and trashed = false", fields='files(id, name, webViewLink)').execute()
            items = results.get('files', [])

            if not items:
                raise ValueError("Aucun fichier disponible, veuillez vérifier le dossier doc_to_do s'il est composé de document")

            for file in items:
                file_id = file['id']
                original_file_name = file['name']
                file_name = self.clean_file_name(original_file_name)
                file_link = file.get('webViewLink', 'No link found')
                _, extension = os.path.splitext(file_name)
                file_name_without_extension = os.path.splitext(file_name)[0]
                
                if extension in ['.pdf', '.txt', '.csv', '.jpeg', '.jpg', '.xls', '.xlsx', '.doc', '.docx', '.png', '.PNG', '.JPEG', '.JPG']:
                    print(f"Traitement du fichier: {file_name}")

                    data_dict = {
                        'uuid': uuid_id,
                        'client': client_name,
                        'source': source,
                        'mandat_id': mandat_space_id,
                        'mandat_name': mandat_space_name,
                        'file_name': file_name,
                        'pinnokio_func': pinnokio_func,
                        'extension': extension,
                        'uri_drive_link': file_link,
                        'file_name_wo_ext': file_name_without_extension,
                        'legal_name': legal_name,
                        'drive_file_id': file_id,
                    }
                    collected_data.append(data_dict)
                    treated_files_info.append((file_name, extension))
                   
            print(f"impression de collected_data : {collected_data}\n treated file info: {treated_files_info}\n")
            return collected_data, treated_files_info

        except Exception as e:
           return {"erreur": str(e)}, None

    async def list_files_in_doc_to_do(self,user_id, input_drive_doc_id):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            fields = 'files(id, name, mimeType, createdTime, modifiedTime, size, webViewLink, owners, permissions, shared, fileExtension, quotaBytesUsed)'
            
            request = drive_service.files().list(
                q=f"'{input_drive_doc_id}' in parents and trashed = false",
                fields=fields
            )
            
            import functools
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                functools.partial(request.execute)
            )
            
            items = results.get('files', [])
            return items
            
        except Exception as e:
            error_msg = str(e)
            print(f"Erreur dans list_files_in_doc_to_do: {error_msg}")
            
            # Détecter si c'est une erreur OAuth nécessitant reconnexion
            error_lower = error_msg.lower()
            is_oauth_error = ("invalid_grant" in error_lower or 
                              "token has been expired" in error_lower or
                              "token has been revoked" in error_lower)
            
            return {
                "erreur": error_msg,
                "oauth_reauth_required": is_oauth_error  # Flag pour détection facile
            }
        
    def classify_invoices_in_folder_to_delete(self,user_id:str, fiscal_year: str, parent_id: str):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            fiscal_year_folder_id = self.find_folder(user_id, fiscal_year, parent_id)
            if fiscal_year_folder_id is None:
                error_message = (f"Le classeur défini (ID : {parent_id}) ne dispose pas d'une année fiscale définie. "
                                "Veuillez vérifier que le dossier correct est utilisé comme parent.")
                print(error_message)
                return error_message

            accounting_folder_id = self.find_folder(user_id, 'ACCOUNTING', fiscal_year_folder_id)
            if accounting_folder_id is None:
                print(f"Aucun dossier 'ACCOUNTING' trouvé dans l'année fiscale spécifiée {fiscal_year}.")
                return None

            invoices_folder_id = self.find_folder(user_id, 'INVOICES', accounting_folder_id)
            if invoices_folder_id is None:
                print(f"Aucun dossier 'INVOICES' trouvé dans le dossier 'ACCOUNTING'.")
                return None

            invoice_folders = self.list_drive_folders(user_id, invoices_folder_id)
            filtered_invoice_folder = self.filter_out_doc_to_do(invoice_folders)

            return f"voici les dossiers disponible , si le fournisseur recherché n'existe pas il faut le créer :{filtered_invoice_folder}"

        except Exception as e:
            return {"erreur": str(e)}

    def add_comments(self,user_id:str, file_id: str, comment_text: str):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            body = {'content': comment_text}
            return drive_service.comments().create(fileId=file_id, body=body, fields='id').execute()
        except Exception as e:
            print(f"Une erreur est survenue : {e}")
            return None

    def push_file_to_human_review_folder(self,user_id:str, parent_folder_id: str, human_review_folder_name: str, drive_file_id: str, comments: str = None):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            human_review_folder_id = self.find_folder_by_name(user_id, human_review_folder_name, parent_folder_id, drive_service)
            if human_review_folder_id:
                move_file = self.move_file(user_id, drive_file_id, human_review_folder_id)
                if comments is not None:
                    self.add_comments(user_id, drive_file_id, comments)
                    print("l'ajoute des commentaires à été effectués")
                if move_file:
                    print("Fichier déplacé dans le classeur 'Human_review'")
                    return True
                else:
                    print("Erreur lors du déplacement du fichier")
                    return False
            else:
                print("Le dossier 'Human_review' n'a pas été trouvé")
                return False
        except Exception as e:
            print(f"Erreur push_file_to_human_review_folder: {e}")
            return False

    def find_folder_by_name(self,user_id:str, folder_name: str, parent_id: str = None, drive_service: 'googleapiclient.discovery.Resource' = None):
        """Version helper avec service Drive optionnel"""
        if drive_service is None:
            creds = self._initialize_prod_credentials(user_id)
            service = build('drive', 'v3', credentials=creds)
        else:
            service = drive_service
        
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        try:
            response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            folders = response.get('files', [])
            
            if folders:
                return folders[0].get('id')
            else:
                return None
        except Exception as e:
            print(f"Erreur lors de la recherche du dossier : {e}")
            return None

    def Archived_Pinnokio_folder(self,user_id:str, folder_id: str):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            folder_metadata = drive_service.files().get(fileId=folder_id, fields='name, mimeType').execute()

            if folder_metadata.get('mimeType') != 'application/vnd.google-apps.folder':
                print(f"L'ID {folder_id} ne correspond pas à un dossier.")
                return False
                
            current_name = folder_metadata.get('name')
            
            if current_name.startswith('ARCHIVED_PINNOKIO_'):
                print(f"Le dossier '{current_name}' est déjà archivé.")
                return True
                
            new_name = f"ARCHIVED_PINNOKIO_{current_name}"
            
            updated_metadata = {'name': new_name}
            drive_service.files().update(fileId=folder_id, body=updated_metadata).execute()
            
            print(f"Dossier renommé avec succès: '{current_name}' → '{new_name}'")
            return True
            
        except Exception as e:
            print(f"Erreur lors du renommage du dossier {folder_id}: {str(e)}")
            return False

    def find_file_by_name(self,user_id: str, file_name: str, parent_id: str = None):
        """Version singleton avec user_id optionnel (pour compatibilité)"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
            

            query = f"name = '{file_name}' and mimeType != 'application/vnd.google-apps.folder' and trashed = false"

            if parent_id:
                query += f" and '{parent_id}' in parents"

            response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)', pageSize=10).execute()
            files = response.get('files', [])

            if files:
                return files[0].get('id')
            else:
                return None

        except Exception as e:
            print(f"Erreur lors de la recherche du fichier : {e}")
            return None
    
    def extract_section_and_content(self,user_id:str, section_info: tuple):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            section_title, file_id = section_info
            file_content = self.download_file_content(user_id, file_id, drive_service)
            return section_title, file_content
        except Exception as e:
            print(f"Erreur extract_section_and_content: {e}")
            return None, None

    def download_file_content(self,user_id:str, file_id: str, drive_service: 'googleapiclient.discovery.Resource' = None):
        """Version helper avec service Drive optionnel"""
        if drive_service is None and self.mode == 'prod':
            raise ValueError("drive_service requis en mode prod")

        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
        file = service.files().get(fileId=file_id, fields='mimeType').execute()
        mimeType = file['mimeType']
        
        if mimeType.startswith('application/vnd.google-apps.document'):
            exportMimeType = 'text/plain'
            request = service.files().export_media(fileId=file_id, mimeType=exportMimeType)
            
            import io
            from googleapiclient.http import MediaIoBaseDownload

            file_io = io.BytesIO()
            downloader = MediaIoBaseDownload(file_io, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            
            file_io.seek(0)
            content = file_io.getvalue().decode('utf-8')
            return content
        else:
            raise ValueError("Le fichier spécifié n'est pas un document Google Docs ou n'est pas pris en charge.")

    def create_google_doc_with_content(self, user_id: str, title: str, parent_id: str = None, content: str = None):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
            docs_service = build('docs', 'v1', credentials=creds)
           
            document_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document'
            }
            if parent_id:
                document_metadata['parents'] = [parent_id]

            doc = drive_service.files().create(body=document_metadata, fields='id').execute()
            document_id = doc.get('id')

            if content:
                requests = [
                    {
                        'insertText': {
                            'location': {'index': 1},
                            'text': "\nSection 1\n"
                        }
                    },
                    {
                        'createNamedRange': {
                            'name': 'Section1',
                            'range': {
                                'startIndex': 1,
                                'endIndex': 12,
                            }
                        }
                    },
                ]
                docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()

            return document_id

        except Exception as e:
            print(f"Erreur create_google_doc_with_content: {e}")
            return None

    def classify_invoices_in_folder(self, user_id: str,file_id: str):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            file = drive_service.files().get(fileId=file_id, fields='parents').execute()
            parent_id = file.get('parents')[0] if file.get('parents') else None

            if not parent_id:
                return None, "Aucun dossier parent trouvé pour le fichier donné."

            folder = drive_service.files().get(fileId=parent_id, fields='parents').execute()
            grandparent_id = folder.get('parents')[0] if folder.get('parents') else None

            if not grandparent_id:
                return None, "Aucun grand-parent trouvé pour le dossier donné."

            query = f"'{grandparent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            folders = drive_service.files().list(q=query, fields='files(id, name)').execute()
            folder_list = folders.get('files', [])

            if not folder_list:
                return None, "Aucun dossier trouvé dans le grand-parent."

            return folder_list, "Liste des dossiers récupérée avec succès."
        except Exception as e:
            return None, f"Erreur lors de la récupération des dossiers : {str(e)}"

    def get_invoice_folder_by_year(self,user_id: str, file_id: str ):
        """Version singleton avec user_id obligatoire"""
        try:
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)

            file = drive_service.files().get(fileId=file_id, fields='parents').execute()
            parent_id = file.get('parents')[0] if file.get('parents') else None

            if not parent_id:
                return None, "Aucun dossier parent trouvé pour le fichier donné."

            folder = drive_service.files().get(fileId=parent_id, fields='parents').execute()
            grandparent_id = folder.get('parents')[0] if folder.get('parents') else None

            if not grandparent_id:
                return parent_id, "Aucun grand-parent trouvé pour le dossier donné."

            return grandparent_id

        except Exception as e:
            return None, f"Erreur get_invoice_folder_by_year: {str(e)}"

    def create_or_find_folder(self,user_id: str, folder_name: str, parent_id: str = None):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
            
            folder_id = self.find_folder(user_id, folder_name, parent_id)

            if folder_id is None:
                folder_id = self.create_drive_folder(user_id, folder_name, parent_id)
                print(f"Dossier créé : {folder_name}, ID : {folder_id}")
            else:
                print(f"Dossier existant trouvé : {folder_name}, ID : {folder_id}")

            return folder_id, folder_name
        except Exception as e:
            print(f"Erreur create_or_find_folder: {e}")
            return None, None

    def create_drive_folder(self,user_id: str, folder_name: str, parent_folder_id: str = None):
        """Version helper avec service Drive optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)

        existing_folders = self.list_drive_folders(user_id, parent_folder_id)
        for folder in existing_folders:
            if folder['name'] == folder_name:
                print(f'Un dossier avec le nom "{folder_name}" existe déjà sous id {folder["id"]}')
                return folder['id']

        # Vérifier que le parent_folder_id existe si fourni
        if parent_folder_id:
            try:
                service.files().get(fileId=parent_folder_id, fields='id').execute()
            except Exception as e:
                print(f'❌ Erreur: Le dossier parent {parent_folder_id} n\'existe pas ou n\'est pas accessible: {e}')
                return None

        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id] if parent_folder_id else []
        }

        try:
            folder = service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
            print(f'✅ Création du dossier {folder_name} sous id {folder_id} sur Drive effectuée')
            return folder_id
        except Exception as e:
            error_msg = str(e)
            if '404' in error_msg or 'notFound' in error_msg:
                print(f'❌ Erreur lors de la création du dossier Drive : Le dossier parent {parent_folder_id} n\'existe pas ou n\'est pas accessible avec les credentials de l\'utilisateur {user_id}')
            else:
                print(f'❌ Erreur lors de la création du dossier Drive : {e}')
            return None

    def find_folder(self,user_id: str, folder_name: str, parent_id: str = None):
        """Version helper avec service Drive optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

        if parent_id:
            query += f" and '{parent_id}' in parents"

        try:
            response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            files = response.get('files', [])
            if files:
                return files[0]['id']
            else:
                return None
        except Exception as e:
            print(f"Erreur lors de la recherche du dossier : {e}")
            return None

    def create_folder_and_move_file(self,user_id: str, folder_name: str, file_id: str, parent_id: str):
        """Version singleton avec user_id optionnel"""
        try:
            

            folder_id, _ = self.create_or_find_folder(user_id, folder_name, parent_id)
            self.move_file(user_id, file_id, folder_id)

            return folder_id, file_id

        except Exception as e:
            print(f"Erreur create_folder_and_move_file: {e}")
            return None, None

    def delete_folder(self,user_id: str, folder_id: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           
            drive_service.files().delete(fileId=folder_id).execute()
            print("le dossier a été supprimer avec succes")
            return True
        except Exception as e:
            print(f"Une erreur s'est produite lors de la suppression du dossier : {e}")
            return False

    def create_sheet_service(self,user_id: str, user_email: str):
        """Version helper pour créer service Sheets (mode dev uniquement)"""
        if self.mode == 'prod':
            raise ValueError("create_sheet_service non disponible en mode prod")

        delegated_credentials = self.credentials.with_subject(user_email)
        return build('sheets', 'v4', credentials=delegated_credentials)

    def create_sheet_service_no_delegated_mail(self):
        """Version helper pour créer service Sheets sans délégation (mode dev uniquement)"""
        if self.mode == 'prod':
            raise ValueError("create_sheet_service_no_delegated_mail non disponible en mode prod")

        return build('sheets', 'v4', credentials=self.credentials)

    def get_folder_ids_for_client(self,user_id: str, contact_space_name: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            spreadsheet_service = build('sheets', 'v4', credentials=creds)
            

            client_data = self.get_gsheet_mapping_table(user_id, contact_space_name)

            for entry in client_data:
                input_drive_doc_id = entry.get('input_drive_doc_id')
                output_drive_doc_id = entry.get('output_drive_doc_id')
                mandat_space_id = entry.get('contact_spaces_id')
                mandat_space_name = entry.get('contact_space_name')
                client_name = entry.get('client_name')
                legal_name = entry.get('legal_name', client_name)
                uuid_id = entry.get('uuid')
                gl_sheet_id = entry.get('jrnl_setup_sheet_id', "")
                root_folder_id = entry.get('drive_client_parent_id')
                doc_folder_id = entry.get('main_doc_drive_id')
                erp_type = entry.get('erp_type', "")
                odoo_url = entry.get('odoo_url', "")
                odoo_username = entry.get('odoo_username', "")
                odoo_db = entry.get('odoo_db', "")
                secret_manager = entry.get('secret_manager', "")

                if input_drive_doc_id and output_drive_doc_id:
                    print("Folder IDs found:", input_drive_doc_id, output_drive_doc_id, mandat_space_id)
                    return input_drive_doc_id, output_drive_doc_id, mandat_space_id, mandat_space_name, client_name, legal_name, uuid_id, gl_sheet_id, root_folder_id, doc_folder_id, erp_type, odoo_url, odoo_username, secret_manager, odoo_db

            print("No valid folder IDs found for the client.")
            return None, None
        except Exception as e:
            print(f"Erreur get_folder_ids_for_client: {e}")
            return None, None

    def get_folder_name_by_id(self,user_id: str, folder_id: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            folder = drive_service.files().get(fileId=folder_id, fields='name').execute()
            return folder.get('name')
        except Exception as e:
            print(f"Erreur lors de la récupération du nom du dossier : {e}")
            return None

    def list_drive_folders(self,user_id: str, parent_id: str = None,):
        """Version helper avec service Drive optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
       
        query = "mimeType='application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        results = service.files().list(
            q=query,
            fields="nextPageToken, files(id,name, description)",
            spaces='drive',
            pageSize=100
        ).execute()

        items = results.get('files', [])
        folders = [{'id': item['id'], 'name': item['name'], 'description': item.get('description', 'Pas de description disponible')} for item in items]

        while 'nextPageToken' in results:
            page_token = results['nextPageToken']
            results = service.files().list(
                q=query,
                fields="nextPageToken, files(id, name)",
                spaces='drive',
                pageSize=100,
                pageToken=page_token
            ).execute()
            items = results.get('files', [])
            folders.extend([{'id': item['id'], 'name': item['name'], 'description': item.get('description', 'Pas de description disponible')} for item in items])
        
        return folders

    def download_file(self,user_id:str, file_id: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            request = drive_service.files().get_media(fileId=file_id)
            file_metadata = drive_service.files().get(fileId=file_id).execute()
            file_name = file_metadata.get('name')

            import io
            from googleapiclient.http import MediaIoBaseDownload

            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.seek(0)
            return fh, file_name
        except Exception as e:
            print(f"Erreur download_file: {e}")
            return None, None

    def move_files(self,user_id, file_ids, new_folder_id: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            if not isinstance(file_ids, list):
                file_ids = [file_ids]
            
            results = []
            for file_id in file_ids:
                if file_id:
                    result = self.move_file(user_id, file_id, new_folder_id )
                    if result:
                        results.append(result)
            
            return results
        except Exception as e:
            print(f"Erreur move_files: {e}")
            return []

    def move_file(self,user_id: str, file_id: str, new_folder_id: str):
        """Version helper avec service Drive optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)

        try:
            import time
            time_stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))

            file = service.files().get(fileId=file_id, fields='parents, name').execute()
            file_name = file.get('name', 'Inconnu')
            previous_parents = ",".join(file.get('parents', []))
            
            new_folder = service.files().get(fileId=new_folder_id, fields='name').execute()
            new_folder_name = new_folder.get('name', 'Inconnu')

            new_description = f"Timestamp: {time_stamp}"
            file_metadata = {'description': new_description}

            service.files().update(
                fileId=file_id,
                body=file_metadata,
                addParents=new_folder_id,
                removeParents=previous_parents,
                fields='id, parents'
            ).execute()

            return f"Fichier {file_name} déplacé avec succès dans le dossier Drive {new_folder_name}."
        except Exception as e:
            print(f"Une erreur est survenue : {e}")
            return None

    async def async_move_files(self,user_id, file_ids, new_folder_id: str):
        """Version singleton async avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            if not isinstance(file_ids, list):
                file_ids = [file_ids]
            
            results = []
            for file_id in file_ids:
                if file_id:
                    result = await self.async_move_file(user_id, file_id, new_folder_id)
                    if result:
                        results.append(result)
            
            return results
        except Exception as e:
            print(f"Erreur async_move_files: {e}")
            return []

    async def async_move_file(self,user_id: str, file_id: str, new_folder_id: str):
        """Version helper async avec service Drive optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
       
        try:
            import time
            current_timestamp = time.time()
            time_stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_timestamp))
           
            file = await asyncio.to_thread(service.files().get(fileId=file_id, fields='parents, name').execute)
            file_name = file.get('name', 'Inconnu')
            previous_parents = ",".join(file.get('parents', []))
            
            new_folder = await asyncio.to_thread(service.files().get(fileId=new_folder_id, fields='name').execute)
            new_folder_name = new_folder.get('name', 'Inconnu')

            new_description = f"Timestamp: {time_stamp}"
            file_metadata = {'description': new_description}
            
            await asyncio.to_thread(
                service.files().update(
                    fileId=file_id,
                    body=file_metadata,
                    addParents=new_folder_id,
                    removeParents=previous_parents,
                    fields='id, parents'
                ).execute
            )
            
            return f"Fichier {file_name} déplacé avec succès dans le dossier Drive {new_folder_name}."
        except Exception as e:
            print(f"Une erreur est survenue : {e}")
            return None

    def timestamp(self):
        """Méthode utilitaire"""
        import time
        current_timestamp = time.time()
        formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_timestamp))
        print(f"timestamp:[{formatted_time}]")

    def get_gsheet_mapping_table(self,user_id: str, value_to_find: str, column_name_search: str = 'contact_spaces_id'):
        """Version helper avec service Sheets optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('sheets', 'v4', credentials=creds)
       
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_mapping_id,
                range='contact'
            ).execute()
            rows = result.get('values', [])

            if not rows:
                print("Aucune donnée trouvée dans la feuille de calcul.")
                return None

            headers = rows[0]
            column_index = headers.index(column_name_search)

            filtered_rows = []
            for row in rows[1:]:
                if len(row) > column_index and row[column_index] == value_to_find:
                    filtered_rows.append(row)

            client_dict_list = []
            for contact in filtered_rows:
                if len(contact) < len(headers):
                    contact.extend([''] * (len(headers) - len(contact)))
                client_dict_list.append(dict(zip(headers, contact)))

            return client_dict_list

        except Exception as e:
            print(f"Une erreur est survenue lors de la récupération des données : {e}")
            return None
    
    def list_files_in_folder(self,user_id: str, folder_id: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            file_list = {}
            query = f"'{folder_id}' in parents and trashed = false"
            response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            
            for file in response.get('files', []):
                file_list[file.get('name')] = file.get('id')
            print(f'impression des fichiers: {file_list}')
            return file_list
        except Exception as e:
            print(f"Erreur lors de la liste des fichiers : {e}")
            return None

    
    def find_doc_to_do_folders(self,user_id: str, folder_id: str, path: list):
        """Version helper avec service Drive optionnel"""
        creds = self._initialize_prod_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
       
        folders = self.list_drive_folders(user_id, folder_id) or []
        doc_to_do_paths = {}

        for folder in folders:
            new_path = path + [folder.get('name')]

            if folder.get('name') == 'doc_to_do':
                doc_to_do_paths[folder.get('id')] = new_path

            doc_to_do_paths.update(self.find_doc_to_do_folders(user_id, folder.get('id'), new_path))

        return doc_to_do_paths

    
    
    def get_file_id_path(self, file_id: str, user_id: str = None):
        """Version singleton avec user_id optionnel"""
        try:
            if self.mode == 'prod' and user_id:
                creds = self._initialize_prod_credentials(user_id)
                drive_service = build('drive', 'v3', credentials=creds)
            else:
                drive_service = self.drive_service

            folders = []
            current_id = file_id

            while True:
                file = drive_service.files().get(fileId=current_id, fields='name, parents').execute()
                folders.append(file['name'])

                if 'parents' not in file:
                    break

                current_id = file['parents'][0]

            path = '/'.join(reversed(folders))
            return path

        except Exception as e:
            print(f"Erreur get_file_id_path: {e}")
            return None

    def is_acceptable_file_type(self, mime_type: str) -> bool:
        """Méthode utilitaire sans besoin de credentials"""
        acceptable_mime_types = [
            'application/pdf', 
            'image/jpeg', 
            'image/png', 
            'image/gif', 
            'image/webp'
        ]
        return mime_type in acceptable_mime_types

    def convert_pdf_to_png(self,user_id: str, drive_file_id: str, conversion_index: int = 0):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            file_metadata = drive_service.files().get(fileId=drive_file_id, fields='name, mimeType').execute()
            file_name = file_metadata['name']
            mime_type = file_metadata['mimeType']
            
            output_format = 'PNG' if conversion_index == 0 else 'JPEG'

            if not self.is_acceptable_file_type(mime_type):
                print(f"File {file_name} with mime type {mime_type} is not acceptable.")
                return None

            if mime_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                print(f"le fichier est dans le format attendu pas besoin de transformation. {mime_type}")
                request = drive_service.files().get_media(fileId=drive_file_id)

                import io
                from googleapiclient.http import MediaIoBaseDownload

                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request, chunksize=204800)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                fh.seek(0)
                return [fh]

            elif mime_type == 'application/pdf':
                print(f"le fichier est en format pdf et doit etre converti en images {output_format}. {mime_type}")
                request = drive_service.files().get_media(fileId=drive_file_id)

                import io
                from googleapiclient.http import MediaIoBaseDownload
                from pdf2image import convert_from_bytes

                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request, chunksize=204800)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                fh.seek(0)

                pages = convert_from_bytes(fh.read(), fmt='png', dpi=200)

                image_data_list = []
                for page in pages:
                    img_data = io.BytesIO()
                    page.save(img_data, format=output_format)
                    img_data.seek(0)
                    image_data_list.append(img_data)

                return image_data_list

            else:
                raise ValueError("Unsupported file type")

        except Exception as e:
            print(f"Erreur convert_pdf_to_png: {e}")
            return None

    
    def update_folder_description(self,user_id:str, folder_id: str, new_description: str):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
           

            folder_metadata = {'description': new_description}
            drive_service.files().update(fileId=folder_id, body=folder_metadata).execute()
            print(f"Description mise à jour pour le dossier ID: {folder_id}")
        except Exception as e:
            print(f"Erreur lors de la mise à jour de la description du dossier : {e}")

    def share_folder_with_email(self,user_id:str, folder_id: str, email_address: str, role: str = 'writer'):
        """Version singleton avec user_id optionnel"""
        try:
            
            creds = self._initialize_prod_credentials(user_id)
            drive_service = build('drive', 'v3', credentials=creds)
            

            user_permission = {
                'type': 'user',
                'role': role,
                'emailAddress': email_address
            }
            drive_service.permissions().create(
                fileId=folder_id,
                body=user_permission,
                fields='id',
            ).execute()
            print(f"Dossier {folder_id} partagé avec {email_address} en tant que {role}.")
        except Exception as e:
            print(f"Erreur lors du partage du dossier : {e}")

    
    def clean_file_name(self, file_name: str) -> str:
        """Nettoie le nom de fichier"""
        import re
        file_name = re.sub(r'[^\w\s.-]', '', file_name)
        file_name = re.sub(r'\s+', '_', file_name)
        return file_name

    def filter_out_doc_to_do(self, folders):
        """Filtre les dossiers pour exclure 'doc_to_do'"""
        return [folder for folder in folders if folder['name'] != 'doc_to_do']


# === FONCTIONS HELPER ===
def filter_out_doc_to_do(folders):
    """Filtre les dossiers pour exclure 'doc_to_do'"""
    return [folder for folder in folders if folder['name'] != 'doc_to_do']


def get_drive_client_service(mode: str = 'prod') -> DriveClientServiceSingleton:
    """Factory function pour récupérer l'instance singleton"""
    return DriveClientServiceSingleton(mode=mode)


# Garder la compatibilité avec l'ancien nom de classe
DriveClientService = DriveClientServiceSingleton