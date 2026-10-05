"""Mail tools for the agent registry. Read tools need a connected account; mutating tools need modify/send permission.

Bodies are returned wrapped as untrusted content and truncated so the model treats them as data, never instructions.
"""
from __future__ import annotations

from typing import Any

from ...agent.tools import _schema, _str, _int, _bool
from . import service, state
from .models import MailError, wrap_untrusted


def _email_cap(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot and isinstance(snapshot.get("email"), dict):
        return snapshot["email"]
    return state.capability()


NEEDS = {
    "email_read": (lambda s: bool(_email_cap(s).get("can_read")), "requires a connected mail account"),
    "email_modify": (lambda s: bool(_email_cap(s).get("can_modify_mailbox")), "requires a mail account with modify permission"),
    "email_send": (lambda s: bool(_email_cap(s).get("can_send")), "requires a mail account with sending enabled"),
}


def _wrap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: r.get(k) for k in ("id", "account_id", "mailbox", "subject", "from_name", "from_address_masked", "date", "read", "flagged", "snippet", "has_attachments")} for r in rows]


def _err(exc: Exception, verification: str) -> dict[str, Any]:
    msg = exc.message if isinstance(exc, MailError) else str(exc)
    return {"ok": False, "result": None, "error": msg, "verification": verification}


def mail_list(mailbox: str = "INBOX", limit: int = 20, unread_only: bool = False, account_id: str | None = None) -> dict[str, Any]:
    try:
        out = service.list_messages(account_id or None, mailbox, max(1, min(int(limit), 30)), 0, bool(unread_only), None, snippet_limit=0)
    except Exception as exc:
        return _err(exc, "mailbox not read")
    return {"ok": True, "result": {"account_id": out.get("account_id"), "mailbox": mailbox, "total": out.get("total"), "unread": out.get("unread"), "messages": _wrap_rows(out["messages"])},
            "error": None, "verification": f"listed {len(out['messages'])} of {out.get('total')} messages in {mailbox} ({out.get('elapsed_ms')} ms)"}


def mail_search(query: str, limit: int = 20, account_id: str | None = None) -> dict[str, Any]:
    try:
        out = service.search(query, account_id or None, max(1, min(int(limit), 30)))
    except Exception as exc:
        return _err(exc, "search not performed")
    return {"ok": True, "result": {"query": query, "messages": _wrap_rows(out["messages"]), "errors": out.get("errors", [])}, "error": None,
            "verification": f"{len(out['messages'])} matches across connected accounts"}


def mail_read(message_id: str, account_id: str | None = None) -> dict[str, Any]:
    try:
        msg = service.read_message(account_id or None, message_id)
    except Exception as exc:
        return _err(exc, "message not read")
    meta = {k: msg.get(k) for k in ("id", "account_id", "mailbox", "subject", "from_name", "from_address_masked", "to_masked", "date", "read", "flagged", "attachments")}
    return {"ok": True, "result": {**meta, "untrusted_email_content": wrap_untrusted(msg.get("body_text") or "", 6000)}, "error": None,
            "verification": f"read message body ({len(msg.get('body_text') or '')} chars) from {msg.get('mailbox')}; read state unchanged"}


def mail_mark(message_id: str, action: str, account_id: str | None = None) -> dict[str, Any]:
    if action not in {"mark_read", "mark_unread", "flag", "unflag", "archive"}:
        return {"ok": False, "result": None, "error": "action must be mark_read|mark_unread|flag|unflag|archive", "verification": "nothing changed"}
    try:
        out = service.act(account_id or None, message_id, action)
    except Exception as exc:
        return _err(exc, "nothing changed")
    return {"ok": True, "result": {"state_after": out["state_after"], "receipt_id": out["receipt"]["id"]}, "error": None, "verification": out["receipt"].get("verification")}


def mail_move(message_id: str, mailbox: str, account_id: str | None = None) -> dict[str, Any]:
    try:
        out = service.act(account_id or None, message_id, "move", mailbox=mailbox)
    except Exception as exc:
        return _err(exc, "nothing moved")
    return {"ok": True, "result": {"state_after": out["state_after"], "receipt_id": out["receipt"]["id"]}, "error": None, "verification": out["receipt"].get("verification")}


