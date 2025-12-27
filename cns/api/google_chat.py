"""
Google Chat webhook API endpoint.

Receives webhook events from Google Chat and routes them to MIRA's orchestrator.
Supports MESSAGE events and attachment processing (images, documents).

Configuration:
- Set up Google Chat App in Google Cloud Console
- Configure webhook URL: https://your-domain.com/v0/api/google-chat
- Enable Chat API scope
- For signature verification: store bearer token in Vault under google_chat/webhook_token

Reference: https://developers.google.com/chat/how-tos/webhooks
"""
import base64
import logging
from typing import Dict, Any, Optional, List, Union

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth.api import get_current_user
from clients.files_manager import FilesManager
from utils.distributed_lock import UserRequestLock
from utils.document_processing import process_document, ProcessedDocument, SUPPORTED_DOCUMENT_FORMATS
from utils.image_compression import compress_image, CompressedImage
from utils.text_sanitizer import sanitize_message_content
from utils.google_chat_formatter import format_response_as_card, format_error_as_card, format_simple_text
from utils.timezone_utils import utc_now
from utils.user_context import set_current_user_id
from .base import BaseHandler, ValidationError, create_success_response

from cns.services.orchestrator import get_orchestrator
from cns.infrastructure.continuum_pool import get_continuum_pool


logger = logging.getLogger(__name__)

router = APIRouter()

# Image validation constants
SUPPORTED_IMAGE_FORMATS = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_MB = 5

# Distributed per-user request lock
_user_request_lock = UserRequestLock(ttl=60)


class GoogleChatEvent(BaseModel):
    """Google Chat webhook event payload."""
    type: str = Field(..., description="Event type: MESSAGE, ADDED_TO_SPACE, REMOVED_FROM_SPACE")
    message: Optional[Dict[str, Any]] = Field(None, description="Message object for MESSAGE events")
    user: Optional[Dict[str, Any]] = Field(None, description="User who triggered the event")
    space: Optional[Dict[str, Any]] = Field(None, description="Space where event occurred")


