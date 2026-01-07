"""
Manifest query service for retrieving conversation segment data.

Provides segment data for manifest display with Valkey caching
and event-driven invalidation. Formatting is handled by ManifestTrinket.
"""
import logging
from typing import List, Dict, Any, Optional

from clients.valkey_client import get_valkey_client
from cns.core.events import ManifestUpdatedEvent
from cns.integration.event_bus import EventBus
from cns.infrastructure.continuum_repository import get_continuum_repository
from config import config

logger = logging.getLogger(__name__)

# Module-level singleton instance
_manifest_service_instance = None


def get_manifest_query_service(event_bus: Optional[EventBus] = None) -> 'ManifestQueryService':
    """
    Get or create singleton ManifestQueryService instance.

    Args:
        event_bus: Event bus for cache invalidation (only needed on first call)

    Returns:
        Singleton ManifestQueryService instance
    """
    global _manifest_service_instance
    if _manifest_service_instance is None:
        logger.info("Creating singleton ManifestQueryService instance")
        _manifest_service_instance = ManifestQueryService(event_bus)
    return _manifest_service_instance


class ManifestQueryService:
    """
    Retrieves conversation segment data for manifest display.

    Queries segment sentinels from messages table with Valkey caching.
    Formatting is delegated to ManifestTrinket following the trinket pattern.
    """

    def __init__(self, event_bus: Optional[EventBus] = None, continuum_repository=None):
        """
        Initialize manifest query service.

        Args:
            event_bus: Event bus for subscribing to ManifestUpdatedEvent
            continuum_repository: Continuum repository (uses singleton if not provided)
        """
        self.valkey = get_valkey_client()
        self.cache_ttl = config.system.manifest_cache_ttl
        self.continuum_repository = continuum_repository or get_continuum_repository()

        # Subscribe to manifest update events for cache invalidation
        if event_bus:
            event_bus.subscribe('ManifestUpdatedEvent', self._handle_manifest_updated)
            logger.info("ManifestQueryService subscribed to ManifestUpdatedEvent")

    def _handle_manifest_updated(self, event: ManifestUpdatedEvent) -> None:
        """
        Handle manifest update event by invalidating cache.

        Args:
            event: ManifestUpdatedEvent with user_id
        """
        cache_key = self._get_cache_key(event.user_id)
        try:
            self.valkey.delete(cache_key)
            thread_desc = self._get_thread_desc()
            logger.debug(f"Invalidated manifest cache for user {event.user_id}, {thread_desc}")
        except Exception as e:
            logger.warning(f"Failed to invalidate manifest cache: {e}")

    def _get_cache_key(self, user_id: str) -> str:
        """
        Generate cache key for manifest, scoped to thread if available.

        Args:
            user_id: User ID

        Returns:
            Cache key string
        """
        from utils.user_context import get_current_user
        user_context = get_current_user()
        thread_context = user_context.get('google_chat_thread_key') if user_context else None

        if thread_context:
            return f"manifest_segments:{user_id}:{thread_context}"
        else:
            return f"manifest_segments:{user_id}"

    def _get_thread_desc(self) -> str:
        """Get thread description for logging."""
        from utils.user_context import get_current_user
        user_context = get_current_user()
        thread_context = user_context.get('google_chat_thread_key') if user_context else None
        return f"thread '{thread_context}'" if thread_context else "default"

    def get_segments(self, user_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get segment data for manifest display.

        Returns raw segment dictionaries for the trinket to format.
        Results are cached in Valkey for performance.

        Args:
            user_id: User ID for query
            limit: Maximum number of segments (uses config.system.manifest_depth if None)

        Returns:
            List of segment dictionaries with keys:
            - id: Segment ID
            - display_title: Title for display
            - synopsis: Full synopsis content
            - status: 'active' or 'collapsed'
            - start_time: ISO format start time
            - end_time: ISO format end time
            - created_at: ISO format creation time
        """
        # Use config default if not specified
        if limit is None:
            limit = config.system.manifest_depth

        # Try cache first
        cache_key = self._get_cache_key(user_id)
        try:
            import json
            cached = self.valkey.get(cache_key)
            if cached:
                thread_desc = self._get_thread_desc()
                logger.debug(f"Manifest cache hit for user {user_id}, {thread_desc}")
                cached_str = cached.decode('utf-8') if isinstance(cached, bytes) else cached
                return json.loads(cached_str)
        except Exception as e:
            logger.debug(f"Manifest cache miss: {e}")

        # Query segments from database
        segments = self._query_segments(user_id, limit)

        # Cache the result
        if segments:
            try:
                import json
                self.valkey.setex(cache_key, self.cache_ttl, json.dumps(segments))
                thread_desc = self._get_thread_desc()
                logger.debug(f"Cached manifest segments for user {user_id}, {thread_desc} (TTL={self.cache_ttl}s)")
            except Exception as e:
                logger.warning(f"Failed to cache manifest segments: {e}")

        return segments

    def _query_segments(self, user_id: str, limit: int) -> List[Dict[str, Any]]:
        """
        Query segment boundary sentinels from messages table.

        Args:
            user_id: User ID for RLS
            limit: Maximum segments to return

        Returns:
            List of segment dictionaries with metadata

        Raises:
            RuntimeError: If database query fails
        """
        segment_messages = self.continuum_repository.find_all_segments(user_id, limit)

        segments = []
        for msg in segment_messages:
            metadata = msg.metadata

            # Use display_title from metadata for manifest tree, fallback to content for in-progress segments
            display_title = metadata.get('display_title')
            if not display_title:
                # Fallback for active segments or segments without display_title
                display_title = msg.content if msg.content else '[Segment in progress]'
                # Truncate if using full content as fallback
                if len(display_title) > 50:
                    display_title = display_title[:47] + "..."

            segments.append({
                'id': str(msg.id),
                'display_title': display_title,
                'synopsis': msg.content,  # Full synopsis available if needed
                'status': metadata.get('status', 'unknown'),
                'start_time': metadata.get('segment_start_time'),
                'end_time': metadata.get('segment_end_time'),
                'created_at': msg.created_at.isoformat() if msg.created_at else None
            })

        # Reverse to chronological order (oldest first)
        segments.reverse()

        logger.debug(f"Queried {len(segments)} segments for user {user_id}")
        return segments
