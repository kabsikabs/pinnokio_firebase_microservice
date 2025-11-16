"""
Registre centralisé pour gérer tous les outils disponibles pour l'agent Pinnokio.
"""

import logging
from typing import Dict, List, Any, Callable

logger = logging.getLogger("pinnokio.tool_registry")


class ToolRegistry:
    """
    Registre centralisé pour enregistrer et gérer les outils (SPT et LPT).
    """
    
    def __init__(self):
        self.tools_definitions: List[Dict[str, Any]] = []
        self.tools_mapping: Dict[str, Callable] = {}
        logger.info("ToolRegistry initialisé")
    
    def register_tools(self, tools_list: List[Dict[str, Any]], tools_mapping: Dict[str, Callable]):
        """
        Enregistre un ensemble d'outils dans le registre.
        
        Args:
            tools_list: Liste des définitions d'outils (JSON schema).
            tools_mapping: Dictionnaire mappant les noms d'outils vers leurs fonctions.
        """
        self.tools_definitions.extend(tools_list)
        self.tools_mapping.update(tools_mapping)
        logger.info(f"Outils enregistrés: {len(tools_list)} définitions, {len(tools_mapping)} mappings")
    
    def get_tool_definition(self, tool_name: str) -> Dict[str, Any]:
        """
        Récupère la définition d'un outil par son nom.
        """
        for tool in self.tools_definitions:
            if tool.get("name") == tool_name:
                return tool
        return {}
    
    def get_tool_function(self, tool_name: str) -> Callable:
        """
        Récupère la fonction d'implémentation d'un outil par son nom.
        """
        return self.tools_mapping.get(tool_name)
    
    def list_tools(self) -> List[str]:
        """
        Liste tous les noms d'outils enregistrés.
        """
        return [tool.get("name") for tool in self.tools_definitions]



