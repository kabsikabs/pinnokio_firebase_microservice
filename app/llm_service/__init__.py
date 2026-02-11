"""
Service LLM pour le microservice Firebase.

Architecture Worker Pattern (Scalable):
- LLMGateway: Enqueue les jobs vers le worker externe (pinnokio_agentic_worker)
- SessionStateManager: Gere l'etat des sessions dans Redis
- redis_namespaces: Constantes et helpers pour les cles Redis

Note: Le traitement LLM est delegue au worker externe.
"""

from .llm_gateway import get_llm_gateway, LLMGateway
from .session_state_manager import get_session_state_manager, SessionStateManager
from .redis_namespaces import RedisTTL

__all__ = [
    'get_llm_gateway',
    'LLMGateway',
    'get_session_state_manager',
    'SessionStateManager',
    'RedisTTL',
]
