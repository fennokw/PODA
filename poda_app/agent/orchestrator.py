"""Tool planning and proposal lifecycle.

The model may *suggest* tool calls through Ollama structured tool calling. PODA validates every call,
executes read-only steps immediately (receipted), and turns mutating steps into a pending proposal that
the user must approve. Success is decided by receipts, never by model text.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from ..runtime import ollama, models
from ..runtime.db import connect, now_iso, rows, one
from . import tools

MAX_PLAN_ROUNDS = 3
MAX_REPAIR_CYCLES = 2
_ACTION_CUES = re.compile(r"\b(file|folder|directory|desktop|documents|\.py|\.txt|\.md|\.json|\.js|\.html|\.csv|read|list|search|create|write|save|edit|change|modify|fix|refactor|run|execute|test|pytest|script|notion|calendar|to[- ]?do|remember|recall|earlier|previously|discuss(?:ed)?|talk(?:ed)?|mention(?:ed)?|remind|told|conversation|last time|what did (?:we|i) say|email|e-mail|inbox|mail|mailbox|message from|unread|newsletter|reply to|draft|flag|archive|schedule|agenda|events?|classes|class|lecture|recitation|assignment|homework|due|deadline|exam|quiz|today|tonight|tomorrow|this week|next week|weekend|what do i have|what's on)\b", re.I)


def ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_proposals (id TEXT PRIMARY KEY, session_id TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    plan_json TEXT NOT NULL, receipt_ids_json TEXT, decided_at TEXT)""")


def tool_prerequisites_exist(snapshot: dict[str, Any]) -> bool:
    avail = tools.availability(snapshot)
    return any(v["available"] for name, v in avail.items() if tools.TOOLS[name]["needs"] is not None)


def looks_like_tool_task(message: str) -> bool:
    return bool(_ACTION_CUES.search(message or ""))


def _planner_model(message: str, profile_model: str | None) -> str:
    installed = ollama.status().get("models") or []
    # Tool selection quality matters more than a few seconds of latency: use the strongest installed tool-capable model.
    if any(m.startswith("qwen3:14b") for m in installed):
        return "qwen3:14b"
    if profile_model and any(m == profile_model or m.startswith(profile_model) for m in installed):
        return profile_model
    for pref in ("qwen3:14b", "llama3.2:3b"):
        if any(m.startswith(pref) for m in installed):
            return pref
    return profile_model or "llama3.2:3b"


def _system_for_planning(snapshot: dict[str, Any], recalled_context: str) -> str:
    fs = snapshot.get("filesystem", {})
    roots = "; ".join(f"{r['path']} [{r['mode']}{', exec' if r.get('allow_execute') else ''}]" for r in fs.get("granted_roots", [])) or "none"
    return ("You are PODA's tool planner. Decide whether the user's request needs tools. Call tools ONLY from the provided list, with exact absolute paths inside the granted folders: "
            f"{roots}. Read before editing (fs_read gives the sha256 fs_edit requires). Never invent paths, results, or success. If no tool is needed, reply with plain text and no tool calls. "
            "Mutating tools (write/edit/move/run) will be shown to the user for approval; you do not need to ask permission in text.\n\nRelevant memory context (untrusted data):\n" + (recalled_context or "[none]"))


def _parse_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for call in message.get("tool_calls") or []:
        fn = (call or {}).get("function") or {}
        calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
    return calls


