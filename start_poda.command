#!/bin/bash
# PODA launcher: verifies the Python runtime, refuses to kill non-PODA processes, and proves the
# running server reports THIS build before opening the browser.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PODA_PORT:-8787}"
EXPECTED_VERSION="$(cat PODA_VERSION.txt | tr -d '[:space:]')"

if [ ! -d .venv ] || ! .venv/bin/python -c 'import fastapi,pydantic,uvicorn,open3d,httpx,cryptography' >/dev/null 2>&1; then
  echo "PODA Python environment is missing or incomplete. Running setup..."
  ./setup_poda.command
fi

EXPECTED_BUILD="$(.venv/bin/python -c 'from poda_app.runtime.config import BUILD_ID; print(BUILD_ID)')"

existing_pid="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
if [ -n "$existing_pid" ]; then
  running_build="$(curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/system/build" 2>/dev/null | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("build_id",""))' 2>/dev/null || true)"
  cmd="$(ps -p "$existing_pid" -o command= 2>/dev/null || true)"
  if [ "$running_build" = "$EXPECTED_BUILD" ]; then
    echo "PODA build ${EXPECTED_BUILD} is already running (PID $existing_pid)."
    open "http://127.0.0.1:${PORT}" || true
    exit 0
  fi
  if echo "$cmd" | grep -qE 'uvicorn.*poda_app\.main|python.*poda_app'; then
    echo "Another PODA build is already running on port ${PORT} (PID ${existing_pid})."
    echo 'PODA Public will not stop a separate/private PODA installation automatically.'
    echo "Stop your other instance explicitly or use: PODA_PORT=8788 ./start_poda.command"
    exit 1
  else
    echo "ERROR: port ${PORT} is occupied by a process that is NOT PODA:"
    echo "  PID $existing_pid: $cmd"
    echo "PODA will not kill unrelated services. Stop it yourself or run: PODA_PORT=8788 ./start_poda.command"
    exit 1
  fi
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "WARNING: Ollama is not installed. The UI will start, but chat and semantic memory need Ollama (https://ollama.com/download/mac)."
else
  if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Starting Ollama service..."
    (ollama serve >/tmp/poda-ollama.log 2>&1 &) || true
    sleep 3
  fi
  for m in nomic-embed-text llama3.2:3b qwen3:14b; do
    if ! curl -s http://127.0.0.1:11434/api/tags | grep -q "\"$m"; then
      echo "Model $m is not installed. Run ./install_ollama_models.command"
    fi
  done
fi

PODA_DATA_DIR="${PODA_DATA_DIR:-$HOME/Library/Application Support/PODA-Public}"
export PODA_DATA_DIR
mkdir -p "$PODA_DATA_DIR/logs" && chmod 700 "$PODA_DATA_DIR" "$PODA_DATA_DIR/logs"
.venv/bin/python -m uvicorn poda_app.main:app --host 127.0.0.1 --port "${PORT}" --no-server-header >>"$PODA_DATA_DIR/logs/server.log" 2>&1 &
SERVER_PID=$!
cleanup(){ kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup INT TERM EXIT

for _ in $(seq 1 60); do
  build="$(curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/system/build" 2>/dev/null | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("build_id",""))' 2>/dev/null || true)"
  if [ "$build" = "$EXPECTED_BUILD" ]; then
    echo "PODA v${EXPECTED_VERSION} build ${EXPECTED_BUILD} verified on http://127.0.0.1:${PORT}"
    echo "Canonical memory viewer: http://127.0.0.1:${PORT}/memory-viewer"
    echo "Logs: ${PODA_DATA_DIR}/logs/server.log"
    open "http://127.0.0.1:${PORT}" || true
    wait "$SERVER_PID"
    exit $?
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "PODA server exited during startup. Last log lines:"; tail -40 "$PODA_DATA_DIR/logs/server.log"; exit 1
  fi
  sleep .25
done
echo "ERROR: PODA started but did not identify itself as build ${EXPECTED_BUILD}."; tail -40 "$PODA_DATA_DIR/logs/server.log"; exit 1
