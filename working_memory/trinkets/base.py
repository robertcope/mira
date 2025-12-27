"""
Event-aware base trinket class.

Provides common functionality for all trinkets to participate in the
event-driven working memory system. Persists content to Valkey for
API access and monitoring.
"""
import json
import logging
from enum import Enum
from typing import Dict, Any, TYPE_CHECKING

from clients.valkey_client import get_valkey_client
from utils.user_context import get_current_user_id
from utils.timezone_utils import utc_now, format_utc_iso

if TYPE_CHECKING:
    from cns.integration.event_bus import EventBus
    from working_memory.core import WorkingMemory

logger = logging.getLogger(__name__)

# Valkey key prefix for trinket content storage
TRINKET_KEY_PREFIX = "trinkets"


class TrinketPlacement(Enum):
    """Where trinket content appears in the context window."""
    SYSTEM = "system"                    # Goes in system prompt (cached)
    NOTIFICATION_CENTER = "notification" # Goes in notification center (slides forward)


# Trinkets that go in the notification center (all others default to SYSTEM)
_NOTIFICATION_CENTER_TRINKETS = frozenset({
    'TimeManager',
    'ManifestTrinket',
    'ReminderManager',
    'GetContextTrinket',
    'ProactiveMemoryTrinket',
})


class EventAwareTrinket:
    """
    Base class for event-driven trinkets.

    Trinkets inherit from this class to:
    1. Receive update requests via UpdateTrinketEvent
    2. Generate content when requested
    3. Publish their content via TrinketContentEvent
    """

    # Cache policy for this trinket's content
    # True = content should be cached (static content like tool guidance)
    # False = content changes frequently, don't cache (default)
    cache_policy: bool = False

    @property
    def placement(self) -> TrinketPlacement:
        """Placement is determined by _NOTIFICATION_CENTER_TRINKETS registry."""
        if self.__class__.__name__ in _NOTIFICATION_CENTER_TRINKETS:
            return TrinketPlacement.NOTIFICATION_CENTER
        return TrinketPlacement.SYSTEM

    def __init__(self, event_bus: 'EventBus', working_memory: 'WorkingMemory'):
        """
        Initialize the trinket with event bus connection.

        Args:
            event_bus: CNS event bus for publishing content
            working_memory: Working memory instance for registration
        """
        self.event_bus = event_bus
        self.working_memory = working_memory
        self._variable_name: str = self._get_variable_name()

        # Register with working memory
        self.working_memory.register_trinket(self)

        logger.info(f"{self.__class__.__name__} initialized and registered")
    
    def _get_variable_name(self) -> str:
        """
        Get the variable name this trinket publishes.
        
        Subclasses should override this to specify their section name.
        
        Returns:
            Variable name for system prompt composition
        """
        # Default implementation - subclasses should override
        return self.__class__.__name__.lower() + "_section"
    
    def handle_update_request(self, event) -> None:
        """
        Handle an update request from working memory.

        Generates content, persists to Valkey, and publishes it. Infrastructure
        failures propagate to the event handler in core.py for proper isolation.

        Args:
            event: UpdateTrinketEvent with context
        """
        from cns.core.events import UpdateTrinketEvent, TrinketContentEvent
        event: UpdateTrinketEvent

        # Generate content - let infrastructure failures propagate
        content = self.generate_content(event.context)

        # Publish and persist if we have content
        if content and content.strip():
            # Persist to Valkey for API access
            self._persist_to_valkey(content)

            self.event_bus.publish(TrinketContentEvent.create(
                continuum_id=event.continuum_id,
                variable_name=self._variable_name,
                content=content,
                trinket_name=self.__class__.__name__,
                cache_policy=self.cache_policy,
                placement=self.placement.value
            ))
            logger.debug(f"{self.__class__.__name__} published content ({len(content)} chars, placement={self.placement.value})")

    def _persist_to_valkey(self, content: str) -> None:
        """
        Persist trinket content to Valkey for API access.

        Stores content in a user-scoped hash with metadata for monitoring.
        Uses hset_with_retry for transient failure handling.

        Args:
            content: Generated trinket content
        """
        user_id = get_current_user_id()
        hash_key = f"{TRINKET_KEY_PREFIX}:{user_id}"

        value = json.dumps({
            "content": content,
            "cache_policy": self.cache_policy,
            "updated_at": format_utc_iso(utc_now())
        })

        valkey = get_valkey_client()
        valkey.hset_with_retry(hash_key, self._variable_name, value)
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate content for this trinket.
        
        Subclasses must implement this method to generate their
        specific content based on the provided context.
        
        Args:
            context: Context from UpdateTrinketEvent
            
        Returns:
            Generated content string or empty string if no content
        """
        raise NotImplementedError("Subclasses must implement generate_content()")