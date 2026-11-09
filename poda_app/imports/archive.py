"""Secure parsing of ChatGPT/Claude export ZIPs into normalized conversations.

The raw archive is never persisted by PODA. It is parsed from a temporary file,
validated against path traversal / zip-bomb limits, and deleted after import.
Only text authored by the user or assistant is imported; attachments/binary
content are ignored.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MAX_ARCHIVE_BYTES = 1_500_000_000
MAX_FILES = 30_000
MAX_UNCOMPRESSED_BYTES = 3_000_000_000
MAX_JSON_MEMBER_BYTES = 750_000_000
MAX_MESSAGE_CHARS = 24_000
MAX_MESSAGES = 250_000


class ImportArchiveError(ValueError):
    pass


@dataclass
class ImportMessage:
    role: str
    text: str
    created_at: str | None = None
    source_id: str | None = None


@dataclass
class ImportConversation:
    source_id: str
    title: str
    messages: list[ImportMessage] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename.replace("\\", "/")
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and not name.startswith("/")


def validate_zip(path: Path) -> list[zipfile.ZipInfo]:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ImportArchiveError("Archive is too large for the local importer (1.5 GB compressed limit).")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ImportArchiveError("The selected file is not a valid ZIP archive.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ImportArchiveError(f"Archive contains too many files ({len(infos):,}; limit {MAX_FILES:,}).")
        total = 0
        for info in infos:
            if not _safe_member(info):
                raise ImportArchiveError(f"Unsafe archive path rejected: {info.filename!r}")
            # Unix symlink bit; raw exports should contain files/directories only.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ImportArchiveError(f"Symbolic links are not allowed in imports: {info.filename!r}")
            total += int(info.file_size or 0)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ImportArchiveError("Archive expands beyond the 3 GB safety limit.")
            if info.file_size > 0 and info.compress_size > 0 and info.file_size / max(1, info.compress_size) > 500:
                raise ImportArchiveError(f"Suspicious compression ratio in {info.filename!r}.")
        return infos


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value if _text(v)).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                return _text(value[key])
    return ""


def _clean(text: str) -> str:
    text = (text or "").replace("\x00", "").strip()
    return text[:MAX_MESSAGE_CHARS]


def _role(value: Any) -> str | None:
    v = str(value or "").lower().strip()
    if v in {"user", "human", "customer"}:
        return "user"
    if v in {"assistant", "claude", "chatgpt", "bot", "ai"}:
        return "assistant"
    return None


def _chatgpt_conversation(obj: dict[str, Any], index: int) -> ImportConversation | None:
    mapping = obj.get("mapping")
    if not isinstance(mapping, dict):
        return None
    msgs: list[ImportMessage] = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        m = node.get("message")
        if not isinstance(m, dict):
            continue
        role = _role((m.get("author") or {}).get("role") if isinstance(m.get("author"), dict) else m.get("role"))
        if not role:
            continue
        content = m.get("content") or {}
        text = ""
        if isinstance(content, dict):
            parts = content.get("parts")
            text = _text(parts) if isinstance(parts, list) else _text(content.get("text") or content.get("content"))
        else:
            text = _text(content)
        text = _clean(text)
        if not text:
            continue
        msgs.append(ImportMessage(role=role, text=text, created_at=str(m.get("create_time") or "") or None, source_id=str(m.get("id") or "") or None))
    # Mapping order is tree order, not necessarily chronological.
    msgs.sort(key=lambda m: (m.created_at is None, m.created_at or ""))
    if not msgs:
        return None
    cid = str(obj.get("conversation_id") or obj.get("id") or f"chatgpt-{index}")
    return ImportConversation(cid, _clean(str(obj.get("title") or "Untitled conversation"))[:180], msgs,
                              str(obj.get("create_time") or "") or None, str(obj.get("update_time") or "") or None)


def _claude_conversation(obj: dict[str, Any], index: int) -> ImportConversation | None:
    raw = obj.get("chat_messages") or obj.get("messages")
    if not isinstance(raw, list):
        return None
    msgs: list[ImportMessage] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = _role(m.get("sender") or m.get("role") or m.get("author"))
        if not role:
            continue
        text = _clean(_text(m.get("text") if "text" in m else m.get("content")))
        if not text:
            continue
        msgs.append(ImportMessage(role, text, str(m.get("created_at") or m.get("createdAt") or "") or None,
                                  str(m.get("uuid") or m.get("id") or "") or None))
    if not msgs:
        return None
    cid = str(obj.get("uuid") or obj.get("id") or obj.get("conversation_id") or f"claude-{index}")
    return ImportConversation(cid, _clean(str(obj.get("name") or obj.get("title") or "Untitled conversation"))[:180], msgs,
                              str(obj.get("created_at") or obj.get("createdAt") or "") or None,
                              str(obj.get("updated_at") or obj.get("updatedAt") or "") or None)


def _walk_candidates(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
    elif isinstance(data, dict):
        # Some exports wrap conversations in a top-level key.
        for key in ("conversations", "chats", "data"):
            value = data.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return
        yield data


def parse_archive(path: Path) -> tuple[str, list[ImportConversation], dict[str, Any]]:
    infos = validate_zip(path)
    conversations: list[ImportConversation] = []
    provider_hits = {"chatgpt": 0, "claude": 0}
    with zipfile.ZipFile(path) as zf:
        json_infos = [i for i in infos if not i.is_dir() and i.filename.lower().endswith(".json") and i.file_size <= MAX_JSON_MEMBER_BYTES]
        # Prefer likely conversation files first but accept nested provider exports.
        json_infos.sort(key=lambda i: ("conversation" not in i.filename.lower() and "chat" not in i.filename.lower(), i.filename.lower()))
        seen_ids: set[str] = set()
        for info in json_infos:
            try:
                with zf.open(info) as fh:
                    data = json.loads(fh.read().decode("utf-8", "replace"))
            except Exception:
                continue
            for idx, obj in enumerate(_walk_candidates(data)):
                conv = _chatgpt_conversation(obj, idx)
                provider = "chatgpt"
                if conv is None:
                    conv = _claude_conversation(obj, idx)
                    provider = "claude"
                if conv is None or conv.source_id in seen_ids:
                    continue
                seen_ids.add(conv.source_id)
                conversations.append(conv)
                provider_hits[provider] += 1
                if sum(len(c.messages) for c in conversations) > MAX_MESSAGES:
                    raise ImportArchiveError(f"Archive exceeds the {MAX_MESSAGES:,}-message safety limit.")
    if not conversations:
        raise ImportArchiveError("No supported ChatGPT or Claude conversations were found in the ZIP.")
    provider = max(provider_hits, key=provider_hits.get)
    if provider_hits["chatgpt"] and provider_hits["claude"]:
        provider = "mixed"
    metadata = {"provider_hits": provider_hits, "files_scanned": len(infos), "json_files_scanned": len([i for i in infos if i.filename.lower().endswith('.json')])}
    return provider, conversations, metadata
