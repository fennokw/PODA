"""Tool registry with strict argument validation, receipts, and availability derived from live grants/connectors."""
from __future__ import annotations

import json
from typing import Any, Callable

from ..runtime.db import connect
from . import fs_tools, exec_tools, grants, receipts


def _memory_search(query: str, limit: int = 10) -> dict[str, Any]:
    from ..memory.retrieval import recall
    out = recall(query, limit=max(1, min(int(limit), 30)))
    return {"ok": True, "result": {"mode": out.get("mode"), "results": [{k: r.get(k) for k in ("id", "title", "summary", "provenance", "score", "created_at")} for r in out.get("results", [])]},
            "error": None, "verification": f"{len(out.get('results', []))} memories ranked via {out.get('mode')}"}


_ITEM_KEYS = ("summary", "title", "kind", "is_deadline", "local_date", "start_time", "end_time", "all_day", "course", "source_name", "commitment", "areas", "import_source", "source_status", "page_id")


def _compact(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only locally-interpreted fields; raw UTC timestamps are deliberately dropped so the model cannot misread them."""
    return [{k: it.get(k) for k in _ITEM_KEYS if it.get(k) not in (None, "", [], {})} for it in items]


def _notion_query_items(start: str | None = None, end: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Calendar-aware read of the connected Notion database. With start/end (local YYYY-MM-DD) returns interpreted items in local time."""
    try:
        from ..connectors.notion import mapping as notion_mapping  # type: ignore
        from ..connectors.notion.service import query_pages  # type: ignore
    except Exception as exc:
        return {"ok": False, "result": None, "error": f"Notion connector unavailable: {exc}", "verification": "no Notion request made"}
    try:
        if start or end:
            start = start or notion_mapping.local_day(0)
            end = end or start
            data = notion_mapping.items_between_effective(start, end)
            data["items"] = _compact(data["items"][:max(1, min(int(limit), 200))])
            data["note"] = f"All times are local ({data['time_zone']}). Items marked is_deadline are Canvas homework/tests due that day; others are classes/events."
            return {"ok": True, "result": data, "error": None,
                    "verification": f"read {data['count']} live Notion items dated {start}..{end} (local {data['time_zone']}; date property '{data['mapping']['date']}'{', inferred mapping' if data['mapping']['inferred'] else ''})"}
        pages = query_pages(limit=max(1, min(int(limit), 200)))
        m = notion_mapping.effective_mapping()
        items = _compact([notion_mapping.interpret_item(p, m) for p in pages]) if m else pages
        return {"ok": True, "result": {"count": len(items), "items": items}, "error": None, "verification": f"read {len(items)} live Notion pages (no date filter)"}
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc), "verification": "Notion request failed"}


def _notion_agenda(day: str = "today", include_deadlines: bool = True) -> dict[str, Any]:
    """Agenda for 'today' | 'tomorrow' | 'yesterday' | 'this week' | 'next week' | 'weekend' | YYYY-MM-DD, in local time."""
    try:
        from ..connectors.notion import mapping as notion_mapping  # type: ignore
        start, end = notion_mapping.resolve_day_word(day)
        data = notion_mapping.items_between_effective(start, end)
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc), "verification": "Notion request failed"}
    events = [i for i in data["items"] if not i.get("is_deadline")]
    deadlines = [i for i in data["items"] if i.get("is_deadline")]
    lines = [i["summary"] for i in events] + ([i["summary"] for i in deadlines] if include_deadlines else [])
    covers = f"{day} = {start}" + (f" to {end}" if end != start else "")
    return {"ok": True, "result": {"covers": covers, "agenda_text": "\n".join(lines) or f"Nothing scheduled or due on {covers} — the calendar is clear.",
                                   "events": _compact(events), "deadlines": _compact(deadlines) if include_deadlines else [], "time_zone": data["time_zone"],
                                   "note": "Times are local. Report events/classes with their start–end times and deadlines as 'due <time>'."},
            "error": None, "verification": f"read {data['count']} live Notion items for {start}..{end} ({len(events)} events, {len(deadlines)} deadlines)"}


def _memory_dive(surface_id: str | None = None, query: str | None = None, limit: int = 6) -> dict[str, Any]:
    from ..memory.surface import dive
    if not surface_id and not query:
        return {"ok": False, "result": None, "error": "provide surface_id or query", "verification": "no lookup performed"}
    out = dive(surface_id=surface_id, query=query, limit=max(1, min(int(limit), 8)))
    n = len(out.get("members", []))
    return {"ok": True, "result": out, "error": None, "verification": f"loaded {n} in-depth exchange(s) from surface {str((out.get('surface') or {}).get('id') or '')[:8] or 'none'}"}



