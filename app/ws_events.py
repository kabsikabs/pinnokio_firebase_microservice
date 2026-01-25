"""
WebSocket Event Constants - Backend Mirror
==========================================

Ce fichier est le MIROIR EXACT de src/lib/ws-events.ts (frontend Next.js).

CRITICAL RULE:
Toute modification ici DOIT etre repercutee dans ws-events.ts et vice-versa.
Les 46 evenements definis ici correspondent EXACTEMENT aux evenements frontend.

Frontend source: pinnokio_app_v2/src/lib/ws-events.ts
Backend mirror: firebase_microservice/app/ws_events.py

Usage:
    from app.ws_events import WS_EVENTS

    await hub.broadcast(uid, {
        "type": WS_EVENTS.LLM.STREAM_START,
        "payload": {...}
    })

Convention de nommage:
- Prefixe par domaine (AUTH, LLM, COMPANY, DASHBOARD, INVOICE, SESSION, etc.)
- Suffixe par action (START, END, UPDATE, ERROR, CHANGED, etc.)

Event count verification:
- AUTH: 8 events
- LLM: 12 events
- USER: 1 event
- COMPANY: 3 events
- DASHBOARD: 9 events (added orchestration events)
- JOB: 2 events
- APPROVAL: 2 events
- ACTIVITY: 1 event
- INVOICE: 1 event
- WORKFLOW: 1 event
- SESSION: 2 events
- CONNECTION: 2 events
TOTAL: 44 unique events (53 with legacy aliases counted)
"""


# ============================================
# AUTH Events (8 events)
# ============================================
class AuthEvents:
    """Evenements d'authentification."""
    LOGIN_SUCCESS = "auth.login_success"
    LOGIN_ERROR = "auth.login_error"
    OAUTH_SUCCESS = "auth.oauth_success"
    OAUTH_ERROR = "auth.oauth_error"
    FIREBASE_TOKEN = "auth.firebase_token"
    LOGOUT = "auth.logout"
    SESSION_CONFIRMED = "auth.session_confirmed"
    SESSION_REGISTERED = "auth.session_registered"


# ============================================
# LLM/Chat Events (12 events)
# ============================================
class LLMEvents:
    """Evenements LLM/Chat streaming."""
    # Streaming events (from microservice)
    STREAM_START = "llm.stream_start"
    STREAM_DELTA = "llm.stream_delta"
    STREAM_END = "llm.stream_end"
    STREAM_INTERRUPTED = "llm.stream_interrupted"  # Streaming was interrupted by user action
    STOP_STREAMING = "llm.stop_streaming"          # Request to stop streaming

    # Tool use events
    TOOL_USE_START = "llm.tool_use_start"
    TOOL_USE_PROGRESS = "llm.tool_use_progress"
    TOOL_USE_END = "llm.tool_use_end"

    # Other events
    APPROVAL_REQUEST = "llm.approval_request"
    ERROR = "llm.error"
    SESSION_READY = "llm.session_ready"
    INITIALIZE = "llm.initialize"
    MESSAGE = "llm.message"
    RESPONSE = "llm.response"


# ============================================
# User Events (3 events)
# ============================================
class UserEvents:
    """Evenements utilisateur."""
    PROFILE = "user.profile"
    FIRST_CONNECT = "user.first_connect"  # First connection welcome with credit
    SETTINGS_UPDATED = "user.settings_updated"  # User settings updated


# ============================================
# Company Events (3 events)
# ============================================
class CompanyEvents:
    """Evenements company/societe."""
    LIST = "company.list"
    DETAILS = "company.details"
    SELECT = "company.select"


# ============================================
# Dashboard Events (13 events)
# ============================================
class DashboardEvents:
    """Evenements dashboard."""
    METRICS_UPDATE = "dashboard.metrics_update"
    METRICS = "dashboard.metrics"
    REFRESH = "dashboard.refresh"
    FULL_DATA = "dashboard.full_data"  # Complete dashboard data endpoint
    # Orchestration events
    ORCHESTRATE_INIT = "dashboard.orchestrate_init"  # Initialize dashboard orchestration sequence
    PHASE_START = "dashboard.phase_start"  # Signal start of a loading phase
    PHASE_COMPLETE = "dashboard.phase_complete"  # Signal completion of a loading phase
    DATA_LOADING_PROGRESS = "dashboard.data_loading_progress"  # Progress update during data loading
    COMPANY_CHANGE = "dashboard.company_change"  # Company selection changed in dashboard context
    SWITCH_ACCOUNT = "dashboard.switch_account"  # Switch between own account and shared accounts
    # Widget-specific update events
    STORAGE_UPDATE = "dashboard.storage_update"  # Storage info updated
    EXPENSES_UPDATE = "dashboard.expenses_update"  # Expenses data updated
    TASKS_UPDATE = "dashboard.tasks_update"  # Tasks data updated
    APPROVALS_UPDATE = "dashboard.approvals_update"  # Approvals data updated


