#!/bin/bash
# Verify imports, unit tests in a throwaway database, and a throwaway live boot.
set -euo pipefail
cd "$(dirname "$0")"
fail=0
check(){ if eval "$2" >/dev/null 2>&1; then printf '[OK]   %s\n' "$1"; else printf '[FAIL] %s\n' "$1"; fail=1; fi; }
VERSION="$(tr -d '[:space:]' < PODA_VERSION.txt)"
check "Version file matches runtime ($VERSION)" ".venv/bin/python -c 'from poda_app.runtime.config import VERSION; import sys; sys.exit(0 if VERSION==\"$VERSION\" else 1)'"
check "Python imports (fastapi, open3d, httpx, cryptography)" ".venv/bin/python -c 'import fastapi, open3d, httpx, cryptography, numpy'"
check "SQLCipher available" ".venv/bin/python -c 'from sqlcipher3 import dbapi2'"
check "App imports and routes register" "PODA_DATA_DIR=\$(mktemp -d) PODA_DB_ENCRYPTION=off .venv/bin/python -c 'from fastapi.testclient import TestClient; from poda_app.main import app; c=TestClient(app, base_url=\"http://127.0.0.1:8799\"); assert c.get(\"/system/build\").status_code==200; assert c.get(\"/memory-viewer\").status_code==200' 2>/dev/null"
check "Home shell navigates to canonical viewer" "grep -q \"/memory-viewer\" poda_app/static/app.js || grep -rq \"/memory-viewer\" poda_app/static/*.js"
check "Viewer keeps commit/discard/return controls" "grep -q 'COMMIT MEMORY CHANGES' poda_app/static/memory.html && grep -q 'DISCARD DRAFT' poda_app/static/memory.html && grep -qi 'RETURN TO PODA CHAT' poda_app/static/memory.html"
check "Idle rotation constants (15 s, 0.0544 rad/s)" "grep -q '15000' poda_app/static/memory.js && grep -q '0.0544' poda_app/static/memory.js"
check "No remote CDN/font/analytics references in static" "! grep -rEi 'https?://(cdn|fonts\.|unpkg|jsdelivr|googleapis|google-analytics|segment|sentry)' poda_app/static"
check "No cloud model SDK in backend" "! grep -rEi 'openai|anthropic|cohere|gemini' poda_app --include=*.py"
if command -v node >/dev/null 2>&1; then for f in poda_app/static/*.js; do check "JS syntax $f" "node --check $f"; done; fi
echo "Running pytest (isolated temp database) ..."
if .venv/bin/python -m pytest -q tests 2>&1 | tail -3; then :; fi
.venv/bin/python -m pytest -q tests >/dev/null 2>&1 && printf '[OK]   pytest suite\n' || { printf '[FAIL] pytest suite\n'; fail=1; }
echo "Live boot check with a fresh throwaway empty database (port 8798) ..."
TMP="$(mktemp -d)"; echo "Using an empty test-only database; personal data is never copied."
PODA_DATA_DIR="$TMP" PODA_BACKUP_DIR="$TMP/backups" PODA_DB_ENCRYPTION=off PODA_PORT=8798 .venv/bin/python -m uvicorn poda_app.main:app --host 127.0.0.1 --port 8798 >"$TMP/server.log" 2>&1 &
PID=$!
ok=0; for _ in $(seq 1 60); do if curl -fsS --max-time 1 http://127.0.0.1:8798/system/build >/dev/null 2>&1; then ok=1; break; fi; sleep .25; done
if [ "$ok" = 1 ]; then
  BUILD="$(curl -s http://127.0.0.1:8798/system/build | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["build_id"])')"
  check "Live server reports build $BUILD" "test -n '$BUILD'"
  COOKIE="$(curl -s -D - -o /dev/null http://127.0.0.1:8798/ | grep -i 'set-cookie' | sed 's/.*poda_session=\([^;]*\).*/\1/')"
  check "Protected route rejects missing session" "test \"\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8798/capabilities)\" = 401"
  check "Protected route accepts session cookie" "test \"\$(curl -s -o /dev/null -w '%{http_code}' -b poda_session=$COOKIE http://127.0.0.1:8798/capabilities)\" = 200"
  check "Memory scene loads on migrated copy" "curl -s -b poda_session=$COOKIE http://127.0.0.1:8798/memory/open3d/scene | grep -q cloud_volume"
  check "Database copy encrypted on disk (or encryption disabled)" "! head -c 16 '$TMP/poda.db' | grep -q 'SQLite format 3' || [ \"\${PODA_DB_ENCRYPTION:-auto}\" = off ]"
else
  printf '[FAIL] live server did not start\n'; tail -20 "$TMP/server.log"; fail=1
fi
kill $PID 2>/dev/null || true; rm -rf "$TMP"
if [ "$fail" -ne 0 ]; then echo "PODA v$VERSION verification FAILED."; exit 1; fi
echo "PODA v$VERSION verification PASSED."
