"""Manage PODA's local Hindsight sidecar and its private derived index."""
from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, RLock
from urllib.parse import urlsplit

import httpx

from ..runtime import config, ollama
from ..runtime.db import get_setting, set_setting
from ..security import egress

_PACKAGE = "hindsight-api-slim[embedded-db]==1.2.0"
_ROOT = config.DATA_DIR / "hindsight"
_RUNTIME = _ROOT / "runtime"
_LOG = config.LOG_DIR / "hindsight.log"
_LOCK = RLock()
_CANCEL = Event()
_process: subprocess.Popen | None = None
_url: str | None = None
_token: str | None = None
_phase = "idle"
_last_error: str | None = None


def _bin(name: str) -> Path:
    return _RUNTIME / ("Scripts" if config.WINDOWS else "bin") / (name + (".exe" if config.WINDOWS else ""))


def installed() -> bool:
    return (_RUNTIME / ".poda-version").is_file() and _bin("hindsight-api").is_file()


def _models() -> tuple[str, str, str]:
    url = urlsplit(config.OLLAMA_BASE)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username or url.password or url.path or url.query or url.fragment):
        raise RuntimeError("Hindsight requires Ollama on this computer.")
    try:
        names = ollama.installed_models()
    except ollama.OllamaError as exc:
        raise RuntimeError("Start Ollama, then try Hindsight again.") from exc
    embed = next((name for name in names if name.split(":")[0] == config.EMBED_MODEL.split(":")[0]), None)
    chat = next((name for name in names if "embed" not in name.lower() and name != embed
                 and not re.search(r"(?:^|[:-])cloud(?:$|[:-])", name, re.I)), None)
    if not embed or not chat:
        raise RuntimeError(f"Open System and install a chat model and {config.EMBED_MODEL} in Ollama first.")
    return config.OLLAMA_BASE, chat, embed


def _environment() -> dict[str, str]:
    keys = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "TMPDIR", "LANG", "LOCALAPPDATA", "APPDATA", "SSL_CERT_FILE", "SSL_CERT_DIR")
    return {key: os.environ[key] for key in keys if key in os.environ}


def status() -> dict:
    alive = _process is not None and _process.poll() is None and _url is not None
    return {"installed": installed(), "local_server_reachable": alive, "phase": _phase,
            "setup_error": _last_error}


def _install_command(command: list[str], timeout: int, log) -> None:
    with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=_environment(),
                          stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if config.WINDOWS else 0) as process:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if _CANCEL.wait(0.25) or time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=3)
                raise RuntimeError("Hindsight setup was interrupted. Try installing again.")
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)


def install_and_start() -> None:
    global _phase, _last_error
    with _LOCK:
        _CANCEL.clear()
        try:
            model_config = _models()
            if not installed():
                if egress.offline_mode():
                    raise RuntimeError("Turn off Offline Mode in Privacy to download Hindsight.")
                _phase = "installing"
                egress._log("hindsight_setup", "PIP", "pypi.org", "/", None, 0, 0, "Install local Hindsight", 0, False)
                _ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
                _LOG.parent.mkdir(parents=True, exist_ok=True)
                with _LOG.open("w", encoding="utf-8") as log:
                    _install_command([sys.executable, "-m", "venv", str(_RUNTIME)], 120, log)
                    _install_command([str(_bin("python")), "-m", "pip", "--isolated", "install",
                                      "--disable-pip-version-check", "--only-binary=:all:", _PACKAGE], 600, log)
                (_RUNTIME / ".poda-version").write_text(_PACKAGE, encoding="utf-8")
            if _CANCEL.is_set():
                raise RuntimeError("Hindsight setup was interrupted. Try installing again.")
            start(model_config)
            _last_error = None
        except (subprocess.SubprocessError, OSError) as exc:
            _phase = "idle"
            _last_error = "Hindsight installation could not complete. Check the setup details, then retry."
            raise RuntimeError(_last_error) from exc
        except RuntimeError as exc:
            _phase = "idle"
            _last_error = str(exc)
            raise


