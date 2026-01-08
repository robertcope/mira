"""Time manager trinket for current date/time injection."""
import logging
from typing import Dict, Any

from utils.timezone_utils import (
    utc_now, convert_from_utc, format_datetime
)
from utils.user_context import get_user_preferences
from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


class TimeManager(EventAwareTrinket):
    """
    Manages current date/time information for the notification center.

    Always generates fresh timestamp when requested.
    """
    
    def _get_variable_name(self) -> str:
        """Time manager publishes to 'datetime_section'."""
        return "datetime_section"
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate current date/time content with temporal landmarks.

        Args:
            context: Update context (unused for time manager)

        Returns:
            Formatted date/time section with today, tomorrow, this weekend, and next week
        """
        from datetime import timedelta

        current_time = utc_now()
        user_tz = get_user_preferences().timezone
        local_time = convert_from_utc(current_time, user_tz)

        # Format today with day of week and prettier display
        day_of_week = local_time.strftime('%A').upper()
        date_part = local_time.strftime('%B %d, %Y').upper()
        time_part = local_time.strftime('%-I:%M %p').upper()
        timezone_name = local_time.strftime('%Z')

        # Calculate temporal landmarks
        tomorrow = local_time + timedelta(days=1)
        tomorrow_str = tomorrow.strftime('%A, %B %d, %Y').upper()

        # Find this weekend (upcoming Saturday/Sunday)
        current_weekday = local_time.weekday()  # 0=Monday, 5=Saturday, 6=Sunday

        if current_weekday == 5:  # Saturday
            # This weekend is today and tomorrow
            saturday = local_time
            sunday = local_time + timedelta(days=1)
        elif current_weekday == 6:  # Sunday
            # This weekend is next Saturday/Sunday (6 days away)
            saturday = local_time + timedelta(days=6)
            sunday = saturday + timedelta(days=1)
        else:  # Monday through Friday
            # This weekend is the upcoming Saturday/Sunday
            days_until_saturday = 5 - current_weekday
            saturday = local_time + timedelta(days=days_until_saturday)
            sunday = saturday + timedelta(days=1)

        weekend_str = f"{saturday.strftime('%A %b %d').upper()} - {sunday.strftime('%A %b %d, %Y').upper()}"

        # Find next Monday (start of next week)
        if current_weekday == 6:  # Sunday
            # Next Monday is tomorrow
            next_monday = local_time + timedelta(days=1)
        else:
            # Next Monday is (7 - current_weekday) days away
            days_until_monday = 7 - current_weekday
            next_monday = local_time + timedelta(days=days_until_monday)

        next_week_str = next_monday.strftime('%A, %B %d, %Y').upper()

        datetime_info = (
            f"<current_datetime>TODAY IS {day_of_week}, {date_part} AT {time_part} {timezone_name}.\n"
            f"TOMORROW: {tomorrow_str}\n"
            f"THIS WEEKEND: {weekend_str}\n"
            f"NEXT WEEK STARTS: {next_week_str}</current_datetime>"
        )

        logger.debug(f"Generated datetime information for {day_of_week}, {date_part} at {time_part}")
        return datetime_info