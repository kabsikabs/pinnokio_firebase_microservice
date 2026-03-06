"""
HR Page Orchestration
=====================

Handles WebSocket events for the HR module (PostgreSQL Neon).
Routes events to hr_rpc_handlers methods and broadcasts responses.

Architecture:
    Frontend (Next.js) → wsClient.send({type: 'hr.orchestrate_init'})
                      → WebSocket → main.py router
                      → handle_hr_event()
                      → hr_rpc_handlers.list_employees()
                      → hub.broadcast(uid, {type: 'hr.full_data', payload: ...})

Pattern: PAGE_STATE (3-Level Cache)
    page.restore_state -> cache hit: instant data via page.state_restored
    page.restore_state -> cache miss: hr.orchestrate_init -> full load

    Level 2: company:{uid}:{company_id}:context (from dashboard)
    Level 3 (Page): page_state:{uid}:{company_id}:hr

Events handled:
    - hr.orchestrate_init → Full page data load
    - hr.refresh → Refresh all data (invalidates cache)
    - hr.employees_list → List employees
    - hr.employee_get → Get single employee
    - hr.employee_create → Create employee
    - hr.employee_update → Update employee
    - hr.employee_delete → Delete employee
    - hr.payroll_calculate → Calculate payroll (async job)
    - hr.settings_get → Get HR settings
    - hr.settings_update → Update HR settings
    - hr.metrics_get → Get HR metrics
    - hr.rubrics_list → List rubrics (payroll items) with company overrides
    - hr.rubric_toggle → Toggle rubric active/inactive status
    - hr.rubric_update → Update rubric (custom label, rate, accounts)
    - hr.contracts_list → List contracts for an employee
    - hr.contract_create → Create new contract
    - hr.contract_update → Update existing contract (not yet implemented)
    - hr.active_contract_get → Get active contract for an employee

Rubrics Storage:
    System rubrics come from Neon (hr.payroll_items_catalog).
    Company-specific overrides are stored in Neon (hr.company_payroll_items).
    Structure: {
        is_active: bool,
        custom_label: str,
        custom_rate: float,
        debit_account_employee: str,
        credit_account_employee: str,
        debit_account_employer: str,
        credit_account_employer: str,
        updated_at: timestamp,
        updated_by: uid
    }
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Callable, Awaitable, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.hr_rpc_handlers import get_hr_rpc_handlers
from app.redis_client import get_redis

logger = logging.getLogger("hr.orchestration")

# ============================================
# Constants
# ============================================

TTL_HR_PAGE_STATE = 1800  # 30 minutes for page state cache
HR_SETTINGS_FIRESTORE_PATH = "hr_settings"  # Firestore collection for HR settings


# ============================================
# Helper Functions
# ============================================

def _get_company_context(uid: str, company_id: str) -> Dict[str, Any]:
    """
    Retrieve company context from Level 2 cache.

    Cache hierarchy:
    1. Level 2: company:{uid}:{company_id}:context (full company_data)
    2. Fallback Firebase: fetch_all_mandates_light → repopulate Level 2

    Returns:
        Dict with: mandate_path, client_uuid, etc.
    """
    redis_client = get_redis()

    # 1. Try Level 2 cache
    level2_key = f"company:{uid}:{company_id}:context"
    try:
        cached = redis_client.get(level2_key)
        if cached:
            data = json.loads(cached if isinstance(cached, str) else cached.decode())
            logger.info(
                f"[HR] Context from Level 2: {level2_key} "
                f"mandate_path={data.get('mandate_path', '')[:30]}..."
            )
            return data
    except Exception as e:
        logger.warning(f"[HR] Level 2 context read error: {e}")

    # 2. Fallback Firebase: Level 2 expiré ou absent → récupérer depuis Firebase
    logger.warning(f"[HR] Level 2 cache MISS for uid={uid}, company={company_id} — fetching from Firebase...")
    try:
        from app.firebase_providers import get_firebase_management
        from app.wrappers.dashboard_orchestration_handlers import set_selected_company

        firebase = get_firebase_management()
        mandates = firebase.fetch_all_mandates_light(uid)
        for m in (mandates or []):
            m_ids = (m.get("contact_space_id"), m.get("id"), m.get("contact_space_name"))
            if company_id in m_ids:
                m["company_id"] = company_id
                set_selected_company(uid, company_id, m)
                logger.info(
                    f"[HR] Context repopulated from Firebase: "
                    f"mandate_path={m.get('mandate_path', '')[:30]}..."
                )
                return m
    except Exception as e:
        logger.error(f"[HR] Firebase fallback failed: {e}")

    logger.error(f"[HR] No company context found for uid={uid}, company={company_id}")
    return {}


def _save_page_state(
    uid: str,
    company_id: str,
    mandate_path: str,
    data: Dict[str, Any]
) -> bool:
    """
    Save HR page state to Redis for fast recovery.

    Key: page_state:{uid}:{company_id}:hr
    """
    try:
        from app.wrappers.page_state_manager import get_page_state_manager

        page_manager = get_page_state_manager()
        page_manager.save_page_state(
            uid=uid,
            company_id=company_id,
            page="hr",
            mandate_path=mandate_path,
            data=data
        )
        logger.info(f"[HR] Page state saved for fast recovery")
        return True
    except Exception as e:
        logger.warning(f"[HR] Failed to save page state: {e}")
        return False


async def _resolve_hr_company_id(
    uid: str,
    company_id: str,
    payload: Dict[str, Any],
) -> Optional[str]:
    """
    Resolve the PostgreSQL hr_company_id from multiple sources.

    Resolution order:
    1. Explicit in payload (hr_company_id)
    2. From page_state:hr cache (set during orchestrate_init)
    3. Fallback: ensure_company via mandate_path (creates if needed)

    Returns:
        PostgreSQL company UUID string, or None if resolution failed.
    """
    # 1. Explicit in payload
    hr_company_id = payload.get("hr_company_id")
    if hr_company_id:
        return hr_company_id

    # 2. From page_state cache (fastest path after init)
    try:
        from app.wrappers.page_state_manager import get_page_state_manager

        page_manager = get_page_state_manager()
        page_state = page_manager.get_page_state(
            uid=uid,
            company_id=company_id,
            page="hr",
        )
        if page_state and isinstance(page_state, dict):
            cached_id = (
                page_state.get("hr_company_id")
                or page_state.get("data", {}).get("hr_company_id")
            )
            if cached_id:
                return cached_id
    except Exception as e:
        logger.debug("[HR] page_state lookup for hr_company_id failed: %s", e)

    # 3. Fallback: resolve via ensure_company (mandate_path required)
    mandate_path = payload.get("mandate_path")
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if mandate_path:
        try:
            handlers = get_hr_rpc_handlers()
            company_result = await handlers.ensure_company(
                account_firebase_uid=uid,
                mandate_path=mandate_path,
                company_name=payload.get("company_name", ""),
                country=payload.get("country", "CH"),
            )
            return company_result.get("company_id")
        except Exception as e:
            logger.error("[HR] ensure_company fallback failed: %s", e)

    return None


def _invalidate_page_state(uid: str, company_id: str) -> bool:
    """Invalidate HR page state cache."""
    try:
        from app.wrappers.page_state_manager import get_page_state_manager

        page_manager = get_page_state_manager()
        page_manager.invalidate_page_state(
            uid=uid,
            company_id=company_id,
            page="hr"
        )
        logger.info(f"[HR] Page state invalidated")
        return True
    except Exception as e:
        logger.warning(f"[HR] Failed to invalidate page state: {e}")
        return False


async def _get_hr_settings(uid: str, company_id: str, mandate_path: str) -> Dict[str, Any]:
    """
    Get HR settings from Firestore.

    Path: {mandate_path}/setup/hr_settings

    Returns default settings if not found.
    """
    default_settings = {
        "payroll_frequency": "monthly",
        "payroll_day": 25,
        "annual_leave_days": 25,
        "rtt_days": 0,
        "sick_leave_policy": "",
        "contributions": [],
        "currency": "CHF",
        "work_hours_per_week": 42,
    }

    try:
        from app.firebase_providers import get_firebase_management

        firebase = get_firebase_management()
        settings_path = f"{mandate_path}/setup/hr_settings"

        doc = await asyncio.to_thread(
            firebase.get_document,
            settings_path
        )

        if doc:
            # Merge with defaults
            return {**default_settings, **doc}

        return default_settings

    except Exception as e:
        logger.warning(f"[HR] Failed to get settings from Firestore: {e}")
        return default_settings


async def _save_hr_settings(
    uid: str,
    company_id: str,
    mandate_path: str,
    settings: Dict[str, Any]
) -> bool:
    """
    Save HR settings to Firestore.

    Path: {mandate_path}/setup/hr_settings
    """
    try:
        from app.firebase_providers import get_firebase_management

        firebase = get_firebase_management()
        settings_path = f"{mandate_path}/setup/hr_settings"

        # Add metadata
        settings_with_meta = {
            **settings,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": uid,
        }

        await asyncio.to_thread(
            firebase.set_document,
            settings_path,
            settings_with_meta,
            True  # merge=True
        )

        logger.info(f"[HR] Settings saved to Firestore: {settings_path}")
        return True

    except Exception as e:
        logger.error(f"[HR] Failed to save settings to Firestore: {e}")
        return False


def _calculate_payroll_summary(
    employees: list,
    payroll_results: list = None
) -> Dict[str, Any]:
    """
    Calculate payroll summary from employees and payroll results.
    """
    active_employees = [e for e in employees if e.get("status") != "terminated"]

    # Calculate total payroll if we have results
    total_gross = 0
    total_net = 0
    total_employer_charges = 0

    if payroll_results:
        for result in payroll_results:
            total_gross += result.get("gross_salary", 0)
            total_net += result.get("net_salary", 0)
            total_employer_charges += result.get("employer_charges", 0)

    return {
        "period": datetime.now().strftime("%Y-%m"),
        "employee_count": len(active_employees),
        "total_gross": total_gross,
        "total_net": total_net,
        "total_employer_charges": total_employer_charges,
        "total_cost": total_gross + total_employer_charges,
        "status": "pending" if not payroll_results else "calculated",
        "last_calculated_at": None,
    }


# ============================================
# Event Handlers
# ============================================

async def handle_orchestrate_init(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.orchestrate_init - Load full page data.

    Fetches employees, contracts, rubrics, metrics and settings for the HR page.
    Saves state to page_state cache for fast recovery.

    Args:
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: {
            company_id: str,
            mandate_path: str (optional - will get from Level 2 cache),
            hr_company_id?: str (optional PostgreSQL company UUID)
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    hr_company_id = payload.get("hr_company_id")

    # Get mandate_path from Level 2 cache if not provided
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing company_id", "code": "MISSING_COMPANY_ID"}
        })
        return

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # 1. Ensure company exists in PostgreSQL (get or create)
        if not hr_company_id:
            company_result = await handlers.ensure_company(
                account_firebase_uid=uid,
                mandate_path=mandate_path,
                company_name=payload.get("company_name", ""),
                country=payload.get("country", "CH"),
            )
            hr_company_id = company_result.get("company_id")

        if not hr_company_id:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": "Failed to get/create PostgreSQL company"}
            })
            return

        # 2. Fetch data in parallel
        employees_task = handlers.list_employees(
            company_id=hr_company_id,
            firebase_user_id=uid,
        )
        settings_task = _get_hr_settings(uid, company_id, mandate_path)
        references_task = handlers.get_all_references(
            country_code="CH", lang="fr", firebase_user_id=uid,
            company_id=company_id
        )
        clusters_task = handlers.list_clusters(
            country_code="CH", firebase_user_id=uid, company_id=company_id
        )

        employees_result, settings, references_result, clusters_result = await asyncio.gather(
            employees_task,
            settings_task,
            references_task,
            clusters_task,
            return_exceptions=True
        )

        # Handle potential exceptions
        if isinstance(employees_result, Exception):
            logger.error(f"[HR] Failed to fetch employees: {employees_result}")
            employees = []
        else:
            employees = employees_result.get("employees", [])

        if isinstance(settings, Exception):
            logger.error(f"[HR] Failed to fetch settings: {settings}")
            settings = {}

        # Handle references
        references = {}
        if isinstance(references_result, Exception):
            logger.error(f"[HR] Failed to fetch references: {references_result}")
        else:
            references = references_result if isinstance(references_result, dict) else {}

        # Handle clusters
        clusters = []
        if isinstance(clusters_result, Exception):
            logger.error(f"[HR] Failed to fetch clusters: {clusters_result}")
        elif isinstance(clusters_result, dict):
            clusters = clusters_result.get("clusters", [])

        # Merge clusters into references
        references["clusters"] = clusters

        # 3. Calculate metrics
        active_employees = len([e for e in employees if e.get("status") != "terminated"])
        on_leave_employees = len([e for e in employees if e.get("status") == "on_leave"])

        metrics = {
            "active_employees": active_employees,
            "total_employees": len(employees),
            "on_leave": on_leave_employees,
            "total_payroll": 0,  # Will be calculated when payroll data is available
            "next_payroll_date": "",  # Will be calculated from settings
        }

        # 4. Get payroll summary (placeholder for now)
        payroll_summary = _calculate_payroll_summary(employees)

        # 5. Prepare contracts (empty for now - will be loaded per employee)
        contracts = []

        # 6. Get rubrics/payroll items
        rubrics = []
        try:
            rubrics_result = await handlers.get_payroll_items(country_code="CH")
            if isinstance(rubrics_result, dict):
                rubrics = rubrics_result.get("payroll_items", [])
        except Exception as e:
            logger.warning(f"[HR] Failed to fetch rubrics: {e}")

        # 7. Build full data response
        hr_data = {
            "employees": employees,
            "contracts": contracts,
            "rubrics": rubrics,
            "references": references,
            "metrics": metrics,
            "settings": settings,
            "payroll_summary": payroll_summary,
            "company_id": company_id,
            "hr_company_id": hr_company_id,
            "mandate_path": mandate_path,
            "meta": {
                "loaded_at": datetime.now(timezone.utc).isoformat() + "Z",
                "version": "1.0",
                "source": "postgresql"
            }
        }

        # 8. Save page state for fast recovery
        _save_page_state(uid, company_id, mandate_path, hr_data)

        # 9. Send full data response
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.FULL_DATA,
            "payload": {
                "success": True,
                **hr_data,
            }
        })

        logger.info(
            f"[HR] Orchestration complete: uid={uid} company={company_id} "
            f"employees={len(employees)} contracts={len(contracts)} "
            f"rubrics={len(rubrics)}"
        )

    except Exception as e:
        logger.error(f"[HR] Orchestration failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "ORCHESTRATION_ERROR"}
        })


async def handle_refresh(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.refresh - Reload all data.

    Invalidates cache first, then re-runs orchestration.
    """
    company_id = payload.get("company_id")

    # Invalidate page state cache first
    if company_id:
        _invalidate_page_state(uid, company_id)

    # Delegate to orchestrate_init
    await handle_orchestrate_init(uid, session_id, payload)


