#!/bin/bash
# Deletes the local personal memory database after an explicit confirmation; no bundled data ships in the repository.
set -euo pipefail
cd "$(dirname "$0")"
DIR="${PODA_DATA_DIR:-$HOME/Library/Application Support/PODA-Public}"
DB="$DIR/poda.db"
read -r -p "This permanently deletes PODA's local chat, memories, and database. Type DELETE ALL: " CONFIRM
[ "$CONFIRM" = 'DELETE ALL' ] || { echo 'Cancelled'; exit 1; }
[ ! -f "$DB" ] || { echo 'Stop PODA first. Create a secure backup if needed.'; rm -f -- "$DB" "$DB-wal" "$DB-shm"; }
echo "Cleared the local database; the next start creates a new blank personal memory graph."
