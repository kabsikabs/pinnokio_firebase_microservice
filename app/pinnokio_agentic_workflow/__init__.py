"""
Pinnokio Agentic Workflow Framework

Framework pour l'orchestration d'agents intelligents avec support SPT/LPT.
Basé sur BaseAIAgent avec boucles d'itérations et de tours.

Composants principaux:
- PinnokioBrain: Agent cerveau orchestrateur
- TaskTracker: Suivi des tâches SPT/LPT
- Workflows: Implémentations de workflows agentiques
"""

from .orchestrator import PinnokioBrain, TaskTracker

__all__ = [
    'PinnokioBrain',
    'TaskTracker'
]

