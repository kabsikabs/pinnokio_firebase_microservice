
import requests
from .chroma_vector_proxy import get_chroma_vector_proxy
from .erp_config import ODOO_KLK_VISION
from .aws_service import AWSManager
from .g_cred import FireBaseManagement,DriveClientService,get_secret
from .klk_agents import NEW_Anthropic_Agent,Anthropic_KDB_AGENT,BaseAIAgent,NEW_OpenAiAgent,NEW_GeminiAgent,ModelProvider,ModelSize
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor
from .onboarding_creation import DMS_CREATION
from functools import partial
import asyncio
import reflex as rx
import boto3
import json
import aiohttp
import os
import yaml
from dotenv import load_dotenv
load_dotenv()

class PINNOKIO_DEPARTEMENTS:
    def __init__(self):
        self.aws_service=AWSManager()
    async def run_pinnokio_router(self,payload, source=None, check_health=False,mthd='default'):
        # URL du point de terminaison pour les différentes sources
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        if mthd == 'single':
            final_payload = payload  # Utilise directement le dictionnaire fourni
        else:  # méthode par défaut
            collection_name=payload.get('collection_name')
            final_payload = {'collection_name': collection_name}  # ici payload est traité comme collection_id
        
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8080'

        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # URL du point de terminaison pour l'event-trigger et la santé selon la source
        url_event_trigger = f'{base_url}/event-trigger'
        url_health_check = f'{base_url}/health'

        # Si check_health est True, on fait une requête GET pour vérifier la santé de l'application
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(url_health_check) as response:
                    status = response.status
                    text = await response.text()
                    
                    if status == 200:
                        print('Health Check réussi !')
                        print('Réponse :', text)
                    else:
                        print('Erreur lors du Health Check :', status)
                    return {'status': status, 'text': text}

        # Sinon, on envoie une requête POST avec des données au point de terminaison /event-trigger
        
        print(f"Envoi de la requête POST avec payload {final_payload} à {url_event_trigger}...")
        async with aiohttp.ClientSession() as session:
            async with session.post(url_event_trigger, json=final_payload) as response:
                status = response.status
                try:
                    json_response = await response.json()
                except:
                    json_response = {}
                    text = await response.text()
                    print("Réponse non-JSON:", text)
                
                # Vérifie que la requête a réussi
                if status == 202:
                    print('Requête POST réussie !')
                    print('Réponse :', json_response)
                    return {'status': status, 'json': json_response, 'success': True}
                else:
                    print('Erreur lors de la requête POST :', status)
                    return {'status': status, 'json': json_response, 'success': False}

    
    async def stop_pinnokio_router(self,payload, source=None, check_health=False):
        """
        Envoie un signal d'arrêt à l'application Pinnokio APBookkeeper en cours
        """
        # Configuration des URLs
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8080'
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        elif source == 'ecs':
            base_url = aws_url  # Utilise l'URL du load balancer pour ECS
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws', 'local' ou 'ecs'.")

        # URLs des endpoints
        url_stop = f'{base_url}/stop_router'
        url_health_check = f'{base_url}/health'

        # Vérification de la santé si demandée
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(url_health_check) as response:
                    status = response.status
                    text = await response.text()
                    
                    if status == 200:
                        print('Health Check réussi !')
                        print('Réponse :', text)
                    else:
                        print('Erreur lors du Health Check :', status)
                    return {'status': status, 'text': text}

        # Envoi de la requête d'arrêt (sans payload)
        print(f"Envoi de la requête d'arrêt à {url_stop}...")
        async with aiohttp.ClientSession() as session:
            async with session.post(url_stop, json=payload) as response:
                status = response.status
                try:
                    json_response = await response.json()
                except:
                    json_response = {}
                    text = await response.text()
                    print("Réponse non-JSON:", text)
                
                if status == 200:
                    print('Requête d\'arrêt réussie !')
                    print('Réponse :', json_response)
                    return {'status': status, 'message': "L'ordre d'arrêt a été exécuté correctement...", 'success': True}
                else:
                    error_message = f"Erreur lors de la requête d'arrêt : {status}"
                    print(error_message)
                    return {'status': status, 'message': error_message, 'success': False}
    
    async def async_run_pinnokio_onboarding(self,payload, source=None, check_health=False,mthd='default'):
        # URL du point de terminaison pour les différentes sources
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        if mthd == 'single':
            final_payload = payload  # Utilise directement le dictionnaire fourni
        else:  # méthode par défaut
            collection_name=payload.get('collection_name')
            final_payload = {'collection_name': collection_name}  # ici payload est traité comme collection_id
        
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8080'

        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # URL du point de terminaison pour l'onboarding_manager_agent et la santé selon la source
        url_event_trigger = f'{base_url}/onboarding_manager_agent'
        url_health_check = f'{base_url}/health'

        # Si check_health est True, on fait une requête GET pour vérifier la santé de l'application
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(url_health_check) as response:
                    status = response.status
                    text = await response.text()
                    
                    if status == 200:
                        print('Health Check réussi !')
                        print('Réponse :', text)
                    else:
                        print('Erreur lors du Health Check :', status)
                    return {'status': status, 'text': text}

        # Sinon, on envoie une requête POST avec des données au point de terminaison /event-trigger
        
        print(f"Envoi de la requête POST avec payload {final_payload} à {url_event_trigger}...")
        async with aiohttp.ClientSession() as session:
            async with session.post(url_event_trigger, json=final_payload) as response:
                status = response.status
                try:
                    json_response = await response.json()
                except:
                    json_response = {}
                    text = await response.text()
                    print("Réponse non-JSON:", text)
                
                # Vérifie que la requête a réussi
                if status in [200, 202]:
                    print('Requête POST réussie !')
                    print('Réponse :', json_response)
                    return {'status': status, 'json': json_response, 'success': True}
                else:
                    print('Erreur lors de la requête POST :', status)
                    return {'status': status, 'json': json_response, 'success': False}

    
    def run_pinnokio_onboarding(self, payload, source=None, check_health=False, mthd='default'):
        """Version synchrone de run_pinnokio_onboarding utilisant requests au lieu de aiohttp"""
        import requests
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        # URL du point de terminaison pour les différentes sources
        if mthd == 'single':
            final_payload = payload  # Utilise directement le dictionnaire fourni
        else:  # méthode par défaut
            collection_name = payload.get('collection_name')
            final_payload = {'collection_name': collection_name}  # ici payload est traité comme collection_id
        
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8080'

        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # URL du point de terminaison pour l'onboarding_manager_agent et la santé selon la source
        url_event_trigger = f'{base_url}/onboarding_manager_agent'
        url_health_check = f'{base_url}/health'

        # Si check_health est True, on fait une requête GET pour vérifier la santé de l'application
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            try:
                response = requests.get(url_health_check)
                status = response.status_code
                text = response.text
                
                if status == 200:
                    print('Health Check réussi !')
                    print('Réponse :', text)
                else:
                    print('Erreur lors du Health Check :', status)
                return {'status': status, 'text': text}
            except Exception as e:
                print(f"Erreur lors du Health Check: {e}")
                return {'status': 500, 'text': str(e), 'success': False}

        # Sinon, on envoie une requête POST avec des données au point de terminaison /event-trigger
        print(f"Envoi de la requête POST avec payload {final_payload} à {url_event_trigger}...")
        try:
            response = requests.post(url_event_trigger, json=final_payload, timeout=10)
            status = response.status_code
            
            try:
                json_response = response.json()
            except:
                json_response = {}
                text = response.text
                print("Réponse non-JSON:", text)
            
            # Vérifie que la requête a réussi
            if status in [200, 202]:
                print('Requête POST réussie !')
                print('Réponse :', json_response)
                return {'status': status, 'json': json_response, 'success': True}
            else:
                print('Erreur lors de la requête POST :', status)
                return {'status': status, 'json': json_response, 'success': False}
        except Exception as e:
            print(f"Erreur lors de la requête POST: {e}")
            return {'status': 500, 'json': {'message': str(e)}, 'success': False}

    def stop_pinnokio_onboarding(self, payload, source=None, job_id=None, mthd='default'):
        """
        Arrête un job d'onboarding en cours d'exécution.
        
        Args:
            payload (dict): Données contenant les informations du job à arrêter
            source (str, optional): Source de l'environnement ('docker', 'aws', 'local')
            job_id (str, optional): ID du job à arrêter, peut être fourni directement
            mthd (str, optional): Méthode de traitement du payload ('single' ou 'default')
            
        Returns:
            dict: Résultat de la requête d'arrêt avec statut et réponse
        """
        import requests
        import os
        
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        
        # Préparation du payload final
        if mthd == 'single':
            final_payload = payload  # Utilise directement le dictionnaire fourni
        else:  # méthode par défaut
            user_id = payload.get('user_id')
            job_id_from_payload = payload.get('job_id') or job_id
            
            if not job_id_from_payload:
                raise ValueError("L'ID du job (job_id) doit être fourni soit dans le payload, soit comme paramètre.")
            
            final_payload = {
                'user_id': user_id,
                'job_ids': [job_id_from_payload]
            }
        
        # Définition des URLs selon l'environnement
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8080'

        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # Construction de l'URL du point de terminaison pour arrêter le job d'onboarding
        job_id_for_url = job_id or payload.get('job_id')
        url_stop_job = f'{base_url}/stop-onboarding/{job_id_for_url}'
        
        print(f"Envoi de la requête POST pour arrêter le job {job_id_for_url} avec payload {final_payload} à {url_stop_job}...")
        
        try:
            # Envoi de la requête POST pour arrêter le job
            response = requests.post(url_stop_job, json=final_payload, timeout=10)
            status = response.status_code
            
            try:
                json_response = response.json()
            except:
                json_response = {}
                text = response.text
                print("Réponse non-JSON:", text)
            
            # Vérifie que la requête a réussi
            if status in [200, 202]:
                print('Requête d\'arrêt réussie !')
                print('Réponse :', json_response)
                return {'status': status, 'json': json_response, 'success': True}
            else:
                print('Erreur lors de la requête d\'arrêt :', status)
                return {'status': status, 'json': json_response, 'success': False}
        except Exception as e:
            print(f"Erreur lors de la requête d'arrêt: {e}")
            return {'status': 500, 'json': {'message': str(e)}, 'success': False}


    def run_pinnokio_apbookeeper(self, payload, source=None, check_health=False, mthd='default'):
        # URL du point de terminaison pour les différentes sources
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
         # Traitement du payload selon la méthode
        if mthd == 'single':
            final_payload = payload  # Utilise directement le dictionnaire fourni
        else:  # méthode par défaut
            collection_name=payload.get('collection_name')
            final_payload = {'collection_name': collection_name}  # ici payload est traité comme collection_id
        
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8081'
        

        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        elif source=='ecs':
            task_id=self.aws_service.run_task(final_payload)
            return task_id
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # URL du point de terminaison pour l'event-trigger et la santé selon la source
        url_event_trigger = f'{base_url}/apbookeeper-event-trigger'
        url_health_check = f'{base_url}/health'

        # Si check_health est True, on fait une requête GET pour vérifier la santé de l'application
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            response = requests.get(url_health_check)
            if response.status_code == 200:
                print('Health Check réussi !')
                print('Réponse :', response.text)
            else:
                print('Erreur lors du Health Check :', response.status_code)
            return response

       

        print(f"Envoi de la requête POST avec payload {final_payload} à {url_event_trigger}...")
        response = requests.post(url_event_trigger, json=final_payload)

        # Vérifie que la requête a réussi
        if response.status_code == 202:
            print('Requête POST réussie !')
            print('Réponse :', response.json())
            return response.json()
        else:
            return response.json()

    async def stop_pinnokio_apbookeeper(self,payload, source=None, check_health=False):
        """
        Envoie un signal d'arrêt à l'application Pinnokio APBookkeeper en cours
        """
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        # Configuration des URLs
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8081'
        
        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        elif source == 'ecs':
            base_url = aws_url  # Utilise l'URL du load balancer pour ECS
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws', 'local' ou 'ecs'.")

        # URLs des endpoints
        url_stop = f'{base_url}/stop_apbookeeper'
        url_health_check = f'{base_url}/health'

        # Vérification de la santé si demandée
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            response = requests.get(url_health_check)
            if response.status_code == 200:
                print('Health Check réussi !')
                print('Réponse :', response.text)
            else:
                print('Erreur lors du Health Check :', response.status_code)
            return response

        # Envoi de la requête d'arrêt (sans payload)
        print(f"Envoi de la requête d'arrêt à {url_stop}...")
        async with aiohttp.ClientSession() as session:
            async with session.post(url_stop, json=payload) as response:
                status = response.status
                try:
                    json_response = await response.json()
                except:
                    json_response = {}
                    text = await response.text()
                    print("Réponse non-JSON:", text)
                
                if status == 200:
                    print('Requête d\'arrêt réussie !')
                    print('Réponse :', json_response)
                    return {'status': status, 'message': "L'ordre d'arrêt a été exécuté correctement...", 'success': True}
                else:
                    error_message = f"Erreur lors de la requête d'arrêt : {status}"
                    print(error_message)
                    return {'status': status, 'message': error_message, 'success': False}
        
    
    def run_pinnokio_banker(self,payload, source=None, check_health=False,mthd='default'):
        
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
         # Traitement du payload selon la méthode
        if mthd == 'single':
            final_payload = payload  # Utilise directement le dictionnaire fourni
        else:  # méthode par défaut
            collection_name=payload.get('collection_name')
            final_payload = {'collection_name': collection_name}  # ici payload est traité comme collection_id
        # URL du point de terminaison pour les différentes sources
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8082'
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # URL du point de terminaison pour l'event-trigger et la santé selon la source
        url_event_trigger = f'{base_url}/banker-event-trigger'
        url_health_check = f'{base_url}/health'

        # Si check_health est True, on fait une requête GET pour vérifier la santé de l'application
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            response = requests.get(url_health_check)
            if response.status_code == 200:
                print('Health Check réussi !')
                print('Réponse :', response.text)  # La réponse est du texte brut
            else:
                print('Erreur lors du Health Check :', response.status_code)
            return response

        # Sinon, on envoie une requête POST avec des données au point de terminaison /event-trigger
        
        print(f"Envoi de la requête POST avec payload {payload} à {url_event_trigger}...")
        response = requests.post(url_event_trigger, json=payload)

        # Vérifie que la requête a réussi
        if response.status_code == 202:
            print('Requête POST réussie !')
            print('Réponse :', response.json())  # La réponse est un JSON
        else:
            print('Erreur lors de la requête POST :', response.status_code)

        return response
    
    def stop_pinnokio_banker(self, payload, source=None, check_health=False):
        """
        Arrête un ou plusieurs jobs Pinnokio Banker en cours d'exécution.
        
        Args:
            job_id (str): ID du job principal à arrêter
            user_id (str, optional): ID utilisateur pour l'authentification
            job_ids (list, optional): Liste des IDs de jobs à arrêter (pour arrêt multiple)
            source (str, optional): Source du serveur ('docker', 'aws', 'local'). 
                                Par défaut utilise PINNOKIO_SOURCE ou 'aws'
            check_health (bool): Si True, vérifie la santé du serveur avant l'arrêt
            
        Returns:
            requests.Response: Réponse de la requête
        """
        
        if source is None:
            source = os.environ.get('PINNOKIO_SOURCE', 'aws')  # 'aws' comme valeur par défaut
        
        # URLs des points de terminaison pour les différentes sources
        docker_url = 'http://localhost:8080'
        aws_url = 'http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com'
        local_url = 'http://127.0.0.1:8082'
        
        # Choix de l'URL en fonction de la source
        if source == 'docker':
            base_url = docker_url
        elif source == 'aws':
            base_url = aws_url
        elif source == 'local':
            base_url = local_url
        else:
            raise ValueError("Source invalide. Choisissez parmi 'docker', 'aws' ou 'local'.")

        # URLs des points de terminaison
        url_stop = f'{base_url}/stop_banker'
        url_health_check = f'{base_url}/health'

        # Si check_health est True, on fait une requête GET pour vérifier la santé de l'application
        if check_health:
            print(f"Vérification de la santé de l'application avec GET sur {url_health_check}...")
            try:
                response = requests.get(url_health_check, timeout=10)
                if response.status_code == 200:
                    print('Health Check réussi !')
                    print('Réponse :', response.text)
                else:
                    print('Erreur lors du Health Check :', response.status_code)
                return response
            except requests.exceptions.RequestException as e:
                print(f'Erreur lors du Health Check : {e}')
                return None

        # Préparation du payload pour l'arrêt
        
        
        
        print(f"Envoi de la requête POST d'arrêt pour le job  {url_stop}...")

        try:
            # Envoi de la requête POST pour arrêter le(s) job(s)
            response = requests.post(url_stop, json=payload, timeout=30)

            # Vérification que la requête a réussi
            if response.status_code == 200:
                print('Requête d\'arrêt réussie !')
                print('Réponse :', response.json())
            elif response.status_code == 404:
                
                print('Réponse :', response.json())
            else:
                print('Erreur lors de la requête d\'arrêt :', response.status_code)
                try:
                    print('Réponse :', response.json())
                except:
                    print('Réponse :', response.text)

        except requests.exceptions.Timeout:
            
            return None
        except requests.exceptions.RequestException as e:
            print(f'Erreur lors de la requête d\'arrêt : {e}')
            return None

        return response.json()


