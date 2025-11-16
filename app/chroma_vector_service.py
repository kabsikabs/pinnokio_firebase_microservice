from __future__ import annotations
import time
import threading
import chromadb
import chromadb.utils.embedding_functions as embedding_functions
import json
import uuid
import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Any
from .tools.g_cred import get_secret
from .redis_client import get_redis


_CHROMA_VECTOR_SERVICE_SINGLETON: Optional["ChromaVectorService"] = None


class ChromaVectorService:
    """
    Gestionnaire ChromaDB avec pattern Singleton thread-safe.
    Garantit une seule instance avec une seule connexion ChromaDB.

    Important: Mapping RPC: "CHROMA_VECTOR.*"
    """

    _instance: Optional["ChromaVectorService"] = None
    _lock = threading.Lock()
    _initialized = False
    _collection_instances: Dict[str, Any] = {}  # Cache des collections

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._initialize_services()
                    self.__class__._initialized = True

    def _initialize_services(self):
        """Initialise ChromaDB et les services connexes."""
        try:
            self._initialize_chroma_client()
            self._initialize_embeddings()
            print("✅ ChromaVectorService initialisé avec succès")
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation ChromaVectorService: {e}")
            raise

    def _initialize_chroma_client(self):
        """Initialise le client ChromaDB."""
        def safe_env(key, default=None):
            value = os.getenv(key, default)
            return None if value == "None" else value

        try:
            # Configuration simple comme dans l'app qui fonctionne
            chroma_host = safe_env("CHROMA_HOST")
            chroma_port = safe_env("CHROMA_PORT")

            print(f"🔗 Connexion ChromaDB: {chroma_host}:{chroma_port}")

            # Configuration minimale qui fonctionne (sans headers/settings/tenant/database)
            self.chroma = chromadb.HttpClient(
                host=chroma_host or '35.180.247.70',
                port=chroma_port or '8000',
                ssl=safe_env("CHROMA_SSL") == "True"
            )

            # Test immédiat de connexion
            heartbeat = self.chroma.heartbeat()
            print(f"✅ ChromaDB connecté, heartbeat: {heartbeat}")

        except Exception as e:
            print(f"❌ Erreur connexion ChromaDB: {e}")
            raise

    def _initialize_embeddings(self):
        """Initialise le modèle d'embeddings."""
        try:
            self.api_key = get_secret('openai_pinnokio')
            self.embedding_model = 'text-embedding-ada-002'
            self.embeddings = embedding_functions.OpenAIEmbeddingFunction(
                api_key=self.api_key,
                model_name=self.embedding_model
            )
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation des embeddings: {e}")
            raise

    def _register_collection_session(self, user_id: str, collection_name: str, session_id: str) -> dict:
        """
        Enregistre une session utilisateur pour une collection spécifique.
        Utilise le même pattern que le registre utilisateur existant.
        """
        try:
            r = get_redis()
            key = f"registry:chroma:{user_id}:{collection_name}"
            payload = {
                "user_id": user_id,
                "collection_name": collection_name,
                "session_id": session_id,
                "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            r.hset(key, mapping=payload)
            r.expire(key, 90)  # TTL de 90 secondes comme pour les autres listeners
            return payload
        except Exception as e:
            print(f"❌ Erreur lors de l'enregistrement de la session Chroma: {e}")
            raise

    def _update_collection_heartbeat(self, user_id: str, collection_name: str) -> bool:
        """
        Met à jour le heartbeat pour une collection utilisateur.
        """
        try:
            r = get_redis()
            key = f"registry:chroma:{user_id}:{collection_name}"

            # Vérifier si la clé existe
            if not r.exists(key):
                return False

            # Mettre à jour le heartbeat
            r.hset(key, "last_heartbeat", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            r.expire(key, 90)  # Renouveler le TTL

            return True
        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour du heartbeat Chroma: {e}")
            return False

    def _unregister_collection_session(self, user_id: str, collection_name: str) -> bool:
        """
        Désenregistre une session utilisateur pour une collection.
        """
        try:
            r = get_redis()
            key = f"registry:chroma:{user_id}:{collection_name}"
            result = r.delete(key)
            return bool(result)
        except Exception as e:
            print(f"❌ Erreur lors du désenregistrement de la session Chroma: {e}")
            return False

    def get_or_create_collection(self, collection_name: str):
        """
        Récupère ou crée une collection ChromaDB.
        Met en cache l'instance pour éviter les créations multiples.
        """
        if collection_name not in self._collection_instances:
            with self._lock:
                if collection_name not in self._collection_instances:
                    collection = self.chroma.get_or_create_collection(
                        name=collection_name,
                        embedding_function=self.embeddings
                    )
                    self._collection_instances[collection_name] = collection

        return self._collection_instances[collection_name]

    def generate_unique_id(self) -> str:
        """Génère un ID unique."""
        return str(uuid.uuid4())

    def generate_embeddings(self, text_list: List[str]) -> List[List[float]]:
        """Génère des embeddings pour une liste de textes."""
        embeddings = self.embeddings(text_list)
        assert len(embeddings) == len(text_list), "Le nombre d'embeddings ne correspond pas au nombre de documents"
        return embeddings

    # === MÉTHODES MÉTIER ===
    # Toutes les méthodes ci-dessous sont accessibles via RPC sous "CHROMA_VECTOR.*"

    def register_collection_user(self, user_id: str, collection_name: str, session_id: str) -> dict:
        """
        RPC: CHROMA_VECTOR.register_collection_user
        Enregistre un utilisateur pour une collection spécifique.
        """
        # ANCIEN comportement maintenu à 100%
        result = self._register_collection_session(user_id, collection_name, session_id)
        print(f"🔗 Enregistrement Chroma: utilisateur={user_id}, collection={collection_name}")
        
        # NOUVEAU : Sync silencieuse avec le registre unifié (si activé)
        try:
            from .registry.registry_wrapper import get_chroma_registry_wrapper
            wrapper = get_chroma_registry_wrapper()
            if wrapper.unified_enabled:
                wrapper.registry_wrapper.update_user_service(
                    user_id, 
                    "chroma", 
                    {
                        "collections": [collection_name],
                        "last_heartbeat": result.get("registered_at")
                    }
                )
        except Exception as e:
            # Erreur silencieuse - ne pas impacter l'ancien système
            print(f"⚠️ Erreur sync ChromaDB unifié (register): {e}")
        
        return result  # Format IDENTIQUE qu'avant

    def heartbeat_collection(self, user_id: str, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.heartbeat_collection
        Met à jour le heartbeat pour une collection utilisateur.
        """
        # ANCIEN comportement maintenu
        success = self._update_collection_heartbeat(user_id, collection_name)
        result = {"user_id": user_id, "collection_name": collection_name, "heartbeat_updated": success}
        
        # NOUVEAU : Sync avec registre unifié (si activé)
        try:
            from .registry.registry_wrapper import get_chroma_registry_wrapper
            import time
            wrapper = get_chroma_registry_wrapper()
            if wrapper.unified_enabled:
                wrapper.registry_wrapper.update_user_service(
                    user_id,
                    "chroma", 
                    {
                        "collections": [collection_name],
                        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    }
                )
        except Exception as e:
            # Erreur silencieuse
            print(f"⚠️ Erreur sync heartbeat ChromaDB unifié: {e}")
        
        return result

    def unregister_collection_user(self, user_id: str, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.unregister_collection_user
        Désenregistre un utilisateur d'une collection.
        """
        # ANCIEN comportement maintenu
        success = self._unregister_collection_session(user_id, collection_name)
        print(f"🔗 Désenregistrement Chroma: utilisateur={user_id}, collection={collection_name}")
        result = {"user_id": user_id, "collection_name": collection_name, "unregistered": success}
        
        # NOUVEAU : Sync avec registre unifié (si activé)
        try:
            from .registry.registry_wrapper import get_chroma_registry_wrapper
            wrapper = get_chroma_registry_wrapper()
            if wrapper.unified_enabled and wrapper.registry_wrapper.unified_registry:
                # Récupérer les collections actuelles et retirer celle-ci
                user_registry = wrapper.registry_wrapper.unified_registry.get_user_registry(user_id)
                if user_registry:
                    collections = user_registry.get("services", {}).get("chroma", {}).get("collections", [])
                    if collection_name in collections:
                        collections.remove(collection_name)
                    
                    wrapper.registry_wrapper.update_user_service(
                        user_id,
                        "chroma",
                        {"collections": collections}
                    )
        except Exception as e:
            # Erreur silencieuse
            print(f"⚠️ Erreur sync désenregistrement ChromaDB unifié: {e}")
        
        return result

    def add_documents(self, collection_name: str, documents: List[str], metadatas: List[Dict[str, Any]], ids: Optional[List[str]] = None) -> dict:
        """
        RPC: CHROMA_VECTOR.add_documents
        Ajoute des documents à une collection.
        """
        try:
            collection = self.get_or_create_collection(collection_name)

            if ids is None:
                ids = [self.generate_unique_id() for _ in documents]

            collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )

            return {
                "success": True,
                "collection_name": collection_name,
                "documents_added": len(documents),
                "ids": ids
            }
        except Exception as e:
            print(f"❌ Erreur lors de l'ajout de documents: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def query_documents(self, collection_name: str, query_texts: List[str], n_results: int = 10, where: Optional[Dict[str, Any]] = None) -> dict:
        """
        RPC: CHROMA_VECTOR.query_documents
        Recherche des documents dans une collection.
        """
        try:
            collection = self.get_or_create_collection(collection_name)

            results = collection.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where
            )

            return {
                "success": True,
                "collection_name": collection_name,
                "results": results
            }
        except Exception as e:
            print(f"❌ Erreur lors de la recherche: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def _convert_where_to_chroma_format(self, where: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convertit un dictionnaire simple en format ChromaDB valide.
        
        Args:
            where: Dictionnaire simple comme {'source': 'journal', 'pinnokio_func': 'APbookeeper'}
            
        Returns:
            Format ChromaDB valide avec opérateurs $eq
        """
        if not where:
            return where
            
        # Si c'est déjà au format ChromaDB (contient des opérateurs), on le retourne tel quel
        if any(key.startswith('$') for key in where.keys()):
            return where
            
        # Conversion du format simple vers le format ChromaDB
        if len(where) == 1:
            # Un seul critère : {'source': 'journal'} -> {'source': {'$eq': 'journal'}}
            key, value = next(iter(where.items()))
            return {key: {"$eq": value}}
        else:
            # Plusieurs critères : utiliser $and
            conditions = []
            for key, value in where.items():
                conditions.append({key: {"$eq": value}})
            return {"$and": conditions}

    def delete_documents(self, collection_name: str, where: Optional[Dict[str, Any]] = None, ids: Optional[List[str]] = None) -> dict:
        """
        RPC: CHROMA_VECTOR.delete_documents
        Supprime des documents d'une collection.
        """
        try:
            collection = self.get_or_create_collection(collection_name)

            # Conversion automatique du format where si nécessaire
            chroma_where = self._convert_where_to_chroma_format(where) if where else None
            
            print(f"🔍 Suppression avec where: {chroma_where}, ids: {ids}")
            collection.delete(where=chroma_where, ids=ids)

            return {
                "success": True,
                "collection_name": collection_name,
                "deleted": True
            }
        except Exception as e:
            print(f"❌ Erreur lors de la suppression: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def get_collection_info(self, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.get_collection_info
        Récupère les informations d'une collection.
        """
        try:
            collection = self.get_or_create_collection(collection_name)
            count = collection.count()

            return {
                "success": True,
                "collection_name": collection_name,
                "document_count": count
            }
        except Exception as e:
            print(f"❌ Erreur lors de la récupération des infos: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def analyze_collection(self, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.analyze_collection
        Analyse une collection (équivalent à ChromaAnalyzer).
        """
        # Validation du paramètre collection_name
        if not collection_name:
            return {
                "success": False,
                "error": "collection_name est requis et ne peut pas être None ou vide",
                "collection_name": collection_name
            }

        try:
            print(f"🔍 analyze_collection appelé avec collection_name: '{collection_name}'")
            collection = self.get_or_create_collection(collection_name)

            # Récupérer des informations basiques
            count = collection.count()

            if count > 0:
                # Récupérer un échantillon pour analyser la structure
                sample = collection.peek(min(5, count))

                # Calculer la taille approximative
                embeddings_size = 0
                documents_size = 0
                metadata_size = 0

                if sample.get('embeddings'):
                    embedding_dimension = len(sample['embeddings'][0])
                    embeddings_size = count * embedding_dimension * 4  # float32

                if sample.get('documents'):
                    documents_size = sum(len(str(doc).encode('utf-8')) for doc in sample['documents'])
                    documents_size = documents_size * (count / len(sample['documents']))  # Extrapolation

                if sample.get('metadatas'):
                    metadata_size = sum(len(json.dumps(meta, separators=(',', ':')).encode('utf-8')) for meta in sample['metadatas'] if meta)
                    metadata_size = metadata_size * (count / len([m for m in sample['metadatas'] if m]))  # Extrapolation

                total_size = embeddings_size + documents_size + metadata_size
            else:
                total_size = embeddings_size = documents_size = metadata_size = 0

            return {
                "success": True,
                "collection_name": collection_name,
                "analysis": {
                    "total_size": int(total_size),
                    "embeddings_size": int(embeddings_size),
                    "documents_size": int(documents_size),
                    "metadata_size": int(metadata_size),
                    "document_count": count
                }
            }
        except Exception as e:
            print(f"❌ Erreur lors de l'analyse: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def create_chroma_instance(self, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.create_chroma_instance
        Crée/récupère une instance ChromaDB pour une collection.
        Équivalent au proxy ChromaKLKProxy.
        """
        try:
            # Vérifier/créer la collection
            collection = self.get_or_create_collection(collection_name)

            return {
                "success": True,
                "collection_name": collection_name,
                "message": f"Instance ChromaDB créée pour la collection '{collection_name}'",
                "collection_id": collection.id if hasattr(collection, 'id') else collection_name
            }
        except Exception as e:
            print(f"❌ Erreur lors de la création d'instance: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def create_analyzer_instance(self, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.create_analyzer_instance
        Crée une instance d'analyseur pour une collection.
        Équivalent au proxy ChromaAnalyzerProxy.
        """
        # Validation du paramètre collection_name
        if not collection_name:
            return {
                "success": False,
                "error": "collection_name est requis et ne peut pas être None ou vide",
                "collection_name": collection_name
            }

        try:
            print(f"🔍 create_analyzer_instance appelé avec collection_name: '{collection_name}'")

            # Analyser directement la collection
            analysis = self.analyze_collection(collection_name)

            if analysis["success"]:
                return {
                    "success": True,
                    "collection_name": collection_name,
                    "message": f"Analyseur créé pour la collection '{collection_name}'",
                    "analyzer_ready": True
                }
            else:
                return analysis
        except Exception as e:
            print(f"❌ Erreur lors de la création d'analyseur: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def generate_report(self, collection_name: str) -> dict:
        """
        RPC: CHROMA_VECTOR.generate_report
        Génère un rapport d'analyse pour une collection.
        Équivalent à ChromaAnalyzer.generate_report().
        """
        # Validation du paramètre collection_name
        if not collection_name:
            return {
                "success": False,
                "error": "collection_name est requis et ne peut pas être None ou vide",
                "collection_name": collection_name
            }

        try:
            print(f"🔍 generate_report appelé avec collection_name: '{collection_name}'")

            # Utiliser analyze_collection qui contient déjà toute la logique
            analysis = self.analyze_collection(collection_name)

            if analysis["success"]:
                # Retourner le rapport dans le format attendu par l'ancienne API
                return {
                    "success": True,
                    "collection_name": collection_name,
                    "report": analysis["analysis"],  # Les données d'analyse
                    "message": f"Rapport généré pour la collection '{collection_name}'"
                }
            else:
                return analysis
        except Exception as e:
            print(f"❌ Erreur lors de la génération du rapport: {e}")
            return {
                "success": False,
                "error": str(e),
                "collection_name": collection_name
            }

    def analyze_storage_full(self, collection_name: str, max_storage: float = 10.0) -> dict:
        """
        RPC: CHROMA_VECTOR.analyze_storage_full
        Analyse complète du stockage avec format attendu par l'application Reflex.
        Compatible avec l'ancienne méthode analyze_storage de base_state.py.

        Args:
            collection_name: Nom de la collection à analyser
            max_storage: Stockage maximum en GB (défaut: 10 GB)

        Returns:
            Format attendu par l'application Reflex:
            {
                "success": True,
                "data": {
                    "storage_size_gb": float,
                    "storage_percentage": int,
                    "storage_details": str
                }
            }
        """
        # Validation du paramètre collection_name
        if not collection_name:
            return {
                "success": False,
                "error": "collection_name est requis et ne peut pas être None ou vide"
            }

        try:
            print(f"🔍 analyze_storage_full appelé avec collection_name: '{collection_name}', max_storage: {max_storage} GB")

            # Utiliser analyze_collection pour récupérer les données
            analysis = self.analyze_collection(collection_name)

            if not analysis["success"]:
                return {
                    "success": False,
                    "error": analysis.get("error", "Erreur lors de l'analyse de la collection")
                }

            analysis_data = analysis["analysis"]

            # Convertir les bytes en GB
            total_size_bytes = analysis_data["total_size"]
            embeddings_size_bytes = analysis_data["embeddings_size"]
            documents_size_bytes = analysis_data["documents_size"]
            metadata_size_bytes = analysis_data["metadata_size"]
            document_count = analysis_data["document_count"]

            # Conversion bytes vers GB (1 GB = 1024^3 bytes)
            gb_factor = 1024 ** 3
            total_size_gb = total_size_bytes / gb_factor
            embeddings_size_gb = embeddings_size_bytes / gb_factor
            documents_size_gb = documents_size_bytes / gb_factor
            metadata_size_gb = metadata_size_bytes / gb_factor

            # Calcul du pourcentage d'utilisation
            storage_percentage = min(100, int((total_size_gb / max_storage) * 100)) if max_storage > 0 else 0

            # Fonction pour formater la taille (comme dans ChromaAnalyzer original)
            def format_size(size_in_bytes: int) -> str:
                if size_in_bytes < 1024:
                    return f"{size_in_bytes} B"
                elif size_in_bytes < 1024**2:
                    return f"{size_in_bytes / 1024:.2f} KB"
                elif size_in_bytes < 1024**3:
                    return f"{size_in_bytes / (1024**2):.2f} MB"
                elif size_in_bytes < 1024**4:
                    return f"{size_in_bytes / (1024**3):.2f} GB"
                else:
                    return f"{size_in_bytes / (1024**4):.2f} TB"

            # Calcul des pourcentages pour chaque catégorie
            if total_size_bytes > 0:
                embeddings_pct = (embeddings_size_bytes / total_size_bytes) * 100
                documents_pct = (documents_size_bytes / total_size_bytes) * 100
                metadata_pct = (metadata_size_bytes / total_size_bytes) * 100
            else:
                embeddings_pct = documents_pct = metadata_pct = 0

            # Format des détails comme attendu par l'application Reflex
            storage_details = (
                f"Total amount of documents: {document_count}\n"
                f"embeddings: {format_size(embeddings_size_bytes)} ({embeddings_pct:.1f}%)\n"
                f"documents: {format_size(documents_size_bytes)} ({documents_pct:.1f}%)\n"
                f"metadata: {format_size(metadata_size_bytes)} ({metadata_pct:.1f}%)"
            )

            result = {
                "success": True,
                "data": {
                    "storage_size_gb": round(total_size_gb, 6),  # Arrondi pour éviter les nombres trop longs
                    "storage_percentage": storage_percentage,
                    "storage_details": storage_details
                }
            }

            print(f"✅ analyze_storage_full résultat: {result}")
            return result

        except Exception as e:
            print(f"❌ Erreur lors de analyze_storage_full: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }


def get_chroma_vector_service() -> ChromaVectorService:
    """
    Retourne l'instance singleton de ChromaVectorService.
    Thread-safe, initialise au premier appel.
    """
    global _CHROMA_VECTOR_SERVICE_SINGLETON
    if _CHROMA_VECTOR_SERVICE_SINGLETON is None:
        _CHROMA_VECTOR_SERVICE_SINGLETON = ChromaVectorService()
    return _CHROMA_VECTOR_SERVICE_SINGLETON