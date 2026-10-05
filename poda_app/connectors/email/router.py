"""Mail API: Apple Mail + Gmail IMAP connections, mailbox browsing, receipted actions, drafts, gated send, extraction."""
from __future__ import annotations

import email as _email
import imaplib
import re
from email.header import decode_header
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...runtime.db import connect, now_iso
from . import service, extraction
from .models import MailError

router = APIRouter()


def _http(exc: MailError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail())


# ----------------------------------------------------------------------------- models

class AppleConnect(BaseModel):
    account_names: list[str] = []
    allow_modify: bool = False
    allow_send: bool = False
    auto_launch: bool = True


class GmailConnect(BaseModel):
    address: str
    app_password: str
    allow_modify: bool = False
    allow_send: bool = False


class Permissions(BaseModel):
    allow_modify: bool = False
    allow_send: bool = False


class ActionRequest(BaseModel):
    account_id: str | None = None
    action: str
    mailbox: str | None = None
    confirm: bool = False
    session_id: str | None = None


class DraftRequest(BaseModel):
    account_id: str | None = None
    to: list[str] = []
    cc: list[str] = []
    subject: str = ""
    body: str = ""
    reply_to_message_id: str | None = None
    session_id: str | None = None


class SendRequest(DraftRequest):
    confirm: bool = False


class ExtractRequest(BaseModel):
    account_id: str | None = None


class ProposeRequest(BaseModel):
    account_id: str | None = None
    commitment: dict[str, Any]
    session_id: str | None = None


class SnippetRequest(BaseModel):
    account_id: str | None = None
    ids: list[str]


# ----------------------------------------------------------------------------- connections

@router.get("/mail/status")
def mail_status() -> dict[str, Any]:
    return service.status()


@router.post("/mail/apple/connect")
def apple_connect(req: AppleConnect) -> dict[str, Any]:
    try:
        return service.connect_apple(req.account_names, req.allow_modify, req.allow_send, req.auto_launch)
    except MailError as exc:
        raise _http(exc)


@router.post("/mail/gmail/connect")
def gmail_connect(req: GmailConnect) -> dict[str, Any]:
    try:
        return service.connect_gmail(req.address, req.app_password, req.allow_modify, req.allow_send)
    except MailError as exc:
        raise _http(exc)


@router.post("/mail/accounts/{account_id}/permissions")
def set_permissions(account_id: str, req: Permissions) -> dict[str, Any]:
    try:
        return service.set_permissions(account_id, req.allow_modify, req.allow_send)
    except MailError as exc:
        raise _http(exc)


@router.delete("/mail/accounts/{account_id}")
def revoke_account(account_id: str) -> dict[str, Any]:
    try:
        return service.revoke(account_id)
    except MailError as exc:
        raise _http(exc)


# ----------------------------------------------------------------------------- reading

@router.get("/mail/mailboxes")
def mailboxes(account_id: str | None = None) -> dict[str, Any]:
    try:
        return service.mailboxes(account_id)
    except MailError as exc:
        raise _http(exc)


@router.get("/mail/messages")
def list_messages(account_id: str | None = None, mailbox: str = "INBOX", limit: int = 50, offset: int = 0, unread_only: bool = False,
                  query: str | None = None, snippet_limit: int = 0) -> dict[str, Any]:
    try:
        return service.list_messages(account_id, mailbox, max(1, min(limit, 100)), max(0, offset), unread_only, query, snippet_limit=max(0, min(snippet_limit, 10)))
    except MailError as exc:
        raise _http(exc)


@router.post("/mail/snippets")
def snippets(req: SnippetRequest) -> dict[str, Any]:
    try:
        return {"snippets": service.snippets(req.account_id, req.ids[:10])}
    except MailError as exc:
        raise _http(exc)


@router.get("/mail/search")
def search(q: str, account_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail={"code": "VALIDATION", "message": "q is required"})
    try:
        return service.search(q.strip(), account_id, max(1, min(limit, 100)))
    except MailError as exc:
        raise _http(exc)


@router.get("/mail/messages/{message_id}")
def read_message(message_id: str, account_id: str | None = None) -> dict[str, Any]:
    try:
        return service.read_message(account_id, message_id)
    except MailError as exc:
        raise _http(exc)


# ----------------------------------------------------------------------------- actions

@router.post("/mail/messages/{message_id}/actions")
def message_action(message_id: str, req: ActionRequest) -> dict[str, Any]:
    try:
        return service.act(req.account_id, message_id, req.action, req.mailbox, req.confirm, req.session_id)
    except MailError as exc:
        raise _http(exc)


