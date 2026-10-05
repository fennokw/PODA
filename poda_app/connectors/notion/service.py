"""Connection lifecycle: connect to a verified data source, verify read/write, query, disconnect."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ...runtime import config
from ...runtime.db import connect, now_iso, one, rows
from ...security import keychain
from . import state
from .client import NotionClient, NotionError, identity_summary, rich_plain


# ---------------------------------------------------------------------------------- pages
def property_value(prop: dict[str, Any]) -> Any:
    ptype = prop.get("type")
    value = prop.get(ptype) if ptype else None
    if ptype in {"title", "rich_text"}:
        return rich_plain(value)
    if ptype in {"select", "status"}:
        return (value or {}).get("name") if isinstance(value, dict) else None
    if ptype == "multi_select":
        return [i.get("name") for i in (value or []) if isinstance(i, dict) and i.get("name")]
    if ptype == "date":
        if not isinstance(value, dict) or not value:
            return None
        return {"start": value.get("start"), "end": value.get("end"), "time_zone": value.get("time_zone")}
    if ptype in {"number", "checkbox", "url", "email", "phone_number", "created_time", "last_edited_time"}:
        return value
    if ptype == "relation":
        return [i.get("id") for i in (value or []) if isinstance(i, dict) and i.get("id")]
    if ptype == "people":
        return [i.get("name") or i.get("id") for i in (value or []) if isinstance(i, dict)]
    if ptype == "formula" and isinstance(value, dict):
        ft = value.get("type")
        return value.get(ft) if ft else value
    if ptype == "rollup" and isinstance(value, dict):
        rt = value.get("type")
        rv = value.get(rt) if rt else value
        return rv if not isinstance(rv, list) else rv[:20]
    return value


def normalize_page(page: dict[str, Any]) -> dict[str, Any]:
    props = page.get("properties") or {}
    values = {name: property_value(p) for name, p in props.items() if isinstance(p, dict)}
    title = ""
    for p in props.values():
        if isinstance(p, dict) and p.get("type") == "title":
            title = rich_plain(p.get("title"))
            if title:
                break
    parent = page.get("parent") or {}
    return {"id": page.get("id"), "url": page.get("url"), "title": title or "Untitled", "properties": values,
            "last_edited_time": page.get("last_edited_time"), "created_time": page.get("created_time"),
            "in_trash": bool(page.get("in_trash") or page.get("archived")), "data_source_id": parent.get("data_source_id")}


def schema_fingerprint(properties: dict[str, Any]) -> str:
    parts = sorted(f"{name}:{(p or {}).get('type')}:{(p or {}).get('id')}" for name, p in (properties or {}).items())
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def schema_summary(properties: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for name, p in (properties or {}).items():
        if not isinstance(p, dict):
            continue
        entry: dict[str, Any] = {"id": p.get("id"), "type": p.get("type")}
        cfg = p.get(p.get("type") or "") or {}
        if p.get("type") == "relation" and isinstance(cfg, dict):
            entry["relation_data_source_id"] = cfg.get("data_source_id")
            entry["relation_database_id"] = cfg.get("database_id")
        if p.get("type") in {"select", "multi_select", "status"} and isinstance(cfg, dict):
            entry["options"] = [o.get("name") for o in cfg.get("options") or [] if isinstance(o, dict)]
            if p.get("type") == "status":
                entry["groups"] = [{"name": g.get("name"), "options": g.get("option_ids")} for g in cfg.get("groups") or []]
        out[name] = entry
    return out


def inspect_relations(client: NotionClient, properties: dict[str, Any]) -> list[dict[str, Any]]:
    """For each relation property, check whether PODA can actually read the related data source."""
    out = []
    for name, p in (properties or {}).items():
        if not isinstance(p, dict) or p.get("type") != "relation":
            continue
        cfg = p.get("relation") or {}
        ds_id = cfg.get("data_source_id")
        db_id = cfg.get("database_id")
        entry = {"property": name, "data_source_id": ds_id, "database_id": db_id, "accessible": False, "title": None, "error": None}
        try:
            if ds_id:
                entry["title"] = rich_plain(client.data_source(ds_id).get("title")) or "Untitled"
                entry["accessible"] = True
            elif db_id:
                entry["title"] = rich_plain(client.database(db_id).get("title")) or "Untitled"
                entry["accessible"] = True
            else:
                entry["error"] = "relation has no target id"
        except NotionError as exc:
            entry["error"] = f"{exc.kind}: {exc.message}"
        out.append(entry)
    return out


# ---------------------------------------------------------------------------------- connect
def connect_data_source(data_source_id: str, database_id: str | None = None) -> dict[str, Any]:
    client = NotionClient()
    me = identity_summary(client.me())
    ds = client.data_source(data_source_id)
    if ds.get("object") != "data_source":
        raise NotionError("validation", f"{data_source_id} is not a data source (object={ds.get('object')}).")
    parent = ds.get("parent") or {}
    resolved_db_id = parent.get("database_id") or database_id
    if not resolved_db_id:
        raise NotionError("validation", "The data source did not report a parent database.")
    database = client.database(resolved_db_id)
    sources = database.get("data_sources") or []
    if sources and data_source_id.replace("-", "") not in {str(s.get("id", "")).replace("-", "") for s in sources}:
        raise NotionError("validation", "The selected data source is not a child of that database.")
    properties = ds.get("properties") or {}
    fingerprint = schema_fingerprint(properties)
    related = inspect_relations(client, properties)
    first = client.query(data_source_id, {"page_size": 1})
    if "results" not in first or "has_more" not in first:
        raise NotionError("validation", "Query response lacked pagination fields; read is not verified.")
    database_title = rich_plain(database.get("title")) or "Notion Database"
    source_name = rich_plain(ds.get("title")) or next((str(s.get("name")) for s in sources if str(s.get("id", "")).replace("-", "") == data_source_id.replace("-", "")), "Data source")
    unshared = [r for r in related if not r["accessible"]]
    status_value = "DEGRADED" if unshared else "CONNECTED"
    detail = (f"Connected to '{database_title}' / '{source_name}' in workspace '{me.get('workspace_name') or me.get('workspace_id')}'. Read verified ({len(first.get('results') or [])} sample page). "
              "Write access not yet verified. "
              + (f"Relation(s) not readable by PODA: {', '.join(r['property'] for r in unshared)} — share their original databases with PODA for complete relation handling." if unshared else "All relation targets are readable."))
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO notion_config (id, database_id, database_title, data_source_id, data_source_name, schema_json, notion_version, read_verified, insert_verified, update_verified,
                                          last_verified_at, last_success_at, workspace_id, workspace_name, bot_id, schema_fingerprint, status, status_detail, related_json)
               VALUES (1, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET database_id=excluded.database_id, database_title=excluded.database_title, data_source_id=excluded.data_source_id,
                 data_source_name=excluded.data_source_name, schema_json=excluded.schema_json, notion_version=excluded.notion_version, read_verified=1, insert_verified=0, update_verified=0,
                 last_verified_at=excluded.last_verified_at, last_success_at=excluded.last_success_at, workspace_id=excluded.workspace_id, workspace_name=excluded.workspace_name,
                 bot_id=excluded.bot_id, schema_fingerprint=excluded.schema_fingerprint, status=excluded.status, status_detail=excluded.status_detail, related_json=excluded.related_json""",
            (resolved_db_id, database_title, data_source_id, source_name, json.dumps(properties), config.NOTION_VERSION, now_iso(), now_iso(),
             me.get("workspace_id"), me.get("workspace_name"), me.get("bot_id"), fingerprint, status_value, detail, json.dumps(related)),
        )
        conn.execute("""INSERT INTO integration_state (integration, active, mode, detail, account_identifier, last_verified_at, last_success_at) VALUES ('notion', 1, 'notion_data_source_connected', ?, ?, ?, ?)
                        ON CONFLICT(integration) DO UPDATE SET active=1, mode=excluded.mode, detail=excluded.detail, account_identifier=excluded.account_identifier, last_verified_at=excluded.last_verified_at, last_success_at=excluded.last_success_at""",
                     (detail, database_title, now_iso(), now_iso()))
        conn.execute("UPDATE integration_state SET active=1, mode='notion_database', detail=?, account_identifier=?, last_verified_at=?, last_success_at=? WHERE integration='calendar'",
                     (detail, database_title, now_iso(), now_iso()))
        conn.commit()
    finally:
        conn.close()
    return {"connected": True, "status": status_value, "status_detail": detail, "database_id": resolved_db_id, "database_title": database_title,
            "data_source_id": data_source_id, "data_source_name": source_name, "workspace_id": me.get("workspace_id"), "workspace_name": me.get("workspace_name"),
            "property_count": len(properties), "properties": schema_summary(properties), "related": related, "schema_fingerprint": fingerprint,
            "read_verified": True, "insert_verified": False, "update_verified": False, "api_version": config.NOTION_VERSION, "token_storage": "macOS Keychain"}


