"""
Thread-scoped tier management for Google Chat.

Each Google Chat thread has its own tier preference stored in Valkey.
Default is 'balanced'. Tier persists until explicitly changed in that thread.
"""
import logging

from clients.valkey_client import get_valkey_client
from utils.user_context import get_current_user

logger = logging.getLogger(__name__)


def get_thread_tier(user_id: str) -> str:
    """
    Get tier for current Google Chat thread.

    Returns the tier stored in Valkey for the current thread, or 'balanced'
    if no thread context exists or no tier has been set.

    Args:
        user_id: User identifier

    Returns:
        Tier name (e.g., 'balanced', 'nuanced', 'fast')
    """
    context = get_current_user()
    thread_key = context.get('google_chat_thread_key')

    if not thread_key:
        logger.info(f"No google_chat_thread_key in context, returning 'balanced'")
        return 'balanced'

    valkey_key = f"gchat_tier:{user_id}:{thread_key}"
    valkey = get_valkey_client()
    tier = valkey.get(valkey_key)

    if tier:
        tier_name = tier.decode() if isinstance(tier, bytes) else tier
        logger.info(f"Thread tier for {thread_key}: {tier_name} (key: {valkey_key})")
        return tier_name

    logger.info(f"No tier set for thread {thread_key}, returning 'balanced' (key: {valkey_key})")
    return 'balanced'


def set_thread_tier(user_id: str, tier: str) -> bool:
    """
    Set tier for current Google Chat thread.

    Stores the tier in Valkey with no TTL (persists until explicitly changed).

    Args:
        user_id: User identifier
        tier: Tier name to set

    Returns:
        True if tier was set, False if no thread context
    """
    context = get_current_user()
    thread_key = context.get('google_chat_thread_key')

    if not thread_key:
        logger.warning("Cannot set thread tier: no google_chat_thread_key in context")
        return False

    valkey_key = f"gchat_tier:{user_id}:{thread_key}"
    valkey = get_valkey_client()
    valkey.set(valkey_key, tier)
    logger.info(f"Set thread tier to '{tier}' for thread {thread_key} (key: {valkey_key})")
    return True
