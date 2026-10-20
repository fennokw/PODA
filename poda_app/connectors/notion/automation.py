"""A single narrowly scoped unattended Notion insertion with duplicate suppression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import mapping, state
from .client import NotionClient, NotionError
from .service import normalize_page, query_pages


def create_calendar_event(title: str, start: str, end: str = "", notes: str = "") -> dict[str, Any]:
    try:
        cfg = state.get_config() or {}
        active = mapping.get_active_mapping()
        if not cfg.get("insert_verified") or not active or active.get("stale"):
            return {"ok": False, "error": "Notion insert permission and an up-to-date schema mapping are required",
                    "result": None, "verification": "not written"}
        if not 1 <= len(title.strip()) <= 160 or len(notes) > 1800:
            raise ValueError("Invalid event title or notes")
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if dt.tzinfo is None or not datetime.now(timezone.utc) <= dt.astimezone(timezone.utc) <= datetime.now(timezone.utc) + timedelta(days=90):
            raise ValueError("Use a future, timezone-aware start within 90 days")
        if end:
            ending = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if ending.tzinfo is None or ending <= dt or ending - dt > timedelta(hours=12):
                raise ValueError("Event end must follow start by at most 12 hours")
        event = {"title":title.strip(), "start":start, "end":end or None, "notes":notes}
        props = mapping.build_properties(active["mapping"], cfg["schema"], event)
        # Always check destination immediately before the write.
        # Limit duplicate detection to the relevant calendar DAY; scanning
        # hundreds of unrelated pages for every creation can saturate Notion
        # and make the UI appear frozen when many meetings are inserted.
        existing = query_pages(limit=1000, filter_obj={
            "property":active["mapping"]["date"],
            "date":{"equals":dt.date().isoformat()}
        })
        duplicates = mapping.find_duplicates(event, active["mapping"], existing)
        if duplicates:
            return {"ok": True, "result":{"created":False,"duplicate":True,"matches":duplicates[:3]},
                    "verification":"duplicate found; no write"}
        payload = {"parent":{"type":"data_source_id","data_source_id":cfg["data_source_id"]},"properties":props}
        client = NotionClient()
        created = client.create_page(payload)
        reread = normalize_page(client.page(created["id"]))
        title_key = active["mapping"]["title"]
        date_key = active["mapping"]["date"]
        verified = (reread["properties"].get(title_key) == title.strip() and
                    (reread["properties"].get(date_key) or {}).get("start") == start)
        return {"ok":verified,"result":{"created":True,"verified":verified,"page_id":created["id"],"url":reread["url"]},
                "error":None if verified else "Created but re-read did not match",
                "verification":"re-read-after-write" if verified else "unverified"}
    except (ValueError, NotionError, KeyError) as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"not confirmed"}

def update_calendar_event(page_id: str, title: str = "", start: str = "", end: str = "") -> dict[str, Any]:
    """Update only title and/or date on an existing page in the bound source."""
    try:
        import uuid
        uuid.UUID(page_id)
        cfg = state.get_config() or {}
        active = mapping.get_active_mapping()
        if not cfg.get("update_verified") or not active or active.get("stale"):
            raise ValueError("Notion update access or fresh mapping is required")
        client = NotionClient()
        raw = client.page(page_id)
        if (raw.get("parent") or {}).get("data_source_id") != cfg.get("data_source_id"):
            raise ValueError("Page is outside the authorized Notion calendar")
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
        after=normalize_page(client.page(page_id))
        ok = (not title or after["properties"].get(names["title"])==title.strip()) and (
            not start or (after["properties"].get(names["date"]) or {}).get("start")==start)
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
        cfg=state.get_config() or {}
        if not cfg.get("update_verified"):
            raise ValueError("Notion update permission not verified")
        client=NotionClient()
        before=client.page(page_id)
        if (before.get("parent") or {}).get("data_source_id")!=cfg.get("data_source_id"):
            raise ValueError("Page is outside the authorized calendar")
        client.update_page(page_id,{"in_trash":True})
        after=client.page(page_id)
        ok=bool(after.get("in_trash") or after.get("archived"))
        return {"ok":ok,"result":{"archived":ok,"page_id":page_id},
                "error":None if ok else "Archive not confirmed","verification":"re-read-after-write" if ok else "not verified"}
    except (ValueError,NotionError) as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"not confirmed"}
