"""
Time tool for retrieving current date and time information.

This tool provides the authoritative current time to prevent hallucination
of temporal information. MIRA must use this tool when asked about current time.
"""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
import parsedatetime as pdt

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
            "Get the current date and time, or parse natural language date/time expressions. "
            "Use this tool when the user asks \"what time is it?\", \"what's the date?\", or when they "
            "reference dates like \"next Tuesday\", \"3 days from now\", \"August 25th\", \"eod\", etc. "
            "Handles a wide range of natural language date/time formats. Returns dates in ISO format."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["full", "time_only", "date_only", "day_only", "relative_date"],
                    "description": (
                        "Type of temporal information requested:\n"
                        "- full: complete date and time (default)\n"
                        "- time_only: just the time\n"
                        "- date_only: just the date\n"
                        "- day_only: just the day of the week\n"
                        "- relative_date: parse a natural language date/time expression"
                    )
                },
                "relative_expression": {
                    "type": "string",
                    "description": (
                        "Required when query_type is 'relative_date'. Supports many formats:\n"
                        "Relative: 'next monday', 'this friday', 'tomorrow', 'yesterday', "
                        "'3 days from now', '2 weeks ago', 'next month'\n"
                        "Absolute: 'August 25th 2024', '25 Aug 2024', 'Aug 25 5pm'\n"
                        "Special: 'eod' (end of day), 'eom' (end of month), 'eoy' (end of year)\n"
                        "Complex: '5 hours before noon', '2 days from tomorrow', 'tomorrow eod'"
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

    def _calculate_relative_date(self, expression: str, current_time) -> Dict[str, Any]:
        """
        Calculate a date from a relative expression using parsedatetime.

        Args:
            expression: Natural language relative date expression
            current_time: The current datetime in user's timezone

        Returns:
            Dictionary with calculated date information
        """
        try:
            # Create parsedatetime calendar instance
            cal = pdt.Calendar()

            # Parse the expression relative to current_time
            # parseDT returns (datetime_obj, parse_status)
            # parse_status: 0=failed, 1=date, 2=time, 3=datetime
            target_date, parse_status = cal.parseDT(
                datetimeString=expression,
                sourceTime=current_time,
                tzinfo=current_time.tzinfo
            )

            # Check if parsing was successful
            if parse_status == 0:
                return {
                    "success": False,
                    "error": f"Could not parse relative date expression: '{expression}'. "
                            "Try formats like 'next monday', '3 days from now', 'tomorrow', "
                            "'August 25th', 'eod', '5 hours before noon', etc."
                }

            # Format the result
            date_str = target_date.strftime('%Y-%m-%d')
            day_of_week = target_date.strftime('%A')
            full_date_str = target_date.strftime('%B %d, %Y')

            return {
                "success": True,
                "date": date_str,
                "day_of_week": day_of_week,
                "full_date": full_date_str,
                "response": f"'{expression}' is {day_of_week}, {full_date_str} ({date_str})",
                "timestamp_local": target_date.isoformat(),
                "parse_status": parse_status  # 1=date, 2=time, 3=datetime
            }

        except Exception as e:
            logger.error(f"Failed to parse relative date expression '{expression}': {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": f"Error parsing date expression: {str(e)}"
            }

    def run(self, query_type: str = "full", relative_expression: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute the time tool to get current temporal information or calculate relative dates.

        Args:
            query_type: Type of information to return (full, time_only, date_only, day_only, relative_date)
            relative_expression: Required when query_type is 'relative_date'

        Returns:
            Dictionary with current temporal information or calculated date
        """
        try:
            # Get current time in UTC
            current_utc = utc_now()

            # Get user's timezone preference
            user_prefs = get_user_preferences()
            user_tz = user_prefs.timezone

            # Convert to user's local time
            local_time = convert_from_utc(current_utc, user_tz)

            # Handle relative date calculation
            if query_type == "relative_date":
                if not relative_expression:
                    return {
                        "success": False,
                        "error": "relative_expression is required when query_type is 'relative_date'"
                    }
                return self._calculate_relative_date(relative_expression, local_time)

            # Extract components for standard queries
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
