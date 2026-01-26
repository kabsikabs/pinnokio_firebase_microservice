"""
Telegram Room Registration Handler
===================================

Handles Telegram user registration for communication rooms.

This is a CRITICAL feature that allows users to register their Telegram
accounts for automated notifications from the system.

Flow:
1. Frontend: User enters Telegram username and clicks "Register"
2. Backend: Generate 6-digit verification code
3. Backend: Start TelegramUserRegistration listener
4. User: Sends verification code to bot via Telegram
5. Backend: Listener receives code, validates username
6. Backend: On success, create_telegram_user() saves to Firebase
7. Backend: Send success event to frontend

Note: Telegram registration runs in background and uses callbacks.
"""

import asyncio
import logging
import random
import string
from typing import Dict, Any, Optional

from app.ws_hub import hub
from app.ws_events import WS_EVENTS
from app.firebase_providers import get_firebase_management
from app.cache.unified_cache_manager import get_firebase_cache_manager

logger = logging.getLogger("company_settings.telegram")

# Room display names mapping
ROOM_DISPLAY_NAMES = {
    "accounting_room": "Accounting Room",
    "router_room": "Router Room",
    "banker_room": "Banker Room",
    "approval_room": "Approval Room",
}


def generate_verification_code() -> str:
    """Generate a 6-digit verification code."""
    return ''.join(random.choices(string.digits, k=6))


async def handle_telegram_start_registration(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.telegram_start_registration WebSocket event.

    This starts the Telegram registration process for a specific room.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "room_name": str (e.g., "accounting_room"),
            "username": str (Telegram username with or without @),
            "company_name": str (optional, for display),
            "client_name": str (optional)
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    room_name = payload.get("room_name")
    username = payload.get("username", "").strip()
    company_name = payload.get("company_name", "")
    client_name = payload.get("client_name", "")

    # Validate required params
    if not company_id or not mandate_path or not room_name or not username:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_FAILED,
            "payload": {
                "error": "Missing required parameters",
                "room_name": room_name,
            }
        })
        return

    # Normalize username
    if not username.startswith('@'):
        username = f"@{username}"

    # Generate verification code
    verification_code = generate_verification_code()

    logger.info(f"[TELEGRAM] Starting registration for {room_name}, username={username}")

    try:
        # Import TelegramUserRegistration (lazy import to avoid circular deps)
        from app.libs.telegram_registration import TelegramUserRegistration

        # Define callbacks for the listener
        async def on_success(chat_id: str, sender_username: str):
            """Called when registration succeeds."""
            try:
                logger.info(f"[TELEGRAM] Registration success for {room_name}: {sender_username} -> {chat_id}")

                # Save to Firebase
                firebase = get_firebase_management()
                firebase.create_telegram_user(
                    user_id=uid,
                    mandate_path=mandate_path,
                    telegram_username=sender_username,
                    additional_data={
                        "company_name": company_name,
                        "client_name": client_name,
                        "mandate_doc_id": company_id,
                        "telegram_chat_id": chat_id,
                        "room_name": room_name,
                        "registration_method": "websocket",
                    }
                )

                # Save room mapping
                firebase.set_document(
                    mandate_path,
                    {
                        "telegram_users_mapping": {room_name: chat_id},
                        "telegram_room_assignments": {room_name: sender_username},
                    },
                    merge=True
                )

                # Invalidate cache
                cache = get_firebase_cache_manager()
                await cache.invalidate_cache(uid, company_id, "company_settings", "full_data")

                # Send success event
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_SUCCESS,
                    "payload": {
                        "room_name": room_name,
                        "chat_id": chat_id,
                        "username": sender_username,
                        "company_id": company_id,
                    }
                })

            except Exception as e:
                logger.error(f"[TELEGRAM] Error in success callback: {e}")
                await hub.broadcast(uid, {
                    "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_FAILED,
                    "payload": {
                        "error": f"Failed to save registration: {str(e)}",
                        "room_name": room_name,
                    }
                })

        async def on_error(error_message: str):
            """Called when registration fails."""
            logger.error(f"[TELEGRAM] Registration error for {room_name}: {error_message}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_FAILED,
                "payload": {
                    "error": error_message,
                    "room_name": room_name,
                }
            })

        async def on_timeout():
            """Called when registration times out."""
            logger.warning(f"[TELEGRAM] Registration timeout for {room_name}")
            await hub.broadcast(uid, {
                "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_FAILED,
                "payload": {
                    "error": "Registration timed out. Please try again.",
                    "room_name": room_name,
                    "timeout": True,
                }
            })

        # Create and start the listener
        listener = TelegramUserRegistration(
            expected_username=username,
            expected_code=verification_code,
            success_callback=lambda chat_id, sender: asyncio.create_task(on_success(str(chat_id), sender)),
            error_callback=lambda err: asyncio.create_task(on_error(err)),
            timeout_callback=lambda: asyncio.create_task(on_timeout()),
        )

        # Send verification code to frontend (user needs to send this to bot)
        await hub.broadcast(uid, {
            "type": "company_settings.telegram_code_generated",  # Custom event for UI
            "payload": {
                "room_name": room_name,
                "verification_code": verification_code,
                "username": username,
                "timeout_seconds": 60,
            }
        })

        # Start listening in background
        asyncio.create_task(listener.start_listening())

        logger.info(f"[TELEGRAM] Listener started for {room_name}, code={verification_code}")

    except ImportError:
        logger.error("[TELEGRAM] TelegramUserRegistration not available")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_FAILED,
            "payload": {
                "error": "Telegram registration service not available",
                "room_name": room_name,
            }
        })
    except Exception as e:
        logger.error(f"[TELEGRAM] Error starting registration: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_REGISTRATION_FAILED,
            "payload": {
                "error": str(e),
                "room_name": room_name,
            }
        })


