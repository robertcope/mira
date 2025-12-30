"""
Production-ready Vault client for MIRA secret management.

Core principles:
- Never manages vault server lifecycle
- Fails fast with clear errors
- Uses environment variables for configuration
- Individual secret retrieval only
- IAM-ready auth abstraction
"""

import os
import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import hvac
from hvac.exceptions import VaultError, InvalidPath, Unauthorized, Forbidden

logger = logging.getLogger(__name__)

# Global singleton instance and cache
_vault_client_instance: Optional['VaultClient'] = None
_secret_cache: Dict[str, str] = {}


def _ensure_vault_client() -> 'VaultClient':
    global _vault_client_instance
    if _vault_client_instance is None:
        _vault_client_instance = VaultClient()
    return _vault_client_instance


class VaultClient:
    """Production client with AppRole auth, env-based config, fail-fast validation, and KV v2 API."""

    # Renewal threshold: renew token when TTL falls below this value
    TOKEN_RENEWAL_THRESHOLD_SECONDS = 300  # 5 minutes

    def __init__(self, vault_addr: Optional[str] = None,
                 vault_token: Optional[str] = None,
                 vault_namespace: Optional[str] = None):
        """Initializes with env vars, validates config, authenticates via AppRole, fails fast on errors."""
        try:

            # Get configuration from environment
            self.vault_addr = vault_addr or os.getenv('VAULT_ADDR')
            self.vault_token = vault_token or os.getenv('VAULT_TOKEN')
            self.vault_namespace = vault_namespace or os.getenv('VAULT_NAMESPACE')

            # AppRole credentials
            self.vault_role_id = os.getenv('VAULT_ROLE_ID')
            self.vault_secret_id = os.getenv('VAULT_SECRET_ID')

            if not self.vault_addr:
                raise ValueError("VAULT_ADDR environment variable is required")

            if not self.vault_role_id or not self.vault_secret_id:
                raise ValueError("VAULT_ROLE_ID and VAULT_SECRET_ID environment variables are required")

            client_kwargs = {'url': self.vault_addr}

            if self.vault_namespace:
                client_kwargs['namespace'] = self.vault_namespace

            self.client = hvac.Client(**client_kwargs)

            # Token metadata for renewal
            self._token_expiry: Optional[datetime] = None
            self._auth_lock = threading.Lock()

            self._authenticate_approle()

            if not self.client.is_authenticated():
                raise PermissionError("Vault authentication failed")

            logger.info(f"Vault client initialized: {self.vault_addr}")

        except Exception as e:
            logger.error(f"Vault client initialization failed: {e}")
            raise
    
    def _authenticate_approle(self):
        """Authenticate via AppRole and store token expiry metadata."""
        try:
            auth_response = self.client.auth.approle.login(
                role_id=self.vault_role_id,
                secret_id=self.vault_secret_id
            )

            self.client.token = auth_response['auth']['client_token']

            # Store token expiry time for proactive renewal
            lease_duration = auth_response['auth'].get('lease_duration', 3600)
            self._token_expiry = datetime.utcnow() + timedelta(seconds=lease_duration)

            logger.info(f"AppRole authentication successful (token valid for {lease_duration}s)")

        except Exception as e:
            logger.error(f"AppRole authentication failed: {e}")
            raise PermissionError(f"AppRole authentication failed: {str(e)}")

    def _ensure_token_valid(self):
        """
        Ensure token is valid before operations. Renews if TTL is low, re-authenticates if expired.

        Thread-safe via lock to prevent concurrent renewal attempts.
        """
        with self._auth_lock:
            # If we don't have expiry metadata, try to look it up
            if self._token_expiry is None:
                try:
                    token_info = self.client.auth.token.lookup_self()
                    ttl = token_info['data'].get('ttl', 0)
                    self._token_expiry = datetime.utcnow() + timedelta(seconds=ttl)
                except Exception:
                    # Can't lookup token, will try to use it and re-auth if it fails
                    logger.warning("Could not lookup token TTL, will re-authenticate on first failure")
                    return

            now = datetime.utcnow()
            seconds_until_expiry = (self._token_expiry - now).total_seconds()

            # Token expired - re-authenticate
            if seconds_until_expiry <= 0:
                logger.info("Token expired, re-authenticating via AppRole")
                self._authenticate_approle()
                return

            # Token expiring soon - renew it
            if seconds_until_expiry < self.TOKEN_RENEWAL_THRESHOLD_SECONDS:
                try:
                    logger.info(f"Token TTL below threshold ({seconds_until_expiry:.0f}s), renewing")
                    renew_response = self.client.auth.token.renew_self()
                    lease_duration = renew_response['auth'].get('lease_duration', 3600)
                    self._token_expiry = datetime.utcnow() + timedelta(seconds=lease_duration)
                    logger.info(f"Token renewed successfully (valid for {lease_duration}s)")
                except Exception as e:
                    # Renewal failed, try re-authentication
                    logger.warning(f"Token renewal failed ({e}), re-authenticating via AppRole")
                    self._authenticate_approle()
    
    def get_secret(self, path: str, field: str) -> str:
        """Retrieves single field from KV v2 API with structured error handling and auto token renewal."""
        self._ensure_token_valid()

        try:
            response = self.client.secrets.kv.v2.read_secret_version(path=path, raise_on_deleted_version=True)
            secret_data = response['data']['data']

            if field not in secret_data:
                available_fields = list(secret_data.keys())
                raise KeyError(f"Field '{field}' not found in secret '{path}'. Available: {', '.join(available_fields)}")

            return secret_data[field]

        except InvalidPath:
            logger.error(f"Secret path not found: {path}")
            raise FileNotFoundError(f"Secret path '{path}' not found in Vault")

        except (Unauthorized, Forbidden) as e:
            # Token might have expired despite our checks - try re-auth once
            logger.warning(f"Auth error accessing {path}/{field}, attempting re-authentication")
            try:
                self._authenticate_approle()
                # Retry the operation once after re-auth
                response = self.client.secrets.kv.v2.read_secret_version(path=path, raise_on_deleted_version=True)
                secret_data = response['data']['data']
                if field not in secret_data:
                    available_fields = list(secret_data.keys())
                    raise KeyError(f"Field '{field}' not found in secret '{path}'. Available: {', '.join(available_fields)}")
                return secret_data[field]
            except Exception:
                # Re-auth or retry failed, raise original error
                logger.error(f"Access denied to secret {path}/{field} even after re-authentication")
                raise PermissionError(f"Access denied to secret '{path}': {str(e)}")

        except VaultError as e:
            logger.error(f"Vault API error for {path}/{field}: {e}")
            raise RuntimeError(f"Vault API error retrieving '{path}/{field}': {str(e)}")

