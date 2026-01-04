# Location-Based Reminders with OwnTracks

MIRA supports location-based reminders that trigger automatically when you arrive at specific locations. This guide walks through setting up OwnTracks to provide location updates to MIRA.

## Overview

**How it works:**
1. You create a location-based reminder: "Remind me when I'm at ALARA to get my keys"
2. MIRA geocodes "ALARA" to coordinates using Google Maps API
3. OwnTracks on your phone reports your location to MIRA every few minutes
4. When you enter the geofence (default 100m radius), MIRA sends a Google Chat notification

## Prerequisites

- MIRA instance running and accessible (e.g., `https://your-mira-instance.com`)
- Google Maps API key configured in MIRA (`config.google_maps_api_key`)
- OwnTracks app installed on your phone ([Android](https://play.google.com/store/apps/details?id=org.owntracks.android) | [iOS](https://apps.apple.com/app/owntracks/id692424691))
- Your MIRA API authentication token

## Step 1: Get Your Authentication Token

MIRA uses a single API key for authentication (shown during initial setup).

```bash
# Your API key from MIRA startup
export MIRA_TOKEN="mira_xxxxxxxxxxxxx"
```

If you don't have your API key, you can find it in Vault at `mira/api_keys` under the `mira_api` key.

## Step 2: Configure OwnTracks

### Android Configuration

1. Open OwnTracks app
2. Go to **Settings** (hamburger menu → Settings)
3. Navigate to **Connection** section
4. Set **Mode** to `HTTP`
5. Configure HTTP endpoint:

   ```
   URL: https://your-mira-instance.com/v0/api/location/update
   Authentication: Bearer <your-token>
   ```

   **Detailed settings:**
   - **URL**: `https://your-mira-instance.com/v0/api/location/update`
   - **Authentication**: Enable
   - **Username**: (leave empty)
   - **Password**: (leave empty)
   - **Headers**:
     - Add custom header: `Authorization: Bearer mira_xxxxxxxxxxxxx`
     - (Replace `mira_xxxxxxxxxxxxx` with your actual token)

6. **Tracking Settings** (optional but recommended):
   - **Location reporting**: Move
   - **Location accuracy**: High
   - **Move distance**: 100m (or less for more frequent updates)
   - **Minimum interval**: 5 minutes

7. Tap **Save** and enable location permissions for OwnTracks

### iOS Configuration

1. Open OwnTracks app
2. Tap settings icon (gear)
3. Tap **Mode** → Select `HTTP`
4. Configure HTTP settings:

   ```
   URL: https://your-mira-instance.com/v0/api/location/update
   Authentication: Bearer
   Token: mira_xxxxxxxxxxxxx
   ```

   **Detailed settings:**
   - **Mode**: HTTP
   - **URL**: `https://your-mira-instance.com/v0/api/location/update`
   - **Auth**: Bearer Token
   - **Token**: Your MIRA API token (paste full token including `mira_` prefix)

5. **Tracking Settings** (Settings → Tracking):
   - **Mode**: Move
   - **Monitoring**: Always
   - **Distance filter**: 100m
   - **Interval**: 300 seconds (5 minutes)

6. Grant location permissions: Settings → Privacy → Location Services → OwnTracks → **Always**

## Step 3: Test the Integration

### Verify Location Updates

1. Send a test location update manually via OwnTracks
2. Check MIRA logs for confirmation:

   ```bash
   # In MIRA logs, you should see:
   INFO - Location updated for user <user_id>: lat=37.7749, lon=-122.4194, acc=10m
   ```

3. Test the API endpoint directly:

   ```bash
   curl -X POST https://your-mira-instance.com/v0/api/location/update \
     -H "Authorization: Bearer mira_xxxxxxxxxxxxx" \
     -H "Content-Type: application/json" \
     -d '{
       "lat": 37.7749,
       "lon": -122.4194,
       "acc": 10,
       "tst": 1704067200
     }'
   ```

   Expected response:
   ```json
   {
     "success": true,
     "data": {
       "location_stored": true,
       "triggered_reminders": [],
       "message": "Location updated successfully. 0 reminder(s) triggered."
     }
   }
   ```

### Retrieve Current Location

```bash
curl -X GET https://your-mira-instance.com/v0/api/location/current \
  -H "Authorization: Bearer mira_xxxxxxxxxxxxx"
```

Expected response:
```json
{
  "success": true,
  "data": {
    "location": {
      "lat": 37.7749,
      "lng": -122.4194,
      "accuracy": 10,
      "timestamp": 1704067200,
      "updated_at": "2026-01-04T10:30:00Z"
    },
    "ttl_seconds": 1620
  }
}
```

## Step 4: Create Location-Based Reminders

### Via MIRA Chat

Simply tell MIRA:

```
"Remind me when I'm at ALARA to get my keys"
"Remind me when I'm at the grocery store to buy milk"
"When I'm next at the office, remind me to grab the USB drive"
```

MIRA will:
1. Use `reminder_tool` with `location_name` parameter
2. Geocode the location name via `maps_tool`
3. Store the reminder with coordinates and 100m default radius

### Via API (Programmatic)

```bash
curl -X POST https://your-mira-instance.com/v0/api/chat \
  -H "Authorization: Bearer mira_xxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Remind me when I'\''m at ALARA to get my keys"
  }'
```

### View Location-Based Reminders

```
"Show me my location reminders"
"What location-based reminders do I have?"
```

MIRA will query: `reminder_tool(operation="get_reminders", date_type="location")`

## How Location Triggering Works

### Geofence Evaluation

When OwnTracks sends a location update:

1. **Location Storage**: MIRA stores your location in Valkey with 24-hour TTL
2. **Freshness Check**: Only triggers reminders if location is less than 30 minutes old
3. **Reminder Query**: Fetches all active location-based reminders for your user
4. **Distance Calculation**: Uses haversine formula to calculate distance from each reminder location
5. **Geofence Check**: If distance ≤ trigger radius (default 100m), reminder triggers
6. **Debouncing**: Won't trigger same reminder again for 1 hour (prevents spam)
7. **Notification**: Sends Google Chat notification with reminder details

### Debouncing Logic

- Each reminder tracks `last_triggered_at` timestamp
- Minimum 1 hour between triggers for same reminder
- Prevents notification spam when you stay at a location
- After 1 hour, reminder can trigger again if you're still there

### Location Data Retention

- Current location stored in Valkey: **24 hours TTL**
- Location reminders only trigger on fresh data (≤30 minutes old)
- No persistent location history (privacy by design)
- Reminders store coordinates but not location history
- Location data automatically expires if OwnTracks stops reporting

## Advanced Configuration

### Custom Trigger Radius

```
"Remind me when I'm within 50 meters of ALARA to get my keys"
```

MIRA will extract the radius from natural language and pass:
```python
reminder_tool(
    operation="add_reminder",
    title="get my keys",
    location_name="ALARA",
    trigger_radius_meters=50
)
```

### Battery Optimization

OwnTracks can drain battery with frequent location updates. Recommended settings:

**Balanced (recommended):**
- Mode: Move
- Distance filter: 100m
- Interval: 5 minutes
- Monitoring: Significant location changes

**Battery Saver:**
- Mode: Move
- Distance filter: 250m
- Interval: 10 minutes
- Monitoring: Significant location changes only

**High Accuracy:**
- Mode: Continuous
- Distance filter: 50m
- Interval: 2 minutes
- Monitoring: Always

## Troubleshooting

### Location Updates Not Arriving

1. **Check OwnTracks connection status:**
   - Open app → Check status indicator (should be green/connected)
   - Settings → Connection → View log for errors

2. **Verify MIRA is receiving updates:**
   ```bash
   # Check MIRA logs
   tail -f /path/to/mira/logs/mira.log | grep "Location updated"
   ```

3. **Test endpoint manually:**
   ```bash
   curl -v -X POST https://your-mira-instance.com/v0/api/location/update \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"lat": 37.7749, "lon": -122.4194, "acc": 10}'
   ```

   - Check for HTTP 200 response
   - Verify `Authorization` header is correct
   - Ensure URL is reachable from phone's network

### Reminders Not Triggering

1. **Verify reminder was created as location-based:**
   ```
   "Show me my location reminders"
   ```
   Should show `location_trigger: true` and coordinates

2. **Check current location:**
   ```bash
   curl https://your-mira-instance.com/v0/api/location/current \
     -H "Authorization: Bearer YOUR_TOKEN"
   ```

3. **Manually trigger check:**
   ```bash
   # Send current location to force evaluation
   curl -X POST https://your-mira-instance.com/v0/api/location/update \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"lat": REMINDER_LAT, "lon": REMINDER_LNG, "acc": 5}'
   ```

4. **Check debouncing:**
   - Was this reminder triggered in the last hour?
   - Check reminder's `last_triggered_at` field

### Authentication Errors

**401 Unauthorized:**
- Verify token is correct
- Check token hasn't expired (if using JWT)
- Ensure `Authorization: Bearer` prefix is included

**403 Forbidden:**
- User may not have permission
- Check user account is active

### Location Inaccuracy

If reminders trigger too early/late:

1. **Increase trigger radius:**
   ```
   "Update my ALARA reminder to have a 200 meter radius"
   ```

2. **Improve OwnTracks accuracy:**
   - Settings → Location → Mode: High accuracy
   - Reduce distance filter to 50m
   - Use WiFi scanning for better accuracy

## Privacy & Security

### Data Retention

- **Current location**: 30 minutes in Valkey, then auto-deleted
- **Location history**: Not stored (privacy by design)
- **Reminder coordinates**: Stored permanently (until reminder completed/deleted)

### Security Best Practices

1. **Use HTTPS**: Always use `https://` for MIRA endpoint
2. **Secure tokens**: Treat API tokens like passwords
3. **Token rotation**: Regenerate tokens periodically
4. **Network security**: OwnTracks can use WiFi-only mode for home network

### Disable Location Tracking

To stop location updates:

1. **OwnTracks:** Settings → Monitoring: Manual/Off
2. **MIRA:** Location data expires automatically (24 hours)
3. **Delete reminders:** "Delete all my location reminders"

## FAQ

**Q: Does this work offline?**
A: No, OwnTracks requires internet connectivity to send location updates to MIRA.

**Q: How accurate is location detection?**
A: GPS accuracy varies (5-50m typical). MIRA uses geofence radius to account for this.

**Q: Can I use multiple devices?**
A: Yes, but only the most recent location update is stored. Last device to report wins.

**Q: What happens if I turn off OwnTracks?**
A: Location data remains queryable for 24 hours, but reminders won't trigger after 30 minutes of no updates (stale data safeguard).

**Q: Does this drain my battery?**
A: OwnTracks uses efficient location tracking, but frequent updates will use more battery. Use "Move" mode with 100m distance filter for balanced usage.

**Q: Can I use a different app instead of OwnTracks?**
A: Yes! Any app that can send HTTP POST requests with JSON body can work. See "API Specification" below.

## API Specification

### POST /v0/api/location/update

**Request:**
```json
{
  "lat": 37.7749,        // Required: Latitude (-90 to 90)
  "lon": -122.4194,      // Required: Longitude (-180 to 180)
  "acc": 10,             // Optional: Accuracy in meters
  "tst": 1704067200,     // Optional: Unix timestamp
  "alt": 100,            // Optional: Altitude in meters
  "batt": 85,            // Optional: Battery level (0-100)
  "tid": "AA"            // Optional: Tracker ID
}
```

**Response (success):**
```json
{
  "success": true,
  "data": {
    "location_stored": true,
    "triggered_reminders": [
      {
        "reminder_id": "rem_a1b2c3d4",
        "title": "get my keys",
        "place_name": "ALARA",
        "distance_meters": 45.2,
        "triggered_at": "2026-01-04T10:30:00Z"
      }
    ],
    "message": "Location updated successfully. 1 reminder(s) triggered."
  }
}
```

### GET /v0/api/location/current

**Response:**
```json
{
  "success": true,
  "data": {
    "location": {
      "lat": 37.7749,
      "lng": -122.4194,
      "accuracy": 10,
      "timestamp": 1704067200,
      "altitude": 100,
      "battery": 85,
      "tracker_id": "AA",
      "updated_at": "2026-01-04T10:30:00Z"
    },
    "ttl_seconds": 1620
  }
}
```

## Support

For issues or questions:
- GitHub Issues: https://github.com/anthropics/mira/issues
- Documentation: `/docs/LOCATION_REMINDERS_SETUP.md`
