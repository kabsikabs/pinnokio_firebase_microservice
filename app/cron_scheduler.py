"""
CRON Scheduler pour l'exécution automatique des tâches planifiées.

Fonctionnement:
    1. Boucle toutes les N secondes (défaut: 60s)
    2. Appelle firebase.get_tasks_ready_for_execution_utc(now_utc)
    3. Pour chaque tâche due:
       a. Créer execution_id
       b. Créer thread_key
       c. Lancer _execute_scheduled_task()
       d. Mettre à jour next_execution (si SCHEDULED)
       e. Désactiver tâche (si ONE_TIME)
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cron_scheduler")


class CronScheduler:
    """
    Scheduler CRON pour l'exécution automatique des tâches.
    """

    def __init__(self, check_interval: int = 60):
        """
        Initialise le scheduler.

        Args:
            check_interval: Intervalle en secondes entre chaque vérification (défaut: 60)
        """
        self.check_interval = check_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None

        logger.info(f"[CRON] Scheduler initialisé (intervalle: {check_interval}s)")

    async def start(self):
        """Démarre le scheduler."""
        if self.running:
            logger.warning("[CRON] Scheduler déjà en cours d'exécution")
            return

        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[CRON] Scheduler démarré")

    async def stop(self):
        """Arrête le scheduler."""
        if not self.running:
            logger.warning("[CRON] Scheduler déjà arrêté")
            return

        self.running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("[CRON] Scheduler arrêté")

    async def _run_loop(self):
        """Boucle principale du scheduler."""
        logger.info("[CRON] Boucle principale démarrée")

        while self.running:
            try:
                await self._check_and_execute_tasks()
            except Exception as e:
                logger.error(f"[CRON] Erreur dans la boucle: {e}", exc_info=True)

            # Attendre avant la prochaine itération
            await asyncio.sleep(self.check_interval)

    async def _check_and_execute_tasks(self):
        """
        Vérifie et exécute les tâches dues.

        Steps:
            1. Obtenir now_utc
            2. Appeler firebase.get_tasks_ready_for_execution_utc(now_utc)
            3. Pour chaque tâche:
               await self._execute_task(task_data, now_utc)
        """
        try:
            from .firebase_providers import get_firebase_management
            fbm = get_firebase_management()

            # 1. Timestamp UTC actuel
            now_utc = datetime.now(timezone.utc)

            logger.debug(f"[CRON] Vérification des tâches à {now_utc.isoformat()}")

            # 2. Récupérer les tâches prêtes
            tasks_ready = fbm.get_tasks_ready_for_execution_utc(now_utc)

            if not tasks_ready:
                logger.debug("[CRON] Aucune tâche prête pour exécution")
                return

            logger.info(f"[CRON] {len(tasks_ready)} tâche(s) prête(s) pour exécution")

            # 3. Exécuter chaque tâche
            for task_data in tasks_ready:
                try:
                    await self._execute_task(task_data, now_utc)
                except Exception as e:
                    task_id = task_data.get("task_id", "unknown")
                    logger.error(f"[CRON] Erreur exécution tâche {task_id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[CRON] Erreur _check_and_execute_tasks: {e}", exc_info=True)

    async def _execute_task(self, task_data: dict, triggered_at: datetime):
        """
        Exécute une tâche.

        Steps:
            1. Générer execution_id et thread_key
            2. Créer document d'exécution (firebase.create_task_execution)
            3. Créer chat RTDB (firebase_realtime.create_chat)
            4. Lancer execution via LLM Manager (asyncio.create_task)
            5. Mettre à jour next_execution:
               - SCHEDULED: Calculer prochaine occurrence
               - ONE_TIME: Désactiver la tâche
        """
        try:
            task_id = task_data["task_id"]
            user_id = task_data["user_id"]
            company_id = task_data["company_id"]
            mandate_path = task_data["mandate_path"]
            execution_plan = task_data["execution_plan"]

            logger.info(f"[CRON] 🚀 Exécution tâche: {task_id} (user={user_id}, company={company_id})")

            # 1. Générer IDs
            execution_id = f"exec_{uuid.uuid4().hex[:12]}"
            timestamp = int(triggered_at.timestamp())
            thread_key = f"task_{task_id}_{timestamp}"

            # 2. Créer document d'exécution
            from .firebase_providers import get_firebase_management
            fbm = get_firebase_management()

            execution_data = {
                "execution_id": execution_id,
                "task_id": task_id,
                "thread_key": thread_key,
                "status": "running",
                "started_at": triggered_at.isoformat(),
                "workflow_checklist": None,  # Sera créée par l'agent
                "lpt_tasks": {}
            }

            fbm.create_task_execution(mandate_path, task_id, execution_data)

            # 3. Créer chat RTDB
            from .firebase_providers import get_firebase_realtime
            rtdb = get_firebase_realtime()

            mission_title = task_data.get("mission", {}).get("title", "Tâche planifiée")

            chat_result = rtdb.create_chat(
                user_id=user_id,
                space_code=company_id,
                thread_name=mission_title,
                mode="chats",
                chat_mode="task_execution",
                thread_key=thread_key
            )

            if not chat_result.get("success"):
                raise ValueError(f"Échec création chat: {chat_result}")

            # 4. Lancer l'exécution (async task)
            from .llm_service.llm_manager import get_llm_manager
            llm_manager = get_llm_manager()

            asyncio.create_task(
                llm_manager._execute_scheduled_task(
                    user_id=user_id,
                    company_id=company_id,
                    task_data=task_data,
                    thread_key=thread_key,
                    execution_id=execution_id
                )
            )

            logger.info(f"[CRON] ✅ Tâche lancée: {task_id} | Thread: {thread_key}")

            # 5. Mettre à jour next_execution
            if execution_plan == "SCHEDULED":
                await self._update_scheduled_task(fbm, task_data, triggered_at)

            elif execution_plan == "ONE_TIME":
                await self._disable_one_time_task(fbm, task_data, triggered_at)

        except Exception as e:
            logger.error(f"[CRON] Erreur _execute_task: {e}", exc_info=True)

    async def _update_scheduled_task(self, fbm, task_data: dict, triggered_at: datetime):
        """
        Met à jour une tâche SCHEDULED après déclenchement.

        Actions:
            - Calculer next_execution (local_time et UTC)
            - Mettre à jour task document
            - Mettre à jour /scheduled_tasks
        """
        try:
            task_id = task_data["task_id"]
            mandate_path = task_data["mandate_path"]
            schedule = task_data.get("schedule", {})

            cron_expr = schedule.get("cron_expression")
            timezone_str = schedule.get("timezone")

            if not cron_expr or not timezone_str:
                logger.error(f"[CRON] Données schedule manquantes pour {task_id}")
                return

            # Calculer prochaine occurrence
            next_local, next_utc = fbm.calculate_task_next_execution(
                cron_expr, timezone_str, from_time=triggered_at
            )

            if not next_local or not next_utc:
                logger.error(f"[CRON] Erreur calcul next_execution pour {task_id}")
                return

            # Mettre à jour task document
            fbm.update_task(
                mandate_path, task_id,
                {
                    "schedule.next_execution_local_time": next_local,
                    "schedule.next_execution_utc": next_utc,
                    "execution_count": task_data.get("execution_count", 0) + 1
                }
            )

            # Mettre à jour aussi dans /scheduled_tasks
            job_id = f"{mandate_path.replace('/', '_')}_{task_id}"
            scheduler_ref = fbm.db.collection("scheduled_tasks").document(job_id)

            scheduler_ref.update({
                "next_execution_local_time": next_local,
                "next_execution_utc": next_utc,
                "updated_at": fbm.db.SERVER_TIMESTAMP
            })

            logger.info(f"[CRON] Prochaine exécution: {next_local} (local) | {next_utc} (UTC)")

        except Exception as e:
            logger.error(f"[CRON] Erreur _update_scheduled_task: {e}", exc_info=True)

    async def _disable_one_time_task(self, fbm, task_data: dict, triggered_at: datetime):
        """
        Désactive une tâche ONE_TIME après exécution.

        Actions:
            - Marquer enabled=False et status=completed
            - Supprimer de /scheduled_tasks
        """
        try:
            task_id = task_data["task_id"]
            mandate_path = task_data["mandate_path"]

            # Désactiver la tâche
            fbm.update_task(
                mandate_path, task_id,
                {
                    "enabled": False,
                    "status": "completed",
                    "completed_at": triggered_at.isoformat()
                }
            )

            # Supprimer de /scheduled_tasks
            job_id = f"{mandate_path.replace('/', '_')}_{task_id}"
            fbm.delete_scheduler_job_completely(job_id)

            logger.info(f"[CRON] Tâche ONE_TIME désactivée: {task_id}")

        except Exception as e:
            logger.error(f"[CRON] Erreur _disable_one_time_task: {e}", exc_info=True)


# Singleton global
_CRON_SCHEDULER_SINGLETON: Optional[CronScheduler] = None


def get_cron_scheduler() -> CronScheduler:
    """Retourne l'instance singleton du scheduler CRON."""
    global _CRON_SCHEDULER_SINGLETON

    if _CRON_SCHEDULER_SINGLETON is None:
        _CRON_SCHEDULER_SINGLETON = CronScheduler(check_interval=60)

    return _CRON_SCHEDULER_SINGLETON
