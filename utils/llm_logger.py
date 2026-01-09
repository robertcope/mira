"""
LLM Request/Response Logging Utility

Provides structured logging of all LLM interactions to disk for debugging,
auditing, and analysis. Creates timestamped JSON files organized by date.

Key features:
- Request/response correlation via unique IDs
- Separate files per request for easy analysis
- Optional user-scoped organization
- Captures streaming events and tool calls
- Timezone-aware timestamps using utils/timezone_utils
- Automatic directory creation and cleanup
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from utils.timezone_utils import utc_now, format_utc_iso

logger = logging.getLogger(__name__)


class LLMLogger:
    """
    Logger for LLM requests and responses.

    Writes structured JSON logs to disk organized by date and optionally by user.
    Each request gets a unique correlation ID that links request/response pairs.
    """

    def __init__(
        self,
        base_dir: str = "data/llm_logs",
        enabled: bool = True,
        user_scoped: bool = False
    ):
        """
        Initialize LLM logger.

        Args:
            base_dir: Base directory for log files (default: data/llm_logs)
            enabled: Whether logging is enabled (default: True)
            user_scoped: Whether to organize logs by user_id (default: False)
        """
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.user_scoped = user_scoped

        if self.enabled:
            self._ensure_base_dir()

    def _ensure_base_dir(self) -> None:
        """Create base log directory if it doesn't exist."""
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"LLM log directory ready: {self.base_dir}")
        except Exception as e:
            logger.error(f"Failed to create LLM log directory {self.base_dir}: {e}")
            self.enabled = False

    def _get_log_dir(self, user_id: Optional[str] = None) -> Path:
        """
        Get the log directory for the current date (and optionally user).

        Structure:
        - Non-user-scoped: data/llm_logs/2026-01-08/
        - User-scoped: data/llm_logs/2026-01-08/user_abc123/

        Args:
            user_id: Optional user ID for scoping

        Returns:
            Path object for the log directory
        """
        now = utc_now()
        date_str = now.strftime("%Y-%m-%d")

        if self.user_scoped and user_id:
            log_dir = self.base_dir / date_str / f"user_{user_id}"
        else:
            log_dir = self.base_dir / date_str

        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _generate_correlation_id(self) -> str:
        """Generate a unique correlation ID for request/response pairing."""
        return str(uuid4())

    def _write_log_file(self, log_dir: Path, filename: str, data: Dict[str, Any]) -> None:
        """
        Write log data to a JSON file.

        Args:
            log_dir: Directory to write to
            filename: Name of the log file
            data: Data to write as JSON
        """
        try:
            file_path = log_dir / filename
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(f"Wrote LLM log: {file_path}")
        except Exception as e:
            logger.error(f"Failed to write LLM log {filename}: {e}")

    def log_request(
        self,
        provider: str,
        model: str,
        system_prompt: Any,  # Can be str or List[Dict] with cache_control
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        user_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        correlation_id: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Log an LLM request.

        Args:
            provider: Provider name (anthropic, groq, openrouter, etc.)
            model: Model identifier
            system_prompt: System prompt text
            messages: Message history
            tools: Tool definitions (optional)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            user_id: User ID for scoping (optional)
            endpoint: API endpoint URL (optional)
            correlation_id: Correlation ID (generated if not provided)
            **kwargs: Additional metadata to include

        Returns:
            Correlation ID for linking with response
        """
        if not self.enabled:
            logger.warning(f"LLM logging disabled, skipping log_request for {provider}/{model}")
            return correlation_id or self._generate_correlation_id()

        logger.info(f"Logging LLM request: {provider}/{model}")
        try:
            # Generate correlation ID if not provided
            if not correlation_id:
                correlation_id = self._generate_correlation_id()

            # Get log directory
            log_dir = self._get_log_dir(user_id)

            # Create timestamp
            timestamp = utc_now()
            timestamp_str = format_utc_iso(timestamp, include_ms=True)

            # Build request log data
            request_data = {
                "correlation_id": correlation_id,
                "timestamp": timestamp_str,
                "type": "request",
                "provider": provider,
                "model": model,
                "endpoint": endpoint,
                "user_id": user_id,
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "temperature": temperature,
                **kwargs
            }

            # Generate filename with timestamp and correlation ID
            filename = f"{timestamp.strftime('%H%M%S')}_{correlation_id[:8]}_request.json"

            # Write log file
            self._write_log_file(log_dir, filename, request_data)

            return correlation_id

        except Exception as e:
            logger.error(f"Failed to log LLM request: {e}")
            return correlation_id or self._generate_correlation_id()

    def log_response(
        self,
        correlation_id: str,
        response_data: Dict[str, Any],
        user_id: Optional[str] = None,
        error: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Log an LLM response.

        Args:
            correlation_id: Correlation ID from the request
            response_data: Response data from the LLM
            user_id: User ID for scoping (optional)
            error: Error message if request failed (optional)
            **kwargs: Additional metadata to include
        """
        if not self.enabled:
            return

        try:
            # Get log directory
            log_dir = self._get_log_dir(user_id)

            # Create timestamp
            timestamp = utc_now()
            timestamp_str = format_utc_iso(timestamp, include_ms=True)

            # Build response log data
            log_data = {
                "correlation_id": correlation_id,
                "timestamp": timestamp_str,
                "type": "response",
                "user_id": user_id,
                "error": error,
                "response": response_data,
                **kwargs
            }

            # Generate filename with timestamp and correlation ID
            filename = f"{timestamp.strftime('%H%M%S')}_{correlation_id[:8]}_response.json"

            # Write log file
            self._write_log_file(log_dir, filename, log_data)

        except Exception as e:
            logger.error(f"Failed to log LLM response: {e}")

    def log_streaming_event(
        self,
        correlation_id: str,
        event_type: str,
        event_data: Dict[str, Any],
        user_id: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Log a streaming event.

        Args:
            correlation_id: Correlation ID from the request
            event_type: Type of streaming event (text_delta, tool_use, etc.)
            event_data: Event data
            user_id: User ID for scoping (optional)
            **kwargs: Additional metadata to include
        """
        if not self.enabled:
            return

        try:
            # Get log directory
            log_dir = self._get_log_dir(user_id)

            # Create timestamp
            timestamp = utc_now()
            timestamp_str = format_utc_iso(timestamp, include_ms=True)

            # Build event log data
            log_data = {
                "correlation_id": correlation_id,
                "timestamp": timestamp_str,
                "type": "streaming_event",
                "event_type": event_type,
                "user_id": user_id,
                "event": event_data,
                **kwargs
            }

            # Append to streaming log file (one per correlation ID)
            filename = f"{correlation_id[:8]}_streaming.jsonl"
            file_path = log_dir / filename

            # Append as JSON lines (one event per line)
            with open(file_path, 'a') as f:
                f.write(json.dumps(log_data, default=str) + '\n')

        except Exception as e:
            logger.error(f"Failed to log streaming event: {e}")


# Global logger instance (configured from settings)
_global_logger: Optional[LLMLogger] = None


def get_llm_logger() -> LLMLogger:
    """
    Get the global LLM logger instance.

    Creates a default logger if one hasn't been configured yet.
    Configuration is read from environment variables via llm_logger_config.

    Returns:
        Global LLMLogger instance
    """
    global _global_logger
    if _global_logger is None:
        # Import here to avoid circular dependencies
        from utils.llm_logger_config import get_llm_logger_config
        config = get_llm_logger_config()
        _global_logger = LLMLogger(
            base_dir=config['base_dir'],
            enabled=config['enabled'],
            user_scoped=config['user_scoped']
        )
    return _global_logger


def configure_llm_logger(
    base_dir: str = "data/llm_logs",
    enabled: bool = True,
    user_scoped: bool = False
) -> LLMLogger:
    """
    Configure the global LLM logger.

    Args:
        base_dir: Base directory for log files
        enabled: Whether logging is enabled
        user_scoped: Whether to organize logs by user_id

    Returns:
        Configured LLMLogger instance
    """
    global _global_logger
    _global_logger = LLMLogger(
        base_dir=base_dir,
        enabled=enabled,
        user_scoped=user_scoped
    )
    return _global_logger