def _list_capabilities() -> dict[str, Any]:
    from ..runtime.capability import manifest
    cap = manifest()
    return {"ok": True, "result": {k: cap[k] for k in ("filesystem", "code", "notion", "calendar", "email", "ollama", "offline_mode")}, "error": None, "verification": "live ledger probe"}


def _str(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **kw}


def _int(desc: str, lo: int, hi: int) -> dict[str, Any]:
    return {"type": "integer", "description": desc, "minimum": lo, "maximum": hi}


def _bool(desc: str) -> dict[str, Any]:
    return {"type": "boolean", "description": desc}


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: dict[str, dict[str, Any]] = {
    "fs_list": {"description": "List files and folders inside a granted folder (depth ≤ 2).", "mutating": False, "requires_confirmation": False, "needs": "fs_read",
                "handler": fs_tools.fs_list, "schema": _schema({"path": _str("Absolute path inside a granted folder"), "depth": _int("Recursion depth 0-2", 0, 2)}, ["path"])},
    "fs_read": {"description": "Read a UTF-8 text file inside a granted folder; returns content and sha256.", "mutating": False, "requires_confirmation": False, "needs": "fs_read",
                "handler": fs_tools.fs_read, "schema": _schema({"path": _str("Absolute file path"), "max_bytes": _int("Maximum bytes to return", 1, 200000)}, ["path"])},
    "fs_search": {"description": "Regex search across text files inside a granted folder.", "mutating": False, "requires_confirmation": False, "needs": "fs_read",
                  "handler": fs_tools.fs_search, "schema": _schema({"root": _str("Folder to search"), "pattern": _str("Python regular expression"), "glob": _str("Glob filter, default **/*"), "max_results": _int("Max hits", 1, 200)}, ["root", "pattern"])},
    "fs_write": {"description": "Create a text file inside a folder granted edit access. Refuses to overwrite unless overwrite=true.", "mutating": True, "requires_confirmation": True, "needs": "fs_write",
                 "handler": fs_tools.fs_write, "schema": _schema({"path": _str("Absolute file path to create"), "content": _str("Full UTF-8 file content"), "overwrite": _bool("Allow replacing an existing file (a backup is kept)")}, ["path", "content"])},
    "fs_edit": {"description": "Replace an exact text span in a file after validating its sha256 (read the file first).", "mutating": True, "requires_confirmation": True, "needs": "fs_write",
                "handler": fs_tools.fs_edit, "schema": _schema({"path": _str("Absolute file path"), "expected_sha256": _str("sha256 returned by fs_read"), "old_text": _str("Exact text to replace"), "new_text": _str("Replacement text"), "replace_all": _bool("Replace every occurrence")}, ["path", "expected_sha256", "old_text", "new_text"])},
    "fs_move": {"description": "Move or rename a file within granted folders.", "mutating": True, "requires_confirmation": True, "needs": "fs_write",
                "handler": fs_tools.fs_move, "schema": _schema({"src": _str("Source path"), "dst": _str("Destination path")}, ["src", "dst"])},
    "run_python": {"description": "Run a Python script or snippet inside a folder granted execute access; returns real exit code and bounded output.", "mutating": True, "requires_confirmation": True, "needs": "execute",
                   "handler": exec_tools.run_python, "schema": _schema({"cwd": _str("Working directory inside an execute-enabled grant"), "path": _str("Script path (optional if code given)"), "code": _str("Inline Python code (optional if path given)"),
                                                                       "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments"}, "timeout": _int("Seconds", 1, 120)}, ["cwd"])},
    "run_tests": {"description": "Run pytest inside a folder granted execute access.", "mutating": True, "requires_confirmation": True, "needs": "execute",
                  "handler": exec_tools.run_tests, "schema": _schema({"cwd": _str("Project directory"), "args": {"type": "array", "items": {"type": "string"}, "description": "pytest args"}, "timeout": _int("Seconds", 1, 120)}, ["cwd"])},
    "memory_search": {"description": "Search PODA's local second brain (hybrid lexical + semantic).", "mutating": False, "requires_confirmation": False, "needs": None,
                      "handler": _memory_search, "schema": _schema({"query": _str("Search text"), "limit": _int("Max results", 1, 30)}, ["query"])},
    "memory_dive": {"description": "Read the full question→response exchanges stored inside one surface memory (by surface_id, or the best surface for a query). Use when a surface memory is relevant but too compressed.",
                    "mutating": False, "requires_confirmation": False, "needs": None, "handler": _memory_dive,
                    "schema": _schema({"surface_id": _str("Surface memory id (from recalled memories)"), "query": _str("Topic to look up when no surface_id is known"), "limit": _int("Max exchanges", 1, 8)}, [])},
    "notion_query_items": {"description": "Read the connected Notion calendar/task database. Pass start and end as local YYYY-MM-DD to get events, classes, and deadlines in local time (Canvas due times are converted from UTC).", "mutating": False, "requires_confirmation": False, "needs": "notion_read",
                           "handler": _notion_query_items, "schema": _schema({"start": _str("Local date lower bound YYYY-MM-DD"), "end": _str("Local date upper bound YYYY-MM-DD"), "limit": _int("Max items", 1, 200)}, [])},
    "notion_agenda": {"description": "Agenda from the connected Notion calendar for a day word: today, tomorrow, yesterday, this week, next week, weekend, or YYYY-MM-DD. Returns events/classes with local times and deadlines separately. Use this for 'what do I have on…' questions.", "mutating": False, "requires_confirmation": False, "needs": "notion_read",
                      "handler": _notion_agenda, "schema": _schema({"day": _str("today | tomorrow | yesterday | this week | next week | weekend | YYYY-MM-DD"), "include_deadlines": _bool("Include homework/test due items")}, ["day"])},
    "list_capabilities": {"description": "Return PODA's live verified capability state.", "mutating": False, "requires_confirmation": False, "needs": None,
                          "handler": _list_capabilities, "schema": _schema({}, [])},
}

