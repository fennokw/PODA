"""Classic Outlook provider using the user's existing Windows MAPI profile."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ...security import egress
from .models import MailError, encode_id, mask_address, snippet, split_sender
from .outlook_worker import REMEDY as AUTOMATION_REMEDY

PROVIDER = "outlook_classic"
LIST_TIMEOUT = 25
READ_TIMEOUT = 20
ACTION_TIMEOUT = 30


def _installed() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID"):
            return True
    except OSError:
        return False


def is_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "tasklist.exe"), "/fi", "IMAGENAME eq OUTLOOK.EXE", "/nh"],
                                capture_output=True, timeout=4, creationflags=subprocess.CREATE_NO_WINDOW)
        return b"OUTLOOK.EXE" in result.stdout.upper()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run(script: str, args: dict[str, Any], timeout: int = LIST_TIMEOUT) -> dict:
    if sys.platform != "win32":
        raise MailError("mail_not_running", "Classic Outlook requires Windows.", AUTOMATION_REMEDY, status=409)
    if egress.offline_mode():
        raise MailError("offline", "PODA is in offline mode; Outlook access is paused.", status=503)
    if script != "status" and not is_running():
        raise MailError("mail_not_running", "Classic Outlook is closed.", AUTOMATION_REMEDY, status=409)
    try:
        proc = subprocess.run([sys.executable, "-m", "poda_app.connectors.email.outlook_worker"],
                              input=json.dumps({"operation": script, "args": args}).encode("utf-8"),
                              capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired as exc:
        raise MailError("timeout", "Classic Outlook timed out. Inspect Outlook before retrying a write.",
                        AUTOMATION_REMEDY, status=504) from exc
    except OSError as exc:
        raise MailError("provider", "The Outlook worker could not start.", AUTOMATION_REMEDY, status=502) from exc
    try:
        response = json.loads(proc.stdout)
        if proc.returncode:
            raise ValueError("worker failed")
    except ValueError as exc:
        raise MailError("provider", "The Outlook worker could not read the profile.", AUTOMATION_REMEDY, status=502) from exc
    if response.get("error"):
        raise MailError(response["kind"], response["error"], response.get("remedy"), response["status"])
    return response["result"]


def status(auto_launch: bool = False) -> dict:
    running = is_running()
    result = {"available": _installed(), "authorized": None, "running": running,
              "accounts_detected": [], "error": None}
    if not result["available"]:
        return {**result, "error": "Classic Outlook is unavailable."}
    if not running and not auto_launch:
        return {**result, "error": "Open classic Outlook to detect its accounts."}
    try:
        accounts = _run("status", {}).get("accounts", [])
        return {**result, "running": True, "authorized": True, "accounts_detected": [
            {"account_name": account["account_name"], "address_masked": mask_address(account["addresses"][0]),
             "provider_type": PROVIDER, "enabled": True} for account in accounts]}
    except MailError as exc:
        return {**result, "error": exc.message}


def mailboxes(account: str) -> list[dict[str, Any]]:
    return _run("mailboxes", {"account": account}, timeout=LIST_TIMEOUT).get("mailboxes", [])


def _row(account: str, mailbox: str, r: dict[str, Any], account_id: str) -> dict[str, Any]:
    name, addr = split_sender(r.get("sender"))
    return {
        "id": encode_id(PROVIDER, account, mailbox, r.get("native_id")),
        "account_id": account_id, "mailbox": mailbox,
        "subject": r.get("subject") or "(no subject)",
        "from_name": name or mask_address(addr), "from_address_masked": mask_address(addr),
        "to_masked": [], "date": r.get("date"), "read": bool(r.get("read")), "flagged": bool(r.get("flagged")),
        "snippet": "", "has_attachments": None, "message_id": r.get("message_id"), "native_id": r.get("native_id"),
    }


def list_messages(account: str, account_id: str, mailbox: str = "INBOX", limit: int = 50, offset: int = 0, unread_only: bool = False,
                  query: str | None = None, snippet_limit: int = 0) -> dict[str, Any]:
    """Read bounded Outlook headers and fetch body snippets separately."""
    data = _run("list", {"account": account, "mailbox": mailbox, "limit": limit, "offset": offset, "unread_only": unread_only, "query": query or ""}, timeout=LIST_TIMEOUT)
    rows = [_row(account, mailbox, r, account_id) for r in data.get("rows", [])]
    for r in rows:
        r["snippet"] = None  # null = not loaded yet
    if snippet_limit and rows:
        try:
            snips = _run("snippets", {"account": account, "mailbox": mailbox, "ids": [r["native_id"] for r in rows[:min(snippet_limit, 10)]]}, timeout=READ_TIMEOUT)
            for r in rows[:min(snippet_limit, 10)]:
                r["snippet"] = snippet(snips.get(str(r["native_id"]), ""))
        except MailError:
            pass
    for r in rows:
        r.pop("native_id", None)
    return {"messages": rows, "total": data.get("total", len(rows)), "unread": data.get("unread"), "scanned": data.get("scanned"), "truncated": bool(data.get("truncated"))}


def snippets(account: str, mailbox: str, native_ids: list[str]) -> dict[str, str]:
    data = _run("snippets", {"account": account, "mailbox": mailbox, "ids": native_ids[:10]}, timeout=READ_TIMEOUT)
    return {k: snippet(v) for k, v in data.items()}


def read_message(account: str, account_id: str, mailbox: str, native_id: str) -> dict[str, Any]:
    data = _run("read", {"account": account, "mailbox": mailbox, "native_id": native_id, "max_chars": 200000}, timeout=READ_TIMEOUT)
    row = _row(account, mailbox, data, account_id)
    row.pop("native_id", None)
    row.update({
        "to_masked": [mask_address(a) for a in data.get("to") or []],
        "body_text": data.get("body") or "",
        "attachments": data.get("attachments") or [],
        "has_attachments": bool(data.get("attachments")),
        "headers": {"reply_to": mask_address(data.get("reply_to")), "cc_masked": [mask_address(a) for a in data.get("cc") or []]},
        "untrusted": True,
    })
    row["snippet"] = snippet(row["body_text"])
    return row


def set_flags(account: str, mailbox: str, native_id: str, read: bool | None = None, flagged: bool | None = None) -> dict[str, Any]:
    return _run("set_flags", {"account": account, "mailbox": mailbox, "native_id": native_id, "read": read, "flagged": flagged}, timeout=ACTION_TIMEOUT)


def move(account: str, mailbox: str, native_id: str, target: str) -> dict[str, Any]:
    return _run("move", {"account": account, "mailbox": mailbox, "native_id": native_id, "target": target}, timeout=ACTION_TIMEOUT)


def trash(account: str, mailbox: str, native_id: str) -> dict[str, Any]:
    return _run("trash", {"account": account, "mailbox": mailbox, "native_id": native_id}, timeout=ACTION_TIMEOUT)


def draft(subject: str, body: str, to: list[str], cc: list[str] | None = None, sender: str | None = None, send: bool = False, account: str | None = None) -> dict[str, Any]:
    return _run("draft", {"subject": subject, "body": body, "to": to, "cc": cc or [], "sender": sender, "send": send, "account": account}, timeout=ACTION_TIMEOUT)


def archive_target(account_type: str | None, mailbox_names: list[str]) -> str:
    """Gmail accounts archive into All Mail; others into an 'Archive' mailbox when present."""
    lowered = {m.lower(): m for m in mailbox_names}
    for cand in ("[gmail]/all mail", "all mail"):
        if cand in lowered:
            return lowered[cand]
    for name in mailbox_names:
        if name.lower().endswith("all mail"):
            return name
    for cand in ("archive", "archived"):
        if cand in lowered:
            return lowered[cand]
    for name in mailbox_names:
        if name.lower().endswith("/archive"):
            return name
    raise MailError("not_found", "No archive mailbox exists for this account (expected 'All Mail' for Gmail or 'Archive').",
                    "Create an 'Archive' folder in classic Outlook for this account, or use move with an explicit mailbox.", status=404)