def status(verify: bool = False) -> dict[str, Any]:
    cfg = state.get_config()
    present = state.token_present()
    result: dict[str, Any] = {"token_present": present, "config": cfg, "api_version": config.NOTION_VERSION,
                              "status": (cfg or {}).get("status") or "DISCONNECTED", "status_detail": (cfg or {}).get("status_detail")}
    if not cfg or not present or not cfg.get("data_source_id"):
        result["status"] = "DISCONNECTED"
        result["status_detail"] = result.get("status_detail") or ("Token stored but no data source selected." if present else "No Notion token stored.")
        result["capability"] = state.capability()
        return result
    if verify:
        try:
            client = NotionClient()
            me = identity_summary(client.me())
            if cfg.get("workspace_id") and me.get("workspace_id") and me["workspace_id"] != cfg["workspace_id"]:
                state.set_status("DISCONNECTED", f"The stored token now belongs to workspace '{me.get('workspace_name')}', not the one this connection was bound to.")
                result.update(status="DISCONNECTED", status_detail="workspace mismatch", verified=False, capability=state.capability())
                return result
            ds = client.data_source(cfg["data_source_id"])
            props = ds.get("properties") or {}
            fp = schema_fingerprint(props)
            related = inspect_relations(client, props)
            unshared = [r for r in related if not r["accessible"]]
            changed = bool(cfg.get("schema_fingerprint")) and fp != cfg["schema_fingerprint"]
            new_status = "DEGRADED" if (changed or unshared) else "CONNECTED"
            detail = ("Schema changed since connection; review the Schema Mapper before writing. " if changed else "Live schema matches the connected fingerprint. ") + \
                     (f"Unreadable relation targets: {', '.join(r['property'] for r in unshared)}." if unshared else "")
            conn = connect()
            try:
                conn.execute("UPDATE notion_config SET schema_json=?, read_verified=1, last_verified_at=?, last_success_at=?, status=?, status_detail=?, related_json=? WHERE id=1",
                             (json.dumps(props), now_iso(), now_iso(), new_status, detail.strip(), json.dumps(related)))
                conn.execute("UPDATE integration_state SET active=1, last_verified_at=?, detail=? WHERE integration IN ('notion','calendar')", (now_iso(), detail.strip()))
                conn.commit()
            finally:
                conn.close()
            result.update(verified=True, status=new_status, status_detail=detail.strip(), schema_changed=changed, properties=schema_summary(props), related=related,
                          schema_fingerprint=fp, workspace_name=me.get("workspace_name"))
        except NotionError as exc:
            if exc.kind in {"invalid_token", "not_found", "restricted"}:
                state.set_status("DISCONNECTED", f"Verification failed: {exc.message}")
                result.update(status="DISCONNECTED")
            else:
                state.set_status("DEGRADED", f"Verification could not complete: {exc.message}")
                result.update(status="DEGRADED")
            result.update(verified=False, error=exc.to_dict(), status_detail=exc.message)
    result["capability"] = state.capability()
    return result


