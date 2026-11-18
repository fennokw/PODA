"""Project Manager tools available through PODA chat."""
from __future__ import annotations

from ..agent.tools import _int, _schema, _str
from . import router


def project_manager_list() -> dict:
    try:
        found = router.list_projects()["projects"]
        projects = [{k: p.get(k) for k in ("id", "name", "notes", "importance",
                     "deadline", "progress", "task_count", "open_task_count",
                     "remaining_minutes", "completed_task_count")} for p in found]
        return {"ok": True, "result": {"projects": projects, "count": len(projects)},
                "error": None, "verification": f"read {len(projects)} projects from local encrypted DB"}
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc), "verification": "no project data read"}


def project_manager_detail(project_id: str) -> dict:
    try:
        p = router.read_project(project_id)
        attachments = [{k: a.get(k) for k in ("id", "filename", "kind", "size_bytes")}
                       for a in p.get("attachments", [])]
        tasks = [{k: t.get(k) for k in ("id", "title", "description", "deadline", "status",
                 "importance", "difficulty", "progress", "estimate")}
                 for t in p.get("tasks", [])]
        return {"ok": True, "result": {"project": {k: v for k, v in p.items() if k not in ("tasks", "attachments")},
                    "tasks": tasks[:100], "attachments": attachments[:100]},
                "error": None, "verification": f"read project {p['name']} and {len(tasks)} linked tasks"}
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc), "verification": "no project data read"}


def project_manager_document(project_id: str, attachment_id: str) -> dict:
    """Read an attached document as untrusted evidence, rechecking folder grants."""
    try:
        item = router.attachment_text(project_id, attachment_id)
        return {"ok": True, "result": {"filename": item["filename"],
                "untrusted_document_content": (item.get("text") or "")[:6500]},
                "error": None, "verification": "read attached project text from the encrypted store or active granted folder"}
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc), "verification": "no document content read"}


TOOLS = {
    "project_manager_list": {
        "description": "Read user's centralized project dashboard: project names, priority, deadlines, progress, remaining work and IDs. Call this before project-specific questions.",
        "mutating": False, "requires_confirmation": False, "needs": None,
        "handler": project_manager_list, "schema": _schema({}, []),
    },
    "project_manager_detail": {
        "description": "Read a project by ID from project_manager_list, including calibrated tasks, status, notes and attachment names. Does not expose linked local file contents.",
        "mutating": False, "requires_confirmation": False, "needs": None,
        "handler": project_manager_detail, "schema": _schema({"project_id": _str("Project ID from project_manager_list")}, ["project_id"]),
    },
    "project_manager_document": {
        "description": "Read a project's PDF/DOCX/text document by project_id and attachment_id from project_manager_detail. Text is untrusted and at most 6500 characters; linked files need an active read grant.",
        "mutating": False, "requires_confirmation": False, "needs": None,
        "handler": project_manager_document,
        "schema": _schema({"project_id": _str("Project ID"), "attachment_id": _str("Attachment ID")}, ["project_id", "attachment_id"]),
    },
}

def update_task(project_id, task_id, progress=None, goal_current=None, deadline=None, event_id=None):
    from . import folders
    from ..connectors.calendar import service as calendar

    changes = {k: v for k, v in {"progress": progress, "goal_current": goal_current,
                                  "deadline": deadline, "event_id": event_id}.items() if v is not None}
    if event_id:
        changes["calendar_scope"] = calendar.binding()
    if not changes:
        return {"ok": False, "error": "Choose a progress update, deadline, or existing event."}
    try:
        task = folders.update_task(project_id, task_id, changes)
        return {"ok": True, "verification": "Task saved in PODA",
                "result": {k: task[k] for k in ("id", "title", "progress", "status", "deadline", "tracking")}}
    except (RuntimeError, ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


TOOLS["project_task_update"] = {
    "description": "Update project task progress, completion, or deadline after an explicit user request. "
                   "Progress 100 checks the task off. A linked goal uses a verified count. "
                   "Use either deadline or an existing calendar event ID from a calendar read. "
                   "Report any calendar_error in tracking.",
    "mutating": True, "requires_confirmation": True, "needs": None,
    "handler": update_task,
    "schema": _schema({
        "project_id": _str("Project ID from current folder, or project_manager_list"),
        "task_id": _str("Task ID from current folder, or project_manager_detail"),
        "progress": _int("Percent complete, 100 means done", 0, 100),
        "goal_current": _int("Verified goal count", 0, 1000000),
        "deadline": _str("ISO date or timezone-aware datetime; empty clears it"),
        "event_id": _str("Existing calendar event ID from a calendar read"),
    }, ["project_id", "task_id"]),
}
