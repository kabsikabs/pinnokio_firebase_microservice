"""
Onboarding Orchestration Functions
==================================

WebSocket handler functions for onboarding events.
These functions are called from main.py WebSocket message routing.

Each function:
1. Extracts parameters from payload
2. Calls the appropriate handler method
3. Sends the result back via WebSocket
"""

import logging
from typing import Dict, Any

from app.ws_hub import hub
from app.ws_events import WS_EVENTS

logger = logging.getLogger(__name__)


async def handle_test_erp_connection(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle onboarding.test_erp_connection event.

    Tests ERP connectivity during onboarding.

    Payload (supports both formats):
        Format 1 (onboarding wizard):
            erpType: str - ERP type (odoo, banana)
            odooDetails: dict - Odoo connection parameters
                - url: Server URL
                - databaseName: Database name
                - username: Username
                - apiKey: API key

        Format 2 (generic):
            erpType: str - ERP type (odoo, banana)
            connectionData: dict - Connection parameters
                - url: Server URL
                - database: Database name
                - username: Username
                - apiKey: API key

    Response:
        type: onboarding.erp_result
        payload:
            success: bool
            connected: bool
            message: str
            details?: dict
    """
    from .handlers import get_onboarding_handlers

    
    handlers = get_onboarding_handlers()

    try:
        erp_type = payload.get("erpType", "odoo")

        # Support both payload formats from frontend
        connection_data = payload.get("connectionData", {})

        # If odooDetails is provided (from onboarding wizard), normalize it
        if "odooDetails" in payload:
            odoo_details = payload["odooDetails"]
            connection_data = {
                "url": odoo_details.get("url", ""),
                "database": odoo_details.get("databaseName", ""),  # Map databaseName -> database
                "username": odoo_details.get("username", ""),
                "apiKey": odoo_details.get("apiKey", ""),
            }

        result = await handlers.test_erp_connection(
            user_id=uid,
            erp_type=erp_type,
            connection_data=connection_data,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERP_RESULT,
            "payload": result
        })

    except Exception as e:
        logger.error(f"handle_test_erp_connection error: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "test_erp_connection"
            }
        })


async def handle_load_clients(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle onboarding.load_clients event.

    Loads the list of clients for the user.

    Response:
        type: onboarding.clients_loaded
        payload:
            success: bool
            clients: list
    """
    from .handlers import get_onboarding_handlers

    
    handlers = get_onboarding_handlers()

    try:
        result = await handlers.load_clients(user_id=uid)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.CLIENTS_LOADED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"handle_load_clients error: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "load_clients"
            }
        })


async def handle_save_client(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle onboarding.save_client event.

    Creates a new client.

    Payload:
        clientData: dict
            - firstName: str
            - lastName: str
            - email: str
            - phone?: str
            - address?: str

    Response:
        type: onboarding.client_saved
        payload:
            success: bool
            client: dict
    """
    from .handlers import get_onboarding_handlers

    
    handlers = get_onboarding_handlers()

    try:
        client_data = payload.get("clientData", {})

        result = await handlers.save_client(
            user_id=uid,
            client_data=client_data,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.CLIENT_SAVED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"handle_save_client error: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "save_client"
            }
        })


async def handle_update_client(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle onboarding.update_client event.

    Updates an existing client.

    Payload:
        clientUuid: str
        clientData: dict

    Response:
        type: onboarding.client_updated
        payload:
            success: bool
            client: dict
    """
    from .handlers import get_onboarding_handlers

    
    handlers = get_onboarding_handlers()

    try:
        client_uuid = payload.get("clientUuid", "")
        client_data = payload.get("clientData", {})

        result = await handlers.update_client(
            user_id=uid,
            client_uuid=client_uuid,
            client_data=client_data,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.CLIENT_UPDATED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"handle_update_client error: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "update_client"
            }
        })