# ============================================
# Job Events (2 events)
# ============================================
class JobEvents:
    """Evenements jobs/taches."""
    STATUS_CHANGED = "job.status_changed"
    BATCH_UPDATE = "job.batch_update"


# ============================================
# Task Events (5 events)
# ============================================
class TaskEvents:
    """Evenements tasks (taches planifiees)."""
    LIST = "task.list"
    EXECUTE = "task.execute"
    EXECUTED = "task.executed"
    STATUS_CHANGED = "task.status_changed"
    TOGGLE_ENABLED = "task.toggle_enabled"
    UPDATE = "task.update"
    UPDATED = "task.updated"


# ============================================
# Approval Events (7 events)
# ============================================
class ApprovalEvents:
    """Evenements approbations."""
    NEW = "approval.new"
    STATUS_CHANGED = "approval.status_changed"
    LIST = "approval.list"
    SEND_ROUTER = "approval.send_router"
    SEND_BANKER = "approval.send_banker"
    SEND_APBOOKEEPER = "approval.send_apbookeeper"
    RESULT = "approval.result"


# ============================================
# Activity Events (1 event)
# ============================================
class ActivityEvents:
    """Evenements activite."""
    CREATED = "activity.created"


# ============================================
# Invoice Events (14 events)
# APBookkeeper/Invoices page management
# ============================================
class InvoiceEvents:
    """
    Evenements factures (APBookkeeper).

    Gere:
    - Orchestration de page
    - Liste des factures par categorie
    - Traitement (processing) de factures
    - Arret de traitement
    - Suppression de factures traitees
    - Redemarrage de jobs
    - Instructions par document

    NOTE Invoices Specificites:
    - 4 onglets: to_do, in_process, pending, processed
    - AAA/AW colonnes uniquement sur to_do
    - Boutons conditionnels par onglet
    """
    # Orchestration events
    ORCHESTRATE_INIT = "invoices.orchestrate_init"
    FULL_DATA = "invoices.full_data"

    # Document operations
    LIST = "invoices.list"
    PROCESS = "invoices.process"
    PROCESSED = "invoices.processed"
    STOP = "invoices.stop"
    STOPPED = "invoices.stopped"
    DELETE = "invoices.delete"
    DELETED = "invoices.deleted"
    RESTART = "invoices.restart"
    RESTARTED = "invoices.restarted"
    REFRESH = "invoices.refresh"

    # Instructions
    INSTRUCTIONS_SAVE = "invoices.instructions_save"
    INSTRUCTIONS_SAVED = "invoices.instructions_saved"

    # Legacy (backward compatibility)
    FIELD_UPDATE = "invoice.field_update"

    # Error
    ERROR = "invoices.error"


# ============================================
# Workflow Events (1 event)
# ============================================
class WorkflowEvents:
    """Evenements workflow."""
    STEP_UPDATE = "workflow.step_update"


# ============================================
# Session Events (2 events)
# ============================================
class SessionEvents:
    """Evenements session."""
    EXPIRED = "session.expired"
    INVALIDATED = "session.invalidated"


# ============================================
# Connection Events (2 events)
# ============================================
class ConnectionEvents:
    """Evenements connexion systeme."""
    STATUS = "connection"
    ERROR = "error"


# ============================================
# Page State Events (4 events) - NEW
# ============================================
class PageStateEvents:
    """
    Evenements pour la gestion d'etat des pages.

    Permet le rechargement rapide des pages apres refresh
    en restaurant l'etat depuis le cache Redis.
    """
    RESTORE = "page.restore_state"       # Frontend demande restauration
    RESTORED = "page.state_restored"     # Backend retourne etat cache
    NOT_FOUND = "page.state_not_found"   # Etat non trouve, orchestration necessaire
    INVALIDATE = "page.invalidate_state" # Invalider cache d'une page


# ============================================
# Balance Events (6 events) - NEW
# ============================================
class BalanceEvents:
    """
    Events for account balance operations (top-up, refresh).

    Flow:
    - TOP_UP: Frontend requests top-up with amount
    - TOP_UP_RESULT: Backend returns Stripe checkout URL
    - TOP_UP_COMPLETE: After Stripe redirect, payment completed/cancelled
    - REFRESH: Frontend requests fresh balance data
    - REFRESHED: Backend returns updated balance data
    - ERROR: Error during balance operations
    """
    TOP_UP = "balance.top_up"               # Request top-up
    TOP_UP_RESULT = "balance.top_up_result" # Checkout URL or error
    TOP_UP_COMPLETE = "balance.top_up_complete"  # Payment completed
    REFRESH = "balance.refresh"             # Request balance refresh
    REFRESHED = "balance.refreshed"         # Balance data updated
    ERROR = "balance.error"                 # Error occurred


