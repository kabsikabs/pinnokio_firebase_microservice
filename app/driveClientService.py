

import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from .tools.g_cred import get_secret
from .firebase_providers import FirebaseManagement


class DriveClientService:

    def __init__(self,service_account_info=None,user_email=None, scopes=None,mode=None,user_id=None):
        """ Initialise un client Drive et Sheets authentifié avec les informations du compte de service spécifiées. """
        try:
            if mode is None:
                mode='prod'
            else:
                mode=mode

            if mode=='dev':
                
                if service_account_info is None:
                    #service_account_info=os.getenv('GOOGLE_SERVICE_ACCOUNT_SECRET')
                    service_account_info = json.loads(get_secret(os.getenv('GOOGLE_SERVICE_ACCOUNT_SECRET')))
                # Définition des scopes si non fournis
                if scopes is None:
                    #scopes = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/documents']
                    scopes = os.getenv("GOOGLE_DRIVE_SCOPES").split(",")
                # Création des credentials et initialisation des services
                try:
                    self.credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=scopes)
                    self.drive_service = build('drive', 'v3', credentials=self.credentials)
                    #print('Connection réussie à Google Drive')

                    # Création du service Sheets avec délégation d'autorité
                    if user_email is None:
                        user_email=os.getenv('ADMIN_EMAIL')
                    else:
                        user_email
                    self.spreadsheet_service = self.create_sheet_service(user_email)
                    self.spreadsheet_no_deleguation= self.create_sheet_service_no_deleguated_mail()
                    
                    self.spreadsheet_mapping_id='1hZ2iicbqlP7Bid42l4yXgZFB93SnQoRUDaOGaXE1N70'
                    self.sheet_mapping_name='contact'
                # Ajout du service Google Docs
                    self.docs_service = build('docs', 'v1', credentials=self.credentials)
                except Exception as e:
                    raise RuntimeError(f"Erreur lors de la création des clients Drive, Sheets et Docs : {e}")

            if mode=='prod':
                firebase_instance= FirebaseManagement()
                service_account_info=firebase_instance.user_app_permission_token(user_id=user_id)
                print(f"impression du service_account_info : {service_account_info}")
                if not isinstance(service_account_info, dict):
                    raise ValueError("Les données des jetons (token_data) doivent être un dictionnaire.")
                
                # Définition des scopes si non fournis
                if scopes is None:
                    scopes = ['https://www.googleapis.com/auth/drive',
                            'https://www.googleapis.com/auth/spreadsheets',
                            'https://www.googleapis.com/auth/documents']

                # Création des credentials à partir des jetons
                try:
                    creds = Credentials(
                        token=service_account_info.get('token'),
                        refresh_token=service_account_info.get('refresh_token'),
                        token_uri=service_account_info.get('token_uri'),
                        client_id=service_account_info.get('client_id'),
                        client_secret=service_account_info.get('client_secret'),
                        scopes=scopes
                    )
                except Exception as e:
                    raise ValueError(f"Erreur lors de la création des credentials : {e}")

                # Gestion du rafraîchissement des tokens
                if creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        # Mettez à jour les jetons si nécessaire
                        service_account_info['token'] = creds.token
                        service_account_info['expiry'] = creds.expiry.isoformat()
                    except Exception as e:
                        raise RuntimeError(f"Erreur lors du rafraîchissement des tokens : {e}")

                # Initialisation des services
                try:
                    self.credentials = creds
                    self.drive_service = build('drive', 'v3', credentials=creds)
                    print('Connexion réussie à Google Drive')

                    # Service Sheets
                    self.spreadsheet_service = build('sheets', 'v4', credentials=creds)
                    print('Connexion réussie à Google Sheets')

                    # Service Docs
                    self.docs_service = build('docs', 'v1', credentials=creds)
                    print('Connexion réussie à Google Docs')

                    self.spreadsheet_mapping_id = '1hZ2iicbqlP7Bid42l4yXgZFB93SnQoRUDaOGaXE1N70'
                    self.sheet_mapping_name = 'contact'
                except Exception as e:
                    raise RuntimeError(f"Erreur lors de la création des clients Drive, Sheets et Docs : {e}")

                # Vérification d'accès immédiate pour détecter invalid_grant le plus tôt possible
                try:
                    # Appel léger pour valider l'accès Drive
                    self.drive_service.files().list(pageSize=1, fields='files(id)').execute()
                except Exception as e:
                    msg = str(e)
                    # Remonter un signal explicite qu'un re-consent est requis
                    if 'invalid_grant' in msg.lower():
                        raise RuntimeError('invalid_grant: Token has been expired or revoked.')
                    raise


        except Exception as e:
                raise RuntimeError(f"Erreur lors de la création des clients Drive, Sheets et Docs : {e}")

    def router_metadata_dict_from_drive(self,uuid_id,client_name,source,mandat_space_id,mandat_space_name,pinnokio_func,legal_name,input_drive_doc_id):  
        collected_data = []
        treated_files_info = []
        
        try:
            results = self.drive_service.files().list(q=f"'{input_drive_doc_id}' in parents",fields='files(id, name, webViewLink)').execute()
            items = results.get('files', [])
            #print(f"impression de results:{results}")
        # Vérifiez si des fichiers ont été trouvés
            if not items:
                raise ValueError("Aucun fichier disponible, veuillez vérifier le dossier doc_to_do s'il est composé de document")

            for file in items:
                file_id = file['id']
                original_file_name = file['name']
                file_name = self.clean_file_name(original_file_name)
                file_link = file.get('webViewLink', 'No link found')
                _, extension = os.path.splitext(file_name)

                file_name_without_extension = os.path.splitext(file_name)[0]
                print(f"imprsssion de file_name_without_extension : {file_name_without_extension}\n impression extension : {extension}")
                
                if extension in ['.pdf', '.txt', '.csv', '.jpeg','.jpg', '.xls', '.xlsx', '.doc', '.docx','.png', '.PNG','.JPEG','.JPG']:
                    print(f"Traitement du fichier: {file_name}")

                    
                    # Création du dictionnaire de données
                    data_dict = {
                        'uuid':uuid_id,
                        'client': client_name,
                        'source': source,
                        'mandat_id': mandat_space_id,
                        'mandat_name':mandat_space_name,
                        'file_name': file_name,
                        'pinnokio_func': pinnokio_func,
                        'extension':extension,
                        'uri_drive_link': file_link,
                        'file_name_wo_ext':file_name_without_extension,
                        'legal_name':legal_name,
                        'drive_file_id':file_id,
                    }
                    collected_data.append(data_dict)
                    #print(f"impression de file_name_withou_extension:{file_name_without_extension}")
                    # Enregistrez les informations du fichier traité
                    treated_files_info.append((file_name, extension))

                    # Traitement des fichiers avec llama_process_document
                   
            print(f"impression de collected_data : {collected_data}\n treated file info: {treated_files_info}\n")
          
            return collected_data, treated_files_info
        except Exception as e:
           return {"erreur": str(e)}, None

    async def list_files_in_doc_to_do(self, input_drive_doc_id):
        try:
            fields = 'files(id, name, mimeType, createdTime, modifiedTime, size, webViewLink, owners, permissions, shared, fileExtension, quotaBytesUsed)'
            
            # Créer la requête
            request = self.drive_service.files().list(
                q=f"'{input_drive_doc_id}' in parents and trashed = false",
                fields=fields
            )
            
            # Exécuter la requête de manière asynchrone
            import functools
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                functools.partial(request.execute)
            )
            
            items = results.get('files', [])
            #print(f"impression de results:{results}")
            return items
            
        except Exception as e:
            print(f"Erreur dans list_files_in_doc_to_do: {str(e)}")
            return {"erreur": str(e)}, None
        

    def classify_invoices_in_folder_to_delete(self, fiscal_year: str, parent_id: str):
        """Classifie les factures dans le dossier spécifié pour l'année fiscale donnée."""
        # Obtenir l'ID du dossier racine pour l'année fiscale
        fiscal_year_folder_id = self.find_folder(fiscal_year, parent_id)  # Assurez-vous que cette fonction existe
        if fiscal_year_folder_id is None:
            # Si aucun dossier pour l'année fiscale n'est trouvé, renvoyer un message d'erreur
            error_message = (f"Le classeur défini (ID : {parent_id}) ne dispose pas d'une année fiscale définie. "
                            "Veuillez vérifier que le dossier correct est utilisé comme parent.")
            print(error_message)
            return error_message

        print(f"impression de fiscal_year_folder_id: {fiscal_year_folder_id}")

        # Obtenir l'ID du dossier 'ACCOUNTING'
        accounting_folder_id = self.find_folder('ACCOUNTING', parent_id=fiscal_year_folder_id)
        if accounting_folder_id is None:
            # Gestion optionnelle de l'absence du dossier 'ACCOUNTING'
            print(f"Aucun dossier 'ACCOUNTING' trouvé dans l'année fiscale spécifiée {fiscal_year}.")
            return None

        print(f"impression de accounting_folder_id: {accounting_folder_id}")

        # Rechercher le dossier 'INVOICES' dans le dossier 'ACCOUNTING'
        invoices_folder_id = self.find_folder('INVOICES', parent_id=accounting_folder_id)
        if invoices_folder_id is None:
            # Gestion optionnelle de l'absence du dossier 'INVOICES'
            print(f"Aucun dossier 'INVOICES' trouvé dans le dossier 'ACCOUNTING'.")
            return None

        # Récupérer tous les dossiers dans 'INVOICES'
        invoice_folders = self.list_drive_folders(invoices_folder_id)
        filtered_invoice_folder=self.filter_out_doc_to_do(invoice_folders)
        
        return f"voici les dossiers disponible , si le fournisseur recherché n'existe pas il faut le créer :{filtered_invoice_folder}"

    def filter_out_doc_to_do(self,folders):
        return [folder for folder in folders if folder['name'] != 'doc_to_do']

    def add_comments(self,file_id, comment_text):
        """Ajoute un commentaire à un fichier spécifique sur Google Drive."""
   
        body = {
            'content': comment_text
        }
        try:
            return self.drive_service.comments().create(fileId=file_id, body=body, fields='id').execute()
        except Exception as e:
            print(f"Une erreur est survenue : {e}")
            return None

    def push_file_to_human_review_folder(self,parent_folder_id,human_review_folder_name,drive_file_id,comments=None):
        human_review_folder_id=self.find_folder_by_name(human_review_folder_name,parent_folder_id)
        if human_review_folder_id:
            move_file=self.move_file(drive_file_id,human_review_folder_id)
            if comments is not None:
                self.add_comments(drive_file_id,comments)
                print(f"l'ajoute des commentaires à été effectués")
            if move_file:
                print("Fichier déplacé dans le classeur 'Human_review'")
                return True
            else:
                print("Erreur lors du déplacement du fichier")
                return False
        else:
            print("Le dossier 'Human_review' n'a pas été trouvé")
            return False

    def find_folder_by_name(self, folder_name, parent_id=None):
        """Recherche un dossier par son nom dans Google Drive.

        Args:
            folder_name (str): Le nom du dossier à rechercher.
            parent_id (str, optional): L'ID du dossier parent dans lequel effectuer la recherche. Recherche dans tout Drive si None.

        Returns:
            str: L'ID du dossier trouvé, ou None si aucun dossier correspondant n'est trouvé.
        """
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder'"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        try:
            response = self.drive_service.files().list(q=query,
                                                    spaces='drive',
                                                    fields='files(id, name)').execute()
            folders = response.get('files', [])
            
            # Retourne l'ID du premier dossier trouvé qui correspond au nom
            if folders:
                return folders[0].get('id')
            else:
                return None
        except Exception as e:
            print(f"Erreur lors de la recherche du dossier : {e}")
            return None

    
    def Archived_Pinnokio_folder(self, folder_id):
        """
        Renomme un dossier en ajoutant le préfixe 'ARCHIVED_PINNOKIO_' à son nom actuel.
        
        Arguments:
            folder_id (str): L'ID du dossier Google Drive à renommer
            
        Returns:
            bool: True si le renommage a réussi, False sinon
        """
        try:
            # Récupération des métadonnées du dossier pour obtenir son nom actuel
            folder_metadata = self.drive_service.files().get(
                fileId=folder_id, 
                fields='name, mimeType'
            ).execute()
            
            # Vérification qu'il s'agit bien d'un dossier
            if folder_metadata.get('mimeType') != 'application/vnd.google-apps.folder':
                print(f"L'ID {folder_id} ne correspond pas à un dossier.")
                return False
                
            current_name = folder_metadata.get('name')
            
            # Vérification si le dossier est déjà archivé
            if current_name.startswith('ARCHIVED_PINNOKIO_'):
                print(f"Le dossier '{current_name}' est déjà archivé.")
                return True
                
            # Construction du nouveau nom
            new_name = f"ARCHIVED_PINNOKIO_{current_name}"
            
            # Mise à jour du nom du dossier
            updated_metadata = {
                'name': new_name
            }
            
            self.drive_service.files().update(
                fileId=folder_id,
                body=updated_metadata
            ).execute()
            
            print(f"Dossier renommé avec succès: '{current_name}' → '{new_name}'")
            return True
            
        except Exception as e:
            print(f"Erreur lors du renommage du dossier {folder_id}: {str(e)}")
            return True
    
    def find_file_by_name(self, file_name, parent_id=None):
        """Recherche un fichier par son nom dans Google Drive, spécifiquement dans un dossier parent donné.

        Args:
            file_name (str): Le nom du fichier à rechercher.
            parent_id (str, optional): L'ID du dossier parent dans lequel effectuer la recherche. Recherche dans tout Drive si None.

        Returns:
            str: L'ID du fichier trouvé, ou None si aucun fichier correspondant n'est trouvé.
        """
        # Démarre avec la condition de base pour le nom du fichier
        query = f"name = '{file_name}' and mimeType != 'application/vnd.google-apps.folder'"
        
        # Ajoute la condition du parent_id si spécifié
        if parent_id:
            # Assurez-vous d'utiliser la bonne syntaxe pour inclure le parent_id
            query += f" and '{parent_id}' in parents"
        
        try:
            response = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                pageSize=10  # Vous pourriez vouloir augmenter le pageSize pour déboguer
            ).execute()
            files = response.get('files', [])
            
            # Retourne l'ID du premier fichier trouvé qui correspond au nom
            if files:
                return files[0].get('id')
            else:
                return None
        except Exception as e:
            print(f"Erreur lors de la recherche du fichier : {e}")
            return None
    
    def extract_section_and_content(self, section_info):
        # Extraction du titre de la section et de l'ID du fichier
        section_title, file_id = section_info

        # Appel à la fonction pour récupérer le contenu du fichier basé sur l'ID
        file_content = self.download_file_content(file_id)

        return section_title, file_content
    
    def download_file_content(self, file_id):
        """Télécharge ou exporte le contenu d'un fichier Google Docs en tant que chaîne de caractères."""
        file = self.drive_service.files().get(fileId=file_id, fields='mimeType').execute()
        mimeType = file['mimeType']
        
        # Définir le mimeType d'exportation en fonction du format souhaité (texte brut ou HTML)
        if mimeType.startswith('application/vnd.google-apps.document'):
            exportMimeType = 'text/plain'  # Modifier ici pour 'text/html' si vous préférez le format HTML
            
            request = self.drive_service.files().export_media(fileId=file_id, mimeType=exportMimeType)
            file_io = io.BytesIO()
            downloader = MediaIoBaseDownload(file_io, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            
            file_io.seek(0)
            content = file_io.getvalue().decode('utf-8')  # Convertir en chaîne de caractères
       
            return content
        else:
            # Gérer les cas pour les autres types de fichiers si nécessaire
            raise ValueError("Le fichier spécifié n'est pas un document Google Docs ou n'est pas pris en charge.")

    def create_google_doc_with_content(self, title, parent_id=None, content=None):
        """Crée un document Google Docs avec du contenu.

        Args:
            title (str): Le titre du document Google Docs.
            parent_id (str, optional): L'ID du dossier parent où créer le document. Créé à la racine si None.
            content (str, optional): Le contenu initial du document.

        Returns:
            str: L'ID du document créé, ou None si la création échoue.
        """
        # Créer un document Google Docs vide
        document_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.document'
        }
        if parent_id:
            document_metadata['parents'] = [parent_id]
        doc = self.drive_service.files().create(body=document_metadata, fields='id').execute()
        document_id = doc.get('id')

        if content:
            # Utiliser l'API Google Docs pour insérer le contenu
            requests = [
    {
        'insertText': {
            'location': {
                'index': 1,
            },
            'text': "\nSection 1\n"
        }
    },
    {
        'createNamedRange': {
            'name': 'Section1',
            'range': {
                'startIndex': 1,
                'endIndex': 12, # Ajustez selon la longueur de la section
            }
        }
    },
    # D'autres requêtes pour ajouter du contenu, etc.
        ]
            self.docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()

        return document_id

    def classify_invoices_in_folder(self, file_id):
        try:
            # Étape 1 : Obtenir le dossier parent du fichier
            file = self.drive_service.files().get(fileId=file_id, fields='parents').execute()
            parent_id = file.get('parents')[0] if file.get('parents') else None

            if not parent_id:
                return None, "Aucun dossier parent trouvé pour le fichier donné."

            # Étape 2 : Obtenir le grand-parent du dossier
            folder = self.drive_service.files().get(fileId=parent_id, fields='parents').execute()
            grandparent_id = folder.get('parents')[0] if folder.get('parents') else None

            if not grandparent_id:
                return None, "Aucun grand-parent trouvé pour le dossier donné."

            # Étape 3 : Lister tous les dossiers dans le grand-parent
            query = f"'{grandparent_id}' in parents and mimeType = 'application/vnd.google-apps.folder'"
            folders = self.drive_service.files().list(q=query, fields='files(id, name)').execute()
            folder_list = folders.get('files', [])

            if not folder_list:
                return None, "Aucun dossier trouvé dans le grand-parent."

            return folder_list, "Liste des dossiers récupérée avec succès."
        except Exception as e:
            return None, f"Erreur lors de la récupération des dossiers : {str(e)}"

    def get_invoice_folder_by_year(self, file_id):
        # Étape 1 : Obtenir le dossier parent du fichier
        file = self.drive_service.files().get(fileId=file_id, fields='parents').execute()
        parent_id = file.get('parents')[0] if file.get('parents') else None

        if not parent_id:
            return None, "Aucun dossier parent trouvé pour le fichier donné."

        # Étape 2 : Obtenir le dossier parent du dossier
        folder = self.drive_service.files().get(fileId=parent_id, fields='parents').execute()
        grandparent_id = folder.get('parents')[0] if folder.get('parents') else None

        if not grandparent_id:
            return parent_id, "Aucun grand-parent trouvé pour le dossier donné."

        return grandparent_id

    def create_or_find_folder(self, folder_name, parent_id=None):
        """ Crée un dossier s'il n'existe pas, sinon renvoie l'ID du dossier existant. """
        folder_id = self.find_folder(folder_name, parent_id)

        if folder_id is None:
            folder_id = self.create_drive_folder(folder_name, parent_id)
            print(f"Dossier créé : {folder_name}, ID : {folder_id}")
        else:
            print(f"Dossier existant trouvé : {folder_name}, ID : {folder_id}")

        return folder_id, folder_name

    def create_drive_folder(self, folder_name, parent_folder_id=None):
        """
        Create a new folder under the specified parent folder, after checking if a folder with the same name already exists.

        Args:
            folder_name: The name of the folder to create.
            parent_folder_id: The ID of the parent folder under which to create the new folder.

        Returns:
            The ID of the newly created or existing folder.
        """
        # Vérifier d'abord si un dossier avec le même nom existe déjà
        existing_folders = self.list_drive_folders(parent_folder_id)
        for folder in existing_folders:
            if folder['name'] == folder_name:
                print(f'Un dossier avec le nom "{folder_name}" existe déjà sous id {folder["id"]}')
                return folder['id']

        # Créer le dossier si aucun dossier existant n'a été trouvé
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id] if parent_folder_id else []
        }
        try:
            folder = self.drive_service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
            print(f'Création du dossier {folder_name} sous id {folder_id} sur Drive effectuée')
            return folder_id
        except Exception as e:
            print(f'Erreur lors de la création du dossier Drive : {e}')
            return None

    def find_folder(self, folder_name, parent_id=None):
        # Construction de la requête de base
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder'and trashed = false"

        # Ajout de la condition parent si un parent_id est fourni
        if parent_id:
            query += f" and '{parent_id}' in parents"

        # Exécution de la requête
        try:
            response = self.drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
            files = response.get('files', [])
            if files:
                return files[0]['id']
            else:
                return None
        except Exception as e:
            print(f"Erreur lors de la recherche du dossier : {e}")
            return None

    def create_folder_and_move_file(self, folder_name, file_id, parent_id):
        """
        Crée un dossier s'il n'existe pas et déplace un fichier spécifié dans ce dossier.

        Args:
            folder_name: Nom du dossier à créer ou dans lequel déplacer le fichier.
            file_id: ID du fichier à déplacer.
            parent_id: ID du dossier parent où créer le dossier. Si None, crée à la racine.

        Returns:
            Un tuple contenant l'ID du dossier et l'ID du fichier.
        """
        # Créer le dossier ou trouver son ID s'il existe déjà
        folder_id, _ = self.create_or_find_folder(folder_name, parent_id)

        # Déplacer le fichier dans le dossier
        self.move_file(file_id, folder_id)

        return folder_id, file_id

    def delete_folder(self, folder_id):
        """
        Supprime un dossier spécifié par son ID.

        Args:
            folder_id: ID du dossier à supprimer.

        Returns:
            True si le dossier a été supprimé avec succès, False sinon.
        """
        try:
            # Supprimer le dossier
            self.drive_service.files().delete(fileId=folder_id).execute()
            print(f"le dossier a été supprimer avec succes")
            return True
        except HttpError as error:
            # Gérer les erreurs éventuelles
            print(f"Une erreur s'est produite lors de la suppression du dossier : {error}")
            return False

    def create_sheet_service(self, user_email):
        """ Crée un client d'API Google Sheet avec l'authentification du compte de service et la délégation de l'autorité. """
        delegated_credentials = self.credentials.with_subject(user_email)
        sheets_api_service = build('sheets', 'v4', credentials=delegated_credentials)
        #print("Client d'API Google Sheet créé avec succès avec l'authentification du compte de service et la délégation de l'autorité.")
        return sheets_api_service
    
    def create_sheet_service_no_deleguated_mail(self):
        """ Crée un client d'API Google Sheet avec l'authentification du compte de service et la délégation de l'autorité. """
        sheets_no_deleg_api_service = build('sheets', 'v4', credentials=self.credentials)
        #print("Client d'API Google Sheet créé avec succès avec l'authentification du compte de service et la délégation de l'autorité.")
        return sheets_no_deleg_api_service
    
    def get_folder_ids_for_client(self,contact_space_name):
        
        client_data = self.get_gsheet_mapping_table(contact_space_name)

        for entry in client_data:
            input_drive_doc_id = entry.get('input_drive_doc_id')
            output_drive_doc_id = entry.get('output_drive_doc_id')
            mandat_space_id=entry.get('contact_spaces_id')
            mandat_space_name=entry.get('contact_space_name')
            client_name=entry.get('client_name')
            legal_name=entry.get('legal_name',client_name)
            uuid_id=entry.get('uuid')
            gl_sheet_id=entry.get('jrnl_setup_sheet_id',"")
            root_folder_id=entry.get('drive_client_parent_id')
            doc_folder_id=entry.get('main_doc_drive_id')
            erp_type=entry.get('erp_type',"")
            odoo_url=entry.get('odoo_url',"")
            odoo_username=entry.get('odoo_username',"")
            odoo_db=entry.get('odoo_db',"")
            secret_manager=entry.get('secret_manager',"")
            if input_drive_doc_id and output_drive_doc_id:
                print("Folder IDs found:", input_drive_doc_id, output_drive_doc_id,mandat_space_id)
                return input_drive_doc_id, output_drive_doc_id,mandat_space_id,mandat_space_name,client_name,legal_name,uuid_id,gl_sheet_id,root_folder_id,doc_folder_id,erp_type,odoo_url,odoo_username,secret_manager, odoo_db
                
        print("No valid folder IDs found for the client.")
        return None, None
    
    def get_folder_name_by_id(self, folder_id):
        try:
            # Utiliser l'API Google Drive pour obtenir les détails du dossier
            folder = self.drive_service.files().get(fileId=folder_id, fields='name').execute()
            return folder.get('name')
        except Exception as e:
            print(f"Erreur lors de la récupération du nom du dossier : {e}")
            return None

    def list_drive_folders(self, parent_id=None):
        """
        List all folders in the drive, optionally within a specified parent folder.

        Args:
            service: Authenticated Google Drive service instance.
            parent_id: The ID of the parent folder to list the folders within.

        Returns:
            A list of folders, where each folder is represented by a dictionary containing its id and name.
        """
        query = "mimeType='application/vnd.google-apps.folder'"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        results = self.drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id,name, description)",
            spaces='drive',
            pageSize=100  # You can increase or decrease this number depending on your needs
        ).execute()

        items = results.get('files', [])
        folders = [{'id': item['id'], 'name': item['name'], 'description': item.get('description', 'Pas de description disponible')} for item in items]

        while 'nextPageToken' in results:
            page_token = results['nextPageToken']
            results = self.drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name)",
                spaces='drive',
                pageSize=100,  # Same as above
                pageToken=page_token
            ).execute()
            items = results.get('files', [])
            folders.extend([{'id': item['id'], 'name': item['name'], 'description': item.get('description', 'Pas de description disponible')} for item in items])

        
        return folders

    def download_file(self, file_id):
        request = self.drive_service.files().get_media(fileId=file_id)
        file_metadata = self.drive_service.files().get(fileId=file_id).execute()
        file_name = file_metadata.get('name')
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()  
        fh.seek(0)
        return fh,file_name

    def move_files(self, file_ids, new_folder_id):
        """
        Déplace un ou plusieurs fichiers vers un nouveau dossier.
        
        Args:
            file_ids: Peut être un seul ID de fichier (str) ou une liste d'IDs
            new_folder_id: ID du dossier de destination
            
        Returns:
            Liste des résultats pour chaque fichier déplacé
        """
        # Convertir en liste si un seul ID est fourni
        if not isinstance(file_ids, list):
            file_ids = [file_ids]
        
        results = []
        for file_id in file_ids:
            # Ignorer les valeurs vides ou None
            if file_id:
                result = self.move_file(file_id, new_folder_id)
                if result:
                    results.append(result)
        
        return results

    def move_file(self, file_id, new_folder_id):
        try:
            time_stamp = self.timestamp()

            # Récupération des informations sur le fichier
            file = self.drive_service.files().get(fileId=file_id, fields='parents, name').execute()
            file_name = file.get('name', 'Inconnu')
            previous_parents = ",".join(file.get('parents', []))
            
            # Récupération du nom du nouveau dossier
            new_folder = self.drive_service.files().get(fileId=new_folder_id, fields='name').execute()
            new_folder_name = new_folder.get('name', 'Inconnu')

            # Mise à jour de la description du fichier
            new_description = f"Timestamp: {time_stamp}"
            file_metadata = {'description': new_description}

            # Déplacement du fichier vers le nouveau dossier
            self.drive_service.files().update(
                fileId=file_id,
                body=file_metadata,
                addParents=new_folder_id,
                removeParents=previous_parents,
                fields='id, parents').execute()


            return f"Fichier {file_name} déplacé avec succès dans le dossier Drive {new_folder_name}."
        except Exception as e:
            print(f"Une erreur est survenue : {e}")
            return None

    async def async_move_files(self, file_ids, new_folder_id):
        """
        Déplace un ou plusieurs fichiers vers un nouveau dossier de manière asynchrone.
        
        Args:
            file_ids: Peut être un seul ID de fichier (str) ou une liste d'IDs
            new_folder_id: ID du dossier de destination
            
        Returns:
            Liste des résultats pour chaque fichier déplacé
        """
        # Convertir en liste si un seul ID est fourni
        if not isinstance(file_ids, list):
            file_ids = [file_ids]
        
        results = []
        for file_id in file_ids:
            # Ignorer les valeurs vides ou None
            if file_id:
                result = await self.async_move_file(file_id, new_folder_id)
                if result:
                    results.append(result)
        
        return results

    async def async_move_file(self, file_id, new_folder_id):
        try:
             # Obtenir le timestamp actuel
            current_timestamp = time.time()
            
            # Convertir le timestamp en heure locale formatée
            time_stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_timestamp))
           

            # Récupération des informations sur le fichier
            # Utiliser asyncio pour les opérations bloquantes sur l'API Drive
            file = await asyncio.to_thread(
                self.drive_service.files().get(fileId=file_id, fields='parents, name').execute
            )
            file_name = file.get('name', 'Inconnu')
            previous_parents = ",".join(file.get('parents', []))
            
            # Récupération du nom du nouveau dossier
            new_folder = await asyncio.to_thread(
                self.drive_service.files().get(fileId=new_folder_id, fields='name').execute
            )
            new_folder_name = new_folder.get('name', 'Inconnu')

            # Mise à jour de la description du fichier
            new_description = f"Timestamp: {time_stamp}"
            file_metadata = {'description': new_description}
            
            # Déplacement du fichier vers le nouveau dossier
            await asyncio.to_thread(
                self.drive_service.files().update(
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
        # Obtenir le timestamp actuel
        current_timestamp = time.time()
        
        # Convertir le timestamp en heure locale formatée
        formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_timestamp))
        
        # Imprimer le timestamp selon le modèle désiré
        print(f"timestamp:[{formatted_time}]")

    def get_gsheet_mapping_table(self, value_to_find,column_name_search='contact_spaces_id'):
        """
        Récupère les données d'une feuille Google Sheets spécifique et retourne une liste de dictionnaires
        correspondant aux lignes où la valeur de la colonne 'contact_space_name' correspond à 'value_to_find'.

        :param value_to_find: La valeur à rechercher dans la colonne 'contact_space_name'.
        :return: Liste de dictionnaires contenant les données filtrées, ou None en cas d'erreur.
        """

        try:
            # Récupération des données de la feuille de calcul
            result = self.spreadsheet_service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_mapping_id,
                range='contact'
            ).execute()
            rows = result.get('values', [])

            if not rows:
                print("Aucune donnée trouvée dans la feuille de calcul.")
                return None

            # Les en-têtes sont dans la première sous-liste
            headers = rows[0]

            # Nom de la colonne codé en dur
            contact_space_name = column_name_search

            # Trouver l'indice de la colonne 'contact_space_name'
            try:
                column_index = headers.index(contact_space_name)
            except ValueError:
                print(f"La colonne '{contact_space_name}' n'est pas trouvée dans les en-têtes.")
                return None

            # Filtrer les données en utilisant la valeur 'value_to_find' dans la colonne spécifiée
            filtered_rows = []
            for row in rows[1:]:
                if len(row) > column_index and row[column_index] == value_to_find:
                    filtered_rows.append(row)

            # Transformation en liste de dictionnaires
            client_dict_list = []
            for contact in filtered_rows:
                # Assurez-vous que chaque ligne a suffisamment d'éléments pour correspondre aux en-têtes
                if len(contact) < len(headers):
                    # Étendez la ligne avec des valeurs vides pour correspondre aux en-têtes
                    contact.extend([''] * (len(headers) - len(contact)))
                client_dict_list.append(dict(zip(headers, contact)))

            return client_dict_list

        except Exception as e:
            print(f"Une erreur est survenue lors de la récupération des données : {e}")
            return None
    
    def list_files_in_folder(self, folder_id):
        try:
            # Liste pour stocker les noms de fichier et leurs IDs
            file_list = {}

            # Requête pour lister les fichiers dans le dossier spécifié
            query = f"'{folder_id}' in parents"
            response = self.drive_service.files().list(q=query, 
                                                    spaces='drive', 
                                                    fields='files(id, name)').execute()
            
            # Ajouter chaque fichier trouvé à la liste
            for file in response.get('files', []):
                file_list[file.get('name')] = file.get('id')
            print(f'impression des fichiers: {file_list}')
            return file_list
        except Exception as e:
            print(f"Erreur lors de la liste des fichiers : {e}")
            return None

    def klk_router_app(self, fiscal_year,root_folder_id):
        """
        Recherche tous les dossiers dans Google Drive et filtre ceux nommés 'doc_to_do', capturant leur chemin, basé sur une année fiscale donnée.

        Args:
            fiscal_year: Année fiscale à utiliser comme filtre pour la recherche de dossiers.
            root_folder_id: ID du dossier racine à partir duquel la recherche commence. Si None, commence à la racine du Drive.

        Returns:
            Un dictionnaire contenant les chemins des dossiers 'doc_to_do' ou un message d'erreur si l'année fiscale n'est pas valide.
        """
        # Suppose que main_doc est récupéré correctement ici
        main_doc = self.list_files_in_folder(root_folder_id)
        # Assurez-vous que les années disponibles sont en entier pour la comparaison
        available_years = sorted([int(year) for year in main_doc.keys()])
        fiscal_year_int = int(fiscal_year)

        # Si fiscal_year_int est déjà parmi les années disponibles, on l'utilise directement.
        if fiscal_year_int in available_years:
            print(f"L'année fiscale {fiscal_year_int} est disponible.")
            fiscal_year = str(fiscal_year_int)  # Convertir en chaîne pour l'utilisation avec main_doc
        else:
            # Recherche de l'année la plus proche supérieure uniquement si fiscal_year_int n'est pas disponible
            closest_years = [year for year in available_years if year > fiscal_year_int]
            if closest_years:
                closest_year = min(closest_years)
                print(f"Le chiffre {fiscal_year_int} n'est pas disponible. Le chiffre le plus proche supérieur est {closest_year}.")
                fiscal_year = str(closest_year)  # Mise à jour de fiscal_year à l'année la plus proche
            else:
                print(f"Aucune année supérieure trouvée pour {fiscal_year_int}.")
                return None, "Aucune année fiscale valide trouvée. Veuillez vérifier les données disponibles."

        folder_id_for_year = main_doc.get(fiscal_year)

        
        if not folder_id_for_year:
            return None, "Aucune année fiscale correspondante trouvée dans les dossiers disponibles."
        # Recherchez les dossiers 'doc_to_do' et créez le DataFrame
        doc_to_do_paths = self.find_doc_to_do_folders(folder_id_for_year, [self.get_folder_name_by_id(folder_id_for_year)])
        print(f"impression de doc_to_paths: {doc_to_do_paths}")
        df = pd.DataFrame.from_dict(doc_to_do_paths, orient='index', columns=['Year', 'Department', 'Service', 'Suffix'])
        # Traitez le DataFrame (filtrage, ajout de colonnes, etc.)
        df['Service'] = df['Service'].replace({'': None, 'doc_to_do': None}).fillna(df['Department'])
        df['chroma_source'] = df.apply(lambda row: f"documents/{row['Department'].lower()}/{row['Service'].lower()}/doc_to_do", axis=1)
        # Extraire les folder_id et les attacher au DataFrame
        folder_ids = list(doc_to_do_paths.keys())  # Les clés sont les folder_id
        df['folder_id'] = folder_ids
        # Obtenez la table des fonctions pinnokio
        gsheet_function_table = self.get_gsheet_pinnokio_functions_table()
        print(f"impression de gsheet_function_table:{gsheet_function_table}")
        df_gsheet_function_table = pd.DataFrame(gsheet_function_table)
        df = df.merge(df_gsheet_function_table, left_on='Service', right_on='drive_service_doc_to_do_name', how='left')
        df.rename(columns={'function_name': 'pinnokio_func'}, inplace=True)
        df.drop(columns=['drive_service_doc_to_do_name', 'function_description'], inplace=True)

        # Affichez la liste des services disponibles et attendez l'input de l'utilisateur
        services = df['Service'].dropna().unique()
        

        return df, services

    def klk_router_wrapper(self, fiscal_year, selected_service_text, drive_file_id, chroma_ids, collection_name, root_folder_id=None):
        # Appel de la première partie
        df, services = self.klk_router_app(fiscal_year, root_folder_id)

        # Assurez-vous que le service sélectionné fait partie des services
        if selected_service_text in services:
            # Appel de la deuxième partie avec le DataFrame obtenu de la première partie
            return self.klk_router_app_part2(df, services, selected_service_text, drive_file_id, chroma_ids, collection_name)
        else:
            return "Service sélectionné non valide ou non trouvé."
    
    def find_doc_to_do_folders(self,folder_id, path):
                # Assurez-vous que folders est toujours une liste
                folders = self.list_drive_folders(folder_id) or []
                doc_to_do_paths = {}

                for folder in folders:
                    new_path = path + [folder.get('name')]  # Utilisez get pour éviter KeyError

                    if folder.get('name') == 'doc_to_do':
                        doc_to_do_paths[folder.get('id')] = new_path  # Utilisez get pour éviter KeyError

                    # Recherche récursive dans les sous-dossiers
                    doc_to_do_paths.update(self.find_doc_to_do_folders(folder.get('id'), new_path))  # Utilisez get pour éviter KeyError
                    
                return doc_to_do_paths

    def get_gsheet_pinnokio_functions_table(self):
        """
        Récupère toutes les données de l'onglet 'functions' d'une feuille Google Sheets et les retourne sous forme 
        d'une liste de dictionnaires.

        :return: Liste de dictionnaires contenant toutes les données de l'onglet 'functions', ou None en cas d'erreur.
        """
        
        try:
            # Récupération des données de la feuille de calcul
            result = self.spreadsheet_service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_mapping_id,
                range='functions'
            ).execute()
            rows = result.get('values', [])

            if not rows:
                print("Aucune donnée trouvée dans la feuille de calcul.")
                return None

            # Les en-têtes sont dans la première sous-liste
            headers = rows[0]

            # Initialisation de la liste qui va contenir les dictionnaires
            all_data_dicts = []

            # Parcours des lignes en sautant les en-têtes
            for row in rows[1:]:
                # Assurez-vous que chaque ligne a suffisamment d'éléments pour correspondre aux en-têtes
                if len(row) < len(headers):
                    # Étendez la ligne avec des valeurs vides pour correspondre aux en-têtes
                    row.extend([''] * (len(headers) - len(row)))
                all_data_dicts.append(dict(zip(headers, row)))

            return all_data_dicts

        except Exception as e:
            print(f"Une erreur est survenue lors de la récupération des données : {e}")
            return None

    def create_or_get_context_sheet(self, context_file_name, folder_id):
        # Recherche du fichier par nom
        response = self.drive_service.files().list(q=f"name='{context_file_name}' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.spreadsheet'").execute()

        files = response.get('files', [])

        if not files:
            print("Fichier 'context' non trouvé, création en cours.")
            # Création du Google Sheet avec le dossier parent spécifié lors de la création
            file_metadata = {
                'name': context_file_name,
                'mimeType': 'application/vnd.google-apps.spreadsheet',
                'parents': [folder_id]
            }
            sheet = self.drive_service.files().create(body=file_metadata, fields='id').execute()
            sheet_id = sheet['id']
            
            # Initialiser le contenu du Google Sheet
            # Préparer les données pour les headers et les premières lignes
            values = [HEADERS] + INITIAL_DATA  # Assurez-vous que HEADERS et INITIAL_DATA sont définis
            data = [{
                'range': 'A1:E',  # Ajustez en fonction du nombre d'en-têtes et de colonnes
                'values': values
            }]
            body = {
                'valueInputOption': 'USER_ENTERED',
                'data': data
            }
            self.spreadsheet_service.spreadsheets().values().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
            
            return sheet_id
        else:
            return files[0]['id']

    def get_file_id_path(self, file_id):
        """Récupère le chemin complet du fichier basé sur son file_id."""
        folders = []
        current_id = file_id

        while True:
            # Récupère les métadonnées du fichier/dossier actuel
            file = self.drive_service.files().get(fileId=current_id, fields='name, parents').execute()
            folders.append(file['name'])

            # S'il n'y a pas de parent, on a atteint la racine
            if 'parents' not in file:
                break

            # Met à jour current_id pour le parent du fichier/dossier actuel
            current_id = file['parents'][0]

        # Construit le chemin en inversant l'ordre des dossiers
        path = '/'.join(reversed(folders))
        return path

    def is_acceptable_file_type(self, mime_type):
        acceptable_mime_types = [
            'application/pdf', 
            'image/jpeg', 
            'image/png', 
            'image/gif', 
            'image/webp'
        ]
        return mime_type in acceptable_mime_types

    def convert_pdf_to_png(self, drive_file_id,conversion_index=0):
        """ conversion index 0 pour PNG 1 pour JPEG"""
        # Récupérer les métadonnées du fichier pour vérifier l'extension
        print(f"impression de drive_file_id:{drive_file_id}")
        file_metadata = self.drive_service.files().get(fileId=drive_file_id, fields='name, mimeType').execute()
        file_name = file_metadata['name']
        mime_type = file_metadata['mimeType']
        
         # Déterminer le format de sortie en fonction de conversion_index
        output_format = 'PNG' if conversion_index == 0 else 'JPEG'
        # Vérifier si le fichier est déjà dans un format d'image supporté
        if not self.is_acceptable_file_type(mime_type):
            print(f"File {file_name} with mime type {mime_type} is not acceptable.")
            return None
        if mime_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
            print(f"le fichier est dans le format attendu pas besoin de transformation. {mime_type}")
            # Téléchargement du fichier d'image directement
            request = self.drive_service.files().get_media(fileId=drive_file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request, chunksize=204800)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.seek(0)
            return [fh]
        
        elif mime_type == 'application/pdf':
            print(f"le fichier est en format pdf et doit etre converti en images {output_format}. {mime_type}")
            # Téléchargement du fichier PDF
            request = self.drive_service.files().get_media(fileId=drive_file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request, chunksize=204800)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.seek(0)

            # Conversion du PDF en images
            pages = convert_from_bytes(fh.read(), fmt='png', dpi=200)
            
            # Créer une liste pour stocker les images
            image_data_list = []
            
            # Convertir chaque page au format choisi et l'ajouter à la liste
            for page in pages:
                img_data = io.BytesIO()
                page.save(img_data, format=output_format)
                img_data.seek(0)
                image_data_list.append(img_data)

            return image_data_list

        else:
            raise ValueError("Unsupported file type")

    def load_context_column_data(self, spreadsheet_id):
        """Charge le contenu des colonnes 'context' (E), 'action_step' (D) et une colonne non spécifiée (B)
        d'un Google Sheet et retourne un dictionnaire avec des clés basées sur la valeur de la colonne B."""
        try:
            # Spécifie la plage pour inclure les colonnes B, 'action_step' (D) et 'context' (E)
            range_name = 'B2:E'  # Assurez-vous de démarrer à la ligne 2 pour éviter l'en-tête
            result = self.spreadsheet_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=range_name).execute()
            values = result.get('values', [])

            # Créer un dictionnaire pour stocker le contenu de la colonne 'context' avec des clés basées sur la valeur de la colonne B
            context_variables = {}
            for row in values:
                # Gère les lignes vides et les cas où les colonnes B, 'action_step' ou 'context' sont vides
                column_b_value = row[0] if len(row) > 0 and row[0] else "undefined"
                context_value = row[3] if len(row) > 3 and row[3] else ""

                # Construction de la clé basée sur la valeur de la colonne B
                variable_name = f"{column_b_value}"
                context_variables[variable_name] = context_value
            
            return context_variables
        except HttpError as error:
            print(f"Une erreur est survenue: {error}")
            return None

    def update_folder_description(self, folder_id, new_description):
        """
        Met à jour la description d'un dossier dans Google Drive.

        Args:
            folder_id: L'ID du dossier à mettre à jour.
            new_description: La nouvelle description du dossier.

        """
    
        try:
            folder_metadata = {'description': new_description}
            self.drive_service.files().update(
                fileId=folder_id,
                body=folder_metadata
            ).execute()
            print(f"Description mise à jour pour le dossier ID: {folder_id}")
        except Exception as e:
            print(f"Erreur lors de la mise à jour de la description du dossier : {e}")

    def share_folder_with_email(self, folder_id, email_address, role='writer'):
        """
        Partage un dossier dans Google Drive avec une adresse email spécifiée.

        Args:
            folder_id: L'ID du dossier à partager.
            email_address: L'adresse email avec laquelle partager le dossier.
            role: Le rôle à attribuer à l'utilisateur partagé ('reader' ou 'writer').

        Returns:
            None. Imprime un message de confirmation ou d'erreur.
        """
        try:
            user_permission = {
                'type': 'user',
                'role': role,
                'emailAddress': email_address
            }
            self.drive_service.permissions().create(
                fileId=folder_id,
                body=user_permission,
                fields='id',
            ).execute()
            print(f"Dossier {folder_id} partagé avec {email_address} en tant que {role}.")
        except Exception as e:
            print(f"Erreur lors du partage du dossier : {e}")

