"""
Unit tests for Dashboard Orchestration Handlers

This module tests the dashboard orchestration functionality without
requiring full system setup.

Run with:
    pytest tests/test_dashboard_orchestration.py -v
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timezone


# Mock Redis before importing the module
@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis_mock = MagicMock()
    redis_mock.get.return_value = None
    redis_mock.setex.return_value = True
    return redis_mock


@pytest.fixture
def mock_firestore():
    """Mock Firestore client."""
    db_mock = MagicMock()
    return db_mock


@pytest.fixture
def mock_hub():
    """Mock WebSocket hub."""
    hub_mock = MagicMock()
    hub_mock.broadcast = AsyncMock()
    return hub_mock


@pytest.fixture
def mock_dashboard_handlers():
    """Mock dashboard handlers."""
    handlers_mock = MagicMock()
    handlers_mock.full_data = AsyncMock(return_value={
        "success": True,
        "data": {
            "company": {"id": "test-company", "name": "Test Co"},
            "metrics": {},
            "storage": {},
        }
    })
    return handlers_mock


@pytest.fixture
def setup_mocks(mock_redis, mock_firestore, mock_hub, mock_dashboard_handlers):
    """Setup all mocks for testing."""
    with patch('app.wrappers.dashboard_orchestration_handlers.get_redis', return_value=mock_redis), \
         patch('app.wrappers.dashboard_orchestration_handlers.get_firestore', return_value=mock_firestore), \
         patch('app.wrappers.dashboard_orchestration_handlers.hub', mock_hub), \
         patch('app.wrappers.dashboard_orchestration_handlers.get_dashboard_handlers', return_value=mock_dashboard_handlers):
        yield {
            'redis': mock_redis,
            'firestore': mock_firestore,
            'hub': mock_hub,
            'dashboard_handlers': mock_dashboard_handlers
        }


class TestOrchestrationStateManager:
    """Test the OrchestrationStateManager class."""

    def test_create_orchestration(self, mock_redis):
        """Test creating a new orchestration state."""
        from app.wrappers.dashboard_orchestration_handlers import OrchestrationStateManager

        manager = OrchestrationStateManager(redis_client=mock_redis)

        orchestration_id = manager.create_orchestration(
            uid="test-user",
            session_id="test-session",
            company_id="test-company"
        )

        # Verify orchestration_id is a valid UUID
        assert orchestration_id is not None
        assert len(orchestration_id) == 36  # UUID format

        # Verify Redis was called
        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args[0]

        # Check key format
        assert args[0] == "orchestration:test-user:test-session:state"

        # Check TTL
        assert args[1] == 3600

        # Check state structure
        state = json.loads(args[2])
        assert state["orchestration_id"] == orchestration_id
        assert state["phase"] == "company"
        assert state["selected_company_id"] == "test-company"
        assert state["cancellation_requested"] is False

    def test_get_orchestration(self, mock_redis):
        """Test retrieving orchestration state."""
        from app.wrappers.dashboard_orchestration_handlers import OrchestrationStateManager

        # Setup mock response
        test_state = {
            "orchestration_id": "test-id",
            "phase": "data",
            "selected_company_id": "test-company"
        }
        mock_redis.get.return_value = json.dumps(test_state)

        manager = OrchestrationStateManager(redis_client=mock_redis)
        state = manager.get_orchestration("test-user", "test-session")

        assert state == test_state
        mock_redis.get.assert_called_once_with("orchestration:test-user:test-session:state")

    def test_update_orchestration(self, mock_redis):
        """Test updating orchestration state."""
        from app.wrappers.dashboard_orchestration_handlers import OrchestrationStateManager

        # Setup existing state
        existing_state = {
            "orchestration_id": "test-id",
            "phase": "company",
            "updated_at": "2026-01-18T10:00:00Z"
        }
        mock_redis.get.return_value = json.dumps(existing_state)

        manager = OrchestrationStateManager(redis_client=mock_redis)

        result = manager.update_orchestration(
            "test-user",
            "test-session",
            {"phase": "data"}
        )

        assert result is True
        mock_redis.setex.assert_called_once()

        # Verify updated state
        updated_state = json.loads(mock_redis.setex.call_args[0][2])
        assert updated_state["phase"] == "data"

    def test_request_cancellation(self, mock_redis):
        """Test requesting orchestration cancellation."""
        from app.wrappers.dashboard_orchestration_handlers import OrchestrationStateManager

        existing_state = {"orchestration_id": "test-id", "phase": "company"}
        mock_redis.get.return_value = json.dumps(existing_state)

        manager = OrchestrationStateManager(redis_client=mock_redis)
        result = manager.request_cancellation("test-user", "test-session")

        assert result is True

        # Verify cancellation flag was set
        updated_state = json.loads(mock_redis.setex.call_args[0][2])
        assert updated_state["cancellation_requested"] is True

    def test_is_cancelled(self, mock_redis):
        """Test checking if orchestration is cancelled."""
        from app.wrappers.dashboard_orchestration_handlers import OrchestrationStateManager

        manager = OrchestrationStateManager(redis_client=mock_redis)

        # Test 1: No state (should be cancelled)
        mock_redis.get.return_value = None
        assert manager.is_cancelled("test-user", "test-session", "test-id") is True

        # Test 2: Cancellation requested
        mock_redis.get.return_value = json.dumps({
            "orchestration_id": "test-id",
            "cancellation_requested": True
        })
        assert manager.is_cancelled("test-user", "test-session", "test-id") is True

        # Test 3: Different orchestration ID
        mock_redis.get.return_value = json.dumps({
            "orchestration_id": "different-id",
            "cancellation_requested": False
        })
        assert manager.is_cancelled("test-user", "test-session", "test-id") is True

        # Test 4: Active orchestration
        mock_redis.get.return_value = json.dumps({
            "orchestration_id": "test-id",
            "cancellation_requested": False
        })
        assert manager.is_cancelled("test-user", "test-session", "test-id") is False


@pytest.mark.asyncio
class TestOrchestrationHandlers:
    """Test the main orchestration handlers."""

    async def test_handle_orchestrate_init(self, setup_mocks):
        """Test handle_orchestrate_init creates orchestration and starts background task."""
        from app.wrappers.dashboard_orchestration_handlers import handle_orchestrate_init

        mocks = setup_mocks

        response = await handle_orchestrate_init(
            uid="test-user",
            session_id="test-session",
            payload={}
        )

        # Verify response structure
        assert response["type"] == "dashboard.orchestrate_init"
        assert response["payload"]["success"] is True
        assert "orchestration_id" in response["payload"]

        # Verify Redis was called to create state
        mocks['redis'].setex.assert_called()

    async def test_handle_company_change(self, setup_mocks):
        """Test handle_company_change with valid company_id."""
        from app.wrappers.dashboard_orchestration_handlers import handle_company_change

        mocks = setup_mocks

        response = await handle_company_change(
            uid="test-user",
            session_id="test-session",
            payload={"company_id": "new-company"}
        )

        # Verify response
        assert response["type"] == "dashboard.company_change"
        assert response["payload"]["success"] is True
        assert response["payload"]["company_id"] == "new-company"

    async def test_handle_company_change_missing_id(self, setup_mocks):
        """Test handle_company_change with missing company_id."""
        from app.wrappers.dashboard_orchestration_handlers import handle_company_change

        response = await handle_company_change(
            uid="test-user",
            session_id="test-session",
            payload={}
        )

        # Verify error response
        assert response["type"] == "error"
        assert response["payload"]["success"] is False
        assert response["payload"]["code"] == "MISSING_COMPANY_ID"

    async def test_handle_refresh(self, setup_mocks):
        """Test handle_refresh with selected company."""
        from app.wrappers.dashboard_orchestration_handlers import handle_refresh

        mocks = setup_mocks

        # Setup Redis to return selected company
        mocks['redis'].get.return_value = json.dumps({
            "company_id": "test-company",
            "company_name": "Test Co"
        })

        response = await handle_refresh(
            uid="test-user",
            session_id="test-session",
            payload={}
        )

        # Verify response
        assert response["type"] == "dashboard.refresh"
        assert response["payload"]["success"] is True
        assert response["payload"]["company_id"] == "test-company"

        # Verify dashboard handlers was called with force_refresh
        mocks['dashboard_handlers'].full_data.assert_called_once()
        call_kwargs = mocks['dashboard_handlers'].full_data.call_args[1]
        assert call_kwargs["force_refresh"] is True

        # Verify broadcast was called
        mocks['hub'].broadcast.assert_called()

    async def test_handle_refresh_no_company(self, setup_mocks):
        """Test handle_refresh with no selected company."""
        from app.wrappers.dashboard_orchestration_handlers import handle_refresh

        mocks = setup_mocks

        # Setup Redis to return None
        mocks['redis'].get.return_value = None

        response = await handle_refresh(
            uid="test-user",
            session_id="test-session",
            payload={}
        )

        # Verify error response
        assert response["type"] == "error"
        assert response["payload"]["success"] is False
        assert response["payload"]["code"] == "NO_COMPANY"


def test_module_exports():
    """Test that all expected functions are exported."""
    from app.wrappers import dashboard_orchestration_handlers

    # Check main handler functions exist
    assert hasattr(dashboard_orchestration_handlers, 'handle_orchestrate_init')
    assert hasattr(dashboard_orchestration_handlers, 'handle_company_change')
    assert hasattr(dashboard_orchestration_handlers, 'handle_refresh')
    assert hasattr(dashboard_orchestration_handlers, 'get_state_manager')
    assert hasattr(dashboard_orchestration_handlers, 'OrchestrationStateManager')

    # Check they are callable
    assert callable(dashboard_orchestration_handlers.handle_orchestrate_init)
    assert callable(dashboard_orchestration_handlers.handle_company_change)
    assert callable(dashboard_orchestration_handlers.handle_refresh)
    assert callable(dashboard_orchestration_handlers.get_state_manager)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
