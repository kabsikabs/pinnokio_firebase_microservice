"""
JobLoader - Chargement des jobs depuis cache Redis ou sources (Firebase/Drive/ERP)
Utilisé lors de l'initialisation de la session LLM pour fournir les métriques au prompt.
"""

import logging
import json
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("pinnokio.job_loader")


class JobLoader:
    """
    Charge les jobs de tous les départements avec cache Redis (mode UI).
    
    Départements gérés :
    - APBOOKEEPER : Factures fournisseur (source: Firebase)
    - ROUTER : Documents à router (source: Drive + Firebase)
    - BANK : Transactions bancaires (source: ERP Odoo)
    
    Workflow :
    1. Mode UI : Cache Redis → Fallback sources
    2. Mode BACKEND : Accès direct sources
    3. Calcul des métriques agrégées
    """
    
    def __init__(self, user_id: str, company_id: str, client_uuid: Optional[str] = None):
        self.user_id = user_id
        self.company_id = company_id
        self.client_uuid = client_uuid
    
    @staticmethod
    def _normalize_timestamp(timestamp_value) -> str:
        """
        Convertit n'importe quel type de timestamp en string ISO.
        Gère : DatetimeWithNanoseconds (Firebase), datetime, string, etc.
        """
        if not timestamp_value:
            return ''
        
        try:
            # Si c'est déjà une string, la retourner telle quelle
            if isinstance(timestamp_value, str):
                return timestamp_value
            
            # Si c'est un objet avec isoformat (datetime, DatetimeWithNanoseconds, etc.)
            if hasattr(timestamp_value, 'isoformat'):
                return timestamp_value.isoformat()
            
            # Sinon, convertir en string
            return str(timestamp_value)
        except Exception as e:
            logger.warning(f"[JOB_LOADER] Erreur conversion timestamp: {e}")
            return str(timestamp_value) if timestamp_value else ''
    
    async def load_all_jobs(self, mode: str, user_context: Dict) -> Tuple[Dict, Dict]:
        """
        Charge tous les jobs de tous les départements.
        
        Args:
            mode: "UI" (avec cache) ou "BACKEND" (sans cache)
            user_context: Contexte utilisateur avec mandate_path, bank_erp, etc.
        
        Returns:
            (jobs_data, jobs_metrics)
            
            jobs_data: {
                "APBOOKEEPER": {
                    "to_do": [...],
                    "in_process": [...],
                    "pending": [...],
                    "processed": [...]
                },
                "ROUTER": {...},
                "BANK": {...}
            }
            
            jobs_metrics: {
                "APBOOKEEPER": {
                    "to_do": 5,
                    "in_process": 2,
                    "pending": 1,
                    "processed": 12
                },
                "ROUTER": {...},
                "BANK": {
                    "accounts": {
                        "FR76...": {
                            "to_reconcile": 45,
                            "total_amount": 12500.00
                        }
                    }
                }
            }
        """
        logger.info(f"[JOB_LOADER] ═══ DÉBUT Chargement jobs ═══")
        logger.info(f"[JOB_LOADER] Mode: {mode} | User: {self.user_id} | Company: {self.company_id}")
        
        # 🔍 Afficher les clés Redis qui seront utilisées (pour debug)
        if mode == "UI":
            ap_key = self._build_reflex_cache_key("APBOOKEEPER")
            router_key = self._build_reflex_cache_key("ROUTER")
            bank_key = self._build_reflex_cache_key("BANK")
            logger.info(f"[JOB_LOADER] 🔑 Clés Redis (format Reflex):")
            logger.info(f"[JOB_LOADER]   - APBookkeeper: {ap_key}")
            logger.info(f"[JOB_LOADER]   - Router: {router_key}")
            logger.info(f"[JOB_LOADER]   - Bank: {bank_key}")
        else:
            logger.info(f"[JOB_LOADER] Mode BACKEND → Appel direct aux sources (pas de cache)")
        
        jobs_data = {}
        
        # Charger les jobs de chaque département en parallèle
        apbookeeper_task = self.load_apbookeeper_jobs(mode)
        router_task = self.load_router_jobs(mode, user_context)
        bank_task = self.load_bank_transactions(mode, user_context)
        
        # Attendre tous les chargements
        apbookeeper_data, router_data, bank_data = await asyncio.gather(
            apbookeeper_task,
            router_task,
            bank_task,
            return_exceptions=True
        )
        
        # 🔍 LOGS DE DIAGNOSTIC - Avant assemblage
        logger.info(f"[JOB_LOADER] 🔍 DIAGNOSTIC AVANT assemblage - "
                   f"router_data type: {type(router_data)}, "
                   f"clés: {list(router_data.keys()) if isinstance(router_data, dict) else 'N/A'}")
        if isinstance(router_data, dict):
            unprocessed_list = router_data.get('unprocessed', [])
            logger.info(f"[JOB_LOADER] 🔍 DIAGNOSTIC router_data.unprocessed - "
                       f"Type: {type(unprocessed_list)}, "
                       f"Longueur: {len(unprocessed_list) if isinstance(unprocessed_list, list) else 'N/A'}")
            if isinstance(unprocessed_list, list) and len(unprocessed_list) > 0:
                logger.info(f"[JOB_LOADER] 🔍 DIAGNOSTIC Premier doc unprocessed - "
                           f"Clés: {list(unprocessed_list[0].keys()) if isinstance(unprocessed_list[0], dict) else 'N/A'}")
        
        # Gérer les erreurs potentielles
        jobs_data["APBOOKEEPER"] = apbookeeper_data if not isinstance(apbookeeper_data, Exception) else {}
        jobs_data["ROUTER"] = router_data if not isinstance(router_data, Exception) else {}
        jobs_data["BANK"] = bank_data if not isinstance(bank_data, Exception) else {}
        
        # 🔍 LOGS DE DIAGNOSTIC - Après assemblage
        logger.info(f"[JOB_LOADER] 🔍 DIAGNOSTIC APRÈS assemblage - "
                   f"jobs_data['ROUTER'] type: {type(jobs_data['ROUTER'])}, "
                   f"clés: {list(jobs_data['ROUTER'].keys()) if isinstance(jobs_data['ROUTER'], dict) else 'N/A'}")
        if isinstance(jobs_data['ROUTER'], dict):
            final_unprocessed = jobs_data['ROUTER'].get('unprocessed', [])
            logger.info(f"[JOB_LOADER] 🔍 DIAGNOSTIC jobs_data['ROUTER']['unprocessed'] - "
                       f"Longueur: {len(final_unprocessed) if isinstance(final_unprocessed, list) else 'N/A'}")
        
        # Calculer les métriques
        jobs_metrics = self.calculate_metrics(jobs_data)
        
        logger.info(f"[JOB_LOADER] ✅ Jobs chargés - AP: {jobs_metrics['APBOOKEEPER']['to_do']}, "
                   f"Router: {jobs_metrics['ROUTER']['to_process']}, "
                   f"Bank: {jobs_metrics['BANK']['total_accounts']} comptes")
        
        return jobs_data, jobs_metrics
    
    async def load_apbookeeper_jobs(self, mode: str) -> Dict:
        """
        Charge les jobs APBookkeeper depuis Redis → Firebase.
        
        Returns:
            {
                "to_do": [{"job_id": "...", "file_name": "...", "status": "...", ...}],
                "in_process": [...],
                "pending": [...],
                "processed": [...]
            }
        """
        try:
            logger.info(f"[JOB_LOADER] Chargement APBookkeeper jobs...")
            
            # Mode UI : Vérifier cache Redis
            if mode == "UI":
                cached_data = await self._get_from_cache("APBOOKEEPER")
                if cached_data:
                    to_do_count = len(cached_data.get("to_do", [])) if isinstance(cached_data.get("to_do"), list) else 0
                    in_process_count = len(cached_data.get("in_process", [])) if isinstance(cached_data.get("in_process"), list) else 0
                    pending_count = len(cached_data.get("pending", [])) if isinstance(cached_data.get("pending"), list) else 0
                    
                    # ⭐ Vérifier que le cache contient réellement des données
                    total_docs = to_do_count + in_process_count + pending_count
                    if total_docs > 0:
                        logger.info(f"[JOB_LOADER] ✅ APBookkeeper depuis cache - to_do: {to_do_count}, in_process: {in_process_count}, pending: {pending_count}")
                        logger.info(f"[JOB_LOADER] 🔍 DEBUG APBookkeeper - Structure: {list(cached_data.keys())}")
                        return cached_data
                    else:
                        logger.warning(f"[JOB_LOADER] ⚠️ Cache APBookkeeper VIDE (0 documents) - Fallback vers Firebase")
                        # Ne pas retourner → continue vers fallback Firebase
            
            # Fallback ou mode BACKEND : Fetch depuis Firebase
            logger.info(f"[JOB_LOADER] Fetch APBookkeeper depuis Firebase...")
            data = await self._fetch_apbookeeper_from_firebase()
            
            # Mettre en cache si mode UI
            if mode == "UI" and data:
                await self._set_to_cache("APBOOKEEPER", data)
            
            return data
        
        except Exception as e:
            logger.error(f"[JOB_LOADER] Erreur chargement APBookkeeper: {e}", exc_info=True)
            return {"to_do": [], "in_process": [], "pending": [], "processed": []}
    
    async def load_router_jobs(self, mode: str, user_context: Dict) -> Dict:
        """
        Charge les jobs Router depuis Redis → Drive + Firebase.
        
        Returns:
            {
                "to_process": [{"drive_file_id": "...", "file_name": "...", ...}],
                "in_process": [...]
            }
        """
        try:
            logger.info(f"[JOB_LOADER] Chargement Router jobs...")
            
            # Mode UI : Vérifier cache Redis
            if mode == "UI":
                cached_data = await self._get_from_cache("ROUTER")
                if cached_data:
                    unprocessed_count = len(cached_data.get("to_process", [])) if isinstance(cached_data.get("to_process"), list) else 0
                    in_process_count = len(cached_data.get("in_process", [])) if isinstance(cached_data.get("in_process"), list) else 0
                    processed_count = len(cached_data.get("processed", [])) if isinstance(cached_data.get("processed"), list) else 0
                    
                    # ⭐ Vérifier que le cache contient réellement des données
                    total_docs = unprocessed_count + in_process_count + processed_count
                    if total_docs > 0:
                        logger.info(f"[JOB_LOADER] ✅ Router depuis cache - unprocessed: {unprocessed_count}, in_process: {in_process_count}, processed: {processed_count}")
                        logger.info(f"[JOB_LOADER] 🔍 DEBUG Router - Structure: {list(cached_data.keys())}")
                        return cached_data
                    else:
                        logger.warning(f"[JOB_LOADER] ⚠️ Cache Router VIDE (0 documents) - Fallback vers Drive+Firebase")
                        # Ne pas retourner → continue vers fallback Drive
            
            # Fallback ou mode BACKEND : Fetch depuis Drive + Firebase
            logger.info(f"[JOB_LOADER] Fetch Router depuis Drive + Firebase...")
            data = await self._fetch_router_from_drive_firebase(user_context)
            
            # Mettre en cache si mode UI
            if mode == "UI" and data:
                await self._set_to_cache("ROUTER", data)
            
            return data
        
        except Exception as e:
            logger.error(f"[JOB_LOADER] Erreur chargement Router: {e}", exc_info=True)
            return {"to_process": [], "in_process": []}
    
    async def load_bank_transactions(self, mode: str, user_context: Dict) -> Dict:
        """
        Charge les transactions bancaires depuis Redis → ERP Odoo.
        
        Returns:
            {
                "to_reconcile": [{"transaction_id": "...", "date": "...", "amount": ..., ...}],
                "pending": [...],
                "in_process": [...],
                "in_process_batches": [...]
            }
        """
        try:
            logger.info(f"[JOB_LOADER] Chargement Bank transactions...")
            
            # Vérifier que l'ERP est Odoo (champ "mandate_bank_erp" est un string)
            mandate_bank_erp = user_context.get("mandate_bank_erp")
            
            logger.info(f"[JOB_LOADER] 🔍 DEBUG Bank ERP - mandate_bank_erp={mandate_bank_erp}")
            
            if mandate_bank_erp != "odoo":
                logger.info(f"[JOB_LOADER] ERP non-Odoo ({mandate_bank_erp}), pas de transactions bancaires")
                return {"to_reconcile": [], "pending": [], "in_process": [], "in_process_batches": []}
            
            # Mode UI : Vérifier cache Redis
            if mode == "UI":
                cached_data = await self._get_from_cache("BANK")
                if cached_data:
                    to_reconcile_count = len(cached_data.get("to_reconcile", [])) if isinstance(cached_data.get("to_reconcile"), list) else 0
                    in_process_count = len(cached_data.get("in_process", [])) if isinstance(cached_data.get("in_process"), list) else 0
                    pending_count = len(cached_data.get("pending", [])) if isinstance(cached_data.get("pending"), list) else 0
                    
                    # ⭐ Vérifier que le cache contient réellement des données
                    total_txs = to_reconcile_count + in_process_count + pending_count
                    if total_txs > 0:
                        logger.info(f"[JOB_LOADER] ✅ Bank depuis cache - to_reconcile: {to_reconcile_count}, in_process: {in_process_count}, pending: {pending_count}")
                        logger.info(f"[JOB_LOADER] 🔍 DEBUG Bank - Structure: {list(cached_data.keys())}")
                        return cached_data
                    else:
                        logger.warning(f"[JOB_LOADER] ⚠️ Cache Bank VIDE (0 transactions) - Fallback vers ERP Odoo")
                        # Ne pas retourner → continue vers fallback ERP
            
            # Fallback ou mode BACKEND : Fetch depuis ERP Odoo
            logger.info(f"[JOB_LOADER] Fetch Bank depuis ERP Odoo...")
            data = await self._fetch_bank_from_erp(user_context)
            
            # Mettre en cache si mode UI
            if mode == "UI" and data:
                await self._set_to_cache("BANK", data)
            
            return data
        
        except Exception as e:
            logger.error(f"[JOB_LOADER] Erreur chargement Bank: {e}", exc_info=True)
            return {"to_reconcile": [], "pending": [], "in_process": [], "in_process_batches": []}
    
    def _build_reflex_cache_key(self, department: str) -> str:
        """
        Construit la clé Redis compatible avec le format Reflex.
        
        Format Reflex : cache:{user_id}:{company_id}:{data_type}:{sub_type}
        """
        # Mapping département → format Reflex (data_type:sub_type)
        reflex_mapping = {
            "BANK": "bank:transactions",
            "ROUTER": "drive:documents",
            "APBOOKEEPER": "apbookeeper:documents"
        }
        
        data_type_sub = reflex_mapping.get(department)
        if not data_type_sub:
            # Fallback si département inconnu
            data_type_sub = f"{department.lower()}:data"
        
        cache_key = f"cache:{self.user_id}:{self.company_id}:{data_type_sub}"
        return cache_key
    
    async def _get_from_cache(self, department: str) -> Optional[Dict]:
        """Récupère les jobs depuis le cache Redis (format Reflex)."""
        try:
            from ...redis_client import get_redis
            
            redis_client = get_redis()
            cache_key = self._build_reflex_cache_key(department)
            
            cached_data = redis_client.get(cache_key)
            
            if cached_data:
                parsed = json.loads(cached_data)
                
                # Extraire les données (format Reflex uniforme)
                data = parsed.get("data")
                cached_at = parsed.get("cached_at", "unknown")
                
                logger.info(f"[JOB_LOADER] ✅ CACHE HIT (Reflex): {cache_key} | Cached: {cached_at}")
                return data
            
            logger.info(f"[JOB_LOADER] ❌ CACHE MISS (Reflex): {cache_key}")
            return None
        
        except Exception as e:
            logger.warning(f"[JOB_LOADER] Erreur lecture cache {department}: {e}")
            return None
    
    def _serialize_for_json(self, obj):
        """Convertit récursivement les objets Firebase DatetimeWithNanoseconds en strings ISO."""
        from google.cloud.firestore_v1._helpers import DatetimeWithNanoseconds
        
        if isinstance(obj, DatetimeWithNanoseconds):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._serialize_for_json(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_for_json(item) for item in obj]
        else:
            return obj
    
    async def _set_to_cache(self, department: str, data: Dict, ttl: int = 3600):
        """Stocke les jobs dans le cache Redis (format Reflex compatible)."""
        try:
            from ...redis_client import get_redis
            
            redis_client = get_redis()
            cache_key = self._build_reflex_cache_key(department)
            
            # Mapping pour le champ "source" (format Reflex)
            source_mapping = {
                "BANK": "bank.transactions",
                "ROUTER": "router.documents",
                "APBOOKEEPER": "apbookeeper.documents"
            }
            
            # Convertir les DatetimeWithNanoseconds en strings ISO
            serializable_data = self._serialize_for_json(data)
            
            # Payload compatible avec le format Reflex
            cached_payload = {
                "data": serializable_data,
                "cached_at": datetime.now().isoformat(),
                "ttl_seconds": ttl,  # Reflex utilise "ttl_seconds"
                "source": source_mapping.get(department, f"{department.lower()}.data")
            }
            
            redis_client.setex(
                cache_key,
                ttl,
                json.dumps(cached_payload)
            )
            
            data_size = len(str(data)) if data else 0
            logger.info(f"[JOB_LOADER] ✅ Cache mis à jour (Reflex): {cache_key} | TTL: {ttl}s | Taille: {data_size} car.")
        
        except Exception as e:
            logger.warning(f"[JOB_LOADER] Erreur écriture cache {department}: {e}")
    
    async def _fetch_apbookeeper_from_firebase(self) -> Dict:
        """
        Récupère les documents APBookkeeper depuis Firebase.
        Basé sur la logique de Reflex (voir JobManager_SPT.md lignes 661-808).
        """
        try:
            from ...firebase_providers import FirebaseManagement
            
            firebase_service = FirebaseManagement()
            departement = 'APbookeeper'
            
            # Helper pour créer les items
            def create_document_item(doc):
                return {
                    "job_id": doc['data'].get('job_id', ''),
                    "drive_file_id": doc['data'].get('drive_file_id', ''),
                    "file_name": doc['data'].get('file_name', ''),
                    "status": doc['data'].get('status', 'to_process'),
                    "timestamp": self._normalize_timestamp(doc['data'].get('timestamp', '')),
                    "uri_drive_link": doc['data'].get('uri_drive_link', ''),
                    "source": doc['data'].get('source', ''),
                    "client": doc['data'].get('client', '')
                }
            
            # TO_DO documents
            todo_docs = firebase_service.fetch_journal_entries_by_mandat_id(
                self.user_id,
                self.company_id,
                source='documents/accounting/invoices/doc_to_do',
                departement=departement
            )
            
            items_to_do = [create_document_item(doc) for doc in todo_docs]
            items_in_process = []
            final_items_to_do = []
            
            # Vérifier les statuts avec notifications Firebase
            for item in items_to_do:
                notification = firebase_service.check_job_status(
                    user_id=self.user_id,
                    job_id=item["job_id"]
                )
                
                if notification and notification.get('function_name') == 'APbookeeper':
                    firebase_status = notification.get('status')
                    
                    if firebase_status in ['running', 'in queue', 'stopping']:
                        item["status"] = firebase_status
                        items_in_process.append(item)
                    elif firebase_status == 'pending':
                        pass  # Sera géré dans la section PENDING
                    else:
                        if firebase_status in ['error', 'stopped']:
                            item["status"] = firebase_status
                        elif firebase_status in ['completed', 'success', 'close']:
                            item["status"] = 'completed'
                        final_items_to_do.append(item)
                else:
                    final_items_to_do.append(item)
            
            # PENDING documents
            pending_docs = firebase_service.fetch_pending_journal_entries_by_mandat_id(
                self.user_id,
                self.company_id,
                source='documents/accounting/invoices/doc_to_do',
                departement=departement
            )
            
            items_pending = []
            for doc in pending_docs:
                doc_item = create_document_item(doc)
                notification = firebase_service.check_job_status(
                    user_id=self.user_id,
                    job_id=doc_item["job_id"]
                )
                
                if notification and notification.get('function_name') == 'APbookeeper':
                    if notification.get('status') == 'pending':
                        doc_item["status"] = 'pending'
                        items_pending.append(doc_item)
            
            # PROCESSED documents
            booked_docs = firebase_service.fetch_journal_entries_by_mandat_id(
                self.user_id,
                self.company_id,
                source='documents/invoices/doc_booked',
                departement=departement
            )
            
            items_booked = [create_document_item(doc) for doc in booked_docs]
            for item in items_booked:
                item["status"] = 'completed'
            
            logger.info(f"[JOB_LOADER] APBookkeeper: {len(final_items_to_do)} to_do, "
                       f"{len(items_in_process)} in_process, {len(items_pending)} pending, "
                       f"{len(items_booked)} processed")
            
            return {
                "to_do": final_items_to_do,
                "in_process": items_in_process,
                "pending": items_pending,
                "processed": items_booked
            }
        
        except Exception as e:
            logger.error(f"[JOB_LOADER] Erreur fetch APBookkeeper Firebase: {e}", exc_info=True)
            return {"to_do": [], "in_process": [], "pending": [], "processed": []}
    
    async def _fetch_router_from_drive_firebase(self, user_context: Dict) -> Dict:
        """
        Récupère les documents Router depuis Drive + Firebase.
        Basé sur la logique de Reflex (voir JobManager_SPT.md lignes 542-652).
        """
        try:
            from ...firebase_providers import FirebaseManagement
            
            # ✅ Récupérer input_drive_doc_id depuis le contexte (pour Router)
            input_drive_id = user_context.get("input_drive_doc_id") or user_context.get("mandate_input_drive_doc_id")
            
            if not input_drive_id:
                logger.warning(f"[JOB_LOADER] Pas de input_drive_doc_id dans le contexte")
                return {"to_process": [], "in_process": []}
            
            # 1. Récupérer les fichiers depuis Drive (nouveau singleton pattern)
            from ...driveClientService import get_drive_client_service
            drive_service = get_drive_client_service('prod')
            drive_files = await drive_service.list_files_in_doc_to_do(self.user_id,input_drive_id)
            
            if isinstance(drive_files, dict) and "erreur" in drive_files:
                error_msg = drive_files['erreur']
                logger.error(f"[JOB_LOADER] Erreur Drive: {error_msg}")
                
                # Détecter si reconnexion OAuth requise
                if drive_files.get('oauth_reauth_required', False):
                    logger.warning(f"[JOB_LOADER] ⚠️ OAuth reauth requis - invalid_grant détecté")
                
                return {"unprocessed": [], "in_process": [], "processed": []}
            
            if not drive_files:
                logger.info(f"[JOB_LOADER] Aucun fichier Router trouvé dans Drive")
                return {"unprocessed": [], "in_process": [], "processed": []}
            
            # 2. Créer les items Drive (FORMAT REFLEX UNIFORME)
            all_drive_documents = []
            for doc in drive_files:
                drive_doc = {
                    "id": doc.get('id', ''),                      # ← Format Reflex
                    "name": doc.get('name', ''),                  # ← Format Reflex
                    "created_time": self._normalize_timestamp(doc.get('createdTime', '')),
                    "status": "to_process",
                    "router_drive_view_link": doc.get('webViewLink', ''),
                    "client": user_context.get("company_name", "")  # ← Ajouté pour Reflex
                }
                all_drive_documents.append(drive_doc)
            
            # 3. Vérifier les statuts avec Firebase
            firebase_service = FirebaseManagement()
            drive_documents_unprocessed = []
            drive_documents_in_process = []
            drive_documents_processed = []
            
            for drive_doc in all_drive_documents:
                notification = firebase_service.check_job_status(
                    user_id=self.user_id,
                    file_id=drive_doc["id"]  # ← Utiliser "id" format Reflex
                )
                
                if notification and notification.get('function_name') == 'Router':
                    firebase_status = notification.get('status')
                    
                    if firebase_status in ['running', 'in queue', 'stopping']:
                        drive_doc["status"] = firebase_status
                        drive_documents_in_process.append(drive_doc)
                    elif firebase_status in ['completed', 'success']:
                        drive_doc["status"] = 'routed'
                        drive_documents_processed.append(drive_doc)  # ← Ajouté pour Reflex
                    else:
                        if firebase_status == 'error':
                            drive_doc["status"] = 'error'
                        elif firebase_status == 'pending':
                            drive_doc["status"] = 'pending'
                        drive_documents_unprocessed.append(drive_doc)
                else:
                    drive_documents_unprocessed.append(drive_doc)
            
            logger.info(f"[JOB_LOADER] Router: {len(drive_documents_unprocessed)} unprocessed, "
                       f"{len(drive_documents_in_process)} in_process, {len(drive_documents_processed)} processed")
            
            # FORMAT REFLEX UNIFORME
            return {
                "to_process": drive_documents_unprocessed,  # ← Format Reflex
                "in_process": drive_documents_in_process,
                "processed": drive_documents_processed        # ← Format Reflex
            }
        
        except Exception as e:
            logger.error(f"[JOB_LOADER] Erreur fetch Router: {e}", exc_info=True)
            return {"unprocessed": [], "in_process": [], "processed": []}
    
    async def _fetch_bank_from_erp(self, user_context: Dict) -> Dict:
        """
        Récupère les transactions bancaires depuis ERP Odoo via le singleton ERPService.
        
        ⭐ NOUVELLE ARCHITECTURE :
        - Utilise ERPService.get_odoo_bank_statement_move_line_not_rec()
        - Gestion automatique des credentials (Firebase + Secret Manager)
        - Cache de connexion thread-safe (TTL 30 minutes)
        - Nettoyage automatique des connexions expirées
        
        Returns:
            Dict avec:
            - to_reconcile, pending, in_process, in_process_batches: listes de transactions
            - bank_accounts: liste des comptes bancaires (journal_id)
            - selected_bank_account: compte bancaire sélectionné par défaut
            - warning_message: message d'info/erreur à afficher dans le prompt (optionnel)
        """
        try:
            # ✅ Vérifier le type d'ERP bancaire (champ simple string)
            bank_erp_type = user_context.get("mandate_bank_erp", "").lower()
            
            # Cas 1 : Pas de configuration ERP
            if not bank_erp_type:
                logger.warning(f"[JOB_LOADER] Aucune configuration ERP bancaire trouvée")
                return {
                    "to_reconcile": [], 
                    "pending": [], 
                    "in_process": [], 
                    "in_process_batches": [],
                    "warning_message": "⚠️ Aucune configuration ERP bancaire configurée. L'accès aux transactions bancaires n'est pas disponible."
                }
            
            # Cas 2 : ERP non-Odoo (ex: Sage, Cegid, etc.)
            if bank_erp_type != "odoo":
                logger.info(f"[JOB_LOADER] ERP non-Odoo détecté: {bank_erp_type}")
                return {
                    "to_reconcile": [], 
                    "pending": [], 
                    "in_process": [], 
                    "in_process_batches": [],
                    "warning_message": f"ℹ️ Votre ERP bancaire est '{bank_erp_type}'. Pour l'instant, seules les transactions bancaires depuis **Odoo** sont disponibles. D'autres providers (Sage, Cegid, etc.) seront ajoutés prochainement."
                }
            
            # ✅ Cas 3 : Configuration Odoo - Utiliser le singleton ERPService
            # Le singleton gère automatiquement :
            # - Récupération des credentials depuis Firebase
            # - Récupération du mot de passe depuis Secret Manager
            # - Cache de la connexion (TTL 30 minutes)
            # - Thread-safety
            # - Nettoyage automatique des connexions expirées
            
            logger.info(f"[JOB_LOADER] Récupération transactions bancaires via singleton ERPService...")
            
            try:
                from ...erp_service import ERPService
                from ...firebase_providers import FirebaseManagement
                
                # ⭐ Utiliser le singleton ERPService avec cache de connexions
                # Signature: get_odoo_bank_statement_move_line_not_rec(user_id, company_id, client_uuid, journal_id, reconciled)
                bank_transactions = ERPService.get_odoo_bank_statement_move_line_not_rec(
                    user_id=self.user_id,
                    company_id=self.company_id,
                    client_uuid=self.client_uuid,
                    journal_id=None,  # Tous les journaux bancaires
                    reconciled=False  # Non réconciliées uniquement
                )
                
                logger.info(
                    f"[JOB_LOADER] ✅ {len(bank_transactions)} transactions bancaires récupérées "
                    f"via singleton ERP (avec cache)"
                )
                
            except Exception as erp_error:
                # Gestion des erreurs du singleton ERP
                logger.error(f"[JOB_LOADER] ❌ Erreur singleton ERP: {erp_error}", exc_info=True)
                
                # Messages d'erreur spécifiques selon le type
                error_msg = str(erp_error)
                if "Failed to connect to ERP" in error_msg:
                    warning = "⚠️ Impossible de se connecter à Odoo. Vérifiez vos identifiants ERP dans les paramètres."
                elif "client_uuid not found" in error_msg:
                    warning = "⚠️ Configuration utilisateur incomplète. Contactez votre administrateur."
                elif "mandate not found" in error_msg:
                    warning = "⚠️ Données entreprise non trouvées. Vérifiez votre configuration."
                else:
                    warning = f"⚠️ Erreur lors de la récupération des transactions bancaires : {error_msg}"
                
                return {
                    "to_reconcile": [], 
                    "pending": [], 
                    "in_process": [], 
                    "in_process_batches": [],
                    "bank_accounts": [],
                    "selected_bank_account": None,
                    "warning_message": warning
                }
            
            # Récupérer les statuts depuis Firebase (jobs en cours, pending, etc.)
            firebase_service = FirebaseManagement()
            
            # Structurer les transactions par statut
            to_reconcile = []
            pending = []
            in_process = []
            in_process_batches = []
            bank_accounts = {}
            
            for tx in bank_transactions:
                # Lire move_id depuis Odoo (source native)
                move_id = tx.get("move_id")
                journal_id = tx.get("journal_id")
                journal_name = tx.get("journal_name", "")
                currency_code = tx.get("currency_name", "")

                # Créer l'item transaction (utiliser transaction_id pour généricité)
                tx_item = {
                    "transaction_id": str(move_id) if move_id is not None else "",
                    "transaction_name": tx.get("move_name", ""),
                    "journal_id": str(journal_id) if journal_id is not None else "",
                    "journal_name": journal_name,
                    "date": self._normalize_timestamp(tx.get("date", "")),
                    "amount": float(tx.get("amount", 0)),
                    "partner_name": tx.get("partner_name", ""),
                    "partner_id": tx.get("partner_id"),
                    "payment_ref": tx.get("payment_ref", ""),
                    "ref": tx.get("ref", ""),
                    "transaction_type": tx.get("transaction_type", ""),
                    "currency_id": currency_code,
                    "currency_name": currency_code,
                    "amount_residual": float(tx.get("amount_residual", 0)),
                    "is_reconciled": tx.get("is_reconciled", False),
                    "display_name": tx.get("display_name", ""),
                    "state": tx.get("state", "")
                }

                # Vérifier le statut dans Firebase (utiliser move_id comme job_id)
                notification = firebase_service.check_job_status(
                    user_id=self.user_id,
                    job_id=str(move_id)
                )
                
                if notification and notification.get('function_name') == 'Banker':
                    firebase_status = notification.get('status')
                    
                    if firebase_status in ['running', 'in queue', 'stopping']:
                        tx_item["status"] = firebase_status
                        in_process.append(tx_item)
                    elif firebase_status == 'pending':
                        tx_item["status"] = 'pending'
                        pending.append(tx_item)
                    else:
                        if firebase_status in ['error', 'stopped']:
                            tx_item["status"] = firebase_status
                        else:
                            tx_item["status"] = 'to_reconcile'
                        to_reconcile.append(tx_item)
                else:
                    tx_item["status"] = 'to_reconcile'
                    to_reconcile.append(tx_item)
                
                # Regrouper par compte bancaire
                journal_id = tx_item["journal_id"]
                if journal_id:
                    if journal_id not in bank_accounts:
                        bank_accounts[journal_id] = {
                            "journal_id": journal_id,
                            "to_reconcile": 0,
                            "total_amount": 0.0
                        }
                    bank_accounts[journal_id]["to_reconcile"] += 1
                    bank_accounts[journal_id]["total_amount"] += tx_item["amount"]
            
            logger.info(f"[JOB_LOADER] Bank: {len(to_reconcile)} à réconcilier, "
                       f"{len(in_process)} en cours, {len(pending)} pending, "
                       f"{len(bank_accounts)} comptes bancaires")
            
            # Sélectionner le premier compte bancaire par défaut (format Reflex)
            selected_bank_account = None
            bank_accounts_list = list(bank_accounts.values())
            if bank_accounts_list:
                selected_bank_account = bank_accounts_list[0].get("journal_id", "")
            
            return {
                "to_reconcile": to_reconcile,
                "pending": pending,
                "in_process": in_process,
                "in_process_batches": in_process_batches,
                "bank_accounts": [acc["journal_id"] for acc in bank_accounts_list],  # ✅ Liste des noms uniquement (List[str])
                "selected_bank_account": selected_bank_account  # Format Reflex
            }
        
        except Exception as e:
            logger.error(f"[JOB_LOADER] Erreur fetch Bank ERP: {e}", exc_info=True)
            return {
                "to_reconcile": [], 
                "pending": [], 
                "in_process": [], 
                "in_process_batches": [],
                "bank_accounts": [],
                "selected_bank_account": None,
                "warning_message": f"⚠️ Erreur lors de la récupération des transactions bancaires depuis Odoo : {str(e)}. Les transactions bancaires ne sont pas disponibles pour le moment."
            }
    
    def calculate_metrics(self, jobs_data: Dict) -> Dict:
        """
        Calcule les métriques agrégées par département.
        
        Extrait également les warning_message pour affichage dans le prompt système.
        
        Returns:
            {
                "APBOOKEEPER": {"to_do": 5, "in_process": 2, ...},
                "ROUTER": {"to_process": 12, "in_process": 1},
                "BANK": {"total_accounts": 2, "accounts": {...}, "warning_message": "..."},
                "warnings": ["...", "..."]  # Tous les warnings collectés
            }
        """
        metrics = {}
        all_warnings = []
        
        # APBookkeeper
        ap_data = jobs_data.get("APBOOKEEPER", {})
        metrics["APBOOKEEPER"] = {
            "to_do": len(ap_data.get("to_do", [])),
            "in_process": len(ap_data.get("in_process", [])),
            "pending": len(ap_data.get("pending", [])),
            "processed": len(ap_data.get("processed", []))
        }
        if "warning_message" in ap_data:
            metrics["APBOOKEEPER"]["warning_message"] = ap_data["warning_message"]
            all_warnings.append(f"📋 APBookkeeper: {ap_data['warning_message']}")
        
        # Router (format Reflex uniforme)
        router_data = jobs_data.get("ROUTER", {})
        metrics["ROUTER"] = {
            "to_process": len(router_data.get("to_process", [])),  # Utiliser "to_process" (format Reflex)
            "in_process": len(router_data.get("in_process", [])),
            "processed": len(router_data.get("processed", []))
        }
        if "warning_message" in router_data:
            metrics["ROUTER"]["warning_message"] = router_data["warning_message"]
            all_warnings.append(f"🗂️ Router: {router_data['warning_message']}")
        
        # Bank
        bank_data = jobs_data.get("BANK", {})
        bank_accounts = {}
        
        # Regrouper les transactions par compte bancaire
        for tx in bank_data.get("to_reconcile", []):
            journal_id = tx.get("journal_id")
            if journal_id:
                if journal_id not in bank_accounts:
                    bank_accounts[journal_id] = {
                        "to_reconcile": 0,
                        "total_amount": 0.0
                    }
                bank_accounts[journal_id]["to_reconcile"] += 1
                bank_accounts[journal_id]["total_amount"] += float(tx.get("amount", 0))
        
        metrics["BANK"] = {
            "total_accounts": len(bank_accounts),
            "total_to_reconcile": len(bank_data.get("to_reconcile", [])),
            "pending": len(bank_data.get("pending", [])),
            "in_process": len(bank_data.get("in_process", [])),
            "accounts": bank_accounts
        }
        if "warning_message" in bank_data:
            metrics["BANK"]["warning_message"] = bank_data["warning_message"]
            all_warnings.append(f"🏦 Bank: {bank_data['warning_message']}")
        
        # Ajouter tous les warnings collectés
        if all_warnings:
            metrics["warnings"] = all_warnings
        
        return metrics