# Connector-provided tools plug in here without editing this file. A connector module exposes `TOOLS` (same shape as
# above) and `NEEDS`: {need_key: (predicate(snapshot_or_None) -> bool, reason_when_unavailable)}.
EXTENSION_NEEDS: dict[str, tuple[Any, str]] = {}


def register_extension(extra_tools: dict[str, dict[str, Any]], needs: dict[str, tuple[Any, str]] | None = None) -> None:
    for name, spec in extra_tools.items():
        TOOLS[name] = spec
    for key, value in (needs or {}).items():
        EXTENSION_NEEDS[key] = value


def _load_extensions() -> None:
    try:
        from ..connectors.email import tools as email_tools  # optional; connector may not be installed yet
        register_extension(getattr(email_tools, "TOOLS", {}), getattr(email_tools, "NEEDS", {}))
    except Exception:
        pass


_TYPE_MAP = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


class ToolValidationError(ValueError):
    pass


def validate_args(name: str, args: Any) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if not spec:
        raise ToolValidationError(f"Unknown tool: {name}")
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            raise ToolValidationError(f"Arguments are not valid JSON: {exc}")
    if not isinstance(args, dict):
        raise ToolValidationError("Arguments must be an object")
    schema = spec["schema"]
    props = schema["properties"]
    unknown = set(args) - set(props)
    if unknown:
        raise ToolValidationError(f"Unknown argument(s) for {name}: {sorted(unknown)}")
    for req in schema["required"]:
        if req not in args or args[req] in (None, ""):
            raise ToolValidationError(f"Missing required argument '{req}' for {name}")
    clean: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        expected = _TYPE_MAP[props[key]["type"]]
        if expected is int and isinstance(value, bool):
            raise ToolValidationError(f"Argument '{key}' must be an integer")
        if expected is int and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        if expected is bool and isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        if not isinstance(value, expected):
            raise ToolValidationError(f"Argument '{key}' must be {props[key]['type']}")
        if expected is int:
            # Deterministic clamping: small local models often pass 0 or huge limits; ranges are safety bounds, not intent.
            lo, hi = props[key].get("minimum"), props[key].get("maximum")
            if lo is not None and value < lo:
                if key in schema["required"]:
                    raise ToolValidationError(f"Argument '{key}' must be at least {lo}")
                continue  # junk like max_bytes=0 → fall back to the handler default
            if hi is not None and value > hi:
                value = hi
        if expected is list and not all(isinstance(v, str) for v in value):
            raise ToolValidationError(f"Argument '{key}' must be a list of strings")
        clean[key] = value
    return clean


