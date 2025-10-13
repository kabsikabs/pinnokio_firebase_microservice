from anthropic import Anthropic,AsyncAnthropic,BadRequestError
import re
from colorama import Fore, Style
import json
import ast
import asyncio
import io
from PIL import Image
import os
import requests
import pytz
import base64
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from openai import OpenAI
from ..driveClientService import DriveClientService
from ..firebase_providers import FirebaseManagement
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple, Union,Callable,AsyncGenerator
from ..tools.g_cred import get_secret

from abc import ABC, abstractmethod
import json
from datetime import datetime, timezone
from ..chroma_vector_service import ChromaVectorService
import base64
import logging

from google import genai
from google.genai import types

class ModelPricing:
    """Structure des prix pour les différents modèles"""
    
    PRICE_STRUCTURE = {
        # OpenAI Models
        "gpt-4o-2024-08-06": {
            "mode": "text",
            "input_price": 2.50,  # Prix par million de tokens
            "output_price": 10.00,
            "sales_multiplier": 2  # Facteur de multiplication pour le prix de vente
        },
        "gpt-4o-audio-preview-2024-10-01": {
            "mode": "audio",
            "input_price": 40.00,
            "output_price": 80.00,
            "sales_multiplier": 2
        },
        "gpt-4o-2024-05-13": {
            "mode": "audio",
            "input_price": 100.00,
            "output_price": 200.00,
            "sales_multiplier": 2
        },
        "gpt-4o-mini-2024-07-18": {
            "mode": "text",
            "input_price": 0.150,
            "output_price": 0.600,
            "sales_multiplier": 2
        },
        "gpt-4o-mini-audio-preview": {
            "mode": "audio",
            "input_price": 10.000,
            "output_price": 20.000,
            "sales_multiplier": 2
        },
        "o1": {
            "mode": "text",
            "input_price": 15.00,
            "output_price": 60.00,
            "sales_multiplier": 2
        },
        "o3-mini-2025-01-31": {
            "mode": "text",
            "input_price": 1.10,
            "output_price": 4.40,
            "sales_multiplier": 2
        },

        # Anthropic Models
        "claude-3-5-haiku-20241022": {
            "mode": "text",
            "input_price": 1.00,
            "output_price": 5.00,
            "sales_multiplier": 2
        },
        "claude-3-7-sonnet-20250219": {
            "mode": "text",
            "input_price": 3.00,
            "output_price": 15.00,
            "sales_multiplier": 2
        },
        "claude-3-opus-20240229": {
            "mode": "text",
            "input_price": 15.00,
            "output_price": 75.00,
            "sales_multiplier": 2
        },
        #Deepseek model
        "deepseek-chat": {
            "mode": "text",
            "input_price": 0.14,
            "output_price": 0.28,
            "sales_multiplier": 2
        },
        "deepseek-reasoner": {
            "mode": "text",
            "input_price": 0.32,
            "output_price": 0.64,
            "sales_multiplier": 2
        },
        #Gemini model
         "gemini-1.5-pro": {
            "mode": "text",
            "input_price": 2.5,
            "output_price": 10.00,
            "sales_multiplier": 2
        },
         "gemini-1.5-flash": {
            "mode": "text",
            "input_price": 0.15,
            "output_price": 0.60,
            "sales_multiplier": 2
        },
        "gemini-1.5-flash-8b": {
            "mode": "text",
            "input_price": 0.075,
            "output_price": 0.30,
            "sales_multiplier": 2
        },
        # Perplexity Models
        "sonar-deep-research": {
            "mode": "text",
            "input_price": 3,  # $0.2 per 1M tokens
            "output_price": 8,  # $0.2 per 1M tokens
            "sales_multiplier": 2
        },
        "sonar-reasoning-pro": {
            "mode": "text",
            "input_price": 2,  # $1.0 per 1M tokens
            "output_price": 8,  # $1.0 per 1M tokens
            "sales_multiplier": 2
        },
        "sonar-reasoning": {
            "mode": "text",
            "input_price": 1,  # $5.0 per 1M tokens
            "output_price": 5.0,  # $5.0 per 1M tokens
            "sales_multiplier": 2
        },
         "sonar-pro": {
            "mode": "text",
            "input_price": 3,  # $5.0 per 1M tokens
            "output_price": 15,  # $5.0 per 1M tokens
            "sales_multiplier": 2
        },
         "sonar": {
            "mode": "text",
            "input_price": 3,  # $5.0 per 1M tokens
            "output_price": 15,  # $5.0 per 1M tokens
            "sales_multiplier": 2
        },
        "r1-1776": {
            "mode": "text",
            "input_price": 2,  # $5.0 per 1M tokens
            "output_price": 8,  # $5.0 per 1M tokens
            "sales_multiplier": 2
        },


    }






    @staticmethod
    def calculate_token_cost(model: str, input_tokens: int, output_tokens: int) -> dict:
        """
        Calcule le coût des tokens pour un modèle donné
        
        Args:
            model: Nom du modèle
            input_tokens: Nombre de tokens en entrée
            output_tokens: Nombre de tokens en sortie
            
        Returns:
            dict: Dictionnaire contenant les coûts d'achat et de vente
        """
        if model not in ModelPricing.PRICE_STRUCTURE:
            return {
            "buy_price": "{:.6f}".format(0),
            "sales_price": "{:.6f}".format(0),
            "mode": "unknown"
        }
            
        pricing = ModelPricing.PRICE_STRUCTURE[model]
        
        # Calcul du prix d'achat
        input_cost = (input_tokens / 1_000_000) * pricing["input_price"]
        output_cost = (output_tokens / 1_000_000) * pricing["output_price"]
        total_buy_price = input_cost + output_cost
        
        # Calcul du prix de vente
        total_sales_price = total_buy_price * pricing["sales_multiplier"]
        
        return {
        "buy_price": "{:.6f}".format(total_buy_price),
        "sales_price": "{:.6f}".format(total_sales_price),
        "mode": pricing["mode"]
    }


