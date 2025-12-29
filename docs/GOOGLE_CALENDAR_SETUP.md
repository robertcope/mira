# Google Calendar Integration Setup Guide

This guide walks you through enabling MIRA's Google Calendar integration, which allows MIRA to view, create, and manage calendar events on your behalf.

## Architecture Overview

MIRA's Google Calendar integration uses OAuth 2.0 for secure access:
- User authorizes MIRA via Google's consent flow
- Tokens stored per-user via `UserCredentialService`
- Automatic token refresh when expired
- Read/write calendar access (with optional read-only safety mode)

## Prerequisites

- Google Cloud Project with billing enabled
- OAuth consent screen configured
- MIRA instance accessible via HTTPS (required for OAuth callback)

## Step 1: Set Up Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select an existing one
3. Enable the **Google Calendar API**:
   - Navigate to "APIs & Services" → "Library"
   - Search for "Google Calendar API"
   - Click "Enable"

## Step 2: Configure OAuth Consent Screen

1. Go to "APIs & Services" → "OAuth consent screen"
2. Select **External** user type (or Internal if using Google Workspace)
3. Fill in required fields:
   - **App name**: MIRA
   - **User support email**: Your email
   - **Developer contact information**: Your email
4. Click "Save and Continue"
5. Add scopes:
   - Click "Add or Remove Scopes"
   - Add these scopes:
     - `https://www.googleapis.com/auth/calendar.readonly`
     - `https://www.googleapis.com/auth/calendar.events`
   - Click "Update"
6. Add test users (if External):
   - Add the Google accounts that will use MIRA
   - Required while app is in "Testing" status
7. Complete the wizard

## Step 3: Create OAuth Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Select **Web application**
4. Configure:
   - **Name**: MIRA Calendar
   - **Authorized redirect URIs**: Add your callback URL:
     ```
     https://your-domain.com/v0/api/oauth/google/callback
     ```
     Replace `your-domain.com` with your MIRA server domain
5. Click "Create"
6. **Save the Client ID and Client Secret** - you'll need these next

## Step 4: Store Credentials in Vault

Store the OAuth credentials in HashiCorp Vault:

```bash
vault kv put mira/google_calendar \
  client_id="YOUR_CLIENT_ID.apps.googleusercontent.com" \
  client_secret="YOUR_CLIENT_SECRET"
```

Or via the Vault UI:
- Path: `mira/google_calendar`
- Keys: `client_id`, `client_secret`

## Step 5: Authorize Your Account

1. Start your MIRA server
2. Navigate to the authorization URL:
   ```
   https://your-domain.com/v0/api/oauth/google/authorize
   ```
3. Sign in with your Google account
4. Review and accept the permissions:
   - "See, edit, share, and permanently delete all the calendars you can access using Google Calendar"
5. You'll see "Authorization Successful" on completion

## Step 6: Test the Integration

### Via MIRA Chat

Ask MIRA about your calendar:
- "What's on my calendar today?"
- "Do I have any meetings tomorrow?"
- "Create a meeting called 'Team Sync' tomorrow at 2pm"

### Via API

Check authorization status:
```bash
curl https://your-domain.com/v0/api/oauth/google/status \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Expected response:
```json
{
  "data": {
    "authorized": true,
    "has_refresh_token": true,
    "scopes": [
      "https://www.googleapis.com/auth/calendar.readonly",
      "https://www.googleapis.com/auth/calendar.events"
    ]
  }
}
```

## Tool Capabilities

### Operations Available

| Operation | Description | Parameters |
|-----------|-------------|------------|
| `list_calendars` | List all accessible calendars | None |
| `get_events` | Get events from a calendar | `calendar_id`, `start_date`, `end_date`, `max_results` |
| `create_event` | Create a new event | `summary`, `start`, `end`, `description`, `location`, `attendees`, `all_day` |
| `update_event` | Update an existing event | `event_id`, `summary`, `start`, `end`, `description`, `location` |
| `delete_event` | Delete an event | `event_id`, `calendar_id` |
| `get_free_busy` | Check availability | `start`, `end`, `calendars` |

### Read-Only Safety Mode

By default, `update_event` and `delete_event` are disabled via `READONLY_MODE=True` in [utils/google_calendar_client.py](../utils/google_calendar_client.py).

To enable calendar modifications:
```python
# In utils/google_calendar_client.py, line 25
READONLY_MODE = False
```

This safety flag prevents accidental calendar modifications while you build trust with the integration.

## Troubleshooting

### Authorization Errors

**"OAuth consent screen not configured"**
- Ensure OAuth consent screen is set up in Google Cloud Console
- Add your email as a test user if app is in "Testing" status

**"redirect_uri_mismatch"**
- Verify the callback URL in Google Cloud Console matches exactly:
  `https://your-domain.com/v0/api/oauth/google/callback`
- Check for trailing slashes, http vs https

**"No refresh token available"**
- Revoke access and re-authorize:
  1. Go to https://myaccount.google.com/permissions
  2. Remove MIRA's access
  3. Re-authorize via `/v0/api/oauth/google/authorize`

### Token Issues

**"Token has been expired or revoked"**
- Re-authorize via `/v0/api/oauth/google/authorize`
- Check that refresh token exists in credential storage

**"Invalid credentials"**
- Verify Vault credentials match Google Cloud Console
- Ensure `client_secret` wasn't truncated

### API Errors

**"Calendar not found"**
- Use `list_calendars` to see available calendar IDs
- Use `"primary"` for the user's main calendar

**"Permission denied"**
- Check OAuth scopes include calendar.events
- Verify calendar isn't read-only shared calendar

### Check OAuth Status

```bash
# Check if authorized
curl https://your-domain.com/v0/api/oauth/google/status \
  -H "Authorization: Bearer YOUR_API_KEY"

# Revoke authorization
curl -X DELETE https://your-domain.com/v0/api/oauth/google/revoke \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## API Reference

### OAuth Endpoints

**GET** `/v0/api/oauth/google/authorize`

Initiates OAuth flow. Redirects to Google consent screen.

Query parameters:
- `redirect_after` (optional): URL to redirect after successful auth

---

**GET** `/v0/api/oauth/google/callback`

OAuth callback handler. Exchanges code for tokens and stores them.

Query parameters (set by Google):
- `code`: Authorization code
- `state`: CSRF protection token
- `error`: Error code if authorization denied

---

**GET** `/v0/api/oauth/google/status`

Check current OAuth authorization status.

Response:
```json
{
  "data": {
    "authorized": true,
    "has_refresh_token": true,
    "scopes": ["..."]
  }
}
```

---

**DELETE** `/v0/api/oauth/google/revoke`

Revoke stored OAuth tokens.

Response:
```json
{
  "data": {
    "revoked": true,
    "message": "Google Calendar authorization revoked"
  }
}
```

## Implementation Files

- [cns/api/oauth.py](../cns/api/oauth.py) - OAuth flow handlers
- [utils/google_calendar_client.py](../utils/google_calendar_client.py) - Calendar API client
- [tools/implementations/google_calendar_tool.py](../tools/implementations/google_calendar_tool.py) - MIRA tool implementation

## Resources

- [Google Calendar API Documentation](https://developers.google.com/calendar)
- [OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google Cloud Console](https://console.cloud.google.com)
- [Manage Third-Party Access](https://myaccount.google.com/permissions)
