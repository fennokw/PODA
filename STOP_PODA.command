#!/bin/bash
# Stops PODA only when the listener belongs to THIS source checkout.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PODA_PORT:-8787}"
pid="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
if [ -z "$pid" ]; then echo "No process is listening on port ${PORT}."; exit 0; fi
cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true)"
if echo "$cmd" | grep -qE 'uvicorn.*poda_app\.main|python.*poda_app' && [ "$cwd" = "$PWD" ]; then
  kill "$pid" && echo "Stopped PODA from this checkout (PID $pid)."
else
  echo "Port ${PORT} is occupied by another process or another PODA checkout (PID $pid, cwd=${cwd:-unknown}): $cmd"
  echo "Not touching it."; exit 1
fi