def _title_property(schema: dict[str, Any]) -> str | None:
    return next((n for n, p in (schema or {}).items() if isinstance(p, dict) and p.get("type") == "title"), None)


def _ensure_test_pages_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS notion_test_pages (page_id TEXT PRIMARY KEY, data_source_id TEXT, title TEXT, created_at TEXT NOT NULL, archived INTEGER DEFAULT 0, note TEXT)")


def _record_test_page(page_id: str, data_source_id: str, title: str) -> None:
    conn = connect()
    try:
        _ensure_test_pages_table(conn)
        conn.execute("INSERT OR REPLACE INTO notion_test_pages (page_id, data_source_id, title, created_at, archived, note) VALUES (?,?,?,?,0,?)",
                     (page_id, data_source_id, title, now_iso(), "created by verify-write"))
        conn.commit()
    finally:
        conn.close()


def _mark_test_page_archived(page_id: str) -> None:
    conn = connect()
    try:
        _ensure_test_pages_table(conn)
        conn.execute("UPDATE notion_test_pages SET archived=1, note='archived by verify-write' WHERE page_id=?", (page_id,))
        conn.commit()
    finally:
        conn.close()


def leftover_test_pages() -> list[dict[str, Any]]:
    conn = connect()
    try:
        _ensure_test_pages_table(conn)
        return rows(conn, "SELECT * FROM notion_test_pages WHERE archived=0 ORDER BY created_at DESC")
    finally:
        conn.close()