def availability(snapshot: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    fs = grants.filesystem_capability()
    notion = (snapshot or {}).get("notion") or {}
    if snapshot is None:
        from ..connectors.notion import state as notion_state
        notion = notion_state.capability()
    out = {}
    for name, spec in TOOLS.items():
        need = spec["needs"]
        if need is None:
            ok, reason = True, "always available"
        elif need == "fs_read":
            ok, reason = fs["general_user_file_read"], "requires a granted folder"
        elif need == "fs_write":
            ok, reason = fs["general_user_file_edit"], "requires a folder granted edit or temp access"
        elif need == "execute":
            ok, reason = fs["code_execution"], "requires a grant with execution allowed"
        elif need == "notion_read":
            ok, reason = bool(notion.get("can_read_content")), "requires a connected Notion data source with verified read"
        elif need in EXTENSION_NEEDS:
            predicate, why = EXTENSION_NEEDS[need]
            try:
                ok = bool(predicate(snapshot))
            except Exception:
                ok = False
            reason = why
        else:
            ok, reason = False, "unknown prerequisite"
        out[name] = {"available": bool(ok), "reason": "available" if ok else reason}
    return out


def ollama_tool_specs(snapshot: dict[str, Any] | None = None, only_available: bool = True) -> list[dict[str, Any]]:
    avail = availability(snapshot)
    specs = []
    for name, spec in TOOLS.items():
        if only_available and not avail[name]["available"]:
            continue
        specs.append({"type": "function", "function": {"name": name, "description": spec["description"], "parameters": spec["schema"]}})
    return specs


def describe(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    avail = availability(snapshot)
    return [{"name": n, "description": s["description"], "parameters": s["schema"], "requires_confirmation": s["requires_confirmation"], "mutating": s["mutating"], **avail[n]} for n, s in TOOLS.items()]


def summarize_args(name: str, args: dict[str, Any]) -> str:
    parts = []
    for k, v in args.items():
        text = v if isinstance(v, str) else json.dumps(v)
        if k in {"content", "code", "new_text", "old_text"}:
            text = f"<{len(str(v))} chars>"
        parts.append(f"{k}={str(text)[:80]}")
    return f"{name}(" + ", ".join(parts) + ")"


def _target_of(name: str, args: dict[str, Any]) -> str | None:
    for key in ("path", "root", "cwd", "src", "query"):
        if key in args:
            return str(args[key])[:300]
    return None


def execute(name: str, args: Any, approved: bool = False, session_id: str | None = None) -> dict[str, Any]:
    """Validate → receipt → run → verify. Mutating tools require explicit approval."""
    try:
        clean = validate_args(name, args)
    except ToolValidationError as exc:
        rid = receipts.open_receipt(name, None, {"raw_args": str(args)[:500]}, approved, session_id)
        receipt = receipts.close_receipt(rid, "blocked", "argument validation failed", error=str(exc))
        return {"receipt": receipt, "result": {"ok": False, "result": None, "error": str(exc), "verification": "not executed"}}
    spec = TOOLS[name]
    rid = receipts.open_receipt(name, _target_of(name, clean), clean, approved, session_id)
    if spec["mutating"] and not approved:
        receipt = receipts.close_receipt(rid, "blocked", "awaiting explicit user approval", error="mutating tool requires approval")
        return {"receipt": receipt, "result": {"ok": False, "result": None, "error": "mutating tool requires approval", "verification": "not executed"}}
    avail = availability()[name]
    if not avail["available"]:
        receipt = receipts.close_receipt(rid, "blocked", "prerequisite missing", error=avail["reason"])
        return {"receipt": receipt, "result": {"ok": False, "result": None, "error": avail["reason"], "verification": "not executed"}}
    try:
        result = spec["handler"](**clean)
    except PermissionError as exc:
        result = {"ok": False, "result": None, "error": str(exc), "verification": "blocked by grant policy"}
    except Exception as exc:
        result = {"ok": False, "result": None, "error": f"{exc.__class__.__name__}: {exc}", "verification": "tool raised an exception"}
    if result.get("ok"):
        outcome = "succeeded"
    elif "No active grant" in str(result.get("error") or "") or "blocked by grant" in str(result.get("verification") or ""):
        outcome = "blocked"
    else:
        outcome = "failed"
    detail = json.dumps({k: v for k, v in (result.get("result") or {}).items() if k not in {"content", "entries", "hits", "stdout", "stderr", "pages"}} if isinstance(result.get("result"), dict) else result.get("result"), default=str)[:3000]
    receipt = receipts.close_receipt(rid, outcome, result.get("verification"), detail=detail, error=result.get("error"))
    return {"receipt": receipt, "result": result}


_load_extensions()
