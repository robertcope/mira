"""
WebSocket API endpoint - real-time bidirectional streaming chat.

Provides WebSocket protocol at /v0/ws/chat for streaming responses with
authentication via Bearer token (header) or auth message.
"""
import base64
import json
import logging
from typing import Dict, Any, Optional, Union, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from clients.files_manager import FilesManager
from utils.distributed_lock import UserRequestLock
from utils.document_processing import process_document, ProcessedDocument, SUPPORTED_DOCUMENT_FORMATS, MAX_DOCUMENT_SIZE_MB
from utils.image_compression import compress_image, CompressedImage
from utils.text_sanitizer import sanitize_message_content
from utils.timezone_utils import utc_now, format_utc_iso
from utils.user_context import set_current_user_id, set_current_user_data
from cns.services.orchestrator import get_orchestrator
from cns.infrastructure.continuum_pool import get_continuum_pool


logger = logging.getLogger(__name__)

router = APIRouter()


# Image validation constants
SUPPORTED_IMAGE_FORMATS = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_MB = 5

# Distributed per-user request lock (coordinates across workers)
_user_request_lock = UserRequestLock(ttl=60)


class WebSocketMessage(BaseModel):
    """Inbound WebSocket message structure."""
    type: str = Field(..., description="Message type (message, ping, auth)")
    content: Optional[str] = Field(None, description="Text content for message type")
    image: Optional[str] = Field(None, description="Base64-encoded image")
    image_type: Optional[str] = Field(None, description="MIME type for image")
    document: Optional[str] = Field(None, description="Base64-encoded document")
    document_type: Optional[str] = Field(None, description="MIME type for document")
    token: Optional[str] = Field(None, description="Bearer token for auth message type")


async def send_error(websocket: WebSocket, message: str):
    """Send error message to client."""
    await websocket.send_json({"type": "error", "message": message})


async def authenticate_websocket(websocket: WebSocket, app_state) -> Optional[Dict[str, Any]]:
    """
    Authenticate WebSocket connection via Bearer token in headers or auth message.

    Returns user context dict if authenticated, None otherwise.
    """
    # Method A: Check for Bearer token in headers
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Strip "Bearer " prefix

        if token == app_state.api_key:
            user_id = app_state.single_user_id
            user_email = app_state.user_email

            set_current_user_id(user_id)
            set_current_user_data({"user_id": user_id, "email": user_email})

            return {"user_id": user_id, "email": user_email}
        else:
            await send_error(websocket, "Invalid authentication token")
            await websocket.close(code=1008)  # Policy violation
            return None

    # Method B: Wait for auth message
    try:
        data = await websocket.receive_json()

        if data.get("type") != "auth":
            await send_error(websocket, "Authentication required - send auth message or Bearer token in headers")
            await websocket.close(code=1008)
            return None

        token = data.get("token")
        if not token or token != app_state.api_key:
            await websocket.send_json({"type": "auth_failure"})
            await websocket.close(code=1008)
            return None

        user_id = app_state.single_user_id
        user_email = app_state.user_email

        set_current_user_id(user_id)
        set_current_user_data({"user_id": user_id, "email": user_email})

        await websocket.send_json({"type": "auth_success"})
        return {"user_id": user_id, "email": user_email}

    except Exception as e:
        logger.error(f"WebSocket authentication error: {e}")
        await send_error(websocket, "Authentication failed")
        await websocket.close(code=1011)  # Internal error
        return None


