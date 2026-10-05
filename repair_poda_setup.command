#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
echo "Repairing PODA Python environment..."
if [ -d .venv ]; then
  mv .venv ".venv.failed.$(date +%Y%m%d-%H%M%S)"
fi
./setup_poda.command
