#!/usr/bin/env python3
"""
Script pour télécharger les logs CloudWatch.

Usage:
    python scripts/download_cloudwatch_logs.py list
    python scripts/download_cloudwatch_logs.py download <log_stream_name>
    python scripts/download_cloudwatch_logs.py download <log_stream_name> --output logs/my_log.log
    python scripts/download_cloudwatch_logs.py download <log_stream_name> --json
    python scripts/download_cloudwatch_logs.py info
    python scripts/download_cloudwatch_logs.py check-credentials
"""

import sys
import os
import argparse
from datetime import datetime, timedelta

# Ajouter le répertoire parent au path pour importer le module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.tools.cloudwatch_logs import CloudWatchLogsExtractor


def list_logs(args):
    """Liste les streams de logs."""
    extractor = CloudWatchLogsExtractor(
        region_name=args.region,
        log_group_name=args.log_group
    )
    
    # Calculer les dates si spécifiées
    start_time = None
    end_time = None
    
    if args.days:
        start_time = datetime.now() - timedelta(days=args.days)
    
    if args.start_date:
        start_time = datetime.fromisoformat(args.start_date)
    
    if args.end_date:
        end_time = datetime.fromisoformat(args.end_date)
    
    streams = extractor.list_log_streams(
        limit=args.limit,
        order_by=args.order_by,
        descending=args.descending,
        start_time=start_time,
        end_time=end_time
    )
    
    if not streams:
        print("Aucun stream de logs trouvé.")
        return
    
    print(f"\n{len(streams)} stream(s) de logs trouvé(s):\n")
    print(f"{'Nom du stream':<60} {'Premier événement':<20} {'Dernier événement':<20} {'Taille (bytes)':<15}")
    print("-" * 120)
    
    for stream in streams:
        name = stream['logStreamName'][:58] + '..' if len(stream['logStreamName']) > 60 else stream['logStreamName']
        first = stream['firstEventTimeFormatted']
        last = stream['lastEventTimeFormatted']
        size = stream['storedBytes']
        print(f"{name:<60} {first:<20} {last:<20} {size:<15}")


def download_log(args):
    """Télécharge un log."""
    extractor = CloudWatchLogsExtractor(
        region_name=args.region,
        log_group_name=args.log_group
    )
    
    # Calculer les dates si spécifiées
    start_time = None
    end_time = None
    
    if args.start_date:
        start_time = datetime.fromisoformat(args.start_date)
    
    if args.end_date:
        end_time = datetime.fromisoformat(args.end_date)
    
    try:
        if args.json:
            output_file = extractor.download_log_json(
                log_stream_name=args.log_stream_name,
                output_file=args.output,
                start_time=start_time,
                end_time=end_time
            )
        else:
            output_file = extractor.download_log(
                log_stream_name=args.log_stream_name,
                output_file=args.output,
                start_time=start_time,
                end_time=end_time
            )
        
        print(f"\n✓ Log téléchargé avec succès: {output_file}")
    except Exception as e:
        print(f"\n✗ Erreur lors du téléchargement: {e}")
        sys.exit(1)


def show_info(args):
    """Affiche les informations sur le groupe de journaux."""
    extractor = CloudWatchLogsExtractor(
        region_name=args.region,
        log_group_name=args.log_group
    )
    
    try:
        info = extractor.get_log_group_info()
        
        if not info:
            print(f"Le groupe de journaux '{args.log_group}' n'existe pas ou n'est pas accessible.")
            return
        
        print(f"\nInformations sur le groupe de journaux: {info['logGroupName']}\n")
        print(f"Créé le: {info.get('creationTimeFormatted', 'N/A')}")
        print(f"Taille totale: {info.get('storedBytes', 0):,} bytes")
        print(f"Rétention: {info.get('retentionInDays', 'Illimitée')} jours")
        print(f"Filtres de métriques: {info.get('metricFilterCount', 0)}")
    except Exception as e:
        print(f"\n✗ Erreur: {e}")
        sys.exit(1)