class GoogleChatHandler(BaseHandler):
    """Handler for Google Chat webhook events."""

    def _store_chat_space(self, event_data: Dict[str, Any], user_id: str):
        """
        Store Google Chat space information for push notifications.

        Args:
            event_data: Full Google Chat event payload
            user_id: MIRA user ID
        """
        try:
            from utils.google_chat_spaces_repository import GoogleChatSpacesRepository

            # Extract space information from event
            space_info = event_data.get("space", {})
            space_name = space_info.get("name")

            if not space_name:
                logger.warning("No space name in Google Chat event")
                return

            # Determine space type
            space_type = space_info.get("type", "DM")

            # Extract thread key if present (for threading replies)
            thread_key = None
            message_info = event_data.get("message", {})
            thread_info = message_info.get("thread", {})
            if thread_info:
                thread_key = thread_info.get("name")

            # Store space info
            spaces_repo = GoogleChatSpacesRepository()
            spaces_repo.upsert_space(
                space_name=space_name,
                space_type=space_type,
                thread_key=thread_key
            )

            logger.debug(f"Stored Google Chat space {space_name} for user {user_id}")

        except Exception as e:
            # Don't fail the message processing if space storage fails
            logger.error(f"Error storing Google Chat space: {e}", exc_info=True)

    def process_message_event(
        self,
        *,
        user_id: str,
        message_text: str,
        event_data: Dict[str, Any],
        request: Request
    ) -> Dict[str, Any]:
        """
        Process MESSAGE event from Google Chat.

        Args:
            user_id: MIRA user_id (mapped from Google workspace user)
            message_text: Text content from Google Chat message
            event_data: Full Google Chat event payload
            request: FastAPI request object

        Returns:
            Google Chat Card message response
        """
        start_time = utc_now()

        # Validate user_id
        if not user_id or user_id.strip() == "":
            logger.error(f"Invalid user_id received: '{user_id}' (type: {type(user_id)})")
            raise ValidationError(f"Invalid user_id: user_id cannot be empty (received: '{user_id}')")

        logger.debug(f"Processing message for user_id: {user_id}")

        # Set user context for RLS
        set_current_user_id(user_id)

        # Store Google Chat space for push notifications
        self._store_chat_space(event_data, user_id)

        # Sanitize message text
        msg = sanitize_message_content(message_text.strip())
        if not msg:
            raise ValidationError("Message cannot be empty")

        # Check for attachments (images, documents)
        compressed_image: Optional[CompressedImage] = None
        processed_doc: Optional[ProcessedDocument] = None

        # Extract attachments from Chat App format or legacy webhook format
        if "chat" in event_data:
            # Chat App format: attachments in messagePayload.message.attachment
            message = event_data.get("chat", {}).get("messagePayload", {}).get("message", {})
            attachments = message.get("attachment", [])
            logger.info(f"[Google Chat] Chat App format detected, found {len(attachments)} attachments")
        else:
            # Legacy webhook format
            attachments = event_data.get("message", {}).get("attachment", [])
            logger.info(f"[Google Chat] Legacy webhook format detected, found {len(attachments)} attachments")

        if attachments:
            logger.info(f"[Google Chat] Processing attachment: {attachments[0]}")
            # Process first attachment only (Google Chat typically sends one at a time)
            attachment = attachments[0]
            attachment_type = attachment.get("contentType", "")
            download_url = attachment.get("downloadUrl")

            if download_url:
                # Download attachment from Google Chat
                # Note: Requires bearer token from event's 'token' field for authentication
                import requests
                bearer_token = event_data.get("token")
                if not bearer_token:
                    logger.error("[Google Chat] No bearer token found in event_data for attachment download")
                    raise ValidationError("Cannot download attachment: missing authentication token")

                logger.info(f"[Google Chat] Downloading attachment from {download_url[:50]}...")
                try:
                    response = requests.get(
                        download_url,
                        headers={"Authorization": f"Bearer {bearer_token}"},
                        timeout=30
                    )
                    logger.info(f"[Google Chat] Attachment download response: status={response.status_code}")

                    if response.status_code != 200:
                        logger.error(f"[Google Chat] Attachment download failed: {response.status_code} {response.text}")
                        raise ValidationError(f"Failed to download attachment: HTTP {response.status_code}")

                    attachment_bytes = response.content
                    logger.info(f"[Google Chat] Downloaded {len(attachment_bytes)} bytes, type={attachment_type}")

                    # Process as image or document
                    if attachment_type in SUPPORTED_IMAGE_FORMATS:
                        if len(attachment_bytes) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
                            raise ValidationError(f"Image exceeds {MAX_IMAGE_SIZE_MB}MB")
                        try:
                            logger.info(f"[Google Chat] Compressing image of type {attachment_type}")
                            compressed_image = compress_image(attachment_bytes, attachment_type)
                            logger.info(f"[Google Chat] Image compressed successfully")
                        except ValueError as e:
                            logger.error(f"[Google Chat] Image processing failed: {e}", exc_info=True)
                            raise ValidationError(f"Image processing failed: {e}")

                    elif attachment_type in SUPPORTED_DOCUMENT_FORMATS:
                        # Will process document after getting orchestrator (needs FilesManager)
                        logger.info(f"[Google Chat] Document attachment detected, will process after orchestrator setup")
                    else:
                        logger.warning(f"[Google Chat] Unsupported attachment type: {attachment_type}")

                except requests.RequestException as e:
                    logger.error(f"[Google Chat] Network error downloading attachment: {e}", exc_info=True)
                    raise ValidationError(f"Failed to download attachment: {e}")

        # Acquire user lock (one request at a time per user)
        if not _user_request_lock.acquire(user_id):
            raise ValidationError("Another request is already in progress")

        files_manager: Optional[FilesManager] = None
        try:
            # Resolve dependencies
            orchestrator = get_orchestrator()
            continuum_pool = get_continuum_pool()

            # Get user's continuum
            continuum = continuum_pool.get_or_create()

            # Increment segment turn counter
            segment_turn_number = continuum_pool.repository.increment_segment_turn(
                continuum.id, user_id
            )

            # Get segment ID for file lifecycle
            active_sentinel = continuum_pool.repository.find_active_segment(continuum.id, user_id)
            if not active_sentinel:
                raise ValidationError("No active segment found")
            segment_id = active_sentinel.metadata.get('segment_id')
            if not segment_id:
                raise ValidationError("Active segment missing segment_id")

            # Process document attachment if present
            if attachments and not compressed_image:
                attachment = attachments[0]
                attachment_type = attachment.get("contentType", "")
                download_url = attachment.get("downloadUrl")

                if download_url and attachment_type in SUPPORTED_DOCUMENT_FORMATS:
                    files_manager = FilesManager(orchestrator.llm_provider.anthropic_client)

                    # Download attachment
                    import requests
                    bearer_token = event_data.get("token")
                    response = requests.get(
                        download_url,
                        headers={"Authorization": f"Bearer {bearer_token}"}
                    )
                    if response.status_code == 200:
                        document_bytes = response.content
                        filename = attachment.get("name", f"document.{attachment_type.split('/')[-1]}")

                        try:
                            processed_doc = process_document(
                                document_bytes,
                                attachment_type,
                                files_manager=files_manager,
                                filename=filename,
                                segment_id=segment_id
                            )
                        except ValueError as e:
                            raise ValidationError(f"Document processing failed: {e}")

            # Build content arrays (same pattern as chat.py)
            inference_content: Union[str, List[Dict[str, Any]]]
            storage_content: Optional[Union[str, List[Dict[str, Any]]]] = None

            if compressed_image:
                # Image: inference tier (1200px) + storage tier (512px WebP)
                inference_content = [
                    {"type": "text", "text": msg},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": compressed_image.inference_media_type,
                            "data": compressed_image.inference_base64,
                        }
                    }
                ]
                storage_content = [
                    {"type": "text", "text": msg},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": compressed_image.storage_media_type,
                            "data": compressed_image.storage_base64,
                        }
                    }
                ]
            elif processed_doc:
                # Document handling based on content_type
                if processed_doc.content_type == "container_upload":
                    doc_block: Dict[str, Any] = {
                        "type": "container_upload",
                        "file_id": processed_doc.data
                    }
                elif processed_doc.content_type == "document":
                    doc_block = {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": processed_doc.media_type,
                            "data": processed_doc.data,
                        }
                    }
                else:
                    doc_block = {
                        "type": "text",
                        "text": f"[Document: {processed_doc.media_type}]\n{processed_doc.data}",
                    }

                inference_content = [{"type": "text", "text": msg}, doc_block]
                storage_content = [{"type": "text", "text": msg}, doc_block]
            else:
                inference_content = msg

            # Process via orchestrator
            uow = continuum_pool.begin_work(continuum)

            from config.config_manager import config as app_config
            continuum, response_text, metadata = orchestrator.process_message(
                continuum,
                inference_content,
                app_config.system_prompt,
                stream=True,
                stream_callback=None,
                unit_of_work=uow,
                storage_content=storage_content,
                segment_turn_number=segment_turn_number,
            )

            # Commit changes
            uow.commit()

            processing_time_ms = int((utc_now() - start_time).total_seconds() * 1000)

            # Add processing time to metadata
            metadata["processing_time_ms"] = processing_time_ms

            # Format response as Google Chat Card
            return format_response_as_card(
                response_text,
                metadata=metadata,
                include_metadata=False  # Set to True to show tools/timing footer
            )

        finally:
            _user_request_lock.release(user_id)


