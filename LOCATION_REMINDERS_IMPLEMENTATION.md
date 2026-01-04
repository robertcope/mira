# Location-Based Reminders - Implementation Summary

## Overview

MIRA now supports location-based reminders that trigger automatically when you arrive at specific locations. This implementation uses OwnTracks for location tracking and integrates seamlessly with MIRA's existing reminder system.

## What Was Implemented

### 1. **Enhanced Reminder Schema** ([reminder_tool.py:165-214](tools/implementations/reminder_tool.py#L165-L214))

Added location fields to the SQLite reminders table:
- `location_trigger` (boolean): Whether this is a location-based reminder
- `encrypted__place_name` (text): User-friendly place name ("ALARA")
- `place_id` (text): Google Maps Place ID for accuracy
- `coordinates_lat/lng` (real): Resolved coordinates
- `trigger_radius_meters` (integer): Geofence radius (default 100m)
- `last_triggered_at` (timestamp): For debouncing

### 2. **Location API Endpoints** ([cns/api/location.py](cns/api/location.py))

Two new REST endpoints:

**POST `/v0/api/location/update`**
- Receives location updates from OwnTracks
- Stores current location in Valkey (30-minute TTL)
- Automatically evaluates location-based reminders
- Returns list of triggered reminders
- Supports Bearer token and Basic auth

**GET `/v0/api/location/current`**
- Retrieves user's current location from Valkey
- Shows TTL and last update time
- Useful for debugging location tracking

### 3. **Location Reminder Service** ([cns/services/location_reminder_service.py](cns/services/location_reminder_service.py))

Core geofence evaluation logic:
- `check_location_reminders()`: Main evaluation function
- `haversine_distance()`: Calculates distance between coordinates
- Debouncing: Prevents spam by enforcing 1-hour cooldown
- Google Chat notifications when reminders trigger
- Updates `last_triggered_at` timestamp

### 4. **Updated Reminder Tool** ([tools/implementations/reminder_tool.py](tools/implementations/reminder_tool.py))

Enhanced `reminder_tool` operations:

**`add_reminder`** - Now supports location-based creation:
```python
reminder_tool(
    operation="add_reminder",
    title="get my keys",
    location_name="ALARA",  # NEW: triggers geocoding
    trigger_radius_meters=100  # Optional: default 100m
)
```

**`get_reminders`** - New `date_type="location"`:
```python
reminder_tool(
    operation="get_reminders",
    date_type="location"  # NEW: fetches all location-based reminders
)
```

**`_resolve_location()`** - New helper method:
- Geocodes location names using `maps_tool`
- Returns coordinates, place_id, formatted_address
- Handles failures gracefully with clear error messages

### 5. **ReminderManager Trinket** ([working_memory/trinkets/reminder_manager.py](working_memory/trinkets/reminder_manager.py))

Updated notification center to display location reminders:

```xml
<location_reminders>
  <instruction>These reminders trigger when you arrive at specific locations.</instruction>
  <location_reminder id="rem_a1b2c3d4" title="get my keys" place="ALARA" radius="100m">
    <details>Don't forget the USB drive too</details>
  </location_reminder>
  <guidance>Inform the user about location-based reminders if they ask about active reminders.</guidance>
</location_reminders>
```

### 6. **Main App Integration** ([main.py:25,514](main.py#L25))

Registered location router:
```python
from cns.api import location
app.include_router(location.router, prefix="/v0/api", tags=["location"])
```

### 7. **Comprehensive Documentation** ([docs/LOCATION_REMINDERS_SETUP.md](docs/LOCATION_REMINDERS_SETUP.md))

Complete setup guide covering:
- OwnTracks installation and configuration (Android & iOS)
- Authentication setup
- Testing and troubleshooting
- Battery optimization tips
- API specification
- Privacy and security best practices

## Architecture

```
┌─────────────────┐
│   OwnTracks App │ (User's phone)
│   (GPS tracking)│
└────────┬────────┘
         │ HTTP POST /v0/api/location/update
         │ {"lat": 37.7749, "lon": -122.4194, "acc": 10}
         ▼
┌─────────────────────────────────────────┐
│         MIRA Location API               │
│  [cns/api/location.py]                  │
│  - Verify authentication                │
│  - Store location in Valkey (30min TTL) │
│  - Call check_location_reminders()      │
└────────┬────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│   Location Reminder Service                  │
│   [cns/services/location_reminder_service.py]│
│   - Fetch location-based reminders           │
│   - Calculate haversine distance             │
│   - Check geofence (distance <= radius)      │
│   - Apply debouncing (1-hour cooldown)       │
│   - Send Google Chat notification            │
│   - Update last_triggered_at                 │
└──────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│   Reminder Tool (SQLite)    │
│   [tools/implementations/   │
│    reminder_tool.py]        │
│   - User-scoped database    │
│   - Location fields stored  │
│   - Coordinates from Maps   │
└─────────────────────────────┘
```

## User Workflow

### Creating Location Reminder

**User says:** "Remind me when I'm at ALARA to get my keys"

**MIRA processes:**
1. LLM recognizes location-based reminder intent
2. Calls `reminder_tool(operation="add_reminder", title="get my keys", location_name="ALARA")`
3. `_resolve_location("ALARA")` → calls `maps_tool(operation="geocode", query="ALARA")`
4. Stores reminder with coordinates: `{lat: 37.7749, lng: -122.4194, radius: 100m}`
5. Responds: "Location-based reminder created for 'ALARA' (123 Main St) with 100m radius"

### Location Update & Trigger

**OwnTracks sends:** Every 5 minutes when user moves
```json
POST /v0/api/location/update
{
  "lat": 37.7750,
  "lon": -122.4195,
  "acc": 8
}
```