async def handle_employees_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle hr.employees_list - List all employees."""
    company_id = payload.get("company_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()
        result = await handlers.list_employees(
            company_id=hr_company_id,
            firebase_user_id=uid,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.EMPLOYEES_LOADED,
            "payload": {
                "success": True,
                "employees": result.get("employees", []),
            }
        })

    except Exception as e:
        logger.error(f"[HR] List employees failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_employee_get(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle hr.employee_get - Get single employee details."""
    company_id = payload.get("company_id")
    employee_id = payload.get("employee_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id or not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or employee_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()
        result = await handlers.get_employee(
            company_id=hr_company_id,
            employee_id=employee_id,
            firebase_user_id=uid,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.EMPLOYEE_LOADED,
            "payload": {
                "success": True,
                "employee": result.get("employee"),
            }
        })

    except Exception as e:
        logger.error(f"[HR] Get employee failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_employee_create(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.employee_create - Create new employee.

    Invalidates page state cache after successful creation.
    """
    company_id = payload.get("company_id")
    data = payload.get("data", {})

    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)
    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Map frontend EmployeeFormData to backend create_employee params
        # cluster_code is a regional code (CH-GE, CH-VD) — NOT the department name
        cluster = data.get("cluster_code", "CH-GE")
        # Derive country_code from cluster_code (CH-GE → CH) if not provided
        country = data.get("countryCode") or (cluster.split("-")[0] if "-" in cluster else cluster)

        result = await handlers.create_employee(
            company_id=hr_company_id,
            identifier=data.get("email", ""),  # Use email as identifier
            first_name=data.get("firstName", ""),
            last_name=data.get("lastName", ""),
            birth_date=data.get("birthDate", "2000-01-01"),
            cluster_code=cluster,
            hire_date=data.get("startDate", ""),
            firebase_user_id=uid,
            # Additional optional fields (must match hr.employees columns)
            email=data.get("email"),
            phone=data.get("phone"),
            gender=data.get("gender"),
            nationality=data.get("nationality"),
            address=data.get("address"),
            city=data.get("city"),
            postal_code=data.get("postalCode"),
            country_code=country,
            tax_status=data.get("taxStatus"),
            family_status=data.get("familyStatus"),
            permit_type=data.get("permitType"),
        )

        if result.get("employee_id"):
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

            # Fetch the created employee to return full data
            employee_result = await handlers.get_employee(
                company_id=hr_company_id,
                employee_id=result["employee_id"],
                firebase_user_id=uid,
            )

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.EMPLOYEE_CREATED,
                "payload": {
                    "success": True,
                    "employee": employee_result.get("employee"),
                }
            })
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": result.get("error", "Failed to create employee")}
            })

    except Exception as e:
        logger.error(f"[HR] Create employee failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_employee_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.employee_update - Update employee.

    Invalidates page state cache after successful update.
    """
    company_id = payload.get("company_id")
    employee_id = payload.get("employee_id")
    data = payload.get("data", {})
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id or not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or employee_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Map frontend fields (camelCase) to backend fields (snake_case)
        update_fields = {}
        # Direct snake_case fields (already in backend format)
        direct_fields = ["status", "phone", "email", "address", "gender", "nationality"]
        for field in direct_fields:
            if field in data:
                update_fields[field] = data[field]

        # camelCase → snake_case mapping
        field_mapping = {
            "firstName": "first_name",
            "lastName": "last_name",
            "startDate": "start_date",
            "birthDate": "birth_date",
            "taxStatus": "tax_status",
            "familyStatus": "family_status",
            "permitType": "permit_type",
            "countryCode": "country_code",
            "dependents": "dependents",
            "city": "city",
            "postalCode": "postal_code",
            "clusterCode": "cluster_code",
        }
        for frontend_key, backend_key in field_mapping.items():
            if frontend_key in data:
                update_fields[backend_key] = data[frontend_key]

        result = await handlers.update_employee(
            company_id=hr_company_id,
            employee_id=employee_id,
            firebase_user_id=uid,
            **update_fields,
        )

        if result.get("success"):
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

            # Fetch updated employee
            employee_result = await handlers.get_employee(
                company_id=hr_company_id,
                employee_id=employee_id,
                firebase_user_id=uid,
            )

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.EMPLOYEE_UPDATED,
                "payload": {
                    "success": True,
                    "employee": employee_result.get("employee"),
                }
            })
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": result.get("error", "Failed to update employee")}
            })

    except Exception as e:
        logger.error(f"[HR] Update employee failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_employee_delete(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.employee_delete - Delete employee.

    Invalidates page state cache after successful deletion.
    """
    company_id = payload.get("company_id")
    employee_id = payload.get("employee_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id or not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or employee_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()
        result = await handlers.delete_employee(
            company_id=hr_company_id,
            employee_id=employee_id,
            firebase_user_id=uid,
        )

        if result.get("success"):
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.EMPLOYEE_DELETED,
            "payload": {
                "success": result.get("success", False),
                "employee_id": employee_id,
            }
        })

    except Exception as e:
        logger.error(f"[HR] Delete employee failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_payroll_calculate(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle hr.payroll_calculate - Calculate payroll (async job).

    Uses the centralized handle_job_process(job_type="hr") dispatch.
    The worker receives the job via active_jobs polling or HTTP,
    calculates, and publishes results via Redis task_manager.
    """
    company_id = payload.get("company_id")
    period = payload.get("period")  # Format: "2024-01"
    mandate_path = payload.get("mandate_path")
    employee_ids = payload.get("employee_ids")  # Optional: specific employees
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id or not period:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or period", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        # Parse period
        year, month = period.split("-")

        # Build company_data from Level 2 cache (same pattern as other pages)
        if not mandate_path and company_id:
            context = _get_company_context(uid, company_id)
            mandate_path = context.get("mandate_path")

        company_data = _get_company_context(uid, company_id) if company_id else {}
        if mandate_path:
            company_data["mandate_path"] = mandate_path
        # hr_company_id is the Neon UUID, use as company_id for the worker
        company_data["company_id"] = hr_company_id

        # Submit via centralized dispatch
        from app.tools.hr_jobber_client import get_hr_jobber_client
        client = get_hr_jobber_client()

        result = await client.submit_batch_payroll(
            uid=uid,
            company_data=company_data,
            period={"year": int(year), "month": int(month)},
            employee_ids=employee_ids,
        )

        # Notify that calculation started
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.PAYROLL_CALCULATING,
            "payload": {
                "success": result.get("success", False),
                "batch_id": result.get("batch_id"),
                "period": period,
                "dispatch_method": result.get("dispatch_method"),
            }
        })

        logger.info(
            "[HR] Payroll calculation started: batch_id=%s period=%s dispatch=%s",
            result.get("batch_id"), period, result.get("dispatch_method"),
        )

    except Exception as e:
        logger.error(f"[HR] Payroll calculate failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_settings_get(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle hr.settings_get - Get HR settings."""
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")

    # Get mandate_path from Level 2 cache if not provided
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing mandate_path"}
        })
        return

    try:
        settings = await _get_hr_settings(uid, company_id, mandate_path)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.SETTINGS_LOADED,
            "payload": {
                "success": True,
                "settings": settings,
            }
        })

    except Exception as e:
        logger.error(f"[HR] Get settings failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_settings_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.settings_update - Update HR settings.

    Persists settings to Firestore and invalidates page state cache.
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    settings = payload.get("settings", {})

    # Get mandate_path from Level 2 cache if not provided
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing mandate_path"}
        })
        return

    try:
        # Save to Firestore
        success = await _save_hr_settings(uid, company_id, mandate_path, settings)

        if success:
            # Invalidate page state cache
            _invalidate_page_state(uid, company_id)

            # Get updated settings
            updated_settings = await _get_hr_settings(uid, company_id, mandate_path)

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.SETTINGS_UPDATED,
                "payload": {
                    "success": True,
                    "settings": updated_settings,
                }
            })
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": "Failed to save settings"}
            })

    except Exception as e:
        logger.error(f"[HR] Update settings failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_metrics_get(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """Handle hr.metrics_get - Get HR metrics."""
    company_id = payload.get("company_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Get employees to calculate metrics
        employees_result = await handlers.list_employees(
            company_id=hr_company_id,
            firebase_user_id=uid,
        )
        employees = employees_result.get("employees", [])

        # Calculate metrics
        active_employees = len([e for e in employees if e.get("status") != "terminated"])
        on_leave_employees = len([e for e in employees if e.get("status") == "on_leave"])

        metrics = {
            "active_employees": active_employees,
            "total_employees": len(employees),
            "on_leave": on_leave_employees,
            "total_payroll": 0,
            "next_payroll_date": "",
        }

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.METRICS_LOADED,
            "payload": {
                "success": True,
                "metrics": metrics,
            }
        })

    except Exception as e:
        logger.error(f"[HR] Get metrics failed: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e)}
        })


# ============================================
# Rubrics Handlers
# ============================================

async def _get_rubric_overrides(mandate_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Load company-specific rubric overrides from Neon (hr.company_payroll_items).

    Returns:
        Dict mapping rubric_code to override data
    """
    try:
        from app.tools.neon_hr_manager import get_neon_hr_manager

        manager = get_neon_hr_manager()
        company_id = await manager.get_company_id_from_mandate_path(mandate_path)
        if not company_id:
            logger.warning(f"[HR] No Neon company found for mandate_path={mandate_path}")
            return {}

        pool = await manager.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    cat.code AS rubric_code,
                    cpi.is_enabled AS is_active,
                    cpi.custom_label,
                    cpi.custom_rate_employee AS custom_rate,
                    dae.account_number AS debit_account_employee,
                    cae.account_number AS credit_account_employee,
                    daer.account_number AS debit_account_employer,
                    caer.account_number AS credit_account_employer,
                    cpi.updated_at
                FROM hr.company_payroll_items cpi
                JOIN hr.payroll_items_catalog cat ON cpi.catalog_item_id = cat.id
                LEFT JOIN core.chart_of_accounts dae ON cpi.debit_account_employee = dae.id
                LEFT JOIN core.chart_of_accounts cae ON cpi.credit_account_employee = cae.id
                LEFT JOIN core.chart_of_accounts daer ON cpi.debit_account_employer = daer.id
                LEFT JOIN core.chart_of_accounts caer ON cpi.credit_account_employer = caer.id
                WHERE cpi.company_id = $1
            """, company_id)

        overrides = {}
        for row in rows:
            code = row["rubric_code"]
            override = {}
            if row["is_active"] is not None:
                override["is_active"] = row["is_active"]
            if row["custom_label"]:
                override["custom_label"] = row["custom_label"]
            if row["custom_rate"] is not None:
                override["custom_rate"] = float(row["custom_rate"])
            if row["debit_account_employee"]:
                override["debit_account_employee"] = row["debit_account_employee"]
            if row["credit_account_employee"]:
                override["credit_account_employee"] = row["credit_account_employee"]
            if row["debit_account_employer"]:
                override["debit_account_employer"] = row["debit_account_employer"]
            if row["credit_account_employer"]:
                override["credit_account_employer"] = row["credit_account_employer"]
            if row["updated_at"]:
                override["updated_at"] = row["updated_at"].isoformat()
            if override:
                overrides[code] = override

        logger.info(f"[HR] Loaded {len(overrides)} rubric overrides from Neon")
        return overrides

    except Exception as e:
        logger.warning(f"[HR] Failed to load rubric overrides: {e}")
        return {}


async def _save_rubric_override(
    mandate_path: str,
    rubric_code: str,
    override_data: Dict[str, Any],
    uid: str
) -> bool:
    """
    Save a rubric override to Neon (hr.company_payroll_items).

    Finds the catalog item by code, then upserts the company override.
    """
    try:
        from app.tools.neon_hr_manager import get_neon_hr_manager

        manager = get_neon_hr_manager()
        company_id = await manager.get_company_id_from_mandate_path(mandate_path)
        if not company_id:
            logger.error(f"[HR] No Neon company found for mandate_path={mandate_path}")
            return False

        pool = await manager.get_pool()
        async with pool.acquire() as conn:
            # Find the catalog item by code
            catalog_row = await conn.fetchrow(
                "SELECT id FROM hr.payroll_items_catalog WHERE code = $1 LIMIT 1",
                rubric_code
            )
            if not catalog_row:
                logger.error(f"[HR] Catalog item not found: {rubric_code}")
                return False

            catalog_item_id = catalog_row["id"]

            # Build SET clause dynamically from override_data
            set_parts = ["updated_at = NOW()"]
            params = [company_id, catalog_item_id]
            param_idx = 3

            field_mapping = {
                "is_active": "is_enabled",
                "custom_label": "custom_label",
                "custom_rate": "custom_rate_employee",
                "debit_account_employee": "debit_account_employee",
                "credit_account_employee": "credit_account_employee",
                "debit_account_employer": "debit_account_employer",
                "credit_account_employer": "credit_account_employer",
            }

            # Account fields need account_number → UUID resolution
            account_cols = {
                "debit_account_employee",
                "credit_account_employee",
                "debit_account_employer",
                "credit_account_employer",
            }

            for field, col in field_mapping.items():
                if field in override_data:
                    val = override_data[field]
                    # Resolve account_number → UUID for all 4 account columns
                    if col in account_cols:
                        if val and str(val).strip():
                            row = await conn.fetchrow(
                                "SELECT id FROM core.chart_of_accounts WHERE company_id = $1 AND account_number = $2",
                                company_id, str(val).strip()
                            )
                            val = row["id"] if row else None
                        else:
                            val = None
                    set_parts.append(f"{col} = ${param_idx}")
                    params.append(val)
                    param_idx += 1

            set_clause = ", ".join(set_parts)

            await conn.execute(f"""
                INSERT INTO hr.company_payroll_items (company_id, catalog_item_id, is_enabled)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (company_id, catalog_item_id)
                DO UPDATE SET {set_clause}
            """, *params)

        logger.info(f"[HR] Rubric override saved: {rubric_code}")
        return True

    except Exception as e:
        logger.error(f"[HR] Failed to save rubric override: {e}")
        return False


def _merge_rubrics_with_overrides(
    system_rubrics: list,
    overrides: Dict[str, Dict[str, Any]]
) -> list:
    """
    Merge system rubrics from Jobber with company-specific overrides.

    Override values take precedence over system values.
    """
    merged = []
    for rubric in system_rubrics:
        rubric_code = rubric.get("code") or rubric.get("id")
        override = overrides.get(rubric_code, {})

        # Merge: override values > system values
        merged_rubric = {
            **rubric,
            # Override fields if present
            "is_active": override.get("is_active", rubric.get("is_active", True)),
            "custom_label": override.get("custom_label", ""),
            "custom_rate": override.get("custom_rate"),
            # Account mapping
            "debit_account_employee": override.get("debit_account_employee", ""),
            "credit_account_employee": override.get("credit_account_employee", ""),
            "debit_account_employer": override.get("debit_account_employer", ""),
            "credit_account_employer": override.get("credit_account_employer", ""),
            # Metadata
            "has_override": bool(override),
            "override_updated_at": override.get("updated_at"),
        }
        merged.append(merged_rubric)

    return merged


async def handle_rubrics_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.rubrics_list - List all rubrics with company overrides.

    Event: hr.rubrics_list
    Payload: { company_id: str, country_code?: str }
    Response: hr.rubrics_loaded { success: bool, rubrics: [...] }
    """
    company_id = payload.get("company_id")
    country_code = payload.get("country_code", "CH")
    mandate_path = payload.get("mandate_path")

    # Get mandate_path from Level 2 cache if not provided
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # 1. Fetch system rubrics from Jobber
        rubrics_result = await handlers.get_payroll_items(country_code=country_code)
        system_rubrics = rubrics_result.get("payroll_items", [])

        # 2. Fetch company overrides from Firestore
        overrides = await _get_rubric_overrides(mandate_path)

        # 3. Merge rubrics with overrides
        merged_rubrics = _merge_rubrics_with_overrides(system_rubrics, overrides)

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.RUBRICS_LOADED,
            "payload": {
                "success": True,
                "rubrics": merged_rubrics,
                "country_code": country_code,
            }
        })

        logger.info(
            f"[HR] Rubrics listed: uid={uid} country={country_code} "
            f"total={len(merged_rubrics)} overrides={len(overrides)}"
        )

    except Exception as e:
        logger.error(f"[HR] List rubrics failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "RUBRICS_LIST_ERROR"}
        })