# ============================================
# Chat Events (12 events) - NEW
# Chat session management (distinct from LLM streaming)
# ============================================
class ChatEvents:
    """
    Evenements pour la gestion des sessions de chat.

    Distinct de LLMEvents qui gere le streaming des reponses.
    ChatEvents gere:
    - Orchestration de page chat
    - Sessions de chat (CRUD)
    - Historique des messages
    - Mode chat (general, onboarding, etc.)
    - Auto-naming de sessions
    - Workflow checklist (onboarding)
    """
    # Orchestration events
    ORCHESTRATE_INIT = "chat.orchestrate_init"     # Initialize chat page data
    FULL_DATA = "chat.full_data"                   # Complete chat page data

    # Session management
    SESSIONS_LIST = "chat.sessions_list"           # List of chat sessions
    SESSION_SELECT = "chat.session_select"         # Select a session
    SESSION_CREATE = "chat.session_create"         # Create new session
    SESSION_DELETE = "chat.session_delete"         # Delete session
    SESSION_RENAME = "chat.session_rename"         # Rename session
    SESSION_AUTO_NAME = "chat.session_auto_name"   # Auto-name virgin chat (LLM)

    # Message sending
    SEND_MESSAGE = "chat.send_message"             # Send a message
    MESSAGE_SENT = "chat.message_sent"             # Message sent confirmation

    # Message history
    HISTORY_LOAD = "chat.history_load"             # Load chat history
    HISTORY_LOADED = "chat.history_loaded"         # History data received

    # Chat mode
    MODE_CHANGE = "chat.mode_change"               # Change chat mode
    MODE_CHANGED = "chat.mode_changed"             # Mode change confirmed

    # Workflow checklist (onboarding)
    WORKFLOW_SET = "chat.workflow_set"             # Set workflow checklist
    WORKFLOW_STEP_UPDATE = "chat.workflow_step_update"  # Update step status

    # Interactive cards
    CARD_RECEIVED = "chat.card_received"           # Interactive card received
    CARD_CLICKED = "chat.card_clicked"             # User clicked on card

    # Tool use indicators
    TOOL_INDICATOR_START = "chat.tool_indicator_start"   # Tool use started
    TOOL_INDICATOR_END = "chat.tool_indicator_end"       # Tool use ended

    # Error
    ERROR = "chat.error"                           # Chat-specific error


# ============================================
# Routing Events (10 events) - NEW
# Document routing management
# ============================================
class RoutingEvents:
    """
    Evenements pour la page Routing (Document Matrix).

    Gere le routage de documents depuis Google Drive
    vers les differents departements de traitement.
    """
    # Orchestration events
    ORCHESTRATE_INIT = "routing.orchestrate_init"   # Initialize page data
    FULL_DATA = "routing.full_data"                 # Complete page data

    # Document operations
    LIST = "routing.list"                           # List documents by category
    PROCESS = "routing.process"                     # Process selected documents
    PROCESSED = "routing.processed"                 # Processing complete
    RESTART = "routing.restart"                     # Restart a job
    RESTARTED = "routing.restarted"                 # Job restarted
    REFRESH = "routing.refresh"                     # Refresh current tab

    # Instructions
    INSTRUCTIONS_SAVE = "routing.instructions_save" # Save document instructions
    INSTRUCTIONS_SAVED = "routing.instructions_saved"  # Instructions saved

    # OAuth (Drive connection)
    OAUTH_INIT = "routing.oauth_init"               # Initialize OAuth flow
    OAUTH_CALLBACK = "routing.oauth_callback"       # OAuth callback received

    # Error
    ERROR = "routing.error"                         # Routing-specific error


# ============================================
# Notification Events (10 events) - NEW
# Notifications from jobbeurs (Router, APbookeeper, Bankbookeeper)
# ============================================
class NotificationEvents:
    """
    Evenements pour les notifications Firebase (Firestore).

    Les notifications proviennent des jobbeurs et sont affichees
    dans le NotificationBell du header.

    Source: clients/{uid}/notifications
    Canal PubSub: notification:{uid}
    """
    # Initial load
    FULL_DATA = "notification.full_data"               # Liste complete

    # Real-time updates (via Redis PubSub)
    DELTA = "notification.delta"                       # Delta (new/update/remove)
    NEW = "notification.new"                           # Nouvelle notification
    UPDATED = "notification.updated"                   # Notification mise a jour
    REMOVED = "notification.removed"                   # Notification supprimee

    # User actions
    MARK_READ = "notification.mark_read"               # Marquer comme lu (request)
    MARK_READ_RESULT = "notification.mark_read_result" # Resultat
    CLICK = "notification.click"                       # Clic sur notification
    CLICK_RESULT = "notification.click_result"         # Resultat avec redirect info

    # Error
    ERROR = "notification.error"                       # Erreur notification


