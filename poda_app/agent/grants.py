"""Filesystem grant registry. The Capability Ledger derives every file permission from these rows."""
from __future__ import annotations

import os
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from ..runtime import config
from ..runtime.db import connect, now_iso, rows, one
from . import sandbox

MODES = {"read", "edit", "temp"}
FORBIDDEN_ROOTS = {"/", "/System", "/Library", "/usr", "/bin", "/sbin", "/private", "/etc", "/var"}


def canonical(path: str) -> Path:
    value = path.strip()
    if len(value) >= 2 and (value[0], value[-1]) in {('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")}:
        value = value[1:-1]
    if value.startswith("file://"):
        parsed = urlsplit(value)
        if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
            raise ValueError("Choose a local file or folder.")
        value = url2pathname(parsed.path)
    if not value:
        raise ValueError("Enter a file or folder path.")
    return Path(value).expanduser().resolve(strict=False)


def _active_filter() -> str:
    return "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"


def list_grants(include_inactive: bool = False) -> list[dict[str, Any]]:
    conn = connect()
    try:
        if include_inactive:
            return rows(conn, "SELECT * FROM fs_grants ORDER BY created_at DESC")
        return rows(conn, f"SELECT * FROM fs_grants WHERE {_active_filter()} ORDER BY created_at DESC", (now_iso(),))
    finally:
        conn.close()


def add_grant(root_path: str, mode: str, label: str | None = None, allow_execute: bool = False, ttl_minutes: int | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    permission_hint = (" Allow PODA to access the folder in System Settings → Privacy & Security → Files and Folders."
                       if sys.platform == "darwin" else " Check the file or folder permissions.")
    try:
        root = canonical(root_path)
        kind = root.stat().st_mode
    except PermissionError as exc:
        raise ValueError("PODA could not access this path." + permission_hint) from exc
    except FileNotFoundError as exc:
        raise ValueError(f"Path not found on the computer running PODA: {root_path.strip()}. Choose the file or folder again or copy its full path.") from exc
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Could not open the grant path: {exc}") from exc
    is_file = stat.S_ISREG(kind)
    if not (is_file or stat.S_ISDIR(kind)):
        raise ValueError("Grant an existing regular file or folder.")
    if is_file and allow_execute:
        raise ValueError("Execution requires a folder grant. Turn off execution to grant just this file.")
    if str(root) in FORBIDDEN_ROOTS or root == Path.home().resolve() or root == Path(root.anchor):
        raise ValueError("Choose a specific user file or folder outside system roots and the whole home directory.")
    protected = [config.DATA_DIR.resolve()]
    if config.WINDOWS:
        protected.append(canonical(str(Path.home() / "AppData")))
        for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if os.environ.get(variable):
                protected.append(canonical(os.environ[variable]))
    if any(root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
        raise ValueError("Choose a specific user file or folder outside system and PODA's private data.")
    try:
        # stat can succeed on a protected macOS Desktop directory before contents are accessible.
        if is_file:
            with root.open("rb"):
                pass
        else:
            with os.scandir(root):
                pass
    except PermissionError as exc:
        raise ValueError(f"PODA cannot read {root}." + permission_hint) from exc
    except OSError as exc:
        raise ValueError(f"Could not open the grant path: {exc}") from exc
    expires = None
    if mode == "temp":
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes or 60)).isoformat()
    grant = {"id": str(uuid.uuid4()), "root_path": str(root), "mode": mode, "label": label or root.name, "created_at": now_iso(),
             "expires_at": expires, "revoked_at": None, "allow_execute": 1 if allow_execute else 0, "target_kind": "file" if is_file else "directory"}
    conn = connect()
    try:
        conn.execute("INSERT INTO fs_grants (id, root_path, mode, label, created_at, expires_at, revoked_at, allow_execute, target_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                     (grant["id"], grant["root_path"], grant["mode"], grant["label"], grant["created_at"], grant["expires_at"], None, grant["allow_execute"], grant["target_kind"]))
        conn.commit()
    finally:
        conn.close()
    return grant


def revoke_grant(grant_id: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute("UPDATE fs_grants SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now_iso(), grant_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def resolve(path: str, need: str = "read") -> tuple[Path, dict[str, Any]]:
    """Canonicalize `path` (following symlinks) and return the grant that authorizes `need` ('read'|'write'|'execute').

    Raises PermissionError when no active grant covers the canonical path.
    """
    try:
        target = canonical(path)
    except (ValueError, OSError, RuntimeError) as exc:
        raise PermissionError(f"Could not resolve the requested path: {exc}") from exc
    # Also guard against symlinked parents escaping a root: compare the fully resolved path.
    for grant in list_grants():
        root = Path(grant["root_path"])
        if grant.get("target_kind", "directory") == "file":
            if target != root or target.is_dir() or need == "execute":
                continue
        elif not target.is_relative_to(root):
            continue
        if need == "read":
            return target, grant
        if need == "write" and grant["mode"] in {"edit", "temp"}:
            return target, grant
        if need == "execute" and grant["allow_execute"] and grant["mode"] in {"edit", "temp"}:
            return target, grant
    raise PermissionError(f"No active grant permits '{need}' at {target}")


def filesystem_capability() -> dict[str, Any]:
    grants = list_grants()
    readable = [g for g in grants]
    writable = [g for g in grants if g["mode"] in {"edit", "temp"}]
    executable = [g for g in grants if g["allow_execute"] and g["mode"] in {"edit", "temp"}]
    run_allowed = sandbox.execution_mode() != "blocked_no_os_sandbox"
    desktop = Path.home() / "Desktop"

    def covers_desktop(grant):
        root = Path(grant["root_path"])
        return desktop.is_relative_to(root) or root.is_relative_to(desktop)
    return {
        "general_user_file_read": bool(readable),
        "general_user_file_create": any(g.get("target_kind") != "file" for g in writable),
        "general_user_file_edit": bool(writable),
        "general_user_file_delete": False,
        "code_execution": bool(executable) and run_allowed,
        "desktop_access": any(covers_desktop(g) for g in readable),
        "desktop_write_access": any(covers_desktop(g) for g in writable),
        "execution_isolation": sandbox.execution_mode(), "granted_roots": [{"id": g["id"], "path": g["root_path"], "target_kind": g.get("target_kind", "directory"), "mode": g["mode"], "allow_execute": bool(g["allow_execute"]), "expires_at": g["expires_at"], "label": g["label"]} for g in grants],
        "detail": (f"{len(grants)} file or folder grant(s) active: " + "; ".join(f"{g['root_path']} ({g['mode']}{', exec' if g['allow_execute'] else ''})" for g in grants))
                  if grants else "No file or folder grants are active. Use Files & Agent to grant access to existing user files.",
    }
