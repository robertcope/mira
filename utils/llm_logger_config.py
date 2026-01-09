"""
LLM Logger Configuration

Provides configuration helpers for the LLM logging system.
Can be configured via environment variables or programmatically.

Environment Variables:
- MIRA_LLM_LOGGING: Set to "1" to enable LLM logging (default: disabled)
- MIRA_LLM_LOG_DIR: Base directory for logs (default: data/llm_logs)
- MIRA_LLM_LOG_USER_SCOPED: Set to "1" to organize logs by user (default: disabled)
"""

import os
from typing import Optional

# Default configuration
DEFAULT_LOG_DIR = "data/llm_logs"
DEFAULT_ENABLED = False
DEFAULT_USER_SCOPED = False


def is_llm_logging_enabled() -> bool:
    """Check if LLM logging is enabled via environment variable."""
    return os.environ.get('MIRA_LLM_LOGGING', "1") == "1"


def get_llm_log_directory() -> str:
    """Get the configured log directory as an absolute path."""
    log_dir = os.environ.get('MIRA_LLM_LOG_DIR', DEFAULT_LOG_DIR)

    # Convert to absolute path if relative
    if not os.path.isabs(log_dir):
        # Get the project root (parent of utils directory)
        import pathlib
        utils_dir = pathlib.Path(__file__).parent
        project_root = utils_dir.parent
        log_dir = str(project_root / log_dir)

    return log_dir


def is_user_scoped_logging() -> bool:
    """Check if user-scoped logging is enabled."""
    return bool(os.environ.get('MIRA_LLM_LOG_USER_SCOPED'))


def get_llm_logger_config() -> dict:
    """
    Get the complete LLM logger configuration.

    Returns:
        Dictionary with configuration parameters
    """
    return {
        'enabled': is_llm_logging_enabled(),
        'base_dir': get_llm_log_directory(),
        'user_scoped': is_user_scoped_logging()
    }


def configure_from_env() -> Optional[dict]:
    """
    Get configuration from environment variables.

    Returns:
        Configuration dict if enabled, None otherwise
    """
    if not is_llm_logging_enabled():
        return None

    return get_llm_logger_config()