def check_credentials(args):
    """Vérifie que les credentials AWS sont valides."""
    print("Vérification des credentials AWS...\n")
    
    try:
        extractor = CloudWatchLogsExtractor(
            region_name=args.region,
            log_group_name=args.log_group
        )
        
        if extractor.check_credentials():
            print("✓ Credentials AWS valides et accessibles")
            
            # Tente aussi de récupérer les infos du groupe
            try:
                info = extractor.get_log_group_info()
                if info:
                    print(f"✓ Groupe de journaux '{args.log_group}' accessible")
                else:
                    print(f"⚠ Groupe de journaux '{args.log_group}' non trouvé (mais credentials valides)")
            except Exception as e:
                print(f"⚠ Erreur lors de l'accès au groupe: {e}")
        else:
            print("✗ Credentials AWS invalides ou non trouvés")
            print("\nPour configurer les credentials, utilisez:")
            print("  - Variables d'environnement: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
            print("  - Fichier: ~/.aws/credentials (ou %USERPROFILE%\\.aws\\credentials sur Windows)")
            print("  - IAM Role (si sur EC2/ECS)")
            sys.exit(1)
    except Exception as e:
        print(f"✗ Erreur: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Outil pour extraire les logs CloudWatch',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  # Vérifier les credentials AWS
  python scripts/download_cloudwatch_logs.py check-credentials
  
  # Lister les 10 derniers streams
  python scripts/download_cloudwatch_logs.py list --limit 10
  
  # Lister les streams des 7 derniers jours
  python scripts/download_cloudwatch_logs.py list --days 7
  
  # Télécharger un log
  python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123
  
  # Télécharger un log au format JSON
  python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123 --json
  
  # Télécharger un log avec filtre de date
  python scripts/download_cloudwatch_logs.py download ecs/pinnokio_microservice/abc123 --start-date 2025-01-01 --end-date 2025-01-02
        """
    )
    
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='Région AWS (défaut: us-east-1)'
    )
    
    parser.add_argument(
        '--log-group',
        default='/ecs/pinnokio_microservice',
        help='Nom du groupe de journaux (défaut: /ecs/pinnokio_microservice)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commandes disponibles')
    
    # Commande check-credentials
    check_parser = subparsers.add_parser('check-credentials', help='Vérifie que les credentials AWS sont valides')
    
    # Commande list
    list_parser = subparsers.add_parser('list', help='Liste les streams de logs')
    list_parser.add_argument('--limit', type=int, help='Nombre maximum de streams à afficher')
    list_parser.add_argument('--order-by', choices=['LastEventTime', 'LogStreamName'], default='LastEventTime',
                            help='Critère de tri (défaut: LastEventTime)')
    list_parser.add_argument('--ascending', action='store_true', help='Tri croissant (défaut: décroissant)')
    list_parser.add_argument('--days', type=int, help='Filtrer les streams des N derniers jours')
    list_parser.add_argument('--start-date', help='Date de début (format: YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)')
    list_parser.add_argument('--end-date', help='Date de fin (format: YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)')
    
    # Commande download
    download_parser = subparsers.add_parser('download', help='Télécharge un log')
    download_parser.add_argument('log_stream_name', help='Nom du stream de logs à télécharger')
    download_parser.add_argument('--output', '-o', help='Fichier de sortie (optionnel)')
    download_parser.add_argument('--json', action='store_true', help='Télécharger au format JSON')
    download_parser.add_argument('--start-date', help='Date de début (format: YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)')
    download_parser.add_argument('--end-date', help='Date de fin (format: YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)')
    
    # Commande info
    info_parser = subparsers.add_parser('info', help='Affiche les informations sur le groupe de journaux')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Corriger l'argument descending
    if hasattr(args, 'ascending'):
        args.descending = not args.ascending
    else:
        args.descending = True
    
    if args.command == 'check-credentials':
        check_credentials(args)
    elif args.command == 'list':
        list_logs(args)
    elif args.command == 'download':
        download_log(args)
    elif args.command == 'info':
        show_info(args)


if __name__ == '__main__':
    main()