async def handle_delete_client(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle onboarding.delete_client event.

    Deletes a client (with security check for associated companies).

    Payload:
        clientUuid: str

    Response:
        type: onboarding.client_deleted
        payload:
            success: bool
            hasCompanies?: bool (if deletion blocked)
    """
    from .handlers import get_onboarding_handlers


    handlers = get_onboarding_handlers()

    try:
        client_uuid = payload.get("clientUuid", "")

        result = await handlers.delete_client(
            user_id=uid,
            client_uuid=client_uuid,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.CLIENT_DELETED,
            "payload": result
        })

    except Exception as e:
        logger.error(f"handle_delete_client error: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "delete_client"
            }
        })


async def handle_submit(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle onboarding.submit event.

    Process complete onboarding form submission.

    Payload:
        formData: dict - Complete onboarding form data
            - baseInfo: dict
            - businessDetails: dict
            - systemsConfig: dict

    Flow:
    1. Send progress(database: in_progress)
    2. Validate & save temp_data
    3. Create mandate in Firebase
    4. Send progress(database: completed)
    5. Check OAuth needed
       - If YES: Send oauth_url + progress(auth: in_progress)
       - If NO: Continue to DMS creation

    Response:
        Multiple events sent during workflow:
        - onboarding.progress (step, status, message)
        - onboarding.oauth_url (if OAuth required)
        - onboarding.complete (on success)
        - onboarding.error (on failure)
    """
    from .handlers import get_onboarding_handlers

    handlers = get_onboarding_handlers()

    try:
        form_data = payload.get("formData", {})

        logger.info("═" * 70)
        logger.info("[ONBOARDING FLOW] 🚀 DÉBUT handle_submit")
        logger.info("═" * 70)
        logger.info(f"[ONBOARDING FLOW] uid={uid}")
        logger.info(f"[ONBOARDING FLOW] session_id={session_id}")
        logger.info(f"[ONBOARDING FLOW] form_data keys: {list(form_data.keys()) if form_data else 'EMPTY'}")

        if form_data:
            base_info = form_data.get("baseInfo", {})
            business_details = form_data.get("businessDetails", {})
            systems_config = form_data.get("systemsConfig", {})
            logger.info(f"[ONBOARDING FLOW] baseInfo: entityType={base_info.get('entityType')}, "
                       f"ownershipType={base_info.get('ownershipType')}, "
                       f"companyName={base_info.get('companyName')}")
            logger.info(f"[ONBOARDING FLOW] systemsConfig: dmsType={systems_config.get('dmsType')}, "
                       f"chatType={systems_config.get('chatType')}, "
                       f"accountingSystem={systems_config.get('accountingSystem')}")

        # Step 1: Database - in_progress
        logger.info("─" * 70)
        logger.info("[ONBOARDING FLOW] 📊 ÉTAPE 1: Database - in_progress")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "database",
                "status": "in_progress",
                "message": "Creating company record..."
            }
        })
        logger.info("[ONBOARDING FLOW] ✅ Progress event sent")

        # Step 2: Submit onboarding (validates and creates mandate)
        logger.info("─" * 70)
        logger.info("[ONBOARDING FLOW] 📊 ÉTAPE 2: Appel submit_onboarding handler")
        result = await handlers.submit_onboarding(
            user_id=uid,
            session_id=session_id,
            onboarding_data=form_data
        )
        logger.info(f"[ONBOARDING FLOW] submit_onboarding result: {result}")

        if not result.get("success"):
            logger.error(f"[ONBOARDING FLOW] ❌ submit_onboarding FAILED: {result.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.PROGRESS,
                "payload": {
                    "step": "database",
                    "status": "error",
                    "message": result.get("error", "Failed to create company")
                }
            })
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.ERROR,
                "payload": {
                    "error": result.get("error", "Submission failed"),
                    "operation": "submit"
                }
            })
            return

        mandate_path = result.get("mandate_path")
        requires_oauth = result.get("requires_oauth", False)
        dms_type = result.get("dms_type", "google_drive")
        chat_type = result.get("chat_type", "pinnokio")

        logger.info(f"[ONBOARDING FLOW] ✅ submit_onboarding SUCCESS")
        logger.info(f"[ONBOARDING FLOW]   mandate_path: {mandate_path}")
        logger.info(f"[ONBOARDING FLOW]   requires_oauth: {requires_oauth}")
        logger.info(f"[ONBOARDING FLOW]   dms_type: {dms_type}")
        logger.info(f"[ONBOARDING FLOW]   chat_type: {chat_type}")

        # Step 3: Database - completed
        logger.info("─" * 70)
        logger.info("[ONBOARDING FLOW] 📊 ÉTAPE 3: Database - completed")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "database",
                "status": "completed",
                "message": "Company record created"
            }
        })

        # Send submitted confirmation
        logger.info("[ONBOARDING FLOW] 📤 Envoi SUBMITTED event")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.SUBMITTED,
            "payload": {
                "success": True,
                "mandate_path": mandate_path,
                "requires_oauth": requires_oauth
            }
        })

        # Step 4: Check if OAuth is needed
        logger.info("─" * 70)
        logger.info(f"[ONBOARDING FLOW] 📊 ÉTAPE 4: Vérification OAuth (requires_oauth={requires_oauth})")

        if requires_oauth:
            logger.info("[ONBOARDING FLOW] 🔐 OAuth REQUIS - Démarrage du flux OAuth")
            # Google Auth - in_progress
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.PROGRESS,
                "payload": {
                    "step": "google_auth",
                    "status": "in_progress",
                    "message": "Waiting for Google authorization..."
                }
            })

            # Initiate OAuth flow
            logger.info("[ONBOARDING FLOW] 🔐 Appel initiate_oauth_flow...")
            oauth_result = await handlers.initiate_oauth_flow(
                user_id=uid,
                session_id=session_id,
                dms_type=dms_type,
                chat_type=chat_type,
                mandate_path=mandate_path,
                return_path="/chat"
            )
            logger.info(f"[ONBOARDING FLOW] initiate_oauth_flow result: {oauth_result}")

            if oauth_result.get("success") and oauth_result.get("requires_oauth"):
                # Send OAuth URL to frontend
                auth_url = oauth_result.get("auth_url")
                logger.info(f"[ONBOARDING FLOW] 📤 Envoi OAUTH_URL event")
                logger.info(f"[ONBOARDING FLOW]   auth_url: {auth_url[:100]}..." if auth_url else "NO URL")
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.ONBOARDING.OAUTH_URL,
                    "payload": {
                        "auth_url": auth_url,
                        "state_token": oauth_result.get("state_token"),
                        "mandate_path": mandate_path,
                        "provider": "google_drive"
                    }
                })
                logger.info("[ONBOARDING FLOW] ⏳ En attente du callback OAuth...")
                logger.info("═" * 70)
                # Workflow continues when OAuth callback is received
                return
            else:
                logger.info("[ONBOARDING FLOW] OAuth non requis finalement, continuation...")
                # OAuth not actually required, continue to DMS
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.ONBOARDING.PROGRESS,
                    "payload": {
                        "step": "google_auth",
                        "status": "completed",
                        "message": "Authorization not required"
                    }
                })
        else:
            logger.info("[ONBOARDING FLOW] 🔓 OAuth NON requis - Skip vers DMS creation")

        # Step 5: DMS Creation (if no OAuth, or OAuth was skipped)
        logger.info("─" * 70)
        logger.info("[ONBOARDING FLOW] 📊 ÉTAPE 5: DMS Creation")
        await _continue_dms_creation(uid, session_id, mandate_path, dms_type, handlers)

    except Exception as e:
        logger.error("═" * 70)
        logger.error(f"[ONBOARDING FLOW] ❌ EXCEPTION dans handle_submit: {e}")
        logger.error("═" * 70, exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "submit"
            }
        })