class ModelSize(Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    REASONING_SMALL="reasoning_S"
    REASONING_MEDIUM="reasoning_M"
    REASONING_LARGE="reasoning_L"

class ModelProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI  = "gemini"
    MISTRAL = "mistral"
    LLAMA = "llama"
    DEEP_SEEK = "deep_seek"
    PERPLEXITY = "perplexity"  

class BaseAIAgent:
    """
    Agent de base pour l'IA avec support de différents systèmes de gestion documentaire (DMS).
    """
    SUPPORTED_DMS = {
        'google_drive': 'Google Drive',
        'one_drive': 'Microsoft OneDrive',
        'dropbox': 'Dropbox'
    }
    
    def __init__(self, 
                 collection_name: Optional[str] = None,
                 dms_system: Optional[str] = None,
                 dms_mode: Optional[str] = None,
                 firebase_user_id: Optional[str]=None,
                 chat_instance: Optional[Any] = None,
                 job_id: Optional[str]=None,
                 ) -> None:
        """
        Initialise l'agent avec support DMS optionnel.

        Args:
            collection_name (str, optional): Nom de la collection pour ChromaDB
            dms_system (str, optional): Système DMS à utiliser ('google_drive', 'one_drive', 'dropbox')
            dms_credentials (dict, optional): Credentials pour l'authentification DMS
            dms_config (dict, optional): Configuration supplémentaire pour le DMS
                Pour Google Drive: {'user_email': 'email@domain.com'}
                Pour OneDrive: {'tenant_id': 'tenant_id', 'client_id': 'client_id'}
                Pour Dropbox: {'app_key': 'key', 'app_secret': 'secret'}
        """
        self.chat_system = chat_instance
        self.default_provider = None
        self.default_model_size = None
        self.firebase_user_id=firebase_user_id
        # Initialisation des attributs de base
        self.token_usage = {
            "total_input_tokens": 0,
            "total_output_tokens": 0
        }
        self.chat_history = {}
        self.provider_models = {
            ModelProvider.ANTHROPIC: {
                ModelSize.SMALL: ["claude-3-5-haiku-20241022"],
                ModelSize.MEDIUM: ["claude-3-7-sonnet-20250219"],
                ModelSize.LARGE: ["claude-3-opus-latest"]
            },
            ModelProvider.OPENAI: {
                ModelSize.SMALL: ["gpt-4o-mini"],
                ModelSize.MEDIUM: ["gpt-4o"],
                ModelSize.LARGE: ["o1"],
                ModelSize.REASONING_SMALL: ["o3-mini-2025-01-31"],
                ModelSize.REASONING_LARGE: ["o1-2024-12-17"]
                
            },
            ModelProvider.GEMINI: {  # Ajout des modèles Gemini
                ModelSize.SMALL: ["gemini-1.5-flash-8b"],
                ModelSize.MEDIUM: ["gemini-2.0-flash-exp"],
                ModelSize.LARGE: ["gemini-1.5-pro"],
                ModelSize.REASONING_SMALL: ["gemini-2.0-flash-thinking-exp-1219"]
                
            },
            ModelProvider.DEEP_SEEK: {  # Ajout des modèles Gemini
                ModelSize.MEDIUM: ["deepseek-chat"],
                ModelSize.REASONING_MEDIUM: ["deepseek-reasoner"]
   
            },
            ModelProvider.PERPLEXITY: {
            ModelSize.SMALL: ["sonar"],
            ModelSize.MEDIUM: ["r1-1776"],
            ModelSize.LARGE: ["sonar-pro"],
            ModelSize.REASONING_LARGE:['sonar-deep-research'],
            ModelSize.REASONING_MEDIUM:['sonar-reasoning-pro'],
            ModelSize.REASONING_SMALL:['sonar-reasoning'],

            },
        }
        self.current_model = None
        self.system_prompt = None
        self.provider_instances = {}
        self.firebase_instance=FirebaseManagement()
        # Initialisation de ChromaDB (Singleton)
        self.chroma_db_instance = ChromaVectorService()

        # Initialisation du système DMS si spécifié
        self.dms_system = None
        
        if dms_system:
            self._initialize_dms(dms_mode,dms_system,firebase_user_id)
        
        self.chat_system = None
        self.job_id=job_id

    def update_job_id(self,job_id):
        self.job_id=job_id  
    
    def _initialize_dms(self,dms_mode:str, dms_system: str,firebase_user_id: str) -> None:
        """
        Initialise le système de gestion documentaire spécifié.

        Args:
            dms_system (str): Système DMS à initialiser
            credentials (dict): Credentials pour l'authentification
            config (dict): Configuration supplémentaire
        """
        if dms_system not in self.SUPPORTED_DMS:
            raise ValueError(f"Système DMS non supporté. Choix possibles: {', '.join(self.SUPPORTED_DMS.keys())}")

        try:
            if dms_system == 'google_drive':
                self.dms_name='google_drive'
                
                # TEMPORAIRE: Désactiver Google Drive pour éviter l'erreur de token
                print(f"Initialisation du système DMS Google Drive en mode {dms_mode} - DÉSACTIVÉ TEMPORAIREMENT")
                # self.dms_system = DriveClientService(mode=dms_mode,user_id=firebase_user_id)
                self.dms_system = None  # Désactiver temporairement

            elif dms_system == 'one_drive':
                pass
            elif dms_system == 'dropbox':
                pass
                
        except Exception as e:
            raise Exception(f"Erreur lors de l'initialisation du DMS {dms_system}: {str(e)}")


    def load_token_usage_to_db(self, project_id: str, job_id: str, workflow_step: str,file_name: str=None) -> None:
        """
        Charge les données d'utilisation des tokens dans Firebase.
        
        Args:
            project_id (str): Identifiant du projet
            job_id (str): Identifiant du job
            
        Le format des données sauvegardées sera:
        {
            project_id: str,
            job_id: str,
            provider_name: str,
            timestamp: datetime,
            total_input_tokens: int,
            total_output_tokens: int,
            provider_model_name: str,
            buy_price: str,
            sales_price: str,
            output_mode: str
        }
        """
        token_usage = self.get_token_usage_by_provider()
        
       
        
        # Créer un timestamp au format ISO avec timezone
        current_time = datetime.now(pytz.UTC).isoformat()
        
        success = True
        for provider_name, usage_data in token_usage.items():
            data = {
                'function':'router',
                'project_id': project_id,
                'job_id': job_id,
                
                'provider_name': provider_name,
                'timestamp': current_time,
                'workflow_step':workflow_step,
                'total_input_tokens': usage_data['total_input_tokens'],
                'total_output_tokens': usage_data['total_output_tokens'],
                'provider_model_name': usage_data['model'],
                'buy_price': usage_data['buy_price'],
                'sales_price': usage_data['sales_price'],
                'output_mode': usage_data['mode']
            }
            
            # Appel à la méthode de FireBaseManagement
            if not self.firebase_instance.upload_token_usage(self.firebase_user_id,data):
                success = False
                print(f"Échec du chargement des données pour le provider {provider_name}")
        
        return success


    
    def _transforme_image_for_provider(self,provider=ModelProvider,drive_files_ids=None,files_to_download=None,local_files=None,
                                       method='batch',text=None):
        
        def convert_pdf_to_images():
            pass

        def get_image_size(img):
            """Détermine la taille d'une image."""
            if isinstance(img, io.BytesIO):
                return len(img.getvalue())
            elif isinstance(img, bytes):
                return len(img)
            raise TypeError(f"Type d'image non supporté: {type(img)}")

        def resize_image_if_needed(image_data, max_size_bytes=4*1024*1024, max_dimension=8000):
                if isinstance(image_data, io.BytesIO):
                    initial_size = len(image_data.getvalue())
                    image_data.seek(0)
                else:
                    initial_size = len(image_data)
                
                img = Image.open(image_data)
                
                if initial_size <= max_size_bytes and img.width <= max_dimension and img.height <= max_dimension:
                    print(f"Image size and dimensions are already within limits: {initial_size/1024/1024:.2f} MB, {img.width}x{img.height}")
                    image_data.seek(0)
                    return image_data

                original_format = img.format
                quality = 95
                format = original_format if original_format in ['JPEG', 'PNG'] else 'PNG'

                # Redimensionner l'image si elle dépasse les dimensions maximales
                if img.width > max_dimension or img.height > max_dimension:
                    scale_factor = min(max_dimension / img.width, max_dimension / img.height)
                    new_width = int(img.width * scale_factor)
                    new_height = int(img.height * scale_factor)
                    img = img.resize((new_width, new_height), Image.LANCZOS)

                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format=format, quality=quality)

                while len(img_byte_arr.getvalue()) > max_size_bytes:
                    img_byte_arr.seek(0)
                    img_byte_arr.truncate(0)
                    if quality > 5:
                        quality -= 5
                    elif img.size[0] > 100:
                        width, height = img.size
                        img = img.resize((int(width * 0.9), int(height * 0.9)), Image.LANCZOS)
                    else:
                        format = 'JPEG'

                    img.save(img_byte_arr, format=format, quality=quality)

                img_byte_arr.seek(0)
                print(f"Image size after resizing: {len(img_byte_arr.getvalue())/1024/1024:.2f} MB, {img.width}x{img.height}")
                return img_byte_arr

        def process_image_if_needed(img, img_size):
            """
            Traite l'image si nécessaire (redimensionnement, compression).
            """
            if img_size > 5 * 1024 * 1024:  # 5MB
                return resize_image_if_needed(img)
            return img

        def is_acceptable_local_file(file_path):
            """
            Vérifie si le type de fichier est accepté.
            """
            acceptable_extensions = ['.pdf', '.jpeg', '.jpg', '.png', '.gif', '.webp']
            _, ext = os.path.splitext(file_path)
            return ext.lower() in acceptable_extensions
        
        def convert_local_file_to_images(file_path, conversion_index=0):
                from pdf2image import convert_from_path
                import mimetypes
                
                # Déterminer le format de sortie en fonction de conversion_index
                output_format = 'PNG' if conversion_index == 0 else 'JPEG'
                
                # Déterminer le type MIME du fichier
                mime_type, _ = mimetypes.guess_type(file_path)
                
                if mime_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                    print(f"le fichier est dans le format attendu pas besoin de transformation. {mime_type}")
                    with open(file_path, 'rb') as f:
                        return [io.BytesIO(f.read())]
                
                elif mime_type == 'application/pdf':
                    #print(f"le fichier est en format pdf et doit etre converti en images {output_format}. {mime_type}")
                    
                    # Conversion du PDF en images
                    pages = convert_from_path(file_path, fmt='png', dpi=200)
                    
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

        def create_image_batches(images, max_batch_size=4*1024*1024, max_image_size=4*1024*1024):
                batches = []
                current_batch = []
                current_batch_size = 0

                for img, img_size in images:
                    if img_size > max_image_size:
                        print(f"Image of size {img_size/1024/1024:.2f} MB exceeds maximum allowed size of {max_image_size/1024/1024:.2f} MB. Skipping this image.")
                        continue
                    if current_batch_size + img_size > max_batch_size:
                        batches.append(current_batch)
                        current_batch = []
                        current_batch_size = 0
                    current_batch.append((img, img_size))
                    current_batch_size += img_size

                if current_batch:
                    batches.append(current_batch)

                return batches

        def prepare_image_content(img_data, media_type, text):
            """
            Prépare le contenu pour l'API avec l'image et le texte.
            """

            
            if provider == ModelProvider.ANTHROPIC:
                image_content = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(img_data).decode("utf-8"),
                }
            }
                text_content = {
                    "type": "text",
                    "text": text
                }
                return [image_content, text_content]
            
            elif provider == ModelProvider.OPENAI:
                return [{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": text
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64.b64encode(img_data).decode('utf-8')}",
                                "detail": "high"  # Ou 'low' selon les besoins
                            }
                        }
                    ]
                }]
            
            elif provider == ModelProvider.GEMINI:
                # Gemini attend une simple structure avec inline_data
                image_part = genai.types.Part.from_bytes(
                data=img_data,
                mime_type=media_type
            )
                # Pour Gemini, on retourne une liste avec l'image suivie du texte
                return [image_part, text]
            else:
                raise ValueError(f"Provider {provider} non supporté pour le traitement d'images")

                
        # Traitement des images
        all_images = []

        # 1. Traitement des images depuis Google Drive
        if drive_files_ids:
            print(f"Traitement des images depuis Google Drive {drive_files_ids} impession de dms_system:{self.dms_system}")
            if self.dms_name=='google_drive':
                print(f"fichier drive a telecharger....")
                print(f"Tentative de téléchargement du fichier Drive: {drive_files_ids}")
                for file_id in drive_files_ids:
                    try:
                        print(f"Récupération des métadonnées pour le fichier {file_id}")
                        file_metadata = self.dms_system.drive_service.files().get(
                            fileId=file_id, 
                            fields='mimeType'
                        ).execute()
                        print(f"Type MIME du fichier: {file_metadata['mimeType']}")
                        if not self.dms_system.is_acceptable_file_type(file_metadata['mimeType']):
                            print(f"Type MIME non supporté: {file_metadata['mimeType']}")
                            continue
                        print("Conversion du fichier en PNG...")    
                        images = self.dms_system.convert_pdf_to_png(file_id, conversion_index=1)
                        print(f"Nombre d'images obtenues après conversion: {len(images)}")
                        for img in images:
                            img_size = get_image_size(img)
                            processed_img = process_image_if_needed(img, img_size)
                            all_images.append((processed_img, get_image_size(processed_img)))
                    except Exception as e:
                        print(f"Erreur lors du traitement du fichier Drive {file_id}: {e}")

        # 2. Traitement des fichiers à télécharger
        if files_to_download:
            for file_url in files_to_download:
                try:
                    response = requests.get(file_url)
                    images = convert_pdf_to_images(io.BytesIO(response.content))
                    for img in images:
                        img_size = get_image_size(img)
                        processed_img = process_image_if_needed(img, img_size)
                        all_images.append((processed_img, get_image_size(processed_img)))
                except Exception as e:
                    print(f"Erreur lors du téléchargement {file_url}: {e}")

        # 3. Traitement des fichiers locaux
        if local_files:
            for file_path in local_files:
                try:
                    if not is_acceptable_local_file(file_path):
                        print(f"Type de fichier non supporté: {file_path}")
                        continue
                        
                    images = convert_local_file_to_images(file_path, conversion_index=1)
                    for img in images:
                        img_size = get_image_size(img)
                        processed_img = process_image_if_needed(img, img_size)
                        all_images.append((processed_img, self.get_image_size(processed_img)))
                except Exception as e:
                    print(f"Erreur lors du traitement du fichier local {file_path}: {e}")

        # Traitement par lots ou individuel
        all_responses = {}
        if method == 'batch':
            print("Point de contrôle 1 : Entrée dans la méthode batch")
            print(f"Vérification des données : nombre d'images total : {len(all_images)}")
            image_batches = create_image_batches(all_images)
            print(f"Vérification des lots : {len(image_batches)} lots créés")
            if not image_batches:
                print("Attention : Aucun lot d'images n'a été créé")
                return []
            for batch_index, batch in enumerate(image_batches):
                try:
                    batch_content = []
                    for img, _ in batch:
                        image = Image.open(img)
                        media_type = f"image/{image.format.lower()}"
                        content = prepare_image_content(img.getvalue(), media_type, text)
                        batch_content.extend(content)

                    return batch_content
                    
                except Exception as e:
                    print(f"Erreur lors du traitement du lot {batch_index}: {e}")
                    all_responses[f"error_batch_{batch_index+1}"] = str(e)

        elif method == 'image':
            for image_index, (img, _) in enumerate(all_images):
                try:
                    image = Image.open(img)
                    media_type = f"image/{image.format.lower()}"
                    content = prepare_image_content(img.getvalue(), media_type, text)
                    
                    #response = process_image_data(content, text, tool_list, tool_choice)
                    return content
                except Exception as e:
                    print(f"Erreur lors du traitement de l'image {image_index}: {e}")
                    all_responses[f"error_image_{image_index+1}"] = str(e)

        else:
            raise ValueError("La méthode doit être 'batch' ou 'image'")

        return all_responses

    def update_system_prompt(self, system_prompt: str):
        """
        Met à jour le prompt système pour tous les providers enregistrés dans cette instance.
        
        Args:
            system_prompt (str): Le nouveau prompt système à utiliser
        """
        self.system_prompt = system_prompt
        
        # Met à jour uniquement les providers qui sont enregistrés dans cette instance
        for provider_type, instance in self.provider_instances.items():
            try:
                if hasattr(instance, 'update_system_prompt'):
                    #print(f"Mise à jour du prompt système pour le provider {provider_type.value}")
                    #print(f"BaseAIAgent: Mise à jour du prompt système: {system_prompt}")
                    instance.update_system_prompt(system_prompt)
            except Exception as e:
                print(f"Erreur lors de la mise à jour du prompt système pour {provider_type.value}: {str(e)}")

    def _transform_tool_mapping(self, tool_mapping: Optional[Dict[str, Any]], provider: ModelProvider) -> Dict[str, Any]:
        """
        Transforme le mapping des outils selon le provider.
        Maintient un format standard mais permet des adaptations spécifiques aux providers.
        
        Args:
            tool_mapping: Le mapping original des outils vers leurs fonctions
            provider: Le provider pour lequel transformer le mapping
            
        Returns:
            Dict: Le mapping transformé selon le provider
        """
        if not tool_mapping:
            return {}

        # Convertir en liste de dictionnaires si ce n'est pas déjà le cas
        if isinstance(tool_mapping, dict):
            tool_mapping = [tool_mapping]

        standardized_mapping = {}
        
        for mapping_dict in tool_mapping:
            for tool_name, function in mapping_dict.items():
                if function is None:
                    # Cas où la fonction n'est pas définie
                    standardized_mapping[tool_name] = None
                    continue

                if not callable(function):
                    print(f"Avertissement: {tool_name} n'est pas mappé à une fonction callable")
                    continue

                # Créer une wrapper function si nécessaire selon le provider
                if provider == ModelProvider.ANTHROPIC:
                    # Anthropic accepte directement les fonctions
                    standardized_mapping[tool_name] = function

                elif provider == ModelProvider.OPENAI:
                    # OpenAI utilise le même format mais pourrait nécessiter des adaptations futures
                    standardized_mapping[tool_name] = function
                
                elif provider == ModelProvider.GEMINI:
                    # Gemini accepte à la fois les fonctions callables et None
                    # Pour Gemini, on maintient la structure originale
                    standardized_mapping[tool_name] = function

                elif provider == ModelProvider.DEEP_SEEK:
                    # OpenAI utilise le même format mais pourrait nécessiter des adaptations futures
                    standardized_mapping[tool_name] = function
                

                else:
                    # Pour les futurs providers, on peut ajouter des wrappers spécifiques
                    standardized_mapping[tool_name] = self._create_provider_specific_wrapper(
                        function, 
                        tool_name, 
                        provider
                    )

        return standardized_mapping

    def _create_gemini_function_wrapper(self, function: callable, tool_name: str) -> callable:
        """Crée un wrapper spécifique pour les fonctions Gemini."""
        def wrapper(*args, **kwargs):
            try:
                # Exécution de la fonction
                result = function(*args, **kwargs)
                return {
                    "result": result,
                    "tool_name": tool_name,
                    "provider": "gemini"
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "tool_name": tool_name,
                    "provider": "gemini"
                }
        return wrapper

    def _create_provider_specific_wrapper(
        self, 
        function: Callable, 
        tool_name: str, 
        provider: ModelProvider
    ) -> Callable:
        """
        Crée un wrapper spécifique au provider pour une fonction donnée.
        Permet d'adapter le comportement des fonctions selon les besoins du provider.
        
        Args:
            function: La fonction originale
            tool_name: Le nom de l'outil
            provider: Le provider concerné
            
        Returns:
            Callable: La fonction adaptée au provider
        """
        def generic_wrapper(*args, **kwargs):
            # Log de l'appel pour le débogage
            print(f"Appel de l'outil {tool_name} avec provider {provider}")
            print(f"Arguments: {args}")
            print(f"Keywords: {kwargs}")
            
            try:
                # Exécution de la fonction
                result = function(*args, **kwargs)
                
                # Post-traitement spécifique au provider si nécessaire
                if provider == ModelProvider.ANTHROPIC:
                    return result
                elif provider == ModelProvider.OPENAI:
                    return result
                else:
                    # Format par défaut pour les nouveaux providers
                    return {
                        "result": result,
                        "tool_name": tool_name,
                        "provider": provider.value
                    }
            
            except Exception as e:
                print(f"Erreur lors de l'exécution de {tool_name}: {str(e)}")
                return {
                    "error": str(e),
                    "tool_name": tool_name,
                    "provider": provider.value
                }
        
        return generic_wrapper

    def _transform_tools_for_provider(self, tools: List[Dict[str, Any]], provider: ModelProvider) -> List[Dict[str, Any]]:
        """
        Transforme la structure des outils en fonction du provider.
        
        Args:
            tools: Liste des outils au format Anthropic
            provider: Le provider pour lequel transformer les outils
            
        Returns:
            Liste des outils transformés selon le format attendu par le provider
        """
        if provider == ModelProvider.ANTHROPIC:
            return tools  # Pas de transformation nécessaire
        
        elif provider == ModelProvider.OPENAI:  
            transformed_tools = []
            for tool in tools:
                transformed_tool = {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description"),
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False
                        }
                    }
                }
                
                # Récupération des propriétés depuis input_schema
                if "input_schema" in tool:
                    input_schema = tool["input_schema"]
                    if "properties" in input_schema:
                        transformed_tool["function"]["parameters"]["properties"] = \
                            input_schema["properties"]
                    
                    # Gérer les champs requis
                    if "required" in input_schema:
                        transformed_tool["function"]["parameters"]["required"] = \
                            input_schema["required"]
                
                transformed_tools.append(transformed_tool)
            
            return transformed_tools
        
        elif provider == ModelProvider.GEMINI:
            def transform_properties(properties):
                """Fonction récursive pour transformer les propriétés"""
                transformed = {}
                for prop_name, prop_details in properties.items():
                    # Créer une copie pour ne pas modifier l'original
                    transformed_prop = prop_details.copy()
                    
                    # Convertir le type en majuscules
                    if 'type' in transformed_prop:
                        transformed_prop['type'] = transformed_prop['type'].upper()
                    
                    # Gérer les propriétés imbriquées pour les objets
                    if transformed_prop.get('type') == 'OBJECT' and 'properties' in transformed_prop:
                        transformed_prop['properties'] = transform_properties(transformed_prop['properties'])
                    
                    transformed[prop_name] = transformed_prop
                return transformed
            
            transformed_tools = []
            for tool in tools:
                # Conversion du format standard vers le format Gemini
                transformed_tool = {
                    'name': tool.get('name'),
                    'description': tool.get('description', ''),
                    'parameters': {
                        'type': 'OBJECT',  # Gemini utilise des types en majuscules
                        'properties': {},
                        'required': []
                    }
                }
                
                # Conversion des propriétés
                if "input_schema" in tool:
                    input_schema = tool["input_schema"]
                    if "properties" in input_schema:
                        transformed_tool['parameters']['properties'] = transform_properties(input_schema["properties"])
                    if "required" in input_schema:
                        transformed_tool['parameters']['required'] = input_schema["required"]
                
                transformed_tools.append(transformed_tool)  # Désindenté
            return transformed_tools  # Désindenté

        
        elif provider == ModelProvider.DEEP_SEEK:  
            transformed_tools = []
            for tool in tools:
                transformed_tool = {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description"),
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False
                        }
                    }
                }
                
                # Récupération des propriétés depuis input_schema
                if "input_schema" in tool:
                    input_schema = tool["input_schema"]
                    if "properties" in input_schema:
                        transformed_tool["function"]["parameters"]["properties"] = \
                            input_schema["properties"]
                    
                    # Gérer les champs requis
                    if "required" in input_schema:
                        transformed_tool["function"]["parameters"]["required"] = \
                            input_schema["required"]
                
                transformed_tools.append(transformed_tool)
            
            return transformed_tools

        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _transform_tool_choice(self, tool_choice: Optional[Dict[str, Any]], provider: ModelProvider, tools_provided: bool = False) -> Union[Dict[str, Any], str, None]:
        """
        Transforme la configuration tool_choice selon le provider.
        
        Args:
            tool_choice: Configuration tool_choice au format Anthropic
            provider: Le provider cible
            tools_provided: Indique si des outils sont fournis
            
        Returns:
            Configuration tool_choice adaptée au format du provider
        """
        # Si pas d'outils fournis, retourner None quel que soit le provider
        if not tools_provided:
            return None

        if tool_choice is None:
            if provider == ModelProvider.ANTHROPIC:
                return {"type": "auto"}
            elif provider == ModelProvider.GEMINI:
                return {'mode': 'NONE'}
            

        if provider == ModelProvider.ANTHROPIC:
            # Pour Anthropic, s'assurer que le format est correct
            if not isinstance(tool_choice, dict):
                return {"type": "auto"}
            return tool_choice

        elif provider == ModelProvider.OPENAI:
            if not isinstance(tool_choice, dict):
                return "auto"

            tool_choice_type = tool_choice.get("type", "auto")

            # Mappings pour OpenAI
            openai_mappings = {
                "auto": "auto",
                "any": "required",
                "none": "none"
            }

            # Cas spécial pour les outils nommés
            if tool_choice_type == "tool" and "name" in tool_choice:
                return {
                    "type": "function",
                    "function": {
                        "name": tool_choice["name"]
                    }
                }

            # Utiliser le mapping ou retourner "auto" par défaut
            return openai_mappings.get(tool_choice_type, "auto")

        
        elif provider == ModelProvider.GEMINI:
            # Mappings pour Gemini
            if isinstance(tool_choice, dict):
                choice_type = tool_choice.get("type", "auto")
                
                
                # Mappings spécifiques à Gemini
                gemini_mappings = {
                    "auto": "AUTO",
                    "none": "NONE",
                    "any": "ANY",
                    "tool": "ANY"  # Si un outil spécifique est demandé
                }
                
                # Cas spécial pour la sélection d'un outil spécifique
                if choice_type == "tool" and "name" in tool_choice:
                    return {
                        "mode": "ANY",
                        "allowed_function_names": [tool_choice["name"]]
                    }
                
                return {'mode': gemini_mappings.get(choice_type, "AUTO")}
            
            return {'mode': 'AUTO'}

        elif provider == ModelProvider.DEEP_SEEK:
            if not isinstance(tool_choice, dict):
                return "auto"

            tool_choice_type = tool_choice.get("type", "auto")

            # Mappings pour OpenAI
            openai_mappings = {
                "auto": "auto",
                "any": "required",
                "none": "none"
            }

            # Cas spécial pour les outils nommés
            if tool_choice_type == "tool" and "name" in tool_choice:
                return {
                    "type": "function",
                    "function": {
                        "name": tool_choice["name"]
                    }
                }

            # Utiliser le mapping ou retourner "auto" par défaut
            return openai_mappings.get(tool_choice_type, "auto")

        

        else:
            raise ValueError(f"Provider non supporté: {provider}")

    def register_provider(self, provider: ModelProvider, instance: Any, default_model_size: Optional[ModelSize] = None):
        """
        Enregistre une instance de provider pour une utilisation ultérieure et définit optionnellement
        une taille de modèle par défaut.

        Args:
            provider (ModelProvider): Le provider à enregistrer
            instance (Any): L'instance du provider
            default_model_size (ModelSize, optional): Taille de modèle par défaut pour ce provider
        
        Raises:
            ValueError: Si la taille de modèle spécifiée n'est pas disponible pour ce provider
        """
        if default_model_size is not None:
            if provider not in self.provider_models:
                raise ValueError(f"Provider {provider} non reconnu")
            
            if default_model_size not in self.provider_models[provider]:
                available_sizes = list(self.provider_models[provider].keys())
                raise ValueError(
                    f"Taille de modèle {default_model_size} non disponible pour {provider}. "
                    f"Tailles disponibles: {available_sizes}"
                )
            
            # Stocker la taille de modèle par défaut dans un dictionnaire
            if not hasattr(self, 'provider_default_sizes'):
                self.provider_default_sizes = {}
            self.provider_default_sizes[provider] = default_model_size

        self.provider_instances[provider] = instance
        if self.default_provider is None:
            self.default_provider = provider  # Set the default provider when registering
    
    def register_size_model(self, default_model_size: ModelSize) -> None:
        """
        Met à jour la taille par défaut du modèle pour le provider par défaut.
        
        Cette méthode permet de modifier uniquement la taille par défaut du modèle
        en utilisant le provider par défaut déjà configuré.
        
        Args:
            default_model_size (ModelSize): La nouvelle taille par défaut à utiliser
        
        Raises:
            ValueError: Levée dans les cas suivants:
                - Aucun provider par défaut n'a été configuré
                - La taille de modèle spécifiée n'est pas disponible pour ce provider
        """
        if self.default_provider is None:
            raise ValueError("Aucun provider par défaut n'a été configuré. Utilisez register_provider d'abord.")
            
        provider = self.default_provider
        
        if provider not in self.provider_models:
            raise ValueError(f"Provider {provider} non reconnu")
        
        if default_model_size not in self.provider_models[provider]:
            available_sizes = list(self.provider_models[provider].keys())
            raise ValueError(
                f"Taille de modèle {default_model_size} non disponible pour {provider}. "
                f"Tailles disponibles: {available_sizes}"
            )
        
        if not hasattr(self, 'provider_default_sizes'):
            self.provider_default_sizes = {}
        self.provider_default_sizes[provider] = default_model_size


    def get_default_model_size(self, provider: Optional[ModelProvider] = None) -> Optional[ModelSize]:
        """
        Récupère la taille de modèle par défaut pour un provider donné.

        Args:
            provider (ModelProvider, optional): Le provider pour lequel récupérer la taille par défaut.
                                             Si non spécifié, utilise le provider par défaut.

        Returns:
            Optional[ModelSize]: La taille de modèle par défaut ou None si non définie

        Raises:
            ValueError: Si aucun provider n'est spécifié et qu'il n'y a pas de provider par défaut
        """
        if provider is None:
            provider = self.default_provider
            if provider is None:
                raise ValueError("Aucun provider spécifié et pas de provider par défaut défini")

        return getattr(self, 'provider_default_sizes', {}).get(provider)


    def get_provider_instance(self, provider: ModelProvider) -> Any:
        """
        Récupère l'instance du provider.
        """
        if provider is None:
            provider = self.default_provider
        if provider is None:
            raise ValueError("No provider specified and no default provider set.")
        instance = self.provider_instances.get(provider)
        if instance is None:
            raise ValueError(f"No instance registered for provider {provider}")
        return instance

    def get_model_by_size_and_provider(self, provider: ModelProvider, size: ModelSize) -> str:
        """
        Retourne le nom du modèle approprié basé sur le provider et la taille.
        """
        try:
            models = self.provider_models[provider][size]
            return models[0]  # Retourne le premier modèle disponible
        except KeyError:
            raise ValueError(f"No model available for provider {provider} and size {size}")

    

    def process_text(self, 
                content: str,
                size: Optional[ModelSize] = None,
                stream: bool = False,
                provider: Optional[ModelProvider] = None,
                max_tokens: int = 1024) -> Dict[str, Any]:
        """
        Point d'entrée unifié pour le traitement de texte.
        
        Args:
            content (str): Le texte à traiter
            size (Optional[ModelSize]): La taille du modèle à utiliser. Si non spécifiée,
                                    utilise la taille par défaut du provider
            stream (bool): Indique si le traitement doit être effectué en streaming
            provider (Optional[ModelProvider]): Le provider à utiliser. Si non spécifié,
                                            utilise le provider par défaut
            max_tokens (int): Nombre maximum de tokens pour la réponse
            
        Returns:
            Dict[str, Any]: Résultat du traitement de texte
            
        Raises:
            ValueError: Si aucun provider n'est spécifié et qu'il n'y a pas de provider par défaut,
                    ou si aucune taille de modèle n'est disponible
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider
        
        # Détermination de la taille du modèle
        if size is None:
            size = self.get_default_model_size(provider)
            if size is None:
                # Si aucune taille par défaut n'est définie, utiliser la première taille disponible
                available_sizes = list(self.provider_models[provider].keys())
                if not available_sizes:
                    raise ValueError(f"No model sizes available for provider {provider}")
                size = available_sizes[0]
                print(f"No default model size set for {provider}, using {size}")
        
        model = self.get_model_by_size_and_provider(provider, size)
        provider_instance = self.get_provider_instance(provider)
        #print(f"impression du content avant:{content}")
        if provider == ModelProvider.ANTHROPIC:
            # Utilise la méthode anthropic_send_message du provider Anthropic
            response = provider_instance.anthropic_send_message(
                content=content,
                model_name=self._get_model_index(model),
                streaming=stream,
                max_tokens=max_tokens
            )
            #print(f"impression de raw_data:{response}")
            data = provider_instance.final_handle_responses(response)
            #print(f"impression de data:{data}")
            return data 
        # Ajouter d'autres providers ici avec leur logique spécifique
        
        elif provider == ModelProvider.OPENAI:
            data = provider_instance.openai_send_message(
                content=content,
                model_name=self._get_model_index(model)
            )
            #print(f"impression de response d'OpenAI")
            # Formatage de la réponse OpenAI pour correspondre au format unifié
            return data
        
        elif provider == ModelProvider.GEMINI:
            response = provider_instance.process_text(
                content=content,
                model_name=self._get_model_index(model),
                stream=stream,
                max_tokens=max_tokens
            )
            return response
        
        elif provider == ModelProvider.DEEP_SEEK:
            data = provider_instance.deepseek_send_message(
                content=content,
                model_name=self._get_model_index(model),
                stream=stream,
                max_tokens=max_tokens
            )
            #print(f"impression de response d'OpenAI")
            # Formatage de la réponse OpenAI pour correspondre au format unifié
            return data

        
                

        else:
            raise ValueError(f"Provider {provider} not implemented")

    async def process_text_tool_streaming(self,
                                           content: str,
                                           tools: List[Dict[str, Any]],
                                           tool_mapping: List[Dict[str, Any]],
                                           size: Optional[ModelSize] = None,
                                           provider: Optional[ModelProvider] = None,
                                           tool_choice: Optional[Dict[str, Any]] = None,
                                           max_tokens: int = 1024) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Point d'entrée unifié pour le traitement de texte en streaming avec support d'outils.
        
        Args:
            content (str): Le texte à traiter
            tools (List[Dict[str, Any]]): Liste des outils disponibles
            tool_mapping (List[Dict[str, Any]]): Mapping des outils vers leurs fonctions
            size (Optional[ModelSize]): La taille du modèle à utiliser
            provider (Optional[ModelProvider]): Le provider à utiliser
            tool_choice (Optional[Dict[str, Any]]): Configuration du choix d'outil
            max_tokens (int): Nombre maximum de tokens pour la réponse
            
        Yields:
            Dict[str, Any]: Chunks de réponse en streaming au format uniforme:
                {
                    "type": "text" | "tool_use" | "tool_result" | "final" | "status" | "error",
                    "content": str,           # Pour le texte
                    "tool_name": str,         # Pour l'utilisation d'outil
                    "tool_input": dict,       # Arguments de l'outil
                    "tool_output": any,       # Résultat de l'outil
                    "tool_results": list,     # Liste des résultats (dans final)
                    "is_final": bool,
                    "model": str
                }
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider
        
        # Détermination de la taille du modèle
        if size is None:
            size = self.get_default_model_size(provider)
            if size is None:
                available_sizes = list(self.provider_models[provider].keys())
                if not available_sizes:
                    raise ValueError(f"No model sizes available for provider {provider}")
                size = available_sizes[0]
                print(f"No default model size set for {provider}, using {size}")
        
        model = self.get_model_by_size_and_provider(provider, size)
        
        # Obtenir l'instance du provider
        provider_instance = self.get_provider_instance(provider)
        
        # Transformer les tools et tool_choice selon le provider (uniformisation)
        transformed_tools = self._transform_tools_for_provider(tools, provider)
        transformed_tool_choice = self._transform_tool_choice(tool_choice, provider, bool(tools))
        transformed_tool_mapping = self._transform_tool_mapping(tool_mapping, provider)
        
        print(f"🔵 Tool choice transformé: {transformed_tool_choice}")
        print(f"🔵 Nombre d'outils transformés: {len(transformed_tools) if transformed_tools else 0}")
        
        # Appeler la méthode streaming avec tools du provider spécifique
        if provider == ModelProvider.ANTHROPIC:
            async for chunk in provider_instance.anthropic_send_message_tool_streaming(
                content=content,
                model_name=model,
                tools=transformed_tools,
                tool_mapping=transformed_tool_mapping,
                tool_choice=transformed_tool_choice,
                max_tokens=max_tokens
            ):
                yield chunk
                
        elif provider == ModelProvider.OPENAI:
            async for chunk in provider_instance.openai_send_message_tool_streaming(
                content=content,
                model_name=model,
                tools=transformed_tools,
                tool_mapping=transformed_tool_mapping,
                tool_choice=transformed_tool_choice,
                max_tokens=max_tokens
            ):
                yield chunk
            
        elif provider == ModelProvider.GEMINI:
            # TODO: Implémenter le streaming Gemini avec tools
            yield {
                "type": "error",
                "content": "Streaming avec tools Gemini non implémenté",
                "is_final": True
            }
            
        elif provider == ModelProvider.DEEP_SEEK:
            # TODO: Implémenter le streaming DeepSeek avec tools
            yield {
                "type": "error",
                "content": "Streaming avec tools DeepSeek non implémenté",
                "is_final": True
            }
            
        else:
            raise ValueError(f"Streaming with tools not implemented for provider {provider}")

    async def process_text_streaming(self, 
                content: str,
                size: Optional[ModelSize] = None,
                provider: Optional[ModelProvider] = None,
                max_tokens: int = 1024) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Point d'entrée unifié pour le traitement de texte en streaming.
        
        Args:
            content (str): Le texte à traiter
            size (Optional[ModelSize]): La taille du modèle à utiliser
            provider (Optional[ModelProvider]): Le provider à utiliser
            max_tokens (int): Nombre maximum de tokens pour la réponse
            
        Yields:
            Dict[str, Any]: Chunks de réponse en streaming
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider
        
        # Détermination de la taille du modèle (même logique que process_text)
        if size is None:
            size = self.get_default_model_size(provider)
            if size is None:
                # Si aucune taille par défaut n'est définie, utiliser la première taille disponible
                available_sizes = list(self.provider_models[provider].keys())
                if not available_sizes:
                    raise ValueError(f"No model sizes available for provider {provider}")
                size = available_sizes[0]
                print(f"No default model size set for {provider}, using {size}")
        
        model = self.get_model_by_size_and_provider(provider, size)
        
        # Obtenir l'instance du provider
        provider_instance = self.get_provider_instance(provider)
        
        # Appeler la méthode streaming du provider spécifique
        if provider == ModelProvider.ANTHROPIC:
            # Utiliser la méthode streaming source d'Anthropic
            async for chunk in provider_instance.anthropic_send_message_streaming(
                content=content,
                model_name=model,  # Utiliser le nom du modèle résolu
                max_tokens=max_tokens
            ):
                yield chunk
                
        elif provider == ModelProvider.OPENAI:
            # Utiliser la méthode streaming d'OpenAI
            async for chunk in provider_instance.openai_send_message_streaming(
                content=content,
                model_name=model,  # Utiliser le nom du modèle résolu
                max_tokens=max_tokens
            ):
                yield chunk
            
        elif provider == ModelProvider.GEMINI:
            # TODO: Implémenter le streaming Gemini
            yield {"content": "Streaming Gemini non implémenté", "is_final": True}
            
        elif provider == ModelProvider.DEEP_SEEK:
            # TODO: Implémenter le streaming DeepSeek
            yield {"content": "Streaming DeepSeek non implémenté", "is_final": True}
            
        else:
            raise ValueError(f"Streaming not implemented for provider {provider}")

    def process_search(self, 
                    content: str,
                    size: Optional[ModelSize] = None,
                    stream: bool = False,
                    provider: Optional[ModelProvider] = None,
                    max_tokens: int = 1024,
                    domain_filter: Optional[List[str]] = None,
                    temperature: float = 0.2,
                    top_p: float = 0.9,
                    top_k: int = 0) -> Tuple[str, List[str]]:
        """
        Point d'entrée pour les recherches avec Perplexity.
        
        Args:
            content (str): La requête de recherche
            size (ModelSize): La taille du modèle à utiliser
            stream (bool): Activer le streaming de la réponse
            provider (Optional[ModelProvider]): Le provider à utiliser (défaut: Perplexity)
            max_tokens (int): Nombre maximum de tokens pour la réponse
            domain_filter (Optional[List[str]]): Liste des domaines pour filtrer les sources (max 3)
            temperature (float): Température pour la génération (0.0-2.0)
            top_p (float): Paramètre top_p pour le sampling (0.0-1.0)
            top_k (int): Paramètre top_k pour le filtering (0-2048)
            
        Returns:
            Tuple[str, List[str]]: (contenu de la réponse, liste des citations)
            
        Raises:
            ValueError: Si le provider n'est pas Perplexity ou si les paramètres sont invalides
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider

         # Détermination de la taille du modèle
        if size is None:
            size = self.get_default_model_size(provider)
            if size is None:
                # Si aucune taille par défaut n'est définie, utiliser la première taille disponible
                available_sizes = list(self.provider_models[provider].keys())
                if not available_sizes:
                    raise ValueError(f"No model sizes available for provider {provider}")
                size = available_sizes[0]
                print(f"No default model size set for {provider}, using {size}")

        if provider != ModelProvider.PERPLEXITY:
            raise ValueError("process_search ne supporte que le provider Perplexity")

        model = self.get_model_by_size_and_provider(provider, size)
        provider_instance = self.get_provider_instance(provider)

        # Vérification des paramètres
        if domain_filter and len(domain_filter) > 3:
            raise ValueError("Maximum 3 domaines autorisés dans domain_filter")
        if not (0 <= temperature < 2):
            raise ValueError("Temperature doit être entre 0 et 2")
        if not (0 <= top_p <= 1):
            raise ValueError("top_p doit être entre 0 et 1")
        if not (0 <= top_k <= 2048):
            raise ValueError("top_k doit être entre 0 et 2048")

        response = provider_instance.search(
            query=content,
            model_name=self._get_model_index(model),
            domain_filter=domain_filter,
            max_tokens=max_tokens)

        # Si la réponse est un tuple (contenu, citations), la retourner directement
        if isinstance(response, tuple) and len(response) == 2:
            return response
        
        # Sinon, extraire le contenu et les citations de la réponse brute
        content = response['choices'][0]['message']['content'] if response and 'choices' in response else None
        citations = response.get('citations', []) if response else []
        
        return content, citations

    def process_vision(self,
                    text: str,
                    size: Optional[ModelSize] = None,
                    method: str = 'batch',
                    stream: bool = False,
                    max_tokens: int = 1024,
                    tool_list: Optional[List[Dict[str, Any]]] = None,
                    provider: Optional[ModelProvider] = None,
                    tool_mapping: Optional[Dict[str, Any]] = None,
                    tool_choice: Optional[Dict[str, Any]] = None,
                    file_ids: Optional[List[str]] = None,
                    local_files: Optional[List[str]] = None,
                    files_to_download: Optional[List[str]] = None,
                    final_resume: bool = True) -> Dict[str, Any]:
        """
        Point d'entrée unifié pour le traitement de vision.
        
        Args:
            content (str): Le texte de la requête
            provider (ModelProvider): Le fournisseur d'IA à utiliser
            size (ModelSize): La taille du modèle à utiliser
            image_data (Any, optional): Les données d'image à analyser (fichiers locaux)
            method (str, optional): Méthode de traitement ('batch' ou 'image'). Par défaut 'batch'
            stream (bool, optional): Utiliser le streaming. Par défaut False
            max_tokens (int, optional): Nombre maximum de tokens. Par défaut 1024
            tool_list (List[Dict], optional): Liste des outils disponibles
            tool_mapping (Dict, optional): Mapping des outils vers leurs fonctions
            tool_choice (Dict, optional): Configuration du choix d'outil
            file_ids (List[str], optional): Liste des IDs de fichiers dans le DMS
            files_to_download (List[str], optional): Liste des URLs de fichiers à télécharger
            final_resume (bool, optional): Générer un résumé final. Par défaut True
        
        Returns:
            Dict[str, Any]: Résultat de l'analyse de vision
            
        Raises:
            ValueError: Si aucune source d'image n'est fournie (image_data, file_ids ou files_to_download)
        """
        if not any([local_files, file_ids, files_to_download]):
            raise ValueError("Au moins une source d'image doit être fournie (image_data, file_ids ou files_to_download)")

        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider

        # Détermination de la taille du modèle
        if size is None:
            size = self.get_default_model_size(provider)
            if size is None:
                # Si aucune taille par défaut n'est définie, utiliser la première taille disponible
                available_sizes = list(self.provider_models[provider].keys())
                if not available_sizes:
                    raise ValueError(f"No model sizes available for provider {provider}")
                size = available_sizes[0]
                print(f"No default model size set for {provider}, using {size}")

        model = self.get_model_by_size_and_provider(provider, size)
        provider_instance = self.get_provider_instance(provider)

        # Transformation des outils selon le provider si nécessaire
        transformed_tools = None
        transformed_tool_choice = None
        transformed_tool_mapping = None
        if tool_list is not None and tool_mapping is not None:
            transformed_tools = self._transform_tools_for_provider(tool_list, provider)
            transformed_tool_mapping = self._transform_tool_mapping(tool_mapping, provider)
            if tool_choice is not None:
                transformed_tool_choice = self._transform_tool_choice(tool_choice, provider)

        # Vérification de la disponibilité du DMS si des file_ids sont fournis
        if not self.dms_system:
            raise ValueError("DMS non configuré alors que des file_ids sont fournis")

        
            

        content=self._transforme_image_for_provider(provider=provider,drive_files_ids=file_ids,files_to_download=files_to_download,
                                                local_files=local_files,text=text,method=method)
        
        if provider == ModelProvider.ANTHROPIC:
            
            response = provider_instance.antho_agent(
                content=content,
                model_name=self._get_model_index(model),
                antho_tools=transformed_tools,
                tool_mapping=transformed_tool_mapping,
                tool_choice=transformed_tool_choice,
                stream=stream,
                max_tokens=max_tokens
            )

            #print(f"impression de la response de vision avant le resumé général:{response}")
            if final_resume:
                final_resume_prompt=f"""Un document a été traité de plusieurs pages dont voici les réponses découpés par page.\n\n{response}\n\n Conformément à la question initial:{text}
                Merci d'apporter une synthese des réponses apporté"""
                response = provider_instance.anthropic_send_message(
                content=final_resume_prompt,
                model_name=self._get_model_index(model),
                streaming=stream,
                max_tokens=max_tokens
            )
                #print(f"impression de raw_data:{response}")
                data = provider_instance.final_handle_responses(response)
                #print(f"impression de data:{data}")
                
                return data

            return response

            
        if provider == ModelProvider.OPENAI:
            # Appeler la méthode openai_send_message
            if transformed_tools:
                response = provider_instance.openai_send_message_tool(
                    content=content,
                    model_name=self._get_model_index(model),
                    tool_list=transformed_tools,
                    tool_name=tool_mapping.keys(),
                    tool_choice=transformed_tool_choice
                )
                print(f"impression de response avant la transofmration avec outil:{response}")
            else:
                response = provider_instance.openai_send_message(
                    content=content,
                    model_name=self._get_model_index(model)
                )
                print(f"impression de response avant la transofmration:{response}")
            # Si un résumé final est demandé
            if final_resume and response:
                resume_prompt = f"""Un document a été traité avec plusieurs pages. 
                Voici les réponses par page: {response}
                
                Question initiale: {text}
                
                Merci de faire une synthèse des réponses."""
                
                final_summary = provider_instance.openai_send_message(
                    content=resume_prompt,
                    model_name=self._get_model_index(model)
                )
                print(f"impression de response avant la transofmration:{final_summary}")
                return final_summary
        
        if provider == ModelProvider.GEMINI:
            
            if transformed_tools:
                response = provider_instance.process_vision(
                    text=content,
                    model_name=self._get_model_index(model),
                    tool_list=transformed_tools,
                    tool_mapping=transformed_tool_mapping,
                    tool_choice=transformed_tool_choice,
                    max_tokens=max_tokens
                )
            else:
                response = provider_instance.process_vision(
                    text=content,
                    model_name=self._get_model_index(model),
                    max_tokens=max_tokens
                )
                print(f"impression de reponse de vision pour Gemini:{response}")
            if final_resume and response:
                
                resume_prompt = f"""Un document a été traité avec plusieurs pages. 
                Voici les réponses par page: {response}
                
                Question initiale: {text}
                
                Merci de faire une synthèse des réponses."""
                
                final_response = provider_instance.process_text(
                    content=resume_prompt,
                    model_name=self._get_model_index(model),
                    max_tokens=max_tokens
                )
                return final_response


        else:
            raise ValueError(f"Provider {provider} not implemented")
    
    def process_tool_use(self,
                    content: str,
                    tools: List[Dict[str, Any]],
                    tool_mapping: Optional[Dict[str, Any]],
                    size: Optional[ModelSize] = None,
                    tool_choice: Optional[Dict[str, Any]] = None,
                    stream: bool = False,
                    provider: Optional[ModelProvider] = None,
                    raw_output: bool = False,   
                    max_tokens: int = 1024,
                    thinking:bool=False) -> Dict[str, Any]:
        """
        Point d'entrée unifié pour l'utilisation d'outils.
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider

        # Détermination de la taille du modèle
        if size is None:
            size = self.get_default_model_size(provider)
            if size is None:
                # Si aucune taille par défaut n'est définie, utiliser la première taille disponible
                available_sizes = list(self.provider_models[provider].keys())
                if not available_sizes:
                    raise ValueError(f"No model sizes available for provider {provider}")
                size = available_sizes[0]
                print(f"No default model size set for {provider}, using {size}")
        
        model = self.get_model_by_size_and_provider(provider, size)
        provider_instance = self.get_provider_instance(provider)

        transformed_tools = self._transform_tools_for_provider(tools, provider)
        transformed_tool_choice = self._transform_tool_choice(tool_choice, provider, bool(tools))
        transformed_mapping = self._transform_tool_mapping(tool_mapping, provider)
        print(f"impression de tool choice:{transformed_tool_choice}")
        if provider == ModelProvider.ANTHROPIC:
            # Utilise la méthode antho_agent du provider Anthropic
            response = provider_instance.antho_agent(
                content=content,
                model_name=self._get_model_index(model),
                antho_tools=tools,
                tool_mapping=transformed_mapping,
                tool_choice=tool_choice,
                stream=stream,
                max_tokens=max_tokens,
                raw_output=raw_output,
                thinking=thinking
            )

            return response
        elif provider == ModelProvider.OPENAI:
            # Si la taille commence par 'REASONING', changer le nom de l'argument
            max_tokens_key = 'max_completion_tokens' if size.name.startswith('REASONING') else 'max_tokens'

            response = provider_instance.openai_agent(
                content=content,
                model_name=self._get_model_index(model),
                tools=transformed_tools,  # Utilise les outils transformés
                tool_mapping=transformed_mapping,
                tool_choice=transformed_tool_choice,
                stream=stream,
                raw_output=raw_output,
                **{max_tokens_key: max_tokens}  # Utilise la clé correcte pour max_tokens
            )
            return response

        elif provider == ModelProvider.GEMINI:
                # Configuration spécifique pour Gemini
                response = provider_instance.process_tool_use(
                    content=content,
                    tools=transformed_tools,
                    model_name=self._get_model_index(model),
                    tool_mapping=transformed_mapping,
                    tool_choice=transformed_tool_choice,
                    stream=stream,
                    max_tokens=max_tokens
                )
                
                # Gestion des erreurs et uniformisation de la réponse
                if 'error' in response:
                    return {'error': response['error']}
                    
                elif 'tool_calls' in response:
                    return response['tool_calls'][0]['arguments']
                    
                else:
                    return {
                        'text_output': response.get('text_output', {}),
                        'provider': 'gemini',
                        'model': model
                    }
        
        elif provider == ModelProvider.DEEP_SEEK:
            response = provider_instance.deepseek_agent(  # Nouvelle méthode à créer
                content=content,
                model_name=self._get_model_index(model),
                tools=transformed_tools,  # Utilise les outils transformés
                tool_mapping=transformed_mapping,
                tool_choice=transformed_tool_choice,
                stream=stream,
                raw_output=raw_output,
                max_tokens=max_tokens
            )
            return response  # Transformation de la réponse
        
        else:
            raise ValueError(f"Provider {provider} not implemented")

    def _get_model_index(self, model: str) -> str:
        """
        Renvoie le nom exact du modèle correspondant.
        """
        # Anthropic models
        if model in self.provider_models[ModelProvider.ANTHROPIC][ModelSize.SMALL]:
            return self.provider_models[ModelProvider.ANTHROPIC][ModelSize.SMALL][0]
        elif model in self.provider_models[ModelProvider.ANTHROPIC][ModelSize.MEDIUM]:
            return self.provider_models[ModelProvider.ANTHROPIC][ModelSize.MEDIUM][0]
        elif model in self.provider_models[ModelProvider.ANTHROPIC][ModelSize.LARGE]:
            return self.provider_models[ModelProvider.ANTHROPIC][ModelSize.LARGE][0]
        
        # OpenAI models
        elif model in self.provider_models[ModelProvider.OPENAI][ModelSize.SMALL]:
            return self.provider_models[ModelProvider.OPENAI][ModelSize.SMALL][0]
        elif model in self.provider_models[ModelProvider.OPENAI][ModelSize.MEDIUM]:
            return self.provider_models[ModelProvider.OPENAI][ModelSize.MEDIUM][0]
        elif model in self.provider_models[ModelProvider.OPENAI][ModelSize.LARGE]:
            return self.provider_models[ModelProvider.OPENAI][ModelSize.LARGE][0]
        elif model in self.provider_models[ModelProvider.OPENAI][ModelSize.REASONING_SMALL]:
            return self.provider_models[ModelProvider.OPENAI][ModelSize.REASONING_SMALL][0]
        elif model in self.provider_models[ModelProvider.OPENAI][ModelSize.REASONING_LARGE]:
            return self.provider_models[ModelProvider.OPENAI][ModelSize.REASONING_LARGE][0]
        
        # Gemini models
        elif model in self.provider_models[ModelProvider.GEMINI][ModelSize.SMALL]:
            return self.provider_models[ModelProvider.GEMINI][ModelSize.SMALL][0]
        elif model in self.provider_models[ModelProvider.GEMINI][ModelSize.MEDIUM]:
            return self.provider_models[ModelProvider.GEMINI][ModelSize.MEDIUM][0]
        elif model in self.provider_models[ModelProvider.GEMINI][ModelSize.LARGE]:
            return self.provider_models[ModelProvider.GEMINI][ModelSize.LARGE][0]
        elif model in self.provider_models[ModelProvider.GEMINI][ModelSize.REASONING_SMALL]:
            return self.provider_models[ModelProvider.GEMINI][ModelSize.REASONING_SMALL][0]
        
        #Deepseek models
        
        elif model in self.provider_models[ModelProvider.DEEP_SEEK][ModelSize.MEDIUM]:
            return self.provider_models[ModelProvider.DEEP_SEEK][ModelSize.MEDIUM][0]
        elif model in self.provider_models[ModelProvider.DEEP_SEEK][ModelSize.REASONING_MEDIUM]:
            return self.provider_models[ModelProvider.DEEP_SEEK][ModelSize.REASONING_MEDIUM][0]
        
        # Perplexity models
        elif model in self.provider_models[ModelProvider.PERPLEXITY][ModelSize.SMALL]:
            return self.provider_models[ModelProvider.PERPLEXITY][ModelSize.SMALL][0]
        elif model in self.provider_models[ModelProvider.PERPLEXITY][ModelSize.MEDIUM]:
            return self.provider_models[ModelProvider.PERPLEXITY][ModelSize.MEDIUM][0]
        elif model in self.provider_models[ModelProvider.PERPLEXITY][ModelSize.LARGE]:
            return self.provider_models[ModelProvider.PERPLEXITY][ModelSize.LARGE][0]


        raise ValueError(f"Unknown model: {model}")

    def format_chat_history(self, provider: Optional[ModelProvider] = None) -> str:
        """
        Sauvegarde et formate l'historique des chats depuis un provider.
        
        Args:
            provider: Le provider dont l'historique doit être formaté
            
        Returns:
            str: L'historique formaté
        """
        # Utilise le provider par défaut si non spécifié
        if provider is None:
            provider = self.default_provider
        if provider is None:
            return "Aucun provider configuré"
            
        # Sauvegarde l'historique du provider
        if not self.save_chat_history(provider):
            return "Aucun historique disponible"
            
        # Récupère l'historique sauvegardé pour ce provider
        history = self.chat_history.get(provider.value, [])
        if not history:
            return "Aucun historique disponible"
            
        # Formate l'historique
        formatted_entries = []
        for entry in history:
            if isinstance(entry, dict) and 'role' in entry and 'content' in entry:
                formatted_entries.append(
                    f"Role: {entry['role']}\nContent: {entry['content']}\n"
                )
                
        return "\n".join(formatted_entries) if formatted_entries else "Format d'historique invalide"
        
    def save_chat_history(self,provider: Optional[ModelProvider] = None):
        """
        Sauvegarde l'historique du chat depuis l'instance provider active.
        
        Args:
            provider: Le provider dont l'historique doit être sauvegardé
        
        Returns:
            bool: True si la sauvegarde a réussi, False sinon
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Aucun provider spécifié et pas de provider par défaut")
            provider = self.default_provider
        print(f"impression de provider:{provider}")
        instance = self.get_provider_instance(provider)
        if hasattr(instance, 'chat_history'):
            self.chat_history[provider.value] = instance.chat_history
            print(f"impression du chat_history: {self.chat_history[provider.value]}")
            return {'sucess':True,'chat_history':self.chat_history[provider.value]}
        return {'sucess':False,'chat_history':"Erreur sur la méthode save_chat_history"}

    def load_chat_history(self, provider: Optional[ModelProvider] = None, history=None):
        """
        Charge l'historique du chat pour un provider spécifique avec les transformations nécessaires.
        Si history est fourni, l'utilise comme nouvelle source, sinon utilise l'historique stocké.
        
        Args:
            provider (Optional[ModelProvider]): Le provider pour lequel charger l'historique. 
                                            Si non spécifié, utilise le provider par défaut.
            history: Historique optionnel à charger
        
        Returns:
            bool: True si le chargement a réussi, False sinon
            
        Raises:
            ValueError: Si aucun provider n'est spécifié et qu'il n'y a pas de provider par défaut
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Aucun provider spécifié et pas de provider par défaut")
            provider = self.default_provider
        print(f"impression de provider:{provider}")
        instance = self.get_provider_instance(provider)
        history_to_load = history if history is not None else self.chat_history.get(provider.value, [])
        
        if not history_to_load:
            return False
            
        if provider == ModelProvider.ANTHROPIC:
            # Format Anthropic: [{"role": "user/assistant", "content": "message"}]
            transformed_history = self._transform_for_anthropic(history_to_load)
            if hasattr(instance, 'chat_history'):

                instance.chat_history = transformed_history
                return True
                
        # Ajouter d'autres providers ici avec leurs transformations spécifiques
                
        return False

    def _transform_for_anthropic(self, history):
        """
        Transforme l'historique au format attendu par Anthropic.
        
        Args:
            history: L'historique à transformer
            
        Returns:
            list: L'historique transformé au format Anthropic
        """
        transformed = []
        for msg in history:
            # Assurez-vous que le message a le bon format
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                # Anthropic n'accepte que 'user' ou 'assistant' comme rôles
                role = msg['role']
                if role not in ['user', 'assistant']:
                    continue
                    
                transformed.append({
                    "role": role,
                    "content": msg['content']
                })
        return transformed

    def flush_chat_history(self, provider: Optional[ModelProvider] = None):
        """
        Vide l'historique du chat au niveau de l'instance du provider.
        
        Args:
            provider (ModelProvider, optional): Provider spécifique. Si non fourni, utilise le provider par défaut.
        """
        if provider is None:
            provider = self.default_provider
        if provider is None:
            raise ValueError("Aucun provider spécifié et pas de provider par défaut")
        
        instance = self.get_provider_instance(provider)
        if hasattr(instance, 'flush_chat_history'):
            instance.flush_chat_history()

    def clear_chat_history(self):
        self.chat_history = []

    def update_token_usage(self, input_tokens: int, output_tokens: int):
        self.token_usage["total_input_tokens"] += input_tokens
        self.token_usage["total_output_tokens"] += output_tokens

    def get_token_usage_by_provider(self):
        """
        Récupère l'utilisation des tokens, le modèle et les coûts pour chaque provider enregistré.
        """
        usage = {}
        for provider, instance in self.provider_instances.items():
            if hasattr(instance, 'get_total_tokens'):
                # On récupère d'abord les tokens de l'instance
                token_info = instance.get_total_tokens()
                #print(f"impression de token_info:{token_info}")
                # Initialisation des valeurs par défaut
                current_model = None
                input_tokens = 0
                output_tokens = 0
                # Si on a des données pour au moins un modèle
                if token_info and hasattr(instance, 'current_model'):
                    current_model = instance.current_model
                    # On cherche les données du modèle actuel
                    if current_model in token_info:
                        model_data = token_info[current_model]
                        input_tokens = model_data['total_input_tokens']
                        output_tokens = model_data['total_output_tokens']
                
                # Création du dictionnaire de résultats
                provider_info = {
                    'total_input_tokens': input_tokens,
                    'total_output_tokens': output_tokens,
                    'model': current_model
                }
                
                # Ajout des informations de coût si on a un modèle
                if current_model:
                    cost_info = ModelPricing.calculate_token_cost(
                        current_model,
                        input_tokens,
                        output_tokens
                    )
                    provider_info.update(cost_info)
                    
                usage[provider.value] = provider_info
        
        return usage

    def send_message_to_space(self, message: str, **kwargs):
        """
        Envoie un message via le système de chat configuré.
        """
        if self.chat_system and hasattr(self.chat_system, 'send_message'):
            return self.chat_system.send_message(message, **kwargs)

    def add_user_message(self, message: str, provider: Optional[ModelProvider] = None):
        """
        Point d'entrée unifié pour l'ajout de messages utilisateur.
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider
        
        provider_instance = self.get_provider_instance(provider)

        if provider == ModelProvider.ANTHROPIC:
            provider_instance.add_user_message(message)
        elif provider == ModelProvider.OPENAI:
            provider_instance.add_user_message(message)
        elif provider == ModelProvider.GEMINI:
            provider_instance.add_user_message(message)
        elif provider == ModelProvider.DEEP_SEEK:
            provider_instance.add_user_message(message)
        else:
            raise ValueError(f"Provider {provider} not implemented")

    def add_ai_message(self, message: str, provider: Optional[ModelProvider] = None):
        """
        Point d'entrée unifié pour l'ajout de messages utilisateur.
        """
        if provider is None:
            if self.default_provider is None:
                raise ValueError("Provider not specified and no default provider set.")
            provider = self.default_provider
        
        provider_instance = self.get_provider_instance(provider)

        if provider == ModelProvider.ANTHROPIC:
            provider_instance.add_ai_message(message)
        elif provider == ModelProvider.OPENAI:
            provider_instance.add_ai_message(message)
        elif provider == ModelProvider.GEMINI:
            provider_instance.add_ai_message(message)
        elif provider == ModelProvider.DEEP_SEEK:
            provider_instance.add_ai_message(message)
        else:
            raise ValueError(f"Provider {provider} not implemented")

    def add_messages_ai_hu(self, message: str, ai_message: Optional[str] = None, mode: str = 'simple', provider: Optional[ModelProvider] = None):
        """
        Ajoute des messages utilisateur et AI, avec option d'envoi via système de chat.
        """
        if not ai_message:
            ai_message = 'ok bien recu'

        if mode == 'detailed' and self.chat_system:
            self.send_message_to_space(message)

        instance = self.get_provider_instance(provider if provider else self.default_provider)
        instance.add_user_message(message)
        instance.add_ai_message(ai_message)

    def agent_workflow(self,
                initial_user_input: str,
                size: ModelSize,
                tools: List[Dict[str, Any]],
                tool_mapping: Dict[str, Any],
                clerck_instance: Any,
                manager_instance: Any,
                auditor_instance: Any,
                manager_prompt: str,
                provider: Optional[ModelProvider] = None,
                max_tokens: int = 1024,
                project_id:str=None,
                job_id:str=None,
                workflow_step:str=None) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Implements a workflow for agent interaction with multiple specialized instances.
        
        Args:
            initial_user_input (str): The initial query from the user
            size (ModelSize): Size of the model to use
            tools (List[Dict[str, Any]]): List of available tools
            tool_mapping (Dict[str, Any]): Mapping of tool names to their functions
            clerck_instance (Any): Instance handling accounting clerk tasks
            manager_instance (Any): Instance handling management tasks
            auditor_instance (Any): Instance handling auditing tasks
            manager_prompt (str): System prompt for the manager instance
            provider (Optional[ModelProvider]): The AI provider to use
            max_tokens (int): Maximum tokens for response generation
            
        Returns:
            Tuple[bool, Optional[str], Optional[str], Optional[str]]: 
                - Success status
                - Next step
                - Instructions
                - Response text
        """
        print("Starting agent workflow interaction!")
        print("The workflow will automatically terminate after 'TERMINATE' or 5 turns.")
        
        max_turns = 5
        turn_count = 0
        
        # Initialize instances with their system prompts
        manager_instance.update_system_prompt(manager_prompt)
        clerck_instance.update_system_prompt("Tu es un assistant comptable")
        auditor_instance.update_system_prompt("Tu es un auditeur comptable")

        user_input = initial_user_input
        answer_text = ""
        manager_instance.flush_chat_history()
        
        while turn_count < max_turns:
            turn_count += 1
            
            print(f"\033[95mCurrent user input: {user_input}\033[0m")
            response = manager_instance.process_tool_use(
                content=user_input,
                tools=tools,
                tool_mapping=tool_mapping,
                size=size,
                provider=provider,
                max_tokens=max_tokens
            )
            print(f"\033[93mWorkflow response: {response}\033[0m")

            manager_instance.load_token_usage_to_db(project_id=project_id,job_id=job_id,workflow_step=workflow_step)

            next_user_input = ""
            new_answer_text = ""

            if isinstance(response, list) and len(response) > 0:
                if "tool_output" in response[0]:
                    tool_block = response[0]["tool_output"]
                    tool_name = tool_block.get('tool_name')
                    tool_content = tool_block.get('content', '')

                    if tool_name == 'GET_JOB_ID_DETAILS':
                        print(f"Tool output for 'GET_JOB_ID_DETAILS': {tool_content}")
                        next_user_input = f"Tool GET_JOB_ID_DETAILS response: {tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'ASK_KDB_JOURNAL':
                        print(f"Tool output for 'ASK_KDB_JOURNAL': {tool_content}")
                        next_user_input = f"Tool ASK_KDB_JOURNAL response: {tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'VIEW_PAYLOAD':
                        print(f"Tool output for 'VIEW_PAYLOAD': {tool_content}")
                        next_user_input = f"Tool VIEW_PAYLOAD response: {tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'UPDATE_INVOICE_INFORMATION':
                        print(f"Tool output for 'UPDATE_INVOICE_INFORMATION': {tool_content}")
                        next_user_input = f"Tool UPDATE_INVOICE_INFORMATION response: {tool_content}"
                        new_answer_text = next_user_input

                    elif tool_name == 'GET_CONTACT_INFO_IN_ODOO':
                        print(f"Tool output for 'GET_CONTACT_INFO_IN_ODOO': {tool_content}")
                        prompt = f"System response for Odoo contact search, please synthesize and respond: {tool_content}"
                        response_text = clerck_instance.process_text(
                            content=prompt,
                            size=size,
                            provider=provider,
                            max_tokens=max_tokens
                        )
                        next_user_input = f"Tool GET_CONTACT_INFO_IN_ODOO response: {response_text}"
                        new_answer_text = next_user_input

                    elif tool_name == 'GET_PRECISE_INFO_IN_SPACE_CHAT':
                        print(f"Tool output for 'GET_PRECISE_INFO_IN_SPACE_CHAT': {tool_content}")
                        next_user_input = f"Tool GET_PRECISE_INFO_IN_SPACE_CHAT response: {tool_content}"
                        new_answer_text = tool_content
                    
                    elif tool_name == 'ASK_USER_IN_CHAT':
                        print(f"Tool output for 'ASK_USER_IN_CHAT': {tool_content}")
                        if tool_content in ['NEXT', 'TERMINATE', 'PREV', 'CLOSE_APP', 'DELETE', 'PENDING']:
                            if tool_content == 'NEXT':
                                tool_content = 'NEXT_W_O_SAVING'
                            next_step = tool_content
                            instructions = f"Command {tool_content} initiated by user"
                            return True, next_step, instructions, None

                        prompt = "Based on the user conversation, please specify the actions to apply according to your context..."
                        new_answer_text = prompt

                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"Tool output for 'SEARCH_IN_CHART_OF_ACCOUNT': {tool_content}")
                        prompt = f"System response for chart of accounts search, please synthesize and respond: {tool_content}"
                        response_text = clerck_instance.process_text(
                            content=prompt,
                            size=size,
                            provider=provider,
                            max_tokens=max_tokens
                        )
                        next_user_input = f"Tool SEARCH_IN_CHART_OF_ACCOUNT response: {response_text}"
                        new_answer_text = next_user_input

                    elif tool_name == 'VIEW_DOCUMENT_WITH_VISION':
                        print(f"Tool output for 'VIEW_DOCUMENT_WITH_VISION': {tool_content}")
                        next_user_input = f"Tool VIEW_DOCUMENT_WITH_VISION response: {tool_content}"
                        new_answer_text = tool_content
                    
                    elif tool_name == 'NEXT_STEP_AND_INSTRUCTIONS':
                        print(f"Tool output for 'NEXT_STEP_AND_INSTRUCTIONS': {tool_content}")
                        next_step = tool_content['next_step']
                        instructions = tool_content['instruction']
                        
                        prompt = "The session will now end. Please summarize the actions taken, your choices, and the reasoning behind them."
                        chat_summary = manager_instance.process_text(
                            content=prompt,
                            size=size,
                            provider=provider,
                            max_tokens=max_tokens
                        )
                        
                        return True, next_step, instructions, chat_summary

                elif "text_output" in response[0]:
                    print(f"impression de response[0] dans text_output:{response[0]}")
                    text_block = response[0]["text_output"]
                    if isinstance(text_block, dict):
                        new_answer_text = text_block.get('answer_text', 'No answer response available')
                        thinking_text = text_block.get('thinking_text', 'No thinking response available')
                    else:
                        new_answer_text = text_block
                    next_user_input = f"Auditor response: {new_answer_text}"

            # Update answer_text and user_input
            answer_text = new_answer_text if new_answer_text else answer_text
            user_input = next_user_input if next_user_input else answer_text

        # Session end summary
        prompt = "The session will now end. Please summarize the actions taken, your choices, and the reasoning behind them."
        chat_summary = manager_instance.process_text(
            content=prompt,
            size=size,
            provider=provider,
            max_tokens=max_tokens
        )
        manager_instance.load_token_usage_to_db(project_id=project_id,job_id=job_id,workflow_step=workflow_step)
        print(f"Maximum turns ({max_turns}) reached without 'TERMINATE'. Ending conversation.")
        return False, None, None, chat_summary
    
class NEW_DeepSeek_agent:
    def __init__(self, space_manager=None, collection_name=None, job_id=None):
        """
        Initialise la classe OpenAiAgent avec la clé API nécessaire pour authentifier les requêtes.
        
        :param space_manager: Gestionnaire d'espace (optionnel).
        :param collection_name: Nom de la collection (optionnel).
        :param job_id: ID du job (optionnel).
        """
        self.chat_history = []
        self.space_manager = space_manager
        self.collection_name = collection_name
        self.job_id = job_id
        self.api_key = get_secret('pinnokio_deepseek')
        self.client=OpenAI(api_key=self.api_key,base_url="https://api.deepseek.com")
        self.token_usage = {}
        self.current_model = None
        self.models=['deepseek-chat']
    
    def get_models(self):
        get_models=self.client.models.list()
        print(f"impression get_MODELS:{get_models}")

    def update_token_usage(self, raw_response):
        """
        Met à jour les compteurs de tokens pour OpenAI.
        
        Args:
            raw_response: La réponse brute d'OpenAI contenant les informations d'utilisation
        """
        if hasattr(raw_response, 'model') and hasattr(raw_response, 'usage'):
            model = raw_response.model
            
            if model not in self.token_usage:
                self.token_usage[model] = {
                    'total_input_tokens': 0,
                    'total_output_tokens': 0
                }

            # Mise à jour des tokens d'entrée (prompt)
            prompt_tokens = raw_response.usage.prompt_tokens
            self.token_usage[model]['total_input_tokens'] += prompt_tokens

            # Mise à jour des tokens de sortie (completion)
            completion_tokens = raw_response.usage.completion_tokens
            self.token_usage[model]['total_output_tokens'] += completion_tokens

            # Mise à jour du modèle courant
            self.current_model = model

            # Gérer les détails supplémentaires si disponibles
            if hasattr(raw_response.usage, 'prompt_tokens_details'):
                cached_tokens = getattr(raw_response.usage.prompt_tokens_details, 'cached_tokens', 0)
                audio_tokens = getattr(raw_response.usage.prompt_tokens_details, 'audio_tokens', 0)
                
                if 'details' not in self.token_usage[model]:
                    self.token_usage[model]['details'] = {}
                
                self.token_usage[model]['details'].update({
                    'cached_tokens': cached_tokens,
                    'audio_tokens': audio_tokens
                })

    def get_total_tokens(self):
        """
        Retourne l'utilisation totale des tokens pour chaque modèle.
        
        Returns:
            dict: Un dictionnaire contenant l'utilisation des tokens par modèle
                {
                    'model_name': {
                        'total_input_tokens': X,
                        'total_output_tokens': Y,
                        'model': 'model_name',
                        'details': {  # Optionnel, si disponible
                            'cached_tokens': Z,
                            'audio_tokens': W
                        }
                    }
                }
        """
        token_stats = {}
        
        for model, usage in self.token_usage.items():
            stats = {
                'total_input_tokens': usage['total_input_tokens'],
                'total_output_tokens': usage['total_output_tokens'],
                'model': model
            }
            
            # Ajouter les détails si disponibles
            if 'details' in usage:
                stats['details'] = usage['details']
                
            token_stats[model] = stats
            
        return token_stats

    def reset_token_counters(self):
        """
        Réinitialise tous les compteurs de tokens pour tous les modèles.
        """
        self.token_usage = {}

    def flush_chat_history(self):
        self.chat_history = []
        self.reset_token_counters()
    
    def add_user_message(self, content):
        if isinstance(content, dict):
            content = json.dumps(content)  # Convertit le dict en chaîne de caractères JSON
        self.chat_history.append({'role': 'user', 'content': content})
    
    def add_ai_message(self,content):
        if isinstance(content,dict):
            content=json.dumps(content)
        self.chat_history.append({'role': 'assistant', 'content': content})

    def update_system_prompt(self, content):
        if isinstance(content, dict):
            content = json.dumps(content)  # Convertit le dict en chaîne de caractères JSON
        self.chat_history.append({'role': 'system', 'content': content})
    
    def openai_send_message_tool(self, content, model_index=None,model_name=None, tool_list=None, tool_name=None,tool_choice=None):
        if tool_list is None or tool_name is None:
            # Si tool_list ou tool_name n'est pas renseigné, appelle une fonction alternative
            print("tool_list ou tool_name non renseigné, appel de openai_send_message.")
            return self.openai_send_message(content, model_index,model_name)
        else:
            # Sinon, continue avec la logique existante pour utiliser l'outil spécifié
            if not isinstance(model_index, int):
                chosen_model=model_name
            else:
                chosen_model = self.models[model_index]

            print(f"Modèle choisi : {chosen_model}")
            
            
            self.add_user_message(content)
            
            tools = self.find_tool_by_name(tool_list, tool_name)
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=self.chat_history,
                max_tokens=1024,
                tools=[tools],
                tool_choice=tool_choice
            )
            self.update_token_usage(response)
            print(response)
            
            # Vérifie si la réponse contient des tool_calls
            if response.choices[0].message.tool_calls:
                tool_calls = response.choices[0].message.tool_calls
                function_call = tool_calls[0]
                function_arguments = function_call.function.arguments
                arguments_dict = json.loads(function_arguments)
                #ai_response = arguments_dict
                ai_response = json.dumps(arguments_dict)
            else:
                ai_response = response.choices[0].message.content
            
            self.add_ai_message(ai_response)
            
            return ai_response
    
    def find_tool_by_name(self, tools_list, tool_name):
        """
        Recherche un outil spécifique par son nom dans une liste de dictionnaires.

        Args:
            tools_list (list): La liste des dictionnaires représentant les outils.
            tool_name (str): Le nom de l'outil à rechercher.

        Returns:
            dict or None: Le dictionnaire représentant l'outil trouvé, ou None si aucun outil correspondant n'est trouvé.
        """
        for tool in tools_list:
            # Ajoute un log pour voir le contenu de chaque outil
           

            # Vérifie si le nom de la fonction correspond
            if tool.get('function') and tool['function'].get('name') == tool_name:
                return tool
        
        return None  # Aucun outil trouvé avec le nom spécifié

    def deepseek_agent(self, content, model_index=None, model_name=None, tools=None, tool_mapping=None, verbose=True, tool_choice=None, stream=False,raw_output=False, max_tokens=1024):
        """
        Point d'entrée principal pour l'utilisation d'outils avec l'API OpenAI.
        
        Args:
            content (str): Le contenu du message
            model_index (int, optional): Index du modèle à utiliser
            model_name (str, optional): Nom spécifique du modèle
            tools (list): Liste des outils disponibles
            tool_mapping (dict): Mapping des outils vers leurs fonctions
            verbose (bool): Afficher les détails d'exécution
            tool_choice (dict): Configuration du choix d'outil
            stream (bool): Utiliser le streaming
            max_tokens (int): Nombre maximum de tokens pour la réponse

        Returns:
            list: Liste des réponses générées
        """
        if not model_name and model_index is not None:
            chosen_model = self.models[model_index]
        else:
            chosen_model = model_name
        
        # Configuration par défaut du tool_choice si non spécifié
        if tool_choice is None:
            tool_choice = "auto"  # Format OpenAI pour auto

        # Préparation des outils mappés
        mapped_tools = []
        if tool_mapping:
            if isinstance(tool_mapping, dict):
                tool_mapping = [tool_mapping]

            for tool_dict in tool_mapping:
                for tool_name, function_or_none in tool_dict.items():
                    tool_info = next((tool for tool in tools if 'function' in tool and tool['function']['name'] == tool_name), None)
                    if tool_info:
                        mapped_tools.append(tool_info)

        # Ajout du message utilisateur à l'historique
        self.add_user_message(content)
        print(f"impression du model choisi:{chosen_model}")
        try:
            # Création de la requête à l'API
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=self.chat_history,
                max_tokens=max_tokens,
                tools=tools if tools else None,
                tool_choice=tool_choice,
                stream=stream
            )
            
            # Mise à jour des compteurs de tokens
            self.update_token_usage(response)

            responses = []
            
            # Traitement de la réponse
            message = response.choices[0].message
            
            # Vérification de l'utilisation d'outils
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tool_call in message.tool_calls:
                    function_call = tool_call.function
                    tool_name = function_call.name
                    
                    try:
                        # Parsing des arguments de l'outil
                        arguments = json.loads(function_call.arguments)
                        
                        # Recherche de la fonction correspondante dans le mapping
                        function_or_none = None
                        for tool_dict in tool_mapping or []:
                            if tool_name in tool_dict:
                                function_or_none = tool_dict[tool_name]
                                break
                        
                        if callable(function_or_none):
                            # Exécution de la fonction
                            tool_result = function_or_none(**arguments)
                            
                            # Formatage de la réponse de l'outil
                            tool_response = {
                                "tool_output": {
                                    "tool_name": tool_name,
                                    "content": tool_result
                                }
                            }
                            responses.append(tool_response)
                        else:
                            # Si pas de fonction trouvée, retourner les arguments bruts
                            tool_response = {
                                "tool_output": {
                                    "tool_name": tool_name,
                                    "content": arguments
                                }
                            }
                            responses.append(tool_response)
                    
                    except json.JSONDecodeError as e:
                        print(f"Erreur de décodage JSON pour les arguments de l'outil: {e}")
                        continue
                    except Exception as e:
                        print(f"Erreur lors de l'exécution de l'outil {tool_name}: {e}")
                        continue

            # Si un message texte est présent
            if message.content:
                text_response = {
                    "text_output": {
                        "content": {
                            "answer_text": message.content,
                            "thinking_text": ""  # OpenAI n'a pas d'équivalent direct au thinking_text d'Anthropic
                        }
                    }
                }
                responses.append(text_response)

            # Ajout de la réponse à l'historique
            self.add_ai_message(message.content if message.content else str(responses))
            if not raw_output:
                data=self.final_handle_responses(responses)
            else:
                data=responses
            return data

        except Exception as e:
            print(f"Erreur lors de l'exécution de openai_agent: {e}")
            return [{
                "text_output": {
                    "content": {
                        "answer_text": f"Une erreur s'est produite: {str(e)}",
                        "thinking_text": ""
                    }
                }
            }]

    def final_handle_responses(self, input_data):
        
        # Vérifier si input_data est une liste
        if isinstance(input_data, list):
           
            # Si c'est une liste avec un seul élément, retourner directement cet élément
            if len(input_data) == 1:
                return self.basic_handle_response(input_data[0])
            else:
                
                # Si c'est une liste avec plusieurs éléments, traiter chaque élément
                results = []
                for item in input_data:
                    result = self.basic_handle_response(item)
                    results.append(result)
                return results
        else:
            # Si ce n'est pas une liste, traiter comme un seul élément
            #print("passage par final_handle_response, uraiter comme un seul élément")
            return self.basic_handle_response(input_data)

    def basic_handle_response(self, response):
        # Initialisation de la variable de sortie
        data = {}

        # Vérification des clés 'tool_output' et 'text_output' dans la réponse
        has_tool_output = 'tool_output' in response
        has_text_output = 'text_output' in response

        # Priorité de traitement : text_output > tool_output
        if has_tool_output and has_text_output:
            # Si à la fois du texte et un outil fournissent des sorties, les combiner pour une réponse enrichie
            print("Texte et outil présents, combinaison des réponses.")
            
            # Extraire les contenus du texte et de l'outil
            text_content = response['text_output'].get('content', '')
            tool_content = response['tool_output'].get('content', {})
            
            # Affichage pour le débogage
            #print("Réponse textuelle reçue :")
            #print(text_content)
            #print("Réponse de l'outil reçue :")
            #print(tool_content)

            # Combinaison des données dans un seul dictionnaire
            data = {
                'text_output': {'text': text_content},
                'tool_output': tool_content
            }

        elif has_text_output:
            text_content = response['text_output'].get('content', '')
            if isinstance(text_content, dict) and 'answer_text' in text_content:
                data= text_content['answer_text']
            #data = {'text_output': {'text': text_content}}

        elif has_tool_output:
            tool_content = response['tool_output'].get('content', {})
            if isinstance(tool_content, list):
            # Si c'est une liste, extraire les éléments de la liste
                data = []
                for item in tool_content:
                    if isinstance(item, dict) and 'text' in item:
                        data.append(item['text'])
            else:
                data = tool_content
            

            # Vérifier si 'next_step' fait partie des éléments autorisés dans step_list
            

        else:
            print("Format de réponse non reconnu.")
            #data = {'erreur': "Erreur dans l'extraction du texte"}
        #print(f"impression de reponses final dans basic_handle_response:{data}")
        return data



    def deepseek_send_message(self, content, model_index=None,model_name=None,stream=None,max_tokens=None):
        """
        Envoie un message en utilisant un des modèles spécifiés par index.
        Liste des modèles : ['gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo-0125']
        
        Args:
            content (str): Le contenu du message à envoyer.
            model_index (int): L'index du modèle à utiliser pour envoyer le message.
        """
        if max_tokens is None:
            max_tokens=1024
        if not stream:
            stream=False
        # Sélection du modèle en utilisant l'index fourni
        if not isinstance(model_index, int):
            chosen_model=model_name
        else:
            chosen_model = self.models[model_index]

        
        # Déterminer si le contenu est de type vision
        is_vision_content = isinstance(content, list) and any(isinstance(item, dict) and 
                        ('type' in item or 'role' in item) for item in content)
        
        if is_vision_content:
            # Pour le contenu vision, nous utilisons directement le format fourni
            messages = content
        else:
            # Pour le texte simple, nous utilisons l'historique normal
            self.add_user_message(content)
            messages = self.chat_history

        
        try:
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=messages,
                stream=stream,
                max_tokens=max_tokens,
            )
            #print(response)
            self.update_token_usage(response)
            ai_response = response.choices[0].message.content
            self.add_ai_message(ai_response)
            return ai_response
        except Exception as e:
            print(f"Erreur lors de l'envoi du message : {e}")
            return None

    
    def chat_openai_agent(self, model_index):
        """
        Fonction permettant des échanges continus avec l'agent OpenAI.

        Args:
            agent (OpenAIAgent): Instance de l'agent OpenAI.
            model_index (int): L'index du modèle à utiliser pour envoyer les messages.
        """
        while True:
            user_input = input("Vous: ")
            if user_input.lower() in ['exit', 'quit', 'stop']:
                print("Chat terminé.")
                break
            
            response = self.openai_send_message(user_input, model_index)
            if response:
                print(f"Agent: {response}")
            else:
                print("Erreur lors de l'envoi du message.")

class NEW_Anthropic_Agent:
    def __init__(self,space_manager=None,drive_manager=None,collection_name=None,job_id=None):
        """
        Initialise la classe Mistral avec la clé API nécessaire pour authentifier les requêtes.
        
        :param api_key: Clé API pour accéder à l'API de Mistral AI.
       
         """
        self.chat_history = []
        self.space_manager=space_manager
        self.drive_manager=drive_manager
        self.collection_name=collection_name
        self.job_id=job_id
        self.tool_list=[]
        self.system_prompt=None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.models=['claude-3-5-sonnet-20240620', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307', 'claude-3-opus-20240229']

        self.api_key = get_secret('ANTHROPIC_PINNOKIO')
        self.client = Anthropic(
       
    # defaults to os.environ.get("ANTHROPIC_API_KEY")
            api_key=self.api_key,
        )
        self.client_stream=AsyncAnthropic(api_key=self.api_key)
          # Utilisez add_ai_message ou add_user_message selon le rôle du system_prompt
        
        
        self.messages = []
        self.thread_pool = ThreadPoolExecutor()
        self.token_usage = {}  # format: {'model_name': {'input': X, 'output': Y}}
        self.current_model = None
    
    def update_system_prompt(self,system_prompt):
        self.system_prompt=system_prompt
        print(f"impression de system_prompt dans update_system_prompt: {system_prompt}")
        print(f"Type de system_prompt: {type(system_prompt)}")

    def flush_chat_history(self):
        self.chat_history=[]
        self.reset_token_counters()


    def reset_token_counters(self):
        """
        Réinitialise tous les compteurs de tokens pour tous les modèles.
        """
        self.token_usage = {}

    def update_job_id(self,job_id):
        self.job_id=job_id

    def add_user_message(self, message):
    # Ajoute un message utilisateur à l'historique des chats avec une indication claire du rôle "user"
        self.chat_history.append({"role": "user", "content":message})

    def add_ai_message(self, message):
    # Ajoute un message de l'assistant à l'historique des chats avec une indication claire du rôle "assistant"
        self.chat_history.append({"role": "assistant", "content":message})

    def add_messages_ai_hu(self,message,ai_message=None,mode='simple'):
       
        if not ai_message:
            ai_message='ok bien recu'
        else:
            ai_message=ai_message

        if mode == 'detailed':
            # Utiliser space_manager pour envoyer le message si le mode est 'detailed'
            self.space_manager.send_message(self.collection_name, self.job_id, text=message)
            self.add_user_message(message)
            self.add_ai_message(ai_message)
        
        elif mode =='simple':
            self.add_user_message(message)
            self.add_ai_message(ai_message)

    def update_token_usage(self, raw_response):
        """
        Met à jour les compteurs de tokens par modèle.
        
        Args:
            raw_response: La réponse brute d'Anthropic
        """
        if hasattr(raw_response, 'usage') and hasattr(raw_response, 'model'):
            model = raw_response.model
            if model not in self.token_usage:
                self.token_usage[model] = {
                    'total_input_tokens': 0,
                    'total_output_tokens': 0
                }
            self.token_usage[model]['total_input_tokens'] += raw_response.usage.input_tokens
            self.token_usage[model]['total_output_tokens'] += raw_response.usage.output_tokens
            self.current_model = model

    def get_total_tokens(self):
        """
        Retourne l'utilisation des tokens pour chaque modèle.
        
        Returns:
            dict: Utilisation des tokens par modèle
        """
        return {
            model: {
                'total_input_tokens': usage['total_input_tokens'],
                'total_output_tokens': usage['total_output_tokens'],
                'model': model
            }
            for model, usage in self.token_usage.items()
        }
        
    def final_handle_responses(self, input_data):
        
        # Vérifier si input_data est une liste
        if isinstance(input_data, list):
           
            # Si c'est une liste avec un seul élément, retourner directement cet élément
            if len(input_data) == 1:
                return self.basic_handle_response(input_data[0])
            else:
                
                # Si c'est une liste avec plusieurs éléments, traiter chaque élément
                results = []
                for item in input_data:
                    result = self.basic_handle_response(item)
                    results.append(result)
                return results
        else:
            # Si ce n'est pas une liste, traiter comme un seul élément
            #print("passage par final_handle_response, uraiter comme un seul élément")
            return self.basic_handle_response(input_data)

    def basic_handle_response(self, response):
        # Initialisation de la variable de sortie
        data = {}

        # Vérification des clés 'tool_output' et 'text_output' dans la réponse
        has_tool_output = 'tool_output' in response
        has_text_output = 'text_output' in response

        # Priorité de traitement : text_output > tool_output
        if has_tool_output and has_text_output:
            # Si à la fois du texte et un outil fournissent des sorties, les combiner pour une réponse enrichie
            print("Texte et outil présents, combinaison des réponses.")
            
            # Extraire les contenus du texte et de l'outil
            text_content = response['text_output'].get('content', '')
            tool_content = response['tool_output'].get('content', {})
            
            # Affichage pour le débogage
            #print("Réponse textuelle reçue :")
            #print(text_content)
            #print("Réponse de l'outil reçue :")
            #print(tool_content)

            # Combinaison des données dans un seul dictionnaire
            data = {
                'text_output': {'text': text_content},
                'tool_output': tool_content
            }

        elif has_text_output:
            text_content = response['text_output'].get('content', '')
            if isinstance(text_content, dict) and 'answer_text' in text_content:
                data= text_content['answer_text']
            #data = {'text_output': {'text': text_content}}

        elif has_tool_output:
            tool_content = response['tool_output'].get('content', {})
            if isinstance(tool_content, list):
            # Si c'est une liste, extraire les éléments de la liste
                data = []
                for item in tool_content:
                    if isinstance(item, dict) and 'text' in item:
                        data.append(item['text'])
            else:
                data = tool_content
            

            # Vérifier si 'next_step' fait partie des éléments autorisés dans step_list
            

        else:
            print("Format de réponse non reconnu.")
            #data = {'erreur': "Erreur dans l'extraction du texte"}
        #print(f"impression de reponses final dans basic_handle_response:{data}")
        return data


    def handle_response(self, response, step_list=None):
        # Initialisation de la variable de sortie
        data = {}

        # Vérification des clés 'tool_output' et 'text_output' dans la réponse
        has_tool_output = 'tool_output' in response
        has_text_output = 'text_output' in response

        # Priorité de traitement : text_output > tool_output
        if has_tool_output and has_text_output:
            print("Texte et outil présents, choix textuel.")
            text_content = response['text_output'].get('content', '')
            #print("Réponse textuelle reçue :")
            #print(text_content)

            thinking_content = ""
            if "<thinking>" in text_content and "</thinking>" in text_content:
                start_index = text_content.index("<thinking>") + len("<thinking>")
                end_index = text_content.index("</thinking>")
                thinking_content = text_content[start_index:end_index].strip()
                text_content = text_content[:start_index] + text_content[end_index + len("</thinking>"):]
                text_content = text_content.strip()

            if thinking_content and not text_content.strip():
                print("Contenu <thinking> présent, pas de texte restant, priorité à l'outil.")
                tool_name = response['tool_output']['name']
                tool_output = response['tool_output']['output']
                print(f"Outil sélectionné : {tool_name}")
                print(f"Sortie de l'outil : {tool_output}")

                data = {'tool_output': {'name': tool_name, 'output': tool_output}}
            
            elif text_content.strip():
                print("Réponse textuelle après extraction de <thinking> :")
                print(text_content)

                data = {'text_output': {'text': text_content}}
            else:
                print("Pas de contenu textuel ou d'outil valide.")
                data = None

        elif has_text_output:
            text_content = response['text_output'].get('content', '')
            #print("Réponse textuelle reçue :")
            #print(text_content)
            data = {'text_output': {'text': text_content}}

        elif has_tool_output:
            tool_content = response['tool_output'].get('content', {})
            next_step = tool_content.get('next_step', '')
            instruction = tool_content.get('instruction', '')

            # Vérifier si 'next_step' fait partie des éléments autorisés dans step_list
            if step_list is not None and next_step in step_list:
                print("Le next_step est autorisé.")
                print(f"Next Step: {next_step}")
                print(f"Instruction: {instruction}")
                data = {'tool_output': {'next_step': next_step, 'instruction': instruction}}
            elif step_list is not None:
                print("Le next_step n'est pas autorisé ou n'existe pas.")
                print("Aucune action spécifique n'est nécessaire.")
                data = {'erreur': "Le next_step n'est pas autorisé ou n'existe pas."}
            else:
                print("Step_list non fourni, traitement basique du tool_output.")
                data = {'tool_output': {'instruction': instruction}}

        else:
            print("Format de réponse non reconnu.")
            data = {'erreur': "Erreur dans l'extraction du texte"}

        return data

    async def run_in_thread(self, func, *args, **kwargs):
        """Helper pour exécuter des fonctions synchrones dans le thread pool"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.thread_pool, partial(func, *args, **kwargs))



    def analyse_multipage_docs(self, text, model_index=None,model_name=None, drive_service=None, file_ids=None, files_to_download=None,local_files=None,method='batch',final_resume=True,
                               tool_list=None, tool_mapping=None,tool_choice=None):
        '''if tool_choice is None:
            print(f"tool choice n'est pas définit par défaut 'auto'.....")
            tool_choice={"type": "auto"}'''
        
        mapped_tools = []
        tool_names = []
        
        if tool_mapping:
            if isinstance(tool_mapping, dict):
                tool_mapping = [tool_mapping]
            
            for tool_dict in tool_mapping:
                for tool_name, function_or_none in tool_dict.items():
                    tool_info = next((tool for tool in tool_list if 'name' in tool and tool['name'] == tool_name), None)
                    if not tool_info:
                        print(f"Aucun outil correspondant trouvé pour {tool_name}.")
                        continue

                    mapped_tools.append(tool_info)
            
            tool_names = [tool['name'] for tool in mapped_tools] if mapped_tools else []
            print(f"print l'outil a été trouvé son nom est: {tool_names}")
        
        # Convertir tool_names en chaîne si elle ne contient qu'un seul élément
        if len(tool_names) == 1:
            tool_names = tool_names[0]
        answer = self.analyze_image(text,model_index,model_name, drive_service, file_ids, files_to_download,local_files,method,
                                    tool_list=tool_list, tool_name=tool_names,tool_choice=tool_choice)
        
        #print(f"impression de answer dans analyse_mulitple_docs niveau 1:{answer}")
        def format_answer_to_text(answer):
            formatted_text = ""
            
            # Trier les clés pour s'assurer que les réponses sont dans l'ordre
            sorted_keys = sorted([key for key in answer.keys() if key.startswith('response_')])
            
            for key in sorted_keys:
                formatted_text += f"{key}: {answer[key]}\n\n"
            
            return formatted_text.strip()
        
        if 'vision_with_tool' in answer:
            print(f"utilsation d'utoillll.......")
            data = answer['vision_with_tool']
            print(f"impression de l'output answer de analyse_mutlipage_docs:{data}")
            # Extraire toutes les valeurs sous les clés qui commencent par 'response_batch'
            response_batches = {key: value for key, value in data.items() if key.startswith('response_batch')}
            
            # Retourner les résultats extraits
            
            return response_batches 
            
        answer=format_answer_to_text(answer)
        #print(f"impression de answer dans analyse_Multiple_docs:{answer}")
        if final_resume:
            prompt=f"""Un document a été traité de plusieurs pages dont voici les réponses découpés par page.\n\n{answer}\n\n Conformément à la question initial:{text}
            Merci d'apporter une synthese des réponses apporté"""
            
           
            #Methode avec appel direct au point de terminaison réponse direct mais sans streaming et non possibilité de travailler sur
            #la structuration de la réponse en output
            if not isinstance(model_index, int):
                chosen_model=model_name
            else:
                chosen_model = self.models[model_index]

            data = self.client.messages.create(
                            model=chosen_model,
                            max_tokens=1024,
                            system=self.system_prompt,
                            messages=[{"role": "user", "content": prompt}],
                        )
            print(f"imprssion de la reponse final dans analyse_mulitpe_docds:{data}")
            return  data.content[0].text

            #return accumulated_response
        else:

            return  answer

    
    def analyze_image(self, text, model_index=None, model_name=None, drive_service=None, 
                 file_ids=None, files_to_download=None, local_files=None, 
                 method='batch', tool_list=None, tool_name=None, tool_choice=None):
        """
        Analyse des images avec gestion centralisée des appels API.
        """
        #print(f"impression de text:{text}")
        def process_image_data( content, tools=None, tool_choice=None):
            """
            Point centralisé pour l'appel à l'API Anthropic.
            """
            

            try:
                if tools:
                    if isinstance(tool_name, str):
                        tools = [self.find_tool_by_name(tool_list, tool_name)]
                    elif isinstance(tool_name, list):
                        tools = [self.find_tool_by_name(tool_list, name) for name in tool_name]
                    response = self.client.messages.create(
                        model=chosen_model,
                        max_tokens=1024,
                        messages=[{"role": "user", "content": content}],
                        system=self.system_prompt,
                        tools=tools if tools else None,
                        tool_choice=tool_choice if tool_choice else None,
                    )
                else:
                    
                    response = self.client.messages.create(
                        model=chosen_model,
                        max_tokens=1024,
                        messages=[{"role": "user", "content": content}],
                        system=self.system_prompt,
                        
                    )

                self.update_token_usage(response)
                return response
            except Exception as e:
                print(f"Erreur lors de l'appel API: {e}")
                return None

        def prepare_image_content(img_data, media_type, text):
            """
            Prépare le contenu pour l'API avec l'image et le texte.
            """
            return [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(img_data).decode("utf-8"),
                    }
                },
                {
                    "type": "text",
                    "text": text,
                }
            ]
        
        

        # Sélection du modèle
        if not model_name:
            chosen_model = self.models[model_index]
        else:
            chosen_model = model_name

        # Traitement des images
        all_images = []

        # 1. Traitement des images depuis Google Drive
        if drive_service and file_ids:
            print(f"fichier drive a telecharger....")
            for file_id in file_ids:
                try:
                    file_metadata = drive_service.drive_service.files().get(
                        fileId=file_id, 
                        fields='mimeType'
                    ).execute()
                    
                    if not drive_service.is_acceptable_file_type(file_metadata['mimeType']):
                        print(f"Type MIME non supporté: {file_metadata['mimeType']}")
                        continue
                        
                    images = drive_service.convert_pdf_to_png(file_id, conversion_index=1)
                    for img in images:
                        img_size = self.get_image_size(img)
                        processed_img = self.process_image_if_needed(img, img_size)
                        all_images.append((processed_img, self.get_image_size(processed_img)))
                except Exception as e:
                    print(f"Erreur lors du traitement du fichier Drive {file_id}: {e}")

        # 2. Traitement des fichiers à télécharger
        if files_to_download:
            for file_url in files_to_download:
                try:
                    response = requests.get(file_url)
                    images = convert_pdf_to_images(io.BytesIO(response.content))
                    for img in images:
                        img_size = self.get_image_size(img)
                        processed_img = self.process_image_if_needed(img, img_size)
                        all_images.append((processed_img, self.get_image_size(processed_img)))
                except Exception as e:
                    print(f"Erreur lors du téléchargement {file_url}: {e}")

        # 3. Traitement des fichiers locaux
        if local_files:
            for file_path in local_files:
                try:
                    if not self.is_acceptable_local_file(file_path):
                        print(f"Type de fichier non supporté: {file_path}")
                        continue
                        
                    images = self.convert_local_file_to_images(file_path, conversion_index=1)
                    for img in images:
                        img_size = self.get_image_size(img)
                        processed_img = self.process_image_if_needed(img, img_size)
                        all_images.append((processed_img, self.get_image_size(processed_img)))
                except Exception as e:
                    print(f"Erreur lors du traitement du fichier local {file_path}: {e}")

        # Traitement par lots ou individuel
        all_responses = {}
        if method == 'batch':
            print(f"impression de batch....")
            image_batches = self.create_image_batches(all_images)
            for batch_index, batch in enumerate(image_batches):
                try:
                    batch_content = []
                    for img, _ in batch:
                        image = Image.open(img)
                        media_type = f"image/{image.format.lower()}"
                        content = prepare_image_content(img.getvalue(), media_type, text)
                        batch_content.extend(content)
                    #print(f"impression de batch_content:{batch_content}")
                    response = process_image_data(batch_content, tool_list, tool_choice)
                    #print(f"impression de response du batach:{response}")
                    if response:
                        _, _, _, data = self.new_extract_tool_use_data(response)
                        response_text = self.final_handle_responses(data)
                        all_responses[f"response_batch_{batch_index+1}"] = response_text
                except Exception as e:
                    print(f"Erreur lors du traitement du lot {batch_index}: {e}")
                    all_responses[f"error_batch_{batch_index+1}"] = str(e)

        elif method == 'image':
            for image_index, (img, _) in enumerate(all_images):
                try:
                    image = Image.open(img)
                    media_type = f"image/{image.format.lower()}"
                    content = prepare_image_content(img.getvalue(), media_type, text)
                    
                    response = process_image_data(content,tool_list, tool_choice)
                    if response:
                        _, _, _, data = self.new_extract_tool_use_data(response)
                        response_text = self.final_handle_responses(data)
                        all_responses[f"response_image_{image_index+1}"] = response_text
                except Exception as e:
                    print(f"Erreur lors du traitement de l'image {image_index}: {e}")
                    all_responses[f"error_image_{image_index+1}"] = str(e)

        else:
            raise ValueError("La méthode doit être 'batch' ou 'image'")

        return all_responses

    def get_image_size(self, img):
        """Détermine la taille d'une image."""
        if isinstance(img, io.BytesIO):
            return len(img.getvalue())
        elif isinstance(img, bytes):
            return len(img)
        raise TypeError(f"Type d'image non supporté: {type(img)}")

    def resize_image_if_needed(self,image_data, max_size_bytes=4*1024*1024, max_dimension=8000):
            if isinstance(image_data, io.BytesIO):
                initial_size = len(image_data.getvalue())
                image_data.seek(0)
            else:
                initial_size = len(image_data)
            
            img = Image.open(image_data)
            
            if initial_size <= max_size_bytes and img.width <= max_dimension and img.height <= max_dimension:
                print(f"Image size and dimensions are already within limits: {initial_size/1024/1024:.2f} MB, {img.width}x{img.height}")
                image_data.seek(0)
                return image_data

            original_format = img.format
            quality = 95
            format = original_format if original_format in ['JPEG', 'PNG'] else 'PNG'

            # Redimensionner l'image si elle dépasse les dimensions maximales
            if img.width > max_dimension or img.height > max_dimension:
                scale_factor = min(max_dimension / img.width, max_dimension / img.height)
                new_width = int(img.width * scale_factor)
                new_height = int(img.height * scale_factor)
                img = img.resize((new_width, new_height), Image.LANCZOS)

            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format=format, quality=quality)

            while len(img_byte_arr.getvalue()) > max_size_bytes:
                img_byte_arr.seek(0)
                img_byte_arr.truncate(0)
                if quality > 5:
                    quality -= 5
                elif img.size[0] > 100:
                    width, height = img.size
                    img = img.resize((int(width * 0.9), int(height * 0.9)), Image.LANCZOS)
                else:
                    format = 'JPEG'

                img.save(img_byte_arr, format=format, quality=quality)

            img_byte_arr.seek(0)
            print(f"Image size after resizing: {len(img_byte_arr.getvalue())/1024/1024:.2f} MB, {img.width}x{img.height}")
            return img_byte_arr

    def process_image_if_needed(self, img, img_size):
        """
        Traite l'image si nécessaire (redimensionnement, compression).
        """
        if img_size > 5 * 1024 * 1024:  # 5MB
            return self.resize_image_if_needed(img)
        return img

    def is_acceptable_local_file(self, file_path):
        """
        Vérifie si le type de fichier est accepté.
        """
        acceptable_extensions = ['.pdf', '.jpeg', '.jpg', '.png', '.gif', '.webp']
        _, ext = os.path.splitext(file_path)
        return ext.lower() in acceptable_extensions
    
    def convert_local_file_to_images(self,file_path, conversion_index=0):
            from pdf2image import convert_from_path
            import mimetypes
            
            # Déterminer le format de sortie en fonction de conversion_index
            output_format = 'PNG' if conversion_index == 0 else 'JPEG'
            
            # Déterminer le type MIME du fichier
            mime_type, _ = mimetypes.guess_type(file_path)
            
            if mime_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                print(f"le fichier est dans le format attendu pas besoin de transformation. {mime_type}")
                with open(file_path, 'rb') as f:
                    return [io.BytesIO(f.read())]
            
            elif mime_type == 'application/pdf':
                print(f"le fichier est en format pdf et doit etre converti en images {output_format}. {mime_type}")
                
                # Conversion du PDF en images
                pages = convert_from_path(file_path, fmt='png', dpi=200)
                
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

    def create_image_batches(self,images, max_batch_size=4*1024*1024, max_image_size=4*1024*1024):
            batches = []
            current_batch = []
            current_batch_size = 0

            for img, img_size in images:
                if img_size > max_image_size:
                    print(f"Image of size {img_size/1024/1024:.2f} MB exceeds maximum allowed size of {max_image_size/1024/1024:.2f} MB. Skipping this image.")
                    continue
                if current_batch_size + img_size > max_batch_size:
                    batches.append(current_batch)
                    current_batch = []
                    current_batch_size = 0
                current_batch.append((img, img_size))
                current_batch_size += img_size

            if current_batch:
                batches.append(current_batch)

            return batches

        


    def antho_agent(self, content, model_index=None,antho_tools=None, tool_mapping=None, verbose=True, tool_choice=None,max_tokens=1024,stream=False,model_name=None,raw_output=False,thinking=False):
        
        print(f"impression de model_inde:{model_index} impression de model_Name:{model_name} dans Antho_agent")
        if tool_choice is None:
            tool_choice = {'type': 'auto'}

        responses = []

        # Vérifier si tool_mapping est fourni et non None
        mapped_tools = []
        tool_names = []
        if tool_mapping:
            if isinstance(tool_mapping, dict):
                tool_mapping = [tool_mapping]

            for tool_dict in tool_mapping:
                for tool_name, function_or_none in tool_dict.items():
                    tool_info = next((tool for tool in antho_tools if 'name' in tool and tool['name'] == tool_name), None)
                    if not tool_info:
                        print(f"Aucun outil correspondant trouvé pour {tool_name}.")
                        continue
                    
                    mapped_tools.append(tool_info)
                    
            tool_names = [tool['name'] for tool in mapped_tools] if mapped_tools else []

        # Appel de la méthode anthro_send_message_tool avec les paramètres préparés
        tool_setup = self.anthro_send_message_tool(content=content, model_index=model_index,model_name=model_name,stream=stream, tool_list=antho_tools, tool_name=tool_names, tool_choice=tool_choice,max_tokens=max_tokens,thinking=thinking)
        # Ajoutez ici toute autre logique nécessaire pour le traitement de tool_setup
        #response_data = {}
        #print(f"impression brut:{tool_setup}")

        mess, tool_id, used_tool_name, response_text = self.new_extract_tool_use_data(tool_setup, verbose)
        #print(f"impression de mess:{mess}\n impression de tools_id:{tool_id}\n impression de response data:{response_text}\n impression de used_tool_name:{used_tool_name}")
        
        accumulated_message = ""

        if mess is not None:
            accumulated_message += f"Réponse de l'outil {used_tool_name}: {mess}"

        if response_text is not None:
            accumulated_message += f"\nRéponse textuelle: {response_text}"
            
        
        if accumulated_message.strip():
            #print(f"impression du message de la réponse de ia:{accumulated_message}")
            self.add_ai_message(accumulated_message.strip())  # Enlever les espaces en trop à la fin

        if isinstance(mess, dict) and used_tool_name is not None:

            # Traiter le cas où mess est un dictionnaire
            print('Dictionnnaire Outil simple détecté.....')

            used_tool_info = next((tool for tool in mapped_tools if tool['name'] == used_tool_name), None)  # Trouver l'outil correspondant dans mapped_tools
            #print(f"impression de used_tool_info:{used_tool_info}\n impression de tool_name:{used_tool_name}")
            
            if used_tool_info:
                required_fields = used_tool_info['input_schema']['properties'].keys()
                func_args = {field: mess[field] for field in required_fields if field in mess}
                    
                #print(f"{Fore.MAGENTA}impression des required_fields:{required_fields}\n impression des func_args:{func_args}{Style.RESET_ALL}")
                response_data = {"tool_output": {"content": func_args}}
                
                function_or_none = None
                for tool_dict in tool_mapping:
                    if used_tool_name in tool_dict:
                        function_or_none = tool_dict[used_tool_name]
                        break

                #print(f"impression de function_or_none:{function_or_none}")
                if callable(function_or_none):
                    BLUE_LIGHT = "\033[94m"
                    RESET_COLOR = "\033[0m"
                    print(f"{BLUE_LIGHT}Fonction Call_back enclenché{RESET_COLOR}")
                    search_results = function_or_none(**func_args)

                    response_data["tool_output"] = {
                        "tool_use_id": tool_id,
                        "tool_name": used_tool_name,
                        "content": search_results
                    }
                    #print(f"impression de tool_output data:{response_data}")
                else:
                    # Si aucune fonction n'est trouvée, inclure tout de même le nom de l'outil dans l'output
                    print(f"Aucune fonction correspondante trouvée pour l'outil {used_tool_name}")
                    response_data = {
                        "tool_output": {
                            "tool_use_id": tool_id,
                            "tool_name": used_tool_name,  # Inclure le nom de l'outil
                            "content": mess  # Retourner le message tel quel
                        }
                    }
                    print(f"impression de tool_output data sans fonction correspondante: {response_data}")
            else:
                print(f"Aucune information trouvée pour l'outil {used_tool_name}")

            responses.append(response_data)
            
        elif isinstance(mess, list) and all(isinstance(item, dict) for item in mess) and isinstance(used_tool_name, list) and len(mess) == len(used_tool_name):
            responses = []
            print('Dictionnnaire Outil multiple détectés.....')
            # Traiter le cas où mess est une liste de dictionnaires
            for single_mess, single_tool_name in zip(mess, used_tool_name):
                print('Outil détecté.....')

                used_tool_info = next((tool for tool in mapped_tools if tool['name'] == single_tool_name), None)  # Trouver l'outil correspondant dans mapped_tools
                #print(f"impression de used_tool_info:{used_tool_info}\n impression de tool_name:{single_tool_name}")
                
                if used_tool_info:
                    required_fields = used_tool_info['input_schema']['properties'].keys()
                    func_args = {field: single_mess[field] for field in required_fields if field in single_mess}
                        
                    #print(f"{Fore.MAGENTA}impression des required_fields:{required_fields}\n impression des func_args:{func_args}{Style.RESET_ALL}")
                    response_data = {"tool_output": {"content": func_args}}
                    
                    function_or_none = None
                    for tool_dict in tool_mapping:
                        if single_tool_name in tool_dict:
                            function_or_none = tool_dict[single_tool_name]
                            break

                    #print(f"impression de function_or_none:{function_or_none}")
                    if callable(function_or_none):
                        BLUE_LIGHT = "\033[94m"
                        RESET_COLOR = "\033[0m"
                        print(f"{BLUE_LIGHT}Fonction Call_back enclenché{RESET_COLOR}")
                        search_results = function_or_none(**func_args)

                        response_data["tool_output"] = {
                            "tool_use_id": tool_id,
                            "tool_name": single_tool_name,
                            "content": search_results
                        }
                        #print(f"impression de tool_output data:{response_data}")
                    else:
                        print(f"Aucune fonction correspondante trouvée pour l'outil {single_tool_name}")
                else:
                    print(f"Aucune information trouvée pour l'outil {single_tool_name}")

                responses.append(response_data)

        else:
            func_args = {}  # ou gérer le cas où mess n'est ni un dictionnaire ni une liste de dictionnaires

        if response_text:
            responses.append(response_text)

        # Traiter toutes les réponses consolidées
        for response in responses:
            self.process_output(response)

        # Retourner toutes les réponses consolidées
        #print(f"impression de response final dans antho_agent:{responses}")

        #Extract_content
        if not raw_output:
            data=self.final_handle_responses(responses)
        else:
            data=responses
        
        return data

    def extract_from_message(self, response):
        if hasattr(response, 'content'):
            for block in response.content:
                if hasattr(block, 'text') and block.type == 'text':
                    return block.text
        return "No valid text found in the response."

    def correct_double_quotes(self,json_str):
        # Remplace les guillemets doubles consécutifs par des apostrophes
        corrected_str = re.sub(r'""', '"', json_str)  # Corrige les guillemets doubles consécutifs
        corrected_str = re.sub(r'"(.*?)"', r"'\1'", corrected_str)  # Remet les guillemets simples
        return corrected_str

    def add_double_quotes_to_keys(self, json_str):
        # Ajoute des guillemets doubles aux clés sans remplacer les guillemets simples dans les valeurs
        # Trouver les clés qui ne sont pas entourées par des guillemets simples ou doubles
        key_pattern = r"(?<=\s|{|,)([a-zA-Z0-9_]+)(?=\s*:)"
        # Ajouter des guillemets doubles aux clés
        corrected_str = re.sub(key_pattern, r'"\1"', json_str)
        
        return corrected_str

    def extract_blocks_recursively(self, text_block):
        print(f"{Fore.MAGENTA}{Style.BRIGHT}UTILISATION DE METHODE EXTRACT_BLOCKS_RECURSIVELY - SURVEILLEZ LA FREQUENCE DE SON UTILISATION{Style.RESET_ALL}")
        extracted_inputs = []
        extracted_ids = []
        extracted_names = []
        response_data = {}

        # Prétraitement de la chaîne pour supprimer les caractères d'échappement supplémentaires
        text_block = re.sub(r"\\+(')", r"\1", text_block)
        text_block = re.sub(r"\\+", r"\\", text_block)
        #print(f"Après prétraitement : {text_block}")

        nested_blocks = re.findall(r'\[(.*?)\]', text_block)
        for nested_block_str in nested_blocks:
            if 'TextBlock' in nested_block_str:
                match_text_block = re.search(r'TextBlock\(text=[\'"](.*?)[\'"], type=[\'"](.*?)[\'"]', nested_block_str)
                if match_text_block:
                    text_content = match_text_block.group(1).strip("'\"")
                    text_content = re.sub(r'\\n', '', text_content)
                    response_data["text_output"] = {"content": {"answer_text": text_content.strip(), "thinking_text": ""}}
                    
                    print(f"Bloc TextBlock trouvé : {text_content}")
                    
                    # Appel récursif pour extraire les blocs imbriqués dans le TextBlock
                    nested_inputs, nested_ids, nested_names, nested_response_data = self.extract_blocks_recursively(text_content)
                    extracted_inputs.extend(nested_inputs)
                    extracted_ids.extend(nested_ids)
                    extracted_names.extend(nested_names)
                    response_data.update(nested_response_data)

            if 'ToolUseBlock' in nested_block_str:
                match_tool_use_block = re.search(r'ToolUseBlock\(id=[\'"](.*?)[\'"], input=({.*?}), name=[\'"](.*?)[\'"]', nested_block_str)
                if match_tool_use_block:
                    tool_id = match_tool_use_block.group(1)
                    tool_input_str = match_tool_use_block.group(2)
                    tool_name = match_tool_use_block.group(3)
                    print(f"Bloc ToolUseBlock trouvé : id={tool_id}, input={tool_input_str}, name={tool_name}")
                    tool_input_str = re.sub(r"'+", '"', tool_input_str)
                    tool_input_str = re.sub(r'"([^"]*)"', lambda m: '"{}"'.format(m.group(1).replace('"', '\\"').replace('\\', '\\\\')), tool_input_str)
                    try:
                        tool_input = json.loads(tool_input_str)
                        extracted_inputs.append(tool_input)
                        extracted_ids.append(tool_id)
                        extracted_names.append(tool_name)
                    except json.JSONDecodeError as e:
                        print(f"Erreur lors de la conversion de la chaîne JSON : {e}")

        return extracted_inputs, extracted_ids, extracted_names, response_data

    def new_extract_tool_use_data(self, response, verbose=True):
        extracted_inputs = []
        extracted_ids = []
        extracted_names = []
        response_data = {}
        #print(f"impression de au niveau d'extract tool:{response}")
        if hasattr(response, 'content'):
            for block in response.content:
                #print(f"Analyse du bloc : type={type(block)}, attributs={dir(block)}")
                if hasattr(block, 'input') and hasattr(block, 'id') or hasattr(block, 'name'):
                    tool_input = getattr(block, 'input', None)
                    tool_id = block.id
                    tool_name = block.name
                    #print(f"Extraction réussie - ID: {tool_id}, Nom: {tool_name}, Input: {tool_input}")
                    
                
                    if isinstance(tool_input, dict):
                        #print(f"la réponse est considéré ocmme un dict.....")
                        extracted_inputs.append(tool_input)
                        extracted_ids.append(tool_id)
                        extracted_names.append(tool_name)
                         # Impression après ajout
                        #print(f"Liste des inputs après ajout : {extracted_inputs}")
                        #print(f"Liste des IDs après ajout : {extracted_ids}")
                        #print(f"Liste des noms après ajout : {extracted_names}")

                    else:
                        print(f"Le bloc ne dispose pas des attributs nécessaires et a été ignoré.")
                        try:
                            tool_input_dict = json.loads(tool_input)
                            extracted_inputs.append(tool_input_dict)
                            extracted_ids.append(tool_id)
                            extracted_names.append(tool_name)
                        except json.JSONDecodeError as e:
                            print(f"Erreur lors de la conversion de la chaîne JSON : {e}")
                            continue

                if hasattr(block, 'text') and hasattr(block, 'type') and block.type == 'text':
                    text_block = block.text
                    response_data["text_output"] = text_block
                    #print(f"impression de text_block:{text_block}")
                    if verbose:
                        thinking_text = ""
                        answer_text = text_block
                        if '<thinking>' in text_block and '</thinking>' in text_block:
                            thinking_text = text_block.split('<thinking>')[1].split('</thinking>')[0].strip()
                            answer_text = text_block.split('</thinking>')[1].strip()

                        if thinking_text:
                            print(f"{Fore.GREEN}Thinking: {thinking_text}")
                        if answer_text:
                            pass
                            #print(f"{Fore.BLUE}Answer: {answer_text}")

                        response_data["text_output"] = {"content": {"answer_text": answer_text, "thinking_text": thinking_text}}
                        #print(f"impression de response_data2:{response_data}")
                    
                    #print(f"impression de text_block dans new_extract_tool_use_data:{text_block}")
                    # Appel de la fonction récursive pour extraire les blocs imbriqués
                    nested_inputs, nested_ids, nested_names, nested_response_data = self.extract_blocks_recursively(text_block)
                    extracted_inputs.extend(nested_inputs)
                    extracted_ids.extend(nested_ids)
                    extracted_names.extend(nested_names)
                    response_data.update(nested_response_data)

        if len(extracted_inputs) == 1:
            return extracted_inputs[0], extracted_ids[0], extracted_names[0], response_data
        elif len(extracted_inputs) > 1:
            print(f"impression sortie multiples")
            return extracted_inputs, extracted_ids, extracted_names, response_data
        else:
            return {}, None, None, response_data

    def extract_tool_use_data(self, response,verbose=True):
        """
        Extrait les données de l'outil utilisé (tool_use) à partir d'une réponse, en gérant différents formats.

        Args:
            response: L'objet contenant les données de réponse, qui doit avoir un attribut 'content'.

        Returns:
            tuple: Retourne un tuple avec le dictionnaire 'input' et l'ID du 'ToolUseBlock' s'il y a un seul bloc,
                ou deux listes séparées pour les 'input' et les 'id' s'il y a plusieurs blocs.
        """

        extracted_inputs = []
        extracted_ids = []
        extracted_names = []
        response_data = {}
        #print(f"impression de au niveau d'extract tool:{response}")
        if hasattr(response, 'content'):
            for block in response.content:
                if hasattr(block, 'input') and hasattr(block, 'id') or hasattr(block, 'name'):
                    tool_input = getattr(block, 'input', None)
                    tool_id = block.id
                    tool_name = block.name
                    if isinstance(tool_input, dict):
                        extracted_inputs.append(tool_input)
                        extracted_ids.append(tool_id)
                        extracted_names.append(tool_name)
                    else:
                        try:
                            tool_input_dict = json.loads(tool_input)
                            extracted_inputs.append(tool_input_dict)
                            extracted_ids.append(tool_id)
                            extracted_names.append(tool_name)
                        except json.JSONDecodeError as e:
                            print(f"Erreur lors de la conversion de la chaîne JSON : {e}")
                            continue
                    
                    

                if hasattr(block, 'text') and hasattr(block, 'type') and block.type == 'text':
                    text_block = block.text
                    response_data["text_output"] = text_block
                    print(f"impression de text_block:{text_block}")
                    
                    
                    if verbose:
                        thinking_text = ""
                        answer_text = text_block
                        if '<thinking>' in text_block and '</thinking>' in text_block:
                            thinking_text = text_block.split('<thinking>')[1].split('</thinking>')[0].strip()
                            answer_text = text_block.split('</thinking>')[1].strip()

                        if thinking_text:
                            pass
                            #print(f"{Fore.GREEN}Thinking: {thinking_text}")
                        if answer_text:
                            pass
                            #print(f"{Fore.BLUE}Answer: {answer_text}")
                        
                        response_data["text_output"]={"content":{"answer_text":answer_text,"thinking_text":thinking_text}}
                        #print(f"impression de response_data2:{response_data}")

                    #r"\[(?P<tool_use_block>ToolUseBlock\(.*?\))\]"
                    pattern_text_block = r'\[TextBlock\(text=(?P<text>.*?), type=\'text\'\)\]'
                    match_text_block = re.search(pattern_text_block, text_block, re.DOTALL)
                    
                    if match_text_block:
                        text_content = match_text_block.group('text').strip("'\"")
                        text_content = re.sub(r'\\n', '', text_content)
                        response_data["text_output"] = {"content": {"answer_text": text_content.strip(), "thinking_text": thinking_text}}

                    pattern_tool_use_block = r'\[ToolUseBlock\(.*?\)\]'
                    match_tool_use_block = re.search(pattern_tool_use_block, text_block)
                    
                    if match_tool_use_block:
                        tool_use_block_str = match_tool_use_block.group()
                        input_pattern = r"ToolUseBlock\(id=(?P<tool_id>[^,]+),\s*input=(?P<tool_input>{[^}]+})"
                        input_match = re.search(input_pattern, tool_use_block_str, re.DOTALL)

                    if input_match:
                        tool_id = input_match.group("tool_id").strip("'\"")
                        tool_input_str = input_match.group("tool_input")
                        #print(f"impression de tool_input_str:{tool_input_str}")
                        tool_input_str = tool_input_str.replace("'", '"')
                        tool_input_str = re.sub(r'"([^"]*)"', lambda m: '"{}"'.format(m.group(1).replace('"', '\\"').replace('\\', '\\\\')), tool_input_str)
                        #print(f"impression de tool_input_str après correction:{tool_input_str}")
                        try:
                            tool_input = json.loads(tool_input_str)
                            extracted_inputs.append(tool_input)
                            extracted_ids.append(tool_id)
                        except json.JSONDecodeError as e:
                            print(f"Erreur lors de la conversion de la chaîne JSON : {e}")

                    cleaned_text = re.sub(pattern_tool_use_block, '', text_block)
                    cleaned_text = cleaned_text.strip()
                    response_data["text_output"] = {"content": {"answer_text": cleaned_text, "thinking_text": thinking_text}}
    
        if len(extracted_inputs) == 1:
            return extracted_inputs[0], extracted_ids[0], extracted_names[0], response_data
        elif len(extracted_inputs) > 1:
            return extracted_inputs, extracted_ids, extracted_names, response_data
        else:
            return {}, None, None, response_data

    def parse_input(self, input_str):
        """ Parse the input string into a dictionary using ast.literal_eval. """
        try:
            return ast.literal_eval(input_str)
        except (ValueError, SyntaxError) as e:
            print(f"Error evaluating tool input from text block: {e}")
            print("Failed input string:", input_str)
            return None


    def get_last_n_messages(self, n):
        if isinstance(self.chat_history, list):
            last_messages = []
            count = 0  # Compteur pour les échanges récupérés

            # Parcourt l'historique à l'envers pour récupérer les derniers échanges
            for message in reversed(self.chat_history):
                if ("Human: " in message or "Assistant: " in message) and count < n:
                    # Pour les messages de l'assistant, nettoie le format ContentBlock
                    if "Assistant: " in message:
                        start = message.find("text='") + 6  # Trouve le début du texte
                        end = message.find("', type='text")  # Trouve la fin du texte
                        clean_message = "Assistant: " + message[start:end]
                        last_messages.insert(0, clean_message)  # Ajoute le message nettoyé
                    else:
                        last_messages.insert(0, message)  # Ajoute le message tel quel pour l'humain
                    count += 1
                if count == n:
                    break  # Arrête la boucle une fois les n échanges récupérés

            # Affiche les derniers échanges
            for message in last_messages:
                print(message)
        else:
            print("chat_history n'est pas une liste ou est inaccessible.")

    def create_prompt(self):
        prompt = self.system_prompt + ''.join(self.chat_history)
        if not prompt.endswith("\n\nAssistant:"):
            prompt += "\n\nAssistant:"
        return prompt


    

    def format_chat_history(self):
        # Formate l'historique des chats pour une présentation claire
        formatted_history = []
        for entry in self.chat_history:
            formatted_history.append(f"Role: {entry['role']}\nContent: {entry['content']}\n")
        return "\n".join(formatted_history)

    def process_output(self, output):
        
        message = ""
        #print(f"impression du message a l'entrée de ia:{output}")
        
        # Vérifier si l'output provient d'un outil
        if 'tool_output' in output:
            tool_output = output['tool_output']
            
            if 'content' in tool_output:
                if isinstance(tool_output['content'], dict):
                    # Gérer le cas où 'content' est un dictionnaire
                    for key, value in tool_output['content'].items():
                        message += f"\n{key}: {value}"
                elif hasattr(tool_output['content'], 'content'):
                    if isinstance(tool_output['content'].content, list):
                        tool_message = tool_output['content'].content[0].text
                        role = 'tool_use'
                        message += f"\n\n{role}: {tool_message}"

                    
            # Capturer les valeurs des tokens
            if 'usage' in tool_output:
                self.total_input_tokens += tool_output['usage']['input_tokens']
                self.total_output_tokens += tool_output['usage']['output_tokens']

        # Vérifier si l'output est un texte de réponse
        elif 'text_output' in output:
            text_output = output['text_output']
            if 'content' in text_output and isinstance(text_output['content'], dict):
                text_message = text_output['content']['answer_text']
                thinking_text = text_output['content'].get('thinking_text', '')
                role = 'assistant'
                if thinking_text:
                    message += f"\n\n{role}: {text_message}\n\nThinking text:\n\n{thinking_text}\n\nEnd of thinking text\n\n"
                else:
                    message += f"\n\n{role}: {text_message}"

                # Capturer les valeurs des tokens si disponibles
                if 'usage' in text_output:
                    self.total_input_tokens += text_output['usage']['input_tokens']
                    self.total_output_tokens += text_output['usage']['output_tokens']

        
        return message


    def anthro_send_message_tool(self, content,stream,model_name=None,model_index=None, tool_list=None, tool_name=None,tool_choice={"type": "auto"},max_tokens=1024,thinking=False):
        if not isinstance(model_index, int):
            chosen_model=model_name
        else:
            chosen_model = self.models[model_index]
        
        if tool_list is None or tool_name is None:
            # Si tool_list ou tool_name n'est pas renseigné, appelle une fonction alternative
            #print(f"Impression de model_index:{model_index} impression de model_name{model_name} avant anthropic_send_message")
            
            return self.anthropic_send_message(content=content,model_name=model_name, model_index=model_index,thinking=thinking)
        else:
            # Sinon, continue avec la logique existante pour utiliser l'outil spécifié
            
            
            self.add_user_message(content)  # Assurez-vous que cette méthode ajoute le rôle "user"
            
            messages = []
            for msg in self.chat_history:
                messages.append({"role": msg["role"], "content": msg["content"]})
            #print(f"impression de tool_name:{tool_name}")
            if isinstance(tool_name, str):
                # Si tool_name est une chaîne, la convertir en liste d'un seul élément
                tool_name = [tool_name]
            #print(f"impression de tool_name apres transformation:{tool_name}")
            tools = [self.find_tool_by_name(tool_list, name) for name in tool_name]
            #print(f"impression de valeur tools avant la porte:{tools}\n\n\n impression de tool_list:{tool_list}")
            
            #print(f"impression du message systeme:{self.system_prompt}")
            #print(f"impression de messages:{messages}")
            response = self.client.messages.create(
                model=chosen_model,
                max_tokens=max_tokens,
                tools=tools,
                messages=messages,
                tool_choice=tool_choice,
                system=self.system_prompt,
                stream=stream
            )
            print(f"impression the response brut de tool:{response}")
            self.update_token_usage(response)
            ai_response = response.content
            #self.add_ai_message(ai_response)  # Assurez-vous que cette méthode ajoute le rôle "assistant"
            
            return response

    def find_tool_by_name(self, tools_list, tool_name):
        """
        Recherche un outil spécifique par son nom dans une liste de dictionnaires,
        et gère le cas où tool_name est un dictionnaire, une chaîne ou une liste.

        Args:
            tools_list (list): La liste des dictionnaires représentant les outils.
            tool_name (str, list, dict): Le nom de l'outil à rechercher, ou le dictionnaire
                                        représentant l'outil, ou une liste de noms d'outils.

        Returns:
            list or dict or None: Une liste des dictionnaires d'outils trouvés, ou un seul dictionnaire
                                si un seul outil est trouvé, ou None si aucun outil n'est trouvé.
        """
        # Si tool_name est un dictionnaire, retourne-le directement
        if isinstance(tool_name, dict):
            return tool_name if tool_name in tools_list else None
        
        # Si tool_name est une liste, recherche tous les outils avec les noms donnés
        if isinstance(tool_name, list):
            return [tool for tool in tools_list if tool.get('name') in tool_name] or None

        # Si tool_name est une chaîne, recherche un outil avec ce nom
        if isinstance(tool_name, str):
            for tool in tools_list:
                if tool.get('name') == tool_name:
                    return tool

        # Aucun outil trouvé
        print(f"Aucun outil trouvé avec le nom: {tool_name}")
        return None

    async def anthropic_send_message_tool_stream(self, content, model_index, tool_list=None, tool_mapping=None, tool_name=None, verbose=True, tool_choice={"type": "any"}, max_tokens=1024):
        """
        Envoie un message en streaming avec gestion des outils et callbacks.
        """
        
        chosen_model = self.models[model_index]
        print(f"Modèle choisi : {chosen_model}")
        
        # Vérifier si tool_mapping est fourni et non None
        mapped_tools = []
        
        if tool_mapping:
            if isinstance(tool_mapping, dict):
                tool_mapping = [tool_mapping]

            for tool_dict in tool_mapping:
                for tool_name, function_or_none in tool_dict.items():
                    tool_info = next((tool for tool in tool_list if 'name' in tool and tool['name'] == tool_name), None)
                    if not tool_info:
                        print(f"Aucun outil correspondant trouvé pour {tool_name}.")
                        continue
                    mapped_tools.append(tool_info)
            
           

        content = content.strip()
        self.add_user_message(content)
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": msg["content"]} 
                    for i, msg in enumerate(self.chat_history)]

        try:
            accumulated_response = ""
            accumulated_json = ""
            tool_responses = []
            current_tool_name = None
            tool_id = None

            async with self.client_stream.messages.stream(
                model=chosen_model,
                messages=messages,
                max_tokens=max_tokens,
                system=self.system_prompt,
                tools=tool_list,
                tool_choice=tool_choice,
                
            ) as stream:
                async for chunk in stream:
                    if chunk.type == "content_block_start" and chunk.content_block.type == "tool_use":
                        current_tool_name = chunk.content_block.name
                        tool_id = chunk.content_block.id
                        
                    elif chunk.type == "content_block_delta":
                        if chunk.delta.type == "text_delta":
                            text = chunk.delta.text
                            accumulated_response += text
                            yield text
                        elif chunk.delta.type == "input_json_delta":
                            json_part = chunk.delta.partial_json
                            accumulated_json += json_part
                            yield f"\nTool Input: {json_part}"

                    elif chunk.type == "content_block_stop" and current_tool_name:
                        # Traiter le JSON accumulé quand le bloc d'outil est terminé
                        try:
                            tool_data = json.loads(accumulated_json)
                            
                            # Chercher la fonction de callback correspondante
                            function_or_none = None
                            for tool_dict in tool_mapping or []:
                                if current_tool_name in tool_dict:
                                    function_or_none = tool_dict[current_tool_name]
                                    break
                            
                            if callable(function_or_none):
                                print(f"\033[94mFonction Call_back enclenché pour {current_tool_name}\033[0m")
                                # Extraire les arguments requis
                                tool_info = next((tool for tool in mapped_tools if tool['name'] == current_tool_name), None)
                                if tool_info:
                                    required_fields = tool_info['input_schema']['properties'].keys()
                                    func_args = {field: tool_data[field] for field in required_fields if field in tool_data}
                                    
                                    # Si la fonction est synchrone, l'exécuter dans un thread
                                    if not asyncio.iscoroutinefunction(function_or_none):
                                        loop = asyncio.get_event_loop()
                                        result = await loop.run_in_executor(None, lambda: function_or_none(**func_args))
                                    else:
                                        result = await function_or_none(**func_args)
                                    
                                    tool_responses.append({
                                        "tool_output": {
                                            "tool_use_id": tool_id,
                                            "tool_name": current_tool_name,
                                            "content": result
                                        }
                                    })
                                    
                                    yield f"\nTool use: {current_tool_name}: {result}"
                            
                            accumulated_json = ""  # Réinitialiser pour le prochain outil
                            current_tool_name = None
                            tool_id = None
                            
                        except json.JSONDecodeError:
                            print(f"Erreur lors du décodage JSON: {accumulated_json}")
                            
            self.add_ai_message(accumulated_response)
            
            # Traiter toutes les réponses d'outils
            for response in tool_responses:
                self.process_output(response)
                
            yield {"type": "complete", "tool_responses": tool_responses}

        except Exception as e:
            print(f"Erreur lors du streaming : {e}")
            yield f"Erreur: {str(e)}"
        

    async def anthropic_send_message_stream(self, content, model_index, max_tokens=1024):
        """
        Envoie un message en utilisant un des modèles spécifiés par index, en mode streaming.

        Args:
            content (str): Le contenu du message à envoyer.
            model_index (int): L'index du modèle à utiliser pour envoyer le message.
            max_tokens (int): Le nombre maximum de tokens pour la réponse.

        Yields:
            str: Chaque fragment de texte généré en mode streaming.
        """
        
        
        chosen_model = self.models[model_index]
        print(f"Modèle choisi : {chosen_model}")
        content = content.strip()
        # Ajouter le message de l'utilisateur à l'historique
        self.add_user_message(content)
        
        # Préparation des messages pour l'API
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": msg["content"]} for i, msg in enumerate(self.chat_history)]
        
        try:
            # Utiliser un flux de messages pour le streaming
            accumulated_response = ""
            with self.client.messages.stream(
                model=chosen_model,
                messages=messages,
                max_tokens=max_tokens,
                system=self.system_prompt
            ) as stream:
                for text in stream.text_stream:
                    accumulated_response += text  # Accumule le texte
                    yield text  # Émet chaque fragment de texte en continu

            # Ajoute la réponse complète à l'historique après le streaming
            self.add_ai_message(accumulated_response)

        except Exception as e:
            print(f"Erreur lors de l'envoi du message en streaming : {e}")

    async def anthropic_send_message_tool_streaming(self, content, model_index=None, model_name=None, 
                                                     tools=None, tool_mapping=None, tool_choice=None, max_tokens=1024):
        """
        Envoie un message avec streaming et support d'outils depuis l'API Anthropic.
        Retourne un format uniforme pour l'intégration avec BaseAIAgent.
        
        Args:
            content (str): Le contenu du message
            model_index (int, optional): Index du modèle à utiliser
            model_name (str, optional): Nom spécifique du modèle
            tools (list): Liste des outils disponibles
            tool_mapping (list[dict]): Mapping des outils vers leurs fonctions
            tool_choice (dict): Configuration du choix d'outil {"type": "auto" | "any" | "tool", "name": str}
            max_tokens (int): Nombre maximum de tokens pour la réponse
            
        Yields:
            Dict[str, Any]: Chunks de réponse au format uniforme:
                {
                    "type": "text" | "tool_use" | "tool_result" | "final",
                    "content": str,           # Pour le texte
                    "tool_name": str,         # Pour l'utilisation d'outil
                    "tool_input": dict,       # Arguments de l'outil
                    "tool_output": any,       # Résultat de l'outil
                    "is_final": bool,
                    "model": str
                }
        """
        try:
            # Déterminer le modèle
            if model_name:
                chosen_model = model_name
            elif model_index is not None:
                chosen_model = self.models[model_index]
            else:
                chosen_model = self.models[0]
            
            # Configuration par défaut du tool_choice
            if tool_choice is None:
                tool_choice = {"type": "auto"}
            
            print(f"Envoi streaming avec tools vers Anthropic - modèle: {chosen_model}")
            
            # Ajouter le message utilisateur
            self.add_user_message(content)
            messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": msg["content"]} 
                       for i, msg in enumerate(self.chat_history)]
            
            # YIELD IMMÉDIAT
            yield {
                "type": "status",
                "content": "",
                "is_final": False,
                "model": chosen_model,
                "status": "initializing"
            }
            
            try:
                print(f"🔵 Début du streaming Anthropic avec tools...")
                
                # Yield pour la connexion
                yield {
                    "type": "status",
                    "content": "",
                    "is_final": False,
                    "model": chosen_model,
                    "status": "connecting"
                }
                
                # Variables pour accumuler les données
                accumulated_text = ""
                accumulated_json = ""
                current_tool_name = None
                current_tool_id = None
                tool_results = []
                
                # Créer le stream avec tools
                async with self.client_stream.messages.stream(
                    model=chosen_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=self.system_prompt,
                    tools=tools,
                    tool_choice=tool_choice
                ) as stream:
                    async for chunk in stream:
                        # Début d'un bloc de contenu
                        if chunk.type == "content_block_start":
                            if chunk.content_block.type == "tool_use":
                                current_tool_name = chunk.content_block.name
                                current_tool_id = chunk.content_block.id
                                print(f"🔵 Début utilisation outil: {current_tool_name}")
                        
                        # Delta de contenu
                        elif chunk.type == "content_block_delta":
                            if chunk.delta.type == "text_delta":
                                text = chunk.delta.text
                                accumulated_text += text
                                yield {
                                    "type": "text",
                                    "content": text,
                                    "is_final": False,
                                    "model": chosen_model
                                }
                                await asyncio.sleep(0)
                            
                            elif chunk.delta.type == "input_json_delta":
                                json_part = chunk.delta.partial_json
                                accumulated_json += json_part
                        
                        # Fin d'un bloc de contenu
                        elif chunk.type == "content_block_stop" and current_tool_name:
                            try:
                                tool_input = json.loads(accumulated_json)
                                
                                # Yield pour indiquer l'utilisation d'outil
                                yield {
                                    "type": "tool_use",
                                    "tool_name": current_tool_name,
                                    "tool_id": current_tool_id,
                                    "tool_input": tool_input,
                                    "is_final": False,
                                    "model": chosen_model
                                }
                                
                                # Exécuter la fonction si mapping fourni
                                tool_output = None
                                if tool_mapping:
                                    function_or_none = None
                                    for tool_dict in tool_mapping:
                                        if current_tool_name in tool_dict:
                                            function_or_none = tool_dict[current_tool_name]
                                            break
                                    
                                    if callable(function_or_none):
                                        print(f"🔵 Exécution de la fonction {current_tool_name}")
                                        # Exécution async ou sync
                                        if asyncio.iscoroutinefunction(function_or_none):
                                            tool_output = await function_or_none(**tool_input)
                                        else:
                                            loop = asyncio.get_event_loop()
                                            tool_output = await loop.run_in_executor(None, lambda: function_or_none(**tool_input))
                                        
                                        # Yield du résultat
                                        yield {
                                            "type": "tool_result",
                                            "tool_name": current_tool_name,
                                            "tool_id": current_tool_id,
                                            "tool_output": tool_output,
                                            "is_final": False,
                                            "model": chosen_model
                                        }
                                        
                                        tool_results.append({
                                            "tool_name": current_tool_name,
                                            "tool_id": current_tool_id,
                                            "input": tool_input,
                                            "output": tool_output
                                        })
                                
                            except json.JSONDecodeError as e:
                                print(f"🔴 Erreur décodage JSON tool: {e}")
                            
                            # Réinitialiser pour le prochain outil
                            accumulated_json = ""
                            current_tool_name = None
                            current_tool_id = None
                
                print(f"🔵 Streaming terminé")
                
                # Ajouter à l'historique
                if accumulated_text:
                    self.add_ai_message(accumulated_text)
                
                # Signal de fin
                yield {
                    "type": "final",
                    "content": accumulated_text,
                    "tool_results": tool_results,
                    "is_final": True,
                    "model": chosen_model
                }
                
            except asyncio.CancelledError:
                print(f"🔴 Streaming Anthropic tools annulé")
                raise
            except Exception as stream_error:
                print(f"🔴 Erreur streaming Anthropic tools: {stream_error}")
                import traceback
                traceback.print_exc()
                yield {
                    "type": "error",
                    "content": f"Erreur streaming: {str(stream_error)}",
                    "is_final": True,
                    "error": str(stream_error),
                    "model": chosen_model
                }
        
        except Exception as e:
            print(f"Erreur streaming Anthropic tools: {e}")
            yield {
                "type": "error",
                "content": f"Erreur: {str(e)}",
                "is_final": True,
                "error": str(e)
            }

    async def anthropic_send_message_streaming(self, content, model_index=None, model_name=None, max_tokens=1024):
        """Envoie un message avec streaming réel depuis l'API Anthropic."""
        try:
            # Déterminer le modèle
            if model_name:
                chosen_model = model_name
            elif model_index is not None:
                chosen_model = self.models[model_index]
            else:
                chosen_model = self.models[0]  # Modèle par défaut
            
            print(f"Envoi streaming vers Anthropic avec modèle: {chosen_model}")
            print(f"System prompt: {self.system_prompt}")
            
            # Préparer les messages
            print(f"🔵 Chat history: {self.chat_history}")
            print(f"🔵 Chat history length: {len(self.chat_history)}")
            messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": msg["content"]} for i, msg in enumerate(self.chat_history)]
            print(f"🔵 Messages préparés: {messages}")
            
            # Utiliser le vrai streaming d'Anthropic avec async
            print(f"🔵 Début du streaming Anthropic...")
            print(f"🔵 Client stream type: {type(self.client_stream)}")
            print(f"🔵 Client stream: {self.client_stream}")
            
            # YIELD IMMÉDIAT pour indiquer que le générateur est actif (évite annulation prématurée)
            yield {
                "content": "",
                "is_final": False,
                "model": chosen_model,
                "status": "initializing"
            }
            
            try:
                print(f"🔵 Tentative de création du stream async...")
                print(f"🔵 Model: {chosen_model}")
                print(f"🔵 Max tokens: {max_tokens}")
                print(f"🔵 System prompt length: {len(self.system_prompt) if self.system_prompt else 0}")
                print(f"🔵 Messages count: {len(messages)}")
                
                # Utiliser le client async (self.client_stream) pour ne pas bloquer l'event loop
                stream = self.client_stream.messages.stream(
                    model=chosen_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=self.system_prompt,
                )
                
                print(f"🔵 Stream object créé: {type(stream)}")
                
                # Yield pour garder le générateur actif pendant l'ouverture du stream
                yield {
                    "content": "",
                    "is_final": False,
                    "model": chosen_model,
                    "status": "connecting"
                }
                
                print(f"🔵 AVANT async with - tentative d'entrée dans le context manager...")
                
                async with stream as stream_context:
                    print(f"🔵 Stream async créé (context manager ouvert), début de l'itération...")
                    chunk_count = 0
                    # Utiliser la boucle async pour ne pas bloquer l'event loop
                    async for text in stream_context.text_stream:
                        chunk_count += 1
                        print(f"🔵 Chunk #{chunk_count} reçu: '{text[:50] if len(text) > 50 else text}...'")
                        yield {
                            "content": text,
                            "is_final": False,
                            "model": chosen_model
                        }
                        # Forcer un yield à l'event loop pour permettre l'envoi immédiat
                        await asyncio.sleep(0)
                    print(f"🔵 Streaming terminé, {chunk_count} chunks reçus")
                    
                    # Signal de fin
                    yield {
                        "content": "",
                        "is_final": True,
                        "model": chosen_model
                    }
            except asyncio.CancelledError:
                print(f"🔴 Streaming annulé (CancelledError)")
                raise
            except Exception as stream_error:
                print(f"🔴 Erreur lors du streaming: {stream_error}")
                print(f"🔴 Type d'erreur: {type(stream_error)}")
                import traceback
                traceback.print_exc()
                yield {
                    "content": f"Erreur streaming: {str(stream_error)}",
                    "is_final": True,
                    "error": str(stream_error)
                }
            
        except Exception as e:
            print(f"Erreur streaming Anthropic: {e}")
            yield {
                "content": f"Erreur: {str(e)}",
                "is_final": True,
                "error": str(e)
            }

    def anthropic_send_message(self, content, model_index=None,model_name=None, max_tokens=1024, streaming=False,thinking=False):
        """
        Envoie un message en utilisant un des modèles spécifiés par index, avec option de streaming.

        Args:
            content (str): Le contenu du message à envoyer.
            model_index (int): L'index du modèle à utiliser pour envoyer le message.
            max_tokens (int): Le nombre maximum de tokens pour la réponse.
            streaming (bool): Active le streaming si True.

        Returns:
            str: La réponse complète de l'IA si le streaming est désactivé.
            None: Si le streaming est activé (affiche en temps réel).
        """
        
        
        if not isinstance(model_index, int):
            chosen_model=model_name
        else:
            chosen_model = self.models[model_index]
        
        is_vision_content = isinstance(content, list) and any(item.get('type') == 'image' for item in content)
    

        if is_vision_content:
            print(f"contenu de vision .....")
            # Pour le contenu vision, envoyer directement sans historique
            #print(json.dumps(content, indent=2))
            print(f"Envoi à Anthropic - system_prompt: {self.system_prompt}")
            print(f"Type system_prompt: {type(self.system_prompt)}")
            response = self.client.messages.create(
                model=chosen_model,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                stream=False,
                system=self.system_prompt
            )
            return response
        # Ajout du message de l'utilisateur à l'historique
        else:
            self.add_user_message(content)
            
            # Création de la liste des messages à envoyer à l'API
            messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": msg["content"]} for i, msg in enumerate(self.chat_history)]
            
        try:
            #print(f"impression des messages avant l'envoi:{messages}")
            # Si le streaming est activé, utiliser un flux de messages
            if streaming:
                accumulated_response = ""
                with self.client.messages.stream(
                    model=chosen_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=self.system_prompt,
                ) as stream:
                    for text in stream.text_stream:
                        accumulated_response += text  # Accumule le texte
                        self.add_ai_message(accumulated_response)  # Mise à jour progressive du chat
                        # Mettre à jour également dans la méthode async `anthropic_process_question`
                
                self.add_ai_message(accumulated_response)  # Ajoute la réponse complète après le streaming

            # Si le streaming est désactivé, faire un appel classique
            if thinking:
                thinking_data={"type":"enable",
                               "budget_tokens":16000}
                response = self.client.messages.create(
                    model=chosen_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    stream=False,
                    thinking=thinking_data,
                    system=self.system_prompt
                )
                ai_response = response.content
                print(f"impression de response thinking:{response}")
                self.update_token_usage(response)
                _, _, _, response_data = self.new_extract_tool_use_data(response, verbose=True)
                data_to_hist = self.process_output(response_data)
                self.add_ai_message(data_to_hist)
                return response_data

            else:
                response = self.client.messages.create(
                    model=chosen_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    stream=False,
                    system=self.system_prompt
                )

                ai_response = response.content
                print(f"impression de response:{response}")
                self.update_token_usage(response)
                _, _, _, response_data = self.new_extract_tool_use_data(response, verbose=True)
                data_to_hist = self.process_output(response_data)
                self.add_ai_message(data_to_hist)
                return response_data

        except Exception as e:
            print(f"Erreur lors de l'envoi du message : {e}")
            return None


    def print_ai_response(self, ai_response):
        # Vérification si la réponse est une liste (typiquement contenant des objets ToolsBetaMessage)
        if isinstance(ai_response, list):
            for message in ai_response:
                # Traitement de chaque ToolsBetaMessage dans la liste
                for content_block in message.content:
                    # Vérification si le bloc de contenu est de type TextBlock et contient du texte
                    if hasattr(content_block, 'type') and content_block.type == 'text':
                        print(f"Anthropic: {content_block.text}")
        else:
            # Si ai_response n'est pas une liste, traiter comme un simple message texte
            print(f"Anthropic: {ai_response}")

    def chat_with_antho_bank(self, initial_user_input, model_index, antho_tools, tool_mapping, clerck_instance, manager_instance, auditor_instance, manager_prompt):
        print("Bienvenue dans le chat avec Anthropic!")
        print("Le chat se terminera automatiquement après 'TERMINATE' ou 10 tours.")
        
        max_turns = 10
        turn_count = 0
        manager_instance.update_system_prompt(manager_prompt)
        clerck_prompt = "Tu es un assistant comptable"
        clerck_instance.update_system_prompt(clerck_prompt)
        auditor_prompt = "Tu es un auditeur comptable"
        auditor_instance.update_system_prompt(auditor_prompt)

        user_input = initial_user_input
        answer_text = ""  # Initialisation de answer_text
        manager_instance.flush_chat_history()
        while turn_count < max_turns:
            turn_count += 1
            
            #print(f"\033[95mimpression de user input: {user_input}\033[0m")
            response = manager_instance.antho_agent(user_input, model_index, antho_tools=antho_tools, tool_mapping=tool_mapping)
            print(f"\033[93mimpression de response dans chat_with_antho: {response}\033[0m")

            
            next_user_input = ""
            new_answer_text = ""  # Nouvelle variable pour stocker la réponse de cette itération

            if isinstance(response, list) and len(response) > 0:
                if "tool_output" in response[0]:
                    tool_block = response[0]["tool_output"]
                    #print(f"impression de tool_block:{tool_block}")
                    tool_name = tool_block.get('tool_name')
                    tool_content = tool_block.get('content', '')

                    if tool_name == 'RETRIEVE_INFORMATION_FOR_DECISION':
                        #print(f"impression de l'output pour l'outil 'RETRIEVE_INFORMATION_FOR_DECISION':{tool_content}")
                        next_user_input = f"reponse de l'outil RETRIEVE_INFORMATION_FOR_DECISION:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'ASK_KDB_JOURNAL':
                        print(f"impression de l'output pour l'outil 'ASK_KDB_JOURNAL':{tool_content}")
                        next_user_input = f"reponse de l'outil ASK_KDB_JOURNAL:{tool_content}"
                        new_answer_text = tool_content
                    
                    elif tool_name == 'ASK_USER':
                        resume, next_step = tool_content
                        print(f"impression de l'output pour l'outil 'ASK_USER':{tool_content}")
                        
                        if next_step in ['CLOSE_APP','TERMINATE','NEXT']:
                            prompt=f"""La sesssion va se terminer à present suite aux instructions de l'utilisateur, merci d'effectuer
                            un resumé des etapes entrepris sur le traitment de cette transaction:{resume}"""
                            chat_resume=manager_instance.anthropic_send_message(prompt, model_index)
                            response_text = chat_resume['text_output']['content'].get('answer_text', '')
                            if next_step=='CLOSE_APP':
                                next_step='CLOSE_APP'
                            elif next_step =='TERMINATE':
                                next_step='PROCESS_TERMINATE'
                            elif next_step =='NEXT':
                                next_step='NEXT'
                            instructions="Cloture de l'application....."
                            return True, next_step, instructions, chat_resume
                            
                        else:
                            next_user_input = f"reponse de l'outil ASK_USER:{resume}"
                            
                            new_answer_text = resume

                    elif tool_name == 'UPDATE_DATA_PAYLOAD':
                        print(f"impression de l'output pour l'outil 'UPDATE_DATA_PAYLOAD':{tool_content}")
                        #prompt = f"Voici la réponse du système sur la mise à jour des informations de la facture, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        #response = clerck_instance.anthropic_send_message(prompt, model_index)
                        #response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil 'UPDATE_DATA_PAYLOAD:{tool_content}"
                        new_answer_text = next_user_input

                    elif tool_name == 'RECONCILE_ITEM':
                        print(f"impression de l'output pour l'outil 'RECONCILE_ITEM':{tool_content}")
                        if 'success' in tool_content and tool_content['success'] == True:
                            text_=tool_content['message']
                            next_step='PROCESS_TERMINATE'
                            prompt=f"{text_}\nLa sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
                            chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
                            response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
                            
                            instructions=text_
                            return True,next_step,instructions, response_text
                        elif 'success' in tool_content and tool_content['success'] == False:
                            text_=tool_content['message']
                            next_step='ERROR_IN_BOOKING'
                            prompt=f"""{text_}\nUne erreur s'est produite lors de la tentative de réconciliation, faites un résumé des étapes de l'erreur et de sa cause et proposer la correction adéquate """
                            chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
                            response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
                            instructions=text_
                            return True,next_step,instructions, response_text

                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"impression de l'output pour l'outil 'SEARCH_IN_CHART_OF_ACCOUNT':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche du plan comptable, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil SEARCH_IN_CHART_OF_ACCOUNT:{response_text}"
                        new_answer_text = next_user_input

                    
                    elif tool_name == 'NEXT':
                        print(f"impression de l'output pour l'outil 'NEXT':{tool_content}")
                        next_user_input = f"reponse de l'outil NEXT:{tool_content}"
                        new_answer_text = tool_content
                        next_step=tool_content['next_step']
                        instructions=tool_content['instruction']
                        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
                        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
                        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
                        reasoning_tool=tool_content['reasoning']
                        #print(f"impression de reasoing:{reasoning}")
                        return True,next_step,instructions, response_text

                elif "text_output" in response[0]:
                    text_block = response[0]["text_output"].get('content', response[0]["text_output"].get('text', {}))
                    new_answer_text = text_block.get('answer_text', 'Pas de réponse de answer_text disponible')
                    thinking_text = text_block.get('thinking_text', 'Pas de réponse de thinking_text disponible')
                    #prompt = f"Question ou reflexion de l'exécutant:{new_answer_text}\n {thinking_text}"
                    #response = auditor_instance.anthropic_send_message(prompt, model_index)
                    #response_text = response['text_output']['content'].get('answer_text', '')
                    next_user_input = f"reponse provenant de l'auditeur:{new_answer_text}"

            # Mise à jour de answer_text et user_input
            answer_text = new_answer_text if new_answer_text else answer_text
            user_input = next_user_input if next_user_input else answer_text

        
        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
        next_step='MAX_TURNS'
        instructions=f'Le nombre de tours à été atteint {max_turns} un resumé va etre effectué et le programme devrait repartir....'
        print(f"Nombre maximum de tours ({max_turns}) atteint sans 'TERMINATE'. Fin de la conversation.")
        return False,next_step,instructions,response_text

    def chat_with_antho_coa_mapping(self, initial_user_input, model_index, antho_tools, tool_mapping, clerck_instance, manager_instance, auditor_instance, manager_prompt):
        print("Bienvenue dans le chat avec Anthropic!")
        print("Le chat se terminera automatiquement après 'TERMINATE' ou 10 tours.")
        
        max_turns = 10
        turn_count = 0
        manager_instance.update_system_prompt(manager_prompt)
        clerck_prompt = "Tu es un assistant comptable"
        clerck_instance.update_system_prompt(clerck_prompt)
        auditor_prompt = "Tu es un auditeur comptable"
        auditor_instance.update_system_prompt(auditor_prompt)

        user_input = initial_user_input
        answer_text = ""  # Initialisation de answer_text
        manager_instance.flush_chat_history()
        while turn_count < max_turns:
            turn_count += 1
            
            #print(f"\033[95mimpression de user input: {user_input}\033[0m")
            response = manager_instance.antho_agent(user_input, model_index, antho_tools=antho_tools, tool_mapping=tool_mapping)
            print(f"\033[93mimpression de response dans chat_with_antho: {response}\033[0m")

            
            next_user_input = ""
            new_answer_text = ""  # Nouvelle variable pour stocker la réponse de cette itération

            if isinstance(response, list) and len(response) > 0:
                if "tool_output" in response[0]:
                    tool_block = response[0]["tool_output"]
                    #print(f"impression de tool_block:{tool_block}")
                    tool_name = tool_block.get('tool_name')
                    tool_content = tool_block.get('content', '')

                    if tool_name == 'APPLY_PANDAS_TRANSFORMATIONS':
                        print(f"impression de l'output pour l'outil 'APPLY_PANDAS_TRANSFORMATIONS':{tool_content}")
                        next_user_input = f"reponse de l'outil APPLY_PANDAS_TRANSFORMATIONS:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name=='SAVE_TO_OUTPUT':
                        print(f"impression de l'output pour l'outil 'SAVE_TO_OUTPUT':{tool_content}")
                        if tool_content in ['CLOSE_APP']:
                        
                            next_step=tool_content
                            instructions=f"la mission est terminé"
                            response=None
                            return True,next_step,instructions, response
                        else:
                            prompt=f"la sauvegarde en csv n'a pas fonctionner suite à ceci:{tool_content}"
                            new_answer_text = prompt
                    
                    elif tool_name=='ASK_USER_IN_CHAT':
                        print(f"impression de l'output pour l'outil 'ASK_USER_IN_CHAT':{tool_content}")
                        if tool_content in ['NEXT','TERMINATE','PREV','CLOSE_APP','DELETE','PENDING']:
                            if tool_content =='NEXT':
                                tool_content='NEXT_W_O_SAVING'
                            next_step=tool_content
                            instructions=f"la commande {tool_content} a été initié par l'utilisateur"
                            response=None
                            return True,next_step,instructions, response
                        else:
                            prompt=f"Sur base de la conversation avec l'utilisateur, veuillez les actions suivantes a appliquer selon votre context...."
                            new_answer_text = prompt

                    
                    
                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"impression de l'output pour l'outil 'SEARCH_IN_CHART_OF_ACCOUNT':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche du plan comptable, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil SEARCH_IN_CHART_OF_ACCOUNT:{response_text}"
                        new_answer_text = next_user_input

                    
                    elif tool_name == 'NEXT':
                        print(f"impression de l'output pour l'outil 'NEXT':{tool_content}")
                        next_user_input = f"reponse de l'outil NEXT:{tool_content}"
                        new_answer_text = tool_content
                        next_step=tool_content['next_step']
                        instructions=tool_content['instruction']
                        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
                        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
                        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
                        reasoning_tool=tool_content['reasoning']
                        #print(f"impression de reasoing:{reasoning}")
                        return True,next_step,instructions, response_text

                elif "text_output" in response[0]:
                    text_block = response[0]["text_output"].get('content', response[0]["text_output"].get('text', {}))
                    new_answer_text = text_block.get('answer_text', 'Pas de réponse de answer_text disponible')
                    thinking_text = text_block.get('thinking_text', 'Pas de réponse de thinking_text disponible')
                    #prompt = f"Question ou reflexion de l'exécutant:{new_answer_text}\n {thinking_text}"
                    #response = auditor_instance.anthropic_send_message(prompt, model_index)
                    #response_text = response['text_output']['content'].get('answer_text', '')
                    next_user_input = f"reponse provenant de l'auditeur:{new_answer_text}"
                    print(colored(f"Impression provenant de text_output: \n text_block:{text_block}\n new_answer_text:{new_answer_text}\n thinking_text:{thinking_text}","white","on_blue"))
            # Mise à jour de answer_text et user_input
            answer_text = new_answer_text if new_answer_text else answer_text
            user_input = next_user_input if next_user_input else answer_text

        
        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
        next_step='MAX_TURNS'
        instructions=f'Le nombre de tours à été atteint {max_turns} un resumé va etre effectué et le programme devrait repartir....'
        print(f"Nombre maximum de tours ({max_turns}) atteint sans 'TERMINATE'. Fin de la conversation.")
        return False,next_step,instructions,response_text

    

    def chat_with_antho_create_invoice(self, initial_user_input, model_index, antho_tools, tool_mapping, clerck_instance, manager_instance, auditor_instance, manager_prompt,validator_instance):
        print("Bienvenue dans le chat avec Anthropic!")
        print("Le chat se terminera automatiquement après 'TERMINATE' ou 10 tours.")
        
        max_turns = 10
        turn_count = 0
        manager_instance.update_system_prompt(manager_prompt)
        clerck_prompt = "Tu es un assistant comptable"
        clerck_instance.update_system_prompt(clerck_prompt)
        auditor_prompt = "Tu es un auditeur comptable"
        auditor_instance.update_system_prompt(auditor_prompt)

        user_input = initial_user_input
        answer_text = ""  # Initialisation de answer_text
        manager_instance.flush_chat_history()
        while not validator_instance.check_account_found()['account_found'] and turn_count < max_turns:
            turn_count += 1
            
            print(f"\033[95mimpression de user input: {user_input}\033[0m")
            response = manager_instance.antho_agent(user_input, model_index, antho_tools=antho_tools, tool_mapping=tool_mapping)
            print(f"\033[93mimpression de response dans chat_with_antho: {response}\033[0m")

            
            next_user_input = ""
            new_answer_text = ""  # Nouvelle variable pour stocker la réponse de cette itération

            if isinstance(response, list) and len(response) > 0:
                if "tool_output" in response[0]:
                    tool_block = response[0]["tool_output"]
                    #print(f"impression de tool_block:{tool_block}")
                    tool_name = tool_block.get('tool_name')
                    tool_content = tool_block.get('content', '')

                    

                    
                    if tool_name == 'SEARCH_ON_INTERNET':
                        print(f"impression de l'output pour l'outil 'SEARCH_ON_INTERNET':{tool_content}")
                        next_user_input = f"reponse de l'outil SEARCH_ON_INTERNET:{tool_content}"
                        new_answer_text = tool_content
       
                    

                   

                    elif tool_name == 'UPDATE_INVOICE_INFORMATION':
                        print(f"impression de l'output pour l'outil 'UPDATE_INVOICE_INFORMATION':{tool_content}")
                        #prompt = f"Voici la réponse du système sur la mise à jour des informations de la facture, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        #response = clerck_instance.anthropic_send_message(prompt, model_index)
                        #response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil UPDATE_INVOICE_INFORMATION:{tool_content}"
                        new_answer_text = next_user_input

                    
                    
                    
                    
                    elif tool_name=='ASK_USER_IN_CHAT':
                        print(f"impression de l'output pour l'outil 'ASK_USER_IN_CHAT':{tool_content}")
                        if tool_content in ['NEXT','TERMINATE','PREV','CLOSE_APP','DELETE','PENDING']:
                            if tool_content =='NEXT':
                                tool_content='NEXT_W_O_SAVING'
                            next_step=tool_content
                            instructions=f"la commande {tool_content} a été initié par l'utilisateur"
                            response=None
                            return True,next_step,instructions, response
                        else:
                            prompt=f"Sur base de la conversation avec l'utilisateur, veuillez les actions suivantes a appliquer selon votre context...."
                            new_answer_text = prompt

                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"impression de l'output pour l'outil 'SEARCH_IN_CHART_OF_ACCOUNT':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche du plan comptable, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil SEARCH_IN_CHART_OF_ACCOUNT:{response_text}"
                        new_answer_text = next_user_input

                    elif tool_name == 'VIEW_DOCUMENT_WITH_VISION':
                        print(f"impression de l'output pour l'outil 'VIEW_DOCUMENT_WITH_VISION':{tool_content}")
                        next_user_input = f"reponse de l'outil VIEW_DOCUMENT_WITH_VISION:{tool_content}"
                        new_answer_text = tool_content
                    
                   

                elif "text_output" in response[0]:
                    text_block = response[0]["text_output"].get('content', response[0]["text_output"].get('text', {}))
                    new_answer_text = text_block.get('answer_text', 'Pas de réponse de answer_text disponible')
                    thinking_text = text_block.get('thinking_text', 'Pas de réponse de thinking_text disponible')
                    #prompt = f"Question ou reflexion de l'exécutant:{new_answer_text}\n {thinking_text}"
                    #response = auditor_instance.anthropic_send_message(prompt, model_index)
                    #response_text = response['text_output']['content'].get('answer_text', '')
                    next_user_input = f"reponse provenant de l'auditeur:{new_answer_text}"

            # Mise à jour de answer_text et user_input
            answer_text = new_answer_text if new_answer_text else answer_text
            user_input = next_user_input if next_user_input else answer_text
            # Vérifier si account_found est maintenant True
            if validator_instance.check_account_found()['account_found']:
                print("Les comptes ont été trouvés. Fin de la conversation.")
                break  # Sortir de la boucle si account_found est True
            
        if validator_instance.check_account_found()['account_found']:
            # Fin normale de la conversation
            prompt = f"La session va se terminer à présent. Merci de faire un résumé des actions entreprises ainsi que de ton choix et les motivations de ce dernier."
            chat_history_resume = manager_instance.anthropic_send_message(prompt, model_index)
            response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
            return True, None, None, response_text
        else:
            # Le nombre maximum de tours a été atteint sans trouver les comptes
            print(f"Nombre maximum de tours ({max_turns}) atteint sans que les comptes soient trouvés. Fin de la conversation.")
            return False, None, None, answer_text


    def chat_with_antho_v2(self, initial_user_input, model_index, antho_tools, tool_mapping, clerck_instance, manager_instance, auditor_instance, manager_prompt):
        print("Bienvenue dans le chat avec Anthropic!")
        print("Le chat se terminera automatiquement après 'TERMINATE' ou 10 tours.")
        
        max_turns = 5
        turn_count = 0
        manager_instance.update_system_prompt(manager_prompt)
        clerck_prompt = "Tu es un assistant comptable"
        clerck_instance.update_system_prompt(clerck_prompt)
        auditor_prompt = "Tu es un auditeur comptable"
        auditor_instance.update_system_prompt(auditor_prompt)

        user_input = initial_user_input
        answer_text = ""  # Initialisation de answer_text
        manager_instance.flush_chat_history()
        while turn_count < max_turns:
            turn_count += 1
            
            print(f"\033[95mimpression de user input: {user_input}\033[0m")
            response = manager_instance.antho_agent(user_input, model_index, antho_tools=antho_tools, tool_mapping=tool_mapping)
            print(f"\033[93mimpression de response dans chat_with_antho: {response}\033[0m")

            
            next_user_input = ""
            new_answer_text = ""  # Nouvelle variable pour stocker la réponse de cette itération

            if isinstance(response, list) and len(response) > 0:
                if "tool_output" in response[0]:
                    tool_block = response[0]["tool_output"]
                    #print(f"impression de tool_block:{tool_block}")
                    tool_name = tool_block.get('tool_name')
                    tool_content = tool_block.get('content', '')

                    if tool_name == 'GET_JOB_ID_DETAILS':
                        print(f"impression de l'output pour l'outil 'GET_JOB_ID_DETAILS':{tool_content}")
                        next_user_input = f"reponse de l'outil GET_JOB_ID_DETAILS:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'ASK_KDB_JOURNAL':
                        print(f"impression de l'output pour l'outil 'ASK_KDB_JOURNAL':{tool_content}")
                        next_user_input = f"reponse de l'outil ASK_KDB_JOURNAL:{tool_content}"
                        new_answer_text = tool_content

                    
                    elif tool_name == 'SEARCH_ON_INTERNET':
                        print(f"impression de l'output pour l'outil 'SEARCH_ON_INTERNET':{tool_content}")
                        next_user_input = f"reponse de l'outil SEARCH_ON_INTERNET:{tool_content}"
                        new_answer_text = tool_content
       
                    elif tool_name == 'GET_INVOICE_DETAILS':
                        print(f"impression de l'output pour l'outil 'GET_INVOICE_DETAILS':{tool_content}")
                        next_user_input = f"reponse de l'outil GET_INVOICE_DETAILS:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'VIEW_PAYLOAD':
                        print(f"impression de l'output pour l'outil 'VIEW_PAYLOAD':{tool_content}")
                        next_user_input = f"reponse de l'outil VIEW_PAYLOAD:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'UPDATE_INVOICE_INFORMATION':
                        print(f"impression de l'output pour l'outil 'UPDATE_INVOICE_INFORMATION':{tool_content}")
                        #prompt = f"Voici la réponse du système sur la mise à jour des informations de la facture, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        #response = clerck_instance.anthropic_send_message(prompt, model_index)
                        #response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil UPDATE_INVOICE_INFORMATION:{tool_content}"
                        new_answer_text = next_user_input

                    elif tool_name == 'GET_CONTACT_INFO_IN_ODOO':
                        print(f"impression de l'output pour l'outil 'GET_CONTACT_INFO_IN_ODOO':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche dans les contacts d'odoo, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil GET_CONTACT_INFO_IN_ODOO:{response_text}"
                        new_answer_text = next_user_input


                    elif tool_name=='ASK_USER_IN_CHAT':
                        print(f"impression de l'output pour l'outil 'ASK_USER_IN_CHAT':{tool_content}")
                        if tool_content in ['NEXT','TERMINATE','PREV','CLOSE_APP','DELETE','PENDING']:
                            if tool_content =='NEXT':
                                tool_content='NEXT_W_O_SAVING'
                            next_step=tool_content
                            instructions=f"la commande {tool_content} a été initié par l'utilisateur"
                            response=None
                            return True,next_step,instructions, response
                        else:
                            prompt=f"Sur base de la conversation avec l'utilisateur, veuillez les actions suivantes a appliquer selon votre context...."
                            new_answer_text = prompt

                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"impression de l'output pour l'outil 'SEARCH_IN_CHART_OF_ACCOUNT':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche du plan comptable, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil SEARCH_IN_CHART_OF_ACCOUNT:{response_text}"
                        new_answer_text = next_user_input

                    elif tool_name == 'VIEW_DOCUMENT_WITH_VISION':
                        print(f"impression de l'output pour l'outil 'VIEW_DOCUMENT_WITH_VISION':{tool_content}")
                        next_user_input = f"reponse de l'outil VIEW_DOCUMENT_WITH_VISION:{tool_content}"
                        new_answer_text = tool_content
                    
                    elif tool_name == 'SETUP_TABLE_FOR_INVOICE':
                        print(f"impression de l'output pour l'outil 'SETUP_TABLE_FOR_INVOICE':{tool_content}")
                        next_user_input = f"reponse de l'outil SETUP_TABLE_FOR_INVOICE:{tool_content}"
                        answer,table_dict,data = tool_content
                        if answer:
                            next_step='POST_INVOICE_IN_ODOO'
                            prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
                            chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
                            response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
                            instructions=f"La facture est saisie et prete à etre publier dans l'erp, veuillez appliquer des à present vos parametres d'approbation pour la publication de la facture dans le système"
                            return True,next_step,instructions, response_text
                        else:
                            new_answer_text=data
                        
                        #print(f"impression de reasoing:{reasoning}")
                        

                elif "text_output" in response[0]:
                    text_block = response[0]["text_output"].get('content', response[0]["text_output"].get('text', {}))
                    new_answer_text = text_block.get('answer_text', 'Pas de réponse de answer_text disponible')
                    thinking_text = text_block.get('thinking_text', 'Pas de réponse de thinking_text disponible')
                    #prompt = f"Question ou reflexion de l'exécutant:{new_answer_text}\n {thinking_text}"
                    #response = auditor_instance.anthropic_send_message(prompt, model_index)
                    #response_text = response['text_output']['content'].get('answer_text', '')
                    next_user_input = f"reponse provenant de l'auditeur:{new_answer_text}"

            # Mise à jour de answer_text et user_input
            answer_text = new_answer_text if new_answer_text else answer_text
            user_input = next_user_input if next_user_input else answer_text

        
        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
        
        print(f"Nombre maximum de tours ({max_turns}) atteint sans 'TERMINATE'. Fin de la conversation.")
        return False,None,None,response_text



    def chat_with_antho(self, initial_user_input, model_index, antho_tools, tool_mapping, clerck_instance, manager_instance, auditor_instance, manager_prompt):
        print("Bienvenue dans le chat avec Anthropic!")
        print("Le chat se terminera automatiquement après 'TERMINATE' ou 10 tours.")
        
        max_turns = 5
        turn_count = 0
        manager_instance.update_system_prompt(manager_prompt)
        clerck_prompt = "Tu es un assistant comptable"
        clerck_instance.update_system_prompt(clerck_prompt)
        auditor_prompt = "Tu es un auditeur comptable"
        auditor_instance.update_system_prompt(auditor_prompt)

        user_input = initial_user_input
        answer_text = ""  # Initialisation de answer_text
        manager_instance.flush_chat_history()
        while turn_count < max_turns:
            turn_count += 1
            
            print(f"\033[95mimpression de user input: {user_input}\033[0m")
            response = manager_instance.antho_agent(user_input, model_index, antho_tools=antho_tools, tool_mapping=tool_mapping)
            print(f"\033[93mimpression de response dans chat_with_antho: {response}\033[0m")

            
            next_user_input = ""
            new_answer_text = ""  # Nouvelle variable pour stocker la réponse de cette itération

            if isinstance(response, list) and len(response) > 0:
                if "tool_output" in response[0]:
                    tool_block = response[0]["tool_output"]
                    #print(f"impression de tool_block:{tool_block}")
                    tool_name = tool_block.get('tool_name')
                    tool_content = tool_block.get('content', '')

                    if tool_name == 'GET_JOB_ID_DETAILS':
                        print(f"impression de l'output pour l'outil 'GET_JOB_ID_DETAILS':{tool_content}")
                        next_user_input = f"reponse de l'outil GET_JOB_ID_DETAILS:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'ASK_KDB_JOURNAL':
                        print(f"impression de l'output pour l'outil 'ASK_KDB_JOURNAL':{tool_content}")
                        next_user_input = f"reponse de l'outil ASK_KDB_JOURNAL:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'VIEW_PAYLOAD':
                        print(f"impression de l'output pour l'outil 'VIEW_PAYLOAD':{tool_content}")
                        next_user_input = f"reponse de l'outil VIEW_PAYLOAD:{tool_content}"
                        new_answer_text = tool_content

                    elif tool_name == 'UPDATE_INVOICE_INFORMATION':
                        print(f"impression de l'output pour l'outil 'UPDATE_INVOICE_INFORMATION':{tool_content}")
                        #prompt = f"Voici la réponse du système sur la mise à jour des informations de la facture, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        #response = clerck_instance.anthropic_send_message(prompt, model_index)
                        #response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil UPDATE_INVOICE_INFORMATION:{tool_content}"
                        new_answer_text = next_user_input

                    elif tool_name == 'GET_CONTACT_INFO_IN_ODOO':
                        print(f"impression de l'output pour l'outil 'GET_CONTACT_INFO_IN_ODOO':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche dans les contacts d'odoo, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil GET_CONTACT_INFO_IN_ODOO:{response_text}"
                        new_answer_text = next_user_input

                    
                    elif tool_name == 'GET_PRECISE_INFO_IN_SPACE_CHAT':
                        print(f"impression de l'output pour l'outil 'GET_PRECISE_INFO_IN_SPACE_CHAT':{tool_content}")
                        next_user_input = f"reponse de l'outil GET_PRECISE_INFO_IN_SPACE_CHAT:{tool_content}"
                        new_answer_text = tool_content
                    
                    elif tool_name=='ASK_USER_IN_CHAT':
                        print(f"impression de l'output pour l'outil 'ASK_USER_IN_CHAT':{tool_content}")
                        if tool_content in ['NEXT','TERMINATE','PREV','CLOSE_APP','DELETE','PENDING']:
                            if tool_content =='NEXT':
                                tool_content='NEXT_W_O_SAVING'
                            next_step=tool_content
                            instructions=f"la commande {tool_content} a été initié par l'utilisateur"
                            response=None
                            return True,next_step,instructions, response
                        else:
                            prompt=f"Sur base de la conversation avec l'utilisateur, veuillez les actions suivantes a appliquer selon votre context...."
                            new_answer_text = prompt

                    elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                        print(f"impression de l'output pour l'outil 'SEARCH_IN_CHART_OF_ACCOUNT':{tool_content}")
                        prompt = f"Voici la réponse du système sur la recherche du plan comptable, tu peux en faire une synthèse et répondre à l'utilisateur:{tool_content}"
                        response = clerck_instance.anthropic_send_message(prompt, model_index)
                        response_text = response['text_output']['content'].get('answer_text', '')
                        next_user_input = f"reponse de l'outil SEARCH_IN_CHART_OF_ACCOUNT:{response_text}"
                        new_answer_text = next_user_input

                    elif tool_name == 'VIEW_DOCUMENT_WITH_VISION':
                        print(f"impression de l'output pour l'outil 'VIEW_DOCUMENT_WITH_VISION':{tool_content}")
                        next_user_input = f"reponse de l'outil VIEW_DOCUMENT_WITH_VISION:{tool_content}"
                        new_answer_text = tool_content
                    
                    elif tool_name == 'NEXT_STEP_AND_INSTRUCTIONS':
                        print(f"impression de l'output pour l'outil 'NEXT_STEP_AND_INSTRUCTIONS':{tool_content}")
                        next_user_input = f"reponse de l'outil NEXT_STEP_AND_INSTRUCTIONS:{tool_content}"
                        new_answer_text = tool_content
                        next_step=tool_content['next_step']
                        instructions=tool_content['instruction']
                        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
                        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
                        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
                        reasoning_tool=tool_content['reasoning']
                        #print(f"impression de reasoing:{reasoning}")
                        return True,next_step,instructions, response_text

                elif "text_output" in response[0]:
                    text_block = response[0]["text_output"].get('content', response[0]["text_output"].get('text', {}))
                    new_answer_text = text_block.get('answer_text', 'Pas de réponse de answer_text disponible')
                    thinking_text = text_block.get('thinking_text', 'Pas de réponse de thinking_text disponible')
                    #prompt = f"Question ou reflexion de l'exécutant:{new_answer_text}\n {thinking_text}"
                    #response = auditor_instance.anthropic_send_message(prompt, model_index)
                    #response_text = response['text_output']['content'].get('answer_text', '')
                    next_user_input = f"reponse provenant de l'auditeur:{new_answer_text}"

            # Mise à jour de answer_text et user_input
            answer_text = new_answer_text if new_answer_text else answer_text
            user_input = next_user_input if next_user_input else answer_text

        
        prompt=f"La sesssion va se terminer à present, merci de faire un résumé des actions entrepris ainsi que de ton choix et les motivations de ce dernier"
        chat_history_resume=manager_instance.anthropic_send_message(prompt, model_index)
        response_text = chat_history_resume['text_output']['content'].get('answer_text', '')
        
        print(f"Nombre maximum de tours ({max_turns}) atteint sans 'TERMINATE'. Fin de la conversation.")
        return False,None,None,response_text



class NEW_OpenAiAgent:
    def __init__(self, space_manager=None, collection_name=None, job_id=None):
        """
        Initialise la classe OpenAiAgent avec la clé API nécessaire pour authentifier les requêtes.
        
        :param space_manager: Gestionnaire d'espace (optionnel).
        :param collection_name: Nom de la collection (optionnel).
        :param job_id: ID du job (optionnel).
        """
        self.chat_history = []
        self.space_manager = space_manager
        self.collection_name = collection_name
        self.job_id = job_id
        self.api_key = get_secret('openai_pinnokio')
        self.client=OpenAI(api_key=self.api_key)
        self.token_usage = {}
        self.current_model = None
        self.models=['gpt-4o-mini','gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo-0125']
        self.reasoning_models=['o1-mini-2024-09-12','o1-2024-12-17']

    def update_token_usage(self, raw_response):
        """
        Met à jour les compteurs de tokens pour OpenAI.
        
        Args:
            raw_response: La réponse brute d'OpenAI contenant les informations d'utilisation
        """
        if hasattr(raw_response, 'model') and hasattr(raw_response, 'usage'):
            model = raw_response.model
            #print(f"impression de raw_response:{raw_response}")
            if model not in self.token_usage:
                self.token_usage[model] = {
                    'total_input_tokens': 0,
                    'total_output_tokens': 0
                }

            # Mise à jour des tokens d'entrée (prompt)
            prompt_tokens = raw_response.usage.prompt_tokens
            self.token_usage[model]['total_input_tokens'] += prompt_tokens

            # Mise à jour des tokens de sortie (completion)
            completion_tokens = raw_response.usage.completion_tokens
            self.token_usage[model]['total_output_tokens'] += completion_tokens

            # Mise à jour du modèle courant
            self.current_model = model

            # Gérer les détails supplémentaires si disponibles
            if hasattr(raw_response.usage, 'prompt_tokens_details'):
                cached_tokens = getattr(raw_response.usage.prompt_tokens_details, 'cached_tokens', 0)
                audio_tokens = getattr(raw_response.usage.prompt_tokens_details, 'audio_tokens', 0)
                
                if 'details' not in self.token_usage[model]:
                    self.token_usage[model]['details'] = {}
                
                self.token_usage[model]['details'].update({
                    'cached_tokens': cached_tokens,
                    'audio_tokens': audio_tokens
                })

    def get_total_tokens(self):
        """
        Retourne l'utilisation totale des tokens pour chaque modèle.
        
        Returns:
            dict: Un dictionnaire contenant l'utilisation des tokens par modèle
                {
                    'model_name': {
                        'total_input_tokens': X,
                        'total_output_tokens': Y,
                        'model': 'model_name',
                        'details': {  # Optionnel, si disponible
                            'cached_tokens': Z,
                            'audio_tokens': W
                        }
                    }
                }
        """
        token_stats = {}
        
        for model, usage in self.token_usage.items():
            stats = {
                'total_input_tokens': usage['total_input_tokens'],
                'total_output_tokens': usage['total_output_tokens'],
                'model': model
            }
            
            # Ajouter les détails si disponibles
            if 'details' in usage:
                stats['details'] = usage['details']
                
            token_stats[model] = stats
            
        return token_stats

    def flush_chat_history(self):
        self.chat_history = []
        self.reset_token_counters()
    
    def reset_token_counters(self):
        """
        Réinitialise tous les compteurs de tokens pour tous les modèles.
        """
        self.token_usage = {}

    def add_user_message(self, content):
        if isinstance(content, dict):
            content = json.dumps(content)  # Convertit le dict en chaîne de caractères JSON
        self.chat_history.append({'role': 'user', 'content': content})
    
    def add_ai_message(self,content):
        if isinstance(content,dict):
            content=json.dumps(content)
        self.chat_history.append({'role': 'assistant', 'content': content})

    def update_system_prompt(self, content):
        if isinstance(content, dict):
            content = json.dumps(content)  # Convertit le dict en chaîne de caractères JSON
        self.chat_history.append({'role': 'developer', 'content': content})
    
    def openai_send_message_tool(self, content, model_index=None,model_name=None, tool_list=None, tool_name=None,tool_choice=None):
        if tool_list is None or tool_name is None:
            # Si tool_list ou tool_name n'est pas renseigné, appelle une fonction alternative
            print("tool_list ou tool_name non renseigné, appel de openai_send_message.")
            return self.openai_send_message(content, model_index,model_name)
        else:
            # Sinon, continue avec la logique existante pour utiliser l'outil spécifié
            if not isinstance(model_index, int):
                chosen_model=model_name
            else:
                chosen_model = self.models[model_index]

            print(f"Modèle choisi : {chosen_model}")
            
            
            self.add_user_message(content)
            
            tools = self.find_tool_by_name(tool_list, tool_name)
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=self.chat_history,
                max_tokens=1024,
                tools=[tools],
                tool_choice=tool_choice
            )
            self.update_token_usage(response)
            #print(response)
            
            # Vérifie si la réponse contient des tool_calls
            if response.choices[0].message.tool_calls:
                tool_calls = response.choices[0].message.tool_calls
                function_call = tool_calls[0]
                function_arguments = function_call.function.arguments
                arguments_dict = json.loads(function_arguments)
                #ai_response = arguments_dict
                ai_response = json.dumps(arguments_dict)
            else:
                ai_response = response.choices[0].message.content
            
            self.add_ai_message(ai_response)
            
            return ai_response
    
    def find_tool_by_name(self, tools_list, tool_name):
        """
        Recherche un outil spécifique par son nom dans une liste de dictionnaires.

        Args:
            tools_list (list): La liste des dictionnaires représentant les outils.
            tool_name (str): Le nom de l'outil à rechercher.

        Returns:
            dict or None: Le dictionnaire représentant l'outil trouvé, ou None si aucun outil correspondant n'est trouvé.
        """
        for tool in tools_list:
            # Ajoute un log pour voir le contenu de chaque outil
           

            # Vérifie si le nom de la fonction correspond
            if tool.get('function') and tool['function'].get('name') == tool_name:
                return tool
        
        return None  # Aucun outil trouvé avec le nom spécifié

    def openai_agent(self, content, model_index=None, model_name=None, tools=None, tool_mapping=None, verbose=True, tool_choice=None, stream=False,raw_output=False, **kwargs):
        """
        Point d'entrée principal pour l'utilisation d'outils avec l'API OpenAI.
        
        Args:
            content (str): Le contenu du message
            model_index (int, optional): Index du modèle à utiliser
            model_name (str, optional): Nom spécifique du modèle
            tools (list): Liste des outils disponibles
            tool_mapping (dict): Mapping des outils vers leurs fonctions
            verbose (bool): Afficher les détails d'exécution
            tool_choice (dict): Configuration du choix d'outil
            stream (bool): Utiliser le streaming
            max_tokens (int): Nombre maximum de tokens pour la réponse

        Returns:
            list: Liste des réponses générées
        """
        if not model_name and model_index is not None:
            chosen_model = self.models[model_index]
        else:
            chosen_model = model_name
        
        # Configuration par défaut du tool_choice si non spécifié
        if tool_choice is None:
            tool_choice = "auto"  # Format OpenAI pour auto

        # Préparation des outils mappés
        mapped_tools = []
        if tool_mapping:
            if isinstance(tool_mapping, dict):
                tool_mapping = [tool_mapping]

            for tool_dict in tool_mapping:
                for tool_name, function_or_none in tool_dict.items():
                    tool_info = next((tool for tool in tools if 'function' in tool and tool['function']['name'] == tool_name), None)
                    if tool_info:
                        mapped_tools.append(tool_info)

        # Ajout du message utilisateur à l'historique
        self.add_user_message(content)
        print(f"impression du model choisi:{chosen_model}")
        try:
            # Création du dictionnaire des paramètres de base
            api_params = {
                "model": chosen_model,
                "messages": self.chat_history,
                "stream": stream
            }
            
            # Ajout des paramètres de token depuis kwargs
            if 'max_tokens' in kwargs or 'max_completion_tokens' in kwargs:
                # Si les paramètres sont déjà fournis par l'encapsuleur, les utiliser tels quels
                api_params.update(kwargs)
            else:
                # Si appel direct, vérifier si le modèle est dans reasoning_models
                max_tokens_value = kwargs.get('max_tokens', 1024)  # Valeur par défaut
                tokens_key = 'max_completion_tokens' if chosen_model in self.reasoning_models else 'max_tokens'
                api_params[tokens_key] = max_tokens_value

            
            # Ajout conditionnel des outils et tool_choice
            if tools:
                api_params["tools"] = tools
                api_params["tool_choice"] = tool_choice

            # Création de la requête à l'API
            #print(f"Impression de parametre:{api_params}")
            response = self.client.chat.completions.create(**api_params)
            #print(f"impreesion brut de repsonse:{response}")
            # Mise à jour des compteurs de tokens
            self.update_token_usage(response)

            responses = []
            
            # Traitement de la réponse
            message = response.choices[0].message
            
            # Vérif ication de l'utilisation d'outils
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tool_call in message.tool_calls:
                    function_call = tool_call.function
                    tool_name = function_call.name
                    
                    try:
                        # Parsing des arguments de l'outil
                        arguments = json.loads(function_call.arguments)
                        
                        # Recherche de la fonction correspondante dans le mapping
                        function_or_none = None
                        for tool_dict in tool_mapping or []:
                            if tool_name in tool_dict:
                                function_or_none = tool_dict[tool_name]
                                break
                        
                        if callable(function_or_none):
                            # Exécution de la fonction
                            tool_result = function_or_none(**arguments)
                            
                            # Formatage de la réponse de l'outil
                            tool_response = {
                                "tool_output": {
                                    "tool_name": tool_name,
                                    "content": tool_result
                                }
                            }
                            responses.append(tool_response)
                        else:
                            # Si pas de fonction trouvée, retourner les arguments bruts
                            tool_response = {
                                "tool_output": {
                                    "tool_name": tool_name,
                                    "content": arguments
                                }
                            }
                            responses.append(tool_response)
                    
                    except json.JSONDecodeError as e:
                        print(f"Erreur de décodage JSON pour les arguments de l'outil: {e}")
                        continue
                    except Exception as e:
                        print(f"Erreur lors de l'exécution de l'outil {tool_name}: {e}")
                        continue

            # Si un message texte est présent
            if message.content:
                text_response = {
                    "text_output": {
                        "content": {
                            "answer_text": message.content,
                            "thinking_text": ""  # OpenAI n'a pas d'équivalent direct au thinking_text d'Anthropic
                        }
                    }
                }
                responses.append(text_response)

            # Ajout de la réponse à l'historique
            self.add_ai_message(message.content if message.content else str(responses))
            if not raw_output:
                data=self.final_handle_responses(responses)
            else:
                data=responses
            
            return data

        except Exception as e:
            print(f"Erreur lors de l'exécution de openai_agent: {e}")
            return [{
                "text_output": {
                    "content": {
                        "answer_text": f"Une erreur s'est produite: {str(e)}",
                        "thinking_text": ""
                    }
                }
            }]

    def openai_send_message(self, content, model_index=None,model_name=None):
        """
        Envoie un message en utilisant un des modèles spécifiés par index.
        Liste des modèles : ['gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo-0125']
        
        Args:
            content (str): Le contenu du message à envoyer.
            model_index (int): L'index du modèle à utiliser pour envoyer le message.
        """
        
        
        # Sélection du modèle en utilisant l'index fourni
        if not isinstance(model_index, int):
            chosen_model=model_name
        else:
            chosen_model = self.models[model_index]

        
        # Déterminer si le contenu est de type vision
        is_vision_content = isinstance(content, list) and any(isinstance(item, dict) and 
                        ('type' in item or 'role' in item) for item in content)
        
        if is_vision_content:
            # Pour le contenu vision, nous utilisons directement le format fourni
            messages = content
        else:
            # Pour le texte simple, nous utilisons l'historique normal
            self.add_user_message(content)
            messages = self.chat_history

        
        try:
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=messages,
                max_tokens=1024
            )
            #print(response)
            self.update_token_usage(response)
            ai_response = response.choices[0].message.content
            self.add_ai_message(ai_response)
            return ai_response
        except Exception as e:
            print(f"Erreur lors de l'envoi du message : {e}")
            return None

    def chat_openai_agent(self, model_index):
        """
        Fonction permettant des échanges continus avec l'agent OpenAI.

        Args:
            agent (OpenAIAgent): Instance de l'agent OpenAI.
            model_index (int): L'index du modèle à utiliser pour envoyer les messages.
        """
        while True:
            user_input = input("Vous: ")
            if user_input.lower() in ['exit', 'quit', 'stop']:
                print("Chat terminé.")
                break
            
            response = self.openai_send_message(user_input, model_index)
            if response:
                print(f"Agent: {response}")
            else:
                print("Erreur lors de l'envoi du message.")

    async def openai_send_message_tool_streaming(self, content, model_index=None, model_name=None,
                                                  tools=None, tool_mapping=None, tool_choice=None, max_tokens=1024):
        """
        Envoie un message avec streaming et support d'outils depuis l'API OpenAI.
        Retourne un format uniforme pour l'intégration avec BaseAIAgent.
        
        Args:
            content (str): Le contenu du message
            model_index (int, optional): Index du modèle à utiliser
            model_name (str, optional): Nom spécifique du modèle
            tools (list): Liste des outils disponibles (format OpenAI)
            tool_mapping (list[dict]): Mapping des outils vers leurs fonctions
            tool_choice (str|dict): Configuration du choix d'outil ("auto", "required", ou {"type": "function", "function": {"name": "..."}})
            max_tokens (int): Nombre maximum de tokens pour la réponse
            
        Yields:
            Dict[str, Any]: Chunks de réponse au format uniforme:
                {
                    "type": "text" | "tool_use" | "tool_result" | "final",
                    "content": str,           # Pour le texte
                    "tool_name": str,         # Pour l'utilisation d'outil
                    "tool_input": dict,       # Arguments de l'outil
                    "tool_output": any,       # Résultat de l'outil
                    "is_final": bool,
                    "model": str
                }
        """
        try:
            # Déterminer le modèle
            if model_name:
                chosen_model = model_name
            elif model_index is not None:
                chosen_model = self.models[model_index]
            else:
                chosen_model = self.models[0]
            
            # Configuration par défaut du tool_choice
            if tool_choice is None:
                tool_choice = "auto"
            
            print(f"Envoi streaming avec tools vers OpenAI - modèle: {chosen_model}")
            
            # Ajouter le message utilisateur
            self.add_user_message(content)
            
            # YIELD IMMÉDIAT
            yield {
                "type": "status",
                "content": "",
                "is_final": False,
                "model": chosen_model,
                "status": "initializing"
            }
            
            try:
                print(f"🔵 Début du streaming OpenAI avec tools...")
                print(f"🔵 Model: {chosen_model}")
                print(f"🔵 Tools count: {len(tools) if tools else 0}")
                
                # Déterminer le bon paramètre de tokens
                api_params = {
                    "model": chosen_model,
                    "messages": self.chat_history,
                    "stream": True
                }
                
                tokens_key = 'max_completion_tokens' if chosen_model in self.reasoning_models else 'max_tokens'
                api_params[tokens_key] = max_tokens
                
                # Ajouter les tools si fournis
                if tools:
                    api_params["tools"] = tools
                    api_params["tool_choice"] = tool_choice
                
                # Yield pour la connexion
                yield {
                    "type": "status",
                    "content": "",
                    "is_final": False,
                    "model": chosen_model,
                    "status": "connecting"
                }
                
                # Variables pour accumuler les données
                accumulated_text = ""
                tool_calls_data = {}  # Dictionnaire pour stocker les appels d'outils en cours
                tool_results = []
                
                # Créer le stream OpenAI
                stream = self.client.chat.completions.create(**api_params)
                
                print(f"🔵 Stream OpenAI créé, début de l'itération...")
                chunk_count = 0
                
                # Itérer sur les chunks du stream
                for chunk in stream:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        chunk_count += 1
                        
                        # Vérifier si le delta contient du texte
                        if hasattr(delta, 'content') and delta.content:
                            text = delta.content
                            accumulated_text += text
                            
                            yield {
                                "type": "text",
                                "content": text,
                                "is_final": False,
                                "model": chosen_model
                            }
                            await asyncio.sleep(0)
                        
                        # Vérifier si le delta contient des appels d'outils
                        if hasattr(delta, 'tool_calls') and delta.tool_calls:
                            for tool_call_delta in delta.tool_calls:
                                tool_call_id = tool_call_delta.id if tool_call_delta.id else None
                                tool_index = tool_call_delta.index
                                
                                # Initialiser l'entrée pour ce tool_call si nécessaire
                                if tool_index not in tool_calls_data:
                                    tool_calls_data[tool_index] = {
                                        "id": tool_call_id,
                                        "name": "",
                                        "arguments": ""
                                    }
                                
                                # Mettre à jour l'ID si fourni
                                if tool_call_id:
                                    tool_calls_data[tool_index]["id"] = tool_call_id
                                
                                # Accumuler le nom de la fonction
                                if hasattr(tool_call_delta.function, 'name') and tool_call_delta.function.name:
                                    tool_calls_data[tool_index]["name"] += tool_call_delta.function.name
                                
                                # Accumuler les arguments
                                if hasattr(tool_call_delta.function, 'arguments') and tool_call_delta.function.arguments:
                                    tool_calls_data[tool_index]["arguments"] += tool_call_delta.function.arguments
                
                print(f"🔵 Streaming terminé, {chunk_count} chunks reçus")
                print(f"🔵 Tool calls détectés: {len(tool_calls_data)}")
                
                # Traiter les appels d'outils accumulés
                for tool_index, tool_data in tool_calls_data.items():
                    tool_name = tool_data["name"]
                    tool_id = tool_data["id"]
                    
                    try:
                        tool_input = json.loads(tool_data["arguments"])
                        
                        # Yield pour indiquer l'utilisation d'outil
                        yield {
                            "type": "tool_use",
                            "tool_name": tool_name,
                            "tool_id": tool_id,
                            "tool_input": tool_input,
                            "is_final": False,
                            "model": chosen_model
                        }
                        
                        # Exécuter la fonction si mapping fourni
                        tool_output = None
                        if tool_mapping:
                            function_or_none = None
                            for tool_dict in tool_mapping:
                                if tool_name in tool_dict:
                                    function_or_none = tool_dict[tool_name]
                                    break
                            
                            if callable(function_or_none):
                                print(f"🔵 Exécution de la fonction {tool_name}")
                                # Exécution async ou sync
                                if asyncio.iscoroutinefunction(function_or_none):
                                    tool_output = await function_or_none(**tool_input)
                                else:
                                    loop = asyncio.get_event_loop()
                                    tool_output = await loop.run_in_executor(None, lambda: function_or_none(**tool_input))
                                
                                # Yield du résultat
                                yield {
                                    "type": "tool_result",
                                    "tool_name": tool_name,
                                    "tool_id": tool_id,
                                    "tool_output": tool_output,
                                    "is_final": False,
                                    "model": chosen_model
                                }
                                
                                tool_results.append({
                                    "tool_name": tool_name,
                                    "tool_id": tool_id,
                                    "input": tool_input,
                                    "output": tool_output
                                })
                    
                    except json.JSONDecodeError as e:
                        print(f"🔴 Erreur décodage JSON tool arguments: {e}")
                    except Exception as e:
                        print(f"🔴 Erreur exécution outil {tool_name}: {e}")
                
                # Ajouter la réponse à l'historique
                if accumulated_text:
                    self.add_ai_message(accumulated_text)
                elif tool_results:
                    # Si uniquement des tools sans texte, ajouter une représentation
                    self.add_ai_message(f"[Utilisation d'outils: {', '.join([r['tool_name'] for r in tool_results])}]")
                
                # Signal de fin
                yield {
                    "type": "final",
                    "content": accumulated_text,
                    "tool_results": tool_results,
                    "is_final": True,
                    "model": chosen_model
                }
                
            except asyncio.CancelledError:
                print(f"🔴 Streaming OpenAI tools annulé")
                raise
            except Exception as stream_error:
                print(f"🔴 Erreur streaming OpenAI tools: {stream_error}")
                import traceback
                traceback.print_exc()
                yield {
                    "type": "error",
                    "content": f"Erreur streaming: {str(stream_error)}",
                    "is_final": True,
                    "error": str(stream_error),
                    "model": chosen_model
                }
        
        except Exception as e:
            print(f"Erreur streaming OpenAI tools: {e}")
            yield {
                "type": "error",
                "content": f"Erreur: {str(e)}",
                "is_final": True,
                "error": str(e)
            }

    async def openai_send_message_streaming(self, content, model_index=None, model_name=None, max_tokens=1024):
        """
        Envoie un message avec streaming réel depuis l'API OpenAI.
        Retourne le même format que anthropic_send_message_streaming pour uniformité.
        
        Args:
            content (str): Le contenu du message à envoyer
            model_index (int, optional): Index du modèle à utiliser
            model_name (str, optional): Nom spécifique du modèle
            max_tokens (int): Nombre maximum de tokens pour la réponse
            
        Yields:
            Dict[str, Any]: Chunks de réponse au format uniforme:
                {
                    "content": str,      # Le texte du chunk
                    "is_final": bool,    # True pour le dernier chunk
                    "model": str,        # Nom du modèle utilisé
                    "status": str,       # Optionnel: statut de la requête
                    "error": str         # Optionnel: message d'erreur
                }
        """
        try:
            # Déterminer le modèle
            if model_name:
                chosen_model = model_name
            elif model_index is not None:
                chosen_model = self.models[model_index]
            else:
                chosen_model = self.models[0]  # Modèle par défaut
            
            print(f"Envoi streaming vers OpenAI avec modèle: {chosen_model}")
            
            # Ajouter le message utilisateur
            self.add_user_message(content)
            
            # YIELD IMMÉDIAT pour indiquer que le générateur est actif
            yield {
                "content": "",
                "is_final": False,
                "model": chosen_model,
                "status": "initializing"
            }
            
            try:
                print(f"🔵 Début du streaming OpenAI...")
                print(f"🔵 Model: {chosen_model}")
                print(f"🔵 Max tokens: {max_tokens}")
                print(f"🔵 Messages count: {len(self.chat_history)}")
                
                # Déterminer le bon paramètre de tokens selon le modèle
                api_params = {
                    "model": chosen_model,
                    "messages": self.chat_history,
                    "stream": True
                }
                
                # Utiliser max_completion_tokens pour les modèles de raisonnement, sinon max_tokens
                tokens_key = 'max_completion_tokens' if chosen_model in self.reasoning_models else 'max_tokens'
                api_params[tokens_key] = max_tokens
                
                # Yield pour indiquer la connexion
                yield {
                    "content": "",
                    "is_final": False,
                    "model": chosen_model,
                    "status": "connecting"
                }
                
                # Créer le stream OpenAI
                stream = self.client.chat.completions.create(**api_params)
                
                print(f"🔵 Stream OpenAI créé, début de l'itération...")
                chunk_count = 0
                accumulated_content = ""
                
                # Itérer sur les chunks du stream
                for chunk in stream:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        
                        # Vérifier si le delta contient du contenu
                        if hasattr(delta, 'content') and delta.content:
                            chunk_count += 1
                            content_text = delta.content
                            accumulated_content += content_text
                            
                            print(f"🔵 Chunk #{chunk_count} reçu: '{content_text[:50] if len(content_text) > 50 else content_text}...'")
                            
                            yield {
                                "content": content_text,
                                "is_final": False,
                                "model": chosen_model
                            }
                            
                            # Petite pause pour permettre l'envoi immédiat
                            await asyncio.sleep(0)
                
                print(f"🔵 Streaming terminé, {chunk_count} chunks reçus")
                
                # Ajouter la réponse complète à l'historique
                if accumulated_content:
                    self.add_ai_message(accumulated_content)
                
                # Mettre à jour l'utilisation des tokens si disponible
                # Note: Pour le streaming OpenAI, les tokens ne sont pas toujours disponibles
                # dans chaque chunk, mais peuvent l'être dans le dernier
                
                # Signal de fin
                yield {
                    "content": "",
                    "is_final": True,
                    "model": chosen_model
                }
                
            except asyncio.CancelledError:
                print(f"🔴 Streaming OpenAI annulé (CancelledError)")
                raise
            except Exception as stream_error:
                print(f"🔴 Erreur lors du streaming OpenAI: {stream_error}")
                print(f"🔴 Type d'erreur: {type(stream_error)}")
                import traceback
                traceback.print_exc()
                yield {
                    "content": f"Erreur streaming: {str(stream_error)}",
                    "is_final": True,
                    "error": str(stream_error)
                }
        
        except Exception as e:
            print(f"Erreur streaming OpenAI: {e}")
            yield {
                "content": f"Erreur: {str(e)}",
                "is_final": True,
                "error": str(e)
            }

    def final_handle_responses(self, input_data):
        
        # Vérifier si input_data est une liste
        if isinstance(input_data, list):
           
            # Si c'est une liste avec un seul élément, retourner directement cet élément
            if len(input_data) == 1:
                return self.basic_handle_response(input_data[0])
            else:
                
                # Si c'est une liste avec plusieurs éléments, traiter chaque élément
                results = []
                for item in input_data:
                    result = self.basic_handle_response(item)
                    results.append(result)
                return results
        else:
            # Si ce n'est pas une liste, traiter comme un seul élément
            #print("passage par final_handle_response, uraiter comme un seul élément")
            return self.basic_handle_response(input_data)

    def basic_handle_response(self, response):
        # Initialisation de la variable de sortie
        data = {}

        # Vérification des clés 'tool_output' et 'text_output' dans la réponse
        has_tool_output = 'tool_output' in response
        has_text_output = 'text_output' in response

        # Priorité de traitement : text_output > tool_output
        if has_tool_output and has_text_output:
            # Si à la fois du texte et un outil fournissent des sorties, les combiner pour une réponse enrichie
            print("Texte et outil présents, combinaison des réponses.")
            
            # Extraire les contenus du texte et de l'outil
            text_content = response['text_output'].get('content', '')
            tool_content = response['tool_output'].get('content', {})
            
            # Affichage pour le débogage
            #print("Réponse textuelle reçue :")
            #print(text_content)
            #print("Réponse de l'outil reçue :")
            #print(tool_content)

            # Combinaison des données dans un seul dictionnaire
            data = {
                'text_output': {'text': text_content},
                'tool_output': tool_content
            }

        elif has_text_output:
            text_content = response['text_output'].get('content', '')
            if isinstance(text_content, dict) and 'answer_text' in text_content:
                data= text_content['answer_text']
            #data = {'text_output': {'text': text_content}}

        elif has_tool_output:
            tool_content = response['tool_output'].get('content', {})
            if isinstance(tool_content, list):
            # Si c'est une liste, extraire les éléments de la liste
                data = []
                for item in tool_content:
                    if isinstance(item, dict) and 'text' in item:
                        data.append(item['text'])
            else:
                data = tool_content
            

            # Vérifier si 'next_step' fait partie des éléments autorisés dans step_list
            

        else:
            print("Format de réponse non reconnu.")
            #data = {'erreur': "Erreur dans l'extraction du texte"}
        #print(f"impression de reponses final dans basic_handle_response:{data}")
        return data