# ============================================
# Messenger Events (9 events) - NEW
# Direct messages from Firebase RTDB
# ============================================
class MessengerEvents:
    """
    Evenements pour les messages directs (Firebase RTDB).

    Les messages sont affiches dans le MessengerBell du header.

    Source: Firebase RTDB - direct_messages/{uid}
    Canal PubSub: messenger:{uid}
    """
    # Initial load
    FULL_DATA = "messenger.full_data"                  # Liste complete

    # Real-time updates (via Redis PubSub)
    DELTA = "messenger.delta"                          # Delta (new/update/remove)
    NEW = "messenger.new"                              # Nouveau message
    REMOVED = "messenger.removed"                      # Message supprime

    # User actions
    MARK_READ = "messenger.mark_read"                  # Marquer comme lu (request)
    MARK_READ_RESULT = "messenger.mark_read_result"    # Resultat
    CLICK = "messenger.click"                          # Clic sur message
    CLICK_RESULT = "messenger.click_result"            # Resultat avec redirect info

    # Error
    ERROR = "messenger.error"                          # Erreur messenger


# ============================================
# Static Data Events (4 events) - NEW
# Données statiques pour les dropdowns (chargées une seule fois)
# ============================================
class StaticDataEvents:
    """
    Evenements pour les donnees statiques de l'application.

    Ces donnees sont chargees une seule fois pendant Phase 0 (user_setup)
    et mises en cache dans le store frontend de maniere permanente.

    Contenu:
    - Languages: Liste des langues disponibles
    - Countries: Liste des pays
    - Legal forms: Formes juridiques par pays
    - ERPs: Liste des systemes ERP (Odoo, Banana, etc.)
    - DMS: Systemes de gestion documentaire (Drive, etc.)
    - Currencies: Liste des devises
    - Communication: Types de communication (Pinnokio, Telegram)
    """
    # Request/Response pattern
    LOAD = "static_data.load"             # Frontend demande chargement
    LOADED = "static_data.loaded"         # Backend retourne toutes les donnees
    REFRESH = "static_data.refresh"       # Force rafraichissement du cache
    ERROR = "static_data.error"           # Erreur de chargement


# ============================================
# Company Settings Events (15 events) - NEW
# Page de configuration de l'entreprise
# ============================================
class CompanySettingsEvents:
    """
    Evenements pour la page Company Settings.

    Gere:
    - Orchestration de page
    - Informations entreprise
    - Parametres DMS/Communication/Accounting
    - Workflow (APbookeeper, Banker, Router)
    - Contextes
    - Telegram room registration
    - Asset management
    - ERP connections
    - User sharing
    - Company deletion
    """
    # Orchestration events
    ORCHESTRATE_INIT = "company_settings.orchestrate_init"
    FULL_DATA = "company_settings.full_data"

    # Additional data (optimized - Telegram/ERP only, uses COMPANY.DETAILS for rest)
    FETCH_ADDITIONAL = "company_settings.fetch_additional"
    ADDITIONAL_DATA = "company_settings.additional_data"

    # Company Info
    SAVE_COMPANY_INFO = "company_settings.save_company_info"
    COMPANY_INFO_SAVED = "company_settings.company_info_saved"

    # Settings (DMS, Communication, Accounting)
    SAVE_SETTINGS = "company_settings.save_settings"
    SETTINGS_SAVED = "company_settings.settings_saved"

    # Workflow params
    SAVE_WORKFLOW = "company_settings.save_workflow"
    WORKFLOW_SAVED = "company_settings.workflow_saved"

    # Context
    SAVE_CONTEXT = "company_settings.save_context"
    CONTEXT_SAVED = "company_settings.context_saved"

    # Telegram room registration (CRITICAL)
    TELEGRAM_START_REGISTRATION = "company_settings.telegram_start_registration"
    TELEGRAM_REGISTRATION_SUCCESS = "company_settings.telegram_registration_success"
    TELEGRAM_REGISTRATION_FAILED = "company_settings.telegram_registration_failed"
    TELEGRAM_REMOVE_USER = "company_settings.telegram_remove_user"
    TELEGRAM_USER_REMOVED = "company_settings.telegram_user_removed"

    # Asset management
    SAVE_ASSET_SETTINGS = "company_settings.save_asset_settings"
    ASSET_SETTINGS_SAVED = "company_settings.asset_settings_saved"

    # ERP connections
    SAVE_ERP_CONNECTIONS = "company_settings.save_erp_connections"
    ERP_CONNECTIONS_SAVED = "company_settings.erp_connections_saved"
    TEST_ERP_CONNECTION = "company_settings.test_erp_connection"
    ERP_CONNECTION_RESULT = "company_settings.erp_connection_result"

    # User sharing
    GET_SHARED_USERS = "company_settings.get_shared_users"
    SHARED_USERS_DATA = "company_settings.shared_users_data"
    UPDATE_SHARED_ACCESS = "company_settings.update_shared_access"
    SHARED_ACCESS_UPDATED = "company_settings.shared_access_updated"

    # Company deletion
    DELETE_COMPANY = "company_settings.delete_company"
    COMPANY_DELETED = "company_settings.company_deleted"

    # Error
    ERROR = "company_settings.error"