async def handle_message(
    websocket: WebSocket,
    user_id: str,
    message: str,
    image: Optional[str],
    image_type: Optional[str],
    document: Optional[str],
    document_type: Optional[str],
):
    """Process a chat message and stream the response."""
    start_time = utc_now()

    # Set user context for RLS
    set_current_user_id(user_id)

    # Basic validation
    msg = (message or "").strip()
    if not msg:
        await send_error(websocket, "Message cannot be empty")
        return

    # Sanitize text
    msg = sanitize_message_content(msg)

    # Validate and compress image if provided
    compressed: Optional[CompressedImage] = None
    if image:
        if not image_type:
            await send_error(websocket, "image_type is required when image is provided")
            return
        if image_type not in SUPPORTED_IMAGE_FORMATS:
            await send_error(
                websocket,
                f"Unsupported image format. Supported: {', '.join(sorted(SUPPORTED_IMAGE_FORMATS))}"
            )
            return
        try:
            decoded = base64.b64decode(image, validate=True)
            if len(decoded) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
                await send_error(websocket, f"Image exceeds maximum size of {MAX_IMAGE_SIZE_MB}MB")
                return

            compressed = compress_image(decoded, image_type)
        except ValueError as e:
            await send_error(websocket, f"Image compression failed: {e}")
            return
        except Exception as e:
            await send_error(websocket, f"Invalid base64 image: {str(e)}")
            return

    # Validate document if provided
    document_bytes: Optional[bytes] = None
    if document:
        if not document_type:
            await send_error(websocket, "document_type is required when document is provided")
            return
        if document_type not in SUPPORTED_DOCUMENT_FORMATS:
            await send_error(
                websocket,
                f"Unsupported document format. Supported: PDF, DOCX, XLSX, TXT, CSV, JSON"
            )
            return
        try:
            document_bytes = base64.b64decode(document, validate=True)
            if len(document_bytes) > MAX_DOCUMENT_SIZE_MB * 1024 * 1024:
                await send_error(websocket, f"Document exceeds maximum size of {MAX_DOCUMENT_SIZE_MB}MB")
                return
        except Exception as e:
            await send_error(websocket, f"Invalid base64 document: {str(e)}")
            return

    # Concurrency control: one active request per user
    if not _user_request_lock.acquire(user_id):
        await send_error(websocket, "Another chat request is already in progress for this user")
        return

    files_manager: Optional[FilesManager] = None
    try:
        # Resolve dependencies
        orchestrator = get_orchestrator()
        continuum_pool = get_continuum_pool()

        # Get the user's continuum
        continuum = continuum_pool.get_or_create()

        # Increment segment turn counter
        segment_turn_number = continuum_pool.repository.increment_segment_turn(
            continuum.id, user_id
        )

        # Get segment ID for file lifecycle tracking
        active_sentinel = continuum_pool.repository.find_active_segment(continuum.id, user_id)
        if not active_sentinel:
            await send_error(websocket, "No active segment found")
            return
        segment_id = active_sentinel.metadata.get('segment_id')
        if not segment_id:
            await send_error(websocket, "Active segment missing segment_id")
            return

        # Process document with Files API support
        processed_doc: Optional[ProcessedDocument] = None
        if document_bytes:
            files_manager = FilesManager(orchestrator.llm_provider.anthropic_client)

            try:
                processed_doc = process_document(
                    document_bytes,
                    document_type,
                    files_manager=files_manager,
                    filename=f"document.{document_type.split('/')[-1]}",
                    segment_id=segment_id
                )
            except ValueError as e:
                await send_error(websocket, f"Document processing failed: {e}")
                return
            except Exception as e:
                await send_error(websocket, f"Document upload failed: {e}")
                return

        # Build content arrays (inference tier for LLM, storage tier for persistence)
        inference_content: Union[str, List[Dict[str, Any]]]
        storage_content: Optional[Union[str, List[Dict[str, Any]]]] = None

        if compressed:
            # Image: Inference tier (1200px) for current LLM call
            inference_content = [
                {"type": "text", "text": msg},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": compressed.inference_media_type,
                        "data": compressed.inference_base64,
                    }
                }
            ]
            # Storage tier (512px WebP) for persistence
            storage_content = [
                {"type": "text", "text": msg},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": compressed.storage_media_type,
                        "data": compressed.storage_base64,
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

        # Stream callback for WebSocket
        # The orchestrator expects a synchronous callback, so we use asyncio.create_task
        import asyncio

        def stream_callback(text_chunk):
            """Send text chunks to WebSocket client."""
            # The orchestrator may pass either a string or a dict with {"type": "text", "content": "..."}
            if isinstance(text_chunk, dict):
                # Already formatted - send as-is
                logger.debug(f"Stream callback received dict: {text_chunk}")
                asyncio.create_task(websocket.send_json(text_chunk))
            else:
                # Plain string - wrap it
                logger.debug(f"Stream callback received string: {repr(text_chunk)[:100]}")
                asyncio.create_task(websocket.send_json({"type": "text", "content": text_chunk}))

        # Create a Unit of Work and process via orchestrator
        uow = continuum_pool.begin_work(continuum)

        from config.config_manager import config as app_config
        continuum, response_text, metadata = orchestrator.process_message(
            continuum,
            inference_content,
            app_config.system_prompt,
            stream=True,
            stream_callback=stream_callback,
            unit_of_work=uow,
            storage_content=storage_content,
            segment_turn_number=segment_turn_number,
        )

        # Commit batched changes
        uow.commit()

        processing_time_ms = int((utc_now() - start_time).total_seconds() * 1000)

        # Send completion message with metadata
        await websocket.send_json({
            "type": "complete",
            "continuum_id": str(continuum.id),
            "metadata": {
                "tools_used": metadata.get("tools_used", []),
                "referenced_memories": metadata.get("referenced_memories", []),
                "surfaced_memories": metadata.get("surfaced_memories", []),
                "processing_time_ms": processing_time_ms,
            }
        })

    except Exception as e:
        logger.error(f"WebSocket message processing error: {e}", exc_info=True)
        await send_error(websocket, "Message processing failed")
    finally:
        _user_request_lock.release(user_id)


@router.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time chat with streaming responses.

    Authentication: Bearer token in Authorization header or auth message.
    Message format: JSON with type field (message, ping, auth).
    """
    await websocket.accept()

    try:
        # Authenticate connection
        user_context = await authenticate_websocket(websocket, websocket.app.state)
        if not user_context:
            return  # Authentication failed, connection closed

        user_id = user_context["user_id"]

        # Message loop
        while True:
            try:
                data = await websocket.receive_json()

                # Validate message has type field
                if "type" not in data:
                    await send_error(websocket, "Message must include 'type' field")
                    continue

                msg_type = data.get("type")

                if msg_type == "ping":
                    # Keepalive
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "message":
                    # Chat message
                    await handle_message(
                        websocket,
                        user_id,
                        data.get("content"),
                        data.get("image"),
                        data.get("image_type"),
                        data.get("document"),
                        data.get("document_type"),
                    )

                elif msg_type == "auth":
                    # Already authenticated, ignore
                    await websocket.send_json({"type": "auth_success"})

                else:
                    # Unknown message type
                    await send_error(websocket, f"Unknown message type: {msg_type}")

            except PydanticValidationError as e:
                await send_error(websocket, f"Invalid message format: {e}")
            except json.JSONDecodeError:
                await send_error(websocket, "Invalid JSON")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user_id if 'user_id' in locals() else 'unknown'}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await send_error(websocket, "Internal server error")
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass
