#!/usr/bin/env python3
"""
Simple test script for LLM logging functionality.

Usage:
    MIRA_LLM_LOGGING=1 python test_llm_logging.py
"""

import json
import os
from pathlib import Path

# Enable logging for this test
os.environ['MIRA_LLM_LOGGING'] = '1'

from utils.llm_logger import get_llm_logger


def test_basic_logging():
    """Test basic request/response logging."""
    print("Testing LLM Logger...")

    logger = get_llm_logger()

    # Test request logging
    print("\n1. Testing request logging...")
    correlation_id = logger.log_request(
        provider="anthropic",
        model="claude-sonnet-4.5-20250929",
        system_prompt="You are a helpful assistant.",
        messages=[
            {"role": "user", "content": "Hello, how are you?"}
        ],
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        user_id="test_user_123"
    )

    print(f"✓ Request logged with correlation_id: {correlation_id}")

    # Test response logging
    print("\n2. Testing response logging...")
    logger.log_response(
        correlation_id=correlation_id,
        response_data={
            "content": [
                {"type": "text", "text": "I'm doing well, thank you!"}
            ],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 15,
                "output_tokens": 8
            }
        },
        user_id="test_user_123"
    )

    print(f"✓ Response logged")

    # Test streaming event logging
    print("\n3. Testing streaming event logging...")
    logger.log_streaming_event(
        correlation_id=correlation_id,
        event_type="text_delta",
        event_data={"content": "Hello"},
        user_id="test_user_123"
    )

    print(f"✓ Streaming event logged")

    # Verify files were created
    print("\n4. Verifying log files...")
    log_dir = Path(logger.base_dir)

    if not log_dir.exists():
        print(f"✗ Log directory not found: {log_dir}")
        return False

    # Find today's log directory
    from utils.timezone_utils import utc_now
    today = utc_now().strftime("%Y-%m-%d")
    today_dir = log_dir / today

    if not today_dir.exists():
        print(f"✗ Today's log directory not found: {today_dir}")
        return False

    # List log files
    log_files = list(today_dir.glob("*.json")) + list(today_dir.glob("*.jsonl"))
    print(f"✓ Found {len(log_files)} log files in {today_dir}")

    for log_file in sorted(log_files):
        print(f"  - {log_file.name} ({log_file.stat().st_size} bytes)")

        # Print first 100 chars of each file
        if log_file.suffix == '.json':
            with open(log_file) as f:
                data = json.load(f)
                print(f"    Type: {data.get('type')}, Provider: {data.get('provider', 'N/A')}")

    print("\n✓ All tests passed!")
    print(f"\nLog files written to: {today_dir.absolute()}")
    return True


if __name__ == '__main__':
    try:
        success = test_basic_logging()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
