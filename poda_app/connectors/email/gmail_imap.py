"""Direct Gmail access over IMAP/SMTP with an app password held in macOS Keychain.

Egress: only imap.gmail.com:993 and smtp.gmail.com:465, logged to egress_log (metadata only), blocked in offline mode.
"""
from __future__ import annotations

import email
import imaplib
import re
import smtplib
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime, formatdate, make_msgid
from typing import Any

from ...runtime import config
from ...runtime.db import connect, now_iso
from ...security import egress
from ...security.keychain import keychain_get, keychain_set, keychain_delete
from .models import MailError, encode_id, split_sender, strip_html, snippet, mask_address

PROVIDER = "gmail_imap"
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
KEYCHAIN_SERVICE = "PODA.Public.Gmail"
ALLOWED_HOSTS = {IMAP_HOST, SMTP_HOST}
APP_PASSWORD_REMEDY = ("Create a Google App Password (requires 2-Step Verification): myaccount.google.com → Security → 2-Step Verification → App passwords. "
                       "Paste the 16-character password here; PODA stores it only in macOS Keychain.")


def _check_egress(host: str) -> None:
    allowed = set(config.EGRESS_ALLOWLIST.get("gmail", set())) | ALLOWED_HOSTS
    if host not in allowed:
        _log("IMAP", host, "blocked", None, blocked=True)
        raise MailError("egress_blocked", f"Host {host} is not allowlisted for the Gmail connector", status=403)
    if egress.offline_mode():
        _log("IMAP", host, "offline", None, blocked=True)
        raise MailError("offline", "PODA is in offline mode; external connectors are disabled", status=503)


def _log(method: str, host: str, path: str, status: int | None, blocked: bool = False, duration_ms: int = 0) -> None:
    try:
        conn = connect()
        try:
            conn.execute("INSERT INTO egress_log (ts, service, method, host, path, status, bytes_out, bytes_in, purpose, duration_ms, blocked) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (now_iso(), "gmail", method, host, path, status, 0, 0, "mail", duration_ms, 1 if blocked else 0))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def store_password(address: str, app_password: str) -> None:
    keychain_set(KEYCHAIN_SERVICE, address.strip().lower(), app_password.strip())


def delete_password(address: str) -> None:
    keychain_delete(KEYCHAIN_SERVICE, address.strip().lower())


def _password(address: str) -> str:
    pw = keychain_get(KEYCHAIN_SERVICE, address.strip().lower())
    if not pw:
        raise MailError("invalid_credentials", "No Gmail app password is stored in Keychain for this account.", APP_PASSWORD_REMEDY, status=401)
    return pw


class _Session:
    def __init__(self, address: str, password: str | None = None):
        self.address = address
        _check_egress(IMAP_HOST)
        started = time.perf_counter()
        try:
            self.conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=25)
            self.conn.login(address, password or _password(address))
        except imaplib.IMAP4.error as exc:
            _log("IMAP", IMAP_HOST, "login", 401, duration_ms=int((time.perf_counter() - started) * 1000))
            raise MailError("invalid_credentials", "Gmail rejected the address or app password.", APP_PASSWORD_REMEDY, status=401) from exc
        except OSError as exc:
            _log("IMAP", IMAP_HOST, "login", None, duration_ms=int((time.perf_counter() - started) * 1000))
            raise MailError("provider", f"Could not reach Gmail IMAP: {exc}", status=502) from exc
        _log("IMAP", IMAP_HOST, "login", 200, duration_ms=int((time.perf_counter() - started) * 1000))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.conn.logout()
        except Exception:
            pass

    def select(self, mailbox: str, readonly: bool = True) -> int:
        typ, data = self.conn.select(_quote(mailbox), readonly=readonly)
        if typ != "OK":
            raise MailError("not_found", f"Mailbox {mailbox} not found", status=404)
        _log("IMAP", IMAP_HOST, mailbox, 200)
        try:
            return int(data[0])
        except Exception:
            return 0


def _quote(name: str) -> str:
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _hdr(value: Any) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def verify_login(address: str, app_password: str) -> dict[str, Any]:
    with _Session(address, app_password) as s:
        count = s.select("INBOX")
        return {"ok": True, "inbox_count": count}


def mailboxes(address: str) -> list[dict[str, Any]]:
    out = []
    with _Session(address) as s:
        typ, data = s.conn.list()
        if typ != "OK":
            return out
        for raw in data[:200]:
            line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            m = re.match(r'^\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$', line)
            if not m:
                continue
            name = m.group("name").strip().strip('"')
            if "\\Noselect" in m.group("flags"):
                continue
            unread = None
            try:
                typ2, st = s.conn.status(_quote(name), "(UNSEEN MESSAGES)")
                if typ2 == "OK":
                    text = st[0].decode(errors="replace") if isinstance(st[0], bytes) else str(st[0])
                    um = re.search(r"UNSEEN (\d+)", text)
                    cm = re.search(r"MESSAGES (\d+)", text)
                    unread = int(um.group(1)) if um else None
                    out.append({"name": name, "unread": unread, "count": int(cm.group(1)) if cm else None})
                    continue
            except Exception:
                pass
            out.append({"name": name, "unread": unread, "count": None})
    return out


