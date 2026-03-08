"""
Libs Module
===========

Shared libraries and utilities for the backend.

Contains:
- telegram_registration: Telegram user registration via Telethon
"""

from .telegram_registration import TelegramUserRegistration

__all__ = [
    "TelegramUserRegistration",
]
