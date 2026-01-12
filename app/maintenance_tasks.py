"""
Tâches de maintenance périodiques pour le système de registre unifié et listeners.
Exécutées par Celery Beat pour maintenir la santé du système.
"""

import logging
from datetime import datetime, timedelta, timezone
from .task_service import celery_app
from .registry import get_unified_registry, get_registry_listeners
from .firebase_client import get_firestore

logger = logging.getLogger("maintenance_tasks")

@celery_app.task(name='app.maintenance_tasks.cleanup_expired_registries')
def cleanup_expired_registries():
    """
    Nettoie les entrées expirées dans les registres.
    Exécutée toutes les 5 minutes.
    """
    try:
        registry = get_unified_registry()
        registry.cleanup_expired_entries()
        return {"status": "success", "message": "Cleanup completed"}
    except Exception as e:
        logger.error("cleanup_expired_registries_error error=%s", repr(e))
        return {"status": "error", "error": str(e)}

@celery_app.task(name='app.maintenance_tasks.health_check_services')
def health_check_services():
    """
    Vérifie la santé des services connectés.
    Exécutée toutes les minutes.
    """
    try:
        health_status = {
            "redis": _check_redis_health(),
            "firestore": _check_firestore_health(),
            "chroma": _check_chroma_health()
        }
        
        overall_health = all(status["status"] == "ok" for status in health_status.values())
        
        return {
            "status": "healthy" if overall_health else "degraded",
            "services": health_status
        }
    except Exception as e:
        logger.error("health_check_services_error error=%s", repr(e))
        return {"status": "error", "error": str(e)}

def _check_redis_health() -> dict:
    """Vérifie la santé de Redis."""
    try:
        from .redis_client import get_redis
        r = get_redis()
        r.ping()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _check_firestore_health() -> dict:
    """Vérifie la santé de Firestore."""
    try:
        from .firebase_client import get_firestore
        db = get_firestore()
        # Test simple de lecture
        list(db.collections())
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _check_chroma_health() -> dict:
    """Vérifie la santé de ChromaDB."""
    try:
        from .chroma_vector_service import get_chroma_vector_service
        chroma_service = get_chroma_vector_service()
        # Test de heartbeat
        chroma_service.chroma.heartbeat()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task(name='app.maintenance_tasks.cleanup_expired_listeners')
def cleanup_expired_listeners():
    """
    Nettoie automatiquement les listeners expirés dans le registre centralisé.
    
    Cette tâche parcourt tous les utilisateurs ayant des listeners enregistrés,
    vérifie leur heartbeat et supprime ceux dont le TTL est dépassé.
    
    Exécutée toutes les minutes par Celery Beat.
    
    Returns:
        dict: Statut de l'exécution avec le nombre de listeners nettoyés
    """
    try:
        logger.info("cleanup_expired_listeners_start")
        
        db = get_firestore()
        registry = get_registry_listeners()
        
        # Parcourir tous les utilisateurs dans listeners_active
        users_ref = db.collection("listeners_active")
        users_docs = users_ref.stream()
        
        total_cleaned = 0
        total_users_checked = 0
        users_with_expired = []
        
        for user_doc in users_docs:
            uid = user_doc.id
            total_users_checked += 1
            
            try:
                # Lister les listeners de cet utilisateur (include_expired=True)
                result = registry.list_user_listeners(uid, include_expired=True)
                
                if not result.get("success"):
                    logger.error(
                        "cleanup_list_error uid=%s error=%s", 
                        uid, result.get("error")
                    )
                    continue
                
                # Identifier et nettoyer les listeners expirés
                expired_count = 0
                for listener in result.get("listeners", []):
                    if listener.get("status") in ["expired", "zombie"]:
                        # Nettoyer ce listener
                        unregister_result = registry.unregister_listener(
                            user_id=uid,
                            listener_type=listener.get("listener_type"),
                            space_code=listener.get("space_code"),
                            thread_key=listener.get("thread_key")
                        )
                        
                        if unregister_result.get("success"):
                            total_cleaned += 1
                            expired_count += 1
                            logger.info(
                                "listener_expired_cleanup uid=%s listener_id=%s type=%s status=%s",
                                uid, 
                                listener.get("listener_id"),
                                listener.get("listener_type"),
                                listener.get("status")
                            )
                        else:
                            logger.error(
                                "listener_cleanup_error uid=%s listener_id=%s error=%s",
                                uid,
                                listener.get("listener_id"),
                                unregister_result.get("error")
                            )
                
                if expired_count > 0:
                    users_with_expired.append({
                        "uid": uid,
                        "cleaned_count": expired_count
                    })
                    
            except Exception as e:
                logger.error("cleanup_user_error uid=%s error=%s", uid, repr(e))
                continue
        
        logger.info(
            "cleanup_expired_listeners_complete users_checked=%s total_cleaned=%s users_with_expired=%s",
            total_users_checked,
            total_cleaned,
            len(users_with_expired)
        )
        
        return {
            "status": "success",
            "total_cleaned": total_cleaned,
            "users_checked": total_users_checked,
            "users_with_expired": len(users_with_expired),
            "details": users_with_expired[:10]  # Limiter à 10 pour éviter logs trop longs
        }
        
    except Exception as e:
        logger.error("cleanup_expired_listeners_error error=%s", repr(e), exc_info=True)
        return {
            "status": "error",
            "error": str(e)
        }