def _parse_message(raw: bytes, max_body: int = 200_000) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    body_plain, body_html, attachments = "", "", []
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" in disp or part.get_filename():
            attachments.append({"name": _hdr(part.get_filename()), "size": len(part.get_payload(decode=True) or b""), "mime": ctype})
            continue
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and not body_plain:
            body_plain = text
        elif ctype == "text/html" and not body_html:
            body_html = text
    body = body_plain or strip_html(body_html)
    return {"subject": _hdr(msg.get("Subject")) or "(no subject)", "from": _hdr(msg.get("From")), "to": [a.strip() for a in _hdr(msg.get("To")).split(",") if a.strip()],
            "cc": [a.strip() for a in _hdr(msg.get("Cc")).split(",") if a.strip()], "reply_to": _hdr(msg.get("Reply-To")), "date": _date(msg.get("Date")),
            "message_id": (msg.get("Message-ID") or "").strip(), "body": body[:max_body], "attachments": attachments}


def _date(value: str | None) -> str | None:
    try:
        return parsedate_to_datetime(value).isoformat() if value else None
    except Exception:
        return None


def _flags_from(raw: str) -> tuple[bool, bool]:
    return ("\\Seen" in raw), ("\\Flagged" in raw)


def list_messages(address: str, account_id: str, mailbox: str = "INBOX", limit: int = 50, offset: int = 0, unread_only: bool = False, query: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with _Session(address) as s:
        total = s.select(mailbox)
        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        if query:
            q = query.replace('"', "")
            criteria = ["OR", "SUBJECT", f'"{q}"', "FROM", f'"{q}"'] + (["UNSEEN"] if unread_only else [])
        typ, data = s.conn.uid("SEARCH", None, *criteria)
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        typ, un = s.conn.uid("SEARCH", None, "UNSEEN")
        unread = len(un[0].split()) if typ == "OK" and un and un[0] else 0
        page = list(reversed(uids))[offset:offset + limit]
        rows = []
        if page:
            seq = b",".join(page).decode()
            typ, fetched = s.conn.uid("FETCH", seq, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)] X-GM-MSGID)")
            if typ == "OK":
                for item in fetched:
                    if not isinstance(item, tuple):
                        continue
                    meta = item[0].decode(errors="replace")
                    hdr = email.message_from_bytes(item[1])
                    uid = re.search(r"UID (\d+)", meta)
                    read, flagged = _flags_from(meta)
                    name, addr = split_sender(_hdr(hdr.get("From")))
                    rows.append({"id": encode_id(PROVIDER, address, mailbox, uid.group(1) if uid else "?"), "account_id": account_id, "mailbox": mailbox,
                                 "subject": _hdr(hdr.get("Subject")) or "(no subject)", "from_name": name or mask_address(addr), "from_address_masked": mask_address(addr),
                                 "to_masked": [mask_address(a) for a in _hdr(hdr.get("To")).split(",") if a.strip()][:5], "date": _date(hdr.get("Date")),
                                 "read": read, "flagged": flagged, "snippet": None, "has_attachments": None, "message_id": (hdr.get("Message-ID") or "").strip()})
        rows.sort(key=lambda r: r["date"] or "", reverse=True)
        return {"messages": rows, "total": len(uids) if query or unread_only else total, "unread": unread, "scanned": len(uids)}


def read_message(address: str, account_id: str, mailbox: str, uid: str) -> dict[str, Any]:
    with _Session(address) as s:
        s.select(mailbox)
        typ, data = s.conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            raise MailError("not_found", "Message not found", status=404)
        meta = data[0][0].decode(errors="replace")
        read, flagged = _flags_from(meta)
        parsed = _parse_message(data[0][1])
        name, addr = split_sender(parsed["from"])
        return {"id": encode_id(PROVIDER, address, mailbox, uid), "account_id": account_id, "mailbox": mailbox, "subject": parsed["subject"],
                "from_name": name or mask_address(addr), "from_address_masked": mask_address(addr), "to_masked": [mask_address(a) for a in parsed["to"]],
                "date": parsed["date"], "read": read, "flagged": flagged, "snippet": snippet(parsed["body"]), "has_attachments": bool(parsed["attachments"]),
                "message_id": parsed["message_id"], "body_text": parsed["body"], "attachments": parsed["attachments"],
                "headers": {"reply_to": mask_address(parsed["reply_to"]), "cc_masked": [mask_address(a) for a in parsed["cc"]]}, "untrusted": True}


