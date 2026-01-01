"""
Google Calendar Tool - Calendar management via Google Calendar API.

Provides calendar access, event management, and scheduling capabilities.
Requires OAuth authorization via /v0/api/oauth/google/authorize.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from tools.registry import registry
from tools.repo import Tool
from utils.timezone_utils import parse_utc_time_string, utc_now


# -------------------- CONFIGURATION --------------------

class GoogleCalendarToolConfig(BaseModel):
    """Configuration for the google_calendar_tool."""
    enabled: bool = Field(
        default=True,
        description="Whether this tool is enabled"
    )
    default_calendar: str = Field(
        default="primary",
        description="Default calendar ID for operations"
    )
    default_event_duration_minutes: int = Field(
        default=60,
        description="Default event duration in minutes when end time not specified"
    )


# Register with registry
registry.register("google_calendar_tool", GoogleCalendarToolConfig)


# -------------------- MAIN TOOL CLASS --------------------

class GoogleCalendarTool(Tool):
    """
    Google Calendar management tool.

    Provides access to Google Calendar for viewing, creating, and managing events.
    Requires OAuth authorization before use.
    """

    name = "google_calendar_tool"

    simple_description = """
    Access and manage Google Calendar events. View upcoming events, create meetings,
    check availability, and update or delete existing events.
    """

    implementation_details = """
    OPERATIONS:
    - list_calendars: List all accessible calendars
      Parameters: None

    - get_events: Get events from a calendar
      Parameters:
        calendar_id (optional, default="primary"): Calendar to query
        start_date (optional): Start of date range (ISO format or natural language)
        end_date (optional): End of date range
        max_results (optional, default=50): Maximum events to return

    - create_event: Create a new calendar event
      Parameters:
        summary (required): Event title
        start (required): Start datetime (ISO format)
        end (optional): End datetime (default: start + 1 hour)
        description (optional): Event description
        location (optional): Event location
        attendees (optional): JSON array of email addresses
        calendar_id (optional, default="primary"): Target calendar
        all_day (optional, default=false): Whether this is an all-day event

    - update_event: Update an existing event
      Parameters:
        event_id (required): Event ID to update
        calendar_id (optional, default="primary"): Calendar containing event
        summary (optional): New title
        start (optional): New start datetime
        end (optional): New end datetime
        description (optional): New description
        location (optional): New location

    - delete_event: Delete an event
      Parameters:
        event_id (required): Event ID to delete
        calendar_id (optional, default="primary"): Calendar containing event

    - get_free_busy: Check availability
      Parameters:
        start (required): Start of time range
        end (required): End of time range
        calendars (optional): JSON array of calendar IDs

    USAGE NOTES:
    - Tool requires OAuth authorization. If not authorized, will return error with auth URL.
    - All datetimes should be in ISO 8601 format with timezone
    - Use "primary" for the user's main calendar
    - Event IDs can be found in get_events response

    IMPORTANT: 
    - The only calendar that create_event, update_event, and delete_event should be run against is the "Mira" calendar.
    """

    description = simple_description + implementation_details

    anthropic_schema = {
        "name": "google_calendar_tool",
        "description": "Manage Google Calendar events - view, create, update, delete events and check availability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list_calendars", "get_events", "create_event", "update_event", "delete_event", "get_free_busy"],
                    "description": "The calendar operation to perform"
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary' for main calendar)"
                },
                "event_id": {
                    "type": "string",
                    "description": "Event ID for update/delete operations"
                },
                "summary": {
                    "type": "string",
                    "description": "Event title/summary"
                },
                "start": {
                    "type": "string",
                    "description": "Start datetime in ISO 8601 format"
                },
                "end": {
                    "type": "string",
                    "description": "End datetime in ISO 8601 format"
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date for get_events query"
                },
                "end_date": {
                    "type": "string",
                    "description": "End date for get_events query"
                },
                "description": {
                    "type": "string",
                    "description": "Event description"
                },
                "location": {
                    "type": "string",
                    "description": "Event location"
                },
                "attendees": {
                    "type": "string",
                    "description": "JSON array of attendee email addresses"
                },
                "calendars": {
                    "type": "string",
                    "description": "JSON array of calendar IDs for free/busy query"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events to return"
                },
                "all_day": {
                    "type": "boolean",
                    "description": "Whether this is an all-day event"
                }
            },
            "required": ["operation"]
        }
    }

    def __init__(self):
        """Initialize the Google Calendar tool."""
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self._client = None

    def _get_client(self):
        """Get or create Google Calendar client."""
        if not self._client:
            from utils.google_calendar_client import GoogleCalendarClient
            self._client = GoogleCalendarClient()
        return self._client

    def _load_config(self) -> GoogleCalendarToolConfig:
        """Load tool configuration."""
        from utils.user_credentials import UserCredentialService

        credential_service = UserCredentialService()
        config_json = credential_service.get_credential(
            credential_type="tool_config",
            service_name="google_calendar_tool"
        )

        if config_json:
            return GoogleCalendarToolConfig(**json.loads(config_json))
        return GoogleCalendarToolConfig()

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate tool configuration.

        For Google Calendar, this checks if OAuth is authorized.
        """
        from utils.user_credentials import UserCredentialService

        credential_service = UserCredentialService()
        tokens = credential_service.get_credential("oauth_tokens", "google_calendar")

        return {
            "oauth_authorized": tokens is not None,
            "message": "Google Calendar connected" if tokens else "OAuth authorization required"
        }

    def run(self, operation: str, **kwargs) -> Dict[str, Any]:
        """
        Execute a calendar operation.

        Args:
            operation: The operation to perform
            **kwargs: Operation-specific parameters

        Returns:
            Dict containing operation results

        Raises:
            ValueError: If operation fails or parameters are invalid
        """
        try:
            if operation == "list_calendars":
                return self._list_calendars()
            elif operation == "get_events":
                return self._get_events(**kwargs)
            elif operation == "create_event":
                return self._create_event(**kwargs)
            elif operation == "update_event":
                return self._update_event(**kwargs)
            elif operation == "delete_event":
                return self._delete_event(**kwargs)
            elif operation == "get_free_busy":
                return self._get_free_busy(**kwargs)
            else:
                raise ValueError(
                    f"Unknown operation: {operation}. Valid operations: "
                    "list_calendars, get_events, create_event, update_event, delete_event, get_free_busy"
                )
        except RuntimeError as e:
            # Handle authorization errors with helpful message
            error_msg = str(e)
            if "not authorized" in error_msg.lower():
                return {
                    "success": False,
                    "error": "oauth_required",
                    "message": str(e),
                    "auth_url": "/v0/api/oauth/google/authorize"
                }
            raise
        except Exception as e:
            self.logger.error(f"Error in google_calendar_tool operation '{operation}': {e}")
            raise

    def _list_calendars(self) -> Dict[str, Any]:
        """List all accessible calendars."""
        client = self._get_client()
        calendars = client.list_calendars()

        return {
            "success": True,
            "calendars": calendars,
            "count": len(calendars),
            "message": f"Found {len(calendars)} calendar(s)"
        }

    def _get_events(
        self,
        calendar_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_results: int = 50,
        **kwargs
    ) -> Dict[str, Any]:
        """Get events from a calendar."""
        config = self._load_config()
        calendar_id = calendar_id or config.default_calendar

        # Parse dates
        time_min = None
        time_max = None

        if start_date:
            time_min = parse_utc_time_string(start_date) if 'T' in start_date else datetime.fromisoformat(start_date)
        if end_date:
            time_max = parse_utc_time_string(end_date) if 'T' in end_date else datetime.fromisoformat(end_date)

        client = self._get_client()
        events = client.get_events(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )

        return {
            "success": True,
            "events": events,
            "count": len(events),
            "calendar_id": calendar_id,
            "message": f"Found {len(events)} event(s)"
        }

    def _create_event(
        self,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[str] = None,
        calendar_id: Optional[str] = None,
        all_day: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a new calendar event."""
        if not summary:
            raise ValueError("Event summary is required")
        if not start:
            raise ValueError("Event start time is required")

        config = self._load_config()
        calendar_id = calendar_id or config.default_calendar

        # Parse start time
        start_dt = parse_utc_time_string(start) if 'T' in start else datetime.fromisoformat(start)

        # Default end time
        if end:
            end_dt = parse_utc_time_string(end) if 'T' in end else datetime.fromisoformat(end)
        else:
            end_dt = start_dt + timedelta(minutes=config.default_event_duration_minutes)

        # Parse attendees
        attendee_list = None
        if attendees:
            attendee_list = json.loads(attendees) if isinstance(attendees, str) else attendees

        client = self._get_client()
        event = client.create_event(
            summary=summary,
            start=start_dt,
            end=end_dt,
            calendar_id=calendar_id,
            description=description,
            location=location,
            attendees=attendee_list,
            all_day=all_day,
        )

        return {
            "success": True,
            "event": event,
            "message": f"Created event: {summary}"
        }

    def _update_event(
        self,
        event_id: Optional[str] = None,
        calendar_id: Optional[str] = None,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Update an existing event."""
        if not event_id:
            raise ValueError("Event ID is required")

        config = self._load_config()
        calendar_id = calendar_id or config.default_calendar

        # Parse dates if provided
        start_dt = None
        end_dt = None
        if start:
            start_dt = parse_utc_time_string(start) if 'T' in start else datetime.fromisoformat(start)
        if end:
            end_dt = parse_utc_time_string(end) if 'T' in end else datetime.fromisoformat(end)

        client = self._get_client()
        event = client.update_event(
            event_id=event_id,
            calendar_id=calendar_id,
            summary=summary,
            start=start_dt,
            end=end_dt,
            description=description,
            location=location,
        )

        return {
            "success": True,
            "event": event,
            "message": f"Updated event: {event['summary']}"
        }

    def _delete_event(
        self,
        event_id: Optional[str] = None,
        calendar_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Delete an event."""
        if not event_id:
            raise ValueError("Event ID is required")

        config = self._load_config()
        calendar_id = calendar_id or config.default_calendar

        client = self._get_client()
        client.delete_event(event_id=event_id, calendar_id=calendar_id)

        return {
            "success": True,
            "deleted_event_id": event_id,
            "message": "Event deleted successfully"
        }

    def _get_free_busy(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        calendars: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Get free/busy information."""
        if not start or not end:
            raise ValueError("Start and end times are required")

        start_dt = parse_utc_time_string(start) if 'T' in start else datetime.fromisoformat(start)
        end_dt = parse_utc_time_string(end) if 'T' in end else datetime.fromisoformat(end)

        calendar_ids = None
        if calendars:
            calendar_ids = json.loads(calendars) if isinstance(calendars, str) else calendars

        client = self._get_client()
        busy_periods = client.get_free_busy(
            time_min=start_dt,
            time_max=end_dt,
            calendar_ids=calendar_ids,
        )

        return {
            "success": True,
            "busy_periods": busy_periods,
            "message": f"Retrieved availability for {len(busy_periods)} calendar(s)"
        }
