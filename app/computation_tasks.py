"""
Tâches de calcul et transformation parallèles utilisant Celery.
Toutes les tâches sont isolées par utilisateur/société via le registre unifié.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any
from .task_service import celery_app, publish_task_progress
from .unified_registry import get_unified_registry

@celery_app.task(bind=True, name='compute_document_analysis')
def compute_document_analysis(self, user_id: str, document_data: dict, job_id: str):
    """
    Tâche parallèle pour l'analyse de documents (OCR, extraction, etc.)
    Isolée par utilisateur/société.
    """
    registry = get_unified_registry()
    task_id = f"doc_analysis_{job_id}"
    
    try:
        # Enregistrer la tâche dans le registre unifié
        company_info = _get_user_company_from_registry(registry, user_id)
        registry.register_task(
            task_id=task_id,
            task_type="document_analysis",
            user_id=user_id,
            company_id=company_info.get("company_id", "default"),
            priority="normal",
            max_duration=1800  # 30 minutes max
        )
        
        # Publier le début du traitement
        registry.update_task_progress(task_id, "processing", 0, "initializing", self.request.id)
        publish_task_progress(user_id, task_id, "started", 0)
        
        # Simulation de traitement intensif par étapes
        steps = [
            ("preprocessing", 20),
            ("ocr_extraction", 40),
            ("data_validation", 60),
            ("entity_recognition", 80),
            ("final_processing", 100)
        ]
        
        results = {}
        for step_name, progress in steps:
            # Mise à jour progression
            registry.update_task_progress(task_id, "processing", progress, step_name)
            publish_task_progress(user_id, task_id, "processing", progress, {"current_step": step_name})
            
            # Simulation du travail (remplacer par vraie logique)
            time.sleep(2)
            
            # Simulation de résultats par étape
            if step_name == "ocr_extraction":
                results["extracted_text"] = document_data.get("content", "Sample document text")
            elif step_name == "entity_recognition":
                results["entities"] = ["Company Name", "Invoice Number", "Amount"]
        
        # Résultat final
        final_result = {
            "job_id": job_id,
            "analysis_complete": True,
            "extracted_data": {
                "invoice_amount": 1250.00,
                "currency": "EUR",
                "invoice_number": f"INV-{job_id[:8]}",
                "extracted_text": results.get("extracted_text", ""),
                "entities": results.get("entities", [])
            },
            "confidence_score": 0.95,
            "processing_time_ms": int(time.time() * 1000) - int(self.request.get('started_at', time.time()) * 1000)
        }
        
        # Marquer comme terminé
        registry.update_task_progress(task_id, "completed", 100, "completed")
        publish_task_progress(user_id, task_id, "completed", 100, final_result)
        
        return final_result
        
    except Exception as e:
        # Marquer comme échoué
        registry.update_task_progress(task_id, "failed", 0, "error")
        publish_task_progress(user_id, task_id, "failed", 0, {"error": str(e)})
        raise

@celery_app.task(bind=True, name='compute_vector_embeddings')
def compute_vector_embeddings(self, user_id: str, documents: list, collection_name: str):
    """
    Tâche parallèle pour le calcul d'embeddings vectoriels.
    Utilise ChromaDB via le service existant.
    """
    registry = get_unified_registry()
    task_id = f"embeddings_{collection_name}_{uuid.uuid4().hex[:8]}"
    
    try:
        # Enregistrer la tâche
        company_info = _get_user_company_from_registry(registry, user_id)
        registry.register_task(
            task_id=task_id,
            task_type="vector_embeddings",
            user_id=user_id,
            company_id=company_info.get("company_id", "default"),
            priority="normal",
            max_duration=900  # 15 minutes max
        )
        
        # Début du traitement
        registry.update_task_progress(task_id, "processing", 0, "initializing", self.request.id)
        publish_task_progress(user_id, task_id, "started", 0)
        
        # Importer le service ChromaDB
        from .chroma_vector_service import get_chroma_vector_service
        chroma_service = get_chroma_vector_service()
        
        # Traitement par batch pour optimiser
        batch_size = 10
        processed_count = 0
        total_docs = len(documents)
        results = []
        
        for i in range(0, total_docs, batch_size):
            batch = documents[i:i+batch_size]
            
            # Mise à jour progression
            progress = min(90, int((processed_count / total_docs) * 90))
            registry.update_task_progress(task_id, "processing", progress, f"processing_batch_{i//batch_size + 1}")
            publish_task_progress(user_id, task_id, "processing", progress, {
                "batch": i//batch_size + 1,
                "processed": processed_count,
                "total": total_docs
            })
            
            # Traitement du batch (simulation - remplacer par vraie logique)
            try:
                # Ici, appeler la vraie méthode ChromaDB
                batch_results = {
                    "batch_id": i//batch_size + 1,
                    "documents_processed": len(batch),
                    "embeddings_created": len(batch)
                }
                results.append(batch_results)
                processed_count += len(batch)
                
                # Simulation du temps de traitement
                time.sleep(1)
                
            except Exception as batch_error:
                print(f"⚠️ Erreur traitement batch {i//batch_size + 1}: {batch_error}")
                continue
        
        # Finalisation
        registry.update_task_progress(task_id, "processing", 95, "finalizing")
        publish_task_progress(user_id, task_id, "processing", 95, {"step": "finalizing"})
        
        final_result = {
            "collection_name": collection_name,
            "total_documents": total_docs,
            "processed_count": processed_count,
            "batch_results": results,
            "success_rate": (processed_count / total_docs) * 100 if total_docs > 0 else 0
        }
        
        # Marquer comme terminé
        registry.update_task_progress(task_id, "completed", 100, "completed")
        publish_task_progress(user_id, task_id, "completed", 100, final_result)
        
        return final_result
        
    except Exception as e:
        registry.update_task_progress(task_id, "failed", 0, "error")
        publish_task_progress(user_id, task_id, "failed", 0, {"error": str(e)})
        raise

@celery_app.task(bind=True, name='process_llm_conversation')
def process_llm_conversation(
    self, 
    conversation_id: str, 
    user_id: str, 
    company_id: str, 
    prompt: str,
    model: str = "gpt-4",
    temperature: float = 0.7
):
    """
    Tâche parallèle pour traiter une conversation LLM de manière isolée.
    Chaque conversation est isolée par utilisateur/société.
    """
    registry = get_unified_registry()
    task_id = f"llm_{conversation_id}"
    
    try:
        # Enregistrer la tâche avec isolation
        registry.register_task(
            task_id=task_id,
            task_type="llm_conversation",
            user_id=user_id,
            company_id=company_id,
            priority="normal",
            max_duration=300  # 5 minutes max
        )
        
        # Marquer comme en cours
        registry.update_task_progress(
            task_id=task_id,
            status="processing",
            progress_percent=10,
            current_step="initializing",
            worker_id=self.request.id
        )
        
        # Publier l'événement de début
        publish_task_progress(user_id, conversation_id, "started", 10)
        
        # Configuration du contexte isolé pour cette société/utilisateur
        conversation_context = {
            "user_id": user_id,
            "company_id": company_id,
            "conversation_id": conversation_id,
            "isolation_namespace": f"{user_id}_{company_id}"
        }
        
        # Mise à jour progression
        registry.update_task_progress(task_id, "processing", 30, "calling_llm")
        publish_task_progress(user_id, conversation_id, "calling_llm", 30)
        
        # Simulation d'appel LLM (remplacer par vraie implémentation OpenAI)
        time.sleep(3)  # Simulation latence API
        
        # Simulation de réponse LLM
        llm_response = f"Réponse simulée pour la conversation {conversation_id} de l'utilisateur {user_id} de la société {company_id}. Prompt: {prompt[:100]}..."
        
        # Mise à jour progression
        registry.update_task_progress(task_id, "processing", 80, "processing_response")
        publish_task_progress(user_id, conversation_id, "processing_response", 80)
        
        # Sauvegarde de la conversation (isolée par société)
        conversation_data = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "company_id": company_id,
            "prompt": prompt,
            "response": llm_response,
            "model": model,
            "temperature": temperature,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tokens_used": len(prompt.split()) + len(llm_response.split())  # Simulation
        }
        
        # Stocker dans Redis avec namespace isolé
        try:
            from .redis_client import get_redis
            import json
            
            conv_key = f"conversation:{company_id}:{user_id}:{conversation_id}"
            redis_client = get_redis()
            redis_client.hset(conv_key, mapping={
                "data": json.dumps(conversation_data),
                "created_at": str(time.time())
            })
            redis_client.expire(conv_key, 24 * 3600)  # TTL 24h
        except Exception as e:
            print(f"⚠️ Erreur sauvegarde conversation: {e}")
        
        # Marquer comme terminé
        registry.update_task_progress(task_id, "completed", 100, "completed")
        
        # Publier le résultat final
        publish_task_progress(user_id, conversation_id, "completed", 100, {
            "response": llm_response,
            "tokens_used": conversation_data["tokens_used"]
        })
        
        return {
            "success": True,
            "conversation_id": conversation_id,
            "response": llm_response,
            "tokens_used": conversation_data["tokens_used"]
        }
        
    except Exception as e:
        # Marquer comme échoué
        registry.update_task_progress(task_id, "failed", 0, "error")
        publish_task_progress(user_id, conversation_id, "failed", 0, {"error": str(e)})
        raise

# Fonction utilitaire pour récupérer les infos société d'un utilisateur
def _get_user_company_from_registry(registry, user_id: str) -> dict:
    """Récupère les informations société d'un utilisateur depuis le registre."""
    try:
        user_registry = registry.get_user_registry(user_id)
        if user_registry:
            return {
                "company_id": user_registry.get("companies", {}).get("current_company_id", "default"),
                "authorized_companies": user_registry.get("companies", {}).get("authorized_companies_ids", [])
            }
    except Exception as e:
        print(f"⚠️ Erreur récupération infos société pour {user_id}: {e}")
    
    return {"company_id": "default", "authorized_companies": []}

