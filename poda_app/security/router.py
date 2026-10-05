"""Privacy & Permissions endpoints: encryption status, egress audit, offline mode, backups, export."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..runtime import config
from ..runtime.db import connect, encryption_status, export_plaintext_copy, set_setting, rows, now_iso
from ..agent.grants import list_grants
from ..connectors.notion import state as notion_state
from . import egress
from .crypto import create_encrypted_backup, list_backups, restore_encrypted_backup

router = APIRouter()


@router.get("/privacy/status")
def privacy_status() -> dict[str, Any]:
    conn = connect()
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("messages", "memory_nodes", "memory_embeddings", "action_receipts", "egress_log")}
    finally:
        conn.close()
    return {
        "encryption": encryption_status(),
        "data_dir": str(config.DATA_DIR),
        "data_dir_mode": oct(Path(config.DATA_DIR).stat().st_mode & 0o777) if Path(config.DATA_DIR).exists() else None,
        "counts": counts,
        "connections": {"notion": notion_state.capability()},
        "file_grants": list_grants(),
        "egress": egress.egress_summary(),
        "backups": list_backups(),
        "threat_model": {
            "protected": ["remote services (no cloud models, telemetry, CDNs, or analytics)", "casual file inspection of the database (SQLCipher AES-256 when enabled)",
                           "cross-site web pages driving the API (origin + session cookie checks)", "accidental token leakage (Keychain only, redacted logs)"],
            "not_protected": ["an administrator or privileged malware on this Mac", "a compromised process running as the same macOS user", "screen capture or browser extensions observing rendered content",
                              "OS-level backups of the Keychain itself"],
        },
    }


@router.get("/privacy/egress")
def privacy_egress(limit: int = 100) -> dict[str, Any]:
    return {"recent": egress.recent_egress(limit), "summary": egress.egress_summary()}


class OfflineRequest(BaseModel):
    enabled: bool


@router.post("/privacy/offline")
def privacy_offline(req: OfflineRequest) -> dict[str, Any]:
    set_setting("offline_mode", "1" if req.enabled else "0")
    return {"offline_mode": req.enabled}


@router.post("/privacy/backup")
def privacy_backup() -> dict[str, Any]:
    try:
        return create_encrypted_backup()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class RestoreRequest(BaseModel):
    filename: str
    confirm: bool = False


@router.post("/privacy/restore")
def privacy_restore(req: RestoreRequest) -> dict[str, Any]:
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Restore replaces the live database. Re-send with confirm=true after reading the backup details.")
    try:
        return restore_encrypted_backup(req.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/privacy/export")
def privacy_export() -> dict[str, Any]:
    """Write a plaintext copy for the user's own inspection/export. Stored 0600 in the backups dir."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = config.BACKUP_DIR / f"poda-plaintext-export-{stamp}.db"
    result = export_plaintext_copy(dest)
    return {**result, "path": str(dest), "warning": "This file is NOT encrypted. Delete it when you are done."}


class RetentionRequest(BaseModel):
    delete_messages_before: str | None = None
    delete_egress_log: bool = False
    delete_receipts_before: str | None = None


@router.post("/privacy/retention")
def privacy_retention(req: RetentionRequest) -> dict[str, Any]:
    conn = connect()
    try:
        removed: dict[str, int] = {}
        if req.delete_messages_before:
            ids = [r["id"] for r in rows(conn, "SELECT id FROM messages WHERE created_at < ?", (req.delete_messages_before,))]
            removed_nodes = 0
            for mid in ids:
                # A node may own this message as the user turn (source_message_id) or the reply (response_message_id).
                node = conn.execute("SELECT id, source_message_id, response_message_id FROM memory_nodes WHERE source_message_id=? OR response_message_id=?", (mid, mid)).fetchone()
                if node:
                    conn.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?", (node[0], node[0]))
                    conn.execute("DELETE FROM memory_embeddings WHERE node_id=?", (node[0],))
                    conn.execute("DELETE FROM memory_nodes WHERE id=?", (node[0],))
                    removed_nodes += 1
                    for other in (node[1], node[2]):  # keep the pair consistent: both halves go together
                        if other and other != mid:
                            conn.execute("DELETE FROM messages WHERE id=?", (other,))
                conn.execute("DELETE FROM messages WHERE id=?", (mid,))
            removed["messages"] = len(ids)
            removed["linked_memory_nodes"] = removed_nodes
        if req.delete_egress_log:
            removed["egress_log"] = conn.execute("DELETE FROM egress_log").rowcount
        if req.delete_receipts_before:
            removed["receipts"] = conn.execute("DELETE FROM action_receipts WHERE started_at < ?", (req.delete_receipts_before,)).rowcount
        conn.commit()
        return {"removed": removed, "at": now_iso()}
    finally:
        conn.close()