async def handle_oauth_complete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle OAuth callback completion from google_auth_callback via WebSocket.

    Called after OAuth tokens are saved, continues the workflow.

    Payload:
        success: bool
        mandate_path: str
        context: dict (optional additional context)

    Flow:
    1. Send progress(auth: completed)
    2. Start DMS creation
    3. Send progress(folders: in_progress)
    4. Create folder structure
    5. Send progress(folders: completed)
    6. Send onboarding.complete

    Response:
        Multiple events sent during workflow
    """
    from .handlers import get_onboarding_handlers

    handlers = get_onboarding_handlers()

    try:
        success = payload.get("success", False)
        mandate_path = payload.get("mandate_path", "")
        context = payload.get("context", {})

        logger.info("═" * 70)
        logger.info("[ONBOARDING FLOW] 🔓 handle_oauth_complete appelé")
        logger.info("═" * 70)
        logger.info(f"[ONBOARDING FLOW] uid={uid}")
        logger.info(f"[ONBOARDING FLOW] success={success}")
        logger.info(f"[ONBOARDING FLOW] mandate_path={mandate_path}")
        logger.info(f"[ONBOARDING FLOW] context={context}")

        if not success:
            logger.error(f"[ONBOARDING FLOW] ❌ OAuth FAILED: {payload.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.PROGRESS,
                "payload": {
                    "step": "google_auth",
                    "status": "error",
                    "message": payload.get("error", "OAuth failed")
                }
            })
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.ERROR,
                "payload": {
                    "error": payload.get("error", "OAuth authorization failed"),
                    "operation": "oauth_complete"
                }
            })
            return

        logger.info("[ONBOARDING FLOW] ✅ OAuth SUCCESS - Envoi des events")

        # OAuth completed successfully
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.OAUTH_SUCCESS,
            "payload": {
                "success": True,
                "provider": "google_drive"
            }
        })
        logger.info("[ONBOARDING FLOW] 📤 OAUTH_SUCCESS event envoyé")

        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "google_auth",
                "status": "completed",
                "message": "Google authorization complete"
            }
        })
        logger.info("[ONBOARDING FLOW] 📤 PROGRESS google_auth=completed envoyé")

        # Get DMS type from context or default
        dms_type = context.get("dms_type", "google_drive")
        logger.info(f"[ONBOARDING FLOW] dms_type={dms_type}")

        # Continue with DMS creation
        logger.info("[ONBOARDING FLOW] → Continuation vers _continue_dms_creation")
        await _continue_dms_creation(uid, session_id, mandate_path, dms_type, handlers)

    except Exception as e:
        logger.error("═" * 70)
        logger.error(f"[ONBOARDING FLOW] ❌ EXCEPTION dans handle_oauth_complete: {e}")
        logger.error("═" * 70, exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "oauth_complete"
            }
        })


async def _continue_dms_creation(
    uid: str,
    session_id: str,
    mandate_path: str,
    dms_type: str,
    handlers
) -> None:
    """
    Continue onboarding workflow with DMS folder creation.

    Internal helper function called after database/OAuth steps complete.

    After DMS creation:
    1. Retrieves job_id from temp_data
    2. Loads company context (like company_change)
    3. Updates COMPLETE payload with redirectUrl: /chat/{job_id}?action=create
    """
    try:
        logger.info("─" * 70)
        logger.info("[ONBOARDING FLOW] 📂 _continue_dms_creation démarré")
        logger.info(f"[ONBOARDING FLOW] uid={uid}")
        logger.info(f"[ONBOARDING FLOW] mandate_path={mandate_path}")
        logger.info(f"[ONBOARDING FLOW] dms_type={dms_type}")

        # DMS Creation - in_progress
        logger.info("[ONBOARDING FLOW] 📤 Envoi PROGRESS dms_creation=in_progress")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "dms_creation",
                "status": "in_progress",
                "message": "Creating folder structure..."
            }
        })

        # Create DMS structure
        logger.info("[ONBOARDING FLOW] 📂 Appel create_dms_structure...")
        dms_result = await handlers.create_dms_structure(
            user_id=uid,
            mandate_path=mandate_path,
            dms_type=dms_type
        )
        logger.info(f"[ONBOARDING FLOW] create_dms_structure result: {dms_result}")

        if not dms_result.get("success"):
            logger.warning(f"[ONBOARDING FLOW] ⚠️ DMS creation failed: {dms_result.get('error')}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.PROGRESS,
                "payload": {
                    "step": "dms_creation",
                    "status": "error",
                    "message": dms_result.get("error", "Failed to create folders")
                }
            })
            # Continue anyway - DMS can be set up later
            logger.warning("[ONBOARDING FLOW] ⚠️ Continuation malgré l'échec DMS")
        else:
            logger.info("[ONBOARDING FLOW] ✅ DMS creation SUCCESS")

        # DMS Creation - completed (even if skipped)
        logger.info("[ONBOARDING FLOW] 📤 Envoi PROGRESS dms_creation=completed")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "dms_creation",
                "status": "completed",
                "message": "Folder structure ready" if dms_result.get("success") else "DMS setup pending"
            }
        })

        # ═══════════════════════════════════════════════════════════════════════
        # STEP: Company Setup - Full orchestration (Level 1 + Level 2 + LLM)
        # Uses the same logic as dashboard orchestration to ensure consistent state
        #
        # NOTE: Level 3 (dashboard.full_data) is SKIPPED because:
        # 1. User goes to CHAT after onboarding, not dashboard
        # 2. Chat will enrich company data (COA, contexts, etc.)
        # 3. When user visits dashboard, cache miss will trigger Level 3 loading
        # ═══════════════════════════════════════════════════════════════════════
        logger.info("[ONBOARDING FLOW] 📤 Envoi PROGRESS company_setup=in_progress")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "company_setup",
                "status": "in_progress",
                "message": "Initializing company..."
            }
        })

        # Retrieve job_id from temp_data
        logger.info("[ONBOARDING FLOW] 📊 Récupération job_id depuis temp_data...")
        job_id = None
        company_id = None

        try:
            from app.firebase_client import get_firestore
            db = get_firestore()

            # Get job_id from temp_data
            temp_data_path = f"clients/{uid}/temp_data/onboarding"
            temp_doc = db.document(temp_data_path).get()
            if temp_doc.exists:
                temp_data = temp_doc.to_dict()
                job_id = temp_data.get("job_id")
                logger.info(f"[ONBOARDING FLOW] job_id récupéré: {job_id}")
            else:
                logger.warning("[ONBOARDING FLOW] ⚠️ temp_data non trouvé")

            # Get company_id (contact_space_id) from mandate
            mandate_doc = db.document(mandate_path).get()
            if mandate_doc.exists:
                mandate_data = mandate_doc.to_dict()
                company_id = mandate_data.get("contact_space_id")
                logger.info(f"[ONBOARDING FLOW] company_id (contact_space_id): {company_id}")

        except Exception as temp_err:
            logger.warning(f"[ONBOARDING FLOW] ⚠️ Erreur récupération temp_data: {temp_err}")

        # ═══════════════════════════════════════════════════════════════════════
        # FULL ORCHESTRATION: Use reusable run_company_orchestration()
        # This function handles: company.list, company.details, L1+L2 cache, LLM init
        # ═══════════════════════════════════════════════════════════════════════
        logger.info("[ONBOARDING FLOW] 📊 Running company orchestration (Level 1 + Level 2 + LLM)...")

        try:
            import asyncio
            from app.firebase_providers import get_firebase_management
            from app.wrappers.dashboard_orchestration_handlers import run_company_orchestration

            firebase_mgmt = get_firebase_management()

            # Fetch full mandate data (includes workflow_params, context_details, etc.)
            full_mandate = await asyncio.to_thread(
                firebase_mgmt.fetch_single_mandate,
                mandate_path
            )

            if not full_mandate:
                logger.warning("[ONBOARDING FLOW] ⚠️ Could not load mandate data")
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.ONBOARDING.PROGRESS,
                    "payload": {
                        "step": "company_setup",
                        "status": "error",
                        "message": "Failed to load company data"
                    }
                })
                return

            # Ensure mandate_path is in full_mandate (may not be returned by fetch_single_mandate)
            if "mandate_path" not in full_mandate or not full_mandate.get("mandate_path"):
                full_mandate["mandate_path"] = mandate_path

            # Progress message before LLM init
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.PROGRESS,
                "payload": {
                    "step": "company_setup",
                    "status": "in_progress",
                    "message": "Starting AI assistant..."
                }
            })

            # Call the reusable company orchestration function
            # This does: company.list, company.details, L1+L2 cache, Neon HR sync, LLM init
            orchestration_result = await run_company_orchestration(
                uid=uid,
                company_id=company_id,
                full_mandate=full_mandate,
                broadcast_list=True  # Broadcast company.list with the new company
            )

            if not orchestration_result.get("success"):
                error_msg = orchestration_result.get("error", "Company orchestration failed")
                logger.error(f"[ONBOARDING FLOW] ❌ Company orchestration failed: {error_msg}")
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.ONBOARDING.PROGRESS,
                    "payload": {
                        "step": "company_setup",
                        "status": "error",
                        "message": error_msg
                    }
                })
                return

            logger.info(f"[ONBOARDING FLOW] ✅ Company orchestration completed successfully")
            logger.info(f"[ONBOARDING FLOW] ✅ Events sent: company.list, company.details, llm.session_ready")
            logger.info(f"[ONBOARDING FLOW] ✅ Cache set: Level 1 + Level 2")

            # NOTE: Level 3 (dashboard.full_data) is SKIPPED
            # It will be loaded when user visits dashboard via cache miss

        except Exception as ctx_err:
            logger.error(f"[ONBOARDING FLOW] ❌ Error during company setup: {ctx_err}", exc_info=True)
            await hub.broadcast(uid, {
                "type": WS_EVENTS.ONBOARDING.PROGRESS,
                "payload": {
                    "step": "company_setup",
                    "status": "error",
                    "message": f"Company setup failed: {str(ctx_err)}"
                }
            })
            return

        # ═══════════════════════════════════════════════════════════════════════
        # Complete onboarding (mandate update) - STILL in_progress for the user
        # We keep the progress spinner visible until redirect is ready
        # ═══════════════════════════════════════════════════════════════════════
        logger.info("[ONBOARDING FLOW] 🏁 Appel complete_onboarding...")
        complete_result = await handlers.complete_onboarding(
            user_id=uid,
            mandate_path=mandate_path
        )
        logger.info(f"[ONBOARDING FLOW] complete_onboarding result: {complete_result}")

        # ═══════════════════════════════════════════════════════════════════════
        # STEP: Build redirectUrl with job_id and action=create
        # ═══════════════════════════════════════════════════════════════════════
        if job_id:
            redirect_url = f"/chat/{job_id}?action=create"
            logger.info(f"[ONBOARDING FLOW] redirectUrl avec job_id: {redirect_url}")
        else:
            redirect_url = complete_result.get("redirect_url", "/chat")
            logger.warning(f"[ONBOARDING FLOW] ⚠️ Pas de job_id, fallback: {redirect_url}")

        # ═══════════════════════════════════════════════════════════════════════
        # NOW mark company_setup as completed + send onboarding.complete together
        # This ensures the progress spinner stays visible until redirect is ready
        # ═══════════════════════════════════════════════════════════════════════
        logger.info("[ONBOARDING FLOW] 📤 Envoi PROGRESS company_setup=completed")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.PROGRESS,
            "payload": {
                "step": "company_setup",
                "status": "completed",
                "message": "Company ready"
            }
        })

        # Send onboarding complete immediately after (no gap)
        logger.info("[ONBOARDING FLOW] 📤 Envoi COMPLETE event")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.COMPLETE,
            "payload": {
                "success": True,
                "redirectUrl": redirect_url,
                "mandate_path": mandate_path,
                "company_id": company_id,
                "job_id": job_id,
            }
        })

        logger.info("═" * 70)
        logger.info(f"[ONBOARDING FLOW] 🎉 ONBOARDING TERMINÉ AVEC SUCCÈS!")
        logger.info(f"[ONBOARDING FLOW] uid={uid}")
        logger.info(f"[ONBOARDING FLOW] mandate_path={mandate_path}")
        logger.info(f"[ONBOARDING FLOW] company_id={company_id}")
        logger.info(f"[ONBOARDING FLOW] job_id={job_id}")
        logger.info(f"[ONBOARDING FLOW] redirectUrl={redirect_url}")
        logger.info("═" * 70)

    except Exception as e:
        logger.error("═" * 70)
        logger.error(f"[ONBOARDING FLOW] ❌ EXCEPTION dans _continue_dms_creation: {e}")
        logger.error("═" * 70, exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.ONBOARDING.ERROR,
            "payload": {
                "error": str(e),
                "operation": "dms_creation"
            }
        })
