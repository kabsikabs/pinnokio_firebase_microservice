"""
Contexte LLM dynamique pour une session utilisateur/société.
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class LLMContext:
    """Contexte dynamique pour une session LLM.
    
    Ce contexte est partagé par tous les threads de conversation
    d'un utilisateur dans une société donnée.
    """
    
    user_id: str
    collection_name: str
    dms_system: str = "google_drive"
    dms_mode: str = "prod"
    chat_mode: str = "general_chat"
    
    # Contexte métier (optionnel, récupéré depuis Firestore)
    company_name: Optional[str] = None
    company_context: Optional[str] = None
    gl_accounting_erp: Optional[str] = None
    mandate_path: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convertit le contexte en dictionnaire."""
        return {
            "user_id": self.user_id,
            "collection_name": self.collection_name,
            "dms_system": self.dms_system,
            "dms_mode": self.dms_mode,
            "chat_mode": self.chat_mode,
            "company_name": self.company_name,
            "company_context": self.company_context,
            "gl_accounting_erp": self.gl_accounting_erp,
            "mandate_path": self.mandate_path
        }


