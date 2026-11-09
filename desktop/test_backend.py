"""Test the desktop handshake using a real isolated backend process."""
import json
import os
import subprocess
import sys
import time

import httpx
import pytest

from pathlib import Path

BACKEND = Path(os.environ.get("PODA_TEST_BACKEND", Path(__file__).parent / ".backend")).resolve()


def test_desktop_requires_token_and_local_session(tmp_path):
    assert not (BACKEND / "poda_app" / "server.py").exists()
    assert (BACKEND / "backend-lock.json").is_file()
    env = dict(os.environ, PODA_DATA_DIR=str(tmp_path), PODA_BACKUP_DIR=str(tmp_path / "backups"),
               PODA_DB_ENCRYPTION="off", PODA_DESKTOP_TOKEN="synthetic-local-token")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    log_path = tmp_path / "test-server.log"
    with log_path.open("w+") as log:
        child = subprocess.Popen([sys.executable, str(BACKEND / "desktop_backend.py")], env=env, stdin=subprocess.PIPE, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            line = ""
            while time.monotonic() < deadline and child.poll() is None:
                line = next((s for s in log_path.read_text().splitlines() if s.startswith("PODA_DESKTOP ")), "")
                if line:
                    break
                time.sleep(.05)
            assert line, log_path.read_text()
            port = json.loads(line.removeprefix("PODA_DESKTOP "))["port"]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as http:
                while time.monotonic() < deadline:
                    try:
                        assert http.get("/").status_code == 401
                        break
                    except httpx.RequestError:
                        time.sleep(.05)
                else:
                    pytest.fail(log_path.read_text())
                http.headers["X-PODA-Desktop"] = env["PODA_DESKTOP_TOKEN"]
                assert http.get("/system/build").json()["port"] == port
                assert http.get("/memory").status_code == 401
                assert http.get("/").status_code == 200
                assert http.get("/memory").status_code == 200
                assert http.get("/memory", headers={"Origin": "https://example.test"}).status_code == 403
            child.stdin.close()
            assert child.wait(timeout=12) == 0, log_path.read_text()
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
