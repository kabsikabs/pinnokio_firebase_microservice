"""
Script de migration pour ajouter les métadonnées manquantes aux chats dans active_chats.

Ce script parcourt tous les threads dans /{space_code}/active_chats et ajoute les champs
manquants selon les règles suivantes :
- thread_name: même nom que le thread_key
- thread_key: clé du thread
- created_at: date d'aujourd'hui (ISO 8601 UTC)
- created_by: '7hQs0jluP5YUWcREqdi22NRFnU32'
- chat_mode: déterminé selon le préfixe du thread_key
  - 'apbookeeper_chat' si commence par 'klk_'
  - 'banker_chat' si commence par 'bank_batch'
  - 'router_chat' pour tous les autres
"""

import sys
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env (comme dans le reste du projet)
load_dotenv()

# Ajouter le répertoire parent au path pour les imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app.firebase_providers import get_firebase_realtime
import logging

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Modes de chat exacts comme dans llm_manager
CHAT_MODES = {
    'apbookeeper_chat': 'apbookeeper_chat',
    'banker_chat': 'banker_chat',
    'router_chat': 'router_chat'
}

DEFAULT_CREATED_BY = '7hQs0jluP5YUWcREqdi22NRFnU32'


def determine_chat_mode(thread_key: str) -> str:
    """
    Détermine le chat_mode selon le préfixe du thread_key.
    
    Args:
        thread_key: Clé du thread
        
    Returns:
        Mode de chat: 'apbookeeper_chat', 'banker_chat', ou 'router_chat'
    """
    if thread_key.startswith('klk_'):
        return CHAT_MODES['apbookeeper_chat']
    elif thread_key.startswith('bank_batch'):
        return CHAT_MODES['banker_chat']
    else:
        return CHAT_MODES['router_chat']


def get_missing_fields(thread_data: Dict[str, Any], thread_key: str) -> Dict[str, Any]:
    """
    Détermine les champs manquants et leurs valeurs par défaut.
    
    Args:
        thread_data: Données actuelles du thread
        thread_key: Clé du thread
        
    Returns:
        Dictionnaire des champs à ajouter/mettre à jour
    """
    missing_fields = {}
    
    # Vérifier chaque champ requis
    required_fields = {
        'thread_name': thread_key,
        'thread_key': thread_key,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'created_by': DEFAULT_CREATED_BY,
        'chat_mode': determine_chat_mode(thread_key)
    }
    
    for field, default_value in required_fields.items():
        if field not in thread_data or thread_data.get(field) is None:
            missing_fields[field] = default_value
            logger.info(f"  ⚠️  Champ manquant: {field} → {default_value}")
        else:
            logger.debug(f"  ✅ Champ présent: {field} = {thread_data.get(field)}")
    
    return missing_fields


