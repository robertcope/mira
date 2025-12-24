#!/usr/bin/env python3
"""
Quick WebSocket connectivity test for MIRA.
Tests if the WebSocket endpoint is accessible and responding.
"""
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

try:
    import websockets
except ImportError:
    print("ERROR: 'websockets' package not installed")
    print("Install with: pip install websockets")
    sys.exit(1)


async def test_websocket():
    """Test WebSocket connection to MIRA."""

    # Test localhost first
    ws_url = "ws://localhost:1993/v0/ws/chat"

    print(f"Testing WebSocket connection to: {ws_url}")
    print("-" * 60)

    try:
        async with websockets.connect(ws_url) as websocket:
            print("✓ WebSocket connection established!")

            # Try to authenticate
            print("\nSending auth message...")

            # Get API key from Vault
            try:
                from clients.vault_client import get_api_key
                api_key = get_api_key('mira_api')
                print(f"✓ Retrieved API key from Vault")
            except Exception as e:
                print(f"✗ Failed to get API key: {e}")
                api_key = input("Enter API key manually: ").strip()

            auth_msg = {
                "type": "auth",
                "token": api_key
            }

            await websocket.send(json.dumps(auth_msg))
            print(f"✓ Sent auth message")

            # Wait for auth response
            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            data = json.loads(response)
            print(f"\n✓ Received response: {data}")

            if data.get("type") == "auth_success":
                print("\n✓ Authentication successful!")

                # Try sending a ping
                print("\nSending ping...")
                await websocket.send(json.dumps({"type": "ping"}))

                pong = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                pong_data = json.loads(pong)
                print(f"✓ Received pong: {pong_data}")

                print("\n" + "=" * 60)
                print("SUCCESS: WebSocket endpoint is working correctly!")
                print("=" * 60)

            else:
                print(f"\n✗ Authentication failed: {data}")
                return False

    except asyncio.TimeoutError:
        print("\n✗ ERROR: Timeout waiting for server response")
        print("   Server may be processing but not responding")
        return False

    except ConnectionRefusedError:
        print("\n✗ ERROR: Connection refused")
        print("   Is MIRA server running on localhost:1993?")
        print("   Start with: python main.py")
        return False

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"\n✗ ERROR: Invalid status code: {e}")
        print("   WebSocket endpoint may not be registered")
        print("   Check main.py includes websocket_api.router")
        return False

    except Exception as e:
        print(f"\n✗ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    print("MIRA WebSocket Connectivity Test")
    print("=" * 60)

    try:
        result = asyncio.run(test_websocket())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(130)
