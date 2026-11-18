#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
ROOT="$(pwd -P)"

if [ "$(uname -m)" != arm64 ]; then
  echo "This build requires an Apple Silicon Mac and a native Terminal session."
  exit 1
fi
if ! command -v npm >/dev/null || ! command -v python3 >/dev/null; then
  echo "Install Node.js 24 and Python 3.11 or newer, then run Install-PODA.command again."
  exit 1
fi

# Keep the running app bundle intact while another backend owns this profile.
python3 - <<'PYTHON'
from pathlib import Path
from poda_app.runtime.instance import DataDirectoryInUse, claim_data_directory
try:
    installed_backend = Path("dist/desktop/PODA-darwin-arm64/PODA.app/Contents/Resources/backend/desktop_backend.py").resolve()
    with claim_data_directory(wait_seconds=18, desktop_entrypoint=installed_backend):
        pass
except DataDirectoryInUse as exc:
    raise SystemExit(f"{exc}\nIf the other server is running in Terminal, stop it there before reinstalling.") from None
PYTHON

(
  cd desktop
  npm ci --ignore-scripts
  npm run package:mac
)
APP="$ROOT/dist/desktop/PODA-darwin-arm64/PODA.app"
"$APP/Contents/Resources/backend/SETUP_DESKTOP.command"

SHORTCUT="$HOME/Desktop/PODA.app"
mkdir -p "$HOME/Desktop"
if [ -e "$SHORTCUT" ] && [ ! -L "$SHORTCUT" ]; then
  SHORTCUT="$HOME/Desktop/PODA Desktop.app"
fi
if [ -e "$SHORTCUT" ] && [ ! -L "$SHORTCUT" ]; then
  echo "A file already exists at $SHORTCUT. Open $APP directly."
else
  ln -sfn "$APP" "$SHORTCUT"
  echo "Shortcut created at $SHORTCUT"
fi
if [ "${1:-}" != --no-launch ]; then
  open "$APP"
fi
