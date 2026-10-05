#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "PODA .venv does not exist yet. Running full setup instead."
  ./setup_poda.command
  exit $?
fi

source .venv/bin/activate

echo "Testing Open3D..."
if python -c 'import open3d; print("Open3D", open3d.__version__)' ; then
  echo "Open3D is already working."
  exit 0
fi

echo
echo "Repairing native Open3D dependency: libusb"
if ! command -v brew >/dev/null 2>&1; then
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required to install libusb."
  read -r -p "Install Homebrew now? [Y/n]: " answer
  case "${answer:-Y}" in
    n|N|no|NO)
      open "https://brew.sh" || true
      exit 1
      ;;
    *)
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"; fi
      ;;
  esac
fi

brew install libusb || brew reinstall libusb
    brew link --overwrite libusb >/dev/null 2>&1 || true
python -c 'import open3d; print("Open3D repaired:", open3d.__version__)'
echo "Open3D repair complete."
