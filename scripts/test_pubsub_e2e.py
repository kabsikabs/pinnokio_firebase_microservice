#!/usr/bin/env python3
"""
Test E2E — Pipeline PubSub Cache/Metrics/Notifications
========================================================

Simule les signaux Redis PubSub envoyés par les workers (Router, APbookeeper,
Bankbookeeper, EXbookeeper) et vérifie que le backend traite correctement:

1. Mise à jour du cache BUSINESS (routing, invoices, bank, expenses)
2. Mise à jour du cache billing_history
3. Calcul des metrics (MetricsCalculator)
4. Publication contextuelle WebSocket (page-aware)
5. Notifications et messenger
6. Cycle complet Router→completed→APbookeeper→created→completed

Usage:
    # Mode interactif (choix du scénario)
    python scripts/test_pubsub_e2e.py

    # Mode full (tous les scénarios)
    python scripts/test_pubsub_e2e.py --all

    # Scénario spécifique
    python scripts/test_pubsub_e2e.py --scenario router_lifecycle
    python scripts/test_pubsub_e2e.py --scenario ap_lifecycle
    python scripts/test_pubsub_e2e.py --scenario bank_lifecycle
    python scripts/test_pubsub_e2e.py --scenario cross_domain
    python scripts/test_pubsub_e2e.py --scenario expenses_format
    python scripts/test_pubsub_e2e.py --scenario notifications
    python scripts/test_pubsub_e2e.py --scenario page_context

Prérequis:
    - Redis doit tourner (localhost:6379)
    - Le backend firebase_microservice doit être démarré
    - Un utilisateur doit être connecté via WebSocket (pour tester la publication WSS)
"""

import redis
import json
import time
import sys
import argparse
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "password": None,
    "db": 0,
    "decode_responses": True,
}

# Remplacer par des valeurs réelles si connecté
TEST_UID = "7hQs0jluP5YUWcREqdi22NRFnU32"
TEST_COMPANY_ID = "AAAAgaDzK_I"
TEST_MANDATE_PATH = f"mandates/{TEST_COMPANY_ID}"

# Délai entre les signaux (laisser le temps au backend de traiter)
SIGNAL_DELAY = 1.5
VERIFICATION_DELAY = 0.5

# Status mapping (reproduit StatusNormalizer)
STATUS_TO_CATEGORY = {
    "to_process": "to_process", "error": "to_process", "stopped": "to_process", "skipped": "to_process",
    "in_process": "in_process", "on_process": "in_process", "running": "in_process",
    "in_queue": "in_process", "stopping": "in_process",
    "pending": "pending", "pending_approval": "pending",
    "completed": "processed", "routed": "processed", "matched": "processed", "done": "processed",
}

