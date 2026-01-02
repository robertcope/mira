"""
Test Google Chat search completion notifications.

Tests the event-driven notification system that sends Google Chat messages
when background searches complete.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4

from cns.core.events import UpdateTrinketEvent
from utils.google_chat_notifier import GoogleChatNotifier
from utils.timezone_utils import utc_now


@pytest.fixture
def mock_chat_client():
    """Mock Google Chat client."""
    return MagicMock()


@pytest.fixture
def mock_spaces_repo():
    """Mock Google Chat spaces repository."""
    repo = Mock()
    repo.get_space_for_user.return_value = {
        'space_name': 'spaces/AAAAAAAAAAA',
        'thread_key': None
    }
    return repo


@pytest.fixture
def mock_db():
    """Mock PostgreSQL client."""
    return Mock()


@pytest.fixture
def mock_event_bus():
    """Mock event bus that captures subscriptions."""
    bus = Mock()
    bus.subscribers = {}

    def subscribe(event_type, callback):
        if event_type not in bus.subscribers:
            bus.subscribers[event_type] = []
        bus.subscribers[event_type].append(callback)

    bus.subscribe = subscribe
    return bus


@pytest.fixture
def notifier(mock_event_bus, mock_chat_client, mock_spaces_repo, mock_db):
    """Create GoogleChatNotifier with mocked dependencies."""
    with patch('utils.google_chat_notifier.PostgresClient', return_value=mock_db), \
         patch('utils.google_chat_notifier.GoogleChatSpacesRepository', return_value=mock_spaces_repo), \
         patch('utils.google_chat_notifier.get_google_chat_client', return_value=mock_chat_client):

        notifier = GoogleChatNotifier(event_bus=mock_event_bus)
        notifier.chat_client = mock_chat_client  # Bypass lazy init
        return notifier


def test_notifier_subscribes_to_update_trinket_event(mock_event_bus, notifier):
    """Test that notifier subscribes to UpdateTrinketEvent on init."""
    assert 'UpdateTrinketEvent' in mock_event_bus.subscribers
    assert len(mock_event_bus.subscribers['UpdateTrinketEvent']) == 1


def test_handles_success_event(notifier, mock_chat_client, mock_event_bus):
    """Test handling of successful search completion event."""
    user_id = str(uuid4())
    continuum_id = str(uuid4())
    task_id = str(uuid4())

    # Create success event
    event = UpdateTrinketEvent(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        target_trinket='GetContextTrinket',
        context={
            'task_id': task_id,
            'status': 'success',
            'query': 'test search query',
            'summary': {
                'findings': [
                    {'source': 'conversation', 'content': 'finding 1'},
                    {'source': 'memory', 'content': 'finding 2'},
                    {'source': 'web', 'content': 'finding 3'}
                ]
            }
        }
    )

    # Trigger the subscription callback
    callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
    callback(event)

    # Verify Google Chat message was sent
    mock_chat_client.send_message.assert_called_once()
    call_args = mock_chat_client.send_message.call_args

    assert call_args.kwargs['space_name'] == 'spaces/AAAAAAAAAAA'
    assert 'test search query' in call_args.kwargs['text']
    assert 'Found 3 relevant items' in call_args.kwargs['text']
    assert '✓' in call_args.kwargs['text']


def test_handles_timeout_event(notifier, mock_chat_client, mock_event_bus):
    """Test handling of search timeout event."""
    user_id = str(uuid4())
    continuum_id = str(uuid4())
    task_id = str(uuid4())

    # Create timeout event
    event = UpdateTrinketEvent(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        target_trinket='GetContextTrinket',
        context={
            'task_id': task_id,
            'status': 'timeout',
            'query': 'slow search query',
            'iteration': 8,
            'elapsed': 420.5,
            'search_mode': 'comprehensive',
            'findings_count': 5
        }
    )

    # Trigger the subscription callback
    callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
    callback(event)

    # Verify Google Chat message was sent
    mock_chat_client.send_message.assert_called_once()
    call_args = mock_chat_client.send_message.call_args

    assert 'slow search query' in call_args.kwargs['text']
    assert '8 iterations' in call_args.kwargs['text']
    assert '5 findings' in call_args.kwargs['text']
    assert '⏱' in call_args.kwargs['text']


def test_handles_failure_event(notifier, mock_chat_client, mock_event_bus):
    """Test handling of search failure event."""
    user_id = str(uuid4())
    continuum_id = str(uuid4())
    task_id = str(uuid4())

    # Create failure event
    event = UpdateTrinketEvent(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        target_trinket='GetContextTrinket',
        context={
            'task_id': task_id,
            'status': 'failed',
            'query': 'failed search query',
            'error': 'LLM service unavailable',
            'error_type': 'ServiceError'
        }
    )

    # Trigger the subscription callback
    callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
    callback(event)

    # Verify Google Chat message was sent
    mock_chat_client.send_message.assert_called_once()
    call_args = mock_chat_client.send_message.call_args

    assert 'failed search query' in call_args.kwargs['text']
    assert 'ServiceError' in call_args.kwargs['text']
    assert '✗' in call_args.kwargs['text']


def test_ignores_pending_status(notifier, mock_chat_client, mock_event_bus):
    """Test that pending status events don't trigger notifications."""
    user_id = str(uuid4())
    continuum_id = str(uuid4())

    # Create pending event
    event = UpdateTrinketEvent(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        target_trinket='GetContextTrinket',
        context={
            'task_id': str(uuid4()),
            'status': 'pending',
            'query': 'pending search'
        }
    )

    # Trigger the subscription callback
    callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
    callback(event)

    # Verify no message was sent
    mock_chat_client.send_message.assert_not_called()


