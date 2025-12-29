"""
Time tool for retrieving current date and time information.

This tool provides the authoritative current time to prevent hallucination
of temporal information. MIRA must use this tool when asked about current time.
"""

import logging
from typing import Dict, Any
from pydantic import BaseModel, Field

from utils.timezone_utils import utc_now, convert_from_utc, format_datetime
from utils.user_context import get_user_preferences
from tools.repo import Tool
from tools.registry import registry

logger = logging.getLogger(__name__)


class TimeToolConfig(BaseModel):
    """Configuration for the time_tool."""
    enabled: bool = Field(
        default=True,
        description="Whether this tool is enabled by default (should always be True)"
    )


# Register with registry
registry.register("time_tool", TimeToolConfig)


class TimeTool(Tool):
    """
    Provides current date and time information.

    This tool returns the authoritative current time in the user's timezone.
    Use this tool when the user asks about the current time, date, or day.
    """

    name = "time_tool"
    simple_description = "Get the current date and time"

    anthropic_schema = {
        "name": "time_tool",
        "description": (
            "Get the current date and time. Use this tool when the user asks "
            "\"what time is it?\", \"what's the date?\", \"what day is it?\", or "
            "similar temporal queries. Returns current time in user's timezone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["full", "time_only", "date_only", "day_only"],
                    "description": (
                        "Type of temporal information requested:\n"
                        "- full: complete date and time (default)\n"
                        "- time_only: just the time\n"
                        "- date_only: just the date\n"
                        "- day_only: just the day of the week"
                    )
                }
            },
            "required": []
        }
    }

    def __init__(self):
        """Initialize time tool."""
        super().__init__()
        logger.info("TimeTool initialized")

    def run(self, query_type: str = "full") -> Dict[str, Any]:
        """
        Execute the time tool to get current temporal information.

        Args:
            query_type: Type of information to return (full, time_only, date_only, day_only)

        Returns:
            Dictionary with current temporal information
        """
        try:
            # Get current time in UTC
            current_utc = utc_now()

            # Get user's timezone preference
            user_prefs = get_user_preferences()
            user_tz = user_prefs.timezone

            # Convert to user's local time
            local_time = convert_from_utc(current_utc, user_tz)

            # Extract components
            day_of_week = local_time.strftime('%A')
            date_str = local_time.strftime('%B %d, %Y')
            time_str = local_time.strftime('%-I:%M %p')
            timezone_abbr = local_time.strftime('%Z')

            # Build response based on query type
            if query_type == "time_only":
                response = f"It is {time_str} {timezone_abbr}."
            elif query_type == "date_only":
                response = f"Today is {date_str}."
            elif query_type == "day_only":
                response = f"Today is {day_of_week}."
            else:  # full
                response = f"It is {day_of_week}, {date_str} at {time_str} {timezone_abbr}."

            logger.info(f"Time tool executed: query_type={query_type}, response={response}")

            return {
                "success": True,
                "response": response,
                "timestamp_utc": current_utc.isoformat(),
                "timestamp_local": local_time.isoformat(),
                "timezone": user_tz
            }

        except Exception as e:
            error_msg = f"Failed to get current time: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg
            }
