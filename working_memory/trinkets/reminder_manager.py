"""Reminder manager trinket for system prompt injection."""
import logging
import datetime
from typing import Dict, Any, List

from utils.timezone_utils import convert_from_utc, format_datetime, parse_utc_time_string
from utils.user_context import get_user_preferences
from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


class ReminderManager(EventAwareTrinket):
    """
    Manages reminder information for the notification center.

    Fetches active reminders from the reminder tool when requested.
    """
    
    def _get_variable_name(self) -> str:
        """Reminder manager publishes to 'active_reminders'."""
        return "active_reminders"
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate reminder content by fetching from reminder tool.

        Args:
            context: Update context (unused for reminder manager)

        Returns:
            Formatted reminders section or empty string if no reminders

        Raises:
            Exception: If ReminderTool operations fail (infrastructure/filesystem issues)
        """
        from tools.implementations.reminder_tool import ReminderTool
        reminder_tool = ReminderTool()

        # Let infrastructure failures propagate
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

        # Get internal reminders separately
        internal_overdue = reminder_tool.run(
            operation="get_reminders",
            date_type="overdue",
            category="internal"
        )

        internal_today = reminder_tool.run(
            operation="get_reminders",
            date_type="today",
            category="internal"
        )

        # Collect reminders, keeping overdue and today separate
        user_overdue = self._collect_reminders([overdue_result])
        user_today = self._collect_reminders([today_result])
        internal_overdue_list = self._collect_reminders([internal_overdue])
        internal_today_list = self._collect_reminders([internal_today])

        if not user_overdue and not user_today and not internal_overdue_list and not internal_today_list:
            logger.debug("No active reminders")
            return ""  # Legitimately empty - user has no reminders set

        # Format reminder content with separate overdue/today sections
        reminder_info = self._format_reminders(
            user_overdue, user_today,
            internal_overdue_list, internal_today_list
        )
        total_user = len(user_overdue) + len(user_today)
        total_internal = len(internal_overdue_list) + len(internal_today_list)
        logger.debug(f"Generated reminder info with {total_user} user ({len(user_overdue)} overdue) and {total_internal} internal ({len(internal_overdue_list)} overdue) reminders")
        return reminder_info
    
    def _collect_reminders(self, results: List[Dict]) -> List[Dict]:
        """Collect non-completed reminders from multiple results."""
        reminders = []
        for result in results:
            if result.get("count", 0) > 0:
                for reminder in result.get("reminders", []):
                    if not reminder.get('completed', False):
                        reminders.append(reminder)
        return reminders
    
    def _format_reminders(
        self,
        user_overdue: List[Dict],
        user_today: List[Dict],
        internal_overdue: List[Dict],
        internal_today: List[Dict]
    ) -> str:
        """
        Format reminders as XML with urgent overdue section and relative time.

        Overdue reminders are displayed with urgency indicators.
        Today's reminders use hybrid format with both relative and absolute time.
        """
        from utils.timezone_utils import format_relative_time, utc_now

        user_tz = get_user_preferences().timezone
        parts = ["<active_reminders>"]
        parts.append("<instruction>Reminders require immediate action. When one is due or overdue, notify The User even if mid-conversation. They cannot see reminders unless you voice them. Non-negotiable.</instruction>")

        # USER REMINDERS SECTION
        if user_overdue or user_today:
            parts.append("<user_reminders>")

            # Overdue user reminders
            if user_overdue:
                parts.append('<overdue urgent="true">')
                parts.append("<warning>⚠️ OVERDUE - REQUIRE IMMEDIATE ATTENTION</warning>")
                for reminder in user_overdue:
                    date_obj = parse_utc_time_string(reminder["reminder_date"])
                    relative_time = format_relative_time(date_obj)
                    parts.append(self._format_reminder_xml(reminder, relative_time))
                parts.append("<action>YOU MUST notify the user about these overdue reminders IMMEDIATELY.</action>")
                parts.append("</overdue>")

            # Today's user reminders
            if user_today:
                parts.append("<today>")
                now = utc_now()
                for reminder in user_today:
                    date_obj = parse_utc_time_string(reminder["reminder_date"])
                    local_time = convert_from_utc(date_obj, user_tz)

                    if date_obj > now:
                        relative_time = format_relative_time(date_obj)
                        time_str = format_datetime(local_time, 'short')
                        parts.append(self._format_reminder_xml(reminder, relative_time, time_str))
                    else:
                        formatted_time = format_datetime(local_time, 'date_time')
                        parts.append(self._format_reminder_xml(reminder, formatted_time))

                parts.append("<guidance>Please remind the user about these during the continuum when appropriate.</guidance>")
                parts.append("</today>")

            parts.append("</user_reminders>")

        # INTERNAL REMINDERS SECTION
        if internal_overdue or internal_today:
            parts.append("<internal_reminders>")

            # Overdue internal reminders
            if internal_overdue:
                parts.append("<overdue>")
                parts.append("<warning>⚠️ OVERDUE INTERNAL REMINDERS</warning>")
                for reminder in internal_overdue:
                    date_obj = parse_utc_time_string(reminder["reminder_date"])
                    relative_time = format_relative_time(date_obj)
                    parts.append(self._format_reminder_xml(reminder, relative_time))
                parts.append("</overdue>")

            # Today's internal reminders
            if internal_today:
                parts.append("<today>")
                now = utc_now()
                for reminder in internal_today:
                    date_obj = parse_utc_time_string(reminder["reminder_date"])
                    local_time = convert_from_utc(date_obj, user_tz)

                    if date_obj > now:
                        relative_time = format_relative_time(date_obj)
                        time_str = format_datetime(local_time, 'short')
                        parts.append(self._format_reminder_xml(reminder, relative_time, time_str))
                    else:
                        formatted_time = format_datetime(local_time, 'date_time')
                        parts.append(self._format_reminder_xml(reminder, formatted_time))

                parts.append("</today>")

            parts.append("</internal_reminders>")

        parts.append("</active_reminders>")
        return "\n".join(parts)

    def _format_reminder_xml(self, reminder: Dict, due: str, time: str = None) -> str:
        """Format a single reminder as XML element."""
        attrs = [
            f'id="{reminder["id"]}"',
            f'title="{reminder["encrypted__title"]}"',
            f'due="{due}"'
        ]
        if time:
            attrs.append(f'time="{time}"')

        if reminder.get('encrypted__description'):
            return f"<reminder {' '.join(attrs)}>\n<details>{reminder['encrypted__description']}</details>\n</reminder>"
        return f"<reminder {' '.join(attrs)}/>"