"""Tool-based workload access so chat can use the same calibrated planner as the UI."""
from __future__ import annotations

from typing import Any

from ..agent.tools import _schema, _str, _int, _bool
from . import service


def _ok(data: Any, verification: str) -> dict[str, Any]:
    return {"ok": True, "result": data, "error": None, "verification": verification}


def _fail(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "result": None, "error": str(exc), "verification": "no update performed"}


def workload_list(include_done: bool = False) -> dict[str, Any]:
    items = service.all_items(include_done)
    return _ok({"items": [{k: v for k, v in x.items() if k != "description"} for x in items],
                "count": len(items)}, f"read {len(items)} local workload items")


def workload_estimate(title: str, category: str = "general", difficulty: int = 5,
                      description: str = "") -> dict[str, Any]:
    try:
        normalized = service.normalize({"title": title, "category": category, "difficulty": difficulty,
                                        "description": description})
        result = service.estimate(normalized)
        return _ok({"title": title, "estimate": result}, "computed calibrated estimate from local user examples")
    except ValueError as exc:
        return _fail(exc)


def workload_schedule(days: int = 14, check_calendar: bool = True) -> dict[str, Any]:
    try:
        plan = service.schedule(days=days, calendar=check_calendar)
        return _ok(plan, f"drafted {len(plan['sessions'])} work blocks; no calendar changes")
    except ValueError as exc:
        return _fail(exc)


def workload_breakdown(item_id: str) -> dict[str, Any]:
    try:
        return _ok(service.breakdown(item_id), "generated editable task phases; no writes")
    except KeyError as exc:
        return _fail(exc)


def workload_add(title: str, description: str = "", project: str = "", category: str = "general",
                 difficulty: int = 5, importance: int = 5, deadline: str | None = None,
                 user_minutes: int | None = None) -> dict[str, Any]:
    try:
        item = service.save_item({"title": title, "description": description, "project": project,
                                  "category": category, "difficulty": difficulty, "importance": importance,
                                  "deadline": deadline, "user_minutes": user_minutes})
        return _ok({"item_id": item["id"], "title": item["title"], "estimate": item["estimate"]},
                   "confirmed item exists in local encrypted work database")
    except ValueError as exc:
        return _fail(exc)


def workload_update(item_id: str, title: str | None = None, status: str | None = None,
                    progress: int | None = None, user_minutes: int | None = None,
                    actual_minutes: int | None = None, deadline: str | None = None) -> dict[str, Any]:
    changes = {k: v for k, v in {"title": title, "status": status, "progress": progress,
                                "user_minutes": user_minutes, "actual_minutes": actual_minutes,
                                "deadline": deadline}.items() if v is not None}
    if not changes:
        return _fail(ValueError("Provide at least one changed field"))
    try:
        item = service.save_item(changes, item_id)
        return _ok({"item_id": item["id"], "title": item["title"], "estimate": item["estimate"]},
                   "confirmed updates persisted in local workload database")
    except (KeyError, ValueError) as exc:
        return _fail(exc)


DESCRIPTION = ("Workload estimates are personal, explainable predictions, not exact. "
               "When asked about deadlines, homework, project progress, study plans or scheduling, "
               "use workload_list or workload_schedule. Existing user/actual-time examples calibrate "
               "similar work types and difficulty. Scheduling never writes to Notion.")

TOOLS = {
    "workload_list": {"description": "List the current tracked homework, work, and projects with calibrated time estimates. " + DESCRIPTION,
                      "handler": workload_list, "mutating": False, "requires_confirmation": False,
                      "needs": None, "schema": _schema({"include_done": _bool("Include finished tasks")}, [])},
    "workload_estimate": {"description": "Estimate duration of a described new task using personal category/difficulty calibration, without saving it.",
                          "handler": workload_estimate, "mutating": False, "requires_confirmation": False,
                          "needs": None, "schema": _schema({"title": _str("Short task title"), "category": _str("general|homework|study|writing|coding|research|admin|creative|reading"),
                              "difficulty": _int("Difficulty 1 to 10", 1, 10), "description": _str("What needs doing")},
                              ["title"])},
    "workload_schedule": {"description": "Propose work blocks ordered by deadlines, priority and remaining effort, checking live Notion calendar conflicts when available; never writes events.",
                          "handler": workload_schedule, "mutating": False, "requires_confirmation": False,
                          "needs": None, "schema": _schema({"days": _int("Schedule horizon 1–31 days", 1, 31),
                                                            "check_calendar": _bool("Check live Notion events if connected")}, [])},
    "workload_breakdown": {"description": "Break a saved work item into preparation, execution and review phases.",
                           "handler": workload_breakdown, "mutating": False, "requires_confirmation": False,
                           "needs": None, "schema": _schema({"item_id": _str("Workload item id from workload_list")}, ["item_id"])},
    "workload_add": {"description": "Add a new task or homework item locally. Requires explicit user approval.",
                     "handler": workload_add, "mutating": True, "requires_confirmation": True,
                     "needs": None, "schema": _schema({"title": _str("Task title"), "description": _str("Detailed work"),
                              "project": _str("Project/course grouping"), "category": _str("general|homework|study|writing|coding|research|admin|creative|reading"),
                              "difficulty": _int("Difficulty 1–10", 1, 10), "importance": _int("Priority 1–10", 1, 10),
                              "deadline": _str("YYYY-MM-DD or ISO datetime"), "user_minutes": _int("User's own predicted minutes", 1, 200000)}, ["title"])},
    "workload_update": {"description": "Update a saved task's progress, status, deadline, estimate or actual time. Requires explicit user approval.",
                        "handler": workload_update, "mutating": True, "requires_confirmation": True,
                        "needs": None, "schema": _schema({"item_id": _str("Existing work item id"), "title": _str("New title"),
                              "status": _str("todo|active|paused|done"), "progress": _int("Completion percent", 0, 100),
                              "user_minutes": _int("User predicted minutes", 1, 200000),
                              "actual_minutes": _int("Actual elapsed minutes measured/reported by user", 1, 200000),
                              "deadline": _str("YYYY-MM-DD or ISO datetime")}, ["item_id"])},
}
