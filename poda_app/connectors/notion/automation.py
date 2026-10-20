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
        existing = query_pages(limit=500)
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
