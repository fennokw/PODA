#!/bin/bash
# Creates an AES-256-GCM encrypted backup of the PODA database (key in macOS Keychain: PODA.Public.BackupKey).
set -euo pipefail
cd "$(dirname "$0")"
.venv/bin/python - <<'PY'
from poda_app.runtime.db import init_database
from poda_app.security.crypto import create_encrypted_backup, list_backups
init_database()
r = create_encrypted_backup()
print(f"Encrypted backup written: {r['path']} ({r['bytes']} bytes)")
print("Backup key lives in macOS Keychain (service PODA.Public.BackupKey). To restore on another Mac you must export that key:")
print("  security find-generic-password -s PODA.Public.BackupKey -a primary -w")
print(f"Backups available: {len(list_backups())}")
PY