async def handle_telegram_remove_user(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.telegram_remove_user WebSocket event.

    Removes a Telegram user from the authorized list and clears room mapping.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "room_name": str (optional, to clear specific room),
            "username": str (Telegram username to remove)
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    room_name = payload.get("room_name")
    username = payload.get("username", "").strip()

    if not company_id or not mandate_path or not username:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing required parameters"}
        })
        return

    # Normalize username (remove @)
    clean_username = username.replace("@", "").strip()

    try:
        firebase = get_firebase_management()

        # Delete from telegram_users collection
        firebase.delete_telegram_user(clean_username, mandate_path)

        # Update mandate document to clear room mapping if specified
        if room_name:
            # Get current mappings
            mandate_data = firebase.get_document(mandate_path) or {}
            users_mapping = mandate_data.get("telegram_users_mapping", {})
            room_assignments = mandate_data.get("telegram_room_assignments", {})
            auth_users = mandate_data.get("telegram_auth_users", [])

            # Remove from mappings
            if room_name in users_mapping:
                del users_mapping[room_name]
            if room_name in room_assignments:
                del room_assignments[room_name]
            if clean_username in auth_users:
                auth_users.remove(clean_username)

            # Update document
            firebase.set_document(
                mandate_path,
                {
                    "telegram_users_mapping": users_mapping,
                    "telegram_room_assignments": room_assignments,
                    "telegram_auth_users": auth_users,
                },
                merge=True
            )

        # Invalidate cache
        cache = get_firebase_cache_manager()
        await cache.invalidate_cache(uid, company_id, "company_settings", "full_data")

        # Send success event
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.TELEGRAM_USER_REMOVED,
            "payload": {
                "success": True,
                "username": clean_username,
                "room_name": room_name,
                "company_id": company_id,
            }
        })

        logger.info(f"[TELEGRAM] User {clean_username} removed from {room_name or 'all rooms'}")

    except Exception as e:
        logger.error(f"[TELEGRAM] Error removing user: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })


async def handle_telegram_reset_room(
    uid: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    """
    Handle company_settings.telegram_reset_room WebSocket event.

    Resets a room's Telegram configuration without removing the user entirely.

    Args:
        payload: {
            "company_id": str,
            "mandate_path": str,
            "room_name": str
        }
    """
    company_id = payload.get("company_id")
    mandate_path = payload.get("mandate_path")
    room_name = payload.get("room_name")

    if not company_id or not mandate_path or not room_name:
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": "Missing required parameters"}
        })
        return

    try:
        firebase = get_firebase_management()

        # Get current mappings
        mandate_data = firebase.get_document(mandate_path) or {}
        users_mapping = mandate_data.get("telegram_users_mapping", {})
        room_assignments = mandate_data.get("telegram_room_assignments", {})

        # Clear the specific room
        if room_name in users_mapping:
            del users_mapping[room_name]
        if room_name in room_assignments:
            del room_assignments[room_name]

        # Update document
        firebase.set_document(
            mandate_path,
            {
                "telegram_users_mapping": users_mapping,
                "telegram_room_assignments": room_assignments,
            },
            merge=True
        )

        # Invalidate cache
        cache = get_firebase_cache_manager()
        await cache.invalidate_cache(uid, company_id, "company_settings", "full_data")

        # Send success event
        await hub.broadcast(uid, {
            "type": "company_settings.telegram_room_reset",
            "payload": {
                "success": True,
                "room_name": room_name,
                "company_id": company_id,
            }
        })

        logger.info(f"[TELEGRAM] Room {room_name} reset for company_id={company_id}")

    except Exception as e:
        logger.error(f"[TELEGRAM] Error resetting room: {e}")
        await hub.broadcast(uid, {
            "type": WS_EVENTS.COMPANY_SETTINGS.ERROR,
            "payload": {"error": str(e)}
        })
