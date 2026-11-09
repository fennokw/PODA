"""Live Notion context for chat: a compact, locally-interpreted agenda for the next 7 days (external, untrusted).

Replaces the old dump of 55 unfiltered rows, which cost ~6K prompt tokens and led the model to answer from raw UTC rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import state

MAX_CHARS = 4500


def live_context(days: int = 7) -> str:
    cfg = state.get_config()
    if not cfg or not state.token_present() or not cfg.get("data_source_id"):
        return "[Notion calendar database not connected]"
    try:
        from . import mapping as nm
        tz = ZoneInfo(nm._LOCAL_TZ_NAME)
        today = datetime.now(tz).date()
        start, end = today.isoformat(), (today + timedelta(days=days)).isoformat()
        data = nm.items_between_effective(start, end)
    except Exception as exc:
        return f"[Notion calendar read failed: {exc}]"
    by_day: dict[str, list[str]] = {}
    for it in data["items"]:
        by_day.setdefault(it.get("local_date") or "undated", []).append(it["summary"])
    lines = [f"Connected Notion database: {cfg.get('database_title')} — agenda for {start} (today) through {end}, local time {data['time_zone']}. "
             f"Date property '{data['mapping']['date']}'{' (inferred mapping)' if data['mapping']['inferred'] else ''}. For other ranges call notion_agenda / notion_query_items."]
    for d in range(days + 1):
        day = (today + timedelta(days=d)).isoformat()
        label = "today" if d == 0 else ("tomorrow" if d == 1 else datetime.fromisoformat(day).strftime("%A"))
        entries = by_day.get(day) or []
        lines.append(f"{day} ({label}): " + ("; ".join(entries) if entries else "nothing scheduled or due"))
    text = "\n".join(lines)
    return text[:MAX_CHARS] + ("\n[agenda truncated]" if len(text) > MAX_CHARS else "")