async def handle_rubric_toggle(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.rubric_toggle - Toggle rubric active/inactive status.

    Event: hr.rubric_toggle
    Payload: { rubric_id: str, is_active: bool, company_id: str }
    Response: hr.rubric_toggled { success: bool, rubric_id: str, is_active: bool }
    """
    company_id = payload.get("company_id")
    rubric_id = payload.get("rubric_id")
    is_active = payload.get("is_active", True)
    mandate_path = payload.get("mandate_path")

    if not rubric_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing rubric_id", "code": "MISSING_RUBRIC_ID"}
        })
        return

    # Get mandate_path from Level 2 cache if not provided
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        # Save override to Firestore
        override_data = {"is_active": is_active}
        success = await _save_rubric_override(mandate_path, rubric_id, override_data, uid)

        if success:
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.RUBRIC_TOGGLED,
                "payload": {
                    "success": True,
                    "rubric_id": rubric_id,
                    "is_active": is_active,
                }
            })

            logger.info(f"[HR] Rubric toggled: rubric={rubric_id} active={is_active}")
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": "Failed to save rubric toggle", "code": "TOGGLE_SAVE_ERROR"}
            })

    except Exception as e:
        logger.error(f"[HR] Toggle rubric failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "RUBRIC_TOGGLE_ERROR"}
        })


async def handle_rubric_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.rubric_update - Update rubric (custom label, rate, accounts).

    Event: hr.rubric_update
    Payload: {
        rubric_id: str,
        company_id: str,
        data: {
            is_active?: bool,
            custom_label?: str,
            custom_rate?: float,
            debit_account_employee?: str,
            credit_account_employee?: str,
            debit_account_employer?: str,
            credit_account_employer?: str
        }
    }
    Response: hr.rubric_updated { success: bool, rubric: {...} }
    """
    company_id = payload.get("company_id")
    rubric_id = payload.get("rubric_id")
    data = payload.get("data", {})
    mandate_path = payload.get("mandate_path")

    if not rubric_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing rubric_id", "code": "MISSING_RUBRIC_ID"}
        })
        return

    # Get mandate_path from Level 2 cache if not provided
    if not mandate_path and company_id:
        context = _get_company_context(uid, company_id)
        mandate_path = context.get("mandate_path")

    if not mandate_path:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {
                "error": "Session context not initialized. Please refresh the dashboard first.",
                "code": "SESSION_NOT_INITIALIZED"
            }
        })
        return

    try:
        # Build override data from the provided fields
        override_data = {}

        # Only include fields that are provided
        allowed_fields = [
            "is_active",
            "custom_label",
            "custom_rate",
            "debit_account_employee",
            "credit_account_employee",
            "debit_account_employer",
            "credit_account_employer",
        ]

        for field in allowed_fields:
            if field in data:
                override_data[field] = data[field]

        if not override_data:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": "No update data provided", "code": "NO_UPDATE_DATA"}
            })
            return

        # Save override to Firestore
        success = await _save_rubric_override(mandate_path, rubric_id, override_data, uid)

        if success:
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

            # Fetch updated rubric data to return
            handlers = get_hr_rpc_handlers()
            country_code = payload.get("country_code", "CH")

            # Get system rubric
            rubrics_result = await handlers.get_payroll_items(country_code=country_code)
            system_rubrics = rubrics_result.get("payroll_items", [])

            # Find the specific rubric
            system_rubric = next(
                (r for r in system_rubrics if (r.get("code") or r.get("id")) == rubric_id),
                {"code": rubric_id}
            )

            # Get updated override
            overrides = await _get_rubric_overrides(mandate_path)
            override = overrides.get(rubric_id, {})

            # Merge to get final state
            updated_rubric = {
                **system_rubric,
                "is_active": override.get("is_active", system_rubric.get("is_active", True)),
                "custom_label": override.get("custom_label", ""),
                "custom_rate": override.get("custom_rate"),
                "debit_account_employee": override.get("debit_account_employee", ""),
                "credit_account_employee": override.get("credit_account_employee", ""),
                "debit_account_employer": override.get("debit_account_employer", ""),
                "credit_account_employer": override.get("credit_account_employer", ""),
                "has_override": True,
                "override_updated_at": override.get("updated_at"),
            }

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.RUBRIC_UPDATED,
                "payload": {
                    "success": True,
                    "rubric": updated_rubric,
                }
            })

            logger.info(
                f"[HR] Rubric updated: rubric={rubric_id} "
                f"fields={list(override_data.keys())}"
            )
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {"error": "Failed to save rubric update", "code": "UPDATE_SAVE_ERROR"}
            })

    except Exception as e:
        logger.error(f"[HR] Update rubric failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "RUBRIC_UPDATE_ERROR"}
        })


