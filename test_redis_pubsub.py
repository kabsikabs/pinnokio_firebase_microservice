"""
Test Redis PubSub - Simulation des Workers
===========================================

Ce script simule les publications Redis PubSub que font les workers (Router, APBookkeeper, Banker)
pour valider que le RedisSubscriber du backend les traite correctement.

TESTS:
  1. task_manager (Router)  - Changement de status d'un job
  2. notifications          - Push notification vers l'utilisateur
  3. direct_message_notif   - Push message direct (messenger)

PREREQUIS:
  - Redis doit etre lance (localhost:6379)
  - Le backend (firebase_microservice) doit etre lance avec RedisSubscriber actif
  - L'utilisateur doit etre connecte au frontend (WebSocket) pour voir les events

USAGE:
  python test_redis_pubsub.py                   # Menu interactif
  python test_redis_pubsub.py --test router     # Test Router (routing)
  python test_redis_pubsub.py --test ap         # Test APBookkeeper (invoices)
  python test_redis_pubsub.py --test bank       # Test Banker (banking)
  python test_redis_pubsub.py --test notif      # Test notifications
  python test_redis_pubsub.py --test message    # Test messages directs
  python test_redis_pubsub.py --test all        # Tous les tests en sequence
"""

import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timezone
import redis.asyncio as redis

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================
# Configuration
# ============================================

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

# Donnees de test (commun)
USER_ID = '7hQs0jluP5YUWcREqdi22NRFnU32'

# ROUTER
ROUTER_JOB_ID = '1MPb2oIchEBWl85dZ9it1vYfzpPGc8aDp'
ROUTER_MANDATE_PATH = 'clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/kXAQYwMgsMrV60jeVcuz/mandates/BIr6edJxlNeKUBZxNhu4'
ROUTER_COLLECTION_ID = 'AAAABzwjXro'

# Alias pour retrocompat
FILE_ID = ROUTER_JOB_ID
MANDATE_PATH = ROUTER_MANDATE_PATH
COLLECTION_ID = ROUTER_COLLECTION_ID

# APBOOKEEPER (Invoices)
AP_JOB_ID = 'klk_2334bbf0-c3e8-4046-b0dc-b2be36096e4c'
AP_MANDATE_PATH = 'clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/LaCWd6ltASD2vgCl8J01/mandates/ZhnLigKULKQOoZhcW9Fp'
AP_COLLECTION_ID = 'AAAAgaDzK_I'

# BANKER (Bank) - valeurs a remplir
BANK_JOB_ID = ''
BANK_MANDATE_PATH = ''
BANK_COLLECTION_ID = ''


async def get_redis_client():
    """Cree et retourne un client Redis async."""
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True
    )
    await r.ping()
    return r


