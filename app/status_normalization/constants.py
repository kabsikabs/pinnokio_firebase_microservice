"""
Constantes de normalisation des statuts.

Ce fichier définit:
- NormalizedStatus: Les valeurs de statut normalisées envoyées au frontend
- StatusCategory: Les catégories de regroupement pour les onglets UI
- RAW_TO_NORMALIZED: Mapping des statuts bruts vers normalisés
- NORMALIZED_TO_CATEGORY: Mapping des statuts normalisés vers catégories
"""

from enum import Enum
from typing import Dict, Set


class NormalizedStatus(str, Enum):
    """
    Statuts normalisés envoyés au frontend.
    Ces valeurs correspondent aux types TypeScript du frontend:
    - RoutingStatus, InvoiceStatus, etc.
    """
    TO_PROCESS = "to_process"      # À traiter (par défaut)
    IN_QUEUE = "in_queue"          # En file d'attente
    ON_PROCESS = "on_process"      # En cours de traitement
    STOPPING = "stopping"          # Arrêt en cours
    PENDING = "pending"            # En attente d'approbation
    COMPLETED = "completed"        # Terminé
    ERROR = "error"                # Erreur
    STOPPED = "stopped"            # Arrêté
    SKIPPED = "skipped"            # Sauté (retour to_process)
    ROUTED = "routed"              # Routé (spécifique Router)


class StatusCategory(str, Enum):
    """
    Catégories de regroupement pour les onglets UI.
    Correspond aux tabs frontend: to_process, in_process, pending, processed
    """
    TO_PROCESS = "to_process"      # Onglet "À traiter"
    IN_PROCESS = "in_process"      # Onglet "En cours"
    PENDING = "pending"            # Onglet "En attente"
    PROCESSED = "processed"        # Onglet "Traités"


# ============================================================================
# MAPPING STATUTS BRUTS → NORMALISÉS
# ============================================================================
# Statuts bruts provenant de:
# - Firebase notifications (jobbeurs)
# - Bases de données legacy
# - APIs externes

RAW_TO_NORMALIZED: Dict[str, NormalizedStatus] = {
    # Statuts "en cours" → ON_PROCESS
    "running": NormalizedStatus.ON_PROCESS,
    "processing": NormalizedStatus.ON_PROCESS,

    # Statuts "file d'attente" → IN_QUEUE
    "in queue": NormalizedStatus.IN_QUEUE,
    "in_queue": NormalizedStatus.IN_QUEUE,
    "queued": NormalizedStatus.IN_QUEUE,

    # Statuts "terminé" → COMPLETED
    "success": NormalizedStatus.COMPLETED,
    "close": NormalizedStatus.COMPLETED,
    "closed": NormalizedStatus.COMPLETED,
    "done": NormalizedStatus.COMPLETED,
    "finished": NormalizedStatus.COMPLETED,
    "completed": NormalizedStatus.COMPLETED,

    # Statuts passthrough (déjà normalisés)
    "to_process": NormalizedStatus.TO_PROCESS,
    "on_process": NormalizedStatus.ON_PROCESS,
    "stopping": NormalizedStatus.STOPPING,
    "pending": NormalizedStatus.PENDING,
    "error": NormalizedStatus.ERROR,
    "stopped": NormalizedStatus.STOPPED,
    "skipped": NormalizedStatus.SKIPPED,
    "routed": NormalizedStatus.ROUTED,
}


# ============================================================================
# MAPPING STATUTS NORMALISÉS → CATÉGORIES
# ============================================================================
# Définit dans quelle catégorie/onglet chaque statut apparaît

NORMALIZED_TO_CATEGORY: Dict[NormalizedStatus, StatusCategory] = {
    # Catégorie "À traiter"
    NormalizedStatus.TO_PROCESS: StatusCategory.TO_PROCESS,
    NormalizedStatus.ERROR: StatusCategory.TO_PROCESS,
    NormalizedStatus.STOPPED: StatusCategory.TO_PROCESS,
    NormalizedStatus.SKIPPED: StatusCategory.TO_PROCESS,

    # Catégorie "En cours"
    NormalizedStatus.IN_QUEUE: StatusCategory.IN_PROCESS,
    NormalizedStatus.ON_PROCESS: StatusCategory.IN_PROCESS,
    NormalizedStatus.STOPPING: StatusCategory.IN_PROCESS,

    # Catégorie "En attente"
    NormalizedStatus.PENDING: StatusCategory.PENDING,

    # Catégorie "Traités"
    NormalizedStatus.COMPLETED: StatusCategory.PROCESSED,
    NormalizedStatus.ROUTED: StatusCategory.PROCESSED,  # Documents routés = traités
}


# ============================================================================
# GROUPES PAR CATÉGORIE (pour référence rapide)
# ============================================================================

CATEGORY_STATUS_GROUPS: Dict[StatusCategory, Set[NormalizedStatus]] = {
    StatusCategory.TO_PROCESS: {
        NormalizedStatus.TO_PROCESS,
        NormalizedStatus.ERROR,
        NormalizedStatus.STOPPED,
        NormalizedStatus.SKIPPED,
    },
    StatusCategory.IN_PROCESS: {
        NormalizedStatus.IN_QUEUE,
        NormalizedStatus.ON_PROCESS,
        NormalizedStatus.STOPPING,
    },
    StatusCategory.PENDING: {
        NormalizedStatus.PENDING,
    },
    StatusCategory.PROCESSED: {
        NormalizedStatus.COMPLETED,
        NormalizedStatus.ROUTED,  # Documents routés = traités
    },
}


# ============================================================================
# OVERRIDES PAR FONCTION (spécificités métier)
# ============================================================================
# Certaines fonctions (Router, Banker, APbookeeper) ont des mappings spéciaux

FUNCTION_SPECIFIC_OVERRIDES: Dict[str, Dict[str, NormalizedStatus]] = {
    "router": {
        # Router envoie désormais "routed" directement (fix BUG-RT-002).
        # On garde "success"/"completed" → routed comme filet de sécurité
        # pour les éventuels messages en transit lors du déploiement.
        "success": NormalizedStatus.ROUTED,
        "completed": NormalizedStatus.ROUTED,
    },
    "banker": {
        # Banker garde les conventions standard
    },
    "apbookeeper": {
        # APbookeeper garde les conventions standard
    },
}
