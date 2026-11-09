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
    assert (BACKEND / "poda_app" / "main.py").is_file()
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


def test_relaunch_waits_for_profile_and_keeps_grant(tmp_path):
    from contextlib import ExitStack

    env = dict(os.environ, PODA_DATA_DIR=str(tmp_path / "profile"), PODA_DB_ENCRYPTION="off",
               PODA_DESKTOP_TOKEN="synthetic-local-token")
    folder = tmp_path / "example-grant"
    folder.mkdir()

    with ExitStack() as cleanup:
        def start(label):
            logfile = tmp_path / f"{label}.log"
            with logfile.open("w", encoding="utf-8") as log:
                child = subprocess.Popen([sys.executable, str(BACKEND / "desktop_backend.py")], env=env,
                                         stdin=subprocess.PIPE, stdout=log, stderr=log)

            def stop():
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            cleanup.callback(stop)
            return child, logfile

        def connect(child, logfile):
            deadline = time.monotonic() + 30
            while child.poll() is None and time.monotonic() < deadline:
                handshake = next((line for line in logfile.read_text(encoding="utf-8", errors="replace").splitlines()
                                  if line.startswith("PODA_DESKTOP ")), None)
                if handshake:
                    port = json.loads(handshake.removeprefix("PODA_DESKTOP "))["port"]
                    http = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1,
                                        headers={"X-PODA-Desktop": env["PODA_DESKTOP_TOKEN"]})
                    cleanup.callback(http.close)
                    try:
                        if http.get("/").status_code == 200:
                            return http
                    except httpx.RequestError:
                        pass
                    http.close()
                time.sleep(0.05)
            pytest.fail(logfile.read_text(encoding="utf-8", errors="replace"))

        first, first_log = start("first")
        http = connect(first, first_log)
        assert http.post("/agent/grants", json={"root_path": str(folder), "mode": "read"}).status_code == 200
        second, second_log = start("second")
        time.sleep(2)
        assert second.poll() is None
        assert "PODA_DESKTOP " not in second_log.read_text(encoding="utf-8", errors="replace")
        first.stdin.close()
        assert first.wait(timeout=15) == 0
        http = connect(second, second_log)
        assert any(item["root_path"] == str(folder.resolve()) for item in http.get("/agent/grants").json()["grants"])
        second.stdin.close()
        assert second.wait(timeout=15) == 0