def all_test_pages() -> list[dict[str, Any]]:
    conn = connect()
    try:
        _ensure_test_pages_table(conn)
        return rows(conn, "SELECT * FROM notion_test_pages ORDER BY created_at DESC")
    finally:
        conn.close()


def verify_write() -> dict[str, Any]:
    cfg = state.get_config()
    if not cfg or not cfg.get("data_source_id"):
        raise NotionError("validation", "No Notion data source is connected.")
    title_name = _title_property(cfg.get("schema") or {})
    if not title_name:
        raise NotionError("validation", "The connected data source has no title property, so PODA cannot safely perform the write test.")
    client = NotionClient()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    title = f"PODA WRITE ACCESS TEST {stamp}"
    created_id = None
    insert_ok = update_ok = reread_ok = archived_ok = False
    error: str | None = None
    try:
        created = client.create_page({"parent": {"type": "data_source_id", "data_source_id": cfg["data_source_id"]},
                                      "properties": {title_name: {"title": [{"type": "text", "text": {"content": title}}]}}})
        created_id = created.get("id")
        if not created_id:
            raise NotionError("server", "Notion returned success but no page ID.")
        insert_ok = True
        _record_test_page(created_id, cfg["data_source_id"], title)
        reread = client.page(created_id)
        reread_ok = normalize_page(reread)["title"] == title
        client.update_page(created_id, {"in_trash": True})
        update_ok = True
        confirm = client.page(created_id)
        archived_ok = bool(confirm.get("in_trash") or confirm.get("archived"))
        if archived_ok:
            _mark_test_page_archived(created_id)
    except NotionError as exc:
        error = f"{exc.kind}: {exc.message}"
        if created_id and not update_ok:
            try:
                client.update_page(created_id, {"in_trash": True})
                update_ok = archived_ok = True
                _mark_test_page_archived(created_id)
                error += " (test page archived on retry)"
            except NotionError:
                pass
    leftovers = leftover_test_pages()
    write_ok = insert_ok and update_ok
    conn = connect()
    try:
        conn.execute("UPDATE notion_config SET insert_verified=?, update_verified=?, last_verified_at=?, last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END WHERE id=1",
                     (1 if insert_ok else 0, 1 if update_ok else 0, now_iso(), 1 if write_ok else 0, now_iso()))
        if write_ok:
            conn.execute("UPDATE integration_state SET active=1, detail=?, last_verified_at=?, last_success_at=? WHERE integration IN ('notion','calendar')",
                         (f"Notion read/insert/update verified for {cfg.get('database_title')}; test page archived.", now_iso(), now_iso()))
        conn.commit()
    finally:
        conn.close()
    if error and not write_ok:
        raise NotionError("validation", f"Write verification failed: {error}." + (f" A page titled '{title}' (id {created_id}) may remain in Notion; remove it manually." if created_id and not archived_ok else ""), code="write_verification_failed")
    return {"write_verified": write_ok and archived_ok, "insert_verified": insert_ok, "update_verified": update_ok, "reread_verified": reread_ok,
            "test_page_id": created_id, "test_page_archived": archived_ok, "test_page_title": title, "leftover_test_pages": leftovers, "error": error}


