"""Only one scheduler and API process may serve a PODA data directory."""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class DataDirectoryInUse(RuntimeError):
    pass


def _process(pid: int) -> dict | None:
    if type(pid) is not int or pid <= 1:
        return None
    try:
        result = subprocess.run(
            ["/bin/ps", "-ww", "-o", "uid=", "-o", "ppid=", "-o", "lstart=", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=1, env={"LC_ALL": "C"},
        )
        parts = result.stdout.strip().split(None, 7)
        if result.returncode or len(parts) != 8 or int(parts[0]) != os.getuid():
            return None
        return {"pid": pid, "parent": int(parts[1]), "started": " ".join(parts[2:7]), "command": parts[7]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _matches(record: dict, live: dict | None) -> bool:
    return bool(live and live["pid"] == record.get("pid") and live["started"] == record.get("started"))


def _record_owner(lock: int, desktop_pid: int) -> None:
    backend = _process(os.getpid())
    owner = _process(desktop_pid)
    if backend and owner and os.getpgrp() == os.getpid():
        owner_identity = {field: owner[field] for field in ("pid", "started")}
        backend_identity = {field: backend[field] for field in ("pid", "started")}
        record = json.dumps({"backend": backend_identity, "owner": owner_identity}).encode()
        os.pwrite(lock, record, 0)
        os.ftruncate(lock, len(record))


def _same_backend(process: dict, entrypoint: Path, *, serve: bool) -> bool:
    # Match the exact installed script path, including spaces in a macOS app bundle.
    _, sep, args = process["command"].partition(" -u ")
    expected = str(entrypoint)
    return bool(sep and args == (expected + " --serve" if serve else expected))


def _legacy_orphan(lock_path: Path, entrypoint: Path) -> dict | None:
    """Older bundles stored no owner. Only recover a verified orphan of this bundle."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(["/usr/sbin/lsof", "-t", str(lock_path)],
                                capture_output=True, text=True, timeout=2)
        pids = [int(value) for value in result.stdout.splitlines() if value.isascii() and value.isdecimal()]
    except (OSError, subprocess.TimeoutExpired):
        return None
    for pid in pids:
        backend = _process(pid)
        if not backend or not _same_backend(backend, entrypoint, serve=True):
            continue
        if backend["parent"] == 1:
            return backend
        supervisor = _process(backend["parent"])
        if supervisor and supervisor["parent"] == 1 and _same_backend(supervisor, entrypoint, serve=False):
            # Leave the orphan supervisor intact so it can reap the old child.
            return backend
    return None


def _recover_orphan(lock: int, lock_path: Path, entrypoint: Path) -> None:
    try:
        record = json.loads(os.pread(lock, 16 * 1024, 0))
        owner, backend = record["owner"], record["backend"]
        if not isinstance(owner, dict) or not isinstance(backend, dict):
            raise ValueError("Invalid owner record")
    except (ValueError, TypeError, KeyError, OSError):
        backend = _legacy_orphan(lock_path, entrypoint)
        if backend and _matches(backend, _process(backend["pid"])):
            try:
                os.kill(backend["pid"], signal.SIGKILL)
                print("PODA recovered an abandoned desktop backend.", file=sys.stderr, flush=True)
            except ProcessLookupError:
                pass
        return
    # Missing process information is not proof that a live owner has disappeared.
    owner_pid = owner.get("pid")
    if type(owner_pid) is not int or owner_pid <= 1 or not isinstance(owner.get("started"), str):
        return
    live_owner = _process(owner_pid)
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        pass
    except PermissionError:
        return
    else:
        if not live_owner or _matches(owner, live_owner):
            return

    pid = backend.get("pid")
    live_backend = _process(pid)
    if not _matches(backend, live_backend) or not _same_backend(live_backend, entrypoint, serve=True):
        return
    try:
        if os.getpgid(pid) == pid and os.getsid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
            print("PODA recovered an abandoned desktop backend.", file=sys.stderr, flush=True)
    except ProcessLookupError:
        pass


@contextmanager
def claim_data_directory(*, wait_seconds: float = 0, desktop_owner: int | None = None,
                         desktop_entrypoint: Path | None = None):
    config.ensure_dirs()
    lock_path = config.DATA_DIR / ".server.lock"
    lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    beginning = time.monotonic()
    deadline = beginning + wait_seconds
    # Give a previous desktop supervisor time for orderly shutdown first.
    recovery_at = beginning + 11
    try:
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if not isinstance(exc, BlockingIOError) and exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                now = time.monotonic()
                if os.name != "nt" and desktop_entrypoint and recovery_at <= now < deadline:
                    recovery_at = now + 2
                    _recover_orphan(lock, lock_path, desktop_entrypoint)
                    continue
                remaining = deadline - now
                if remaining <= 0:
                    raise DataDirectoryInUse(
                        "This data folder is already in use by another PODA server. Quit the other PODA app "
                        "or stop its local server, then open PODA again."
                    ) from exc
                time.sleep(min(0.2, remaining))
        if os.name != "nt":
            os.ftruncate(lock, 0)
            if desktop_owner:
                _record_owner(lock, desktop_owner)
        yield
    finally:
        os.close(lock)