@router.post("/mail/drafts")
def create_draft(req: DraftRequest) -> dict[str, Any]:
    try:
        return service.create_draft(req.account_id, req.to, req.subject, req.body, req.cc, req.reply_to_message_id, req.session_id)
    except MailError as exc:
        raise _http(exc)


@router.post("/mail/send")
def send(req: SendRequest) -> dict[str, Any]:
    try:
        return service.send(req.account_id, req.to, req.subject, req.body, req.cc, req.reply_to_message_id, req.confirm, req.session_id)
    except MailError as exc:
        raise _http(exc)


@router.get("/mail/receipts")
def receipts(limit: int = 50) -> dict[str, Any]:
    return {"receipts": service.mail_receipts(limit)}


# ----------------------------------------------------------------------------- extraction → Notion

@router.post("/mail/messages/{message_id}/extract")
def extract(message_id: str, req: ExtractRequest) -> dict[str, Any]:
    try:
        msg = service.read_message(req.account_id, message_id)
    except MailError as exc:
        raise _http(exc)
    try:
        return extraction.extract(msg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "EXTRACTION_FAILED", "message": str(exc)})


@router.post("/mail/messages/{message_id}/propose-notion")
def propose_notion(message_id: str, req: ProposeRequest) -> dict[str, Any]:
    c = req.commitment or {}
    if not c.get("title") or not c.get("date_start"):
        raise HTTPException(status_code=400, detail={"code": "VALIDATION", "message": "commitment needs title and date_start"})
    try:
        from ..notion import state as notion_state
        from ..notion.mapping import propose_event
        from ..notion.client import NotionError
    except Exception as exc:
        raise HTTPException(status_code=409, detail={"code": "NOTION_NOT_READY", "message": f"Notion connector unavailable: {exc}"})
    cap = notion_state.capability()
    if not cap.get("connected") or not cap.get("mapping_active"):
        raise HTTPException(status_code=409, detail={"code": "NOTION_NOT_READY", "message": "Connect Notion and save a schema mapping before proposing events."})
    event = {"title": c["title"], "start": c["date_start"], "end": c.get("date_end"), "all_day": bool(c.get("all_day", True)), "notes": f"Source email {message_id}: {c.get('evidence_span', '')[:300]}",
             "external_id": f"mail:{message_id}", "project": c.get("suggested_project"), "class": c.get("suggested_class")}
    try:
        return propose_event(event, session_id=req.session_id)
    except NotionError as exc:  # type: ignore[misc]
        raise HTTPException(status_code=409, detail={"code": "NOTION_NOT_READY", "message": str(exc)})
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "PROPOSAL_FAILED", "message": str(exc)})


# ----------------------------------------------------------------------------- legacy endpoints (kept verbatim in behaviour)

class EmailPreviewRequest(BaseModel):
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    email_address: str
    app_password: str
    mailbox: str = "INBOX"
    limit: int = 10
    search: str = "ALL"


class EventExtractRequest(BaseModel):
    text: str


def _decode(value: str | None) -> str:
    if not value:
        return ""
    out = ""
    for part, enc in decode_header(value):
        out += part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
    return out


@router.post("/email/imap/preview", status_code=410)
def email_preview() -> dict[str, Any]:
    """Retired: the legacy arbitrary-host, request-scoped IMAP path bypassed offline/egress policy.

    Use the modern Apple Mail or Gmail connector instead. Do not initiate any connection here,
    even if the caller supplied credentials. Kept as a 410 so old clients fail explicitly.
    """
    raise HTTPException(status_code=410, detail={
        "code": "LEGACY_PREVIEW_RETIRED",
        "message": "Direct IMAP preview is disabled for security. Connect Apple Mail or Gmail using Mail settings.",
        "credential_storage": "No supplied credentials were used or stored",
    })


@router.post("/calendar/extract-events")
def extract_events(req: EventExtractRequest) -> dict[str, Any]:
    text = req.text
    date_matches = re.findall(r"\b(?:today|tomorrow|next\s+\w+|\d{1,2}/\d{1,2}(?:/\d{2,4})?|[A-Z][a-z]+\s+\d{1,2}(?:,\s*\d{4})?)\b", text, flags=re.I)
    time_matches = re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)\b", text)
    return {"event_candidates": [{"date_phrase": d, "time_phrase": time_matches[0] if time_matches else None, "source_text": text[:500], "status": "unconfirmed"} for d in date_matches[:8]],
            "note": "Candidates are unconfirmed extractions; nothing is written anywhere."}
