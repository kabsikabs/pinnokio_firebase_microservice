#!/usr/bin/env python3
"""
Exemple du format complet envoyé au LPT Router
avec les nouvelles informations de traçabilité
"""

import json
from datetime import datetime, timezone

def create_example_router_payload():
    """Crée un exemple de payload Router avec traçabilité complète."""

    print("=== FORMAT COMPLET ENVOYÉ AU LPT ROUTER ===\n")

    # Informations de base (récupérées du contexte utilisateur)
    context = {
        "company_name": "ACME Corporation",
        "client_uuid": "client_123456789",
        "mandate_path": "clients/user_123/bo_clients/client_123/mandates/mandate_456",
        "dms_system": "google_drive",
        "communication_mode": "webhook",
        "log_communication_mode": "firebase"
    }

    # Informations de traçabilité (nouvelles)
    traceability_info = {
        "thread_key": "thread_abc123def456",  # ⭐ Clé du thread pour callback
        "thread_name": "Router_file_xyz789",  # ⭐ Nom descriptif du thread
        "execution_id": "exec_xyz789uvw012",  # ⭐ ID d'exécution unique
        "execution_plan": "NOW",  # ⭐ Mode d'exécution (NOW, ON_DEMAND, SCHEDULED)
        "initiated_at": datetime.now(timezone.utc).isoformat(),  # ⭐ Timestamp d'initiation
        "source": "pinnokio_brain"  # ⭐ Source de l'appel
    }

    # Payload complet qui sera envoyé au LPT Router
    payload = {
        # === INFORMATIONS DE BASE ===
        "collection_name": "acme_corporation",  # Company ID
        "user_id": "user_123",  # Firebase User ID
        "client_uuid": context['client_uuid'],  # Client UUID
        "mandates_path": context['mandate_path'],  # Chemin Firebase du mandat

        # === DONNÉES DE LA TÂCHE ===
        "jobs_data": [
            {
                "file_name": "document_file_xyz789",  # Nom du fichier
                "drive_file_id": "file_xyz789",  # ID Drive du fichier
                "instructions": "Router vers le dossier Factures Fournisseurs",  # Instructions
                "status": 'to_route',  # Statut initial
                "approval_required": False,  # Nécessite approbation
                "automated_workflow": True  # Workflow automatisé
            }
        ],

        # === CONFIGURATION ===
        "settings": [
            {"communication_mode": context['communication_mode']},  # Mode de communication
            {"log_communication_mode": context['log_communication_mode']},  # Mode de logging
            {"dms_system": context['dms_system']}  # Système DMS
        ],

        # === TRAÇABILITÉ COMPLÈTE POUR CALLBACK ===
        "traceability": traceability_info,  # ⭐ Section traçabilité complète

        # === IDENTIFICATION ===
        "pub_sub_id": "router_file_xyz789_abc123def",  # ID unique pour cette exécution
        "start_instructions": None  # Instructions de démarrage
    }

    print("📨 PAYLOAD ENVOYÉ AU LPT ROUTER :\n")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    print("\n=== ANALYSE DES CHAMPS DE TRAÇABILITÉ ===")
    print(f"\n🔑 Thread Key: {payload['traceability']['thread_key']}")
    print(f"   → Utilisé pour envoyer le callback au bon thread")
    print(f"\n📝 Thread Name: {payload['traceability']['thread_name']}")
    print(f"   → Nom descriptif pour identifier le thread")
    print(f"\n🆔 Execution ID: {payload['traceability']['execution_id']}")
    print(f"   → ID unique d'exécution pour traçabilité complète")
    print(f"\n🎯 Execution Plan: {payload['traceability']['execution_plan']}")
    print(f"   → Mode d'exécution (NOW = immédiat, ON_DEMAND = manuel, etc.)")
    print(f"\n⏰ Initiated At: {payload['traceability']['initiated_at']}")
    print(f"   → Timestamp d'initiation pour debug et audit")
    print(f"\n🔗 Source: {payload['traceability']['source']}")
    print(f"   → Indique que l'appel vient du brain Pinnokio")

    print("\n=== URL DE DESTINATION ===")
    print(f"🌐 LOCAL: http://127.0.0.1:8080/event-trigger")
    print(f"🌐 PROD:  http://klk-load-balancer-http-https-435479360.us-east-1.elb.amazonaws.com/event-trigger")

    print("\n=== FONCTIONNEMENT DU CALLBACK ===")
    print("📡 Le LPT Router termine et envoie un callback avec :")
    print(f"   - thread_key: {payload['traceability']['thread_key']}")
    print(f"   - execution_id: {payload['traceability']['execution_id']}")
    print(f"   - status: 'completed' / 'error'")
    print(f"   - results: données du routage")
    print("\n🔄 Le brain Pinnokio reçoit le callback et :")
    print("   1. Met à jour la checklist (UPDATE_STEP)")
    print("   2. Continue le workflow")
    print("   3. Termine avec TERMINATE_TASK")

    return payload

