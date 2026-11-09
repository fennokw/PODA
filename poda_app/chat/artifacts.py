"""Downloadable chat content is derived from a saved reply, outside filesystem grants."""
from __future__ import annotations

import csv
import io
import json
import re

FORMATS = {
    "md": {"md", "markdown"}, "txt": {"txt", "text", "plaintext"}, "csv": {"csv"},
    "tsv": {"tsv"}, "json": {"json"}, "py": {"python", "py"}, "js": {"javascript", "js"},
    "ts": {"typescript", "ts"}, "html": {"html"}, "css": {"css"}, "svg": {"svg", "xml"},
    "yaml": {"yaml", "yml"}, "yml": {"yaml", "yml"}, "sh": {"sh", "bash", "shell"},
}
_EXTENSION = "|".join(FORMATS)
_NAME = re.compile(rf"(?<![\w./\\])([\w][\w.-]{{0,79}}\.({_EXTENSION}))\b", re.I)
_REQUEST = re.compile(r"\b(download(?:able)?|(?:create|make|write|generate|save|output|export|give me|provide|return|send me)\b.{0,120}\b(?:file|csv|tsv|json|markdown|plain text|script|pdf|docx|xlsx))\b", re.I | re.S)
_TYPES = r"csv|tsv|json|markdown|plain text|python|javascript|typescript|html|css|svg|yaml|shell|bash"
_TYPED = re.compile(rf"\b(?:as|into|in)\s+(?:an?\s+)?({_TYPES})\b|\b({_TYPES})\s+(?:file|script)\b|\b(?:a|an)\s+(csv|tsv|json|markdown|plain text)\b", re.I)
_UNSUPPORTED = re.compile(r"\.(pdf|docx|xlsx|pptx|zip)\b|\b(?:an?\s+|as\s+)(pdf|docx|xlsx|pptx|zip)\b", re.I)
_DISK = re.compile(r"(?:[A-Za-z]:[\\/]|~[\\/]|(?<![\w/])/(?:Users|home|mnt|tmp|var|Volumes|private|opt|workspace)/|file://)|\b(?:on|onto|to|into|in|under|inside)\s+(?:the\s+|my\s+|a\s+|this\s+)?(?:desktop|documents|downloads folder|project folder|folder|directory|filesystem|local disk)\b", re.I)
_FENCE = re.compile(r"^\s{0,3}```([\w-]*)[ \t]*\n(.*?)^\s{0,3}```[ \t]*$", re.M | re.S)
_LABELS = {"markdown": "md", "plain text": "txt", "python": "py", "javascript": "js", "typescript": "ts", "shell": "sh", "bash": "sh"}


def external_destination(message: str) -> bool:
    return bool(_DISK.search(message))


def requested_name(message: str) -> str | None:
    if external_destination(message):
        return None
    name = _NAME.search(message)
    if not _REQUEST.search(message) and not (name and re.search(r"\b(create|generate|make|write|export|give me|provide)\b", message, re.I)):
        return None
    if name:
        return name[1]
    match = _TYPED.search(message)
    ext = next(value.lower() for value in match.groups() if value) if match else "md"
    return f"poda-output.{_LABELS.get(ext, ext)}"


def unsupported_format(message: str) -> bool:
    return bool(_UNSUPPORTED.search(message))


def saved_filename(metadata: dict | None) -> str:
    name = metadata.get("download_name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) and _NAME.fullmatch(name) else "poda-response.md"


def file_body(content: str, filename: str) -> str | None:
    ext = filename.rsplit(".", 1)[-1].lower()
    allowed = FORMATS.get(ext)
    if not allowed:
        return None
    blocks = list(_FENCE.finditer(content))
    matched = next((block[2] for block in blocks if block[1].lower() in allowed), None)
    if matched is None and ext not in {"md", "txt"} and len(blocks) == 1 and not blocks[0][1]:
        matched = blocks[0][2]
    if matched is None:
        if ext in {"md", "txt", "json", "csv", "tsv"}:
            matched = content
        else:
            return None
    if ext == "json":
        try:
            json.loads(matched)
        except ValueError:
            return None
    if ext in {"csv", "tsv"}:
        try:
            reader = csv.reader(io.StringIO(matched), delimiter="," if ext == "csv" else "\t", strict=True)
            header = next(reader, [])
            if len(header) < 2 or any(row and len(row) != len(header) for row in reader):
                return None
        except csv.Error:
            return None
    return matched if matched.strip() else None