# ============================================
# COA Events (15 events) - NEW
# Chart of Accounts page
# ============================================
class COAEvents:
    """
    Evenements pour la page COA (Chart of Accounts).

    Gere:
    - Orchestration de page
    - Chargement comptes COA
    - Chargement fonctions KLK
    - Sauvegarde modifications vers ERP
    - Synchronisation depuis ERP
    - Gestion fonctions custom (CRUD)
    """
    # Orchestration events
    ORCHESTRATE_INIT = "coa.orchestrate_init"
    FULL_DATA = "coa.full_data"

    # Accounts
    LOAD_ACCOUNTS = "coa.load_accounts"
    ACCOUNTS_LOADED = "coa.accounts_loaded"

    # Functions
    LOAD_FUNCTIONS = "coa.load_functions"
    FUNCTIONS_LOADED = "coa.functions_loaded"

    # Save/Sync
    SAVE_CHANGES = "coa.save_changes"
    CHANGES_SAVED = "coa.changes_saved"
    SYNC_ERP = "coa.sync_erp"
    SYNC_PROGRESS = "coa.sync_progress"
    SYNC_COMPLETE = "coa.sync_complete"

    # Function CRUD
    TOGGLE_FUNCTION = "coa.toggle_function"
    FUNCTION_TOGGLED = "coa.function_toggled"
    CREATE_FUNCTION = "coa.create_function"
    UPDATE_FUNCTION = "coa.update_function"
    DELETE_FUNCTION = "coa.delete_function"
    FUNCTION_SAVED = "coa.function_saved"
    FUNCTION_DELETED = "coa.function_deleted"

    # Error
    ERROR = "coa.error"


# ============================================
# Banking Events (14 events) - NEW
# Bank transaction management
# ============================================
class BankingEvents:
    """
    Evenements pour la page Banking (Transactions bancaires).

    Gere:
    - Orchestration de page
    - Liste des comptes bancaires
    - Liste des transactions par categorie
    - Traitement (matching) de transactions
    - Arret de traitement
    - Suppression de transactions matchees
    - Instructions par transaction

    NOTE Banking Specificites:
    - PAS d'onglet "completed" - utilise "matched"
    - Selecteur de compte bancaire comme filtre principal
    - Affichage balance avec couleur
    - Gestion des batches dans in_process
    """
    # Orchestration events
    ORCHESTRATE_INIT = "banking.orchestrate_init"
    FULL_DATA = "banking.full_data"

    # Account operations
    ACCOUNTS_LIST = "banking.accounts_list"
    ACCOUNTS_LOADED = "banking.accounts_loaded"

    # Transaction operations
    LIST = "banking.list"
    PROCESS = "banking.process"
    PROCESSED = "banking.processed"
    STOP = "banking.stop"
    STOPPED = "banking.stopped"
    DELETE = "banking.delete"
    DELETED = "banking.deleted"
    REFRESH = "banking.refresh"

    # Instructions
    INSTRUCTIONS_SAVE = "banking.instructions_save"
    INSTRUCTIONS_SAVED = "banking.instructions_saved"

    # Error
    ERROR = "banking.error"


# ============================================
# Metrics Events (9 events) - NEW
# Shared metrics stores (dashboard + detail pages)
# ============================================
class MetricsEvents:
    """
    Evenements pour les stores de metriques partages.

    Architecture:
    - Les metriques sont chargees une fois au onboarding/changement de societe
    - Partagees entre dashboard et pages de detail (routing, ap, bank, expenses)
    - Pattern optimiste/pessimiste pour les actions utilisateur

    Flow:
    1. FULL_DATA: Charge toutes les metriques au login/company change
    2. *_UPDATE: Mise a jour par module lors d'actions
    3. REFRESH: Demande rafraichissement force depuis source (Drive, Firebase)

    Optimistic Updates:
    - Frontend applique delta immediatement
    - Backend confirme/rejette via UPDATE_CONFIRMED/UPDATE_FAILED
    - Rollback auto si timeout (10s)
    """
    # Full data load (at onboarding/company change)
    FULL_DATA = "metrics.full_data"

    # Module-specific updates
    ROUTING_UPDATE = "metrics.routing_update"
    AP_UPDATE = "metrics.ap_update"
    BANK_UPDATE = "metrics.bank_update"
    EXPENSES_UPDATE = "metrics.expenses_update"

    # Optimistic update confirmation/rejection
    UPDATE_CONFIRMED = "metrics.update_confirmed"
    UPDATE_FAILED = "metrics.update_failed"

    # Refresh requests
    REFRESH = "metrics.refresh"              # Refresh all modules
    REFRESH_MODULE = "metrics.refresh_module"  # Refresh specific module


# ============================================
# Pending Action Events (4 events) - NEW
# ============================================
class PendingActionEvents:
    """
    Evenements pour les actions en attente (OAuth, paiements).

    Gere la preservation d'etat pendant les redirections externes
    vers Google OAuth, Stripe, etc.
    """
    SAVE = "pending_action.save"         # Sauvegarder action avant redirect
    SAVED = "pending_action.saved"       # Confirmation + URL de redirect
    COMPLETE = "pending_action.complete" # Action completee (callback recu)
    CANCEL = "pending_action.cancel"     # Annuler action en attente