def _state(s: _Session, uid: str) -> dict[str, Any]:
    typ, data = s.conn.uid("FETCH", uid, "(FLAGS)")
    if typ != "OK" or not data or data[0] is None:
        return {"exists": False}
    meta = data[0].decode(errors="replace") if isinstance(data[0], bytes) else str(data[0])
    read, flagged = _flags_from(meta)
    return {"exists": True, "read": read, "flagged": flagged}


def set_flags(address: str, mailbox: str, uid: str, read: bool | None = None, flagged: bool | None = None) -> dict[str, Any]:
    with _Session(address) as s:
        s.select(mailbox, readonly=False)
        if read is not None:
            s.conn.uid("STORE", uid, "+FLAGS" if read else "-FLAGS", "(\\Seen)")
        if flagged is not None:
            s.conn.uid("STORE", uid, "+FLAGS" if flagged else "-FLAGS", "(\\Flagged)")
        st = _state(s, uid)
        return {"native_id": uid, "read": st.get("read"), "flagged": st.get("flagged"), "mailbox": mailbox}


def move(address: str, mailbox: str, uid: str, target: str) -> dict[str, Any]:
    with _Session(address) as s:
        s.select(mailbox, readonly=False)
        caps = s.conn.capabilities or ()
        if "MOVE" in caps:
            typ, _ = s.conn.uid("MOVE", uid, _quote(target))
        else:
            typ, _ = s.conn.uid("COPY", uid, _quote(target))
            if typ == "OK":
                s.conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                s.conn.expunge()
        if typ != "OK":
            raise MailError("provider", f"Gmail refused to move the message to {target}", status=502)
        gone = not _state(s, uid).get("exists", True)
        return {"moved": gone, "mailbox": target, "native_id": None}


def archive(address: str, mailbox: str, uid: str) -> dict[str, Any]:
    return move(address, mailbox, uid, "[Gmail]/All Mail")


def trash(address: str, mailbox: str, uid: str) -> dict[str, Any]:
    out = move(address, mailbox, uid, "[Gmail]/Trash")
    return {"trashed": out["moved"]}


def _build(address: str, to: list[str], subject: str, body: str, cc: list[str] | None, in_reply_to: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


def draft(address: str, to: list[str], subject: str, body: str, cc: list[str] | None = None, in_reply_to: str | None = None) -> dict[str, Any]:
    msg = _build(address, to, subject, body, cc, in_reply_to)
    with _Session(address) as s:
        typ, data = s.conn.append(_quote("[Gmail]/Drafts"), "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise MailError("provider", "Gmail refused to store the draft", status=502)
        text = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else str(data)
        uid = re.search(r"APPENDUID \d+ (\d+)", text)
        return {"saved": True, "draft_id": encode_id(PROVIDER, address, "[Gmail]/Drafts", uid.group(1) if uid else msg["Message-ID"]), "message_id": msg["Message-ID"]}


def send(address: str, to: list[str], subject: str, body: str, cc: list[str] | None = None, in_reply_to: str | None = None) -> dict[str, Any]:
    _check_egress(SMTP_HOST)
    msg = _build(address, to, subject, body, cc, in_reply_to)
    started = time.perf_counter()
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=25) as smtp:
            smtp.login(address, _password(address))
            refused = smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        _log("SMTP", SMTP_HOST, "send", 535, duration_ms=int((time.perf_counter() - started) * 1000))
        raise MailError("invalid_credentials", "Gmail SMTP rejected the credentials.", APP_PASSWORD_REMEDY, status=401) from exc
    except (smtplib.SMTPException, OSError) as exc:
        _log("SMTP", SMTP_HOST, "send", None, duration_ms=int((time.perf_counter() - started) * 1000))
        raise MailError("provider", f"SMTP send failed: {exc}", status=502) from exc
    _log("SMTP", SMTP_HOST, "send", 250, duration_ms=int((time.perf_counter() - started) * 1000))
    return {"sent": not refused, "refused": {k: str(v) for k, v in (refused or {}).items()}, "message_id": msg["Message-ID"]}


def search(address: str, account_id: str, query: str, limit: int = 50) -> list[dict[str, Any]]:
    out = []
    for mb in ("INBOX", "[Gmail]/All Mail"):
        try:
            out.extend(list_messages(address, account_id, mb, limit=limit, query=query)["messages"])
        except MailError:
            continue
    seen, dedup = set(), []
    for r in out:
        key = r.get("message_id") or r["id"]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    dedup.sort(key=lambda r: r["date"] or "", reverse=True)
    return dedup[:limit]
