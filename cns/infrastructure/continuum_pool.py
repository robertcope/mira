"""
Continuum pool using Valkey for distributed caching.

Provides session detection and automatic expiration for continuums,
replacing the in-memory LRU pool with Valkey-based caching.
"""
import logging
from typing import Dict, Optional, List, Any
from collections import OrderedDict
import threading

from cns.core.continuum import Continuum
from cns.core.message import Message
from cns.infrastructure.continuum_repository import ContinuumRepository
from cns.infrastructure.valkey_message_cache import ValkeyMessageCache
from cns.core.segment_cache_loader import SegmentCacheLoader
from config import config
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class UnitOfWork:
    """
    Unit of Work pattern for continuum operations.
    
    Accumulates changes during a continuum turn and commits them
    atomically to both database and cache.
    """
    
    def __init__(self, continuum: Continuum, pool: 'ContinuumPool'):
        """
        Initialize unit of work.

        Args:
            continuum: Continuum being modified
            pool: Parent continuum pool for persistence operations
        """
        self.continuum = continuum
        self.pool = pool
        self.pending_messages = []
        self.metadata_updated = False
        
    def add_messages(self, *messages: Message) -> None:
        """
        Queue messages for persistence.
        
        Args:
            *messages: One or more Message objects to persist
        """
        self.pending_messages.extend(messages)
        
    def mark_metadata_updated(self) -> None:
        """Mark that continuum metadata needs to be updated."""
        self.metadata_updated = True
        
    def commit(self) -> None:
        """
        Persist all accumulated changes atomically.

        Saves messages to database, updates cache, and persists metadata changes.
        Segment creation now happens automatically in repository.save_message().
        """
        if self.pending_messages:
            # Batch save to database
            self.pool.repository.save_messages_batch(
                self.pending_messages,
                self.continuum.id,
                self.continuum.user_id
            )

            # Update Valkey cache once with current continuum state
            # Get thread context from ambient contextvars
            from utils.user_context import get_current_user
            user_context = get_current_user()
            thread_context = user_context.get('google_chat_thread_key') if user_context else None
            self.pool.valkey_cache.set_continuum(self.continuum.messages, thread_context)

            logger.debug(f"Committed {len(self.pending_messages)} messages for continuum {self.continuum.id}")

        # Update metadata if needed
        if self.metadata_updated:
            self.pool.repository.update_continuum_metadata(self.continuum)
            logger.debug(f"Updated metadata for continuum {self.continuum.id}")

    def _get_real_messages(self) -> List[Message]:
        """
        Get conversation messages, excluding summaries and boundaries.

        Returns:
            List of actual conversation messages (user/assistant exchanges)
        """
        return [
            msg for msg in self.continuum.messages
            if not msg.metadata.get('system_notification')
            and not msg.metadata.get('is_segment_boundary')
        ]


