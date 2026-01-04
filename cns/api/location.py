"""
Location API endpoint for OwnTracks and location-based reminder integration.

Receives location updates from OwnTracks or other location tracking services,
stores current location in Valkey, and evaluates location-based reminders.
"""
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from .base import BaseHandler, APIResponse, create_success_response, create_error_response
from auth.api import get_current_user
from utils.user_context import set_current_user_id
from clients.valkey_client import get_valkey
from utils.timezone_utils import utc_now, format_utc_iso

logger = logging.getLogger(__name__)

router = APIRouter()


class LocationUpdate(BaseModel):
    """Location update request model."""
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lon: float = Field(..., ge=-180, le=180, description="Longitude")
    acc: Optional[float] = Field(None, description="Accuracy in meters")
    tst: Optional[int] = Field(None, description="Timestamp (Unix epoch)")
    alt: Optional[float] = Field(None, description="Altitude in meters")
    batt: Optional[int] = Field(None, ge=0, le=100, description="Battery level percentage")
    tid: Optional[str] = Field(None, description="Tracker ID")


class LocationAPIHandler(BaseHandler):
    """Handler for location update requests."""

    def process_request(self, location: LocationUpdate, user_id: str) -> APIResponse:
        """
        Process location update and check location-based reminders.

        Args:
            location: Location update data from OwnTracks
            user_id: Authenticated user ID

        Returns:
            APIResponse with triggered reminders (if any)
        """
        try:
            # Set user context for this request
            set_current_user_id(user_id)

            # Store location in Valkey with 24-hour TTL
            valkey = get_valkey()
            location_data = {
                "lat": location.lat,
                "lng": location.lon,  # Normalize to lng for consistency
                "accuracy": location.acc,
                "timestamp": location.tst if location.tst else int(utc_now().timestamp()),
                "altitude": location.alt,
                "battery": location.batt,
                "tracker_id": location.tid,
                "updated_at": format_utc_iso(utc_now())
            }

            valkey_key = f"location:{user_id}"
            valkey.setex(valkey_key, 86400, json.dumps(location_data))  # 24-hour TTL

            logger.info(
                f"Location updated for user {user_id}: "
                f"lat={location.lat}, lon={location.lon}, acc={location.acc}m"
            )

            # Check location-based reminders
            from cns.services.location_reminder_service import check_location_reminders

            triggered_reminders = check_location_reminders(
                user_id=user_id,
                lat=location.lat,
                lng=location.lon,
                timestamp=location_data["timestamp"]
            )

            return create_success_response({
                "location_stored": True,
                "triggered_reminders": triggered_reminders,
                "message": f"Location updated successfully. {len(triggered_reminders)} reminder(s) triggered."
            })

        except Exception as e:
            logger.error(f"Failed to process location update: {e}", exc_info=True)
            return create_error_response(
                code="LOCATION_UPDATE_FAILED",
                message=f"Failed to update location: {str(e)}"
            )


def get_location_handler() -> LocationAPIHandler:
    """Get location handler instance."""
    return LocationAPIHandler()


@router.post("/location/update")
async def update_location(
    location: LocationUpdate,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Update user location and trigger location-based reminders.

    Accepts location data from OwnTracks or compatible location tracking apps.
    Stores location in Valkey and evaluates geofences for active location-based reminders.

    Authentication:
    - Bearer token: `Authorization: Bearer <mira_api_key>`

    Request body (OwnTracks format):
    ```json
    {
        "lat": 37.7749,
        "lon": -122.4194,
        "acc": 10,
        "tst": 1704067200,
        "alt": 100,
        "batt": 85,
        "tid": "AA"
    }
    ```

    Returns:
    - 200: Location updated successfully
    - 401: Authentication failed
    - 500: Server error
    """
    try:
        handler = get_location_handler()
        response = handler.handle_request(location=location, user_id=user["user_id"])
        return response.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Location endpoint error: {e}", exc_info=True)
        return {
            "success": False,
            "error": {
                "code": "LOCATION_ENDPOINT_ERROR",
                "message": f"Location update failed: {str(e)}"
            }
        }


@router.get("/location/current")
async def get_current_location(user: Dict[str, Any] = Depends(get_current_user)):
    """
    Get user's current location from Valkey.

    Returns the most recent location update (within last 24 hours).

    Returns:
    - 200: Current location data
    - 404: No recent location data found
    - 401: Authentication failed
    """
    try:
        user_id = user["user_id"]
        set_current_user_id(user_id)

        valkey = get_valkey()
        valkey_key = f"location:{user_id}"
        location_json = valkey.get(valkey_key)

        if not location_json:
            return {
                "success": False,
                "error": {
                    "code": "NO_LOCATION_DATA",
                    "message": "No recent location data found (last 24 hours)"
                }
            }

        location_data = json.loads(location_json)

        return {
            "success": True,
            "data": {
                "location": location_data,
                "ttl_seconds": valkey.ttl(valkey_key)
            }
        }

    except Exception as e:
        logger.error(f"Failed to retrieve current location: {e}", exc_info=True)
        return {
            "success": False,
            "error": {
                "code": "LOCATION_RETRIEVAL_FAILED",
                "message": f"Failed to get current location: {str(e)}"
            }
        }