@celery_app.task(name="app.maintenance_tasks.finalize_daily_chat_billing")
def finalize_daily_chat_billing(target_date: str | None = None, days_back: int = 7) -> dict:
    """
    Finalise la facturation quotidienne du chat.

    - Source: collection group `token_usage` (docs agrégés) avec billing_kind='chat_daily'
    - Cible: {mandate_path}/billing/topping/expenses/{job_id}
    - Puis: recalcul solde via FirebaseManagement.get_user_balance(mandate_path)

    Notes:
    - Par défaut, traite la veille UTC (pour éviter les écritures en cours sur le jour courant).
    - Si `days_back > 1` et `target_date` est None, fait un rattrapage sur les N derniers jours (hors aujourd'hui).
    - Cette tâche est conçue pour être exécutée via Celery Beat (CRON).
    """
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
        from .firebase_providers import get_firebase_management

        db = get_firestore()
        fbm = get_firebase_management()

        # Dates à traiter
        if target_date:
            target_dates = [target_date]
        else:
            # Rattrapage: veille → veille-(days_back-1)
            n = max(1, int(days_back or 1))
            target_dates = [
                (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(1, n + 1)
            ]

        logger.info("[BILLING_CRON] start target_dates=%s", target_dates)

        total_token_usage_docs = 0
        expenses_written = 0
        skipped_missing_mandate = 0
        skipped_already_billed = 0
        balances_updated = 0

        for td in target_dates:
            # Collection group query sur tous les token_usage (clients/*/token_usage/*)
            query = (
                db.collection_group("token_usage")
                .where(filter=FieldFilter("billing_kind", "==", "chat_daily"))
                .where(filter=FieldFilter("billing_date", "==", td))
            )

            token_usage_docs = list(query.stream())
            total_token_usage_docs += len(token_usage_docs)
            logger.info("[BILLING_CRON] token_usage_docs=%s date=%s", len(token_usage_docs), td)

            mandate_paths: set[str] = set()

            for doc in token_usage_docs:
                data = doc.to_dict() or {}
                mandate_path = data.get("mandate_path")
                job_id = data.get("job_id") or doc.id
                collection_name = data.get("collection_name")
                user_id = data.get("user_id")

                if not mandate_path:
                    skipped_missing_mandate += 1
                    continue

                expense_ref = db.document(f"{mandate_path}/billing/topping/expenses/{job_id}")
                existing = expense_ref.get()
                if existing.exists and (existing.to_dict() or {}).get("billed") is True:
                    skipped_already_billed += 1
                    continue

                # Format "Chat usage DD/MM/YYYY"
                ddmmyyyy = td
                try:
                    ddmmyyyy = datetime.strptime(td, "%Y-%m-%d").strftime("%d/%m/%Y")
                except Exception:
                    ddmmyyyy = td

                total_input = data.get("total_input_tokens", 0) or 0
                total_output = data.get("total_output_tokens", 0) or 0
                total_tokens = data.get("total_tokens", total_input + total_output)

                expense_payload = {
                    "job_id": job_id,
                    "function": "chat",
                    "file_name": f"Chat usage {ddmmyyyy}",
                    "billing_kind": "chat_daily",
                    "billing_date": td,
                    "collection_name": collection_name,
                    "user_id": user_id,
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "total_tokens": total_tokens,
                    "total_buy_price": data.get("total_buy_price", 0.0),
                    "total_sales_price": data.get("total_sales_price", 0.0),
                    "entries_count": data.get("entries_count", 0),
                    "billed": False,
                    "billing_timestamp": datetime.now(timezone.utc).isoformat(),
                }

                expense_ref.set(expense_payload, merge=True)
                expenses_written += 1
                mandate_paths.add(mandate_path)
                
                # ═══════════════════════════════════════════════════════════════
                # Indexation task_manager selon CONTRAT_UNIFIE_BILLING_TASK_MANAGER
                # Indexer lors de la finalisation journalière avec les totaux cumulatifs
                # ═══════════════════════════════════════════════════════════════
                try:
                    # Construire billing depuis les données token_usage agrégées
                    billing_data = {
                        "billed": False,  # Sera mis à True par le wallet
                        "billing_timestamp": datetime.now(timezone.utc).isoformat(),
                        "billing_kind": "chat_daily",
                        "billing_date": td,
                        "token_usage_job_id": job_id,
                        "total_input_tokens": total_input,
                        "total_output_tokens": total_output,
                        "total_tokens": total_tokens,
                        "total_buy_price": data.get("total_buy_price", 0.0),
                        "total_sales_price": data.get("total_sales_price", 0.0),
                        "collection_name": collection_name,
                    }
                    
                    # Utiliser les mêmes valeurs que expense_payload pour cohérence
                    task_file_name = expense_payload["file_name"]  # "Chat usage DD/MM/YYYY"
                    task_department = expense_payload["function"]  # "chat"
                    
                    # Indexation best-effort dans task_manager
                    # Champs écrits au même niveau que billing: timestamp, file_name, department
                    fbm._upsert_task_manager_billing_index(
                        user_id=user_id,
                        task_doc_id=job_id,  # task_doc_id = job_id selon contrat
                        billing_data=billing_data,
                        department=task_department,  # Valeur du champ function
                        file_name=task_file_name,    # Même nom que dans expense_payload
                        mandate_path=mandate_path
                    )
                except Exception as e:
                    # Best-effort: ne pas casser la facturation si l'indexation échoue
                    logger.warning(f"[BILLING_CRON] Erreur non bloquante indexation task_manager: {e}")

            # Mettre à jour le solde une fois par mandate_path
            for mp in mandate_paths:
                try:
                    _ = fbm.get_user_balance(mandate_path=mp)
                    balances_updated += 1
                except Exception as e:
                    logger.error("[BILLING_CRON] balance_update_error mandate_path=%s error=%s", mp, repr(e))

        result = {
            "status": "success",
            "target_dates": target_dates,
            "token_usage_docs": total_token_usage_docs,
            "expenses_written": expenses_written,
            "balances_updated": balances_updated,
            "skipped_missing_mandate": skipped_missing_mandate,
            "skipped_already_billed": skipped_already_billed,
        }

        logger.info("[BILLING_CRON] complete %s", result)
        return result
    except Exception as e:
        logger.error("[BILLING_CRON] fatal error=%s", repr(e), exc_info=True)
        return {"status": "error", "error": str(e)}

