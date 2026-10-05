"""Scoped filesystem tools. Every path is canonicalized and must fall inside an active grant root.

Every tool returns {"ok": bool, "result": ..., "error": str|None, "verification": str}. No delete tool exists.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from . import grants

MAX_WRITE_BYTES = 2 * 1024 * 1024
MAX_EDIT_BYTES = 500 * 1024
MAX_READ_BYTES = 200_000
MAX_HASH_BYTES = 8 * 1024 * 1024  # Never ingest an arbitrary multi-GB file just to hash it.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".poda-backups"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ok(result: Any, verification: str) -> dict[str, Any]:
    return {"ok": True, "result": result, "error": None, "verification": verification}


def _fail(error: str, verification: str = "no change made") -> dict[str, Any]:
    return {"ok": False, "result": None, "error": error, "verification": verification}


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _atomic_write(target: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".poda-tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(target: Path) -> str | None:
    if not target.exists():
        return None
    bak = target.with_name(f"{target.name}.poda-bak-{time.strftime('%Y%m%d-%H%M%S')}")
    bak.write_bytes(target.read_bytes())
    return str(bak)


def fs_list(path: str, depth: int = 1) -> dict[str, Any]:
    try:
        root, grant = grants.resolve(path, "read")
    except PermissionError as exc:
        return _fail(str(exc))
    if not root.exists():
        return _fail(f"Path does not exist: {root}")
    if not root.is_dir():
        return _fail(f"Not a directory: {root}")
    depth = max(0, min(int(depth), 2))
    entries: list[dict[str, Any]] = []

    def walk(d: Path, level: int) -> None:
        try:
            children = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            entries.append({"path": str(d), "error": str(exc)})
            return
        for child in children:
            if child.name in SKIP_DIRS:
                continue
            try:
                st = child.lstat()
                entries.append({"path": str(child), "name": child.name, "type": "dir" if child.is_dir() else ("symlink" if child.is_symlink() else "file"),
                                "bytes": st.st_size if child.is_file() else None, "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))})
            except OSError:
                continue
            if child.is_dir() and not child.is_symlink() and level < depth:
                walk(child, level + 1)
            if len(entries) >= 2000:
                return

    walk(root, 0)
    return _ok({"root": str(root), "grant": grant["root_path"], "count": len(entries), "entries": entries[:2000]}, f"listed {len(entries)} entries under {root}")


def fs_read(path: str, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    try:
        target, grant = grants.resolve(path, "read")
    except PermissionError as exc:
        return _fail(str(exc))
    if not target.exists():
        return _fail(f"File does not exist: {target}")
    if not target.is_file():
        return _fail(f"Not a regular file: {target}")
    max_bytes = max(1, min(int(max_bytes), MAX_READ_BYTES))
    size = target.stat().st_size
    if size > MAX_HASH_BYTES:
        return _fail(f"File is {size} bytes, exceeding the {MAX_HASH_BYTES}-byte bounded read/hash limit; narrow the request")
    # Bounded read, even when a file is concurrently extended after stat().
    with target.open("rb") as fh:
        data = fh.read(MAX_HASH_BYTES + 1)
    if len(data) > MAX_HASH_BYTES:
        return _fail("File grew beyond the bounded read limit during access")
    if not _is_text(data):
        return _fail(f"Refusing to read binary file ({size} bytes): {target}")
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    checksum = _sha(data)
    return _ok({"path": str(target), "bytes": len(data), "sha256": checksum, "truncated": truncated, "content": text},
               f"read {min(len(data), max_bytes)} of {len(data)} bytes; sha256 {checksum[:12]}")


def fs_search(root: str, pattern: str, glob: str = "**/*", max_results: int = 200) -> dict[str, Any]:
    try:
        base, grant = grants.resolve(root, "read")
    except PermissionError as exc:
        return _fail(str(exc))
    if not base.is_dir():
        return _fail(f"Not a directory: {base}")
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return _fail(f"Invalid regex: {exc}")
    max_results = max(1, min(int(max_results), 200))
    hits: list[dict[str, Any]] = []
    scanned = 0
    for p in base.glob(glob):
        if any(part in SKIP_DIRS for part in p.relative_to(base).parts):
            continue
        if not p.is_file() or p.is_symlink():
            continue
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if not _is_text(data):
            continue
        scanned += 1
        for lineno, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append({"path": str(p), "line": lineno, "text": line.strip()[:300]})
                if len(hits) >= max_results:
                    return _ok({"root": str(base), "pattern": pattern, "scanned_files": scanned, "hits": hits, "truncated": True}, f"{len(hits)} hits (truncated) across {scanned} files")
    return _ok({"root": str(base), "pattern": pattern, "scanned_files": scanned, "hits": hits, "truncated": False}, f"{len(hits)} hits across {scanned} files")


def fs_write(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    try:
        target, grant = grants.resolve(path, "write")
    except PermissionError as exc:
        return _fail(str(exc))
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        return _fail(f"Content is {len(data)} bytes; the write limit is {MAX_WRITE_BYTES} bytes")
    if target.exists() and target.is_dir():
        return _fail(f"Target is a directory: {target}")
    if target.exists() and not overwrite:
        return _fail(f"File already exists and overwrite=false: {target}")
    backup = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # parent must still be inside the grant after mkdir
        grants.resolve(str(target.parent), "write")
        backup = _backup(target) if target.exists() else None
        _atomic_write(target, data)
        readback = target.read_bytes()
    except PermissionError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(f"Write failed: {exc}")
    if readback != data:
        return _fail("Readback mismatch after write", "file content does not match what was written")
    return _ok({"path": str(target), "bytes": len(data), "sha256": _sha(readback), "overwritten": bool(backup), "backup": backup, "grant": grant["root_path"]},
               f"wrote {len(data)} bytes and read back identical content; sha256 {_sha(readback)[:12]}")


def fs_edit(path: str, expected_sha256: str, old_text: str, new_text: str, replace_all: bool = False) -> dict[str, Any]:
    try:
        target, grant = grants.resolve(path, "write")
    except PermissionError as exc:
        return _fail(str(exc))
    if not target.is_file():
        return _fail(f"Not a regular file: {target}")
    data = target.read_bytes()
    if len(data) > MAX_EDIT_BYTES:
        return _fail(f"File is {len(data)} bytes; the edit limit is {MAX_EDIT_BYTES} bytes")
    current = _sha(data)
    if current != (expected_sha256 or "").lower():
        return _fail(f"Stale hash: file sha256 is {current[:12]}…, expected {str(expected_sha256)[:12]}…; re-read the file first", "file left untouched")
    if not _is_text(data):
        return _fail("Refusing to edit a binary file")
    text = data.decode("utf-8")
    if not old_text:
        return _fail("old_text must not be empty")
    count = text.count(old_text)
    if count == 0:
        return _fail("old_text was not found in the file", "file left untouched")
    if count > 1 and not replace_all:
        return _fail(f"old_text matches {count} times; pass replace_all=true or make it unique", "file left untouched")
    updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
    new_data = updated.encode("utf-8")
    if abs(len(new_data) - len(data)) > MAX_EDIT_BYTES:
        return _fail("Patch exceeds the size limit")
    try:
        backup = _backup(target)
        _atomic_write(target, new_data)
        readback = target.read_bytes()
    except OSError as exc:
        return _fail(f"Edit failed: {exc}")
    if readback != new_data:
        return _fail("Readback mismatch after edit", "file content does not match the intended edit")
    return _ok({"path": str(target), "replacements": count if replace_all else 1, "bytes_before": len(data), "bytes_after": len(new_data),
                "sha256_before": current, "sha256": _sha(readback), "backup": backup, "grant": grant["root_path"]},
               f"replaced {count if replace_all else 1} occurrence(s); readback sha256 {_sha(readback)[:12]}")


def fs_move(src: str, dst: str) -> dict[str, Any]:
    try:
        source, g1 = grants.resolve(src, "write")
        dest, g2 = grants.resolve(dst, "write")
    except PermissionError as exc:
        return _fail(str(exc))
    if not source.exists():
        return _fail(f"Source does not exist: {source}")
    if dest.exists():
        return _fail(f"Destination already exists: {dest}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        grants.resolve(str(dest.parent), "write")
        os.replace(source, dest)
    except PermissionError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(f"Move failed: {exc}")
    return _ok({"from": str(source), "to": str(dest)}, f"moved; destination exists={dest.exists()} source exists={source.exists()}")
