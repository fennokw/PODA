"""User-reviewed homework requirements and task progress."""
from __future__ import annotations

import json
import re
from contextlib import closing
from uuid import uuid4

from ..runtime.db import connect, one, now_iso
from . import folders, schedule


def read(db, task_id: str) -> dict:
    record = one(db, "SELECT items_json,revision FROM pm_homework_checklists WHERE task_id=?", (task_id,))
    if not record:
        return {"items": [], "revision": 0}
    return {"items": json.loads(record["items_json"]), "revision": record["revision"]}


def scope(db, session_id: str):
    current = folders.context(db, session_id)
    if not current or not current["task_id"]:
        raise KeyError("File this Work Discussion under a task first.")
    return current


def update(session_id: str, revision: int, labels: list[str] | None,
           item_id: str | None, completed: bool | None):
    with folders.LOCK, closing(connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        current = scope(db, session_id)
        task_id = current["task_id"]
        previous = read(db, task_id)
        if revision != previous["revision"]:
            raise RuntimeError("Progress changed in another view. Refresh the checklist and try again.")
        items = previous["items"]
        if labels is not None:
            cleaned = [label.strip() for label in labels]
            if (not 1 <= len(cleaned) <= 50 or any(not 1 <= len(label) <= 240 for label in cleaned)
                    or len({label.casefold() for label in cleaned}) != len(cleaned)):
                raise ValueError("Use 1–50 distinct requirements, up to 240 characters each.")
            by_label = {item["label"].casefold(): item for item in items}
            items = [by_label.get(label.casefold()) or {"id": str(uuid4()), "label": label, "done": False}
                     for label in cleaned]
        else:
            item = next((entry for entry in items if entry["id"] == item_id), None)
            if item is None or completed is None:
                raise ValueError("Choose an existing requirement and specify its completion state.")
            item["done"] = completed
        done = sum(item["done"] for item in items)
        progress = 100 * done // len(items)
        status = "done" if progress == 100 else "active" if progress else "todo"
        db.execute("""INSERT INTO pm_homework_checklists(task_id,items_json,revision) VALUES (?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET items_json=excluded.items_json,revision=excluded.revision""",
            (task_id, json.dumps(items, ensure_ascii=False), revision + 1))
        task = folders.get_task(db, current["project_id"], task_id)
        tracking = folders.details(db, task_id)
        db.execute("UPDATE workload_items SET progress=?,status=?,updated_at=? WHERE id=?",
                   (progress, status, now_iso(), task_id))
        if tracking.get("goal_target"):
            target = tracking["goal_target"]
            db.execute("UPDATE pm_task_details SET goal_current=? WHERE task_id=?",
                       (target if status == "done" else min(target - 1, target * progress // 100), task_id))
        db.commit()
        if tracking.get("scheduled") and (task["status"] != status or tracking.get("calendar_error")):
            schedule.sync(db, task_id)
        return {**read(db, task_id), "progress": progress, "status": status,
                "calendar_error": folders.details(db, task_id).get("calendar_error")}

_CONFIRM = re.compile(r"^(?:please\s+)?(?:mark\s+(.+?)\s+(?:as\s+)?(?:done|complete)|i\s+(?:finished|completed)\s+(.+?))[.!]?$", re.I)


def confirm_from_chat(session_id: str, message: str) -> str:
    match = _CONFIRM.fullmatch(message.strip())
    if not match:
        return ""
    label = " ".join((match[1] or match[2]).lower().split())
    with closing(connect()) as db:
        current = scope(db, session_id)
        state = read(db, current["task_id"])
    if not state["items"]:
        return ""
    matches = [item for item in state["items"] if item["label"].lower() == label or
               item["label"].lower().startswith(label + " ") or item["label"].lower().startswith(label + ":")]
    if len(matches) != 1:
        return "Select the exact checklist item in Chat details so PODA can update the right requirement."
    item = matches[0]
    if item["done"]:
        return f'{item["label"]} is already marked complete.'
    try:
        result = update(session_id, state["revision"], None, item["id"], True)
    except RuntimeError as exc:
        return str(exc)
    text = f'Marked {item["label"]} complete. The task is {result["progress"]}% complete.'
    if result["calendar_error"]:
        text += " Calendar sync needs attention: " + result["calendar_error"]
    return text
