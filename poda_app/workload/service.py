"""Local adaptive workload records, calibration, and explainable schedule planning.

Work is suggestion-only: creating calendar events is never implicit.
All persisted descriptions and calibration examples remain in the encrypted PODA DB.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from ..runtime.db import connect, now_iso, rows, one

CATEGORIES = {"general", "homework", "study", "writing", "coding", "research", "admin", "creative", "reading"}
STATES = {"todo", "active", "paused", "done"}
BASE = {"general": 55, "homework": 65, "study": 60, "writing": 80, "coding": 95,
        "research": 85, "admin": 30, "creative": 100, "reading": 45}
DEFAULT_PREFS = {"start_hour": 9, "end_hour": 21, "session_minutes": 70,
                 "break_minutes": 10, "max_daily_minutes": 300, "include_weekends": True}


def ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS workload_items (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        project TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'general',
        importance INTEGER NOT NULL DEFAULT 5, difficulty INTEGER NOT NULL DEFAULT 5,
        deadline TEXT, baseline_minutes INTEGER, user_minutes INTEGER, actual_minutes INTEGER,
        progress INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'todo',
        source_type TEXT NOT NULL DEFAULT 'manual', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workload_calibration (
        id TEXT PRIMARY KEY, item_id TEXT, category TEXT NOT NULL, difficulty INTEGER NOT NULL,
        baseline_minutes INTEGER NOT NULL, observed_minutes INTEGER NOT NULL,
        kind TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(item_id) REFERENCES workload_items(id) ON DELETE SET NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workload_preferences (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")


def _db_rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = connect()
    try:
        ensure_tables(conn)
        return rows(conn, sql, params)
    finally:
        conn.close()


def _bounded(value: Any, lo: int, hi: int, name: str) -> int:
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a number from {lo} to {hi}") from exc
    if number < lo or number > hi:
        raise ValueError(f"{name} must be {lo}–{hi}")
    return number


def _minutes(value: Any, name: str) -> int | None:
    if value in ("", None):
        return None
    return _bounded(value, 1, 200000, name)


def _deadline(value: Any) -> str | None:
    if value in ("", None):
        return None
    try:
        text = str(value)
        datetime.fromisoformat(text.replace("Z", "+00:00")) if "T" in text else date.fromisoformat(text)
        return text
    except ValueError as exc:
        raise ValueError("deadline must be YYYY-MM-DD or ISO 8601 datetime") from exc


def normalize(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {**(existing or {}), **data}
    title = str(result.get("title") or "").strip()
    if not title or len(title) > 200:
        raise ValueError("title must be 1–200 characters")
    category = str(result.get("category") or "general").lower().strip()
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(sorted(CATEGORIES))}")
    state = str(result.get("status") or "todo").lower()
    if state not in STATES:
        raise ValueError("status must be todo, active, paused, or done")
    description = str(result.get("description") or "")[:30000]
    return {
        "title": title, "description": description,
        "project": str(result.get("project") or "")[:160],
        "category": category, "importance": _bounded(result.get("importance", 5), 1, 10, "importance"),
        "difficulty": _bounded(result.get("difficulty", 5), 1, 10, "difficulty"),
        "deadline": _deadline(result.get("deadline")),
        "baseline_minutes": _minutes(result.get("baseline_minutes"), "baseline_minutes"),
        "user_minutes": _minutes(result.get("user_minutes"), "user_minutes"),
        "actual_minutes": _minutes(result.get("actual_minutes"), "actual_minutes"),
        "progress": _bounded(result.get("progress", 0), 0, 100, "progress"),
        "status": state, "source_type": str(result.get("source_type") or "manual")[:32],
    }


def all_items(include_done: bool = True) -> list[dict[str, Any]]:
    where = "" if include_done else " WHERE status != 'done'"
    items = _db_rows("SELECT * FROM workload_items" + where + " ORDER BY updated_at DESC")
    return [{**item, "estimate": estimate(item)} for item in items]


def get_item(item_id: str) -> dict[str, Any] | None:
    found = _db_rows("SELECT * FROM workload_items WHERE id=?", (item_id,))
    return {**found[0], "estimate": estimate(found[0])} if found else None


def save_item(data: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
    existing = get_item(item_id) if item_id else None
    if item_id and not existing:
        raise KeyError("work item not found")
    clean = normalize(data, existing)
    stamp = now_iso()
    conn = connect()
    try:
        ensure_tables(conn)
        if existing:
            fields = list(clean)
            conn.execute("UPDATE workload_items SET " + ",".join(f"{key}=?" for key in fields)
                         + ",updated_at=? WHERE id=?",
                         (*[clean[k] for k in fields], stamp, item_id))
        else:
            item_id = str(uuid.uuid4())
            fields = ["id", *clean.keys(), "created_at", "updated_at"]
            conn.execute("INSERT INTO workload_items (" + ",".join(fields) + ") VALUES ("
                         + ",".join("?" for _ in fields) + ")",
                         (item_id, *clean.values(), stamp, stamp))
        # Replacing the user estimate updates, rather than duplicates, its calibration example.
        conn.execute("DELETE FROM workload_calibration WHERE item_id=? AND kind='user'", (item_id,))
        if clean["user_minutes"]:
            conn.execute("INSERT INTO workload_calibration VALUES (?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), item_id, clean["category"], clean["difficulty"],
                          clean["baseline_minutes"] or heuristic_base(clean), clean["user_minutes"], "user", stamp))
        conn.execute("DELETE FROM workload_calibration WHERE item_id=? AND kind='actual'", (item_id,))
        if clean["actual_minutes"]:
            conn.execute("INSERT INTO workload_calibration VALUES (?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), item_id, clean["category"], clean["difficulty"],
                          clean["baseline_minutes"] or heuristic_base(clean), clean["actual_minutes"], "actual", stamp))
        conn.commit()
    finally:
        conn.close()
    return get_item(item_id) or {}


def delete_item(item_id: str) -> bool:
    conn = connect()
    try:
        ensure_tables(conn)
        conn.execute("DELETE FROM workload_calibration WHERE item_id=?", (item_id,))
        cursor = conn.execute("DELETE FROM workload_items WHERE id=?", (item_id,))
        conn.commit()
        return bool(cursor.rowcount)
    finally:
        conn.close()


def heuristic_base(item: dict[str, Any]) -> int:
    """Transparent coarse starting estimate, prior to any personal feedback."""
    category = item.get("category") or "general"
    difficulty = _bounded(item.get("difficulty", 5), 1, 10, "difficulty")
    text = str(item.get("description") or "")
    quantity = len(re.findall(r"(?im)^\s*(?:\d+[.)]|[-*]\s+|q\d+\b)", text))
    words = len(text.split())
    scale = (0.55 + difficulty * 0.105) * (1 + min(0.65, quantity * 0.07 + words / 2500))
    return max(10, int(round(BASE.get(category, 55) * scale / 5) * 5))


def calibrate(category: str, difficulty: int, exclude_item: str | None = None) -> dict[str, Any]:
    """Weighted log ratio: same-category and similar-difficulty examples contribute most.

    Baseline is a weak default; feedback shifts predictions smoothly and is clamped.
    """
    samples = _db_rows("SELECT * FROM workload_calibration ORDER BY created_at DESC LIMIT 300")
    weighted_sum = 0.0
    total_weight = 0.0
    contributors = 0
    for sample in samples:
        if exclude_item and sample.get("item_id") == exclude_item:
            continue
        if not sample.get("baseline_minutes") or not sample.get("observed_minutes"):
            continue
        distance = abs(int(sample["difficulty"]) - int(difficulty))
        weight = (1.0 if sample["category"] == category else 0.18) / (1 + distance * 0.75)
        if sample["kind"] == "actual":
            weight *= 2.25
        log_ratio = math.log(max(0.25, min(5.0, sample["observed_minutes"] / sample["baseline_minutes"])))
        weighted_sum += weight * log_ratio
        total_weight += weight
        contributors += 1
    # A prior centered on 1x avoids overreacting to one outlier.
    multiplier = math.exp(weighted_sum / (1.5 + total_weight))
    multiplier = max(0.35, min(3.5, multiplier))
    return {"multiplier": round(multiplier, 3), "examples": contributors,
            "effective_samples": round(total_weight, 2),
            "confidence": "high" if total_weight >= 8 else "medium" if total_weight >= 3 else "low"}


def estimate(item: dict[str, Any]) -> dict[str, Any]:
    base = int(item.get("baseline_minutes") or heuristic_base(item))
    adjust = calibrate(str(item.get("category") or "general"), int(item.get("difficulty") or 5),
                       exclude_item=item.get("id"))
    personal = item.get("user_minutes")
    if personal:
        predicted = int(personal)
        source = "user-provided estimate"
    else:
        predicted = max(5, round(base * adjust["multiplier"] / 5) * 5)
        source = "calibrated baseline" if adjust["examples"] else "category/difficulty heuristic"
    # Treat remaining time as a conservative linear approximation; never imply exact precision.
    remaining = max(0, math.ceil(predicted * (1 - int(item.get("progress") or 0) / 100) / 5) * 5)
    if item.get("status") == "done":
        remaining = 0
    uncertainty = 0.2 if personal or adjust["confidence"] == "high" else 0.35 if adjust["confidence"] == "medium" else 0.5
    return {"minutes": predicted, "remaining_minutes": remaining,
            "range_minutes": [max(5, round(predicted * (1 - uncertainty))), round(predicted * (1 + uncertainty))],
            "source": source, "calibration": adjust,
            "note": "An estimate, not a measured duration; record actual time to improve future predictions."}


def preferences(new: dict[str, Any] | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        ensure_tables(conn)
        stored = {r["key"]: json.loads(r["value"]) for r in rows(conn, "SELECT * FROM workload_preferences")}
        if new is not None:
            merged = {**DEFAULT_PREFS, **stored, **new}
            validated = {
                "start_hour": _bounded(merged["start_hour"], 0, 23, "start_hour"),
                "end_hour": _bounded(merged["end_hour"], 1, 24, "end_hour"),
                "session_minutes": _bounded(merged["session_minutes"], 15, 180, "session_minutes"),
                "break_minutes": _bounded(merged["break_minutes"], 0, 60, "break_minutes"),
                "max_daily_minutes": _bounded(merged["max_daily_minutes"], 30, 900, "max_daily_minutes"),
                "include_weekends": bool(merged["include_weekends"]),
            }
            if validated["start_hour"] >= validated["end_hour"]:
                raise ValueError("start_hour must precede end_hour")
            for key, value in validated.items():
                conn.execute("INSERT OR REPLACE INTO workload_preferences VALUES (?,?)", (key, json.dumps(value)))
            conn.commit()
            return validated
        return {**DEFAULT_PREFS, **stored}
    finally:
        conn.close()


def _as_local(text: str | None, fallback: datetime) -> datetime:
    if not text:
        return fallback
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00")) if "T" in text else datetime.combine(date.fromisoformat(text), time(23, 59))
        return d.astimezone() if d.tzinfo else d.replace(tzinfo=fallback.tzinfo)
    except ValueError:
        return fallback


def _calendar_blocks(first: date, last: date) -> tuple[list[tuple[datetime, datetime]], str]:
    """Read only; if Notion cannot be reached, mark availability unverified."""
    try:
        from ..connectors.notion import state, mapping
        status = state.capability()
        if not status.get("can_read_content"):
            return [], "Notion not connected; calendar conflicts NOT checked"
        outcome = mapping.items_between_effective(first.isoformat(), last.isoformat())
        blocks: list[tuple[datetime, datetime]] = []
        now = datetime.now().astimezone()
        for item in outcome.get("items", []):
            if item.get("is_deadline"):
                continue
            start = str(item.get("date_start") or "")
            finish = str(item.get("date_end") or start)
            if not start:
                continue
            if "T" not in start:
                start_day = date.fromisoformat(start)
                end_day = date.fromisoformat(finish.split("T")[0])
                blocks.append((datetime.combine(start_day, time.min, now.tzinfo),
                               datetime.combine(end_day + timedelta(days=1), time.min, now.tzinfo)))
            else:
                begin = _as_local(start, now)
                end = _as_local(finish, now)
                if end <= begin:
                    end = begin + timedelta(hours=1)
                blocks.append((begin, end))
        return blocks, "Notion calendar read; draft plan only (not written to Notion)"
    except Exception as exc:
        return [], f"Notion availability unavailable: {type(exc).__name__}; conflicts NOT checked"


def _overlap(begin: datetime, end: datetime, blocks: list[tuple[datetime, datetime]]) -> bool:
    return any(begin < e and end > s for s, e in blocks)


def schedule(days: int = 14, calendar: bool = True, now: datetime | None = None) -> dict[str, Any]:
    days = _bounded(days, 1, 31, "days")
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    prefs = preferences()
    items = [i for i in all_items(False) if i["estimate"]["remaining_minutes"] > 0 and i["status"] != "paused"]
    # Transparent urgency/importance blend: near deadlines outweigh routine work,
    # while critical longer-term projects still receive attention before becoming urgent.
    def score(item: dict[str, Any]) -> float:
        due = _as_local(item["deadline"], now + timedelta(days=365))
        days_left = max(0.0, (due - now).total_seconds() / 86400)
        return item["importance"] * 4.0 + 100.0 / (1.0 + days_left)
    items.sort(key=lambda item: (-score(item), _as_local(item["deadline"], now + timedelta(days=365))))
    last = now.date() + timedelta(days=days - 1)
    if calendar:
        blocks, calendar_note = _calendar_blocks(now.date(), last)
    else:
        blocks, calendar_note = [], "Calendar check disabled; conflicts NOT checked"
    busy = list(blocks)
    daily_minutes: dict[str, int] = {}
    sessions: list[dict[str, Any]] = []
    overdue: list[str] = []
    unscheduled: list[dict[str, Any]] = []
    for item in items:
        remaining = int(item["estimate"]["remaining_minutes"])
        cutoff = _as_local(item["deadline"], now + timedelta(days=365))
        if cutoff <= now:
            overdue.append(item["id"])
        for offset in range(days):
            if remaining <= 0:
                break
            day = now.date() + timedelta(days=offset)
            if not prefs["include_weekends"] and day.weekday() >= 5:
                continue
            daykey = day.isoformat()
            used = daily_minutes.get(daykey, 0)
            cursor = datetime.combine(day, time(prefs["start_hour"], 0), now.tzinfo)
            finish = datetime.combine(day, time(prefs["end_hour"] % 24, 0), now.tzinfo)
            if prefs["end_hour"] == 24:
                finish += timedelta(days=1)
            cursor = max(cursor, now)
            while remaining > 0 and cursor < min(finish, cutoff):
                capacity = prefs["max_daily_minutes"] - used
                if capacity < 15:
                    break
                chunk = min(remaining, prefs["session_minutes"], capacity)
                end = cursor + timedelta(minutes=chunk)
                if end > finish or end > cutoff:
                    chunk = int((min(finish, cutoff) - cursor).total_seconds() // 60)
                    end = cursor + timedelta(minutes=chunk)
                if chunk < 15:
                    break
                conflict = next(((s, e) for s, e in sorted(busy) if cursor < e and end > s), None)
                if conflict:
                    cursor = max(cursor, conflict[1])
                    continue
                sessions.append({"item_id": item["id"], "title": item["title"], "project": item["project"],
                                 "start": cursor.isoformat(), "end": end.isoformat(),
                                 "minutes": chunk, "deadline": item["deadline"], "importance": item["importance"],
                                 "priority_score": round(score(item), 1)})
                busy.append((cursor, end))
                remaining -= chunk
                used += chunk
                daily_minutes[daykey] = used
                cursor = end + timedelta(minutes=prefs["break_minutes"])
        if remaining:
            unscheduled.append({"item_id": item["id"], "title": item["title"],
                                "remaining_minutes": remaining, "deadline": item["deadline"],
                                "reason": "Insufficient verified open work time before deadline/horizon"})
    sessions.sort(key=lambda x: x["start"])
    return {"sessions": sessions, "unscheduled": unscheduled, "overdue_ids": overdue,
            "calendar": calendar_note, "preferences": prefs, "horizon_days": days,
            "total_minutes": sum(s["minutes"] for s in sessions),
            "note": "Local suggestions only. Time blocks are NOT added to Notion. Estimates and free-time assumptions may be wrong."}


def breakdown(item_id: str) -> dict[str, Any]:
    item = get_item(item_id)
    if not item:
        raise KeyError("work item not found")
    minutes = item["estimate"]["remaining_minutes"]
    category = item["category"]
    labels = (["Read prompt and outline", "Complete the core problems", "Verify solutions and submit"]
              if category in {"homework", "study"} else
              ["Review requirements", "Execute the main work", "Test, revise, and finish"])
    portions = (0.15, 0.7, 0.15) if category in {"homework", "coding"} else (0.2, 0.65, 0.15)
    allocations = [round(minutes * portions[0]), round(minutes * portions[1])]
    allocations.append(minutes - sum(allocations))
    return {"item_id": item_id, "phases": [{"title": title, "minutes": m} for title, m in zip(labels, allocations)],
            "estimate": item["estimate"], "note": "Editable suggested phases, not a claim that the work was performed."}
