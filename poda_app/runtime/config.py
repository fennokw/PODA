"""Build identity, paths, and environment configuration for PODA.

Everything here is derived from the filesystem or environment at import time so
that the running process can always prove exactly which build it is serving.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

APP_NAME = "PODA"
VERSION = "0.9.3-public"

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PACKAGE_DIR = BASE_DIR / "poda_app"
STATIC_DIR = PACKAGE_DIR / "static"
# Store all personal state outside the checkout in an isolated public-release directory.
# Test instances can set PODA_DATA_DIR to an isolated temporary directory.
WINDOWS = sys.platform == "win32"
if WINDOWS:
    _profile_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
elif sys.platform == "darwin":
    _profile_data = Path.home() / "Library" / "Application Support"
else:
    _profile_data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
_default_data = _profile_data / ("PODA-Windows" if WINDOWS else "PODA-Public")
DATA_DIR = Path(os.getenv("PODA_DATA_DIR", str(_default_data))).expanduser().resolve()
BACKUP_DIR = Path(os.getenv("PODA_BACKUP_DIR", str(DATA_DIR / "backups"))).expanduser().resolve()
DB_PATH = DATA_DIR / "poda.db"
LOG_DIR = DATA_DIR / "logs"

HOST = "127.0.0.1"
PORT = int(os.getenv("PODA_PORT", "8787"))
CREDENTIAL_NAMESPACE = os.getenv("PODA_CREDENTIAL_NAMESPACE", "PODA-Windows" if WINDOWS else "PODA")

FAST_MODEL = "qwen3-4b-q4_k_m"
ADVANCED_MODEL = "qwen3-8b-q4_k_m"
OPTIONAL_DEEP_MODEL = "qwen3-14b-q4_k_m"
EMBED_MODEL = "nomic-embed-text-v1.5-f16"
EMBED_SPACE_VERSION = f"{EMBED_MODEL}:v1"

# Encryption at rest: public release defaults to REQUIRED; startup fails if SQLCipher/the operating system credential store cannot work.
DB_ENCRYPTION = os.getenv("PODA_DB_ENCRYPTION", "required").lower()  # required | auto | off (off is test/recovery-only)
DB_KEYCHAIN_SERVICE = "PODA.Public.Database"
DB_KEYCHAIN_ACCOUNT = "primary"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
NOTION_KEYCHAIN_SERVICE = "PODA.Public.Notion"
NOTION_KEYCHAIN_ACCOUNT = "primary"

EGRESS_ALLOWLIST = {
    "addon_catalog": {"raw.githubusercontent.com"},
    "notion": {"api.notion.com"},
}

_SOURCE_GLOBS = ("poda_app/**/*.py", "poda_app/static/**/*", "*.cmd", "*.ps1", "requirements.txt")


def compute_build_id() -> str:
    """Stable hash of every shipped source/static file so stale servers are detectable."""
    digest = hashlib.sha256()
    files: list[Path] = []
    for pattern in _SOURCE_GLOBS:
        files.extend(p for p in BASE_DIR.glob(pattern) if p.is_file() and "__pycache__" not in p.parts)
    for path in sorted(set(files)):
        try:
            digest.update(path.relative_to(BASE_DIR).as_posix().encode())
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


BUILD_ID = compute_build_id()


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(BACKUP_DIR, 0o700)
    except OSError:
        pass
