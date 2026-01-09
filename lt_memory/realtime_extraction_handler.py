"""
Real-time memory extraction handler.

Subscribes to TurnCompletedEvent and extracts memories from recent conversation
turns immediately, making them searchable without waiting for segment collapse.

This complements batch segment extraction by providing short-term memory recall
for discussions that haven't yet collapsed into long-term storage.
"""
import logging
import threading
from typing import List, Optional

from cns.core.events import TurnCompletedEvent
from cns.core.message import Message
from cns.integration.event_bus import EventBus
from lt_memory.processing.extraction_engine import ExtractionEngine
from lt_memory.processing.memory_processor import MemoryProcessor
from config.config import ExtractionConfig
from utils.user_context import set_current_user_id, get_current_user_id

logger = logging.getLogger(__name__)


class RealtimeExtractionHandler:
    """
    Handles real-time memory extraction during active conversation.

    Subscribes to TurnCompletedEvent and periodically extracts memories from
    recent turns, making them immediately searchable without waiting for
    segment collapse (which takes 60+ minutes).

    Design:
    - Triggers every N turns (configurable)
    - Extracts from last M turns only (to avoid re-processing)
    - Uses sync extraction for immediate availability
    - Tracks extraction state on active segment sentinel
    """

    def __init__(
        self,
        event_bus: EventBus,
        extraction_engine: ExtractionEngine,
        memory_processor: MemoryProcessor,
        continuum_repo,
        llm_provider,
        config: ExtractionConfig,
        extraction_interval: int = 3,  # Extract every N turns
        window_size: int = 3  # Extract from last M turns
    ):
        """
        Initialize real-time extraction handler.

        Args:
            event_bus: Event bus for subscribing to turn events
            extraction_engine: Engine for building extraction payloads
            memory_processor: Processor for handling extraction results
            continuum_repo: Repository for loading messages and updating sentinels
            llm_provider: LLM provider for making extraction calls
            config: Extraction configuration
            extraction_interval: Trigger extraction every N turns (default: 3)
            window_size: Extract from last M turns only (default: 3)
        """
        self.event_bus = event_bus
        self.extraction_engine = extraction_engine
        self.memory_processor = memory_processor
        self.continuum_repo = continuum_repo
        self.llm_provider = llm_provider
        self.config = config
        self.extraction_interval = extraction_interval
        self.window_size = window_size

        # Subscribe to turn completed events
        self.event_bus.subscribe('TurnCompletedEvent', self._handle_turn_completed)
        logger.info(
            f"RealtimeExtractionHandler initialized: "
            f"interval={extraction_interval} turns, window={window_size} turns"
        )

    def _handle_turn_completed(self, event: TurnCompletedEvent) -> None:
        """
        Handle turn completed event and conditionally trigger extraction.

        Args:
            event: TurnCompletedEvent with continuum and turn info
        """
        try:
            # Set user context for database operations
            continuum = event.continuum
            set_current_user_id(continuum.user_id)

            # Check if we should extract this turn
            if not self._should_extract(event):
                return

            logger.info(
                f"Real-time extraction triggered: continuum={continuum.id}, "
                f"turn={event.turn_number}, segment_turn={event.segment_turn_number}"
            )

            # Get recent messages from continuum cache (last N turns = 2N messages)
            messages = self._get_recent_messages(continuum, self.window_size)

            if not messages:
                logger.warning("No messages to extract")
                return

            # Run extraction in background thread to avoid blocking response
            # Copy context since it doesn't propagate to new threads
            import contextvars
            ctx = contextvars.copy_context()

            def run_extraction():
                """Run extraction in background with copied context."""
                ctx.run(self._extract_and_store_with_metadata,
                       continuum.user_id,
                       str(continuum.id),
                       messages,
                       event.turn_number)

            thread = threading.Thread(
                target=run_extraction,
                daemon=True,
                name=f"realtime-extraction-{continuum.id}"
            )
            thread.start()

            logger.debug(f"Started background extraction thread for turn {event.turn_number}")

        except Exception as e:
            # Log but don't raise - extraction failures shouldn't break conversation flow
            logger.error(
                f"Real-time extraction failed for continuum {event.continuum_id}, "
                f"turn {event.turn_number}: {e}",
                exc_info=True
            )

    def _should_extract(self, event: TurnCompletedEvent) -> bool:
        """
        Determine if extraction should run for this turn.

        Extraction runs every N turns within the segment (starting at turn N).

        Args:
            event: TurnCompletedEvent

        Returns:
            True if extraction should run
        """
        # Extract every N turns (e.g., turns 3, 6, 9, ...)
        should_run = event.segment_turn_number > 0 and event.segment_turn_number % self.extraction_interval == 0

        if should_run:
            logger.debug(
                f"Extraction checkpoint: segment_turn={event.segment_turn_number}, "
                f"interval={self.extraction_interval}"
            )

        return should_run

    def _get_recent_messages(self, continuum, window_size: int) -> List[Message]:
        """
        Get last N turns (2N messages) from continuum message cache.

        Filters out system notifications and segment boundaries.

        Args:
            continuum: Continuum object with message cache
            window_size: Number of turns to extract

        Returns:
            List of recent user/assistant messages
        """
        # Get all messages from cache
        all_messages = continuum.messages

        # Filter to user/assistant only (exclude system notifications, boundaries)
        conversation_messages = [
            msg for msg in all_messages
            if msg.role in ('user', 'assistant')
            and not msg.metadata.get('is_segment_boundary')
            and not msg.metadata.get('system_notification')
        ]

        # Take last 2*window_size messages (each turn = user + assistant)
        recent_messages = conversation_messages[-(window_size * 2):]

        logger.debug(
            f"Extracted {len(recent_messages)} recent messages "
            f"(from {len(conversation_messages)} total conversation messages)"
        )

        return recent_messages

    def _extract_and_store_with_metadata(
        self,
        user_id: str,
        continuum_id: str,
        messages: List[Message],
        turn_number: int
    ) -> None:
        """
        Extract memories and update metadata (runs in background thread).

        Args:
            user_id: User ID
            continuum_id: Continuum UUID
            messages: Messages to extract from
            turn_number: Current turn number
        """
        try:
            # Set user context for this thread
            set_current_user_id(user_id)

            # Extract and store memories
            self._extract_and_store(user_id, continuum_id, messages, turn_number)

            # Update segment metadata
            self._update_extraction_metadata(continuum_id, user_id, turn_number)

        except Exception as e:
            logger.error(
                f"Background extraction failed for continuum {continuum_id}, "
                f"turn {turn_number}: {e}",
                exc_info=True
            )

    def _extract_and_store(
        self,
        user_id: str,
        continuum_id: str,
        messages: List[Message],
        turn_number: int
    ) -> None:
        """
        Extract memories from messages and store immediately.

        Uses sync extraction for immediate availability (no batch API delay).

        Args:
            user_id: User ID
            continuum_id: Continuum UUID
            messages: Messages to extract from
            turn_number: Current turn number (for logging)

        Raises:
            Exception: If extraction or storage fails
        """
        from lt_memory.models import ProcessingChunk
        from utils.timezone_utils import utc_now

        # Create ProcessingChunk from messages
        chunk = ProcessingChunk(
            messages=messages,
            temporal_start=messages[0].created_at if messages else utc_now(),
            temporal_end=messages[-1].created_at if messages else utc_now(),
            chunk_index=0,
            memory_context_snapshot={}  # Will be populated by engine
        )

        # Build extraction payload
        payload = self.extraction_engine.build_extraction_payload(
            chunk=chunk,
            for_batch=False  # Immediate execution, not batch
        )

        # Execute extraction synchronously (uses LLM directly instead of batch API)
        try:
            # Call LLM for extraction
            # When for_batch=False, payload uses system_prompt + user_prompt format
            response = self.llm_provider.generate_response(
                messages=[
                    {"role": "system", "content": payload.system_prompt},
                    {"role": "user", "content": payload.user_prompt}
                ],
                model_preference=self.config.extraction_model,
                thinking_enabled=self.config.extraction_thinking_enabled,
                thinking_budget=self.config.extraction_thinking_budget,
                temperature=self.config.extraction_temperature,
                max_tokens=self.config.max_extraction_tokens
            )

            response_text = self.llm_provider.extract_text_content(response)

            # Process extraction response (dedup, validation, storage)
            # Note: User context must be set via contextvars before calling this
            result = self.memory_processor.process_extraction_response(
                response_text=response_text,
                short_to_uuid=payload.short_to_uuid,
                memory_context=payload.memory_context
            )

            logger.info(
                f"Real-time extraction complete: {len(result.memories)} memories extracted, "
                f"{len(result.linking_pairs)} linking pairs identified"
            )

        except Exception as e:
            logger.error(f"Real-time extraction failed: {e}", exc_info=True)
            raise

    def _update_extraction_metadata(
        self,
        continuum_id: str,
        user_id: str,
        turn_number: int
    ) -> None:
        """
        Update active segment sentinel with last real-time extraction turn.

        This helps avoid re-processing on subsequent extractions.

        Args:
            continuum_id: Continuum UUID
            user_id: User ID
            turn_number: Turn number when extraction occurred
        """
        try:
            # Find active segment sentinel
            sentinel = self.continuum_repo.find_active_segment(continuum_id, user_id)

            if not sentinel:
                logger.warning(f"No active segment found for continuum {continuum_id}")
                return

            # Update metadata
            sentinel.metadata['last_realtime_extraction_turn'] = turn_number

            # Persist
            self.continuum_repo.save_message(sentinel, continuum_id, user_id)

            logger.debug(f"Updated segment metadata: last_realtime_extraction_turn={turn_number}")

        except Exception as e:
            # Log but don't raise - metadata update failure shouldn't break extraction
            logger.warning(f"Failed to update segment extraction metadata: {e}")
