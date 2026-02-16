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
    System rubrics come from Jobber (get_payroll_items).
    Company-specific overrides are stored in Firestore:
    Path: {mandate_path}/hr/rubric_overrides/{rubric_code}
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

        # TODO: Add when implemented in hr_rpc_handlers
        # contracts_task = handlers.list_all_contracts(company_id=hr_company_id, firebase_user_id=uid)
        # rubrics_task = handlers.get_payroll_items(country_code="CH")

        employees_result, settings = await asyncio.gather(
            employees_task,
            settings_task,
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
    hr_company_id = payload.get("hr_company_id")

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id"}
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
    hr_company_id = payload.get("hr_company_id")
    employee_id = payload.get("employee_id")

    if not hr_company_id or not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or employee_id"}
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
    hr_company_id = payload.get("hr_company_id")
    data = payload.get("data", {})

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Map frontend EmployeeFormData to backend create_employee params
        result = await handlers.create_employee(
            company_id=hr_company_id,
            identifier=data.get("email", ""),  # Use email as identifier
            first_name=data.get("firstName", ""),
            last_name=data.get("lastName", ""),
            birth_date=data.get("birthDate", "2000-01-01"),
            cluster_code=data.get("department", "DEFAULT"),
            hire_date=data.get("startDate", ""),
            firebase_user_id=uid,
            # Additional fields
            email=data.get("email"),
            phone=data.get("phone"),
            position=data.get("position"),
            status=data.get("status", "active"),
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
    hr_company_id = payload.get("hr_company_id")
    employee_id = payload.get("employee_id")
    data = payload.get("data", {})

    if not hr_company_id or not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or employee_id"}
        })
        return

    try:
        handlers = get_hr_rpc_handlers()

        # Map frontend fields to backend fields
        update_fields = {}
        if "firstName" in data:
            update_fields["first_name"] = data["firstName"]
        if "lastName" in data:
            update_fields["last_name"] = data["lastName"]
        if "email" in data:
            update_fields["email"] = data["email"]
        if "phone" in data:
            update_fields["phone"] = data["phone"]
        if "position" in data:
            update_fields["position"] = data["position"]
        if "department" in data:
            update_fields["cluster_code"] = data["department"]
        if "status" in data:
            update_fields["status"] = data["status"]

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
    hr_company_id = payload.get("hr_company_id")
    employee_id = payload.get("employee_id")

    if not hr_company_id or not employee_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or employee_id"}
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
    """Handle hr.payroll_calculate - Calculate payroll (async job)."""
    hr_company_id = payload.get("hr_company_id")
    period = payload.get("period")  # Format: "2024-01"
    mandate_path = payload.get("mandate_path")

    if not hr_company_id or not period:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id or period"}
        })
        return

    try:
        # Parse period
        year, month = period.split("-")

        handlers = get_hr_rpc_handlers()

        # Submit batch payroll calculation (async)
        result = await handlers.submit_payroll_batch(
            user_id=uid,
            company_id=hr_company_id,
            year=int(year),
            month=int(month),
            session_id=session_id,
            mandate_path=mandate_path,
        )

        # Notify that calculation started
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.PAYROLL_CALCULATING,
            "payload": {
                "success": True,
                "job_id": result.get("job_id"),
                "period": period,
            }
        })

        logger.info(
            f"[HR] Payroll calculation started: job_id={result.get('job_id')} "
            f"period={period}"
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
    hr_company_id = payload.get("hr_company_id")

    if not hr_company_id:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {"error": "Missing hr_company_id"}
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
    Load company-specific rubric overrides from Firestore.

    Path: {mandate_path}/hr/rubric_overrides/{rubric_code}

    Returns:
        Dict mapping rubric_code to override data
    """
    try:
        from app.firebase_providers import get_firebase_management

        firebase = get_firebase_management()
        collection_path = f"{mandate_path}/hr/rubric_overrides"

        # Get all documents in the collection
        docs = await asyncio.to_thread(
            firebase.get_collection,
            collection_path
        )

        overrides = {}
        if docs:
            for doc_id, doc_data in docs.items():
                overrides[doc_id] = doc_data

        logger.info(f"[HR] Loaded {len(overrides)} rubric overrides from Firestore")
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
    Save a rubric override to Firestore.

    Path: {mandate_path}/hr/rubric_overrides/{rubric_code}
    """
    try:
        from app.firebase_providers import get_firebase_management

        firebase = get_firebase_management()
        doc_path = f"{mandate_path}/hr/rubric_overrides/{rubric_code}"

        # Add metadata
        data_with_meta = {
            **override_data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": uid,
        }

        await asyncio.to_thread(
            firebase.set_document,
            doc_path,
            data_with_meta,
            True  # merge=True
        )

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
    hr_company_id = payload.get("hr_company_id")

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
    hr_company_id = payload.get("hr_company_id")
    mandate_path = payload.get("mandate_path")
    data = payload.get("data", {})

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
    hr_company_id = payload.get("hr_company_id")
    employee_id = payload.get("employee_id")
    data = payload.get("data", {})

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

        # Check if update_contract is implemented
        if not hasattr(handlers, 'update_contract'):
            await hub.broadcast(uid, {
                "type": WS_EVENTS.HR.ERROR,
                "payload": {
                    "error": "Contract update not yet implemented",
                    "code": "NOT_IMPLEMENTED"
                }
            })
            logger.warning("[HR] update_contract not implemented in hr_rpc_handlers")
            return

        # TODO: Call handlers.update_contract when implemented
        # result = await handlers.update_contract(
        #     company_id=hr_company_id,
        #     contract_id=contract_id,
        #     firebase_user_id=uid,
        #     **data,
        # )

        await hub.broadcast(uid, {
            "type": WS_EVENTS.HR.ERROR,
            "payload": {
                "error": "Contract update not yet implemented",
                "code": "NOT_IMPLEMENTED"
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
    hr_company_id = payload.get("hr_company_id")

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
