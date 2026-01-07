"""
Valkey-based message cache for continuum messages.

Provides distributed caching with event-driven invalidation via segment timeout.
"""
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from cns.core.message import Message
from clients.valkey_client import ValkeyClient
from config import config
from utils.user_context import get_current_user_id
from utils.timezone_utils import parse_utc_time_string

logger = logging.getLogger(__name__)


class ValkeyMessageCache:
    """
    Manages continuum message cache in Valkey.

    Cache invalidation is event-driven (triggered by segment timeout),
    not TTL-based. Cache miss indicates new session requiring boundary marker.
    """

    def __init__(self, valkey_client: Optional[ValkeyClient] = None):
        """
        Initialize Valkey continuum cache.

        Cache invalidation is event-driven via segment timeout, not TTL-based.

        Args:
            valkey_client: Valkey client instance (creates one if not provided)
        """
        # Get or create Valkey client
        if valkey_client:
            self.valkey = valkey_client
        else:
            from clients.valkey_client import get_valkey_client
            self.valkey = get_valkey_client()

        self.key_prefix = "continuum"

        logger.info("ValkeyMessageCache initialized (event-driven invalidation)")
    
    def _get_key(self, user_id: str, thread_context: Optional[str] = None) -> str:
        """
        Generate cache key for continuum messages.

        Args:
            user_id: User ID
            thread_context: Optional thread identifier (e.g., Google Chat thread key)

        Returns:
            Cache key string: continuum:user_id:thread_context:messages or continuum:user_id:messages
        """
        if thread_context:
            # Thread-scoped: continuum:user_id:thread_context:messages
            return f"{self.key_prefix}:{user_id}:{thread_context}:messages"
        else:
            # Default (non-threaded): continuum:user_id:messages
            return f"{self.key_prefix}:{user_id}:messages"

    def _serialize_messages(self, messages: List[Message]) -> str:
        """
        Serialize messages to JSON for storage.
        
        Args:
            messages: List of Message objects
            
        Returns:
            JSON string representation
        """
        serialized = []
        for msg in messages:
            msg_dict = {
                'id': str(msg.id),
                'content': msg.content,
                'role': msg.role,
                'created_at': msg.created_at.isoformat() if msg.created_at else None,
                'metadata': msg.metadata
            }
            serialized.append(msg_dict)
        
        return json.dumps(serialized)
    
    def _deserialize_messages(self, data: str) -> List[Message]:
        """
        Deserialize JSON data back to Message objects.
        
        Args:
            data: JSON string from Valkey
            
        Returns:
            List of Message objects
        """
        messages = []
        serialized = json.loads(data)
        
        for msg_dict in serialized:
            # Parse created_at if present
            created_at = None
            if msg_dict.get('created_at'):
                created_at = parse_utc_time_string(msg_dict['created_at'])
            
            message = Message(
                id=msg_dict['id'],
                content=msg_dict['content'],
                role=msg_dict['role'],
                created_at=created_at,
                metadata=msg_dict.get('metadata', {})
            )
            messages.append(message)
        
        return messages
    
    def get_continuum(self, thread_context: Optional[str] = None) -> Optional[List[Message]]:
        """
        Get continuum messages from Valkey cache, optionally scoped to thread.

        Args:
            thread_context: Optional thread identifier (e.g., Google Chat thread key)

        Returns:
            List of cached messages, or None if cache miss

        Cache miss indicates a new session (invalidated by segment timeout).

        Requires: Active user context (set via set_current_user_id during authentication)

        Raises:
            ValkeyError: If Valkey infrastructure is unavailable
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()
        key = self._get_key(user_id, thread_context)
        data = self.valkey.get(key)

        if data:
            thread_desc = f"thread '{thread_context}'" if thread_context else "default"
            logger.debug(f"Found cached continuum for user {user_id}, {thread_desc}")
            return self._deserialize_messages(data)
        else:
            thread_desc = f"thread '{thread_context}'" if thread_context else "default"
            logger.debug(f"No cached continuum found for user {user_id}, {thread_desc}")
            return None

    def set_continuum(
        self,
        messages: List[Message],
        thread_context: Optional[str] = None
    ) -> None:
        """
        Store continuum messages in Valkey, optionally scoped to thread.

        Cache remains until explicitly invalidated by segment timeout handler.

        Args:
            messages: List of messages to cache
            thread_context: Optional thread identifier (e.g., Google Chat thread key)

        Requires: Active user context (set via set_current_user_id during authentication)

        Raises:
            ValkeyError: If Valkey infrastructure is unavailable
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()
        key = self._get_key(user_id, thread_context)
        data = self._serialize_messages(messages)

        # Set without expiration - invalidation is event-driven
        self.valkey.set(key, data)

        thread_desc = f"thread '{thread_context}'" if thread_context else "default"
        logger.debug(f"Cached continuum for user {user_id}, {thread_desc}")

    def invalidate_continuum(self, thread_context: Optional[str] = None) -> bool:
        """
        Invalidate continuum cache entry, optionally scoped to thread.

        Args:
            thread_context: Optional thread identifier (e.g., Google Chat thread key)

        Requires: Active user context (set via set_current_user_id during authentication)

        Returns:
            True if cache entry was invalidated, False if entry didn't exist

        Raises:
            ValkeyError: If Valkey infrastructure is unavailable
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()
        messages_key = self._get_key(user_id, thread_context)

        messages_result = self.valkey.delete(messages_key)

        if messages_result:
            thread_desc = f"thread '{thread_context}'" if thread_context else "default"
            logger.debug(f"Invalidated cached continuum for user {user_id}, {thread_desc}")

        return bool(messages_result)