# ============================================
# Contracts Handlers
# ============================================

async def handle_contracts_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.contracts_list - List all contracts for an employee.

    Event: hr.contracts_list
    Payload: { company_id: str, employee_id: str, hr_company_id?: str }
    Response: hr.contracts_loaded { success: bool, contracts: [...] }
    """
    company_id = payload.get("company_id")
    employee_id = payload.get("employee_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing employee_id", "code": "MISSING_EMPLOYEE_ID"}
        })
        return

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()
        result = await handlers.list_contracts(
            company_id=hr_company_id,
            employee_id=employee_id,
            firebase_user_id=uid,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.CONTRACTS_LOADED,
            "payload": {
                "success": True,
                "contracts": result.get("contracts", []),
                "employee_id": employee_id,
            }
        })

        logger.info(
            f"[HR] Contracts listed: uid={uid} employee={employee_id} "
            f"count={len(result.get('contracts', []))}"
        )

    except Exception as e:
        logger.error(f"[HR] List contracts failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "CONTRACTS_LIST_ERROR"}
        })


async def handle_contract_create(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.contract_create - Create a new contract.

    Event: hr.contract_create
    Payload: {
        company_id: str,
        employee_id: str,
        hr_company_id: str,
        mandate_path?: str,
        data: {
            contract_type: str,
            base_salary: float,
            currency?: str,
            work_rate?: float,
            start_date: str,
            end_date?: str,
            weekly_hours?: float,
            annual_leave_days?: int,
            remuneration_type?: str,
            job_title?: str,
            department?: str,
            thirteenth_month?: bool,
            thirteenth_month_rate?: float,
            bonus_target?: float,
            bonus_type?: str
        }
    }
    Response: hr.contract_created { success: bool, contract: {...} }
    """
    company_id = payload.get("company_id")
    employee_id = payload.get("employee_id")
    mandate_path = payload.get("mandate_path")
    data = payload.get("data", {})
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing employee_id", "code": "MISSING_EMPLOYEE_ID"}
        })
        return

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    # Validate required fields
    contract_type = data.get("contract_type")
    start_date = data.get("start_date")
    base_salary = data.get("base_salary")

    if not contract_type or not start_date or base_salary is None:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {
                "error": "Missing required fields: contract_type, start_date, base_salary",
                "code": "MISSING_REQUIRED_FIELDS"
            }
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Create the contract
        result = await handlers.create_contract(
            company_id=hr_company_id,
            employee_id=employee_id,
            contract_type=contract_type,
            start_date=start_date,
            base_salary=float(base_salary),
            firebase_user_id=uid,
            # Optional fields from data
            currency=data.get("currency", "CHF"),
            work_rate=data.get("work_rate", 100.0),
            end_date=data.get("end_date"),
            weekly_hours=data.get("weekly_hours", 42.0),
            annual_leave_days=data.get("annual_leave_days", 25),
            remuneration_type=data.get("remuneration_type", "monthly"),
            job_title=data.get("job_title"),
            department=data.get("department"),
            thirteenth_month=data.get("thirteenth_month", False),
            thirteenth_month_rate=data.get("thirteenth_month_rate"),
            bonus_target=data.get("bonus_target"),
            bonus_type=data.get("bonus_type"),
        )

        if result.get("contract_id"):
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

            # Fetch the created contract
            contracts_result = await handlers.list_contracts(
                company_id=hr_company_id,
                employee_id=employee_id,
                firebase_user_id=uid,
            )
            contracts = contracts_result.get("contracts", [])

            # Find the newly created contract
            created_contract = next(
                (c for c in contracts if str(c.get("id")) == str(result["contract_id"])),
                {"id": result["contract_id"]}
            )

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.CONTRACT_CREATED,
                "payload": {
                    "success": True,
                    "contract": created_contract,
                    "employee_id": employee_id,
                }
            })

            logger.info(
                f"[HR] Contract created: uid={uid} employee={employee_id} "
                f"contract_id={result['contract_id']}"
            )
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to create contract"),
                    "code": "CONTRACT_CREATE_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[HR] Create contract failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "CONTRACT_CREATE_ERROR"}
        })


