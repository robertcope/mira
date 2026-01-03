"""
Google Chat webhook API endpoint.

Receives webhook events from Google Chat and routes them to MIRA's orchestrator.
Supports MESSAGE events and attachment processing (images, documents).

Configuration:
- Set up Google Chat App in Google Cloud Console
- Configure webhook URL: https://your-domain.com/v0/api/google-chat
- Enable Chat API scope
- Store JWT audience in Vault at 'mira/google_chat_config' with 'audience' field
  (either your endpoint URL or Google Cloud project number)

Reference: https://developers.google.com/workspace/chat/verify-requests-from-chat
"""
import base64
import logging
import threading
import contextvars
from typing import Dict, Any, Optional, List, Union

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth.api import get_current_user
from utils.google_chat_jwt_verifier import (
    verify_google_chat_jwt,
    GoogleChatJWTVerificationError,
)
from clients.files_manager import FilesManager
from utils.distributed_lock import UserRequestQueue
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

# Google Chat webhook timeout
# Google Chat requires webhook responses within 30 seconds
# We set threshold at 25s to provide safety margin for response transmission
WEBHOOK_TIMEOUT_THRESHOLD_SECONDS = 25

# Image validation constants
# Pre-compression size limit - images are resized to 1200px for inference, so large
# raw files will be significantly reduced. Set high to avoid rejecting images that
# compress well. Anthropic's 5MB limit applies to base64-encoded compressed images.
SUPPORTED_IMAGE_FORMATS = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_MB = 20

# Per-user request queue
# processing_ttl=180s: accommodates long-running operations (web searches, complex tool usage)
# queue_timeout=300s: requests expire after 5min in queue
# max_queue_depth=5: prevent abuse
_user_request_queue = UserRequestQueue(
    processing_ttl=180,
    queue_timeout=300,
    max_queue_depth=5
)


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

        # Extract and store thread_key for per-thread tier resolution
        message_info = event_data.get("message", {})
        thread_info = message_info.get("thread", {})
        thread_key = thread_info.get("name")
        if thread_key:
            from utils.user_context import update_current_user
            update_current_user({'google_chat_thread_key': thread_key})
            logger.info(f"Set google_chat_thread_key: {thread_key}")

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

            # Get resource name from attachmentDataRef for downloading actual content
            attachment_data_ref = attachment.get("attachmentDataRef", {})
            resource_name = attachment_data_ref.get("resourceName")

            # Download using Google Chat API with service account credentials
            if resource_name:
                logger.info(f"[Google Chat] Downloading attachment via API using resourceName")
                try:
                    from utils.google_chat_client import get_google_chat_client

                    # Get authenticated API client
                    chat_client = get_google_chat_client()
                    if not chat_client._service:
                        raise RuntimeError("Google Chat API service not initialized")

                    # Download attachment using Google Chat API
                    # Use media().download() with alt=media to get raw bytes
                    request = chat_client._service.media().download_media(
                        resourceName=resource_name
                    )
                    attachment_bytes = request.execute()

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

                except Exception as e:
                    logger.error(f"[Google Chat] Error downloading attachment: {e}", exc_info=True)
                    raise ValidationError(f"Failed to download attachment: {e}")

        # Wait for processing lock (blocks until available instead of rejecting)
        # This ensures requests process serially without rejection errors
        logger.info(f"[Google Chat] Waiting for processing lock for user {user_id[:8]}")

        files_manager: Optional[FilesManager] = None
        try:
            # Use context manager to automatically acquire/release processing lock
            # This blocks until lock is available, ensuring serial processing
            with _user_request_queue.process_request(user_id):
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
                    attachment_data_ref = attachment.get("attachmentDataRef", {})
                    resource_name = attachment_data_ref.get("resourceName")

                    if resource_name and attachment_type in SUPPORTED_DOCUMENT_FORMATS:
                        files_manager = FilesManager(orchestrator.llm_provider.anthropic_client)

                        # Download attachment using Google Chat API
                        try:
                            from utils.google_chat_client import get_google_chat_client

                            chat_client = get_google_chat_client()
                            if not chat_client._service:
                                raise RuntimeError("Google Chat API service not initialized")

                            request = chat_client._service.media().download_media(
                                resourceName=resource_name
                            )
                            document_bytes = request.execute()

                            filename = attachment.get("contentName", f"document.{attachment_type.split('/')[-1]}")

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

                        except Exception as e:
                            logger.error(f"[Google Chat] Error downloading document: {e}", exc_info=True)
                            raise ValidationError(f"Failed to download document: {e}")

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

                # Log processing time for monitoring long-running requests
                tools_used = metadata.get("tools_used", [])
                logger.info(
                    f"[Google Chat] Request completed in {processing_time_ms}ms "
                    f"(user={user_id[:8]}..., tools={len(tools_used)})"
                )

                # Format response as Google Chat Card
                return format_response_as_card(
                    response_text,
                    metadata=metadata,
                    include_metadata=False  # Set to True to show tools/timing footer
                )

        finally:
            # Context manager handles lock release, but clean up files if needed
            pass


