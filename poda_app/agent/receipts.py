"""Action receipts: the only evidence PODA may cite for a completed side effect (Truth Contract)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..runtime.db import connect, now_iso, rows, one
from ..security.redact import redact_obj


def open_receipt(tool: str, target: str | None, args: dict[str, Any] | None, approved: bool = False, session_id: str | None = None) -> str:
    rid = str(uuid.uuid4())
    conn = connect()
    try:
        conn.execute("INSERT INTO action_receipts (id, tool, target, args_json, outcome, verification, approved_by_user, session_id, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (rid, tool, target, json.dumps(redact_obj(args or {}), ensure_ascii=False, default=str), "running", None, 1 if approved else 0, session_id, now_iso()))
        conn.commit()
    finally:
        conn.close()
    return rid


def close_receipt(rid: str, outcome: str, verification: str | None = None, detail: str | None = None, error: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        conn.execute("UPDATE action_receipts SET outcome=?, verification=?, detail=?, error=?, finished_at=? WHERE id=?",
                     (outcome, verification, (detail or "")[:4000], (error or "")[:2000] or None, now_iso(), rid))
        conn.commit()
        return one(conn, "SELECT * FROM action_receipts WHERE id=?", (rid,)) or {}
    finally:
        conn.close()


def list_receipts(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rows(conn, "SELECT * FROM action_receipts ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 500)),))
    finally:
        conn.close()


def get_receipt(rid: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        return one(conn, "SELECT * FROM action_receipts WHERE id=?", (rid,))
    finally:
        conn.close()


def format_receipt(r: dict[str, Any]) -> str:
    return (f"[receipt {r.get('id','')[:8]}] tool={r.get('tool')} target={r.get('target')} outcome={r.get('outcome')} "
            f"verification={r.get('verification') or 'none'} at={r.get('finished_at') or r.get('started_at')}")
