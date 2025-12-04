"""
Service LLM pour le microservice Firebase.
Gère les sessions LLM et la communication via Firebase Realtime Database.

⭐ Architecture Stateless (Multi-Instance Ready):
- SessionStateManager: Externalise l'état des sessions dans Redis
- ChatHistoryManager: Externalise l'historique des chats dans Redis
"""

from .llm_manager import get_llm_manager, LLMManager
from .llm_context import LLMContext
from .session_state_manager import get_session_state_manager, SessionStateManager
from .chat_history_manager import get_chat_history_manager, ChatHistoryManager

__all__ = [
    'get_llm_manager', 
    'LLMManager', 
    'LLMContext',
    'get_session_state_manager',
    'SessionStateManager',
    'get_chat_history_manager',
    'ChatHistoryManager'
]