def migrate_active_chats_metadata(
    space_code: str = "AAAAgaDzK_I",
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Migre les métadonnées des chats dans active_chats.
    
    Args:
        space_code: Code de l'espace (défaut: "AAAAgaDzK_I")
        dry_run: Si True, affiche seulement ce qui serait modifié sans écrire
        
    Returns:
        Statistiques de la migration
    """
    logger.info("=" * 80)
    logger.info(f"🚀 DÉMARRAGE MIGRATION active_chats")
    logger.info(f"   Space code: {space_code}")
    logger.info(f"   Mode: {'DRY-RUN (simulation)' if dry_run else 'EXÉCUTION'}")
    logger.info("=" * 80)
    
    stats = {
        'total_threads': 0,
        'threads_updated': 0,
        'threads_skipped': 0,
        'threads_errors': 0,
        'errors': []
    }
    
    try:
        # Obtenir la connexion RTDB
        rtdb = get_firebase_realtime()
        logger.info("✅ Connexion RTDB établie")
        
        # Chemin vers active_chats
        active_chats_path = f"{space_code}/active_chats"
        logger.info(f"📂 Parcours du chemin: {active_chats_path}")
        
        # Récupérer tous les threads
        active_chats_ref = rtdb.db.child(active_chats_path)
        all_threads = active_chats_ref.get()
        
        if not all_threads:
            logger.warning(f"⚠️  Aucun thread trouvé dans {active_chats_path}")
            return stats
        
        logger.info(f"📊 {len(all_threads)} thread(s) trouvé(s)")
        logger.info("-" * 80)
        
        # Parcourir chaque thread
        for thread_key, thread_data in all_threads.items():
            stats['total_threads'] += 1
            
            logger.info(f"\n🔍 Thread: {thread_key}")
            
            try:
                # Vérifier si thread_data est un dictionnaire
                if not isinstance(thread_data, dict):
                    logger.warning(f"  ⚠️  Données invalides (pas un dict), skip")
                    stats['threads_skipped'] += 1
                    continue
                
                # Déterminer les champs manquants
                missing_fields = get_missing_fields(thread_data, thread_key)
                
                if not missing_fields:
                    logger.info(f"  ✅ Tous les champs requis sont présents")
                    stats['threads_skipped'] += 1
                    continue
                
                # Afficher ce qui sera ajouté
                logger.info(f"  📝 Champs à ajouter/mettre à jour:")
                for field, value in missing_fields.items():
                    logger.info(f"     - {field}: {value}")
                
                # Appliquer les modifications si pas en dry-run
                if not dry_run:
                    thread_ref = active_chats_ref.child(thread_key)
                    
                    # Mettre à jour chaque champ manquant
                    for field, value in missing_fields.items():
                        thread_ref.child(field).set(value)
                    
                    logger.info(f"  ✅ Thread mis à jour avec succès")
                    stats['threads_updated'] += 1
                else:
                    logger.info(f"  🔍 [DRY-RUN] Modifications non appliquées")
                    stats['threads_updated'] += 1
                    
            except Exception as e:
                error_msg = f"Erreur lors du traitement du thread {thread_key}: {str(e)}"
                logger.error(f"  ❌ {error_msg}")
                stats['threads_errors'] += 1
                stats['errors'].append({
                    'thread_key': thread_key,
                    'error': str(e)
                })
        
        # Résumé final
        logger.info("\n" + "=" * 80)
        logger.info("📊 RÉSUMÉ DE LA MIGRATION")
        logger.info("=" * 80)
        logger.info(f"Total de threads: {stats['total_threads']}")
        logger.info(f"Threads mis à jour: {stats['threads_updated']}")
        logger.info(f"Threads ignorés (déjà complets): {stats['threads_skipped']}")
        logger.info(f"Erreurs: {stats['threads_errors']}")
        
        if stats['errors']:
            logger.warning("\n❌ Erreurs rencontrées:")
            for error in stats['errors']:
                logger.warning(f"  - {error['thread_key']}: {error['error']}")
        
        if dry_run:
            logger.info("\n💡 Pour appliquer les modifications, relancez avec dry_run=False")
        
        return stats
        
    except Exception as e:
        logger.error(f"❌ Erreur fatale lors de la migration: {e}")
        import traceback
        traceback.print_exc()
        stats['errors'].append({
            'thread_key': 'GLOBAL',
            'error': str(e)
        })
        return stats


def main():
    """Point d'entrée principal du script."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Migre les métadonnées des chats dans active_chats'
    )
    parser.add_argument(
        '--space-code',
        type=str,
        default='AAAAgaDzK_I',
        help='Code de l\'espace (défaut: AAAAgaDzK_I)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Exécute réellement les modifications (par défaut: dry-run)'
    )
    
    args = parser.parse_args()
    
    # Exécuter la migration
    stats = migrate_active_chats_metadata(
        space_code=args.space_code,
        dry_run=not args.execute
    )
    
    # Code de sortie
    if stats['threads_errors'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
