"""Calendar view of the connected data source — the same pages Notion Calendar shows for this database, grouped
by their date property. Read-only. Works before a Schema Mapper version is saved by falling back to the obvious
properties (the `Date` date property, the title property, `Type`, `Status`, `Commitment`, `Area`, `Import Source`)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import mapping, state
from .client import NotionError
from .service import query_pages

LOOKBACK_DAYS = 14  # multi-day pages that *started* before the window but overlap it


def resolve_fields(cfg: dict[str, Any], active: dict[str, Any] | None) -> dict[str, Any]:
    schema = cfg.get("schema") or {}
    mapped = (active or {}).get("mapping") or {}

    def typed(name: str | None, *types: str) -> str | None:
        prop = schema.get(name or "")
        return name if isinstance(prop, dict) and (not types or prop.get("type") in types) else None

    def pick(preferred: str | None, candidates: tuple[str, ...], *types: str) -> str | None:
        if typed(preferred, *types):
            return preferred
        for name in candidates:
            if typed(name, *types):
                return name
        for name, prop in schema.items():  # any property of an acceptable type
            if isinstance(prop, dict) and types and prop.get("type") in types and name not in candidates:
                return name
        return None

    title = typed(mapped.get("title"), "title") or next((n for n, p in schema.items() if isinstance(p, dict) and p.get("type") == "title"), None)
    date_prop = typed(mapped.get("date"), "date") or ("Date" if typed("Date", "date") else None) or next((n for n, p in schema.items() if isinstance(p, dict) and p.get("type") == "date"), None)
    return {
        "title": title,
        "date": date_prop,
        "type": pick(mapped.get("type"), ("Type", "Category", "Kind"), "select", "multi_select", "status"),
        "status": pick(mapped.get("status"), ("Status", "Source Status"), "status", "select"),
        "commitment": typed("Commitment", "select"),
        "area": pick(None, ("Area", "Project", "Project Domain", "Domain"), "multi_select", "select"),
        "source": pick(None, ("Import Source", "Source"), "select"),
        "notes": typed(mapped.get("notes"), "rich_text") or typed("Notes", "rich_text"),
        "source_url": typed("Source URL", "url") or next((n for n, p in schema.items() if isinstance(p, dict) and p.get("type") == "url"), None),
        "done_values": list(mapped.get("status_done_values") or []),
        "from_mapping": bool(active),
    }


def _date_parts(value: Any) -> tuple[str | None, str | None, str | None]:
    if isinstance(value, dict):
        return value.get("start"), value.get("end"), value.get("time_zone")
    if isinstance(value, str):
        return value, None, None
    return None, None, None


def interpret(page: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any] | None:
    props = page.get("properties") or {}
    start, end, tz = _date_parts(props.get(fields["date"] or ""))
    if not start:
        return None
    title = (props.get(fields["title"] or "") or page.get("title") or "Untitled") if fields.get("title") else (page.get("title") or "Untitled")
    status_val = props.get(fields["status"]) if fields.get("status") else None
    area = props.get(fields["area"]) if fields.get("area") else None
    notes = props.get(fields["notes"]) if fields.get("notes") else None
    type_val = props.get(fields["type"]) if fields.get("type") else None
    if isinstance(type_val, list):
        type_val = type_val[0] if type_val else None
    return {
        "page_id": page.get("id"), "title": str(title), "url": page.get("url"),
        "start": start, "end": end, "all_day": "T" not in str(start), "time_zone": tz,
        "type": type_val, "status": status_val, "done": (status_val in set(fields.get("done_values") or [])) if status_val is not None and fields.get("done_values") else None,
        "commitment": props.get(fields["commitment"]) if fields.get("commitment") else None,
        "area": area if isinstance(area, list) else ([area] if area else []),
        "source": props.get(fields["source"]) if fields.get("source") else None,
        "notes": (str(notes)[:400] if notes else None),
        "source_url": props.get(fields["source_url"]) if fields.get("source_url") else None,
        "last_edited_time": page.get("last_edited_time"),
    }


def _overlaps(event: dict[str, Any], start: str, end: str) -> bool:
    s = str(event["start"])[:10]
    e = str(event.get("end") or event["start"])[:10]
    return s <= end and e >= start


def events_between(start: str, end: str) -> dict[str, Any]:
    cfg = state.get_config()
    if not cfg or not cfg.get("data_source_id") or not state.token_present():
        raise NotionError("validation", "No Notion data source is connected.", code="not_connected")
    try:
        start_d = date.fromisoformat(start[:10]); end_d = date.fromisoformat(end[:10])
    except ValueError as exc:
        raise NotionError("validation", f"start/end must be ISO dates (YYYY-MM-DD): {exc}", code="bad_range")
    if end_d < start_d:
        raise NotionError("validation", "end must not be before start.", code="bad_range")
    if (end_d - start_d).days > 120:
        raise NotionError("validation", "Ask for at most 120 days at a time.", code="bad_range")
    active = mapping.get_active_mapping()
    fields = resolve_fields(cfg, active)
    if not fields["date"]:
        raise NotionError("validation", "The connected data source has no date property, so there is nothing to place on a calendar.", code="no_date_property")
    query_start = (start_d - timedelta(days=LOOKBACK_DAYS)).isoformat()
    filt = {"and": [{"property": fields["date"], "date": {"on_or_after": query_start}}, {"property": fields["date"], "date": {"on_or_before": end_d.isoformat()}}]}
    pages = query_pages(limit=500, filter_obj=filt, sorts=[{"property": fields["date"], "direction": "ascending"}])
    events = []
    for page in pages:
        if page.get("in_trash"):
            continue
        event = interpret(page, fields)
        if event and _overlaps(event, start_d.isoformat(), end_d.isoformat()):
            events.append(event)
    events.sort(key=lambda e: (str(e["start"])[:10], not e["all_day"], str(e["start"])))
    return {"start": start_d.isoformat(), "end": end_d.isoformat(), "count": len(events), "events": events, "fields": fields,
            "mapping_version": (active or {}).get("version"), "database_title": cfg.get("database_title"), "data_source_name": cfg.get("data_source_name"),
            "note": "Read-only view of the connected data source's pages placed by their date property — the same records Notion Calendar shows. Times are converted to this Mac's time zone in the app."}
