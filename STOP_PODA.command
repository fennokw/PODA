#!/bin/bash
# Stops the PODA server only if the process on the port is PODA.
set -euo pipefail
PORT="${PODA_PORT:-8787}"
pid="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
if [ -z "$pid" ]; then echo "No process is listening on port ${PORT}."; exit 0; fi
cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
if echo "$cmd" | grep -qE 'uvicorn.*poda_app\.main|python.*poda_app'; then
  kill "$pid" && echo "Stopped PODA (PID $pid)."
else
  echo "Port ${PORT} is used by a non-PODA process (PID $pid): $cmd"; echo "Not touching it."; exit 1
fi
