"""Schema Mapper: versioned mapping from PODA event fields to the user's live Notion properties, with
preview, idempotent proposals, re-read-after-write commits, conflict-aware updates, and confirmed archive."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from ...agent import receipts
from ...runtime.db import connect, now_iso, one, rows
from . import state
from .client import NotionClient, NotionError
from .service import normalize_page, query_pages, schema_fingerprint

MAPPING_FIELDS = {"title": {"title"}, "date": {"date"}, "status": {"status", "select"}, "type": {"select", "multi_select", "status"},
                  "project_relation": {"relation"}, "class_relation": {"relation"}, "notes": {"rich_text"}}
EXTERNAL_ID_TYPES = {"rich_text", "number", "title", "url"}


def _schema() -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = state.get_config()
    if not cfg or not cfg.get("data_source_id"):
        raise NotionError("validation", "No Notion data source is connected.")
    return cfg, cfg.get("schema") or {}


def validate_mapping(mapping: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not mapping.get("title"):
        errors.append("A title property is required.")
    if not mapping.get("date"):
        errors.append("A date property is required (this is what Notion Calendar places on the calendar).")
    for field, allowed in MAPPING_FIELDS.items():
        name = mapping.get(field)
        if not name:
            continue
        prop = schema.get(name)
        if not isinstance(prop, dict):
            errors.append(f"{field}: property '{name}' does not exist in the live schema.")
        elif prop.get("type") not in allowed:
            errors.append(f"{field}: property '{name}' has type '{prop.get('type')}', expected one of {sorted(allowed)}.")
    for key, name in (mapping.get("external_ids") or {}).items():
        if not name:
            continue
        prop = schema.get(name)
        if not isinstance(prop, dict):
            errors.append(f"external_ids.{key}: property '{name}' does not exist.")
        elif prop.get("type") not in EXTERNAL_ID_TYPES:
            errors.append(f"external_ids.{key}: property '{name}' has type '{prop.get('type')}', expected text/number/url.")
    return errors


def get_active_mapping(data_source_id: str | None = None) -> dict[str, Any] | None:
    cfg = state.get_config()
    ds = data_source_id or (cfg or {}).get("data_source_id")
    if not ds:
        return None
    conn = connect()
    try:
        row = one(conn, "SELECT * FROM notion_mappings WHERE data_source_id=? AND active=1 ORDER BY version DESC LIMIT 1", (ds,))
    finally:
        conn.close()
    if not row:
        return None
    row["mapping"] = json.loads(row.pop("mapping_json") or "{}")
    row["stale"] = bool(cfg and cfg.get("schema_fingerprint") and row.get("schema_fingerprint") and row["schema_fingerprint"] != cfg["schema_fingerprint"])
    return row


def mapping_history() -> list[dict[str, Any]]:
    conn = connect()
    try:
        out = rows(conn, "SELECT id, version, data_source_id, schema_fingerprint, created_at, active FROM notion_mappings ORDER BY version DESC LIMIT 50")
    finally:
        conn.close()
    return out


def save_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    cfg, schema = _schema()
    clean = {k: (mapping.get(k) or None) for k in MAPPING_FIELDS}
    clean["external_ids"] = {k: v for k, v in (mapping.get("external_ids") or {}).items() if v}
    clean["status_done_values"] = [str(v) for v in (mapping.get("status_done_values") or [])]
    clean["default_status"] = mapping.get("default_status") or None
    clean["default_type"] = mapping.get("default_type") or None
    errors = validate_mapping(clean, schema)
    if errors:
        raise NotionError("validation", "Mapping rejected: " + " ".join(errors), code="mapping_invalid")
    conn = connect()
    try:
        prev = one(conn, "SELECT MAX(version) AS v FROM notion_mappings WHERE data_source_id=?", (cfg["data_source_id"],))
        version = int((prev or {}).get("v") or 0) + 1
        conn.execute("UPDATE notion_mappings SET active=0 WHERE data_source_id=?", (cfg["data_source_id"],))
        mid = str(uuid.uuid4())
        conn.execute("INSERT INTO notion_mappings (id, version, data_source_id, mapping_json, schema_fingerprint, created_at, active) VALUES (?,?,?,?,?,?,1)",
                     (mid, version, cfg["data_source_id"], json.dumps(clean), cfg.get("schema_fingerprint") or schema_fingerprint(schema), now_iso()))
        conn.commit()
    finally:
        conn.close()
    return {"saved": True, "id": mid, "version": version, "mapping": clean, "schema_fingerprint": cfg.get("schema_fingerprint")}


def interpret_page(page: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    props = page.get("properties") or {}
    date = props.get(mapping.get("date") or "") if mapping.get("date") else None
    start = (date or {}).get("start") if isinstance(date, dict) else None
    end = (date or {}).get("end") if isinstance(date, dict) else None
    ext = {k: props.get(name) for k, name in (mapping.get("external_ids") or {}).items() if name}
    status_val = props.get(mapping["status"]) if mapping.get("status") else None
    return {
        "page_id": page.get("id"), "title": props.get(mapping.get("title") or "") or page.get("title"),
        "date_start": start, "date_end": end, "all_day": bool(start) and "T" not in str(start), "time_zone": (date or {}).get("time_zone") if isinstance(date, dict) else None,
        "status": status_val, "done": status_val in set(mapping.get("status_done_values") or []) if status_val is not None else None,
        "type": props.get(mapping["type"]) if mapping.get("type") else None,
        "project_ids": props.get(mapping["project_relation"]) if mapping.get("project_relation") else None,
        "class_ids": props.get(mapping["class_relation"]) if mapping.get("class_relation") else None,
        "external_ids": ext, "last_edited_time": page.get("last_edited_time"), "in_trash": page.get("in_trash"), "url": page.get("url"),
    }


def preview(n: int = 5) -> dict[str, Any]:
    active = get_active_mapping()
    if not active:
        raise NotionError("validation", "No active mapping. Save a mapping first.", code="mapping_missing")
    pages = query_pages(limit=max(1, min(n, 25)))
    return {"mapping_version": active["version"], "stale": active["stale"], "count": len(pages),
            "pages": [{"interpreted": interpret_page(p, active["mapping"]), "raw": p} for p in pages]}


def _text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": str(value)[:2000]}}]}


def build_properties(mapping: dict[str, Any], schema: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {mapping["title"]: {"title": [{"type": "text", "text": {"content": str(event["title"])[:2000]}}]}}
    date_obj: dict[str, Any] = {"start": event["start"]}
    if event.get("end"):
        date_obj["end"] = event["end"]
    if event.get("time_zone") and "T" in str(event["start"]):
        date_obj["time_zone"] = event["time_zone"]
    props[mapping["date"]] = {"date": date_obj}
    status_value = event.get("status") or mapping.get("default_status")
    if mapping.get("status") and status_value:
        kind = (schema.get(mapping["status"]) or {}).get("type")
        props[mapping["status"]] = {kind: {"name": status_value}}
    type_value = event.get("type") or mapping.get("default_type")
    if mapping.get("type") and type_value:
        kind = (schema.get(mapping["type"]) or {}).get("type")
        props[mapping["type"]] = {"multi_select": [{"name": type_value}]} if kind == "multi_select" else {kind: {"name": type_value}}
    if mapping.get("project_relation") and event.get("project_ids"):
        props[mapping["project_relation"]] = {"relation": [{"id": i} for i in event["project_ids"]]}
    if mapping.get("class_relation") and event.get("class_ids"):
        props[mapping["class_relation"]] = {"relation": [{"id": i} for i in event["class_ids"]]}
    if mapping.get("notes") and event.get("notes"):
        props[mapping["notes"]] = _text(event["notes"])
    for key, name in (mapping.get("external_ids") or {}).items():
        value = (event.get("external_ids") or {}).get(key)
        if value is None or not name:
            continue
        kind = (schema.get(name) or {}).get("type")
        props[name] = {"number": float(value)} if kind == "number" else ({"url": str(value)} if kind == "url" else _text(value))
    return props


def _idempotency_key(event: dict[str, Any]) -> str:
    ext = json.dumps(event.get("external_ids") or {}, sort_keys=True)
    return hashlib.sha256(f"{str(event.get('title','')).strip().casefold()}|{event.get('start')}|{ext}".encode()).hexdigest()[:20]


def find_duplicates(event: dict[str, Any], mapping: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dups = []
    title = str(event.get("title", "")).strip().casefold()
    start = str(event.get("start") or "")[:10]
    ext = event.get("external_ids") or {}
    for p in pages:
        interp = interpret_page(p, mapping)
        if p.get("in_trash"):
            continue
        reasons = []
        if event.get("page_id") and p.get("id") == event["page_id"]:
            reasons.append("same Notion page id")
        for key, val in ext.items():
            if val is not None and str(interp["external_ids"].get(key) or "") == str(val):
                reasons.append(f"same {key}")
        if title and str(interp.get("title") or "").strip().casefold() == title and str(interp.get("date_start") or "")[:10] == start and start:
            reasons.append("same title and date")
        if reasons:
            dups.append({"page_id": p["id"], "title": interp.get("title"), "date_start": interp.get("date_start"), "url": p.get("url"), "reasons": reasons})
    return dups


def propose_event(event: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    cfg, schema = _schema()
    active = get_active_mapping()
    if not active:
        raise NotionError("validation", "No active mapping. Save a mapping in the Notion Schema Mapper first.", code="mapping_missing")
    if active["stale"]:
        raise NotionError("validation", "The saved mapping predates a schema change. Re-save the mapping after reviewing the live schema.", code="mapping_stale")
    if not event.get("title") or not event.get("start"):
        raise NotionError("validation", "title and start are required.")
    mapping = active["mapping"]
    payload = {"parent": {"type": "data_source_id", "data_source_id": cfg["data_source_id"]}, "properties": build_properties(mapping, schema, event)}
    key = _idempotency_key(event)
    conn = connect()
    try:
        prior = one(conn, "SELECT id, status FROM agent_proposals WHERE status='committed' AND plan_json LIKE ? ORDER BY created_at DESC LIMIT 1", (f'%"idempotency_key": "{key}"%',))
    finally:
        conn.close()
    pages = query_pages(limit=300)
    duplicates = find_duplicates(event, mapping, pages)
    if prior:
        duplicates.insert(0, {"proposal_id": prior["id"], "reasons": ["an identical event was already committed by PODA (idempotency key match)"]})
    proposal_id = str(uuid.uuid4())
    plan = {"kind": "notion_create_event", "event": event, "payload": payload, "duplicates": duplicates, "idempotency_key": key,
            "mapping_version": active["version"], "data_source_id": cfg["data_source_id"], "data_source_name": cfg.get("data_source_name"), "database_title": cfg.get("database_title")}
    conn = connect()
    try:
        conn.execute("INSERT INTO agent_proposals (id, session_id, created_at, status, plan_json) VALUES (?,?,?,?,?)", (proposal_id, session_id, now_iso(), "proposed", json.dumps(plan)))
        conn.commit()
    finally:
        conn.close()
    return {"proposal_id": proposal_id, "payload_preview": payload, "interpreted": {"title": event["title"], "start": event["start"], "end": event.get("end"), "all_day": "T" not in str(event["start"])},
            "duplicates": duplicates, "requires_confirmation": True, "target": f"{cfg.get('database_title')} / {cfg.get('data_source_name')}",
            "warning": "Duplicates detected — confirm only if you really want another page." if duplicates else None}


def _load_proposal(proposal_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        row = one(conn, "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,))
    finally:
        conn.close()
    if not row:
        raise NotionError("validation", "Unknown proposal id.", code="proposal_missing")
    row["plan"] = json.loads(row.pop("plan_json") or "{}")
    return row


def _finish_proposal(proposal_id: str, status_value: str, receipt_ids: list[str]) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE agent_proposals SET status=?, receipt_ids_json=?, decided_at=? WHERE id=?", (status_value, json.dumps(receipt_ids), now_iso(), proposal_id))
        conn.commit()
    finally:
        conn.close()


def commit_event(proposal_id: str, session_id: str | None = None) -> dict[str, Any]:
    proposal = _load_proposal(proposal_id)
    if proposal["status"] != "proposed":
        raise NotionError("validation", f"Proposal is already {proposal['status']}.", code="proposal_closed")
    plan = proposal["plan"]
    cfg = state.get_config() or {}
    if not cfg.get("insert_verified"):
        raise NotionError("validation", "Insert permission has not been verified with the explicit write test.", code="write_unverified")
    target = f"notion data source {plan.get('data_source_id')}"
    rid = receipts.open_receipt("notion.create_event", target, {"event": plan.get("event"), "proposal_id": proposal_id}, approved=True, session_id=session_id)
    try:
        client = NotionClient()
        created = client.create_page(plan["payload"])
        page_id = created.get("id")
        if not page_id:
            raise NotionError("server", "Notion returned success without a page id.")
        reread = normalize_page(client.page(page_id))
        active = get_active_mapping(plan.get("data_source_id"))
        interp = interpret_page(reread, active["mapping"]) if active else {}
        want_title = str(plan["event"]["title"]).strip()
        want_start = str(plan["event"]["start"])
        matches = (str(interp.get("title") or "").strip() == want_title) and str(interp.get("date_start") or "").startswith(want_start[:10])
        verification = "re-read-after-write" + ("" if matches else " (MISMATCH: re-read values differ from request)")
        receipt = receipts.close_receipt(rid, "success" if matches else "unverified", verification, f"Created Notion page {page_id} in {plan.get('database_title')} / {plan.get('data_source_name')}")
        _finish_proposal(proposal_id, "committed" if matches else "committed_unverified", [rid])
        return {"created": True, "verified": matches, "page_id": page_id, "url": reread.get("url"), "page": reread, "interpreted": interp, "receipt": receipt,
                "calendar_note": "API write confirmed by re-read. Visually confirm the entry appears in Notion Calendar on the mapped date property."}
    except NotionError as exc:
        receipts.close_receipt(rid, "failed", None, "Notion API write failed", f"{exc.kind}: {exc.message}")
        _finish_proposal(proposal_id, "failed", [rid])
        raise


def update_event(page_id: str, changes: dict[str, Any], expected_last_edited_time: str | None, session_id: str | None = None) -> dict[str, Any]:
    cfg, schema = _schema()
    active = get_active_mapping()
    if not active:
        raise NotionError("validation", "No active mapping.", code="mapping_missing")
    if not cfg.get("update_verified"):
        raise NotionError("validation", "Update permission has not been verified with the explicit write test.", code="write_unverified")
    client = NotionClient()
    current = normalize_page(client.page(page_id))
    if expected_last_edited_time and current.get("last_edited_time") != expected_last_edited_time:
        raise NotionError("validation", f"Conflict: the page changed in Notion at {current.get('last_edited_time')} (expected {expected_last_edited_time}). Reload before editing.", code="conflict")
    mapping = active["mapping"]
    merged = {"title": changes.get("title") or current["title"], "start": changes.get("start") or (interpret_page(current, mapping).get("date_start")),
              **{k: changes.get(k) for k in ("end", "time_zone", "status", "type", "project_ids", "class_ids", "notes", "external_ids")}}
    props = build_properties(mapping, schema, merged)
    props = {k: v for k, v in props.items() if k in {mapping.get(f) for f in changes if mapping.get(f)} or k == mapping["title"] and "title" in changes or k == mapping["date"] and ("start" in changes or "end" in changes)}
    if not props:
        raise NotionError("validation", "No mapped changes supplied.")
    rid = receipts.open_receipt("notion.update_event", f"notion page {page_id}", {"changes": changes}, approved=True, session_id=session_id)
    try:
        updated = client.update_page(page_id, {"properties": props})
        reread = normalize_page(client.page(page_id))
        receipt = receipts.close_receipt(rid, "success", "re-read-after-write", f"Updated {list(props)} on page {page_id}")
        return {"updated": True, "page": reread, "interpreted": interpret_page(reread, mapping), "receipt": receipt}
    except NotionError as exc:
        receipts.close_receipt(rid, "failed", None, "Notion API update failed", f"{exc.kind}: {exc.message}")
        raise


def archive_event(page_id: str, confirm: bool, session_id: str | None = None) -> dict[str, Any]:
    if not confirm:
        raise NotionError("validation", "Archiving requires explicit confirmation (confirm=true).", code="confirmation_required")
    cfg = state.get_config() or {}
    if not cfg.get("update_verified"):
        raise NotionError("validation", "Update permission has not been verified with the explicit write test.", code="write_unverified")
    rid = receipts.open_receipt("notion.archive_event", f"notion page {page_id}", {"confirm": True}, approved=True, session_id=session_id)
    try:
        client = NotionClient()
        client.update_page(page_id, {"in_trash": True})
        reread = client.page(page_id)
        archived = bool(reread.get("in_trash") or reread.get("archived"))
        receipt = receipts.close_receipt(rid, "success" if archived else "unverified", "re-read-after-write", f"Archived page {page_id}")
        return {"archived": archived, "page_id": page_id, "receipt": receipt}
    except NotionError as exc:
        receipts.close_receipt(rid, "failed", None, "Notion API archive failed", f"{exc.kind}: {exc.message}")
        raise


def items_between(start: str, end: str) -> list[dict[str, Any]]:
    active = get_active_mapping()
    if not active:
        raise NotionError("validation", "No active mapping; cannot filter by date without knowing the date property.", code="mapping_missing")
    prop = active["mapping"]["date"]
    filt = {"and": [{"property": prop, "date": {"on_or_after": start}}, {"property": prop, "date": {"on_or_before": end}}]}
    pages = query_pages(limit=500, filter_obj=filt, sorts=[{"property": prop, "direction": "ascending"}])
    return [interpret_page(p, active["mapping"]) for p in pages]


# ----------------------------------------------------------------------------------------------
# Inferred mapping + local-time interpretation (so schedule questions work before a mapping is saved)
# ----------------------------------------------------------------------------------------------
from datetime import datetime as _dt, timedelta as _td
from zoneinfo import ZoneInfo as _Zone

_LOCAL_TZ_NAME = "America/New_York"

DATE_NAME_PRIORITY = ["date", "due date / event date", "event date", "due date", "when", "start", "scheduled", "due"]
TITLE_NAME_PRIORITY = ["name", "title", "task", "event"]


def infer_mapping(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort mapping from the live schema. Marked inferred=True; the Schema Mapper can save/override it."""
    if not schema:
        return None
    by_type: dict[str, list[str]] = {}
    for name, prop in schema.items():
        by_type.setdefault((prop or {}).get("type") or "", []).append(name)
    titles = by_type.get("title") or []
    dates = by_type.get("date") or []
    if not titles or not dates:
        return None

    def pick(cands: list[str], priority: list[str]) -> str:
        lowered = {c.lower(): c for c in cands}
        for p in priority:
            if p in lowered:
                return lowered[p]
        for c in cands:  # avoid sync bookkeeping dates such as "Source Date" / "Source Last Seen"
            if not c.lower().startswith("source"):
                return c
        return cands[0]

    status = next((n for n in (by_type.get("status") or []) ), None) or next((n for n in (by_type.get("select") or []) if n.lower() in {"status", "state"}), None)
    type_prop = next((n for n in (by_type.get("select") or []) if n.lower() in {"type", "kind", "category"}), None)
    relations = by_type.get("relation") or []
    return {
        "inferred": True,
        "title": pick(titles, TITLE_NAME_PRIORITY),
        "date": pick(dates, DATE_NAME_PRIORITY),
        "status": status,
        "type": type_prop,
        "project_relation": next((r for r in relations if "project" in r.lower() or "domain" in r.lower()), None),
        "class_relation": next((r for r in relations if "class" in r.lower() or "course" in r.lower()), None),
        "external_ids": {k: n for k, n in {
            "canvas_assignment_id": next((n for n in schema if "canvas assignment" in n.lower()), None),
            "canvas_course_id": next((n for n in schema if "canvas course" in n.lower()), None),
            "ics_event_id": next((n for n in schema if "ics event" in n.lower() or "external event" in n.lower()), None),
        }.items() if n},
        "status_done_values": ["Done", "Complete", "Completed"],
        "context_props": [n for n in schema if n in {"Type", "Import Source", "Commitment", "Area", "Notes", "Source Name", "Source Status", "Calendar Source", "Location"}],
    }