# Individual secret retrieval functions
def get_database_url(service: str, admin: bool = False) -> str:
    """
    Get database URL from Vault for mira_service.

    Args:
        service: Database service name (only 'mira_service' supported)
        admin: If True, returns admin connection string (mira_admin role with BYPASSRLS)

    Returns:
        PostgreSQL connection URL
    """
    if service != 'mira_service':
        raise ValueError(f"Unknown database service: '{service}'. Only 'mira_service' is supported.")

    field = 'admin_url' if admin else 'service_url'
    cache_key = f"mira/database/{field}"

    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    vault_client = _ensure_vault_client()
    value = vault_client.get_secret('mira/database', field)
    _secret_cache[cache_key] = value
    return value


def get_api_key(key_name: str) -> str:
    cache_key = f"mira/api_keys/{key_name}"
    
    if cache_key in _secret_cache:
        return _secret_cache[cache_key]
    
    vault_client = _ensure_vault_client()
    value = vault_client.get_secret('mira/api_keys', key_name)
    _secret_cache[cache_key] = value
    return value


def get_auth_secret(secret_name: str) -> str:
    cache_key = f"mira/auth/{secret_name}"
    
    if cache_key in _secret_cache:
        return _secret_cache[cache_key]
    
    vault_client = _ensure_vault_client()
    value = vault_client.get_secret('mira/auth', secret_name)
    _secret_cache[cache_key] = value
    return value


def get_service_config(service: str, field: str) -> str:
    cache_key = f"mira/services/{field}"
    
    if cache_key in _secret_cache:
        return _secret_cache[cache_key]
    
    vault_client = _ensure_vault_client()
    value = vault_client.get_secret('mira/services', field)
    _secret_cache[cache_key] = value
    return value


