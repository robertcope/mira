"""
User context management using contextvars.

This module provides transparent user context that works for both:
- Single-user scenarios (CLI): Context set once and persists
- Multi-user scenarios (web): Context isolated per request automatically

Uses Python's contextvars which provides automatic isolation for
concurrent operations while working identically for single-threaded use.
"""

import contextvars
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field

# Context variable for current user data
_user_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    'user_context',
    default=None
)


def set_current_user_id(user_id: str) -> None:
    """
    Set current user ID in context (standardized key: 'user_id').
    """
    current = _user_context.get() or {}
    current["user_id"] = user_id
    _user_context.set(current)


def get_current_user_id() -> str:
    """
    Get current user ID from context (reads 'user_id').
    """
    context = _user_context.get()
    if not context or "user_id" not in context:
        raise RuntimeError("No user context set. Ensure authentication is properly initialized.")
    return context["user_id"]


def set_current_user_data(user_data: Dict[str, Any]) -> None:
    """
    Set complete user data in context.
    Standardizes to 'user_id' and does not maintain legacy 'id'.
    """
    data = user_data.copy()
    if "user_id" not in data and "id" in data:
        # Normalize legacy key to standardized key
        data["user_id"] = data.pop("id")
    current = _user_context.get() or {}
    current.update(data)
    _user_context.set(current)


def get_current_user() -> Dict[str, Any]:
    """
    Get current user data from context.

    Returns:
        Copy of current user data dictionary

    Raises:
        RuntimeError: If no user context is set
    """
    context = _user_context.get()
    if not context:
        raise RuntimeError("No user context set. Ensure authentication is properly initialized.")
    return context.copy()


def update_current_user(updates: Dict[str, Any]) -> None:
    """
    Update current user data with new values.

    Args:
        updates: Dictionary of updates to apply
    """
    current = _user_context.get() or {}
    current.update(updates)
    _user_context.set(current)


def clear_user_context() -> None:
    """
    Clear the current user context.

    Useful for cleanup or testing scenarios.
    """
    _user_context.set(None)


def has_user_context() -> bool:
    """
    Check if user context is currently set.

    Returns:
        True if user context exists, False otherwise
    """
    context = _user_context.get()
    return context is not None and "user_id" in context


# ============================================================
# AccountTiers - Database-backed tier definitions
# ============================================================

class LLMProvider(str, Enum):
    """LLM provider routing type."""
    ANTHROPIC = "anthropic"  # Direct Anthropic SDK
    GENERIC = "generic"      # OpenAI-compatible endpoint (Groq, OpenRouter, Ollama, etc.)


@dataclass(frozen=True)
class TierConfig:
    """LLM configuration for a tier."""
    name: str
    model: str
    thinking_budget: int
    description: str
    display_order: int
    provider: LLMProvider = LLMProvider.ANTHROPIC
    endpoint_url: Optional[str] = None
    api_key_name: Optional[str] = None
    show_locked: bool = False
    locked_message: Optional[str] = None


# Module-level cache for tiers (loaded once per process)
_tiers_cache: Optional[dict[str, TierConfig]] = None


def get_account_tiers() -> dict[str, TierConfig]:
    """
    Get all available account tiers from database.
    Cached at module level (tiers rarely change).
    """
    global _tiers_cache
    if _tiers_cache is not None:
        return _tiers_cache

    from clients.postgres_client import PostgresClient
    db = PostgresClient('mira_service')

    results = db.execute_query(
        "SELECT name, model, thinking_budget, description, display_order, provider, endpoint_url, api_key_name, show_locked, locked_message FROM account_tiers ORDER BY display_order"
    )

    _tiers_cache = {
        row['name']: TierConfig(
            name=row['name'],
            model=row['model'],
            thinking_budget=row['thinking_budget'],
            description=row['description'] or '',
            display_order=row['display_order'],
            provider=LLMProvider(row['provider']),
            endpoint_url=row['endpoint_url'],
            api_key_name=row['api_key_name'],
            show_locked=row.get('show_locked', False) or False,
            locked_message=row.get('locked_message')
        )
        for row in results
    }
    return _tiers_cache


def resolve_tier(tier_name: str) -> TierConfig:
    """Get LLM config for a tier name."""
    tiers = get_account_tiers()
    if tier_name not in tiers:
        raise ValueError(f"Unknown tier: {tier_name}")
    return tiers[tier_name]


def get_accessible_tiers(max_tier: str) -> list[TierConfig]:
    """Get all tiers accessible up to and including max_tier."""
    tiers = get_account_tiers()
    max_order = tiers[max_tier].display_order
    return [t for t in tiers.values() if t.display_order <= max_order]


def can_access_tier(requested_tier: str, max_tier: str) -> bool:
    """Check if requested tier is within user's allowed access."""
    tiers = get_account_tiers()
    return tiers[requested_tier].display_order <= tiers[max_tier].display_order


# ============================================================
# InternalLLM - Database-backed internal LLM configurations
# ============================================================