async def handle_contract_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.contract_update - Update an existing contract.

    Event: hr.contract_update
    Payload: {
        company_id: str,
        contract_id: str,
        hr_company_id: str,
        employee_id?: str,
        data: { ... fields to update ... }
    }
    Response: hr.contract_updated { success: bool, contract: {...} }

    NOTE: update_contract is not yet implemented in hr_rpc_handlers.
    This handler is prepared for future implementation.
    """
    company_id = payload.get("company_id")
    contract_id = payload.get("contract_id")
    employee_id = payload.get("employee_id")
    data = payload.get("data", {})
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not contract_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing contract_id", "code": "MISSING_CONTRACT_ID"}
        })
        return

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Map camelCase → snake_case + pack provisions
        update_fields = {}
        field_mapping = {
            "contractType": "contract_type",
            "baseSalary": "base_salary",
            "startDate": "start_date",
            "endDate": "end_date",
            "workRate": "work_rate",
            "weeklyHours": "weekly_hours",
            "annualLeaveDays": "annual_leave_days",
            "remunerationType": "remuneration_type",
            "jobTitle": "job_title",
            "department": "department",
            "isActive": "is_active",
            "countryCode": "country_code",
        }
        # Direct snake_case fields
        direct_fields = ["currency"]
        for f in direct_fields:
            if f in data:
                update_fields[f] = data[f]

        for frontend_key, backend_key in field_mapping.items():
            if frontend_key in data:
                update_fields[backend_key] = data[frontend_key]

        # Pack provisions JSONB
        provisions = {}
        prov_mapping = {
            "thirteenthMonth": "thirteenth_month",
            "thirteenthMonthRate": "thirteenth_month_rate",
            "bonusTarget": "bonus_target",
            "bonusType": "bonus_type",
        }
        for frontend_key, backend_key in prov_mapping.items():
            if frontend_key in data:
                provisions[backend_key] = data[frontend_key]
        if provisions:
            update_fields["provisions"] = provisions

        result = await handlers.update_contract(
            company_id=hr_company_id,
            contract_id=contract_id,
            employee_id=employee_id,
            firebase_user_id=uid,
            **update_fields,
        )

        if result.get("success"):
            # Invalidate page state cache
            if company_id:
                _invalidate_page_state(uid, company_id)

            # Fetch updated contract to send back
            updated_contract = None
            if employee_id:
                contracts_result = await handlers.list_contracts(
                    company_id=hr_company_id,
                    employee_id=employee_id,
                    firebase_user_id=uid,
                )
                contracts = contracts_result.get("contracts", [])
                updated_contract = next(
                    (c for c in contracts if str(c.get("id")) == str(contract_id)),
                    None
                )

            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.CONTRACT_UPDATED,
                "payload": {
                    "success": True,
                    "contract": updated_contract or {"id": contract_id},
                    "employee_id": employee_id,
                }
            })

            logger.info(
                f"[HR] Contract updated: uid={uid} contract={contract_id}"
            )
        else:
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {
                    "error": result.get("error", "Failed to update contract"),
                    "code": "CONTRACT_UPDATE_ERROR"
                }
            })

    except Exception as e:
        logger.error(f"[HR] Update contract failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "CONTRACT_UPDATE_ERROR"}
        })


async def handle_active_contract_get(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.active_contract_get - Get the active contract for an employee.

    Event: hr.active_contract_get
    Payload: { company_id: str, employee_id: str, hr_company_id?: str }
    Response: hr.active_contract_loaded { success: bool, contract: {...} | null }
    """
    company_id = payload.get("company_id")
    employee_id = payload.get("employee_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing employee_id", "code": "MISSING_EMPLOYEE_ID"}
        })
        return

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()
        result = await handlers.get_active_contract(
            company_id=hr_company_id,
            employee_id=employee_id,
            firebase_user_id=uid,
        )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ACTIVE_CONTRACT_LOADED,
            "payload": {
                "success": True,
                "contract": result.get("contract"),
                "employee_id": employee_id,
            }
        })

        has_contract = result.get("contract") is not None
        logger.info(
            f"[HR] Active contract fetched: uid={uid} employee={employee_id} "
            f"has_contract={has_contract}"
        )

    except Exception as e:
        logger.error(f"[HR] Get active contract failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "ACTIVE_CONTRACT_GET_ERROR"}
        })


