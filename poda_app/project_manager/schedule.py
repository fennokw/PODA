"""Mirror task deadlines into Calendar and, with write access, Notion."""
from __future__ import annotations

import json
from threading import RLock

from ..connectors.calendar import service as calendar
from ..runtime.db import connect, one, rows

SYNC_LOCK = RLock()
PREFIX = "project-task:"


def get_event(event_id, expected_scope=None):
    if not event_id or event_id.startswith(PREFIX):
        raise ValueError("Choose a scheduled event to link.")
    with calendar._lock:
        scope = calendar.binding()
        if expected_scope is not None and scope != expected_scope:
            raise ValueError("Select the calendar that contains the linked event.")
        calendar._require()
        if scope["provider"] == "notion":
            from ..connectors.notion import calendar as notion, mapping, state
            from ..connectors.notion.client import NotionClient
            from ..connectors.notion.service import normalize_page
            raw = mapping.page_in_source(NotionClient(), event_id, scope["data_source_id"])
            if raw.get("in_trash") or raw.get("archived"):
                raise ValueError("The linked event was archived.")
            event = notion.interpret(normalize_page(raw), notion.resolve_fields(state.get_config(), mapping.get_active_mapping()))
            if event and event.get("time_zone") and "T" in event["start"]:
                event["start"] = mapping._date_value(event["start"], event["time_zone"]).isoformat()
        elif scope["provider"] == "google":
            from ..connectors.calendar import google
            raw = google._request("GET", google._path(event_id))
            event = google._event(raw) if raw.get("status") != "cancelled" else None
        else:
            db = calendar._db()
            try:
                row = one(db, "SELECT payload FROM local_calendar_events WHERE id=? AND archived=0", (event_id,))
                event = json.loads(row["payload"]) if row else None
            finally:
                db.close()
        if not event or event.get("is_deadline") or not event.get("start"):
            raise ValueError("Choose an existing event with a date.")
        return event, scope


def sync(db, task_id):
    """Local state is durable before any write, and a failed mirror is visible and retryable."""
    from . import folders

    task = one(db, "SELECT * FROM workload_items WHERE id=?", (task_id,))
    info = folders.details(db, task_id)
    with SYNC_LOCK, calendar._lock:
        scope = calendar.binding()
        if info.get("calendar_page") and (scope["provider"] != "notion" or scope["data_source_id"] != info["calendar_source"]):
            folders.save_details(db, task_id, {"calendar_error": "Select the original Notion calendar to update its task entry."})
            return
        expected = calendar._expected.get()
        if expected is not None and scope != expected:
            folders.save_details(db, task_id, {"calendar_error": "The approved calendar changed. Review the task and retry sync."})
            return
        if scope["provider"] != "notion":
            folders.save_details(db, task_id, {"calendar_error": None})
            return
        if not task["deadline"] and not info.get("calendar_page"):
            return
        try:
            _mirror_notion(db, task, info, scope)
        except Exception as exc:
            folders.save_details(db, task_id, {"calendar_error": str(exc)})


def _mirror_notion(db, task, info, scope):
    from . import folders
    from ..connectors.notion import mapping
    from ..connectors.notion.client import NotionClient
    from ..connectors.notion.service import normalize_page, query_pages

    source = scope["data_source_id"]
    cfg = mapping.require_write("update" if info.get("calendar_page") else "insert")
    calendar._require(True, "can_update_content" if info.get("calendar_page") else "can_insert_content")
    client = NotionClient()
    active, schema = mapping.live_mapping(cfg, client)
    names = active["mapping"]
    if not names.get("type") or not names.get("completed"):
        raise ValueError("Map Type and Completed to sync a task into Notion.")
    from ..connectors.notion.event_defaults import options
    if "Task" not in options(schema[names["type"]]):
        raise ValueError("Add a Task option to the mapped Notion Type field.")
    external_ids = names.get("external_ids") or {}
    external_key = next((name for name in ("poda_task_id", "ics_event_id", "external_event_id")
                         if schema.get(external_ids.get(name), {}).get("type") == "rich_text"), None)
    if not external_key:
        raise ValueError("Map a text external ID (such as BestNDS External Event ID) to safely sync task deadlines.")
    id_field = external_ids[external_key]
    key = PREFIX + task["id"]
    page_id = info.get("calendar_page")
    if page_id:
        page = normalize_page(mapping.page_in_source(client, page_id, source))
        if page["in_trash"] or page["properties"].get(id_field) != key:
            raise ValueError("The linked Notion task was archived or changed ownership.")
    else:
        found = query_pages(limit=2, filter_obj={"property": id_field, "rich_text": {"equals": key}},
                            client=client, data_source_id=source)
        if len(found) > 1:
            raise ValueError("Multiple Notion entries match this task. Resolve them before retrying.")
        page_id = found[0]["id"] if found else None
    props = mapping.build_properties(names, schema, {
        "title": task["title"], "start": task["deadline"], "type": "Task",
        "completed": task["status"] == "done" or task["progress"] == 100,
        "external_ids": {external_key: key},
    })
    if not task["deadline"]:
        props[names["date"]] = {"date": None}
    if page_id:
        calendar._require(True, "can_update_content")
        client.update_page(page_id, {"properties": props})
    elif task["deadline"]:
        saved = client.create_page({"parent": {"type": "data_source_id", "data_source_id": source}, "properties": props})
        page_id = saved["id"]
    else:
        return
    folders.save_details(db, task["id"], {"calendar_page": page_id, "calendar_source": source})
    actual = normalize_page(mapping.page_in_source(client, page_id, source))
    expected = dict(props)
    if not task["deadline"]:
        expected.pop(names["date"])
        if actual["properties"].get(names["date"]):
            raise ValueError("Notion still has a deadline for this task.")
    if not mapping.event_matches(actual, expected):
        raise ValueError("Notion returned different task properties. Review or retry the sync.")
    folders.save_details(db, task["id"], {"calendar_error": None})


def overlay(start, end, scope, existing=()):
    """Expose local deadlines when there is no current verified Notion mirror."""
    from . import folders
    from ..workload import service as workload

    db = connect()
    try:
        workload.ensure_tables(db)
        folders.ensure_tables(db)
        items = rows(db, """SELECT w.id, w.title, w.deadline, w.status, w.progress, p.id AS project_id,
            d.calendar_page, d.calendar_source, d.calendar_error
            FROM pm_task_details d JOIN workload_items w ON w.id=d.task_id
            JOIN projects p ON lower(w.project)=lower(p.name)
            WHERE d.scheduled=1 AND substr(w.deadline,1,10) BETWEEN ? AND ?""", (start, end))
    finally:
        db.close()
    active_ids = {str(item.get("page_id", "")).replace("-", ""): item for item in existing}
    result = []
    for task in items:
        mirror = task["calendar_page"]
        if scope["provider"] == "notion" and task["calendar_source"] == scope["data_source_id"]:
            if mirror and mirror.replace("-", "") in active_ids:
                active_ids[mirror.replace("-", "")].update(project_task_id=task["id"], project_id=task["project_id"])
                continue
        due = task["deadline"]
        result.append({"page_id": PREFIX + task["id"], "project_task_id": task["id"], "project_id": task["project_id"],
                       "title": task["title"], "start": due, "end": None, "type": "Task", "is_deadline": True,
                       "done": task["status"] == "done" or task["progress"] == 100, "all_day": "T" not in due})
    return result