class NEW_GeminiAgent:
    """
    Agent pour l'intégration de Google Gemini AI, supportant les fonctionnalités texte, 
    outil et vision avec une interface cohérente.
    """
    def __init__(self, project_id: Optional[str] = None, 
                 location: Optional[str] = None):
        """
        Initialise l'agent Gemini avec les configurations nécessaires.
        
        Args:
            project_id: ID du projet pour Vertex AI
            location: Localisation pour Vertex AI
        """
        self.chat_history = []
        self.token_usage = {}
        self.current_model = None
        self.models = ['gemini-1.5-flash-8b','gemini-2.0-flash-thinking-exp-1219','gemini-2.0-flash-exp']
        
        if project_id and location:
            # Configuration pour Vertex AI
            self.client = genai.Client(
                vertexai=True,
                project=project_id,
                location=location
            )
        else:
            # Configuration pour Google AI API
            api_key = get_secret('gemini_api_key')
            self.client = genai.Client(api_key=api_key)

    def update_token_usage(self, response: Any) -> None:
        """Met à jour le compteur de tokens basé sur la réponse.
        
        Args:
            response: La réponse brute de l'API Gemini contenant les métadonnées d'utilisation
        """
        if not hasattr(response, 'usage_metadata'):
            print("Pas de métadonnées d'utilisation disponibles")
            return

        model = getattr(response, 'model_version', 'default')
        if model not in self.token_usage:
            self.token_usage[model] = {
                'total_input_tokens': 0,
                'total_output_tokens': 0,
                'total_tokens': 0
            }
        
        usage = response.usage_metadata
        self.token_usage[model]['total_input_tokens'] += usage.prompt_token_count
        self.token_usage[model]['total_output_tokens'] += usage.candidates_token_count
        self.token_usage[model]['cached_tokens'] = usage.cached_content_token_count
        self.token_usage[model]['total_tokens'] = usage.total_token_count

        # Mise à jour du modèle courant
        self.current_model = model
       

    def get_total_tokens(self) -> Dict[str, Dict[str, int]]:
        """Retourne l'utilisation totale des tokens par modèle.
        
        Returns:
            Dict contenant les statistiques d'utilisation des tokens par modèle
        """
        return {
            model: {
                'total_input_tokens': usage['total_input_tokens'],
                'total_output_tokens': usage['total_output_tokens'],
                'total_tokens': usage['total_tokens'],
                'model': model
            }
            for model, usage in self.token_usage.items()
        }

    def process_text(self, content: str, model_index: Optional[int] = None, 
                    model_name: Optional[str] = None, stream: bool = False,
                    max_tokens: int = 1024, **kwargs) -> Dict[str, Any]:
        """
        Traite une requête texte simple.
        
        Args:
            content: Le texte à traiter
            model_index: Index du modèle à utiliser
            model_name: Nom du modèle à utiliser
            stream: Activer le streaming
            max_tokens: Nombre maximum de tokens
            **kwargs: Arguments supplémentaires pour la génération
            
        Returns:
            Dict contenant la réponse générée ou un générateur si stream=True
        """
        
        if not isinstance(model_index, int):
            chosen_model = model_name
        else:
            chosen_model = self.models[model_index]
            if chosen_model is None:
                print(f"Modèle d'index {model_index} non trouvé. Utilisation du modèle par défaut.")
                chosen_model = self.models[0]  # Utilisation du modèle par défaut
        
        print(f"Model choisi: {chosen_model}")
        
        
        try:
            if stream:
                def stream_generator():
                    response = self.client.models.generate_content_stream(
                        model=chosen_model,
                        contents=content,
                        config=types.GenerateContentConfig(
                            max_output_tokens=max_tokens,
                            **kwargs
                        )
                    )
                    accumulated_response = ""
                    for chunk in response:
                        if hasattr(chunk, 'text'):
                            accumulated_response += chunk.text
                            yield {'text_output': {'content': chunk.text}}
                
                return stream_generator()
            else:
                response = self.client.models.generate_content(
                    model=chosen_model,
                    contents=content,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_tokens,
                        **kwargs
                    )
                )
                
                self.update_token_usage(response)
                return response.text
        except Exception as e:
            error_msg = {'error': str(e)}
            print(f"Erreur lors du traitement: {error_msg}")
            return error_msg

    def process_vision(
        self,
        text: List[Union[str, Dict[str, Any]]],  # Le contenu transformé par BaseAgent
        model_name: Optional[str] = None,
        model_index: Optional[int] = None,
        tool_list: Optional[List[Dict[str, Any]]] = None,
        tool_mapping: Optional[Dict[str, Any]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
        max_tokens: int = 1024,
        **kwargs
        ) -> Dict[str, Any]:
        """
        Traite une requête vision en utilisant les données déjà transformées par BaseAgent.
        
        Args:
            text: Liste contenant le texte et les images transformées par BaseAgent
            model_name: Nom du modèle à utiliser
            tool_list: Liste des outils disponibles
            tool_mapping: Mapping des outils vers leurs fonctions
            tool_choice: Configuration du choix d'outil
            max_tokens: Nombre maximum de tokens
            **kwargs: Arguments supplémentaires
            
        Returns:
            Dict contenant la réponse générée
        """
        try:
            # Sélection du modèle
            if not isinstance(model_index, int):
                chosen_model = model_name
            else:
                chosen_model = self.models[model_index]
                if chosen_model is None:
                    print(f"Modèle d'index {model_index} non trouvé. Utilisation du modèle par défaut.")
                    chosen_model = self.models[0]
            print(f"\n=== Début du process_vision Gemini ===")
            #print(f"Text reçu: {text}")
            print(f"Tool list reçu: {tool_list}")
            print(f"Tool mapping reçu: {tool_mapping}")
            print(f"Tool choice reçu: {tool_choice}")
            
            # Configuration pour Gemini
            config = types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                **kwargs
            )
            
            # Ajout de la configuration des outils si fournie
            if tool_list:
                gemini_tools = types.Tool(function_declarations=tool_list)
                config.tools = [gemini_tools]
                
                if tool_choice:
                    config.tool_config = {'function_calling_config': tool_choice}
            
            # Génération de la réponse
            response = self.client.models.generate_content(
                model=model_name,
                contents=text,  # BaseAgent a déjà préparé le contenu correctement
                config=config
            )
            
            print(f"Réponse brute Gemini: {response}")
            self.update_token_usage(response)
            
            # Gestion des appels d'outils
            if not response.candidates:
                return {'error': 'Aucune réponse générée'}
                
            
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'function_call') and part.function_call is not None:
                        # Traitement des appels de fonction ici si nécessaire
                        return {'tool_calls': [{'name': part.function_call.name, 'args': part.function_call.args}]}
                    elif hasattr(part, 'text') and part.text is not None:
                        # Retour du texte si disponible
                        return part.text
            
            # Si on arrive ici, c'est qu'on n'a trouvé ni texte ni appel de fonction
            return {'error': 'Format de réponse non reconnu'}

        except Exception as e:
            print(f"\n=== ERREUR DÉTECTÉE ===")
            print(f"Type d'erreur: {type(e).__name__}")
            print(f"Message d'erreur: {str(e)}")
            return {'error': f'Erreur lors du traitement: {str(e)}'}

    
    def reset_token_counters(self):
        """
        Réinitialise tous les compteurs de tokens pour tous les modèles.
        """
        self.token_usage = {}

    def flush_chat_history(self):
        self.chat_history = []
        self.reset_token_counters()
    
    def process_tool_use(
        self,
        content: str,
        tools: List[Dict[str, Any]],
        model_name: str,
        model_index: Optional[int] = None,
        tool_mapping: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "AUTO",
        stream: bool = False,
        max_tokens: int = 1024,
        
        **kwargs
        ) -> Dict[str, Any]:
        """
        Traite une requête avec utilisation d'outils pour Gemini.
        
        Args:
            content: Le texte de la requête
            tools: Liste des déclarations d'outils
            model_name: Nom du modèle à utiliser
            tool_mapping: Liste de dictionnaires mappant les noms d'outils aux fonctions
            tool_choice: Mode d'utilisation des outils ("AUTO", "ANY", "NONE")
            stream: Activer le streaming
            max_tokens: Nombre maximum de tokens
            **kwargs: Arguments supplémentaires
            
        Returns:
            Dict contenant la réponse et les résultats des appels d'outils
        """
        try:
            #print(f"\n=== Début du process_tool_use ===")
            #print(f"Content reçu: {content}")
            #print(f"Tools reçus: {json.dumps(tools, indent=2)}")
            #print(f"Tool mapping reçu: {tool_mapping}")
            #print(f"Tool choice reçu: {tool_choice}")

            # Flatten tool_mapping if it is a list of dictionaries
            if isinstance(tool_mapping, list):
                tool_mapping = {key: value for d in tool_mapping for key, value in d.items()}
                #print(f"[DEBUG] Tool mapping après flattening : {tool_mapping}")

            #print(f"Tool choice reçu: {tool_choice}")
            # 1. Sélection du modèle
            if not isinstance(model_index, int):
                chosen_model = model_name

            
            else:
                chosen_model = self.models[model_index]
                if chosen_model is None:
                    print(f"Modèle d'index {model_index} non trouvé. Utilisation du modèle par défaut.")
                    chosen_model = self.models[0]
            
            # 2. Préparation des outils
            function_declarations = []
            callable_functions = {}  # Pour lier les outils callables

            for tool in tools:
                if callable(tool):
                    # Convertir la fonction en un schéma JSON
                    func_dict = {
                        'name': tool.__name__,
                        'description': tool.__doc__ or 'Aucune description',
                        'parameters': {
                            'type': 'OBJECT',
                            'properties': {},
                            'required': []
                        }
                    }
                    # Analyse des paramètres de la fonction
                    import inspect
                    sig = inspect.signature(tool)
                    for param_name, param in sig.parameters.items():
                        param_type = param.annotation.__name__ if param.annotation != inspect.Parameter.empty else 'STRING'
                        func_dict['parameters']['properties'][param_name] = {
                            'type': 'STRING',  # Par défaut, les types sont STRING
                            'description': f'Paramètre {param_name}'
                        }
                        if param.default == inspect.Parameter.empty:
                            func_dict['parameters']['required'].append(param_name)

                    # Ajouter la déclaration et enregistrer la fonction callable
                    function_declarations.append(func_dict)
                    callable_functions[tool.__name__] = tool
                else:
                    # Ajouter directement les outils définis en JSON
                    function_declarations.append(tool)
            # Créer le `Tool` pour Gemini
            #print(f"\n=== Création du Tool Gemini ===")
            gemini_tools = types.Tool(function_declarations=function_declarations)
            #print(f"Tool Gemini créé: {gemini_tools}")
            
                    # 3. Configuration de l'appel
            function_calling_config = {
                'mode': tool_choice
            }
            if tool_choice == "ANY" and tool_mapping:
                function_calling_config['allowed_function_names'] = list(tool_mapping.keys())
            

            config = types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                tools=[gemini_tools],
                tool_config={'function_calling_config': tool_choice},
                **kwargs
            )
            #print(f"\n=== Configuration finale ===")
            #print(f"Config: {config}")
            # Préparation de la map des fonctions pour le traitement des résultats
            

            # Appel à l'API Gemini
            response = self.client.models.generate_content(
                model=model_name,
                contents=content,
                config=config
            )
            print(f"ipmression de reponse brut dans tool use gemini:{response}")
            self.update_token_usage(response)

            if not response.candidates:
                return {'error': 'Aucune réponse générée'}

            # Traiter les appels de fonction
            tool_calls = []
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'function_call'):
                        fc = part.function_call
                        #print(f"[INFO] Appel de fonction détecté : {fc.name}, Arguments : {fc.args}")
                        handler = tool_mapping.get(fc.name)
                        
                        # Si pas de handler ou handler est None, retourner juste les arguments
                        if handler:
                            #print(f"[DEBUG] Handler trouvé pour l'outil '{fc.name}': {handler}")
                            try:
                                # Appeler le handler avec les arguments
                                result = handler(**fc.args)
                                #print(f"[INFO] Résultat du handler '{fc.name}': {result}")
                            except Exception as e:
                                result = {"error": f"Erreur lors de l'exécution du handler : {str(e)}"}
                                #print(f"[ERROR] Erreur dans le handler '{fc.name}': {str(e)}")
                        else:
                            result = {"info": "Aucun handler défini pour cet outil."}
                            #print(f"[WARNING] Aucun handler trouvé pour '{fc.name}'")

                        tool_calls.append({
                            'name': fc.name,
                            'arguments': fc.args,
                            'result': result
                        })

            # 6. Retour des résultats
            if tool_calls:
                print(f"[INFO] Résultats des appels d'outils : {json.dumps(tool_calls, indent=2)}")
                return {'tool_calls': tool_calls}

            print(f"[INFO] Aucun appel d'outil détecté. Réponse textuelle : {response.text}")
            return {'text_output': {'content': response.text}}

        except Exception as e:
            print(f"\n=== ERREUR DÉTECTÉE ===")
            print(f"Type d'erreur: {type(e).__name__}")
            print(f"Message d'erreur: {str(e)}")
            return {'error': f'Erreur lors du traitement: {str(e)}'}

    def add_user_message(self, message: Union[str, Dict[str, Any]]) -> None:
        """Ajoute un message utilisateur à l'historique."""
        if isinstance(message, dict):
            message = json.dumps(message)
        self.chat_history.append({'role': 'user', 'content': message})

    def add_ai_message(self, message: Union[str, Dict[str, Any]]) -> None:
        """Ajoute un message AI à l'historique."""
        if isinstance(message, dict):
            message = json.dumps(message)
        self.chat_history.append({'role': 'assistant', 'content': message})

    def update_system_prompt(self, prompt: str) -> None:
        """Met à jour le prompt système."""
        self.system_prompt = prompt