def _process_message_with_timeout_protection(
    handler: GoogleChatHandler,
    user_id: str,
    message_text: str,
    event_data: Dict[str, Any],
    request: Request,
    request_start_time,
) -> Dict[str, Any]:
    """
    Process message with webhook timeout protection.

    Google Chat webhooks timeout after 30 seconds. If processing exceeds 25 seconds,
    we return an acknowledgment and continue processing in background, then push
    the final response via Google Chat API.

    Args:
        handler: GoogleChatHandler instance
        user_id: MIRA user ID
        message_text: Message text to process
        event_data: Full Google Chat event payload
        request: FastAPI request object
        request_start_time: Timestamp when webhook request started

    Returns:
        Google Chat Card response (either final response or acknowledgment)
    """
    # Extract space info for push notifications
    space_info = event_data.get("space", {})
    space_name = space_info.get("name")
    space_type = space_info.get("type", "DM")

    # Extract thread info - handle both Chat App format and legacy webhook format
    if "chat" in event_data:
        # Chat App format: thread is in chat.messagePayload.message.thread
        message_info = event_data.get("chat", {}).get("messagePayload", {}).get("message", {})
    else:
        # Legacy webhook format: thread is in message.thread
        message_info = event_data.get("message", {})

    thread_info = message_info.get("thread", {})
    thread_key = thread_info.get("name")

    logger.info(f"[Google Chat] Extracted thread_key for async response: {thread_key}")

    # Start a result container that the background thread can populate
    result_container = {"response": None, "completed": False}

    # Capture context before spawning thread
    ctx = contextvars.copy_context()

    def process_in_thread():
        """Execute processing and store result."""
        try:
            # Run processing within captured context
            def _process():
                response = handler.process_message_event(
                    user_id=user_id,
                    message_text=message_text,
                    event_data=event_data,
                    request=request
                )
                result_container["response"] = response
                result_container["completed"] = True

            # Execute in context to preserve user_id and other contextvars
            ctx.run(_process)
        except Exception as e:
            logger.error(f"[Google Chat] Processing thread failed: {e}", exc_info=True)
            result_container["error"] = e
            result_container["completed"] = True

    # Start processing in background thread
    process_thread = threading.Thread(
        target=process_in_thread,
        daemon=True
    )
    process_thread.start()

    # Wait for completion or timeout
    timeout_remaining = WEBHOOK_TIMEOUT_THRESHOLD_SECONDS - (utc_now() - request_start_time).total_seconds()
    process_thread.join(timeout=max(0, timeout_remaining))

    # Check if processing completed within timeout
    if result_container["completed"]:
        if "error" in result_container:
            raise result_container["error"]

        elapsed = (utc_now() - request_start_time).total_seconds()
        logger.info(f"[Google Chat] Synchronous response completed in {elapsed:.1f}s")
        return result_container["response"]

    # Timeout approaching - return acknowledgment and continue in background
    logger.info(
        f"[Google Chat] Webhook timeout approaching ({WEBHOOK_TIMEOUT_THRESHOLD_SECONDS}s), "
        f"returning acknowledgment and continuing in background"
    )

    def background_completion():
        """Wait for processing to complete then send the full response."""
        try:
            # Wait for processing thread to complete
            process_thread.join()

            if "error" in result_container:
                raise result_container["error"]

            # Send the full response via Google Chat API
            # Will appear in the thread specified by thread_key (both DMs and rooms support threading)
            from utils.google_chat_client import get_google_chat_client

            logger.info(f"[Google Chat] Sending background response to space={space_name}, thread_key={thread_key}")
            chat_client = get_google_chat_client()
            chat_client.send_card_message(
                space_name=space_name,
                card_json=result_container["response"],
                thread_key=thread_key
            )

            logger.info(f"[Google Chat] Background response sent to space {space_name}, thread {thread_key}")

        except Exception as e:
            logger.error(f"[Google Chat] Background completion failed: {e}", exc_info=True)

            # Try to send error notification
            try:
                from utils.google_chat_client import get_google_chat_client

                chat_client = get_google_chat_client()
                error_card = format_error_as_card(
                    "PROCESSING_ERROR",
                    "Failed to complete your request. Please try again."
                )
                chat_client.send_card_message(
                    space_name=space_name,
                    card_json=error_card,
                    thread_key=thread_key
                )
            except Exception as push_error:
                logger.error(f"[Google Chat] Failed to send error notification: {push_error}", exc_info=True)

    background_thread = threading.Thread(
        target=background_completion,
        daemon=True
    )
    background_thread.start()

    # Return acknowledgment to keep webhook alive
    # Google Chat will automatically thread this response correctly
    return format_simple_text("⏳ Working on your request...")


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

    Authentication:
    - Validates JWT bearer token from Google Chat
    - Returns 401 if token is missing or invalid
    """
    request_start = utc_now()

    # Verify JWT from Google Chat
    try:
        authorization_header = request.headers.get("Authorization")
        verify_google_chat_jwt(authorization_header)
    except GoogleChatJWTVerificationError as e:
        logger.warning(f"Google Chat JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))

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

            # Process message with timeout protection
            from anyio import to_thread
            from functools import partial
            handler = GoogleChatHandler()
            response_card = await to_thread.run_sync(
                partial(
                    _process_message_with_timeout_protection,
                    handler=handler,
                    user_id=user_id,
                    message_text=message_text,
                    event_data=event_data,
                    request=request,
                    request_start_time=request_start
                )
            )

            elapsed_ms = int((utc_now() - request_start).total_seconds() * 1000)
            logger.info(f"[Google Chat] Chat App message processed in {elapsed_ms}ms, sending response")

            return JSONResponse(content=response_card, media_type="application/json")

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

            # Process message with timeout protection
            from anyio import to_thread
            from functools import partial
            handler = GoogleChatHandler()
            response_card = await to_thread.run_sync(
                partial(
                    _process_message_with_timeout_protection,
                    handler=handler,
                    user_id=user_id,
                    message_text=message_text,
                    event_data=event_data,
                    request=request,
                    request_start_time=request_start
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
