"""
Onboarding Handlers
===================

Backend handlers for the company onboarding flow in Next.js frontend.

NAMESPACE: ONBOARDING

This module provides:
- ERP connection testing (Odoo, Banana)
- Client management (for managing companies on behalf of others)
- Form submission and company creation
- OAuth flows for Google Drive/Chat

Architecture:
    Frontend (Next.js) -> wsClient.send({type: "onboarding.*", ...})
                       -> WebSocket Hub
                       -> handlers.py
                       -> Firebase | ERP Services
"""

import logging
import uuid
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class OnboardingHandlers:
    """
    Handlers for onboarding operations.

    Provides:
    - test_erp_connection: Test ERP connectivity
    - load_clients: Load client list for mandataires
    - save_client: Create new client
    - update_client: Update existing client
    - delete_client: Delete client (with security check)
    """

    def __init__(self):
        self._start_time: Optional[datetime] = None

    def _elapsed_ms(self, start_time: datetime) -> int:
        """Calculate elapsed time in milliseconds."""
        return int((datetime.utcnow() - start_time).total_seconds() * 1000)

    # ============================================
    # ERP CONNECTION
    # ============================================

    async def test_erp_connection(
        self,
        user_id: str,
        erp_type: str,
        connection_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Test ERP connection during onboarding.

        RPC: ONBOARDING.test_erp_connection

        Args:
            user_id: Firebase UID
            erp_type: ERP type (odoo, banana, etc.)
            connection_data: Connection configuration data
                - url: ERP server URL
                - database: Database name
                - username: Username
                - apiKey: API key (for testing only, not stored)

        Returns:
            {
                "success": True,
                "connected": True/False,
                "message": "...",
                "details": {...}  # Optional additional info
            }
        """
        start_time = datetime.utcnow()

        try:
            logger.info(
                f"ONBOARDING.test_erp_connection "
                f"user_id={user_id} erp_type={erp_type}"
            )

            if erp_type == "odoo":
                return await self._test_odoo_connection(connection_data, start_time)
            elif erp_type == "banana":
                return await self._test_banana_connection(connection_data, start_time)
            else:
                return {
                    "success": True,
                    "connected": False,
                    "message": f"Connection test not implemented for {erp_type}",
                    "durationMs": self._elapsed_ms(start_time)
                }

        except Exception as e:
            logger.error(f"ONBOARDING.test_erp_connection error: {e}")
            return {
                "success": False,
                "connected": False,
                "error": str(e)
            }

    async def _test_odoo_connection(
        self,
        connection_data: Dict[str, Any],
        start_time: datetime
    ) -> Dict[str, Any]:
        """
        Test Odoo XML-RPC connection.

        Uses the ERP service singleton to verify connectivity.
        """
        try:
            url = connection_data.get("url", "")
            database = connection_data.get("database", "")
            username = connection_data.get("username", "")
            api_key = connection_data.get("apiKey", "")

            # Validate required fields
            if not all([url, database, username, api_key]):
                return {
                    "success": True,
                    "connected": False,
                    "message": "Missing required connection parameters",
                    "durationMs": self._elapsed_ms(start_time)
                }

            # Normalize URL
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"

            # Try to connect using xmlrpc
            try:
                import xmlrpc.client

                # Test common endpoint
                common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)

                # Authenticate
                uid = common.authenticate(database, username, api_key, {})

                if uid:
                    # Get server version for additional info
                    try:
                        version_info = common.version()
                        server_version = version_info.get("server_version", "unknown")
                    except Exception:
                        server_version = "unknown"

                    return {
                        "success": True,
                        "connected": True,
                        "message": f"Successfully connected to Odoo (UID: {uid})",
                        "details": {
                            "url": url,
                            "database": database,
                            "userId": uid,
                            "serverVersion": server_version
                        },
                        "durationMs": self._elapsed_ms(start_time)
                    }
                else:
                    return {
                        "success": True,
                        "connected": False,
                        "message": "Authentication failed - invalid credentials",
                        "durationMs": self._elapsed_ms(start_time)
                    }

            except xmlrpc.client.Fault as fault:
                return {
                    "success": True,
                    "connected": False,
                    "message": f"Odoo error: {fault.faultString}",
                    "durationMs": self._elapsed_ms(start_time)
                }
            except Exception as conn_error:
                error_msg = str(conn_error)
                if "Connection refused" in error_msg:
                    message = "Connection refused - server may be down or URL incorrect"
                elif "Name or service not known" in error_msg or "getaddrinfo failed" in error_msg:
                    message = "Invalid URL - server not found"
                elif "timed out" in error_msg.lower():
                    message = "Connection timeout - server not responding"
                else:
                    message = f"Connection error: {error_msg}"

                return {
                    "success": True,
                    "connected": False,
                    "message": message,
                    "durationMs": self._elapsed_ms(start_time)
                }

        except Exception as e:
            logger.error(f"ONBOARDING._test_odoo_connection error: {e}")
            return {
                "success": False,
                "connected": False,
                "error": str(e),
                "durationMs": self._elapsed_ms(start_time)
            }

    async def _test_banana_connection(
        self,
        connection_data: Dict[str, Any],
        start_time: datetime
    ) -> Dict[str, Any]:
        """
        Test Banana accounting connection.

        Banana doesn't have a direct API - returns placeholder.
        """
        return {
            "success": True,
            "connected": True,
            "message": "Banana connection test - local file-based accounting (no remote verification needed)",
            "durationMs": self._elapsed_ms(start_time)
        }

    # ============================================
    # CLIENT MANAGEMENT
    # ============================================

    async def load_clients(self, user_id: str) -> Dict[str, Any]:
        """
        Load client list for a user (mandataire).

        Clients are stored in: clients/{user_id}/bo_clients/{client_uuid}

        Returns:
            {"success": True, "clients": [...]} or {"success": False, "error": "..."}
        """
        try:
            from app.firebase_client import get_firestore

            logger.info(f"ONBOARDING.load_clients user_id={user_id}")

            db = get_firestore()
            clients = []

            # Query all clients for this user
            clients_ref = db.collection(f"clients/{user_id}/bo_clients")
            docs = clients_ref.stream()

            for doc in docs:
                data = doc.to_dict()
                clients.append({
                    "uuid": doc.id,
                    "name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
                    "firstName": data.get("first_name", ""),
                    "lastName": data.get("last_name", ""),
                    "email": data.get("email", ""),
                    "phone": data.get("phone", ""),
                    "address": data.get("address", ""),
                })

            logger.info(f"ONBOARDING.load_clients found {len(clients)} clients")

            return {
                "success": True,
                "clients": clients
            }

        except Exception as e:
            logger.error(f"ONBOARDING.load_clients error: {e}")
            return {
                "success": False,
                "error": str(e),
                "clients": []
            }

    async def save_client(
        self,
        user_id: str,
        client_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save a new client.

        Path: clients/{user_id}/bo_clients/{new_uuid}

        Args:
            user_id: Firebase UID
            client_data: Client information
                - firstName: First name
                - lastName: Last name
                - email: Email address
                - phone: Phone number (optional)
                - address: Address (optional)

        Returns:
            {"success": True, "client": {...}} or {"success": False, "error": "..."}
        """
        try:
            from app.firebase_client import get_firestore
            import uuid

            logger.info(f"ONBOARDING.save_client user_id={user_id}")

            db = get_firestore()

            # Generate new UUID
            client_uuid = str(uuid.uuid4())

            # Prepare document
            doc_data = {
                "first_name": client_data.get("firstName", ""),
                "last_name": client_data.get("lastName", ""),
                "email": client_data.get("email", ""),
                "phone": client_data.get("phone", ""),
                "address": client_data.get("address", ""),
                "created_at": datetime.utcnow().isoformat(),
                "created_by": user_id,
            }

            # Save to Firestore
            doc_ref = db.collection(f"clients/{user_id}/bo_clients").document(client_uuid)
            doc_ref.set(doc_data)

            # Return created client
            client = {
                "uuid": client_uuid,
                "name": f"{doc_data['first_name']} {doc_data['last_name']}".strip(),
                "firstName": doc_data["first_name"],
                "lastName": doc_data["last_name"],
                "email": doc_data["email"],
                "phone": doc_data["phone"],
                "address": doc_data["address"],
            }

            logger.info(f"ONBOARDING.save_client created client_uuid={client_uuid}")

            return {
                "success": True,
                "client": client
            }

        except Exception as e:
            logger.error(f"ONBOARDING.save_client error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def update_client(
        self,
        user_id: str,
        client_uuid: str,
        client_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update an existing client.

        Path: clients/{user_id}/bo_clients/{client_uuid}

        Returns:
            {"success": True, "client": {...}} or {"success": False, "error": "..."}
        """
        try:
            from app.firebase_client import get_firestore

            logger.info(f"ONBOARDING.update_client user_id={user_id} client_uuid={client_uuid}")

            db = get_firestore()

            # Prepare update data
            update_data = {
                "first_name": client_data.get("firstName", ""),
                "last_name": client_data.get("lastName", ""),
                "email": client_data.get("email", ""),
                "phone": client_data.get("phone", ""),
                "address": client_data.get("address", ""),
                "updated_at": datetime.utcnow().isoformat(),
            }

            # Update document
            doc_ref = db.collection(f"clients/{user_id}/bo_clients").document(client_uuid)
            doc_ref.update(update_data)

            # Return updated client
            client = {
                "uuid": client_uuid,
                "name": f"{update_data['first_name']} {update_data['last_name']}".strip(),
                "firstName": update_data["first_name"],
                "lastName": update_data["last_name"],
                "email": update_data["email"],
                "phone": update_data["phone"],
                "address": update_data["address"],
            }

            logger.info(f"ONBOARDING.update_client updated client_uuid={client_uuid}")

            return {
                "success": True,
                "client": client
            }

        except Exception as e:
            logger.error(f"ONBOARDING.update_client error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def delete_client(
        self,
        user_id: str,
        client_uuid: str,
    ) -> Dict[str, Any]:
        """
        Delete a client.

        SECURITY: Cannot delete if client has associated companies (mandates).

        Path: clients/{user_id}/bo_clients/{client_uuid}

        Returns:
            {"success": True} or {"success": False, "error": "...", "hasCompanies": True}
        """
        try:
            from app.firebase_client import get_firestore

            logger.info(f"ONBOARDING.delete_client user_id={user_id} client_uuid={client_uuid}")

            db = get_firestore()

            # Security check: verify client has no associated companies
            # Check if any mandate references this client_uuid
            mandates_ref = db.collection(f"clients/{user_id}/mandates")
            query = mandates_ref.where("client_uuid", "==", client_uuid).limit(1)
            results = list(query.stream())

            if results:
                logger.warning(
                    f"ONBOARDING.delete_client blocked - client has companies: "
                    f"user_id={user_id} client_uuid={client_uuid}"
                )
                return {
                    "success": False,
                    "error": "Cannot delete client with associated companies",
                    "hasCompanies": True
                }

            # Delete the client
            doc_ref = db.collection(f"clients/{user_id}/bo_clients").document(client_uuid)
            doc_ref.delete()

            logger.info(f"ONBOARDING.delete_client deleted client_uuid={client_uuid}")

            return {"success": True}

        except Exception as e:
            logger.error(f"ONBOARDING.delete_client error: {e}")
            return {
                "success": False,
                "error": str(e)
            }


    # ============================================
    # ONBOARDING SUBMISSION
    # ============================================

    async def submit_onboarding(
        self,
        user_id: str,
        session_id: str,
        onboarding_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process complete onboarding form submission.

        Uses ONBOARDING_MANAGEMENT from pinnokio_app/logique_metier/onboarding_flow.py
        to ensure correct field mapping and document structure.

        RPC: ONBOARDING.submit

        Args:
            user_id: Firebase UID
            session_id: WebSocket session ID
            onboarding_data: Complete form data (frontend format)

        Returns:
            {
                "success": True,
                "mandate_path": "clients/{uid}/bo_clients/{client}/mandates/{company}",
                "client_uuid": "...",
                "requires_oauth": True/False,
            }
        """
        start_time = datetime.utcnow()

        try:
            logger.info("─" * 60)
            logger.info("[HANDLER] 📋 submit_onboarding DÉMARRÉ (via ONBOARDING_MANAGEMENT)")
            logger.info(f"[HANDLER] user_id={user_id}")
            logger.info(f"[HANDLER] session_id={session_id}")
            logger.info(f"[HANDLER] onboarding_data keys: {list(onboarding_data.keys()) if onboarding_data else 'VIDE'}")

            # 1. Validate onboarding data
            logger.info("[HANDLER] Étape 1: Validation des données...")
            validation = self._validate_onboarding_data(onboarding_data)
            if not validation.get("valid"):
                logger.error(f"[HANDLER] ❌ Validation ÉCHOUÉE: {validation.get('error')}")
                return {
                    "success": False,
                    "error": validation.get("error", "Invalid onboarding data"),
                    "durationMs": self._elapsed_ms(start_time)
                }
            logger.info("[HANDLER] ✅ Validation réussie")

            # 2. Transform frontend data to ONBOARDING_MANAGEMENT format
            logger.info("[HANDLER] Étape 2: Transformation des données pour ONBOARDING_MANAGEMENT...")
            fb_data = self._transform_to_fb_data_format(onboarding_data, user_id)
            logger.info(f"[HANDLER]   fb_data keys: {list(fb_data.keys())}")

            # Extract key configuration for later
            systems_config = onboarding_data.get("systemsConfig", {})
            dms_type = systems_config.get("dmsType", "google_drive")
            chat_type = systems_config.get("chatType", "pinnokio")
            logger.info(f"[HANDLER]   dms_type: {dms_type}")
            logger.info(f"[HANDLER]   chat_type: {chat_type}")

            # 2.5 Generate initial_context_data and job_id
            logger.info("[HANDLER] Étape 2.5: Génération initial_context_data et job_id...")
            initial_context_data = self._generate_business_context_text(fb_data)
            job_id = self._generate_job_id(fb_data)

            # Add to fb_data (matching OnboardingState reference)
            fb_data["initial_context_data"] = initial_context_data
            fb_data["job_id"] = job_id
            logger.info(f"[HANDLER]   initial_context_data length: {len(initial_context_data)} chars")
            logger.info(f"[HANDLER]   job_id: {job_id}")

            # 2.6 Save to temp_data (matching OnboardingState.submit_onboarding reference)
            logger.info("[HANDLER] Étape 2.6: Sauvegarde temp_data...")
            try:
                from app.firebase_client import get_firestore
                db = get_firestore()
                temp_data_path = f"clients/{user_id}/temp_data/onboarding"
                db.document(temp_data_path).set(fb_data, merge=True)
                logger.info(f"[HANDLER]   ✅ temp_data sauvegardé: {temp_data_path}")
            except Exception as temp_err:
                logger.warning(f"[HANDLER]   ⚠️ temp_data sauvegarde échouée (non bloquant): {temp_err}")

            # 3. Use ONBOARDING_MANAGEMENT to create client and mandate
            logger.info("[HANDLER] Étape 3: Création via ONBOARDING_MANAGEMENT.process_onboarding()...")
            try:
                from pinnokio_app.logique_metier.onboarding_flow import ONBOARDING_MANAGEMENT

                onboarding_mgmt = ONBOARDING_MANAGEMENT(fb_data=fb_data)
                client_uuid, client_mandat_id, mandates_path, erp_path = await onboarding_mgmt.process_onboarding()

                logger.info(f"[HANDLER] ✅ ONBOARDING_MANAGEMENT.process_onboarding() SUCCESS")
                logger.info(f"[HANDLER]   client_uuid: {client_uuid}")
                logger.info(f"[HANDLER]   client_mandat_id: {client_mandat_id}")
                logger.info(f"[HANDLER]   mandates_path: {mandates_path}")
                logger.info(f"[HANDLER]   erp_path: {erp_path}")

            except Exception as mgmt_err:
                logger.error(f"[HANDLER] ❌ ONBOARDING_MANAGEMENT.process_onboarding() FAILED: {mgmt_err}")
                return {
                    "success": False,
                    "error": f"Failed to create mandate: {str(mgmt_err)}",
                    "durationMs": self._elapsed_ms(start_time)
                }

            # 4. Determine if OAuth is required
            requires_oauth = dms_type in ["google_drive"] or chat_type in ["google_chat"]
            logger.info(f"[HANDLER] Étape 4: OAuth requis? {requires_oauth}")

            logger.info("─" * 60)
            logger.info("[HANDLER] ✅ submit_onboarding TERMINÉ AVEC SUCCÈS")
            logger.info(f"[HANDLER]   mandate_path: {mandates_path}")
            logger.info(f"[HANDLER]   requires_oauth: {requires_oauth}")
            logger.info(f"[HANDLER]   duration: {self._elapsed_ms(start_time)}ms")
            logger.info("─" * 60)

            return {
                "success": True,
                "mandate_path": mandates_path,
                "mandate_id": client_mandat_id,
                "client_uuid": client_uuid,
                "requires_oauth": requires_oauth,
                "dms_type": dms_type,
                "chat_type": chat_type,
                "job_id": job_id,
                "durationMs": self._elapsed_ms(start_time)
            }

        except Exception as e:
            logger.error("─" * 60)
            logger.error(f"[HANDLER] ❌ EXCEPTION submit_onboarding: {e}")
            logger.error("─" * 60, exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "durationMs": self._elapsed_ms(start_time)
            }

    def _transform_to_fb_data_format(
        self,
        onboarding_data: Dict[str, Any],
        user_id: str
    ) -> Dict[str, Any]:
        """
        Transform frontend onboarding data to the format expected by ONBOARDING_MANAGEMENT.

        Frontend format (camelCase) -> Backend format (snake_case)
        
        This format matches the reference implementation in:
        pinnokio_app/state/OnboardingState.py -> compile_onboarding_data()
        """
        base_info = onboarding_data.get("baseInfo", {})
        business_details = onboarding_data.get("businessDetails", {})
        systems_config = onboarding_data.get("systemsConfig", {})

        # Determine client_name based on ownership type and new client data
        ownership_type = base_info.get("ownershipType", "I own this company")
        new_client = base_info.get("newClient", {})
        
        if ownership_type == "I'm managing this company for someone else" and new_client:
            client_name = f"{new_client.get('firstName', '')} {new_client.get('lastName', '')}".strip()
            client_email = new_client.get("email", "")
        else:
            client_name = base_info.get("companyName") or base_info.get("businessName") or ""
            client_email = base_info.get("email", "")

        # Map frontend fields to backend fb_data format (matching OnboardingState.compile_onboarding_data)
        fb_data = {
            # Top-level entity type
            "entity_type": base_info.get("entityType", "Company"),
            
            "base_info": {
                "client_uuid": base_info.get("clientUuid", ""),
                "client_email": client_email,
                "client_name": client_name,
                "business_name": base_info.get("businessName", ""),
                "company_name": base_info.get("companyName", ""),
                "email": base_info.get("email", ""),
                "phone_number": base_info.get("phoneNumber", ""),
                "website": base_info.get("website", ""),
                "address": base_info.get("address", ""),
                "country": base_info.get("country", ""),
                "legal_status": base_info.get("legalForm", ""),
                "language": base_info.get("language", "en"),
                "ownership_type": ownership_type,
                # For manager mode with new client
                "first_name": new_client.get("firstName", "") if new_client else "",
                "last_name": new_client.get("lastName", "") if new_client else "",
            },
            
            "business_details": {
                # Core business info
                "selling_type": business_details.get("sellingType", "Both"),
                "invoicing_methods": business_details.get("invoicingMethods", ""),
                "recurring_invoices": business_details.get("recurringInvoices", False),
                "order_invoices": business_details.get("orderInvoices", False),
                "base_currency": business_details.get("baseCurrency", "CHF"),
                "currency_id": business_details.get("currencyId"),
                "business_activity_details": business_details.get("businessActivityDetails", ""),
                
                # VAT info
                "vat_info": {
                    "has_vat": business_details.get("hasVat", False),
                    "vat_number": business_details.get("vatNumber", ""),
                },
                
                # Employees
                "employees": {
                    "has_employees": business_details.get("hasEmployees", False),
                    "details": business_details.get("employeesDetails", ""),
                },
                
                # Inventory/Stock
                "inventory": {
                    "has_stock": business_details.get("hasStock", False),
                    "management_details": business_details.get("stockManagement", ""),
                },
                
                # Rent
                "rent": {
                    "has_rent": business_details.get("hasRent", False),
                },
                
                # Specific taxes
                "specific_taxes": {
                    "has_specific_taxes": business_details.get("hasSpecificTaxes", False),
                    "details": business_details.get("specificTaxesDetails", ""),
                },
                
                # Personal expenses
                "personal_expenses": {
                    "has_personal_expenses": business_details.get("hasPersonalExpenses", False),
                    "details": business_details.get("personalExpensesDetails", ""),
                },
                
                # Fixed assets / Immobilisations
                "assets": {
                    "asset_management_activated": business_details.get("assetManagementActivated", False),
                    "details": business_details.get("assetManagementDetails", ""),
                },
            },
            
            "accounting_systems": {
                "accounting_system": systems_config.get("accountingSystem", "pinnokio"),
                "accounting_system_id": systems_config.get("accountingSystemId"),
                "accounting_api_key": systems_config.get("odooDetails", {}).get("apiKey", ""),
                # Transform odooDetails from camelCase to snake_case
                "odoo_details": {
                    "company_name": systems_config.get("odooDetails", {}).get("companyName", ""),
                    "database_name": systems_config.get("odooDetails", {}).get("databaseName", ""),
                    "url": systems_config.get("odooDetails", {}).get("url", ""),
                    "username": systems_config.get("odooDetails", {}).get("username", ""),
                },
            },
            
            "system_details": {
                "dms": {
                    "type": systems_config.get("dmsType", "google_drive"),
                    "dms_id": systems_config.get("dmsId"),
                },
                "chat": {
                    "type": systems_config.get("chatType", "pinnokio"),
                    "chat_id": systems_config.get("chatId"),
                },
            },
            
            "metadata": {
                "completed_at": datetime.utcnow().isoformat(),
                "user_id": user_id,
            },
            
            # Analysis method for account analysis
            "analysis_method": {
                "method": business_details.get("accountAnalysisMethod", "based_on_coa"),
            },
        }

        return fb_data

    def _validate_onboarding_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate onboarding data structure."""
        try:
            base_info = data.get("baseInfo", {})
            systems_config = data.get("systemsConfig", {})

            # Required fields
            if not base_info.get("entityType"):
                return {"valid": False, "error": "Entity type is required"}
            if not base_info.get("country"):
                return {"valid": False, "error": "Country is required"}
            if not base_info.get("language"):
                return {"valid": False, "error": "Language is required"}
            if not (base_info.get("companyName") or base_info.get("businessName")):
                return {"valid": False, "error": "Company or business name is required"}
            if not systems_config.get("accountingSystem"):
                return {"valid": False, "error": "Accounting system is required"}

            return {"valid": True}

        except Exception as e:
            return {"valid": False, "error": str(e)}

    def _generate_business_context_text(self, fb_data: Dict[str, Any]) -> str:
        """
        Generate a descriptive text of the company's initial context.

        Replicates the logic from OnboardingState.generate_business_context_text()
        in the old Flet app.

        Args:
            fb_data: The transformed onboarding data in backend format

        Returns:
            A detailed textual description of the company context
        """
        try:
            base_info = fb_data.get("base_info", {})
            business_details = fb_data.get("business_details", {})
            accounting_systems = fb_data.get("accounting_systems", {})
            metadata = fb_data.get("metadata", {})

            # Build context text
            context_text = f"""Contexte de l'Entreprise

La société {base_info.get('business_name') or base_info.get('company_name', 'Non renseigné')} est une entité de type {fb_data.get('entity_type', 'Non renseigné')}
située en {base_info.get('country', 'Non renseigné')}, dirigée par {base_info.get('client_name', 'Non renseigné')}.
Statut de propriété: {"Propriétaire" if base_info.get('ownership_type') == 'I own this company' else "Gestionnaire pour le compte d'un tiers"}

Informations Financières:
- Devise de base: {business_details.get('base_currency', 'CHF')}

Informations de Contact:
- Email: {base_info.get('email', 'Non renseigné')}
- Téléphone: {base_info.get('phone_number', 'Non renseigné')}
- Adresse: {base_info.get('address', 'Non renseigné')}
- Site Web: {base_info.get('website', 'Non renseigné')}

Description de l'Activité:
{f"* {business_details.get('business_activity_details')}" if business_details.get('business_activity_details') else "* Aucune description fournie"}

Profil d'Activité:
- Type de vente: {business_details.get('selling_type', 'Non renseigné')}
- Méthodes de facturation: {business_details.get('invoicing_methods') or 'Non renseigné'}

Aspects Fiscaux et Réglementaires:
- Statut TVA: {'Assujetti' if business_details.get('vat_info', {}).get('has_vat') else 'Non assujetti'}
* Numéro de TVA: {business_details.get('vat_info', {}).get('vat_number', 'Non renseigné')}

Ressources Humaines:
- Présence d'employés: {'Oui' if business_details.get('employees', {}).get('has_employees') else 'Non'}
{f"* Détails: {business_details.get('employees', {}).get('details')}" if business_details.get('employees', {}).get('has_employees') else ''}

Gestion des Stocks:
- Gestion de stock: {'Oui' if business_details.get('inventory', {}).get('has_stock') else 'Non'}
{f"* Détails de gestion: {business_details.get('inventory', {}).get('management_details')}" if business_details.get('inventory', {}).get('has_stock') else ''}

Systèmes Comptables:
- Système comptable: {accounting_systems.get('accounting_system', 'Non renseigné')}

Aspects Financiers Complémentaires:
- Loyers: {'Présents' if business_details.get('rent', {}).get('has_rent') else 'Aucun'}
- Taxes spécifiques: {'Oui' if business_details.get('specific_taxes', {}).get('has_specific_taxes') else 'Non'}
{f"* Détails: {business_details.get('specific_taxes', {}).get('details')}" if business_details.get('specific_taxes', {}).get('has_specific_taxes') else ''}
- Dépenses personnelles: {'Oui' if business_details.get('personal_expenses', {}).get('has_personal_expenses') else 'Non'}
{f"* Détails: {business_details.get('personal_expenses', {}).get('details')}" if business_details.get('personal_expenses', {}).get('has_personal_expenses') else ''}

Préparé le: {metadata.get('completed_at', datetime.utcnow().isoformat())}
"""
            logger.info(f"[HANDLER] initial_context_data généré: {context_text[:200]}...")
            return context_text

        except Exception as e:
            logger.error(f"[HANDLER] Erreur dans _generate_business_context_text: {e}")
            return f"Erreur lors de la génération du contexte: {str(e)}"

    def _generate_job_id(self, fb_data: Dict[str, Any]) -> str:
        """
        Generate a unique job_id for the onboarding.

        Format: {space_name}_{uuid4}

        This job_id is used for:
        - Tracking the onboarding workflow
        - Redirect URL construction after onboarding (to chat)
        - Account analysis process identification

        Args:
            fb_data: The transformed onboarding data

        Returns:
            A unique job_id string
        """
        base_info = fb_data.get("base_info", {})
        space_name = (
            base_info.get("business_name") or
            base_info.get("company_name") or
            "company"
        )
        # Normalize space_name (replace spaces, special chars)
        space_name_normalized = space_name.replace(" ", "_").replace("-", "_").lower()
        job_id = f"{space_name_normalized}_{uuid.uuid4()}"
        logger.info(f"[HANDLER] job_id généré: {job_id}")
        return job_id

    # NOTE: _create_self_client and create_mandate_in_firebase removed
    # These are now handled by ONBOARDING_MANAGEMENT.process_onboarding()
    # from pinnokio_app/logique_metier/onboarding_flow.py

    async def create_dms_structure(
        self,
        user_id: str,
        mandate_path: str,
        dms_type: str,
        command_args: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create the DMS folder structure for the new company.

        Uses DMS_CREATION from pinnokio_app/logique_metier/onboarding_flow.py
        with command='create_mandate' to create:
        - Client parent folder
        - Company space folder
        - Recursive structure from schema JSON (drive_manager.json)
        - GCS folders (setup/coa, setup/gl_journals, etc.)

        Returns:
            {
                "success": True,
                "root_folder_id": "...",
                "folders_created": {...}
            }
        """
        try:
            logger.info("─" * 60)
            logger.info("[HANDLER] 📂 create_dms_structure DÉMARRÉ (via DMS_CREATION)")
            logger.info(f"[HANDLER]   user_id: {user_id}")
            logger.info(f"[HANDLER]   mandate_path: {mandate_path}")
            logger.info(f"[HANDLER]   dms_type: {dms_type}")

            if dms_type != "google_drive":
                logger.info(f"[HANDLER] ℹ️ DMS type '{dms_type}' - création de dossiers ignorée")
                return {
                    "success": True,
                    "root_folder_id": None,
                    "folders_created": [],
                    "skipped": True
                }

            # Get mandate details from Firebase
            logger.info("[HANDLER] Récupération des détails du mandate...")
            from app.firebase_client import get_firestore
            db = get_firestore()
            mandate_doc = db.document(mandate_path).get()

            if not mandate_doc.exists:
                logger.error("[HANDLER] ❌ Mandate non trouvé!")
                return {
                    "success": False,
                    "error": "Mandate not found"
                }

            mandate_data = mandate_doc.to_dict()
            company_name = mandate_data.get("contact_space_name") or mandate_data.get("legal_name", "New Company")
            client_uuid = mandate_data.get("client_uuid", "")
            email = mandate_data.get("email", "")

            # Extract mandate_id and client_doc_id from mandate_path
            # Format: clients/{user_id}/bo_clients/{client_doc_id}/mandates/{mandate_id}
            path_parts = mandate_path.split("/")
            client_doc_id = path_parts[3] if len(path_parts) > 3 else ""
            mandate_id = path_parts[5] if len(path_parts) > 5 else ""

            # ⭐ CORRECTION: Récupérer client_name depuis le document client (pas company_name)
            client_path = f"clients/{user_id}/bo_clients/{client_doc_id}"
            client_doc = db.document(client_path).get()
            if client_doc.exists:
                client_data = client_doc.to_dict()
                client_name = client_data.get("client_name", company_name)  # Fallback sur company_name si absent
            else:
                logger.warning(f"[HANDLER] ⚠️ Document client non trouvé: {client_path}, utilisation de company_name")
                client_name = company_name

            logger.info(f"[HANDLER]   company_name: {company_name}")
            logger.info(f"[HANDLER]   client_name: {client_name} (pour drive_client_parent_id)")
            logger.info(f"[HANDLER]   client_uuid: {client_uuid}")
            logger.info(f"[HANDLER]   client_doc_id: {client_doc_id}")
            logger.info(f"[HANDLER]   mandate_id: {mandate_id}")

            # Build command_args for DMS_CREATION
            dms_command_args = {
                "client_name": client_name,    # ⭐ CORRECTION: Utiliser client_name (nom utilisateur) pour le dossier parent
                "space_name": company_name,    # Utiliser company_name (nom société) pour l'espace/mandat
                "specific_year": datetime.utcnow().year,
                "share_email": email,
                "communication_mode": mandate_data.get("chat_type", "pinnokio"),
            }
            logger.info(f"[HANDLER]   dms_command_args: {dms_command_args}")

            # Use DMS_CREATION from onboarding_flow.py
            logger.info("[HANDLER] Initialisation DMS_CREATION avec command='create_mandate'...")
            try:
                from pinnokio_app.logique_metier.onboarding_flow import DMS_CREATION

                dms_instance = DMS_CREATION(
                    dms_type=dms_type,
                    command="create_mandate",
                    mandates_path=mandate_path,
                    user_mail=email,
                    command_args=dms_command_args,
                    firebase_user_id=user_id,
                    client_uuid=client_uuid,
                    client_mandat_doc_id=mandate_id,
                )

                # DMS_CREATION stores the result in firebase_create_mandate_template
                firebase_template = getattr(dms_instance, 'firebase_create_mandate_template', {})
                logger.info(f"[HANDLER] ✅ DMS_CREATION SUCCESS")
                logger.info(f"[HANDLER]   firebase_template: {firebase_template}")

                return {
                    "success": True,
                    "root_folder_id": firebase_template.get("drive_space_parent_id"),
                    "folders_created": firebase_template,
                }

            except Exception as dms_err:
                logger.error(f"[HANDLER] ❌ DMS_CREATION FAILED: {dms_err}")
                return {
                    "success": False,
                    "error": f"DMS creation failed: {str(dms_err)}"
                }

        except Exception as e:
            logger.error("─" * 60)
            logger.error(f"[HANDLER] ❌ EXCEPTION create_dms_structure: {e}")
            logger.error("─" * 60, exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def initiate_oauth_flow(
        self,
        user_id: str,
        session_id: str,
        dms_type: str,
        chat_type: str,
        mandate_path: str,
        return_path: str = "/chat"
    ) -> Dict[str, Any]:
        """
        Initiate Google OAuth flow for Drive/Chat.

        Uses GoogleAuthManager from pinnokio_app/logique_metier/onboarding_flow.py
        which reads credentials from GOOGLE_AUTH2_KEY secret.

        Args:
            user_id: Firebase UID
            session_id: WebSocket session ID
            dms_type: DMS type (google_drive, etc.)
            chat_type: Chat type (google_chat, pinnokio, telegram)
            mandate_path: Path to mandate document
            return_path: Path to redirect after OAuth

        Returns:
            {
                "success": True,
                "auth_url": "https://accounts.google.com/o/oauth2/auth?...",
                "state_token": "..."
            }
        """
        try:
            logger.info("─" * 60)
            logger.info("[HANDLER] 🔐 initiate_oauth_flow DÉMARRÉ (via GoogleAuthManager)")
            logger.info(f"[HANDLER]   user_id: {user_id}")
            logger.info(f"[HANDLER]   session_id: {session_id}")
            logger.info(f"[HANDLER]   dms_type: {dms_type}")
            logger.info(f"[HANDLER]   chat_type: {chat_type}")
            logger.info(f"[HANDLER]   mandate_path: {mandate_path}")

            # 1. Determine if OAuth is needed
            requires_google_drive = dms_type == "google_drive"
            requires_google_chat = chat_type == "google_chat"

            if not requires_google_drive and not requires_google_chat:
                logger.info("[HANDLER] ℹ️ Aucun OAuth Google requis")
                return {
                    "success": True,
                    "requires_oauth": False,
                    "message": "No OAuth required for this configuration"
                }

            # 2. Save pending action for callback
            logger.info("[HANDLER] Étape 1: Sauvegarde pending_action...")
            from app.wrappers.pending_action_manager import get_pending_action_manager
            import os
            import json
            import base64

            pending_manager = get_pending_action_manager()
            state_token = pending_manager.save_pending_action(
                uid=user_id,
                session_id=session_id,
                action_type="oauth",
                provider="google_drive",
                return_page="onboarding",
                return_path=return_path,
                context={
                    "mandate_path": mandate_path,
                    "dms_type": dms_type,
                    "chat_type": chat_type,
                    "step": "oauth_complete",
                    "session_id": session_id,
                }
            )
            logger.info(f"[HANDLER]   state_token: {state_token}")

            # 3. Use GoogleAuthManager to generate authorization URL
            logger.info("[HANDLER] Étape 2: Utilisation de GoogleAuthManager...")
            try:
                from pinnokio_app.logique_metier.onboarding_flow import GoogleAuthManager

                auth_manager = GoogleAuthManager(user_id=user_id)

                # Configure scopes based on what's needed
                if requires_google_drive and not requires_google_chat:
                    # Drive only - use minimal scopes
                    auth_manager.set_drive_only_scopes()
                    logger.info("[HANDLER]   Scopes: Drive only")
                else:
                    # Update scopes based on choices
                    auth_manager.update_scopes_for_choices(
                        dms_type=dms_type if requires_google_drive else None,
                        chat_type=chat_type if requires_google_chat else None
                    )
                    logger.info(f"[HANDLER]   Scopes configurés: {auth_manager.SCOPES}")

                # Build state for OAuth callback
                redirect_uri = os.getenv("GOOGLE_AUTH_REDIRECT_LOCAL", "http://localhost:8000/google_auth_callback/")
                logger.info(f"[HANDLER]   redirect_uri: {redirect_uri}")

                state = {
                    "user_id": user_id,
                    "session_id": session_id,
                    "source": "onboarding",
                    "communication_mode": "pinnokio",
                    "redirect_uri": redirect_uri,
                    "context_params": {
                        "mandate_path": mandate_path,
                        "state_token": state_token,
                        "dms_type": dms_type,
                        "chat_type": chat_type,
                        "session_id": session_id,
                    }
                }
                state_encoded = base64.b64encode(json.dumps(state).encode()).decode()

                # Generate authorization URL using GoogleAuthManager
                # This uses GOOGLE_AUTH2_KEY secret internally
                auth_url = auth_manager.get_authorization_url(state=state_encoded)

                logger.info(f"[HANDLER] ✅ URL OAuth générée via GoogleAuthManager")
                logger.info(f"[HANDLER]   auth_url (tronquée): {auth_url[:100]}...")
                logger.info("─" * 60)

                return {
                    "success": True,
                    "auth_url": auth_url,
                    "state_token": state_token,
                    "requires_oauth": True
                }

            except Exception as auth_err:
                logger.error(f"[HANDLER] ❌ GoogleAuthManager FAILED: {auth_err}")
                return {
                    "success": False,
                    "error": f"OAuth configuration error: {str(auth_err)}"
                }

        except Exception as e:
            logger.error("─" * 60)
            logger.error(f"[HANDLER] ❌ EXCEPTION initiate_oauth_flow: {e}")
            logger.error("─" * 60, exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def complete_onboarding(
        self,
        user_id: str,
        mandate_path: str,
    ) -> Dict[str, Any]:
        """
        Mark onboarding as complete and update mandate status.

        Returns:
            {"success": True, "redirect_url": "/chat"}
        """
        try:
            from app.firebase_client import get_firestore

            logger.info("─" * 60)
            logger.info("[HANDLER] 🏁 complete_onboarding DÉMARRÉ")
            logger.info(f"[HANDLER]   user_id: {user_id}")
            logger.info(f"[HANDLER]   mandate_path: {mandate_path}")

            db = get_firestore()
            logger.info("[HANDLER] Mise à jour du mandate...")
            db.document(mandate_path).update({
                "onboarding_completed": True,
                "status": "active",
                "onboarding_completed_at": datetime.utcnow().isoformat()
            })

            logger.info("[HANDLER] ✅ Mandate mis à jour avec succès")
            logger.info("[HANDLER] 🎉 ONBOARDING COMPLÈTEMENT TERMINÉ!")
            logger.info("─" * 60)

            return {
                "success": True,
                "redirect_url": "/chat"
            }

        except Exception as e:
            logger.error("─" * 60)
            logger.error(f"[HANDLER] ❌ EXCEPTION complete_onboarding: {e}")
            logger.error("─" * 60, exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
_handlers_instance: Optional[OnboardingHandlers] = None


def get_onboarding_handlers() -> OnboardingHandlers:
    """Get or create the singleton OnboardingHandlers instance."""
    global _handlers_instance
    if _handlers_instance is None:
        _handlers_instance = OnboardingHandlers()
    return _handlers_instance
