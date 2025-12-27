"""
Event-driven working memory core.

Coordinates trinkets and system prompt composition through CNS events.
All operations are synchronous - events are published and handled immediately.
"""
import json
import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from clients.valkey_client import get_valkey_client
from utils.user_context import get_current_user_id, get_current_user
from utils.timezone_utils import utc_now, format_utc_iso
from .composer import SystemPromptComposer, ComposerConfig
from .trinkets.base import TRINKET_KEY_PREFIX

if TYPE_CHECKING:
    from cns.integration.event_bus import EventBus

logger = logging.getLogger(__name__)


class WorkingMemory:
    """
    Event-driven working memory coordinator.
    
    This class orchestrates the system prompt composition by:
    1. Managing trinket registrations
    2. Routing UpdateTrinketEvent to specific trinkets
    3. Collecting trinket content via TrinketContentEvent
    4. Composing and publishing the final system prompt
    
    All operations are synchronous to maintain simplicity.
    """
    
    def __init__(self, event_bus: 'EventBus', composer_config: Optional[ComposerConfig] = None):
        """
        Initialize working memory with event bus connection.
        
        Args:
            event_bus: CNS event bus for subscribing and publishing
            composer_config: Optional configuration for the composer
        """
        self.event_bus = event_bus
        self.composer = SystemPromptComposer(composer_config)
        self._trinkets: Dict[str, object] = {}
        self._current_continuum_id: Optional[str] = None
        self._current_user_id: Optional[str] = None

        # Subscribe to core events
        # System prompt composition request
        self.event_bus.subscribe('ComposeSystemPromptEvent', self._handle_compose_prompt)
        
        # Trinket update routing
        self.event_bus.subscribe('UpdateTrinketEvent', self._handle_update_trinket)
        
        # Trinket content collection
        self.event_bus.subscribe('TrinketContentEvent', self._handle_trinket_content)
        
        logger.debug("Subscribed to working memory events")
        logger.info("WorkingMemory initialized")
        
    def register_trinket(self, trinket: object) -> None:
        """
        Register a trinket with working memory.
        
        Args:
            trinket: Trinket instance to register
        """
        trinket_name = trinket.__class__.__name__
        self._trinkets[trinket_name] = trinket
        logger.info(f"Registered trinket: {trinket_name}")
    
    def _handle_compose_prompt(self, event) -> None:
        """
        Handle request to compose system prompt.
        
        This triggers all trinkets to update and then composes the final prompt.
        """
        from cns.core.events import ComposeSystemPromptEvent, UpdateTrinketEvent, SystemPromptComposedEvent
        event: ComposeSystemPromptEvent
        
        # Store context for future trinket updates
        self._current_continuum_id = event.continuum_id
        self._current_user_id = event.user_id

        # Get user's first name for system prompt personalization
        user_data = get_current_user() or {}
        first_name = (user_data.get('first_name') or '').strip()
        user_name = first_name if first_name else "The User"
        logger.debug(f"Using name '{user_name}' for system prompt")

        # Replace "The User" with actual user name (or keep default if no name set)
        personalized_prompt = event.base_prompt.replace("The User", user_name)

        # Set base prompt
        self.composer.set_base_prompt(personalized_prompt)
        
        # Clear previous sections except base
        self.composer.clear_sections(preserve_base=True)
        
        # Request updates from all registered trinkets
        for trinket_name in self._trinkets.keys():
            self.event_bus.publish(UpdateTrinketEvent.create(
                continuum_id=event.continuum_id,
                target_trinket=trinket_name,
                context={'user_id': event.user_id}  # Provide user_id for trinkets that need it
            ))
        
        # After all trinkets have updated (synchronously), compose the prompt
        structured = self.composer.compose()

        # Publish composed prompt event with structured content
        self.event_bus.publish(SystemPromptComposedEvent.create(
            continuum_id=event.continuum_id,
            cached_content=structured['cached_content'],
            non_cached_content=structured['non_cached_content'],
            notification_center=structured['notification_center']
        ))

        logger.info(
            f"Composed system prompt: cached {len(structured['cached_content'])} chars, "
            f"non-cached {len(structured['non_cached_content'])} chars, "
            f"notification center {len(structured['notification_center'])} chars"
        )
    
    def _handle_update_trinket(self, event) -> None:
        """
        Route update request to specific trinket.

        Event handler continues processing even if individual trinkets fail,
        but distinguishes infrastructure failures from logic errors for observability.
        """
        from cns.core.events import UpdateTrinketEvent
        event: UpdateTrinketEvent

        trinket = self._trinkets.get(event.target_trinket)
        if not trinket:
            logger.warning(f"No trinket registered with name: {event.target_trinket}")
            return

        # Call the trinket's update method if it has one
        if hasattr(trinket, 'handle_update_request'):
            try:
                trinket.handle_update_request(event)
                logger.debug(f"Routed update to {event.target_trinket}")
            except Exception as e:
                # Event handler continues - isolate trinket failures
                # Use exception type to distinguish infrastructure from logic errors
                error_type = type(e).__name__
                if 'Database' in error_type or 'Valkey' in error_type or 'Connection' in error_type:
                    logger.error(
                        f"Infrastructure failure in trinket {event.target_trinket}: {e}",
                        exc_info=True,
                        extra={'error_category': 'infrastructure'}
                    )
                else:
                    logger.error(
                        f"Trinket {event.target_trinket} failed: {e}",
                        exc_info=True,
                        extra={'error_category': 'logic'}
                    )
        else:
            logger.warning(f"Trinket {event.target_trinket} has no handle_update_request method")
    
    def _handle_trinket_content(self, event) -> None:
        """
        Handle trinket content updates.

        Trinkets publish their sections which we add to the composer.
        Placement determines routing: "system" or "notification".
        """
        from cns.core.events import TrinketContentEvent
        event: TrinketContentEvent

        self.composer.add_section(
            event.variable_name,
            event.content,
            cache_policy=event.cache_policy,
            placement=event.placement
        )
        logger.debug(f"Received content for '{event.variable_name}' from {event.trinket_name} (placement={event.placement})")
    
    def publish_trinket_update(self, target_trinket: str, context: Optional[Dict] = None) -> None:
        """
        Publish an update request for a specific trinket.
        
        This is used when external events need to trigger specific trinket updates.
        For example, when memories are surfaced, we update ProactiveMemoryTrinket.
        
        Args:
            target_trinket: Name of the trinket class to update
            context: Optional context data for the trinket
        """
        if not self._current_continuum_id or not self._current_user_id:
            logger.warning("No active continuum context for trinket update")
            return
        
        from cns.core.events import UpdateTrinketEvent

        self.event_bus.publish(UpdateTrinketEvent.create(
            continuum_id=self._current_continuum_id,
            target_trinket=target_trinket,
            context=context or {}
        ))
    
    
    def get_active_trinkets(self) -> List[str]:
        """
        Get list of registered trinket names.

        Returns:
            List of trinket class names
        """
        return list(self._trinkets.keys())

    def get_trinket(self, name: str) -> Optional[object]:
        """
        Get a registered trinket by name.

        Used when components need direct access to trinket state (e.g., orchestrator
        accessing ProactiveMemoryTrinket's cached memories for retention evaluation).

        Args:
            name: Trinket class name (e.g., 'ProactiveMemoryTrinket')

        Returns:
            Trinket instance or None if not found
        """
        return self._trinkets.get(name)

    def get_trinket_state(self, section_name: str) -> Optional[Dict[str, Any]]:
        """
        Get cached state of a single trinket from Valkey.

        Args:
            section_name: The trinket's section name (e.g., 'user_information')

        Returns:
            Dict with section_name, content, cache_policy, last_updated
            or None if not found
        """
        user_id = get_current_user_id()
        hash_key = f"{TRINKET_KEY_PREFIX}:{user_id}"

        valkey = get_valkey_client()
        json_value = valkey.hget_with_retry(hash_key, section_name)

        if json_value is None:
            return None

        try:
            data = json.loads(json_value)
            return {
                "section_name": section_name,
                "content": data.get("content", ""),
                "cache_policy": data.get("cache_policy", False),
                "last_updated": data.get("updated_at")
            }
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in trinket cache for section: {section_name}")
            return None

    def get_all_trinket_states(self) -> Dict[str, Any]:
        """
        Get cached state of all trinkets from Valkey.

        Queries each section individually and compiles results.

        Returns:
            Dict with trinkets list and metadata
        """
        user_id = get_current_user_id()
        hash_key = f"{TRINKET_KEY_PREFIX}:{user_id}"

        valkey = get_valkey_client()
        section_names = valkey._client.hkeys(hash_key)

        trinkets = []
        for section_name in section_names:
            state = self.get_trinket_state(section_name)
            if state:
                trinkets.append(state)

        return {
            "trinkets": trinkets,
            "meta": {
                "trinket_count": len(trinkets),
                "loaded_at": format_utc_iso(utc_now())
            }
        }