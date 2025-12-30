"""
Google Chat JWT verification for webhook authentication.

Verifies that incoming webhook requests are actually from Google Chat by validating
the JWT bearer token in the Authorization header.

Configuration:
- Store 'google_chat_audience' field in Vault at 'mira/google_chat_config'
- Audience can be either:
  - HTTP Endpoint URL (e.g., 'https://your-domain.com/v0/api/google-chat')
  - Google Cloud Project Number (e.g., '1234567890')

Reference: https://developers.google.com/workspace/chat/verify-requests-from-chat
"""
import logging
from typing import Optional
import httpx
import jwt
from jwt import PyJWKClient
from functools import lru_cache

from clients.vault_client import get_secret_data

logger = logging.getLogger(__name__)

# Google Chat service account email - used to verify the token is from Chat
GOOGLE_CHAT_EMAIL = "chat@system.gserviceaccount.com"

# When Authentication Audience is set to "HTTP Endpoint URL", Google sends an
# ID token issued by accounts.google.com (not the Chat service account directly)
GOOGLE_OAUTH_ISSUER = "https://accounts.google.com"

# Google's OAuth public keys for JWT verification (JWK format)
# This is the correct endpoint for ID tokens issued by accounts.google.com
GOOGLE_OAUTH_JWK_URL = "https://www.googleapis.com/oauth2/v3/certs"

# Cache duration for JWK keys (1 hour)
JWK_CACHE_LIFESPAN = 3600


class GoogleChatJWTVerificationError(Exception):
    """Raised when JWT verification fails."""

    pass


@lru_cache(maxsize=1)
def _get_jwk_client() -> PyJWKClient:
    """
    Get cached PyJWKClient for Google OAuth public keys.

    Returns:
        PyJWKClient configured for Google OAuth keys
    """
    return PyJWKClient(GOOGLE_OAUTH_JWK_URL, lifespan=JWK_CACHE_LIFESPAN)


def _get_google_chat_audience() -> str:
    """
    Get the expected audience for Google Chat JWT verification from Vault.

    Returns:
        The audience string (endpoint URL or project number)

    Raises:
        RuntimeError: If audience configuration is missing
    """
    try:
        config = get_secret_data("mira/google_chat_config")
        audience = config.get("audience")

        if not audience:
            raise RuntimeError(
                "Google Chat audience not configured in Vault. "
                "Add 'audience' field to 'mira/google_chat_config' with your "
                "endpoint URL or Google Cloud project number."
            )

        return audience

    except Exception as e:
        if "audience not configured" in str(e):
            raise
        raise RuntimeError(
            f"Failed to retrieve Google Chat config from Vault: {e}. "
            "Ensure 'mira/google_chat_config' exists with 'audience' field."
        ) from e


def verify_google_chat_jwt(authorization_header: Optional[str]) -> bool:
    """
    Verify a Google Chat JWT bearer token.

    Args:
        authorization_header: The full Authorization header value (e.g., 'Bearer <token>')

    Returns:
        True if verification succeeds

    Raises:
        GoogleChatJWTVerificationError: If verification fails for any reason
    """
    if not authorization_header:
        raise GoogleChatJWTVerificationError("Missing Authorization header")

    # Extract bearer token
    if not authorization_header.startswith("Bearer "):
        raise GoogleChatJWTVerificationError(
            "Invalid Authorization header format - expected 'Bearer <token>'"
        )

    token = authorization_header[7:]  # Remove 'Bearer ' prefix

    if not token:
        raise GoogleChatJWTVerificationError("Empty bearer token")

    try:
        # Decode token header without verification to log debug info
        unverified_header = jwt.get_unverified_header(token)
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        logger.info(
            f"JWT debug - kid: {unverified_header.get('kid')}, "
            f"iss: {unverified_payload.get('iss')}, "
            f"aud: {unverified_payload.get('aud')}, "
            f"email: {unverified_payload.get('email')}"
        )

        # Get expected audience from config
        audience = _get_google_chat_audience()

        # Get the signing key from Google's JWK endpoint
        jwk_client = _get_jwk_client()
        signing_key = jwk_client.get_signing_key_from_jwt(token)

        # Decode and verify the JWT
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=GOOGLE_OAUTH_ISSUER,
        )

        # Verify the token is from Google Chat by checking the email claim
        token_email = decoded.get("email")
        if token_email != GOOGLE_CHAT_EMAIL:
            raise GoogleChatJWTVerificationError(
                f"Token email '{token_email}' is not from Google Chat "
                f"(expected '{GOOGLE_CHAT_EMAIL}')"
            )

        logger.debug("Google Chat JWT verification successful")
        return True

    except jwt.ExpiredSignatureError:
        raise GoogleChatJWTVerificationError("JWT token has expired")

    except jwt.InvalidAudienceError:
        raise GoogleChatJWTVerificationError(
            f"Invalid audience - token not intended for this application"
        )

    except jwt.InvalidIssuerError:
        raise GoogleChatJWTVerificationError(
            f"Invalid issuer - token not from Google Chat"
        )

    except jwt.DecodeError as e:
        raise GoogleChatJWTVerificationError(f"Failed to decode JWT: {e}")

    except jwt.PyJWKClientError as e:
        raise GoogleChatJWTVerificationError(
            f"Failed to fetch Google public keys: {e}"
        )

    except Exception as e:
        # Don't expose internal errors in the message
        logger.error(f"JWT verification failed: {e}", exc_info=True)
        raise GoogleChatJWTVerificationError("JWT verification failed")