def test_ignores_other_trinkets(notifier, mock_chat_client, mock_event_bus):
    """Test that events for other trinkets are ignored."""
    user_id = str(uuid4())
    continuum_id = str(uuid4())

    # Create event for different trinket
    event = UpdateTrinketEvent(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        target_trinket='SomeOtherTrinket',
        context={
            'status': 'success',
            'data': 'something'
        }
    )

    # Trigger the subscription callback
    callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
    callback(event)

    # Verify no message was sent
    mock_chat_client.send_message.assert_not_called()


def test_handles_missing_google_chat_space(notifier, mock_chat_client, mock_spaces_repo, mock_event_bus):
    """Test graceful handling when user has no Google Chat space configured."""
    mock_spaces_repo.get_space_for_user.return_value = None

    user_id = str(uuid4())
    continuum_id = str(uuid4())

    # Create success event
    event = UpdateTrinketEvent(
        continuum_id=continuum_id,
        user_id=user_id,
        event_id=str(uuid4()),
        occurred_at=utc_now(),
        target_trinket='GetContextTrinket',
        context={
            'task_id': str(uuid4()),
            'status': 'success',
            'query': 'test query',
            'summary': {'findings': []}
        }
    )

    # Trigger the subscription callback
    callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
    callback(event)

    # Verify no message was sent (but no exception raised)
    mock_chat_client.send_message.assert_not_called()


def test_handles_google_chat_not_configured(mock_event_bus):
    """Test graceful handling when Google Chat client is not configured."""
    with patch('utils.google_chat_notifier.PostgresClient'), \
         patch('utils.google_chat_notifier.GoogleChatSpacesRepository'), \
         patch('utils.google_chat_notifier.get_google_chat_client', side_effect=RuntimeError("Not configured")):

        notifier = GoogleChatNotifier(event_bus=mock_event_bus)
        notifier._ensure_chat_client()

        # Verify chat_client is marked as unavailable
        assert notifier.chat_client is False

        # Create success event
        event = UpdateTrinketEvent(
            continuum_id=str(uuid4()),
            user_id=str(uuid4()),
            event_id=str(uuid4()),
            occurred_at=utc_now(),
            target_trinket='GetContextTrinket',
            context={
                'task_id': str(uuid4()),
                'status': 'success',
                'query': 'test query',
                'summary': {'findings': []}
            }
        )

        # Trigger callback - should not raise exception
        callback = mock_event_bus.subscribers['UpdateTrinketEvent'][0]
        callback(event)  # Should exit gracefully without sending


def test_format_search_notification_all_statuses(notifier):
    """Test message formatting for all completion statuses."""
    # Success with findings
    message = notifier._format_search_notification(
        'success',
        {
            'query': 'test query',
            'summary': {
                'findings': [{'a': 1}, {'b': 2}]
            }
        }
    )
    assert '✓' in message
    assert 'test query' in message
    assert 'Found 2 relevant items' in message

    # Timeout
    message = notifier._format_search_notification(
        'timeout',
        {
            'query': 'timeout query',
            'iteration': 5,
            'findings_count': 3
        }
    )
    assert '⏱' in message
    assert 'timeout query' in message
    assert '5 iterations' in message
    assert '3 findings' in message

    # Failure
    message = notifier._format_search_notification(
        'failed',
        {
            'query': 'failed query',
            'error_type': 'NetworkError'
        }
    )
    assert '✗' in message
    assert 'failed query' in message
    assert 'NetworkError' in message
