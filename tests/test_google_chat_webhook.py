"""
Test Google Chat webhook handler.

Tests the webhook endpoint with mock Google Chat payloads.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
import json


# Mock Google Chat MESSAGE event
MOCK_MESSAGE_EVENT = {
    "type": "MESSAGE",
    "message": {
        "text": "Hello MIRA, what's the weather today?",
        "sender": {
            "name": "users/12345678901234567890",
            "displayName": "Taylor",
            "email": "taylor@example.com",
            "type": "HUMAN"
        },
        "createTime": "2025-12-26T10:30:00.000000Z",
        "name": "spaces/AAAAAAAAAAA/messages/xyz123"
    },
    "user": {
        "name": "users/12345678901234567890",
        "displayName": "Taylor",
        "email": "taylor@example.com",
        "type": "HUMAN"
    },
    "space": {
        "name": "spaces/AAAAAAAAAAA",
        "type": "DM"
    },
    "token": "mock-bearer-token-for-attachments"
}

# Mock ADDED_TO_SPACE event
MOCK_ADDED_EVENT = {
    "type": "ADDED_TO_SPACE",
    "space": {
        "name": "spaces/AAAAAAAAAAA",
        "type": "DM"
    },
    "user": {
        "name": "users/12345678901234567890",
        "displayName": "Taylor",
        "email": "taylor@example.com",
        "type": "HUMAN"
    }
}

# Mock REMOVED_FROM_SPACE event
MOCK_REMOVED_EVENT = {
    "type": "REMOVED_FROM_SPACE",
    "space": {
        "name": "spaces/AAAAAAAAAAA",
        "type": "DM"
    },
    "user": {
        "name": "users/12345678901234567890",
        "displayName": "Taylor",
        "email": "taylor@example.com",
        "type": "HUMAN"
    }
}


@pytest.fixture
def client():
    """Create test client with mocked app state."""
    from main import create_app
    app = create_app()

    # Mock single-user credentials
    app.state.single_user_id = "test-user-id"
    app.state.user_email = "test@localhost"
    app.state.api_key = "test-api-key"

    return TestClient(app)


def test_added_to_space_event(client):
    """Test ADDED_TO_SPACE event returns greeting."""
    response = client.post(
        "/v0/api/google-chat",
        json=MOCK_ADDED_EVENT
    )

    assert response.status_code == 200
    data = response.json()
    assert "text" in data
    assert "MIRA" in data["text"]
    assert "assistant" in data["text"].lower()


def test_removed_from_space_event(client):
    """Test REMOVED_FROM_SPACE event returns empty response."""
    response = client.post(
        "/v0/api/google-chat",
        json=MOCK_REMOVED_EVENT
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {}


@patch('cns.api.google_chat.get_orchestrator')
@patch('cns.api.google_chat.get_continuum_pool')
@patch('cns.api.google_chat._user_request_lock')
def test_message_event(mock_lock, mock_pool, mock_orchestrator, client):
    """Test MESSAGE event processes through MIRA orchestrator."""
    # Mock lock acquisition
    mock_lock.acquire.return_value = True

    # Mock continuum pool
    mock_continuum = Mock()
    mock_continuum.id = "continuum-123"
    mock_pool_instance = Mock()
    mock_pool_instance.get_or_create.return_value = mock_continuum
    mock_pool_instance.repository.increment_segment_turn.return_value = 1

    # Mock active segment
    mock_sentinel = Mock()
    mock_sentinel.metadata = {"segment_id": "segment-456"}
    mock_pool_instance.repository.find_active_segment.return_value = mock_sentinel

    # Mock unit of work
    mock_uow = Mock()
    mock_pool_instance.begin_work.return_value = mock_uow

    mock_pool.return_value = mock_pool_instance

    # Mock orchestrator
    mock_orch_instance = Mock()
    mock_orch_instance.process_message.return_value = (
        mock_continuum,
        "This is MIRA's response about the weather.",
        {
            "tools_used": ["web_tool"],
            "referenced_memories": [],
            "surfaced_memories": []
        }
    )
    mock_orchestrator.return_value = mock_orch_instance

    # Send MESSAGE event
    response = client.post(
        "/v0/api/google-chat",
        json=MOCK_MESSAGE_EVENT
    )

    assert response.status_code == 200
    data = response.json()

    # Verify Card structure
    assert "cardsV2" in data
    assert len(data["cardsV2"]) == 1
    assert data["cardsV2"][0]["cardId"] == "mira-response"

    # Verify message was processed
    mock_orch_instance.process_message.assert_called_once()
    call_args = mock_orch_instance.process_message.call_args

    # Check message text was passed
    assert "weather" in str(call_args).lower()

    # Verify UOW commit was called
    mock_uow.commit.assert_called_once()

    # Verify lock was released
    mock_lock.release.assert_called_once_with("test-user-id")


def test_empty_message(client):
    """Test empty message returns validation error."""
    empty_event = {
        "type": "MESSAGE",
        "message": {
            "text": "   ",  # Empty/whitespace only
            "sender": {"name": "users/123", "displayName": "Test"}
        },
        "user": {"name": "users/123"}
    }

    response = client.post(
        "/v0/api/google-chat",
        json=empty_event
    )

    assert response.status_code == 200
    data = response.json()

    # Should return error card
    assert "cardsV2" in data
    card = data["cardsV2"][0]
    assert card["cardId"] == "mira-error"


def test_unknown_event_type(client):
    """Test unknown event type returns empty response."""
    unknown_event = {
        "type": "UNKNOWN_EVENT_TYPE",
        "space": {"name": "spaces/ABC"}
    }

    response = client.post(
        "/v0/api/google-chat",
        json=unknown_event
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {}


@patch('cns.api.google_chat.get_orchestrator')
@patch('cns.api.google_chat.get_continuum_pool')
@patch('cns.api.google_chat._user_request_lock')
@patch('cns.api.google_chat.requests.get')
@patch('cns.api.google_chat.compress_image')
def test_message_with_image_attachment(mock_compress, mock_requests_get, mock_lock, mock_pool, mock_orchestrator, client):
    """Test MESSAGE event with image attachment downloads and processes image."""
    # Mock image download
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"fake-image-bytes"
    mock_requests_get.return_value = mock_response

    # Mock image compression
    from utils.image_compression import CompressedImage
    mock_compressed = CompressedImage(
        inference_base64="inference-base64-data",
        inference_media_type="image/jpeg",
        storage_base64="storage-base64-data",
        storage_media_type="image/webp"
    )
    mock_compress.return_value = mock_compressed

    # Mock lock acquisition
    mock_lock.acquire.return_value = True

    # Mock continuum pool
    mock_continuum = Mock()
    mock_continuum.id = "continuum-123"
    mock_pool_instance = Mock()
    mock_pool_instance.get_or_create.return_value = mock_continuum
    mock_pool_instance.repository.increment_segment_turn.return_value = 1

    # Mock active segment
    mock_sentinel = Mock()
    mock_sentinel.metadata = {"segment_id": "segment-456"}
    mock_pool_instance.repository.find_active_segment.return_value = mock_sentinel

    # Mock unit of work
    mock_uow = Mock()
    mock_pool_instance.begin_work.return_value = mock_uow
    mock_pool.return_value = mock_pool_instance

    # Mock orchestrator
    mock_orch_instance = Mock()
    mock_orch_instance.process_message.return_value = (
        mock_continuum,
        "I can see a cat in this image.",
        {"tools_used": [], "referenced_memories": [], "surfaced_memories": []}
    )
    mock_orchestrator.return_value = mock_orch_instance

    # Create message with image attachment (legacy format)
    image_event = {
        "type": "MESSAGE",
        "message": {
            "text": "Can you analyze this image?",
            "sender": {
                "name": "users/123",
                "displayName": "Taylor",
                "email": "taylor@example.com"
            },
            "attachment": [
                {
                    "name": "photo.jpg",
                    "contentType": "image/jpeg",
                    "downloadUrl": "https://chat.googleapis.com/v1/media/fake-url"
                }
            ]
        },
        "user": {"name": "users/123", "displayName": "Taylor"},
        "space": {"name": "spaces/ABC", "type": "DM"},
        "token": "mock-bearer-token"
    }

    response = client.post(
        "/v0/api/google-chat",
        json=image_event
    )

    assert response.status_code == 200

    # Verify image download was attempted
    mock_requests_get.assert_called_once()
    call_args = mock_requests_get.call_args
    assert call_args[0][0] == "https://chat.googleapis.com/v1/media/fake-url"
    assert call_args[1]["headers"]["Authorization"] == "Bearer mock-bearer-token"

    # Verify image compression was called
    mock_compress.assert_called_once_with(b"fake-image-bytes", "image/jpeg")

    # Verify orchestrator received image content
    mock_orch_instance.process_message.assert_called_once()
    call_args = mock_orch_instance.process_message.call_args
    inference_content = call_args[0][1]  # Second positional arg

    # Should be a list with text and image blocks
    assert isinstance(inference_content, list)
    assert len(inference_content) == 2
    assert inference_content[0]["type"] == "text"
    assert inference_content[1]["type"] == "image"
    assert inference_content[1]["source"]["type"] == "base64"
    assert inference_content[1]["source"]["data"] == "inference-base64-data"


@patch('cns.api.google_chat.get_orchestrator')
@patch('cns.api.google_chat.get_continuum_pool')
@patch('cns.api.google_chat._user_request_lock')
@patch('cns.api.google_chat.requests.get')
@patch('cns.api.google_chat.compress_image')
def test_chat_app_format_with_image(mock_compress, mock_requests_get, mock_lock, mock_pool, mock_orchestrator, client):
    """Test Chat App format with image attachment."""
    # Mock image download
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"fake-image-bytes"
    mock_requests_get.return_value = mock_response

    # Mock image compression
    from utils.image_compression import CompressedImage
    mock_compressed = CompressedImage(
        inference_base64="inference-base64-data",
        inference_media_type="image/jpeg",
        storage_base64="storage-base64-data",
        storage_media_type="image/webp"
    )
    mock_compress.return_value = mock_compressed

    # Mock lock acquisition
    mock_lock.acquire.return_value = True

    # Mock continuum pool
    mock_continuum = Mock()
    mock_continuum.id = "continuum-123"
    mock_pool_instance = Mock()
    mock_pool_instance.get_or_create.return_value = mock_continuum
    mock_pool_instance.repository.increment_segment_turn.return_value = 1

    # Mock active segment
    mock_sentinel = Mock()
    mock_sentinel.metadata = {"segment_id": "segment-456"}
    mock_pool_instance.repository.find_active_segment.return_value = mock_sentinel

    # Mock unit of work
    mock_uow = Mock()
    mock_pool_instance.begin_work.return_value = mock_uow
    mock_pool.return_value = mock_pool_instance

    # Mock orchestrator
    mock_orch_instance = Mock()
    mock_orch_instance.process_message.return_value = (
        mock_continuum,
        "I can see a dog in this image.",
        {"tools_used": [], "referenced_memories": [], "surfaced_memories": []}
    )
    mock_orchestrator.return_value = mock_orch_instance

    # Create Chat App format message with image
    chat_app_event = {
        "chat": {
            "messagePayload": {
                "message": {
                    "text": "What's in this picture?",
                    "attachment": [
                        {
                            "name": "image.png",
                            "contentType": "image/png",
                            "downloadUrl": "https://chat.googleapis.com/v1/media/chat-app-url"
                        }
                    ]
                }
            }
        },
        "token": "chat-app-bearer-token"
    }

    response = client.post(
        "/v0/api/google-chat",
        json=chat_app_event
    )

    assert response.status_code == 200

    # Verify image download was attempted with correct auth
    mock_requests_get.assert_called_once()
    call_args = mock_requests_get.call_args
    assert call_args[0][0] == "https://chat.googleapis.com/v1/media/chat-app-url"
    assert call_args[1]["headers"]["Authorization"] == "Bearer chat-app-bearer-token"

    # Verify image was compressed
    mock_compress.assert_called_once_with(b"fake-image-bytes", "image/png")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