class NEW_PERPLEX_AGENT:
    def __init__(self):
        self.api_key = get_secret('perplexity_api')
        self.url = "https://api.perplexity.ai/chat/completions"
        self.system_prompt = None
        self.model = "llama-3.1-sonar-small-128k-online"
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}"
        }
        logging.basicConfig(level=logging.INFO)
        self.token_usage = {}
        self.chat_history = []

    def update_token_usage(self, raw_response):
        """
        Met à jour les compteurs de tokens par modèle.
        
        Args:
            raw_response (dict): La réponse brute de l'API Perplexity
            Format attendu:
            {
                'model': 'llama-3.1-sonar-small-128k-online',
                'usage': {
                    'prompt_tokens': 49,
                    'completion_tokens': 286,
                    'total_tokens': 335
                },
                ...
            }
        """
        if isinstance(raw_response, dict) and 'usage' in raw_response and 'model' in raw_response:
            model = raw_response['model']
            if model not in self.token_usage:
                self.token_usage[model] = {
                    'total_input_tokens': 0,
                    'total_output_tokens': 0
                }
            
            usage = raw_response['usage']
            self.token_usage[model]['total_input_tokens'] += usage.get('prompt_tokens', 0)
            self.token_usage[model]['total_output_tokens'] += usage.get('completion_tokens', 0)
            self.current_model = model

    def get_total_tokens(self):
        """
        Retourne l'utilisation des tokens pour chaque modèle dans un format unifié.
        
        Returns:
            dict: Utilisation des tokens par modèle
            Format:
            {
                'llama-3.1-sonar-small-128k-online': {
                    'total_input_tokens': X,
                    'total_output_tokens': Y,
                    'model': 'llama-3.1-sonar-small-128k-online'
                },
                ...
            }
        """
        return {
            model: {
                'total_input_tokens': usage['total_input_tokens'],
                'total_output_tokens': usage['total_output_tokens'],
                'model': model
            }
            for model, usage in self.token_usage.items()
        }

    def update_system_prompt(self,system_prompt):
        self.system_prompt = system_prompt

    def agent_init(self):
        prompt=f"""Tu es un agent spécialisé dans les recherches internet, tu es appelé pour faire des recherches ciblé
         sur des contacts ou des sociétés. """
        self.update_system_prompt(prompt)

    def clear_chat_history(self):
        """Clear the chat history"""
        self.chat_history = []

    def search(self, query, model_name=None, domain_filter=None,
               max_tokens=None):
        """
        Effectue une recherche avec paramètres de génération configurables.
        
        Args:
            query (str): La requête à rechercher
            model_name (str, optional): Le modèle à utiliser
            domain_filter (List[str], optional): Liste de domaines pour filtrer les sources
            temperature (float, optional): Température pour la génération (0.0-2.0)
            top_p (float, optional): Paramètre top_p pour le sampling (0.0-1.0)
            top_k (int, optional): Paramètre top_k pour le filtering (0-2048)
            max_tokens (int, optional): Nombre maximum de tokens de sortie
            stream (bool, optional): Activer le streaming de la réponse
        
        Returns:
            Tuple[str, List[str]]: (contenu de la réponse, liste des citations)
        """
        if model_name:
            self.model = model_name

        base_messages = [{"role": "user", "content": query}] if self.system_prompt is None else [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": query}
        ]
        
        # Insert chat history before the new query
        messages = base_messages[:1] + self.chat_history + base_messages[1:] if self.system_prompt else self.chat_history + base_messages

        payload = {
            "model": self.model,
            "messages": messages
        }
        print(f"impression de payload:{payload}")
        # Ajout des paramètres optionnels
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        if domain_filter:
            if not isinstance(domain_filter, list):
                raise ValueError("domain_filter doit être une liste")
            if len(domain_filter) > 3:
                raise ValueError("Maximum 3 domaines autorisés dans domain_filter")
            payload["search_domain_filter"] = domain_filter

        try:
            response = requests.post(self.url, headers=self.headers, json=payload)
            response.raise_for_status()
            result = response.json()
            self.chat_history.append({"role": "user", "content": query})
            self.chat_history.append({
                "role": "assistant", 
                "content": result['choices'][0]['message']['content']
            })
            # Mise à jour du suivi des tokens
            self.update_token_usage(result)
            
            # Extraction de la réponse et des citations
            content = result['choices'][0]['message']['content']
            citations = result.get('citations', [])
            
            return content, citations
            
        except requests.RequestException as e:
            logging.error(f"Error during API request: {e}")
            return None, []
        except (KeyError, IndexError) as e:
            logging.error(f"Error parsing API response: {e}")
            return None, []
        
    def run(self,question):
        self.agent_init()
        data=self.search(question)
        return data
      


