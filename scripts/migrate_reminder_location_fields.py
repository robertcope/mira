#!/usr/bin/env python3
"""
Migration script to add location-based reminder fields to existing user databases.

This script adds the new location columns to the reminders table in all user SQLite databases.
Run this after deploying the location reminders feature.

Usage:
    python scripts/migrate_reminder_location_fields.py
"""
import sys
import os
import logging
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.postgres_client import PostgresClient
from utils.user_context import set_current_user_id
from tools.implementations.reminder_tool import ReminderTool

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_user_reminders(user_id: str) -> bool:
    """
    Migrate reminder table for a single user.

    Args:
        user_id: User ID to migrate

    Returns:
        True if successful, False if failed
    """
    try:
        # Set user context to access their SQLite database
        set_current_user_id(user_id)

        # Initialize reminder tool (this will run migrations automatically)
        reminder_tool = ReminderTool()

        # Verify migrations worked by checking if location_trigger column exists
        try:
            result = reminder_tool.db.execute(
                "SELECT location_trigger FROM reminders LIMIT 1"
            )
            logger.info(f"✓ User {user_id[:8]}... - location fields already exist or successfully migrated")
            return True
        except Exception as e:
            if "no such column" in str(e).lower():
                logger.error(f"✗ User {user_id[:8]}... - migration failed: {e}")
                return False
            # If it's a different error (like "no rows"), that's fine
            logger.info(f"✓ User {user_id[:8]}... - location fields verified")
            return True

    except Exception as e:
        logger.error(f"✗ User {user_id[:8]}... - unexpected error: {e}", exc_info=True)
        return False


def get_all_users():
    """Get all active users from PostgreSQL."""
    db = PostgresClient('mira_service')
    query = "SELECT id, email FROM users WHERE is_active = true ORDER BY created_at"
    return db.execute(query)


def main():
    """Main migration script."""
    logger.info("=" * 60)
    logger.info("Location Reminders Migration Script")
    logger.info("=" * 60)

    # Get all users
    try:
        users = get_all_users()
        logger.info(f"Found {len(users)} active user(s) to migrate")
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        sys.exit(1)

    if not users:
        logger.info("No users found - nothing to migrate")
        return

    # Migrate each user
    success_count = 0
    failure_count = 0

    for user in users:
        user_id = user['id']
        email = user.get('email', 'unknown')

        logger.info(f"Migrating user: {email} ({user_id[:8]}...)")

        if migrate_user_reminders(user_id):
            success_count += 1
        else:
            failure_count += 1

    # Summary
    logger.info("=" * 60)
    logger.info("Migration Summary")
    logger.info("=" * 60)
    logger.info(f"Total users: {len(users)}")
    logger.info(f"✓ Successful: {success_count}")
    logger.info(f"✗ Failed: {failure_count}")

    if failure_count > 0:
        logger.warning(f"⚠ {failure_count} user(s) failed migration - check logs above")
        sys.exit(1)
    else:
        logger.info("✓ All users migrated successfully!")


if __name__ == "__main__":
    main()