async def handle_payroll_history_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle hr.payroll_history_list - List payroll results.

    Event: hr.payroll_history_list
    Payload: {
        company_id: str,
        year?: int,
        employee_id?: str,
    }
    Response: hr.payroll_history_loaded { success: bool, payroll_results: [...] }
    """
    company_id = payload.get("company_id")
    year = payload.get("year")
    employee_id = payload.get("employee_id")
    hr_company_id = await _resolve_hr_company_id(uid, company_id, payload)

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id", "code": "MISSING_HR_COMPANY_ID"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()
        result = await handlers.list_payroll_results(
            company_id=hr_company_id,
            employee_id=employee_id,
            year=year,
            firebase_user_id=uid,
        )

        payroll_results = result.get("payroll_results", [])

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.PAYROLL_HISTORY_LOADED,
            "payload": {
                "success": True,
                "payroll_results": payroll_results,
            }
        })

        logger.info(
            f"[HR] Payroll history loaded: uid={uid} count={len(payroll_results)} "
            f"year={year} employee={employee_id}"
        )

    except Exception as e:
        logger.error(f"[HR] Payroll history list failed: {e}", exc_info=True)
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": str(e), "code": "PAYROLL_HISTORY_ERROR"}
        })


# ============================================
# Event Router
# ============================================

# Map event types to handlers
HR_EVENT_HANDLERS: Dict[str, Callable[[str, str, Dict[str, Any]], Awaitable[None]]] = {
    WS_EVENTS.HR.ORCHESTRATE_INIT: handle_orchestrate_init,
    WS_EVENTS.HR.REFRESH: handle_refresh,
    WS_EVENTS.HR.EMPLOYEES_LIST: handle_employees_list,
    WS_EVENTS.HR.EMPLOYEE_GET: handle_employee_get,
    WS_EVENTS.HR.EMPLOYEE_CREATE: handle_employee_create,
    WS_EVENTS.HR.EMPLOYEE_UPDATE: handle_employee_update,
    WS_EVENTS.HR.EMPLOYEE_DELETE: handle_employee_delete,
    WS_EVENTS.HR.PAYROLL_CALCULATE: handle_payroll_calculate,
    WS_EVENTS.HR.SETTINGS_GET: handle_settings_get,
    WS_EVENTS.HR.SETTINGS_UPDATE: handle_settings_update,
    WS_EVENTS.HR.METRICS_GET: handle_metrics_get,
    # Rubrics handlers
    WS_EVENTS.HR.RUBRICS_LIST: handle_rubrics_list,
    WS_EVENTS.HR.RUBRIC_TOGGLE: handle_rubric_toggle,
    WS_EVENTS.HR.RUBRIC_UPDATE: handle_rubric_update,
    # Contracts handlers
    WS_EVENTS.HR.CONTRACTS_LIST: handle_contracts_list,
    WS_EVENTS.HR.CONTRACT_CREATE: handle_contract_create,
    WS_EVENTS.HR.CONTRACT_UPDATE: handle_contract_update,
    WS_EVENTS.HR.ACTIVE_CONTRACT_GET: handle_active_contract_get,
    # Payroll History
    WS_EVENTS.HR.PAYROLL_HISTORY_LIST: handle_payroll_history_list,
}


async def handle_hr_event(
    event_type: str,
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> bool:
    """
    Route HR WebSocket event to appropriate handler.

    Args:
        event_type: WebSocket event type (e.g., 'hr.orchestrate_init')
        uid: Firebase user ID
        session_id: WebSocket session ID
        payload: Event payload data

    Returns:
        True if event was handled, False otherwise
    """
    handler = HR_EVENT_HANDLERS.get(event_type)
    if handler:
        await handler(uid, session_id, payload)
        return True
    return False


def register_hr_handlers(router: Dict[str, Callable]) -> None:
    """
    Register HR event handlers with the main WebSocket router.

    Args:
        router: Dictionary mapping event types to handlers
    """
    for event_type, handler in HR_EVENT_HANDLERS.items():
        router[event_type] = handler

    logger.info(f"[HR] Registered {len(HR_EVENT_HANDLERS)} WebSocket event handlers")