def get_secret_data(path: str) -> Dict[str, Any]:
    """
    Get all data from a secret path (for JSON/structured secrets like service accounts).

    Args:
        path: Secret path (e.g., 'mira/google_service_account')

    Returns:
        Dict containing all secret data

    Raises:
        RuntimeError: If secret cannot be retrieved
    """
    vault_client = _ensure_vault_client()
    vault_client._ensure_token_valid()

    try:
        response = vault_client.client.secrets.kv.v2.read_secret_version(
            path=path,
            raise_on_deleted_version=True
        )
        return response['data']['data']
    except (Unauthorized, Forbidden) as e:
        # Token might have expired despite our checks - try re-auth once
        logger.warning(f"Auth error accessing {path}, attempting re-authentication")
        try:
            vault_client._authenticate_approle()
            response = vault_client.client.secrets.kv.v2.read_secret_version(
                path=path,
                raise_on_deleted_version=True
            )
            return response['data']['data']
        except Exception:
            logger.error(f"Failed to retrieve secret data from {path} even after re-authentication")
            raise RuntimeError(f"Failed to retrieve secret from Vault path '{path}': {e}") from e
    except Exception as e:
        logger.error(f"Failed to retrieve secret data from {path}: {e}")
        raise RuntimeError(f"Failed to retrieve secret from Vault path '{path}': {e}") from e


def get_database_credentials() -> Dict[str, str]:
    vault_client = _ensure_vault_client()

    username_key = "mira/database/username"
    password_key = "mira/database/password"

    if username_key not in _secret_cache:
        _secret_cache[username_key] = vault_client.get_secret('mira/database', 'username')
    if password_key not in _secret_cache:
        _secret_cache[password_key] = vault_client.get_secret('mira/database', 'password')

    return {
        'username': _secret_cache[username_key],
        'password': _secret_cache[password_key]
    }



def preload_secrets() -> None:
    """
    Load all secrets into memory cache at startup.

    This prevents token expiration issues by caching everything
    while the AppRole token is still valid.
    """
    vault_client = _ensure_vault_client()

    # Load all API keys by reading the entire secret and caching each field
    try:
        response = vault_client.client.secrets.kv.v2.read_secret_version(
            path='mira/api_keys',
            raise_on_deleted_version=True
        )
        api_keys = response['data']['data']
        for key_name, value in api_keys.items():
            cache_key = f"mira/api_keys/{key_name}"
            _secret_cache[cache_key] = value
        logger.info(f"Preloaded {len(api_keys)} API keys into cache")
    except Exception as e:
        logger.error(f"Failed to preload API keys: {e}")

    # Load database credentials
    try:
        response = vault_client.client.secrets.kv.v2.read_secret_version(
            path='mira/database',
            raise_on_deleted_version=True
        )
        db_secrets = response['data']['data']
        for field, value in db_secrets.items():
            cache_key = f"mira/database/{field}"
            _secret_cache[cache_key] = value
        logger.info(f"Preloaded {len(db_secrets)} database secrets into cache")
    except Exception as e:
        logger.error(f"Failed to preload database secrets: {e}")

    # Load auth secrets
    try:
        response = vault_client.client.secrets.kv.v2.read_secret_version(
            path='mira/auth',
            raise_on_deleted_version=True
        )
        auth_secrets = response['data']['data']
        for field, value in auth_secrets.items():
            cache_key = f"mira/auth/{field}"
            _secret_cache[cache_key] = value
        logger.info(f"Preloaded {len(auth_secrets)} auth secrets into cache")
    except Exception as e:
        logger.error(f"Failed to preload auth secrets: {e}")


# Health check function
def test_vault_connection() -> Dict[str, Any]:
    try:
        vault_client = _ensure_vault_client()
        vault_client.get_secret('mira/database', 'username')
        
        return {
            "status": "success",
            "vault_addr": vault_client.vault_addr,
            "namespace": vault_client.vault_namespace,
            "authenticated": True,
            "message": "Vault connection successful"
        }
        
    except Exception as e:
        logger.error(f"Vault connection test failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "authenticated": False
        }