**MIRA evaluates:**
1. Stores location in Valkey: `location:user_id` → `{lat, lng, timestamp}`
2. Queries location-based reminders: `date_type="location"`
3. Calculates distance: `haversine_distance(37.7750, -122.4195, 37.7749, -122.4194)` = 15m
4. Checks: `15m <= 100m` ✓ and `last_triggered_at` > 1 hour ago ✓
5. **TRIGGERS**: Sends Google Chat notification
6. Updates `last_triggered_at` = now

**User receives:** Google Chat message:
```
📍 Location Reminder: get my keys
You're at ALARA (15m away)

Don't forget the USB drive too
```

## Key Design Decisions

### ✅ Privacy-First
- **No persistent location history**: Current location stored in Valkey with 30-minute TTL, then auto-deleted
- **Opt-in tracking**: User must explicitly configure OwnTracks
- **User-scoped**: All location data isolated by user_id via contextvars/RLS

### ✅ Battery-Efficient Polling
- **Server-side geofencing**: No continuous GPS required
- **Move-based updates**: OwnTracks only reports on significant movement (100m+)
- **Configurable interval**: Default 5 minutes, user can adjust

### ✅ Fail-Fast Infrastructure
- Location API raises exceptions on Valkey/database failures (no silent degradation)
- Missing Google Maps API key fails reminder creation immediately
- Authentication failures return 401, not silent fallback

### ✅ Debouncing to Prevent Spam
- **1-hour cooldown**: Same reminder won't trigger again for 1 hour
- **`last_triggered_at` tracking**: Stored in SQLite per reminder
- **Prevents notification spam**: User won't get bombarded while at a location

### ✅ Leveraging Existing Infrastructure
- **Maps tool integration**: Reuses existing `maps_tool` for geocoding
- **Reminder tool extension**: Builds on SQLite-based reminder system
- **Google Chat notifications**: Uses existing `GoogleChatNotifier`
- **User isolation**: Automatic via contextvars and RLS

## Testing the Implementation

### 1. Test Reminder Creation

```bash
# Via MIRA chat
curl -X POST https://your-mira-instance.com/v0/api/chat \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Remind me when I'\''m at ALARA to get my keys"}'
```

### 2. Test Location Update

```bash
# Manually send location
curl -X POST https://your-mira-instance.com/v0/api/location/update \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "lat": 37.7749,
    "lon": -122.4194,
    "acc": 10,
    "tst": '$(date +%s)'
  }'
```

### 3. Check Current Location

```bash
curl -X GET https://your-mira-instance.com/v0/api/location/current \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 4. View Location Reminders

```bash
# Via MIRA chat
curl -X POST https://your-mira-instance.com/v0/api/chat \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me my location reminders"}'
```

## Future Enhancements (Not Implemented)

### Potential Improvements

1. **Departure Reminders**
   - "Remind me when I leave work to stop by the store"
   - Would need `trigger_type: "arrival" | "departure"`

2. **Time + Location Conditions**
   - "Remind me on weekdays when I'm at the office to check email"
   - Would need schedule + location combo logic

3. **Proximity Warnings**
   - "Warn me when I'm within 5km of home so I can prep dinner"
   - Pre-arrival notifications

4. **Historical Location Inference**
   - "Where was I on Tuesday at 3pm?"
   - Would require persistent location history (privacy tradeoff)

5. **Native Mobile App**
   - Custom MIRA app with built-in geofencing
   - Better battery optimization
   - Richer notifications

6. **Multi-Device Support**
   - Aggregate location from multiple devices
   - Smart device selection (prefer phone over laptop)

## Files Modified/Created

### New Files
- `cns/api/location.py` - Location API endpoints
- `cns/services/location_reminder_service.py` - Geofence evaluation service
- `docs/LOCATION_REMINDERS_SETUP.md` - User setup guide
- `LOCATION_REMINDERS_IMPLEMENTATION.md` - This file

### Modified Files
- `tools/implementations/reminder_tool.py` - Added location fields and operations
- `working_memory/trinkets/reminder_manager.py` - Display location reminders in HUD
- `main.py` - Register location router

## Dependencies

### Required
- **Google Maps API**: For geocoding location names
- **Valkey/Redis**: For ephemeral location storage
- **OwnTracks**: For location reporting (user-installed app)

### Optional
- **Google Chat**: For push notifications (can use other notification methods)

## Deployment Notes

1. **Environment Variables**: Ensure `GOOGLE_MAPS_API_KEY` is set in config
2. **Valkey TTL**: Location data expires after 30 minutes automatically
3. **HTTPS Required**: OwnTracks requires HTTPS for security
4. **Authentication**: Generate API tokens for users before configuring OwnTracks
5. **Database Migration**: SQLite tables auto-migrate with ALTER TABLE (safe)

## Security Considerations

### Authentication
- Location endpoint requires Bearer token or Basic auth
- Token validation via existing JWT infrastructure
- User_id extracted from token, not user-provided

### Data Protection
- Location stored with user_id scoping (RLS)
- No cross-user location access possible
- Valkey TTL ensures automatic cleanup

### Rate Limiting (Future)
- Consider rate limiting location updates (e.g., max 1/minute)
- Prevents abuse/spam of location endpoint

## Conclusion

This implementation provides a robust, privacy-preserving location-based reminder system for MIRA. It leverages OwnTracks for passive location tracking, integrates seamlessly with existing infrastructure, and maintains MIRA's fail-fast philosophy.

Users can now create reminders like "Remind me when I'm at ALARA to get my keys" and receive automatic Google Chat notifications when they arrive at those locations.

**Status**: ✅ Fully implemented and ready for production use.
