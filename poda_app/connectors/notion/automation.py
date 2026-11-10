"""A single narrowly scoped unattended Notion insertion with duplicate suppression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from . import mapping
from .client import NotionClient, NotionError
from .service import normalize_page, query_pages

_create_lock = Lock()


def create_calendar_event(title: str, start: str, end: str = "", notes: str = "", commitment: str = "") -> dict[str, Any]:
    try:
        cfg = mapping.require_write("insert")
        client = NotionClient()
        active, schema = mapping.live_mapping(cfg, client)
        if not 1 <= len(title.strip()) <= 160 or len(notes) > 1800:
            raise ValueError("Invalid event title or notes")
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if dt.tzinfo is None or not datetime.now(timezone.utc) <= dt.astimezone(timezone.utc) <= datetime.now(timezone.utc) + timedelta(days=90):
            raise ValueError("Use a future, timezone-aware start within 90 days")
        if end:
            ending = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if ending.tzinfo is None or not timedelta(0) < ending - dt <= timedelta(hours=12):
                raise ValueError("Event end must follow start by at most 12 hours")
        event = {"title":title.strip(), "start":start, "end":end or None, "notes":notes}
        names = active["mapping"]
        type_field = schema.get(names.get("type"), {})
        type_kind = type_field.get("type")
        event_type = next((option["name"] for option in (type_field.get(type_kind) or {}).get("options", [])
                           if option.get("name", "").casefold() == "event"), None)
        if commitment and event_type:
            event["type"] = event_type
        props = mapping.build_properties(names, schema, event)
        if commitment:
            field = schema.get("Commitment", {})
            options = (field.get("select") or {}).get("options", [])
            choice = next((option["name"] for option in options
                           if option.get("name", "").casefold() == commitment.casefold()), None)
            if field.get("type") != "select" or not choice:
                raise ValueError(f"The connected Notion calendar has no Commitment option named {commitment}.")
            props["Commitment"] = {"select": {"name": choice}}
            event["commitment"] = choice
        # Always check destination immediately before the write.
        # Check the event-local and UTC dates under a shared lock so concurrent
        # automations cannot both insert the same title and start.
        first_day, last_day = sorted((dt.date(), dt.astimezone(timezone.utc).date()))
        with _create_lock:
            existing = query_pages(limit=1000, filter_obj={"and": [
                {"property": names["date"], "date": {"on_or_after": first_day.isoformat()}},
                {"property": names["date"], "date": {"on_or_before": last_day.isoformat()}},
            ]}, client=client, data_source_id=cfg["data_source_id"])
            duplicates = mapping.find_duplicates(event, names, existing, exact_start=True)
            if duplicates:
                return {"ok": True, "result":{"created":False,"duplicate":True,"matches":duplicates[:3]},
                        "verification":"duplicate found; no write"}
            payload = {"parent":{"type":"data_source_id","data_source_id":cfg["data_source_id"]},"properties":props}
            created = client.create_page(payload)
        expected = {name: props[name] for name in (names["title"], names["date"])}
        if "type" in event:
            expected[names["type"]] = props[names["type"]]
        if commitment:
            expected["Commitment"] = props["Commitment"]
        verification_error = None
        try:
            reread = normalize_page(mapping.page_in_source(client, created["id"], cfg["data_source_id"]))
            verified = mapping.event_matches(reread, expected)
        except NotionError as exc:
            verified = False
            verification_error = exc.message
        return {"ok":verified,"result":{"created":True,"verified":verified,"page_id":created["id"],"url":created.get("url"),**event},
                "error":None if verified else verification_error or "Created but re-read did not match",
                "verification":"re-read-after-write" if verified else "unverified"}
    except (ValueError, NotionError, KeyError) as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"not confirmed"}

def update_calendar_event(page_id: str, title: str = "", start: str = "", end: str = "") -> dict[str, Any]:
    """Update only title and/or date on an existing page in the bound source."""
    try:
        import uuid
        uuid.UUID(page_id)
        cfg = mapping.require_write("update")
        client = NotionClient()
        active, _ = mapping.live_mapping(cfg, client)
        mapping.page_in_source(client, page_id, cfg["data_source_id"])
        props = {}
        names = active["mapping"]
        if title:
            if not 1 <= len(title.strip()) <= 160:
                raise ValueError("Invalid title")
            props[names["title"]] = {"title":[{"type":"text","text":{"content":title.strip()}}]}
        if start:
            parsed = datetime.fromisoformat(start.replace("Z","+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Date requires a timezone")
            date_obj = {"start":start}
            if end:
                stop = datetime.fromisoformat(end.replace("Z","+00:00"))
                if stop.tzinfo is None or stop <= parsed:
                    raise ValueError("Invalid end time")
                date_obj["end"] = end
            props[names["date"]] = {"date":date_obj}
        elif end:
            raise ValueError("Provide start when updating end")
        if not props:
            raise ValueError("Specify title and/or start")
        client.update_page(page_id,{"properties":props})
        after = normalize_page(mapping.page_in_source(client, page_id, cfg["data_source_id"]))
        ok = mapping.event_matches(after, props)
        return {"ok":ok,"result":{"updated":True,"verified":ok,"page_id":page_id},
                "error":None if ok else "Notion re-read mismatch",
                "verification":"re-read-after-write" if ok else "not verified"}
    except (ValueError, NotionError, KeyError) as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"not confirmed"}


def archive_calendar_event(page_id: str) -> dict[str, Any]:
    """Archive one item in the currently authorized calendar."""
    try:
        import uuid
        uuid.UUID(page_id)
        cfg = mapping.require_write("update")
        client = NotionClient()
        mapping.page_in_source(client, page_id, cfg["data_source_id"])
        client.update_page(page_id,{"in_trash":True})
        after = mapping.page_in_source(client, page_id, cfg["data_source_id"])
        ok=bool(after.get("in_trash") or after.get("archived"))
        return {"ok":ok,"result":{"archived":ok,"page_id":page_id},
                "error":None if ok else "Archive not confirmed","verification":"re-read-after-write" if ok else "not verified"}
    except (ValueError,NotionError) as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"not confirmed"}
