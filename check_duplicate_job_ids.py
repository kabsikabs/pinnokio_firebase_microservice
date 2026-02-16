#!/usr/bin/env python3
"""
Script pour analyser les documents Firebase et trouver les doublons de job_id.

Usage:
    python check_duplicate_job_ids.py

Le script:
1. Se connecte à Firebase
2. Lit tous les documents dans le chemin spécifié
3. Identifie les documents avec des job_id identiques
4. Affiche le nombre total de documents et les firebase_doc_id concernés
"""

import sys
import os
import csv
from collections import defaultdict
from typing import Dict, List, Any
from datetime import datetime

# Charger les variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Variables d'environnement chargées depuis .env")
except ImportError:
    print("⚠️  python-dotenv non installé, utilisation des variables d'environnement système")

# Ajouter le répertoire racine au path pour les imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app.firebase_client import get_firestore
    from app.firebase_providers import get_firebase_management
except ImportError as e:
    print(f"❌ Erreur d'import: {e}")
    print("Assurez-vous d'être dans le répertoire du projet et que l'environnement virtuel est activé")
    sys.exit(1)


def analyze_all_departments_journals(klk_vision_path: str, output_filename: str = None) -> None:
    """
    Analyse tous les documents dans klk_vision et leurs sous-collections journal.
    
    Args:
        klk_vision_path: Chemin vers la collection klk_vision (ex: 'clients/{uid}/klk_vision')
        output_filename: Nom du fichier CSV de sortie (si None, utilise le nom existant)
    """
    print("=" * 80)
    print("🔍 ANALYSE DE TOUS LES DÉPARTEMENTS ET JOURNAUX")
    print("=" * 80)
    print(f"\n📂 Chemin analysé: {klk_vision_path}\n")
    
    try:
        # Vérifier les variables d'environnement nécessaires
        print("🔍 Vérification des variables d'environnement...")
        firebase_admin_json = os.getenv("FIREBASE_ADMIN_JSON")
        google_project_id = os.getenv("GOOGLE_PROJECT_ID")
        
        if not firebase_admin_json and not google_project_id:
            print("❌ Variables d'environnement manquantes!")
            print("   Vous devez définir soit:")
            print("   - FIREBASE_ADMIN_JSON (recommandé pour développement local)")
            print("   - GOOGLE_PROJECT_ID (si vous utilisez Google Secret Manager)")
            print("\n   Vérifiez votre fichier .env ou vos variables d'environnement système.")
            return
        
        if firebase_admin_json:
            print("✅ FIREBASE_ADMIN_JSON trouvé")
        if google_project_id:
            print(f"✅ GOOGLE_PROJECT_ID trouvé: {google_project_id}")
        
        # Initialiser Firebase
        print("\n🔌 Connexion à Firebase...")
        db = get_firestore()
        print("✅ Connexion réussie\n")
        
        # Parser le chemin
        # Format attendu: 'clients/{uid}/klk_vision'
        parts = klk_vision_path.strip('/').split('/')
        
        # Vérifier le format: clients / uid / klk_vision
        if len(parts) != 3:
            print(f"❌ Format de chemin invalide. Attendu 3 parties, reçu {len(parts)}")
            print(f"   Format attendu: 'clients/{{uid}}/klk_vision'")
            print(f"   Reçu: {klk_vision_path}")
            return
        
        if parts[0] != 'clients' or parts[2] != 'klk_vision':
            print(f"❌ Format de chemin invalide.")
            print(f"   Attendu: 'clients/{{uid}}/klk_vision'")
            print(f"   Reçu: {klk_vision_path}")
            return
        
        # Construire la référence à la collection klk_vision
        uid = parts[1]
        klk_vision_ref = (db.collection('clients')
                         .document(uid)
                         .collection('klk_vision'))
        
        print(f"📖 Lecture de tous les documents dans klk_vision...")
        
        # Récupérer tous les documents de klk_vision
        dept_docs = klk_vision_ref.stream()
        
        # Dictionnaire pour regrouper par job_id
        job_id_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        total_docs = 0
        total_dept_docs = 0
        
        # Liste pour stocker tous les documents avec leurs données complètes
        all_documents_data: List[Dict[str, Any]] = []
        
        # Parcourir tous les documents de klk_vision
        for dept_doc in dept_docs:
            total_dept_docs += 1
            dept_doc_data = dept_doc.to_dict()
            dept_doc_id = dept_doc.id
            
            # Extraire le champ 'departement' du document parent
            departement = dept_doc_data.get('departement', '') if dept_doc_data else ''
            
            print(f"  📁 Département '{departement}' (doc_id: {dept_doc_id}) - Lecture du journal...")
            
            # Accéder à la sous-collection journal de ce document
            journal_ref = klk_vision_ref.document(dept_doc_id).collection('journal')
            journal_docs = journal_ref.stream()
            
            # Parcourir tous les documents du journal
            for doc in journal_docs:
                total_docs += 1
                doc_data = doc.to_dict()
                firebase_doc_id = doc.id
                
                # Extraire les données (peuvent être dans 'data' ou directement dans doc_data)
                data_dict = doc_data.get('data', {}) if isinstance(doc_data.get('data'), dict) else doc_data
                
                # Extraire job_id
                job_id = data_dict.get('job_id', '') or doc_data.get('job_id', '')
                if not job_id:
                    job_id = f"NO_JOB_ID_{firebase_doc_id}"
                
                # Extraire file_name et créer file_name_wo_ext
                file_name = data_dict.get('file_name', '') or data_dict.get('name', '') or ''
                file_name_wo_ext = os.path.splitext(file_name)[0] if file_name else ''
                
                # Extraire timestamp et le formater
                timestamp = data_dict.get('timestamp', '') or doc_data.get('timestamp', '')
                if timestamp:
                    if hasattr(timestamp, 'strftime'):
                        # C'est un objet datetime
                        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                    elif isinstance(timestamp, str):
                        timestamp_str = timestamp
                    else:
                        timestamp_str = str(timestamp)
                else:
                    timestamp_str = ''
                
                # Préparer les données du document
                document_row = {
                    'doc_id': firebase_doc_id,
                    'client': data_dict.get('client', '') or '',
                    'drive_file_id': data_dict.get('drive_file_id', '') or data_dict.get('drive_file_id', '') or '',
                    'file_name': file_name,
                    'file_name_wo_ext': file_name_wo_ext,
                    'job_id': job_id,
                    'legal_name': data_dict.get('legal_name', '') or '',
                    'mandat_id': data_dict.get('mandat_id', '') or doc_data.get('mandat_id', '') or '',
                    'mandat_name': data_dict.get('mandat_name', '') or '',
                    'pinnokio_func': data_dict.get('pinnokio_func', '') or data_dict.get('function_name', '') or '',
                    'source': data_dict.get('source', '') or '',
                    'status': data_dict.get('status', '') or '',
                    'timestamp': timestamp_str,
                    'departement': departement  # Ajouter le département du document parent
                }
                
                all_documents_data.append(document_row)
                
                # Ajouter à la liste des documents avec ce job_id (pour l'analyse des doublons)
                job_id_groups[job_id].append({
                    'firebase_doc_id': firebase_doc_id,
                    'data': doc_data
                })
        
        print(f"\n✅ {total_dept_docs} documents de départements trouvés")
        print(f"✅ {total_docs} documents de journal trouvés au total\n")
        
        # Identifier les doublons (job_id avec plus d'un document)
        duplicates: Dict[str, List[str]] = {}
        for job_id, docs_list in job_id_groups.items():
            if len(docs_list) > 1:
                duplicates[job_id] = [doc['firebase_doc_id'] for doc in docs_list]
        
        # Déterminer le nom du fichier CSV
        if output_filename:
            csv_filename = output_filename
        else:
            # Utiliser le nom du fichier existant s'il existe, sinon créer un nouveau nom
            existing_file = "router_documents_20260121_142659.csv"
            if os.path.exists(existing_file):
                csv_filename = existing_file
            else:
                csv_filename = f"router_documents_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        print(f"📝 Génération du fichier CSV: {csv_filename}")
        
        # Définir les colonnes CSV (ajouter 'departement' à la fin)
        csv_columns = [
            'doc_id', 'client', 'drive_file_id', 'file_name', 'file_name_wo_ext',
            'job_id', 'legal_name', 'mandat_id', 'mandat_name', 'pinnokio_func',
            'source', 'status', 'timestamp', 'departement'
        ]
        
        # Écrire le fichier CSV
        try:
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=csv_columns)
                writer.writeheader()
                for doc_row in all_documents_data:
                    writer.writerow(doc_row)
            
            print(f"✅ Fichier CSV créé avec succès: {csv_filename}")
            print(f"   📊 {len(all_documents_data)} lignes écrites\n")
        except Exception as csv_error:
            print(f"❌ Erreur lors de la création du CSV: {csv_error}\n")
        
        # Afficher les résultats de l'analyse des doublons
        print("=" * 80)
        print("📊 RÉSULTATS DE L'ANALYSE")
        print("=" * 80)
        print(f"\n📈 Nombre total de documents: {total_docs}")
        print(f"🔑 Nombre de job_id uniques: {len(job_id_groups)}")
        print(f"⚠️  Nombre de job_id en doublon: {len(duplicates)}\n")
        
        if duplicates:
            print("=" * 80)
            print("🔴 DOUBLONS TROUVÉS (job_id avec plusieurs documents)")
            print("=" * 80)
            
            total_duplicate_docs = 0
            for job_id, doc_ids in duplicates.items():
                count = len(doc_ids)
                total_duplicate_docs += count
                print(f"\n🔑 job_id: {job_id}")
                print(f"   📄 Nombre de documents: {count}")
                print(f"   🆔 firebase_doc_id:")
                for doc_id in doc_ids:
                    print(f"      - {doc_id}")
            
            print(f"\n📊 Total de documents en doublon: {total_duplicate_docs}")
        else:
            print("✅ Aucun doublon trouvé - tous les job_id sont uniques !")
        
        print("\n" + "=" * 80)
        
    except Exception as e:
        print(f"\n❌ Erreur lors de l'analyse: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Chemin vers la collection klk_vision
    klk_vision_path = "/clients/7hQs0jluP5YUWcREqdi22NRFnU32/klk_vision"
    
    # Nettoyer le chemin (enlever les slashes en début/fin)
    klk_vision_path = klk_vision_path.strip('/')
    
    # Nom du fichier de sortie (même nom que l'existant pour rafraîchir Excel)
    output_file = "router_documents_20260121_142659.csv"
    
    analyze_all_departments_journals(klk_vision_path, output_file)
