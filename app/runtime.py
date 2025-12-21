"""
Runtime singletons / shared state.

But: certains modules (ex: `app.registry.registry_listeners`) doivent pouvoir accéder
à l'instance *effective* démarrée par FastAPI au startup, sans créer de dépendance
circulaire sur `app.main`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .listeners_manager import ListenersManager

# Instance réellement démarrée par `app.main` au startup.
listeners_manager: Optional["ListenersManager"] = None


