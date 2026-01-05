"""
Module pour extraire les logs CloudWatch du groupe de journaux ECS.

Ce module fournit une interface pour:
- Lister les streams de logs avec leurs dates
- Télécharger un log complet
"""

import boto3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import os
import json


class CloudWatchLogsExtractor:
    """
    Classe pour extraire les logs depuis CloudWatch Logs.
    
    Utilise le groupe de journaux: /ecs/pinnokio_microservice
    """
    
    def __init__(self, region_name: str = 'us-east-1', log_group_name: str = '/ecs/pinnokio_microservice',
                 aws_access_key_id: Optional[str] = None,
                 aws_secret_access_key: Optional[str] = None,
                 aws_session_token: Optional[str] = None):
        """
        Initialise l'extracteur de logs CloudWatch.
        
        Args:
            region_name: La région AWS (par défaut: us-east-1)
            log_group_name: Le nom du groupe de journaux (par défaut: /ecs/pinnokio_microservice)
            aws_access_key_id: Clé d'accès AWS (optionnel, utilise les credentials par défaut si non fourni)
            aws_secret_access_key: Clé secrète AWS (optionnel, utilise les credentials par défaut si non fourni)
            aws_session_token: Token de session AWS (optionnel, pour les credentials temporaires)
        
        Note:
            Si les credentials ne sont pas fournis, boto3 utilisera automatiquement:
            1. Variables d'environnement (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
            2. Fichier ~/.aws/credentials
            3. IAM Role (si sur EC2/ECS)
        """
        self.region_name = region_name
        self.log_group_name = log_group_name
        
        # Créer le client boto3 avec ou sans credentials explicites
        if aws_access_key_id and aws_secret_access_key:
            self.client = boto3.client(
                'logs',
                region_name=region_name,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token
            )
        else:
            # Utilise les credentials par défaut de boto3 (env vars, fichier, IAM role)
            self.client = boto3.client('logs', region_name=region_name)
    
    def list_log_streams(self, 
                         limit: Optional[int] = None,
                         order_by: str = 'LastEventTime',
                         descending: bool = True,
                         start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None) -> List[Dict]:
        """
        Liste les streams de logs avec leurs dates.
        
        Args:
            limit: Nombre maximum de streams à retourner (None = tous)
            order_by: Critère de tri ('LastEventTime' ou 'LogStreamName')
            descending: True pour tri décroissant, False pour croissant
            start_time: Filtrer les streams après cette date (optionnel)
            end_time: Filtrer les streams avant cette date (optionnel)
        
        Returns:
            Liste de dictionnaires contenant:
            - logStreamName: nom du stream
            - creationTime: timestamp de création (ms)
            - firstEventTimestamp: timestamp du premier événement (ms)
            - lastEventTimestamp: timestamp du dernier événement (ms)
            - storedBytes: taille en bytes
        """
        try:
            kwargs = {
                'logGroupName': self.log_group_name,
                'orderBy': order_by,
                'descending': descending
            }
            
            if start_time:
                kwargs['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                kwargs['endTime'] = int(end_time.timestamp() * 1000)
            
            streams = []
            paginator = self.client.get_paginator('describe_log_streams')
            
            for page in paginator.paginate(**kwargs):
                for stream in page.get('logStreams', []):
                    stream_info = {
                        'logStreamName': stream.get('logStreamName'),
                        'creationTime': stream.get('creationTime'),
                        'creationTimeFormatted': self._format_timestamp(stream.get('creationTime')),
                        'firstEventTimestamp': stream.get('firstEventTimestamp'),
                        'firstEventTimeFormatted': self._format_timestamp(stream.get('firstEventTimestamp')),
                        'lastEventTimestamp': stream.get('lastEventTimestamp'),
                        'lastEventTimeFormatted': self._format_timestamp(stream.get('lastEventTimestamp')),
                        'storedBytes': stream.get('storedBytes', 0)
                    }
                    streams.append(stream_info)
                    
                    if limit and len(streams) >= limit:
                        return streams
            
            return streams
        
        except self.client.exceptions.ResourceNotFoundException:
            print(f"Le groupe de journaux '{self.log_group_name}' n'existe pas.")
            return []
        except Exception as e:
            print(f"Erreur lors de la récupération des streams: {e}")
            raise
    
    def download_log(self, 
                    log_stream_name: str,
                    output_file: Optional[str] = None,
                    start_time: Optional[datetime] = None,
                    end_time: Optional[datetime] = None) -> str:
        """
        Télécharge un log complet depuis un stream.
        
        Args:
            log_stream_name: Le nom du stream de logs à télécharger
            output_file: Chemin du fichier de sortie (optionnel, généré automatiquement si None)
            start_time: Filtrer les événements après cette date (optionnel)
            end_time: Filtrer les événements avant cette date (optionnel)
        
        Returns:
            Le chemin du fichier créé
        """
        try:
            kwargs = {
                'logGroupName': self.log_group_name,
                'logStreamName': log_stream_name
            }
            
            if start_time:
                kwargs['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                kwargs['endTime'] = int(end_time.timestamp() * 1000)
            
            # Récupérer tous les événements (avec pagination)
            all_events = []
            next_token = None
            
            while True:
                if next_token:
                    kwargs['nextToken'] = next_token
                
                response = self.client.get_log_events(**kwargs)
                events = response.get('events', [])
                all_events.extend(events)
                
                next_token = response.get('nextForwardToken')
                if not next_token or not events:
                    break
                
                # Éviter la boucle infinie si le token ne change pas
                if 'nextToken' in kwargs and kwargs['nextToken'] == next_token:
                    break
            
            # Générer le nom de fichier si non fourni
            if not output_file:
                safe_stream_name = log_stream_name.replace('/', '_').replace('\\', '_')
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_file = f"logs/{safe_stream_name}_{timestamp}.log"
            
            # Créer le répertoire si nécessaire
            os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
            
            # Écrire les logs dans le fichier
            with open(output_file, 'w', encoding='utf-8') as f:
                for event in all_events:
                    timestamp = self._format_timestamp(event.get('timestamp'))
                    message = event.get('message', '')
                    f.write(f"[{timestamp}] {message}\n")
            
            print(f"Log téléchargé: {output_file} ({len(all_events)} événements)")
            return output_file
        
        except self.client.exceptions.ResourceNotFoundException:
            raise Exception(f"Le stream '{log_stream_name}' n'existe pas dans le groupe '{self.log_group_name}'")
        except Exception as e:
            print(f"Erreur lors du téléchargement du log: {e}")
            raise
    
    def download_log_json(self,
                          log_stream_name: str,
                          output_file: Optional[str] = None,
                          start_time: Optional[datetime] = None,
                          end_time: Optional[datetime] = None) -> str:
        """
        Télécharge un log complet au format JSON.
        
        Args:
            log_stream_name: Le nom du stream de logs à télécharger
            output_file: Chemin du fichier de sortie (optionnel)
            start_time: Filtrer les événements après cette date (optionnel)
            end_time: Filtrer les événements avant cette date (optionnel)
        
        Returns:
            Le chemin du fichier créé
        """
        try:
            kwargs = {
                'logGroupName': self.log_group_name,
                'logStreamName': log_stream_name
            }
            
            if start_time:
                kwargs['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                kwargs['endTime'] = int(end_time.timestamp() * 1000)
            
            # Récupérer tous les événements (avec pagination)
            all_events = []
            next_token = None
            
            while True:
                if next_token:
                    kwargs['nextToken'] = next_token
                
                response = self.client.get_log_events(**kwargs)
                events = response.get('events', [])
                all_events.extend(events)
                
                next_token = response.get('nextForwardToken')
                if not next_token or not events:
                    break
                
                # Éviter la boucle infinie
                if 'nextToken' in kwargs and kwargs['nextToken'] == next_token:
                    break
            
            # Générer le nom de fichier si non fourni
            if not output_file:
                safe_stream_name = log_stream_name.replace('/', '_').replace('\\', '_')
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_file = f"logs/{safe_stream_name}_{timestamp}.json"
            
            # Créer le répertoire si nécessaire
            os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
            
            # Écrire les logs au format JSON
            log_data = {
                'logGroupName': self.log_group_name,
                'logStreamName': log_stream_name,
                'exportedAt': datetime.now().isoformat(),
                'totalEvents': len(all_events),
                'events': [
                    {
                        'timestamp': event.get('timestamp'),
                        'timestampFormatted': self._format_timestamp(event.get('timestamp')),
                        'message': event.get('message', '')
                    }
                    for event in all_events
                ]
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            
            print(f"Log JSON téléchargé: {output_file} ({len(all_events)} événements)")
            return output_file
        
        except Exception as e:
            print(f"Erreur lors du téléchargement du log JSON: {e}")
            raise
    
    def _format_timestamp(self, timestamp_ms: Optional[int]) -> str:
        """
        Formate un timestamp en millisecondes en chaîne lisible.
        
        Args:
            timestamp_ms: Timestamp en millisecondes
        
        Returns:
            Chaîne formatée (ISO format) ou None si timestamp est None
        """
        if timestamp_ms is None:
            return "N/A"
        dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    
    def get_log_group_info(self) -> Dict:
        """
        Récupère les informations sur le groupe de journaux.
        
        Returns:
            Dictionnaire avec les informations du groupe
        """
        try:
            response = self.client.describe_log_groups(
                logGroupNamePrefix=self.log_group_name
            )
            
            groups = response.get('logGroups', [])
            if groups:
                group = groups[0]
                return {
                    'logGroupName': group.get('logGroupName'),
                    'creationTime': group.get('creationTime'),
                    'creationTimeFormatted': self._format_timestamp(group.get('creationTime')),
                    'storedBytes': group.get('storedBytes', 0),
                    'retentionInDays': group.get('retentionInDays'),
                    'metricFilterCount': group.get('metricFilterCount', 0)
                }
            else:
                return {}
        except self.client.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'AccessDeniedException':
                raise Exception("Accès refusé. Vérifiez vos permissions AWS et vos credentials.")
            elif error_code == 'InvalidParameterException':
                raise Exception(f"Paramètre invalide: {e}")
            else:
                raise Exception(f"Erreur AWS: {e}")
        except Exception as e:
            error_msg = str(e)
            if 'Unable to locate credentials' in error_msg or 'NoCredentialsError' in error_msg:
                raise Exception(
                    "Credentials AWS non trouvés. Configurez-les via:\n"
                    "  - Variables d'environnement (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)\n"
                    "  - Fichier ~/.aws/credentials\n"
                    "  - IAM Role (si sur EC2/ECS)"
                )
            raise Exception(f"Erreur lors de la récupération des informations du groupe: {e}")
    
    def check_credentials(self) -> bool:
        """
        Vérifie que les credentials AWS sont valides en tentant une opération simple.
        
        Returns:
            True si les credentials sont valides, False sinon
        """
        try:
            # Tente une opération simple qui nécessite des credentials
            self.client.describe_log_groups(limit=1)
            return True
        except self.client.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'AccessDeniedException':
                print("✗ Credentials trouvés mais permissions insuffisantes")
                return False
            return False
        except Exception as e:
            error_msg = str(e)
            if 'Unable to locate credentials' in error_msg or 'NoCredentialsError' in error_msg:
                print("✗ Aucun credential AWS trouvé")
                return False
            print(f"✗ Erreur de vérification: {e}")
            return False

