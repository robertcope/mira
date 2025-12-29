"""
Google Calendar API client for MIRA.

Handles authenticated API calls with automatic token refresh.
User tokens stored via UserCredentialService.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from clients.vault_client import get_secret_data
from utils.timezone_utils import format_utc_iso, parse_utc_time_string, utc_now
from utils.user_credentials import UserCredentialService

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Safety flag: when True, update_event and delete_event are disabled
# Set to False when you trust MIRA with calendar modifications
READONLY_MODE = True


class GoogleCalendarClient:
    """
    Client for Google Calendar API with automatic token refresh.

    Each instance is user-scoped via UserCredentialService.
    """

    def __init__(self):
        """Initialize client - credentials loaded lazily."""
        self._service = None
        self._credentials: Optional[Credentials] = None

    def _load_credentials(self) -> Credentials:
        """Load and refresh credentials from UserCredentialService."""
        credential_service = UserCredentialService()
        tokens_json = credential_service.get_credential("oauth_tokens", "google_calendar")

        if not tokens_json:
            raise RuntimeError(
                "Google Calendar not authorized. "
                "Visit /v0/api/oauth/google/authorize to connect your account."
            )

        tokens = json.loads(tokens_json)

        # Get client credentials for refresh
        oauth_creds = get_secret_data('mira/google_calendar')

        # Build credentials object
        credentials = Credentials(
            token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_uri=tokens.get("token_uri", GOOGLE_TOKEN_URI),
            client_id=oauth_creds["client_id"],
            client_secret=oauth_creds["client_secret"],
            scopes=tokens.get("scopes", []),
        )

        # Check if expired and refresh if needed
        expiry_str = tokens.get("expiry")
        if expiry_str:
            expiry = parse_utc_time_string(expiry_str)
            # Refresh if expires within 5 minutes
            if expiry < utc_now() + timedelta(minutes=5):
                logger.info("Google Calendar token expired, refreshing...")
                credentials = self._refresh_credentials(credentials, credential_service)

        return credentials

    def _refresh_credentials(
        self,
        credentials: Credentials,
        credential_service: UserCredentialService
    ) -> Credentials:
        """Refresh expired credentials and update storage."""
        import google.auth.transport.requests

        if not credentials.refresh_token:
            raise RuntimeError(
                "No refresh token available. Re-authorize at /v0/api/oauth/google/authorize"
            )

        request = google.auth.transport.requests.Request()
        credentials.refresh(request)

        # Calculate new expiry
        expiry = utc_now() + timedelta(seconds=3600)  # Default 1 hour

        # Update stored tokens
        stored_tokens = {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "expiry": format_utc_iso(expiry),
            "scopes": list(credentials.scopes) if credentials.scopes else [],
        }

        credential_service.store_credential(
            credential_type="oauth_tokens",
            service_name="google_calendar",
            credential_value=json.dumps(stored_tokens)
        )

        logger.info("Google Calendar token refreshed successfully")
        return credentials

    def _get_service(self):
        """Get or create the Calendar API service."""
        if not self._service:
            self._credentials = self._load_credentials()
            self._service = build('calendar', 'v3', credentials=self._credentials)
        return self._service

    def list_calendars(self) -> List[Dict[str, Any]]:
        """
        List all calendars accessible to the user.

        Returns:
            List of calendar objects with id, summary, primary flag
        """
        service = self._get_service()

        calendars = []
        page_token = None

        while True:
            response = service.calendarList().list(pageToken=page_token).execute()

            for cal in response.get('items', []):
                calendars.append({
                    'id': cal['id'],
                    'summary': cal.get('summary', 'Unnamed'),
                    'primary': cal.get('primary', False),
                    'access_role': cal.get('accessRole'),
                    'background_color': cal.get('backgroundColor'),
                })

            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return calendars

    def get_events(
        self,
        calendar_id: str = 'primary',
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: int = 100,
        single_events: bool = True,
        order_by: str = 'startTime',
    ) -> List[Dict[str, Any]]:
        """
        Get events from a calendar.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            time_min: Start of time range (default: now)
            time_max: End of time range (default: 7 days from now)
            max_results: Maximum events to return
            single_events: Expand recurring events
            order_by: Sort order ('startTime' or 'updated')

        Returns:
            List of event objects
        """
        service = self._get_service()

        # Default time range: now to 7 days
        if not time_min:
            time_min = utc_now()
        if not time_max:
            time_max = time_min + timedelta(days=7)

        events = []
        page_token = None

        while len(events) < max_results:
            # Format as RFC3339 - Google requires either 'Z' suffix or +HH:MM offset, not both
            # Replace +00:00 with Z for cleaner format
            time_min_str = time_min.isoformat().replace('+00:00', 'Z')
            time_max_str = time_max.isoformat().replace('+00:00', 'Z')
            # Ensure Z suffix if no timezone info
            if not time_min_str.endswith('Z') and '+' not in time_min_str:
                time_min_str += 'Z'
            if not time_max_str.endswith('Z') and '+' not in time_max_str:
                time_max_str += 'Z'

            response = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=min(max_results - len(events), 250),
                singleEvents=single_events,
                orderBy=order_by,
                pageToken=page_token,
            ).execute()

            for event in response.get('items', []):
                events.append(self._format_event(event))

            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return events

    def _format_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Format a raw event into a cleaner structure."""
        start = event.get('start', {})
        end = event.get('end', {})

        return {
            'id': event['id'],
            'summary': event.get('summary', 'No title'),
            'description': event.get('description'),
            'location': event.get('location'),
            'start': start.get('dateTime') or start.get('date'),
            'end': end.get('dateTime') or end.get('date'),
            'all_day': 'date' in start,
            'status': event.get('status'),
            'html_link': event.get('htmlLink'),
            'attendees': [
                {
                    'email': a.get('email'),
                    'name': a.get('displayName'),
                    'response': a.get('responseStatus'),
                    'organizer': a.get('organizer', False),
                }
                for a in event.get('attendees', [])
            ],
            'organizer': event.get('organizer', {}).get('email'),
            'recurring': 'recurringEventId' in event,
        }

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        calendar_id: str = 'primary',
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        all_day: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new calendar event.

        Args:
            summary: Event title
            start: Start datetime
            end: End datetime
            calendar_id: Target calendar
            description: Event description
            location: Event location
            attendees: List of attendee email addresses
            all_day: Whether this is an all-day event

        Returns:
            Created event object
        """
        service = self._get_service()

        event_body: Dict[str, Any] = {
            'summary': summary,
        }

        if all_day:
            event_body['start'] = {'date': start.strftime('%Y-%m-%d')}
            event_body['end'] = {'date': end.strftime('%Y-%m-%d')}
        else:
            event_body['start'] = {'dateTime': start.isoformat(), 'timeZone': 'UTC'}
            event_body['end'] = {'dateTime': end.isoformat(), 'timeZone': 'UTC'}

        if description:
            event_body['description'] = description
        if location:
            event_body['location'] = location
        if attendees:
            event_body['attendees'] = [{'email': email} for email in attendees]

        event = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            sendUpdates='all' if attendees else 'none',
        ).execute()

        return self._format_event(event)

    def update_event(
        self,
        event_id: str,
        calendar_id: str = 'primary',
        summary: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing event.

        Args:
            event_id: Event ID to update
            calendar_id: Calendar containing the event
            summary: New title (optional)
            start: New start time (optional)
            end: New end time (optional)
            description: New description (optional)
            location: New location (optional)

        Returns:
            Updated event object
        """
        if READONLY_MODE:
            raise PermissionError(
                "Calendar modifications are disabled (READONLY_MODE=True). "
                "Set READONLY_MODE=False in utils/google_calendar_client.py to enable."
            )

        service = self._get_service()

        # Get existing event
        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()

        # Update fields
        if summary is not None:
            event['summary'] = summary
        if description is not None:
            event['description'] = description
        if location is not None:
            event['location'] = location
        if start is not None:
            is_all_day = 'date' in event.get('start', {})
            if is_all_day:
                event['start'] = {'date': start.strftime('%Y-%m-%d')}
            else:
                event['start'] = {'dateTime': start.isoformat(), 'timeZone': 'UTC'}
        if end is not None:
            is_all_day = 'date' in event.get('end', {})
            if is_all_day:
                event['end'] = {'date': end.strftime('%Y-%m-%d')}
            else:
                event['end'] = {'dateTime': end.isoformat(), 'timeZone': 'UTC'}

        updated = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event,
        ).execute()

        return self._format_event(updated)

    def delete_event(
        self,
        event_id: str,
        calendar_id: str = 'primary',
    ) -> bool:
        """
        Delete an event.

        Args:
            event_id: Event ID to delete
            calendar_id: Calendar containing the event

        Returns:
            True if deleted successfully
        """
        if READONLY_MODE:
            raise PermissionError(
                "Calendar modifications are disabled (READONLY_MODE=True). "
                "Set READONLY_MODE=False in utils/google_calendar_client.py to enable."
            )

        service = self._get_service()

        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()

        return True

    def get_free_busy(
        self,
        time_min: datetime,
        time_max: datetime,
        calendar_ids: Optional[List[str]] = None,
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        Get free/busy information for calendars.

        Args:
            time_min: Start of query range
            time_max: End of query range
            calendar_ids: List of calendar IDs (default: primary)

        Returns:
            Dict mapping calendar IDs to lists of busy periods
        """
        service = self._get_service()

        if not calendar_ids:
            calendar_ids = ['primary']

        # Format as RFC3339 - replace +00:00 with Z
        time_min_str = time_min.isoformat().replace('+00:00', 'Z')
        time_max_str = time_max.isoformat().replace('+00:00', 'Z')
        if not time_min_str.endswith('Z') and '+' not in time_min_str:
            time_min_str += 'Z'
        if not time_max_str.endswith('Z') and '+' not in time_max_str:
            time_max_str += 'Z'

        body = {
            'timeMin': time_min_str,
            'timeMax': time_max_str,
            'items': [{'id': cal_id} for cal_id in calendar_ids],
        }

        response = service.freebusy().query(body=body).execute()

        result = {}
        for cal_id, data in response.get('calendars', {}).items():
            result[cal_id] = data.get('busy', [])

        return result