class Anthropic_KDB_AGENT:
    def __init__(self,chroma_db_instance) -> None:
        
        #self.api_key=get_secret('voyageai_api_anthropic')
        #self.vo=voyageai.Client(self.api_key)
        anthropic=NEW_Anthropic_Agent()
        self.agent=BaseAIAgent()
        self.agent.register_provider(ModelProvider.ANTHROPIC,anthropic)
        self.chroma_db_instance=chroma_db_instance
        self.models=['claude-3-5-sonnet-20240620', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307', 'claude-3-opus-20240229']
        self.AGENT_INIT()

    def afficher_date_heure(self):
        # Obtenir la date et l'heure actuelles
        maintenant = datetime.now(timezone.utc)
        # Formater la date et l'heure
        date_heure_formatee = maintenant.strftime("%Y-%m-%d %H:%M:%S")
        # Afficher la date et l'heure
        return date_heure_formatee


    def AGENT_INIT(self):
        prompt = f"""Tu es un assistant spécialisé dans la recherche d'informations à travers une base de données vectorielle (ChromaDB). Ta mission est d'aider les utilisateurs à trouver des informations pertinentes en utilisant les outils adaptés à leur requête.

        # CONTEXTE TEMPOREL
        Date et heure actuelles: {self.afficher_date_heure()}
        Utilise cette information comme point de référence pour toute question temporelle.

        # STRUCTURE DES MÉTADONNÉES DE FILTRAGE
        Pour chaque recherche, tu dois créer un filtre de métadonnées adapté comportant ces clés:
        
        1. pinnokio_func (OBLIGATOIRE): Indique le département concerné:
        - APbookeeper: Factures fournisseurs
        - EXbookeeper: Notes de frais
        - Bankbookeeper: Transactions bancaires
        - HRmanager: Ressources humaines
        - Admanager: Questions administratives
        
        2. source (OBLIGATOIRE): Indique le type de contenu à rechercher:
        - journal: Journaux des traitements internes
        - journal/chat: Échanges avec l'utilisateur
        - context: Informations contextuelles
        
        3. file_name (OPTIONNEL): Nom spécifique du fichier si mentionné
        - Si non spécifié, utilise toujours '<UNKNOWN>' comme valeur par défaut
        - Format typique: "chat_{{job_id}}.txt" ou "journal_{{job_id}}.txt"

        # OUTILS DISPONIBLES
        Tu as accès aux outils suivants pour effectuer tes recherches:

        1. ASK_PINNOKIO: Outil de base pour interroger la base de données vectorielle.
        - Paramètres:
            * user_query: Question précise pour la recherche
            * metadata_filters: Dictionnaire avec pinnokio_func, source et éventuellement file_name

        2. GET_JOB_ID: Outil pour extraire un identifiant de job (job_id) à partir d'une requête.
        - Paramètres:
            * user_query: Question pour rechercher un job_id
            * metadata_filters: Dictionnaire avec pinnokio_func et source appropriés

        3. GET_JOB_DETAILS: Outil pour obtenir les informations détaillées sur un job spécifique.
        - Paramètres: 
            * job_id: Identifiant du job (format klk-uuid)
            * user_query: Question spécifique sur ce job
            * mode: "basis" (information générale) ou "accounting_tech" (détails comptables)
            * metadata_filters: Normalement pas nécessaire car job_id est suffisant

        4. GET_BY_FILTER: Outil pour des recherches avec filtres complexes.
        - Paramètres:
            * user_query: Question pour la recherche
            * metadata_filters: Dictionnaire complet avec tous les critères de filtrage

        5. CREATE_METADATA_FILTER: Outil pour générer un dictionnaire de filtrage approprié
        - Paramètres:
            * user_query: Question de l'utilisateur
            * pinnokio_func: Département concerné (obligatoire)
            * source: Type de contenu (obligatoire)
            * file_name: Nom du fichier (par défaut '<UNKNOWN>')

        # WORKFLOW DE RECHERCHE
        Suis ce processus pour répondre efficacement aux requêtes:

        1. ANALYSE DE LA REQUÊTE
        - Identifie le type d'information recherchée et le département concerné
        - Détermine si la requête concerne un job spécifique, une facture, ou une information générale

        2. CRÉATION DU FILTRE DE MÉTADONNÉES
        - Utilise d'abord CREATE_METADATA_FILTER pour générer le dictionnaire de filtrage approprié
        - Assure-toi que pinnokio_func et source sont correctement définis selon le contexte

        3. SÉLECTION D'OUTIL
        - Pour une requête générale → Utilise ASK_PINNOKIO avec le filtre créé
        - Pour une requête sur un job sans ID → Utilise GET_JOB_ID avec le filtre créé, puis GET_JOB_DETAILS
        - Pour une requête sur une facture → Utilise GET_JOB_ID avec filtres adaptés puis GET_JOB_DETAILS en mode "accounting_tech"
        - Pour une recherche avec critères temporels ou multiples → Utilise GET_BY_FILTER avec le filtre créé

        4. EXÉCUTION ET ANALYSE
        - Exécute l'outil approprié avec le filtre de métadonnées
        - Si nécessaire, utilise un deuxième outil pour affiner les résultats

        5. RÉPONSE FINALE
        - Fournis une réponse claire et concise basée sur les informations trouvées
        - Si aucune information satisfaisante n'est trouvée, explique pourquoi
        - Signale explicitement la fin de ta tâche par TASK_COMPLETE si tu as obtenu tous les résultats attendus

        # EXEMPLES DE FLUX DE TRAVAIL

        ## Exemple 1: Question sur une facture
        Requête: "Comment a été comptabilisée la dernière facture de Société ABC?"
        Actions:
        1. Créer un filtre: {{"pinnokio_func": "APbookeeper", "source": "journal/chat", "file_name": "<UNKNOWN>"}}
        2. Utiliser GET_JOB_ID avec ce filtre pour trouver le job_id associé
        3. Utiliser GET_JOB_DETAILS avec le job_id trouvé en mode "accounting_tech"

        ## Exemple 2: Question sur une note de frais
        Requête: "Quelles étaient les notes de frais soumises la semaine dernière?"
        Actions:
        1. Créer un filtre: {{"pinnokio_func": "EXbookeeper", "source": "journal", "file_name": "<UNKNOWN>"}}
        2. Utiliser GET_BY_FILTER avec ce filtre pour trouver les informations

        ## Exemple 3: Question RH
        Requête: "Quelles sont les dernières politiques de congés?"
        Actions:
        1. Créer un filtre: {{"pinnokio_func": "HRmanager", "source": "context", "file_name": "<UNKNOWN>"}}
        2. Utiliser ASK_PINNOKIO avec ce filtre pour trouver les informations

        N'oublie pas que ton objectif principal est de fournir des réponses pertinentes et précises aux utilisateurs. Choisis toujours l'outil le plus approprié en fonction du contexte de la requête.
        """
        self.agent.update_system_prompt(prompt)
    
    
    def antho_kdb(self,user_query,metadata_dict,model_index,excl_job_id=None):
            if excl_job_id is not None:
                file_to_exclude=f"journal_{excl_job_id}.txt"
                resultat=self.chroma_db_instance.ask_pinnokio_chroma_2(user_query, metadata_filters=metadata_dict,exclude_file_name=file_to_exclude)
            else:
                resultat=self.chroma_db_instance.ask_pinnokio_chroma_2(user_query, metadata_filters=metadata_dict)
            print(f"impression de userquey_dans la recherche de chroma_db:{user_query}")
            documents=resultat['documents']
            #print(f"impression des documents recherché dans chromadb:{documents}")
            prompt=f"""Voici les résultat de la recherche sémantique dans la base de donnée vectorielle, trouve la meilleures correspondance à la question initial qui est:
            \n{user_query}\n.
            
            Si les résulats de la recherche vectoriel ne fournit pas de réponse satisfaisante, partager votre retour à l'utilisateur en expliquant l'insatisfesance de ton choix
            réponses:{documents}"""
            data=self.agent.process_text(prompt)
            
            self.agent.flush_chat_history()
            return data
    
    def antho_dld_context(self,user_query,model_index):
            
            metadata_dict={'source':'context','pinnokio_func':'APbookeeper','file_name':'accounting_report.txt'}
            resultat=self.chroma_db_instance.ask_pinnokio_chroma_2(user_query, metadata_filters=metadata_dict)
            
            documents=resultat['documents']
            print(f"impression des documents recherché dans chromadb:{documents}")
            prompt=f"""Voici les résultat de la recherche sémantique dans la base de donnée vectorielle, trouve la meilleures correspondance à la question initial qui est:
            \n{user_query}\n.
            
            Si les résulats de la recherche vectoriel ne fournit pas de réponse satisfaisante, partager votre retour à l'utilisateur en expliquant l'insatisfesance de ton choix
            réponses:{documents}"""
            data=self.agent.process_text(prompt)
            
            self.agent.flush_chat_history()
            return data
    
    
    def antho_get_job_id_detail(self, user_query, metadata_dict, model_index, job_id=None,mode='basis'):
        """Mode = 'basis' requete avec prompt basic de recherche sur job id
        Mode= 'accounting_tech, prompt orienté dans la sémmantique pour une recherche précise sur les méthode de comptabilisation adapaté au FrameWork de Pinnokio"""
        print(f"methode antho_get_job_id_details lancée.....")
        if job_id is None:
            resultat = self.chroma_db_instance.ask_pinnokio_chroma_2(user_query, metadata_filters=metadata_dict)
            documents = resultat.get('documents', [])
            #print(f"Documents trouvés dans ChromaDB : {documents}")
            
            prompt = f"""Voici les résultats de la recherche sémantique dans la base de données vectorielle, trouve la meilleure correspondance à la question initiale qui est :
            \n{user_query}\n.
            Récupère le job_id qui concerne la demande particulière de l'utilisateur.
            Réponses : {documents}"""
            
            get_job_id_tool = [{
                "name": "get_job_id",
                "description": "outil d'extraction d'un job_id",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "numéro uuid avec le préfixe klk "
                        }
                    },
                    "required": ["job_id"]
                }
            }]
            
            tool_map = {'get_job_id': None}
            tool_choice = {'type': 'tool', 'name': 'get_job_id'}
            #print(f"Impreesion du prompt dasn antho_get_job_id_detail : {prompt}")
            response_text = self.agent.process_tool_use(content=prompt,tools=get_job_id_tool,tool_mapping=tool_map, tool_choice=tool_choice)
            #print(f"Réponse obtenue : {data}")
            

        if job_id is not None:
            metadata_dict = {'source': 'journal/chat', 'pinnokio_func': 'APbookeeper', 'file_name': f"chat_{job_id}.txt"}
            #print(f"Dictionnaire de métadonnées : {metadata_dict}")
            
            job_details = self.chroma_db_instance.ask_pinnokio_chroma_2(user_query, metadata_filters=metadata_dict)
            
            if 'documents' in job_details:
                documents = job_details['documents']
                #print(f"Documents trouvés pour le job_id : {documents}")
                if mode=='basis':
                    prompt = f"Voici les détails du chat du job_id demandé : {documents}\n\nMerci de l'analyser et de répondre à la question de l'utilisateur en fonction du contexte."
                
                elif mode=='accounting_tech':
                    prompt=f""" \n\n
                    Ta recherche est axée spécifiquement sur toute information pertinantes sur la maniere de saisire la facture. Pour faire cela tu as acces aux logs d'audit de la derniere facture saisie de ce contact. Ton objectif ces informations sont récupérables principalement 
                    durant les échanges entre l'IA et l'utilisateur. Les informations de l'utilisateur permettent de connaitre si il existe des regles de repartiation spéciale en terme d'allocation de compte comptable. Ou si il est définit une regle pour reconnaitre
                    les différents type de charge a reconnaitre sur les factures ou si il faut appliquer une regle spéciale en matiere d'encodage de TVA.
                    Communique aussi les informations sur les comptes comptable utilisé et ainsi que sur la nature de la charge , ces informations sont récupérable durant les étapes de transfromation du log appelés:
                    'BOOK_NEW_INVOICE_BASED_ON_HISTORY_INVOICE','CREATE_NEW_INVOICE','ROUTE_DEFINITION'  & 'ROUTE_DEFINITION_2' sont des étapes servant a trouver les informations sur le contact dans l'erp
                     ATTENTION: Il est possible que le systeme reviennent plusieurs fois sur des étapes pour se corriger ou changer des informations en fonctions des demandes de l'utilisateur. Les informations finaux determinante seront toujours
                      dans le dictionnaire présenté avant l'envoi dans ODOO dans l'étape 'POST_INVOICE_IN_ODOO'. Enfin , communique tres brievement sur l'étape realisé par la facture, les factures saisie dans le systeme on une étape 'POST_INVOICE_IN_ODOO' reussi et 'ARCHIVE_INVOICE'.
                      Si le document que tu traites est dépourvu de la realisation de cette étape, tu peux communiquer evenutuellement sur d'autre information contextuel en relation avec saisie des facture de contact est communiqué dans le chat.
                      Si aucune information pertinante est à relevé , réponds tout simplement en disant le context fourni n'indique aucun traitement spéciale de facture .
                    Voici les détails du chat du job_id demandé : {documents}\n\n """

                data = self.agent.process_text(prompt)
                response_text = data.get('text_output', {}).get('content', {}).get('answer_text', "Réponse non trouvée.")
                
            else:
                response_text = "Les détails du job n'ont pas pu être récupérés."
        else:
            response_text = "Le Job_id n'a pas pu être extrait."
            #print(response_text)

        self.agent.flush_chat_history()
        return response_text

    def antho_get_by_filter(self,user_query,metadata_dict,model_index):
        # Appel asynchrone à la base de données
        if metadata_dict.get('file_name') == '<UNKNOWN>':
            del metadata_dict['file_name']
        print(f"imprssion de metadata dict:{metadata_dict}")
        filtered_metadatas, node_ids, documents = self.chroma_db_instance.fetch_documents_with_date_range(
            query_text=user_query,
            criteria=metadata_dict
        )

        #print(f"impression de userquery_dans la recherche de chroma_db:{user_query}")

        prompt = f"""Voici les résultat de la recherche sémantique dans la base de donnée vectorielle, trouve la meilleures correspondance à la question initial qui est:
        \n{user_query}\n.
        
        Si les résulats de la recherche vectoriel ne fournit pas de réponse satisfaisante, partager votre retour à l'utilisateur en expliquant l'insatisfesance de ton choix
        réponses:{documents}"""
        print(f"impression du prompt pour kdb:{prompt}")
        chosen_model = self.models[model_index]
        
        data=self.agent.process_text(prompt)
        
        print(f"imprssion de la reponse final dans analyse_mulitpe_docds:{data}")
        return  data
    
    def x_CHROMADB_AGENT(self, user_query, initial_metadata_dict=None, model_index=0):
        """
        Point d'entrée principal pour l'agent de recherche dans ChromaDB
        
        Args:
            collection_name: Nom de la collection ChromaDB à interroger
            user_query: Question de l'utilisateur
            initial_metadata_dict: Dictionnaire initial de métadonnées (optionnel)
            model_index: Index du modèle à utiliser (défaut: 0)
            
        Returns:
            Réponse de l'agent
        """
        # Définition des outils disponibles
        tools = [
            {
                "name": "CREATE_METADATA_FILTER",
                "description": "Créer un dictionnaire de filtrage pour la recherche",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pinnokio_func": {
                            "type": "string",
                            "description": "Département concerné (APbookeeper, EXbookeeper, Bankbookeeper, HRmanager, Admanager)",
                            "enum": ["APbookeeper", "EXbookeeper", "Bankbookeeper", "HRmanager", "Admanager"]
                        },
                        "source": {
                            "type": "string",
                            "description": "Type de contenu (journal, journal/chat, context)",
                            "enum": ["journal", "journal/chat", "context"]
                        },
                        "file_name": {
                            "type": "string",
                            "description": "Nom du fichier spécifique si mentionné, sinon '<UNKNOWN>'"
                        }
                    },
                    "required": ["pinnokio_func", "source"]
                }
            },
            {
                "name": "ASK_PINNOKIO",
                "description": "Recherche simple dans la base de données vectorielle",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_query": {
                            "type": "string",
                            "description": "Question précise pour la recherche"
                        },
                        "metadata_filters": {
                            "type": "object",
                            "description": "Filtres de métadonnées pour affiner la recherche"
                        }
                    },
                    "required": ["user_query", "metadata_filters"]
                }
            },
            {
                "name": "GET_JOB_ID",
                "description": "Extraction d'un job_id à partir d'une requête",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_query": {
                            "type": "string",
                            "description": "Requête pour rechercher un job_id spécifique"
                        },
                        "metadata_filters": {
                            "type": "object",
                            "description": "Filtres de métadonnées pour affiner la recherche"
                        }
                    },
                    "required": ["user_query", "metadata_filters"]
                }
            },
            {
                "name": "GET_JOB_DETAILS",
                "description": "Obtenir les détails d'un job spécifique",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Identifiant du job (format klk-uuid)"
                        },
                        "user_query": {
                            "type": "string",
                            "description": "Question spécifique sur ce job"
                        },
                        "mode": {
                            "type": "string",
                            "description": "Mode de recherche: 'basis' ou 'accounting_tech'",
                            "enum": ["basis", "accounting_tech"]
                        }
                    },
                    "required": ["job_id", "user_query"]
                }
            },
            {
                "name": "GET_BY_FILTER",
                "description": "Recherche avancée avec filtres multiples",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_query": {
                            "type": "string",
                            "description": "Question pour la recherche"
                        },
                        "metadata_filters": {
                            "type": "object",
                            "description": "Dictionnaire de filtres de métadonnées"
                        }
                    },
                    "required": ["user_query", "metadata_filters"]
                }
            },
            {
                "name": "TASK_COMPLETE",
                "description": "Signaler la fin de la tâche de recherche",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message final résumant les résultats obtenus"
                        }
                    },
                    "required": ["message"]
                }
            }
        ]
        
        # Fonction pour créer un dictionnaire de métadonnées
        def create_metadata_filter(pinnokio_func, source, file_name="<UNKNOWN>"):
            return {
                "pinnokio_func": pinnokio_func,
                "source": source,
                "file_name": file_name
            }
        
        # Mapping des outils vers leurs fonctions respectives
        tool_map = {
            'CREATE_METADATA_FILTER': create_metadata_filter,
            'ASK_PINNOKIO': lambda user_query, metadata_filters: self.antho_kdb(
                
                user_query=user_query, 
                metadata_dict=metadata_filters, 
                model_index=model_index
            ),
            'GET_JOB_ID': lambda user_query, metadata_filters: self.antho_get_job_id_detail(
                
                user_query=user_query, 
                metadata_dict=metadata_filters, 
                model_index=model_index, 
                job_id=None, 
                mode='basis'
            ),
            'GET_JOB_DETAILS': lambda job_id, user_query, mode='basis': self.antho_get_job_id_detail(
                
                user_query=user_query, 
                metadata_dict={"pinnokio_func": "APbookeeper", "source": "journal/chat", "file_name": f"chat_{job_id}.txt"},
                model_index=model_index, 
                job_id=job_id, 
                mode=mode
            ),
            'GET_BY_FILTER': lambda user_query, metadata_filters: self.antho_get_by_filter(
                user_query=user_query, 
                metadata_dict=metadata_filters, 
                model_index=model_index
            ),
            'TASK_COMPLETE': lambda message: f"SIGNAL_TERMINATE: {message}"
        }
        
        # Configuration initiale de l'agent avec le prompt système
        self.AGENT_INIT()
        
        # Lancement du workflow avec un maximum de tours pour éviter les boucles infinies
        max_turns = 10
        turn_count = 0
        
        # Si un dictionnaire initial de métadonnées est fourni, on l'utilise comme contexte supplémentaire
        metadata_context = ""
        if initial_metadata_dict:
            metadata_context = f"\nContexte initial de filtrage: {json.dumps(initial_metadata_dict)}"
        
        # Message initial pour l'agent
        current_message = f"Nouvelle requête utilisateur: {user_query}{metadata_context}\n\nAnalyse cette requête, détermine les métadonnées de filtrage appropriées en utilisant CREATE_METADATA_FILTER, puis utilise l'outil le plus adapté pour répondre à la question."
        
        # Historique des étapes et résultats pour construire la réponse finale
        workflow_steps = []
        final_response = None
        
        while turn_count < max_turns:
            turn_count += 1
            print(f"Tour {turn_count}/{max_turns}")
            
            # L'agent décide quel outil utiliser
            response = self.agent.process_tool_use(
                content=current_message,
                tools=tools,
                tool_mapping=tool_map,
                raw_output=True,
            )
            
            print(f"Réponse de l'agent (tour {turn_count}): {response}")
            
            # Vérifier si l'agent a terminé sa tâche
            if isinstance(response, str) and "SIGNAL_TERMINATE" in response:
                final_message = response.replace("SIGNAL_TERMINATE: ", "")
                print(f"Tâche terminée: {final_message}")
                final_response = final_message
                workflow_steps.append(f"Terminaison: {final_message}")
                break
                
            # Si l'agent a utilisé un outil, traiter la réponse
            if isinstance(response, list) and len(response) > 0:
                tool_response = response[0]
                
                # Cas d'utilisation d'un outil
                if "tool_output" in tool_response:
                    tool_name = tool_response.get("tool_input", {}).get("tool_name", "outil inconnu")
                    tool_result = tool_response["tool_output"].get("content", "Résultat non disponible")
                    
                    # Enregistrer l'étape du workflow
                    workflow_steps.append(f"Utilisation de {tool_name}: {tool_result}...")
                    
                    # Cas spécial pour CREATE_METADATA_FILTER
                    if tool_name == "CREATE_METADATA_FILTER":
                        current_message = f"Filtres de métadonnées créés: {tool_result}\n\nMaintenant, sélectionne l'outil approprié pour répondre à la question originale en utilisant ces filtres."
                    # Cas spécial pour GET_JOB_ID
                    elif tool_name == "GET_JOB_ID" and "job_id" in tool_result:
                        job_id = tool_result.get("job_id")
                        current_message = f"Job ID trouvé: {job_id}\n\nMaintenant, utilise GET_JOB_DETAILS pour obtenir les informations complètes sur ce job."
                    # Cas général
                    else:
                        current_message = f"Résultat de {tool_name}: {tool_result}\n\nContinue ton analyse ou termine la tâche si tu as obtenu toutes les informations nécessaires."
                # Cas de réponse textuelle directe
                else:
                    text_response = tool_response.get("text_output", {}).get("content", {}).get("answer_text", "")
                    workflow_steps.append(f"Réponse directe: {text_response}...")
                    final_response = text_response
                    break
            else:
                # Si l'agent a répondu directement en texte
                text_response = response.get("text_output", {}).get("content", {}).get("answer_text", "")
                workflow_steps.append(f"Réponse directe: {text_response}...")
                final_response = text_response
                break
        
        if final_response is None:
            workflow_summary = "\n- ".join([""] + workflow_steps)
            final_response = f"Le processus de recherche a atteint sa limite de tours sans fournir une réponse définitive. Voici les étapes exécutées:{workflow_summary}"

        # Maintenant, que nous ayons une réponse finale ou un message d'erreur,
        # générer un résumé concis avec l'agent
        self.agent.flush_chat_history()

        workflow_summary = "\n- ".join([""] + workflow_steps)
        summarization_prompt = f"""
        Voici la question initiale de l'utilisateur : 
        "{user_query}"

        Voici les informations que nous avons pu recueillir :
        {final_response}

        Voici également un résumé des étapes de recherche effectuées :
        {workflow_summary}

        Génère un résumé concis et précis qui répond clairement à la question initiale de l'utilisateur.
        Assure-toi que ta réponse soit structurée, factuelle et directement liée à la demande.
        Ne mentionne pas les étapes de recherche, mais concentre-toi uniquement sur les informations pertinentes.
        """

        # Utiliser l'agent pour générer un résumé concis
        summarized_response = self.agent.process_text(summarization_prompt)

        # Extraire le texte de la réponse
        if isinstance(summarized_response, dict):
            if 'text_output' in summarized_response:
                text_output = summarized_response.get('text_output', {})
                if isinstance(text_output, dict):
                    final_response = text_output.get('content', {}).get('answer_text', final_response)
                else:
                    final_response = str(text_output)

        # Nettoyage de l'historique de chat pour la prochaine utilisation
        self.agent.flush_chat_history()

        return final_response

    def CHROMADB_AGENT(self, user_query, initial_metadata_dict=None, model_index=0):
        """
        Point d'entrée principal pour l'agent de recherche dans ChromaDB
        
        Args:
            user_query: Question de l'utilisateur
            initial_metadata_dict: Dictionnaire initial de métadonnées (optionnel)
            model_index: Index du modèle à utiliser (défaut: 0)
            
        Returns:
            Réponse de l'agent
        """
        tools = [
            # ... (your tools definition remains the same)
            {
                "name": "CREATE_METADATA_FILTER",
                "description": "Créer un dictionnaire de filtrage pour la recherche",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pinnokio_func": {
                            "type": "string",
                            "description": "Département concerné (APbookeeper, EXbookeeper, Bankbookeeper, HRmanager, Admanager)",
                            "enum": ["APbookeeper", "EXbookeeper", "Bankbookeeper", "HRmanager", "Admanager"]
                        },
                        "source": {
                            "type": "string",
                            "description": "Type de contenu (journal, journal/chat, context)",
                            "enum": ["journal", "journal/chat", "context"]
                        },
                        "file_name": {
                            "type": "string",
                            "description": "Nom du fichier spécifique si mentionné, sinon '<UNKNOWN>'"
                        }
                    },
                    "required": ["pinnokio_func", "source"]
                }
            },
            {
                "name": "ASK_PINNOKIO",
                "description": "Recherche simple dans la base de données vectorielle",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_query": {
                            "type": "string",
                            "description": "Question précise pour la recherche"
                        },
                        "metadata_filters": {
                            "type": "object",
                            "description": "Filtres de métadonnées pour affiner la recherche"
                        }
                    },
                    "required": ["user_query", "metadata_filters"]
                }
            },
            {
                "name": "GET_JOB_ID",
                "description": "Extraction d'un job_id à partir d'une requête",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_query": {
                            "type": "string",
                            "description": "Requête pour rechercher un job_id spécifique"
                        },
                        "metadata_filters": {
                            "type": "object",
                            "description": "Filtres de métadonnées pour affiner la recherche"
                        }
                    },
                    "required": ["user_query", "metadata_filters"]
                }
            },
            {
                "name": "GET_JOB_DETAILS",
                "description": "Obtenir les détails d'un job spécifique",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Identifiant du job (format klk-uuid)"
                        },
                        "user_query": {
                            "type": "string",
                            "description": "Question spécifique sur ce job"
                        },
                        "mode": {
                            "type": "string",
                            "description": "Mode de recherche: 'basis' ou 'accounting_tech'",
                            "enum": ["basis", "accounting_tech"]
                        }
                    },
                    "required": ["job_id", "user_query"]
                }
            },
            {
                "name": "GET_BY_FILTER",
                "description": "Recherche avancée avec filtres multiples",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_query": {
                            "type": "string",
                            "description": "Question pour la recherche"
                        },
                        "metadata_filters": {
                            "type": "object",
                            "description": "Dictionnaire de filtres de métadonnées"
                        }
                    },
                    "required": ["user_query", "metadata_filters"]
                }
            },
            {
                "name": "TASK_COMPLETE",
                "description": "Signaler la fin de la tâche de recherche",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message final résumant les résultats obtenus"
                        }
                    },
                    "required": ["message"]
                }
            }
        ]
        
        def create_metadata_filter(pinnokio_func, source, file_name="<UNKNOWN>"):
            # Strip potential whitespace from inputs, especially enum values
            pinnokio_func = pinnokio_func.strip() if isinstance(pinnokio_func, str) else pinnokio_func
            source = source.strip() if isinstance(source, str) else source
            file_name = file_name.strip() if isinstance(file_name, str) else file_name
            
            # Validate against enums if possible (though agent should handle this)
            valid_pinnokio_funcs = ["APbookeeper", "EXbookeeper", "Bankbookeeper", "HRmanager", "Admanager"]
            valid_sources = ["journal", "journal/chat", "context"]
            if pinnokio_func not in valid_pinnokio_funcs:
                print(f"Warning: Invalid pinnokio_func '{pinnokio_func}' provided to create_metadata_filter. Using APbookeeper as default.")
                # Potentially raise error or use a default
                # pinnokio_func = "APbookeeper" 
            if source not in valid_sources:
                print(f"Warning: Invalid source '{source}' provided to create_metadata_filter. Using 'journal' as default.")
                # source = "journal"

            return {
                "pinnokio_func": pinnokio_func,
                "source": source,
                "file_name": file_name
            }
        
        tool_map = {
            'CREATE_METADATA_FILTER': create_metadata_filter,
            'ASK_PINNOKIO': lambda user_query, metadata_filters: self.antho_kdb(
                user_query=user_query, 
                metadata_dict=metadata_filters, 
                model_index=model_index
            ),
            'GET_JOB_ID': lambda user_query, metadata_filters: self.antho_get_job_id_detail(
                user_query=user_query, 
                metadata_dict=metadata_filters, 
                model_index=model_index, 
                job_id=None, 
                mode='basis'
            ),
            'GET_JOB_DETAILS': lambda job_id, user_query, mode='basis': self.antho_get_job_id_detail(
                user_query=user_query, 
                metadata_dict={"pinnokio_func": "APbookeeper", "source": "journal/chat", "file_name": f"chat_{job_id}.txt"}, # Example default metadata
                model_index=model_index, 
                job_id=job_id, 
                mode=mode
            ),
            'GET_BY_FILTER': lambda user_query, metadata_filters: self.antho_get_by_filter(
                user_query=user_query, 
                metadata_dict=metadata_filters, 
                model_index=model_index
            ),
            'TASK_COMPLETE': lambda message: f"SIGNAL_TERMINATE: {message}"
        }
        
        self.AGENT_INIT() 
        
        max_turns = 10 # Max 5-7 recommended for Anthropic tools
        turn_count = 0
        
        metadata_context = ""
        if initial_metadata_dict:
            metadata_context = f"\nContexte initial de filtrage: {json.dumps(initial_metadata_dict)}"
        
        current_message = f"Nouvelle requête utilisateur: {user_query}{metadata_context}\n\nAnalyse cette requête, détermine les métadonnées de filtrage appropriées en utilisant CREATE_METADATA_FILTER si nécessaire, puis utilise l'outil le plus adapté pour répondre à la question. Si tu as la réponse, utilise TASK_COMPLETE pour terminer."
        
        workflow_steps = [f"Requête initiale: {user_query}{metadata_context}"]
        final_response = None
        
        while turn_count < max_turns:
            turn_count += 1
            print(f"--- CHROMADB_AGENT Tour {turn_count}/{max_turns} ---")
            print(f"Message à l'agent: {current_message}")
            
            # L'agent décide quel outil utiliser ou répond directement
            # process_tool_use returns a list of response blocks
            # Example: [{'tool_output': {'tool_use_id': ..., 'tool_name': ..., 'content': ...}}, {'text_output': ...}]
            # or just [{'text_output': ...}]
            agent_sdk_response_blocks = self.agent.process_tool_use(
                content=current_message,
                tools=tools,
                tool_mapping=tool_map,
                raw_output=True, # This ensures we get the structured output
            )
            
            print(f"Réponse brute de l'agent SDK (tour {turn_count}): {agent_sdk_response_blocks}")
            
            # Assume no tool will be called unless found
            next_turn_prompt_parts = [] # Accumulate parts for the next prompt to the agent
            tool_was_called_this_turn = False

            for block in agent_sdk_response_blocks:
                if "tool_output" in block:
                    tool_was_called_this_turn = True
                    tool_output_data = block["tool_output"]
                    # tool_name might be in tool_input (Anthropic SDK v2) or tool_output (some wrappers)
                    tool_name = block.get("tool_input", {}).get("tool_name", tool_output_data.get("tool_name", "outil inconnu"))
                    tool_result = tool_output_data.get("content", "Résultat non disponible")
                    tool_input_args_str = str(block.get("tool_input", {}).get("input", {}))


                    workflow_steps.append(f"Tour {turn_count}: Utilisation de {tool_name}({tool_input_args_str}) -> Résultat (premier 200 chars): {str(tool_result)[:200]}...")

                    # 1. Check for explicit termination by TASK_COMPLETE tool
                    if tool_name == "TASK_COMPLETE":
                        if isinstance(tool_result, str) and "SIGNAL_TERMINATE:" in tool_result:
                            final_response = tool_result.replace("SIGNAL_TERMINATE:", "").strip()
                            workflow_steps.append(f"Terminaison explicite par TASK_COMPLETE: {final_response}")
                            break # Break from iterating blocks

                    # 2. Check for implicit termination from *other* tools' output
                    elif isinstance(tool_result, str) and "TASK_COMPLETE" in tool_result:
                        # The sub-LLM (e.g., in antho_kdb) has signaled completion
                        final_response = tool_result.split("TASK_COMPLETE", 1)[0].strip()
                        # If there's text after TASK_COMPLETE, it might be a message for the user
                        if len(tool_result.split("TASK_COMPLETE", 1)) > 1 and tool_result.split("TASK_COMPLETE", 1)[1].strip():
                            final_response += "\n" + tool_result.split("TASK_COMPLETE", 1)[1].strip()

                        workflow_steps.append(f"Terminaison implicite (résultat de {tool_name} contenait TASK_COMPLETE): {final_response}")
                        break # Break from iterating blocks
                    
                    # 3. Process normal tool output for next turn
                    else:
                        if tool_name == "CREATE_METADATA_FILTER":
                            next_turn_prompt_parts.append(f"Filtres de métadonnées créés: {tool_result}\n\nMaintenant, sélectionne l'outil approprié pour répondre à la question originale '{user_query}' en utilisant ces filtres, ou utilise TASK_COMPLETE si tu as la réponse.")
                        elif tool_name == "GET_JOB_ID":
                            # Check if tool_result is a dictionary and contains job_id
                            if isinstance(tool_result, dict) and "job_id" in tool_result:
                                job_id = tool_result.get("job_id")
                                next_turn_prompt_parts.append(f"Job ID trouvé: {job_id}\n\nMaintenant, utilise GET_JOB_DETAILS pour obtenir les informations complètes sur ce job ({user_query}).")
                            elif isinstance(tool_result, str): # If it's a string response
                                 next_turn_prompt_parts.append(f"Résultat de GET_JOB_ID: {tool_result}\n\nAnalyse ce résultat. Si un job_id est clairement identifié, utilise GET_JOB_DETAILS. Sinon, continue ou termine.")
                            else: # Fallback for unexpected tool_result format
                                next_turn_prompt_parts.append(f"Résultat de GET_JOB_ID: {tool_result}\n\nAnalyse ce résultat. Si un job_id est clairement identifié, utilise GET_JOB_DETAILS. Sinon, continue ton analyse ou termine la tâche.")
                        else: # ASK_PINNOKIO, GET_JOB_DETAILS, GET_BY_FILTER
                            next_turn_prompt_parts.append(f"Résultat de {tool_name}: {tool_result}\n\nContinue ton analyse de la requête '{user_query}' ou termine la tâche avec TASK_COMPLETE si tu as obtenu toutes les informations nécessaires.")
                
                elif "text_output" in block:
                    text_content = block.get("text_output", {}).get("content", {}).get("answer_text", "")
                    if text_content: # Only add if there's actual text
                        workflow_steps.append(f"Tour {turn_count}: Réponse textuelle de l'agent: {text_content}")
                        # If this is the *only* kind of response (no tool call), it might be the final answer
                        # But usually, text_output accompanies a tool_use or is a thinking step.
                        next_turn_prompt_parts.append(f"L'agent a aussi dit: {text_content}\nContinue la tâche pour '{user_query}'.")

            if final_response is not None:
                print(f"Tâche terminée dans le tour {turn_count}.")
                break # Break from the main while loop

            if not next_turn_prompt_parts:
                 # This case can happen if agent returns empty response or unexpected format
                if tool_was_called_this_turn: # A tool was called but didn't fit specific handling to form next prompt
                    current_message = f"L'outil a été exécuté. Analyse la situation pour la requête '{user_query}' et décide de la prochaine étape ou utilise TASK_COMPLETE."
                else: # No tool called, no text output with content
                    workflow_steps.append(f"Tour {turn_count}: L'agent n'a pas appelé d'outil ni fourni de texte significatif.")
                    current_message = f"Ta dernière réponse n'était pas claire. Pour la requête '{user_query}', réessaie d'utiliser un outil ou termine avec TASK_COMPLETE si tu as une réponse."
            else:
                current_message = "\n".join(next_turn_prompt_parts)

        if final_response is None:
            workflow_summary = "\n- ".join([""] + workflow_steps)
            final_response = f"Le processus de recherche a atteint sa limite de {max_turns} tours sans fournir une réponse définitive via TASK_COMPLETE. Voici les étapes exécutées:{workflow_summary}"
            workflow_steps.append(f"Terminaison: Limite de tours atteinte.")

        # Summarization step
        self.agent.flush_chat_history() # Good practice
        
        # Construct a clear summary of the workflow for the summarizer
        # Avoid overly long workflow_steps in the prompt if they are too verbose
        summarized_workflow_for_prompt = "\n- ".join([""] + [s[:500] + "..." if len(s) > 500 else s for s in workflow_steps])


        summarization_prompt = f"""
        La question initiale de l'utilisateur était : 
        "{user_query}"

        Voici les informations finales recueillies par l'agent de recherche après plusieurs étapes :
        "{final_response}"

        Voici un résumé des étapes de recherche effectuées (utile pour le contexte, mais ne pas répéter dans la réponse finale) :
        {summarized_workflow_for_prompt}

        En te basant PRINCIPALEMENT sur les "informations finales recueillies", génère une réponse concise, claire et factuelle à la question initiale de l'utilisateur.
        Si les informations finales indiquent un échec ou une absence de résultats, explique cela poliment.
        Ne mentionne PAS explicitement les outils utilisés ou le processus de recherche interne à moins que ce ne soit crucial pour expliquer pourquoi une réponse n'a pas pu être trouvée.
        Concentre-toi sur la fourniture d'une réponse directe.
        Si la réponse finale est déjà une explication d'échec (par exemple, "Aucune facture trouvée..."), reformule-la pour qu'elle soit une réponse directe et polie à l'utilisateur.
        """
        print(f"\n--- PROMPT DE SYNTHESE --- \n{summarization_prompt}\n-------------------------\n")

        summarized_answer_obj = self.agent.process_text(summarization_prompt) # model_index is handled by AnthoAgent
        
        final_summarized_text = final_response # Fallback to pre-summary response

        if isinstance(summarized_answer_obj, dict):
            if 'text_output' in summarized_answer_obj:
                text_output_content = summarized_answer_obj.get('text_output', {}).get('content', {})
                if isinstance(text_output_content, dict):
                    final_summarized_text = text_output_content.get('answer_text', final_summarized_text)
                elif isinstance(text_output_content, str): # Sometimes content might be a direct string
                    final_summarized_text = text_output_content
            # If the structure is simpler, e.g. directly {'answer_text': '...'}
            elif 'answer_text' in summarized_answer_obj:
                 final_summarized_text = summarized_answer_obj.get('answer_text', final_summarized_text)

        elif isinstance(summarized_answer_obj, str): # If process_text returns a direct string
            final_summarized_text = summarized_answer_obj

        print(f"--- RÉPONSE FINALE SYNTHÉTISÉE --- \n{final_summarized_text}\n------------------------------\n")
        
        self.agent.flush_chat_history()
        return final_summarized_text