# ============================================
# Consolidated WS_EVENTS Class
# ============================================
class WS_EVENTS:
    """
    Point d'acces centralise pour tous les evenements WebSocket.

    Usage:
        from app.ws_events import WS_EVENTS

        # Envoyer un evenement LLM
        event_type = WS_EVENTS.LLM.STREAM_START

        # Envoyer un evenement Auth
        event_type = WS_EVENTS.AUTH.LOGIN_SUCCESS
    """
    AUTH = AuthEvents
    LLM = LLMEvents
    USER = UserEvents
    COMPANY = CompanyEvents
    DASHBOARD = DashboardEvents
    JOB = JobEvents
    TASK = TaskEvents
    APPROVAL = ApprovalEvents
    ACTIVITY = ActivityEvents
    INVOICE = InvoiceEvents      # Legacy single event
    INVOICES = InvoiceEvents      # Full APBookkeeper events
    WORKFLOW = WorkflowEvents
    SESSION = SessionEvents
    CONNECTION = ConnectionEvents
    PAGE_STATE = PageStateEvents      # NEW: Page state management
    PENDING_ACTION = PendingActionEvents  # NEW: OAuth/payment flows
    BALANCE = BalanceEvents           # NEW: Account balance operations
    CHAT = ChatEvents                 # NEW: Chat session management
    ROUTING = RoutingEvents           # NEW: Document routing management
    NOTIFICATION = NotificationEvents # NEW: Notifications from jobbeurs
    MESSENGER = MessengerEvents       # NEW: Direct messages (RTDB)
    STATIC_DATA = StaticDataEvents    # NEW: Static dropdown data (loaded once)
    COMPANY_SETTINGS = CompanySettingsEvents  # NEW: Company settings page
    COA = COAEvents                   # NEW: Chart of Accounts page
    BANKING = BankingEvents           # NEW: Banking transactions page
    METRICS = MetricsEvents           # NEW: Shared metrics stores


# ============================================
# Legacy Event Mapping (Backward Compatibility)
# ============================================
# Mapping des anciens noms d'evenements vers les nouveaux
# Utilise pendant la migration progressive du code existant

LEGACY_EVENT_MAPPING = {
    # Anciens evenements LLM (llm_manager.py)
    "llm_stream_start": WS_EVENTS.LLM.STREAM_START,
    "llm_stream_chunk": WS_EVENTS.LLM.STREAM_DELTA,
    "llm_stream_delta": WS_EVENTS.LLM.STREAM_DELTA,  # Alias
    "llm_stream_complete": WS_EVENTS.LLM.STREAM_END,
    "llm_stream_end": WS_EVENTS.LLM.STREAM_END,  # Alias
    "llm_stream_error": WS_EVENTS.LLM.ERROR,
    "llm_stream_interrupted": WS_EVENTS.LLM.STREAM_INTERRUPTED,  # User stopped streaming
    "llm.error": WS_EVENTS.LLM.ERROR,

    # Anciens evenements Workflow (pinnokio_brain.py, listeners_manager.py)
    "WORKFLOW_CHECKLIST": WS_EVENTS.WORKFLOW.STEP_UPDATE,
    "WORKFLOW_STEP_UPDATE": WS_EVENTS.WORKFLOW.STEP_UPDATE,
    "workflow.step_update": WS_EVENTS.WORKFLOW.STEP_UPDATE,
    "WORKFLOW_USER_JOINED": WS_EVENTS.ACTIVITY.CREATED,  # Mapped to activity
    "WORKFLOW_PAUSED": WS_EVENTS.WORKFLOW.STEP_UPDATE,
    "WORKFLOW_RESUMING": WS_EVENTS.WORKFLOW.STEP_UPDATE,
    "WORKFLOW_RESUMED": WS_EVENTS.WORKFLOW.STEP_UPDATE,
    "workflow_*": WS_EVENTS.WORKFLOW.STEP_UPDATE,  # Pattern match

    # Anciens evenements Chat/Message
    "chat_message": WS_EVENTS.LLM.MESSAGE,
    "chat.message": WS_EVENTS.LLM.MESSAGE,

    # Anciens evenements Job (main.py lpt/callback)
    "hr_job_completed": WS_EVENTS.JOB.STATUS_CHANGED,
    "job_status_changed": WS_EVENTS.JOB.STATUS_CHANGED,
    "job.status_changed": WS_EVENTS.JOB.STATUS_CHANGED,

    # Connection events
    "connection": WS_EVENTS.CONNECTION.STATUS,
    "error": WS_EVENTS.CONNECTION.ERROR,
    "ping": WS_EVENTS.CONNECTION.STATUS,
    "pong": WS_EVENTS.CONNECTION.STATUS,
}


# ============================================
# Helper Functions
# ============================================

