"""
Tests d'intégration pour la migration Redis scalabilité.

Ces tests valident que:
1. Les approbations fonctionnent cross-instance (Redis polling)
2. Les sessions peuvent être reconstruites depuis Redis
3. Les streams peuvent être arrêtés cross-instance (Pub/Sub)
4. La déduplication fonctionne cross-instance (Redis SET)
5. Les verrous distribués fonctionnent correctement

Usage:
    python -m pytest tests/test_redis_scalability.py -v

Author: Scalability Team
Created: 2026-01-20
"""

import asyncio
import pytest
import time
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def approval_manager():
    """Fixture pour ApprovalStateManager."""
    from app.llm_service.approval_state_manager import get_approval_state_manager
    manager = get_approval_state_manager()
    
    # Nettoyer avant le test
    yield manager
    
    # Nettoyer après le test
    # (Le TTL Redis fera le ménage automatiquement)


@pytest.fixture
def session_registry():
    """Fixture pour SessionRegistryManager."""
    from app.llm_service.session_registry_manager import get_session_registry_manager
    manager = get_session_registry_manager()
    
    # Nettoyer avant le test
    yield manager
    
    # Nettoyer après le test
    # (Le TTL Redis fera le ménage automatiquement)


@pytest.fixture
def brain_state_manager():
    """Fixture pour BrainStateManager."""
    from app.llm_service.brain_state_manager import get_brain_state_manager
    return get_brain_state_manager()


@pytest.fixture
def processed_messages():
    """Fixture pour ProcessedMessagesManager."""
    from app.llm_service.processed_messages_manager import get_processed_messages_manager
    manager = get_processed_messages_manager()
    
    # Nettoyer avant le test
    yield manager
    
    # Nettoyer après le test
    # (Le TTL Redis fera le ménage automatiquement)


@pytest.fixture
async def stream_registry():
    """Fixture pour StreamRegistryManager (async)."""
    from app.llm_service.stream_registry_manager import get_stream_registry_manager
    manager = get_stream_registry_manager()
    
    yield manager
    
    # Nettoyer
    await manager.stop_listening()


@pytest.fixture
async def distributed_lock_factory():
    """Fixture factory pour DistributedLock."""
    from app.llm_service.distributed_lock import DistributedLock
    
    locks_created = []
    
    def create_lock(resource_name: str, **kwargs):
        lock = DistributedLock(resource_name, **kwargs)
        locks_created.append(lock)
        return lock
    
    yield create_lock
    
    # Nettoyer tous les locks créés
    for lock in locks_created:
        if lock.is_acquired:
            lock.release()


# ═══════════════════════════════════════════════════════════════
# TESTS APPROVAL STATE MANAGER
# ═══════════════════════════════════════════════════════════════

def test_approval_create_and_resolve(approval_manager):
    """Test création et résolution d'approbation."""
    user_id = "test_user_123"
    thread_key = "test_thread_456"
    card_id = "test_card_789"
    
    # Créer approbation
    success = approval_manager.create_pending_approval(
        user_id=user_id,
        thread_key=thread_key,
        card_message_id=card_id,
        card_type="test_approval",
        card_params={"test": "data"}
    )
    assert success, "Création approbation doit réussir"
    
    # Vérifier état pending
    state = approval_manager.get_approval_state(user_id, thread_key, card_id)
    assert state is not None, "État doit exister"
    assert state["status"] == "pending"
    assert state["card_type"] == "test_approval"
    
    # Résoudre approbation
    success = approval_manager.resolve_approval(
        user_id=user_id,
        thread_key=thread_key,
        card_message_id=card_id,
        action="approve",
        user_message="Test OK"
    )
    assert success, "Résolution doit réussir"
    
    # Vérifier état approved
    state = approval_manager.get_approval_state(user_id, thread_key, card_id)
    assert state["status"] == "approved"
    assert state["action"] == "approve"
    assert state["user_message"] == "Test OK"
    assert state["responded_at"] is not None


def test_approval_timeout(approval_manager):
    """Test timeout d'approbation."""
    user_id = "test_user_timeout"
    thread_key = "test_thread_timeout"
    card_id = "test_card_timeout"
    
    # Créer approbation
    approval_manager.create_pending_approval(
        user_id=user_id,
        thread_key=thread_key,
        card_message_id=card_id,
        card_type="test_approval",
        card_params={}
    )
    
    # Marquer timeout
    success = approval_manager.mark_timeout(user_id, thread_key, card_id)
    assert success
    
    # Vérifier état timeout
    state = approval_manager.get_approval_state(user_id, thread_key, card_id)
    assert state["status"] == "timeout"


# ═══════════════════════════════════════════════════════════════
# TESTS SESSION REGISTRY MANAGER
# ═══════════════════════════════════════════════════════════════

