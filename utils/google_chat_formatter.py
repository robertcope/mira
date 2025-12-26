"""
Google Chat response formatter.

Converts MIRA's internal response format to Google Chat Card messages.
Reference: https://developers.google.com/chat/api/guides/message-formats/cards
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def format_response_as_card(
    response_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    include_metadata: bool = False
) -> Dict[str, Any]:
    """
    Format MIRA response as Google Chat Card message.

    Args:
        response_text: The assistant's response text
        metadata: Optional metadata (tools_used, memories, etc.)
        include_metadata: Whether to append metadata footer to card

    Returns:
        Google Chat message payload with cardsV2 format
    """
    # Build main text section
    widgets: List[Dict[str, Any]] = [
        {
            "textParagraph": {
                "text": response_text
            }
        }
    ]

    # Add metadata footer if requested and available
    if include_metadata and metadata:
        footer_lines = []

        tools_used = metadata.get("tools_used", [])
        if tools_used:
            footer_lines.append(f"🔧 Tools: {', '.join(tools_used)}")

        processing_time = metadata.get("processing_time_ms")
        if processing_time:
            footer_lines.append(f"⏱️ {processing_time}ms")

        memory_count = len(metadata.get("referenced_memories", []))
        if memory_count > 0:
            footer_lines.append(f"🧠 {memory_count} memories referenced")

        if footer_lines:
            widgets.append({
                "textParagraph": {
                    "text": "<font color=\"#666666\">" + " • ".join(footer_lines) + "</font>"
                }
            })

    # Build Card structure for Google Chat Apps
    # Must match google.apps.card.v1.Card schema exactly
    card = {
        "sections": [{
            "widgets": widgets
        }]
    }

    # Wrap in cardsV2 array for the message response
    return {
        "cardsV2": [{
            "cardId": "mira-response",
            "card": card
        }]
    }


def format_error_as_card(error_code: str, error_message: str) -> Dict[str, Any]:
    """
    Format error response as Google Chat Card message.

    Args:
        error_code: Error code (e.g., VALIDATION_ERROR)
        error_message: Human-readable error message

    Returns:
        Google Chat message payload with error card
    """
    return {
        "cardsV2": [{
            "cardId": "mira-error",
            "card": {
                "sections": [{
                    "widgets": [{
                        "textParagraph": {
                            "text": f"<font color=\"#d93025\">❌ {error_message}</font>"
                        }
                    }]
                }]
            }
        }]
    }


def format_simple_text(text: str) -> Dict[str, Any]:
    """
    Format simple text message (for system messages, not MIRA responses).

    Args:
        text: Text to send

    Returns:
        Google Chat text message payload
    """
    return {
        "text": text
    }
