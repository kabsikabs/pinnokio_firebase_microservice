"""
StatusNormalizer - Classe principale de normalisation des statuts.

Usage:
    from status_normalization import StatusNormalizer

    # Normalisation simple
    status = StatusNormalizer.normalize("running")  # → "on_process"

    # Avec contexte de fonction
    status = StatusNormalizer.normalize_for_function("Router", "success")  # → "routed"

    # Obtenir la catégorie
    category = StatusNormalizer.get_category("on_process")  # → "in_process"
"""

import logging
from typing import Optional

from .constants import (
    NormalizedStatus,
    StatusCategory,
    RAW_TO_NORMALIZED,
    NORMALIZED_TO_CATEGORY,
    FUNCTION_SPECIFIC_OVERRIDES,
)

logger = logging.getLogger(__name__)


class StatusNormalizer:
    """
    Classe utilitaire pour normaliser les statuts.
    Toutes les méthodes sont statiques pour faciliter l'usage.
    """

    @staticmethod
    def normalize(raw_status: Optional[str], default: str = "to_process") -> str:
        """
        Normalise un statut brut vers sa valeur standardisée.

        Args:
            raw_status: Statut brut (ex: "running", "in queue", "success")
            default: Valeur par défaut si le statut est None ou inconnu

        Returns:
            Statut normalisé (ex: "on_process", "in_queue", "completed")

        Examples:
            >>> StatusNormalizer.normalize("running")
            'on_process'
            >>> StatusNormalizer.normalize("in queue")
            'in_queue'
            >>> StatusNormalizer.normalize("success")
            'completed'
            >>> StatusNormalizer.normalize(None)
            'to_process'
            >>> StatusNormalizer.normalize("unknown_status")
            'unknown_status'  # Passthrough si inconnu
        """
        if not raw_status:
            return default

        normalized_key = raw_status.lower().strip()

        # Chercher dans le mapping
        if normalized_key in RAW_TO_NORMALIZED:
            return RAW_TO_NORMALIZED[normalized_key].value

        # Si pas trouvé, retourner tel quel (passthrough)
        logger.debug(f"Status '{raw_status}' not in mapping, passing through")
        return normalized_key

    @staticmethod
    def normalize_for_function(
        function_name: Optional[str],
        raw_status: Optional[str],
        default: str = "to_process"
    ) -> str:
        """
        Normalise un statut avec prise en compte du contexte de fonction.
        Certaines fonctions (Router, Banker, etc.) ont des règles spécifiques.

        Args:
            function_name: Nom de la fonction (ex: "Router", "Banker", "APbookeeper")
            raw_status: Statut brut
            default: Valeur par défaut

        Returns:
            Statut normalisé selon les règles de la fonction

        Examples:
            >>> StatusNormalizer.normalize_for_function("Router", "success")
            'routed'  # Router override: success → routed
            >>> StatusNormalizer.normalize_for_function("Banker", "success")
            'completed'  # Banker: success → completed (standard)
        """
        if not raw_status:
            return default

        normalized_key = raw_status.lower().strip()

        # Vérifier les overrides spécifiques à la fonction
        if function_name:
            func_key = function_name.lower().strip()
            if func_key in FUNCTION_SPECIFIC_OVERRIDES:
                overrides = FUNCTION_SPECIFIC_OVERRIDES[func_key]
                if normalized_key in overrides:
                    result = overrides[normalized_key].value
                    logger.debug(
                        f"Function '{function_name}' override: "
                        f"'{raw_status}' → '{result}'"
                    )
                    return result

        # Pas d'override, utiliser le mapping standard
        return StatusNormalizer.normalize(raw_status, default)

    @staticmethod
    def get_category(normalized_status: str) -> str:
        """
        Retourne la catégorie (onglet UI) pour un statut normalisé.

        Args:
            normalized_status: Statut déjà normalisé

        Returns:
            Catégorie: "to_process", "in_process", "pending", ou "processed"

        Examples:
            >>> StatusNormalizer.get_category("on_process")
            'in_process'
            >>> StatusNormalizer.get_category("completed")
            'processed'
            >>> StatusNormalizer.get_category("error")
            'to_process'
        """
        # Convertir en enum si c'est une string
        try:
            status_enum = NormalizedStatus(normalized_status)
            if status_enum in NORMALIZED_TO_CATEGORY:
                return NORMALIZED_TO_CATEGORY[status_enum].value
        except ValueError:
            pass

        # Par défaut, mettre dans "to_process"
        logger.debug(
            f"Category not found for status '{normalized_status}', "
            f"defaulting to 'to_process'"
        )
        return StatusCategory.TO_PROCESS.value

    @staticmethod
    def is_valid_status(status: str) -> bool:
        """
        Vérifie si un statut est une valeur normalisée valide.

        Args:
            status: Statut à vérifier

        Returns:
            True si c'est un statut normalisé valide
        """
        try:
            NormalizedStatus(status)
            return True
        except ValueError:
            return False

    @staticmethod
    def is_in_progress(status: str) -> bool:
        """
        Vérifie si un statut indique un traitement en cours.

        Args:
            status: Statut (brut ou normalisé)

        Returns:
            True si le statut indique un traitement actif
        """
        normalized = StatusNormalizer.normalize(status)
        return normalized in (
            NormalizedStatus.IN_QUEUE.value,
            NormalizedStatus.ON_PROCESS.value,
            NormalizedStatus.STOPPING.value,
        )

    @staticmethod
    def is_completed(status: str) -> bool:
        """
        Vérifie si un statut indique une fin de traitement.

        Args:
            status: Statut (brut ou normalisé)

        Returns:
            True si le statut indique un traitement terminé
        """
        normalized = StatusNormalizer.normalize(status)
        return normalized in (
            NormalizedStatus.COMPLETED.value,
            NormalizedStatus.ROUTED.value,
        )

    @staticmethod
    def is_error(status: str) -> bool:
        """
        Vérifie si un statut indique une erreur.

        Args:
            status: Statut (brut ou normalisé)

        Returns:
            True si le statut indique une erreur
        """
        normalized = StatusNormalizer.normalize(status)
        return normalized == NormalizedStatus.ERROR.value