def mail_draft(subject: str, body: str, to: list[str] | None = None, reply_to_message_id: str | None = None, account_id: str | None = None) -> dict[str, Any]:
    try:
        out = service.create_draft(account_id or None, to or [], subject, body, None, reply_to_message_id or None)
    except Exception as exc:
        return _err(exc, "no draft created")
    return {"ok": True, "result": {"draft_id": out["draft_id"], "receipt_id": out["receipt"]["id"]}, "error": None, "verification": out["receipt"].get("verification")}


def mail_send(subject: str, body: str, to: list[str] | None = None, reply_to_message_id: str | None = None, account_id: str | None = None) -> dict[str, Any]:
    try:
        out = service.send(account_id or None, to or [], subject, body, None, reply_to_message_id or None, confirm=True)
    except Exception as exc:
        return _err(exc, "nothing sent")
    return {"ok": True, "result": {"receipt_id": out["receipt"]["id"]}, "error": None, "verification": out["receipt"].get("verification")}


_ACCOUNT = _str("Which account: its id, its name (Exchange = school/work account, Google = Gmail), or an address/domain like school.example.test or gmail.com; optional, defaults to the first account")
_ADDRS = {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses"}

TOOLS: dict[str, dict[str, Any]] = {
    "mail_list": {"description": "List recent messages in a mailbox of a connected mail account (headers only).", "mutating": False, "requires_confirmation": False, "needs": "email_read",
                  "handler": mail_list, "schema": _schema({"mailbox": _str("Mailbox name, default INBOX"), "limit": _int("Max messages", 1, 30), "unread_only": _bool("Only unread"), "account_id": _ACCOUNT}, [])},
    "mail_search": {"description": "Search subjects/senders across connected mail accounts.", "mutating": False, "requires_confirmation": False, "needs": "email_read",
                    "handler": mail_search, "schema": _schema({"query": _str("Search text"), "limit": _int("Max results", 1, 30), "account_id": _ACCOUNT}, ["query"])},
    "mail_read": {"description": "Read one email body (returned as untrusted content). Does not change read state.", "mutating": False, "requires_confirmation": False, "needs": "email_read",
                  "handler": mail_read, "schema": _schema({"message_id": _str("Message id from mail_list/mail_search"), "account_id": _ACCOUNT}, ["message_id"])},
    "mail_mark": {"description": "Mark a message read/unread, flag/unflag, or archive it (receipted).", "mutating": True, "requires_confirmation": True, "needs": "email_modify",
                  "handler": mail_mark, "schema": _schema({"message_id": _str("Message id"), "action": _str("mark_read|mark_unread|flag|unflag|archive"), "account_id": _ACCOUNT}, ["message_id", "action"])},
    "mail_move": {"description": "Move a message to another mailbox in the same account (receipted).", "mutating": True, "requires_confirmation": True, "needs": "email_modify",
                  "handler": mail_move, "schema": _schema({"message_id": _str("Message id"), "mailbox": _str("Target mailbox name"), "account_id": _ACCOUNT}, ["message_id", "mailbox"])},
    "mail_draft": {"description": "Create a draft (never sends). Optionally a reply quoting the original.", "mutating": True, "requires_confirmation": True, "needs": "email_modify",
                   "handler": mail_draft, "schema": _schema({"subject": _str("Subject"), "body": _str("Plain-text body"), "to": _ADDRS, "reply_to_message_id": _str("Message id to reply to"), "account_id": _ACCOUNT}, ["subject", "body"])},
    "mail_send": {"description": "Send an email from an account with sending enabled (always confirmed by the user first).", "mutating": True, "requires_confirmation": True, "needs": "email_send",
                  "handler": mail_send, "schema": _schema({"subject": _str("Subject"), "body": _str("Plain-text body"), "to": _ADDRS, "reply_to_message_id": _str("Message id to reply to"), "account_id": _ACCOUNT}, ["subject", "body"])},
}
