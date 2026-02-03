"""
Routing Page Handlers
=====================

RPC handlers and orchestration for the Routing/Documents Matrix page.

This module manages document routing from Google Drive through processing stages:
- Unprocessed: Documents from Drive awaiting routing
- In-Process: Documents currently being processed
- Pending: Documents awaiting external action
- Processed: Completed documents in Firebase

Events:
- ROUTING.orchestrate_init: Initialize page data
- ROUTING.full_data: Complete page data response
- ROUTING.list: List documents by category
- ROUTING.process: Process selected documents
- ROUTING.stop: Stop running jobs
- ROUTING.restart: Restart a job
- ROUTING.delete: Delete processed jobs
- ROUTING.instructions_save: Save document instructions
"""

from .handlers import RoutingHandlers, get_routing_handlers
from .orchestration import (
    handle_routing_orchestrate_init,
    handle_routing_refresh,
    handle_routing_process,
    handle_routing_restart,
    handle_routing_stop,
    handle_routing_delete,
    handle_routing_oauth_init,
)

__all__ = [
    "RoutingHandlers",
    "get_routing_handlers",
    "handle_routing_orchestrate_init",
    "handle_routing_refresh",
    "handle_routing_process",
    "handle_routing_restart",
    "handle_routing_stop",
    "handle_routing_delete",
    "handle_routing_oauth_init",
]
