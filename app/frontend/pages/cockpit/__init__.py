"""Cockpit Dashboard (AI Reporter) — WSS handlers."""

from .handlers import (
    handle_cockpit_create_session,
    handle_cockpit_list_sessions,
    handle_cockpit_delete_session,
    handle_cockpit_rename_session,
    handle_cockpit_generate,
    handle_cockpit_list_widgets,
    handle_cockpit_delete_widget,
    handle_cockpit_refresh_widget,
    handle_cockpit_update_layout,
)

__all__ = [
    "handle_cockpit_create_session",
    "handle_cockpit_list_sessions",
    "handle_cockpit_delete_session",
    "handle_cockpit_rename_session",
    "handle_cockpit_generate",
    "handle_cockpit_list_widgets",
    "handle_cockpit_delete_widget",
    "handle_cockpit_refresh_widget",
    "handle_cockpit_update_layout",
]
