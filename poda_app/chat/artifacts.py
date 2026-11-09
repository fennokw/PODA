"""Downloadable chat content is derived from a saved reply, outside filesystem grants."""
from __future__ import annotations

import csv
import io
import json
import re

from .artifact_formats import DOCUMENT_INPUT

FORMATS = {
    "md": {"md", "markdown"}, "txt": {"txt", "text", "plaintext"}, "csv": {"csv"},
    "tsv": {"tsv"}, "json": {"json"}, "py": {"python", "py"}, "js": {"javascript", "js"},
    "ts": {"typescript", "ts"}, "html": {"html"}, "css": {"css"}, "svg": {"svg", "xml"},
    "yaml": {"yaml", "yml"}, "yml": {"yaml", "yml"}, "sh": {"sh", "bash", "shell"},
}
_EXTENSION = "|".join((*FORMATS, *DOCUMENT_INPUT))
_NAME = re.compile(rf"(?<![\w./\\])([\w][\w.-]{{0,79}}\.({_EXTENSION}))\b", re.I)
_REQUEST = re.compile(r"\b(download(?:able)?|(?:create|make|write|generate|save|output|export|give me|provide|return|send me|convert)\b.{0,120}\b(?:files?|csv|tsv|json|markdown|plain text|script|pdf|word document|docx|excel(?: workbook)?|xlsx|spreadsheet))\b", re.I | re.S)
_TYPES = r"csv|tsv|json|markdown|plain text|python|javascript|typescript|html|css|svg|yaml|shell|bash|pdf|docx|xlsx|word(?: document)?|excel(?: workbook)?|spreadsheet"
_TYPED = re.compile(rf"\b(?:as|into|in)\s+(?:an?\s+)?({_TYPES})\b|\b({_TYPES})\s+(?:file|script)\b|\b(?:a|an)\s+(csv|tsv|json|markdown|plain text|pdf|docx|xlsx|word document|excel(?: workbook)?|spreadsheet)\b", re.I)
_NAMED = re.compile(r"\b(?:named|called|as)\s+[`'\"]?([\w][\w.-]{0,100}\.[a-z0-9]{1,12})\b", re.I)
_QUOTED_NAME = re.compile(r"""\b(?:named|called|as)\s+([`'"])([\w][\w .-]{0,130}\.[a-z0-9]{1,12})\1""", re.I)
_SANDBOX = re.compile(r"\b(?:PODA\s+Sandbox|(?:in|from|inside) (?:the |your )?sandbox)\b", re.I)
_REVISION = re.compile(r"\b(?:edit|change|modify|update|replace|rename|revise|fix|add|remove|read|open|show|download|list|delete)\b", re.I)
_FILE_REF = re.compile(r"\b(?:the|that|this|your|previous|last|generated)\s+(?:\w+\s+)?(?:file|pdf|document|spreadsheet|workbook|csv|script)\b", re.I)
_DISK = re.compile(r"(?:[A-Za-z]:[\\/]|~[\\/]|(?<![\w/])/(?:Users|home|mnt|tmp|var|Volumes|private|opt|workspace)/|file://)|\b(?:on|onto|to|into|in|under|inside)\s+(?:the\s+|my\s+|a\s+|this\s+)?(?:desktop|documents|downloads folder|project folder|folder|directory|filesystem|local disk)\b", re.I)
_FENCE = re.compile(r"^\s{0,3}```([\w-]*)[ \t]*\n(.*?)^\s{0,3}```[ \t]*$", re.M | re.S)
_LABELS = {"markdown": "md", "plain text": "txt", "python": "py", "javascript": "js", "typescript": "ts", "shell": "sh", "bash": "sh", "word": "docx", "word document": "docx", "excel": "xlsx", "excel workbook": "xlsx", "spreadsheet": "xlsx"}


def external_destination(message: str) -> bool:
    return bool(_DISK.search(message))


def requested_name(message: str) -> str | None:
    if external_destination(message):
        return None
    quoted = _QUOTED_NAME.search(message)
    name = _NAMED.search(message) or _NAME.search(message)
    if not _REQUEST.search(message) and not ((name or quoted) and re.search(r"\b(create|generate|make|write|export|give me|provide)\b", message, re.I)):
        return None
    if quoted:
        return quoted[2]
    match = _TYPED.search(message)
    ext = next(value.lower() for value in match.groups() if value) if match else "md"
    if name:
        return name[1]
    return f"poda-output.{_LABELS.get(ext, ext)}"


def creates_attachment(message: str) -> bool:
    return bool(re.search(r"\b(create|generate|make|write|save|export|convert|output)\b", message, re.I))


def refers_to_workspace(message: str, recent: str | None, names: list[str]) -> bool:
    if external_destination(message):
        return False
    if _SANDBOX.search(message):
        return True
    if not _REVISION.search(message):
        return False
    if recent and _FILE_REF.search(message):
        return True
    return any(re.search(rf"(?<![\w./\\]){re.escape(name)}(?![\w./\\])", message, re.I) for name in names)


def source_language(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext in DOCUMENT_INPUT:
        return DOCUMENT_INPUT[ext]
    if ext == "txt":
        return "text"
    return next((lang for lang, suffix in _LABELS.items() if suffix == ext), ext if ext in FORMATS else "base64")


def generated_source(reply: str, name: str) -> tuple[str, str] | None:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext in DOCUMENT_INPUT:
        fmt = "md" if DOCUMENT_INPUT[ext] == "markdown" else "csv"
        if ext == "xlsx":
            blocks = [block[2] for block in _FENCE.finditer(reply) if block[1].lower() in {"csv", ""}]
            return (blocks[0], "auto") if blocks else None
        body = file_body(reply, f"output.{fmt}")
        return (body, "auto") if body else None
    if ext not in FORMATS:
        block = next((block[2] for block in _FENCE.finditer(reply) if block[1].lower() == "base64"), None)
        return ("".join(block.split()), "base64") if block and block.strip() else None
    body = file_body(reply, name)
    return (body, "auto") if body else None


def saved_filename(metadata: dict | None) -> str:
    name = metadata.get("download_name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) and _NAME.fullmatch(name) and name.rsplit(".", 1)[-1].lower() in FORMATS else "poda-response.md"


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