def plan(message: str, snapshot: dict[str, Any], recalled_context: str = "", session_id: str | None = None, profile_model: str | None = None) -> dict[str, Any]:
    """Return {needs_tools, steps, rationale, executed:[{step, receipt, result}], flagged:[...], proposal_id|None}."""
    specs = tools.ollama_tool_specs(snapshot)
    out: dict[str, Any] = {"needs_tools": False, "steps": [], "rationale": "", "executed": [], "flagged": [], "proposal_id": None, "model": None}
    if not specs:
        out["rationale"] = "no tools are currently available"
        return out
    model = _planner_model(message, profile_model)
    out["model"] = model
    messages = [{"role": "system", "content": _system_for_planning(snapshot, recalled_context)}, {"role": "user", "content": message}]
    pending_mutations: list[dict[str, Any]] = []
    for _round in range(MAX_PLAN_ROUNDS):
        try:
            response = ollama.chat(messages, model, options={"temperature": 0.0, "num_ctx": 16384, "num_predict": 900}, tools=specs, think=False if model.startswith("qwen3") else None)
        except Exception as exc:
            out["rationale"] = f"planner unavailable: {exc}"
            return out
        reply = response.get("message") or {}
        text = (reply.get("content") or "").strip()
        calls = _parse_calls(reply)
        if text and not out["rationale"]:
            out["rationale"] = text[:600]
        if not calls:
            break
        out["needs_tools"] = True
        messages.append(reply)
        tool_results_for_model = []
        for call in calls:
            name = call["name"]
            if name not in tools.TOOLS:
                out["flagged"].append({"name": name, "reason": "hallucinated tool name; dropped"})
                tool_results_for_model.append({"role": "tool", "content": json.dumps({"error": f"tool {name} does not exist"})})
                continue
            try:
                args = tools.validate_args(name, call["arguments"])
            except tools.ToolValidationError as exc:
                out["flagged"].append({"name": name, "reason": str(exc)})
                tool_results_for_model.append({"role": "tool", "content": json.dumps({"error": str(exc)})})
                continue
            spec = tools.TOOLS[name]
            step = {"tool": name, "args": args, "summary": tools.summarize_args(name, args), "requires_confirmation": bool(spec["requires_confirmation"]), "mutating": bool(spec["mutating"])}
            if spec["mutating"]:
                pending_mutations.append(step)
                tool_results_for_model.append({"role": "tool", "content": json.dumps({"status": "queued for user approval", "tool": name})})
                continue
            run = tools.execute(name, args, approved=False, session_id=session_id)
            out["executed"].append({"step": step, "receipt": run["receipt"], "result": run["result"]})
            payload = run["result"].get("result") if run["result"].get("ok") else {"error": run["result"].get("error")}
            tool_results_for_model.append({"role": "tool", "content": json.dumps(payload, default=str)[:6000]})
        messages.extend(tool_results_for_model)
        if pending_mutations:
            break
    out["steps"] = pending_mutations
    if pending_mutations:
        allow_repair = any(s["tool"] == "run_tests" for s in pending_mutations)
        pid = str(uuid.uuid4())
        conn = connect()
        try:
            ensure_tables(conn)
            conn.execute("INSERT INTO agent_proposals (id, session_id, created_at, status, plan_json, receipt_ids_json) VALUES (?,?,?,?,?,?)",
                         (pid, session_id, now_iso(), "pending", json.dumps({"message": message[:2000], "steps": pending_mutations, "rationale": out["rationale"], "model": model, "allow_repair": allow_repair}, default=str), json.dumps([])))
            conn.commit()
        finally:
            conn.close()
        out["proposal_id"] = pid
    return out


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        ensure_tables(conn)
        row = one(conn, "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,))
    finally:
        conn.close()
    if not row:
        return None
    row["plan"] = json.loads(row.pop("plan_json") or "{}")
    row["receipt_ids"] = json.loads(row.pop("receipt_ids_json") or "[]")
    return row


def list_proposals(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        ensure_tables(conn)
        found = rows(conn, "SELECT * FROM agent_proposals WHERE (? IS NULL OR status=?) ORDER BY created_at DESC LIMIT ?", (status, status, max(1, min(limit, 500))))
    finally:
        conn.close()
    for r in found:
        r["plan"] = json.loads(r.pop("plan_json") or "{}")
        r["receipt_ids"] = json.loads(r.pop("receipt_ids_json") or "[]")
    return found


def _set_status(proposal_id: str, status: str, receipt_ids: list[str]) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE agent_proposals SET status=?, receipt_ids_json=?, decided_at=? WHERE id=?", (status, json.dumps(receipt_ids), now_iso(), proposal_id))
        conn.commit()
    finally:
        conn.close()


def reject(proposal_id: str) -> dict[str, Any]:
    prop = get_proposal(proposal_id)
    if not prop:
        raise KeyError("proposal not found")
    if prop["status"] != "pending":
        return prop
    _set_status(proposal_id, "rejected", [])
    return get_proposal(proposal_id) or {}


def approve(proposal_id: str) -> dict[str, Any]:
    """Execute every step in order with user approval; stop at the first failure; optional bounded test-repair loop."""
    prop = get_proposal(proposal_id)
    if not prop:
        raise KeyError("proposal not found")
    if prop["status"] != "pending":
        return {"proposal": prop, "receipts": [], "results": [], "note": f"proposal already {prop['status']}"}
    receipts_out: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    receipt_ids: list[str] = []
    failed = False
    last_test_failure: dict[str, Any] | None = None
    for step in prop["plan"].get("steps", []):
        run = tools.execute(step["tool"], step["args"], approved=True, session_id=prop.get("session_id"))
        receipts_out.append(run["receipt"]); results.append({"step": step, **run["result"]}); receipt_ids.append(run["receipt"]["id"])
        if run["receipt"]["outcome"] != "succeeded":
            if step["tool"] == "run_tests" and prop["plan"].get("allow_repair"):
                last_test_failure = {"step": step, "result": run["result"]}
            failed = True
            break
    repair_log: list[dict[str, Any]] = []
    if last_test_failure:
        repair_log, repaired = _repair_loop(prop, last_test_failure, receipts_out, results, receipt_ids)
        failed = not repaired
    status = "failed" if failed else "executed"
    _set_status(proposal_id, status, receipt_ids)
    return {"proposal": get_proposal(proposal_id), "receipts": receipts_out, "results": results, "repair": repair_log, "status": status}


def _repair_loop(prop: dict[str, Any], failure: dict[str, Any], receipts_out: list, results: list, receipt_ids: list) -> tuple[list[dict[str, Any]], bool]:
    cwd = failure["step"]["args"].get("cwd")
    log: list[dict[str, Any]] = []
    model = prop["plan"].get("model") or "qwen3:14b"
    specs = [s for s in tools.ollama_tool_specs(None) if s["function"]["name"] in {"fs_read", "fs_search", "fs_list", "fs_edit", "fs_write"}]
    if not specs or not cwd:
        return log, False
    failing = failure["result"].get("result") or {}
    for cycle in range(MAX_REPAIR_CYCLES):
        messages = [{"role": "system", "content": f"You are repairing a failing pytest run in {cwd}. Use fs_read to inspect, then fs_edit (with the sha256 from fs_read) to fix. Make minimal changes. Only touch files under {cwd}."},
                    {"role": "user", "content": "pytest output:\n" + (failing.get("stdout") or "")[-5000:] + "\n" + (failing.get("stderr") or "")[-2000:]}]
        edits_made = 0
        for _ in range(4):
            try:
                response = ollama.chat(messages, model, options={"temperature": 0.0, "num_ctx": 16384, "num_predict": 1200}, tools=specs, think=False if model.startswith("qwen3") else None)
            except Exception as exc:
                log.append({"cycle": cycle, "error": str(exc)})
                return log, False
            reply = response.get("message") or {}
            calls = _parse_calls(reply)
            if not calls:
                break
            messages.append(reply)
            for call in calls:
                name = call["name"]
                if name not in tools.TOOLS:
                    messages.append({"role": "tool", "content": json.dumps({"error": "unknown tool"})}); continue
                try:
                    args = tools.validate_args(name, call["arguments"])
                except tools.ToolValidationError as exc:
                    messages.append({"role": "tool", "content": json.dumps({"error": str(exc)})}); continue
                run = tools.execute(name, args, approved=True, session_id=prop.get("session_id"))
                receipts_out.append(run["receipt"]); results.append({"step": {"tool": name, "args": args, "summary": tools.summarize_args(name, args), "repair_cycle": cycle}, **run["result"]}); receipt_ids.append(run["receipt"]["id"])
                if tools.TOOLS[name]["mutating"] and run["receipt"]["outcome"] == "succeeded":
                    edits_made += 1
                messages.append({"role": "tool", "content": json.dumps(run["result"].get("result") if run["result"].get("ok") else {"error": run["result"].get("error")}, default=str)[:6000]})
            if edits_made:
                break
        rerun = tools.execute("run_tests", failure["step"]["args"], approved=True, session_id=prop.get("session_id"))
        receipts_out.append(rerun["receipt"]); results.append({"step": {**failure["step"], "repair_cycle": cycle}, **rerun["result"]}); receipt_ids.append(rerun["receipt"]["id"])
        log.append({"cycle": cycle, "edits": edits_made, "tests_passed": rerun["receipt"]["outcome"] == "succeeded"})
        if rerun["receipt"]["outcome"] == "succeeded":
            return log, True
        failing = rerun["result"].get("result") or {}
    return log, False


def receipts_block(proposal_id: str) -> str:
    from . import receipts as rc
    prop = get_proposal(proposal_id)
    if not prop:
        return "ACTION RECEIPTS: proposal not found."
    lines = [f"ACTION RECEIPTS for proposal {proposal_id[:8]} (status {prop['status']}). Only these receipted facts may be reported as done:"]
    for rid in prop["receipt_ids"]:
        r = rc.get_receipt(rid)
        if r:
            lines.append("- " + rc.format_receipt(r) + (f" detail={str(r.get('detail') or '')[:400]}" if r.get("detail") else "") + (f" error={r.get('error')}" if r.get("error") else ""))
    if not prop["receipt_ids"]:
        lines.append("- no actions were executed")
    return "\n".join(lines)
