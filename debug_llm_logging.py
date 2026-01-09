#!/usr/bin/env python3
"""
Debug script to check LLM logging configuration.
"""

import os
import sys

print("=== LLM Logging Debug ===\n")

# Check environment variable
env_value = os.environ.get('MIRA_LLM_LOGGING', 'NOT_SET')
print(f"1. Environment variable MIRA_LLM_LOGGING: {env_value}")

# Check config function
from utils.llm_logger_config import is_llm_logging_enabled, get_llm_logger_config
print(f"2. is_llm_logging_enabled(): {is_llm_logging_enabled()}")

# Check full config
config = get_llm_logger_config()
print(f"3. Full config: {config}")

# Try to get logger
try:
    from utils.llm_logger import get_llm_logger
    logger = get_llm_logger()
    print(f"4. Logger instance created: {logger}")
    print(f"   - enabled: {logger.enabled}")
    print(f"   - base_dir: {logger.base_dir}")
    print(f"   - user_scoped: {logger.user_scoped}")

    # Check if directory exists
    import pathlib
    if logger.base_dir.exists():
        print(f"   - Directory exists: YES")
        print(f"   - Directory contents: {list(logger.base_dir.iterdir())}")
    else:
        print(f"   - Directory exists: NO")

    # Try logging a test request
    print("\n5. Testing log_request()...")
    correlation_id = logger.log_request(
        provider="test",
        model="test-model",
        system_prompt="test prompt",
        messages=[{"role": "user", "content": "test"}],
        user_id="debug_user"
    )
    print(f"   - Correlation ID returned: {correlation_id}")

    # Check for created files
    from utils.timezone_utils import utc_now
    today = utc_now().strftime("%Y-%m-%d")
    today_dir = logger.base_dir / today
    if today_dir.exists():
        files = list(today_dir.glob("*"))
        print(f"   - Files in today's directory: {len(files)}")
        for f in files:
            print(f"     - {f.name}")
    else:
        print(f"   - Today's directory doesn't exist: {today_dir}")

except Exception as e:
    print(f"4. ERROR creating logger: {e}")
    import traceback
    traceback.print_exc()

print("\n=== End Debug ===")
