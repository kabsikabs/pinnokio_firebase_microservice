"""
Outils SPT (Short Process Tooling) pour l'agent Pinnokio.
Ces outils sont rapides (<30s) et s'exécutent de manière synchrone.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger("pinnokio.spt_tools")


class SPTTools:
    """
    Outils SPT (Short Process Tooling) : accès rapide à Firebase, ChromaDB, etc.
    """
    
    def __init__(self, firebase_user_id: str, collection_name: str, brain=None):
        self.firebase_user_id = firebase_user_id
        self.collection_name = collection_name
        self.brain = brain  # ⭐ Référence au brain pour accès au contexte utilisateur
        logger.info(f"SPTTools initialisé pour user={firebase_user_id}, collection={collection_name}")
    
    def get_tools_definitions(self) -> List[Dict[str, Any]]:
        """
        Retourne les définitions JSON des outils SPT pour l'agent.
        """
        return [
            {
                "name": "GET_FIREBASE_DATA",
                "description": "🔍 Accès rapide aux données Firebase (clients, factures, documents). Utilisez ce tool pour récupérer des informations stockées.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Chemin Firebase (ex: 'clients/{uid}/notifications', 'companies/{collection}/invoices')"
                        },
                        "query_filters": {
                            "type": "object",
                            "description": "Filtres optionnels pour la requête Firestore"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "SEARCH_CHROMADB",
                "description": "🔎 Recherche sémantique dans ChromaDB pour trouver des documents ou informations pertinentes.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Requête de recherche sémantique"
                        },
                        "n_results": {
                            "type": "integer",
                            "description": "Nombre de résultats à retourner (défaut: 5)"
                        }
                    },
                    "required": ["query"]
                }
            },
        ]
    
    def get_tools_mapping(self) -> Dict[str, Any]:
        """
        Retourne le mapping des noms d'outils vers leurs fonctions d'implémentation.
        """
        return {
            "GET_FIREBASE_DATA": self.get_firebase_data,
            "SEARCH_CHROMADB": self.search_chromadb
        }
    
    async def get_firebase_data(self, path: str, query_filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Récupère des données depuis Firebase Firestore.
        """
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            
            # Remplacer les placeholders dans le path
            path = path.replace("{uid}", self.firebase_user_id)
            path = path.replace("{collection}", self.collection_name)
            
            logger.info(f"GET_FIREBASE_DATA: path={path}, filters={query_filters}")
            
            # TODO: Implémenter la logique de récupération Firestore
            # Utiliser firebase_service.db pour accéder à Firestore
            
            return {
                "success": True,
                "path": path,
                "data": {},
                "message": "Récupération Firebase (à implémenter)"
            }
        
        except Exception as e:
            logger.error(f"Erreur GET_FIREBASE_DATA: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def search_chromadb(self, query: str, n_results: int = 5) -> Dict[str, Any]:
        """
        Effectue une recherche sémantique dans ChromaDB.
        """
        try:
            from ...chroma_vector_service import get_chroma_service
            
            chroma_service = get_chroma_service()
            
            logger.info(f"SEARCH_CHROMADB: query='{query}', n_results={n_results}")
            
            # TODO: Implémenter la recherche ChromaDB
            # Utiliser chroma_service pour effectuer la recherche
            
            return {
                "success": True,
                "query": query,
                "results": [],
                "message": "Recherche ChromaDB (à implémenter)"
            }
        
        except Exception as e:
            logger.error(f"Erreur SEARCH_CHROMADB: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    