


Methode dans reflex pour le cache
class PinnokioCacheManager:
    """Gestionnaire de cache Redis pour les données externes."""

    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self._connection_config = None

    async def _get_redis_client(self) -> redis.Redis:
        """Récupère le client Redis (même configuration que les listeners)."""
        if self.redis_client is None:
            self._connection_config = self._load_redis_config()

            self.redis_client = redis.Redis(
                host=self._connection_config.get("host"),
                port=self._connection_config.get("port", 6379),
                password=self._connection_config.get("password"),
                ssl=self._connection_config.get("tls", False),
                db=self._connection_config.get("db", 0),
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        return self.redis_client

    def _load_redis_config(self) -> Dict:
        """Charge la configuration Redis depuis les variables d'environnement."""
        return {
            "host": os.getenv("LISTENERS_REDIS_HOST", "localhost"),
            "port": int(os.getenv("LISTENERS_REDIS_PORT", "6379")),
            "password": os.getenv("LISTENERS_REDIS_PASSWORD"),
            "tls": os.getenv("LISTENERS_REDIS_TLS", "false").lower() == "true",
            "db": int(os.getenv("LISTENERS_REDIS_DB", "0")),
        }

    def _build_cache_key(self, user_id: str, company_id: str, data_type: str, sub_type: str = None) -> str:
        """Construit une clé de cache standardisée."""
        key = f"cache:{user_id}:{company_id}:{data_type}"
        if sub_type:
            key += f":{sub_type}"
        return key

    async def get_cached_data(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None,
        fallback_fn: Optional[Callable] = None,
        ttl_seconds: int = 3600
        ) -> Optional[Dict]:
        """Récupère des données du cache avec fallback optionnel vers la source."""
        cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        print(f"🔍 [CACHE] Tentative de récupération: {cache_key}")
        
        try:
            redis_client = await self._get_redis_client()
            print(f"🔗 [CACHE] Connexion Redis établie")

            # Tentative de récupération depuis le cache
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                cache_info = data.get("cached_at", "unknown")
                data_size = len(data.get("data", {})) if isinstance(data.get("data"), dict) else "N/A"
                print(f"✅ [CACHE] HIT: {cache_key} | Cached: {cache_info} | Size: {data_size}")
                return data

            # Cache miss - utiliser le fallback si fourni
            print(f"❌ [CACHE] MISS: {cache_key}")
            if fallback_fn and callable(fallback_fn):
                print(f"🔄 [CACHE] Appel du fallback pour: {cache_key}")
                fresh_data = await self._call_fallback_safely(fallback_fn)
                if fresh_data:
                    print(f"💾 [CACHE] Mise en cache des données fraîches: {cache_key}")
                    await self.set_cached_data(user_id, company_id, data_type, sub_type, fresh_data, ttl_seconds)
                    # Retourner les données avec la même structure que le cache
                    return {
                        "data": fresh_data,
                        "cached_at": datetime.now().isoformat(),
                        "source": "fallback"
                    }
                else:
                    print(f"⚠️ [CACHE] Fallback n'a retourné aucune donnée: {cache_key}")

            return None
        except Exception as e:
            print(f"❌ [CACHE] Erreur lors de la récupération: {cache_key} | Error: {e}")
            # En cas d'erreur de cache, appeler le fallback si disponible
            if fallback_fn and callable(fallback_fn):
                print(f"🔄 [CACHE] Tentative de fallback après erreur: {cache_key}")
                fresh_data = await self._call_fallback_safely(fallback_fn)
                if fresh_data:
                    # Retourner les données avec la même structure que le cache
                    return {
                        "data": fresh_data,
                        "cached_at": datetime.now().isoformat(),
                        "source": "fallback_after_error"
                    }
                return fresh_data
            return None

    async def set_cached_data(
        self,
        user_id: str,
        company_id: str,
        data_type: str,
        sub_type: str = None,
        data: Dict = None,
        ttl_seconds: int = 3600
        ) -> bool:
        """Stocke des données dans le cache."""
        cache_key = self._build_cache_key(user_id, company_id, data_type, sub_type)
        print(f"💾 [CACHE] Tentative de stockage: {cache_key} | TTL: {ttl_seconds}s")
        
        try:
            if not data:
                print(f"⚠️ [CACHE] Données vides pour: {cache_key}")
                return False

            redis_client = await self._get_redis_client()

            # Calculer la taille des données
            data_size = len(str(data)) if data else 0
            print(f"📊 [CACHE] Taille des données: {data_size} caractères")

            # Ajouter des métadonnées de cache
            cached_payload = {
                "data": data,
                "cached_at": datetime.now().isoformat(),
                "ttl_seconds": ttl_seconds,
                "source": f"{data_type}.{sub_type}" if sub_type else data_type
            }

            # Stocker avec TTL
            await redis_client.setex(
                cache_key,
                ttl_seconds,
                json.dumps(cached_payload)
            )

            # Mettre à jour les métadonnées de refresh
            await self._update_refresh_metadata(user_id, company_id, data_type, sub_type)

            print(f"✅ [CACHE] Stockage réussi: {cache_key} | TTL: {ttl_seconds}s | Taille: {data_size}")
            return True
        except Exception as e:
            print(f"❌ [CACHE] Erreur de stockage: {cache_key} | Error: {e}")
            return False

    async def invalidate_company_cache(self, user_id: str, company_id: str) -> bool:
        """Invalide tout le cache d'une société pour un utilisateur."""
        pattern = f"cache:{user_id}:{company_id}:*"
        print(f"🗑️ [CACHE] Invalidation demandée: {pattern}")
        
        try:
            redis_client = await self._get_redis_client()

            # Rechercher toutes les clés correspondant au pattern
            keys = await redis_client.keys(pattern)
            print(f"🔍 [CACHE] Clés trouvées pour invalidation: {len(keys)}")
            
            if keys:
                for key in keys:
                    print(f"🗑️ [CACHE] Suppression de la clé: {key}")
                
                await redis_client.delete(*keys)
                print(f"✅ [CACHE] Invalidation réussie: {len(keys)} clés supprimées pour user={user_id}, company={company_id}")
            else:
                print(f"ℹ️ [CACHE] Aucune clé à invalider pour: {pattern}")

            return True
        except Exception as e:
            print(f"❌ [CACHE] Erreur d'invalidation: {pattern} | Error: {e}")
            return False

    async def get_cache_stats(self, user_id: str, company_id: str) -> Dict:
        """Retourne les statistiques du cache pour une société."""
        try:
            redis_client = await self._get_redis_client()
            pattern = f"cache:{user_id}:{company_id}:*"
            keys = await redis_client.keys(pattern)

            stats = {
                "total_keys": len(keys),
                "data_types": {},
                "total_size_bytes": 0,
                "oldest_entry": None,
                "newest_entry": None
            }

            for key in keys:
                try:
                    data = await redis_client.get(key)
                    if data:
                        parsed = json.loads(data)
                        data_type = key.split(":")[-2] if len(key.split(":")) > 3 else "unknown"

                        if data_type not in stats["data_types"]:
                            stats["data_types"][data_type] = 0
                        stats["data_types"][data_type] += 1

                        stats["total_size_bytes"] += len(data)

                        cached_at = parsed.get("cached_at")
                        if cached_at:
                            if not stats["oldest_entry"] or cached_at < stats["oldest_entry"]:
                                stats["oldest_entry"] = cached_at
                            if not stats["newest_entry"] or cached_at > stats["newest_entry"]:
                                stats["newest_entry"] = cached_at
                except Exception:
                    continue

            return stats
        except Exception as e:
            print(f"⚠️ Cache stats error: {e}")
            return {"error": str(e)}

    async def _call_fallback_safely(self, fallback_fn: Callable) -> Any:
        """Appelle la fonction de fallback de manière sécurisée."""
        try:
            if asyncio.iscoroutinefunction(fallback_fn):
                return await fallback_fn()
            else:
                return fallback_fn()
        except Exception as e:
            print(f"⚠️ Fallback function error: {e}")
            return None

    async def _update_refresh_metadata(self, user_id: str, company_id: str, data_type: str, sub_type: str = None):
        """Met à jour les métadonnées de rafraîchissement."""
        try:
            meta_key = self._build_cache_key(user_id, company_id, "meta", "last_refresh")

            redis_client = await self._get_redis_client()
            current_meta = await redis_client.get(meta_key)

            meta_data = json.loads(current_meta) if current_meta else {}
            source = f"{data_type}.{sub_type}" if sub_type else data_type
            meta_data[source] = datetime.now().isoformat()

            await redis_client.setex(meta_key, 86400, json.dumps(meta_data))  # TTL 24h pour les métadonnées
        except Exception as e:
            print(f"⚠️ Metadata update error: {e}")


***************
FORMAT DE CACHING Des transasaction bancaire extraction depuis le cache redis
# Vérifier le cache Redis
                cached_data = await cache_manager.get_cached_data(
                    user_id=self.firebase_user_id,
                    company_id=self.companies_search_id,
                    data_type="bank",
                    sub_type="transactions"
                )

Format des données quand extraite (Redis ou depui la source dans Reflex)
@rx.event(background=True)
    async def load_bank_transactions_from_cache(self, cached_data: dict):
        """Charge les transactions depuis les données du cache."""
        async with self:
            try:
                print("📋 [BANK] Chargement depuis le cache...")
                
                # Charger les transactions to_reconcile
                to_reconcile_data = cached_data.get("to_reconcile", [])
                self.items_to_reconcile = [
                    TransactionItem.from_dict(tx_data) 
                    for tx_data in to_reconcile_data
                ]
                
                # Charger les transactions pending
                pending_data = cached_data.get("pending", [])
                self.pending_items = [
                    TransactionItem.from_dict(tx_data) 
                    for tx_data in pending_data
                ]
                
                # Charger les transactions in_process
                in_process_data = cached_data.get("in_process", [])
                self.in_process_items = [
                    TransactionItem.from_dict(tx_data) 
                    for tx_data in in_process_data
                ]
                
                # Charger les lots en cours (BatchItem)
                in_process_batches_data = cached_data.get("in_process_batches", [])
                self._in_process_batches = [
                    BatchItem(
                        batch_id=batch_data.get("batch_id", ""),
                        bank_account=batch_data.get("bank_account", ""),
                        transaction_count=int(batch_data.get("transaction_count", 0) or 0),
                        status=batch_data.get("status", ""),
                        timestamp=str(batch_data.get("timestamp", "")),
                    )
                    for batch_data in in_process_batches_data
                ]
                
                # Charger les comptes bancaires
                self.available_bank_accounts = cached_data.get("bank_accounts", [])
                self.selected_bank_account = cached_data.get("selected_bank_account", "")
                
                # Calculer le solde total
                self._calculate_total_balance()
                
                print(f"✅ [BANK] Cache chargé: {len(self.items_to_reconcile)} to_reconcile, "
                      f"{len(self.pending_items)} pending, {len(self.in_process_items)} in_process, "
                      f"{len(self._in_process_batches)} batches")
                
            except Exception as e:
                print(f"❌ [BANK] Erreur chargement depuis cache: {e}")
                raise e
***************************************************************************
Si pas de donnée en cache , extraire depuis la source qui l'erp

Prendre depuis les métadonnée qui sont consituté dans le brain , les valeur de 
odoo_url = erp_data.get("odoo_url")
            odoo_db_name = erp_data.get("odoo_db") 
            odoo_username = erp_data.get("odoo_username")
            odoo_company_name = erp_data.get("odoo_company_name")
            
            # Récupérer la clé API depuis le gestionnaire de secrets
            secret_manager_name = erp_data.get("secret_manager")
Les prendre depuis reconstruct_full_company profile

Mettre une condition l'erp est odoo.
Ensuite créer l'instance ERP_PINNOKIO, avec comme parametre l'erp_type, pour l'instant intégrer unqiueemnt odoo, et si odoo
intégrer créer une instance pour l'utilisateur_company avec la connection compartimenter , 
créer la méthode get_bank_statement_move_line_not_rec
si odoo, voici la méthode pour faire appel si odoo
def get_odoo_bank_statement_move_line_not_rec(self, journal_id=None, reconciled=None):
        """
        Récupère les mouvements des relevés bancaires depuis Odoo pour un modèle spécifié, en retournant les détails
        spécifiques de chaque mouvement de manière regroupée. Les filtres sur `journal_id` et `reconciled` sont optionnels.

        Args:
            journal_id (int, optional): L'identifiant du journal à filtrer. Récupère tous les mouvements si None.
            reconciled (bool, optional): Filtrer les mouvements qui sont réconciliés ou non. Récupère tous les mouvements si None.

        Returns:
            list: Une liste de dictionnaires, chaque dictionnaire contenant les détails regroupés d'un mouvement de relevé bancaire.
            pd.DataFrame: Un DataFrame contenant les mêmes données pour une manipulation ultérieure.
        """
        # Définition des critères de recherche de base (uniquement filtré par l'entreprise)
        domain = []
        filters = [['company_id.name', '=', self.company_name]]
        search_criteria = domain + filters

        # Liste des champs spécifiques à récupérer pour chaque mouvement de relevé bancaire
        fields_to_retrieve = [
            'move_id', 'journal_id', 'payment_ids', 'partner_id', 'account_number', 'partner_name',
            'transaction_type', 'payment_ref', 'currency_id', 'amount', 'running_balance',
            'amount_currency', 'amount_residual', 'is_reconciled', 'statement_complete',
            'statement_valid', 'display_name', 'name', 'ref', 'date', 'state', 'move_type',
            'company_id'
        ]

        # Exécution de la requête vers Odoo pour récupérer les informations sans filtres supplémentaires
        bank_statement_moves = self.execute_kw('account.bank.statement.line', 'search_read', [search_criteria], {'fields': fields_to_retrieve})

        # Conversion en DataFrame pour une manipulation facile
        df = pd.DataFrame(bank_statement_moves)

        # Application des filtres optionnels
        if journal_id is not None:
            df = df[df['journal_id'].apply(lambda x: x[0] == journal_id)]  # Filtre sur journal_id

        if reconciled is not None:
            if 'is_reconciled' in df.columns:
                df = df[df['is_reconciled'] == reconciled]  # Filtre sur reconciled
            else:
                # Colonne absente: ignorer le filtre et informer (société sans module bancaire configuré)
                print("ℹ️ [ERP] Colonne 'is_reconciled' absente; filtre 'reconciled' ignoré (banking module non configuré).")

        # Conversion du DataFrame filtré en liste de dictionnaires
        df=self.expand_list_columns(df)
        filtered_data = df.to_dict('records')

        return filtered_data, df

**************
Au final qu'on soit par cache ou par appel a Odoo on obtiens les trnsactions
Ce processus peut prendre un peu de temps donc le mettre asynchrone, une fois les données chargée si provenant de la source, charge le cache redis exactement sous le meme format attendu que si cétait effecuté depuis reflex.

a present nous avons un dictionnaire avec toutes les transaction bancaires non réconcilier par compte en compte, l'objectif il faudrait que cela soit dans un dictionnaire pour que l'agent
soit capable soit de trier sur la base des données (a regareder entre dataframe ou dictionnaire)

Voici la continuité du code coté Reflex apres la récupération afin que tu puisse comprendre les colonnes et champs important du clé du dictionnaire pour aigullier l'agent.

#print(f"🔍 DONNÉES BRUTES ERP - Premier élément journal_id: {transactions_data[0] if transactions_data else 'Aucune donnée'}")    
                # Valider le format: une liste (éventuellement vide) est acceptable
                if not isinstance(transactions_data, list):
                    print(f"❌ Format de résultat inattendu: {type(transactions_data)}")
                    yield rx.toast.error(
                        title="Format error",
                        description=f"Unexpected result type from ERP: {type(transactions_data)}",
                        duration=5000
                    )

                    self._reset_transaction_data()
                    return
                
                '''# Filtrer les transactions non réconciliées
                filtered_transactions = [
                    tx for tx in transactions_data 
                    if isinstance(tx, dict) and tx.get('is_reconciled') == False
                ]'''
                
                

                # 2. Récupérer les IDs des transactions en cours de traitement
                in_process_transaction_ids = await self._get_in_process_transaction_ids()
                print(f"🔄 {len(in_process_transaction_ids)} transactions en cours identifiées")
                
                pending_transaction_ids = await self._get_pending_transaction_ids_optimized()
                print(f"📋 {len(pending_transaction_ids)} transactions pending identifiées")
                # 3. Filtrer les transactions (non réconciliées ET non en cours)
                
                filtered_transactions = []
                excluded_count = 0
                
                for tx in transactions_data:
                    if not isinstance(tx, dict) or tx.get('is_reconciled', False):
                        continue
                        
                    # Récupérer l'ID de la transaction (selon votre structure de données)
                    tx_id = str(tx.get('move_id', '') or tx.get('transaction_id', ''))
                    
                    # Exclure si en cours de traitement
                    if tx_id and tx_id in in_process_transaction_ids:
                        excluded_count += 1
                        print(f"⏭️ Transaction {tx_id} exclue (en cours de traitement)")
                        continue

                    if tx_id and tx_id in pending_transaction_ids:
                        excluded_count += 1
                        print(f"⏭️ Transaction {tx_id} exclue (dans pending)")
                        continue
                        
                    filtered_transactions.append(tx)
                
                print(f"📊 {len(filtered_transactions)} transactions disponibles pour réconciliation")
                print(f"🚫 {excluded_count} transactions exclues (en cours de traitement)")
                    
                
                
                
                
                
                # Extraction des comptes bancaires uniques
                bank_accounts = self._extract_bank_accounts(filtered_transactions)
                
                if not bank_accounts:
                    print("⚠️ Aucun compte bancaire trouvé - utilisation d'un compte par défaut")
                    yield rx.toast.info(
                        title="No bank account detected",
                        description="Using a default account to continue.",
                        duration=3000
                    )
                    bank_accounts = ["Default"]
                
                print(f"🏦 {len(bank_accounts)} comptes bancaires identifiés: {bank_accounts}")
                
                # Mise à jour des comptes bancaires disponibles
                #self.available_bank_accounts = bank_accounts
                
                self._merge_bank_accounts(bank_accounts)

                # Sélectionner automatiquement le premier compte bancaire
                if not self.selected_bank_account or self.selected_bank_account not in bank_accounts:
                    self.selected_bank_account = bank_accounts[0]
                    print(f"🎯 Compte bancaire sélectionné automatiquement: {self.selected_bank_account}")
                    
                    # Réinitialiser les sélections lors du changement de compte
                    self.selected_items = []
                    print("🔄 Sélections réinitialisées")
                
                # Conversion en objets TransactionItem
                self.items_to_reconcile = self._convert_transactions_to_items(filtered_transactions)
                
                # Calculer le solde total pour le compte sélectionné
                self._calculate_total_balance()
                
                # Notification de succès
                yield rx.toast.success(
                    title="Loading complete",
                    description=f"{len(self.items_to_reconcile)} transactions loaded, account '{self.selected_bank_account}' selected",
                    duration=3000
                )
                
                # Mettre en cache si demandé
                if getattr(self, '_should_cache_after_load', False):
                    print(f"💾 [BANK] Mise en cache automatique après fetch_transactions")
                    yield BankTransactionState.cache_bank_data_now
                
            except Exception as e:
                print(f"❌ Erreur lors de l'appel à l'API ERP: {e}")
                import traceback
                print(f"❌ Traceback complet: {traceback.format_exc()}")
                
                # Toast spécifique pour absence de module bancaire
                if "is_reconciled" in str(e):
                    yield rx.toast.info(
                        title="No Banking Module",
                        description="This company has no banking transactions configured or available.",
                        duration=4000
                    )
                else:
                    yield rx.toast.error(
                        title="ERP Connection Error",
                        description=f"Unable to retrieve transactions: {str(e)}",
                        duration=5000
                    )
                self.error = f"Erreur lors de la récupération des transactions: {str(e)}"
                self._reset_transaction_data()
                
        except Exception as e:
            print(f"❌ Erreur globale dans fetch_transactions: {e}")
            import traceback
            print(f"❌ Traceback complet: {traceback.format_exc()}")
            self.error = f"Erreur inattendue: {str(e)}"
            yield rx.toast.error(
                title="Unexpected error",
                description=self.error,
                duration=5000
            )

            self._reset_transaction_data()
        finally:
            self.is_loading = False
            # Libérer le verrou fetch_bank_inflight si il était activé
            if getattr(self, 'fetch_bank_inflight', False):
                print("🔓 [BANK] Désactivation du verrou fetch_bank_inflight (fin fetch_transactions)")
                self.fetch_bank_inflight = False
************************
Pour les documents de Router.
Nous dispons déjà d'une instance Google drive dans notre microservice, 
deux source de donnée google drive avec le parametre input_drive_id et firebase avec mandate_path et une regle de filtrage
@rx.event()
    async def fetch_drive_documents(self):
        """Fetch documents from Google Drive."""
        
        try:
            
            
            drive_service = DriveClientService(user_id=self.firebase_user_id,mode='prod')
            data = await drive_service.list_files_in_doc_to_do(self.input_drive_id)
            #print(f"impress de dara de drive:{data}")
            
            # Cas d'erreur du service
            if isinstance(data, dict) and "erreur" in data:
                print(f"Erreur reçue du service: {data['erreur']}")
                # Détection re-consent si invalid_grant
                try:
                    err_txt = str(data.get("erreur", "")).lower()
                    if "invalid_grant" in err_txt:
                        # Signaler à l'appelant qu'un re-consent est requis
                        raise Exception("OAUTH_REAUTH_REQUIRED: invalid_grant")
                except Exception:
                    pass
                return  # Retourne None en cas d'erreur
            
            # Cas de dossier vide
            elif data == []:
                print("Aucun fichier trouvé dans le dossier")
                return {  # Retourne un dictionnaire avec des listes vides mais valides
                    "to_process": [],
                    "in_process": []
                }
                
                

            # 2. Initialiser une liste de GdriveDocumentItem avec les données Drive
           # Initialiser les listes pour différentes catégories de documents
            drive_documents_to_process = []  # Pour les documents à traiter
            drive_documents_in_process = []  # Pour les documents en cours
            all_drive_documents = []
            for doc in data:
                drive_doc = GdriveDocumentItem(
                    id=doc.get('id', ''),
                    name=doc.get('name', ''),
                    created_time=datetime.strptime(doc.get('createdTime', ''), 
                                                "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%d/%m/%Y %H:%M"),
                    status="to_process",  # Statut par défaut
                    client=self.client_name,
                    router_drive_view_link=doc.get('webViewLink')
                )
                all_drive_documents.append(drive_doc)
        
            # 3. Récupérer les statuts des notifications Firebase pour mettre à jour les statuts
            firebase_service = FireBaseManagement()
            
            # Pour chaque document Drive, vérifier s'il existe une notification correspondante
            for drive_doc in all_drive_documents:
                # Vérifier dans Firebase si ce document a une notification
                notification = firebase_service.check_job_status(
                    user_id=self.firebase_user_id,
                    file_id=drive_doc.id
                )
                
                # Si une notification existe et qu'elle correspond à la fonction Router
                if notification and notification.get('function_name') == 'Router':
                    firebase_status = notification.get('status')
                    
                    # Mettre à jour le statut du document
                    if firebase_status == 'running':
                        drive_doc.status = 'on_process'
                        drive_documents_in_process.append(drive_doc)  # Ajouter aux documents en cours
                    elif firebase_status == 'in queue':
                        drive_doc.status = 'in_queue'
                        drive_documents_in_process.append(drive_doc)  # Ajouter aux documents en cours
                    elif firebase_status == 'stopping':
                        drive_doc.status = 'stopping'
                        drive_documents_in_process.append(drive_doc)  # Ajouter aux documents en cours
                    else:
                        # Tous les autres statuts restent dans la liste principale
                        if firebase_status == 'error':
                            drive_doc.status = 'error'
                        elif firebase_status == 'pending':
                            drive_doc.status = 'pending'
                        elif firebase_status in ['completed', 'success']:
                            drive_doc.status = 'routed'
                        # et on l'ajoute à la liste des documents à traiter
                        drive_documents_to_process.append(drive_doc)
                else:
                    # Aucune notification, document à traiter
                    drive_documents_to_process.append(drive_doc)  # Statut inconnu, on garde dans la liste principale
            
                    
                print(f"Document {drive_doc.name} (ID: {drive_doc.id}): statut mis à jour de 'to_process' à '{drive_doc.status}'")
            
            # 4. Mettre à jour la liste des documents non traités
        
            #self.unprocessed_documents = drive_documents_to_process
            #self.items_in_process = drive_documents_in_process
            
        
            print(f"Chargement réussi: {len(drive_documents_to_process)} documents à traiter et {len(drive_documents_in_process)} documents en cours")
            return {
            "to_process": drive_documents_to_process,
            "in_process": drive_documents_in_process
            }
        except Exception as e:
            err_str = str(e)
            print(f"Erreur lors du chargement des documents Drive ou de la synchronisation des statuts: {err_str}")
            # Propager un signal clair à l'appelant si re-consent requis
            if "invalid_grant" in err_str.lower() or "OAUTH_REAUTH_REQUIRED" in err_str:
                raise Exception("OAUTH_REAUTH_REQUIRED: invalid_grant")
            return None


****************
Meme principe si non redis extreaire les donnée de la source , et ensutie mettre à jour dans Redis


Pour les factures fournisseurs

async def _fetch_ap_from_firebase(self) -> Dict:
        """Récupère les documents APbookeeper depuis Firebase (méthode existante adaptée)."""
        try:
            print("🔄 [AP] Récupération des documents depuis Firebase...")
            
            # Utiliser la logique existante de fetch_documents mais retourner les données structurées
            firebase_c = FireBaseManagement()
            departement = 'APbookeeper'
            
            # Helper function pour créer DocumentItem (réutilisée de fetch_documents)
            def create_document_item(doc):
                def _format_timestamp(value):
                    if not value:
                        return "N/A"
                    try:
                        if hasattr(value, "strftime"):
                            return value.strftime("%d/%m/%Y %H:%M")
                        if isinstance(value, str):
                            s = value.strip()
                            from datetime import datetime, timezone
                            try:
                                if s.endswith("Z"):
                                    return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
                                return datetime.fromisoformat(s).strftime("%d/%m/%Y %H:%M")
                            except Exception:
                                return s
                        if isinstance(value, (int, float)):
                            from datetime import datetime, timezone
                            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%d/%m/%Y %H:%M")
                        seconds = getattr(value, "seconds", None)
                        if isinstance(seconds, (int, float)):
                            from datetime import datetime, timezone
                            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        pass
                    return "N/A"

                ts = _format_timestamp(doc['data'].get('timestamp'))

                return DocumentItem(
                    client=doc['data'].get('client', ''),
                    file_name=doc['data'].get('file_name', ''),
                    status=doc['data'].get('status', 'to_process'),
                    timestamp=ts,
                    source=doc['data'].get('source', ''),
                    uri_drive_link=doc['data'].get('uri_drive_link', ''),
                    job_id=doc['data'].get('job_id', ''),
                    drive_file_id=doc['data'].get('drive_file_id', ""),
                    pinnokio_func=departement
                )
            
            # Récupérer tous les documents
            all_docs = {}
            
            # TO_DO documents
            todo_docs = firebase_c.fetch_journal_entries_by_mandat_id(
                self.firebase_user_id,
                self.base_collection_id,
                source='documents/accounting/invoices/doc_to_do',
                departement=departement
            )
            
            items_to_do = [create_document_item(doc) for doc in todo_docs]
            items_in_process = []
            final_items_to_do = []
            
            # Traiter les statuts avec notifications Firebase
            for item in items_to_do:
                notification = firebase_c.check_job_status(
                    user_id=self.firebase_user_id,
                    job_id=item.job_id
                )
                
                if notification and notification.get('function_name') == 'APbookeeper':
                    firebase_status = notification.get('status')
                    
                    if firebase_status == 'running':
                        item.status = 'on_process'
                        items_in_process.append(item)
                    elif firebase_status == 'in queue':
                        item.status = 'in_queue'
                        items_in_process.append(item)
                    elif firebase_status == 'stopping':
                        item.status = 'stopping'
                        items_in_process.append(item)
                    elif firebase_status == 'pending':
                        # Sera géré dans la section PENDING
                        pass
                    else:
                        if firebase_status in ['error','stopped']:
                            item.status = firebase_status
                        elif firebase_status in ['completed', 'success','close']:
                            item.status = 'completed'
                        final_items_to_do.append(item)
                else:
                    final_items_to_do.append(item)
            
            # PENDING documents
            pending_docs = firebase_c.fetch_pending_journal_entries_by_mandat_id(
                self.firebase_user_id,
                self.base_collection_id,
                source='documents/accounting/invoices/doc_to_do',
                departement=departement
            )
            
            items_pending = []
            for doc in pending_docs:
                doc_item = create_document_item(doc)
                notification = firebase_c.check_job_status(
                    user_id=self.firebase_user_id,
                    job_id=doc_item.job_id
                )
                
                if notification and notification.get('function_name') == 'APbookeeper':
                    firebase_status = notification.get('status')
                    if firebase_status == 'pending':
                        doc_item.status = 'pending'
                        items_pending.append(doc_item)
            
            # PROCESSED documents
            booked_docs = firebase_c.fetch_journal_entries_by_mandat_id(
                self.firebase_user_id,
                self.base_collection_id,
                source='documents/invoices/doc_booked',
                departement=departement
            )
            
            items_booked = [create_document_item(doc) for doc in booked_docs]
            for item in items_booked:
                item.status = 'completed'
            
            # Convertir en dictionnaires pour le cache
            cache_data = {
                "to_do": [item.to_dict() for item in final_items_to_do],
                "in_process": [item.to_dict() for item in items_in_process],
                "pending": [item.to_dict() for item in items_pending],
                "processed": [item.to_dict() for item in items_booked]
            }
            
            print(f"📊 [AP] Données récupérées - to_do: {len(final_items_to_do)}, "
                  f"in_process: {len(items_in_process)}, pending: {len(items_pending)}, "
                  f"processed: {len(items_booked)}")
            
            return cache_data
            
        except Exception as e:
            print(f"❌ [AP] Erreur récupération Firebase: {e}")
            raise e



********************************
Ceci va permettre à deéfinir les valeurs a initailiser pour le SPT des job, et de ceci fournir les méttric de base a intégrer au system de prompt de cette agent. 
Dans le context de l'agent il va peut donner des indications precises sur la nature de ces documents, les informations comme le file_id qui peut transmettre ect…


Acces aux notification, autre outil , client/{uid}/notifications, faire un Stream sur la collection , 
avec acces aux champs status, file_name, job_id, function 
ou appel à une notifiaction précise en ajouter client/{uid}/notifications/job_id


Ceci sont les informations pour permettre à l'agent, JobManager de pouvoir fournir les informaitons nécaissaire sur les jobs a traiter et 