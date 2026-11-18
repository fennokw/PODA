"""Unified Project Manager on top of existing projects + calibrated workload items.

Uploaded documents are stored as bounded BLOBs in the encrypted PODA database;
linked files stay in place and require active folder grants at read time.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..runtime.db import connect, now_iso, one, rows
from ..workload import service as workload
from ..workload.router import _extract_text, _suggest
from ..agent import grants
from . import folders

router = APIRouter(prefix="/project-manager", tags=["project-manager"])
MAX_FILE_BYTES = 8 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".txt", ".md", ".csv"}


class ProjectData(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    notes: str = Field(default="", max_length=30000)
    importance: int = Field(default=5, ge=1, le=10)
    difficulty: int = Field(default=5, ge=1, le=10)
    hours_remaining: float = Field(default=5, ge=0, le=10000)
    deadline: str | None = None
    progress: int = Field(default=0, ge=0, le=100)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str | None = Field(default=None, max_length=30000)
    importance: int | None = Field(default=None, ge=1, le=10)
    difficulty: int | None = Field(default=None, ge=1, le=10)
    hours_remaining: float | None = Field(default=None, ge=0, le=10000)
    deadline: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)


class FileLink(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


def ensure_tables(db) -> None:
    workload.ensure_tables(db)
    folders.ensure_tables(db)
    db.execute("""CREATE TABLE IF NOT EXISTS pm_attachments (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('uploaded', 'linked')),
        filename TEXT NOT NULL,
        source_path TEXT,
        mime TEXT,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT,
        content_text TEXT,
        blob BLOB,
        created_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_pm_attachments_project ON pm_attachments(project_id)")


def _project(db, project_id: str) -> dict[str, Any]:
    item = one(db, "SELECT * FROM projects WHERE id=?", (project_id,))
    if not item:
        raise HTTPException(status_code=404, detail="Project not found")
    return item


def _unique(db, name: str, exclude: str | None = None) -> None:
    match = one(db, "SELECT id FROM projects WHERE lower(name)=lower(?) AND id != ?",
                (name, exclude or ""))
    if match:
        raise HTTPException(status_code=409, detail="Another project already has that name")


def _attachment_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in ("id", "project_id", "kind", "filename",
           "source_path", "mime", "size_bytes", "sha256", "created_at")}


def _stats(db, project: dict[str, Any]) -> dict[str, Any]:
    tasks = rows(db, "SELECT * FROM workload_items WHERE lower(project)=lower(?) ORDER BY deadline ASC, updated_at DESC",
                 (project["name"],))
    task_views = [{**t, "progress": 100 if t["status"] == "done" else t["progress"], "estimate": workload.estimate(t)} for t in tasks]
    open_items = [t for t in task_views if t.get("status") != "done"]
    remaining = sum(t["estimate"]["remaining_minutes"] for t in open_items)
    docs = rows(db, """SELECT id, project_id, kind, filename, source_path, mime, size_bytes,
                         sha256, created_at FROM pm_attachments WHERE project_id=? ORDER BY created_at DESC""", (project["id"],))
    return {**project, "tasks": task_views, "attachments": docs, "task_count": len(tasks),
            "open_task_count": len(open_items), "remaining_minutes": remaining,
            "completed_task_count": len(tasks)-len(open_items)}


@router.get("/projects")
def list_projects() -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        entries = rows(db, "SELECT * FROM projects ORDER BY updated_at DESC")
        return {"projects": [_stats(db, item) for item in entries]}
    finally:
        db.close()


