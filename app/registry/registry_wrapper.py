"""
Wrapper transparent pour maintenir la compatibilité des APIs existantes
tout en intégrant le nouveau système de registre unifié.

Ce wrapper garantit que le code côté Reflex n'a AUCUN changement à faire.
"""

import os
import json
from typing import Optional, Dict, Any, List
from .unified_registry import get_unified_registry

class RegistryWrapper:
    """Wrapper transparent qui maintient les APIs existantes identiques."""
    
    def __init__(self):
        self.unified_enabled = os.getenv("UNIFIED_REGISTRY_ENABLED", "false").lower() == "true"
        self.unified_registry = None
        self.debug_enabled = os.getenv("REGISTRY_DEBUG", "false").lower() == "true"
        
        if self.unified_enabled:
            try:
                self.unified_registry = get_unified_registry()
                if self.debug_enabled:
                    print("✅ RegistryWrapper: Registre unifié activé")
            except Exception as e:
                print(f"❌ Erreur initialisation registre unifié: {e}")
                self.unified_enabled = False
        elif self.debug_enabled:
            print("📝 RegistryWrapper: Mode legacy (registre unifié désactivé)")
    
    def register_user(self, user_id: str, session_id: str, backend_route: str = None) -> dict:
        """
        Wrapper pour _registry_register_user - API IDENTIQUE
        Maintient le comportement exact de l'ancienne fonction.
        """
        
        # TOUJOURS exécuter l'ancien code (sécurité totale)
        legacy_result = self._legacy_register_user(user_id, session_id, backend_route)
        
        # SI activé, AUSSI utiliser le nouveau système EN PLUS
        if self.unified_enabled and self.unified_registry:
            try:
                # Récupérer les infos société depuis Firestore (comme maintenant)
                company_info = self._get_user_company_info(user_id)
                
                # Enregistrer dans le nouveau système EN PLUS
                self.unified_registry.register_user_session(
                    user_id=user_id,
                    session_id=session_id,
                    company_id=company_info.get("current_company", "default"),
                    authorized_companies=company_info.get("authorized_companies", []),
                    backend_route=backend_route
                )
                
                if self.debug_enabled:
                    print(f"✅ Sync registre unifié: user={user_id}, company={company_info.get('current_company')}")
                    
            except Exception as e:
                # En cas d'erreur, continuer avec l'ancien système
                print(f"⚠️ Erreur registre unifié (fallback vers legacy): {e}")
        
        # Retourner EXACTEMENT le même format qu'avant
        return legacy_result
    
    def unregister_session(self, session_id: str) -> bool:
        """
        Wrapper pour _registry_unregister_session - API IDENTIQUE
        """
        
        # TOUJOURS exécuter l'ancien code
        legacy_result = self._legacy_unregister_session(session_id)
        
        # SI activé, AUSSI nettoyer le nouveau système
        if self.unified_enabled and self.unified_registry:
            try:
                unified_result = self.unified_registry.unregister_user_session(session_id)
                if self.debug_enabled:
                    print(f"✅ Sync désenregistrement unifié: session={session_id}, success={unified_result}")
            except Exception as e:
                print(f"⚠️ Erreur désenregistrement unifié: {e}")
        
        return legacy_result
    
    def update_heartbeat(self, user_id: str) -> bool:
        """
        Nouveau wrapper pour les heartbeats utilisateur.
        """
        
        # Mettre à jour le registre unifié si activé
        if self.unified_enabled and self.unified_registry:
            try:
                return self.unified_registry.update_user_heartbeat(user_id)
            except Exception as e:
                print(f"⚠️ Erreur heartbeat unifié pour {user_id}: {e}")
                return False
        
        return True  # Mode legacy, toujours OK
    
    def update_user_service(self, user_id: str, service_name: str, service_data: dict) -> bool:
        """
        Wrapper pour mettre à jour les données d'un service utilisateur.
        Utilisé par ChromaDB, LLM, etc.
        """
        
        if self.unified_enabled and self.unified_registry:
            try:
                result = self.unified_registry.update_user_service(user_id, service_name, service_data)
                if self.debug_enabled:
                    print(f"✅ Sync service {service_name} pour user {user_id}: {service_data}")
                return result
            except Exception as e:
                print(f"⚠️ Erreur sync service {service_name} pour {user_id}: {e}")
                return False
        
        return True  # Mode legacy, pas de sync
    
    # ========== Méthodes legacy (comportement exact de l'ancien système) ==========
    
    def _legacy_register_user(self, user_id: str, session_id: str, backend_route: str = None) -> dict:
        """Implémentation exacte de l'ancienne fonction _registry_register_user."""
        try:
            from ..redis_client import get_redis
            import time
            
            r = get_redis()
            key = f"registry:user:{user_id}"
            payload = {
                "user_id": user_id,
                "session_id": session_id,
                "backend_route": backend_route or "",
                "last_seen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            r.hset(key, mapping=payload)
            r.expire(key, 24 * 3600)
            return payload
        except Exception as e:
            print(f"❌ Erreur _legacy_register_user: {e}")
            raise
    
    def _legacy_unregister_session(self, session_id: str) -> bool:
        """Implémentation exacte de l'ancienne fonction _registry_unregister_session."""
        try:
            from ..redis_client import get_redis
            import logging
            
            logger = logging.getLogger("registry.wrapper")
            r = get_redis()
            cursor = 0
            removed = False
            pattern = "registry:user:*"
            user_id = None
            
            # ÉTAPE 1 : Trouver user_id et supprimer registry:user:*
            while True:
                cursor, keys = r.scan(cursor=cursor, match=pattern, count=200)
                for k in keys:
                    try:
                        sid = r.hget(k, "session_id")
                        if sid and sid.decode() == session_id:
                            # Récupérer user_id AVANT de supprimer
                            user_id = r.hget(k, "user_id")
                            if user_id:
                                user_id = user_id.decode()
                            r.delete(k)
                            removed = True
                            logger.info(f"🗑️ [SIGNOUT] Supprimé registry:user pour session={session_id}")
                    except Exception:
                        continue
                if cursor == 0:
                    break
            
            # ⭐ ÉTAPE 2 : Nettoyer TOUTES les sessions LLM de cet utilisateur
            if user_id:
                try:
                    # Supprimer toutes les clés llm_init:user_id:*
                    llm_pattern = f"llm_init:{user_id}:*"
                    cursor = 0
                    llm_cleaned = 0
                    
                    while True:
                        cursor, keys = r.scan(cursor=cursor, match=llm_pattern, count=200)
                        for k in keys:
                            try:
                                r.delete(k)
                                llm_cleaned += 1
                            except Exception as e:
                                logger.error(f"Erreur suppression {k}: {e}")
                        if cursor == 0:
                            break
                    
                    if llm_cleaned > 0:
                        logger.info(
                            f"✅ [SIGNOUT] Nettoyé {llm_cleaned} session(s) LLM pour user_id={user_id}"
                        )
                    
                    # Supprimer aussi les clés session:user_id:* (sessions LLM en mémoire)
                    session_pattern = f"session:{user_id}:*"
                    cursor = 0
                    session_cleaned = 0
                    
                    while True:
                        cursor, keys = r.scan(cursor=cursor, match=session_pattern, count=200)
                        for k in keys:
                            try:
                                r.delete(k)
                                session_cleaned += 1
                            except Exception as e:
                                logger.error(f"Erreur suppression {k}: {e}")
                        if cursor == 0:
                            break
                    
                    if session_cleaned > 0:
                        logger.info(
                            f"✅ [SIGNOUT] Nettoyé {session_cleaned} session(s) mémoire pour user_id={user_id}"
                        )
                        
                except Exception as e:
                    logger.error(f"❌ [SIGNOUT] Erreur nettoyage sessions LLM: {e}")
            
            return removed
        except Exception as e:
            print(f"❌ Erreur _legacy_unregister_session: {e}")
            return False
    
    def _get_user_company_info(self, user_id: str) -> dict:
        """
        Récupère les infos société depuis Firestore (logique existante).
        Utilise exactement la même logique que le système actuel.
        """
        try:
            from ..firebase_client import get_firestore
            
            db = get_firestore()
            doc = db.collection("listeners_registry").document(user_id).get()
            
            if doc.exists:
                data = doc.to_dict() or {}
                authorized_companies = data.get("authorized_companies_ids", [])
                current_company = authorized_companies[0] if authorized_companies else "default"
                
                return {
                    "current_company": current_company,
                    "authorized_companies": authorized_companies
                }
        except Exception as e:
            if self.debug_enabled:
                print(f"⚠️ Erreur récupération infos société pour {user_id}: {e}")
        
        return {"current_company": "default", "authorized_companies": []}


class ChromaRegistryWrapper:
    """Wrapper spécifique pour les fonctions ChromaDB."""
    
    def __init__(self):
        self.unified_enabled = os.getenv("UNIFIED_REGISTRY_ENABLED", "false").lower() == "true"
        self.registry_wrapper = get_registry_wrapper() if self.unified_enabled else None
        self.debug_enabled = os.getenv("REGISTRY_DEBUG", "false").lower() == "true"
    
    def register_collection_user(self, user_id: str, collection_name: str, session_id: str) -> dict:
        """
        Wrapper pour ChromaVectorService.register_collection_user
        Maintient l'API exacte + sync silencieuse avec registre unifié.
        """
        
        # ANCIEN comportement maintenu à 100% (appel à la méthode originale)
        result = self._legacy_register_collection(user_id, collection_name, session_id)
        
        # NOUVEAU : Sync silencieuse avec le registre unifié
        if self.unified_enabled and self.registry_wrapper:
            try:
                # Ajouter la collection au registre unifié
                self.registry_wrapper.update_user_service(
                    user_id, 
                    "chroma", 
                    {
                        "collections": [collection_name],
                        "last_heartbeat": result.get("registered_at")
                    }
                )
                
                if self.debug_enabled:
                    print(f"✅ Sync ChromaDB collection: user={user_id}, collection={collection_name}")
                    
            except Exception as e:
                # Erreur silencieuse - ne pas impacter l'ancien système
                print(f"⚠️ Erreur sync ChromaDB unifié: {e}")
        
        return result  # Format IDENTIQUE qu'avant
    
    def heartbeat_collection(self, user_id: str, collection_name: str) -> dict:
        """Wrapper pour ChromaVectorService.heartbeat_collection"""
        
        # Comportement legacy
        result = self._legacy_heartbeat_collection(user_id, collection_name)
        
        # Sync avec registre unifié
        if self.unified_enabled and self.registry_wrapper:
            try:
                import time
                self.registry_wrapper.update_user_service(
                    user_id,
                    "chroma", 
                    {
                        "collections": [collection_name],
                        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    }
                )
            except Exception as e:
                print(f"⚠️ Erreur sync heartbeat ChromaDB: {e}")
        
        return result
    
    def unregister_collection_user(self, user_id: str, collection_name: str) -> dict:
        """Wrapper pour ChromaVectorService.unregister_collection_user"""
        
        # Comportement legacy
        result = self._legacy_unregister_collection(user_id, collection_name)
        
        # Sync avec registre unifié
        if self.unified_enabled and self.registry_wrapper:
            try:
                # Récupérer les collections actuelles et retirer celle-ci
                user_registry = self.registry_wrapper.unified_registry.get_user_registry(user_id)
                if user_registry:
                    collections = user_registry.get("services", {}).get("chroma", {}).get("collections", [])
                    if collection_name in collections:
                        collections.remove(collection_name)
                    
                    self.registry_wrapper.update_user_service(
                        user_id,
                        "chroma",
                        {"collections": collections}
                    )
            except Exception as e:
                print(f"⚠️ Erreur sync désenregistrement ChromaDB: {e}")
        
        return result
    
    # ========== Méthodes legacy ChromaDB ==========
    
    def _legacy_register_collection(self, user_id: str, collection_name: str, session_id: str) -> dict:
        """Implémentation legacy exacte pour l'enregistrement de collection."""
        try:
            from ..redis_client import get_redis
            import time
            
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
            r.expire(key, 90)  # TTL de 90 secondes comme avant
            return payload
        except Exception as e:
            print(f"❌ Erreur _legacy_register_collection: {e}")
            raise
    
    def _legacy_heartbeat_collection(self, user_id: str, collection_name: str) -> dict:
        """Implémentation legacy exacte pour le heartbeat de collection."""
        try:
            from ..redis_client import get_redis
            import time
            
            r = get_redis()
            key = f"registry:chroma:{user_id}:{collection_name}"
            
            # Vérifier si la clé existe
            if not r.exists(key):
                return {"user_id": user_id, "collection_name": collection_name, "heartbeat_updated": False}
            
            # Mettre à jour le heartbeat
            r.hset(key, "last_heartbeat", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            r.expire(key, 90)  # Renouveler le TTL
            
            return {"user_id": user_id, "collection_name": collection_name, "heartbeat_updated": True}
        except Exception as e:
            print(f"❌ Erreur _legacy_heartbeat_collection: {e}")
            return {"user_id": user_id, "collection_name": collection_name, "heartbeat_updated": False}
    
    def _legacy_unregister_collection(self, user_id: str, collection_name: str) -> dict:
        """Implémentation legacy exacte pour le désenregistrement de collection."""
        try:
            from ..redis_client import get_redis
            
            r = get_redis()
            key = f"registry:chroma:{user_id}:{collection_name}"
            result = r.delete(key)
            success = bool(result)
            
            return {"user_id": user_id, "collection_name": collection_name, "unregistered": success}
        except Exception as e:
            print(f"❌ Erreur _legacy_unregister_collection: {e}")
            return {"user_id": user_id, "collection_name": collection_name, "unregistered": False}


# Singletons pour les wrappers
_registry_wrapper: Optional[RegistryWrapper] = None
_chroma_wrapper: Optional[ChromaRegistryWrapper] = None

def get_registry_wrapper() -> RegistryWrapper:
    """Récupère l'instance singleton du wrapper de registre."""
    global _registry_wrapper
    if _registry_wrapper is None:
        _registry_wrapper = RegistryWrapper()
    return _registry_wrapper

def get_chroma_registry_wrapper() -> ChromaRegistryWrapper:
    """Récupère l'instance singleton du wrapper ChromaDB."""
    global _chroma_wrapper
    if _chroma_wrapper is None:
        _chroma_wrapper = ChromaRegistryWrapper()
    return _chroma_wrapper