class TextStreamer:
    def __init__(self, chunk_size: int = 4, delay: float = 0.05):
        """
        Initialise le streamer de texte.
        
        Args:
            chunk_size: Nombre de mots par fragment
            delay: Délai en secondes entre chaque fragment
        """
        self.chunk_size = chunk_size
        self.delay = delay

    async def stream_text(self, text: str) -> AsyncGenerator[str, None]:
        """
        Transforme un texte en flux streaming.
        
        Args:
            text: Le texte à streamer
            
        Yields:
            str: Les fragments de texte
        """
        if not text:
            return

        words = text.split()
        current_chunk = []
        current_size = 0

        for word in words:
            current_chunk.append(word)
            current_size += 1
            
            if current_size >= self.chunk_size:
                yield ' '.join(current_chunk) + ' '
                current_chunk = []
                current_size = 0
                await asyncio.sleep(self.delay)
        
        # Envoie le dernier fragment s'il en reste
        if current_chunk:
            yield ' '.join(current_chunk)


def get_current_weather(location: str) -> str:
    """Obtient la météo actuelle pour une localisation donnée.
    
    Args:
        location: La ville et le pays, ex: Paris, FR
    """
    return f"Il fait beau à {location}"

def get_current_time(city: str) -> str:
    """Obtient l'heure actuelle pour une ville.
    
    Args:
        city: La ville, ex: Tokyo, JP
    """
    return f"Il est 14h00 à {city}"