@router.post("/projects")
def create_project(body: ProjectData) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name is required")
    db = connect()
    try:
        ensure_tables(db)
        _unique(db, name)
        pid, ts = str(uuid.uuid4()), now_iso()
        db.execute("""INSERT INTO projects (id, name, importance, difficulty, hours_remaining,
                    deadline, progress, notes, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (pid, name, body.importance, body.difficulty, body.hours_remaining,
                    body.deadline, body.progress, body.notes, ts, ts))
        db.commit()
        return _stats(db, _project(db, pid))
    finally:
        db.close()


@router.get("/projects/{project_id}")
def read_project(project_id: str) -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        return {**_stats(db, _project(db, project_id)), "chats": folders.chats(db, project_id)}
    finally:
        db.close()


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch) -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        old = _project(db, project_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes:
            changes["name"] = changes["name"].strip()
            if not changes["name"]:
                raise HTTPException(status_code=422, detail="Project name is required")
            _unique(db, changes["name"], exclude=project_id)
        if not changes:
            return _stats(db, old)
        old_name = old["name"]
        updates = {**changes, "updated_at": now_iso()}
        db.execute("UPDATE projects SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?",
                   (*updates.values(), project_id))
        if changes.get("name") and changes["name"] != old_name:
            db.execute("UPDATE workload_items SET project=?, updated_at=? WHERE lower(project)=lower(?)",
                       (changes["name"], now_iso(), old_name))
        db.commit()
        return _stats(db, _project(db, project_id))
    finally:
        db.close()


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        old = _project(db, project_id)
        db.execute("UPDATE workload_items SET project='', updated_at=? WHERE lower(project)=lower(?)", (now_iso(), old["name"]))
        db.execute("DELETE FROM pm_chat_folders WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM pm_attachments WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        db.commit()
        return {"deleted": True, "note": "Related workload tasks were preserved without a project assignment."}
    finally:
        db.close()


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=30000)
    user_minutes: int | None = Field(default=None, ge=1, le=200000)
    actual_minutes: int | None = Field(default=None, ge=1, le=200000)
    progress: int | None = Field(default=None, ge=0, le=100)
    deadline: str | None = None
    goal: str | None = Field(default=None, max_length=300)
    goal_target: int | None = Field(default=None, ge=1, le=1000000)
    goal_current: int | None = Field(default=None, ge=0, le=1000000)
    event_id: str | None = Field(default=None, max_length=300)
    calendar_scope: dict[str, str] | None = None


class NewChat(BaseModel):
    task_id: str | None = None


@router.post("/projects/{project_id}/chats")
def create_chat(project_id: str, body: NewChat):
    db = connect()
    try:
        ensure_tables(db)
        _project(db, project_id)
        if body.task_id:
            folders.get_task(db, project_id, body.task_id)
        return {"session_id": folders.new_chat(project_id, body.task_id)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        db.close()


@router.get("/projects/{project_id}/tasks/{task_id}")
def read_task(project_id: str, task_id: str):
    try:
        return folders.refresh_task(project_id, task_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/projects/{project_id}/tasks/{task_id}")
def patch_task(project_id: str, task_id: str, body: TaskPatch):
    from ..connectors.notion.client import NotionError

    try:
        data = body.model_dump(exclude_unset=True)
        for field in ("title", "progress", "goal_current"):
            if field in data and data[field] is None:
                raise ValueError(f"{field} cannot be empty.")
        if data.get("goal") is None and "goal" in data:
            data["goal"] = ""
        return folders.update_task(project_id, task_id, data)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, NotionError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/tasks/{task_id}/retry-calendar")
def retry_task_calendar(project_id: str, task_id: str):
    try:
        return folders.refresh_task(project_id, task_id, retry=True)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/calendar-events")
def linkable_events(start: str, end: str):
    from ..connectors.calendar import service as calendar
    from ..connectors.notion.client import NotionError

    try:
        with calendar._lock:
            result = calendar.events_between(start, end)
            return {"scope": calendar.binding(),
                    "events": [event for event in result["events"] if not event.get("is_deadline")]}
    except (ValueError, NotionError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/attachments")
async def upload_attachment(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "unnamed").name
    if Path(filename).suffix.lower() not in ALLOWED:
        raise HTTPException(status_code=422, detail="Supported files: PDF, DOCX, TXT, MD, CSV")
    blob = await file.read(MAX_FILE_BYTES + 1)
    await file.close()
    if not blob or len(blob) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Document must be 1 byte to 8 MB")
    try:
        extracted = _extract_text(filename, blob)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db = connect()
    try:
        ensure_tables(db)
        _project(db, project_id)
        aid = str(uuid.uuid4())
        db.execute("""INSERT INTO pm_attachments
            (id, project_id, kind, filename, source_path, mime, size_bytes,
             sha256, content_text, blob, created_at)
             VALUES (?, ?, 'uploaded', ?, NULL, ?, ?, ?, ?, ?, ?)""",
           (aid, project_id, filename, file.content_type, len(blob),
            hashlib.sha256(blob).hexdigest(), extracted[:100000], blob, now_iso()))
        db.commit()
        return {"attachment": _attachment_row(one(db, """SELECT id,project_id,kind,filename,source_path,
            mime,size_bytes,sha256,created_at FROM pm_attachments WHERE id=?""", (aid,))),
            "extracted_characters": len(extracted), "suggested_tasks": _suggest(extracted, filename)[:15]}
    finally:
        db.close()


@router.post("/projects/{project_id}/links")
def link_file(project_id: str, body: FileLink) -> dict[str, Any]:
    try:
        file_path, grant = grants.resolve(body.path, "read")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not file_path.is_file():
        raise HTTPException(status_code=422, detail="Linked path must be a readable file in a granted folder")
    if file_path.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Linked files are limited to 8 MB")
    db = connect()
    try:
        ensure_tables(db)
        _project(db, project_id)
        aid = str(uuid.uuid4())
        db.execute("""INSERT INTO pm_attachments (id,project_id,kind,filename,source_path,mime,
                   size_bytes,sha256,content_text,blob,created_at)
                   VALUES (?,?,'linked',?,?,NULL,?,NULL,NULL,NULL,?)""",
                   (aid,project_id,file_path.name,str(file_path),file_path.stat().st_size,now_iso()))
        db.commit()
        return {"attachment": _attachment_row(one(db, """SELECT id,project_id,kind,filename,source_path,
            mime,size_bytes,sha256,created_at FROM pm_attachments WHERE id=?""",(aid,))),
                "note": "File remains on this computer and can only be opened while the grant remains active."}
    finally:
        db.close()


def _attachment(db, project_id: str, aid: str) -> dict[str, Any]:
    _project(db, project_id)
    row = one(db, "SELECT * FROM pm_attachments WHERE id=? AND project_id=?", (aid, project_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return row


@router.get("/projects/{project_id}/attachments/{attachment_id}/text")
def attachment_text(project_id: str, attachment_id: str) -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        row = _attachment(db, project_id, attachment_id)
    finally:
        db.close()
    if row["kind"] == "linked":
        try:
            path, _ = grants.resolve(row["source_path"], "read")
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                raise HTTPException(status_code=422, detail="Linked file missing or too large")
            extracted = _extract_text(row["filename"], path.read_bytes())
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        extracted = row.get("content_text") or ""
    return {"filename": row["filename"], "text": extracted[:100000], "untrusted": True}


@router.get("/projects/{project_id}/attachments/{attachment_id}/download")
def download_attachment(project_id: str, attachment_id: str):
    db = connect()
    try:
        ensure_tables(db)
        row = _attachment(db, project_id, attachment_id)
    finally:
        db.close()
    if row["kind"] == "linked":
        try:
            path, _ = grants.resolve(row["source_path"], "read")
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                raise HTTPException(status_code=422, detail="Linked file missing or too large")
            data = path.read_bytes()
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    else:
        data = row.get("blob") or b""
    # All responses remain within the local loopback interface.
    safe_name = row["filename"].replace('"', "").replace("\r", "").replace("\n", "")[:160]
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/projects/{project_id}/attachments/{attachment_id}")
def unlink_attachment(project_id: str, attachment_id: str) -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        row = _attachment(db, project_id, attachment_id)
        db.execute("DELETE FROM pm_attachments WHERE id=?", (row["id"],))
        db.commit()
        return {"removed": True, "source_file_untouched": True}
    finally:
        db.close()
