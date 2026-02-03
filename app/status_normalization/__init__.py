"""
Status Normalization Module

Ce module centralise la normalisation des statuts provenant des différentes sources
(Firebase, Jobbeurs) vers des valeurs standardisées pour le frontend.

Usage:
    from status_normalization import StatusNormalizer, NormalizedStatus

    # Normaliser un statut brut
    normalized = StatusNormalizer.normalize("running")  # → "on_process"

    # Obtenir la catégorie pour un statut
    category = StatusNormalizer.get_category("on_process")  # → "in_process"

    # Normaliser avec contexte de fonction
    normalized = StatusNormalizer.normalize_for_function("Router", "success")  # → "routed"
"""

from .constants import NormalizedStatus, StatusCategory, RAW_TO_NORMALIZED, NORMALIZED_TO_CATEGORY
from .normalizer import StatusNormalizer

__all__ = [
    "NormalizedStatus",
    "StatusCategory",
    "StatusNormalizer",
    "RAW_TO_NORMALIZED",
    "NORMALIZED_TO_CATEGORY",
]
