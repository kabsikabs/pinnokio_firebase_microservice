"""
Outils pour FileManager Pinnokio Agent
=======================================

Implémentations des outils de gestion de fichiers DMS.

Auteur: Assistant IA
Date: 2025
"""

from typing import Dict, List, Any, Optional
from tools.g_cred import DriveClientService
from functools import partial


class FileManagerTools:
    """
    Classe contenant toutes les implémentations d'outils pour FileManager.
    """
    
    def __init__(self, drive_service: DriveClientService, root_folder_id: Optional[str] = None):
        """
        Initialise les outils avec le service Drive.
        
        Args:
            drive_service (DriveClientService): Instance du service Drive
            root_folder_id (str, optional): ID du dossier racine client (drive_client_parent_id)
        """
        self.drive_service = drive_service
        self.root_folder_id = root_folder_id
    
    def search_file_in_dms(self, 
                           file_name: Optional[str] = None,
                           folder_id: Optional[str] = None,
                           mime_type: Optional[str] = None,
                           max_results: int = 20) -> Dict:
        """
        Recherche des fichiers dans le DMS selon les critères.
        
        Args:
            file_name (str): Nom du fichier à rechercher (partiel accepté)
            folder_id (str): ID du dossier où chercher (si None, utilise root_folder_id)
            mime_type (str): Type MIME du fichier
            max_results (int): Nombre max de résultats
            
        Returns:
            Dict: Résultats de la recherche
        """
        try:
            # Utiliser root_folder_id si folder_id n'est pas fourni
            search_folder_id = folder_id or self.root_folder_id
            
            # Construire la requête de recherche
            query_parts = []
            
            if file_name:
                query_parts.append(f"name contains '{file_name}'")
            
            if search_folder_id:
                query_parts.append(f"'{search_folder_id}' in parents")
            
            if mime_type:
                query_parts.append(f"mimeType = '{mime_type}'")
            
            # Toujours exclure les fichiers supprimés
            query_parts.append("trashed=false")
            
            query = " and ".join(query_parts) if query_parts else "trashed=false"
            
            # Rechercher dans Drive
            results = []
            page_token = None
            
            while len(results) < max_results:
                response = self.drive_service.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='nextPageToken, files(id, name, mimeType, parents, createdTime, modifiedTime, size, webViewLink)',
                    pageToken=page_token,
                    pageSize=min(max_results - len(results), 100)
                ).execute()
                
                results.extend(response.get('files', []))
                page_token = response.get('nextPageToken')
                
                if not page_token:
                    break
            
            return {
                'success': True,
                'count': len(results),
                'files': results[:max_results]
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'count': 0,
                'files': []
            }
    
    def create_folder_in_dms(self, 
                            folder_name: str,
                            parent_folder_id: Optional[str] = None,
                            description: Optional[str] = None) -> Dict:
        """
        Crée un nouveau dossier dans le DMS.
        
        Args:
            folder_name (str): Nom du dossier à créer
            parent_folder_id (str): ID du dossier parent (si None, utilise root_folder_id)
            description (str): Description du dossier (optionnel)
            
        Returns:
            Dict: Informations sur le dossier créé
        """
        try:
            # Utiliser root_folder_id si parent_folder_id n'est pas fourni
            target_parent_id = parent_folder_id or self.root_folder_id
            
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            
            if description:
                file_metadata['description'] = description
            
            if target_parent_id:
                file_metadata['parents'] = [target_parent_id]
            
            folder = self.drive_service.drive_service.files().create(
                body=file_metadata,
                fields='id, name, webViewLink, createdTime'
            ).execute()
            
            return {
                'success': True,
                'folder_id': folder.get('id'),
                'folder_name': folder.get('name'),
                'folder_url': folder.get('webViewLink'),
                'created_time': folder.get('createdTime')
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def move_file_in_dms(self, 
                        file_id: str,
                        new_parent_folder_id: str,
                        remove_from_current: bool = True) -> Dict:
        """
        Déplace un fichier vers un autre dossier.
        
        Args:
            file_id (str): ID du fichier à déplacer
            new_parent_folder_id (str): ID du nouveau dossier parent
            remove_from_current (bool): Supprimer des dossiers actuels
            
        Returns:
            Dict: Résultat du déplacement
        """
        try:
            # Récupérer les parents actuels si besoin de les supprimer
            previous_parents = ""
            
            if remove_from_current:
                file = self.drive_service.drive_service.files().get(
                    fileId=file_id,
                    fields='parents'
                ).execute()
                previous_parents = ",".join(file.get('parents', []))
            
            # Déplacer le fichier
            file = self.drive_service.drive_service.files().update(
                fileId=file_id,
                addParents=new_parent_folder_id,
                removeParents=previous_parents if remove_from_current else None,
                fields='id, name, parents, webViewLink'
            ).execute()
            
            return {
                'success': True,
                'file_id': file.get('id'),
                'file_name': file.get('name'),
                'new_parents': file.get('parents'),
                'file_url': file.get('webViewLink')
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def rename_file_in_dms(self, 
                          file_id: str,
                          new_name: str) -> Dict:
        """
        Renomme un fichier ou dossier.
        
        Args:
            file_id (str): ID du fichier à renommer
            new_name (str): Nouveau nom
            
        Returns:
            Dict: Résultat du renommage
        """
        try:
            file = self.drive_service.drive_service.files().update(
                fileId=file_id,
                body={'name': new_name},
                fields='id, name, webViewLink, modifiedTime'
            ).execute()
            
            return {
                'success': True,
                'file_id': file.get('id'),
                'old_name': None,  # Pas récupéré pour éviter un appel supplémentaire
                'new_name': file.get('name'),
                'file_url': file.get('webViewLink'),
                'modified_time': file.get('modifiedTime')
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def delete_file_in_dms(self, 
                          file_id: str,
                          permanent: bool = False) -> Dict:
        """
        Supprime un fichier ou dossier (corbeille ou permanent).
        
        Args:
            file_id (str): ID du fichier à supprimer
            permanent (bool): Si True, suppression permanente. Sinon, corbeille.
            
        Returns:
            Dict: Résultat de la suppression
        """
        try:
            if permanent:
                # Suppression permanente
                self.drive_service.drive_service.files().delete(
                    fileId=file_id
                ).execute()
                
                return {
                    'success': True,
                    'file_id': file_id,
                    'deletion_type': 'permanent',
                    'message': 'Fichier supprimé définitivement'
                }
            else:
                # Déplacer vers la corbeille
                file = self.drive_service.drive_service.files().update(
                    fileId=file_id,
                    body={'trashed': True},
                    fields='id, name, trashed'
                ).execute()
                
                return {
                    'success': True,
                    'file_id': file.get('id'),
                    'file_name': file.get('name'),
                    'deletion_type': 'trashed',
                    'message': 'Fichier déplacé vers la corbeille'
                }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_file_metadata(self, 
                         file_id: str,
                         fields: Optional[str] = None) -> Dict:
        """
        Récupère les métadonnées d'un fichier.
        
        Args:
            file_id (str): ID du fichier
            fields (str): Champs à récupérer (optionnel)
            
        Returns:
            Dict: Métadonnées du fichier
        """
        try:
            if fields is None:
                fields = 'id, name, mimeType, parents, createdTime, modifiedTime, size, webViewLink, owners, permissions, shared, description'
            
            file = self.drive_service.drive_service.files().get(
                fileId=file_id,
                fields=fields
            ).execute()
            
            return {
                'success': True,
                'metadata': file
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def list_folder_contents(self, 
                            folder_id: str,
                            recursive: bool = False,
                            max_depth: int = 3) -> Dict:
        """
        Liste le contenu d'un dossier.
        
        Args:
            folder_id (str): ID du dossier
            recursive (bool): Lister récursivement les sous-dossiers
            max_depth (int): Profondeur maximale si recursive=True
            
        Returns:
            Dict: Contenu du dossier
        """
        try:
            contents = []
            
            def list_folder_recursive(fid, depth=0):
                if depth > max_depth:
                    return
                
                query = f"'{fid}' in parents and trashed=false"
                
                results = self.drive_service.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name, mimeType, size, webViewLink, modifiedTime)',
                    pageSize=100
                ).execute()
                
                files = results.get('files', [])
                
                for file in files:
                    file_info = {
                        **file,
                        'depth': depth,
                        'parent_id': fid
                    }
                    contents.append(file_info)
                    
                    # Si c'est un dossier et mode récursif, explorer
                    if recursive and file.get('mimeType') == 'application/vnd.google-apps.folder':
                        list_folder_recursive(file.get('id'), depth + 1)
            
            list_folder_recursive(folder_id)
            
            return {
                'success': True,
                'folder_id': folder_id,
                'count': len(contents),
                'contents': contents
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def copy_file_in_dms(self, 
                        file_id: str,
                        new_name: Optional[str] = None,
                        destination_folder_id: Optional[str] = None) -> Dict:
        """
        Copie un fichier dans le DMS.
        
        Args:
            file_id (str): ID du fichier à copier
            new_name (str): Nom de la copie (optionnel)
            destination_folder_id (str): ID du dossier destination (optionnel)
            
        Returns:
            Dict: Informations sur la copie créée
        """
        try:
            body = {}
            
            if new_name:
                body['name'] = new_name
            
            if destination_folder_id:
                body['parents'] = [destination_folder_id]
            
            copied_file = self.drive_service.drive_service.files().copy(
                fileId=file_id,
                body=body,
                fields='id, name, mimeType, parents, webViewLink, createdTime'
            ).execute()
            
            return {
                'success': True,
                'original_file_id': file_id,
                'copied_file_id': copied_file.get('id'),
                'copied_file_name': copied_file.get('name'),
                'copied_file_url': copied_file.get('webViewLink'),
                'created_time': copied_file.get('createdTime')
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Retourne le schéma JSON de tous les outils FileManager.
        
        Returns:
            List[Dict]: Liste des schémas d'outils
        """
        return [
            {
                "name": "search_file_in_dms",
                "description": "Recherche des fichiers dans le DMS selon les critères. Supporte la recherche par nom (partiel), dossier parent, type MIME.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_name": {
                            "type": "string",
                            "description": "Nom du fichier à rechercher (recherche partielle supportée)"
                        },
                        "folder_id": {
                            "type": "string",
                            "description": "ID du dossier où effectuer la recherche"
                        },
                        "mime_type": {
                            "type": "string",
                            "description": "Type MIME du fichier (ex: 'application/pdf', 'application/vnd.google-apps.document')"
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Nombre maximum de résultats à retourner (défaut: 20)"
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "create_folder_in_dms",
                "description": "Crée un nouveau dossier dans le DMS. Utilisez pour organiser les fichiers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "folder_name": {
                            "type": "string",
                            "description": "Nom du dossier à créer"
                        },
                        "parent_folder_id": {
                            "type": "string",
                            "description": "ID du dossier parent où créer le nouveau dossier (optionnel)"
                        },
                        "description": {
                            "type": "string",
                            "description": "Description du dossier (optionnel)"
                        }
                    },
                    "required": ["folder_name"]
                }
            },
            {
                "name": "move_file_in_dms",
                "description": "Déplace un fichier ou dossier vers un autre emplacement dans le DMS.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "ID du fichier ou dossier à déplacer"
                        },
                        "new_parent_folder_id": {
                            "type": "string",
                            "description": "ID du nouveau dossier parent de destination"
                        },
                        "remove_from_current": {
                            "type": "boolean",
                            "description": "Si true, retire le fichier de ses dossiers actuels (défaut: true)"
                        }
                    },
                    "required": ["file_id", "new_parent_folder_id"]
                }
            },
            {
                "name": "rename_file_in_dms",
                "description": "Renomme un fichier ou dossier dans le DMS.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "ID du fichier ou dossier à renommer"
                        },
                        "new_name": {
                            "type": "string",
                            "description": "Nouveau nom à attribuer"
                        }
                    },
                    "required": ["file_id", "new_name"]
                }
            },
            {
                "name": "delete_file_in_dms",
                "description": "⚠️ Supprime un fichier ou dossier (corbeille ou permanent). ATTENTION: L'utiliser uniquement sur demande explicite !",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "ID du fichier ou dossier à supprimer"
                        },
                        "permanent": {
                            "type": "boolean",
                            "description": "Si true, suppression définitive. Si false, déplace vers la corbeille (défaut: false)"
                        }
                    },
                    "required": ["file_id"]
                }
            },
            {
                "name": "get_file_metadata",
                "description": "Récupère les métadonnées détaillées d'un fichier (propriétaire, date création, taille, permissions, etc.).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "ID du fichier dont récupérer les métadonnées"
                        }
                    },
                    "required": ["file_id"]
                }
            },
            {
                "name": "get_departement_prompt",
                "description": "Charge le contexte métier spécifique d'un département (règles, workflows, structure). Utilisez pour comprendre comment organiser les fichiers dans un département.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "department": {
                            "type": "string",
                            "description": "Nom du département (banks_cash, invoices, expenses, hr, legal_administration, etc.)"
                        }
                    },
                    "required": ["department"]
                }
            },
            {
                "name": "vision_document",
                "description": "Analyse visuelle d'un document via IA (PDF, image, scan). Utilisez pour extraire des informations d'un document sans le télécharger.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "ID Google Drive du fichier à analyser"
                        },
                        "question": {
                            "type": "string",
                            "description": "Question à poser sur le document (ex: 'Quelle est la date?', 'Quel est le montant total?')"
                        }
                    },
                    "required": ["file_id", "question"]
                }
            },
            {
                "name": "create_fiscal_year_structure",
                "description": "Crée automatiquement la structure complète de dossiers pour une année fiscale avec tous les départements. Utilisez pour initialiser une nouvelle année.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fiscal_year": {
                            "type": "integer",
                            "description": "Année fiscale à créer (ex: 2025, 2026)"
                        }
                    },
                    "required": ["fiscal_year"]
                }
            },
            {
                "name": "CALL_DRIVE_AGENT",
                "description": "⭐ Délègue les manipulations de fichiers au DriveAgent spécialisé. Utilisez pour toutes les opérations de recherche, déplacement, copie, renommage de fichiers dans Drive.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "instructions": {
                            "type": "string",
                            "description": "Instructions détaillées pour le DriveAgent sur les opérations à effectuer"
                        }
                    },
                    "required": ["instructions"]
                }
            },
            {
                "name": "CALL_GAPP_AGENT",
                "description": "⭐ Délègue la création/modification de contenu Google Apps au GoogleAppsAgent. Utilisez pour créer des Google Docs, Sheets, Slides.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "document_type": {
                            "type": "string",
                            "enum": ["doc", "sheet", "slide"],
                            "description": "Type de document à créer ou modifier"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["create", "update", "format"],
                            "description": "Action à effectuer sur le document"
                        },
                        "instructions": {
                            "type": "string",
                            "description": "Instructions détaillées pour l'agent Google Apps"
                        },
                        "context": {
                            "type": "object",
                            "description": "Contexte et données supplémentaires (titre, contenu, données, emplacement, etc.)"
                        }
                    },
                    "required": ["document_type", "action", "instructions"]
                }
            },
            {
                "name": "ASK_USER",
                "description": "Pose une question directement à l'utilisateur pour obtenir des informations manquantes. Utilisez quand vous avez besoin de clarifications. Attend une réponse.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "La question à poser à l'utilisateur"
                        }
                    },
                    "required": ["question"]
                }
            },
            {
                "name": "TERMINATE_FILE_MANAGEMENT",
                "description": "🎯 Termine le processus de gestion de fichiers. Utilisez quand toutes les opérations demandées sont terminées. Fournissez un rapport complet.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "operation_status": {
                            "type": "string",
                            "enum": ["SUCCESS", "PARTIAL_SUCCESS", "FAILURE"],
                            "description": "Statut global de l'opération"
                        },
                        "files_processed": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Liste des IDs de fichiers traités"
                        },
                        "folders_created": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Liste des IDs de dossiers créés"
                        },
                        "documents_created": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Liste des documents Google Apps créés"
                        },
                        "errors_encountered": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Liste des erreurs rencontrées"
                        },
                        "conclusion": {
                            "type": "string",
                            "description": "Résumé textuel complet de toutes les opérations effectuées"
                        }
                    },
                    "required": ["operation_status", "conclusion"]
                }
            }
        ]
    
    def get_tool_mapping(self) -> Dict[str, Any]:
        """
        Retourne le mapping nom d'outil -> fonction.
        
        Note: Les outils de haut niveau (CALL_DRIVE_AGENT, CALL_GAPP_AGENT, 
        ASK_USER, TERMINATE_FILE_MANAGEMENT, get_departement_prompt, 
        vision_document, create_fiscal_year_structure) sont gérés directement 
        dans file_manager_agents.py par le FileManagerPinnokio.
        
        Returns:
            Dict: Mapping des outils DMS bas niveau
        """
        return {
            "search_file_in_dms": self.search_file_in_dms,
            "create_folder_in_dms": self.create_folder_in_dms,
            "move_file_in_dms": self.move_file_in_dms,
            "rename_file_in_dms": self.rename_file_in_dms,
            "delete_file_in_dms": self.delete_file_in_dms,
            "get_file_metadata": self.get_file_metadata,
        }
    
    def get_high_level_tools_only_schema(self) -> List[Dict[str, Any]]:
        """
        Retourne uniquement les outils de HAUT NIVEAU (pour Agent Principal).
        Exclut les outils DMS bas niveau qui doivent être délégués.
        
        Returns:
            List[Dict]: Liste des schémas d'outils de haut niveau
        """
        return [tool for tool in self.get_tools_schema() 
                if tool["name"] in ["CALL_DRIVE_AGENT", "CALL_GAPP_AGENT", "ASK_USER", 
                                     "TERMINATE_FILE_MANAGEMENT", "get_departement_prompt",
                                     "vision_document", "create_fiscal_year_structure"]]
    
    def get_dms_tools_only_schema(self) -> List[Dict[str, Any]]:
        """
        Retourne uniquement les outils DMS bas niveau (pour DriveAgent).
        
        Returns:
            List[Dict]: Liste des schémas d'outils DMS
        """
        return [tool for tool in self.get_tools_schema() 
                if tool["name"] not in ["CALL_DRIVE_AGENT", "CALL_GAPP_AGENT", "ASK_USER", 
                                         "TERMINATE_FILE_MANAGEMENT", "get_departement_prompt",
                                         "vision_document", "create_fiscal_year_structure"]]
    
    def get_high_level_tool_mapping(self, file_manager_instance) -> Dict[str, Any]:
        """
        Retourne le mapping des outils de HAUT NIVEAU pour l'agent principal.
        
        Args:
            file_manager_instance: Instance de FileManagerPinnokio
            
        Returns:
            Dict: Mapping nom d'outil -> fonction de FileManagerPinnokio
        """
        return {
            "get_departement_prompt": file_manager_instance.get_departement_prompt,
            "vision_document": file_manager_instance.vision_document,
            "create_fiscal_year_structure": file_manager_instance.create_fiscal_year_structure,
            # CALL_DRIVE_AGENT, CALL_GAPP_AGENT, ASK_USER et TERMINATE_FILE_MANAGEMENT
            # sont gérés directement dans le workflow
        }


