"""Workload personalization REST API. All writes are local and explicitly user initiated."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import service

router = APIRouter(prefix="/workload", tags=["workload"])
MAX_FILE_BYTES = 8 * 1024 * 1024


class WorkItem(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    project: str = ""
    category: str = "general"
    importance: int = Field(default=5, ge=1, le=10)
    difficulty: int = Field(default=5, ge=1, le=10)
    deadline: str | None = None
    baseline_minutes: int | None = Field(default=None, ge=1)
    user_minutes: int | None = Field(default=None, ge=1)
    actual_minutes: int | None = Field(default=None, ge=1)
    progress: int = Field(default=0, ge=0, le=100)
    status: str = "todo"
    source_type: str = "manual"


class Preferences(BaseModel):
    sleep_hours: int = Field(default=8, ge=4, le=12)
    meal_minutes: int = Field(default=90, ge=0, le=360)
    free_minutes: int = Field(default=120, ge=0, le=600)


@router.get("")
def list_workload() -> dict[str, Any]:
    items = service.all_items()
    return {"items": items, "total": len(items), "preferences": service.preferences()}


@router.post("")
def create_workload(item: WorkItem) -> dict[str, Any]:
    try:
        return service.save_item(item.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{item_id}")
def update_workload(item_id: str, item: WorkItem) -> dict[str, Any]:
    try:
        return service.save_item(item.model_dump(), item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{item_id}")
def remove_workload(item_id: str) -> dict[str, Any]:
    if not service.delete_item(item_id):
        raise HTTPException(status_code=404, detail="work item not found")
    return {"deleted": True, "item_id": item_id}


@router.get("/estimate")
def estimate_workload(category: str = "general", difficulty: int = 5,
                      description: str = "", title: str = "New task") -> dict[str, Any]:
    try:
        data = service.normalize({"title": title, "category": category,
                                  "difficulty": difficulty, "description": description})
        return {"estimate": service.estimate(data), "category": category, "difficulty": difficulty}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/schedule")
def proposed_schedule(days: int = 14, include_calendar: bool = True) -> dict[str, Any]:
    try:
        return service.schedule(days, include_calendar)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{item_id}/breakdown")
def task_breakdown(item_id: str) -> dict[str, Any]:
    try:
        return service.breakdown(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{item_id}/analysis")
def analyze_workload(item_id: str) -> dict[str, Any]:
    from .analyzer import analyze
    try:
        return analyze(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{item_id}")
def work_item_details(item_id: str) -> dict[str, Any]:
    result = service.get_item(item_id)
    if not result:
        raise HTTPException(status_code=404, detail="Work item not found")
    return result


@router.get("/settings/preferences")
def get_preferences() -> dict[str, Any]:
    return service.preferences()


@router.put("/settings/preferences")
def set_preferences(data: Preferences) -> dict[str, Any]:
    try:
        return service.preferences(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _extract_text(filename: str, blob: bytes) -> str:
    suffix = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if suffix in {"txt", "md", "csv"}:
        return blob.decode("utf-8-sig", errors="replace")[:100000]
    if suffix == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as doc:
                infos = doc.infolist()
                if len(infos) > 300 or sum(i.file_size for i in infos) > 12_000_000:
                    raise ValueError("DOCX contains too much expanded content")
                if any(i.filename.startswith("/") or ".." in i.filename.split("/") for i in infos):
                    raise ValueError("Unsafe DOCX archive paths")
                xml = doc.read("word/document.xml")
            root = ET.fromstring(xml)
            return "\n".join("".join(t.text or "" for t in p.iter() if t.tag.endswith("}t"))
                             for p in root.iter() if p.tag.endswith("}p"))[:100000]
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ValueError("Invalid DOCX document") from exc
    if suffix == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF extraction requires pypdf (install: pip install pypdf)") from exc
        try:
            pdf = PdfReader(io.BytesIO(blob), strict=False)
            if pdf.is_encrypted:
                raise ValueError("Password-protected PDFs must be unlocked first")
            if len(pdf.pages) > 120:
                raise ValueError("PDF exceeds 120 pages")
            return "\n".join((page.extract_text() or "") for page in pdf.pages)[:100000]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Could not extract text from this PDF") from exc
    raise ValueError("Supported formats: PDF, DOCX, TXT, Markdown, and CSV")


def _suggest(text: str, filename: str) -> list[dict[str, Any]]:
    """Extract candidate work items; never silently create tasks from uploaded content."""
    suggestions = []
    seen = set()
    lines = text.splitlines()[:1000]
    for index, line in enumerate(lines):
        clean = re.sub(r"^\s*(?:[-*•]|\[ ?\]|\d{1,3}[.)])\s*", "", line).strip()
        if not clean or len(clean) < 9 or len(clean) > 170:
            continue
        if not (re.match(r"^\s*(?:[-*•]|\[ ?\]|\d{1,3}[.)])", line)
                or re.search(r"\b(?:homework|problem|question|task|submit|assignment|chapter|review|finish|read|complete|study|todo)\b", line, re.I)):
            continue
        if clean.lower() in seen:
            continue
        seen.add(clean.lower())
        category = ("homework" if re.search("question|problem|homework|assignment|pset", clean, re.I)
                    else "study" if re.search("study|exam|quiz|review", clean, re.I) else "general")
        excerpt = "\\n".join(lines[max(0, index - 2):min(len(lines), index + 5)])[:3000]
        suggestions.append({"title": clean[:180], "description": excerpt,
                            "category": category, "importance": 5, "difficulty": 5,
                            "source_type": "uploaded_document"})
        if len(suggestions) == 35:
            break
    if not suggestions:
        suggestions = [{"title": "Review " + filename[:120], "description": text[:10000],
                        "category": "reading", "importance": 5, "difficulty": 5,
                        "source_type": "uploaded_document"}]
    return suggestions


@router.post("/intake")
async def intake(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=422, detail="File name required")
    data = await file.read(MAX_FILE_BYTES + 1)
    await file.close()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Document must be <= 8 MB")
    name = file.filename.replace("\\", "/").split("/")[-1]
    try:
        text = _extract_text(name, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="No extractable text; scanned PDFs need OCR externally before upload")
    return {"filename": name, "characters": len(text), "preview": text[:2500],
            "suggestions": _suggest(text, name),
            "note": "Private local preview; no task is saved until explicitly added. Original uploaded bytes are discarded."}
