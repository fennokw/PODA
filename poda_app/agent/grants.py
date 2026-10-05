"""Filesystem grant registry. The Capability Ledger derives every file permission from these rows."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..runtime.db import connect, now_iso, rows, one
from . import sandbox

MODES = {"read", "edit", "temp"}
FORBIDDEN_ROOTS = {"/", "/System", "/Library", "/usr", "/bin", "/sbin", "/private", "/etc", "/var"}


def canonical(path: str) -> Path:
    p = Path(os.path.expanduser(path)).resolve(strict=False)
    return p


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
    root = canonical(root_path)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Grant root must be an existing directory: {root}")
    if str(root) in FORBIDDEN_ROOTS or root == Path.home():
        raise ValueError("Refusing to grant the whole home directory or a system root. Choose a specific folder.")
    expires = None
    if mode == "temp":
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes or 60)).isoformat()
    grant = {"id": str(uuid.uuid4()), "root_path": str(root), "mode": mode, "label": label or root.name, "created_at": now_iso(),
             "expires_at": expires, "revoked_at": None, "allow_execute": 1 if allow_execute else 0}
    conn = connect()
    try:
        conn.execute("INSERT INTO fs_grants (id, root_path, mode, label, created_at, expires_at, revoked_at, allow_execute) VALUES (?,?,?,?,?,?,?,?)",
                     (grant["id"], grant["root_path"], grant["mode"], grant["label"], grant["created_at"], grant["expires_at"], None, grant["allow_execute"]))
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
    target = canonical(path)
    # Also guard against symlinked parents escaping a root: compare the fully resolved path.
    for grant in list_grants():
        root = Path(grant["root_path"])
        try:
            target.relative_to(root)
        except ValueError:
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
    desktop = str(Path.home() / "Desktop")
    return {
        "general_user_file_read": bool(readable),
        "general_user_file_create": bool(writable),
        "general_user_file_edit": bool(writable),
        "general_user_file_delete": False,
        "code_execution": bool(executable) and run_allowed,
        "desktop_access": any(desktop == g["root_path"] or desktop.startswith(g["root_path"] + "/") or g["root_path"].startswith(desktop) for g in readable),
        "desktop_write_access": any(desktop == g["root_path"] or desktop.startswith(g["root_path"] + "/") or g["root_path"].startswith(desktop) for g in writable),
        "execution_isolation": sandbox.execution_mode(), "granted_roots": [{"id": g["id"], "path": g["root_path"], "mode": g["mode"], "allow_execute": bool(g["allow_execute"]), "expires_at": g["expires_at"], "label": g["label"]} for g in grants],
        "detail": (f"{len(grants)} folder grant(s) active: " + "; ".join(f"{g['root_path']} ({g['mode']}{', exec' if g['allow_execute'] else ''})" for g in grants))
                  if grants else "No folder grants are active. PODA cannot read, create, or edit user files until a folder is explicitly granted in Files & Agent.",
    }
