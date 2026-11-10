"""PODA-owned workspace. File permissions here are separate from user-folder grants."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from threading import RLock

from ..chat.artifact_formats import DOCUMENT_INPUT, render_document
from ..runtime import config
from .fs_tools import _fail, _ok

MAX_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024
_LOCK = RLock()
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def filename(value: str) -> str:
    if (not value or len(value.encode("utf-8")) > 180 or value != value.strip()
            or value.startswith(".") or value.endswith(".") or _RESERVED.match(value)
            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value)):
        raise ValueError("Use a filename without folders, device names, or special path characters.")
    return value


def _directory(parent: Path, name: str) -> Path:
    root = parent / name
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise PermissionError("PODA's workspace directory is unavailable.")
    return root


def root() -> Path:
    config.ensure_dirs()
    return _directory(config.DATA_DIR, "PODA Sandbox")


def _read(path: Path, limit: int = MAX_BYTES) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("PODA Sandbox accepts ordinary files only.")
    if info.st_size > limit:
        raise ValueError("The file exceeds the sandbox size limit.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not os.path.samestat(info, opened):
            raise PermissionError("The sandbox file changed while opening it.")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("The file exceeds the sandbox size limit.")
    return data


def _atomic_write(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".poda-", delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def files() -> list[dict]:
    output = []
    for path in root().iterdir():
        try:
            filename(path.name)
            info = path.lstat()
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            output.append({"filename": path.name, "bytes": info.st_size, "modified": info.st_mtime})
    return sorted(output, key=lambda item: (-item["modified"], item["filename"]))[:500]


def read_bytes(name: str) -> bytes:
    return _read(root() / filename(name))


def archive_path(value: str) -> str:
    parts = value.split("/")
    if len(value.encode("utf-8")) > 240 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("ZIP paths must stay inside the archive.")
    for part in parts:
        filename(part.lstrip("."))  # allow project dotfiles without permitting traversal
    return value


def archive_source_entries(content: str) -> list[tuple[str, bytes]]:
    if len(content.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("Generated ZIP source may be up to 512 KiB.")
    entries = json.loads(content)
    if not isinstance(entries, list) or not entries or any(
        not isinstance(item, dict) or set(item) != {"filename", "content"}
        or not isinstance(item["filename"], str) or not isinstance(item["content"], str)
        for item in entries
    ):
        raise ValueError("ZIP source must be a JSON array of filename and content strings.")
    result = [(archive_path(item["filename"]), item["content"].encode("utf-8")) for item in entries]
    if len({name.casefold() for name, _ in result}) != len(result):
        raise ValueError("ZIP filenames must be distinct.")
    return result


def pack_archive(entries: list[tuple[str, bytes]]) -> bytes:
    if not 1 <= len(entries) <= 64:
        raise ValueError("A ZIP needs 1 to 64 files.")
    if sum(len(data) for _, data in entries) > MAX_BYTES:
        raise ValueError("ZIP contents may be up to 16 MiB before compression.")
    paths = [archive_path(name).casefold() for name, _ in entries]
    names = set(paths)
    if len(names) != len(paths):
        raise ValueError("ZIP filenames must be distinct.")
    if any("/".join(parts[:index]) in names for name in paths
           for parts in [name.split("/")] for index in range(1, len(parts))):
        raise ValueError("A ZIP file path cannot also be a directory.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            member = zipfile.ZipInfo(name)
            member.create_system = 3
            mode = 0o755 if name.lower().endswith((".command", ".sh")) else 0o644
            member.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _source_path(name: str, digest: str) -> Path:
    key = hashlib.sha256((os.path.normcase(name) + ":" + digest).encode("utf-8")).hexdigest()
    return _directory(root(), ".poda-sources") / key


def save(name: str, data: bytes, *, expected_sha256: str | None = None, unique: bool = False,
         source: str | None = None) -> dict:
    name = filename(name)
    if len(data) > MAX_BYTES:
        raise ValueError("A PODA Sandbox file may be up to 16 MiB.")
    with _LOCK:
        path = root() / name
        if unique:
            base = path
            for index in range(2, 10002):
                if not os.path.lexists(path):
                    break
                suffix = f"-{index}{base.suffix}"
                stem = base.stem.encode("utf-8")[:180 - len(suffix.encode("utf-8"))].decode("utf-8", errors="ignore")
                path = base.with_name(filename(stem + suffix))
            else:
                raise FileExistsError("Too many sandbox files share that name.")
        if expected_sha256:
            if hashlib.sha256(_read(path)).hexdigest() != expected_sha256.lower():
                raise ValueError("The file changed. Read the latest version before editing it.")
        elif os.path.lexists(path):
            raise FileExistsError("Read the file and supply its SHA-256 to replace it.")
        digest = hashlib.sha256(data).hexdigest()
        if source is not None:
            record = json.dumps({"sha256": digest, "content": source}, ensure_ascii=False).encode()
            _atomic_write(_source_path(path.name, digest), record)
        _atomic_write(path, data)
        if _read(path) != data:
            raise OSError("PODA could not verify the sandbox write.")
        if expected_sha256 and expected_sha256.lower() != digest and path.suffix.lstrip(".").lower() in DOCUMENT_INPUT:
            try:
                _source_path(path.name, expected_sha256.lower()).unlink(missing_ok=True)
            except OSError:
                pass
        return {"filename": path.name, "bytes": len(data), "sha256": digest}


def save_content(name: str, content: str, *, encoding: str = "auto",
                 expected_sha256: str | None = None, unique: bool = False) -> dict:
    ext = Path(filename(name)).suffix.lstrip(".").lower()
    if encoding == "base64":
        if len(content) > (MAX_BYTES + 2) // 3 * 4:
            raise ValueError("A PODA Sandbox file may be up to 16 MiB.")
        return save(name, base64.b64decode(content, validate=True), expected_sha256=expected_sha256, unique=unique)
    if encoding != "auto":
        raise ValueError("Use encoding auto or base64.")
    data = content.encode("utf-8")
    if ext == "zip":
        archive = pack_archive(archive_source_entries(content))
        return save(name, archive, expected_sha256=expected_sha256, unique=unique)
    if ext in DOCUMENT_INPUT:
        if not content.strip() or len(data) > MAX_SOURCE_BYTES:
            raise ValueError("A generated document needs between 1 byte and 512 KiB of source text.")
        with _LOCK:
            return save(name, render_document(ext, content), expected_sha256=expected_sha256, unique=unique, source=content)
    return save(name, data, expected_sha256=expected_sha256, unique=unique)


def sandbox_list() -> dict:
    return _ok({"files": files()[:60]}, "Listed PODA Sandbox files.")


def sandbox_read(path: str, offset: int = 0) -> dict:
    with _LOCK:
        data = read_bytes(path)
        digest = hashlib.sha256(data).hexdigest()
        text, encoding = None, "auto"
        if Path(path).suffix.lstrip(".").lower() in DOCUMENT_INPUT:
            try:
                record = json.loads(_read(_source_path(path, digest), MAX_SOURCE_BYTES * 6 + 100))
                if record["sha256"] == digest:
                    text = record["content"]
            except (OSError, ValueError, KeyError):
                pass
        if text is None:
            try:
                text = data.decode("utf-8")
                if "\x00" in text:
                    raise ValueError("binary")
            except (UnicodeError, ValueError):
                encoding = "base64"
                text = base64.b64encode(data).decode("ascii")
        end = offset + 3600
        return _ok({"filename": path, "bytes": len(data), "sha256": digest, "encoding": encoding,
                    "content": text[offset:end], "next_offset": end if len(text) > end else None,
                    "truncated": len(text) > end},
                   "Read the current PODA Sandbox file and verified its SHA-256.")


def sandbox_write(path: str, content: str, encoding: str = "auto", expected_sha256: str | None = None) -> dict:
    try:
        return _ok(save_content(path, content, encoding=encoding, expected_sha256=expected_sha256),
                   "Saved and read back the exact file bytes in PODA Sandbox.")
    except (ValueError, OSError, csv.Error) as exc:
        return _fail(str(exc))