def test_session_register_and_exists(session_registry):
    """Test enregistrement et vérification de session."""
    user_id = "test_user_session"
    company_id = "test_company"
    
    # Nettoyer les données résiduelles
    session_registry.unregister(user_id, company_id)
    
    # Vérifier inexistence initiale
    assert not session_registry.exists(user_id, company_id)
    
    # Enregistrer session
    success = session_registry.register(user_id, company_id, "instance_A")
    assert success
    
    # Vérifier existence
    assert session_registry.exists(user_id, company_id)
    
    # Récupérer info
    info = session_registry.get_info(user_id, company_id)
    assert info is not None
    assert info["instance_id"] == "instance_A"
    assert info["session_key"] == f"{user_id}:{company_id}"


def test_session_update_activity(session_registry):
    """Test mise à jour activité."""
    user_id = "test_user_activity"
    company_id = "test_company"
    
    # Enregistrer
    session_registry.register(user_id, company_id)
    
    info1 = session_registry.get_info(user_id, company_id)
    time.sleep(0.1)
    
    # Mettre à jour
    session_registry.update_activity(user_id, company_id)
    
    info2 = session_registry.get_info(user_id, company_id)
    assert info2["last_activity"] > info1["last_activity"]


# ═══════════════════════════════════════════════════════════════
# TESTS BRAIN STATE MANAGER
# ═══════════════════════════════════════════════════════════════

def test_brain_save_and_load(brain_state_manager):
    """Test sauvegarde et chargement état brain."""
    user_id = "test_user_brain"
    company_id = "test_company"
    thread_key = "test_thread_brain"
    
    # Sauvegarder état
    success = brain_state_manager.save_state(
        user_id=user_id,
        company_id=company_id,
        thread_key=thread_key,
        active_plans={"plan1": {"status": "active"}},
        active_lpt_tasks={"task1": {"status": "running"}},
        mode="general_chat"
    )
    assert success
    
    # Charger état
    state = brain_state_manager.load_state(user_id, company_id, thread_key)
    assert state is not None
    assert state["mode"] == "general_chat"
    assert "plan1" in state["active_plans"]
    assert "task1" in state["active_lpt_tasks"]


def test_brain_update_plans(brain_state_manager):
    """Test mise à jour plans."""
    user_id = "test_user_plans"
    company_id = "test_company"
    thread_key = "test_thread_plans"
    
    # Créer état initial
    brain_state_manager.save_state(
        user_id, company_id, thread_key,
        active_plans={"plan1": {}},
        active_lpt_tasks={}
    )
    
    # Mettre à jour plans
    success = brain_state_manager.update_plans(
        user_id, company_id, thread_key,
        {"plan1": {}, "plan2": {}}
    )
    assert success
    
    # Vérifier
    state = brain_state_manager.load_state(user_id, company_id, thread_key)
    assert len(state["active_plans"]) == 2


# ═══════════════════════════════════════════════════════════════
# TESTS PROCESSED MESSAGES MANAGER
# ═══════════════════════════════════════════════════════════════

def test_processed_messages_mark_and_check(processed_messages):
    """Test marquage et vérification messages traités."""
    user_id = "test_user_msg"
    company_id = "test_company"
    thread_key = "test_thread_msg"
    message_id = "msg_123"
    
    # Nettoyer les données résiduelles
    processed_messages.clear_thread(user_id, company_id, thread_key)
    
    # Vérifier non traité
    assert not processed_messages.is_processed(user_id, company_id, thread_key, message_id)
    
    # Marquer traité
    success = processed_messages.mark_processed(user_id, company_id, thread_key, message_id)
    assert success
    
    # Vérifier traité
    assert processed_messages.is_processed(user_id, company_id, thread_key, message_id)


def test_processed_messages_bulk(processed_messages):
    """Test marquage en bulk."""
    user_id = "test_user_bulk"
    company_id = "test_company"
    thread_key = "test_thread_bulk"
    message_ids = {"msg1", "msg2", "msg3"}
    
    # Marquer en bulk
    success = processed_messages.mark_many_processed(
        user_id, company_id, thread_key, message_ids
    )
    assert success
    
    # Vérifier count
    count = processed_messages.count_processed(user_id, company_id, thread_key)
    assert count == 3
    
    # Récupérer tous les IDs
    ids = processed_messages.get_processed_ids(user_id, company_id, thread_key)
    assert ids == message_ids