def effective_mapping() -> dict[str, Any] | None:
    """Saved active mapping if present, else an inferred one from the stored schema."""
    active = get_active_mapping()
    if active:
        m = dict(active["mapping"]); m["inferred"] = False; m.setdefault("context_props", [])
        cfg = state.get_config() or {}
        schema = cfg.get("schema") or {}
        m["context_props"] = [n for n in schema if n in {"Type", "Import Source", "Commitment", "Area", "Notes", "Source Name", "Source Status", "Calendar Source", "Location"}]
        return m
    cfg = state.get_config() or {}
    return infer_mapping(cfg.get("schema") or {})


def _to_local(value: str | None, tz: _Zone) -> dict[str, Any]:
    if not value:
        return {"iso": None, "local": None, "date": None, "time": None, "all_day": None}
    if "T" not in value:
        return {"iso": value, "local": value, "date": value, "time": None, "all_day": True}
    try:
        d = _dt.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return {"iso": value, "local": value, "date": value[:10], "time": None, "all_day": False}
    return {"iso": value, "local": d.isoformat(), "date": d.date().isoformat(), "time": d.strftime("%-I:%M %p"), "all_day": False}


def interpret_item(page: dict[str, Any], mapping: dict[str, Any], tz_name: str = _LOCAL_TZ_NAME) -> dict[str, Any]:
    """Calendar-aware interpretation in the user's local time zone (Canvas due times arrive in UTC)."""
    tz = _Zone(tz_name)
    base = interpret_page(page, mapping)
    props = page.get("properties") or {}
    start = _to_local(base.get("date_start"), tz)
    end = _to_local(base.get("date_end"), tz)
    ctx = {k: props.get(k) for k in (mapping.get("context_props") or []) if props.get(k) not in (None, "", [], {})}
    kind = str(ctx.get("Type") or base.get("type") or "")
    source = str(ctx.get("Import Source") or "")
    is_deadline = kind.lower() in {"homework", "test", "task", "assignment", "quiz", "exam"} or source.lower() == "canvas"
    return {
        **base,
        "start_local": start["local"], "end_local": end["local"], "local_date": start["date"], "local_end_date": end["date"],
        "start_time": start["time"], "end_time": end["time"], "all_day": bool(start["all_day"]),
        "kind": kind or None, "import_source": source or None, "is_deadline": is_deadline,
        "commitment": ctx.get("Commitment"), "areas": ctx.get("Area"), "course": ctx.get("Notes") or None,
        "source_name": ctx.get("Source Name") or None, "source_status": ctx.get("Source Status"),
        "summary": _item_summary(base.get("title"), kind, source, start, end, ctx, is_deadline),
    }


