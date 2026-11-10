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
    "mmd": {"mmd", "mermaid"},
}
_EXTENSION = "|".join((*FORMATS, *DOCUMENT_INPUT, "zip"))
_NAME = re.compile(rf"(?<![\w./\\])([\w][\w.-]{{0,79}}\.({_EXTENSION}))\b", re.I)
_REQUEST = re.compile(r"\b(download(?:able)?|(?:create|make|write|generate|save|output|export|give me|provide|return|send me|convert)\b.{0,120}\b(?:files?|csv|tsv|json|markdown|plain text|script|pdf|word document|docx|excel(?: workbook)?|xlsx|spreadsheet))\b", re.I | re.S)
_TYPES = r"csv|tsv|json|markdown|plain text|python|javascript|typescript|html|css|svg|yaml|shell|bash|pdf|docx|xlsx|word(?: document)?|excel(?: workbook)?|spreadsheet"
_OUTPUT_TYPE = re.compile(rf"\b(?:as|into|in)\s+(?:an?\s+)?({_TYPES})\b", re.I)
_ZIP_OUTPUT = re.compile(r"\b(?:(?:as|into|in)\s+(?:an?\s+)?zip|zip\s+(?:file|archive|export))\b", re.I)
_TYPED = re.compile(rf"\b({_TYPES})\s+(?:file|script)\b|\b(?:a|an)\s+(csv|tsv|json|markdown|plain text|pdf|docx|xlsx|word document|excel(?: workbook)?|spreadsheet)\b", re.I)
_NAMED = re.compile(r"\b(?:named|called|as)\s+[`'\"]?([\w][\w.-]{0,100}\.[a-z0-9]{1,12})\b", re.I)
_QUOTED_NAME = re.compile(r"""\b(?:named|called|as)\s+([`'"])([\w][\w .-]{0,130}\.[a-z0-9]{1,12})\1""", re.I)
_SANDBOX = re.compile(r"\b(?:PODA\s+Sandbox|(?:in|from|inside) (?:the |your )?sandbox)\b", re.I)
_REVISION = re.compile(r"\b(?:edit|change|modify|update|replace|rename|revise|fix|add|remove|read|open|show|download|list|delete)\b", re.I)
_FILE_REF = re.compile(r"\b(?:the|that|this|your|previous|last|generated)\s+(?:\w+\s+)?(?:file|pdf|document|spreadsheet|workbook|csv|script)\b", re.I)
_DISK = re.compile(r"(?:[A-Za-z]:[\\/]|~[\\/]|(?<![\w/])/(?:Users|home|mnt|tmp|var|Volumes|private|opt|workspace)/|file://)|\b(?:on|onto|to|into|in|under|inside)\s+(?:the\s+|my\s+|a\s+|this\s+)?(?:desktop|documents|downloads folder|project folder|folder|directory|filesystem|local disk)\b", re.I)
_FENCE = re.compile(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})(?P<language>[\w-]*)[ \t]*\r?\n(?P<body>.*?)^[ ]{0,3}(?P=fence)[ \t]*\r?$", re.M | re.S)
_MERMAID = re.compile(r"\bmermaid(?:\.js)?\b", re.I)
_MERMAID_HEADER = re.compile(r"^\s*(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|quadrantChart|requirementDiagram|C4\w+|mindmap|timeline|gitGraph|sankey-beta|xychart-beta|block-beta|packet-beta|kanban|architecture-beta|radar-beta|treemap-beta)\b")
_RETRY = re.compile(r"(?:please\s+)?(?:(?:now\s+)?try(?:\s+(?:it\s+)?again|\s+it\s+now|\s+now)?|retry(?:\s+it)?)[.!?]*", re.I)
_LABELS = {"markdown": "md", "plain text": "txt", "python": "py", "javascript": "js", "typescript": "ts", "shell": "sh", "bash": "sh", "word": "docx", "word document": "docx", "excel": "xlsx", "excel workbook": "xlsx", "spreadsheet": "xlsx"}


def external_destination(message: str) -> bool:
    return bool(_DISK.search(message))