class PINNOKIO_TOOLS:
    def __init__(self, collection_name,agent,dms_mode=None,user_id=None):
        # Utiliser le proxy ChromaVector au lieu de CHROMA_KLK directement
        chroma_proxy = get_chroma_vector_proxy()
        self.chroma_db_instance = chroma_proxy.create_chroma_instance(collection_name)
        self.kdb_agent=Anthropic_KDB_AGENT(self.chroma_db_instance)
        if agent is None:
            self.agent = NEW_Anthropic_Agent()
        else:
            self.agent=agent
            
        self.models=['claude-3-5-sonnet-20240620', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307', 'claude-3-opus-20240229']
        self.thread_pool = ThreadPoolExecutor()
        # 🆕 Stocker user_id localement pour l'utiliser lors des appels à drive_service
        self.user_id = user_id
        self.drive_service=DriveClientService(mode=dms_mode)  # Plus de user_id dans le constructeur

    async def run_in_thread(self, func, *args, **kwargs):
        """Helper pour exécuter des fonctions synchrones dans le thread pool"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.thread_pool, partial(func, *args, **kwargs))

    def metadata_creation(self, question):
        """Version asynchrone de metadata_creation"""
        prompt = f"""
            Nous somme le {datetime.now()}, 
            Objectif :
            Créer un dictionnaire à utiliser comme filtre pour effectuer une recherche dans une base de données vectorielle, en se basant sur la question de l'utilisateur.

            Instructions pour construire le filtre :

            Utilisation de l'outil :

            Utilisez l'outil create_metadatafilter pour constituer le dictionnaire de filtre.
            Critères de filtrage :

            Argument pinnokio_func :
            Ce champ doit indiquer le département concerné, en fonction de la question de l'utilisateur. Les départements disponibles sont :

            APbookeeper : Factures fournisseurs.
            EXbookeeper : Notes de frais.
            Bankbookeeper : Transactions bancaires.
            HRmanager : Ressources humaines.
            Admanager : Questions administratives.
            Argument source :
            Ce champ doit préciser le type de contenu à rechercher, selon le contexte fourni dans la question :

            journal : Traite des journaux liés aux traitements spécifiques d'éléments par département.
            journal/chat : Concerne les échanges avec l'utilisateur sur les traitements spécifiques d'éléments par département.
            Argument file_name :
            Ce champ doit être utilisé uniquement si le nom du fichier est explicitement mentionné dans la question de l'utilisateur.

            Si le nom du fichier est absent ou ambigu, demandez directement à l'utilisateur de fournir ce détail avant de continuer.
            Résumé :
            Le dictionnaire de filtre doit être adapté à la question de l'utilisateur, en prenant en compte :

            Le département concerné (pinnokio_func).
            Le type de contenu recherché (source).
            Un éventuel fichier spécifique (file_name), seulement si mentionné explicitement, fournit toujours la valeur
            '<UNKNOWN>' comme valeur par défaut.
                    """

        self.agent.update_system_prompt(prompt)

        tooling = [{
            "name": "create_metadatafilter",
            "description": "Constitue un dictionnaire de filtres pour la recherche dans la base de données vectorielle",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pinnokio_func": {
                        "type": "string",
                        "enum": [
                            "APbookeeper",
                            "EXbookeeper",
                            "Bankbookeeper",
                            "HRmanager",
                            "Admanager"
                        ],
                        "description": "Département concerné par la recherche"
                    },
                    "source": {
                        "type": "string",
                        "enum": [
                            "journal",
                            "journal/chat"
                        ],
                        "description": "Source des données à filtrer"
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Nom du fichier à rechercher"
                    }
                },
                "required": ["pinnokio_func"]
            }
            }]

        mission = f"""Merci de determiner le filtre de dictionnaire concernant la question suivante:
        {question}
        """
        
        tool_map = {'create_metadatafilter': None}
        tool_choice = {'type': 'tool', 'name': 'create_metadatafilter'}
        
        # Exécution asynchrone de l'agent
        data =self.agent.process_tool_use(
            content=mission,
            tools=tooling,
            tool_mapping=tool_map,
            tool_choice=tool_choice
        )
        
        print (f"impression de data dans la creation du métadata dict:{data}")
        return data

    def antho_kdb_async(self, user_query, model_index, excl_job_id=None):
        """Version asynchrone de antho_kdb"""
        #metadata_dict=self.metadata_creation(user_query)
        data=self.kdb_agent.CHROMADB_AGENT(user_query=user_query)
        print(f"impression de data depuis antho_kdb_async;{data}")
        return data
        

    def create_async_db_tool(self, model_index=0):
        """Crée un outil asynchrone compatible avec le système de streaming"""
        return partial(self.antho_kdb_async, model_index=model_index)

    async def __aenter__(self):
        """Support pour le context manager asynchrone"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Nettoyage des ressources lors de la sortie du context manager"""
        self.thread_pool.shutdown(wait=True)

    async def analyse_multipage_docs_async(self, *args, model_index=0, file_ids=None, **kwargs):
        """Version asynchrone de analyse_multipage_docs utilisant run_in_thread"""
        return await self.run_in_thread(
            self.agent.analyse_multipage_docs,
            *args,
            model_index=model_index,
            drive_service=self.drive_service,
            file_ids=file_ids,
            **kwargs
        )


    
    


class PINNOKIO_ONBOARDING_SETUP:
    def __init__(self,business_context_text=None,buisness_data=None):
        self.init_setup_agent()
        self.get_business_att()
        if business_context_text:
            self.init_general_context_compiler(business_context_text)

    def init_setup_agent(self):
        try:
            anthropic_agent=NEW_Anthropic_Agent()
            openai_agent=NEW_OpenAiAgent()
            gemini_agent=NEW_GeminiAgent()
            self.pinnokio_CFO=BaseAIAgent()
            self.pinnokio_clerck=BaseAIAgent()
            self.pinnokio_auditor=BaseAIAgent()
            self.pinnokio_CFO.register_provider(ModelProvider.OPENAI,openai_agent)
            self.pinnokio_clerck.register_provider(ModelProvider.GEMINI,gemini_agent)
            self.pinnokio_auditor.register_provider(ModelProvider.ANTHROPIC,anthropic_agent)
            print(f"Accounting agent team setup successfully......")
        except Exception as e:
            print(f"error in Accounting agent team setup:{e}")

    def get_business_att(self,buisness_data):
        accounting_systems = buisness_data.get('accounting_systems', {})
        self.erp=buisness_data.get('accounting_system',"")
        self.first_name=buisness_data.get('first_name')
        self.last_name=buisness_data.get('last_name')
        self.business_name = buisness_data.get("business_name", "")
        #Attention met le nom du secret dans Firebase apres l'avoir mis dans secret manager pour l'appeler ici pour la connection dans odoo
        #erp_api_key=self.get_secret_name_from_data()
        if self.erp=='Odoo':
            odoo_details = accounting_systems.get('odoo_details', {})
            odoo_company_name = odoo_details.get('company_name', "")
            odoo_db_name = odoo_details.get('database_name', "")
            odoo_url = odoo_details.get('url', "")
            odoo_username = odoo_details.get('username', "")
            self.erp_instance=ODOO_KLK_VISION(url=odoo_url,db=odoo_db_name,username=odoo_username,odoo_company_name=odoo_company_name,password=erp_api_key)

    def get_secret_name_from_data(self):
        """
        Génère le nom d'un secret basé sur les données d'onboarding.

        :param onboarding_data: Dictionnaire structuré contenant les données d'onboarding.
        :return: Nom du secret sous forme de chaîne de caractères.
        """
        try:
            
            # Générer les premières lettres pour chaque champ
            first_name_part = self.first_name[:2].lower() if len(self.first_name) >= 2 else "na"
            last_name_part = self.last_name[:2].lower() if len(self.last_name) >= 2 else "na"
            business_name_part = self.business_name[:2].lower() if len(self.business_name) >= 2 else "na"
            accounting_system_part = self.erp.lower() if self.erp else "na"

            # Construire le nom du secret
            secret_name = f"{first_name_part}_{last_name_part}_{business_name_part}_{accounting_system_part}"

            return secret_name

        except Exception as e:
            print(f"Erreur lors de la génération du nom du secret : {e}")
            raise


    def init_general_context_compiler(self,business_context_text):
          
        def read_yaml_file(prompt_name):
            """
            Lit un fichier YAML et retourne son contenu
            
            Args:
                file_path (str): Chemin vers le fichier YAML
                
            Returns:
                dict: Contenu du fichier YAML sous forme de dictionnaire
            """
            try:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                yml_path = os.path.join(current_dir, 'tool_definition.yml')
                with open(yml_path, 'r', encoding='utf-8') as file:
                    content = yaml.safe_load(file)
                    if prompt_name:
                        return content.get(prompt_name)
                    return content
            except FileNotFoundError:
                print(f"Erreur : Le fichier {yml_path} n'a pas été trouvé.")
                return None
            except yaml.YAMLError as e:
                print(f"Erreur lors de la lecture du YAML : {e}")
                return None

        load_yaml_context=read_yaml_file('INITIAL_PROMPT_PINNOKIO')

        initial_prompt=f"{load_yaml_context}\n\n{business_context_text}"
        self.pinnokio_CFO.update_system_prompt(initial_prompt)

        mission_task=f"""Sur base des informations recu sur le client,
          1. Rediger le context général de l'entreprise
          2. Analyser les comptes et effectuer le mapping des comptes pour effectuer la revue analytique des comptes
          3. Tester la cohérence entre l'analyse générale et l'analyse des comptes et affiner l'analyse général 
          4. Rediger le prompt initial pour les départements d'exécution 
           Comptabilité - Afin de donner les informations importante sur la maniere saisir les factures et les informations importante liées à l'activité.
           Par exemple les factures provenant d'un meme fournisseur mais fournissant divers services qui doivent etre saisie sur différent compte comptable.
           Les factures eventuelles qui demandent d'etre saisie sur plusieurs lignes comptable par besoin de granularité demandé par l'activité, par exemple différents taux TVA sur une meme facture.

           Indiquer à l'auditeur qui surveille les activités comptable pour lui avertir des spécificité eventuelle sur l'activité , par exemple si la société recoit des factures qui ne sont pas addressé à son nom , comment doit elle agir et si c'est le cas fournir le nom des personnes ou autre entitiés autorisé à etre saisi dans les livres de compte
           
           Router: Indiquer au département de dispatchine (router), qui pour objectif de récuperer les documents déposé sur le point d'entrée du dossier de partage.
           Et indiquer les regles à appliquer sur le dispatching en fonction de l'activité et des documents.
           
           Banques: Analyser les mouvements de banques sur 3 mois, pour comprendre le flux financier et la maniere dont ces informations ont été comptabilité 
           pour rediger le prompt initial à la reconciliation bancaire pour savoir comment comptabiliser les mouvements bancaires.

           Chacune de ces étapes constituera un bloc de travail et devra etre validé dans le bloc au travers de control et validé à la conclusion pour pouvoir
           passer au prochain bloc d'execution.

           """

    def init_accounting_params(self):
        pass
       
    def DMS_COMMAND(self,gcp_project_id,dms_type,command,command_args):
        try:
            DMS_CREATION(gcp_project_id=gcp_project_id,dms_type=dms_type,user_mail=user_mail,command=command,command_args=command_args)
            print(f"DMS settings setup successfuly")
        except Exception as e:
            print(f"error in DMS Setting:{e}")






class PINNOKIO_FIREBASE_CHROMA_ACTIONS:
    def __init__(self,collection_name,firebase_user_id):
        # Utiliser le proxy ChromaVector au lieu de CHROMA_KLK directement
        chroma_proxy = get_chroma_vector_proxy()
        self.chroma_db_instance = chroma_proxy.create_chroma_instance(collection_name)
        self.firebase_user_id=firebase_user_id
        self.firebase_instance=FireBaseManagement()

    async def ASYNC_DELETE_JOB_ID(self, job_id=None, file_name=None, chroma_only=False, mandate_path=None):
            """
            Supprime un job dans Chroma et Firebase.
            
            Args:
                job_id: ID du job à supprimer
                file_name: Nom du fichier à supprimer
                chroma_only: Si True, supprime uniquement dans Chroma (pas Firebase)
                mandate_path: Chemin du mandat (pour suppression expenses_details si EXbookeeper)
            """
            try:
                print(f"[DEBUG] ASYNC_DELETE_JOB_ID appelée avec: job_id={job_id}, file_name={file_name}, chroma_only={chroma_only}, mandate_path={mandate_path}")
                
                deletion_tasks = []
                
                if file_name:
                    print(f"[DEBUG] Ajout tâche Chroma pour file_name: {file_name}")
                    criteria_journal = {'file_name': file_name}
                    
                    # Le proxy ChromaKLKProxy gère automatiquement le mode (LOCAL/PROD vs ACTUEL)
                    task = self.chroma_db_instance.async_chroma_delete_documents_new_methode(**criteria_journal)
                    print(f"[DEBUG] Tâche Chroma créée (file_name): {type(task)} - {task}")
                
                    deletion_tasks.append(task)
                    
                if job_id:
                    print(f"[DEBUG] Ajout tâches Chroma pour job_id: {job_id}")
                    
                    # Journal deletion
                    criteria_journal = {
                        'source': 'journal',
                        'pinnokio_func': 'APbookeeper', 
                        'file_name': f"journal_{job_id}.txt"
                    }
                    task_journal = self.chroma_db_instance.async_chroma_delete_documents_new_methode(**criteria_journal)
                    print(f"[DEBUG] Tâche Chroma créée (journal): {type(task_journal)} - {task_journal}")
                    deletion_tasks.append(task_journal)
                    
                    # Chat deletion
                    criteria_chat = {
                        'source': 'journal/chat',
                        'pinnokio_func': 'APbookeeper',
                        'file_name': f"chat_klk_{job_id}.txt"
                    }
                    task_chat = self.chroma_db_instance.async_chroma_delete_documents_new_methode(**criteria_chat)
                    print(f"[DEBUG] Tâche Chroma créée (chat): {type(task_chat)} - {task_chat}")
                    deletion_tasks.append(task_chat)
                    
                    if not chroma_only:
                        # Firebase deletion - adapter selon le mode
                        import os
                        mode = os.getenv("LISTENERS_MODE", "ACTUEL").strip().upper()
                        print(f"[DEBUG] Mode détecté: {mode}")
                        
                        if mode in ["LOCAL", "PROD"]:
                            # En mode LOCAL/PROD, l'appel RPC est synchrone via __getattribute__ wrapper, retourne directement un booléen
                            print(f"🔄 Mode {mode}: Exécution directe de la suppression Firebase")
                            firebase_result = self.firebase_instance.async_delete_items_by_job_id(
                                self.firebase_user_id, 
                                job_id, 
                                mandate_path=mandate_path
                            )
                            print(f"[DEBUG] Résultat suppression Firebase: {firebase_result} (type: {type(firebase_result)})")
                        else:
                            # Mode ACTUEL : ajouter à la liste des tâches asynchrones
                            print(f"[DEBUG] Mode {mode}: Ajout Firebase à deletion_tasks")
                            firebase_task = self.firebase_instance.async_delete_items_by_job_id(
                                self.firebase_user_id,
                                job_id,
                                mandate_path=mandate_path
                            )
                            print(f"[DEBUG] Tâche Firebase créée: {type(firebase_task)} - {firebase_task}")
                            deletion_tasks.append(firebase_task)

                print(f"[DEBUG] deletion_tasks contient {len(deletion_tasks)} tâches:")
                # Vérifier que chaque tâche est une coroutine et corriger si nécessaire
                import inspect
                for i, task in enumerate(deletion_tasks):
                    print(f"[DEBUG]   Task {i}: {type(task)} - {task}")
                    if not inspect.iscoroutine(task):
                        print(f"[DEBUG] ⚠️ ATTENTION: Task {i} n'est pas une coroutine! Type: {type(task)}")
                        # Si ce n'est pas une coroutine, on la convertit en coroutine qui retourne la valeur
                        task_value = task
                        async def _wrap_result(val=task_value):
                            return val
                        deletion_tasks[i] = _wrap_result()

                # Vérifier si on a au moins quelque chose à supprimer
                if not deletion_tasks and not file_name and not job_id:
                    print("[DEBUG] Aucune tâche et aucun paramètre fourni")
                    print("Merci de renseigner le job id et/ou le nom du fichier à supprimer.")
                    return False

                # Execute all deletion tasks (Chroma + Firebase en mode ACTUEL, seulement Chroma en mode LOCAL/PROD)
                if deletion_tasks:
                    print(f"[DEBUG] Exécution de {len(deletion_tasks)} tâches avec asyncio.gather()")
                    try:
                        results = await asyncio.gather(*deletion_tasks)
                        print(f"[DEBUG] Résultats gather: {results}")
                        print("Tâches de suppression terminées avec succès.")
                        return True
                    except Exception as e:
                        print(f"[DEBUG] Erreur dans asyncio.gather: {e}")
                        print(f"Erreur lors des suppressions: {e}")
                        return False
                else:
                    # Cas où il n'y a que Firebase en mode LOCAL/PROD (déjà exécuté)
                    print("[DEBUG] Aucune tâche à exécuter, Firebase déjà traité")
                    print("Suppressions terminées avec succès.")
                    return True
                    
            except Exception as e:
                print(f"[DEBUG] Exception globale dans ASYNC_DELETE_JOB_ID: {e}")
                import traceback
                traceback.print_exc()
                print(f"Erreur lors de la suppression: {e}")
                return False
       
    def DELETE_JOB_ID(self, job_id=None,file_name=None):
        """
        Fonction de suppression de job dans son execution
        """
        try:
            # Suppression des nodes dans chroma db
            if file_name is not None:
                criteria_journal={'file_name':f"{file_name}"}
                self.chroma_db_instance.chroma_delete_documents_new_methode(**criteria_journal)
                print('Suppression source du nom du fichier dans Chroma reussi....')

            if job_id is not None:
                criteria_journal={'source':'journal','pinnokio_func':'APbookeeper','file_name':f"journal_{job_id}.txt"}
                
                self.chroma_db_instance.chroma_delete_documents_new_methode(**criteria_journal)
                mess=(f"Suppression des documents dans Chroma DB réussie pour le job_id {job_id} soure JOURNAL")
                print(mess)

                criteria_journal={'source':'journal/chat','pinnokio_func':'APbookeeper','file_name':f"chat_klk_{job_id}.txt"}
                self.chroma_db_instance.chroma_delete_documents_new_methode(**criteria_journal)
        
                
                # Suppression dans firebase
                print(f"Début de la suppression des items dans Firebase pour le job_id: {job_id}")
                self.firebase_instance.delete_items_by_job_id(self.firebase_user_id,job_id)
                mess=(f"Suppression des items dans Firebase réussie pour le job_id: {job_id}")
               
                
                print("Tâche de suppression terminée avec succès.")
         
                return True
            else:
                print("Merci de renseinger le job id et ou le nom du fichier a supprimer.")
                return False
        except Exception as e:
            print(f"Erreur lors de la suppression: {e}")

    def DELETE_MULTIPLE_JOBS(self, job_file_pairs):
        """
        Enveloppe la méthode DELETE_JOB_ID pour traiter plusieurs paires (job_id, file_name).
        """
        try:
            for job_id, file_name in job_file_pairs:
                print(f"Traitement de la paire: Job ID = {job_id}, File Name = {file_name}")
                # Appel de la méthode DELETE_JOB_ID pour chaque paire
                self.DELETE_JOB_ID(job_id=job_id, file_name=file_name)
            
            print("Traitement de toutes les paires terminé avec succès.")
            return True
        
        except Exception as e:
            print(f"Erreur lors du traitement des paires : {e}")
            return False

    async def ASYNC_DELETE_MULTIPLE_JOBS(self, job_file_pairs, chroma_only=False, mandate_path=None):
        """
        Version asynchrone de DELETE_MULTIPLE_JOBS.
        
        Args:
            job_file_pairs: Liste de tuples (job_id, file_name)
            chroma_only: Si True, supprime uniquement dans Chroma (pas Firebase)
            mandate_path: Chemin du mandat (pour suppression expenses_details si EXbookeeper)
        """
        try:
            for job_id, file_name in job_file_pairs:
                print(f"Traitement de la paire: Job ID = {job_id}, File Name = {file_name}, mandate_path = {mandate_path}")
                
                result = await self.ASYNC_DELETE_JOB_ID(
                    job_id=job_id, 
                    file_name=file_name,
                    chroma_only=chroma_only,
                    mandate_path=mandate_path
                )
                print(f"[DEBUG ASYNC_DELETE_MULTIPLE_JOBS] Résultat pour {job_id}: {result} (type: {type(result)})")
                await asyncio.sleep(0)  # Permet à d'autres tâches de s'exécuter
                if not result:
                    print(f"[DEBUG ASYNC_DELETE_MULTIPLE_JOBS] Échec pour {job_id}, retour False")
                    return False
            print("Traitement de toutes les paires terminé avec succès.")
            print(f"[DEBUG ASYNC_DELETE_MULTIPLE_JOBS] Retour True")
            return True
        
        except Exception as e:
            print(f"[DEBUG ASYNC_DELETE_MULTIPLE_JOBS] Exception: {e}")
            import traceback
            traceback.print_exc()
            print(f"Erreur lors du traitement des paires : {e}")
            return False
      


dulce_space='AAAABzwjXro'
auxo_200_space='AAAAnkSS5Ww'
fim='AAAAd8qX9nA'
psh='AAAAGP0qP_A'
psl='AAAATYrxJdU'
senemo_sarl='AAAA2uQSf3I'
pssl_collection_name='AAAAMFKYrSE'        
sens_comm_collection_name='AAAAQksrfXA'
fim_collection_name='AAAAd8qX9nA'
fm_collection_name='AAAADz4Z5Zw'
Val_ri='AAAAgu-o_D4'
es_collection='AAAAgaDzK_I'
collection_name=es_collection
pub_sub_id='klk_google_pubsub_id_klk_5de61595-dc59-4140-8b9e-fe9334e5370e'
user_id="11111"
test_data={'collection_name': 'AAAAgaDzK_I', 'jobs_data': [{'file_name': '2024-10-01_robert-verdina_cor-owner-rents_5aead9ca', 'job_id': 'klk_32e66ae4-1290-44b6-9c85-ed47e5e1555e', 'instructions': ''}], 'start_instructions': None, 'settings': [{'communication_mode': 'pinnokio'}, {'dms_system': 'google_drive'}], 'client_uuid': '32dd3346-043d-4e99-99f9-3072c9e2b9a1', 'user_id': '7hQs0jluP5YUWcREqdi22NRFnU32', 'pub_sub_id': 'klk_google_pubsub_id_klk_32e66ae4-1290-44b6-9c85-ed47e5e1555e', 'mandates_path': 'clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/LaCWd6ltASD2vgCl8J01/mandates/ZhnLigKULKQOoZhcW9Fp'}
criteria={'pinnokio_func':'APbookeeper'}
#test_=PINNOKIO_DEPARTEMENTS()

#test_.run_pinnokio_apbookeeper(test_data,'local',mthd='single')

#test_.stop_pinnokio_router('1jg55GMsBQWkGSaVtW2Bc2iKW6mGZ-nDM','local')
'''async def test_pinnokio():
    # Création de l'instance
    test = PINNOKIO_TOOLS(es_collection)
    
    # Test avec une question
    question = "stp donne les informations sur la derniere facture de Mews System, ou quelque chose similaire a ceci"
    
    try:
        # Utilisation de la version asynchrone
        result = await test.antho_kdb_async(question, model_index=0)
        print("Résultat de la recherche:")
        print(result)
    except Exception as e:
        print(f"Une erreur s'est produite: {e}")
    finally:
        # S'assurer que les ressources sont libérées
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, test.thread_pool.shutdown, True)

if __name__ == "__main__":
    asyncio.run(test_pinnokio())'''

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
},
{
            "name": "ASK_KDB_JOURNAL",
            "description": "Méthode adaptée pour utiliser fetch_documents_with_date_range...",
            "input_schema": {
                "type": "object",
                "properties": {
                    "user_query": {"type": "string", "description": "Requête utilisateur"},
                    "excl_job_id": {"type": "string", "description": "ID du job à exclure"}
                },
                "required": ["user_query"]
            }
        },]
