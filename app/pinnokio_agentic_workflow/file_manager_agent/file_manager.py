"""
FileManager Pinnokio Agent et Google Apps Agent
================================================

Agents spécialisés pour la gestion de fichiers DMS et la création de contenu Google Apps.

Auteur: Assistant IA
Date: 2025
"""

from ...llm.klk_agents import BaseAIAgent, ModelSize, ModelProvider, NEW_MOONSHOT_AIAgent
from tools.g_cred import DriveClientService, FireBaseManagement
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import requests
import traceback
from datetime import datetime as dt


# ============================================================================
# FILEMANAGER PINNOKIO AGENT - Agent de gestion documentaire avec DMS
# ============================================================================

class FileManagerPinnokio(BaseAIAgent):
    """
    Agent FileManager spécialisé dans la gestion de documents et fichiers dans un DMS.
    Supporte google_drive initialement, extensible à d'autres DMS.
    Peut déléguer la création/modification de contenu à GoogleAppsAgent.
    """
    
    def __init__(self,
                 dms_system='google_drive',
                 dms_mode='prod',
                 mandate_path=None,
                 firebase_user_id=None,
                 job_id=None,
                 collection_name=None,
                 communication_mode='google_chat',
                 tracking_endpoint_url=None,
                 chat_system=None,
                 **kwargs):
        """
        Initialise l'agent FileManager.
        
        Args:
            dms_system (str): Système DMS à utiliser ('google_drive', etc.)
            dms_mode (str): Mode d'authentification ('prod' ou 'dev')
            mandate_path (str): Chemin du mandat dans Firebase
            firebase_user_id (str): ID de l'utilisateur Firebase
            job_id (str): ID du job en cours
            collection_name (str): Nom de la collection/espace
            communication_mode (str): Mode de communication ('google_chat', 'telegram', etc.)
            tracking_endpoint_url (str): URL de l'endpoint de tracking/callback
            chat_system (GoogleSpaceManager): Instance du système de chat
        """
        # Initialiser la classe de base avec DMS
        super().__init__(
            collection_name=collection_name,
            dms_system=dms_system,
            dms_mode=dms_mode,
            firebase_user_id=firebase_user_id,
            job_id=job_id,
            function_name='file_manager_pinnokio'
        )
        
        self.mandate_path = mandate_path
        self.collection_name = collection_name
        self.communication_mode = communication_mode
        self.tracking_endpoint_url = tracking_endpoint_url or "http://localhost:8080/file_manager_tracking"
        self.chat_system = chat_system
        
        # Récupérer le profil client et les données du mandat (comme dans new_router.py)
        self.client_profile_data = self._load_client_profile_data(collection_name, firebase_user_id)
        
        # Extraire le dossier racine (drive_client_parent_id)
        self.root_folder_id = self.client_profile_data.get('mandate_drive_space_parent_id', None)
        
        # Initialiser GoogleAppsAgent (agent secondaire)
        self.gapp_agent = GoogleAppsAgent(
            firebase_user_id=firebase_user_id,
            job_id=job_id,
            collection_name=collection_name,
            parent_agent=self
        )
        
        # Initialiser DriveAgent (agent secondaire)
        self.drive_agent = DriveAgent(
            parent_agent=self,
            drive_service=self.dms_system,  # DriveClientService depuis BaseAIAgent._initialize_dms()
            root_folder_id=self.root_folder_id,
            firebase_user_id=firebase_user_id,
            job_id=job_id,
            collection_name=collection_name
        )
        
        # Initialiser le system prompt
        self._initialize_file_manager_prompt()
        
        print(f"✅ FileManagerPinnokio initialisé avec DMS: {dms_system}, Mode: {dms_mode}")
        if self.root_folder_id:
            print(f"📂 Dossier racine configuré: {self.root_folder_id}")
    
    def _load_client_profile_data(self, collection_name: Optional[str], firebase_user_id: Optional[str]) -> Dict[str, Any]:
        """
        Charge les données du profil client depuis Firebase + cartographie Drive.
        
        Returns:
            Dict: Dictionnaire contenant toutes les données du profil client + cartographie
        """
        if not collection_name:
            return {}
        
        try:
            firebase_service = FireBaseManagement(user_id=firebase_user_id)
            
            # Récupérer les données combinées du mandat et du client
            client_dict = firebase_service.get_combined_mandate_and_client_data(collection_name)
            client_uuid = client_dict.get('client_uuid')
            
            if not client_uuid:
                print("⚠️ Aucun client_uuid trouvé")
                return {}
            
            # Reconstruire le profil complet du client
            profile_data = firebase_service.reconstruct_full_client_profile(client_uuid, collection_name)
            
            # Créer un dictionnaire avec toutes les données (sans self.xxx)
            client_profile = {
                'input_drive_doc_id': profile_data.get('mandate_input_drive_doc_id', None),
                'output_drive_doc_id': profile_data.get('mandate_output_drive_doc_id', None),
                'mandat_space_id': profile_data.get('mandate_contact_space_id', None),
                'mandat_space_name': profile_data.get('mandate_contact_space_name', None),
                'client_name': profile_data.get('client_name', None),
                'legal_name': profile_data.get('mandate_legal_name', None),
                'uuid_id': profile_data.get('client_uuid', None),
                'country': profile_data.get('mandate_country', None),
                'gl_sheet_id': profile_data.get('gl_sheet_id', None),
                'drive_client_parent_id': profile_data.get('drive_client_parent_id', None),  # ← DOSSIER RACINE
                'doc_folder_id': profile_data.get('mandate_main_doc_drive_id', None),
                'erp_type': profile_data.get('erp_erp_type', None),
                'odoo_url': profile_data.get('erp_odoo_url', None),
                'odoo_username': profile_data.get('erp_odoo_username', None),
                'secret_manager': profile_data.get('erp_secret_manager', None),
                'odoo_db': profile_data.get('erp_odoo_db', None),
                'mandate_drive_space_parent_id': profile_data.get('mandate_drive_space_parent_id', None),
                'odoo_company_name': profile_data.get('erp_odoo_company_name', None),
                'mandat_base_currency': profile_data.get('mandat_base_currency', None),
                'gl_accounting_erp': profile_data.get('mandate_gl_accounting_erp', None),
                'ar_erp': profile_data.get('mandate_ar_erp', None),
                'ap_erp': profile_data.get('mandate_ap_erp', None),
                'bank_erp': profile_data.get('mandate_bank_erp', None),
                'mandat_country': profile_data.get('country', None),
                'user_language': profile_data.get('mandate_language', ""),
                'telegram_users_mapping': profile_data.get('mandate_telegram_users_mapping', {}),
                'communication_chat_type': profile_data.get('mandate_communication_chat_type', 'pinnokio'),
                'communication_log_type': profile_data.get('mandate_communication_log_type', 'pinnokio'),
                'dms_type': profile_data.get('mandate_dms_type', None),
                'today': dt.today().date()
            }
            
            # ═══════════════════════════════════════════════════════════
            # ENRICHISSEMENT: Cartographie Google Drive
            # ═══════════════════════════════════════════════════════════
            if hasattr(self, 'dms_system') and self.dms_system and client_profile.get('drive_client_parent_id'):
                try:
                    from datetime import datetime as dt_now
                    current_year = dt_now.now().year
                    
                    print(f"📂 Chargement de la cartographie Drive pour l'année {current_year}...")
                    
                    # Appeler klk_router_app pour récupérer la cartographie
                    df_cartography, services, available_years = self.dms_system.klk_router_app(
                        fiscal_year=current_year,
                        root_folder_id=client_profile['drive_client_parent_id'],
                        mandate_path=self.mandate_path if hasattr(self, 'mandate_path') else None
                    )
                    
                    if df_cartography is not None and not df_cartography.empty:
                        # Stocker dans le profil
                        client_profile['drive_cartography_df'] = df_cartography
                        client_profile['drive_services'] = list(services) if services is not None else []
                        client_profile['drive_available_years'] = available_years
                        
                        print(f"✅ Cartographie chargée : {len(df_cartography)} entrées, {len(services)} services")
                    else:
                        print("⚠️ Cartographie vide ou non disponible")
                        client_profile['drive_cartography_df'] = None
                        client_profile['drive_services'] = []
                        client_profile['drive_available_years'] = []
                        
                except Exception as e:
                    print(f"⚠️ Erreur lors du chargement de la cartographie Drive: {e}")
                    client_profile['drive_cartography_df'] = None
                    client_profile['drive_services'] = []
                    client_profile['drive_available_years'] = []
            else:
                client_profile['drive_cartography_df'] = None
                client_profile['drive_services'] = []
                client_profile['drive_available_years'] = []
            
            # ═══════════════════════════════════════════════════════════
            # ENRICHISSEMENT: Départements et leurs prompts
            # ═══════════════════════════════════════════════════════════
            try:
                print(f"🏢 Chargement des départements...")
                
                # Charger les prompts router depuis Firebase
                router_prompts_tuple = firebase_service.load_router_context(
                    mandate_path=self.mandate_path if hasattr(self, 'mandate_path') else None
                )
                
                if router_prompts_tuple:
                    # Le tuple contient (banks_cash, contrats, expenses, financial_statement, hr, invoices, letters, taxes)
                    departments_dict = {
                        'banks_cash': router_prompts_tuple[0],
                        'contrats': router_prompts_tuple[1],
                        'expenses': router_prompts_tuple[2],
                        'financial_statement': router_prompts_tuple[3],
                        'hr': router_prompts_tuple[4],
                        'invoices': router_prompts_tuple[5],
                        'letters': router_prompts_tuple[6],
                        'taxes': router_prompts_tuple[7]
                    }
                    
                    client_profile['departments_prompts'] = departments_dict
                    client_profile['available_departments'] = [k for k, v in departments_dict.items() if v]
                    
                    print(f"✅ {len(client_profile['available_departments'])} départements chargés")
                else:
                    client_profile['departments_prompts'] = {}
                    client_profile['available_departments'] = []
                    
            except Exception as e:
                print(f"⚠️ Erreur lors du chargement des départements: {e}")
                client_profile['departments_prompts'] = {}
                client_profile['available_departments'] = []
            
            print(f"✅ Profil client enrichi pour collection: {collection_name}")
            return client_profile
            
        except Exception as e:
            print(f"⚠️ Erreur lors du chargement du profil client: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def _initialize_file_manager_prompt(self):
        """Initialise le system prompt de l'agent FileManager Principal"""
        
        # Formater la cartographie Drive si disponible
        cartography_text = "Non disponible"
        if self.client_profile_data.get('drive_cartography_df') is not None:
            df = self.client_profile_data['drive_cartography_df']
            if not df.empty:
                cartography_text = f"\n{df.to_string(max_rows=50, max_cols=8)}\n"
        
        # Liste des départements
        departments_list = ", ".join(self.client_profile_data.get('available_departments', []))
        
        # Informations client
        legal_name = self.client_profile_data.get('legal_name', 'Client')
        dms_type = self.client_profile_data.get('dms_type', 'google_drive')
        
        prompt = f"""
            ╔═══════════════════════════════════════════════════════════════╗
            ║     AGENT PRINCIPAL DE GESTION DOCUMENTAIRE - PINNOKIO       ║
            ╚═══════════════════════════════════════════════════════════════╝

            🎯 MISSION PRINCIPALE :
            Vous êtes l'agent principal de gestion documentaire pour la société {legal_name}.
            Votre rôle est de COORDONNER et ORCHESTRER toutes les opérations liées aux 
            documents et fichiers dans le système {dms_type}.

            👔 CONTEXTE CLIENT :
            - Société : {legal_name}
            - Système DMS : {dms_type}
            - Dossier racine : {self.root_folder_id}
            - Langue : {self.client_profile_data.get('user_language', 'fr')}

            📂 CARTOGRAPHIE DU DRIVE :
            {cartography_text}

            📁 STRUCTURE DU DMS - INFORMATIONS CRUCIALES :

            Le dataset part du dossier source 'drive_space_parent_id' (dossier racine de la société).
            À ce niveau, vous trouverez :

            1. **input_drive_doc_id** (doc_to_do) :
            📥 Dossier où les documents à traiter par l'agent Router sont déposés
            ⚠️ NE PAS TOUCHER : Ces documents sont gérés par l'agent Router (hors périmètre)
            🎯 Le Router récupère ces documents et les alloue aux départements concernés

            2. **output_drive_doc_id** (doc_done) :
            📤 Dossier de sortie par défaut
            💡 Utilisez-le pour déposer les documents créés SANS destination finale spécifiée
            Ex: Si l'utilisateur demande un rapport sans dire où le mettre → doc_done

            3. **main_doc_drive_id** (doc) :
            🗂️ Dossier contenant TOUTES LES ANNÉES FISCALES
            📊 Chaque année fiscale a une structure identique dupliquée par département

            STRUCTURE DES ANNÉES FISCALES (dans main_doc_drive_id) :

            Chaque année (ex: 2025/) contient 3 départements principaux :

            ├── 📂 ACCOUNTING/
            │   ├── BANKS_CASH/           → Relevés bancaires, documents banques
            │   ├── EXPENSES/             → Notes de frais, dépenses
            │   ├── FINANCIAL_STATEMENT/
            │   │   ├── Final/           → États financiers définitifs
            │   │   └── Intermediary/    → États financiers provisoires
            │   └── INVOICES/            → Factures clients/fournisseurs
            │
            ├── 📂 HR/
            │   ├── employees/           → Dossiers employés
            │   ├── Social Charges/      → Charges sociales
            │   └── W.T/                 → Withholding Taxes (impôts à la source)
            │
            └── 📂 LEGAL_&_ADMINISTRATION/
                ├── CONTRATS/            → Contrats divers
                ├── LETTERS/             → Correspondances
                └── TAXES/
                    ├── O.T/             → Ordinary Taxes (impôts ordinaires)
                    ├── S.T/             → Special Taxes (impôts spéciaux)
                    └── VAT/             → TVA

            ⚠️ RÈGLE CRUCIALE - DOSSIERS doc_to_do :

            Chaque sous-département possède un dossier 'doc_to_do' (visible dans votre cartographie).
            🚫 NE JAMAIS TRAITER ces dossiers de votre propre initiative !
            📋 Ces documents sont DÉJÀ dans les paramètres des agents dédiés à ces départements.
            ✅ N'y touchez QUE si explicitement demandé par l'utilisateur.

            📝 AUTRES DOSSIERS POSSIBLES :

            Au-delà de cette structure de base, vous pourrez trouver d'autres dossiers créés selon :
            - La personnalisation de la société
            - Son organisation interne
            - Sa structure hiérarchique
            - Ses workflows spécifiques

            💡 Pour ces dossiers personnalisés, consultez le prompt du département concerné via 
            get_departement_prompt(department) pour comprendre leur fonction et leur usage.

            🏢 DÉPARTEMENTS DISPONIBLES :
            {departments_list}

            🤖 AGENTS SUBORDONNÉS À VOTRE DISPOSITION :

            1. **DriveAgent** : Spécialiste des manipulations de fichiers
            Capacités : rechercher, créer dossiers, déplacer, renommer, supprimer, copier fichiers
            Usage : CALL_DRIVE_AGENT("instructions détaillées")
            Retour : Rapport textuel d'exécution

            2. **GoogleAppsAgent** : Spécialiste de création de contenu Google
            Capacités : créer Docs, Sheets, Slides, formater documents
            Usage : CALL_GAPP_AGENT("instructions détaillées + EMPLACEMENT obligatoire")
            ⚠️ IMPORTANT : TOUJOURS spécifier l'emplacement de création
            Retour : Rapport textuel avec URLs des documents créés

            🔧 VOS OUTILS DIRECTS :

            1. **get_departement_prompt(department)** :
            Charge le contexte métier spécifique d'un département
            Ex: get_departement_prompt("invoices") pour obtenir les règles de facturation

            2. **vision_document(file_id, question)** :
            Analyse visuelle d'un document via IA
            Ex: vision_document("abc123", "Quelle est la date de ce document?")

            3. **create_fiscal_year_structure(fiscal_year)** :
            Crée automatiquement la structure complète de dossiers pour une année fiscale
            Ex: create_fiscal_year_structure(2025)

            4. **ASK_USER(question)** :
            Conversation directe avec l'utilisateur pour obtenir des clarifications
            Ex: ASK_USER("Où souhaitez-vous archiver ce document?")

            5. **CALL_DRIVE_AGENT(instructions)** :
            Délègue les manipulations de fichiers au DriveAgent
            Ex: CALL_DRIVE_AGENT("Recherche tous les PDF de 2025 et déplace-les dans Factures 2025")

            6. **CALL_GAPP_AGENT(instructions)** :
            Délègue la création de contenu au GoogleAppsAgent
            Ex: CALL_GAPP_AGENT("Crée un Google Doc 'Rapport Q1' dans le dossier Rapports")

            7. **TERMINATE_FILE_MANAGEMENT()** :
            Clôture de la mission avec rapport complet

            ⚙️ WORKFLOW RECOMMANDÉ :

            1. **Analyser** la demande utilisateur
            2. **🗺️ CONSULTER LA CARTOGRAPHIE** pour identifier les folder_id nécessaires
            3. **Charger contexte** si besoin → get_departement_prompt()
            4. **Analyser documents** si besoin → vision_document()
            5. **Créer structure** si besoin → create_fiscal_year_structure()
            6. **Manipuler fichiers** → CALL_DRIVE_AGENT() **AVEC les folder_id de la cartographie**
            7. **Créer contenu** → CALL_GAPP_AGENT() (avec emplacement + folder_id!)
            8. **Clarifier** si besoin → ASK_USER()
            9. **Clôturer** → TERMINATE_FILE_MANAGEMENT()

            🎯 MISSIONS TYPIQUES QUE VOUS POUVEZ RECEVOIR :

            1. **CONSULTATION DE DOCUMENTS** :
            Exemple: "Lis le contenu du document 'Rapport Q1'"
            Action: 
            - Si seul le NOM est donné → 🗺️ Consultez la cartographie pour identifier le dossier
            - Identifiez le service_folder_id approprié (ex: INVOICES → '1ABC...')
            - Déléguez à CALL_DRIVE_AGENT avec le folder_id exact
            - Si besoin d'analyse visuelle → vision_document()
            
            Exemple d'instruction correcte :
            "Cherche le fichier 'Rapport Q1' dans le dossier folder_id='1ABC123XYZ' 
            (service_folder_id de INVOICES/2025)"

            2. **CRÉATION DE RAPPORTS** :
            Exemple: "Crée un résumé du document 'Facture_123' dans un Google Doc"
            Action:
            - Localiser le document source
            - Analyser son contenu (vision_document si nécessaire)
            - CALL_GAPP_AGENT pour créer le rapport (SPÉCIFIER l'emplacement!)
            - Si pas d'emplacement précis → output_drive_doc_id (doc_done)
            - Possibilité d'itérations: demander modifications, approuver le travail

            3. **RÉORGANISATION/ARCHIVAGE** :
            Exemple: "Déplace les fichiers mal archivés de 2024 vers 2025"
            Action:
            - Identifier les fichiers mal placés (mauvaise année, mauvais département)
            - Utiliser la cartographie pour trouver le bon emplacement
            - CALL_DRIVE_AGENT pour effectuer les déplacements
            - Vérifier la cohérence (année fiscale, département, sous-catégorie)

            4. **GESTION STRUCTURE** :
            Exemple: "Crée la structure pour l'année 2026"
            Action:
            - Vérifier si l'année existe déjà (cartographie)
            - create_fiscal_year_structure(2026)
            - Confirmer la création

            5. **RECHERCHE ET ANALYSE** :
            Exemple: "Trouve tous les contrats de 2025 et dis-moi combien il y en a"
            Action:
            - CALL_DRIVE_AGENT pour recherche dans LEGAL_&_ADMINISTRATION/CONTRATS/2025/
            - Compiler les résultats
            - Rapport à l'utilisateur

            ⚠️ INSTRUCTIONS IMPORTANTES :

            - Vous êtes le **COORDINATEUR**, déléguez les tâches spécifiques
            - Les agents subordonnés peuvent vous poser des questions (via text_output)
            - Utilisez la cartographie Drive pour localiser les éléments
            - **TOUJOURS** vérifier l'emplacement avant de créer du contenu
            - Communiquez clairement avec l'utilisateur si besoin
            - 🚫 N'INTERVENEZ JAMAIS dans les dossiers doc_to_do SAUF demande explicite
            - ✅ Agissez UNIQUEMENT sur instruction utilisateur directe
            
            🚨 **RÈGLE CRITIQUE - VÉRITÉ ET TRANSPARENCE** :
            - **NE JAMAIS INVENTER** de résultats ou prétendre qu'une opération a réussi
            - **TOUJOURS vérifier** le statut retourné par vos agents subordonnés
            - Si DriveAgent retourne "MAX_TURNS_REACHED" ou "FAILURE", **NE PAS dire que c'est réussi**
            - Si un agent subordonné échoue, vous **DEVEZ** le signaler dans votre rapport
            - **BASEZ-VOUS UNIQUEMENT** sur les retours réels de vos agents, jamais sur des suppositions
            - En cas de doute ou d'échec partiel, demandez clarification ou signalez l'échec

            💬 COMMUNICATION AVEC VOS AGENTS SUBORDONNÉS :

            Quand vous appelez CALL_DRIVE_AGENT ou CALL_GAPP_AGENT :
            - Si l'agent retourne un STATUS "WAITING_INPUT" avec un text_output
            - C'est qu'il vous pose une QUESTION ou demande une CLARIFICATION
            - Répondez-lui en rappelant le même outil avec votre réponse
            - L'agent conserve son historique jusqu'au TERMINATE
            
            🗺️ **UTILISATION OBLIGATOIRE DE LA CARTOGRAPHIE DRIVE** :
            
            Vous avez accès à la cartographie Drive complète ci-dessus.
            
            ⚠️ **RÈGLE ABSOLUE** : Quand vous déléguez au DriveAgent :
            
            1. **CONSULTEZ D'ABORD** la cartographie pour identifier les folder_id exacts
            2. **FOURNISSEZ TOUJOURS** les folder_id dans vos instructions au DriveAgent
            3. **NE JAMAIS** donner juste un nom de dossier sans son ID
            
            ❌ **MAUVAIS EXEMPLE** :
            "Cherche dans le dossier des factures..."
            
            ✅ **BON EXEMPLE** :
            "Cherche le fichier 'digitec' dans le dossier avec folder_id='1ABC123XYZ' 
            (dossier service_folder_id de la ligne INVOICES/2025 de la cartographie)"
            
            📋 **COMMENT UTILISER LA CARTOGRAPHIE** :
            
            - Colonne 'Department' : Département principal (ACCOUNTING, HR, LEGAL_&_ADMINISTRATION)
            - Colonne 'Service' : Sous-service (INVOICES, EXPENSES, BANKS_CASH, etc.)
            - Colonne 'departement_folder_id' : ID du dossier département
            - Colonne 'service_folder_id' : ID du dossier service (⭐ UTILISEZ CELUI-CI)
            - Colonne 'Year' : Année fiscale
            
            💡 Pour trouver le dossier des factures de 2025 :
            1. Regardez la ligne avec Service='INVOICES' et Year=2025
            2. Prenez le 'service_folder_id' de cette ligne
            3. Utilisez cet ID dans vos instructions au DriveAgent

            📊 RAPPORT FINAL OBLIGATOIRE (TERMINATE_FILE_MANAGEMENT) :

            {{
            "job_id": "{self.job_id}",
            "operation_status": "SUCCESS | PARTIAL_SUCCESS | FAILURE",
            "files_processed": ["liste des fichiers"],
            "folders_created": ["liste des dossiers"],
            "documents_created": [
                {{"type": "doc", "url": "https://...", "title": "..."}}
            ],
            "errors_encountered": ["liste des erreurs"],
            "conclusion": "Résumé détaillé incluant actions de vos agents subordonnés"
            }}

            ⚙️ CONTEXTE TECHNIQUE :
            - DMS : {dms_type}
            - Job ID : {self.job_id}
            - Collection : {self.chroma_db_instance.collection_name if self.chroma_db_instance else 'N/A'}

            Vous êtes maintenant prêt à orchestrer vos agents ! Commencez votre mission.
            """
        self.update_system_prompt(prompt)
    
    # ═══════════════════════════════════════════════════════════════
    # NOUVEAUX OUTILS AGENT PRINCIPAL
    # ═══════════════════════════════════════════════════════════════
    
    def get_departement_prompt(self, department: str) -> Dict[str, Any]:
        """
        Charge le prompt spécifique d'un département depuis Firebase.
        
        Args:
            department: Nom du département (banks_cash, invoices, expenses, hr, etc.)
        
        Returns:
            Dict avec success, prompt et message
        """
        try:
            department_lower = department.lower().strip()
            
            # Vérifier si les départements sont disponibles
            if not self.client_profile_data.get('departments_prompts'):
                return {
                    'success': False,
                    'prompt': None,
                    'message': 'Aucun département configuré pour ce client'
                }
            
            departments = self.client_profile_data['departments_prompts']
            
            # Chercher le département (avec variations possibles)
            department_key = None
            for key in departments.keys():
                if key.lower() == department_lower or department_lower in key.lower():
                    department_key = key
                    break
            
            if not department_key:
                available = ", ".join(self.client_profile_data.get('available_departments', []))
                return {
                    'success': False,
                    'prompt': None,
                    'message': f"Département '{department}' non trouvé. Disponibles: {available}"
                }
            
            prompt = departments.get(department_key, '')
            
            if not prompt:
                return {
                    'success': False,
                    'prompt': None,
                    'message': f"Aucun prompt configuré pour le département '{department_key}'"
                }
            
            return {
                'success': True,
                'prompt': prompt,
                'message': f"Prompt du département '{department_key}' chargé avec succès",
                'department_key': department_key
            }
            
        except Exception as e:
            return {
                'success': False,
                'prompt': None,
                'message': f"Erreur lors du chargement du prompt: {str(e)}"
            }
    
    def vision_document(self, file_id: str, question: str) -> Dict[str, Any]:
        """
        Analyse visuelle d'un document via Vision AI (Anthropic).
        
        Args:
            file_id: ID du fichier dans Google Drive
            question: Question à poser sur le document
        
        Returns:
            Dict avec success, analysis et message
        """
        try:
            if not hasattr(self, 'dms_system') or not self.dms_system:
                return {
                    'success': False,
                    'analysis': None,
                    'message': 'Service Drive non disponible'
                }
            
            print(f"👁️ Analyse visuelle du document {file_id}...")
            
            # Construire le prompt de vision
            vision_prompt = f"""
            Analysez ce document et répondez à la question suivante:
            
            {question}
            
            Soyez précis et concis dans votre réponse.
            """
            
            # ✅ Utiliser self.process_vision directement car FileManagerPinnokio hérite de BaseAIAgent
            from tools.agents import ModelSize
            analysis_result = self.process_vision(
                text=vision_prompt,
                size=ModelSize.MEDIUM,
                file_ids=[file_id]
            )
            
            # Enregistrer les tokens
            if hasattr(self, 'token_manager') and self.chroma_db_instance:
                self.load_token_usage_to_db(
                    project_id=self.chroma_db_instance.collection_name,
                    job_id=self.job_id,
                    workflow_step='vision_document'
                )
            
            return {
                'success': True,
                'analysis': analysis_result,
                'message': f'Analyse visuelle complétée pour le document {file_id}'
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'analysis': None,
                'message': f"Erreur lors de l'analyse visuelle: {str(e)}"
            }
    
    def create_fiscal_year_structure(self, fiscal_year: int) -> Dict[str, Any]:
        """
        Crée la structure de dossiers pour une année fiscale.
        
        NOTA: Logique adaptée de DMS_CREATION.ensure_folder_structure()
        Le modèle de structure est chargé depuis Google Cloud Storage.
        
        Args:
            fiscal_year: Année fiscale (ex: 2025)
        
        Returns:
            Dict avec success, folders_created et message
        """
        try:
            if not hasattr(self, 'dms_system') or not self.dms_system:
                return {
                    'success': False,
                    'folders_created': [],
                    'message': 'Service Drive non disponible'
                }
            
            if not self.root_folder_id:
                return {
                    'success': False,
                    'folders_created': [],
                    'message': 'Dossier racine non configuré'
                }
            
            print(f"📁 Création structure année fiscale {fiscal_year}...")
            
            # Charger le schéma des dossiers depuis Google Cloud Storage
            from tools.g_cred import StorageClient
            import json
            
            bucket_name = 'pinnokio_app'
            destination_blob_name = 'setting_params/router_gdrive_structure/drive_manager.json'
            
            print(f"  📥 Téléchargement du modèle depuis GCS: {destination_blob_name}")
            storage_service = StorageClient()
            file_in_memory = storage_service.download_blob(bucket_name, destination_blob_name)
            folder_description_schema = json.load(file_in_memory)
            print(f"  ✅ Modèle de structure chargé depuis GCS")
            
            # Variable pour stocker l'année courante (utilisée par create_folder_structure_recursive)
            self.current_year = fiscal_year
            
            # Utiliser la méthode récursive pour créer la structure
            folder_info = self._create_folder_structure_recursive_fiscal(
                folder_description_schema, 
                self.root_folder_id
            )
            
            # Compter les dossiers créés
            def count_folders(info_dict):
                count = 0
                for key, value in info_dict.items():
                    if isinstance(value, dict) and 'id' in value:
                        count += 1
                        if 'subfolders' in value:
                            count += count_folders(value['subfolders'])
                return count
            
            total_folders = count_folders(folder_info)
            
            return {
                'success': True,
                'folders_info': folder_info,
                'message': f'Structure année fiscale {fiscal_year} créée avec succès ({total_folders} dossiers)',
                'fiscal_year': fiscal_year
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'folders_info': {},
                'message': f"Erreur lors de la création de la structure: {str(e)}"
            }
    
    def _create_folder_structure_recursive_fiscal(self, folder_schema, parent_id):
        """
        Crée récursivement la structure des dossiers basée sur le schéma JSON
        
        Adapté de DMS_CREATION.create_folder_structure_recursive()
        
        Args:
            folder_schema: Liste des dossiers à créer avec leurs sous-dossiers
            parent_id: ID du dossier parent où créer la structure
        
        Returns:
            dict: Dictionnaire contenant les IDs des dossiers créés
        """
        folder_info = {}
        
        for folder in folder_schema:
            folder_name = folder['folder_name']
            folder_function = folder['folder_function']
            
            # Gestion spéciale pour le dossier "Year"
            if folder_name == "Year":
                folder_name = str(self.current_year)
            
            # Créer ou trouver le dossier
            try:
                current_folder_id, current_folder_name = self.dms_system.create_or_find_folder(
                    self.firebase_user_id,
                    folder_name, 
                    parent_id
                )
                
                # Stocker les informations du dossier
                folder_info[folder_name] = {
                    'id': current_folder_id,
                    'name': current_folder_name,
                    'function': folder_function
                }
                
                print(f"  ✅ Dossier: {folder_name} (ID: {current_folder_id})")
                
                # Traiter les sous-dossiers s'ils existent
                if 'subfolders' in folder and folder['subfolders']:
                    subfolder_info = self._create_folder_structure_recursive_fiscal(
                        folder['subfolders'], 
                        current_folder_id
                    )
                    folder_info[folder_name]['subfolders'] = subfolder_info
                    
            except Exception as folder_error:
                print(f"  ❌ Erreur création dossier {folder_name}: {folder_error}")
                
        return folder_info
    
    def ASK_USER(self, question: str, agent_instance=None, is_auth_request: bool = False) -> Dict[str, Any]:
        """
        Pose une question directe à l'utilisateur via le système de chat.
        Contextualise l'agent si agent_instance est fourni.
        """
        # Si aucune instance externe n'est fournie, on utilise self car c'est une instance de BaseAIAgent
        if agent_instance is None:
            agent_instance = self

        try:
            if not hasattr(self, 'chat_system') or not self.chat_system:
                return {
                    'success': False,
                    'answer': None,
                    'message': 'Système de chat non disponible'
                }
            
            print(f"❓ Question à l'utilisateur: {question}")
            
            # Notification directe (Code existant préservé)
            try:
                from tools.firebase_realtime import FirebaseRealtimeChat
                realtime = FirebaseRealtimeChat(self.firebase_user_id)
                # Note: direct_message_notif n'est pas défini dans le scope, je suppose qu'il s'agit de 'question'
                # ou d'une variable globale. Par sécurité j'utilise 'question'.
                notif_message_id = realtime.send_direct_message(self.firebase_user_id, question)
            except Exception as e:
                print(f"⚠️ Erreur notification realtime: {e}")
                notif_message_id = None

            # Contextualiser l'agent si agent_instance est fourni
            if agent_instance:
                if is_auth_request:
                    context_msg = f"SYSTEM: Une procédure de validation d'authentification est en cours. Un lien a été envoyé à l'utilisateur : '{question}'. L'utilisateur peut te poser des questions en attendant la validation. Réponds-lui courtoisement en attendant le signal de fin."
                else:
                    context_msg = f"SYSTEM: Une question a été posée à l'utilisateur : '{question}'. En attente de sa réponse."
                
                # Appel à la méthode d'injection de message (si disponible sur l'instance)
                if hasattr(agent_instance, 'add_messages_ai_hu'):
                     agent_instance.add_messages_ai_hu(
                        context_msg,
                        ai_message="J'ai bien reçu le message système.",
                        mode='simple'
                    )

            # --- DÉFINITION DES OUTILS POUR L'INTERACTION UTILISATEUR ---
            # Comme demandé, on inclut UNIQUEMENT l'outil de vision pour le moment
            # afin de traiter les images/PDFs envoyés par l'utilisateur
            
            vision_tool = {
                "name": "vision_document",
                "description": "Analyse visuelle d'un document via IA. Utilisez-le pour analyser un document image ou PDF si l'utilisateur en envoie un.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "ID du fichier Drive"},
                        "question": {"type": "string", "description": "Question sur le document"}
                    },
                    "required": ["file_id", "question"]
                }
            }
            
            # Liste des outils disponibles PENDANT l'attente utilisateur
            interaction_tools = [vision_tool]
            
            # Mapping des fonctions
            interaction_tool_mapping = {
                "vision_document": self.vision_document
            }

            # Utiliser send_message_and_listen en mode texte
            # La fonction retourne un tuple (next_step, instructions, fiscal_year)
            result = self.chat_system.send_message_and_listen(
                subscription_id=None,
                space_code=self.collection_name,
                thread_key=self.job_id,
                report=question,
                format='text',
                message_mode='chats',
                timeout_minutes=300,
                anthropic_instance=agent_instance, # Passer l'agent
                job_id=self.job_id,
                collection_name=self.collection_name,
                antho_tools=interaction_tools,     # Ajout des outils
                tool_mapping=interaction_tool_mapping # Ajout du mapping
            )
            
            # Nettoyage notif
            if notif_message_id:
                try:
                    realtime.delete_direct_message(self.firebase_user_id, notif_message_id)
                except:
                    pass

            # Analyse du résultat (Tuple ou valeur simple)
            if isinstance(result, tuple):
                next_step = result[0]
                instructions = result[1]
                fiscal_year = result[2] if len(result) > 2 else None
                # ✅ Utiliser instructions au lieu de next_step pour le contexte de l'agent
                response = instructions
                print(f"📥 Réponse utilisateur - next_step: {next_step}, instructions: {instructions}, fiscal_year: {fiscal_year}")
            else:
                response = result

            if response:
                return {
                    'success': True,
                    'answer': response,
                    'message': "Réponse reçue"
                }
            else:
                return {
                    'success': False,
                    'answer': None,
                    'message': 'Timeout ou pas de réponse'
                }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'answer': None,
                'message': f"Erreur lors de la communication avec l'utilisateur: {str(e)}"
            }
    
    def file_manager_agent_workflow(self,
                                    manager_instance: Any,
                                    initial_query: str,
                                    tools: List[Dict[str, Any]],
                                    tool_mapping: Dict[str, Any],
                                    size: ModelSize = ModelSize.MEDIUM,
                                    provider: Optional[ModelProvider] = None,
                                    max_tokens: int = 2048,
                                    project_id: str = None,
                                    job_id: str = None,
                                    workflow_step: str = 'file_manager_workflow',
                                    max_turns: int = 7,
                                    raw_output: bool = True,
                                    max_tokens_budget: int = 30000) -> Tuple[bool, str, str, Dict]:
        """
        Workflow intelligent pour le FileManager (boucle interne).

        Args:
            max_tokens_budget (int): Budget maximum de tokens (défaut: 30,000)

        Returns:
            Tuple[bool, str, str, Dict]:
                - Success status (bool)
                - Status code (str): MISSION_COMPLETED / MAX_TURNS_REACHED / ERROR_FATAL
                - Response text (str)
                - Report data (Dict)
        """
        try:
            print(f"🗂️ [FILE_MANAGER_WORKFLOW] Démarrage - Tours max: {max_turns}")
            print(f"💰 [FILE_MANAGER_WORKFLOW] Budget tokens: {max_tokens_budget:,}")

            turn_count = 0
            # ⭐ PREMIER MESSAGE: Intégrer le message initial après le system prompt
            user_input = f"""📋 MISSION INITIALE :
                    {initial_query}

                    ⚠️ BUDGET TOKENS : {max_tokens_budget:,} tokens maximum
                    En cas de dépassement, un résumé sera généré automatiquement pour continuer.

                    Commence maintenant l'analyse de la demande."""

            next_user_input_parts = []

            # Variables pour le rapport final
            files_processed = []
            folders_created = []
            documents_created_by_gapp = []
            errors_encountered = []

            # ⭐ Tracking initial des tokens
            initial_message = user_input
            
            while turn_count < max_turns:
                turn_count += 1
                print(f"\033[95m📁 Tour {turn_count}/{max_turns} - FileManager\033[0m")

                # ═══════════════════════════════════════════════════
                # VÉRIFICATION BUDGET TOKENS
                # ═══════════════════════════════════════════════════
                try:
                    # Récupérer le provider pour calculer les tokens
                    if provider:
                        tokens_before = manager_instance.get_total_context_tokens(provider)
                    else:
                        # Utiliser le provider par défaut si non spécifié
                        tokens_before = manager_instance.get_total_context_tokens(
                            manager_instance.default_provider if hasattr(manager_instance, 'default_provider') else ModelProvider.MOONSHOT_AI
                        )

                    print(f"💰 [TOKENS] Tour {turn_count} - Tokens: {tokens_before:,}/{max_tokens_budget:,}")

                    # Si budget dépassé, générer résumé et réinitialiser
                    if tokens_before >= max_tokens_budget:
                        print(f"⚠️ [TOKENS] Budget atteint ({tokens_before:,} tokens) - Contextualisation en cours...")

                        # Générer un résumé de la conversation
                        summary = self.generate_conversation_summary(
                            job_id=job_id,
                            total_tokens_used=tokens_before,
                            initial_query=initial_message,
                            files_processed=files_processed,
                            folders_created=folders_created,
                            documents_created=documents_created_by_gapp,
                            errors_encountered=errors_encountered
                        )

                        # Réinitialiser le contexte avec le résumé
                        tokens_after_reset = self.reset_context_with_summary(
                            manager_instance=manager_instance,
                            summary=summary,
                            provider=provider
                        )

                        print(f"✅ [TOKENS] Contexte réinitialisé - Avant: {tokens_before:,} → Après: {tokens_after_reset:,}")

                        # Mettre à jour user_input pour inclure le résumé
                        user_input = f"""📋 RÉSUMÉ DES TRAVAUX PRÉCÉDENTS :
                            {summary}

                            📌 MESSAGE INITIAL :
                            {initial_message}

                            Continue maintenant avec la mission en cours."""

                        tokens_before = tokens_after_reset

                except Exception as e:
                    print(f"⚠️ [TOKENS] Erreur calcul tokens: {e}")
                    import traceback
                    traceback.print_exc()

                # ═══════════════════════════════════════════════════
                # APPEL DE L'AGENT
                # ═══════════════════════════════════════════════════
                ia_responses = manager_instance.process_tool_use(
                    content=user_input,
                    tools=tools,
                    tool_mapping=tool_mapping,
                    size=size,
                    provider=provider,
                    max_tokens=max_tokens,
                    raw_output=raw_output
                    )

                # ═══════════════════════════════════════════════════
                # TRACKING DES TOKENS APRÈS CHAQUE OUTPUT
                # ═══════════════════════════════════════════════════
                if project_id and job_id:
                    try:
                        # Capturer les dépenses à chaque output de l'agent
                        manager_instance.load_token_usage_to_db(
                            project_id=project_id,
                            job_id=job_id,
                            workflow_step=f"{workflow_step}_turn_{turn_count}"
                        )
                        print(f"💾 [TRACKING] Tokens sauvegardés pour tour {turn_count}")
                    except Exception as e:
                        print(f"⚠️ [TRACKING] Erreur sauvegarde tokens: {e}")

                print(f"\033[93m📤 Réponse FileManager: {str(ia_responses)[:300]}...\033[0m")
                
                # Normalisation
                if not isinstance(ia_responses, list):
                    ia_responses = [ia_responses] if ia_responses else []
                
                next_user_input_parts = []
                
                # ═══════════════════════════════════════════════════
                # TRAITEMENT DES RÉPONSES
                # ═══════════════════════════════════════════════════
                for response_block in ia_responses:
                    if not isinstance(response_block, dict):
                        next_user_input_parts.append(f"Réponse inattendue: {str(response_block)[:200]}")
                        continue
                    
                    # ───────────────────────────────────────────────
                    # TOOL_OUTPUT
                    # ───────────────────────────────────────────────
                    if "tool_output" in response_block:
                        tool_block = response_block["tool_output"]
                        tool_name = tool_block.get('tool_name', 'UnknownTool')
                        tool_content = tool_block.get('content', '')
                        
                        print(f"  🔧 Outil appelé: {tool_name}")

                        # ═══════════════════════════════════════════════════
                        # GESTION ERREUR INVALID_GRANT (AUTO-GUÉRISON)
                        # ═══════════════════════════════════════════════════
                        is_auth_error = False
                        if isinstance(tool_content, dict) and 'error' in tool_content:
                            error_msg = str(tool_content['error'])
                            if 'invalid_grant' in error_msg or 'Token has been expired' in error_msg:
                                is_auth_error = True
                        elif isinstance(tool_content, str) and ('invalid_grant' in tool_content or 'Token has been expired' in tool_content):
                            is_auth_error = True
                            
                        if is_auth_error:
                            print("⚠️ [AUTH] Erreur invalid_grant détectée - Lancement procédure renouvellement")
                            
                            # 1. Générer l'URL d'auth
                            auth_url = None
                            if hasattr(self, 'dms_system') and hasattr(self.dms_system, 'get_authorization_url'):
                                # Récupérer les infos contextuelles pour le state
                                thread_key = self.job_id
                                space_code = self.collection_name
                                communication_mode = self.communication_mode
                                
                                # ✅ RÉCUPÉRATION DU CHAT_ID pour l'intégrer au state OAuth
                                chat_id = None
                                if hasattr(self, 'chat_system') and self.chat_system:
                                    if hasattr(self.chat_system, 'telegram_instance') and self.chat_system.telegram_instance:
                                        telegram_users_mapping = getattr(self.chat_system, 'telegram_users_mapping', {})
                                        chat_id = self.chat_system.telegram_instance.get_primary_chat_id(telegram_users_mapping)
                                        print(f"🔐 [AUTH] Chat ID récupéré pour le state OAuth: {chat_id}")
                                
                                auth_url = self.dms_system.get_authorization_url(
                                    user_id=self.firebase_user_id,
                                    job_id=self.job_id,
                                    source='filemanager_agent',
                                    communication_mode=communication_mode,
                                    thread_key=thread_key, 
                                    space_code=space_code,
                                    mandate_path=self.mandate_path, # INDISPENSABLE pour le webhook
                                    chat_id=chat_id  # ✅ AJOUT DU CHAT_ID dans le state
                                )
                            
                            if auth_url:
                                # 2. Demander à l'utilisateur de cliquer
                                auth_msg = f"🔒 **Action requise** : Mon accès Google Drive a expiré.\n\nMerci de cliquer sur ce lien pour le renouveler :\n{auth_url}\n\nUne fois validé, je reprendrai automatiquement."
                                
                                print(f"🔐 [AUTH] Envoi demande auth à l'utilisateur ({communication_mode})")
                                
                                # Utiliser ASK_USER pour envoyer et attendre
                                # Le backend enverra "TERMINATE" (ou autre signal) via le callback pour débloquer
                                # Passer self comme anthropic_instance pour permettre la conversation pendant l'attente
                                auth_response = self.ASK_USER(auth_msg, is_auth_request=True)
                                
                                # 3. Vérifier le retour
                                if auth_response and isinstance(auth_response, dict) and auth_response.get('success'):
                                    answer = auth_response.get('answer', '')
                                    print(f"✅ [AUTH] Retour reçu : {answer}")
                                    
                                    # Recharger l'instance DMS si nécessaire (souvent géré par le prochain appel API qui relit le token, 
                                    # mais on peut forcer un refresh si le client le supporte)
                                    # Pour l'instant, on assume que le prochain appel utilisera les nouveaux tokens stockés.
                                    
                                    # Modifier la réponse pour l'IA
                                    tool_content = "Authentification renouvelée avec succès. Vous pouvez réessayer l'opération."
                                    next_user_input_parts.append("Système: L'authentification a été rétablie. Veuillez réessayer l'action précédente.")
                                    continue # Passer au prochain item, l'IA va recevoir l'info de réessayer
                                else:
                                    print("❌ [AUTH] Échec ou timeout de l'attente auth")
                                    tool_content = "Échec du renouvellement d'authentification (Timeout ou refus)."
                            else:
                                print("❌ [AUTH] Impossible de générer l'URL d'auth")
                        
                        # ▼▼▼ TERMINATE_FILE_MANAGEMENT ▼▼▼
                        if tool_name == 'TERMINATE_FILE_MANAGEMENT':
                            print(f"[FILE_MANAGER_WORKFLOW] ✓ TERMINATE_FILE_MANAGEMENT")

                            if isinstance(tool_content, dict):
                                operation_status = tool_content.get('operation_status', 'SUCCESS')
                                conclusion = tool_content.get('conclusion', '')
                                files_processed = tool_content.get('files_processed', files_processed)
                                folders_created = tool_content.get('folders_created', folders_created)
                                documents_created = tool_content.get('documents_created', documents_created_by_gapp)
                                errors_encountered = tool_content.get('errors_encountered', errors_encountered)
                            else:
                                operation_status = 'SUCCESS'
                                conclusion = str(tool_content)

                            # Préparer le rapport final
                            report_data = {
                                'job_id': job_id,
                                'operation_status': operation_status,
                                'files_processed': files_processed,
                                'folders_created': folders_created,
                                'documents_created': documents_created,
                                'errors_encountered': errors_encountered,
                                'conclusion': conclusion
                            }

                            # ═══════════════════════════════════════════════════
                            # FACTURATION SELON LE STATUT
                            # ═══════════════════════════════════════════════════
                            if project_id and job_id:
                                try:
                                    # Récupérer le FirebaseService
                                    firebase_service = FireBaseManagement(user_id=self.firebase_user_id)

                                    # Facturation uniquement en cas de succès (sans problème technique)
                                    if operation_status == 'SUCCESS':
                                        print(f"💳 [BILLING] Facturation du job (succès)")
                                        firebase_service.finalize_job_billing(
                                            user_id=self.firebase_user_id,
                                            collection_name=project_id,
                                            job_id=job_id,
                                            file_name=f"file_manager_{job_id}",
                                            function_name='file_manager_agent',
                                            mandate_path=self.mandate_path if hasattr(self, 'mandate_path') else None
                                        )
                                        print(f"✅ [BILLING] Job facturé avec succès")
                                    else:
                                        # Pas de facturation en cas d'erreur technique
                                        print(f"⚠️ [BILLING] Pas de facturation (statut: {operation_status})")
                                        firebase_service.job_failure_no_billing(
                                            user_id=self.firebase_user_id,
                                            collection_name=project_id,
                                            job_id=job_id,
                                            file_name=f"file_manager_{job_id}",
                                            function_name='file_manager_agent'
                                        )
                                        print(f"✅ [BILLING] Tokens enregistrés sans facturation")

                                except Exception as e:
                                    print(f"❌ [BILLING] Erreur lors de la facturation: {e}")
                                    import traceback
                                    traceback.print_exc()

                            # Envoyer le rapport au tracking endpoint
                            self._send_tracking_report(report_data, report_type='completion')

                            return True, "MISSION_COMPLETED", conclusion, report_data
                        
                        # ▼▼▼ ASK_USER ▼▼▼
                        elif tool_name == 'ASK_USER':
                            print(f"  ❓ Question posée à l'utilisateur")
                            
                            if isinstance(tool_content, dict):
                                question = tool_content.get('question', '')
                            else:
                                question = str(tool_content)
                            
                            # Utiliser la méthode ASK_USER qui gère l'attente de la réponse utilisateur
                            ask_result = self.ASK_USER(question, agent_instance=self)
                            
                            if ask_result.get('success'):
                                response_from_user = ask_result.get('answer', 'Pas de réponse')
                                next_user_input_parts.append(
                                    f"Réponse de l'utilisateur à votre question: {response_from_user}"
                                )
                            else:
                                error_msg = ask_result.get('message', 'Erreur inconnue')
                                next_user_input_parts.append(
                                    f"⚠️ Impossible d'obtenir une réponse de l'utilisateur: {error_msg}"
                                )
                            
                            # Envoyer aussi un tracking pour info (sans attendre de réponse)
                            self._send_tracking_report(
                                {'question': question, 'job_id': job_id, 'answer': response_from_user if ask_result.get('success') else None},
                                report_type='question'
                            )
                        
                        # ▼▼▼ get_departement_prompt ▼▼▼
                        elif tool_name == 'get_departement_prompt':
                            print(f"  📋 Chargement prompt département")
                            
                            if isinstance(tool_content, dict):
                                department = tool_content.get('department', '')
                            else:
                                department = str(tool_content)
                            
                            result = self.get_departement_prompt(department)
                            
                            if result.get('success'):
                                next_user_input_parts.append(
                                    f"Prompt département {department}: {result.get('prompt', '')[:500]}"
                                )
                            else:
                                next_user_input_parts.append(
                                    f"Erreur chargement prompt {department}: {result.get('message', '')}"
                                )
                        
                        # ▼▼▼ vision_document ▼▼▼
                        elif tool_name == 'vision_document':
                            print(f"  👁️ Analyse visuelle de document")
                            
                            if isinstance(tool_content, dict):
                                file_id = tool_content.get('file_id', '')
                                question = tool_content.get('question', '')
                            else:
                                # Parser si c'est une string au format "file_id: xxx, question: xxx"
                                file_id = ''
                                question = str(tool_content)
                            
                            result = self.vision_document(file_id, question)
                            
                            if result.get('success'):
                                next_user_input_parts.append(
                                    f"Analyse visuelle: {result.get('response', '')[:500]}"
                                )
                            else:
                                next_user_input_parts.append(
                                    f"Erreur analyse visuelle: {result.get('error', '')}"
                                )
                        
                        # ▼▼▼ create_fiscal_year_structure ▼▼▼
                        elif tool_name == 'create_fiscal_year_structure':
                            print(f"  📅 Création structure année fiscale")
                            
                            if isinstance(tool_content, dict):
                                fiscal_year = tool_content.get('fiscal_year', 0)
                            else:
                                try:
                                    fiscal_year = int(tool_content)
                                except:
                                    fiscal_year = 0
                            
                            result = self.create_fiscal_year_structure(fiscal_year)
                            
                            if result.get('success'):
                                # Ajouter les dossiers créés au tracking
                                if 'folders_created' in result:
                                    folders_created.extend(result['folders_created'])
                                
                                next_user_input_parts.append(
                                    f"Structure {fiscal_year} créée: {result.get('message', '')}"
                                )
                            else:
                                errors_encountered.append(f"create_fiscal_year_structure: {result.get('error', '')}")
                                next_user_input_parts.append(
                                    f"Erreur création structure: {result.get('error', '')}"
                                )
                        
                        # ▼▼▼ CALL_DRIVE_AGENT ▼▼▼
                        elif tool_name == 'CALL_DRIVE_AGENT':
                            print(f"  🗂️ Appel à DriveAgent")
                            
                            if isinstance(tool_content, dict):
                                instructions = tool_content.get('instructions', '')
                            else:
                                instructions = str(tool_content)
                            
                            # Appeler le workflow du DriveAgent
                            drive_success, drive_status, drive_response, drive_report = self.drive_agent.drive_agent_workflow(
                                manager_instance=self.drive_agent,
                                initial_query=instructions,
                                size=ModelSize.MEDIUM,
                                project_id=project_id,
                                job_id=job_id,
                                workflow_step=f"{workflow_step}_drive",
                                max_turns=5
                            )
                            
                            # Collecter les fichiers traités et dossiers créés
                            if drive_report:
                                if 'files_processed' in drive_report:
                                    files_processed.extend(drive_report['files_processed'])
                                if 'folders_created' in drive_report:
                                    folders_created.extend(drive_report['folders_created'])
                                if 'errors_encountered' in drive_report:
                                    errors_encountered.extend(drive_report['errors_encountered'])
                            
                            # Construire un message clair incluant le statut et les détails
                            status_msg = f"\n📊 **RAPPORT DRIVEAGENT** :\n"
                            status_msg += f"- Statut: {drive_status}\n"
                            status_msg += f"- Succès: {'OUI' if drive_success else 'NON'}\n"
                            
                            if drive_report:
                                operation_status = drive_report.get('operation_status', 'UNKNOWN')
                                status_msg += f"- Opération: {operation_status}\n"
                                status_msg += f"- Fichiers traités: {len(drive_report.get('files_processed', []))}\n"
                                status_msg += f"- Dossiers créés: {len(drive_report.get('folders_created', []))}\n"
                                
                                if drive_report.get('errors_encountered'):
                                    status_msg += f"- Erreurs: {', '.join(drive_report['errors_encountered'][:3])}\n"
                            
                            status_msg += f"\n📝 Détails: {drive_response[:400]}\n"
                            
                            # 🚨 Si le DriveAgent a échoué, ajouter un avertissement clair
                            if not drive_success or drive_status in ["MAX_TURNS_REACHED", "ERROR_FATAL"]:
                                status_msg += "\n⚠️ **ATTENTION** : Le DriveAgent N'A PAS TERMINÉ avec succès.\n"
                                status_msg += "Vous DEVEZ signaler cet échec dans votre rapport final.\n"
                                status_msg += "NE PAS prétendre que l'opération a réussi !\n"
                            
                            next_user_input_parts.append(status_msg)
                        
                        # ▼▼▼ CALL_GAPP_AGENT ▼▼▼
                        elif tool_name == 'CALL_GAPP_AGENT':
                            print(f"  📄 Appel à GoogleAppsAgent")
                            
                            if isinstance(tool_content, dict):
                                document_type = tool_content.get('document_type', 'doc')
                                action = tool_content.get('action', 'create')
                                context = tool_content.get('context', {})
                                instructions = tool_content.get('instructions', '')
                            else:
                                document_type = 'doc'
                                action = 'create'
                                context = {}
                                instructions = str(tool_content)
                            
                            # Appeler le workflow du GoogleAppsAgent
                            gapp_success, gapp_status, gapp_response, gapp_report = self.gapp_agent.gapp_agent_workflow(
                                manager_instance=self.gapp_agent,
                                initial_query=f"Action: {action} un {document_type}. Instructions: {instructions}",
                                document_type=document_type,
                                action=action,
                                context=context,
                                size=ModelSize.MEDIUM,
                                project_id=project_id,
                                job_id=job_id,
                                workflow_step=f"{workflow_step}_gapp",
                                max_turns=5
                            )
                            
                            # Collecter les documents créés
                            if gapp_report and 'documents_created' in gapp_report:
                                documents_created_by_gapp.extend(gapp_report['documents_created'])
                            
                            next_user_input_parts.append(
                                f"Résultat de l'agent Google Apps ({gapp_status}): {gapp_response[:500]}"
                            )
                        
                        # Autres outils de gestion de fichiers (BAS NIVEAU - NE DEVRAIENT PLUS ÊTRE APPELÉS DIRECTEMENT)
                        elif tool_name in ['search_file_in_dms', 'create_folder_in_dms', 'move_file_in_dms', 
                                          'rename_file_in_dms', 'delete_file_in_dms', 'get_file_metadata']:
                            print(f"  📂 Opération DMS: {tool_name}")
                            
                            # Tracer l'opération
                            if isinstance(tool_content, dict):
                                if tool_content.get('success'):
                                    if tool_name == 'create_folder_in_dms':
                                        folders_created.append(tool_content.get('folder_id', ''))
                                    else:
                                        files_processed.append(tool_content.get('file_id', ''))
                                else:
                                    errors_encountered.append(f"{tool_name}: {tool_content.get('error', 'Unknown error')}")
                            
                            next_user_input_parts.append(
                                f"Résultat {tool_name}: {str(tool_content)[:500]}"
                            )
                        
                        else:
                            next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                    
                    # ───────────────────────────────────────────────
                    # TEXT_OUTPUT
                    # ───────────────────────────────────────────────
                    elif "text_output" in response_block:
                        text_block = response_block["text_output"]
                        extracted_text = "Pas de texte"
                        
                        if isinstance(text_block, dict) and "content" in text_block:
                            content = text_block["content"]
                            if isinstance(content, dict):
                                extracted_text = content.get('answer_text', str(content))
                            else:
                                extracted_text = str(content)
                        elif isinstance(text_block, str):
                            extracted_text = text_block
                        
                        print(f"  💬 Texte: {extracted_text[:200]}...")
                        next_user_input_parts.append(f"Contexte: {extracted_text[:300]}")
                
                # Préparer input pour le prochain tour
                if next_user_input_parts:
                    user_input = "\n".join(next_user_input_parts)
                else:
                    print("[FILE_MANAGER_WORKFLOW] Aucune réponse utilisable")
                    return False, "NO_IA_ACTION", "L'IA n'a pas fourni de réponse claire.", {}
            
            # ═══════════════════════════════════════════════════
            # MAX TOURS ATTEINT
            # ═══════════════════════════════════════════════════
            print(f"[FILE_MANAGER_WORKFLOW] Maximum de {max_turns} tours atteint")

            summary = f"Maximum de {max_turns} tours atteint. Dernier état: {user_input[:500]}"

            report_data = {
                'job_id': job_id,
                'operation_status': 'INCOMPLETE',
                'files_processed': files_processed,
                'folders_created': folders_created,
                'documents_created': documents_created_by_gapp,
                'errors_encountered': errors_encountered,
                'conclusion': summary
                }

            # ═══════════════════════════════════════════════════
            # PAS DE FACTURATION (TRAVAIL INCOMPLET)
            # ═══════════════════════════════════════════════════
            if project_id and job_id:
                try:
                    firebase_service = FireBaseManagement(user_id=self.firebase_user_id)
                    print(f"⚠️ [BILLING] Pas de facturation (tours maximum atteints)")
                    firebase_service.job_failure_no_billing(
                        user_id=self.firebase_user_id,
                        collection_name=project_id,
                        job_id=job_id,
                        file_name=f"file_manager_{job_id}",
                        function_name='file_manager_agent'
                    )
                    print(f"✅ [BILLING] Tokens enregistrés sans facturation")
                except Exception as e:
                    print(f"❌ [BILLING] Erreur: {e}")

            return False, "MAX_TURNS_REACHED", summary, report_data
            
        except Exception as e:
            print(f"[FILE_MANAGER_WORKFLOW] ERREUR FATALE: {e}")
            import traceback
            traceback.print_exc()

            error_report = {
                'job_id': job_id,
                'operation_status': 'ERROR',
                'conclusion': f"Erreur fatale: {str(e)}"
            }

            # ═══════════════════════════════════════════════════
            # PAS DE FACTURATION (ERREUR TECHNIQUE)
            # ═══════════════════════════════════════════════════
            if project_id and job_id:
                try:
                    firebase_service = FireBaseManagement(user_id=self.firebase_user_id)
                    print(f"❌ [BILLING] Pas de facturation (erreur technique)")
                    firebase_service.job_failure_no_billing(
                        user_id=self.firebase_user_id,
                        collection_name=project_id,
                        job_id=job_id,
                        file_name=f"file_manager_{job_id}",
                        function_name='file_manager_agent'
                    )
                    print(f"✅ [BILLING] Tokens enregistrés sans facturation")
                except Exception as e2:
                    print(f"❌ [BILLING] Erreur: {e2}")

            return False, "ERROR_FATAL", f"Erreur: {str(e)}", error_report

    def generate_conversation_summary(self,
                                     job_id: str,
                                     total_tokens_used: int,
                                     initial_query: str,
                                     files_processed: List,
                                     folders_created: List,
                                     documents_created: List,
                                     errors_encountered: List) -> str:
        """
        Génère un résumé compressé de la conversation et des travaux effectués.

        Cette méthode est appelée quand le budget de tokens est atteint (30K)
        pour compresser l'historique et permettre de continuer le travail.

        Args:
            job_id: ID du job en cours
            total_tokens_used: Nombre total de tokens utilisés
            initial_query: Message initial de l'utilisateur
            files_processed: Liste des fichiers traités
            folders_created: Liste des dossiers créés
            documents_created: Liste des documents créés
            errors_encountered: Liste des erreurs rencontrées

        Returns:
            Résumé compressé de la conversation (max 500 tokens)
        """
        print(f"📝 [SUMMARY] Génération résumé - job={job_id}, tokens={total_tokens_used:,}")

        # Construire le contexte pour le résumé
        summary_context = f"""
                ═══════════════════════════════════════════════════
                📋 RÉSUMÉ DES TRAVAUX EFFECTUÉS (Budget: {total_tokens_used:,} tokens atteint)
                ═══════════════════════════════════════════════════

                🎯 DEMANDE INITIALE :
                {initial_query[:300]}...

                📊 PROGRESSION :
                - Fichiers traités : {len(files_processed)}
                - Dossiers créés : {len(folders_created)}
                - Documents créés : {len(documents_created)}
                - Erreurs rencontrées : {len(errors_encountered)}

                📁 FICHIERS TRAITÉS :
                {chr(10).join([f"  - {f}" for f in files_processed[:5]])}
                {f"  ... et {len(files_processed) - 5} autres" if len(files_processed) > 5 else ""}

                📂 DOSSIERS CRÉÉS :
                {chr(10).join([f"  - {f}" for f in folders_created[:5]])}
                {f"  ... et {len(folders_created) - 5} autres" if len(folders_created) > 5 else ""}

                📄 DOCUMENTS CRÉÉS :
                {chr(10).join([f"  - {doc.get('type', 'N/A')}: {doc.get('url', 'N/A')}" for doc in documents_created[:3]])}
                {f"  ... et {len(documents_created) - 3} autres" if len(documents_created) > 3 else ""}

                ⚠️ ERREURS :
                {chr(10).join([f"  - {err}" for err in errors_encountered[:3]])}
                {f"  ... et {len(errors_encountered) - 3} autres" if len(errors_encountered) > 3 else ""}

                ═══════════════════════════════════════════════════

                ✅ Ce résumé remplace l'historique complet pour économiser des tokens.
                Continue le travail en tenant compte de ces informations.
                """

        print(f"✅ [SUMMARY] Résumé généré ({len(summary_context)} caractères)")
        return summary_context

    def reset_context_with_summary(self,
                                   manager_instance: Any,
                                   summary: str,
                                   provider: Optional[ModelProvider] = None) -> int:
        """
        Réinitialise le contexte avec un résumé intégré au system prompt.

        Cette méthode :
        1. Ajoute le résumé au system prompt de base
        2. Vide l'historique du chat
        3. Calcule et retourne le nombre de tokens du nouveau contexte

        Args:
            manager_instance: Instance de l'agent (BaseAIAgent)
            summary: Résumé de la conversation à intégrer
            provider: Provider LLM à utiliser pour le calcul de tokens

        Returns:
            Nombre de tokens du nouveau contexte (system prompt + résumé)
        """
        print(f"🔄 [RESET] Réinitialisation du contexte avec résumé")

        try:
            # Récupérer l'instance provider
            if provider:
                provider_instance = manager_instance.get_provider_instance(provider)
            else:
                default_prov = manager_instance.default_provider if hasattr(manager_instance, 'default_provider') else ModelProvider.MOONSHOT_AI
                provider_instance = manager_instance.get_provider_instance(default_prov)

            # Sauvegarder le system prompt de base (si pas déjà sauvegardé)
            if not hasattr(self, '_base_system_prompt'):
                if hasattr(provider_instance, 'system_prompt'):
                    self._base_system_prompt = provider_instance.system_prompt
                else:
                    self._base_system_prompt = ""

            # Créer le nouveau system prompt avec résumé intégré
            new_system_prompt = f"""{self._base_system_prompt}

                    {summary}

                    Continue la conversation en tenant compte de ce contexte historique.
                    """

            # Mettre à jour le system prompt
            if hasattr(provider_instance, 'update_system_prompt'):
                provider_instance.update_system_prompt(new_system_prompt)
            elif hasattr(provider_instance, 'system_prompt'):
                provider_instance.system_prompt = new_system_prompt

            # Vider l'historique du chat
            if hasattr(manager_instance, 'clear_chat_history'):
                manager_instance.clear_chat_history()
            elif hasattr(manager_instance, 'chat_history'):
                manager_instance.chat_history = []

            # Calculer les tokens du nouveau contexte
            tokens_after_reset = 0
            try:
                if provider:
                    tokens_after_reset = manager_instance.get_total_context_tokens(provider)
                else:
                    tokens_after_reset = manager_instance.get_total_context_tokens(
                        manager_instance.default_provider if hasattr(manager_instance, 'default_provider') else ModelProvider.MOONSHOT_AI
                    )
            except:
                # Estimation approximative si get_total_context_tokens échoue
                tokens_after_reset = len(new_system_prompt) // 4

            print(f"✅ [RESET] Contexte réinitialisé - Nouveau contexte: {tokens_after_reset:,} tokens")

            return tokens_after_reset

        except Exception as e:
            print(f"❌ [RESET] Erreur lors de la réinitialisation: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def _send_tracking_report(self, data: Dict, report_type: str = 'completion') -> Optional[str]:
        """
        Envoie un rapport ou une question à l'endpoint de tracking.
        
        Args:
            data (Dict): Données à envoyer
            report_type (str): 'completion' pour rapport final, 'question' pour poser une question
            
        Returns:
            Optional[str]: Réponse de l'endpoint (si question), None sinon
        """
        try:
            payload = {
                **data,
                'report_type': report_type,
                'mandate_path': self.mandate_path,
                'timestamp': datetime.now().isoformat()
            }
            
            response = requests.post(
                self.tracking_endpoint_url,
                json=payload,
                timeout=30 if report_type == 'question' else 10
            )
            
            if response.status_code == 200:
                result = response.json()
                
                if report_type == 'question':
                    # Récupérer la réponse de l'utilisateur
                    return result.get('answer', 'Pas de réponse')
                else:
                    print(f"✅ Rapport envoyé avec succès à {self.tracking_endpoint_url}")
                    return None
            else:
                print(f"⚠️ Erreur lors de l'envoi du rapport: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Erreur lors de l'envoi du rapport: {e}")
            return None


# ============================================================================
# GOOGLE APPS AGENT - Agent spécialisé pour Google Docs/Sheets/Slides
# ============================================================================

class GoogleAppsAgent(BaseAIAgent):
    """
    Agent spécialisé dans la création et modification de contenu Google Apps.
    Gère Google Docs, Sheets et Slides.
    """
    
    def __init__(self,
                 firebase_user_id=None,
                 job_id=None,
                 collection_name=None,
                 parent_agent=None,
                 **kwargs):
        """
        Initialise l'agent GoogleApps.
        
        Args:
            firebase_user_id (str): ID de l'utilisateur Firebase
            job_id (str): ID du job en cours
            collection_name (str): Nom de la collection
            parent_agent (FileManagerPinnokio): Référence à l'agent parent
        """
        super().__init__(
            collection_name=collection_name,
            firebase_user_id=firebase_user_id,
            job_id=job_id,
            function_name='google_apps_agent'
        )
        
        self.parent_agent = parent_agent
        
        # Enregistrer le provider LLM pour GoogleAppsAgent (Kimi K2.5)
        moonshot_provider = NEW_MOONSHOT_AIAgent()
        self.register_provider(ModelProvider.MOONSHOT_AI, moonshot_provider)
        
        # Initialiser le system prompt
        self._initialize_gapp_prompt()
        
        print(f"✅ GoogleAppsAgent initialisé")
    
    def _initialize_gapp_prompt(self):
        """Initialise le system prompt de l'agent GoogleApps"""
        prompt = f"""Vous êtes GoogleAppsAgent, spécialisé dans la création et modification de contenu Google Apps.
                Vous travaillez sous les ordres de l'Agent Principal (FileManager).

                🎯 MISSION :
                Créer et modifier du contenu structuré dans Google Apps (Docs, Sheets, Slides) selon les instructions reçues.
                Vous gérez le layout, le formatage et l'organisation du contenu.

                📋 OUTILS DISPONIBLES :

                1. **create_google_doc** : Crée un nouveau Google Doc avec contenu
                2. **update_doc_content** : Modifie le contenu d'un Google Doc existant
                3. **create_google_sheet** : Crée une nouvelle Google Sheet avec données
                4. **update_sheet_data** : Modifie les données d'une Google Sheet
                5. **create_google_slide** : Crée une nouvelle présentation Google Slides
                6. **format_document** : Applique un formatage/layout à un document
                7. **read_document_content** : Lit le contenu d'un document Google Apps
                8. **vision_document** : Analyse visuelle d'un document via IA
                👁️ Utilisez-le pour analyser visuellement un document source avant de créer un résumé
                Ex: vision_document(file_id="abc123", question="Quels sont les points clés?")
                💡 Très utile pour créer des résumés ou rapports à partir de documents sources
                9. **TERMINATE_GAPP_OPERATION** : Clôture l'opération Google Apps

                ⚠️ INSTRUCTIONS IMPORTANTES :

                1. **Emplacement OBLIGATOIRE** : 
                - Vérifiez TOUJOURS que vous avez l'EMPLACEMENT de création
                - Si l'emplacement est manquant ou ambigu, produisez un text_output pour demander
                - NE CRÉEZ JAMAIS sans savoir où placer le document

                2. **Communication avec l'Agent Principal** :
                - Si vous manquez d'informations, produisez un text_output avec votre question
                - L'Agent Principal lira votre question et vous répondra
                - Votre historique est CONSERVÉ jusqu'au TERMINATE
                - Ne flush PAS votre historique tant que TERMINATE n'est pas appelé

                3. **Structuration** : Organisez le contenu de manière logique et professionnelle
                4. **Formatage** : Appliquez un formatage approprié (titres, listes, tableaux)
                5. **Vérification** : Utilisez read_document_content pour vérifier avant de terminer

                📊 RAPPORT DE SORTIE (via TERMINATE_GAPP_OPERATION) :

                {{
                "operation_type": "CREATE | UPDATE | FORMAT",
                "document_type": "doc | sheet | slide",
                "documents_created": [
                    {{"type": "doc", "id": "...", "url": "https://..."}}
                ],
                "operation_status": "SUCCESS | FAILURE",
                "errors": ["liste des erreurs"],
                "summary": "Résumé de l'opération"
                }}

                🔄 WORKFLOW RECOMMANDÉ :

                1. Analyser la demande (type, contenu, EMPLACEMENT)
                2. Si emplacement manquant → text_output avec question
                3. Créer ou localiser le document
                4. Structurer et formater le contenu
                5. Vérifier le résultat
                6. Conclure avec TERMINATE_GAPP_OPERATION

                💬 EXEMPLE DE COMMUNICATION :
                Si l'Agent Principal dit "Crée un rapport mensuel", vous devez demander:
                → text_output: "Où souhaitez-vous que je crée ce rapport? Quel dossier précisément?"

                Commencez l'exécution de votre tâche !
                """
        self.update_system_prompt(prompt)
    
    def vision_document(self, file_id: str, question: str) -> Dict[str, Any]:
        """
        Analyse visuelle d'un document via Vision AI.
        Particulièrement utile pour analyser des documents sources avant de créer des résumés.
        
        Args:
            file_id: ID du fichier dans Google Drive
            question: Question à poser sur le document
        
        Returns:
            Dict avec success, analysis et message
        """
        try:
            # Utiliser l'outil de vision du parent agent si disponible
            if self.parent_agent and hasattr(self.parent_agent, 'vision_document'):
                return self.parent_agent.vision_document(file_id, question)
            else:
                # Fallback: créer notre propre agent vision (Kimi K2.5)
                from ...llm.klk_agents import NEW_MOONSHOT_AIAgent, ModelSize
                vision_agent = NEW_MOONSHOT_AIAgent(
                    collection_name=self.collection_name,
                    job_id=self.job_id
                )

                print(f"👁️ [GoogleAppsAgent] Analyse visuelle du document {file_id}...")
                
                vision_prompt = f"""
                Analysez ce document et répondez à la question suivante:
                
                {question}
                
                Soyez précis et concis dans votre réponse.
                """
                
                analysis_result = vision_agent.process_vision(
                    text=vision_prompt,
                    size=ModelSize.MEDIUM,
                    file_ids=[file_id]
                )
                
                # Enregistrer les tokens
                if hasattr(self, 'token_manager'):
                    vision_agent.load_token_usage_to_db(
                        project_id=self.collection_name,
                        job_id=self.job_id,
                        workflow_step='gapp_vision_document'
                    )
                
                return {
                    'success': True,
                    'analysis': analysis_result,
                    'message': f'Analyse visuelle complétée pour le document {file_id}'
                }
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'analysis': None,
                'message': f"Erreur lors de l'analyse visuelle: {str(e)}"
            }
    
    def gapp_agent_workflow(self,
                           manager_instance: Any,
                           initial_query: str,
                           document_type: str,
                           action: str,
                           context: Dict,
                           tools: Optional[List[Dict[str, Any]]] = None,
                           tool_mapping: Optional[Dict[str, Any]] = None,
                           size: ModelSize = ModelSize.MEDIUM,
                           provider: Optional[ModelProvider] = None,
                           max_tokens: int = 1024,
                           project_id: str = None,
                           job_id: str = None,
                           workflow_step: str = 'gapp_workflow',
                           max_turns: int = 5,
                           raw_output: bool = True) -> Tuple[bool, str, str, Dict]:
        """
        Workflow intelligent pour GoogleAppsAgent (boucle interne).
        
        Args:
            document_type (str): Type de document ('doc', 'sheet', 'slide')
            action (str): Action à effectuer ('create', 'update', 'format')
            context (Dict): Contexte et données pour l'opération
            
        Returns:
            Tuple[bool, str, str, Dict]:
                - Success status (bool)
                - Status code (str)
                - Response text (str)
                - Report data (Dict)
        """
        try:
            print(f"📄 [GAPP_WORKFLOW] Démarrage - Tours max: {max_turns}")
            print(f"   Type: {document_type}, Action: {action}")
            
            # Si tools/tool_mapping non fournis, utiliser les outils par défaut
            if tools is None:
                tools = self._get_default_gapp_tools()
            if tool_mapping is None:
                tool_mapping = self._get_default_gapp_tool_mapping()
            
            turn_count = 0
            user_input = initial_query
            next_user_input_parts = []
            
            # Variables pour le rapport
            documents_created = []
            errors = []
            
            while turn_count < max_turns:
                turn_count += 1
                print(f"\033[96m📝 Tour {turn_count}/{max_turns} - GoogleApps\033[0m")
                
                # Appel de l'agent
                ia_responses = manager_instance.process_tool_use(
                    content=user_input,
                    tools=tools,
                    tool_mapping=tool_mapping,
                    size=size,
                    provider=provider,
                    max_tokens=max_tokens,
                    raw_output=raw_output
                )
                
                # Tracking
                if project_id and job_id:
                    manager_instance.load_token_usage_to_db(
                        project_id=project_id,
                        job_id=job_id,
                        workflow_step=f"{workflow_step}_turn_{turn_count}"
                    )
                
                # Normalisation
                if not isinstance(ia_responses, list):
                    ia_responses = [ia_responses] if ia_responses else []
                
                next_user_input_parts = []
                
                # Traitement des réponses
                for response_block in ia_responses:
                    if not isinstance(response_block, dict):
                        continue
                    
                    if "tool_output" in response_block:
                        tool_block = response_block["tool_output"]
                        tool_name = tool_block.get('tool_name', 'UnknownTool')
                        tool_content = tool_block.get('content', '')
                        
                        print(f"  🔧 Outil GoogleApps: {tool_name}")
                        
                        # TERMINATE_GAPP_OPERATION
                        if tool_name == 'TERMINATE_GAPP_OPERATION':
                            print(f"[GAPP_WORKFLOW] ✓ TERMINATE_GAPP_OPERATION")
                            
                            if isinstance(tool_content, dict):
                                operation_status = tool_content.get('operation_status', 'SUCCESS')
                                summary = tool_content.get('summary', '')
                                documents_created = tool_content.get('documents_created', documents_created)
                            else:
                                operation_status = 'SUCCESS'
                                summary = str(tool_content)
                            
                            report_data = {
                                'operation_type': action,
                                'document_type': document_type,
                                'documents_created': documents_created,
                                'operation_status': operation_status,
                                'errors': errors,
                                'summary': summary
                            }
                            
                            # Flush chat history UNIQUEMENT ici (terminaison)
                            self.flush_chat_history()
                            
                            return True, "OPERATION_COMPLETED", summary, report_data
                        
                        # Autres outils Google Apps
                        elif tool_name in ['create_google_doc', 'create_google_sheet', 'create_google_slide']:
                            if isinstance(tool_content, dict) and tool_content.get('success'):
                                documents_created.append({
                                    'type': document_type,
                                    'id': tool_content.get('document_id'),
                                    'url': tool_content.get('document_url')
                                })
                            else:
                                errors.append(f"{tool_name}: {tool_content.get('error', 'Unknown error')}")
                            
                            next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                        
                        else:
                            next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                    
                    elif "text_output" in response_block:
                        text_block = response_block["text_output"]
                        if isinstance(text_block, dict) and "content" in text_block:
                            content = text_block["content"]
                            extracted_text = content.get('answer_text', str(content)) if isinstance(content, dict) else str(content)
                        elif isinstance(text_block, str):
                            extracted_text = text_block
                        else:
                            extracted_text = str(text_block)
                        
                        print(f"  💬 Question à l'Agent Principal: {extracted_text[:200]}...")
                        
                        # TEXT_OUTPUT = question/clarification pour l'agent principal
                        # PAS de flush_chat_history ici ! L'historique est conservé
                        return False, "WAITING_INPUT", extracted_text, {
                            'operation_type': action,
                            'document_type': document_type,
                            'documents_created': documents_created,
                            'errors': errors
                        }
                
                if next_user_input_parts:
                    user_input = "\n".join(next_user_input_parts)
                else:
                    return False, "NO_IA_ACTION", "L'agent GoogleApps n'a pas fourni de réponse.", {}
            
            # Max tours atteint
            print(f"[GAPP_WORKFLOW] Maximum de {max_turns} tours atteint")
            
            report_data = {
                'operation_type': action,
                'document_type': document_type,
                'documents_created': documents_created,
                'operation_status': 'INCOMPLETE',
                'errors': errors,
                'summary': f"Max tours atteint. Documents créés: {len(documents_created)}"
            }
            
            # Flush car terminé (même si incomplet)
            self.flush_chat_history()
            
            return False, "MAX_TURNS_REACHED", f"Max tours atteint", report_data
            
        except Exception as e:
            print(f"[GAPP_WORKFLOW] ERREUR FATALE: {e}")
            traceback.print_exc()
            
            error_report = {
                'operation_type': action,
                'document_type': document_type,
                'operation_status': 'ERROR',
                'summary': f"Erreur: {str(e)}"
            }
            
            # Flush car terminé (en erreur)
            self.flush_chat_history()
            
            return False, "ERROR_FATAL", f"Erreur: {str(e)}", error_report
    
    def _get_default_gapp_tools(self) -> List[Dict[str, Any]]:
        """Retourne les outils par défaut de GoogleAppsAgent"""
        return [
            {
                "name": "create_google_doc",
                "description": "Crée un nouveau Google Doc avec le contenu spécifié",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Titre du document"},
                        "content": {"type": "string", "description": "Contenu du document"},
                        "parent_folder_id": {"type": "string", "description": "ID du dossier parent (optionnel)"}
                    },
                    "required": ["title", "content"]
                }
            },
            {
                "name": "update_doc_content",
                "description": "Met à jour le contenu d'un Google Doc existant",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "document_id": {"type": "string", "description": "ID du document à modifier"},
                        "content": {"type": "string", "description": "Nouveau contenu"}
                    },
                    "required": ["document_id", "content"]
                }
            },
            {
                "name": "create_google_sheet",
                "description": "Crée une nouvelle Google Sheet avec données",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Titre de la feuille"},
                        "data": {"type": "array", "description": "Données au format tableau"},
                        "parent_folder_id": {"type": "string", "description": "ID du dossier parent (optionnel)"}
                    },
                    "required": ["title", "data"]
                }
            },
            {
                "name": "TERMINATE_GAPP_OPERATION",
                "description": "Termine l'opération Google Apps et retourne le rapport",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "operation_status": {
                            "type": "string",
                            "enum": ["SUCCESS", "FAILURE"],
                            "description": "Statut de l'opération"
                        },
                        "summary": {"type": "string", "description": "Résumé de l'opération"},
                        "documents_created": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Liste des documents créés"
                        }
                    },
                    "required": ["operation_status", "summary"]
                }
            }
        ]
    
    def _get_default_gapp_tool_mapping(self) -> Dict[str, Any]:
        """Retourne le mapping par défaut des outils GoogleApps"""
        return {
            "create_google_doc": self._create_google_doc_impl,
            "update_doc_content": self._update_doc_content_impl,
            "create_google_sheet": self._create_google_sheet_impl,
            # TERMINATE_GAPP_OPERATION n'est pas mappé (géré par le workflow)
        }
    
    # ═══════════════════════════════════════════════════════════════
    # IMPLÉMENTATIONS DES OUTILS GOOGLE APPS
    # ═══════════════════════════════════════════════════════════════
    
    def _create_google_doc_impl(self, title: str, content: str, parent_folder_id: Optional[str] = None) -> Dict:
        """Implémentation de création de Google Doc"""
        try:
            # Utiliser le service Docs du parent agent si disponible
            if hasattr(self.parent_agent, 'dms_system') and hasattr(self.parent_agent.dms_system, 'docs_service'):
                docs_service = self.parent_agent.dms_system.docs_service
                drive_service = self.parent_agent.dms_system.drive_service
                
                # Créer le document
                doc = docs_service.documents().create(body={'title': title}).execute()
                doc_id = doc.get('documentId')
                
                # Ajouter le contenu
                requests = [{
                    'insertText': {
                        'location': {'index': 1},
                        'text': content
                    }
                }]
                docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
                
                # Déplacer vers le dossier parent si spécifié
                if parent_folder_id:
                    drive_service.files().update(
                        fileId=doc_id,
                        addParents=parent_folder_id,
                        removeParents='root',
                        fields='id, parents'
                    ).execute()
                
                return {
                    'success': True,
                    'document_id': doc_id,
                    'document_url': f"https://docs.google.com/document/d/{doc_id}/edit",
                    'title': title
                }
            else:
                return {'success': False, 'error': 'Service Google Docs non disponible'}
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _update_doc_content_impl(self, document_id: str, content: str) -> Dict:
        """Implémentation de mise à jour de Google Doc"""
        try:
            if hasattr(self.parent_agent, 'dms_system') and hasattr(self.parent_agent.dms_system, 'docs_service'):
                docs_service = self.parent_agent.dms_system.docs_service
                
                # Lire le document pour obtenir l'index de fin
                doc = docs_service.documents().get(documentId=document_id).execute()
                end_index = doc.get('body').get('content')[-1].get('endIndex') - 1
                
                # Supprimer le contenu existant et insérer le nouveau
                requests = [
                    {'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': end_index}}},
                    {'insertText': {'location': {'index': 1}, 'text': content}}
                ]
                
                docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
                
                return {
                    'success': True,
                    'document_id': document_id,
                    'message': 'Document mis à jour avec succès'
                }
            else:
                return {'success': False, 'error': 'Service Google Docs non disponible'}
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _create_google_sheet_impl(self, title: str, data: List[List[Any]], parent_folder_id: Optional[str] = None) -> Dict:
        """Implémentation de création de Google Sheet"""
        try:
            if hasattr(self.parent_agent, 'dms_system') and hasattr(self.parent_agent.dms_system, 'spreadsheet_service'):
                sheets_service = self.parent_agent.dms_system.spreadsheet_service
                drive_service = self.parent_agent.dms_system.drive_service
                
                # Créer la feuille
                spreadsheet = {
                    'properties': {'title': title},
                    'sheets': [{'properties': {'title': 'Sheet1'}}]
                }
                
                spreadsheet = sheets_service.spreadsheets().create(body=spreadsheet).execute()
                spreadsheet_id = spreadsheet.get('spreadsheetId')
                
                # Ajouter les données
                range_name = 'Sheet1!A1'
                body = {'values': data}
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                # Déplacer vers le dossier parent si spécifié
                if parent_folder_id:
                    drive_service.files().update(
                        fileId=spreadsheet_id,
                        addParents=parent_folder_id,
                        removeParents='root',
                        fields='id, parents'
                    ).execute()
                
                return {
                    'success': True,
                    'document_id': spreadsheet_id,
                    'document_url': f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
                    'title': title
                }
            else:
                return {'success': False, 'error': 'Service Google Sheets non disponible'}
                
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ============================================================================
# DRIVE AGENT - Agent subordonné spécialisé dans les manipulations Drive
# ============================================================================