@dataclass(frozen=True)
class InternalLLMConfig:
    """Internal LLM configuration for system operations (not user-facing)."""
    name: str
    model: str
    endpoint_url: str
    api_key_name: Optional[str]
    description: str


_internal_llm_cache: dict[str, InternalLLMConfig] | None = None


def load_internal_llm_configs() -> None:
    """Load internal LLM configs at startup. Call during app boot."""
    global _internal_llm_cache
    from clients.postgres_client import PostgresClient
    db = PostgresClient('mira_service')
    results = db.execute_query(
        "SELECT name, model, endpoint_url, api_key_name, description FROM internal_llm"
    )
    _internal_llm_cache = {
        row['name']: InternalLLMConfig(
            name=row['name'],
            model=row['model'],
            endpoint_url=row['endpoint_url'],
            api_key_name=row['api_key_name'],
            description=row['description'] or ''
        )
        for row in results
    }


def get_internal_llm(name: str) -> InternalLLMConfig:
    """Get internal LLM config by name."""
    if _internal_llm_cache is None:
        raise RuntimeError("Internal LLM configs not loaded. Call load_internal_llm_configs() at startup.")
    return _internal_llm_cache[name]


# ============================================================
# UserPreferences - Database-backed user settings
# ============================================================

class UserPreferences(BaseModel):
    """
    User preferences loaded from database.
    Cached in contextvars after first load per request.
    """
    timezone: str = Field(default="America/Chicago")
    memory_manipulation_enabled: bool = Field(default=True)
    llm_tier: str = Field(default="balanced")
    max_tier: str = Field(default="balanced")


def get_user_preferences() -> UserPreferences:
    """
    Get current user's preferences with Valkey caching.

    Cache hierarchy:
    1. Valkey (shared across all contexts - WebSocket, HTTP, etc.)
    2. Database (source of truth)

    Valkey cache is invalidated on preference updates, ensuring
    all contexts see changes immediately.
    """
    import json
    from clients.valkey_client import get_valkey_client
    from clients.postgres_client import PostgresClient

    user_id = get_current_user_id()
    cache_key = f"user_prefs:{user_id}"

    # Check Valkey cache first (shared across all contexts)
    valkey = get_valkey_client()
    cached = valkey.get(cache_key)
    if cached:
        data = json.loads(cached)
        return UserPreferences(**data)

    # Cache miss - fetch from database
    db = PostgresClient('mira_service')
    result = db.execute_single(
        """SELECT timezone, memory_manipulation_enabled, llm_tier, max_tier
           FROM users WHERE id = %s""",
        (user_id,)
    )

    prefs = UserPreferences(
        timezone=result.get('timezone') or 'America/Chicago',
        memory_manipulation_enabled=result.get('memory_manipulation_enabled', True),
        llm_tier=result.get('llm_tier') or 'balanced',
        max_tier=result.get('max_tier') or 'balanced',
    )

    # Cache in Valkey with 5-minute TTL (safety net - invalidation handles freshness)
    valkey.set(cache_key, prefs.model_dump_json(), ex=300)

    return prefs


def update_user_preference(field: str, value: Any) -> UserPreferences:
    """
    Update a single preference field in database and invalidate cache.

    Args:
        field: Preference field name (timezone, llm_tier, etc.)
        value: New value for the field

    Returns:
        Updated UserPreferences object
    """
    if field not in UserPreferences.model_fields:
        raise ValueError(f"Unknown preference field: {field}")

    user_id = get_current_user_id()

    from clients.postgres_client import PostgresClient
    from clients.valkey_client import get_valkey_client

    db = PostgresClient('mira_service')

    db.execute_update(
        f"UPDATE users SET {field} = %s WHERE id = %s",
        (value, user_id)
    )

    # Invalidate Valkey cache - next get_user_preferences() will fetch fresh
    valkey = get_valkey_client()
    valkey.delete(f"user_prefs:{user_id}")

    return get_user_preferences()


# ============================================================
# Activity tracking (not a preference - computed value)
# ============================================================

def get_user_cumulative_activity_days() -> int:
    """
    Get current user's cumulative activity days with context caching.

    This is the canonical way to get "how many days" for scoring calculations.
    Returns activity days (not calendar days) to ensure vacation-proof decay.

    Context caching ensures we only query the database once per session,
    with subsequent calls returning the cached value.

    Returns:
        Cumulative activity days for current user

    Raises:
        RuntimeError: If no user context is set
    """
    # Check if already cached in context
    try:
        user_data = get_current_user()
        if 'cumulative_activity_days' in user_data:
            return user_data['cumulative_activity_days']
    except RuntimeError:
        raise RuntimeError("No user context set. Cannot get activity days without user context.")

    # Not cached - query user activity module and cache result
    user_id = get_current_user_id()

    from utils.user_activity import get_user_cumulative_activity_days as get_activity_days
    activity_days = get_activity_days(user_id)

    # Cache for subsequent calls
    update_current_user({'cumulative_activity_days': activity_days})

    return activity_days