fim='AAAAd8qX9nA'
#agent=Anthropic_Agent()
system_prompt="""Tu es un agent intelligent dédié à l'automatisation des tâches comptables, 
ton nom est Pinnokio. Ton expertise inclut la saisie automatisée de factures, la réconciliation bancaire et le dispatch 
de documents. Tu comprends le contexte économique et comptable de chaque entreprise pour offrir des solutions personnalisées.
Prépare-toi à recevoir des informations sur le client pour commencer ta mission."""
tools_description=""" Tu dispose des outils suivants:
VIEW_DOCUMENT_WITH_VISION te permet de visualiser un document"""
final_prompt=f"""Mission:{system_prompt}\n outils:{tools_description} """
#agent.update_system_prompt(final_prompt)
#service_account_info = json.loads(get_secret('service_account_key'))
user_mail='cedric.gacond@klkvision.tech'
base_drive_item_id='1QnBe-ai7eroeQHst-_zM9RJdFhfRcrR1'

payload={'firebase_user_id': '7TgxiVYENcYMO54TV0siXDigSr33', 'job_id': 'Aproz Sources Minérales SA_5f1386ba-cbb2-4938-acf4-6306fc684c6e', 'mandate_path': 'clients/7TgxiVYENcYMO54TV0siXDigSr33/bo_clients/7TgxiVYENcYMO54TV0siXDigSr33/mandates/azwMY25V43LVfe1UnxSm', 'mode': 'onboarding', 'setup_coa_type': {'method': 'based_on_coa'}, 'erp_system': 'odoo', 'context': "Contexte de l'Entreprise\n\n        La société  est une entité de type Company \n        située en Switzerland, dirigée par cedric gacond.\n        Statut de propriété: Propriétaire\n        \n        Informations Financières:\n        - Devise de base: CHF\n\n        Informations de Contact:\n        - Email: cgacond@gmail.com\n        - Téléphone: \n        - Adresse: rue du dauphiné\n14\n        - Site Web: www.aproz.ch\n\n        Profil d'Activité:\n        - Type de vente: Goods\n        - Facturation: \n        * Factures récurrentes: Non\n        * Factures sur commande: Oui\n\n        Aspects Fiscaux et Réglementaires:\n        - Statut TVA: Assujetti\n        * Numéro de TVA: CHE-102.560.397 TVA\n\n        Ressources Humaines:\n        - Présence d'employés: Oui\n        * Détails: 65 Employee\n\n        Gestion des Stocks:\n        - Gestion de stock: Oui\n        * Détails de gestion: We have an inventory \n\n        Systèmes Comptables:\n        - Système comptable: odoo\n        - Système de gestion des comptes clients: Same as accounting\n        - Système de gestion de communication pour les traitements de travaux: Same as accounting \n\n        Aspects Financiers Complémentaires:\n        - Loyers: Présents\n        - Taxes spécifiques: Non\n        \n        - Dépenses personnelles: Non\n        \n\n        Préparé le: 2025-03-31T16:03:16.063817\n        "}
#pinnokio_tooling=PINNOKIO_TOOLS(collection_name=fim,agent=agent,dms_mode='dev')
#departements=PINNOKIO_DEPARTEMENTS()
#import asyncio
#test=departements.run_pinnokio_onboarding(payload=payload,source='local',mthd='single')
#print (f"impression de test:{test}")
'''view_doc = partial(pinnokio_tooling.analyse_multipage_docs_async,
                  file_ids=[base_drive_item_id],
                  model_index=0)
ask_db = partial(pinnokio_tooling.antho_kdb_async, model_index=0)
tool_map=[{'VIEW_DOCUMENT_WITH_VISION':view_doc},{'ASK_KDB_JOURNAL': ask_db}]
tool_choice={"type": "auto"}'''


''''async def chat():
    
    
    
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
        
        async_gen = agent.anthropic_send_message_tool_stream(prompt, 0,tool_list=tools,tool_mapping=tool_map,tool_choice=tool_choice)
        
        try:
            async for chunk in async_gen:
                print(str(chunk), end='', flush=True)
            print("\n" + "-" * 50)  # Séparateur entre les messages
            
        except Exception as e:
            print(f"\nErreur lors de l'itération : {e}")

if __name__ == "__main__":
    asyncio.run(chat())
'''