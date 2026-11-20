"""Project chat assignments and progress for existing workload tasks."""
from __future__ import annotations

import json
import re
from threading import RLock

from ..runtime.db import connect, now_iso, one, rows
from ..chat import discussions
from ..workload import service as workload

LOCK = RLock()


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pm_chat_folders (
        session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT,
        linked_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS pm_chats_by_project ON pm_chat_folders(project_id, task_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS pm_task_details (
        task_id TEXT PRIMARY KEY, goal TEXT NOT NULL DEFAULT '',
        goal_target INTEGER, goal_current INTEGER NOT NULL DEFAULT 0,
        event_id TEXT, event_title TEXT, event_provider TEXT, event_source TEXT,
        calendar_page TEXT, calendar_source TEXT, calendar_error TEXT,
        scheduled INTEGER NOT NULL DEFAULT 0)""")


def chats(db, project_id, task_id=None):
    scope = "" if task_id is None else " AND f.task_id=?"
    params = (project_id,) if task_id is None else (project_id, task_id)
    linked = rows(db, """SELECT f.session_id, f.task_id,
        COALESCE((SELECT max(created_at) FROM messages m WHERE m.session_id=f.session_id),
                 f.linked_at) AS updated_at
        FROM pm_chat_folders f LEFT JOIN workload_items w ON w.id=f.task_id
        JOIN projects p ON p.id=f.project_id
        WHERE f.project_id=? AND (f.task_id IS NULL OR lower(w.project)=lower(p.name))
        """ + scope + " ORDER BY updated_at DESC", params)
    return [{**item, "title": discussions.header(db, item["session_id"])["title"]} for item in linked]


def assign(db, session_id, project_id, task_id=None):
    discussions.promote(db, session_id)
    db.execute("""INSERT INTO pm_chat_folders VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id,
        task_id=excluded.task_id, linked_at=excluded.linked_at""",
        (session_id, project_id, task_id, now_iso()))
    db.commit()


def find_chats(db, query):
    """Search conversation titles without exposing the rest of their transcript."""
    matches = []
    for discussion in discussions.list_work(db, limit=-1 if query else 100):
        if query.casefold() not in discussion["label"].casefold():
            continue
        sid = discussion["session_id"]
        filing = context(db, sid) or {}
        matches.append({"session_id": sid, "title": discussion["label"],
                        **{key: filing.get(key) for key in ("project_id", "project_name", "task_id", "task_title")}})
        if len(matches) == 40:
            break
    return matches


def context(db, session_id):
    return one(db, """SELECT p.id AS project_id, p.name AS project_name, w.id AS task_id,
        w.title AS task_title, w.progress, w.deadline, d.goal, d.goal_target, d.goal_current
        FROM pm_chat_folders f JOIN projects p ON p.id=f.project_id
        LEFT JOIN workload_items w ON w.id=f.task_id AND lower(w.project)=lower(p.name)
        LEFT JOIN pm_task_details d ON d.task_id=w.id WHERE f.session_id=?""", (session_id,))


def command_options(db, session_id):
    current = context(db, session_id) if session_id else None
    return {"task_name": current["task_title"] if current else None,
            "goal_target": current["goal_target"] if current else None}


def command(session_id, message):
    """Consume only explicit user commands. Return remaining prompt, reply, and current context."""
    from .router import ensure_tables as ensure_projects

    message = message.strip()
    db = connect()
    try:
        ensure_projects(db)
        current = context(db, session_id)
        if message.casefold() == "/taskdone" or (current and current["task_id"] and re.fullmatch(
                r"(?:please )?(?:mark (?:this|the) task (?:as )?(?:done|complete|completed)|check (?:(?:this|the) task|it) off|check off (?:(?:this|the) task|it)|complete (?:this|the) task)[.!]?",
                message, re.I)):
            if not current or not current["task_id"]:
                return "", "Choose a task in the chat header first.", current
            task = update_task(current["project_id"], current["task_id"], {"progress": 100})
            return "", completion_reply(task), context(db, session_id)

        if count := re.fullmatch(r"/taskprogress\s+(\d{1,7})", message, re.I):
            if not current or not current["task_id"]:
                return "", "Open a task chat before reporting a goal count.", current
            task = update_task(current["project_id"], current["task_id"], {"goal_current": int(count[1])})
            reply = completion_reply(task) if task["progress"] == 100 else f'Updated {task["title"]} to {task["progress"]}%.'
            return "", reply, context(db, session_id)
        if re.match(r"^/(?:project|task)(?=\s|$)", message, re.I):
            return "", "Use Project and Task at the top of the chat to file this discussion.", current
        return message, "", current
    except ValueError as exc:
        return "", str(exc), context(db, session_id)
    finally:
        db.close()


def details(db, task_id):
    return one(db, "SELECT * FROM pm_task_details WHERE task_id=?", (task_id,)) or {}


def save_details(db, task_id, changes):
    db.execute("INSERT OR IGNORE INTO pm_task_details(task_id) VALUES (?)", (task_id,))
    if changes:
        db.execute("UPDATE pm_task_details SET " + ", ".join(f"{name}=?" for name in changes) + " WHERE task_id=?",
                   (*changes.values(), task_id))
    db.commit()


def get_task(db, project_id, task_id):
    task = one(db, """SELECT w.* FROM workload_items w JOIN projects p ON lower(w.project)=lower(p.name)
                     WHERE p.id=? AND w.id=?""", (project_id, task_id))
    if not task:
        raise KeyError("Task not found in this project.")
    return task


def task_view(db, project_id, task_id):
    task = get_task(db, project_id, task_id)
    return {**task, "progress": 100 if task["status"] == "done" else task["progress"],
            "estimate": workload.estimate(task), "tracking": details(db, task_id),
            "chats": chats(db, project_id, task_id)}


def completion_reply(task):
    result = f'Marked {task["title"]} complete.'
    if error := task["tracking"].get("calendar_error"):
        result += f" Calendar sync needs attention: {error}"
    return result


def update_task(project_id, task_id, changes):
    from . import schedule

    with LOCK:
        db = connect()
        try:
            ensure_tables(db)
            task = get_task(db, project_id, task_id)
            tracking = details(db, task_id)
            local = {k: changes[k] for k in ("title", "description", "user_minutes", "actual_minutes", "progress", "deadline") if k in changes}
            tracked = {k: changes[k] for k in ("goal", "goal_target", "goal_current") if k in changes}
            if "event_id" in changes:
                event_id = changes["event_id"]
                if event_id:
                    event, scope = schedule.get_event(event_id, changes.get("calendar_scope"))
                    local["deadline"] = event["start"]
                    tracked.update(event_id=event_id, event_title=event["title"],
                                   event_provider=scope["provider"], event_source=scope["data_source_id"], scheduled=1)
                else:
                    tracked.update(event_id=None, event_title=None, event_provider=None, event_source=None)
            elif "deadline" in local:
                # A manual due date replaces the event dependency. The event keeps its own time.
                tracked.update(event_id=None, event_title=None, event_provider=None, event_source=None, scheduled=1)
            target = tracked.get("goal_target", tracking.get("goal_target"))
            current = tracked.get("goal_current", tracking.get("goal_current", 0))
            if "goal_current" in changes and not target:
                raise ValueError("Set a numeric goal target before reporting progress.")
            if target and current > target:
                raise ValueError("Goal progress cannot exceed the target.")
            if target and tracked.keys() & {"goal_target", "goal_current"}:
                local["progress"] = current * 100 // target
            if "progress" in local:
                progress = local["progress"]
                local["status"] = "done" if progress == 100 else ("active" if progress else "todo")
                if target and not tracked.keys() & {"goal_target", "goal_current"}:
                    tracked["goal_current"] = target if progress == 100 else min(target - 1, (target * progress + 50) // 100)
            dirty = {k: v for k, v in local.items() if task[k] != v}
            if dirty:
                workload.save_item(dirty, task_id)
            save_details(db, task_id, tracked)
            schedule_changed = dirty.keys() & {"deadline", "title", "status"} or "scheduled" in tracked
            if (tracked.get("scheduled") or tracking.get("scheduled")) and (schedule_changed or tracking.get("calendar_error")):
                schedule.sync(db, task_id)
            return task_view(db, project_id, task_id)
        finally:
            db.close()


def refresh_task(project_id, task_id, retry=False):
    from . import schedule

    with LOCK:
        db = connect()
        try:
            ensure_tables(db)
            task = get_task(db, project_id, task_id)
            tracking = details(db, task_id)
            if tracking.get("event_id"):
                scope = {"provider": tracking["event_provider"], "data_source_id": tracking["event_source"]}
                try:
                    event, _ = schedule.get_event(tracking["event_id"], scope)
                except Exception as exc:
                    return {**task_view(db, project_id, task_id), "link_warning": str(exc)}
                if event["start"] != task["deadline"]:
                    workload.save_item({"deadline": event["start"]}, task_id)
                    retry = True
                if event["title"] != tracking["event_title"]:
                    save_details(db, task_id, {"event_title": event["title"]})
            if retry and tracking.get("scheduled"):
                schedule.sync(db, task_id)
            return task_view(db, project_id, task_id)
        finally:
            db.close()
