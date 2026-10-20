"""Persistent, narrowly scoped permissions for unattended Daily Automation.

Grants are explicit on schedule creation/edit, stored in the private local DB
and checked against the *live* schedule and connector grants before every write.
The model cannot set, expand, or bypass these permissions.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# Only audited, scoped tool classes may be preauthorized.
# In particular self-modifying automations, arbitrary inline Python, and
# bulk operations backed by an unchecked preview are not auto-approved.
PERMISSION_SPECS = {
    "notion_calendar_create": ("Add Notion calendar events", {}),
    "notion_calendar_update": ("Edit Notion calendar events", {}),
    "notion_calendar_archive": ("Archive Notion calendar events", {}),
    "mail_mark": ("Mark or archive mail", {"account_id": "Mail account ID", "action": "Exact action: mark_read / mark_unread / flag / unflag / archive"}),
    "mail_move": ("Move mail to a folder", {"account_id": "Mail account ID", "mailbox": "Exact destination mailbox"}),
    "mail_draft": ("Save draft emails", {"account_id": "Mail account ID", "recipient": "Exact recipient email address"}),
    "mail_send": ("Send emails", {"account_id": "Mail account ID", "recipient": "Exact recipient email address"}),
    "fs_write": ("Create files within folder", {"directory": "Absolute granted folder path"}),
    "fs_edit": ("Edit files within folder", {"directory": "Absolute granted folder path"}),
    "fs_move": ("Move files within folder", {"directory": "Absolute granted folder path"}),
    "run_tests": ("Run tests in project", {"cwd": "Exact granted project folder"}),
    "run_python": ("Run one script", {"cwd": "Exact execute-granted folder", "path": "Absolute script file path"}),
    "workload_add": ("Add tracked work items", {"project": "Exact project name (can be blank)"}),
    "workload_update": ("Edit one tracked work item", {"item_id": "Exact work item ID"}),
}
MAIL_ACTIONS = {"mark_read", "mark_unread", "flag", "unflag", "archive"}
MAIL_ADDRESS = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def catalog() -> list[dict[str, Any]]:
    from ..agent import tools
    available = tools.availability()
    return [
        {"tool": key, "label": label,
         "fields": [{"name": k, "label": v} for k, v in fields.items()],
         "available": bool(available.get(key, {}).get("available")),
         "reason": available.get(key, {}).get("reason", "Tool unavailable"),
         "high_risk": key in {"mail_send", "run_python", "notion_calendar_archive"}}
        for key, (label, fields) in PERMISSION_SPECS.items()
        if key in tools.TOOLS and tools.TOOLS[key]["mutating"]
    ]


def prepare(grants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from ..agent import tools
    from ..connectors.notion import state
    from ..agent import grants as filesystem_grants
    if not isinstance(grants, list) or len(grants) > 50:
        raise ValueError("Permissions must be an array of at most 50 specific grants")
    verified = []
    seen = set()
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) != {"tool", "scope"}:
            raise ValueError("Each permission needs a tool and an exact scope object")
        tool = grant["tool"]
        if tool not in PERMISSION_SPECS or tool not in tools.TOOLS or not tools.TOOLS[tool]["mutating"]:
            raise ValueError(f"Tool {tool!r} does not support unattended authorization")
        if not tools.availability()[tool]["available"]:
            raise ValueError(f"Tool {tool} is not currently available for authorization")
        spec_fields = PERMISSION_SPECS[tool][1]
        raw = grant["scope"]
        if not isinstance(raw, dict) or not (set(raw) == set(spec_fields) or (
            tool.startswith("notion_calendar_") and set(raw) == {"data_source_id"})):
            raise ValueError(f"Permission {tool} requires exactly: {', '.join(spec_fields) or 'no fields'}")
        if tool.startswith("notion_calendar_") and raw.get("data_source_id"):
            cfg = state.get_config() or {}
            if raw["data_source_id"] != cfg.get("data_source_id"):
                raise ValueError("Existing Notion permission targets another data source")
        scope = {}
        for key in spec_fields:
            val = raw[key]
            if not isinstance(val, str) or len(val) > 500 or (not val.strip() and key != "project"):
                raise ValueError(f"Invalid {key} for {tool}")
            scope[key] = val.strip()
        if tool.startswith("notion_calendar_"):
            cfg = state.get_config() or {}
            needed = "insert_verified" if tool == "notion_calendar_create" else "update_verified"
            if not cfg.get("data_source_id") or not cfg.get(needed) or not state.token_present():
                raise ValueError("Verify the required Notion write permission before authorizing this task")
            scope["data_source_id"] = cfg["data_source_id"]
        if tool == "mail_mark" and scope["action"] not in MAIL_ACTIONS:
            raise ValueError("Invalid mail action")
        if tool in {"mail_send", "mail_draft"} and not MAIL_ADDRESS.fullmatch(scope["recipient"]):
            raise ValueError("Use one exact valid recipient address")
        if tool in {"fs_write", "fs_edit", "fs_move", "run_python", "run_tests"}:
            key = "directory" if "directory" in scope else "cwd"
            folder = Path(scope[key]).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError(f"{key} must be an existing absolute folder")
            mode = "execute" if tool in {"run_python", "run_tests"} else "edit"
            filesystem_grants.resolve(str(folder), mode)
            scope[key] = str(folder.resolve())
            if tool == "run_python":
                script = Path(scope["path"]).expanduser()
                if not script.is_absolute() or not script.is_file():
                    raise ValueError("Authorize one existing absolute script path")
                filesystem_grants.resolve(str(script), "read")
                scope["path"] = str(script.resolve())
        canonical = json.dumps([tool, scope], sort_keys=True)
        if canonical not in seen:
            verified.append({"tool": tool, "scope": scope})
            seen.add(canonical)
    return verified


def _inside(path: str, root: str) -> bool:
    try:
        actual = str(Path(path).expanduser().resolve())
        return os.path.commonpath([actual, root]) == root
    except (ValueError, OSError, TypeError):
        return False


def _matches(permission: dict[str, Any], tool: str, args: dict[str, Any]) -> bool:
    if permission.get("tool") != tool:
        return False
    scope = permission.get("scope") or {}
    if tool.startswith("notion_calendar_"):
        from ..connectors.notion import state
        cfg = state.get_config() or {}
        needed = "insert_verified" if tool == "notion_calendar_create" else "update_verified"
        return bool(cfg.get("data_source_id") == scope.get("data_source_id")
                    and cfg.get(needed) and state.token_present())
    if tool == "mail_mark":
        return args.get("account_id") == scope.get("account_id") and args.get("action") == scope.get("action")
    if tool == "mail_move":
        return args.get("account_id") == scope.get("account_id") and args.get("mailbox") == scope.get("mailbox")
    if tool in {"mail_draft", "mail_send"}:
        return (args.get("account_id") == scope.get("account_id")
                and [str(v).casefold() for v in args.get("to", [])] == [scope.get("recipient", "").casefold()]
                and not args.get("reply_to_message_id"))
    if tool in {"fs_write", "fs_edit"}:
        return isinstance(args.get("path"), str) and _inside(args["path"], scope["directory"])
    if tool == "fs_move":
        return all(isinstance(args.get(k), str) and _inside(args[k], scope["directory"]) for k in ("src", "dst"))
    if tool == "run_tests":
        return isinstance(args.get("cwd"), str) and str(Path(args["cwd"]).expanduser().resolve()) == scope["cwd"]
    if tool == "run_python":
        return (isinstance(args.get("cwd"), str) and str(Path(args["cwd"]).expanduser().resolve()) == scope["cwd"]
                and isinstance(args.get("path"), str) and str(Path(args["path"]).expanduser().resolve()) == scope["path"]
                and not args.get("code") and not args.get("args"))
    if tool == "workload_add":
        return (args.get("project") or "") == scope.get("project", "")
    if tool == "workload_update":
        return args.get("item_id") == scope.get("item_id")
    return False


def authorize(schedule_id: str, revision: str, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Fail closed on paused/deleted/edited automation, revoked access or scope mismatch."""
    from ..runtime.db import connect, one
    from ..agent import tools
    if tool not in PERMISSION_SPECS or tool not in tools.TOOLS:
        return False, "Tool is not eligible for unattended execution"
    conn = connect()
    try:
        from .service import ensure_tables
        ensure_tables(conn)
        row = one(conn, "SELECT enabled,updated_at,permissions_json FROM daily_prompts WHERE id=?", (schedule_id,))
    finally:
        conn.close()
    if not row or not row["enabled"] or row["updated_at"] != revision:
        return False, "Automation was paused, deleted or edited; unattended grants revoked"
    try:
        policies = json.loads(row.get("permissions_json") or "[]")
        if not isinstance(policies, list):
            return False, "Malformed persisted grants"
        valid = any(_matches(p, tool, args) for p in policies)
        if not valid:
            return False, "Action or target not explicitly authorized for this automation"
        availability = tools.availability()
        if not availability.get(tool, {}).get("available"):
            return False, "Tool or connector permission is no longer available"
        return True, "Explicit persistent approval for this tool and target"
    except (ValueError, TypeError, KeyError, OSError):
        return False, "Authorization could not be verified"
