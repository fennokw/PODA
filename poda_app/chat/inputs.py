"""Bounded, turn-local copies of files supplied in the chat composer."""
from __future__ import annotations

import base64
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..agent import sandbox_files
from . import artifacts, document_ocr

MAX_FILES = 4
MAX_TOTAL_BYTES = 32 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpeg", ".jpg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_EDIT = re.compile(r"\b(edit|modify|fix|update|patch|change|rewrite|revise|replace|correct)\b|\b(?:add|remove)\b.{0,100}\b(?:to|from|in)\b", re.I)
_EXTERNAL_READ = re.compile(r"\b(?:my|our|connected) (?:calendar|inbox|e-?mail|notion|workload|projects?)\b|\b(?:search|check|read|pull|fetch|look up)\b.{0,40}\b(?:inbox|notion|calendar|e-?mails?|workload)\b", re.I)


@dataclass(frozen=True)
class InputFile:
    name: str
    data: bytes

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


def needs_separate_tools(message: str, intents: set[str], *, uploaded: bool = False) -> bool:
    local = {"file_create", "file_edit", "file_read"} if uploaded else set()
    return bool(artifacts.external_destination(message) or intents - local or _EXTERNAL_READ.search(message))


def edit_target(message: str, files: tuple[InputFile, ...]) -> InputFile | None:
    if len(files) == 1 and files[0].suffix in {".zip", ".pdf"} and _EDIT.search(message):
        return files[0]
    return None


def edited_name(file: InputFile) -> str:
    stem = Path(file.name).stem.encode()[:155].decode(errors="ignore")
    return f"{stem}-edited{file.suffix}"


def _zip_members(file: InputFile) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(file.data)) as archive:
        members = archive.infolist()
        if len(members) > 128:
            raise ValueError("This ZIP has too many entries. Attach a ZIP of just the relevant source files.")
        files: dict[str, bytes] = {}
        seen = set()
        total = 0
        for member in members:
            name = sandbox_files.archive_path(member.filename.rstrip("/") if member.is_dir() else member.filename)
            mode = stat.S_IFMT(member.external_attr >> 16)
            if mode not in (0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG) or member.flag_bits & 1:
                raise ValueError("ZIP attachments must contain ordinary, unencrypted files.")
            if name.casefold() in seen:
                raise ValueError("This ZIP has duplicate paths.")
            seen.add(name.casefold())
            if member.is_dir():
                continue
            total += member.file_size
            if len(files) >= 64 or total > sandbox_files.MAX_BYTES:
                raise ValueError("Attach a ZIP with up to 64 files and 16 MiB of uncompressed contents.")
            files[name] = archive.read(member)
    if not files:
        raise ValueError("This ZIP contains no files.")
    return files


def _text(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8-sig")
        return None if "\x00" in text else text
    except UnicodeError:
        return None


def _image(file: InputFile) -> tuple[str, str]:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(file.data)) as original:
        if original.width * original.height > 30_000_000:
            raise ValueError("Resize this image to under 30 megapixels.")
        image = ImageOps.exif_transpose(original)
        image.thumbnail((2200, 2200))
        output = io.BytesIO()
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            flattened = Image.new("RGB", image.size, "white")
            rgba = image.convert("RGBA")
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            image = flattened
        image.convert("RGB").save(output, "PNG")
        try:
            recognized = document_ocr.image_text(image)
        except Exception:
            recognized = ""
    return base64.b64encode(output.getvalue()).decode("ascii"), recognized


@dataclass
class Prepared:
    context: str
    images: list[str]
    image_names: list[str]
    warnings: list[str]
    original_zip: dict[str, bytes] | None
    editable_paths: set[str]


def prepare(files: tuple[InputFile, ...], message: str, limit: int, editing: InputFile | None) -> Prepared:
    result = Prepared("", [], [], [], None, set())
    blocks = []
    per_file = max(800, limit // max(1, len(files)))
    for file in files:
        try:
            if file.suffix in IMAGE_SUFFIXES:
                image, recognized = _image(file)
                result.images.append(image)
                result.image_names.append(file.name)
                if not recognized:
                    continue
                content = "[Recognized image text · OCR can contain transcription errors]\n" + recognized[:per_file]
            elif file.suffix == ".pdf":
                content = document_ocr.pdf_text(file.data, per_file, editing=file is editing)
            elif file.suffix in {".docx", ".xlsx"}:
                content = document_ocr.office_text(file.data, file.suffix, per_file)
            elif file.suffix == ".zip":
                members = _zip_members(file)
                if file is editing:
                    result.original_zip = members
                # Mentioned paths come first. Keep binary files in the export but out of the model context.
                listed = sorted(members, key=lambda name: (name.casefold() not in message.casefold(), name))
                content = "ZIP members: " + json.dumps(listed, ensure_ascii=False)
                remaining = max(0, per_file - len(content))
                for name in listed:
                    path = Path(name)
                    if path.name.lower() in {".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"} or path.suffix in {".pem", ".key"} or ".git" in path.parts:
                        continue
                    text = _text(members[name])
                    if text is None:
                        continue
                    header = f"\n\nZIP source path {json.dumps(name, ensure_ascii=False)}\n"
                    if remaining <= len(header) + 60:
                        break
                    if len(text) <= remaining - len(header):
                        content += header + text
                        remaining -= len(header) + len(text)
                        if file is editing:
                            result.editable_paths.add(name)
                    elif remaining > 500:
                        excerpt = text[:min(1200, remaining - len(header) - 60)]
                        content += header + excerpt + "\n[Excerpt only. Do not replace this member.]"
                        remaining -= len(header) + len(excerpt) + 50
            else:
                text = _text(file.data)
                if text is None:
                    raise ValueError("Use a ZIP, PDF, DOCX, XLSX, image, or UTF-8 text/code file.")
                content = text[:per_file] + ("\n[Text excerpt ends here.]" if len(text) > per_file else "")
            blocks.append(f"Attachment {json.dumps(file.name, ensure_ascii=False)}\n{content}")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "PODA could not read this file."
            result.warnings.append(f"{file.name}: {reason}")
    result.context = "\n\n".join(blocks)
    return result


def finalize_archive(source: str, original: dict[str, bytes] | None, editable: set[str], single_click: bool) -> bytes:
    updates = sandbox_files.archive_source_entries(source)
    members = dict(original or {})
    original_names = {name.casefold(): name for name in members}
    for name, content in updates:
        existing = original_names.get(name.casefold())
        if existing is not None and existing not in editable:
            raise ValueError(f"Only fully read ZIP members can be edited. Attach {existing} separately or supply a smaller ZIP.")
        members[existing or name] = content
    if single_click and "index.html" in members:
        if not any(name.lower().endswith(".command") for name in members):
            members["Run on Mac.command"] = b'#!/bin/bash\ncd "$(dirname "$0")" || exit 1\nopen "./index.html"\n'
        if not any(name.lower().endswith((".cmd", ".bat")) for name in members):
            members["Run on Windows.cmd"] = b'@echo off\r\nstart "" "%~dp0index.html"\r\n'
    return sandbox_files.pack_archive(list(members.items()))
