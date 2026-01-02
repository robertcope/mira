"""
Google Chat notification service for proactive push messages.

Checks for due reminders and sends notifications to users via Google Chat.
Integrates with scheduler service for periodic execution.
"""
import logging
from typing import List, Dict, Any
from apscheduler.triggers.interval import IntervalTrigger

from utils.google_chat_client import get_google_chat_client
from utils.google_chat_formatter import format_simple_text
from utils.timezone_utils import utc_now, parse_utc_time_string, format_relative_time, convert_from_utc, format_utc_iso
from utils.user_context import set_current_user_id, get_user_preferences

logger = logging.getLogger(__name__)


class GoogleChatNotifier:
    """
    Service for sending proactive Google Chat notifications.

    Handles:
    - Due reminder notifications (periodic check)
    - Background search completion notifications (event-driven)
    """

    def __init__(self, event_bus=None):
        """
        Initialize the notifier service.

        Args:
            event_bus: Optional event bus for subscribing to search completion events
        """
        # Lazy imports to avoid circular dependencies
        from clients.postgres_client import PostgresClient
        from utils.google_chat_spaces_repository import GoogleChatSpacesRepository

        self.chat_client = None
        self.spaces_repo = GoogleChatSpacesRepository()
        self.db = PostgresClient('mira_service')
        self.event_bus = event_bus

        # Subscribe to search completion events if event bus provided
        if self.event_bus:
            self.event_bus.subscribe('UpdateTrinketEvent', self._handle_search_completion)

    def _ensure_chat_client(self):
        """Lazily initialize Google Chat client (may not be configured)."""
        if self.chat_client is None:
            try:
                self.chat_client = get_google_chat_client()
            except RuntimeError as e:
                logger.warning(f"Google Chat client not available: {e}")
                self.chat_client = False  # Mark as unavailable

    def _handle_search_completion(self, event):
        """
        Handle UpdateTrinketEvent for search completions.

        Filters for GetContextTrinket updates and sends Google Chat notifications
        for all completion statuses (success, timeout, failure).

        Args:
            event: UpdateTrinketEvent from the event bus
        """
        # Only handle GetContextTrinket updates
        if event.target_trinket != 'GetContextTrinket':
            return

        context = event.context
        status = context.get('status')
        task_id = context.get('task_id', 'unknown')

        # Only handle completion events (skip 'pending' status)
        if status not in ('success', 'timeout', 'failed'):
            logger.debug(f"Ignoring non-completion status '{status}' for task {task_id[:8] if len(task_id) > 8 else task_id}")
            return

        logger.info(f"Received search completion event: task_id={task_id[:8] if len(task_id) > 8 else task_id}, status={status}")

        try:
            self._notify_search_completion(
                user_id=event.user_id,
                continuum_id=event.continuum_id,
                status=status,
                context=context
            )
        except Exception as e:
            # Log but don't raise - search completion notifications are non-critical
            logger.error(f"Error sending search completion notification: {e}", exc_info=True)

    def _notify_search_completion(
        self,
        user_id: str,
        continuum_id: str,
        status: str,
        context: Dict[str, Any]
    ):
        """
        Send Google Chat notification for search completion.

        Args:
            user_id: User ID who initiated the search
            continuum_id: Continuum ID where search was initiated
            status: Completion status ('success', 'timeout', 'failed')
            context: Event context with search details
        """
        self._ensure_chat_client()

        # If Google Chat not configured, skip silently
        if self.chat_client is False:
            logger.info("Google Chat not configured, skipping search completion notification")
            return

        # Get user's Google Chat space
        space_info = self.spaces_repo.get_space_for_user(user_id)

        if not space_info:
            logger.info(f"User {user_id} has no Google Chat space configured, skipping notification")
            return

        # Format notification message based on status
        message = self._format_search_notification(status, context)

        # Send message via Google Chat API
        try:
            self.chat_client.send_message(
                space_name=space_info['space_name'],
                text=message,
                thread_key=space_info.get('thread_key')
            )
            logger.info(f"Sent search completion notification to user {user_id} (status: {status})")
        except Exception as e:
            logger.error(f"Error sending Google Chat message: {e}", exc_info=True)
            raise

    def _format_search_notification(
        self,
        status: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Format brief search completion notification message.

        Args:
            status: Completion status ('success', 'timeout', 'failed')
            context: Event context with search details

        Returns:
            Formatted notification text
        """
        query = context.get('query', 'Unknown query')

        if status == 'success':
            summary = context.get('summary', {})
            findings_count = len(summary.get('findings', []))
            return f"✓ Search complete: \"{query}\" - Found {findings_count} relevant items"

        elif status == 'timeout':
            findings_count = context.get('findings_count', 0)
            iteration = context.get('iteration', 0)
            return f"⏱ Search timeout: \"{query}\" - Stopped after {iteration} iterations with {findings_count} findings"

        elif status == 'failed':
            error_type = context.get('error_type', 'Unknown error')
            return f"✗ Search failed: \"{query}\" - {error_type}"

        return f"Search update: \"{query}\" - Status: {status}"

    def check_and_notify_reminders(self):
        """
        Check for due reminders and send notifications via Google Chat.

        This is called by the scheduler service periodically.
        Designed to be safe to run concurrently (idempotent).

        Note: Reminders are stored in user-specific SQLite databases,
        so we iterate over all users and check each individually.
        """
        try:
            self._ensure_chat_client()

            # If Google Chat not configured, skip silently
            if self.chat_client is False:
                logger.debug("Google Chat not configured, skipping reminder notifications")
                return

            # Get all active users
            users = self._get_all_users()

            if not users:
                logger.debug("No users found")
                return

            # Check each user's reminders
            for user in users:
                try:
                    user_id = str(user['id'])
                    self._check_and_notify_user(user_id, user['timezone'])
                except Exception as e:
                    logger.error(f"Error checking reminders for user {user['id']}: {e}", exc_info=True)
                    # Continue with other users

        except Exception as e:
            logger.error(f"Error in reminder notification check: {e}", exc_info=True)
            # Don't raise - scheduler should keep running

    def _get_all_users(self) -> List[Dict[str, Any]]:
        """
        Get all active users from PostgreSQL.

        Returns:
            List of user dicts with id and timezone
        """
        try:
            query = """
                SELECT id, timezone, email
                FROM users
                WHERE is_active = true
            """

            results = self.db.execute_query(query)
            return results

        except Exception as e:
            logger.error(f"Error querying users: {e}", exc_info=True)
            return []

    def _check_and_notify_user(self, user_id: str, timezone: str):
        """
        Check reminders for a specific user and notify if any are due.

        Args:
            user_id: User ID to check
            timezone: User's timezone
        """
        # Set user context to access their SQLite database
        set_current_user_id(user_id)

        # Get due reminders from user's SQLite database
        from tools.implementations.reminder_tool import ReminderTool
        reminder_tool = ReminderTool()

        try:
            # Get overdue and today's reminders
            overdue_result = reminder_tool.run(
                operation="get_reminders",
                date_type="overdue",
                category="user"
            )

            today_result = reminder_tool.run(
                operation="get_reminders",
                date_type="today",
                category="user"
            )

            # Collect unnotified reminders that are actually due (not future)
            due_reminders = []
            now = utc_now()

            for result in [overdue_result, today_result]:
                for reminder in result.get('reminders', []):
                    # Only notify if not already notified AND due time has passed
                    if not reminder.get('notified_at'):
                        reminder_date = parse_utc_time_string(reminder['reminder_date'])
                        if reminder_date <= now:
                            due_reminders.append(reminder)

            if not due_reminders:
                return

            # Send notification
            self._notify_user_reminders(user_id, due_reminders, timezone)

            # Mark as notified in user's SQLite database
            self._mark_reminders_notified_sqlite(reminder_tool, [r['id'] for r in due_reminders])

        except Exception as e:
            logger.error(f"Error checking reminders for user {user_id}: {e}", exc_info=True)
            raise

    def _notify_user_reminders(
        self,
        user_id: str,
        reminders: List[Dict[str, Any]],
        timezone: str
    ):
        """
        Send notification to user for their due reminders.

        Args:
            user_id: User ID to notify
            reminders: List of due reminders for this user
            timezone: User's timezone
        """
        try:
            # Get user's Google Chat space
            space_info = self.spaces_repo.get_space_for_user(user_id)

            if not space_info:
                logger.debug(f"User {user_id} has no Google Chat space configured")
                return

            # Format notification message
            message = self._format_reminder_notification(reminders, timezone)

            # Send message via Google Chat API
            self.chat_client.send_message(
                space_name=space_info['space_name'],
                text=message,
                thread_key=space_info.get('thread_key')
            )

            logger.info(f"Sent {len(reminders)} reminder notification(s) to user {user_id}")

        except Exception as e:
            logger.error(f"Error notifying user {user_id}: {e}", exc_info=True)
            raise

    def _format_reminder_notification(
        self,
        reminders: List[Dict[str, Any]],
        timezone: str
    ) -> str:
        """
        Format reminder notification message.

        Args:
            reminders: List of reminders to include
            timezone: User's timezone for formatting

        Returns:
            Formatted notification text
        """
        if len(reminders) == 1:
            reminder = reminders[0]
            reminder_date = parse_utc_time_string(reminder['reminder_date'])
            relative_time = format_relative_time(reminder_date)

            message = f"⏰ **Reminder: {reminder['encrypted__title']}**\n\n"

            if reminder.get('encrypted__description'):
                message += f"{reminder['encrypted__description']}\n\n"

            message += f"Due: {relative_time}"

            return message

        else:
            # Multiple reminders
            message = f"⏰ **You have {len(reminders)} reminders due:**\n\n"

            for reminder in reminders:
                reminder_date = parse_utc_time_string(reminder['reminder_date'])
                relative_time = format_relative_time(reminder_date)

                message += f"• **{reminder['encrypted__title']}** - {relative_time}\n"

            return message

    def _mark_reminders_notified_sqlite(
        self,
        reminder_tool,
        reminder_ids: List[str]
    ):
        """
        Mark reminders as notified in user's SQLite database.

        Args:
            reminder_tool: ReminderTool instance (already has user context)
            reminder_ids: List of reminder IDs to mark
        """
        try:
            now_iso = format_utc_iso(utc_now())

            # Update each reminder's notified_at timestamp
            for reminder_id in reminder_ids:
                reminder_tool.db.update(
                    'reminders',
                    {'notified_at': now_iso},
                    'id = :id',
                    {'id': reminder_id}
                )

            logger.info(f"Marked {len(reminder_ids)} reminders as notified")

        except Exception as e:
            logger.error(f"Error marking reminders as notified: {e}", exc_info=True)
            raise


# Global singleton
_google_chat_notifier = None


def get_google_chat_notifier(event_bus=None) -> GoogleChatNotifier:
    """
    Get or create global GoogleChatNotifier instance.

    Args:
        event_bus: Optional event bus for event subscriptions (only used on first init)

    Returns:
        GoogleChatNotifier instance
    """
    global _google_chat_notifier
    if _google_chat_notifier is None:
        _google_chat_notifier = GoogleChatNotifier(event_bus=event_bus)
    return _google_chat_notifier


def register_notification_job(scheduler_service, event_bus=None) -> bool:
    """
    Register Google Chat notification job with scheduler.

    Args:
        scheduler_service: System scheduler service
        event_bus: Optional event bus for search completion notifications

    Returns:
        True if registered successfully

    Raises:
        RuntimeError: If job registration fails
    """
    try:
        # Initialize notifier with event bus (enables search completion notifications)
        notifier = get_google_chat_notifier(event_bus=event_bus)

        # Check every minute for due reminders
        trigger = IntervalTrigger(minutes=1)

        scheduler_service.register_job(
            job_id='google_chat_reminder_notifications',
            func=notifier.check_and_notify_reminders,
            trigger=trigger,
            component='google_chat_notifier',
            description='Check for due reminders and send Google Chat notifications',
            replace_existing=True
        )

        logger.info("Registered Google Chat reminder notification job")
        if event_bus:
            logger.info("Enabled Google Chat notifications for background search completions")
        return True

    except Exception as e:
        logger.error(f"Error registering Google Chat notification job: {e}", exc_info=True)
        raise RuntimeError(f"Failed to register Google Chat notification job: {e}") from e
