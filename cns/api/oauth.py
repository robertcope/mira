"""
Google OAuth 2.0 callback flow for MIRA.

Handles authorization URL generation and callback for Google services.
Per-user tokens stored via UserCredentialService.
"""
import json
import logging
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from auth.api import get_current_user
from clients.vault_client import get_secret_data
from cns.api.base import create_error_response, create_success_response, generate_request_id
from utils.timezone_utils import format_utc_iso, utc_now
from utils.user_context import set_current_user_id
from utils.user_credentials import UserCredentialService

logger = logging.getLogger(__name__)
router = APIRouter()

# OAuth state storage (in-memory for single-user mode)
# Maps state -> {user_id, redirect_after, created_at}
_oauth_states: Dict[str, Dict[str, Any]] = {}

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Google Calendar scopes
GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_google_oauth_credentials() -> Dict[str, str]:
    """Get Google OAuth client credentials from Vault."""
    try:
        creds = get_secret_data('mira/google_calendar')
        if not creds.get('client_id') or not creds.get('client_secret'):
            raise RuntimeError("Missing client_id or client_secret in mira/google_calendar")
        return creds
    except Exception as e:
        logger.error(f"Failed to get Google OAuth credentials: {e}")
        raise RuntimeError(
            "Google Calendar OAuth not configured. "
            "Store client_id and client_secret in Vault at mira/google_calendar"
        ) from e


def _build_redirect_uri(request: Request) -> str:
    """Build the OAuth redirect URI from request context."""
    # Use X-Forwarded headers if behind proxy, otherwise use request URL
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    return f"{scheme}://{host}/v0/api/oauth/google/callback"


@router.get("/oauth/google/authorize")
async def google_oauth_authorize(
    request: Request,
    redirect_after: Optional[str] = Query(None, description="URL to redirect after auth"),
):
    """
    Start Google OAuth authorization flow.

    In single-user mode, redirects directly to Google.
    No authentication required - uses app.state.single_user_id.
    """
    try:
        # Get single user ID from app state (set during startup)
        user_id = request.app.state.single_user_id
        if not user_id:
            return HTMLResponse(
                content="<h1>Error</h1><p>Single user not configured</p>",
                status_code=500
            )

        creds = _get_google_oauth_credentials()

        # Generate secure state parameter
        state = secrets.token_urlsafe(32)

        # Store state with user context
        _oauth_states[state] = {
            "user_id": user_id,
            "redirect_after": redirect_after,
            "created_at": format_utc_iso(utc_now())
        }

        # Build authorization URL
        redirect_uri = _build_redirect_uri(request)

        params = {
            "client_id": creds["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_CALENDAR_SCOPES),
            "state": state,
            "access_type": "offline",  # Get refresh token
            "prompt": "consent",  # Force consent to get refresh token
        }

        auth_url = f"{GOOGLE_AUTH_URI}?{urlencode(params)}"

        # Redirect directly to Google
        return RedirectResponse(url=auth_url)

    except Exception as e:
        logger.error(f"Error starting OAuth flow: {e}", exc_info=True)
        return HTMLResponse(
            content=f"<h1>Error</h1><p>{str(e)}</p>",
            status_code=500
        )


@router.get("/oauth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """
    Handle Google OAuth callback.

    Exchanges authorization code for tokens and stores them.
    """
    # Handle OAuth errors
    if error:
        logger.error(f"OAuth error: {error} - {error_description}")
        return HTMLResponse(
            content=f"<h1>Authorization Failed</h1><p>{error_description or error}</p>",
            status_code=400
        )

    if not code or not state:
        return HTMLResponse(
            content="<h1>Invalid Request</h1><p>Missing code or state parameter</p>",
            status_code=400
        )

    # Validate state
    if state not in _oauth_states:
        logger.error(f"Invalid OAuth state: {state}")
        return HTMLResponse(
            content="<h1>Invalid State</h1><p>OAuth state expired or invalid. Please try again.</p>",
            status_code=400
        )

    state_data = _oauth_states.pop(state)  # Remove state after use
    user_id = state_data["user_id"]
    redirect_after = state_data.get("redirect_after")

    try:
        # Set user context for credential storage
        set_current_user_id(user_id)

        creds = _get_google_oauth_credentials()
        redirect_uri = _build_redirect_uri(request)

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                GOOGLE_TOKEN_URI,
                data={
                    "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                }
            )

        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            return HTMLResponse(
                content=f"<h1>Token Exchange Failed</h1><p>{token_response.text}</p>",
                status_code=400
            )

        token_data = token_response.json()

        # Calculate expiry time
        from datetime import timedelta
        expires_in = token_data.get("expires_in", 3600)
        expiry = utc_now() + timedelta(seconds=expires_in)

        # Store tokens
        stored_tokens = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "token_uri": GOOGLE_TOKEN_URI,
            "expiry": format_utc_iso(expiry),
            "scopes": GOOGLE_CALENDAR_SCOPES,
        }

        credential_service = UserCredentialService()

        # Preserve existing refresh_token if new one not provided
        existing = credential_service.get_credential("oauth_tokens", "google_calendar")
        if existing and not stored_tokens["refresh_token"]:
            existing_data = json.loads(existing)
            stored_tokens["refresh_token"] = existing_data.get("refresh_token")

        credential_service.store_credential(
            credential_type="oauth_tokens",
            service_name="google_calendar",
            credential_value=json.dumps(stored_tokens)
        )

        logger.info(f"Stored Google Calendar OAuth tokens for user {user_id}")

        # Redirect to success page or configured redirect
        if redirect_after:
            return RedirectResponse(url=redirect_after)

        return HTMLResponse(
            content="""
            <h1>Authorization Successful</h1>
            <p>Google Calendar has been connected to MIRA.</p>
            <p>You can close this window.</p>
            """,
            status_code=200
        )

    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return HTMLResponse(
            content=f"<h1>Error</h1><p>{str(e)}</p>",
            status_code=500
        )


@router.delete("/oauth/google/revoke")
async def google_oauth_revoke(
    response: Response,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Revoke Google Calendar OAuth tokens."""
    request_id = generate_request_id()

    try:
        credential_service = UserCredentialService()
        deleted = credential_service.delete_credential("oauth_tokens", "google_calendar")

        api_response = create_success_response(
            data={
                "revoked": deleted,
                "message": "Google Calendar authorization revoked" if deleted else "No authorization found"
            },
            meta={"request_id": request_id}
        )
        return api_response.to_dict()

    except Exception as e:
        logger.error(f"Error revoking OAuth: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()


@router.get("/oauth/google/status")
async def google_oauth_status(
    response: Response,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Check Google Calendar OAuth status."""
    request_id = generate_request_id()

    try:
        credential_service = UserCredentialService()
        tokens_json = credential_service.get_credential("oauth_tokens", "google_calendar")

        if not tokens_json:
            api_response = create_success_response(
                data={
                    "authorized": False,
                    "message": "Google Calendar not connected"
                },
                meta={"request_id": request_id}
            )
            return api_response.to_dict()

        tokens = json.loads(tokens_json)
        has_refresh = bool(tokens.get("refresh_token"))

        api_response = create_success_response(
            data={
                "authorized": True,
                "has_refresh_token": has_refresh,
                "scopes": tokens.get("scopes", []),
            },
            meta={"request_id": request_id}
        )
        return api_response.to_dict()

    except Exception as e:
        logger.error(f"Error checking OAuth status: {e}", exc_info=True)
        api_response = create_error_response(e, request_id)
        response.status_code = 500
        return api_response.to_dict()
