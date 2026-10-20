"""Read-only Project Manager tools available through normal PODA chat."""
from __future__ import annotations

from ..agent.tools import _schema, _str
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
        attachments = [{k: a.get(k) for k in ("filename", "kind", "size_bytes")}
                       for a in p.get("attachments", [])]
        tasks = [{k: t.get(k) for k in ("title", "description", "deadline", "status",
                 "importance", "difficulty", "progress", "estimate")}
                 for t in p.get("tasks", [])]
        return {"ok": True, "result": {"project": {k: v for k, v in p.items() if k not in ("tasks", "attachments")},
                    "tasks": tasks[:100], "attachments": attachments[:100]},
                "error": None, "verification": f"read project {p['name']} and {len(tasks)} linked tasks"}
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc), "verification": "no project data read"}


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
}
