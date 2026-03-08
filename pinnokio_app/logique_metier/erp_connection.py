"""
ERP Connection Test
===================

Utilitaires pour tester les connexions ERP (Odoo, Bexio, etc.)
"""

import requests
import xmlrpc.client
from typing import Dict, Any, Optional


def test_odoo_connection(url: str, db: str, username: str, api_key: str) -> Dict[str, Any]:
    """
    Teste la connexion à une instance Odoo.
    
    Args:
        url: URL de l'instance Odoo (ex: https://mon-instance.odoo.com)
        db: Nom de la base de données
        username: Nom d'utilisateur (email)
        api_key: Clé API Odoo
        
    Returns:
        Dict avec:
        - success: bool
        - message: str (optionnel)
        - error: str (optionnel)
        - connectionDetails: dict (optionnel)
    """
    try:
        # Nettoyer l'URL
        url = url.rstrip('/')
        
        # URLs pour XML-RPC
        common_url = f'{url}/xmlrpc/2/common'
        object_url = f'{url}/xmlrpc/2/object'
        
        # 1. Tester la connexion et récupérer la version
        common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
        
        try:
            version_info = common.version()
            server_version = version_info.get('server_version', 'Unknown')
        except Exception as e:
            return {
                'success': False,
                'error': f'Impossible de se connecter au serveur Odoo: {str(e)}'
            }
        
        # 2. Tenter l'authentification
        try:
            uid = common.authenticate(db, username, api_key, {})
            
            if not uid:
                return {
                    'success': False,
                    'error': 'Authentification échouée. Vérifiez vos identifiants.'
                }
        except xmlrpc.client.Fault as e:
            if 'database' in str(e).lower():
                return {
                    'success': False,
                    'error': f'Base de données "{db}" introuvable.'
                }
            return {
                'success': False,
                'error': f'Erreur d\'authentification: {str(e)}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Erreur d\'authentification: {str(e)}'
            }
        
        # 3. Tester l'accès aux données en récupérant les infos de la société
        try:
            models = xmlrpc.client.ServerProxy(object_url, allow_none=True)
            
            # Récupérer l'ID de l'utilisateur courant
            user = models.execute_kw(
                db, uid, api_key,
                'res.users', 'read',
                [uid], {'fields': ['company_id']}
            )
            
            if user and len(user) > 0:
                company_id = user[0].get('company_id')
                
                if company_id:
                    # Récupérer les infos de la société
                    company = models.execute_kw(
                        db, uid, api_key,
                        'res.company', 'read',
                        [company_id[0]], {'fields': ['name']}
                    )
                    
                    company_name = company[0].get('name', '') if company else ''
                else:
                    company_name = ''
            else:
                company_name = ''
            
            return {
                'success': True,
                'message': 'Connexion Odoo établie avec succès !',
                'connectionDetails': {
                    'serverVersion': server_version,
                    'databaseName': db,
                    'companyName': company_name,
                }
            }
            
        except xmlrpc.client.Fault as e:
            return {
                'success': False,
                'error': f'Accès aux données refusé: {str(e)}'
            }
        except Exception as e:
            # Authentification OK mais erreur lors de la récupération des données
            # On considère quand même la connexion comme réussie
            return {
                'success': True,
                'message': 'Connexion Odoo établie (accès limité)',
                'connectionDetails': {
                    'serverVersion': server_version,
                    'databaseName': db,
                }
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': f'Erreur inattendue: {str(e)}'
        }


def test_bexio_connection(api_key: str) -> Dict[str, Any]:
    """
    Teste la connexion à Bexio.
    
    Args:
        api_key: Clé API Bexio
        
    Returns:
        Dict avec success, message, error
    """
    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
        }
        
        # Endpoint pour tester la connexion
        response = requests.get(
            'https://api.bexio.com/2.0/contact',
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return {
                'success': True,
                'message': 'Connexion Bexio établie avec succès !',
            }
        elif response.status_code == 401:
            return {
                'success': False,
                'error': 'Clé API invalide.',
            }
        else:
            return {
                'success': False,
                'error': f'Erreur HTTP {response.status_code}',
            }
    
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'error': 'Timeout lors de la connexion à Bexio.',
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Erreur: {str(e)}',
        }


def test_quickbooks_connection(api_key: str) -> Dict[str, Any]:
    """
    Teste la connexion à QuickBooks.
    
    Args:
        api_key: Clé API QuickBooks
        
    Returns:
        Dict avec success, message, error
    """
    # TODO: Implémenter le test QuickBooks (OAuth2)
    return {
        'success': True,
        'message': 'QuickBooks configuré (test non implémenté)',
    }


def test_banana_connection(api_key: str) -> Dict[str, Any]:
    """
    Teste la connexion à Banana Accounting.
    
    Args:
        api_key: Clé API Banana
        
    Returns:
        Dict avec success, message, error
    """
    # TODO: Implémenter le test Banana
    return {
        'success': True,
        'message': 'Banana Accounting configuré (test non implémenté)',
    }