def requested_name(message: str) -> str | None:
    if external_destination(message):
        return None
    quoted = _QUOTED_NAME.search(message)
    named = _NAMED.search(message)
    # A library name in the request describes the syntax, not the export filename.
    mentioned = next((m for m in _NAME.finditer(message) if m[1].casefold() != "mermaid.js"), None)
    wants_diagram = is_mermaid_request(message) and creates_attachment(message)
    names_file = (named or quoted or mentioned) and re.search(r"\b(create|generate|make|write|export|give me|provide)\b", message, re.I)
    wants_zip = _ZIP_OUTPUT.search(message) and (creates_attachment(message) or re.search(r"\b(give me|provide|return|download)\b", message, re.I))
    if not (_REQUEST.search(message) or wants_diagram or wants_zip or names_file):
        return None
    explicit = quoted[2] if quoted else named[1] if named else None
    if wants_zip:
        if explicit and explicit.lower().endswith(".zip"):
            return explicit
        archive = next((m[1] for m in _NAME.finditer(message) if m[2].lower() == "zip"), None)
        return archive or "poda-output.zip"
    if explicit:
        return explicit
    match = _OUTPUT_TYPE.search(message) or _TYPED.search(message)
    if match:
        ext = next(value.lower() for value in match.groups() if value)
        suffix = _LABELS.get(ext, ext)
        if not mentioned or not mentioned[1].lower().endswith(f".{suffix}"):
            return f"poda-output.{suffix}"
    return mentioned[1] if mentioned else "poda-output.md"


def is_mermaid_request(message: str) -> bool:
    return bool(_MERMAID.search(message))


def is_attachment_retry(message: str) -> bool:
    return bool(_RETRY.fullmatch(" ".join(message.split())))


def creates_attachment(message: str) -> bool:
    return bool(re.search(r"\b(create|generate|make|write|save|export|convert|output|package|bundle|zip)\b", message, re.I))


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
    if ext == "mmd":
        return "mermaid"
    if ext == "zip":
        return "json"
    return next((lang for lang, suffix in _LABELS.items() if suffix == ext), ext if ext in FORMATS else "base64")


def generated_source(reply: str, name: str, *, mermaid: bool = False) -> tuple[str, str] | None:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "zip":
        body = file_body(reply, "output.json")
        return (body, "auto") if body else None
    if ext in {"md", "mmd"}:
        body = _mermaid_source(reply, markdown=ext == "md")
        if body or mermaid or ext == "mmd":
            return (body, "auto") if body else None
    if ext in DOCUMENT_INPUT:
        fmt = "md" if DOCUMENT_INPUT[ext] == "markdown" else "csv"
        if ext == "xlsx":
            blocks = [block["body"] for block in _FENCE.finditer(reply) if block["language"].lower() in {"csv", ""}]
            return (blocks[0], "auto") if blocks else None
        body = file_body(reply, f"output.{fmt}")
        return (body, "auto") if body else None
    if ext not in FORMATS:
        block = next((block["body"] for block in _FENCE.finditer(reply) if block["language"].lower() == "base64"), None)
        return ("".join(block.split()), "base64") if block and block.strip() else None
    body = file_body(reply, name)
    return (body, "auto") if body else None


def _mermaid_source(reply: str, *, markdown: bool) -> str | None:
    body = file_body(reply, "output.md") or ""
    diagrams = [block["body"].strip() for block in _FENCE.finditer(body)
                if block["language"].lower() in FORMATS["mmd"] and block["body"].strip()]
    if not diagrams and _MERMAID_HEADER.match(body):
        diagrams = [body.strip()]
    if not diagrams:
        return None
    if not markdown:
        return diagrams[0] + "\n"
    # A Markdown wrapper is the requested document. Otherwise, use just the
    # diagram blocks and leave model commentary outside the exported file.
    if any(block["language"].lower() in FORMATS["md"] for block in _FENCE.finditer(reply)) and _FENCE.search(body):
        return body
    return "\n\n".join(f"```mermaid\n{diagram}\n```" for diagram in diagrams) + "\n"


def saved_filename(metadata: dict | None) -> str:
    name = metadata.get("download_name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) and _NAME.fullmatch(name) and name.rsplit(".", 1)[-1].lower() in FORMATS else "poda-response.md"


def file_body(content: str, filename: str) -> str | None:
    ext = filename.rsplit(".", 1)[-1].lower()
    allowed = FORMATS.get(ext)
    if not allowed:
        return None
    blocks = list(_FENCE.finditer(content))
    matched = next((block["body"] for block in blocks if block["language"].lower() in allowed), None)
    if matched is None and ext not in {"md", "txt"} and len(blocks) == 1 and not blocks[0]["language"]:
        matched = blocks[0]["body"]
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
