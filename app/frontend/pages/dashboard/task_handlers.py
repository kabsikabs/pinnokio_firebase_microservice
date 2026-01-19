"""
Task Handlers - Wrapper Layer
==============================

Handlers WebSocket pour la gestion des tâches planifiées et on-demand.
Permet l'exécution et le toggle des tâches depuis le dashboard Next.js.

NAMESPACE: TASK

Architecture:
    Frontend (Next.js) → WebSocket → task_handlers.py → FirebaseManagement/RPC

Events gérés:
    - task.list: Liste des tâches pour un mandat
    - task.execute: Exécution immédiate d'une tâche
    - task.toggle_enabled: Active/désactive une tâche
    - task.executed: Résultat d'exécution (broadcast)
    - task.status_changed: Changement de statut (broadcast)

Author: Migration Agent
Created: 2026-01-18
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.firebase_providers import FirebaseManagement
from app.redis_client import get_redis
from app.ws_events import WS_EVENTS
from app.ws_hub import hub

logger = logging.getLogger("task.handlers")


# ============================================
# CONSTANTS
# ============================================

TTL_TASKS_CACHE = 60  # 1 minute


# ============================================
# SINGLETON
# ============================================

_task_handlers_instance: Optional["TaskHandlers"] = None


def get_task_handlers() -> "TaskHandlers":
    """Singleton accessor pour les handlers task."""
    global _task_handlers_instance
    if _task_handlers_instance is None:
        _task_handlers_instance = TaskHandlers()
    return _task_handlers_instance


class TaskHandlers:
    """
    Handlers pour le namespace TASK.

    Méthodes:
    - list_tasks: Liste les tâches pour un mandat
    - execute_task: Exécute une tâche immédiatement
    - toggle_task_enabled: Active/désactive une tâche
    """

    NAMESPACE = "TASK"

    # ============================================
    # LIST TASKS
    # ============================================

    async def list_tasks(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
    ) -> Dict[str, Any]:
        """
        Liste toutes les tâches pour un mandat avec groupement temporel.

        RPC: TASK.list_tasks

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat

        Returns:
            {
                "success": True,
                "data": {
                    "planned_tasks": [...],
                    "on_demand_tasks": [...],
                    "grouped": {
                        "today": [...],
                        "this_week": [...],
                        "later": [...],
                        "no_schedule": [...]
                    },
                    "counts": {...}
                }
            }
        """
        try:
            redis = get_redis()
            cache_key = f"tasks:{company_id}"

            # Check cache
            cached = redis.get(cache_key)
            if cached:
                import json
                data = json.loads(cached if isinstance(cached, str) else cached.decode())
                logger.info(f"TASK.list_tasks company_id={company_id} source=cache")
                return {"success": True, "data": data}

            # Fetch from Firebase
            firebase = FirebaseManagement()
            all_tasks = await asyncio.to_thread(
                firebase.list_tasks_for_mandate,
                mandate_path
            )

            if not all_tasks:
                all_tasks = []

            # Transform and group
            planned_tasks = []
            on_demand_tasks = []
            today_tasks = []
            this_week_tasks = []
            later_tasks = []
            no_schedule_tasks = []

            now = datetime.now(timezone.utc)
            today_date = now.date()
            week_end = today_date + timedelta(days=7)

            for task in all_tasks:
                task_data = self._format_task(task)

                if task_data["executionPlan"] in ["SCHEDULED", "ONE_TIME"]:
                    planned_tasks.append(task_data)

                    # Group by time period
                    next_exec = task_data.get("nextExecution", "")
                    if not next_exec:
                        no_schedule_tasks.append(task_data)
                    else:
                        try:
                            exec_date = datetime.fromisoformat(
                                next_exec.replace('Z', '+00:00')
                            ).date()

                            if exec_date == today_date:
                                today_tasks.append(task_data)
                            elif exec_date <= week_end:
                                this_week_tasks.append(task_data)
                            else:
                                later_tasks.append(task_data)
                        except Exception:
                            no_schedule_tasks.append(task_data)

                elif task_data["executionPlan"] == "ON_DEMAND":
                    on_demand_tasks.append(task_data)

            # Sort by next execution
            planned_tasks.sort(key=lambda x: x.get("nextExecution", "9999"))

            result = {
                "plannedTasks": planned_tasks,
                "onDemandTasks": on_demand_tasks,
                "grouped": {
                    "today": today_tasks,
                    "thisWeek": this_week_tasks,
                    "later": later_tasks,
                    "noSchedule": no_schedule_tasks
                },
                "counts": {
                    "totalPlanned": len(planned_tasks),
                    "totalOnDemand": len(on_demand_tasks),
                    "today": len(today_tasks),
                    "thisWeek": len(this_week_tasks)
                }
            }

            # Cache result
            import json
            redis.setex(cache_key, TTL_TASKS_CACHE, json.dumps(result))

            logger.info(
                f"TASK.list_tasks company_id={company_id} "
                f"planned={len(planned_tasks)} on_demand={len(on_demand_tasks)}"
            )

            return {"success": True, "data": result}

        except Exception as e:
            logger.error(f"TASK.list_tasks error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "TASK_LIST_ERROR", "message": str(e)}
            }

    def _format_task(self, task: Dict) -> Dict[str, Any]:
        """
        Formate une tâche pour la réponse frontend.

        Structure complète matching Reflex TaskData:
        - mission: title, description, plan
        - schedule: frequency, time, timezone, cron, next_execution
        - lastExecutionReport: executed_at, status, duration, summary, errors
        """
        mission = task.get("mission", {})
        schedule = task.get("schedule", {})
        last_report = task.get("last_execution_report") or {}

        task_id = task.get("task_id", task.get("id", ""))

        return {
            "id": task_id,
            "taskId": task_id,
            "executionPlan": task.get("execution_plan", "ON_DEMAND"),
            # Mission info (aliased for convenience)
            "title": mission.get("title", ""),
            "description": mission.get("description", ""),
            "mission": {
                "title": mission.get("title", ""),
                "description": mission.get("description", ""),
                "plan": mission.get("plan", "")
            },
            # Schedule info
            "schedule": {
                "frequency": schedule.get("frequency", ""),
                "time": schedule.get("time", ""),
                "timezone": schedule.get("timezone", "Europe/Paris"),
                "nextExecutionLocalTime": schedule.get("next_execution_local_time", ""),
                "nextExecutionUtc": schedule.get("next_execution_utc", ""),
                "cronExpression": schedule.get("cron_expression", ""),
                "dayOfWeek": schedule.get("day_of_week"),
                "dayOfMonth": schedule.get("day_of_month")
            },
            "nextExecution": schedule.get("next_execution_utc", "") or schedule.get("next_execution_local_time", ""),
            "frequency": schedule.get("frequency", ""),
            "scheduledNextExecution": task.get("scheduled_next_execution", ""),
            # Status
            "status": task.get("status", "inactive"),
            "enabled": task.get("enabled", False),
            # Timestamps
            "createdAt": task.get("created_at", ""),
            "updatedAt": task.get("updated_at", ""),
            # Execution stats
            "executionCount": task.get("execution_count", 0),
            "lastExecutionReport": {
                "executedAt": last_report.get("executed_at", ""),
                "executionId": last_report.get("execution_id", ""),
                "status": last_report.get("status", ""),
                "durationSeconds": last_report.get("duration_seconds", 0),
                "summary": last_report.get("summary", ""),
                "errors": last_report.get("errors", [])
            } if last_report else None
        }

    # ============================================
    # EXECUTE TASK
    # ============================================

    async def execute_task(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """
        Exécute une tâche immédiatement via le microservice LLM.

        RPC: TASK.execute_task

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            task_id: ID de la tâche à exécuter

        Returns:
            {
                "success": True,
                "data": {
                    "execution_id": "...",
                    "thread_key": "...",
                    "task_title": "..."
                }
            }
        """
        try:
            logger.info(f"TASK.execute_task task_id={task_id} user_id={user_id}")

            # Get task details first
            firebase = FirebaseManagement()
            task_path = f"{mandate_path}/tasks/{task_id}"
            task_doc = await asyncio.to_thread(
                firebase.get_document,
                task_path
            )

            task_title = "Unknown Task"
            if task_doc:
                mission = task_doc.get("mission", {})
                task_title = mission.get("title", task_id)

            # Call LLM microservice via RPC
            from app.rpc_client import rpc_call, RPCError

            try:
                result = await asyncio.to_thread(
                    rpc_call,
                    "LLM.execute_task_now",
                    kwargs={
                        "mandate_path": mandate_path,
                        "task_id": task_id,
                        "user_id": user_id,
                        "company_id": company_id
                    },
                    user_id=user_id,
                    timeout_ms=30000
                )

                if result and result.get("success"):
                    execution_data = {
                        "executionId": result.get("execution_id", ""),
                        "threadKey": result.get("thread_key", task_id),
                        "taskTitle": result.get("task_title", task_title)
                    }

                    # Broadcast execution started
                    await hub.broadcast(user_id, {
                        "type": "task.executed",
                        "payload": {
                            "success": True,
                            "taskId": task_id,
                            **execution_data
                        }
                    })

                    # Invalidate tasks cache
                    redis = get_redis()
                    redis.delete(f"tasks:{company_id}")

                    logger.info(
                        f"TASK.execute_task success task_id={task_id} "
                        f"execution_id={execution_data['executionId']}"
                    )

                    return {"success": True, "data": execution_data}
                else:
                    error_msg = result.get("error", "Unknown error") if result else "No response"
                    logger.error(f"TASK.execute_task RPC failed: {error_msg}")
                    return {
                        "success": False,
                        "error": {"code": "TASK_EXECUTE_ERROR", "message": error_msg}
                    }

            except RPCError as rpc_err:
                logger.error(f"TASK.execute_task RPC error: {rpc_err}")
                return {
                    "success": False,
                    "error": {"code": "RPC_ERROR", "message": str(rpc_err)}
                }

        except Exception as e:
            logger.error(f"TASK.execute_task error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "TASK_EXECUTE_ERROR", "message": str(e)}
            }

    # ============================================
    # TOGGLE TASK ENABLED
    # ============================================

    async def toggle_task_enabled(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        task_id: str,
        enabled: bool,
    ) -> Dict[str, Any]:
        """
        Active ou désactive une tâche.

        RPC: TASK.toggle_task_enabled

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            task_id: ID de la tâche
            enabled: Nouvel état (True/False)

        Returns:
            {"success": True, "data": {"enabled": bool}}
        """
        try:
            logger.info(f"TASK.toggle_task_enabled task_id={task_id} enabled={enabled}")

            firebase = FirebaseManagement()

            # Update task in Firebase
            success = await asyncio.to_thread(
                firebase.update_task,
                mandate_path=mandate_path,
                task_id=task_id,
                updates={"enabled": enabled}
            )

            if success:
                # Broadcast status change
                await hub.broadcast(user_id, {
                    "type": "task.status_changed",
                    "payload": {
                        "taskId": task_id,
                        "enabled": enabled,
                        "companyId": company_id
                    }
                })

                # Invalidate tasks cache
                redis = get_redis()
                redis.delete(f"tasks:{company_id}")

                logger.info(f"TASK.toggle_task_enabled success task_id={task_id}")
                return {"success": True, "data": {"enabled": enabled}}
            else:
                return {
                    "success": False,
                    "error": {"code": "TASK_UPDATE_ERROR", "message": "Failed to update task"}
                }

        except Exception as e:
            logger.error(f"TASK.toggle_task_enabled error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "TASK_TOGGLE_ERROR", "message": str(e)}
            }

    # ============================================
    # UPDATE TASK
    # ============================================

    async def update_task(
        self,
        user_id: str,
        company_id: str,
        mandate_path: str,
        task_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Met à jour une tâche avec les champs fournis.

        RPC: TASK.update_task

        Args:
            user_id: Firebase UID
            company_id: Company ID
            mandate_path: Chemin du mandat
            task_id: ID de la tâche
            updates: Dict des champs à mettre à jour
                Peut inclure:
                - mission: {title, description, plan}
                - schedule: {frequency, time, timezone, ...}
                - enabled: bool
                - execution_plan: SCHEDULED | ONE_TIME | ON_DEMAND

        Returns:
            {"success": True, "data": {updated_task}}
        """
        try:
            logger.info(f"TASK.update_task task_id={task_id} updates={list(updates.keys())}")

            firebase = FirebaseManagement()

            # Build the update dict for Firebase
            firebase_updates = {}
            now = datetime.now(timezone.utc).isoformat()

            # Handle mission updates
            if "mission" in updates:
                mission = updates["mission"]
                if isinstance(mission, dict):
                    for key in ["title", "description", "plan"]:
                        if key in mission:
                            firebase_updates[f"mission.{key}"] = mission[key]

            # Handle schedule updates
            if "schedule" in updates:
                schedule = updates["schedule"]
                if isinstance(schedule, dict):
                    for key in ["frequency", "time", "timezone", "cronExpression",
                                "dayOfWeek", "dayOfMonth"]:
                        if key in schedule:
                            # Convert camelCase to snake_case for Firebase
                            fb_key = key
                            if key == "cronExpression":
                                fb_key = "cron_expression"
                            elif key == "dayOfWeek":
                                fb_key = "day_of_week"
                            elif key == "dayOfMonth":
                                fb_key = "day_of_month"
                            firebase_updates[f"schedule.{fb_key}"] = schedule[key]

            # Handle top-level fields
            if "enabled" in updates:
                firebase_updates["enabled"] = updates["enabled"]
            if "execution_plan" in updates:
                firebase_updates["execution_plan"] = updates["execution_plan"]
            if "status" in updates:
                firebase_updates["status"] = updates["status"]

            # Always update the updated_at timestamp
            firebase_updates["updated_at"] = now

            if not firebase_updates:
                return {
                    "success": False,
                    "error": {"code": "NO_UPDATES", "message": "No valid update fields provided"}
                }

            # Update in Firebase
            success = await asyncio.to_thread(
                firebase.update_task,
                mandate_path=mandate_path,
                task_id=task_id,
                updates=firebase_updates
            )

            if success:
                # Fetch the updated task
                task_path = f"{mandate_path}/tasks/{task_id}"
                updated_doc = await asyncio.to_thread(
                    firebase.get_document,
                    task_path
                )

                updated_task = self._format_task(updated_doc) if updated_doc else {}

                # Broadcast update
                await hub.broadcast(user_id, {
                    "type": "task.updated",
                    "payload": {
                        "taskId": task_id,
                        "task": updated_task,
                        "companyId": company_id
                    }
                })

                # Invalidate tasks cache
                redis = get_redis()
                redis.delete(f"tasks:{company_id}")

                logger.info(f"TASK.update_task success task_id={task_id}")
                return {"success": True, "data": {"task": updated_task}}
            else:
                return {
                    "success": False,
                    "error": {"code": "TASK_UPDATE_ERROR", "message": "Failed to update task in Firebase"}
                }

        except Exception as e:
            logger.error(f"TASK.update_task error: {e}", exc_info=True)
            return {
                "success": False,
                "error": {"code": "TASK_UPDATE_ERROR", "message": str(e)}
            }