class DriveAgent(BaseAIAgent):
    """
    Agent subordonné spécialisé dans les manipulations de fichiers Drive.
    Travaille sous les ordres du FileManagerPinnokio (agent principal).
    """
    
    def __init__(self,
                 parent_agent: 'FileManagerPinnokio',
                 drive_service,
                 root_folder_id: str,
                 firebase_user_id=None,
                 job_id=None,
                 collection_name=None,
                 **kwargs):
        """
        Initialise le DriveAgent.
        
        Args:
            parent_agent: Référence à l'agent principal FileManagerPinnokio
            drive_service: Instance de DriveClientService
            root_folder_id: ID du dossier racine
        """
        super().__init__(
            collection_name=collection_name,
            firebase_user_id=firebase_user_id,
            job_id=job_id,
            function_name='drive_agent'
        )
        
        self.parent_agent = parent_agent
        self.drive_service = drive_service
        self.root_folder_id = root_folder_id
        
        # Enregistrer le provider LLM pour DriveAgent (Kimi K2.5)
        moonshot_provider = NEW_MOONSHOT_AIAgent()
        self.register_provider(ModelProvider.MOONSHOT_AI, moonshot_provider)
        
        # Initialiser le system prompt
        self._initialize_drive_agent_prompt()
        
        print(f"✅ DriveAgent initialisé")
    
    def _initialize_drive_agent_prompt(self):
        """Initialise le system prompt du DriveAgent"""
        prompt = f"""Vous êtes DriveAgent, spécialiste des manipulations de fichiers dans Google Drive.
            Vous travaillez sous les ordres de l'Agent Principal (FileManager).

            🎯 MISSION :
            Exécuter avec précision les opérations de manipulation de fichiers et dossiers dans Google Drive.

            🔧 VOS OUTILS DISPONIBLES :

            1. **search_file_in_dms(file_name, folder_id, mime_type, max_results)** :
               Rechercher des fichiers par nom, type, date
               ⚠️ IMPORTANT : Le paramètre 'folder_id' est CRUCIAL !
               - Si l'Agent Principal vous donne un folder_id → UTILISEZ-LE
               - Si aucun folder_id n'est fourni → Cherche dans le dossier racine
               
            2. **create_folder_in_dms(folder_name, parent_folder_id, description)** :
               Créer un nouveau dossier
               💡 Utilisez parent_folder_id pour créer au bon endroit
               
            3. **move_file_in_dms(file_id, new_parent_folder_id)** :
               Déplacer un fichier vers un dossier
               
            4. **rename_file_in_dms(file_id, new_name)** :
               Renommer un fichier ou dossier
               
            5. **delete_file_in_dms(file_id, permanent)** :
               Supprimer un fichier (⚠️ irréversible si permanent=True)
               
            6. **copy_file_in_dms(file_id, new_name, destination_folder_id)** :
               Copier un fichier
               
            7. **get_file_metadata(file_id)** :
               Récupérer les métadonnées d'un fichier
               
            8. **list_folder_contents(folder_id, recursive, max_depth)** :
               Lister le contenu d'un dossier
               
            9. **vision_document(file_id, question)** :
               Analyse visuelle d'un document via IA
               💡 Utilisez-le pour lire le contenu visuel de PDFs, images, documents scannés
               
            10. **TERMINATE_DRIVE_OPERATION** : Terminer votre tâche

            📂 CONTEXTE :
            - Dossier racine : {self.root_folder_id}
            - Toutes vos opérations doivent être dans ce dossier racine sauf instruction contraire

            ⚠️ INSTRUCTIONS IMPORTANTES :

            1. **Exécution précise** : Suivez EXACTEMENT les instructions de l'Agent Principal
            
            2. **Utilisez les folder_id fournis** :
               - L'Agent Principal vous donne des folder_id précis → UTILISEZ-LES
               - NE PAS chercher un dossier par nom si on vous donne son ID
               - Le folder_id est une référence DIRECTE au dossier
               
            3. **Vérification** : Toujours vérifier l'existence avant de créer
            
            4. **Clarification** : Si vous manquez d'informations CRITIQUES (pas le folder_id, mais 
               d'autres détails), indiquez-le dans un text_output
               
            5. **Évitez les boucles** : Si une recherche ne donne aucun résultat après 2 tentatives,
               produisez un text_output pour demander clarification au lieu de réessayer
               
            6. **Rapport détaillé** : Utilisez TERMINATE_DRIVE_OPERATION avec un rapport HONNÊTE

            💬 COMMUNICATION :
            - Si vous avez besoin de clarifications, produisez un text_output avec votre question
            - L'Agent Principal lira votre output et vous répondra
            - NE flush PAS votre historique tant que TERMINATE n'est pas appelé
            - Votre historique est conservé entre les échanges avec l'Agent Principal

            📊 RAPPORT FINAL (via TERMINATE_DRIVE_OPERATION) :
            {{
            "operation_status": "SUCCESS | PARTIAL_SUCCESS | FAILURE",
            "files_processed": ["liste des fichiers traités"],
            "folders_created": ["liste des dossiers créés"],
            "errors_encountered": ["liste des erreurs"],
            "summary": "Résumé textuel de l'opération"
            }}

            Commencez l'exécution de votre tâche !
            """
        self.update_system_prompt(prompt)
    
    def vision_document(self, file_id: str, question: str) -> Dict[str, Any]:
        """
        Analyse visuelle d'un document via Vision AI.
        
        Args:
            file_id: ID du fichier dans Google Drive
            question: Question à poser sur le document
        
        Returns:
            Dict avec success, analysis et message
        """
        try:
            # Utiliser l'outil de vision du parent agent
            if self.parent_agent and hasattr(self.parent_agent, 'vision_document'):
                return self.parent_agent.vision_document(file_id, question)
            else:
                # Fallback: créer notre propre agent vision (Kimi K2.5)
                from ...llm.klk_agents import NEW_MOONSHOT_AIAgent, ModelSize
                vision_agent = NEW_MOONSHOT_AIAgent(
                    collection_name=self.collection_name,
                    job_id=self.job_id
                )

                print(f"👁️ [DriveAgent] Analyse visuelle du document {file_id}...")
                
                vision_prompt = f"""
                Analysez ce document et répondez à la question suivante:
                
                {question}
                
                Soyez précis et concis dans votre réponse.
                """
                
                analysis_result = vision_agent.process_vision(
                    text=vision_prompt,
                    size=ModelSize.MEDIUM,
                    file_ids=[file_id]
                )
                
                # Enregistrer les tokens
                if hasattr(self, 'token_manager'):
                    vision_agent.load_token_usage_to_db(
                        project_id=self.collection_name,
                        job_id=self.job_id,
                        workflow_step='drive_vision_document'
                    )
                
                return {
                    'success': True,
                    'analysis': analysis_result,
                    'message': f'Analyse visuelle complétée pour le document {file_id}'
                }
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'analysis': None,
                'message': f"Erreur lors de l'analyse visuelle: {str(e)}"
            }
    
    def _get_default_drive_tools(self) -> List[Dict[str, Any]]:
        """
        Retourne les outils par défaut pour DriveAgent (outils DMS bas niveau uniquement).
        
        Returns:
            List[Dict]: Liste des schémas d'outils DMS
        """
        from tools.file_manager_tools import FileManagerTools
        
        # Créer une instance temporaire de FileManagerTools
        tools_instance = FileManagerTools(
            drive_service=self.drive_service,
            root_folder_id=self.root_folder_id
        )
        
        # Récupérer tous les outils et filtrer pour ne garder que les outils DMS
        all_tools = tools_instance.get_tools_schema()
        
        # Garder uniquement les outils DMS bas niveau (exclure les outils de haut niveau)
        high_level_tools = ['CALL_DRIVE_AGENT', 'CALL_GAPP_AGENT', 'ASK_USER', 
                           'TERMINATE_FILE_MANAGEMENT', 'get_departement_prompt',
                           'vision_document', 'create_fiscal_year_structure']
        
        dms_tools = [tool for tool in all_tools if tool['name'] not in high_level_tools]
        
        # Ajouter TERMINATE_DRIVE_OPERATION
        terminate_tool = {
            "name": "TERMINATE_DRIVE_OPERATION",
            "description": "🎯 Termine l'opération DriveAgent et retourne un rapport complet à l'agent principal.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "operation_status": {
                        "type": "string",
                        "enum": ["SUCCESS", "PARTIAL_SUCCESS", "FAILURE"],
                        "description": "Statut de l'opération"
                    },
                    "files_processed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des IDs de fichiers traités"
                    },
                    "folders_created": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des IDs de dossiers créés"
                    },
                    "errors_encountered": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des erreurs rencontrées"
                    },
                    "summary": {
                        "type": "string",
                        "description": "Résumé textuel de l'opération"
                    }
                },
                "required": ["operation_status", "summary"]
            }
        }
        
        dms_tools.append(terminate_tool)
        
        return dms_tools
    
    def _get_default_drive_tool_mapping(self) -> Dict[str, Any]:
        """
        Retourne le mapping par défaut des outils DriveAgent.
        
        Returns:
            Dict: Mapping nom d'outil -> fonction
        """
        from tools.file_manager_tools import FileManagerTools
        
        # Créer une instance temporaire de FileManagerTools
        tools_instance = FileManagerTools(
            drive_service=self.drive_service,
            root_folder_id=self.root_folder_id
        )
        
        # Récupérer le mapping de base
        mapping = tools_instance.get_tool_mapping()
        
        # Ajouter vision_document
        mapping['vision_document'] = self.vision_document
        
        # TERMINATE_DRIVE_OPERATION n'est pas mappé (géré par le workflow)
        
        return mapping
    
    def drive_agent_workflow(self,
                            manager_instance: Any,
                            initial_query: str,
                            tools: Optional[List[Dict[str, Any]]] = None,
                            tool_mapping: Optional[Dict[str, Any]] = None,
                            size: ModelSize = ModelSize.MEDIUM,
                            provider: Optional[ModelProvider] = None,
                            max_tokens: int = 1024,
                            project_id: str = None,
                            job_id: str = None,
                            workflow_step: str = 'drive_workflow',
                            max_turns: int = 5,
                            raw_output: bool = True,
                            max_tokens_budget: int = 10000) -> Tuple[bool, str, str, Dict]:
        """
        Workflow d'exécution du DriveAgent (boucle interne).
        
        Args:
            initial_query: Instructions de l'agent principal
            max_turns: Maximum 5 tours
            max_tokens_budget: Budget 10,000 tokens
        
        Returns:
            Tuple[bool, str, str, Dict]:
                - Success status
                - Status code
                - Response text (pour agent principal)
                - Report data
        """
        try:
            print(f"🔧 [DRIVE_WORKFLOW] Démarrage - Tours max: {max_turns}")
            print(f"💰 [DRIVE_WORKFLOW] Budget tokens: {max_tokens_budget:,}")
            
            # Si tools/tool_mapping non fournis, utiliser les outils par défaut
            if tools is None:
                tools = self._get_default_drive_tools()
            if tool_mapping is None:
                tool_mapping = self._get_default_drive_tool_mapping()
            
            turn_count = 0
            user_input = initial_query
            next_user_input_parts = []
            
            # Variables pour le rapport
            files_processed = []
            folders_created = []
            errors_encountered = []
            
            while turn_count < max_turns:
                turn_count += 1
                print(f"\033[94m🔧 Tour {turn_count}/{max_turns} - DriveAgent\033[0m")
                
                # Vérification budget tokens
                try:
                    if hasattr(manager_instance, 'get_total_context_tokens'):
                        tokens_before = manager_instance.get_total_context_tokens(provider)
                        print(f"💰 [TOKENS] Tour {turn_count} - Tokens: {tokens_before:,}/{max_tokens_budget:,}")
                        
                        if tokens_before >= max_tokens_budget:
                            print(f"⚠️ [TOKENS] Budget atteint - Arrêt")
                            break
                except Exception as e:
                    print(f"⚠️ [TOKENS] Erreur calcul: {e}")
                
                # Appel de l'agent
                ia_responses = manager_instance.process_tool_use(
                    content=user_input,
                    tools=tools,
                    tool_mapping=tool_mapping,
                    size=size,
                    provider=provider,
                    max_tokens=max_tokens,
                    raw_output=raw_output
                )
                
                # Tracking tokens
                if project_id and job_id:
                    manager_instance.load_token_usage_to_db(
                        project_id=project_id,
                        job_id=job_id,
                        workflow_step=f"{workflow_step}_turn_{turn_count}"
                    )
                
                # Normalisation
                if not isinstance(ia_responses, list):
                    ia_responses = [ia_responses] if ia_responses else []
                
                next_user_input_parts = []
                
                # Traitement des réponses
                for response_block in ia_responses:
                    if not isinstance(response_block, dict):
                        continue
                    
                    # TOOL_OUTPUT
                    if "tool_output" in response_block:
                        tool_block = response_block["tool_output"]
                        tool_name = tool_block.get('tool_name', 'UnknownTool')
                        tool_content = tool_block.get('content', '')
                        
                        print(f"  🔧 Outil: {tool_name}")
                        
                        # TERMINATE_DRIVE_OPERATION
                        if tool_name == 'TERMINATE_DRIVE_OPERATION':
                            print(f"[DRIVE_WORKFLOW] ✓ TERMINATE_DRIVE_OPERATION")
                            
                            if isinstance(tool_content, dict):
                                operation_status = tool_content.get('operation_status', 'SUCCESS')
                                summary = tool_content.get('summary', '')
                                files_processed = tool_content.get('files_processed', files_processed)
                                folders_created = tool_content.get('folders_created', folders_created)
                                errors_encountered = tool_content.get('errors_encountered', errors_encountered)
                            else:
                                operation_status = 'SUCCESS'
                                summary = str(tool_content)
                            
                            report_data = {
                                'operation_status': operation_status,
                                'files_processed': files_processed,
                                'folders_created': folders_created,
                                'errors_encountered': errors_encountered,
                                'summary': summary
                            }
                            
                            # Flush chat history UNIQUEMENT ici
                            manager_instance.flush_chat_history()
                            
                            return True, "OPERATION_COMPLETED", summary, report_data
                        
                        # Autres outils DMS
                        else:
                            if isinstance(tool_content, dict):
                                if tool_content.get('success'):
                                    if tool_name == 'create_folder_in_dms':
                                        folders_created.append(tool_content.get('folder_id', ''))
                                    else:
                                        files_processed.append(tool_content.get('file_id', ''))
                                else:
                                    errors_encountered.append(f"{tool_name}: {tool_content.get('error', 'Unknown')}")
                            
                            next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                    
                    # TEXT_OUTPUT (question à l'agent principal)
                    elif "text_output" in response_block:
                        text_block = response_block["text_output"]
                        
                        if isinstance(text_block, dict) and "content" in text_block:
                            content = text_block["content"]
                            extracted_text = content.get('answer_text', str(content)) if isinstance(content, dict) else str(content)
                        elif isinstance(text_block, str):
                            extracted_text = text_block
                        else:
                            extracted_text = str(text_block)
                        
                        print(f"  💬 Question à l'Agent Principal: {extracted_text[:200]}...")
                        
                        # Ce text_output sera retourné à l'agent principal
                        # PAS de flush_chat_history ici !
                        return False, "WAITING_INPUT", extracted_text, {
                            'files_processed': files_processed,
                            'folders_created': folders_created,
                            'errors_encountered': errors_encountered
                        }
                
                # Préparer input pour le prochain tour
                if next_user_input_parts:
                    user_input = "\n".join(next_user_input_parts)
                else:
                    return False, "NO_IA_ACTION", "Aucune réponse utilisable du DriveAgent", {}
            
            # Max tours atteint
            print(f"[DRIVE_WORKFLOW] Max tours atteint ({max_turns})")
            
            report_data = {
                'operation_status': 'INCOMPLETE',
                'files_processed': files_processed,
                'folders_created': folders_created,
                'errors_encountered': errors_encountered,
                'summary': f"Max tours atteint. {len(files_processed)} fichiers traités."
            }
            
            # Flush uniquement si terminé
            manager_instance.flush_chat_history()
            
            return False, "MAX_TURNS_REACHED", report_data['summary'], report_data
            
        except Exception as e:
            print(f"[DRIVE_WORKFLOW] ERREUR: {e}")
            traceback.print_exc()
            
            error_report = {
                'operation_status': 'ERROR',
                'summary': f"Erreur: {str(e)}"
            }
            
            manager_instance.flush_chat_history()
            
            return False, "ERROR_FATAL", f"Erreur: {str(e)}", error_report