@router.post("/google-chat")
async def google_chat_webhook(request: Request):
    """
    Google Chat webhook endpoint.

    Receives events from Google Chat and processes MESSAGE events through MIRA.
    For OSS single-user mode, all messages route to the single MIRA user.

    Event types:
    - MESSAGE: User sent a message
    - ADDED_TO_SPACE: Bot added to space (respond with greeting)
    - REMOVED_FROM_SPACE: Bot removed (no response needed)
    """
    request_start = utc_now()
    try:
        event_data = await request.json()

        # Google Chat App format (interactive apps, not simple webhooks)
        # Has structure: { "chat": { "messagePayload": { "message": {...} } } }
        if "chat" in event_data:
            chat_data = event_data.get("chat", {})
            message_payload = chat_data.get("messagePayload", {})
            message = message_payload.get("message", {})
            message_text = message.get("text", "").strip()

            logger.info(f"[Google Chat] Received Chat App message: '{message_text[:50]}...'")

            if not message_text:
                return JSONResponse(
                    content=format_error_as_card(
                        "VALIDATION_ERROR",
                        "Message cannot be empty"
                    ),
                    media_type="application/json"
                )

            # For OSS single-user mode: Use the single MIRA user
            # For multi-user: Would map Google user email to MIRA user_id
            user_id = getattr(request.app.state, 'single_user_id', None)

            if not user_id:
                logger.error("single_user_id not set in app.state - Google Chat requires single-user mode")
                return JSONResponse(
                    content=format_error_as_card(
                        "CONFIGURATION_ERROR",
                        "MIRA not configured for single-user mode"
                    ),
                    media_type="application/json",
                    status_code=500
                )

            # Process message through MIRA (run synchronously in thread pool)
            from anyio import to_thread
            from functools import partial
            handler = GoogleChatHandler()
            response_card = await to_thread.run_sync(
                partial(
                    handler.process_message_event,
                    user_id=user_id,
                    message_text=message_text,
                    event_data=event_data,
                    request=request
                )
            )

            elapsed_ms = int((utc_now() - request_start).total_seconds() * 1000)
            logger.info(f"[Google Chat] Message processed in {elapsed_ms}ms, sending response")

            # Extract response text for simple text response
            # Google Chat Apps work better with plain text for conversational AI
            import json
            logger.info(f"[Google Chat] Full response: {json.dumps(response_card, indent=2)}")

            # Extract just the text from the card
            card_obj = response_card.get("cardsV2", [{}])[0].get("card", {})
            sections = card_obj.get("sections", [])
            widgets = sections[0].get("widgets", []) if sections else []
            text_widget = widgets[0] if widgets else {}
            response_text = text_widget.get("textParagraph", {}).get("text", "Response generated")

            # Return simple text response
            simple_response = {"text": response_text}

            logger.info(f"[Google Chat] Sending text response: {json.dumps(simple_response, indent=2)}")

            return JSONResponse(content=simple_response, media_type="application/json")

        # Legacy webhook format (simple webhooks, kept for backwards compatibility)
        event_type = event_data.get("type")
        logger.info(f"[Google Chat] Received webhook event: {event_type}")

        # Handle ADDED_TO_SPACE (bot added to space)
        if event_type == "ADDED_TO_SPACE":
            return JSONResponse(
                content=format_simple_text(
                    "👋 Hi! I'm MIRA, your AI assistant. Send me a message to get started!"
                ),
                media_type="application/json"
            )

        # Handle REMOVED_FROM_SPACE (no response needed)
        if event_type == "REMOVED_FROM_SPACE":
            return JSONResponse(content={}, media_type="application/json")

        # Handle MESSAGE event
        if event_type == "MESSAGE":
            message = event_data.get("message", {})
            message_text = message.get("text", "").strip()

            if not message_text:
                return JSONResponse(
                    content=format_error_as_card(
                        "VALIDATION_ERROR",
                        "Message cannot be empty"
                    ),
                    media_type="application/json"
                )

            # For OSS single-user mode: Use the single MIRA user
            # For multi-user: Would map Google user email to MIRA user_id
            user_id = getattr(request.app.state, 'single_user_id', None)

            if not user_id:
                logger.error("single_user_id not set in app.state - Google Chat requires single-user mode")
                return JSONResponse(
                    content=format_error_as_card(
                        "CONFIGURATION_ERROR",
                        "MIRA not configured for single-user mode"
                    ),
                    media_type="application/json",
                    status_code=500
                )

            # Process message through MIRA (run synchronously in thread pool)
            from anyio import to_thread
            from functools import partial
            handler = GoogleChatHandler()
            response_card = await to_thread.run_sync(
                partial(
                    handler.process_message_event,
                    user_id=user_id,
                    message_text=message_text,
                    event_data=event_data,
                    request=request
                )
            )

            elapsed_ms = int((utc_now() - request_start).total_seconds() * 1000)
            logger.info(f"[Google Chat] MESSAGE processed in {elapsed_ms}ms, sending response")

            return JSONResponse(content=response_card, media_type="application/json")

        # Unknown event type
        logger.warning(f"Unknown Google Chat event type: {event_type}")
        return JSONResponse(content={}, media_type="application/json")

    except ValidationError as e:
        logger.warning(f"Validation error: {e.message}")
        return JSONResponse(
            content=format_error_as_card("VALIDATION_ERROR", e.message),
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Google Chat webhook error: {e}", exc_info=True)
        return JSONResponse(
            content=format_error_as_card("INTERNAL_ERROR", "Failed to process message"),
            media_type="application/json"
        )
