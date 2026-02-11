"""
ECS Manager - Auto-scaling ECS Workers (Start/Stop on demand)
==============================================================

Manages ECS Fargate service scaling for external workers:
- Auto-start workers when jobs arrive and desiredCount == 0
- Scale down workers after idle timeout
- Check worker status via describe_services

Architecture:
    Job arrives → job_actions_handler.py
      → Step 1.5: register_batch() (active_jobs Firebase)
      → Step 1.6: ECSManager.ensure_worker_running(job_type)
            → describe_services() → runningCount > 0?
                YES → worker already up, continue HTTP dispatch
                NO  → update_service(desiredCount=1), worker polls active_jobs on start

Worker idle 15 min → _graceful_shutdown() → update_service(desiredCount=0) → exit

Author: Auto-scaling Agent
Created: 2026-02-11
"""

import json
import logging
import os
from typing import Dict, Optional

import boto3

logger = logging.getLogger("ecs_manager")

# Lazy-initialized ECS client (module-level singleton)
_ecs_client = None


class ECSManager:
    """Manages ECS service scaling for workers."""

    # Config mapping job_type → ECS service name
    # Default values match actual AWS ECS service names in pinnokio_cluster
    SERVICE_CONFIG = {
        "router": {
            "service": os.getenv("ECS_SERVICE_ROUTER", "klk_router_service"),
            "container": "klk_router",
        },
        "apbookeeper": {
            "service": os.getenv("ECS_SERVICE_APBOOKEEPER", "klk_apbookeeper_service"),
            "container": "klk_apbookeeper",
        },
        "bankbookeeper": {
            "service": os.getenv("ECS_SERVICE_BANKER", "klk_task_bank_service-afec4qu1"),
            "container": "klk_banker",
        },
    }
    CLUSTER = os.getenv("ECS_CLUSTER_NAME", "pinnokio_cluster")
    REGION = os.getenv("ECS_REGION", "us-east-1")

    @classmethod
    def _get_ecs_client(cls):
        """Lazy-init boto3 ECS client with credentials from Google Secret Manager."""
        global _ecs_client
        if _ecs_client is not None:
            return _ecs_client

        try:
            from .tools.g_cred import get_aws_credentials_from_gsm

            creds = get_aws_credentials_from_gsm()
            if creds:
                _ecs_client = boto3.client(
                    "ecs",
                    region_name=cls.REGION,
                    aws_access_key_id=creds.get("aws_access_key_id"),
                    aws_secret_access_key=creds.get("aws_secret_access_key"),
                )
            else:
                # Fallback: use default boto3 credential chain (env vars, IAM role, etc.)
                _ecs_client = boto3.client("ecs", region_name=cls.REGION)

            logger.info("[ECS_MANAGER] ECS client initialized")
            return _ecs_client

        except Exception as e:
            logger.error(f"[ECS_MANAGER] Failed to initialize ECS client: {e}")
            raise

    @classmethod
    def ensure_worker_running(cls, job_type: str) -> Dict:
        """
        Ensure the ECS worker service for job_type is running.

        - If runningCount > 0 → return {"status": "already_running"}
        - If desiredCount == 0 → update_service(desiredCount=1) → return {"status": "starting"}
        - If desiredCount > 0 but runningCount == 0 → return {"status": "provisioning"}

        Args:
            job_type: One of "router", "apbookeeper", "bankbookeeper"

        Returns:
            dict with "status" key
        """
        config = cls.SERVICE_CONFIG.get(job_type)
        if not config:
            logger.warning(f"[ECS_MANAGER] Unknown job_type: {job_type}")
            return {"status": "unknown_job_type"}

        service_name = config["service"]

        try:
            client = cls._get_ecs_client()
            response = client.describe_services(
                cluster=cls.CLUSTER,
                services=[service_name],
            )

            services = response.get("services", [])
            if not services:
                logger.error(f"[ECS_MANAGER] Service {service_name} not found in cluster {cls.CLUSTER}")
                return {"status": "service_not_found"}

            svc = services[0]
            desired = svc.get("desiredCount", 0)
            running = svc.get("runningCount", 0)
            pending = svc.get("pendingCount", 0)

            logger.info(
                f"[ECS_MANAGER] Service {service_name}: "
                f"desired={desired} running={running} pending={pending}"
            )

            if running > 0:
                return {"status": "already_running", "running": running}

            if desired == 0:
                # Scale up: set desiredCount to 1
                client.update_service(
                    cluster=cls.CLUSTER,
                    service=service_name,
                    desiredCount=1,
                )
                logger.info(f"[ECS_MANAGER] Scaled UP service {service_name} to desiredCount=1")
                return {"status": "starting"}

            # desired > 0 but running == 0: ECS is provisioning
            return {"status": "provisioning", "pending": pending}

        except Exception as e:
            logger.error(f"[ECS_MANAGER] ensure_worker_running failed for {job_type}: {e}")
            raise

    @classmethod
    def scale_down(cls, job_type: str) -> Dict:
        """
        Scale down the ECS service to desiredCount=0.

        Args:
            job_type: One of "router", "apbookeeper", "bankbookeeper"

        Returns:
            dict with "status" key
        """
        config = cls.SERVICE_CONFIG.get(job_type)
        if not config:
            return {"status": "unknown_job_type"}

        service_name = config["service"]

        try:
            client = cls._get_ecs_client()
            client.update_service(
                cluster=cls.CLUSTER,
                service=service_name,
                desiredCount=0,
            )
            logger.info(f"[ECS_MANAGER] Scaled DOWN service {service_name} to desiredCount=0")
            return {"status": "scaling_down"}

        except Exception as e:
            logger.error(f"[ECS_MANAGER] scale_down failed for {job_type}: {e}")
            raise

    @classmethod
    def get_worker_status(cls, job_type: str) -> Dict:
        """
        Get the current status of an ECS worker service.

        Args:
            job_type: One of "router", "apbookeeper", "bankbookeeper"

        Returns:
            dict with "desired", "running", "pending" counts
        """
        config = cls.SERVICE_CONFIG.get(job_type)
        if not config:
            return {"status": "unknown_job_type"}

        service_name = config["service"]

        try:
            client = cls._get_ecs_client()
            response = client.describe_services(
                cluster=cls.CLUSTER,
                services=[service_name],
            )

            services = response.get("services", [])
            if not services:
                return {"status": "service_not_found"}

            svc = services[0]
            return {
                "service": service_name,
                "desired": svc.get("desiredCount", 0),
                "running": svc.get("runningCount", 0),
                "pending": svc.get("pendingCount", 0),
                "status": svc.get("status", "unknown"),
            }

        except Exception as e:
            logger.error(f"[ECS_MANAGER] get_worker_status failed for {job_type}: {e}")
            return {"status": "error", "error": str(e)}