def normalize_event_type(event_type: str) -> str:
    """
    Normalise un type d'evenement legacy vers le nouveau format.

    Cette fonction permet la retrocompatibilite pendant la migration
    progressive du code backend existant.

    Args:
        event_type: Type d'evenement (legacy ou nouveau format)

    Returns:
        Type d'evenement normalise au nouveau format

    Examples:
        >>> normalize_event_type("llm_stream_start")
        "llm.stream_start"

        >>> normalize_event_type("llm.stream_start")
        "llm.stream_start"

        >>> normalize_event_type("WORKFLOW_CHECKLIST")
        "workflow.step_update"
    """
    return LEGACY_EVENT_MAPPING.get(event_type, event_type)


def get_all_events() -> list[str]:
    """
    Retourne la liste de tous les evenements definis.

    Utile pour la validation et le debugging.

    Returns:
        Liste de tous les noms d'evenements
    """
    events = []

    # Collecte tous les attributs de chaque classe d'evenements
    for event_class in [
        AuthEvents, LLMEvents, UserEvents, CompanyEvents, DashboardEvents,
        JobEvents, ApprovalEvents, ActivityEvents, InvoiceEvents,
        WorkflowEvents, SessionEvents, ConnectionEvents
    ]:
        for attr_name in dir(event_class):
            if not attr_name.startswith('_'):
                events.append(getattr(event_class, attr_name))

    return events


def validate_event_type(event_type: str) -> bool:
    """
    Verifie si un type d'evenement est valide.

    Args:
        event_type: Type d'evenement a valider

    Returns:
        True si l'evenement est valide (nouveau ou legacy), False sinon
    """
    all_events = get_all_events()
    return event_type in all_events or event_type in LEGACY_EVENT_MAPPING


# ============================================
# Event Descriptions (for logging/debugging)
# ============================================

EVENT_DESCRIPTIONS = {
    # Auth Events
    WS_EVENTS.AUTH.LOGIN_SUCCESS: "User login successful",
    WS_EVENTS.AUTH.LOGIN_ERROR: "User login failed",
    WS_EVENTS.AUTH.OAUTH_SUCCESS: "OAuth login successful",
    WS_EVENTS.AUTH.OAUTH_ERROR: "OAuth login failed",
    WS_EVENTS.AUTH.FIREBASE_TOKEN: "Firebase token received",
    WS_EVENTS.AUTH.LOGOUT: "User logged out",
    WS_EVENTS.AUTH.SESSION_CONFIRMED: "Session confirmed by backend",
    WS_EVENTS.AUTH.SESSION_REGISTERED: "Session registered with backend",

    # LLM Events
    WS_EVENTS.LLM.STREAM_START: "LLM response streaming started",
    WS_EVENTS.LLM.STREAM_DELTA: "LLM response chunk received",
    WS_EVENTS.LLM.STREAM_END: "LLM response streaming ended",
    WS_EVENTS.LLM.STREAM_INTERRUPTED: "LLM streaming interrupted by user",
    WS_EVENTS.LLM.STOP_STREAMING: "Request to stop LLM streaming",
    WS_EVENTS.LLM.TOOL_USE_START: "LLM tool call started",
    WS_EVENTS.LLM.TOOL_USE_PROGRESS: "LLM tool call progress update",
    WS_EVENTS.LLM.TOOL_USE_END: "LLM tool call completed",
    WS_EVENTS.LLM.APPROVAL_REQUEST: "LLM requesting approval for action",
    WS_EVENTS.LLM.ERROR: "LLM streaming error occurred",
    WS_EVENTS.LLM.SESSION_READY: "LLM session ready for use",
    WS_EVENTS.LLM.INITIALIZE: "Initialize LLM session",
    WS_EVENTS.LLM.MESSAGE: "LLM message received",
    WS_EVENTS.LLM.RESPONSE: "LLM response received",

    # User Events
    WS_EVENTS.USER.PROFILE: "User profile received",
    WS_EVENTS.USER.FIRST_CONNECT: "First connection welcome with credit",
    WS_EVENTS.USER.SETTINGS_UPDATED: "User settings updated",

    # Company Events
    WS_EVENTS.COMPANY.LIST: "Company list received",
    WS_EVENTS.COMPANY.DETAILS: "Company details received",
    WS_EVENTS.COMPANY.SELECT: "Company selected",

    # Dashboard Events
    WS_EVENTS.DASHBOARD.METRICS_UPDATE: "Dashboard metrics updated",
    WS_EVENTS.DASHBOARD.METRICS: "Dashboard metrics request",
    WS_EVENTS.DASHBOARD.REFRESH: "Dashboard refresh requested",
    WS_EVENTS.DASHBOARD.FULL_DATA: "Dashboard full data request/response",
    WS_EVENTS.DASHBOARD.ORCHESTRATE_INIT: "Dashboard orchestration sequence initialized",
    WS_EVENTS.DASHBOARD.PHASE_START: "Dashboard loading phase started",
    WS_EVENTS.DASHBOARD.PHASE_COMPLETE: "Dashboard loading phase completed",
    WS_EVENTS.DASHBOARD.DATA_LOADING_PROGRESS: "Dashboard data loading progress update",
    WS_EVENTS.DASHBOARD.COMPANY_CHANGE: "Dashboard company selection changed",
    WS_EVENTS.DASHBOARD.SWITCH_ACCOUNT: "Switch between own account and shared accounts",

    # Job Events
    WS_EVENTS.JOB.STATUS_CHANGED: "Job status changed",
    WS_EVENTS.JOB.BATCH_UPDATE: "Multiple jobs updated",

    # Approval Events
    WS_EVENTS.APPROVAL.NEW: "New approval request",
    WS_EVENTS.APPROVAL.STATUS_CHANGED: "Approval status changed",

    # Activity Events
    WS_EVENTS.ACTIVITY.CREATED: "New activity created",

    # Invoice Events
    WS_EVENTS.INVOICE.FIELD_UPDATE: "Invoice field updated",

    # Workflow Events
    WS_EVENTS.WORKFLOW.STEP_UPDATE: "Workflow step updated",

    # Session Events
    WS_EVENTS.SESSION.EXPIRED: "User session expired",
    WS_EVENTS.SESSION.INVALIDATED: "User session invalidated",

    # Connection Events
    WS_EVENTS.CONNECTION.STATUS: "WebSocket connection status changed",
    WS_EVENTS.CONNECTION.ERROR: "WebSocket error occurred",
}