gemini_tool= [
    get_current_weather,  # Fonction callable
    get_current_time,     # Fonction callable
    {
        'name': 'get_population',
        'description': 'Obtenir la population d\'une ville',
        'parameters': {
            'type': 'OBJECT',
            'properties': {
                'city': {
                    'type': 'STRING',
                    'description': 'La ville, ex: Paris, FR',
                }
            },
            'required': ['city']
        }
    }
]

tool=[{
    "name": "GET_YES_OR_NO",
    "description": "Extrait le nom d'un contact",
    "input_schema": {
        "type": "object",
        "properties": {
            "yes_or_no": {
                "type": "string",
                "enum": ["YES", "NO"],
                "description": "Répondre clairement par YES ou NO à une question précise"
            },
            "justification": {
                "type": "string",
                "description": "Explication sur les motivations ayant amené à ce choix"
            }
        },
        "required": ["yes_or_no"]
    }
},
{
    "name": "get_current_weather",
    "description": "Pour prendre les information de méteo",
    "input_schema": {
        "type": "object",
        "properties": {
            
            "location": {
                "type": "string",
                "description": "nom de ville ou de pays ou prendre les information sur la méteo"
            }
        },
        "required": ["location"]
    }
}]



tool_map=[{'GET_YES_OR_NO':None},{'get_current_weather':get_current_weather}]
tool_choice={"type": "auto"}
# Initialisation
collection_name='AAAAd8qX9nA'
user_mail='cedric.gacond@klkvision.tech'