def _item_summary(title, kind, source, start, end, ctx, is_deadline) -> str:
    when = start["date"] or "undated"
    if start["time"]:
        when += f" {start['time']}" + (f"–{end['time']}" if end.get("time") else "")
    label = "DUE" if is_deadline else (kind.upper() if kind else "EVENT")
    extra = []
    if ctx.get("Notes"):
        extra.append(str(ctx["Notes"]))
    if ctx.get("Commitment") and not is_deadline:
        extra.append(f"attendance: {ctx['Commitment']}")
    if ctx.get("Source Status") and str(ctx["Source Status"]).lower() in {"cancelled", "missing"}:
        extra.append(f"source {ctx['Source Status']}")
    return f"[{label}] {when} — {title}" + (f" ({'; '.join(extra)})" if extra else "")


def items_between_effective(start: str, end: str, include_cancelled: bool = False, tz_name: str = _LOCAL_TZ_NAME) -> dict[str, Any]:
    """Date-filtered, locally-interpreted items. Works with a saved OR inferred mapping. `start`/`end` are local YYYY-MM-DD."""
    m = effective_mapping()
    if not m:
        raise NotionError("validation", "Cannot infer the date/title properties from the live schema; save a mapping in the Notion Mapper.", code="mapping_missing")
    prop = m["date"]
    tz = _Zone(tz_name)
    # Widen the Notion filter by a day on each side so UTC-stored timestamps that fall on local boundary days are not missed.
    lo = (_dt.fromisoformat(start) - _td(days=1)).date().isoformat()
    hi = (_dt.fromisoformat(end) + _td(days=1)).date().isoformat()
    filt = {"and": [{"property": prop, "date": {"on_or_after": lo}}, {"property": prop, "date": {"on_or_before": hi}}]}
    pages = query_pages(limit=500, filter_obj=filt, sorts=[{"property": prop, "direction": "ascending"}])
    items = []
    for p in pages:
        it = interpret_item(p, m, tz_name)
        d0, d1 = it.get("local_date"), it.get("local_end_date") or it.get("local_date")
        if not d0:
            continue
        if d1 < start or d0 > end:
            continue
        if not include_cancelled and str(it.get("source_status") or "").lower() == "cancelled":
            continue
        items.append(it)
    items.sort(key=lambda i: (i.get("local_date") or "", i.get("start_local") or ""))
    return {"start": start, "end": end, "time_zone": tz_name, "mapping": {"date": m["date"], "title": m["title"], "inferred": m.get("inferred", False)}, "count": len(items), "items": items}


def local_day(offset_days: int = 0, tz_name: str = _LOCAL_TZ_NAME) -> str:
    return (_dt.now(_Zone(tz_name)).date() + _td(days=offset_days)).isoformat()


def resolve_day_word(word: str, tz_name: str = _LOCAL_TZ_NAME) -> tuple[str, str]:
    w = (word or "today").strip().lower()
    today = _dt.now(_Zone(tz_name)).date()
    if w in {"today", "tonight"}:
        d = today
    elif w == "tomorrow":
        d = today + _td(days=1)
    elif w == "yesterday":
        d = today - _td(days=1)
    elif w in {"this week", "week"}:
        startd = today - _td(days=today.weekday())
        return startd.isoformat(), (startd + _td(days=6)).isoformat()
    elif w == "next week":
        startd = today - _td(days=today.weekday()) + _td(days=7)
        return startd.isoformat(), (startd + _td(days=6)).isoformat()
    elif w in {"weekend", "this weekend"}:
        sat = today + _td(days=(5 - today.weekday()) % 7)
        return sat.isoformat(), (sat + _td(days=1)).isoformat()
    else:
        d = _dt.fromisoformat(w[:10]).date()
    return d.isoformat(), d.isoformat()