def get_event_description(event_type: str) -> str:
    """
    Retourne la description d'un type d'evenement.

    Args:
        event_type: Type d'evenement

    Returns:
        Description de l'evenement ou message par defaut
    """
    normalized = normalize_event_type(event_type)
    return EVENT_DESCRIPTIONS.get(normalized, f"Unknown event: {event_type}")


# ============================================
# Migration Verification
# ============================================

def verify_sync_with_frontend():
    """
    Fonction utilitaire pour verifier la synchronisation avec le frontend.

    A executer lors des tests pour s'assurer que tous les evenements
    frontend sont bien presents dans ce fichier.

    Note: Cette fonction necessite l'acces au fichier TypeScript frontend
    pour une verification complete. Dans un environnement de production,
    cette verification devrait etre faite via des tests d'integration.
    """
    all_events = get_all_events()
    print(f"Total events defined: {len(all_events)}")
    print("\nEvent breakdown by category:")
    print(f"  AUTH: {len([e for e in all_events if e.startswith('auth.')])}")
    print(f"  LLM: {len([e for e in all_events if e.startswith('llm.')])}")
    print(f"  COMPANY: {len([e for e in all_events if e.startswith('company.')])}")
    print(f"  DASHBOARD: {len([e for e in all_events if e.startswith('dashboard.')])}")
    print(f"  JOB: {len([e for e in all_events if e.startswith('job.')])}")
    print(f"  APPROVAL: {len([e for e in all_events if e.startswith('approval.')])}")
    print(f"  ACTIVITY: {len([e for e in all_events if e.startswith('activity.')])}")
    print(f"  INVOICE: {len([e for e in all_events if e.startswith('invoice.')])}")
    print(f"  WORKFLOW: {len([e for e in all_events if e.startswith('workflow.')])}")
    print(f"  SESSION: {len([e for e in all_events if e.startswith('session.')])}")
    print(f"  CONNECTION: {len([e for e in all_events if 'connection' in e or e == 'error'])}")
    print(f"\nLegacy events mapped: {len(LEGACY_EVENT_MAPPING)}")


# ============================================
# Exports
# ============================================

__all__ = [
    'WS_EVENTS',
    'AuthEvents',
    'LLMEvents',
    'UserEvents',
    'CompanyEvents',
    'DashboardEvents',
    'JobEvents',
    'TaskEvents',
    'ApprovalEvents',
    'ActivityEvents',
    'InvoiceEvents',
    'WorkflowEvents',
    'SessionEvents',
    'ConnectionEvents',
    'PageStateEvents',      # NEW
    'PendingActionEvents',  # NEW
    'BalanceEvents',        # NEW
    'ChatEvents',           # NEW: Chat session management
    'RoutingEvents',        # NEW: Document routing management
    'NotificationEvents',   # NEW: Notifications from jobbeurs
    'MessengerEvents',      # NEW: Direct messages (RTDB)
    'StaticDataEvents',     # NEW: Static dropdown data
    'CompanySettingsEvents',  # NEW: Company settings page
    'COAEvents',            # NEW: Chart of Accounts page
    'BankingEvents',        # NEW: Banking transactions page
    'MetricsEvents',        # NEW: Shared metrics stores
    'LEGACY_EVENT_MAPPING',
    'EVENT_DESCRIPTIONS',
    'normalize_event_type',
    'validate_event_type',
    'get_all_events',
    'get_event_description',
    'verify_sync_with_frontend',
]
