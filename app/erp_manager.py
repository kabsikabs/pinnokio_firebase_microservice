import xmlrpc.client
import pandas as pd
from .tools.g_cred import get_secret


class OdooModelManager:
    """
    Gestionnaire centralisé pour l'adaptation des modèles Odoo selon les versions
    """
    
    def __init__(self, odoo_version):
        version_parts = odoo_version.split('.')
        if len(version_parts) >= 2:
            self.version_num = float(f"{version_parts[0]}.{version_parts[1]}")
        else:
            self.version_num = float(version_parts[0])
        self.odoo_version = odoo_version
        
    # ========================================================================================
    # 1. DÉFINITION DES CHAMPS PAR MODÈLE ET VERSION
    # ========================================================================================
    
    @property
    def MODEL_FIELDS_MAPPING(self):
        """
        Mapping des champs par modèle selon les versions d'Odoo
        """
        return {
            'account.account': {
                'base_fields': ['code', 'display_name', 'account_type', 'reconcile'],
                'version_fields': {
                    '<18': ['company_id', 'deprecated'],
                    '>=18&<18.3': ['company_ids', 'deprecated'],  # Odoo 18.0 à 18.2 utilise encore 'deprecated'
                    '>=18.3': ['company_ids', 'active']  # Odoo 18.3+ utilise 'active' au lieu de 'deprecated'
                }
            },
            
            
            'account.journal': {
                'base_fields': ['code', 'name', 'type', 'default_account_id'],
                'version_fields': {
                    '<18': ['company_id'],
                    '>=18': ['company_id']  # Journal reste en company_id en 18
                }
            },
            
            'account.move.line': {
                'base_fields': ['date', 'account_type', 'currency_id', 'parent_state', 
                              'amount_currency', 'name', 'debit', 'credit', 'balance', 
                              'account_id', 'journal_id', 'move_id', 'write_date'],
                'version_fields': {
                    '<18': ['company_id'],
                    '>=18': ['company_ids']
                }
            },
            
            'account.move': {
                'base_fields': ['id', 'name', 'journal_id', 'partner_id', 'invoice_date', 
                              'date', 'payment_reference', 'invoice_date_due', 'ref', 
                              'amount_residual', 'payment_state', 'payment_id', 
                              'transaction_ids', 'amount_residual_signed', 'amount_paid', 
                              'currency_id'],
                'version_fields': {
                    '<18': ['company_id'],
                    '>=18': ['company_ids']
                }
            },
            
            'account.bank.statement.line': {
                'base_fields': ['move_id', 'journal_id', 'payment_ids', 'partner_id', 
                              'account_number', 'partner_name', 'transaction_type', 
                              'payment_ref', 'currency_id', 'amount', 'running_balance',
                              'amount_currency', 'amount_residual', 'is_reconciled', 
                              'statement_complete', 'statement_valid', 'display_name', 
                              'name', 'ref', 'date', 'state', 'move_type'],
                'version_fields': {
                    '<18': ['company_id'],
                    '>=18': ['company_ids']
                }
            },
            
            'res.company': {
                'base_fields': ['name', 'id', 'partner_id'],
                'version_fields': {
                    '<18': [],
                    '>=18': []
                }
            }
        }
    
    # ========================================================================================
    # 2. GÉNÉRATION DES LISTES DE CHAMPS SELON VERSION
    # ========================================================================================
    
    def get_fields_for_model(self, model_name):
        """
        Retourne la liste des champs appropriés pour un modèle selon la version Odoo
        """
        if model_name not in self.MODEL_FIELDS_MAPPING:
            raise ValueError(f"Modèle {model_name} non supporté")
        
        mapping = self.MODEL_FIELDS_MAPPING[model_name]
        fields = mapping['base_fields'].copy()
        version_fields = mapping['version_fields']
        # Ajouter les champs spécifiques à la version
        for version_key, version_specific_fields in version_fields.items():
            if self._matches_version_condition(version_key):
                fields.extend(version_specific_fields)
                break
        
        return fields
    
    def _matches_version_condition(self, condition):
        """
        Vérifie si la version actuelle correspond à une condition de version
        
        Args:
            condition (str): Condition de version (ex: '<18', '>=18&<18.3', '>=18.3')
            
        Returns:
            bool: True si la condition est remplie
        """
        # Gestion des conditions multiples avec &
        if '&' in condition:
            conditions = condition.split('&')
            return all(self._evaluate_single_condition(cond.strip()) for cond in conditions)
        else:
            return self._evaluate_single_condition(condition)
    
    def _evaluate_single_condition(self, condition):
        """
        Évalue une condition de version simple
        
        Args:
            condition (str): Condition simple (ex: '<18', '>=18.3')
            
        Returns:
            bool: True si la condition est remplie
        """
        if condition.startswith('>='):
            threshold = float(condition[2:])
            return self.version_num >= threshold
        elif condition.startswith('<='):
            threshold = float(condition[2:])
            return self.version_num <= threshold
        elif condition.startswith('>'):
            threshold = float(condition[1:])
            return self.version_num > threshold
        elif condition.startswith('<'):
            threshold = float(condition[1:])
            return self.version_num < threshold
        elif condition.startswith('=='):
            threshold = float(condition[2:])
            return self.version_num == threshold
        else:
            return False
    

    def get_domain_for_model(self, model_name, company_id):
        """
        Retourne le domaine de recherche approprié selon la version
        """
        # Modèles qui ont des champs company
        company_models = ['account.account', 'account.journal', 'account.move.line', 
                         'account.move', 'account.bank.statement.line']
        
        if model_name not in company_models:
            return []
        
        # Spécificité: account.journal reste sur company_id même en 18
        if model_name == 'account.journal':
            return [['company_id', '=', company_id]]
        
        if self.version_num < 18:
            return [['company_id', '=', company_id]]
        else:
            return [['company_ids', 'in', [company_id]]]
    
    # ========================================================================================
    # 3. MÉTHODES DE TRANSFORMATION OUTPUT (ODOO → APP)
    # ========================================================================================
    
    def adapt_record_to_app(self, record, model_name):
        """
        Transforme un enregistrement Odoo vers le format attendu par l'application
        
        Args:
            record (dict): Enregistrement brut d'Odoo
            model_name (str): Nom du modèle
            
        Returns:
            dict: Enregistrement adapté pour l'application
        """
        adapted_record = record.copy()
        
        # === Transformation 1: deprecated/active ===
        if model_name == 'account.account' and self.version_num >= 18.3:
            # Odoo 18.3+ utilise 'active' au lieu de 'deprecated'
            if 'active' in adapted_record and 'deprecated' not in adapted_record:
                # On simule 'deprecated' pour l'app: deprecated = not active
                adapted_record['deprecated'] = not adapted_record.get('active', True)
        
        # === Transformation 2: company_ids/company_id ===
        company_models = ['account.account', 'account.journal', 'account.move.line', 
                         'account.move', 'account.bank.statement.line']
        
        if model_name in company_models and self.version_num >= 18:
            if 'company_ids' in adapted_record and 'company_id' not in adapted_record:
                # Odoo 18+ utilise 'company_ids', on simule 'company_id' pour l'app
                company_ids = adapted_record.get('company_ids', [])
                adapted_record['company_id'] = company_ids[0] if company_ids else None
        
        return adapted_record
    
    def adapt_records_list_to_app(self, records_list, model_name):
        """
        Transforme une liste d'enregistrements pour l'application
        
        Args:
            records_list (list): Liste d'enregistrements Odoo
            model_name (str): Nom du modèle
            
        Returns:
            list: Liste d'enregistrements adaptés
        """
        return [self.adapt_record_to_app(record, model_name) for record in records_list]
    
    # ========================================================================================
    # 4. MÉTHODES DE TRANSFORMATION INPUT (APP → ODOO)
    # ========================================================================================
    
    def adapt_data_for_odoo(self, app_data, model_name):
        """
        Transforme les données de l'application vers le format Odoo
        
        Args:
            app_data (dict): Données au format application
            model_name (str): Nom du modèle
            
        Returns:
            dict: Données adaptées pour Odoo
        """
        odoo_data = app_data.copy()
        
        # === Transformation 1: deprecated/active ===
        if model_name == 'account.account' and self.version_num >= 18.3:
            # Odoo 18.3+ utilise 'active' au lieu de 'deprecated'
            if 'deprecated' in odoo_data:
                # Convertir 'deprecated' vers 'active' pour Odoo 18.3+
                odoo_data['active'] = not odoo_data.pop('deprecated')
        
        # === Transformation 2: company_id/company_ids ===
        company_models = ['account.account', 'account.journal', 'account.move.line', 
                         'account.move', 'account.bank.statement.line']
        
        if model_name in company_models and self.version_num >= 18:
            if 'company_id' in odoo_data:
                # Convertir 'company_id' vers 'company_ids' pour Odoo 18+
                company_id = odoo_data.pop('company_id')
                if company_id:
                    odoo_data['company_ids'] = [(6, 0, [company_id])]  # Format Odoo Many2many
        
        return odoo_data
    
    # ========================================================================================
    # 5. MÉTHODES D'INTÉGRATION AVEC LA CLASSE PRINCIPALE
    # ========================================================================================
    
    def execute_search_read(self, erp_instance, model_name, domain=None, company_id=None):
        """
        Exécute search_read avec adaptation automatique
        """
        # Construire le domaine complet
        full_domain = domain or []
        if company_id:
            company_domain = self.get_domain_for_model(model_name, company_id)
            full_domain.extend(company_domain)
        
        # Récupérer les champs appropriés
        fields = self.get_fields_for_model(model_name)
        
        # Exécuter la requête
        raw_records = erp_instance.execute_kw(
            model_name, 'search_read', 
            [full_domain], 
            {'fields': fields}
        )
        
        # Adapter les enregistrements pour l'application
        adapted_records = self.adapt_records_list_to_app(raw_records, model_name)
        
        return adapted_records
    
    def execute_write(self, erp_instance, model_name, record_ids, values):
        """
        Exécute write avec adaptation automatique
        
        Args:
            erp_instance: Instance de connexion ERP
            model_name (str): Nom du modèle
            record_ids (list): IDs des enregistrements à modifier
            values (dict): Valeurs au format application
            
        Returns:
            bool: Succès de l'opération
        """
        # Adapter les données pour Odoo
        odoo_values = self.adapt_data_for_odoo(values, model_name)
        
        # Exécuter la mise à jour
        return erp_instance._execute_kw(
            model_name, 'write',
            [record_ids, odoo_values]
        )
    
    def execute_create(self, erp_instance, model_name, values_list):
        """
        Exécute create avec adaptation automatique
        
        Args:
            erp_instance: Instance de connexion ERP
            model_name (str): Nom du modèle
            values_list (list): Liste de dictionnaires de valeurs au format application
            
        Returns:
            list: IDs des enregistrements créés
        """
        # Adapter chaque set de valeurs pour Odoo
        odoo_values_list = [self.adapt_data_for_odoo(values, model_name) for values in values_list]
        
        # Exécuter la création
        return erp_instance._execute_kw(
            model_name, 'create',
            [odoo_values_list]
        )