def start(model_config: tuple[str, str, str] | None = None) -> None:
    global _process, _url, _token, _phase, _last_error
    with _LOCK:
        if model_config is None:
            _CANCEL.clear()
        if status()["local_server_reachable"]:
            return
        if not installed():
            raise RuntimeError("Install Hindsight from Personalize to start memory search.")
        base, chat, embed = model_config or _models()
        if egress.offline_mode() and not (_ROOT / ".initialized").exists():
            raise RuntimeError("Turn off Offline Mode for the first Hindsight startup.")
        password = get_setting("hindsight_database_password", "")
        if not password:
            password = secrets.token_hex(24)
            set_setting("hindsight_database_password", password)
        _phase = "starting"
        token = secrets.token_urlsafe(32)
        env = {**_environment(),
               "PG0_HOME": str(_ROOT / "database"),
               "PG0_INSTALLATION_DIR": str(_ROOT / "postgres"),
               "HINDSIGHT_API_DATABASE_URL": f"pg0://hindsight:{password}@poda?listen_addresses=127.0.0.1",
               "HINDSIGHT_API_LLM_PROVIDER": "ollama", "HINDSIGHT_API_LLM_BASE_URL": base + "/v1",
               "HINDSIGHT_API_LLM_MODEL": chat, "HINDSIGHT_API_LLM_API_KEY": "ollama",
               # Ollama serves this protocol locally. All inference stays on loopback.
               "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
               "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": base + "/v1",
               "HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL": embed,
               "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY": "ollama",
               "HINDSIGHT_API_RERANKER_PROVIDER": "rrf", "HINDSIGHT_API_MCP_ENABLED": "false",
               "HINDSIGHT_API_TENANT_EXTENSION": "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension",
               "HINDSIGHT_API_TENANT_API_KEY": token, "HINDSIGHT_API_LOG_LEVEL": "warning",
               # The startup banner uses Unicode even when stdout is redirected on Windows.
               "PYTHONIOENCODING": "utf-8",
               "HF_HUB_OFFLINE": "1", "OTEL_SDK_DISABLED": "true", "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        try:
            with _LOG.open("a", encoding="utf-8") as log:
                _process = subprocess.Popen([str(_bin("hindsight-api")), "--host", "127.0.0.1", "--port", str(port),
                                             "--log-level", "warning", "--no-access-log"],
                                            cwd=_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                            stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW if config.WINDOWS else 0)
            deadline = time.monotonic() + 120
            with httpx.Client(base_url=base_url, timeout=2, trust_env=False) as client:
                while not _CANCEL.is_set() and time.monotonic() < deadline and _process.poll() is None:
                    try:
                        if client.get("/health").is_success:
                            # Verify the sidecar and its auth before any memory is sent.
                            route = "/v1/default/banks"
                            if (client.get(route).status_code in (401, 403)
                                    and client.get(route, headers={"Authorization": f"Bearer {token}"}).is_success):
                                (_ROOT / ".initialized").touch()
                                _url, _token, _phase, _last_error = base_url, token, "ready", None
                                return
                    except httpx.HTTPError:
                        pass
                    _CANCEL.wait(0.5)
            raise RuntimeError("Hindsight did not start. Open setup details and retry.")
        except (OSError, RuntimeError) as exc:
            stop()
            _last_error = str(exc)
            raise RuntimeError(_last_error) from exc


def stop() -> None:
    global _process, _url, _token, _phase
    _CANCEL.set()
    with _LOCK:
        process, _process = _process, None
        _url, _token, _phase = None, None, "idle"
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process and config.WINDOWS:
            # Windows process termination cannot run the sidecar's shutdown hook.
            try:
                subprocess.run([str(_bin("python")), "-c", "from pg0 import Pg0; Pg0(name='poda').stop()"],
                               env={**_environment(), "PG0_HOME": str(_ROOT / "database"),
                                    "PG0_INSTALLATION_DIR": str(_ROOT / "postgres")},
                               capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass


def endpoint() -> tuple[str, dict[str, str]]:
    url, token = _url, _token
    if not url or not token or not status()["local_server_reachable"]:
        raise httpx.ConnectError("PODA's Hindsight service is unavailable.")
    return url, {"Authorization": f"Bearer {token}"}


def diagnostics() -> str:
    if not _LOG.is_file():
        return ""
    with _LOG.open("rb") as log:
        log.seek(max(0, _LOG.stat().st_size - 5000))
        tail = log.read(5000).decode("utf-8", "replace")
    for secret in (_token, get_setting("hindsight_database_password", "")):
        if secret:
            tail = tail.replace(secret, "[redacted]")
    return tail
