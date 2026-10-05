"""Persisted Notion connection state (no secrets). Status values: DISCONNECTED | CONNECTED | DEGRADED."""
from __future__ import annotations

import json
from typing import Any

from ...runtime import config
from ...runtime.db import connect, now_iso, one
from ...security import keychain


def get_config() -> dict[str, Any] | None:
    conn = connect()
    try:
        row = one(conn, "SELECT * FROM notion_config WHERE id=1")
    finally:
        conn.close()
    if not row:
        return None
    try:
        row["schema"] = json.loads(row.pop("schema_json") or "{}")
    except Exception:
        row["schema"] = {}
    try:
        row["related"] = json.loads(row.pop("related_json") or "{}")
    except Exception:
        row["related"] = {}
    return row


def token_present() -> bool:
    return keychain.keychain_exists(config.NOTION_KEYCHAIN_SERVICE, config.NOTION_KEYCHAIN_ACCOUNT)


def set_status(status: str, detail: str | None = None) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE notion_config SET status=?, status_detail=?, last_verified_at=? WHERE id=1", (status, detail, now_iso()))
        active = 1 if status in {"CONNECTED", "DEGRADED"} else 0
        conn.execute("UPDATE integration_state SET active=?, detail=COALESCE(?, detail), last_verified_at=? WHERE integration IN ('notion','calendar')", (active, detail, now_iso()))
        conn.commit()
    finally:
        conn.close()


def capability() -> dict[str, Any]:
    cfg = get_config()
    present = token_present()
    status = (cfg or {}).get("status") or "DISCONNECTED"
    connected = bool(cfg and present and status in {"CONNECTED", "DEGRADED"} and cfg.get("data_source_id"))
    return {
        "connected": connected,
        "status": status if (cfg and present) else "DISCONNECTED",
        "status_detail": (cfg or {}).get("status_detail") if cfg else "No Notion database is connected.",
        "database_id": (cfg or {}).get("database_id"),
        "database_title": (cfg or {}).get("database_title"),
        "data_source_id": (cfg or {}).get("data_source_id"),
        "data_source_name": (cfg or {}).get("data_source_name"),
        "workspace_name": (cfg or {}).get("workspace_name"),
        "token_storage": "macOS Keychain" if present else None,
        "can_read_content": bool(connected and (cfg or {}).get("read_verified")),
        "can_insert_content": bool(connected and (cfg or {}).get("insert_verified")),
        "can_update_content": bool(connected and (cfg or {}).get("update_verified")),
        "mapping_active": bool(cfg and _active_mapping_exists((cfg or {}).get("data_source_id"))),
        "api_version": config.NOTION_VERSION,
        "schema_fingerprint": (cfg or {}).get("schema_fingerprint"),
    }


def _active_mapping_exists(data_source_id: str | None) -> bool:
    if not data_source_id:
        return False
    conn = connect()
    try:
        return bool(one(conn, "SELECT id FROM notion_mappings WHERE data_source_id=? AND active=1 LIMIT 1", (data_source_id,)))
    finally:
        conn.close()
