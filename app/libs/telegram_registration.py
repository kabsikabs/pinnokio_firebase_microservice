"""
Telegram User Registration
==========================

Handles Telegram user registration via Telethon client.

This module provides the TelegramUserRegistration class that:
1. Connects to Telegram using bot token
2. Listens for messages from expected username
3. Validates verification code
4. Triggers callbacks on success/error/timeout

Usage:
    listener = TelegramUserRegistration(
        expected_username="@username",
        expected_code="123456",
        success_callback=on_success,
        error_callback=on_error,
        timeout_callback=on_timeout,
    )
    await listener.start_listening()

Note: Requires telethon package and valid Telegram API credentials in GSM.
"""

import asyncio
import json
import logging
import os
from typing import Callable, Optional, Awaitable

from telethon import TelegramClient, events

from app.tools.g_cred import get_secret

logger = logging.getLogger("libs.telegram_registration")

# Default timeout for registration (seconds)
DEFAULT_TIMEOUT_SECONDS = 120

# Secret name in Google Secret Manager
TELEGRAM_SECRET_NAME = "telegram_pinnokio"


class TelegramUserRegistration:
    """
    Telegram user registration listener.

    Listens for messages from a specific user containing a verification code.
    Uses callback pattern for async notification of results.
    """

    def __init__(
        self,
        expected_username: str,
        expected_code: str,
        success_callback: Callable[[int, str], Awaitable[None]],
        error_callback: Callable[[str], Awaitable[None]],
        timeout_callback: Optional[Callable[[], Awaitable[None]]] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        secret_name: str = TELEGRAM_SECRET_NAME,
    ):
        """
        Initialize the Telegram registration listener.

        Args:
            expected_username: Telegram username to expect (with or without @)
            expected_code: Verification code the user must send
            success_callback: Async callback(chat_id, username) on successful registration
            error_callback: Async callback(error_message) on error
            timeout_callback: Optional async callback() on timeout
            timeout_seconds: How long to wait before timing out (default 120s)
            secret_name: Name of secret in GSM containing Telegram credentials
        """
        # Ensure event loop exists
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Load Telegram credentials from GSM
        try:
            cfg = json.loads(get_secret(secret_name))
            config_keys = cfg.get("App configuration", cfg)
            api_id = config_keys.get("api_id")
            api_hash = config_keys.get("api_hash")
            bot_token = config_keys.get("bot_api_id") or config_keys.get("bot_token")
        except Exception as e:
            logger.error(f"[TELEGRAM] Failed to load credentials: {e}")
            raise RuntimeError(f"Failed to load Telegram credentials: {e}")

        # Session name based on environment
        env_suffix = os.getenv("ENV", "local")
        session_name = f"pinnokio_backend_{env_suffix}"

        # Initialize Telethon client
        self.client = TelegramClient(session_name, api_id, api_hash)
        self.bot_token = bot_token

        # Registration parameters
        self.expected_username = expected_username.lower().replace("@", "")
        self.expected_code = expected_code
        self.success_callback = success_callback
        self.error_callback = error_callback
        self.timeout_callback = timeout_callback
        self.timeout_seconds = timeout_seconds

        # State
        self.is_listening = False
        self.registration_completed = False
        self._timeout_task: Optional[asyncio.Task] = None
        self._callback_queue: asyncio.Queue = asyncio.Queue()
        self._callback_processor: Optional[asyncio.Task] = None

        logger.info(
            f"[TELEGRAM] Registration initialized for @{self.expected_username} "
            f"with code {self.expected_code}"
        )

    async def connect(self) -> bool:
        """
        Connect the Telegram client.

        Returns:
            True if connected successfully, False otherwise
        """
        try:
            await self.client.start(bot_token=self.bot_token)
            logger.info("[TELEGRAM] Client connected for registration")
            return True
        except Exception as e:
            logger.error(f"[TELEGRAM] Connection error: {e}")
            await self._queue_callback("error", message="Failed to connect to Telegram. Please try again.")
            return False

    async def start_listening(self) -> bool:
        """
        Start listening for registration messages.

        Returns:
            True if listening started successfully, False otherwise
        """
        if not await self.connect():
            return False

        self.is_listening = True
        logger.info(f"[TELEGRAM] Listening started for @{self.expected_username}")

        # Start callback processor
        self._callback_processor = asyncio.create_task(self._process_callbacks())

        # Start timeout task
        self._timeout_task = asyncio.create_task(self._handle_timeout())

        # Register message handler
        @self.client.on(events.NewMessage)
        async def message_handler(event):
            if not self.is_listening or self.registration_completed:
                return

            try:
                # Get sender info
                sender = await event.get_sender()
                if not sender or not hasattr(sender, "username"):
                    return

                sender_username = (sender.username or "").lower()
                message_text = event.message.message.strip()
                chat_id = event.chat_id

                logger.debug(
                    f"[TELEGRAM] Message from @{sender_username}: '{message_text}'"
                )

                # Check if it's the expected user with the correct code
                if sender_username == self.expected_username and message_text == self.expected_code:
                    logger.info(f"[TELEGRAM] Code validated for @{sender_username}")

                    # Mark as completed
                    self.registration_completed = True

                    # Send confirmation message to user
                    success_message = (
                        f"Registration successful!\n\n"
                        f"Welcome to Pinnokio! Your account (@{self.expected_username}) "
                        f"has been successfully registered and linked to this chat.\n\n"
                        f"You can now receive notifications and interact with your "
                        f"Pinnokio agents through this channel."
                    )

                    try:
                        await self.client.send_message(chat_id, success_message)
                    except Exception as e:
                        logger.warning(f"[TELEGRAM] Could not send confirmation: {e}")

                    # Queue success callback
                    await self._queue_callback(
                        "success",
                        chat_id=chat_id,
                        username=sender_username,
                    )

                    # Stop listening
                    await self.stop_listening()

            except Exception as e:
                logger.error(f"[TELEGRAM] Error processing message: {e}")

        return True

    async def _queue_callback(self, callback_type: str, **kwargs) -> None:
        """Queue a callback for processing."""
        await self._callback_queue.put({"type": callback_type, **kwargs})

    async def _process_callbacks(self) -> None:
        """Process queued callbacks in a separate context."""
        try:
            while self.is_listening or not self._callback_queue.empty():
                try:
                    callback_data = await asyncio.wait_for(
                        self._callback_queue.get(),
                        timeout=1.0,
                    )

                    if callback_data["type"] == "success":
                        asyncio.create_task(
                            self._execute_success_callback(
                                callback_data["chat_id"],
                                callback_data["username"],
                            )
                        )
                    elif callback_data["type"] == "error":
                        asyncio.create_task(
                            self._execute_error_callback(callback_data["message"])
                        )
                    elif callback_data["type"] == "timeout":
                        asyncio.create_task(self._execute_timeout_callback())

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"[TELEGRAM] Callback processor error: {e}")

        except Exception as e:
            logger.error(f"[TELEGRAM] Fatal callback processor error: {e}")

    async def _execute_success_callback(self, chat_id: int, username: str) -> None:
        """Execute the success callback."""
        try:
            await self.success_callback(chat_id, username)
        except Exception as e:
            logger.error(f"[TELEGRAM] Success callback error: {e}")

    async def _execute_error_callback(self, error_message: str) -> None:
        """Execute the error callback."""
        try:
            await self.error_callback(error_message)
        except Exception as e:
            logger.error(f"[TELEGRAM] Error callback error: {e}")

    async def _execute_timeout_callback(self) -> None:
        """Execute the timeout callback."""
        try:
            if self.timeout_callback:
                await self.timeout_callback()
        except Exception as e:
            logger.error(f"[TELEGRAM] Timeout callback error: {e}")

    async def _handle_timeout(self) -> None:
        """Handle registration timeout."""
        try:
            await asyncio.sleep(self.timeout_seconds)

            if self.is_listening and not self.registration_completed:
                logger.warning(
                    f"[TELEGRAM] Registration timeout for @{self.expected_username}"
                )
                await self._queue_callback("timeout")
                await self.stop_listening()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[TELEGRAM] Timeout handler error: {e}")

    async def stop_listening(self) -> None:
        """Stop listening and disconnect the client."""
        try:
            self.is_listening = False

            # Cancel timeout task
            if self._timeout_task and not self._timeout_task.done():
                self._timeout_task.cancel()
                try:
                    await self._timeout_task
                except asyncio.CancelledError:
                    pass

            # Cancel callback processor
            if self._callback_processor and not self._callback_processor.done():
                self._callback_processor.cancel()
                try:
                    await self._callback_processor
                except asyncio.CancelledError:
                    pass

            # Disconnect client
            if self.client.is_connected():
                await self.client.disconnect()

            logger.info("[TELEGRAM] Client disconnected")

        except Exception as e:
            logger.warning(f"[TELEGRAM] Disconnect error: {e}")