# ============================================
# WEBSOCKET EVENT HANDLERS
# ============================================

async def handle_task_list(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle task.list WebSocket event."""
    handlers = get_task_handlers()
    result = await handlers.list_tasks(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", "")
    )

    if result.get("success"):
        await hub.broadcast(uid, {
            "type": "dashboard.tasks_update",
            "payload": result
        })

    return {"type": "task.list", "payload": result}


async def handle_task_execute(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle task.execute WebSocket event."""
    handlers = get_task_handlers()
    result = await handlers.execute_task(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        task_id=payload.get("task_id", "")
    )
    return {"type": "task.execute", "payload": result}


async def handle_task_toggle(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle task.toggle_enabled WebSocket event."""
    logger.info(
        f"[WS] handle_task_toggle received - uid={uid} "
        f"company_id={payload.get('company_id')} "
        f"mandate_path={payload.get('mandate_path')} "
        f"task_id={payload.get('task_id')} "
        f"enabled={payload.get('enabled')}"
    )
    handlers = get_task_handlers()
    result = await handlers.toggle_task_enabled(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        task_id=payload.get("task_id", ""),
        enabled=payload.get("enabled", False)
    )
    logger.info(f"[WS] handle_task_toggle result - success={result.get('success')}")
    return {"type": "task.toggle_enabled", "payload": result}


async def handle_task_update(
    uid: str,
    session_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle task.update WebSocket event."""
    logger.info(
        f"[WS] handle_task_update received - uid={uid} "
        f"company_id={payload.get('company_id')} "
        f"mandate_path={payload.get('mandate_path')} "
        f"task_id={payload.get('task_id')} "
        f"updates_keys={list(payload.get('updates', {}).keys())}"
    )
    handlers = get_task_handlers()
    result = await handlers.update_task(
        user_id=uid,
        company_id=payload.get("company_id", ""),
        mandate_path=payload.get("mandate_path", ""),
        task_id=payload.get("task_id", ""),
        updates=payload.get("updates", {})
    )
    logger.info(f"[WS] handle_task_update result - success={result.get('success')}")
    return {"type": "task.update", "payload": result}


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "TaskHandlers",
    "get_task_handlers",
    "handle_task_list",
    "handle_task_execute",
    "handle_task_toggle",
    "handle_task_update",
]