class ContinuumPool:
    """
    Continuum pool backed by Valkey with TTL-based session management.
    
    Uses Valkey for distributed caching with automatic expiration,
    enabling clear session boundary detection when continuums expire.
    """
    
    def __init__(self, repository: ContinuumRepository,
                 session_loader: SegmentCacheLoader):
        """
        Initialize pool with repository and session loader.

        Args:
            repository: Repository for continuum persistence
            session_loader: Session cache loader for new sessions
        """
        self.repository = repository
        self.session_loader = session_loader
        self.valkey_cache = ValkeyMessageCache()
        # Lock for thread-safe operations
        self._lock = threading.Lock()
        
    def get_or_create(self) -> Continuum:
        """
        Get or create continuum, using thread context from ambient context.

        Checks Valkey cache first - if not found, it's a new session.
        Uses ambient user context from set_current_user_id().
        Thread context (if present) is read from user context for per-thread conversations.

        Returns:
            Continuum instance with appropriate cache
        """
        user_id = get_current_user_id()

        # Get thread context from contextvars (set by Google Chat webhook)
        from utils.user_context import get_current_user
        user_context = get_current_user()
        thread_context = user_context.get('google_chat_thread_key') if user_context else None

        with self._lock:
            # Check Valkey cache first (thread-aware key)
            cached_messages = self.valkey_cache.get_continuum(thread_context)

            if cached_messages is not None:
                # Cache hit - continuing session in this thread
                continuum = self.repository.get_continuum(user_id, thread_context)
                if not continuum:
                    thread_desc = f"thread '{thread_context}'" if thread_context else "default"
                    raise RuntimeError(
                        f"Cache hit but continuum not found for user {user_id}, {thread_desc}"
                    )
                continuum.apply_cache(cached_messages)
                thread_desc = f"thread '{thread_context}'" if thread_context else "default"
                logger.debug(f"Continuing session for user {user_id}, {thread_desc}")
                return continuum

            # Cache miss - new session or first message in thread
            continuum = self.repository.get_continuum(user_id, thread_context)

            if not continuum:
                # First message in this thread - create new continuum
                thread_desc = f"thread '{thread_context}'" if thread_context else "default (no thread)"
                logger.info(
                    f"Creating new continuum for user {user_id}, {thread_desc}"
                )
                continuum = self.repository.create_continuum(
                    user_id=user_id,
                    thread_context=thread_context
                )

            # Load session cache with segment boundary if needed
            thread_desc = f"thread '{thread_context}'" if thread_context else "default"
            logger.info(f"New session detected for user {user_id}, {thread_desc} - loading with session boundary")

            messages = self.session_loader.load_session_cache(
                str(continuum.id), user_id
            )
            continuum.apply_cache(messages)

            # Cache in Valkey for future requests
            if messages:
                self.valkey_cache.set_continuum(messages, thread_context)

            return continuum
    
    def begin_work(self, continuum: Continuum) -> UnitOfWork:
        """
        Begin a unit of work for continuum operations.

        Args:
            continuum: Continuum to track changes for

        Returns:
            UnitOfWork instance for accumulating and committing changes
        """
        return UnitOfWork(continuum, self)
    
    def get_by_id(self, continuum_id: str, user_id: str) -> Optional[Continuum]:
        """
        Get continuum by ID, checking Valkey cache.
        
        Args:
            continuum_id: Continuum identifier
            user_id: User identifier for access verification
            
        Returns:
            Continuum instance or None if not found
        """
        with self._lock:
            # Load continuum from repository
            continuum = self.repository.get_by_id(continuum_id, user_id)

            if not continuum:
                return None

            # No callback needed - using Unit of Work pattern

            # Check Valkey for cached messages
            cached_messages = self.valkey_cache.get_continuum()

            if cached_messages:
                # Apply cached messages from cache
                continuum.apply_cache(cached_messages)
                logger.debug(f"Found cached messages for continuum {continuum_id}")
            else:
                # New session or cache expired
                logger.debug(f"No cached messages for continuum {continuum_id}")
                # Messages already loaded by repository

            return continuum
    
    def invalidate(self) -> None:
        """
        Remove continuum from Valkey cache, using thread context from ambient context.

        Requires: Active user context (set via set_current_user_id during authentication)

        Raises:
            RuntimeError: If no user context is set
        """
        user_id = get_current_user_id()

        # Get thread context from contextvars
        from utils.user_context import get_current_user
        user_context = get_current_user()
        thread_context = user_context.get('google_chat_thread_key') if user_context else None

        if self.valkey_cache.invalidate_continuum(thread_context):
            thread_desc = f"thread '{thread_context}'" if thread_context else "default"
            logger.debug(f"Invalidated cached continuum for user {user_id}, {thread_desc}")
        else:
            thread_desc = f"thread '{thread_context}'" if thread_context else "default"
            logger.debug(f"No cached continuum to invalidate for user {user_id}, {thread_desc}")
    
    def update_cache(self, user_id: str, messages: List[Message]) -> None:
        """
        Update continuum cache in Valkey, using thread context from ambient context.

        Called when messages are added or modified.

        Args:
            user_id: User identifier
            messages: Updated message list
        """
        # Get thread context from contextvars
        from utils.user_context import get_current_user
        user_context = get_current_user()
        thread_context = user_context.get('google_chat_thread_key') if user_context else None

        self.valkey_cache.set_continuum(messages, thread_context)
        thread_desc = f"thread '{thread_context}'" if thread_context else "default"
        logger.debug(f"Updated continuum cache for user {user_id}, {thread_desc}")

    def get_session_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get session information for a user.

        Args:
            user_id: User identifier

        Returns:
            Dict with session info (cached: bool)
        """
        cached_messages = self.valkey_cache.get_continuum()
        return {
            'cached': cached_messages is not None
        }


# Global continuum pool instance
_continuum_pool: Optional[ContinuumPool] = None


def initialize_continuum_pool(repository: ContinuumRepository,
                                session_loader: SegmentCacheLoader) -> ContinuumPool:
    """
    Initialize the global continuum pool with required dependencies.

    Must be called during application startup.

    Args:
        repository: Continuum repository
        session_loader: Session cache loader for new sessions

    Returns:
        Initialized ContinuumPool instance
    """
    global _continuum_pool
    _continuum_pool = ContinuumPool(repository, session_loader)
    logger.info("Continuum pool initialized with session cache loader")
    return _continuum_pool


def get_continuum_pool() -> ContinuumPool:
    """
    Get the global continuum pool instance.

    Raises:
        RuntimeError: If pool has not been initialized
    """
    global _continuum_pool
    if _continuum_pool is None:
        raise RuntimeError(
            "Continuum pool not initialized. Call initialize_continuum_pool() "
            "during application startup."
        )
    return _continuum_pool