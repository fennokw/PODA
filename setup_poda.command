#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
echo "PODA setup for macOS (Apple Silicon)"
find_python() {
  for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$candidate"; return 0
    fi
  done
  return 1
}
PYTHON_BIN="$(find_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: Python 3.11+ is required. Install from https://www.python.org/downloads/macos/ and rerun."; open "https://www.python.org/downloads/macos/" || true; exit 1
fi
echo "Using $($PYTHON_BIN --version) at $(command -v "$PYTHON_BIN")"
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
  echo "Existing .venv is too old; rebuilding."; rm -rf .venv
fi
[ -d .venv ] || "$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel >/dev/null
.venv/bin/python -m pip install --upgrade -r requirements.txt

verify_open3d() { .venv/bin/python -c 'import open3d; print(open3d.__version__)'; }
if ! OUT="$(verify_open3d 2>&1)"; then
  echo "Open3D cannot load: $OUT"
  if echo "$OUT" | grep -qi 'libusb'; then
    echo "Open3D needs the native libusb library."
    if ! command -v brew >/dev/null 2>&1; then
      [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    if command -v brew >/dev/null 2>&1; then
      read -r -p "Install libusb with Homebrew now? [Y/n]: " A
      case "${A:-Y}" in n|N|no|NO) echo "Install libusb manually (brew install libusb) and rerun."; exit 1;; *) brew install libusb || brew reinstall libusb;; esac
    else
      echo "Homebrew is not installed. PODA will not install Homebrew for you. Install it from https://brew.sh, then: brew install libusb"; exit 1
    fi
  fi
  verify_open3d >/dev/null || { echo "Open3D still fails to import. Run ./repair_open3d.command"; exit 1; }
fi
.venv/bin/python - <<'PY'
import sys, fastapi, pydantic, uvicorn, requests, httpx, dateutil, cryptography, open3d
try:
    from sqlcipher3 import dbapi2 as sc; enc = f"SQLCipher {sc.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0]}"
except Exception as e: enc = f"SQLCipher unavailable ({e}); default PODA startup will REFUSE plaintext storage"
print(f"PODA runtime verified on Python {sys.version.split()[0]} | FastAPI {fastapi.__version__} | Pydantic {pydantic.__version__} | Open3D {open3d.__version__} | cryptography {cryptography.__version__} | {enc}")
PY
PODA_DATA_DIR="${PODA_DATA_DIR:-$HOME/Library/Application Support/PODA-Public}"
mkdir -p "$PODA_DATA_DIR" && chmod 700 "$PODA_DATA_DIR"
if ! .venv/bin/python -c 'from sqlcipher3 import dbapi2' >/dev/null 2>&1; then
  echo 'ERROR: SQLCipher is REQUIRED for public-release personal storage. Resolve setup before starting PODA.'
  exit 1
fi
echo "PODA Python setup complete. Next: ./install_ollama_models.command, then ./start_poda.command"
