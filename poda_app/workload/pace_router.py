"""Standalone, local work-speed personalization examples.

Observations contribute to the same category/difficulty-weighted calibration
used by Workload. Examples are NOT disguised as incomplete project tasks.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from ..runtime.db import connect, rows, one, now_iso
from . import service
from .router import _extract_text, MAX_FILE_BYTES

router = APIRouter(prefix="/workload/pace", tags=["personalization"])


def ensure_tables(conn) -> None:
    service.ensure_tables(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS workload_pace_examples (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
        category TEXT NOT NULL, difficulty INTEGER NOT NULL,
        baseline_minutes INTEGER NOT NULL, user_minutes INTEGER NOT NULL,
        actual_minutes INTEGER, filename TEXT, source_text TEXT NOT NULL,
        blob BLOB, calibration_user_id TEXT NOT NULL,
        calibration_actual_id TEXT, created_at TEXT NOT NULL)""")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"blob", "source_text"}}


@router.get("")
def get_pace() -> dict[str, Any]:
    db = connect()
    try:
        ensure_tables(db)
        examples = rows(db, "SELECT * FROM workload_pace_examples ORDER BY created_at DESC")
    finally:
        db.close()
    categories = {cat: {str(d): service.calibrate(cat, d) for d in (3, 5, 8)}
                  for cat in sorted(service.CATEGORIES)}
    return {"examples": [_public(r) for r in examples], "calibration": categories,
            "preferences": service.preferences(),
            "note": "Predictions are estimates, calibrated from user and actual times weighted by task category and difficulty."}


@router.post("/examples")
async def create_example(title: str = Form(...), category: str = Form("general"),
                         difficulty: int = Form(5), user_minutes: int = Form(...),
                         actual_minutes: int | None = Form(None),
                         baseline_minutes: int | None = Form(None),
                         description: str = Form(""), file: UploadFile | None = File(None)) -> dict[str, Any]:
    clean = service.normalize({"title":title,"description":description,
        "category":category,"difficulty":difficulty,
        "user_minutes":user_minutes, "actual_minutes":actual_minutes,
        "baseline_minutes":baseline_minutes})
    raw = b""
    filename = None
    source_text = ""
    if file and file.filename:
        filename = file.filename.replace("\\", "/").split("/")[-1]
        raw = await file.read(MAX_FILE_BYTES + 1)
        await file.close()
        if len(raw)>MAX_FILE_BYTES:
            raise HTTPException(status_code=413,detail="Document exceeds 8 MB")
        try:
            source_text = _extract_text(filename, raw)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
    base = clean["baseline_minutes"] or service.heuristic_base(clean)
    example_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    actual_id = str(uuid.uuid4()) if clean["actual_minutes"] else None
    stamp = now_iso()
    db = connect()
    try:
        ensure_tables(db)
        db.execute("""INSERT INTO workload_pace_examples
          (id,title,description,category,difficulty,baseline_minutes,user_minutes,
           actual_minutes,filename,source_text,blob,calibration_user_id,calibration_actual_id,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
           (example_id,clean["title"],clean["description"],clean["category"],clean["difficulty"],
            base,clean["user_minutes"],clean["actual_minutes"],filename,source_text[:100000],
            raw if raw else None,user_id,actual_id,stamp))
        db.execute("INSERT INTO workload_calibration VALUES (?,?,?,?,?,?,?,?)",
                   (user_id,None,clean["category"],clean["difficulty"],base,clean["user_minutes"],"user",stamp))
        if actual_id:
            db.execute("INSERT INTO workload_calibration VALUES (?,?,?,?,?,?,?,?)",
                       (actual_id,None,clean["category"],clean["difficulty"],base,clean["actual_minutes"],"actual",stamp))
        db.commit()
        record=one(db,"SELECT * FROM workload_pace_examples WHERE id=?",(example_id,))
    finally:
        db.close()
    return {"example":_public(record), "calibration": service.calibrate(clean["category"],clean["difficulty"]),
            "file_extracted_characters":len(source_text)}


@router.delete("/examples/{example_id}")
def remove_example(example_id: str) -> dict[str, bool]:
    db=connect()
    try:
        ensure_tables(db)
        row=one(db,"SELECT * FROM workload_pace_examples WHERE id=?",(example_id,))
        if not row:raise HTTPException(status_code=404,detail="Pacing example not found")
        for k in ("calibration_user_id","calibration_actual_id"):
            if row.get(k):
                db.execute("DELETE FROM workload_calibration WHERE id=?",(row[k],))
        db.execute("DELETE FROM workload_pace_examples WHERE id=?",(example_id,))
        db.commit()
        return {"deleted":True}
    finally:db.close()


@router.get("/examples/{example_id}/download")
def download_example(example_id: str):
    db=connect()
    try:
        ensure_tables(db)
        row=one(db,"SELECT * FROM workload_pace_examples WHERE id=?",(example_id,))
        if not row:raise HTTPException(status_code=404,detail="Pacing example not found")
    finally:db.close()
    if not row.get("blob"):raise HTTPException(status_code=404,detail="This example has no uploaded file")
    safe=(row.get("filename") or "document").replace('"',"").replace("\r","").replace("\n","")[:140]
    return Response(content=row["blob"],media_type="application/octet-stream",
        headers={"Content-Disposition":f'attachment; filename="{safe}"',"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
