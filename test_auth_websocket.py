#!/usr/bin/env python3
"""
WebSocket Authentication Handler Test Script
=============================================

This script demonstrates and tests the new WebSocket authentication handler.

Usage:
    python test_auth_websocket.py

Requirements:
    - Backend server running (python -m app.main)
    - Redis server running
    - Valid Firebase credentials configured
"""

import asyncio
import json
import websockets
import sys


async def test_auth_flow():
    """Test the complete authentication flow."""

    # Configuration
    ws_url = "ws://localhost:8000/ws?uid=test_user_123"

    print("\n" + "="*60)
    print("WebSocket Authentication Handler Test")
    print("="*60 + "\n")

    try:
        # Connect to WebSocket
        print(f"[1/4] Connecting to WebSocket: {ws_url}")
        async with websockets.connect(ws_url) as websocket:
            print("✓ Connected successfully\n")

            # Prepare authentication message
            print("[2/4] Preparing authentication message")
            auth_message = {
                "type": "auth.firebase_token",
                "payload": {
                    "token": "test_token_replace_with_real_token",
                    "uid": "test_user_123",
                    "email": "test@example.com",
                    "displayName": "Test User",
                    "photoURL": "https://example.com/photo.jpg",
                    "sessionId": "test_session_456"
                }
            }

            print(f"Message: {json.dumps(auth_message, indent=2)}\n")

            # Send authentication message
            print("[3/4] Sending authentication message")
            await websocket.send(json.dumps(auth_message))
            print("✓ Message sent\n")

            # Wait for response (with timeout)
            print("[4/4] Waiting for authentication response...")
            try:
                response_text = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=5.0
                )

                response = json.loads(response_text)
                print(f"✓ Response received\n")

                # Display response
                print("="*60)
                print("RESPONSE:")
                print("="*60)
                print(json.dumps(response, indent=2))
                print("\n")

                # Analyze response
                response_type = response.get("type")
                payload = response.get("payload", {})

                if response_type == "auth.session_confirmed":
                    print("✅ SUCCESS: Authentication confirmed!")
                    print(f"   User ID: {payload.get('user', {}).get('id')}")
                    print(f"   Session ID: {payload.get('sessionId')}")
                    print(f"   Permissions: {payload.get('permissions')}")

                elif response_type == "auth.login_error":
                    print("❌ FAILED: Authentication error")
                    print(f"   Error: {payload.get('error')}")
                    print(f"   Code: {payload.get('code')}")

                    # Common error explanations
                    if "token" in payload.get('error', '').lower():
                        print("\n   NOTE: This test uses a dummy token.")
                        print("   For real testing, replace with a valid Firebase ID token.")
                        print("   Get token from: Firebase Console → Authentication → User")

                else:
                    print(f"⚠️  UNEXPECTED: Received type '{response_type}'")

            except asyncio.TimeoutError:
                print("❌ TIMEOUT: No response received within 5 seconds")
                print("   Check:")
                print("   - Backend server is running")
                print("   - WebSocket handler is processing messages")
                print("   - No errors in backend logs")

    except websockets.exceptions.WebSocketException as e:
        print(f"❌ WebSocket Error: {e}")
        print("\nTroubleshooting:")
        print("   - Is the backend server running?")
        print("   - Start with: python -m app.main")
        print("   - Check the server is listening on port 8000")

    except ConnectionRefusedError:
        print("❌ Connection Refused")
        print("\nThe backend server is not running.")
        print("Start it with: python -m app.main")

    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*60)
    print("Test Complete")
    print("="*60 + "\n")


async def test_with_real_token():
    """
    Template for testing with a real Firebase token.

    To use:
    1. Get a real Firebase ID token from your frontend
    2. Replace the token value below
    3. Run this function instead of test_auth_flow()
    """

    ws_url = "ws://localhost:8000/ws?uid=YOUR_FIREBASE_UID"

    async with websockets.connect(ws_url) as websocket:
        auth_message = {
            "type": "auth.firebase_token",
            "payload": {
                "token": "PASTE_YOUR_REAL_FIREBASE_TOKEN_HERE",
                "uid": "YOUR_FIREBASE_UID",
                "email": "your-email@example.com",
                "displayName": "Your Name",
                "photoURL": "https://...",
                "sessionId": "test_session_real"
            }
        }

        await websocket.send(json.dumps(auth_message))
        response_text = await asyncio.wait_for(websocket.recv(), timeout=5.0)
        response = json.loads(response_text)

        print(json.dumps(response, indent=2))


def print_instructions():
    """Print usage instructions."""
    print("\n" + "="*60)
    print("WebSocket Authentication Test - Instructions")
    print("="*60 + "\n")

    print("BEFORE RUNNING THIS TEST:\n")

    print("1. Start the backend server:")
    print("   cd /c/Users/Cedri/Coding/firebase_microservice")
    print("   python -m app.main\n")

    print("2. Ensure Redis is running:")
    print("   redis-server\n")

    print("3. Verify environment variables are set:")
    print("   - FIREBASE_ADMIN_JSON or FIREBASE_ADMIN_SECRET_NAME")
    print("   - LISTENERS_REDIS_HOST (default: 127.0.0.1)")
    print("   - LISTENERS_REDIS_PORT (default: 6379)\n")

    print("TEST MODES:\n")

    print("1. DUMMY TOKEN TEST (will fail auth, tests connectivity):")
    print("   python test_auth_websocket.py\n")

    print("2. REAL TOKEN TEST (requires valid Firebase token):")
    print("   - Edit this file")
    print("   - Update test_with_real_token() with real values")
    print("   - Uncomment the call to test_with_real_token() at the bottom\n")

    print("EXPECTED RESULTS:\n")

    print("Dummy Token Test:")
    print("  - ✓ WebSocket connection succeeds")
    print("  - ✓ Message sent successfully")
    print("  - ✓ Response received")
    print("  - ❌ Auth fails (expected - dummy token)")
    print("  - Response type: 'auth.login_error'\n")

    print("Real Token Test:")
    print("  - ✓ All steps succeed")
    print("  - ✓ Auth succeeds")
    print("  - Response type: 'auth.session_confirmed'")
    print("  - Session stored in Redis\n")

    print("="*60 + "\n")


if __name__ == "__main__":
    # Show instructions
    if "--help" in sys.argv or "-h" in sys.argv:
        print_instructions()
        sys.exit(0)

    # Run test
    print("\nStarting WebSocket authentication test...")
    print("(Use --help for detailed instructions)\n")

    try:
        asyncio.run(test_auth_flow())

        # Uncomment below to test with real token (after updating values):
        # asyncio.run(test_with_real_token())

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