class ODOO_KLK_VISION:
    def __init__(self, url, db, username, password,odoo_company_name):
        self.url = url
        self.db = db
        self.username = username
        self.password = password
        self.company_name=odoo_company_name
        self.uid = self.authenticate()
        
        self.odoo_version=self.get_odoo_version()
        self.company_name=odoo_company_name
        self.company_id=self.get_company_id()
        self.model_manager = OdooModelManager(self.odoo_version)

    def authenticate(self):
        common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(self.url))
        return common.authenticate(self.db, self.username, self.password, {})


    def execute_kw(self, model, method, args, kwargs={}):
        
        models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(self.url))
        
        return models.execute_kw(self.db, self.uid, self.password, model, method, args, kwargs)

    def test_connection(self):
        """
        Teste la connexion à Odoo et retourne un résultat avec un message approprié.
        À utiliser dans Reflex pour afficher des toasts.
        
        Returns:
            dict: Dictionnaire contenant le statut et le message
                - success (bool): True si la connexion est réussie, False sinon
                - message (str): Message de succès ou d'erreur
        """
        try:
            # 1) Validation des champs requis
            missing_fields = []
            if not self.url:
                missing_fields.append("URL")
            if not self.db:
                missing_fields.append("Base de données")
            if not self.username:
                missing_fields.append("Nom d'utilisateur / Email")
            if not self.password:
                missing_fields.append("Clé API / Mot de passe")
            if not self.company_name:
                missing_fields.append("Nom de la société")

            if missing_fields:
                return {
                    "success": False,
                    "message": "Champs requis manquants: " + ", ".join(missing_fields)
                }

            # 2) Authentification
            if not self.uid:
                return {
                    "success": False,
                    "message": "Échec de l'authentification. Vérifiez l'URL, la base de données, l'utilisateur et la clé API."
                }

            # 3) Récupérer la version du serveur (information)
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(self.url))
            version_info = common.version()
            server_version = version_info.get('server_version', '')

            # 4) Vérifier l'existence de la société demandée
            companies = self.execute_kw(
                'res.company', 'search_read',
                [[['name', '=', self.company_name]]],
                {'fields': ['id', 'name']}
            )

            if not companies:
                # Proposer des suggestions avec ilike
                try:
                    suggestions = self.execute_kw(
                        'res.company', 'search_read',
                        [[['name', 'ilike', self.company_name]]],
                        {'fields': ['name'], 'limit': 5}
                    )
                    suggestion_names = ", ".join([s['name'] for s in suggestions]) if suggestions else "aucune"
                except Exception:
                    suggestion_names = "aucune"

                return {
                    "success": False,
                    "message": (
                        f"Société introuvable: '{self.company_name}'. "
                        f"Vérifiez l'orthographe ou vos droits d'accès. Suggestions: {suggestion_names}."
                    )
                }

            target_company_id = companies[0]['id']

            # 5) Vérifier l'accès de l'utilisateur à la société (entreprises autorisées)
            try:
                user_info = self.execute_kw(
                    'res.users', 'read',
                    [[self.uid], ['company_id', 'company_ids', 'allowed_company_ids']]
                )
                if user_info:
                    info = user_info[0]
                    allowed_ids = set()

                    # allowed_company_ids (Odoo récents)
                    if isinstance(info.get('allowed_company_ids'), list):
                        allowed_ids.update(info['allowed_company_ids'])

                    # company_ids (multi-company plus anciens)
                    if isinstance(info.get('company_ids'), list):
                        allowed_ids.update(info['company_ids'])

                    # company_id (société par défaut)
                    if info.get('company_id'):
                        try:
                            default_company_id = info['company_id'][0] if isinstance(info['company_id'], (list, tuple)) else int(info['company_id'])
                            allowed_ids.add(default_company_id)
                        except Exception:
                            pass

                    if allowed_ids and target_company_id not in allowed_ids:
                        try:
                            allowed_names = self.execute_kw('res.company', 'read', [list(allowed_ids), ['name']])
                            allowed_names_str = ", ".join([c['name'] for c in allowed_names])
                        except Exception:
                            allowed_names_str = ""
                        return {
                            "success": False,
                            "message": (
                                f"Accès refusé à la société '{self.company_name}'. "
                                f"Sociétés autorisées pour cet utilisateur: {allowed_names_str or list(allowed_ids)}."
                            )
                        }
            except Exception:
                # Si la lecture de res.users échoue, on n'empêche pas le test; on continue.
                pass

            # 6) Vérifier un accès modèle scoping par société (ex: account.journal)
            #    Utilise le bon champ selon la version (company_id vs company_ids)
            try:
                # 'account.journal' utilise 'company_id' même en 18
                domain = [['company_id', '=', target_company_id]]
                # Appel inoffensif pour tester les permissions sur un modèle de base
                self.execute_kw('account.journal', 'search_count', [domain])
            except xmlrpc.client.Fault as fault:
                return {
                    "success": False,
                    "message": (
                        "Connexion authentifiée mais permissions insuffisantes sur 'account.journal' "
                        f"pour la société '{self.company_name}': {fault.faultString}"
                    )
                }

            # 7) Succès
            return {
                "success": True,
                "message": f"Connexion réussie à Odoo {server_version} pour la société {self.company_name}"
            }

        except xmlrpc.client.Fault as fault:
            return {
                "success": False,
                "message": f"Erreur Odoo: {fault.faultString}"
            }
        except ConnectionRefusedError:
            return {
                "success": False,
                "message": "Connexion refusée. Vérifiez l'URL et que le serveur est accessible."
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Erreur lors de la connexion: {str(e)}"
            }

    def get_odoo_version(self):
        # Récupération de la version d'Odoo
        version_info = self.execute_kw('ir.module.module', 'search_read', 
                                    [[['name', '=', 'base']]],
                                    {'fields': ['latest_version']})
        if version_info and version_info[0].get('latest_version'):
            version_string = version_info[0]['latest_version']
            print(f"Version d'Odoo : {version_string}")
            
            # Extraction de la version sans le préfixe saas~
            if version_string.startswith('saas~'):
                # Garder la partie après "saas~"
                clean_version = version_string.split('~')[1]
            else:
                # Garder la version telle quelle si pas de préfixe
                clean_version = version_string
                
            return clean_version
        else:
            print("Impossible de récupérer la version d'Odoo")
            return None

    def get_company_id(self):
        co_info = self.get_odoo_company_names(company_name=self.company_name)
        print(f"Tentative de recupération des informatinos sur la société:{co_info}")
        
        if 'error' in co_info:
            print(f"❌ Erreur : Impossible de récupérer les informations de la société '{self.company_name}'.")
            return {}

        company_id = co_info['id']
        return company_id

   
    def get_accounts_with_active_tax_codes(self):
        """Liste tous les comptes configurés sur des codes TVA actifs"""
        tax_lines = self.execute_kw(
            'account.tax.repartition.line', 
            'search_read', 
            [[
                ('tax_id.active', '=', True),
                ('company_id', '=', self.company_id)
            ]], 
            {'fields': ['account_id', 'tax_id','company_id']}
        )

        if not tax_lines:
            return {'error': f"Aucune ligne de répartition trouvée pour des taxes actives dans la société {self.company_name}."}
        
        account_ids = list(set(line['account_id'][0] for line in tax_lines if line['account_id']))

        # Utiliser le ModelManager pour la récupération
        accounts = self.model_manager.execute_search_read(
            erp_instance=self,
            model_name='account.account',
            domain=[('id', 'in', account_ids)],
            company_id=self.company_id
        )

        return accounts if accounts else {'error': f"Aucun compte trouvé pour les lignes de répartition dans la société {self.company_name}."}

    def fetch_account_journal(self):
        """Récupère les journaux avec adaptation automatique"""
        company_id = self.execute_kw('res.company', 'search', [[['name', '=', self.company_name]]])
        
        if not company_id:
            raise ValueError(f"❌ No company found with name: {self.company_name}")

        # Utiliser le ModelManager
        records = self.model_manager.execute_search_read(
            erp_instance=self,
            model_name='account.journal',
            company_id=company_id[0]
        )
        
        return records


    
    def fetch_chart_of_account(self, company_id=None):
        """
        Version adaptée de fetch_chart_of_account utilisant le ModelManager
        """
        target_company_id = company_id if company_id else self.company_id
        print(f"[DEBUG] Target company_id: {target_company_id}")
        
        # Vérifier que l'ID de la société existe
        company_exists = self.execute_kw('res.company', 'search_count', [[['id', '=', target_company_id]]])
        if not company_exists:
            raise ValueError(f"❌ Aucune société trouvée avec l'ID : {target_company_id}")
        
        # Utiliser le ModelManager
        records = self.model_manager.execute_search_read(
            erp_instance=self,
            model_name='account.account',
            company_id=target_company_id
        )
        
        return records
    
    
    def create_accounts(self, accounts_data):
        """
        Crée un ou plusieurs comptes comptables avec adaptation automatique
        """
        try:
            # Récupérer l'ID de l'entreprise
            company_data = self.execute_kw(
                'res.company',
                'search_read',
                [[['name', '=', self.company_name]]],
                {'fields': ['id']}
            )
            if not company_data:
                return {'success': False, 'message': f"Company '{self.company_name}' not found"}

            company_id = company_data[0]['id']
            created_account_ids = []

            # Traiter chaque compte
            for account_data in accounts_data:
                # Ajouter company_id au format application
                account_data['company_id'] = company_id
                
                # Utiliser le ModelManager pour adapter et créer
                account_id = self.model_manager.execute_create(
                    erp_instance=self,
                    model_name='account.account',
                    values_list=account_data
                )
                created_account_ids.append(account_id)

            return {'success': True, 'account_ids': created_account_ids}

        except Exception as e:
            print(f"Error while creating accounts: {e}")
            return {'success': False, 'message': 'Failed to create accounts'}


    
    def deprecate_accounts(self, account_ids):
        """
        Déprécie (désactive) un ou plusieurs comptes avec adaptation automatique
        """
        try:
            if not account_ids:
                return {'success': False, 'message': 'No account IDs provided'}

            # Utiliser le format application (deprecated: True)
            update_data = {'deprecated': True}
            
            # Le ModelManager va adapter automatiquement vers 'active': False pour Odoo 18+
            result = self.model_manager.execute_write(
                erp_instance=self,
                model_name='account.account',
                record_ids=account_ids,
                values=update_data
            )

            if result:
                return {'success': True, 'message': f"{len(account_ids)} accounts deprecated successfully"}
            else:
                return {'success': False, 'message': 'Failed to deprecate accounts'}

        except Exception as e:
            print(f"Error while deprecating accounts: {e}")
            return {'success': False, 'message': 'Failed to deprecate accounts'}


    def get_account_chart(self, account_types=None, company_id=None):
        """
        Récupère les données du plan comptable avec adaptation automatique selon la version
        """
        target_company_id = company_id or self.company_id
        
        # Utiliser le ModelManager pour la récupération adaptée
        records = self.model_manager.execute_search_read(
            erp_instance=self,
            model_name='account.account',
            company_id=target_company_id
        )
        
        # Créer le DataFrame (données déjà adaptées)
        df = pd.DataFrame(records)
        
        # Ajouter display_name si manquant
        if 'display_name' not in df.columns and 'name' in df.columns:
            df['display_name'] = df['name']
        
        # Filtrer par types si nécessaire
        if account_types is not None:
            if isinstance(account_types, str):
                account_types = [account_types]
            df = df[df['account_type'].isin(account_types)]
        
        return df


    
    def get_company_information(self, company_name=None):
        """
        Récupère les informations détaillées d'une société depuis la base de données Odoo.

        Args:
            company_name (str, optional): Le nom de la société à récupérer. Par défaut, None.

        Returns:
            dict: Dictionnaire contenant les informations de la société ou un message d'erreur.
        """
        # Définir les critères de recherche
        domain = [('name', '=', company_name)] if company_name else []
        
        # Liste des champs à récupérer
        fields_to_retrieve = [
            'name', 'id', 'partner_id', 'street', 'vat', 
            'company_registry', 'currency_id', 'phone', 
            'mobile', 'email', 'website'
        ]

        # Utiliser la méthode search_read pour obtenir les enregistrements
        companies = self.execute_kw('res.company', 'search_read', [domain], {'fields': fields_to_retrieve})
        if companies:
            # Convertir en DataFrame pour une manipulation facile
            df_companies = pd.DataFrame(companies)
            
            if not df_companies.empty:
                # Filtrer par le nom si spécifié
                filtered_df = df_companies[df_companies['name'] == company_name] if company_name else df_companies
                return filtered_df.to_dict('records')[0]
            else:
                return {'error': 'Aucune société trouvée'}
        else:
            return {'error': 'Échec de la récupération des informations sur les sociétés'}



    def get_oldest_date(self):
        """
        Récupère la date la plus ancienne dans le modèle `account.move.line`, filtrée par société.

        Returns:
            str: La date la plus ancienne au format 'YYYY-MM-DD', ou None si aucun enregistrement n'existe.
        """
        move_line_model = 'account.move.line'
        
        # Récupérer l'ID de la société en fonction du nom
        company_info = self.get_company_information(self.company_name)
        company_id = company_info.get('id') if company_info else None

        if not company_id:
            print(f"Société {self.company_name} introuvable.")
            return None

        try:
            # Recherche avec un tri pour obtenir la date la plus ancienne pour la société
            oldest_record = self.execute_kw(move_line_model, 'search_read', 
                                            [[('company_id', '=', company_id)]],  # Filtre par company_id
                                            {'fields': ['date'], 'limit': 1, 'order': 'date asc'})
            
            if oldest_record:
                return oldest_record[0]['date']  # Retourne la date du premier enregistrement
            else:
                print(f"Aucune date trouvée pour la société {self.company_name}.")
                return None
        except Exception as e:
            print(f"Erreur lors de la récupération de la date la plus ancienne : {str(e)}")
            return None

    def fetch_financial_records(self, domain=[], **kwargs):
        """
        Récupère les enregistrements financiers en fonction des critères fournis.

        Args:
            domain (list): Liste de tuples définissant les critères de recherche.
                        Chaque tuple contient trois éléments :
                        - Le nom du champ sur lequel filtrer.
                        - L'opérateur (par exemple, '=', '!=', '>', '<', '>=', '<=').
                        - La valeur de comparaison.
                        Exemples de domain:
                            - [('date', '>=', '2023-01-01')]: Toutes les lignes après le 1er janvier 2023.
                            - [('account_id', '=', id_du_compte)]: Toutes les lignes pour un compte spécifique.
                            - [('journal_id', '!=', id_du_journal)]: Exclure les lignes d'un journal spécifique.

        Returns:
            pd.DataFrame: DataFrame contenant les enregistrements correspondant aux critères.
        """
        version_num = float(self.odoo_version.split('.')[0])
        move_line_model = 'account.move.line'
        account_model = 'account.account'
        
        move_line_fields = ['date', 'account_type', 'currency_id', 'parent_state', 'amount_currency', 
                            'name', 'debit', 'credit', 'balance', 'account_id', 'journal_id', 'move_id', 'company_id','write_date']
        # Définition des champs account en fonction de la version
        if version_num < 18:
            account_fields = ['code', 'name', 'account_type', 'reconcile', 'company_id']
        else:
            account_fields = ['code', 'name', 'account_type', 'reconcile', 'company_ids']

        # Tentative de récupération des données de account.move.line
        move_line_records = self.execute_kw(move_line_model, 'search_read', [domain], {'fields': move_line_fields})
        df = pd.DataFrame(move_line_records)
        print(f"impression du dataframe account.move.line:{df}")
        
        if not df.empty:
            filtered_df = df[df['company_id'].apply(lambda x: x[1] == self.company_name if isinstance(x, (list, tuple)) else False)]
            df = filtered_df
            print(f"impressino de df dans fetch_financial_records:{df}")
            
            if 'move_id' in df.columns and not df.empty:
                df['move_id'] = df['move_id'].apply(lambda x: x[0] if x else None)
                
                # Appliquer les filtres supplémentaires basés sur kwargs, si nécessaire
                for column, value in kwargs.items():
                    if column in df.columns:
                        df = df[df[column] == value]
                
                return df, None  # Retourne le DataFrame de account.move.line et None pour raw_coa
        
        # Si le DataFrame est vide ou si la colonne move_id n'existe pas
        print("Le DataFrame account.move.line est vide ou la colonne move_id est manquante, récupération des données de account.account.")
        
        # On retire le filtre de date du domaine pour account.account
        account_domain = [item for item in domain if item[0] != 'date']
        # Modification du domaine en fonction de la version
        if version_num < 18:
            account_records = self.execute_kw(account_model, 'search_read', [account_domain], {'fields': account_fields})
        else:
            # Modifier le domaine pour utiliser company_ids au lieu de company_id si nécessaire
            account_domain = [['company_ids', 'in', [self.company_id]]]
            account_records = self.execute_kw(account_model, 'search_read', [account_domain], {'fields': account_fields})
        
        if account_records:
            df = pd.DataFrame(account_records)
            print(f"impression du dataframe account.account:{df}")
            
            # Filtrage en fonction de la version
            if version_num < 18:
                filtered_df = df[df['company_id'].apply(lambda x: x[1] == self.company_name if isinstance(x, (list, tuple)) else False)]
            else:
                filtered_df = df[df['company_ids'].apply(lambda x: self.company_id in x)]
                
            df = filtered_df
            return None, df  # Retourne None pour le DataFrame principal et le DataFrame de account.account comme raw_coa
        else:
            print("Erreur lors de l'extraction du plan comptable.")
            return None, None


    

    def get_pl_metrics(self, start_date=None, end_date=None):
        """Calcule les métriques P&L en utilisant fetch_financial_records.
        
        Args:
            start_date (str, optional): Date de début au format 'YYYY-MM-DD'
            end_date (str, optional): Date de fin au format 'YYYY-MM-DD'
            
        Returns:
            dict: Dictionnaire contenant les métriques P&L
        """
        # Définir les types de comptes à analyser
        pl_account_types = [
            'income', 'income_other',
            'expense', 'expense_depreciation', 'expense_direct_cost'
        ]
        
        # Construire le domaine de recherche
        domain = [('account_type', 'in', pl_account_types)]
        if start_date:
            domain.append(('date', '>=', start_date))
        if end_date:
            domain.append(('date', '<=', end_date))
            
        # Récupérer les données
        df, _ = self.fetch_financial_records(domain=domain)
        
        if df is None or df.empty:
            return {
                "total_income": 0,
                "total_expenses": 0,
                "net_profit": 0,
                "breakdown": {
                    "income": 0,
                    "other_income": 0,
                    "expenses": 0,
                    "depreciation": 0,
                    "cost_of_revenue": 0
                }
            }

        # Ajouter la colonne klk_balance
        df['klk_balance'] = df['debit'] - df['credit']
        
        # Grouper par type de compte et calculer les soldes
        metrics = {}
        
        # Calculer les revenus (le signe est inversé car crédit est positif pour les revenus)
        income_types = ['income', 'income_other']
        income_df = df[df['account_type'].isin(income_types)]
        total_income = -income_df['klk_balance'].sum()
        
        # Calculer les dépenses
        expense_types = ['expense', 'expense_depreciation', 'expense_direct_cost']
        expense_df = df[df['account_type'].isin(expense_types)]
        total_expenses = expense_df['klk_balance'].sum()
        
        # Breakdown détaillé par type
        breakdown = {
            'income': -df[df['account_type'] == 'income']['klk_balance'].sum(),
            'other_income': -df[df['account_type'] == 'income_other']['klk_balance'].sum(),
            'expenses': df[df['account_type'] == 'expense']['klk_balance'].sum(),
            'depreciation': df[df['account_type'] == 'expense_depreciation']['klk_balance'].sum(),
            'cost_of_revenue': df[df['account_type'] == 'expense_direct_cost']['klk_balance'].sum()
        }
        
        return {
            "total_income": float(total_income),
            "total_expenses": float(total_expenses),
            "net_profit": float(total_income - total_expenses),
            "breakdown": breakdown
        }
    
    def aged_suppliers(self, **kwargs):
        """
        Analyse les factures fournisseurs par tranches d'ancienneté par rapport à la date d'échéance.
        
        Arguments :
        - supplier_name (str, optionnel) : Nom du fournisseur pour filtrer les factures
        - states (list, optionnel) : Liste des états des factures pour filtrer
        
        Retourne :
        - DataFrame contenant l'analyse d'ancienneté des factures
        - Dictionnaire contenant les mêmes informations que le DataFrame
        """
        supplier_name = kwargs.get('supplier_name')
        states = kwargs.get('states', ['posted'])  # Par défaut, on ne prend que les factures validées
        
        # Initialiser les conditions de filtre
        filter_condition = [['move_type', '=', 'in_invoice']]
        
        # Vérification et ajout du filtre fournisseur
        if supplier_name:
            df_suppliers = self.search_supplier(supplier_name)
            
            if isinstance(df_suppliers, str) and df_suppliers == 'empty dataframe':
                return False, f"Le fournisseur avec le nom '{supplier_name}' n'existe pas."
            
            elif df_suppliers.shape[0] == 1:
                supplier_name = df_suppliers.iloc[0]['name']
                filter_condition.append(['invoice_partner_display_name', '=', supplier_name])
            
            elif df_suppliers.shape[0] > 1:
                found_names = df_suppliers['name'].tolist()
                return False, f"Plusieurs fournisseurs trouvés pour '{supplier_name}': {', '.join(found_names)}. Veuillez préciser le nom exact."
        
        # Ajout des conditions d'état
        if states:
            if not isinstance(states, list):
                raise ValueError("states doit être une liste.")
            filter_condition.append(['state', 'in', states])
        
        payment_states = kwargs.get('payment_states', ['not_paid','partial'])  # Par défaut, on filtre les factures impayées
        if payment_states:
            if not isinstance(payment_states, list):
                raise ValueError("payment_states doit être une liste.")
            filter_condition.append(['payment_state', 'in', payment_states])

        # Définition des champs nécessaires
        fields = [
            'invoice_partner_display_name',  # nom du fournisseur
            'ref',                          # référence facture
            'invoice_date',                 # date facture
            'invoice_date_due',             # date d'échéance
            'amount_total_signed',
            'amount_residual',        # montant en devise de base
            'amount_total_in_currency_signed',  # montant en devise étrangère
            'currency_id',                  # information sur la devise
            'state' ,
            'payment_state'                          # état de la facture
        ]
        
        # Récupération des données
        moves = self.execute_kw('account.move', 'search_read', [filter_condition], {'fields': fields})
        df_moves = pd.DataFrame(moves)
        
        if df_moves.empty:
            return False, "Aucune facture trouvée avec les critères spécifiés."
        
        # Préparation et nettoyage des données
        df_moves = df_moves.rename(columns={
            'invoice_partner_display_name': 'supplier_name',
            'ref': 'invoice_ref'
        })
        
        # Conversion des dates avec gestion des erreurs
        try:
            # Conversion explicite des dates avec format
            df_moves['invoice_date'] = pd.to_datetime(df_moves['invoice_date'], format='%Y-%m-%d', errors='coerce')
            df_moves['invoice_date_due'] = pd.to_datetime(df_moves['invoice_date_due'], format='%Y-%m-%d', errors='coerce')
            
            # Vérification des conversions
            print(f"Types des colonnes après conversion :")
            print(f"invoice_date: {df_moves['invoice_date'].dtype}")
            print(f"invoice_date_due: {df_moves['invoice_date_due'].dtype}")
            
            # Nettoyage des lignes avec dates invalides
            invalid_dates = df_moves[df_moves['invoice_date_due'].isna()]
            if not invalid_dates.empty:
                print(f"Attention : {len(invalid_dates)} lignes avec des dates d'échéance invalides ont été trouvées")
            
            df_moves = df_moves.dropna(subset=['invoice_date_due'])
            
        except Exception as e:
            print(f"Erreur lors de la conversion des dates : {str(e)}")
            print(f"Valeurs uniques dans invoice_date_due : {df_moves['invoice_date_due'].unique()}")
            return False, "Erreur lors de la conversion des dates"
        
        # Extraction de la devise
        df_moves['currency_code'] = df_moves['currency_id'].apply(
            lambda x: x[1] if isinstance(x, list) and len(x) == 2 else None
        )
        
        # Calcul des tranches d'ancienneté avec gestion des erreurs
        today = pd.Timestamp.now().date()
        
        # Calcul des jours de retard sans utiliser .dt directement
        df_moves['days_overdue'] = df_moves['invoice_date_due'].apply(
            lambda x: (today - x.date()).days if pd.notnull(x) else None
        )
        
        # Initialisation des colonnes d'ancienneté
        df_moves['on_date'] = 0.0
        df_moves['days_1_30'] = 0.0
        df_moves['days_31_60'] = 0.0
        df_moves['days_60_plus'] = 0.0
        
        # Création des colonnes d'ancienneté pour toutes les factures
        df_moves['period'] = pd.cut(
            df_moves['days_overdue'],
            bins=[-float('inf'), 0, 30, 60, float('inf')],
            labels=['current', '1-30', '31-60', '60+']
        )
        
        # Création d'un DataFrame pour conserver toutes les factures individuelles
        df_details = df_moves.copy()
        
        # Création d'un résumé par fournisseur si demandé
        if not supplier_name:
            supplier_currencies = df_moves.groupby('supplier_name')['currency_code'].first().reset_index()
            df_summary = df_moves.groupby(['supplier_name', 'period']).agg({
                'amount_residual': 'sum',
                'amount_total_in_currency_signed': 'sum',
                'currency_code': 'first',
                'invoice_date_due': 'count'  # Nombre de factures dans chaque période
            }).reset_index()
            
            # Pivot pour avoir les périodes en colonnes
            df_summary = df_summary.pivot(
                index='supplier_name',
                columns='period',
                values=['amount_residual', 'amount_total_in_currency_signed']
            ).fillna(0)
            
            # Réorganisation des colonnes pour plus de clarté
            df_summary.columns = [f"{col[0]}_{col[1]}" for col in df_summary.columns]
            df_summary = df_summary.reset_index()
            df_summary = df_summary.merge(supplier_currencies, on='supplier_name', how='left')
            return (df_summary, df_details), (df_summary.to_dict('records'), df_details.to_dict('records'))
        
        # Si un fournisseur spécifique est demandé, retourner uniquement ses factures détaillées
        return df_details, df_details.to_dict('records')

    
    def aged_customers(self, **kwargs):
        """
        Analyse les factures clients par tranches d'ancienneté par rapport à la date d'échéance.
        
        Arguments :
        - customer_name (str, optionnel) : Nom du client pour filtrer les factures
        - states (list, optionnel) : Liste des états des factures pour filtrer
        
        Retourne :
        - DataFrame contenant l'analyse d'ancienneté des factures
        - Dictionnaire contenant les mêmes informations que le DataFrame
        """
        customer_name = kwargs.get('customer_name')
        states = kwargs.get('states', ['posted'])  # Par défaut, on ne prend que les factures validées
        
        # Initialiser les conditions de filtre
        filter_condition = [['move_type', '=', 'out_invoice']]  # Factures clients uniquement
        
        # Vérification et ajout du filtre client
        if customer_name:
            df_customers = self.search_customer(customer_name)
            
            if isinstance(df_customers, str) and df_customers == 'empty dataframe':
                return False, f"Le client avec le nom '{customer_name}' n'existe pas."
            
            elif df_customers.shape[0] == 1:
                customer_name = df_customers.iloc[0]['name']
                filter_condition.append(['invoice_partner_display_name', '=', customer_name])
            
            elif df_customers.shape[0] > 1:
                found_names = df_customers['name'].tolist()
                return False, f"Plusieurs clients trouvés pour '{customer_name}': {', '.join(found_names)}. Veuillez préciser le nom exact."
        
        # Ajout des conditions d'état
        if states:
            if not isinstance(states, list):
                raise ValueError("states doit être une liste.")
            filter_condition.append(['state', 'in', states])
        
        payment_states = kwargs.get('payment_states', ['not_paid', 'partial'])  # Par défaut, on filtre les factures impayées
        if payment_states:
            if not isinstance(payment_states, list):
                raise ValueError("payment_states doit être une liste.")
            filter_condition.append(['payment_state', 'in', payment_states])

        # Définition des champs nécessaires
        fields = [
            'invoice_partner_display_name',  # nom du client
            'ref',                          # référence facture
            'invoice_date',                 # date facture
            'invoice_date_due',             # date d'échéance
            'amount_total_signed',
            'amount_residual',        # montant en devise de base
            'amount_total_in_currency_signed',  # montant en devise étrangère
            'currency_id',                  # information sur la devise
            'state',
            'payment_state'                 # état de la facture
        ]
        
        # Récupération des données
        moves = self.execute_kw('account.move', 'search_read', [filter_condition], {'fields': fields})
        df_moves = pd.DataFrame(moves)
        
        if df_moves.empty:
            return False, "Aucune facture trouvée avec les critères spécifiés."
        
        # Préparation et nettoyage des données
        df_moves = df_moves.rename(columns={
            'invoice_partner_display_name': 'customer_name',
            'ref': 'invoice_ref'
        })
        
        # Conversion des dates
        try:
            df_moves['invoice_date'] = pd.to_datetime(df_moves['invoice_date'], format='%Y-%m-%d', errors='coerce')
            df_moves['invoice_date_due'] = pd.to_datetime(df_moves['invoice_date_due'], format='%Y-%m-%d', errors='coerce')
            df_moves = df_moves.dropna(subset=['invoice_date_due'])
        except Exception as e:
            return False, "Erreur lors de la conversion des dates"
        
        # Extraction de la devise
        df_moves['currency_code'] = df_moves['currency_id'].apply(
            lambda x: x[1] if isinstance(x, list) and len(x) == 2 else None
        )
        
        # Calcul des tranches d'ancienneté
        today = pd.Timestamp.now().date()
        df_moves['days_overdue'] = df_moves['invoice_date_due'].apply(
            lambda x: (today - x.date()).days if pd.notnull(x) else None
        )
        
        df_moves['on_date'] = 0.0
        df_moves['days_1_30'] = 0.0
        df_moves['days_31_60'] = 0.0
        df_moves['days_60_plus'] = 0.0
        
        df_moves['period'] = pd.cut(
            df_moves['days_overdue'],
            bins=[-float('inf'), 0, 30, 60, float('inf')],
            labels=['current', '1-30', '31-60', '60+']
        )
        
        # Création des détails et résumé
        df_details = df_moves.copy()
        
        if not customer_name:
            customer_currencies = df_moves.groupby('customer_name')['currency_code'].first().reset_index()
            df_summary = df_moves.groupby(['customer_name', 'period']).agg({
                'amount_residual': 'sum',
                'amount_total_in_currency_signed': 'sum',
                'currency_code': 'first',
                'invoice_date_due': 'count'
            }).reset_index()
            
            df_summary = df_summary.pivot(
                index='customer_name',
                columns='period',
                values=['amount_residual', 'amount_total_in_currency_signed']
            ).fillna(0)
            
            df_summary.columns = [f"{col[0]}_{col[1]}" for col in df_summary.columns]
            df_summary = df_summary.reset_index()
            df_summary = df_summary.merge(customer_currencies, on='customer_name', how='left')
            
            return (df_summary, df_details), (df_summary.to_dict('records'), df_details.to_dict('records'))
        
        return df_details, df_details.to_dict('records')

    
    
    def update_accounts(self, accounts_data):
        """
        Met à jour un ou plusieurs comptes avec adaptation automatique
        """
        try:
            update_results = []

            for account_data in accounts_data:
                account_id = account_data.pop('account_id', None)
                if not account_id:
                    update_results.append({'success': False, 'message': "Missing 'account_id' in payload"})
                    continue

                # Vérifier si le compte existe
                account_exists = self.execute_kw(
                    'account.account',
                    'search_read',
                     [[['id', '=', int(account_id)]]],
                    {'fields': ['id']}
                )
                if not account_exists:
                    update_results.append({'success': False, 'message': f"Account with ID '{account_id}' not found"})
                    continue

                if not account_data:
                    update_results.append({'success': False, 'message': f"No fields provided for account ID '{account_id}'"})
                    continue

                # Utiliser le ModelManager pour adapter et mettre à jour
                result = self.model_manager.execute_write(
                    erp_instance=self,
                    model_name='account.account',
                    record_ids=[int(account_id)],
                    values=account_data
                )
                
                if result:
                    update_results.append({'success': True, 'account_id': account_id, 'message': "Account successfully updated"})
                else:
                    update_results.append({'success': False, 'account_id': account_id, 'message': "Failed to update account"})

            return {'success': True, 'results': update_results}

        except Exception as e:
            print(f"Error while updating accounts: {e}")
            return {'success': False, 'message': 'Failed to update accounts'}



    def get_account_types(self):
        """
        Récupère la liste des `account_type` disponibles dans Odoo.
        
        :return: Liste des `account_type` disponibles.
        """
        try:
            # Utiliser fields_get pour obtenir les options du champ 'account_type'
            account_fields = self.execute_kw(
                'account.account',
                'fields_get',
                [],
                {'attributes': ['selection']}
            )
            
            # Extraire les options du champ 'account_type'
            account_type_selection = account_fields.get('account_type', {}).get('selection', [])
            
            # Retourner uniquement les clés (valeurs techniques) des types
            return [item[0] for item in account_type_selection]
        
        except Exception as e:
            print(f"Erreur lors de la récupération des types de comptes : {e}")
            return []

    def get_fiscal_localization(self):
        # Recherche de l'ID de la société par son nom
        company_id = self.execute_kw('res.company', 'search', [[('name', '=', self.company_name)]])
        
        if not company_id:
            raise ValueError(f"La société '{self.company_name}' n'a pas été trouvée.")
        
        # Récupération des informations de la société avec les champs supplémentaires
        company_data = self.execute_kw('res.company', 'read', [company_id, ['country_id', 'currency_id', 'street', 'website', 'vat', 'email']])
        
        if not company_data:
            raise ValueError(f"Impossible de récupérer les données de la société '{self.company_name}'.")
        
        # Extraction des informations
        country = company_data[0]['country_id'][1] if company_data[0]['country_id'] else "Non défini"
        currency = company_data[0]['currency_id'][1] if company_data[0]['currency_id'] else "Non défini"
        street = company_data[0]['street'] or "Non défini"
        website = company_data[0]['website'] or "Non défini"
        vat = company_data[0]['vat'] or "Non défini"
        email = company_data[0]['email'] or "Non défini"
        country_id=company_data[0]['country_id'][0]
        currency_id=company_data[0]['currency_id'][0]
        # Retour des informations
        return {
            "Pays": country,
            "Pays_id":country_id,
            "Devise": currency,
            "Devise_id":currency_id,
            "Adresse": street,
            "Site web": website,
            "TVA": vat,
            "Email": email
        }

    def get_odoo_company_names(self, company_name=None):
        """Récupère les informations de société"""
        domain = [('name', '=', company_name)] if company_name else []
        fields_to_retrieve = ['name', 'id', 'partner_id']

        companies = self.execute_kw('res.company', 'search_read', [domain], {'fields': fields_to_retrieve})
        if companies:
            df_companies = pd.DataFrame(companies)
            
            if not df_companies.empty:
                filtered_df = df_companies[df_companies['name'] == company_name] if company_name else df_companies
                return filtered_df[['name', 'id']].to_dict('records')[0]
            else:
                return {'error': 'No companies found'}
        else:
            return {'error': 'Failed to retrieve companies'}

    
    def get_company_names(self):
        # Interroger le modèle res.company pour obtenir les noms des sociétés
        company_ids = self.execute_kw('res.company', 'search', [[]])
        companies = self.execute_kw('res.company', 'read', [company_ids, ['name']])
        print(f"impression des companies:{companies}")
        return [company['name'] for company in companies]

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

        if not df.empty:
            # Application des filtres optionnels uniquement si des données existent
            if journal_id is not None:
                if 'journal_id' in df.columns:
                    df = df[df['journal_id'].apply(lambda x: x[0] == journal_id if isinstance(x, (list, tuple)) and x else False)]
            
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

    def get_journal_list(self, journal_type='purchase'):
        """
        Retrieves a list of journals of a specified type from the Odoo database.
        If no type is specified, retrieves all purchase journals.

        :param journal_type: Optional; specifies the type of journals to retrieve. Can be 'sale', 'cash', 'bank', 'general', or 'purchase'.
        :return: A JSON string representing a list of dictionaries, each containing the details of the specified type of journal, or an error message.
        """
        try:
            # Define the fields that you want to retrieve for each journal
            fields_to_retrieve = ['name', 'type', 'code', 'active', 'default_account_id', 'company_id','currency_id','profit_account_id','loss_account_id']
            
            # Use the search_read method to get all records from the account.journal model
            journals = self.execute_kw('account.journal', 'search_read', [[]], {'fields': fields_to_retrieve})
            df_journals = pd.DataFrame(journals)
            
            # Filter the DataFrame to get only journals of the specified type and matching company name
            filtered_df = df_journals[(df_journals['type'] == journal_type) & (df_journals['company_id'].apply(lambda x: x[1] == self.company_name))]
            
            # Check if the filtered list is empty and return an appropriate message or the list of journals in JSON
            if filtered_df.empty:
                return f'No {journal_type} journals found'
            else:
                return filtered_df.to_json(orient='records')
        except Exception as e:
            print(f"Error while retrieving journals: {e}")
            return 'Failed to retrieve journals'

    def expand_list_columns(self, df):
        """
        Transforme les colonnes contenant des listes en deux colonnes :
        - Le premier élément de la liste conserve le nom d'origine de la colonne.
        - Le deuxième élément de la liste utilise le suffixe '_name' au lieu de '_id'.

        Args:
            df (pd.DataFrame): Le DataFrame d'entrée.

        Returns:
            pd.DataFrame: Le DataFrame transformé avec les colonnes de listes éclatées.
        """
        for col in df.columns:
            # Vérifier si la colonne contient des listes
            if df[col].apply(lambda x: isinstance(x, list)).any():
                # Créer une colonne temporaire pour extraire le deuxième élément avant de modifier la colonne d'origine
                second_element_col = col.replace('_id', '') + '_name'
                
                # Extraire le premier élément (sans modifier immédiatement la colonne d'origine)
                df[col + "_temp"] = df[col].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else None)
                
                # Extraire le deuxième élément dans une nouvelle colonne '_name'
                df[second_element_col] = df[col].apply(lambda x: x[1] if isinstance(x, list) and len(x) > 1 else None)
                
                # Remplacer la colonne d'origine par le premier élément
                df[col] = df[col + "_temp"]
                
                # Supprimer la colonne temporaire utilisée pour l'extraction
                df.drop(columns=[col + "_temp"], inplace=True)

        return df

    def get_journal_id_by_move_id_lines(self, move_ids):
        """
        Recherche les lignes d'écriture (account.move.line) correspondant aux move_ids fournis.
        
        Args:
            move_ids (list or int): Liste des IDs de mouvements (ou un seul ID) à rechercher.
        
        Returns:
            list: Liste des lignes d'écriture trouvées.
            str: Message d'erreur si aucun résultat n'est trouvé.
        """
        if not move_ids:
            return "Veuillez indiquer le code interne à la facture d'ODOO qui se trouve sous le champ 'name' des factures des fournisseurs recherchés."
        
        # Assurez-vous que move_ids est une liste
        if isinstance(move_ids, int):
            move_ids = [move_ids]  # Convertir en liste si un seul move_id est fourni

        all_lines = []  # Liste pour stocker toutes les lignes d'écriture trouvées

        # Traitement de chaque move_id dans la liste
        for move_id in move_ids:
            # Ajout d'un filtre pour rechercher des lignes d'écriture par move_id
            domain = [['move_id', '=', move_id]]

            # Recherche des lignes d'écriture dans le modèle 'account.move.line'
            lines = self.execute_kw(
                'account.move.line',  # Modèle cible
                'search_read',  # Méthode de recherche
                [domain],  # Filtre
                {
                    'fields': [
                        'id', 'name', 'move_id', 'journal_id', 'account_id', 'partner_id',
                        'debit', 'credit', 'date', 'amount_currency', 'currency_id',
                        'product_id', 'quantity', 'price_unit', 'tax_ids', 'full_reconcile_id'
                    ]
                }
            )

            if not lines:
                return f"Aucune ligne d'écriture correspondante trouvée pour l'ID {move_id}. Veuillez vérifier le numéro de facture fourni."

            # Ajouter les lignes trouvées à la liste globale
            all_lines.extend(lines)

        if not all_lines:
            return "Aucune ligne d'écriture correspondante trouvée pour les IDs fournis."

        # Retourner les lignes d'écriture trouvées
        return all_lines    
    
    def get_journal_id_by_move_id(self, move_ids):
        """
        Recherche les mouvements comptables (account.move) correspondant aux move_ids fournis.
        
        Args:
            move_ids (list or int): Liste des IDs de mouvements (ou un seul ID) à rechercher.
        
        Returns:
            list: Liste des mouvements comptables trouvés.
            str: Message d'erreur si aucun résultat n'est trouvé.
        """
        if not move_ids:
            return "Veuillez indiquer le code interne à la facture d'ODOO qui se trouve sous le champ 'name' des factures des fournisseurs recherchés."
        
        # Assurez-vous que move_ids est une liste
        if isinstance(move_ids, int):
            move_ids = [move_ids]  # Convertir en liste si un seul move_id est fourni

        all_moves = []  # Liste pour stocker tous les mouvements comptables trouvés

        # Traitement de chaque move_id dans la liste
        for move_id in move_ids:
            # Ajout d'un filtre pour rechercher des mouvements comptables par id
            domain = [['id', '=', move_id]]

            # Recherche des mouvements comptables dans le modèle 'account.move'
            moves = self.execute_kw(
                'account.move',  # Modèle cible
                'search_read',  # Méthode de recherche
                [domain],  # Filtre
                {
                    'fields': [
                        'id', 'name', 'journal_id', 'partner_id', 'invoice_date', 'date',
                        'payment_reference', 'invoice_date_due', 'ref', 'amount_residual',
                        'company_id', 'payment_state', 'payment_id', 'transaction_ids',
                        'amount_residual_signed', 'amount_paid', 'currency_id'
                    ]
                }
            )

            if not moves:
                return f"Aucun mouvement comptable correspondant trouvé pour l'ID {move_id}. Veuillez vérifier le numéro de facture fourni."

            # Ajouter les mouvements trouvés à la liste globale
            all_moves.extend(moves)

        if not all_moves:
            return "Aucun mouvement comptable correspondant trouvé pour les IDs fournis."

        # Retourner les mouvements comptables trouvés
        return all_moves