def query_pages(limit: int = 100, filter_obj: dict[str, Any] | None = None, sorts: list[dict[str, Any]] | None = None, client: NotionClient | None = None) -> list[dict[str, Any]]:
    cfg = state.get_config()
    if not cfg or not cfg.get("data_source_id"):
        raise NotionError("validation", "No Notion data source is connected.")
    client = client or NotionClient()
    pages: list[dict[str, Any]] = []
    cursor = None
    remaining = max(1, min(int(limit), 1000))
    while remaining > 0:
        payload: dict[str, Any] = {"page_size": min(100, remaining)}
        if filter_obj:
            payload["filter"] = filter_obj
        if sorts:
            payload["sorts"] = sorts
        if cursor:
            payload["start_cursor"] = cursor
        data = client.query(cfg["data_source_id"], payload)
        batch = data.get("results") or []
        pages.extend(normalize_page(p) for p in batch if isinstance(p, dict) and p.get("object") == "page")
        remaining -= len(batch)
        if not data.get("has_more") or not data.get("next_cursor") or not batch:
            break
        cursor = data.get("next_cursor")
    _touch_success()
    return pages


def _touch_success() -> None:
    conn = connect()
    try:
        conn.execute("UPDATE integration_state SET last_success_at=?, last_verified_at=? WHERE integration IN ('notion','calendar')", (now_iso(), now_iso()))
        conn.execute("UPDATE notion_config SET last_success_at=? WHERE id=1", (now_iso(),))
        conn.commit()
    finally:
        conn.close()


def disconnect() -> dict[str, Any]:
    deleted = keychain.keychain_delete(config.NOTION_KEYCHAIN_SERVICE, config.NOTION_KEYCHAIN_ACCOUNT)
    conn = connect()
    try:
        conn.execute("DELETE FROM notion_config WHERE id=1")
        conn.execute("UPDATE notion_mappings SET active=0")
        conn.execute("UPDATE integration_state SET active=0, mode='not_connected', detail='No Notion database is connected.', account_identifier=NULL, last_verified_at=? WHERE integration='notion'", (now_iso(),))
        conn.execute("UPDATE integration_state SET active=0, mode='extract_only', detail='No Notion calendar database is connected. PODA can extract event candidates from text.', account_identifier=NULL, last_verified_at=? WHERE integration='calendar'", (now_iso(),))
        conn.commit()
    finally:
        conn.close()
    return {"connected": False, "token_deleted": deleted, "status": "DISCONNECTED"}


def delete_token() -> dict[str, Any]:
    deleted = keychain.keychain_delete(config.NOTION_KEYCHAIN_SERVICE, config.NOTION_KEYCHAIN_ACCOUNT)
    if state.get_config():
        state.set_status("DISCONNECTED", "Token removed from Keychain.")
    return {"token_deleted": deleted, "status": "DISCONNECTED"}
