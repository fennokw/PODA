#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[ -f requirements.txt ] || { echo "Stage the local runtime with npm run stage:backend."; exit 1; }
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
DATA="${PODA_DATA_DIR:-$HOME/Library/Application Support/PODA-Public}"
HASH="$(shasum -a 256 requirements.txt | cut -c1-16)"
umask 077
mkdir -p "$DATA/desktop-runtime"
export PODA_VENV_DIR="$DATA/desktop-runtime/$HASH"
if [ -f "$PODA_VENV_DIR/.ready" ] && [ -x "$PODA_VENV_DIR/bin/python" ]; then
  echo "PODA runtime is ready."
  exit 0
fi
./setup-runtime.command
touch "$PODA_VENV_DIR/.ready"
echo "Setup finished. Open PODA.app again."