'''base_agent.register_provider(ModelProvider.ANTHROPIC, anthropic_instance)
base_agent.register_provider(ModelProvider.OPENAI,openai_instance)
base_agent.register_provider(ModelProvider.GEMINI,gemini_instance)
base_agent.register_provider(ModelProvider.DEEP_SEEK,deepseek_instance)
base_agent.register_provider(ModelProvider.PERPLEXITY,perplex_agent)

system_prompt=f"""Tu es un aide comptable.... """
base_agent.update_system_prompt(system_prompt)

base_drive_item_id=['1OHuJ4w_i54bjEzOHb00-yGhyGG7hRzqZ']
# Traitement de texte
response,citations = base_agent.process_search(
    content="Donne moi définition de ce qui peut etre attribué a 'current_asset' selon les statuts comptable Suisse statutaires",
    provider=ModelProvider.PERPLEXITY,
    size=ModelSize.SMALL,

    
    )


print(f"impression de response:{response}")
print(f"impression de citations:{citations}")
test=base_agent.get_token_usage_by_provider()
print(test)
response,citations = base_agent.process_search(
    content="Dis moi stp le pays sur lequel tu as fait ta recherche et me dire aussi ce que tu as recherché et si tu estime que tes sources sont fiables",
    provider=ModelProvider.PERPLEXITY,
    size=ModelSize.SMALL,

    
    )
print(f"impression de response:{response}")
print(f"impression de citations:{citations}")'''


'''local_path = "C:/Users/Cedri/Coding/pinnokio_app/assets/invoice_stat.png"
with open(local_path, 'rb') as f:
    image_data = f.read()
response=gemini_instance.process_vision(text="Que voyez-vous sur cette image ?",images=image_data,model_index=2)
tokken_coutn=gemini_instance.get_total_tokens()
print(response)'''
'''response2=base_agent.process_text(content="Ok alors si c'est la nuit à Tokyo, qu'elle a la couleur du ciel a Loas Angels....",
                                 provider=ModelProvider.OPENAI,
                                size=ModelSize.SMALL,

                            )
print(response2)
token1=base_agent.get_token_usage_by_provider()'''
#token=base_agent.chat_history
#print(f"impression de chat_history:{token1}")

# Utilisation d'outils
'''response = base_agent.process_tool_use(
    content="Quel est la température a Honk Kong",
    tools=tool,
    tool_mapping=tool_map,
    provider=ModelProvider.OPENAI,
    size=ModelSize.REASONING_MEDIUM
)
'''
#print(f"impression de response :{response}")

'''
tools = [{
    "name": "VIEW_DOCUMENT_WITH_VISION",
    "description": "Le document png est initialisé cette fonction permet de poser des question sur le document visionné",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Ce champs est destiné a être utilisé pour poser les questions nécaissaire sur la recherche d'information"
            }
        },
        "required": ["text"] 
    }
},]
fim='AAAAd8qX9nA'
agent=Anthropic_Agent()
service_account_info = json.loads(get_secret('service_account_key'))
user_mail='cedric.gacond@klkvision.tech'
base_drive_item_id='1QnBe-ai7eroeQHst-_zM9RJdFhfRcrR1'
pinnokio_tooling=PINNOKIO_TOOLS(fim,service_account_info,user_mail,agent)
#departements=PINNOKIO_DEPARTEMENTS()
view_doc = partial(pinnokio_tooling.analyse_multipage_docs_async,
                  file_ids=[base_drive_item_id],
                  model_index=0)
tool_map={'VIEW_DOCUMENT_WITH_VISION':view_doc}
tool_choice={"type": "auto"}
async def chat():
    antho = Anthropic_Agent()
    system_prompt="""Tu es un agent intelligent dédié à l'automatisation des tâches comptables, 
    ton nom est Pinnokio. Ton expertise inclut la saisie automatisée de factures, la réconciliation bancaire et le dispatch 
    de documents. Tu comprends le contexte économique et comptable de chaque entreprise pour offrir des solutions personnalisées.
    Prépare-toi à recevoir des informations sur le client pour commencer ta mission."""
    tools_description=""" Tu dispose des outils suivants:
    VIEW_DOCUMENT_WITH_VISION te permet de visualiser un document"""
    final_prompt=f"""Mission:{system_prompt}\n outils:{tools_description} """
    antho.update_system_prompt(final_prompt)
    print("Démarrage du chat (tapez 'TERMINATE' pour quitter)")
    print("-" * 50)
    
    while True:
        # Récupérer l'entrée utilisateur
        prompt = input("\nVous: ")
        
        # Vérifier si l'utilisateur veut terminer
        if prompt.upper() == "TERMINATE":
            print("\nFin du chat. Au revoir!")
            break
            
        # Obtenir et afficher la réponse de Claude
        print("\nClaude:", end=' ', flush=True)
        
        async_gen = antho.anthropic_send_message_tool_stream(prompt, 0,tool_list=tools,tool_mapping=tool_map,tool_choice=tool_choice)
        
        try:
            async for chunk in async_gen:
                print(str(chunk), end='', flush=True)
            print("\n" + "-" * 50)  # Séparateur entre les messages
            
        except Exception as e:
            print(f"\nErreur lors de l'itération : {e}")

if __name__ == "__main__":
    asyncio.run(chat())'''



'''# Exemple d'utilisation
async def main():
    # Initialisation des composants
    base_agent = BaseAIAgent()
    deepseek_instance = DeepSeek_agent()
    base_agent.register_provider(ModelProvider.DEEP_SEEK, deepseek_instance)
    streamer = TextStreamer(chunk_size=4, delay=0.05)
    
    # Obtention de la réponse via BaseAIAgent
    response = base_agent.process_text(
        content="Selon quel est la raison qu'un pays aussi riche que la RDC soit si pauvre",
        size=ModelSize.REASONING_MEDIUM,
        provider=ModelProvider.DEEP_SEEK
    )
    
    # Extraction du texte de la réponse
    if isinstance(response, dict):
        if 'text_output' in response:
            text = response.get('text_output', '')
        else:
            text = str(response)
    else:
        text = str(response)
    
    # Streaming du texte
    async for chunk in streamer.stream_text(text):
        print(chunk, end='', flush=True)
    print()  # Nouvelle ligne à la fin

if __name__ == "__main__":
    asyncio.run(main())'''