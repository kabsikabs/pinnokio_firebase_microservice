"""
Formateur de messages IA pour RTDB compatible avec Reflex.
Gère uniquement les messages générés par l'IA.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Any


class RTDBMessageFormatter:
    """Formateur de messages IA pour RTDB compatible avec Reflex."""
    
    @staticmethod
    def format_ai_message(
        content: str,
        user_id: str,
        message_id: str = None,
        metadata: dict = None,
        timestamp: str = None
    ) -> dict:
        """
        Formate un message IA pour RTDB (type MESSAGE).
        Format compatible avec handle_realtime_message() de Reflex.
        
        Args:
            content (str): Contenu de la réponse IA
            user_id (str): ID de l'utilisateur
            message_id (str, optional): ID du message. Généré automatiquement si non fourni
            metadata (dict, optional): Métadonnées supplémentaires
            timestamp (str, optional): Timestamp ISO 8601. Généré automatiquement si non fourni
            
        Returns:
            dict: Message formaté pour RTDB
        """
        if not message_id:
            message_id = str(uuid.uuid4())
        
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
            
        # Format JSON structuré attendu par Reflex pour les réponses IA
        structured_content = {
            "message": {
                "argumentText": content
            }
        }
        
        message_data = {
            'id': message_id,
            'content': json.dumps(structured_content),  # JSON string
            'sender_id': user_id,
            'timestamp': timestamp,
            'message_type': 'MESSAGE',                 # Type pour réponses IA
            'read': False,
            'local_processed': False
        }
        
        if metadata:
            message_data['metadata'] = metadata
            
        return message_data
