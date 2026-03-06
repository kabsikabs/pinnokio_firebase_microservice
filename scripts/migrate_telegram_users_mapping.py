#!/usr/bin/env python3
"""
Script de migration : telegram_users_mapping → telegram_users/{username}/authorized_mandates.

Pour la retro-compatibilite, copie le dictionnaire `telegram_users_mapping` stocke dans
chaque document mandate vers les entrees correspondantes dans la collection
`telegram_users/{username}/authorized_mandates/{mandate_path}`.

Usage:
    # Dry-run (affiche les changements sans les appliquer)
    python scripts/migrate_telegram_users_mapping.py

    # Appliquer les changements
    python scripts/migrate_telegram_users_mapping.py --apply

    # Specifier un autre utilisateur Telegram
    python scripts/migrate_telegram_users_mapping.py --telegram-user kabsikabs --apply
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Ensure repo root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import firebase_admin
from firebase_admin import credentials, firestore

# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_USERNAME = "kabsikabs"

MANDATE_PATHS = [
    "clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/LaCWd6ltASD2vgCl8J01/mandates/ZhnLigKULKQOoZhcW9Fp",
    "clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/PX2cdw7FwckdwO47hqHT/mandates/NWt8OEeP1jW8Jy1Uuih0",
    "clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/PX2cdw7FwckdwO47hqHT/mandates/ddKZ6YRU9MaanMR44rDT",
    "clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/Xfr79IdgzN6huROyKvhP/mandates/IYeVcwxU6f0Hy5bB1ydC",
    "clients/7hQs0jluP5YUWcREqdi22NRFnU32/bo_clients/kXAQYwMgsMrV60jeVcuz/mandates/BIr6edJxlNeKUBZxNhu4",
]


# ============================================================
# FIREBASE INIT (reuse backend auth)
# ============================================================

def init_firebase():
    """Initialize Firebase using backend credentials."""
    if firebase_admin._apps:
        return firestore.client()

    # Try GOOGLE_APPLICATION_CREDENTIALS file first
    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_file and os.path.exists(creds_file):
        cred = credentials.Certificate(creds_file)
        firebase_admin.initialize_app(cred)
        print(f"  Firebase initialized from GOOGLE_APPLICATION_CREDENTIALS: {creds_file}")
        return firestore.client()

    # Fallback: FIRESTORE_SERVICE_ACCOUNT_SECRET (JSON string in env or Secret Manager)
    import json
    secret_name = os.getenv("FIRESTORE_SERVICE_ACCOUNT_SECRET")
    if secret_name:
        try:
            # Try parsing as JSON directly
            sa_info = json.loads(secret_name)
            cred = credentials.Certificate(sa_info)
        except (json.JSONDecodeError, ValueError):
            # It's a Secret Manager secret name
            from app.secrets_client import get_secret
            sa_json = get_secret(secret_name)
            sa_info = json.loads(sa_json)
            cred = credentials.Certificate(sa_info)
        firebase_admin.initialize_app(cred)
        print("  Firebase initialized from FIRESTORE_SERVICE_ACCOUNT_SECRET")
        return firestore.client()

    raise RuntimeError(
        "No Firebase credentials found. Set GOOGLE_APPLICATION_CREDENTIALS or FIRESTORE_SERVICE_ACCOUNT_SECRET."
    )


# ============================================================
# MIGRATION LOGIC
# ============================================================

def migrate(db, telegram_username: str, apply: bool):
    """
    For each mandate_path:
      1. Read telegram_users_mapping from the mandate document
      2. Read telegram_users/{telegram_username} document
      3. Find the authorized_mandates entry matching mandate_path
      4. Add telegram_users_mapping to that entry
    """
    print(f"\n{'=' * 60}")
    print(f"  Migration telegram_users_mapping → telegram_users/{telegram_username}")
    print(f"  Mode: {'APPLY' if apply else 'DRY-RUN'}")
    print(f"{'=' * 60}\n")

    # Read the telegram user document once
    tg_user_ref = db.collection("telegram_users").document(telegram_username)
    tg_user_doc = tg_user_ref.get()

    if not tg_user_doc.exists:
        print(f"  telegram_users/{telegram_username} NOT FOUND - aborting")
        return

    tg_user_data = tg_user_doc.to_dict()
    authorized_mandates = tg_user_data.get("authorized_mandates", {})
    updated = False
    stats = {"found": 0, "updated": 0, "skipped": 0, "missing_mandate": 0, "missing_entry": 0}

    for mandate_path in MANDATE_PATHS:
        print(f"\n  --- {mandate_path}")

        # Step 1: Read telegram_users_mapping from mandate document
        mandate_doc = db.document(mandate_path).get()
        if not mandate_doc.exists:
            print(f"      Mandate document NOT FOUND - skipping")
            stats["missing_mandate"] += 1
            continue

        mandate_data = mandate_doc.to_dict()
        telegram_users_mapping = mandate_data.get("telegram_users_mapping")

        if not telegram_users_mapping:
            print(f"      No telegram_users_mapping in mandate - skipping")
            stats["skipped"] += 1
            continue

        stats["found"] += 1
        print(f"      telegram_users_mapping: {telegram_users_mapping}")

        # Step 2: Check if this mandate_path exists in authorized_mandates
        if mandate_path not in authorized_mandates:
            print(f"      Entry NOT FOUND in authorized_mandates of {telegram_username} - skipping")
            stats["missing_entry"] += 1
            continue

        # Step 3: Check if already has telegram_users_mapping
        existing_mapping = authorized_mandates[mandate_path].get("telegram_users_mapping")
        if existing_mapping == telegram_users_mapping:
            print(f"      Already up-to-date - skipping")
            stats["skipped"] += 1
            continue

        # Step 4: Add telegram_users_mapping to the authorized_mandate entry
        print(f"      -> Adding telegram_users_mapping to authorized_mandates entry")
        if existing_mapping:
            print(f"         (replacing existing: {existing_mapping})")
        authorized_mandates[mandate_path]["telegram_users_mapping"] = telegram_users_mapping
        updated = True
        stats["updated"] += 1

    # Apply changes
    if updated and apply:
        tg_user_ref.update({
            "authorized_mandates": authorized_mandates,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"\n  APPLIED: telegram_users/{telegram_username} updated")
    elif updated:
        print(f"\n  DRY-RUN: Would update telegram_users/{telegram_username}")
    else:
        print(f"\n  No changes needed")

    # Summary
    print(f"\n  Summary:")
    print(f"    Mandate paths checked:  {len(MANDATE_PATHS)}")
    print(f"    Mapping found:          {stats['found']}")
    print(f"    Entries updated:        {stats['updated']}")
    print(f"    Skipped (up-to-date):   {stats['skipped']}")
    print(f"    Missing mandate doc:    {stats['missing_mandate']}")
    print(f"    Missing auth entry:     {stats['missing_entry']}")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Migrate telegram_users_mapping to telegram_users collection")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument("--telegram-user", default=TELEGRAM_USERNAME, help=f"Telegram username (default: {TELEGRAM_USERNAME})")
    args = parser.parse_args()

    db = init_firebase()
    migrate(db, args.telegram_user, args.apply)


if __name__ == "__main__":
    main()
