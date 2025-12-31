"""
Distributed lock implementation using Valkey for multi-process concurrency control.

Provides atomic distributed locks that work across multiple worker processes,
replacing in-memory locks that only work within a single process.

Also provides UserRequestQueue for queuing requests instead of rejecting them.
"""

import logging
import uuid
import json
import time
from typing import Optional, Dict, Any
from contextlib import contextmanager

from clients.valkey_client import get_valkey
from utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


class DistributedLock:
    """
    Distributed lock using Valkey's atomic SET NX operation.
    
    Ensures only one process can hold a lock for a given resource at a time,
    with automatic expiration to prevent deadlocks from crashed processes.
    """
    
    def __init__(self, lock_prefix: str = "lock:", default_ttl: int = 60):
        """
        Initialize distributed lock manager.
        
        Args:
            lock_prefix: Prefix for lock keys in Valkey
            default_ttl: Default TTL in seconds for locks (prevents deadlocks)
        """
        self.lock_prefix = lock_prefix
        self.default_ttl = default_ttl
        self.valkey = get_valkey()
    
    def acquire(self, resource_id: str, ttl: Optional[int] = None, lock_value: Optional[str] = None) -> bool:
        """
        Attempt to acquire a distributed lock.

        Uses Valkey's atomic SET NX (set if not exists) operation to ensure
        only one process can acquire the lock.

        Args:
            resource_id: Unique identifier for the resource to lock
            ttl: Time-to-live in seconds (uses default if not specified)
            lock_value: Optional value to store with lock (for debugging)

        Returns:
            True if lock was acquired, False if already locked

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        key = f"{self.lock_prefix}{resource_id}"
        ttl = ttl or self.default_ttl
        lock_value = lock_value or str(uuid.uuid4())

        # SET NX (set if not exists) with EX (expiration)
        # This is atomic - either we get the lock or we don't
        success = self.valkey.set(
            key,
            lock_value,
            nx=True,  # Only set if key doesn't exist
            ex=ttl    # Set expiration time
        )

        if success:
            logger.debug(f"Acquired lock for {resource_id} with TTL {ttl}s")
        else:
            logger.debug(f"Failed to acquire lock for {resource_id} - already locked")

        return bool(success)
    
    def get_lock_owner(self, resource_id: str) -> Optional[str]:
        """
        Get the current owner (value) of a lock.

        Args:
            resource_id: Unique identifier for the resource

        Returns:
            Lock owner value if locked, None if not locked

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        key = f"{self.lock_prefix}{resource_id}"
        value = self.valkey.get(key)
        return value
    
    def force_release(self, resource_id: str) -> bool:
        """
        Force release a lock regardless of owner.
        
        Use with caution - only for cleaning up stale locks.
        
        Args:
            resource_id: Unique identifier for the resource
            
        Returns:
            True if lock was released, False if lock didn't exist
        """
        logger.warning(f"Force releasing lock for {resource_id}")
        return self.release(resource_id)
    
    def release(self, resource_id: str) -> bool:
        """
        Release a distributed lock.

        Args:
            resource_id: Unique identifier for the resource to unlock

        Returns:
            True if lock was released, False if lock didn't exist

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        key = f"{self.lock_prefix}{resource_id}"

        deleted = self.valkey.delete(key)

        if deleted:
            logger.debug(f"Released lock for {resource_id}")
        else:
            logger.debug(f"No lock to release for {resource_id}")

        return bool(deleted)
    
    def is_locked(self, resource_id: str) -> bool:
        """
        Check if a resource is currently locked.

        Args:
            resource_id: Unique identifier for the resource

        Returns:
            True if resource is locked, False otherwise

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        key = f"{self.lock_prefix}{resource_id}"
        return self.valkey.exists(key)
    
    def get_ttl(self, resource_id: str) -> int:
        """
        Get remaining TTL for a lock.

        Args:
            resource_id: Unique identifier for the resource

        Returns:
            TTL in seconds, -2 if key doesn't exist, -1 if no TTL set

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        key = f"{self.lock_prefix}{resource_id}"
        return self.valkey.ttl(key)
    
    @contextmanager
    def lock(self, resource_id: str, ttl: Optional[int] = None):
        """
        Context manager for distributed locks.

        Usage:
            with distributed_lock.lock("user_123"):
                # Critical section - only one process can be here
                process_user_request()

        Args:
            resource_id: Unique identifier for the resource to lock
            ttl: Time-to-live in seconds

        Raises:
            LockAcquisitionError: If lock can't be acquired

        Yields:
            None if lock acquired successfully
        """
        acquired = False
        try:
            acquired = self.acquire(resource_id, ttl)
            if not acquired:
                raise LockAcquisitionError(f"Could not acquire lock for {resource_id}")
            yield
        finally:
            if acquired:
                self.release(resource_id)


class LockAcquisitionError(Exception):
    """Raised when a distributed lock cannot be acquired."""
    pass


class UserRequestLock:
    """
    Specialized distributed lock for per-user request concurrency control.
    
    Ensures a user can only have one active chat request at a time across
    all worker processes.
    """
    
    def __init__(self, ttl: int = 60):
        """
        Initialize user request lock.
        
        Args:
            ttl: Lock timeout in seconds (protects against crashes)
        """
        self.lock = DistributedLock(lock_prefix="user_lock:", default_ttl=ttl)
        self.default_ttl = ttl
    
    
    
    def acquire(self, user_id: str) -> bool:
        """
        Attempt to acquire lock for user.
        
        Args:
            user_id: User identifier
        
        Returns:
            True if lock acquired, False if user has concurrent request
        """
        success = self.lock.acquire(user_id, ttl=self.default_ttl)
        if success:
            logger.debug(f"Acquired lock for user {user_id} (TTL: {self.default_ttl}s)")
        else:
            logger.debug(f"Failed to acquire lock for user {user_id} - concurrent request in progress")
        return success
    
    
    def release(self, user_id: str) -> bool:
        """
        Release lock for user.
        
        Args:
            user_id: User identifier
        
        Returns:
            True if lock was released
        """
        return self.lock.release(user_id)
    
    def is_locked(self, user_id: str) -> bool:
        """
        Check if user currently has an active request.
        
        Args:
            user_id: User identifier
        
        Returns:
            True if user has active request
        """
        return self.lock.is_locked(user_id)
    
    @contextmanager
    def lock_user(self, user_id: str):
        """
        Context manager for user request locks.

        Args:
            user_id: User identifier

        Raises:
            LockAcquisitionError: If lock can't be acquired

        Yields:
            None if lock acquired successfully
        """
        with self.lock.lock(user_id):
            yield


class UserRequestQueue:
    """
    Per-user request queue with Valkey-backed FIFO storage.

    Queues incoming requests instead of rejecting them, ensuring serial
    processing while accepting concurrent submissions. Each user gets their
    own queue.

    Architecture:
    - Valkey LIST for FIFO queue: LPUSH to enqueue, BRPOP to dequeue
    - Processing lock ensures only one request processes at a time
    - Request timeout prevents infinite waits
    - Queue depth limit prevents abuse
    """

    def __init__(
        self,
        processing_ttl: int = 180,
        queue_timeout: int = 300,
        max_queue_depth: int = 5
    ):
        """
        Initialize user request queue.

        Args:
            processing_ttl: TTL for processing lock (max time to process one request)
            queue_timeout: Max seconds a request can wait in queue before expiring
            max_queue_depth: Max requests allowed in queue per user
        """
        self.valkey = get_valkey()
        self.processing_lock = DistributedLock(
            lock_prefix="user_processing:",
            default_ttl=processing_ttl
        )
        self.processing_ttl = processing_ttl
        self.queue_timeout = queue_timeout
        self.max_queue_depth = max_queue_depth
        self.logger = logging.getLogger("user_request_queue")

    def _get_queue_key(self, user_id: str) -> str:
        """Get Valkey key for user's request queue."""
        return f"user_queue:{user_id}"

    def enqueue(self, user_id: str, request_data: Dict[str, Any]) -> str:
        """
        Add request to user's queue.

        Args:
            user_id: User identifier
            request_data: Request payload to queue

        Returns:
            request_id for tracking

        Raises:
            ValueError: If queue is full
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        queue_key = self._get_queue_key(user_id)

        # Check queue depth
        current_depth = self.valkey.llen(queue_key)
        if current_depth >= self.max_queue_depth:
            raise ValueError(
                f"Queue full: user has {current_depth} pending requests "
                f"(max {self.max_queue_depth})"
            )

        # Create queue entry with metadata
        request_id = str(uuid.uuid4())
        queue_entry = {
            "request_id": request_id,
            "user_id": user_id,
            "enqueued_at": utc_now().isoformat(),
            "data": request_data
        }

        # Push to queue (LPUSH = add to head, RPOP = remove from tail = FIFO)
        self.valkey.lpush(queue_key, json.dumps(queue_entry))

        # Set queue expiration to prevent orphaned queues
        self.valkey.expire(queue_key, self.queue_timeout)

        self.logger.info(
            f"Enqueued request {request_id[:8]} for user {user_id[:8]} "
            f"(queue depth: {current_depth + 1})"
        )

        return request_id

    def dequeue_blocking(self, user_id: str, timeout: int = 0) -> Optional[Dict[str, Any]]:
        """
        Block until next request available in queue, then dequeue it.

        Uses BRPOP for blocking pop - waits until request available or timeout.

        Args:
            user_id: User identifier
            timeout: Max seconds to wait (0 = wait indefinitely)

        Returns:
            Request entry dict with request_id, user_id, enqueued_at, data
            None if timeout or queue empty

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        queue_key = self._get_queue_key(user_id)

        # BRPOP: Block until element available, then pop from tail (FIFO)
        # Returns tuple: (key, value) or None if timeout
        result = self.valkey.brpop(queue_key, timeout=timeout)

        if not result:
            return None

        # result is tuple: (key, value)
        _, queue_entry_json = result
        queue_entry = json.loads(queue_entry_json)

        # Check if request expired while in queue
        enqueued_at = queue_entry["enqueued_at"]
        enqueued_time = time.mktime(time.strptime(enqueued_at, "%Y-%m-%dT%H:%M:%S.%f"))
        elapsed = time.time() - enqueued_time

        if elapsed > self.queue_timeout:
            self.logger.warning(
                f"Request {queue_entry['request_id'][:8]} expired in queue "
                f"(waited {elapsed:.1f}s, max {self.queue_timeout}s)"
            )
            return None

        self.logger.info(
            f"Dequeued request {queue_entry['request_id'][:8]} for user {user_id[:8]} "
            f"(waited {elapsed:.1f}s)"
        )

        return queue_entry

    def get_queue_depth(self, user_id: str) -> int:
        """
        Get current queue depth for user.

        Args:
            user_id: User identifier

        Returns:
            Number of requests in queue

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        queue_key = self._get_queue_key(user_id)
        return self.valkey.llen(queue_key)

    def clear_queue(self, user_id: str) -> int:
        """
        Clear all queued requests for user.

        Args:
            user_id: User identifier

        Returns:
            Number of requests removed

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        queue_key = self._get_queue_key(user_id)
        depth = self.valkey.llen(queue_key)
        self.valkey.delete(queue_key)

        if depth > 0:
            self.logger.warning(f"Cleared {depth} queued requests for user {user_id[:8]}")

        return depth

    def acquire_processing_lock(self, user_id: str) -> bool:
        """
        Acquire lock for processing user's request.

        Args:
            user_id: User identifier

        Returns:
            True if lock acquired, False if user has concurrent processing

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        return self.processing_lock.acquire(user_id, ttl=self.processing_ttl)

    def release_processing_lock(self, user_id: str) -> bool:
        """
        Release processing lock for user.

        Args:
            user_id: User identifier

        Returns:
            True if lock was released

        Raises:
            Exception: If Valkey is unavailable (infrastructure failure)
        """
        return self.processing_lock.release(user_id)

    @contextmanager
    def process_request(self, user_id: str, poll_interval: float = 1.0, max_wait: int = 300):
        """
        Context manager for processing a request with automatic lock management.

        Waits (with polling) until processing lock is available, then acquires it.
        This ensures requests are processed serially without rejection.

        Args:
            user_id: User identifier
            poll_interval: Seconds to wait between lock acquisition attempts
            max_wait: Maximum seconds to wait for lock (raises after timeout)

        Raises:
            LockAcquisitionError: If processing lock can't be acquired within max_wait

        Yields:
            None if lock acquired successfully
        """
        import time

        start_time = time.time()
        acquired = False

        try:
            # Poll until lock is available
            while True:
                acquired = self.processing_lock.acquire(user_id, ttl=self.processing_ttl)
                if acquired:
                    self.logger.info(f"Acquired processing lock for user {user_id[:8]}")
                    break

                # Check if we've exceeded max wait time
                elapsed = time.time() - start_time
                if elapsed >= max_wait:
                    raise LockAcquisitionError(
                        f"Could not acquire processing lock for {user_id} "
                        f"after {elapsed:.1f}s (max: {max_wait}s)"
                    )

                # Log wait status
                if int(elapsed) % 10 == 0 and elapsed > 0:  # Log every 10 seconds
                    ttl = self.processing_lock.get_ttl(user_id)
                    self.logger.info(
                        f"Waiting for processing lock for user {user_id[:8]} "
                        f"(waited {elapsed:.1f}s, lock TTL: {ttl}s)"
                    )

                # Wait before retry
                time.sleep(poll_interval)

            yield

        finally:
            if acquired:
                self.processing_lock.release(user_id)
                self.logger.debug(f"Released processing lock for user {user_id[:8]}")