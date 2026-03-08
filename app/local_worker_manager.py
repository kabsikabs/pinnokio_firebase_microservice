"""
Local Worker Manager - Auto-start workers as subprocesses in dev (Codespace)
=============================================================================

Drop-in replacement for ECSManager when PINNOKIO_ENVIRONMENT=LOCAL.
Same interface: ensure_worker_running / scale_down / get_worker_status.

Instead of ECS Fargate scaling, it spawns workers as local subprocesses.
Workers self-terminate after idle timeout (WORKER_IDLE_TIMEOUT_SECONDS)
via their own _graceful_shutdown() which skips ECS when LOCAL.

Architecture:
    Job arrives → job_actions_handler.py
      → Step 1.6: LocalWorkerManager.ensure_worker_running(job_type)
            → proc.poll() is None?
                YES → worker already running
                NO  → subprocess.Popen("python main.py")
                     → logs → /tmp/klk_{type}.log

    Worker idle 15 min → _graceful_shutdown() → skip ECS → SIGTERM → exit
    Next job → ensure_worker_running() → detects proc dead → re-launch

Author: LocalWorkerManager Agent
Created: 2026-02-19
"""

import logging
import os
import socket
import subprocess
from typing import Dict, Optional

logger = logging.getLogger("local_worker_manager")

# Port each worker listens on (for duplicate-detection)
WORKER_PORTS = {
    "router": 8080,
    "apbookeeper": 8081,
    "bankbookeeper": 8082,
}

# Worker configurations: cwd must match repo checkout paths in Codespace
WORKER_CONFIG = {
    "router": {
        "cwd": "/workspaces/klk_router",
        "cmd": ["python", "main.py"],
        "log": "/tmp/klk_router.log",
    },
    "apbookeeper": {
        "cwd": "/workspaces/klk_accountant",
        "cmd": ["python", "main.py"],
        "log": "/tmp/klk_accountant.log",
    },
    "bankbookeeper": {
        "cwd": "/workspaces/klk_bank",
        "cmd": ["python", "main.py"],
        "log": "/tmp/klk_bank.log",
    },
}

# Job types served by the same worker process (e.g. onboarding runs inside klk_router)
JOB_TYPE_ALIAS = {
    "onboarding": "router",
}


class LocalWorkerManager:
    """Manages local worker subprocesses for dev/Codespace environments."""

    # Class-level process registry (singleton pattern, same as ECSManager)
    _processes: Dict[str, subprocess.Popen] = {}
    _log_files: Dict[str, object] = {}

    @classmethod
    def ensure_worker_running(cls, job_type: str) -> Dict:
        """
        Ensure the local worker subprocess for job_type is running.

        - If process exists and alive → return {"status": "already_running"}
        - If no process or dead → spawn new subprocess → return {"status": "starting"}

        Args:
            job_type: One of "router", "apbookeeper", "bankbookeeper"

        Returns:
            dict with "status" key (compatible with ECSManager interface)
        """
        # Resolve alias (e.g. "onboarding" → "router", same klk_router process)
        resolved_type = JOB_TYPE_ALIAS.get(job_type, job_type)
        config = WORKER_CONFIG.get(resolved_type)
        if not config:
            logger.warning(f"[LOCAL_WORKER] Unknown job_type: {job_type}")
            return {"status": "unknown_job_type"}

        if resolved_type != job_type:
            logger.info(f"[LOCAL_WORKER] Alias resolved: {job_type} → {resolved_type}")

        # Check if worker directory exists
        if not os.path.isdir(config["cwd"]):
            logger.error(f"[LOCAL_WORKER] Worker directory not found: {config['cwd']}")
            return {"status": "error", "error": f"Directory not found: {config['cwd']}"}

        # Check if process exists and is still alive (use resolved_type for process registry)
        proc = cls._processes.get(resolved_type)
        if proc and proc.poll() is None:
            logger.info(f"[LOCAL_WORKER] Worker {job_type} already running (pid={proc.pid})")
            return {"status": "already_running", "pid": proc.pid}

        # Check if an external process (manually started) is already listening on the port
        port = WORKER_PORTS.get(resolved_type)
        if port and cls._is_port_in_use(port):
            logger.info(f"[LOCAL_WORKER] Worker {resolved_type} port {port} already in use (external process), skipping launch")
            return {"status": "already_running", "pid": -1}

        # Clean up dead process reference
        if proc:
            logger.info(f"[LOCAL_WORKER] Worker {resolved_type} was dead (rc={proc.returncode}), restarting...")
            cls._cleanup_process(resolved_type)

        # Start new subprocess
        try:
            log_file = open(config["log"], "a")
            env = {**os.environ, "PINNOKIO_ENVIRONMENT": "LOCAL"}
            proc = subprocess.Popen(
                config["cmd"],
                cwd=config["cwd"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
            cls._processes[resolved_type] = proc
            cls._log_files[resolved_type] = log_file

            logger.info(
                f"[LOCAL_WORKER] Started worker {resolved_type} "
                f"(pid={proc.pid}, log={config['log']})"
            )
            return {"status": "starting", "pid": proc.pid}

        except Exception as e:
            logger.error(f"[LOCAL_WORKER] Failed to start worker {job_type}: {e}")
            return {"status": "error", "error": str(e)}

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        """Check if a port is already in use (another process listening)."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", port))
                return result == 0
        except Exception:
            return False

    @classmethod
    def scale_down(cls, job_type: str) -> Dict:
        """
        Terminate the local worker subprocess.

        Args:
            job_type: One of "router", "apbookeeper", "bankbookeeper"

        Returns:
            dict with "status" key
        """
        proc = cls._processes.get(job_type)
        if proc and proc.poll() is None:
            logger.info(f"[LOCAL_WORKER] Terminating worker {job_type} (pid={proc.pid})")
            proc.terminate()
        cls._cleanup_process(job_type)
        return {"status": "scaling_down"}

    @classmethod
    def get_worker_status(cls, job_type: str) -> Dict:
        """
        Get the current status of a local worker subprocess.

        Args:
            job_type: One of "router", "apbookeeper", "bankbookeeper"

        Returns:
            dict with "status" key
        """
        config = WORKER_CONFIG.get(job_type)
        if not config:
            return {"status": "unknown_job_type"}

        proc = cls._processes.get(job_type)
        if proc and proc.poll() is None:
            return {"status": "running", "pid": proc.pid}
        return {"status": "stopped"}

    @classmethod
    def _cleanup_process(cls, job_type: str):
        """Clean up process and log file references."""
        cls._processes.pop(job_type, None)
        log_file = cls._log_files.pop(job_type, None)
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass
