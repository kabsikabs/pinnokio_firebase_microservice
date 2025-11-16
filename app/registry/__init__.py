"""
Module Registry - Gestion centralisée des utilisateurs, sessions et listeners.

Architecture:
- unified_registry.py: Registre unifié moderne (Redis + Firestore)
- registry_listeners.py: Suivi des listeners WebSocket actifs
- registry_wrapper.py: Wrapper de compatibilité pour transition progressive

Utilisation:
    from .registry import get_unified_registry, get_registry_listeners, get_registry_wrapper
"""

from .unified_registry import UnifiedRegistryService, get_unified_registry
from .registry_listeners import RegistryListeners, get_registry_listeners
from .registry_wrapper import RegistryWrapper, get_registry_wrapper

__all__ = [
    'UnifiedRegistryService',
    'get_unified_registry',
    'RegistryListeners',
    'get_registry_listeners',
    'RegistryWrapper',
    'get_registry_wrapper',
]

