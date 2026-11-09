"""Shared fixtures: every test runs against an isolated plaintext database in a temp dir, never the user's data."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="poda-tests-"))
os.environ["PODA_DATA_DIR"] = str(_TMP / "data")
os.environ["PODA_BACKUP_DIR"] = str(_TMP / "backups")
os.environ["PODA_DB_ENCRYPTION"] = "off"
os.environ["PODA_PORT"] = "8799"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from poda_app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app, base_url="http://127.0.0.1:8799") as c:
        c.get("/")  # obtain the local session cookie
        yield c


@pytest.fixture()
def tmp_folder(tmp_path):
    folder = tmp_path / "granted"
    folder.mkdir()
    return folder


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
