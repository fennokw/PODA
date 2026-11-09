"""Bounded code execution inside execute-enabled grants. Real exit codes, captured output, scrubbed environment."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import grants, sandbox

MAX_TIMEOUT = 120
MAX_OUTPUT = 64 * 1024


def _ok(result: Any, verification: str) -> dict[str, Any]:
    return {"ok": True, "result": result, "error": None, "verification": verification}


def _fail(error: str, verification: str = "nothing executed") -> dict[str, Any]:
    return {"ok": False, "result": None, "error": error, "verification": verification}


def _python_for(cwd: Path) -> str:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    for candidate in (cwd / ".venv" / relative, cwd / "venv" / relative):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _scrubbed_env(cwd: Path) -> dict[str, str]:
    if os.name == "nt":
        system = os.environ.get("SystemRoot", r"C:\Windows")
        temp = str(cwd / ".poda-exec-tmp")
        return {"SYSTEMROOT": system, "WINDIR": system, "PATH": str(Path(system) / "System32"),
                "TEMP": temp, "TMP": temp, "HOME": str(cwd), "USERPROFILE": str(cwd),
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"}
    return {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd), "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1", "TMPDIR": str(cwd / ".poda-exec-tmp")}


def _run(cmd: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    cleanup = None
    payload = None
    try:
        if sandbox.execution_mode() == "windows_docker_sandbox":
            from .windows_sandbox import prepare
            cmd, cleanup, payload = prepare(cmd, cwd)
            env = _scrubbed_env(cwd)
            # The Docker client needs valid Windows process directories. Its
            # environment is not forwarded to the container.
            env.pop("HOME", None)
            if "USERPROFILE" in os.environ:
                env["USERPROFILE"] = os.environ["USERPROFILE"]
            env["TEMP"] = env["TMP"] = tempfile.gettempdir()
        else:
            scratch = cwd / ".poda-exec-tmp"
            scratch.mkdir(exist_ok=True, mode=0o700)
            cmd = sandbox.wrap(cmd, str(cwd), str(scratch))
            env = _scrubbed_env(cwd)
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    started = time.perf_counter()
    try:
        io = {"input": payload} if payload is not None else {"stdin": subprocess.DEVNULL}
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, timeout=timeout, **io)
    except OSError as exc:
        return _fail(f"Could not start the approved command: {exc}")
    except subprocess.TimeoutExpired as exc:
        stopped = True
        if cleanup:
            try:
                removed = subprocess.run(cleanup, env=env, capture_output=True, timeout=8)
                stopped = removed.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                stopped = False
        out = (exc.stdout or b"")[:MAX_OUTPUT].decode("utf-8", "replace")
        err = (exc.stderr or b"")[:MAX_OUTPUT].decode("utf-8", "replace")
        return {"ok": False, "result": {"command": cmd, "cwd": str(cwd), "exit_code": None, "timed_out": True, "stdout": out, "stderr": err, "wall_s": round(time.perf_counter() - started, 2)},
                "error": f"Timed out after {timeout}s", "verification": f"process killed after {timeout}s" if stopped else "Docker cleanup could not be verified"}
    stdout = proc.stdout[:MAX_OUTPUT].decode("utf-8", "replace")
    stderr = proc.stderr[:MAX_OUTPUT].decode("utf-8", "replace")
    result = {"command": cmd, "cwd": str(cwd), "exit_code": proc.returncode, "timed_out": False, "stdout": stdout, "stderr": stderr,
              "stdout_truncated": len(proc.stdout) > MAX_OUTPUT, "stderr_truncated": len(proc.stderr) > MAX_OUTPUT, "wall_s": round(time.perf_counter() - started, 2)}
    verification = f"real process exit code {proc.returncode} in {result['wall_s']}s; stdout {len(proc.stdout)}B stderr {len(proc.stderr)}B"
    return {"ok": proc.returncode == 0, "result": result, "error": None if proc.returncode == 0 else f"exit code {proc.returncode}", "verification": verification}


def run_python(cwd: str, path: str | None = None, code: str | None = None, args: list[str] | None = None, timeout: int = 60) -> dict[str, Any]:
    try:
        workdir, grant = grants.resolve(cwd, "execute")
    except PermissionError as exc:
        return _fail(str(exc))
    if not workdir.is_dir():
        return _fail(f"cwd is not a directory: {workdir}")
    timeout = max(1, min(int(timeout or 60), MAX_TIMEOUT))
    args = [str(a) for a in (args or [])][:32]
    python = _python_for(workdir)
    if path:
        try:
            script, _ = grants.resolve(path if os.path.isabs(os.path.expanduser(path)) else str(workdir / path), "read")
        except PermissionError as exc:
            return _fail(str(exc))
        if not script.is_file():
            return _fail(f"Script not found: {script}")
        return _run([python, str(script), *args], workdir, timeout)
    if code is not None:
        if len(code) > 200_000:
            return _fail("code exceeds 200 KB")
        return _run([python, "-c", code, *args], workdir, timeout)
    return _fail("Provide either path or code")


def run_tests(cwd: str, args: list[str] | None = None, timeout: int = 120) -> dict[str, Any]:
    try:
        workdir, grant = grants.resolve(cwd, "execute")
    except PermissionError as exc:
        return _fail(str(exc))
    if not workdir.is_dir():
        return _fail(f"cwd is not a directory: {workdir}")
    timeout = max(1, min(int(timeout or 120), MAX_TIMEOUT))
    safe_args = [a for a in (args or ["-q"]) if isinstance(a, str) and ".." not in a and not a.startswith("--rootdir") and not os.path.isabs(a)][:16]
    return _run([_python_for(workdir), "-m", "pytest", *safe_args], workdir, timeout)