# Department → Domain mapping (reproduit _extract_department_to_domain)
DEPARTMENT_TO_DOMAIN = {
    "Router": "routing", "router": "routing",
    "APbookeeper": "invoices", "Apbookeeper": "invoices", "apbookeeper": "invoices",
    "Bankbookeeper": "bank", "banker": "bank", "Banker": "bank",
    "EXbookeeper": "expenses", "exbookeeper": "expenses",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Colors:
    RESET = "\033[0m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


def ok(msg: str):
    print(f"  {Colors.GREEN}✅ {msg}{Colors.RESET}")


def fail(msg: str):
    print(f"  {Colors.RED}❌ {msg}{Colors.RESET}")


def warn(msg: str):
    print(f"  {Colors.YELLOW}⚠️  {msg}{Colors.RESET}")


def info(msg: str):
    print(f"  {Colors.CYAN}ℹ️  {msg}{Colors.RESET}")


def section(title: str):
    print(f"\n{Colors.BOLD}{'━' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}  {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{'━' * 70}{Colors.RESET}")


def subsection(title: str):
    print(f"\n  {Colors.CYAN}── {title} ──{Colors.RESET}")


def gen_job_id(prefix: str = "klk") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def gen_file_id() -> str:
    return f"1{uuid.uuid4().hex[:24]}"


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.details: List[str] = []

    def check(self, condition: bool, pass_msg: str, fail_msg: str) -> bool:
        if condition:
            ok(pass_msg)
            self.passed += 1
        else:
            fail(fail_msg)
            self.failed += 1
            self.details.append(fail_msg)
        return condition

    def summary(self):
        section("RÉSUMÉ")
        total = self.passed + self.failed
        print(f"  Tests: {self.passed}/{total} passés", end="")
        if self.warnings:
            print(f", {self.warnings} warnings", end="")
        print()
        if self.failed:
            print(f"\n  {Colors.RED}Échecs:{Colors.RESET}")
            for d in self.details:
                print(f"    - {d}")
        print()
        return self.failed == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Redis helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_redis_client() -> redis.Redis:
    return redis.Redis(**REDIS_CONFIG)


def build_business_key(uid: str, company_id: str, domain: str) -> str:
    return f"business:{uid}:{company_id}:{domain}"


def set_user_context(client: redis.Redis, uid: str, page: str, company_id: str):
    """Simule le contexte utilisateur (page active + company sélectionnée)."""
    page_key = f"session:context:{uid}:page"
    company_key = f"user_selected_company:{uid}"
    client.set(page_key, json.dumps(page))
    client.set(company_key, company_id)
    info(f"User context set: page={page}, company={company_id}")


def get_business_cache(client: redis.Redis, uid: str, company_id: str, domain: str) -> Optional[dict]:
    """Lit et unwrap le cache business pour un domaine."""
    key = build_business_key(uid, company_id, domain)
    raw = client.get(key)
    if not raw:
        return None
    data = json.loads(raw)
    # Unwrap UCM format
    if isinstance(data, dict) and "cache_version" in data and "data" in data:
        return data["data"]
    return data


def get_billing_history(client: redis.Redis, uid: str, company_id: str) -> Optional[dict]:
    """Lit le cache billing_history."""
    return get_business_cache(client, uid, company_id, "billing_history")


def count_items_in_cache(cache_data: dict) -> Dict[str, int]:
    """Compte les items par catégorie dans un cache domaine."""
    counts = {}
    for cat in ("to_process", "in_process", "pending", "processed"):
        items = cache_data.get(cat, [])
        counts[cat] = len(items) if isinstance(items, list) else 0
    counts["total"] = sum(counts.values())
    return counts


def find_item_in_cache(cache_data: dict, job_id: str) -> Tuple[Optional[str], Optional[dict]]:
    """Cherche un item par job_id dans toutes les catégories. Retourne (catégorie, item)."""
    for cat in ("to_process", "in_process", "pending", "processed"):
        items = cache_data.get(cat, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("job_id") == job_id or item.get("id") == job_id:
                return cat, item
    return None, None


def publish_signal(client: redis.Redis, uid: str, channel_suffix: str, data: dict) -> int:
    """Publie un signal Redis PubSub."""
    channel = f"user:{uid}/{channel_suffix}"
    result = client.publish(channel, json.dumps(data))
    info(f"Published to {channel} → {result} subscriber(s)")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Signal Builders (reproduisent exactement les payloads des workers)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_task_manager_signal(
    msg_type: str,       # "task_manager_created" | "task_manager_update"
    job_id: str,
    department: str,     # "Router" | "APbookeeper" | "Bankbookeeper" | "EXbookeeper"
    status: str,         # "to_process" | "on_process" | "completed" | ...
    company_id: str = TEST_COMPANY_ID,
    file_name: str = "",
    file_id: str = "",
    extra_data: dict = None,
    department_data: dict = None,
    billing: dict = None,
) -> dict:
    """Construit un signal task_manager tel qu'envoyé par les workers g_cred.py."""
    now = datetime.utcnow().isoformat()
    signal = {
        "type": msg_type,
        "job_id": job_id,
        "department": department,
        "status": status,
        "collection_id": company_id,
        "company_id": company_id,
        "mandate_path": f"mandates/{company_id}",
        "collection_path": f"mandates/{company_id}/task_manager",
        "timestamp": now,
        "data": {
            "status": status,
            "department": department,
            "file_name": file_name or f"test_file_{uuid.uuid4().hex[:8]}.pdf",
            "file_id": file_id or gen_file_id(),
            "last_event_time": now,
            "started_at": now,
        },
    }
    if extra_data:
        signal["data"].update(extra_data)
    if department_data:
        signal["department_data"] = department_data
        signal["data"]["department_data"] = department_data
    if billing:
        signal["billing"] = billing
        signal["data"]["billing"] = billing
    return signal


def build_notification_signal(
    job_id: str,
    status: str,
    department: str = "Router",
    file_name: str = "",
) -> dict:
    """Construit un signal notification tel qu'envoyé par les workers."""
    return {
        "type": "notification_update",
        "job_id": job_id,
        "collection_path": f"clients/{TEST_UID}/notifications",
        "update_data": {
            "docId": f"notif_{int(time.time())}",
            "message": f"Job {department} {status}",
            "status": status,
            "functionName": department,
            "file_name": file_name,
            "timestamp": time.time(),
        },
        "status": status,
        "timestamp": time.time(),
    }


def build_messenger_signal(
    job_id: str,
    action_type: str = "approval_required",
    department: str = "Router",
) -> dict:
    """Construit un signal direct_message_notif."""
    return {
        "type": "direct_message",
        "message_id": f"msg_{int(time.time())}",
        "recipient_id": TEST_UID,
        "sender_id": "system",
        "collection_path": f"clients/{TEST_UID}/direct_message_notif",
        "data": {
            "action_type": action_type,
            "job_id": job_id,
            "priority": "high",
            "message": f"Please review: {department} job",
            "timestamp": time.time(),
        },
        "action_type": action_type,
        "job_id": job_id,
        "priority": "high",
        "timestamp": time.time(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 1: Router Lifecycle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_router_lifecycle(client: redis.Redis, results: TestResults):
    """
    Simule le cycle de vie complet d'un job Router:
    running → on_process → routed → completed
    Vérifie le cache routing à chaque étape.
    """
    section("SCENARIO 1: Router Lifecycle")

    job_id = gen_file_id()  # Le Router utilise le file_id comme job_id
    file_name = "2026-03-09_test-supplier_company_hosting_abc123.pdf"

    # Préparer le contexte: user sur la page routing
    set_user_context(client, TEST_UID, "routing", TEST_COMPANY_ID)

    # Snapshot avant
    cache_before = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    counts_before = count_items_in_cache(cache_before) if cache_before else {"total": 0}
    info(f"Cache routing avant: {counts_before}")

    # ── Step 1: running ──
    subsection("Step 1: Router → running")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id,
        department="router",
        status="running",
        file_name=file_name,
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    if cache:
        cat, item = find_item_in_cache(cache, job_id)
        results.check(
            cat == "in_process",
            f"running → in_process (found in '{cat}')",
            f"running devrait être dans in_process, trouvé dans '{cat}'"
        )
    else:
        results.check(False, "", "Cache routing vide après running")

    # ── Step 2: on_process ──
    subsection("Step 2: Router → on_process")
    signal["data"]["status"] = "on_process"
    signal["status"] = "on_process"
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    cat, item = find_item_in_cache(cache, job_id) if cache else (None, None)
    results.check(
        cat == "in_process",
        f"on_process → in_process (found in '{cat}')",
        f"on_process devrait être dans in_process, trouvé dans '{cat}'"
    )

    # ── Step 3: routed ──
    subsection("Step 3: Router → routed")
    signal["data"]["status"] = "routed"
    signal["status"] = "routed"
    signal["department_data"] = {
        "Router": {"selected_service": "APbookeeper", "confidence": 0.95}
    }
    signal["data"]["department_data"] = signal["department_data"]
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    cat, item = find_item_in_cache(cache, job_id) if cache else (None, None)
    results.check(
        cat == "processed",
        f"routed → processed (found in '{cat}')",
        f"routed devrait être dans processed, trouvé dans '{cat}'"
    )
    if item:
        results.check(
            item.get("routed_to") == "APbookeeper",
            f"routed_to='APbookeeper' aplati depuis department_data",
            f"routed_to manquant ou incorrect: {item.get('routed_to')}"
        )

    # ── Step 4: completed ──
    subsection("Step 4: Router → completed")
    signal["data"]["status"] = "completed"
    signal["status"] = "completed"
    signal["department"] = "Router"
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    cat, item = find_item_in_cache(cache, job_id) if cache else (None, None)
    results.check(
        cat == "processed",
        f"completed → processed (found in '{cat}')",
        f"completed devrait être dans processed, trouvé dans '{cat}'"
    )

    # Vérifier billing_history
    subsection("Vérification billing_history")
    bh = get_billing_history(client, TEST_UID, TEST_COMPANY_ID)
    if bh:
        items = bh.get("items", [])
        bh_item = next((i for i in items if i.get("jobId") == job_id or i.get("id") == job_id), None)
        results.check(
            bh_item is not None,
            f"Item trouvé dans billing_history ({len(items)} items total)",
            f"Item {job_id} NON trouvé dans billing_history ({len(items)} items)"
        )
        if bh_item:
            results.check(
                bh_item.get("status") in ("completed", "processed"),
                f"billing_history status={bh_item.get('status')}",
                f"billing_history status inattendu: {bh_item.get('status')}"
            )
    else:
        warn("billing_history cache vide (normal si premier test)")
        results.warnings += 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 2: APbookeeper Lifecycle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_ap_lifecycle(client: redis.Redis, results: TestResults):
    """
    Simule le cycle de vie d'un job APbookeeper:
    created(to_process) → on_process → completed
    Avec department_data enrichi (supplier, amount, etc.)
    """
    section("SCENARIO 2: APbookeeper Lifecycle")

    klk_job_id = gen_job_id("klk")
    file_name = "2026-03-09_test-supplier_invoice_12345.pdf"

    set_user_context(client, TEST_UID, "invoices", TEST_COMPANY_ID)

    # ── Step 1: created (to_process) ──
    subsection("Step 1: APbookeeper → created (to_process)")
    signal = build_task_manager_signal(
        msg_type="task_manager_created",
        job_id=klk_job_id,
        department="APbookeeper",
        status="to_process",
        file_name=file_name,
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    if cache:
        cat, item = find_item_in_cache(cache, klk_job_id)
        results.check(
            cat == "to_process",
            f"created → to_process (found in '{cat}')",
            f"created devrait être dans to_process, trouvé dans '{cat}'"
        )
    else:
        results.check(False, "", "Cache invoices vide après created")

    # ── Step 2: on_process ──
    subsection("Step 2: APbookeeper → on_process")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=klk_job_id,
        department="APbookeeper",
        status="on_process",
        file_name=file_name,
        extra_data={"current_step": "Extracting data", "current_step_technical": "ocr_extraction"},
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    cat, item = find_item_in_cache(cache, klk_job_id) if cache else (None, None)
    results.check(
        cat == "in_process",
        f"on_process → in_process (found in '{cat}')",
        f"on_process devrait être dans in_process, trouvé dans '{cat}'"
    )
    if item:
        results.check(
            item.get("current_step") == "Extracting data",
            f"current_step='Extracting data' propagé",
            f"current_step manquant: {item.get('current_step')}"
        )

    # ── Step 3: update with department_data (no status change) ──
    subsection("Step 3: APbookeeper → update (department_data, pas de status)")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=klk_job_id,
        department="APbookeeper",
        status="",  # Pas de status dans ce signal
        file_name=file_name,
        department_data={
            "APbookeeper": {
                "supplier_name": "Test Supplier SA",
                "invoice_ref": "INV-2026-001",
                "invoice_date": "2026-03-01",
                "amount_vat_excluded": 1500.00,
                "amount_vat_included": 1615.50,
                "amount_vat": 115.50,
                "currency": "CHF",
            }
        },
    )
    # Remove empty status
    signal.pop("status", None)
    signal["data"].pop("status", None)
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    cat, item = find_item_in_cache(cache, klk_job_id) if cache else (None, None)
    results.check(
        cat == "in_process",
        f"Sans status → reste in_process (found in '{cat}')",
        f"Sans status devrait rester in_process, trouvé dans '{cat}'"
    )
    if item:
        results.check(
            item.get("supplier_name") == "Test Supplier SA",
            f"supplier_name='Test Supplier SA' aplati depuis department_data",
            f"supplier_name manquant: {item.get('supplier_name')}"
        )
        results.check(
            item.get("amount_vat_excluded") == 1500.00,
            f"amount_vat_excluded=1500.00 aplati",
            f"amount_vat_excluded incorrect: {item.get('amount_vat_excluded')}"
        )

    # ── Step 4: completed with billing ──
    subsection("Step 4: APbookeeper → completed (with billing)")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=klk_job_id,
        department="APbookeeper",
        status="completed",
        file_name=file_name,
        billing={
            "total_tokens": 15000,
            "total_sales_price": 0.45,
            "currency": "CHF",
            "billed": True,
        },
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    cat, item = find_item_in_cache(cache, klk_job_id) if cache else (None, None)
    results.check(
        cat == "processed",
        f"completed → processed (found in '{cat}')",
        f"completed devrait être dans processed, trouvé dans '{cat}'"
    )
    # Vérifier que les champs department_data sont conservés après le merge
    if item:
        results.check(
            item.get("supplier_name") == "Test Supplier SA",
            "supplier_name conservé après merge completed",
            f"supplier_name perdu après merge: {item.get('supplier_name')}"
        )

    # Vérifier billing_history
    subsection("Vérification billing_history")
    bh = get_billing_history(client, TEST_UID, TEST_COMPANY_ID)
    if bh:
        items = bh.get("items", [])
        bh_item = next((i for i in items if i.get("jobId") == klk_job_id or i.get("id") == klk_job_id), None)
        if bh_item:
            results.check(
                bh_item.get("totalTokens") == 15000,
                f"billing_history totalTokens=15000",
                f"billing_history totalTokens incorrect: {bh_item.get('totalTokens')}"
            )
            results.check(
                bh_item.get("cost") == 0.45,
                f"billing_history cost=0.45",
                f"billing_history cost incorrect: {bh_item.get('cost')}"
            )
        else:
            warn("Item non trouvé dans billing_history")
            results.warnings += 1
    else:
        warn("billing_history cache vide")
        results.warnings += 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 3: Bankbookeeper Lifecycle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_bank_lifecycle(client: redis.Redis, results: TestResults):
    """
    Simule le cycle de vie d'un job Bankbookeeper:
    to_process → on_process → matched (completed)
    """
    section("SCENARIO 3: Bankbookeeper Lifecycle")

    tx_id = "577"
    job_id = f"company_{TEST_COMPANY_ID}_{tx_id}"

    set_user_context(client, TEST_UID, "banking", TEST_COMPANY_ID)

    # ── Step 1: on_process ──
    subsection("Step 1: Bankbookeeper → on_process")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id,
        department="Bankbookeeper",
        status="on_process",
        extra_data={"description": "Payment ABC Corp", "amount": -1250.00, "date": "2026-03-05"},
        department_data={
            "Bankbookeeper": {
                "transaction_id": tx_id,
                "step_id": "matching",
                "step_label": "Recherche correspondance",
            }
        },
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "bank")
    if cache:
        cat, item = find_item_in_cache(cache, job_id)
        results.check(
            cat == "in_process",
            f"on_process → in_process (found in '{cat}')",
            f"on_process devrait être dans in_process, trouvé dans '{cat}'"
        )
    else:
        results.check(False, "", "Cache bank vide après on_process")

    # ── Step 2: matched (= processed) ──
    subsection("Step 2: Bankbookeeper → matched")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id,
        department="Bankbookeeper",
        status="matched",
        department_data={
            "Bankbookeeper": {
                "transaction_id": tx_id,
                "step_id": "done",
                "step_label": "Réconcilié",
                "reconciliation_details": {"matched_invoice": "INV-001", "confidence": 0.98},
            }
        },
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "bank")
    cat, item = find_item_in_cache(cache, job_id) if cache else (None, None)
    results.check(
        cat == "processed",
        f"matched → processed (found in '{cat}')",
        f"matched devrait être dans processed, trouvé dans '{cat}'"
    )
    if item:
        results.check(
            item.get("step_label") == "Réconcilié",
            f"step_label='Réconcilié' aplati depuis department_data",
            f"step_label manquant: {item.get('step_label')}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 4: Cross-domain Router→APbookeeper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_cross_domain(client: redis.Redis, results: TestResults):
    """
    Simule le flux complet Router→APbookeeper:
    1. Router: running → routed → completed
    2. APbookeeper: created(to_process) → on_process → completed

    Vérifie que le cross-domain ADD crée l'item dans invoices
    quand le Router complete.
    """
    section("SCENARIO 4: Cross-Domain Router → APbookeeper")

    router_job_id = gen_file_id()
    ap_job_id = gen_job_id("klk")
    file_name = "2026-03-09_cross-domain-test_invoice.pdf"

    # ── Phase 1: Router ──
    set_user_context(client, TEST_UID, "routing", TEST_COMPANY_ID)

    subsection("Phase 1a: Router → on_process")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=router_job_id,
        department="router",
        status="on_process",
        file_name=file_name,
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    subsection("Phase 1b: Router → completed (triggers cross-domain)")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=router_job_id,
        department="Router",
        status="completed",
        file_name=file_name,
        department_data={
            "Router": {"selected_service": "APbookeeper", "confidence": 0.92}
        },
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY * 2)  # Extra delay for cross-domain processing

    # Vérifier routing cache: completed
    cache_routing = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    if cache_routing:
        cat, _ = find_item_in_cache(cache_routing, router_job_id)
        results.check(
            cat == "processed",
            f"Router completed → processed dans routing cache",
            f"Router devrait être dans processed, trouvé dans '{cat}'"
        )

    # Note: Le cross-domain ADD nécessite que le doc Firebase existe réellement.
    # En mode test pur Redis, on ne peut pas vérifier le cross-domain ADD automatique.
    # On simule plutôt le signal APbookeeper created qui arrive juste après.
    info("Cross-domain ADD nécessite Firebase (non testable en Redis pur)")
    info("On simule le signal APbookeeper created qui en résulte")

    # ── Phase 2: APbookeeper (le job routé) ──
    subsection("Phase 2a: APbookeeper → created (to_process)")
    signal = build_task_manager_signal(
        msg_type="task_manager_created",
        job_id=ap_job_id,
        department="APbookeeper",
        status="to_process",
        file_name=file_name,
        extra_data={"file_id": router_job_id, "klk_job_id": ap_job_id},
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    # Switch user to invoices page
    set_user_context(client, TEST_UID, "invoices", TEST_COMPANY_ID)

    cache_inv = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    if cache_inv:
        cat, item = find_item_in_cache(cache_inv, ap_job_id)
        results.check(
            cat == "to_process",
            f"APbookeeper created → to_process dans invoices cache",
            f"APbookeeper devrait être dans to_process, trouvé dans '{cat}'"
        )
    else:
        results.check(False, "", "Cache invoices vide après APbookeeper created")

    subsection("Phase 2b: APbookeeper → on_process")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=ap_job_id,
        department="APbookeeper",
        status="on_process",
        file_name=file_name,
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    subsection("Phase 2c: APbookeeper → completed")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=ap_job_id,
        department="APbookeeper",
        status="completed",
        file_name=file_name,
        billing={"total_tokens": 8500, "total_sales_price": 0.25, "currency": "CHF", "billed": True},
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache_inv = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    if cache_inv:
        cat, item = find_item_in_cache(cache_inv, ap_job_id)
        results.check(
            cat == "processed",
            f"APbookeeper completed → processed dans invoices cache",
            f"APbookeeper devrait être dans processed, trouvé dans '{cat}'"
        )
        counts = count_items_in_cache(cache_inv)
        info(f"Cache invoices final: {counts}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 5: Expenses Cache Format (fix validation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_expenses_format(client: redis.Redis, results: TestResults):
    """
    Vérifie que le cache expenses est en format dict catégorisé
    (pas en format liste plate legacy qui causait le crash MetricsCalculator).
    """
    section("SCENARIO 5: Expenses Cache Format Validation")

    set_user_context(client, TEST_UID, "expenses", TEST_COMPANY_ID)

    job_id = gen_job_id("klk_ex")

    # ── Envoyer un signal EXbookeeper ──
    subsection("EXbookeeper → on_process")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id,
        department="EXbookeeper",
        status="on_process",
        extra_data={
            "supplier": "Expense Supplier",
            "amount": 89.50,
            "currency": "CHF",
            "category": "travel",
        },
        department_data={
            "EXbookeeper": {
                "supplier": "Expense Supplier",
                "amount": 89.50,
                "currency": "CHF",
                "category": "travel",
            }
        },
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    # Lire le cache raw (sans unwrap) pour vérifier le format
    key = build_business_key(TEST_UID, TEST_COMPANY_ID, "expenses")
    raw = client.get(key)

    if raw:
        data = json.loads(raw)

        # Vérifier que ce n'est PAS une liste plate
        # Le bug était: unified_cache_manager écrivait {"data": [...], "cache_version": "3.0"}
        is_wrapped = isinstance(data, dict) and "cache_version" in data and "data" in data
        inner = data.get("data", data) if is_wrapped else data

        results.check(
            not isinstance(inner, list),
            f"Cache expenses n'est PAS une liste plate (type={type(inner).__name__})",
            f"Cache expenses est une LISTE PLATE — bug MetricsCalculator toujours présent!"
        )

        if isinstance(inner, dict):
            has_categories = any(k in inner for k in ("to_process", "in_process", "pending", "processed"))
            results.check(
                has_categories,
                "Cache expenses a des clés de catégorie (to_process, in_process, ...)",
                f"Cache expenses est un dict mais sans catégories: {list(inner.keys())[:5]}"
            )

            # Vérifier que l'item est dans la bonne catégorie
            cat, item = find_item_in_cache(inner, job_id)
            results.check(
                cat == "in_process",
                f"EXbookeeper on_process → in_process (found in '{cat}')",
                f"EXbookeeper devrait être dans in_process, trouvé dans '{cat}'"
            )

            # Vérifier que data.get("to_process") ne crash pas
            try:
                inner.get("to_process", [])
                ok("data.get('to_process') fonctionne (pas de AttributeError)")
                results.passed += 1
            except AttributeError:
                fail("data.get('to_process') CRASH — format liste plate!")
                results.failed += 1
    else:
        warn("Cache expenses vide (pas de données pre-existantes)")
        results.warnings += 1

    # ── Completed ──
    subsection("EXbookeeper → completed")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id,
        department="EXbookeeper",
        status="completed",
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "expenses")
    if cache:
        cat, item = find_item_in_cache(cache, job_id)
        results.check(
            cat == "processed",
            f"EXbookeeper completed → processed",
            f"EXbookeeper devrait être dans processed, trouvé dans '{cat}'"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 6: Notifications & Messenger
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_notifications(client: redis.Redis, results: TestResults):
    """
    Teste les canaux notification et direct_message_notif.
    Vérifie que le backend les reçoit (par le nombre de subscribers).
    """
    section("SCENARIO 6: Notifications & Messenger")

    job_id = gen_job_id("klk_notif")

    # ── Notification ──
    subsection("Notification signal")
    notif = build_notification_signal(
        job_id=job_id,
        status="completed",
        department="Router",
        file_name="notif_test.pdf",
    )
    subs = publish_signal(client, TEST_UID, "notifications", notif)
    results.check(
        subs > 0,
        f"Notification reçue par {subs} subscriber(s)",
        f"Notification non reçue (0 subscribers sur user:{TEST_UID}/notifications)"
    )

    time.sleep(SIGNAL_DELAY)

    # ── Messenger (direct_message_notif) ──
    subsection("Messenger signal (direct_message_notif)")
    messenger = build_messenger_signal(
        job_id=job_id,
        action_type="approval_required",
        department="Router",
    )
    subs = publish_signal(client, TEST_UID, "direct_message_notif", messenger)
    results.check(
        subs > 0,
        f"Messenger reçu par {subs} subscriber(s)",
        f"Messenger non reçu (0 subscribers sur user:{TEST_UID}/direct_message_notif)"
    )

    time.sleep(SIGNAL_DELAY)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 7: Page Context — Dashboard vs Domain Page
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_page_context(client: redis.Redis, results: TestResults):
    """
    Teste que le backend réagit différemment selon la page active:
    - Sur dashboard → publie metrics + billing_item_update
    - Sur routing → publie routing.task_manager_update
    - Sur invoices → publie invoices.task_manager_update
    - Sur aucune page → cache seulement (pas de WSS)

    On vérifie le cache (toujours mis à jour) et les subscribers (WSS).
    """
    section("SCENARIO 7: Page Context — Comportement par page")

    # ── Test 1: User sur dashboard ──
    subsection("Test 1: Signal Router pendant user sur dashboard")
    set_user_context(client, TEST_UID, "dashboard", TEST_COMPANY_ID)
    time.sleep(VERIFICATION_DELAY)

    job_id_1 = gen_file_id()
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id_1,
        department="Router",
        status="completed",
        file_name="dashboard_test.pdf",
    )
    subs = publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    if cache:
        cat, _ = find_item_in_cache(cache, job_id_1)
        results.check(
            cat == "processed",
            f"Cache routing mis à jour même depuis dashboard (found in '{cat}')",
            f"Cache routing NON mis à jour depuis dashboard (found in '{cat}')"
        )
    info("Dashboard: le backend publie metrics_update + billing_item_update (vérifier logs)")

    # ── Test 2: User sur routing ──
    subsection("Test 2: Signal Router pendant user sur routing")
    set_user_context(client, TEST_UID, "routing", TEST_COMPANY_ID)
    time.sleep(VERIFICATION_DELAY)

    job_id_2 = gen_file_id()
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id_2,
        department="Router",
        status="on_process",
        file_name="routing_page_test.pdf",
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    if cache:
        cat, _ = find_item_in_cache(cache, job_id_2)
        results.check(
            cat == "in_process",
            f"Cache routing mis à jour depuis page routing",
            f"Cache routing NON mis à jour depuis page routing (found in '{cat}')"
        )
    info("Routing page: le backend publie routing.task_manager_update (vérifier logs)")

    # ── Test 3: User sur invoices reçoit signal APbookeeper ──
    subsection("Test 3: Signal APbookeeper pendant user sur invoices")
    set_user_context(client, TEST_UID, "invoices", TEST_COMPANY_ID)
    time.sleep(VERIFICATION_DELAY)

    job_id_3 = gen_job_id("klk_ctx")
    signal = build_task_manager_signal(
        msg_type="task_manager_created",
        job_id=job_id_3,
        department="APbookeeper",
        status="to_process",
        file_name="context_test.pdf",
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    if cache:
        cat, _ = find_item_in_cache(cache, job_id_3)
        results.check(
            cat == "to_process",
            f"Cache invoices mis à jour (found in '{cat}')",
            f"Cache invoices NON mis à jour (found in '{cat}')"
        )
    info("Invoices page: le backend publie invoices.task_manager_update (vérifier logs)")

    # ── Test 4: User sur banking reçoit signal Router (cross-page: cache only) ──
    subsection("Test 4: Signal Router pendant user sur banking (cross-page)")
    set_user_context(client, TEST_UID, "banking", TEST_COMPANY_ID)
    time.sleep(VERIFICATION_DELAY)

    job_id_4 = gen_file_id()
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=job_id_4,
        department="Router",
        status="running",
        file_name="cross_page_test.pdf",
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    if cache:
        cat, _ = find_item_in_cache(cache, job_id_4)
        results.check(
            cat == "in_process",
            f"Cache routing mis à jour même en cross-page (found in '{cat}')",
            f"Cache routing NON mis à jour en cross-page (found in '{cat}')"
        )
    info("Cross-page: le cache est TOUJOURS mis à jour, mais PAS de WSS publish (vérifier logs)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 8: Metrics Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_metrics_validation(client: redis.Redis, results: TestResults):
    """
    Envoie des signaux depuis dashboard et vérifie que les metrics
    sont calculables depuis les caches (pas de crash MetricsCalculator).
    """
    section("SCENARIO 8: Metrics Validation (anti-regression)")

    set_user_context(client, TEST_UID, "dashboard", TEST_COMPANY_ID)

    # Envoyer un signal pour chaque domaine pour déclencher le calcul de metrics
    domains_to_test = [
        ("Router", "routing", "completed"),
        ("APbookeeper", "invoices", "completed"),
        ("Bankbookeeper", "bank", "matched"),
        ("EXbookeeper", "expenses", "completed"),
    ]

    for dept, domain, status in domains_to_test:
        subsection(f"Metrics: {dept} → {status} (dashboard)")
        job_id = gen_job_id(f"klk_metrics_{domain}")
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=job_id,
            department=dept,
            status=status,
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        time.sleep(SIGNAL_DELAY)

    # Vérifier chaque cache domaine
    info("Vérification des caches après tous les signaux...")
    for dept, domain, status in domains_to_test:
        cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, domain)
        if cache:
            # Le point crucial: le cache ne doit PAS être une liste
            results.check(
                isinstance(cache, dict),
                f"Cache {domain} est un dict (MetricsCalculator safe)",
                f"Cache {domain} est {type(cache).__name__} — MetricsCalculator va CRASH!"
            )
            counts = count_items_in_cache(cache)
            info(f"  {domain}: {counts}")
        else:
            warn(f"Cache {domain} vide (pas de données pre-existantes)")
            results.warnings += 1

    info("Si le backend n'a PAS loggé 'list object has no attribute get', les metrics sont OK")
    info("Vérifier dans les logs: '[CONTEXTUAL_PUBLISHER] metrics_published'")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 9: Live Demo (vrais items existants)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scenario_live_demo(client: redis.Redis, results: TestResults):
    """
    Utilise de VRAIS items existants dans les caches pour simuler des
    mouvements visibles côté frontend.

    1. Prend un item to_process du routing → le fait passer en in_process → completed
    2. Prend un item to_process des invoices → le fait passer en in_process → completed
    3. Met l'user sur dashboard pour voir les metrics bouger

    ⚠️  Modifie les caches Redis (pas Firebase) — les items reviendront
    à leur état original au prochain refresh de page.
    """
    section("SCENARIO 9: Live Demo (vrais items, mouvement visible UI)")

    # ── Étape 0: Fixer le user_selected_company (souvent None) ──
    subsection("Étape 0: Fix user context")
    company_key = f"user_selected_company:{TEST_UID}"
    current_company = client.get(company_key)
    if not current_company or current_company == "None":
        client.set(company_key, TEST_COMPANY_ID)
        warn(f"user_selected_company était '{current_company}' → fixé à '{TEST_COMPANY_ID}'")
    else:
        ok(f"user_selected_company déjà set: {current_company}")

    # ── Chercher de vrais items dans les caches ──
    subsection("Recherche d'items existants")

    # Router: prendre le 1er item to_process
    cache_routing = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
    router_item = None
    router_job_id = None
    if cache_routing:
        to_process = cache_routing.get("to_process", [])
        if isinstance(to_process, list) and to_process:
            router_item = to_process[0]
            router_job_id = router_item.get("job_id") or router_item.get("id")
            file_name = router_item.get("file_name", "unknown.pdf")
            ok(f"Router to_process: job_id={router_job_id}  file={file_name[:50]}")
        else:
            warn("Pas d'items to_process dans routing")
    else:
        warn("Cache routing vide")

    # Invoices: prendre le 1er item to_process
    cache_invoices = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
    invoice_item = None
    invoice_job_id = None
    if cache_invoices:
        to_process = cache_invoices.get("to_process", [])
        if isinstance(to_process, list) and to_process:
            invoice_item = to_process[0]
            invoice_job_id = invoice_item.get("job_id") or invoice_item.get("id")
            file_name_inv = invoice_item.get("file_name", "unknown.pdf")
            ok(f"Invoice to_process: job_id={invoice_job_id}  file={file_name_inv[:50]}")
        else:
            warn("Pas d'items to_process dans invoices")
    else:
        warn("Cache invoices vide")

    if not router_job_id and not invoice_job_id:
        fail("Aucun item to_process trouvé — impossible de faire la demo live")
        results.failed += 1
        return

    # ── Demo 1: Router to_process → in_process (user sur routing) ──
    if router_job_id:
        subsection("Demo 1a: Router to_process → in_process")
        set_user_context(client, TEST_UID, "routing", TEST_COMPANY_ID)
        time.sleep(VERIFICATION_DELAY)

        file_name_r = router_item.get("file_name", "test.pdf")
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=router_job_id,
            department="router",
            status="on_process",
            file_name=file_name_r,
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        time.sleep(SIGNAL_DELAY)

        cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
        cat, _ = find_item_in_cache(cache, router_job_id) if cache else (None, None)
        results.check(
            cat == "in_process",
            f"Router {router_job_id[:20]}... déplacé to_process → in_process ✨",
            f"Router devrait être dans in_process, trouvé dans '{cat}'"
        )
        info("→ Regarde la page Routing: l'item devrait passer de 'To Process' à 'In Process'")

        # Pause pour laisser l'utilisateur voir le mouvement
        print(f"\n  {Colors.YELLOW}⏸️  Pause 5s — vérifie le mouvement sur la page Routing...{Colors.RESET}")
        time.sleep(5)

        # ── Demo 1b: Router in_process → completed (routed) ──
        subsection("Demo 1b: Router in_process → completed")
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=router_job_id,
            department="Router",
            status="completed",
            file_name=file_name_r,
            department_data={
                "Router": {"selected_service": "APbookeeper", "confidence": 0.95}
            },
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        time.sleep(SIGNAL_DELAY)

        cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "routing")
        cat, item = find_item_in_cache(cache, router_job_id) if cache else (None, None)
        results.check(
            cat == "processed",
            f"Router {router_job_id[:20]}... déplacé in_process → processed ✨",
            f"Router devrait être dans processed, trouvé dans '{cat}'"
        )
        info("→ Regarde la page Routing: l'item devrait passer dans 'Processed'")

        print(f"\n  {Colors.YELLOW}⏸️  Pause 5s — vérifie le mouvement sur la page Routing...{Colors.RESET}")
        time.sleep(5)

    # ── Demo 2: Invoice to_process → in_process → completed (user sur invoices) ──
    if invoice_job_id:
        subsection("Demo 2a: Invoice to_process → in_process")
        set_user_context(client, TEST_UID, "invoices", TEST_COMPANY_ID)
        time.sleep(VERIFICATION_DELAY)

        file_name_i = invoice_item.get("file_name", "test.pdf")
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=invoice_job_id,
            department="APbookeeper",
            status="on_process",
            file_name=file_name_i,
            extra_data={"current_step": "Extraction données", "current_step_technical": "ocr_extraction"},
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        time.sleep(SIGNAL_DELAY)

        cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
        cat, _ = find_item_in_cache(cache, invoice_job_id) if cache else (None, None)
        results.check(
            cat == "in_process",
            f"Invoice {invoice_job_id[:20]}... déplacé to_process → in_process ✨",
            f"Invoice devrait être dans in_process, trouvé dans '{cat}'"
        )
        info("→ Regarde la page Invoices: l'item devrait passer dans 'In Process'")

        print(f"\n  {Colors.YELLOW}⏸️  Pause 5s — vérifie le mouvement sur la page Invoices...{Colors.RESET}")
        time.sleep(5)

        subsection("Demo 2b: Invoice in_process → completed")
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=invoice_job_id,
            department="APbookeeper",
            status="completed",
            file_name=file_name_i,
            department_data={
                "APbookeeper": {
                    "supplier_name": "Test Supplier SA",
                    "invoice_ref": "DEMO-001",
                    "amount_vat_included": 1200.00,
                    "currency": "CHF",
                }
            },
            billing={"total_tokens": 12000, "total_sales_price": 0.35, "currency": "CHF", "billed": True},
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        time.sleep(SIGNAL_DELAY)

        cache = get_business_cache(client, TEST_UID, TEST_COMPANY_ID, "invoices")
        cat, _ = find_item_in_cache(cache, invoice_job_id) if cache else (None, None)
        results.check(
            cat == "processed",
            f"Invoice {invoice_job_id[:20]}... déplacé in_process → processed ✨",
            f"Invoice devrait être dans processed, trouvé dans '{cat}'"
        )
        info("→ Regarde la page Invoices: l'item devrait passer dans 'Processed'")

        print(f"\n  {Colors.YELLOW}⏸️  Pause 5s — vérifie le mouvement sur la page Invoices...{Colors.RESET}")
        time.sleep(5)

    # ── Demo 3: Metrics sur dashboard ──
    subsection("Demo 3: Vérification metrics sur Dashboard")
    set_user_context(client, TEST_UID, "dashboard", TEST_COMPANY_ID)
    time.sleep(VERIFICATION_DELAY)

    # Envoyer un signal anodin pour trigger le recalcul de metrics
    dummy_job = gen_job_id("klk_metrics_trigger")
    signal = build_task_manager_signal(
        msg_type="task_manager_update",
        job_id=dummy_job,
        department="Router",
        status="completed",
    )
    publish_signal(client, TEST_UID, "task_manager", signal)
    time.sleep(SIGNAL_DELAY)

    info("→ Va sur le Dashboard: les metrics devraient refléter les mouvements")
    info("   (routing: -1 to_process +1 processed, invoices: -1 to_process +1 processed)")

    # ── Cleanup info ──
    subsection("⚠️  Note importante")
    info("Les mouvements sont uniquement dans le cache Redis")
    info("Un refresh de la page reconstruira le cache depuis Firebase")
    info("Les items originaux sont inchangés dans Firebase/task_manager")

    # ── Remettre les items dans to_process (optionnel) ──
    subsection("Restauration des items dans to_process")
    if router_job_id:
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=router_job_id,
            department="router",
            status="to_process",
            file_name=router_item.get("file_name", ""),
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        ok(f"Router {router_job_id[:20]}... restauré → to_process")

    if invoice_job_id:
        signal = build_task_manager_signal(
            msg_type="task_manager_update",
            job_id=invoice_job_id,
            department="APbookeeper",
            status="to_process",
            file_name=invoice_item.get("file_name", ""),
        )
        publish_signal(client, TEST_UID, "task_manager", signal)
        ok(f"Invoice {invoice_job_id[:20]}... restauré → to_process")

    time.sleep(SIGNAL_DELAY)
    info("Items restaurés à leur état initial dans le cache")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCENARIOS = {
    "router_lifecycle": ("Router Lifecycle (running→completed)", scenario_router_lifecycle),
    "ap_lifecycle": ("APbookeeper Lifecycle (created→completed)", scenario_ap_lifecycle),
    "bank_lifecycle": ("Bankbookeeper Lifecycle (on_process→matched)", scenario_bank_lifecycle),
    "cross_domain": ("Cross-Domain Router→APbookeeper", scenario_cross_domain),
    "expenses_format": ("Expenses Cache Format Validation", scenario_expenses_format),
    "notifications": ("Notifications & Messenger", scenario_notifications),
    "page_context": ("Page Context (dashboard vs domain)", scenario_page_context),
    "metrics": ("Metrics Validation (anti-regression)", scenario_metrics_validation),
    "live_demo": ("🔴 Live Demo (vrais items, mouvement visible UI)", scenario_live_demo),
}


def main():
    global TEST_UID, TEST_COMPANY_ID, TEST_MANDATE_PATH, SIGNAL_DELAY

    parser = argparse.ArgumentParser(description="Test E2E Pipeline PubSub Cache/Metrics")
    parser.add_argument("--all", action="store_true", help="Exécuter tous les scénarios")
    parser.add_argument("--scenario", type=str, help="Scénario spécifique")
    parser.add_argument("--uid", type=str, default=TEST_UID, help=f"User ID (default: {TEST_UID})")
    parser.add_argument("--company", type=str, default=TEST_COMPANY_ID, help=f"Company ID (default: {TEST_COMPANY_ID})")
    parser.add_argument("--delay", type=float, default=SIGNAL_DELAY, help=f"Délai entre signaux (default: {SIGNAL_DELAY}s)")
    parser.add_argument("--no-confirm", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    TEST_UID = args.uid
    TEST_COMPANY_ID = args.company
    TEST_MANDATE_PATH = f"mandates/{TEST_COMPANY_ID}"
    SIGNAL_DELAY = args.delay

    print(f"\n{Colors.BOLD}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}  TEST E2E — Pipeline PubSub Cache/Metrics/Notifications{Colors.RESET}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.RESET}")
    print(f"  UID:        {TEST_UID}")
    print(f"  Company:    {TEST_COMPANY_ID}")
    print(f"  Delay:      {SIGNAL_DELAY}s")

    # Connexion Redis
    try:
        client = get_redis_client()
        client.ping()
        ok("Redis connecté")
    except redis.ConnectionError as e:
        fail(f"Redis non disponible: {e}")
        sys.exit(1)

    # Vérifier subscribers (pubsub_numsub returns list of [channel, count] pairs)
    channel = f"user:{TEST_UID}/task_manager"
    numsub_raw = client.pubsub_numsub(channel)
    if isinstance(numsub_raw, list) and len(numsub_raw) >= 2:
        sub_count = numsub_raw[1] if isinstance(numsub_raw[0], (str, bytes)) else 0
    elif isinstance(numsub_raw, dict):
        sub_count = numsub_raw.get(channel, 0)
    else:
        sub_count = 0
    if sub_count > 0:
        ok(f"{sub_count} subscriber(s) sur {channel}")
    else:
        warn(f"0 subscribers sur {channel} — le backend écoute-t-il?")
        warn("Les signaux seront publiés mais sans traitement backend")

    # Choix du scénario
    if args.scenario:
        scenarios_to_run = [args.scenario]
    elif args.all:
        scenarios_to_run = list(SCENARIOS.keys())
    else:
        print(f"\n  {Colors.BOLD}Scénarios disponibles:{Colors.RESET}")
        for i, (key, (desc, _)) in enumerate(SCENARIOS.items(), 1):
            print(f"    {i}. [{key}] {desc}")
        print(f"    A. Tous les scénarios")

        if not args.no_confirm:
            try:
                choice = input(f"\n  Choix (1-{len(SCENARIOS)}/A): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n  Annulé.")
                sys.exit(0)

            if choice.upper() == "A":
                scenarios_to_run = list(SCENARIOS.keys())
            elif choice.isdigit() and 1 <= int(choice) <= len(SCENARIOS):
                scenarios_to_run = [list(SCENARIOS.keys())[int(choice) - 1]]
            else:
                print("  Choix invalide.")
                sys.exit(1)
        else:
            scenarios_to_run = list(SCENARIOS.keys())

    # Exécution
    results = TestResults()
    for scenario_key in scenarios_to_run:
        desc, func = SCENARIOS[scenario_key]
        try:
            func(client, results)
        except Exception as e:
            fail(f"Scenario '{scenario_key}' crashed: {e}")
            results.failed += 1
            import traceback
            traceback.print_exc()

    # Résumé
    success = results.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