async def publish_and_log(r, channel: str, payload: dict, test_name: str):
    """Publie un message sur un canal Redis et log le resultat."""
    message_json = json.dumps(payload)

    logger.info("=" * 70)
    logger.info(f"TEST: {test_name}")
    logger.info(f"Canal: {channel}")
    logger.info(f"Payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    logger.info("-" * 70)

    subscribers_count = await r.publish(channel, message_json)

    if subscribers_count == 0:
        logger.warning(f"AUCUN abonne n'a recu le message. Le backend (RedisSubscriber) est-il lance ?")
    else:
        logger.info(f"Message publie avec succes ! {subscribers_count} abonne(s) ont recu le message.")

    logger.info("=" * 70)
    return subscribers_count


# ============================================
# TEST 1: Task Manager (Router)
# ============================================

async def test_task_manager_router(r):
    """
    Simule un changement de status d'un job Router dans task_manager.

    SCENARIO: Le worker Router a traite un document et change son status
    de "in_queue" a "pending" (en attente de validation).

    ATTENDU:
    - Si user sur page /routing → WebSocket event routing.task_manager_update
    - Si user sur page /dashboard → WebSocket event dashboard.metrics_update (BUG ACTUEL: pas envoye)
    - Cache BUSINESS mis a jour: business:{uid}:{cid}:routing
    - Metrics du widget Router dans dashboard doivent refleter le changement
    """
    channel = f"user:{USER_ID}/task_manager"

    payload = {
        "type": "task_manager_update",
        "job_id": FILE_ID,
        "mandate_path": MANDATE_PATH,
        "collection_path": f"clients/{USER_ID}/task_manager/{FILE_ID}",
        "collection_id": COLLECTION_ID,
        "company_id": COLLECTION_ID,
        "department": "router",
        "status": "pending",
        "functionName": "routing",
        "data": {
            "job_id": FILE_ID,
            "name": "Facture_Test_Router_001.pdf",
            "status": "pending",
            "department": "router",
            "routed_to": "Fournisseur ABC",
            "created_at": "2026-02-09T10:00:00Z",
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "TASK_MANAGER - Router (status: pending)")


async def test_task_manager_router_status_change(r, new_status: str):
    """
    Simule un changement de status specifique pour le Router.
    Permet de tester differents statuts: in_queue, on_process, pending, processed, error
    """
    channel = f"user:{USER_ID}/task_manager"

    payload = {
        "type": "task_manager_update",
        "job_id": FILE_ID,
        "mandate_path": MANDATE_PATH,
        "collection_path": f"clients/{USER_ID}/task_manager/{FILE_ID}",
        "collection_id": COLLECTION_ID, 
        "company_id": COLLECTION_ID,
        "department": "router",
        "status": new_status,
        "functionName": "routing",
        "data": {
            "job_id": FILE_ID,
            "name": "Facture_Test_Router_001.pdf",
            "status": new_status,
            "department": "router",
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, f"TASK_MANAGER - Router (status: {new_status})")


# ============================================
# TEST 1b: Task Manager (APBookkeeper / Invoices)
# ============================================

async def test_task_manager_ap(r):
    """
    Simule un changement de status d'un job APBookkeeper dans task_manager.

    SCENARIO: Le worker APBookkeeper traite une facture fournisseur.

    ATTENDU:
    - Si user sur page /invoices → WebSocket event invoices.task_manager_update
    - Si user sur page /dashboard → WebSocket event dashboard.metrics_update
    - Cache BUSINESS mis a jour: business:{uid}:{cid}:invoices
    - Le cache invoices utilise le format LISTE PLATE {"items": [...]}
    """
    channel = f"user:{USER_ID}/task_manager"

    payload = {
        "type": "task_manager_update",
        "job_id": AP_JOB_ID,
        "mandate_path": AP_MANDATE_PATH,
        "collection_path": f"clients/{USER_ID}/task_manager/{AP_JOB_ID}",
        "collection_id": AP_COLLECTION_ID,
        "company_id": AP_COLLECTION_ID,
        "department": "APbookeeper",
        "status": "pending",
        "functionName": "APbookeeper",
        "data": {
            "job_id": AP_JOB_ID,
            "id": AP_JOB_ID,
            "name": "Facture_Fournisseur_Test_001.pdf",
            "status": "pending",
            "department": "APbookeeper",
            "supplier": "Fournisseur XYZ",
            "amount": 1250.00,
            "currency": "CHF",
            "step": "validation",
            "created_at": "2026-02-09T14:00:00Z",
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "TASK_MANAGER - APBookkeeper (status: pending)")


async def test_task_manager_ap_status_change(r, new_status: str):
    """
    Simule un changement de status specifique pour APBookkeeper.
    Statuts possibles: in_queue, on_process, pending, processed, error
    """
    channel = f"user:{USER_ID}/task_manager"

    payload = {
        "type": "task_manager_update",
        "job_id": AP_JOB_ID,
        "mandate_path": AP_MANDATE_PATH,
        "collection_path": f"clients/{USER_ID}/task_manager/{AP_JOB_ID}",
        "collection_id": AP_COLLECTION_ID,
        "company_id": AP_COLLECTION_ID,
        "department": "APbookeeper",
        "status": new_status,
        "functionName": "APbookeeper",
        "data": {
            "job_id": AP_JOB_ID,
            "id": AP_JOB_ID,
            "name": "Facture_Fournisseur_Test_001.pdf",
            "status": new_status,
            "department": "APbookeeper",
            "supplier": "Fournisseur XYZ",
            "amount": 1250.00,
            "currency": "CHF",
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, f"TASK_MANAGER - APBookkeeper (status: {new_status})")


# ============================================
# TEST 1c: Task Manager (Banker / Bank)
# ============================================

async def test_task_manager_bank_status_change(r, new_status: str):
    """
    Simule un changement de status specifique pour Banker.
    Statuts possibles: in_queue, on_process, pending, processed, error
    """
    if not BANK_JOB_ID:
        logger.warning("BANK_JOB_ID non configure. Remplissez les constantes BANK_* en haut du fichier.")
        return 0

    channel = f"user:{USER_ID}/task_manager"

    payload = {
        "type": "task_manager_update",
        "job_id": BANK_JOB_ID,
        "mandate_path": BANK_MANDATE_PATH,
        "collection_path": f"clients/{USER_ID}/task_manager/{BANK_JOB_ID}",
        "collection_id": BANK_COLLECTION_ID,
        "company_id": BANK_COLLECTION_ID,
        "department": "Banker",
        "status": new_status,
        "functionName": "Banker",
        "data": {
            "job_id": BANK_JOB_ID,
            "id": BANK_JOB_ID,
            "name": "Transaction_Bank_Test_001",
            "status": new_status,
            "department": "Banker",
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, f"TASK_MANAGER - Banker (status: {new_status})")


# ============================================
# TEST 2: Notifications
# ============================================

async def test_notification_job_created(r):
    """
    Simule une notification de creation de job.

    SCENARIO: Le worker a cree un nouveau job et notifie l'utilisateur.

    ATTENDU:
    - Cache USER mis a jour: user:{uid}:notifications
    - Si user connecte → WebSocket event notification.delta
    - L'icone notification dans le header du frontend doit s'incrementer
    """
    channel = f"user:{USER_ID}/notifications"

    payload = {
        "type": "job_created",
        "job_id": FILE_ID,
        "title": "Nouveau document a traiter",
        "message": "Le document Facture_Test_Router_001.pdf a ete recu et est en cours de traitement.",
        "status": "in_queue",
        "functionName": "routing",
        "department": "router",
        "priority": "normal",
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "NOTIFICATION - Job Created (Router)")


async def test_notification_status_update(r):
    """
    Simule une notification de changement de status.

    SCENARIO: Le worker a termine le traitement d'un document.

    ATTENDU:
    - Cache USER mis a jour
    - Si user connecte → WebSocket event notification.delta
    - Badge notification doit s'incrementer
    """
    channel = f"user:{USER_ID}/notifications"

    payload = {
        "type": "notification_update",
        "job_id": FILE_ID,
        "title": "Document traite avec succes",
        "message": "Le document Facture_Test_Router_001.pdf a ete route vers Comptabilite.",
        "status": "processed",
        "functionName": "routing",
        "department": "router",
        "priority": "normal",
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "NOTIFICATION - Status Update (processed)")


async def test_notification_error(r):
    """
    Simule une notification d'erreur.

    SCENARIO: Le worker a rencontre une erreur.

    ATTENDU:
    - Notification avec priority=high
    - Badge notification doit s'incrementer avec indicateur d'erreur
    """
    channel = f"user:{USER_ID}/notifications"

    payload = {
        "type": "notification_update",
        "job_id": FILE_ID,
        "title": "Erreur de traitement",
        "message": "Impossible de traiter le document: format non reconnu.",
        "status": "error",
        "functionName": "routing",
        "department": "router",
        "priority": "high",
        "read": False,
        "error_details": "FileFormatError: unsupported MIME type",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "NOTIFICATION - Error (high priority)")


# ============================================
# TEST 3: Direct Messages (Messenger)
# ============================================

async def test_direct_message(r):
    """
    Simule un message direct (messenger) vers l'utilisateur.

    SCENARIO: Le worker envoie un message direct a l'utilisateur
    (ex: demande de validation, question sur un document).

    ATTENDU:
    - Cache USER mis a jour: user:{uid}:messages
    - Si user connecte → WebSocket event messenger.delta (priority HIGH)
    - L'icone messenger dans le header du frontend doit s'incrementer
    - Si user NON connecte → le message est cache et sera delivre a la reconnexion
    """
    channel = f"user:{USER_ID}/direct_message_notif"

    payload = {
        "message_id": f"msg_{FILE_ID[:8]}_{int(datetime.now().timestamp())}",
        "action_type": "new_message",
        "priority": "high",
        "sender": "Router Worker",
        "sender_type": "agent",
        "subject": "Validation requise",
        "body": "Le document Facture_Test_Router_001.pdf necessite votre validation. "
                "Le fournisseur detecte est 'ABC Corp' mais la confiance est faible (62%). "
                "Pouvez-vous confirmer ?",
        "job_id": FILE_ID,
        "department": "router",
        "requires_action": True,
        "action_buttons": [
            {"label": "Confirmer", "action": "approve"},
            {"label": "Corriger", "action": "edit"},
            {"label": "Rejeter", "action": "reject"}
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "DIRECT MESSAGE - Validation Required")


async def test_direct_message_info(r):
    """
    Simule un message informatif (pas d'action requise).

    ATTENDU:
    - Meme flux que test_direct_message mais priority=normal
    """
    channel = f"user:{USER_ID}/direct_message_notif"

    payload = {
        "message_id": f"msg_info_{int(datetime.now().timestamp())}",
        "action_type": "new_message",
        "priority": "normal",
        "sender": "Router Worker",
        "sender_type": "agent",
        "subject": "Traitement termine",
        "body": "Le batch de 5 documents a ete traite. 4 succes, 1 erreur.",
        "department": "router",
        "requires_action": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return await publish_and_log(r, channel, payload, "DIRECT MESSAGE - Info (no action)")


# ============================================
# Sequences de Test
# ============================================

async def run_router_tests(r):
    """Sequence complete de tests pour le Router task_manager."""
    print("\n" + "=" * 70)
    print("   SEQUENCE DE TEST: ROUTER TASK_MANAGER")
    print("=" * 70)
    print("""
INSTRUCTIONS:
  1. Assurez-vous que le backend est lance
  2. Ouvrez le frontend dans le navigateur
  3. Connectez-vous avec l'utilisateur de test

OBSERVATIONS A FAIRE:
  - Page /routing : la liste doit se mettre a jour en temps reel
  - Page /dashboard : les metrics du widget Router doivent changer
  - Page /dashboard : l'expense_history doit refleter le job
  - Verifiez les logs du backend pour voir le traitement
""")

    input("Appuyez sur Entree pour lancer le test (status: in_queue)...")
    await test_task_manager_router_status_change(r, "in_queue")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'on_process'...")
    await test_task_manager_router_status_change(r, "on_process")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'pending'...")
    await test_task_manager_router_status_change(r, "pending")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'processed'...")
    await test_task_manager_router_status_change(r, "processed")

    print("\n" + "-" * 70)
    print("RESULTATS ATTENDUS:")
    print("  - Page /routing : le job doit avoir bouge entre les onglets")
    print("    in_queue -> In Queue | on_process -> In Process | pending -> Pending | processed -> Processed")
    print("  - Page /dashboard : metrics widget Router doit refleter les changements")
    print("  - Logs backend : verifier les lignes [REDIS_SUBSCRIBER]")
    print("-" * 70)


async def run_notification_tests(r):
    """Sequence complete de tests pour les notifications."""
    print("\n" + "=" * 70)
    print("   SEQUENCE DE TEST: NOTIFICATIONS")
    print("=" * 70)
    print("""
INSTRUCTIONS:
  1. Gardez le frontend ouvert (n'importe quelle page)
  2. Observez l'icone de notification dans le header

OBSERVATIONS A FAIRE:
  - L'icone notification doit s'incrementer a chaque push
  - Le dropdown des notifications doit montrer les nouveaux items
  - Les notifications 'high priority' doivent etre visuellement distinctes
  - Quand vous fermez/lisez une notification : verifier le comportement backend
""")

    input("Appuyez sur Entree pour envoyer une notification 'job_created'...")
    await test_notification_job_created(r)
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour envoyer une notification 'status_update'...")
    await test_notification_status_update(r)
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour envoyer une notification 'error' (high priority)...")
    await test_notification_error(r)

    print("\n" + "-" * 70)
    print("RESULTATS ATTENDUS:")
    print("  - 3 notifications dans le dropdown")
    print("  - La notification erreur doit etre mise en evidence (high priority)")
    print("  - Si vous fermez une notification, observer les logs backend")
    print("    (le frontend devrait envoyer un event de read/dismiss)")
    print("-" * 70)


async def run_message_tests(r):
    """Sequence complete de tests pour les messages directs."""
    print("\n" + "=" * 70)
    print("   SEQUENCE DE TEST: MESSAGES DIRECTS (MESSENGER)")
    print("=" * 70)
    print("""
INSTRUCTIONS:
  1. Gardez le frontend ouvert (n'importe quelle page)
  2. Observez l'icone messenger dans le header

OBSERVATIONS A FAIRE:
  - L'icone messenger doit s'incrementer
  - Le message avec action_buttons doit presenter les options
  - Le message info (sans action) doit etre simple
  - Verifier le comportement si user se deconnecte puis reconnecte
""")

    input("Appuyez sur Entree pour envoyer un message 'validation requise' (high priority)...")
    await test_direct_message(r)
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour envoyer un message 'info' (normal priority)...")
    await test_direct_message_info(r)

    print("\n" + "-" * 70)
    print("RESULTATS ATTENDUS:")
    print("  - 2 messages dans le messenger")
    print("  - Le premier doit avoir des boutons d'action (Confirmer/Corriger/Rejeter)")
    print("  - Le second est informatif uniquement")
    print("  - Si user offline : les messages doivent etre dans le cache USER")
    print("    et delivres a la reconnexion")
    print("-" * 70)


async def run_ap_tests(r):
    """Sequence complete de tests pour APBookkeeper task_manager."""
    print("\n" + "=" * 70)
    print("   SEQUENCE DE TEST: APBOOKEEPER (INVOICES)")
    print("=" * 70)
    print(f"""
CONFIG:
  Job ID:        {AP_JOB_ID}
  Collection ID: {AP_COLLECTION_ID}
  Mandate Path:  {AP_MANDATE_PATH}

INSTRUCTIONS:
  1. Assurez-vous que le backend est lance
  2. Ouvrez le frontend sur la page /invoices
  3. Verifiez que la company selectionnee correspond a {AP_COLLECTION_ID}

OBSERVATIONS A FAIRE:
  - Page /invoices : l'item doit se deplacer entre les onglets
  - Page /dashboard : les metrics du widget AP doivent changer
  - Verifiez que les champs (name, supplier, amount) sont preserves apres deplacement
  - Verifiez les logs backend pour [REDIS_SUBSCRIBER]
""")

    input("Appuyez sur Entree pour lancer le test (status: in_queue)...")
    await test_task_manager_ap_status_change(r, "in_queue")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'on_process'...")
    await test_task_manager_ap_status_change(r, "on_process")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'pending'...")
    await test_task_manager_ap_status_change(r, "pending")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'completed'...")
    await test_task_manager_ap_status_change(r, "completed")

    print("\n" + "-" * 70)
    print("RESULTATS ATTENDUS:")
    print("  - Page /invoices : le job doit avoir bouge entre les onglets")
    print("    in_queue -> A traiter | on_process -> En cours | pending -> En attente | completed -> Traite")
    print("  - Les champs name/supplier/amount doivent etre preserves a chaque deplacement")
    print("  - Page /dashboard : metrics widget AP doit refleter les changements")
    print("  - Logs backend : domain=invoices, department=APbookeeper")
    print("-" * 70)


async def run_bank_tests(r):
    """Sequence complete de tests pour Banker task_manager."""
    if not BANK_JOB_ID:
        print("\n" + "=" * 70)
        print("   SEQUENCE DE TEST: BANKER (BANK)")
        print("=" * 70)
        print("\n  BANK_JOB_ID non configure !")
        print("  Remplissez les constantes BANK_* en haut du fichier test_redis_pubsub.py")
        print("=" * 70)
        return

    print("\n" + "=" * 70)
    print("   SEQUENCE DE TEST: BANKER (BANK)")
    print("=" * 70)
    print(f"""
CONFIG:
  Job ID:        {BANK_JOB_ID}
  Collection ID: {BANK_COLLECTION_ID}
  Mandate Path:  {BANK_MANDATE_PATH}

INSTRUCTIONS:
  1. Assurez-vous que le backend est lance
  2. Ouvrez le frontend sur la page /banking
  3. Verifiez que la company selectionnee correspond a {BANK_COLLECTION_ID}

OBSERVATIONS A FAIRE:
  - Page /banking : la transaction doit se deplacer entre les onglets
  - Page /dashboard : les metrics du widget Bank doivent changer
  - Verifiez les logs backend pour [REDIS_SUBSCRIBER]
""")

    input("Appuyez sur Entree pour lancer le test (status: in_queue)...")
    await test_task_manager_bank_status_change(r, "in_queue")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'on_process'...")
    await test_task_manager_bank_status_change(r, "on_process")
    await asyncio.sleep(2)

    input("\nAppuyez sur Entree pour changer le status a 'pending'...")
    await test_task_manager_bank_status_change(r, "pending")

    print("\n" + "-" * 70)
    print("RESULTATS ATTENDUS:")
    print("  - Page /banking : la transaction doit avoir bouge entre les onglets")
    print("    in_queue -> A rapprocher | on_process -> En cours | pending -> En attente")
    print("  - Page /dashboard : metrics widget Bank doit refleter les changements")
    print("  - Logs backend : domain=bank, department=Banker")
    print("-" * 70)


async def run_all_tests(r):
    """Lance tous les tests en sequence."""
    await run_router_tests(r)
    print("\n\n")
    await run_ap_tests(r)
    print("\n\n")
    if BANK_JOB_ID:
        await run_bank_tests(r)
        print("\n\n")
    await run_notification_tests(r)
    print("\n\n")
    await run_message_tests(r)

    print("\n" + "=" * 70)
    print("   TOUS LES TESTS TERMINES")
    print("=" * 70)
    print(f"""
RESUME DES VERIFICATIONS:
  1. [ROUTER] Page /routing mise a jour en temps reel ?
  2. [ROUTER] Dashboard metrics widget Router mis a jour ?
  3. [AP]     Page /invoices mise a jour en temps reel ?
  4. [AP]     Dashboard metrics widget AP mis a jour ?
  5. [BANK]   Page /banking mise a jour en temps reel ? {'(non teste - pas configure)' if not BANK_JOB_ID else ''}
  6. [NOTIF]  Notifications recues dans le header ?
  7. [NOTIF]  Comportement a la fermeture d'une notification ?
  8. [MSG]    Messages recus dans le messenger ?
  9. [MSG]    Boutons d'action presents sur le message priority=high ?
 10. [CACHE]  Verifier les cles Redis (redis-cli):
             - business:{{uid}}:{ROUTER_COLLECTION_ID}:routing
             - business:{{uid}}:{AP_COLLECTION_ID}:invoices
             - user:{{uid}}:notifications
             - user:{{uid}}:messages
""")


# ============================================
# Point d'entree
# ============================================

async def main():
    """Point d'entree principal."""
    logger.info(f"Connexion a Redis sur {REDIS_HOST}:{REDIS_PORT}...")

    try:
        r = await get_redis_client()
        logger.info("Connexion Redis etablie.")
    except Exception as e:
        logger.error(f"Impossible de se connecter a Redis: {e}")
        return

    # Parser les arguments
    test_arg = None
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        test_arg = sys.argv[2].lower()

    try:
        if test_arg == "router":
            await run_router_tests(r)
        elif test_arg == "ap":
            await run_ap_tests(r)
        elif test_arg == "bank":
            await run_bank_tests(r)
        elif test_arg == "notif":
            await run_notification_tests(r)
        elif test_arg == "message":
            await run_message_tests(r)
        elif test_arg == "all":
            await run_all_tests(r)
        else:
            # Menu interactif
            print("\n" + "=" * 70)
            print("   TEST REDIS PUBSUB - Menu Principal")
            print("=" * 70)
            print(f"\n  User ID:          {USER_ID}")
            print(f"  Router Job ID:    {ROUTER_JOB_ID}")
            print(f"  Router Coll. ID:  {ROUTER_COLLECTION_ID}")
            print(f"  AP Job ID:        {AP_JOB_ID}")
            print(f"  AP Coll. ID:      {AP_COLLECTION_ID}")
            print(f"  Bank Job ID:      {BANK_JOB_ID or '(non configure)'}")
            print()
            print("  1. Test Router (task_manager) - Changements de status")
            print("  2. Test APBookkeeper (task_manager) - Changements de status")
            print("  3. Test Banker (task_manager) - Changements de status")
            print("  4. Test Notifications - Push notifications")
            print("  5. Test Messages Directs - Messenger")
            print("  6. Tous les tests")
            print("  7. Test unitaire rapide Router (1 publish)")
            print("  8. Test unitaire rapide AP (1 publish)")
            print("  0. Quitter")
            print()

            choice = input("Choix: ").strip()

            if choice == "1":
                await run_router_tests(r)
            elif choice == "2":
                await run_ap_tests(r)
            elif choice == "3":
                await run_bank_tests(r)
            elif choice == "4":
                await run_notification_tests(r)
            elif choice == "5":
                await run_message_tests(r)
            elif choice == "6":
                await run_all_tests(r)
            elif choice == "7":
                await test_task_manager_router(r)
            elif choice == "8":
                await test_task_manager_ap(r)
            elif choice == "0":
                print("Au revoir.")
            else:
                print("Choix invalide.")

    finally:
        await r.aclose()
        logger.info("Connexion Redis fermee.")


if __name__ == "__main__":
    asyncio.run(main())
