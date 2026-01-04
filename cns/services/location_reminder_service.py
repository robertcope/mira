"""
Location-based reminder evaluation service.

Checks user's current location against active location-based reminders
and triggers notifications when user enters geofence radius.
"""
import logging
import math
from typing import List, Dict, Any
from datetime import datetime, timedelta

from utils.user_context import set_current_user_id, get_current_user_id
from utils.timezone_utils import utc_now, format_utc_iso, parse_utc_time_string

logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calculate the great circle distance between two points using the haversine formula.

    Args:
        lat1: First point latitude
        lng1: First point longitude
        lat2: Second point latitude
        lng2: Second point longitude

    Returns:
        Distance in meters
    """
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371000  # Earth radius in meters
    return c * r


def check_location_reminders(user_id: str, lat: float, lng: float, timestamp: int = None) -> List[Dict[str, Any]]:
    """
    Check location-based reminders and trigger notifications if within geofence.

    Only triggers reminders if location data is fresh (within 30 minutes).

    Args:
        user_id: User ID
        lat: Current latitude
        lng: Current longitude
        timestamp: Unix timestamp of location update (optional, defaults to now)

    Returns:
        List of triggered reminder dicts
    """
    triggered = []

    try:
        # Check location freshness - only trigger on data less than 30 minutes old
        location_timestamp = timestamp if timestamp else int(utc_now().timestamp())
        location_age_seconds = int(utc_now().timestamp()) - location_timestamp

        if location_age_seconds > 1800:  # 30 minutes
            logger.debug(
                f"Location data too old for triggering reminders: {location_age_seconds}s "
                f"(max 1800s). Skipping reminder evaluation."
            )
            return triggered

        # Set user context for database queries
        set_current_user_id(user_id)

        # Get all active location-based reminders for this user
        from tools.implementations.reminder_tool import ReminderTool
        reminder_tool = ReminderTool()

        result = reminder_tool.run(operation="get_reminders", date_type="location", category="all")

        if not result.get("reminders"):
            logger.debug(f"No location-based reminders for user {user_id}")
            return triggered

        reminders = result["reminders"]
        logger.info(f"Checking {len(reminders)} location-based reminders for user {user_id}")

        # Evaluate each reminder
        for reminder in reminders:
            try:
                # Skip if already completed
                if reminder.get("completed"):
                    continue

                # Skip if no coordinates
                if not reminder.get("coordinates_lat") or not reminder.get("coordinates_lng"):
                    logger.warning(f"Reminder {reminder['id']} missing coordinates, skipping")
                    continue

                # Calculate distance
                reminder_lat = reminder["coordinates_lat"]
                reminder_lng = reminder["coordinates_lng"]
                distance = haversine_distance(lat, lng, reminder_lat, reminder_lng)

                trigger_radius = reminder.get("trigger_radius_meters", 100)

                logger.debug(
                    f"Reminder {reminder['id']} ({reminder['encrypted__place_name']}): "
                    f"distance={distance:.1f}m, radius={trigger_radius}m"
                )

                # Check if within geofence
                if distance <= trigger_radius:
                    # Check debouncing: don't trigger if triggered recently (within 1 hour)
                    last_triggered = reminder.get("last_triggered_at")
                    if last_triggered:
                        try:
                            last_triggered_dt = parse_utc_time_string(last_triggered)
                            time_since_last = (utc_now() - last_triggered_dt).total_seconds()

                            # Debounce: 1 hour minimum between triggers
                            if time_since_last < 3600:
                                logger.info(
                                    f"Reminder {reminder['id']} debounced "
                                    f"(triggered {int(time_since_last)}s ago)"
                                )
                                continue
                        except Exception as e:
                            logger.warning(f"Failed to parse last_triggered_at: {e}")

                    # Trigger the reminder!
                    logger.info(
                        f"🎯 Triggered location reminder {reminder['id']} at distance {distance:.1f}m "
                        f"from {reminder['encrypted__place_name']}"
                    )

                    # Send notification
                    _send_location_reminder_notification(user_id, reminder, distance)

                    # Update last_triggered_at
                    _update_reminder_trigger_time(reminder["id"])

                    triggered.append({
                        "reminder_id": reminder["id"],
                        "title": reminder["encrypted__title"],
                        "place_name": reminder["encrypted__place_name"],
                        "distance_meters": round(distance, 1),
                        "triggered_at": format_utc_iso(utc_now())
                    })

            except Exception as e:
                logger.error(f"Error evaluating reminder {reminder.get('id')}: {e}", exc_info=True)
                continue

        if triggered:
            logger.info(f"Triggered {len(triggered)} location reminder(s) for user {user_id}")

        return triggered

    except Exception as e:
        logger.error(f"Failed to check location reminders for user {user_id}: {e}", exc_info=True)
        return triggered


def _send_location_reminder_notification(user_id: str, reminder: Dict[str, Any], distance: float):
    """
    Send push notification for triggered location-based reminder.

    Args:
        user_id: User ID
        reminder: Reminder dict
        distance: Distance from location in meters
    """
    try:
        from utils.google_chat_spaces_repository import GoogleChatSpacesRepository
        from utils.google_chat_client import get_google_chat_client

        place_name = reminder.get("encrypted__place_name", "location")
        title = reminder.get("encrypted__title")
        description = reminder.get("encrypted__description", "")

        # Build notification message
        message_parts = [
            f"📍 **Location Reminder: {title}**",
            f"\nYou're at {place_name} ({distance:.0f}m away)"
        ]

        if description:
            message_parts.append(f"\n{description}")

        message = "\n".join(message_parts)

        # Get user's Google Chat space
        spaces_repo = GoogleChatSpacesRepository()
        space_info = spaces_repo.get_space_for_user(user_id)

        if not space_info:
            logger.info(f"User {user_id} has no Google Chat space configured, skipping notification")
            return

        # Get chat client and send message
        chat_client = get_google_chat_client()
        chat_client.send_message(
            space_name=space_info['space_name'],
            text=message,
            thread_key=None  # Location reminders go to main space, not threads
        )

        logger.info(f"Sent location reminder notification to user {user_id} for '{place_name}'")

    except Exception as e:
        logger.error(f"Failed to send location reminder notification: {e}", exc_info=True)


def _update_reminder_trigger_time(reminder_id: str):
    """
    Update reminder's last_triggered_at timestamp for debouncing.

    Args:
        reminder_id: Reminder ID
    """
    try:
        from tools.implementations.reminder_tool import ReminderTool
        reminder_tool = ReminderTool()

        # Update the reminder via direct database access
        update_data = {
            "last_triggered_at": format_utc_iso(utc_now()),
            "updated_at": format_utc_iso(utc_now())
        }

        reminder_tool.db.update(
            "reminders",
            update_data,
            "id = :id",
            {"id": reminder_id}
        )

        logger.debug(f"Updated last_triggered_at for reminder {reminder_id}")

    except Exception as e:
        logger.error(f"Failed to update reminder trigger time: {e}", exc_info=True)


def get_user_current_location(user_id: str) -> Dict[str, Any]:
    """
    Get user's current location from Valkey.

    Args:
        user_id: User ID

    Returns:
        Dict with lat, lng, accuracy, timestamp, ttl_seconds or None if not found
    """
    import json
    from clients.valkey_client import get_valkey

    try:
        valkey = get_valkey()
        valkey_key = f"location:{user_id}"
        location_json = valkey.get(valkey_key)

        if not location_json:
            return None

        location_data = json.loads(location_json)

        # Add TTL information
        ttl = valkey.ttl(valkey_key)
        location_data['ttl_seconds'] = ttl if ttl > 0 else 0

        return location_data

    except Exception as e:
        logger.error(f"Failed to get user location from Valkey: {e}")
        return None
