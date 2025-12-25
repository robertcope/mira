"""
Tests for UI session storage (chat display state).

Verifies that the UI session handler correctly stores, retrieves, and clears
chat display messages in Valkey.
"""
import pytest
import json
from unittest.mock import Mock, patch

from cns.api.actions import UISessionDomainHandler, ValidationError


@pytest.fixture
def ui_handler():
    """Create UI session handler with mocked user context."""
    with patch('cns.api.actions.get_current_user_id', return_value='test-user-123'):
        handler = UISessionDomainHandler()
        yield handler


@pytest.fixture
def mock_valkey():
    """Mock Valkey client."""
    with patch('cns.api.actions.get_valkey_client') as mock:
        valkey_instance = Mock()
        mock.return_value = valkey_instance
        yield valkey_instance


def test_save_display_success(ui_handler, mock_valkey):
    """Test saving display state."""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]

    result = ui_handler.execute_action("save_display", {"messages": messages})

    assert result["saved"] is True
    assert result["message_count"] == 2

    # Verify Valkey was called with correct parameters
    mock_valkey.setex.assert_called_once()
    call_args = mock_valkey.setex.call_args
    assert call_args[0][0] == "ui_session:test-user-123:chat_display"
    assert call_args[0][1] == 604800  # 7 days TTL
    stored_data = json.loads(call_args[0][2])
    assert len(stored_data) == 2
    assert stored_data[0]["role"] == "user"


def test_save_display_validates_structure(ui_handler, mock_valkey):
    """Test that save_display validates message structure."""
    # Invalid: not a list
    with pytest.raises(ValidationError, match="messages must be a list"):
        ui_handler.execute_action("save_display", {"messages": "not a list"})

    # Invalid: missing required fields
    with pytest.raises(ValidationError, match="must have 'role' and 'content' fields"):
        ui_handler.execute_action("save_display", {"messages": [{"role": "user"}]})

    # Invalid: bad role
    with pytest.raises(ValidationError, match="Invalid role"):
        ui_handler.execute_action("save_display", {
            "messages": [{"role": "invalid", "content": "test"}]
        })


def test_get_display_with_stored_data(ui_handler, mock_valkey):
    """Test retrieving stored display state."""
    stored_messages = [
        {"role": "user", "content": "Test message"},
        {"role": "assistant", "content": "Test response"}
    ]
    mock_valkey.get.return_value = json.dumps(stored_messages)

    result = ui_handler.execute_action("get_display", {})

    assert "messages" in result
    assert len(result["messages"]) == 2
    assert result["messages"][0]["role"] == "user"
    assert result["message_count"] == 2
    mock_valkey.get.assert_called_once_with("ui_session:test-user-123:chat_display")


def test_get_display_no_stored_data(ui_handler, mock_valkey):
    """Test retrieving display state when none exists."""
    mock_valkey.get.return_value = None

    result = ui_handler.execute_action("get_display", {})

    assert result["messages"] == []
    assert "No stored display state" in result["message"]


def test_get_display_handles_corrupt_data(ui_handler, mock_valkey):
    """Test that get_display handles corrupt JSON gracefully."""
    mock_valkey.get.return_value = "not valid json"

    result = ui_handler.execute_action("get_display", {})

    assert result["messages"] == []
    assert "Failed to decode" in result["message"]


def test_clear_display_success(ui_handler, mock_valkey):
    """Test clearing display state."""
    mock_valkey.delete.return_value = True  # Key was deleted

    result = ui_handler.execute_action("clear_display", {})

    assert result["cleared"] is True
    assert "cleared" in result["message"]
    mock_valkey.delete.assert_called_once_with("ui_session:test-user-123:chat_display")


def test_clear_display_nothing_to_clear(ui_handler, mock_valkey):
    """Test clearing when no display state exists."""
    mock_valkey.delete.return_value = False  # No keys deleted

    result = ui_handler.execute_action("clear_display", {})

    assert result["cleared"] is False
    assert "No display state to clear" in result["message"]


def test_actions_schema():
    """Verify the handler declares correct action schemas."""
    handler = UISessionDomainHandler()

    assert "save_display" in handler.ACTIONS
    assert "get_display" in handler.ACTIONS
    assert "clear_display" in handler.ACTIONS

    # Verify save_display requires messages
    save_schema = handler.ACTIONS["save_display"]
    assert "messages" in save_schema["required"]

    # Verify get/clear have no required fields
    assert handler.ACTIONS["get_display"]["required"] == []
    assert handler.ACTIONS["clear_display"]["required"] == []
