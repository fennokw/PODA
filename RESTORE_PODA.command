#!/bin/bash
# Restores an encrypted backup. The current database is kept as .backups/poda-before-restore-<stamp>.db.
set -euo pipefail
cd "$(dirname "$0")"
./STOP_PODA.command || true
.venv/bin/python - <<'PY'
import sys
from poda_app.security.crypto import list_backups, restore_encrypted_backup
backups = list_backups()
if not backups:
    print("No encrypted backups found in the private application-support backups directory"); sys.exit(1)
for i, b in enumerate(backups): print(f"[{i}] {b['filename']}  created {b.get('created_at')}  build {b.get('build')}  {b['bytes']} bytes")
choice = input("Restore which backup? [index, blank to cancel]: ").strip()
if not choice: sys.exit(0)
b = backups[int(choice)]
if input(f"Type RESTORE to replace the live database with {b['filename']}: ").strip() != "RESTORE": sys.exit(0)
print(restore_encrypted_backup(b["filename"]))
PY
