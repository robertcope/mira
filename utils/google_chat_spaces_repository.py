"""
Repository for managing Google Chat space identifiers.

Stores space information for each user to enable proactive push notifications.
Uses PostgreSQL with RLS for automatic user isolation.
"""
import logging
from typing import Optional, Dict, Any
from uuid import UUID

from clients.postgres_client import PostgresClient
from utils.timezone_utils import utc_now, format_utc_iso
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class GoogleChatSpacesRepository:
    """
    Repository for managing Google Chat space associations.

    All queries automatically scoped to current user via RLS.
    """

    def __init__(self):
        """Initialize repository with database connection."""
        self.db = PostgresClient('mira_service')

    def upsert_space(
        self,
        space_name: str,
        space_type: str = 'DM',
        thread_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Insert or update Google Chat space information for current user.

        Args:
            space_name: Google Chat space ID (e.g., 'spaces/AAAAxxxxxxx')
            space_type: Type of space ('DM' or 'ROOM')
            thread_key: Optional thread key for threading messages

        Returns:
            Dict with space information

        Raises:
            RuntimeError: If database operation fails
        """
        user_id = get_current_user_id()

        # Validate user_id
        if not user_id or not isinstance(user_id, str) or user_id.strip() == "":
            logger.error(f"Invalid user_id detected: '{user_id}' (type: {type(user_id)})")
            raise RuntimeError(f"Cannot upsert Google Chat space: user_id is empty or invalid (got: '{user_id}')")

        now = format_utc_iso(utc_now())

        try:
            # Upsert space info (update if exists, insert if not)
            query = """
                INSERT INTO google_chat_spaces (user_id, space_name, space_type, thread_key, last_message_at, created_at, updated_at)
                VALUES (%(user_id)s, %(space_name)s, %(space_type)s, %(thread_key)s, %(now)s, %(now)s, %(now)s)
                ON CONFLICT (user_id, space_name)
                DO UPDATE SET
                    space_type = EXCLUDED.space_type,
                    thread_key = EXCLUDED.thread_key,
                    last_message_at = EXCLUDED.last_message_at,
                    updated_at = EXCLUDED.updated_at
                RETURNING id, user_id, space_name, space_type, thread_key, last_message_at, created_at, updated_at
            """

            params = {
                'user_id': user_id,
                'space_name': space_name,
                'space_type': space_type,
                'thread_key': thread_key,
                'now': now
            }

            result = self.db.execute_single(query, params)

            if not result:
                raise RuntimeError(f"Failed to upsert Google Chat space {space_name}")

            logger.info(f"Upserted Google Chat space {space_name} for user {user_id}")
            return result

        except Exception as e:
            logger.error(f"Error upserting Google Chat space {space_name}: {e}", exc_info=True)
            raise RuntimeError(f"Failed to upsert Google Chat space: {e}") from e

    def get_space_for_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get Google Chat space for a specific user (admin operation).

        Creates a user-scoped database client to properly set RLS context.
        Called from scheduled jobs that iterate over users.

        Args:
            user_id: User ID to look up space for

        Returns:
            Dict with space info or None if not found
        """
        try:
            # Create user-scoped client to ensure RLS context is set correctly
            db = PostgresClient('mira_service', user_id=user_id)

            query = """
                SELECT id, user_id, space_name, space_type, thread_key, last_message_at, created_at, updated_at
                FROM google_chat_spaces
                ORDER BY last_message_at DESC NULLS LAST
                LIMIT 1
            """

            result = db.execute_single(query)
            return result

        except Exception as e:
            logger.error(f"Error getting Google Chat space for user {user_id}: {e}", exc_info=True)
            # Return None instead of raising - user may not have Google Chat configured
            return None

    def get_current_user_space(self) -> Optional[Dict[str, Any]]:
        """
        Get Google Chat space for current user.

        Returns:
            Dict with space info or None if not found
        """
        try:
            # RLS automatically filters by current user
            query = """
                SELECT id, user_id, space_name, space_type, thread_key, last_message_at, created_at, updated_at
                FROM google_chat_spaces
                ORDER BY last_message_at DESC NULLS LAST
                LIMIT 1
            """

            result = self.db.execute_single(query)
            return result

        except Exception as e:
            logger.error(f"Error getting current user's Google Chat space: {e}", exc_info=True)
            return None

    def delete_space(self, space_name: str) -> bool:
        """
        Delete Google Chat space for current user.

        Args:
            space_name: Space ID to delete

        Returns:
            True if deleted, False if not found
        """
        try:
            query = """
                DELETE FROM google_chat_spaces
                WHERE space_name = %(space_name)s
            """

            rows_affected = self.db.execute(query, {'space_name': space_name})
            return rows_affected > 0

        except Exception as e:
            logger.error(f"Error deleting Google Chat space {space_name}: {e}", exc_info=True)
            raise RuntimeError(f"Failed to delete Google Chat space: {e}") from e
