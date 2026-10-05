#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
printf 'PODA %s — clean public Mac installation\n' "$(cat PODA_VERSION.txt)"
chmod +x ./*.command
printf '\n1/3: Python, macOS dependencies, encryption verification\n'
./setup_poda.command
printf '\n2/3: Ollama models\n'
if command -v ollama >/dev/null 2>&1; then
  ./install_ollama_models.command
else
  echo 'Install Ollama from https://ollama.com/download/mac, launch it, then run ./install_ollama_models.command'
  open 'https://ollama.com/download/mac' || true
fi
printf '\n3/3: source and isolated smoke verification\n'
./VERIFY_PODA.command
printf '\nInstall complete. Run ./start_poda.command\n'
