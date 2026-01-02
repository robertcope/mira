"""
Google Chat API client for sending proactive messages.

Uses service account authentication to send messages to Google Chat spaces.
Credentials stored in Vault following MIRA's security patterns.
"""
import logging
from typing import Dict, Any, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from clients.vault_client import get_secret_data

logger = logging.getLogger(__name__)

# Google Chat API scopes
SCOPES = ['https://www.googleapis.com/auth/chat.bot']


class GoogleChatClient:
    """
    Client for sending messages to Google Chat spaces via API.

    Requires service account credentials with Google Chat API access.
    """

    def __init__(self):
        """Initialize Google Chat API client with service account credentials."""
        self._service = None
        self._initialize_service()

    def _initialize_service(self):
        """
        Initialize Google Chat API service with credentials from Vault.

        Raises:
            RuntimeError: If credentials are missing or invalid (fail-fast)
        """
        try:
            # Get service account credentials from Vault
            credentials_json = get_secret_data('mira/google_service_account')

            if not credentials_json:
                raise RuntimeError(
                    "Google Chat service account credentials not found in Vault at "
                    "'mira/google_service_account'. Push notifications cannot function without credentials."
                )

            # Create credentials object
            credentials = service_account.Credentials.from_service_account_info(
                credentials_json,
                scopes=SCOPES
            )

            # Build Google Chat API service
            self._service = build('chat', 'v1', credentials=credentials)

            logger.info("Google Chat API client initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Google Chat API client: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to initialize Google Chat API client: {e}. "
                "Ensure service account credentials are stored in Vault."
            ) from e

    def send_message(
        self,
        space_name: str,
        text: str,
        thread_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a text message to a Google Chat space.

        Args:
            space_name: Space identifier (e.g., 'spaces/AAAAxxxxxxx')
            text: Message text to send
            thread_key: Optional thread key/name to reply in existing thread
                       Can be either a full resource name (spaces/.../threads/...)
                       or a custom threadKey

        Returns:
            API response with message details

        Raises:
            RuntimeError: If message send fails (infrastructure failure)
        """
        if not self._service:
            raise RuntimeError("Google Chat API service not initialized")

        try:
            message_body = {
                'text': text
            }

            # Add thread if provided
            request_body = message_body
            if thread_key:
                # If thread_key is a full resource name (starts with spaces/),
                # use thread.name, otherwise use thread.threadKey
                if thread_key.startswith('spaces/'):
                    request_body['thread'] = {'name': thread_key}
                else:
                    request_body['thread'] = {'threadKey': thread_key}

            # Send message via API
            result = self._service.spaces().messages().create(
                parent=space_name,
                body=request_body
            ).execute()

            logger.info(f"Message sent to space {space_name} (thread: {thread_key or 'none'})")
            return result

        except HttpError as e:
            logger.error(f"HTTP error sending message to {space_name}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to send message to Google Chat space {space_name}: {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error sending message to {space_name}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to send message to Google Chat space {space_name}: {e}"
            ) from e

    def send_card_message(
        self,
        space_name: str,
        card_json: Dict[str, Any],
        thread_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a card message to a Google Chat space.

        Args:
            space_name: Space identifier (e.g., 'spaces/AAAAxxxxxxx')
            card_json: Card message JSON (formatted by google_chat_formatter)
            thread_key: Optional thread key/name to reply in existing thread
                       Can be either a full resource name (spaces/.../threads/...)
                       or a custom threadKey

        Returns:
            API response with message details

        Raises:
            RuntimeError: If message send fails (infrastructure failure)
        """
        if not self._service:
            raise RuntimeError("Google Chat API service not initialized")

        try:
            request_body = card_json.copy()

            # Add thread if provided
            if thread_key:
                # If thread_key is a full resource name (starts with spaces/),
                # use thread.name, otherwise use thread.threadKey
                if thread_key.startswith('spaces/'):
                    request_body['thread'] = {'name': thread_key}
                else:
                    request_body['thread'] = {'threadKey': thread_key}

            # Send card message via API
            logger.debug(f"Sending card message: parent={space_name}, body={request_body}")
            result = self._service.spaces().messages().create(
                parent=space_name,
                body=request_body
            ).execute()

            logger.info(f"Card message sent to space {space_name} (thread: {thread_key or 'none'}), result: {result.get('name', 'unknown')}")
            return result

        except HttpError as e:
            logger.error(f"HTTP error sending card to {space_name}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to send card message to Google Chat space {space_name}: {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error sending card to {space_name}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to send card message to Google Chat space {space_name}: {e}"
            ) from e

    def update_message(
        self,
        message_name: str,
        card_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing message in Google Chat.

        Args:
            message_name: Full message resource name (e.g., 'spaces/.../messages/...')
            card_json: Updated card message JSON

        Returns:
            API response with updated message details

        Raises:
            RuntimeError: If message update fails (infrastructure failure)
        """
        if not self._service:
            raise RuntimeError("Google Chat API service not initialized")

        try:
            request_body = card_json.copy()

            # Update message via API
            logger.debug(f"Updating message: name={message_name}")
            result = self._service.spaces().messages().update(
                name=message_name,
                updateMask='cardsV2',
                body=request_body
            ).execute()

            logger.info(f"Message updated: {message_name}")
            return result

        except HttpError as e:
            logger.error(f"HTTP error updating message {message_name}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to update Google Chat message {message_name}: {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error updating message {message_name}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to update Google Chat message {message_name}: {e}"
            ) from e


# Global singleton instance
_google_chat_client: Optional[GoogleChatClient] = None


def get_google_chat_client() -> GoogleChatClient:
    """
    Get or create the global GoogleChatClient instance.

    Returns:
        GoogleChatClient instance

    Raises:
        RuntimeError: If client initialization fails
    """
    global _google_chat_client

    if _google_chat_client is None:
        _google_chat_client = GoogleChatClient()

    return _google_chat_client
