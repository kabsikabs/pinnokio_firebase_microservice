"""
HR Page Handlers
================

WebSocket event handlers for the HR module.
Maps WebSocket events to hr_rpc_handlers methods.
"""

from .orchestration import handle_hr_event, register_hr_handlers

__all__ = [
    "handle_hr_event",
    "register_hr_handlers",
]