# ═══════════════════════════════════════════════════════════════
# TESTS STREAM REGISTRY MANAGER
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stream_register_and_check(stream_registry):
    """Test enregistrement et vérification stream."""
    user_id = "test_user_stream"
    company_id = "test_company"
    thread_key = "test_thread_stream"
    
    # Vérifier inexistence
    is_active = await stream_registry.is_stream_active(user_id, company_id, thread_key)
    assert not is_active
    
    # Enregistrer
    success = await stream_registry.register_stream(user_id, company_id, thread_key)
    assert success
    
    # Vérifier existence
    is_active = await stream_registry.is_stream_active(user_id, company_id, thread_key)
    assert is_active
    
    # Désenregistrer
    success = await stream_registry.unregister_stream(user_id, company_id, thread_key)
    assert success
    
    # Vérifier suppression
    is_active = await stream_registry.is_stream_active(user_id, company_id, thread_key)
    assert not is_active


@pytest.mark.asyncio
async def test_stream_pubsub_signal(stream_registry):
    """Test signal Pub/Sub cross-instance."""
    user_id = "test_user_pubsub"
    company_id = "test_company"
    thread_key = "test_thread_pubsub"
    
    # Variable pour tracker si callback appelé
    callback_called = {"value": False}
    
    def stop_callback():
        callback_called["value"] = True
    
    # Enregistrer callback
    stream_registry.register_stop_callback(thread_key, stop_callback)
    
    # Démarrer listener
    await stream_registry.start_listening(user_id)
    
    # Attendre que le listener soit prêt
    await asyncio.sleep(0.5)
    
    # Envoyer signal
    success = await stream_registry.publish_stop_signal(user_id, company_id, thread_key)
    assert success
    
    # Attendre réception
    await asyncio.sleep(0.5)
    
    # Vérifier callback appelé
    assert callback_called["value"], "Callback devrait avoir été appelé"


# ═══════════════════════════════════════════════════════════════
# TESTS DISTRIBUTED LOCK
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_distributed_lock_acquire_release(distributed_lock_factory):
    """Test acquisition et libération verrou."""
    lock = distributed_lock_factory("test_resource_lock")
    
    # Acquérir
    acquired = await lock.acquire()
    assert acquired
    assert lock.is_acquired
    
    # Libérer
    lock.release()
    assert not lock.is_acquired


@pytest.mark.asyncio
async def test_distributed_lock_contention(distributed_lock_factory):
    """Test contention entre deux locks."""
    resource = "test_resource_contention"
    
    lock1 = distributed_lock_factory(resource, timeout=2)
    lock2 = distributed_lock_factory(resource, timeout=2)
    
    # Lock1 acquiert
    acquired1 = await lock1.acquire()
    assert acquired1
    
    # Lock2 ne peut pas acquérir (timeout court)
    acquired2 = await lock2.acquire()
    assert not acquired2
    
    # Lock1 libère
    lock1.release()
    
    # Lock2 peut maintenant acquérir
    acquired2 = await lock2.acquire()
    assert acquired2
    
    lock2.release()


@pytest.mark.asyncio
async def test_distributed_lock_context_manager(distributed_lock_factory):
    """Test context manager async."""
    lock = distributed_lock_factory("test_resource_context")
    
    async with lock:
        assert lock.is_acquired
        # Code protégé ici
    
    assert not lock.is_acquired


# ═══════════════════════════════════════════════════════════════
# TESTS INTÉGRATION COMPLÈTE
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_workflow_simulation():
    """Test workflow complet simulant 2 instances."""
    from app.llm_service.approval_state_manager import get_approval_state_manager
    
    approval_manager = get_approval_state_manager()
    
    user_id = "test_user_workflow"
    thread_key = "test_thread_workflow"
    card_id = "test_card_workflow"
    
    # ═══ INSTANCE A : Créer approbation et attendre (simulation polling) ═══
    approval_manager.create_pending_approval(
        user_id=user_id,
        thread_key=thread_key,
        card_message_id=card_id,
        card_type="workflow_test",
        card_params={"test": "data"},
        timeout=10
    )
    
    # Simuler polling (3 tentatives)
    for i in range(3):
        await asyncio.sleep(0.5)
        state = approval_manager.get_approval_state(user_id, thread_key, card_id)
        if state and state.get("status") != "pending":
            break
        
        # Simuler INSTANCE B qui résout après 1 seconde
        if i == 1:
            approval_manager.resolve_approval(
                user_id=user_id,
                thread_key=thread_key,
                card_message_id=card_id,
                action="approve",
                user_message="Approved by instance B"
            )
    
    # Vérifier résolution
    final_state = approval_manager.get_approval_state(user_id, thread_key, card_id)
    assert final_state["status"] == "approved"
    assert final_state["user_message"] == "Approved by instance B"
    
    print("✅ Test workflow complet réussi!")


if __name__ == "__main__":
    print("Exécution des tests Redis scalabilité...")
    print("Usage: python -m pytest tests/test_redis_scalability.py -v")