def create_example_apbookkeeper_payload():
    """Crée un exemple de payload APBookkeeper avec traçabilité."""

    print("\n\n=== FORMAT COMPLET ENVOYÉ AU LPT APBOOKKEEPER ===\n")

    # Informations de base
    context = {
        "company_name": "ACME Corporation",
        "client_uuid": "client_123456789",
        "mandate_path": "clients/user_123/bo_clients/client_123/mandates/mandate_456",
        "dms_system": "google_drive",
        "communication_mode": "webhook",
        "log_communication_mode": "firebase"
    }

    # Informations de traçabilité
    traceability_info = {
        "thread_key": "thread_abc123def456",
        "thread_name": "APBookkeeper_batch_abc123def",
        "execution_id": "exec_xyz789uvw012",
        "execution_plan": "NOW",
        "initiated_at": datetime.now(timezone.utc).isoformat(),
        "source": "pinnokio_brain"
    }

    payload = {
        # Informations de base
        "collection_name": "acme_corporation",
        "user_id": "user_123",
        "client_uuid": context['client_uuid'],
        "mandates_path": context['mandate_path'],
        "batch_id": "batch_abc123def456",

        # Données de la tâche (jobs)
        "jobs_data": [
            {
                "file_name": "document_facture_001.pdf",
                "job_id": "file_abc123",
                "instructions": "Saisir facture avec vérification TVA",
                "status": "to_process",
                "approval_required": False,
                "approval_contact_creation": False
            },
            {
                "file_name": "document_facture_002.pdf",
                "job_id": "file_def456",
                "instructions": "Facture urgente, prioriser",
                "status": "to_process",
                "approval_required": True,
                "approval_contact_creation": False
            }
        ],

        # Instructions générales
        "start_instructions": "Vérifier tous les montants HT/TTC avant saisie",

        # Configuration
        "settings": [
            {"communication_mode": context['communication_mode']},
            {"log_communication_mode": context['log_communication_mode']},
            {"dms_system": context['dms_system']}
        ],

        # Traçabilité complète
        "traceability": traceability_info,
        "pub_sub_id": "batch_abc123def456"
    }

    print("📨 PAYLOAD ENVOYÉ AU LPT APBOOKKEEPER :\n")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    print("\n=== DIFFÉRENCES AVEC ROUTER ===")
    print("• batch_id au lieu de jobs_data simple")
    print("• start_instructions pour toutes les factures")
    print("• approval_required par facture")
    print("• pub_sub_id = batch_id")

    return payload

if __name__ == "__main__":
    # Exemple Router
    router_payload = create_example_router_payload()

    # Exemple APBookkeeper
    apbookkeeper_payload = create_example_apbookkeeper_payload()

    print("
=== RÉSUMÉ ==="    print("✅ Payload inclut TOUTES les informations de traçabilité")
    print("✅ Thread_key pour routing des callbacks")
    print("✅ Execution_id pour traçabilité complète")
    print("✅ Execution_plan pour contexte du mode")
    print("✅ Timestamp d'initiation pour audit")
    print("✅ Source identification pour debug")
    print("\n🎯 Le LPT peut maintenant retracer complètement l'origine de chaque appel !")
