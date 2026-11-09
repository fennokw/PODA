from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..runtime.db import connect, now_iso, rows, one
from .priority import rank

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    importance: int = 5
    difficulty: int = 5
    hours_remaining: float = 5
    deadline: str | None = None
    progress: int = 0
    notes: str = ""


@router.get("/projects")
def list_projects() -> dict[str, Any]:
    conn = connect()
    try:
        return {"projects": rows(conn, "SELECT * FROM projects ORDER BY updated_at DESC")}
    finally:
        conn.close()


@router.post("/projects")
def create_project(req: ProjectCreate) -> dict[str, Any]:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    conn = connect()
    try:
        pid = str(uuid.uuid4())
        conn.execute("INSERT INTO projects (id, name, importance, difficulty, hours_remaining, deadline, progress, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (pid, req.name.strip(), req.importance, req.difficulty, req.hours_remaining, req.deadline or None, req.progress, req.notes, now_iso(), now_iso()))
        conn.commit()
        return {"status": "created", "id": pid}
    finally:
        conn.close()


@router.put("/projects/{project_id}")
def update_project(project_id: str, req: ProjectCreate) -> dict[str, Any]:
    conn = connect()
    try:
        if not one(conn, "SELECT id FROM projects WHERE id=?", (project_id,)):
            raise HTTPException(status_code=404, detail="Project not found")
        conn.execute("UPDATE projects SET name=?, importance=?, difficulty=?, hours_remaining=?, deadline=?, progress=?, notes=?, updated_at=? WHERE id=?",
                     (req.name.strip(), req.importance, req.difficulty, req.hours_remaining, req.deadline or None, req.progress, req.notes, now_iso(), project_id))
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
        return {"status": "deleted", "removed": cur.rowcount}
    finally:
        conn.close()


@router.get("/projects/prioritize")
def prioritize() -> dict[str, Any]:
    conn = connect()
    try:
        projects = rows(conn, "SELECT * FROM projects")
    finally:
        conn.close()
    return {"ranked": rank(projects), "note": "Scores are explainable recommendations from user-entered records; nothing is scheduled or changed automatically."}
